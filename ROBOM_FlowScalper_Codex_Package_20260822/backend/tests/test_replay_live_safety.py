# LIVE PAPER 안전위반이 저장 Run worker를 자동 종료하는지 검증한다.
"""ReplayLiveSafetyGuard와 async worker 취소 회귀검사다."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from backend.app.replay.safety import (
    ReplayLiveSafetyGuard,
    ReplayLiveSafetySnapshot,
    ReplayLiveSafetyThresholds,
    ReplayLiveSafetyViolation,
    run_with_live_safety,
)


def _snapshot(**overrides: object) -> ReplayLiveSafetySnapshot:
    baseline = ReplayLiveSafetySnapshot(
        run_id="run-live-safe",
        runtime_mode="LIVE_SHADOW_PAPER",
        market_data_state="LIVE",
        event_count=100,
        queue_depth=0,
        lag_p95_ms=50.0,
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
        storage_entry_allowed=True,
        real_orders_enabled=False,
        auth_required=False,
        last_error=None,
    )
    return replace(baseline, **overrides)


def test_replay_guard_allows_only_planned_rotation_lock_grace() -> None:
    now = 0.0
    guard = ReplayLiveSafetyGuard(_snapshot(), monotonic=lambda: now)
    assert guard.initial_violations() == ()

    now = 1.0
    rotation_started = _snapshot(
        event_count=110,
        planned_rotations=3,
        reconnects=2,
        entry_locked=True,
    )
    assert guard.observe(rotation_started) == ()

    now = 2.0
    rotation_completed = replace(
        rotation_started,
        event_count=120,
        reconnects=3,
        entry_locked=False,
    )
    assert guard.observe(rotation_completed) == ()

    now = 3.0
    unplanned = replace(
        rotation_completed,
        event_count=130,
        reconnects=4,
        unplanned_reconnects=1,
    )
    assert guard.observe(unplanned) == (
        "UNPLANNED_RECONNECT",
        "RECONNECT_NOT_PLANNED_ROTATION",
    )


def test_replay_guard_rejects_stall_lag_and_new_critical_incident() -> None:
    now = 0.0
    guard = ReplayLiveSafetyGuard(
        _snapshot(),
        thresholds=ReplayLiveSafetyThresholds(max_event_stall_seconds=30.0),
        monotonic=lambda: now,
    )

    now = 31.0
    violations = guard.observe(
        _snapshot(
            lag_p95_ms=501.0,
            critical_lag_active=True,
            critical_lag_incident_count=1,
        )
    )

    assert violations == (
        "EVENT_STREAM_STALLED",
        "LAG_LIMIT_EXCEEDED",
        "CRITICAL_LAG_ACTIVE",
        "CRITICAL_LAG_INCIDENT",
    )


@pytest.mark.asyncio
async def test_live_safety_cancels_replay_worker_on_critical_lag() -> None:
    calls = 0
    worker_started = asyncio.Event()
    worker_stopped = asyncio.Event()

    def probe() -> ReplayLiveSafetySnapshot:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _snapshot()
        return _snapshot(
            event_count=101,
            lag_p95_ms=2_000.0,
            critical_lag_active=True,
            critical_lag_incident_count=1,
            entry_locked=True,
        )

    async def replay() -> dict[str, object]:
        worker_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            worker_stopped.set()
        return {"unexpected": True}

    with pytest.raises(ReplayLiveSafetyViolation) as raised:
        await run_with_live_safety(
            replay,
            probe=probe,
            thresholds=ReplayLiveSafetyThresholds(poll_seconds=0.005),
        )

    assert worker_started.is_set()
    assert worker_stopped.is_set()
    assert raised.value.violations == (
        "ENTRY_LOCKED",
        "LAG_LIMIT_EXCEEDED",
        "CRITICAL_LAG_ACTIVE",
        "CRITICAL_LAG_INCIDENT",
    )


@pytest.mark.asyncio
async def test_live_safety_returns_result_after_transient_probe_errors() -> None:
    calls = 0

    def probe() -> ReplayLiveSafetySnapshot:
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise TimeoutError("transient probe timeout")
        return _snapshot(event_count=100 + calls)

    async def replay() -> dict[str, object]:
        await asyncio.sleep(0.02)
        return {"replay_id": "replay-safe", "real_orders_enabled": False}

    result = await run_with_live_safety(
        replay,
        probe=probe,
        thresholds=ReplayLiveSafetyThresholds(poll_seconds=0.005),
    )

    assert result == {"replay_id": "replay-safe", "real_orders_enabled": False}
    assert calls >= 4


@pytest.mark.asyncio
async def test_live_safety_does_not_start_worker_from_unsafe_baseline() -> None:
    started = False

    async def replay() -> dict[str, object]:
        nonlocal started
        started = True
        return {}

    with pytest.raises(ReplayLiveSafetyViolation) as raised:
        await run_with_live_safety(
            replay,
            probe=lambda: _snapshot(real_orders_enabled=True),
            thresholds=ReplayLiveSafetyThresholds(poll_seconds=0.005),
        )

    assert raised.value.violations == ("REAL_ORDERS_ENABLED",)
    assert started is False
