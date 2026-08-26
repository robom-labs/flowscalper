# 중단기 추세 연구의 완성 봉·다음 봉 진입·보수적 목표/손절 순서를 검증한다.

from __future__ import annotations

from dataclasses import replace

from scripts.research_public_intraday_trend_candidates import (
    PREREGISTERED_CANDIDATES,
    IntradayBar,
    IntradayFeatures,
    IntradayOutcome,
    _simulate,
    aggregate_bars,
    apply_portfolio_limits,
)
from scripts.research_public_trend_candidates import BAR_INTERVAL_MS, Kline


def _kline(index: int) -> Kline:
    return Kline(
        symbol="BTCUSDT",
        open_ts_ms=index * BAR_INTERVAL_MS,
        open=100 + index,
        high=101 + index,
        low=99 + index,
        close=100.5 + index,
        volume=1,
        taker_buy_volume=0.5,
    )


def _bar(index: int, *, open_: float, high: float, low: float, close: float) -> IntradayBar:
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


def _features() -> IntradayFeatures:
    return IntradayFeatures(
        ema20=101,
        ema80=99,
        atr=1,
        adx=25,
        relative_volume=1.2,
        momentum_24h=0.02,
    )


def _outcome(symbol: str, score: float) -> IntradayOutcome:
    return IntradayOutcome(
        candidate_id="TEST",
        symbol=symbol,
        side="LONG",
        signal_ts_ms=899_999,
        entry_ts_ms=900_000,
        exit_ts_ms=1_799_999,
        holding_minutes=15,
        exit_reason="MAX_HOLD",
        tp1_hit_ts_ms=None,
        tp2_hit_ts_ms=None,
        entry=100,
        stop=98,
        take_profit_1=102,
        take_profit_2=104,
        gross_bps=10,
        base_net_bps=-3,
        stress_net_bps=-15,
        score=score,
    )


def test_intraday_aggregation_requires_complete_contiguous_source_bars() -> None:
    complete_15m = aggregate_bars(tuple(_kline(index) for index in range(3)), 15)
    incomplete_15m = aggregate_bars(tuple(_kline(index) for index in range(2)), 15)
    complete_30m = aggregate_bars(tuple(_kline(index) for index in range(6)), 30)

    assert len(complete_15m) == 1
    assert complete_15m[0].close_ts_ms == 900_000 - 1
    assert incomplete_15m == ()
    assert len(complete_30m) == 1


def test_intraday_same_bar_stop_precedes_targets() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=106, low=95, close=103),
    )
    outcome = _simulate(
        rows,
        index=0,
        direction=1,
        features=_features(),
        score=1,
        spec=replace(PREREGISTERED_CANDIDATES[0], maximum_holding_hours=1),
    )

    assert outcome.entry == 100
    assert outcome.exit_reason == "STOP"
    assert outcome.tp1_hit_ts_ms is None
    assert outcome.gross_bps < 0


def test_intraday_tp1_then_cost_protected_stop_records_milestones() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=102.2, low=99.5, close=102),
        _bar(2, open_=102, high=102.1, low=100, close=100.2),
    )
    outcome = _simulate(
        rows,
        index=0,
        direction=1,
        features=_features(),
        score=1,
        spec=replace(PREREGISTERED_CANDIDATES[0], maximum_holding_hours=1),
    )

    assert outcome.exit_reason == "STOP_AFTER_TP1"
    assert outcome.tp1_hit_ts_ms == rows[1].close_ts_ms
    assert outcome.tp2_hit_ts_ms is None
    assert outcome.exit_ts_ms == rows[2].close_ts_ms
    assert outcome.gross_bps > 0


def test_intraday_portfolio_limit_prefers_two_highest_scores() -> None:
    low = _outcome("BTCUSDT", 1)
    high = replace(low, symbol="ETHUSDT", score=9)
    medium = replace(low, symbol="SOLUSDT", score=4)

    selected = apply_portfolio_limits((low, high, medium))

    assert [row.symbol for row in selected] == ["ETHUSDT", "SOLUSDT"]
