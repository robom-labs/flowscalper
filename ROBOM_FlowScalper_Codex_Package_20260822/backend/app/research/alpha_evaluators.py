# 100후보의 F03~F20 alpha를 미래정보 없이 동일한 연구 snapshot에서 결정적으로 평가한다.

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from backend.app.domain.models import Side


class AlphaEvaluationError(ValueError):
    """연구 snapshot이나 사전등록 parameter가 불완전할 때 실행을 거부한다."""


class TrendDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AlphaFeatureSnapshot:
    symbol: str
    decision_ts_ms: int
    completed_candle_close_ts_ms: int
    interval_seconds: int
    close: float
    previous_close: float
    open: float
    high: float
    low: float
    atr: float
    ema20: float
    ema50: float
    ema_slope: float
    adx: float
    rsi: float
    relative_volume: float
    trade_count_z: float
    taker_ratio: float
    close_location: float
    realized_volatility_fast: float
    realized_volatility_slow: float
    prior_donchian20_high: float
    prior_donchian20_low: float
    prior_donchian55_high: float
    prior_donchian55_low: float
    session_vwap: float
    anchored_vwap: float
    previous_anchored_vwap: float
    completed_structure_long_stop: float
    completed_structure_short_stop: float
    bollinger_upper: float
    bollinger_lower: float
    bandwidth_percentile: float
    keltner_upper: float
    keltner_lower: float
    compression_bars: int
    higher_1h_trend: TrendDirection
    higher_4h_trend: TrendDirection
    setup_15m_trend: TrendDirection
    setup_pullback_distance_atr: float | None
    supertrend_side: Side | None
    anchored_vwap_confirmation_side: Side | None
    anchored_vwap_confirmation_bars: int
    breakout_side: Side | None
    bars_since_breakout: int | None
    retest_distance_atr: float | None
    structure_reclaimed: bool
    ofi_aligned: bool
    momentum_6h: float
    momentum_24h: float
    momentum_volatility_ratio: float
    cross_sectional_rank: float | None
    point_in_time_universe_size: int
    liquidity_floor_passed: bool
    opening_range_high: float | None
    opening_range_low: float | None
    opening_range_complete: bool
    spread_bps: float
    spread_percentile: float
    sequence_valid: bool
    data_stale: bool
    queue_imbalance_top5: float
    microprice_spread_fraction: float
    microstructure_persistence_ms: int
    cost_viability_passed: bool
    mlofi_robust_z: float
    price_response_aligned: bool
    signed_notional_z: float
    trade_intensity_z: float
    opposing_depth_depletion: float
    regime: str
    vwap_deviation_z: float
    price_progress_efficiency: float
    refill_ratio: float
    bid_refill_ratio: float
    ask_refill_ratio: float
    ofi_reversal_confirmed: bool
    microprice_reentry_confirmed: bool

    def __post_init__(self) -> None:
        if (
            not self.symbol
            or self.decision_ts_ms < 0
            or self.completed_candle_close_ts_ms < 0
            or self.completed_candle_close_ts_ms > self.decision_ts_ms
            or self.interval_seconds <= 0
            or self.close <= 0
            or self.atr <= 0
        ):
            raise AlphaEvaluationError("완성봉 연구 snapshot의 필수 값이 잘못됐습니다.")


@dataclass(frozen=True, slots=True)
class AlphaSignal:
    family_id: str
    symbol: str
    side: Side
    signal_ts_ms: int
    completed_candle_close_ts_ms: int
    reason_codes: tuple[str, ...]


AlphaEvaluator = Callable[[AlphaFeatureSnapshot, Mapping[str, str]], AlphaSignal | None]


ALPHA_PARAMETER_CONTRACTS: dict[str, dict[str, str]] = {
    "F03": {
        "higher_timeframes": "1h,4h",
        "setup_timeframe": "15m",
        "trigger_timeframe": "5m",
        "ema_fast": "20",
        "ema_slow": "50",
        "adx_minimum": "20",
        "relative_volume_minimum": "1.1",
        "pullback_band_atr": "0.5",
        "closed_candle_only": "True",
    },
    "F04": {
        "timeframe": "5m",
        "lookback": "20",
        "close_confirmation": "True",
        "maximum_chase_atr": "0.5",
        "relative_volume_minimum": "1.2",
    },
    "F05": {
        "timeframe": "1h",
        "lookback": "55",
        "close_confirmation": "True",
        "adx_minimum": "25",
        "initial_stop_atr": "2.0",
    },
    "F06": {
        "timeframe": "5m",
        "breakout_lookback": "20",
        "retest_tolerance_atr": "0.35",
        "relative_volume_minimum": "1.2",
        "ofi_alignment_required": "True",
        "maximum_retest_bars": "6",
    },
    "F07": {
        "timeframe": "15m",
        "atr_period": "10",
        "supertrend_multiplier": "3.0",
        "adx_minimum": "25",
        "ema_slope_period": "50",
        "close_confirmation": "True",
    },
    "F08": {
        "trend_timeframe": "1h",
        "trigger_timeframe": "5m",
        "ema_fast": "20",
        "ema_slow": "50",
        "vwap_pullback_atr": "0.35",
        "ofi_alignment_required": "True",
    },
    "F09": {
        "timeframe": "5m",
        "anchor": "UTC_00_SESSION_OPEN_OR_CONFIRMED_DONCHIAN20_BREAKOUT",
        "confirmation_bars": "2",
        "relative_volume_minimum": "1.2",
        "deterministic_anchor": "True",
    },
    "F10": {
        "timeframe": "5m",
        "bollinger_period": "20",
        "standard_deviations": "2.0",
        "bandwidth_percentile": "20",
        "percentile_lookback": "240",
        "relative_volume_minimum": "1.3",
    },
    "F11": {
        "timeframe": "5m",
        "bollinger_period": "20",
        "bollinger_std": "2.0",
        "keltner_period": "20",
        "keltner_atr": "1.5",
        "minimum_compression_bars": "3",
        "relative_volume_minimum": "1.2",
    },
    "F12": {
        "timeframe": "5m",
        "fast_realized_vol_bars": "12",
        "slow_realized_vol_bars": "72",
        "expansion_ratio": "1.5",
        "close_location_minimum": "0.75",
        "maximum_impulse_atr": "1.5",
    },
    "F13": {
        "timeframe": "5m",
        "relative_volume_minimum": "1.5",
        "trade_count_z_minimum": "1.0",
        "long_taker_ratio_minimum": "0.6",
        "short_taker_ratio_maximum": "0.4",
        "spread_percentile_maximum": "75",
    },
    "F14": {
        "sessions_utc": "00:00,08:00,13:30",
        "opening_range_minutes": "15",
        "trigger_timeframe": "5m",
        "maximum_chase_atr": "0.5",
        "relative_volume_minimum": "1.2",
    },
    "F15": {
        "timeframe": "6h",
        "momentum_lookback_bars": "4",
        "absolute_return_minimum": "0.02",
        "ema_fast": "20",
        "ema_slow": "50",
        "momentum_volatility_ratio_minimum": "1.0",
        "closed_candle_only": "True",
    },
    "F16": {
        "rebalance_timeframe": "6h",
        "momentum_lookback_hours": "24",
        "long_quantile": "0.8",
        "short_quantile": "0.2",
        "minimum_universe_size": "20",
        "point_in_time_universe": "True",
    },
    "F17": {
        "depth_levels": "5",
        "long_imbalance_minimum": "0.65",
        "short_imbalance_maximum": "0.35",
        "microprice_spread_fraction": "0.15",
        "persistence_ms": "500",
        "sequence_valid_required": "True",
    },
    "F18": {
        "depth_levels": "10",
        "robust_z_minimum": "2.0",
        "normalization": "MEAN_BID_ASK_NOTIONAL_DEPTH",
        "persistence_ms": "500",
        "price_response_required": "True",
    },
    "F19": {
        "signed_notional_z_minimum": "2.0",
        "trade_intensity_z_minimum": "1.5",
        "opposing_depth_depletion_minimum": "0.2",
        "spread_percentile_maximum": "75",
        "persistence_ms": "500",
    },
    "F20": {
        "regime": "RANGE",
        "vwap_deviation_z_minimum": "2.0",
        "price_progress_efficiency_maximum": "0.25",
        "refill_minimum": "0.2",
        "ofi_reversal_required": "True",
        "microprice_reentry_required": "True",
    },
}


def _validate_parameter_contract(family_id: str, parameters: Mapping[str, str]) -> None:
    expected = ALPHA_PARAMETER_CONTRACTS[family_id]
    actual = dict(parameters)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        changed = sorted(key for key in set(expected) & set(actual) if expected[key] != actual[key])
        raise AlphaEvaluationError(
            "사전등록 alpha parameter 계약과 실행값이 다릅니다: "
            f"family={family_id}, missing={missing}, unexpected={unexpected}, changed={changed}"
        )


def _number(parameters: Mapping[str, str], key: str) -> float:
    try:
        return float(parameters[key])
    except (KeyError, TypeError, ValueError) as error:
        raise AlphaEvaluationError(f"숫자 parameter가 없거나 잘못됐습니다: {key}") from error


def _integer(parameters: Mapping[str, str], key: str) -> int:
    value = _number(parameters, key)
    if not value.is_integer():
        raise AlphaEvaluationError(f"정수 parameter가 아닙니다: {key}")
    return int(value)


def _signal(
    family_id: str,
    snapshot: AlphaFeatureSnapshot,
    side: Side,
    *reason_codes: str,
) -> AlphaSignal:
    return AlphaSignal(
        family_id=family_id,
        symbol=snapshot.symbol,
        side=side,
        signal_ts_ms=snapshot.decision_ts_ms,
        completed_candle_close_ts_ms=snapshot.completed_candle_close_ts_ms,
        reason_codes=tuple(reason_codes),
    )


def _trend_side(snapshot: AlphaFeatureSnapshot) -> Side | None:
    if (
        snapshot.higher_1h_trend is TrendDirection.UP
        and snapshot.higher_4h_trend is TrendDirection.UP
        and snapshot.ema20 > snapshot.ema50
    ):
        return Side.LONG
    if (
        snapshot.higher_1h_trend is TrendDirection.DOWN
        and snapshot.higher_4h_trend is TrendDirection.DOWN
        and snapshot.ema20 < snapshot.ema50
    ):
        return Side.SHORT
    return None


def _breakout_side(snapshot: AlphaFeatureSnapshot, *, lookback: int) -> Side | None:
    high = snapshot.prior_donchian20_high if lookback == 20 else snapshot.prior_donchian55_high
    low = snapshot.prior_donchian20_low if lookback == 20 else snapshot.prior_donchian55_low
    if snapshot.close > high:
        return Side.LONG
    if snapshot.close < low:
        return Side.SHORT
    return None


def _chase_atr(snapshot: AlphaFeatureSnapshot, side: Side, *, lookback: int = 20) -> float:
    boundary = (
        snapshot.prior_donchian20_high
        if lookback == 20 and side is Side.LONG
        else snapshot.prior_donchian20_low
        if lookback == 20
        else snapshot.prior_donchian55_high
        if side is Side.LONG
        else snapshot.prior_donchian55_low
    )
    return abs(snapshot.close - boundary) / snapshot.atr


def _f03(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    side = _trend_side(snapshot)
    if side is None:
        return None
    direction = 1 if side is Side.LONG else -1
    if (
        snapshot.adx < _number(parameters, "adx_minimum")
        or snapshot.relative_volume < _number(parameters, "relative_volume_minimum")
        or snapshot.setup_pullback_distance_atr is None
        or snapshot.setup_pullback_distance_atr > _number(parameters, "pullback_band_atr")
        or (side is Side.LONG and snapshot.setup_15m_trend is not TrendDirection.UP)
        or (side is Side.SHORT and snapshot.setup_15m_trend is not TrendDirection.DOWN)
        or (snapshot.close - snapshot.previous_close) * direction <= 0
    ):
        return None
    return _signal("F03", snapshot, side, "HTF_TREND", "EMA_PULLBACK", "CLOSE_REACCELERATION")


def _f04(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    side = _breakout_side(snapshot, lookback=_integer(parameters, "lookback"))
    if (
        side is None
        or snapshot.relative_volume < _number(parameters, "relative_volume_minimum")
        or _chase_atr(snapshot, side) > _number(parameters, "maximum_chase_atr")
    ):
        return None
    return _signal("F04", snapshot, side, "DONCHIAN20_BREAK", "CLOSE_CONFIRMED", "CHASE_OK")


def _f05(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    side = _breakout_side(snapshot, lookback=_integer(parameters, "lookback"))
    if side is None or snapshot.adx < _number(parameters, "adx_minimum"):
        return None
    return _signal("F05", snapshot, side, "DONCHIAN55_BREAK", "ADX_CONFIRMED")


def _f06(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    if (
        snapshot.breakout_side is None
        or snapshot.bars_since_breakout is None
        or snapshot.retest_distance_atr is None
        or snapshot.bars_since_breakout > _integer(parameters, "maximum_retest_bars")
        or snapshot.retest_distance_atr > _number(parameters, "retest_tolerance_atr")
        or snapshot.relative_volume < _number(parameters, "relative_volume_minimum")
        or not snapshot.structure_reclaimed
        or not snapshot.ofi_aligned
    ):
        return None
    return _signal(
        "F06", snapshot, snapshot.breakout_side, "BREAKOUT", "RETEST", "STRUCTURE_AND_OFI"
    )


def _f07(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    side = snapshot.supertrend_side
    if side is None:
        return None
    direction = 1 if side is Side.LONG else -1
    if (
        snapshot.adx < _number(parameters, "adx_minimum")
        or snapshot.ema_slope * direction <= 0
        or not snapshot.cost_viability_passed
    ):
        return None
    return _signal("F07", snapshot, side, "SUPERTREND", "ADX_AND_SLOPE", "COST_GATE")


def _f08(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    side = (
        Side.LONG
        if snapshot.higher_1h_trend is TrendDirection.UP
        else Side.SHORT
        if snapshot.higher_1h_trend is TrendDirection.DOWN
        else None
    )
    if side is None:
        return None
    direction = 1 if side is Side.LONG else -1
    if (
        abs(snapshot.previous_close - snapshot.session_vwap)
        > snapshot.atr * _number(parameters, "vwap_pullback_atr")
        or (snapshot.close - snapshot.session_vwap) * direction <= 0
        or not snapshot.ofi_aligned
    ):
        return None
    return _signal("F08", snapshot, side, "HTF_TREND", "VWAP_PULLBACK", "OFI_REACCELERATION")


def _f09(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    side = snapshot.anchored_vwap_confirmation_side
    if (
        side is None
        or snapshot.anchored_vwap_confirmation_bars < _integer(parameters, "confirmation_bars")
        or snapshot.relative_volume < _number(parameters, "relative_volume_minimum")
    ):
        return None
    return _signal(
        "F09",
        snapshot,
        side,
        "DETERMINISTIC_ANCHOR",
        "AVWAP_RECLAIM_CONFIRMED",
        "RVOL",
    )


def _f10(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    if snapshot.bandwidth_percentile > _number(
        parameters, "bandwidth_percentile"
    ) or snapshot.relative_volume < _number(parameters, "relative_volume_minimum"):
        return None
    trend = _trend_side(snapshot)
    if trend is Side.LONG and snapshot.close > snapshot.bollinger_upper:
        return _signal("F10", snapshot, Side.LONG, "BANDWIDTH_SQUEEZE", "UP_BREAK", "RVOL")
    if trend is Side.SHORT and snapshot.close < snapshot.bollinger_lower:
        return _signal("F10", snapshot, Side.SHORT, "BANDWIDTH_SQUEEZE", "DOWN_BREAK", "RVOL")
    return None


def _f11(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    if snapshot.compression_bars < _integer(
        parameters, "minimum_compression_bars"
    ) or snapshot.relative_volume < _number(parameters, "relative_volume_minimum"):
        return None
    side = _breakout_side(snapshot, lookback=20)
    if side is None:
        return None
    return _signal("F11", snapshot, side, "BOLLINGER_INSIDE_KELTNER", "DONCHIAN_BREAK", "RVOL")


def _f12(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    slow = snapshot.realized_volatility_slow
    if slow <= 0 or snapshot.realized_volatility_fast / slow < _number(
        parameters, "expansion_ratio"
    ):
        return None
    impulse_atr = abs(snapshot.close - snapshot.previous_close) / snapshot.atr
    if impulse_atr > _number(parameters, "maximum_impulse_atr"):
        return None
    minimum = _number(parameters, "close_location_minimum")
    if snapshot.close_location >= minimum:
        return _signal("F12", snapshot, Side.LONG, "VOL_EXPANSION", "CLOSE_LOCATION_LONG")
    if snapshot.close_location <= 1 - minimum:
        return _signal("F12", snapshot, Side.SHORT, "VOL_EXPANSION", "CLOSE_LOCATION_SHORT")
    return None


def _f13(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    side = _breakout_side(snapshot, lookback=20)
    if (
        side is None
        or snapshot.relative_volume < _number(parameters, "relative_volume_minimum")
        or snapshot.trade_count_z < _number(parameters, "trade_count_z_minimum")
        or snapshot.spread_percentile > _number(parameters, "spread_percentile_maximum")
    ):
        return None
    if side is Side.LONG and snapshot.taker_ratio < _number(parameters, "long_taker_ratio_minimum"):
        return None
    if side is Side.SHORT and snapshot.taker_ratio > _number(
        parameters, "short_taker_ratio_maximum"
    ):
        return None
    return _signal("F13", snapshot, side, "RVOL_BREAK", "TRADE_COUNT", "TAKER_FLOW")


def _f14(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    if (
        not snapshot.opening_range_complete
        or snapshot.opening_range_high is None
        or snapshot.opening_range_low is None
        or snapshot.relative_volume < _number(parameters, "relative_volume_minimum")
    ):
        return None
    if snapshot.close > snapshot.opening_range_high:
        side = Side.LONG
        boundary = snapshot.opening_range_high
    elif snapshot.close < snapshot.opening_range_low:
        side = Side.SHORT
        boundary = snapshot.opening_range_low
    else:
        return None
    if abs(snapshot.close - boundary) / snapshot.atr > _number(parameters, "maximum_chase_atr"):
        return None
    return _signal("F14", snapshot, side, "OPENING_RANGE_COMPLETE", "RANGE_BREAK", "CHASE_OK")


def _f15(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    minimum = _number(parameters, "absolute_return_minimum")
    if snapshot.momentum_volatility_ratio < _number(
        parameters, "momentum_volatility_ratio_minimum"
    ):
        return None
    if snapshot.momentum_24h >= minimum and snapshot.ema20 > snapshot.ema50:
        return _signal("F15", snapshot, Side.LONG, "TWENTY_FOUR_HOUR_MOMENTUM", "EMA_TREND")
    if snapshot.momentum_24h <= -minimum and snapshot.ema20 < snapshot.ema50:
        return _signal("F15", snapshot, Side.SHORT, "TWENTY_FOUR_HOUR_MOMENTUM", "EMA_TREND")
    return None


def _f16(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    rank = snapshot.cross_sectional_rank
    if (
        rank is None
        or snapshot.point_in_time_universe_size < _integer(parameters, "minimum_universe_size")
        or not snapshot.liquidity_floor_passed
    ):
        return None
    if rank >= _number(parameters, "long_quantile"):
        return _signal("F16", snapshot, Side.LONG, "POINT_IN_TIME_UNIVERSE", "TOP_QUANTILE")
    if rank <= _number(parameters, "short_quantile"):
        return _signal("F16", snapshot, Side.SHORT, "POINT_IN_TIME_UNIVERSE", "BOTTOM_QUANTILE")
    return None


def _microstructure_healthy(snapshot: AlphaFeatureSnapshot) -> bool:
    return snapshot.sequence_valid and not snapshot.data_stale and snapshot.cost_viability_passed


def _f17(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    if not _microstructure_healthy(snapshot) or snapshot.microstructure_persistence_ms < _integer(
        parameters, "persistence_ms"
    ):
        return None
    displacement = _number(parameters, "microprice_spread_fraction")
    if (
        snapshot.queue_imbalance_top5 >= _number(parameters, "long_imbalance_minimum")
        and snapshot.microprice_spread_fraction >= displacement
    ):
        return _signal("F17", snapshot, Side.LONG, "QUEUE_IMBALANCE", "MICROPRICE", "PERSISTENT")
    if (
        snapshot.queue_imbalance_top5 <= _number(parameters, "short_imbalance_maximum")
        and snapshot.microprice_spread_fraction <= -displacement
    ):
        return _signal("F17", snapshot, Side.SHORT, "QUEUE_IMBALANCE", "MICROPRICE", "PERSISTENT")
    return None


def _f18(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    minimum = _number(parameters, "robust_z_minimum")
    if (
        not _microstructure_healthy(snapshot)
        or snapshot.microstructure_persistence_ms < _integer(parameters, "persistence_ms")
        or not snapshot.price_response_aligned
    ):
        return None
    if snapshot.mlofi_robust_z >= minimum:
        return _signal("F18", snapshot, Side.LONG, "MLOFI_Z", "PRICE_RESPONSE", "PERSISTENT")
    if snapshot.mlofi_robust_z <= -minimum:
        return _signal("F18", snapshot, Side.SHORT, "MLOFI_Z", "PRICE_RESPONSE", "PERSISTENT")
    return None


def _f19(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    if (
        not _microstructure_healthy(snapshot)
        or snapshot.trade_intensity_z < _number(parameters, "trade_intensity_z_minimum")
        or snapshot.opposing_depth_depletion
        < _number(parameters, "opposing_depth_depletion_minimum")
        or snapshot.spread_percentile > _number(parameters, "spread_percentile_maximum")
        or snapshot.microstructure_persistence_ms < _integer(parameters, "persistence_ms")
    ):
        return None
    minimum = _number(parameters, "signed_notional_z_minimum")
    if snapshot.signed_notional_z >= minimum:
        return _signal(
            "F19",
            snapshot,
            Side.LONG,
            "SIGNED_NOTIONAL",
            "INTENSITY",
            "DEPTH_DEPLETION",
        )
    if snapshot.signed_notional_z <= -minimum:
        return _signal(
            "F19",
            snapshot,
            Side.SHORT,
            "SIGNED_NOTIONAL",
            "INTENSITY",
            "DEPTH_DEPLETION",
        )
    return None


def _f20(snapshot: AlphaFeatureSnapshot, parameters: Mapping[str, str]) -> AlphaSignal | None:
    if (
        snapshot.regime != parameters.get("regime")
        or abs(snapshot.vwap_deviation_z) < _number(parameters, "vwap_deviation_z_minimum")
        or snapshot.price_progress_efficiency
        > _number(parameters, "price_progress_efficiency_maximum")
        or not snapshot.ofi_reversal_confirmed
        or not snapshot.microprice_reentry_confirmed
        or not _microstructure_healthy(snapshot)
    ):
        return None
    side = Side.SHORT if snapshot.vwap_deviation_z > 0 else Side.LONG
    supporting_refill = (
        snapshot.bid_refill_ratio if side is Side.LONG else snapshot.ask_refill_ratio
    )
    if supporting_refill < _number(parameters, "refill_minimum"):
        return None
    return _signal("F20", snapshot, side, "VWAP_EXTREME", "ABSORPTION", "FLOW_REVERSAL")


ALPHA_EVALUATORS: dict[str, AlphaEvaluator] = {
    "F03": _f03,
    "F04": _f04,
    "F05": _f05,
    "F06": _f06,
    "F07": _f07,
    "F08": _f08,
    "F09": _f09,
    "F10": _f10,
    "F11": _f11,
    "F12": _f12,
    "F13": _f13,
    "F14": _f14,
    "F15": _f15,
    "F16": _f16,
    "F17": _f17,
    "F18": _f18,
    "F19": _f19,
    "F20": _f20,
}

ALPHA_EVALUATION_INTERVAL_SECONDS: dict[str, int] = {
    "F03": 300,
    "F04": 300,
    "F05": 3_600,
    "F06": 300,
    "F07": 900,
    "F08": 300,
    "F09": 300,
    "F10": 300,
    "F11": 300,
    "F12": 300,
    "F13": 300,
    "F14": 300,
    "F15": 21_600,
    "F16": 21_600,
    "F17": 1,
    "F18": 1,
    "F19": 1,
    "F20": 1,
}


def evaluate_alpha(
    family_id: str,
    snapshot: AlphaFeatureSnapshot,
    parameters: Mapping[str, str],
) -> AlphaSignal | None:
    """등록된 evaluator만 실행하고 SIHO 미확정 family는 fail closed한다."""

    try:
        evaluator = ALPHA_EVALUATORS[family_id]
    except KeyError as error:
        raise AlphaEvaluationError(
            f"실행 가능한 alpha evaluator가 없습니다: {family_id}"
        ) from error
    expected_interval = ALPHA_EVALUATION_INTERVAL_SECONDS[family_id]
    if snapshot.interval_seconds != expected_interval:
        return None
    _validate_parameter_contract(family_id, parameters)
    return evaluator(snapshot, parameters)
