# 실행 중인 PAPER 서비스에 별도 시장 연결을 추가하지 않고 장시간 안전성을 판정한다.
"""기존 localhost PAPER 서비스의 비침습 soak 표본과 수용 판정을 제공한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.app.storage.integrity import RuntimeSafetyViolation


@dataclass(frozen=True, slots=True)
class RunningServiceSoakThresholds:
    """실행 서비스 관찰을 PASS로 판정할 수치 상한을 고정한다."""

    max_queue_depth: int = 64
    max_processing_lag_p95_ms: float = 500.0
    max_trade_lag_p95_ms: float = 1_000.0
    max_event_loop_lag_ms: float = 500.0
    max_event_stall_seconds: float = 30.0
    max_memory_growth_mb: float = 256.0
    max_market_persistence_buffer: int = 10_000
    max_persistence_flush_last_ms: float = 20_000.0
    max_wal_checkpoint_last_ms: float = 30_000.0

    def __post_init__(self) -> None:
        values = (
            self.max_queue_depth,
            self.max_processing_lag_p95_ms,
            self.max_trade_lag_p95_ms,
            self.max_event_loop_lag_ms,
            self.max_event_stall_seconds,
            self.max_memory_growth_mb,
            self.max_market_persistence_buffer,
            self.max_persistence_flush_last_ms,
            self.max_wal_checkpoint_last_ms,
        )
        if any(value <= 0 for value in values):
            raise ValueError("service soak 상한은 모두 양수여야 합니다.")


@dataclass(frozen=True, slots=True)
class StrategyState:
    strategy_id: str
    mode: str
    lifecycle: str
    settings_revision: int
    manual_lock: bool
    changed_by: str
    change_reason: str


@dataclass(frozen=True, slots=True)
class RunningServiceSample:
    elapsed_seconds: float
    observed_at: str
    run_id: str
    operation_state: str
    market_observation_active: bool
    paper_entry_active: bool
    market_data_state: str
    execution_state: str
    event_count: int
    strategy_evaluation_count: int
    qualified_signal_count: int
    queue_depth: int
    queue_capacity: int
    supervisor_running: bool
    consumer_running: bool
    consumer_delivery_count: int
    consumer_delivery_failure_count: int
    consumer_delivery_drop_count: int
    consumer_recovery_count: int
    consumer_cooperative_yield_count: int
    consumer_fault_active: bool
    queue_overload_active: bool
    queue_overload_incident_count: int
    queue_overload_recovery_count: int
    queue_overload_drop_count: int
    processing_lag_p95_ms: float
    trade_lag_p95_ms: float
    wide_lag_p95_ms: float
    reconnects: int
    planned_rotations: int
    unplanned_reconnects: int
    sequence_gaps: int
    resyncs: int
    dropped_events: int
    persistence_fault_count: int
    persistence_buffer_dropped: int
    persistence_backlog_peak: int
    persistence_backlog_entry_lock_count: int
    persistence_flush_count: int
    persistence_flush_last_ms: float
    persistence_flush_slow_count: int
    execution_persistence_count: int
    execution_persistence_last_ms: float
    execution_persistence_max_ms: float
    execution_persistence_last_items: int
    live_event_processing_count: int
    live_event_processing_last_ms: float
    live_event_processing_max_ms: float
    live_event_processing_over_100ms_count: int
    live_event_processing_max_event_type: str
    live_event_processing_max_symbol: str
    live_event_phase_max_ms: float
    live_event_phase_max_name: str
    live_event_phase_max_event_type: str
    live_event_phase_max_symbol: str
    live_event_phase_over_100ms_count: int
    wal_checkpoint_count: int
    wal_checkpoint_last_ms: float
    wal_checkpoint_busy_count: int
    wal_checkpoint_fault_count: int
    wal_checkpoint_log_frames: int
    wal_checkpointed_frames: int
    wal_checkpoint_deferred_count: int
    wal_checkpoint_last_wal_bytes: int
    critical_lag_event_count: int
    critical_lag_incident_count: int
    critical_lag_last_duration_ms: float
    critical_lag_max_duration_ms: float
    event_gap_max_ms: float
    event_gap_over_500ms_count: int
    event_loop_lag_last_ms: float
    event_loop_lag_max_ms: float
    event_loop_lag_over_100ms_count: int
    event_loop_lag_over_500ms_count: int
    event_loop_lag_last_over_500ms: float
    critical_lag_active: bool
    entry_locked: bool
    storage_entry_allowed: bool
    storage_health_refresh_count: int
    storage_health_refresh_last_ms: float
    storage_health_refresh_max_ms: float
    process_cpu_percent: float
    process_memory_mb: float
    process_memory_peak_mb: float
    process_uptime_seconds: float
    market_persistence_buffer: int
    candle_persistence_buffer: int
    position_count: int
    protected_position_count: int
    main_trade_count: int
    league_trade_count: int
    strategy_count: int
    league_account_count: int
    independent_account_shape_valid: bool
    current_version_base_samples: int
    current_version_stress_samples: int
    current_version_base_net_pnl: str
    current_version_stress_net_pnl: str
    strategy_states: tuple[StrategyState, ...]
    real_orders_enabled: bool
    auth_required: bool
    manual_pause_requested: bool
    last_error: str | None
    persistence_last_error: str
    wal_checkpoint_last_error: str


def parse_running_service_sample(
    payload: Mapping[str, object],
    *,
    elapsed_seconds: float,
    observed_at: str,
) -> RunningServiceSample:
    """대시보드 payload에서 장시간 수용에 필요한 값을 엄격히 읽는다."""

    status = _mapping(payload.get("status"), "status")
    operation = _mapping(payload.get("operation_status"), "operation_status")
    system = _mapping(payload.get("system"), "system")
    strategies = _mapping_rows(payload.get("strategies"), "strategies")
    league_accounts = _mapping_rows(payload.get("league_accounts"), "league_accounts")
    focus_positions = _mapping_rows(payload.get("focus_positions"), "focus_positions")

    strategy_states = tuple(
        StrategyState(
            strategy_id=_string(row.get("strategy_id"), "strategy.strategy_id"),
            mode=_string(row.get("mode"), "strategy.mode"),
            lifecycle=_string(row.get("lifecycle"), "strategy.lifecycle"),
            settings_revision=_integer(
                row.get("settings_revision"), "strategy.settings_revision"
            ),
            manual_lock=_boolean(row.get("manual_lock"), "strategy.manual_lock"),
            changed_by=_string(row.get("changed_by"), "strategy.changed_by"),
            change_reason=_string(row.get("change_reason"), "strategy.change_reason"),
        )
        for row in strategies
    )
    strategy_ids = {state.strategy_id for state in strategy_states}
    profile_pairs = {
        (
            _string(row.get("strategy_id"), "league_account.strategy_id"),
            _string(row.get("profile"), "league_account.profile"),
        )
        for row in league_accounts
    }
    expected_pairs = {
        (strategy_id, profile)
        for strategy_id in strategy_ids
        for profile in ("BASE", "STRESS")
    }
    protected_positions = sum(_position_is_protected(row) for row in focus_positions)
    base_samples, stress_samples, base_net, stress_net = _current_strategy_totals(strategies)
    return RunningServiceSample(
        elapsed_seconds=round(elapsed_seconds, 3),
        observed_at=observed_at,
        run_id=_string(status.get("run_id"), "status.run_id"),
        operation_state=_string(operation.get("state"), "operation_status.state"),
        market_observation_active=_boolean(
            operation.get("market_observation_active"),
            "operation_status.market_observation_active",
        ),
        paper_entry_active=_boolean(
            operation.get("paper_entry_active"), "operation_status.paper_entry_active"
        ),
        market_data_state=_string(
            status.get("market_data_state"), "status.market_data_state"
        ),
        execution_state=_string(status.get("execution_state"), "status.execution_state"),
        event_count=_integer(system.get("event_count"), "system.event_count"),
        strategy_evaluation_count=_integer(
            system.get("strategy_evaluation_count"), "system.strategy_evaluation_count"
        ),
        qualified_signal_count=_integer(
            system.get("qualified_signal_count"), "system.qualified_signal_count"
        ),
        queue_depth=_integer(system.get("queue_depth"), "system.queue_depth"),
        queue_capacity=_integer(system.get("queue_capacity"), "system.queue_capacity"),
        supervisor_running=_boolean(
            system.get("supervisor_running"), "system.supervisor_running"
        ),
        consumer_running=_boolean(
            system.get("consumer_running"), "system.consumer_running"
        ),
        consumer_delivery_count=_integer(
            system.get("consumer_delivery_count"), "system.consumer_delivery_count"
        ),
        consumer_delivery_failure_count=_integer(
            system.get("consumer_delivery_failure_count"),
            "system.consumer_delivery_failure_count",
        ),
        consumer_delivery_drop_count=_integer(
            system.get("consumer_delivery_drop_count"),
            "system.consumer_delivery_drop_count",
        ),
        consumer_recovery_count=_integer(
            system.get("consumer_recovery_count"), "system.consumer_recovery_count"
        ),
        consumer_cooperative_yield_count=_integer(
            system.get("consumer_cooperative_yield_count") or 0,
            "system.consumer_cooperative_yield_count",
        ),
        consumer_fault_active=_boolean(
            system.get("consumer_fault_active"), "system.consumer_fault_active"
        ),
        queue_overload_active=_boolean(
            system.get("queue_overload_active"), "system.queue_overload_active"
        ),
        queue_overload_incident_count=_integer(
            system.get("queue_overload_incident_count"),
            "system.queue_overload_incident_count",
        ),
        queue_overload_recovery_count=_integer(
            system.get("queue_overload_recovery_count"),
            "system.queue_overload_recovery_count",
        ),
        queue_overload_drop_count=_integer(
            system.get("queue_overload_drop_count"),
            "system.queue_overload_drop_count",
        ),
        processing_lag_p95_ms=_number(system.get("lag_p95_ms"), "system.lag_p95_ms"),
        trade_lag_p95_ms=_number(
            system.get("trade_lag_p95_ms"), "system.trade_lag_p95_ms"
        ),
        wide_lag_p95_ms=_number(
            system.get("wide_lag_p95_ms"), "system.wide_lag_p95_ms"
        ),
        reconnects=_integer(system.get("reconnects"), "system.reconnects"),
        planned_rotations=_integer(
            system.get("planned_rotations"), "system.planned_rotations"
        ),
        unplanned_reconnects=_integer(
            system.get("unplanned_reconnects"), "system.unplanned_reconnects"
        ),
        sequence_gaps=_integer(system.get("sequence_gaps"), "system.sequence_gaps"),
        resyncs=_integer(system.get("resyncs"), "system.resyncs"),
        dropped_events=_integer(system.get("dropped_events"), "system.dropped_events"),
        persistence_fault_count=_integer(
            system.get("persistence_fault_count"), "system.persistence_fault_count"
        ),
        persistence_buffer_dropped=_integer(
            system.get("persistence_buffer_dropped"),
            "system.persistence_buffer_dropped",
        ),
        persistence_backlog_peak=_integer(
            system.get("persistence_backlog_peak"),
            "system.persistence_backlog_peak",
        ),
        persistence_backlog_entry_lock_count=_integer(
            system.get("persistence_backlog_entry_lock_count"),
            "system.persistence_backlog_entry_lock_count",
        ),
        persistence_flush_count=_integer(
            system.get("persistence_flush_count"), "system.persistence_flush_count"
        ),
        persistence_flush_last_ms=_number(
            system.get("persistence_flush_last_ms"), "system.persistence_flush_last_ms"
        ),
        persistence_flush_slow_count=_integer(
            system.get("persistence_flush_slow_count"),
            "system.persistence_flush_slow_count",
        ),
        execution_persistence_count=_integer(
            system.get("execution_persistence_count") or 0,
            "system.execution_persistence_count",
        ),
        execution_persistence_last_ms=_number(
            system.get("execution_persistence_last_ms") or 0.0,
            "system.execution_persistence_last_ms",
        ),
        execution_persistence_max_ms=_number(
            system.get("execution_persistence_max_ms") or 0.0,
            "system.execution_persistence_max_ms",
        ),
        execution_persistence_last_items=_integer(
            system.get("execution_persistence_last_items") or 0,
            "system.execution_persistence_last_items",
        ),
        live_event_processing_count=_integer(
            system.get("live_event_processing_count") or 0,
            "system.live_event_processing_count",
        ),
        live_event_processing_last_ms=_number(
            system.get("live_event_processing_last_ms") or 0.0,
            "system.live_event_processing_last_ms",
        ),
        live_event_processing_max_ms=_number(
            system.get("live_event_processing_max_ms") or 0.0,
            "system.live_event_processing_max_ms",
        ),
        live_event_processing_over_100ms_count=_integer(
            system.get("live_event_processing_over_100ms_count") or 0,
            "system.live_event_processing_over_100ms_count",
        ),
        live_event_processing_max_event_type=_string(
            system.get("live_event_processing_max_event_type") or "NONE",
            "system.live_event_processing_max_event_type",
        ),
        live_event_processing_max_symbol=_string(
            system.get("live_event_processing_max_symbol") or "NONE",
            "system.live_event_processing_max_symbol",
        ),
        live_event_phase_max_ms=_number(
            system.get("live_event_phase_max_ms") or 0.0,
            "system.live_event_phase_max_ms",
        ),
        live_event_phase_max_name=_string(
            system.get("live_event_phase_max_name") or "NONE",
            "system.live_event_phase_max_name",
        ),
        live_event_phase_max_event_type=_string(
            system.get("live_event_phase_max_event_type") or "NONE",
            "system.live_event_phase_max_event_type",
        ),
        live_event_phase_max_symbol=_string(
            system.get("live_event_phase_max_symbol") or "NONE",
            "system.live_event_phase_max_symbol",
        ),
        live_event_phase_over_100ms_count=_integer(
            system.get("live_event_phase_over_100ms_count") or 0,
            "system.live_event_phase_over_100ms_count",
        ),
        wal_checkpoint_count=_integer(
            system.get("wal_checkpoint_count"), "system.wal_checkpoint_count"
        ),
        wal_checkpoint_last_ms=_number(
            system.get("wal_checkpoint_last_ms"), "system.wal_checkpoint_last_ms"
        ),
        wal_checkpoint_busy_count=_integer(
            system.get("wal_checkpoint_busy_count"),
            "system.wal_checkpoint_busy_count",
        ),
        wal_checkpoint_fault_count=_integer(
            system.get("wal_checkpoint_fault_count"),
            "system.wal_checkpoint_fault_count",
        ),
        wal_checkpoint_log_frames=_integer(
            system.get("wal_checkpoint_log_frames"),
            "system.wal_checkpoint_log_frames",
        ),
        wal_checkpointed_frames=_integer(
            system.get("wal_checkpointed_frames"), "system.wal_checkpointed_frames"
        ),
        wal_checkpoint_deferred_count=_integer(
            system.get("wal_checkpoint_deferred_count"),
            "system.wal_checkpoint_deferred_count",
        ),
        wal_checkpoint_last_wal_bytes=_integer(
            system.get("wal_checkpoint_last_wal_bytes"),
            "system.wal_checkpoint_last_wal_bytes",
        ),
        critical_lag_event_count=_integer(
            system.get("critical_lag_event_count"),
            "system.critical_lag_event_count",
        ),
        critical_lag_incident_count=_integer(
            system.get("critical_lag_incident_count"),
            "system.critical_lag_incident_count",
        ),
        critical_lag_last_duration_ms=_number(
            system.get("critical_lag_last_duration_ms") or 0.0,
            "system.critical_lag_last_duration_ms",
        ),
        critical_lag_max_duration_ms=_number(
            system.get("critical_lag_max_duration_ms"),
            "system.critical_lag_max_duration_ms",
        ),
        event_gap_max_ms=_number(
            system.get("event_gap_max_ms"), "system.event_gap_max_ms"
        ),
        event_gap_over_500ms_count=_integer(
            system.get("event_gap_over_500ms_count"),
            "system.event_gap_over_500ms_count",
        ),
        event_loop_lag_last_ms=_number(
            system.get("event_loop_lag_last_ms"),
            "system.event_loop_lag_last_ms",
        ),
        event_loop_lag_max_ms=_number(
            system.get("event_loop_lag_max_ms"),
            "system.event_loop_lag_max_ms",
        ),
        event_loop_lag_over_100ms_count=_integer(
            system.get("event_loop_lag_over_100ms_count"),
            "system.event_loop_lag_over_100ms_count",
        ),
        event_loop_lag_over_500ms_count=_integer(
            system.get("event_loop_lag_over_500ms_count"),
            "system.event_loop_lag_over_500ms_count",
        ),
        event_loop_lag_last_over_500ms=_number(
            system.get("event_loop_lag_last_over_500ms_ms") or 0.0,
            "system.event_loop_lag_last_over_500ms_ms",
        ),
        critical_lag_active=_boolean(
            system.get("critical_lag_active"), "system.critical_lag_active"
        ),
        entry_locked=_boolean(system.get("entry_locked"), "system.entry_locked"),
        storage_entry_allowed=_boolean(
            system.get("storage_entry_allowed"), "system.storage_entry_allowed"
        ),
        storage_health_refresh_count=_integer(
            system.get("storage_health_refresh_count") or 0,
            "system.storage_health_refresh_count",
        ),
        storage_health_refresh_last_ms=_number(
            system.get("storage_health_refresh_last_ms") or 0.0,
            "system.storage_health_refresh_last_ms",
        ),
        storage_health_refresh_max_ms=_number(
            system.get("storage_health_refresh_max_ms") or 0.0,
            "system.storage_health_refresh_max_ms",
        ),
        process_cpu_percent=_number(
            system.get("process_cpu_percent"), "system.process_cpu_percent"
        ),
        process_memory_mb=_number(
            system.get("process_memory_mb"), "system.process_memory_mb"
        ),
        process_memory_peak_mb=_number(
            system.get("process_memory_peak_mb"), "system.process_memory_peak_mb"
        ),
        process_uptime_seconds=_number(
            system.get("process_uptime_seconds"), "system.process_uptime_seconds"
        ),
        market_persistence_buffer=_integer(
            system.get("market_persistence_buffer"), "system.market_persistence_buffer"
        ),
        candle_persistence_buffer=_integer(
            system.get("candle_persistence_buffer"), "system.candle_persistence_buffer"
        ),
        position_count=len(focus_positions),
        protected_position_count=protected_positions,
        main_trade_count=_integer(status.get("trade_count"), "status.trade_count"),
        league_trade_count=sum(
            _integer(row.get("trade_count"), "league_account.trade_count")
            for row in league_accounts
        ),
        strategy_count=len(strategy_states),
        league_account_count=len(league_accounts),
        independent_account_shape_valid=(
            profile_pairs == expected_pairs
            and len(league_accounts) == len(expected_pairs)
        ),
        current_version_base_samples=base_samples,
        current_version_stress_samples=stress_samples,
        current_version_base_net_pnl=base_net,
        current_version_stress_net_pnl=stress_net,
        strategy_states=strategy_states,
        real_orders_enabled=_boolean(
            status.get("real_orders_enabled"), "status.real_orders_enabled"
        ),
        auth_required=_boolean(status.get("auth_required"), "status.auth_required"),
        manual_pause_requested=_boolean(
            system.get("manual_pause_requested"), "system.manual_pause_requested"
        ),
        last_error=(
            None
            if system.get("last_error") is None
            else _string(system.get("last_error"), "system.last_error")
        ),
        persistence_last_error=_string(
            system.get("persistence_last_error"), "system.persistence_last_error"
        ),
        wal_checkpoint_last_error=_string(
            system.get("wal_checkpoint_last_error"),
            "system.wal_checkpoint_last_error",
        ),
    )


def summarize_running_service_soak(
    samples: Sequence[RunningServiceSample],
    *,
    requested_duration_seconds: float,
    thresholds: RunningServiceSoakThresholds | None = None,
    probe_error_count: int = 0,
    maximum_consecutive_probe_errors: int = 0,
    max_consecutive_probe_errors: int = 3,
    operator_aborted: bool = False,
) -> dict[str, object]:
    """수집된 표본을 독립 검사로 판정하고 PASS·FAIL·중단을 분리한다."""

    if requested_duration_seconds <= 0:
        raise ValueError("요청 관찰시간은 양수여야 합니다.")
    if max_consecutive_probe_errors <= 0:
        raise ValueError("연속 probe 오류 상한은 양수여야 합니다.")
    active_thresholds = thresholds or RunningServiceSoakThresholds()
    if not samples:
        return {
            "status": "ABORTED_OPERATOR" if operator_aborted else "FAIL",
            "requested_duration_seconds": requested_duration_seconds,
            "observed_duration_seconds": 0.0,
            "checks": {"samples_present": False},
            "failures": ["NO_SAMPLES"],
            "samples": [],
        }
    baseline = samples[0]
    final = samples[-1]
    strategy_transitions = _strategy_transitions(samples)
    longest_event_stall = _longest_event_stall(samples)
    memory_growth = (
        max(sample.process_memory_mb for sample in samples)
        - baseline.process_memory_mb
    )
    event_loop_lag_over_500ms_delta = (
        final.event_loop_lag_over_500ms_count
        - baseline.event_loop_lag_over_500ms_count
    )
    if active_thresholds.max_event_loop_lag_ms == 500.0:
        event_loop_lag_bounded = event_loop_lag_over_500ms_delta == 0
    else:
        event_loop_lag_bounded = (
            max(sample.event_loop_lag_max_ms for sample in samples)
            <= active_thresholds.max_event_loop_lag_ms
        )
    allowed_operation_samples = all(_operation_sample_is_safe(sample) for sample in samples)
    adjacent_samples = tuple(zip(samples, samples[1:], strict=False))
    baseline_strategy_ids = {state.strategy_id for state in baseline.strategy_states}
    counter_checks = {
        "no_consumer_delivery_failures": (
            final.consumer_delivery_failure_count
            == baseline.consumer_delivery_failure_count
        ),
        "no_consumer_delivery_drops": (
            final.consumer_delivery_drop_count == baseline.consumer_delivery_drop_count
        ),
        "no_queue_overload_incidents": (
            final.queue_overload_incident_count == baseline.queue_overload_incident_count
        ),
        "no_queue_overload_drops": (
            final.queue_overload_drop_count == baseline.queue_overload_drop_count
        ),
        "no_unplanned_reconnects": final.unplanned_reconnects == baseline.unplanned_reconnects,
        "no_sequence_gaps": final.sequence_gaps == baseline.sequence_gaps,
        "no_resyncs": final.resyncs == baseline.resyncs,
        "no_dropped_events": final.dropped_events == baseline.dropped_events,
        "no_persistence_faults": (
            final.persistence_fault_count == baseline.persistence_fault_count
        ),
        "no_persistence_buffer_drops": (
            final.persistence_buffer_dropped == baseline.persistence_buffer_dropped
        ),
        "no_persistence_backlog_entry_locks": (
            final.persistence_backlog_entry_lock_count
            == baseline.persistence_backlog_entry_lock_count
        ),
        "no_wal_checkpoint_faults": (
            final.wal_checkpoint_fault_count == baseline.wal_checkpoint_fault_count
        ),
        "no_critical_lag_events": (
            final.critical_lag_event_count == baseline.critical_lag_event_count
        ),
        "no_critical_lag_incidents": (
            final.critical_lag_incident_count == baseline.critical_lag_incident_count
        ),
    }
    checks = {
        "samples_present": len(samples) >= 2,
        "requested_duration_completed": (
            final.elapsed_seconds >= requested_duration_seconds
        ),
        "same_run": all(sample.run_id == baseline.run_id for sample in samples),
        "process_not_restarted": all(
            current.process_uptime_seconds >= previous.process_uptime_seconds
            for previous, current in adjacent_samples
        ),
        "operation_samples_safe": allowed_operation_samples,
        "final_running_live_paper": (
            final.operation_state == "RUNNING"
            and final.market_data_state == "LIVE"
            and final.execution_state == "PAPER"
            and final.market_observation_active
            and not final.entry_locked
        ),
        "events_continued": final.event_count > baseline.event_count,
        "event_count_monotonic": all(
            current.event_count >= previous.event_count
            for previous, current in adjacent_samples
        ),
        "event_stall_bounded": (
            longest_event_stall <= active_thresholds.max_event_stall_seconds
        ),
        "strategy_evaluations_continued": (
            final.strategy_evaluation_count > baseline.strategy_evaluation_count
        ),
        "strategy_evaluations_monotonic": all(
            current.strategy_evaluation_count >= previous.strategy_evaluation_count
            for previous, current in adjacent_samples
        ),
        "qualified_signals_monotonic": all(
            current.qualified_signal_count >= previous.qualified_signal_count
            for previous, current in adjacent_samples
        ),
        "supervisor_running_throughout": all(
            sample.supervisor_running for sample in samples
        ),
        "consumer_running_throughout": all(
            sample.consumer_running for sample in samples
        ),
        "consumer_deliveries_continued": (
            final.consumer_delivery_count > baseline.consumer_delivery_count
        ),
        "consumer_delivery_count_monotonic": all(
            current.consumer_delivery_count >= previous.consumer_delivery_count
            for previous, current in adjacent_samples
        ),
        "consumer_cooperative_yield_count_monotonic": all(
            current.consumer_cooperative_yield_count
            >= previous.consumer_cooperative_yield_count
            for previous, current in adjacent_samples
        ),
        "consumer_fault_never_active": all(
            not sample.consumer_fault_active for sample in samples
        ),
        "queue_overload_never_active": all(
            not sample.queue_overload_active for sample in samples
        ),
        "strategy_shape_stable": all(
            sample.strategy_count == baseline.strategy_count for sample in samples
        ),
        "strategy_ids_stable": all(
            {state.strategy_id for state in sample.strategy_states}
            == baseline_strategy_ids
            for sample in samples
        ),
        "independent_accounts_complete": all(
            sample.independent_account_shape_valid
            and sample.league_account_count == sample.strategy_count * 2
            for sample in samples
        ),
        "strategy_transitions_audited": all(
            transition["revision_advanced"]
            and transition["actor_present"]
            and transition["reason_present"]
            for transition in strategy_transitions
        ),
        "all_positions_protected": all(
            sample.position_count == sample.protected_position_count for sample in samples
        ),
        "critical_lag_fail_closed": all(
            not sample.critical_lag_active
            or (sample.entry_locked and not sample.paper_entry_active)
            for sample in samples
        ),
        "real_orders_disabled": all(not sample.real_orders_enabled for sample in samples),
        "auth_not_required": all(not sample.auth_required for sample in samples),
        "manual_pause_not_requested": all(
            not sample.manual_pause_requested for sample in samples
        ),
        "storage_entry_allowed": all(sample.storage_entry_allowed for sample in samples),
        "no_runtime_errors": all(
            sample.last_error is None
            and sample.persistence_last_error == "NONE"
            and sample.wal_checkpoint_last_error == "NONE"
            for sample in samples
        ),
        "queue_bounded": max(sample.queue_depth for sample in samples)
        <= active_thresholds.max_queue_depth,
        "queue_never_saturated": all(
            sample.queue_depth < sample.queue_capacity for sample in samples
        ),
        "processing_lag_bounded": max(
            sample.processing_lag_p95_ms for sample in samples
        )
        <= active_thresholds.max_processing_lag_p95_ms,
        "trade_lag_bounded": max(sample.trade_lag_p95_ms for sample in samples)
        <= active_thresholds.max_trade_lag_p95_ms,
        "event_loop_lag_bounded": event_loop_lag_bounded,
        "memory_growth_bounded": memory_growth <= active_thresholds.max_memory_growth_mb,
        "market_persistence_buffer_bounded": max(
            sample.market_persistence_buffer for sample in samples
        )
        <= active_thresholds.max_market_persistence_buffer,
        "persistence_flush_continued": (
            final.persistence_flush_count > baseline.persistence_flush_count
        ),
        "persistence_flush_count_monotonic": all(
            current.persistence_flush_count >= previous.persistence_flush_count
            for previous, current in adjacent_samples
        ),
        "execution_persistence_count_monotonic": all(
            current.execution_persistence_count
            >= previous.execution_persistence_count
            for previous, current in adjacent_samples
        ),
        "live_event_processing_count_monotonic": all(
            current.live_event_processing_count
            >= previous.live_event_processing_count
            for previous, current in adjacent_samples
        ),
        "live_event_processing_slow_count_monotonic": all(
            current.live_event_processing_over_100ms_count
            >= previous.live_event_processing_over_100ms_count
            for previous, current in adjacent_samples
        ),
        "live_event_phase_slow_count_monotonic": all(
            current.live_event_phase_over_100ms_count
            >= previous.live_event_phase_over_100ms_count
            for previous, current in adjacent_samples
        ),
        "storage_health_refresh_count_monotonic": all(
            current.storage_health_refresh_count
            >= previous.storage_health_refresh_count
            for previous, current in adjacent_samples
        ),
        "persistence_flush_latency_bounded": max(
            sample.persistence_flush_last_ms for sample in samples
        )
        <= active_thresholds.max_persistence_flush_last_ms,
        "wal_checkpoint_continued": (
            final.wal_checkpoint_count > baseline.wal_checkpoint_count
        ),
        "wal_checkpoint_count_monotonic": all(
            current.wal_checkpoint_count >= previous.wal_checkpoint_count
            for previous, current in adjacent_samples
        ),
        "wal_checkpoint_deferred_count_monotonic": all(
            current.wal_checkpoint_deferred_count
            >= previous.wal_checkpoint_deferred_count
            for previous, current in adjacent_samples
        ),
        "persistence_backlog_lock_count_monotonic": all(
            current.persistence_backlog_entry_lock_count
            >= previous.persistence_backlog_entry_lock_count
            for previous, current in adjacent_samples
        ),
        "wal_checkpoint_latency_bounded": max(
            sample.wal_checkpoint_last_ms for sample in samples
        )
        <= active_thresholds.max_wal_checkpoint_last_ms,
        "wal_checkpoint_complete_at_end": (
            final.wal_checkpoint_log_frames == final.wal_checkpointed_frames
        ),
        "planned_reconnects_accounted": (
            final.reconnects - baseline.reconnects
            == final.planned_rotations - baseline.planned_rotations
        ),
        "reconnect_counters_monotonic": all(
            current.reconnects >= previous.reconnects
            and current.planned_rotations >= previous.planned_rotations
            for previous, current in adjacent_samples
        ),
        "critical_lag_counters_monotonic": all(
            current.critical_lag_event_count >= previous.critical_lag_event_count
            and current.critical_lag_incident_count
            >= previous.critical_lag_incident_count
            for previous, current in adjacent_samples
        ),
        "event_gap_counter_monotonic": all(
            current.event_gap_over_500ms_count
            >= previous.event_gap_over_500ms_count
            for previous, current in adjacent_samples
        ),
        "event_loop_lag_counter_monotonic": all(
            current.event_loop_lag_over_100ms_count
            >= previous.event_loop_lag_over_100ms_count
            and current.event_loop_lag_over_500ms_count
            >= previous.event_loop_lag_over_500ms_count
            for previous, current in adjacent_samples
        ),
        "probe_errors_bounded": (
            maximum_consecutive_probe_errors < max_consecutive_probe_errors
        ),
        **counter_checks,
    }
    failures = [name for name, passed in checks.items() if not passed]
    status = "ABORTED_OPERATOR" if operator_aborted else "PASS" if not failures else "FAIL"
    return {
        "schema": "flowscalper.running_service_soak.v1",
        "status": status,
        "requested_duration_seconds": requested_duration_seconds,
        "observed_duration_seconds": final.elapsed_seconds,
        "run_id": baseline.run_id,
        "sample_count": len(samples),
        "probe_error_count": probe_error_count,
        "maximum_consecutive_probe_errors": maximum_consecutive_probe_errors,
        "thresholds": asdict(active_thresholds),
        "checks": checks,
        "failures": failures,
        "event_delta": final.event_count - baseline.event_count,
        "strategy_evaluation_delta": (
            final.strategy_evaluation_count - baseline.strategy_evaluation_count
        ),
        "qualified_signal_delta": (
            final.qualified_signal_count - baseline.qualified_signal_count
        ),
        "consumer_delivery_delta": (
            final.consumer_delivery_count - baseline.consumer_delivery_count
        ),
        "consumer_delivery_failure_delta": (
            final.consumer_delivery_failure_count
            - baseline.consumer_delivery_failure_count
        ),
        "consumer_delivery_drop_delta": (
            final.consumer_delivery_drop_count - baseline.consumer_delivery_drop_count
        ),
        "consumer_recovery_delta": (
            final.consumer_recovery_count - baseline.consumer_recovery_count
        ),
        "consumer_cooperative_yield_delta": (
            final.consumer_cooperative_yield_count
            - baseline.consumer_cooperative_yield_count
        ),
        "queue_overload_incident_delta": (
            final.queue_overload_incident_count - baseline.queue_overload_incident_count
        ),
        "queue_overload_recovery_delta": (
            final.queue_overload_recovery_count - baseline.queue_overload_recovery_count
        ),
        "queue_overload_drop_delta": (
            final.queue_overload_drop_count - baseline.queue_overload_drop_count
        ),
        "main_trade_delta": final.main_trade_count - baseline.main_trade_count,
        "league_trade_delta": final.league_trade_count - baseline.league_trade_count,
        "current_version_base_sample_delta": (
            final.current_version_base_samples - baseline.current_version_base_samples
        ),
        "current_version_stress_sample_delta": (
            final.current_version_stress_samples - baseline.current_version_stress_samples
        ),
        "current_version_base_net_pnl": final.current_version_base_net_pnl,
        "current_version_stress_net_pnl": final.current_version_stress_net_pnl,
        "maximum_queue_depth": max(sample.queue_depth for sample in samples),
        "maximum_processing_lag_p95_ms": max(
            sample.processing_lag_p95_ms for sample in samples
        ),
        "maximum_trade_lag_p95_ms": max(sample.trade_lag_p95_ms for sample in samples),
        "maximum_wide_lag_p95_ms_observational": max(
            sample.wide_lag_p95_ms for sample in samples
        ),
        "maximum_process_cpu_percent": max(
            sample.process_cpu_percent for sample in samples
        ),
        "baseline_process_memory_mb": baseline.process_memory_mb,
        "maximum_process_memory_mb": max(sample.process_memory_mb for sample in samples),
        "memory_growth_mb": round(max(0.0, memory_growth), 3),
        "maximum_process_memory_peak_mb": max(
            sample.process_memory_peak_mb for sample in samples
        ),
        "maximum_market_persistence_buffer": max(
            sample.market_persistence_buffer for sample in samples
        ),
        "maximum_persistence_backlog_peak": max(
            sample.persistence_backlog_peak for sample in samples
        ),
        "persistence_backlog_entry_lock_delta": (
            final.persistence_backlog_entry_lock_count
            - baseline.persistence_backlog_entry_lock_count
        ),
        "maximum_candle_persistence_buffer": max(
            sample.candle_persistence_buffer for sample in samples
        ),
        "maximum_persistence_flush_last_ms": max(
            sample.persistence_flush_last_ms for sample in samples
        ),
        "execution_persistence_delta": (
            final.execution_persistence_count
            - baseline.execution_persistence_count
        ),
        "maximum_execution_persistence_last_ms": max(
            sample.execution_persistence_last_ms for sample in samples
        ),
        "maximum_execution_persistence_max_ms": max(
            sample.execution_persistence_max_ms for sample in samples
        ),
        "maximum_execution_persistence_last_items": max(
            sample.execution_persistence_last_items for sample in samples
        ),
        "live_event_processing_delta": (
            final.live_event_processing_count - baseline.live_event_processing_count
        ),
        "live_event_processing_over_100ms_delta": (
            final.live_event_processing_over_100ms_count
            - baseline.live_event_processing_over_100ms_count
        ),
        "maximum_live_event_processing_last_ms": max(
            sample.live_event_processing_last_ms for sample in samples
        ),
        "maximum_live_event_processing_max_ms": max(
            sample.live_event_processing_max_ms for sample in samples
        ),
        "live_event_processing_max_event_type": (
            final.live_event_processing_max_event_type
        ),
        "live_event_processing_max_symbol": final.live_event_processing_max_symbol,
        "live_event_phase_over_100ms_delta": (
            final.live_event_phase_over_100ms_count
            - baseline.live_event_phase_over_100ms_count
        ),
        "maximum_live_event_phase_max_ms": max(
            sample.live_event_phase_max_ms for sample in samples
        ),
        "live_event_phase_max_name": final.live_event_phase_max_name,
        "live_event_phase_max_event_type": final.live_event_phase_max_event_type,
        "live_event_phase_max_symbol": final.live_event_phase_max_symbol,
        "storage_health_refresh_delta": (
            final.storage_health_refresh_count - baseline.storage_health_refresh_count
        ),
        "maximum_storage_health_refresh_last_ms": max(
            sample.storage_health_refresh_last_ms for sample in samples
        ),
        "maximum_storage_health_refresh_max_ms": max(
            sample.storage_health_refresh_max_ms for sample in samples
        ),
        "maximum_wal_checkpoint_last_ms": max(
            sample.wal_checkpoint_last_ms for sample in samples
        ),
        "wal_checkpoint_busy_delta": (
            final.wal_checkpoint_busy_count - baseline.wal_checkpoint_busy_count
        ),
        "wal_checkpoint_deferred_delta": (
            final.wal_checkpoint_deferred_count
            - baseline.wal_checkpoint_deferred_count
        ),
        "maximum_wal_checkpoint_observed_bytes": max(
            sample.wal_checkpoint_last_wal_bytes for sample in samples
        ),
        "critical_lag_incident_delta": (
            final.critical_lag_incident_count - baseline.critical_lag_incident_count
        ),
        "critical_lag_event_delta": (
            final.critical_lag_event_count - baseline.critical_lag_event_count
        ),
        "maximum_critical_lag_duration_ms": max(
            sample.critical_lag_max_duration_ms for sample in samples
        ),
        "event_gap_over_500ms_delta": (
            final.event_gap_over_500ms_count - baseline.event_gap_over_500ms_count
        ),
        "maximum_event_gap_ms": max(sample.event_gap_max_ms for sample in samples),
        "event_loop_lag_over_100ms_delta": (
            final.event_loop_lag_over_100ms_count
            - baseline.event_loop_lag_over_100ms_count
        ),
        "event_loop_lag_over_500ms_delta": event_loop_lag_over_500ms_delta,
        "event_loop_lag_last_over_500ms": final.event_loop_lag_last_over_500ms,
        "maximum_event_loop_lag_ms": max(
            sample.event_loop_lag_max_ms for sample in samples
        ),
        "longest_event_stall_seconds": longest_event_stall,
        "maximum_open_positions": max(sample.position_count for sample in samples),
        "strategy_transitions": strategy_transitions,
        "baseline": sample_as_dict(baseline),
        "final": sample_as_dict(final),
        "samples": [sample_as_dict(sample) for sample in samples],
        "paper_safety": {
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_requested": False,
            "api_key_requested": False,
            "wallet_requested": False,
            "additional_market_connection_started": False,
        },
    }


def sample_as_dict(sample: RunningServiceSample) -> dict[str, Any]:
    """중첩 dataclass 표본을 JSON 증거용 dict로 변환한다."""

    return asdict(sample)


def _operation_sample_is_safe(sample: RunningServiceSample) -> bool:
    if sample.execution_state != "PAPER" or sample.real_orders_enabled or sample.auth_required:
        return False
    if sample.operation_state == "RUNNING":
        return sample.market_data_state == "LIVE" and sample.market_observation_active
    if sample.operation_state in {"SAFETY_WAITING", "RECONNECTING"}:
        return sample.entry_locked and not sample.paper_entry_active
    return False


def _position_is_protected(position: Mapping[str, object]) -> int:
    required = (
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
    if any(position.get(field) in {None, "", "—"} for field in required):
        return 0
    if position.get("paper_only") is not True:
        return 0
    if position.get("real_orders_enabled") is not False:
        return 0
    if position.get("auth_required") is not False:
        return 0
    return 1


def _current_strategy_totals(
    strategies: Sequence[Mapping[str, object]],
) -> tuple[int, int, str, str]:
    base_samples = 0
    stress_samples = 0
    base_net = Decimal("0")
    stress_net = Decimal("0")
    for row in strategies:
        performance = _mapping(row.get("performance"), "strategy.performance")
        base = _mapping(performance.get("BASE"), "strategy.performance.BASE")
        stress = _mapping(performance.get("STRESS"), "strategy.performance.STRESS")
        base_samples += _integer(base.get("sample_size"), "BASE.sample_size")
        stress_samples += _integer(stress.get("sample_size"), "STRESS.sample_size")
        base_net += _decimal(base.get("net_pnl"), "BASE.net_pnl")
        stress_net += _decimal(stress.get("net_pnl"), "STRESS.net_pnl")
    return base_samples, stress_samples, str(base_net), str(stress_net)


def _strategy_transitions(samples: Sequence[RunningServiceSample]) -> list[dict[str, object]]:
    transitions: list[dict[str, object]] = []
    previous = {state.strategy_id: state for state in samples[0].strategy_states}
    for sample in samples[1:]:
        current = {state.strategy_id: state for state in sample.strategy_states}
        for strategy_id, state in current.items():
            before = previous.get(strategy_id)
            if before is None or (before.mode, before.lifecycle) == (state.mode, state.lifecycle):
                continue
            transitions.append(
                {
                    "elapsed_seconds": sample.elapsed_seconds,
                    "strategy_id": strategy_id,
                    "before_mode": before.mode,
                    "after_mode": state.mode,
                    "before_lifecycle": before.lifecycle,
                    "after_lifecycle": state.lifecycle,
                    "before_revision": before.settings_revision,
                    "after_revision": state.settings_revision,
                    "revision_advanced": state.settings_revision > before.settings_revision,
                    "actor": state.changed_by,
                    "actor_present": bool(state.changed_by),
                    "reason": state.change_reason,
                    "reason_present": bool(state.change_reason),
                }
            )
        previous = current
    return transitions


def _longest_event_stall(samples: Sequence[RunningServiceSample]) -> float:
    last_progress_elapsed = samples[0].elapsed_seconds
    last_event_count = samples[0].event_count
    maximum = 0.0
    for sample in samples[1:]:
        if sample.event_count > last_event_count:
            last_event_count = sample.event_count
            last_progress_elapsed = sample.elapsed_seconds
        else:
            maximum = max(maximum, sample.elapsed_seconds - last_progress_elapsed)
    return round(maximum, 3)


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeSafetyViolation(f"{field}가 객체가 아닙니다.")
    return value


def _mapping_rows(value: object, field: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list) or not all(isinstance(row, Mapping) for row in value):
        raise RuntimeSafetyViolation(f"{field}가 객체 배열이 아닙니다.")
    return tuple(row for row in value if isinstance(row, Mapping))


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeSafetyViolation(f"{field}가 빈 문자열이거나 문자열이 아닙니다.")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeSafetyViolation(f"{field}가 정수가 아닙니다.")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RuntimeSafetyViolation(f"{field}가 숫자가 아닙니다.")
    return float(value)


def _decimal(value: object, field: str) -> Decimal:
    text = _string(value, field)
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise RuntimeSafetyViolation(f"{field}가 decimal 문자열이 아닙니다.") from error


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeSafetyViolation(f"{field}가 boolean이 아닙니다.")
    return value
