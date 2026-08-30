# 연속 상승 상태와 위험감쇠를 결합한 주별 모멘텀 PAPER 후보를 고정 비용으로 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import fmean, pstdev

from backend.app.build_identity import git_commit
from backend.app.research import probability_of_backtest_overfitting
from scripts.research_daily_regime_trend_tournament import (
    DAILY_INTERVAL_MINUTES,
    DAILY_INTERVAL_MS,
    aggregate_four_hour_to_daily,
    development_walk_forward_stability,
)
from scripts.research_multiyear_trend_tournament import (
    BASE_EXECUTION_COST_BPS,
    BINANCE_FUTURES_FUNDING_URL,
    BINANCE_FUTURES_KLINES_URL,
    STRESS_EXECUTION_COST_BPS,
    FundingRate,
    funding_adjustment,
    load_public_research_data,
)
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_public_trend_candidates import DEFAULT_SYMBOLS, _parse_date
from scripts.research_slow_regime_trend_tournament import (
    SlowTrendOutcome,
    SlowTrendSpec,
    _development_profile,
    _eligible,
    _fold_returns,
    _oos_assessment,
    _rank_key,
    _rankable,
    _simulate,
    _split,
)

WEEKLY_INTERVAL_MINUTES = 7 * DAILY_INTERVAL_MINUTES
WEEKLY_INTERVAL_MS = 7 * DAILY_INTERVAL_MS
MONDAY_OFFSET_MS = 4 * DAILY_INTERVAL_MS
MINIMUM_RESEARCH_DAYS = 1_095
MAXIMUM_CONCURRENT_POSITIONS = 2
MAXIMUM_DAILY_ENTRIES = 2
MAXIMUM_FINALISTS = 5
BASE_RISK_BUDGET_BPS = 40.0
TARGET_WEEKLY_VOLATILITY = 0.08
VOLATILITY_LOOKBACK_WEEKS = 8
MINIMUM_VOLATILITY_SCALE = 0.25
HYPOTHESIS_ID = "HYP-129-UP-UP-STATE-RISK-CAPPED-MOMENTUM-TOURNAMENT"
PREREGISTRATION_PATH = "docs/research/HYP-129-up-up-state-risk-capped-momentum.md"


@dataclass(frozen=True, slots=True)
class StateMomentumSpec:
    candidate_id: str
    family: str
    selection_kind: str
    formation_weeks: int
    state_policy: str
    risk_style: str
    selected_count: int
    minimum_absolute_momentum: float
    stop_lookback_days: int
    stop_buffer_atr: float
    tp1_r: float
    tp2_r: float
    base_risk_budget_bps: float
    target_weekly_volatility: float

    @property
    def is_negative_control(self) -> bool:
        return self.state_policy == "NON_UP_UP"


@dataclass(frozen=True, slots=True)
class WeeklyContext:
    week_open_ts_ms: int
    week_close_ts_ms: int
    current_market_four_week_return: float
    previous_market_four_week_return: float
    up_up: bool
    momentum_2w_by_symbol: Mapping[str, float]
    momentum_4w_by_symbol: Mapping[str, float]
    rank_2w_by_symbol: Mapping[str, float]
    rank_4w_by_symbol: Mapping[str, float]
    weekly_volatility_by_symbol: Mapping[str, float]
    slow_aligned_long_symbols: frozenset[str]

    @property
    def breadth_2w(self) -> float:
        values = tuple(self.momentum_2w_by_symbol.values())
        return sum(value > 0 for value in values) / len(values)


@dataclass(frozen=True, slots=True)
class CandidateTrade:
    outcome: SlowTrendOutcome
    risk_scale: float
    momentum: float


def _candidate_specs() -> tuple[StateMomentumSpec, ...]:
    families = (
        (
            "XSMOM_2W_LONG",
            "TWO_WEEK_CROSS_SECTIONAL_WINNERS_LONG",
            "WINNERS_LONG",
            2,
            2,
            0.01,
            7,
            0.25,
            1.5,
            4.0,
        ),
        (
            "XSMOM_4W_LONG",
            "FOUR_WEEK_CROSS_SECTIONAL_WINNERS_LONG",
            "WINNERS_LONG",
            4,
            2,
            0.02,
            10,
            0.30,
            1.5,
            4.5,
        ),
        (
            "XSMOM_2W_WML",
            "TWO_WEEK_WINNER_MINUS_LOSER",
            "WINNER_LOSER",
            2,
            2,
            0.01,
            7,
            0.25,
            1.5,
            3.5,
        ),
        (
            "TSMOM_2W",
            "TWO_WEEK_TIME_SERIES_MOMENTUM",
            "TIME_SERIES",
            2,
            2,
            0.02,
            7,
            0.25,
            1.5,
            4.0,
        ),
        (
            "XSMOM_2W_SLOW_ALIGN",
            "TWO_WEEK_WINNERS_LONG_SLOW_ALIGNMENT",
            "WINNERS_LONG_SLOW_ALIGN",
            2,
            2,
            0.01,
            10,
            0.30,
            1.5,
            4.5,
        ),
    )
    output: list[StateMomentumSpec] = []
    for (
        family_key,
        family,
        selection_kind,
        formation_weeks,
        selected_count,
        minimum_absolute_momentum,
        stop_lookback_days,
        stop_buffer_atr,
        tp1_r,
        tp2_r,
    ) in families:
        for state_policy in ("UP_UP", "ALL_REGIMES", "NON_UP_UP"):
            for risk_style in ("FIXED_RISK", "VOL_CAPPED"):
                output.append(
                    StateMomentumSpec(
                        candidate_id=(
                            f"T129_{family_key}_{state_policy}_{risk_style}"
                        ),
                        family=family,
                        selection_kind=selection_kind,
                        formation_weeks=formation_weeks,
                        state_policy=state_policy,
                        risk_style=risk_style,
                        selected_count=selected_count,
                        minimum_absolute_momentum=minimum_absolute_momentum,
                        stop_lookback_days=stop_lookback_days,
                        stop_buffer_atr=stop_buffer_atr,
                        tp1_r=tp1_r,
                        tp2_r=tp2_r,
                        base_risk_budget_bps=BASE_RISK_BUDGET_BPS,
                        target_weekly_volatility=TARGET_WEEKLY_VOLATILITY,
                    )
                )
    return tuple(output)


PREREGISTERED_STATE_MOMENTUM_CANDIDATES = _candidate_specs()


def state_momentum_candidate_fingerprint(
    specs: Sequence[StateMomentumSpec] = PREREGISTERED_STATE_MOMENTUM_CANDIDATES,
) -> str:
    return hashlib.sha256(
        json.dumps(
            [asdict(spec) for spec in specs],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def aggregate_daily_to_weekly(
    rows: Sequence[IntradayBar],
) -> tuple[IntradayBar, ...]:
    grouped: dict[int, list[IntradayBar]] = defaultdict(list)
    for row in rows:
        if row.interval_minutes != DAILY_INTERVAL_MINUTES:
            raise ValueError("주봉 집계 입력은 완성 일봉이어야 합니다.")
        week_open = (
            (row.open_ts_ms - MONDAY_OFFSET_MS) // WEEKLY_INTERVAL_MS
        ) * WEEKLY_INTERVAL_MS + MONDAY_OFFSET_MS
        grouped[week_open].append(row)
    output: list[IntradayBar] = []
    for week_open, values in sorted(grouped.items()):
        ordered = sorted(values, key=lambda row: row.open_ts_ms)
        if len(ordered) != 7 or ordered[0].open_ts_ms != week_open:
            continue
        if any(
            current.open_ts_ms - previous.open_ts_ms != DAILY_INTERVAL_MS
            for previous, current in zip(ordered, ordered[1:], strict=False)
        ):
            continue
        output.append(
            IntradayBar(
                symbol=ordered[0].symbol,
                interval_minutes=WEEKLY_INTERVAL_MINUTES,
                open_ts_ms=week_open,
                open=ordered[0].open,
                high=max(row.high for row in ordered),
                low=min(row.low for row in ordered),
                close=ordered[-1].close,
                volume=sum(row.volume for row in ordered),
            )
        )
    return tuple(output)


def _rank(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(values, key=lambda symbol: (values[symbol], symbol))
    denominator = max(1, len(ordered) - 1)
    return {symbol: index / denominator for index, symbol in enumerate(ordered)}


def _compound(values: Sequence[float]) -> float:
    result = 1.0
    for value in values:
        result *= 1 + value
    return result - 1


def _ema_last(values: Sequence[float], span: int) -> float:
    if not values:
        raise ValueError("EMA 입력은 비어 있을 수 없습니다.")
    alpha = 2 / (span + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def build_weekly_contexts(
    weekly_by_symbol: Mapping[str, Sequence[IntradayBar]],
) -> tuple[WeeklyContext, ...]:
    if not weekly_by_symbol:
        return ()
    maps = {
        symbol: {row.open_ts_ms: row for row in rows}
        for symbol, rows in sorted(weekly_by_symbol.items())
    }
    common_times = sorted(set.intersection(*(set(rows) for rows in maps.values())))
    if any(
        current - previous != WEEKLY_INTERVAL_MS
        for previous, current in zip(common_times, common_times[1:], strict=False)
    ):
        raise ValueError("공통 주봉 시계열에 빈 주가 있습니다.")
    symbols = tuple(sorted(maps))
    closes = {
        symbol: [maps[symbol][timestamp].close for timestamp in common_times]
        for symbol in symbols
    }
    market_returns: list[float] = [0.0]
    weekly_returns_by_symbol: dict[str, list[float]] = {
        symbol: [0.0] for symbol in symbols
    }
    for index in range(1, len(common_times)):
        returns = {
            symbol: closes[symbol][index] / closes[symbol][index - 1] - 1
            for symbol in symbols
        }
        for symbol, value in returns.items():
            weekly_returns_by_symbol[symbol].append(value)
        market_returns.append(fmean(returns.values()))
    output: list[WeeklyContext] = []
    start_index = max(12, VOLATILITY_LOOKBACK_WEEKS, 5)
    for index in range(start_index, len(common_times)):
        momentum_2w = {
            symbol: closes[symbol][index] / closes[symbol][index - 2] - 1
            for symbol in symbols
        }
        momentum_4w = {
            symbol: closes[symbol][index] / closes[symbol][index - 4] - 1
            for symbol in symbols
        }
        current_market = _compound(market_returns[index - 3 : index + 1])
        previous_market = _compound(market_returns[index - 4 : index])
        weekly_volatility = {
            symbol: pstdev(
                weekly_returns_by_symbol[symbol][
                    index - VOLATILITY_LOOKBACK_WEEKS + 1 : index + 1
                ]
            )
            for symbol in symbols
        }
        slow_aligned = frozenset(
            symbol
            for symbol in symbols
            if closes[symbol][index]
            > _ema_last(closes[symbol][: index + 1], 4)
            > _ema_last(closes[symbol][: index + 1], 12)
        )
        output.append(
            WeeklyContext(
                week_open_ts_ms=common_times[index],
                week_close_ts_ms=common_times[index] + WEEKLY_INTERVAL_MS - 1,
                current_market_four_week_return=current_market,
                previous_market_four_week_return=previous_market,
                up_up=current_market >= 0 and previous_market >= 0,
                momentum_2w_by_symbol=momentum_2w,
                momentum_4w_by_symbol=momentum_4w,
                rank_2w_by_symbol=_rank(momentum_2w),
                rank_4w_by_symbol=_rank(momentum_4w),
                weekly_volatility_by_symbol=weekly_volatility,
                slow_aligned_long_symbols=slow_aligned,
            )
        )
    return tuple(output)


def _state_allows(context: WeeklyContext, policy: str) -> bool:
    if policy == "UP_UP":
        return context.up_up
    if policy == "NON_UP_UP":
        return not context.up_up
    if policy == "ALL_REGIMES":
        return True
    raise ValueError(f"알 수 없는 시장 상태 정책입니다. {policy}")


def _candidate_legs(
    context: WeeklyContext,
    spec: StateMomentumSpec,
) -> tuple[tuple[str, int, float, float], ...]:
    momentum = (
        context.momentum_2w_by_symbol
        if spec.formation_weeks == 2
        else context.momentum_4w_by_symbol
    )
    ranks = (
        context.rank_2w_by_symbol
        if spec.formation_weeks == 2
        else context.rank_4w_by_symbol
    )
    descending = sorted(momentum, key=lambda symbol: (momentum[symbol], symbol), reverse=True)
    ascending = list(reversed(descending))
    legs: list[tuple[str, int, float, float]] = []
    if spec.selection_kind in {"WINNERS_LONG", "WINNERS_LONG_SLOW_ALIGN"}:
        for symbol in descending:
            value = momentum[symbol]
            if value < spec.minimum_absolute_momentum:
                continue
            if (
                spec.selection_kind == "WINNERS_LONG_SLOW_ALIGN"
                and symbol not in context.slow_aligned_long_symbols
            ):
                continue
            legs.append((symbol, 1, value, ranks[symbol]))
            if len(legs) == spec.selected_count:
                break
    elif spec.selection_kind == "WINNER_LOSER":
        winner = next(
            (
                symbol
                for symbol in descending
                if momentum[symbol] >= spec.minimum_absolute_momentum
            ),
            None,
        )
        loser = next(
            (
                symbol
                for symbol in ascending
                if momentum[symbol] <= -spec.minimum_absolute_momentum
            ),
            None,
        )
        if winner is not None:
            legs.append((winner, 1, momentum[winner], ranks[winner]))
        if loser is not None and loser != winner:
            legs.append((loser, -1, momentum[loser], ranks[loser]))
    elif spec.selection_kind == "TIME_SERIES":
        strongest = sorted(
            momentum,
            key=lambda symbol: (abs(momentum[symbol]), symbol),
            reverse=True,
        )
        for symbol in strongest:
            value = momentum[symbol]
            if abs(value) < spec.minimum_absolute_momentum:
                continue
            legs.append((symbol, 1 if value > 0 else -1, value, ranks[symbol]))
            if len(legs) == spec.selected_count:
                break
    else:
        raise ValueError(f"알 수 없는 후보 선발 방식입니다. {spec.selection_kind}")
    return tuple(legs[: spec.selected_count])


def volatility_risk_scale(
    weekly_volatility: float,
    *,
    target_weekly_volatility: float = TARGET_WEEKLY_VOLATILITY,
) -> float:
    if weekly_volatility <= 0:
        return 1.0
    return max(
        MINIMUM_VOLATILITY_SCALE,
        min(1.0, target_weekly_volatility / weekly_volatility),
    )


def _true_range_average(
    rows: Sequence[IntradayBar],
    index: int,
    lookback: int = 14,
) -> float | None:
    if index < lookback:
        return None
    values: list[float] = []
    for cursor in range(index - lookback + 1, index + 1):
        previous_close = rows[cursor - 1].close
        current = rows[cursor]
        values.append(
            max(
                current.high - current.low,
                abs(current.high - previous_close),
                abs(current.low - previous_close),
            )
        )
    return fmean(values)


def _slow_spec(spec: StateMomentumSpec) -> SlowTrendSpec:
    return SlowTrendSpec(
        candidate_id=spec.candidate_id,
        family=spec.family,
        interval_minutes=DAILY_INTERVAL_MINUTES,
        setup_kind="STATE_CONDITIONED_MOMENTUM",
        side_policy="BOTH",
        style=spec.risk_style,
        lookback=spec.stop_lookback_days,
        momentum_72h_minimum=spec.minimum_absolute_momentum,
        rank_threshold=0.0,
        breadth_threshold=0.0,
        adx_minimum=0.0,
        relative_volume_minimum=0.0,
        retest_band_atr=0.0,
        stop_buffer_atr=spec.stop_buffer_atr,
        tp1_r=spec.tp1_r,
        tp2_r=spec.tp2_r,
        cooldown_hours=7 * 24,
        require_slow_alignment=False,
    )


def _candidate_trade(
    rows: Sequence[IntradayBar],
    *,
    signal_index: int,
    direction: int,
    momentum: float,
    rank: float,
    context: WeeklyContext,
    spec: StateMomentumSpec,
) -> CandidateTrade | None:
    atr = _true_range_average(rows, signal_index)
    if atr is None or atr <= 0:
        return None
    history = rows[
        signal_index - spec.stop_lookback_days + 1 : signal_index + 1
    ]
    if len(history) != spec.stop_lookback_days:
        return None
    structural_stop = (
        min(row.low for row in history) - atr * spec.stop_buffer_atr
        if direction > 0
        else max(row.high for row in history) + atr * spec.stop_buffer_atr
    )
    score = (
        abs(momentum) * 100
        + (rank if direction > 0 else 1 - rank) * 2
        + abs(context.current_market_four_week_return) * 10
    )
    outcome = _simulate(
        rows,
        index=signal_index,
        direction=direction,
        structural_stop=structural_stop,
        signal_atr=atr,
        score=score,
        breadth=context.breadth_2w,
        relative_rank=rank,
        spec=_slow_spec(spec),
    )
    if outcome is None:
        return None
    scale = (
        volatility_risk_scale(
            context.weekly_volatility_by_symbol[outcome.symbol],
            target_weekly_volatility=spec.target_weekly_volatility,
        )
        if spec.risk_style == "VOL_CAPPED"
        else 1.0
    )
    return CandidateTrade(outcome=outcome, risk_scale=scale, momentum=momentum)


def apply_state_momentum_portfolio_limits(
    trades: Iterable[CandidateTrade],
) -> tuple[CandidateTrade, ...]:
    selected: list[CandidateTrade] = []
    active: list[tuple[int, str]] = []
    entries_by_day: dict[int, int] = defaultdict(int)
    for trade in sorted(
        trades,
        key=lambda row: (
            row.outcome.entry_ts_ms,
            -row.outcome.score,
            row.outcome.symbol,
        ),
    ):
        outcome = trade.outcome
        active = [
            (exit_ts, symbol)
            for exit_ts, symbol in active
            if exit_ts >= outcome.entry_ts_ms
        ]
        day = outcome.entry_ts_ms // DAILY_INTERVAL_MS
        if len(active) >= MAXIMUM_CONCURRENT_POSITIONS:
            continue
        if entries_by_day[day] >= MAXIMUM_DAILY_ENTRIES:
            continue
        if any(symbol == outcome.symbol for _, symbol in active):
            continue
        selected.append(trade)
        active.append((outcome.exit_ts_ms, outcome.symbol))
        entries_by_day[day] += 1
    return tuple(selected)


def _apply_account_risk_and_costs(
    trade: CandidateTrade,
    rates: Sequence[FundingRate],
    spec: StateMomentumSpec,
) -> tuple[SlowTrendOutcome, dict[str, float | int]]:
    outcome = trade.outcome
    risk_distance_bps = abs(outcome.entry - outcome.stop) / outcome.entry * 10_000
    risk_budget_bps = spec.base_risk_budget_bps * trade.risk_scale
    notional_fraction = min(1.0, risk_budget_bps / max(risk_distance_bps, 1e-12))
    if outcome.censored or outcome.gross_bps is None:
        return outcome, {
            "applied_funding_event_count": 0,
            "net_funding_cashflow_bps": 0.0,
            "excluded_ambiguous_boundary_credit_count": 0,
            "excluded_ambiguous_boundary_credit_bps": 0.0,
            "notional_fraction": notional_fraction,
            "risk_scale": trade.risk_scale,
        }
    funding = funding_adjustment(
        rates,
        side=outcome.side,
        entry_ts_ms=outcome.entry_ts_ms,
        exit_ts_ms=outcome.exit_ts_ms,
        bar_interval_ms=DAILY_INTERVAL_MS,
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
        "net_funding_cashflow_bps": funding.funding_bps * notional_fraction,
        "excluded_ambiguous_boundary_credit_count": (
            funding.excluded_ambiguous_credit_count
        ),
        "excluded_ambiguous_boundary_credit_bps": (
            funding.excluded_ambiguous_credit_bps * notional_fraction
        ),
        "notional_fraction": notional_fraction,
        "risk_scale": trade.risk_scale,
    }


def research_state_momentum_tournament(
    daily_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    specs: Sequence[StateMomentumSpec] = PREREGISTERED_STATE_MOMENTUM_CANDIDATES,
) -> tuple[
    dict[str, tuple[SlowTrendOutcome, ...]],
    dict[str, dict[str, float | int]],
    tuple[WeeklyContext, ...],
]:
    weekly_by_symbol = {
        symbol: aggregate_daily_to_weekly(rows)
        for symbol, rows in sorted(daily_by_symbol.items())
    }
    contexts = build_weekly_contexts(weekly_by_symbol)
    daily_index = {
        symbol: {row.open_ts_ms: index for index, row in enumerate(rows)}
        for symbol, rows in sorted(daily_by_symbol.items())
    }
    raw: dict[str, list[CandidateTrade]] = {spec.candidate_id: [] for spec in specs}
    for spec in specs:
        for context in contexts:
            if not _state_allows(context, spec.state_policy):
                continue
            signal_day_open = context.week_open_ts_ms + 6 * DAILY_INTERVAL_MS
            for symbol, direction, momentum, rank in _candidate_legs(context, spec):
                signal_index = daily_index[symbol].get(signal_day_open)
                if signal_index is None:
                    continue
                trade = _candidate_trade(
                    daily_by_symbol[symbol],
                    signal_index=signal_index,
                    direction=direction,
                    momentum=momentum,
                    rank=rank,
                    context=context,
                    spec=spec,
                )
                if trade is not None:
                    raw[spec.candidate_id].append(trade)
    output: dict[str, tuple[SlowTrendOutcome, ...]] = {}
    audit: dict[str, dict[str, float | int]] = {}
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    for candidate_id, trades in raw.items():
        selected = apply_state_momentum_portfolio_limits(trades)
        outcomes: list[SlowTrendOutcome] = []
        funding_events = 0
        funding_cashflow = 0.0
        excluded_events = 0
        excluded_credit = 0.0
        notional_fractions: list[float] = []
        risk_scales: list[float] = []
        for trade in selected:
            revised, row_audit = _apply_account_risk_and_costs(
                trade,
                funding_by_symbol.get(trade.outcome.symbol, ()),
                spec_by_id[candidate_id],
            )
            outcomes.append(revised)
            funding_events += int(row_audit["applied_funding_event_count"])
            funding_cashflow += float(row_audit["net_funding_cashflow_bps"])
            excluded_events += int(
                row_audit["excluded_ambiguous_boundary_credit_count"]
            )
            excluded_credit += float(
                row_audit["excluded_ambiguous_boundary_credit_bps"]
            )
            notional_fractions.append(float(row_audit["notional_fraction"]))
            risk_scales.append(float(row_audit["risk_scale"]))
        output[candidate_id] = tuple(outcomes)
        audit[candidate_id] = {
            "raw_intent_count": len(trades),
            "selected_trade_count": len(outcomes),
            "closed_trade_count": sum(not row.censored for row in outcomes),
            "censored_open_count": sum(row.censored for row in outcomes),
            "applied_funding_event_count": funding_events,
            "net_funding_cashflow_account_bps": funding_cashflow,
            "excluded_ambiguous_boundary_credit_count": excluded_events,
            "excluded_ambiguous_boundary_credit_account_bps": excluded_credit,
            "mean_notional_fraction": (
                fmean(notional_fractions) if notional_fractions else 0.0
            ),
            "minimum_risk_scale": min(risk_scales, default=0.0),
            "maximum_risk_scale": max(risk_scales, default=0.0),
        }
    return output, audit, contexts


def select_stable_state_momentum_candidates(
    development: Mapping[str, Mapping[str, object]],
    walk_forward: Mapping[str, Mapping[str, object]],
    specs: Sequence[StateMomentumSpec] = PREREGISTERED_STATE_MOMENTUM_CANDIDATES,
) -> tuple[str, ...]:
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    eligible = sorted(
        (
            candidate_id
            for candidate_id, profile in development.items()
            if not spec_by_id[candidate_id].is_negative_control
            and _eligible(profile)
            and walk_forward[candidate_id]["stability_pass"] is True
        ),
        key=lambda candidate_id: (*_rank_key(development[candidate_id]), candidate_id),
        reverse=True,
    )
    selected: list[str] = []
    families: set[str] = set()
    for candidate_id in eligible:
        family = spec_by_id[candidate_id].family
        if family in families:
            continue
        selected.append(candidate_id)
        families.add(family)
        if len(selected) == MAXIMUM_FINALISTS:
            break
    return tuple(selected)


def _bar_fingerprint(rows: Sequence[IntradayBar]) -> str:
    return hashlib.sha256(
        json.dumps(
            [asdict(row) for row in rows],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


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
    daily_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    *,
    start_ms: int,
    end_ms: int,
    specs: Sequence[StateMomentumSpec] = PREREGISTERED_STATE_MOMENTUM_CANDIDATES,
) -> dict[str, object]:
    outcomes, audit, contexts = research_state_momentum_tournament(
        daily_by_symbol,
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
    walk_forward = {
        candidate_id: development_walk_forward_stability(
            rows,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for candidate_id, rows in outcomes.items()
    }
    finalists = select_stable_state_momentum_candidates(
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
    source_by_symbol = {str(row["symbol"]): row for row in source_manifest}
    derived_manifest: list[dict[str, object]] = []
    for symbol, daily_rows in sorted(daily_by_symbol.items()):
        weekly_rows = aggregate_daily_to_weekly(daily_rows)
        source = source_by_symbol[symbol]
        derived_manifest.append(
            {
                "symbol": symbol,
                "source_bar_file_sha256": source["bar_file_sha256"],
                "funding_file_sha256": source["funding_file_sha256"],
                "daily_bar_count": len(daily_rows),
                "daily_sha256": _bar_fingerprint(daily_rows),
                "weekly_bar_count": len(weekly_rows),
                "weekly_sha256": _bar_fingerprint(weekly_rows),
            }
        )
    dataset_hash = hashlib.sha256(
        json.dumps(derived_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    ranked = sorted(
        (candidate_id for candidate_id, profile in development.items() if _rankable(profile)),
        key=lambda candidate_id: (*_rank_key(development[candidate_id]), candidate_id),
        reverse=True,
    )
    unranked = sorted(
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
            "hyp128_results_were_inspected": True,
            "independent_future_oos": False,
            "reason": (
                "HYP-128 실패와 2024~2025 논문을 본 뒤 설계한 적응 연구이므로 "
                "마지막 30%도 독립 미래표본이 아닙니다."
            ),
        },
        "source": {
            "venue": "BINANCE_USDM",
            "public_only": True,
            "bar_endpoint": BINANCE_FUTURES_KLINES_URL,
            "funding_endpoint": BINANCE_FUTURES_FUNDING_URL,
            "source_bar_interval": "4h",
            "derived_execution_interval": "1d",
            "derived_state_interval": "1w_monday_utc",
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "completed_candles_only": True,
            "dataset_hash": dataset_hash,
            "datasets": derived_manifest,
        },
        "research_basis": [
            {
                "title": "State transitions and momentum effect in cryptocurrency market",
                "url": "https://doi.org/10.1016/j.frl.2025.108356",
                "use": "4주 시장수익의 연속 UP-UP 상태를 검증 가설로 사용",
            },
            {
                "title": "Cryptocurrency market risk-managed momentum strategies",
                "url": "https://doi.org/10.1016/j.frl.2025.107879",
                "use": "2주 형성·1주 평가와 변동성 위험조정의 출처로 사용",
            },
            {
                "title": "Cryptocurrency anomalies and economic constraints",
                "url": "https://doi.org/10.1016/j.irfa.2024.103218",
                "use": "대형 코인·비용·최근 구간·롱/숏 분해를 경계로 사용",
            },
            {
                "title": "Cryptocurrency momentum has (not) its moments",
                "url": "https://doi.org/10.1007/s11408-025-00474-9",
                "use": "모멘텀 위험조정의 반대·조건부 결과를 함께 보존",
            },
        ],
        "preregistration": {
            "hypothesis_id": HYPOTHESIS_ID,
            "path": PREREGISTRATION_PATH,
            "candidate_count": len(specs),
            "family_count": len({spec.family for spec in specs}),
            "candidate_fingerprint": state_momentum_candidate_fingerprint(specs),
            "candidates": [asdict(spec) for spec in specs],
            "up_up_candidate_count": sum(spec.state_policy == "UP_UP" for spec in specs),
            "all_regime_control_count": sum(
                spec.state_policy == "ALL_REGIMES" for spec in specs
            ),
            "non_up_up_negative_control_count": sum(
                spec.is_negative_control for spec in specs
            ),
            "negative_controls_cannot_be_selected": True,
            "base_execution_cost_bps": BASE_EXECUTION_COST_BPS,
            "stress_execution_cost_bps": STRESS_EXECUTION_COST_BPS,
            "historical_funding_directionally_applied": True,
            "risk_budget_bps_per_trade": BASE_RISK_BUDGET_BPS,
            "volatility_scaling_can_only_reduce_risk": True,
            "maximum_notional_fraction": 1.0,
            "maximum_concurrent_positions": MAXIMUM_CONCURRENT_POSITIONS,
            "maximum_daily_entries": MAXIMUM_DAILY_ENTRIES,
            "next_day_open_entry": True,
            "same_day_stop_before_target": True,
            "fixed_maximum_hold": False,
            "censored_open_positions_are_not_scored": True,
            "tp1_fraction": 0.4,
            "split": "chronological 50% train / 20% validation / 30% diagnostic OOS",
            "walk_forward_fold_count": 6,
            "thresholds_lowered_after_results": False,
        },
        "weekly_state_distribution": {
            "context_count": len(contexts),
            "up_up_count": sum(context.up_up for context in contexts),
            "non_up_up_count": sum(not context.up_up for context in contexts),
        },
        "funding_cost_risk_audit": audit,
        "development_profiles": development,
        "development_walk_forward": walk_forward,
        "ranking_contract": {
            "minimum_development_closed_trades": 60,
            "minimum_validation_closed_trades": 20,
            "minimum_oos_closed_trades": 30,
            "sparse_candidates_are_not_ranked": True,
            "walk_forward_stability_required_before_selection": True,
            "negative_controls_excluded_from_selection": True,
            "maximum_distinct_family_finalists": MAXIMUM_FINALISTS,
        },
        "development_ranking_top_10": [
            {
                "rank": index + 1,
                "candidate_id": candidate_id,
                "family": spec_by_id[candidate_id].family,
                "state_policy": spec_by_id[candidate_id].state_policy,
                "risk_style": spec_by_id[candidate_id].risk_style,
                "negative_control": spec_by_id[candidate_id].is_negative_control,
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
            for index, candidate_id in enumerate(ranked[:10])
        ],
        "unranked_insufficient_sample": unranked,
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
            "시가총액 과거값이 없어 논문의 가치가중 대신 동일가중 시장상태를 사용했습니다.",
            "일봉은 과거 실행가능 bid·ask 깊이와 봉 내부 가격순서를 제공하지 않습니다.",
            "같은 일봉에서 stop과 target이 모두 닿으면 stop을 먼저 적용했습니다.",
            "실제 펀딩은 적용했지만 실행비용은 BASE·STRESS 고정 왕복비용입니다.",
            "변동성 조정은 프로젝트 안전규칙에 따라 위험을 늘리지 않고 줄이기만 합니다.",
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
    if end_ms - start_ms < MINIMUM_RESEARCH_DAYS * DAILY_INTERVAL_MS:
        raise ValueError(
            f"상태조건 모멘텀 연구기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다."
        )
    symbols = tuple(args.symbol or DEFAULT_SYMBOLS)
    four_hour_bars, funding, source_manifest = load_public_research_data(
        symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        cache_dir=args.cache_dir,
    )
    daily = {
        symbol: aggregate_four_hour_to_daily(rows)
        for symbol, rows in four_hour_bars.items()
    }
    if any(len(rows) < 1_000 for rows in daily.values()):
        raise RuntimeError("상태조건 모멘텀 일봉표본이 1,000개 미만인 종목이 있습니다.")
    report = build_report(
        source_manifest,
        daily,
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
