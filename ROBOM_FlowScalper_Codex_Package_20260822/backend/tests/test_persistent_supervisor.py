"""지속 공개 supervisor의 장시간·재연결·bounded queue 계약을 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import backend.app.market_data.supervisor as supervisor_module
from backend.app.adapters.base import BackoffPolicy, ConnectionState
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import (
    DataQuality,
    MarketDataState,
    MarketEvent,
    RuntimeMode,
    Venue,
)
from backend.app.live_public import PublicDataUnavailable
from backend.app.market_data.supervisor import (
    BinanceDepthCoalescer,
    BinancePersistentProvider,
    BinanceTradeCoalescer,
    PersistentPublicSupervisor,
    ProviderSelection,
    SupervisorTelemetry,
    _wide_and_deep,
)
from backend.app.orderbook import BinanceOrderBook
from backend.app.runtime import PaperRuntime
from backend.app.time_sync import VenueClockCalibration


class RecordedProvider:
    venue = Venue.BINANCE_USDM

    def __init__(
        self,
        *,
        fail_first: bool = False,
        fail_reconnect_prepare_once: bool = False,
        burst: int = 2,
        clock_offset_ms: float = 0.0,
        clock_rtt_ms: float = 0.0,
        clock_sync_status: str = "UNVERIFIED",
    ) -> None:
        self.fail_first = fail_first
        self.fail_reconnect_prepare_once = fail_reconnect_prepare_once
        self.burst = burst
        self.prepare_count = 0
        self.connection_count = 0
        self.release = asyncio.Event()
        self.clock_offset_ms = clock_offset_ms
        self.clock_rtt_ms = clock_rtt_ms
        self.clock_sync_status = clock_sync_status

    async def prepare(self, *, run_id: str, clock: DeterministicClock) -> ProviderSelection:
        del run_id, clock
        self.prepare_count += 1
        if self.fail_reconnect_prepare_once and self.prepare_count == 2:
            raise PublicDataUnavailable("recorded prepare failure")
        wide = tuple(f"S{index:02d}USDT" for index in range(50))
        return ProviderSelection(
            venue=self.venue,
            instruments={},
            tickers={},
            wide_symbols=wide,
            deep_symbols=wide[:10],
            bootstrap_events=(),
            venue_clock_offset_ms=self.clock_offset_ms,
            venue_clock_rtt_ms=self.clock_rtt_ms,
            clock_sync_status=self.clock_sync_status,
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
    *,
    lag_ms: float = 5,
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
            lag_ms=lag_ms,
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
    assert supervisor.telemetry.clock_sync_status == "UNVERIFIED"

    provider.release.set()
    await asyncio.sleep(0.01)
    assert supervisor.telemetry.planned_rotation_count == 1
    assert supervisor.telemetry.reconnect_count == 1
    assert supervisor.telemetry.as_dict()["unplanned_reconnects"] == 0

    await supervisor.stop()
    assert supervisor.telemetry.state is ConnectionState.DISCONNECTED


async def test_supervisor_exposes_verified_venue_clock_correction() -> None:
    provider = RecordedProvider(
        clock_offset_ms=1_802.5,
        clock_rtt_ms=18,
        clock_sync_status="SYNCED",
    )
    supervisor = PersistentPublicSupervisor(
        provider,
        run_id="run-clock-sync",
        clock=DeterministicClock(),
        sink=lambda _: None,
        startup_timeout_seconds=1,
    )

    await supervisor.start()

    diagnostics = supervisor.telemetry.as_dict()
    assert diagnostics["venue_clock_offset_ms"] == 1_802.5
    assert diagnostics["venue_clock_rtt_ms"] == 18
    assert diagnostics["clock_sync_status"] == "SYNCED"

    await supervisor.stop()


async def test_planned_rotation_locks_before_provider_prepare() -> None:
    class BlockingPrepareProvider(RecordedProvider):
        def __init__(self) -> None:
            super().__init__(burst=1)
            self.blocking_prepare_count = 0
            self.rotation_prepare_started = asyncio.Event()
            self.allow_rotation_prepare = asyncio.Event()

        async def prepare(
            self,
            *,
            run_id: str,
            clock: DeterministicClock,
        ) -> ProviderSelection:
            self.blocking_prepare_count += 1
            if self.blocking_prepare_count > 1:
                self.rotation_prepare_started.set()
                await self.allow_rotation_prepare.wait()
            return await super().prepare(run_id=run_id, clock=clock)

    provider = BlockingPrepareProvider()
    supervisor = PersistentPublicSupervisor(
        provider,
        run_id="run-rotation-lock",
        clock=DeterministicClock(),
        sink=lambda _: None,
        startup_timeout_seconds=1,
    )

    await supervisor.start()
    provider.release.set()
    await asyncio.wait_for(provider.rotation_prepare_started.wait(), timeout=1)

    assert supervisor.telemetry.planned_rotation_count == 1
    assert supervisor.telemetry.state is ConnectionState.RECONNECTING
    assert supervisor.telemetry.entry_locked is True

    provider.allow_rotation_prepare.set()
    await supervisor.stop()


async def test_supervisor_clock_calibration_survives_host_wall_clock_step() -> None:
    clock = DeterministicClock(current_utc_ms=1_000, current_monotonic_ns=1_000_000_000)
    calibration = VenueClockCalibration(
        venue_anchor_ms=3_000,
        monotonic_anchor_ns=clock.monotonic_ns(),
        measured_offset_ms=2_000,
        round_trip_ms=10,
    )

    class ClockStepProvider(RecordedProvider):
        async def prepare(
            self,
            *,
            run_id: str,
            clock: DeterministicClock,
        ) -> ProviderSelection:
            selection = await super().prepare(run_id=run_id, clock=clock)
            return ProviderSelection(
                venue=selection.venue,
                instruments=selection.instruments,
                tickers=selection.tickers,
                wide_symbols=selection.wide_symbols,
                deep_symbols=selection.deep_symbols,
                bootstrap_events=(),
                venue_clock_offset_ms=calibration.measured_offset_ms,
                venue_clock_rtt_ms=calibration.round_trip_ms,
                clock_sync_status="SYNCED",
                venue_clock_calibration=calibration,
            )

        async def events(
            self,
            selection: ProviderSelection,
            *,
            run_id: str,
            clock: DeterministicClock,
        ) -> AsyncIterator[MarketEvent]:
            for sequence, venue_ts_ms in enumerate((3_000, 3_010)):
                if sequence == 1:
                    clock.current_utc_ms += 2_000
                    clock.current_monotonic_ns += 10_000_000
                yield _event(run_id, "BTCUSDT", clock, sequence).model_copy(
                    update={
                        "event_id": f"clock-step-{sequence}",
                        "event_type": "DEPTH_UPDATE",
                        "venue_ts_ms": venue_ts_ms,
                        "quality": DataQuality(
                            is_live=True,
                            is_stale=False,
                            sequence_valid=True,
                            lag_ms=selection.venue_lag_ms(
                                clock=clock,
                                venue_ts_ms=venue_ts_ms,
                            ),
                        ),
                    }
                )
            await self.release.wait()

    provider = ClockStepProvider()
    supervisor = PersistentPublicSupervisor(
        provider,
        run_id="run-clock-step",
        clock=clock,
        sink=lambda _: None,
        startup_timeout_seconds=1,
    )

    await supervisor.start()
    await asyncio.sleep(0)

    diagnostics = supervisor.telemetry.as_dict()
    assert diagnostics["venue_clock_offset_ms"] == 10
    assert diagnostics["lag_p95_ms"] == 0
    assert diagnostics["critical_lag_active"] is False
    assert diagnostics["entry_locked"] is False

    await supervisor.stop()


async def test_supervisor_reconnects_and_bounds_overload_queue() -> None:
    provider = RecordedProvider(
        fail_first=True,
        fail_reconnect_prepare_once=True,
        burst=100,
    )
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
    assert provider.prepare_count >= 3
    assert supervisor.telemetry.reconnect_count >= 1
    assert (
        supervisor.telemetry.as_dict()["unplanned_reconnects"]
        == supervisor.telemetry.reconnect_count
    )
    assert supervisor.telemetry.dropped_event_count > 0
    assert supervisor.telemetry.queue_depth <= 4
    assert supervisor.telemetry.entry_locked
    assert supervisor.telemetry.last_reconnect_error is not None
    assert "recorded prepare failure" in supervisor.telemetry.last_reconnect_error
    assert supervisor.telemetry.last_reconnect_error_ts_ms is not None
    assert (
        supervisor.telemetry.as_dict()["last_reconnect_error"]
        == supervisor.telemetry.last_reconnect_error
    )
    assert supervisor.telemetry.last_error == "QueueOverload: depth=4; capacity=4"
    assert supervisor.telemetry.queue_overload_active is True

    sink_gate.set()
    for _ in range(50):
        if supervisor.telemetry.queue_overload_recovery_count == 1:
            break
        await asyncio.sleep(0.01)
    assert supervisor.telemetry.queue_overload_active is False
    assert supervisor.telemetry.queue_overload_recovery_count == 1
    assert supervisor.telemetry.queue_depth == 0
    assert supervisor.telemetry.entry_locked is False
    assert supervisor.telemetry.last_error is None
    await supervisor.stop()


async def test_consumer_delivery_failure_locks_then_recovers_without_task_death() -> None:
    class GatedRecoveryProvider(RecordedProvider):
        def __init__(self) -> None:
            super().__init__(burst=0)
            self.allow_recovery = asyncio.Event()

        async def events(
            self,
            selection: ProviderSelection,
            *,
            run_id: str,
            clock: DeterministicClock,
        ) -> AsyncIterator[MarketEvent]:
            yield _event(run_id, selection.deep_symbols[0], clock, 0)
            await self.allow_recovery.wait()
            for sequence in range(1, 7):
                yield _event(run_id, selection.deep_symbols[0], clock, sequence).model_copy(
                    update={"event_type": "DEPTH_UPDATE"}
                )
                await asyncio.sleep(0)
            await self.release.wait()

    provider = GatedRecoveryProvider()
    delivery_attempts = 0
    delivered: list[str] = []

    async def flaky_sink(event: MarketEvent) -> None:
        nonlocal delivery_attempts
        delivery_attempts += 1
        if delivery_attempts == 1:
            raise RuntimeError("recorded sink failure")
        delivered.append(event.event_id)

    supervisor = PersistentPublicSupervisor(
        provider,
        run_id="run-consumer-recovery",
        clock=DeterministicClock(),
        sink=flaky_sink,
        queue_capacity=4,
        startup_timeout_seconds=1,
    )

    await supervisor.start()
    for _ in range(20):
        if supervisor.telemetry.consumer_delivery_failure_count == 1:
            break
        await asyncio.sleep(0.01)

    assert supervisor.telemetry.consumer_delivery_failure_count == 1
    assert supervisor.telemetry.consumer_delivery_drop_count == 1
    assert supervisor.telemetry.consumer_fault_active is True
    assert supervisor.telemetry.consumer_running is True
    assert supervisor.telemetry.entry_locked is True
    assert supervisor.telemetry.dropped_event_count == 1
    assert supervisor._tasks is not None
    assert all(not task.done() for task in supervisor._tasks)

    provider.allow_recovery.set()
    for _ in range(50):
        if supervisor.telemetry.consumer_recovery_count == 1:
            break
        await asyncio.sleep(0.01)

    assert supervisor.telemetry.consumer_recovery_count == 1
    assert supervisor.telemetry.consumer_fault_active is False
    assert supervisor.telemetry.consumer_delivery_count >= 4
    assert supervisor.telemetry.consumer_last_delivery_ts_ms is not None
    assert supervisor.telemetry.entry_locked is False
    assert supervisor.telemetry.last_error is None
    assert len(delivered) >= 4
    assert supervisor._tasks is not None
    assert all(not task.done() for task in supervisor._tasks)

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
    for interval in (5, 15, 30, 60, 180, 300, 600, 900, 1_800, 3_600, 14_400):
        runtime.set_chart_selection("BTCUSDT", interval)
        assert runtime.dashboard()["chart"]["interval"]


def test_binance_trade_coalescer_preserves_side_quantity_and_vwap() -> None:
    clock = DeterministicClock(current_utc_ms=1_000)
    coalescer = BinanceTradeCoalescer(bucket_ms=100)

    def trade(
        sequence: int,
        *,
        timestamp: int,
        price: str,
        quantity: str,
        buyer_is_aggressor: bool,
    ) -> MarketEvent:
        return MarketEvent(
            event_id=f"trade-{sequence}",
            run_id="run-coalesced-trades",
            venue=Venue.BINANCE_USDM,
            symbol="BTCUSDT",
            event_type="TRADE",
            venue_ts_ms=timestamp,
            transaction_ts_ms=timestamp,
            receive_monotonic_ns=clock.monotonic_ns(),
            sequence_end=sequence,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=5,
            ),
            data={
                "price": price,
                "quantity": quantity,
                "buyer_is_aggressor": buyer_is_aggressor,
            },
        )

    assert (
        coalescer.push(
            trade(1, timestamp=1_000, price="100", quantity="1", buyer_is_aggressor=True)
        )
        == ()
    )
    assert (
        coalescer.push(
            trade(2, timestamp=1_050, price="102", quantity="3", buyer_is_aggressor=True)
        )
        == ()
    )
    sell_pending = coalescer.push(
        trade(3, timestamp=1_080, price="99", quantity="2", buyer_is_aggressor=False)
    )
    assert sell_pending == ()
    completed = coalescer.push(
        trade(4, timestamp=1_100, price="101", quantity="1", buyer_is_aggressor=True)
    )

    assert len(completed) == 2
    buy = next(event for event in completed if event.data["buyer_is_aggressor"])
    sell_event = next(event for event in completed if not event.data["buyer_is_aggressor"])
    assert buy.data == {
        "price": "101.5",
        "quantity": "4",
        "buyer_is_aggressor": True,
        "source_event_count": 2,
    }
    assert buy.sequence_start == 1
    assert buy.sequence_end == 2
    assert sell_event.data["quantity"] == "2"
    assert sell_event.data["source_event_count"] == 1
    assert len(coalescer.flush()) == 1


def test_binance_trade_coalescer_flushes_mixed_sides_in_timestamp_order() -> None:
    quality = DataQuality(
        is_live=True,
        is_stale=False,
        sequence_valid=True,
        lag_ms=5,
    )
    coalescer = BinanceTradeCoalescer(bucket_ms=250)

    def trade(sequence: int, timestamp: int, buyer: bool) -> MarketEvent:
        return MarketEvent(
            event_id=f"trade-{sequence}",
            run_id="run-ordered-trades",
            venue=Venue.BINANCE_USDM,
            symbol="BTCUSDT",
            event_type="TRADE",
            venue_ts_ms=timestamp,
            transaction_ts_ms=timestamp,
            receive_monotonic_ns=timestamp * 1_000_000,
            sequence_end=sequence,
            quality=quality,
            data={
                "price": "100",
                "quantity": "1",
                "buyer_is_aggressor": buyer,
            },
        )

    assert coalescer.push(trade(1, 1_240, True)) == ()
    assert coalescer.push(trade(2, 1_010, False)) == ()
    completed = coalescer.push(trade(3, 1_250, True))

    assert [event.transaction_ts_ms for event in completed] == [1_010, 1_240]


def test_binance_depth_coalescer_preserves_sequence_span_and_latest_book() -> None:
    quality = DataQuality(
        is_live=True,
        is_stale=False,
        sequence_valid=True,
        lag_ms=5,
    )
    coalescer = BinanceDepthCoalescer(bucket_ms=500)

    def depth(sequence: int, timestamp: int, bid: str) -> MarketEvent:
        return MarketEvent(
            event_id=f"depth-{sequence}",
            run_id="run-coalesced-depth",
            venue=Venue.BINANCE_USDM,
            symbol="BTCUSDT",
            event_type="DEPTH_UPDATE",
            venue_ts_ms=timestamp,
            transaction_ts_ms=timestamp,
            receive_monotonic_ns=timestamp * 1_000_000,
            sequence_start=sequence,
            sequence_end=sequence,
            previous_sequence_end=sequence - 1,
            quality=quality,
            data={
                "bid": bid,
                "bid_qty": "2",
                "ask": "101",
                "ask_qty": "2",
                "bids": [[bid, "2"]],
                "asks": [["101", "2"]],
            },
        )

    assert coalescer.push(depth(10, 1_000, "100")) == ()
    assert coalescer.push(depth(11, 1_220, "100.2")) == ()
    completed = coalescer.push(depth(12, 1_500, "100.4"))

    assert len(completed) == 1
    aggregate = completed[0]
    assert aggregate.venue_ts_ms == 1_220
    assert aggregate.sequence_start == 10
    assert aggregate.sequence_end == 11
    assert aggregate.previous_sequence_end == 9
    assert aggregate.data["bid"] == "100.2"
    assert aggregate.data["source_event_count"] == 2
    assert len(coalescer.flush()) == 1


def test_strategy_snapshot_work_is_bounded_to_500ms_but_every_book_reaches_execution() -> None:
    clock = DeterministicClock(current_utc_ms=1_000)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-evaluation-cadence",
        clock=clock,
    )
    first = _event(runtime.run_id, "BTCUSDT", clock, 0).model_copy(update={"venue_ts_ms": 1_000})
    second = _event(runtime.run_id, "BTCUSDT", clock, 1).model_copy(
        update={"event_type": "DEPTH_UPDATE", "venue_ts_ms": 1_100}
    )
    third = _event(runtime.run_id, "BTCUSDT", clock, 2).model_copy(
        update={"event_type": "DEPTH_UPDATE", "venue_ts_ms": 1_250}
    )
    fourth = _event(runtime.run_id, "BTCUSDT", clock, 3).model_copy(
        update={"event_type": "DEPTH_UPDATE", "venue_ts_ms": 1_500}
    )

    runtime.ingest_live_event(first)
    first_count = runtime.strategy_evaluation_count
    runtime.ingest_live_event(second)
    assert runtime.strategy_evaluation_count == first_count
    assert runtime.latest_books["BTCUSDT"].ts_ms == 1_100
    runtime.ingest_live_event(third)
    assert runtime.strategy_evaluation_count == first_count
    assert runtime.latest_books["BTCUSDT"].ts_ms == 1_250
    runtime.ingest_live_event(fourth)
    assert runtime.strategy_evaluation_count > first_count
    assert runtime.latest_books["BTCUSDT"].ts_ms == 1_500
    diagnostics = runtime.dashboard()["system"]
    assert diagnostics["strategy_evaluation_count"] == runtime.strategy_evaluation_count
    assert diagnostics["qualified_signal_count"] == runtime.qualified_signal_count


def test_recovered_position_symbol_is_pinned_into_wide_and_deep_selection() -> None:
    ranked = tuple(f"S{index:02d}USDT" for index in range(60))

    wide, deep = _wide_and_deep(
        ranked,
        50,
        10,
        pinned_symbols=("S59USDT",),
    )

    assert len(wide) == 50
    assert len(deep) == 10
    assert "S59USDT" in wide
    assert deep[0] == "S59USDT"


async def test_manual_pause_survives_persistent_supervisor_connection(monkeypatch) -> None:
    provider = RecordedProvider()

    def provider_factory(**options: object) -> RecordedProvider:
        del options
        return provider

    monkeypatch.setattr("backend.app.runtime.BinancePersistentProvider", provider_factory)
    monkeypatch.setattr("backend.app.runtime.BybitPersistentProvider", provider_factory)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-manual-pause-start",
        clock=DeterministicClock(),
    )
    runtime.set_paused(True)

    try:
        assert await runtime.start_persistent_live() is True
        assert runtime.market_data_state is MarketDataState.LIVE
        assert runtime._manual_pause_requested is True
        assert runtime.paused is True
        assert runtime.dashboard()["operation_status"]["state"] == "MANUALLY_PAUSED"
    finally:
        await runtime.shutdown_supervisor()


def test_critical_public_lag_locks_supervisor_and_runtime_until_fresh_depth() -> None:
    clock = DeterministicClock(current_utc_ms=1_000)
    provider = RecordedProvider()
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-critical-lag",
        clock=clock,
        venue=Venue.BINANCE_USDM,
    )
    supervisor = PersistentPublicSupervisor(
        provider,
        run_id=runtime.run_id,
        clock=clock,
        sink=runtime.ingest_live_event,
    )
    runtime._supervisor = supervisor
    supervisor.telemetry.consumer_running = True
    supervisor.running = lambda: True  # type: ignore[method-assign]
    runtime.market_data_state = MarketDataState.LIVE
    runtime.runtime_health_flags = ["PUBLIC_SUPERVISOR_RUNNING", "NO_AUTH_HEADERS"]

    initial_fresh = _event(runtime.run_id, "BTCUSDT", clock, 0, lag_ms=5)
    supervisor._observe(initial_fresh)
    runtime.ingest_live_event(initial_fresh)
    delayed = _event(runtime.run_id, "BTCUSDT", clock, 1, lag_ms=2_000).model_copy(
        update={"event_type": "DEPTH_UPDATE"}
    )
    supervisor._observe(delayed)
    runtime.ingest_live_event(delayed)

    assert supervisor.telemetry.entry_locked is True
    assert supervisor.telemetry.critical_lag_event_count == 1
    assert runtime.paused is True
    assert "CRITICAL_MARKET_LAG_ENTRY_LOCK" in runtime.runtime_health_flags
    assert "SUPERVISOR_ENTRY_LOCK" in runtime.runtime_health_flags
    assert supervisor.telemetry.critical_lag_incident_count == 1
    assert supervisor.telemetry.critical_lag_last_started_ts_ms == 1_000
    safety_status = runtime.dashboard()["operation_status"]
    assert safety_status["state"] == "SAFETY_WAITING"
    assert safety_status["market_observation_active"] is True
    assert safety_status["paper_entry_active"] is False
    assert safety_status["automatic_recovery"] is True
    runtime.set_paused(False)
    assert runtime.paused is True

    clock.advance_ms(2_500)
    for sequence in range(2, 2_003):
        supervisor._observe(
            _event(runtime.run_id, "BTCUSDT", clock, sequence, lag_ms=5).model_copy(
                update={"event_type": "DEPTH_UPDATE"}
            )
        )
    recovered_depth = _event(runtime.run_id, "BTCUSDT", clock, 0, lag_ms=5)
    supervisor._observe(recovered_depth)
    runtime.ingest_live_event(recovered_depth)

    assert supervisor.telemetry.entry_locked is False
    assert supervisor.telemetry.critical_lag_last_recovered_ts_ms == 3_500
    assert supervisor.telemetry.critical_lag_last_duration_ms == 2_500
    assert supervisor.telemetry.critical_lag_max_duration_ms == 2_500
    assert "CRITICAL_MARKET_LAG_ENTRY_LOCK" not in runtime.runtime_health_flags
    assert "SUPERVISOR_ENTRY_LOCK" not in runtime.runtime_health_flags
    assert runtime.paused is False
    assert runtime.dashboard()["operation_status"]["state"] == "RUNNING"

    runtime.set_paused(True)
    supervisor._observe(recovered_depth)
    runtime.ingest_live_event(recovered_depth)
    assert runtime.paused is True
    manual_status = runtime.dashboard()["operation_status"]
    assert manual_status["state"] == "MANUALLY_PAUSED"
    assert manual_status["recommended_action"] == "RESUME"


def test_runtime_surfaces_consumer_and_queue_entry_lock_reasons() -> None:
    clock = DeterministicClock(current_utc_ms=1_000)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-consumer-health",
        clock=clock,
        venue=Venue.BINANCE_USDM,
    )
    supervisor = PersistentPublicSupervisor(
        RecordedProvider(),
        run_id=runtime.run_id,
        clock=clock,
        sink=runtime.ingest_live_event,
    )
    runtime._supervisor = supervisor
    runtime.market_data_state = MarketDataState.LIVE
    runtime.runtime_health_flags = ["PUBLIC_SUPERVISOR_RUNNING", "NO_AUTH_HEADERS"]

    supervisor.telemetry.consumer_running = False
    supervisor.telemetry.entry_locked = True
    runtime._refresh_supervisor_entry_safety()
    runtime.runtime_health_flags.append("PERSISTENCE_FAULT_ENTRY_LOCK")

    assert runtime.paused is True
    assert "ENTRY_LOCK_CONSUMER_NOT_RUNNING" in runtime.runtime_health_flags
    assert "SUPERVISOR_ENTRY_LOCK" in runtime.runtime_health_flags
    stopped_status = runtime.dashboard()["operation_status"]
    assert stopped_status["state"] == "SAFETY_BLOCKED"
    assert stopped_status["market_observation_active"] is False
    assert stopped_status["paper_entry_active"] is False
    assert stopped_status["automatic_recovery"] is False
    assert stopped_status["recommended_action"] == "NONE"
    assert "저장 또는 복구 안전문제" in stopped_status["detail_ko"]
    runtime.runtime_health_flags.remove("PERSISTENCE_FAULT_ENTRY_LOCK")
    restartable_status = runtime.dashboard()["operation_status"]
    assert restartable_status["recommended_action"] == "START"
    supervisor.running = lambda: True  # type: ignore[method-assign]

    supervisor.telemetry.consumer_running = True
    supervisor.telemetry.consumer_fault_active = True
    supervisor.telemetry.queue_overload_active = True
    runtime._refresh_supervisor_entry_safety()

    assert "ENTRY_LOCK_CONSUMER_NOT_RUNNING" not in runtime.runtime_health_flags
    assert "ENTRY_LOCK_CONSUMER_DELIVERY_FAULT" in runtime.runtime_health_flags
    assert "ENTRY_LOCK_EVENT_QUEUE_OVERLOAD" in runtime.runtime_health_flags

    supervisor.telemetry.consumer_fault_active = False
    supervisor.telemetry.queue_overload_active = False
    supervisor.telemetry.entry_locked = False
    runtime._refresh_supervisor_entry_safety()

    assert runtime.paused is False
    assert "ENTRY_LOCK_CONSUMER_DELIVERY_FAULT" not in runtime.runtime_health_flags
    assert "ENTRY_LOCK_EVENT_QUEUE_OVERLOAD" not in runtime.runtime_health_flags
    assert "SUPERVISOR_ENTRY_LOCK" not in runtime.runtime_health_flags


def test_runtime_blocks_when_supervisor_task_stops_with_stale_consumer_flag() -> None:
    clock = DeterministicClock(current_utc_ms=2_000)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-supervisor-health",
        clock=clock,
        venue=Venue.BINANCE_USDM,
    )
    supervisor = PersistentPublicSupervisor(
        RecordedProvider(),
        run_id=runtime.run_id,
        clock=clock,
        sink=runtime.ingest_live_event,
    )
    runtime._supervisor = supervisor
    runtime.market_data_state = MarketDataState.LIVE
    runtime.runtime_health_flags = ["PUBLIC_SUPERVISOR_RUNNING", "NO_AUTH_HEADERS"]
    supervisor.telemetry.consumer_running = True
    supervisor.telemetry.entry_locked = False

    runtime._refresh_supervisor_entry_safety()

    assert runtime.paused is True
    assert "ENTRY_LOCK_PUBLIC_SUPERVISOR_NOT_RUNNING" in runtime.runtime_health_flags
    status = runtime.dashboard()["operation_status"]
    assert status["state"] == "SAFETY_BLOCKED"
    assert status["market_observation_active"] is False
    assert status["paper_entry_active"] is False
    assert status["automatic_recovery"] is False
    assert status["recommended_action"] == "START"


def test_supervisor_records_receive_gap_without_changing_safety_thresholds() -> None:
    clock = DeterministicClock(current_utc_ms=10_000)
    supervisor = PersistentPublicSupervisor(
        RecordedProvider(),
        run_id="run-event-gap",
        clock=clock,
        sink=lambda _: None,
    )

    supervisor._observe(_event("run-event-gap", "BTCUSDT", clock, 0))
    clock.advance_ms(750)
    supervisor._observe(_event("run-event-gap", "BTCUSDT", clock, 0))

    diagnostics = supervisor.telemetry.as_dict()
    assert diagnostics["event_gap_last_ms"] == 750
    assert diagnostics["event_gap_max_ms"] == 750
    assert diagnostics["event_gap_max_ts_ms"] == 10_750
    assert diagnostics["event_gap_over_500ms_count"] == 1
    assert diagnostics["event_gap_last_over_500ms_ts_ms"] == 10_750
    assert diagnostics["critical_lag_threshold_ms"] == 1_500


def test_supervisor_records_event_loop_lag_without_changing_entry_state() -> None:
    telemetry = SupervisorTelemetry(entry_locked=False)

    telemetry.observe_event_loop_lag(250.25, 10_000)
    telemetry.observe_event_loop_lag(20.0, 10_100)

    diagnostics = telemetry.as_dict()
    assert diagnostics["event_loop_lag_last_ms"] == 20.0
    assert diagnostics["event_loop_lag_max_ms"] == 250.25
    assert diagnostics["event_loop_lag_over_100ms_count"] == 1
    assert diagnostics["event_loop_lag_last_over_100ms_ts_ms"] == 10_000
    assert telemetry.entry_locked is False


def test_wide_scanner_lag_is_visible_but_does_not_lock_executable_path() -> None:
    clock = DeterministicClock(current_utc_ms=1_000)
    provider = RecordedProvider()
    supervisor = PersistentPublicSupervisor(
        provider,
        run_id="run-wide-lag",
        clock=clock,
        sink=lambda _: None,
    )

    wide = _event("run-wide-lag", "ETHUSDT", clock, 1, lag_ms=9_000)
    supervisor._observe(wide)
    fresh_depth = _event("run-wide-lag", "BTCUSDT", clock, 0, lag_ms=10)
    supervisor._observe(fresh_depth)

    assert supervisor.telemetry.wide_lag_p95_ms == 9_000
    assert supervisor.telemetry.lag_p95_ms == 10
    assert supervisor.telemetry.entry_locked is False


def test_stale_trade_lag_is_separate_and_does_not_masquerade_as_book_lag() -> None:
    clock = DeterministicClock(current_utc_ms=2_000)
    supervisor = PersistentPublicSupervisor(
        RecordedProvider(),
        run_id="run-trade-lag",
        clock=clock,
        sink=lambda _: None,
    )
    stale_quality = DataQuality(
        is_live=True,
        is_stale=True,
        sequence_valid=True,
        lag_ms=2_000,
        flags=("TRADE_LAG_STALE",),
    )
    trade = _event("run-trade-lag", "BTCUSDT", clock, 1, lag_ms=2_000).model_copy(
        update={
            "event_type": "TRADE",
            "quality": stale_quality,
            "data": {
                "price": "100",
                "quantity": "1",
                "buyer_is_aggressor": True,
            },
        }
    )

    supervisor._observe(trade)
    supervisor._observe(_event("run-trade-lag", "BTCUSDT", clock, 0, lag_ms=10))

    assert supervisor.telemetry.trade_lag_p95_ms == 2_000
    assert supervisor.telemetry.stale_trade_event_count == 1
    assert supervisor.telemetry.lag_p95_ms == 10
    assert supervisor.telemetry.critical_lag_event_count == 0
    assert supervisor.telemetry.entry_locked is False


async def test_binance_marks_late_public_trade_stale_before_strategy_input() -> None:
    clock = DeterministicClock(current_utc_ms=2_000)
    selection = ProviderSelection(
        venue=Venue.BINANCE_USDM,
        instruments={},
        tickers={},
        wide_symbols=("BTCUSDT",),
        deep_symbols=("BTCUSDT",),
        bootstrap_events=(),
    )
    provider = BinancePersistentProvider()
    payload = {
        "e": "aggTrade",
        "E": 1_000,
        "T": 1_000,
        "s": "BTCUSDT",
        "a": 7,
        "p": "100",
        "q": "2",
        "m": False,
    }

    events = [
        event
        async for event in provider._normalize(
            payload,
            selection,
            {},
            object(),  # aggTrade 경로에서는 REST 호가 adapter를 사용하지 않는다.
            run_id="run-stale-trade",
            clock=clock,
        )
    ]

    assert len(events) == 1
    assert events[0].quality.is_stale is True
    assert events[0].quality.flags == ("TRADE_LAG_STALE",)


async def test_binance_rotation_warmup_applies_but_suppresses_stale_depth() -> None:
    clock = DeterministicClock(current_utc_ms=5_000)
    selection = ProviderSelection(
        venue=Venue.BINANCE_USDM,
        instruments={},
        tickers={},
        wide_symbols=("BTCUSDT",),
        deep_symbols=("BTCUSDT",),
        bootstrap_events=(),
    )
    provider = BinancePersistentProvider()
    book = BinanceOrderBook()
    book.reset_snapshot(100, [["99", "1"]], [["101", "1"]])
    books = {"BTCUSDT": book}
    stale_payload = {
        "e": "depthUpdate",
        "E": 1_000,
        "T": 1_000,
        "s": "BTCUSDT",
        "U": 101,
        "u": 101,
        "pu": 100,
        "b": [["100", "1"]],
        "a": [["102", "1"]],
    }

    stale_events = [
        event
        async for event in provider._normalize(
            stale_payload,
            selection,
            books,
            object(),
            run_id="run-rotation-warmup",
            clock=clock,
            suppress_stale_depth=True,
        )
    ]

    assert stale_events == []
    assert book.last_update_id == 101

    fresh_payload = {
        **stale_payload,
        "E": 5_000,
        "T": 5_000,
        "U": 102,
        "u": 102,
        "pu": 101,
        "b": [["100", "2"]],
    }
    fresh_events = [
        event
        async for event in provider._normalize(
            fresh_payload,
            selection,
            books,
            object(),
            run_id="run-rotation-warmup",
            clock=clock,
            suppress_stale_depth=True,
        )
    ]

    assert len(fresh_events) == 1
    assert fresh_events[0].event_type == "DEPTH_UPDATE"
    assert fresh_events[0].quality.lag_ms == 0
    assert book.last_update_id == 102


def test_rotation_waits_for_fresh_depth_from_every_selected_symbol() -> None:
    clock = DeterministicClock(current_utc_ms=5_000)
    warming_symbols = {"BTCUSDT", "ETHUSDT"}
    btc = _event("run-rotation-all-symbols", "BTCUSDT", clock, 1).model_copy(
        update={"event_type": "DEPTH_UPDATE"}
    )
    eth = _event("run-rotation-all-symbols", "ETHUSDT", clock, 2).model_copy(
        update={"event_type": "DEPTH_UPDATE"}
    )

    assert (
        supervisor_module._rotation_depth_output_ready(btc, warming_symbols) is False
    )
    assert warming_symbols == {"ETHUSDT"}
    assert (
        supervisor_module._rotation_depth_output_ready(btc, warming_symbols) is False
    )
    assert warming_symbols == {"ETHUSDT"}
    assert supervisor_module._rotation_depth_output_ready(eth, warming_symbols) is True
    assert warming_symbols == set()


def test_lag_percentile_work_is_bounded_during_high_frequency_events() -> None:
    telemetry = SupervisorTelemetry(lag_percentile_refresh_samples=256)

    for sequence in range(2_000):
        telemetry.observe_lag(float(sequence % 100), executable_path=True)
        _ = telemetry.lag_p95_ms
        _ = telemetry.lag_p50_ms

    assert telemetry.lag_p95_ms == 94
    assert telemetry.lag_p50_ms == 49
    assert telemetry.lag_percentile_refresh_count <= 12


def test_transient_first_lag_recovers_during_warmup_without_waiting_256_events() -> None:
    telemetry = SupervisorTelemetry(lag_percentile_refresh_samples=256)

    telemetry.observe_lag(12_000, executable_path=True)
    for _ in range(15):
        telemetry.observe_lag(15, executable_path=True)

    assert telemetry.lag_p95_ms == 15
    assert telemetry.lag_p50_ms == 15
    assert telemetry.critical_lag_active is False
