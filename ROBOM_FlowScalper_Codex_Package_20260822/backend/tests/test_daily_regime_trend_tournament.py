# 일봉 느린 레짐 추세 리그의 집계, walk-forward, 비용·체결 계약을 검증한다.

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.research_daily_regime_trend_tournament import (
    DAILY_INTERVAL_MINUTES,
    DAILY_INTERVAL_MS,
    DEVELOPMENT_FOLD_COUNT,
    PREREGISTERED_DAILY_CANDIDATES,
    SOURCE_INTERVAL_MINUTES,
    SOURCE_INTERVAL_MS,
    aggregate_four_hour_to_daily,
    development_walk_forward_stability,
    select_stable_development_candidates,
)
from scripts.research_multiyear_trend_tournament import (
    FundingRate,
    candidate_fingerprint,
    funding_adjustment,
)
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_slow_regime_trend_tournament import (
    SlowTrendOutcome,
    _simulate,
)


def _source_bar(index: int, *, day: int = 0) -> IntradayBar:
    base = 100 + index
    return IntradayBar(
        symbol="BTCUSDT",
        interval_minutes=SOURCE_INTERVAL_MINUTES,
        open_ts_ms=day * DAILY_INTERVAL_MS + index * SOURCE_INTERVAL_MS,
        open=base,
        high=base + 2,
        low=base - 1,
        close=base + 1,
        volume=10 + index,
    )


def _daily_bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
) -> IntradayBar:
    return IntradayBar(
        symbol="BTCUSDT",
        interval_minutes=DAILY_INTERVAL_MINUTES,
        open_ts_ms=index * DAILY_INTERVAL_MS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
    )


def _outcome(entry_ts_ms: int, stress_net_bps: float) -> SlowTrendOutcome:
    return SlowTrendOutcome(
        candidate_id="TEST",
        family="TEST",
        symbol="BTCUSDT",
        side="LONG",
        signal_ts_ms=entry_ts_ms - 1,
        entry_ts_ms=entry_ts_ms,
        exit_ts_ms=entry_ts_ms + DAILY_INTERVAL_MS - 1,
        holding_minutes=DAILY_INTERVAL_MINUTES,
        exit_reason="TP2" if stress_net_bps > 0 else "STOP",
        tp1_hit_ts_ms=entry_ts_ms if stress_net_bps > 0 else None,
        tp2_hit_ts_ms=entry_ts_ms if stress_net_bps > 0 else None,
        entry=100,
        stop=98,
        take_profit_1=103,
        take_profit_2=108,
        gross_bps=stress_net_bps + 25,
        base_net_bps=stress_net_bps + 12,
        stress_net_bps=stress_net_bps,
        score=1,
        regime_breadth=0.8,
        relative_rank=0.9,
        censored=False,
    )


def _walk_forward_outcomes(
    *,
    start_ms: int,
    end_ms: int,
    negative_folds: set[int] | None = None,
) -> tuple[SlowTrendOutcome, ...]:
    negative_folds = negative_folds or set()
    development_end_ms = start_ms + int((end_ms - start_ms) * 0.70)
    duration_ms = development_end_ms - start_ms
    rows: list[SlowTrendOutcome] = []
    for fold in range(DEVELOPMENT_FOLD_COUNT):
        fold_start = start_ms + duration_ms * fold // DEVELOPMENT_FOLD_COUNT
        for offset in range(8):
            entry = fold_start + (10 + offset * 3) * DAILY_INTERVAL_MS
            if fold in negative_folds:
                stress_net_bps = 20 if offset < 2 else -20
            else:
                stress_net_bps = -10 if offset == 0 else 20
            rows.append(
                _outcome(
                    entry,
                    stress_net_bps,
                )
            )
    return tuple(rows)


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


def test_daily_tournament_preregisters_30_distinct_daily_candidates() -> None:
    ids = {spec.candidate_id for spec in PREREGISTERED_DAILY_CANDIDATES}
    families = {spec.family for spec in PREREGISTERED_DAILY_CANDIDATES}

    assert len(PREREGISTERED_DAILY_CANDIDATES) == 30
    assert len(ids) == 30
    assert len(families) == 5
    assert all(
        spec.interval_minutes == DAILY_INTERVAL_MINUTES for spec in PREREGISTERED_DAILY_CANDIDATES
    )
    assert {spec.setup_kind for spec in PREREGISTERED_DAILY_CANDIDATES} == {
        "CHANNEL_BREAKOUT",
        "BREAKOUT_RETEST",
        "FIRST_PULLBACK_RECLAIM",
        "ICHIMOKU_PULLBACK_CONTINUATION",
        "EMA_PULLBACK_CONTINUATION",
    }
    assert "maximum_holding_bars" not in PREREGISTERED_DAILY_CANDIDATES[0].__slots__


def test_daily_candidate_fingerprint_is_deterministic_and_parameter_sensitive() -> None:
    baseline = candidate_fingerprint(PREREGISTERED_DAILY_CANDIDATES)
    assert baseline == candidate_fingerprint(PREREGISTERED_DAILY_CANDIDATES)
    modified = (
        replace(PREREGISTERED_DAILY_CANDIDATES[0], tp2_r=8.0),
        *PREREGISTERED_DAILY_CANDIDATES[1:],
    )
    assert baseline != candidate_fingerprint(modified)


def test_daily_aggregation_uses_only_six_contiguous_four_hour_bars() -> None:
    complete = tuple(_source_bar(index) for index in range(6))
    incomplete = tuple(_source_bar(index, day=1) for index in range(5))
    gap = tuple(_source_bar(index, day=2) for index in (0, 1, 2, 3, 4, 6))

    output = aggregate_four_hour_to_daily((*complete, *incomplete, *gap))

    assert len(output) == 1
    daily = output[0]
    assert daily.open_ts_ms == 0
    assert daily.open == complete[0].open
    assert daily.high == max(row.high for row in complete)
    assert daily.low == min(row.low for row in complete)
    assert daily.close == complete[-1].close
    assert daily.volume == sum(row.volume for row in complete)


def test_daily_simulation_enters_next_day_and_applies_stop_before_target() -> None:
    rows = (
        _daily_bar(0, open_=100, high=101, low=99, close=100),
        _daily_bar(1, open_=100, high=110, low=97, close=105),
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
        spec=PREREGISTERED_DAILY_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.entry_ts_ms == rows[1].open_ts_ms
    assert outcome.exit_reason == "STOP"
    assert outcome.tp1_hit_ts_ms is None


def test_daily_funding_boundary_excludes_ambiguous_credit_but_keeps_cost() -> None:
    rates = (
        FundingRate("BTCUSDT", 0, 0.0001),
        FundingRate("BTCUSDT", 8 * 3_600_000, 0.0001),
        FundingRate("BTCUSDT", DAILY_INTERVAL_MS, 0.0001),
    )
    exit_ts_ms = 2 * DAILY_INTERVAL_MS - 1

    long_adjustment = funding_adjustment(
        rates,
        side="LONG",
        entry_ts_ms=0,
        exit_ts_ms=exit_ts_ms,
        bar_interval_ms=DAILY_INTERVAL_MS,
    )
    short_adjustment = funding_adjustment(
        rates,
        side="SHORT",
        entry_ts_ms=0,
        exit_ts_ms=exit_ts_ms,
        bar_interval_ms=DAILY_INTERVAL_MS,
    )

    assert long_adjustment.funding_bps == pytest.approx(-3)
    assert long_adjustment.applied_event_count == 3
    assert short_adjustment.funding_bps == pytest.approx(1)
    assert short_adjustment.applied_event_count == 1
    assert short_adjustment.excluded_ambiguous_credit_count == 2


def test_walk_forward_requires_positive_latest_folds_and_four_positive_folds() -> None:
    start_ms = 0
    end_ms = 600 * DAILY_INTERVAL_MS
    stable = development_walk_forward_stability(
        _walk_forward_outcomes(start_ms=start_ms, end_ms=end_ms),
        start_ms=start_ms,
        end_ms=end_ms,
    )
    stale = development_walk_forward_stability(
        _walk_forward_outcomes(
            start_ms=start_ms,
            end_ms=end_ms,
            negative_folds={5},
        ),
        start_ms=start_ms,
        end_ms=end_ms,
    )

    assert stable["evaluable_fold_count"] == 6
    assert stable["positive_fold_count"] == 6
    assert stable["stability_pass"] is True
    assert stale["positive_fold_count"] == 5
    assert stale["latest_two_folds_positive"] is False
    assert stale["stability_pass"] is False


def test_stable_selection_excludes_unstable_high_rank_and_duplicate_family() -> None:
    specs = (
        PREREGISTERED_DAILY_CANDIDATES[0],
        PREREGISTERED_DAILY_CANDIDATES[1],
        PREREGISTERED_DAILY_CANDIDATES[6],
        PREREGISTERED_DAILY_CANDIDATES[12],
        PREREGISTERED_DAILY_CANDIDATES[18],
        PREREGISTERED_DAILY_CANDIDATES[24],
    )
    development = {
        spec.candidate_id: _eligible_profile(20 - index) for index, spec in enumerate(specs)
    }
    walk_forward = {
        spec.candidate_id: {"stability_pass": index != 0} for index, spec in enumerate(specs)
    }

    selected = select_stable_development_candidates(
        development,
        walk_forward,
        specs,
    )

    assert len(selected) == 5
    assert PREREGISTERED_DAILY_CANDIDATES[0].candidate_id not in selected
    assert PREREGISTERED_DAILY_CANDIDATES[1].candidate_id in selected
