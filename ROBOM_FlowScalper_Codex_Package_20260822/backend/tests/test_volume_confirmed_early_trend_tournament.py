# 거래량 확인 추세 후보의 완성봉, 다음 봉 체결, 비용과 선발 경계를 검증한다.

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.research_multiyear_trend_tournament import FundingRate
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_slow_regime_trend_tournament import SlowFeatures, SlowTrendOutcome, _simulate
from scripts.research_volume_confirmed_early_trend_tournament import (
    BASE_RISK_BUDGET_BPS,
    INTERVAL_MINUTES,
    INTERVAL_MS,
    PREREGISTERED_VOLUME_TREND_CANDIDATES,
    VolumeIndicators,
    _apply_account_risk_funding_and_costs,
    _confirmed_cross,
    _slow_spec,
    build_volume_indicators,
    select_stable_volume_trend_candidates,
    volume_setup,
    volume_trend_candidate_fingerprint,
)


def _bar(
    index: int,
    *,
    open_: float = 100,
    high: float = 102,
    low: float = 98,
    close: float = 100,
    volume: float = 10,
) -> IntradayBar:
    return IntradayBar(
        symbol="BTCUSDT",
        interval_minutes=INTERVAL_MINUTES,
        open_ts_ms=index * INTERVAL_MS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _feature(
    *,
    ema20: float = 101,
    ema50: float = 100,
    atr: float = 2,
) -> SlowFeatures:
    return SlowFeatures(
        ema20=ema20,
        ema50=ema50,
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


def _outcome(
    *,
    entry_ts_ms: int = 0,
    exit_ts_ms: int = 3 * INTERVAL_MS - 1,
    gross_bps: float | None = 100,
    censored: bool = False,
) -> SlowTrendOutcome:
    return SlowTrendOutcome(
        candidate_id="TEST",
        family="TEST",
        symbol="BTCUSDT",
        side="LONG",
        signal_ts_ms=entry_ts_ms - 1,
        entry_ts_ms=entry_ts_ms,
        exit_ts_ms=exit_ts_ms,
        holding_minutes=3 * INTERVAL_MINUTES,
        exit_reason="CENSORED_OPEN" if censored else "TP2",
        tp1_hit_ts_ms=None if censored else exit_ts_ms,
        tp2_hit_ts_ms=None if censored else exit_ts_ms,
        entry=100,
        stop=98,
        take_profit_1=103,
        take_profit_2=108,
        gross_bps=None if censored else gross_bps,
        base_net_bps=None if censored else 87,
        stress_net_bps=None if censored else 75,
        score=1,
        regime_breadth=0.8,
        relative_rank=0.9,
        censored=censored,
    )


def _profile(
    expectancy: float,
    *,
    stress_expectancy: float | None = None,
) -> dict[str, object]:
    stress_value = expectancy if stress_expectancy is None else stress_expectancy
    return {
        "base": {
            "sample_size": 80,
            "expectancy_bps": expectancy,
            "profit_factor": 1.5,
        },
        "stress": {
            "sample_size": 80,
            "expectancy_bps": stress_value,
            "profit_factor": 1.5 if stress_value > 0 else 0.8,
        },
        "validation_stress": {
            "sample_size": 30,
            "expectancy_bps": stress_value,
            "profit_factor": 1.3 if stress_value > 0 else 0.8,
        },
    }


def test_volume_trend_preregisters_30_distinct_candidates() -> None:
    specs = PREREGISTERED_VOLUME_TREND_CANDIDATES

    assert len(specs) == 30
    assert len({spec.candidate_id for spec in specs}) == 30
    assert len({spec.family for spec in specs}) == 5
    assert {spec.side_policy for spec in specs} == {"LONG", "SHORT", "BOTH"}
    assert {spec.style for spec in specs} == {"BALANCED", "SELECTIVE"}
    assert {spec.setup_kind for spec in specs} == {
        "OBV_MA_CROSS",
        "OBV_PRICE_BREAKOUT",
        "SQUEEZE_BREAKOUT",
        "FILTER_TURN",
        "OBV_FIRST_PULLBACK",
    }
    assert all(spec.base_risk_budget_bps == BASE_RISK_BUDGET_BPS for spec in specs)
    assert "maximum_holding_bars" not in specs[0].__slots__


def test_volume_trend_fingerprint_is_deterministic_and_parameter_sensitive() -> None:
    baseline = volume_trend_candidate_fingerprint()
    changed = (
        replace(PREREGISTERED_VOLUME_TREND_CANDIDATES[0], tp2_r=8.0),
        *PREREGISTERED_VOLUME_TREND_CANDIDATES[1:],
    )

    assert baseline == volume_trend_candidate_fingerprint()
    assert baseline != volume_trend_candidate_fingerprint(changed)


def test_obv_keeps_flat_close_neutral_and_never_rewrites_past_values() -> None:
    rows = (
        _bar(0, close=100, volume=10),
        _bar(1, close=101, volume=20),
        _bar(2, close=101, volume=30),
        _bar(3, close=100, volume=40),
        *tuple(_bar(index, close=100 + index, volume=10 + index) for index in range(4, 12)),
    )
    changed = (*rows[:-1], _bar(11, close=1_000, volume=1_000))

    baseline = build_volume_indicators(rows, fast=2, slow=4)
    mutated = build_volume_indicators(changed, fast=2, slow=4)

    assert baseline.obv[:4] == (0.0, 20.0, 20.0, -20.0)
    assert baseline.obv[:-1] == mutated.obv[:-1]
    assert baseline.spread[:-1] == mutated.spread[:-1]
    assert baseline.obv[-1] != mutated.obv[-1]


def test_obv_cross_requires_all_preregistered_confirmation_bars() -> None:
    spread = (None, 0.0, 0.02, 0.03)

    assert not _confirmed_cross(
        spread,
        index=2,
        direction=1,
        threshold=0.01,
        confirmation_bars=2,
    )
    assert _confirmed_cross(
        spread,
        index=3,
        direction=1,
        threshold=0.01,
        confirmation_bars=2,
    )


def test_obv_cross_setup_uses_completed_signal_bar_and_structural_stop() -> None:
    rows = tuple(_bar(index) for index in range(6)) + (
        _bar(6, open_=101, high=106, low=99, close=105),
        _bar(7, open_=105, high=107, low=104, close=106),
    )
    features = tuple(_feature() for _ in rows)
    indicators = VolumeIndicators(
        obv=tuple(0.0 for _ in rows),
        spread=(None, None, None, 0.0, 0.0, 0.0, 0.02, 0.02),
    )
    spec = replace(
        PREREGISTERED_VOLUME_TREND_CANDIDATES[0],
        lookback=3,
        obv_fast=2,
        obv_slow=4,
        obv_band_fraction=0.01,
        confirmation_bars=1,
        stop_buffer_atr=0.25,
    )

    ready, stop = volume_setup(
        rows,
        features,
        indicators,
        index=6,
        direction=1,
        spec=spec,
    )

    assert ready is True
    assert stop == pytest.approx(97.5)
    assert rows[7].open_ts_ms > rows[6].close_ts_ms


def test_volume_trend_simulation_enters_next_bar_and_applies_stop_first() -> None:
    rows = (
        _bar(0, open_=100, high=102, low=99, close=101),
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
        spec=_slow_spec(PREREGISTERED_VOLUME_TREND_CANDIDATES[0]),
    )

    assert outcome is not None
    assert outcome.entry_ts_ms == rows[1].open_ts_ms
    assert outcome.exit_reason == "STOP"
    assert outcome.tp1_hit_ts_ms is None


def test_account_risk_scales_price_funding_and_both_execution_costs() -> None:
    funding_at_eight_hours = 8 * 3_600_000
    rates = (FundingRate("BTCUSDT", funding_at_eight_hours, 0.0001),)

    revised, audit = _apply_account_risk_funding_and_costs(
        _outcome(),
        rates,
        PREREGISTERED_VOLUME_TREND_CANDIDATES[0],
    )

    assert audit["notional_fraction"] == pytest.approx(0.2)
    assert audit["applied_funding_event_count"] == 1
    assert audit["net_funding_cashflow_account_bps"] == pytest.approx(-0.2)
    assert revised.gross_bps == pytest.approx(19.8)
    assert revised.base_net_bps == pytest.approx(17.2)
    assert revised.stress_net_bps == pytest.approx(14.8)


def test_censored_position_is_not_scored_or_charged_as_closed() -> None:
    revised, audit = _apply_account_risk_funding_and_costs(
        _outcome(censored=True),
        (FundingRate("BTCUSDT", 8 * 3_600_000, 0.0001),),
        PREREGISTERED_VOLUME_TREND_CANDIDATES[0],
    )

    assert revised.censored is True
    assert revised.gross_bps is None
    assert revised.base_net_bps is None
    assert revised.stress_net_bps is None
    assert audit["applied_funding_event_count"] == 0


def test_selection_requires_stress_profit_and_one_candidate_per_family() -> None:
    stronger = PREREGISTERED_VOLUME_TREND_CANDIDATES[0]
    duplicate_family = PREREGISTERED_VOLUME_TREND_CANDIDATES[1]
    different_family = PREREGISTERED_VOLUME_TREND_CANDIDATES[6]
    cost_failure = PREREGISTERED_VOLUME_TREND_CANDIDATES[12]
    specs = (stronger, duplicate_family, different_family, cost_failure)
    development = {
        stronger.candidate_id: _profile(20),
        duplicate_family.candidate_id: _profile(15),
        different_family.candidate_id: _profile(10),
        cost_failure.candidate_id: _profile(12, stress_expectancy=-1),
    }
    walk_forward = {
        spec.candidate_id: {"stability_pass": True} for spec in specs
    }

    selected = select_stable_volume_trend_candidates(
        development,
        walk_forward,
        specs,
    )

    assert selected == (stronger.candidate_id, different_family.candidate_id)
    assert cost_failure.candidate_id not in selected
