# 24개 중단기 추세 가설을 같은 공개 완성봉과 비용조건에서 PAPER 토너먼트로 비교한다.

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean

from backend.app.build_identity import git_commit
from backend.app.research import (
    bootstrap_mean_interval,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)
from scripts.research_public_intraday_trend_candidates import IntradayBar, aggregate_bars
from scripts.research_public_trend_candidates import (
    BAR_INTERVAL_MS,
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
MINIMUM_OOS_SAMPLE = 40
MAXIMUM_CONCURRENT_POSITIONS = 2
MAXIMUM_DAILY_ENTRIES = 4
MAXIMUM_FINALISTS = 3
EMBARGO_MS = 48 * 3_600_000
TP1_FRACTION = 0.4
SEED = 20260830
CACHE_FILE_RE = re.compile(
    r"^(?P<symbol>.+)-5m-(?P<start>\d+)-(?P<end>\d+)\.json$"
)


@dataclass(frozen=True, slots=True)
class TournamentSpec:
    candidate_id: str
    family: str
    interval_minutes: int
    setup_kind: str
    lookback: int
    momentum_24h_minimum: float
    fast_momentum_minimum: float
    adx_minimum: float
    relative_volume_minimum: float
    pullback_band_atr: float
    retest_band_atr: float
    compression_atr_ratio_maximum: float | None
    stop_buffer_atr: float
    tp1_r: float
    tp2_r: float
    cooldown_hours: int

    @property
    def cooldown_ms(self) -> int:
        return self.cooldown_hours * 3_600_000


def _spec(
    candidate_id: str,
    family: str,
    interval_minutes: int,
    setup_kind: str,
    *,
    lookback: int,
    momentum: float,
    fast_momentum: float,
    adx: float,
    relative_volume: float,
    pullback_band: float,
    retest_band: float,
    compression_ratio: float | None,
    stop_buffer: float,
    tp1_r: float,
    tp2_r: float,
    cooldown_hours: int,
) -> TournamentSpec:
    return TournamentSpec(
        candidate_id=candidate_id,
        family=family,
        interval_minutes=interval_minutes,
        setup_kind=setup_kind,
        lookback=lookback,
        momentum_24h_minimum=momentum,
        fast_momentum_minimum=fast_momentum,
        adx_minimum=adx,
        relative_volume_minimum=relative_volume,
        pullback_band_atr=pullback_band,
        retest_band_atr=retest_band,
        compression_atr_ratio_maximum=compression_ratio,
        stop_buffer_atr=stop_buffer,
        tp1_r=tp1_r,
        tp2_r=tp2_r,
        cooldown_hours=cooldown_hours,
    )


PREREGISTERED_CANDIDATES = (
    _spec(
        "T116L_PULLBACK_15M_BALANCED",
        "EMA_PULLBACK_RECLAIM",
        15,
        "PULLBACK_RECLAIM",
        lookback=20,
        momentum=0.008,
        fast_momentum=0.002,
        adx=16,
        relative_volume=0.75,
        pullback_band=0.30,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.10,
        tp1_r=1.0,
        tp2_r=2.4,
        cooldown_hours=4,
    ),
    _spec(
        "T116L_PULLBACK_15M_SELECTIVE",
        "EMA_PULLBACK_RECLAIM",
        15,
        "PULLBACK_RECLAIM",
        lookback=32,
        momentum=0.015,
        fast_momentum=0.004,
        adx=22,
        relative_volume=1.00,
        pullback_band=0.18,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.12,
        tp1_r=1.4,
        tp2_r=3.0,
        cooldown_hours=6,
    ),
    _spec(
        "T116L_PULLBACK_30M_BALANCED",
        "EMA_PULLBACK_RECLAIM",
        30,
        "PULLBACK_RECLAIM",
        lookback=16,
        momentum=0.008,
        fast_momentum=0.002,
        adx=16,
        relative_volume=0.75,
        pullback_band=0.30,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.12,
        tp1_r=1.0,
        tp2_r=2.4,
        cooldown_hours=6,
    ),
    _spec(
        "T116L_PULLBACK_30M_SELECTIVE",
        "EMA_PULLBACK_RECLAIM",
        30,
        "PULLBACK_RECLAIM",
        lookback=24,
        momentum=0.015,
        fast_momentum=0.004,
        adx=22,
        relative_volume=1.00,
        pullback_band=0.18,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.15,
        tp1_r=1.4,
        tp2_r=3.0,
        cooldown_hours=8,
    ),
    _spec(
        "T116L_RETEST_15M_BALANCED",
        "DONCHIAN_BREAKOUT_RETEST",
        15,
        "BREAKOUT_RETEST",
        lookback=20,
        momentum=0.010,
        fast_momentum=0.002,
        adx=18,
        relative_volume=0.90,
        pullback_band=0.0,
        retest_band=0.45,
        compression_ratio=None,
        stop_buffer=0.35,
        tp1_r=1.1,
        tp2_r=2.6,
        cooldown_hours=5,
    ),
    _spec(
        "T116L_RETEST_15M_SELECTIVE",
        "DONCHIAN_BREAKOUT_RETEST",
        15,
        "BREAKOUT_RETEST",
        lookback=40,
        momentum=0.018,
        fast_momentum=0.005,
        adx=24,
        relative_volume=1.20,
        pullback_band=0.0,
        retest_band=0.30,
        compression_ratio=None,
        stop_buffer=0.25,
        tp1_r=1.4,
        tp2_r=3.2,
        cooldown_hours=8,
    ),
    _spec(
        "T116L_RETEST_30M_BALANCED",
        "DONCHIAN_BREAKOUT_RETEST",
        30,
        "BREAKOUT_RETEST",
        lookback=16,
        momentum=0.010,
        fast_momentum=0.002,
        adx=18,
        relative_volume=0.90,
        pullback_band=0.0,
        retest_band=0.45,
        compression_ratio=None,
        stop_buffer=0.35,
        tp1_r=1.1,
        tp2_r=2.6,
        cooldown_hours=8,
    ),
    _spec(
        "T116L_RETEST_30M_SELECTIVE",
        "DONCHIAN_BREAKOUT_RETEST",
        30,
        "BREAKOUT_RETEST",
        lookback=32,
        momentum=0.018,
        fast_momentum=0.005,
        adx=24,
        relative_volume=1.20,
        pullback_band=0.0,
        retest_band=0.30,
        compression_ratio=None,
        stop_buffer=0.25,
        tp1_r=1.4,
        tp2_r=3.2,
        cooldown_hours=10,
    ),
    _spec(
        "T116L_COMPRESSION_15M_BALANCED",
        "VOLATILITY_COMPRESSION_RETEST",
        15,
        "COMPRESSION_RETEST",
        lookback=20,
        momentum=0.006,
        fast_momentum=0.002,
        adx=15,
        relative_volume=1.00,
        pullback_band=0.0,
        retest_band=0.45,
        compression_ratio=0.75,
        stop_buffer=0.30,
        tp1_r=1.0,
        tp2_r=2.5,
        cooldown_hours=5,
    ),
    _spec(
        "T116L_COMPRESSION_15M_SELECTIVE",
        "VOLATILITY_COMPRESSION_RETEST",
        15,
        "COMPRESSION_RETEST",
        lookback=32,
        momentum=0.012,
        fast_momentum=0.004,
        adx=20,
        relative_volume=1.30,
        pullback_band=0.0,
        retest_band=0.30,
        compression_ratio=0.60,
        stop_buffer=0.25,
        tp1_r=1.3,
        tp2_r=3.0,
        cooldown_hours=7,
    ),
    _spec(
        "T116L_COMPRESSION_30M_BALANCED",
        "VOLATILITY_COMPRESSION_RETEST",
        30,
        "COMPRESSION_RETEST",
        lookback=16,
        momentum=0.006,
        fast_momentum=0.002,
        adx=15,
        relative_volume=1.00,
        pullback_band=0.0,
        retest_band=0.45,
        compression_ratio=0.75,
        stop_buffer=0.30,
        tp1_r=1.0,
        tp2_r=2.5,
        cooldown_hours=8,
    ),
    _spec(
        "T116L_COMPRESSION_30M_SELECTIVE",
        "VOLATILITY_COMPRESSION_RETEST",
        30,
        "COMPRESSION_RETEST",
        lookback=24,
        momentum=0.012,
        fast_momentum=0.004,
        adx=20,
        relative_volume=1.30,
        pullback_band=0.0,
        retest_band=0.30,
        compression_ratio=0.60,
        stop_buffer=0.25,
        tp1_r=1.3,
        tp2_r=3.0,
        cooldown_hours=10,
    ),
    _spec(
        "T116L_MULTISPEED_15M_BALANCED",
        "MULTISPEED_TREND_RECLAIM",
        15,
        "MULTISPEED_RECLAIM",
        lookback=20,
        momentum=0.006,
        fast_momentum=0.003,
        adx=16,
        relative_volume=0.70,
        pullback_band=0.30,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.12,
        tp1_r=1.0,
        tp2_r=2.4,
        cooldown_hours=4,
    ),
    _spec(
        "T116L_MULTISPEED_15M_SELECTIVE",
        "MULTISPEED_TREND_RECLAIM",
        15,
        "MULTISPEED_RECLAIM",
        lookback=32,
        momentum=0.012,
        fast_momentum=0.006,
        adx=22,
        relative_volume=0.95,
        pullback_band=0.18,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.12,
        tp1_r=1.4,
        tp2_r=3.0,
        cooldown_hours=6,
    ),
    _spec(
        "T116L_MULTISPEED_30M_BALANCED",
        "MULTISPEED_TREND_RECLAIM",
        30,
        "MULTISPEED_RECLAIM",
        lookback=16,
        momentum=0.006,
        fast_momentum=0.003,
        adx=16,
        relative_volume=0.70,
        pullback_band=0.30,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.15,
        tp1_r=1.0,
        tp2_r=2.4,
        cooldown_hours=6,
    ),
    _spec(
        "T116L_MULTISPEED_30M_SELECTIVE",
        "MULTISPEED_TREND_RECLAIM",
        30,
        "MULTISPEED_RECLAIM",
        lookback=24,
        momentum=0.012,
        fast_momentum=0.006,
        adx=22,
        relative_volume=0.95,
        pullback_band=0.18,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.15,
        tp1_r=1.4,
        tp2_r=3.0,
        cooldown_hours=8,
    ),
    _spec(
        "T116L_TWO_LEG_15M_BALANCED",
        "TWO_LEG_PULLBACK_REVERSAL",
        15,
        "TWO_LEG_RECLAIM",
        lookback=20,
        momentum=0.008,
        fast_momentum=0.002,
        adx=16,
        relative_volume=0.80,
        pullback_band=0.40,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.10,
        tp1_r=1.0,
        tp2_r=2.4,
        cooldown_hours=5,
    ),
    _spec(
        "T116L_TWO_LEG_15M_SELECTIVE",
        "TWO_LEG_PULLBACK_REVERSAL",
        15,
        "TWO_LEG_RECLAIM",
        lookback=32,
        momentum=0.015,
        fast_momentum=0.005,
        adx=22,
        relative_volume=1.05,
        pullback_band=0.25,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.12,
        tp1_r=1.3,
        tp2_r=3.0,
        cooldown_hours=7,
    ),
    _spec(
        "T116L_TWO_LEG_30M_BALANCED",
        "TWO_LEG_PULLBACK_REVERSAL",
        30,
        "TWO_LEG_RECLAIM",
        lookback=16,
        momentum=0.008,
        fast_momentum=0.002,
        adx=16,
        relative_volume=0.80,
        pullback_band=0.40,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.12,
        tp1_r=1.0,
        tp2_r=2.4,
        cooldown_hours=7,
    ),
    _spec(
        "T116L_TWO_LEG_30M_SELECTIVE",
        "TWO_LEG_PULLBACK_REVERSAL",
        30,
        "TWO_LEG_RECLAIM",
        lookback=24,
        momentum=0.015,
        fast_momentum=0.005,
        adx=22,
        relative_volume=1.05,
        pullback_band=0.25,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.15,
        tp1_r=1.3,
        tp2_r=3.0,
        cooldown_hours=9,
    ),
    _spec(
        "T116L_INSIDE_15M_BALANCED",
        "INSIDE_BAR_TREND_EXPANSION",
        15,
        "INSIDE_BAR_EXPANSION",
        lookback=20,
        momentum=0.008,
        fast_momentum=0.002,
        adx=16,
        relative_volume=0.90,
        pullback_band=0.0,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.10,
        tp1_r=1.0,
        tp2_r=2.4,
        cooldown_hours=4,
    ),
    _spec(
        "T116L_INSIDE_15M_SELECTIVE",
        "INSIDE_BAR_TREND_EXPANSION",
        15,
        "INSIDE_BAR_EXPANSION",
        lookback=32,
        momentum=0.015,
        fast_momentum=0.005,
        adx=22,
        relative_volume=1.20,
        pullback_band=0.0,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.12,
        tp1_r=1.3,
        tp2_r=3.0,
        cooldown_hours=6,
    ),
    _spec(
        "T116L_INSIDE_30M_BALANCED",
        "INSIDE_BAR_TREND_EXPANSION",
        30,
        "INSIDE_BAR_EXPANSION",
        lookback=16,
        momentum=0.008,
        fast_momentum=0.002,
        adx=16,
        relative_volume=0.90,
        pullback_band=0.0,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.12,
        tp1_r=1.0,
        tp2_r=2.4,
        cooldown_hours=6,
    ),
    _spec(
        "T116L_INSIDE_30M_SELECTIVE",
        "INSIDE_BAR_TREND_EXPANSION",
        30,
        "INSIDE_BAR_EXPANSION",
        lookback=24,
        momentum=0.015,
        fast_momentum=0.005,
        adx=22,
        relative_volume=1.20,
        pullback_band=0.0,
        retest_band=0.0,
        compression_ratio=None,
        stop_buffer=0.15,
        tp1_r=1.3,
        tp2_r=3.0,
        cooldown_hours=8,
    ),
)


@dataclass(frozen=True, slots=True)
class TournamentFeatures:
    ema20: float
    ema80: float
    ema20_slope: float
    atr: float
    atr_ratio: float
    adx: float
    relative_volume: float
    momentum_24h: float
    momentum_fast: float


@dataclass(frozen=True, slots=True)
class TournamentOutcome:
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
    censored: bool


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def load_segmented_public_klines(
    symbols: Sequence[str],
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[dict[str, tuple[Kline, ...]], tuple[dict[str, object], ...]]:
    """기존 공개 cache 조각을 정확히 이어 붙이고 누락·중복 봉은 거부한다."""

    data: dict[str, tuple[Kline, ...]] = {}
    manifest: list[dict[str, object]] = []
    for symbol in symbols:
        segments: list[tuple[int, int, Path]] = []
        for path in cache_dir.glob(f"{symbol}-5m-*.json"):
            match = CACHE_FILE_RE.match(path.name)
            if match is None or match.group("symbol") != symbol:
                continue
            segment_start = int(match.group("start"))
            segment_end = int(match.group("end"))
            if segment_end <= start_ms or segment_start >= end_ms:
                continue
            segments.append((segment_start, segment_end, path))
        if not segments:
            raise FileNotFoundError(f"{symbol} 공개 5분 cache 조각이 없습니다.")
        rows_by_ts: dict[int, Kline] = {}
        segment_evidence: list[dict[str, object]] = []
        for segment_start, segment_end, path in sorted(segments):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError(f"{path} 공개 cache 형식이 배열이 아닙니다.")
            for raw in payload:
                row = Kline(**raw)
                if start_ms <= row.open_ts_ms and row.open_ts_ms + BAR_INTERVAL_MS <= end_ms:
                    rows_by_ts[row.open_ts_ms] = row
            segment_evidence.append(
                {
                    "path": path.name,
                    "declared_start_ts_ms": segment_start,
                    "declared_end_ts_ms": segment_end,
                    "sha256": _file_sha256(path),
                }
            )
        rows = tuple(rows_by_ts[key] for key in sorted(rows_by_ts))
        if not rows or rows[0].open_ts_ms != start_ms:
            raise ValueError(f"{symbol} 공개 cache 시작 봉이 요청범위와 다릅니다.")
        if rows[-1].open_ts_ms + BAR_INTERVAL_MS != end_ms:
            raise ValueError(f"{symbol} 공개 cache 종료 봉이 요청범위와 다릅니다.")
        if any(
            current.open_ts_ms - previous.open_ts_ms != BAR_INTERVAL_MS
            for previous, current in zip(rows, rows[1:], strict=False)
        ):
            raise ValueError(f"{symbol} 공개 cache에 5분 봉 gap이 있습니다.")
        data[symbol] = rows
        manifest.append(
            {
                "symbol": symbol,
                "interval": "5m",
                "start_ts_ms": rows[0].open_ts_ms,
                "end_ts_ms": rows[-1].close_ts_ms,
                "bar_count": len(rows),
                "segments": segment_evidence,
            }
        )
    return data, tuple(manifest)


def _features(rows: Sequence[IntradayBar]) -> tuple[TournamentFeatures | None, ...]:
    closes = [row.close for row in rows]
    volumes = [row.volume for row in rows]
    ema20 = _ema(closes, 20)
    ema80 = _ema(closes, 80)
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
    atr14 = _rolling_mean(true_ranges, 14)
    atr56 = _rolling_mean(true_ranges, 56)
    plus_mean = _rolling_mean(plus_dm, 14)
    minus_mean = _rolling_mean(minus_dm, 14)
    dx: list[float] = []
    for index in range(len(rows)):
        if math.isnan(atr14[index]) or atr14[index] <= 0:
            dx.append(0.0)
            continue
        plus_di = 100 * plus_mean[index] / atr14[index]
        minus_di = 100 * minus_mean[index] / atr14[index]
        denominator = plus_di + minus_di
        dx.append(100 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
    adx = _rolling_mean(dx, 14)
    volume_mean = _rolling_mean(volumes, 20)
    bars_per_day = 24 * 60 // rows[0].interval_minutes
    fast_bars = max(2, 4 * 60 // rows[0].interval_minutes)
    output: list[TournamentFeatures | None] = []
    for index, row in enumerate(rows):
        if (
            index < max(100, bars_per_day)
            or math.isnan(atr14[index])
            or math.isnan(atr56[index])
            or math.isnan(adx[index])
        ):
            output.append(None)
            continue
        prior_volume = volume_mean[index - 1]
        output.append(
            TournamentFeatures(
                ema20=ema20[index],
                ema80=ema80[index],
                ema20_slope=ema20[index] - ema20[index - 4],
                atr=atr14[index],
                atr_ratio=atr14[index] / atr56[index] if atr56[index] > 0 else math.inf,
                adx=adx[index],
                relative_volume=row.volume / prior_volume if prior_volume > 0 else 0.0,
                momentum_24h=row.close / rows[index - bars_per_day].close - 1,
                momentum_fast=row.close / rows[index - fast_bars].close - 1,
            )
        )
    return tuple(output)


def _hourly_direction(rows: Sequence[IntradayBar]) -> tuple[int, ...]:
    closes = [row.close for row in rows]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    output: list[int] = []
    for index, row in enumerate(rows):
        if index < 50:
            output.append(0)
        elif (
            ema20[index] > ema50[index]
            and ema20[index] >= ema20[index - 2]
            and row.close >= ema20[index]
        ):
            output.append(1)
        elif (
            ema20[index] < ema50[index]
            and ema20[index] <= ema20[index - 2]
            and row.close <= ema20[index]
        ):
            output.append(-1)
        else:
            output.append(0)
    return tuple(output)


def _base_direction(
    row: IntradayBar,
    features: TournamentFeatures,
    spec: TournamentSpec,
) -> int:
    if (
        features.ema20 > features.ema80
        and features.ema20_slope > 0
        and row.close >= features.ema20
        and features.momentum_24h >= spec.momentum_24h_minimum
        and features.momentum_fast >= spec.fast_momentum_minimum
    ):
        return 1
    if (
        features.ema20 < features.ema80
        and features.ema20_slope < 0
        and row.close <= features.ema20
        and features.momentum_24h <= -spec.momentum_24h_minimum
        and features.momentum_fast <= -spec.fast_momentum_minimum
    ):
        return -1
    return 0


def _breakout_retest(
    rows: Sequence[IntradayBar],
    index: int,
    direction: int,
    spec: TournamentSpec,
    atr: float,
) -> tuple[bool, float | None]:
    breakout = rows[index - 1]
    retest = rows[index]
    history = rows[index - spec.lookback - 1 : index - 1]
    if len(history) != spec.lookback:
        return False, None
    level = max(row.high for row in history) if direction > 0 else min(row.low for row in history)
    band = atr * spec.retest_band_atr
    if direction > 0:
        ready = (
            breakout.close > level
            and level - band <= retest.low <= level + band
            and retest.close > level
            and retest.close >= retest.open
        )
        return ready, min(retest.low, level - atr * spec.stop_buffer_atr)
    ready = (
        breakout.close < level
        and level - band <= retest.high <= level + band
        and retest.close < level
        and retest.close <= retest.open
    )
    return ready, max(retest.high, level + atr * spec.stop_buffer_atr)


def _setup(
    rows: Sequence[IntradayBar],
    feature_rows: Sequence[TournamentFeatures | None],
    index: int,
    direction: int,
    spec: TournamentSpec,
) -> tuple[bool, float | None]:
    current = rows[index]
    previous = rows[index - 1]
    current_features = feature_rows[index]
    previous_features = feature_rows[index - 1]
    if current_features is None or previous_features is None:
        return False, None
    atr = current_features.atr
    if spec.setup_kind in {"BREAKOUT_RETEST", "COMPRESSION_RETEST"}:
        if (
            spec.setup_kind == "COMPRESSION_RETEST"
            and (
                spec.compression_atr_ratio_maximum is None
                or previous_features.atr_ratio > spec.compression_atr_ratio_maximum
            )
        ):
            return False, None
        return _breakout_retest(rows, index, direction, spec, atr)
    if spec.setup_kind in {"PULLBACK_RECLAIM", "MULTISPEED_RECLAIM"}:
        if direction > 0:
            ready = (
                previous.low <= previous_features.ema20 + atr * spec.pullback_band_atr
                and previous.close >= previous_features.ema80
                and current.close > current_features.ema20
                and current.close > previous.high
                and current.close > current.open
            )
            stop = min(previous.low, current.low) - atr * spec.stop_buffer_atr
        else:
            ready = (
                previous.high >= previous_features.ema20 - atr * spec.pullback_band_atr
                and previous.close <= previous_features.ema80
                and current.close < current_features.ema20
                and current.close < previous.low
                and current.close < current.open
            )
            stop = max(previous.high, current.high) + atr * spec.stop_buffer_atr
        if spec.setup_kind == "MULTISPEED_RECLAIM":
            fast_aligned = current_features.momentum_fast * direction >= max(
                spec.fast_momentum_minimum,
                abs(previous_features.momentum_fast),
            )
            ready = ready and fast_aligned
        return ready, stop
    if spec.setup_kind == "TWO_LEG_RECLAIM":
        first = rows[index - 2]
        second = rows[index - 1]
        if direction > 0:
            ready = (
                first.close < first.open
                and second.low <= first.low
                and second.low <= current_features.ema20 + atr * spec.pullback_band_atr
                and current.close > max(first.high, second.high)
                and current.close > current.open
            )
            return ready, min(first.low, second.low, current.low) - atr * spec.stop_buffer_atr
        ready = (
            first.close > first.open
            and second.high >= first.high
            and second.high >= current_features.ema20 - atr * spec.pullback_band_atr
            and current.close < min(first.low, second.low)
            and current.close < current.open
        )
        return ready, max(first.high, second.high, current.high) + atr * spec.stop_buffer_atr
    if spec.setup_kind != "INSIDE_BAR_EXPANSION":
        raise ValueError(f"알 수 없는 추세 토너먼트 setup입니다: {spec.setup_kind}")
    parent = rows[index - 2]
    inside = rows[index - 1]
    is_inside = inside.high < parent.high and inside.low > parent.low
    if direction > 0:
        ready = is_inside and current.close > parent.high and current.close > current.open
        return ready, inside.low - atr * spec.stop_buffer_atr
    ready = is_inside and current.close < parent.low and current.close < current.open
    return ready, inside.high + atr * spec.stop_buffer_atr


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
    spec: TournamentSpec,
) -> TournamentOutcome | None:
    entry_index = index + 1
    if entry_index >= len(rows):
        return None
    entry = rows[entry_index].open
    risk = (entry - structural_stop) * direction
    risk_atr = risk / signal_atr if signal_atr > 0 else math.inf
    if risk <= 0 or not 0.65 <= risk_atr <= 3.0:
        return None
    side = "LONG" if direction > 0 else "SHORT"
    current_stop = structural_stop
    tp1 = entry + direction * risk * spec.tp1_r
    tp2 = entry + direction * risk * spec.tp2_r
    realized = 0.0
    remaining = 1.0
    tp1_hit_ts_ms: int | None = None
    tp2_hit_ts_ms: int | None = None
    for cursor in range(entry_index, len(rows)):
        bar = rows[cursor]
        stop_hit = bar.low <= current_stop if direction > 0 else bar.high >= current_stop
        tp1_hit = bar.high >= tp1 if direction > 0 else bar.low <= tp1
        tp2_hit = bar.high >= tp2 if direction > 0 else bar.low <= tp2
        if stop_hit:
            gross_bps = realized + remaining * _return_bps(side, entry, current_stop)
            return TournamentOutcome(
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
                tp2_hit_ts_ms=tp2_hit_ts_ms,
                entry=entry,
                stop=structural_stop,
                take_profit_1=tp1,
                take_profit_2=tp2,
                gross_bps=gross_bps,
                base_net_bps=gross_bps - BASE_COST_BPS,
                stress_net_bps=gross_bps - STRESS_COST_BPS,
                score=score,
                censored=False,
            )
        if tp2_hit:
            if tp1_hit_ts_ms is None:
                tp1_hit_ts_ms = bar.close_ts_ms
                realized = TP1_FRACTION * _return_bps(side, entry, tp1)
                remaining = 1 - TP1_FRACTION
            tp2_hit_ts_ms = bar.close_ts_ms
            gross_bps = realized + remaining * _return_bps(side, entry, tp2)
            return TournamentOutcome(
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
                tp2_hit_ts_ms=tp2_hit_ts_ms,
                entry=entry,
                stop=structural_stop,
                take_profit_1=tp1,
                take_profit_2=tp2,
                gross_bps=gross_bps,
                base_net_bps=gross_bps - BASE_COST_BPS,
                stress_net_bps=gross_bps - STRESS_COST_BPS,
                score=score,
                censored=False,
            )
        if tp1_hit and tp1_hit_ts_ms is None:
            tp1_hit_ts_ms = bar.close_ts_ms
            realized = TP1_FRACTION * _return_bps(side, entry, tp1)
            remaining = 1 - TP1_FRACTION
            current_stop = entry + direction * entry * STRESS_COST_BPS / 10_000
    last = rows[-1]
    return TournamentOutcome(
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
        censored=True,
    )


def _symbol_outcomes(
    rows: Sequence[IntradayBar],
    feature_rows: Sequence[TournamentFeatures | None],
    hourly_rows: Sequence[IntradayBar],
    hourly_directions: Sequence[int],
    spec: TournamentSpec,
) -> list[TournamentOutcome]:
    hourly_close_times = [row.close_ts_ms for row in hourly_rows]
    output: list[TournamentOutcome] = []
    cooldown_until = 0
    start = max(102, spec.lookback + 2)
    for index in range(start, len(rows) - 1):
        features = feature_rows[index]
        if features is None or rows[index].open_ts_ms < cooldown_until:
            continue
        if features.adx < spec.adx_minimum:
            continue
        volume_features = feature_rows[index - 1] if spec.setup_kind in {
            "BREAKOUT_RETEST",
            "COMPRESSION_RETEST",
        } else features
        if (
            volume_features is None
            or volume_features.relative_volume < spec.relative_volume_minimum
        ):
            continue
        direction = _base_direction(rows[index], features, spec)
        if direction == 0:
            continue
        hourly_index = bisect.bisect_right(hourly_close_times, rows[index].close_ts_ms) - 1
        if hourly_index < 0 or hourly_directions[hourly_index] != direction:
            continue
        ready, structural_stop = _setup(rows, feature_rows, index, direction, spec)
        if not ready or structural_stop is None:
            continue
        trend_distance = abs(features.ema20 - features.ema80) / rows[index].close
        score = (
            abs(features.momentum_24h) * 100
            + abs(features.momentum_fast) * 200
            + features.adx / 100
            + volume_features.relative_volume
            + trend_distance * 1_000
        )
        outcome = _simulate(
            rows,
            index=index,
            direction=direction,
            structural_stop=structural_stop,
            signal_atr=features.atr,
            score=score,
            spec=spec,
        )
        if outcome is None:
            continue
        output.append(outcome)
        cooldown_until = outcome.exit_ts_ms + spec.cooldown_ms
    return output


def apply_portfolio_limits(
    outcomes: Iterable[TournamentOutcome],
) -> tuple[TournamentOutcome, ...]:
    selected: list[TournamentOutcome] = []
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
    specs: Sequence[TournamentSpec] = PREREGISTERED_CANDIDATES,
) -> dict[str, tuple[TournamentOutcome, ...]]:
    raw: dict[str, list[TournamentOutcome]] = {spec.candidate_id: [] for spec in specs}
    for _symbol, klines in sorted(data.items()):
        by_interval = {
            interval: aggregate_bars(klines, interval)
            for interval in {spec.interval_minutes for spec in specs}
        }
        feature_cache = {interval: _features(rows) for interval, rows in by_interval.items()}
        hourly_rows = aggregate_bars(klines, 60)
        hourly_directions = _hourly_direction(hourly_rows)
        for spec in specs:
            raw[spec.candidate_id].extend(
                _symbol_outcomes(
                    by_interval[spec.interval_minutes],
                    feature_cache[spec.interval_minutes],
                    hourly_rows,
                    hourly_directions,
                    spec,
                )
            )
    return {candidate_id: apply_portfolio_limits(rows) for candidate_id, rows in raw.items()}


def _split(
    rows: Sequence[TournamentOutcome],
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, tuple[TournamentOutcome, ...]]:
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


def _profile(rows: Sequence[TournamentOutcome], field: str) -> dict[str, object]:
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
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    gross_loss = abs(sum(losses))
    average_win = fmean(wins) if wins else None
    average_loss = abs(fmean(losses)) if losses else None
    payoff_ratio = (
        average_win / average_loss
        if average_win is not None and average_loss is not None and average_loss > 0
        else None
    )
    return {
        "sample_size": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(values),
        "expectancy_bps": fmean(values),
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "payoff_ratio": payoff_ratio,
        "net_sum_bps": sum(values),
        "maximum_drawdown_bps": drawdown,
        "sample_status": "ENOUGH" if len(values) >= MINIMUM_OOS_SAMPLE else "INSUFFICIENT",
    }


def _development_profile(
    parts: Mapping[str, Sequence[TournamentOutcome]],
) -> dict[str, object]:
    development = (*parts["train"], *parts["validation"])
    return {
        "base": _profile(development, "base_net_bps"),
        "stress": _profile(development, "stress_net_bps"),
        "validation_base": _profile(parts["validation"], "base_net_bps"),
        "validation_stress": _profile(parts["validation"], "stress_net_bps"),
        "exit_reasons": dict(Counter(row.exit_reason for row in development)),
        "censored_count": len(parts["censored"]),
    }


def _eligible(profile: Mapping[str, object]) -> bool:
    base = profile["base"]
    stress = profile["stress"]
    validation_stress = profile["validation_stress"]
    assert isinstance(base, Mapping)
    assert isinstance(stress, Mapping)
    assert isinstance(validation_stress, Mapping)
    return (
        int(base["sample_size"]) >= MINIMUM_DEVELOPMENT_SAMPLE
        and int(validation_stress["sample_size"]) >= MINIMUM_VALIDATION_SAMPLE
        and stress["expectancy_bps"] is not None
        and float(stress["expectancy_bps"]) > 0
        and stress["profit_factor"] is not None
        and float(stress["profit_factor"]) > 1
        and validation_stress["expectancy_bps"] is not None
        and float(validation_stress["expectancy_bps"]) > 0
        and validation_stress["profit_factor"] is not None
        and float(validation_stress["profit_factor"]) > 1
    )


def _rankable(profile: Mapping[str, object]) -> bool:
    base = profile["base"]
    validation_stress = profile["validation_stress"]
    assert isinstance(base, Mapping)
    assert isinstance(validation_stress, Mapping)
    return (
        int(base["sample_size"]) >= MINIMUM_DEVELOPMENT_SAMPLE
        and int(validation_stress["sample_size"]) >= MINIMUM_VALIDATION_SAMPLE
    )


def _optional_number(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _nested_value(
    profile: Mapping[str, object],
    section: str,
    key: str,
) -> object:
    nested = profile.get(section)
    return nested.get(key) if isinstance(nested, Mapping) else None


def _rank_key(profile: Mapping[str, object]) -> tuple[float, float, float]:
    validation_stress = profile["validation_stress"]
    stress = profile["stress"]
    assert isinstance(validation_stress, Mapping)
    assert isinstance(stress, Mapping)

    def numeric(value: object) -> float:
        number = _optional_number(value)
        return -math.inf if number is None else number

    return (
        numeric(validation_stress["expectancy_bps"]),
        numeric(validation_stress["profit_factor"]),
        numeric(stress["expectancy_bps"]),
    )


def _select_finalists(
    development: Mapping[str, Mapping[str, object]],
    specs: Sequence[TournamentSpec],
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
    outcomes: Mapping[str, Sequence[TournamentOutcome]],
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


def _concentration(rows: Sequence[TournamentOutcome]) -> dict[str, object]:
    contributions: dict[str, float] = defaultdict(float)
    for row in rows:
        if row.base_net_bps is not None:
            contributions[row.symbol] += row.base_net_bps
    positive_total = sum(value for value in contributions.values() if value > 0)
    largest_symbol = (
        max(contributions, key=lambda symbol: contributions[symbol])
        if contributions
        else None
    )
    largest_share = (
        max(contributions.values()) / positive_total
        if contributions and positive_total > 0
        else None
    )
    return {
        "base_net_bps_by_symbol": dict(sorted(contributions.items())),
        "largest_positive_contributor": largest_symbol,
        "largest_positive_contribution_share": largest_share,
    }


def _oos_assessment(
    candidate_id: str,
    parts: Mapping[str, Sequence[TournamentOutcome]],
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
    concentration_share = _optional_number(
        concentration["largest_positive_contribution_share"]
    )
    pbo_value = _optional_number(global_pbo.get("pbo"))
    pbo_value = pbo_value if pbo_value is not None else 1.0
    base_profit_factor = _optional_number(base["profit_factor"])
    stress_profit_factor = _optional_number(stress["profit_factor"])
    bootstrap_lower = _optional_number(bootstrap.get("lower"))
    dsr_probability = _optional_number(dsr.get("dsr_probability"))
    robustness_gates = {
        "oos_sample_at_least_40": len(rows) >= MINIMUM_OOS_SAMPLE,
        "oos_base_expectancy_positive": bool(base_values) and fmean(base_values) > 0,
        "oos_stress_expectancy_positive": bool(stress_values) and fmean(stress_values) > 0,
        "oos_base_profit_factor_at_least_1_15": (
            base_profit_factor is not None and base_profit_factor >= 1.15
        ),
        "oos_stress_profit_factor_above_1": (
            stress_profit_factor is not None and stress_profit_factor > 1
        ),
        "bootstrap_lower_positive": bootstrap_lower is not None and bootstrap_lower > 0,
        "dsr_at_least_0_95": dsr_probability is not None and dsr_probability >= 0.95,
        "pbo_at_most_0_20": pbo_value <= 0.20,
        "largest_symbol_share_at_most_0_50": (
            concentration_share is not None and concentration_share <= 0.50
        ),
    }
    win_rate = _optional_number(base["win_rate"])
    return {
        "candidate_id": candidate_id,
        "base": base,
        "stress": stress,
        "exit_reasons": dict(Counter(row.exit_reason for row in rows)),
        "bootstrap_expectancy_95": bootstrap,
        "deflated_sharpe": dsr,
        "global_pbo": dict(global_pbo),
        "symbol_concentration": concentration,
        "robustness_gates": robustness_gates,
        "historical_robustness_pass": all(robustness_gates.values()),
        "user_stretch_win_rate_70_reached": win_rate is not None and win_rate >= 0.70,
        "examples": [asdict(row) for row in rows[:20]],
    }


def build_report(
    data: Mapping[str, Sequence[Kline]],
    dataset_manifest: Sequence[Mapping[str, object]],
    *,
    start_ms: int,
    end_ms: int,
    specs: Sequence[TournamentSpec] = PREREGISTERED_CANDIDATES,
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
        if bool(assessment["historical_robustness_pass"])
    )
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    ranked_ids = sorted(
        (
            candidate_id
            for candidate_id, profile in development.items()
            if _rankable(profile)
        ),
        key=lambda candidate_id: (*_rank_key(development[candidate_id]), candidate_id),
        reverse=True,
    )
    unranked_ids = sorted(
        candidate_id
        for candidate_id, profile in development.items()
        if not _rankable(profile)
    )
    candidate_material = [asdict(spec) for spec in specs]
    dataset_material = list(dataset_manifest)
    return {
        "schema_version": 2,
        "status": (
            "HISTORICAL_DIAGNOSTIC_PASS_FORWARD_REQUIRED"
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
        "source": {
            "venue": "BINANCE_USDM",
            "public_only": True,
            "base_interval": "5m",
            "research_intervals": ["15m", "30m", "1h context"],
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "dataset_hash": hashlib.sha256(
                json.dumps(dataset_material, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "datasets": dataset_material,
        },
        "preregistration": {
            "hypothesis_id": "HYP-116L-PARALLEL-INTRADAY-TREND-TOURNAMENT",
            "path": "docs/research/HYP-116L-parallel-trend-tournament.md",
            "candidate_count": len(specs),
            "family_count": len({spec.family for spec in specs}),
            "candidates": candidate_material,
            "candidate_fingerprint": hashlib.sha256(
                json.dumps(candidate_material, sort_keys=True, separators=(",", ":")).encode()
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
            "selection": (
                "development BASE/STRESS + validation STRESS positive, "
                "then up to three distinct families"
            ),
            "thresholds_lowered_after_results": False,
        },
        "development_profiles": development,
        "ranking_contract": {
            "minimum_development_closed_trades": MINIMUM_DEVELOPMENT_SAMPLE,
            "minimum_validation_closed_trades": MINIMUM_VALIDATION_SAMPLE,
            "sparse_candidates_are_not_ranked": True,
        },
        "development_ranking_top_10": [
            {
                "rank": index + 1,
                "candidate_id": candidate_id,
                "family": spec_by_id[candidate_id].family,
                "eligible": _eligible(development[candidate_id]),
                "validation_stress_expectancy_bps": _nested_value(
                    development[candidate_id],
                    "validation_stress",
                    "expectancy_bps",
                ),
                "validation_stress_profit_factor": _nested_value(
                    development[candidate_id],
                    "validation_stress",
                    "profit_factor",
                ),
                "development_stress_expectancy_bps": _nested_value(
                    development[candidate_id],
                    "stress",
                    "expectancy_bps",
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
        "historical_robustness_pass_candidates": list(historical_pass),
        "promotion_assessment": {
            "status": "NOT_PROVEN",
            "registry_changes": [],
            "future_live_public_forward_samples_required": True,
            "actual_bid_ask_depth_forward_required": True,
            "minimum_natural_base_stress_opportunities_per_strategy": 30,
            "reason": (
                "역사 완성봉에는 실제 과거 bid·ask 깊이가 없고 후보 설계 전 미래구간이 아니므로 "
                "통과해도 별도 SHADOW V2와 미래 자연표본이 필요합니다."
            ),
        },
        "limitations": [
            (
                "역사 kline에는 과거 실행가능 bid·ask 깊이가 없어 "
                "고정 BASE/STRESS 비용을 차감했습니다."
            ),
            "이 토너먼트는 후보 선별이며 runtime Registry를 자동 변경하지 않습니다.",
            "70% 승률은 목표 진단값이며 충분한 비용후 독립 표본 전에는 성과로 주장하지 않습니다.",
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
        raise ValueError(f"추세 토너먼트 기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다.")
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
