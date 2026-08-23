"""저장소 위생 검사에서 구버전·생성 파일과 버전 불일치를 차단한다."""

from pathlib import Path

from scripts.check_repository_hygiene import path_violations, version_violations


def test_current_versions_are_consistent() -> None:
    assert version_violations() == []


def test_generated_and_legacy_paths_are_rejected() -> None:
    violations = path_violations(
        [
            Path("frontend/App_backup.tsx"),
            Path("data/run-ledger.sqlite3"),
            Path("frontend/tsconfig.app.tsbuildinfo"),
            Path("artifacts/release/current.zip"),
            Path("FINAL_EVIDENCE.md"),
        ]
    )

    messages = "\n".join(violations)
    assert "App_backup.tsx" in messages
    assert "run-ledger.sqlite3" in messages
    assert "tsconfig.app.tsbuildinfo" in messages
    assert "current.zip" in messages
    assert "FINAL_EVIDENCE.md" in messages
