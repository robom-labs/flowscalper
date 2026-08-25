"""종료 중 취소 신호가 겹쳐도 PAPER 저장 작업을 유실하지 않는지 검증한다."""

from __future__ import annotations

import asyncio

import pytest

from backend.app.main import _await_shutdown_task


@pytest.mark.asyncio
async def test_shutdown_waiter_finishes_persistence_after_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def persistence() -> None:
        started.set()
        await release.wait()
        finished.set()

    persistence_task = asyncio.create_task(persistence())
    waiter = asyncio.create_task(_await_shutdown_task(persistence_task))
    await started.wait()

    waiter.cancel()
    release.set()
    await waiter

    assert finished.is_set()
    assert persistence_task.done()
