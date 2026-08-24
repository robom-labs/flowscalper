"""저장 공개시장 이벤트를 화면용 replay timeline으로 변환한다."""

from __future__ import annotations

from collections.abc import Callable

from backend.app.storage.sqlite import SQLiteLedger


def build_replay_timeline(
    ledger: SQLiteLedger,
    source_run_id: str,
    *,
    symbol: str | None = None,
    limit: int = 2_000,
    cooperative_yield: Callable[[], None] | None = None,
) -> dict[str, object]:
    """checksum 검증 이벤트와 실제 집계 candle을 제한된 화면 프레임으로 제공한다."""

    if ledger.get_run(source_run_id) is None:
        raise ValueError(f"저장 Run을 찾을 수 없습니다: {source_run_id}")
    available_symbols = ledger.market_event_symbols(source_run_id)
    selected_symbol = symbol.strip().upper() if symbol else None
    if selected_symbol is None and available_symbols:
        selected_symbol = str(available_symbols[0]["symbol"])
    if selected_symbol is not None and selected_symbol not in {
        str(row["symbol"]) for row in available_symbols
    }:
        raise ValueError(f"저장 Run에 없는 종목입니다: {selected_symbol}")
    events = ledger.list_market_events(
        source_run_id,
        symbol=selected_symbol,
        limit=limit,
        cooperative_yield=cooperative_yield,
    )
    stored_candles = (
        ledger.list_candles(
            source_run_id,
            symbol=selected_symbol,
            interval_seconds=1,
        )
        if selected_symbol is not None
        else []
    )
    candles = [
        {
            "time": int(str(candle["open_ts_ms"])) // 1_000,
            "open_ts_ms": int(str(candle["open_ts_ms"])),
            "open": float(str(candle["open"])),
            "high": float(str(candle["high"])),
            "low": float(str(candle["low"])),
            "close": float(str(candle["close"])),
            "volume": float(str(candle["volume"])),
            "trade_count": int(str(candle["trade_count"])),
        }
        for candle in stored_candles
    ]
    total_events = next(
        (
            int(str(row["event_count"])) if row["event_count"] is not None else None
            for row in available_symbols
            if row["symbol"] == selected_symbol
        ),
        0,
    )
    return {
        "run_id": source_run_id,
        "symbol": selected_symbol,
        "total_events": total_events,
        "truncated": total_events is None or total_events > len(events),
        "available_symbols": available_symbols,
        "events": events,
        "candles": candles,
    }
