"""Registry 기반 독립 계좌의 다중 포지션·위험·비용·복구 계약을 검증한다."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.candidates import CandidatePlan, CandidatePlanner
from backend.app.costing import CostModel, CostProfile
from backend.app.domain.market import Instrument
from backend.app.domain.models import Side, Venue
from backend.app.execution import BookSnapshot
from backend.app.execution.models import ExitReason, OrderIntent
from backend.app.execution.portfolio import PaperPortfolioEngine
from backend.app.regime import Regime
from backend.app.risk import RiskManager, RiskSizingInput, RiskState
from backend.app.strategies.base import CandidateDecision, CandidateStatus
from backend.app.strategies.registry import ExitStyle, StrategyRegistry
from backend.app.strategies.shadow import ShadowLedger
from backend.tests.test_strategies import features


def league_book(
    symbol: str,
    ts_ms: int,
    *,
    bids: tuple[tuple[str, str], ...] = (("99.9", "100"), ("99.8", "100")),
    asks: tuple[tuple[str, str], ...] = (("100.1", "100"), ("100.2", "100")),
) -> BookSnapshot:
    return BookSnapshot(
        venue=Venue.FIXTURE,
        symbol=symbol,
        ts_ms=ts_ms,
        bids=tuple((Decimal(price), Decimal(quantity)) for price, quantity in bids),
        asks=tuple((Decimal(price), Decimal(quantity)) for price, quantity in asks),
    )


def league_plan(
    strategy_id: str,
    symbol: str,
    side: Side = Side.LONG,
    *,
    signal_time_ms: int = 1_000,
) -> CandidatePlan:
    decision = CandidateDecision(
        strategy_id=strategy_id,
        side=side,
        status=CandidateStatus.QUALIFIED,
        reason_codes=("STRUCTURE_CONFIRMED", "FLOW_CONFIRMED"),
        rejection_codes=(),
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99") if side is Side.LONG else Decimal("101"),
        take_profit=Decimal("106") if side is Side.LONG else Decimal("94"),
        expected_cost_bps=Decimal("13"),
        net_reward_risk=Decimal("4"),
    )
    instrument = Instrument(
        venue=Venue.FIXTURE,
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        status="TEST",
        contract_type="PAPER",
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
    )
    result = CandidatePlanner().build(
        signal_event_id=f"signal-{strategy_id}-{symbol}-{side.value}",
        run_id="run-league",
        venue=Venue.FIXTURE,
        decision=decision,
        snapshot=replace(
            features(),
            symbol=symbol,
            ts_ms=signal_time_ms,
            micro_vwap_10s=100.01 if side is Side.LONG else 99.99,
        ),
        regime=(
            Regime.RANGE
            if StrategyRegistry().descriptor(strategy_id).exit_style is ExitStyle.REVERSION_70_30
            else Regime.TREND_UP
            if side is Side.LONG
            else Regime.TREND_DOWN
        ),
        book=league_book(symbol, signal_time_ms),
        instrument=instrument,
        signal_time_ms=signal_time_ms,
        risk_state=RiskState(),
        main_eligible=True,
        shadow_eligible=True,
        exit_style=StrategyRegistry().descriptor(strategy_id).exit_style,
    )
    assert result.plan is not None, result.rejection_codes
    return result.plan


def league_engine() -> PaperPortfolioEngine:
    strategy_ids = StrategyRegistry().strategy_ids
    return PaperPortfolioEngine(
        run_id="run-league",
        strategy_ids=strategy_ids,
        shadow_ledger=ShadowLedger(strategy_ids),
        venue=Venue.FIXTURE,
    )


def test_registry_builds_dynamic_independent_thousand_usdt_accounts() -> None:
    engine = league_engine()
    assert len(engine.shadows) == len(StrategyRegistry().strategy_ids) * len(CostProfile)
    assert {account.account_id for account in engine.shadows.values()} == {
        f"{strategy_id}:{profile.value}"
        for strategy_id in StrategyRegistry().strategy_ids
        for profile in CostProfile
    }
    assert all(
        account.risk_state.starting_equity == Decimal("1000") for account in engine.shadows.values()
    )
    assert all(account.max_positions == 3 for account in engine.shadows.values())
    assert engine.main.max_positions == 1


def test_different_strategies_can_hold_opposite_btc_positions_without_competing() -> None:
    engine = league_engine()
    long_plan = league_plan("LSA_REVERSAL_V1", "BTCUSDT", Side.LONG)
    short_plan = league_plan("CBR_CONTINUATION_V1", "BTCUSDT", Side.SHORT)
    engine.offer((long_plan, short_plan), entries_paused=False)
    lsa = engine.shadows["LSA_REVERSAL_V1:BASE"]
    cbr = engine.shadows["CBR_CONTINUATION_V1:BASE"]
    assert "BTCUSDT" in lsa.pending_entries
    assert "BTCUSDT" in cbr.pending_entries

    engine.on_book(league_book("BTCUSDT", 1_250))
    assert lsa.positions["BTCUSDT"].plan.direction is Side.LONG
    assert cbr.positions["BTCUSDT"].plan.direction is Side.SHORT
    assert len(engine.main.positions) == 1


def test_duplicate_symbol_is_rejected_and_three_symbol_cap_is_enforced() -> None:
    engine = league_engine()
    symbols = ("ADAUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT")
    plans = tuple(league_plan("LSA_REVERSAL_V1", symbol) for symbol in symbols)
    engine.offer(plans, entries_paused=False)
    account = engine.shadows["LSA_REVERSAL_V1:BASE"]
    assert len(account.pending_entries) == 3
    assert any(
        row["event"] == "LEAGUE_MAX_POSITIONS_REJECTED" and row["account_id"] == account.account_id
        for row in engine.audit_events
    )

    duplicate = league_plan("LSA_REVERSAL_V1", sorted(account.pending_entries)[0])
    engine.offer((duplicate,), entries_paused=False)
    assert len(account.pending_entries) == 3
    assert any(
        row["event"] == "LEAGUE_DUPLICATE_SYMBOL_REJECTED"
        and row["account_id"] == account.account_id
        for row in engine.audit_events
    )

    for symbol in tuple(account.pending_entries):
        engine.on_book(league_book(symbol, 1_250))
    assert len(account.positions) == 3


def test_account_loss_and_cooldown_do_not_leak_to_another_account() -> None:
    engine = league_engine()
    losing = engine.shadows["LSA_REVERSAL_V1:BASE"]
    isolated = engine.shadows["CBR_CONTINUATION_V1:BASE"]
    engine.league_risk_manager.record_open(
        losing.risk_state,
        planned_risk=Decimal("5"),
        notional=Decimal("500"),
        effective_leverage=Decimal("0.5"),
    )
    engine.league_risk_manager.record_close(
        losing.risk_state,
        Decimal("-20"),
        key="BTCUSDT:LSA_REVERSAL_V1",
        now_ms=10_000,
        planned_risk=Decimal("5"),
        notional=Decimal("500"),
    )
    assert losing.risk_state.current_equity == Decimal("980")
    assert "DAILY_LOSS_LOCK" in engine.league_risk_manager.entry_rejections(
        losing.risk_state,
        "ETHUSDT:LSA_REVERSAL_V1",
        10_001,
    )
    assert isolated.risk_state.current_equity == Decimal("1000")
    assert (
        engine.league_risk_manager.entry_rejections(
            isolated.risk_state,
            "ETHUSDT:CBR_CONTINUATION_V1",
            10_001,
        )
        == ()
    )


def test_system_entry_lock_blocks_main_and_all_league_accounts() -> None:
    engine = league_engine()
    plans = (
        league_plan("LSA_REVERSAL_V1", "BTCUSDT"),
        league_plan("CBR_CONTINUATION_V1", "ETHUSDT"),
    )
    engine.offer(plans, entries_paused=True)
    assert not engine.main.pending_entries
    assert all(not account.pending_entries for account in engine.shadows.values())
    assert engine.audit_events[-1]["event"] == "SYSTEM_ENTRY_PAUSED"


def test_league_risk_sizing_fees_leverage_and_total_risk_limit() -> None:
    model = CostModel()
    assert model.fee(Decimal("5000"), entry=True, profile=CostProfile.BASE) == Decimal("3")
    assert model.fee(Decimal("5000"), entry=False, profile=CostProfile.BASE) == Decimal("3")
    manager = RiskManager(engine_limits := league_engine().league_risk_manager.limits)
    result = manager.size(
        RiskSizingInput(
            equity=Decimal("1000"),
            entry_price=Decimal("100"),
            stop_price=Decimal("99"),
            entry_fee_per_unit=Decimal("0.06"),
            stop_fee_per_unit=Decimal("0.06"),
            p95_exit_slippage_per_unit=Decimal("0.03"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            executable_depth_quantity=Decimal("1000"),
        )
    )
    assert result.quantity is not None and result.planned_loss is not None
    assert result.planned_loss <= Decimal("5")
    assert result.quantity * Decimal("100") / Decimal("1000") <= Decimal("5")
    assert engine_limits.maximum_gross_notional_fraction == Decimal("5.0")

    state = RiskState(pending_planned_risk=Decimal("10"), pending_notional=Decimal("1000"))
    assert (
        manager.pending_rejections(
            state,
            planned_risk=Decimal("5"),
            planned_notional=Decimal("500"),
        )
        == ()
    )
    assert "MAXIMUM_TOTAL_OPEN_RISK" in manager.pending_rejections(
        state,
        planned_risk=Decimal("5.001"),
        planned_notional=Decimal("500"),
    )


def test_base_stress_latency_partial_fill_fee_and_risk_use_actual_quantity() -> None:
    engine = league_engine()
    plan = league_plan("LSA_REVERSAL_V1", "BTCUSDT")
    engine.offer((plan,), entries_paused=False)
    partial = league_book(
        "BTCUSDT",
        1_250,
        bids=(("99.9", "100"),),
        asks=(("100.1", "0.005"), ("100.5", "100")),
    )
    engine.on_book(partial)
    base = engine.shadows["LSA_REVERSAL_V1:BASE"]
    stress = engine.shadows["LSA_REVERSAL_V1:STRESS"]
    assert "BTCUSDT" in base.positions
    assert "BTCUSDT" not in stress.positions
    managed = base.positions["BTCUSDT"]
    fill = managed.protected.entry_fill
    assert fill.quantity == Decimal("0.005")
    assert fill.fee_usdt == fill.notional * Decimal("6") / Decimal("10000")
    expected_risk = managed.plan.max_planned_loss * fill.quantity / managed.plan.position_size
    assert base.risk_state.open_planned_risk == expected_risk
    assert base.risk_state.pending_planned_risk == 0
    assert base.risk_state.pending_notional == 0
    assert base.risk_state.gross_notional == fill.notional

    engine.on_book(replace(partial, ts_ms=1_500))
    assert "BTCUSDT" in stress.positions
    assert (
        stress.positions["BTCUSDT"].protected.entry_fill.fee_usdt
        > base.positions["BTCUSDT"].protected.entry_fill.fee_usdt
    )


def test_exit_styles_have_exact_fractions_and_trend_tp1_never_widens_stop() -> None:
    reversion = league_plan("LSA_REVERSAL_V1", "BTCUSDT")
    trend = league_plan("CBR_CONTINUATION_V1", "ETHUSDT")
    assert [target.quantity_fraction for target in reversion.take_profit_targets] == [
        Decimal("0.70"),
        Decimal("0.30"),
    ]
    assert [target.quantity_fraction for target in trend.take_profit_targets] == [
        Decimal("0.40"),
        Decimal("0.60"),
    ]

    engine = league_engine()
    engine.offer((trend,), entries_paused=False)
    engine.on_book(league_book("ETHUSDT", 1_250))
    account = engine.shadows["CBR_CONTINUATION_V1:BASE"]
    managed = account.positions["ETHUSDT"]
    initial_stop = managed.protected.current_stop
    tp1 = trend.take_profit_targets[0].price
    engine.on_book(
        league_book(
            "ETHUSDT",
            2_000,
            bids=((str(tp1 + Decimal("0.1")), "100"),),
            asks=((str(tp1 + Decimal("0.2")), "100"),),
        )
    )
    engine.on_book(
        league_book(
            "ETHUSDT",
            2_250,
            bids=((str(tp1 + Decimal("0.05")), "100"),),
            asks=((str(tp1 + Decimal("0.15")), "100"),),
        )
    )
    managed = account.positions["ETHUSDT"]
    assert managed.remaining_quantity < managed.original_quantity
    assert managed.protected.current_stop >= initial_stop
    assert managed.protected.current_stop > managed.protected.entry_fill.average_price
    adverse = replace(
        features(),
        symbol="ETHUSDT",
        ofi_3s=-3,
        trade_imbalance_3s=-0.8,
        microprice=99.8,
    )
    adverse_book = league_book(
        "ETHUSDT",
        31_250,
        bids=(("99.5", "100"),),
        asks=(("99.6", "100"),),
    )
    engine.evaluate_health(adverse, Regime.SHOCK, now_ms=31_249, book=adverse_book)
    assert managed.pending_exit is None
    engine.evaluate_health(adverse, Regime.SHOCK, now_ms=31_250, book=adverse_book)
    assert managed.pending_exit is None
    engine.evaluate_health(adverse, Regime.SHOCK, now_ms=34_249, book=adverse_book)
    assert managed.pending_exit is None
    engine.evaluate_health(adverse, Regime.SHOCK, now_ms=34_250, book=adverse_book)
    assert managed.pending_exit is not None
    assert managed.pending_exit.label == "EXIT_EDGE_DECAY"


def _trigger_book(
    plan: CandidatePlan,
    ts_ms: int,
    price: Decimal,
) -> BookSnapshot:
    if plan.direction is Side.LONG:
        bids = ((str(price + Decimal("0.1")), "100"),)
        asks = ((str(price + Decimal("0.2")), "100"),)
    else:
        bids = ((str(price - Decimal("0.2")), "100"),)
        asks = ((str(price - Decimal("0.1")), "100"),)
    return league_book(plan.symbol, ts_ms, bids=bids, asks=asks)


def _stop_book(plan: CandidatePlan, ts_ms: int) -> BookSnapshot:
    stop = plan.initial_stop
    if plan.direction is Side.LONG:
        bids = ((str(stop - Decimal("0.1")), "100"),)
        asks = ((str(stop + Decimal("0.1")), "100"),)
    else:
        bids = ((str(stop - Decimal("0.1")), "100"),)
        asks = ((str(stop + Decimal("0.1")), "100"),)
    return league_book(plan.symbol, ts_ms, bids=bids, asks=asks)


@pytest.mark.parametrize("strategy_id", StrategyRegistry().strategy_ids)
@pytest.mark.parametrize("side", (Side.LONG, Side.SHORT))
@pytest.mark.parametrize("outcome", ("TAKE_PROFIT", "STOP"))
def test_every_strategy_runs_entry_protection_and_exit_end_to_end(
    strategy_id: str,
    side: Side,
    outcome: str,
) -> None:
    """A-J의 양방향 진입과 자동 TP1·TP2·SL 보호를 같은 PAPER 엔진으로 검증한다."""

    engine = league_engine()
    plan = league_plan(strategy_id, "BTCUSDT", side)
    expected_fractions = (
        [Decimal("0.70"), Decimal("0.30")]
        if plan.exit_style is ExitStyle.REVERSION_70_30
        else [Decimal("0.40"), Decimal("0.60")]
    )
    assert [target.quantity_fraction for target in plan.take_profit_targets] == expected_fractions
    assert plan.max_planned_loss <= plan.risk_budget
    assert plan.net_reward_risk >= Decimal("1.20")

    engine.offer((plan,), entries_paused=False)
    engine.on_book(league_book(plan.symbol, 1_250))
    engine.on_book(league_book(plan.symbol, 1_500))
    accounts = (
        engine.main,
        engine.shadows[f"{strategy_id}:BASE"],
        engine.shadows[f"{strategy_id}:STRESS"],
    )
    for account in accounts:
        managed = account.positions[plan.symbol]
        assert {order.intent for order in managed.protected.protection_orders} == {
            OrderIntent.TAKE_PROFIT,
            OrderIntent.STOP_EXIT,
        }
        assert managed.protected.current_stop == plan.initial_stop

    if outcome == "TAKE_PROFIT":
        tp1 = plan.take_profit_targets[0].price
        for ts_ms in (2_000, 2_250, 2_500):
            engine.on_book(_trigger_book(plan, ts_ms, tp1))
        tp2 = plan.take_profit_targets[1].price
        for ts_ms in (3_000, 3_250, 3_500):
            engine.on_book(_trigger_book(plan, ts_ms, tp2))
        expected_reason = ExitReason.TAKE_PROFIT
        expected_flags = ("TP1", "TP2")
    else:
        for ts_ms in (2_000, 2_250, 2_500):
            engine.on_book(_stop_book(plan, ts_ms))
        expected_reason = ExitReason.STOP
        expected_flags = ("STOP_LOSS",)

    for account in accounts:
        assert plan.symbol not in account.positions
        assert len(account.completed_trades) == 1
        trade = account.completed_trades[0]
        assert trade.strategy_id == strategy_id
        assert trade.side is side
        assert trade.exit_reason is expected_reason
        assert trade.flags == expected_flags
        assert trade.net_pnl_usdt == (trade.gross_pnl_usdt - trade.fees_usdt - trade.slippage_usdt)
        assert (trade.net_pnl_usdt > 0) is (outcome == "TAKE_PROFIT")

    base_trade = engine.shadow_ledger.account(strategy_id, CostProfile.BASE).trades[0]
    stress_trade = engine.shadow_ledger.account(strategy_id, CostProfile.STRESS).trades[0]
    assert stress_trade.fees_usdt > base_trade.fees_usdt
    assert stress_trade.slippage_usdt >= base_trade.slippage_usdt


def test_multiple_pending_and_positions_recovery_roundtrip() -> None:
    engine = league_engine()
    plans = tuple(
        league_plan("LSA_REVERSAL_V1", symbol) for symbol in ("ADAUSDT", "BTCUSDT", "ETHUSDT")
    )
    engine.offer(plans, entries_paused=False)
    for symbol in ("ADAUSDT", "BTCUSDT"):
        engine.on_book(league_book(symbol, 1_250))
    payload = engine.recovery_state(
        registry_settings=StrategyRegistry().rows(),
        snapshot_ts_ms=1_300,
    )

    restored = league_engine()
    restored.restore_state(payload)
    assert (
        restored.recovery_state(
            registry_settings=StrategyRegistry().rows(),
            snapshot_ts_ms=1_300,
        )
        == payload
    )
    account = restored.shadows["LSA_REVERSAL_V1:BASE"]
    assert set(account.positions) == {"ADAUSDT", "BTCUSDT"}
    assert set(account.pending_entries) == {"ETHUSDT"}
    assert account.risk_state.open_positions == 2
    assert account.risk_state.open_planned_risk > 0
    assert account.risk_state.gross_notional > 0


def test_schema_v1_single_position_payload_is_migrated_without_new_accounts() -> None:
    engine = league_engine()
    engine.offer((league_plan("LSA_REVERSAL_V1", "BTCUSDT"),), entries_paused=False)
    payload = engine.recovery_state()
    payload["schema_version"] = 1
    payload.pop("venue", None)
    payload.pop("snapshot_ts_ms", None)
    payload.pop("strategy_registry", None)
    legacy_ids = set(StrategyRegistry().strategy_ids[:4])
    legacy_accounts = []
    for raw in payload["accounts"]:
        row = dict(raw)
        strategy_id = str(row["account_id"]).split(":", 1)[0]
        if row["account_id"] != "MAIN:BASE" and strategy_id not in legacy_ids:
            continue
        if row["account_id"] != "MAIN:BASE":
            row["account_id"] = f"SHADOW:{row['account_id']}"
        row.pop("max_positions", None)
        pending_map = row.pop("pending_entries")
        row["pending_entry"] = next(iter(pending_map.values()), None)
        position_map = row.pop("positions")
        row["position"] = next(iter(position_map.values()), None)
        for plan_payload in (row["pending_entry"],):
            if isinstance(plan_payload, dict):
                plan_payload.pop("exit_style", None)
                plan_payload.pop("quantity_step", None)
                plan_payload.pop("executable_depth_quantity", None)
        for key in (
            "open_planned_risk",
            "pending_planned_risk",
            "gross_notional",
            "pending_notional",
            "maximum_effective_leverage",
        ):
            row["risk_state"].pop(key, None)
        legacy_accounts.append(row)
    payload["accounts"] = legacy_accounts
    shadow_accounts = []
    for raw in payload["shadow_ledger"]["accounts"]:
        if raw["strategy_id"] not in legacy_ids:
            continue
        row = dict(raw)
        positions = row.pop("open_positions")
        row["open_position"] = next(iter(positions.values()), None)
        shadow_accounts.append(row)
    payload["shadow_ledger"]["accounts"] = shadow_accounts

    restored = league_engine()
    restored.restore_state(payload)
    assert restored.main.pending_entry is not None
    assert restored.main.pending_entry.plan.symbol == "BTCUSDT"
    assert restored.shadows[
        "QUEUE_MICROPRICE_MOMENTUM_V1:BASE"
    ].risk_state.current_equity == Decimal("1000")


def test_schema_v2_additive_strategy_accounts_start_empty_without_rejecting_recovery() -> None:
    registry = StrategyRegistry()
    legacy_ids = registry.strategy_ids[:-1]
    legacy = PaperPortfolioEngine(
        run_id="run-league",
        strategy_ids=legacy_ids,
        shadow_ledger=ShadowLedger(legacy_ids),
        venue=Venue.FIXTURE,
    )
    payload = legacy.recovery_state(
        registry_settings=registry.rows()[:-1],
        snapshot_ts_ms=2_000,
    )
    payload["schema_version"] = 2

    restored = league_engine()
    restored.restore_state(payload)

    for profile in CostProfile:
        account = restored.shadows[f"HOURLY_MOMENTUM_BREAKOUT_V1:{profile.value}"]
        assert account.risk_state.current_equity == Decimal("1000")
        assert account.pending_entries == {}
        assert account.positions == {}
        shadow = restored.shadow_ledger.account(
            "HOURLY_MOMENTUM_BREAKOUT_V1",
            profile,
        )
        assert shadow.current_equity_usdt == Decimal("1000")
        assert shadow.trades == []


def test_current_snapshot_rejects_missing_existing_profile_account() -> None:
    engine = league_engine()
    payload = engine.recovery_state(
        registry_settings=StrategyRegistry().rows(),
        snapshot_ts_ms=2_000,
    )
    payload["accounts"] = [
        row for row in payload["accounts"] if row["account_id"] != "CBR_CONTINUATION_V1:STRESS"
    ]
    with pytest.raises(ValueError, match="Strategy Registry"):
        league_engine().restore_state(payload)
