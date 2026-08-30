# 작은 손실과 드문 큰 추세 수익을 목표로 하는 무제한 추적청산 PAPER 후보를 검증한다.

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import fmean, median
from typing import cast

from backend.app.build_identity import git_commit
from backend.app.research import probability_of_backtest_overfitting
from scripts.research_daily_regime_trend_tournament import (
    development_walk_forward_stability,
)
from scripts.research_multiyear_trend_tournament import (
    BASE_EXECUTION_COST_BPS,
    BINANCE_FUTURES_FUNDING_URL,
    BINANCE_FUTURES_KLINES_URL,
    FundingRate,
    funding_adjustment,
    load_public_research_data,
)
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_public_trend_candidates import DEFAULT_SYMBOLS, _parse_date
from scripts.research_slow_regime_trend_tournament import (
    MAXIMUM_CONCURRENT_POSITIONS,
    MAXIMUM_DAILY_ENTRIES,
    MarketSnapshot,
    SlowFeatures,
    SlowTrendOutcome,
    _allowed_directions,
    _context_snapshots,
    _development_profile,
    _eligible,
    _features,
    _fold_returns,
    _oos_assessment,
    _rank_key,
    _rankable,
    _regime_allows,
    _split,
    apply_portfolio_limits,
)
from scripts.research_volume_confirmed_early_trend_tournament import (
    BASE_RISK_BUDGET_BPS,
    INTERVAL_MINUTES,
    INTERVAL_MS,
    PREREGISTERED_VOLUME_TREND_CANDIDATES,
    VolumeIndicators,
    VolumeTrendSpec,
    _directional_spread,
    _slow_spec,
    build_volume_indicators,
    volume_setup,
)

STRESS_EXECUTION_COST_BPS = 25.0
MINIMUM_RESEARCH_DAYS = 1_095
MAXIMUM_FINALISTS = 5
HYPOTHESIS_ID = "HYP-131-ASYMMETRIC-TREND-RUNNER-TOURNAMENT"
PREREGISTRATION_PATH = "docs/research/HYP-131-asymmetric-trend-runner.md"


@dataclass(frozen=True, slots=True)
class RunnerExitSpec:
    exit_id: str
    activation_r: float
    chandelier_lookback: int
    chandelier_atr_multiplier: float


@dataclass(frozen=True, slots=True)
class AsymmetricTrendSpec:
    candidate_id: str
    family: str
    entry: VolumeTrendSpec
    exit: RunnerExitSpec

    @property
    def cooldown_ms(self) -> int:
        return self.entry.cooldown_ms


@dataclass(frozen=True, slots=True)
class AsymmetricTrendOutcome:
    candidate_id: str
    family: str
    symbol: str
    side: str
    signal_ts_ms: int
    entry_ts_ms: int
    exit_ts_ms: int
    holding_minutes: int
    exit_reason: str
    activation_ts_ms: int | None
    entry: float
    initial_stop: float
    final_stop: float
    activation_price: float
    exit_price: float | None
    gross_r: float | None
    maximum_favorable_r: float
    maximum_adverse_r: float
    gross_bps: float | None
    base_net_bps: float | None
    stress_net_bps: float | None
    score: float
    regime_breadth: float
    relative_rank: float
    censored: bool


RUNNER_EXITS = (
    RunnerExitSpec("CHAND22_ATR3", 1.0, 22, 3.0),
    RunnerExitSpec("CHAND22_ATR4", 1.0, 22, 4.0),
)


def _runner_specs() -> tuple[AsymmetricTrendSpec, ...]:
    output: list[AsymmetricTrendSpec] = []
    for entry in PREREGISTERED_VOLUME_TREND_CANDIDATES:
        entry_key = entry.candidate_id.removeprefix("T130_")
        for exit_spec in RUNNER_EXITS:
            output.append(
                AsymmetricTrendSpec(
                    candidate_id=f"T131_{entry_key}_{exit_spec.exit_id}",
                    family=entry.family,
                    entry=entry,
                    exit=exit_spec,
                )
            )
    return tuple(output)


PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES = _runner_specs()


def asymmetric_candidate_fingerprint(
    specs: Sequence[AsymmetricTrendSpec] = PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES,
) -> str:
    return hashlib.sha256(
        json.dumps(
            [asdict(spec) for spec in specs],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _return_bps(side: str, entry: float, exit_price: float) -> float:
    direction = 1 if side == "LONG" else -1
    return (exit_price - entry) / entry * 10_000 * direction


def _next_chandelier_stop(
    rows: Sequence[IntradayBar],
    feature_rows: Sequence[SlowFeatures | None],
    *,
    cursor: int,
    entry_index: int,
    direction: int,
    current_stop: float,
    lookback: int,
    atr_multiplier: float,
) -> float:
    """현재 봉을 제외한 완성봉만으로 다음 실행가능 추적손절을 계산한다."""
    if cursor <= entry_index:
        return current_stop
    completed = rows[max(entry_index, cursor - lookback) : cursor]
    features = feature_rows[cursor - 1]
    if not completed or features is None or features.atr <= 0:
        return current_stop
    if direction > 0:
        candidate = max(row.high for row in completed) - (features.atr * atr_multiplier)
        return max(current_stop, candidate)
    candidate = min(row.low for row in completed) + features.atr * atr_multiplier
    return min(current_stop, candidate)


def _stop_fill(bar: IntradayBar, *, direction: int, stop: float) -> float:
    """손절가를 넘어선 시가 갭은 손절가보다 불리한 시가로 체결한다."""
    return min(bar.open, stop) if direction > 0 else max(bar.open, stop)


def _simulate_runner(
    rows: Sequence[IntradayBar],
    feature_rows: Sequence[SlowFeatures | None],
    *,
    index: int,
    direction: int,
    structural_stop: float,
    signal_atr: float,
    score: float,
    breadth: float,
    relative_rank: float,
    spec: AsymmetricTrendSpec,
) -> AsymmetricTrendOutcome | None:
    entry_index = index + 1
    if entry_index >= len(rows):
        return None
    entry = rows[entry_index].open
    risk = (entry - structural_stop) * direction
    risk_atr = risk / signal_atr if signal_atr > 0 else math.inf
    if risk <= 0 or not 0.65 <= risk_atr <= 4.0:
        return None
    side = "LONG" if direction > 0 else "SHORT"
    activation_price = entry + direction * risk * spec.exit.activation_r
    current_stop = structural_stop
    activation_ts_ms: int | None = None
    maximum_favorable_r = 0.0
    maximum_adverse_r = 0.0
    for cursor in range(entry_index, len(rows)):
        if activation_ts_ms is not None:
            current_stop = _next_chandelier_stop(
                rows,
                feature_rows,
                cursor=cursor,
                entry_index=entry_index,
                direction=direction,
                current_stop=current_stop,
                lookback=spec.exit.chandelier_lookback,
                atr_multiplier=spec.exit.chandelier_atr_multiplier,
            )
        bar = rows[cursor]
        stop_hit = bar.low <= current_stop if direction > 0 else bar.high >= current_stop
        if stop_hit:
            exit_price = _stop_fill(bar, direction=direction, stop=current_stop)
            gross_bps = _return_bps(side, entry, exit_price)
            return AsymmetricTrendOutcome(
                candidate_id=spec.candidate_id,
                family=spec.family,
                symbol=rows[index].symbol,
                side=side,
                signal_ts_ms=rows[index].close_ts_ms,
                entry_ts_ms=rows[entry_index].open_ts_ms,
                exit_ts_ms=bar.close_ts_ms,
                holding_minutes=(cursor - entry_index + 1) * INTERVAL_MINUTES,
                exit_reason=(
                    "CHANDELIER_TRAIL" if activation_ts_ms is not None else "INITIAL_STOP"
                ),
                activation_ts_ms=activation_ts_ms,
                entry=entry,
                initial_stop=structural_stop,
                final_stop=current_stop,
                activation_price=activation_price,
                exit_price=exit_price,
                gross_r=gross_bps / (risk / entry * 10_000),
                maximum_favorable_r=maximum_favorable_r,
                maximum_adverse_r=maximum_adverse_r,
                gross_bps=gross_bps,
                base_net_bps=gross_bps - BASE_EXECUTION_COST_BPS,
                stress_net_bps=gross_bps - STRESS_EXECUTION_COST_BPS,
                score=score,
                regime_breadth=breadth,
                relative_rank=relative_rank,
                censored=False,
            )
        favorable = (bar.high - entry) / risk if direction > 0 else (entry - bar.low) / risk
        adverse = (entry - bar.low) / risk if direction > 0 else (bar.high - entry) / risk
        maximum_favorable_r = max(maximum_favorable_r, favorable)
        maximum_adverse_r = max(maximum_adverse_r, adverse)
        activation_hit = (
            bar.high >= activation_price if direction > 0 else bar.low <= activation_price
        )
        if activation_ts_ms is None and activation_hit:
            activation_ts_ms = bar.close_ts_ms
    last = rows[-1]
    return AsymmetricTrendOutcome(
        candidate_id=spec.candidate_id,
        family=spec.family,
        symbol=rows[index].symbol,
        side=side,
        signal_ts_ms=rows[index].close_ts_ms,
        entry_ts_ms=rows[entry_index].open_ts_ms,
        exit_ts_ms=last.close_ts_ms,
        holding_minutes=(len(rows) - entry_index) * INTERVAL_MINUTES,
        exit_reason="CENSORED_OPEN",
        activation_ts_ms=activation_ts_ms,
        entry=entry,
        initial_stop=structural_stop,
        final_stop=current_stop,
        activation_price=activation_price,
        exit_price=None,
        gross_r=None,
        maximum_favorable_r=maximum_favorable_r,
        maximum_adverse_r=maximum_adverse_r,
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
    indicators: VolumeIndicators,
    snapshots: Sequence[MarketSnapshot],
    spec: AsymmetricTrendSpec,
) -> list[AsymmetricTrendOutcome]:
    snapshot_times = [snapshot.close_ts_ms for snapshot in snapshots]
    output: list[AsymmetricTrendOutcome] = []
    cooldown_until = 0
    entry_spec = spec.entry
    start = max(205, entry_spec.lookback + entry_spec.obv_slow + 2)
    symbol = rows[0].symbol
    slow_spec = _slow_spec(entry_spec)
    for index in range(start, len(rows) - 1):
        features = feature_rows[index]
        if features is None or rows[index].open_ts_ms < cooldown_until:
            continue
        if (
            features.adx < entry_spec.adx_minimum
            or features.relative_volume < entry_spec.relative_volume_minimum
        ):
            continue
        snapshot_index = (
            bisect.bisect_right(
                snapshot_times,
                rows[index].close_ts_ms,
            )
            - 1
        )
        if snapshot_index < 0:
            continue
        snapshot = snapshots[snapshot_index]
        relative_rank = snapshot.rank_by_symbol.get(symbol)
        if relative_rank is None:
            continue
        for direction in _allowed_directions(entry_spec.side_policy):
            if not _regime_allows(snapshot, symbol, direction, slow_spec):
                continue
            ready, structural_stop = volume_setup(
                rows,
                feature_rows,
                indicators,
                index=index,
                direction=direction,
                spec=entry_spec,
            )
            if not ready or structural_stop is None:
                continue
            rank_strength = relative_rank if direction > 0 else 1 - relative_rank
            spread = _directional_spread(indicators.spread[index], direction) or 0.0
            score = (
                abs(features.momentum_72h) * 100
                + features.adx / 50
                + features.relative_volume
                + rank_strength * 2
                + spread * 100
                + abs(snapshot.breadth - 0.5) * 4
            )
            outcome = _simulate_runner(
                rows,
                feature_rows,
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


def _apply_account_risk_funding_and_costs(
    outcome: AsymmetricTrendOutcome,
    rates: Sequence[FundingRate],
    spec: AsymmetricTrendSpec,
) -> tuple[AsymmetricTrendOutcome, dict[str, float | int]]:
    risk_distance_bps = abs(outcome.entry - outcome.initial_stop) / outcome.entry * 10_000
    notional_fraction = min(
        1.0,
        spec.entry.base_risk_budget_bps / max(risk_distance_bps, 1e-12),
    )
    if outcome.censored or outcome.gross_bps is None:
        return outcome, {
            "applied_funding_event_count": 0,
            "net_funding_cashflow_account_bps": 0.0,
            "excluded_ambiguous_boundary_credit_count": 0,
            "excluded_ambiguous_boundary_credit_account_bps": 0.0,
            "notional_fraction": notional_fraction,
        }
    funding = funding_adjustment(
        rates,
        side=outcome.side,
        entry_ts_ms=outcome.entry_ts_ms,
        exit_ts_ms=outcome.exit_ts_ms,
        bar_interval_ms=INTERVAL_MS,
    )
    funded_gross = outcome.gross_bps + funding.funding_bps
    revised = replace(
        outcome,
        gross_bps=funded_gross * notional_fraction,
        base_net_bps=(funded_gross - BASE_EXECUTION_COST_BPS) * notional_fraction,
        stress_net_bps=(funded_gross - STRESS_EXECUTION_COST_BPS) * notional_fraction,
    )
    return revised, {
        "applied_funding_event_count": funding.applied_event_count,
        "net_funding_cashflow_account_bps": funding.funding_bps * notional_fraction,
        "excluded_ambiguous_boundary_credit_count": (funding.excluded_ambiguous_credit_count),
        "excluded_ambiguous_boundary_credit_account_bps": (
            funding.excluded_ambiguous_credit_bps * notional_fraction
        ),
        "notional_fraction": notional_fraction,
    }


def research_asymmetric_trend_tournament(
    bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    specs: Sequence[AsymmetricTrendSpec] = PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES,
) -> tuple[
    dict[str, tuple[AsymmetricTrendOutcome, ...]],
    dict[str, dict[str, float | int]],
]:
    features_by_symbol = {
        symbol: _features(rows) for symbol, rows in sorted(bars_by_symbol.items())
    }
    indicator_cache = {
        (symbol, spec.entry.obv_fast, spec.entry.obv_slow): build_volume_indicators(
            bars_by_symbol[symbol],
            fast=spec.entry.obv_fast,
            slow=spec.entry.obv_slow,
        )
        for symbol in sorted(bars_by_symbol)
        for spec in specs
    }
    snapshots = _context_snapshots(bars_by_symbol, features_by_symbol)
    raw: dict[str, list[AsymmetricTrendOutcome]] = {spec.candidate_id: [] for spec in specs}
    for spec in specs:
        for symbol, rows in sorted(bars_by_symbol.items()):
            raw[spec.candidate_id].extend(
                _symbol_outcomes(
                    rows,
                    features_by_symbol[symbol],
                    indicator_cache[(symbol, spec.entry.obv_fast, spec.entry.obv_slow)],
                    snapshots,
                    spec,
                )
            )
    output: dict[str, tuple[AsymmetricTrendOutcome, ...]] = {}
    audit: dict[str, dict[str, float | int]] = {}
    specs_by_id = {spec.candidate_id: spec for spec in specs}
    for candidate_id, candidate_rows in raw.items():
        selected = cast(
            tuple[AsymmetricTrendOutcome, ...],
            apply_portfolio_limits(cast(Sequence[SlowTrendOutcome], candidate_rows)),
        )
        adjusted: list[AsymmetricTrendOutcome] = []
        funding_events = 0
        funding_cashflow = 0.0
        excluded_events = 0
        excluded_credit = 0.0
        notionals: list[float] = []
        for outcome in selected:
            revised, row_audit = _apply_account_risk_funding_and_costs(
                outcome,
                funding_by_symbol.get(outcome.symbol, ()),
                specs_by_id[candidate_id],
            )
            adjusted.append(revised)
            funding_events += int(row_audit["applied_funding_event_count"])
            funding_cashflow += float(row_audit["net_funding_cashflow_account_bps"])
            excluded_events += int(row_audit["excluded_ambiguous_boundary_credit_count"])
            excluded_credit += float(row_audit["excluded_ambiguous_boundary_credit_account_bps"])
            notionals.append(float(row_audit["notional_fraction"]))
        output[candidate_id] = tuple(adjusted)
        audit[candidate_id] = {
            "raw_intent_count": len(candidate_rows),
            "selected_trade_count": len(adjusted),
            "closed_trade_count": sum(not row.censored for row in adjusted),
            "censored_open_count": sum(row.censored for row in adjusted),
            "applied_funding_event_count": funding_events,
            "net_funding_cashflow_account_bps": funding_cashflow,
            "excluded_ambiguous_boundary_credit_count": excluded_events,
            "excluded_ambiguous_boundary_credit_account_bps": excluded_credit,
            "mean_notional_fraction": fmean(notionals) if notionals else 0.0,
            "maximum_notional_fraction": max(notionals, default=0.0),
        }
    return output, audit


def _skewness(values: Sequence[float]) -> float | None:
    if len(values) < 3:
        return None
    mean = fmean(values)
    variance = fmean((value - mean) ** 2 for value in values)
    if variance <= 0:
        return 0.0
    return fmean((value - mean) ** 3 for value in values) / variance**1.5


def _positive_skew_profile(
    rows: Sequence[AsymmetricTrendOutcome],
) -> dict[str, object]:
    closed = [
        row
        for row in rows
        if not row.censored and row.stress_net_bps is not None and row.gross_r is not None
    ]
    stress = [float(row.stress_net_bps) for row in closed]
    gross_r = [float(row.gross_r) for row in closed]
    positive = sorted((value for value in stress if value > 0), reverse=True)
    top_count = max(1, math.ceil(len(stress) * 0.10)) if stress else 0
    positive_total = sum(positive)
    return {
        "sample_size": len(closed),
        "stress_return_skewness": _skewness(stress),
        "mean_gross_r": fmean(gross_r) if gross_r else None,
        "median_gross_r": median(gross_r) if gross_r else None,
        "maximum_winner_gross_r": max(gross_r, default=None),
        "minimum_loser_gross_r": min(gross_r, default=None),
        "top_decile_positive_contribution_share": (
            sum(positive[:top_count]) / positive_total if positive_total > 0 else None
        ),
        "activation_count": sum(row.activation_ts_ms is not None for row in closed),
        "initial_stop_count": sum(row.exit_reason == "INITIAL_STOP" for row in closed),
        "trailing_stop_count": sum(row.exit_reason == "CHANDELIER_TRAIL" for row in closed),
        "median_holding_minutes": (
            median(row.holding_minutes for row in closed) if closed else None
        ),
        "median_winner_holding_minutes": (
            median(
                row.holding_minutes
                for row in closed
                if row.stress_net_bps is not None and row.stress_net_bps > 0
            )
            if any(row.stress_net_bps is not None and row.stress_net_bps > 0 for row in closed)
            else None
        ),
        "median_loser_holding_minutes": (
            median(
                row.holding_minutes
                for row in closed
                if row.stress_net_bps is not None and row.stress_net_bps <= 0
            )
            if any(row.stress_net_bps is not None and row.stress_net_bps <= 0 for row in closed)
            else None
        ),
    }


def select_stable_asymmetric_candidates(
    development: Mapping[str, Mapping[str, object]],
    walk_forward: Mapping[str, Mapping[str, object]],
    specs: Sequence[AsymmetricTrendSpec] = PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES,
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
    bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    dataset_manifest: Sequence[Mapping[str, object]],
    *,
    start_ms: int,
    end_ms: int,
    specs: Sequence[AsymmetricTrendSpec] = PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES,
) -> dict[str, object]:
    outcomes, funding_audit = research_asymmetric_trend_tournament(
        bars_by_symbol,
        funding_by_symbol,
        specs,
    )
    splits = {
        candidate_id: _split(
            cast(Sequence[SlowTrendOutcome], rows),
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for candidate_id, rows in outcomes.items()
    }
    development = {
        candidate_id: _development_profile(parts) for candidate_id, parts in splits.items()
    }
    walk_forward = {
        candidate_id: development_walk_forward_stability(
            cast(Sequence[SlowTrendOutcome], rows),
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for candidate_id, rows in outcomes.items()
    }
    finalists = select_stable_asymmetric_candidates(development, walk_forward, specs)
    development_end_ms = start_ms + int((end_ms - start_ms) * 0.70)
    pbo = probability_of_backtest_overfitting(
        _fold_returns(
            cast(Mapping[str, Sequence[SlowTrendOutcome]], outcomes),
            start_ms=start_ms,
            development_end_ms=development_end_ms,
        )
    )
    oos: dict[str, dict[str, object]] = {}
    for candidate_id in finalists:
        assessment = _oos_assessment(
            candidate_id,
            splits[candidate_id],
            trials=len(specs),
            global_pbo=pbo,
        )
        oos_rows = cast(
            Sequence[AsymmetricTrendOutcome],
            splits[candidate_id]["oos"],
        )
        skew_profile = _positive_skew_profile(oos_rows)
        stress = assessment["stress"]
        if not isinstance(stress, Mapping):
            raise TypeError("OOS STRESS profile이 객체가 아닙니다.")
        payoff = stress["payoff_ratio"]
        skewness = skew_profile["stress_return_skewness"]
        maximum_winner = skew_profile["maximum_winner_gross_r"]
        asymmetry_gates = {
            "oos_stress_payoff_at_least_1_50": (isinstance(payoff, int | float) and payoff >= 1.50),
            "oos_stress_return_skewness_positive": (
                isinstance(skewness, int | float) and skewness > 0
            ),
            "oos_maximum_winner_at_least_3r": (
                isinstance(maximum_winner, int | float) and maximum_winner >= 3.0
            ),
        }
        base_gates = assessment["robustness_gates"]
        if not isinstance(base_gates, Mapping):
            raise TypeError("OOS robustness gate가 객체가 아닙니다.")
        combined_gates = {**base_gates, **asymmetry_gates}
        assessment["base_robustness_pass"] = assessment["adaptive_historical_robustness_pass"]
        assessment["positive_skew_profile"] = skew_profile
        assessment["robustness_gates"] = combined_gates
        assessment["adaptive_historical_robustness_pass"] = all(
            bool(value) for value in combined_gates.values()
        )
        oos[candidate_id] = assessment
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
        candidate_id for candidate_id, profile in development.items() if not _rankable(profile)
    )
    datasets = list(dataset_manifest)
    dataset_hash = hashlib.sha256(
        json.dumps(datasets, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
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
            "hyp130_results_were_inspected": True,
            "independent_future_oos": False,
            "reason": (
                "HYP-130의 고정 익절 근접 실패를 본 뒤 청산을 바꾼 적응 역사 연구라서 "
                "마지막 30%도 독립 미래표본이 아닙니다."
            ),
        },
        "source": {
            "venue": "BINANCE_USDM",
            "public_only": True,
            "bar_endpoint": BINANCE_FUTURES_KLINES_URL,
            "funding_endpoint": BINANCE_FUTURES_FUNDING_URL,
            "bar_interval": "4h",
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "completed_candles_only": True,
            "dataset_hash": dataset_hash,
            "datasets": datasets,
        },
        "research_basis": [
            {
                "title": "A Century of Evidence on Trend-Following Investing",
                "url": "https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf",
                "use": "작은 손실을 제한하고 드문 장기 추세를 보유하는 가설 경계",
            },
            {
                "title": "Trend Following in Focus",
                "url": "https://www.aqr.com/-/media/AQR/Documents/Whitepapers/Trend-Following-in-Focus_September-2018.pdf",
                "use": "소수의 큰 추세가 다수의 작은 손실을 상쇄하는 양의 비대칭 가설",
            },
            {
                "title": "Time Series Momentum",
                "url": "https://doi.org/10.1016/j.jfineco.2011.11.003",
                "use": "추세 지속과 시간순 검증의 1차 연구 근거",
            },
            {
                "title": "Trend-following Strategies for Crypto Investors",
                "url": "https://www.monash.edu/__data/assets/pdf_file/0011/3744821/Trend-following-Strategies-for-Crypto-Investors.pdf",
                "use": "암호자산 일봉 추세와 거래비용·변동성 조절의 제한사항",
            },
            {
                "title": "Chandelier Exit",
                "url": "https://www.tradingview.com/support/solutions/43000773013-chandelier-exit/",
                "use": "고점·저점과 ATR 배수로 추세를 따라가는 추적손절 공식",
            },
        ],
        "preregistration": {
            "hypothesis_id": HYPOTHESIS_ID,
            "path": PREREGISTRATION_PATH,
            "candidate_count": len(specs),
            "entry_family_count": len({spec.family for spec in specs}),
            "exit_variant_count": len({spec.exit.exit_id for spec in specs}),
            "candidate_fingerprint": asymmetric_candidate_fingerprint(specs),
            "candidates": [asdict(spec) for spec in specs],
            "no_fixed_take_profit": True,
            "no_fixed_maximum_hold": True,
            "no_partial_take_profit": True,
            "initial_stop_never_widens": True,
            "trail_uses_previous_completed_bars_only": True,
            "gap_through_stop_uses_worse_open": True,
            "same_bar_initial_stop_before_activation": True,
            "censored_open_positions_are_not_scored": True,
            "base_execution_cost_bps": BASE_EXECUTION_COST_BPS,
            "stress_execution_cost_bps": STRESS_EXECUTION_COST_BPS,
            "historical_funding_directionally_applied": True,
            "base_risk_budget_bps_per_trade": BASE_RISK_BUDGET_BPS,
            "maximum_concurrent_positions": MAXIMUM_CONCURRENT_POSITIONS,
            "maximum_daily_entries": MAXIMUM_DAILY_ENTRIES,
            "split": "chronological 50% train / 20% validation / 30% diagnostic OOS",
            "walk_forward_fold_count": 6,
            "thresholds_lowered_after_results": False,
        },
        "funding_cost_risk_audit": funding_audit,
        "development_profiles": development,
        "development_positive_skew_profiles": {
            candidate_id: _positive_skew_profile(
                cast(
                    Sequence[AsymmetricTrendOutcome],
                    (*splits[candidate_id]["train"], *splits[candidate_id]["validation"]),
                )
            )
            for candidate_id in outcomes
        },
        "development_walk_forward": walk_forward,
        "ranking_contract": {
            "minimum_development_closed_trades": 60,
            "minimum_validation_closed_trades": 20,
            "minimum_oos_closed_trades": 30,
            "walk_forward_stability_required_before_selection": True,
            "sparse_candidates_are_not_ranked": True,
            "maximum_distinct_entry_family_finalists": MAXIMUM_FINALISTS,
            "oos_stress_payoff_ratio_minimum": 1.50,
            "oos_positive_skew_required": True,
            "oos_maximum_winner_gross_r_minimum": 3.0,
        },
        "development_ranking_top_10": [
            {
                "rank": index + 1,
                "candidate_id": candidate_id,
                "family": spec_by_id[candidate_id].family,
                "side_policy": spec_by_id[candidate_id].entry.side_policy,
                "style": spec_by_id[candidate_id].entry.style,
                "exit_id": spec_by_id[candidate_id].exit.exit_id,
                "eligible": _eligible(development[candidate_id]),
                "walk_forward_stability_pass": walk_forward[candidate_id]["stability_pass"],
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
        "selection_bias": {"candidate_trials": len(specs), "pbo": pbo},
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
            "이 연구는 HYP-130 결과를 본 뒤 만든 적응 역사검증입니다.",
            "현재 생존 대형 12종목 중심이라 survivorship bias가 있습니다.",
            "4시간봉은 과거 실행가능 bid·ask 깊이와 봉 내부 가격순서를 제공하지 않습니다.",
            "실제 펀딩은 적용했지만 실행비용은 BASE·STRESS 고정 왕복비용입니다.",
            "양의 비대칭은 수익 보장이 아니며 긴 연속 손실과 drawdown을 만들 수 있습니다.",
            "역사 통과도 실제 호가 기반 미래 PAPER SHADOW를 대신하지 않습니다.",
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
    if end_ms - start_ms < MINIMUM_RESEARCH_DAYS * 86_400_000:
        raise ValueError(
            f"비대칭 추세 토너먼트 기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다."
        )
    symbols = tuple(args.symbol or DEFAULT_SYMBOLS)
    bars, funding, manifest = load_public_research_data(
        symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        cache_dir=args.cache_dir,
    )
    report = build_report(
        bars,
        funding,
        manifest,
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
