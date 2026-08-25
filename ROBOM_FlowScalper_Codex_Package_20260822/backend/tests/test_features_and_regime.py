"""다중창 피처의 결정성·유한성, 레짐과 stale 후보 차단을 검증한다."""

import math
from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.candidates import CandidateRanker, CandidateSeed
from backend.app.domain.market import TradeTick
from backend.app.domain.models import Venue
from backend.app.features import BookFrame, FeatureEngine
from backend.app.features.engine import FeatureInputError
from backend.app.regime import Regime, RegimeClassifier


def build_engine(*, stale_last: bool = False) -> FeatureEngine:
    engine = FeatureEngine()
    start = 1_000_000
    for index in range(49):
        midpoint = Decimal("100") + Decimal(index) * Decimal("0.01")
        bid = midpoint - Decimal("0.01")
        ask = midpoint + Decimal("0.01")
        ts_ms = start + index * 250
        engine.ingest_book(
            BookFrame.from_levels(
                venue=Venue.FIXTURE,
                symbol="BTCUSDT",
                ts_ms=ts_ms,
                bids=[(bid, Decimal(10 + index % 3)), (bid - Decimal("0.01"), Decimal("8"))],
                asks=[(ask, Decimal(8)), (ask + Decimal("0.01"), Decimal("7"))],
                stale=stale_last and index == 48,
                sequence_valid=not (stale_last and index == 48),
                lag_ms=600 if stale_last and index == 48 else 20,
            )
        )
        engine.ingest_trade(
            TradeTick(
                venue=Venue.FIXTURE,
                symbol="BTCUSDT",
                price=midpoint,
                quantity=Decimal("0.5"),
                trade_ts_ms=ts_ms,
                buyer_is_aggressor=index % 4 != 0,
            )
        )
    return engine


def test_features_are_deterministic_and_finite() -> None:
    first = build_engine().snapshot()
    second = build_engine().snapshot()
    assert first == second
    assert first.sample_count == 49
    assert first.warmup_seconds == 12.0
    assert first.mid == pytest.approx(100.48)
    assert first.imbalance_top10 > 0
    assert first.micro_vwap_10s > 0
    assert all(
        math.isfinite(value)
        for value in (
            first.spread_bps,
            first.ofi_250ms,
            first.ofi_1s,
            first.ofi_3s,
            first.ofi_10s,
            first.realized_volatility_30s,
            first.compression_ratio,
            first.bid_book_slope_10,
            first.ask_book_slope_10,
        )
    )
    assert first.bid_book_slope_10 > first.ask_book_slope_10


def test_single_pass_window_metrics_match_reference_calculations() -> None:
    engine = build_engine()
    snapshot = engine.snapshot()
    latest = engine._books[-1]
    mids = [
        (book.ts_ms, float((book.bids[0][0] + book.asks[0][0]) / 2))
        for book in engine._books
    ]

    assert snapshot.ofi_250ms == pytest.approx(
        engine._window_sum(engine._ofi, latest.ts_ms, 250)
    )
    assert snapshot.ofi_1s == pytest.approx(
        engine._window_sum(engine._ofi, latest.ts_ms, 1_000)
    )
    assert snapshot.ofi_3s == pytest.approx(
        engine._window_sum(engine._ofi, latest.ts_ms, 3_000)
    )
    assert snapshot.ofi_10s == pytest.approx(
        engine._window_sum(engine._ofi, latest.ts_ms, 10_000)
    )
    assert snapshot.trade_imbalance_1s == pytest.approx(
        engine._trade_imbalance(latest.ts_ms, 1_000)
    )
    assert snapshot.trade_imbalance_3s == pytest.approx(
        engine._trade_imbalance(latest.ts_ms, 3_000)
    )
    assert snapshot.trade_imbalance_10s == pytest.approx(
        engine._trade_imbalance(latest.ts_ms, 10_000)
    )
    assert snapshot.signed_notional_3s == pytest.approx(
        engine._signed_notional(latest.ts_ms, 3_000)
    )
    assert snapshot.refill_ratio == pytest.approx(
        engine._depth_ratio(latest.ts_ms, 3_000, refill=True)
    )
    assert snapshot.cancel_ratio == pytest.approx(
        engine._depth_ratio(latest.ts_ms, 3_000, refill=False)
    )
    assert snapshot.price_response_efficiency == pytest.approx(
        engine._price_response(mids, latest.ts_ms, 3_000)
    )
    assert snapshot.realized_volatility_30s == pytest.approx(
        engine._realized_volatility(mids, latest.ts_ms, 30_000)
    )
    assert snapshot.realized_volatility_120s == pytest.approx(
        engine._realized_volatility(mids, latest.ts_ms, 120_000)
    )
    assert snapshot.compression_ratio == pytest.approx(
        engine._compression(mids, latest.ts_ms)
    )
    assert snapshot.efficiency_ratio_30s == pytest.approx(
        engine._efficiency_ratio(mids, latest.ts_ms, 30_000)
    )
    assert snapshot.micro_vwap_10s == pytest.approx(
        engine._micro_vwap(latest.ts_ms, 10_000, snapshot.mid)
    )
    bid_quantity = sum(quantity for _, quantity in latest.bids[:10])
    ask_quantity = sum(quantity for _, quantity in latest.asks[:10])
    bid_vwap = Decimal(str(snapshot.depth_bid_10)) / bid_quantity
    ask_vwap = Decimal(str(snapshot.depth_ask_10)) / ask_quantity
    multi_level_microprice = (
        bid_vwap * ask_quantity + ask_vwap * bid_quantity
    ) / (bid_quantity + ask_quantity)
    assert snapshot.multi_level_microprice_10 == pytest.approx(
        float(multi_level_microprice)
    )
    assert snapshot.multi_level_microprice_10_minus_mid_bps == pytest.approx(
        (float(multi_level_microprice) - snapshot.mid) / snapshot.mid * 10_000
    )
    average_depth_notional = (
        snapshot.depth_bid_10 + snapshot.depth_ask_10
    ) / 2
    assert snapshot.depth_adjusted_ofi_3s_bps == pytest.approx(
        snapshot.ofi_3s * snapshot.mid / average_depth_notional * 10_000
    )
    assert snapshot.bid_book_slope_10 == pytest.approx(
        engine._book_slope(latest.bids[:10], Decimal(str(snapshot.mid)))
    )
    assert snapshot.ask_book_slope_10 == pytest.approx(
        engine._book_slope(latest.asks[:10], Decimal(str(snapshot.mid)))
    )


def test_feature_history_retains_only_each_metric_maximum_window() -> None:
    engine = FeatureEngine()
    start = 1_000_000
    for index in range(125):
        timestamp = start + index * 1_000
        midpoint = Decimal("100") + Decimal(index) * Decimal("0.01")
        engine.ingest_book(
            BookFrame.from_levels(
                venue=Venue.FIXTURE,
                symbol="BTCUSDT",
                ts_ms=timestamp,
                bids=[(midpoint - Decimal("0.01"), Decimal("10"))],
                asks=[(midpoint + Decimal("0.01"), Decimal("10"))],
            )
        )
        engine.ingest_trade(
            TradeTick(
                venue=Venue.FIXTURE,
                symbol="BTCUSDT",
                price=midpoint,
                quantity=Decimal("1"),
                trade_ts_ms=timestamp,
                buyer_is_aggressor=True,
            )
        )

    now_ms = start + 124_000
    assert engine._books[0].ts_ms == now_ms - 120_000
    assert engine._trades[0].trade_ts_ms == now_ms - 10_000
    assert engine._ofi[0][0] == now_ms - 10_000
    assert engine._depth_changes[0][0] == now_ms - 3_000


def test_degraded_data_cannot_produce_candidate() -> None:
    snapshot = build_engine(stale_last=True).snapshot()
    regime = RegimeClassifier().classify(snapshot)
    seed = CandidateSeed(
        symbol="BTCUSDT",
        strategy_id="TEST",
        structure_quality=1,
        flow_confirmation=1,
        price_response_quality=1,
        liquidity_quality=1,
        regime_fit=1,
        cost_penalty=0,
        latency_penalty=0,
        uncertainty_penalty=0,
    )
    candidate = CandidateRanker().evaluate(seed, snapshot, regime)
    assert regime is Regime.DEGRADED
    assert candidate.score is None
    assert "STALE_OR_DEGRADED_DATA" in candidate.rejection_codes
    assert candidate.tp_probability is None
    assert candidate.calibration_status == "CALIBRATING"


def test_regime_warmup_shock_and_range_are_explicit() -> None:
    snapshot = build_engine().snapshot()
    classifier = RegimeClassifier(minimum_warmup_seconds=20)
    assert classifier.classify(snapshot) is Regime.WARMUP
    assert RegimeClassifier(shock_volatility=0).classify(snapshot) is Regime.SHOCK
    range_snapshot = replace(
        snapshot,
        efficiency_ratio_30s=0.1,
        realized_volatility_30s=0.0001,
        spread_bps=2,
    )
    assert RegimeClassifier().classify(range_snapshot) is Regime.RANGE


def test_invalid_numeric_book_is_rejected_before_features() -> None:
    with pytest.raises(FeatureInputError):
        BookFrame.from_levels(
            venue=Venue.FIXTURE,
            symbol="BTCUSDT",
            ts_ms=1,
            bids=[(Decimal("NaN"), Decimal("1"))],
            asks=[(Decimal("101"), Decimal("1"))],
        )


def test_candidate_ranking_is_stable() -> None:
    snapshot = build_engine().snapshot()
    seed_a = CandidateSeed("ETHUSDT", "A", 0.7, 0.7, 0.7, 0.7, 0.7, 0.1, 0.1, 0.1)
    seed_b = CandidateSeed("BTCUSDT", "B", 0.9, 0.9, 0.9, 0.9, 0.9, 0.1, 0.1, 0.1)
    ranked = CandidateRanker().rank(
        [(seed_a, snapshot, Regime.RANGE), (seed_b, snapshot, Regime.RANGE)]
    )
    assert [item.symbol for item in ranked] == ["BTCUSDT", "ETHUSDT"]
