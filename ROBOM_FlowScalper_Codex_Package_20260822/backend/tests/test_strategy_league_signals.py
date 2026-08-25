"""전략 리그 E~J의 대칭 조건과 실제 event-time 지속성을 검증한다."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

import backend.app.strategies.runtime_evaluator as runtime_evaluator
from backend.app.domain.models import Side
from backend.app.regime import Regime
from backend.app.strategies import (
    AggressorFlowContext,
    AggressorFlowStrategy,
    BookSlopeAsymmetryContext,
    BookSlopeAsymmetryStrategy,
    DepthAdjustedOfiContext,
    DepthAdjustedOfiStrategy,
    MultilevelMicropriceContext,
    MultilevelMicropriceStrategy,
    OfiReturnConfluenceContext,
    OfiReturnConfluenceStrategy,
    QueueMicropriceContext,
    QueueMicropriceStrategy,
)
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.registry import StrategyMode, StrategyRegistry
from backend.app.strategies.runtime_evaluator import (
    StrategySignalEvaluator,
    _trailing_return_bps,
)
from backend.tests.test_strategies import features, plan


def aligned_features(side: Side, *, ts_ms: int = 1_000, signed: float = 1_000.0):
    direction = 1 if side is Side.LONG else -1
    return replace(
        features(),
        ts_ms=ts_ms,
        imbalance_top5=0.25 * direction,
        imbalance_top10=0.20 * direction,
        ofi_250ms=2.0 * direction,
        ofi_3s=3.0 * direction,
        trade_imbalance_1s=0.30 * direction,
        trade_imbalance_3s=0.40 * direction,
        trade_imbalance_10s=0.20 * direction,
        signed_notional_3s=abs(signed) * direction,
        price_response_efficiency=0.70,
        microprice=100.02 if side is Side.LONG else 99.98,
        microprice_minus_mid_bps=2.0 * direction,
        micro_vwap_10s=100.01 if side is Side.LONG else 99.99,
        multi_level_microprice_10=100.03 if side is Side.LONG else 99.97,
        multi_level_microprice_10_minus_mid_bps=3.0 * direction,
        depth_adjusted_ofi_3s_bps=2.5 * direction,
        bid_book_slope_10=300.0 if side is Side.LONG else 50.0,
        ask_book_slope_10=50.0 if side is Side.LONG else 300.0,
    )


def only_strategy(strategy_id: str) -> StrategyRegistry:
    registry = StrategyRegistry()
    for current in registry.strategy_ids:
        registry.configure(
            current,
            mode=StrategyMode.SHADOW if current == strategy_id else StrategyMode.OFF,
            long_enabled=True,
            short_enabled=True,
        )
    return registry


def decision_for(rows, strategy_id: str, side: Side):
    return next(
        row.decision
        for row in rows
        if row.decision.strategy_id == strategy_id and row.decision.side is side
    )


def test_runtime_reuses_four_side_and_exit_style_plans(monkeypatch) -> None:
    calls: list[tuple[Side, object]] = []
    original = runtime_evaluator._plan

    def counted_plan(snapshot, side, tick_size, *, exit_style):
        calls.append((side, exit_style))
        return original(snapshot, side, tick_size, exit_style=exit_style)

    monkeypatch.setattr(runtime_evaluator, "_plan", counted_plan)
    rows = StrategySignalEvaluator().evaluate(
        StrategyRegistry(),
        aligned_features(Side.LONG),
        Regime.RANGE,
    )

    assert len(rows) == 20
    assert len(calls) == 4
    assert len(set(calls)) == 4


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_queue_microprice_strategy_qualifies_both_directions(side: Side) -> None:
    decision = QueueMicropriceStrategy().evaluate(
        QueueMicropriceContext(
            side=side,
            features=aligned_features(side),
            regime=Regime.RANGE,
            plan=plan(side),
            confirmation_ms=500,
        )
    )
    assert decision.status is CandidateStatus.QUALIFIED
    assert decision.rejection_codes == ()


@pytest.mark.parametrize(
    ("field_name", "value", "reason"),
    [
        ("data_healthy", False, "STALE_OR_DEGRADED_DATA"),
        ("spread_bps", 8.01, "WIDE_SPREAD"),
        ("imbalance_top5", 0.17, "TOP5_IMBALANCE_WEAK"),
        ("imbalance_top10", 0.11, "TOP10_IMBALANCE_WEAK"),
        ("ofi_250ms", 0.0, "SHORT_OFI_NOT_ALIGNED"),
        ("ofi_3s", 0.0, "MEDIUM_OFI_NOT_ALIGNED"),
        ("trade_imbalance_1s", 0.14, "AGGRESSOR_FLOW_NOT_ALIGNED"),
        ("microprice_minus_mid_bps", 0.20, "MICROPRICE_NOT_DISPLACED"),
    ],
)
def test_queue_microprice_core_rejections(field_name: str, value: object, reason: str) -> None:
    feature = replace(aligned_features(Side.LONG), **{field_name: value})
    decision = QueueMicropriceStrategy().evaluate(
        QueueMicropriceContext(
            Side.LONG,
            feature,
            Regime.RANGE,
            plan(Side.LONG),
            500,
        )
    )
    assert reason in decision.rejection_codes


@pytest.mark.parametrize(
    ("regime", "reason"),
    [
        (Regime.WARMUP, "REGIME_WARMUP"),
        (Regime.DEGRADED, "REGIME_DEGRADED"),
        (Regime.SHOCK, "REGIME_SHOCK"),
    ],
)
def test_queue_microprice_rejects_forbidden_regimes(regime: Regime, reason: str) -> None:
    decision = QueueMicropriceStrategy().evaluate(
        QueueMicropriceContext(
            Side.LONG,
            aligned_features(Side.LONG),
            regime,
            plan(Side.LONG),
            500,
        )
    )
    assert reason in decision.rejection_codes


def test_queue_confirmation_uses_event_time_and_resets_when_alignment_breaks() -> None:
    strategy_id = QueueMicropriceStrategy.strategy_id
    evaluator = StrategySignalEvaluator()
    registry = only_strategy(strategy_id)

    first = evaluator.evaluate(registry, aligned_features(Side.LONG, ts_ms=1_000), Regime.RANGE)
    early = evaluator.evaluate(registry, aligned_features(Side.LONG, ts_ms=1_499), Regime.RANGE)
    ready = evaluator.evaluate(registry, aligned_features(Side.LONG, ts_ms=1_500), Regime.RANGE)
    assert "QUEUE_ALIGNMENT_NOT_PERSISTENT" in decision_for(
        first, strategy_id, Side.LONG
    ).rejection_codes
    assert "QUEUE_ALIGNMENT_NOT_PERSISTENT" in decision_for(
        early, strategy_id, Side.LONG
    ).rejection_codes
    assert decision_for(ready, strategy_id, Side.LONG).status is CandidateStatus.QUALIFIED

    broken = replace(aligned_features(Side.LONG, ts_ms=1_600), ofi_250ms=0.0)
    evaluator.evaluate(registry, broken, Regime.RANGE)
    restarted = evaluator.evaluate(
        registry,
        aligned_features(Side.LONG, ts_ms=2_200),
        Regime.RANGE,
    )
    assert "QUEUE_ALIGNMENT_NOT_PERSISTENT" in decision_for(
        restarted, strategy_id, Side.LONG
    ).rejection_codes


@pytest.mark.parametrize(
    ("side", "regime"),
    [(Side.LONG, Regime.TREND_UP), (Side.SHORT, Regime.TREND_DOWN)],
)
def test_aggressor_flow_strategy_qualifies_both_directions(
    side: Side,
    regime: Regime,
) -> None:
    decision = AggressorFlowStrategy().evaluate(
        AggressorFlowContext(
            side,
            aligned_features(side),
            regime,
            plan(side),
            aggressive_signed_notional_robust_z=2.5,
            confirmation_ms=500,
        )
    )
    assert decision.status is CandidateStatus.QUALIFIED


@pytest.mark.parametrize(
    ("field_name", "value", "reason"),
    [
        ("data_healthy", False, "STALE_OR_DEGRADED_DATA"),
        ("spread_bps", 10.01, "WIDE_SPREAD"),
        ("trade_imbalance_3s", 0.24, "SHORT_FLOW_NOT_ALIGNED"),
        ("trade_imbalance_10s", 0.09, "LONG_FLOW_NOT_ALIGNED"),
        ("ofi_3s", 0.0, "OFI_NOT_ALIGNED"),
        ("price_response_efficiency", 0.54, "PRICE_RESPONSE_INEFFICIENT"),
        ("microprice", 99.99, "MICROPRICE_NOT_ALIGNED"),
    ],
)
def test_aggressor_flow_core_rejections(field_name: str, value: object, reason: str) -> None:
    feature = replace(aligned_features(Side.LONG), **{field_name: value})
    decision = AggressorFlowStrategy().evaluate(
        AggressorFlowContext(
            Side.LONG,
            feature,
            Regime.TREND_UP,
            plan(Side.LONG),
            aggressive_signed_notional_robust_z=2.5,
            confirmation_ms=500,
        )
    )
    assert reason in decision.rejection_codes


def test_aggressor_flow_rejects_regime_z_confirmation_and_absorption() -> None:
    strategy = AggressorFlowStrategy()
    feature = aligned_features(Side.LONG)
    common = {
        "side": Side.LONG,
        "features": feature,
        "plan": plan(Side.LONG),
        "confirmation_ms": 500,
    }
    assert "REGIME_DIRECTION_MISMATCH" in strategy.evaluate(
        AggressorFlowContext(
            regime=Regime.RANGE,
            aggressive_signed_notional_robust_z=2.5,
            **common,
        )
    ).rejection_codes
    assert "AGGRESSIVE_NOTIONAL_WEAK" in strategy.evaluate(
        AggressorFlowContext(
            regime=Regime.TREND_UP,
            aggressive_signed_notional_robust_z=1.79,
            **common,
        )
    ).rejection_codes
    assert "FLOW_ALIGNMENT_NOT_PERSISTENT" in strategy.evaluate(
        AggressorFlowContext(
            regime=Regime.TREND_UP,
            aggressive_signed_notional_robust_z=2.5,
            **{**common, "confirmation_ms": 499},
        )
    ).rejection_codes
    absorption = replace(feature, signed_notional_3s=50_000, price_response_efficiency=0.20)
    assert "PRICE_RESPONSE_INEFFICIENT" in strategy.evaluate(
        AggressorFlowContext(
            Side.LONG,
            absorption,
            Regime.TREND_UP,
            plan(Side.LONG),
            4.0,
            500,
        )
    ).rejection_codes


@pytest.mark.parametrize(
    ("side", "regime", "sign"),
    [
        (Side.LONG, Regime.TREND_UP, 1),
        (Side.SHORT, Regime.TREND_DOWN, -1),
    ],
)
def test_aggressor_confirmation_uses_prefix_and_event_time(
    side: Side,
    regime: Regime,
    sign: int,
) -> None:
    strategy_id = AggressorFlowStrategy.strategy_id
    evaluator = StrategySignalEvaluator()
    registry = only_strategy(strategy_id)
    for index, value in enumerate((90, 105, 95, 110, 100)):
        evaluator.evaluate(
            registry,
            aligned_features(side, ts_ms=index * 100, signed=value * sign),
            Regime.WARMUP,
        )
    first = evaluator.evaluate(
        registry,
        aligned_features(side, ts_ms=1_000, signed=1_000 * sign),
        regime,
    )
    ready = evaluator.evaluate(
        registry,
        aligned_features(side, ts_ms=1_500, signed=1_100 * sign),
        regime,
    )
    assert "FLOW_ALIGNMENT_NOT_PERSISTENT" in decision_for(
        first, strategy_id, side
    ).rejection_codes
    assert decision_for(ready, strategy_id, side).status is CandidateStatus.QUALIFIED


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_multilevel_microprice_strategy_qualifies_both_directions(side: Side) -> None:
    decision = MultilevelMicropriceStrategy().evaluate(
        MultilevelMicropriceContext(
            side=side,
            features=aligned_features(side),
            regime=Regime.RANGE,
            plan=plan(side),
            confirmation_ms=750,
        )
    )
    assert decision.status is CandidateStatus.QUALIFIED
    assert decision.initial_stop is not None
    assert decision.take_profit is not None


@pytest.mark.parametrize(
    ("field_name", "value", "reason"),
    [
        ("data_healthy", False, "STALE_OR_DEGRADED_DATA"),
        ("spread_bps", 8.01, "WIDE_SPREAD"),
        (
            "multi_level_microprice_10_minus_mid_bps",
            0.39,
            "MULTILEVEL_FAIR_PRICE_NOT_DISPLACED",
        ),
        ("microprice_minus_mid_bps", 0.0, "TOP_MICROPRICE_NOT_ALIGNED"),
        ("ofi_250ms", 0.0, "SHORT_OFI_NOT_ALIGNED"),
        ("ofi_3s", 0.0, "MEDIUM_OFI_NOT_ALIGNED"),
        ("trade_imbalance_1s", 0.14, "AGGRESSOR_FLOW_NOT_ALIGNED"),
        ("price_response_efficiency", 0.34, "PRICE_RESPONSE_INEFFICIENT"),
    ],
)
def test_multilevel_microprice_core_rejections(
    field_name: str,
    value: object,
    reason: str,
) -> None:
    feature = replace(aligned_features(Side.LONG), **{field_name: value})
    decision = MultilevelMicropriceStrategy().evaluate(
        MultilevelMicropriceContext(
            Side.LONG,
            feature,
            Regime.RANGE,
            plan(Side.LONG),
            750,
        )
    )
    assert reason in decision.rejection_codes


def test_multilevel_confirmation_uses_event_time_and_resets() -> None:
    strategy_id = MultilevelMicropriceStrategy.strategy_id
    evaluator = StrategySignalEvaluator()
    registry = only_strategy(strategy_id)
    first = evaluator.evaluate(
        registry,
        aligned_features(Side.LONG, ts_ms=1_000),
        Regime.RANGE,
    )
    ready = evaluator.evaluate(
        registry,
        aligned_features(Side.LONG, ts_ms=1_750),
        Regime.RANGE,
    )
    assert "MULTILEVEL_ALIGNMENT_NOT_PERSISTENT" in decision_for(
        first,
        strategy_id,
        Side.LONG,
    ).rejection_codes
    assert decision_for(ready, strategy_id, Side.LONG).status is CandidateStatus.QUALIFIED

    broken = replace(
        aligned_features(Side.LONG, ts_ms=1_800),
        multi_level_microprice_10_minus_mid_bps=0.0,
    )
    evaluator.evaluate(registry, broken, Regime.RANGE)
    restarted = evaluator.evaluate(
        registry,
        aligned_features(Side.LONG, ts_ms=2_800),
        Regime.RANGE,
    )
    assert "MULTILEVEL_ALIGNMENT_NOT_PERSISTENT" in decision_for(
        restarted,
        strategy_id,
        Side.LONG,
    ).rejection_codes


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_depth_adjusted_ofi_strategy_qualifies_both_directions(side: Side) -> None:
    decision = DepthAdjustedOfiStrategy().evaluate(
        DepthAdjustedOfiContext(
            side=side,
            features=aligned_features(side),
            regime=Regime.RANGE,
            plan=plan(side),
            directional_depth_adjusted_ofi_robust_z=2.5,
            confirmation_ms=500,
        )
    )
    assert decision.status is CandidateStatus.QUALIFIED
    assert decision.net_reward_risk is not None


@pytest.mark.parametrize(
    ("field_name", "value", "reason"),
    [
        ("data_healthy", False, "STALE_OR_DEGRADED_DATA"),
        ("spread_bps", 10.01, "WIDE_SPREAD"),
        ("depth_adjusted_ofi_3s_bps", 0.0, "DEPTH_ADJUSTED_OFI_NOT_ALIGNED"),
        ("ofi_250ms", 0.0, "SHORT_OFI_NOT_ALIGNED"),
        ("ofi_3s", 0.0, "MEDIUM_OFI_NOT_ALIGNED"),
        ("trade_imbalance_1s", 0.14, "AGGRESSOR_FLOW_NOT_ALIGNED"),
        ("microprice_minus_mid_bps", 0.0, "MICROPRICE_NOT_ALIGNED"),
        ("price_response_efficiency", 0.39, "PRICE_RESPONSE_INEFFICIENT"),
    ],
)
def test_depth_adjusted_ofi_core_rejections(
    field_name: str,
    value: object,
    reason: str,
) -> None:
    feature = replace(aligned_features(Side.LONG), **{field_name: value})
    decision = DepthAdjustedOfiStrategy().evaluate(
        DepthAdjustedOfiContext(
            Side.LONG,
            feature,
            Regime.RANGE,
            plan(Side.LONG),
            2.5,
            500,
        )
    )
    assert reason in decision.rejection_codes


def test_depth_adjusted_ofi_requires_robust_impulse_and_event_time() -> None:
    strategy_id = DepthAdjustedOfiStrategy.strategy_id
    direct = DepthAdjustedOfiStrategy().evaluate(
        DepthAdjustedOfiContext(
            Side.LONG,
            aligned_features(Side.LONG),
            Regime.RANGE,
            plan(Side.LONG),
            1.99,
            500,
        )
    )
    assert "DEPTH_ADJUSTED_OFI_IMPULSE_WEAK" in direct.rejection_codes

    evaluator = StrategySignalEvaluator()
    registry = only_strategy(strategy_id)
    for index, value in enumerate((0.08, 0.10, 0.09, 0.11, 0.10)):
        evaluator.evaluate(
            registry,
            replace(
                aligned_features(Side.LONG, ts_ms=index * 100),
                depth_adjusted_ofi_3s_bps=value,
            ),
            Regime.WARMUP,
        )
    first = evaluator.evaluate(
        registry,
        replace(
            aligned_features(Side.LONG, ts_ms=1_000),
            depth_adjusted_ofi_3s_bps=2.5,
        ),
        Regime.RANGE,
    )
    ready = evaluator.evaluate(
        registry,
        replace(
            aligned_features(Side.LONG, ts_ms=1_500),
            depth_adjusted_ofi_3s_bps=2.6,
        ),
        Regime.RANGE,
    )
    assert "DEPTH_ADJUSTED_OFI_NOT_PERSISTENT" in decision_for(
        first,
        strategy_id,
        Side.LONG,
    ).rejection_codes
    assert decision_for(ready, strategy_id, Side.LONG).status is CandidateStatus.QUALIFIED


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_ofi_return_confluence_qualifies_both_directions(side: Side) -> None:
    direction = 1 if side is Side.LONG else -1
    decision = OfiReturnConfluenceStrategy().evaluate(
        OfiReturnConfluenceContext(
            side=side,
            features=aligned_features(side),
            regime=Regime.RANGE,
            plan=plan(side),
            directional_depth_adjusted_ofi_robust_z=2.0,
            trailing_return_3s_bps=3.0 * direction,
            confirmation_ms=1_000,
        )
    )
    assert decision.status is CandidateStatus.QUALIFIED
    assert decision.net_reward_risk is not None


@pytest.mark.parametrize(
    ("field_name", "value", "reason"),
    [
        ("data_healthy", False, "STALE_OR_DEGRADED_DATA"),
        ("spread_bps", 8.01, "WIDE_SPREAD"),
        ("depth_adjusted_ofi_3s_bps", 0.0, "DEPTH_ADJUSTED_OFI_NOT_ALIGNED"),
        ("ofi_250ms", 0.0, "SHORT_OFI_NOT_ALIGNED"),
        ("ofi_3s", 0.0, "MEDIUM_OFI_NOT_ALIGNED"),
        ("microprice_minus_mid_bps", 0.19, "MICROPRICE_NOT_ALIGNED"),
        ("price_response_efficiency", 0.29, "PRICE_RESPONSE_INEFFICIENT"),
    ],
)
def test_ofi_return_confluence_core_rejections(
    field_name: str,
    value: object,
    reason: str,
) -> None:
    feature = replace(aligned_features(Side.LONG), **{field_name: value})
    decision = OfiReturnConfluenceStrategy().evaluate(
        OfiReturnConfluenceContext(
            Side.LONG,
            feature,
            Regime.RANGE,
            plan(Side.LONG),
            2.0,
            3.0,
            1_000,
        )
    )
    assert reason in decision.rejection_codes


def test_ofi_return_confluence_requires_history_z_return_persistence_and_cost() -> None:
    strategy = OfiReturnConfluenceStrategy()
    base = OfiReturnConfluenceContext(
        Side.LONG,
        aligned_features(Side.LONG),
        Regime.RANGE,
        plan(Side.LONG),
        2.0,
        3.0,
        1_000,
    )
    assert "RETURN_HISTORY_MISSING" in strategy.evaluate(
        replace(base, trailing_return_3s_bps=None)
    ).rejection_codes
    assert "TRAILING_RETURN_NOT_ALIGNED" in strategy.evaluate(
        replace(base, trailing_return_3s_bps=1.99)
    ).rejection_codes
    assert "DEPTH_ADJUSTED_OFI_IMPULSE_WEAK" in strategy.evaluate(
        replace(base, directional_depth_adjusted_ofi_robust_z=1.49)
    ).rejection_codes
    assert "OFI_RETURN_CONFLUENCE_NOT_PERSISTENT" in strategy.evaluate(
        replace(base, confirmation_ms=999)
    ).rejection_codes
    for regime, reason in (
        (Regime.WARMUP, "REGIME_WARMUP"),
        (Regime.DEGRADED, "REGIME_DEGRADED"),
        (Regime.SHOCK, "REGIME_SHOCK"),
    ):
        assert reason in strategy.evaluate(replace(base, regime=regime)).rejection_codes
    expensive_plan = replace(
        plan(Side.LONG),
        target=plan(Side.LONG).entry + Decimal("0.40"),
    )
    assert "COST_FRACTION_TOO_HIGH" in strategy.evaluate(
        replace(base, plan=expensive_plan)
    ).rejection_codes


def test_trailing_return_uses_nearest_prefix_anchor_and_ignores_future() -> None:
    history = [
        replace(features(), ts_ms=500, mid=99.0),
        replace(features(), ts_ms=1_000, mid=100.0),
        replace(features(), ts_ms=1_100, mid=101.0),
        replace(features(), ts_ms=9_000, mid=1_000.0),
    ]
    current = replace(features(), ts_ms=4_000, mid=100.05)
    assert _trailing_return_bps(history, current) == pytest.approx(5.0)
    stale_anchor = replace(features(), ts_ms=-501, mid=99.0)
    assert _trailing_return_bps([stale_anchor], current) is None


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_ofi_return_confluence_runtime_uses_prefix_and_event_time(side: Side) -> None:
    strategy_id = OfiReturnConfluenceStrategy.strategy_id
    evaluator = StrategySignalEvaluator()
    registry = only_strategy(strategy_id)
    direction = 1 if side is Side.LONG else -1
    for index, value in enumerate((0.08, 0.10, 0.09, 0.11, 0.10, 0.12)):
        evaluator.evaluate(
            registry,
            replace(
                aligned_features(side, ts_ms=index * 500),
                mid=100.0,
                depth_adjusted_ofi_3s_bps=value * direction,
            ),
            Regime.WARMUP,
        )

    def current(ts_ms: int, mid: float):
        return replace(
            aligned_features(side, ts_ms=ts_ms),
            mid=mid,
            microprice=mid + 0.01 * direction,
            microprice_minus_mid_bps=1.0 * direction,
            depth_adjusted_ofi_3s_bps=2.5 * direction,
        )

    first = evaluator.evaluate(
        registry,
        current(4_000, 100.05 if side is Side.LONG else 99.95),
        Regime.RANGE,
    )
    ready = evaluator.evaluate(
        registry,
        current(5_000, 100.06 if side is Side.LONG else 99.94),
        Regime.RANGE,
    )
    assert "OFI_RETURN_CONFLUENCE_NOT_PERSISTENT" in decision_for(
        first,
        strategy_id,
        side,
    ).rejection_codes
    assert decision_for(ready, strategy_id, side).status is CandidateStatus.QUALIFIED


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_book_slope_asymmetry_qualifies_both_directions(side: Side) -> None:
    decision = BookSlopeAsymmetryStrategy().evaluate(
        BookSlopeAsymmetryContext(
            side=side,
            features=aligned_features(side),
            regime=Regime.RANGE,
            plan=plan(side),
            bid_slope_percentile=0.90 if side is Side.LONG else 0.10,
            ask_slope_percentile=0.10 if side is Side.LONG else 0.90,
            history_sample_count=32,
            confirmation_ms=1_000,
        )
    )
    assert decision.status is CandidateStatus.QUALIFIED
    assert decision.initial_stop is not None
    assert decision.take_profit is not None
    assert decision.net_reward_risk is not None


def test_book_slope_asymmetry_rejects_short_history_weak_structure_and_cost() -> None:
    base = BookSlopeAsymmetryContext(
        side=Side.LONG,
        features=aligned_features(Side.LONG),
        regime=Regime.RANGE,
        plan=plan(Side.LONG),
        bid_slope_percentile=0.90,
        ask_slope_percentile=0.10,
        history_sample_count=32,
        confirmation_ms=1_000,
    )
    strategy = BookSlopeAsymmetryStrategy()
    assert "BOOK_SLOPE_HISTORY_SHORT" in strategy.evaluate(
        replace(base, history_sample_count=31)
    ).rejection_codes
    assert "OPPOSING_BOOK_NOT_THIN" in strategy.evaluate(
        replace(base, ask_slope_percentile=0.16)
    ).rejection_codes
    assert "SUPPORTING_BOOK_NOT_FIRM" in strategy.evaluate(
        replace(base, bid_slope_percentile=0.49)
    ).rejection_codes
    assert "BOOK_SLOPE_ASYMMETRY_WEAK" in strategy.evaluate(
        replace(
            base,
            features=replace(
                aligned_features(Side.LONG),
                bid_book_slope_10=70.0,
                ask_book_slope_10=50.0,
            ),
        )
    ).rejection_codes
    assert "BOOK_SLOPE_ASYMMETRY_NOT_PERSISTENT" in strategy.evaluate(
        replace(base, confirmation_ms=999)
    ).rejection_codes
    expensive_plan = replace(
        plan(Side.LONG),
        target=plan(Side.LONG).entry + Decimal("0.40"),
    )
    assert "COST_FRACTION_TOO_HIGH" in strategy.evaluate(
        replace(base, plan=expensive_plan)
    ).rejection_codes


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_book_slope_runtime_uses_prefix_percentiles_and_event_time(side: Side) -> None:
    strategy_id = BookSlopeAsymmetryStrategy.strategy_id
    evaluator = StrategySignalEvaluator()
    registry = only_strategy(strategy_id)
    for index in range(32):
        evaluator.evaluate(
            registry,
            replace(
                aligned_features(side, ts_ms=index * 100),
                bid_book_slope_10=100.0,
                ask_book_slope_10=100.0,
            ),
            Regime.WARMUP,
        )
    first = evaluator.evaluate(
        registry,
        aligned_features(side, ts_ms=4_000),
        Regime.RANGE,
    )
    ready = evaluator.evaluate(
        registry,
        aligned_features(side, ts_ms=5_000),
        Regime.RANGE,
    )
    assert "BOOK_SLOPE_ASYMMETRY_NOT_PERSISTENT" in decision_for(
        first,
        strategy_id,
        side,
    ).rejection_codes
    assert decision_for(ready, strategy_id, side).status is CandidateStatus.QUALIFIED
