"""v0.2 시장이벤트 원장·종단 리플레이·전략 성과표를 검증한다."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

import backend.app.main as main_module
import backend.app.replay.process as replay_process_module
from backend.app.analytics.reports import TradeAnalytics
from backend.app.build_identity import STRATEGY_IDS, STRATEGY_VERSION
from backend.app.candidates import PlanBuildResult
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import (
    DataQuality,
    MarketDataState,
    MarketEvent,
    RuntimeMode,
    Venue,
)
from backend.app.main import create_app
from backend.app.market_data.supervisor import ProviderSelection
from backend.app.regime import Regime
from backend.app.replay.engine import ReplayEngine
from backend.app.replay.market import StoredMarketReplay, _candidate_plan_count
from backend.app.replay.process import (
    _REPLAY_TARGET_ARCHIVE_READ_BYTES_PER_SECOND,
    _REPLAY_TARGET_CPU_RATIO,
    _ReplayCpuBudget,
)
from backend.app.replay.safety import (
    ReplayLiveSafetyThresholds,
    run_with_live_safety,
)
from backend.app.replay.timeline import build_replay_preview
from backend.app.runtime import PaperRuntime
from backend.app.storage.io_priority import storage_io_priority_gate
from backend.app.storage.parquet import ParquetEventStore
from backend.app.storage.sqlite import (
    LedgerInvariantError,
    SQLiteLedger,
    persist_archives_and_candles_in_process,
    run_passive_wal_checkpoint_in_process,
)
from backend.app.strategies.runtime_evaluator import EvaluatedSignal
from backend.tests.test_candidate_paper_portfolio import (
    book,
    candidate_plan,
    qualified_decision,
)
from backend.tests.test_storage_replay_analytics import _sample_trade
from backend.tests.test_strategies import features


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


def candle_row(
    run_id: str,
    *,
    close: str = "100.1",
    open_ts_ms: int = 1_000,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "symbol": "BTCUSDT",
        "interval_seconds": 1,
        "open_ts_ms": open_ts_ms,
        "open": "100",
        "high": "100.2",
        "low": "99.9",
        "close": close,
        "volume": "1",
    }


def test_replay_candidate_count_deduplicates_main_and_league_accounts() -> None:
    audits = [
        {"event": "MAIN_CANDIDATE_SELECTED", "candidate_id": "candidate-1"},
        {"event": "LEAGUE_CANDIDATE_ARMED", "candidate_id": "candidate-1"},
        {"event": "LEAGUE_CANDIDATE_ARMED", "candidate_id": "candidate-1"},
        {"event": "LEAGUE_CANDIDATE_ARMED", "candidate_id": "candidate-2"},
        {"event": "ENTRY_FILLED", "candidate_id": "candidate-2"},
    ]
    assert _candidate_plan_count(audits) == 2


def test_cooperative_market_replay_preserves_canonical_checksum() -> None:
    events = [
        market_event("run-streaming", event_id="event-2", ts_ms=2_000).model_dump(mode="json"),
        market_event("run-streaming", event_id="event-1", ts_ms=1_000).model_dump(mode="json"),
    ]
    engine = ReplayEngine()
    baseline = engine.replay_market_path(
        events,
        config={"seed": 20260822},
        strategy_version="test-version",
        seed=20260822,
        decision_path=("SUMMARY:evaluated=0:qualified=0",),
        final_state="OBSERVING_NO_MAIN_TRADE",
    )
    checkpoints = 0

    def cooperate() -> None:
        nonlocal checkpoints
        checkpoints += 1

    cooperative = engine.replay_market_path(
        events,
        config={"seed": 20260822},
        strategy_version="test-version",
        seed=20260822,
        decision_path=("SUMMARY:evaluated=0:qualified=0",),
        final_state="OBSERVING_NO_MAIN_TRADE",
        cooperative_yield=cooperate,
    )

    assert cooperative == baseline
    assert checkpoints >= 2


def test_market_replay_and_ledger_use_observed_receive_order(tmp_path: Path) -> None:
    received_first = market_event(
        "run-receive-order",
        event_id="received-first",
        ts_ms=2_000,
    ).model_copy(update={"receive_monotonic_ns": 10})
    received_second = market_event(
        "run-receive-order",
        event_id="received-second",
        ts_ms=1_000,
    ).model_copy(update={"receive_monotonic_ns": 20})
    events = [
        received_second.model_dump(mode="json"),
        received_first.model_dump(mode="json"),
    ]
    events[0]["receive_ts_ms"] = 4_000
    events[1]["receive_ts_ms"] = 3_000

    digest = ReplayEngine().replay_market_path(
        events,
        config={"seed": 20260822},
        strategy_version="receive-order-test",
        seed=20260822,
        decision_path=(),
        final_state="OBSERVED_RECEIVE_ORDER",
    )
    assert digest.first_ts_ms == 2_000
    assert digest.last_ts_ms == 1_000

    ledger = SQLiteLedger(tmp_path / "receive-order.sqlite3")
    ledger.start_run(
        "run-receive-order",
        mode="REPLAY",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    assert ledger.record_market_events(events) == 2
    assert [row["event_id"] for row in ledger.list_market_events("run-receive-order")] == [
        "received-first",
        "received-second",
    ]
    ledger.close()


def test_market_replay_event_limit_freezes_open_run_input_scope(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "event-limit-replay.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-event-limit",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ingest_live_event(market_event(runtime.run_id, event_id="event-1", ts_ms=1_000))
    runtime.ingest_live_event(market_event(runtime.run_id, event_id="event-2", ts_ms=2_000))
    runtime.flush_storage()

    first = StoredMarketReplay().run(
        ledger,
        source_run_id=runtime.run_id,
        created_ts_ms=2_100,
        event_limit=2,
    )
    runtime.ingest_live_event(market_event(runtime.run_id, event_id="event-3", ts_ms=3_000))
    runtime.flush_storage()
    second = StoredMarketReplay().run(
        ledger,
        source_run_id=runtime.run_id,
        created_ts_ms=3_100,
        event_limit=2,
    )

    assert first.event_count == second.event_count == 2
    assert first.input_checksum == second.input_checksum
    assert first.checksum == second.checksum
    assert len(first.input_checksum) == 64
    ledger.close()


def test_replay_cpu_budget_yields_without_carrying_unbounded_sleep_debt() -> None:
    assert _REPLAY_TARGET_CPU_RATIO == 0.05
    wall_values = iter((0.0, 1.0, 2.5))
    cpu_values = iter((0.0, 1.0, 1.1))
    sleeps: list[float] = []
    budget = _ReplayCpuBudget(
        target_cpu_ratio=0.25,
        max_sleep_seconds=0.50,
        monotonic=lambda: next(wall_values),
        process_time=lambda: next(cpu_values),
        sleeper=sleeps.append,
    )

    budget.checkpoint()
    budget.checkpoint()

    assert sleeps == [0.50]


def test_replay_archive_budget_throttles_bytes_independently_from_cpu() -> None:
    assert _REPLAY_TARGET_ARCHIVE_READ_BYTES_PER_SECOND == 256 * 1024
    wall_values = iter((0.0, 1.0))
    cpu_values = iter((0.0, 0.0))
    sleeps: list[float] = []
    budget = _ReplayCpuBudget(
        target_cpu_ratio=0.25,
        target_archive_read_bytes_per_second=1_000,
        monotonic=lambda: next(wall_values),
        process_time=lambda: next(cpu_values),
        sleeper=sleeps.append,
    )

    budget.archive_checkpoint(250)

    assert sleeps == [0.25]


def test_storage_io_priority_gate_blocks_replay_read_for_live_exclusive_writer(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "priority-gate.sqlite3"
    writer_ready = threading.Event()
    release_writer = threading.Event()
    replay_acquired = threading.Event()

    def hold_live_writer() -> None:
        with storage_io_priority_gate(ledger_path, exclusive=True):
            writer_ready.set()
            assert release_writer.wait(1.0)

    def wait_for_replay_read() -> None:
        with storage_io_priority_gate(ledger_path, exclusive=False):
            replay_acquired.set()

    writer = threading.Thread(target=hold_live_writer)
    replay = threading.Thread(target=wait_for_replay_read)
    writer.start()
    assert writer_ready.wait(1.0)
    replay.start()
    assert not replay_acquired.wait(0.1)
    release_writer.set()
    writer.join(1.0)
    assert replay_acquired.wait(1.0)
    replay.join(1.0)
    assert not writer.is_alive()
    assert not replay.is_alive()


def test_replay_process_applies_background_io_policy_before_work(monkeypatch) -> None:
    applied: list[object] = []
    monkeypatch.setattr(
        replay_process_module.pa,
        "set_cpu_count",
        lambda count: applied.append(("cpu", count)),
    )
    monkeypatch.setattr(
        replay_process_module.pa,
        "set_io_thread_count",
        lambda count: applied.append(("io", count)),
    )
    monkeypatch.setattr(
        replay_process_module,
        "_apply_replay_background_io_policy",
        lambda: applied.append("background"),
    )

    replay_process_module._prepare_cpu_budget()

    assert applied == [("cpu", 1), ("io", 1), "background"]


def test_replay_subprocess_environment_limits_parallel_numeric_workers() -> None:
    environment = replay_process_module._worker_environment()

    assert all(
        environment[variable] == "1"
        for variable in replay_process_module._REPLAY_SINGLE_THREAD_ENVIRONMENT
    )


async def test_replay_subprocess_returns_paper_result_from_low_priority_worker(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "subprocess-replay.sqlite3"
    ledger = SQLiteLedger(ledger_path)
    ledger.start_run(
        "run-subprocess-replay",
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    event = market_event(
        "run-subprocess-replay",
        event_id="subprocess-depth",
        ts_ms=1_000,
    )
    assert ledger.record_market_events([event.model_dump(mode="json")]) == 1
    ledger.close()

    result = await replay_process_module.replay_stored_run_in_subprocess(
        str(ledger_path),
        None,
        "run-subprocess-replay",
        2_000,
        "BTCUSDT",
        1,
    )

    assert result["event_count"] == 1
    assert len(str(result["input_checksum"])) == 64
    assert result["real_orders_enabled"] is False
    assert result["auth_required"] is False


def test_schema_v7_market_events_are_ordered_checksummed_immutable_and_counted(
    tmp_path: Path,
) -> None:
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
    assert ledger.schema_version == 7
    assert ledger._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert ledger._connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0
    assert inserted == 2
    assert [row["event_id"] for row in ledger.list_market_events("run-market")] == [
        "event-1",
        "event-2",
    ]
    assert ledger.record_market_events([earlier.model_dump(mode="json")]) == 0
    conflicting = earlier.model_copy(update={"data": {**earlier.data, "bid": "1.0"}})
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
    summaries = ledger.list_replayable_run_summaries()
    first_run = next(row for row in summaries if row["run_id"] == "run-market")
    assert first_run["market_event_count"] == 2
    assert ledger.market_event_symbols("run-market") == [{"symbol": "BTCUSDT", "event_count": 2}]
    ledger.close()

    connection = sqlite3.connect(tmp_path / "ledger.sqlite3")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        connection.execute(
            "UPDATE market_events SET payload_json = '{}' WHERE event_id = 'event-1'"
        )
    connection.close()


def test_replayable_run_listing_does_not_force_live_buffer_flush(
    tmp_path: Path,
) -> None:
    class NonFlushingRuntime(PaperRuntime):
        def _flush_persistence(self, market_limit: int | None = None) -> None:
            raise AssertionError("리플레이 목록 조회가 LIVE 저장 버퍼를 동기 flush했습니다.")

    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    ledger.start_run(
        "run-listed",
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    event = market_event("run-listed", event_id="event-listed", ts_ms=1_000)
    assert ledger.record_market_events([event.model_dump(mode="json")]) == 1
    runtime = NonFlushingRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-listed",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )

    listed = runtime.replayable_runs()[0]
    assert listed["run_id"] == "run-listed"
    assert listed["trade_count"] == 0
    assert listed["shadow_trade_count"] == 0
    ledger.close()


def test_live_replay_listing_uses_warmed_cache_during_active_ledger_contention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "cached-replay-list.sqlite3")
    ledger.start_run(
        "run-cached",
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    first = market_event("run-cached", event_id="event-cached-1", ts_ms=1_000)
    assert ledger.record_market_events([first.model_dump(mode="json")]) == 1
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-cached",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime._refresh_dashboard_trade_cache()

    def unexpected_scan(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("LIVE replay 목록이 활성 원장을 다시 읽었습니다.")

    monkeypatch.setattr(ledger, "list_replayable_run_summaries", unexpected_scan)
    monkeypatch.setattr(
        PaperRuntime,
        "_history_shadow_trades",
        lambda _runtime: (
            {"run_id": "run-cached", "trade_id": "shadow-1"},
            {"run_id": "run-cached", "trade_id": "shadow-2"},
            {"run_id": "run-cached", "trade_id": "shadow-3"},
        ),
    )
    listed = runtime.replayable_runs()[0]
    assert listed["market_event_count"] == 1
    assert listed["shadow_trade_count"] == 3

    second = market_event("run-cached", event_id="event-cached-2", ts_ms=2_000)
    runtime._market_event_buffer.append(second.model_dump(mode="json"))
    assert runtime.replayable_runs()[0]["market_event_count"] == 2
    runtime._flush_persistence()
    assert runtime.replayable_runs()[0]["market_event_count"] == 2
    ledger.close()


def test_history_api_separates_main_league_profile_and_version_scope(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "history-scope.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-001",
        venue=Venue.FIXTURE,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    main_trade = {
        **_sample_trade("main-current"),
        "candidate_id": "candidate-main-current",
        "signal_event_id": "signal-main-current",
        "sample_type": "LIVE_PUBLIC",
        "strategy_version": STRATEGY_VERSION,
    }
    shadow_trade = {
        **_sample_trade("shadow-current"),
        "shadow_trade_id": "shadow-current",
        "closed_ts_ms": 2_100,
        "candidate_id": "candidate-shadow-current",
        "signal_event_id": "signal-shadow-current",
        "profile": "STRESS",
        "sample_type": "LIVE_PUBLIC",
        "strategy_version": STRATEGY_VERSION,
    }
    ledger.record_trade(main_trade)
    ledger.record_shadow_trade(shadow_trade)
    ledger.start_run(
        "run-history-prior",
        mode="LIVE_SHADOW_PAPER",
        venue="BINANCE_USDM",
        config={"strategy_version": "prior-version"},
        started_ts_ms=500,
    )
    prior_shadow = {
        **shadow_trade,
        "trade_id": "shadow-prior",
        "shadow_trade_id": "shadow-prior",
        "run_id": "run-history-prior",
        "strategy_version": "prior-version",
    }
    ledger.record_shadow_trade(prior_shadow)

    with TestClient(create_app(runtime)) as client:
        response = client.get(
            "/api/history",
            params={
                "run_scope": "CURRENT",
                "account_scope": "ALL",
                "profile": "ALL",
                "version_scope": "CURRENT",
                "sample_type": "LIVE_PUBLIC",
            },
        )
        all_versions = runtime.history_records(
            run_scope="ALL",
            account_scope="LEAGUE",
            profile="STRESS",
            version_scope="ALL",
            sample_type="LIVE_PUBLIC",
        )

    assert response.status_code == 200
    payload = response.json()
    assert {row["account_scope"] for row in payload["rows"]} == {"MAIN", "LEAGUE"}
    league = next(row for row in payload["rows"] if row["account_scope"] == "LEAGUE")
    assert league["profile"] == "STRESS"
    assert league["account_id"].endswith(":STRESS")
    assert league["candidate_id"] == "candidate-shadow-current"
    assert league["signal_event_id"] == "signal-shadow-current"
    assert league["opportunity_id"] == "shadow-current"
    assert league["replay_available"] is False
    assert payload["scope"]["strategy_version"] == STRATEGY_VERSION
    assert payload["paper_only"] is True
    assert payload["real_orders_enabled"] is False
    assert payload["auth_required"] is False
    assert {row["strategy_version"] for row in all_versions["rows"]} == {
        STRATEGY_VERSION,
        "prior-version",
    }
    ledger.close()


def test_live_history_uses_warmed_trade_cache_without_rescanning_active_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-history-cache.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-001",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    main_trade = {
        **_sample_trade("main-cache"),
        "sample_type": "LIVE_PUBLIC",
        "strategy_version": STRATEGY_VERSION,
    }
    shadow_trade = {
        **_sample_trade("shadow-cache"),
        "shadow_trade_id": "shadow-cache",
        "closed_ts_ms": 2_000,
        "sample_type": "LIVE_PUBLIC",
        "strategy_version": STRATEGY_VERSION,
    }
    ledger.record_trade(main_trade)
    ledger.record_shadow_trade(shadow_trade)
    runtime._refresh_dashboard_trade_cache()

    def unexpected_scan(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("활성 원장 전체 거래를 다시 스캔하면 안 됩니다.")

    monkeypatch.setattr(ledger, "list_trades", unexpected_scan)
    monkeypatch.setattr(ledger, "list_shadow_trades", unexpected_scan)
    monkeypatch.setattr(ledger, "list_replayable_run_summaries", unexpected_scan)
    response = runtime.history_records(account_scope="ALL", sample_type="LIVE_PUBLIC")

    assert {row["trade_id"] for row in response["rows"]} == {
        "main-cache",
        "shadow-cache",
    }
    assert response["scope"]["returned_count"] == 2
    ledger.close()


def test_live_position_moves_to_completed_history_without_waiting_for_ledger_rescan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """열린 PAPER 포지션은 종료 즉시 현재 목록에서 빠지고 완료 기록에 들어가야 한다."""

    plan = replace(candidate_plan(), venue=Venue.BINANCE_USDM)
    ledger = SQLiteLedger(tmp_path / "live-position-history-lifecycle.sqlite3")
    ledger.start_run(
        plan.run_id,
        mode=RuntimeMode.LIVE_SHADOW_PAPER.value,
        venue=Venue.BINANCE_USDM.value,
        config={"strategy_version": STRATEGY_VERSION},
        started_ts_ms=1,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id=plan.run_id,
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime._refresh_dashboard_trade_cache()

    def unexpected_scan(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise AssertionError("거래 종료 직후 활성 원장을 다시 전체 스캔하면 안 됩니다.")

    monkeypatch.setattr(ledger, "list_trades", unexpected_scan)
    monkeypatch.setattr(ledger, "list_shadow_trades", unexpected_scan)
    monkeypatch.setattr(ledger, "list_replayable_run_summaries", unexpected_scan)

    runtime.paper_portfolio.offer((plan,), entries_paused=False)
    runtime.paper_portfolio.on_book(replace(book(1_249), venue=Venue.BINANCE_USDM))
    opened_book = replace(book(1_250), venue=Venue.BINANCE_USDM)
    runtime.paper_portfolio.on_book(opened_book)
    stress_opened_book = replace(book(1_500), venue=Venue.BINANCE_USDM)
    runtime.paper_portfolio.on_book(stress_opened_book)
    runtime.latest_books[plan.symbol] = stress_opened_book

    open_accounts = {str(row["account_id"]) for row in runtime.focus_positions()}
    assert open_accounts == {
        "SHARED_PAPER",
        f"{plan.strategy_id}:BASE",
        f"{plan.strategy_id}:STRESS",
    }
    assert (
        runtime.history_records(
            account_scope="ALL",
            version_scope="CURRENT",
            sample_type="LIVE_PUBLIC",
        )["rows"]
        == []
    )

    tp1 = plan.take_profit_targets[0].price
    runtime.paper_portfolio.on_book(
        replace(
            book(2_000, bids=((str(tp1 + Decimal("0.1")), "100"),), asks=(("107", "100"),)),
            venue=Venue.BINANCE_USDM,
        )
    )
    runtime.paper_portfolio.on_book(
        replace(
            book(2_250, bids=((str(tp1 + Decimal("0.05")), "100"),), asks=(("107", "100"),)),
            venue=Venue.BINANCE_USDM,
        )
    )
    tp2 = plan.take_profit_targets[1].price
    runtime.paper_portfolio.on_book(
        replace(
            book(3_000, bids=((str(tp2 + Decimal("0.1")), "100"),), asks=(("107", "100"),)),
            venue=Venue.BINANCE_USDM,
        )
    )
    base_closed_book = replace(
        book(
            3_250,
            bids=((str(tp2 + Decimal("0.05")), "100"),),
            asks=(("107", "100"),),
        ),
        venue=Venue.BINANCE_USDM,
    )
    runtime.paper_portfolio.on_book(base_closed_book)
    runtime.latest_books[plan.symbol] = base_closed_book

    assert {str(row["account_id"]) for row in runtime.focus_positions()} == {
        f"{plan.strategy_id}:STRESS"
    }
    partially_completed = runtime.history_records(
        account_scope="ALL",
        version_scope="CURRENT",
        sample_type="LIVE_PUBLIC",
    )
    assert {str(row["account_scope"]) for row in partially_completed["rows"]} == {
        "MAIN",
        "LEAGUE",
    }
    assert {str(row["profile"]) for row in partially_completed["rows"]} == {"BASE"}

    all_closed_book = replace(
        book(
            3_750,
            bids=((str(tp2 + Decimal("0.05")), "100"),),
            asks=(("107", "100"),),
        ),
        venue=Venue.BINANCE_USDM,
    )
    runtime.paper_portfolio.on_book(all_closed_book)
    runtime.latest_books[plan.symbol] = all_closed_book

    assert runtime.focus_positions() == []
    completed = runtime.history_records(
        account_scope="ALL",
        version_scope="CURRENT",
        sample_type="LIVE_PUBLIC",
    )
    assert len(completed["rows"]) == 3
    assert {str(row["account_scope"]) for row in completed["rows"]} == {"MAIN", "LEAGUE"}
    assert {str(row["profile"]) for row in completed["rows"]} == {"BASE", "STRESS"}
    assert completed["paper_only"] is True
    assert completed["real_orders_enabled"] is False
    assert completed["auth_required"] is False
    ledger.close()


def test_recovered_trade_does_not_overwrite_persisted_strategy_version(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "recovered-history-version.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-001",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    prior_trade = {
        **_sample_trade("recovered-prior"),
        "sample_type": "LIVE_PUBLIC",
        "strategy_version": "prior-strategy-version",
    }
    ledger.record_trade(prior_trade)
    runtime._refresh_dashboard_trade_cache()

    class RecoveredTradeStub:
        trade_id = "recovered-prior"

    runtime.paper_portfolio.main.completed_trades.append(RecoveredTradeStub())  # type: ignore[arg-type]

    current = runtime.history_records(
        account_scope="MAIN",
        version_scope="CURRENT",
        sample_type="LIVE_PUBLIC",
    )
    all_versions = runtime.history_records(
        account_scope="MAIN",
        version_scope="ALL",
        sample_type="LIVE_PUBLIC",
    )

    assert current["rows"] == []
    assert runtime._dashboard_live_main_trades() == ()
    assert len(all_versions["rows"]) == 1
    assert all_versions["rows"][0]["strategy_version"] == "prior-strategy-version"
    ledger.close()


def test_replayable_run_listing_does_not_wait_for_live_writer_lock(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    ledger.start_run(
        "run-readable",
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    event = market_event("run-readable", event_id="event-readable", ts_ms=1_000)
    assert ledger.record_market_events([event.model_dump(mode="json")]) == 1
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_writer_lock() -> None:
        with ledger._lock:
            lock_acquired.set()
            release_lock.wait(timeout=2)

    writer = threading.Thread(target=hold_writer_lock)
    writer.start()
    assert lock_acquired.wait(timeout=1)
    try:
        summaries = ledger.list_replayable_run_summaries()
    finally:
        release_lock.set()
        writer.join(timeout=1)

    assert summaries[0]["run_id"] == "run-readable"
    ledger.close()


def test_replay_preview_does_not_wait_for_live_writer_lock(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "preview-readable.sqlite3")
    run_id = "run-preview-readable"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260830},
        started_ts_ms=1_000,
    )
    event = market_event(run_id, event_id="preview-readable", ts_ms=1_000)
    assert ledger.record_market_events([event.model_dump(mode="json")]) == 1
    lock_acquired = threading.Event()
    release_lock = threading.Event()
    preview_finished = threading.Event()
    preview_rows: list[dict[str, object]] = []

    def hold_writer_lock() -> None:
        with ledger._lock, ledger._read_lock:
            lock_acquired.set()
            release_lock.wait(timeout=2)

    def read_preview() -> None:
        preview_rows.append(build_replay_preview(ledger, run_id))
        preview_finished.set()

    writer = threading.Thread(target=hold_writer_lock)
    reader = threading.Thread(target=read_preview)
    writer.start()
    assert lock_acquired.wait(timeout=1)
    reader.start()
    try:
        assert preview_finished.wait(timeout=1)
    finally:
        release_lock.set()
        writer.join(timeout=1)
        reader.join(timeout=1)

    assert preview_rows[0]["symbol"] == "BTCUSDT"
    assert preview_rows[0]["available_symbols"] == [{"symbol": "BTCUSDT", "event_count": 1}]
    ledger.close()


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
    assert migrated.schema_version == 7
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
    runtime.ingest_live_event(market_event(runtime.run_id, event_id="depth-1", ts_ms=1_000))
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="trade-1", ts_ms=1_100, event_type="TRADE")
    )
    runtime.ingest_live_event(market_event(runtime.run_id, event_id="depth-2", ts_ms=1_500))
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
    assert first.scope_symbol is None
    assert first.as_dict()["scope_symbol"] is None
    assert first.event_count == 4
    assert first.event_type_counts == {"DEPTH_UPDATE": 2, "TRADE": 2}
    assert first.strategy_evaluation_count == runtime.strategy_evaluation_count
    assert first.qualified_signal_count == 0
    assert first.final_state == "OBSERVING_NO_MAIN_TRADE"
    assert first.real_orders_enabled is False
    assert first.auth_required is False
    assert len(ledger.list_replay_runs(runtime.run_id)) == 2
    latest = ledger.list_latest_replay_runs()
    assert len(latest) == 1
    assert latest[0]["replay_id"] == second.replay_id
    ledger.close()


def test_external_parquet_market_archive_keeps_sqlite_small_and_replays(
    tmp_path: Path,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "ledger.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-parquet-market",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        market_event_archive=archive,
        clock=DeterministicClock(),
    )
    events = [
        market_event(
            runtime.run_id,
            event_id=f"event-{index}",
            ts_ms=1_000 + index,
            event_type="TRADE" if index % 2 else "DEPTH_UPDATE",
        )
        for index in range(4)
    ]
    persisted_rows = [runtime._persistable_market_event(event) for event in reversed(events)]
    runtime._market_event_buffer = list(persisted_rows)

    runtime.flush_storage()

    assert ledger.count("market_events") == 0
    assert ledger.count("market_event_archives") == 1
    assert ledger.market_event_symbols(runtime.run_id) == [{"symbol": "BTCUSDT", "event_count": 4}]
    assert [row["event_id"] for row in ledger.list_market_events(runtime.run_id)] == [
        f"event-{index}" for index in range(4)
    ]
    assert len(ledger.list_market_events(runtime.run_id, event_types=("TRADE",))) == 2
    assert ledger.list_market_events(runtime.run_id, limit=2)[-1]["event_id"] == "event-1"
    files = archive.dataset_files()
    assert len(files) == 1
    assert files[0].suffix == ".parquet"
    assert files[0].stat().st_size > 0
    repeated = archive.write_market_event_batch(persisted_rows)
    assert ledger.record_market_event_archive(repeated, persisted_rows) == 0
    with pytest.raises(ValueError, match="배치 checksum"):
        archive.read_market_event_batch(files[0], expected_checksum="0" * 64)
    with pytest.raises(ValueError, match="배치 checksum"):
        archive.read_market_event_batch_filtered(
            files[0],
            expected_checksum="0" * 64,
            symbol="BTCUSDT",
        )
    complete_table = pq.ParquetFile(files[0]).read()
    assert {"receive_ts_ms", "receive_monotonic_ns"} <= set(complete_table.column_names)
    truncated_file = files[0].with_name("tampered-truncated.parquet")
    pq.write_table(complete_table.slice(0, complete_table.num_rows - 1), truncated_file)
    with pytest.raises(ValueError, match="배치 checksum"):
        archive.read_market_event_batch_filtered(
            truncated_file,
            expected_checksum=repeated.checksum,
            symbol="BTCUSDT",
        )
    assert (
        len(
            archive.read_market_event_batch_filtered(
                files[0],
                expected_checksum=repeated.checksum,
                symbol="BTCUSDT",
                event_types=("TRADE",),
                start_ts_ms=1_000,
                end_ts_ms=2_000,
            )
        )
        == 2
    )
    replay = StoredMarketReplay().run(
        ledger,
        source_run_id=runtime.run_id,
        created_ts_ms=3_000,
        symbol="btcusdt",
    )
    assert replay.event_count == 4
    assert replay.scope_symbol == "BTCUSDT"
    assert replay.as_dict()["scope_symbol"] == "BTCUSDT"
    ledger.close()


def test_recent_timeline_reads_only_the_newest_archive_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "recent-market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "recent-ledger.sqlite3",
        market_event_archive=archive,
    )
    run_id = "run-recent-window"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260830},
        started_ts_ms=1_000,
    )
    for batch_index in range(6):
        rows = [
            market_event(
                run_id,
                event_id=f"recent-{batch_index * 2 + offset}",
                ts_ms=1_000 + batch_index * 100 + offset,
            ).model_dump(mode="json")
            for offset in range(2)
        ]
        batch = archive.write_market_event_batch(rows)
        assert ledger.record_market_event_archive(batch, rows) == 2

    read_paths: list[Path] = []
    original_read = archive.read_market_event_batch_filtered

    def traced_read(path: Path, **kwargs: object) -> list[dict[str, object]]:
        read_paths.append(path)
        return original_read(path, **kwargs)

    monkeypatch.setattr(archive, "read_market_event_batch_filtered", traced_read)
    recent = ledger.list_recent_market_events(
        run_id,
        symbol="BTCUSDT",
        limit=3,
    )

    assert [row["event_id"] for row in recent] == ["recent-9", "recent-10", "recent-11"]
    assert len(read_paths) == 2
    ledger.close()


def test_archive_manifest_stats_and_candles_use_one_full_commit(tmp_path: Path) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-atomic",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "atomic-ledger.sqlite3",
        market_event_archive=archive,
    )
    run_id = "run-atomic-persistence"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    assert ledger._connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0
    event = market_event(run_id, event_id="atomic-event", ts_ms=1_000).model_dump(mode="json")
    batch = archive.write_market_event_batch([event])
    statements: list[str] = []
    ledger._connection.set_trace_callback(statements.append)

    assert ledger.record_archives_and_candles(
        [(batch, [event])],
        [candle_row(run_id)],
    ) == (1, 1)

    assert sum(statement == "BEGIN IMMEDIATE" for statement in statements) == 1
    assert sum(statement == "COMMIT" for statement in statements) == 1
    assert ledger.count("market_event_archives") == 1
    assert ledger.count("candles") == 1
    assert ledger.market_event_symbols(run_id) == [{"symbol": "BTCUSDT", "event_count": 1}]
    ledger.close()


def test_filtered_archive_read_releases_live_gate_between_small_batches(
    tmp_path: Path,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-streamed",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    rows = [
        market_event(
            "run-streamed",
            event_id=f"streamed-{index}",
            ts_ms=1_000 + index,
            event_type="TRADE" if index % 2 else "DEPTH_UPDATE",
        ).model_dump(mode="json")
        for index in range(300)
    ]
    archived = archive.write_market_event_batch(rows)
    guard_steps: list[str] = []
    cooperative_checkpoints: list[int] = []

    @contextmanager
    def archive_guard():
        guard_steps.append("ENTER")
        yield
        guard_steps.append("EXIT")

    restored = archive.read_market_event_batch_filtered(
        archived.path,
        expected_checksum=archived.checksum,
        symbol="BTCUSDT",
        batch_guard=archive_guard,
        cooperative_yield=lambda: cooperative_checkpoints.append(1),
        batch_size=64,
    )

    assert len(restored) == 300
    assert cooperative_checkpoints == [1, 1, 1, 1, 1]
    assert guard_steps == ["ENTER", "EXIT"] * 6


def test_passive_checkpoint_runs_outside_commit_connection(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "passive-checkpoint.sqlite3")
    run_id = "run-passive-checkpoint"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    assert ledger.record_candles([candle_row(run_id)]) == 1

    busy, log_frames, checkpointed_frames = run_passive_wal_checkpoint_in_process(str(ledger.path))

    assert busy == 0
    assert log_frames > 0
    assert checkpointed_frames == log_frames
    assert ledger.list_candles(
        run_id,
        symbol="BTCUSDT",
        interval_seconds=1,
    ) == [candle_row(run_id)]
    ledger.close()


def test_candle_timeline_range_reads_only_requested_event_window(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "bounded-candle-timeline.sqlite3")
    run_id = "run-bounded-candle-timeline"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    assert (
        ledger.record_candles(
            [
                candle_row(run_id, open_ts_ms=1_000),
                candle_row(run_id, open_ts_ms=2_000),
                candle_row(run_id, open_ts_ms=3_000),
            ]
        )
        == 3
    )

    bounded = ledger.list_candles(
        run_id,
        symbol="BTCUSDT",
        interval_seconds=1,
        start_ts_ms=2_000,
        end_ts_ms=2_000,
    )

    assert [row["open_ts_ms"] for row in bounded] == [2_000]
    ledger.close()


def test_archive_and_full_commit_use_independent_connection(tmp_path: Path) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-process",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "process-ledger.sqlite3",
        market_event_archive=archive,
    )
    run_id = "run-process-persistence"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    event = market_event(run_id, event_id="process-event", ts_ms=1_000).model_dump(mode="json")

    timings = persist_archives_and_candles_in_process(
        str(archive.root),
        archive.minimum_free_bytes,
        archive.minimum_free_ratio,
        str(ledger.path),
        [[event]],
        [candle_row(run_id)],
    )

    assert timings["archive_batches"] == 1
    assert float(timings["archive_ms"]) >= 0
    assert float(timings["ledger_ms"]) >= 0
    assert float(timings["wal_probe_ms"]) >= 0
    assert int(timings["wal_log_frames"]) >= 0
    assert int(timings["wal_checkpointed_frames"]) >= 0
    assert int(timings["wal_page_size"]) > 0
    assert ledger.count("market_event_archives") == 1
    assert ledger.count("candles") == 1
    assert ledger.market_event_symbols(run_id) == [{"symbol": "BTCUSDT", "event_count": 1}]
    ledger.close()


def test_atomic_persistence_rolls_back_manifest_and_stats_on_candle_fault(
    tmp_path: Path,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-rollback",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "rollback-ledger.sqlite3",
        market_event_archive=archive,
    )
    run_id = "run-atomic-rollback"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    assert ledger.record_candles([candle_row(run_id)]) == 1
    event = market_event(run_id, event_id="rollback-event", ts_ms=1_000).model_dump(mode="json")
    batch = archive.write_market_event_batch([event])

    with pytest.raises(LedgerInvariantError, match="중복 캔들 payload 불일치"):
        ledger.record_archives_and_candles(
            [(batch, [event])],
            [candle_row(run_id, close="101")],
        )

    assert ledger.count("market_event_archives") == 0
    assert ledger.market_event_symbols(run_id) == []
    assert ledger.count("candles") == 1
    ledger.close()


def test_market_archive_timeline_limit_verifies_all_receive_order_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-limited",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "limited-ledger.sqlite3",
        market_event_archive=archive,
    )
    ledger.start_run(
        "run-limited",
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    for index in range(3):
        event = market_event(
            "run-limited",
            event_id=f"limited-{index}",
            ts_ms=1_000 + index,
        ).model_dump(mode="json")
        batch = archive.write_market_event_batch([event])
        assert ledger.record_market_event_archive(batch, [event]) == 1
    original_read = archive.read_market_event_batch_filtered
    read_paths: list[Path] = []

    def counted_read(
        path: Path,
        *,
        expected_checksum: str,
        symbol: str | None = None,
        event_types: tuple[str, ...] = (),
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        batch_guard=None,
        cooperative_yield=None,
        batch_size: int = 128,
    ) -> list[dict[str, object]]:
        read_paths.append(path)
        return original_read(
            path,
            expected_checksum=expected_checksum,
            symbol=symbol,
            event_types=event_types,
            start_ts_ms=start_ts_ms,
            end_ts_ms=end_ts_ms,
            batch_guard=batch_guard,
            cooperative_yield=cooperative_yield,
            batch_size=batch_size,
        )

    monkeypatch.setattr(archive, "read_market_event_batch_filtered", counted_read)
    yielded_archive_bytes: list[int] = []
    guard_steps: list[str] = []

    @contextmanager
    def archive_guard():
        guard_steps.append("ENTER")
        yield
        guard_steps.append("EXIT")

    events = ledger.list_market_events(
        "run-limited",
        symbol="BTCUSDT",
        limit=1,
        archive_batch_yield=yielded_archive_bytes.append,
        archive_batch_guard=archive_guard,
    )

    assert [event["event_id"] for event in events] == ["limited-0"]
    assert len(read_paths) == 3
    assert yielded_archive_bytes == [path.stat().st_size for path in read_paths]
    assert guard_steps == ["ENTER", "EXIT", "ENTER", "EXIT"] * 3

    late_sqlite_event = market_event(
        "run-limited",
        event_id="limited-late-sqlite",
        ts_ms=9_000,
    ).model_dump(mode="json")
    assert ledger.record_market_events([late_sqlite_event]) == 1
    read_paths.clear()
    mixed_events = ledger.list_market_events(
        "run-limited",
        symbol="BTCUSDT",
        limit=1,
    )

    assert [event["event_id"] for event in mixed_events] == ["limited-0"]
    assert len(read_paths) == 3

    early_sqlite_event = market_event(
        "run-limited",
        event_id="limited-early-sqlite",
        ts_ms=500,
    ).model_dump(mode="json")
    assert ledger.record_market_events([early_sqlite_event]) == 1
    read_paths.clear()
    merged_events = ledger.list_market_events(
        "run-limited",
        symbol="BTCUSDT",
        limit=2,
    )

    assert [event["event_id"] for event in merged_events] == [
        "limited-early-sqlite",
        "limited-0",
    ]
    assert len(read_paths) == 3
    ledger.close()


def test_market_archive_separates_runtime_partitions_and_replays_both(
    tmp_path: Path,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    first_rows = [
        {
            "run_id": "run-first",
            "event_id": "event-first",
            "venue": "BINANCE_USDM",
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "recv_ts_ms": 1_001,
            "data": {"price": "100"},
        }
    ]
    second_rows = [
        {
            **first_rows[0],
            "run_id": "run-second",
            "event_id": "event-second",
        }
    ]

    first = archive.write_market_event_batch(first_rows)
    second = archive.write_market_event_batch(second_rows)

    assert "run=RUN-FIRST" in first.path.parts
    assert "run=RUN-SECOND" in second.path.parts
    assert first.path.parent != second.path.parent
    assert (
        archive.read_market_event_batch(
            first.path,
            expected_checksum=first.checksum,
        )
        == first_rows
    )
    assert (
        archive.read_market_event_batch(
            second.path,
            expected_checksum=second.checksum,
        )
        == second_rows
    )


def test_runtime_persists_top10_book_without_mutating_live_event() -> None:
    event = market_event("run-top10", event_id="depth-top10", ts_ms=1_000)
    bids = [[str(100 - index / 10), "1"] for index in range(20)]
    asks = [[str(100.1 + index / 10), "1"] for index in range(20)]
    event.data["bids"] = bids
    event.data["asks"] = asks

    persisted = PaperRuntime._persistable_market_event(event)

    assert len(event.data["bids"]) == 20
    assert len(event.data["asks"]) == 20
    assert isinstance(persisted["data"], dict)
    assert len(persisted["data"]["bids"]) == 10
    assert len(persisted["data"]["asks"]) == 10
    assert persisted["data"]["bid"] == event.data["bid"]
    assert persisted["data"]["ask"] == event.data["ask"]
    expected = event.model_dump(mode="json")
    expected["data"]["bids"] = bids[:10]
    expected["data"]["asks"] = asks[:10]
    expected["receive_ts_ms"] = event.venue_ts_ms + round(event.quality.lag_ms)
    assert persisted == expected


def test_candidate_sqlite_commit_is_deferred_out_of_live_candidate_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = candidate_plan()
    ledger = SQLiteLedger(tmp_path / "candidate-buffer.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id=plan.run_id,
        venue=Venue.FIXTURE,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    event = market_event(
        plan.run_id,
        event_id="candidate-buffer-event",
        ts_ms=plan.signal_time_ms,
    ).model_copy(update={"venue": Venue.FIXTURE})
    signal = EvaluatedSignal(
        symbol=plan.symbol,
        regime=Regime.RANGE,
        decision=qualified_decision(),
        main_eligible=True,
        shadow_eligible=True,
    )
    monkeypatch.setattr(
        runtime.candidate_planner,
        "build",
        lambda **_kwargs: PlanBuildResult(plan, ()),
    )
    monkeypatch.setattr(
        ledger,
        "record_candidate",
        lambda _candidate: pytest.fail("LIVE 후보계획 단계에서 SQLite를 직접 호출했습니다."),
    )

    plans = runtime._build_candidate_plans(
        event,
        features(),
        Regime.RANGE,
        book(plan.signal_time_ms),
        (signal,),
    )

    assert plans == (plan,)
    assert ledger.count("candidates") == 0
    assert [row["candidate_id"] for row in runtime._candidate_plan_buffer] == [plan.candidate_id]
    assert runtime._has_unpersisted_execution_state() is True

    runtime._persist_execution_state(plan.signal_time_ms)

    assert runtime._candidate_plan_buffer == []
    assert ledger.count("candidates") == 1
    assert ledger.get_candidate(plan.run_id, plan.candidate_id) is not None
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
    persisted_main_audit_times = [
        (str(row["event"]), int(str(row["ts_ms"])))
        for row in ledger.list_execution_audits(runtime.run_id)
        if row.get("account_id") == runtime.paper_portfolio.main.account_id
    ]
    assert ("ENTRY_FILLED", 1_250) in persisted_main_audit_times
    assert ("TAKE_PROFIT_EXIT_PENDING", 2_000) in persisted_main_audit_times
    assert ("EXIT_FILL", 2_250) in persisted_main_audit_times
    assert ("TAKE_PROFIT_EXIT_PENDING", 3_000) in persisted_main_audit_times
    assert ("EXIT_FILL", 3_250) in persisted_main_audit_times
    persisted_main_transitions = [
        row
        for row in ledger.list_execution_audits(runtime.run_id)
        if row.get("account_id") == runtime.paper_portfolio.main.account_id
        and row.get("transition_id") is not None
    ]
    assert persisted_main_transitions
    assert [int(str(row["response_revision"])) for row in persisted_main_transitions] == list(
        range(1, len(persisted_main_transitions) + 1)
    )
    assert all(
        int(str(row["occurred_ts_ms"])) == int(str(row["ts_ms"]))
        for row in persisted_main_transitions
    )
    diagnostics = runtime._operational_diagnostics()
    latest_transition = runtime.paper_portfolio.latest_execution_transition
    assert diagnostics["last_paper_transition_id"] == latest_transition["transition_id"]
    assert diagnostics["last_paper_transition_state"] == "CLOSED"
    assert diagnostics["last_paper_transition_account_id"] in {
        "MAIN:BASE",
        f"{plan.strategy_id}:BASE",
        f"{plan.strategy_id}:STRESS",
    }
    assert diagnostics["last_paper_transition_symbol"] == plan.symbol
    trade = ledger.list_trades(runtime.run_id)[0]
    fill_evidence = ledger.list_trade_fill_evidence(
        [(runtime.run_id, str(trade["trade_id"]))]
    )[(runtime.run_id, str(trade["trade_id"]))]
    run = ledger.get_run(runtime.run_id)
    assert run is not None
    assert trade["config_hash"] == run["config_hash"]
    shadow_trades = ledger.list_shadow_trades(runtime.run_id)
    assert all(row["config_hash"] == run["config_hash"] for row in shadow_trades)
    assert all(row["strategy_version"] == STRATEGY_VERSION for row in shadow_trades)
    assert all(row["sample_type"] == "REPLAY" for row in shadow_trades)
    assert Decimal(str(trade["net_pnl_usdt"])) == (
        Decimal(str(trade["gross_pnl_usdt"]))
        - Decimal(str(trade["fees_usdt"]))
        - Decimal(str(trade["slippage_usdt"]))
    )
    assert fill_evidence["fill_evidence_state"] == "PRESENT"
    assert len(fill_evidence["fills"]) == 3
    assert [fill["ts_ms"] for fill in fill_evidence["fills"]] == sorted(
        fill["ts_ms"] for fill in fill_evidence["fills"]
    )
    assert sum(
        Decimal(str(fill["fee_usdt"])) for fill in fill_evidence["fills"]
    ) == Decimal(str(trade["fees_usdt"]))
    assert sum(
        Decimal(str(fill["slippage_usdt"])) for fill in fill_evidence["fills"]
    ) == Decimal(str(trade["slippage_usdt"]))
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
    assert base["breakevens"] == 0
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
        if report["strategy_id"] == "CBR_CONTINUATION_V1" and report["profile"] == "STRESS"
    )
    assert empty["sample_size"] == 0
    assert empty["profit_factor"] is None


def test_runtime_strategy_analytics_do_not_double_count_shared_main_trade(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "strategy-analytics-ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-001",
        venue=Venue.FIXTURE,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    main_trade = _sample_trade()
    shadow_trade = {
        **_sample_trade("shadow-trade-001"),
        "shadow_trade_id": "shadow-trade-001",
        "closed_ts_ms": 2_100,
        "sample_type": "LIVE_PUBLIC",
        "strategy_version": STRATEGY_VERSION,
    }
    fixture_shadow_trade = {
        **_sample_trade("shadow-trade-fixture"),
        "shadow_trade_id": "shadow-trade-fixture",
        "closed_ts_ms": 2_200,
        "sample_type": "OFFLINE_FIXTURE",
        "strategy_version": STRATEGY_VERSION,
    }
    ledger.start_run(
        "run-prior-strategy",
        mode="LIVE_SHADOW_PAPER",
        venue="BINANCE_USDM",
        config={"strategy_version": "prior-version"},
        started_ts_ms=500,
    )
    prior_shadow_trade = {
        **_sample_trade("shadow-trade-prior"),
        "run_id": "run-prior-strategy",
        "shadow_trade_id": "shadow-trade-prior",
        "closed_ts_ms": 2_050,
        "sample_type": "LIVE_PUBLIC",
    }
    prior_shadow_trade.pop("strategy_version")
    ledger.record_trade(main_trade)
    ledger.record_shadow_trade(shadow_trade)
    ledger.record_shadow_trade(fixture_shadow_trade)
    ledger.record_shadow_trade(prior_shadow_trade)

    base = next(
        report
        for report in runtime.strategy_performance()
        if report["strategy_id"] == "LSA_REVERSAL_V1" and report["profile"] == "BASE"
    )
    symbol = runtime.strategy_symbol_performance()[0]

    assert base["sample_size"] == 1
    assert base["analysis_scope"] == "CURRENT_STRATEGY_VERSION"
    assert base["strategy_version"] == STRATEGY_VERSION
    assert base["excluded_prior_version_samples"] == 1
    assert symbol["sample_size"] == 1
    assert symbol["analysis_scope"] == "CURRENT_STRATEGY_VERSION"
    assert symbol["strategy_version"] == STRATEGY_VERSION
    assert symbol["strategy_display_name_ko"] == runtime.strategy_registry.descriptor(
        str(symbol["strategy_id"])
    ).display_name_ko
    assert symbol["excluded_prior_version_samples"] == 1
    enriched = ledger.list_shadow_trades("run-prior-strategy")[0]
    assert enriched["strategy_version"] == "prior-version"
    assert enriched["config_hash"]
    ledger.close()


def test_replay_and_strategy_analytics_are_connected_to_http_api(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "api-ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-api-replay",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ingest_live_event(market_event(runtime.run_id, event_id="api-depth", ts_ms=1_000))
    with TestClient(create_app(runtime)) as client:
        runs = client.get("/api/replay/runs")
        assert runs.status_code == 200
        assert runs.json()[0]["run_id"] == runtime.run_id
        replay = client.post(f"/api/replay/{runtime.run_id}", json={})
        assert replay.status_code == 202
        operation_id = replay.json()["operation_id"]
        for _ in range(100):
            operation = client.get(f"/api/replay/operations/{operation_id}")
            if operation.json()["state"] == "COMPLETED":
                break
            time.sleep(0.01)
        assert operation.json()["state"] == "COMPLETED"
        result = operation.json()["result"]
        assert result["event_count"] == 1
        assert len(result["input_checksum"]) == 64
        assert operation.json()["total_events"] == 1
        unavailable = client.post(
            f"/api/replay/{runtime.run_id}",
            json={"event_limit": 2},
        )
        assert unavailable.status_code == 409
        assert unavailable.json()["detail"]["error_code"] == "REPLAY_SCOPE_NOT_AVAILABLE"
        timeline = client.get(f"/api/replay/{runtime.run_id}/timeline")
        assert timeline.status_code == 200
        assert timeline.json()["symbol"] == "BTCUSDT"
        assert timeline.json()["total_events"] == 1
        assert timeline.json()["events"][0]["event_id"] == "api-depth"
        assert timeline.json()["available_symbols"] == [{"symbol": "BTCUSDT", "event_count": 1}]
        preview = client.get(f"/api/replay/{runtime.run_id}/preview")
        assert preview.status_code == 200
        assert preview.json()["symbol"] == "BTCUSDT"
        assert preview.json()["events"] == []
        assert preview.json()["preview_only"] is True
        missing_timeline = client.get("/api/replay/unknown/timeline")
        assert missing_timeline.status_code == 404
        missing_preview = client.get("/api/replay/unknown/preview")
        assert missing_preview.status_code == 404
        results = client.get("/api/replay/results")
        assert results.status_code == 200
        assert results.json()[0]["checksum"] == result["checksum"]
        assert results.json()[0]["decision_path"] == result["decision_path"][-20:]
        analytics = client.get("/api/analytics/strategies")
        assert analytics.status_code == 200
        assert len(analytics.json()) == len(STRATEGY_IDS) * 2
        assert all(
            report["analysis_scope"] == "CURRENT_STRATEGY_VERSION" for report in analytics.json()
        )
        symbols = client.get("/api/analytics/strategy-symbols")
        assert symbols.status_code == 200
        assert symbols.json()["analysis_scope"] == "CURRENT_STRATEGY_VERSION"
        assert symbols.json()["strategy_version"] == STRATEGY_VERSION
        assert symbols.json()["excluded_prior_version_samples"] == 0
        replay_transitions = ledger.list_incidents(category="REPLAY_STATE_TRANSITION")
        assert replay_transitions
        assert replay_transitions[0]["payload"]["previous_state"] == "NONE"
        assert replay_transitions[0]["payload"]["new_state"] == "REQUESTED"
        assert replay_transitions[0]["payload"]["request_revision"] == 0
        assert replay_transitions[0]["payload"]["response_revision"] == 1
        assert replay_transitions[0]["payload"]["reversible"] is True
        assert replay_transitions[-1]["payload"]["new_state"] == "COMPLETED"
        assert replay_transitions[-1]["payload"]["reversible"] is False
        assert all(
            row["payload"]["transition_id"] == row["incident_id"] for row in replay_transitions
        )
        assert all(row["payload"]["run_id"] == runtime.run_id for row in replay_transitions)
        assert all(
            row["payload"]["cause_code"] == "USER_REPLAY_REQUEST" for row in replay_transitions
        )
        assert all(row["payload"]["actor"] == "USER_UI" for row in replay_transitions)
        assert all(row["payload"]["strategy_id"] is None for row in replay_transitions)
        assert all(row["payload"]["account_id"] is None for row in replay_transitions)
    ledger.close()


def test_http_replay_preview_reuses_recent_identical_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "preview-cache.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-preview-cache",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="preview-cache-depth", ts_ms=1_000)
    )
    original_replay_preview = PaperRuntime.replay_preview
    call_count = 0

    def counted_replay_preview(
        instance: PaperRuntime,
        run_id: str,
        *,
        symbol: str | None = None,
        candle_limit: int = 500,
    ) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        return original_replay_preview(
            instance,
            run_id,
            symbol=symbol,
            candle_limit=candle_limit,
        )

    monkeypatch.setattr(PaperRuntime, "replay_preview", counted_replay_preview)
    with TestClient(create_app(runtime)) as client:
        first = client.get(f"/api/replay/{runtime.run_id}/preview?candle_limit=500")
        second = client.get(f"/api/replay/{runtime.run_id}/preview?candle_limit=500")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert call_count == 1
    ledger.close()


def test_http_replay_fails_closed_when_input_scope_count_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "unknown-replay-scope.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-unknown-replay-scope",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    monkeypatch.setattr(
        SQLiteLedger,
        "market_event_symbols",
        lambda _ledger, _run_id: [{"symbol": "BTCUSDT", "event_count": None}],
    )
    monkeypatch.setattr(
        PaperRuntime,
        "replayable_runs",
        lambda _runtime: [{"run_id": runtime.run_id, "market_event_count": None}],
    )

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            f"/api/replay/{runtime.run_id}",
            json={"symbol": "BTCUSDT"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "REPLAY_SCOPE_COUNT_UNAVAILABLE"
    ledger.close()


def test_live_http_replay_uses_isolated_process_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-api-ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-isolated-replay",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ingest_live_event(market_event(runtime.run_id, event_id="isolated-depth", ts_ms=1_000))
    runtime.flush_storage()
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="isolated-buffered-depth", ts_ms=2_000)
    )
    calls: list[tuple[object, ...]] = []
    flush_calls = 0
    original_flush_storage = PaperRuntime.flush_storage

    async def start_persistent_live_without_network(
        _runtime: PaperRuntime,
        _progress=None,
    ) -> bool:
        _runtime.market_data_state = MarketDataState.LIVE
        _runtime.paused = False
        return True

    async def run_subprocess(*arguments, **_options):
        calls.append(arguments)
        return replay_process_module.replay_stored_run_from_paths(*arguments)

    def track_flush_storage(_runtime: PaperRuntime) -> None:
        nonlocal flush_calls
        flush_calls += 1
        original_flush_storage(_runtime)

    monkeypatch.setattr(
        PaperRuntime,
        "start_persistent_live",
        start_persistent_live_without_network,
    )
    monkeypatch.setattr(
        PaperRuntime,
        "flush_storage",
        track_flush_storage,
    )
    monkeypatch.setattr(
        main_module,
        "replay_stored_run_in_subprocess",
        run_subprocess,
    )
    with TestClient(create_app(runtime)) as client:
        response = client.post(f"/api/replay/{runtime.run_id}", json={})
        assert response.status_code == 202
        operation_id = response.json()["operation_id"]
        for _ in range(100):
            operation = client.get(f"/api/replay/operations/{operation_id}")
            if operation.json()["state"] == "COMPLETED":
                break
            time.sleep(0.01)
        assert operation.json()["state"] == "COMPLETED"
        assert operation.json()["total_events"] == 1
        assert operation.json()["result"]["event_count"] == 1
        assert operation.json()["result"]["real_orders_enabled"] is False
        assert len(calls) == 1
        assert calls[0][0] == str(ledger.path)
        assert calls[0][5] == 1
        assert flush_calls == 0
    assert flush_calls == 1
    ledger.close()


def test_live_http_replay_auto_aborts_without_persisting_unsafe_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-safety-abort.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-safety-abort",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ingest_live_event(market_event(runtime.run_id, event_id="safety-depth", ts_ms=1_000))
    runtime.market_data_state = MarketDataState.LIVE
    runtime.paused = False
    baseline = runtime.replay_live_safety_snapshot()
    probe_calls = 0
    worker_stopped = threading.Event()

    async def start_persistent_live_without_network(
        _runtime: PaperRuntime,
        _progress=None,
    ) -> bool:
        _runtime.market_data_state = MarketDataState.LIVE
        _runtime.paused = False
        return True

    def safety_probe(_runtime: PaperRuntime):
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls == 1:
            return baseline
        return replace(
            baseline,
            event_count=baseline.event_count + 1,
            lag_p95_ms=2_000.0,
            critical_lag_active=True,
            critical_lag_incident_count=baseline.critical_lag_incident_count + 1,
            entry_locked=True,
        )

    async def blocking_subprocess(*_arguments, **_options):
        try:
            await asyncio.Event().wait()
        finally:
            worker_stopped.set()
        return {}

    async def fast_live_safety(start_replay, *, probe):
        return await run_with_live_safety(
            start_replay,
            probe=probe,
            thresholds=ReplayLiveSafetyThresholds(poll_seconds=0.005),
        )

    monkeypatch.setattr(
        PaperRuntime,
        "start_persistent_live",
        start_persistent_live_without_network,
    )
    monkeypatch.setattr(PaperRuntime, "replay_live_safety_snapshot", safety_probe)
    monkeypatch.setattr(
        main_module,
        "replay_stored_run_in_subprocess",
        blocking_subprocess,
    )
    monkeypatch.setattr(main_module, "run_with_live_safety", fast_live_safety)
    with TestClient(create_app(runtime)) as client:
        response = client.post(f"/api/replay/{runtime.run_id}", json={})
        assert response.status_code == 202
        operation_id = response.json()["operation_id"]
        for _ in range(200):
            operation = client.get(f"/api/replay/operations/{operation_id}")
            if operation.json()["state"] == "FAILED_RETRYABLE":
                break
            time.sleep(0.01)

        payload = operation.json()
        assert payload["state"] == "FAILED_RETRYABLE"
        assert payload["error_code"] == "REPLAY_ABORTED_LIVE_SAFETY"
        assert "CRITICAL_LAG_ACTIVE" in payload["error_message_ko"]
        assert payload["real_orders_enabled"] is False
        assert payload["auth_required"] is False
        assert worker_stopped.is_set()
        assert ledger.list_replay_runs(runtime.run_id) == []
        transitions = ledger.list_incidents(category="REPLAY_STATE_TRANSITION")
        assert transitions[-1]["payload"]["cause_code"] == ("REPLAY_ABORTED_LIVE_SAFETY")


def test_live_preview_timeline_and_focus_are_process_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-ui-replay-ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-ui-replay",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    calls: list[tuple[str, tuple[object, ...]]] = []

    def preview_stub(*arguments: object) -> dict[str, object]:
        calls.append(("preview", arguments))
        return {
            "run_id": "run-live-ui-replay",
            "symbol": "BTCUSDT",
            "total_events": 1,
            "truncated": True,
            "available_symbols": [{"symbol": "BTCUSDT", "event_count": 1}],
            "events": [],
            "candles": [],
            "preview_only": True,
        }

    def timeline_stub(*arguments: object) -> dict[str, object]:
        calls.append(("timeline", arguments))
        return {
            "run_id": "run-live-ui-replay",
            "symbol": "BTCUSDT",
            "total_events": 1,
            "truncated": False,
            "available_symbols": [{"symbol": "BTCUSDT", "event_count": 1}],
            "events": [],
            "candles": [],
        }

    def focus_stub(*arguments: object) -> dict[str, object]:
        calls.append(("focus", arguments))
        return {"run_id": "run-live-ui-replay", "trade_id": "trade-live-focus"}

    async def run_sync(function, *arguments):
        return function(*arguments)

    monkeypatch.setattr(main_module, "replay_preview_from_paths", preview_stub)
    monkeypatch.setattr(main_module, "replay_timeline_from_paths", timeline_stub)
    monkeypatch.setattr(main_module, "replay_focus_from_paths", focus_stub)
    monkeypatch.setattr(main_module.to_process, "run_sync", run_sync)
    client = TestClient(create_app(runtime))

    preview = client.get("/api/replay/run-live-ui-replay/preview?symbol=BTCUSDT")
    timeline = client.get("/api/replay/run-live-ui-replay/timeline?symbol=BTCUSDT")
    focus = client.get(
        "/api/replay/run-live-ui-replay/focus?trade_id=trade-live-focus&profile=BASE"
    )

    assert preview.status_code == 200
    assert timeline.status_code == 200
    assert focus.status_code == 200
    assert [name for name, _ in calls] == ["preview", "timeline", "focus"]
    assert all(arguments[0] == str(ledger.path) for _, arguments in calls)
    assert calls[0][1][2:] == ("run-live-ui-replay", "BTCUSDT", 500)
    assert calls[1][1][2:] == ("run-live-ui-replay", "BTCUSDT", 2_000)
    assert calls[2][1][2:5] == (
        "run-live-ui-replay",
        "trade-live-focus",
        "BASE",
    )
    ledger.close()


def test_live_replay_returns_busy_instead_of_hanging_other_ui_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-replay-busy.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-replay-busy",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    started = threading.Event()
    release = threading.Event()

    async def start_persistent_live_without_network(
        _runtime: PaperRuntime,
        _progress=None,
    ) -> bool:
        _runtime.market_data_state = MarketDataState.LIVE
        _runtime.paused = False
        return True

    def timeline_stub(*_arguments: object) -> dict[str, object]:
        return {
            "run_id": runtime.run_id,
            "symbol": "BTCUSDT",
            "total_events": 0,
            "truncated": False,
            "available_symbols": [],
            "events": [],
            "candles": [],
        }

    async def blocking_run_sync(function, *arguments):
        started.set()
        await asyncio.to_thread(release.wait)
        return function(*arguments)

    monkeypatch.setattr(main_module, "replay_timeline_from_paths", timeline_stub)
    monkeypatch.setattr(main_module.to_process, "run_sync", blocking_run_sync)
    monkeypatch.setattr(
        PaperRuntime,
        "start_persistent_live",
        start_persistent_live_without_network,
    )
    responses: list[object] = []

    with TestClient(create_app(runtime)) as client:
        worker = threading.Thread(
            target=lambda: responses.append(client.get(f"/api/replay/{runtime.run_id}/timeline"))
        )
        worker.start()
        assert started.wait(timeout=2)
        try:
            busy = client.post(f"/api/replay/{runtime.run_id}", json={})
            assert busy.status_code == 409
            assert busy.json()["detail"]["error_code"] == "REPLAY_BUSY"
            assert busy.json()["detail"]["retryable"] is True
        finally:
            release.set()
            worker.join(timeout=2)

    assert not worker.is_alive()
    assert responses and responses[0].status_code == 200
    ledger.close()


def test_live_scanner_uses_actual_strategy_decision_without_fake_score() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-scanner",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    runtime.live_selection = ProviderSelection(
        venue=Venue.BINANCE_USDM,
        instruments={},
        tickers={},
        wide_symbols=("BTCUSDT",),
        deep_symbols=("BTCUSDT",),
        bootstrap_events=(),
    )
    runtime.ingest_live_event(market_event(runtime.run_id, event_id="scanner-depth", ts_ms=1_000))

    scanner = runtime.dashboard()["scanner"]
    assert isinstance(scanner, list)
    assert len(scanner) == 1
    assert scanner[0]["symbol"] == "BTCUSDT"
    assert scanner[0]["score"] is None
    assert scanner[0]["status"] in {"QUALIFIED", "REJECTED"}
    assert scanner[0]["reason_codes"]
    assert scanner[0]["strategy"] != "WARMUP"
