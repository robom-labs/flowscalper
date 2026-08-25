# 저장 공개시장 이벤트로 장중 후보와 방향 미러를 시간순 PAPER 연구한다.

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import time
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import fmean
from typing import Any

import duckdb

from backend.app.build_identity import STRATEGY_VERSION, git_commit
from backend.app.domain.market import TradeTick
from backend.app.domain.models import Side, Venue
from backend.app.features import BookFrame, FeatureInputError
from backend.app.intraday import (
    CandidateFamily,
    HorizonClass,
    IntradayCandidateEvaluator,
    MultiTimeframeFeatureEngine,
    ResearchPricePlan,
    ResearchVariantKind,
    SignalVariant,
    build_research_price_plan,
    pair_original_and_mechanical_mirror,
)
from backend.app.market_data import CandleBuilder
from backend.app.research import (
    DatasetSlice,
    ResearchProtocol,
    bootstrap_mean_interval,
    deflated_sharpe_ratio,
    finalize_research_manifest,
    probability_of_backtest_overfitting,
)

DEFAULT_RESEARCH_TRAIN_RUNS = (
    "RUN-94899287D623",
    "RUN-B987D1D386C6",
    "RUN-6D9E264F0360",
    "RUN-B85A51C5DAED",
    "RUN-E6FE0A69A138",
    "RUN-683EA01095FE",
)
DEFAULT_VALIDATION_RUNS = (
    "RUN-ED214939F990",
    "RUN-8CD493F93260",
)
DEFAULT_OOS_RUNS = (
    "RUN-4C905F26DA0D",
    "RUN-72EB83B350A7",
    "RUN-8805DB58DCE8",
    "RUN-D1CBBE3D2458",
    "RUN-517B78C88366",
)
BASE_COST_BPS = 13.0
STRESS_COST_BPS = 25.0
MINIMUM_OOS_SAMPLE = 30
SEED = 20260826
HORIZON_HOLDING_MS = {
    HorizonClass.MICRO_SCALP.value: 180_000,
    HorizonClass.FAST_INTRADAY.value: 3_600_000,
    HorizonClass.INTRADAY_SWING.value: 21_600_000,
}


@dataclass(frozen=True, slots=True)
class IntervalResearchSpec:
    horizon: HorizonClass
    interval_seconds: int
    higher_interval_seconds: int | None

    @property
    def key(self) -> str:
        return f"{self.horizon.value}:{self.interval_seconds}"


INTERVAL_SPECS = (
    IntervalResearchSpec(HorizonClass.MICRO_SCALP, 1, 60),
    IntervalResearchSpec(HorizonClass.MICRO_SCALP, 5, 60),
    IntervalResearchSpec(HorizonClass.MICRO_SCALP, 15, 60),
    IntervalResearchSpec(HorizonClass.MICRO_SCALP, 30, 60),
    IntervalResearchSpec(HorizonClass.FAST_INTRADAY, 60, 300),
    IntervalResearchSpec(HorizonClass.FAST_INTRADAY, 180, 900),
    IntervalResearchSpec(HorizonClass.FAST_INTRADAY, 300, 900),
    IntervalResearchSpec(HorizonClass.FAST_INTRADAY, 900, 3_600),
    IntervalResearchSpec(HorizonClass.INTRADAY_SWING, 900, 3_600),
    IntervalResearchSpec(HorizonClass.INTRADAY_SWING, 1_800, 14_400),
    IntervalResearchSpec(HorizonClass.INTRADAY_SWING, 3_600, 14_400),
    IntervalResearchSpec(HorizonClass.INTRADAY_SWING, 14_400, None),
)


@dataclass(frozen=True, slots=True)
class ResearchOutcome:
    key: str
    run_id: str
    symbol: str
    family: str
    variant: str
    horizon: str
    interval_seconds: int
    side: str
    information_set_id: str
    entry_ts_ms: int
    exit_ts_ms: int
    holding_ms: int
    exit_reason: str
    gross_bps: float
    base_net_bps: float
    stress_net_bps: float
    regime: str


@dataclass(slots=True)
class PendingTrade:
    key: str
    run_id: str
    signal: SignalVariant
    family: CandidateFamily
    horizon: HorizonClass
    plan: ResearchPricePlan
    regime: str
    tp1_taken: bool = False
    realized_gross_bps: float = 0.0
    remaining_fraction: float = 1.0


@dataclass(slots=True)
class RunDiagnostics:
    event_count: int = 0
    trade_event_count: int = 0
    depth_event_count: int = 0
    signal_count: int = 0
    stale_book_rejections: int = 0
    missing_feature_rejections: int = 0
    censored_count: int = 0
    first_ts_ms: int | None = None
    last_ts_ms: int | None = None
    signals_by_key: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _event_rows(run_dir: Path, *, maximum_events: int | None = None) -> Iterator[dict[str, Any]]:
    files = tuple(sorted(run_dir.rglob("*.parquet")))
    if not files:
        raise FileNotFoundError(f"시장 archive가 없습니다: {run_dir}")
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET memory_limit = '192MB'")
        connection.execute("SET threads = 1")
        connection.execute("SET preserve_insertion_order = false")
        query = """
            SELECT payload_json
            FROM read_parquet(?, union_by_name = true)
            WHERE json_extract_string(payload_json, '$.event_type') IN (
              'TRADE', 'DEPTH_UPDATE', 'ORDERBOOK', 'REST_BOOK_TICKER_BOOTSTRAP'
            )
            ORDER BY ts_ms, venue_ts_ms, symbol, payload_json
        """
        if maximum_events is not None:
            query += " LIMIT ?"
            parameters: list[object] = [[str(path) for path in files], maximum_events]
        else:
            parameters = [[str(path) for path in files]]
        reader = connection.execute(query, parameters).to_arrow_reader(batch_size=2_048)
        for batch in reader:
            for raw in batch.column(0).to_pylist():
                yield json.loads(raw)
    finally:
        connection.close()


def _book_frame(payload: dict[str, Any]) -> BookFrame:
    data = payload["data"]
    quality = payload["quality"]
    return BookFrame.from_levels(
        venue=Venue.BINANCE_USDM,
        symbol=str(payload["symbol"]),
        ts_ms=int(payload["venue_ts_ms"]),
        bids=((Decimal(price), Decimal(quantity)) for price, quantity in data["bids"]),
        asks=((Decimal(price), Decimal(quantity)) for price, quantity in data["asks"]),
        sequence_valid=bool(quality.get("sequence_valid", False)),
        stale=bool(quality.get("is_stale", True)),
        lag_ms=float(quality.get("lag_ms", math.inf)),
    )


def _trade_tick(payload: dict[str, Any]) -> TradeTick:
    data = payload["data"]
    return TradeTick(
        venue=Venue.BINANCE_USDM,
        symbol=str(payload["symbol"]),
        price=Decimal(str(data["price"])),
        quantity=Decimal(str(data["quantity"])),
        trade_ts_ms=int(payload.get("transaction_ts_ms") or payload["venue_ts_ms"]),
        buyer_is_aggressor=bool(data["buyer_is_aggressor"]),
        event_id=str(payload["event_id"]),
    )


def _variant_key(
    spec: IntervalResearchSpec,
    family: CandidateFamily,
    variant: ResearchVariantKind,
) -> str:
    return f"{spec.key}:{family.value}:{variant.value}"


def _preregistered_keys(
    specs: Sequence[IntervalResearchSpec] = INTERVAL_SPECS,
) -> tuple[str, ...]:
    """거래가 없던 가설도 다중검정 모수에서 빠지지 않게 전체 그리드를 고정한다."""

    return tuple(
        sorted(
            _variant_key(spec, family, variant)
            for spec in specs
            for family in CandidateFamily
            for variant in ResearchVariantKind
        )
    )


def _return_bps(side: Side, entry: Decimal, exit_price: Decimal) -> float:
    direction = Decimal(1) if side is Side.LONG else Decimal(-1)
    return float((exit_price - entry) / entry * Decimal(10_000) * direction)


def _finalize_pending(
    pending: PendingTrade,
    *,
    exit_ts_ms: int,
    exit_price: Decimal,
    exit_reason: str,
) -> ResearchOutcome:
    gross_bps = pending.realized_gross_bps + pending.remaining_fraction * _return_bps(
        pending.plan.side,
        pending.plan.entry,
        exit_price,
    )
    return ResearchOutcome(
        key=pending.key,
        run_id=pending.run_id,
        symbol=pending.signal.symbol,
        family=pending.family.value,
        variant=pending.signal.variant.value,
        horizon=pending.horizon.value,
        interval_seconds=pending.signal.interval_seconds,
        side=pending.signal.side.value,
        information_set_id=pending.signal.information_set_id,
        entry_ts_ms=pending.plan.signal_ts_ms,
        exit_ts_ms=exit_ts_ms,
        holding_ms=exit_ts_ms - pending.plan.signal_ts_ms,
        exit_reason=exit_reason,
        gross_bps=gross_bps,
        base_net_bps=gross_bps - BASE_COST_BPS,
        stress_net_bps=gross_bps - STRESS_COST_BPS,
        regime=pending.regime,
    )


def _advance_pending(
    pending: PendingTrade,
    frame: BookFrame,
) -> ResearchOutcome | None:
    executable_exit = frame.bids[0][0] if pending.plan.side is Side.LONG else frame.asks[0][0]
    direction = Decimal(1) if pending.plan.side is Side.LONG else Decimal(-1)
    stop_hit = (executable_exit - pending.plan.stop) * direction <= 0
    tp1_hit = (executable_exit - pending.plan.take_profit_1) * direction >= 0
    tp2_hit = (executable_exit - pending.plan.take_profit_2) * direction >= 0
    if stop_hit:
        return _finalize_pending(
            pending,
            exit_ts_ms=frame.ts_ms,
            exit_price=executable_exit,
            exit_reason="STOP",
        )
    if tp2_hit:
        if not pending.tp1_taken:
            pending.realized_gross_bps = 0.7 * _return_bps(
                pending.plan.side,
                pending.plan.entry,
                pending.plan.take_profit_1,
            )
            pending.remaining_fraction = 0.3
        return _finalize_pending(
            pending,
            exit_ts_ms=frame.ts_ms,
            exit_price=pending.plan.take_profit_2,
            exit_reason="TP2",
        )
    if tp1_hit and not pending.tp1_taken:
        pending.tp1_taken = True
        pending.realized_gross_bps = 0.7 * _return_bps(
            pending.plan.side,
            pending.plan.entry,
            pending.plan.take_profit_1,
        )
        pending.remaining_fraction = 0.3
    if frame.ts_ms >= pending.plan.signal_ts_ms + pending.plan.maximum_holding_ms:
        return _finalize_pending(
            pending,
            exit_ts_ms=frame.ts_ms,
            exit_price=executable_exit,
            exit_reason="MAX_HOLD",
        )
    return None


def _new_pending(
    *,
    run_id: str,
    signal: SignalVariant,
    family: CandidateFamily,
    spec: IntervalResearchSpec,
    frame: BookFrame,
    atr: float,
    regime: str,
) -> PendingTrade:
    entry = frame.asks[0][0] if signal.side is Side.LONG else frame.bids[0][0]
    plan = build_research_price_plan(
        side=signal.side,
        signal_ts_ms=signal.signal_ts_ms,
        executable_entry=entry,
        atr=Decimal(str(atr)),
        horizon=spec.horizon,
    )
    return PendingTrade(
        key=_variant_key(spec, family, signal.variant),
        run_id=run_id,
        signal=signal,
        family=family,
        horizon=spec.horizon,
        plan=plan,
        regime=regime,
    )


def research_run(
    run_id: str,
    run_dir: Path,
    *,
    specs: Sequence[IntervalResearchSpec] = INTERVAL_SPECS,
    maximum_events: int | None = None,
) -> tuple[list[ResearchOutcome], RunDiagnostics]:
    intervals = tuple(sorted({value for spec in specs for value in (
        spec.interval_seconds,
        *((spec.higher_interval_seconds,) if spec.higher_interval_seconds else ()),
    )}))
    builder = CandleBuilder(intervals=intervals, maximum_bars=128)
    features = MultiTimeframeFeatureEngine(
        intervals=intervals,
        maximum_bars=128,
        minimum_bars=20,
    )
    evaluator = IntradayCandidateEvaluator()
    latest_books: dict[str, BookFrame] = {}
    pending: dict[tuple[str, str], PendingTrade] = {}
    cooldown_until: dict[tuple[str, str], int] = {}
    outcomes: list[ResearchOutcome] = []
    diagnostics = RunDiagnostics()
    specs_by_interval: dict[int, list[IntervalResearchSpec]] = defaultdict(list)
    for spec in specs:
        specs_by_interval[spec.interval_seconds].append(spec)

    for payload in _event_rows(run_dir, maximum_events=maximum_events):
        diagnostics.event_count += 1
        ts_ms = int(payload.get("venue_ts_ms", 0))
        diagnostics.first_ts_ms = (
            ts_ms if diagnostics.first_ts_ms is None else diagnostics.first_ts_ms
        )
        diagnostics.last_ts_ms = ts_ms
        event_type = str(payload.get("event_type"))
        symbol = str(payload.get("symbol"))
        if event_type in {"DEPTH_UPDATE", "ORDERBOOK", "REST_BOOK_TICKER_BOOTSTRAP"}:
            try:
                frame = _book_frame(payload)
            except (FeatureInputError, KeyError, ValueError):
                continue
            diagnostics.depth_event_count += 1
            latest_books[symbol] = frame
            for pending_key, trade in tuple(pending.items()):
                if trade.signal.symbol != symbol:
                    continue
                outcome = _advance_pending(trade, frame)
                if outcome is not None:
                    outcomes.append(outcome)
                    del pending[pending_key]
            continue
        if event_type != "TRADE":
            continue
        diagnostics.trade_event_count += 1
        try:
            trade = _trade_tick(payload)
            completed = builder.add(trade)
        except (KeyError, ValueError):
            continue
        for candle in completed:
            features.ingest_completed(candle)
        frame = latest_books.get(symbol)
        if (
            frame is None
            or frame.ts_ms > trade.trade_ts_ms
            or trade.trade_ts_ms - frame.ts_ms > 1_000
        ):
            diagnostics.stale_book_rejections += len(completed)
            continue
        if not frame.sequence_valid or frame.stale or frame.lag_ms > 500:
            diagnostics.stale_book_rejections += len(completed)
            continue
        for candle in completed:
            for spec in specs_by_interval.get(candle.interval_seconds, ()):
                snapshot = features.snapshot(
                    symbol,
                    spec.interval_seconds,
                    as_of_ts_ms=trade.trade_ts_ms,
                    higher_interval_seconds=spec.higher_interval_seconds,
                )
                if snapshot is None or snapshot.atr <= 0:
                    diagnostics.missing_feature_rejections += 1
                    continue
                originals = evaluator.evaluate(snapshot, decision_ts_ms=trade.trade_ts_ms)
                reverse_hypotheses = evaluator.evaluate_reverse_hypotheses(
                    snapshot,
                    decision_ts_ms=trade.trade_ts_ms,
                )
                variant_groups: list[tuple[tuple[SignalVariant, CandidateFamily], ...]] = []
                for original in originals:
                    variant_groups.append(
                        tuple(
                            (variant, original.family)
                            for variant in pair_original_and_mechanical_mirror(
                                original.as_variant()
                            )
                        )
                    )
                variant_groups.extend(
                    ((reverse.as_variant(), reverse.family),)
                    for reverse in reverse_hypotheses
                )
                for group in variant_groups:
                    group_keys = [
                        (
                            _variant_key(spec, family, signal.variant),
                            signal,
                            family,
                        )
                        for signal, family in group
                    ]
                    if any(
                        (key, symbol) in pending
                        or signal.signal_ts_ms < cooldown_until.get((key, symbol), 0)
                        for key, signal, _ in group_keys
                    ):
                        continue
                    new_trades: list[tuple[str, PendingTrade]] = []
                    try:
                        for key, signal, family in group_keys:
                            new_trades.append(
                                (
                                    key,
                                    _new_pending(
                                        run_id=run_id,
                                        signal=signal,
                                        family=family,
                                        spec=spec,
                                        frame=frame,
                                        atr=snapshot.atr,
                                        regime=snapshot.regime,
                                    ),
                                )
                            )
                    except ValueError:
                        continue
                    for key, trade_plan in new_trades:
                        pending_key = (key, symbol)
                        pending[pending_key] = trade_plan
                        cooldown_until[pending_key] = (
                            trade_plan.signal.signal_ts_ms
                            + trade_plan.plan.maximum_holding_ms
                        )
                        diagnostics.signal_count += 1
                        diagnostics.signals_by_key[key] += 1
    diagnostics.censored_count = len(pending)
    diagnostics.signals_by_key = dict(sorted(diagnostics.signals_by_key.items()))
    return outcomes, diagnostics


def _profile(values: Sequence[float]) -> dict[str, object]:
    if not values:
        return {
            "sample_size": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "expectancy_bps": None,
            "profit_factor": None,
            "net_sum_bps": 0.0,
            "maximum_drawdown_bps": 0.0,
            "downside_deviation_bps": None,
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
    downside = [min(0.0, value) for value in values]
    downside_deviation = (
        math.sqrt(fmean(value * value for value in downside)) if downside else None
    )
    gross_loss = abs(sum(losses))
    return {
        "sample_size": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(values),
        "expectancy_bps": fmean(values),
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "net_sum_bps": sum(values),
        "maximum_drawdown_bps": maximum_drawdown,
        "downside_deviation_bps": downside_deviation,
        "sample_status": "ENOUGH" if len(values) >= MINIMUM_OOS_SAMPLE else "INSUFFICIENT",
    }


def _summaries(outcomes: Sequence[ResearchOutcome], keys: Sequence[str]) -> dict[str, object]:
    return {
        key: {
            "gross": _profile([row.gross_bps for row in outcomes if row.key == key]),
            "base": _profile([row.base_net_bps for row in outcomes if row.key == key]),
            "stress": _profile([row.stress_net_bps for row in outcomes if row.key == key]),
            "exit_reasons": dict(
                sorted(
                    {
                        reason: sum(
                            1
                            for row in outcomes
                            if row.key == key and row.exit_reason == reason
                        )
                        for reason in {row.exit_reason for row in outcomes if row.key == key}
                    }.items()
                )
            ),
        }
        for key in keys
    }


def _purged_split_outcomes(
    by_run: dict[str, list[ResearchOutcome]],
    dataset: Sequence[DatasetSlice],
    selected_runs: Sequence[str],
) -> tuple[dict[str, list[ResearchOutcome]], dict[str, object]]:
    raw = {
        "train": [
            row
            for run_id in DEFAULT_RESEARCH_TRAIN_RUNS
            if run_id in selected_runs
            for row in by_run[run_id]
        ],
        "validation": [
            row
            for run_id in DEFAULT_VALIDATION_RUNS
            if run_id in selected_runs
            for row in by_run[run_id]
        ],
        "oos": [
            row
            for run_id in DEFAULT_OOS_RUNS
            if run_id in selected_runs
            for row in by_run[run_id]
        ],
    }
    slices = {row.run_id: row for row in dataset}
    train_slices = [slices[run] for run in DEFAULT_RESEARCH_TRAIN_RUNS if run in slices]
    validation_slices = [slices[run] for run in DEFAULT_VALIDATION_RUNS if run in slices]
    if not train_slices or not validation_slices:
        return raw, {
            "status": "PARTIAL_DIAGNOSTIC_NOT_APPLIED",
            "raw_counts": {split: len(rows) for split, rows in raw.items()},
        }
    train_boundary_ms = max(row.end_ts_ms for row in train_slices)
    validation_boundary_ms = max(row.end_ts_ms for row in validation_slices)
    filtered = {
        "train": [
            row
            for row in raw["train"]
            if row.exit_ts_ms < train_boundary_ms - HORIZON_HOLDING_MS[row.horizon]
        ],
        "validation": [
            row
            for row in raw["validation"]
            if row.entry_ts_ms > train_boundary_ms + HORIZON_HOLDING_MS[row.horizon]
            and row.exit_ts_ms
            < validation_boundary_ms - HORIZON_HOLDING_MS[row.horizon]
        ],
        "oos": [
            row
            for row in raw["oos"]
            if row.entry_ts_ms > validation_boundary_ms + HORIZON_HOLDING_MS[row.horizon]
        ],
    }
    return filtered, {
        "status": "APPLIED",
        "train_boundary_ms": train_boundary_ms,
        "validation_boundary_ms": validation_boundary_ms,
        "horizon_specific_purge_embargo_ms": HORIZON_HOLDING_MS,
        "raw_counts": {split: len(rows) for split, rows in raw.items()},
        "included_counts": {split: len(rows) for split, rows in filtered.items()},
        "excluded_counts": {
            split: len(raw[split]) - len(filtered[split]) for split in raw
        },
    }


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_ss = sum((value - left_mean) ** 2 for value in left)
    right_ss = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_ss * right_ss)
    return numerator / denominator if denominator else None


def _mirror_correlations(outcomes: Sequence[ResearchOutcome]) -> dict[str, object]:
    rows: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    completed_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for outcome in outcomes:
        base_key = outcome.key.rsplit(":", 1)[0]
        rows[(base_key, outcome.information_set_id)][outcome.variant] = outcome.base_net_bps
        completed_counts[base_key][outcome.variant] += 1
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for (base_key, _), values in rows.items():
        if {"ORIGINAL", "MECHANICAL_MIRROR"} <= values.keys():
            grouped[base_key].append((values["ORIGINAL"], values["MECHANICAL_MIRROR"]))
    return {
        key: {
            "paired_sample_size": len(pairs),
            "completed_original_count": completed_counts[key]["ORIGINAL"],
            "completed_mirror_count": completed_counts[key]["MECHANICAL_MIRROR"],
            "base_return_correlation": _pearson(
                [pair[0] for pair in pairs],
                [pair[1] for pair in pairs],
            ),
        }
        for key, pairs in sorted(grouped.items())
    }


def _mirror_signal_parity(diagnostics: dict[str, object]) -> dict[str, object]:
    mismatches: list[dict[str, object]] = []
    checked_pairs = 0
    for run_id, raw in diagnostics.items():
        if not isinstance(raw, dict):
            continue
        counts = raw.get("signals_by_key", {})
        if not isinstance(counts, dict):
            continue
        for key, count in counts.items():
            if not str(key).endswith(":ORIGINAL"):
                continue
            checked_pairs += 1
            mirror_key = str(key).removesuffix(":ORIGINAL") + ":MECHANICAL_MIRROR"
            mirror_count = counts.get(mirror_key, 0)
            if int(count) != int(mirror_count):
                mismatches.append(
                    {
                        "run_id": run_id,
                        "original_key": key,
                        "original_count": count,
                        "mirror_count": mirror_count,
                    }
                )
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "checked_run_candidate_pairs": checked_pairs,
        "mismatches": mismatches,
        "same_signal_timestamp_enforced": True,
    }


def _selection_report(
    train_validation: Sequence[ResearchOutcome],
    oos: Sequence[ResearchOutcome],
    *,
    keys: Sequence[str],
    train_validation_run_ids: Sequence[str],
) -> dict[str, object]:
    promotable_keys = [key for key in keys if not key.endswith(":MECHANICAL_MIRROR")]
    fold_returns = {
        key: [
            fmean(values) if (values := [
                row.base_net_bps
                for row in train_validation
                if row.key == key and row.run_id == run_id
            ]) else 0.0
            for run_id in train_validation_run_ids
        ]
        for key in promotable_keys
    }
    candidates_with_sample = [
        key for key in promotable_keys if sum(row.key == key for row in train_validation) >= 10
    ]
    selected = (
        max(
            candidates_with_sample,
            key=lambda key: (
                fmean(row.base_net_bps for row in train_validation if row.key == key),
                key,
            ),
        )
        if candidates_with_sample
        else None
    )
    selected_oos = [row.base_net_bps for row in oos if row.key == selected]
    fold_count = len(train_validation_run_ids)
    if len(fold_returns) < 2:
        pbo = {"status": "INSUFFICIENT_HYPOTHESES", "pbo": None}
    elif fold_count < 4 or fold_count % 2:
        pbo = {
            "status": "INSUFFICIENT_EVEN_FOLDS",
            "pbo": None,
            "fold_count": fold_count,
        }
    else:
        pbo = probability_of_backtest_overfitting(fold_returns)
    return {
        "candidate_count": len(promotable_keys),
        "selected_on_train_validation": selected,
        "pbo": pbo,
        "oos_deflated_sharpe": deflated_sharpe_ratio(
            selected_oos,
            trials=max(1, len(promotable_keys)),
        ),
        "oos_expectancy_bootstrap_95": bootstrap_mean_interval(
            selected_oos,
            seed=SEED,
        ),
        "no_trade_baseline_bps": 0.0,
        "selection_is_not_profitability_proof": True,
    }


def _promotion_assessment(
    oos: Sequence[ResearchOutcome],
    selection: dict[str, object],
) -> dict[str, object]:
    selected = selection["selected_on_train_validation"]
    values = [row.base_net_bps for row in oos if row.key == selected]
    stress = [row.stress_net_bps for row in oos if row.key == selected]
    base = _profile(values)
    stress_profile = _profile(stress)
    bootstrap = selection["oos_expectancy_bootstrap_95"]
    dsr = selection["oos_deflated_sharpe"]
    pbo = selection["pbo"]
    gates = {
        "selected_candidate_exists": selected is not None,
        "oos_sample_at_least_30": len(values) >= MINIMUM_OOS_SAMPLE,
        "oos_base_expectancy_positive": bool(values) and fmean(values) > 0,
        "oos_base_profit_factor_above_1": (
            base["profit_factor"] is not None and float(base["profit_factor"]) > 1
        ),
        "oos_stress_expectancy_positive": bool(stress) and fmean(stress) > 0,
        "bootstrap_lower_positive": (
            isinstance(bootstrap, dict)
            and bootstrap.get("lower") is not None
            and float(bootstrap["lower"]) > 0
        ),
        "dsr_at_least_0_95": (
            isinstance(dsr, dict)
            and dsr.get("dsr_probability") is not None
            and float(dsr["dsr_probability"]) >= 0.95
        ),
        "pbo_at_most_0_20": (
            isinstance(pbo, dict)
            and isinstance(pbo.get("pbo"), int | float)
            and float(pbo["pbo"]) <= 0.20
        ),
    }
    passed = all(gates.values())
    return {
        "selected_candidate": selected,
        "status": "OOS_PASS" if passed else "NOT_PROVEN",
        "gates": gates,
        "oos_base": base,
        "oos_stress": stress_profile,
        "registry_changes": [],
        "registry_policy": (
            "OOS_PASS여도 별도 신규 ID와 SHADOW 승인 전에는 등록하지 않음"
        ),
    }


def _dataset_slice(run_id: str, run_dir: Path) -> DatasetSlice:
    files = tuple(sorted(run_dir.rglob("*.parquet")))
    if not files:
        raise FileNotFoundError(f"시장 archive가 없습니다: {run_dir}")
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(run_dir).as_posix()
        digest.update(relative.encode())
        digest.update(str(path.stat().st_size).encode())
        with path.open("rb") as stream:
            while block := stream.read(8 * 1024 * 1024):
                digest.update(block)
    connection = duckdb.connect(":memory:")
    try:
        row = connection.execute(
            """
            SELECT MIN(venue_ts_ms), MAX(venue_ts_ms), COUNT(*),
              LIST_SORT(LIST_DISTINCT(LIST(symbol)))
            FROM read_parquet(?, union_by_name = true)
            """,
            [[str(path) for path in files]],
        ).fetchone()
    finally:
        connection.close()
    if row is None or row[0] is None or row[1] is None:
        raise ValueError(f"시장 archive 시간 범위를 읽을 수 없습니다: {run_dir}")
    return DatasetSlice(
        run_id=run_id,
        venue="BINANCE_USDM",
        symbols=tuple(str(symbol) for symbol in row[3]),
        start_ts_ms=int(row[0]),
        end_ts_ms=int(row[1]),
        event_count=int(row[2]),
        checksum=digest.hexdigest(),
    )


def _config_hash(project_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in (
        "config/strategy.example.yaml",
        "config/cost_model.example.yaml",
        "config/risk.example.yaml",
    ):
        digest.update(relative.encode())
        digest.update((project_root / relative).read_bytes())
    return digest.hexdigest()


def _html_report(output: dict[str, object]) -> str:
    result = output["result"]
    assert isinstance(result, dict)
    assessment = result["promotion_assessment"]
    assert isinstance(assessment, dict)
    rows = []
    summaries = result["splits"]
    assert isinstance(summaries, dict)
    oos = summaries["oos"]
    assert isinstance(oos, dict)
    oos_summary = oos["summary"]
    assert isinstance(oos_summary, dict)
    for key, profiles in oos_summary.items():
        assert isinstance(profiles, dict)
        base = profiles["base"]
        assert isinstance(base, dict)
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(key))}</td>"
            f"<td>{base['sample_size']}</td>"
            f"<td>{base['expectancy_bps']}</td>"
            f"<td>{base['profit_factor']}</td>"
            f"<td>{base['sample_status']}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="ko"><meta charset="utf-8"><title>FlowScalper 장중 연구</title>
<style>body{{font:14px system-ui;margin:32px;color:#17212b}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccd5df;padding:7px;text-align:left}}th{{background:#edf3f7}}</style>
<h1>FlowScalper 장중 후보 OOS 연구</h1>
<p>PAPER 전용 · 실제 주문 0 · 수익성 상태 {html.escape(str(assessment['status']))}</p>
<p>선정 후보 {html.escape(str(assessment['selected_candidate']))}</p>
<table><thead><tr><th>후보</th><th>OOS 표본</th><th>BASE 기대값 bp</th>
<th>Profit Factor</th><th>표본 상태</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("data/market-parquet-v6/venue=BINANCE_USDM"),
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-html", type=Path)
    parser.add_argument("--run-id", action="append")
    parser.add_argument("--maximum-events", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.maximum_events is not None and args.maximum_events <= 0:
        raise ValueError("maximum-events는 양수여야 합니다.")
    project_root = Path(__file__).resolve().parents[1]
    configured_runs = (
        *DEFAULT_RESEARCH_TRAIN_RUNS,
        *DEFAULT_VALIDATION_RUNS,
        *DEFAULT_OOS_RUNS,
    )
    selected_runs = tuple(args.run_id or configured_runs)
    unknown_runs = set(selected_runs) - set(configured_runs)
    if unknown_runs:
        raise ValueError(f"사전등록되지 않은 Run입니다: {sorted(unknown_runs)}")
    dataset = tuple(
        _dataset_slice(run_id, args.archive / f"run={run_id}") for run_id in selected_runs
    )
    protocol = ResearchProtocol(
        hypothesis_id="HYP-INTRADAY-CANDLE-ORIGINAL-MIRROR-REVERSE-V1",
        strategy_id="RESEARCH_ONLY_INTRADAY_CANDIDATES",
        strategy_version=STRATEGY_VERSION,
        feature_version="COMPLETED_CANDLE_MTF_V1",
        cost_model_version="TOP_OF_BOOK_BASE13_STRESS25_V1",
        parameter_grid={
            "horizon_interval": tuple(spec.key for spec in INTERVAL_SPECS),
            "candidate_family": tuple(family.value for family in CandidateFamily),
            "variant": tuple(variant.value for variant in ResearchVariantKind),
        },
        horizon_seconds=(180, 3_600, 21_600),
        base_cost_bps=BASE_COST_BPS,
        stress_cost_bps=STRESS_COST_BPS,
        seed=SEED,
        purge_ms=21_600_000,
        embargo_ms=21_600_000,
        falsification_criteria=(
            "OOS BASE expectancy_bps <= 0",
            "OOS BASE profit_factor <= 1",
            "OOS STRESS expectancy_bps <= 0",
            "OOS sample_size < 30",
            "OOS bootstrap 95% lower <= 0",
            "DSR < 0.95 or PBO > 0.20",
        ),
        baseline_ids=("NO_TRADE", "MECHANICAL_MIRROR", "HYPOTHESIS_REVERSE"),
    )
    started_ts_ms = time.time_ns() // 1_000_000
    manifest = protocol.manifest(
        dataset,
        code_hash=git_commit(),
        config_hash=_config_hash(project_root),
        generated_ts_ms=started_ts_ms,
    )
    by_run: dict[str, list[ResearchOutcome]] = {}
    diagnostics: dict[str, object] = {}
    for run_id in selected_runs:
        outcomes, run_diagnostics = research_run(
            run_id,
            args.archive / f"run={run_id}",
            maximum_events=args.maximum_events,
        )
        by_run[run_id] = outcomes
        diagnostics[run_id] = asdict(run_diagnostics)
    all_outcomes = [row for run_id in selected_runs for row in by_run[run_id]]
    keys = _preregistered_keys()
    split_runs = {
        "train": tuple(run for run in DEFAULT_RESEARCH_TRAIN_RUNS if run in selected_runs),
        "validation": tuple(run for run in DEFAULT_VALIDATION_RUNS if run in selected_runs),
        "oos": tuple(run for run in DEFAULT_OOS_RUNS if run in selected_runs),
    }
    split_outcomes, purge_embargo = _purged_split_outcomes(
        by_run,
        dataset,
        selected_runs,
    )
    splits = {
        split: {
            "run_ids": list(split_runs[split]),
            "outcome_count": len(rows),
            "summary": _summaries(rows, keys),
            "examples": [asdict(row) for row in rows[:20]],
        }
        for split, rows in split_outcomes.items()
    }
    selection = _selection_report(
        [*split_outcomes["train"], *split_outcomes["validation"]],
        split_outcomes["oos"],
        keys=keys,
        train_validation_run_ids=(*split_runs["train"], *split_runs["validation"]),
    )
    result: dict[str, object] = {
        "schema_version": 1,
        "execution_scope": (
            "PARTIAL_DIAGNOSTIC_NOT_EVIDENCE"
            if args.maximum_events is not None or selected_runs != configured_runs
            else "FULL_PREREGISTERED_ARCHIVE"
        ),
        "method": {
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
            "completed_candles_only": True,
            "current_candle_features": False,
            "signal_lookahead": False,
            "entry": "actual ask for LONG and actual bid for SHORT",
            "exit": "actual bid for LONG and actual ask for SHORT",
            "base_cost_bps": BASE_COST_BPS,
            "stress_cost_bps": STRESS_COST_BPS,
            "same_information_mechanical_mirror": True,
            "reverse_hypothesis_has_separate_conditions": True,
            "annualization": False,
            "natural_signal_thresholds_lowered": False,
            "horizon_specific_purge_embargo_ms": HORIZON_HOLDING_MS,
        },
        "run_diagnostics": diagnostics,
        "keys": keys,
        "splits": splits,
        "purge_embargo": purge_embargo,
        "selection_bias": selection,
        "mirror_signal_parity": _mirror_signal_parity(diagnostics),
        "mirror_correlations": _mirror_correlations(all_outcomes),
        "promotion_assessment": _promotion_assessment(split_outcomes["oos"], selection),
        "profitability_status": "NOT_PROVEN",
        "operational_metrics": {
            "status": "NOT_RUN",
            "reason": "연구 계산과 실제 LIVE 장시간 성능 검증은 별도 증거다.",
        },
    }
    output: dict[str, object] = {
        "manifest": finalize_research_manifest(
            manifest,
            result=result,
            completed_ts_ms=time.time_ns() // 1_000_000,
        ),
        "result": result,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n")
    else:
        print(rendered)
    if args.output_html is not None:
        args.output_html.parent.mkdir(parents=True, exist_ok=True)
        args.output_html.write_text(_html_report(output))


if __name__ == "__main__":
    main()
