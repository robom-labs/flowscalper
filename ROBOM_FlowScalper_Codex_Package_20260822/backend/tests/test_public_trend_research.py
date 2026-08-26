# 공개 완성 봉 추세 연구의 다음 봉 진입·비용·동시체결 보수성을 검증한다.

from __future__ import annotations

from dataclasses import replace

from scripts.research_public_trend_candidates import (
    BAR_INTERVAL_MS,
    BASE_COST_BPS,
    PREREGISTERED_CANDIDATES,
    Kline,
    TrendSignal,
    apply_portfolio_limits,
    simulate_signal,
)


def _bar(index: int, *, open_: float, high: float, low: float, close: float) -> Kline:
    return Kline(
        symbol="BTCUSDT",
        open_ts_ms=index * BAR_INTERVAL_MS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=10,
        taker_buy_volume=5,
    )


def _signal() -> TrendSignal:
    return TrendSignal(
        candidate_id=PREREGISTERED_CANDIDATES[0].candidate_id,
        symbol="BTCUSDT",
        side="LONG",
        signal_ts_ms=BAR_INTERVAL_MS - 1,
        entry_ts_ms=BAR_INTERVAL_MS,
        score=2,
        atr=1,
        signal_close=100,
    )


def test_signal_enters_at_next_bar_open_and_charges_roundtrip_cost() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=101, high=101.5, low=100.8, close=101.2),
        _bar(2, open_=101.2, high=101.4, low=101.0, close=101.3),
    )
    spec = PREREGISTERED_CANDIDATES[0]
    outcome = simulate_signal(rows, 0, _signal(), spec)

    assert outcome.entry == 101
    assert outcome.entry_ts_ms == rows[1].open_ts_ms
    assert outcome.base_net_bps == outcome.gross_bps - BASE_COST_BPS


def test_same_bar_stop_is_conservatively_taken_before_targets() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=106, low=95, close=103),
    )
    outcome = simulate_signal(rows, 0, _signal(), PREREGISTERED_CANDIDATES[0])

    assert outcome.exit_reason == "STOP"
    assert outcome.gross_bps < 0


def test_portfolio_limit_prefers_higher_score_at_same_entry_time() -> None:
    rows = (
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=100.5, low=99.5, close=100),
    )
    first = simulate_signal(rows, 0, _signal(), PREREGISTERED_CANDIDATES[0])
    higher = replace(first, symbol="ETHUSDT", score=9)
    third = replace(first, symbol="SOLUSDT", score=3)

    selected = apply_portfolio_limits((first, higher, third))

    assert [row.symbol for row in selected] == ["ETHUSDT", "SOLUSDT"]
