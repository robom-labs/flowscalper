# 실행 중 PAPER 서비스 soak가 다른 연결 없이 상태·전략·저장 안전선을 판정하는지 검증한다.
"""비침습 서비스 장시간 관찰의 parser와 수용 회귀검사다."""

from __future__ import annotations

from copy import deepcopy

from backend.app.ops.service_soak import (
    RunningServiceSample,
    parse_running_service_sample,
    summarize_running_service_soak,
)


def _strategy(strategy_id: str, *, revision: int = 1) -> dict[str, object]:
    report = {"sample_size": 1, "net_pnl": "-0.25"}
    return {
        "strategy_id": strategy_id,
        "mode": "SHADOW",
        "lifecycle": "SHADOW",
        "settings_revision": revision,
        "manual_lock": False,
        "changed_by": "MIGRATION",
        "change_reason": "TEST_BASELINE",
        "performance": {"BASE": dict(report), "STRESS": dict(report)},
    }


def _payload() -> dict[str, object]:
    strategies = [_strategy("A"), _strategy("B")]
    accounts = [
        {
            "strategy_id": strategy_id,
            "profile": profile,
            "trade_count": 1,
        }
        for strategy_id in ("A", "B")
        for profile in ("BASE", "STRESS")
    ]
    return {
        "status": {
            "run_id": "run-soak",
            "market_data_state": "LIVE",
            "execution_state": "PAPER",
            "trade_count": 1,
            "real_orders_enabled": False,
            "auth_required": False,
        },
        "operation_status": {
            "state": "RUNNING",
            "market_observation_active": True,
            "paper_entry_active": True,
        },
        "system": {
            "event_count": 100,
            "strategy_evaluation_count": 1_000,
            "qualified_signal_count": 2,
            "queue_depth": 0,
            "queue_capacity": 4_096,
            "lag_p95_ms": 50.0,
            "trade_lag_p95_ms": 80.0,
            "wide_lag_p95_ms": 1_200.0,
            "reconnects": 1,
            "planned_rotations": 1,
            "unplanned_reconnects": 0,
            "sequence_gaps": 0,
            "resyncs": 0,
            "dropped_events": 0,
            "persistence_fault_count": 0,
            "persistence_buffer_dropped": 0,
            "persistence_flush_count": 10,
            "persistence_flush_last_ms": 100.0,
            "persistence_flush_slow_count": 0,
            "wal_checkpoint_count": 2,
            "wal_checkpoint_last_ms": 50.0,
            "wal_checkpoint_busy_count": 0,
            "wal_checkpoint_fault_count": 0,
            "wal_checkpoint_log_frames": 10,
            "wal_checkpointed_frames": 10,
            "critical_lag_incident_count": 0,
            "critical_lag_active": False,
            "entry_locked": False,
            "storage_entry_allowed": True,
            "process_cpu_percent": 25.0,
            "process_memory_mb": 200.0,
            "process_memory_peak_mb": 220.0,
            "process_uptime_seconds": 100.0,
            "market_persistence_buffer": 10,
            "candle_persistence_buffer": 2,
            "manual_pause_requested": False,
            "last_error": None,
            "persistence_last_error": "NONE",
            "wal_checkpoint_last_error": "NONE",
        },
        "strategies": strategies,
        "league_accounts": accounts,
        "focus_positions": [],
    }


def _sample(payload: dict[str, object], elapsed: float) -> RunningServiceSample:
    return parse_running_service_sample(
        payload,
        elapsed_seconds=elapsed,
        observed_at=f"2026-08-26T00:00:{int(elapsed):02d}+00:00",
    )


def _advanced_payload() -> dict[str, object]:
    payload = deepcopy(_payload())
    system = payload["system"]
    assert isinstance(system, dict)
    system.update(
        {
            "event_count": 300,
            "strategy_evaluation_count": 1_500,
            "qualified_signal_count": 3,
            "persistence_flush_count": 12,
            "wal_checkpoint_count": 3,
            "process_memory_mb": 210.0,
            "process_memory_peak_mb": 230.0,
            "process_uptime_seconds": 130.0,
        }
    )
    return payload


def test_running_service_soak_passes_only_with_exact_progress_and_dynamic_accounts() -> None:
    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(_advanced_payload(), 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "PASS"
    assert result["event_delta"] == 200
    assert result["strategy_evaluation_delta"] == 500
    assert result["memory_growth_mb"] == 10.0
    assert result["failures"] == []
    assert result["paper_safety"]["additional_market_connection_started"] is False


def test_running_service_soak_rejects_stalled_strategy_and_new_faults() -> None:
    unsafe = _advanced_payload()
    system = unsafe["system"]
    assert isinstance(system, dict)
    system["strategy_evaluation_count"] = 1_000
    system["dropped_events"] = 1
    system["persistence_fault_count"] = 1

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(unsafe, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "strategy_evaluations_continued" in result["failures"]
    assert "no_dropped_events" in result["failures"]
    assert "no_persistence_faults" in result["failures"]


def test_running_service_soak_allows_fail_closed_reconnect_that_recovers() -> None:
    waiting = _advanced_payload()
    operation = waiting["operation_status"]
    system = waiting["system"]
    assert isinstance(operation, dict)
    assert isinstance(system, dict)
    operation.update(
        {
            "state": "RECONNECTING",
            "paper_entry_active": False,
        }
    )
    system["entry_locked"] = True
    final = _advanced_payload()
    final_system = final["system"]
    assert isinstance(final_system, dict)
    final_system.update({"reconnects": 2, "planned_rotations": 2})

    result = summarize_running_service_soak(
        [
            _sample(_payload(), 0.0),
            _sample(waiting, 10.0),
            _sample(final, 30.0),
        ],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "PASS"
    assert result["checks"]["operation_samples_safe"] is True
    assert result["checks"]["planned_reconnects_accounted"] is True


def test_running_service_soak_requires_every_open_position_to_have_paper_protection() -> None:
    unprotected = _advanced_payload()
    unprotected["focus_positions"] = [
        {
            "initial_stop": "99",
            "current_stop": "99",
            "take_profit_1": "102",
            "maximum_planned_loss_usdt": "1",
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        },
        {
            "initial_stop": "99",
            "current_stop": "99",
            "take_profit_1": None,
            "maximum_planned_loss_usdt": "1",
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        },
    ]

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(unprotected, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert result["maximum_open_positions"] == 2
    assert "all_positions_protected" in result["failures"]


def test_running_service_soak_rejects_missing_base_stress_account_pair() -> None:
    invalid = _advanced_payload()
    accounts = invalid["league_accounts"]
    assert isinstance(accounts, list)
    accounts.pop()

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(invalid, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "independent_accounts_complete" in result["failures"]


def test_running_service_soak_requires_audited_revision_for_strategy_transition() -> None:
    changed = _advanced_payload()
    strategies = changed["strategies"]
    assert isinstance(strategies, list)
    first = strategies[0]
    assert isinstance(first, dict)
    first.update({"mode": "OFF", "lifecycle": "QUARANTINED"})

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(changed, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "strategy_transitions_audited" in result["failures"]

    first.update(
        {
            "settings_revision": 2,
            "changed_by": "AUTO_GOVERNOR",
            "change_reason": "TWO_WINDOW_COST_FAILURE",
        }
    )
    repaired = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(changed, 30.0)],
        requested_duration_seconds=30.0,
    )
    assert repaired["status"] == "PASS"


def test_running_service_soak_rejects_process_or_counter_regression() -> None:
    middle = _advanced_payload()
    final = _advanced_payload()
    middle_system = middle["system"]
    final_system = final["system"]
    assert isinstance(middle_system, dict)
    assert isinstance(final_system, dict)
    middle_system.update(
        {
            "event_count": 400,
            "strategy_evaluation_count": 1_800,
            "process_uptime_seconds": 140.0,
        }
    )
    final_system.update(
        {
            "event_count": 350,
            "strategy_evaluation_count": 1_700,
            "process_uptime_seconds": 120.0,
        }
    )

    result = summarize_running_service_soak(
        [
            _sample(_payload(), 0.0),
            _sample(middle, 15.0),
            _sample(final, 30.0),
        ],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "process_not_restarted" in result["failures"]
    assert "event_count_monotonic" in result["failures"]
    assert "strategy_evaluations_monotonic" in result["failures"]


def test_running_service_soak_requires_critical_lag_to_lock_entries() -> None:
    unsafe = _advanced_payload()
    system = unsafe["system"]
    assert isinstance(system, dict)
    system["critical_lag_active"] = True

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(unsafe, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "critical_lag_fail_closed" in result["failures"]
