"""micro-VWAP 과도이탈과 주문흐름 소진을 이용한 PAPER 평균복귀 전략을 정의한다."""

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
class VwapExhaustionContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    vwap_deviation_robust_z: float
    excursion_direction_valid: bool
    aggressive_flow_robust_z: float
    price_progress_stalled: bool
    opposite_depth_refilled: bool
    ofi_reversed: bool
    microprice_reversed: bool
    structure_reentered: bool
    confirmation_ms: int
    vwap_deviation_bps: float | None = None
    price_progress_percentile: float | None = None


class VwapExhaustionStrategy:
    strategy_id = "VWAP_EXHAUSTION_REVERSION_V1"

    def evaluate(self, context: VwapExhaustionContext) -> CandidateDecision:
        rejections: list[str] = []
        if not context.features.data_healthy:
            rejections.append("STALE_OR_DEGRADED_DATA")
        if context.regime is not Regime.RANGE:
            rejections.append("REGIME_NOT_RANGE")
        if context.features.spread_bps > 12:
            rejections.append("WIDE_SPREAD")
        if context.vwap_deviation_robust_z < 2.0:
            rejections.append("VWAP_DEVIATION_NOT_EXTREME")
        if not context.excursion_direction_valid:
            rejections.append("EXCURSION_DIRECTION_MISMATCH")
        if context.aggressive_flow_robust_z < 1.5:
            rejections.append("AGGRESSIVE_FLOW_WEAK")
        if not context.price_progress_stalled:
            rejections.append("PRICE_PROGRESS_NOT_STALLED")
        if not context.opposite_depth_refilled:
            rejections.append("OPPOSITE_DEPTH_NOT_REFILLED")
        if not context.ofi_reversed:
            rejections.append("OFI_NOT_REVERSED")
        if not context.microprice_reversed:
            rejections.append("MICROPRICE_NOT_REVERSED")
        if not context.structure_reentered or context.confirmation_ms < 300:
            rejections.append("STRUCTURE_REENTRY_NOT_CONFIRMED")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "VWAP_DEVIATION_EXTREME",
            "AGGRESSIVE_FLOW_EXHAUSTED",
            "PRICE_PROGRESS_STALLED",
            "OPPOSITE_DEPTH_REFILLED",
            "OFI_REVERSED",
            "MICROPRICE_REVERSED",
            "STRUCTURE_REENTERED",
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
