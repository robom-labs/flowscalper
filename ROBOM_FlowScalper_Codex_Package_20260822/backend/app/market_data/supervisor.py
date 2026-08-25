"""공개 WebSocket을 장시간 감독하고 bounded queue로 정규 이벤트를 전달한다."""

from __future__ import annotations

import asyncio
import inspect
import json
import random
import statistics
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import httpx
import websockets
from websockets.exceptions import WebSocketException

from backend.app.adapters.base import BackoffPolicy, ConnectionState
from backend.app.adapters.binance_usdm import BinancePublicAdapter
from backend.app.adapters.binance_usdm.public import BinanceStreamRouter
from backend.app.adapters.bybit_linear import BybitPublicAdapter
from backend.app.adapters.bybit_linear.public import PUBLIC_LINEAR_WS
from backend.app.clocks import Clock
from backend.app.domain.market import Instrument, Ticker
from backend.app.domain.models import DataQuality, MarketEvent, Venue
from backend.app.live_public import PublicDataUnavailable, _eligible_tickers, _ticker_events
from backend.app.orderbook import BinanceOrderBook, BybitOrderBook, SequenceGap
from backend.app.time_sync import (
    VenueClockCalibration,
    estimate_venue_clock_calibration,
    venue_lag_ms,
)

EventSink = Callable[[MarketEvent], Awaitable[None] | None]
ProtectedSymbolSource = Callable[[], Sequence[str]]
_STRATEGY_TRADE_LAG_MAX_MS = 500.0


def _percentile_95(ordered: Sequence[float]) -> float | None:
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * 0.95)))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    venue: Venue
    instruments: dict[str, Instrument]
    tickers: dict[str, Ticker]
    wide_symbols: tuple[str, ...]
    deep_symbols: tuple[str, ...]
    bootstrap_events: tuple[MarketEvent, ...]
    venue_clock_offset_ms: float = 0.0
    venue_clock_rtt_ms: float = 0.0
    clock_sync_status: str = "UNVERIFIED"
    venue_clock_calibration: VenueClockCalibration | None = None

    def current_venue_clock_offset_ms(self, clock: Clock) -> float:
        if self.venue_clock_calibration is None:
            return self.venue_clock_offset_ms
        return self.venue_clock_calibration.current_offset_ms(
            local_utc_ms=clock.utc_ms(),
            monotonic_ns=clock.monotonic_ns(),
        )

    def venue_lag_ms(self, *, clock: Clock, venue_ts_ms: int) -> float:
        if self.venue_clock_calibration is not None:
            return self.venue_clock_calibration.lag_ms(
                venue_ts_ms=venue_ts_ms,
                monotonic_ns=clock.monotonic_ns(),
            )
        return venue_lag_ms(
            local_utc_ms=clock.utc_ms(),
            venue_ts_ms=venue_ts_ms,
            venue_clock_offset_ms=self.venue_clock_offset_ms,
        )


class PublicStreamProvider(Protocol):
    venue: Venue

    async def prepare(self, *, run_id: str, clock: Clock) -> ProviderSelection: ...

    def events(
        self,
        selection: ProviderSelection,
        *,
        run_id: str,
        clock: Clock,
    ) -> AsyncIterator[MarketEvent]: ...


@dataclass(slots=True)
class SupervisorTelemetry:
    state: ConnectionState = ConnectionState.DISCONNECTED
    event_count: int = 0
    reconnect_count: int = 0
    gap_count: int = 0
    resync_count: int = 0
    dropped_event_count: int = 0
    queue_depth: int = 0
    queue_capacity: int = 0
    started_monotonic_ns: int | None = None
    last_event_monotonic_ns: int | None = None
    last_error: str | None = None
    planned_rotation_count: int = 0
    trade_source_event_count: int = 0
    trade_output_event_count: int = 0
    entry_locked: bool = True
    critical_lag_threshold_ms: float = 1_500.0
    critical_lag_event_count: int = 0
    critical_lag_active: bool = False
    critical_lag_incident_count: int = 0
    critical_lag_last_started_ts_ms: int | None = None
    critical_lag_last_recovered_ts_ms: int | None = None
    critical_lag_last_duration_ms: float | None = None
    critical_lag_max_duration_ms: float = 0.0
    _critical_lag_started_monotonic_ns: int | None = None
    event_gap_last_ms: float = 0.0
    event_gap_max_ms: float = 0.0
    event_gap_max_ts_ms: int | None = None
    event_gap_over_500ms_count: int = 0
    event_gap_last_over_500ms_ts_ms: int | None = None
    lag_samples_ms: deque[float] = field(default_factory=lambda: deque(maxlen=2_000))
    trade_lag_samples_ms: deque[float] = field(default_factory=lambda: deque(maxlen=2_000))
    wide_lag_samples_ms: deque[float] = field(default_factory=lambda: deque(maxlen=2_000))
    lag_percentile_refresh_samples: int = 256
    lag_percentile_refresh_count: int = 0
    _lag_p95_cache_ms: float | None = None
    _lag_p50_cache_ms: float | None = None
    _trade_lag_p95_cache_ms: float | None = None
    _wide_lag_p95_cache_ms: float | None = None
    _deep_samples_since_refresh: int = 0
    _trade_samples_since_refresh: int = 0
    _wide_samples_since_refresh: int = 0
    stale_trade_event_count: int = 0
    venue_clock_offset_ms: float = 0.0
    venue_clock_rtt_ms: float = 0.0
    clock_sync_status: str = "UNVERIFIED"

    def observe_event_gap(self, receive_monotonic_ns: int, observed_ts_ms: int) -> None:
        """연속 공개 이벤트의 수신 공백을 네트워크·프로세스 정지 진단용으로 남긴다."""

        previous = self.last_event_monotonic_ns
        if previous is None or receive_monotonic_ns < previous:
            return
        gap_ms = (receive_monotonic_ns - previous) / 1_000_000
        self.event_gap_last_ms = gap_ms
        if gap_ms > self.event_gap_max_ms:
            self.event_gap_max_ms = gap_ms
            self.event_gap_max_ts_ms = observed_ts_ms
        if gap_ms > 500:
            self.event_gap_over_500ms_count += 1
            self.event_gap_last_over_500ms_ts_ms = observed_ts_ms

    def observe_critical_lag_state(
        self,
        *,
        observed_monotonic_ns: int,
        observed_ts_ms: int,
    ) -> None:
        """P95 안전 잠금의 시작·복구 시각과 지속시간을 전이마다 한 번 기록한다."""

        if self.critical_lag_active:
            if self._critical_lag_started_monotonic_ns is None:
                self._critical_lag_started_monotonic_ns = observed_monotonic_ns
                self.critical_lag_incident_count += 1
                self.critical_lag_last_started_ts_ms = observed_ts_ms
            return
        started = self._critical_lag_started_monotonic_ns
        if started is None:
            return
        duration_ms = max(0.0, (observed_monotonic_ns - started) / 1_000_000)
        self.critical_lag_last_recovered_ts_ms = observed_ts_ms
        self.critical_lag_last_duration_ms = duration_ms
        self.critical_lag_max_duration_ms = max(
            self.critical_lag_max_duration_ms,
            duration_ms,
        )
        self._critical_lag_started_monotonic_ns = None

    def observe_lag(self, lag_ms: float, *, executable_path: bool) -> None:
        """초기 1·4·16표본과 이후 제한 주기에만 지연 분위수를 갱신한다."""

        if executable_path:
            self.lag_samples_ms.append(lag_ms)
            self._deep_samples_since_refresh += 1
            if lag_ms > self.critical_lag_threshold_ms:
                self.critical_lag_active = True
            warmup_refresh = len(self.lag_samples_ms) in {1, 4, 16}
            if (
                warmup_refresh
                or self._deep_samples_since_refresh >= self.lag_percentile_refresh_samples
            ):
                ordered = sorted(self.lag_samples_ms)
                self._lag_p95_cache_ms = _percentile_95(ordered)
                self._lag_p50_cache_ms = statistics.median(ordered)
                self.critical_lag_active = bool(
                    self._lag_p95_cache_ms is not None
                    and self._lag_p95_cache_ms > self.critical_lag_threshold_ms
                )
                self._deep_samples_since_refresh = 0
                self.lag_percentile_refresh_count += 1
            return
        self.wide_lag_samples_ms.append(lag_ms)
        self._wide_samples_since_refresh += 1
        warmup_refresh = len(self.wide_lag_samples_ms) in {1, 4, 16}
        if (
            warmup_refresh
            or self._wide_samples_since_refresh >= self.lag_percentile_refresh_samples
        ):
            self._wide_lag_p95_cache_ms = _percentile_95(sorted(self.wide_lag_samples_ms))
            self._wide_samples_since_refresh = 0
            self.lag_percentile_refresh_count += 1

    def observe_trade_lag(self, lag_ms: float) -> None:
        """전략 체결흐름 지연을 실제 체결 호가 지연과 분리해 집계한다."""

        self.trade_lag_samples_ms.append(lag_ms)
        self._trade_samples_since_refresh += 1
        warmup_refresh = len(self.trade_lag_samples_ms) in {1, 4, 16}
        if (
            warmup_refresh
            or self._trade_samples_since_refresh >= self.lag_percentile_refresh_samples
        ):
            self._trade_lag_p95_cache_ms = _percentile_95(
                sorted(self.trade_lag_samples_ms)
            )
            self._trade_samples_since_refresh = 0
            self.lag_percentile_refresh_count += 1

    @property
    def lag_p95_ms(self) -> float | None:
        return self._lag_p95_cache_ms

    @property
    def lag_p50_ms(self) -> float | None:
        return self._lag_p50_cache_ms

    @property
    def wide_lag_p95_ms(self) -> float | None:
        return self._wide_lag_p95_cache_ms

    @property
    def trade_lag_p95_ms(self) -> float | None:
        return self._trade_lag_p95_cache_ms

    def as_dict(self) -> dict[str, object]:
        return {
            "connection_state": self.state.value,
            "event_count": self.event_count,
            "reconnects": self.reconnect_count,
            "unplanned_reconnects": max(
                0,
                self.reconnect_count - self.planned_rotation_count,
            ),
            "sequence_gaps": self.gap_count,
            "resyncs": self.resync_count,
            "dropped_events": self.dropped_event_count,
            "queue_depth": self.queue_depth,
            "queue_capacity": self.queue_capacity,
            "lag_p50_ms": self.lag_p50_ms,
            "lag_p95_ms": self.lag_p95_ms,
            "trade_lag_p95_ms": self.trade_lag_p95_ms,
            "wide_lag_p95_ms": self.wide_lag_p95_ms,
            "planned_rotations": self.planned_rotation_count,
            "trade_source_events": self.trade_source_event_count,
            "trade_output_events": self.trade_output_event_count,
            "stale_trade_events": self.stale_trade_event_count,
            "entry_locked": self.entry_locked,
            "critical_lag_threshold_ms": self.critical_lag_threshold_ms,
            "critical_lag_event_count": self.critical_lag_event_count,
            "critical_lag_incident_count": self.critical_lag_incident_count,
            "critical_lag_last_started_ts_ms": self.critical_lag_last_started_ts_ms,
            "critical_lag_last_recovered_ts_ms": self.critical_lag_last_recovered_ts_ms,
            "critical_lag_last_duration_ms": self.critical_lag_last_duration_ms,
            "critical_lag_max_duration_ms": round(self.critical_lag_max_duration_ms, 3),
            "event_gap_last_ms": round(self.event_gap_last_ms, 3),
            "event_gap_max_ms": round(self.event_gap_max_ms, 3),
            "event_gap_max_ts_ms": self.event_gap_max_ts_ms,
            "event_gap_over_500ms_count": self.event_gap_over_500ms_count,
            "event_gap_last_over_500ms_ts_ms": self.event_gap_last_over_500ms_ts_ms,
            "lag_percentile_refresh_count": self.lag_percentile_refresh_count,
            "critical_lag_active": self.critical_lag_active,
            "last_error": self.last_error,
            "venue_clock_offset_ms": self.venue_clock_offset_ms,
            "venue_clock_rtt_ms": self.venue_clock_rtt_ms,
            "clock_sync_status": self.clock_sync_status,
        }


class PersistentPublicSupervisor:
    """첫 검증 이벤트 뒤에도 producer와 consumer를 계속 유지한다."""

    def __init__(
        self,
        provider: PublicStreamProvider,
        *,
        run_id: str,
        clock: Clock,
        sink: EventSink,
        queue_capacity: int = 4_096,
        startup_timeout_seconds: float = 25.0,
        critical_lag_threshold_ms: float = 1_500.0,
        backoff: BackoffPolicy | None = None,
        rng: random.Random | None = None,
        protected_symbols: ProtectedSymbolSource | None = None,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity은 양수여야 합니다.")
        if critical_lag_threshold_ms <= 0:
            raise ValueError("critical lag 기준은 양수여야 합니다.")
        self.provider = provider
        self.run_id = run_id
        self.clock = clock
        self.sink = sink
        self.startup_timeout_seconds = startup_timeout_seconds
        self.backoff = backoff or BackoffPolicy()
        self.rng = rng or random.Random(20260822)
        self.protected_symbols = protected_symbols
        self.telemetry = SupervisorTelemetry(
            queue_capacity=queue_capacity,
            critical_lag_threshold_ms=critical_lag_threshold_ms,
        )
        self.selection: ProviderSelection | None = None
        self._queue: asyncio.Queue[MarketEvent] = asyncio.Queue(maxsize=queue_capacity)
        self._ready = asyncio.Event()
        self._stopping = asyncio.Event()
        self._tasks: tuple[asyncio.Task[None], asyncio.Task[None]] | None = None

    async def start(self) -> ProviderSelection:
        if self._tasks is not None:
            if self.selection is None:
                raise RuntimeError("supervisor 시작 상태가 불완전합니다.")
            return self.selection
        self.telemetry.state = ConnectionState.CONNECTING
        self._update_provider_protection()
        self.selection = await self.provider.prepare(run_id=self.run_id, clock=self.clock)
        self._apply_clock_sync(self.selection)
        for event in self.selection.bootstrap_events:
            await self._deliver(event)
        self.telemetry.started_monotonic_ns = self.clock.monotonic_ns()
        consumer = asyncio.create_task(self._consume(), name=f"paper-consumer-{self.run_id}")
        producer = asyncio.create_task(self._produce(), name=f"public-producer-{self.run_id}")
        self._tasks = (producer, consumer)
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.startup_timeout_seconds)
        except TimeoutError as error:
            await self.stop()
            raise PublicDataUnavailable(
                f"{self.provider.venue.value} 지속 WebSocket 검증 이벤트 시간초과"
            ) from error
        return self.selection

    async def stop(self) -> None:
        self._stopping.set()
        tasks = self._tasks
        self._tasks = None
        if tasks is not None:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.telemetry.state = ConnectionState.DISCONNECTED
        self.telemetry.entry_locked = True

    async def _produce(self) -> None:
        attempt = 0
        while not self._stopping.is_set():
            if self.selection is None:
                return
            try:
                if attempt > 0:
                    self._update_provider_protection()
                    self.selection = await self.provider.prepare(
                        run_id=self.run_id,
                        clock=self.clock,
                    )
                    self._apply_clock_sync(self.selection)
                self.telemetry.state = (
                    ConnectionState.CONNECTING if attempt == 0 else ConnectionState.RECONNECTING
                )
                async for event in self.provider.events(
                    self.selection,
                    run_id=self.run_id,
                    clock=self.clock,
                ):
                    if self._stopping.is_set():
                        return
                    attempt = 0
                    self._apply_clock_sync(self.selection)
                    self._observe(event)
                    self._enqueue(event)
                if not self._stopping.is_set():
                    self.telemetry.planned_rotation_count += 1
                    self.telemetry.state = ConnectionState.RECONNECTING
                    self.telemetry.entry_locked = True
                    self._update_provider_protection()
                    self.selection = await self.provider.prepare(
                        run_id=self.run_id,
                        clock=self.clock,
                    )
                    self._apply_clock_sync(self.selection)
                    self.telemetry.reconnect_count += 1
                    # 즉시 정상 종료하는 공급자가 event loop를 독점하지 않게 한다.
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise
            except (
                OSError,
                TimeoutError,
                ValueError,
                KeyError,
                SequenceGap,
                PublicDataUnavailable,
                httpx.HTTPError,
                WebSocketException,
            ) as error:
                self.telemetry.last_error = f"{type(error).__name__}: {error}"
                self.telemetry.reconnect_count += 1
                self.telemetry.state = ConnectionState.RECONNECTING
                self.telemetry.entry_locked = True
                delay = self.backoff.delay_ms(attempt, self.rng) / 1_000
                attempt += 1
                await asyncio.sleep(delay)

    def _apply_clock_sync(self, selection: ProviderSelection) -> None:
        self.telemetry.venue_clock_offset_ms = selection.current_venue_clock_offset_ms(self.clock)
        self.telemetry.venue_clock_rtt_ms = selection.venue_clock_rtt_ms
        self.telemetry.clock_sync_status = selection.clock_sync_status

    def _update_provider_protection(self) -> None:
        if self.protected_symbols is None:
            return
        updater = getattr(self.provider, "update_pinned_symbols", None)
        if callable(updater):
            updater(tuple(dict.fromkeys(self.protected_symbols())))

    def _observe(self, event: MarketEvent) -> None:
        self.telemetry.event_count += 1
        if event.event_type == "TRADE":
            self.telemetry.trade_source_event_count += int(
                event.data.get("source_event_count", 1)
            )
            self.telemetry.trade_output_event_count += 1
        observed_ts_ms = self.clock.utc_ms()
        self.telemetry.observe_event_gap(event.receive_monotonic_ns, observed_ts_ms)
        self.telemetry.last_event_monotonic_ns = event.receive_monotonic_ns
        if event.quality.lag_ms is not None:
            executable_path = event.event_type in {"DEPTH_UPDATE", "ORDERBOOK"}
            if event.event_type == "TRADE":
                self.telemetry.observe_trade_lag(event.quality.lag_ms)
                if event.quality.is_stale:
                    self.telemetry.stale_trade_event_count += 1
            else:
                self.telemetry.observe_lag(
                    event.quality.lag_ms,
                    executable_path=executable_path,
                )
            if executable_path:
                self.telemetry.observe_critical_lag_state(
                    observed_monotonic_ns=event.receive_monotonic_ns,
                    observed_ts_ms=observed_ts_ms,
                )
                if event.quality.lag_ms > self.telemetry.critical_lag_threshold_ms:
                    self.telemetry.critical_lag_event_count += 1
        flags = set(event.quality.flags)
        if "SEQUENCE_GAP" in flags:
            self.telemetry.gap_count += 1
        if "BOOK_RESYNC" in flags:
            self.telemetry.resync_count += 1
        lag_p95_ms = self.telemetry.lag_p95_ms
        current_critical_lag = self.telemetry.critical_lag_active
        if (
            event.event_type in {"DEPTH_UPDATE", "ORDERBOOK"}
            and event.quality.is_live
            and event.quality.sequence_valid
            and not event.quality.is_stale
        ):
            self.telemetry.last_error = None
            self.telemetry.state = ConnectionState.LIVE
            self.telemetry.entry_locked = bool(
                current_critical_lag
                or (
                    lag_p95_ms is not None and lag_p95_ms > self.telemetry.critical_lag_threshold_ms
                )
            )
            self._ready.set()
        elif current_critical_lag or (
            lag_p95_ms is not None and lag_p95_ms > self.telemetry.critical_lag_threshold_ms
        ):
            self.telemetry.entry_locked = True

    def _enqueue(self, event: MarketEvent) -> None:
        if self._queue.full():
            self.telemetry.dropped_event_count += 1
            self.telemetry.entry_locked = True
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(event)
        self.telemetry.queue_depth = self._queue.qsize()

    async def _consume(self) -> None:
        while not self._stopping.is_set():
            try:
                event = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._deliver(event)
            finally:
                self._queue.task_done()
                self.telemetry.queue_depth = self._queue.qsize()

    async def _deliver(self, event: MarketEvent) -> None:
        outcome = self.sink(event)
        if inspect.isawaitable(outcome):
            await outcome


class BinancePersistentProvider:
    """wide 1초 ticker와 deep depth·trade를 독립 연결로 감독한다."""

    venue = Venue.BINANCE_USDM

    def __init__(
        self,
        *,
        wide_max: int = 50,
        deep_max: int = 20,
        planned_rotation_seconds: float = 15 * 60,
        pinned_symbols: tuple[str, ...] = (),
    ) -> None:
        if not 10 <= deep_max <= 30:
            raise ValueError("deep_max는 10..30 범위여야 합니다.")
        self.wide_max = wide_max
        self.deep_max = deep_max
        self.planned_rotation_seconds = planned_rotation_seconds
        self.pinned_symbols = tuple(dict.fromkeys(pinned_symbols))
        self._previous_deep: tuple[str, ...] = ()
        self._deep_since_ms: dict[str, int] = {}

    def update_pinned_symbols(self, symbols: Sequence[str]) -> None:
        self.pinned_symbols = tuple(dict.fromkeys(symbols))

    async def prepare(self, *, run_id: str, clock: Clock) -> ProviderSelection:
        try:
            async with BinancePublicAdapter() as adapter:
                instruments, tickers = await asyncio.gather(
                    adapter.fetch_instruments(), adapter.fetch_tickers()
                )
                clock_calibration = await estimate_venue_clock_calibration(
                    adapter.fetch_server_time_ms,
                    clock.utc_ms,
                    clock.monotonic_ns,
                )
        except (OSError, httpx.HTTPError, ValueError) as error:
            raise PublicDataUnavailable(f"BINANCE_USDM prepare 실패: {error}") from error
        eligible = _eligible_tickers(instruments, tickers)
        if not eligible:
            raise PublicDataUnavailable("BINANCE_USDM 유효 종목이 없습니다.")
        wide, deep = _wide_and_deep(
            tuple(eligible),
            self.wide_max,
            self.deep_max,
            pinned_symbols=self.pinned_symbols,
        )
        deep = _safe_rotate_deep(
            self._previous_deep,
            deep,
            ranked_symbols=wide,
            now_ms=clock.utc_ms(),
            since_ms=self._deep_since_ms,
            pinned_symbols=self.pinned_symbols,
        )
        self._previous_deep = deep
        by_symbol = {item.symbol: item for item in instruments if item.symbol in set(wide)}
        return ProviderSelection(
            venue=self.venue,
            instruments=by_symbol,
            tickers={symbol: eligible[symbol] for symbol in wide},
            wide_symbols=wide,
            deep_symbols=deep,
            bootstrap_events=_ticker_events(
                self.venue,
                eligible,
                run_id=run_id,
                clock=clock,
                maximum=self.wide_max,
            ),
            venue_clock_offset_ms=clock_calibration.measured_offset_ms,
            venue_clock_rtt_ms=clock_calibration.round_trip_ms,
            clock_sync_status="SYNCED",
            venue_clock_calibration=clock_calibration,
        )

    async def events(
        self,
        selection: ProviderSelection,
        *,
        run_id: str,
        clock: Clock,
    ) -> AsyncIterator[MarketEvent]:
        router = BinanceStreamRouter(maximum_streams_per_connection=100)
        wide_url = router.urls(f"{symbol}@ticker" for symbol in selection.wide_symbols)[0]
        depth_url = router.urls(f"{symbol}@depth" for symbol in selection.deep_symbols)[0]
        trade_url = router.urls(f"{symbol}@aggTrade" for symbol in selection.deep_symbols)[0]
        started = asyncio.get_running_loop().time()
        trade_coalescer = BinanceTradeCoalescer(bucket_ms=250)
        async with (
            websockets.connect(
                wide_url,
                max_size=2_000_000,
                max_queue=512,
                ping_interval=20,
                close_timeout=1,
                additional_headers=None,
            ) as wide_socket,
            websockets.connect(
                depth_url,
                max_size=2_000_000,
                max_queue=512,
                ping_interval=20,
                close_timeout=1,
                additional_headers=None,
            ) as depth_socket,
            websockets.connect(
                trade_url,
                max_size=2_000_000,
                max_queue=512,
                ping_interval=20,
                close_timeout=1,
                additional_headers=None,
            ) as trade_socket,
            BinancePublicAdapter() as adapter,
        ):
            snapshot_values = await asyncio.gather(
                *(adapter.fetch_depth(symbol, limit=1000) for symbol in selection.deep_symbols)
            )
            books: dict[str, BinanceOrderBook] = {}
            for symbol, snapshot in zip(selection.deep_symbols, snapshot_values, strict=True):
                book = BinanceOrderBook()
                book.reset_snapshot(
                    int(snapshot["lastUpdateId"]), snapshot["bids"], snapshot["asks"]
                )
                books[symbol] = book
            sockets: dict[str, Any] = {
                "wide": wide_socket,
                "depth": depth_socket,
                "trade": trade_socket,
            }
            pending: dict[asyncio.Task[str | bytes], str] = {
                asyncio.create_task(socket.recv()): name for name, socket in sockets.items()
            }
            try:
                while asyncio.get_running_loop().time() - started < self.planned_rotation_seconds:
                    done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        family = pending.pop(task)
                        payload = _json_object(task.result())
                        pending[asyncio.create_task(sockets[family].recv())] = family
                        async for event in self._normalize(
                            payload,
                            selection,
                            books,
                            adapter,
                            run_id=run_id,
                            clock=clock,
                        ):
                            if event.event_type == "TRADE":
                                for aggregate in trade_coalescer.push(event):
                                    yield aggregate
                            else:
                                yield event
                for aggregate in trade_coalescer.flush():
                    yield aggregate
            finally:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

    async def _normalize(
        self,
        payload: dict[str, Any],
        selection: ProviderSelection,
        books: dict[str, BinanceOrderBook],
        adapter: BinancePublicAdapter,
        *,
        run_id: str,
        clock: Clock,
    ) -> AsyncIterator[MarketEvent]:
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            return
        event_name = str(data.get("e", ""))
        symbol = str(data.get("s", ""))
        if symbol not in selection.wide_symbols:
            return
        venue_ts_ms = int(data.get("E") or data.get("T") or clock.utc_ms())
        quality = DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=selection.venue_lag_ms(clock=clock, venue_ts_ms=venue_ts_ms),
        )
        if event_name == "24hrTicker":
            receive_monotonic_ns = clock.monotonic_ns()
            yield MarketEvent(
                event_id=(f"binance-wide-{symbol}-{venue_ts_ms}-{receive_monotonic_ns}"),
                run_id=run_id,
                venue=self.venue,
                symbol=symbol,
                event_type="WIDE_TICKER",
                venue_ts_ms=venue_ts_ms,
                receive_monotonic_ns=receive_monotonic_ns,
                quality=quality,
                data={
                    "last_price": str(data["c"]),
                    "base_volume_24h": str(data["v"]),
                    "quote_volume_24h": str(data["q"]),
                    "trade_count_24h": int(data.get("n", 0)),
                },
            )
            return
        if event_name == "bookTicker":
            yield MarketEvent(
                event_id=f"binance-book-{symbol}-{data.get('u', venue_ts_ms)}",
                run_id=run_id,
                venue=self.venue,
                symbol=symbol,
                event_type="BOOK_TICKER",
                venue_ts_ms=venue_ts_ms,
                receive_monotonic_ns=clock.monotonic_ns(),
                sequence_end=int(data["u"]) if data.get("u") is not None else None,
                quality=quality,
                data={
                    "bid": str(data["b"]),
                    "bid_qty": str(data["B"]),
                    "ask": str(data["a"]),
                    "ask_qty": str(data["A"]),
                },
            )
            return
        if event_name == "aggTrade" and symbol in selection.deep_symbols:
            trade_stale = bool(
                quality.lag_ms is not None
                and quality.lag_ms > _STRATEGY_TRADE_LAG_MAX_MS
            )
            yield MarketEvent(
                event_id=f"binance-trade-{symbol}-{data['a']}",
                run_id=run_id,
                venue=self.venue,
                symbol=symbol,
                event_type="TRADE",
                venue_ts_ms=venue_ts_ms,
                transaction_ts_ms=int(data["T"]),
                receive_monotonic_ns=clock.monotonic_ns(),
                sequence_end=int(data["a"]),
                quality=DataQuality(
                    is_live=True,
                    is_stale=trade_stale,
                    sequence_valid=True,
                    lag_ms=quality.lag_ms,
                    flags=("TRADE_LAG_STALE",) if trade_stale else (),
                ),
                data={
                    "price": str(data["p"]),
                    "quantity": str(data["q"]),
                    "buyer_is_aggressor": not bool(data["m"]),
                },
            )
            return
        if event_name != "depthUpdate" or symbol not in books:
            return
        book = books[symbol]
        try:
            applied = book.apply_delta(
                int(data["U"]),
                int(data["u"]),
                int(data["pu"]) if data.get("pu") is not None else None,
                data["b"],
                data["a"],
            )
        except SequenceGap:
            snapshot = await adapter.fetch_depth(symbol, limit=1000)
            book.reset_snapshot(int(snapshot["lastUpdateId"]), snapshot["bids"], snapshot["asks"])
            yield MarketEvent(
                event_id=f"binance-resync-{symbol}-{venue_ts_ms}",
                run_id=run_id,
                venue=self.venue,
                symbol=symbol,
                event_type="HEALTH",
                venue_ts_ms=venue_ts_ms,
                receive_monotonic_ns=clock.monotonic_ns(),
                quality=DataQuality(
                    is_live=True,
                    is_stale=True,
                    sequence_valid=False,
                    lag_ms=quality.lag_ms,
                    flags=("SEQUENCE_GAP", "BOOK_RESYNC"),
                ),
                data={"reason": "BINANCE_PU_GAP"},
            )
            return
        if not applied:
            return
        bids, asks = book.top(20)
        yield MarketEvent(
            event_id=f"binance-depth-{symbol}-{data['u']}",
            run_id=run_id,
            venue=self.venue,
            symbol=symbol,
            event_type="DEPTH_UPDATE",
            venue_ts_ms=venue_ts_ms,
            transaction_ts_ms=int(data["T"]) if data.get("T") is not None else None,
            receive_monotonic_ns=clock.monotonic_ns(),
            sequence_start=int(data["U"]),
            sequence_end=int(data["u"]),
            previous_sequence_end=int(data["pu"]) if data.get("pu") is not None else None,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=book.sequence_valid,
                lag_ms=quality.lag_ms,
            ),
            data={
                "bid": str(bids[0][0]),
                "bid_qty": str(bids[0][1]),
                "ask": str(asks[0][0]),
                "ask_qty": str(asks[0][1]),
                "bids": [[str(price), str(quantity)] for price, quantity in bids],
                "asks": [[str(price), str(quantity)] for price, quantity in asks],
            },
        )


@dataclass(slots=True)
class _TradeAggregate:
    """같은 종목·방향의 짧은 공개 체결 묶음을 손실 없이 합산한다."""

    first: MarketEvent
    last: MarketEvent
    quantity: Decimal
    notional: Decimal
    source_event_count: int = 1


class BinanceTradeCoalescer:
    """고빈도 aggTrade를 짧은 VWAP 묶음으로 줄여 깊은 호가 수신을 보호한다."""

    def __init__(self, *, bucket_ms: int = 100) -> None:
        if bucket_ms <= 0:
            raise ValueError("trade bucket은 양수여야 합니다.")
        self.bucket_ms = bucket_ms
        self._buckets: dict[tuple[str, bool, int], _TradeAggregate] = {}
        self._latest_bucket = -1

    def push(self, event: MarketEvent) -> tuple[MarketEvent, ...]:
        if event.event_type != "TRADE":
            raise ValueError("TRADE 이벤트만 합산할 수 있습니다.")
        buyer_is_aggressor = bool(event.data["buyer_is_aggressor"])
        timestamp = int(event.transaction_ts_ms or event.venue_ts_ms)
        bucket = timestamp // self.bucket_ms
        completed: tuple[MarketEvent, ...] = ()
        if bucket > self._latest_bucket:
            completed = self._flush_before(bucket)
            self._latest_bucket = bucket
        key = (event.symbol, buyer_is_aggressor, bucket)
        quantity = Decimal(str(event.data["quantity"]))
        notional = Decimal(str(event.data["price"])) * quantity
        aggregate = self._buckets.get(key)
        if aggregate is None:
            self._buckets[key] = _TradeAggregate(
                first=event,
                last=event,
                quantity=quantity,
                notional=notional,
            )
        else:
            aggregate.last = event
            aggregate.quantity += quantity
            aggregate.notional += notional
            aggregate.source_event_count += 1
        return completed

    def flush(self) -> tuple[MarketEvent, ...]:
        return self._flush_before(self._latest_bucket + 1)

    def _flush_before(self, bucket: int) -> tuple[MarketEvent, ...]:
        ready_keys = [key for key in self._buckets if key[2] < bucket]
        ready = [self._buckets.pop(key) for key in ready_keys]
        ready.sort(
            key=lambda aggregate: (
                int(aggregate.last.transaction_ts_ms or aggregate.last.venue_ts_ms),
                aggregate.last.receive_monotonic_ns,
                aggregate.last.symbol,
            )
        )
        events = tuple(self._event(aggregate) for aggregate in ready)
        return events

    @staticmethod
    def _event(aggregate: _TradeAggregate) -> MarketEvent:
        first = aggregate.first
        last = aggregate.last
        first_sequence = first.sequence_end
        last_sequence = last.sequence_end
        return MarketEvent(
            event_id=(
                f"binance-trade-bucket-{last.symbol}-"
                f"{first_sequence or first.venue_ts_ms}-{last_sequence or last.venue_ts_ms}"
            ),
            run_id=last.run_id,
            venue=last.venue,
            symbol=last.symbol,
            event_type="TRADE",
            venue_ts_ms=last.venue_ts_ms,
            transaction_ts_ms=last.transaction_ts_ms,
            receive_monotonic_ns=last.receive_monotonic_ns,
            sequence_start=first_sequence,
            sequence_end=last_sequence,
            quality=last.quality,
            data={
                "price": str(aggregate.notional / aggregate.quantity),
                "quantity": str(aggregate.quantity),
                "buyer_is_aggressor": bool(last.data["buyer_is_aggressor"]),
                "source_event_count": aggregate.source_event_count,
            },
        )


class BybitPersistentProvider:
    """Bybit public linear를 인증 없이 별도 Run의 fallback으로 유지한다."""

    venue = Venue.BYBIT_LINEAR

    def __init__(
        self,
        *,
        wide_max: int = 50,
        deep_max: int = 20,
        planned_rotation_seconds: float = 15 * 60,
        pinned_symbols: tuple[str, ...] = (),
    ) -> None:
        if not 10 <= deep_max <= 30:
            raise ValueError("deep_max는 10..30 범위여야 합니다.")
        self.wide_max = wide_max
        self.deep_max = deep_max
        self.planned_rotation_seconds = planned_rotation_seconds
        self.pinned_symbols = tuple(dict.fromkeys(pinned_symbols))
        self._previous_deep: tuple[str, ...] = ()
        self._deep_since_ms: dict[str, int] = {}

    def update_pinned_symbols(self, symbols: Sequence[str]) -> None:
        self.pinned_symbols = tuple(dict.fromkeys(symbols))

    async def prepare(self, *, run_id: str, clock: Clock) -> ProviderSelection:
        try:
            async with BybitPublicAdapter() as adapter:
                instruments, tickers = await asyncio.gather(
                    adapter.fetch_instruments(), adapter.fetch_tickers()
                )
                clock_calibration = await estimate_venue_clock_calibration(
                    adapter.fetch_server_time_ms,
                    clock.utc_ms,
                    clock.monotonic_ns,
                )
        except (OSError, httpx.HTTPError, ValueError) as error:
            raise PublicDataUnavailable(f"BYBIT_LINEAR prepare 실패: {error}") from error
        eligible = _eligible_tickers(instruments, tickers)
        if not eligible:
            raise PublicDataUnavailable("BYBIT_LINEAR 유효 종목이 없습니다.")
        wide, deep = _wide_and_deep(
            tuple(eligible),
            self.wide_max,
            self.deep_max,
            pinned_symbols=self.pinned_symbols,
        )
        deep = _safe_rotate_deep(
            self._previous_deep,
            deep,
            ranked_symbols=wide,
            now_ms=clock.utc_ms(),
            since_ms=self._deep_since_ms,
            pinned_symbols=self.pinned_symbols,
        )
        self._previous_deep = deep
        return ProviderSelection(
            venue=self.venue,
            instruments={item.symbol: item for item in instruments if item.symbol in set(wide)},
            tickers={symbol: eligible[symbol] for symbol in wide},
            wide_symbols=wide,
            deep_symbols=deep,
            bootstrap_events=_ticker_events(
                self.venue,
                eligible,
                run_id=run_id,
                clock=clock,
                maximum=self.wide_max,
            ),
            venue_clock_offset_ms=clock_calibration.measured_offset_ms,
            venue_clock_rtt_ms=clock_calibration.round_trip_ms,
            clock_sync_status="SYNCED",
            venue_clock_calibration=clock_calibration,
        )

    async def events(
        self,
        selection: ProviderSelection,
        *,
        run_id: str,
        clock: Clock,
    ) -> AsyncIterator[MarketEvent]:
        topics = [*(f"tickers.{symbol}" for symbol in selection.wide_symbols)]
        topics.extend(f"orderbook.50.{symbol}" for symbol in selection.deep_symbols)
        topics.extend(f"publicTrade.{symbol}" for symbol in selection.deep_symbols)
        books = {symbol: BybitOrderBook() for symbol in selection.deep_symbols}
        started = asyncio.get_running_loop().time()
        async with websockets.connect(
            PUBLIC_LINEAR_WS,
            max_size=2_000_000,
            max_queue=2_048,
            ping_interval=None,
            close_timeout=1,
            additional_headers=None,
        ) as socket:
            await socket.send(json.dumps({"op": "subscribe", "args": topics}))
            while asyncio.get_running_loop().time() - started < self.planned_rotation_seconds:
                try:
                    async with asyncio.timeout(20):
                        payload = _json_object(await socket.recv())
                except TimeoutError:
                    await socket.send(json.dumps({"op": "ping"}))
                    continue
                if payload.get("success") is True or payload.get("op") in {"pong", "ping"}:
                    continue
                topic = str(payload.get("topic", ""))
                symbol = topic.rsplit(".", maxsplit=1)[-1]
                venue_ts_ms = int(payload.get("ts") or clock.utc_ms())
                quality = DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=selection.venue_lag_ms(
                        clock=clock,
                        venue_ts_ms=venue_ts_ms,
                    ),
                )
                data = payload.get("data")
                if topic.startswith("tickers.") and isinstance(data, dict):
                    if data.get("bid1Price") and data.get("ask1Price"):
                        yield MarketEvent(
                            event_id=f"bybit-ticker-{symbol}-{venue_ts_ms}",
                            run_id=run_id,
                            venue=self.venue,
                            symbol=symbol,
                            event_type="BOOK_TICKER",
                            venue_ts_ms=venue_ts_ms,
                            receive_monotonic_ns=clock.monotonic_ns(),
                            quality=quality,
                            data={
                                "bid": str(data["bid1Price"]),
                                "bid_qty": str(data.get("bid1Size", "0")),
                                "ask": str(data["ask1Price"]),
                                "ask_qty": str(data.get("ask1Size", "0")),
                            },
                        )
                    continue
                if topic.startswith("publicTrade.") and isinstance(data, list):
                    for trade in data:
                        if not isinstance(trade, dict):
                            continue
                        yield MarketEvent(
                            event_id=f"bybit-trade-{symbol}-{trade.get('i', trade['T'])}",
                            run_id=run_id,
                            venue=self.venue,
                            symbol=symbol,
                            event_type="TRADE",
                            venue_ts_ms=int(trade["T"]),
                            transaction_ts_ms=int(trade["T"]),
                            receive_monotonic_ns=clock.monotonic_ns(),
                            quality=quality,
                            data={
                                "price": str(trade["p"]),
                                "quantity": str(trade["v"]),
                                "buyer_is_aggressor": str(trade["S"]) == "Buy",
                            },
                        )
                    continue
                if not topic.startswith("orderbook.") or not isinstance(data, dict):
                    continue
                book = books[symbol]
                message_type = str(payload.get("type"))
                book.apply(
                    message_type,
                    int(data["u"]),
                    int(data["seq"]),
                    data["b"],
                    data["a"],
                )
                bids, asks = book.top(20)
                yield MarketEvent(
                    event_id=f"bybit-depth-{symbol}-{data['u']}",
                    run_id=run_id,
                    venue=self.venue,
                    symbol=symbol,
                    event_type="ORDERBOOK",
                    venue_ts_ms=venue_ts_ms,
                    transaction_ts_ms=int(data["cts"]) if data.get("cts") else None,
                    receive_monotonic_ns=clock.monotonic_ns(),
                    sequence_start=int(data["u"]),
                    sequence_end=int(data["u"]),
                    quality=quality,
                    data={
                        "bid": str(bids[0][0]),
                        "bid_qty": str(bids[0][1]),
                        "ask": str(asks[0][0]),
                        "ask_qty": str(asks[0][1]),
                        "bids": [[str(price), str(quantity)] for price, quantity in bids],
                        "asks": [[str(price), str(quantity)] for price, quantity in asks],
                    },
                )


def _wide_and_deep(
    ranked_symbols: Sequence[str],
    wide_max: int,
    deep_max: int,
    *,
    pinned_symbols: Sequence[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if wide_max < deep_max:
        raise ValueError("wide_max는 deep_max 이상이어야 합니다.")
    wide_values = list(ranked_symbols[:wide_max])
    for priority in reversed(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
        if priority in ranked_symbols and priority not in wide_values:
            wide_values.insert(0, priority)
            del wide_values[wide_max:]
    deep_values: list[str] = []
    for symbol in pinned_symbols:
        if symbol not in ranked_symbols:
            raise PublicDataUnavailable(f"복구 PAPER 종목이 공개 유니버스에 없습니다: {symbol}")
        if symbol not in wide_values:
            wide_values.insert(0, symbol)
            del wide_values[wide_max:]
        if symbol not in deep_values:
            deep_values.append(symbol)
    for priority in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        if priority in wide_values and priority not in deep_values:
            deep_values.append(priority)
    deep_values.extend(symbol for symbol in wide_values if symbol not in deep_values)
    return tuple(wide_values), tuple(deep_values[:deep_max])


def _safe_rotate_deep(
    previous: Sequence[str],
    proposed: Sequence[str],
    *,
    ranked_symbols: Sequence[str],
    now_ms: int,
    since_ms: dict[str, int],
    pinned_symbols: Sequence[str] = (),
    minimum_residency_ms: int = 30 * 60 * 1_000,
    replacement_limit: int = 4,
) -> tuple[str, ...]:
    """진행·고정 종목과 최소 체류시간을 지키며 한 번에 네 종목만 교체한다."""

    target_size = len(proposed)
    if not previous:
        result = tuple(proposed)
        since_ms.update({symbol: now_ms for symbol in result})
        return result
    ranked = tuple(dict.fromkeys(ranked_symbols))
    protected = set(pinned_symbols)
    retained = [
        symbol
        for symbol in previous
        if symbol in ranked
        and (symbol in protected or now_ms - since_ms.get(symbol, now_ms) < minimum_residency_ms)
    ]
    removable = [
        symbol for symbol in reversed(previous) if symbol in ranked and symbol not in retained
    ]
    desired_new = [
        symbol for symbol in proposed if symbol not in previous and symbol not in retained
    ][:replacement_limit]
    keep = [symbol for symbol in previous if symbol in ranked]
    for symbol in desired_new:
        if not removable:
            break
        removed = removable.pop(0)
        keep.remove(removed)
        since_ms.pop(removed, None)
        keep.append(symbol)
        since_ms[symbol] = now_ms
    for symbol in ranked:
        if len(keep) >= target_size:
            break
        if symbol not in keep:
            keep.append(symbol)
            since_ms[symbol] = now_ms
    result = tuple(sorted(keep[:target_size], key=ranked.index))
    since_ms.update({symbol: since_ms.get(symbol, now_ms) for symbol in result})
    return result


def _json_object(payload: str | bytes) -> dict[str, Any]:
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("WebSocket payload는 JSON 객체여야 합니다.")
    return decoded
