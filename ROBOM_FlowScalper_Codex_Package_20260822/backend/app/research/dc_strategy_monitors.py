# V9 DC01·DC02 후보를 주문 없이 실제 관측자료만으로 판정한다.
"""Directional Change 전략의 순수 monitoring-only 판정기를 제공한다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from typing import TypeGuard

DC_CONTINUATION_STRATEGY_ID = "DC_OVERSHOOT_CONTINUATION_V1"
DC_REVERSAL_STRATEGY_ID = "DC_OVERSHOOT_EXHAUSTION_REVERSAL_V1"

_ZERO = Decimal(0)
_ONE = Decimal(1)
_EPSILON = Decimal("1e-18")
_CONTINUATION_MIN_OVERSHOOT = Decimal("0.50")
_CONTINUATION_MAX_OVERSHOOT = Decimal("2.00")
_MIN_PULLBACK = Decimal("0.20")
_MAX_PULLBACK = Decimal("0.50")
_MIN_RECLAIM = Decimal("0.50")
_CONTINUATION_TAKER_THRESHOLD = Decimal("0.10")
_REVERSAL_MIN_OVERSHOOT = Decimal("2.00")
_MIN_RETRACEMENT_THETA_MULTIPLIER = Decimal("0.50")
_TP1_R = Decimal("1.50")
_TP2_R = Decimal("3.00")
_RUNNER_ACTIVATION_R = Decimal("2.00")
_TP1_FRACTION = Decimal("0.25")
_REMAINDER_FRACTION = Decimal("0.75")
_FAST_EXPIRY_CAP_MS = 6 * 60 * 60 * 1_000
_SWING_EXPIRY_CAP_MS = 24 * 60 * 60 * 1_000


class MonitorStatus(StrEnum):
    WAIT = "WAIT"
    ARMED = "ARMED"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"
    VETO = "VETO"
    BLOCKED_DATA = "BLOCKED_DATA"


class MonitorSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class DCConfirmationDirection(StrEnum):
    UPTURN = "UPTURN"
    DOWNTURN = "DOWNTURN"


class TrendDirection(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    NEUTRAL = "NEUTRAL"


class IntegrityState(StrEnum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"


class TransitionState(StrEnum):
    STABLE = "STABLE"
    UNSTABLE = "UNSTABLE"


class ContinuationHorizon(StrEnum):
    FAST_INTRADAY = "FAST_INTRADAY"
    INTRADAY_SWING = "INTRADAY_SWING"


class ContinuationExitVariant(StrEnum):
    BASELINE = "BASELINE"
    RUNNER = "RUNNER"


class ExhaustionCondition(StrEnum):
    LOW_PRICE_PROGRESS_EFFICIENCY = "LOW_PRICE_PROGRESS_EFFICIENCY"
    CVD_DIVERGENCE = "CVD_DIVERGENCE"
    EXTREME_FUNDING = "EXTREME_FUNDING"
    OI_EXTREME_THEN_TURN = "OI_EXTREME_THEN_TURN"
    TAKER_WITHOUT_NEW_EXTREME = "TAKER_WITHOUT_NEW_EXTREME"
    REVERSAL_SIDE_REFILL = "REVERSAL_SIDE_REFILL"
    INTEGRITY_NORMAL = "INTEGRITY_NORMAL"


class ReversalTargetSource(StrEnum):
    CONFIRMATION_PRICE = "CONFIRMATION_PRICE"
    SESSION_POC = "SESSION_POC"
    ONE_POINT_FIVE_R = "ONE_POINT_FIVE_R"


class MonitorReason(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    NONFINITE_INPUT = "NONFINITE_INPUT"
    INVALID_PRICE = "INVALID_PRICE"
    ACTUAL_CONFIRMATION_REQUIRED = "ACTUAL_CONFIRMATION_REQUIRED"
    RETROACTIVE_ACTUAL_CONFIRMATION = "RETROACTIVE_ACTUAL_CONFIRMATION"
    FUTURE_DATA = "FUTURE_DATA"
    TEMPORAL_ORDER_INVALID = "TEMPORAL_ORDER_INVALID"
    FIVE_MINUTE_INCOMPLETE = "FIVE_MINUTE_INCOMPLETE"
    FIVE_MINUTE_ORDER_INVALID = "FIVE_MINUTE_ORDER_INVALID"
    FIVE_MINUTE_NOT_POST_CONFIRMATION = "FIVE_MINUTE_NOT_POST_CONFIRMATION"
    EVENT_DURATION_HISTORY_INVALID = "EVENT_DURATION_HISTORY_INVALID"
    PRICE_GEOMETRY_INVALID = "PRICE_GEOMETRY_INVALID"
    DUPLICATE_OBSERVATION = "DUPLICATE_OBSERVATION"
    REPLAY_ORDER_INVALID = "REPLAY_ORDER_INVALID"
    REPLAY_CONTINUITY_BROKEN = "REPLAY_CONTINUITY_BROKEN"
    CONFIRMATION_DIRECTION_MISMATCH = "CONFIRMATION_DIRECTION_MISMATCH"
    INTEGRITY_NOT_NORMAL = "INTEGRITY_NOT_NORMAL"
    TRANSITION_NOT_STABLE = "TRANSITION_NOT_STABLE"
    COST_COVERAGE_FAILED = "COST_COVERAGE_FAILED"
    TREND_PREREQUISITE_MISSING = "TREND_PREREQUISITE_MISSING"
    FOUR_HOUR_TREND_FILTER_FAILED = "FOUR_HOUR_TREND_FILTER_FAILED"
    OPPOSITE_ACTUAL_CONFIRMATION = "OPPOSITE_ACTUAL_CONFIRMATION"
    SETUP_EXPIRED = "SETUP_EXPIRED"
    PRICE_INVALIDATED = "PRICE_INVALIDATED"
    SETUP_ALREADY_INVALIDATED = "SETUP_ALREADY_INVALIDATED"
    RISK_DISTANCE_EXCEEDS_CONTRACT = "RISK_DISTANCE_EXCEEDS_CONTRACT"
    OVERSHOOT_NOT_ARMED = "OVERSHOOT_NOT_ARMED"
    PULLBACK_OUTSIDE_RANGE = "PULLBACK_OUTSIDE_RANGE"
    RECLAIM_PENDING = "RECLAIM_PENDING"
    EXTREME_LIMIT_FAILED = "EXTREME_LIMIT_FAILED"
    BREAKOUT_PENDING = "BREAKOUT_PENDING"
    TAKER_PENDING = "TAKER_PENDING"
    CVD_PENDING = "CVD_PENDING"
    EXHAUSTION_PENDING = "EXHAUSTION_PENDING"
    RETRACEMENT_PENDING = "RETRACEMENT_PENDING"
    VWAP_STRUCTURE_PENDING = "VWAP_STRUCTURE_PENDING"
    FIVE_MINUTE_STRUCTURE_PENDING = "FIVE_MINUTE_STRUCTURE_PENDING"
    STRONG_TREND_VETO = "STRONG_TREND_VETO"
    WORLD_FLOW_VETO = "WORLD_FLOW_VETO"
    TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"
    TRIGGER_CONDITIONS_MET = "TRIGGER_CONDITIONS_MET"


@dataclass(frozen=True, slots=True)
class CompletedFiveMinuteBar:
    """현재 시점에 확정된 5분봉과 직전 확정봉 경계를 담는다."""

    close_ts_ms: int
    previous_close_ts_ms: int
    close: Decimal
    previous_high: Decimal
    previous_low: Decimal
    completed: bool = True


@dataclass(frozen=True, slots=True)
class ContinuationMonitorInput:
    observation_id: str
    observation_sequence: int
    as_of_ts_ms: int
    side: MonitorSide
    horizon: ContinuationHorizon
    exit_variant: ContinuationExitVariant
    actual_confirmation: bool
    confirmation_direction: DCConfirmationDirection
    confirmation_ts_ms: int
    confirmation_price: Decimal
    theta: Decimal
    current_mid: Decimal
    overshoot_extreme: Decimal
    pullback_extreme: Decimal
    previous_event_durations_ms: tuple[int, ...]
    trend_1h: TrendDirection
    close_4h: Decimal
    ema50_4h: Decimal
    integrity: IntegrityState
    transition: TransitionState
    cost_coverage_passed: bool
    five_minute: CompletedFiveMinuteBar
    taker_imbalance_5m: Decimal
    cvd_slope: Decimal
    maximum_risk_distance: Decimal
    armed_ts_ms: int | None = None
    opposite_confirmation_ts_ms: int | None = None
    previous_trail: Decimal | None = None
    last_completed_5m_swing_extreme: Decimal | None = None
    atr5: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ReversalMonitorInput:
    observation_id: str
    observation_sequence: int
    as_of_ts_ms: int
    side: MonitorSide
    actual_confirmation: bool
    confirmation_direction: DCConfirmationDirection
    confirmation_ts_ms: int
    confirmation_price: Decimal
    theta: Decimal
    current_mid: Decimal
    overshoot_extreme: Decimal
    five_minute: CompletedFiveMinuteBar
    session_vwap: Decimal
    session_poc: Decimal
    trigger_taker_imbalance_5m: Decimal
    price_progress_efficiency: Decimal
    cvd_divergence_against_prior_trend: bool
    funding_z: Decimal
    oi_change_z_before_turn: Decimal
    oi_turns_against_prior_direction: bool
    exhaustion_taker_imbalance_5m: Decimal
    new_extreme_made: bool
    reversal_side_refill_ratio: Decimal
    integrity: IntegrityState
    strong_prior_trend: bool
    world_flow_strong_same_direction: bool


@dataclass(frozen=True, slots=True)
class ContinuationMetrics:
    overshoot_ratio: Decimal
    pullback_fraction: Decimal
    reclaim_fraction: Decimal
    median_previous_20_event_duration_ms: Decimal
    expiry_ms: Decimal
    actual_elapsed_ms: int


@dataclass(frozen=True, slots=True)
class ReversalMetrics:
    overshoot_ratio: Decimal
    actual_retracement_fraction: Decimal
    exhaustion_conditions: tuple[ExhaustionCondition, ...]
    exhaustion_count: int


@dataclass(frozen=True, slots=True)
class ContinuationReferenceLevels:
    """실제 주문계획이 아닌 현재 mid 기준 monitoring 참고 수준이다."""

    reference_mid: Decimal
    stop: Decimal
    risk_distance: Decimal
    tp1_price: Decimal
    tp1_fraction: Decimal
    tp2_price: Decimal | None
    tp2_fraction: Decimal | None
    runner_enabled: bool
    runner_fraction: Decimal | None
    runner_activation_price: Decimal | None
    trail_price: Decimal | None


@dataclass(frozen=True, slots=True)
class ReversalReferenceLevels:
    """실제 주문계획이 아닌 현재 mid 기준 monitoring 참고 수준이다."""

    reference_mid: Decimal
    stop: Decimal
    risk_distance: Decimal
    one_point_five_r_price: Decimal
    first_target_price: Decimal
    first_target_source: ReversalTargetSource
    runner_enabled: bool = False


@dataclass(frozen=True, slots=True)
class ContinuationMonitorResult:
    strategy_id: str
    observation_id: str
    side: MonitorSide | None
    status: MonitorStatus
    reasons: tuple[MonitorReason, ...]
    metrics: ContinuationMetrics | None = None
    reference_levels: ContinuationReferenceLevels | None = None
    monitoring_only: bool = field(default=True, init=False)
    entry_allowed: bool = field(default=False, init=False)
    active_allowed: bool = field(default=False, init=False)
    order_call_count: int = field(default=0, init=False)
    plan_created: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class ReversalMonitorResult:
    strategy_id: str
    observation_id: str
    side: MonitorSide | None
    status: MonitorStatus
    reasons: tuple[MonitorReason, ...]
    metrics: ReversalMetrics | None = None
    reference_levels: ReversalReferenceLevels | None = None
    monitoring_only: bool = field(default=True, init=False)
    entry_allowed: bool = field(default=False, init=False)
    active_allowed: bool = field(default=False, init=False)
    order_call_count: int = field(default=0, init=False)
    plan_created: bool = field(default=False, init=False)


def _side_or_none(value: object) -> MonitorSide | None:
    return value if isinstance(value, MonitorSide) else None


def _continuation_result(
    snapshot: ContinuationMonitorInput,
    status: MonitorStatus,
    *reasons: MonitorReason,
    metrics: ContinuationMetrics | None = None,
    levels: ContinuationReferenceLevels | None = None,
) -> ContinuationMonitorResult:
    return ContinuationMonitorResult(
        strategy_id=DC_CONTINUATION_STRATEGY_ID,
        observation_id=snapshot.observation_id,
        side=_side_or_none(snapshot.side),
        status=status,
        reasons=tuple(reasons),
        metrics=metrics,
        reference_levels=levels,
    )


def _reversal_result(
    snapshot: ReversalMonitorInput,
    status: MonitorStatus,
    *reasons: MonitorReason,
    metrics: ReversalMetrics | None = None,
    levels: ReversalReferenceLevels | None = None,
) -> ReversalMonitorResult:
    return ReversalMonitorResult(
        strategy_id=DC_REVERSAL_STRATEGY_ID,
        observation_id=snapshot.observation_id,
        side=_side_or_none(snapshot.side),
        status=status,
        reasons=tuple(reasons),
        metrics=metrics,
        reference_levels=levels,
    )


def _valid_int(value: object, *, minimum: int = 0) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _valid_bool(value: object) -> bool:
    return isinstance(value, bool)


def _valid_decimal(value: object, *, positive: bool = False) -> bool:
    if not isinstance(value, Decimal) or not value.is_finite():
        return False
    return not positive or value > _ZERO


def _validate_five_minute_bar(
    bar: object,
    *,
    as_of_ts_ms: int,
) -> MonitorReason | None:
    if not isinstance(bar, CompletedFiveMinuteBar):
        return MonitorReason.INVALID_INPUT
    if not _valid_bool(bar.completed):
        return MonitorReason.INVALID_INPUT
    if not bar.completed:
        return MonitorReason.FIVE_MINUTE_INCOMPLETE
    if not _valid_int(bar.close_ts_ms) or not _valid_int(bar.previous_close_ts_ms):
        return MonitorReason.TEMPORAL_ORDER_INVALID
    if bar.close_ts_ms > as_of_ts_ms:
        return MonitorReason.FUTURE_DATA
    if bar.previous_close_ts_ms >= bar.close_ts_ms:
        return MonitorReason.FIVE_MINUTE_ORDER_INVALID
    prices = (bar.close, bar.previous_high, bar.previous_low)
    if any(not _valid_decimal(value) for value in prices):
        return MonitorReason.NONFINITE_INPUT
    if any(value <= _ZERO for value in prices):
        return MonitorReason.INVALID_PRICE
    if bar.previous_low > bar.previous_high:
        return MonitorReason.PRICE_GEOMETRY_INVALID
    return None


def _validate_common(
    *,
    observation_id: object,
    observation_sequence: object,
    as_of_ts_ms: object,
    confirmation_ts_ms: object,
    actual_confirmation: object,
    side: object,
    confirmation_direction: object,
    five_minute: object,
) -> MonitorReason | None:
    if not isinstance(observation_id, str) or not observation_id.strip():
        return MonitorReason.INVALID_INPUT
    if not _valid_int(observation_sequence) or not _valid_int(as_of_ts_ms):
        return MonitorReason.TEMPORAL_ORDER_INVALID
    if not _valid_int(confirmation_ts_ms):
        return MonitorReason.TEMPORAL_ORDER_INVALID
    if confirmation_ts_ms > as_of_ts_ms:
        return MonitorReason.FUTURE_DATA
    if not _valid_bool(actual_confirmation):
        return MonitorReason.INVALID_INPUT
    if not isinstance(side, MonitorSide) or not isinstance(
        confirmation_direction, DCConfirmationDirection
    ):
        return MonitorReason.INVALID_INPUT
    bar_reason = _validate_five_minute_bar(five_minute, as_of_ts_ms=as_of_ts_ms)
    if bar_reason is not None:
        return bar_reason
    if not isinstance(five_minute, CompletedFiveMinuteBar):
        return MonitorReason.INVALID_INPUT
    if five_minute.close_ts_ms <= confirmation_ts_ms:
        return MonitorReason.FIVE_MINUTE_NOT_POST_CONFIRMATION
    if not actual_confirmation:
        return MonitorReason.ACTUAL_CONFIRMATION_REQUIRED
    return None


def _expected_confirmation(side: MonitorSide) -> DCConfirmationDirection:
    if side is MonitorSide.LONG:
        return DCConfirmationDirection.UPTURN
    return DCConfirmationDirection.DOWNTURN


def _side_sign(side: MonitorSide) -> Decimal:
    return _ONE if side is MonitorSide.LONG else -_ONE


def _expected_trend(side: MonitorSide) -> TrendDirection:
    if side is MonitorSide.LONG:
        return TrendDirection.TREND_UP
    return TrendDirection.TREND_DOWN


def _median_previous_20(values: tuple[int, ...]) -> Decimal | None:
    if len(values) != 20 or any(not _valid_int(value, minimum=1) for value in values):
        return None
    ordered = sorted(values)
    return (Decimal(ordered[9]) + Decimal(ordered[10])) / Decimal(2)


def _continuation_metrics(
    snapshot: ContinuationMonitorInput,
) -> ContinuationMetrics | None:
    sign = _side_sign(snapshot.side)
    directional_overshoot = sign * (
        snapshot.overshoot_extreme - snapshot.confirmation_price
    )
    directional_pullback_range = sign * (
        snapshot.overshoot_extreme - snapshot.pullback_extreme
    )
    directional_reclaim = sign * (snapshot.current_mid - snapshot.pullback_extreme)
    median_duration = _median_previous_20(snapshot.previous_event_durations_ms)
    if (
        directional_overshoot <= _ZERO
        or directional_pullback_range <= _ZERO
        or directional_reclaim < _ZERO
        or median_duration is None
    ):
        return None
    overshoot_ratio = directional_overshoot / (
        snapshot.confirmation_price * snapshot.theta
    )
    pullback_fraction = (
        sign * (snapshot.overshoot_extreme - snapshot.current_mid)
    ) / max(directional_overshoot, _EPSILON)
    reclaim_fraction = directional_reclaim / max(
        directional_pullback_range, _EPSILON
    )
    cap_ms = (
        _FAST_EXPIRY_CAP_MS
        if snapshot.horizon is ContinuationHorizon.FAST_INTRADAY
        else _SWING_EXPIRY_CAP_MS
    )
    expiry_ms = min(Decimal(3) * median_duration, Decimal(cap_ms))
    return ContinuationMetrics(
        overshoot_ratio=overshoot_ratio,
        pullback_fraction=pullback_fraction,
        reclaim_fraction=reclaim_fraction,
        median_previous_20_event_duration_ms=median_duration,
        expiry_ms=expiry_ms,
        actual_elapsed_ms=snapshot.as_of_ts_ms - snapshot.confirmation_ts_ms,
    )


def _continuation_levels(
    snapshot: ContinuationMonitorInput,
) -> ContinuationReferenceLevels | None:
    sign = _side_sign(snapshot.side)
    if snapshot.side is MonitorSide.LONG:
        stop = min(
            snapshot.pullback_extreme * (_ONE - Decimal("0.20") * snapshot.theta),
            snapshot.confirmation_price * (_ONE - Decimal("0.50") * snapshot.theta),
        )
    else:
        stop = max(
            snapshot.pullback_extreme * (_ONE + Decimal("0.20") * snapshot.theta),
            snapshot.confirmation_price * (_ONE + Decimal("0.50") * snapshot.theta),
        )
    signed_risk_distance = sign * (snapshot.current_mid - stop)
    if signed_risk_distance <= _ZERO:
        return None
    tp1 = snapshot.current_mid + sign * _TP1_R * signed_risk_distance
    if snapshot.exit_variant is ContinuationExitVariant.BASELINE:
        return ContinuationReferenceLevels(
            reference_mid=snapshot.current_mid,
            stop=stop,
            risk_distance=signed_risk_distance,
            tp1_price=tp1,
            tp1_fraction=_TP1_FRACTION,
            tp2_price=snapshot.current_mid
            + sign * _TP2_R * signed_risk_distance,
            tp2_fraction=_REMAINDER_FRACTION,
            runner_enabled=False,
            runner_fraction=None,
            runner_activation_price=None,
            trail_price=None,
        )
    if (
        snapshot.previous_trail is None
        or snapshot.last_completed_5m_swing_extreme is None
        or snapshot.atr5 is None
    ):
        return None
    if snapshot.side is MonitorSide.LONG:
        trail = max(
            snapshot.previous_trail,
            snapshot.overshoot_extreme * (_ONE - Decimal("0.75") * snapshot.theta),
            snapshot.last_completed_5m_swing_extreme
            - Decimal("0.10") * snapshot.atr5,
        )
    else:
        trail = min(
            snapshot.previous_trail,
            snapshot.overshoot_extreme * (_ONE + Decimal("0.75") * snapshot.theta),
            snapshot.last_completed_5m_swing_extreme
            + Decimal("0.10") * snapshot.atr5,
        )
    return ContinuationReferenceLevels(
        reference_mid=snapshot.current_mid,
        stop=stop,
        risk_distance=signed_risk_distance,
        tp1_price=tp1,
        tp1_fraction=_TP1_FRACTION,
        tp2_price=None,
        tp2_fraction=None,
        runner_enabled=True,
        runner_fraction=_REMAINDER_FRACTION,
        runner_activation_price=snapshot.current_mid
        + sign * _RUNNER_ACTIVATION_R * signed_risk_distance,
        trail_price=trail,
    )


def _validate_continuation(
    snapshot: ContinuationMonitorInput,
) -> MonitorReason | None:
    common = _validate_common(
        observation_id=snapshot.observation_id,
        observation_sequence=snapshot.observation_sequence,
        as_of_ts_ms=snapshot.as_of_ts_ms,
        confirmation_ts_ms=snapshot.confirmation_ts_ms,
        actual_confirmation=snapshot.actual_confirmation,
        side=snapshot.side,
        confirmation_direction=snapshot.confirmation_direction,
        five_minute=snapshot.five_minute,
    )
    if common is not None:
        return common
    if not isinstance(snapshot.horizon, ContinuationHorizon) or not isinstance(
        snapshot.exit_variant, ContinuationExitVariant
    ):
        return MonitorReason.INVALID_INPUT
    if not isinstance(snapshot.trend_1h, TrendDirection) or not isinstance(
        snapshot.integrity, IntegrityState
    ) or not isinstance(snapshot.transition, TransitionState):
        return MonitorReason.INVALID_INPUT
    boolean_values = (snapshot.cost_coverage_passed,)
    if any(not _valid_bool(value) for value in boolean_values):
        return MonitorReason.INVALID_INPUT
    decimal_values = (
        snapshot.confirmation_price,
        snapshot.theta,
        snapshot.current_mid,
        snapshot.overshoot_extreme,
        snapshot.pullback_extreme,
        snapshot.close_4h,
        snapshot.ema50_4h,
        snapshot.taker_imbalance_5m,
        snapshot.cvd_slope,
        snapshot.maximum_risk_distance,
    )
    optional_decimals = (
        snapshot.previous_trail,
        snapshot.last_completed_5m_swing_extreme,
        snapshot.atr5,
    )
    if any(not _valid_decimal(value) for value in decimal_values) or any(
        value is not None and not _valid_decimal(value) for value in optional_decimals
    ):
        return MonitorReason.NONFINITE_INPUT
    positive_prices = (
        snapshot.confirmation_price,
        snapshot.current_mid,
        snapshot.overshoot_extreme,
        snapshot.pullback_extreme,
        snapshot.close_4h,
        snapshot.ema50_4h,
        snapshot.maximum_risk_distance,
    )
    if any(value <= _ZERO for value in positive_prices):
        return MonitorReason.INVALID_PRICE
    if not _ZERO < snapshot.theta < _ONE:
        return MonitorReason.INVALID_INPUT
    if snapshot.atr5 is not None and snapshot.atr5 < _ZERO:
        return MonitorReason.INVALID_INPUT
    if snapshot.previous_trail is not None and snapshot.previous_trail <= _ZERO:
        return MonitorReason.INVALID_PRICE
    if (
        snapshot.last_completed_5m_swing_extreme is not None
        and snapshot.last_completed_5m_swing_extreme <= _ZERO
    ):
        return MonitorReason.INVALID_PRICE
    if (
        snapshot.exit_variant is ContinuationExitVariant.RUNNER
        and snapshot.atr5 is not None
        and snapshot.atr5 <= _ZERO
    ):
        return MonitorReason.INVALID_INPUT
    if _median_previous_20(snapshot.previous_event_durations_ms) is None:
        return MonitorReason.EVENT_DURATION_HISTORY_INVALID
    if snapshot.armed_ts_ms is not None:
        if not _valid_int(snapshot.armed_ts_ms):
            return MonitorReason.TEMPORAL_ORDER_INVALID
        if not snapshot.confirmation_ts_ms <= snapshot.armed_ts_ms <= snapshot.as_of_ts_ms:
            return MonitorReason.TEMPORAL_ORDER_INVALID
    if snapshot.opposite_confirmation_ts_ms is not None:
        if not _valid_int(snapshot.opposite_confirmation_ts_ms):
            return MonitorReason.TEMPORAL_ORDER_INVALID
        if not (
            snapshot.confirmation_ts_ms
            < snapshot.opposite_confirmation_ts_ms
            <= snapshot.as_of_ts_ms
        ):
            return MonitorReason.TEMPORAL_ORDER_INVALID
    if (
        snapshot.exit_variant is ContinuationExitVariant.RUNNER
        and any(value is None for value in optional_decimals)
    ):
        return MonitorReason.INVALID_INPUT
    return None


def evaluate_dc01_continuation(
    snapshot: ContinuationMonitorInput,
) -> ContinuationMonitorResult:
    """DC01을 actual-as-of 자료로만 평가하고 주문 생성 권한은 반환하지 않는다."""

    if not isinstance(snapshot, ContinuationMonitorInput):
        raise TypeError("ContinuationMonitorInput이 필요합니다.")
    invalid = _validate_continuation(snapshot)
    if invalid is not None:
        return _continuation_result(snapshot, MonitorStatus.BLOCKED_DATA, invalid)
    if snapshot.confirmation_direction is not _expected_confirmation(snapshot.side):
        return _continuation_result(
            snapshot,
            MonitorStatus.WAIT,
            MonitorReason.CONFIRMATION_DIRECTION_MISMATCH,
        )
    metrics = _continuation_metrics(snapshot)
    levels = _continuation_levels(snapshot)
    if metrics is None or levels is None:
        return _continuation_result(
            snapshot,
            MonitorStatus.BLOCKED_DATA,
            MonitorReason.PRICE_GEOMETRY_INVALID,
        )
    if snapshot.opposite_confirmation_ts_ms is not None:
        return _continuation_result(
            snapshot,
            MonitorStatus.INVALIDATED,
            MonitorReason.OPPOSITE_ACTUAL_CONFIRMATION,
            metrics=metrics,
            levels=levels,
        )
    if Decimal(metrics.actual_elapsed_ms) >= metrics.expiry_ms:
        return _continuation_result(
            snapshot,
            MonitorStatus.INVALIDATED,
            MonitorReason.SETUP_EXPIRED,
            metrics=metrics,
            levels=levels,
        )
    sign = _side_sign(snapshot.side)
    price_invalidation = snapshot.confirmation_price * (
        _ONE - sign * Decimal("0.25") * snapshot.theta
    )
    if (
        sign * (snapshot.current_mid - price_invalidation) <= _ZERO
        or sign * (snapshot.pullback_extreme - price_invalidation) <= _ZERO
    ):
        return _continuation_result(
            snapshot,
            MonitorStatus.INVALIDATED,
            MonitorReason.PRICE_INVALIDATED,
            metrics=metrics,
            levels=levels,
        )
    if snapshot.integrity is not IntegrityState.NORMAL:
        return _continuation_result(
            snapshot,
            MonitorStatus.BLOCKED_DATA,
            MonitorReason.INTEGRITY_NOT_NORMAL,
            metrics=metrics,
            levels=levels,
        )
    expected_trend = _expected_trend(snapshot.side)
    if snapshot.trend_1h is not expected_trend:
        status = (
            MonitorStatus.INVALIDATED
            if snapshot.armed_ts_ms is not None
            else MonitorStatus.WAIT
        )
        return _continuation_result(
            snapshot,
            status,
            MonitorReason.TREND_PREREQUISITE_MISSING,
            metrics=metrics,
            levels=levels,
        )
    if sign * (snapshot.close_4h - snapshot.ema50_4h) <= _ZERO:
        return _continuation_result(
            snapshot,
            MonitorStatus.WAIT,
            MonitorReason.FOUR_HOUR_TREND_FILTER_FAILED,
            metrics=metrics,
            levels=levels,
        )
    if snapshot.transition is not TransitionState.STABLE:
        return _continuation_result(
            snapshot,
            MonitorStatus.WAIT,
            MonitorReason.TRANSITION_NOT_STABLE,
            metrics=metrics,
            levels=levels,
        )
    if not snapshot.cost_coverage_passed:
        return _continuation_result(
            snapshot,
            MonitorStatus.VETO,
            MonitorReason.COST_COVERAGE_FAILED,
            metrics=metrics,
            levels=levels,
        )
    if not (
        _CONTINUATION_MIN_OVERSHOOT
        <= metrics.overshoot_ratio
        <= _CONTINUATION_MAX_OVERSHOOT
    ):
        return _continuation_result(
            snapshot,
            MonitorStatus.WAIT,
            MonitorReason.OVERSHOOT_NOT_ARMED,
            metrics=metrics,
            levels=levels,
        )

    pending: list[MonitorReason] = []
    if not _MIN_PULLBACK <= metrics.pullback_fraction <= _MAX_PULLBACK:
        pending.append(MonitorReason.PULLBACK_OUTSIDE_RANGE)
    if metrics.reclaim_fraction < _MIN_RECLAIM:
        pending.append(MonitorReason.RECLAIM_PENDING)
    extreme_limit = snapshot.overshoot_extreme * (
        _ONE + sign * Decimal("0.10") * snapshot.theta
    )
    if sign * (extreme_limit - snapshot.current_mid) <= _ZERO:
        pending.append(MonitorReason.EXTREME_LIMIT_FAILED)
    bar = snapshot.five_minute
    breakout_boundary = (
        bar.previous_high
        if snapshot.side is MonitorSide.LONG
        else bar.previous_low
    )
    if sign * (bar.close - breakout_boundary) <= _ZERO:
        pending.append(MonitorReason.BREAKOUT_PENDING)
    if sign * snapshot.taker_imbalance_5m < _CONTINUATION_TAKER_THRESHOLD:
        pending.append(MonitorReason.TAKER_PENDING)
    if sign * snapshot.cvd_slope < _ZERO:
        pending.append(MonitorReason.CVD_PENDING)
    if pending:
        return _continuation_result(
            snapshot,
            MonitorStatus.ARMED,
            *pending,
            metrics=metrics,
            levels=levels,
        )
    if levels.risk_distance > snapshot.maximum_risk_distance:
        return _continuation_result(
            snapshot,
            MonitorStatus.VETO,
            MonitorReason.RISK_DISTANCE_EXCEEDS_CONTRACT,
            metrics=metrics,
            levels=levels,
        )
    return _continuation_result(
        snapshot,
        MonitorStatus.TRIGGERED,
        MonitorReason.TRIGGER_CONDITIONS_MET,
        metrics=metrics,
        levels=levels,
    )


def _validate_reversal(snapshot: ReversalMonitorInput) -> MonitorReason | None:
    common = _validate_common(
        observation_id=snapshot.observation_id,
        observation_sequence=snapshot.observation_sequence,
        as_of_ts_ms=snapshot.as_of_ts_ms,
        confirmation_ts_ms=snapshot.confirmation_ts_ms,
        actual_confirmation=snapshot.actual_confirmation,
        side=snapshot.side,
        confirmation_direction=snapshot.confirmation_direction,
        five_minute=snapshot.five_minute,
    )
    if common is not None:
        return common
    if not isinstance(snapshot.integrity, IntegrityState):
        return MonitorReason.INVALID_INPUT
    boolean_values = (
        snapshot.cvd_divergence_against_prior_trend,
        snapshot.oi_turns_against_prior_direction,
        snapshot.new_extreme_made,
        snapshot.strong_prior_trend,
        snapshot.world_flow_strong_same_direction,
    )
    if any(not _valid_bool(value) for value in boolean_values):
        return MonitorReason.INVALID_INPUT
    decimal_values = (
        snapshot.confirmation_price,
        snapshot.theta,
        snapshot.current_mid,
        snapshot.overshoot_extreme,
        snapshot.session_vwap,
        snapshot.session_poc,
        snapshot.trigger_taker_imbalance_5m,
        snapshot.price_progress_efficiency,
        snapshot.funding_z,
        snapshot.oi_change_z_before_turn,
        snapshot.exhaustion_taker_imbalance_5m,
        snapshot.reversal_side_refill_ratio,
    )
    if any(not _valid_decimal(value) for value in decimal_values):
        return MonitorReason.NONFINITE_INPUT
    prices = (
        snapshot.confirmation_price,
        snapshot.current_mid,
        snapshot.overshoot_extreme,
        snapshot.session_vwap,
        snapshot.session_poc,
    )
    if any(value <= _ZERO for value in prices):
        return MonitorReason.INVALID_PRICE
    if not _ZERO < snapshot.theta < _ONE:
        return MonitorReason.INVALID_INPUT
    if not _ZERO <= snapshot.price_progress_efficiency <= _ONE:
        return MonitorReason.INVALID_INPUT
    if snapshot.reversal_side_refill_ratio < _ZERO:
        return MonitorReason.INVALID_INPUT
    return None


def _reversal_metrics(snapshot: ReversalMonitorInput) -> ReversalMetrics | None:
    prior_sign = (
        _ONE
        if snapshot.confirmation_direction is DCConfirmationDirection.UPTURN
        else -_ONE
    )
    directional_overshoot = prior_sign * (
        snapshot.overshoot_extreme - snapshot.confirmation_price
    )
    directional_retracement = prior_sign * (
        snapshot.overshoot_extreme - snapshot.current_mid
    )
    if directional_overshoot <= _ZERO or directional_retracement < _ZERO:
        return None
    conditions: list[ExhaustionCondition] = []
    if snapshot.price_progress_efficiency <= Decimal("0.25"):
        conditions.append(ExhaustionCondition.LOW_PRICE_PROGRESS_EFFICIENCY)
    if snapshot.cvd_divergence_against_prior_trend:
        conditions.append(ExhaustionCondition.CVD_DIVERGENCE)
    if prior_sign * snapshot.funding_z >= Decimal("1.50"):
        conditions.append(ExhaustionCondition.EXTREME_FUNDING)
    if (
        prior_sign * snapshot.oi_change_z_before_turn >= _ONE
        and snapshot.oi_turns_against_prior_direction
    ):
        conditions.append(ExhaustionCondition.OI_EXTREME_THEN_TURN)
    if (
        prior_sign * snapshot.exhaustion_taker_imbalance_5m > _ZERO
        and not snapshot.new_extreme_made
    ):
        conditions.append(ExhaustionCondition.TAKER_WITHOUT_NEW_EXTREME)
    if snapshot.reversal_side_refill_ratio >= Decimal("0.20"):
        conditions.append(ExhaustionCondition.REVERSAL_SIDE_REFILL)
    if snapshot.integrity is IntegrityState.NORMAL:
        conditions.append(ExhaustionCondition.INTEGRITY_NORMAL)
    return ReversalMetrics(
        overshoot_ratio=directional_overshoot
        / (snapshot.confirmation_price * snapshot.theta),
        actual_retracement_fraction=directional_retracement
        / snapshot.overshoot_extreme,
        exhaustion_conditions=tuple(conditions),
        exhaustion_count=len(conditions),
    )


def _reversal_levels(
    snapshot: ReversalMonitorInput,
) -> ReversalReferenceLevels | None:
    sign = _side_sign(snapshot.side)
    if snapshot.side is MonitorSide.SHORT:
        stop = snapshot.overshoot_extreme * (
            _ONE + Decimal("0.20") * snapshot.theta
        )
    else:
        stop = snapshot.overshoot_extreme * (
            _ONE - Decimal("0.20") * snapshot.theta
        )
    signed_risk_distance = sign * (snapshot.current_mid - stop)
    if signed_risk_distance <= _ZERO:
        return None
    one_point_five_r = (
        snapshot.current_mid + sign * _TP1_R * signed_risk_distance
    )
    candidates = (
        (ReversalTargetSource.CONFIRMATION_PRICE, snapshot.confirmation_price),
        (ReversalTargetSource.SESSION_POC, snapshot.session_poc),
        (ReversalTargetSource.ONE_POINT_FIVE_R, one_point_five_r),
    )
    eligible = [
        (source, price, sign * (price - snapshot.current_mid))
        for source, price in candidates
        if sign * (price - snapshot.current_mid) > _ZERO
    ]
    if not eligible:
        return None
    source, first_target, _distance = min(eligible, key=lambda row: row[2])
    return ReversalReferenceLevels(
        reference_mid=snapshot.current_mid,
        stop=stop,
        risk_distance=signed_risk_distance,
        one_point_five_r_price=one_point_five_r,
        first_target_price=first_target,
        first_target_source=source,
    )


def evaluate_dc02_reversal(snapshot: ReversalMonitorInput) -> ReversalMonitorResult:
    """DC02를 actual-as-of 자료로만 평가하고 주문 생성 권한은 반환하지 않는다."""

    if not isinstance(snapshot, ReversalMonitorInput):
        raise TypeError("ReversalMonitorInput이 필요합니다.")
    invalid = _validate_reversal(snapshot)
    if invalid is not None:
        return _reversal_result(snapshot, MonitorStatus.BLOCKED_DATA, invalid)
    expected_confirmation = (
        DCConfirmationDirection.DOWNTURN
        if snapshot.side is MonitorSide.LONG
        else DCConfirmationDirection.UPTURN
    )
    if snapshot.confirmation_direction is not expected_confirmation:
        return _reversal_result(
            snapshot,
            MonitorStatus.WAIT,
            MonitorReason.CONFIRMATION_DIRECTION_MISMATCH,
        )
    metrics = _reversal_metrics(snapshot)
    levels = _reversal_levels(snapshot)
    if metrics is None:
        return _reversal_result(
            snapshot,
            MonitorStatus.BLOCKED_DATA,
            MonitorReason.PRICE_GEOMETRY_INVALID,
        )
    vetoes: list[MonitorReason] = []
    if snapshot.strong_prior_trend:
        vetoes.append(MonitorReason.STRONG_TREND_VETO)
    if snapshot.world_flow_strong_same_direction:
        vetoes.append(MonitorReason.WORLD_FLOW_VETO)
    if vetoes:
        return _reversal_result(
            snapshot,
            MonitorStatus.VETO,
            *vetoes,
            metrics=metrics,
            levels=levels,
        )
    if metrics.overshoot_ratio < _REVERSAL_MIN_OVERSHOOT:
        return _reversal_result(
            snapshot,
            MonitorStatus.WAIT,
            MonitorReason.OVERSHOOT_NOT_ARMED,
            metrics=metrics,
            levels=levels,
        )
    pending: list[MonitorReason] = []
    if metrics.exhaustion_count < 3:
        pending.append(MonitorReason.EXHAUSTION_PENDING)
    if (
        metrics.actual_retracement_fraction
        < _MIN_RETRACEMENT_THETA_MULTIPLIER * snapshot.theta
    ):
        pending.append(MonitorReason.RETRACEMENT_PENDING)
    sign = _side_sign(snapshot.side)
    bar = snapshot.five_minute
    if sign * (bar.close - snapshot.session_vwap) <= _ZERO:
        pending.append(MonitorReason.VWAP_STRUCTURE_PENDING)
    structure_boundary = (
        bar.previous_high
        if snapshot.side is MonitorSide.LONG
        else bar.previous_low
    )
    if sign * (bar.close - structure_boundary) <= _ZERO:
        pending.append(MonitorReason.FIVE_MINUTE_STRUCTURE_PENDING)
    if sign * snapshot.trigger_taker_imbalance_5m < _ZERO:
        pending.append(MonitorReason.TAKER_PENDING)
    if pending:
        return _reversal_result(
            snapshot,
            MonitorStatus.ARMED,
            *pending,
            metrics=metrics,
            levels=levels,
        )
    if levels is None:
        return _reversal_result(
            snapshot,
            MonitorStatus.INVALIDATED,
            MonitorReason.TARGET_UNAVAILABLE,
            metrics=metrics,
        )
    return _reversal_result(
        snapshot,
        MonitorStatus.TRIGGERED,
        MonitorReason.TRIGGER_CONDITIONS_MET,
        metrics=metrics,
        levels=levels,
    )


def _replay_order_reason(
    snapshot: ContinuationMonitorInput | ReversalMonitorInput,
    *,
    seen_observation_ids: set[str],
    previous_sequence: int | None,
    previous_as_of_ts_ms: int | None,
    continuity_broken: bool,
) -> MonitorReason | None:
    if continuity_broken:
        return MonitorReason.REPLAY_CONTINUITY_BROKEN
    if snapshot.observation_id in seen_observation_ids:
        return MonitorReason.DUPLICATE_OBSERVATION
    if not _valid_int(snapshot.observation_sequence) or not _valid_int(
        snapshot.as_of_ts_ms
    ):
        return MonitorReason.REPLAY_ORDER_INVALID
    if previous_sequence is not None and snapshot.observation_sequence != previous_sequence + 1:
        return MonitorReason.REPLAY_ORDER_INVALID
    if previous_as_of_ts_ms is not None and snapshot.as_of_ts_ms <= previous_as_of_ts_ms:
        return MonitorReason.REPLAY_ORDER_INVALID
    return None


def _continuation_context_key(snapshot: ContinuationMonitorInput) -> tuple[object, ...]:
    return (
        snapshot.side,
        snapshot.horizon,
        snapshot.exit_variant,
        snapshot.confirmation_direction,
        snapshot.confirmation_ts_ms,
        snapshot.confirmation_price,
        snapshot.theta,
    )


def _reversal_context_key(snapshot: ReversalMonitorInput) -> tuple[object, ...]:
    return (
        snapshot.side,
        snapshot.confirmation_direction,
        snapshot.confirmation_ts_ms,
        snapshot.confirmation_price,
        snapshot.theta,
    )


def evaluate_dc01_replay(
    snapshots: Sequence[ContinuationMonitorInput],
) -> tuple[ContinuationMonitorResult, ...]:
    """한 DC01 setup의 연속 replay에서 arm·무효화와 순서를 보존한다."""

    seen_observation_ids: set[str] = set()
    previous_sequence: int | None = None
    previous_as_of_ts_ms: int | None = None
    previous_actual_confirmation: bool | None = None
    expected_context: tuple[object, ...] | None = None
    continuity_broken = False
    armed_ts_ms: int | None = None
    invalidated = False
    results: list[ContinuationMonitorResult] = []
    for snapshot in snapshots:
        reason = _replay_order_reason(
            snapshot,
            seen_observation_ids=seen_observation_ids,
            previous_sequence=previous_sequence,
            previous_as_of_ts_ms=previous_as_of_ts_ms,
            continuity_broken=continuity_broken,
        )
        context = _continuation_context_key(snapshot)
        if (
            reason is None
            and previous_actual_confirmation is False
            and snapshot.actual_confirmation is True
            and previous_as_of_ts_ms is not None
            and snapshot.confirmation_ts_ms <= previous_as_of_ts_ms
        ):
            reason = MonitorReason.RETROACTIVE_ACTUAL_CONFIRMATION
        if reason is None and expected_context is not None and context != expected_context:
            reason = MonitorReason.REPLAY_ORDER_INVALID
        if reason is not None:
            results.append(
                _continuation_result(snapshot, MonitorStatus.BLOCKED_DATA, reason)
            )
            continuity_broken = True
            continue
        expected_context = context
        effective_armed_ts_ms = snapshot.armed_ts_ms
        if armed_ts_ms is not None:
            effective_armed_ts_ms = (
                armed_ts_ms
                if effective_armed_ts_ms is None
                else min(armed_ts_ms, effective_armed_ts_ms)
            )
        effective = (
            snapshot
            if effective_armed_ts_ms == snapshot.armed_ts_ms
            else replace(snapshot, armed_ts_ms=effective_armed_ts_ms)
        )
        result = evaluate_dc01_continuation(effective)
        if invalidated and result.status is not MonitorStatus.BLOCKED_DATA:
            result = _continuation_result(
                effective,
                MonitorStatus.INVALIDATED,
                MonitorReason.SETUP_ALREADY_INVALIDATED,
                metrics=result.metrics,
                levels=result.reference_levels,
            )
        results.append(result)
        if result.status in {MonitorStatus.ARMED, MonitorStatus.TRIGGERED}:
            armed_ts_ms = effective.armed_ts_ms or effective.as_of_ts_ms
        elif result.status is MonitorStatus.INVALIDATED:
            invalidated = True
        seen_observation_ids.add(snapshot.observation_id)
        previous_sequence = snapshot.observation_sequence
        previous_as_of_ts_ms = snapshot.as_of_ts_ms
        previous_actual_confirmation = snapshot.actual_confirmation
    return tuple(results)


def evaluate_dc02_replay(
    snapshots: Sequence[ReversalMonitorInput],
) -> tuple[ReversalMonitorResult, ...]:
    """한 DC02 setup의 연속 replay를 순서·context 혼합 없이 판정한다."""

    seen_observation_ids: set[str] = set()
    previous_sequence: int | None = None
    previous_as_of_ts_ms: int | None = None
    previous_actual_confirmation: bool | None = None
    expected_context: tuple[object, ...] | None = None
    continuity_broken = False
    results: list[ReversalMonitorResult] = []
    for snapshot in snapshots:
        reason = _replay_order_reason(
            snapshot,
            seen_observation_ids=seen_observation_ids,
            previous_sequence=previous_sequence,
            previous_as_of_ts_ms=previous_as_of_ts_ms,
            continuity_broken=continuity_broken,
        )
        context = _reversal_context_key(snapshot)
        if (
            reason is None
            and previous_actual_confirmation is False
            and snapshot.actual_confirmation is True
            and previous_as_of_ts_ms is not None
            and snapshot.confirmation_ts_ms <= previous_as_of_ts_ms
        ):
            reason = MonitorReason.RETROACTIVE_ACTUAL_CONFIRMATION
        if reason is None and expected_context is not None and context != expected_context:
            reason = MonitorReason.REPLAY_ORDER_INVALID
        if reason is not None:
            results.append(_reversal_result(snapshot, MonitorStatus.BLOCKED_DATA, reason))
            continuity_broken = True
            continue
        expected_context = context
        results.append(evaluate_dc02_reversal(snapshot))
        seen_observation_ids.add(snapshot.observation_id)
        previous_sequence = snapshot.observation_sequence
        previous_as_of_ts_ms = snapshot.as_of_ts_ms
        previous_actual_confirmation = snapshot.actual_confirmation
    return tuple(results)
