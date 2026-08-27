"""CPU·메모리·디스크 진단과 저장 실패 신규진입 차단을 실제 런타임에 검증한다."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import backend.app.ops.resources as resources_module
import backend.app.runtime as runtime_module
import backend.app.storage.parquet as parquet_module
import backend.app.storage.sqlite as sqlite_module
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketDataState, MarketEvent, RuntimeMode, Venue
from backend.app.market_data.candles import Candle
from backend.app.ops import ProcessResourceSampler
from backend.app.runtime import PaperRuntime
from backend.app.storage.parquet import DiskUsage, ParquetEventStore
from backend.app.storage.sqlite import SQLiteLedger
from backend.tests.test_candidate_paper_portfolio import candidate_plan
from backend.tests.test_storage_replay_analytics import _sample_trade
from scripts.soak_live import maximum_observed_growth


def test_process_resource_sampler_reports_actual_cpu_memory_and_disk(tmp_path: Path) -> None:
    sampler = ProcessResourceSampler(tmp_path)
    first = sampler.sample()
    sum(index * index for index in range(20_000))
    second = sampler.sample()

    assert float(str(first["process_memory_mb"])) > 0
    assert first["process_memory_source"] in {
        "CURRENT_RSS_LIBPROC",
        "CURRENT_RSS_PROCFS",
        "CURRENT_WORKING_SET",
    }
    assert float(str(first["process_memory_peak_mb"])) >= float(
        str(first["process_memory_mb"])
    )
    assert str(first["process_memory_peak_source"]).startswith("PEAK_")
    assert float(str(second["process_cpu_percent"])) >= 0
    assert int(str(second["process_threads"])) >= 1
    assert float(str(second["disk_total_mb"])) > 0
    assert float(str(second["disk_free_mb"])) > 0
    assert 0 <= float(str(second["disk_free_ratio"])) <= 1


def test_current_memory_does_not_relabel_peak_rss_as_current(monkeypatch) -> None:
    current_bytes = 128 * 1024**2
    monkeypatch.setattr(resources_module.sys, "platform", "darwin")
    monkeypatch.setattr(resources_module, "_darwin_current_rss_bytes", lambda: current_bytes)

    measured_bytes, source = resources_module._process_memory_bytes()

    assert measured_bytes == current_bytes
    assert source == "CURRENT_RSS_LIBPROC"
    assert source != "PEAK_MAX_RSS"


def test_current_memory_fallback_is_labeled_as_peak(monkeypatch) -> None:
    peak_bytes = 192 * 1024**2
    monkeypatch.setattr(resources_module.sys, "platform", "darwin")
    monkeypatch.setattr(resources_module, "_darwin_current_rss_bytes", lambda: 0)
    monkeypatch.setattr(
        resources_module,
        "_peak_process_memory_bytes",
        lambda: (peak_bytes, "PEAK_MAX_RSS"),
    )

    measured_bytes, source = resources_module._process_memory_bytes()

    assert measured_bytes == peak_bytes
    assert source == "PEAK_MAX_RSS_FALLBACK"


def test_soak_current_and_peak_memory_growth_remain_independent() -> None:
    current_growth = maximum_observed_growth([96.0, 94.0, 95.0], 100.0)
    peak_growth = maximum_observed_growth([162.0, 170.0, 170.0], 150.0)

    assert current_growth == 0.0
    assert peak_growth == 20.0


def test_archive_worker_applies_macos_background_io_policy_once(monkeypatch) -> None:
    calls: list[list[str]] = []

    def record_policy(command, **options):
        calls.append(command)
        assert options == {
            "check": False,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(parquet_module, "_BACKGROUND_IO_POLICY_APPLIED", False)
    monkeypatch.setattr(parquet_module.sys, "platform", "darwin")
    monkeypatch.setattr(parquet_module.Path, "is_file", lambda _path: True)
    monkeypatch.setattr(parquet_module.subprocess, "run", record_policy)

    parquet_module._apply_background_io_policy()
    parquet_module._apply_background_io_policy()

    assert calls == [["/usr/sbin/taskpolicy", "-b", "-p", str(parquet_module.os.getpid())]]
    assert parquet_module._set_background_io_policy(False) is True
    assert parquet_module._set_background_io_policy(True) is True
    assert calls == [
        ["/usr/sbin/taskpolicy", "-b", "-p", str(parquet_module.os.getpid())],
        ["/usr/sbin/taskpolicy", "-B", "-p", str(parquet_module.os.getpid())],
        ["/usr/sbin/taskpolicy", "-b", "-p", str(parquet_module.os.getpid())],
    ]


def test_atomic_sqlite_commit_temporarily_leaves_background_priority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[object] = []
    archive = ParquetEventStore(
        tmp_path / "priority-archive",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(tmp_path / "priority-ledger.sqlite3")
    ledger.start_run(
        "run-priority",
        mode=RuntimeMode.LIVE_SHADOW_PAPER.value,
        venue=Venue.BINANCE_USDM.value,
        config={"execution": "PAPER"},
        started_ts_ms=1_000,
    )
    rows = [
        {
            "event_id": "priority-event",
            "run_id": "run-priority",
            "venue": Venue.BINANCE_USDM.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "receive_monotonic_ns": 1_000,
            "data": {"price": "100", "quantity": "1"},
        }
    ]

    monkeypatch.setattr(
        sqlite_module,
        "_apply_persistence_background_io_policy",
        lambda: calls.append("ARCHIVE_BACKGROUND"),
    )
    monkeypatch.setattr(
        sqlite_module,
        "_set_persistence_background_io_policy",
        lambda enabled: calls.append(enabled) or True,
    )

    timings = sqlite_module.persist_archives_and_candles_in_process(
        str(archive.root),
        0,
        0,
        str(ledger.path),
        [rows],
        [],
    )

    assert calls == ["ARCHIVE_BACKGROUND", False, True]
    assert timings["archive_batches"] == 1
    assert ledger.count("market_event_archives") == 1
    ledger.close()


def test_storage_worker_keeps_low_cpu_priority_without_accumulating(
    monkeypatch,
) -> None:
    current_niceness = 0
    adjustments: list[int] = []

    def adjust_niceness(increment: int) -> int:
        nonlocal current_niceness
        adjustments.append(increment)
        current_niceness = min(19, current_niceness + increment)
        return current_niceness

    monkeypatch.setattr(sqlite_module.os, "nice", adjust_niceness)

    sqlite_module._apply_storage_worker_cpu_priority()
    sqlite_module._apply_storage_worker_cpu_priority()

    assert adjustments == [0, 19, 0]
    assert current_niceness == 19


def test_archive_worker_warms_arrow_and_zstd_without_disk_write(monkeypatch) -> None:
    warmed = 0

    def record_policy() -> None:
        nonlocal warmed
        warmed += 1

    monkeypatch.setattr(parquet_module, "_apply_background_io_policy", record_policy)

    assert parquet_module.warm_market_event_worker_process() == parquet_module.os.getpid()
    assert warmed == 1


async def test_live_runtime_prewarms_archive_worker_once(tmp_path: Path, monkeypatch) -> None:
    archive = ParquetEventStore(
        tmp_path / "warm-market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-warm-worker",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        market_event_archive=archive,
    )
    calls: list[object] = []

    async def run_sync(function, *arguments):
        calls.append(function)
        assert arguments == ()
        return 123

    monkeypatch.setattr(runtime_module.to_process, "run_sync", run_sync)

    assert await runtime._warm_market_archive_worker() is True
    assert await runtime._warm_market_archive_worker() is True
    assert calls == [parquet_module.warm_market_event_worker_process]
    assert runtime._persistence_worker_warmed is True
    assert runtime._persistence_worker_warm_ms >= 0


async def test_live_sink_defers_changed_execution_persistence_to_worker(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-threaded-execution-ledger",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    event = MarketEvent(
        event_id="depth-threaded-ledger",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="DEPTH_UPDATE",
        venue_ts_ms=1_000,
        receive_monotonic_ns=1_000,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
    )
    calls: list[tuple[str, object]] = []

    def ingest(
        self: PaperRuntime,
        received: MarketEvent,
        *,
        defer_execution_persistence: bool = False,
    ) -> None:
        calls.append(("ingest", defer_execution_persistence))
        self.paper_portfolio.audit_events.append(
            {
                "run_id": self.run_id,
                "ts_ms": received.venue_ts_ms,
                "event": "LEAGUE_RISK_REJECTED",
            }
        )

    async def run_sync(function, *arguments):
        calls.append(("thread", arguments))
        return function(*arguments)

    monkeypatch.setattr(PaperRuntime, "ingest_live_event", ingest)
    monkeypatch.setattr(runtime_module.to_thread, "run_sync", run_sync)
    monkeypatch.setattr(PaperRuntime, "_persist_execution_state_safely", lambda *_: True)

    await runtime.ingest_live_event_async(event)

    assert calls == [("ingest", True), ("thread", (1_000,))]


def test_unchanged_execution_state_skips_sqlite_persistence(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-no-ledger-churn",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    writes = 0

    def record_write(_ts_ms: int) -> None:
        nonlocal writes
        writes += 1

    monkeypatch.setattr(
        PaperRuntime,
        "_persist_execution_state",
        lambda _self, ts: record_write(ts),
    )

    assert runtime._persist_execution_state_safely(1_000) is True
    assert writes == 0


def test_runtime_event_memory_rolls_one_event_at_fixed_capacity() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    first = MarketEvent(
        event_id="first",
        run_id="ready",
        venue=Venue.NONE,
        symbol="BTCUSDT",
        event_type="WIDE_TICKER",
        venue_ts_ms=1,
        receive_monotonic_ns=1,
        quality=DataQuality(
            is_live=False,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"last_price": "100"},
    )
    last = first.model_copy(update={"event_id": "last", "venue_ts_ms": 2})

    runtime._events.extend([first] * 10_000)
    runtime._events.append(last)

    assert runtime._events.maxlen == 10_000
    assert len(runtime.events) == 10_000
    assert runtime.events[-1] is last
    assert sum(event is first for event in runtime.events) == 9_999


def test_runtime_plan_rejections_roll_one_row_at_fixed_capacity() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    first = {"event_id": "first"}
    last = {"event_id": "last"}

    runtime.plan_rejections.extend([first] * 2_000)
    runtime.plan_rejections.append(last)

    assert runtime.plan_rejections.maxlen == 2_000
    assert len(runtime.plan_rejections) == 2_000
    assert runtime.plan_rejections[-1] is last
    assert sum(row is first for row in runtime.plan_rejections) == 1_999


def test_live_dashboard_bounds_event_projection(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-bounded-dashboard",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    event = MarketEvent(
        event_id="event",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="WIDE_TICKER",
        venue_ts_ms=1,
        receive_monotonic_ns=1,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"last_price": "100"},
    )
    runtime._events.extend(
        event.model_copy(update={"event_id": f"event-{index}", "venue_ts_ms": index})
        for index in range(1_000)
    )
    observed: dict[str, object] = {}

    def capture_dashboard(_status, events, **_kwargs):
        observed["events"] = events
        return {}

    monkeypatch.setattr(runtime_module, "build_dashboard_snapshot", capture_dashboard)

    runtime.dashboard()

    projected = observed["events"]
    assert isinstance(projected, tuple)
    assert len(projected) == 512
    assert projected[0].event_id == "event-488"
    assert projected[-1].event_id == "event-999"


def test_live_dashboard_strategy_statistics_are_cached_until_trade_state_changes(
    monkeypatch,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-performance-cache-a",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    calls = 0

    class CountingAnalytics:
        def strategy_reports(self, _trades, *, strategy_ids):
            nonlocal calls
            calls += 1
            return [
                {"strategy_id": strategy_id, "profile": profile}
                for strategy_id in strategy_ids
                for profile in ("BASE", "STRESS")
            ]

    monkeypatch.setattr(runtime_module, "TradeAnalytics", CountingAnalytics)

    first = runtime.strategy_performance(include_persisted=False)
    second = runtime.strategy_performance(include_persisted=False)
    runtime.run_id = "run-performance-cache-b"
    third = runtime.strategy_performance(include_persisted=False)

    assert first == second == third
    assert calls == 2


def test_live_dashboard_waits_for_versioned_trade_cache_before_showing_statistics(
    monkeypatch,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-performance-loading",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    recovered_as_current = {
        **_sample_trade("recovered-prior-version"),
        "sample_type": "LIVE_PUBLIC",
        "strategy_version": runtime_module.STRATEGY_VERSION,
    }
    monkeypatch.setattr(
        PaperRuntime,
        "_dashboard_live_shadow_trades",
        lambda _runtime: (recovered_as_current,),
    )
    runtime.dashboard_trade_cache_loading = True

    loading = next(
        row
        for row in runtime.strategy_performance(include_persisted=False)
        if row["strategy_id"] == "LSA_REVERSAL_V1" and row["profile"] == "BASE"
    )
    assert loading["sample_size"] == 0
    assert loading["data_state"] == "LOADING_HISTORY"
    assert runtime.strategy_symbol_performance(include_persisted=False) == []
    assert runtime.strategy_analytics_scope(include_persisted=False)["data_state"] == (
        "LOADING_HISTORY"
    )

    runtime.dashboard_trade_cache_loading = False
    runtime.dashboard_trade_cache_ready = True
    ready = next(
        row
        for row in runtime.strategy_performance(include_persisted=False)
        if row["strategy_id"] == "LSA_REVERSAL_V1" and row["profile"] == "BASE"
    )
    assert ready["sample_size"] == 1
    assert ready["data_state"] == "READY"


def test_live_strategy_symbol_analytics_reuses_warmed_trade_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-analytics-cache.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-analytics-cache",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    cached_trade = _sample_trade("shadow-current")
    cached_trade.update(
        {
            "shadow_trade_id": "shadow-current",
            "run_id": runtime.run_id,
            "closed_ts_ms": cached_trade["exit_ts_ms"],
            "sample_type": "LIVE_PUBLIC",
            "strategy_version": runtime_module.STRATEGY_VERSION,
        }
    )
    ledger.record_shadow_trade(cached_trade)
    runtime._refresh_dashboard_trade_cache()

    def reject_live_ledger_scan(*_args, **_kwargs):
        raise AssertionError("LIVE 분석 요청이 활성 원장을 다시 읽었습니다.")

    monkeypatch.setattr(SQLiteLedger, "list_shadow_trades", reject_live_ledger_scan)

    report = next(
        row
        for row in runtime.strategy_performance(include_persisted=False)
        if row["strategy_id"] == cached_trade["strategy_id"] and row["profile"] == "BASE"
    )
    symbols = runtime.strategy_symbol_performance(include_persisted=False)
    scope = runtime.strategy_analytics_scope(include_persisted=False)

    assert report["sample_size"] == 1
    assert symbols[0]["sample_size"] == 1
    assert scope["excluded_prior_version_samples"] == 0
    ledger.close()


def test_disk_pressure_is_connected_to_runtime_entry_gate_and_dashboard(
    tmp_path: Path,
) -> None:
    guard = ParquetEventStore(
        tmp_path / "parquet",
        minimum_free_bytes=200,
        minimum_free_ratio=0.10,
        disk_usage=lambda _: DiskUsage(total=1_000, used=950, free=50),
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-disk-pressure",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        storage_guard=guard,
    )
    runtime.market_data_state = MarketDataState.LIVE
    runtime.paused = False

    assert runtime._refresh_storage_safety(force=True) is False
    assert runtime.paused is True
    assert "STORAGE_PRESSURE_ENTRY_LOCK" in runtime.runtime_health_flags
    dashboard = runtime.dashboard()
    assert dashboard["system"]["storage_entry_allowed"] is False
    assert dashboard["system"]["disk_pressure_entry_lock"] is True
    assert dashboard["system"]["storage_lock_reason"] == "FREE_BYTES_BELOW_LIMIT"
    assert float(str(dashboard["system"]["process_memory_mb"])) > 0
    assert dashboard["operation_status"]["state"] == "SAFETY_WAITING"
    assert dashboard["operation_status"]["automatic_recovery"] is True

    plan = replace(
        candidate_plan(),
        run_id=runtime.run_id,
        venue=Venue.BINANCE_USDM,
    )
    runtime.paper_portfolio.offer((plan,), entries_paused=runtime.paused)
    assert runtime.paper_portfolio.main.pending_entry is None


def test_runtime_checks_active_ledger_volume_as_well_as_archive(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "archive"
    ledger_path = tmp_path / "separate-ledger" / "ledger.sqlite3"

    def disk_usage(path: Path) -> DiskUsage:
        if path == ledger_path.parent:
            return DiskUsage(total=1_000, used=960, free=40)
        return DiskUsage(total=1_000, used=100, free=900)

    guard = ParquetEventStore(
        archive_path,
        minimum_free_bytes=100,
        minimum_free_ratio=0.10,
        disk_usage=disk_usage,
    )
    ledger = SQLiteLedger(ledger_path)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-ledger-pressure",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        storage_guard=guard,
        ledger=ledger,
    )
    runtime.market_data_state = MarketDataState.LIVE
    runtime.paused = False

    assert runtime._refresh_storage_safety(force=True) is False
    dashboard = runtime.dashboard()
    assert dashboard["system"]["storage_lock_reason"] == (
        "LEDGER_FREE_BYTES_BELOW_LIMIT"
    )
    assert dashboard["system"]["archive_storage_free_bytes"] == 900
    assert dashboard["system"]["ledger_storage_free_bytes"] == 40
    assert runtime.paused is True
    ledger.close()


def test_runtime_persists_only_canonical_one_second_and_replay_candles(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "candles.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-bounded-candles",
        venue=Venue.FIXTURE,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    candles = [
        Candle(
            symbol="BTCUSDT",
            interval_seconds=interval,
            open_ts_ms=1_000,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("2"),
            trade_count=3,
        )
        for interval in (1, 5, 15, 30, 60, 180, 300, 600, 900, 3600)
    ]

    runtime._buffer_completed_candles(candles)

    assert {row["interval_seconds"] for row in runtime._candle_buffer} == {1, 180}
    ledger.close()


def test_rejection_audit_does_not_duplicate_full_recovery_snapshots(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "bounded-snapshots.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-bounded-snapshots",
        venue=Venue.FIXTURE,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    initial_snapshots = ledger.count("snapshots")
    initial_account_snapshots = ledger.count("strategy_account_snapshots")
    runtime.paper_portfolio.audit_events.append(
        {
            "event": "LEAGUE_RISK_REJECTED",
            "run_id": runtime.run_id,
            "symbol": "BTCUSDT",
            "account_id": "LSA_REVERSAL_V1:BASE",
            "ts_ms": 1_000,
        }
    )

    runtime._persist_execution_state(1_000)

    assert ledger.count("execution_audit") == 1
    assert ledger.count("snapshots") == initial_snapshots
    assert ledger.count("strategy_account_snapshots") == initial_account_snapshots

    runtime.paper_portfolio.audit_events.append(
        {
            "event": "LEAGUE_CANDIDATE_ARMED",
            "run_id": runtime.run_id,
            "symbol": "BTCUSDT",
            "account_id": "LSA_REVERSAL_V1:BASE",
            "ts_ms": 2_000,
        }
    )
    runtime._persist_execution_state(2_000)

    assert ledger.count("execution_audit") == 2
    assert ledger.count("snapshots") == initial_snapshots + 1
    assert ledger.count("strategy_account_snapshots") == initial_account_snapshots + 1
    ledger.close()


def test_sqlite_write_fault_fails_closed_and_bounds_retry_buffer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "fault.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-storage-fault",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )

    def fail_write(_rows):
        raise OSError("simulated disk full")

    monkeypatch.setattr(ledger, "record_market_events", fail_write)
    runtime._market_event_buffer = [
        {"event_id": f"event-{index}"} for index in range(12_000)
    ]
    runtime._flush_persistence()

    assert runtime.paused is True
    assert runtime.paper_portfolio.main.risk_state.faulted is True
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" in runtime.runtime_health_flags
    assert runtime._persistence_fault_count == 1
    assert runtime._persistence_buffer_dropped == 2_000
    assert len(runtime._market_event_buffer) == 10_000
    dashboard = runtime.dashboard()
    assert "OSError" in str(dashboard["system"]["persistence_last_error"])
    assert dashboard["operation_status"]["state"] == "SAFETY_BLOCKED"
    assert dashboard["operation_status"]["automatic_recovery"] is False
    ledger.close()


async def test_market_persistence_worker_flushes_outside_ingest_loop(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "worker.sqlite3")
    clock = DeterministicClock()
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-persistence-worker",
        venue=Venue.BINANCE_USDM,
        clock=clock,
        ledger=ledger,
    )
    for index in range(500):
        runtime.ingest_live_event(
            MarketEvent(
                event_id=f"wide-{index}",
                run_id=runtime.run_id,
                venue=runtime.venue,
                symbol="BTCUSDT",
                event_type="WIDE_TICKER",
                venue_ts_ms=index,
                receive_monotonic_ns=index,
                quality=DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=0,
                ),
                data={"last_price": "100"},
            )
        )

    assert ledger.count("market_events") == 0
    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    for _ in range(100):
        if ledger.count("market_events") == 500:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert ledger.count("market_events") == 500
    assert runtime._persistence_fault_count == 0
    ledger.close()


async def test_parquet_persistence_worker_keeps_event_loop_responsive(
    tmp_path: Path,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "worker-parquet.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-parquet-worker",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
        market_event_archive=archive,
    )
    runtime._wal_checkpoint_next_flush = 1
    for index in range(2_000):
        runtime.ingest_live_event(
            MarketEvent(
                event_id=f"wide-parquet-{index}",
                run_id=runtime.run_id,
                venue=runtime.venue,
                symbol="BTCUSDT",
                event_type="WIDE_TICKER",
                venue_ts_ms=index,
                receive_monotonic_ns=index,
                quality=DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=0,
                ),
                data={"last_price": "100", "quote_volume_24h": "1000000"},
            )
        )

    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    heartbeat_ticks = 0
    for _ in range(500):
        if ledger.count("market_event_archives") == 1:
            break
        heartbeat_ticks += 1
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert heartbeat_ticks >= 10
    assert ledger.count("market_event_archives") == 1
    assert ledger.market_event_symbols(runtime.run_id) == [
        {"symbol": "BTCUSDT", "event_count": 2_000}
    ]
    assert runtime._persistence_flush_count >= 1
    assert runtime._persistence_flush_last_completed_ts_ms == runtime.clock.utc_ms()
    assert runtime._persistence_flush_max_ts_ms == runtime.clock.utc_ms()
    assert runtime._persistence_flush_slowest_market_events == 2_000
    assert runtime._persistence_flush_slowest_candles >= 0
    assert runtime._persistence_flush_slowest_archive_batches == 1
    assert runtime._persistence_flush_slowest_archive_ms >= 0
    assert runtime._persistence_flush_slowest_ledger_ms >= 0
    assert runtime._wal_checkpoint_count == 1
    assert runtime._wal_checkpoint_log_frames >= 0
    assert runtime._wal_checkpointed_frames == runtime._wal_checkpoint_log_frames
    assert runtime._wal_checkpoint_fault_count == 0
    assert runtime._persistence_fault_count == 0
    assert runtime._persistence_buffer_dropped == 0
    assert runtime._market_event_buffer == []
    ledger.close()


async def test_parquet_process_fault_restores_batch_and_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-fault",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "worker-parquet-fault.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-parquet-worker-fault",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
        market_event_archive=archive,
    )
    runtime._market_event_buffer = [
        {
            "event_id": "event-process-fault",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "receive_monotonic_ns": 1_000,
            "data": {"price": "100", "quantity": "1"},
        }
    ]

    async def fail_process(*_args: object) -> object:
        raise OSError("simulated process archive fault")

    monkeypatch.setattr(runtime_module.to_process, "run_sync", fail_process)
    await runtime._flush_persistence_isolated(None)

    assert [row["event_id"] for row in runtime._market_event_buffer] == [
        "event-process-fault"
    ]
    assert runtime._persistence_fault_count == 1
    assert runtime._persistence_buffer_dropped == 0
    assert runtime.paused is True
    assert runtime.paper_portfolio.main.risk_state.faulted is True
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" in runtime.runtime_health_flags
    ledger.close()


async def test_incomplete_oversized_wal_checkpoint_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "oversized-wal.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-oversized-wal",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime._wal_checkpoint_next_flush = 1
    runtime._market_event_buffer = [
        {
            "event_id": "event-oversized-wal",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "receive_monotonic_ns": 1_000,
            "data": {"price": "100", "quantity": "1"},
        }
    ]

    async def incomplete_checkpoint(function, *arguments):
        assert function is runtime_module.run_passive_wal_checkpoint_in_process
        assert arguments == (str(ledger.path), True)
        return (0, 20_000, 0)

    monkeypatch.setattr(runtime_module.to_process, "run_sync", incomplete_checkpoint)
    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    for _ in range(100):
        if runtime._persistence_fault_count == 1:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert runtime._wal_checkpoint_count == 1
    assert runtime._wal_checkpoint_busy_count == 1
    assert runtime._persistence_fault_count == 1
    assert runtime.paused is True
    assert runtime.paper_portfolio.main.risk_state.faulted is True
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" in runtime.runtime_health_flags
    ledger.close()


async def test_atomic_ledger_fault_restores_market_and_candle_batches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-ledger-fault",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "worker-ledger-fault.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-atomic-ledger-fault",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
        market_event_archive=archive,
    )
    runtime._market_event_buffer = [
        {
            "event_id": "event-ledger-fault",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "receive_monotonic_ns": 1_000,
            "data": {"price": "100", "quantity": "1"},
        }
    ]
    runtime._candle_buffer = [
        {
            "run_id": runtime.run_id,
            "symbol": "BTCUSDT",
            "interval_seconds": 1,
            "open_ts_ms": 1_000,
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100.5",
            "volume": "1",
        }
    ]

    async def fail_ledger_commit(function, *_args: object) -> object:
        assert function is runtime_module.persist_archives_and_candles_in_process
        raise OSError("simulated atomic ledger fault")

    monkeypatch.setattr(runtime_module.to_process, "run_sync", fail_ledger_commit)
    await runtime._flush_persistence_isolated(None)

    assert [row["event_id"] for row in runtime._market_event_buffer] == [
        "event-ledger-fault"
    ]
    assert [row["open_ts_ms"] for row in runtime._candle_buffer] == [1_000]
    assert ledger.count("market_event_archives") == 0
    assert ledger.count("candles") == 0
    assert runtime._persistence_fault_count == 1
    assert runtime._persistence_buffer_dropped == 0
    assert runtime.paused is True
    assert runtime.paper_portfolio.main.risk_state.faulted is True
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" in runtime.runtime_health_flags
    ledger.close()


def test_market_persistence_batch_is_bounded_on_slow_storage(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "bounded-worker.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-bounded-worker",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime._market_event_buffer = [
        {
            "event_id": f"wide-{index}",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "WIDE_TICKER",
            "venue_ts_ms": index,
            "receive_monotonic_ns": index,
            "data": {"last_price": "100"},
        }
        for index in range(600)
    ]

    runtime._flush_persistence(500)

    assert ledger.count("market_events") == 500
    assert len(runtime._market_event_buffer) == 100
    runtime._flush_persistence(500)
    assert ledger.count("market_events") == 600
    assert runtime._market_event_buffer == []
    ledger.close()


def test_stale_trade_is_archived_but_not_used_for_candles_or_strategy_features() -> None:
    clock = DeterministicClock(current_utc_ms=2_000)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-stale-trade-gate",
        venue=Venue.BINANCE_USDM,
        clock=clock,
    )

    def depth(event_id: str, ts_ms: int) -> MarketEvent:
        return MarketEvent(
            event_id=event_id,
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="DEPTH_UPDATE",
            venue_ts_ms=ts_ms,
            receive_monotonic_ns=clock.monotonic_ns(),
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=10,
            ),
            data={
                "bid": "100",
                "bid_qty": "2",
                "ask": "100.1",
                "ask_qty": "2",
            },
        )

    def trade(event_id: str, ts_ms: int, *, stale: bool) -> MarketEvent:
        return MarketEvent(
            event_id=event_id,
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="TRADE",
            venue_ts_ms=ts_ms,
            transaction_ts_ms=ts_ms,
            receive_monotonic_ns=clock.monotonic_ns(),
            quality=DataQuality(
                is_live=True,
                is_stale=stale,
                sequence_valid=True,
                lag_ms=900 if stale else 10,
                flags=("TRADE_LAG_STALE",) if stale else (),
            ),
            data={
                "price": "100.05",
                "quantity": "1",
                "buyer_is_aggressor": True,
            },
        )

    runtime.ingest_live_event(depth("depth-1", 2_000))
    runtime.ingest_live_event(trade("trade-stale", 2_100, stale=True))
    runtime.ingest_live_event(depth("depth-2", 2_600))

    assert runtime.candle_builder.snapshot("BTCUSDT") == ()
    assert runtime.latest_features["BTCUSDT"].data_healthy is False
    assert runtime.dashboard()["system"]["stale_trade_symbols"] == 1

    runtime.ingest_live_event(trade("trade-fresh", 2_700, stale=False))
    runtime.ingest_live_event(depth("depth-3", 3_200))

    assert runtime.candle_builder.snapshot("BTCUSDT")
    assert runtime.latest_features["BTCUSDT"].data_healthy is True
    assert runtime.dashboard()["system"]["stale_trade_symbols"] == 0


def test_live_dashboard_never_waits_for_sqlite_writer_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "dashboard-cache.sqlite3")
    ledger.start_run(
        "run-prior-dashboard",
        mode="LIVE_SHADOW_PAPER",
        venue="BINANCE_USDM",
        config={"strategy_version": "prior-dashboard-version"},
        started_ts_ms=500,
    )
    ledger.record_shadow_trade(
        {
            **_sample_trade("prior-dashboard-trade"),
            "run_id": "run-prior-dashboard",
            "shadow_trade_id": "prior-dashboard-trade",
            "closed_ts_ms": 2_000,
            "sample_type": "LIVE_PUBLIC",
            "strategy_version": "prior-dashboard-version",
        }
    )
    ledger.record_trade(
        {
            **_sample_trade("prior-dashboard-main-trade"),
            "run_id": "run-prior-dashboard",
            "sample_type": "LIVE_PUBLIC",
            "strategy_version": "prior-dashboard-version",
        }
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-dashboard-cache",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )

    def fail_if_dashboard_reads(*_args, **_kwargs):
        raise AssertionError("LIVE dashboard는 SQLite를 다시 읽으면 안 됩니다.")

    monkeypatch.setattr(ledger, "list_trades", fail_if_dashboard_reads)
    monkeypatch.setattr(ledger, "list_shadow_trades", fail_if_dashboard_reads)

    dashboard = runtime.dashboard()

    assert dashboard["status"]["mode"] == "LIVE_SHADOW_PAPER"
    assert dashboard["history"] == []
    assert dashboard["history_scope"]["excluded_prior_version_samples"] == 1
    assert len(dashboard["strategies"]) == 11
    lsa_base = dashboard["strategies"][0]["performance"]["BASE"]
    assert lsa_base["sample_size"] == 0
    assert lsa_base["excluded_prior_version_samples"] == 1
    ledger.close()
