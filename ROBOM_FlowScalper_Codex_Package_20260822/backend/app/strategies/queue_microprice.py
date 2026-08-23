"""호가 불균형과 microprice 정렬이 지속된 순간추세 PAPER 전략을 평가한다."""

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
class QueueMicropriceContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    confirmation_ms: int


class QueueMicropriceStrategy:
    strategy_id = "QUEUE_MICROPRICE_MOMENTUM_V1"

    def evaluate(self, context: QueueMicropriceContext) -> CandidateDecision:
        feature = context.features
        direction = 1 if context.side is Side.LONG else -1
        rejections: list[str] = []
        if not feature.data_healthy:
            rejections.append("STALE_OR_DEGRADED_DATA")
        if context.regime is Regime.WARMUP:
            rejections.append("REGIME_WARMUP")
        elif context.regime is Regime.DEGRADED:
            rejections.append("REGIME_DEGRADED")
        elif context.regime is Regime.SHOCK:
            rejections.append("REGIME_SHOCK")
        if feature.spread_bps > 8:
            rejections.append("WIDE_SPREAD")
        if feature.imbalance_top5 * direction < 0.18:
            rejections.append("TOP5_IMBALANCE_WEAK")
        if feature.imbalance_top10 * direction < 0.12:
            rejections.append("TOP10_IMBALANCE_WEAK")
        if feature.ofi_250ms * direction <= 0:
            rejections.append("SHORT_OFI_NOT_ALIGNED")
        if feature.ofi_3s * direction <= 0:
            rejections.append("MEDIUM_OFI_NOT_ALIGNED")
        if feature.trade_imbalance_1s * direction < 0.15:
            rejections.append("AGGRESSOR_FLOW_NOT_ALIGNED")
        minimum_displacement = max(0.25, feature.spread_bps * 0.10)
        if feature.microprice_minus_mid_bps * direction < minimum_displacement:
            rejections.append("MICROPRICE_NOT_DISPLACED")
        if context.confirmation_ms < 500:
            rejections.append("QUEUE_ALIGNMENT_NOT_PERSISTENT")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "TOP5_IMBALANCE_ALIGNED",
            "TOP10_IMBALANCE_ALIGNED",
            "MULTI_WINDOW_OFI_ALIGNED",
            "AGGRESSOR_FLOW_ALIGNED",
            "MICROPRICE_DISPLACED",
            "QUEUE_ALIGNMENT_PERSISTENT",
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


def queue_alignment_ready(
    side: Side,
    feature: FeatureSnapshot,
    regime: Regime,
) -> bool:
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and regime in {Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN}
        and feature.spread_bps <= 8
        and feature.imbalance_top5 * direction >= 0.18
        and feature.imbalance_top10 * direction >= 0.12
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and feature.trade_imbalance_1s * direction >= 0.15
        and feature.microprice_minus_mid_bps * direction
        >= max(0.25, feature.spread_bps * 0.10)
    )
