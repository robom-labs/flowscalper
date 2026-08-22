"""자격 증명 없는 REST·WebSocket을 검증한 뒤에만 LIVE 이벤트를 만든다."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import websockets
from websockets.exceptions import WebSocketException

from backend.app.adapters.binance_usdm import BinancePublicAdapter
from backend.app.adapters.binance_usdm.public import PUBLIC_WS_BASE
from backend.app.adapters.bybit_linear import BybitPublicAdapter
from backend.app.adapters.bybit_linear.public import PUBLIC_LINEAR_WS
from backend.app.clocks import Clock
from backend.app.domain.market import Instrument, Ticker
from backend.app.domain.models import DataQuality, MarketEvent, Venue
from backend.app.orderbook import BinanceOrderBook, BybitOrderBook, SequenceGap


class PublicDataUnavailable(RuntimeError):
    """공개 시장데이터가 검증되지 않아 LIVE 전환을 차단한다."""


@dataclass(frozen=True, slots=True)
class LiveBootstrapResult:
    venue: Venue
    events: tuple[MarketEvent, ...]
    eligible_symbol_count: int
    wide_symbol_count: int
    deep_symbol_count: int
    websocket_lag_ms: float
    selected_symbol: str


class LiveBootstrapProbe(Protocol):
    async def bootstrap(
        self, venue: Venue, *, run_id: str, clock: Clock
    ) -> LiveBootstrapResult: ...


class LivePublicBootstrapper:
    """거래소별 공개 계약과 sequence-valid 첫 이벤트를 검증한다."""

    async def bootstrap(self, venue: Venue, *, run_id: str, clock: Clock) -> LiveBootstrapResult:
        try:
            if venue is Venue.BINANCE_USDM:
                return await self._binance(run_id=run_id, clock=clock)
            if venue is Venue.BYBIT_LINEAR:
                return await self._bybit(run_id=run_id, clock=clock)
            raise ValueError(f"LIVE 공개 데이터를 지원하지 않는 거래소: {venue}")
        except (
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            httpx.HTTPError,
            WebSocketException,
        ) as error:
            raise PublicDataUnavailable(
                f"{venue.value}: {type(error).__name__}: {error}"
            ) from error

    async def _binance(self, *, run_id: str, clock: Clock) -> LiveBootstrapResult:
        async with BinancePublicAdapter() as adapter:
            instruments, tickers = await asyncio.gather(
                adapter.fetch_instruments(), adapter.fetch_tickers()
            )
            eligible = _eligible_tickers(instruments, tickers)
            if not eligible:
                raise PublicDataUnavailable("BINANCE_USDM 유효 종목이 없습니다.")
            selected = "BTCUSDT" if "BTCUSDT" in eligible else next(iter(eligible))
            depth_event = await _binance_depth_event(adapter, selected, run_id, clock)
        ticker_events = _ticker_events(
            Venue.BINANCE_USDM, eligible, run_id=run_id, clock=clock, maximum=50
        )
        lag_ms = depth_event.quality.lag_ms
        if lag_ms is None:
            raise PublicDataUnavailable("Binance WebSocket timestamp가 없습니다.")
        return LiveBootstrapResult(
            venue=Venue.BINANCE_USDM,
            events=(*ticker_events, depth_event),
            eligible_symbol_count=len(eligible),
            wide_symbol_count=min(50, len(eligible)),
            deep_symbol_count=1,
            websocket_lag_ms=lag_ms,
            selected_symbol=selected,
        )

    async def _bybit(self, *, run_id: str, clock: Clock) -> LiveBootstrapResult:
        async with BybitPublicAdapter() as adapter:
            instruments, tickers = await asyncio.gather(
                adapter.fetch_instruments(), adapter.fetch_tickers()
            )
        eligible = _eligible_tickers(instruments, tickers)
        if not eligible:
            raise PublicDataUnavailable("BYBIT_LINEAR 유효 종목이 없습니다.")
        selected = "BTCUSDT" if "BTCUSDT" in eligible else next(iter(eligible))
        depth_event = await _bybit_depth_event(selected, run_id, clock)
        ticker_events = _ticker_events(
            Venue.BYBIT_LINEAR, eligible, run_id=run_id, clock=clock, maximum=50
        )
        lag_ms = depth_event.quality.lag_ms
        if lag_ms is None:
            raise PublicDataUnavailable("Bybit WebSocket timestamp가 없습니다.")
        return LiveBootstrapResult(
            venue=Venue.BYBIT_LINEAR,
            events=(*ticker_events, depth_event),
            eligible_symbol_count=len(eligible),
            wide_symbol_count=min(50, len(eligible)),
            deep_symbol_count=1,
            websocket_lag_ms=lag_ms,
            selected_symbol=selected,
        )


async def _binance_depth_event(
    adapter: BinancePublicAdapter,
    symbol: str,
    run_id: str,
    clock: Clock,
) -> MarketEvent:
    url = f"{PUBLIC_WS_BASE}/stream?streams={symbol.lower()}@depth@100ms"
    async with asyncio.timeout(20):
        async with websockets.connect(
            url, max_size=1_000_000, ping_interval=20, additional_headers=None
        ) as websocket:
            snapshot = await adapter.fetch_depth(symbol)
            book = BinanceOrderBook()
            book.reset_snapshot(int(snapshot["lastUpdateId"]), snapshot["bids"], snapshot["asks"])
            while True:
                payload = _json_object(await websocket.recv())
                data = payload.get("data", payload)
                if not isinstance(data, dict) or data.get("e") != "depthUpdate":
                    continue
                try:
                    applied = book.apply_delta(
                        int(data["U"]),
                        int(data["u"]),
                        int(data["pu"]) if data.get("pu") is not None else None,
                        data["b"],
                        data["a"],
                    )
                except SequenceGap:
                    snapshot = await adapter.fetch_depth(symbol)
                    book.reset_snapshot(
                        int(snapshot["lastUpdateId"]), snapshot["bids"], snapshot["asks"]
                    )
                    continue
                if not applied:
                    continue
                bids, asks = book.top(1)
                venue_ts_ms = int(data["E"])
                return MarketEvent(
                    event_id=f"binance-depth-{data['u']}",
                    run_id=run_id,
                    venue=Venue.BINANCE_USDM,
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
                        lag_ms=max(0.0, float(clock.utc_ms() - venue_ts_ms)),
                    ),
                    data={
                        "bid": str(bids[0][0]),
                        "bid_qty": str(bids[0][1]),
                        "ask": str(asks[0][0]),
                        "ask_qty": str(asks[0][1]),
                    },
                )


async def _bybit_depth_event(symbol: str, run_id: str, clock: Clock) -> MarketEvent:
    async with asyncio.timeout(20):
        async with websockets.connect(
            PUBLIC_LINEAR_WS, max_size=1_000_000, ping_interval=20, additional_headers=None
        ) as websocket:
            await websocket.send(
                json.dumps({"op": "subscribe", "args": [f"orderbook.50.{symbol}"]})
            )
            book = BybitOrderBook()
            while True:
                payload = _json_object(await websocket.recv())
                if payload.get("success") is True:
                    continue
                data = payload.get("data")
                message_type = payload.get("type")
                if not isinstance(data, dict) or message_type not in {"snapshot", "delta"}:
                    continue
                book.apply(
                    str(message_type),
                    int(data["u"]),
                    int(data["seq"]),
                    data["b"],
                    data["a"],
                )
                if not book.sequence_valid:
                    continue
                bids, asks = book.top(1)
                venue_ts_ms = int(payload["ts"])
                return MarketEvent(
                    event_id=f"bybit-depth-{data['u']}",
                    run_id=run_id,
                    venue=Venue.BYBIT_LINEAR,
                    symbol=symbol,
                    event_type="ORDERBOOK",
                    venue_ts_ms=venue_ts_ms,
                    transaction_ts_ms=int(data["cts"]) if data.get("cts") is not None else None,
                    receive_monotonic_ns=clock.monotonic_ns(),
                    sequence_start=int(data["u"]),
                    sequence_end=int(data["u"]),
                    quality=DataQuality(
                        is_live=True,
                        is_stale=False,
                        sequence_valid=True,
                        lag_ms=max(0.0, float(clock.utc_ms() - venue_ts_ms)),
                    ),
                    data={
                        "bid": str(bids[0][0]),
                        "bid_qty": str(bids[0][1]),
                        "ask": str(asks[0][0]),
                        "ask_qty": str(asks[0][1]),
                    },
                )


def _eligible_tickers(
    instruments: Sequence[Instrument], tickers: Sequence[Ticker]
) -> dict[str, Ticker]:
    active = {
        instrument.symbol
        for instrument in instruments
        if instrument.status.upper() == "TRADING"
        and instrument.quote_asset == "USDT"
        and (
            instrument.contract_type.upper() == "PERPETUAL"
            if instrument.venue is Venue.BINANCE_USDM
            else "PERPETUAL" in instrument.contract_type.upper()
        )
        and instrument.base_asset not in {"USDC", "FDUSD", "TUSD", "USDP"}
        and not instrument.symbol.endswith(("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"))
    }
    return {
        ticker.symbol: ticker
        for ticker in sorted(tickers, key=lambda item: (-item.quote_turnover_24h, item.symbol))
        if ticker.symbol in active
    }


def _ticker_events(
    venue: Venue,
    tickers: dict[str, Ticker],
    *,
    run_id: str,
    clock: Clock,
    maximum: int,
) -> tuple[MarketEvent, ...]:
    timestamp = clock.utc_ms()
    return tuple(
        MarketEvent(
            event_id=f"{venue.value.lower()}-ticker-{ticker.symbol}-{timestamp}",
            run_id=run_id,
            venue=venue,
            symbol=ticker.symbol,
            event_type="REST_BOOK_TICKER_BOOTSTRAP",
            venue_ts_ms=timestamp,
            receive_monotonic_ns=clock.monotonic_ns(),
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0.0,
                flags=("REST_BOOTSTRAP",),
            ),
            data={
                "bid": str(ticker.bid),
                "bid_qty": "0",
                "ask": str(ticker.ask),
                "ask_qty": "0",
                "quote_turnover_24h": str(ticker.quote_turnover_24h),
            },
        )
        for ticker in list(tickers.values())[:maximum]
    )


def _json_object(payload: str | bytes) -> dict[str, Any]:
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("WebSocket payload는 JSON 객체여야 합니다.")
    return decoded
