"""공격 체결 흐름과 가격 반응이 지속된 추세 PAPER 전략을 평가한다."""

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
class AggressorFlowContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    aggressive_signed_notional_robust_z: float
    confirmation_ms: int


class AggressorFlowStrategy:
    strategy_id = "AGGRESSOR_FLOW_CONTINUATION_V1"

    def evaluate(self, context: AggressorFlowContext) -> CandidateDecision:
        feature = context.features
        direction = 1 if context.side is Side.LONG else -1
        expected_regime = Regime.TREND_UP if context.side is Side.LONG else Regime.TREND_DOWN
        rejections: list[str] = []
        if not feature.data_healthy:
            rejections.append("STALE_OR_DEGRADED_DATA")
        if context.regime is not expected_regime:
            rejections.append("REGIME_DIRECTION_MISMATCH")
        if feature.spread_bps > 10:
            rejections.append("WIDE_SPREAD")
        if context.aggressive_signed_notional_robust_z < 1.8:
            rejections.append("AGGRESSIVE_NOTIONAL_WEAK")
        if feature.trade_imbalance_3s * direction < 0.25:
            rejections.append("SHORT_FLOW_NOT_ALIGNED")
        if feature.trade_imbalance_10s * direction < 0.10:
            rejections.append("LONG_FLOW_NOT_ALIGNED")
        if feature.ofi_3s * direction <= 0:
            rejections.append("OFI_NOT_ALIGNED")
        if feature.price_response_efficiency < 0.55:
            rejections.append("PRICE_RESPONSE_INEFFICIENT")
        if (feature.microprice - feature.mid) * direction <= 0:
            rejections.append("MICROPRICE_NOT_ALIGNED")
        if context.confirmation_ms < 500:
            rejections.append("FLOW_ALIGNMENT_NOT_PERSISTENT")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "AGGRESSIVE_NOTIONAL_STRONG",
            "MULTI_WINDOW_FLOW_ALIGNED",
            "OFI_ALIGNED",
            "PRICE_RESPONSE_EFFICIENT",
            "MICROPRICE_ALIGNED",
            "FLOW_ALIGNMENT_PERSISTENT",
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


def aggressor_alignment_ready(
    side: Side,
    feature: FeatureSnapshot,
    regime: Regime,
    aggressive_signed_notional_robust_z: float,
) -> bool:
    direction = 1 if side is Side.LONG else -1
    expected_regime = Regime.TREND_UP if side is Side.LONG else Regime.TREND_DOWN
    return (
        feature.data_healthy
        and regime is expected_regime
        and feature.spread_bps <= 10
        and aggressive_signed_notional_robust_z >= 1.8
        and feature.trade_imbalance_3s * direction >= 0.25
        and feature.trade_imbalance_10s * direction >= 0.10
        and feature.ofi_3s * direction > 0
        and feature.price_response_efficiency >= 0.55
        and (feature.microprice - feature.mid) * direction > 0
    )
