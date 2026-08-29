# LIVE PAPER 안전이 흔들리면 대용량 저장 Run 검증을 자동 중단한다.
"""LIVE 공개시장 우선의 replay 안전감시와 협력적 취소를 제공한다."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReplayLiveSafetySnapshot:
    """replay가 침범하면 안 되는 LIVE PAPER 최소 안전상태다."""

    run_id: str
    runtime_mode: str
    operation_state: str
    market_data_state: str
    execution_state: str
    process_uptime_seconds: float
    event_count: int
    queue_depth: int
    lag_p95_ms: float
    reconnects: int
    planned_rotations: int
    unplanned_reconnects: int
    sequence_gaps: int
    resyncs: int
    dropped_events: int
    persistence_fault_count: int
    persistence_buffer_dropped: int
    event_loop_lag_over_500ms_count: int
    critical_lag_incident_count: int
    critical_lag_active: bool
    entry_locked: bool
    position_count: int
    storage_entry_allowed: bool
    real_orders_enabled: bool
    auth_required: bool
    last_error: str | None
    server_time_ms: int | None = None
    event_loop_lag_max_ms: float | None = None
    event_loop_lag_last_over_500ms_ts_ms: int | None = None
    event_loop_lag_last_over_500ms_ms: float | None = None
    event_gap_last_over_500ms_ts_ms: int | None = None
    live_event_phase_max_ts_ms: int | None = None
    live_event_phase_max_ms: float | None = None
    live_event_phase_max_name: str | None = None
    dashboard_build_max_ts_ms: int | None = None
    dashboard_build_max_ms: float | None = None
    persistence_flush_max_ts_ms: int | None = None
    persistence_flush_max_ms: float | None = None
    wal_checkpoint_last_completed_ts_ms: int | None = None
    wal_checkpoint_max_ms: float | None = None


@dataclass(frozen=True, slots=True)
class ReplayLiveSafetyThresholds:
    """장시간 replay가 양보해야 하는 보수적 LIVE 상한이다."""

    max_queue_depth: int = 64
    max_lag_p95_ms: float = 500.0
    max_event_stall_seconds: float = 30.0
    poll_seconds: float = 1.0
    max_consecutive_probe_errors: int = 3
    planned_rotation_lock_grace_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.max_queue_depth < 0:
            raise ValueError("max_queue_depth는 0 이상이어야 합니다.")
        if self.max_lag_p95_ms <= 0 or self.max_event_stall_seconds <= 0:
            raise ValueError("지연과 이벤트 정지 상한은 양수여야 합니다.")
        if self.poll_seconds <= 0 or self.planned_rotation_lock_grace_seconds <= 0:
            raise ValueError("감시 간격과 planned rotation 유예시간은 양수여야 합니다.")
        if self.max_consecutive_probe_errors <= 0:
            raise ValueError("연속 감시 오류 상한은 양수여야 합니다.")


class ReplayLiveSafetyViolation(RuntimeError):
    """LIVE 우선 조건이 깨져 replay worker를 중단했음을 보존한다."""

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        super().__init__(", ".join(violations))


def replay_live_safety_snapshot_from_dashboard(
    payload: Mapping[str, object],
) -> ReplayLiveSafetySnapshot:
    """localhost 대시보드를 replay 자동중단용 최소 snapshot으로 엄격히 변환한다."""

    from backend.app.storage.integrity import parse_runtime_safety_sample

    status = payload.get("status")
    system = payload.get("system")
    if not isinstance(status, Mapping) or not isinstance(system, Mapping):
        raise ValueError("대시보드 status 또는 system이 없습니다.")
    runtime_mode = status.get("mode")
    event_loop_lag_count = system.get("event_loop_lag_over_500ms_count")
    if not isinstance(runtime_mode, str) or not runtime_mode:
        raise ValueError("대시보드 status.mode가 올바르지 않습니다.")
    if isinstance(event_loop_lag_count, bool) or not isinstance(event_loop_lag_count, int):
        raise ValueError("대시보드 system.event_loop_lag_over_500ms_count가 올바르지 않습니다.")

    def optional_integer(key: str) -> int | None:
        value = system.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"대시보드 system.{key}가 올바르지 않습니다.")
        return int(value)

    def optional_number(key: str) -> float | None:
        value = system.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"대시보드 system.{key}가 올바르지 않습니다.")
        return float(value)

    def optional_string(key: str) -> str | None:
        value = system.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError(f"대시보드 system.{key}가 올바르지 않습니다.")
        return value

    sample = parse_runtime_safety_sample(payload)
    return ReplayLiveSafetySnapshot(
        run_id=sample.run_id,
        runtime_mode=runtime_mode,
        operation_state=sample.operation_state,
        market_data_state=sample.market_data_state,
        execution_state=sample.execution_state,
        process_uptime_seconds=sample.process_uptime_seconds,
        event_count=sample.event_count,
        queue_depth=sample.queue_depth,
        lag_p95_ms=sample.lag_p95_ms,
        reconnects=sample.reconnects,
        planned_rotations=sample.planned_rotations,
        unplanned_reconnects=sample.unplanned_reconnects,
        sequence_gaps=sample.sequence_gaps,
        resyncs=sample.resyncs,
        dropped_events=sample.dropped_events,
        persistence_fault_count=sample.persistence_fault_count,
        persistence_buffer_dropped=sample.persistence_buffer_dropped,
        event_loop_lag_over_500ms_count=event_loop_lag_count,
        critical_lag_incident_count=sample.critical_lag_incident_count,
        critical_lag_active=sample.critical_lag_active,
        entry_locked=sample.entry_locked,
        position_count=sample.position_count,
        storage_entry_allowed=sample.storage_entry_allowed,
        real_orders_enabled=sample.real_orders_enabled,
        auth_required=sample.auth_required,
        last_error=sample.last_error,
        server_time_ms=optional_integer("server_time_ms"),
        event_loop_lag_max_ms=optional_number("event_loop_lag_max_ms"),
        event_loop_lag_last_over_500ms_ts_ms=optional_integer(
            "event_loop_lag_last_over_500ms_ts_ms"
        ),
        event_loop_lag_last_over_500ms_ms=optional_number("event_loop_lag_last_over_500ms_ms"),
        event_gap_last_over_500ms_ts_ms=optional_integer("event_gap_last_over_500ms_ts_ms"),
        live_event_phase_max_ts_ms=optional_integer("live_event_phase_max_ts_ms"),
        live_event_phase_max_ms=optional_number("live_event_phase_max_ms"),
        live_event_phase_max_name=optional_string("live_event_phase_max_name"),
        dashboard_build_max_ts_ms=optional_integer("dashboard_build_max_ts_ms"),
        dashboard_build_max_ms=optional_number("dashboard_build_max_ms"),
        persistence_flush_max_ts_ms=optional_integer("persistence_flush_max_ts_ms"),
        persistence_flush_max_ms=optional_number("persistence_flush_max_ms"),
        wal_checkpoint_last_completed_ts_ms=optional_integer("wal_checkpoint_last_completed_ts_ms"),
        wal_checkpoint_max_ms=optional_number("wal_checkpoint_max_ms"),
    )


class ReplayLiveSafetyGuard:
    """baseline 이후 새로 생긴 LIVE 위험만 결정적으로 판정한다."""

    def __init__(
        self,
        baseline: ReplayLiveSafetySnapshot,
        *,
        thresholds: ReplayLiveSafetyThresholds | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.baseline = baseline
        self.thresholds = thresholds or ReplayLiveSafetyThresholds()
        self._monotonic = monotonic
        now = monotonic()
        self._last_event_count = baseline.event_count
        self._last_event_progress_monotonic = now
        self._last_planned_rotations = baseline.planned_rotations
        self._planned_lock_deadline_monotonic = 0.0

    def initial_violations(self) -> tuple[str, ...]:
        """검증 시작 전부터 LIVE가 안전하지 않으면 worker를 만들지 않는다."""

        return self._state_violations(self.baseline, allow_planned_transition=False)

    def observe(self, sample: ReplayLiveSafetySnapshot) -> tuple[str, ...]:
        """한 snapshot을 반영하고 baseline 대비 새 안전위반 코드를 반환한다."""

        now = self._monotonic()
        violations: list[str] = []
        if sample.event_count > self._last_event_count:
            self._last_event_count = sample.event_count
            self._last_event_progress_monotonic = now
        elif (
            now - self._last_event_progress_monotonic
            > self.thresholds.max_event_stall_seconds
        ):
            violations.append("EVENT_STREAM_STALLED")
        if sample.planned_rotations > self._last_planned_rotations:
            self._last_planned_rotations = sample.planned_rotations
            self._planned_lock_deadline_monotonic = (
                now + self.thresholds.planned_rotation_lock_grace_seconds
            )
        planned_delta = sample.planned_rotations - self.baseline.planned_rotations
        reconnect_delta = sample.reconnects - self.baseline.reconnects
        allow_planned_transition = bool(
            planned_delta > 0
            and planned_delta in {reconnect_delta, reconnect_delta + 1}
            and now <= self._planned_lock_deadline_monotonic
        )
        violations.extend(
            self._state_violations(
                sample,
                allow_planned_transition=allow_planned_transition,
            )
        )
        counter_fields = (
            (
                "UNPLANNED_RECONNECT",
                self.baseline.unplanned_reconnects,
                sample.unplanned_reconnects,
            ),
            ("SEQUENCE_GAP", self.baseline.sequence_gaps, sample.sequence_gaps),
            ("RESYNC", self.baseline.resyncs, sample.resyncs),
            ("EVENT_DROP", self.baseline.dropped_events, sample.dropped_events),
            (
                "PERSISTENCE_FAULT",
                self.baseline.persistence_fault_count,
                sample.persistence_fault_count,
            ),
            (
                "PERSISTENCE_BUFFER_DROP",
                self.baseline.persistence_buffer_dropped,
                sample.persistence_buffer_dropped,
            ),
            (
                "EVENT_LOOP_LAG_OVER_500MS",
                self.baseline.event_loop_lag_over_500ms_count,
                sample.event_loop_lag_over_500ms_count,
            ),
            (
                "CRITICAL_LAG_INCIDENT",
                self.baseline.critical_lag_incident_count,
                sample.critical_lag_incident_count,
            ),
        )
        violations.extend(
            code for code, before, current in counter_fields if current > before
        )
        planned_transition_counts = bool(
            allow_planned_transition
            and planned_delta == reconnect_delta + 1
            and planned_delta > 0
        )
        if (
            planned_delta < 0
            or reconnect_delta < 0
            or (
                reconnect_delta != planned_delta
                and not planned_transition_counts
            )
        ):
            violations.append("RECONNECT_NOT_PLANNED_ROTATION")
        return tuple(dict.fromkeys(violations))

    def _state_violations(
        self,
        sample: ReplayLiveSafetySnapshot,
        *,
        allow_planned_transition: bool,
    ) -> tuple[str, ...]:
        violations: list[str] = []
        if sample.run_id != self.baseline.run_id:
            violations.append("RUN_CHANGED")
        if sample.process_uptime_seconds < self.baseline.process_uptime_seconds:
            violations.append("PROCESS_RESTARTED")
        if sample.runtime_mode != "LIVE_SHADOW_PAPER":
            violations.append("RUNTIME_NOT_LIVE_PAPER")
        if sample.operation_state != "RUNNING" and not allow_planned_transition:
            violations.append("OPERATION_NOT_RUNNING")
        if sample.market_data_state != "LIVE":
            violations.append("MARKET_NOT_LIVE")
        if sample.execution_state != "PAPER":
            violations.append("EXECUTION_NOT_PAPER")
        if sample.real_orders_enabled:
            violations.append("REAL_ORDERS_ENABLED")
        if sample.auth_required:
            violations.append("AUTH_REQUIRED")
        if not sample.storage_entry_allowed:
            violations.append("STORAGE_ENTRY_BLOCKED")
        if sample.entry_locked and not allow_planned_transition:
            violations.append("ENTRY_LOCKED")
        if sample.position_count:
            violations.append("POSITION_OPENED")
        if sample.last_error is not None:
            violations.append("RUNTIME_ERROR")
        if sample.queue_depth > self.thresholds.max_queue_depth:
            violations.append("QUEUE_LIMIT_EXCEEDED")
        if sample.lag_p95_ms > self.thresholds.max_lag_p95_ms:
            violations.append("LAG_LIMIT_EXCEEDED")
        if sample.critical_lag_active:
            violations.append("CRITICAL_LAG_ACTIVE")
        return tuple(violations)


async def run_with_live_safety[ReplayResultT](
    start_replay: Callable[[], Coroutine[Any, Any, ReplayResultT]],
    *,
    probe: Callable[[], ReplayLiveSafetySnapshot],
    thresholds: ReplayLiveSafetyThresholds | None = None,
) -> ReplayResultT:
    """replay와 LIVE 감시를 함께 실행하고 위험 시 worker task를 즉시 취소한다."""

    active_thresholds = thresholds or ReplayLiveSafetyThresholds()
    guard = ReplayLiveSafetyGuard(probe(), thresholds=active_thresholds)
    initial = guard.initial_violations()
    if initial:
        raise ReplayLiveSafetyViolation(initial)
    replay_task: asyncio.Task[ReplayResultT] = asyncio.create_task(
        start_replay(),
        name="live-safe-stored-replay",
    )
    consecutive_probe_errors = 0
    try:
        while True:
            done, _ = await asyncio.wait(
                {replay_task},
                timeout=active_thresholds.poll_seconds,
            )
            try:
                sample = probe()
            except Exception as error:
                consecutive_probe_errors += 1
                if (
                    consecutive_probe_errors
                    >= active_thresholds.max_consecutive_probe_errors
                ):
                    await _cancel_replay_task(replay_task)
                    raise ReplayLiveSafetyViolation(
                        ("SAFETY_PROBE_FAILED",)
                    ) from error
                if replay_task in done:
                    continue
            else:
                consecutive_probe_errors = 0
                violations = guard.observe(sample)
                if violations:
                    await _cancel_replay_task(replay_task)
                    raise ReplayLiveSafetyViolation(violations)
                if replay_task in done:
                    return await replay_task
    except asyncio.CancelledError:
        await _cancel_replay_task(replay_task)
        raise


async def _cancel_replay_task[ReplayResultT](
    task: asyncio.Task[ReplayResultT],
) -> None:
    """anyio cancellable process 호출까지 종료된 뒤 상위 상태를 확정한다."""

    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
