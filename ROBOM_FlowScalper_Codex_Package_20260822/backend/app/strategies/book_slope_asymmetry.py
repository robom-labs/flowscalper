"""10단계 호가 기울기 비대칭이 지속되는 PAPER 순간추세를 평가한다."""

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
class BookSlopeAsymmetryContext:
    side: Side
    features: FeatureSnapshot
    regime: Regime
    plan: PlanInputs
    bid_slope_percentile: float
    ask_slope_percentile: float
    history_sample_count: int
    confirmation_ms: int


class BookSlopeAsymmetryStrategy:
    strategy_id = "BOOK_SLOPE_ASYMMETRY_V1"

    def evaluate(self, context: BookSlopeAsymmetryContext) -> CandidateDecision:
        feature = context.features
        direction = 1 if context.side is Side.LONG else -1
        support_slope = (
            feature.bid_book_slope_10
            if context.side is Side.LONG
            else feature.ask_book_slope_10
        )
        opposing_slope = (
            feature.ask_book_slope_10
            if context.side is Side.LONG
            else feature.bid_book_slope_10
        )
        support_percentile = (
            context.bid_slope_percentile
            if context.side is Side.LONG
            else context.ask_slope_percentile
        )
        opposing_percentile = (
            context.ask_slope_percentile
            if context.side is Side.LONG
            else context.bid_slope_percentile
        )
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
        if context.history_sample_count < 32:
            rejections.append("BOOK_SLOPE_HISTORY_SHORT")
        if opposing_slope <= 0 or opposing_percentile > 0.15:
            rejections.append("OPPOSING_BOOK_NOT_THIN")
        if support_slope <= 0 or support_percentile < 0.50:
            rejections.append("SUPPORTING_BOOK_NOT_FIRM")
        if opposing_slope <= 0 or support_slope / opposing_slope < 1.50:
            rejections.append("BOOK_SLOPE_ASYMMETRY_WEAK")
        if feature.ofi_250ms * direction <= 0:
            rejections.append("SHORT_OFI_NOT_ALIGNED")
        if feature.ofi_3s * direction <= 0:
            rejections.append("MEDIUM_OFI_NOT_ALIGNED")
        if feature.trade_imbalance_1s * direction < 0.10:
            rejections.append("AGGRESSOR_FLOW_NOT_ALIGNED")
        if feature.microprice_minus_mid_bps * direction < 0.15:
            rejections.append("MICROPRICE_NOT_ALIGNED")
        if feature.price_response_efficiency < 0.25:
            rejections.append("PRICE_RESPONSE_INEFFICIENT")
        if context.confirmation_ms < 1_000:
            rejections.append("BOOK_SLOPE_ASYMMETRY_NOT_PERSISTENT")
        plan, plan_rejections = costed_plan(context.side, context.plan)
        rejections.extend(plan_rejections)
        reasons = (
            "OPPOSING_BOOK_THIN",
            "SUPPORTING_BOOK_FIRM",
            "BOOK_SLOPE_ASYMMETRY_STRONG",
            "MULTI_WINDOW_OFI_ALIGNED",
            "AGGRESSOR_FLOW_ALIGNED",
            "MICROPRICE_ALIGNED",
            "PRICE_RESPONSE_EFFICIENT",
            "BOOK_SLOPE_ASYMMETRY_PERSISTENT",
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


def book_slope_asymmetry_ready(
    side: Side,
    feature: FeatureSnapshot,
    regime: Regime,
    bid_slope_percentile: float,
    ask_slope_percentile: float,
    history_sample_count: int,
) -> bool:
    support_slope = (
        feature.bid_book_slope_10 if side is Side.LONG else feature.ask_book_slope_10
    )
    opposing_slope = (
        feature.ask_book_slope_10 if side is Side.LONG else feature.bid_book_slope_10
    )
    support_percentile = (
        bid_slope_percentile if side is Side.LONG else ask_slope_percentile
    )
    opposing_percentile = (
        ask_slope_percentile if side is Side.LONG else bid_slope_percentile
    )
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and regime in {Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN}
        and feature.spread_bps <= 8
        and history_sample_count >= 32
        and opposing_slope > 0
        and opposing_percentile <= 0.15
        and support_slope > 0
        and support_percentile >= 0.50
        and support_slope / opposing_slope >= 1.50
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and feature.trade_imbalance_1s * direction >= 0.10
        and feature.microprice_minus_mid_bps * direction >= 0.15
        and feature.price_response_efficiency >= 0.25
    )
