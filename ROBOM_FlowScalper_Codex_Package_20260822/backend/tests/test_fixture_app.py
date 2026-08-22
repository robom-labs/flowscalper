"""오프라인 fixture가 PAPER 상태로 끝까지 부팅되는지 검증한다."""

import json
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketDataState, MarketEvent, RuntimeMode, Venue
from backend.app.live_public import LiveBootstrapResult, PublicDataUnavailable
from backend.app.main import create_app
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import SQLiteLedger


def test_fixture_boot_is_honestly_labeled() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.FIXTURE_OFFLINE,
        clock=DeterministicClock(),
        run_id="run-test",
    )
    runtime.boot_fixture()
    client = TestClient(create_app(runtime))

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "FIXTURE_OFFLINE",
        "market_data_state": "FIXTURE",
        "execution_state": "PAPER",
        "venue": "FIXTURE",
        "run_id": "run-test",
        "real_orders_enabled": False,
        "auth_required": False,
        "starting_equity_usdt": 1000.0,
        "current_equity_usdt": 1000.0,
        "wide_symbols": 10,
        "deep_symbols": 10,
        "processing_lag_p95_ms": None,
        "health_flags": ["OFFLINE_SIMULATION"],
    }


def test_fixture_events_are_deterministic() -> None:
    first = PaperRuntime(clock=DeterministicClock(), run_id="run-a")
    second = PaperRuntime(clock=DeterministicClock(), run_id="run-a")
    first.boot_fixture(20)
    second.boot_fixture(20)
    assert first.events == second.events
    assert all(not event.quality.is_live for event in first.events)


def test_dashboard_controls_preserve_run_history() -> None:
    runtime = PaperRuntime(clock=DeterministicClock(), run_id="run-original")
    runtime.boot_fixture()
    client = TestClient(create_app(runtime))

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["chart"]["lines"].keys() == {"entry", "take_profit", "stop"}
    assert dashboard["position"]["elapsed_seconds"] == 121
    assert dashboard["status"]["market_data_state"] == "FIXTURE"

    assert client.post("/api/control/pause").json()["paused"] is True
    assert client.post("/api/control/resume").json()["paused"] is False
    assert client.post("/api/control/emergency-close").json()["position"] is None
    new_snapshot = client.post("/api/control/new-run").json()
    assert new_snapshot["status"]["run_id"] != "run-original"
    assert new_snapshot["history"][0]["run_id"] == "run-original"


def test_persistent_run_reset_finalizes_old_run_without_deleting_history(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "runtime.sqlite3")
    runtime = PaperRuntime(clock=DeterministicClock(), run_id="run-persisted", ledger=ledger)
    runtime.boot_fixture()
    client = TestClient(create_app(runtime))

    before = client.get("/api/dashboard").json()
    run_config = json.loads(ledger.get_run("run-persisted")["config_json"])
    after = client.post("/api/control/new-run").json()

    assert before["history"][0]["trade_id"] == "run-persisted-fixture-trade-001"
    assert run_config["app_version"] == "0.1.0-paper"
    assert "LSA_REVERSAL_V1" in run_config["strategy_version"]
    assert run_config["git_commit"]
    assert after["status"]["run_id"] != "run-persisted"
    assert {row["run_id"] for row in after["history"]} == {
        "run-persisted",
        after["status"]["run_id"],
    }
    assert ledger.get_run("run-persisted")["finalized_ts_ms"] is not None
    assert ledger.count("trades") == 2
    assert ledger.count("transitions") == 10
    assert ledger.count("snapshots") == 2
    assert ledger.count("paper_orders") == 4
    assert ledger.count("fills") == 4
    orders = ledger.list_orders("run-persisted")
    fills = ledger.list_fills("run-persisted")
    trade = ledger.list_trades("run-persisted")[0]
    assert [order["intent"] for order in orders] == ["ENTRY_IOC", "TAKE_PROFIT"]
    assert [(fill["planned_price"], fill["price"]) for fill in fills] == [
        ("100.00", "100.10"),
        ("102.00", "101.90"),
    ]
    assert sum(Decimal(fill["fee_usdt"]) for fill in fills) == Decimal(trade["fees_usdt"])
    assert sum(Decimal(fill["slippage_usdt"]) for fill in fills) == Decimal(trade["slippage_usdt"])
    transition_times = [
        transition["ts_ms"] for transition in ledger.list_transitions("run-persisted")
    ]
    assert transition_times == sorted(transition_times)
    assert transition_times[2] == orders[0]["created_ts_ms"]
    assert transition_times[3] == fills[0]["ts_ms"]
    assert transition_times[-1] == fills[-1]["ts_ms"]
    ledger.close()


async def test_live_status_requires_verified_event_and_failover_starts_new_run() -> None:
    clock = DeterministicClock()
    runtime = PaperRuntime(mode=RuntimeMode.LIVE_SHADOW_PAPER, clock=clock, run_id="run-binance")
    initial = runtime.status()
    assert initial.market_data_state is MarketDataState.DISCONNECTED
    assert initial.venue is Venue.BINANCE_USDM
    assert initial.health_flags == (
        "ENTRY_LOCK_DATA_NOT_VERIFIED",
        "PAPER_ENTRIES_PAUSED",
    )

    class FailoverProbe:
        async def bootstrap(
            self, venue: Venue, *, run_id: str, clock: DeterministicClock
        ) -> LiveBootstrapResult:
            if venue is Venue.BINANCE_USDM:
                raise PublicDataUnavailable("fixture network failure")
            return _verified_live_result(venue, run_id, clock, lag_ms=120)

    assert await runtime.boot_live_public(FailoverProbe())
    status = runtime.status()
    assert status.market_data_state is MarketDataState.LIVE
    assert status.venue is Venue.BYBIT_LINEAR
    assert status.run_id != "run-binance"
    assert status.wide_symbols == 50
    assert status.deep_symbols == 10
    assert status.processing_lag_p95_ms == 120
    assert status.auth_required is False
    assert all(event.quality.is_live for event in runtime.events)
    dashboard = runtime.dashboard()
    assert dashboard["position"] is None
    assert dashboard["history"] == []
    assert dashboard["performance"]["sample_size"] == 0
    assert dashboard["chart"]["fixture"] is False
    assert dashboard["chart"]["lines"] == {
        "entry": None,
        "take_profit": None,
        "stop": None,
    }
    assert all(row["status"] == "CALIBRATING" for row in dashboard["scanner"])
    assert all(row["score"] is None for row in dashboard["scanner"])
    assert all("fixture" not in log["message"] for log in dashboard["logs"])


async def test_live_failure_and_critical_lag_keep_entries_paused() -> None:
    clock = DeterministicClock()

    class FailedProbe:
        async def bootstrap(
            self, venue: Venue, *, run_id: str, clock: DeterministicClock
        ) -> LiveBootstrapResult:
            raise PublicDataUnavailable(f"{venue.value} unavailable")

    failed = PaperRuntime(mode=RuntimeMode.LIVE_SHADOW_PAPER, clock=clock, run_id="run-failed")
    assert not await failed.boot_live_public(FailedProbe())
    failed.set_paused(False)
    assert failed.status().market_data_state is MarketDataState.DISCONNECTED
    assert failed.paused
    assert "PUBLIC_DATA_UNAVAILABLE" in failed.status().health_flags

    class LaggedProbe:
        async def bootstrap(
            self, venue: Venue, *, run_id: str, clock: DeterministicClock
        ) -> LiveBootstrapResult:
            return _verified_live_result(venue, run_id, clock, lag_ms=8_500)

    lagged = PaperRuntime(mode=RuntimeMode.LIVE_SHADOW_PAPER, clock=clock, run_id="run-lagged")
    assert await lagged.boot_live_public(LaggedProbe())
    assert lagged.status().market_data_state is MarketDataState.LIVE
    assert lagged.paused
    assert "CRITICAL_MARKET_LAG_ENTRY_LOCK" in lagged.status().health_flags
    lagged.set_paused(False)
    assert lagged.paused


def _verified_live_result(
    venue: Venue,
    run_id: str,
    clock: DeterministicClock,
    *,
    lag_ms: float,
) -> LiveBootstrapResult:
    event = MarketEvent(
        event_id="verified-public-event",
        run_id=run_id,
        venue=venue,
        symbol="BTCUSDT",
        event_type="ORDERBOOK",
        venue_ts_ms=clock.utc_ms() - int(lag_ms),
        receive_monotonic_ns=clock.monotonic_ns(),
        sequence_start=1,
        sequence_end=1,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=lag_ms,
        ),
        data={"bid": "100", "bid_qty": "2", "ask": "100.1", "ask_qty": "2"},
    )
    return LiveBootstrapResult(
        venue=venue,
        events=(event,),
        eligible_symbol_count=527,
        wide_symbol_count=50,
        deep_symbol_count=10,
        websocket_lag_ms=lag_ms,
        selected_symbol="BTCUSDT",
    )
