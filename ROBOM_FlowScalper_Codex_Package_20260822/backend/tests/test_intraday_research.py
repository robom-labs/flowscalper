# 완성 캔들 장중 피처와 원본·미러 연구 계약을 결정적으로 검증한다.

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from backend.app.domain.models import Side
from backend.app.intraday import (
    CandidateFamily,
    HorizonClass,
    IntradayCandidateEvaluator,
    MultiTimeframeFeatureEngine,
    ResearchVariantKind,
    SignalVariant,
    TimeframeFeatureSnapshot,
    build_research_price_plan,
    pair_original_and_mechanical_mirror,
)
from backend.app.market_data import Candle


def _candle(
    *,
    interval: int,
    index: int,
    close: str,
    volume: str = "10",
    buy_fraction: str = "0.6",
) -> Candle:
    price = Decimal(close)
    quantity = Decimal(volume)
    buy = quantity * Decimal(buy_fraction)
    sell = quantity - buy
    return Candle(
        symbol="BTCUSDT",
        interval_seconds=interval,
        open_ts_ms=index * interval * 1_000,
        open=price - Decimal("0.1"),
        high=price + Decimal("0.5"),
        low=price - Decimal("0.5"),
        close=price,
        volume=quantity,
        trade_count=5,
        quote_volume=price * quantity,
        taker_buy_volume=buy,
        taker_sell_volume=sell,
        taker_buy_quote_volume=price * buy,
        taker_sell_quote_volume=price * sell,
    )


def test_multitimeframe_features_use_only_bars_completed_by_as_of_time() -> None:
    engine = MultiTimeframeFeatureEngine(intervals=(60, 300), minimum_bars=20)
    for index in range(12):
        engine.ingest_completed(
            _candle(interval=300, index=index, close=str(90 + index))
        )
    for index in range(61):
        engine.ingest_completed(
            _candle(interval=60, index=index, close=str(100 + index), volume=str(10 + index))
        )
    as_of = 61 * 60 * 1_000
    snapshot = engine.snapshot(
        "BTCUSDT",
        60,
        as_of_ts_ms=as_of,
        higher_interval_seconds=300,
    )
    assert snapshot is not None
    assert snapshot.feature_ts_ms == as_of
    assert snapshot.source_open_ts_ms == 60 * 60 * 1_000
    assert snapshot.close == 160
    assert snapshot.donchian_high == 159.5
    assert snapshot.higher_timeframe_trend == "UP"
    expected_vwap = sum((100 + index) * (10 + index) for index in range(61)) / sum(
        10 + index for index in range(61)
    )
    assert abs(snapshot.session_vwap - expected_vwap) < 1e-12

    engine.ingest_completed(_candle(interval=60, index=61, close="999"))
    unchanged = engine.snapshot(
        "BTCUSDT",
        60,
        as_of_ts_ms=as_of,
        higher_interval_seconds=300,
    )
    assert unchanged == snapshot


def test_multitimeframe_features_fail_closed_on_duplicate_out_of_order_and_missing_warmup() -> None:
    engine = MultiTimeframeFeatureEngine(intervals=(60,), minimum_bars=3)
    first = _candle(interval=60, index=1, close="100")
    assert engine.ingest_completed(first) is True
    assert engine.ingest_completed(first) is False
    assert engine.ingest_completed(_candle(interval=60, index=0, close="999")) is False
    assert engine.snapshot("BTCUSDT", 60) is None
    assert engine.duplicate_bars == 1
    assert engine.out_of_order_bars == 1


def test_mechanical_mirror_keeps_timestamp_information_and_symmetric_geometry() -> None:
    original = SignalVariant(
        candidate_id="FLOW_TREND_PULLBACK",
        variant=ResearchVariantKind.ORIGINAL,
        symbol="BTCUSDT",
        side=Side.LONG,
        signal_ts_ms=123_000,
        interval_seconds=60,
        information_set_id="BTCUSDT:60:123000",
    )
    paired_original, mirror = pair_original_and_mechanical_mirror(original)
    assert paired_original == original
    assert mirror.side is Side.SHORT
    assert mirror.signal_ts_ms == original.signal_ts_ms
    assert mirror.information_set_id == original.information_set_id

    long_plan = build_research_price_plan(
        side=original.side,
        signal_ts_ms=original.signal_ts_ms,
        executable_entry=Decimal("100.1"),
        atr=Decimal("1"),
        horizon=HorizonClass.FAST_INTRADAY,
    )
    short_plan = build_research_price_plan(
        side=mirror.side,
        signal_ts_ms=mirror.signal_ts_ms,
        executable_entry=Decimal("99.9"),
        atr=Decimal("1"),
        horizon=HorizonClass.FAST_INTRADAY,
    )
    assert long_plan.risk_distance == short_plan.risk_distance == Decimal("1.1")
    assert long_plan.take_profit_1 - long_plan.entry == (
        short_plan.entry - short_plan.take_profit_1
    )
    assert long_plan.take_profit_2 - long_plan.entry == (
        short_plan.entry - short_plan.take_profit_2
    )


def test_candidate_families_remain_research_only_and_reverse_is_separate_hypothesis() -> None:
    snapshot = TimeframeFeatureSnapshot(
        symbol="BTCUSDT",
        interval_seconds=60,
        feature_ts_ms=60_000,
        source_open_ts_ms=0,
        sample_count=30,
        close=102,
        atr=1,
        realized_volatility=0.01,
        session_vwap=101,
        ema_fast=101.5,
        ema_slow=100.5,
        donchian_high=101,
        donchian_low=95,
        bollinger_mid=100,
        bollinger_upper=101,
        bollinger_lower=99,
        keltner_upper=102,
        keltner_lower=98,
        relative_volume=2,
        taker_flow_ratio=0.4,
        close_zscore=2,
        higher_timeframe_trend="UP",
        regime="TREND_UP",
    )
    signals = IntradayCandidateEvaluator().evaluate(snapshot)
    families = {signal.family for signal in signals}
    assert CandidateFamily.COMPRESSION_RVOL_BREAKOUT in families
    assert CandidateFamily.HTF_TREND_ENTRY in families
    original = signals[0]
    reverse = IntradayCandidateEvaluator.hypothesis_reverse(original)
    assert reverse.variant is ResearchVariantKind.HYPOTHESIS_REVERSE
    assert reverse.candidate_id != original.candidate_id
    assert reverse.information_set_id == original.information_set_id
    assert reverse.side is not original.side

    range_snapshot = replace(
        snapshot,
        close=97,
        close_zscore=-2.1,
        taker_flow_ratio=0.1,
        higher_timeframe_trend="FLAT",
        regime="RANGE",
    )
    assert CandidateFamily.RANGE_VWAP_REVERSION in {
        signal.family for signal in IntradayCandidateEvaluator().evaluate(range_snapshot)
    }
