"""전략 Registry 설정과 전략별 BASE·STRESS shadow 계좌 격리를 검증한다."""

from __future__ import annotations

from decimal import Decimal

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.costing import CostProfile
from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Side
from backend.app.regime import Regime
from backend.app.runtime import PaperRuntime
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.registry import StrategyMode, StrategyRegistry
from backend.app.strategies.runtime_evaluator import StrategySignalEvaluator
from backend.app.strategies.shadow import ShadowLedger, ShadowPosition
from backend.tests.test_strategies import features


def test_registry_exposes_six_strategies_and_honors_mode_and_direction() -> None:
    registry = StrategyRegistry()
    assert registry.strategy_ids == (
        "LSA_REVERSAL_V1",
        "CBR_CONTINUATION_V1",
        "VWAP_EXHAUSTION_REVERSION_V1",
        "OFI_CONTINUATION_PULLBACK_V1",
        "QUEUE_MICROPRICE_MOMENTUM_V1",
        "AGGRESSOR_FLOW_CONTINUATION_V1",
    )
    assert [row["mode"] for row in registry.rows()] == [
        "ACTIVE",
        "ACTIVE",
        "SHADOW",
        "SHADOW",
        "SHADOW",
        "SHADOW",
    ]
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

    assert len(decisions) == 9
    assert all(item.decision.status is CandidateStatus.REJECTED for item in decisions)
    lsa = next(item for item in decisions if item.decision.strategy_id == "LSA_REVERSAL_V1")
    assert lsa.decision.side is Side.LONG
    assert not lsa.main_eligible
    assert lsa.shadow_eligible
    assert not any(
        item.decision.strategy_id == "VWAP_EXHAUSTION_REVERSION_V1" for item in decisions
    )


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
    assert ledger.account(
        "CBR_CONTINUATION_V1", CostProfile.BASE
    ).current_equity_usdt == Decimal("1000")


def test_live_depth_runs_all_six_strategies_without_fake_probability() -> None:
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
    )
    assert all(decision.tp_probability is None for decision in decisions)
    assert len(runtime.dashboard()["shadow_accounts"]) == 12
    assert len(runtime.dashboard()["league_accounts"]) == 12
