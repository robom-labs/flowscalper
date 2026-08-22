"""SQLite 스키마를 멱등적으로 최신 버전으로 준비한다."""

from __future__ import annotations

import os
from pathlib import Path

from backend.app.storage.sqlite import SQLiteLedger

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    default_path = PROJECT_ROOT / "data" / "run-ledger.sqlite3"
    path = Path(os.environ.get("ROBOM_DB_PATH", str(default_path)))
    ledger = SQLiteLedger(path)
    ledger.close()
    print(f"PASS: SQLite schema ready: {path}")


if __name__ == "__main__":
    main()
