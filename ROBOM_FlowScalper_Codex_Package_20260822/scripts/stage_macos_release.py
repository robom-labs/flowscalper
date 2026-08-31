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
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_COMMIT_DIRECTORY = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_MANIFEST_NAME = "release-manifest.json"
_RUNTIME_SAFETY_FIELDS = (
    "private_api_enabled",
    "api_key_enabled",
    "wallet_enabled",
    "runtime_ai_order_decision_enabled",
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
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
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
            timeout=120,
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
        "files": {
            str(path.relative_to(dist)): _sha256(path)
            for path in files
        },
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
    if missing and schema_version != 1:
        raise RuntimeError(
            "schema v2 dashboard에 안전 필드가 누락됐습니다: " + ", ".join(missing)
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
        raise RuntimeError(
            f"기존 릴리스 manifest commit이 다릅니다: {release_path}"
        )
    if require_commit_directory and release_path.name != commit:
        raise RuntimeError(
            f"릴리스 디렉터리와 manifest commit이 다릅니다: {release_path}"
        )
    manifest_release_path = (
        release_path if require_commit_directory else release_path.parent / commit
    )
    if manifest.get("release_path") != str(manifest_release_path):
        raise RuntimeError(
            "릴리스 manifest release_path가 예정된 불변 릴리스와 다릅니다: "
            f"{manifest_release_path}"
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
        raise RuntimeError(
            f"legacy 릴리스 manifest schema를 승격할 수 없습니다: {schema_version}"
        )
    commit = manifest.get("commit")
    if not isinstance(commit, str) or _COMMIT_DIRECTORY.fullmatch(commit) is None:
        raise RuntimeError(f"legacy 릴리스 commit 형식이 올바르지 않습니다: {commit}")
    if release_path.name != commit or manifest.get("release_id") != commit:
        raise RuntimeError(
            f"legacy 릴리스 디렉터리·release_id·commit이 일치하지 않습니다: {release_path}"
        )
    if manifest.get("release_path") != str(release_path):
        raise RuntimeError(
            f"legacy 릴리스 release_path가 실제 릴리스와 다릅니다: {release_path}"
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
            raise RuntimeError(
                f"legacy 릴리스 PAPER 안전 불변조건이 다릅니다: {field}"
            )
    release_tree = _release_tree_manifest(release_path)
    migrated = {
        **manifest,
        "schema_version": 2,
        "legacy_schema_version": 1,
        "legacy_manifest_sha256": hashlib.sha256(legacy_bytes).hexdigest(),
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
    """안전 확인된 현재 릴리스와 직전 롤백 릴리스만 실행 폴더에 남긴다."""

    runtime_root = runtime_root.resolve(strict=True)
    releases_root = (runtime_root / "releases").resolve(strict=True)
    active = current_release(runtime_root)
    if active is None:
        raise RuntimeError("정리할 현재 릴리스가 없습니다.")
    retained = {active}
    deployment_path = runtime_root / "current-deployment.json"
    if deployment_path.is_file():
        deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
        rollback_value = deployment.get("rollback_release")
        if rollback_value:
            rollback = Path(str(rollback_value)).resolve(strict=True)
            if not rollback.is_relative_to(releases_root):
                raise RuntimeError(f"rollback 릴리스가 releases 밖을 가리킵니다: {rollback}")
            retained.add(rollback)

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
    if _run_git(source_root, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("추적 또는 미추적 파일 변경이 남아 있어 불변 릴리스를 만들 수 없습니다.")
    commit = _run_git(source_root, "rev-parse", "HEAD")
    if len(commit) != 40:
        raise RuntimeError(f"Git commit 형식이 올바르지 않습니다: {commit}")
    releases_root = runtime_root / "releases"
    releases_root.mkdir(parents=True, exist_ok=True)
    release_path = releases_root / commit
    if release_path.exists() or release_path.is_symlink():
        return _verify_release_tree(release_path, expected_commit=commit)
    staging = releases_root / f".staging-{commit[:12]}-{uuid4().hex}"
    staging.mkdir(mode=0o700)
    previous = current_release(runtime_root)
    try:
        _extract_commit(source_root, commit, staging)
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
    market_archive = (
        arguments.market_archive or source_root / "data" / "market-parquet-v6"
    )
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
