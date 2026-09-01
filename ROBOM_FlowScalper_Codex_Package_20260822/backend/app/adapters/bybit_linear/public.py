"""인증 없는 Bybit V5 linear REST 메타데이터를 페이지 끝까지 처리한다."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from backend.app.domain.market import Instrument, Ticker
from backend.app.domain.models import Venue

REST_BASE = "https://api.bybit.com"
PUBLIC_LINEAR_WS = "wss://stream.bybit.com/v5/public/linear"


class BybitProtocolError(ValueError):
    """Bybit 공개 응답이 예상 계약을 위반할 때 발생한다."""


def _positive_decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise BybitProtocolError(f"{field} 숫자가 잘못되었습니다.") from exc
    if not number.is_finite() or number <= 0:
        raise BybitProtocolError(f"{field}는 유한한 양수여야 합니다.")
    return number


def _finite_decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise BybitProtocolError(f"{field} 숫자가 잘못되었습니다.") from exc
    if not number.is_finite():
        raise BybitProtocolError(f"{field}는 유한한 숫자여야 합니다.")
    return number


class BybitPublicAdapter:
    venue = Venue.BYBIT_LINEAR

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(base_url=REST_BASE, timeout=10.0)

    async def __aenter__(self) -> BybitPublicAdapter:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def fetch_instruments(self) -> list[Instrument]:
        cursor = ""
        instruments: list[Instrument] = []
        seen_cursors: set[str] = set()
        while True:
            params: dict[str, str | int] = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            response = await self._client.get("/v5/market/instruments-info", params=params)
            response.raise_for_status()
            result = self._result(response.json())
            items = result.get("list")
            if not isinstance(items, list):
                raise BybitProtocolError("instrument list 배열이 없습니다.")
            instruments.extend(self.parse_instrument(item) for item in items)
            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise BybitProtocolError("instrument 페이지 cursor가 반복되었습니다.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return instruments

    async def fetch_tickers(self) -> list[Ticker]:
        response = await self._client.get("/v5/market/tickers", params={"category": "linear"})
        response.raise_for_status()
        result = self._result(response.json())
        items = result.get("list")
        if not isinstance(items, list):
            raise BybitProtocolError("ticker list 배열이 없습니다.")
        return [self.parse_ticker(item) for item in items]

    async def fetch_server_time_ms(self) -> int:
        """인증 없는 V5 market time endpoint의 나노초 시각을 ms로 반환한다."""

        response = await self._client.get("/v5/market/time")
        response.raise_for_status()
        result = self._result(response.json())
        if "timeNano" not in result:
            raise BybitProtocolError("timeNano 응답 계약이 잘못되었습니다.")
        return int(str(result["timeNano"])) // 1_000_000

    @staticmethod
    def _result(payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("retCode") != 0:
            raise BybitProtocolError("Bybit 공개 API가 성공 응답을 반환하지 않았습니다.")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise BybitProtocolError("Bybit result 객체가 없습니다.")
        return result

    @classmethod
    def parse_instrument(cls, item: dict[str, Any]) -> Instrument:
        price_filter = item.get("priceFilter")
        lot_filter = item.get("lotSizeFilter")
        if not isinstance(price_filter, dict) or not isinstance(lot_filter, dict):
            raise BybitProtocolError("instrument 정밀도 필터가 없습니다.")
        return Instrument(
            venue=cls.venue,
            symbol=str(item["symbol"]),
            base_asset=str(item["baseCoin"]),
            quote_asset=str(item["quoteCoin"]),
            status=str(item["status"]),
            contract_type=str(item["contractType"]),
            tick_size=_positive_decimal(price_filter["tickSize"], "tickSize"),
            quantity_step=_positive_decimal(lot_filter["qtyStep"], "qtyStep"),
            minimum_quantity=_positive_decimal(lot_filter["minOrderQty"], "minOrderQty"),
            onboard_ts_ms=int(item["launchTime"]) if item.get("launchTime") else None,
        )

    @classmethod
    def parse_ticker(cls, item: dict[str, Any]) -> Ticker:
        return Ticker(
            venue=cls.venue,
            symbol=str(item["symbol"]),
            bid=_positive_decimal(item["bid1Price"], "bid1Price"),
            ask=_positive_decimal(item["ask1Price"], "ask1Price"),
            quote_turnover_24h=_positive_decimal(item["turnover24h"], "turnover24h"),
            trade_count_24h=0,
            price_change_percent_24h=(
                _finite_decimal(item.get("price24hPcnt", "0"), "price24hPcnt")
                * Decimal("100")
            ),
        )
