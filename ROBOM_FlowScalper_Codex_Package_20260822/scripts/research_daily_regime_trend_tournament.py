# 공개 4시간봉을 완성 일봉으로 집계해 느린 레짐의 저회전 추세 PAPER 후보를 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TypedDict

from backend.app.build_identity import git_commit
from backend.app.research import probability_of_backtest_overfitting
from scripts.research_multiyear_trend_tournament import (
    BASE_EXECUTION_COST_BPS,
    BINANCE_FUTURES_FUNDING_URL,
    BINANCE_FUTURES_KLINES_URL,
    STRESS_EXECUTION_COST_BPS,
    FundingRate,
    apply_actual_funding_and_costs,
    candidate_fingerprint,
    load_public_research_data,
)
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_public_trend_candidates import DEFAULT_SYMBOLS, _parse_date
from scripts.research_slow_regime_trend_tournament import (
    EMBARGO_MS,
    SlowTrendOutcome,
    SlowTrendSpec,
    _context_snapshots,
    _development_profile,
    _eligible,
    _features,
    _fold_returns,
    _oos_assessment,
    _profile,
    _rank_key,
    _rankable,
    _split,
    _symbol_outcomes,
    apply_portfolio_limits,
)

SOURCE_INTERVAL_MINUTES = 240
SOURCE_INTERVAL_MS = SOURCE_INTERVAL_MINUTES * 60_000
DAILY_INTERVAL_MINUTES = 1_440
DAILY_INTERVAL_MS = DAILY_INTERVAL_MINUTES * 60_000
EXPECTED_SOURCE_BARS_PER_DAY = DAILY_INTERVAL_MS // SOURCE_INTERVAL_MS
MINIMUM_RESEARCH_DAYS = 1_095
DEVELOPMENT_FOLD_COUNT = 6
MINIMUM_WALK_FORWARD_FOLD_SAMPLE = 8
MINIMUM_EVALUABLE_FOLDS = 5
MINIMUM_POSITIVE_FOLDS = 4
MAXIMUM_FINALISTS = 5
HYPOTHESIS_ID = "HYP-128-DAILY-REGIME-WALK-FORWARD-TREND-TOURNAMENT"
PREREGISTRATION_PATH = "docs/research/HYP-128-daily-regime-walk-forward-trend.md"


class _SpecParameters(TypedDict):
    lookback: int
    momentum: float
    rank_threshold: float
    breadth: float
    adx: float
    relative_volume: float
    retest_band: float
    stop_buffer: float
    tp1_r: float
    tp2_r: float
    cooldown_hours: int
    slow_alignment: bool


def _spec(
    family_key: str,
    family: str,
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
        candidate_id=f"T128_{family_key}_{side_policy}_{style}",
        family=family,
        interval_minutes=DAILY_INTERVAL_MINUTES,
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
    families: tuple[tuple[str, str, str, Mapping[str, _SpecParameters]], ...] = (
        (
            "CHANNEL_BREAKOUT_1D",
            "DAILY_CHANNEL_BREAKOUT_SLOW_REGIME",
            "CHANNEL_BREAKOUT",
            {
                "BALANCED": {
                    "lookback": 20,
                    "momentum": 0.015,
                    "rank_threshold": 0.60,
                    "breadth": 0.55,
                    "adx": 15,
                    "relative_volume": 0.50,
                    "retest_band": 0.0,
                    "stop_buffer": 0.25,
                    "tp1_r": 1.5,
                    "tp2_r": 4.0,
                    "cooldown_hours": 72,
                    "slow_alignment": False,
                },
                "SELECTIVE": {
                    "lookback": 55,
                    "momentum": 0.040,
                    "rank_threshold": 0.75,
                    "breadth": 0.60,
                    "adx": 21,
                    "relative_volume": 0.80,
                    "retest_band": 0.0,
                    "stop_buffer": 0.40,
                    "tp1_r": 2.0,
                    "tp2_r": 5.5,
                    "cooldown_hours": 120,
                    "slow_alignment": True,
                },
            },
        ),
        (
            "BREAKOUT_RETEST_1D",
            "DAILY_BREAKOUT_FIRST_RETEST_SLOW_REGIME",
            "BREAKOUT_RETEST",
            {
                "BALANCED": {
                    "lookback": 20,
                    "momentum": 0.012,
                    "rank_threshold": 0.60,
                    "breadth": 0.55,
                    "adx": 14,
                    "relative_volume": 0.45,
                    "retest_band": 0.40,
                    "stop_buffer": 0.25,
                    "tp1_r": 1.5,
                    "tp2_r": 4.0,
                    "cooldown_hours": 72,
                    "slow_alignment": False,
                },
                "SELECTIVE": {
                    "lookback": 55,
                    "momentum": 0.030,
                    "rank_threshold": 0.75,
                    "breadth": 0.60,
                    "adx": 20,
                    "relative_volume": 0.70,
                    "retest_band": 0.22,
                    "stop_buffer": 0.38,
                    "tp1_r": 2.0,
                    "tp2_r": 5.0,
                    "cooldown_hours": 96,
                    "slow_alignment": True,
                },
            },
        ),
        (
            "FIRST_PULLBACK_1D",
            "DAILY_EARLY_TREND_FIRST_PULLBACK_SLOW_REGIME",
            "FIRST_PULLBACK_RECLAIM",
            {
                "BALANCED": {
                    "lookback": 10,
                    "momentum": 0.010,
                    "rank_threshold": 0.60,
                    "breadth": 0.55,
                    "adx": 14,
                    "relative_volume": 0.40,
                    "retest_band": 0.45,
                    "stop_buffer": 0.25,
                    "tp1_r": 1.5,
                    "tp2_r": 4.0,
                    "cooldown_hours": 72,
                    "slow_alignment": False,
                },
                "SELECTIVE": {
                    "lookback": 6,
                    "momentum": 0.025,
                    "rank_threshold": 0.75,
                    "breadth": 0.60,
                    "adx": 19,
                    "relative_volume": 0.65,
                    "retest_band": 0.25,
                    "stop_buffer": 0.35,
                    "tp1_r": 2.0,
                    "tp2_r": 5.0,
                    "cooldown_hours": 96,
                    "slow_alignment": True,
                },
            },
        ),
        (
            "ICHIMOKU_PULLBACK_1D",
            "DAILY_ICHIMOKU_PULLBACK_SLOW_REGIME",
            "ICHIMOKU_PULLBACK_CONTINUATION",
            {
                "BALANCED": {
                    "lookback": 52,
                    "momentum": 0.012,
                    "rank_threshold": 0.60,
                    "breadth": 0.55,
                    "adx": 14,
                    "relative_volume": 0.45,
                    "retest_band": 0.35,
                    "stop_buffer": 0.28,
                    "tp1_r": 1.5,
                    "tp2_r": 4.0,
                    "cooldown_hours": 72,
                    "slow_alignment": False,
                },
                "SELECTIVE": {
                    "lookback": 52,
                    "momentum": 0.030,
                    "rank_threshold": 0.75,
                    "breadth": 0.60,
                    "adx": 20,
                    "relative_volume": 0.70,
                    "retest_band": 0.18,
                    "stop_buffer": 0.42,
                    "tp1_r": 2.0,
                    "tp2_r": 5.0,
                    "cooldown_hours": 120,
                    "slow_alignment": True,
                },
            },
        ),
        (
            "EMA_PULLBACK_1D",
            "DAILY_EMA_PULLBACK_CONTINUATION_SLOW_REGIME",
            "EMA_PULLBACK_CONTINUATION",
            {
                "BALANCED": {
                    "lookback": 20,
                    "momentum": 0.010,
                    "rank_threshold": 0.60,
                    "breadth": 0.55,
                    "adx": 14,
                    "relative_volume": 0.40,
                    "retest_band": 0.40,
                    "stop_buffer": 0.25,
                    "tp1_r": 1.5,
                    "tp2_r": 4.0,
                    "cooldown_hours": 72,
                    "slow_alignment": False,
                },
                "SELECTIVE": {
                    "lookback": 50,
                    "momentum": 0.025,
                    "rank_threshold": 0.75,
                    "breadth": 0.60,
                    "adx": 19,
                    "relative_volume": 0.65,
                    "retest_band": 0.22,
                    "stop_buffer": 0.38,
                    "tp1_r": 2.0,
                    "tp2_r": 5.0,
                    "cooldown_hours": 96,
                    "slow_alignment": True,
                },
            },
        ),
    )
    output: list[SlowTrendSpec] = []
    for family_key, family, setup_kind, styles in families:
        for side_policy in ("LONG", "SHORT", "BOTH"):
            for style, parameters in styles.items():
                output.append(
                    _spec(
                        family_key,
                        family,
                        setup_kind,
                        side_policy,
                        style,
                        **parameters,
                    )
                )
    return tuple(output)


PREREGISTERED_DAILY_CANDIDATES = _candidate_specs()


def aggregate_four_hour_to_daily(
    rows: Sequence[IntradayBar],
) -> tuple[IntradayBar, ...]:
    grouped: dict[int, list[IntradayBar]] = {}
    for row in rows:
        if row.interval_minutes != SOURCE_INTERVAL_MINUTES:
            raise ValueError("일봉 집계 입력은 완성 4시간봉이어야 합니다.")
        day_open_ts_ms = row.open_ts_ms - row.open_ts_ms % DAILY_INTERVAL_MS
        grouped.setdefault(day_open_ts_ms, []).append(row)
    output: list[IntradayBar] = []
    for day_open_ts_ms, values in sorted(grouped.items()):
        ordered = sorted(values, key=lambda row: row.open_ts_ms)
        if len(ordered) != EXPECTED_SOURCE_BARS_PER_DAY:
            continue
        if ordered[0].open_ts_ms != day_open_ts_ms:
            continue
        if any(
            current.open_ts_ms - previous.open_ts_ms != SOURCE_INTERVAL_MS
            for previous, current in zip(ordered, ordered[1:], strict=False)
        ):
            continue
        output.append(
            IntradayBar(
                symbol=ordered[0].symbol,
                interval_minutes=DAILY_INTERVAL_MINUTES,
                open_ts_ms=day_open_ts_ms,
                open=ordered[0].open,
                high=max(row.high for row in ordered),
                low=min(row.low for row in ordered),
                close=ordered[-1].close,
                volume=sum(row.volume for row in ordered),
            )
        )
    return tuple(output)


def _daily_bar_fingerprint(rows: Sequence[IntradayBar]) -> str:
    return hashlib.sha256(
        json.dumps(
            [asdict(row) for row in rows],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def research_daily_tournament(
    daily_bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    specs: Sequence[SlowTrendSpec] = PREREGISTERED_DAILY_CANDIDATES,
) -> tuple[
    dict[str, tuple[SlowTrendOutcome, ...]],
    dict[str, dict[str, float | int]],
]:
    features_by_symbol = {
        symbol: _features(symbol_bars)
        for symbol, symbol_bars in sorted(daily_bars_by_symbol.items())
    }
    snapshots = _context_snapshots(daily_bars_by_symbol, features_by_symbol)
    raw: dict[str, list[SlowTrendOutcome]] = {spec.candidate_id: [] for spec in specs}
    for spec in specs:
        for symbol, symbol_bars in sorted(daily_bars_by_symbol.items()):
            raw[spec.candidate_id].extend(
                _symbol_outcomes(
                    symbol_bars,
                    features_by_symbol[symbol],
                    snapshots,
                    spec,
                )
            )
    output: dict[str, tuple[SlowTrendOutcome, ...]] = {}
    audit: dict[str, dict[str, float | int]] = {}
    for candidate_id, candidate_rows in raw.items():
        selected = apply_portfolio_limits(candidate_rows)
        adjusted: list[SlowTrendOutcome] = []
        total_funding_bps = 0.0
        applied_events = 0
        excluded_credit_bps = 0.0
        excluded_credit_events = 0
        for row in selected:
            revised, funding = apply_actual_funding_and_costs(
                row,
                funding_by_symbol.get(row.symbol, ()),
                bar_interval_ms=DAILY_INTERVAL_MS,
            )
            adjusted.append(revised)
            total_funding_bps += funding.funding_bps
            applied_events += funding.applied_event_count
            excluded_credit_bps += funding.excluded_ambiguous_credit_bps
            excluded_credit_events += funding.excluded_ambiguous_credit_count
        output[candidate_id] = tuple(adjusted)
        audit[candidate_id] = {
            "closed_trade_count": sum(not row.censored for row in adjusted),
            "censored_open_count": sum(row.censored for row in adjusted),
            "applied_funding_event_count": applied_events,
            "net_funding_cashflow_bps": total_funding_bps,
            "excluded_ambiguous_boundary_credit_count": excluded_credit_events,
            "excluded_ambiguous_boundary_credit_bps": excluded_credit_bps,
        }
    return output, audit


def development_walk_forward_stability(
    rows: Sequence[SlowTrendOutcome],
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, object]:
    development_end_ms = start_ms + int((end_ms - start_ms) * 0.70)
    duration_ms = development_end_ms - start_ms
    fold_rows: list[dict[str, object]] = []
    positive_flags: list[bool] = []
    evaluable_flags: list[bool] = []
    for index in range(DEVELOPMENT_FOLD_COUNT):
        fold_start_ms = start_ms + duration_ms * index // DEVELOPMENT_FOLD_COUNT
        fold_end_ms = (
            development_end_ms
            if index == DEVELOPMENT_FOLD_COUNT - 1
            else start_ms + duration_ms * (index + 1) // DEVELOPMENT_FOLD_COUNT
        )
        selected = tuple(
            row
            for row in rows
            if not row.censored
            and row.entry_ts_ms > fold_start_ms + EMBARGO_MS
            and row.exit_ts_ms < fold_end_ms - EMBARGO_MS
        )
        stress = _profile(selected, "stress_net_bps")
        sample_value = stress["sample_size"]
        if not isinstance(sample_value, int):
            raise TypeError("walk-forward 표본 수가 정수가 아닙니다.")
        sample_size = sample_value
        expectation = stress["expectancy_bps"]
        profit_factor = stress["profit_factor"]
        evaluable = sample_size >= MINIMUM_WALK_FORWARD_FOLD_SAMPLE
        positive = (
            evaluable
            and isinstance(expectation, int | float)
            and expectation > 0
            and isinstance(profit_factor, int | float)
            and profit_factor > 1
        )
        evaluable_flags.append(evaluable)
        positive_flags.append(positive)
        fold_rows.append(
            {
                "fold": index + 1,
                "start_ts_ms": fold_start_ms,
                "end_ts_ms": fold_end_ms,
                "stress": stress,
                "evaluable": evaluable,
                "positive": positive,
            }
        )
    evaluable_count = sum(evaluable_flags)
    positive_count = sum(positive_flags)
    latest_two_positive = all(positive_flags[-2:])
    passed = (
        evaluable_count >= MINIMUM_EVALUABLE_FOLDS
        and positive_count >= MINIMUM_POSITIVE_FOLDS
        and latest_two_positive
    )
    return {
        "folds": fold_rows,
        "evaluable_fold_count": evaluable_count,
        "positive_fold_count": positive_count,
        "latest_two_folds_positive": latest_two_positive,
        "stability_pass": passed,
    }


def select_stable_development_candidates(
    development: Mapping[str, Mapping[str, object]],
    walk_forward: Mapping[str, Mapping[str, object]],
    specs: Sequence[SlowTrendSpec] = PREREGISTERED_DAILY_CANDIDATES,
) -> tuple[str, ...]:
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    eligible = sorted(
        (
            candidate_id
            for candidate_id, profile in development.items()
            if _eligible(profile) and walk_forward[candidate_id]["stability_pass"] is True
        ),
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


def _profile_metric(
    profiles: Mapping[str, Mapping[str, object]],
    candidate_id: str,
    section: str,
    metric: str,
) -> object:
    values = profiles[candidate_id][section]
    if not isinstance(values, Mapping):
        raise TypeError(f"{candidate_id} {section} 성과가 객체가 아닙니다.")
    return values[metric]


def build_report(
    source_manifest: Sequence[Mapping[str, object]],
    daily_bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    *,
    start_ms: int,
    end_ms: int,
    specs: Sequence[SlowTrendSpec] = PREREGISTERED_DAILY_CANDIDATES,
) -> dict[str, object]:
    outcomes, funding_audit = research_daily_tournament(
        daily_bars_by_symbol,
        funding_by_symbol,
        specs,
    )
    splits = {
        candidate_id: _split(rows, start_ms=start_ms, end_ms=end_ms)
        for candidate_id, rows in outcomes.items()
    }
    development = {
        candidate_id: _development_profile(parts) for candidate_id, parts in splits.items()
    }
    walk_forward = {
        candidate_id: development_walk_forward_stability(
            rows,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for candidate_id, rows in outcomes.items()
    }
    finalists = select_stable_development_candidates(
        development,
        walk_forward,
        specs,
    )
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
    derived_manifest: list[dict[str, object]] = []
    source_by_symbol = {str(row["symbol"]): row for row in source_manifest}
    for symbol, rows in sorted(daily_bars_by_symbol.items()):
        source = source_by_symbol[symbol]
        derived_manifest.append(
            {
                "symbol": symbol,
                "source_bar_file_sha256": source["bar_file_sha256"],
                "funding_file_sha256": source["funding_file_sha256"],
                "daily_bar_count": len(rows),
                "daily_start_ts_ms": rows[0].open_ts_ms,
                "daily_end_ts_ms": rows[-1].close_ts_ms,
                "derived_daily_sha256": _daily_bar_fingerprint(rows),
            }
        )
    dataset_hash = hashlib.sha256(
        json.dumps(derived_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    ranked_ids = sorted(
        (candidate_id for candidate_id, profile in development.items() if _rankable(profile)),
        key=lambda candidate_id: (*_rank_key(development[candidate_id]), candidate_id),
        reverse=True,
    )
    unranked_ids = sorted(
        candidate_id for candidate_id, profile in development.items() if not _rankable(profile)
    )
    return {
        "schema_version": 1,
        "status": (
            "ADAPTIVE_HISTORICAL_PASS_FORWARD_REQUIRED" if historical_pass else "NOT_PROVEN"
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
            "hyp127_diagnostic_oos_was_inspected": True,
            "independent_future_oos": False,
            "reason": (
                "HYP-127의 진단 OOS 실패를 본 뒤 일봉과 walk-forward gate를 선택했으므로 "
                "마지막 30%도 역사 진단일 뿐 독립 미래표본이 아닙니다."
            ),
        },
        "source": {
            "venue": "BINANCE_USDM",
            "public_only": True,
            "bar_endpoint": BINANCE_FUTURES_KLINES_URL,
            "funding_endpoint": BINANCE_FUTURES_FUNDING_URL,
            "source_bar_interval": "4h",
            "derived_signal_interval": "1d",
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "completed_candles_only": True,
            "dataset_hash": dataset_hash,
            "datasets": derived_manifest,
        },
        "research_basis": [
            {
                "title": "Risks and Returns of Cryptocurrency",
                "url": "https://www.nber.org/papers/w24877",
                "use": "일봉·주봉 time-series momentum을 가설 출처로만 사용",
            },
            {
                "title": "Technical analysis in cryptocurrency markets",
                "url": "https://doi.org/10.1016/j.intfin.2022.101601",
                "use": "일봉 이동평균·돌파와 거래비용 민감도를 가설에 사용",
            },
            {
                "title": "Dynamic time series momentum of cryptocurrencies",
                "url": "https://doi.org/10.1016/j.najef.2021.101428",
                "use": "동적 추세와 변동성 확장 가능성을 가설에 사용",
            },
            {
                "title": "Cryptocurrencies and momentum",
                "url": "https://doi.org/10.1016/j.econlet.2019.03.028",
                "use": "유의하지 않은 반대 연구를 실패 가능성 경계에 사용",
            },
        ],
        "preregistration": {
            "hypothesis_id": HYPOTHESIS_ID,
            "path": PREREGISTRATION_PATH,
            "candidate_count": len(specs),
            "family_count": len({spec.family for spec in specs}),
            "candidate_fingerprint": candidate_fingerprint(specs),
            "candidates": [asdict(spec) for spec in specs],
            "base_execution_cost_bps": BASE_EXECUTION_COST_BPS,
            "stress_execution_cost_bps": STRESS_EXECUTION_COST_BPS,
            "historical_funding_directionally_applied": True,
            "ambiguous_entry_or_exit_day_funding_credit_excluded": True,
            "next_day_open_entry": True,
            "same_day_stop_before_target": True,
            "fixed_maximum_hold": False,
            "censored_open_positions_are_not_scored": True,
            "tp1_fraction": 0.4,
            "maximum_concurrent_positions": 2,
            "maximum_daily_entries": 2,
            "split": "chronological 50% train / 20% validation / 30% diagnostic OOS",
            "embargo_days": 7,
            "walk_forward_fold_count": DEVELOPMENT_FOLD_COUNT,
            "minimum_walk_forward_fold_sample": MINIMUM_WALK_FORWARD_FOLD_SAMPLE,
            "minimum_evaluable_folds": MINIMUM_EVALUABLE_FOLDS,
            "minimum_positive_folds": MINIMUM_POSITIVE_FOLDS,
            "latest_two_folds_must_be_positive": True,
            "thresholds_lowered_after_results": False,
        },
        "funding_cost_audit": funding_audit,
        "development_profiles": development,
        "development_walk_forward": walk_forward,
        "ranking_contract": {
            "minimum_development_closed_trades": 60,
            "minimum_validation_closed_trades": 20,
            "minimum_oos_closed_trades": 30,
            "sparse_candidates_are_not_ranked": True,
            "walk_forward_stability_required_before_selection": True,
            "maximum_distinct_family_finalists": MAXIMUM_FINALISTS,
        },
        "development_ranking_top_10": [
            {
                "rank": index + 1,
                "candidate_id": candidate_id,
                "family": spec_by_id[candidate_id].family,
                "side_policy": spec_by_id[candidate_id].side_policy,
                "eligible": _eligible(development[candidate_id]),
                "walk_forward_stability_pass": walk_forward[candidate_id]["stability_pass"],
                "walk_forward_positive_folds": walk_forward[candidate_id]["positive_fold_count"],
                "validation_stress_expectancy_bps": _profile_metric(
                    development,
                    candidate_id,
                    "validation_stress",
                    "expectancy_bps",
                ),
                "validation_stress_profit_factor": _profile_metric(
                    development,
                    candidate_id,
                    "validation_stress",
                    "profit_factor",
                ),
                "development_stress_expectancy_bps": _profile_metric(
                    development,
                    candidate_id,
                    "stress",
                    "expectancy_bps",
                ),
            }
            for index, candidate_id in enumerate(ranked_ids[:10])
        ],
        "unranked_insufficient_sample": unranked_ids,
        "selected_on_train_validation_and_walk_forward": list(finalists),
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
            "real_orders_remain_forbidden": True,
        },
        "limitations": [
            "현재 생존 대형 12종목 중심이라 survivorship bias가 있습니다.",
            "일봉은 과거 실행가능 bid·ask 깊이와 봉 내부 가격순서를 제공하지 않습니다.",
            "같은 일봉에서 stop과 target이 모두 닿으면 stop을 먼저 적용했습니다.",
            "실제 펀딩은 적용했지만 실행비용은 BASE·STRESS 고정 왕복비용입니다.",
            "이 연구는 HYP-127 결과 뒤 설계한 적응 연구라 미래 독립표본이 아닙니다.",
            "역사 통과도 실제 bid·ask SHADOW와 독립 미래표본 전에는 수익성 증거가 아닙니다.",
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
        default=Path("data/multiyear-trend-public-v1"),
    )
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_ms = _parse_date(args.start)
    end_ms = _parse_date(args.end)
    if end_ms - start_ms < MINIMUM_RESEARCH_DAYS * DAILY_INTERVAL_MS:
        raise ValueError(f"일봉 추세 토너먼트 기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다.")
    symbols = tuple(args.symbol or DEFAULT_SYMBOLS)
    four_hour_bars, funding, source_manifest = load_public_research_data(
        symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        cache_dir=args.cache_dir,
    )
    daily_bars = {
        symbol: aggregate_four_hour_to_daily(rows) for symbol, rows in four_hour_bars.items()
    }
    if any(len(rows) < 1_000 for rows in daily_bars.values()):
        raise RuntimeError("일봉 연구표본이 1,000개 미만인 종목이 있습니다.")
    report = build_report(
        source_manifest,
        daily_bars,
        funding,
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
