"""실제 PAPER 실행계좌가 재시작 경계를 넘어 보존되고 손상 시 fail-closed인지 검증한다."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import (
    DataQuality,
    MarketDataState,
    MarketEvent,
    RuntimeMode,
    Side,
    Venue,
)
from backend.app.execution.trailing import (
    TrailingActivationRule,
    TrailingModel,
    TrailingPolicy,
)
from backend.app.main import _runtime_from_environment
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import SQLiteLedger
from backend.app.strategies.registry import StrategyMode
from backend.tests.test_candidate_paper_portfolio import book, candidate_plan
from backend.tests.test_strategy_league_portfolio import league_plan


def _live_book(ts_ms: int, *, bid: str = "99.9", ask: str = "100.1"):
    return replace(
        book(ts_ms, bids=((bid, "100"),), asks=((ask, "100"),)),
        venue=Venue.BINANCE_USDM,
    )


def _reopen_runtime(database: Path, run_id: str) -> tuple[PaperRuntime, SQLiteLedger]:
    ledger = SQLiteLedger(database)
    recovered = ledger.recover_latest(recovered_ts_ms=10_000)
    assert recovered is not None
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    assert runtime.restore_recovery_state(recovered) is True
    return runtime, ledger


def _live_depth_event(
    runtime: PaperRuntime,
    ts_ms: int,
    symbol: str = "BTCUSDT",
) -> MarketEvent:
    return MarketEvent(
        event_id=f"recovery-depth-{symbol}-{ts_ms}",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol=symbol,
        event_type="DEPTH_UPDATE",
        venue_ts_ms=ts_ms,
        receive_monotonic_ns=runtime.clock.monotonic_ns(),
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={
            "bid": "99.9",
            "bid_qty": "100",
            "ask": "100.1",
            "ask_qty": "100",
            "bids": [["99.9", "100"]],
            "asks": [["100.1", "100"]],
        },
    )


def test_trailing_mark_update_persists_latest_monotonic_stop_for_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "trailing-mark-recovery.sqlite3"
    run_id = "run-trailing-mark-recovery"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    plan = replace(
        candidate_plan(),
        run_id=run_id,
        venue=Venue.BINANCE_USDM,
        trailing_policy=TrailingPolicy(
            policy_id="RECOVERY_PERCENT_TRAIL_V1",
            model=TrailingModel.FIXED_RATE,
            activation_rule=TrailingActivationRule.R_MULTIPLE,
            activation_r=Decimal("1"),
            partial_tp_required=False,
            retracement_rate=Decimal("0.005"),
        ),
    )
    runtime.paper_portfolio.offer((plan,), entries_paused=False)
    runtime.paper_portfolio.on_book(_live_book(1_250))
    runtime._persist_execution_state(1_250)
    runtime.paper_portfolio.on_book(_live_book(2_000, bid="102", ask="102.1"))
    runtime._persist_execution_state(2_000)
    runtime.paper_portfolio.on_book(_live_book(2_050, bid="103", ask="103.1"))
    runtime._persist_execution_state(2_050)

    audits = ledger.list_execution_audits(run_id)
    mark = [
        row
        for row in audits
        if row["event"] == "TRAILING_MARK_UPDATED" and row["account_id"] == "MAIN:BASE"
    ]
    assert len(mark) == 1
    assert mark[0]["current_trail"] == "102.485"
    ledger.close()

    recovered_runtime, reopened = _reopen_runtime(database, run_id)
    recovered = recovered_runtime.paper_portfolio.main.position
    assert recovered is not None
    assert recovered.trailing_machine is not None
    assert recovered.trailing_machine.current_trail == Decimal("102.485")
    assert recovered.protected.current_stop == Decimal("102.485")
    reopened.close()


def test_dashboard_history_reads_do_not_wait_for_write_lock(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "history-read.sqlite3")
    completed = threading.Event()
    rows: list[tuple[int, int]] = []

    def read_history() -> None:
        rows.append((len(ledger.list_trades()), len(ledger.list_shadow_trades())))
        completed.set()

    with ledger._lock:
        reader = threading.Thread(target=read_history)
        reader.start()
        assert completed.wait(timeout=1)
    reader.join(timeout=1)
    assert rows == [(0, 0)]
    ledger.close()


def test_replay_result_reads_do_not_wait_for_write_lock(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "replay-read.sqlite3")
    completed = threading.Event()
    rows: list[int] = []

    def read_replays() -> None:
        rows.append(len(ledger.list_replay_runs()))
        completed.set()

    with ledger._lock:
        reader = threading.Thread(target=read_replays)
        reader.start()
        assert completed.wait(timeout=1)
    reader.join(timeout=1)
    assert rows == [0]
    ledger.close()


def test_existing_live_runtime_defers_full_history_cache_until_lifespan(
    tmp_path: Path,
) -> None:
    database = tmp_path / "deferred-history-cache.sqlite3"
    ledger = SQLiteLedger(database)
    first = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id="run-deferred-history-cache",
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    first._persist_execution_state(1_000)
    ledger.close()

    reopened = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id="run-deferred-history-cache",
        ledger=reopened,
        venue=Venue.BINANCE_USDM,
    )

    assert runtime.dashboard_trade_cache_ready is False
    assert runtime.dashboard_trade_cache_loading is False
    assert runtime.startup_trade_cache_ms == 0
    reopened.close()


def test_runtime_revalidates_every_recovered_league_symbol_before_unlock(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-multi-symbol-recovery.sqlite3"
    run_id = "run-recovery-multi"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    symbols = {"ADAUSDT", "BTCUSDT", "ETHUSDT"}
    plans = tuple(
        replace(
            league_plan("LSA_REVERSAL_V1", symbol, Side.LONG),
            run_id=run_id,
            venue=Venue.BINANCE_USDM,
        )
        for symbol in symbols
    )
    runtime.paper_portfolio.offer(plans, entries_paused=False)
    for symbol in symbols:
        runtime.paper_portfolio.on_book(replace(_live_book(1_250), symbol=symbol))
    runtime._persist_execution_state(1_250)
    ledger.close()

    recovered_runtime, reopened = _reopen_runtime(database, run_id)
    assert recovered_runtime._recovery_revalidation_symbols == symbols
    for index, symbol in enumerate(sorted(symbols), start=1):
        recovered_runtime.ingest_live_event(
            _live_depth_event(recovered_runtime, 1_500 + index, symbol)
        )
        assert symbol not in recovered_runtime._recovery_revalidation_symbols
        assert recovered_runtime.paused is (index < len(symbols))
    assert "ENTRY_LOCK_RECOVERY_REVALIDATION" not in recovered_runtime.runtime_health_flags
    reopened.close()


def test_runtime_recovers_registry_open_position_pending_exit_and_final_trade(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-recovery.sqlite3"
    run_id = "run-recovery-live"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    runtime.configure_strategy(
        "CBR_CONTINUATION_V1",
        mode=StrategyMode.SHADOW,
        long_enabled=False,
        short_enabled=True,
    )
    setting_transition = ledger.list_incidents(category="STRATEGY_SETTINGS_TRANSITION")[-1][
        "payload"
    ]
    assert setting_transition["previous_state"] == (
        "SHADOW|SHADOW|LONG=ON|SHORT=ON|MANUAL_LOCK=OFF"
    )
    assert setting_transition["new_state"] == ("SHADOW|SHADOW|LONG=OFF|SHORT=ON|MANUAL_LOCK=ON")
    assert setting_transition["actor"] == "USER_UI"
    assert setting_transition["request_revision"] == 0
    assert setting_transition["response_revision"] == 1
    assert setting_transition["reversible"] is True
    persisted_setting = [
        row
        for row in ledger.list_strategy_settings(run_id)
        if row["strategy_id"] == "CBR_CONTINUATION_V1"
    ][-1]
    assert persisted_setting["transition_id"] == setting_transition["transition_id"]
    assert persisted_setting["previous_state"] == setting_transition["previous_state"]
    assert persisted_setting["new_state"] == setting_transition["new_state"]
    runtime.set_paused(True)
    plan = replace(
        candidate_plan(),
        run_id=run_id,
        venue=Venue.BINANCE_USDM,
        strategy_version="OLD-STRATEGY-VERSION",
    )
    runtime.paper_portfolio.offer((plan,), entries_paused=False)
    runtime.paper_portfolio.on_book(_live_book(1_250))
    runtime._persist_execution_state(1_250)
    assert runtime.paper_portfolio.main.position is not None
    trade_id = runtime.paper_portfolio.main.position.protected.trade_id
    ledger.close()

    recovered_runtime, reopened = _reopen_runtime(database, run_id)
    assert recovered_runtime.paper_portfolio.lifecycle_state() == "PROTECTED"
    assert recovered_runtime.paper_portfolio.main.position is not None
    assert recovered_runtime.paper_portfolio.main.position.protected.trade_id == trade_id
    assert recovered_runtime.paper_portfolio.main.position.plan.strategy_version == (
        "OLD-STRATEGY-VERSION"
    )
    assert recovered_runtime.position_visible is True
    assert recovered_runtime.paused is True
    assert recovered_runtime._manual_pause_requested is True
    assert "ENTRY_LOCK_RECOVERY_REVALIDATION" in recovered_runtime.runtime_health_flags
    assert recovered_runtime._recovery_revalidation_symbol == "BTCUSDT"
    registry = {row["strategy_id"]: row for row in recovered_runtime.strategy_registry.rows()}
    assert registry["CBR_CONTINUATION_V1"]["mode"] == "SHADOW"
    assert registry["CBR_CONTINUATION_V1"]["settings_revision"] == 1
    assert registry["CBR_CONTINUATION_V1"]["manual_lock"] is True
    assert registry["CBR_CONTINUATION_V1"]["changed_by"] == "USER_UI"
    assert registry["CBR_CONTINUATION_V1"]["long_enabled"] is False

    recovered_runtime.ingest_live_event(_live_depth_event(recovered_runtime, 1_500))
    assert recovered_runtime._recovery_revalidation_symbol is None
    assert "ENTRY_LOCK_RECOVERY_REVALIDATION" not in recovered_runtime.runtime_health_flags
    assert recovered_runtime.paused is True

    tp1 = recovered_runtime.paper_portfolio.main.position.plan.take_profit_targets[0].price
    recovered_runtime.paper_portfolio.on_book(_live_book(2_000, bid=str(tp1 + 1), ask=str(tp1 + 2)))
    recovered_runtime._persist_execution_state(2_000)
    assert recovered_runtime.paper_portfolio.lifecycle_state() == "EXIT_PENDING"
    reopened.close()

    exit_pending_runtime, reopened_again = _reopen_runtime(database, run_id)
    assert exit_pending_runtime.paper_portfolio.lifecycle_state() == "EXIT_PENDING"
    exit_pending_runtime.paper_portfolio.on_book(
        _live_book(2_250, bid=str(tp1 + 1), ask=str(tp1 + 2))
    )
    assert exit_pending_runtime.paper_portfolio.main.position is not None
    tp2 = exit_pending_runtime.paper_portfolio.main.position.plan.take_profit_targets[1].price
    exit_pending_runtime.paper_portfolio.on_book(
        _live_book(3_000, bid=str(tp2 + 1), ask=str(tp2 + 2))
    )
    exit_pending_runtime.paper_portfolio.on_book(
        _live_book(3_250, bid=str(tp2 + 1), ask=str(tp2 + 2))
    )
    exit_pending_runtime._persist_execution_state(3_250)
    assert exit_pending_runtime.paper_portfolio.main.position is None
    assert len(reopened_again.list_trades(run_id)) == 1
    assert reopened_again.list_trades(run_id)[0]["trade_id"] == trade_id
    assert reopened_again.list_trades(run_id)[0]["strategy_version"] == ("OLD-STRATEGY-VERSION")
    shadow_rows = reopened_again.list_shadow_trades(run_id)
    assert shadow_rows
    assert {row["strategy_version"] for row in shadow_rows} == {"OLD-STRATEGY-VERSION"}
    performance = exit_pending_runtime.strategy_performance(include_persisted=True)
    base = next(
        row
        for row in performance
        if row["strategy_id"] == plan.strategy_id and row["profile"] == "BASE"
    )
    assert base["sample_size"] == 0
    assert base["excluded_prior_version_samples"] >= 1
    reopened_again.close()

    finalized_runtime, final_ledger = _reopen_runtime(database, run_id)
    assert finalized_runtime.paper_portfolio.main.position is None
    assert finalized_runtime.status().trade_count == 1
    assert len(final_ledger.list_orders(run_id)) == len(
        {row["order_id"] for row in final_ledger.list_orders(run_id)}
    )
    final_ledger.close()


def test_strategy_rollback_history_survives_process_restart(tmp_path: Path) -> None:
    database = tmp_path / "strategy-rollback-recovery.sqlite3"
    run_id = "run-strategy-rollback"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    runtime.configure_strategy(
        "CBR_CONTINUATION_V1",
        mode=StrategyMode.SHADOW,
        long_enabled=True,
        short_enabled=False,
        expected_revision=0,
    )
    runtime.rollback_strategy(
        "CBR_CONTINUATION_V1",
        target_revision=0,
        expected_revision=1,
        reason="USER_ROLLBACK_RECOVERY_TEST",
    )
    ledger.close()

    recovered_runtime, reopened = _reopen_runtime(database, run_id)
    setting = recovered_runtime.strategy_registry.setting("CBR_CONTINUATION_V1")
    history = recovered_runtime.strategy_registry.revision_history("CBR_CONTINUATION_V1")

    assert setting.mode is StrategyMode.SHADOW
    assert setting.revision == 2
    assert setting.short_enabled is True
    assert [row["settings_revision"] for row in history] == [0, 1, 2]
    reopened.close()


def test_paper_entry_intent_revision_survives_process_restart(tmp_path: Path) -> None:
    database = tmp_path / "paper-entry-intent-recovery.sqlite3"
    run_id = "run-paper-entry-intent-recovery"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    runtime.set_paused(
        True,
        expected_revision=0,
        idempotency_key="recovery-pause",
        reason="USER_PAUSE_RECOVERY_TEST",
    )
    runtime._persist_execution_state(1_250)
    ledger.close()

    recovered_runtime, reopened = _reopen_runtime(database, run_id)

    assert recovered_runtime.paper_entry_intent()["manual_pause_requested"] is True
    assert recovered_runtime.paper_entry_intent()["revision"] == 1
    assert recovered_runtime.paper_entry_intent()["reason"] == "USER_PAUSE_RECOVERY_TEST"
    repeated = recovered_runtime.set_paused(
        True,
        expected_revision=0,
        idempotency_key="recovery-pause",
    )
    assert repeated["revision"] == 1
    reopened.close()


@pytest.mark.parametrize(
    ("manual_pause_requested", "record_paused"),
    [("false", False), (False, "false")],
)
def test_paper_entry_intent_recovery_rejects_string_booleans(
    tmp_path: Path,
    manual_pause_requested: object,
    record_paused: object,
) -> None:
    database = tmp_path / "paper-entry-intent-strict-bool.sqlite3"
    run_id = "run-paper-entry-intent-strict-bool"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    runtime._persist_execution_state(1_250)
    ledger.set_app_setting(
        "paper_entry_user_intent",
        {
            "run_id": run_id,
            "manual_pause_requested": manual_pause_requested,
            "revision": 1,
            "actor": "RECOVERY_TEST",
            "reason": "STRICT_BOOL_TEST",
            "idempotency_records": [
                {"key": "strict-bool", "paused": record_paused}
            ],
        },
        updated_ts_ms=1_250,
    )
    ledger.close()

    reopened = SQLiteLedger(database)
    recovered = reopened.recover_latest(recovered_ts_ms=10_000)
    assert recovered is not None
    recovered_runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=reopened,
        venue=Venue.BINANCE_USDM,
    )

    assert recovered_runtime.restore_recovery_state(recovered) is False
    assert "RECOVERY_STATE_REJECTED:ValueError" in recovered_runtime.runtime_health_flags
    assert recovered_runtime.paused is True
    reopened.close()


def test_restart_recovery_transition_is_normalized_and_public(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "normalized-restart-recovery.sqlite3"
    run_id = "run-normalized-restart-recovery"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    runtime._persist_execution_state(1_250)
    ledger.close()

    monkeypatch.setenv("ROBOM_DB_PATH", str(database))
    monkeypatch.setenv("ROBOM_MODE", RuntimeMode.LIVE_SHADOW_PAPER.value)
    recovered_runtime = _runtime_from_environment()
    assert recovered_runtime.ledger is not None
    transition = recovered_runtime.ledger.list_incidents(category="PAPER_RESTART_RECOVERY")[-1]
    payload = transition["payload"]

    assert payload["transition_id"] == transition["incident_id"]
    assert payload["previous_state"] == "SCANNING"
    assert payload["new_state"] == "RECOVERY_REVALIDATION_LOCKED"
    assert payload["occurred_ts_ms"] == transition["ts_ms"]
    assert payload["cause"] == "PAPER_STATE_RECOVERED"
    assert payload["cause_code"] == "PAPER_STATE_RECOVERED"
    assert payload["description_ko"] == (
        "저장된 PAPER 상태를 복구했고 새 공개호가 확인 전까지 신규 진입을 잠갔습니다."
    )
    assert payload["actor"] == "RECOVERY"
    assert payload["run_id"] == run_id
    assert payload["strategy_id"] is None
    assert payload["account_id"] is None
    assert payload["symbol"] is None
    assert payload["request_revision"] == 0
    assert payload["response_revision"] == 1
    assert payload["reversible"] is True
    assert payload["lifecycle_state"] == "SCANNING"
    assert payload["recovery_ok"] is True
    assert payload["ignored_fail_closed_governance_setting_count"] == 0
    assert payload["ignored_fail_closed_governance_setting_tokens"] == []
    assert payload["ignored_fail_closed_governance_data_deleted"] is False
    assert (
        payload["ignored_fail_closed_governance_duplicate_revision_relaxed"]
        is False
    )
    assert payload["ignored_fail_closed_governance_revision_reservations"] == []
    assert payload["open_position"] is False
    diagnostics = recovered_runtime._operational_diagnostics()
    assert diagnostics["startup_recovery_transition_id"] == payload["transition_id"]
    assert diagnostics["startup_recovery_previous_state"] == "SCANNING"
    assert diagnostics["startup_recovery_state"] == "RECOVERY_REVALIDATION_LOCKED"
    assert diagnostics["startup_recovery_cause_code"] == "PAPER_STATE_RECOVERED"
    assert diagnostics["startup_recovery_actor"] == "RECOVERY"
    assert diagnostics["startup_recovery_run_id"] == run_id
    assert diagnostics["startup_recovery_reversible"] is True
    recovered_runtime.ledger.close()


def test_ready_mode_records_recovery_deferred_without_mutating_open_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "deferred-restart-recovery.sqlite3"
    run_id = "run-deferred-restart-recovery"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    runtime._persist_execution_state(1_250)
    ledger.close()

    monkeypatch.setenv("ROBOM_DB_PATH", str(database))
    monkeypatch.setenv("ROBOM_MODE", RuntimeMode.READY.value)
    ready_runtime = _runtime_from_environment()
    assert ready_runtime.mode is RuntimeMode.READY
    assert ready_runtime.run_id == "ready"
    assert ready_runtime.ledger is not None
    assert ready_runtime.ledger.get_run(run_id)["finalized_ts_ms"] is None
    transition = ready_runtime.ledger.list_incidents(category="PAPER_RESTART_RECOVERY")[-1]
    assert transition["run_id"] == run_id
    assert transition["payload"]["previous_state"] == "SCANNING"
    assert transition["payload"]["new_state"] == "RECOVERY_DEFERRED"
    assert transition["payload"]["cause_code"] == "RECOVERY_DEFERRED_READY_MODE"
    assert transition["payload"]["recovery_ok"] is False
    assert transition["payload"]["reversible"] is True
    ready_runtime.ledger.close()


def test_fixture_restart_is_never_described_as_live_revalidation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "fixture-restart-recovery.sqlite3"
    run_id = "run-fixture-restart-recovery"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        clock=DeterministicClock(),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.FIXTURE,
    )
    runtime._persist_execution_state(1_250)
    ledger.close()

    monkeypatch.setenv("ROBOM_DB_PATH", str(database))
    monkeypatch.setenv("ROBOM_MODE", RuntimeMode.DEMO_FIXTURE.value)
    fixture_runtime = _runtime_from_environment()
    assert fixture_runtime.ledger is not None
    transition = fixture_runtime.ledger.list_incidents(category="PAPER_RESTART_RECOVERY")[-1][
        "payload"
    ]
    assert transition["new_state"] == "FIXTURE_STATE_RECOVERED"
    assert transition["cause_code"] == "PAPER_FIXTURE_STATE_RECOVERED"
    assert transition["description_ko"] == (
        "저장된 오프라인 샘플 PAPER 상태를 복구했습니다. LIVE 시장데이터가 아닙니다."
    )
    assert "공개호가" not in transition["description_ko"]
    assert fixture_runtime.mode is RuntimeMode.DEMO_FIXTURE
    assert fixture_runtime.market_data_state is MarketDataState.FIXTURE
    fixture_runtime.ledger.close()


def test_corrupt_latest_snapshot_boots_ready_and_never_creates_a_new_trade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "corrupt-runtime.sqlite3"
    ledger = SQLiteLedger(database)
    ledger.start_run(
        "run-corrupt",
        mode=RuntimeMode.LIVE_SHADOW_PAPER.value,
        venue=Venue.BINANCE_USDM.value,
        config={"execution": "PAPER"},
        started_ts_ms=1_000,
    )
    ledger.save_snapshot(
        "run-corrupt",
        lifecycle_state="PROTECTED",
        ts_ms=1_100,
        payload={"portfolio": {"schema_version": 1}},
    )
    ledger.close()
    connection = sqlite3.connect(database)
    connection.execute("UPDATE snapshots SET payload_json = ?", ('{"tampered":true}',))
    connection.commit()
    connection.close()

    monkeypatch.setenv("ROBOM_DB_PATH", str(database))
    monkeypatch.setenv("ROBOM_MODE", RuntimeMode.LIVE_SHADOW_PAPER.value)
    runtime = _runtime_from_environment()
    assert runtime.mode is RuntimeMode.READY
    assert runtime.paused is True
    assert runtime.paper_portfolio.main.risk_state.faulted is True
    assert "RECOVERY_CHECKSUM_OR_SCHEMA_INVALID" in runtime.runtime_health_flags
    assert runtime.ledger is not None
    assert runtime.ledger.list_trades() == []
    assert runtime.ledger.list_runs()[0]["run_id"] == "run-corrupt"
    transition = runtime.ledger.list_incidents(category="PAPER_RESTART_RECOVERY")[-1]
    assert transition["run_id"] == "run-corrupt"
    assert transition["payload"]["transition_id"] == transition["incident_id"]
    assert transition["payload"]["previous_state"] == "OPEN_RUN_UNVERIFIED"
    assert transition["payload"]["new_state"] == "RECOVERY_FAIL_CLOSED"
    assert transition["payload"]["cause_code"] == ("RECOVERY_CHECKSUM_OR_SCHEMA_INVALID")
    assert transition["payload"]["actor"] == "RECOVERY"
    assert transition["payload"]["run_id"] == "run-corrupt"
    assert transition["payload"]["request_revision"] == 0
    assert transition["payload"]["response_revision"] == 1
    assert transition["payload"]["reversible"] is False
    assert transition["payload"]["requested_mode"] == "LIVE_SHADOW_PAPER"
    assert runtime.startup_recovery_audit == transition["payload"]
    assert runtime.startup_storage_init_ms >= 0
    assert runtime.startup_ledger_open_ms >= 0
    assert runtime.startup_recovery_lookup_ms >= 0
    assert runtime.startup_runtime_init_ms >= 0
    assert runtime.startup_recovery_restore_ms >= 0
    assert runtime.startup_total_ms >= runtime.startup_ledger_open_ms
    assert runtime.startup_portfolio_init_ms >= 0
    assert runtime.startup_trade_cache_ms >= 0
    assert runtime.startup_post_init_total_ms >= runtime.startup_trade_cache_ms
    assert runtime.dashboard_trade_cache_ready is False
    asyncio.run(runtime.warm_dashboard_trade_cache())
    assert runtime.dashboard_trade_cache_ready is True
    assert runtime.dashboard_trade_cache_loading is False
    assert runtime.dashboard_trade_cache_last_ms >= 0
    assert runtime.dashboard_trade_cache_completed_ts_ms is not None
    runtime.ledger.close()
