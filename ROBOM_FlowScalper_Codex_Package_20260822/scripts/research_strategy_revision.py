"""저장 LIVE_PUBLIC 호가·체결로 전략 개정안을 시간순 train·holdout 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from bisect import bisect_left, insort
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from statistics import fmean
from typing import Any

import duckdb

from backend.app.build_identity import STRATEGY_VERSION, git_commit
from backend.app.domain.market import TradeTick
from backend.app.domain.models import Side, Venue
from backend.app.features import BookFrame, FeatureEngine, FeatureInputError, FeatureSnapshot
from backend.app.regime import Regime, RegimeClassifier
from backend.app.research import (
    DatasetSlice,
    ResearchProtocol,
    bootstrap_mean_interval,
    deflated_sharpe_ratio,
    finalize_research_manifest,
    probability_of_backtest_overfitting,
)
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.registry import StrategyMode, StrategyRegistry
from backend.app.strategies.runtime_evaluator import StrategySignalEvaluator
from backend.app.strategies.statistics import robust_z_from_sorted

DEFAULT_TRAIN_RUNS = (
    "RUN-94899287D623",
    "RUN-B987D1D386C6",
    "RUN-6D9E264F0360",
    "RUN-B85A51C5DAED",
    "RUN-E6FE0A69A138",
    "RUN-683EA01095FE",
    "RUN-ED214939F990",
    "RUN-8CD493F93260",
)
DEFAULT_HOLDOUT_RUNS = (
    "RUN-4C905F26DA0D",
    "RUN-72EB83B350A7",
    "RUN-8805DB58DCE8",
    "RUN-D1CBBE3D2458",
    "RUN-517B78C88366",
)
DEFAULT_VALIDATION_RUNS = DEFAULT_TRAIN_RUNS[-2:]
DEFAULT_RESEARCH_TRAIN_RUNS = DEFAULT_TRAIN_RUNS[:-2]
DEFAULT_OOS_RUNS = DEFAULT_HOLDOUT_RUNS


@dataclass(frozen=True, slots=True)
class StrategyVariant:
    name: str
    confirmation_ms: int
    predicate: Callable[[Side, FeatureSnapshot, Regime, float], bool]


@dataclass(frozen=True, slots=True)
class ResearchSignal:
    variant: str
    run_id: str
    symbol: str
    side: Side
    ts_ms: int
    entry_price: float
    target_ts_ms: int
    horizon_ms: int


@dataclass(frozen=True, slots=True)
class ResearchOutcome:
    variant: str
    run_id: str
    symbol: str
    side: str
    entry_ts_ms: int
    exit_ts_ms: int
    gross_bps: float
    base_net_bps: float
    stress_net_bps: float


@dataclass(slots=True)
class _RollingDepthAdjustedHistory:
    limit: int = 1_200
    values: deque[float] | None = None
    sorted_values: list[float] | None = None

    def __post_init__(self) -> None:
        self.values = deque()
        self.sorted_values = []

    def directional_z(self, current: float, side: Side) -> float:
        assert self.sorted_values is not None
        if side is Side.LONG:
            return robust_z_from_sorted(self.sorted_values, current)
        reverse = tuple(-value for value in reversed(self.sorted_values))
        return robust_z_from_sorted(reverse, -current)

    def append(self, value: float) -> None:
        assert self.values is not None and self.sorted_values is not None
        if len(self.values) >= self.limit:
            expired = self.values.popleft()
            index = bisect_left(self.sorted_values, expired)
            self.sorted_values.pop(index)
        self.values.append(value)
        insort(self.sorted_values, value)


def _supported(regime: Regime) -> bool:
    return regime in {Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN}


def _directional_regime(side: Side, regime: Regime) -> bool:
    return regime is (Regime.TREND_UP if side is Side.LONG else Regime.TREND_DOWN)


def _queue_baseline(side: Side, feature: FeatureSnapshot, regime: Regime, _: float) -> bool:
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and _supported(regime)
        and feature.spread_bps <= 8
        and feature.imbalance_top5 * direction >= 0.18
        and feature.imbalance_top10 * direction >= 0.12
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and feature.trade_imbalance_1s * direction >= 0.15
        and feature.microprice_minus_mid_bps * direction >= max(0.25, feature.spread_bps * 0.10)
    )


def _queue_strict(side: Side, feature: FeatureSnapshot, regime: Regime, _: float) -> bool:
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and _directional_regime(side, regime)
        and feature.spread_bps <= 4
        and feature.imbalance_top5 * direction >= 0.30
        and feature.imbalance_top10 * direction >= 0.20
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and feature.ofi_10s * direction > 0
        and feature.trade_imbalance_1s * direction >= 0.25
        and feature.trade_imbalance_3s * direction >= 0.20
        and feature.trade_imbalance_10s * direction >= 0.10
        and feature.microprice_minus_mid_bps * direction >= max(0.50, feature.spread_bps * 0.25)
        and feature.price_response_efficiency >= 0.55
    )


def _queue_cost_aware(
    side: Side,
    feature: FeatureSnapshot,
    regime: Regime,
    _: float,
) -> bool:
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and _supported(regime)
        and feature.spread_bps <= 4
        and feature.imbalance_top5 * direction >= 0.25
        and feature.imbalance_top10 * direction >= 0.18
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and feature.ofi_10s * direction > 0
        and feature.trade_imbalance_1s * direction >= 0.20
        and feature.trade_imbalance_3s * direction >= 0.15
        and feature.trade_imbalance_10s * direction >= 0.05
        and feature.microprice_minus_mid_bps * direction >= max(0.35, feature.spread_bps * 0.20)
        and feature.price_response_efficiency >= 0.45
    )


def _depth_baseline(
    side: Side,
    feature: FeatureSnapshot,
    regime: Regime,
    directional_z: float,
) -> bool:
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and _supported(regime)
        and feature.spread_bps <= 10
        and feature.depth_adjusted_ofi_3s_bps * direction > 0
        and directional_z >= 2
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and feature.trade_imbalance_1s * direction >= 0.15
        and feature.microprice_minus_mid_bps * direction > 0
        and feature.price_response_efficiency >= 0.40
    )


def _depth_strict(
    side: Side,
    feature: FeatureSnapshot,
    regime: Regime,
    directional_z: float,
) -> bool:
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and _directional_regime(side, regime)
        and feature.spread_bps <= 4
        and feature.depth_adjusted_ofi_3s_bps * direction >= 0.50
        and directional_z >= 3
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and feature.ofi_10s * direction > 0
        and feature.trade_imbalance_1s * direction >= 0.25
        and feature.trade_imbalance_3s * direction >= 0.20
        and feature.trade_imbalance_10s * direction >= 0.10
        and feature.microprice_minus_mid_bps * direction >= 0.50
        and feature.multi_level_microprice_10_minus_mid_bps * direction >= 0.30
        and feature.price_response_efficiency >= 0.60
    )


def _depth_cost_aware(
    side: Side,
    feature: FeatureSnapshot,
    regime: Regime,
    directional_z: float,
) -> bool:
    direction = 1 if side is Side.LONG else -1
    return (
        feature.data_healthy
        and _supported(regime)
        and feature.spread_bps <= 4
        and feature.depth_adjusted_ofi_3s_bps * direction > 0
        and directional_z >= 2.5
        and feature.ofi_250ms * direction > 0
        and feature.ofi_3s * direction > 0
        and feature.ofi_10s * direction > 0
        and feature.trade_imbalance_1s * direction >= 0.20
        and feature.trade_imbalance_3s * direction >= 0.15
        and feature.trade_imbalance_10s * direction >= 0.05
        and feature.microprice_minus_mid_bps * direction >= 0.25
        and feature.multi_level_microprice_10_minus_mid_bps * direction >= 0.10
        and feature.price_response_efficiency >= 0.50
    )


VARIANTS = (
    StrategyVariant("QUEUE_BASELINE", 500, _queue_baseline),
    StrategyVariant("QUEUE_COST_AWARE", 1_000, _queue_cost_aware),
    StrategyVariant("QUEUE_STRICT_TREND", 1_500, _queue_strict),
    StrategyVariant("DEPTH_OFI_BASELINE", 500, _depth_baseline),
    StrategyVariant("DEPTH_OFI_COST_AWARE", 1_000, _depth_cost_aware),
    StrategyVariant("DEPTH_OFI_STRICT_TREND", 1_500, _depth_strict),
)
RUNTIME_VARIANT_NAMES = tuple(
    f"RUNTIME_{strategy_id}" for strategy_id in StrategyRegistry().strategy_ids
)


def _event_rows(run_dir: Path) -> Iterator[dict[str, Any]]:
    files = tuple(sorted(run_dir.rglob("*.parquet")))
    if not files:
        raise FileNotFoundError(f"시장 archive가 없습니다: {run_dir}")
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("SET memory_limit = '192MB'")
        connection.execute("SET threads = 1")
        connection.execute("SET preserve_insertion_order = false")
        file_names = [str(path) for path in files]
        reader = connection.execute(
            """
            SELECT payload_json
            FROM read_parquet(?, union_by_name = true)
            WHERE json_extract_string(payload_json, '$.event_type') IN (
              'TRADE', 'DEPTH_UPDATE'
            )
            ORDER BY ts_ms, venue_ts_ms, symbol, payload_json
            """,
            [file_names],
        ).to_arrow_reader(batch_size=2_048)
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


def _resolve_outcome(signal: ResearchSignal, frame: BookFrame) -> ResearchOutcome:
    if signal.side is Side.LONG:
        exit_price = float(frame.bids[0][0])
        gross_bps = (exit_price - signal.entry_price) / signal.entry_price * 10_000
    else:
        exit_price = float(frame.asks[0][0])
        gross_bps = (signal.entry_price - exit_price) / signal.entry_price * 10_000
    return ResearchOutcome(
        variant=signal.variant,
        run_id=signal.run_id,
        symbol=signal.symbol,
        side=signal.side.value,
        entry_ts_ms=signal.ts_ms,
        exit_ts_ms=frame.ts_ms,
        gross_bps=gross_bps,
        base_net_bps=gross_bps - 13,
        stress_net_bps=gross_bps - 25,
    )


def research_run_horizons(
    run_id: str,
    run_dir: Path,
    *,
    horizon_ms_values: tuple[int, ...],
) -> dict[int, list[ResearchOutcome]]:
    if not horizon_ms_values or any(value <= 0 for value in horizon_ms_values):
        raise ValueError("연구 horizon은 양수여야 합니다.")
    features: dict[str, FeatureEngine] = defaultdict(FeatureEngine)
    regimes = RegimeClassifier()
    runtime_registry = StrategyRegistry()
    for strategy_id in runtime_registry.strategy_ids:
        runtime_registry.configure(
            strategy_id,
            mode=StrategyMode.SHADOW,
            long_enabled=True,
            short_enabled=True,
        )
    runtime_evaluator = StrategySignalEvaluator()
    history: dict[str, _RollingDepthAdjustedHistory] = defaultdict(_RollingDepthAdjustedHistory)
    last_evaluation_ms: dict[str, int] = {}
    alignment_started_ms: dict[tuple[str, str, Side], int] = {}
    fired: set[tuple[str, str, Side]] = set()
    cooldown_until_ms: dict[tuple[str, str, Side, int], int] = {}
    pending: dict[tuple[str, str], list[ResearchSignal]] = defaultdict(list)
    outcomes: dict[int, list[ResearchOutcome]] = {
        horizon_ms: [] for horizon_ms in horizon_ms_values
    }

    for payload in _event_rows(run_dir):
        event_type = str(payload.get("event_type"))
        symbol = str(payload.get("symbol"))
        if event_type == "TRADE":
            try:
                features[symbol].ingest_trade(_trade_tick(payload))
            except FeatureInputError:
                continue
            continue
        if event_type != "DEPTH_UPDATE":
            continue
        try:
            frame = _book_frame(payload)
            features[symbol].ingest_book(frame)
        except (FeatureInputError, KeyError, ValueError):
            continue

        waiting = pending.get((run_id, symbol), [])
        if waiting:
            remaining: list[ResearchSignal] = []
            for signal in waiting:
                if frame.ts_ms >= signal.target_ts_ms:
                    outcomes[signal.horizon_ms].append(_resolve_outcome(signal, frame))
                else:
                    remaining.append(signal)
            pending[(run_id, symbol)] = remaining

        if frame.ts_ms - last_evaluation_ms.get(symbol, -(10**18)) < 500:
            continue
        last_evaluation_ms[symbol] = frame.ts_ms
        try:
            snapshot = features[symbol].snapshot()
        except FeatureInputError:
            continue
        regime = regimes.classify(snapshot)
        symbol_history = history[symbol]
        directional_z = {
            side: symbol_history.directional_z(snapshot.depth_adjusted_ofi_3s_bps, side)
            for side in Side
        }
        for variant in VARIANTS:
            for side in Side:
                key = (variant.name, symbol, side)
                aligned = variant.predicate(side, snapshot, regime, directional_z[side])
                if not aligned:
                    alignment_started_ms.pop(key, None)
                    fired.discard(key)
                    continue
                started_ms = alignment_started_ms.setdefault(key, snapshot.ts_ms)
                confirmed_ms = snapshot.ts_ms - started_ms
                if confirmed_ms < variant.confirmation_ms or key in fired:
                    continue
                fired.add(key)
                entry_price = float(frame.asks[0][0] if side is Side.LONG else frame.bids[0][0])
                for horizon_ms in horizon_ms_values:
                    cooldown_key = (*key, horizon_ms)
                    if snapshot.ts_ms < cooldown_until_ms.get(cooldown_key, 0):
                        continue
                    signal = ResearchSignal(
                        variant=variant.name,
                        run_id=run_id,
                        symbol=symbol,
                        side=side,
                        ts_ms=snapshot.ts_ms,
                        entry_price=entry_price,
                        target_ts_ms=snapshot.ts_ms + horizon_ms,
                        horizon_ms=horizon_ms,
                    )
                    pending[(run_id, symbol)].append(signal)
                    cooldown_until_ms[cooldown_key] = signal.target_ts_ms
        for evaluated in runtime_evaluator.evaluate(
            runtime_registry,
            snapshot,
            regime,
        ):
            if evaluated.decision.status is not CandidateStatus.QUALIFIED:
                continue
            variant_name = f"RUNTIME_{evaluated.decision.strategy_id}"
            entry_price = float(
                frame.asks[0][0]
                if evaluated.decision.side is Side.LONG
                else frame.bids[0][0]
            )
            for horizon_ms in horizon_ms_values:
                key = (variant_name, symbol, evaluated.decision.side, horizon_ms)
                if snapshot.ts_ms < cooldown_until_ms.get(key, 0):
                    continue
                signal = ResearchSignal(
                    variant=variant_name,
                    run_id=run_id,
                    symbol=symbol,
                    side=evaluated.decision.side,
                    ts_ms=snapshot.ts_ms,
                    entry_price=entry_price,
                    target_ts_ms=snapshot.ts_ms + horizon_ms,
                    horizon_ms=horizon_ms,
                )
                pending[(run_id, symbol)].append(signal)
                cooldown_until_ms[key] = signal.target_ts_ms
        symbol_history.append(snapshot.depth_adjusted_ofi_3s_bps)
    return outcomes


def research_run(run_id: str, run_dir: Path, *, horizon_ms: int) -> list[ResearchOutcome]:
    return research_run_horizons(
        run_id,
        run_dir,
        horizon_ms_values=(horizon_ms,),
    )[horizon_ms]


def _profile(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "sample_size": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "expectancy_bps": None,
            "profit_factor": None,
            "net_sum_bps": 0.0,
        }
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "sample_size": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(values),
        "expectancy_bps": fmean(values),
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "net_sum_bps": sum(values),
    }


def summarize(outcomes: list[ResearchOutcome]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    variant_names = tuple(variant.name for variant in VARIANTS) + RUNTIME_VARIANT_NAMES
    for variant_name in variant_names:
        selected = [row for row in outcomes if row.variant == variant_name]
        result[variant_name] = {
            "gross": _profile([row.gross_bps for row in selected]),
            "base": _profile([row.base_net_bps for row in selected]),
            "stress": _profile([row.stress_net_bps for row in selected]),
        }
    return result


def _dataset_slice(run_id: str, run_dir: Path) -> DatasetSlice:
    files = tuple(sorted(run_dir.rglob("*.parquet")))
    if not files:
        raise FileNotFoundError(f"시장 archive가 없습니다: {run_dir}")
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(run_dir).as_posix().encode())
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


def _split_result(
    run_ids: tuple[str, ...],
    cached: dict[str, dict[int, list[ResearchOutcome]]],
    *,
    horizon_seconds: int,
    split: str,
    train_boundary_ms: int,
    validation_boundary_ms: int,
) -> tuple[dict[str, Any], list[ResearchOutcome]]:
    outcomes: list[ResearchOutcome] = []
    per_run: dict[str, int] = {}
    raw_count = 0
    horizon_ms = horizon_seconds * 1_000
    for run_id in run_ids:
        raw_rows = cached[run_id][horizon_ms]
        raw_count += len(raw_rows)
        if split == "train":
            rows = [
                row
                for row in raw_rows
                if row.exit_ts_ms < train_boundary_ms - horizon_ms
            ]
        elif split == "validation":
            rows = [
                row
                for row in raw_rows
                if row.entry_ts_ms > train_boundary_ms + horizon_ms
                and row.exit_ts_ms < validation_boundary_ms - horizon_ms
            ]
        else:
            rows = [
                row
                for row in raw_rows
                if row.entry_ts_ms > validation_boundary_ms + horizon_ms
            ]
        outcomes.extend(rows)
        per_run[run_id] = len(rows)
    return (
        {
            "run_outcome_counts": per_run,
            "summary": summarize(outcomes),
            "outcome_count": len(outcomes),
            "raw_outcome_count": raw_count,
            "purge_embargo_excluded_count": raw_count - len(outcomes),
            "outcome_examples": [asdict(row) for row in outcomes[:20]],
        },
        outcomes,
    )


def _selection_bias_report(
    train_validation: list[ResearchOutcome],
    oos: list[ResearchOutcome],
    *,
    train_validation_run_ids: tuple[str, ...],
    seed: int,
) -> dict[str, object]:
    variant_names = tuple(variant.name for variant in VARIANTS) + RUNTIME_VARIANT_NAMES
    fold_returns: dict[str, list[float]] = {}
    for variant_name in variant_names:
        values: list[float] = []
        for run_id in train_validation_run_ids:
            selected = [
                row.base_net_bps
                for row in train_validation
                if row.variant == variant_name and row.run_id == run_id
            ]
            values.append(fmean(selected) if selected else 0.0)
        fold_returns[variant_name] = values
    selected_variant = max(
        variant_names,
        key=lambda name: (fmean(fold_returns[name]), name),
    )
    selected_oos = [
        row.base_net_bps for row in oos if row.variant == selected_variant
    ]
    return {
        "candidate_count": len(variant_names),
        "selected_on_train_validation": selected_variant,
        "pbo": probability_of_backtest_overfitting(fold_returns),
        "oos_deflated_sharpe": deflated_sharpe_ratio(
            selected_oos,
            trials=len(variant_names),
        ),
        "oos_expectancy_bootstrap_95": bootstrap_mean_interval(
            selected_oos,
            seed=seed,
        ),
        "no_trade_baseline_bps": 0.0,
        "selection_is_not_profitability_proof": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("data/market-parquet-v6/venue=BINANCE_USDM"),
    )
    parser.add_argument("--horizon-seconds", type=int, action="append")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    horizons = tuple(args.horizon_seconds or (15, 30, 60, 180))
    if any(horizon <= 0 for horizon in horizons):
        raise ValueError("검증 horizon은 양수여야 합니다.")
    project_root = Path(__file__).resolve().parents[1]
    all_run_ids = (
        *DEFAULT_RESEARCH_TRAIN_RUNS,
        *DEFAULT_VALIDATION_RUNS,
        *DEFAULT_OOS_RUNS,
    )
    dataset = tuple(
        _dataset_slice(run_id, args.archive / f"run={run_id}")
        for run_id in all_run_ids
    )
    dataset_by_run = {row.run_id: row for row in dataset}
    train_boundary_ms = max(
        dataset_by_run[run_id].end_ts_ms for run_id in DEFAULT_RESEARCH_TRAIN_RUNS
    )
    validation_boundary_ms = max(
        dataset_by_run[run_id].end_ts_ms for run_id in DEFAULT_VALIDATION_RUNS
    )
    protocol = ResearchProtocol(
        hypothesis_id="HYP-STRATEGY-LEAGUE-A-J-COST-AFTER-OOS-V1",
        strategy_id="STRATEGY_LEAGUE_A_J",
        strategy_version=STRATEGY_VERSION,
        feature_version="FEATURE_ENGINE_RUNTIME_V1",
        cost_model_version="TOP_OF_BOOK_BASE13_STRESS25_V1",
        parameter_grid={
            "variant": tuple(variant.name for variant in VARIANTS) + RUNTIME_VARIANT_NAMES,
            "horizon_seconds": horizons,
        },
        horizon_seconds=horizons,
        base_cost_bps=13,
        stress_cost_bps=25,
        seed=20260825,
        purge_ms=60_000,
        embargo_ms=60_000,
        falsification_criteria=(
            "OOS BASE expectancy_bps <= 0",
            "OOS BASE profit_factor < 1",
            "OOS STRESS expectancy_bps <= 0",
            "OOS sample_size < 30",
        ),
        baseline_ids=("NO_TRADE", "RUNTIME_A_J", "GROSS_BEFORE_COST"),
    )
    started_ts_ms = time.time_ns() // 1_000_000
    manifest = protocol.manifest(
        dataset,
        code_hash=git_commit(),
        config_hash=_config_hash(project_root),
        generated_ts_ms=started_ts_ms,
    )
    result: dict[str, Any] = {
        "schema_version": 2,
        "method": {
            "signal_lookahead": False,
            "chronological_split": True,
            "split_order": ["train", "validation", "oos"],
            "purge_ms": protocol.purge_ms,
            "embargo_ms": protocol.embargo_ms,
            "runtime_strategy_baselines": list(RUNTIME_VARIANT_NAMES),
            "entry_exit": "actual ask/bid top of reconstructed 10-level book",
            "horizon_seconds": list(horizons),
            "base_cost_bps": 13,
            "stress_cost_bps": 25,
            "evaluation_interval_ms": 500,
            "annualization": False,
            "horizon_specific_purge_embargo": True,
            "train_boundary_ms": train_boundary_ms,
            "validation_boundary_ms": validation_boundary_ms,
        },
        "train_runs": list(DEFAULT_RESEARCH_TRAIN_RUNS),
        "validation_runs": list(DEFAULT_VALIDATION_RUNS),
        "oos_runs": list(DEFAULT_OOS_RUNS),
        "horizons": {},
        "operational_metrics": {
            "status": "NOT_RUN",
            "reason": "연구 계산과 별도 실제 LIVE soak 증거에서 측정",
        },
    }
    cached = {
        run_id: research_run_horizons(
            run_id,
            args.archive / f"run={run_id}",
            horizon_ms_values=tuple(horizon * 1_000 for horizon in horizons),
        )
        for run_id in all_run_ids
    }
    for horizon in horizons:
        split_outputs: dict[str, Any] = {}
        split_outcomes: dict[str, list[ResearchOutcome]] = {}
        for split, run_ids in (
            ("train", DEFAULT_RESEARCH_TRAIN_RUNS),
            ("validation", DEFAULT_VALIDATION_RUNS),
            ("oos", DEFAULT_OOS_RUNS),
        ):
            split_outputs[split], split_outcomes[split] = _split_result(
                run_ids,
                cached,
                horizon_seconds=horizon,
                split=split,
                train_boundary_ms=train_boundary_ms,
                validation_boundary_ms=validation_boundary_ms,
            )
        split_outputs["selection_bias"] = _selection_bias_report(
            [*split_outcomes["train"], *split_outcomes["validation"]],
            split_outcomes["oos"],
            train_validation_run_ids=(
                *DEFAULT_RESEARCH_TRAIN_RUNS,
                *DEFAULT_VALIDATION_RUNS,
            ),
            seed=protocol.seed + horizon,
        )
        result["horizons"][str(horizon)] = split_outputs
    completed_ts_ms = time.time_ns() // 1_000_000
    output = {
        "manifest": finalize_research_manifest(
            manifest,
            result=result,
            completed_ts_ms=completed_ts_ms,
        ),
        "result": result,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
