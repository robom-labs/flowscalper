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

EventSink = Callable[[MarketEvent], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    venue: Venue
    instruments: dict[str, Instrument]
    tickers: dict[str, Ticker]
    wide_symbols: tuple[str, ...]
    deep_symbols: tuple[str, ...]
    bootstrap_events: tuple[MarketEvent, ...]


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
    entry_locked: bool = True
    lag_samples_ms: deque[float] = field(default_factory=lambda: deque(maxlen=2_000))

    @property
    def lag_p95_ms(self) -> float | None:
        if not self.lag_samples_ms:
            return None
        ordered = sorted(self.lag_samples_ms)
        index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * 0.95)))
        return ordered[index]

    @property
    def lag_p50_ms(self) -> float | None:
        return statistics.median(self.lag_samples_ms) if self.lag_samples_ms else None

    def as_dict(self) -> dict[str, object]:
        return {
            "connection_state": self.state.value,
            "event_count": self.event_count,
            "reconnects": self.reconnect_count,
            "sequence_gaps": self.gap_count,
            "resyncs": self.resync_count,
            "dropped_events": self.dropped_event_count,
            "queue_depth": self.queue_depth,
            "queue_capacity": self.queue_capacity,
            "lag_p50_ms": self.lag_p50_ms,
            "lag_p95_ms": self.lag_p95_ms,
            "planned_rotations": self.planned_rotation_count,
            "entry_locked": self.entry_locked,
            "last_error": self.last_error,
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
        backoff: BackoffPolicy | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity은 양수여야 합니다.")
        self.provider = provider
        self.run_id = run_id
        self.clock = clock
        self.sink = sink
        self.startup_timeout_seconds = startup_timeout_seconds
        self.backoff = backoff or BackoffPolicy()
        self.rng = rng or random.Random(20260822)
        self.telemetry = SupervisorTelemetry(queue_capacity=queue_capacity)
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
        self.selection = await self.provider.prepare(run_id=self.run_id, clock=self.clock)
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
                self.telemetry.state = (
                    ConnectionState.CONNECTING
                    if attempt == 0
                    else ConnectionState.RECONNECTING
                )
                async for event in self.provider.events(
                    self.selection,
                    run_id=self.run_id,
                    clock=self.clock,
                ):
                    if self._stopping.is_set():
                        return
                    attempt = 0
                    self._observe(event)
                    self._enqueue(event)
                if not self._stopping.is_set():
                    self.telemetry.planned_rotation_count += 1
                    raise ConnectionError("planned public WebSocket rotation")
            except asyncio.CancelledError:
                raise
            except (
                OSError,
                TimeoutError,
                ValueError,
                KeyError,
                SequenceGap,
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

    def _observe(self, event: MarketEvent) -> None:
        self.telemetry.event_count += 1
        self.telemetry.last_event_monotonic_ns = event.receive_monotonic_ns
        if event.quality.lag_ms is not None:
            self.telemetry.lag_samples_ms.append(event.quality.lag_ms)
        flags = set(event.quality.flags)
        if "SEQUENCE_GAP" in flags:
            self.telemetry.gap_count += 1
        if "BOOK_RESYNC" in flags:
            self.telemetry.resync_count += 1
        if (
            event.event_type in {"DEPTH_UPDATE", "ORDERBOOK"}
            and event.quality.is_live
            and event.quality.sequence_valid
            and not event.quality.is_stale
        ):
            self.telemetry.state = ConnectionState.LIVE
            self.telemetry.entry_locked = False
            self._ready.set()

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
    """Binance public·market 경로를 분리하고 50 wide·10 deep을 유지한다."""

    venue = Venue.BINANCE_USDM

    def __init__(
        self,
        *,
        wide_max: int = 50,
        deep_max: int = 10,
        planned_rotation_seconds: float = 23 * 60 * 60,
    ) -> None:
        if not 8 <= deep_max <= 12:
            raise ValueError("deep_max는 8..12 범위여야 합니다.")
        self.wide_max = wide_max
        self.deep_max = deep_max
        self.planned_rotation_seconds = planned_rotation_seconds

    async def prepare(self, *, run_id: str, clock: Clock) -> ProviderSelection:
        try:
            async with BinancePublicAdapter() as adapter:
                instruments, tickers = await asyncio.gather(
                    adapter.fetch_instruments(), adapter.fetch_tickers()
                )
        except (OSError, httpx.HTTPError, ValueError) as error:
            raise PublicDataUnavailable(f"BINANCE_USDM prepare 실패: {error}") from error
        eligible = _eligible_tickers(instruments, tickers)
        if not eligible:
            raise PublicDataUnavailable("BINANCE_USDM 유효 종목이 없습니다.")
        wide, deep = _wide_and_deep(tuple(eligible), self.wide_max, self.deep_max)
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
        )

    async def events(
        self,
        selection: ProviderSelection,
        *,
        run_id: str,
        clock: Clock,
    ) -> AsyncIterator[MarketEvent]:
        router = BinanceStreamRouter(maximum_streams_per_connection=100)
        public_url = router.urls(
            (
                *(f"{symbol}@bookTicker" for symbol in selection.wide_symbols),
                *(f"{symbol}@depth@100ms" for symbol in selection.deep_symbols),
            )
        )[0]
        market_url = router.urls(
            f"{symbol}@aggTrade" for symbol in selection.deep_symbols
        )[0]
        started = asyncio.get_running_loop().time()
        async with websockets.connect(
            public_url,
            max_size=2_000_000,
            max_queue=2_048,
            ping_interval=20,
            additional_headers=None,
        ) as public_socket, websockets.connect(
            market_url,
            max_size=2_000_000,
            max_queue=2_048,
            ping_interval=20,
            additional_headers=None,
        ) as market_socket, BinancePublicAdapter() as adapter:
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
            sockets: dict[str, Any] = {"public": public_socket, "market": market_socket}
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
                            yield event
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
            lag_ms=max(0.0, float(clock.utc_ms() - venue_ts_ms)),
        )
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
                quality=quality,
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
            book.reset_snapshot(
                int(snapshot["lastUpdateId"]), snapshot["bids"], snapshot["asks"]
            )
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


class BybitPersistentProvider:
    """Bybit public linear를 인증 없이 별도 Run의 fallback으로 유지한다."""

    venue = Venue.BYBIT_LINEAR

    def __init__(
        self,
        *,
        wide_max: int = 50,
        deep_max: int = 10,
        planned_rotation_seconds: float = 23 * 60 * 60,
    ) -> None:
        if not 8 <= deep_max <= 12:
            raise ValueError("deep_max는 8..12 범위여야 합니다.")
        self.wide_max = wide_max
        self.deep_max = deep_max
        self.planned_rotation_seconds = planned_rotation_seconds

    async def prepare(self, *, run_id: str, clock: Clock) -> ProviderSelection:
        try:
            async with BybitPublicAdapter() as adapter:
                instruments, tickers = await asyncio.gather(
                    adapter.fetch_instruments(), adapter.fetch_tickers()
                )
        except (OSError, httpx.HTTPError, ValueError) as error:
            raise PublicDataUnavailable(f"BYBIT_LINEAR prepare 실패: {error}") from error
        eligible = _eligible_tickers(instruments, tickers)
        if not eligible:
            raise PublicDataUnavailable("BYBIT_LINEAR 유효 종목이 없습니다.")
        wide, deep = _wide_and_deep(tuple(eligible), self.wide_max, self.deep_max)
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
                    lag_ms=max(0.0, float(clock.utc_ms() - venue_ts_ms)),
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
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if wide_max < deep_max:
        raise ValueError("wide_max는 deep_max 이상이어야 합니다.")
    wide_values = list(ranked_symbols[:wide_max])
    for priority in reversed(("BTCUSDT", "ETHUSDT", "SOLUSDT")):
        if priority in ranked_symbols and priority not in wide_values:
            wide_values.insert(0, priority)
            del wide_values[wide_max:]
    deep_values: list[str] = []
    for priority in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        if priority in wide_values:
            deep_values.append(priority)
    deep_values.extend(symbol for symbol in wide_values if symbol not in deep_values)
    return tuple(wide_values), tuple(deep_values[:deep_max])


def _json_object(payload: str | bytes) -> dict[str, Any]:
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("WebSocket payload는 JSON 객체여야 합니다.")
    return decoded
