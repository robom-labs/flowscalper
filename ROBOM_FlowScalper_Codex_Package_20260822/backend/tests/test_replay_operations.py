"""저장 Run 검증 작업의 즉시 응답·상태·취소·timeout 계약을 검증한다."""

from __future__ import annotations

import asyncio

import pytest

from backend.app.replay.operations import (
    ReplayOperationConflict,
    ReplayOperationManager,
)


class IncrementingClock:
    def __init__(self) -> None:
        self.value = 1_000

    def __call__(self) -> int:
        self.value += 1
        return self.value


@pytest.mark.asyncio
async def test_replay_operation_reports_progress_and_result() -> None:
    clock = IncrementingClock()
    manager = ReplayOperationManager(clock)
    release = asyncio.Event()

    async def runner(progress):
        await progress("PREPARING", "원장 준비")
        await progress("PROCESSING", "전략 검증")
        await release.wait()
        return {"replay_id": "replay-complete", "real_orders_enabled": False}

    requested = await manager.submit(
        source_run_id="run-one",
        symbol="BTCUSDT",
        total_events=123,
        runner=runner,
    )
    await asyncio.sleep(0)
    processing = manager.get_public(str(requested["operation_id"]))

    assert requested["state"] == "REQUESTED"
    assert processing is not None
    assert processing["state"] == "PROCESSING"
    assert processing["total_events"] == 123
    assert processing["paper_only"] is True
    assert processing["real_orders_enabled"] is False

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    completed = manager.get_public(str(requested["operation_id"]))
    assert completed is not None
    assert completed["state"] == "COMPLETED"
    assert completed["result"] == {
        "replay_id": "replay-complete",
        "real_orders_enabled": False,
    }


@pytest.mark.asyncio
async def test_replay_operation_is_idempotent_and_conflicts_with_other_scope() -> None:
    manager = ReplayOperationManager(IncrementingClock())
    release = asyncio.Event()

    async def runner(_progress):
        await release.wait()
        return {"replay_id": "replay-one"}

    first = await manager.submit(
        source_run_id="run-one",
        symbol="BTCUSDT",
        total_events=10,
        runner=runner,
    )
    duplicate = await manager.submit(
        source_run_id="run-one",
        symbol="BTCUSDT",
        total_events=10,
        runner=runner,
    )
    assert duplicate["operation_id"] == first["operation_id"]

    with pytest.raises(ReplayOperationConflict):
        await manager.submit(
            source_run_id="run-two",
            symbol="ETHUSDT",
            total_events=20,
            runner=runner,
        )

    await manager.cancel(str(first["operation_id"]))
    cancelled = manager.get_public(str(first["operation_id"]))
    for _ in range(10):
        if cancelled is not None and cancelled["state"] == "CANCELLED":
            break
        await asyncio.sleep(0)
        cancelled = manager.get_public(str(first["operation_id"]))
    assert cancelled is not None
    assert cancelled["state"] == "CANCELLED"


@pytest.mark.asyncio
async def test_replay_operation_timeout_is_retryable() -> None:
    manager = ReplayOperationManager(IncrementingClock(), timeout_seconds=0.01)

    async def runner(_progress):
        await asyncio.Event().wait()
        return {}

    requested = await manager.submit(
        source_run_id="run-timeout",
        symbol=None,
        total_events=None,
        runner=runner,
    )
    await asyncio.sleep(0.03)
    failed = manager.get_public(str(requested["operation_id"]))
    assert failed is not None
    assert failed["state"] == "FAILED_RETRYABLE"
    assert failed["error_code"] == "REPLAY_TIMEOUT"
    assert failed["retryable"] is True
