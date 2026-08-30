# 느린 추세·시장 레짐·종목 상대강도를 결합한 24개 PAPER 후보를 고정 비용으로 비교한다.

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean, median

from backend.app.build_identity import git_commit
from backend.app.research import (
    bootstrap_mean_interval,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)
from scripts.research_intraday_trend_tournament import load_segmented_public_klines
from scripts.research_public_intraday_trend_candidates import IntradayBar, aggregate_bars
from scripts.research_public_trend_candidates import (
    BASE_COST_BPS,
    DEFAULT_SYMBOLS,
    STRESS_COST_BPS,
    Kline,
    _ema,
    _parse_date,
    _rolling_mean,
)

MINIMUM_RESEARCH_DAYS = 180
MINIMUM_DEVELOPMENT_SAMPLE = 60
MINIMUM_VALIDATION_SAMPLE = 20
MINIMUM_OOS_SAMPLE = 30
MAXIMUM_CONCURRENT_POSITIONS = 2
MAXIMUM_DAILY_ENTRIES = 2
MAXIMUM_FINALISTS = 4
EMBARGO_MS = 7 * 86_400_000
TP1_FRACTION = 0.4
SEED = 20260830


@dataclass(frozen=True, slots=True)
class SlowTrendSpec:
    candidate_id: str
    family: str
    interval_minutes: int
    setup_kind: str
    side_policy: str
    style: str
    lookback: int
    momentum_72h_minimum: float
    rank_threshold: float
    breadth_threshold: float
    adx_minimum: float
    relative_volume_minimum: float
    retest_band_atr: float
    stop_buffer_atr: float
    tp1_r: float
    tp2_r: float
    cooldown_hours: int
    require_slow_alignment: bool

    @property
    def cooldown_ms(self) -> int:
        return self.cooldown_hours * 3_600_000


def _spec(
    family_key: str,
    family: str,
    interval_minutes: int,
    setup_kind: str,
    side_policy: str,
    style: str,
    *,
    lookback: int,
    momentum: float,
    rank_threshold: float,
    breadth: float,
    adx: float,
    relative_volume: float,
    retest_band: float,
    stop_buffer: float,
    tp1_r: float,
    tp2_r: float,
    cooldown_hours: int,
    slow_alignment: bool,
) -> SlowTrendSpec:
    return SlowTrendSpec(
        candidate_id=f"T117_{family_key}_{side_policy}_{style}",
        family=family,
        interval_minutes=interval_minutes,
        setup_kind=setup_kind,
        side_policy=side_policy,
        style=style,
        lookback=lookback,
        momentum_72h_minimum=momentum,
        rank_threshold=rank_threshold,
        breadth_threshold=breadth,
        adx_minimum=adx,
        relative_volume_minimum=relative_volume,
        retest_band_atr=retest_band,
        stop_buffer_atr=stop_buffer,
        tp1_r=tp1_r,
        tp2_r=tp2_r,
        cooldown_hours=cooldown_hours,
        require_slow_alignment=slow_alignment,
    )


def _candidate_specs() -> tuple[SlowTrendSpec, ...]:
    families = (
        (
            "BREAKOUT_4H",
            "FOUR_HOUR_CHANNEL_BREAKOUT",
            240,
            "CHANNEL_BREAKOUT",
            {
                "BALANCED": dict(
                    lookback=20,
                    momentum=0.02,
                    rank_threshold=0.67,
                    breadth=0.55,
                    adx=16,
                    relative_volume=0.70,
                    retest_band=0.0,
                    stop_buffer=0.30,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=18,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=40,
                    momentum=0.04,
                    rank_threshold=0.83,
                    breadth=0.65,
                    adx=22,
                    relative_volume=1.00,
                    retest_band=0.0,
                    stop_buffer=0.45,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=30,
                    slow_alignment=True,
                ),
            },
        ),
        (
            "RETEST_1H",
            "ONE_HOUR_BREAKOUT_RETEST",
            60,
            "BREAKOUT_RETEST",
            {
                "BALANCED": dict(
                    lookback=20,
                    momentum=0.02,
                    rank_threshold=0.67,
                    breadth=0.55,
                    adx=17,
                    relative_volume=0.75,
                    retest_band=0.45,
                    stop_buffer=0.30,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=12,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=36,
                    momentum=0.04,
                    rank_threshold=0.83,
                    breadth=0.65,
                    adx=22,
                    relative_volume=1.00,
                    retest_band=0.30,
                    stop_buffer=0.40,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=20,
                    slow_alignment=True,
                ),
            },
        ),
        (
            "PULLBACK_1H",
            "ONE_HOUR_FIRST_PULLBACK",
            60,
            "FIRST_PULLBACK_RECLAIM",
            {
                "BALANCED": dict(
                    lookback=20,
                    momentum=0.02,
                    rank_threshold=0.67,
                    breadth=0.55,
                    adx=16,
                    relative_volume=0.60,
                    retest_band=0.35,
                    stop_buffer=0.25,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=12,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=32,
                    momentum=0.04,
                    rank_threshold=0.83,
                    breadth=0.65,
                    adx=20,
                    relative_volume=0.85,
                    retest_band=0.22,
                    stop_buffer=0.35,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=20,
                    slow_alignment=True,
                ),
            },
        ),
        (
            "RELATIVE_4H",
            "FOUR_HOUR_RELATIVE_MOMENTUM",
            240,
            "RELATIVE_CONTINUATION",
            {
                "BALANCED": dict(
                    lookback=18,
                    momentum=0.03,
                    rank_threshold=0.67,
                    breadth=0.55,
                    adx=16,
                    relative_volume=0.65,
                    retest_band=0.35,
                    stop_buffer=0.30,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=18,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=30,
                    momentum=0.05,
                    rank_threshold=0.83,
                    breadth=0.65,
                    adx=22,
                    relative_volume=0.90,
                    retest_band=0.22,
                    stop_buffer=0.45,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=30,
                    slow_alignment=True,
                ),
            },
        ),
    )
    output: list[SlowTrendSpec] = []
    for family_key, family, interval, setup_kind, styles in families:
        for side_policy in ("LONG", "SHORT", "BOTH"):
            for style, parameters in styles.items():
                output.append(
                    _spec(
                        family_key,
                        family,
                        interval,
                        setup_kind,
                        side_policy,
                        style,
                        **parameters,
                    )
                )
    return tuple(output)


PREREGISTERED_CANDIDATES = _candidate_specs()


@dataclass(frozen=True, slots=True)
class SlowFeatures:
    ema20: float
    ema50: float
    ema200: float
    ema20_slope: float
    ema50_slope: float
    atr: float
    adx: float
    relative_volume: float
    momentum_24h: float
    momentum_72h: float
    momentum_168h: float
    trend_age_bars: int


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    close_ts_ms: int
    breadth: float
    btc_close: float
    btc_features: SlowFeatures
    features_by_symbol: Mapping[str, SlowFeatures]
    rank_by_symbol: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class SlowTrendOutcome:
    candidate_id: str
    family: str
    symbol: str
    side: str
    signal_ts_ms: int
    entry_ts_ms: int
    exit_ts_ms: int
    holding_minutes: int
    exit_reason: str
    tp1_hit_ts_ms: int | None
    tp2_hit_ts_ms: int | None
    entry: float
    stop: float
    take_profit_1: float
    take_profit_2: float
    gross_bps: float | None
    base_net_bps: float | None
    stress_net_bps: float | None
    score: float
    regime_breadth: float
    relative_rank: float
    censored: bool


def _features(rows: Sequence[IntradayBar]) -> tuple[SlowFeatures | None, ...]:
    closes = [row.close for row in rows]
    volumes = [row.volume for row in rows]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200)
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index, row in enumerate(rows):
        if index == 0:
            true_ranges.append(row.high - row.low)
            plus_dm.append(0.0)
            minus_dm.append(0.0)
            continue
        previous = rows[index - 1]
        true_ranges.append(
            max(row.high - row.low, abs(row.high - previous.close), abs(row.low - previous.close))
        )
        high_move = row.high - previous.high
        low_move = previous.low - row.low
        plus_dm.append(high_move if high_move > low_move and high_move > 0 else 0.0)
        minus_dm.append(low_move if low_move > high_move and low_move > 0 else 0.0)
    atr = _rolling_mean(true_ranges, 14)
    plus_mean = _rolling_mean(plus_dm, 14)
    minus_mean = _rolling_mean(minus_dm, 14)
    dx: list[float] = []
    for index in range(len(rows)):
        if math.isnan(atr[index]) or atr[index] <= 0:
            dx.append(0.0)
            continue
        plus_di = 100 * plus_mean[index] / atr[index]
        minus_di = 100 * minus_mean[index] / atr[index]
        denominator = plus_di + minus_di
        dx.append(100 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
    adx = _rolling_mean(dx, 14)
    volume_mean = _rolling_mean(volumes, 20)
    bars_24h = 24 * 60 // rows[0].interval_minutes
    bars_72h = 72 * 60 // rows[0].interval_minutes
    bars_168h = 168 * 60 // rows[0].interval_minutes
    age = 0
    prior_direction = 0
    output: list[SlowFeatures | None] = []
    for index, row in enumerate(rows):
        direction = 1 if ema20[index] > ema50[index] else -1 if ema20[index] < ema50[index] else 0
        age = age + 1 if direction != 0 and direction == prior_direction else 1 if direction else 0
        prior_direction = direction
        if (
            index < max(203, bars_168h)
            or math.isnan(atr[index])
            or math.isnan(adx[index])
        ):
            output.append(None)
            continue
        prior_volume = volume_mean[index - 1]
        output.append(
            SlowFeatures(
                ema20=ema20[index],
                ema50=ema50[index],
                ema200=ema200[index],
                ema20_slope=ema20[index] - ema20[index - 3],
                ema50_slope=ema50[index] - ema50[index - 3],
                atr=atr[index],
                adx=adx[index],
                relative_volume=row.volume / prior_volume if prior_volume > 0 else 0.0,
                momentum_24h=row.close / rows[index - bars_24h].close - 1,
                momentum_72h=row.close / rows[index - bars_72h].close - 1,
                momentum_168h=row.close / rows[index - bars_168h].close - 1,
                trend_age_bars=age,
            )
        )
    return tuple(output)


def _context_snapshots(
    rows_by_symbol: Mapping[str, Sequence[IntradayBar]],
    features_by_symbol: Mapping[str, Sequence[SlowFeatures | None]],
) -> tuple[MarketSnapshot, ...]:
    rows_at_close: dict[int, dict[str, IntradayBar]] = defaultdict(dict)
    features_at_close: dict[int, dict[str, SlowFeatures]] = defaultdict(dict)
    for symbol, rows in rows_by_symbol.items():
        features = features_by_symbol[symbol]
        for row, feature in zip(rows, features, strict=True):
            if feature is None:
                continue
            rows_at_close[row.close_ts_ms][symbol] = row
            features_at_close[row.close_ts_ms][symbol] = feature
    output: list[MarketSnapshot] = []
    required = set(rows_by_symbol)
    for close_ts_ms in sorted(features_at_close):
        current_features = features_at_close[close_ts_ms]
        current_rows = rows_at_close[close_ts_ms]
        if set(current_features) != required or "BTCUSDT" not in current_rows:
            continue
        ordered = sorted(
            current_features,
            key=lambda symbol: (current_features[symbol].momentum_72h, symbol),
        )
        denominator = max(1, len(ordered) - 1)
        ranks = {symbol: index / denominator for index, symbol in enumerate(ordered)}
        breadth = sum(
            current_rows[symbol].close > current_features[symbol].ema50
            for symbol in required
        ) / len(required)
        output.append(
            MarketSnapshot(
                close_ts_ms=close_ts_ms,
                breadth=breadth,
                btc_close=current_rows["BTCUSDT"].close,
                btc_features=current_features["BTCUSDT"],
                features_by_symbol=dict(current_features),
                rank_by_symbol=ranks,
            )
        )
    return tuple(output)


def _allowed_directions(side_policy: str) -> tuple[int, ...]:
    if side_policy == "LONG":
        return (1,)
    if side_policy == "SHORT":
        return (-1,)
    return (1, -1)


def _regime_allows(
    snapshot: MarketSnapshot,
    symbol: str,
    direction: int,
    spec: SlowTrendSpec,
) -> bool:
    features = snapshot.features_by_symbol.get(symbol)
    rank = snapshot.rank_by_symbol.get(symbol)
    if features is None or rank is None:
        return False
    btc = snapshot.btc_features
    if direction > 0:
        market_ok = (
            snapshot.breadth >= spec.breadth_threshold
            and snapshot.btc_close > btc.ema50
            and btc.ema50_slope > 0
        )
        symbol_ok = (
            features.ema20 > features.ema50
            and features.ema20_slope > 0
            and features.momentum_72h >= spec.momentum_72h_minimum
            and rank >= spec.rank_threshold
        )
        slow_ok = (
            not spec.require_slow_alignment
            or (features.ema50 > features.ema200 and snapshot.btc_close > btc.ema200)
        )
        return market_ok and symbol_ok and slow_ok
    market_ok = (
        snapshot.breadth <= 1 - spec.breadth_threshold
        and snapshot.btc_close < btc.ema50
        and btc.ema50_slope < 0
    )
    symbol_ok = (
        features.ema20 < features.ema50
        and features.ema20_slope < 0
        and features.momentum_72h <= -spec.momentum_72h_minimum
        and rank <= 1 - spec.rank_threshold
    )
    slow_ok = (
        not spec.require_slow_alignment
        or (features.ema50 < features.ema200 and snapshot.btc_close < btc.ema200)
    )
    return market_ok and symbol_ok and slow_ok


def _midpoint(rows: Sequence[IntradayBar], start: int, end: int) -> float:
    window = rows[start:end]
    if not window:
        raise ValueError("중간값 계산 구간은 비어 있을 수 없습니다.")
    return (max(row.high for row in window) + min(row.low for row in window)) / 2


def _ichimoku_at(
    rows: Sequence[IntradayBar],
    index: int,
) -> tuple[float, float, float, float] | None:
    cloud_source = index - 26
    if index < 25 or cloud_source < 51:
        return None
    conversion = _midpoint(rows, index - 8, index + 1)
    base = _midpoint(rows, index - 25, index + 1)
    source_conversion = _midpoint(rows, cloud_source - 8, cloud_source + 1)
    source_base = _midpoint(rows, cloud_source - 25, cloud_source + 1)
    leading_a = (source_conversion + source_base) / 2
    leading_b = _midpoint(rows, cloud_source - 51, cloud_source + 1)
    return conversion, base, leading_a, leading_b


def _setup(
    rows: Sequence[IntradayBar],
    feature_rows: Sequence[SlowFeatures | None],
    index: int,
    direction: int,
    spec: SlowTrendSpec,
) -> tuple[bool, float | None]:
    current = rows[index]
    previous = rows[index - 1]
    features = feature_rows[index]
    previous_features = feature_rows[index - 1]
    if features is None or previous_features is None:
        return False, None
    atr = features.atr
    if spec.setup_kind == "LIQUIDITY_SWEEP_RECLAIM":
        history = rows[index - spec.lookback : index]
        if len(history) != spec.lookback:
            return False, None
        close_location = (current.close - current.low) / max(current.high - current.low, 1e-12)
        if direction > 0:
            level = min(row.low for row in history)
            sweep_depth = level - current.low
            ready = (
                0 < sweep_depth <= atr * spec.retest_band_atr
                and current.close > level
                and current.close > current.open
                and close_location >= (0.62 if spec.style == "SELECTIVE" else 0.52)
                and (
                    spec.style != "SELECTIVE"
                    or current.close > previous.high
                )
            )
            return ready, current.low - atr * spec.stop_buffer_atr
        level = max(row.high for row in history)
        sweep_depth = current.high - level
        ready = (
            0 < sweep_depth <= atr * spec.retest_band_atr
            and current.close < level
            and current.close < current.open
            and close_location <= (0.38 if spec.style == "SELECTIVE" else 0.48)
            and (
                spec.style != "SELECTIVE"
                or current.close < previous.low
            )
        )
        return ready, current.high + atr * spec.stop_buffer_atr
    if spec.setup_kind == "ICHIMOKU_PULLBACK_CONTINUATION":
        current_lines = _ichimoku_at(rows, index)
        previous_lines = _ichimoku_at(rows, index - 1)
        if current_lines is None or previous_lines is None:
            return False, None
        conversion, base, leading_a, leading_b = current_lines
        previous_conversion, previous_base, _, _ = previous_lines
        cloud_top = max(leading_a, leading_b)
        cloud_bottom = min(leading_a, leading_b)
        band = atr * spec.retest_band_atr
        if direction > 0:
            pullback_touched = previous.low <= max(previous_conversion, previous_base) + band
            ready = (
                current.close > cloud_top
                and conversion > base
                and pullback_touched
                and current.close > conversion
                and current.close > previous.high
                and current.close > current.open
                and (
                    spec.style != "SELECTIVE"
                    or (leading_a > leading_b and base > cloud_top)
                )
            )
            return ready, min(previous.low, current.low, cloud_bottom) - atr * spec.stop_buffer_atr
        pullback_touched = previous.high >= min(previous_conversion, previous_base) - band
        ready = (
            current.close < cloud_bottom
            and conversion < base
            and pullback_touched
            and current.close < conversion
            and current.close < previous.low
            and current.close < current.open
            and (
                spec.style != "SELECTIVE"
                or (leading_a < leading_b and base < cloud_bottom)
            )
        )
        return ready, max(previous.high, current.high, cloud_top) + atr * spec.stop_buffer_atr
    if spec.setup_kind == "CHANNEL_BREAKOUT":
        history = rows[index - spec.lookback : index]
        if len(history) != spec.lookback:
            return False, None
        if direction > 0:
            ready = (
                current.close > max(row.high for row in history)
                and current.close > current.open
            )
            return ready, min(previous.low, current.low) - atr * spec.stop_buffer_atr
        ready = current.close < min(row.low for row in history) and current.close < current.open
        return ready, max(previous.high, current.high) + atr * spec.stop_buffer_atr
    if spec.setup_kind == "BREAKOUT_RETEST":
        history = rows[index - spec.lookback - 1 : index - 1]
        if len(history) != spec.lookback:
            return False, None
        band = atr * spec.retest_band_atr
        if direction > 0:
            level = max(row.high for row in history)
            ready = (
                previous.close > level
                and level - band <= current.low <= level + band
                and current.close > level
                and current.close > current.open
            )
            return ready, min(previous.low, current.low) - atr * spec.stop_buffer_atr
        level = min(row.low for row in history)
        ready = (
            previous.close < level
            and level - band <= current.high <= level + band
            and current.close < level
            and current.close < current.open
        )
        return ready, max(previous.high, current.high) + atr * spec.stop_buffer_atr
    if spec.setup_kind == "FIRST_PULLBACK_RECLAIM":
        maximum_age = max(8, spec.lookback)
        if not 2 <= features.trend_age_bars <= maximum_age:
            return False, None
        band = atr * spec.retest_band_atr
        if direction > 0:
            ready = (
                previous.low <= previous_features.ema20 + band
                and previous.close >= previous_features.ema50
                and current.close > features.ema20
                and current.close > previous.high
                and current.close > current.open
            )
            return ready, min(previous.low, current.low) - atr * spec.stop_buffer_atr
        ready = (
            previous.high >= previous_features.ema20 - band
            and previous.close <= previous_features.ema50
            and current.close < features.ema20
            and current.close < previous.low
            and current.close < current.open
        )
        return ready, max(previous.high, current.high) + atr * spec.stop_buffer_atr
    if direction > 0:
        ready = (
            previous.close < previous.open
            and previous.low <= previous_features.ema20 + atr * spec.retest_band_atr
            and current.close > previous.high
            and current.close > features.ema20
            and current.close > current.open
        )
        return ready, min(previous.low, current.low) - atr * spec.stop_buffer_atr
    ready = (
        previous.close > previous.open
        and previous.high >= previous_features.ema20 - atr * spec.retest_band_atr
        and current.close < previous.low
        and current.close < features.ema20
        and current.close < current.open
    )
    return ready, max(previous.high, current.high) + atr * spec.stop_buffer_atr


def _return_bps(side: str, entry: float, exit_price: float) -> float:
    direction = 1 if side == "LONG" else -1
    return (exit_price - entry) / entry * 10_000 * direction


def _simulate(
    rows: Sequence[IntradayBar],
    *,
    index: int,
    direction: int,
    structural_stop: float,
    signal_atr: float,
    score: float,
    breadth: float,
    relative_rank: float,
    spec: SlowTrendSpec,
) -> SlowTrendOutcome | None:
    entry_index = index + 1
    if entry_index >= len(rows):
        return None
    entry = rows[entry_index].open
    risk = (entry - structural_stop) * direction
    risk_atr = risk / signal_atr if signal_atr > 0 else math.inf
    if risk <= 0 or not 0.65 <= risk_atr <= 4.0:
        return None
    side = "LONG" if direction > 0 else "SHORT"
    current_stop = structural_stop
    tp1 = entry + direction * risk * spec.tp1_r
    tp2 = entry + direction * risk * spec.tp2_r
    realized = 0.0
    remaining = 1.0
    tp1_hit_ts_ms: int | None = None
    for cursor in range(entry_index, len(rows)):
        bar = rows[cursor]
        stop_hit = bar.low <= current_stop if direction > 0 else bar.high >= current_stop
        tp1_hit = bar.high >= tp1 if direction > 0 else bar.low <= tp1
        tp2_hit = bar.high >= tp2 if direction > 0 else bar.low <= tp2
        if stop_hit:
            gross_bps = realized + remaining * _return_bps(side, entry, current_stop)
            return SlowTrendOutcome(
                candidate_id=spec.candidate_id,
                family=spec.family,
                symbol=rows[index].symbol,
                side=side,
                signal_ts_ms=rows[index].close_ts_ms,
                entry_ts_ms=rows[entry_index].open_ts_ms,
                exit_ts_ms=bar.close_ts_ms,
                holding_minutes=(cursor - entry_index + 1) * spec.interval_minutes,
                exit_reason="STOP_AFTER_TP1" if tp1_hit_ts_ms is not None else "STOP",
                tp1_hit_ts_ms=tp1_hit_ts_ms,
                tp2_hit_ts_ms=None,
                entry=entry,
                stop=structural_stop,
                take_profit_1=tp1,
                take_profit_2=tp2,
                gross_bps=gross_bps,
                base_net_bps=gross_bps - BASE_COST_BPS,
                stress_net_bps=gross_bps - STRESS_COST_BPS,
                score=score,
                regime_breadth=breadth,
                relative_rank=relative_rank,
                censored=False,
            )
        if tp2_hit:
            if tp1_hit_ts_ms is None:
                tp1_hit_ts_ms = bar.close_ts_ms
                realized = TP1_FRACTION * _return_bps(side, entry, tp1)
                remaining = 1 - TP1_FRACTION
            gross_bps = realized + remaining * _return_bps(side, entry, tp2)
            return SlowTrendOutcome(
                candidate_id=spec.candidate_id,
                family=spec.family,
                symbol=rows[index].symbol,
                side=side,
                signal_ts_ms=rows[index].close_ts_ms,
                entry_ts_ms=rows[entry_index].open_ts_ms,
                exit_ts_ms=bar.close_ts_ms,
                holding_minutes=(cursor - entry_index + 1) * spec.interval_minutes,
                exit_reason="TP2",
                tp1_hit_ts_ms=tp1_hit_ts_ms,
                tp2_hit_ts_ms=bar.close_ts_ms,
                entry=entry,
                stop=structural_stop,
                take_profit_1=tp1,
                take_profit_2=tp2,
                gross_bps=gross_bps,
                base_net_bps=gross_bps - BASE_COST_BPS,
                stress_net_bps=gross_bps - STRESS_COST_BPS,
                score=score,
                regime_breadth=breadth,
                relative_rank=relative_rank,
                censored=False,
            )
        if tp1_hit and tp1_hit_ts_ms is None:
            tp1_hit_ts_ms = bar.close_ts_ms
            realized = TP1_FRACTION * _return_bps(side, entry, tp1)
            remaining = 1 - TP1_FRACTION
            current_stop = entry + direction * entry * STRESS_COST_BPS / 10_000
            protected_stop_hit = (
                bar.low <= current_stop if direction > 0 else bar.high >= current_stop
            )
            if protected_stop_hit:
                gross_bps = realized + remaining * _return_bps(
                    side, entry, current_stop
                )
                return SlowTrendOutcome(
                    candidate_id=spec.candidate_id,
                    family=spec.family,
                    symbol=rows[index].symbol,
                    side=side,
                    signal_ts_ms=rows[index].close_ts_ms,
                    entry_ts_ms=rows[entry_index].open_ts_ms,
                    exit_ts_ms=bar.close_ts_ms,
                    holding_minutes=(cursor - entry_index + 1) * spec.interval_minutes,
                    exit_reason="STOP_AFTER_TP1",
                    tp1_hit_ts_ms=tp1_hit_ts_ms,
                    tp2_hit_ts_ms=None,
                    entry=entry,
                    stop=structural_stop,
                    take_profit_1=tp1,
                    take_profit_2=tp2,
                    gross_bps=gross_bps,
                    base_net_bps=gross_bps - BASE_COST_BPS,
                    stress_net_bps=gross_bps - STRESS_COST_BPS,
                    score=score,
                    regime_breadth=breadth,
                    relative_rank=relative_rank,
                    censored=False,
                )
    last = rows[-1]
    return SlowTrendOutcome(
        candidate_id=spec.candidate_id,
        family=spec.family,
        symbol=rows[index].symbol,
        side=side,
        signal_ts_ms=rows[index].close_ts_ms,
        entry_ts_ms=rows[entry_index].open_ts_ms,
        exit_ts_ms=last.close_ts_ms,
        holding_minutes=(len(rows) - entry_index) * spec.interval_minutes,
        exit_reason="CENSORED_OPEN",
        tp1_hit_ts_ms=tp1_hit_ts_ms,
        tp2_hit_ts_ms=None,
        entry=entry,
        stop=structural_stop,
        take_profit_1=tp1,
        take_profit_2=tp2,
        gross_bps=None,
        base_net_bps=None,
        stress_net_bps=None,
        score=score,
        regime_breadth=breadth,
        relative_rank=relative_rank,
        censored=True,
    )


def _symbol_outcomes(
    rows: Sequence[IntradayBar],
    feature_rows: Sequence[SlowFeatures | None],
    snapshots: Sequence[MarketSnapshot],
    spec: SlowTrendSpec,
) -> list[SlowTrendOutcome]:
    snapshot_times = [snapshot.close_ts_ms for snapshot in snapshots]
    output: list[SlowTrendOutcome] = []
    cooldown_until = 0
    start = max(205, spec.lookback + 2)
    symbol = rows[0].symbol
    for index in range(start, len(rows) - 1):
        features = feature_rows[index]
        if features is None or rows[index].open_ts_ms < cooldown_until:
            continue
        if (
            features.adx < spec.adx_minimum
            or features.relative_volume < spec.relative_volume_minimum
        ):
            continue
        snapshot_index = bisect.bisect_right(snapshot_times, rows[index].close_ts_ms) - 1
        if snapshot_index < 0:
            continue
        snapshot = snapshots[snapshot_index]
        relative_rank = snapshot.rank_by_symbol.get(symbol)
        if relative_rank is None:
            continue
        for direction in _allowed_directions(spec.side_policy):
            if not _regime_allows(snapshot, symbol, direction, spec):
                continue
            ready, structural_stop = _setup(rows, feature_rows, index, direction, spec)
            if not ready or structural_stop is None:
                continue
            rank_strength = relative_rank if direction > 0 else 1 - relative_rank
            score = (
                abs(features.momentum_72h) * 100
                + abs(features.momentum_168h) * 40
                + features.adx / 50
                + features.relative_volume
                + rank_strength * 2
                + abs(snapshot.breadth - 0.5) * 4
            )
            outcome = _simulate(
                rows,
                index=index,
                direction=direction,
                structural_stop=structural_stop,
                signal_atr=features.atr,
                score=score,
                breadth=snapshot.breadth,
                relative_rank=relative_rank,
                spec=spec,
            )
            if outcome is not None:
                output.append(outcome)
                cooldown_until = outcome.exit_ts_ms + spec.cooldown_ms
            break
    return output


def apply_portfolio_limits(
    outcomes: Iterable[SlowTrendOutcome],
) -> tuple[SlowTrendOutcome, ...]:
    selected: list[SlowTrendOutcome] = []
    open_until: list[int] = []
    entries_by_day: dict[int, int] = defaultdict(int)
    for outcome in sorted(outcomes, key=lambda row: (row.entry_ts_ms, -row.score, row.symbol)):
        open_until = [value for value in open_until if value > outcome.entry_ts_ms]
        day = outcome.entry_ts_ms // 86_400_000
        if len(open_until) >= MAXIMUM_CONCURRENT_POSITIONS:
            continue
        if entries_by_day[day] >= MAXIMUM_DAILY_ENTRIES:
            continue
        selected.append(outcome)
        open_until.append(outcome.exit_ts_ms)
        entries_by_day[day] += 1
    return tuple(selected)


def research_tournament(
    data: Mapping[str, Sequence[Kline]],
    specs: Sequence[SlowTrendSpec] = PREREGISTERED_CANDIDATES,
) -> dict[str, tuple[SlowTrendOutcome, ...]]:
    intervals = {spec.interval_minutes for spec in specs}
    rows_by_interval = {
        interval: {
            symbol: aggregate_bars(rows, interval)
            for symbol, rows in sorted(data.items())
        }
        for interval in intervals
    }
    features_by_interval = {
        interval: {
            symbol: _features(rows)
            for symbol, rows in rows_by_interval[interval].items()
        }
        for interval in intervals
    }
    context_rows = {
        symbol: aggregate_bars(rows, 240)
        for symbol, rows in sorted(data.items())
    }
    context_features = {symbol: _features(rows) for symbol, rows in context_rows.items()}
    snapshots = _context_snapshots(context_rows, context_features)
    raw: dict[str, list[SlowTrendOutcome]] = {spec.candidate_id: [] for spec in specs}
    for spec in specs:
        for symbol in sorted(data):
            raw[spec.candidate_id].extend(
                _symbol_outcomes(
                    rows_by_interval[spec.interval_minutes][symbol],
                    features_by_interval[spec.interval_minutes][symbol],
                    snapshots,
                    spec,
                )
            )
    return {candidate_id: apply_portfolio_limits(rows) for candidate_id, rows in raw.items()}


def _split(
    rows: Sequence[SlowTrendOutcome],
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, tuple[SlowTrendOutcome, ...]]:
    train_end = start_ms + int((end_ms - start_ms) * 0.50)
    validation_end = start_ms + int((end_ms - start_ms) * 0.70)
    closed = [row for row in rows if not row.censored]
    return {
        "train": tuple(row for row in closed if row.exit_ts_ms < train_end - EMBARGO_MS),
        "validation": tuple(
            row
            for row in closed
            if row.entry_ts_ms > train_end + EMBARGO_MS
            and row.exit_ts_ms < validation_end - EMBARGO_MS
        ),
        "oos": tuple(row for row in closed if row.entry_ts_ms > validation_end + EMBARGO_MS),
        "censored": tuple(row for row in rows if row.censored),
    }


def _profile(rows: Sequence[SlowTrendOutcome], field: str) -> dict[str, object]:
    values = [float(value) for row in rows if (value := getattr(row, field)) is not None]
    if not values:
        return {
            "sample_size": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "expectancy_bps": None,
            "profit_factor": None,
            "payoff_ratio": None,
            "net_sum_bps": 0.0,
            "maximum_drawdown_bps": 0.0,
            "sample_status": "INSUFFICIENT",
        }
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    equity = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    gross_loss = abs(sum(losses))
    average_win = fmean(wins) if wins else None
    average_loss = abs(fmean(losses)) if losses else None
    return {
        "sample_size": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(values),
        "expectancy_bps": fmean(values),
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "payoff_ratio": (
            average_win / average_loss
            if average_win is not None and average_loss not in {None, 0}
            else None
        ),
        "net_sum_bps": sum(values),
        "maximum_drawdown_bps": maximum_drawdown,
        "sample_status": "ENOUGH" if len(values) >= MINIMUM_OOS_SAMPLE else "INSUFFICIENT",
    }


def _development_profile(
    parts: Mapping[str, Sequence[SlowTrendOutcome]],
) -> dict[str, object]:
    development = (*parts["train"], *parts["validation"])
    return {
        "gross": _profile(development, "gross_bps"),
        "base": _profile(development, "base_net_bps"),
        "stress": _profile(development, "stress_net_bps"),
        "validation_stress": _profile(parts["validation"], "stress_net_bps"),
        "exit_reasons": dict(Counter(row.exit_reason for row in development)),
        "median_holding_minutes": (
            median(row.holding_minutes for row in development) if development else None
        ),
        "censored_count": len(parts["censored"]),
    }


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _eligible(profile: Mapping[str, object]) -> bool:
    base = profile["base"]
    stress = profile["stress"]
    validation = profile["validation_stress"]
    assert isinstance(base, Mapping)
    assert isinstance(stress, Mapping)
    assert isinstance(validation, Mapping)
    return (
        int(base["sample_size"]) >= MINIMUM_DEVELOPMENT_SAMPLE
        and int(validation["sample_size"]) >= MINIMUM_VALIDATION_SAMPLE
        and _number(stress["expectancy_bps"]) is not None
        and float(stress["expectancy_bps"]) > 0
        and _number(stress["profit_factor"]) is not None
        and float(stress["profit_factor"]) >= 1.05
        and _number(validation["expectancy_bps"]) is not None
        and float(validation["expectancy_bps"]) > 0
        and _number(validation["profit_factor"]) is not None
        and float(validation["profit_factor"]) > 1
    )


def _rankable(profile: Mapping[str, object]) -> bool:
    base = profile["base"]
    validation = profile["validation_stress"]
    assert isinstance(base, Mapping)
    assert isinstance(validation, Mapping)
    return (
        int(base["sample_size"]) >= MINIMUM_DEVELOPMENT_SAMPLE
        and int(validation["sample_size"]) >= MINIMUM_VALIDATION_SAMPLE
    )


def _rank_key(profile: Mapping[str, object]) -> tuple[float, float, float]:
    validation = profile["validation_stress"]
    stress = profile["stress"]
    assert isinstance(validation, Mapping)
    assert isinstance(stress, Mapping)

    def numeric(value: object) -> float:
        number = _number(value)
        return -math.inf if number is None else number

    return (
        numeric(validation["expectancy_bps"]),
        numeric(validation["profit_factor"]),
        numeric(stress["expectancy_bps"]),
    )


def _select_finalists(
    development: Mapping[str, Mapping[str, object]],
    specs: Sequence[SlowTrendSpec],
) -> tuple[str, ...]:
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    eligible = sorted(
        (candidate_id for candidate_id, profile in development.items() if _eligible(profile)),
        key=lambda candidate_id: (*_rank_key(development[candidate_id]), candidate_id),
        reverse=True,
    )
    selected: list[str] = []
    selected_families: set[str] = set()
    for candidate_id in eligible:
        family = spec_by_id[candidate_id].family
        if family in selected_families:
            continue
        selected.append(candidate_id)
        selected_families.add(family)
        if len(selected) == MAXIMUM_FINALISTS:
            break
    return tuple(selected)


def _fold_returns(
    outcomes: Mapping[str, Sequence[SlowTrendOutcome]],
    *,
    start_ms: int,
    development_end_ms: int,
) -> dict[str, tuple[float, ...]]:
    width = (development_end_ms - start_ms) / 8
    return {
        candidate_id: tuple(
            fmean(values)
            if (
                values := [
                    float(row.base_net_bps)
                    for row in rows
                    if not row.censored
                    and row.base_net_bps is not None
                    and start_ms + fold * width <= row.entry_ts_ms
                    < start_ms + (fold + 1) * width
                ]
            )
            else 0.0
            for fold in range(8)
        )
        for candidate_id, rows in outcomes.items()
    }


def _concentration(rows: Sequence[SlowTrendOutcome]) -> dict[str, object]:
    by_symbol: dict[str, float] = defaultdict(float)
    for row in rows:
        if row.base_net_bps is not None:
            by_symbol[row.symbol] += row.base_net_bps
    positive_total = sum(value for value in by_symbol.values() if value > 0)
    largest_symbol = max(by_symbol, key=by_symbol.get) if by_symbol else None
    largest_share = (
        max(by_symbol.values()) / positive_total
        if by_symbol and positive_total > 0
        else None
    )
    return {
        "base_net_bps_by_symbol": dict(sorted(by_symbol.items())),
        "largest_positive_contributor": largest_symbol,
        "largest_positive_contribution_share": largest_share,
    }


def _side_profiles(rows: Sequence[SlowTrendOutcome]) -> dict[str, object]:
    return {
        side: {
            "base": _profile([row for row in rows if row.side == side], "base_net_bps"),
            "stress": _profile([row for row in rows if row.side == side], "stress_net_bps"),
        }
        for side in ("LONG", "SHORT")
        if any(row.side == side for row in rows)
    }


def _oos_assessment(
    candidate_id: str,
    parts: Mapping[str, Sequence[SlowTrendOutcome]],
    *,
    trials: int,
    global_pbo: Mapping[str, object],
) -> dict[str, object]:
    rows = tuple(parts["oos"])
    base_values = [float(row.base_net_bps) for row in rows if row.base_net_bps is not None]
    stress_values = [float(row.stress_net_bps) for row in rows if row.stress_net_bps is not None]
    base = _profile(rows, "base_net_bps")
    stress = _profile(rows, "stress_net_bps")
    bootstrap = bootstrap_mean_interval(base_values, seed=SEED)
    dsr = deflated_sharpe_ratio(base_values, trials=trials)
    concentration = _concentration(rows)
    share = _number(concentration["largest_positive_contribution_share"])
    pbo = _number(global_pbo.get("pbo"))
    pbo = pbo if pbo is not None else 1.0
    base_pf = _number(base["profit_factor"])
    stress_pf = _number(stress["profit_factor"])
    lower = _number(bootstrap.get("lower"))
    dsr_probability = _number(dsr.get("dsr_probability"))
    gates = {
        "oos_sample_at_least_30": len(rows) >= MINIMUM_OOS_SAMPLE,
        "oos_base_expectancy_positive": bool(base_values) and fmean(base_values) > 0,
        "oos_stress_expectancy_positive": bool(stress_values) and fmean(stress_values) > 0,
        "oos_base_profit_factor_at_least_1_15": base_pf is not None and base_pf >= 1.15,
        "oos_stress_profit_factor_at_least_1_05": stress_pf is not None and stress_pf >= 1.05,
        "bootstrap_lower_positive": lower is not None and lower > 0,
        "dsr_at_least_0_95": dsr_probability is not None and dsr_probability >= 0.95,
        "pbo_at_most_0_20": pbo <= 0.20,
        "largest_symbol_share_at_most_0_50": share is not None and share <= 0.50,
    }
    win_rate = _number(base["win_rate"])
    return {
        "candidate_id": candidate_id,
        "base": base,
        "stress": stress,
        "side_profiles": _side_profiles(rows),
        "exit_reasons": dict(Counter(row.exit_reason for row in rows)),
        "median_holding_minutes": median(row.holding_minutes for row in rows) if rows else None,
        "bootstrap_expectancy_95": bootstrap,
        "deflated_sharpe": dsr,
        "global_pbo": dict(global_pbo),
        "symbol_concentration": concentration,
        "robustness_gates": gates,
        "adaptive_historical_robustness_pass": all(gates.values()),
        "user_stretch_win_rate_70_reached": win_rate is not None and win_rate >= 0.70,
        "examples": [asdict(row) for row in rows[:20]],
    }


def build_report(
    data: Mapping[str, Sequence[Kline]],
    dataset_manifest: Sequence[Mapping[str, object]],
    *,
    start_ms: int,
    end_ms: int,
    specs: Sequence[SlowTrendSpec] = PREREGISTERED_CANDIDATES,
) -> dict[str, object]:
    outcomes = research_tournament(data, specs)
    splits = {
        candidate_id: _split(rows, start_ms=start_ms, end_ms=end_ms)
        for candidate_id, rows in outcomes.items()
    }
    development = {
        candidate_id: _development_profile(parts)
        for candidate_id, parts in splits.items()
    }
    finalists = _select_finalists(development, specs)
    development_end_ms = start_ms + int((end_ms - start_ms) * 0.70)
    pbo = probability_of_backtest_overfitting(
        _fold_returns(outcomes, start_ms=start_ms, development_end_ms=development_end_ms)
    )
    oos = {
        candidate_id: _oos_assessment(
            candidate_id,
            splits[candidate_id],
            trials=len(specs),
            global_pbo=pbo,
        )
        for candidate_id in finalists
    }
    historical_pass = tuple(
        candidate_id
        for candidate_id, assessment in oos.items()
        if bool(assessment["adaptive_historical_robustness_pass"])
    )
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    ranked_ids = sorted(
        (candidate_id for candidate_id, profile in development.items() if _rankable(profile)),
        key=lambda candidate_id: (*_rank_key(development[candidate_id]), candidate_id),
        reverse=True,
    )
    unranked_ids = sorted(
        candidate_id
        for candidate_id, profile in development.items()
        if not _rankable(profile)
    )
    candidates = [asdict(spec) for spec in specs]
    datasets = list(dataset_manifest)
    return {
        "schema_version": 1,
        "status": (
            "ADAPTIVE_HISTORICAL_PASS_FORWARD_REQUIRED"
            if historical_pass
            else "NOT_PROVEN"
        ),
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "profitability_claim": "NOT_PROVEN",
        "real_money_readiness": "NOT_READY",
        "generated_ts_ms": time.time_ns() // 1_000_000,
        "code_hash": git_commit(),
        "adaptive_boundary": {
            "prior_results_were_inspected": True,
            "independent_future_oos": False,
            "reason": (
                "Wave116L의 15분·30분 손실과 방향별 결과를 본 뒤 느린 시간축과 "
                "시장 레짐을 추가했으므로 이 역사구간은 후보 제거용 적응 진단입니다."
            ),
        },
        "source": {
            "venue": "BINANCE_USDM",
            "public_only": True,
            "base_interval": "5m",
            "research_intervals": ["1h", "4h", "4h market context"],
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "dataset_hash": hashlib.sha256(
                json.dumps(datasets, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "datasets": datasets,
        },
        "preregistration": {
            "hypothesis_id": "HYP-117-SLOW-REGIME-TREND-TOURNAMENT",
            "path": "docs/research/HYP-117-slow-regime-trend-tournament.md",
            "candidate_count": len(specs),
            "family_count": len({spec.family for spec in specs}),
            "candidates": candidates,
            "candidate_fingerprint": hashlib.sha256(
                json.dumps(candidates, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "base_cost_bps": BASE_COST_BPS,
            "stress_cost_bps": STRESS_COST_BPS,
            "next_bar_open_entry": True,
            "same_bar_stop_before_target": True,
            "fixed_maximum_hold": False,
            "censored_open_positions_are_not_scored": True,
            "tp1_fraction": TP1_FRACTION,
            "maximum_concurrent_positions": MAXIMUM_CONCURRENT_POSITIONS,
            "maximum_daily_entries": MAXIMUM_DAILY_ENTRIES,
            "split": "chronological 50% train / 20% validation / 30% diagnostic OOS",
            "embargo_ms": EMBARGO_MS,
            "thresholds_lowered_after_results": False,
        },
        "development_profiles": development,
        "ranking_contract": {
            "minimum_development_closed_trades": MINIMUM_DEVELOPMENT_SAMPLE,
            "minimum_validation_closed_trades": MINIMUM_VALIDATION_SAMPLE,
            "minimum_oos_closed_trades": MINIMUM_OOS_SAMPLE,
            "sparse_candidates_are_not_ranked": True,
        },
        "development_ranking_top_10": [
            {
                "rank": index + 1,
                "candidate_id": candidate_id,
                "family": spec_by_id[candidate_id].family,
                "side_policy": spec_by_id[candidate_id].side_policy,
                "eligible": _eligible(development[candidate_id]),
                "validation_stress_expectancy_bps": (
                    development[candidate_id]["validation_stress"]["expectancy_bps"]
                ),
                "validation_stress_profit_factor": (
                    development[candidate_id]["validation_stress"]["profit_factor"]
                ),
                "development_stress_expectancy_bps": (
                    development[candidate_id]["stress"]["expectancy_bps"]
                ),
            }
            for index, candidate_id in enumerate(ranked_ids[:10])
        ],
        "unranked_insufficient_sample": unranked_ids,
        "selected_on_train_validation": list(finalists),
        "selection_bias": {
            "candidate_trials": len(specs),
            "pbo": pbo,
        },
        "diagnostic_oos": oos,
        "adaptive_historical_robustness_pass_candidates": list(historical_pass),
        "promotion_assessment": {
            "status": "NOT_PROVEN",
            "registry_changes": [],
            "shadow_implementation_candidates": list(historical_pass),
            "future_live_public_forward_samples_required": True,
            "actual_bid_ask_depth_forward_required": True,
            "minimum_natural_base_stress_opportunities_per_strategy": 30,
        },
        "limitations": [
            "역사 kline에는 당시 실행가능 bid·ask 깊이가 없어 BASE·STRESS 고정비용을 차감했습니다.",
            "이 결과는 앞선 데이터를 본 뒤 설계한 적응 진단이며 독립 미래 OOS가 아닙니다.",
            "70% 승률은 진단값일 뿐 비용후 기대값·강건성 gate를 대신하지 않습니다.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="UTC 시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="UTC 종료일 YYYY-MM-DD, 미포함")
    parser.add_argument("--symbol", action="append")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/public-trend-klines-v1"),
    )
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_ms = _parse_date(args.start)
    end_ms = _parse_date(args.end)
    if end_ms - start_ms < MINIMUM_RESEARCH_DAYS * 86_400_000:
        raise ValueError(f"느린 추세 토너먼트 기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다.")
    data, dataset_manifest = load_segmented_public_klines(
        tuple(args.symbol or DEFAULT_SYMBOLS),
        start_ms=start_ms,
        end_ms=end_ms,
        cache_dir=args.cache_dir,
    )
    report = build_report(
        data,
        dataset_manifest,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output_json is None:
        print(rendered, end="")
        return
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
