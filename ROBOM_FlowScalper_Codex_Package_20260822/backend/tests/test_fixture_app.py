"""오프라인 fixture가 PAPER 상태로 끝까지 부팅되는지 검증한다."""

import asyncio
import json
import threading
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketDataState, MarketEvent, RuntimeMode, Venue
from backend.app.live_public import LiveBootstrapResult, PublicDataUnavailable
from backend.app.main import create_app
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import SQLiteLedger


def _wait_control(client: TestClient, operation_id: str) -> dict[str, object]:
    for _ in range(50):
        operation = client.get(f"/api/control/operations/{operation_id}").json()
        if operation["state"] in {
            "COMPLETED",
            "FAILED_RETRYABLE",
            "FAILED_BLOCKED",
            "CANCELLED",
        }:
            return operation
        time.sleep(0.01)
    raise AssertionError("control operation이 종료되지 않았습니다.")


def test_fixture_boot_is_honestly_labeled() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        clock=DeterministicClock(),
        run_id="run-test",
    )
    runtime.boot_fixture()
    client = TestClient(create_app(runtime))

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "DEMO_FIXTURE",
        "market_data_state": "FIXTURE",
        "execution_state": "PAPER",
        "venue": "FIXTURE",
        "run_id": "run-test",
        "real_orders_enabled": False,
        "auth_required": False,
        "starting_equity_usdt": 1000.0,
        "current_equity_usdt": 1000.0,
        "realized_pnl_usdt": 0.0,
        "unrealized_pnl_usdt": 0.0,
        "cumulative_fees_usdt": 0.0,
        "cumulative_slippage_usdt": 0.0,
        "trade_count": 0,
        "wide_symbols": 10,
        "deep_symbols": 10,
        "processing_lag_p95_ms": None,
        "health_flags": ["OFFLINE_DEMO_ISOLATED"],
    }


def test_fixture_events_are_deterministic() -> None:
    first = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE, clock=DeterministicClock(), run_id="run-a"
    )
    second = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE, clock=DeterministicClock(), run_id="run-a"
    )
    first.boot_fixture(20)
    second.boot_fixture(20)
    assert first.events == second.events
    assert all(not event.quality.is_live for event in first.events)


def test_start_demo_run_clears_live_only_telemetry() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id="run-live-before-demo",
    )
    runtime.wide_symbol_count = 50
    runtime.deep_symbol_count = 20
    runtime.processing_lag_p95_ms = 16_250
    runtime.live_selection = object()  # type: ignore[assignment]

    runtime.start_demo_run()

    status = runtime.status()
    assert status.mode is RuntimeMode.DEMO_FIXTURE
    assert status.wide_symbols == 10
    assert status.deep_symbols == 10
    assert status.processing_lag_p95_ms is None
    assert runtime.live_selection is None


def test_dashboard_controls_preserve_run_history() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        clock=DeterministicClock(),
        run_id="run-original",
    )
    runtime.boot_fixture()
    with TestClient(create_app(runtime)) as client:
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["chart"]["lines"].keys() == {
            "entry",
            "take_profit",
            "take_profit_2",
            "stop",
        }
        assert dashboard["position"]["elapsed_seconds"] == 121
        assert dashboard["status"]["market_data_state"] == "FIXTURE"
        assert dashboard["chart"]["interval"] == "3m"
        assert len(dashboard["chart"]["candles"]) >= 3

        interval_chart = client.post(
            "/api/control/chart",
            json={"symbol": "BTCUSDT", "interval_seconds": 15},
        ).json()["chart"]
        assert interval_chart["interval"] == "15s"
        assert len(interval_chart["candles"]) >= 14
        assert all(float(row["close"]) > 0 for row in interval_chart["candles"])

        assert client.post("/api/control/pause").json()["paused"] is True
        assert client.post("/api/control/resume").json()["paused"] is False
        assert client.post("/api/control/emergency-close").json()["position"] is None
        submitted = client.post("/api/control/new-run")
        assert submitted.status_code == 202
        _wait_control(client, submitted.json()["operation_id"])
        new_snapshot = client.get("/api/dashboard").json()
    assert new_snapshot["status"]["run_id"] != "run-original"
    assert new_snapshot["history"][0]["run_id"] == "run-original"


def test_strategy_configuration_api_is_explicit_and_validated() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    client = TestClient(create_app(runtime))
    dashboard = client.get("/api/dashboard").json()
    assert len(dashboard["strategies"]) == len(runtime.strategy_registry.strategy_ids)
    assert dashboard["operation_status"]["state"] == "READY"
    assert dashboard["operation_status"]["recommended_action"] == "START"

    changed = client.post(
        "/api/strategies/VWAP_EXHAUSTION_REVERSION_V1",
        json={
            "mode": "SHADOW",
            "long_enabled": True,
            "short_enabled": False,
            "expected_revision": 0,
        },
    )
    assert changed.status_code == 200
    row = next(
        item
        for item in changed.json()["strategies"]
        if item["strategy_id"] == "VWAP_EXHAUSTION_REVERSION_V1"
    )
    assert (row["mode"], row["long_enabled"], row["short_enabled"]) == (
        "SHADOW",
        True,
        False,
    )
    assert row["settings_revision"] == 1
    assert row["manual_lock"] is True
    assert row["changed_by"] == "USER_UI"
    assert row["lifecycle"] == "SHADOW"
    assert row["governance"]["evidence_status"] == "NOT_PROVEN"
    governance = client.get("/api/governance")
    assert governance.status_code == 200
    assert governance.json()["champion_id"] == "CBR_CONTINUATION_V1"
    assert len(governance.json()["rows"]) == len(runtime.strategy_registry.strategy_ids)
    stale = client.post(
        "/api/strategies/VWAP_EXHAUSTION_REVERSION_V1",
        json={
            "mode": "OFF",
            "long_enabled": True,
            "short_enabled": False,
            "expected_revision": 0,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_strategy"]["settings_revision"] == 1
    rollback = client.post(
        "/api/strategies/VWAP_EXHAUSTION_REVERSION_V1/rollback",
        json={
            "target_revision": 0,
            "expected_revision": 1,
            "reason": "USER_UNDO_TEST",
        },
    )
    assert rollback.status_code == 200
    restored = next(
        item
        for item in rollback.json()["strategies"]
        if item["strategy_id"] == "VWAP_EXHAUSTION_REVERSION_V1"
    )
    assert restored["settings_revision"] == 2
    assert restored["short_enabled"] is True
    assert [
        item["settings_revision"] for item in restored["governance"]["change_history"]
    ] == [0, 1, 2]
    assert client.post(
        "/api/strategies/UNKNOWN",
        json={
            "mode": "OFF",
            "long_enabled": False,
            "short_enabled": False,
            "expected_revision": 0,
        },
    ).status_code == 404


def test_live_strategy_analytics_api_uses_nonblocking_runtime_cache(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id="run-live-analytics-cache",
    )
    observed: list[tuple[str, bool]] = []

    def strategy_performance(
        _runtime: PaperRuntime,
        *,
        include_persisted: bool = True,
    ) -> list[dict[str, object]]:
        observed.append(("strategies", include_persisted))
        return []

    def strategy_symbol_performance(
        _runtime: PaperRuntime,
        *,
        include_persisted: bool = True,
    ) -> list[dict[str, object]]:
        observed.append(("symbols", include_persisted))
        return []

    def strategy_analytics_scope(
        _runtime: PaperRuntime,
        *,
        include_persisted: bool = True,
    ) -> dict[str, object]:
        observed.append(("scope", include_persisted))
        return {
            "analysis_scope": "CURRENT_STRATEGY_VERSION",
            "strategy_version": "test-version",
            "excluded_prior_version_samples": 0,
        }

    monkeypatch.setattr(PaperRuntime, "strategy_performance", strategy_performance)
    monkeypatch.setattr(PaperRuntime, "strategy_symbol_performance", strategy_symbol_performance)
    monkeypatch.setattr(PaperRuntime, "strategy_analytics_scope", strategy_analytics_scope)
    client = TestClient(create_app(runtime))

    assert client.get("/api/analytics/strategies").status_code == 200
    assert client.get("/api/analytics/strategy-symbols").status_code == 200
    assert observed == [
        ("strategies", False),
        ("symbols", False),
        ("scope", False),
    ]


def test_dashboard_broadcaster_serves_multiple_local_clients() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    with TestClient(create_app(runtime)) as client:
        headers = {"origin": "http://127.0.0.1:8870"}
        with client.websocket_connect("/ws", headers=headers) as first:
            with client.websocket_connect("/ws", headers=headers) as second:
                first_payload = first.receive_json()
                second_payload = second.receive_json()

    assert first_payload["type"] == "dashboard"
    assert second_payload["type"] == "dashboard"
    assert first_payload["data"]["status"]["mode"] == "READY"
    assert second_payload["data"]["system"]["auth_headers"] is False


def test_ready_api_does_not_wait_for_historical_trade_cache(monkeypatch) -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    started = threading.Event()
    release = threading.Event()

    async def slow_warm(active_runtime: PaperRuntime) -> None:
        started.set()
        await asyncio.to_thread(release.wait, 2)
        active_runtime.dashboard_trade_cache_loading = False
        active_runtime.dashboard_trade_cache_ready = True

    monkeypatch.setattr(PaperRuntime, "warm_dashboard_trade_cache", slow_warm)
    with TestClient(create_app(runtime)) as client:
        assert started.wait(timeout=1)
        requested_at = time.monotonic()
        status = client.get("/api/status")
        elapsed = time.monotonic() - requested_at
        dashboard = client.get("/api/dashboard").json()
        assert status.status_code == 200
        assert elapsed < 0.5
        assert dashboard["system"]["dashboard_trade_cache_loading"] is True
        assert dashboard["system"]["dashboard_trade_cache_ready"] is False
        release.set()


async def test_slow_replay_listing_never_blocks_live_status(monkeypatch) -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    started = threading.Event()
    release = threading.Event()

    def slow_replay_listing(_runtime: PaperRuntime) -> list[dict[str, object]]:
        started.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(PaperRuntime, "replayable_runs", slow_replay_listing)
    transport = httpx.ASGITransport(app=create_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        replay_request = asyncio.create_task(client.get("/api/replay/runs"))
        assert await asyncio.to_thread(started.wait, 1)
        status = await asyncio.wait_for(client.get("/api/status"), timeout=0.25)
        release.set()
        replay_response = await replay_request

    assert status.status_code == 200
    assert replay_response.status_code == 200


def test_persistent_run_reset_finalizes_old_run_without_deleting_history(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "runtime.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        clock=DeterministicClock(),
        run_id="run-persisted",
        ledger=ledger,
    )
    runtime.boot_fixture()
    with TestClient(create_app(runtime)) as client:
        before = client.get("/api/dashboard").json()
        run_config = json.loads(ledger.get_run("run-persisted")["config_json"])
        submitted = client.post("/api/control/new-run")
        assert submitted.status_code == 202
        _wait_control(client, submitted.json()["operation_id"])
        after = client.get("/api/dashboard").json()
        assert before["history"][0]["trade_id"] == "run-persisted-fixture-trade-001"
        assert run_config["app_version"] == "0.2.0-paper"
        assert "LSA_REVERSAL_V1" in run_config["strategy_version"]
        assert run_config["sample_type"] == "DEMO_FIXTURE"
        assert run_config["git_commit"]
        assert after["status"]["run_id"] != "run-persisted"
        assert {row["run_id"] for row in after["history"]} == {
            "run-persisted",
            after["status"]["run_id"],
        }
        assert ledger.get_run("run-persisted")["finalized_ts_ms"] is not None
        assert ledger.count("trades") == 2
        assert ledger.count("transitions") == 10
        assert ledger.count("snapshots") == 4
        assert ledger.count("paper_orders") == 4
        assert ledger.count("fills") == 4
        orders = ledger.list_orders("run-persisted")
        fills = ledger.list_fills("run-persisted")
        trade = ledger.list_trades("run-persisted")[0]
        assert trade["config_hash"] == ledger.get_run("run-persisted")["config_hash"]
        assert [order["intent"] for order in orders] == ["ENTRY_IOC", "TAKE_PROFIT"]
        assert [(fill["planned_price"], fill["price"]) for fill in fills] == [
            ("100.00", "100.10"),
            ("102.00", "101.90"),
        ]
        assert sum(Decimal(fill["fee_usdt"]) for fill in fills) == Decimal(
            trade["fees_usdt"]
        )
        assert sum(Decimal(fill["slippage_usdt"]) for fill in fills) == Decimal(
            trade["slippage_usdt"]
        )
        transition_times = [
            transition["ts_ms"] for transition in ledger.list_transitions("run-persisted")
        ]
        assert transition_times == sorted(transition_times)
        assert transition_times[2] == orders[0]["created_ts_ms"]
        assert transition_times[3] == fills[0]["ts_ms"]
        assert transition_times[-1] == fills[-1]["ts_ms"]


async def test_fresh_live_run_starts_zero_and_excludes_demo_performance(tmp_path: Path) -> None:
    clock = DeterministicClock()
    ledger = SQLiteLedger(tmp_path / "fresh-live.sqlite3")
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=clock, ledger=ledger)
    runtime.start_demo_run()
    assert ledger.count("trades") == 1

    class VerifiedProbe:
        async def bootstrap(
            self, venue: Venue, *, run_id: str, clock: DeterministicClock
        ) -> LiveBootstrapResult:
            return _verified_live_result(venue, run_id, clock, lag_ms=25)

    assert await runtime.start_live_run(VerifiedProbe())
    status = runtime.status()
    dashboard = runtime.dashboard()

    assert status.mode is RuntimeMode.LIVE_SHADOW_PAPER
    assert status.starting_equity_usdt == 1000
    assert status.current_equity_usdt == 1000
    assert status.realized_pnl_usdt == 0
    assert status.unrealized_pnl_usdt == 0
    assert status.cumulative_fees_usdt == 0
    assert status.cumulative_slippage_usdt == 0
    assert status.trade_count == 0
    assert dashboard["history"] == []
    assert dashboard["performance"] == {
        "sample_size": 0,
        "gross_pnl": "0",
        "fees": "0",
        "slippage": "0",
        "net_pnl": "0",
        "max_drawdown": "0",
        "win_rate": "표본 없음",
        "calibration": "CALIBRATING",
        "base_equity": "1000",
        "stress_equity": "표본 없음",
    }
    assert ledger.count("trades") == 1
    assert ledger.list_trades()[0]["sample_type"] == "DEMO_FIXTURE"
    ledger.close()


async def test_ready_start_finalizes_all_superseded_open_runs(tmp_path: Path) -> None:
    clock = DeterministicClock()
    ledger = SQLiteLedger(tmp_path / "superseded-runs.sqlite3")
    for index, run_id in enumerate(("run-stale-a", "run-stale-b"), start=1):
        ledger.start_run(
            run_id,
            mode=RuntimeMode.LIVE_SHADOW_PAPER.value,
            venue=Venue.BINANCE_USDM.value,
            config={"strategy_version": f"stale-{index}"},
            started_ts_ms=index,
        )
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=clock, ledger=ledger)

    class VerifiedProbe:
        async def bootstrap(
            self,
            venue: Venue,
            *,
            run_id: str,
            clock: DeterministicClock,
        ) -> LiveBootstrapResult:
            return _verified_live_result(venue, run_id, clock, lag_ms=25)

    assert await runtime.start_live_run(VerifiedProbe())

    assert ledger.get_run("run-stale-a")["finalized_ts_ms"] == clock.utc_ms()
    assert ledger.get_run("run-stale-b")["finalized_ts_ms"] == clock.utc_ms()
    assert set(runtime.archived_run_ids) == {"run-stale-a", "run-stale-b"}
    recovered = ledger.recover_latest(recovered_ts_ms=clock.utc_ms())
    assert recovered is not None
    assert recovered.run_id == runtime.run_id
    ledger.close()


async def test_ready_start_preserves_recoverable_open_paper_exposure(tmp_path: Path) -> None:
    clock = DeterministicClock()
    ledger = SQLiteLedger(tmp_path / "recoverable-open-run.sqlite3")
    ledger.start_run(
        "run-open-paper",
        mode=RuntimeMode.LIVE_SHADOW_PAPER.value,
        venue=Venue.BINANCE_USDM.value,
        config={"strategy_version": "recoverable"},
        started_ts_ms=1,
    )
    ledger.save_snapshot(
        "run-open-paper",
        lifecycle_state="PROTECTED",
        ts_ms=2,
        payload={
            "open_position": {"symbol": "BTCUSDT"},
            "portfolio": {"accounts": []},
        },
    )
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=clock, ledger=ledger)

    assert runtime.live_start_block() == (
        "RECOVERY_OPEN_PAPER_EXPOSURE",
        "이전 Run에 복구할 PAPER 진입 또는 포지션이 있어 새 Run 시작을 차단했습니다.",
    )
    with pytest.raises(ValueError, match="복구할 PAPER"):
        await runtime.start_live_run()
    assert ledger.get_run("run-open-paper")["finalized_ts_ms"] is None
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
        "take_profit_2": None,
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
