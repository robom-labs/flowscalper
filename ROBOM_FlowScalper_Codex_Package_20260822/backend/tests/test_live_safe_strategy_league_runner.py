# 11전략 연구 CLI가 동결 데이터와 LIVE 안전 경계를 지키는지 검증한다.
"""저우선순위 자식 명령·결과 불변조건·안전 관측 집계 회귀검사다."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from backend.app.replay.safety import ReplayLiveSafetySnapshot
from scripts.run_live_safe_strategy_league_replay import (
    SafetyObservations,
    _research_arguments,
    _validate_result_payload,
)


def _snapshot(**overrides: object) -> ReplayLiveSafetySnapshot:
    values: dict[str, object] = {
        "run_id": "run-safe",
        "runtime_mode": "LIVE_SHADOW_PAPER",
        "operation_state": "RUNNING",
        "market_data_state": "LIVE",
        "execution_state": "PAPER",
        "process_uptime_seconds": 100.0,
        "event_count": 100,
        "queue_depth": 0,
        "lag_p95_ms": 25.0,
        "reconnects": 2,
        "planned_rotations": 2,
        "unplanned_reconnects": 0,
        "sequence_gaps": 0,
        "resyncs": 0,
        "dropped_events": 0,
        "persistence_fault_count": 0,
        "persistence_buffer_dropped": 0,
        "event_loop_lag_over_500ms_count": 3,
        "critical_lag_incident_count": 0,
        "critical_lag_active": False,
        "entry_locked": False,
        "position_count": 0,
        "storage_entry_allowed": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "last_error": None,
    }
    values.update(overrides)
    return ReplayLiveSafetySnapshot(**values)  # type: ignore[arg-type]


def test_research_command_forces_all_strategies_and_archive_verification(
    tmp_path: Path,
) -> None:
    arguments = Namespace(
        project_root=tmp_path,
        archive=tmp_path / "archive",
        dataset_manifest=tmp_path / "manifest.json",
        run_id=["RUN-ONE", "RUN-TWO"],
        maximum_events=12_345,
    )

    command = _research_arguments(arguments, tmp_path / "partial.json")

    assert "--all-strategies" in command
    assert "--verify-archive-bytes" in command
    assert command.count("--run-id") == 2
    assert command[-2:] == ("--maximum-events", "12345")


def test_result_validation_requires_paper_safety_and_current_archive_pass() -> None:
    payload = {
        "status": "RESEARCH_STRATEGY_LEAGUE_REPLAY_COMPLETE",
        "method": "ONE_PASS_ALL_REGISTERED_ACTUAL_PAPER_RUNTIME_PATH",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "runtime_ai_order_decision": False,
        "strategy_count": 11,
        "strategy_account_count": 22,
        "runs": [{} for _ in range(13)],
        "frozen_dataset": {
            "selected_run_count": 13,
            "current_archive_byte_reverification": {
                "status": "PASS",
                "run_count": 13,
            },
        },
    }

    assert _validate_result_payload(payload, full_frozen_replay=True) is payload

    payload["real_orders_enabled"] = True
    with pytest.raises(ValueError, match="불변조건"):
        _validate_result_payload(payload, full_frozen_replay=True)


def test_safety_observations_report_event_and_stall_counter_deltas() -> None:
    observations = SafetyObservations()
    observations.record(_snapshot())
    observations.record(
        _snapshot(
            event_count=150,
            queue_depth=4,
            lag_p95_ms=120.0,
            event_loop_lag_over_500ms_count=3,
        )
    )

    report = observations.report()

    assert report["sample_count"] == 2
    assert report["event_delta"] == 50
    assert report["event_loop_lag_over_500ms_delta"] == 0
    assert report["maximum_queue_depth"] == 4
    assert report["maximum_lag_p95_ms"] == 120.0
