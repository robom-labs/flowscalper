# 공개 영상 유래 PAPER 후보의 사전등록과 미래정보 차단을 검증한다.

from __future__ import annotations

from dataclasses import replace

from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_public_video_trend_tournament import (
    PREREGISTERED_VIDEO_CANDIDATES,
)
from scripts.research_slow_regime_trend_tournament import SlowFeatures, _setup


def _bar(
    index: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    interval_minutes: int = 15,
) -> IntradayBar:
    return IntradayBar(
        symbol="BTCUSDT",
        interval_minutes=interval_minutes,
        open_ts_ms=index * interval_minutes * 60_000,
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
        atr=2,
        adx=25,
        relative_volume=1.2,
        momentum_24h=0.02,
        momentum_72h=0.05,
        momentum_168h=0.08,
        trend_age_bars=5,
    )


def test_public_video_tournament_preregisters_12_nonduplicate_candidates() -> None:
    ids = {spec.candidate_id for spec in PREREGISTERED_VIDEO_CANDIDATES}
    families = {spec.family for spec in PREREGISTERED_VIDEO_CANDIDATES}

    assert len(PREREGISTERED_VIDEO_CANDIDATES) == 12
    assert len(ids) == 12
    assert len(families) == 2
    assert all(candidate_id.startswith("T118_") for candidate_id in ids)
    assert {spec.side_policy for spec in PREREGISTERED_VIDEO_CANDIDATES} == {
        "LONG",
        "SHORT",
        "BOTH",
    }
    assert "maximum_holding_bars" not in PREREGISTERED_VIDEO_CANDIDATES[0].__slots__


def test_liquidity_sweep_requires_a_close_back_inside_the_prior_range() -> None:
    spec = replace(
        next(
            spec
            for spec in PREREGISTERED_VIDEO_CANDIDATES
            if spec.setup_kind == "LIQUIDITY_SWEEP_RECLAIM" and spec.style == "BALANCED"
        ),
        lookback=3,
    )
    rows = (
        _bar(0, open_=101, high=102, low=100, close=101),
        _bar(1, open_=101, high=103, low=99, close=102),
        _bar(2, open_=102, high=104, low=98, close=103),
        _bar(3, open_=98.5, high=102, low=97.5, close=101),
    )
    features = (_feature(),) * len(rows)

    ready, stop = _setup(rows, features, 3, 1, spec)
    failed, _ = _setup((*rows[:3], replace(rows[3], close=97.8)), features, 3, 1, spec)

    assert ready is True
    assert stop is not None and stop < rows[3].low
    assert failed is False


def test_liquidity_sweep_setup_does_not_read_a_future_bar() -> None:
    spec = replace(
        next(
            spec
            for spec in PREREGISTERED_VIDEO_CANDIDATES
            if spec.setup_kind == "LIQUIDITY_SWEEP_RECLAIM" and spec.style == "BALANCED"
        ),
        lookback=3,
    )
    rows = (
        _bar(0, open_=101, high=102, low=100, close=101),
        _bar(1, open_=101, high=103, low=99, close=102),
        _bar(2, open_=102, high=104, low=98, close=103),
        _bar(3, open_=98.5, high=102, low=97.5, close=101),
        _bar(4, open_=101, high=103, low=100, close=102),
    )
    mutated = (*rows[:4], replace(rows[4], high=1_000, low=1, close=900))
    features = (_feature(),) * len(rows)

    assert _setup(rows, features, 3, 1, spec) == _setup(
        mutated, features, 3, 1, spec
    )


def test_ichimoku_setup_does_not_read_a_future_bar() -> None:
    spec = next(
        spec
        for spec in PREREGISTERED_VIDEO_CANDIDATES
        if spec.setup_kind == "ICHIMOKU_PULLBACK_CONTINUATION"
        and spec.style == "BALANCED"
    )
    rows = tuple(
        _bar(
            index,
            open_=100 + index * 0.2,
            high=101 + index * 0.2,
            low=99 + index * 0.2,
            close=100.5 + index * 0.2,
            interval_minutes=60,
        )
        for index in range(90)
    )
    mutated = (*rows[:89], replace(rows[89], high=10_000, low=1, close=9_000))
    features = (_feature(),) * len(rows)

    assert _setup(rows, features, 88, 1, spec) == _setup(
        mutated, features, 88, 1, spec
    )
