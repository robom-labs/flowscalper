"""v0.2 시장이벤트 원장·종단 리플레이·전략 성과표를 검증한다."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.analytics.reports import TradeAnalytics
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Venue
from backend.app.main import create_app
from backend.app.replay.market import StoredMarketReplay
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import LedgerInvariantError, SQLiteLedger
from backend.tests.test_candidate_paper_portfolio import book, candidate_plan
from backend.tests.test_storage_replay_analytics import _sample_trade


def market_event(
    run_id: str,
    *,
    event_id: str,
    ts_ms: int,
    event_type: str = "DEPTH_UPDATE",
) -> MarketEvent:
    data: dict[str, object]
    if event_type == "TRADE":
        data = {
            "price": "100.05",
            "quantity": "0.5",
            "buyer_is_aggressor": True,
        }
    else:
        data = {
            "bid": "99.9",
            "bid_qty": "100",
            "ask": "100.1",
            "ask_qty": "100",
            "bids": [["99.9", "100"], ["99.8", "100"]],
            "asks": [["100.1", "100"], ["100.2", "100"]],
        }
    return MarketEvent(
        event_id=event_id,
        run_id=run_id,
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        event_type=event_type,
        venue_ts_ms=ts_ms,
        transaction_ts_ms=ts_ms if event_type == "TRADE" else None,
        receive_monotonic_ns=ts_ms * 1_000_000,
        sequence_start=ts_ms if event_type == "DEPTH_UPDATE" else None,
        sequence_end=ts_ms if event_type == "DEPTH_UPDATE" else None,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=10,
        ),
        data=data,
    )


def test_schema_v3_market_events_are_ordered_checksummed_and_immutable(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    ledger.start_run(
        "run-market",
        mode="REPLAY",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    later = market_event("run-market", event_id="event-2", ts_ms=2_000)
    earlier = market_event("run-market", event_id="event-1", ts_ms=1_000)
    inserted = ledger.record_market_events(
        [later.model_dump(mode="json"), earlier.model_dump(mode="json")]
    )
    assert ledger.schema_version == 3
    assert inserted == 2
    assert [row["event_id"] for row in ledger.list_market_events("run-market")] == [
        "event-1",
        "event-2",
    ]
    assert ledger.record_market_events([earlier.model_dump(mode="json")]) == 0
    conflicting = earlier.model_copy(
        update={"data": {**earlier.data, "bid": "1.0"}}
    )
    with pytest.raises(LedgerInvariantError, match="payload 불일치"):
        ledger.record_market_events([conflicting.model_dump(mode="json")])
    ledger.start_run(
        "run-market-2",
        mode="REPLAY",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    same_venue_event = market_event("run-market-2", event_id="event-1", ts_ms=1_000)
    assert ledger.record_market_events([same_venue_event.model_dump(mode="json")]) == 1
    assert ledger.count("market_events") == 3
    ledger.close()

    connection = sqlite3.connect(tmp_path / "ledger.sqlite3")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        connection.execute(
            "UPDATE market_events SET payload_json = '{}' WHERE event_id = 'event-1'"
        )
    connection.close()


def test_v2_global_event_identity_migrates_to_run_scoped_identity(tmp_path: Path) -> None:
    database = tmp_path / "legacy-v2.sqlite3"
    payload = market_event("legacy-run", event_id="shared-event", ts_ms=1_000).model_dump(
        mode="json"
    )
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        PRAGMA user_version = 2;
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            venue TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            config_json TEXT NOT NULL,
            started_ts_ms INTEGER NOT NULL,
            finalized_ts_ms INTEGER,
            summary_json TEXT
        );
        CREATE TABLE market_events (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            venue TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            venue_ts_ms INTEGER NOT NULL,
            receive_monotonic_ns INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            checksum TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
        (
            "legacy-run",
            "REPLAY",
            Venue.BINANCE_USDM.value,
            "legacy-hash",
            '{"seed":20260822}',
            1_000,
        ),
    )
    connection.execute(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "shared-event",
            "legacy-run",
            Venue.BINANCE_USDM.value,
            "BTCUSDT",
            "DEPTH_UPDATE",
            1_000,
            1_000_000_000,
            payload_json,
            hashlib.sha256(payload_json.encode()).hexdigest(),
        ),
    )
    connection.commit()
    connection.close()

    migrated = SQLiteLedger(database)
    assert migrated.schema_version == 3
    assert migrated.list_market_events("legacy-run")[0]["event_id"] == "shared-event"
    migrated.start_run(
        "new-run",
        mode="REPLAY",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=2_000,
    )
    same_id = market_event("new-run", event_id="shared-event", ts_ms=2_000)
    assert migrated.record_market_events([same_id.model_dump(mode="json")]) == 1
    migrated.close()


def test_runtime_batches_public_events_and_replays_same_pipeline_deterministically(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-public-recorded",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.paused = False
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="depth-1", ts_ms=1_000)
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="trade-1", ts_ms=1_100, event_type="TRADE")
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="depth-2", ts_ms=1_500)
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="trade-2", ts_ms=2_100, event_type="TRADE")
    )
    assert ledger.count("market_events") == 0
    runtime.flush_storage()
    assert ledger.count("market_events") == 4
    assert ledger.count("candles") == 1

    first = StoredMarketReplay().run(
        ledger,
        source_run_id=runtime.run_id,
        created_ts_ms=2_000,
    )
    second = StoredMarketReplay().run(
        ledger,
        source_run_id=runtime.run_id,
        created_ts_ms=2_001,
    )
    assert first.checksum == second.checksum
    assert first.event_count == 4
    assert first.event_type_counts == {"DEPTH_UPDATE": 2, "TRADE": 2}
    assert first.strategy_evaluation_count == 16
    assert first.qualified_signal_count == 0
    assert first.final_state == "OBSERVING_NO_MAIN_TRADE"
    assert first.real_orders_enabled is False
    assert first.auth_required is False
    assert len(ledger.list_replay_runs(runtime.run_id)) == 2
    ledger.close()


def test_main_orders_fills_trade_and_shadow_trades_persist_from_real_engine(
    tmp_path: Path,
) -> None:
    plan = candidate_plan()
    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id=plan.run_id,
        venue=Venue.FIXTURE,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ledger.record_candidate(runtime._candidate_plan_row(plan))
    runtime.paper_portfolio.offer((plan,), entries_paused=False)
    runtime.paper_portfolio.on_book(book(1_250))
    runtime._persist_execution_state(1_250)
    runtime.paper_portfolio.on_book(book(1_500))
    runtime._persist_execution_state(1_500)

    tp1 = plan.take_profit_targets[0].price
    runtime.paper_portfolio.on_book(
        book(2_000, bids=((str(tp1 + Decimal("0.1")), "100"),), asks=(("107", "100"),))
    )
    runtime.paper_portfolio.on_book(
        book(2_250, bids=((str(tp1 + Decimal("0.05")), "100"),), asks=(("107", "100"),))
    )
    runtime._persist_execution_state(2_250)
    tp2 = plan.take_profit_targets[1].price
    runtime.paper_portfolio.on_book(
        book(3_000, bids=((str(tp2 + Decimal("0.1")), "100"),), asks=(("107", "100"),))
    )
    runtime.paper_portfolio.on_book(
        book(3_250, bids=((str(tp2 + Decimal("0.05")), "100"),), asks=(("107", "100"),))
    )
    runtime.paper_portfolio.on_book(
        book(3_750, bids=((str(tp2 + Decimal("0.05")), "100"),), asks=(("107", "100"),))
    )
    runtime.flush_storage()

    assert ledger.count("candidates") == 1
    assert ledger.count("paper_orders") == 3
    assert ledger.count("fills") == 3
    assert ledger.count("trades") == 1
    assert ledger.count("shadow_trades") == 2
    assert ledger.count("execution_audit") > 0
    trade = ledger.list_trades(runtime.run_id)[0]
    run = ledger.get_run(runtime.run_id)
    assert run is not None
    assert trade["config_hash"] == run["config_hash"]
    assert Decimal(str(trade["net_pnl_usdt"])) == (
        Decimal(str(trade["gross_pnl_usdt"]))
        - Decimal(str(trade["fees_usdt"]))
        - Decimal(str(trade["slippage_usdt"]))
    )
    ledger.close()


def test_strategy_reports_include_empty_profiles_costs_pf_expectancy_and_confidence() -> None:
    win = _sample_trade()
    loss = _sample_trade(
        "trade-loss",
        net="-0.50",
        gross="-0.20",
        fees="0.10",
        slippage="0.20",
    )
    stress = {**_sample_trade("trade-stress"), "profile": "STRESS"}
    reports = TradeAnalytics().strategy_reports(
        [win, loss, stress],
        strategy_ids=("LSA_REVERSAL_V1", "CBR_CONTINUATION_V1"),
    )
    assert len(reports) == 4
    base = next(
        report
        for report in reports
        if report["strategy_id"] == "LSA_REVERSAL_V1" and report["profile"] == "BASE"
    )
    assert base["sample_size"] == 2
    assert base["win_rate"] == "0.5"
    assert base["profit_factor"] is not None
    assert base["expectancy_usdt"] == "0.4894"
    assert base["win_rate_ci95"] is not None
    assert base["sample_status"] == "표본 부족"
    assert base["recommendation"] == "관찰"
    assert base["stress_verified"] is True
    empty = next(
        report
        for report in reports
        if report["strategy_id"] == "CBR_CONTINUATION_V1"
        and report["profile"] == "STRESS"
    )
    assert empty["sample_size"] == 0
    assert empty["profit_factor"] is None


def test_replay_and_strategy_analytics_are_connected_to_http_api(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "api-ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-api-replay",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="api-depth", ts_ms=1_000)
    )
    client = TestClient(create_app(runtime))

    runs = client.get("/api/replay/runs")
    assert runs.status_code == 200
    assert runs.json()[0]["run_id"] == runtime.run_id
    replay = client.post(f"/api/replay/{runtime.run_id}", json={})
    assert replay.status_code == 200
    assert replay.json()["event_count"] == 1
    results = client.get("/api/replay/results")
    assert results.status_code == 200
    assert results.json()[0]["checksum"] == replay.json()["checksum"]
    analytics = client.get("/api/analytics/strategies")
    assert analytics.status_code == 200
    assert len(analytics.json()) == 8
    ledger.close()
