# 비대칭 추세 runner의 무제한 보유, 완성봉 추적손절과 보수적 체결을 검증한다.

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.research_asymmetric_trend_runner_tournament import (
    INTERVAL_MINUTES,
    INTERVAL_MS,
    PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES,
    _next_chandelier_stop,
    _simulate_runner,
    asymmetric_candidate_fingerprint,
)
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_slow_regime_trend_tournament import SlowFeatures


def _bar(
    index: int,
    *,
    open_: float = 100,
    high: float = 101,
    low: float = 99,
    close: float = 100,
) -> IntradayBar:
    return IntradayBar(
        symbol="BTCUSDT",
        interval_minutes=INTERVAL_MINUTES,
        open_ts_ms=index * INTERVAL_MS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
    )


def _feature(*, atr: float = 1) -> SlowFeatures:
    return SlowFeatures(
        ema20=101,
        ema50=100,
        ema200=95,
        ema20_slope=1,
        ema50_slope=1,
        atr=atr,
        adx=25,
        relative_volume=1.2,
        momentum_24h=0.02,
        momentum_72h=0.05,
        momentum_168h=0.10,
        trend_age_bars=4,
    )


def test_asymmetric_runner_preregisters_60_distinct_candidates() -> None:
    specs = PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES

    assert len(specs) == 60
    assert len({spec.candidate_id for spec in specs}) == 60
    assert len({spec.family for spec in specs}) == 5
    assert {spec.exit.exit_id for spec in specs} == {
        "CHAND22_ATR3",
        "CHAND22_ATR4",
    }
    assert all(spec.exit.activation_r == 1.0 for spec in specs)
    assert all(spec.exit.chandelier_lookback == 22 for spec in specs)


def test_asymmetric_runner_fingerprint_is_deterministic_and_sensitive() -> None:
    specs = PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES
    changed = (
        replace(
            specs[0],
            exit=replace(specs[0].exit, chandelier_atr_multiplier=9.0),
        ),
        *specs[1:],
    )

    assert asymmetric_candidate_fingerprint() == asymmetric_candidate_fingerprint()
    assert asymmetric_candidate_fingerprint() != asymmetric_candidate_fingerprint(changed)


def test_chandelier_uses_only_previous_completed_bars_and_never_widens() -> None:
    rows = (
        _bar(0, high=101),
        _bar(1, high=110),
        _bar(2, high=1_000),
    )
    changed_current = (*rows[:2], _bar(2, high=10_000))
    features = tuple(_feature() for _ in rows)

    baseline = _next_chandelier_stop(
        rows,
        features,
        cursor=2,
        entry_index=1,
        direction=1,
        current_stop=98,
        lookback=22,
        atr_multiplier=3,
    )
    mutated = _next_chandelier_stop(
        changed_current,
        features,
        cursor=2,
        entry_index=1,
        direction=1,
        current_stop=98,
        lookback=22,
        atr_multiplier=3,
    )

    assert baseline == pytest.approx(107)
    assert mutated == baseline
    assert _next_chandelier_stop(
        rows,
        features,
        cursor=2,
        entry_index=1,
        direction=1,
        current_stop=108,
        lookback=22,
        atr_multiplier=3,
    ) == pytest.approx(108)


def test_same_bar_initial_stop_wins_over_runner_activation() -> None:
    rows = (
        _bar(0),
        _bar(1, open_=100, high=103, low=97, close=101),
    )
    features = tuple(_feature(atr=2) for _ in rows)

    outcome = _simulate_runner(
        rows,
        features,
        index=0,
        direction=1,
        structural_stop=98,
        signal_atr=2,
        score=1,
        breadth=0.8,
        relative_rank=0.9,
        spec=PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.exit_reason == "INITIAL_STOP"
    assert outcome.activation_ts_ms is None
    assert outcome.exit_price == 98


def test_runner_activates_then_exits_on_next_bar_completed_chandelier() -> None:
    rows = (
        _bar(0),
        _bar(1, open_=100, high=103, low=99.5, close=102),
        _bar(2, open_=103, high=110, low=102, close=109),
        _bar(3, open_=109, high=109, low=106, close=107),
    )
    features = tuple(_feature() for _ in rows)

    outcome = _simulate_runner(
        rows,
        features,
        index=0,
        direction=1,
        structural_stop=99,
        signal_atr=1,
        score=1,
        breadth=0.8,
        relative_rank=0.9,
        spec=PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.activation_ts_ms == rows[1].close_ts_ms
    assert outcome.exit_reason == "CHANDELIER_TRAIL"
    assert outcome.final_stop == pytest.approx(107)
    assert outcome.exit_price == pytest.approx(107)
    assert outcome.gross_r == pytest.approx(7)


def test_gap_through_trailing_stop_fills_at_worse_open() -> None:
    rows = (
        _bar(0),
        _bar(1, open_=100, high=103, low=99.5, close=102),
        _bar(2, open_=103, high=110, low=102, close=109),
        _bar(3, open_=105, high=106, low=104, close=105),
    )
    features = tuple(_feature() for _ in rows)

    outcome = _simulate_runner(
        rows,
        features,
        index=0,
        direction=1,
        structural_stop=99,
        signal_atr=1,
        score=1,
        breadth=0.8,
        relative_rank=0.9,
        spec=PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.final_stop == pytest.approx(107)
    assert outcome.exit_price == pytest.approx(105)


def test_no_maximum_hold_and_unresolved_position_is_censored() -> None:
    rows = (_bar(0),) + tuple(
        _bar(index, open_=100, high=100.5, low=99.5, close=100) for index in range(1, 80)
    )
    features = tuple(_feature(atr=2) for _ in rows)

    outcome = _simulate_runner(
        rows,
        features,
        index=0,
        direction=1,
        structural_stop=98,
        signal_atr=2,
        score=1,
        breadth=0.8,
        relative_rank=0.9,
        spec=PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.censored is True
    assert outcome.exit_reason == "CENSORED_OPEN"
    assert outcome.holding_minutes == 79 * INTERVAL_MINUTES
    assert outcome.gross_bps is None
    assert outcome.base_net_bps is None
    assert outcome.stress_net_bps is None
