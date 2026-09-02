# 완성 15분·30분 봉과 현재 공개 호가 흐름을 결합해 중단기 추세 PAPER 후보를 평가한다.

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import fmean

from backend.app.domain.models import Side
from backend.app.features import FeatureSnapshot
from backend.app.market_data import Candle
from backend.app.regime import Regime
from backend.app.strategies.base import (
    CandidateDecision,
    CandidateStatus,
    PlanInputs,
    costed_plan,
)


class IntradayTrendVariant(StrEnum):
    PULLBACK_RECLAIM_15M = "PULLBACK_RECLAIM_15M"
    BREAKOUT_RETEST_15M = "BREAKOUT_RETEST_15M"
    BREAKOUT_RETEST_30M = "BREAKOUT_RETEST_30M"
    MULTISPEED_RECLAIM_30M = "MULTISPEED_RECLAIM_30M"


@dataclass(frozen=True, slots=True)
class IntradayTrendState:
    latest_open_ts_ms: int | None
    interval_seconds: int
    direction: Side | None
    atr: float | None
    structural_stop: float | None
    momentum_24h: float | None
    adx: float | None
    relative_volume: float | None
    reason_codes: tuple[str, ...]
    history_count: int = 0
    hourly_history_count: int = 0
    setup_confirmed: bool = False
    breakout_relative_volume: float | None = None

    @property
    def signal_ts_ms(self) -> int | None:
        if self.latest_open_ts_ms is None:
            return None
        return self.latest_open_ts_ms + self.interval_seconds * 1_000


@dataclass(frozen=True, slots=True)
class IntradayTrendContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    state: IntradayTrendState
    signal_age_ms: int | None
    confirmation_ms: int
    risk_atr: float | None


@dataclass(frozen=True, slots=True)
class IntradayTrendStrategy:
    strategy_id: str
    variant: IntradayTrendVariant
    interval_seconds: int
    take_profit_2_r: float
    confirmation_required_ms: int = 1_000
    maximum_signal_age_ms: int = 5_000

    def evaluate(self, context: IntradayTrendContext) -> CandidateDecision:
        rejections = list(context.state.reason_codes)
        if not context.features.data_healthy:
            rejections.append("STALE_OR_DEGRADED_DATA")
        if context.regime in {Regime.SHOCK, Regime.DEGRADED, Regime.WARMUP}:
            rejections.append(f"REGIME_{context.regime.value}")
        opposite_regime = (
            Regime.TREND_DOWN if context.side is Side.LONG else Regime.TREND_UP
        )
        if context.regime is opposite_regime:
            rejections.append("CURRENT_MICRO_REGIME_OPPOSES_TREND")
        if context.features.spread_bps > 12:
            rejections.append("WIDE_SPREAD")
        if context.state.direction is not context.side:
            rejections.append("INTRADAY_DIRECTION_MISMATCH")
        if context.signal_age_ms is None:
            rejections.append("INTRADAY_SIGNAL_NOT_READY")
        elif not 0 <= context.signal_age_ms <= self.maximum_signal_age_ms:
            rejections.append("NO_NEW_COMPLETED_INTRADAY_SIGNAL")
        if context.confirmation_ms < self.confirmation_required_ms:
            rejections.append("PUBLIC_BOOK_FLOW_CONFIRMATION_PENDING")
        if context.risk_atr is None or not 0.65 <= context.risk_atr <= 3.0:
            rejections.append("STRUCTURAL_STOP_DISTANCE_OUT_OF_RANGE")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "COMPLETED_INTRADAY_TREND_ALIGNED",
            f"{self.variant.value}_CONFIRMED",
            "HIGHER_TIMEFRAME_TREND_ALIGNED",
            "PUBLIC_BOOK_AND_TRADE_FLOW_CONFIRMED",
            "STRUCTURAL_STOP_FIXED_BEFORE_ENTRY",
            "ACTUAL_BOOK_ENTRY_REQUIRED",
        )
        return CandidateDecision(
            strategy_id=self.strategy_id,
            side=context.side,
            status=CandidateStatus.REJECTED if rejections else CandidateStatus.QUALIFIED,
            reason_codes=() if rejections else reasons,
            rejection_codes=tuple(dict.fromkeys(rejections)),
            planned_entry=plan.entry if plan else None,
            initial_stop=plan.stop if plan else None,
            take_profit=plan.target if plan else None,
            expected_cost_bps=context.plan.expected_total_cost_bps,
            net_reward_risk=plan.net_reward_risk if plan else None,
        )


def intraday_flow_confirmation_ready(
    side: Side,
    snapshot: FeatureSnapshot,
    regime: Regime,
) -> bool:
    """완성봉 신호 뒤 현재 공개 호가·체결이 같은 방향일 때만 확인한다."""

    direction = 1 if side is Side.LONG else -1
    opposite_regime = Regime.TREND_DOWN if side is Side.LONG else Regime.TREND_UP
    return (
        snapshot.data_healthy
        and regime not in {Regime.SHOCK, Regime.DEGRADED, Regime.WARMUP, opposite_regime}
        and snapshot.spread_bps <= 12
        and snapshot.ofi_1s * direction > 0
        and snapshot.ofi_3s * direction > 0
        and snapshot.trade_imbalance_3s * direction >= 0.10
        and snapshot.microprice_minus_mid_bps * direction > 0
    )


def intraday_trend_state(
    candles: Sequence[Candle],
    hourly_candles: Sequence[Candle],
    variant: IntradayTrendVariant,
) -> IntradayTrendState:
    interval_seconds = (
        900
        if variant
        in {
            IntradayTrendVariant.PULLBACK_RECLAIM_15M,
            IntradayTrendVariant.BREAKOUT_RETEST_15M,
        }
        else 1_800
    )
    ordered = tuple(
        sorted(
            (row for row in candles if row.interval_seconds == interval_seconds),
            key=lambda row: row.open_ts_ms,
        )
    )
    latest_open_ts_ms = ordered[-1].open_ts_ms if ordered else None
    if len(ordered) < 100:
        return _empty_state(
            latest_open_ts_ms,
            interval_seconds,
            "INTRADAY_HISTORY_WARMUP",
            history_count=len(ordered),
            hourly_history_count=len(hourly_candles),
        )
    if any(
        current.open_ts_ms - previous.open_ts_ms != interval_seconds * 1_000
        for previous, current in zip(ordered[-100:-1], ordered[-99:], strict=True)
    ):
        return _empty_state(
            latest_open_ts_ms,
            interval_seconds,
            "INTRADAY_CANDLE_GAP",
            history_count=len(ordered),
            hourly_history_count=len(hourly_candles),
        )

    latest = ordered[-1]
    previous = ordered[-2]
    closes = [float(row.close) for row in ordered]
    ema20 = _ema(closes[-100:], 20)
    ema80 = _ema(closes[-160:], 80)
    previous_ema20 = _ema(closes[-101:-1], 20)
    previous_ema80 = _ema(closes[-161:-1], 80)
    base_direction = (
        Side.LONG
        if ema20 > ema80 and ema20 >= previous_ema20 and float(latest.close) > ema80
        else Side.SHORT
        if ema20 < ema80 and ema20 <= previous_ema20 and float(latest.close) < ema80
        else None
    )
    higher_direction = _hourly_direction(hourly_candles)
    direction = base_direction if base_direction is higher_direction else None
    atr = _atr(ordered[-40:])
    adx = _adx(ordered[-50:])
    bars_per_day = 86_400 // interval_seconds
    momentum_24h = float(latest.close / ordered[-(bars_per_day + 1)].close - 1)
    relative_volume = _relative_volume(ordered, -1)
    reasons: list[str] = []
    if higher_direction is None:
        reasons.append("HIGHER_TIMEFRAME_HISTORY_OR_TREND_NOT_READY")
    if direction is None:
        reasons.append("INTRADAY_AND_HIGHER_TREND_NOT_ALIGNED")
    if not math.isfinite(atr) or atr <= 0:
        reasons.append("INTRADAY_ATR_INVALID")

    structural_stop: float | None = None
    setup_ready = False
    breakout_volume: float | None = None
    if direction is not None and math.isfinite(atr) and atr > 0:
        directional_momentum = momentum_24h * (1 if direction is Side.LONG else -1)
        if variant is IntradayTrendVariant.PULLBACK_RECLAIM_15M:
            if directional_momentum < 0.01:
                reasons.append("MOMENTUM_24H_BELOW_1_PERCENT")
            if adx < 18:
                reasons.append("INTRADAY_ADX_BELOW_18")
            if relative_volume < 0.80:
                reasons.append("INTRADAY_RELATIVE_VOLUME_BELOW_0_8")
            setup_ready = _pullback_reclaim_ready(
                direction,
                previous,
                latest,
                previous_ema20,
                ema20,
                previous_ema80,
            )
            if not setup_ready:
                reasons.append("PULLBACK_RECLAIM_NOT_CONFIRMED")
            structural_stop = _swing_stop(direction, previous, latest, atr, 0.10)
        elif variant in {
            IntradayTrendVariant.BREAKOUT_RETEST_15M,
            IntradayTrendVariant.BREAKOUT_RETEST_30M,
        }:
            lookback = 32 if interval_seconds == 900 else 24
            minimum_momentum = 0.015
            minimum_breakout_volume = 1.10 if interval_seconds == 900 else 1.0
            if directional_momentum < minimum_momentum:
                reasons.append("MOMENTUM_24H_BELOW_1_5_PERCENT")
            if adx < 20:
                reasons.append("INTRADAY_ADX_BELOW_20")
            breakout_volume = _relative_volume(ordered, -2)
            if breakout_volume < minimum_breakout_volume:
                reasons.append("BREAKOUT_RELATIVE_VOLUME_TOO_LOW")
            breakout_rows = ordered[-(lookback + 2) : -2]
            breakout_level = (
                max(float(row.high) for row in breakout_rows)
                if direction is Side.LONG
                else min(float(row.low) for row in breakout_rows)
            )
            buffer_atr = 0.35 if interval_seconds == 900 else 0.40
            setup_ready = _breakout_retest_ready(
                direction,
                previous,
                latest,
                breakout_level,
                atr,
                buffer_atr,
            )
            if not setup_ready:
                reasons.append("BREAKOUT_RETEST_NOT_CONFIRMED")
            structural_stop = _retest_stop(
                direction,
                latest,
                breakout_level,
                atr,
                buffer_atr,
            )
        else:
            if directional_momentum < 0.012:
                reasons.append("MOMENTUM_24H_BELOW_1_2_PERCENT")
            if adx < 18:
                reasons.append("INTRADAY_ADX_BELOW_18")
            if relative_volume < 0.90:
                reasons.append("INTRADAY_RELATIVE_VOLUME_BELOW_0_9")
            setup_ready = _multispeed_reclaim_ready(
                direction,
                previous,
                latest,
                previous_ema20,
                ema20,
                previous_ema80,
            )
            if not setup_ready:
                reasons.append("MULTISPEED_RECLAIM_NOT_CONFIRMED")
            structural_stop = _swing_stop(direction, previous, latest, atr, 0.15)

    return IntradayTrendState(
        latest_open_ts_ms=latest_open_ts_ms,
        interval_seconds=interval_seconds,
        direction=direction,
        atr=atr,
        structural_stop=structural_stop,
        momentum_24h=momentum_24h,
        adx=adx,
        relative_volume=relative_volume,
        reason_codes=tuple(reasons),
        history_count=len(ordered),
        hourly_history_count=len(hourly_candles),
        setup_confirmed=setup_ready,
        breakout_relative_volume=breakout_volume,
    )


def _empty_state(
    latest_open_ts_ms: int | None,
    interval_seconds: int,
    reason: str,
    *,
    history_count: int = 0,
    hourly_history_count: int = 0,
) -> IntradayTrendState:
    return IntradayTrendState(
        latest_open_ts_ms=latest_open_ts_ms,
        interval_seconds=interval_seconds,
        direction=None,
        atr=None,
        structural_stop=None,
        momentum_24h=None,
        adx=None,
        relative_volume=None,
        reason_codes=(reason,),
        history_count=history_count,
        hourly_history_count=hourly_history_count,
    )


def _ema(values: Sequence[float], period: int) -> float:
    selected = values[-max(period * 4, period) :]
    alpha = 2 / (period + 1)
    current = selected[0]
    for value in selected[1:]:
        current = alpha * value + (1 - alpha) * current
    return current


def _atr(rows: Sequence[Candle], period: int = 14) -> float:
    true_ranges = [
        max(
            float(current.high - current.low),
            abs(float(current.high - previous.close)),
            abs(float(current.low - previous.close)),
        )
        for previous, current in zip(rows, rows[1:], strict=False)
    ]
    return fmean(true_ranges[-period:]) if len(true_ranges) >= period else 0.0


def _adx(rows: Sequence[Candle], period: int = 14) -> float:
    if len(rows) < period * 2:
        return 0.0
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for previous, current in zip(rows, rows[1:], strict=False):
        high = float(current.high)
        low = float(current.low)
        previous_close = float(previous.close)
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        high_move = high - float(previous.high)
        low_move = float(previous.low) - low
        plus_dm.append(high_move if high_move > low_move and high_move > 0 else 0.0)
        minus_dm.append(low_move if low_move > high_move and low_move > 0 else 0.0)
    dx_values: list[float] = []
    for end in range(period, len(true_ranges) + 1):
        true_range = sum(true_ranges[end - period : end])
        if true_range <= 0:
            dx_values.append(0.0)
            continue
        plus_di = 100 * sum(plus_dm[end - period : end]) / true_range
        minus_di = 100 * sum(minus_dm[end - period : end]) / true_range
        denominator = plus_di + minus_di
        dx_values.append(100 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
    return fmean(dx_values[-period:]) if dx_values else 0.0


def _hourly_direction(rows: Sequence[Candle]) -> Side | None:
    ordered = tuple(
        sorted(
            (row for row in rows if row.interval_seconds == 3_600),
            key=lambda row: row.open_ts_ms,
        )
    )
    if len(ordered) < 50:
        return None
    recent = ordered[-50:]
    if any(
        current.open_ts_ms - previous.open_ts_ms != 3_600_000
        for previous, current in zip(recent, recent[1:], strict=False)
    ):
        return None
    closes = [float(row.close) for row in ordered]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    latest = closes[-1]
    if ema20 > ema50 and latest >= ema20:
        return Side.LONG
    if ema20 < ema50 and latest <= ema20:
        return Side.SHORT
    return None


def _relative_volume(rows: Sequence[Candle], index: int) -> float:
    resolved_index = len(rows) + index if index < 0 else index
    if resolved_index < 20:
        return 0.0
    prior = rows[resolved_index - 20 : resolved_index]
    average = fmean(float(row.volume) for row in prior)
    return float(rows[resolved_index].volume) / average if average > 0 else 0.0


def _pullback_reclaim_ready(
    direction: Side,
    previous: Candle,
    latest: Candle,
    previous_ema20: float,
    ema20: float,
    previous_ema80: float,
) -> bool:
    if direction is Side.LONG:
        return (
            float(previous.low) <= previous_ema20 * 1.002
            and float(previous.close) >= previous_ema80
            and float(latest.close) > ema20
            and float(latest.close) > float(previous.high)
            and latest.close > latest.open
        )
    return (
        float(previous.high) >= previous_ema20 * 0.998
        and float(previous.close) <= previous_ema80
        and float(latest.close) < ema20
        and float(latest.close) < float(previous.low)
        and latest.close < latest.open
    )


def _breakout_retest_ready(
    direction: Side,
    breakout: Candle,
    retest: Candle,
    level: float,
    atr: float,
    buffer_atr: float,
) -> bool:
    buffer = atr * buffer_atr
    if direction is Side.LONG:
        return (
            float(breakout.close) > level
            and level - buffer <= float(retest.low) <= level + buffer
            and float(retest.close) > level
            and retest.close >= retest.open
        )
    return (
        float(breakout.close) < level
        and level - buffer <= float(retest.high) <= level + buffer
        and float(retest.close) < level
        and retest.close <= retest.open
    )


def _multispeed_reclaim_ready(
    direction: Side,
    previous: Candle,
    latest: Candle,
    previous_ema20: float,
    ema20: float,
    previous_ema80: float,
) -> bool:
    if direction is Side.LONG:
        return (
            float(previous.low) <= previous_ema20
            and float(previous.close) >= previous_ema80
            and float(latest.close) > ema20
            and float(latest.close) > float(previous.high)
        )
    return (
        float(previous.high) >= previous_ema20
        and float(previous.close) <= previous_ema80
        and float(latest.close) < ema20
        and float(latest.close) < float(previous.low)
    )


def _swing_stop(
    direction: Side,
    previous: Candle,
    latest: Candle,
    atr: float,
    buffer_atr: float,
) -> float:
    if direction is Side.LONG:
        return min(float(previous.low), float(latest.low)) - atr * buffer_atr
    return max(float(previous.high), float(latest.high)) + atr * buffer_atr


def _retest_stop(
    direction: Side,
    latest: Candle,
    level: float,
    atr: float,
    buffer_atr: float,
) -> float:
    if direction is Side.LONG:
        return min(float(latest.low), level - atr * buffer_atr)
    return max(float(latest.high), level + atr * buffer_atr)
