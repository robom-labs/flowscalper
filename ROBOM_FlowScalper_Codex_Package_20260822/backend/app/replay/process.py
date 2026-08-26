"""LIVE 수신과 CPU 경쟁을 피하도록 저장 Run 리플레이를 별도 프로세스에서 실행한다."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from backend.app.replay.focus import ReplayFocusSessionBuilder
from backend.app.replay.market import StoredMarketReplay
from backend.app.replay.timeline import build_replay_timeline
from backend.app.storage.parquet import ParquetEventStore
from backend.app.storage.sqlite import SQLiteLedger

_LOW_PRIORITY_APPLIED = False
_REPLAY_TARGET_CPU_RATIO = 0.05


class _ReplayCpuBudget:
    """LIVE 수신이 우선되도록 replay 프로세스의 구간 CPU 점유율을 제한한다."""

    def __init__(
        self,
        *,
        target_cpu_ratio: float = _REPLAY_TARGET_CPU_RATIO,
        max_sleep_seconds: float = 0.50,
        monotonic: Callable[[], float] = time.monotonic,
        process_time: Callable[[], float] = time.process_time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < target_cpu_ratio <= 1:
            raise ValueError("target_cpu_ratio는 0 초과 1 이하여야 합니다.")
        if max_sleep_seconds <= 0:
            raise ValueError("max_sleep_seconds는 양수여야 합니다.")
        self._target_cpu_ratio = target_cpu_ratio
        self._max_sleep_seconds = max_sleep_seconds
        self._monotonic = monotonic
        self._process_time = process_time
        self._sleeper = sleeper
        self._interval_wall = monotonic()
        self._interval_cpu = process_time()

    def checkpoint(self) -> None:
        """직전 checkpoint 구간의 CPU가 목표 비율을 넘은 만큼 짧게 양보한다."""

        elapsed_wall = max(0.0, self._monotonic() - self._interval_wall)
        elapsed_cpu = max(0.0, self._process_time() - self._interval_cpu)
        required_wall = elapsed_cpu / self._target_cpu_ratio
        sleep_seconds = min(
            self._max_sleep_seconds,
            max(0.0, required_wall - elapsed_wall),
        )
        if sleep_seconds > 0:
            self._sleeper(sleep_seconds)
        self._interval_wall += elapsed_wall + sleep_seconds
        self._interval_cpu += elapsed_cpu


def replay_stored_run_from_paths(
    database_path: str,
    archive_root: str | None,
    source_run_id: str,
    created_ts_ms: int,
    symbol: str | None,
    event_limit: int | None,
) -> dict[str, object]:
    """독립 SQLite 연결과 낮은 OS 우선순위로 결정적 PAPER replay를 실행한다."""

    cpu_budget = _prepare_cpu_budget()
    ledger = _open_ledger(database_path, archive_root)
    try:
        return StoredMarketReplay().run(
            ledger,
            source_run_id=source_run_id,
            created_ts_ms=created_ts_ms,
            symbol=symbol.strip().upper() if symbol else None,
            event_limit=event_limit,
            cooperative_yield=cpu_budget.checkpoint,
            persist_result=False,
        ).as_dict()
    finally:
        ledger.close()


def replay_timeline_from_paths(
    database_path: str,
    archive_root: str | None,
    source_run_id: str,
    symbol: str | None,
    limit: int,
) -> dict[str, object]:
    """화면 timeline 조회도 LIVE와 분리된 저우선순위 프로세스에서 실행한다."""

    cpu_budget = _prepare_cpu_budget()
    ledger = _open_ledger(database_path, archive_root)
    try:
        return build_replay_timeline(
            ledger,
            source_run_id,
            symbol=symbol.strip().upper() if symbol else None,
            limit=limit,
            cooperative_yield=cpu_budget.checkpoint,
        )
    finally:
        ledger.close()


def replay_focus_session_from_paths(
    database_path: str,
    archive_root: str | None,
    source_run_id: str,
    trade_id: str,
    profile: str,
    created_ts_ms: int,
) -> dict[str, object]:
    """거래 집중 replay의 원장 읽기와 전략 재처리를 LIVE 프로세스에서 격리한다."""

    cpu_budget = _prepare_cpu_budget()
    ledger = _open_ledger(database_path, archive_root)
    try:
        return ReplayFocusSessionBuilder().build(
            ledger,
            run_id=source_run_id,
            trade_id=trade_id,
            profile=profile,
            created_ts_ms=created_ts_ms,
            cooperative_yield=cpu_budget.checkpoint,
        )
    finally:
        ledger.close()


def _prepare_cpu_budget() -> _ReplayCpuBudget:
    """worker 프로세스 우선순위와 누적 CPU 예산을 적용한다."""

    global _LOW_PRIORITY_APPLIED
    nice = getattr(os, "nice", None)
    if callable(nice) and not _LOW_PRIORITY_APPLIED:
        try:
            nice(19)
        except OSError:
            pass
        _LOW_PRIORITY_APPLIED = True
    return _ReplayCpuBudget()


def _open_ledger(database_path: str, archive_root: str | None) -> SQLiteLedger:
    archive = ParquetEventStore(Path(archive_root)) if archive_root else None
    return SQLiteLedger(Path(database_path), market_event_archive=archive)
