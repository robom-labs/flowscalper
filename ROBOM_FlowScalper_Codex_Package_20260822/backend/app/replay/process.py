"""LIVE 수신과 CPU 경쟁을 피하도록 저장 Run 리플레이를 별도 프로세스에서 실행한다."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pyarrow as pa

from backend.app.replay.market import StoredMarketReplay
from backend.app.replay.timeline import build_replay_timeline
from backend.app.storage.io_priority import storage_io_priority_gate
from backend.app.storage.parquet import ParquetEventStore
from backend.app.storage.parquet import (
    _apply_background_io_policy as _apply_replay_background_io_policy,
)
from backend.app.storage.sqlite import SQLiteLedger

_LOW_PRIORITY_APPLIED = False
_REPLAY_TARGET_CPU_RATIO = 0.05
_REPLAY_TARGET_ARCHIVE_READ_BYTES_PER_SECOND = 256 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_REPLAY_SINGLE_THREAD_ENVIRONMENT = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


class _ReplayCpuBudget:
    """LIVE 수신이 우선되도록 replay 프로세스의 구간 CPU 점유율을 제한한다."""

    def __init__(
        self,
        *,
        target_cpu_ratio: float = _REPLAY_TARGET_CPU_RATIO,
        target_archive_read_bytes_per_second: float = (
            _REPLAY_TARGET_ARCHIVE_READ_BYTES_PER_SECOND
        ),
        max_sleep_seconds: float = 0.50,
        monotonic: Callable[[], float] = time.monotonic,
        process_time: Callable[[], float] = time.process_time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < target_cpu_ratio <= 1:
            raise ValueError("target_cpu_ratio는 0 초과 1 이하여야 합니다.")
        if max_sleep_seconds <= 0:
            raise ValueError("max_sleep_seconds는 양수여야 합니다.")
        if target_archive_read_bytes_per_second <= 0:
            raise ValueError("archive read 대역폭은 양수여야 합니다.")
        self._target_cpu_ratio = target_cpu_ratio
        self._target_archive_read_bytes_per_second = (
            target_archive_read_bytes_per_second
        )
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

    def archive_checkpoint(self, bytes_read: int) -> None:
        """archive 파일 사이에 명시적으로 쉬어 LIVE 영속화 I/O를 우선한다."""

        if bytes_read < 0:
            raise ValueError("archive read bytes는 0 이상이어야 합니다.")
        self.checkpoint()
        if bytes_read:
            self._sleeper(bytes_read / self._target_archive_read_bytes_per_second)


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
            archive_batch_yield=cpu_budget.archive_checkpoint,
            archive_batch_guard=lambda: _replay_archive_read_gate(database_path),
            persist_result=False,
        ).as_dict()
    finally:
        ledger.close()


async def replay_stored_run_in_subprocess(
    database_path: str,
    archive_root: str | None,
    source_run_id: str,
    created_ts_ms: int,
    symbol: str | None,
    event_limit: int | None,
) -> dict[str, object]:
    """Python import 전부터 낮은 CPU·I/O 우선순위인 취소 가능한 worker를 실행한다."""

    payload = {
        "database_path": database_path,
        "archive_root": archive_root,
        "source_run_id": source_run_id,
        "created_ts_ms": created_ts_ms,
        "symbol": symbol,
        "event_limit": event_limit,
    }
    process = await asyncio.create_subprocess_exec(
        *_worker_command(),
        cwd=str(_PROJECT_ROOT),
        env=_worker_environment(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await process.communicate(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        )
    except asyncio.CancelledError:
        await _terminate_worker(process)
        raise
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()[-2_000:]
        raise RuntimeError(
            f"replay worker가 종료 코드 {process.returncode}로 실패했습니다: {detail}"
        )
    decoded: Any = json.loads(stdout)
    if not isinstance(decoded, dict):
        raise RuntimeError("replay worker 결과는 JSON 객체여야 합니다.")
    return {str(key): value for key, value in decoded.items()}


def _worker_command() -> tuple[str, ...]:
    python_command = (sys.executable, "-m", "backend.app.replay.process")
    taskpolicy = Path("/usr/sbin/taskpolicy")
    nice = Path("/usr/bin/nice")
    if sys.platform == "darwin" and taskpolicy.is_file() and nice.is_file():
        return (
            str(nice),
            "-n",
            "19",
            str(taskpolicy),
            "-b",
            *python_command,
        )
    return python_command


async def _terminate_worker(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except TimeoutError:
        process.kill()
        await process.wait()


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


def _prepare_cpu_budget() -> _ReplayCpuBudget:
    """worker 프로세스의 CPU와 I/O를 모두 LIVE보다 낮은 우선순위로 둔다."""

    global _LOW_PRIORITY_APPLIED
    _limit_replay_worker_threads()
    _apply_replay_background_io_policy()
    nice = getattr(os, "nice", None)
    if callable(nice) and not _LOW_PRIORITY_APPLIED:
        try:
            nice(19)
        except OSError:
            pass
        _LOW_PRIORITY_APPLIED = True
    return _ReplayCpuBudget()


def _worker_environment() -> dict[str, str]:
    """Python import 전부터 수치 라이브러리의 병렬 worker 생성을 막는다."""

    environment = dict(os.environ)
    for variable in _REPLAY_SINGLE_THREAD_ENVIRONMENT:
        environment[variable] = "1"
    return environment


def _limit_replay_worker_threads() -> None:
    """Parquet 한 파일의 병렬 decode burst가 LIVE 수신을 밀지 않게 한다."""

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)


def _open_ledger(database_path: str, archive_root: str | None) -> SQLiteLedger:
    archive = ParquetEventStore(Path(archive_root)) if archive_root else None
    return SQLiteLedger(Path(database_path), market_event_archive=archive)


def _replay_archive_read_gate(database_path: str) -> AbstractContextManager[None]:
    """각 archive 읽기는 LIVE 영속화가 끝난 뒤 공유 I/O 구간에서 수행한다."""

    return storage_io_priority_gate(database_path, exclusive=False)


def _worker_main() -> int:
    """stdin JSON 요청 하나를 처리하고 stdout JSON 결과 하나만 반환한다."""

    payload: Any = json.loads(sys.stdin.buffer.read())
    if not isinstance(payload, dict):
        raise ValueError("replay worker 요청은 JSON 객체여야 합니다.")
    result = replay_stored_run_from_paths(
        str(payload["database_path"]),
        str(payload["archive_root"]) if payload.get("archive_root") is not None else None,
        str(payload["source_run_id"]),
        int(str(payload["created_ts_ms"])),
        str(payload["symbol"]) if payload.get("symbol") is not None else None,
        int(str(payload["event_limit"]))
        if payload.get("event_limit") is not None
        else None,
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())
