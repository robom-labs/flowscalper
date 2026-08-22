"""전략 A/B 양방향, 비용·구조 게이트와 결정적 reason code를 검증한다."""

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.domain.models import Side, Venue
from backend.app.features import FeatureSnapshot
from backend.app.regime import Regime
from backend.app.strategies import (
    CompressionBreakoutContext,
    CompressionBreakoutStrategy,
    LiquiditySweepContext,
    LiquiditySweepStrategy,
    PlanInputs,
)
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.statistics import robust_z, rolling_percentile


def features(*, healthy: bool = True, spread_bps: float = 2.0) -> FeatureSnapshot:
    return FeatureSnapshot(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        ts_ms=1_000,
        sample_count=100,
        warmup_seconds=60,
        data_healthy=healthy,
        lag_ms=20,
        mid=100,
        spread_bps=spread_bps,
        depth_bid_10=100_000,
        depth_ask_10=100_000,
        imbalance_top1=0.2,
        imbalance_top5=0.2,
        imbalance_top10=0.2,
        microprice=100.01,
        microprice_minus_mid_bps=1,
        ofi_250ms=1,
        ofi_1s=2,
        ofi_3s=3,
        ofi_10s=4,
        trade_imbalance_1s=0.4,
        trade_imbalance_3s=0.3,
        trade_imbalance_10s=0.2,
        signed_notional_3s=10_000,
        refill_ratio=0.7,
        cancel_ratio=0.3,
        price_response_efficiency=0.1,
        realized_volatility_30s=0.0001,
        realized_volatility_120s=0.0002,
        compression_ratio=0.5,
        efficiency_ratio_30s=0.7,
        micro_vwap_10s=100.01,
    )


def plan(side: Side, *, target: str | None = None, stop: str | None = None) -> PlanInputs:
    if side is Side.LONG:
        default_stop, default_target = "99", "102"
    else:
        default_stop, default_target = "101", "98"
    return PlanInputs(
        entry=Decimal("100"),
        structural_stop=Decimal(stop or default_stop) if stop != "NONE" else None,
        target=Decimal(target or default_target) if target != "NONE" else None,
        expected_total_cost_bps=Decimal("13"),
    )


def sweep_context(side: Side) -> LiquiditySweepContext:
    return LiquiditySweepContext(
        side=side,
        features=features(),
        regime=Regime.RANGE,
        plan=plan(side),
        sweep_extension_noise_units=1.0,
        aggressive_flow_robust_z=2.1,
        price_response_efficiency_quantile=0.2,
        refill_persistence_ms=700,
        reentry_confirmation_ms=500,
        ofi_flip=True,
        microprice_reclaimed=True,
        range_reentered=True,
    )


def breakout_context(side: Side) -> CompressionBreakoutContext:
    return CompressionBreakoutContext(
        side=side,
        features=features(),
        regime=Regime.TREND_UP if side is Side.LONG else Regime.TREND_DOWN,
        plan=plan(side),
        compression_quantile=0.1,
        breakout_confirmed=True,
        initial_impulse_extended=False,
        pullback_seconds=3,
        pullback_retrace_fraction=0.4,
        counterflow_price_impact_weak=True,
        refill_recovered=True,
        ofi_reaccelerated=True,
        microprice_aligned=True,
        confirmation_ms=500,
    )


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_strategy_a_qualifies_symmetric_structural_plans(side: Side) -> None:
    strategy = LiquiditySweepStrategy()
    first = strategy.evaluate(sweep_context(side))
    second = strategy.evaluate(sweep_context(side))
    assert first == second
    assert first.status is CandidateStatus.QUALIFIED
    assert first.initial_stop is not None and first.take_profit is not None
    assert first.net_reward_risk is not None and first.net_reward_risk >= Decimal("1.20")
    assert first.tp_probability is None
    assert first.korean_explanation("BTCUSDT")[0].endswith("후보")


def test_strategy_a_rejects_continuation_fleeting_refill_and_stale() -> None:
    context = replace(
        sweep_context(Side.LONG),
        features=features(healthy=False),
        price_response_efficiency_quantile=0.8,
        refill_persistence_ms=100,
    )
    decision = LiquiditySweepStrategy().evaluate(context)
    assert decision.status is CandidateStatus.REJECTED
    assert decision.rejection_codes == (
        "STALE_OR_DEGRADED_DATA",
        "CONTINUATION_NOT_ABSORPTION",
        "FLEETING_REFILL",
    )


def test_strategy_a_requires_stop_and_cost_viable_target() -> None:
    no_stop = replace(sweep_context(Side.LONG), plan=plan(Side.LONG, stop="NONE"))
    assert LiquiditySweepStrategy().evaluate(no_stop).rejection_codes == ("NO_STRUCTURAL_STOP",)

    expensive = replace(
        sweep_context(Side.LONG),
        plan=PlanInputs(
            entry=Decimal("100"),
            structural_stop=Decimal("99"),
            target=Decimal("100.2"),
            expected_total_cost_bps=Decimal("13"),
        ),
    )
    assert "COST_FRACTION_TOO_HIGH" in LiquiditySweepStrategy().evaluate(expensive).rejection_codes


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_strategy_b_qualifies_pullback_reacceleration_both_sides(side: Side) -> None:
    decision = CompressionBreakoutStrategy().evaluate(breakout_context(side))
    assert decision.status is CandidateStatus.QUALIFIED
    assert "OFI_REACCELERATION" in decision.reason_codes


def test_strategy_b_rejects_direction_retrace_and_shock() -> None:
    context = replace(
        breakout_context(Side.LONG),
        regime=Regime.SHOCK,
        pullback_retrace_fraction=0.9,
        counterflow_price_impact_weak=False,
    )
    decision = CompressionBreakoutStrategy().evaluate(context)
    assert decision.status is CandidateStatus.REJECTED
    assert decision.rejection_codes == (
        "REGIME_SHOCK",
        "PULLBACK_RETRACE_INVALID",
        "COUNTERFLOW_EFFICIENT",
    )


def test_robust_thresholds_only_use_supplied_history_prefix() -> None:
    prefix = [1.0, 1.1, 0.9, 1.05]
    z_before_future = robust_z(prefix, 2.0)
    percentile_before_future = rolling_percentile(prefix, 2.0)
    future = [1000.0, -1000.0]
    assert robust_z(prefix, 2.0) == z_before_future
    assert rolling_percentile(prefix, 2.0) == percentile_before_future
    assert robust_z(prefix + future, 2.0) != z_before_future
