# 5분 공개 완성 봉을 1시간으로 집계해 비용 대비 큰 추세 가설을 적응형 진단한다.

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
    _profile,
    _rolling_mean,
    load_public_klines,
)

HOUR_MS = 3_600_000
MINIMUM_OOS_SAMPLE = 40
MAXIMUM_CONCURRENT_POSITIONS = 2
MAXIMUM_DAILY_ENTRIES = 4


@dataclass(frozen=True, slots=True)
class HourBar:
    symbol: str
    open_ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def close_ts_ms(self) -> int:
        return self.open_ts_ms + HOUR_MS - 1


@dataclass(frozen=True, slots=True)
class HourlySpec:
    candidate_id: str
    signal_kind: str
    adx_minimum: float
    relative_volume_minimum: float
    lookback: int
    momentum_hours: int
    momentum_minimum: float
    stop_atr: float
    tp1_r: float
    tp2_r: float
    maximum_holding_hours: int
    cooldown_hours: int
    side_policy: str = "BOTH"


HOURLY_CANDIDATES = (
    HourlySpec(
        "HOURLY_DONCHIAN_20_V1",
        "DONCHIAN",
        18,
        1.0,
        20,
        24,
        0.0,
        1.5,
        1.8,
        3.6,
        24,
        6,
    ),
    HourlySpec(
        "HOURLY_DONCHIAN_55_V1",
        "DONCHIAN",
        22,
        1.2,
        55,
        72,
        0.0,
        1.8,
        2.0,
        4.0,
        36,
        12,
    ),
    HourlySpec(
        "HOURLY_MOMENTUM_24H_V1",
        "MOMENTUM",
        18,
        0.9,
        20,
        24,
        0.02,
        1.6,
        2.0,
        4.0,
        30,
        8,
    ),
    HourlySpec(
        "HOURLY_MOMENTUM_72H_V1",
        "MOMENTUM",
        20,
        1.0,
        20,
        72,
        0.04,
        1.8,
        2.0,
        4.5,
        48,
        12,
    ),
    HourlySpec(
        "HOURLY_MOMENTUM_BREAKOUT_24H_V1",
        "MOMENTUM_BREAKOUT",
        20,
        1.1,
        20,
        24,
        0.02,
        1.8,
        2.2,
        4.5,
        36,
        12,
    ),
    HourlySpec(
        "HOURLY_MOMENTUM_BREAKOUT_72H_V1",
        "MOMENTUM_BREAKOUT",
        22,
        1.2,
        55,
        72,
        0.04,
        2.0,
        2.5,
        5.0,
        48,
        18,
    ),
    HourlySpec(
        "HOURLY_MOMENTUM_BREAKOUT_LONG_V1",
        "MOMENTUM_BREAKOUT",
        20,
        1.0,
        20,
        24,
        0.02,
        1.8,
        2.2,
        4.5,
        48,
        12,
        "LONG",
    ),
)


@dataclass(frozen=True, slots=True)
class HourFeatures:
    ema20: float
    ema50: float
    ema80: float
    ema200: float
    ema80_slope: float
    atr: float
    adx: float
    relative_volume: float


@dataclass(frozen=True, slots=True)
class HourOutcome:
    candidate_id: str
    symbol: str
    side: str
    signal_ts_ms: int
    entry_ts_ms: int
    exit_ts_ms: int
    holding_hours: int
    exit_reason: str
    entry: float
    stop: float
    take_profit_1: float
    take_profit_2: float
    gross_bps: float
    base_net_bps: float
    stress_net_bps: float
    score: float


def aggregate_hourly(rows: Sequence[Kline]) -> tuple[HourBar, ...]:
    grouped: dict[int, list[Kline]] = defaultdict(list)
    for row in rows:
        grouped[row.open_ts_ms - row.open_ts_ms % HOUR_MS].append(row)
    output: list[HourBar] = []
    for open_ts_ms, values in sorted(grouped.items()):
        ordered = sorted(values, key=lambda row: row.open_ts_ms)
        if len(ordered) != 12:
            continue
        if any(
            current.open_ts_ms - previous.open_ts_ms != 300_000
            for previous, current in zip(ordered, ordered[1:], strict=False)
        ):
            continue
        output.append(
            HourBar(
                symbol=ordered[0].symbol,
                open_ts_ms=open_ts_ms,
                open=ordered[0].open,
                high=max(row.high for row in ordered),
                low=min(row.low for row in ordered),
                close=ordered[-1].close,
                volume=sum(row.volume for row in ordered),
            )
        )
    return tuple(output)


def _hour_features(rows: Sequence[HourBar]) -> tuple[HourFeatures | None, ...]:
    closes = [row.close for row in rows]
    volumes = [row.volume for row in rows]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema80 = _ema(closes, 80)
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
    output: list[HourFeatures | None] = []
    for index, row in enumerate(rows):
        if index < 200 or math.isnan(atr[index]) or math.isnan(adx[index]):
            output.append(None)
            continue
        prior_volume = volume_mean[index - 1]
        output.append(
            HourFeatures(
                ema20=ema20[index],
                ema50=ema50[index],
                ema80=ema80[index],
                ema200=ema200[index],
                ema80_slope=ema80[index] - ema80[index - 4],
                atr=atr[index],
                adx=adx[index],
                relative_volume=row.volume / prior_volume if prior_volume > 0 else 0.0,
            )
        )
    return tuple(output)


def _direction(features: HourFeatures) -> int:
    if (
        features.ema20 > features.ema50
        and features.ema80 > features.ema200
        and features.ema80_slope > 0
    ):
        return 1
    if (
        features.ema20 < features.ema50
        and features.ema80 < features.ema200
        and features.ema80_slope < 0
    ):
        return -1
    return 0


def _return_bps(side: str, entry: float, exit_price: float) -> float:
    return (exit_price - entry) / entry * 10_000 * (1 if side == "LONG" else -1)


def _simulate(
    rows: Sequence[HourBar],
    *,
    index: int,
    direction: int,
    features: HourFeatures,
    score: float,
    spec: HourlySpec,
) -> HourOutcome:
    entry_index = index + 1
    entry = rows[entry_index].open
    side = "LONG" if direction > 0 else "SHORT"
    risk = features.atr * spec.stop_atr
    stop = entry - direction * risk
    tp1 = entry + direction * risk * spec.tp1_r
    tp2 = entry + direction * risk * spec.tp2_r
    realized = 0.0
    remaining = 1.0
    exit_index = min(len(rows) - 1, entry_index + spec.maximum_holding_hours)
    exit_price = rows[exit_index].close
    exit_reason = "MAX_HOLD"
    tp1_taken = False
    for cursor in range(
        entry_index,
        min(len(rows), entry_index + spec.maximum_holding_hours + 1),
    ):
        bar = rows[cursor]
        stop_hit = bar.low <= stop if direction > 0 else bar.high >= stop
        tp1_hit = bar.high >= tp1 if direction > 0 else bar.low <= tp1
        tp2_hit = bar.high >= tp2 if direction > 0 else bar.low <= tp2
        if stop_hit:
            exit_index = cursor
            exit_price = stop
            exit_reason = "STOP_AFTER_TP1" if tp1_taken else "STOP"
            break
        if tp2_hit:
            if not tp1_taken:
                realized = 0.4 * _return_bps(side, entry, tp1)
                remaining = 0.6
            exit_index = cursor
            exit_price = tp2
            exit_reason = "TP2"
            break
        if tp1_hit and not tp1_taken:
            tp1_taken = True
            realized = 0.4 * _return_bps(side, entry, tp1)
            remaining = 0.6
    gross_bps = realized + remaining * _return_bps(side, entry, exit_price)
    return HourOutcome(
        candidate_id=spec.candidate_id,
        symbol=rows[index].symbol,
        side=side,
        signal_ts_ms=rows[index].close_ts_ms,
        entry_ts_ms=rows[entry_index].open_ts_ms,
        exit_ts_ms=rows[exit_index].close_ts_ms,
        holding_hours=exit_index - entry_index + 1,
        exit_reason=exit_reason,
        entry=entry,
        stop=stop,
        take_profit_1=tp1,
        take_profit_2=tp2,
        gross_bps=gross_bps,
        base_net_bps=gross_bps - BASE_COST_BPS,
        stress_net_bps=gross_bps - STRESS_COST_BPS,
        score=score,
    )


def _symbol_outcomes(
    rows: Sequence[HourBar],
    specs: Sequence[HourlySpec],
) -> dict[str, list[HourOutcome]]:
    features = _hour_features(rows)
    outcomes: dict[str, list[HourOutcome]] = defaultdict(list)
    cooldown_until: dict[str, int] = defaultdict(int)
    for index in range(200, len(rows) - 1):
        current_features = features[index]
        if current_features is None:
            continue
        direction = _direction(current_features)
        if direction == 0:
            continue
        for spec in specs:
            if rows[index].open_ts_ms < cooldown_until[spec.candidate_id]:
                continue
            if (
                current_features.adx < spec.adx_minimum
                or current_features.relative_volume < spec.relative_volume_minimum
            ):
                continue
            if spec.side_policy != "BOTH" and spec.side_policy != (
                "LONG" if direction > 0 else "SHORT"
            ):
                continue
            current = rows[index]
            history = rows[index - spec.lookback : index]
            if spec.signal_kind == "DONCHIAN":
                qualified = (
                    current.close > max(row.high for row in history)
                    if direction > 0
                    else current.close < min(row.low for row in history)
                )
                momentum = 0.0
            elif spec.signal_kind == "MOMENTUM":
                momentum = current.close / rows[index - spec.momentum_hours].close - 1
                qualified = momentum * direction >= spec.momentum_minimum
                qualified = qualified and (
                    current.close > rows[index - 1].high
                    if direction > 0
                    else current.close < rows[index - 1].low
                )
            elif spec.signal_kind == "MOMENTUM_BREAKOUT":
                momentum = current.close / rows[index - spec.momentum_hours].close - 1
                momentum_aligned = momentum * direction >= spec.momentum_minimum
                breakout = (
                    current.close > max(row.high for row in history)
                    if direction > 0
                    else current.close < min(row.low for row in history)
                )
                qualified = momentum_aligned and breakout
            else:
                raise ValueError(f"알 수 없는 hourly 신호입니다: {spec.signal_kind}")
            if not qualified:
                continue
            trend_distance = abs(current_features.ema80 - current_features.ema200) / current.close
            score = (
                current_features.relative_volume
                + current_features.adx / 100
                + trend_distance * 10_000
                + abs(momentum) * 100
            )
            outcome = _simulate(
                rows,
                index=index,
                direction=direction,
                features=current_features,
                score=score,
                spec=spec,
            )
            outcomes[spec.candidate_id].append(outcome)
            cooldown_until[spec.candidate_id] = outcome.exit_ts_ms + spec.cooldown_hours * HOUR_MS
    return outcomes


def apply_hourly_portfolio_limits(
    outcomes: Iterable[HourOutcome],
) -> tuple[HourOutcome, ...]:
    selected: list[HourOutcome] = []
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


def research_hourly(
    data: dict[str, tuple[Kline, ...]],
) -> dict[str, tuple[HourOutcome, ...]]:
    raw: dict[str, list[HourOutcome]] = defaultdict(list)
    for _symbol, klines in sorted(data.items()):
        by_candidate = _symbol_outcomes(aggregate_hourly(klines), HOURLY_CANDIDATES)
        for candidate_id, outcomes in by_candidate.items():
            raw[candidate_id].extend(outcomes)
    return {
        spec.candidate_id: apply_hourly_portfolio_limits(raw.get(spec.candidate_id, ()))
        for spec in HOURLY_CANDIDATES
    }


def _split(
    rows: Sequence[HourOutcome],
    *,
    start_ms: int,
    end_ms: int,
    purge_ms: int,
) -> dict[str, tuple[HourOutcome, ...]]:
    train_end = start_ms + int((end_ms - start_ms) * 0.50)
    validation_end = start_ms + int((end_ms - start_ms) * 0.70)
    return {
        "train": tuple(row for row in rows if row.exit_ts_ms < train_end - purge_ms),
        "validation": tuple(
            row
            for row in rows
            if row.entry_ts_ms > train_end + purge_ms and row.exit_ts_ms < validation_end - purge_ms
        ),
        "oos": tuple(row for row in rows if row.entry_ts_ms > validation_end + purge_ms),
    }


def _fold_returns(
    outcomes: dict[str, tuple[HourOutcome, ...]],
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
                    if start_ms + fold * width <= row.entry_ts_ms < start_ms + (fold + 1) * width
                ]
            )
            else 0.0
            for fold in range(8)
        )
        for candidate_id, rows in outcomes.items()
    }


def build_hourly_report(
    data: dict[str, tuple[Kline, ...]],
    dataset_manifest: Sequence[dict[str, object]],
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, object]:
    outcomes = research_hourly(data)
    purge_ms = max(spec.maximum_holding_hours for spec in HOURLY_CANDIDATES) * HOUR_MS
    splits = {
        candidate_id: _split(
            rows,
            start_ms=start_ms,
            end_ms=end_ms,
            purge_ms=purge_ms,
        )
        for candidate_id, rows in outcomes.items()
    }
    development = {
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
    dsr = deflated_sharpe_ratio(base_values, trials=len(HOURLY_CANDIDATES))
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
    metric_gates = {
        "selected_candidate_exists": selected is not None,
        "oos_sample_at_least_40": len(oos_rows) >= MINIMUM_OOS_SAMPLE,
        "oos_base_expectancy_positive": bool(base_values) and fmean(base_values) > 0,
        "oos_base_profit_factor_at_least_1_15": (
            base["profit_factor"] is not None and float(base["profit_factor"]) >= 1.15
        ),
        "oos_stress_expectancy_positive": (bool(stress_values) and fmean(stress_values) > 0),
        "bootstrap_lower_positive": (
            bootstrap.get("lower") is not None and float(bootstrap["lower"]) > 0
        ),
        "dsr_at_least_0_95": (
            dsr.get("dsr_probability") is not None and float(dsr["dsr_probability"]) >= 0.95
        ),
        "pbo_at_most_0_20": float(pbo["pbo"]) <= 0.20,
        "frequency_half_to_four_per_day": 0.5 <= daily_frequency <= 4,
    }
    metrics_pass = all(metric_gates.values())
    return {
        "schema_version": 1,
        "status": ("ADAPTIVE_DIAGNOSTIC_PASS_REQUIRES_NEW_OOS" if metrics_pass else "NOT_PROVEN"),
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "generated_ts_ms": time.time_ns() // 1_000_000,
        "code_hash": git_commit(),
        "adaptive_boundary": {
            "status": "NOT_PROMOTION_EVIDENCE",
            "reason": (
                "5분 후보 결과를 본 뒤 동일 기간에 별도 horizon을 추가했으므로 "
                "새 미래 OOS 전에는 승격할 수 없습니다."
            ),
            "registry_changes": [],
        },
        "source": {
            "venue": "BINANCE_USDM",
            "public_only": True,
            "base_interval": "5m",
            "research_interval": "1h",
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "datasets": list(dataset_manifest),
            "dataset_hash": hashlib.sha256(
                json.dumps(dataset_manifest, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "protocol": {
            "hypothesis_id": "HYP-ADAPTIVE-PUBLIC-HOURLY-TREND-V1",
            "candidates": [asdict(spec) for spec in HOURLY_CANDIDATES],
            "base_cost_bps": BASE_COST_BPS,
            "stress_cost_bps": STRESS_COST_BPS,
            "next_hour_open_entry": True,
            "same_bar_stop_before_target": True,
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
    report = build_hourly_report(
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
