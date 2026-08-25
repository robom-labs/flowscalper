"""전략 Registry 설정과 전략별 BASE·STRESS shadow 계좌 격리를 검증한다."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

import backend.app.strategies.runtime_evaluator as runtime_evaluator_module
from backend.app.build_identity import STRATEGY_IDS, STRATEGY_VERSION
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.costing import CostProfile
from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Side
from backend.app.regime import Regime
from backend.app.runtime import PaperRuntime
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.registry import StrategyMode, StrategyRegistry
from backend.app.strategies.runtime_evaluator import (
    StrategySignalEvaluator,
    _pullback_metrics,
)
from backend.app.strategies.shadow import ShadowLedger, ShadowPosition
from backend.tests.test_strategies import features


def test_registry_exposes_ten_strategies_and_honors_mode_and_direction() -> None:
    registry = StrategyRegistry()
    assert registry.strategy_ids == (
        "LSA_REVERSAL_V1",
        "CBR_CONTINUATION_V1",
        "VWAP_EXHAUSTION_REVERSION_V1",
        "OFI_CONTINUATION_PULLBACK_V1",
        "QUEUE_MICROPRICE_MOMENTUM_V1",
        "AGGRESSOR_FLOW_CONTINUATION_V1",
        "MULTILEVEL_MICROPRICE_MOMENTUM_V1",
        "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        "OFI_RETURN_CONFLUENCE_V1",
        "BOOK_SLOPE_ASYMMETRY_V1",
    )
    assert STRATEGY_IDS == registry.strategy_ids
    assert STRATEGY_VERSION.startswith("+".join(registry.strategy_ids) + "@")
    assert [row["mode"] for row in registry.rows()] == [
        "OFF",
        "ACTIVE",
        "SHADOW",
        "OFF",
        "OFF",
        "SHADOW",
        "SHADOW",
        "OFF",
        "SHADOW",
        "SHADOW",
    ]
    assert all(row["long_enabled"] and row["short_enabled"] for row in registry.rows())
    registry.configure(
        "VWAP_EXHAUSTION_REVERSION_V1",
        mode=StrategyMode.OFF,
        long_enabled=True,
        short_enabled=True,
    )
    registry.configure(
        "LSA_REVERSAL_V1",
        mode=StrategyMode.SHADOW,
        long_enabled=True,
        short_enabled=False,
    )

    evaluator = StrategySignalEvaluator()
    decisions = evaluator.evaluate(registry, features(), Regime.WARMUP)

    assert len(decisions) == 11
    assert all(item.decision.status is CandidateStatus.REJECTED for item in decisions)
    lsa = next(item for item in decisions if item.decision.strategy_id == "LSA_REVERSAL_V1")
    assert lsa.decision.side is Side.LONG
    assert not lsa.main_eligible
    assert lsa.shadow_eligible
    assert not any(
        item.decision.strategy_id == "VWAP_EXHAUSTION_REVERSION_V1" for item in decisions
    )
    assert not any(
        item.decision.strategy_id
        in {
            "OFI_CONTINUATION_PULLBACK_V1",
            "QUEUE_MICROPRICE_MOMENTUM_V1",
            "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        }
        for item in decisions
    )


def test_strategy_history_statistics_are_computed_once_per_snapshot(monkeypatch) -> None:
    robust_calls = 0
    percentile_calls = 0
    original_robust_z = runtime_evaluator_module.robust_z_from_sorted
    original_percentile = runtime_evaluator_module.rolling_percentile_from_sorted

    def counted_robust_z(history, current: float) -> float:
        nonlocal robust_calls
        robust_calls += 1
        return original_robust_z(history, current)

    def counted_percentile(history, current: float) -> float:
        nonlocal percentile_calls
        percentile_calls += 1
        return original_percentile(history, current)

    monkeypatch.setattr(runtime_evaluator_module, "robust_z_from_sorted", counted_robust_z)
    monkeypatch.setattr(
        runtime_evaluator_module,
        "rolling_percentile_from_sorted",
        counted_percentile,
    )

    decisions = StrategySignalEvaluator().evaluate(
        StrategyRegistry(),
        features(),
        Regime.RANGE,
    )

    assert len(decisions) == 12
    assert robust_calls == 4
    assert percentile_calls == 5


def test_strategy_sorted_history_evicts_with_same_exact_window() -> None:
    evaluator = StrategySignalEvaluator(history_limit=3)
    registry = StrategyRegistry()
    snapshots = [
        replace(
            features(),
            ts_ms=index * 500,
            signed_notional_3s=float(index - 2),
            price_response_efficiency=index / 10,
            compression_ratio=index / 20,
            efficiency_ratio_30s=index / 30,
            micro_vwap_10s=99.0 + index / 10,
        )
        for index in range(5)
    ]

    for snapshot in snapshots:
        evaluator.evaluate(registry, snapshot, Regime.RANGE)

    window = list(evaluator._history[snapshots[-1].symbol])
    ordered = evaluator._sorted_history[snapshots[-1].symbol]
    assert window == snapshots[-3:]
    assert ordered.flow == sorted(abs(item.signed_notional_3s) for item in window)
    assert ordered.price_response == sorted(item.price_response_efficiency for item in window)
    assert ordered.compression == sorted(item.compression_ratio for item in window)
    assert ordered.efficiency == sorted(item.efficiency_ratio_30s for item in window)
    assert ordered.signed_notional == sorted(item.signed_notional_3s for item in window)
    assert ordered.depth_adjusted_ofi == sorted(item.depth_adjusted_ofi_3s_bps for item in window)
    assert ordered.bid_book_slope == sorted(item.bid_book_slope_10 for item in window)
    assert ordered.ask_book_slope == sorted(item.ask_book_slope_10 for item in window)


@pytest.mark.parametrize(
    ("side", "prices"),
    [
        (Side.LONG, (100.0, 102.0, 101.0, 101.2)),
        (Side.SHORT, (100.0, 98.0, 99.0, 98.8)),
    ],
)
def test_pullback_metrics_use_prefix_event_time_and_require_price_reacceleration(
    side: Side,
    prices: tuple[float, ...],
) -> None:
    snapshots = [
        replace(features(), ts_ms=timestamp, mid=price)
        for timestamp, price in zip((0, 1_000, 2_000, 2_500), prices, strict=True)
    ]
    metrics = _pullback_metrics(
        snapshots[:-1],
        snapshots[-1],
        side,
        maximum_duration_seconds=10,
    )
    assert metrics.duration_seconds == 1.5
    assert metrics.maximum_retrace_fraction == pytest.approx(0.5)
    assert metrics.price_reaccelerated

    no_reacceleration = _pullback_metrics(
        snapshots[:-2],
        snapshots[-2],
        side,
        maximum_duration_seconds=10,
    )
    assert not no_reacceleration.price_reaccelerated

    future = replace(features(), ts_ms=9_000, mid=1_000 if side is Side.LONG else 1.0)
    with_future_in_history = _pullback_metrics(
        [*snapshots[:-1], future],
        snapshots[-1],
        side,
        maximum_duration_seconds=10,
    )
    assert with_future_in_history == metrics


def test_runtime_temporal_gate_uses_event_time_and_resets() -> None:
    evaluator = StrategySignalEvaluator()
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_000, aligned=True) == 0
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_299, aligned=True) == 299
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_300, aligned=True) == 300
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_400, aligned=False) == 0
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 2_000, aligned=True) == 0


def test_shadow_accounts_are_independent_by_strategy_and_cost_profile() -> None:
    registry = StrategyRegistry()
    ledger = ShadowLedger(registry.strategy_ids)
    position = ShadowPosition(
        shadow_trade_id="shadow-lsa-base-1",
        symbol="BTCUSDT",
        side=Side.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        entry_fee_usdt=Decimal("0.05"),
        entry_slippage_usdt=Decimal("0.02"),
        opened_ts_ms=1_000,
    )
    ledger.open("LSA_REVERSAL_V1", CostProfile.BASE, position)
    trade = ledger.close(
        "LSA_REVERSAL_V1",
        CostProfile.BASE,
        exit_price=Decimal("101"),
        exit_fee_usdt=Decimal("0.05"),
        exit_slippage_usdt=Decimal("0.03"),
        closed_ts_ms=2_000,
        exit_reason="TAKE_PROFIT_1",
    )

    assert trade.gross_pnl_usdt == Decimal("1")
    assert trade.net_pnl_usdt == Decimal("0.85")
    assert ledger.account("LSA_REVERSAL_V1", CostProfile.BASE).current_equity_usdt == Decimal(
        "1000.85"
    )
    assert ledger.account("LSA_REVERSAL_V1", CostProfile.STRESS).current_equity_usdt == Decimal(
        "1000"
    )
    assert ledger.account("CBR_CONTINUATION_V1", CostProfile.BASE).current_equity_usdt == Decimal(
        "1000"
    )


def test_live_depth_skips_retired_strategies_without_fake_probability() -> None:
    clock = DeterministicClock()
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-registry-live",
        clock=clock,
    )
    runtime.ingest_live_event(
        MarketEvent(
            event_id="depth-1",
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="DEPTH_UPDATE",
            venue_ts_ms=clock.utc_ms(),
            receive_monotonic_ns=clock.monotonic_ns(),
            sequence_start=1,
            sequence_end=1,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={
                "bid": "99.9",
                "bid_qty": "5",
                "ask": "100.1",
                "ask_qty": "5",
                "bids": [["99.9", "5"], ["99.8", "8"]],
                "asks": [["100.1", "5"], ["100.2", "8"]],
            },
        )
    )

    decisions = runtime.strategy_decisions()
    assert runtime.strategy_evaluation_count == 12
    assert {decision.strategy_id for decision in decisions} == set(
        runtime.strategy_registry.strategy_ids
    ) - {
        "LSA_REVERSAL_V1",
        "OFI_CONTINUATION_PULLBACK_V1",
        "QUEUE_MICROPRICE_MOMENTUM_V1",
        "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
    }
    assert all(decision.tp_probability is None for decision in decisions)
    assert len(runtime.dashboard()["shadow_accounts"]) == 20
    assert len(runtime.dashboard()["league_accounts"]) == 20
