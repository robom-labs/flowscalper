# 느린 추세·레짐 보조리그의 사전등록과 보수적 PAPER 청산 계약을 검증한다.

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_slow_regime_trend_tournament import (
    PREREGISTERED_CANDIDATES,
    SlowFeatures,
    SlowTrendOutcome,
    _rankable,
    _select_finalists,
    _setup,
    _simulate,
    apply_portfolio_limits,
)


def _bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> IntradayBar:
    return IntradayBar(
        symbol="BTCUSDT",
        interval_minutes=60,
        open_ts_ms=index * 3_600_000,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
    )


def _feature() -> SlowFeatures:
    return SlowFeatures(
        ema20=100,
        ema50=98,
        ema200=90,
        ema20_slope=0.5,
        ema50_slope=0.25,
        atr=1,
        adx=25,
        relative_volume=1.2,
        momentum_24h=0.02,
        momentum_72h=0.05,
        momentum_168h=0.08,
        trend_age_bars=5,
    )


def _eligible_profile(expectancy: float, profit_factor: float) -> dict[str, object]:
    base = {
        "sample_size": 80,
        "expectancy_bps": expectancy,
        "profit_factor": profit_factor,
    }
    validation = {
        "sample_size": 30,
        "expectancy_bps": expectancy,
        "profit_factor": profit_factor,
    }
    return {
        "base": base,
        "stress": dict(base),
        "validation_stress": validation,
    }


def _outcome(entry_ts_ms: int, exit_ts_ms: int, score: float) -> SlowTrendOutcome:
    return SlowTrendOutcome(
        candidate_id="TEST",
        family="TEST",
        symbol=f"TEST{int(score)}USDT",
        side="LONG",
        signal_ts_ms=entry_ts_ms - 1,
        entry_ts_ms=entry_ts_ms,
        exit_ts_ms=exit_ts_ms,
        holding_minutes=max(1, (exit_ts_ms - entry_ts_ms) // 60_000),
        exit_reason="TP2",
        tp1_hit_ts_ms=entry_ts_ms + 1,
        tp2_hit_ts_ms=exit_ts_ms,
        entry=100,
        stop=98,
        take_profit_1=103,
        take_profit_2=108,
        gross_bps=800,
        base_net_bps=787,
        stress_net_bps=775,
        score=score,
        regime_breadth=0.8,
        relative_rank=0.9,
        censored=False,
    )


def test_slow_tournament_preregisters_24_distinct_candidates_without_max_hold() -> None:
    ids = {spec.candidate_id for spec in PREREGISTERED_CANDIDATES}
    families = {spec.family for spec in PREREGISTERED_CANDIDATES}

    assert len(PREREGISTERED_CANDIDATES) == 24
    assert len(ids) == 24
    assert len(families) == 4
    assert sum(spec.interval_minutes == 60 for spec in PREREGISTERED_CANDIDATES) == 12
    assert sum(spec.interval_minutes == 240 for spec in PREREGISTERED_CANDIDATES) == 12
    assert "maximum_holding_bars" not in PREREGISTERED_CANDIDATES[0].__slots__
    assert "maximum_holding_hours" not in PREREGISTERED_CANDIDATES[0].__slots__


def test_slow_tournament_same_bar_stop_precedes_targets() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=110, low=97, close=105),
    )

    outcome = _simulate(
        rows,
        index=0,
        direction=1,
        structural_stop=98,
        signal_atr=1,
        score=1,
        breadth=0.8,
        relative_rank=0.9,
        spec=PREREGISTERED_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.exit_reason == "STOP"
    assert outcome.tp1_hit_ts_ms is None
    assert outcome.gross_bps == pytest.approx(-200)


def test_slow_tournament_leaves_unresolved_position_censored() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=101, low=99, close=100.5),
        _bar(2, open_=100.5, high=101, low=99, close=100.3),
    )

    outcome = _simulate(
        rows,
        index=0,
        direction=1,
        structural_stop=98,
        signal_atr=1,
        score=1,
        breadth=0.8,
        relative_rank=0.9,
        spec=PREREGISTERED_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.exit_reason == "CENSORED_OPEN"
    assert outcome.censored is True
    assert outcome.base_net_bps is None
    assert outcome.stress_net_bps is None


def test_slow_tournament_tp1_then_cost_protected_stop_keeps_positive_net() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=103.2, low=100.3, close=103),
        _bar(2, open_=103, high=103.1, low=100.2, close=100.3),
    )

    outcome = _simulate(
        rows,
        index=0,
        direction=1,
        structural_stop=98,
        signal_atr=1,
        score=1,
        breadth=0.8,
        relative_rank=0.9,
        spec=PREREGISTERED_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.exit_reason == "STOP_AFTER_TP1"
    assert outcome.tp1_hit_ts_ms == rows[1].close_ts_ms
    assert outcome.base_net_bps is not None and outcome.base_net_bps > 0
    assert outcome.stress_net_bps is not None and outcome.stress_net_bps > 0


def test_slow_tournament_same_bar_tp1_and_protected_stop_closes_conservatively() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=103.2, low=100.2, close=102),
    )

    outcome = _simulate(
        rows,
        index=0,
        direction=1,
        structural_stop=98,
        signal_atr=1,
        score=1,
        breadth=0.8,
        relative_rank=0.9,
        spec=PREREGISTERED_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.exit_reason == "STOP_AFTER_TP1"
    assert outcome.tp1_hit_ts_ms == rows[1].close_ts_ms
    assert outcome.stress_net_bps is not None and outcome.stress_net_bps > 0


def test_slow_tournament_setup_does_not_read_future_bars() -> None:
    rows = (
        _bar(0, open_=99, high=100, low=98, close=99.5),
        _bar(1, open_=99.5, high=101, low=99, close=100.5),
        _bar(2, open_=100.5, high=103, low=100, close=102),
        _bar(3, open_=102, high=104, low=101, close=103),
    )
    mutated = (*rows[:3], replace(rows[3], high=1_000, low=0.1, close=900))
    features = (_feature(),) * len(rows)
    spec = replace(PREREGISTERED_CANDIDATES[0], lookback=2)

    original = _setup(rows, features, 2, 1, spec)
    after_future_change = _setup(mutated, features, 2, 1, spec)

    assert original == after_future_change


def test_slow_tournament_portfolio_limits_concurrency_and_daily_entries() -> None:
    hour = 3_600_000
    rows = (
        _outcome(hour, hour * 5, 3),
        _outcome(hour, hour * 5, 2),
        _outcome(hour, hour * 5, 1),
        _outcome(hour * 6, hour * 7, 4),
    )

    selected = apply_portfolio_limits(rows)

    assert len(selected) == 2
    assert [row.score for row in selected] == [3, 2]


def test_slow_tournament_finalists_are_distinct_families() -> None:
    specs = (
        PREREGISTERED_CANDIDATES[0],
        PREREGISTERED_CANDIDATES[1],
        PREREGISTERED_CANDIDATES[6],
        PREREGISTERED_CANDIDATES[12],
        PREREGISTERED_CANDIDATES[18],
    )
    development = {
        spec.candidate_id: _eligible_profile(10 - index, 2 - index * 0.1)
        for index, spec in enumerate(specs)
    }

    selected = _select_finalists(development, specs)
    selected_families = {
        next(spec.family for spec in specs if spec.candidate_id == candidate_id)
        for candidate_id in selected
    }

    assert len(selected) == 4
    assert len(selected_families) == 4
    assert PREREGISTERED_CANDIDATES[1].candidate_id not in selected


def test_slow_tournament_sparse_candidate_is_not_ranked() -> None:
    sparse = _eligible_profile(72.5, 2.0)
    base = sparse["base"]
    validation = sparse["validation_stress"]
    assert isinstance(base, dict)
    assert isinstance(validation, dict)
    base["sample_size"] = 5
    validation["sample_size"] = 1

    assert _rankable(sparse) is False
