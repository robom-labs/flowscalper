"""CPU·메모리·디스크 진단과 저장 실패 신규진입 차단을 실제 런타임에 검증한다."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketDataState, MarketEvent, RuntimeMode, Venue
from backend.app.ops import ProcessResourceSampler
from backend.app.runtime import PaperRuntime
from backend.app.storage.parquet import DiskUsage, ParquetEventStore
from backend.app.storage.sqlite import SQLiteLedger
from backend.tests.test_candidate_paper_portfolio import candidate_plan


def test_process_resource_sampler_reports_actual_cpu_memory_and_disk(tmp_path: Path) -> None:
    sampler = ProcessResourceSampler(tmp_path)
    first = sampler.sample()
    sum(index * index for index in range(20_000))
    second = sampler.sample()

    assert float(str(first["process_memory_mb"])) > 0
    assert float(str(second["process_cpu_percent"])) >= 0
    assert int(str(second["process_threads"])) >= 1
    assert float(str(second["disk_total_mb"])) > 0
    assert float(str(second["disk_free_mb"])) > 0
    assert 0 <= float(str(second["disk_free_ratio"])) <= 1


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


def test_live_dashboard_never_waits_for_sqlite_writer_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "dashboard-cache.sqlite3")
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
    assert len(dashboard["strategies"]) == 8
    ledger.close()
