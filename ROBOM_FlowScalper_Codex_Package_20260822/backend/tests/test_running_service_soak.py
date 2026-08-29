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


def _protected_position(**overrides: object) -> dict[str, object]:
    position: dict[str, object] = {
        "planned_entry": "100",
        "actual_entry": "100.1",
        "quantity": "1",
        "initial_stop": "99",
        "current_stop": "99",
        "take_profit_1": "102",
        "take_profit_2": "103",
        "maximum_planned_loss_usdt": "1",
        "entry_fee_usdt": "0.06",
        "estimated_exit_fee_usdt": "0.06",
        "slippage_usdt": "0.01",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
    }
    position.update(overrides)
    return position


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
            "supervisor_running": True,
            "consumer_running": True,
            "consumer_delivery_count": 100,
            "consumer_delivery_failure_count": 0,
            "consumer_delivery_drop_count": 0,
            "consumer_recovery_count": 0,
            "consumer_fault_active": False,
            "queue_overload_active": False,
            "queue_overload_incident_count": 0,
            "queue_overload_recovery_count": 0,
            "queue_overload_drop_count": 0,
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
            "persistence_backlog_peak": 10,
            "persistence_backlog_entry_lock_count": 0,
            "persistence_flush_count": 10,
            "persistence_flush_last_ms": 100.0,
            "persistence_flush_slow_count": 0,
            "wal_checkpoint_count": 2,
            "wal_checkpoint_last_ms": 50.0,
            "wal_checkpoint_busy_count": 0,
            "wal_checkpoint_fault_count": 0,
            "wal_checkpoint_log_frames": 10,
            "wal_checkpointed_frames": 10,
            "wal_checkpoint_deferred_count": 0,
            "wal_checkpoint_last_wal_bytes": 1_000,
            "wal_checkpoint_running": False,
            "wal_checkpoint_current_concurrent_flush_delta": 0,
            "wal_checkpoint_last_concurrent_flush_delta": 0,
            "wal_checkpoint_max_concurrent_flush_delta": 0,
            "critical_lag_event_count": 0,
            "critical_lag_incident_count": 0,
            "critical_lag_last_duration_ms": None,
            "critical_lag_max_duration_ms": 0.0,
            "event_gap_max_ms": 0.0,
            "event_gap_over_500ms_count": 0,
            "event_loop_lag_last_ms": 0.0,
            "event_loop_lag_max_ms": 0.0,
            "event_loop_lag_over_100ms_count": 0,
            "event_loop_lag_over_500ms_count": 0,
            "event_loop_lag_last_over_500ms_ms": None,
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
            "consumer_delivery_count": 300,
            "persistence_flush_count": 12,
            "wal_checkpoint_count": 3,
            "wal_checkpoint_last_concurrent_flush_delta": 2,
            "wal_checkpoint_max_concurrent_flush_delta": 2,
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
    assert result["critical_lag_event_delta"] == 0
    assert result["critical_lag_incident_delta"] == 0
    assert result["event_gap_over_500ms_delta"] == 0
    assert result["event_loop_lag_over_100ms_delta"] == 0
    assert result["event_loop_lag_over_500ms_delta"] == 0
    assert result["persistence_backlog_entry_lock_delta"] == 0
    assert result["wal_checkpoint_deferred_delta"] == 0
    assert result["wal_checkpoint_completed_delta"] == 1
    assert result["maximum_wal_checkpoint_last_concurrent_flush_delta"] == 2
    assert result["maximum_wal_checkpoint_concurrent_flush_delta"] == 2
    assert result["failures"] == []
    assert result["paper_safety"]["additional_market_connection_started"] is False


def test_running_service_soak_measures_only_storage_work_completed_in_window() -> None:
    baseline = _payload()
    baseline_system = baseline["system"]
    assert isinstance(baseline_system, dict)
    baseline_system.update(
        {
            "persistence_flush_last_ms": 90_000.0,
            "wal_checkpoint_last_ms": 90_000.0,
        }
    )
    recovered = _advanced_payload()
    recovered_system = recovered["system"]
    assert isinstance(recovered_system, dict)
    recovered_system.update(
        {
            "persistence_flush_last_ms": 5_000.0,
            "wal_checkpoint_last_ms": 3_000.0,
        }
    )

    result = summarize_running_service_soak(
        [_sample(baseline, 0.0), _sample(recovered, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "PASS"
    assert result["maximum_persistence_flush_last_ms"] == 5_000.0
    assert result["maximum_wal_checkpoint_last_ms"] == 3_000.0


def test_running_service_soak_rejects_new_slow_storage_work() -> None:
    slow = _advanced_payload()
    slow_system = slow["system"]
    assert isinstance(slow_system, dict)
    slow_system.update(
        {
            "persistence_flush_last_ms": 20_001.0,
            "wal_checkpoint_last_ms": 30_001.0,
        }
    )

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(slow, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "persistence_flush_latency_bounded" in result["failures"]
    assert "wal_checkpoint_latency_bounded" in result["failures"]


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


def test_running_service_soak_requires_consumer_progress_and_no_task_faults() -> None:
    baseline = _payload()
    baseline_system = baseline["system"]
    assert isinstance(baseline_system, dict)
    baseline_system.update(
        {
            "supervisor_running": True,
            "consumer_running": True,
            "consumer_delivery_count": 100,
            "consumer_delivery_failure_count": 0,
            "consumer_delivery_drop_count": 0,
            "consumer_recovery_count": 0,
            "consumer_fault_active": False,
            "queue_overload_active": False,
            "queue_overload_incident_count": 0,
            "queue_overload_recovery_count": 0,
            "queue_overload_drop_count": 0,
        }
    )
    unsafe = _advanced_payload()
    unsafe_system = unsafe["system"]
    assert isinstance(unsafe_system, dict)
    unsafe_system.update(
        {
            "supervisor_running": False,
            "consumer_running": False,
            "consumer_delivery_count": 100,
            "consumer_delivery_failure_count": 1,
            "consumer_delivery_drop_count": 1,
            "consumer_recovery_count": 0,
            "consumer_fault_active": True,
            "queue_overload_active": True,
            "queue_overload_incident_count": 1,
            "queue_overload_recovery_count": 0,
            "queue_overload_drop_count": 10,
        }
    )

    result = summarize_running_service_soak(
        [_sample(baseline, 0.0), _sample(unsafe, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "supervisor_running_throughout" in result["failures"]
    assert "consumer_running_throughout" in result["failures"]
    assert "consumer_deliveries_continued" in result["failures"]
    assert "no_consumer_delivery_failures" in result["failures"]
    assert "no_consumer_delivery_drops" in result["failures"]
    assert "no_queue_overload_incidents" in result["failures"]


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
        _protected_position(),
        _protected_position(take_profit_2=None),
    ]

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(unprotected, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert result["maximum_open_positions"] == 2
    assert "all_positions_protected" in result["failures"]


def test_running_service_soak_requires_complete_entry_and_cost_contract() -> None:
    required_fields = (
        "planned_entry",
        "actual_entry",
        "quantity",
        "initial_stop",
        "current_stop",
        "take_profit_1",
        "take_profit_2",
        "maximum_planned_loss_usdt",
        "entry_fee_usdt",
        "estimated_exit_fee_usdt",
        "slippage_usdt",
    )

    for field in required_fields:
        invalid = _advanced_payload()
        invalid["focus_positions"] = [_protected_position(**{field: None})]
        result = summarize_running_service_soak(
            [_sample(_payload(), 0.0), _sample(invalid, 30.0)],
            requested_duration_seconds=30.0,
        )

        assert result["status"] == "FAIL", field
        assert "all_positions_protected" in result["failures"], field


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


def test_running_service_soak_rejects_duplicate_base_stress_account_pair() -> None:
    invalid = _advanced_payload()
    accounts = invalid["league_accounts"]
    assert isinstance(accounts, list)
    accounts.append(deepcopy(accounts[0]))

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


def test_running_service_soak_rejects_recovered_critical_lag_and_preserves_diagnostics() -> None:
    recovered = _advanced_payload()
    system = recovered["system"]
    assert isinstance(system, dict)
    system.update(
        {
            "critical_lag_event_count": 20,
            "critical_lag_incident_count": 1,
            "critical_lag_last_duration_ms": 3_230.0,
            "critical_lag_max_duration_ms": 3_230.0,
            "event_gap_max_ms": 7_385.0,
            "event_gap_over_500ms_count": 83,
            "event_loop_lag_last_ms": 250.0,
            "event_loop_lag_max_ms": 250.0,
            "event_loop_lag_over_100ms_count": 1,
            "critical_lag_active": False,
            "entry_locked": False,
        }
    )

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(recovered, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "no_critical_lag_events" in result["failures"]
    assert "no_critical_lag_incidents" in result["failures"]
    assert result["critical_lag_event_delta"] == 20
    assert result["critical_lag_incident_delta"] == 1
    assert result["maximum_critical_lag_duration_ms"] == 3_230.0
    assert result["event_gap_over_500ms_delta"] == 83
    assert result["maximum_event_gap_ms"] == 7_385.0
    assert result["event_loop_lag_over_100ms_delta"] == 1
    assert result["maximum_event_loop_lag_ms"] == 250.0


def test_running_service_soak_rejects_local_event_loop_stall() -> None:
    stalled = _advanced_payload()
    system = stalled["system"]
    assert isinstance(system, dict)
    system.update(
        {
            "event_loop_lag_last_ms": 510.0,
            "event_loop_lag_max_ms": 510.0,
            "event_loop_lag_over_100ms_count": 1,
            "event_loop_lag_over_500ms_count": 1,
            "event_loop_lag_last_over_500ms_ms": 510.0,
            "live_event_phase_max_ms": 505.0,
            "live_event_phase_max_name": "STRATEGY_EVALUATION",
            "live_event_phase_max_event_type": "DEPTH_UPDATE",
            "live_event_phase_max_symbol": "BTCUSDT",
            "live_event_phase_over_100ms_count": 1,
        }
    )

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(stalled, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "event_loop_lag_bounded" in result["failures"]
    assert result["thresholds"]["max_event_loop_lag_ms"] == 500.0
    assert result["maximum_event_loop_lag_ms"] == 510.0
    assert result["event_loop_lag_over_500ms_delta"] == 1
    assert result["live_event_phase_over_100ms_delta"] == 1
    assert result["maximum_live_event_phase_max_ms"] == 505.0
    assert result["live_event_phase_max_name"] == "STRATEGY_EVALUATION"
    assert result["live_event_phase_max_event_type"] == "DEPTH_UPDATE"
    assert result["live_event_phase_max_symbol"] == "BTCUSDT"


def test_running_service_soak_ignores_process_lifetime_event_loop_max_before_baseline() -> None:
    baseline = _payload()
    baseline_system = baseline["system"]
    assert isinstance(baseline_system, dict)
    baseline_system.update(
        {
            "event_loop_lag_max_ms": 1_031.0,
            "event_loop_lag_over_100ms_count": 12,
            "event_loop_lag_over_500ms_count": 1,
            "event_loop_lag_last_over_500ms_ms": 1_031.0,
        }
    )
    stable = _advanced_payload()
    stable_system = stable["system"]
    assert isinstance(stable_system, dict)
    stable_system.update(
        {
            "event_loop_lag_max_ms": 1_031.0,
            "event_loop_lag_over_100ms_count": 14,
            "event_loop_lag_over_500ms_count": 1,
            "event_loop_lag_last_over_500ms_ms": 1_031.0,
        }
    )

    result = summarize_running_service_soak(
        [_sample(baseline, 0.0), _sample(stable, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "PASS"
    assert result["checks"]["event_loop_lag_bounded"] is True
    assert result["event_loop_lag_over_500ms_delta"] == 0
    assert result["maximum_event_loop_lag_ms"] == 1_031.0


def test_running_service_soak_rejects_persistence_backlog_growth_and_lock() -> None:
    backlogged = _advanced_payload()
    system = backlogged["system"]
    assert isinstance(system, dict)
    system.update(
        {
            "market_persistence_buffer": 10_001,
            "persistence_backlog_peak": 10_001,
            "persistence_backlog_entry_lock_count": 1,
            "entry_locked": True,
        }
    )
    operation = backlogged["operation_status"]
    assert isinstance(operation, dict)
    operation.update(
        {
            "state": "SAFETY_WAITING",
            "paper_entry_active": False,
        }
    )

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(backlogged, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "FAIL"
    assert "no_persistence_backlog_entry_locks" in result["failures"]
    assert "market_persistence_buffer_bounded" in result["failures"]
    assert result["persistence_backlog_entry_lock_delta"] == 1
    assert result["maximum_market_persistence_buffer"] == 10_001


def test_running_service_soak_preserves_wal_checkpoint_deferral_evidence() -> None:
    deferred = _advanced_payload()
    system = deferred["system"]
    assert isinstance(system, dict)
    system.update(
        {
            "wal_checkpoint_deferred_count": 3,
            "wal_checkpoint_last_wal_bytes": 8 * 1024 * 1024,
        }
    )

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(deferred, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "PASS"
    assert result["wal_checkpoint_deferred_delta"] == 3
    assert result["maximum_wal_checkpoint_observed_bytes"] == 8 * 1024 * 1024


def test_running_service_soak_accepts_small_wal_deferral_without_checkpoint() -> None:
    deferred = _advanced_payload()
    system = deferred["system"]
    assert isinstance(system, dict)
    system.update(
        {
            "wal_checkpoint_count": 2,
            "wal_checkpoint_deferred_count": 1,
            "wal_checkpoint_last_wal_bytes": 8 * 1024 * 1024,
        }
    )

    result = summarize_running_service_soak(
        [_sample(_payload(), 0.0), _sample(deferred, 30.0)],
        requested_duration_seconds=30.0,
    )

    assert result["status"] == "PASS"
    assert result["checks"]["wal_checkpoint_continued"] is True
