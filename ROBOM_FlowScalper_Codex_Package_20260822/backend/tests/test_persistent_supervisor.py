"""지속 공개 supervisor의 장시간·재연결·bounded queue 계약을 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from backend.app.adapters.base import BackoffPolicy, ConnectionState
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Venue
from backend.app.market_data.supervisor import (
    PersistentPublicSupervisor,
    ProviderSelection,
)
from backend.app.runtime import PaperRuntime


class RecordedProvider:
    venue = Venue.BINANCE_USDM

    def __init__(self, *, fail_first: bool = False, burst: int = 2) -> None:
        self.fail_first = fail_first
        self.burst = burst
        self.connection_count = 0
        self.release = asyncio.Event()

    async def prepare(
        self, *, run_id: str, clock: DeterministicClock
    ) -> ProviderSelection:
        del run_id, clock
        wide = tuple(f"S{index:02d}USDT" for index in range(50))
        return ProviderSelection(
            venue=self.venue,
            instruments={},
            tickers={},
            wide_symbols=wide,
            deep_symbols=wide[:10],
            bootstrap_events=(),
        )

    async def events(
        self,
        selection: ProviderSelection,
        *,
        run_id: str,
        clock: DeterministicClock,
    ) -> AsyncIterator[MarketEvent]:
        self.connection_count += 1
        if self.fail_first and self.connection_count == 1:
            raise OSError("recorded disconnect")
        for index in range(self.burst):
            yield _event(
                run_id,
                selection.deep_symbols[index % len(selection.deep_symbols)],
                clock,
                index,
            )
        await self.release.wait()


def _event(
    run_id: str,
    symbol: str,
    clock: DeterministicClock,
    sequence: int,
) -> MarketEvent:
    return MarketEvent(
        event_id=f"recorded-{sequence}",
        run_id=run_id,
        venue=Venue.BINANCE_USDM,
        symbol=symbol,
        event_type="DEPTH_UPDATE" if sequence == 0 else "BOOK_TICKER",
        venue_ts_ms=clock.utc_ms(),
        receive_monotonic_ns=clock.monotonic_ns(),
        sequence_start=sequence,
        sequence_end=sequence,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=5,
        ),
        data={"bid": "100", "bid_qty": "2", "ask": "100.1", "ask_qty": "2"},
    )


async def test_supervisor_remains_running_after_first_verified_event() -> None:
    provider = RecordedProvider()
    delivered: list[MarketEvent] = []
    supervisor = PersistentPublicSupervisor(
        provider,
        run_id="run-persistent",
        clock=DeterministicClock(),
        sink=delivered.append,
        startup_timeout_seconds=1,
    )

    selection = await supervisor.start()
    await asyncio.sleep(0)

    assert len(selection.wide_symbols) == 50
    assert len(selection.deep_symbols) == 10
    assert supervisor.telemetry.state is ConnectionState.LIVE
    assert supervisor.telemetry.entry_locked is False
    assert supervisor.telemetry.event_count == 2
    assert len(delivered) == 2
    assert supervisor._tasks is not None
    assert all(not task.done() for task in supervisor._tasks)

    await supervisor.stop()
    assert supervisor.telemetry.state is ConnectionState.DISCONNECTED


async def test_supervisor_reconnects_and_bounds_overload_queue() -> None:
    provider = RecordedProvider(fail_first=True, burst=100)
    sink_gate = asyncio.Event()

    async def slow_sink(_: MarketEvent) -> None:
        await sink_gate.wait()

    supervisor = PersistentPublicSupervisor(
        provider,
        run_id="run-overload",
        clock=DeterministicClock(),
        sink=slow_sink,
        queue_capacity=4,
        startup_timeout_seconds=1,
        backoff=BackoffPolicy(initial_ms=1, maximum_ms=2, jitter_fraction=0),
    )

    await supervisor.start()
    await asyncio.sleep(0.01)

    assert provider.connection_count >= 2
    assert supervisor.telemetry.reconnect_count >= 1
    assert supervisor.telemetry.dropped_event_count > 0
    assert supervisor.telemetry.queue_depth <= 4
    assert supervisor.telemetry.entry_locked

    sink_gate.set()
    await supervisor.stop()


def test_runtime_builds_every_chart_interval_from_public_trades() -> None:
    clock = DeterministicClock(current_utc_ms=2_000)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-candles",
        clock=clock,
    )
    for sequence, (timestamp, price) in enumerate(
        ((1_000, "100"), (1_500, "101"), (2_000, "99")),
        start=1,
    ):
        runtime.ingest_live_event(
            MarketEvent(
                event_id=f"trade-{sequence}",
                run_id=runtime.run_id,
                venue=runtime.venue,
                symbol="BTCUSDT",
                event_type="TRADE",
                venue_ts_ms=timestamp,
                transaction_ts_ms=timestamp,
                receive_monotonic_ns=clock.monotonic_ns(),
                quality=DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=0,
                ),
                data={
                    "price": price,
                    "quantity": "1",
                    "buyer_is_aggressor": True,
                },
            )
        )
    runtime.set_chart_selection("btcusdt", 1)
    dashboard = runtime.dashboard()

    assert dashboard["chart"]["interval"] == "1s"
    assert len(dashboard["chart"]["candles"]) == 2
    assert dashboard["chart"]["candles"][0]["open"] == 100.0
    assert dashboard["chart"]["candles"][0]["close"] == 101.0
    for interval in (5, 15, 30, 60, 180, 300, 600, 900):
        runtime.set_chart_selection("BTCUSDT", interval)
        assert runtime.dashboard()["chart"]["interval"]
