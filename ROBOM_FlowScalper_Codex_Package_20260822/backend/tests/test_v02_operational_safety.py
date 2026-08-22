"""CPU·메모리·디스크 진단과 저장 실패 신규진입 차단을 실제 런타임에 검증한다."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import MarketDataState, RuntimeMode, Venue
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
    assert "OSError" in str(runtime.dashboard()["system"]["persistence_last_error"])
    ledger.close()
