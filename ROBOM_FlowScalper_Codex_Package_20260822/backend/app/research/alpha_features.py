# 완료된 Candle과 과거 미세구조 snapshot만으로 F03~F20 공통 연구피처를 만든다.

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean, pstdev

from backend.app.domain.models import Side
from backend.app.features import FeatureSnapshot
from backend.app.market_data import Candle
from backend.app.research.alpha_evaluators import (
    ALPHA_EVALUATION_INTERVAL_SECONDS,
    AlphaFeatureSnapshot,
    TrendDirection,
)
from backend.app.strategies.statistics import robust_z, rolling_percentile

BASE_ROUND_TRIP_COST_BPS = 13.0
MINIMUM_POINT_IN_TIME_LIQUIDITY_24H_USDT = 20_000_000.0
MINIMUM_BARS_BY_FAMILY = {
    "F03": 51,
    "F04": 21,
    "F05": 56,
    "F06": 28,
    "F07": 51,
    "F08": 51,
    "F09": 21,
    "F10": 260,
    "F11": 24,
    "F12": 73,
    "F13": 21,
    "F14": 21,
    "F15": 73,
    "F16": 5,
    "F17": 20,
    "F18": 20,
    "F19": 20,
    "F20": 20,
}


class AlphaFeatureError(ValueError):
    """시간순·완성봉·동일 종목 피처 계약이 깨졌을 때 발생한다."""


@dataclass(frozen=True, slots=True)
class AlphaFeatureDiagnostics:
    accepted_candles: int
    duplicate_candles: int
    out_of_order_candles: int
    candle_gaps: int
    accepted_microstructure: int
    duplicate_microstructure: int
    out_of_order_microstructure: int


@dataclass(frozen=True, slots=True)
class MicroFeatureValues:
    spread_bps: float
    spread_percentile: float
    sequence_valid: bool
    data_stale: bool
    queue_imbalance_top5: float
    microprice_spread_fraction: float
    persistence_ms: int
    cost_viability_passed: bool
    mlofi_robust_z: float
    price_response_aligned: bool
    signed_notional_z: float
    trade_intensity_z: float
    opposing_depth_depletion: float
    price_progress_efficiency: float
    refill_ratio: float
    bid_refill_ratio: float
    ask_refill_ratio: float
    ofi_aligned: bool
    ofi_reversal_confirmed: bool
    microprice_reentry_confirmed: bool


def _ema(values: Sequence[float], period: int) -> float:
    if not values:
        raise AlphaFeatureError("EMA 입력이 비어 있습니다.")
    selected = values[-max(period * 4, period) :]
    result = selected[0]
    alpha = 2 / (period + 1)
    for value in selected[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _true_ranges(bars: Sequence[Candle]) -> list[float]:
    if not bars:
        return []
    previous_close = float(bars[0].close)
    values: list[float] = []
    for bar in bars:
        high = float(bar.high)
        low = float(bar.low)
        values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = float(bar.close)
    return values


def _atr(bars: Sequence[Candle], period: int = 14) -> float:
    ranges = _true_ranges(bars)
    return fmean(ranges[-period:]) if ranges else 0.0


def _rsi(values: Sequence[float], period: int = 14) -> float:
    selected = values[-(period + 1) :]
    if len(selected) < 2:
        return 50.0
    changes = [
        current - previous for previous, current in zip(selected, selected[1:], strict=False)
    ]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = fmean(gains)
    average_loss = fmean(losses)
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100 - 100 / (1 + average_gain / average_loss)


def _adx(bars: Sequence[Candle], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    true_ranges: list[float] = []
    previous = bars[0]
    for current in bars[1:]:
        up_move = float(current.high - previous.high)
        down_move = float(previous.low - current.low)
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(
            max(
                float(current.high - current.low),
                abs(float(current.high - previous.close)),
                abs(float(current.low - previous.close)),
            )
        )
        previous = current
    true_range = sum(true_ranges[-period:])
    if true_range <= 0:
        return 0.0
    plus_di = 100 * sum(plus_dm[-period:]) / true_range
    minus_di = 100 * sum(minus_dm[-period:]) / true_range
    total = plus_di + minus_di
    return 100 * abs(plus_di - minus_di) / total if total else 0.0


def _volatility(closes: Sequence[float], bars: int) -> float:
    selected = closes[-(bars + 1) :]
    returns = [
        math.log(current / previous)
        for previous, current in zip(selected, selected[1:], strict=False)
        if previous > 0 and current > 0
    ]
    return pstdev(returns) if len(returns) >= 2 else 0.0


def _trend(bars: Sequence[Candle]) -> TrendDirection:
    if len(bars) < 50:
        return TrendDirection.UNAVAILABLE
    closes = [float(bar.close) for bar in bars]
    fast = _ema(closes, 20)
    slow = _ema(closes, 50)
    if fast > slow:
        return TrendDirection.UP
    if fast < slow:
        return TrendDirection.DOWN
    return TrendDirection.FLAT


def _session_start_ms(timestamp_ms: int) -> int:
    day_start = timestamp_ms - timestamp_ms % 86_400_000
    time_of_day = timestamp_ms - day_start
    candidates = (0, 8 * 3_600_000, 13 * 3_600_000 + 30 * 60_000)
    eligible = [offset for offset in candidates if offset <= time_of_day]
    return day_start + (eligible[-1] if eligible else 0)


def _vwap(bars: Sequence[Candle]) -> float:
    volume = sum(float(bar.volume) for bar in bars)
    if volume <= 0:
        return float(bars[-1].close)
    notional = sum(
        (
            float(bar.quote_volume)
            if bar.quote_volume > 0
            else (float(bar.high + bar.low + bar.close) / 3) * float(bar.volume)
        )
        for bar in bars
    )
    return notional / volume


def _bollinger(values: Sequence[float]) -> tuple[float, float, float]:
    selected = values[-20:]
    middle = fmean(selected)
    sigma = pstdev(selected) if len(selected) >= 2 else 0.0
    return middle, middle + 2 * sigma, middle - 2 * sigma


def _bandwidth_percentile(bars: Sequence[Candle]) -> float:
    closes = [float(bar.close) for bar in bars]
    bandwidths: list[float] = []
    for end in range(20, len(closes) + 1):
        middle, upper, lower = _bollinger(closes[:end])
        bandwidths.append((upper - lower) / middle if middle > 0 else 0.0)
    if len(bandwidths) < 2:
        return 50.0
    current = bandwidths[-2]
    history = bandwidths[:-2][-240:]
    return rolling_percentile(history, current) * 100


def _compression_bars(bars: Sequence[Candle]) -> int:
    count = 0
    for end in range(len(bars) - 1, 19, -1):
        selected = bars[:end]
        closes = [float(bar.close) for bar in selected]
        _, bollinger_upper, bollinger_lower = _bollinger(closes)
        keltner_mid = _ema(closes, 20)
        atr = _atr(selected)
        if (
            bollinger_upper <= keltner_mid + 1.5 * atr
            and bollinger_lower >= keltner_mid - 1.5 * atr
        ):
            count += 1
        else:
            break
    return count


def _supertrend_side(bars: Sequence[Candle], *, period: int = 10, multiplier: float = 3) -> Side:
    """완료봉만 사용한 Wilder ATR 기반 recursive Supertrend 방향을 계산한다."""

    if len(bars) < period + 1:
        raise AlphaFeatureError("Supertrend warmup 완료봉이 부족합니다.")
    true_ranges = _true_ranges(bars)
    atr_values: list[float | None] = [None] * len(bars)
    atr_values[period - 1] = fmean(true_ranges[:period])
    for index in range(period, len(bars)):
        previous_atr = atr_values[index - 1]
        if previous_atr is None:
            raise AlphaFeatureError("Supertrend ATR 재귀상태가 비어 있습니다.")
        atr_values[index] = (previous_atr * (period - 1) + true_ranges[index]) / period

    final_upper = 0.0
    final_lower = 0.0
    supertrend = 0.0
    previous_final_upper = 0.0
    previous_final_lower = 0.0
    previous_supertrend = 0.0
    for index in range(period - 1, len(bars)):
        atr = atr_values[index]
        if atr is None:
            continue
        bar = bars[index]
        midpoint = (float(bar.high) + float(bar.low)) / 2
        basic_upper = midpoint + multiplier * atr
        basic_lower = midpoint - multiplier * atr
        if index == period - 1:
            final_upper = basic_upper
            final_lower = basic_lower
            supertrend = final_lower if float(bar.close) >= midpoint else final_upper
        else:
            previous_close = float(bars[index - 1].close)
            final_upper = (
                basic_upper
                if basic_upper < previous_final_upper or previous_close > previous_final_upper
                else previous_final_upper
            )
            final_lower = (
                basic_lower
                if basic_lower > previous_final_lower or previous_close < previous_final_lower
                else previous_final_lower
            )
            close = float(bar.close)
            if previous_supertrend == previous_final_upper:
                supertrend = final_upper if close <= final_upper else final_lower
            else:
                supertrend = final_lower if close >= final_lower else final_upper
        previous_final_upper = final_upper
        previous_final_lower = final_lower
        previous_supertrend = supertrend
    return Side.LONG if supertrend == final_lower else Side.SHORT


def _setup_pullback(
    bars: Sequence[Candle],
) -> tuple[TrendDirection, float | None]:
    if len(bars) < 51:
        return TrendDirection.UNAVAILABLE, None
    closes = [float(bar.close) for bar in bars]
    atr = _atr(bars)
    if atr <= 0:
        return TrendDirection.UNAVAILABLE, None
    fast = _ema(closes, 20)
    slow = _ema(closes, 50)
    trend = (
        TrendDirection.UP
        if fast > slow
        else TrendDirection.DOWN
        if fast < slow
        else TrendDirection.FLAT
    )
    return trend, abs(closes[-1] - fast) / atr


def _anchored_vwap_confirmation(
    session_bars: Sequence[Candle],
) -> tuple[Side | None, int]:
    if len(session_bars) < 3:
        return None, 0
    signs: list[int] = []
    for end in range(1, len(session_bars) + 1):
        close = float(session_bars[end - 1].close)
        anchored_vwap = _vwap(session_bars[:end])
        signs.append(1 if close > anchored_vwap else -1 if close < anchored_vwap else 0)
    current = signs[-1]
    if current == 0:
        return None, 0
    count = 0
    for sign in reversed(signs):
        if sign != current:
            break
        count += 1
    crossing_index = len(signs) - count
    if crossing_index == 0 or signs[crossing_index - 1] == current:
        return None, 0
    return (Side.LONG if current > 0 else Side.SHORT), count


def _anchored_vwap_bars(bars: Sequence[Candle]) -> tuple[Candle, ...]:
    """UTC 00시 또는 과거에 확정된 Donchian20 돌파 중 최신 anchor만 사용한다."""

    if not bars:
        return ()
    current = bars[-1]
    utc_day_start = current.open_ts_ms - current.open_ts_ms % 86_400_000
    anchor_ts_ms = utc_day_start
    for index in range(20, len(bars) - 1):
        prior = bars[index - 20 : index]
        candidate = bars[index]
        close = float(candidate.close)
        current_breakout = close > max(float(bar.high) for bar in prior) or close < min(
            float(bar.low) for bar in prior
        )
        previous_breakout = False
        if index > 20:
            previous = bars[index - 1]
            previous_prior = bars[index - 21 : index - 1]
            previous_close = float(previous.close)
            previous_breakout = previous_close > max(
                float(bar.high) for bar in previous_prior
            ) or previous_close < min(float(bar.low) for bar in previous_prior)
        if current_breakout and not previous_breakout:
            anchor_ts_ms = max(
                anchor_ts_ms,
                candidate.open_ts_ms + candidate.interval_seconds * 1_000,
            )
    return tuple(bar for bar in bars if bar.open_ts_ms >= anchor_ts_ms)


def _breakout_retest(
    bars: Sequence[Candle],
    atr: float,
) -> tuple[Side | None, int | None, float | None, bool]:
    maximum_retest_bars = min(6, len(bars) - 21)
    current_close = float(bars[-1].close)
    for age in range(1, maximum_retest_bars + 1):
        breakout_index = len(bars) - 1 - age
        prior = bars[breakout_index - 20 : breakout_index]
        breakout = bars[breakout_index]
        high = max(float(bar.high) for bar in prior)
        low = min(float(bar.low) for bar in prior)
        breakout_close = float(breakout.close)
        if breakout_close > high:
            return Side.LONG, age, abs(current_close - high) / atr, current_close > high
        if breakout_close < low:
            return Side.SHORT, age, abs(current_close - low) / atr, current_close < low
    return None, None, None, False


class AlphaFeatureBuilder:
    """한 Run 안의 완료봉과 과거 미세구조를 시간순으로만 보존한다."""

    def __init__(self, *, maximum_bars: int = 512, maximum_microstructure: int = 4_096) -> None:
        if maximum_bars < 260 or maximum_microstructure < 20:
            raise ValueError("100후보 피처 보관량이 최소 연구창보다 작습니다.")
        self._bars: dict[tuple[str, int], deque[Candle]] = defaultdict(
            lambda: deque(maxlen=maximum_bars)
        )
        self._micro: dict[str, deque[FeatureSnapshot]] = defaultdict(
            lambda: deque(maxlen=maximum_microstructure)
        )
        self._accepted_candles = 0
        self._duplicate_candles = 0
        self._out_of_order_candles = 0
        self._candle_gaps = 0
        self._accepted_microstructure = 0
        self._duplicate_microstructure = 0
        self._out_of_order_microstructure = 0

    def ingest_completed(self, candle: Candle) -> bool:
        close_ts_ms = candle.open_ts_ms + candle.interval_seconds * 1_000
        key = (candle.symbol, candle.interval_seconds)
        history = self._bars[key]
        if history:
            previous = history[-1]
            if candle.open_ts_ms == previous.open_ts_ms:
                self._duplicate_candles += 1
                return False
            if candle.open_ts_ms < previous.open_ts_ms:
                self._out_of_order_candles += 1
                return False
            expected = previous.open_ts_ms + candle.interval_seconds * 1_000
            if candle.open_ts_ms != expected:
                self._candle_gaps += 1
                history.clear()
        if close_ts_ms <= candle.open_ts_ms or candle.close <= 0 or candle.volume < 0:
            raise AlphaFeatureError("완료봉 값이 올바르지 않습니다.")
        history.append(candle)
        self._accepted_candles += 1
        return True

    def ingest_microstructure(self, snapshot: FeatureSnapshot) -> bool:
        history = self._micro[snapshot.symbol]
        if history:
            if snapshot.ts_ms == history[-1].ts_ms:
                self._duplicate_microstructure += 1
                return False
            if snapshot.ts_ms < history[-1].ts_ms:
                self._out_of_order_microstructure += 1
                return False
        snapshot.assert_finite()
        history.append(snapshot)
        self._accepted_microstructure += 1
        return True

    def snapshot(
        self,
        symbol: str,
        family_id: str,
        *,
        decision_ts_ms: int,
    ) -> AlphaFeatureSnapshot | None:
        try:
            interval = ALPHA_EVALUATION_INTERVAL_SECONDS[family_id]
            minimum_bars = MINIMUM_BARS_BY_FAMILY[family_id]
        except KeyError as error:
            raise AlphaFeatureError(f"알 수 없는 alpha family입니다: {family_id}") from error
        bars = tuple(self._bars.get((symbol, interval), ()))
        bars = tuple(
            bar for bar in bars if bar.open_ts_ms + bar.interval_seconds * 1_000 <= decision_ts_ms
        )
        if len(bars) < minimum_bars:
            return None
        completed_close_ts_ms = bars[-1].open_ts_ms + interval * 1_000
        completed_age_ms = decision_ts_ms - completed_close_ts_ms
        if completed_age_ms < 0 or completed_age_ms >= interval * 1_000:
            return None
        closes = [float(bar.close) for bar in bars]
        prior20 = bars[-21:-1]
        prior55 = bars[-56:-1] if len(bars) >= 56 else bars[:-1]
        atr = _atr(bars)
        if atr <= 0 or not prior20:
            return None
        previous_closes = closes[:-1]
        bollinger_mid, bollinger_upper, bollinger_lower = _bollinger(previous_closes)
        keltner_mid = _ema(previous_closes, 20)
        current = bars[-1]
        completed_structure = bars[-3:]
        prior_volumes = [float(bar.volume) for bar in prior20]
        prior_trade_counts = [float(bar.trade_count) for bar in prior20]
        current_volume = float(current.volume)
        current_trade_count = float(current.trade_count)
        volume_mean = fmean(prior_volumes) if prior_volumes else 0.0
        trade_sigma = pstdev(prior_trade_counts) if len(prior_trade_counts) >= 2 else 0.0
        trade_count_z = (
            (current_trade_count - fmean(prior_trade_counts)) / trade_sigma
            if trade_sigma > 0
            else 0.0
        )
        taker_total = current.taker_buy_volume + current.taker_sell_volume
        taker_ratio = float(current.taker_buy_volume / taker_total) if taker_total else 0.5
        high_low = float(current.high - current.low)
        close_location = float(current.close - current.low) / high_low if high_low > 0 else 0.5
        session_start = _session_start_ms(current.open_ts_ms)
        session_bars = [bar for bar in bars if bar.open_ts_ms >= session_start]
        session_vwap = _vwap(session_bars)
        anchored_bars = _anchored_vwap_bars(bars)
        anchored_vwap = _vwap(anchored_bars)
        previous_anchored_vwap = (
            _vwap(anchored_bars[:-1]) if len(anchored_bars) > 1 else anchored_vwap
        )
        opening_bars = [bar for bar in session_bars if bar.open_ts_ms < session_start + 15 * 60_000]
        opening_complete = completed_close_ts_ms >= session_start + 15 * 60_000 and bool(
            opening_bars
        )
        opening_high = max((float(bar.high) for bar in opening_bars), default=None)
        opening_low = min((float(bar.low) for bar in opening_bars), default=None)
        one_hour = self._completed_as_of(symbol, 3_600, decision_ts_ms)
        four_hour = self._completed_as_of(symbol, 14_400, decision_ts_ms)
        fifteen_minute = self._completed_as_of(symbol, 900, decision_ts_ms)
        setup_15m_trend, setup_pullback_distance = _setup_pullback(fifteen_minute)
        anchored_confirmation_side, anchored_confirmation_bars = _anchored_vwap_confirmation(
            anchored_bars
        )
        breakout_side, breakout_age, retest_distance, structure_reclaimed = _breakout_retest(
            bars, atr
        )
        close_sigma = pstdev(previous_closes[-20:]) if len(previous_closes) >= 2 else 0.0
        vwap_deviation_z = (
            (float(current.close) - session_vwap) / close_sigma if close_sigma > 0 else 0.0
        )
        reference_direction = 1 if float(current.close) >= session_vwap else -1
        micro = self._micro_values(
            symbol,
            decision_ts_ms,
            reference_direction=reference_direction,
        )
        cross_rank, universe_size = self._cross_sectional_rank(
            symbol,
            completed_close_ts_ms,
        )
        liquidity_24h = sum(float(bar.quote_volume) for bar in bars[-4:])
        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        ema50_previous = _ema(previous_closes, 50)
        momentum_6h = closes[-1] / closes[-2] - 1 if len(closes) >= 2 else 0.0
        momentum_24h = closes[-1] / closes[-5] - 1 if len(closes) >= 5 else 0.0
        slow_volatility = _volatility(closes, 72)
        momentum_volatility_ratio = (
            abs(momentum_24h) / (slow_volatility * math.sqrt(4)) if slow_volatility > 0 else 0.0
        )
        return AlphaFeatureSnapshot(
            symbol=symbol,
            decision_ts_ms=decision_ts_ms,
            completed_candle_close_ts_ms=completed_close_ts_ms,
            interval_seconds=interval,
            close=closes[-1],
            previous_close=closes[-2],
            open=float(current.open),
            high=float(current.high),
            low=float(current.low),
            atr=atr,
            ema20=ema20,
            ema50=ema50,
            ema_slope=ema50 - ema50_previous,
            adx=_adx(bars),
            rsi=_rsi(closes),
            relative_volume=current_volume / volume_mean if volume_mean > 0 else 0.0,
            trade_count_z=trade_count_z,
            taker_ratio=taker_ratio,
            close_location=close_location,
            realized_volatility_fast=_volatility(closes, 12),
            realized_volatility_slow=slow_volatility,
            prior_donchian20_high=max(float(bar.high) for bar in prior20),
            prior_donchian20_low=min(float(bar.low) for bar in prior20),
            prior_donchian55_high=max(float(bar.high) for bar in prior55),
            prior_donchian55_low=min(float(bar.low) for bar in prior55),
            session_vwap=session_vwap,
            anchored_vwap=anchored_vwap,
            previous_anchored_vwap=previous_anchored_vwap,
            completed_structure_long_stop=min(float(bar.low) for bar in completed_structure),
            completed_structure_short_stop=max(float(bar.high) for bar in completed_structure),
            bollinger_upper=bollinger_upper,
            bollinger_lower=bollinger_lower,
            bandwidth_percentile=_bandwidth_percentile(bars),
            keltner_upper=keltner_mid + 1.5 * atr,
            keltner_lower=keltner_mid - 1.5 * atr,
            compression_bars=_compression_bars(bars),
            higher_1h_trend=_trend(one_hour),
            higher_4h_trend=_trend(four_hour),
            setup_15m_trend=setup_15m_trend,
            setup_pullback_distance_atr=setup_pullback_distance,
            supertrend_side=(_supertrend_side(bars) if len(bars) >= 11 else None),
            anchored_vwap_confirmation_side=anchored_confirmation_side,
            anchored_vwap_confirmation_bars=anchored_confirmation_bars,
            breakout_side=breakout_side,
            bars_since_breakout=breakout_age,
            retest_distance_atr=retest_distance,
            structure_reclaimed=structure_reclaimed,
            ofi_aligned=micro.ofi_aligned,
            momentum_6h=momentum_6h,
            momentum_24h=momentum_24h,
            momentum_volatility_ratio=momentum_volatility_ratio,
            cross_sectional_rank=cross_rank,
            point_in_time_universe_size=universe_size,
            liquidity_floor_passed=(liquidity_24h >= MINIMUM_POINT_IN_TIME_LIQUIDITY_24H_USDT),
            opening_range_high=opening_high,
            opening_range_low=opening_low,
            opening_range_complete=opening_complete,
            spread_bps=micro.spread_bps,
            spread_percentile=micro.spread_percentile,
            sequence_valid=micro.sequence_valid,
            data_stale=micro.data_stale,
            queue_imbalance_top5=micro.queue_imbalance_top5,
            microprice_spread_fraction=micro.microprice_spread_fraction,
            microstructure_persistence_ms=micro.persistence_ms,
            cost_viability_passed=micro.cost_viability_passed,
            mlofi_robust_z=micro.mlofi_robust_z,
            price_response_aligned=micro.price_response_aligned,
            signed_notional_z=micro.signed_notional_z,
            trade_intensity_z=micro.trade_intensity_z,
            opposing_depth_depletion=micro.opposing_depth_depletion,
            regime=("RANGE" if abs(ema20 - _ema(closes, 50)) / closes[-1] < 0.002 else "TREND"),
            vwap_deviation_z=vwap_deviation_z,
            price_progress_efficiency=micro.price_progress_efficiency,
            refill_ratio=micro.refill_ratio,
            bid_refill_ratio=micro.bid_refill_ratio,
            ask_refill_ratio=micro.ask_refill_ratio,
            ofi_reversal_confirmed=micro.ofi_reversal_confirmed,
            microprice_reentry_confirmed=micro.microprice_reentry_confirmed,
        )

    def _completed_as_of(
        self,
        symbol: str,
        interval_seconds: int,
        decision_ts_ms: int,
    ) -> tuple[Candle, ...]:
        return tuple(
            bar
            for bar in self._bars.get((symbol, interval_seconds), ())
            if bar.open_ts_ms + interval_seconds * 1_000 <= decision_ts_ms
        )

    def _cross_sectional_rank(
        self,
        symbol: str,
        completed_close_ts_ms: int,
    ) -> tuple[float | None, int]:
        values: list[tuple[str, float]] = []
        for candidate_symbol, interval in self._bars:
            if interval != 21_600:
                continue
            bars = self._completed_as_of(candidate_symbol, interval, completed_close_ts_ms)
            if len(bars) < 5:
                continue
            close_ts = bars[-1].open_ts_ms + interval * 1_000
            if close_ts != completed_close_ts_ms:
                continue
            values.append((candidate_symbol, float(bars[-1].close / bars[-5].close - 1)))
        if not values or symbol not in {item[0] for item in values}:
            return None, len(values)
        ordered = sorted(values, key=lambda item: (item[1], item[0]))
        index = next(position for position, item in enumerate(ordered) if item[0] == symbol)
        return (index / (len(ordered) - 1) if len(ordered) > 1 else 0.5), len(ordered)

    def _micro_values(
        self,
        symbol: str,
        decision_ts_ms: int,
        *,
        reference_direction: int,
    ) -> MicroFeatureValues:
        history = [
            row
            for row in self._micro.get(symbol, ())
            if row.ts_ms <= decision_ts_ms and decision_ts_ms - row.ts_ms <= 120_000
        ]
        if not history or decision_ts_ms - history[-1].ts_ms > 1_000:
            return self._empty_micro()
        current = history[-1]
        prior = history[:-1]
        spreads = [row.spread_bps for row in prior]
        mlofi_values = [row.depth_adjusted_ofi_3s_bps for row in prior]
        signed_values = [row.signed_notional_3s for row in prior]
        intensity_values = [row.trade_notional_3s for row in prior]
        current_direction = 1 if current.depth_adjusted_ofi_3s_bps >= 0 else -1
        persistence_start = current.ts_ms
        for row in reversed(history[:-1]):
            direction = 1 if row.depth_adjusted_ofi_3s_bps >= 0 else -1
            if direction != current_direction or not row.data_healthy:
                break
            persistence_start = row.ts_ms
        microprice_fraction = (
            current.microprice_minus_mid_bps / current.spread_bps if current.spread_bps > 0 else 0.0
        )
        expected_edge = abs(current.microprice_minus_mid_bps) + abs(
            current.depth_adjusted_ofi_3s_bps
        )
        signed_direction = 1 if current.signed_notional_3s >= 0 else -1
        opposing_depletion = (
            current.ask_cancel_ratio_3s if signed_direction > 0 else current.bid_cancel_ratio_3s
        )
        return MicroFeatureValues(
            spread_bps=current.spread_bps,
            spread_percentile=rolling_percentile(spreads, current.spread_bps) * 100,
            sequence_valid=current.data_healthy,
            data_stale=not current.data_healthy,
            queue_imbalance_top5=(current.imbalance_top5 + 1) / 2,
            microprice_spread_fraction=microprice_fraction,
            persistence_ms=max(0, current.ts_ms - persistence_start),
            cost_viability_passed=(
                current.data_healthy
                and expected_edge > current.spread_bps + BASE_ROUND_TRIP_COST_BPS
            ),
            mlofi_robust_z=robust_z(mlofi_values, current.depth_adjusted_ofi_3s_bps),
            price_response_aligned=(
                current.depth_adjusted_ofi_3s_bps * current.microprice_minus_mid_bps > 0
            ),
            signed_notional_z=robust_z(signed_values, current.signed_notional_3s),
            trade_intensity_z=robust_z(intensity_values, current.trade_notional_3s),
            opposing_depth_depletion=opposing_depletion,
            price_progress_efficiency=current.efficiency_ratio_30s,
            refill_ratio=max(current.bid_refill_ratio_3s, current.ask_refill_ratio_3s),
            bid_refill_ratio=current.bid_refill_ratio_3s,
            ask_refill_ratio=current.ask_refill_ratio_3s,
            ofi_aligned=current.depth_adjusted_ofi_3s_bps * reference_direction > 0,
            ofi_reversal_confirmed=(current.depth_adjusted_ofi_3s_bps * reference_direction < 0),
            microprice_reentry_confirmed=(
                current.microprice_minus_mid_bps * reference_direction < 0
            ),
        )

    @staticmethod
    def _empty_micro() -> MicroFeatureValues:
        return MicroFeatureValues(
            spread_bps=10_000.0,
            spread_percentile=100.0,
            sequence_valid=False,
            data_stale=True,
            queue_imbalance_top5=0.5,
            microprice_spread_fraction=0.0,
            persistence_ms=0,
            cost_viability_passed=False,
            mlofi_robust_z=0.0,
            price_response_aligned=False,
            signed_notional_z=0.0,
            trade_intensity_z=0.0,
            opposing_depth_depletion=0.0,
            price_progress_efficiency=0.0,
            refill_ratio=0.0,
            bid_refill_ratio=0.0,
            ask_refill_ratio=0.0,
            ofi_aligned=False,
            ofi_reversal_confirmed=False,
            microprice_reentry_confirmed=False,
        )

    @property
    def diagnostics(self) -> AlphaFeatureDiagnostics:
        return AlphaFeatureDiagnostics(
            accepted_candles=self._accepted_candles,
            duplicate_candles=self._duplicate_candles,
            out_of_order_candles=self._out_of_order_candles,
            candle_gaps=self._candle_gaps,
            accepted_microstructure=self._accepted_microstructure,
            duplicate_microstructure=self._duplicate_microstructure,
            out_of_order_microstructure=self._out_of_order_microstructure,
        )
