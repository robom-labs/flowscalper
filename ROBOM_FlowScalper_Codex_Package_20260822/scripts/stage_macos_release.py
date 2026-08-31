# 커밋된 소스와 프론트엔드를 불변 macOS 실행 릴리스로 원자적으로 준비한다.
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


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


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


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


def current_release(runtime_root: Path) -> Path | None:
    pointer = runtime_root / "current"
    if not pointer.exists() and not pointer.is_symlink():
        return None
    if not pointer.is_symlink():
        raise RuntimeError(f"current 포인터가 symlink가 아닙니다: {pointer}")
    resolved = pointer.resolve(strict=True)
    releases_root = (runtime_root / "releases").resolve()
    if not resolved.is_relative_to(releases_root):
        raise RuntimeError(f"current 포인터가 releases 밖을 가리킵니다: {resolved}")
    return resolved


def activate_release(
    runtime_root: Path,
    release_path: Path,
    *,
    actor: str = "CODEX_DEPLOY",
    reason: str = "IMMUTABLE_RELEASE_ACTIVATION",
) -> dict[str, Any]:
    release_path = release_path.resolve(strict=True)
    manifest_path = release_path / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
    if _run_git(source_root, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("추적 파일 변경이 남아 있어 불변 릴리스를 만들 수 없습니다.")
    commit = _run_git(source_root, "rev-parse", "HEAD")
    if len(commit) != 40:
        raise RuntimeError(f"Git commit 형식이 올바르지 않습니다: {commit}")
    releases_root = runtime_root / "releases"
    releases_root.mkdir(parents=True, exist_ok=True)
    release_path = releases_root / commit
    if release_path.exists():
        manifest = json.loads(
            (release_path / "release-manifest.json").read_text(encoding="utf-8")
        )
        if str(manifest.get("commit")) != commit:
            raise RuntimeError(f"기존 릴리스 manifest commit이 다릅니다: {release_path}")
        return manifest
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
        created_at = datetime.now(UTC).isoformat()
        manifest = {
            "schema_version": 1,
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
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_enabled": False,
            "wallet_paths_enabled": False,
        }
        _write_json_atomic(staging / "release-manifest.json", manifest)
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
    arguments = parser.parse_args()
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
