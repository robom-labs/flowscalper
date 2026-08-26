# 공개 5분 완성 봉을 15·30분으로 집계해 사전등록 중단기 추세 후보를 PAPER 연구한다.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean

from backend.app.build_identity import git_commit
from backend.app.research import (
    bootstrap_mean_interval,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)
from scripts.research_public_trend_candidates import (
    BASE_COST_BPS,
    DEFAULT_SYMBOLS,
    SEED,
    STRESS_COST_BPS,
    Kline,
    _ema,
    _parse_date,
    _rolling_mean,
    load_public_klines,
)

BASE_BAR_MS = 300_000
MINIMUM_OOS_SAMPLE = 40
MAXIMUM_CONCURRENT_POSITIONS = 2
MAXIMUM_DAILY_ENTRIES = 4


@dataclass(frozen=True, slots=True)
class IntradayBar:
    symbol: str
    interval_minutes: int
    open_ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def interval_ms(self) -> int:
        return self.interval_minutes * 60_000

    @property
    def close_ts_ms(self) -> int:
        return self.open_ts_ms + self.interval_ms - 1


@dataclass(frozen=True, slots=True)
class IntradaySpec:
    candidate_id: str
    interval_minutes: int
    signal_kind: str
    lookback: int
    momentum_minimum: float
    adx_minimum: float
    relative_volume_minimum: float
    stop_atr: float
    tp1_r: float
    tp2_r: float
    maximum_holding_hours: int
    cooldown_hours: int

    @property
    def momentum_bars(self) -> int:
        return 24 * 60 // self.interval_minutes

    @property
    def maximum_holding_bars(self) -> int:
        return self.maximum_holding_hours * 60 // self.interval_minutes

    @property
    def cooldown_ms(self) -> int:
        return self.cooldown_hours * 3_600_000


PREREGISTERED_CANDIDATES = (
    IntradaySpec(
        "INTRADAY_PULLBACK_15M_V1",
        15,
        "EMA20_RECLAIM",
        20,
        0.010,
        18,
        0.8,
        1.5,
        1.4,
        2.8,
        8,
        6,
    ),
    IntradaySpec(
        "INTRADAY_BREAKOUT_15M_V1",
        15,
        "DONCHIAN",
        32,
        0.015,
        20,
        1.1,
        1.6,
        1.6,
        3.2,
        12,
        8,
    ),
    IntradaySpec(
        "INTRADAY_BREAKOUT_30M_V1",
        30,
        "DONCHIAN",
        24,
        0.015,
        20,
        1.0,
        1.7,
        1.6,
        3.2,
        18,
        10,
    ),
    IntradaySpec(
        "INTRADAY_MOMENTUM_30M_V1",
        30,
        "MOMENTUM_CONTINUATION",
        20,
        0.012,
        18,
        0.9,
        1.6,
        1.5,
        3.0,
        16,
        8,
    ),
)


@dataclass(frozen=True, slots=True)
class IntradayFeatures:
    ema20: float
    ema80: float
    atr: float
    adx: float
    relative_volume: float
    momentum_24h: float


@dataclass(frozen=True, slots=True)
class IntradayOutcome:
    candidate_id: str
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
    gross_bps: float
    base_net_bps: float
    stress_net_bps: float
    score: float


def aggregate_bars(rows: Sequence[Kline], interval_minutes: int) -> tuple[IntradayBar, ...]:
    interval_ms = interval_minutes * 60_000
    expected = interval_ms // BASE_BAR_MS
    grouped: dict[int, list[Kline]] = defaultdict(list)
    for row in rows:
        grouped[row.open_ts_ms - row.open_ts_ms % interval_ms].append(row)
    output: list[IntradayBar] = []
    for open_ts_ms, values in sorted(grouped.items()):
        ordered = sorted(values, key=lambda row: row.open_ts_ms)
        if len(ordered) != expected:
            continue
        if any(
            current.open_ts_ms - previous.open_ts_ms != BASE_BAR_MS
            for previous, current in zip(ordered, ordered[1:], strict=False)
        ):
            continue
        output.append(
            IntradayBar(
                symbol=ordered[0].symbol,
                interval_minutes=interval_minutes,
                open_ts_ms=open_ts_ms,
                open=ordered[0].open,
                high=max(row.high for row in ordered),
                low=min(row.low for row in ordered),
                close=ordered[-1].close,
                volume=sum(row.volume for row in ordered),
            )
        )
    return tuple(output)


def _features(
    rows: Sequence[IntradayBar],
    spec: IntradaySpec,
) -> tuple[IntradayFeatures | None, ...]:
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
    minimum_index = max(80, spec.momentum_bars)
    output: list[IntradayFeatures | None] = []
    for index, row in enumerate(rows):
        if index < minimum_index or math.isnan(atr[index]) or math.isnan(adx[index]):
            output.append(None)
            continue
        prior_volume = volume_mean[index - 1]
        output.append(
            IntradayFeatures(
                ema20=ema20[index],
                ema80=ema80[index],
                atr=atr[index],
                adx=adx[index],
                relative_volume=row.volume / prior_volume if prior_volume > 0 else 0.0,
                momentum_24h=row.close / rows[index - spec.momentum_bars].close - 1,
            )
        )
    return tuple(output)


def _direction(features: IntradayFeatures, minimum: float) -> int:
    if features.ema20 > features.ema80 and features.momentum_24h >= minimum:
        return 1
    if features.ema20 < features.ema80 and features.momentum_24h <= -minimum:
        return -1
    return 0


def _qualified(
    rows: Sequence[IntradayBar],
    features: Sequence[IntradayFeatures | None],
    index: int,
    direction: int,
    spec: IntradaySpec,
) -> bool:
    current = rows[index]
    if spec.signal_kind == "DONCHIAN":
        history = rows[index - spec.lookback : index]
        return (
            current.close > max(row.high for row in history)
            if direction > 0
            else current.close < min(row.low for row in history)
        )
    if spec.signal_kind == "MOMENTUM_CONTINUATION":
        return (
            current.close > rows[index - 1].high and current.close > current.open
            if direction > 0
            else current.close < rows[index - 1].low and current.close < current.open
        )
    if spec.signal_kind != "EMA20_RECLAIM":
        raise ValueError(f"알 수 없는 중단기 신호입니다: {spec.signal_kind}")
    previous_features = features[index - 1]
    if previous_features is None:
        return False
    if direction > 0:
        return (
            rows[index - 1].close <= previous_features.ema20
            and current.close > features[index].ema20  # type: ignore[union-attr]
            and current.close > current.open
        )
    return (
        rows[index - 1].close >= previous_features.ema20
        and current.close < features[index].ema20  # type: ignore[union-attr]
        and current.close < current.open
    )


def _return_bps(side: str, entry: float, exit_price: float) -> float:
    direction = 1 if side == "LONG" else -1
    return (exit_price - entry) / entry * 10_000 * direction


def _simulate(
    rows: Sequence[IntradayBar],
    *,
    index: int,
    direction: int,
    features: IntradayFeatures,
    score: float,
    spec: IntradaySpec,
) -> IntradayOutcome:
    entry_index = index + 1
    entry = rows[entry_index].open
    side = "LONG" if direction > 0 else "SHORT"
    risk = features.atr * spec.stop_atr
    initial_stop = entry - direction * risk
    current_stop = initial_stop
    tp1 = entry + direction * risk * spec.tp1_r
    tp2 = entry + direction * risk * spec.tp2_r
    realized = 0.0
    remaining = 1.0
    exit_index = min(len(rows) - 1, entry_index + spec.maximum_holding_bars)
    exit_price = rows[exit_index].close
    exit_reason = "MAX_HOLD"
    tp1_hit_ts_ms: int | None = None
    tp2_hit_ts_ms: int | None = None
    for cursor in range(
        entry_index,
        min(len(rows), entry_index + spec.maximum_holding_bars + 1),
    ):
        bar = rows[cursor]
        stop_hit = bar.low <= current_stop if direction > 0 else bar.high >= current_stop
        tp1_hit = bar.high >= tp1 if direction > 0 else bar.low <= tp1
        tp2_hit = bar.high >= tp2 if direction > 0 else bar.low <= tp2
        if stop_hit:
            exit_index = cursor
            exit_price = current_stop
            exit_reason = "STOP_AFTER_TP1" if tp1_hit_ts_ms is not None else "STOP"
            break
        if tp2_hit:
            if tp1_hit_ts_ms is None:
                tp1_hit_ts_ms = bar.close_ts_ms
                realized = 0.4 * _return_bps(side, entry, tp1)
                remaining = 0.6
            tp2_hit_ts_ms = bar.close_ts_ms
            exit_index = cursor
            exit_price = tp2
            exit_reason = "TP2"
            break
        if tp1_hit and tp1_hit_ts_ms is None:
            tp1_hit_ts_ms = bar.close_ts_ms
            realized = 0.4 * _return_bps(side, entry, tp1)
            remaining = 0.6
            cost_adjustment = entry * BASE_COST_BPS / 10_000
            current_stop = entry + direction * cost_adjustment
    gross_bps = realized + remaining * _return_bps(side, entry, exit_price)
    return IntradayOutcome(
        candidate_id=spec.candidate_id,
        symbol=rows[index].symbol,
        side=side,
        signal_ts_ms=rows[index].close_ts_ms,
        entry_ts_ms=rows[entry_index].open_ts_ms,
        exit_ts_ms=rows[exit_index].close_ts_ms,
        holding_minutes=(exit_index - entry_index + 1) * spec.interval_minutes,
        exit_reason=exit_reason,
        tp1_hit_ts_ms=tp1_hit_ts_ms,
        tp2_hit_ts_ms=tp2_hit_ts_ms,
        entry=entry,
        stop=initial_stop,
        take_profit_1=tp1,
        take_profit_2=tp2,
        gross_bps=gross_bps,
        base_net_bps=gross_bps - BASE_COST_BPS,
        stress_net_bps=gross_bps - STRESS_COST_BPS,
        score=score,
    )


def _symbol_outcomes(
    rows: Sequence[IntradayBar],
    spec: IntradaySpec,
) -> list[IntradayOutcome]:
    features = _features(rows, spec)
    output: list[IntradayOutcome] = []
    cooldown_until = 0
    start = max(80, spec.momentum_bars, spec.lookback)
    for index in range(start, len(rows) - 1):
        current_features = features[index]
        if current_features is None or rows[index].open_ts_ms < cooldown_until:
            continue
        if (
            current_features.adx < spec.adx_minimum
            or current_features.relative_volume < spec.relative_volume_minimum
        ):
            continue
        direction = _direction(current_features, spec.momentum_minimum)
        if direction == 0 or not _qualified(rows, features, index, direction, spec):
            continue
        trend_distance = abs(current_features.ema20 - current_features.ema80) / rows[index].close
        score = (
            abs(current_features.momentum_24h) * 100
            + current_features.adx / 100
            + current_features.relative_volume
            + trend_distance * 1_000
        )
        outcome = _simulate(
            rows,
            index=index,
            direction=direction,
            features=current_features,
            score=score,
            spec=spec,
        )
        output.append(outcome)
        cooldown_until = outcome.exit_ts_ms + spec.cooldown_ms
    return output


def apply_portfolio_limits(outcomes: Iterable[IntradayOutcome]) -> tuple[IntradayOutcome, ...]:
    selected: list[IntradayOutcome] = []
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


def research_intraday(
    data: dict[str, tuple[Kline, ...]],
) -> dict[str, tuple[IntradayOutcome, ...]]:
    output: dict[str, tuple[IntradayOutcome, ...]] = {}
    for spec in PREREGISTERED_CANDIDATES:
        raw: list[IntradayOutcome] = []
        for _symbol, klines in sorted(data.items()):
            raw.extend(_symbol_outcomes(aggregate_bars(klines, spec.interval_minutes), spec))
        output[spec.candidate_id] = apply_portfolio_limits(raw)
    return output


def _split(
    rows: Sequence[IntradayOutcome],
    *,
    start_ms: int,
    end_ms: int,
    purge_ms: int,
) -> dict[str, tuple[IntradayOutcome, ...]]:
    train_end = start_ms + int((end_ms - start_ms) * 0.50)
    validation_end = start_ms + int((end_ms - start_ms) * 0.70)
    return {
        "train": tuple(row for row in rows if row.exit_ts_ms < train_end - purge_ms),
        "validation": tuple(
            row
            for row in rows
            if row.entry_ts_ms > train_end + purge_ms
            and row.exit_ts_ms < validation_end - purge_ms
        ),
        "oos": tuple(row for row in rows if row.entry_ts_ms > validation_end + purge_ms),
    }


def _profile(values: Sequence[float]) -> dict[str, object]:
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
        "maximum_drawdown_bps": drawdown,
        "sample_status": "ENOUGH" if len(values) >= MINIMUM_OOS_SAMPLE else "INSUFFICIENT",
    }


def _fold_returns(
    outcomes: dict[str, tuple[IntradayOutcome, ...]],
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
                    row.base_net_bps
                    for row in rows
                    if start_ms + fold * width <= row.entry_ts_ms
                    < start_ms + (fold + 1) * width
                ]
            )
            else 0.0
            for fold in range(8)
        )
        for candidate_id, rows in outcomes.items()
    }


def _symbol_concentration(rows: Sequence[IntradayOutcome]) -> dict[str, object]:
    contributions: dict[str, float] = defaultdict(float)
    for row in rows:
        contributions[row.symbol] += row.base_net_bps
    positive_total = sum(value for value in contributions.values() if value > 0)
    largest_symbol = max(contributions, key=contributions.get) if contributions else None
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


def build_report(
    data: dict[str, tuple[Kline, ...]],
    dataset_manifest: Sequence[dict[str, object]],
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, object]:
    outcomes = research_intraday(data)
    purge_ms = max(spec.maximum_holding_hours for spec in PREREGISTERED_CANDIDATES) * 3_600_000
    splits = {
        candidate_id: _split(rows, start_ms=start_ms, end_ms=end_ms, purge_ms=purge_ms)
        for candidate_id, rows in outcomes.items()
    }
    development = {
        candidate_id: {
            "base": _profile(
                [row.base_net_bps for row in (*parts["train"], *parts["validation"])]
            ),
            "stress": _profile(
                [row.stress_net_bps for row in (*parts["train"], *parts["validation"])]
            ),
            "validation_stress": _profile(
                [row.stress_net_bps for row in parts["validation"]]
            ),
        }
        for candidate_id, parts in splits.items()
    }
    eligible = [
        candidate_id
        for candidate_id, profile in development.items()
        if int(profile["base"]["sample_size"]) >= 40
        and profile["stress"]["expectancy_bps"] is not None
        and float(profile["stress"]["expectancy_bps"]) > 0
        and profile["validation_stress"]["expectancy_bps"] is not None
        and float(profile["validation_stress"]["expectancy_bps"]) > 0
    ]
    selected = (
        max(
            eligible,
            key=lambda candidate_id: (
                float(development[candidate_id]["validation_stress"]["expectancy_bps"]),
                candidate_id,
            ),
        )
        if eligible
        else None
    )
    oos_rows = splits[selected]["oos"] if selected is not None else ()
    base_values = [row.base_net_bps for row in oos_rows]
    stress_values = [row.stress_net_bps for row in oos_rows]
    base = _profile(base_values)
    stress = _profile(stress_values)
    bootstrap = bootstrap_mean_interval(base_values, seed=SEED)
    dsr = deflated_sharpe_ratio(base_values, trials=len(PREREGISTERED_CANDIDATES))
    pbo = probability_of_backtest_overfitting(
        _fold_returns(
            outcomes,
            start_ms=start_ms,
            development_end_ms=start_ms + int((end_ms - start_ms) * 0.70),
        )
    )
    oos_start = start_ms + int((end_ms - start_ms) * 0.70) + purge_ms
    oos_days = max(1.0, (end_ms - oos_start) / 86_400_000)
    daily_frequency = len(oos_rows) / oos_days
    concentration = _symbol_concentration(oos_rows)
    concentration_share = concentration["largest_positive_contribution_share"]
    metric_gates = {
        "selected_candidate_exists": selected is not None,
        "oos_sample_at_least_40": len(oos_rows) >= MINIMUM_OOS_SAMPLE,
        "oos_base_expectancy_positive": bool(base_values) and fmean(base_values) > 0,
        "oos_base_profit_factor_at_least_1_15": (
            base["profit_factor"] is not None and float(base["profit_factor"]) >= 1.15
        ),
        "oos_stress_expectancy_positive": bool(stress_values) and fmean(stress_values) > 0,
        "oos_win_rate_at_least_0_45": (
            base["win_rate"] is not None and float(base["win_rate"]) >= 0.45
        ),
        "oos_payoff_ratio_at_least_1_10": (
            base["payoff_ratio"] is not None and float(base["payoff_ratio"]) >= 1.10
        ),
        "bootstrap_lower_positive": (
            bootstrap.get("lower") is not None and float(bootstrap["lower"]) > 0
        ),
        "dsr_at_least_0_95": (
            dsr.get("dsr_probability") is not None and float(dsr["dsr_probability"]) >= 0.95
        ),
        "pbo_at_most_0_20": float(pbo["pbo"]) <= 0.20,
        "frequency_half_to_four_per_day": 0.5 <= daily_frequency <= 4,
        "largest_symbol_share_at_most_0_50": (
            concentration_share is not None and float(concentration_share) <= 0.50
        ),
    }
    metrics_pass = all(metric_gates.values())
    return {
        "schema_version": 1,
        "status": (
            "ADAPTIVE_DIAGNOSTIC_PASS_REQUIRES_NEW_OOS" if metrics_pass else "NOT_PROVEN"
        ),
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "generated_ts_ms": time.time_ns() // 1_000_000,
        "code_hash": git_commit(),
        "adaptive_boundary": {
            "status": "NOT_PROMOTION_EVIDENCE",
            "reason": (
                "사전등록은 실행 전에 커밋했지만 미래 수집 구간이 아니므로 "
                "새 OOS 전에는 승격하지 않는다."
            ),
            "registry_changes": [],
        },
        "source": {
            "venue": "BINANCE_USDM",
            "public_only": True,
            "base_interval": "5m",
            "research_intervals": ["15m", "30m"],
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "datasets": list(dataset_manifest),
            "dataset_hash": hashlib.sha256(
                json.dumps(dataset_manifest, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "protocol": {
            "hypothesis_id": "HYP-046-PUBLIC-INTRADAY-TREND-V1",
            "preregistration_path": "docs/research/HYP-046-public-intraday-trend-v1.md",
            "candidates": [asdict(spec) for spec in PREREGISTERED_CANDIDATES],
            "base_cost_bps": BASE_COST_BPS,
            "stress_cost_bps": STRESS_COST_BPS,
            "next_bar_open_entry": True,
            "same_bar_stop_before_target": True,
            "tp1_fraction": 0.4,
            "maximum_concurrent_positions": MAXIMUM_CONCURRENT_POSITIONS,
            "maximum_daily_entries": MAXIMUM_DAILY_ENTRIES,
            "split": "chronological 50% train / 20% validation / 30% diagnostic OOS",
            "purge_embargo_ms": purge_ms,
            "annualization": False,
            "natural_signal_thresholds_lowered": False,
        },
        "development_profiles": development,
        "selected_on_train_validation": selected,
        "selection_bias": {
            "pbo": pbo,
            "oos_deflated_sharpe": dsr,
            "oos_expectancy_bootstrap_95": bootstrap,
            "no_trade_baseline_bps": 0.0,
        },
        "diagnostic_oos": {
            "base": base,
            "stress": stress,
            "daily_frequency": daily_frequency,
            "sample_span_days": oos_days,
            "symbol_concentration": concentration,
            "examples": [asdict(row) for row in oos_rows[:20]],
        },
        "metric_gates": metric_gates,
        "promotion_assessment": {
            "status": "NOT_PROVEN",
            "registry_changes": [],
            "new_future_oos_required": True,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
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
    data, dataset_manifest = load_public_klines(
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
    else:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
