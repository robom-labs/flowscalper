"""깊이보정 OFI와 직전 가격수익률이 동행하는 PAPER 순간추세를 평가한다."""

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
class OfiReturnConfluenceContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    directional_depth_adjusted_ofi_robust_z: float
    trailing_return_3s_bps: float | None
    confirmation_ms: int


class OfiReturnConfluenceStrategy:
    strategy_id = "OFI_RETURN_CONFLUENCE_V1"

    def evaluate(self, context: OfiReturnConfluenceContext) -> CandidateDecision:
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
        if feature.depth_adjusted_ofi_3s_bps * direction <= 0:
            rejections.append("DEPTH_ADJUSTED_OFI_NOT_ALIGNED")
        if context.directional_depth_adjusted_ofi_robust_z < 1.5:
            rejections.append("DEPTH_ADJUSTED_OFI_IMPULSE_WEAK")
        if feature.ofi_250ms * direction <= 0:
            rejections.append("SHORT_OFI_NOT_ALIGNED")
        if feature.ofi_3s * direction <= 0:
            rejections.append("MEDIUM_OFI_NOT_ALIGNED")
        if context.trailing_return_3s_bps is None:
            rejections.append("RETURN_HISTORY_MISSING")
        elif context.trailing_return_3s_bps * direction < 2.0:
            rejections.append("TRAILING_RETURN_NOT_ALIGNED")
        if feature.microprice_minus_mid_bps * direction < 0.20:
            rejections.append("MICROPRICE_NOT_ALIGNED")
        if feature.price_response_efficiency < 0.30:
            rejections.append("PRICE_RESPONSE_INEFFICIENT")
        if context.confirmation_ms < 1_000:
            rejections.append("OFI_RETURN_CONFLUENCE_NOT_PERSISTENT")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "DEPTH_ADJUSTED_OFI_IMPULSE_STRONG",
            "MULTI_WINDOW_OFI_ALIGNED",
            "TRAILING_RETURN_ALIGNED",
            "MICROPRICE_ALIGNED",
            "PRICE_RESPONSE_EFFICIENT",
            "OFI_RETURN_CONFLUENCE_PERSISTENT",
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


def ofi_return_confluence_ready(
    side: Side,
    feature: FeatureSnapshot,
    regime: Regime,
    directional_depth_adjusted_ofi_robust_z: float,
    trailing_return_3s_bps: float | None,
) -> bool:
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and regime in {Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN}
        and feature.spread_bps <= 8
        and feature.depth_adjusted_ofi_3s_bps * direction > 0
        and directional_depth_adjusted_ofi_robust_z >= 1.5
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and trailing_return_3s_bps is not None
        and trailing_return_3s_bps * direction >= 2.0
        and feature.microprice_minus_mid_bps * direction >= 0.20
        and feature.price_response_efficiency >= 0.30
    )
