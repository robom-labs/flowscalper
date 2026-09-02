"""전략별 구조 손절·TP1·TP2와 TP1 runner의 불변 계약을 검증한다."""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.candidates import CandidatePlanner
from backend.app.domain.models import Side, Venue
from backend.app.execution.portfolio import PaperPortfolioEngine
from backend.app.execution.trailing import TrailingActivationRule, TrailingModel
from backend.app.regime import Regime
from backend.app.risk import RiskState
from backend.app.strategies.base import (
    CandidateDecision,
    CandidateStatus,
    RunnerManagement,
    StructuralExitPlan,
    costed_plan,
)
from backend.app.strategies.exit_structure import (
    compression_breakout_exit_plan,
    intraday_structural_exit_plan,
    vwap_reversion_exit_plan,
)
from backend.app.strategies.intraday_trend import (
    IntradayTrendVariant,
    intraday_trend_state,
)
from backend.app.strategies.registry import ExitStyle, StrategyRegistry
from backend.app.strategies.shadow import ShadowLedger
from backend.tests.test_candidate_paper_portfolio import book, instrument
from backend.tests.test_intraday_trend_shadow_strategies import _trend_rows
from backend.tests.test_strategies import features

_CURRENT_STRUCTURAL_STRATEGY_IDS = {
    "CBR_CONTINUATION_V1",
    "VWAP_EXHAUSTION_REVERSION_V1",
    "TREND_PULLBACK_RECLAIM_15M_V2",
    "BREAKOUT_RETEST_15M_V2",
    "BREAKOUT_RETEST_30M_V2",
    "MULTISPEED_TREND_RECLAIM_30M_V2",
}


def _path(prices: list[float], *, start_ts_ms: int = 1_000, step_ms: int = 1_000):
    return [
        replace(
            features(),
            ts_ms=start_ts_ms + index * step_ms,
            mid=price,
            microprice=price + 0.01,
            micro_vwap_10s=price,
        )
        for index, price in enumerate(prices)
    ]


def test_every_current_executable_strategy_declares_structure_based_exits() -> None:
    rows = {
        str(row["strategy_id"]): row for row in StrategyRegistry().rows() if row["mode"] != "OFF"
    }

    assert set(rows) == _CURRENT_STRUCTURAL_STRATEGY_IDS
    for row in rows.values():
        rules = " ".join(str(value) for value in row["exit_rules_ko"])
        assert "초기 손절" in rules
        assert "TP1" in rules and "TP2" in rules
        assert "비용" in rules


@pytest.mark.parametrize(
    ("side", "prices", "current"),
    [
        (Side.LONG, [100, 100.4, 101.4, 100.7, 100.8, 100.9], 101.0),
        (Side.SHORT, [100, 99.6, 98.6, 99.3, 99.2, 99.1], 99.0),
    ],
)
def test_compression_exit_uses_observed_pullback_and_measured_impulse(
    side: Side,
    prices: list[float],
    current: float,
) -> None:
    history = _path(prices)
    snapshot = replace(
        features(),
        ts_ms=8_000,
        mid=current,
        microprice=current + (0.01 if side is Side.LONG else -0.01),
        micro_vwap_10s=current,
    )

    plan = compression_breakout_exit_plan(
        history,
        snapshot,
        side,
        tick_size=Decimal("0.01"),
        expected_cost_bps=Decimal("13"),
    )

    assert plan.structural_exit is not None
    assert plan.structural_exit.runner_management is RunnerManagement.TP1_STRUCTURE_DISTANCE
    assert plan.structural_exit.trailing_distance is not None
    assert plan.target == plan.structural_exit.take_profit_2
    assert costed_plan(side, plan)[1] == ()
    if side is Side.LONG:
        assert plan.structural_stop < plan.entry < plan.structural_exit.take_profit_1 < plan.target
    else:
        assert plan.target < plan.structural_exit.take_profit_1 < plan.entry < plan.structural_stop


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_vwap_exit_anchors_targets_to_pre_excursion_range(side: Side) -> None:
    direction = 1 if side is Side.LONG else -1
    range_prices = [100 + direction * (0.4 + (index % 5) * 0.2) for index in range(20)]
    excursion = [100 + direction * value for value in (0.2, -0.2, -0.5, -0.7)]
    history = _path(range_prices + excursion, step_ms=3_000)
    current = 99.5 if side is Side.LONG else 100.5
    snapshot = replace(
        features(),
        ts_ms=80_000,
        mid=current,
        microprice=current + direction * 0.01,
        micro_vwap_10s=100.0,
    )

    plan = vwap_reversion_exit_plan(
        history,
        snapshot,
        side,
        tick_size=Decimal("0.01"),
        expected_cost_bps=Decimal("13"),
    )

    assert plan.structural_exit is not None
    assert plan.structural_exit.runner_management is RunnerManagement.FIXED_SECOND_TARGET
    assert "과도이탈 전" in plan.structural_exit.take_profit_1_rationale_ko
    assert "과도이탈 전" in plan.structural_exit.take_profit_2_rationale_ko
    assert costed_plan(side, plan)[1] == ()


def test_intraday_targets_ignore_future_candle_and_use_completed_reference() -> None:
    base = _trend_rows(1_800, direction=1, count=120, step=0.2)
    hourly = _trend_rows(3_600, direction=1, count=60, step=0.3)
    state = intraday_trend_state(
        base,
        hourly,
        IntradayTrendVariant.BREAKOUT_RETEST_30M,
    )
    assert state.signal_ts_ms is not None
    assert state.structural_stop is not None
    entry = base[-1].close
    first = intraday_structural_exit_plan(
        side=Side.LONG,
        entry=entry,
        structural_stop=Decimal(str(state.structural_stop)),
        expected_cost_bps=Decimal("13"),
        tick_size=Decimal("0.01"),
        signal_ts_ms=state.signal_ts_ms,
        base_candles=base,
        hourly_candles=hourly,
        variant=IntradayTrendVariant.BREAKOUT_RETEST_30M,
    )
    future = replace(
        base[-1],
        open_ts_ms=state.signal_ts_ms,
        high=Decimal("999"),
        low=Decimal("1"),
        close=Decimal("500"),
    )
    second = intraday_structural_exit_plan(
        side=Side.LONG,
        entry=entry,
        structural_stop=Decimal(str(state.structural_stop)),
        expected_cost_bps=Decimal("13"),
        tick_size=Decimal("0.01"),
        signal_ts_ms=state.signal_ts_ms,
        base_candles=(*base, future),
        hourly_candles=hourly,
        variant=IntradayTrendVariant.BREAKOUT_RETEST_30M,
    )

    assert first == second
    assert first.structural_exit is not None
    assert first.structural_exit.runner_management is RunnerManagement.TP1_ATR_CHANDELIER
    assert first.structural_exit.trailing_reference_ts_ms == state.signal_ts_ms
    assert costed_plan(Side.LONG, first)[1] == ()


def test_candidate_planner_freezes_structural_targets_and_tp1_atr_runner() -> None:
    structural = StructuralExitPlan(
        take_profit_1=Decimal("103"),
        take_profit_2=Decimal("106"),
        stop_rationale_ko="완성 15분봉 눌림 저점 바깥",
        take_profit_1_rationale_ko="완성 15분봉 확정 피벗",
        take_profit_2_rationale_ko="완성 1시간봉 확정 피벗",
        reference_timeframes_ko=("완성 15분봉", "완성 1시간봉"),
        runner_management=RunnerManagement.TP1_ATR_CHANDELIER,
        trailing_atr=Decimal("1"),
        trailing_reference_ts_ms=1_000,
        trailing_reference_interval_seconds=900,
    )
    decision = CandidateDecision(
        strategy_id="BREAKOUT_RETEST_15M_V2",
        side=Side.LONG,
        status=CandidateStatus.QUALIFIED,
        reason_codes=("COMPLETED_INTRADAY_TREND_ALIGNED",),
        rejection_codes=(),
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        take_profit=Decimal("106"),
        expected_cost_bps=Decimal("13"),
        net_reward_risk=Decimal("4"),
        structural_exit=structural,
    )

    result = CandidatePlanner().build(
        signal_event_id="signal-structure-1",
        run_id="run-structure-1",
        venue=Venue.FIXTURE,
        decision=decision,
        snapshot=features(),
        regime=Regime.TREND_UP,
        book=book(1_000),
        instrument=instrument(),
        signal_time_ms=1_000,
        risk_state=RiskState(),
        main_eligible=False,
        shadow_eligible=True,
        exit_style=ExitStyle.TREND_40_60,
    )

    assert result.rejection_codes == ()
    assert result.plan is not None
    assert [target.price for target in result.plan.take_profit_targets] == [
        Decimal("103"),
        Decimal("106"),
    ]
    assert result.plan.trailing_policy is not None
    assert result.plan.trailing_policy.model is TrailingModel.ATR_CHANDELIER
    assert result.plan.trailing_policy.activation_rule is TrailingActivationRule.TP1_TRIGGERED
    assert "TP1_ATR_CHANDELIER_RUNNER" in result.plan.management_policy
    assert any("1차 익절 근거" in row for row in result.plan.plain_korean_explanation)


def test_live_book_past_tp1_rejects_instead_of_inventing_new_target() -> None:
    structural = StructuralExitPlan(
        take_profit_1=Decimal("100.2"),
        take_profit_2=Decimal("104"),
        stop_rationale_ko="구조 손절",
        take_profit_1_rationale_ko="첫 저항",
        take_profit_2_rationale_ko="다음 저항",
        reference_timeframes_ko=("완성 15분봉",),
        runner_management=RunnerManagement.FIXED_SECOND_TARGET,
    )
    decision = CandidateDecision(
        strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
        side=Side.LONG,
        status=CandidateStatus.QUALIFIED,
        reason_codes=("STRUCTURE_REENTERED",),
        rejection_codes=(),
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        take_profit=Decimal("104"),
        expected_cost_bps=Decimal("13"),
        net_reward_risk=Decimal("3"),
        structural_exit=structural,
    )

    result = CandidatePlanner().build(
        signal_event_id="signal-too-late",
        run_id="run-too-late",
        venue=Venue.FIXTURE,
        decision=decision,
        snapshot=features(),
        regime=Regime.RANGE,
        book=book(1_000, asks=(("100.3", "100"), ("100.4", "100"))),
        instrument=instrument(),
        signal_time_ms=1_000,
        risk_state=RiskState(),
        main_eligible=False,
        shadow_eligible=True,
        exit_style=ExitStyle.REVERSION_70_30,
    )

    assert result.plan is None
    assert result.rejection_codes == ("LIVE_BOOK_INVALIDATES_STRUCTURAL_TARGETS",)


def test_structural_price_rationales_survive_portfolio_recovery_and_live_view() -> None:
    structural = StructuralExitPlan(
        take_profit_1=Decimal("103"),
        take_profit_2=Decimal("106"),
        stop_rationale_ko="완성 15분봉 눌림 저점 바깥",
        take_profit_1_rationale_ko="완성 15분봉 확정 피벗",
        take_profit_2_rationale_ko="완성 1시간봉 확정 피벗",
        reference_timeframes_ko=("완성 15분봉", "완성 1시간봉"),
        runner_management=RunnerManagement.FIXED_SECOND_TARGET,
    )
    decision = CandidateDecision(
        strategy_id="BREAKOUT_RETEST_15M_V2",
        side=Side.LONG,
        status=CandidateStatus.QUALIFIED,
        reason_codes=("COMPLETED_INTRADAY_TREND_ALIGNED",),
        rejection_codes=(),
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        take_profit=Decimal("106"),
        expected_cost_bps=Decimal("13"),
        net_reward_risk=Decimal("4"),
        structural_exit=structural,
    )
    built = CandidatePlanner().build(
        signal_event_id="signal-structure-recovery",
        run_id="run-structure-recovery",
        venue=Venue.FIXTURE,
        decision=decision,
        snapshot=features(),
        regime=Regime.TREND_UP,
        book=book(1_000),
        instrument=instrument(),
        signal_time_ms=1_000,
        risk_state=RiskState(),
        main_eligible=False,
        shadow_eligible=True,
        exit_style=ExitStyle.TREND_40_60,
    )
    assert built.plan is not None
    engine = PaperPortfolioEngine(
        run_id=built.plan.run_id,
        strategy_ids=(built.plan.strategy_id,),
        shadow_ledger=ShadowLedger((built.plan.strategy_id,)),
        venue=Venue.FIXTURE,
    )
    engine.offer((built.plan,), entries_paused=False)
    engine.on_book(book(1_250))

    payload = json.loads(json.dumps(engine.recovery_state()))
    restored = PaperPortfolioEngine(
        run_id=built.plan.run_id,
        strategy_ids=(built.plan.strategy_id,),
        shadow_ledger=ShadowLedger((built.plan.strategy_id,)),
        venue=Venue.FIXTURE,
    )
    restored.restore_state(payload)

    recovered = restored.shadows[f"{built.plan.strategy_id}:BASE"].positions[built.plan.symbol].plan
    assert recovered.stop_rationale_ko == structural.stop_rationale_ko
    assert recovered.take_profit_1_rationale_ko == structural.take_profit_1_rationale_ko
    assert recovered.take_profit_2_rationale_ko == structural.take_profit_2_rationale_ko
    assert recovered.reference_timeframes_ko == structural.reference_timeframes_ko
    row = restored.league_position_rows({built.plan.symbol: book(1_500)})[0]
    assert row["stop_rationale_ko"] == structural.stop_rationale_ko
    assert row["reference_timeframes_ko"] == list(structural.reference_timeframes_ko)
