"""불변 진입계획부터 지연 체결·TP1/TP2·main/shadow 회계까지 검증한다."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from backend.app.candidates import CandidatePlan, CandidatePlanner, TakeProfitTarget
from backend.app.costing import CostProfile
from backend.app.domain.market import Instrument
from backend.app.domain.models import Side, Venue
from backend.app.execution import BookSnapshot, ExitReason
from backend.app.execution.portfolio import PaperPortfolioEngine
from backend.app.execution.trailing import (
    TrailingActivationRule,
    TrailingModel,
    TrailingPolicy,
    TrailingState,
)
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
    receive_ts_ms: int | None = None,
) -> BookSnapshot:
    return BookSnapshot(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        ts_ms=ts_ms,
        bids=tuple((Decimal(price), Decimal(quantity)) for price, quantity in bids),
        asks=tuple((Decimal(price), Decimal(quantity)) for price, quantity in asks),
        receive_ts_ms=receive_ts_ms,
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


def test_book_receive_time_cannot_precede_exchange_event_time() -> None:
    with pytest.raises(ValueError, match="receive"):
        book(2_000, receive_ts_ms=1_999).validate()


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


def test_candidate_planner_requires_fresh_completed_reference_for_structure_trailing() -> None:
    planner = CandidatePlanner()
    trailing_policy = TrailingPolicy(
        policy_id="STRUCTURE_REFERENCE_V1",
        model=TrailingModel.CHANDELIER_STRUCTURE,
        activation_rule=TrailingActivationRule.R_MULTIPLE,
        activation_r=Decimal("1"),
        partial_tp_required=False,
        atr_multiplier=Decimal("2"),
    )
    common = {
        "signal_event_id": "depth-trailing-reference",
        "run_id": "run-trailing-reference",
        "venue": Venue.FIXTURE,
        "decision": qualified_decision(),
        "snapshot": features(),
        "regime": Regime.RANGE,
        "book": book(61_000),
        "instrument": instrument(),
        "risk_state": RiskState(),
        "main_eligible": True,
        "shadow_eligible": True,
        "trailing_policy": trailing_policy,
        "trailing_atr": Decimal("1.2"),
        "trailing_reference_interval_seconds": 60,
    }

    missing_structure = planner.build(
        **common,
        signal_time_ms=61_000,
        trailing_reference_ts_ms=60_000,
    )
    assert missing_structure.rejection_codes == ("TRAILING_COMPLETED_STRUCTURE_MISSING",)

    stale_reference = planner.build(
        **common,
        signal_time_ms=121_001,
        trailing_structure_stop=Decimal("99.5"),
        trailing_reference_ts_ms=60_000,
    )
    assert stale_reference.rejection_codes == ("TRAILING_COMPLETED_CANDLE_REFERENCE_STALE",)

    accepted = planner.build(
        **common,
        signal_time_ms=61_000,
        trailing_structure_stop=Decimal("99.5"),
        trailing_reference_ts_ms=60_000,
    )
    assert accepted.rejection_codes == ()
    assert accepted.plan is not None
    assert accepted.plan.trailing_atr == Decimal("1.2")
    assert accepted.plan.trailing_structure_stop == Decimal("99.5")


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
    assert "SAFETY_MAX_HOLD_900S" in plan.management_policy
    assert plan.maximum_holding_ms == 900_000
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
    engine.on_book(book(2_000, bids=((str(tp1 + Decimal("0.1")), "100"),), asks=(("107", "100"),)))
    assert engine.main.position is not None
    assert engine.main.position.pending_exit is not None
    assert engine.main.position.remaining_quantity == engine.main.position.original_quantity
    engine.on_book(book(2_250, bids=((str(tp1 + Decimal("0.05")), "100"),), asks=(("107", "100"),)))
    assert engine.main.position is not None
    assert (
        Decimal(0)
        < engine.main.position.remaining_quantity
        < engine.main.position.original_quantity
    )
    assert engine.main.position.exit_legs[0].label == "TP1"

    tp2 = plan.take_profit_targets[1].price
    engine.on_book(book(3_000, bids=((str(tp2 + Decimal("0.1")), "100"),), asks=(("107", "100"),)))
    engine.on_book(book(3_250, bids=((str(tp2 + Decimal("0.05")), "100"),), asks=(("107", "100"),)))
    assert engine.main.position is None
    assert engine.main.risk_state.open_positions == 0
    assert len(engine.main.completed_trades) == 1
    trade = engine.main.completed_trades[0]
    assert trade.flags == ("TP1", "TP2")
    assert trade.tp1_hit_ts_ms == 2_250
    assert trade.tp2_hit_ts_ms == 3_250
    assert trade.time_to_tp1_ms == 1_000
    assert trade.time_to_tp2_ms == 2_000
    assert trade.time_to_stop_ms is None
    assert trade.holding_ms == 2_000
    assert trade.net_pnl_usdt == trade.gross_pnl_usdt - trade.fees_usdt - trade.slippage_usdt
    assert engine.main_summary()["trade_count"] == 1

    main_audit_times = [
        (str(row["event"]), int(str(row["ts_ms"])))
        for row in engine.audit_events
        if row.get("account_id") == engine.main.account_id
    ]
    assert any(
        row["event"] == "MAIN_CANDIDATE_SELECTED" and row["ts_ms"] == 1_000
        for row in engine.audit_events
    )
    assert ("ENTRY_FILLED", 1_250) in main_audit_times
    assert ("TAKE_PROFIT_EXIT_PENDING", 2_000) in main_audit_times
    assert ("EXIT_FILL", 2_250) in main_audit_times
    assert ("TAKE_PROFIT_EXIT_PENDING", 3_000) in main_audit_times
    assert ("EXIT_FILL", 3_250) in main_audit_times

    # STRESS는 더 긴 청산 지연을 가져 BASE와 다른 시각에 닫힌다.
    assert shadows.account(plan.strategy_id, CostProfile.BASE).open_position is None
    assert shadows.account(plan.strategy_id, CostProfile.STRESS).open_position is not None
    engine.on_book(book(3_750, bids=((str(tp2 + Decimal("0.05")), "100"),), asks=(("107", "100"),)))
    assert shadows.account(plan.strategy_id, CostProfile.STRESS).open_position is None
    assert shadows.account(plan.strategy_id, CostProfile.BASE).current_equity_usdt != Decimal(
        "1000"
    )
    assert shadows.account(plan.strategy_id, CostProfile.STRESS).current_equity_usdt != Decimal(
        "1000"
    )


def test_tp1_trailing_that_is_not_fee_safe_after_fill_rejects_entry_atomically() -> None:
    plan = replace(
        candidate_plan(),
        shadow_eligible=False,
        take_profit_targets=(
            TakeProfitTarget("TP1", Decimal("100.15"), Decimal("0.5")),
            TakeProfitTarget("TP2", Decimal("106"), Decimal("0.5")),
        ),
        trailing_policy=TrailingPolicy(
            policy_id="TP1_FEE_SAFETY_REGRESSION_V1",
            model=TrailingModel.FIXED_RATE,
            activation_rule=TrailingActivationRule.TP1_TRIGGERED,
            activation_r=Decimal("1"),
            partial_tp_required=True,
            retracement_rate=Decimal("0.005"),
        ),
    )
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    engine.offer((plan,), entries_paused=False)
    assert engine.main.pending_entry is not None
    assert engine.main.risk_state.pending_planned_risk > 0

    engine.on_book(
        book(
            1_250,
            asks=(("100.1", "0.001"), ("100.39", "100")),
            bids=(("100.0", "100"),),
        )
    )

    assert engine.main.pending_entry is None
    assert engine.main.position is None
    assert engine.main.entry_orders == []
    assert engine.main.risk_state.pending_planned_risk == 0
    assert engine.main.risk_state.pending_notional == 0
    assert engine.main.risk_state.open_positions == 0
    rejection = next(
        row
        for row in engine.audit_events
        if row["event"] == "ENTRY_REJECTED" and row["account_id"] == "MAIN:BASE"
    )
    assert rejection["error_type"] == "TrailingActivationNotFeeSafeError"
    assert rejection["reason_codes"] == ["TRAILING_ACTIVATION_NOT_FEE_SAFE"]


def test_trailing_breakeven_includes_the_preregistered_cost_buffer() -> None:
    plan = replace(
        candidate_plan(),
        shadow_eligible=False,
        trailing_policy=TrailingPolicy(
            policy_id="COST_COVERED_BREAKEVEN_BUFFER_V1",
            model=TrailingModel.ATR_CHANDELIER,
            activation_rule=TrailingActivationRule.TP1_TRIGGERED,
            activation_r=Decimal("1.5"),
            partial_tp_required=True,
            breakeven_buffer_bps=Decimal("1"),
            atr_multiplier=Decimal("2.5"),
        ),
        trailing_atr=Decimal("1"),
        trailing_reference_ts_ms=1_000,
        trailing_reference_interval_seconds=60,
    )
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    engine.offer((plan,), entries_paused=False)
    engine.on_book(book(1_250))

    managed = engine.main.position
    assert managed is not None
    assert managed.trailing_machine is not None
    entry = managed.protected.entry_fill.average_price
    assert managed.trailing_machine.fee_adjusted_breakeven == (
        entry * (Decimal(1) + Decimal("13") / Decimal(10_000))
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
    adverse_book = book(
        123_000,
        bids=(("99.5", "100"),),
        asks=(("99.6", "100"),),
    )
    engine.evaluate_health(adverse, Regime.SHOCK, now_ms=123_000, book=adverse_book)
    assert engine.main.position.pending_exit is None
    engine.evaluate_health(adverse, Regime.SHOCK, now_ms=125_999, book=adverse_book)
    assert engine.main.position.pending_exit is None
    engine.evaluate_health(adverse, Regime.SHOCK, now_ms=126_000, book=adverse_book)
    assert engine.main.position.pending_exit is not None
    assert engine.main.position.pending_exit.label == "EXIT_EDGE_DECAY"
    assert any(
        row["event"] == "MANAGEMENT_EXIT_ARMED"
        and row["account_id"] == engine.main.account_id
        and row["ts_ms"] == 126_000
        for row in engine.audit_events
    )

    engine.on_book(book(126_250))
    assert engine.main.position is None
    assert any(
        row["event"] == "EXIT_FILL"
        and row["account_id"] == engine.main.account_id
        and row["ts_ms"] == 126_250
        for row in engine.audit_events
    )


def test_transient_one_r_spike_does_not_create_a_three_second_breakeven_stop() -> None:
    plan = candidate_plan()
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    engine.offer((plan,), entries_paused=False)
    engine.on_book(book(1_250))
    managed = engine.main.position
    assert managed is not None

    favorable = replace(features(), mid=101.31, microprice=101.32)
    engine.evaluate_health(favorable, Regime.RANGE, now_ms=2_000)
    assert managed.protected.current_stop == plan.initial_stop

    retraced = replace(features(), mid=101.09, microprice=101.10)
    engine.evaluate_health(retraced, Regime.RANGE, now_ms=2_800)
    assert managed.protected.current_stop == plan.initial_stop

    engine.on_book(book(3_000, bids=(("100.0", "100"),), asks=(("100.1", "100"),)))
    assert engine.main.position is managed
    assert managed.pending_exit is None


def test_stop_trade_records_entry_to_stop_duration_without_inventing_targets() -> None:
    plan = candidate_plan()
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    engine.offer((plan,), entries_paused=False)
    engine.on_book(book(1_250))
    engine.on_book(book(2_000, bids=(("98.9", "100"),), asks=(("99.0", "100"),)))
    engine.on_book(book(2_250, bids=(("98.8", "100"),), asks=(("98.9", "100"),)))

    trade = engine.main.completed_trades[0]
    assert trade.exit_reason is ExitReason.STOP
    assert trade.tp1_hit_ts_ms is None
    assert trade.tp2_hit_ts_ms is None
    assert trade.time_to_tp1_ms is None
    assert trade.time_to_tp2_ms is None
    assert trade.time_to_stop_ms == 1_000
    assert trade.holding_ms == 1_000


def test_paper_lifecycle_audit_is_normalized_and_revision_survives_recovery() -> None:
    plan = candidate_plan()
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )

    engine.offer((plan,), entries_paused=False)
    selected = next(row for row in engine.audit_events if row["event"] == "MAIN_CANDIDATE_SELECTED")
    assert selected["previous_state"] == "SCANNING"
    assert selected["new_state"] == "ENTRY_PENDING"
    assert selected["occurred_ts_ms"] == plan.signal_time_ms
    assert selected["cause_code"] == "MAIN_CANDIDATE_SELECTED"
    assert selected["actor"] == "AUTO_SAFETY"
    assert selected["run_id"] == plan.run_id
    assert selected["strategy_id"] == plan.strategy_id
    assert selected["account_id"] == engine.main.account_id
    assert selected["symbol"] == plan.symbol
    assert selected["request_revision"] == 0
    assert selected["response_revision"] == 1
    assert selected["reversible"] is True
    assert selected["transition_id"] == (
        f"paper-execution-{plan.run_id}-{engine.main.account_id}-{plan.symbol}-rev-1"
    )
    assert selected["description_ko"]

    engine.on_book(book(1_250))
    filled = next(
        row
        for row in engine.audit_events
        if row["event"] == "ENTRY_FILLED" and row["account_id"] == engine.main.account_id
    )
    assert filled["previous_state"] == "ENTRY_PENDING"
    assert filled["new_state"] == "PROTECTED"
    assert filled["occurred_ts_ms"] == 1_250
    assert filled["request_revision"] == 1
    assert filled["response_revision"] == 2
    assert filled["reversible"] is False
    assert filled["transition_id"] != selected["transition_id"]

    recovered_payload = json.loads(json.dumps(engine.recovery_state()))
    assert recovered_payload["schema_version"] == 5
    restored = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    restored.restore_state(recovered_payload)

    tp1 = plan.take_profit_targets[0].price
    restored.on_book(
        book(2_000, bids=((str(tp1 + Decimal("0.1")), "100"),), asks=(("107", "100"),))
    )
    exit_pending = next(
        row
        for row in restored.audit_events
        if row["event"] == "TAKE_PROFIT_EXIT_PENDING"
        and row["account_id"] == restored.main.account_id
    )
    assert exit_pending["previous_state"] == "PROTECTED"
    assert exit_pending["new_state"] == "EXIT_PENDING"
    assert exit_pending["request_revision"] == 2
    assert exit_pending["response_revision"] == 3
    assert exit_pending["reversible"] is True

    corrupted_payload = json.loads(json.dumps(restored.recovery_state()))
    corrupted_payload["last_execution_transition"]["response_revision"] = 99
    with pytest.raises(ValueError, match="마지막 상태전환"):
        PaperPortfolioEngine(
            run_id=plan.run_id,
            strategy_ids=(plan.strategy_id,),
            shadow_ledger=ShadowLedger((plan.strategy_id,)),
        ).restore_state(corrupted_payload)


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
    engine.on_book(book(2_000, bids=((str(tp1 + Decimal("0.1")), "100"),), asks=(("107", "100"),)))
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


def test_percentage_trailing_runner_is_executable_bid_driven_and_restart_safe() -> None:
    plan = replace(
        candidate_plan(),
        trailing_policy=TrailingPolicy(
            policy_id="PERCENT_1R_HALF_PERCENT_V1",
            model=TrailingModel.FIXED_RATE,
            activation_rule=TrailingActivationRule.R_MULTIPLE,
            activation_r=Decimal("1"),
            partial_tp_required=False,
            retracement_rate=Decimal("0.005"),
        ),
    )
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    engine.offer((plan,), entries_paused=False)
    engine.on_book(book(1_250))
    assert engine.main.position is not None

    engine.on_book(
        book(
            2_000,
            bids=(("102", "100"),),
            asks=(("102.1", "100"),),
            receive_ts_ms=2_015,
        )
    )
    managed = engine.main.position
    assert managed is not None
    assert managed.trailing_machine is not None
    assert managed.trailing_machine.state is TrailingState.RUNNER_ACTIVE
    assert managed.trailing_machine.highest_favorable_bid == Decimal("102")
    assert managed.trailing_machine.current_trail == Decimal("101.490")
    assert managed.protected.current_stop == Decimal("101.490")
    activation_rows = [
        row
        for row in engine.audit_events
        if row["event"] == "TRAILING_STATE_TRANSITION" and row["event_time_ms"] == 2_000
    ]
    assert activation_rows
    assert all(row["receive_time_ms"] == 2_015 for row in activation_rows)

    engine.on_book(book(2_050, bids=(("103", "100"),), asks=(("103.1", "100"),)))
    assert engine.main.position is not None
    assert engine.main.position.trailing_machine is not None
    assert engine.main.position.trailing_machine.current_trail == Decimal("102.485")
    mark_rows = [row for row in engine.audit_events if row["event"] == "TRAILING_MARK_UPDATED"]
    assert {row["account_id"] for row in mark_rows} == {
        "MAIN:BASE",
        f"{plan.strategy_id}:BASE",
    }
    main_mark = next(row for row in mark_rows if row["account_id"] == "MAIN:BASE")
    assert main_mark["current_trail"] == "102.485"
    assert main_mark["state_checksum"] == (engine.main.position.trailing_machine.checksum())

    payload = json.loads(json.dumps(engine.recovery_state()))
    restored = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    restored.restore_state(payload)
    restored_managed = restored.main.position
    assert restored_managed is not None
    assert restored_managed.trailing_machine is not None
    assert restored_managed.trailing_machine.checksum() == (managed.trailing_machine.checksum())
    assert restored.audit_events == []

    restored.on_book(book(2_100, bids=(("102.4", "100"),), asks=(("102.5", "100"),)))
    assert restored.main.position is not None
    assert restored.main.position.pending_exit is not None
    assert restored.main.position.pending_exit.reason is ExitReason.TRAILING_STOP
    assert restored.main.position.trailing_machine is not None
    assert restored.main.position.trailing_machine.state is TrailingState.TRAIL_EXIT_PENDING

    restored.on_book(book(2_349, bids=(("101.3", "100"),), asks=(("101.4", "100"),)))
    assert restored.main.position is not None
    restored.on_book(book(2_350, bids=(("101.2", "100"),), asks=(("101.3", "100"),)))
    assert restored.main.position is None
    trade = restored.main.completed_trades[-1]
    assert trade.exit_reason is ExitReason.TRAILING_STOP
    assert trade.exit_price == Decimal("101.2")
    assert trade.trailing_activation_ts_ms == 2_000
    assert trade.runner_started_ts_ms == 2_000
    assert trade.peak_unrealized_usdt > 0
    assert trade.giveback_usdt > 0
    assert trade.runner_net_pnl_usdt == trade.net_pnl_usdt
    assert trade.trail_trigger_slippage_usdt > 0
    assert trade.trailing_state_checksum is not None
    assert trade.signal_event_id == plan.signal_event_id
    trail_rows = [
        row
        for row in restored.audit_events
        if row["event"] == "TRAILING_STATE_TRANSITION" and row["account_id"] == "MAIN:BASE"
    ]
    assert [row["to_state"] for row in trail_rows] == [
        TrailingState.TRAIL_EXIT_PENDING.value,
        TrailingState.CLOSED.value,
    ]
    assert trade.trailing_state_checksum == trail_rows[-1]["state_checksum"]

    closed_payload = json.loads(json.dumps(restored.recovery_state()))
    closed_restored = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    closed_restored.restore_state(closed_payload)
    assert closed_restored.main.completed_trades[-1] == trade

    malformed_payload = json.loads(json.dumps(closed_payload))
    main_account = next(
        row for row in malformed_payload["accounts"] if row["account_id"] == "MAIN:BASE"
    )
    malformed_trade = main_account["completed_trades"][-1]
    malformed_trade["runner_started_ts_ms"] = 1_999
    malformed_restored = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    with pytest.raises(ValueError, match="runner"):
        malformed_restored.restore_state(malformed_payload)


def test_edge_adaptive_trailing_requires_two_adverse_signals_for_three_seconds() -> None:
    plan = replace(
        candidate_plan(),
        trailing_policy=TrailingPolicy(
            policy_id="EDGE_ADAPTIVE_2_SIGNALS_3S_V1",
            model=TrailingModel.EDGE_ADAPTIVE,
            activation_rule=TrailingActivationRule.R_MULTIPLE,
            activation_r=Decimal("1"),
            partial_tp_required=False,
            atr_multiplier=Decimal("2"),
            adverse_atr_multiplier=Decimal("1.2"),
            adverse_signal_count=2,
            adverse_persistence_ms=3_000,
        ),
        trailing_atr=Decimal("1"),
        trailing_reference_ts_ms=1_000,
        trailing_reference_interval_seconds=60,
    )
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    engine.offer((plan,), entries_paused=False)
    engine.on_book(book(1_250))
    engine.on_book(book(2_000, bids=(("102", "100"),), asks=(("102.1", "100"),)))
    managed = engine.main.position
    assert managed is not None
    assert managed.trailing_machine is not None
    assert managed.trailing_machine.current_trail == managed.trailing_machine.fee_adjusted_breakeven

    adverse = replace(
        features(),
        data_healthy=True,
        mid=100.5,
        microprice=100.6,
        ofi_3s=-3,
        trade_imbalance_3s=-0.3,
        spread_bps=2,
    )
    engine.evaluate_health(adverse, Regime.RANGE, now_ms=2_100)
    assert managed.trailing_adverse_reason_count == 2
    assert managed.trailing_adverse_active is False
    engine.evaluate_health(adverse, Regime.RANGE, now_ms=5_099)
    assert managed.trailing_adverse_active is False
    engine.evaluate_health(adverse, Regime.RANGE, now_ms=5_100)
    assert managed.trailing_adverse_active is True

    engine.on_book(book(5_200, bids=(("103", "100"),), asks=(("103.1", "100"),)))
    assert managed.trailing_machine.current_trail == Decimal("101.8")
    snapshot = engine.main_position_snapshot(
        book(5_200, bids=(("103", "100"),), asks=(("103.1", "100"),))
    )
    assert snapshot is not None
    trailing_view = snapshot["trailing"]
    assert isinstance(trailing_view, dict)
    assert trailing_view["state"] == "RUNNER_ACTIVE"
    assert trailing_view["current_trail"] == "101.8"
    assert trailing_view["adverse_active"] is True
    assert trailing_view["adverse_reasons"] == [
        "OFI_ADVERSE",
        "AGGRESSOR_FLOW_ADVERSE",
    ]
    assert trailing_view["reference_ts_ms"] == 1_000
    assert trailing_view["reference_interval_seconds"] == 60
    edge_rows = [
        row for row in engine.audit_events if row["event"] == "TRAILING_EDGE_STATE_UPDATED"
    ]
    assert edge_rows[-1]["adverse_active"] is True
    assert edge_rows[-1]["adverse_reason_count"] == 2

    payload = json.loads(json.dumps(engine.recovery_state()))
    restored = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    restored.restore_state(payload)
    restored_position = restored.main.position
    assert restored_position is not None
    assert restored_position.trailing_adverse_since_ms == 2_100
    assert restored_position.trailing_adverse_active is True
    assert restored_position.trailing_adverse_reasons == (
        "OFI_ADVERSE",
        "AGGRESSOR_FLOW_ADVERSE",
    )


def test_recovery_rejects_malformed_adaptive_trailing_state() -> None:
    plan = replace(
        candidate_plan(),
        trailing_policy=TrailingPolicy(
            policy_id="EDGE_ADAPTIVE_RECOVERY_V1",
            model=TrailingModel.EDGE_ADAPTIVE,
            activation_rule=TrailingActivationRule.R_MULTIPLE,
            activation_r=Decimal("1"),
            partial_tp_required=False,
            atr_multiplier=Decimal("2"),
            adverse_atr_multiplier=Decimal("1.2"),
        ),
        trailing_atr=Decimal("1"),
        trailing_reference_ts_ms=1_000,
        trailing_reference_interval_seconds=60,
    )
    engine = PaperPortfolioEngine(
        run_id=plan.run_id,
        strategy_ids=(plan.strategy_id,),
        shadow_ledger=ShadowLedger((plan.strategy_id,)),
    )
    engine.offer((plan,), entries_paused=False)
    engine.on_book(book(1_250))
    payload = json.loads(json.dumps(engine.recovery_state()))
    main_payload = next(
        row for row in payload["accounts"] if row["account_id"] == engine.main.account_id
    )
    main_payload["positions"]["BTCUSDT"]["trailing_adverse_active"] = "false"

    with pytest.raises(ValueError, match="boolean"):
        PaperPortfolioEngine(
            run_id=plan.run_id,
            strategy_ids=(plan.strategy_id,),
            shadow_ledger=ShadowLedger((plan.strategy_id,)),
        ).restore_state(payload)
