# 상승 상태 모멘텀 리그의 시계열 경계, 비용, 위험감쇠와 선발 계약을 검증한다.

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.research_daily_regime_trend_tournament import (
    DAILY_INTERVAL_MINUTES,
    DAILY_INTERVAL_MS,
)
from scripts.research_multiyear_trend_tournament import FundingRate
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_slow_regime_trend_tournament import SlowTrendOutcome
from scripts.research_state_conditioned_momentum_tournament import (
    BASE_RISK_BUDGET_BPS,
    MAXIMUM_CONCURRENT_POSITIONS,
    MONDAY_OFFSET_MS,
    PREREGISTERED_STATE_MOMENTUM_CANDIDATES,
    WEEKLY_INTERVAL_MINUTES,
    WEEKLY_INTERVAL_MS,
    CandidateTrade,
    WeeklyContext,
    _apply_account_risk_and_costs,
    _candidate_legs,
    _candidate_trade,
    aggregate_daily_to_weekly,
    apply_state_momentum_portfolio_limits,
    build_weekly_contexts,
    entry_open_after_completed_week,
    select_stable_state_momentum_candidates,
    state_momentum_candidate_fingerprint,
    volatility_risk_scale,
)


def _daily_bar(
    index: int,
    *,
    symbol: str = "BTCUSDT",
    open_: float = 100,
    high: float = 101,
    low: float = 99,
    close: float = 100,
) -> IntradayBar:
    return IntradayBar(
        symbol=symbol,
        interval_minutes=DAILY_INTERVAL_MINUTES,
        open_ts_ms=MONDAY_OFFSET_MS + index * DAILY_INTERVAL_MS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
    )


def _weekly_bar(index: int, *, symbol: str, close: float) -> IntradayBar:
    return IntradayBar(
        symbol=symbol,
        interval_minutes=WEEKLY_INTERVAL_MINUTES,
        open_ts_ms=MONDAY_OFFSET_MS + index * WEEKLY_INTERVAL_MS,
        open=close - 1,
        high=close + 2,
        low=close - 2,
        close=close,
        volume=700,
    )


def _context(*, up_up: bool = True) -> WeeklyContext:
    momentum = {"BTCUSDT": 0.08, "ETHUSDT": 0.04, "XRPUSDT": -0.06}
    return WeeklyContext(
        week_open_ts_ms=MONDAY_OFFSET_MS + 14 * WEEKLY_INTERVAL_MS,
        week_close_ts_ms=MONDAY_OFFSET_MS + 15 * WEEKLY_INTERVAL_MS - 1,
        current_market_four_week_return=0.12 if up_up else -0.12,
        previous_market_four_week_return=0.08,
        up_up=up_up,
        momentum_2w_by_symbol=momentum,
        momentum_4w_by_symbol=momentum,
        rank_2w_by_symbol={"BTCUSDT": 1.0, "ETHUSDT": 0.5, "XRPUSDT": 0.0},
        rank_4w_by_symbol={"BTCUSDT": 1.0, "ETHUSDT": 0.5, "XRPUSDT": 0.0},
        weekly_volatility_by_symbol={
            "BTCUSDT": 0.04,
            "ETHUSDT": 0.08,
            "XRPUSDT": 0.16,
        },
        slow_aligned_long_symbols=frozenset({"BTCUSDT"}),
    )


def _outcome(
    symbol: str,
    *,
    entry_ts_ms: int,
    exit_ts_ms: int,
    score: float = 1,
    entry: float = 100,
    stop: float = 98,
    gross_bps: float | None = 100,
    censored: bool = False,
) -> SlowTrendOutcome:
    return SlowTrendOutcome(
        candidate_id="TEST",
        family="TEST",
        symbol=symbol,
        side="LONG",
        signal_ts_ms=entry_ts_ms - 1,
        entry_ts_ms=entry_ts_ms,
        exit_ts_ms=exit_ts_ms,
        holding_minutes=DAILY_INTERVAL_MINUTES,
        exit_reason="CENSORED_OPEN" if censored else "TP2",
        tp1_hit_ts_ms=None if censored else exit_ts_ms,
        tp2_hit_ts_ms=None if censored else exit_ts_ms,
        entry=entry,
        stop=stop,
        take_profit_1=103,
        take_profit_2=108,
        gross_bps=None if censored else gross_bps,
        base_net_bps=None if censored else 87,
        stress_net_bps=None if censored else 75,
        score=score,
        regime_breadth=0.8,
        relative_rank=0.9,
        censored=censored,
    )


def _eligible_profile() -> dict[str, object]:
    development = {
        "sample_size": 80,
        "expectancy_bps": 10.0,
        "profit_factor": 1.5,
    }
    validation = {
        "sample_size": 30,
        "expectancy_bps": 8.0,
        "profit_factor": 1.3,
    }
    return {
        "base": dict(development),
        "stress": dict(development),
        "validation_stress": validation,
    }


def test_state_momentum_tournament_preregisters_30_distinct_candidates() -> None:
    specs = PREREGISTERED_STATE_MOMENTUM_CANDIDATES

    assert len(specs) == 30
    assert len({spec.candidate_id for spec in specs}) == 30
    assert len({spec.family for spec in specs}) == 5
    assert {spec.state_policy for spec in specs} == {
        "UP_UP",
        "ALL_REGIMES",
        "NON_UP_UP",
    }
    assert {spec.risk_style for spec in specs} == {"FIXED_RISK", "VOL_CAPPED"}
    assert sum(spec.is_negative_control for spec in specs) == 10
    assert all(spec.base_risk_budget_bps == BASE_RISK_BUDGET_BPS for spec in specs)


def test_state_momentum_fingerprint_is_deterministic_and_parameter_sensitive() -> None:
    baseline = state_momentum_candidate_fingerprint()
    changed = (
        replace(PREREGISTERED_STATE_MOMENTUM_CANDIDATES[0], tp2_r=8.0),
        *PREREGISTERED_STATE_MOMENTUM_CANDIDATES[1:],
    )

    assert baseline == state_momentum_candidate_fingerprint()
    assert baseline != state_momentum_candidate_fingerprint(changed)


def test_weekly_aggregation_requires_seven_contiguous_monday_utc_daily_bars() -> None:
    complete = tuple(_daily_bar(index) for index in range(7))
    incomplete = tuple(_daily_bar(7 + index) for index in range(6))
    gap = tuple(_daily_bar(14 + index) for index in (0, 1, 2, 3, 4, 5, 7))

    output = aggregate_daily_to_weekly((*complete, *incomplete, *gap))

    assert len(output) == 1
    weekly = output[0]
    assert weekly.open_ts_ms == MONDAY_OFFSET_MS
    assert weekly.open == complete[0].open
    assert weekly.high == max(row.high for row in complete)
    assert weekly.low == min(row.low for row in complete)
    assert weekly.close == complete[-1].close
    assert weekly.volume == sum(row.volume for row in complete)


def test_weekly_context_never_changes_when_only_future_week_is_mutated() -> None:
    original = {
        symbol: tuple(
            _weekly_bar(index, symbol=symbol, close=100 + index * multiplier)
            for index in range(16)
        )
        for symbol, multiplier in (("BTCUSDT", 2.0), ("ETHUSDT", 1.0), ("XRPUSDT", -0.5))
    }
    changed = dict(original)
    changed["BTCUSDT"] = (
        *original["BTCUSDT"][:-1],
        _weekly_bar(15, symbol="BTCUSDT", close=10_000),
    )

    baseline_contexts = build_weekly_contexts(original)
    changed_contexts = build_weekly_contexts(changed)

    assert baseline_contexts[:-1] == changed_contexts[:-1]
    assert baseline_contexts[-1] != changed_contexts[-1]


def test_completed_week_signal_enters_only_at_following_monday_open() -> None:
    context = _context()

    entry_open = entry_open_after_completed_week(context)

    assert entry_open == context.week_open_ts_ms + WEEKLY_INTERVAL_MS
    assert entry_open > context.week_close_ts_ms


def test_candidate_legs_keep_long_winner_short_loser_and_slow_alignment() -> None:
    context = _context()
    winner_spec = PREREGISTERED_STATE_MOMENTUM_CANDIDATES[0]
    wml_spec = next(
        spec
        for spec in PREREGISTERED_STATE_MOMENTUM_CANDIDATES
        if spec.selection_kind == "WINNER_LOSER" and spec.state_policy == "UP_UP"
    )
    aligned_spec = next(
        spec
        for spec in PREREGISTERED_STATE_MOMENTUM_CANDIDATES
        if spec.selection_kind == "WINNERS_LONG_SLOW_ALIGN"
        and spec.state_policy == "UP_UP"
    )

    assert _candidate_legs(context, winner_spec)[:2] == (
        ("BTCUSDT", 1, 0.08, 1.0),
        ("ETHUSDT", 1, 0.04, 0.5),
    )
    wml_legs = {
        (symbol, direction)
        for symbol, direction, _, _ in _candidate_legs(context, wml_spec)
    }
    assert wml_legs == {
        ("BTCUSDT", 1),
        ("XRPUSDT", -1),
    }
    assert _candidate_legs(context, aligned_spec) == (("BTCUSDT", 1, 0.08, 1.0),)


def test_volatility_scaling_can_only_reduce_risk() -> None:
    assert volatility_risk_scale(0) == 1.0
    assert volatility_risk_scale(0.04) == 1.0
    assert volatility_risk_scale(0.08) == 1.0
    assert volatility_risk_scale(0.16) == pytest.approx(0.5)
    assert volatility_risk_scale(10) == 0.25


def test_candidate_trade_enters_next_day_and_applies_same_day_stop_first() -> None:
    rows = tuple(_daily_bar(index) for index in range(15)) + (
        _daily_bar(15, high=110, low=98, close=105),
    )
    spec = PREREGISTERED_STATE_MOMENTUM_CANDIDATES[0]

    trade = _candidate_trade(
        rows,
        signal_index=14,
        direction=1,
        momentum=0.08,
        rank=1.0,
        context=_context(),
        spec=spec,
    )

    assert trade is not None
    assert trade.outcome.entry_ts_ms == rows[15].open_ts_ms
    assert trade.outcome.exit_reason == "STOP"
    assert trade.outcome.tp1_hit_ts_ms is None


def test_portfolio_limits_keep_only_two_highest_score_same_day_positions() -> None:
    entry = 100 * DAILY_INTERVAL_MS
    exit_ = entry + 2 * DAILY_INTERVAL_MS - 1
    trades = tuple(
        CandidateTrade(
            outcome=_outcome(symbol, entry_ts_ms=entry, exit_ts_ms=exit_, score=score),
            risk_scale=1.0,
            momentum=0.1,
        )
        for symbol, score in (("BTCUSDT", 4), ("ETHUSDT", 3), ("XRPUSDT", 2))
    )

    selected = apply_state_momentum_portfolio_limits(trades)

    assert len(selected) == MAXIMUM_CONCURRENT_POSITIONS
    assert [trade.outcome.symbol for trade in selected] == ["BTCUSDT", "ETHUSDT"]


def test_account_risk_and_costs_scale_price_funding_and_execution_costs() -> None:
    entry = DAILY_INTERVAL_MS
    outcome = _outcome(
        "BTCUSDT",
        entry_ts_ms=entry,
        exit_ts_ms=2 * DAILY_INTERVAL_MS - 1,
        gross_bps=100,
    )
    trade = CandidateTrade(outcome=outcome, risk_scale=1.0, momentum=0.1)
    rates = (FundingRate("BTCUSDT", entry + 8 * 3_600_000, 0.0001),)

    revised, audit = _apply_account_risk_and_costs(
        trade,
        rates,
        PREREGISTERED_STATE_MOMENTUM_CANDIDATES[0],
    )

    assert audit["notional_fraction"] == pytest.approx(0.2)
    assert audit["applied_funding_event_count"] == 1
    assert audit["net_funding_cashflow_bps"] == pytest.approx(-0.2)
    assert revised.gross_bps == pytest.approx(19.8)
    assert revised.base_net_bps == pytest.approx(17.2)
    assert revised.stress_net_bps == pytest.approx(14.8)


def test_negative_control_cannot_be_selected_even_with_identical_good_profile() -> None:
    up_up = PREREGISTERED_STATE_MOMENTUM_CANDIDATES[0]
    negative = next(
        spec
        for spec in PREREGISTERED_STATE_MOMENTUM_CANDIDATES
        if spec.family == up_up.family
        and spec.risk_style == up_up.risk_style
        and spec.state_policy == "NON_UP_UP"
    )
    development = {
        up_up.candidate_id: _eligible_profile(),
        negative.candidate_id: _eligible_profile(),
    }
    walk_forward = {
        up_up.candidate_id: {"stability_pass": True},
        negative.candidate_id: {"stability_pass": True},
    }

    selected = select_stable_state_momentum_candidates(
        development,
        walk_forward,
        (up_up, negative),
    )

    assert selected == (up_up.candidate_id,)
