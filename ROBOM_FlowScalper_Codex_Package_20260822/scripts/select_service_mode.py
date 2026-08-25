"""열린 PAPER Run이 있을 때만 macOS 자동실행 복구 모드를 선택한다."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from backend.app.domain.models import RuntimeMode


def select_service_mode(database: Path) -> str:
    if not database.is_file():
        return RuntimeMode.READY.value
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            row = connection.execute(
                """
                SELECT mode
                FROM runs
                WHERE finalized_ts_ms IS NULL
                ORDER BY started_ts_ms DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return RuntimeMode.READY.value
    if row is None:
        return RuntimeMode.READY.value
    mode = str(row[0])
    recoverable = {
        RuntimeMode.LIVE_SHADOW_PAPER.value,
        RuntimeMode.DEMO_FIXTURE.value,
    }
    return mode if mode in recoverable else RuntimeMode.READY.value


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("사용법: select_service_mode.py <ledger.sqlite3>")
    print(select_service_mode(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
