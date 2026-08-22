"""인증 없는 Binance USDⓈ-M REST와 분리된 공개 WebSocket 경로를 처리한다."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from decimal import Decimal
from itertools import islice
from typing import Any

import httpx

from backend.app.domain.market import Instrument, Ticker
from backend.app.domain.models import Venue

REST_BASE = "https://fapi.binance.com"
PUBLIC_WS_BASE = "wss://fstream.binance.com/public"
MARKET_WS_BASE = "wss://fstream.binance.com/market"


class BinanceProtocolError(ValueError):
    """공개 응답이 예상 계약을 위반할 때 발생한다."""


def _positive_decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise BinanceProtocolError(f"{field} 숫자가 잘못되었습니다.") from exc
    if not number.is_finite() or number <= 0:
        raise BinanceProtocolError(f"{field}는 유한한 양수여야 합니다.")
    return number


def _filter_value(filters: list[dict[str, Any]], name: str, field: str) -> object:
    for item in filters:
        if item.get("filterType") == name:
            return item[field]
    raise BinanceProtocolError(f"필수 {name}.{field} 필터가 없습니다.")


class BinancePublicAdapter:
    venue = Venue.BINANCE_USDM

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(base_url=REST_BASE, timeout=10.0)

    async def __aenter__(self) -> BinancePublicAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def fetch_instruments(self) -> list[Instrument]:
        response = await self._client.get("/fapi/v1/exchangeInfo")
        response.raise_for_status()
        payload = response.json()
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            raise BinanceProtocolError("exchangeInfo.symbols 배열이 없습니다.")
        return [self.parse_instrument(item) for item in symbols]

    async def fetch_tickers(self) -> list[Ticker]:
        statistics_response, books_response = await asyncio.gather(
            self._client.get("/fapi/v1/ticker/24hr"),
            self._client.get("/fapi/v1/ticker/bookTicker"),
        )
        statistics_response.raise_for_status()
        books_response.raise_for_status()
        statistics = statistics_response.json()
        books = books_response.json()
        if not isinstance(statistics, list) or not isinstance(books, list):
            raise BinanceProtocolError("ticker 또는 bookTicker 응답이 배열이 아닙니다.")
        books_by_symbol = {
            str(item.get("symbol")): item for item in books if isinstance(item, dict)
        }
        tickers: list[Ticker] = []
        for item in statistics:
            if not isinstance(item, dict):
                continue
            book = books_by_symbol.get(str(item.get("symbol")))
            if book is None:
                continue
            try:
                tickers.append(self.parse_ticker({**item, **book}))
            except (BinanceProtocolError, KeyError, TypeError, ValueError):
                continue
        return tickers

    async def fetch_depth(self, symbol: str, limit: int = 1000) -> dict[str, Any]:
        response = await self._client.get(
            "/fapi/v1/depth",
            params={"symbol": symbol, "limit": limit},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "lastUpdateId" not in payload:
            raise BinanceProtocolError("depth snapshot 계약이 잘못되었습니다.")
        return payload

    @classmethod
    def parse_instrument(cls, item: dict[str, Any]) -> Instrument:
        filters = item.get("filters")
        if not isinstance(filters, list):
            raise BinanceProtocolError("instrument filters 배열이 없습니다.")
        return Instrument(
            venue=cls.venue,
            symbol=str(item["symbol"]),
            base_asset=str(item["baseAsset"]),
            quote_asset=str(item["quoteAsset"]),
            status=str(item["status"]),
            contract_type=str(item["contractType"]),
            tick_size=_positive_decimal(
                _filter_value(filters, "PRICE_FILTER", "tickSize"), "tickSize"
            ),
            quantity_step=_positive_decimal(
                _filter_value(filters, "LOT_SIZE", "stepSize"), "stepSize"
            ),
            minimum_quantity=_positive_decimal(
                _filter_value(filters, "LOT_SIZE", "minQty"), "minQty"
            ),
            onboard_ts_ms=int(item["onboardDate"]) if item.get("onboardDate") else None,
        )

    @classmethod
    def parse_ticker(cls, item: dict[str, Any]) -> Ticker:
        return Ticker(
            venue=cls.venue,
            symbol=str(item["symbol"]),
            bid=_positive_decimal(item["bidPrice"], "bidPrice"),
            ask=_positive_decimal(item["askPrice"], "askPrice"),
            quote_turnover_24h=_positive_decimal(item["quoteVolume"], "quoteVolume"),
            trade_count_24h=int(item.get("count", 0)),
        )


class BinanceStreamRouter:
    """공식 2026 endpoint 분리에 맞춰 공개 스트림을 보수적으로 샤딩한다."""

    def __init__(self, maximum_streams_per_connection: int = 100) -> None:
        if not 1 <= maximum_streams_per_connection <= 1024:
            raise ValueError("스트림 상한은 1..1024 범위여야 합니다.")
        self.maximum_streams = maximum_streams_per_connection

    @staticmethod
    def family(stream: str) -> str:
        lower = stream.lower()
        if "@depth" in lower or lower.endswith("@bookticker") or lower == "!bookticker":
            return "public"
        if any(
            token in lower
            for token in ("@aggtrade", "@markprice", "@ticker", "@miniticker", "@kline_")
        ):
            return "market"
        raise BinanceProtocolError(f"지원하지 않는 공개 스트림입니다: {stream}")

    def urls(self, streams: Iterable[str]) -> list[str]:
        grouped: dict[str, list[str]] = {"public": [], "market": []}
        for stream in streams:
            normalized = stream.lower()
            grouped[self.family(normalized)].append(normalized)
        urls: list[str] = []
        for family, values in grouped.items():
            iterator = iter(values)
            while chunk := tuple(islice(iterator, self.maximum_streams)):
                base = PUBLIC_WS_BASE if family == "public" else MARKET_WS_BASE
                urls.append(f"{base}/stream?streams={'/'.join(chunk)}")
        return urls

    @staticmethod
    def subscription_payload(streams: Iterable[str], request_id: int = 1) -> dict[str, object]:
        return {
            "method": "SUBSCRIBE",
            "params": [item.lower() for item in streams],
            "id": request_id,
        }

    @staticmethod
    def unsubscription_payload(streams: Iterable[str], request_id: int = 1) -> dict[str, object]:
        return {
            "method": "UNSUBSCRIBE",
            "params": [item.lower() for item in streams],
            "id": request_id,
        }
