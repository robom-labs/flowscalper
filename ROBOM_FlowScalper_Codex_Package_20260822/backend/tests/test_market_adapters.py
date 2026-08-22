"""공개 거래소 메타데이터, 페이지네이션과 스트림 라우팅을 검증한다."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from backend.app.adapters.binance_usdm.public import BinancePublicAdapter, BinanceStreamRouter
from backend.app.adapters.bybit_linear.public import BybitPublicAdapter

BINANCE_INSTRUMENT = {
    "symbol": "BTCUSDT",
    "baseAsset": "BTC",
    "quoteAsset": "USDT",
    "status": "TRADING",
    "contractType": "PERPETUAL",
    "onboardDate": 1_600_000_000_000,
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
    ],
}


def test_binance_precision_comes_from_filters() -> None:
    instrument = BinancePublicAdapter.parse_instrument(BINANCE_INSTRUMENT)
    assert instrument.tick_size == Decimal("0.10")
    assert instrument.quantity_step == Decimal("0.001")


def test_binance_routes_public_and_market_streams_separately() -> None:
    router = BinanceStreamRouter(maximum_streams_per_connection=2)
    urls = router.urls(
        ["btcusdt@depth@100ms", "ethusdt@bookTicker", "solusdt@depth", "btcusdt@aggTrade"]
    )
    assert urls == [
        "wss://fstream.binance.com/public/stream?streams=btcusdt@depth@100ms/ethusdt@bookticker",
        "wss://fstream.binance.com/public/stream?streams=solusdt@depth",
        "wss://fstream.binance.com/market/stream?streams=btcusdt@aggtrade",
    ]
    assert router.subscription_payload(["BTCUSDT@DEPTH"]) == {
        "method": "SUBSCRIBE",
        "params": ["btcusdt@depth"],
        "id": 1,
    }


@pytest.mark.asyncio
async def test_binance_combines_statistics_with_public_book_ticker() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ticker/24hr"):
            payload = [{"symbol": "BTCUSDT", "quoteVolume": "100000000", "count": 42}]
        else:
            payload = [{"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "100.1"}]
        return httpx.Response(200, content=json.dumps(payload).encode())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://fapi.binance.test"
    ) as client:
        tickers = await BinancePublicAdapter(client).fetch_tickers()

    assert len(tickers) == 1
    assert tickers[0].bid == Decimal("100")
    assert tickers[0].quote_turnover_24h == Decimal("100000000")


@pytest.mark.asyncio
async def test_bybit_instrument_pagination_reaches_empty_cursor() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        cursor = request.url.params.get("cursor")
        symbol = "BTCUSDT" if not cursor else "ETHUSDT"
        next_cursor = "page-2" if not cursor else ""
        payload = {
            "retCode": 0,
            "result": {
                "nextPageCursor": next_cursor,
                "list": [
                    {
                        "symbol": symbol,
                        "baseCoin": symbol.removesuffix("USDT"),
                        "quoteCoin": "USDT",
                        "status": "Trading",
                        "contractType": "LinearPerpetual",
                        "launchTime": "1600000000000",
                        "priceFilter": {"tickSize": "0.10"},
                        "lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"},
                    }
                ],
            },
        }
        return httpx.Response(200, content=json.dumps(payload).encode())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.bybit.test"
    ) as client:
        adapter = BybitPublicAdapter(client)
        instruments = await adapter.fetch_instruments()

    assert [instrument.symbol for instrument in instruments] == ["BTCUSDT", "ETHUSDT"]
    assert len(requests) == 2
    assert requests[1].url.params["cursor"] == "page-2"
