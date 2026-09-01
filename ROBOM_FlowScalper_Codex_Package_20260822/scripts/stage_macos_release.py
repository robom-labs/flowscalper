# 커밋된 소스와 프론트엔드를 불변 macOS 실행 릴리스로 원자적으로 준비한다.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

GIT_ARCHIVE_TIMEOUT_SECONDS = 300
WORKTREE_COPY_TIMEOUT_SECONDS = 600
WORKTREE_COPY_CHUNK_BYTES = 1024 * 1024
_COMMIT_DIRECTORY = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_MANIFEST_NAME = "release-manifest.json"
_RUNTIME_SAFETY_FIELDS = (
    "private_api_enabled",
    "api_key_enabled",
    "wallet_enabled",
    "runtime_ai_order_decision_enabled",
)
_APPROVED_LEGACY_RUNTIME_COMMIT = "50c3e8ae7af08667546e8a1f2e4a70890e92d0f6"
_APPROVED_LEGACY_MANIFEST_SHA256 = (
    "21bd37ece3cd9bf72317c6fb878bef2a93d3a4c15d85ebacadded2c0c235a73e"
)


def _run_git(source_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=source_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _run_git_bytes(source_root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=source_root,
        check=True,
        capture_output=True,
        timeout=30,
    )
    return result.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _write_bytes_atomic(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _extract_commit(source_root: Path, commit: str, destination: Path) -> None:
    with tempfile.NamedTemporaryFile(
        suffix=".tar",
        delete=False,
        dir=destination.parent,
    ) as stream:
        archive_path = Path(stream.name)
    try:
        subprocess.run(
            ["git", "archive", "--format=tar", "--output", str(archive_path), commit],
            cwd=source_root,
            check=True,
            capture_output=True,
            timeout=GIT_ARCHIVE_TIMEOUT_SECONDS,
        )
        with tarfile.open(archive_path, mode="r") as archive:
            for member in archive.getmembers():
                target = (destination / member.name).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise RuntimeError(f"릴리스 archive 경로가 안전하지 않습니다: {member.name}")
                if member.issym() or member.islnk():
                    raise RuntimeError(f"릴리스 archive link는 허용하지 않습니다: {member.name}")
            archive.extractall(destination, filter="data")
    finally:
        archive_path.unlink(missing_ok=True)


def _assert_worktree_commit_binding(source_root: Path, commit: str) -> None:
    if _run_git(source_root, "rev-parse", "HEAD") != commit:
        raise RuntimeError("릴리스 준비 중 source HEAD가 변경됐습니다.")
    commit_tree = _run_git(source_root, "rev-parse", f"{commit}^{{tree}}")
    if _run_git(source_root, "write-tree") != commit_tree:
        raise RuntimeError("릴리스 source index가 SOURCE_COMMIT tree와 다릅니다.")
    if _run_git(source_root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError(
            "추적 또는 미추적 파일 변경이 남아 있어 불변 릴리스를 만들 수 없습니다."
        )


def _commit_tree_entries(
    source_root: Path,
    commit: str,
) -> tuple[tuple[Path, str, int], ...]:
    raw_entries = _run_git_bytes(
        source_root,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        commit,
    )
    entries: list[tuple[Path, str, int]] = []
    for raw_entry in raw_entries.split(b"\0"):
        if not raw_entry:
            continue
        metadata, separator, raw_path = raw_entry.partition(b"\t")
        if not separator or not raw_path:
            raise RuntimeError("SOURCE_COMMIT tree 항목 형식이 올바르지 않습니다.")
        try:
            raw_mode, object_type, object_id = metadata.decode("ascii").split()
            path_text = raw_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise RuntimeError(
                "SOURCE_COMMIT tree 항목을 안전하게 해석할 수 없습니다."
            ) from error
        if (
            object_type != "blob"
            or raw_mode not in {"100644", "100755"}
            or _COMMIT_DIRECTORY.fullmatch(object_id) is None
        ):
            raise RuntimeError(
                "SOURCE_COMMIT tree에는 regular file만 허용합니다: "
                f"{raw_mode} {object_type} {path_text}"
            )
        relative_path = Path(path_text)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path.as_posix() != path_text
        ):
            raise RuntimeError(
                f"SOURCE_COMMIT tree 경로가 안전하지 않습니다: {path_text}"
            )
        entries.append((relative_path, object_id, int(raw_mode, 8)))
    return tuple(entries)


def _copy_verified_worktree_commit(
    source_root: Path,
    commit: str,
    destination: Path,
) -> None:
    """현재 commit의 regular file만 작업트리에서 blob 검증하며 직접 복사한다."""

    repository_root = Path(
        _run_git(source_root, "rev-parse", "--show-toplevel")
    ).resolve(strict=True)
    _assert_worktree_commit_binding(repository_root, commit)
    destination_root = destination.resolve(strict=True)
    deadline = time.monotonic() + WORKTREE_COPY_TIMEOUT_SECONDS
    for relative_path, expected_object_id, git_mode in _commit_tree_entries(
        repository_root,
        commit,
    ):
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"검증된 작업트리 복사가 {WORKTREE_COPY_TIMEOUT_SECONDS}초를 초과했습니다."
            )
        source_path = repository_root / relative_path
        if source_path.is_symlink() or not source_path.is_file():
            raise RuntimeError(f"릴리스 source가 regular file이 아닙니다: {relative_path}")
        resolved_source = source_path.resolve(strict=True)
        if not resolved_source.is_relative_to(repository_root):
            raise RuntimeError(
                f"릴리스 source 경로가 repository 밖입니다: {relative_path}"
            )
        source_size = source_path.stat(follow_symlinks=False).st_size
        digest = hashlib.sha1(usedforsecurity=False)
        digest.update(f"blob {source_size}\0".encode("ascii"))
        copied_size = 0
        target_path = destination_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_target = target_path.resolve(strict=False)
        if not resolved_target.is_relative_to(destination_root):
            raise RuntimeError(f"릴리스 대상 경로가 staging 밖입니다: {relative_path}")
        with source_path.open("rb") as source, target_path.open("xb") as target:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"검증된 작업트리 복사가 {WORKTREE_COPY_TIMEOUT_SECONDS}초를 초과했습니다."
                    )
                chunk = source.read(WORKTREE_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                target.write(chunk)
                copied_size += len(chunk)
        if copied_size != source_size or digest.hexdigest() != expected_object_id:
            raise RuntimeError(
                "릴리스 source 파일이 SOURCE_COMMIT blob과 다릅니다: "
                f"{relative_path}"
            )
        target_path.chmod(0o755 if git_mode == 0o100755 else 0o644)
    _assert_worktree_commit_binding(repository_root, commit)


def _build_frontend(snapshot_root: Path, source_root: Path, commit: str) -> None:
    source_modules = source_root / "frontend" / "node_modules"
    snapshot_modules = snapshot_root / "frontend" / "node_modules"
    if not source_modules.is_dir():
        raise RuntimeError("frontend/node_modules가 없습니다. 먼저 make setup을 실행하세요.")
    snapshot_modules.symlink_to(source_modules, target_is_directory=True)
    environment = {
        **os.environ,
        "VITE_ROBOM_RELEASE_COMMIT": commit,
        "ROBOM_RELEASE_COMMIT": commit,
    }
    try:
        subprocess.run(
            ["pnpm", "build"],
            cwd=snapshot_root / "frontend",
            env=environment,
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
            timeout=600,
        )
    finally:
        snapshot_modules.unlink(missing_ok=True)
        for build_cache in (snapshot_root / "frontend").rglob("*.tsbuildinfo"):
            build_cache.unlink()


def _copy_prebuilt_frontend(snapshot_root: Path, frontend_dist: Path) -> None:
    target = snapshot_root / "frontend" / "dist"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(frontend_dist, target)


def _frontend_manifest(dist: Path) -> dict[str, Any]:
    index = dist / "index.html"
    if not index.is_file():
        raise RuntimeError(f"프론트엔드 index.html이 없습니다: {index}")
    files = tuple(sorted(path for path in dist.rglob("*") if path.is_file()))
    return {
        "file_count": len(files),
        "index_sha256": _sha256(index),
        "files": {str(path.relative_to(dist)): _sha256(path) for path in files},
    }


def _release_tree_manifest(release_path: Path) -> dict[str, Any]:
    """manifest 자체를 제외한 regular file 전체를 content-addressed 목록으로 만든다."""

    if release_path.is_symlink():
        raise RuntimeError(f"릴리스 root symlink는 허용하지 않습니다: {release_path}")
    release_path = release_path.resolve(strict=True)
    if not release_path.is_dir():
        raise RuntimeError(f"릴리스 root가 디렉터리가 아닙니다: {release_path}")
    files: dict[str, str] = {}
    for path in sorted(release_path.rglob("*")):
        relative = path.relative_to(release_path).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"릴리스 tree link는 허용하지 않습니다: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise RuntimeError(f"릴리스 tree regular file이 아닙니다: {relative}")
        if relative == _RELEASE_MANIFEST_NAME:
            continue
        files[relative] = _sha256(path)
    return {"file_count": len(files), "files": files}


def _read_release_manifest(release_path: Path) -> dict[str, Any]:
    manifest_path = release_path / _RELEASE_MANIFEST_NAME
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"릴리스 manifest를 읽을 수 없습니다: {manifest_path}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"릴리스 manifest가 JSON object가 아닙니다: {manifest_path}")
    return payload


def legacy_runtime_safety_fields_missing(
    manifest: Mapping[str, object],
    system: Mapping[str, object],
) -> tuple[str, ...]:
    """v1 dashboard의 미보고 필드만 manifest 안전계약 아래 제한적으로 식별한다."""

    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        raise RuntimeError(f"릴리스 manifest schema가 올바르지 않습니다: {schema_version}")
    safety_contract = {
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "wallet_paths_enabled": False,
    }
    for field, expected in safety_contract.items():
        if manifest.get(field) is not expected:
            raise RuntimeError(f"릴리스 PAPER 안전 불변조건이 다릅니다: {field}")
    if system.get("auth_headers") is not False:
        raise RuntimeError("dashboard auth_headers가 명시적 False가 아닙니다.")
    missing: list[str] = []
    for field in _RUNTIME_SAFETY_FIELDS:
        if field not in system:
            missing.append(field)
        elif system[field] is not False:
            raise RuntimeError(f"dashboard 안전 필드가 명시적 False가 아닙니다: {field}")
    approved_v1 = schema_version == 1 and manifest.get("commit") == _APPROVED_LEGACY_RUNTIME_COMMIT
    verified_migrated_v1 = (
        schema_version == 2
        and manifest.get("commit") == _APPROVED_LEGACY_RUNTIME_COMMIT
        and manifest.get("legacy_schema_version") == 1
        and manifest.get("legacy_source_commit_verified") is True
        and manifest.get("legacy_frontend_manifest_verified") is True
        and manifest.get("legacy_manifest_sha256") == _APPROVED_LEGACY_MANIFEST_SHA256
    )
    if missing and not approved_v1 and not verified_migrated_v1:
        raise RuntimeError(
            "승인되지 않은 릴리스 dashboard에 안전 필드가 누락됐습니다: " + ", ".join(missing)
        )
    return tuple(missing)


def _verify_release_tree(
    release_path: Path,
    *,
    expected_commit: str | None = None,
    require_commit_directory: bool = True,
) -> dict[str, Any]:
    """manifest와 실제 tree의 누락·추가·변조를 모두 fail-closed 검증한다."""

    if release_path.is_symlink():
        raise RuntimeError(f"릴리스 root symlink는 허용하지 않습니다: {release_path}")
    release_path = release_path.resolve(strict=True)
    manifest = _read_release_manifest(release_path)
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version != 2:
        raise RuntimeError(f"릴리스 manifest schema가 v2가 아닙니다: {schema_version}")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or _COMMIT_DIRECTORY.fullmatch(commit) is None:
        raise RuntimeError(f"릴리스 manifest commit 형식이 올바르지 않습니다: {commit}")
    if manifest.get("release_id") != commit:
        raise RuntimeError("릴리스 manifest release_id와 commit이 다릅니다.")
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeError(f"기존 릴리스 manifest commit이 다릅니다: {release_path}")
    if require_commit_directory and release_path.name != commit:
        raise RuntimeError(f"릴리스 디렉터리와 manifest commit이 다릅니다: {release_path}")
    manifest_release_path = (
        release_path if require_commit_directory else release_path.parent / commit
    )
    if manifest.get("release_path") != str(manifest_release_path):
        raise RuntimeError(
            f"릴리스 manifest release_path가 예정된 불변 릴리스와 다릅니다: {manifest_release_path}"
        )
    safety_contract = {
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "wallet_paths_enabled": False,
    }
    for field, expected in safety_contract.items():
        if manifest.get(field) is not expected:
            raise RuntimeError(f"릴리스 PAPER 안전 불변조건이 다릅니다: {field}")

    expected_count = manifest.get("file_count")
    expected_files = manifest.get("files")
    if type(expected_count) is not int or expected_count < 0:
        raise RuntimeError("릴리스 manifest file_count가 올바르지 않습니다.")
    if not isinstance(expected_files, dict):
        raise RuntimeError("릴리스 manifest files 목록이 없습니다.")
    normalized_expected: dict[str, str] = {}
    for raw_path, raw_digest in expected_files.items():
        if not isinstance(raw_path, str) or not raw_path or raw_path == _RELEASE_MANIFEST_NAME:
            raise RuntimeError("릴리스 manifest 파일 경로가 올바르지 않습니다.")
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != raw_path:
            raise RuntimeError(f"릴리스 manifest 파일 경로가 안전하지 않습니다: {raw_path}")
        if not isinstance(raw_digest, str) or _SHA256.fullmatch(raw_digest) is None:
            raise RuntimeError(f"릴리스 manifest SHA-256이 올바르지 않습니다: {raw_path}")
        normalized_expected[raw_path] = raw_digest
    if expected_count != len(normalized_expected):
        raise RuntimeError(
            "릴리스 manifest file_count와 files 항목 수가 다릅니다: "
            f"{expected_count} != {len(normalized_expected)}"
        )

    actual = _release_tree_manifest(release_path)
    actual_files = actual["files"]
    if not isinstance(actual_files, dict):
        raise RuntimeError("릴리스 tree 검증 결과가 올바르지 않습니다.")
    expected_paths = set(normalized_expected)
    actual_paths = set(actual_files)
    missing = sorted(expected_paths - actual_paths)
    added = sorted(actual_paths - expected_paths)
    modified = sorted(
        path
        for path in expected_paths & actual_paths
        if normalized_expected[path] != actual_files[path]
    )
    if actual["file_count"] != expected_count or missing or added or modified:
        details = {
            "expected_file_count": expected_count,
            "actual_file_count": actual["file_count"],
            "missing": missing,
            "added": added,
            "modified": modified,
        }
        raise RuntimeError(
            "릴리스 tree 무결성 검증에 실패했습니다: "
            + json.dumps(details, ensure_ascii=False, sort_keys=True)
        )
    return manifest


def _verified_legacy_release_tree(
    *,
    runtime_root: Path,
    release_path: Path,
    manifest: Mapping[str, object],
    commit: str,
) -> dict[str, object]:
    """Git commit 원본과 v1 frontend hash로 legacy tree를 재서명 전 검증한다."""

    source_repository = manifest.get("source_repository_path")
    if not isinstance(source_repository, str) or not source_repository:
        raise RuntimeError("legacy 릴리스 source repository 경로가 없습니다.")
    try:
        source_root = Path(source_repository).resolve(strict=True)
    except OSError as error:
        raise RuntimeError("legacy 릴리스 source repository를 확인할 수 없습니다.") from error
    if not source_root.is_dir():
        raise RuntimeError("legacy 릴리스 source repository가 디렉터리가 아닙니다.")
    try:
        verified_commit = _run_git(
            source_root,
            "rev-parse",
            "--verify",
            f"{commit}^{{commit}}",
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            f"legacy 릴리스 commit을 source repository에서 확인할 수 없습니다: {commit}"
        ) from error
    if verified_commit != commit:
        raise RuntimeError("legacy 릴리스 commit 검증 결과가 manifest와 다릅니다.")

    release_tree = _release_tree_manifest(release_path)
    actual_files = release_tree["files"]
    if not isinstance(actual_files, dict):
        raise RuntimeError("legacy 릴리스 tree 검증 결과가 올바르지 않습니다.")
    with tempfile.TemporaryDirectory(
        prefix=f".legacy-verify-{commit[:12]}-",
        dir=runtime_root / "releases",
    ) as temporary_directory:
        archive_root = Path(temporary_directory) / "archive"
        archive_root.mkdir()
        _extract_commit(source_root, commit, archive_root)
        archive_tree = _release_tree_manifest(archive_root)
    archive_files = archive_tree["files"]
    if not isinstance(archive_files, dict):
        raise RuntimeError("legacy Git archive 검증 결과가 올바르지 않습니다.")

    def source_files(files: Mapping[str, object]) -> dict[str, object]:
        return {
            path: digest
            for path, digest in files.items()
            if path != _RELEASE_MANIFEST_NAME and not path.startswith("frontend/dist/")
        }

    expected_source_files = source_files(archive_files)
    actual_source_files = source_files(actual_files)
    if actual_source_files != expected_source_files:
        expected_paths = set(expected_source_files)
        actual_paths = set(actual_source_files)
        details = {
            "missing": sorted(expected_paths - actual_paths),
            "added": sorted(actual_paths - expected_paths),
            "modified": sorted(
                path
                for path in expected_paths & actual_paths
                if expected_source_files[path] != actual_source_files[path]
            ),
        }
        raise RuntimeError(
            "legacy 릴리스 source tree가 Git commit과 다릅니다: "
            + json.dumps(details, ensure_ascii=False, sort_keys=True)
        )

    legacy_frontend = manifest.get("frontend")
    if not isinstance(legacy_frontend, dict):
        raise RuntimeError("legacy 릴리스 frontend manifest가 없습니다.")
    expected_frontend_files = legacy_frontend.get("files")
    expected_frontend_count = legacy_frontend.get("file_count")
    expected_index_sha = legacy_frontend.get("index_sha256")
    if (
        not isinstance(expected_frontend_files, dict)
        or type(expected_frontend_count) is not int
        or expected_frontend_count < 1
        or not isinstance(expected_index_sha, str)
        or _SHA256.fullmatch(expected_index_sha) is None
    ):
        raise RuntimeError("legacy 릴리스 frontend manifest 형식이 올바르지 않습니다.")
    normalized_frontend_files: dict[str, str] = {}
    for raw_path, raw_digest in expected_frontend_files.items():
        if not isinstance(raw_path, str) or not raw_path:
            raise RuntimeError("legacy frontend 파일 경로가 올바르지 않습니다.")
        relative_path = Path(raw_path)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path.as_posix() != raw_path
        ):
            raise RuntimeError(f"legacy frontend 파일 경로가 안전하지 않습니다: {raw_path}")
        if not isinstance(raw_digest, str) or _SHA256.fullmatch(raw_digest) is None:
            raise RuntimeError(f"legacy frontend SHA-256이 올바르지 않습니다: {raw_path}")
        normalized_frontend_files[raw_path] = raw_digest
    if (
        expected_frontend_count != len(normalized_frontend_files)
        or normalized_frontend_files.get("index.html") != expected_index_sha
    ):
        raise RuntimeError("legacy frontend manifest 건수 또는 index hash가 다릅니다.")
    actual_frontend = _frontend_manifest(release_path / "frontend" / "dist")
    if actual_frontend != {
        "file_count": expected_frontend_count,
        "index_sha256": expected_index_sha,
        "files": normalized_frontend_files,
    }:
        raise RuntimeError("legacy 릴리스 frontend 바이트가 v1 manifest와 다릅니다.")
    return release_tree


def migrate_legacy_release_manifest(
    runtime_root: Path,
    release_path: Path,
) -> dict[str, Any]:
    """검증 가능한 legacy v1 manifest만 전체-tree v2 metadata로 원자 승격한다."""

    runtime_root = runtime_root.resolve(strict=True)
    releases_path = runtime_root / "releases"
    if releases_path.is_symlink():
        raise RuntimeError(f"runtime releases symlink는 허용하지 않습니다: {releases_path}")
    releases_root = releases_path.resolve(strict=True)
    if release_path.is_symlink():
        raise RuntimeError(f"legacy 릴리스 root symlink는 허용하지 않습니다: {release_path}")
    release_path = release_path.resolve(strict=True)
    if release_path.parent != releases_root:
        raise RuntimeError(
            f"legacy 릴리스는 runtime releases 바로 아래에 있어야 합니다: {release_path}"
        )
    manifest_path = release_path / _RELEASE_MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError(f"legacy 릴리스 manifest가 regular file이 아닙니다: {manifest_path}")
    legacy_bytes = manifest_path.read_bytes()
    manifest = _read_release_manifest(release_path)
    schema_version = manifest.get("schema_version")
    if schema_version == 2:
        return _verify_release_tree(release_path)
    if type(schema_version) is not int or schema_version != 1:
        raise RuntimeError(f"legacy 릴리스 manifest schema를 승격할 수 없습니다: {schema_version}")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or _COMMIT_DIRECTORY.fullmatch(commit) is None:
        raise RuntimeError(f"legacy 릴리스 commit 형식이 올바르지 않습니다: {commit}")
    if release_path.name != commit or manifest.get("release_id") != commit:
        raise RuntimeError(
            f"legacy 릴리스 디렉터리·release_id·commit이 일치하지 않습니다: {release_path}"
        )
    legacy_manifest_sha256 = hashlib.sha256(legacy_bytes).hexdigest()
    if (
        commit != _APPROVED_LEGACY_RUNTIME_COMMIT
        or legacy_manifest_sha256 != _APPROVED_LEGACY_MANIFEST_SHA256
    ):
        raise RuntimeError(
            "legacy 릴리스 commit 또는 원본 manifest checksum이 승인 allowlist와 다릅니다."
        )
    if manifest.get("release_path") != str(release_path):
        raise RuntimeError(f"legacy 릴리스 release_path가 실제 릴리스와 다릅니다: {release_path}")
    safety_contract = {
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "wallet_paths_enabled": False,
    }
    for field, expected in safety_contract.items():
        if manifest.get(field) is not expected:
            raise RuntimeError(f"legacy 릴리스 PAPER 안전 불변조건이 다릅니다: {field}")
    release_tree = _verified_legacy_release_tree(
        runtime_root=runtime_root,
        release_path=release_path,
        manifest=manifest,
        commit=commit,
    )
    migrated = {
        **manifest,
        "schema_version": 2,
        "legacy_schema_version": 1,
        "legacy_manifest_sha256": legacy_manifest_sha256,
        "legacy_source_commit_verified": True,
        "legacy_frontend_manifest_verified": True,
        "manifest_migrated_at": datetime.now(UTC).isoformat(),
        "file_count": release_tree["file_count"],
        "files": release_tree["files"],
    }
    try:
        _write_json_atomic(manifest_path, migrated)
        return _verify_release_tree(release_path)
    except BaseException:
        _write_bytes_atomic(manifest_path, legacy_bytes)
        raise


def current_release(runtime_root: Path) -> Path | None:
    pointer = runtime_root / "current"
    if not pointer.exists() and not pointer.is_symlink():
        return None
    if not pointer.is_symlink():
        raise RuntimeError(f"current 포인터가 symlink가 아닙니다: {pointer}")
    resolved = pointer.resolve(strict=True)
    releases_root = (runtime_root / "releases").resolve()
    if resolved.parent != releases_root:
        raise RuntimeError(
            f"current 포인터가 releases 바로 아래 릴리스를 가리키지 않습니다: {resolved}"
        )
    return resolved


def activate_release(
    runtime_root: Path,
    release_path: Path,
    *,
    actor: str = "CODEX_DEPLOY",
    reason: str = "IMMUTABLE_RELEASE_ACTIVATION",
) -> dict[str, Any]:
    runtime_root = runtime_root.resolve(strict=True)
    if release_path.is_symlink():
        raise RuntimeError(f"활성화할 릴리스가 symlink입니다: {release_path}")
    release_path = release_path.resolve(strict=True)
    releases_root = (runtime_root / "releases").resolve(strict=True)
    if release_path.parent != releases_root:
        raise RuntimeError(f"활성화할 릴리스가 releases 바로 아래가 아닙니다: {release_path}")
    manifest = _verify_release_tree(release_path)
    commit = str(manifest["commit"])
    previous = current_release(runtime_root)
    pointer = runtime_root / "current"
    temporary_pointer = runtime_root / f".current.{uuid4().hex}.tmp"
    relative_target = os.path.relpath(release_path, runtime_root)
    temporary_pointer.symlink_to(relative_target, target_is_directory=True)
    os.replace(temporary_pointer, pointer)
    occurred_at = datetime.now(UTC).isoformat()
    deployment = {
        "schema_version": 1,
        "transition_id": f"deploy-{uuid4().hex}",
        "previous_state": str(previous) if previous is not None else "NONE",
        "new_state": str(release_path),
        "occurred_at": occurred_at,
        "cause": reason,
        "cause_code": reason,
        "description_ko": "검증된 불변 PAPER 릴리스를 원자적으로 활성화했습니다.",
        "actor": actor,
        "release_commit": commit,
        "rollback_release": str(previous) if previous is not None else None,
        "reversible": previous is not None,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
    }
    deployment_name = occurred_at.replace(":", "-") + f"-{commit[:12]}.json"
    _write_json_atomic(runtime_root / "deployments" / deployment_name, deployment)
    _write_json_atomic(runtime_root / "current-deployment.json", deployment)
    return deployment


def prune_obsolete_releases(runtime_root: Path) -> dict[str, Any]:
    """현재·롤백·launcher source 릴리스를 검증하고 나머지만 정리한다."""

    runtime_root_input = runtime_root
    if runtime_root_input.is_symlink():
        raise RuntimeError(
            f"runtime root symlink는 정리 대상으로 허용하지 않습니다: {runtime_root}"
        )
    runtime_root = runtime_root_input.resolve(strict=True)
    if runtime_root != runtime_root_input.absolute():
        raise RuntimeError("runtime root의 canonical 경로가 다릅니다.")
    releases_path = runtime_root / "releases"
    if releases_path.is_symlink():
        raise RuntimeError(
            f"runtime releases symlink는 정리 대상으로 허용하지 않습니다: {releases_path}"
        )
    releases_root = releases_path.resolve(strict=True)
    if releases_root.parent != runtime_root:
        raise RuntimeError("runtime releases가 runtime root direct child가 아닙니다.")

    def read_strict_object(path: Path, label: str) -> dict[str, object]:
        def reject_constant(value: str) -> object:
            raise RuntimeError(f"{label}에 비표준 숫자가 있습니다: {value}")

        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise RuntimeError(f"{label}에 중복 JSON key가 있습니다: {key}")
                result[key] = value
            return result

        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
        if not isinstance(value, dict):
            raise RuntimeError(f"{label}가 JSON object가 아닙니다.")
        return value

    def verified_retained_release(value: object, label: str) -> Path:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"{label} 릴리스 경로가 없습니다.")
        candidate = Path(value)
        if candidate.is_symlink():
            raise RuntimeError(f"{label} 릴리스 root symlink는 허용하지 않습니다: {candidate}")
        resolved = candidate.resolve(strict=True)
        if (
            resolved != candidate.absolute()
            or resolved.parent != releases_root
            or _COMMIT_DIRECTORY.fullmatch(resolved.name) is None
        ):
            raise RuntimeError(
                f"{label} 릴리스가 releases direct commit child가 아닙니다: {resolved}"
            )
        _verify_release_tree(resolved, expected_commit=resolved.name)
        return resolved

    active = current_release(runtime_root)
    if active is None:
        raise RuntimeError("정리할 현재 릴리스가 없습니다.")
    active = verified_retained_release(str(active), "active")
    retained = {active}
    deployment_path = runtime_root / "current-deployment.json"
    if deployment_path.is_file():
        deployment = read_strict_object(deployment_path, "current deployment")
        rollback_value = deployment.get("rollback_release")
        if rollback_value:
            rollback = verified_retained_release(rollback_value, "rollback")
            retained.add(rollback)

    anchor_path = runtime_root / "support" / "current-release-integrity.json"
    if anchor_path.exists() or anchor_path.is_symlink():
        support_path = runtime_root / "support"
        if (
            support_path.is_symlink()
            or support_path.resolve(strict=True) != support_path.absolute()
            or anchor_path.is_symlink()
            or not anchor_path.is_file()
        ):
            raise RuntimeError("release integrity anchor가 regular file이 아닙니다.")
        anchor = read_strict_object(anchor_path, "release integrity anchor")
        expected_anchor_fields = {
            "schema_version",
            "release_path",
            "release_commit",
            "manifest_sha256",
            "launcher_path",
            "launcher_sha256",
            "launcher_source_release_path",
            "launcher_source_commit",
            "launcher_source_manifest_sha256",
            "paper_only",
            "real_orders_enabled",
        }
        if (
            not isinstance(anchor, dict)
            or set(anchor) != expected_anchor_fields
            or type(anchor.get("schema_version")) is not int
            or anchor.get("schema_version") != 2
            or anchor.get("release_path") != str(active)
            or anchor.get("release_commit") != active.name
            or anchor.get("paper_only") is not True
            or anchor.get("real_orders_enabled") is not False
        ):
            raise RuntimeError("release integrity anchor가 active release와 결합되지 않았습니다.")
        active_manifest = active / _RELEASE_MANIFEST_NAME
        if anchor.get("manifest_sha256") != _sha256(active_manifest):
            raise RuntimeError("release integrity anchor의 active manifest checksum이 다릅니다.")
        source = verified_retained_release(
            anchor.get("launcher_source_release_path"),
            "launcher source",
        )
        source_manifest = source / _RELEASE_MANIFEST_NAME
        source_runner = source / "scripts" / "run_macos_service.sh"
        launcher = runtime_root / "support" / "run_macos_service.sh"
        if (
            anchor.get("launcher_source_commit") != source.name
            or anchor.get("launcher_source_manifest_sha256") != _sha256(source_manifest)
            or launcher.is_symlink()
            or not launcher.is_file()
            or source_runner.is_symlink()
            or not source_runner.is_file()
            or anchor.get("launcher_path") != str(launcher.resolve(strict=True))
            or anchor.get("launcher_sha256") != _sha256(launcher)
            or _sha256(launcher) != _sha256(source_runner)
        ):
            raise RuntimeError("release integrity anchor의 launcher source 결합이 다릅니다.")
        retained.add(source)

    pruned: list[str] = []
    skipped: list[str] = []
    for candidate in sorted(releases_root.iterdir()):
        if candidate.is_symlink() or not candidate.is_dir():
            skipped.append(str(candidate))
            continue
        resolved = candidate.resolve(strict=True)
        if resolved in retained:
            continue
        if _COMMIT_DIRECTORY.fullmatch(candidate.name) is None:
            skipped.append(str(candidate))
            continue
        manifest_path = candidate / "release-manifest.json"
        if not manifest_path.is_file():
            skipped.append(str(candidate))
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(manifest.get("commit")) != candidate.name:
            skipped.append(str(candidate))
            continue
        shutil.rmtree(candidate)
        pruned.append(str(candidate))
    return {
        "schema": "flowscalper.release_retention.v1",
        "status": "PASS",
        "active_release": str(active),
        "retained_releases": sorted(str(path) for path in retained),
        "pruned_releases": pruned,
        "skipped_paths": skipped,
    }


def stage_release(
    source_root: Path,
    runtime_root: Path,
    market_archive_path: Path,
    active_ledger_dir: Path,
    *,
    build_frontend: bool = True,
    prebuilt_frontend_dist: Path | None = None,
) -> dict[str, Any]:
    source_root = source_root.resolve(strict=True)
    runtime_root = runtime_root.resolve()
    market_archive_path = market_archive_path.resolve(strict=True)
    active_ledger_dir = active_ledger_dir.resolve()
    commit = _run_git(source_root, "rev-parse", "HEAD")
    if len(commit) != 40:
        raise RuntimeError(f"Git commit 형식이 올바르지 않습니다: {commit}")
    _assert_worktree_commit_binding(source_root, commit)
    releases_root = runtime_root / "releases"
    releases_root.mkdir(parents=True, exist_ok=True)
    release_path = releases_root / commit
    if release_path.exists() or release_path.is_symlink():
        return _verify_release_tree(release_path, expected_commit=commit)
    staging = releases_root / f".staging-{commit[:12]}-{uuid4().hex}"
    staging.mkdir(mode=0o700)
    previous = current_release(runtime_root)
    try:
        _copy_verified_worktree_commit(source_root, commit, staging)
        if build_frontend:
            _build_frontend(staging, source_root, commit)
        else:
            if prebuilt_frontend_dist is None:
                raise RuntimeError("prebuilt_frontend_dist가 필요합니다.")
            _copy_prebuilt_frontend(staging, prebuilt_frontend_dist.resolve(strict=True))
        frontend = _frontend_manifest(staging / "frontend" / "dist")
        release_tree = _release_tree_manifest(staging)
        created_at = datetime.now(UTC).isoformat()
        manifest = {
            "schema_version": 2,
            "release_id": commit,
            "commit": commit,
            "created_at": created_at,
            "source_repository_path": str(source_root),
            "release_path": str(release_path),
            "market_archive_path": str(market_archive_path),
            "active_ledger_dir": str(active_ledger_dir),
            "previous_release": str(previous) if previous is not None else None,
            "rollback_release": str(previous) if previous is not None else None,
            "frontend": frontend,
            "file_count": release_tree["file_count"],
            "files": release_tree["files"],
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_enabled": False,
            "wallet_paths_enabled": False,
        }
        _write_json_atomic(staging / _RELEASE_MANIFEST_NAME, manifest)
        _verify_release_tree(
            staging,
            expected_commit=commit,
            require_commit_directory=False,
        )
        os.replace(staging, release_path)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _default_runtime_root(source_root: Path) -> Path:
    if len(source_root.parts) >= 3 and source_root.parts[1] == "Volumes":
        return Path("/Volumes") / source_root.parts[2] / "05_RUNTIME" / "ROBOM_FlowScalper"
    raise RuntimeError("macOS 불변 실행 릴리스는 외장 볼륨 소스가 필요합니다.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="커밋된 FlowScalper를 불변 macOS PAPER 실행 릴리스로 준비합니다."
    )
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--market-archive", type=Path)
    parser.add_argument("--active-ledger-dir", type=Path)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--prune-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.prune_only:
        if arguments.activate:
            parser.error("--prune-only와 --activate는 함께 사용할 수 없습니다.")
        runtime_root = (
            arguments.runtime_root
            or _default_runtime_root(arguments.source_root.resolve(strict=True))
        ).resolve(strict=True)
        print(
            json.dumps(
                prune_obsolete_releases(runtime_root),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    source_root = arguments.source_root.resolve(strict=True)
    runtime_root = (arguments.runtime_root or _default_runtime_root(source_root)).resolve()
    market_archive = arguments.market_archive or source_root / "data" / "market-parquet-v6"
    active_ledger_dir = arguments.active_ledger_dir or runtime_root / "active-ledger"
    manifest = stage_release(
        source_root,
        runtime_root,
        market_archive,
        active_ledger_dir,
    )
    result: dict[str, Any] = {"status": "STAGED", "release": manifest}
    if arguments.activate:
        deployment = activate_release(runtime_root, Path(str(manifest["release_path"])))
        result = {"status": "ACTIVATED", "release": manifest, "deployment": deployment}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
