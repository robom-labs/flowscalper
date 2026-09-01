# 완료 1분 수익률로 Semivariance·Periodicity·Jump 연구 피처를 결정적으로 계산한다.
"""고정 메모리 rolling 피처와 방향 비생성 Router·위험축소 계약을 제공한다."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import StrEnum
from statistics import median

from backend.app.domain.models import Side

_MINUTE_MS = 60_000
_FIVE_MINUTE_MS = 5 * _MINUTE_MS
_DAY_MS = 24 * 60 * _MINUTE_MS
_WEEK_MS = 7 * _DAY_MS
_FIVE_MINUTE_SLOTS_PER_WEEK = 7 * 24 * 12
_PI = Decimal("3.1415926535897932384626433832795028841971693993751")
_DEFAULT_EPSILON = Decimal("1E-30")
_MATH_PRECISION = 50


class SemivarianceInputError(ValueError):
    """Semivariance·Jump 입력이 완료봉·시간·수치 계약을 위반했다."""


class FeatureReadiness(StrEnum):
    WARMUP = "WARMUP"
    READY = "READY"
    PERIODICITY_UNCALIBRATED = "PERIODICITY_UNCALIBRATED"


def _validate_decimal(field_name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise SemivarianceInputError(f"{field_name}는 유한한 Decimal이어야 합니다.")


def _validate_unit_decimal(field_name: str, value: Decimal) -> None:
    _validate_decimal(field_name, value)
    if not Decimal(0) <= value <= Decimal(1):
        raise SemivarianceInputError(f"{field_name}는 0 이상 1 이하여야 합니다.")


def _add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _MATH_PRECISION
        return left + right


def _subtract(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _MATH_PRECISION
        return left - right


def _multiply(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _MATH_PRECISION
        return left * right


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = _MATH_PRECISION
        return numerator / denominator


def _utc_week_start_ms(timestamp_ms: int) -> int:
    day_index = timestamp_ms // _DAY_MS
    monday_day_index = ((day_index + 3) // 7) * 7 - 3
    return monday_day_index * _DAY_MS


def five_minute_slot_utc(minute_start_ts_ms: int) -> int:
    """UTC Monday 00:00을 0으로 하는 0..2015 slot을 반환한다."""

    if (
        not isinstance(minute_start_ts_ms, int)
        or isinstance(minute_start_ts_ms, bool)
        or minute_start_ts_ms < 0
        or minute_start_ts_ms % _MINUTE_MS != 0
    ):
        raise SemivarianceInputError("1분 시작시각은 UTC 분 경계에 정렬된 정수여야 합니다.")
    day_index = minute_start_ts_ms // _DAY_MS
    weekday_utc = (day_index + 3) % 7
    five_minute_index = (minute_start_ts_ms % _DAY_MS) // _FIVE_MINUTE_MS
    return int(weekday_utc * 288 + five_minute_index)


@dataclass(frozen=True, slots=True)
class CompletedMinuteReturn:
    """시작시각과 완료근거를 포함한 1분 log return이다."""

    minute_start_ts_ms: int
    completed_ts_ms: int
    log_return: Decimal
    interval_ms: int = _MINUTE_MS
    completed: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.minute_start_ts_ms, int)
            or isinstance(self.minute_start_ts_ms, bool)
            or self.minute_start_ts_ms < 0
            or self.minute_start_ts_ms % _MINUTE_MS != 0
        ):
            raise SemivarianceInputError("1분 return 시작시각이 UTC 분 경계와 다릅니다.")
        if not isinstance(self.completed_ts_ms, int) or isinstance(self.completed_ts_ms, bool):
            raise SemivarianceInputError("완료시각은 정수여야 합니다.")
        if self.interval_ms != _MINUTE_MS or self.completed is not True:
            raise SemivarianceInputError("완료된 1분 return만 받을 수 있습니다.")
        if self.completed_ts_ms < self.minute_start_ts_ms + self.interval_ms:
            raise SemivarianceInputError("완료시각이 1분 구간 종료보다 빠릅니다.")
        _validate_decimal("log_return", self.log_return)


@dataclass(frozen=True, slots=True)
class CompletedPeriodicityWeek:
    """현재 주를 제외한 하나의 완료 UTC 주간 5분 return 배열이다."""

    week_start_ts_ms: int
    five_minute_log_returns: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if self.week_start_ts_ms < 0 or _utc_week_start_ms(self.week_start_ts_ms) != (
            self.week_start_ts_ms
        ):
            raise SemivarianceInputError("주간 시작시각은 UTC 월요일 00:00에 정렬돼야 합니다.")
        if len(self.five_minute_log_returns) != _FIVE_MINUTE_SLOTS_PER_WEEK:
            raise SemivarianceInputError("완료 주간 배열에는 5분 slot 2016개가 필요합니다.")
        for value in self.five_minute_log_returns:
            _validate_decimal("periodicity return", value)


@dataclass(frozen=True, slots=True)
class PeriodicityCalibration:
    status: FeatureReadiness
    completed_week_count: int
    calibration_through_week_start_ms: int | None
    slot_scales: tuple[Decimal, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.completed_week_count < 0 or self.completed_week_count > 12:
            raise SemivarianceInputError("Periodicity 완료 주간은 0..12주여야 합니다.")
        if self.status is FeatureReadiness.READY:
            if self.completed_week_count < 8:
                raise SemivarianceInputError("Periodicity 보정에는 최소 8주가 필요합니다.")
            if self.calibration_through_week_start_ms is None:
                raise SemivarianceInputError("Periodicity 보정 종료 주가 필요합니다.")
            if len(self.slot_scales) != _FIVE_MINUTE_SLOTS_PER_WEEK:
                raise SemivarianceInputError("Periodicity 보정에는 slot scale 2016개가 필요합니다.")
        elif self.slot_scales:
            raise SemivarianceInputError("미보정 Periodicity에 slot scale을 저장할 수 없습니다.")
        for scale in self.slot_scales:
            _validate_decimal("slot_scale", scale)
            if not Decimal("0.25") <= scale <= Decimal("4.00"):
                raise SemivarianceInputError("slot_scale은 0.25..4.00 범위여야 합니다.")

    def scale_for(self, minute_start_ts_ms: int) -> Decimal | None:
        if self.status is not FeatureReadiness.READY:
            return None
        assert self.calibration_through_week_start_ms is not None
        if _utc_week_start_ms(minute_start_ts_ms) <= self.calibration_through_week_start_ms:
            raise SemivarianceInputError(
                "현재 주나 미래 주를 Periodicity calibration에 섞을 수 없습니다."
            )
        return self.slot_scales[five_minute_slot_utc(minute_start_ts_ms)]


def build_periodicity_calibration(
    weeks: Sequence[CompletedPeriodicityWeek],
) -> PeriodicityCalibration:
    """최대 12개 완료 주만으로 2016개 slot scale을 고정한다."""

    ordered = tuple(sorted(weeks, key=lambda row: row.week_start_ts_ms))[-12:]
    if len({week.week_start_ts_ms for week in ordered}) != len(ordered):
        raise SemivarianceInputError("동일 완료 주간을 중복 사용할 수 없습니다.")
    through = ordered[-1].week_start_ts_ms if ordered else None
    if len(ordered) < 8:
        return PeriodicityCalibration(
            status=FeatureReadiness.PERIODICITY_UNCALIBRATED,
            completed_week_count=len(ordered),
            calibration_through_week_start_ms=through,
            slot_scales=(),
            reason_codes=("PERIODICITY_UNCALIBRATED", "COMPLETED_WEEKS_LT_8"),
        )
    if any(
        right.week_start_ts_ms - left.week_start_ts_ms != _WEEK_MS
        for left, right in zip(ordered, ordered[1:], strict=False)
    ):
        return PeriodicityCalibration(
            status=FeatureReadiness.PERIODICITY_UNCALIBRATED,
            completed_week_count=len(ordered),
            calibration_through_week_start_ms=through,
            slot_scales=(),
            reason_codes=("PERIODICITY_UNCALIBRATED", "COMPLETED_WEEK_GAP"),
        )
    all_absolute_returns = tuple(
        abs(value) for week in ordered for value in week.five_minute_log_returns
    )
    baseline = median(all_absolute_returns)
    if baseline <= 0:
        return PeriodicityCalibration(
            status=FeatureReadiness.PERIODICITY_UNCALIBRATED,
            completed_week_count=len(ordered),
            calibration_through_week_start_ms=through,
            slot_scales=(),
            reason_codes=("PERIODICITY_UNCALIBRATED", "ZERO_ABSOLUTE_RETURN_BASELINE"),
        )
    slot_scales: list[Decimal] = []
    for slot in range(_FIVE_MINUTE_SLOTS_PER_WEEK):
        same_slot = median(tuple(abs(week.five_minute_log_returns[slot]) for week in ordered))
        raw_scale = _divide(same_slot, baseline)
        slot_scales.append(min(Decimal("4.00"), max(Decimal("0.25"), raw_scale)))
    return PeriodicityCalibration(
        status=FeatureReadiness.READY,
        completed_week_count=len(ordered),
        calibration_through_week_start_ms=through,
        slot_scales=tuple(slot_scales),
        reason_codes=(),
    )


@dataclass(frozen=True, slots=True)
class RealizedSemivarianceSnapshot:
    window_size: int
    sample_count: int
    status: FeatureReadiness
    warmup_remaining: int
    rs_plus: Decimal
    rs_minus: Decimal
    realized_variance: Decimal
    upside_share: Decimal
    downside_share: Decimal
    imbalance: Decimal


@dataclass(frozen=True, slots=True)
class JumpVariationSnapshot:
    window_size: int
    sample_count: int
    status: FeatureReadiness
    warmup_remaining: int
    realized_variance: Decimal
    bipower_variation: Decimal
    jump_variation: Decimal
    jump_ratio: Decimal


@dataclass(frozen=True, slots=True)
class SemivarianceJumpSnapshot:
    minute_start_ts_ms: int
    log_return: Decimal
    one_hour: RealizedSemivarianceSnapshot
    four_hour: RealizedSemivarianceSnapshot
    jump_one_hour: JumpVariationSnapshot
    periodicity_scale: Decimal | None


class _RollingSemivariance:
    __slots__ = ("_capacity", "_rs_minus", "_rs_plus", "_squares")

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._squares: deque[tuple[Decimal, Decimal]] = deque()
        self._rs_plus = Decimal(0)
        self._rs_minus = Decimal(0)

    def add(self, value: Decimal) -> None:
        square = _multiply(value, value)
        plus = square if value > 0 else Decimal(0)
        minus = square if value < 0 else Decimal(0)
        if len(self._squares) == self._capacity:
            old_plus, old_minus = self._squares.popleft()
            self._rs_plus = _subtract(self._rs_plus, old_plus)
            self._rs_minus = _subtract(self._rs_minus, old_minus)
        self._squares.append((plus, minus))
        self._rs_plus = _add(self._rs_plus, plus)
        self._rs_minus = _add(self._rs_minus, minus)

    def snapshot(self, epsilon: Decimal) -> RealizedSemivarianceSnapshot:
        sample_count = len(self._squares)
        realized_variance = _add(self._rs_plus, self._rs_minus)
        denominator = max(realized_variance, epsilon)
        return RealizedSemivarianceSnapshot(
            window_size=self._capacity,
            sample_count=sample_count,
            status=(
                FeatureReadiness.READY
                if sample_count == self._capacity
                else FeatureReadiness.WARMUP
            ),
            warmup_remaining=self._capacity - sample_count,
            rs_plus=self._rs_plus,
            rs_minus=self._rs_minus,
            realized_variance=realized_variance,
            upside_share=_divide(self._rs_plus, denominator),
            downside_share=_divide(self._rs_minus, denominator),
            imbalance=_divide(_subtract(self._rs_plus, self._rs_minus), denominator),
        )

    @property
    def sample_count(self) -> int:
        return len(self._squares)


class _RollingJumpVariation:
    __slots__ = ("_capacity", "_pair_sum", "_realized_variance", "_returns")

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._returns: deque[Decimal] = deque()
        self._realized_variance = Decimal(0)
        self._pair_sum = Decimal(0)

    def add(self, value: Decimal) -> None:
        if len(self._returns) == self._capacity:
            removed = self._returns.popleft()
            self._realized_variance = _subtract(
                self._realized_variance,
                _multiply(removed, removed),
            )
            if self._returns:
                self._pair_sum = _subtract(
                    self._pair_sum,
                    _multiply(abs(removed), abs(self._returns[0])),
                )
        if self._returns:
            self._pair_sum = _add(
                self._pair_sum,
                _multiply(abs(self._returns[-1]), abs(value)),
            )
        self._returns.append(value)
        self._realized_variance = _add(
            self._realized_variance,
            _multiply(value, value),
        )

    def snapshot(
        self,
        epsilon: Decimal,
        *,
        periodicity_ready: bool,
    ) -> JumpVariationSnapshot:
        sample_count = len(self._returns)
        if not periodicity_ready:
            status = FeatureReadiness.PERIODICITY_UNCALIBRATED
        elif sample_count < self._capacity:
            status = FeatureReadiness.WARMUP
        else:
            status = FeatureReadiness.READY
        bipower_variation = _multiply(_divide(_PI, Decimal(2)), self._pair_sum)
        jump_variation = max(_subtract(self._realized_variance, bipower_variation), Decimal(0))
        jump_ratio = _divide(jump_variation, max(self._realized_variance, epsilon))
        return JumpVariationSnapshot(
            window_size=self._capacity,
            sample_count=sample_count,
            status=status,
            warmup_remaining=max(0, self._capacity - sample_count),
            realized_variance=self._realized_variance,
            bipower_variation=bipower_variation,
            jump_variation=jump_variation,
            jump_ratio=jump_ratio,
        )

    @property
    def sample_count(self) -> int:
        return len(self._returns)


class SemivarianceJumpEngine:
    """완료 1분 return만 시간순으로 받는 고정 메모리 피처 엔진이다."""

    def __init__(
        self,
        *,
        periodicity: PeriodicityCalibration | None = None,
        epsilon: Decimal = _DEFAULT_EPSILON,
    ) -> None:
        _validate_decimal("epsilon", epsilon)
        if epsilon <= 0:
            raise SemivarianceInputError("epsilon은 양수여야 합니다.")
        self._periodicity = periodicity
        self._epsilon = epsilon
        self._one_hour = _RollingSemivariance(60)
        self._four_hour = _RollingSemivariance(240)
        self._jump_one_hour = _RollingJumpVariation(60)
        self._last_minute_start_ts_ms: int | None = None

    def update(self, observation: CompletedMinuteReturn) -> SemivarianceJumpSnapshot:
        if not isinstance(observation, CompletedMinuteReturn):
            raise SemivarianceInputError("CompletedMinuteReturn만 입력할 수 있습니다.")
        if self._last_minute_start_ts_ms is not None:
            expected = self._last_minute_start_ts_ms + _MINUTE_MS
            if observation.minute_start_ts_ms <= self._last_minute_start_ts_ms:
                raise SemivarianceInputError("1분 return이 중복 또는 역순입니다.")
            if observation.minute_start_ts_ms != expected:
                raise SemivarianceInputError("1분 return 구간에 gap이 있습니다.")
        scale = self._periodicity.scale_for(observation.minute_start_ts_ms) if (
            self._periodicity is not None
        ) else None
        adjusted_return = _divide(observation.log_return, scale) if scale is not None else None

        self._one_hour.add(observation.log_return)
        self._four_hour.add(observation.log_return)
        if adjusted_return is not None:
            self._jump_one_hour.add(adjusted_return)
        self._last_minute_start_ts_ms = observation.minute_start_ts_ms
        return SemivarianceJumpSnapshot(
            minute_start_ts_ms=observation.minute_start_ts_ms,
            log_return=observation.log_return,
            one_hour=self._one_hour.snapshot(self._epsilon),
            four_hour=self._four_hour.snapshot(self._epsilon),
            jump_one_hour=self._jump_one_hour.snapshot(
                self._epsilon,
                periodicity_ready=scale is not None,
            ),
            periodicity_scale=scale,
        )

    @property
    def buffer_sizes(self) -> tuple[int, int, int]:
        """1h semivariance, 4h semivariance, 1h jump 버퍼 크기다."""

        return (
            self._one_hour.sample_count,
            self._four_hour.sample_count,
            self._jump_one_hour.sample_count,
        )


class RouterIntent(StrEnum):
    MOMENTUM = "MOMENTUM"
    REVERSAL = "REVERSAL"


class RouterStatus(StrEnum):
    WAIT = "WAIT"
    MOMENTUM_PASS = "MOMENTUM_PASS"
    MOMENTUM_BLOCKED = "MOMENTUM_BLOCKED"
    MOMENTUM_VETO = "MOMENTUM_VETO"
    REVERSAL_ARMED = "REVERSAL_ARMED"
    REVERSAL_NOT_ARMED = "REVERSAL_NOT_ARMED"


@dataclass(frozen=True, slots=True)
class SemivarianceRouterMetrics:
    semivariance_1h_ready: bool
    semivariance_4h_ready: bool
    jump_1h_ready: bool
    upside_share_1h: Decimal
    downside_share_1h: Decimal
    upside_share_4h: Decimal
    downside_share_4h: Decimal
    jump_ratio_1h: Decimal
    positive_jump_share_1h: Decimal
    negative_jump_share_1h: Decimal
    efficiency_ratio_20_1h: Decimal
    trend_condition_passed: bool
    close_5m: Decimal
    session_vwap: Decimal
    oi_change_z: Decimal
    long_liquidation_z: Decimal
    short_liquidation_z: Decimal

    def __post_init__(self) -> None:
        for field_name in (
            "upside_share_1h",
            "downside_share_1h",
            "upside_share_4h",
            "downside_share_4h",
            "jump_ratio_1h",
            "positive_jump_share_1h",
            "negative_jump_share_1h",
            "efficiency_ratio_20_1h",
        ):
            _validate_unit_decimal(field_name, getattr(self, field_name))
        for field_name in (
            "close_5m",
            "session_vwap",
            "oi_change_z",
            "long_liquidation_z",
            "short_liquidation_z",
        ):
            _validate_decimal(field_name, getattr(self, field_name))
        if self.close_5m <= 0 or self.session_vwap <= 0:
            raise SemivarianceInputError("5분 close와 session VWAP은 양수여야 합니다.")


@dataclass(frozen=True, slots=True)
class SemivarianceRouterDecision:
    candidate_side: Side
    intent: RouterIntent
    status: RouterStatus
    reason_codes: tuple[str, ...]

    @property
    def route_allowed(self) -> bool:
        return self.status in {RouterStatus.MOMENTUM_PASS, RouterStatus.REVERSAL_ARMED}

    @property
    def creates_direction(self) -> bool:
        return False


def evaluate_semivariance_router(
    *,
    candidate_side: Side,
    intent: RouterIntent,
    metrics: SemivarianceRouterMetrics,
    veto_enabled: bool = True,
) -> SemivarianceRouterDecision:
    """외부에서 정한 candidate side를 바꾸지 않고 momentum·reversal만 판정한다."""

    if not isinstance(candidate_side, Side) or not isinstance(intent, RouterIntent):
        raise SemivarianceInputError("Router에는 기존 candidate side와 intent가 필요합니다.")
    if intent is RouterIntent.REVERSAL:
        if not metrics.semivariance_1h_ready:
            return SemivarianceRouterDecision(
                candidate_side,
                intent,
                RouterStatus.WAIT,
                ("SEMIVARIANCE_1H_WARMUP",),
            )
        adverse_share = (
            metrics.downside_share_1h
            if candidate_side is Side.LONG
            else metrics.upside_share_1h
        )
        liquidation_z = (
            metrics.long_liquidation_z
            if candidate_side is Side.LONG
            else metrics.short_liquidation_z
        )
        failures: list[str] = []
        if adverse_share < Decimal("0.75"):
            failures.append("ADVERSE_SEMIVARIANCE_SHARE_LT_0_75")
        if metrics.oi_change_z > Decimal("-1.50"):
            failures.append("OI_CHANGE_Z_GT_NEG_1_50")
        if liquidation_z < Decimal("2.00"):
            failures.append("LIQUIDATION_Z_LT_2_00")
        return SemivarianceRouterDecision(
            candidate_side,
            intent,
            (
                RouterStatus.REVERSAL_NOT_ARMED
                if failures
                else RouterStatus.REVERSAL_ARMED
            ),
            tuple(failures) if failures else ("REVERSAL_CONTEXT_ARMED",),
        )

    if not (
        metrics.semivariance_1h_ready
        and metrics.semivariance_4h_ready
        and metrics.jump_1h_ready
    ):
        return SemivarianceRouterDecision(
            candidate_side,
            intent,
            RouterStatus.WAIT,
            ("SEMIVARIANCE_OR_JUMP_WARMUP",),
        )
    favorable_share = (
        metrics.upside_share_1h
        if candidate_side is Side.LONG
        else metrics.downside_share_1h
    )
    adverse_share_1h = (
        metrics.downside_share_1h
        if candidate_side is Side.LONG
        else metrics.upside_share_1h
    )
    adverse_share_4h = (
        metrics.downside_share_4h
        if candidate_side is Side.LONG
        else metrics.upside_share_4h
    )
    adverse_jump_share = (
        metrics.negative_jump_share_1h
        if candidate_side is Side.LONG
        else metrics.positive_jump_share_1h
    )
    wrong_vwap_side = (
        metrics.close_5m < metrics.session_vwap
        if candidate_side is Side.LONG
        else metrics.close_5m > metrics.session_vwap
    )
    veto_reasons: list[str] = []
    if adverse_share_1h >= Decimal("0.65"):
        veto_reasons.append("ADVERSE_SEMIVARIANCE_VETO")
    if adverse_jump_share >= Decimal("0.50"):
        veto_reasons.append("ADVERSE_JUMP_VETO")
    if wrong_vwap_side:
        veto_reasons.append("SESSION_VWAP_VETO")
    if veto_enabled and veto_reasons:
        return SemivarianceRouterDecision(
            candidate_side,
            intent,
            RouterStatus.MOMENTUM_VETO,
            tuple(veto_reasons),
        )

    failures = []
    if not Decimal("0.55") <= favorable_share <= Decimal("0.80"):
        failures.append("FAVORABLE_SEMIVARIANCE_SHARE_OUTSIDE_0_55_0_80")
    if adverse_share_4h > Decimal("0.55"):
        failures.append("ADVERSE_SEMIVARIANCE_4H_GT_0_55")
    if metrics.jump_ratio_1h > Decimal("0.35"):
        failures.append("JUMP_RATIO_1H_GT_0_35")
    if metrics.efficiency_ratio_20_1h < Decimal("0.35"):
        failures.append("ER20_1H_LT_0_35")
    if not metrics.trend_condition_passed:
        failures.append("TREND_CONDITION_NOT_PASSED")
    return SemivarianceRouterDecision(
        candidate_side,
        intent,
        RouterStatus.MOMENTUM_BLOCKED if failures else RouterStatus.MOMENTUM_PASS,
        tuple(failures) if failures else ("MOMENTUM_CONTEXT_PASSED",),
    )


class DownsideRiskStatus(StrEnum):
    WAIT = "WAIT"
    READY = "READY"


@dataclass(frozen=True, slots=True)
class DownsideRiskMultiplierDecision:
    status: DownsideRiskStatus
    valid_symbol_count: int
    downside_symbol_count: int
    downside_breadth: Decimal | None
    multiplier: Decimal | None
    reason_codes: tuple[str, ...]


def downside_semivariance_risk_multiplier(
    downside_shares_1h: Iterable[Decimal | None],
) -> DownsideRiskMultiplierDecision:
    """valid symbol의 downside breadth로 LONG 위험을 줄이기만 한다."""

    valid: list[Decimal] = []
    for share in downside_shares_1h:
        if share is None:
            continue
        _validate_unit_decimal("downside_share_1h", share)
        valid.append(share)
    if not valid:
        return DownsideRiskMultiplierDecision(
            status=DownsideRiskStatus.WAIT,
            valid_symbol_count=0,
            downside_symbol_count=0,
            downside_breadth=None,
            multiplier=None,
            reason_codes=("VALID_SYMBOL_COUNT_ZERO",),
        )
    downside_count = sum(share >= Decimal("0.65") for share in valid)
    breadth = _divide(Decimal(downside_count), Decimal(len(valid)))
    if breadth < Decimal("0.40"):
        multiplier = Decimal("1.00")
    elif breadth < Decimal("0.60"):
        multiplier = Decimal("0.75")
    elif breadth < Decimal("0.75"):
        multiplier = Decimal("0.50")
    else:
        multiplier = Decimal("0.25")
    return DownsideRiskMultiplierDecision(
        status=DownsideRiskStatus.READY,
        valid_symbol_count=len(valid),
        downside_symbol_count=downside_count,
        downside_breadth=breadth,
        multiplier=multiplier,
        reason_codes=("DOWNSIDE_SEMIVARIANCE_RISK_REDUCTION",),
    )
