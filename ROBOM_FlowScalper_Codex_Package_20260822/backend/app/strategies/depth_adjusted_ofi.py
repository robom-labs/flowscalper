"""호가 깊이로 보정한 OFI 충격이 이어지는 PAPER 순간추세를 평가한다."""

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
class DepthAdjustedOfiContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    directional_depth_adjusted_ofi_robust_z: float
    confirmation_ms: int


class DepthAdjustedOfiStrategy:
    strategy_id = "DEPTH_ADJUSTED_OFI_IMPULSE_V1"

    def evaluate(self, context: DepthAdjustedOfiContext) -> CandidateDecision:
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
        if feature.spread_bps > 10:
            rejections.append("WIDE_SPREAD")
        if feature.depth_adjusted_ofi_3s_bps * direction <= 0:
            rejections.append("DEPTH_ADJUSTED_OFI_NOT_ALIGNED")
        if context.directional_depth_adjusted_ofi_robust_z < 2.0:
            rejections.append("DEPTH_ADJUSTED_OFI_IMPULSE_WEAK")
        if feature.ofi_250ms * direction <= 0:
            rejections.append("SHORT_OFI_NOT_ALIGNED")
        if feature.ofi_3s * direction <= 0:
            rejections.append("MEDIUM_OFI_NOT_ALIGNED")
        if feature.trade_imbalance_1s * direction < 0.15:
            rejections.append("AGGRESSOR_FLOW_NOT_ALIGNED")
        if feature.microprice_minus_mid_bps * direction <= 0:
            rejections.append("MICROPRICE_NOT_ALIGNED")
        if feature.price_response_efficiency < 0.40:
            rejections.append("PRICE_RESPONSE_INEFFICIENT")
        if context.confirmation_ms < 500:
            rejections.append("DEPTH_ADJUSTED_OFI_NOT_PERSISTENT")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "DEPTH_ADJUSTED_OFI_IMPULSE_STRONG",
            "MULTI_WINDOW_OFI_ALIGNED",
            "AGGRESSOR_FLOW_ALIGNED",
            "MICROPRICE_ALIGNED",
            "PRICE_RESPONSE_EFFICIENT",
            "DEPTH_ADJUSTED_OFI_PERSISTENT",
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


def depth_adjusted_ofi_ready(
    side: Side,
    feature: FeatureSnapshot,
    regime: Regime,
    directional_depth_adjusted_ofi_robust_z: float,
) -> bool:
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and regime in {Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN}
        and feature.spread_bps <= 10
        and feature.depth_adjusted_ofi_3s_bps * direction > 0
        and directional_depth_adjusted_ofi_robust_z >= 2.0
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and feature.trade_imbalance_1s * direction >= 0.15
        and feature.microprice_minus_mid_bps * direction > 0
        and feature.price_response_efficiency >= 0.40
    )
