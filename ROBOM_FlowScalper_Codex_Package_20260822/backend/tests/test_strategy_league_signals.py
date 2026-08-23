"""전략 리그 E/F의 대칭 조건과 실제 500ms 지속성을 검증한다."""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.app.domain.models import Side
from backend.app.regime import Regime
from backend.app.strategies import (
    AggressorFlowContext,
    AggressorFlowStrategy,
    QueueMicropriceContext,
    QueueMicropriceStrategy,
)
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.registry import StrategyMode, StrategyRegistry
from backend.app.strategies.runtime_evaluator import StrategySignalEvaluator
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
