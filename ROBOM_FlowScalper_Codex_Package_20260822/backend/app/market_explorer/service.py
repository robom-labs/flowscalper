"""인증 없는 전체 시장 목록과 과거 캔들을 읽기 전용으로 제공한다."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any

import httpx

from backend.app.adapters.binance_usdm import BinancePublicAdapter
from backend.app.domain.market import Instrument, Ticker
from backend.app.market_data.timeframes import TIMEFRAME_REGISTRY

BINANCE_REST = "https://fapi.binance.com"
UPBIT_REST = "https://api.upbit.com"


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class CatalogRow:
    venue: str
    symbol: str
    display_symbol: str
    base_asset: str
    quote_asset: str
    market_role: str
    last: float
    bid: float
    ask: float
    change_percent: float
    quote_volume_24h: float
    trade_count_24h: int
    status: str
    korean_name: str = ""
    english_name: str = ""
    strategy_eligible: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "venue": self.venue,
            "symbol": self.symbol,
            "display_symbol": self.display_symbol,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "market_role": self.market_role,
            "last": self.last,
            "bid": self.bid,
            "ask": self.ask,
            "change_percent": self.change_percent,
            "quote_volume_24h": self.quote_volume_24h,
            "trade_count_24h": self.trade_count_24h,
            "status": self.status,
            "korean_name": self.korean_name,
            "english_name": self.english_name,
            "strategy_eligible": self.strategy_eligible,
        }


CatalogLoader = Callable[[], Awaitable[Sequence[CatalogRow]]]
CandleLoader = Callable[[str, int, int], Awaitable[Sequence[Mapping[str, object]]]]


@dataclass(slots=True)
class MarketExplorerService:
    """전체 catalog와 차트용 200봉을 실행 파이프라인과 분리한다."""

    binance_catalog_loader: CatalogLoader | None = None
    upbit_catalog_loader: CatalogLoader | None = None
    binance_candle_loader: CandleLoader | None = None
    upbit_candle_loader: CandleLoader | None = None
    refresh_seconds: float = 30.0
    _catalog: tuple[CatalogRow, ...] = ()
    _refreshed_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def catalog(
        self,
        *,
        source: str | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        stale = monotonic() - self._refreshed_at >= self.refresh_seconds
        if force or not self._catalog or stale:
            async with self._lock:
                stale = monotonic() - self._refreshed_at >= self.refresh_seconds
                if force or not self._catalog or stale:
                    await self._refresh()
        selected = tuple(row for row in self._catalog if source is None or row.venue == source)
        rows = [row.as_dict() for row in selected]
        return {
            "source": source or "ALL_PUBLIC",
            "updated_monotonic": self._refreshed_at,
            "count": len(rows),
            "rows": rows,
            "counts": {
                "BINANCE_USDM": sum(row.venue == "BINANCE_USDM" for row in self._catalog),
                "UPBIT_KRW": sum(row.venue == "UPBIT_KRW" for row in self._catalog),
                "total": len(self._catalog),
            },
            "paper_execution_venue": "BINANCE_USDM",
            "observation_only_venues": ["UPBIT_KRW"],
            "auth_required": False,
            "real_orders_enabled": False,
        }

    async def candles(
        self,
        source: str,
        symbol: str,
        interval_seconds: int,
        limit: int = 200,
    ) -> dict[str, object]:
        source = source.upper()
        if source not in {"BINANCE_USDM", "UPBIT_KRW"}:
            raise ValueError("지원하지 않는 공개시장입니다.")
        TIMEFRAME_REGISTRY.exchange_interval(source, interval_seconds)
        if not 20 <= limit <= 500:
            raise ValueError("캔들 개수는 20..500 범위여야 합니다.")
        loader = (
            self.upbit_candle_loader or self._load_upbit_candles
            if source == "UPBIT_KRW"
            else self.binance_candle_loader or self._load_binance_candles
        )
        rows = [dict(row) for row in await loader(symbol.upper(), interval_seconds, limit)]
        merged = {int(str(row["open_ts_ms"])): row for row in rows if _valid_candle(row)}
        ordered = [merged[key] for key in sorted(merged)][-limit:]
        return {
            "venue": source,
            "source": source,
            "symbol": symbol.upper(),
            "interval_seconds": interval_seconds,
            "candles": ordered,
            "count": len(ordered),
            "ticker": {},
            "observation_only": source == "UPBIT_KRW",
            "auth_required": False,
            "real_orders_enabled": False,
        }

    async def _refresh(self) -> None:
        binance = self.binance_catalog_loader or self._load_binance_catalog
        upbit = self.upbit_catalog_loader or self._load_upbit_catalog
        results = await asyncio.gather(binance(), upbit(), return_exceptions=True)
        rows: list[CatalogRow] = []
        for result in results:
            if not isinstance(result, BaseException):
                rows.extend(result)
        if not rows:
            failures = "; ".join(
                str(result) for result in results if isinstance(result, BaseException)
            )
            raise RuntimeError(f"공개 시장 목록을 불러오지 못했습니다. {failures}")
        self._catalog = tuple(
            sorted(rows, key=lambda row: (row.venue, -row.quote_volume_24h, row.symbol))
        )
        self._refreshed_at = monotonic()

    @staticmethod
    async def _load_binance_catalog() -> Sequence[CatalogRow]:
        async with BinancePublicAdapter() as adapter:
            instruments, tickers = await asyncio.gather(
                adapter.fetch_instruments(), adapter.fetch_tickers()
            )
        instrument_by_symbol = {
            item.symbol: item
            for item in instruments
            if item.status.upper() == "TRADING"
            and item.quote_asset == "USDT"
            and "PERPETUAL" in item.contract_type.upper()
        }
        return [
            _binance_row(instrument_by_symbol[ticker.symbol], ticker)
            for ticker in tickers
            if ticker.symbol in instrument_by_symbol
        ]

    @staticmethod
    async def _load_upbit_catalog() -> Sequence[CatalogRow]:
        async with httpx.AsyncClient(base_url=UPBIT_REST, timeout=10.0) as client:
            markets_response = await client.get("/v1/market/all", params={"is_details": "true"})
            markets_response.raise_for_status()
            markets = markets_response.json()
            market_rows = {
                str(item["market"]): item
                for item in markets
                if isinstance(item, dict) and str(item.get("market", "")).startswith("KRW-")
            }
            codes = sorted(market_rows)
            if not codes:
                return []
            ticker_rows: list[dict[str, Any]] = []
            for start in range(0, len(codes), 100):
                response = await client.get(
                    "/v1/ticker",
                    params={"markets": ",".join(codes[start : start + 100])},
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, list):
                    ticker_rows.extend(item for item in payload if isinstance(item, dict))
        return [
            CatalogRow(
                venue="UPBIT_KRW",
                symbol=str(item["market"]),
                display_symbol=str(item["market"]).replace("KRW-", "") + "/KRW",
                base_asset=str(item["market"]).replace("KRW-", ""),
                quote_asset="KRW",
                market_role="OBSERVATION_ONLY",
                last=_number(item.get("trade_price")),
                bid=0.0,
                ask=0.0,
                change_percent=_number(item.get("signed_change_rate")) * 100,
                quote_volume_24h=_number(item.get("acc_trade_price_24h")),
                trade_count_24h=0,
                status="ACTIVE",
                korean_name=str(market_rows[str(item["market"])].get("korean_name", "")),
                english_name=str(market_rows[str(item["market"])].get("english_name", "")),
                strategy_eligible=False,
            )
            for item in ticker_rows
        ]

    @staticmethod
    async def _load_binance_candles(
        symbol: str,
        interval_seconds: int,
        limit: int,
    ) -> Sequence[Mapping[str, object]]:
        interval = TIMEFRAME_REGISTRY.exchange_interval("BINANCE_USDM", interval_seconds)
        async with httpx.AsyncClient(base_url=BINANCE_REST, timeout=10.0) as client:
            response = await client.get(
                "/fapi/v1/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Binance kline 응답이 배열이 아닙니다.")
        return [
            {
                "time": int(row[0]) // 1000,
                "open_ts_ms": int(row[0]),
                "open": _number(row[1]),
                "high": _number(row[2]),
                "low": _number(row[3]),
                "close": _number(row[4]),
                "volume": _number(row[5]),
                "trade_count": int(row[8]),
            }
            for row in payload
            if isinstance(row, list) and len(row) >= 9
        ]

    @staticmethod
    async def _load_upbit_candles(
        symbol: str,
        interval_seconds: int,
        limit: int,
    ) -> Sequence[Mapping[str, object]]:
        unit = TIMEFRAME_REGISTRY.exchange_interval("UPBIT_KRW", interval_seconds)
        async with httpx.AsyncClient(base_url=UPBIT_REST, timeout=10.0) as client:
            response = await client.get(
                f"/v1/candles/minutes/{unit}",
                params={"market": symbol, "count": limit},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Upbit candle 응답이 배열이 아닙니다.")
        rows: list[dict[str, object]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            utc_text = str(item.get("candle_date_time_utc", ""))
            try:
                open_ts_ms = int(
                    datetime.fromisoformat(utc_text).replace(tzinfo=UTC).timestamp() * 1_000
                )
            except ValueError:
                continue
            rows.append(
                {
                    "time": open_ts_ms // 1_000,
                    "open_ts_ms": open_ts_ms,
                    "open": _number(item.get("opening_price")),
                    "high": _number(item.get("high_price")),
                    "low": _number(item.get("low_price")),
                    "close": _number(item.get("trade_price")),
                    "volume": _number(item.get("candle_acc_trade_volume")),
                    "trade_count": 0,
                }
            )
        return rows


def _valid_candle(row: Mapping[str, object]) -> bool:
    values = [
        _number(row.get(name), math.nan) for name in ("open", "high", "low", "close", "volume")
    ]
    open_value, high, low, close, volume = values
    return (
        all(math.isfinite(value) for value in values)
        and high >= max(open_value, close)
        and low <= min(open_value, close)
        and volume >= 0
    )


def _binance_row(instrument: Instrument, ticker: Ticker) -> CatalogRow:
    return CatalogRow(
        venue="BINANCE_USDM",
        symbol=instrument.symbol,
        display_symbol=f"{instrument.base_asset}/USDT",
        base_asset=instrument.base_asset,
        quote_asset="USDT",
        market_role="PAPER_EXECUTION",
        last=float(ticker.mid),
        bid=float(ticker.bid),
        ask=float(ticker.ask),
        change_percent=0.0,
        quote_volume_24h=float(ticker.quote_turnover_24h),
        trade_count_24h=ticker.trade_count_24h,
        status="ACTIVE",
    )
