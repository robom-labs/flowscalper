"""유동성 sweep·흡수·범위 재진입 반전 전략을 양방향으로 평가한다."""

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
class LiquiditySweepContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    sweep_extension_noise_units: float
    aggressive_flow_robust_z: float
    price_response_efficiency_quantile: float
    refill_persistence_ms: int
    reentry_confirmation_ms: int
    ofi_flip: bool
    microprice_reclaimed: bool
    range_reentered: bool


class LiquiditySweepStrategy:
    strategy_id = "LSA_REVERSAL_V1"

    def evaluate(self, context: LiquiditySweepContext) -> CandidateDecision:
        rejections: list[str] = []
        if not context.features.data_healthy:
            rejections.append("STALE_OR_DEGRADED_DATA")
        if context.regime in {Regime.SHOCK, Regime.DEGRADED, Regime.WARMUP}:
            rejections.append(f"REGIME_{context.regime.value}")
        if context.features.spread_bps > 12:
            rejections.append("WIDE_SPREAD")
        if not 0.5 <= context.sweep_extension_noise_units <= 2.5:
            rejections.append("SWEEP_EXTENSION_INVALID")
        if context.aggressive_flow_robust_z < 1.8:
            rejections.append("AGGRESSIVE_FLOW_WEAK")
        if context.price_response_efficiency_quantile > 0.30:
            rejections.append("CONTINUATION_NOT_ABSORPTION")
        if context.refill_persistence_ms < 500:
            rejections.append("FLEETING_REFILL")
        if context.reentry_confirmation_ms < 300:
            rejections.append("REENTRY_NOT_PERSISTENT")
        if not context.ofi_flip:
            rejections.append("OFI_NOT_FLIPPED")
        if not context.microprice_reclaimed:
            rejections.append("MICROPRICE_NOT_RECLAIMED")
        if not context.range_reentered:
            rejections.append("RANGE_NOT_REENTERED")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "STRUCTURAL_SWEEP_CONFIRMED",
            "AGGRESSIVE_FLOW_ABSORBED",
            "PERSISTENT_REFILL",
            "OFI_FLIP",
            "MICROPRICE_RECLAIM",
            "RANGE_REENTRY",
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
