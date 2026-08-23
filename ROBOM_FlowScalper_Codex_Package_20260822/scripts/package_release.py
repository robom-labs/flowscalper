"""캐시·비밀·원시 시장데이터를 제외한 재현 가능 릴리스 ZIP을 만든다."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
ARCHIVE_ROOT = f"ROBOM_FlowScalper_{VERSION}"
ROOT_FILES = (
    ".gitignore",
    "00_AI_HANDOFF_먼저읽기.md",
    "00_사용법_먼저읽기.md",
    "01_GPT_업그레이드_방향_요청프롬프트_KO.txt",
    "AGENTS.md",
    "CHECKLIST_FOR_USER.md",
    "CHANGELOG.md",
    "FINAL_UPGRADE_EVIDENCE.md",
    "IMPLEMENT.md",
    "Makefile",
    "MIGRATION_NOTES_v0.2.md",
    "OFFICIAL_REFERENCES.md",
    "PLANS.md",
    "PROJECT_MANIFEST.md",
    "README.md",
    "RELEASE_NOTES_v0.2_WAVE10.md",
    "ROBOM_FlowScalper.command",
    "RUNBOOK_LIVE_SHADOW_PAPER.md",
    "SOAK_TEST_REPORT.md",
    "STRATEGY_CATALOG_KO.md",
    "THIRD_PARTY_NOTICES.md",
    "UI_USER_GUIDE_KO.md",
    "UPGRADE_EXEC_PLAN.md",
    "VERSION",
    "frontend/eslint.config.js",
    "frontend/index.html",
    "frontend/package.json",
    "frontend/playwright.config.ts",
    "frontend/pnpm-lock.yaml",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "pyproject.toml",
    "uv.lock",
)
DIRECTORIES = (
    "ROBOM_FlowScalper.app",
    "THIRD_PARTY_LICENSES",
    "artifacts/screenshots",
    "backend/app",
    "backend/tests",
    "config",
    "docs",
    "evidence",
    "frontend/dist",
    "frontend/e2e",
    "frontend/src",
    "frontend/tests",
    "schemas",
    "scripts",
    "templates",
    "ui_reference",
)
EXCLUDED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "playwright-report",
    "test-results",
}
EXCLUDED_NAMES = {".DS_Store"}


def main() -> None:
    output_dir = PROJECT_ROOT / "artifacts" / "release"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"{ARCHIVE_ROOT}.zip"
    checksum_path = archive_path.with_suffix(".zip.sha256")
    files = _release_files()
    commit = _git_commit()
    commit_bytes = f"{commit}\n".encode()
    checksums = {str(path.relative_to(PROJECT_ROOT)): _sha256(path) for path in files}
    checksums["BUILD_COMMIT"] = hashlib.sha256(commit_bytes).hexdigest()
    manifest = (
        "\n".join(f"{digest}  {relative}" for relative, digest in sorted(checksums.items())) + "\n"
    )
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in files:
            relative = path.relative_to(PROJECT_ROOT)
            executable = (
                path.suffix == ".sh"
                or path.name.endswith(".command")
                or "Contents/MacOS" in path.as_posix()
            )
            _write_file(
                archive,
                f"{ARCHIVE_ROOT}/{relative}",
                path.read_bytes(),
                mode=0o755 if executable else 0o644,
            )
        _write_file(archive, f"{ARCHIVE_ROOT}/BUILD_COMMIT", commit_bytes)
        _write_file(archive, f"{ARCHIVE_ROOT}/SHA256SUMS.txt", manifest.encode())
    archive_checksum = _sha256(archive_path)
    checksum_path.write_text(f"{archive_checksum}  {archive_path.name}\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "PASS",
                "archive": str(archive_path),
                "checksum_file": str(checksum_path),
                "sha256": archive_checksum,
                "file_count": len(files) + 2,
                "build_commit": commit,
                "bytes": archive_path.stat().st_size,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _release_files() -> tuple[Path, ...]:
    paths = [PROJECT_ROOT / name for name in ROOT_FILES]
    for directory in DIRECTORIES:
        root = PROJECT_ROOT / directory
        paths.extend(path for path in root.rglob("*") if path.is_file())
    filtered = {
        path
        for path in paths
        if path.exists()
        and not any(part in EXCLUDED_PARTS for part in path.parts)
        and path.name not in EXCLUDED_NAMES
        and path.suffix not in {".pyc", ".sqlite3", ".tsbuildinfo"}
        and not path.name.endswith((".sqlite3-wal", ".sqlite3-shm"))
    }
    return tuple(sorted(filtered))


def _write_file(archive: zipfile.ZipFile, name: str, content: bytes, *, mode: int = 0o644) -> None:
    info = zipfile.ZipInfo(name, date_time=(2026, 8, 22, 0, 0, 0))
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | mode) << 16
    archive.writestr(info, content)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


if __name__ == "__main__":
    main()
