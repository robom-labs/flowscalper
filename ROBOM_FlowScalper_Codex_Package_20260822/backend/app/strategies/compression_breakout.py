"""압축·돌파·되돌림·재가속 추세 전략을 양방향으로 평가한다."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.domain.models import Side
from backend.app.features import FeatureSnapshot
from backend.app.regime import Regime
from backend.app.strategies.base import (
    CandidateDecision,
    CandidateStatus,
    PlanInputs,
    costed_plan,
)


@dataclass(frozen=True, slots=True)
class CompressionBreakoutContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    compression_quantile: float
    breakout_confirmed: bool
    initial_impulse_extended: bool
    pullback_seconds: float
    pullback_retrace_fraction: float
    counterflow_price_impact_weak: bool
    refill_recovered: bool
    ofi_reaccelerated: bool
    microprice_aligned: bool
    confirmation_ms: int


class CompressionBreakoutStrategy:
    strategy_id = "CBR_CONTINUATION_V1"

    def evaluate(self, context: CompressionBreakoutContext) -> CandidateDecision:
        rejections: list[str] = []
        expected_regime = Regime.TREND_UP if context.side is Side.LONG else Regime.TREND_DOWN
        if not context.features.data_healthy:
            rejections.append("STALE_OR_DEGRADED_DATA")
        if context.regime in {Regime.SHOCK, Regime.DEGRADED, Regime.WARMUP}:
            rejections.append(f"REGIME_{context.regime.value}")
        elif context.regime is not expected_regime:
            rejections.append("REGIME_DIRECTION_MISMATCH")
        if context.features.spread_bps > 12:
            rejections.append("WIDE_SPREAD")
        if context.compression_quantile > 0.20:
            rejections.append("NOT_COMPRESSED")
        if not context.breakout_confirmed:
            rejections.append("BREAKOUT_NOT_CONFIRMED")
        if context.initial_impulse_extended:
            rejections.append("INITIAL_IMPULSE_EXTENDED")
        if not 1 <= context.pullback_seconds <= 10:
            rejections.append("PULLBACK_DURATION_INVALID")
        if not 0.20 <= context.pullback_retrace_fraction <= 0.60:
            rejections.append("PULLBACK_RETRACE_INVALID")
        if not context.counterflow_price_impact_weak:
            rejections.append("COUNTERFLOW_EFFICIENT")
        if not context.refill_recovered:
            rejections.append("LIQUIDITY_NOT_RECOVERED")
        if not context.ofi_reaccelerated:
            rejections.append("OFI_NOT_REACCELERATED")
        if not context.microprice_aligned:
            rejections.append("MICROPRICE_NOT_ALIGNED")
        if context.confirmation_ms < 300:
            rejections.append("REACCELERATION_NOT_PERSISTENT")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "COMPRESSION_CONFIRMED",
            "BREAKOUT_CONFIRMED",
            "PULLBACK_ACCEPTED",
            "COUNTERFLOW_IMPACT_FAILED",
            "LIQUIDITY_RECOVERED",
            "OFI_REACCELERATION",
            "MICROPRICE_ALIGNED",
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
            structural_exit=context.plan.structural_exit,
        )
