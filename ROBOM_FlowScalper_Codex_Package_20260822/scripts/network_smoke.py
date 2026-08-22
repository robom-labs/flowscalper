"""자격 증명 없이 REST·WebSocket 공개 시장데이터의 첫 이벤트를 진단한다."""

from __future__ import annotations

import asyncio
import json
import socket
import statistics
import time
from typing import Any

import httpx
import websockets

from backend.app.adapters.binance_usdm import BinancePublicAdapter
from backend.app.adapters.binance_usdm.public import MARKET_WS_BASE, PUBLIC_WS_BASE
from backend.app.adapters.bybit_linear import BybitPublicAdapter
from backend.app.adapters.bybit_linear.public import PUBLIC_LINEAR_WS


async def _receive(url: str, subscribe: dict[str, object] | None = None) -> dict[str, Any]:
    async with asyncio.timeout(20):
        async with websockets.connect(url, max_size=1_000_000, ping_interval=20) as websocket:
            if subscribe is not None:
                await websocket.send(json.dumps(subscribe))
            while True:
                payload = json.loads(await websocket.recv())
                if isinstance(payload, dict) and payload.get("success") is True:
                    continue
                if isinstance(payload, dict):
                    return payload


def _lag_ms(payload: dict[str, Any]) -> float | None:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    timestamp = data.get("E") or data.get("T") or payload.get("ts") or data.get("ts")
    if not isinstance(timestamp, int):
        return None
    return max(0.0, time.time() * 1000 - timestamp)


async def _binance() -> dict[str, object]:
    started = time.perf_counter()
    socket.getaddrinfo("fapi.binance.com", 443)
    async with BinancePublicAdapter() as adapter:
        instruments, tickers = await asyncio.gather(
            adapter.fetch_instruments(), adapter.fetch_tickers()
        )
    active = {
        item.symbol
        for item in instruments
        if item.status == "TRADING"
        and item.contract_type == "PERPETUAL"
        and item.quote_asset == "USDT"
    }
    ticker_symbols = {item.symbol for item in tickers}
    eligible = active & ticker_symbols
    public_event, market_event = await asyncio.gather(
        _receive(f"{PUBLIC_WS_BASE}/stream?streams=btcusdt@depth@100ms"),
        _receive(f"{MARKET_WS_BASE}/stream?streams=btcusdt@aggTrade"),
    )
    lags = [lag for lag in (_lag_ms(public_event), _lag_ms(market_event)) if lag is not None]
    return {
        "status": "PASS",
        "venue": "BINANCE_USDM",
        "eligible_symbol_count": len(eligible),
        "websocket_events": 2,
        "lag_p50_ms": round(statistics.median(lags), 3) if lags else None,
        "lag_p95_ms": round(max(lags), 3) if lags else None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "credentials_sent": False,
    }


async def _bybit() -> dict[str, object]:
    started = time.perf_counter()
    socket.getaddrinfo("api.bybit.com", 443)
    async with BybitPublicAdapter() as adapter:
        instruments, tickers = await asyncio.gather(
            adapter.fetch_instruments(), adapter.fetch_tickers()
        )
    active = {
        item.symbol
        for item in instruments
        if item.status.lower() == "trading"
        and "perpetual" in item.contract_type.lower()
        and item.quote_asset == "USDT"
    }
    ticker_symbols = {item.symbol for item in tickers}
    event = await _receive(
        PUBLIC_LINEAR_WS,
        {"op": "subscribe", "args": ["orderbook.50.BTCUSDT"]},
    )
    lag = _lag_ms(event)
    return {
        "status": "PASS",
        "venue": "BYBIT_LINEAR",
        "eligible_symbol_count": len(active & ticker_symbols),
        "websocket_events": 1,
        "lag_p50_ms": round(lag, 3) if lag is not None else None,
        "lag_p95_ms": round(lag, 3) if lag is not None else None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "credentials_sent": False,
    }


async def main() -> None:
    failures: list[str] = []
    for probe in (_binance, _bybit):
        try:
            result = await probe()
        except (OSError, TimeoutError, ValueError, httpx.HTTPError) as exc:
            failures.append(f"{probe.__name__}: {type(exc).__name__}: {exc}")
            continue
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(
        json.dumps(
            {"status": "NOT_RUN", "reason": "공개 네트워크 연결 실패", "failures": failures},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
