# 현재 미완성 봉을 배제한 다중 시간구간 장중 피처를 계산한다.

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from statistics import fmean, pstdev

from backend.app.market_data import Candle
from backend.app.market_data.timeframes import TIMEFRAME_REGISTRY


class IntradayFeatureError(ValueError):
    """시간 순서나 완성 봉 계약을 위반한 입력을 거부한다."""


class HorizonClass(StrEnum):
    MICRO_SCALP = "MICRO_SCALP"
    FAST_INTRADAY = "FAST_INTRADAY"
    INTRADAY_SWING = "INTRADAY_SWING"

    @property
    def intervals(self) -> tuple[int, ...]:
        if self is HorizonClass.MICRO_SCALP:
            return (1, 5, 15, 30)
        if self is HorizonClass.FAST_INTRADAY:
            return (60, 180, 300, 900)
        return (900, 1_800, 3_600, 14_400)


@dataclass(frozen=True, slots=True)
class TimeframeFeatureSnapshot:
    symbol: str
    interval_seconds: int
    feature_ts_ms: int
    source_open_ts_ms: int
    sample_count: int
    close: float
    atr: float
    realized_volatility: float
    session_vwap: float
    ema_fast: float
    ema_slow: float
    donchian_high: float
    donchian_low: float
    bollinger_mid: float
    bollinger_upper: float
    bollinger_lower: float
    keltner_upper: float
    keltner_lower: float
    relative_volume: float
    taker_flow_ratio: float
    close_zscore: float
    higher_timeframe_trend: str
    regime: str


class MultiTimeframeFeatureEngine:
    """동일 종목의 이미 끝난 봉만 보관하고 as-of 시각으로 계산한다."""

    def __init__(
        self,
        *,
        intervals: tuple[int, ...],
        maximum_bars: int = 1_000,
        minimum_bars: int = 20,
    ) -> None:
        if not intervals or len(set(intervals)) != len(intervals):
            raise ValueError("피처 시간구간은 중복 없는 비어 있지 않은 값이어야 합니다.")
        if maximum_bars < minimum_bars or minimum_bars < 3:
            raise ValueError("피처 보관량은 최소 표본보다 크고 최소 표본은 3 이상이어야 합니다.")
        for interval in intervals:
            TIMEFRAME_REGISTRY.validate_builder(interval)
        self.intervals = intervals
        self.maximum_bars = maximum_bars
        self.minimum_bars = minimum_bars
        self._bars: dict[tuple[str, int], deque[Candle]] = defaultdict(
            lambda: deque(maxlen=maximum_bars)
        )
        self._last_open_ts_ms: dict[tuple[str, int], int] = {}
        self._session_totals: dict[tuple[str, int], tuple[int, Decimal, Decimal]] = {}
        self._session_points: dict[
            tuple[str, int],
            deque[tuple[int, int, Decimal, Decimal]],
        ] = defaultdict(lambda: deque(maxlen=maximum_bars))
        self.duplicate_bars = 0
        self.out_of_order_bars = 0

    def ingest_completed(self, candle: Candle) -> bool:
        if candle.interval_seconds not in self.intervals:
            raise IntradayFeatureError("설정하지 않은 시간구간 봉입니다.")
        key = (candle.symbol, candle.interval_seconds)
        last_open = self._last_open_ts_ms.get(key)
        if last_open is not None and candle.open_ts_ms == last_open:
            self.duplicate_bars += 1
            return False
        if last_open is not None and candle.open_ts_ms < last_open:
            self.out_of_order_bars += 1
            return False
        self._bars[key].append(candle)
        self._last_open_ts_ms[key] = candle.open_ts_ms
        session_start = candle.open_ts_ms - candle.open_ts_ms % 86_400_000
        previous_session = self._session_totals.get(key)
        if previous_session is None or previous_session[0] != session_start:
            session_volume = Decimal(0)
            session_notional = Decimal(0)
        else:
            _, session_volume, session_notional = previous_session
        candle_notional = (
            candle.quote_volume
            if candle.quote_volume > 0
            else ((candle.high + candle.low + candle.close) / Decimal(3)) * candle.volume
        )
        session_volume += candle.volume
        session_notional += candle_notional
        self._session_totals[key] = (session_start, session_volume, session_notional)
        self._session_points[key].append(
            (candle.open_ts_ms, session_start, session_volume, session_notional)
        )
        return True

    def completed_bars(
        self,
        symbol: str,
        interval_seconds: int,
        *,
        as_of_ts_ms: int | None = None,
    ) -> tuple[Candle, ...]:
        if interval_seconds not in self.intervals:
            raise IntradayFeatureError("설정하지 않은 시간구간입니다.")
        bars = tuple(self._bars.get((symbol, interval_seconds), ()))
        if as_of_ts_ms is None:
            return bars
        return tuple(
            bar
            for bar in bars
            if bar.open_ts_ms + bar.interval_seconds * 1_000 <= as_of_ts_ms
        )

    def snapshot(
        self,
        symbol: str,
        interval_seconds: int,
        *,
        as_of_ts_ms: int | None = None,
        higher_interval_seconds: int | None = None,
    ) -> TimeframeFeatureSnapshot | None:
        bars = self.completed_bars(symbol, interval_seconds, as_of_ts_ms=as_of_ts_ms)
        if len(bars) < self.minimum_bars:
            return None
        feature_ts_ms = bars[-1].open_ts_ms + interval_seconds * 1_000
        if as_of_ts_ms is not None and feature_ts_ms > as_of_ts_ms:
            raise IntradayFeatureError("미완성 미래 봉이 피처에 포함됐습니다.")
        closes = [float(bar.close) for bar in bars]
        volumes = [float(bar.volume) for bar in bars]
        true_ranges = self._true_ranges(bars)
        atr = fmean(true_ranges[-14:])
        ema_fast = self._ema(closes, 5)
        ema_slow = self._ema(closes, 10)
        previous = bars[-21:-1]
        donchian_high = max(float(bar.high) for bar in previous)
        donchian_low = min(float(bar.low) for bar in previous)
        bollinger_values = closes[-20:]
        bollinger_mid = fmean(bollinger_values)
        bollinger_sigma = pstdev(bollinger_values)
        keltner_mid = self._ema(closes, 20)
        prior_volumes = volumes[-21:-1]
        average_prior_volume = fmean(prior_volumes)
        current = bars[-1]
        flow_total = current.taker_buy_volume + current.taker_sell_volume
        taker_flow = (
            float((current.taker_buy_volume - current.taker_sell_volume) / flow_total)
            if flow_total
            else 0.0
        )
        session_point = next(
            (
                point
                for point in reversed(self._session_points[(symbol, interval_seconds)])
                if point[0] == bars[-1].open_ts_ms
            ),
            None,
        )
        if session_point is None:
            raise IntradayFeatureError("완성 봉의 session 누적값을 찾을 수 없습니다.")
        _, _, session_volume, session_notional = session_point
        session_vwap = (
            float(session_notional / session_volume) if session_volume else closes[-1]
        )
        selected_closes = closes[-21:]
        log_returns = [
            math.log(current_close / prior_close)
            for prior_close, current_close in zip(
                selected_closes,
                selected_closes[1:],
                strict=False,
            )
            if prior_close > 0 and current_close > 0
        ]
        realized_volatility = pstdev(log_returns) if len(log_returns) >= 2 else 0.0
        close_sigma = pstdev(bollinger_values)
        close_zscore = (
            (closes[-1] - bollinger_mid) / close_sigma if close_sigma > 0 else 0.0
        )
        higher_trend = self._higher_timeframe_trend(
            symbol,
            higher_interval_seconds,
            feature_ts_ms,
        )
        if ema_fast > ema_slow and closes[-1] >= session_vwap:
            regime = "TREND_UP"
        elif ema_fast < ema_slow and closes[-1] <= session_vwap:
            regime = "TREND_DOWN"
        else:
            regime = "RANGE"
        return TimeframeFeatureSnapshot(
            symbol=symbol,
            interval_seconds=interval_seconds,
            feature_ts_ms=feature_ts_ms,
            source_open_ts_ms=bars[-1].open_ts_ms,
            sample_count=len(bars),
            close=closes[-1],
            atr=atr,
            realized_volatility=realized_volatility,
            session_vwap=session_vwap,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            donchian_high=donchian_high,
            donchian_low=donchian_low,
            bollinger_mid=bollinger_mid,
            bollinger_upper=bollinger_mid + 2 * bollinger_sigma,
            bollinger_lower=bollinger_mid - 2 * bollinger_sigma,
            keltner_upper=keltner_mid + 1.5 * atr,
            keltner_lower=keltner_mid - 1.5 * atr,
            relative_volume=(
                volumes[-1] / average_prior_volume if average_prior_volume > 0 else 0.0
            ),
            taker_flow_ratio=taker_flow,
            close_zscore=close_zscore,
            higher_timeframe_trend=higher_trend,
            regime=regime,
        )

    def _higher_timeframe_trend(
        self,
        symbol: str,
        interval_seconds: int | None,
        as_of_ts_ms: int,
    ) -> str:
        if interval_seconds is None:
            return "UNAVAILABLE"
        higher = self.completed_bars(symbol, interval_seconds, as_of_ts_ms=as_of_ts_ms)
        if len(higher) < 10:
            return "UNAVAILABLE"
        closes = [float(bar.close) for bar in higher]
        fast = self._ema(closes, 5)
        slow = self._ema(closes, 10)
        if fast > slow:
            return "UP"
        if fast < slow:
            return "DOWN"
        return "FLAT"

    @staticmethod
    def _true_ranges(bars: tuple[Candle, ...]) -> list[float]:
        values: list[float] = []
        previous_close = float(bars[0].close)
        for bar in bars:
            high = float(bar.high)
            low = float(bar.low)
            values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
            previous_close = float(bar.close)
        return values

    @staticmethod
    def _ema(values: list[float], period: int) -> float:
        selected = values[-max(period * 4, period) :]
        result = selected[0]
        alpha = 2 / (period + 1)
        for value in selected[1:]:
            result = alpha * value + (1 - alpha) * result
        return result
