"""현재 소스에 구버전 복사본과 실행·생성 파일이 섞이지 않았는지 검사한다."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+-paper$")
LEGACY_PART_PATTERN = re.compile(
    r"(^|[-_. ])(old|legacy|backup|copy|bak|복사본|구버전|이전버전)([-_. ]|$)",
    re.IGNORECASE,
)
FORBIDDEN_PARTS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "playwright-report",
    "test-results",
}
FORBIDDEN_SUFFIXES = {
    ".bak",
    ".log",
    ".old",
    ".orig",
    ".parquet",
    ".sqlite",
    ".sqlite3",
    ".tsbuildinfo",
    ".zip",
}
FORBIDDEN_ROOTS = {"artifacts", "data"}
FORBIDDEN_LEGACY_FILES = {
    "01_CODEX_원샷_실행프롬프트_KO.txt",
    "FINAL_EVIDENCE.md",
}


def tracked_project_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "-z", "--", "."],
        check=True,
        capture_output=True,
    )
    repository_root = Path(
        subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    prefix = PROJECT_ROOT.relative_to(repository_root)
    files: list[Path] = []
    for raw_path in result.stdout.decode().split("\0"):
        if not raw_path:
            continue
        relative = Path(raw_path)
        try:
            files.append(relative.relative_to(prefix))
        except ValueError:
            files.append(relative)
    return files


def version_violations() -> list[str]:
    version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    violations: list[str] = []
    if not VERSION_PATTERN.fullmatch(version):
        violations.append(f"VERSION 형식 오류: {version!r}")
        return violations

    frontend_version = json.loads(
        (PROJECT_ROOT / "frontend/package.json").read_text(encoding="utf-8")
    )["version"]
    if frontend_version != version:
        violations.append(f"frontend version 불일치: {frontend_version} != {version}")

    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        python_version = tomllib.load(handle)["project"]["version"]
    if python_version != version.removesuffix("-paper"):
        violations.append(f"Python version 불일치: {python_version} != {version}")

    readme_title = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
    if version not in readme_title:
        violations.append(f"README version 불일치: {readme_title}")
    return violations


def path_violations(paths: list[Path]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        parts = path.parts
        if path.as_posix() in FORBIDDEN_LEGACY_FILES:
            violations.append(f"현재 문서로 대체된 과거 파일이 추적됨: {path}")
        if parts and parts[0] in FORBIDDEN_ROOTS:
            violations.append(f"실행·생성 폴더가 추적됨: {path}")
        if any(part in FORBIDDEN_PARTS for part in parts):
            violations.append(f"cache 또는 의존성 파일이 추적됨: {path}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            violations.append(f"실행·생성 파일이 추적됨: {path}")
        if any(LEGACY_PART_PATTERN.search(part) for part in parts):
            violations.append(f"구버전·복사본 이름이 추적됨: {path}")
    return violations


def main() -> None:
    violations = version_violations() + path_violations(tracked_project_files())
    print(
        json.dumps(
            {
                "status": "PASS" if not violations else "FAIL",
                "version": (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip(),
                "violations": violations,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(bool(violations))


if __name__ == "__main__":
    main()
