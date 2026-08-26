# 대형 활성 원장은 증분 복제하고 닫힌 사본만 전수검사하는지 검증한다.
"""원장 snapshot 무결성과 LIVE 안전중단 회귀검사다."""

from __future__ import annotations

import shutil
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.storage.integrity import (
    LedgerIntegrityError,
    RuntimeSafetyMonitor,
    RuntimeSafetySample,
    RuntimeSafetyThresholds,
    RuntimeSafetyViolation,
    checkpoint_closed_ledger,
    create_closed_ledger_clone,
    create_online_snapshot,
    parse_runtime_safety_sample,
    runtime_safety_violations,
    transfer_closed_snapshot,
    verify_closed_snapshot,
)
from backend.app.storage.sqlite import SQLiteLedger


class CountingSafety:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.stages: list[str] = []
        self.checkpoints = 0
        self.fail_after = fail_after

    def set_stage(self, stage: str) -> None:
        self.stages.append(stage)

    def checkpoint(self) -> None:
        self.checkpoints += 1
        if self.fail_after is not None and self.checkpoints >= self.fail_after:
            raise RuntimeSafetyViolation("테스트 안전중단")


def _sample(**overrides: object) -> RuntimeSafetySample:
    sample = RuntimeSafetySample(
        observed_at="2026-08-26T00:00:00+00:00",
        run_id="run-safe",
        operation_state="RUNNING",
        market_data_state="LIVE",
        execution_state="PAPER",
        event_count=100,
        queue_depth=0,
        queue_capacity=4096,
        lag_p95_ms=50.0,
        critical_lag_threshold_ms=1_500.0,
        reconnects=2,
        planned_rotations=2,
        unplanned_reconnects=0,
        sequence_gaps=0,
        resyncs=0,
        dropped_events=0,
        persistence_fault_count=0,
        persistence_buffer_dropped=0,
        critical_lag_incident_count=0,
        critical_lag_active=False,
        entry_locked=False,
        position_count=0,
        real_orders_enabled=False,
        auth_required=False,
        storage_entry_allowed=True,
        process_uptime_seconds=100.0,
        last_error=None,
    )
    return replace(sample, **overrides)


def _ledger(path: Path) -> SQLiteLedger:
    ledger = SQLiteLedger(path)
    ledger.start_run(
        "run-safe",
        mode="LIVE_SHADOW_PAPER",
        venue="BINANCE_USDM",
        config={"seed": 20260826},
        started_ts_ms=1_000,
    )
    return ledger


def test_online_snapshot_is_verified_without_direct_source_quick_check(tmp_path: Path) -> None:
    source = tmp_path / "active.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    ledger = _ledger(source)
    safety = CountingSafety()

    copied = create_online_snapshot(
        source,
        snapshot,
        pages_per_step=1,
        step_sleep_seconds=0,
        minimum_free_headroom_bytes=0,
        safety=safety,
    )
    checked = verify_closed_snapshot(
        snapshot,
        progress_opcodes=1,
        progress_sleep_seconds=0,
        safety=safety,
    )

    assert copied.source_journal_mode.lower() == "wal"
    assert copied.snapshot_page_count > 0
    assert copied.backup_iterations > 1
    assert checked.quick_check == "ok"
    assert checked.foreign_key_violation_count == 0
    assert checked.user_version == 7
    assert safety.stages == ["ONLINE_SNAPSHOT", "OFFLINE_SNAPSHOT_QUICK_CHECK"]
    assert ledger.count("runs") == 1
    ledger.close()


def test_online_snapshot_cleans_partial_file_after_safety_abort(tmp_path: Path) -> None:
    source = tmp_path / "active.sqlite3"
    snapshot = tmp_path / "partial.sqlite3"
    ledger = _ledger(source)
    safety = CountingSafety(fail_after=2)

    with pytest.raises(RuntimeSafetyViolation, match="테스트 안전중단"):
        create_online_snapshot(
            source,
            snapshot,
            pages_per_step=1,
            step_sleep_seconds=0,
            minimum_free_headroom_bytes=0,
            safety=safety,
        )

    assert not snapshot.exists()
    assert not Path(f"{snapshot}-wal").exists()
    ledger.close()


def test_closed_snapshot_corruption_is_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "active.sqlite3"
    snapshot = tmp_path / "corrupt.sqlite3"
    ledger = _ledger(source)
    create_online_snapshot(
        source,
        snapshot,
        step_sleep_seconds=0,
        minimum_free_headroom_bytes=0,
    )
    ledger.close()
    snapshot.write_bytes(snapshot.read_bytes()[:100])

    with pytest.raises(LedgerIntegrityError, match="snapshot SQLite 검사 실패"):
        verify_closed_snapshot(snapshot, progress_sleep_seconds=0)


def test_closed_snapshot_quick_check_can_be_interrupted_by_safety(tmp_path: Path) -> None:
    source = tmp_path / "active.sqlite3"
    snapshot = tmp_path / "interrupt.sqlite3"
    ledger = _ledger(source)
    create_online_snapshot(
        source,
        snapshot,
        step_sleep_seconds=0,
        minimum_free_headroom_bytes=0,
    )
    safety = CountingSafety(fail_after=2)

    with pytest.raises(RuntimeSafetyViolation, match="안전감시가 중단"):
        verify_closed_snapshot(
            snapshot,
            progress_opcodes=1,
            progress_sleep_seconds=0,
            safety=safety,
        )
    ledger.close()


def test_runtime_guard_rejects_new_queue_drop_lag_position_and_unplanned_reconnect() -> None:
    baseline = _sample()
    unsafe = _sample(
        event_count=200,
        queue_depth=65,
        lag_p95_ms=501.0,
        reconnects=3,
        planned_rotations=2,
        unplanned_reconnects=1,
        dropped_events=1,
        position_count=1,
        process_uptime_seconds=110.0,
    )

    assert runtime_safety_violations(
        baseline,
        unsafe,
        RuntimeSafetyThresholds(),
    ) == (
        "POSITION_OPENED",
        "QUEUE_LIMIT_EXCEEDED",
        "LAG_LIMIT_EXCEEDED",
        "UNPLANNED_RECONNECT",
        "EVENT_DROP",
        "RECONNECT_NOT_PLANNED_ROTATION",
    )


def test_runtime_guard_allows_only_explicit_planned_rotation_lock_grace() -> None:
    baseline = _sample()
    planned_lock = _sample(
        event_count=150,
        reconnects=3,
        planned_rotations=3,
        entry_locked=True,
        process_uptime_seconds=105.0,
    )

    assert runtime_safety_violations(
        baseline,
        planned_lock,
        RuntimeSafetyThresholds(),
    ) == ("ENTRY_LOCKED",)
    assert runtime_safety_violations(
        baseline,
        planned_lock,
        RuntimeSafetyThresholds(),
        allow_planned_rotation_transition=True,
    ) == ()


def test_runtime_guard_allows_planned_rotation_before_reconnect_within_grace() -> None:
    baseline = _sample()
    rotation_start = _sample(
        event_count=140,
        reconnects=2,
        planned_rotations=3,
        entry_locked=True,
        process_uptime_seconds=104.0,
    )

    assert runtime_safety_violations(
        baseline,
        rotation_start,
        RuntimeSafetyThresholds(),
    ) == ("ENTRY_LOCKED", "RECONNECT_NOT_PLANNED_ROTATION")
    assert runtime_safety_violations(
        baseline,
        rotation_start,
        RuntimeSafetyThresholds(),
        allow_planned_rotation_transition=True,
    ) == ()


def test_runtime_monitor_tolerates_two_transient_probe_timeouts() -> None:
    calls = 0

    def probe() -> RuntimeSafetySample:
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise TimeoutError("transient")
        return _sample(event_count=100 + calls, process_uptime_seconds=100.0 + calls)

    monitor = RuntimeSafetyMonitor(
        probe,
        thresholds=RuntimeSafetyThresholds(
            poll_seconds=0.005,
            max_event_stall_seconds=1.0,
            max_consecutive_probe_errors=3,
        ),
    )
    monitor.start()
    deadline = time.monotonic() + 1.0
    while calls < 4 and time.monotonic() < deadline:
        time.sleep(0.005)
    monitor.stop()
    report = monitor.report()

    assert report["probe_error"] is None
    assert report["probe_error_count"] == 2
    assert report["maximum_consecutive_probe_errors"] == 2
    assert report["event_delta"] > 0


def test_runtime_monitor_fails_after_three_consecutive_probe_errors() -> None:
    calls = 0

    def probe() -> RuntimeSafetySample:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise TimeoutError("persistent")
        return _sample()

    monitor = RuntimeSafetyMonitor(
        probe,
        thresholds=RuntimeSafetyThresholds(
            poll_seconds=0.005,
            max_event_stall_seconds=1.0,
            max_consecutive_probe_errors=3,
        ),
    )
    monitor.start()
    deadline = time.monotonic() + 1.0
    while calls < 4 and time.monotonic() < deadline:
        time.sleep(0.005)

    with pytest.raises(RuntimeSafetyViolation, match="런타임 감시 요청 실패"):
        monitor.checkpoint()
    assert monitor.report()["maximum_consecutive_probe_errors"] == 3

def test_dashboard_parser_keeps_paper_and_health_boundaries() -> None:
    payload = {
        "status": {
            "run_id": "run-safe",
            "market_data_state": "LIVE",
            "execution_state": "PAPER",
            "real_orders_enabled": False,
            "auth_required": False,
        },
        "operation_status": {"state": "RUNNING"},
        "position": None,
        "league_positions": [],
        "system": {
            "event_count": 101,
            "queue_depth": 0,
            "queue_capacity": 4096,
            "lag_p95_ms": 55.0,
            "critical_lag_threshold_ms": 1_500.0,
            "reconnects": 2,
            "planned_rotations": 2,
            "unplanned_reconnects": 0,
            "sequence_gaps": 0,
            "resyncs": 0,
            "dropped_events": 0,
            "persistence_fault_count": 0,
            "persistence_buffer_dropped": 0,
            "critical_lag_incident_count": 0,
            "critical_lag_active": False,
            "entry_locked": False,
            "storage_entry_allowed": True,
            "process_uptime_seconds": 101.0,
            "last_error": None,
        },
    }

    sample = parse_runtime_safety_sample(payload)

    assert sample.run_id == "run-safe"
    assert sample.market_data_state == "LIVE"
    assert sample.execution_state == "PAPER"
    assert sample.real_orders_enabled is False
    assert sample.auth_required is False
    assert sample.position_count == 0


def test_online_snapshot_refuses_source_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "active.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE sample(value INTEGER)")
    connection.close()

    with pytest.raises(LedgerIntegrityError, match="덮어쓸 수 없습니다"):
        create_online_snapshot(source, source, minimum_free_headroom_bytes=0)


def test_online_snapshot_time_limit_removes_partial_copy(tmp_path: Path) -> None:
    source = tmp_path / "active.sqlite3"
    snapshot = tmp_path / "timed-out.sqlite3"
    ledger = _ledger(source)

    with pytest.raises(LedgerIntegrityError, match="시간 상한"):
        create_online_snapshot(
            source,
            snapshot,
            pages_per_step=1,
            step_sleep_seconds=0.001,
            minimum_free_headroom_bytes=0,
            max_duration_seconds=0.000_001,
        )

    assert not snapshot.exists()
    ledger.close()


def test_closed_ledger_checkpoint_and_same_device_clone(tmp_path: Path) -> None:
    source = tmp_path / "closed.sqlite3"
    snapshot = tmp_path / "closed-clone.sqlite3"
    ledger = _ledger(source)
    ledger.close()

    checkpoint = checkpoint_closed_ledger(source)
    cloned = create_closed_ledger_clone(
        source,
        snapshot,
        minimum_free_headroom_bytes=0,
        clone_file=lambda before, after: shutil.copyfile(before, after),
    )
    checked = verify_closed_snapshot(snapshot, progress_sleep_seconds=0)

    assert checkpoint.busy == 0
    assert checkpoint.log_frame_count == checkpoint.checkpointed_frame_count
    assert checkpoint.wal_size_bytes_after == 0
    assert cloned.clone_api == "injected-test-clone"
    assert cloned.source_size_bytes == cloned.snapshot_size_bytes
    assert checked.quick_check == "ok"


def test_closed_ledger_clone_refuses_existing_target(tmp_path: Path) -> None:
    source = tmp_path / "closed.sqlite3"
    snapshot = tmp_path / "existing.sqlite3"
    source.write_bytes(b"source")
    snapshot.write_bytes(b"existing")

    with pytest.raises(LedgerIntegrityError, match="이미 있습니다"):
        create_closed_ledger_clone(
            source,
            snapshot,
            minimum_free_headroom_bytes=0,
            clone_file=lambda before, after: shutil.copyfile(before, after),
        )


def test_closed_snapshot_transfer_is_byte_exact_and_safety_checked(tmp_path: Path) -> None:
    source = tmp_path / "closed.sqlite3"
    verification = tmp_path / "verification.sqlite3"
    source.write_bytes(bytes(range(256)) * 50)
    safety = CountingSafety()

    result = transfer_closed_snapshot(
        source,
        verification,
        minimum_free_headroom_bytes=0,
        chunk_bytes=1_024,
        chunk_sleep_seconds=0,
        require_different_device=False,
        safety=safety,
    )

    assert verification.read_bytes() == source.read_bytes()
    assert result.copied_bytes == source.stat().st_size
    assert result.sha256 == result.verification_sha256
    assert safety.stages == ["TRANSFER_SNAPSHOT_TO_VERIFICATION_DEVICE"]
    assert safety.checkpoints > 2


def test_closed_snapshot_transfer_removes_partial_after_safety_abort(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.sqlite3"
    verification = tmp_path / "partial.sqlite3"
    source.write_bytes(bytes(range(256)) * 50)

    with pytest.raises(RuntimeSafetyViolation, match="테스트 안전중단"):
        transfer_closed_snapshot(
            source,
            verification,
            minimum_free_headroom_bytes=0,
            chunk_bytes=1_024,
            chunk_sleep_seconds=0,
            require_different_device=False,
            safety=CountingSafety(fail_after=3),
        )

    assert not verification.exists()
