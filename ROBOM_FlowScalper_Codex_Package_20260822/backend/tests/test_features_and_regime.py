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
        )
    )


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
