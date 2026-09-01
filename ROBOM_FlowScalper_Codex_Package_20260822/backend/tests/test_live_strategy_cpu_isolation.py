"""CPU 전략 평가가 LIVE 이벤트 루프의 heartbeat를 막지 않는지 검증한다."""

from __future__ import annotations

import asyncio
import sys
import time

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Venue
from backend.app.runtime import PaperRuntime
from backend.app.strategies.process_evaluator import (
    ProcessStrategyEvaluator,
    StrategyEvaluationRequest,
    StrategyEvaluationResult,
)


def _consume_python_cpu(duration_seconds: float) -> None:
    """sleep 없이 Python bytecode로 GIL을 계속 요청한다."""

    deadline = time.perf_counter() + duration_seconds
    checksum = 0
    while time.perf_counter() < deadline:
        checksum = (checksum * 1_103_515_245 + 12_345) & 0x7FFF_FFFF
    assert checksum >= 0


def _cpu_bound_strategy_worker(
    request: StrategyEvaluationRequest,
) -> StrategyEvaluationResult:
    assert request.snapshot.symbol == "BTCUSDT"
    _consume_python_cpu(0.35)
    return StrategyEvaluationResult(signals=(), condition_rows=())


async def test_cpu_bound_live_strategy_evaluation_keeps_event_loop_responsive() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-cpu-isolated-strategy-evaluation",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    runtime._live_strategy_evaluator = ProcessStrategyEvaluator(
        worker_function=_cpu_bound_strategy_worker,
    )
    await runtime._live_strategy_evaluator.warm(runtime._strategy_process_state_key())
    event = MarketEvent(
        event_id="depth-cpu-isolated-strategy-evaluation",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="DEPTH_UPDATE",
        venue_ts_ms=1_000,
        receive_monotonic_ns=1_000,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
    )

    heartbeat_gaps: list[float] = []
    stop_heartbeat = asyncio.Event()

    async def heartbeat() -> None:
        loop = asyncio.get_running_loop()
        previous = loop.time()
        while not stop_heartbeat.is_set():
            await asyncio.sleep(0.005)
            current = loop.time()
            heartbeat_gaps.append(current - previous)
            previous = current

    original_switch_interval = sys.getswitchinterval()
    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0.03)
        sys.setswitchinterval(0.30)
        await runtime.ingest_live_event_async(event)
        await asyncio.sleep(0.03)
    finally:
        sys.setswitchinterval(original_switch_interval)
        stop_heartbeat.set()
        await heartbeat_task
        await runtime._live_strategy_evaluator.aclose()

    assert len(heartbeat_gaps) >= 8
    assert max(heartbeat_gaps) < 0.12
