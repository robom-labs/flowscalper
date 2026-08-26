# Binance 공개 완성 봉으로 저빈도 추세 가설을 시간순 PAPER 연구한다.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean

import httpx

from backend.app.build_identity import git_commit
from backend.app.research import (
    bootstrap_mean_interval,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)

BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
BAR_INTERVAL_MS = 300_000
BASE_COST_BPS = 13.0
STRESS_COST_BPS = 25.0
MINIMUM_OOS_SAMPLE = 60
MAXIMUM_CONCURRENT_POSITIONS = 2
MAXIMUM_DAILY_ENTRIES = 8
SEED = 20260826
DEFAULT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "BCHUSDT",
)


@dataclass(frozen=True, slots=True)
class Kline:
    symbol: str
    open_ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy_volume: float

    @property
    def close_ts_ms(self) -> int:
        return self.open_ts_ms + BAR_INTERVAL_MS - 1


@dataclass(frozen=True, slots=True)
class TrendCandidateSpec:
    candidate_id: str
    family: str
    signal_kind: str
    adx_minimum: float
    relative_volume_minimum: float
    breakout_lookback: int
    pullback_band_atr: float
    momentum_6h_minimum: float
    stop_atr: float
    tp1_r: float
    tp2_r: float
    maximum_holding_bars: int
    cooldown_bars: int


PREREGISTERED_CANDIDATES = (
    TrendCandidateSpec(
        "TREND_PULLBACK_BALANCED_V1",
        "EMA_TREND_PULLBACK",
        "PULLBACK_REACCELERATION",
        20,
        1.0,
        20,
        0.45,
        0.0,
        1.3,
        1.5,
        3.0,
        24,
        6,
    ),
    TrendCandidateSpec(
        "TREND_PULLBACK_STRICT_V1",
        "EMA_TREND_PULLBACK",
        "PULLBACK_REACCELERATION",
        25,
        1.25,
        20,
        0.35,
        0.0,
        1.2,
        1.6,
        3.2,
        30,
        9,
    ),
    TrendCandidateSpec(
        "TREND_BREAKOUT_20_V1",
        "DONCHIAN_TREND_BREAKOUT",
        "DONCHIAN_BREAKOUT",
        20,
        1.3,
        20,
        0.0,
        0.0,
        1.5,
        1.5,
        3.0,
        36,
        12,
    ),
    TrendCandidateSpec(
        "TREND_BREAKOUT_55_V1",
        "DONCHIAN_TREND_BREAKOUT",
        "DONCHIAN_BREAKOUT",
        25,
        1.5,
        55,
        0.0,
        0.0,
        1.5,
        1.8,
        3.6,
        48,
        18,
    ),
    TrendCandidateSpec(
        "DUAL_MOMENTUM_RETEST_V1",
        "DUAL_MOMENTUM_RETEST",
        "MOMENTUM_RETEST",
        18,
        0.9,
        20,
        0.55,
        0.008,
        1.5,
        1.8,
        3.5,
        36,
        12,
    ),
    TrendCandidateSpec(
        "DUAL_MOMENTUM_RETEST_STRICT_V1",
        "DUAL_MOMENTUM_RETEST",
        "MOMENTUM_RETEST",
        22,
        1.15,
        20,
        0.40,
        0.012,
        1.4,
        2.0,
        4.0,
        48,
        18,
    ),
)


@dataclass(frozen=True, slots=True)
class TrendFeatures:
    ema_fast: float
    ema_slow: float
    higher_ema_fast: float
    higher_ema_slow: float
    higher_fast_slope: float
    atr: float
    adx: float
    relative_volume: float
    momentum_6h: float


@dataclass(frozen=True, slots=True)
class TrendSignal:
    candidate_id: str
    symbol: str
    side: str
    signal_ts_ms: int
    entry_ts_ms: int
    score: float
    atr: float
    signal_close: float


@dataclass(frozen=True, slots=True)
class TrendOutcome:
    candidate_id: str
    symbol: str
    side: str
    signal_ts_ms: int
    entry_ts_ms: int
    exit_ts_ms: int
    holding_bars: int
    exit_reason: str
    entry: float
    stop: float
    take_profit_1: float
    take_profit_2: float
    gross_bps: float
    base_net_bps: float
    stress_net_bps: float
    score: float


def _parse_date(value: str) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1_000)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _cache_path(cache_dir: Path, symbol: str, start_ms: int, end_ms: int) -> Path:
    return cache_dir / f"{symbol}-5m-{start_ms}-{end_ms}.json"


def _download_symbol(
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[str, tuple[Kline, ...], Path]:
    cache_path = _cache_path(cache_dir, symbol, start_ms, end_ms)
    if cache_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return symbol, tuple(Kline(**row) for row in payload), cache_path
    rows: list[Kline] = []
    cursor = start_ms
    with httpx.Client(timeout=30, headers={"User-Agent": "ROBOM-FlowScalper-PAPER/0.2"}) as client:
        while cursor < end_ms:
            response: httpx.Response | None = None
            for attempt in range(7):
                response = client.get(
                    BINANCE_FUTURES_KLINES_URL,
                    params={
                        "symbol": symbol,
                        "interval": "5m",
                        "startTime": cursor,
                        "endTime": end_ms - 1,
                        "limit": 1_500,
                    },
                )
                if response.status_code != 429:
                    break
                retry_after = float(response.headers.get("retry-after", "0") or 0)
                time.sleep(max(retry_after, min(30.0, 2.0**attempt)))
            if response is None:
                raise RuntimeError(f"{symbol} 공개 봉 응답을 받지 못했습니다.")
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list) or not page:
                break
            for raw in page:
                open_ts_ms = int(raw[0])
                if open_ts_ms < start_ms or open_ts_ms + BAR_INTERVAL_MS > end_ms:
                    continue
                rows.append(
                    Kline(
                        symbol=symbol,
                        open_ts_ms=open_ts_ms,
                        open=float(raw[1]),
                        high=float(raw[2]),
                        low=float(raw[3]),
                        close=float(raw[4]),
                        volume=float(raw[5]),
                        taker_buy_volume=float(raw[9]),
                    )
                )
            next_cursor = int(page[-1][0]) + BAR_INTERVAL_MS
            if next_cursor <= cursor:
                raise RuntimeError(f"{symbol} 공개 봉 cursor가 전진하지 않았습니다.")
            cursor = next_cursor
            time.sleep(0.35)
    ordered = tuple(
        sorted(
            {row.open_ts_ms: row for row in rows}.values(),
            key=lambda row: row.open_ts_ms,
        )
    )
    if len(ordered) < 1_000:
        raise RuntimeError(f"{symbol} 연구 봉이 부족합니다: {len(ordered)}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps([asdict(row) for row in ordered], separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return symbol, ordered, cache_path


def load_public_klines(
    symbols: Sequence[str],
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[dict[str, tuple[Kline, ...]], tuple[dict[str, object], ...]]:
    with ThreadPoolExecutor(max_workers=min(2, len(symbols))) as executor:
        futures = [
            executor.submit(
                _download_symbol,
                symbol,
                start_ms=start_ms,
                end_ms=end_ms,
                cache_dir=cache_dir,
            )
            for symbol in symbols
        ]
        downloaded = [future.result() for future in futures]
    data = {symbol: rows for symbol, rows, _ in downloaded}
    manifest = tuple(
        {
            "symbol": symbol,
            "interval": "5m",
            "start_ts_ms": rows[0].open_ts_ms,
            "end_ts_ms": rows[-1].close_ts_ms,
            "bar_count": len(rows),
            "checksum": _sha256(path),
            "source": BINANCE_FUTURES_KLINES_URL,
        }
        for symbol, rows, path in sorted(downloaded)
    )
    return data, manifest


def _ema(values: Sequence[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    result: list[float] = []
    current = values[0]
    for value in values:
        current = alpha * value + (1 - alpha) * current
        result.append(current)
    return result


def _rolling_mean(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= period:
            total -= values[index - period]
        if index >= period - 1:
            result[index] = total / period
    return result


def _features(rows: Sequence[Kline]) -> tuple[TrendFeatures | None, ...]:
    closes = [row.close for row in rows]
    volumes = [row.volume for row in rows]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema240 = _ema(closes, 240)
    ema600 = _ema(closes, 600)
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
            dx.append(math.nan)
            continue
        plus_di = 100 * plus_mean[index] / atr[index]
        minus_di = 100 * minus_mean[index] / atr[index]
        denominator = plus_di + minus_di
        dx.append(100 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
    adx = _rolling_mean([0.0 if math.isnan(value) else value for value in dx], 14)
    prior_volume_mean = _rolling_mean(volumes, 20)
    output: list[TrendFeatures | None] = []
    for index, row in enumerate(rows):
        if index < 600 or math.isnan(atr[index]) or math.isnan(adx[index]):
            output.append(None)
            continue
        volume_base = prior_volume_mean[index - 1]
        momentum_6h = row.close / rows[index - 72].close - 1 if index >= 72 else 0.0
        output.append(
            TrendFeatures(
                ema_fast=ema20[index],
                ema_slow=ema50[index],
                higher_ema_fast=ema240[index],
                higher_ema_slow=ema600[index],
                higher_fast_slope=ema240[index] - ema240[index - 12],
                atr=atr[index],
                adx=adx[index],
                relative_volume=row.volume / volume_base if volume_base > 0 else 0.0,
                momentum_6h=momentum_6h,
            )
        )
    return tuple(output)


def _direction(features: TrendFeatures) -> int:
    if (
        features.higher_ema_fast > features.higher_ema_slow
        and features.higher_fast_slope > 0
        and features.ema_fast > features.ema_slow
    ):
        return 1
    if (
        features.higher_ema_fast < features.higher_ema_slow
        and features.higher_fast_slope < 0
        and features.ema_fast < features.ema_slow
    ):
        return -1
    return 0


def _signal_at(
    rows: Sequence[Kline],
    feature_rows: Sequence[TrendFeatures | None],
    index: int,
    spec: TrendCandidateSpec,
) -> TrendSignal | None:
    features = feature_rows[index]
    previous_features = feature_rows[index - 1]
    if features is None or previous_features is None or index + 1 >= len(rows):
        return None
    direction = _direction(features)
    if direction == 0:
        return None
    if features.adx < spec.adx_minimum or features.relative_volume < spec.relative_volume_minimum:
        return None
    current = rows[index]
    previous = rows[index - 1]
    atr = features.atr
    if atr <= 0:
        return None
    aligned = False
    if spec.signal_kind == "PULLBACK_REACCELERATION":
        if direction > 0:
            pulled_back = previous.low <= previous_features.ema_fast + spec.pullback_band_atr * atr
            aligned = pulled_back and current.close > previous.high and current.close > current.open
        else:
            pulled_back = previous.high >= previous_features.ema_fast - spec.pullback_band_atr * atr
            aligned = pulled_back and current.close < previous.low and current.close < current.open
    elif spec.signal_kind == "DONCHIAN_BREAKOUT":
        history = rows[index - spec.breakout_lookback : index]
        aligned = (
            current.close > max(row.high for row in history)
            if direction > 0
            else current.close < min(row.low for row in history)
        )
    elif spec.signal_kind == "MOMENTUM_RETEST":
        momentum_aligned = features.momentum_6h * direction >= spec.momentum_6h_minimum
        if direction > 0:
            retest = previous.low <= previous_features.ema_fast + spec.pullback_band_atr * atr
            aligned = momentum_aligned and retest and current.close > previous.high
        else:
            retest = previous.high >= previous_features.ema_fast - spec.pullback_band_atr * atr
            aligned = momentum_aligned and retest and current.close < previous.low
    else:
        raise ValueError(f"알 수 없는 추세 신호 종류입니다: {spec.signal_kind}")
    body = abs(current.close - current.open)
    if not aligned or body < 0.20 * atr:
        return None
    trend_distance = abs(features.higher_ema_fast - features.higher_ema_slow) / current.close
    score = features.relative_volume + features.adx / 100 + trend_distance * 10_000
    return TrendSignal(
        candidate_id=spec.candidate_id,
        symbol=current.symbol,
        side="LONG" if direction > 0 else "SHORT",
        signal_ts_ms=current.close_ts_ms,
        entry_ts_ms=rows[index + 1].open_ts_ms,
        score=score,
        atr=atr,
        signal_close=current.close,
    )


def _return_bps(side: str, entry: float, exit_price: float) -> float:
    direction = 1 if side == "LONG" else -1
    return (exit_price - entry) / entry * 10_000 * direction


def simulate_signal(
    rows: Sequence[Kline],
    signal_index: int,
    signal: TrendSignal,
    spec: TrendCandidateSpec,
) -> TrendOutcome:
    entry_index = signal_index + 1
    entry = rows[entry_index].open
    direction = 1 if signal.side == "LONG" else -1
    risk = spec.stop_atr * signal.atr
    stop = entry - direction * risk
    tp1 = entry + direction * risk * spec.tp1_r
    tp2 = entry + direction * risk * spec.tp2_r
    realized_gross_bps = 0.0
    remaining = 1.0
    exit_price = rows[min(len(rows) - 1, entry_index + spec.maximum_holding_bars)].close
    exit_index = min(len(rows) - 1, entry_index + spec.maximum_holding_bars)
    exit_reason = "MAX_HOLD"
    tp1_taken = False
    for index in range(entry_index, min(len(rows), entry_index + spec.maximum_holding_bars + 1)):
        bar = rows[index]
        stop_hit = bar.low <= stop if direction > 0 else bar.high >= stop
        tp1_hit = bar.high >= tp1 if direction > 0 else bar.low <= tp1
        tp2_hit = bar.high >= tp2 if direction > 0 else bar.low <= tp2
        if stop_hit:
            exit_price = stop
            exit_index = index
            exit_reason = "STOP_AFTER_TP1" if tp1_taken else "STOP"
            break
        if tp2_hit:
            if not tp1_taken:
                realized_gross_bps = 0.4 * _return_bps(signal.side, entry, tp1)
                remaining = 0.6
            exit_price = tp2
            exit_index = index
            exit_reason = "TP2"
            break
        if tp1_hit and not tp1_taken:
            tp1_taken = True
            realized_gross_bps = 0.4 * _return_bps(signal.side, entry, tp1)
            remaining = 0.6
    gross_bps = realized_gross_bps + remaining * _return_bps(signal.side, entry, exit_price)
    return TrendOutcome(
        candidate_id=spec.candidate_id,
        symbol=signal.symbol,
        side=signal.side,
        signal_ts_ms=signal.signal_ts_ms,
        entry_ts_ms=signal.entry_ts_ms,
        exit_ts_ms=rows[exit_index].close_ts_ms,
        holding_bars=exit_index - entry_index + 1,
        exit_reason=exit_reason,
        entry=entry,
        stop=stop,
        take_profit_1=tp1,
        take_profit_2=tp2,
        gross_bps=gross_bps,
        base_net_bps=gross_bps - BASE_COST_BPS,
        stress_net_bps=gross_bps - STRESS_COST_BPS,
        score=signal.score,
    )


def _raw_outcomes_for_symbol(
    rows: Sequence[Kline],
    specs: Sequence[TrendCandidateSpec],
) -> dict[str, list[TrendOutcome]]:
    feature_rows = _features(rows)
    outcomes: dict[str, list[TrendOutcome]] = defaultdict(list)
    cooldown_until: dict[str, int] = defaultdict(int)
    for index in range(600, len(rows) - 1):
        for spec in specs:
            if rows[index].open_ts_ms < cooldown_until[spec.candidate_id]:
                continue
            signal = _signal_at(rows, feature_rows, index, spec)
            if signal is None:
                continue
            outcome = simulate_signal(rows, index, signal, spec)
            outcomes[spec.candidate_id].append(outcome)
            cooldown_until[spec.candidate_id] = (
                outcome.exit_ts_ms + spec.cooldown_bars * BAR_INTERVAL_MS
            )
    return outcomes


def apply_portfolio_limits(outcomes: Iterable[TrendOutcome]) -> tuple[TrendOutcome, ...]:
    selected: list[TrendOutcome] = []
    open_until: list[int] = []
    entries_by_day: dict[int, int] = defaultdict(int)
    for outcome in sorted(outcomes, key=lambda row: (row.entry_ts_ms, -row.score, row.symbol)):
        open_until = [value for value in open_until if value >= outcome.entry_ts_ms]
        day = outcome.entry_ts_ms // 86_400_000
        if len(open_until) >= MAXIMUM_CONCURRENT_POSITIONS:
            continue
        if entries_by_day[day] >= MAXIMUM_DAILY_ENTRIES:
            continue
        selected.append(outcome)
        open_until.append(outcome.exit_ts_ms)
        entries_by_day[day] += 1
    return tuple(selected)


def research_candidates(
    data: dict[str, tuple[Kline, ...]],
    specs: Sequence[TrendCandidateSpec] = PREREGISTERED_CANDIDATES,
) -> dict[str, tuple[TrendOutcome, ...]]:
    raw: dict[str, list[TrendOutcome]] = defaultdict(list)
    for symbol in sorted(data):
        by_candidate = _raw_outcomes_for_symbol(data[symbol], specs)
        for candidate_id, outcomes in by_candidate.items():
            raw[candidate_id].extend(outcomes)
    return {
        spec.candidate_id: apply_portfolio_limits(raw.get(spec.candidate_id, ())) for spec in specs
    }


def _profile(values: Sequence[float]) -> dict[str, object]:
    if not values:
        return {
            "sample_size": 0,
            "win_rate": None,
            "expectancy_bps": None,
            "profit_factor": None,
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
    return {
        "sample_size": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(values),
        "expectancy_bps": fmean(values),
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "net_sum_bps": sum(values),
        "maximum_drawdown_bps": drawdown,
        "sample_status": "ENOUGH" if len(values) >= MINIMUM_OOS_SAMPLE else "INSUFFICIENT",
    }


def _split_outcomes(
    outcomes: Sequence[TrendOutcome],
    *,
    start_ms: int,
    end_ms: int,
    purge_ms: int,
) -> dict[str, tuple[TrendOutcome, ...]]:
    train_end = start_ms + int((end_ms - start_ms) * 0.50)
    validation_end = start_ms + int((end_ms - start_ms) * 0.70)
    return {
        "train": tuple(row for row in outcomes if row.exit_ts_ms < train_end - purge_ms),
        "validation": tuple(
            row
            for row in outcomes
            if row.entry_ts_ms > train_end + purge_ms and row.exit_ts_ms < validation_end - purge_ms
        ),
        "oos": tuple(row for row in outcomes if row.entry_ts_ms > validation_end + purge_ms),
    }


def _fold_returns(
    outcomes: dict[str, tuple[TrendOutcome, ...]],
    *,
    start_ms: int,
    development_end_ms: int,
    fold_count: int = 8,
) -> dict[str, tuple[float, ...]]:
    width = (development_end_ms - start_ms) / fold_count
    return {
        candidate_id: tuple(
            fmean(values)
            if (
                values := [
                    row.base_net_bps
                    for row in rows
                    if start_ms + fold * width <= row.entry_ts_ms < start_ms + (fold + 1) * width
                ]
            )
            else 0.0
            for fold in range(fold_count)
        )
        for candidate_id, rows in outcomes.items()
    }


def build_report(
    data: dict[str, tuple[Kline, ...]],
    dataset_manifest: Sequence[dict[str, object]],
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, object]:
    outcomes = research_candidates(data)
    maximum_holding_ms = (
        max(spec.maximum_holding_bars for spec in PREREGISTERED_CANDIDATES) * BAR_INTERVAL_MS
    )
    splits = {
        candidate_id: _split_outcomes(
            rows,
            start_ms=start_ms,
            end_ms=end_ms,
            purge_ms=maximum_holding_ms,
        )
        for candidate_id, rows in outcomes.items()
    }
    development_profiles = {
        candidate_id: {
            "base": _profile([row.base_net_bps for row in (*parts["train"], *parts["validation"])]),
            "stress": _profile(
                [row.stress_net_bps for row in (*parts["train"], *parts["validation"])]
            ),
            "validation_stress": _profile([row.stress_net_bps for row in parts["validation"]]),
        }
        for candidate_id, parts in splits.items()
    }
    eligible = [
        candidate_id
        for candidate_id, profile in development_profiles.items()
        if int(profile["base"]["sample_size"]) >= 60
        and profile["stress"]["expectancy_bps"] is not None
        and float(profile["stress"]["expectancy_bps"]) > 0
        and profile["validation_stress"]["expectancy_bps"] is not None
        and float(profile["validation_stress"]["expectancy_bps"]) > 0
    ]
    selected = (
        max(
            eligible,
            key=lambda candidate_id: (
                float(development_profiles[candidate_id]["validation_stress"]["expectancy_bps"]),
                float(development_profiles[candidate_id]["stress"]["expectancy_bps"]),
                candidate_id,
            ),
        )
        if eligible
        else None
    )
    oos_rows = splits[selected]["oos"] if selected is not None else ()
    oos_base = [row.base_net_bps for row in oos_rows]
    oos_stress = [row.stress_net_bps for row in oos_rows]
    oos_start = start_ms + int((end_ms - start_ms) * 0.70) + maximum_holding_ms
    oos_days = max(1.0, (end_ms - oos_start) / 86_400_000)
    pbo = probability_of_backtest_overfitting(
        _fold_returns(
            outcomes,
            start_ms=start_ms,
            development_end_ms=start_ms + int((end_ms - start_ms) * 0.70),
        )
    )
    bootstrap = bootstrap_mean_interval(oos_base, seed=SEED)
    dsr = deflated_sharpe_ratio(oos_base, trials=len(PREREGISTERED_CANDIDATES))
    oos_base_profile = _profile(oos_base)
    oos_stress_profile = _profile(oos_stress)
    daily_frequency = len(oos_rows) / oos_days
    gates = {
        "selected_candidate_exists": selected is not None,
        "oos_sample_at_least_60": len(oos_rows) >= MINIMUM_OOS_SAMPLE,
        "oos_base_expectancy_positive": bool(oos_base) and fmean(oos_base) > 0,
        "oos_base_profit_factor_at_least_1_15": (
            oos_base_profile["profit_factor"] is not None
            and float(oos_base_profile["profit_factor"]) >= 1.15
        ),
        "oos_stress_expectancy_positive": bool(oos_stress) and fmean(oos_stress) > 0,
        "oos_stress_profit_factor_above_1": (
            oos_stress_profile["profit_factor"] is not None
            and float(oos_stress_profile["profit_factor"]) > 1
        ),
        "bootstrap_lower_positive": (
            bootstrap.get("lower") is not None and float(bootstrap["lower"]) > 0
        ),
        "dsr_at_least_0_95": (
            dsr.get("dsr_probability") is not None and float(dsr["dsr_probability"]) >= 0.95
        ),
        "pbo_at_most_0_20": float(pbo["pbo"]) <= 0.20,
        "oos_frequency_two_to_eight_per_day": 2 <= daily_frequency <= 8,
    }
    summaries = {
        candidate_id: {
            split_name: {
                "base": _profile([row.base_net_bps for row in rows]),
                "stress": _profile([row.stress_net_bps for row in rows]),
                "exit_reasons": dict(
                    sorted(
                        (reason, sum(row.exit_reason == reason for row in rows))
                        for reason in {row.exit_reason for row in rows}
                    )
                ),
            }
            for split_name, rows in parts.items()
        }
        for candidate_id, parts in splits.items()
    }
    dataset_hash = hashlib.sha256(
        json.dumps(dataset_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "status": "OOS_PASS" if all(gates.values()) else "NOT_PROVEN",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "generated_ts_ms": time.time_ns() // 1_000_000,
        "code_hash": git_commit(),
        "source": {
            "venue": "BINANCE_USDM",
            "endpoint": BINANCE_FUTURES_KLINES_URL,
            "public_only": True,
            "interval": "5m",
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "completed_candles_only": True,
            "dataset_hash": dataset_hash,
            "datasets": list(dataset_manifest),
        },
        "preregistration": {
            "hypothesis_id": "HYP-PUBLIC-TREND-5M-1H-V1",
            "candidates": [asdict(spec) for spec in PREREGISTERED_CANDIDATES],
            "candidate_count": len(PREREGISTERED_CANDIDATES),
            "base_cost_bps": BASE_COST_BPS,
            "stress_cost_bps": STRESS_COST_BPS,
            "next_bar_open_entry": True,
            "same_bar_stop_before_target": True,
            "maximum_concurrent_positions": MAXIMUM_CONCURRENT_POSITIONS,
            "maximum_daily_entries": MAXIMUM_DAILY_ENTRIES,
            "split": "chronological 50% train / 20% validation / 30% untouched OOS",
            "purge_embargo_ms": maximum_holding_ms,
            "selection": (
                "positive development and validation STRESS, then best validation STRESS expectancy"
            ),
            "annualization": False,
            "natural_signal_thresholds_lowered": False,
        },
        "development_profiles": development_profiles,
        "selected_on_train_validation": selected,
        "selection_bias": {
            "pbo": pbo,
            "oos_deflated_sharpe": dsr,
            "oos_expectancy_bootstrap_95": bootstrap,
            "no_trade_baseline_bps": 0.0,
        },
        "oos": {
            "base": oos_base_profile,
            "stress": oos_stress_profile,
            "daily_frequency": daily_frequency,
            "sample_span_days": oos_days,
            "examples": [asdict(row) for row in oos_rows[:20]],
        },
        "promotion_assessment": {
            "status": "OOS_PASS" if all(gates.values()) else "NOT_PROVEN",
            "gates": gates,
            "registry_changes": [],
            "policy": "OOS_PASS 뒤에도 별도 runtime SHADOW 구현과 실제 bid·ask 자연표본이 필요함",
        },
        "summaries": summaries,
        "limitations": [
            (
                "공개 역사 kline에는 과거 bid·ask 깊이가 없어 고정 BASE/STRESS "
                "왕복비용을 차감했습니다."
            ),
            "이 결과는 runtime Strategy Registry 승격이나 실제 수익성 증명이 아닙니다.",
            "실제 bid·ask SHADOW와 장기간 자연표본은 별도로 검증해야 합니다.",
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
    if end_ms - start_ms < 60 * 86_400_000:
        raise ValueError("추세 연구 기간은 최소 60일이어야 합니다.")
    symbols = tuple(args.symbol or DEFAULT_SYMBOLS)
    data, dataset_manifest = load_public_klines(
        symbols,
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
