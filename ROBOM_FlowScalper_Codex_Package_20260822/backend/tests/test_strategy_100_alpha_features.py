# 100후보 공통 피처가 완료봉·시간순 universe·신선한 미세구조만 사용하는지 검증한다.

from __future__ import annotations

from decimal import Decimal

from backend.app.domain.models import Venue
from backend.app.features import FeatureSnapshot
from backend.app.market_data import Candle
from backend.app.research import AlphaFeatureBuilder


def _candle(
    symbol: str,
    interval: int,
    index: int,
    *,
    base: float = 100,
    step: float = 0.1,
    quote_volume: str | None = None,
) -> Candle:
    close = Decimal(str(base + index * step))
    volume = Decimal("100") + index
    return Candle(
        symbol=symbol,
        interval_seconds=interval,
        open_ts_ms=index * interval * 1_000,
        open=close - Decimal("0.05"),
        high=close + Decimal("0.05"),
        low=close - Decimal("0.1"),
        close=close,
        volume=volume,
        trade_count=100 + index,
        quote_volume=(Decimal(quote_volume) if quote_volume is not None else close * volume),
        taker_buy_volume=Decimal("65"),
        taker_sell_volume=Decimal("35"),
    )


def _micro(ts_ms: int, *, healthy: bool = True) -> FeatureSnapshot:
    return FeatureSnapshot(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        ts_ms=ts_ms,
        sample_count=100,
        warmup_seconds=120,
        data_healthy=healthy,
        lag_ms=20,
        mid=100,
        spread_bps=2,
        depth_bid_10=100_000,
        depth_ask_10=100_000,
        imbalance_top1=0.4,
        imbalance_top5=0.4,
        imbalance_top10=0.3,
        microprice=100.1,
        microprice_minus_mid_bps=10,
        ofi_250ms=1,
        ofi_1s=2,
        ofi_3s=3,
        ofi_10s=4,
        trade_imbalance_1s=0.4,
        trade_imbalance_3s=0.4,
        trade_imbalance_10s=0.3,
        signed_notional_3s=10_000 + ts_ms,
        refill_ratio=0.7,
        cancel_ratio=0.3,
        price_response_efficiency=0.1,
        realized_volatility_30s=0.001,
        realized_volatility_120s=0.0005,
        compression_ratio=2,
        efficiency_ratio_30s=0.2,
        micro_vwap_10s=100.1,
        depth_adjusted_ofi_3s_bps=10,
        trade_count_3s=20,
        trade_notional_3s=20_000 + ts_ms,
        bid_refill_ratio_3s=0.6,
        ask_refill_ratio_3s=0.7,
        bid_cancel_ratio_3s=0.3,
        ask_cancel_ratio_3s=0.4,
    )


def test_completed_candle_snapshot_is_deterministic_on_first_post_close_event() -> None:
    builder = AlphaFeatureBuilder()
    for index in range(60):
        builder.ingest_completed(_candle("BTCUSDT", 300, index))
        builder.ingest_completed(_candle("BTCUSDT", 3_600, index))
        builder.ingest_completed(_candle("BTCUSDT", 14_400, index))
    decision = 60 * 300 * 1_000

    first = builder.snapshot("BTCUSDT", "F04", decision_ts_ms=decision)
    second = builder.snapshot("BTCUSDT", "F04", decision_ts_ms=decision)

    assert first == second
    assert first is not None
    assert first.completed_candle_close_ts_ms == decision
    assert first.prior_donchian20_high < first.close
    delayed = builder.snapshot("BTCUSDT", "F04", decision_ts_ms=decision + 1)
    assert delayed is not None
    assert delayed.decision_ts_ms == decision + 1
    assert delayed.completed_candle_close_ts_ms == decision
    assert builder.snapshot("BTCUSDT", "F04", decision_ts_ms=decision + 300_000) is None


def test_candle_gap_clears_the_contiguous_family_warmup() -> None:
    builder = AlphaFeatureBuilder()
    for index in range(21):
        builder.ingest_completed(_candle("BTCUSDT", 300, index))
    builder.ingest_completed(_candle("BTCUSDT", 300, 22))

    assert builder.diagnostics.candle_gaps == 1
    assert builder.snapshot("BTCUSDT", "F04", decision_ts_ms=23 * 300 * 1_000) is None


def test_cross_sectional_rank_uses_same_completed_six_hour_timestamp() -> None:
    builder = AlphaFeatureBuilder()
    symbols = [f"COIN{number:02d}USDT" for number in range(20)]
    for symbol_number, symbol in enumerate(symbols):
        for index in range(5):
            builder.ingest_completed(
                _candle(
                    symbol,
                    21_600,
                    index,
                    step=0.1 + symbol_number * 0.01,
                    quote_volume="6000000",
                )
            )
    decision = 5 * 21_600 * 1_000

    top = builder.snapshot(symbols[-1], "F16", decision_ts_ms=decision)

    assert top is not None
    assert top.point_in_time_universe_size == 20
    assert top.cross_sectional_rank == 1
    assert top.liquidity_floor_passed is True


def test_multitimeframe_pullback_uses_completed_fifteen_minute_setup() -> None:
    builder = AlphaFeatureBuilder()
    decision = 60 * 14_400 * 1_000
    for interval in (300, 900, 3_600, 14_400):
        for index in range(decision // (interval * 1_000)):
            builder.ingest_completed(_candle("BTCUSDT", interval, index))

    snapshot = builder.snapshot("BTCUSDT", "F03", decision_ts_ms=decision)

    assert snapshot is not None
    assert snapshot.setup_15m_trend.value == "UP"
    assert snapshot.setup_pullback_distance_atr is not None


def test_session_anchored_vwap_confirmation_is_chronological() -> None:
    builder = AlphaFeatureBuilder()
    for index in range(30):
        builder.ingest_completed(_candle("BTCUSDT", 300, index))
    decision = 30 * 300 * 1_000

    snapshot = builder.snapshot("BTCUSDT", "F09", decision_ts_ms=decision)

    assert snapshot is not None
    assert snapshot.anchored_vwap_confirmation_side is not None
    assert snapshot.anchored_vwap_confirmation_bars >= 2


def test_microstructure_snapshot_is_fresh_cost_aware_and_persistent() -> None:
    builder = AlphaFeatureBuilder()
    for index in range(20):
        builder.ingest_completed(_candle("BTCUSDT", 1, index))
    decision = 20_000
    for timestamp in (19_000, 19_500, 20_000):
        builder.ingest_microstructure(_micro(timestamp))

    snapshot = builder.snapshot("BTCUSDT", "F17", decision_ts_ms=decision)

    assert snapshot is not None
    assert snapshot.sequence_valid is True
    assert snapshot.data_stale is False
    assert snapshot.queue_imbalance_top5 == 0.7
    assert snapshot.microstructure_persistence_ms == 1_000
    assert snapshot.cost_viability_passed is True
    assert snapshot.bid_refill_ratio == 0.6
    assert snapshot.ask_refill_ratio == 0.7


def test_missing_or_old_microstructure_fails_closed_without_infinite_values() -> None:
    builder = AlphaFeatureBuilder()
    for index in range(20):
        builder.ingest_completed(_candle("BTCUSDT", 1, index))

    snapshot = builder.snapshot("BTCUSDT", "F17", decision_ts_ms=20_000)

    assert snapshot is not None
    assert snapshot.sequence_valid is False
    assert snapshot.data_stale is True
    assert snapshot.cost_viability_passed is False
    assert snapshot.spread_bps == 10_000
