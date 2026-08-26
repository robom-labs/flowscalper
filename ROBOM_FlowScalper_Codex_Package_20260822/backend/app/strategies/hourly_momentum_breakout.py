# 완성 1시간 봉의 24시간 모멘텀과 20시간 돌파가 겹칠 때만 PAPER 후보를 만든다.

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class HourlyMomentumState:
    latest_open_ts_ms: int | None
    direction: Side | None
    atr: float | None
    adx: float | None
    relative_volume: float | None
    momentum_24h: float | None
    reason_codes: tuple[str, ...]

    @property
    def signal_ts_ms(self) -> int | None:
        return self.latest_open_ts_ms + 3_600_000 if self.latest_open_ts_ms is not None else None


@dataclass(frozen=True, slots=True)
class HourlyMomentumBreakoutContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    state: HourlyMomentumState
    signal_age_ms: int | None


def _ema(values: Sequence[float], period: int) -> float:
    alpha = 2 / (period + 1)
    current = values[0]
    for value in values:
        current = alpha * value + (1 - alpha) * current
    return current


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
        tr = sum(true_ranges[end - period : end])
        if tr <= 0:
            dx_values.append(0.0)
            continue
        plus_di = 100 * sum(plus_dm[end - period : end]) / tr
        minus_di = 100 * sum(minus_dm[end - period : end]) / tr
        denominator = plus_di + minus_di
        dx_values.append(100 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
    return fmean(dx_values[-period:]) if dx_values else 0.0


def hourly_momentum_state(candles: Sequence[Candle]) -> HourlyMomentumState:
    ordered = tuple(
        sorted(
            (row for row in candles if row.interval_seconds == 3_600),
            key=lambda row: row.open_ts_ms,
        )
    )
    if len(ordered) < 200:
        return HourlyMomentumState(
            latest_open_ts_ms=ordered[-1].open_ts_ms if ordered else None,
            direction=None,
            atr=None,
            adx=None,
            relative_volume=None,
            momentum_24h=None,
            reason_codes=("HOURLY_HISTORY_WARMUP",),
        )
    latest = ordered[-1]
    closes = [float(row.close) for row in ordered]
    ema20 = _ema(closes[-80:], 20)
    ema50 = _ema(closes[-120:], 50)
    ema80 = _ema(closes[-160:], 80)
    ema200 = _ema(closes[-200:], 200)
    prior_ema80 = _ema(closes[-164:-4], 80)
    direction = (
        Side.LONG
        if ema20 > ema50 and ema80 > ema200 and ema80 > prior_ema80
        else Side.SHORT
        if ema20 < ema50 and ema80 < ema200 and ema80 < prior_ema80
        else None
    )
    true_ranges = [
        max(
            float(current.high - current.low),
            abs(float(current.high - previous.close)),
            abs(float(current.low - previous.close)),
        )
        for previous, current in zip(ordered[-15:-1], ordered[-14:], strict=True)
    ]
    atr = fmean(true_ranges)
    prior_volume = fmean(float(row.volume) for row in ordered[-21:-1])
    relative_volume = float(latest.volume) / prior_volume if prior_volume > 0 else 0.0
    momentum = float(latest.close / ordered[-25].close - 1)
    adx = _adx(ordered[-40:])
    reasons: list[str] = []
    if direction is None:
        reasons.append("HOURLY_TREND_NOT_ALIGNED")
    else:
        breakout = (
            float(latest.close) > max(float(row.high) for row in ordered[-21:-1])
            if direction is Side.LONG
            else float(latest.close) < min(float(row.low) for row in ordered[-21:-1])
        )
        if momentum * (1 if direction is Side.LONG else -1) < 0.02:
            reasons.append("MOMENTUM_24H_BELOW_2_PERCENT")
        if not breakout:
            reasons.append("HOURLY_DONCHIAN_20_NOT_BROKEN")
        if adx < 20:
            reasons.append("HOURLY_ADX_BELOW_20")
        if relative_volume < 1.1:
            reasons.append("HOURLY_RELATIVE_VOLUME_BELOW_1_1")
    if not math.isfinite(atr) or atr <= 0:
        reasons.append("HOURLY_ATR_INVALID")
    return HourlyMomentumState(
        latest_open_ts_ms=latest.open_ts_ms,
        direction=direction,
        atr=atr,
        adx=adx,
        relative_volume=relative_volume,
        momentum_24h=momentum,
        reason_codes=tuple(reasons),
    )


class HourlyMomentumBreakoutStrategy:
    strategy_id = "HOURLY_MOMENTUM_BREAKOUT_V1"

    def evaluate(self, context: HourlyMomentumBreakoutContext) -> CandidateDecision:
        rejections = list(context.state.reason_codes)
        if not context.features.data_healthy:
            rejections.append("STALE_OR_DEGRADED_DATA")
        if context.regime in {Regime.SHOCK, Regime.DEGRADED, Regime.WARMUP}:
            rejections.append(f"REGIME_{context.regime.value}")
        if context.features.spread_bps > 12:
            rejections.append("WIDE_SPREAD")
        if context.state.direction is not context.side:
            rejections.append("HOURLY_DIRECTION_MISMATCH")
        if context.signal_age_ms is None:
            rejections.append("HOURLY_SIGNAL_NOT_READY")
        elif not 0 <= context.signal_age_ms <= 5_000:
            rejections.append("NO_NEW_COMPLETED_HOUR_SIGNAL")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "COMPLETED_HOURLY_TREND_ALIGNED",
            "MOMENTUM_24H_AT_LEAST_2_PERCENT",
            "HOURLY_DONCHIAN_20_BREAKOUT",
            "HOURLY_ADX_CONFIRMED",
            "HOURLY_RELATIVE_VOLUME_CONFIRMED",
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
