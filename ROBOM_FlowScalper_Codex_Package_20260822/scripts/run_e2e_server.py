"""브라우저 검증용 격리 원장에 공개시장 형식 이벤트를 심고 로컬 앱을 실행한다."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Venue
from backend.app.storage.sqlite import SQLiteLedger

REPLAY_RUN_ID = "e2e-public-replay-v1"


def seed_replay_run(database: Path) -> None:
    ledger = SQLiteLedger(database)
    try:
        existing = ledger.get_run(REPLAY_RUN_ID)
        if existing is not None:
            return
        ledger.start_run(
            REPLAY_RUN_ID,
            mode=RuntimeMode.LIVE_SHADOW_PAPER.value,
            venue=Venue.BINANCE_USDM.value,
            config={"seed": 20260822, "purpose": "browser-contract-test"},
            started_ts_ms=1_721_000_000_000,
        )
        quality = DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=8,
        )
        events = [
            MarketEvent(
                event_id="e2e-book-1",
                run_id=REPLAY_RUN_ID,
                venue=Venue.BINANCE_USDM,
                symbol="BTCUSDT",
                event_type="BOOK_TICKER",
                venue_ts_ms=1_721_000_001_000,
                receive_monotonic_ns=1_000_000_000,
                quality=quality,
                data={"bid": "60000.0", "bid_qty": "2", "ask": "60000.5", "ask_qty": "2"},
            ),
            MarketEvent(
                event_id="e2e-depth-2",
                run_id=REPLAY_RUN_ID,
                venue=Venue.BINANCE_USDM,
                symbol="BTCUSDT",
                event_type="DEPTH_UPDATE",
                venue_ts_ms=1_721_000_002_000,
                receive_monotonic_ns=2_000_000_000,
                sequence_start=2,
                sequence_end=2,
                previous_sequence_end=1,
                quality=quality,
                data={
                    "bid": "60000.2",
                    "bid_qty": "3",
                    "ask": "60000.6",
                    "ask_qty": "3",
                    "bids": [["60000.2", "3"], ["60000.1", "4"]],
                    "asks": [["60000.6", "3"], ["60000.7", "4"]],
                },
            ),
            MarketEvent(
                event_id="e2e-trade-3",
                run_id=REPLAY_RUN_ID,
                venue=Venue.BINANCE_USDM,
                symbol="BTCUSDT",
                event_type="TRADE",
                venue_ts_ms=1_721_000_003_000,
                transaction_ts_ms=1_721_000_003_000,
                receive_monotonic_ns=3_000_000_000,
                quality=quality,
                data={"price": "60000.4", "quantity": "0.2", "buyer_is_aggressor": True},
            ),
        ]
        ledger.record_market_events(
            [event.model_dump(mode="json") for event in events]
        )
        ledger.record_candles(
            [
                {
                    "run_id": REPLAY_RUN_ID,
                    "symbol": "BTCUSDT",
                    "interval_seconds": 1,
                    "open_ts_ms": 1_721_000_003_000,
                    "open": "60000.4",
                    "high": "60000.4",
                    "low": "60000.4",
                    "close": "60000.4",
                    "volume": "0.2",
                    "trade_count": 1,
                }
            ]
        )
        ledger.finalize_run(
            REPLAY_RUN_ID,
            finalized_ts_ms=1_721_000_004_000,
            summary={"purpose": "browser-contract-test", "real_orders": 0},
        )
    finally:
        ledger.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args()
    seed_replay_run(arguments.database)
    os.environ["ROBOM_MODE"] = RuntimeMode.DEMO_FIXTURE.value
    os.environ["ROBOM_PORT"] = str(arguments.port)
    os.environ["ROBOM_DB_PATH"] = str(arguments.database)
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=arguments.port)


if __name__ == "__main__":
    main()
