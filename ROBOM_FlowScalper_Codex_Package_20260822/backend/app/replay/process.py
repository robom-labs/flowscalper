"""LIVE 수신과 CPU 경쟁을 피하도록 저장 Run 리플레이를 별도 프로세스에서 실행한다."""

from __future__ import annotations

import os
from pathlib import Path

from backend.app.replay.market import StoredMarketReplay
from backend.app.storage.parquet import ParquetEventStore
from backend.app.storage.sqlite import SQLiteLedger

_LOW_PRIORITY_APPLIED = False


def replay_stored_run_from_paths(
    database_path: str,
    archive_root: str | None,
    source_run_id: str,
    created_ts_ms: int,
    symbol: str | None,
) -> dict[str, object]:
    """독립 SQLite 연결과 낮은 OS 우선순위로 결정적 PAPER replay를 실행한다."""

    global _LOW_PRIORITY_APPLIED
    nice = getattr(os, "nice", None)
    if callable(nice) and not _LOW_PRIORITY_APPLIED:
        try:
            nice(10)
        except OSError:
            pass
        _LOW_PRIORITY_APPLIED = True
    archive = ParquetEventStore(Path(archive_root)) if archive_root else None
    ledger = SQLiteLedger(
        Path(database_path),
        market_event_archive=archive,
    )
    try:
        return StoredMarketReplay().run(
            ledger,
            source_run_id=source_run_id,
            created_ts_ms=created_ts_ms,
            symbol=symbol.strip().upper() if symbol else None,
        ).as_dict()
    finally:
        ledger.close()
