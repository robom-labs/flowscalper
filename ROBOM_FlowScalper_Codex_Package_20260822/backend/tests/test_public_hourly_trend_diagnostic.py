# 시간봉 집계와 적응형 추세 진단의 완성 봉·동시 포지션 경계를 검증한다.

from __future__ import annotations

from dataclasses import replace

from scripts.research_public_hourly_trend_diagnostic import (
    HOUR_MS,
    HourOutcome,
    aggregate_hourly,
    apply_hourly_portfolio_limits,
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


def _outcome(symbol: str, score: float) -> HourOutcome:
    return HourOutcome(
        candidate_id="TEST",
        symbol=symbol,
        side="LONG",
        signal_ts_ms=HOUR_MS - 1,
        entry_ts_ms=HOUR_MS,
        exit_ts_ms=2 * HOUR_MS,
        holding_hours=2,
        exit_reason="MAX_HOLD",
        entry=100,
        stop=98,
        take_profit_1=103,
        take_profit_2=106,
        gross_bps=10,
        base_net_bps=-3,
        stress_net_bps=-15,
        score=score,
    )


def test_hourly_aggregation_requires_all_twelve_completed_five_minute_bars() -> None:
    complete = aggregate_hourly(tuple(_kline(index) for index in range(12)))
    incomplete = aggregate_hourly(tuple(_kline(index) for index in range(11)))

    assert len(complete) == 1
    assert complete[0].open_ts_ms == 0
    assert complete[0].close_ts_ms == HOUR_MS - 1
    assert incomplete == ()


def test_hourly_portfolio_uses_score_and_two_position_cap() -> None:
    low = _outcome("BTCUSDT", 1)
    high = replace(low, symbol="ETHUSDT", score=9)
    medium = replace(low, symbol="SOLUSDT", score=4)

    selected = apply_hourly_portfolio_limits((low, high, medium))

    assert [row.symbol for row in selected] == ["ETHUSDT", "SOLUSDT"]
