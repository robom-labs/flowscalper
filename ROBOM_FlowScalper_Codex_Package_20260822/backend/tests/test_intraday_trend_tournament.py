# 24개 추세 토너먼트의 사전등록·체결·무강제보유·자료연결 계약을 검증한다.

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from scripts.research_intraday_trend_tournament import (
    PREREGISTERED_CANDIDATES,
    TournamentFeatures,
    _rankable,
    _select_finalists,
    _setup,
    _simulate,
    load_segmented_public_klines,
)
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_public_trend_candidates import BAR_INTERVAL_MS, Kline


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
        interval_minutes=15,
        open_ts_ms=index * 900_000,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=10,
    )


def _feature() -> TournamentFeatures:
    return TournamentFeatures(
        ema20=100,
        ema80=98,
        ema20_slope=0.5,
        atr=1,
        atr_ratio=0.9,
        adx=25,
        relative_volume=1.2,
        momentum_24h=0.02,
        momentum_fast=0.006,
    )


def _kline(index: int) -> Kline:
    return Kline(
        symbol="BTCUSDT",
        open_ts_ms=index * BAR_INTERVAL_MS,
        open=100 + index,
        high=101 + index,
        low=99 + index,
        close=100.5 + index,
        volume=10,
        taker_buy_volume=5,
    )


def _write_segment(path: Path, rows: list[Kline]) -> None:
    path.write_text(
        json.dumps([asdict(row) for row in rows]),
        encoding="utf-8",
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


def test_tournament_preregisters_24_materially_grouped_candidates_without_max_hold() -> None:
    ids = {spec.candidate_id for spec in PREREGISTERED_CANDIDATES}
    families = {spec.family for spec in PREREGISTERED_CANDIDATES}

    assert len(PREREGISTERED_CANDIDATES) == 24
    assert len(ids) == 24
    assert len(families) == 6
    assert sum(spec.interval_minutes == 15 for spec in PREREGISTERED_CANDIDATES) == 12
    assert sum(spec.interval_minutes == 30 for spec in PREREGISTERED_CANDIDATES) == 12
    assert "maximum_holding_bars" not in PREREGISTERED_CANDIDATES[0].__slots__
    assert "maximum_holding_hours" not in PREREGISTERED_CANDIDATES[0].__slots__


def test_tournament_same_bar_stop_precedes_both_targets() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=106, low=97, close=103),
    )

    outcome = _simulate(
        rows,
        index=0,
        direction=1,
        structural_stop=98,
        signal_atr=1,
        score=1,
        spec=PREREGISTERED_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.exit_reason == "STOP"
    assert outcome.tp1_hit_ts_ms is None
    assert outcome.gross_bps == pytest.approx(-200)


def test_tournament_leaves_unresolved_position_censored_instead_of_forcing_max_hold() -> None:
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
        spec=PREREGISTERED_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.exit_reason == "CENSORED_OPEN"
    assert outcome.censored is True
    assert outcome.base_net_bps is None
    assert outcome.stress_net_bps is None


def test_tournament_tp1_then_stress_cost_protected_stop_keeps_positive_net() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=102.2, low=99.5, close=102),
        _bar(2, open_=102, high=102.1, low=100.2, close=100.3),
    )

    outcome = _simulate(
        rows,
        index=0,
        direction=1,
        structural_stop=98,
        signal_atr=1,
        score=1,
        spec=PREREGISTERED_CANDIDATES[0],
    )

    assert outcome is not None
    assert outcome.exit_reason == "STOP_AFTER_TP1"
    assert outcome.tp1_hit_ts_ms == rows[1].close_ts_ms
    assert outcome.base_net_bps is not None and outcome.base_net_bps > 0
    assert outcome.stress_net_bps is not None and outcome.stress_net_bps > 0


def test_tournament_setup_does_not_read_future_bars() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=100.5, low=99.8, close=100.2),
        _bar(2, open_=100.1, high=101, low=99.9, close=100.8),
        _bar(3, open_=100.8, high=500, low=1, close=250),
    )
    mutated = (*rows[:3], replace(rows[3], high=1_000, low=0.1, close=900))
    features = (_feature(),) * len(rows)
    spec = PREREGISTERED_CANDIDATES[0]

    original = _setup(rows, features, 2, 1, spec)
    after_future_change = _setup(mutated, features, 2, 1, spec)

    assert original == after_future_change


def test_segmented_cache_loader_stitches_exact_contiguous_public_bars(tmp_path: Path) -> None:
    _write_segment(
        tmp_path / "BTCUSDT-5m-0-600000.json",
        [_kline(0), _kline(1)],
    )
    _write_segment(
        tmp_path / "BTCUSDT-5m-600000-900000.json",
        [_kline(2)],
    )

    data, manifest = load_segmented_public_klines(
        ("BTCUSDT",),
        start_ms=0,
        end_ms=900_000,
        cache_dir=tmp_path,
    )

    assert [row.open_ts_ms for row in data["BTCUSDT"]] == [0, 300_000, 600_000]
    assert manifest[0]["bar_count"] == 3
    assert manifest[0]["segments"][0]["path"] == "BTCUSDT-5m-0-600000.json"


def test_segmented_cache_loader_rejects_a_gap(tmp_path: Path) -> None:
    _write_segment(
        tmp_path / "BTCUSDT-5m-0-600000.json",
        [_kline(0), _kline(1)],
    )
    _write_segment(
        tmp_path / "BTCUSDT-5m-600000-1200000.json",
        [_kline(3)],
    )

    with pytest.raises(ValueError, match="gap"):
        load_segmented_public_klines(
            ("BTCUSDT",),
            start_ms=0,
            end_ms=1_200_000,
            cache_dir=tmp_path,
        )


def test_finalists_are_limited_to_three_distinct_families() -> None:
    specs = (
        PREREGISTERED_CANDIDATES[0],
        PREREGISTERED_CANDIDATES[1],
        PREREGISTERED_CANDIDATES[4],
        PREREGISTERED_CANDIDATES[8],
        PREREGISTERED_CANDIDATES[12],
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

    assert len(selected) == 3
    assert len(selected_families) == 3
    assert PREREGISTERED_CANDIDATES[1].candidate_id not in selected


def test_sparse_candidate_is_not_ranked_even_when_its_average_is_positive() -> None:
    sparse = _eligible_profile(72.5, 2.0)
    base = sparse["base"]
    validation = sparse["validation_stress"]
    assert isinstance(base, dict)
    assert isinstance(validation, dict)
    base["sample_size"] = 5
    validation["sample_size"] = 1

    assert _rankable(sparse) is False
