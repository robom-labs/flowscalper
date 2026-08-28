# 100후보 F03~F20 evaluator의 완성봉·LONG/SHORT 대칭·미세구조 fail-closed를 검증한다.

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.app.domain.models import Side
from backend.app.research import (
    ALPHA_EVALUATION_INTERVAL_SECONDS,
    ALPHA_EVALUATORS,
    ALPHA_FAMILIES,
    AlphaEvaluationError,
    AlphaFeatureSnapshot,
    TrendDirection,
    evaluate_alpha,
)


def _snapshot(side: Side) -> AlphaFeatureSnapshot:
    long = side is Side.LONG
    return AlphaFeatureSnapshot(
        symbol="BTCUSDT",
        decision_ts_ms=10_000,
        completed_candle_close_ts_ms=10_000,
        interval_seconds=300,
        close=101 if long else 99,
        previous_close=100,
        open=100,
        high=102 if long else 101,
        low=99 if long else 98,
        atr=2,
        ema20=100,
        ema50=99 if long else 101,
        ema_slope=0.1 if long else -0.1,
        adx=30,
        rsi=55 if long else 45,
        relative_volume=2,
        trade_count_z=2,
        taker_ratio=0.65 if long else 0.35,
        close_location=0.9 if long else 0.1,
        realized_volatility_fast=0.03,
        realized_volatility_slow=0.01,
        prior_donchian20_high=100.5 if long else 110,
        prior_donchian20_low=90 if long else 99.5,
        prior_donchian55_high=100.5 if long else 120,
        prior_donchian55_low=80 if long else 99.5,
        session_vwap=100,
        anchored_vwap=100.5 if long else 99.5,
        previous_anchored_vwap=100.2 if long else 99.8,
        completed_structure_long_stop=98,
        completed_structure_short_stop=102,
        bollinger_upper=100.5 if long else 110,
        bollinger_lower=90 if long else 99.5,
        bandwidth_percentile=10,
        keltner_upper=102,
        keltner_lower=98,
        compression_bars=3,
        higher_1h_trend=TrendDirection.UP if long else TrendDirection.DOWN,
        higher_4h_trend=TrendDirection.UP if long else TrendDirection.DOWN,
        setup_15m_trend=TrendDirection.UP if long else TrendDirection.DOWN,
        setup_pullback_distance_atr=0.1,
        supertrend_side=side,
        anchored_vwap_confirmation_side=side,
        anchored_vwap_confirmation_bars=2,
        breakout_side=side,
        bars_since_breakout=1,
        retest_distance_atr=0.1,
        structure_reclaimed=True,
        ofi_aligned=True,
        momentum_6h=0.03 if long else -0.03,
        momentum_24h=0.1 if long else -0.1,
        momentum_volatility_ratio=2,
        cross_sectional_rank=0.9 if long else 0.1,
        point_in_time_universe_size=30,
        liquidity_floor_passed=True,
        opening_range_high=100.5 if long else 110,
        opening_range_low=90 if long else 99.5,
        opening_range_complete=True,
        spread_bps=2,
        spread_percentile=50,
        sequence_valid=True,
        data_stale=False,
        queue_imbalance_top5=0.7 if long else 0.3,
        microprice_spread_fraction=0.2 if long else -0.2,
        microstructure_persistence_ms=600,
        cost_viability_passed=True,
        mlofi_robust_z=2.5 if long else -2.5,
        price_response_aligned=True,
        signed_notional_z=2.5 if long else -2.5,
        trade_intensity_z=2,
        opposing_depth_depletion=0.3,
        regime="RANGE",
        vwap_deviation_z=-2.5 if long else 2.5,
        price_progress_efficiency=0.2,
        refill_ratio=0.3,
        bid_refill_ratio=0.3,
        ask_refill_ratio=0.3,
        ofi_reversal_confirmed=True,
        microprice_reentry_confirmed=True,
    )


def _parameters(family_id: str) -> dict[str, str]:
    family = next(spec for spec in ALPHA_FAMILIES if spec.family_id == family_id)
    return dict(family.parameters)


@pytest.mark.parametrize("family_id", sorted(ALPHA_EVALUATORS))
@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_all_implemented_alpha_families_have_symmetric_executable_signal(
    family_id: str,
    side: Side,
) -> None:
    snapshot = replace(
        _snapshot(side),
        interval_seconds=ALPHA_EVALUATION_INTERVAL_SECONDS[family_id],
    )
    signal = evaluate_alpha(family_id, snapshot, _parameters(family_id))

    assert signal is not None
    assert signal.family_id == family_id
    assert signal.side is side
    assert signal.signal_ts_ms == signal.completed_candle_close_ts_ms
    assert signal.reason_codes


def test_alpha_snapshot_rejects_incomplete_future_candle() -> None:
    with pytest.raises(AlphaEvaluationError, match="완성봉"):
        replace(_snapshot(Side.LONG), completed_candle_close_ts_ms=10_001)


def test_alpha_signal_records_actual_decision_after_completed_candle() -> None:
    snapshot = replace(
        _snapshot(Side.LONG),
        decision_ts_ms=10_123,
        completed_candle_close_ts_ms=10_000,
        interval_seconds=ALPHA_EVALUATION_INTERVAL_SECONDS["F04"],
    )

    signal = evaluate_alpha("F04", snapshot, _parameters("F04"))

    assert signal is not None
    assert signal.signal_ts_ms == 10_123
    assert signal.completed_candle_close_ts_ms == 10_000


def test_alpha_family_does_not_signal_on_a_different_timeframe() -> None:
    assert evaluate_alpha("F15", _snapshot(Side.LONG), _parameters("F15")) is None


def test_f15_uses_four_completed_6h_bars_as_24h_momentum() -> None:
    snapshot = replace(
        _snapshot(Side.LONG),
        interval_seconds=ALPHA_EVALUATION_INTERVAL_SECONDS["F15"],
        momentum_6h=-0.03,
        momentum_24h=0.03,
    )

    signal = evaluate_alpha("F15", snapshot, _parameters("F15"))

    assert signal is not None
    assert "TWENTY_FOUR_HOUR_MOMENTUM" in signal.reason_codes


def test_alpha_family_rejects_any_post_registration_parameter_change() -> None:
    parameters = _parameters("F09")
    parameters["confirmation_bars"] = "1"

    with pytest.raises(AlphaEvaluationError, match="parameter 계약"):
        evaluate_alpha("F09", _snapshot(Side.LONG), parameters)


def test_f09_requires_the_preregistered_two_completed_confirmation_bars() -> None:
    snapshot = replace(
        _snapshot(Side.LONG),
        anchored_vwap_confirmation_bars=1,
    )

    assert evaluate_alpha("F09", snapshot, _parameters("F09")) is None


@pytest.mark.parametrize("family_id", ["F01", "F02"])
def test_unconfirmed_siho_alpha_families_fail_closed(family_id: str) -> None:
    with pytest.raises(AlphaEvaluationError, match="evaluator"):
        evaluate_alpha(family_id, _snapshot(Side.LONG), {})


@pytest.mark.parametrize("family_id", ["F17", "F18", "F19", "F20"])
def test_microstructure_families_reject_stale_or_sequence_invalid_book(
    family_id: str,
) -> None:
    stale = replace(_snapshot(Side.LONG), interval_seconds=1, data_stale=True)
    invalid = replace(_snapshot(Side.LONG), interval_seconds=1, sequence_valid=False)

    assert evaluate_alpha(family_id, stale, _parameters(family_id)) is None
    assert evaluate_alpha(family_id, invalid, _parameters(family_id)) is None
