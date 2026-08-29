# 11전략 연구 CLI가 동결 데이터와 LIVE 안전 경계를 지키는지 검증한다.
"""저우선순위 자식 명령·결과 불변조건·안전 관측 집계 회귀검사다."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from backend.app.replay.safety import ReplayLiveSafetySnapshot
from backend.app.research.trial_history import (
    ResearchTrialRecord,
    ResearchTrialStatus,
    evaluate_trial_proposal,
)
from scripts.research_runtime_strategy_replay import (
    SIGNAL_GATE_TARGET_ALL,
    SIGNAL_GATE_TP1_FEASIBILITY,
    STRATEGY_LOGIC_CURRENT,
)
from scripts.run_live_safe_strategy_league_replay import (
    SafetyObservations,
    _acquire_replay_resource_lock,
    _append_trial_history,
    _load_trial_history,
    _release_replay_resource_lock,
    _research_arguments,
    _trial_proposal_from_arguments,
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
        signal_gate=SIGNAL_GATE_TP1_FEASIBILITY,
        signal_gate_target_strategy_id="AGGRESSOR_FLOW_CONTINUATION_V1",
        strategy_logic=STRATEGY_LOGIC_CURRENT,
        target_cpu_ratio=0.25,
        target_archive_read_mib_per_second=16.0,
        live_ledger_path=tmp_path / "run-ledger.sqlite3",
    )

    command = _research_arguments(arguments, tmp_path / "partial.json")

    assert "--all-strategies" in command
    assert "--verify-archive-bytes" in command
    assert command.count("--run-id") == 2
    assert command[-2:] == ("--maximum-events", "12345")
    assert command[command.index("--signal-gate") + 1] == SIGNAL_GATE_TP1_FEASIBILITY
    assert command[command.index("--signal-gate-target-strategy-id") + 1] == (
        "AGGRESSOR_FLOW_CONTINUATION_V1"
    )
    assert command[command.index("--strategy-logic") + 1] == STRATEGY_LOGIC_CURRENT
    assert command[command.index("--target-cpu-ratio") + 1] == "0.25"
    assert command[command.index("--target-archive-read-mib-per-second") + 1] == "16.0"
    assert command[command.index("--live-ledger-path") + 1].endswith(
        "run-ledger.sqlite3"
    )


def test_research_command_accepts_all_strategy_gate_target(tmp_path: Path) -> None:
    arguments = Namespace(
        project_root=tmp_path,
        archive=tmp_path / "archive",
        dataset_manifest=tmp_path / "manifest.json",
        run_id=None,
        maximum_events=1_000,
        signal_gate=SIGNAL_GATE_TP1_FEASIBILITY,
        signal_gate_target_strategy_id=SIGNAL_GATE_TARGET_ALL,
        strategy_logic=STRATEGY_LOGIC_CURRENT,
        target_cpu_ratio=0.25,
        target_archive_read_mib_per_second=16.0,
        live_ledger_path=tmp_path / "run-ledger.sqlite3",
    )

    command = _research_arguments(arguments, tmp_path / "partial.json")

    assert command[command.index("--signal-gate-target-strategy-id") + 1] == (
        SIGNAL_GATE_TARGET_ALL
    )


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
        "signal_gate": SIGNAL_GATE_TP1_FEASIBILITY,
        "signal_gate_target_strategy_id": "AGGRESSOR_FLOW_CONTINUATION_V1",
        "signal_gate_trial_id": (
            f"{SIGNAL_GATE_TP1_FEASIBILITY}:AGGRESSOR_FLOW_CONTINUATION_V1"
        ),
        "strategy_logic": STRATEGY_LOGIC_CURRENT,
        "cooperative_cpu_target_ratio": 0.25,
        "runs": [{} for _ in range(13)],
        "frozen_dataset": {
            "selected_run_count": 13,
            "current_archive_byte_reverification": {
                "status": "PASS",
                "run_count": 13,
                "live_writer_io_priority_gate": True,
                "target_read_mib_per_second": 16.0,
            },
        },
    }

    assert (
        _validate_result_payload(
            payload,
            full_frozen_replay=True,
            signal_gate=SIGNAL_GATE_TP1_FEASIBILITY,
            signal_gate_target_strategy_id="AGGRESSOR_FLOW_CONTINUATION_V1",
            strategy_logic=STRATEGY_LOGIC_CURRENT,
            target_cpu_ratio=0.25,
            target_archive_read_mib_per_second=16.0,
        )
        is payload
    )

    payload["real_orders_enabled"] = True
    with pytest.raises(ValueError, match="불변조건"):
        _validate_result_payload(
            payload,
            full_frozen_replay=True,
            signal_gate=SIGNAL_GATE_TP1_FEASIBILITY,
            signal_gate_target_strategy_id="AGGRESSOR_FLOW_CONTINUATION_V1",
            strategy_logic=STRATEGY_LOGIC_CURRENT,
            target_cpu_ratio=0.25,
            target_archive_read_mib_per_second=16.0,
        )


def test_result_validation_rejects_a_different_strategy_trial() -> None:
    payload = {
        "status": "RESEARCH_STRATEGY_LEAGUE_REPLAY_COMPLETE",
        "method": "ONE_PASS_ALL_REGISTERED_ACTUAL_PAPER_RUNTIME_PATH",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "runtime_ai_order_decision": False,
        "strategy_count": 11,
        "strategy_account_count": 22,
        "signal_gate": SIGNAL_GATE_TP1_FEASIBILITY,
        "signal_gate_target_strategy_id": "VWAP_EXHAUSTION_REVERSION_V1",
        "signal_gate_trial_id": (
            f"{SIGNAL_GATE_TP1_FEASIBILITY}:VWAP_EXHAUSTION_REVERSION_V1"
        ),
        "strategy_logic": STRATEGY_LOGIC_CURRENT,
        "cooperative_cpu_target_ratio": 0.25,
        "runs": [{} for _ in range(13)],
        "frozen_dataset": {
            "selected_run_count": 13,
            "current_archive_byte_reverification": {
                "status": "PASS",
                "run_count": 13,
                "live_writer_io_priority_gate": True,
                "target_read_mib_per_second": 16.0,
            },
        },
    }

    with pytest.raises(ValueError, match="불변조건"):
        _validate_result_payload(
            payload,
            full_frozen_replay=True,
            signal_gate=SIGNAL_GATE_TP1_FEASIBILITY,
            signal_gate_target_strategy_id="AGGRESSOR_FLOW_CONTINUATION_V1",
            strategy_logic=STRATEGY_LOGIC_CURRENT,
            target_cpu_ratio=0.25,
            target_archive_read_mib_per_second=16.0,
        )


def test_safety_observations_report_event_and_stall_counter_deltas() -> None:
    observations = SafetyObservations()
    observations.record(_snapshot())
    observations.record(
        _snapshot(
            event_count=150,
            queue_depth=4,
            lag_p95_ms=120.0,
            reconnects=3,
            planned_rotations=3,
            event_loop_lag_over_500ms_count=4,
            server_time_ms=1_010,
            event_loop_lag_last_over_500ms_ts_ms=1_000,
            event_loop_lag_last_over_500ms_ms=620.0,
            live_event_phase_max_ts_ms=995,
            live_event_phase_max_ms=48.0,
            live_event_phase_max_name="STRATEGY_EVALUATION",
            dashboard_build_max_ts_ms=1_002,
            dashboard_build_max_ms=180.0,
            persistence_flush_max_ts_ms=900,
            persistence_flush_max_ms=400.0,
            wal_checkpoint_last_completed_ts_ms=700,
            wal_checkpoint_max_ms=300.0,
        )
    )

    report = observations.report()

    assert report["sample_count"] == 2
    assert report["event_delta"] == 50
    assert report["event_loop_lag_over_500ms_delta"] == 1
    assert report["maximum_queue_depth"] == 4
    assert report["maximum_lag_p95_ms"] == 120.0
    assert report["previous"]["event_count"] == 100
    assert report["latest_sample_counter_deltas"]["planned_rotations"] == 1
    assert report["latest_sample_counter_deltas"]["event_loop_lag_over_500ms_count"] == 1
    incident = report["event_loop_incident_context"]
    assert incident["same_sample_planned_rotation_incremented"] is True
    assert incident["same_sample_reconnect_incremented"] is True
    assert incident["lag_timestamp_ms"] == 1_000
    assert incident["timing_distance_ms"] == {
        "live_event_phase_max": 5,
        "dashboard_build_max": 2,
        "persistence_flush_max": 100,
        "wal_checkpoint_last_completed": 300,
    }
    assert incident["causality"] == "NOT_PROVEN_TIMING_CORRELATION_ONLY"


def test_safety_observations_do_not_attribute_a_stale_lag_incident() -> None:
    observations = SafetyObservations()
    observations.record(
        _snapshot(
            event_loop_lag_last_over_500ms_ts_ms=900,
            event_loop_lag_last_over_500ms_ms=620.0,
        )
    )
    observations.record(
        _snapshot(
            event_count=125,
            event_loop_lag_last_over_500ms_ts_ms=900,
            event_loop_lag_last_over_500ms_ms=620.0,
        )
    )

    report = observations.report()

    assert report["event_loop_lag_over_500ms_delta"] == 0
    assert report["latest_sample_counter_deltas"]["event_loop_lag_over_500ms_count"] == 0
    assert report["event_loop_incident_context"] is None


def test_live_safe_runner_persists_append_only_trial_history(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_sha256": "manifest-a",
                "runs": [
                    {
                        "run_id": "RUN-ONE",
                        "checksum": "a" * 64,
                        "start_ts_ms": 100,
                        "end_ts_ms": 200,
                        "event_count": 1_000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    arguments = Namespace(
        project_root=project_root,
        dataset_manifest=manifest,
        run_id=None,
        maximum_events=None,
        signal_gate="NONE",
        signal_gate_target_strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
        strategy_logic=STRATEGY_LOGIC_CURRENT,
    )
    proposal = _trial_proposal_from_arguments(arguments)
    record = ResearchTrialRecord(
        trial_id="TRIAL-ONE",
        proposal=proposal,
        status=ResearchTrialStatus.COMPLETE,
        evidence_path="evidence/TRIAL-ONE.json",
    )
    catalog = tmp_path / "trial-history.jsonl"

    _append_trial_history(catalog, record)
    loaded = _load_trial_history(catalog)

    assert loaded == (record,)
    assert evaluate_trial_proposal(loaded, proposal)["decision"] == (
        "BLOCK_DUPLICATE_COMPLETE_TRIAL"
    )


def test_none_gate_history_identity_ignores_an_inactive_target(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_sha256": "manifest-a",
                "runs": [
                    {
                        "run_id": "RUN-ONE",
                        "checksum": "a" * 64,
                        "start_ts_ms": 100,
                        "end_ts_ms": 200,
                        "event_count": 1_000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    common = {
        "project_root": project_root,
        "dataset_manifest": manifest,
        "run_id": None,
        "maximum_events": None,
        "signal_gate": "NONE",
        "strategy_logic": STRATEGY_LOGIC_CURRENT,
    }
    first = _trial_proposal_from_arguments(
        Namespace(
            **common,
            signal_gate_target_strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
        )
    )
    second = _trial_proposal_from_arguments(
        Namespace(
            **common,
            signal_gate_target_strategy_id=SIGNAL_GATE_TARGET_ALL,
        )
    )

    assert first.parameter_fingerprint == second.parameter_fingerprint
    assert first.exact_trial_key == second.exact_trial_key


def test_live_safe_runner_allows_only_one_archive_replay_process(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "strategy-replay.lock"
    first = _acquire_replay_resource_lock(lock_path)

    try:
        with pytest.raises(BlockingIOError):
            _acquire_replay_resource_lock(lock_path)
    finally:
        _release_replay_resource_lock(first)

    second = _acquire_replay_resource_lock(lock_path)
    _release_replay_resource_lock(second)
