# 동결된 공개시장 이벤트를 실제 PAPER 포트폴리오 경로로 100후보에 공급한다.

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import fmean
from typing import Any

from backend.app.candidates import CandidatePlan
from backend.app.costing import CostProfile
from backend.app.execution import BookSnapshot
from backend.app.execution.portfolio import PaperPortfolioEngine
from backend.app.features import BookFrame, FeatureEngine, FeatureInputError, FeatureSnapshot
from backend.app.market_data import Candle, CandleBuilder
from backend.app.regime import Regime, RegimeClassifier
from backend.app.research import (
    ALPHA_EVALUATION_INTERVAL_SECONDS,
    HORIZON_MAXIMUM_HOLD_MS,
    AlphaFeatureBuilder,
    ResearchCandidatePlanBuilder,
    ResearchInstrumentMetadata,
    ResearchTrialSpec,
    ScreeningAccountResult,
    ScreeningStatus,
    ScreeningTrade,
    TrialScreeningResult,
    build_multiple_testing_report,
    build_screening_report,
    build_trailing_ablation_report,
    build_walk_forward_report,
    evaluate_alpha,
    load_research_instruments,
    point_in_time_volatility_regime,
    preregistered_trials,
)
from backend.app.research.cost_covered_exit_variants import (
    COST_COVERED_EXIT_VARIANT_BATCH_ID,
    cost_covered_exit_variant_trials,
)
from backend.app.research.strategy100_dataset_v2 import (
    archive_files_for_logical_run,
    load_bound_manifest,
)
from backend.app.research.strategy100_warmup import FrozenStrategy100Warmup
from backend.app.strategies.shadow import ShadowLedger
from scripts.export_cost_covered_exit_variant_manifest import (
    VARIANT_BOUND_SOURCE_FILES,
)
from scripts.export_strategy_100_manifest import BOUND_SOURCE_FILES
from scripts.research_intraday_candidates import _book_frame, _event_rows, _trade_tick

SCREENING_INTERVALS = (1, 300, 900, 3_600, 14_400, 21_600)
RESEARCH_FEATURE_HISTORY_BARS = 640
RESEARCH_FEATURE_SNAPSHOT_INTERVAL_MS = 500
DEFAULT_RESEARCH_OUTPUTS = (
    Path("evidence/STRATEGY_100_SCREENING.json"),
    Path("evidence/STRATEGY_100_SCREENING_TRADES.jsonl"),
    Path("evidence/STRATEGY_100_SCREENING_AUDIT.json"),
    Path("evidence/TRAILING_ABLATION.json"),
    Path("evidence/WALK_FORWARD_RESULTS.json"),
    Path("evidence/MULTIPLE_TESTING_RESULTS.json"),
)
ENTRY_SETTLEMENT_MARGIN_MS = 1_000
CROSS_SECTIONAL_GRACE_MS = 60_000
ENTRY_REJECTION_EVENTS = frozenset(
    {
        "LEAGUE_SIZING_REJECTED",
        "LEAGUE_RISK_REJECTED",
        "LEAGUE_DUPLICATE_SYMBOL_REJECTED",
        "LEAGUE_MAX_POSITIONS_REJECTED",
        "ENTRY_EXPIRED",
        "ENTRY_REJECTED",
        "ENTRY_UNFILLED",
    }
)


class ResearchCpuBudget:
    """LIVE 서비스보다 낮은 협조 CPU 비율로 후보 계산을 진행한다."""

    def __init__(
        self,
        *,
        target_cpu_ratio: float,
        monotonic: Callable[[], float] = time.monotonic,
        process_time: Callable[[], float] = time.process_time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < target_cpu_ratio <= 1:
            raise ValueError("연구 CPU 목표 비율은 0 초과 1 이하여야 합니다.")
        self._target_cpu_ratio = target_cpu_ratio
        self._monotonic = monotonic
        self._process_time = process_time
        self._sleeper = sleeper
        self._last_wall = monotonic()
        self._last_cpu = process_time()

    def checkpoint(self) -> None:
        elapsed_wall = max(0.0, self._monotonic() - self._last_wall)
        elapsed_cpu = max(0.0, self._process_time() - self._last_cpu)
        required_wall = elapsed_cpu / self._target_cpu_ratio
        sleep_seconds = max(0.0, required_wall - elapsed_wall)
        if sleep_seconds > 0:
            self._sleeper(sleep_seconds)
        self._last_wall += elapsed_wall + sleep_seconds
        self._last_cpu += elapsed_cpu


@dataclass(slots=True)
class AccountCounters:
    evaluated_event_count: int = 0
    signal_count: int = 0
    attempted_entry_count: int = 0
    rejected_entry_count: int = 0
    rejection_counts: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class TrialIntegrity:
    deterministic_signal_pass: bool = True
    no_lookahead_pass: bool = True
    recursive_dependency_pass: bool = False
    recursive_comparison_count: int = 0
    recursive_mismatch_count: int = 0
    evidence_codes: set[str] = field(default_factory=set)


@dataclass(slots=True)
class RunDiagnostics:
    run_id: str
    split: str
    event_count: int = 0
    book_event_count: int = 0
    trade_event_count: int = 0
    rejected_market_event_count: int = 0
    outside_research_universe_event_count: int = 0
    missing_feature_count: int = 0
    alpha_evaluation_count: int = 0
    alpha_signal_count: int = 0
    plan_count: int = 0
    censored_position_count: int = 0
    censored_pending_entry_count: int = 0
    first_ts_ms: int | None = None
    last_ts_ms: int | None = None
    audit_counts: Counter[str] = field(default_factory=Counter)
    warmup_candle_count: int = 0
    warmup_symbol_count: int = 0
    feature_snapshot_count: int = 0
    feature_snapshot_throttled_count: int = 0
    paper_execution_book_count: int = 0
    paper_execution_book_skipped_count: int = 0
    position_health_evaluation_count: int = 0
    alpha_snapshot_cache_hit_count: int = 0
    alpha_snapshot_cache_miss_count: int = 0


@dataclass(frozen=True, slots=True)
class ValidationFold:
    fold_id: str
    start_ts_ms: int
    end_ts_ms: int


@dataclass(frozen=True, slots=True)
class ResearchExecutionWindow:
    window_id: str
    horizon: str
    entry_start_ts_ms: int
    entry_cutoff_ts_ms: int
    observation_end_ts_ms: int
    maximum_holding_ms: int
    purge_embargo_ms: int

    def __post_init__(self) -> None:
        if (
            not self.window_id
            or self.horizon not in HORIZON_MAXIMUM_HOLD_MS
            or self.entry_start_ts_ms < 0
            or self.entry_cutoff_ts_ms <= self.entry_start_ts_ms
            or self.maximum_holding_ms != HORIZON_MAXIMUM_HOLD_MS[self.horizon]
            or self.purge_embargo_ms != self.maximum_holding_ms
            or self.observation_end_ts_ms
            < self.entry_cutoff_ts_ms + self.maximum_holding_ms + ENTRY_SETTLEMENT_MARGIN_MS
        ):
            raise ValueError("research 실행 window의 진입·관찰 경계가 잘못됐습니다.")

    def permits_entry(self, ts_ms: int) -> bool:
        return self.entry_start_ts_ms <= ts_ms < self.entry_cutoff_ts_ms

    def contains_trade(self, trade: ScreeningTrade) -> bool:
        return (
            trade.entry_ts_ms >= self.entry_start_ts_ms
            and trade.entry_ts_ms < self.entry_cutoff_ts_ms
            and trade.exit_ts_ms <= self.observation_end_ts_ms
        )


@dataclass(frozen=True, slots=True)
class ResearchAccountCarry:
    current_equity_usdt: Decimal = Decimal("1000")
    peak_equity_usdt: Decimal = Decimal("1000")
    global_consecutive_losses: int = 0

    def __post_init__(self) -> None:
        if (
            self.current_equity_usdt <= 0
            or self.peak_equity_usdt < self.current_equity_usdt
            or self.peak_equity_usdt < Decimal("1000")
            or self.global_consecutive_losses < 0
        ):
            raise ValueError("research 계좌 carry 상태가 잘못됐습니다.")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest_checksum(manifest: Mapping[str, object]) -> str:
    material = dict(manifest)
    material.pop("manifest_sha256", None)
    return hashlib.sha256(_canonical_json(material).encode()).hexdigest()


def _verify_manifest(manifest: Mapping[str, object], *, name: str) -> str:
    claimed = manifest.get("manifest_sha256")
    actual = _manifest_checksum(manifest)
    if claimed != actual:
        raise ValueError(f"{name} 내부 checksum이 다릅니다.")
    return actual


def _verify_current_bound_sources(
    trial_manifest: Mapping[str, object],
    *,
    bound_source_files: Sequence[Path] = BOUND_SOURCE_FILES,
) -> None:
    claimed = trial_manifest.get("source_checksums")
    if not isinstance(claimed, Mapping):
        raise ValueError("trial manifest source checksum 목록이 없습니다.")
    expected_paths = {path.as_posix() for path in bound_source_files}
    if set(claimed) != expected_paths:
        raise ValueError("trial manifest source checksum 경로가 현재 연구 bundle과 다릅니다.")
    mismatches = [
        path.as_posix()
        for path in bound_source_files
        if claimed.get(path.as_posix()) != hashlib.sha256(path.read_bytes()).hexdigest()
    ]
    if mismatches:
        raise ValueError(
            "trial manifest source checksum이 현재 코드와 다릅니다: " + ", ".join(mismatches)
        )


def _load_inputs(
    trial_path: Path,
    dataset_path: Path,
    instrument_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, object],
]:
    trial_bytes = trial_path.read_bytes()
    dataset_bytes = dataset_path.read_bytes()
    instrument_bytes = instrument_path.read_bytes()
    trial: dict[str, Any] = json.loads(trial_bytes)
    dataset: dict[str, Any] = json.loads(dataset_bytes)
    instruments: dict[str, Any] = json.loads(instrument_bytes)
    trial_sha = _verify_manifest(trial, name="trial manifest")
    manifest_kind = str(trial.get("manifest_kind", "STRATEGY_100_FROZEN_BATCH"))
    is_cost_covered_variant = manifest_kind == "COST_COVERED_EXIT_VARIANT_BATCH"
    _verify_current_bound_sources(
        trial,
        bound_source_files=(
            VARIANT_BOUND_SOURCE_FILES if is_cost_covered_variant else BOUND_SOURCE_FILES
        ),
    )
    dataset_sha = _verify_manifest(dataset, name="dataset manifest")
    _verify_manifest(instruments, name="instrument manifest")
    if (
        trial.get("status") != "PREREGISTERED_NOT_EXECUTED"
        or trial.get("runtime_active_count") != 0
        or trial.get("live_shadow_count") != 0
        or trial.get("paper_only") is not True
        or trial.get("real_orders_enabled") is not False
        or trial.get("private_api_enabled") is not False
    ):
        raise ValueError("trial manifest의 PAPER 계약이 잘못됐습니다.")
    linked = dataset.get("trial_manifest")
    if not isinstance(linked, Mapping):
        raise ValueError("dataset manifest의 trial 연결정보가 없습니다.")
    linked_manifest_sha = trial_sha
    linked_file_sha = hashlib.sha256(trial_bytes).hexdigest()
    parent_sha: str | None = None
    if is_cost_covered_variant:
        parent = trial.get("parent_trial_manifest")
        if (
            trial.get("batch_id") != COST_COVERED_EXIT_VARIANT_BATCH_ID
            or trial.get("trial_count") != len(cost_covered_exit_variant_trials())
            or trial.get("screening_eligible_count") != len(
                cost_covered_exit_variant_trials()
            )
            or not isinstance(parent, Mapping)
        ):
            raise ValueError("비용회수형 변형 manifest 계약이 잘못됐습니다.")
        parent_path = Path(str(parent.get("path", "")))
        parent_bytes = parent_path.read_bytes()
        parent_manifest: dict[str, Any] = json.loads(parent_bytes)
        parent_sha = _verify_manifest(parent_manifest, name="parent trial manifest")
        if (
            parent_manifest.get("trial_count") != 100
            or parent.get("manifest_sha256") != parent_sha
            or parent.get("file_sha256") != hashlib.sha256(parent_bytes).hexdigest()
        ):
            raise ValueError("비용회수형 변형의 원본 100후보 계보가 다릅니다.")
        linked_manifest_sha = parent_sha
        linked_file_sha = hashlib.sha256(parent_bytes).hexdigest()
    elif trial.get("trial_count") != 100:
        raise ValueError("동결 100후보 trial manifest 수가 잘못됐습니다.")
    if (
        linked.get("manifest_sha256") != linked_manifest_sha
        or linked.get("file_sha256") != linked_file_sha
        or dataset.get("status") != "FROZEN_HISTORICAL_FORWARD_PENDING"
        or dataset.get("paper_only") is not True
        or dataset.get("real_orders_enabled") is not False
        or dataset.get("private_api_enabled") is not False
    ):
        raise ValueError("dataset과 trial manifest 연결 또는 PAPER 경계가 다릅니다.")
    v2_source_hashes: dict[str, object] = {}
    if int(str(dataset.get("schema_version", 0))) >= 3:
        cut_reference = dataset.get("live_public_cut")
        warmup_reference = dataset.get("warmup_manifest")
        if not isinstance(cut_reference, Mapping) or not isinstance(
            warmup_reference, Mapping
        ):
            raise ValueError("V2 dataset의 LIVE_PUBLIC cut 또는 워밍업 연결이 없습니다.")
        cut_manifest, _, cut_file_sha = load_bound_manifest(
            cut_reference,
            binding_path=dataset_path,
            expected_status="FROZEN_LIVE_PUBLIC_CUT",
            name="LIVE_PUBLIC cut",
        )
        warmup_manifest, _, warmup_file_sha = load_bound_manifest(
            warmup_reference,
            binding_path=dataset_path,
            expected_status="FROZEN_PUBLIC_KLINE_WARMUP",
            name="public kline warmup",
        )
        if (
            cut_manifest.get("private_api_enabled") is True
            or cut_manifest.get("auth_required") is not False
            or warmup_manifest.get("private_api_enabled") is not False
            or warmup_manifest.get("auth_required") is not False
        ):
            raise ValueError("V2 dataset 공개시장 입력의 private API 경계가 잘못됐습니다.")
        v2_source_hashes = {
            "live_public_cut_manifest_sha256": cut_manifest["manifest_sha256"],
            "live_public_cut_file_sha256": cut_file_sha,
            "warmup_manifest_sha256": warmup_manifest["manifest_sha256"],
            "warmup_manifest_file_sha256": warmup_file_sha,
        }
    return (
        trial,
        dataset,
        instruments,
        {
            "trial_manifest_sha256": trial_sha,
            "trial_manifest_file_sha256": hashlib.sha256(trial_bytes).hexdigest(),
            "dataset_manifest_sha256": dataset_sha,
            "dataset_manifest_file_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "instrument_manifest_sha256": str(instruments["manifest_sha256"]),
            "instrument_manifest_file_sha256": hashlib.sha256(instrument_bytes).hexdigest(),
            "trial_manifest_kind": manifest_kind,
            "parent_trial_manifest_sha256": parent_sha,
            **v2_source_hashes,
        },
    )


def _trials_for_manifest(trial_manifest: Mapping[str, object]) -> tuple[ResearchTrialSpec, ...]:
    manifest_kind = str(
        trial_manifest.get("manifest_kind", "STRATEGY_100_FROZEN_BATCH")
    )
    if manifest_kind == "COST_COVERED_EXIT_VARIANT_BATCH":
        trials = cost_covered_exit_variant_trials()
    elif manifest_kind == "STRATEGY_100_FROZEN_BATCH":
        trials = preregistered_trials()
    else:
        raise ValueError(f"알 수 없는 research trial manifest 종류입니다: {manifest_kind}")
    rows = trial_manifest.get("trials")
    if not isinstance(rows, list):
        raise ValueError("trial manifest에 trial 목록이 없습니다.")
    manifest_ids = [str(row.get("trial_id", "")) for row in rows if isinstance(row, Mapping)]
    expected_ids = [trial.trial_id for trial in trials]
    if manifest_ids != expected_ids:
        raise ValueError("trial manifest와 현재 실행 trial 순서 또는 ID가 다릅니다.")
    return trials


def _validation_folds(
    dataset: Mapping[str, object],
) -> tuple[ValidationFold, ...]:
    rows = dataset.get("runs")
    if not isinstance(rows, list):
        raise ValueError("dataset Run 목록이 없습니다.")
    validation = sorted(
        (
            (int(row["start_ts_ms"]), int(row["end_ts_ms"]), str(row["run_id"]))
            for row in rows
            if isinstance(row, Mapping) and row.get("role") == "VALIDATION"
        ),
        key=lambda value: (value[0], value[1], value[2]),
    )
    if len(validation) != 2:
        raise ValueError("사전등록 PBO는 정확히 두 Validation Run의 네 시간순 fold를 요구합니다.")
    folds: list[ValidationFold] = []
    for start, end, run_id in validation:
        if end <= start:
            raise ValueError("Validation Run 시간 범위가 잘못됐습니다.")
        middle = start + (end - start) // 2
        if middle <= start or middle >= end:
            raise ValueError("Validation Run을 두 fold로 나눌 수 없습니다.")
        folds.extend(
            (
                ValidationFold(f"{run_id}:A", start, middle),
                ValidationFold(f"{run_id}:B", middle, end),
            )
        )
    return tuple(folds)


def _effective_validation_folds(
    folds: Sequence[ValidationFold],
    *,
    maximum_holding_ms: int,
    purge_embargo_ms: int,
) -> tuple[ValidationFold, ...]:
    if maximum_holding_ms <= 0 or purge_embargo_ms != maximum_holding_ms:
        raise ValueError("horizon별 purge·embargo는 최대 보유시간과 같아야 합니다.")
    effective: list[ValidationFold] = []
    for fold in folds:
        start = fold.start_ts_ms + purge_embargo_ms
        end = fold.end_ts_ms - purge_embargo_ms
        entry_cutoff = end - maximum_holding_ms - ENTRY_SETTLEMENT_MARGIN_MS
        if entry_cutoff <= start:
            return ()
        effective.append(ValidationFold(fold.fold_id, start, end))
    return tuple(effective)


def _screening_boundaries(dataset: Mapping[str, object]) -> tuple[int, int]:
    splits = dataset.get("historical_splits")
    if not isinstance(splits, Mapping):
        raise ValueError("dataset split 경계가 없습니다.")
    return (
        int(str(splits["validation_start_ts_ms"])),
        int(str(splits["final_oos_start_ts_ms"])),
    )


def _run_execution_windows(
    row: Mapping[str, object],
    *,
    validation_folds: Sequence[ValidationFold],
    validation_start_ms: int,
    horizon: str,
    maximum_holding_ms: int,
    purge_embargo_ms: int,
) -> tuple[ResearchExecutionWindow, ...]:
    run_id = str(row.get("run_id", ""))
    role = str(row.get("role", ""))
    run_start = int(str(row.get("start_ts_ms", -1)))
    run_end = int(str(row.get("end_ts_ms", -1)))
    if (
        not run_id
        or run_start < 0
        or run_end <= run_start
        or horizon not in HORIZON_MAXIMUM_HOLD_MS
        or maximum_holding_ms != HORIZON_MAXIMUM_HOLD_MS[horizon]
        or purge_embargo_ms != maximum_holding_ms
    ):
        raise ValueError("dataset Run 실행 경계가 잘못됐습니다.")
    raw_windows: tuple[tuple[str, int, int], ...]
    if role == "TRAIN":
        observation_end = min(run_end, validation_start_ms - purge_embargo_ms)
        raw_windows = ((f"{run_id}:TRAIN", run_start + purge_embargo_ms, observation_end),)
    elif role == "VALIDATION":
        raw_windows = tuple(
            (fold.fold_id, fold.start_ts_ms, fold.end_ts_ms)
            for fold in validation_folds
            if fold.fold_id.startswith(f"{run_id}:")
        )
    else:
        raise ValueError("Stage 1 execution window에는 Train·Validation만 허용됩니다.")
    windows: list[ResearchExecutionWindow] = []
    for window_id, start, end in raw_windows:
        entry_cutoff = end - maximum_holding_ms - ENTRY_SETTLEMENT_MARGIN_MS
        if entry_cutoff <= start:
            continue
        windows.append(
            ResearchExecutionWindow(
                window_id=window_id,
                horizon=horizon,
                entry_start_ts_ms=start,
                entry_cutoff_ts_ms=entry_cutoff,
                observation_end_ts_ms=end,
                maximum_holding_ms=maximum_holding_ms,
                purge_embargo_ms=purge_embargo_ms,
            )
        )
    if any(
        left.observation_end_ts_ms > right.entry_start_ts_ms
        for left, right in zip(windows, windows[1:], strict=False)
    ):
        raise ValueError("research execution window가 겹칩니다.")
    return tuple(windows)


def _trade_inside_purged_split(
    trade: ScreeningTrade,
    *,
    execution_windows_by_horizon: Mapping[str, Sequence[ResearchExecutionWindow]],
    trial_horizon_by_id: Mapping[str, str],
) -> bool:
    horizon = trial_horizon_by_id.get(trade.trial_id)
    if horizon is None:
        return False
    return any(
        window.contains_trade(trade) for window in execution_windows_by_horizon.get(horizon, ())
    )


def _book_snapshot(frame: BookFrame) -> BookSnapshot:
    return BookSnapshot(
        venue=frame.venue,
        symbol=frame.symbol,
        ts_ms=frame.ts_ms,
        bids=frame.bids,
        asks=frame.asks,
        sequence_valid=frame.sequence_valid,
        stale=frame.stale,
        receive_ts_ms=frame.ts_ms + max(0, round(frame.lag_ms)),
    )


def _account_key(trial_id: str, profile: CostProfile) -> tuple[str, str]:
    return trial_id, profile.value


class Strategy100RunExecutor:
    """한 source Run을 90전략·180독립계좌의 실제 PAPER lifecycle로 처리한다."""

    def __init__(
        self,
        *,
        run_id: str,
        split: str,
        archive_dir: Path,
        trials: tuple[ResearchTrialSpec, ...],
        instruments: Mapping[str, ResearchInstrumentMetadata],
        account_counters: dict[tuple[str, str], AccountCounters],
        trial_integrity: dict[str, TrialIntegrity],
        execution_windows_by_horizon: Mapping[
            str,
            Sequence[ResearchExecutionWindow],
        ],
        account_carry: Mapping[tuple[str, str], ResearchAccountCarry],
        archive_files: Sequence[Path] | None = None,
        warmup_candles: Sequence[Candle] = (),
        research_symbols: Sequence[str] | None = None,
    ) -> None:
        if split not in {"TRAIN", "VALIDATION"}:
            raise ValueError("Stage 1 executor에는 Train·Validation만 허용됩니다.")
        self.run_id = run_id
        self.split = split
        self.archive_dir = archive_dir
        self.archive_files = tuple(archive_files) if archive_files is not None else None
        self.research_symbols = (
            frozenset(research_symbols) if research_symbols is not None else None
        )
        if self.research_symbols is not None and (
            not self.research_symbols or any(not symbol for symbol in self.research_symbols)
        ):
            raise ValueError(
                "명시적 research 종목 목록은 비어 있거나 빈 종목을 포함할 수 없습니다."
            )
        self.trials = tuple(trial for trial in trials if trial.screening_eligible)
        self.instruments = instruments
        self.account_counters = account_counters
        self.trial_integrity = trial_integrity
        self.execution_windows_by_horizon = {
            horizon: tuple(windows) for horizon, windows in execution_windows_by_horizon.items()
        }
        if not any(self.execution_windows_by_horizon.values()):
            raise ValueError("research Run에 execution window가 없습니다.")
        for horizon, windows in self.execution_windows_by_horizon.items():
            if horizon not in HORIZON_MAXIMUM_HOLD_MS or any(
                window.horizon != horizon for window in windows
            ):
                raise ValueError("research horizon과 execution window가 다릅니다.")
        self.trial_horizon_by_id = {trial.trial_id: trial.alpha.horizon for trial in self.trials}
        self.observation_end_ts_ms = max(
            window.observation_end_ts_ms
            for windows in self.execution_windows_by_horizon.values()
            for window in windows
        )
        strategy_ids = tuple(trial.trial_id for trial in self.trials)
        self.portfolio = PaperPortfolioEngine(
            run_id=run_id,
            strategy_ids=strategy_ids,
            shadow_ledger=ShadowLedger(strategy_ids),
        )
        self._seed_account_carry(account_carry)
        self.plan_builder = ResearchCandidatePlanBuilder()
        self.candles = CandleBuilder(maximum_bars=640, intervals=SCREENING_INTERVALS)
        # 두 독립 재계산 경로는 동일한 과거 범위를 가져야 한다. 서로 다른
        # maxlen은 F20의 과거 VWAP을 잘라 정상적인 과거 의존성을 lookahead
        # 불일치로 잘못 판정했다.
        self.alpha_features = AlphaFeatureBuilder(maximum_bars=RESEARCH_FEATURE_HISTORY_BARS)
        self.recursive_features = AlphaFeatureBuilder(
            maximum_bars=RESEARCH_FEATURE_HISTORY_BARS
        )
        self.regime_classifier = RegimeClassifier()
        self.feature_engines: dict[str, FeatureEngine] = {}
        self.latest_books: dict[str, BookSnapshot] = {}
        self.latest_features: dict[str, FeatureSnapshot] = {}
        self.latest_regimes: dict[str, Regime] = {}
        self.last_feature_snapshot_ms: dict[str, int] = {}
        self.pending_cross_sectional: set[tuple[int, str]] = set()
        self.signal_volatility_regime: dict[str, str] = {}
        self.trials_by_family: dict[str, tuple[ResearchTrialSpec, ...]] = defaultdict(tuple)
        grouped: dict[str, list[ResearchTrialSpec]] = defaultdict(list)
        for trial in self.trials:
            grouped[trial.alpha.family_id].append(trial)
        self.trials_by_family = {
            family_id: tuple(sorted(values, key=lambda trial: trial.trial_number))
            for family_id, values in grouped.items()
        }
        self.family_horizon_by_id = {
            family_id: values[0].alpha.horizon
            for family_id, values in self.trials_by_family.items()
        }
        if any(
            trial.alpha.horizon != self.family_horizon_by_id[family_id]
            for family_id, values in self.trials_by_family.items()
            for trial in values
        ):
            raise ValueError("같은 alpha family에 서로 다른 horizon이 섞였습니다.")
        families_by_interval: dict[int, list[str]] = defaultdict(list)
        for family_id in self.trials_by_family:
            families_by_interval[ALPHA_EVALUATION_INTERVAL_SECONDS[family_id]].append(family_id)
        self.families_by_interval = {
            interval: tuple(sorted(values)) for interval, values in families_by_interval.items()
        }
        self.diagnostics = RunDiagnostics(run_id=run_id, split=split)
        self._seed_warmup(warmup_candles)
        self.incomplete_trial_ids: set[str] = set()

    def _seed_warmup(self, warmup_candles: Sequence[Candle]) -> None:
        symbols: set[str] = set()
        latest_by_key: dict[tuple[str, int], int] = {}
        for candle in warmup_candles:
            key = (candle.symbol, candle.interval_seconds)
            previous_open = latest_by_key.get(key)
            if previous_open is not None and candle.open_ts_ms <= previous_open:
                raise ValueError("100후보 워밍업 완료봉이 중복되거나 역행합니다.")
            latest_by_key[key] = candle.open_ts_ms
            if not self.alpha_features.ingest_completed(candle):
                raise ValueError("100후보 primary 워밍업 완료봉이 거절됐습니다.")
            if not self.recursive_features.ingest_completed(candle):
                raise ValueError("100후보 recursive 워밍업 완료봉이 거절됐습니다.")
            symbols.add(candle.symbol)
        self.diagnostics.warmup_candle_count = len(warmup_candles)
        self.diagnostics.warmup_symbol_count = len(symbols)

    def _seed_account_carry(
        self,
        account_carry: Mapping[tuple[str, str], ResearchAccountCarry],
    ) -> None:
        for account in self.portfolio.shadows.values():
            trial_id, profile = account.account_id.rsplit(":", 1)
            carry = account_carry.get((trial_id, profile), ResearchAccountCarry())
            state = account.risk_state
            state.current_equity = carry.current_equity_usdt
            state.peak_equity = carry.peak_equity_usdt
            state.global_consecutive_losses = carry.global_consecutive_losses
            shadow = self.portfolio.shadow_ledger.account(trial_id, CostProfile(profile))
            shadow.current_equity_usdt = carry.current_equity_usdt
            shadow.peak_equity_usdt = carry.peak_equity_usdt

    def ending_account_carry(self) -> dict[tuple[str, str], ResearchAccountCarry]:
        carry: dict[tuple[str, str], ResearchAccountCarry] = {}
        for account in self.portfolio.shadows.values():
            trial_id, profile = account.account_id.rsplit(":", 1)
            shadow = self.portfolio.shadow_ledger.account(trial_id, account.profile)
            if shadow.current_equity_usdt != account.risk_state.current_equity:
                raise ValueError("research 실행계좌와 shadow 계좌 자산이 다릅니다.")
            carry[(trial_id, profile)] = ResearchAccountCarry(
                current_equity_usdt=account.risk_state.current_equity,
                peak_equity_usdt=max(
                    account.risk_state.peak_equity,
                    shadow.peak_equity_usdt,
                ),
                global_consecutive_losses=account.risk_state.global_consecutive_losses,
            )
        return carry

    def _entries_allowed_for_family(self, family_id: str, ts_ms: int) -> bool:
        horizon = self.family_horizon_by_id[family_id]
        return any(
            window.permits_entry(ts_ms)
            for window in self.execution_windows_by_horizon.get(horizon, ())
        )

    def _trade_inside_execution_window(self, trade: ScreeningTrade) -> bool:
        horizon = self.trial_horizon_by_id.get(trade.trial_id)
        if horizon is None:
            return False
        return any(
            window.contains_trade(trade)
            for window in self.execution_windows_by_horizon.get(horizon, ())
        )

    def execute(
        self,
        *,
        maximum_events: int | None = None,
        cooperative_yield: Callable[[], None] | None = None,
        checkpoint_events: int = 256,
    ) -> tuple[ScreeningTrade, ...]:
        if maximum_events is not None and maximum_events <= 0:
            raise ValueError("maximum_events는 양수여야 합니다.")
        if checkpoint_events <= 0:
            raise ValueError("연구 CPU checkpoint 이벤트 수는 양수여야 합니다.")
        for index, payload in enumerate(
            _event_rows(
                self.archive_dir,
                files=getattr(self, "archive_files", None),
                maximum_events=maximum_events,
                symbols=getattr(self, "research_symbols", None),
            ),
            start=1,
        ):
            if int(str(payload.get("venue_ts_ms", 0))) > self.observation_end_ts_ms:
                # 정렬축은 실제 수신순이다. 거래소 시각이 느린 이벤트가 뒤에
                # 도착할 수 있으므로 경계 초과 한 건으로 전체 순회를 끝내지 않는다.
                if cooperative_yield is not None and index % checkpoint_events == 0:
                    cooperative_yield()
                continue
            self._process(payload)
            if cooperative_yield is not None and index % checkpoint_events == 0:
                cooperative_yield()
        if cooperative_yield is not None:
            cooperative_yield()
        self._drain_audit()
        primary_builder = getattr(self, "alpha_features", None)
        recursive_builder = getattr(self, "recursive_features", None)
        if primary_builder is not None and recursive_builder is not None:
            primary_diagnostics = primary_builder.diagnostics
            recursive_diagnostics = recursive_builder.diagnostics
            self.diagnostics.alpha_snapshot_cache_hit_count = (
                primary_diagnostics.snapshot_cache_hits
                + recursive_diagnostics.snapshot_cache_hits
            )
            self.diagnostics.alpha_snapshot_cache_miss_count = (
                primary_diagnostics.snapshot_cache_misses
                + recursive_diagnostics.snapshot_cache_misses
            )
        self.diagnostics.paper_execution_book_count = getattr(
            self.portfolio,
            "book_active_scan_count",
            0,
        )
        self.diagnostics.paper_execution_book_skipped_count = (
            getattr(self.portfolio, "book_empty_fast_path_count", 0)
        )
        self.diagnostics.position_health_evaluation_count = (
            getattr(self.portfolio, "health_active_scan_count", 0)
        )
        self.diagnostics.censored_position_count = sum(
            len(account.positions) for account in self.portfolio.shadows.values()
        )
        self.diagnostics.censored_pending_entry_count = sum(
            len(account.pending_entries) for account in self.portfolio.shadows.values()
        )
        self.incomplete_trial_ids = {
            account.account_id.rsplit(":", 1)[0]
            for account in self.portfolio.shadows.values()
            if account.positions or account.pending_entries
        }
        trades: list[ScreeningTrade] = []
        for account in self.portfolio.shadows.values():
            trial_id, profile = account.account_id.rsplit(":", 1)
            for trade in account.completed_trades:
                entry_notional = trade.entry_price * trade.quantity
                if entry_notional <= 0:
                    raise ValueError("screening 거래 entry notional이 양수가 아닙니다.")
                screening_trade = ScreeningTrade(
                    trade_id=trade.trade_id,
                    trial_id=trial_id,
                    profile=profile,
                    split=self.split,
                    run_id=self.run_id,
                    symbol=trade.symbol,
                    regime=trade.regime,
                    side=trade.side.value,
                    entry_ts_ms=trade.opened_ts_ms,
                    exit_ts_ms=trade.closed_ts_ms,
                    gross_pnl_usdt=trade.gross_pnl_usdt,
                    fee_usdt=trade.fees_usdt,
                    slippage_usdt=trade.slippage_usdt,
                    net_pnl_usdt=trade.net_pnl_usdt,
                    net_return_bps=float(trade.net_pnl_usdt / entry_notional * Decimal(10_000)),
                    mfe_r=trade.mfe_r,
                    mae_r=trade.mae_r,
                    giveback_usdt=trade.giveback_usdt,
                    signal_event_id=trade.signal_event_id,
                    exit_reason=trade.exit_reason.value,
                    tp1_hit_ts_ms=trade.tp1_hit_ts_ms,
                    trailing_activation_ts_ms=trade.trailing_activation_ts_ms,
                    runner_started_ts_ms=trade.runner_started_ts_ms,
                    peak_unrealized_usdt=trade.peak_unrealized_usdt,
                    runner_net_pnl_usdt=trade.runner_net_pnl_usdt,
                    trail_trigger_slippage_usdt=trade.trail_trigger_slippage_usdt,
                    trailing_state_checksum=trade.trailing_state_checksum,
                    venue=trade.venue.value,
                    volatility_regime=self.signal_volatility_regime.get(
                        trade.signal_event_id or "",
                        "UNKNOWN",
                    ),
                )
                if not self._trade_inside_execution_window(screening_trade):
                    raise ValueError(
                        "research 거래가 purge·embargo·최대보유 execution window를 "
                        f"벗어났습니다: {screening_trade.trade_id}"
                    )
                trades.append(screening_trade)
        return tuple(sorted(trades, key=lambda row: (row.entry_ts_ms, row.trade_id)))

    def _process(self, payload: Mapping[str, Any]) -> None:
        self.diagnostics.event_count += 1
        ts_ms = int(payload.get("venue_ts_ms", 0))
        self.diagnostics.first_ts_ms = (
            ts_ms if self.diagnostics.first_ts_ms is None else self.diagnostics.first_ts_ms
        )
        self.diagnostics.last_ts_ms = ts_ms
        event_type = str(payload.get("event_type"))
        symbol = str(payload.get("symbol"))
        if self.research_symbols is not None and symbol not in self.research_symbols:
            self.diagnostics.outside_research_universe_event_count += 1
            return
        if event_type in {"DEPTH_UPDATE", "ORDERBOOK", "REST_BOOK_TICKER_BOOTSTRAP"}:
            self._process_book(payload, symbol)
        elif event_type == "TRADE":
            self._process_trade(payload, symbol)

    def _process_book(self, payload: Mapping[str, Any], symbol: str) -> None:
        try:
            frame = _book_frame(dict(payload))
        except (ArithmeticError, FeatureInputError, KeyError, TypeError, ValueError):
            self.diagnostics.rejected_market_event_count += 1
            return
        self.diagnostics.book_event_count += 1
        if not frame.sequence_valid or frame.stale or frame.lag_ms > 500:
            self.diagnostics.rejected_market_event_count += 1
            return
        book = _book_snapshot(frame)
        self.portfolio.on_book(book)
        engine = self.feature_engines.setdefault(symbol, FeatureEngine())
        try:
            engine.ingest_book(frame)
            last_snapshot_ms = self.last_feature_snapshot_ms.get(symbol)
            if (
                last_snapshot_ms is not None
                and frame.ts_ms - last_snapshot_ms < RESEARCH_FEATURE_SNAPSHOT_INTERVAL_MS
            ):
                self.diagnostics.feature_snapshot_throttled_count += 1
                self._drain_audit()
                return
            self.last_feature_snapshot_ms[symbol] = frame.ts_ms
            self.diagnostics.feature_snapshot_count += 1
            market_snapshot = engine.snapshot()
        except (FeatureInputError, KeyError, ValueError, ZeroDivisionError):
            self.diagnostics.rejected_market_event_count += 1
            self._drain_audit()
            return
        regime = self.regime_classifier.classify(market_snapshot)
        self.latest_books[symbol] = book
        self.latest_features[symbol] = market_snapshot
        self.latest_regimes[symbol] = regime
        self.alpha_features.ingest_microstructure(market_snapshot)
        self.recursive_features.ingest_microstructure(market_snapshot)
        self.portfolio.evaluate_health(market_snapshot, regime, now_ms=book.ts_ms)
        plans = self._flush_cross_sectional(symbol, decision_ts_ms=book.ts_ms)
        if plans:
            self.portfolio.offer(
                tuple(plans),
                entries_paused=False,
            )
        self._drain_audit()

    def _process_trade(self, payload: Mapping[str, Any], symbol: str) -> None:
        self.diagnostics.trade_event_count += 1
        quality = payload.get("quality")
        if (
            not isinstance(quality, Mapping)
            or quality.get("sequence_valid") is not True
            or quality.get("is_stale") is not False
        ):
            self.diagnostics.rejected_market_event_count += 1
            return
        try:
            trade = _trade_tick(dict(payload))
            engine = self.feature_engines.get(symbol)
            if engine is not None:
                engine.ingest_trade(trade)
            completed = self.candles.add(trade)
        except (ArithmeticError, FeatureInputError, KeyError, TypeError, ValueError):
            self.diagnostics.rejected_market_event_count += 1
            return
        plans: list[CandidatePlan] = []
        for candle in completed:
            self.alpha_features.ingest_completed(candle)
            self.recursive_features.ingest_completed(candle)
            close_ts_ms = candle.open_ts_ms + candle.interval_seconds * 1_000
            for family_id in self.families_by_interval.get(candle.interval_seconds, ()):
                if not self._entries_allowed_for_family(family_id, trade.trade_ts_ms):
                    continue
                if family_id == "F16":
                    self.pending_cross_sectional.add((close_ts_ms, symbol))
                    continue
                plans.extend(
                    self._evaluate_family(
                        family_id,
                        symbol,
                        decision_ts_ms=trade.trade_ts_ms,
                        signal_event_id=f"{trade.event_id}:{family_id}:{close_ts_ms}",
                    )
                )
        if plans:
            self.portfolio.offer(
                tuple(plans),
                entries_paused=False,
            )
        self._drain_audit()

    def _flush_cross_sectional(self, symbol: str, *, decision_ts_ms: int) -> list[CandidatePlan]:
        plans: list[CandidatePlan] = []
        for close_ts_ms, candidate_symbol in sorted(self.pending_cross_sectional):
            if (
                candidate_symbol != symbol
                or decision_ts_ms < close_ts_ms + CROSS_SECTIONAL_GRACE_MS
            ):
                continue
            self.pending_cross_sectional.remove((close_ts_ms, candidate_symbol))
            if not self._entries_allowed_for_family("F16", decision_ts_ms):
                continue
            if decision_ts_ms >= close_ts_ms + ALPHA_EVALUATION_INTERVAL_SECONDS["F16"] * 1_000:
                self.diagnostics.missing_feature_count += 1
                continue
            plans.extend(
                self._evaluate_family(
                    "F16",
                    symbol,
                    decision_ts_ms=decision_ts_ms,
                    signal_event_id=f"F16:{close_ts_ms}:{symbol}:{decision_ts_ms}",
                )
            )
        return plans

    def _evaluate_family(
        self,
        family_id: str,
        symbol: str,
        *,
        decision_ts_ms: int,
        signal_event_id: str,
    ) -> list[CandidatePlan]:
        trials = self.trials_by_family[family_id]
        snapshot = self.alpha_features.snapshot(
            symbol,
            family_id,
            decision_ts_ms=decision_ts_ms,
        )
        if snapshot is None:
            self.diagnostics.missing_feature_count += 1
            return []
        market_snapshot = self.latest_features.get(symbol)
        book = self.latest_books.get(symbol)
        regime = self.latest_regimes.get(symbol)
        for trial in trials:
            for profile in CostProfile:
                self.account_counters[
                    _account_key(trial.trial_id, profile)
                ].evaluated_event_count += 1
        self.diagnostics.alpha_evaluation_count += len(trials)
        parameters = dict(trials[0].alpha.parameters)
        first = evaluate_alpha(family_id, snapshot, parameters)
        second = evaluate_alpha(family_id, snapshot, parameters)
        if first != second:
            for trial in trials:
                self.trial_integrity[trial.trial_id].deterministic_signal_pass = False
                self.trial_integrity[trial.trial_id].evidence_codes.add(
                    "NON_DETERMINISTIC_ALPHA_SIGNAL"
                )
            return []
        recursive_snapshot = self.recursive_features.snapshot(
            symbol,
            family_id,
            decision_ts_ms=decision_ts_ms,
        )
        if (
            recursive_snapshot is not None
            and recursive_snapshot.completed_candle_close_ts_ms
            == snapshot.completed_candle_close_ts_ms
        ):
            recursive_signal = evaluate_alpha(family_id, recursive_snapshot, parameters)
            for trial in trials:
                state = self.trial_integrity[trial.trial_id]
                state.recursive_comparison_count += 1
                if recursive_signal != first:
                    state.recursive_mismatch_count += 1
                    state.recursive_dependency_pass = False
                    state.evidence_codes.add("RECURSIVE_WARMUP_SIGNAL_MISMATCH")
                elif state.recursive_mismatch_count == 0:
                    state.recursive_dependency_pass = True
        if first is None:
            return []
        volatility_regime = point_in_time_volatility_regime(
            fast=snapshot.realized_volatility_fast,
            slow=snapshot.realized_volatility_slow,
        )
        previous_volatility_regime = self.signal_volatility_regime.setdefault(
            signal_event_id,
            volatility_regime,
        )
        if previous_volatility_regime != volatility_regime:
            raise ValueError("같은 signal event의 시점 바인딩 변동성 구간이 달라졌습니다.")
        self.diagnostics.alpha_signal_count += len(trials)
        plans: list[CandidatePlan] = []
        for trial in trials:
            for profile in CostProfile:
                counters = self.account_counters[_account_key(trial.trial_id, profile)]
                counters.signal_count += 1
                counters.attempted_entry_count += 1
            if market_snapshot is None or book is None or regime is None:
                self._reject_both(trial, "DECISION_MARKET_STATE_MISSING")
                continue
            metadata = self.instruments.get(symbol)
            if metadata is None:
                self._reject_both(trial, "INSTRUMENT_METADATA_MISSING")
                self.trial_integrity[trial.trial_id].no_lookahead_pass = False
                continue
            base_account = self.portfolio.shadows[f"{trial.trial_id}:BASE"]
            result = self.plan_builder.build(
                trial=trial,
                signal=first,
                alpha_snapshot=snapshot,
                market_snapshot=market_snapshot,
                book=book,
                metadata=metadata,
                regime=regime,
                run_id=self.run_id,
                signal_event_id=signal_event_id,
                risk_state=base_account.risk_state,
            )
            repeated = self.plan_builder.build(
                trial=trial,
                signal=first,
                alpha_snapshot=snapshot,
                market_snapshot=market_snapshot,
                book=book,
                metadata=metadata,
                regime=regime,
                run_id=self.run_id,
                signal_event_id=signal_event_id,
                risk_state=base_account.risk_state,
            )
            if repeated != result:
                self.trial_integrity[trial.trial_id].deterministic_signal_pass = False
                self.trial_integrity[trial.trial_id].evidence_codes.add(
                    "NON_DETERMINISTIC_CANDIDATE_PLAN"
                )
                self._reject_both(trial, "NON_DETERMINISTIC_CANDIDATE_PLAN")
                continue
            if not result.instrument_metadata_promotion_eligible:
                self.trial_integrity[trial.trial_id].no_lookahead_pass = False
            self.trial_integrity[trial.trial_id].evidence_codes.update(result.evidence_codes)
            if result.plan is None:
                self._reject_both(trial, *(result.rejection_codes or ("PLAN_BUILD_REJECTED",)))
                continue
            self.diagnostics.plan_count += 1
            plans.append(result.plan)
        return plans

    def _reject_both(self, trial: ResearchTrialSpec, *reason_codes: str) -> None:
        for profile in CostProfile:
            counters = self.account_counters[_account_key(trial.trial_id, profile)]
            counters.rejected_entry_count += 1
            counters.rejection_counts.update(reason_codes)

    def _drain_audit(self) -> None:
        for row in self.portfolio.audit_events:
            event = str(row.get("event", "UNKNOWN"))
            self.diagnostics.audit_counts[event] += 1
            if event not in ENTRY_REJECTION_EVENTS:
                continue
            account_id = str(row.get("account_id", ""))
            if not account_id or account_id == self.portfolio.MAIN_ACCOUNT_ID:
                continue
            trial_id, profile = account_id.rsplit(":", 1)
            key = (trial_id, profile)
            if key not in self.account_counters:
                raise ValueError("PAPER audit에 등록되지 않은 trial 계좌가 있습니다.")
            counters = self.account_counters[key]
            counters.rejected_entry_count += 1
            reasons = row.get("reason_codes")
            if isinstance(reasons, list) and reasons:
                counters.rejection_counts.update(str(value) for value in reasons)
            else:
                counters.rejection_counts[event] += 1
        self.portfolio.audit_events.clear()


def _validation_fold_returns(
    results: Sequence[TrialScreeningResult],
    folds_by_horizon: Mapping[str, Sequence[ValidationFold]],
    trial_horizon_by_id: Mapping[str, str],
) -> tuple[
    dict[str, tuple[float, ...]],
    dict[str, tuple[int, ...]],
    int,
]:
    values: dict[str, tuple[float, ...]] = {}
    trade_counts: dict[str, tuple[int, ...]] = {}
    excluded = 0
    for result in results:
        if result.status is not ScreeningStatus.EXECUTED:
            continue
        horizon = trial_horizon_by_id.get(result.trial_id)
        folds = folds_by_horizon.get(horizon or "", ())
        if len(folds) != 4:
            raise ValueError("실행 trial에는 정확히 네 horizon별 Validation fold가 필요합니다.")
        stress = next(account for account in result.accounts if account.profile == "STRESS")
        fold_values: list[float] = []
        fold_counts: list[int] = []
        for fold in folds:
            contained = [
                trade.net_return_bps
                for trade in stress.trades
                if trade.split == "VALIDATION"
                and trade.entry_ts_ms >= fold.start_ts_ms
                and trade.exit_ts_ms <= fold.end_ts_ms
            ]
            fold_values.append(fmean(contained) if contained else 0.0)
            fold_counts.append(len(contained))
        excluded += sum(
            trade.split == "VALIDATION"
            and not any(
                trade.entry_ts_ms >= fold.start_ts_ms and trade.exit_ts_ms <= fold.end_ts_ms
                for fold in folds
            )
            for trade in stress.trades
        )
        values[result.trial_id] = tuple(fold_values)
        trade_counts[result.trial_id] = tuple(fold_counts)
    return values, trade_counts, excluded


def _encode(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Counter):
        return dict(sorted(value.items()))
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_encode(item) for item in value]
    return value


def _run_diagnostics_payload(diagnostics: RunDiagnostics) -> dict[str, object]:
    payload = asdict(diagnostics)
    # dataclasses.asdict()는 Counter를 Counter 생성자로 재구성하면서
    # (key, value) tuple을 새 key로 해석하므로 JSON 전에 평범한 dict로 되돌린다.
    payload["audit_counts"] = dict(sorted(diagnostics.audit_counts.items()))
    return payload


def _atomic_write(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"기존 연구 증거를 덮어쓰지 않습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _validate_output_contract(
    outputs: Sequence[Path],
    *,
    manifest_kind: str,
) -> None:
    """한 번의 연구결과가 서로 또는 기존 불변 증거를 덮지 못하게 한다."""

    resolved = tuple(path.resolve() for path in outputs)
    if len(resolved) != len(set(resolved)):
        raise ValueError("연구 결과 경로는 서로 달라야 합니다.")
    if manifest_kind == "COST_COVERED_EXIT_VARIANT_BATCH":
        frozen_defaults = {path.resolve() for path in DEFAULT_RESEARCH_OUTPUTS}
        if frozen_defaults.intersection(resolved):
            raise ValueError("E06 변형은 동결 100후보와 분리된 결과 경로가 필요합니다.")
    existing = [path for path in outputs if path.exists()]
    if existing:
        raise FileExistsError(f"기존 연구 증거를 덮어쓰지 않습니다: {existing[0]}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("data/market-parquet-v6/venue=BINANCE_USDM"),
    )
    parser.add_argument(
        "--trial-manifest",
        type=Path,
        default=Path("evidence/STRATEGY_100_TRIAL_MANIFEST.json"),
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=Path("evidence/STRATEGY_100_DATASET_MANIFEST.json"),
    )
    parser.add_argument(
        "--instrument-manifest",
        type=Path,
        default=Path("evidence/STRATEGY_100_INSTRUMENTS.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RESEARCH_OUTPUTS[0],
    )
    parser.add_argument(
        "--trades-output",
        type=Path,
        default=DEFAULT_RESEARCH_OUTPUTS[1],
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=DEFAULT_RESEARCH_OUTPUTS[2],
    )
    parser.add_argument(
        "--trailing-ablation-output",
        type=Path,
        default=DEFAULT_RESEARCH_OUTPUTS[3],
    )
    parser.add_argument(
        "--walk-forward-output",
        type=Path,
        default=DEFAULT_RESEARCH_OUTPUTS[4],
    )
    parser.add_argument(
        "--multiple-testing-output",
        type=Path,
        default=DEFAULT_RESEARCH_OUTPUTS[5],
    )
    parser.add_argument("--target-cpu-ratio", type=float, default=0.15)
    parser.add_argument("--cpu-checkpoint-events", type=int, default=256)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not 0 < args.target_cpu_ratio <= 1 or args.cpu_checkpoint_events <= 0:
        raise ValueError("연구 CPU 비율과 checkpoint 이벤트 수가 잘못됐습니다.")
    args.archive = args.archive.resolve(strict=True)
    trial_manifest, dataset, instrument_manifest, source_hashes = _load_inputs(
        args.trial_manifest,
        args.dataset_manifest,
        args.instrument_manifest,
    )
    _validate_output_contract(
        (
            args.output,
            args.trades_output,
            args.audit_output,
            args.trailing_ablation_output,
            args.walk_forward_output,
            args.multiple_testing_output,
        ),
        manifest_kind=str(source_hashes["trial_manifest_kind"]),
    )
    instruments = load_research_instruments(instrument_manifest)
    trials = _trials_for_manifest(trial_manifest)
    executable_trials = tuple(trial for trial in trials if trial.screening_eligible)
    account_counters = {
        _account_key(trial.trial_id, profile): AccountCounters()
        for trial in executable_trials
        for profile in CostProfile
    }
    integrity = {trial.trial_id: TrialIntegrity() for trial in executable_trials}
    run_rows = dataset.get("runs")
    if not isinstance(run_rows, list):
        raise ValueError("dataset manifest에 Run이 없습니다.")
    selected_runs = sorted(
        (
            row
            for row in run_rows
            if isinstance(row, Mapping) and row.get("role") in {"TRAIN", "VALIDATION"}
        ),
        key=lambda row: (int(str(row["start_ts_ms"])), str(row["run_id"])),
    )
    if any(not isinstance(row, Mapping) or row.get("role") == "FINAL_OOS" for row in selected_runs):
        raise ValueError("Stage 1 실행 Run에 Final OOS가 섞였습니다.")
    v2_cut_manifest: dict[str, Any] | None = None
    v2_warmup: FrozenStrategy100Warmup | None = None
    if int(str(dataset.get("schema_version", 0))) >= 3:
        cut_reference = dataset.get("live_public_cut")
        warmup_reference = dataset.get("warmup_manifest")
        if not isinstance(cut_reference, Mapping) or not isinstance(
            warmup_reference, Mapping
        ):
            raise ValueError("V2 dataset의 동결 입력 연결이 없습니다.")
        v2_cut_manifest, _, _ = load_bound_manifest(
            cut_reference,
            binding_path=args.dataset_manifest,
            expected_status="FROZEN_LIVE_PUBLIC_CUT",
            name="LIVE_PUBLIC cut",
        )
        _, warmup_path, _ = load_bound_manifest(
            warmup_reference,
            binding_path=args.dataset_manifest,
            expected_status="FROZEN_PUBLIC_KLINE_WARMUP",
            name="public kline warmup",
        )
        v2_warmup = FrozenStrategy100Warmup.load(warmup_path)
    validation_start, final_oos_start = _screening_boundaries(dataset)
    raw_folds = _validation_folds(dataset)
    folds_by_horizon = {
        horizon: _effective_validation_folds(
            raw_folds,
            maximum_holding_ms=maximum_holding_ms,
            purge_embargo_ms=maximum_holding_ms,
        )
        for horizon, maximum_holding_ms in HORIZON_MAXIMUM_HOLD_MS.items()
    }
    trial_horizon_by_id = {trial.trial_id: trial.alpha.horizon for trial in executable_trials}
    account_carry = {
        _account_key(trial.trial_id, profile): ResearchAccountCarry()
        for trial in executable_trials
        for profile in CostProfile
    }
    incomplete_trials: dict[str, set[str]] = defaultdict(set)
    all_trades: list[ScreeningTrade] = []
    run_diagnostics: list[dict[str, object]] = []
    cpu_budget = ResearchCpuBudget(target_cpu_ratio=args.target_cpu_ratio)
    all_execution_windows_by_horizon: dict[str, list[ResearchExecutionWindow]] = {
        horizon: [] for horizon in HORIZON_MAXIMUM_HOLD_MS
    }
    horizon_role_coverage: dict[str, set[str]] = {
        horizon: set() for horizon in HORIZON_MAXIMUM_HOLD_MS
    }
    for row in selected_runs:
        run_id = str(row["run_id"])
        execution_windows_by_horizon = {
            horizon: _run_execution_windows(
                row,
                validation_folds=folds_by_horizon[horizon],
                validation_start_ms=validation_start,
                horizon=horizon,
                maximum_holding_ms=maximum_holding_ms,
                purge_embargo_ms=maximum_holding_ms,
            )
            for horizon, maximum_holding_ms in HORIZON_MAXIMUM_HOLD_MS.items()
        }
        for horizon, windows in execution_windows_by_horizon.items():
            if windows:
                all_execution_windows_by_horizon[horizon].extend(windows)
                horizon_role_coverage[horizon].add(str(row["role"]))
        if not any(execution_windows_by_horizon.values()):
            run_diagnostics.append(
                {
                    "run_id": run_id,
                    "split": str(row["role"]),
                    "status": "SKIPPED_NO_HORIZON_EXECUTION_WINDOW",
                    "execution_windows_by_horizon": {
                        horizon: [] for horizon in HORIZON_MAXIMUM_HOLD_MS
                    },
                }
            )
            continue
        source_run_id = str(row.get("source_run_id", run_id))
        archive_files = (
            archive_files_for_logical_run(
                live_public_cut=v2_cut_manifest,
                logical_run=row,
                archive_root_override=args.archive.parent,
            )
            if v2_cut_manifest is not None
            else None
        )
        warmup_candles = (
            v2_warmup.candles_before(
                int(str(row["start_ts_ms"])),
                maximum_bars=RESEARCH_FEATURE_HISTORY_BARS,
            )
            if v2_warmup is not None
            else ()
        )
        executor = Strategy100RunExecutor(
            run_id=run_id,
            split=str(row["role"]),
            archive_dir=args.archive / f"run={source_run_id}",
            trials=trials,
            instruments=instruments,
            account_counters=account_counters,
            trial_integrity=integrity,
            execution_windows_by_horizon=execution_windows_by_horizon,
            account_carry=account_carry,
            archive_files=archive_files,
            warmup_candles=warmup_candles,
            research_symbols=(v2_warmup.symbols if v2_warmup is not None else None),
        )
        all_trades.extend(
            executor.execute(
                cooperative_yield=cpu_budget.checkpoint,
                checkpoint_events=args.cpu_checkpoint_events,
            )
        )
        account_carry.update(executor.ending_account_carry())
        for trial_id in executor.incomplete_trial_ids:
            incomplete_trials[trial_id].add(run_id)
        diagnostic = _run_diagnostics_payload(executor.diagnostics)
        diagnostic["execution_windows_by_horizon"] = {
            horizon: [asdict(window) for window in windows]
            for horizon, windows in execution_windows_by_horizon.items()
        }
        diagnostic["incomplete_trial_ids"] = sorted(executor.incomplete_trial_ids)
        run_diagnostics.append(diagnostic)

    retained_trades = tuple(
        trade
        for trade in all_trades
        if _trade_inside_purged_split(
            trade,
            execution_windows_by_horizon=all_execution_windows_by_horizon,
            trial_horizon_by_id=trial_horizon_by_id,
        )
    )
    purged_trade_count = len(all_trades) - len(retained_trades)
    by_account: dict[tuple[str, str], list[ScreeningTrade]] = defaultdict(list)
    for trade in retained_trades:
        by_account[(trade.trial_id, trade.profile)].append(trade)

    horizon_execution_ready = {
        horizon: (
            len(folds_by_horizon[horizon]) == 4
            and horizon_role_coverage[horizon] >= {"TRAIN", "VALIDATION"}
        )
        for horizon in HORIZON_MAXIMUM_HOLD_MS
    }

    results: list[TrialScreeningResult] = []
    for trial in trials:
        if not trial.screening_eligible:
            results.append(
                TrialScreeningResult(
                    trial_id=trial.trial_id,
                    status=ScreeningStatus.BLOCKED,
                    blocker_codes=trial.alpha.blocker_codes,
                    failure_code=None,
                    deterministic_signal_pass=False,
                    no_lookahead_pass=False,
                    recursive_dependency_pass=False,
                    accounts=(),
                )
            )
            continue
        accounts: list[ScreeningAccountResult] = []
        for profile in CostProfile:
            key = _account_key(trial.trial_id, profile)
            counters = account_counters[key]
            trades = tuple(
                sorted(by_account[key], key=lambda value: (value.entry_ts_ms, value.trade_id))
            )
            accounts.append(
                ScreeningAccountResult(
                    account_id=f"{trial.trial_id}:{profile.value}",
                    trial_id=trial.trial_id,
                    profile=profile.value,
                    starting_equity_usdt=Decimal("1000"),
                    final_equity_usdt=Decimal("1000")
                    + sum((trade.net_pnl_usdt for trade in trades), start=Decimal(0)),
                    evaluated_event_count=counters.evaluated_event_count,
                    signal_count=counters.signal_count,
                    attempted_entry_count=counters.attempted_entry_count,
                    rejected_entry_count=counters.rejected_entry_count,
                    trades=trades,
                    rejection_counts=tuple(sorted(counters.rejection_counts.items())),
                )
            )
        state = integrity[trial.trial_id]
        expected_carry = {
            profile.value: account_carry[_account_key(trial.trial_id, profile)]
            for profile in CostProfile
        }
        for account in accounts:
            if expected_carry[account.profile].current_equity_usdt != account.final_equity_usdt:
                raise ValueError("research 계좌 carry와 보존 거래 순손익이 다릅니다.")
        failure_code = None
        if not horizon_execution_ready[trial.alpha.horizon]:
            failure_code = f"DATASET_WINDOW_INSUFFICIENT_{trial.alpha.horizon}"
        elif trial.trial_id in incomplete_trials:
            failure_code = "CENSORED_POSITION_OR_PENDING_ENTRY"
        results.append(
            TrialScreeningResult(
                trial_id=trial.trial_id,
                status=(
                    ScreeningStatus.FAILED if failure_code is not None else ScreeningStatus.EXECUTED
                ),
                blocker_codes=(),
                failure_code=failure_code,
                deterministic_signal_pass=state.deterministic_signal_pass,
                no_lookahead_pass=state.no_lookahead_pass,
                recursive_dependency_pass=state.recursive_dependency_pass,
                accounts=tuple(accounts),
            )
        )

    fold_returns, fold_trade_counts, fold_crossing_excluded = _validation_fold_returns(
        results,
        folds_by_horizon,
        trial_horizon_by_id,
    )
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    report = build_screening_report(
        results,
        trial_manifest_sha256=str(source_hashes["trial_manifest_sha256"]),
        dataset_manifest_sha256=str(source_hashes["dataset_manifest_sha256"]),
        validation_fold_returns=fold_returns,
        generated_ts_utc=now,
        trials=trials,
        selection_limit=int(str(trial_manifest.get("selection_limit", 25))),
    )
    report["trial_batch"] = {
        "manifest_kind": str(source_hashes["trial_manifest_kind"]),
        "batch_id": trial_manifest.get("batch_id", "STRATEGY_100_FROZEN_BATCH"),
        "parent_trial_manifest_sha256": source_hashes["parent_trial_manifest_sha256"],
    }
    trade_lines = "".join(
        json.dumps(_encode(asdict(trade)), ensure_ascii=False, sort_keys=True) + "\n"
        for trade in sorted(retained_trades, key=lambda row: (row.entry_ts_ms, row.trade_id))
    )
    audit: dict[str, object] = {
        "schema_version": 1,
        "status": "EXECUTED_DIAGNOSTIC_PROMOTION_BLOCKED",
        "generated_ts_utc": now,
        "source_hashes": source_hashes,
        "trial_batch": report["trial_batch"],
        "resource_contract": {
            "target_cpu_ratio": args.target_cpu_ratio,
            "cpu_checkpoint_events": args.cpu_checkpoint_events,
            "single_thread_duckdb": True,
        },
        "run_diagnostics": run_diagnostics,
        "processed_run_count": len(selected_runs),
        "processed_roles": ["TRAIN", "VALIDATION"],
        "final_oos_processed": False,
        "raw_closed_trade_count": len(all_trades),
        "retained_trade_count": len(retained_trades),
        "purge_embargo_excluded_trade_count": purged_trade_count,
        "raw_validation_folds": [asdict(fold) for fold in raw_folds],
        "effective_validation_folds_by_horizon": {
            horizon: [asdict(fold) for fold in folds] for horizon, folds in folds_by_horizon.items()
        },
        "purge_embargo_ms_by_horizon": dict(HORIZON_MAXIMUM_HOLD_MS),
        "horizon_role_coverage": {
            horizon: sorted(roles) for horizon, roles in horizon_role_coverage.items()
        },
        "horizon_execution_ready": horizon_execution_ready,
        "final_oos_start_ts_ms": final_oos_start,
        "validation_fold_crossing_excluded_stress_trade_count": fold_crossing_excluded,
        "incomplete_trial_runs": {
            trial_id: sorted(run_ids) for trial_id, run_ids in sorted(incomplete_trials.items())
        },
        "trial_integrity": {
            trial_id: {
                "deterministic_signal_pass": value.deterministic_signal_pass,
                "no_lookahead_pass": value.no_lookahead_pass,
                "recursive_dependency_pass": value.recursive_dependency_pass,
                "recursive_comparison_count": value.recursive_comparison_count,
                "recursive_mismatch_count": value.recursive_mismatch_count,
                "evidence_codes": sorted(value.evidence_codes),
            }
            for trial_id, value in sorted(integrity.items())
        },
        "instrument_metadata": {
            "evidence": "CURRENT_PUBLIC_CONSERVATIVE",
            "historical_point_in_time": False,
            "promotion_eligible": False,
        },
        "recursive_dependency_analysis": {
            "status": (
                "FAIL"
                if any(value.recursive_mismatch_count for value in integrity.values())
                else "PASS"
                if all(value.recursive_comparison_count for value in integrity.values())
                else "PARTIAL_INSUFFICIENT_WARMUP"
            ),
            "comparison_count": sum(
                value.recursive_comparison_count for value in integrity.values()
            ),
            "mismatch_count": sum(value.recursive_mismatch_count for value in integrity.values()),
            "promotion_eligible": all(
                value.recursive_dependency_pass for value in integrity.values()
            ),
        },
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "runtime_ai_enabled": False,
    }
    audit["manifest_sha256"] = hashlib.sha256(_canonical_json(audit).encode()).hexdigest()
    audit_rendered = json.dumps(_encode(audit), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    report["execution_evidence"] = {
        "audit_path": args.audit_output.as_posix(),
        "audit_file_sha256": hashlib.sha256(audit_rendered.encode()).hexdigest(),
        "trades_path": args.trades_output.as_posix(),
        "trades_file_sha256": hashlib.sha256(trade_lines.encode()).hexdigest(),
        "retained_trade_count": len(retained_trades),
        "historical_instrument_metadata": "CURRENT_ONLY_PROMOTION_BLOCKED",
        "recursive_dependency_analysis": audit["recursive_dependency_analysis"],
    }
    report["manifest_sha256"] = hashlib.sha256(_canonical_json(report).encode()).hexdigest()
    trailing_ablation = build_trailing_ablation_report(
        report,
        retained_trades,
        generated_ts_utc=now,
    )
    walk_forward = build_walk_forward_report(
        report,
        trades=retained_trades,
        folds_by_horizon={
            horizon: [asdict(fold) for fold in folds] for horizon, folds in folds_by_horizon.items()
        },
        fold_returns=fold_returns,
        fold_trade_counts=fold_trade_counts,
        fold_crossing_excluded_count=fold_crossing_excluded,
        generated_ts_utc=now,
    )
    multiple_testing = build_multiple_testing_report(
        report,
        generated_ts_utc=now,
    )
    _atomic_write(args.trades_output, trade_lines)
    _atomic_write(args.audit_output, audit_rendered)
    _atomic_write(
        args.output,
        json.dumps(_encode(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(
        args.trailing_ablation_output,
        json.dumps(_encode(trailing_ablation), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(
        args.walk_forward_output,
        json.dumps(_encode(walk_forward), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write(
        args.multiple_testing_output,
        json.dumps(_encode(multiple_testing), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    print(
        json.dumps(
            {
                "output": args.output.as_posix(),
                "status": report["status"],
                "executed_trial_count": report["executed_trial_count"],
                "retained_trade_count": len(retained_trades),
                "selection_count": report["selection_count"],
                "final_oos_status": report["final_oos_status"],
                "manifest_sha256": report["manifest_sha256"],
                "trailing_ablation_output": args.trailing_ablation_output.as_posix(),
                "trailing_ablation_status": trailing_ablation["status"],
                "walk_forward_output": args.walk_forward_output.as_posix(),
                "walk_forward_status": walk_forward["status"],
                "multiple_testing_output": args.multiple_testing_output.as_posix(),
                "multiple_testing_status": multiple_testing["status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
