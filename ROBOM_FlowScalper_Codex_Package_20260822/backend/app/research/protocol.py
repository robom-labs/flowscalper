"""시계열 누수·다중검정·재현성 경계를 코드로 강제하는 PAPER 연구 계약이다."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import combinations
from statistics import NormalDist, fmean, pstdev
from typing import Any


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _checksum(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetSlice:
    run_id: str
    venue: str
    symbols: tuple[str, ...]
    start_ts_ms: int
    end_ts_ms: int
    event_count: int
    checksum: str

    def __post_init__(self) -> None:
        if not self.run_id or not self.venue or not self.checksum:
            raise ValueError("dataset slice 식별자·시장·checksum이 필요합니다.")
        if self.start_ts_ms < 0 or self.end_ts_ms < self.start_ts_ms:
            raise ValueError("dataset slice 시간 범위가 올바르지 않습니다.")
        if self.event_count <= 0 or not self.symbols:
            raise ValueError("dataset slice에는 이벤트와 종목이 있어야 합니다.")


@dataclass(frozen=True, slots=True)
class ResearchObservation:
    observation_id: str
    run_id: str
    symbol: str
    signal_ts_ms: int
    outcome_ts_ms: int
    net_bps: float
    regime: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if not self.observation_id or not self.run_id or not self.symbol:
            raise ValueError("연구 관측값 식별자가 필요합니다.")
        if self.outcome_ts_ms < self.signal_ts_ms:
            raise ValueError("outcome은 signal보다 빠를 수 없습니다.")
        if not math.isfinite(self.net_bps):
            raise ValueError("연구 수익률은 유한한 값이어야 합니다.")


@dataclass(frozen=True, slots=True)
class ResearchProtocol:
    hypothesis_id: str
    strategy_id: str
    strategy_version: str
    feature_version: str
    cost_model_version: str
    parameter_grid: Mapping[str, Sequence[object]]
    horizon_seconds: tuple[int, ...]
    base_cost_bps: float
    stress_cost_bps: float
    seed: int
    purge_ms: int
    embargo_ms: int
    falsification_criteria: tuple[str, ...]
    baseline_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        identifiers = (
            self.hypothesis_id,
            self.strategy_id,
            self.strategy_version,
            self.feature_version,
            self.cost_model_version,
        )
        if any(not value for value in identifiers):
            raise ValueError("연구 가설·전략·버전 식별자가 모두 필요합니다.")
        if not self.parameter_grid or any(not values for values in self.parameter_grid.values()):
            raise ValueError("사전등록 parameter grid가 비어 있습니다.")
        if not self.horizon_seconds or any(value <= 0 for value in self.horizon_seconds):
            raise ValueError("평가 horizon은 양수여야 합니다.")
        if self.base_cost_bps < 0 or self.stress_cost_bps < self.base_cost_bps:
            raise ValueError("STRESS 비용은 BASE 비용 이상이어야 합니다.")
        if self.purge_ms < 0 or self.embargo_ms < 0:
            raise ValueError("purge와 embargo는 음수일 수 없습니다.")
        if not self.falsification_criteria or not self.baseline_ids:
            raise ValueError("반증기준과 비교 baseline을 사전등록해야 합니다.")

    def manifest(
        self,
        dataset: Sequence[DatasetSlice],
        *,
        code_hash: str,
        config_hash: str,
        generated_ts_ms: int,
    ) -> dict[str, Any]:
        if not dataset:
            raise ValueError("연구 dataset manifest가 비어 있습니다.")
        ordered = sorted(dataset, key=lambda row: (row.start_ts_ms, row.run_id))
        dataset_rows = [asdict(row) for row in ordered]
        protocol_payload = {
            **asdict(self),
            "parameter_grid": {
                key: list(values) for key, values in sorted(self.parameter_grid.items())
            },
        }
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "status": "PREREGISTERED",
            "generated_ts_ms": generated_ts_ms,
            "hypothesis": {
                "hypothesis_id": self.hypothesis_id,
                "strategy_id": self.strategy_id,
                "falsification_criteria": list(self.falsification_criteria),
                "baseline_ids": list(self.baseline_ids),
            },
            "versions": {
                "strategy_version": self.strategy_version,
                "feature_version": self.feature_version,
                "cost_model_version": self.cost_model_version,
                "code_hash": code_hash,
                "config_hash": config_hash,
            },
            "protocol": protocol_payload,
            "dataset": dataset_rows,
            "dataset_hash": _checksum(dataset_rows),
            "parameter_hash": _checksum(protocol_payload["parameter_grid"]),
            "run_ids": [row.run_id for row in ordered],
            "time_range": {
                "start_ts_ms": min(row.start_ts_ms for row in ordered),
                "end_ts_ms": max(row.end_ts_ms for row in ordered),
            },
            "operational_metrics_required": [
                "processing_lag_p95_ms",
                "reconnect_count",
                "sequence_gap_count",
                "dropped_event_count",
                "persistence_fault_count",
                "cpu_percent",
                "memory_rss_bytes",
            ],
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }
        manifest["manifest_checksum"] = _checksum(manifest)
        return manifest


def chronological_split(
    observations: Sequence[ResearchObservation],
    *,
    train_end_ts_ms: int,
    validation_end_ts_ms: int,
    purge_ms: int,
    embargo_ms: int,
) -> dict[str, tuple[ResearchObservation, ...]]:
    """outcome 종료와 다음 구간 signal 사이 purge·embargo를 강제한다."""

    if train_end_ts_ms >= validation_end_ts_ms:
        raise ValueError("train 종료는 validation 종료보다 빨라야 합니다.")
    if purge_ms < 0 or embargo_ms < 0:
        raise ValueError("purge와 embargo는 음수일 수 없습니다.")
    ordered = sorted(observations, key=lambda row: (row.signal_ts_ms, row.observation_id))
    train = tuple(
        row for row in ordered if row.outcome_ts_ms < train_end_ts_ms - purge_ms
    )
    validation = tuple(
        row
        for row in ordered
        if row.signal_ts_ms > train_end_ts_ms + embargo_ms
        and row.outcome_ts_ms < validation_end_ts_ms - purge_ms
    )
    oos = tuple(
        row for row in ordered if row.signal_ts_ms > validation_end_ts_ms + embargo_ms
    )
    _assert_disjoint(train, validation, oos)
    return {"train": train, "validation": validation, "oos": oos}


def walk_forward_folds(
    observations: Sequence[ResearchObservation],
    *,
    train_size: int,
    validation_size: int,
    oos_size: int,
    purge_ms: int,
    embargo_ms: int,
    step_size: int | None = None,
) -> tuple[dict[str, tuple[ResearchObservation, ...]], ...]:
    """고정 순서의 expanding이 아닌 rolling train·validation·OOS fold를 만든다."""

    sizes = (train_size, validation_size, oos_size)
    if any(size <= 0 for size in sizes):
        raise ValueError("walk-forward 구간 크기는 양수여야 합니다.")
    ordered = sorted(observations, key=lambda row: (row.signal_ts_ms, row.observation_id))
    step = step_size or oos_size
    if step <= 0:
        raise ValueError("walk-forward step은 양수여야 합니다.")
    folds: list[dict[str, tuple[ResearchObservation, ...]]] = []
    total = sum(sizes)
    for start in range(0, len(ordered) - total + 1, step):
        raw = ordered[start : start + total]
        train_end = raw[train_size].signal_ts_ms
        validation_end = raw[train_size + validation_size].signal_ts_ms
        split = chronological_split(
            raw,
            train_end_ts_ms=train_end,
            validation_end_ts_ms=validation_end,
            purge_ms=purge_ms,
            embargo_ms=embargo_ms,
        )
        folds.append(split)
    return tuple(folds)


def probability_of_backtest_overfitting(
    candidate_fold_returns: Mapping[str, Sequence[float]],
) -> dict[str, object]:
    """대칭 fold 조합에서 in-sample 우승 후보의 OOS 상대순위로 PBO를 계산한다."""

    _validate_candidate_returns(candidate_fold_returns)
    names = sorted(candidate_fold_returns)
    fold_count = len(candidate_fold_returns[names[0]])
    if len(names) == 1:
        return {"pbo": 0.0, "combinations": 0, "logits": [], "status": "ONE_HYPOTHESIS"}
    if fold_count < 4 or fold_count % 2:
        raise ValueError("PBO에는 4개 이상의 짝수 fold가 필요합니다.")
    half = fold_count // 2
    logits: list[float] = []
    all_indexes = set(range(fold_count))
    for train_indexes in combinations(range(fold_count), half):
        if 0 not in train_indexes:
            continue
        test_indexes = sorted(all_indexes - set(train_indexes))
        selected = max(
            names,
            key=lambda name: (
                fmean(candidate_fold_returns[name][index] for index in train_indexes),
                name,
            ),
        )
        test_scores = {
            name: fmean(candidate_fold_returns[name][index] for index in test_indexes)
            for name in names
        }
        ordered_names = sorted(names, key=lambda name: (test_scores[name], name))
        rank_fraction = ordered_names.index(selected) / (len(names) - 1)
        bounded = min(max(rank_fraction, 1e-9), 1 - 1e-9)
        logits.append(math.log(bounded / (1 - bounded)))
    pbo = sum(value <= 0 for value in logits) / len(logits) if logits else 0.0
    return {"pbo": pbo, "combinations": len(logits), "logits": logits, "status": "CALCULATED"}


def deflated_sharpe_ratio(
    returns: Sequence[float],
    *,
    trials: int,
) -> dict[str, float | int | str | None]:
    """비독립 시계열 연환산 없이 후보 수·왜도·첨도를 반영한 DSR을 계산한다."""

    if trials <= 0:
        raise ValueError("가설 수는 양수여야 합니다.")
    values = [float(value) for value in returns]
    if len(values) < 3:
        return {
            "dsr_probability": None,
            "observed_sharpe": None,
            "trials": trials,
            "status": "INSUFFICIENT_SAMPLE",
        }
    sigma = pstdev(values)
    if sigma == 0:
        return {
            "dsr_probability": None,
            "observed_sharpe": None,
            "trials": trials,
            "status": "ZERO_VARIANCE",
        }
    mean = fmean(values)
    sharpe = mean / sigma
    centered = [(value - mean) / sigma for value in values]
    skew = fmean(value**3 for value in centered)
    kurtosis = fmean(value**4 for value in centered)
    gamma = 0.5772156649015329
    normal = NormalDist()
    expected_max = 0.0
    if trials > 1:
        expected_max = (
            (1 - gamma) * normal.inv_cdf(1 - 1 / trials)
            + gamma * normal.inv_cdf(1 - 1 / (trials * math.e))
        )
    variance = (
        1 - skew * sharpe + ((kurtosis - 1) / 4) * sharpe**2
    ) / (len(values) - 1)
    if variance <= 0:
        return {
            "dsr_probability": None,
            "observed_sharpe": sharpe,
            "trials": trials,
            "status": "INVALID_VARIANCE",
        }
    probability = normal.cdf((sharpe - expected_max) / math.sqrt(variance))
    return {
        "dsr_probability": probability,
        "observed_sharpe": sharpe,
        "expected_max_sharpe": expected_max,
        "trials": trials,
        "status": "CALCULATED",
    }


def bootstrap_mean_interval(
    values: Sequence[float],
    *,
    seed: int,
    resamples: int = 2_000,
    confidence: float = 0.95,
) -> dict[str, float | int | str | None]:
    """고정 seed bootstrap으로 기대값 불확실성 범위를 재현 가능하게 계산한다."""

    if not values:
        return {"lower": None, "upper": None, "resamples": 0, "status": "INSUFFICIENT_SAMPLE"}
    if resamples <= 0 or not 0 < confidence < 1:
        raise ValueError("bootstrap 반복수와 신뢰수준이 올바르지 않습니다.")
    samples = [float(value) for value in values]
    rng = random.Random(seed)
    means = sorted(
        fmean(rng.choice(samples) for _ in samples)
        for _ in range(resamples)
    )
    tail = (1 - confidence) / 2
    lower_index = min(len(means) - 1, max(0, int(tail * len(means))))
    upper_index = min(len(means) - 1, max(0, int((1 - tail) * len(means)) - 1))
    return {
        "lower": means[lower_index],
        "upper": means[upper_index],
        "resamples": resamples,
        "status": "CALCULATED",
    }


def finalize_research_manifest(
    manifest: Mapping[str, object],
    *,
    result: Mapping[str, object],
    completed_ts_ms: int,
) -> dict[str, object]:
    """사전등록 manifest와 실행결과를 checksum으로 묶어 사후수정을 드러낸다."""

    if manifest.get("status") != "PREREGISTERED":
        raise ValueError("PREREGISTERED 연구만 실행 완료로 전환할 수 있습니다.")
    finalized = {
        **dict(manifest),
        "status": "EXECUTED",
        "completed_ts_ms": completed_ts_ms,
        "result_hash": _checksum(result),
    }
    finalized.pop("manifest_checksum", None)
    finalized["manifest_checksum"] = _checksum(finalized)
    return finalized


def _validate_candidate_returns(candidate_fold_returns: Mapping[str, Sequence[float]]) -> None:
    if not candidate_fold_returns:
        raise ValueError("후보별 fold 수익률이 비어 있습니다.")
    lengths = {len(values) for values in candidate_fold_returns.values()}
    if len(lengths) != 1 or not next(iter(lengths)):
        raise ValueError("모든 후보는 동일한 수의 fold 수익률이 필요합니다.")
    if any(
        not math.isfinite(float(value))
        for values in candidate_fold_returns.values()
        for value in values
    ):
        raise ValueError("fold 수익률은 유한한 값이어야 합니다.")


def _assert_disjoint(*groups: Sequence[ResearchObservation]) -> None:
    identifiers = [
        {row.observation_id for row in group}
        for group in groups
    ]
    for left_index, left in enumerate(identifiers):
        for right in identifiers[left_index + 1 :]:
            if left & right:
                raise ValueError("연구 시계열 구간에 같은 관측값이 중복됐습니다.")
