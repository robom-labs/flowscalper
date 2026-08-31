"""자격 증명 없이 REST·WebSocket 공개 시장데이터의 첫 이벤트를 진단한다."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import websockets

from backend.app.adapters.binance_usdm import BinancePublicAdapter
from backend.app.adapters.binance_usdm.public import MARKET_WS_BASE, PUBLIC_WS_BASE
from backend.app.adapters.bybit_linear import BybitPublicAdapter
from backend.app.adapters.bybit_linear.public import PUBLIC_LINEAR_WS
from backend.app.market_explorer import MarketExplorerService


async def _receive(
    url: str,
    subscribe: dict[str, object] | None = None,
    *,
    sample_count: int = 8,
) -> list[dict[str, Any]]:
    async with asyncio.timeout(20):
        async with websockets.connect(url, max_size=1_000_000, ping_interval=20) as websocket:
            if subscribe is not None:
                await websocket.send(json.dumps(subscribe))
            samples: list[dict[str, Any]] = []
            while True:
                payload = json.loads(await websocket.recv())
                if isinstance(payload, dict) and payload.get("success") is True:
                    continue
                if isinstance(payload, dict):
                    payload["_client_receive_ts_ms"] = time.time() * 1_000
                    samples.append(payload)
                    if len(samples) >= sample_count:
                        return samples


def _lag_ms(payload: dict[str, Any]) -> float | None:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        return None
    timestamp = data.get("E") or data.get("T") or payload.get("ts") or data.get("ts")
    if not isinstance(timestamp, int):
        return None
    received_ts_ms = payload.get("_client_receive_ts_ms")
    if not isinstance(received_ts_ms, int | float):
        received_ts_ms = time.time() * 1_000
    return max(0.0, float(received_ts_ms) - timestamp)


def _event_observation(stream: str, payload: dict[str, Any]) -> dict[str, object]:
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise ValueError("WebSocket event data가 object가 아닙니다.")
    source_ts_ms = data.get("E") or data.get("T") or payload.get("ts") or data.get("ts")
    received_ts_ms = payload.get("_client_receive_ts_ms")
    if (
        not isinstance(source_ts_ms, int)
        or not isinstance(received_ts_ms, int | float)
        or float(received_ts_ms) < source_ts_ms
    ):
        raise ValueError("WebSocket event timestamp 결합이 유효하지 않습니다.")
    return {
        "stream": stream,
        "source_ts_ms": source_ts_ms,
        "received_ts_ms": round(float(received_ts_ms), 3),
    }


async def _binance() -> dict[str, object]:
    started = time.perf_counter()
    started_at = datetime.now(UTC)
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
    explorer = MarketExplorerService()
    catalog, btc_candles, upbit_candles = await asyncio.gather(
        explorer.catalog(force=True),
        explorer.candles("BINANCE_USDM", "BTCUSDT", 180, 200),
        explorer.candles("UPBIT_KRW", "KRW-BTC", 180, 200),
    )
    catalog_rows = catalog["rows"]
    if not isinstance(catalog_rows, list):
        raise ValueError("공개시장 catalog 응답이 배열이 아닙니다.")
    binance_symbols = [
        str(row["symbol"])
        for row in catalog_rows
        if isinstance(row, dict) and row.get("venue") == "BINANCE_USDM"
    ]
    upbit_symbols = [
        str(row["symbol"])
        for row in catalog_rows
        if isinstance(row, dict) and row.get("venue") == "UPBIT_KRW"
    ]
    if not binance_symbols or len(upbit_symbols) <= 50:
        raise ValueError("Binance 또는 Upbit 공개시장 catalog가 수용기준보다 작습니다.")
    catalog_tail = binance_symbols[-1]
    tail_candles = await explorer.candles("BINANCE_USDM", catalog_tail, 180, 200)
    public_events, market_events = await asyncio.gather(
        _receive(f"{PUBLIC_WS_BASE}/stream?streams=btcusdt@depth@100ms"),
        _receive(f"{MARKET_WS_BASE}/stream?streams=btcusdt@aggTrade"),
    )
    lags = [
        lag
        for lag in (_lag_ms(event) for event in [*public_events, *market_events])
        if lag is not None
    ]
    event_samples = [
        *(
            _event_observation("binance-public-depth", event)
            for event in public_events
        ),
        *(
            _event_observation("binance-market-aggtrade", event)
            for event in market_events
        ),
    ]
    completed_at = datetime.now(UTC)
    return {
        "status": "PASS",
        "venue": "BINANCE_USDM",
        "eligible_symbol_count": len(eligible),
        "binance_catalog_count": len(binance_symbols),
        "upbit_krw_catalog_count": len(upbit_symbols),
        "binance_btcusdt_3m_candle_count": btc_candles["count"],
        "binance_catalog_tail_symbol": catalog_tail,
        "binance_catalog_tail_3m_candle_count": tail_candles["count"],
        "upbit_krw_btc_3m_candle_count": upbit_candles["count"],
        "websocket_events": len(public_events) + len(market_events),
        "event_samples": event_samples,
        "lag_p50_ms": round(statistics.median(lags), 3) if lags else None,
        "lag_p95_ms": round(max(lags), 3) if lags else None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "credentials_sent": False,
        "authorization_header_sent": False,
        "auth_required": False,
        "real_orders_enabled": False,
        "started_ts_utc": started_at.isoformat().replace("+00:00", "Z"),
        "completed_ts_utc": completed_at.isoformat().replace("+00:00", "Z"),
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
    events = await _receive(
        PUBLIC_LINEAR_WS,
        {"op": "subscribe", "args": ["orderbook.50.BTCUSDT"]},
    )
    lags = [lag for lag in (_lag_ms(event) for event in events) if lag is not None]
    return {
        "status": "PASS",
        "venue": "BYBIT_LINEAR",
        "eligible_symbol_count": len(active & ticker_symbols),
        "websocket_events": len(events),
        "lag_p50_ms": round(statistics.median(lags), 3) if lags else None,
        "lag_p95_ms": round(max(lags), 3) if lags else None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "credentials_sent": False,
    }


async def probe_public_network() -> dict[str, object]:
    try:
        return await _binance()
    except (OSError, TimeoutError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        return {
            "status": "FAIL",
            "reason": "Binance·Upbit 필수 공개 네트워크 검증 실패",
            "failure": f"{type(exc).__name__}: {exc}",
            "credentials_sent": False,
            "authorization_header_sent": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = asyncio.run(probe_public_network())
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
