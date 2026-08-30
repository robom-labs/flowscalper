# 거래량 확인을 결합한 4시간 추세 초입·첫 눌림 PAPER 후보를 비용 후 검증한다.

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import fmean

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
    SlowTrendSpec,
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
    _simulate,
    _split,
    apply_portfolio_limits,
)

INTERVAL_MINUTES = 240
INTERVAL_MS = INTERVAL_MINUTES * 60_000
STRESS_EXECUTION_COST_BPS = 25.0
MINIMUM_RESEARCH_DAYS = 1_095
MAXIMUM_FINALISTS = 5
BASE_RISK_BUDGET_BPS = 40.0
HYPOTHESIS_ID = "HYP-130-VOLUME-CONFIRMED-EARLY-TREND-TOURNAMENT"
PREREGISTRATION_PATH = "docs/research/HYP-130-volume-confirmed-early-trend.md"


@dataclass(frozen=True, slots=True)
class VolumeTrendSpec:
    candidate_id: str
    family: str
    setup_kind: str
    side_policy: str
    style: str
    lookback: int
    obv_fast: int
    obv_slow: int
    obv_band_fraction: float
    confirmation_bars: int
    channel_width_maximum: float
    filter_return: float
    recent_trigger_max_age: int
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
    base_risk_budget_bps: float = BASE_RISK_BUDGET_BPS

    @property
    def cooldown_ms(self) -> int:
        return self.cooldown_hours * 3_600_000


@dataclass(frozen=True, slots=True)
class VolumeIndicators:
    obv: tuple[float, ...]
    spread: tuple[float | None, ...]


def _spec(
    family_key: str,
    family: str,
    setup_kind: str,
    side_policy: str,
    style: str,
    **parameters: int | float | bool,
) -> VolumeTrendSpec:
    return VolumeTrendSpec(
        candidate_id=f"T130_{family_key}_{side_policy}_{style}",
        family=family,
        setup_kind=setup_kind,
        side_policy=side_policy,
        style=style,
        lookback=int(parameters["lookback"]),
        obv_fast=int(parameters["obv_fast"]),
        obv_slow=int(parameters["obv_slow"]),
        obv_band_fraction=float(parameters["obv_band_fraction"]),
        confirmation_bars=int(parameters["confirmation_bars"]),
        channel_width_maximum=float(parameters["channel_width_maximum"]),
        filter_return=float(parameters["filter_return"]),
        recent_trigger_max_age=int(parameters["recent_trigger_max_age"]),
        momentum_72h_minimum=float(parameters["momentum_72h_minimum"]),
        rank_threshold=float(parameters["rank_threshold"]),
        breadth_threshold=float(parameters["breadth_threshold"]),
        adx_minimum=float(parameters["adx_minimum"]),
        relative_volume_minimum=float(parameters["relative_volume_minimum"]),
        retest_band_atr=float(parameters["retest_band_atr"]),
        stop_buffer_atr=float(parameters["stop_buffer_atr"]),
        tp1_r=float(parameters["tp1_r"]),
        tp2_r=float(parameters["tp2_r"]),
        cooldown_hours=int(parameters["cooldown_hours"]),
        require_slow_alignment=bool(parameters["require_slow_alignment"]),
    )


def _candidate_specs() -> tuple[VolumeTrendSpec, ...]:
    common_balanced: dict[str, int | float | bool] = {
        "obv_fast": 6,
        "obv_slow": 24,
        "obv_band_fraction": 0.0025,
        "confirmation_bars": 1,
        "channel_width_maximum": 0.06,
        "filter_return": 0.01,
        "recent_trigger_max_age": 12,
        "momentum_72h_minimum": 0.01,
        "rank_threshold": 0.55,
        "breadth_threshold": 0.53,
        "adx_minimum": 14,
        "relative_volume_minimum": 0.55,
        "retest_band_atr": 0.45,
        "stop_buffer_atr": 0.25,
        "tp1_r": 1.5,
        "tp2_r": 3.5,
        "cooldown_hours": 16,
        "require_slow_alignment": False,
    }
    common_selective: dict[str, int | float | bool] = {
        "obv_fast": 12,
        "obv_slow": 48,
        "obv_band_fraction": 0.005,
        "confirmation_bars": 2,
        "channel_width_maximum": 0.04,
        "filter_return": 0.02,
        "recent_trigger_max_age": 18,
        "momentum_72h_minimum": 0.025,
        "rank_threshold": 0.70,
        "breadth_threshold": 0.58,
        "adx_minimum": 18,
        "relative_volume_minimum": 0.80,
        "retest_band_atr": 0.25,
        "stop_buffer_atr": 0.35,
        "tp1_r": 2.0,
        "tp2_r": 4.5,
        "cooldown_hours": 24,
        "require_slow_alignment": True,
    }
    families = (
        (
            "OBV_MA_CROSS_4H",
            "FOUR_HOUR_OBV_MA_EARLY_TREND",
            "OBV_MA_CROSS",
            18,
        ),
        (
            "OBV_PRICE_BREAKOUT_4H",
            "FOUR_HOUR_OBV_CONFIRMED_PRICE_BREAKOUT",
            "OBV_PRICE_BREAKOUT",
            24,
        ),
        (
            "SQUEEZE_BREAKOUT_4H",
            "FOUR_HOUR_VOLUME_CONFIRMED_SQUEEZE_BREAKOUT",
            "SQUEEZE_BREAKOUT",
            18,
        ),
        (
            "FILTER_TURN_4H",
            "FOUR_HOUR_FILTER_TURN_FROM_TREND_EXTREME",
            "FILTER_TURN",
            12,
        ),
        (
            "OBV_FIRST_PULLBACK_4H",
            "FOUR_HOUR_OBV_TRIGGERED_FIRST_PULLBACK",
            "OBV_FIRST_PULLBACK",
            18,
        ),
    )
    output: list[VolumeTrendSpec] = []
    for family_key, family, setup_kind, balanced_lookback in families:
        for side_policy in ("LONG", "SHORT", "BOTH"):
            output.append(
                _spec(
                    family_key,
                    family,
                    setup_kind,
                    side_policy,
                    "BALANCED",
                    **{**common_balanced, "lookback": balanced_lookback},
                )
            )
            output.append(
                _spec(
                    family_key,
                    family,
                    setup_kind,
                    side_policy,
                    "SELECTIVE",
                    **{
                        **common_selective,
                        "lookback": max(24, balanced_lookback * 2),
                    },
                )
            )
    return tuple(output)


PREREGISTERED_VOLUME_TREND_CANDIDATES = _candidate_specs()


def volume_trend_candidate_fingerprint(
    specs: Sequence[VolumeTrendSpec] = PREREGISTERED_VOLUME_TREND_CANDIDATES,
) -> str:
    return hashlib.sha256(
        json.dumps(
            [asdict(spec) for spec in specs],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _slow_spec(spec: VolumeTrendSpec) -> SlowTrendSpec:
    return SlowTrendSpec(
        candidate_id=spec.candidate_id,
        family=spec.family,
        interval_minutes=INTERVAL_MINUTES,
        setup_kind=spec.setup_kind,
        side_policy=spec.side_policy,
        style=spec.style,
        lookback=spec.lookback,
        momentum_72h_minimum=spec.momentum_72h_minimum,
        rank_threshold=spec.rank_threshold,
        breadth_threshold=spec.breadth_threshold,
        adx_minimum=spec.adx_minimum,
        relative_volume_minimum=spec.relative_volume_minimum,
        retest_band_atr=spec.retest_band_atr,
        stop_buffer_atr=spec.stop_buffer_atr,
        tp1_r=spec.tp1_r,
        tp2_r=spec.tp2_r,
        cooldown_hours=spec.cooldown_hours,
        require_slow_alignment=spec.require_slow_alignment,
    )


def build_volume_indicators(
    rows: Sequence[IntradayBar],
    *,
    fast: int,
    slow: int,
) -> VolumeIndicators:
    if fast <= 0 or slow <= fast:
        raise ValueError("OBV 평균 길이는 0 < fast < slow여야 합니다.")
    obv: list[float] = []
    running = 0.0
    for index, row in enumerate(rows):
        if index > 0:
            if row.close > rows[index - 1].close:
                running += row.volume
            elif row.close < rows[index - 1].close:
                running -= row.volume
        obv.append(running)
    spread: list[float | None] = [None] * len(rows)
    for index in range(slow - 1, len(rows)):
        fast_mean = fmean(obv[index - fast + 1 : index + 1])
        slow_mean = fmean(obv[index - slow + 1 : index + 1])
        volume_scale = sum(row.volume for row in rows[index - slow + 1 : index + 1])
        spread[index] = (fast_mean - slow_mean) / max(volume_scale, 1e-12)
    return VolumeIndicators(tuple(obv), tuple(spread))


def _directional_spread(value: float | None, direction: int) -> float | None:
    return None if value is None else value * direction


def _confirmed_cross(
    spread: Sequence[float | None],
    *,
    index: int,
    direction: int,
    threshold: float,
    confirmation_bars: int,
) -> bool:
    start = index - confirmation_bars + 1
    before = start - 1
    if before < 0:
        return False
    current_values = [
        _directional_spread(spread[cursor], direction)
        for cursor in range(start, index + 1)
    ]
    prior = _directional_spread(spread[before], direction)
    return (
        prior is not None
        and prior <= threshold
        and all(value is not None and value > threshold for value in current_values)
    )


def _recent_cross_age(
    spread: Sequence[float | None],
    *,
    index: int,
    direction: int,
    threshold: float,
    maximum_age: int,
) -> int | None:
    first = max(1, index - maximum_age)
    for cursor in range(index, first - 1, -1):
        current = _directional_spread(spread[cursor], direction)
        previous = _directional_spread(spread[cursor - 1], direction)
        if (
            current is not None
            and previous is not None
            and current > threshold
            and previous <= threshold
        ):
            return index - cursor
    return None


def _structural_stop(
    rows: Sequence[IntradayBar],
    *,
    index: int,
    direction: int,
    atr: float,
    buffer_atr: float,
) -> float:
    window = rows[max(0, index - 2) : index + 1]
    if direction > 0:
        return min(row.low for row in window) - atr * buffer_atr
    return max(row.high for row in window) + atr * buffer_atr


def volume_setup(
    rows: Sequence[IntradayBar],
    feature_rows: Sequence[SlowFeatures | None],
    indicators: VolumeIndicators,
    *,
    index: int,
    direction: int,
    spec: VolumeTrendSpec,
) -> tuple[bool, float | None]:
    if index < max(spec.lookback, spec.obv_slow) + 2:
        return False, None
    current = rows[index]
    previous = rows[index - 1]
    features = feature_rows[index]
    previous_features = feature_rows[index - 1]
    if features is None or previous_features is None:
        return False, None
    directional_spread = _directional_spread(indicators.spread[index], direction)
    if directional_spread is None:
        return False, None
    history = rows[index - spec.lookback : index]
    atr = features.atr
    ready = False
    if spec.setup_kind == "OBV_MA_CROSS":
        crossed = _confirmed_cross(
            indicators.spread,
            index=index,
            direction=direction,
            threshold=spec.obv_band_fraction,
            confirmation_bars=spec.confirmation_bars,
        )
        ready = crossed and (
            current.close > features.ema20 > features.ema50
            if direction > 0
            else current.close < features.ema20 < features.ema50
        )
    elif spec.setup_kind == "OBV_PRICE_BREAKOUT":
        prior_obv = indicators.obv[index - spec.lookback : index]
        ready = directional_spread > spec.obv_band_fraction and (
            (
                current.close > max(row.high for row in history)
                and indicators.obv[index] > max(prior_obv)
                and current.close > current.open
            )
            if direction > 0
            else (
                current.close < min(row.low for row in history)
                and indicators.obv[index] < min(prior_obv)
                and current.close < current.open
            )
        )
    elif spec.setup_kind == "SQUEEZE_BREAKOUT":
        upper = max(row.high for row in history)
        lower = min(row.low for row in history)
        width = (upper - lower) / max((upper + lower) / 2, 1e-12)
        ready = (
            width <= spec.channel_width_maximum
            and directional_spread > spec.obv_band_fraction
            and (
                (current.close > upper and current.close > current.open)
                if direction > 0
                else (current.close < lower and current.close < current.open)
            )
        )
    elif spec.setup_kind == "FILTER_TURN":
        close_values = [row.close for row in history]
        if direction > 0:
            extreme = min(close_values)
            previous_near_extreme = previous.close <= extreme * (
                1 + spec.filter_return * 0.25
            )
            ready = (
                previous_near_extreme
                and current.close > extreme * (1 + spec.filter_return)
                and current.close > previous.high
                and directional_spread > 0
            )
        else:
            extreme = max(close_values)
            previous_near_extreme = previous.close >= extreme * (
                1 - spec.filter_return * 0.25
            )
            ready = (
                previous_near_extreme
                and current.close < extreme * (1 - spec.filter_return)
                and current.close < previous.low
                and directional_spread > 0
            )
    elif spec.setup_kind == "OBV_FIRST_PULLBACK":
        cross_age = _recent_cross_age(
            indicators.spread,
            index=index - 1,
            direction=direction,
            threshold=spec.obv_band_fraction,
            maximum_age=spec.recent_trigger_max_age,
        )
        if direction > 0:
            ready = (
                cross_age is not None
                and directional_spread > spec.obv_band_fraction
                and previous.low
                <= previous_features.ema20 + atr * spec.retest_band_atr
                and previous.close >= previous_features.ema50
                and current.close > features.ema20
                and current.close > previous.high
                and current.close > current.open
            )
        else:
            ready = (
                cross_age is not None
                and directional_spread > spec.obv_band_fraction
                and previous.high
                >= previous_features.ema20 - atr * spec.retest_band_atr
                and previous.close <= previous_features.ema50
                and current.close < features.ema20
                and current.close < previous.low
                and current.close < current.open
            )
    else:
        raise ValueError(f"알 수 없는 거래량 추세 setup입니다. {spec.setup_kind}")
    if not ready:
        return False, None
    return True, _structural_stop(
        rows,
        index=index,
        direction=direction,
        atr=atr,
        buffer_atr=spec.stop_buffer_atr,
    )


def _symbol_outcomes(
    rows: Sequence[IntradayBar],
    feature_rows: Sequence[SlowFeatures | None],
    indicators: VolumeIndicators,
    snapshots: Sequence[MarketSnapshot],
    spec: VolumeTrendSpec,
) -> list[SlowTrendOutcome]:
    snapshot_times = [snapshot.close_ts_ms for snapshot in snapshots]
    output: list[SlowTrendOutcome] = []
    cooldown_until = 0
    start = max(205, spec.lookback + spec.obv_slow + 2)
    symbol = rows[0].symbol
    slow_spec = _slow_spec(spec)
    for index in range(start, len(rows) - 1):
        features = feature_rows[index]
        if features is None or rows[index].open_ts_ms < cooldown_until:
            continue
        if (
            features.adx < spec.adx_minimum
            or features.relative_volume < spec.relative_volume_minimum
        ):
            continue
        snapshot_index = bisect.bisect_right(
            snapshot_times,
            rows[index].close_ts_ms,
        ) - 1
        if snapshot_index < 0:
            continue
        snapshot = snapshots[snapshot_index]
        relative_rank = snapshot.rank_by_symbol.get(symbol)
        if relative_rank is None:
            continue
        for direction in _allowed_directions(spec.side_policy):
            if not _regime_allows(snapshot, symbol, direction, slow_spec):
                continue
            ready, structural_stop = volume_setup(
                rows,
                feature_rows,
                indicators,
                index=index,
                direction=direction,
                spec=spec,
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
            outcome = _simulate(
                rows,
                index=index,
                direction=direction,
                structural_stop=structural_stop,
                signal_atr=features.atr,
                score=score,
                breadth=snapshot.breadth,
                relative_rank=relative_rank,
                spec=slow_spec,
            )
            if outcome is not None:
                output.append(outcome)
                cooldown_until = outcome.exit_ts_ms + spec.cooldown_ms
            break
    return output


def _apply_account_risk_funding_and_costs(
    outcome: SlowTrendOutcome,
    rates: Sequence[FundingRate],
    spec: VolumeTrendSpec,
) -> tuple[SlowTrendOutcome, dict[str, float | int]]:
    risk_distance_bps = abs(outcome.entry - outcome.stop) / outcome.entry * 10_000
    notional_fraction = min(
        1.0,
        spec.base_risk_budget_bps / max(risk_distance_bps, 1e-12),
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
        base_net_bps=(funded_gross - BASE_EXECUTION_COST_BPS)
        * notional_fraction,
        stress_net_bps=(funded_gross - STRESS_EXECUTION_COST_BPS)
        * notional_fraction,
    )
    return revised, {
        "applied_funding_event_count": funding.applied_event_count,
        "net_funding_cashflow_account_bps": funding.funding_bps
        * notional_fraction,
        "excluded_ambiguous_boundary_credit_count": (
            funding.excluded_ambiguous_credit_count
        ),
        "excluded_ambiguous_boundary_credit_account_bps": (
            funding.excluded_ambiguous_credit_bps * notional_fraction
        ),
        "notional_fraction": notional_fraction,
    }


def research_volume_trend_tournament(
    bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    specs: Sequence[VolumeTrendSpec] = PREREGISTERED_VOLUME_TREND_CANDIDATES,
) -> tuple[
    dict[str, tuple[SlowTrendOutcome, ...]],
    dict[str, dict[str, float | int]],
]:
    features_by_symbol = {
        symbol: _features(rows) for symbol, rows in sorted(bars_by_symbol.items())
    }
    indicator_cache = {
        (symbol, spec.obv_fast, spec.obv_slow): build_volume_indicators(
            bars_by_symbol[symbol],
            fast=spec.obv_fast,
            slow=spec.obv_slow,
        )
        for symbol in sorted(bars_by_symbol)
        for spec in specs
    }
    snapshots = _context_snapshots(bars_by_symbol, features_by_symbol)
    raw: dict[str, list[SlowTrendOutcome]] = {spec.candidate_id: [] for spec in specs}
    for spec in specs:
        for symbol, rows in sorted(bars_by_symbol.items()):
            raw[spec.candidate_id].extend(
                _symbol_outcomes(
                    rows,
                    features_by_symbol[symbol],
                    indicator_cache[(symbol, spec.obv_fast, spec.obv_slow)],
                    snapshots,
                    spec,
                )
            )
    output: dict[str, tuple[SlowTrendOutcome, ...]] = {}
    audit: dict[str, dict[str, float | int]] = {}
    specs_by_id = {spec.candidate_id: spec for spec in specs}
    for candidate_id, candidate_rows in raw.items():
        selected = apply_portfolio_limits(candidate_rows)
        adjusted: list[SlowTrendOutcome] = []
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
            funding_cashflow += float(
                row_audit["net_funding_cashflow_account_bps"]
            )
            excluded_events += int(
                row_audit["excluded_ambiguous_boundary_credit_count"]
            )
            excluded_credit += float(
                row_audit["excluded_ambiguous_boundary_credit_account_bps"]
            )
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


def select_stable_volume_trend_candidates(
    development: Mapping[str, Mapping[str, object]],
    walk_forward: Mapping[str, Mapping[str, object]],
    specs: Sequence[VolumeTrendSpec] = PREREGISTERED_VOLUME_TREND_CANDIDATES,
) -> tuple[str, ...]:
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    eligible = sorted(
        (
            candidate_id
            for candidate_id, profile in development.items()
            if _eligible(profile)
            and walk_forward[candidate_id]["stability_pass"] is True
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
    specs: Sequence[VolumeTrendSpec] = PREREGISTERED_VOLUME_TREND_CANDIDATES,
) -> dict[str, object]:
    outcomes, funding_audit = research_volume_trend_tournament(
        bars_by_symbol,
        funding_by_symbol,
        specs,
    )
    splits = {
        candidate_id: _split(rows, start_ms=start_ms, end_ms=end_ms)
        for candidate_id, rows in outcomes.items()
    }
    development = {
        candidate_id: _development_profile(parts)
        for candidate_id, parts in splits.items()
    }
    development_end_ms = start_ms + int((end_ms - start_ms) * 0.70)
    walk_forward = {
        candidate_id: development_walk_forward_stability(
            rows,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for candidate_id, rows in outcomes.items()
    }
    finalists = select_stable_volume_trend_candidates(
        development,
        walk_forward,
        specs,
    )
    pbo = probability_of_backtest_overfitting(
        _fold_returns(
            outcomes,
            start_ms=start_ms,
            development_end_ms=development_end_ms,
        )
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
    datasets = list(dataset_manifest)
    dataset_hash = hashlib.sha256(
        json.dumps(datasets, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
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
            "hyp127_hyp128_hyp129_results_were_inspected": True,
            "independent_future_oos": False,
            "reason": (
                "앞선 추세 연구와 외부 논문을 본 뒤 만든 적응 연구이므로 마지막 30%도 "
                "독립 미래표본이 아닙니다."
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
                "title": "Are simple technical trading rules profitable in bitcoin markets?",
                "url": "https://doi.org/10.1016/j.iref.2024.05.003",
                "use": "OBV·이동평균·filter·channel 규칙과 비용·OOS·다중검정 경계",
            },
            {
                "title": "High frequency momentum trading with cryptocurrencies",
                "url": "https://doi.org/10.1016/j.ribaf.2019.101176",
                "use": "시간·횡단면 신호 모멘텀과 상태별 강건성 가설",
            },
            {
                "title": "Technical trading and cryptocurrencies",
                "url": "https://doi.org/10.1007/s10479-019-03357-1",
                "use": "channel·moving-average 규칙과 데이터 스누핑·OOS 경계",
            },
            {
                "title": "Technical analysis in cryptocurrency markets",
                "url": "https://doi.org/10.1016/j.intfin.2022.101601",
                "use": "거래비용과 시장상태에 따른 성과 변화를 제한사항으로 사용",
            },
        ],
        "secondary_public_video_metadata": [
            {
                "title": "Bollinger Band Squeeze Trading Strategy",
                "url": "https://www.youtube.com/watch?v=O7UAwPSQ7Kw",
                "use": "Band Width 수축과 OBV 확인 조합의 2차 가설 근거",
                "full_video_reviewed": False,
                "external_performance_imported": False,
            },
            {
                "title": "I Coded a Supertrend Strategy Backtest",
                "url": "https://www.youtube.com/watch?v=Yl5WCVMllC4",
                "use": "다중시간 추세 확인 개념의 2차 대조 근거",
                "full_video_reviewed": False,
                "external_performance_imported": False,
            },
            {
                "title": "Donchian Channel Strategy for Crypto",
                "url": "https://www.youtube.com/watch?v=DX03SapMezE",
                "use": "구체 규칙 부족과 기존 채널 후보 중복으로 새 후보에서 제외",
                "full_video_reviewed": False,
                "external_performance_imported": False,
            },
        ],
        "preregistration": {
            "hypothesis_id": HYPOTHESIS_ID,
            "path": PREREGISTRATION_PATH,
            "candidate_count": len(specs),
            "family_count": len({spec.family for spec in specs}),
            "candidate_fingerprint": volume_trend_candidate_fingerprint(specs),
            "candidates": [asdict(spec) for spec in specs],
            "base_execution_cost_bps": BASE_EXECUTION_COST_BPS,
            "stress_execution_cost_bps": STRESS_EXECUTION_COST_BPS,
            "historical_funding_directionally_applied": True,
            "next_bar_open_entry": True,
            "same_bar_stop_before_target": True,
            "fixed_maximum_hold": False,
            "censored_open_positions_are_not_scored": True,
            "maximum_concurrent_positions": MAXIMUM_CONCURRENT_POSITIONS,
            "maximum_daily_entries": MAXIMUM_DAILY_ENTRIES,
            "base_risk_budget_bps_per_trade": BASE_RISK_BUDGET_BPS,
            "maximum_notional_fraction": 1.0,
            "split": "chronological 50% train / 20% validation / 30% diagnostic OOS",
            "walk_forward_fold_count": 6,
            "thresholds_lowered_after_results": False,
        },
        "funding_cost_risk_audit": funding_audit,
        "development_profiles": development,
        "development_walk_forward": walk_forward,
        "ranking_contract": {
            "minimum_development_closed_trades": 60,
            "minimum_validation_closed_trades": 20,
            "minimum_oos_closed_trades": 30,
            "walk_forward_stability_required_before_selection": True,
            "sparse_candidates_are_not_ranked": True,
            "maximum_distinct_family_finalists": MAXIMUM_FINALISTS,
        },
        "development_ranking_top_10": [
            {
                "rank": index + 1,
                "candidate_id": candidate_id,
                "family": spec_by_id[candidate_id].family,
                "side_policy": spec_by_id[candidate_id].side_policy,
                "style": spec_by_id[candidate_id].style,
                "eligible": _eligible(development[candidate_id]),
                "walk_forward_stability_pass": walk_forward[candidate_id][
                    "stability_pass"
                ],
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
            "현재 생존 대형 12종목 중심이라 survivorship bias가 있습니다.",
            "4시간봉은 과거 실행가능 bid·ask 깊이와 봉 내부 가격순서를 제공하지 않습니다.",
            "같은 봉에서 stop과 target이 모두 닿으면 stop을 먼저 적용했습니다.",
            "실제 펀딩은 적용했지만 실행비용은 BASE·STRESS 고정 왕복비용입니다.",
            "외부 논문의 Bitcoin·과거기간 성과는 현재 12종목 수익성 증거가 아닙니다.",
            (
                "공개 영상은 검색결과 설명 메타데이터만 대조했고 전체 영상 검토나 "
                "성과 수입은 하지 않았습니다."
            ),
            "높은 빈도는 작은 비용 변화에 취약하므로 STRESS와 실제 호가 미래검증이 필수입니다.",
            "이 연구는 적응 역사검증이므로 통과해도 독립 미래표본이 아닙니다.",
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
            f"거래량 추세 토너먼트 기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다."
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
