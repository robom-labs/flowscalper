"""불변 진입계획부터 지연 체결·TP1/TP2·main/shadow 회계까지 검증한다."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from backend.app.candidates import CandidatePlan, CandidatePlanner
from backend.app.costing import CostProfile
from backend.app.domain.market import Instrument
from backend.app.domain.models import Side, Venue
from backend.app.execution import BookSnapshot
from backend.app.execution.portfolio import PaperPortfolioEngine
from backend.app.regime import Regime
from backend.app.risk import RiskState
from backend.app.strategies.base import (
    CandidateDecision,
    CandidateStatus,
    costed_plan,
)
from backend.app.strategies.registry import StrategyRegistry
from backend.app.strategies.runtime_evaluator import _plan
from backend.app.strategies.shadow import ShadowLedger
from backend.tests.test_strategies import features


def book(
    ts_ms: int,
    *,
    bids: tuple[tuple[str, str], ...] = (("99.9", "100"), ("99.8", "100")),
    asks: tuple[tuple[str, str], ...] = (("100.1", "100"), ("100.2", "100")),
) -> BookSnapshot:
    return BookSnapshot(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        ts_ms=ts_ms,
        bids=tuple((Decimal(price), Decimal(quantity)) for price, quantity in bids),
        asks=tuple((Decimal(price), Decimal(quantity)) for price, quantity in asks),
    )


def qualified_decision(strategy_id: str = "LSA_REVERSAL_V1") -> CandidateDecision:
    return CandidateDecision(
        strategy_id=strategy_id,
        side=Side.LONG,
        status=CandidateStatus.QUALIFIED,
        reason_codes=("STRUCTURE_CONFIRMED", "FLOW_CONFIRMED"),
        rejection_codes=(),
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        take_profit=Decimal("106"),
        expected_cost_bps=Decimal("13"),
        net_reward_risk=Decimal("4"),
    )


def instrument() -> Instrument:
    return Instrument(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        status="TEST",
        contract_type="PAPER",
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
    )


def tight_spread_instrument() -> Instrument:
    return Instrument(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        status="TEST",
        contract_type="PAPER",
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
    )


def tight_spread_book(ts_ms: int) -> BookSnapshot:
    return book(
        ts_ms,
        bids=(("99.99", "100"), ("99.98", "100")),
        asks=(("100.01", "100"), ("100.02", "100")),
    )


def candidate_plan(*, strategy_id: str = "LSA_REVERSAL_V1") -> CandidatePlan:
    result = CandidatePlanner().build(
        signal_event_id="depth-signal-1",
        run_id="run-live-1",
        venue=Venue.FIXTURE,
        decision=qualified_decision(strategy_id),
        snapshot=features(),
        regime=Regime.RANGE,
        book=book(1_000),
        instrument=instrument(),
        signal_time_ms=1_000,
        risk_state=RiskState(),
        main_eligible=True,
        shadow_eligible=True,
    )
    assert result.rejection_codes == ()
    assert result.plan is not None
    return result.plan


@pytest.mark.parametrize("strategy_id", StrategyRegistry().strategy_ids)
@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_runtime_plan_geometry_survives_final_live_cost_gate_for_every_strategy(
    strategy_id: str,
    side: Side,
) -> None:
    """전략 1차 게이트와 최종 호가·비용 게이트가 서로 모순되지 않아야 한다."""

    registry = StrategyRegistry()
    descriptor = registry.descriptor(strategy_id)
    snapshot = features()
    instrument_row = tight_spread_instrument()
    inputs = _plan(
        snapshot,
        side,
        instrument_row.tick_size,
        exit_style=descriptor.exit_style,
    )
    costed, rejection_codes = costed_plan(side, inputs)
    assert rejection_codes == ()
    assert costed is not None
    decision = CandidateDecision(
        strategy_id=strategy_id,
        side=side,
        status=CandidateStatus.QUALIFIED,
        reason_codes=("TEST_CONDITIONS_CONFIRMED",),
        rejection_codes=(),
        planned_entry=costed.entry,
        initial_stop=costed.stop,
        take_profit=costed.target,
        expected_cost_bps=inputs.expected_total_cost_bps,
        net_reward_risk=costed.net_reward_risk,
    )

    result = CandidatePlanner().build(
        signal_event_id=f"depth-{strategy_id}-{side.value}",
        run_id="run-plan-geometry",
        venue=Venue.FIXTURE,
        decision=decision,
        snapshot=snapshot,
        regime=descriptor.supported_regimes[0],
        book=tight_spread_book(1_000),
        instrument=instrument_row,
        signal_time_ms=1_000,
        risk_state=RiskState(),
        main_eligible=True,
        shadow_eligible=True,
        exit_style=descriptor.exit_style,
    )

    assert result.rejection_codes == ()
    assert result.plan is not None
    assert result.plan.initial_stop == costed.stop
    assert result.plan.net_reward_risk >= Decimal("1.20")


def test_candidate_plan_is_complete_immutable_and_risk_bounded() -> None:
    plan = candidate_plan()
    assert plan.planned_entry == Decimal("100.1")
    assert plan.worst_allowed_entry == Decimal("100.4")
    assert [target.label for target in plan.take_profit_targets] == ["TP1", "TP2"]
    assert sum(target.quantity_fraction for target in plan.take_profit_targets) == 1
    assert plan.max_planned_loss <= plan.risk_budget == Decimal("1.000")
    assert plan.net_reward_risk >= Decimal("1.20")
    assert "NO_FIXED_TIME_EXIT" in plan.management_policy
    with pytest.raises(FrozenInstanceError):
        plan.position_size = Decimal("999")  # type: ignore[misc]


def test_latency_executable_depth_tp1_tp2_and_live_pnl_are_end_to_end() -> None:
    plan = candidate_plan()
    shadows = ShadowLedger((plan.strategy_id,))
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=shadows,
    )
    engine.offer((plan,), entries_paused=False)
    engine.on_book(book(1_249))
    assert engine.main.position is None
    assert engine.main.pending_entry is not None

    engine.on_book(book(1_250))
    assert engine.main.position is not None
    assert engine.main.risk_state.open_positions == 1
    assert engine.main.position.protected.entry_fill.average_price == Decimal("100.1")
    assert shadows.account(plan.strategy_id, CostProfile.BASE).open_position is not None
    assert shadows.account(plan.strategy_id, CostProfile.STRESS).open_position is None

    engine.on_book(book(1_500))
    assert shadows.account(plan.strategy_id, CostProfile.STRESS).open_position is not None
    snapshot = engine.main_position_snapshot(
        book(1_600, bids=(("100.8", "100"),), asks=(("100.9", "100"),))
    )
    assert snapshot is not None
    assert Decimal(str(snapshot["gross_pnl"])) > 0
    assert Decimal(str(snapshot["net_pnl"])) < Decimal(str(snapshot["gross_pnl"]))

    tp1 = plan.take_profit_targets[0].price
    engine.on_book(
        book(2_000, bids=((str(tp1 + Decimal("0.1")), "100"),), asks=(("107", "100"),))
    )
    assert engine.main.position is not None
    assert engine.main.position.pending_exit is not None
    assert engine.main.position.remaining_quantity == engine.main.position.original_quantity
    engine.on_book(
        book(2_250, bids=((str(tp1 + Decimal("0.05")), "100"),), asks=(("107", "100"),))
    )
    assert engine.main.position is not None
    assert (
        Decimal(0)
        < engine.main.position.remaining_quantity
        < engine.main.position.original_quantity
    )
    assert engine.main.position.exit_legs[0].label == "TP1"

    tp2 = plan.take_profit_targets[1].price
    engine.on_book(
        book(3_000, bids=((str(tp2 + Decimal("0.1")), "100"),), asks=(("107", "100"),))
    )
    engine.on_book(
        book(3_250, bids=((str(tp2 + Decimal("0.05")), "100"),), asks=(("107", "100"),))
    )
    assert engine.main.position is None
    assert engine.main.risk_state.open_positions == 0
    assert len(engine.main.completed_trades) == 1
    trade = engine.main.completed_trades[0]
    assert trade.flags == ("TP1", "TP2")
    assert trade.net_pnl_usdt == trade.gross_pnl_usdt - trade.fees_usdt - trade.slippage_usdt
    assert engine.main_summary()["trade_count"] == 1

    # STRESS는 더 긴 청산 지연을 가져 BASE와 다른 시각에 닫힌다.
    assert shadows.account(plan.strategy_id, CostProfile.BASE).open_position is None
    assert shadows.account(plan.strategy_id, CostProfile.STRESS).open_position is not None
    engine.on_book(
        book(3_750, bids=((str(tp2 + Decimal("0.05")), "100"),), asks=(("107", "100"),))
    )
    assert shadows.account(plan.strategy_id, CostProfile.STRESS).open_position is None
    assert shadows.account(plan.strategy_id, CostProfile.BASE).current_equity_usdt != Decimal(
        "1000"
    )
    assert shadows.account(plan.strategy_id, CostProfile.STRESS).current_equity_usdt != Decimal(
        "1000"
    )


def test_main_max_one_and_partial_entry_only_protects_actual_fill() -> None:
    first = candidate_plan()
    second = candidate_plan(strategy_id="CBR_CONTINUATION_V1")
    shadows = ShadowLedger((first.strategy_id, second.strategy_id))
    engine = PaperPortfolioEngine(
        run_id=first.run_id,
        strategy_ids=(first.strategy_id, second.strategy_id),
        shadow_ledger=shadows,
    )
    engine.offer((second, first), entries_paused=False)
    selected_id = engine.main.pending_entry.plan.candidate_id if engine.main.pending_entry else None
    assert selected_id in {first.candidate_id, second.candidate_id}

    engine.on_book(
        book(
            1_250,
            asks=(("100.1", "0.005"), ("100.5", "100")),
            bids=(("99.9", "100"),),
        )
    )
    assert engine.main.position is not None
    assert engine.main.position.original_quantity == Decimal("0.005")
    assert engine.main.position.protected.protection_orders[0].requested_quantity == Decimal(
        "0.005"
    )
    engine.offer((first, second), entries_paused=False)
    assert engine.main.pending_entry is None
    assert engine.main.risk_state.open_positions == 1


def test_position_can_hold_beyond_120_seconds_but_persistent_edge_decay_arms_exit() -> None:
    plan = candidate_plan()
    shadows = ShadowLedger((plan.strategy_id,))
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=shadows,
    )
    engine.offer((plan,), entries_paused=False)
    engine.on_book(book(1_250))
    assert engine.main.position is not None

    engine.evaluate_health(features(), Regime.RANGE, now_ms=122_000)
    assert engine.main.position.pending_exit is None
    adverse = replace(
        features(),
        ofi_3s=-3,
        trade_imbalance_3s=-0.8,
        microprice=99.8,
    )
    engine.evaluate_health(adverse, Regime.SHOCK, now_ms=123_000)
    assert engine.main.position.pending_exit is None
    engine.evaluate_health(adverse, Regime.SHOCK, now_ms=124_000)
    assert engine.main.position.pending_exit is not None
    assert engine.main.position.pending_exit.label == "EXIT_EDGE_DECAY"


def test_pending_protected_and_exit_pending_accounts_roundtrip_for_restart() -> None:
    plan = candidate_plan()
    shadows = ShadowLedger((plan.strategy_id,))
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=shadows,
    )

    engine.offer((plan,), entries_paused=False)
    pending_payload = json.loads(json.dumps(engine.recovery_state()))
    pending_restored = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    pending_restored.restore_state(pending_payload)
    assert pending_restored.lifecycle_state() == "ENTRY_PENDING"
    assert pending_restored.recovery_state() == pending_payload

    engine.on_book(book(1_250))
    protected_payload = json.loads(json.dumps(engine.recovery_state()))
    protected_restored = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    protected_restored.restore_state(protected_payload)
    assert protected_restored.lifecycle_state() == "PROTECTED"
    assert protected_restored.main.position is not None
    assert protected_restored.main.position.protected.trade_id == (
        engine.main.position.protected.trade_id if engine.main.position else None
    )
    assert protected_restored.shadow_ledger.recovery_state() == (
        engine.shadow_ledger.recovery_state()
    )

    tp1 = plan.take_profit_targets[0].price
    engine.on_book(
        book(2_000, bids=((str(tp1 + Decimal("0.1")), "100"),), asks=(("107", "100"),))
    )
    exit_payload = json.loads(json.dumps(engine.recovery_state()))
    exit_restored = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    exit_restored.restore_state(exit_payload)
    assert exit_restored.lifecycle_state() == "EXIT_PENDING"
    assert exit_restored.recovery_state() == exit_payload

    exit_payload["run_id"] = "another-run"
    with pytest.raises(ValueError, match="다른 Run"):
        exit_restored.restore_state(exit_payload)
