"""지속 OFI와 약한 역방향 눌림을 이용한 PAPER 추세 지속 전략을 정의한다."""

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
class OfiPullbackContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    multi_window_ofi_aligned: bool
    aggressive_trade_aligned: bool
    microprice_aligned: bool
    price_efficiency_percentile: float
    pullback_seconds: float
    pullback_retrace_fraction: float
    counterflow_price_impact_weak: bool
    original_flow_reaccelerated: bool
    confirmation_ms: int


class OfiPullbackStrategy:
    strategy_id = "OFI_CONTINUATION_PULLBACK_V1"

    def evaluate(self, context: OfiPullbackContext) -> CandidateDecision:
        rejections: list[str] = []
        expected_regime = Regime.TREND_UP if context.side is Side.LONG else Regime.TREND_DOWN
        if not context.features.data_healthy:
            rejections.append("STALE_OR_DEGRADED_DATA")
        if context.regime is not expected_regime:
            rejections.append("REGIME_DIRECTION_MISMATCH")
        if context.features.spread_bps > 12:
            rejections.append("WIDE_SPREAD")
        if not context.multi_window_ofi_aligned:
            rejections.append("MULTI_WINDOW_OFI_NOT_ALIGNED")
        if not context.aggressive_trade_aligned:
            rejections.append("AGGRESSIVE_TRADES_NOT_ALIGNED")
        if not context.microprice_aligned:
            rejections.append("MICROPRICE_NOT_ALIGNED")
        if context.price_efficiency_percentile < 0.50:
            rejections.append("PRICE_EFFICIENCY_WEAK")
        if not 1 <= context.pullback_seconds <= 15:
            rejections.append("PULLBACK_DURATION_INVALID")
        if not 0.10 <= context.pullback_retrace_fraction <= 0.60:
            rejections.append("PULLBACK_RETRACE_INVALID")
        if not context.counterflow_price_impact_weak:
            rejections.append("COUNTERFLOW_PRICE_IMPACT_STRONG")
        if not context.original_flow_reaccelerated:
            rejections.append("ORIGINAL_FLOW_NOT_REACCELERATED")
        if context.confirmation_ms < 300:
            rejections.append("REACCELERATION_NOT_PERSISTENT")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "MULTI_WINDOW_OFI_ALIGNED",
            "AGGRESSIVE_TRADES_ALIGNED",
            "MICROPRICE_ALIGNED",
            "PRICE_EFFICIENCY_HEALTHY",
            "WEAK_COUNTERFLOW_PULLBACK",
            "ORIGINAL_FLOW_REACCELERATED",
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
