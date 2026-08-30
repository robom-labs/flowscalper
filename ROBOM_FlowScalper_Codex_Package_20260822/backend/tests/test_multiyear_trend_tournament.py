# 다년 저회전 추세 리그의 후보 고정, 펀딩 비용, 보수적 체결 계약을 검증한다.

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scripts.research_multiyear_trend_tournament import (
    BASE_EXECUTION_COST_BPS,
    INTERVAL_MINUTES,
    INTERVAL_MS,
    PREREGISTERED_CANDIDATES,
    STRESS_EXECUTION_COST_BPS,
    FundingRate,
    _cache_path,
    apply_actual_funding_and_costs,
    candidate_fingerprint,
    funding_adjustment,
    select_development_candidates,
)
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_slow_regime_trend_tournament import (
    SlowTrendOutcome,
    _simulate,
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
        interval_minutes=INTERVAL_MINUTES,
        open_ts_ms=index * INTERVAL_MS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
    )


def _outcome(*, censored: bool = False) -> SlowTrendOutcome:
    return SlowTrendOutcome(
        candidate_id="TEST",
        family="TEST",
        symbol="BTCUSDT",
        side="LONG",
        signal_ts_ms=-1,
        entry_ts_ms=0,
        exit_ts_ms=3 * INTERVAL_MS - 1,
        holding_minutes=3 * INTERVAL_MINUTES,
        exit_reason="CENSORED_OPEN" if censored else "TP2",
        tp1_hit_ts_ms=None if censored else INTERVAL_MS - 1,
        tp2_hit_ts_ms=None if censored else 3 * INTERVAL_MS - 1,
        entry=100,
        stop=98,
        take_profit_1=103,
        take_profit_2=108,
        gross_bps=None if censored else 100,
        base_net_bps=None if censored else 87,
        stress_net_bps=None if censored else 75,
        score=1,
        regime_breadth=0.8,
        relative_rank=0.9,
        censored=censored,
    )


def _eligible_profile(expectancy: float) -> dict[str, object]:
    development = {
        "sample_size": 80,
        "expectancy_bps": expectancy,
        "profit_factor": 1.5,
    }
    validation = {
        "sample_size": 30,
        "expectancy_bps": expectancy,
        "profit_factor": 1.3,
    }
    return {
        "base": dict(development),
        "stress": dict(development),
        "validation_stress": validation,
    }


def test_multiyear_tournament_preregisters_30_distinct_four_hour_candidates() -> None:
    ids = {spec.candidate_id for spec in PREREGISTERED_CANDIDATES}
    families = {spec.family for spec in PREREGISTERED_CANDIDATES}

    assert len(PREREGISTERED_CANDIDATES) == 30
    assert len(ids) == 30
    assert len(families) == 5
    assert {spec.side_policy for spec in PREREGISTERED_CANDIDATES} == {
        "LONG",
        "SHORT",
        "BOTH",
    }
    assert {spec.style for spec in PREREGISTERED_CANDIDATES} == {
        "BALANCED",
        "SELECTIVE",
    }
    assert all(spec.interval_minutes == 240 for spec in PREREGISTERED_CANDIDATES)
    assert "maximum_holding_bars" not in PREREGISTERED_CANDIDATES[0].__slots__
    assert "maximum_holding_hours" not in PREREGISTERED_CANDIDATES[0].__slots__


def test_multiyear_candidate_fingerprint_is_order_and_parameter_sensitive() -> None:
    baseline = candidate_fingerprint()
    assert baseline == candidate_fingerprint()
    assert baseline != candidate_fingerprint(tuple(reversed(PREREGISTERED_CANDIDATES)))
    modified = (
        replace(PREREGISTERED_CANDIDATES[0], tp2_r=9.0),
        *PREREGISTERED_CANDIDATES[1:],
    )
    assert baseline != candidate_fingerprint(modified)


def test_multiyear_same_bar_stop_precedes_targets_and_entry_is_next_bar() -> None:
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
    assert outcome.entry_ts_ms == rows[1].open_ts_ms
    assert outcome.entry == rows[1].open
    assert outcome.exit_reason == "STOP"
    assert outcome.tp1_hit_ts_ms is None


def test_funding_applies_direction_and_excludes_only_ambiguous_credits() -> None:
    rates = tuple(
        FundingRate("BTCUSDT", timestamp, 0.0001) for timestamp in (0, INTERVAL_MS, 2 * INTERVAL_MS)
    )
    exit_ts_ms = 3 * INTERVAL_MS - 1

    long_adjustment = funding_adjustment(
        rates,
        side="LONG",
        entry_ts_ms=0,
        exit_ts_ms=exit_ts_ms,
    )
    short_adjustment = funding_adjustment(
        rates,
        side="SHORT",
        entry_ts_ms=0,
        exit_ts_ms=exit_ts_ms,
    )

    assert long_adjustment.funding_bps == pytest.approx(-3)
    assert long_adjustment.applied_event_count == 3
    assert long_adjustment.excluded_ambiguous_credit_count == 0
    assert short_adjustment.funding_bps == pytest.approx(1)
    assert short_adjustment.applied_event_count == 1
    assert short_adjustment.excluded_ambiguous_credit_bps == pytest.approx(2)
    assert short_adjustment.excluded_ambiguous_credit_count == 2


def test_actual_funding_and_costs_replaces_old_fixed_cost_values() -> None:
    rates = (FundingRate("BTCUSDT", INTERVAL_MS, 0.0001),)

    adjusted, audit = apply_actual_funding_and_costs(_outcome(), rates)

    assert audit.funding_bps == pytest.approx(-1)
    assert adjusted.base_net_bps == pytest.approx(100 - 1 - BASE_EXECUTION_COST_BPS)
    assert adjusted.stress_net_bps == pytest.approx(100 - 1 - STRESS_EXECUTION_COST_BPS)


def test_actual_funding_does_not_score_censored_open_position() -> None:
    original = _outcome(censored=True)

    adjusted, audit = apply_actual_funding_and_costs(
        original,
        (FundingRate("BTCUSDT", INTERVAL_MS, 0.0001),),
    )

    assert adjusted == original
    assert audit.funding_bps == 0
    assert audit.applied_event_count == 0


def test_development_selection_keeps_at_most_one_candidate_per_family() -> None:
    specs = (
        PREREGISTERED_CANDIDATES[0],
        PREREGISTERED_CANDIDATES[1],
        PREREGISTERED_CANDIDATES[6],
        PREREGISTERED_CANDIDATES[12],
        PREREGISTERED_CANDIDATES[18],
        PREREGISTERED_CANDIDATES[24],
    )
    development = {
        spec.candidate_id: _eligible_profile(20 - index) for index, spec in enumerate(specs)
    }

    selected = select_development_candidates(development, specs)
    families = {
        next(spec.family for spec in specs if spec.candidate_id == candidate_id)
        for candidate_id in selected
    }

    assert len(selected) == 5
    assert len(families) == 5
    assert PREREGISTERED_CANDIDATES[1].candidate_id not in selected


def test_multiyear_cache_paths_separate_kind_and_date_range(tmp_path: Path) -> None:
    bars = _cache_path(tmp_path, "BTCUSDT", "4h", 1, 2)
    funding = _cache_path(tmp_path, "BTCUSDT", "funding", 1, 2)
    other_range = _cache_path(tmp_path, "BTCUSDT", "4h", 1, 3)

    assert bars != funding
    assert bars != other_range
    assert bars.parent == tmp_path
