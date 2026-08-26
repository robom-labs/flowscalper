"""실제 PAPER 실행계좌가 재시작 경계를 넘어 보존되고 손상 시 fail-closed인지 검증한다."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Side, Venue
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
        runtime.paper_portfolio.on_book(
            replace(_live_book(1_250), symbol=symbol)
        )
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
    runtime.set_paused(True)
    plan = replace(
        candidate_plan(),
        run_id=run_id,
        venue=Venue.BINANCE_USDM,
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
    assert recovered_runtime.position_visible is True
    assert recovered_runtime.paused is True
    assert recovered_runtime._manual_pause_requested is True
    assert "ENTRY_LOCK_RECOVERY_REVALIDATION" in recovered_runtime.runtime_health_flags
    assert recovered_runtime._recovery_revalidation_symbol == "BTCUSDT"
    registry = {
        row["strategy_id"]: row for row in recovered_runtime.strategy_registry.rows()
    }
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
    recovered_runtime.paper_portfolio.on_book(
        _live_book(2_000, bid=str(tp1 + 1), ask=str(tp1 + 2))
    )
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
    history = recovered_runtime.strategy_registry.revision_history(
        "CBR_CONTINUATION_V1"
    )

    assert setting.mode is StrategyMode.ACTIVE
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
