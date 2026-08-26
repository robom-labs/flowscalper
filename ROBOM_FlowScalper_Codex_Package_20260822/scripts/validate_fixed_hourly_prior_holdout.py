# 미다운로드 독립 과거구간에서 고정 시간봉 K 후보를 파라미터 변경 없이 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from statistics import fmean, median

from backend.app.build_identity import git_commit
from backend.app.research import bootstrap_mean_interval, deflated_sharpe_ratio
from scripts.research_public_hourly_trend_diagnostic import (
    HOURLY_CANDIDATES,
    HourOutcome,
    research_hourly,
)
from scripts.research_public_trend_candidates import (
    BASE_COST_BPS,
    DEFAULT_SYMBOLS,
    SEED,
    STRESS_COST_BPS,
    Kline,
    _parse_date,
    load_public_klines,
)

FIXED_CANDIDATE_ID = "HOURLY_MOMENTUM_BREAKOUT_24H_V1"
MINIMUM_SAMPLE = 60


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
    average_win = fmean(wins) if wins else None
    average_loss = abs(fmean(losses)) if losses else None
    gross_loss = abs(sum(losses))
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
    }


def _concentration(rows: Sequence[HourOutcome]) -> dict[str, object]:
    by_symbol: dict[str, float] = defaultdict(float)
    for row in rows:
        by_symbol[row.symbol] += row.base_net_bps
    positive_total = sum(value for value in by_symbol.values() if value > 0)
    largest_symbol = max(by_symbol, key=by_symbol.get) if by_symbol else None
    largest_share = (
        max(by_symbol.values()) / positive_total
        if positive_total > 0 and by_symbol
        else None
    )
    return {
        "base_net_bps_by_symbol": dict(sorted(by_symbol.items())),
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
    candidates = {spec.candidate_id: spec for spec in HOURLY_CANDIDATES}
    if FIXED_CANDIDATE_ID not in candidates:
        raise RuntimeError("고정 시간봉 후보가 코드에서 사라졌습니다.")
    rows = research_hourly(data)[FIXED_CANDIDATE_ID]
    base_values = [row.base_net_bps for row in rows]
    stress_values = [row.stress_net_bps for row in rows]
    base = _profile(base_values)
    stress = _profile(stress_values)
    bootstrap = bootstrap_mean_interval(base_values, seed=SEED)
    dsr = deflated_sharpe_ratio(base_values, trials=1)
    span_days = max(1.0, (end_ms - start_ms) / 86_400_000)
    daily_frequency = len(rows) / span_days
    concentration = _concentration(rows)
    share = concentration["largest_positive_contribution_share"]
    gates = {
        "sample_at_least_60": len(rows) >= MINIMUM_SAMPLE,
        "base_expectancy_positive": bool(base_values) and fmean(base_values) > 0,
        "stress_expectancy_positive": bool(stress_values) and fmean(stress_values) > 0,
        "base_profit_factor_at_least_1_15": (
            base["profit_factor"] is not None and float(base["profit_factor"]) >= 1.15
        ),
        "win_rate_at_least_0_40": (
            base["win_rate"] is not None and float(base["win_rate"]) >= 0.40
        ),
        "payoff_ratio_at_least_1_10": (
            base["payoff_ratio"] is not None and float(base["payoff_ratio"]) >= 1.10
        ),
        "bootstrap_lower_positive": (
            bootstrap.get("lower") is not None and float(bootstrap["lower"]) > 0
        ),
        "frequency_half_to_four_per_day": 0.5 <= daily_frequency <= 4,
        "largest_symbol_share_at_most_0_50": (
            share is not None and float(share) <= 0.50
        ),
    }
    passed = all(gates.values())
    return {
        "schema_version": 1,
        "status": (
            "HISTORICAL_REPLICATION_PASS_FUTURE_OOS_REQUIRED"
            if passed
            else "NOT_PROVEN"
        ),
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "generated_ts_ms": time.time_ns() // 1_000_000,
        "code_hash": git_commit(),
        "protocol": {
            "hypothesis_id": "HYP-046B-FIXED-HOURLY-PRIOR-HOLDOUT",
            "preregistration_path": "docs/research/HYP-046B-fixed-hourly-prior-holdout.md",
            "candidate": FIXED_CANDIDATE_ID,
            "candidate_spec": asdict(candidates[FIXED_CANDIDATE_ID]),
            "parameters_changed_after_download": False,
            "base_cost_bps": BASE_COST_BPS,
            "stress_cost_bps": STRESS_COST_BPS,
            "future_oos_required": True,
        },
        "source": {
            "venue": "BINANCE_USDM",
            "public_only": True,
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "datasets": list(dataset_manifest),
            "dataset_hash": hashlib.sha256(
                json.dumps(dataset_manifest, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "results": {
            "base": base,
            "stress": stress,
            "bootstrap_expectancy_95": bootstrap,
            "deflated_sharpe_single_fixed_candidate": dsr,
            "daily_frequency": daily_frequency,
            "sample_span_days": span_days,
            "median_holding_hours": (
                median(row.holding_hours for row in rows) if rows else None
            ),
            "exit_reasons": dict(sorted(Counter(row.exit_reason for row in rows).items())),
            "symbol_concentration": concentration,
            "examples": [asdict(row) for row in rows[:20]],
        },
        "metric_gates": gates,
        "promotion_assessment": {
            "status": "NOT_PROVEN",
            "registry_changes": [],
            "future_oos_required": True,
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
