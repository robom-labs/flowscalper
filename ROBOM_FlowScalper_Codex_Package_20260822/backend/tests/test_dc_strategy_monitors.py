# V9 DC01·DC02 monitoring 판정의 대칭성·경계·무주문 계약을 검증한다.
"""V9 Directional Change 전략 monitor 전용 테스트."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.research.dc_strategy_monitors import (
    CompletedFiveMinuteBar,
    ContinuationExitVariant,
    ContinuationHorizon,
    ContinuationMonitorInput,
    ContinuationMonitorResult,
    DCConfirmationDirection,
    ExhaustionCondition,
    IntegrityState,
    MonitorReason,
    MonitorSide,
    MonitorStatus,
    ReversalMonitorInput,
    ReversalMonitorResult,
    ReversalTargetSource,
    TransitionState,
    TrendDirection,
    evaluate_dc01_continuation,
    evaluate_dc01_replay,
    evaluate_dc02_replay,
    evaluate_dc02_reversal,
)

D = Decimal
_CONFIRMATION_TS_MS = 1_000_000
_AS_OF_TS_MS = 1_600_000


def _bar(*, side: MonitorSide = MonitorSide.LONG) -> CompletedFiveMinuteBar:
    if side is MonitorSide.LONG:
        return CompletedFiveMinuteBar(
            close_ts_ms=1_500_000,
            previous_close_ts_ms=1_200_000,
            close=D("100.80"),
            previous_high=D("100.75"),
            previous_low=D("100.00"),
        )
    return CompletedFiveMinuteBar(
        close_ts_ms=1_500_000,
        previous_close_ts_ms=1_200_000,
        close=D("99.20"),
        previous_high=D("100.00"),
        previous_low=D("99.25"),
    )


def _continuation(
    *,
    side: MonitorSide = MonitorSide.LONG,
    sequence: int = 1,
    observation_id: str | None = None,
    as_of_ts_ms: int = _AS_OF_TS_MS,
) -> ContinuationMonitorInput:
    is_long = side is MonitorSide.LONG
    return ContinuationMonitorInput(
        observation_id=observation_id or f"dc01-{side.value.lower()}-{sequence}",
        observation_sequence=sequence,
        as_of_ts_ms=as_of_ts_ms,
        side=side,
        horizon=ContinuationHorizon.FAST_INTRADAY,
        exit_variant=ContinuationExitVariant.BASELINE,
        actual_confirmation=True,
        confirmation_direction=(
            DCConfirmationDirection.UPTURN
            if is_long
            else DCConfirmationDirection.DOWNTURN
        ),
        confirmation_ts_ms=_CONFIRMATION_TS_MS,
        confirmation_price=D("100"),
        theta=D("0.01"),
        current_mid=D("100.70") if is_long else D("99.30"),
        overshoot_extreme=D("101") if is_long else D("99"),
        pullback_extreme=D("100.40") if is_long else D("99.60"),
        previous_event_durations_ms=(600_000,) * 20,
        trend_1h=(TrendDirection.TREND_UP if is_long else TrendDirection.TREND_DOWN),
        close_4h=D("101") if is_long else D("99"),
        ema50_4h=D("100"),
        integrity=IntegrityState.NORMAL,
        transition=TransitionState.STABLE,
        cost_coverage_passed=True,
        five_minute=_bar(side=side),
        taker_imbalance_5m=D("0.10") if is_long else D("-0.10"),
        cvd_slope=D("0"),
        maximum_risk_distance=D("2"),
    )


def _reversal(
    *,
    side: MonitorSide = MonitorSide.SHORT,
    sequence: int = 1,
    observation_id: str | None = None,
    as_of_ts_ms: int = _AS_OF_TS_MS,
) -> ReversalMonitorInput:
    is_short = side is MonitorSide.SHORT
    bar = (
        CompletedFiveMinuteBar(
            close_ts_ms=1_500_000,
            previous_close_ts_ms=1_200_000,
            close=D("100.80"),
            previous_high=D("102"),
            previous_low=D("101.00"),
        )
        if is_short
        else CompletedFiveMinuteBar(
            close_ts_ms=1_500_000,
            previous_close_ts_ms=1_200_000,
            close=D("99.20"),
            previous_high=D("99.00"),
            previous_low=D("98"),
        )
    )
    return ReversalMonitorInput(
        observation_id=observation_id or f"dc02-{side.value.lower()}-{sequence}",
        observation_sequence=sequence,
        as_of_ts_ms=as_of_ts_ms,
        side=side,
        actual_confirmation=True,
        confirmation_direction=(
            DCConfirmationDirection.UPTURN
            if is_short
            else DCConfirmationDirection.DOWNTURN
        ),
        confirmation_ts_ms=_CONFIRMATION_TS_MS,
        confirmation_price=D("100"),
        theta=D("0.01"),
        current_mid=D("101.40") if is_short else D("98.60"),
        overshoot_extreme=D("102") if is_short else D("98"),
        five_minute=bar,
        session_vwap=D("101") if is_short else D("99"),
        session_poc=D("100.50") if is_short else D("99.50"),
        trigger_taker_imbalance_5m=D("0"),
        price_progress_efficiency=D("0.20"),
        cvd_divergence_against_prior_trend=True,
        funding_z=D("1.50") if is_short else D("-1.50"),
        oi_change_z_before_turn=D("0"),
        oi_turns_against_prior_direction=False,
        exhaustion_taker_imbalance_5m=D("0"),
        new_extreme_made=True,
        reversal_side_refill_ratio=D("0"),
        integrity=IntegrityState.DEGRADED,
        strong_prior_trend=False,
        world_flow_strong_same_direction=False,
    )


def _reversal_without_exhaustion(
    *,
    side: MonitorSide = MonitorSide.SHORT,
) -> ReversalMonitorInput:
    return replace(
        _reversal(side=side),
        price_progress_efficiency=D("0.250001"),
        cvd_divergence_against_prior_trend=False,
        funding_z=D("0"),
        oi_change_z_before_turn=D("0"),
        oi_turns_against_prior_direction=False,
        exhaustion_taker_imbalance_5m=D("0"),
        new_extreme_made=True,
        reversal_side_refill_ratio=D("0.199999"),
        integrity=IntegrityState.DEGRADED,
    )


def _assert_monitoring_only(
    result: ContinuationMonitorResult | ReversalMonitorResult,
) -> None:
    assert result.monitoring_only is True
    assert result.entry_allowed is False
    assert result.active_allowed is False
    assert result.order_call_count == 0
    assert result.plan_created is False


def test_dc01_long_trigger_calculates_baseline_levels_without_order_plan() -> None:
    result = evaluate_dc01_continuation(_continuation())

    assert result.status is MonitorStatus.TRIGGERED
    assert result.reasons == (MonitorReason.TRIGGER_CONDITIONS_MET,)
    assert result.metrics is not None
    assert result.metrics.overshoot_ratio == D("1")
    assert result.metrics.pullback_fraction == D("0.30")
    assert result.metrics.reclaim_fraction == D("0.50")
    assert result.metrics.expiry_ms == D("1800000")
    assert result.reference_levels is not None
    assert result.reference_levels.stop == D("99.500")
    assert result.reference_levels.risk_distance == D("1.200")
    assert result.reference_levels.tp1_price == D("102.5000")
    assert result.reference_levels.tp2_price == D("104.3000")
    assert result.reference_levels.runner_enabled is False
    _assert_monitoring_only(result)


def test_dc01_short_is_symmetric_and_runner_trail_uses_short_formula() -> None:
    snapshot = replace(
        _continuation(side=MonitorSide.SHORT),
        exit_variant=ContinuationExitVariant.RUNNER,
        previous_trail=D("100.20"),
        last_completed_5m_swing_extreme=D("100"),
        atr5=D("0.50"),
    )

    result = evaluate_dc01_continuation(snapshot)

    assert result.status is MonitorStatus.TRIGGERED
    assert result.metrics is not None
    assert result.metrics.overshoot_ratio == D("1")
    assert result.metrics.pullback_fraction == D("0.30")
    assert result.metrics.reclaim_fraction == D("0.50")
    assert result.reference_levels is not None
    assert result.reference_levels.stop == D("100.500")
    assert result.reference_levels.risk_distance == D("1.200")
    assert result.reference_levels.tp1_price == D("97.5000")
    assert result.reference_levels.runner_activation_price == D("96.900")
    assert result.reference_levels.trail_price == D("99.742500")
    assert result.reference_levels.runner_enabled is True
    assert result.reference_levels.tp2_price is None
    _assert_monitoring_only(result)


@pytest.mark.parametrize(
    ("overshoot_extreme", "pullback_extreme", "current_mid"),
    [
        (D("100.50"), D("100.20"), D("100.35")),
        (D("102.00"), D("100.80"), D("101.40")),
    ],
)
def test_dc01_accepts_inclusive_overshoot_boundaries(
    overshoot_extreme: Decimal,
    pullback_extreme: Decimal,
    current_mid: Decimal,
) -> None:
    result = evaluate_dc01_continuation(
        replace(
            _continuation(),
            overshoot_extreme=overshoot_extreme,
            pullback_extreme=pullback_extreme,
            current_mid=current_mid,
            maximum_risk_distance=D("10"),
        )
    )

    assert result.status is MonitorStatus.TRIGGERED


@pytest.mark.parametrize(
    ("pullback_extreme", "current_mid", "expected_pullback"),
    [
        (D("100.60"), D("100.80"), D("0.20")),
        (D("100.00"), D("100.50"), D("0.50")),
    ],
)
def test_dc01_accepts_pullback_and_reclaim_inclusive_boundaries(
    pullback_extreme: Decimal,
    current_mid: Decimal,
    expected_pullback: Decimal,
) -> None:
    result = evaluate_dc01_continuation(
        replace(
            _continuation(),
            pullback_extreme=pullback_extreme,
            current_mid=current_mid,
        )
    )

    assert result.status is MonitorStatus.TRIGGERED
    assert result.metrics is not None
    assert result.metrics.pullback_fraction == expected_pullback
    assert result.metrics.reclaim_fraction == D("0.50")


def test_dc01_pending_trigger_stays_armed_and_cost_or_risk_is_vetoed() -> None:
    pending = evaluate_dc01_continuation(
        replace(_continuation(), taker_imbalance_5m=D("0.09"))
    )
    cost_veto = evaluate_dc01_continuation(
        replace(_continuation(), cost_coverage_passed=False)
    )
    risk_veto = evaluate_dc01_continuation(
        replace(_continuation(), maximum_risk_distance=D("1.19"))
    )

    assert pending.status is MonitorStatus.ARMED
    assert MonitorReason.TAKER_PENDING in pending.reasons
    assert cost_veto.status is MonitorStatus.VETO
    assert cost_veto.reasons == (MonitorReason.COST_COVERAGE_FAILED,)
    assert risk_veto.status is MonitorStatus.VETO
    assert risk_veto.reasons == (MonitorReason.RISK_DISTANCE_EXCEEDS_CONTRACT,)


@pytest.mark.parametrize("side", [MonitorSide.LONG, MonitorSide.SHORT])
def test_dc01_h_cap_is_strict_for_both_sides(side: MonitorSide) -> None:
    snapshot = _continuation(side=side)
    sign = D("1") if side is MonitorSide.LONG else D("-1")
    exact_cap = snapshot.overshoot_extreme * (
        D("1") + sign * D("0.10") * snapshot.theta
    )

    result = evaluate_dc01_continuation(replace(snapshot, current_mid=exact_cap))

    assert result.status is MonitorStatus.ARMED
    assert MonitorReason.EXTREME_LIMIT_FAILED in result.reasons


@pytest.mark.parametrize(
    ("side", "pullback_extreme", "current_mid"),
    [
        (MonitorSide.LONG, D("99.75"), D("100.50")),
        (MonitorSide.SHORT, D("100.25"), D("99.50")),
    ],
)
def test_dc01_historical_pullback_cannot_recover_after_price_invalidation(
    side: MonitorSide,
    pullback_extreme: Decimal,
    current_mid: Decimal,
) -> None:
    result = evaluate_dc01_continuation(
        replace(
            _continuation(side=side),
            pullback_extreme=pullback_extreme,
            current_mid=current_mid,
        )
    )

    assert result.status is MonitorStatus.INVALIDATED
    assert result.reasons == (MonitorReason.PRICE_INVALIDATED,)


def test_dc01_actual_downturn_trend_loss_and_actual_elapsed_expiry_invalidate() -> None:
    opposite = evaluate_dc01_continuation(
        replace(_continuation(), opposite_confirmation_ts_ms=1_550_000)
    )
    trend_loss = evaluate_dc01_continuation(
        replace(
            _continuation(),
            armed_ts_ms=1_300_000,
            trend_1h=TrendDirection.NEUTRAL,
        )
    )
    expiry = evaluate_dc01_continuation(
        replace(
            _continuation(as_of_ts_ms=2_800_000),
            five_minute=replace(_bar(), close_ts_ms=2_700_000),
        )
    )

    assert opposite.status is MonitorStatus.INVALIDATED
    assert opposite.reasons == (MonitorReason.OPPOSITE_ACTUAL_CONFIRMATION,)
    assert trend_loss.status is MonitorStatus.INVALIDATED
    assert trend_loss.reasons == (MonitorReason.TREND_PREREQUISITE_MISSING,)
    assert expiry.status is MonitorStatus.INVALIDATED
    assert expiry.reasons == (MonitorReason.SETUP_EXPIRED,)


@pytest.mark.parametrize(
    ("horizon", "expected_expiry_ms"),
    [
        (ContinuationHorizon.FAST_INTRADAY, 6 * 60 * 60 * 1_000),
        (ContinuationHorizon.INTRADAY_SWING, 24 * 60 * 60 * 1_000),
    ],
)
def test_dc01_expiry_uses_horizon_cap(
    horizon: ContinuationHorizon,
    expected_expiry_ms: int,
) -> None:
    result = evaluate_dc01_continuation(
        replace(
            _continuation(),
            horizon=horizon,
            previous_event_durations_ms=(10 * 60 * 60 * 1_000,) * 20,
        )
    )

    assert result.metrics is not None
    assert result.metrics.expiry_ms == D(expected_expiry_ms)


@pytest.mark.parametrize(
    "snapshot",
    [
        replace(_continuation(), actual_confirmation=False),
        replace(_continuation(), current_mid=D("NaN")),
        replace(_continuation(), previous_event_durations_ms=(600_000,) * 19),
        replace(_continuation(), five_minute=replace(_bar(), completed=False)),
        replace(_continuation(), five_minute=replace(_bar(), close_ts_ms=1_700_000)),
        replace(
            _continuation(),
            five_minute=replace(
                _bar(),
                close_ts_ms=_CONFIRMATION_TS_MS,
                previous_close_ts_ms=_CONFIRMATION_TS_MS - 300_000,
            ),
        ),
        replace(
            _continuation(),
            five_minute=replace(
                _bar(),
                previous_close_ts_ms=1_500_000,
            ),
        ),
    ],
)
def test_dc01_fail_closed_on_inferred_nonfinite_incomplete_future_or_ordered_data(
    snapshot: ContinuationMonitorInput,
) -> None:
    result = evaluate_dc01_continuation(snapshot)

    assert result.status is MonitorStatus.BLOCKED_DATA
    _assert_monitoring_only(result)


def test_dc02_short_triggers_with_exactly_three_exhaustion_conditions() -> None:
    result = evaluate_dc02_reversal(_reversal())

    assert result.status is MonitorStatus.TRIGGERED
    assert result.metrics is not None
    assert result.metrics.overshoot_ratio == D("2")
    assert result.metrics.exhaustion_count == 3
    assert result.metrics.exhaustion_conditions == (
        ExhaustionCondition.LOW_PRICE_PROGRESS_EFFICIENCY,
        ExhaustionCondition.CVD_DIVERGENCE,
        ExhaustionCondition.EXTREME_FUNDING,
    )
    assert result.reference_levels is not None
    assert result.reference_levels.stop == D("102.204")
    assert result.reference_levels.one_point_five_r_price == D("100.19400")
    assert result.reference_levels.first_target_price == D("100.50")
    assert result.reference_levels.first_target_source is ReversalTargetSource.SESSION_POC
    assert result.reference_levels.runner_enabled is False
    _assert_monitoring_only(result)


@pytest.mark.parametrize(
    ("condition", "snapshot"),
    [
        (
            ExhaustionCondition.LOW_PRICE_PROGRESS_EFFICIENCY,
            replace(
                _reversal_without_exhaustion(),
                price_progress_efficiency=D("0.25"),
            ),
        ),
        (
            ExhaustionCondition.CVD_DIVERGENCE,
            replace(
                _reversal_without_exhaustion(),
                cvd_divergence_against_prior_trend=True,
            ),
        ),
        (
            ExhaustionCondition.EXTREME_FUNDING,
            replace(_reversal_without_exhaustion(), funding_z=D("1.50")),
        ),
        (
            ExhaustionCondition.OI_EXTREME_THEN_TURN,
            replace(
                _reversal_without_exhaustion(),
                oi_change_z_before_turn=D("1.00"),
                oi_turns_against_prior_direction=True,
            ),
        ),
        (
            ExhaustionCondition.TAKER_WITHOUT_NEW_EXTREME,
            replace(
                _reversal_without_exhaustion(),
                exhaustion_taker_imbalance_5m=D("0.000001"),
                new_extreme_made=False,
            ),
        ),
        (
            ExhaustionCondition.REVERSAL_SIDE_REFILL,
            replace(
                _reversal_without_exhaustion(),
                reversal_side_refill_ratio=D("0.20"),
            ),
        ),
        (
            ExhaustionCondition.INTEGRITY_NORMAL,
            replace(
                _reversal_without_exhaustion(),
                integrity=IntegrityState.NORMAL,
            ),
        ),
    ],
)
def test_dc02_each_exhaustion_condition_uses_exact_inclusive_boundary(
    condition: ExhaustionCondition,
    snapshot: ReversalMonitorInput,
) -> None:
    result = evaluate_dc02_reversal(snapshot)

    assert result.metrics is not None
    assert result.metrics.exhaustion_conditions == (condition,)
    assert result.metrics.exhaustion_count == 1


def test_dc02_signed_exhaustion_inputs_are_mirrored_without_wrong_sign_credit() -> None:
    short_wrong_signs = evaluate_dc02_reversal(
        replace(
            _reversal_without_exhaustion(side=MonitorSide.SHORT),
            funding_z=D("-1.50"),
            oi_change_z_before_turn=D("-1.00"),
            oi_turns_against_prior_direction=True,
            exhaustion_taker_imbalance_5m=D("-0.10"),
            new_extreme_made=False,
        )
    )
    long_right_signs = evaluate_dc02_reversal(
        replace(
            _reversal_without_exhaustion(side=MonitorSide.LONG),
            funding_z=D("-1.50"),
            oi_change_z_before_turn=D("-1.00"),
            oi_turns_against_prior_direction=True,
            exhaustion_taker_imbalance_5m=D("-0.10"),
            new_extreme_made=False,
        )
    )
    long_wrong_signs = evaluate_dc02_reversal(
        replace(
            _reversal_without_exhaustion(side=MonitorSide.LONG),
            funding_z=D("1.50"),
            oi_change_z_before_turn=D("1.00"),
            oi_turns_against_prior_direction=True,
            exhaustion_taker_imbalance_5m=D("0.10"),
            new_extreme_made=False,
        )
    )

    assert short_wrong_signs.metrics is not None
    assert short_wrong_signs.metrics.exhaustion_count == 0
    assert long_right_signs.metrics is not None
    assert long_right_signs.metrics.exhaustion_conditions == (
        ExhaustionCondition.EXTREME_FUNDING,
        ExhaustionCondition.OI_EXTREME_THEN_TURN,
        ExhaustionCondition.TAKER_WITHOUT_NEW_EXTREME,
    )
    assert long_wrong_signs.metrics is not None
    assert long_wrong_signs.metrics.exhaustion_count == 0


def test_dc02_long_is_directionally_symmetric() -> None:
    short = evaluate_dc02_reversal(_reversal(side=MonitorSide.SHORT))
    long = evaluate_dc02_reversal(_reversal(side=MonitorSide.LONG))

    assert short.status is long.status is MonitorStatus.TRIGGERED
    assert short.metrics is not None and long.metrics is not None
    assert short.metrics.overshoot_ratio == long.metrics.overshoot_ratio == D("2")
    assert short.metrics.exhaustion_count == long.metrics.exhaustion_count == 3
    assert short.reference_levels is not None and long.reference_levels is not None
    assert short.reference_levels.stop > short.reference_levels.reference_mid
    assert long.reference_levels.stop < long.reference_levels.reference_mid
    assert short.reference_levels.first_target_price < short.reference_levels.reference_mid
    assert long.reference_levels.first_target_price > long.reference_levels.reference_mid
    assert short.reference_levels.runner_enabled is False
    assert long.reference_levels.runner_enabled is False


def test_dc02_exact_retracement_boundary_is_inclusive() -> None:
    extreme = D("102")
    boundary_mid = extreme * (D("1") - D("0.50") * D("0.01"))
    result = evaluate_dc02_reversal(
        replace(_reversal(), current_mid=boundary_mid)
    )

    assert result.status is MonitorStatus.TRIGGERED
    assert result.metrics is not None
    assert result.metrics.actual_retracement_fraction == D("0.005")


def test_dc02_requires_three_exhaustion_conditions_and_all_trigger_structure() -> None:
    two_conditions = evaluate_dc02_reversal(
        replace(_reversal(), funding_z=D("0"))
    )
    no_vwap_break = evaluate_dc02_reversal(
        replace(
            _reversal(),
            five_minute=replace(_reversal().five_minute, close=D("101.10")),
        )
    )

    assert two_conditions.status is MonitorStatus.ARMED
    assert MonitorReason.EXHAUSTION_PENDING in two_conditions.reasons
    assert no_vwap_break.status is MonitorStatus.ARMED
    assert MonitorReason.VWAP_STRUCTURE_PENDING in no_vwap_break.reasons


def test_dc02_strong_trend_and_world_flow_each_veto_reversal() -> None:
    result = evaluate_dc02_reversal(
        replace(
            _reversal(),
            strong_prior_trend=True,
            world_flow_strong_same_direction=True,
        )
    )

    assert result.status is MonitorStatus.VETO
    assert result.reasons == (
        MonitorReason.STRONG_TREND_VETO,
        MonitorReason.WORLD_FLOW_VETO,
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        replace(_reversal(), actual_confirmation=False),
        replace(_reversal(), funding_z=D("Infinity")),
        replace(
            _reversal(),
            five_minute=replace(_reversal().five_minute, completed=False),
        ),
        replace(
            _reversal(),
            five_minute=replace(_reversal().five_minute, close_ts_ms=1_700_000),
        ),
    ],
)
def test_dc02_fail_closed_on_inferred_nonfinite_incomplete_or_future_data(
    snapshot: ReversalMonitorInput,
) -> None:
    result = evaluate_dc02_reversal(snapshot)

    assert result.status is MonitorStatus.BLOCKED_DATA
    _assert_monitoring_only(result)


def test_replays_are_deterministic_and_fail_closed_on_duplicate_or_reverse_order() -> None:
    dc01_frames = (
        _continuation(sequence=1, as_of_ts_ms=1_600_000),
        _continuation(sequence=2, as_of_ts_ms=1_700_000),
    )
    dc02_frames = (
        _reversal(sequence=1, as_of_ts_ms=1_600_000),
        _reversal(sequence=2, as_of_ts_ms=1_700_000),
    )

    assert evaluate_dc01_replay(dc01_frames) == evaluate_dc01_replay(dc01_frames)
    assert evaluate_dc02_replay(dc02_frames) == evaluate_dc02_replay(dc02_frames)

    duplicate = replace(
        dc01_frames[1],
        observation_id=dc01_frames[0].observation_id,
    )
    reversed_sequence = replace(
        dc02_frames[1],
        observation_sequence=1,
    )
    duplicate_results = evaluate_dc01_replay((dc01_frames[0], duplicate))
    reversed_results = evaluate_dc02_replay((dc02_frames[0], reversed_sequence))

    assert duplicate_results[1].status is MonitorStatus.BLOCKED_DATA
    assert duplicate_results[1].reasons == (MonitorReason.DUPLICATE_OBSERVATION,)
    assert reversed_results[1].status is MonitorStatus.BLOCKED_DATA
    assert reversed_results[1].reasons == (MonitorReason.REPLAY_ORDER_INVALID,)


def test_dc01_replay_carries_arm_and_latches_trend_loss_without_caller_hint() -> None:
    armed = replace(
        _continuation(sequence=1, as_of_ts_ms=1_600_000),
        taker_imbalance_5m=D("0.09"),
    )
    trend_lost = replace(
        _continuation(sequence=2, as_of_ts_ms=1_700_000),
        trend_1h=TrendDirection.NEUTRAL,
        armed_ts_ms=None,
    )
    apparently_recovered = _continuation(sequence=3, as_of_ts_ms=1_800_000)

    results = evaluate_dc01_replay((armed, trend_lost, apparently_recovered))

    assert results[0].status is MonitorStatus.ARMED
    assert results[1].status is MonitorStatus.INVALIDATED
    assert results[1].reasons == (MonitorReason.TREND_PREREQUISITE_MISSING,)
    assert results[2].status is MonitorStatus.INVALIDATED
    assert results[2].reasons == (MonitorReason.SETUP_ALREADY_INVALIDATED,)


def test_replay_requires_contiguous_sequence_and_latches_broken_continuity() -> None:
    first = _continuation(sequence=1, as_of_ts_ms=1_600_000)
    gap = _continuation(sequence=3, as_of_ts_ms=1_700_000)
    after_gap = _continuation(sequence=4, as_of_ts_ms=1_800_000)

    results = evaluate_dc01_replay((first, gap, after_gap))

    assert results[0].status is MonitorStatus.TRIGGERED
    assert results[1].status is MonitorStatus.BLOCKED_DATA
    assert results[1].reasons == (MonitorReason.REPLAY_ORDER_INVALID,)
    assert results[2].status is MonitorStatus.BLOCKED_DATA
    assert results[2].reasons == (MonitorReason.REPLAY_CONTINUITY_BROKEN,)


def test_replay_rejects_mixed_confirmation_context_and_inferred_confirmation() -> None:
    actual = _reversal(sequence=1, as_of_ts_ms=1_600_000)
    inferred = replace(
        _reversal(sequence=2, as_of_ts_ms=1_700_000),
        actual_confirmation=False,
    )
    mixed_context = replace(
        _reversal(sequence=3, as_of_ts_ms=1_800_000),
        confirmation_price=D("100.01"),
    )

    results = evaluate_dc02_replay((actual, inferred, mixed_context))

    assert results[0].status is MonitorStatus.TRIGGERED
    assert results[1].status is MonitorStatus.BLOCKED_DATA
    assert results[1].reasons == (MonitorReason.ACTUAL_CONFIRMATION_REQUIRED,)
    assert results[2].status is MonitorStatus.BLOCKED_DATA
    assert results[2].reasons == (MonitorReason.RETROACTIVE_ACTUAL_CONFIRMATION,)

    context_results = evaluate_dc02_replay(
        (
            actual,
            replace(
                _reversal(sequence=2, as_of_ts_ms=1_700_000),
                confirmation_price=D("100.01"),
            ),
        )
    )
    assert context_results[1].status is MonitorStatus.BLOCKED_DATA
    assert context_results[1].reasons == (MonitorReason.REPLAY_ORDER_INVALID,)


def test_inferred_frame_cannot_be_retroactively_promoted_to_past_actual_time() -> None:
    inferred = replace(
        _continuation(sequence=1, as_of_ts_ms=1_600_000),
        actual_confirmation=False,
    )
    retroactive_actual = _continuation(sequence=2, as_of_ts_ms=1_700_000)

    results = evaluate_dc01_replay((inferred, retroactive_actual))

    assert results[0].status is MonitorStatus.BLOCKED_DATA
    assert results[0].reasons == (MonitorReason.ACTUAL_CONFIRMATION_REQUIRED,)
    assert results[1].status is MonitorStatus.BLOCKED_DATA
    assert results[1].reasons == (MonitorReason.RETROACTIVE_ACTUAL_CONFIRMATION,)
