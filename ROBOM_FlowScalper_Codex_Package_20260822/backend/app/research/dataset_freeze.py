"""100후보가 동일한 공개시장 Train·Validation·Final OOS만 쓰도록 동결한다."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from backend.app.research.candidate_registry import HORIZON_MAXIMUM_HOLD_MS
from backend.app.research.protocol import DatasetSlice


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def build_strategy_100_dataset_manifest(
    source_evidence: Mapping[str, object],
    recomputed_slices: Sequence[DatasetSlice],
    *,
    source_evidence_path: str,
    source_evidence_sha256: str,
    trial_manifest: Mapping[str, object],
    trial_manifest_path: str,
    trial_manifest_file_sha256: str,
    generated_ts_utc: str,
) -> dict[str, Any]:
    """기존 실행증거와 현재 archive가 byte checksum까지 같을 때만 동결한다."""

    trial_manifest_material = dict(trial_manifest)
    claimed_trial_manifest_sha = trial_manifest_material.pop("manifest_sha256", None)
    actual_trial_manifest_sha = hashlib.sha256(
        _canonical_json(trial_manifest_material).encode()
    ).hexdigest()
    if claimed_trial_manifest_sha != actual_trial_manifest_sha:
        raise ValueError("100후보 trial manifest 내부 checksum이 다릅니다.")
    if (
        not source_evidence_path
        or not trial_manifest_path
        or not _is_sha256(source_evidence_sha256)
        or not _is_sha256(trial_manifest_file_sha256)
    ):
        raise ValueError("dataset freeze 입력 경로·파일 checksum이 잘못됐습니다.")
    if (
        trial_manifest.get("status") != "PREREGISTERED_NOT_EXECUTED"
        or trial_manifest.get("trial_count") != 100
        or trial_manifest.get("alpha_family_count") != 20
        or trial_manifest.get("exit_module_count") != 5
        or trial_manifest.get("runtime_active_count") != 0
        or trial_manifest.get("live_shadow_count") != 0
    ):
        raise ValueError("100후보 trial manifest 사전등록·비활성 계약이 다릅니다.")
    trial_source_rows = trial_manifest.get("source_checksums")
    if not isinstance(trial_source_rows, Mapping) or not trial_source_rows:
        raise ValueError("100후보 trial manifest source checksum이 없습니다.")
    trial_source_checksums = {
        str(path): str(checksum) for path, checksum in trial_source_rows.items()
    }
    if any(
        not path or not _is_sha256(checksum) for path, checksum in trial_source_checksums.items()
    ):
        raise ValueError("100후보 trial manifest source checksum 형식이 잘못됐습니다.")
    trial_code_version = trial_manifest.get("code_version")
    if not isinstance(trial_code_version, str) or not trial_code_version:
        raise ValueError("100후보 trial manifest code version이 없습니다.")

    source_manifest = source_evidence.get("manifest")
    source_result = source_evidence.get("result")
    if not isinstance(source_manifest, Mapping) or not isinstance(source_result, Mapping):
        raise ValueError("원본 연구증거의 manifest와 result가 필요합니다.")
    if source_manifest.get("status") != "EXECUTED":
        raise ValueError("실제로 실행 완료된 원본 연구증거만 동결할 수 있습니다.")
    if source_result.get("execution_scope") != "FULL_PREREGISTERED_ARCHIVE":
        raise ValueError("부분 진단 archive는 100후보 데이터셋으로 동결할 수 없습니다.")
    source_rows = source_manifest.get("dataset")
    source_dataset_hash = source_manifest.get("dataset_hash")
    if not isinstance(source_rows, list) or not isinstance(source_dataset_hash, str):
        raise ValueError("원본 dataset 행과 hash가 필요합니다.")

    source_by_run: dict[str, dict[str, object]] = {}
    for row in source_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("run_id"), str):
            raise ValueError("원본 dataset 행 형식이 잘못됐습니다.")
        normalized = json.loads(_canonical_json(dict(row)))
        run_id = str(normalized["run_id"])
        if run_id in source_by_run:
            raise ValueError("원본 dataset Run ID가 중복됐습니다.")
        source_by_run[run_id] = normalized

    recomputed_by_run = {
        row.run_id: json.loads(_canonical_json(asdict(row))) for row in recomputed_slices
    }
    if len(recomputed_by_run) != len(recomputed_slices):
        raise ValueError("재계산 dataset Run ID가 중복됐습니다.")
    if set(recomputed_by_run) != set(source_by_run):
        raise ValueError("현재 archive Run 범위가 원본 동결 대상과 다릅니다.")
    mismatches = [
        run_id
        for run_id in sorted(source_by_run)
        if recomputed_by_run[run_id] != source_by_run[run_id]
    ]
    if mismatches:
        raise ValueError(f"현재 archive가 원본 checksum과 다릅니다: {mismatches}")

    split_rows = source_result.get("splits")
    if not isinstance(split_rows, Mapping):
        raise ValueError("원본 연구증거의 split 정보가 필요합니다.")
    split_run_ids: dict[str, tuple[str, ...]] = {}
    for split in ("train", "validation", "oos"):
        split_payload = split_rows.get(split)
        if not isinstance(split_payload, Mapping):
            raise ValueError(f"원본 {split} split 정보가 필요합니다.")
        run_ids = split_payload.get("run_ids")
        if (
            not isinstance(run_ids, list)
            or not run_ids
            or not all(isinstance(run_id, str) for run_id in run_ids)
        ):
            raise ValueError(f"원본 {split} Run 목록이 잘못됐습니다.")
        split_run_ids[split] = tuple(run_ids)
    flattened = [run_id for values in split_run_ids.values() for run_id in values]
    if len(flattened) != len(set(flattened)) or set(flattened) != set(source_by_run):
        raise ValueError("Train·Validation·Final OOS Run은 중복 없이 전체를 덮어야 합니다.")

    train_end = max(
        int(str(source_by_run[run_id]["end_ts_ms"])) for run_id in split_run_ids["train"]
    )
    validation_start = min(
        int(str(source_by_run[run_id]["start_ts_ms"])) for run_id in split_run_ids["validation"]
    )
    validation_end = max(
        int(str(source_by_run[run_id]["end_ts_ms"])) for run_id in split_run_ids["validation"]
    )
    oos_start = min(
        int(str(source_by_run[run_id]["start_ts_ms"])) for run_id in split_run_ids["oos"]
    )
    if train_end >= validation_start or validation_end >= oos_start:
        raise ValueError("Train·Validation·Final OOS 시간이 겹치거나 역행합니다.")

    role_by_run = (
        {run_id: "TRAIN" for run_id in split_run_ids["train"]}
        | {run_id: "VALIDATION" for run_id in split_run_ids["validation"]}
        | {run_id: "FINAL_OOS" for run_id in split_run_ids["oos"]}
    )
    source_order = source_manifest.get("run_ids")
    if not isinstance(source_order, list) or not all(
        isinstance(run_id, str) for run_id in source_order
    ):
        raise ValueError("원본 dataset 순서가 필요합니다.")
    if len(source_order) != len(set(source_order)) or set(source_order) != set(source_by_run):
        raise ValueError("원본 dataset 순서가 중복 없이 전체 Run을 덮어야 합니다.")
    ordered_ranges = [
        (
            int(str(source_by_run[run_id]["start_ts_ms"])),
            int(str(source_by_run[run_id]["end_ts_ms"])),
        )
        for run_id in source_order
    ]
    if ordered_ranges != sorted(ordered_ranges):
        raise ValueError("원본 dataset Run 순서가 시간순이 아닙니다.")
    frozen_rows = [
        {**source_by_run[run_id], "role": role_by_run[run_id]} for run_id in source_order
    ]
    if len(frozen_rows) != len(source_by_run):
        raise ValueError("원본 dataset 순서가 전체 Run을 덮지 않습니다.")

    manifest: dict[str, Any] = {
        "schema_version": 2,
        "status": "FROZEN_HISTORICAL_FORWARD_PENDING",
        "generated_ts_utc": generated_ts_utc,
        "source_evidence": {
            "path": source_evidence_path,
            "sha256": source_evidence_sha256,
            "dataset_hash": source_dataset_hash,
            "execution_scope": source_result["execution_scope"],
        },
        "trial_manifest": {
            "path": trial_manifest_path,
            "file_sha256": trial_manifest_file_sha256,
            "manifest_sha256": actual_trial_manifest_sha,
            "code_version": trial_code_version,
            "source_checksums": dict(sorted(trial_source_checksums.items())),
        },
        "historical_splits": {
            "random_shuffle": False,
            "train_run_ids": list(split_run_ids["train"]),
            "validation_run_ids": list(split_run_ids["validation"]),
            "final_oos_run_ids": list(split_run_ids["oos"]),
            "train_end_ts_ms": train_end,
            "validation_start_ts_ms": validation_start,
            "validation_end_ts_ms": validation_end,
            "final_oos_start_ts_ms": oos_start,
            "purge_and_embargo_required": True,
            "purge_embargo_ms_by_horizon": dict(HORIZON_MAXIMUM_HOLD_MS),
            "maximum_holding_ms_by_horizon": dict(HORIZON_MAXIMUM_HOLD_MS),
            "horizon_without_four_usable_validation_folds_must_fail": True,
            "symbol_venue_regime_volatility_cost_holdouts_required": True,
        },
        "forward_live_public": {
            "status": "NOT_STARTED",
            "must_start_after_historical_freeze": True,
            "may_not_be_reassigned_to_historical_split": True,
        },
        "archive_verification": {
            "status": "PASS",
            "method": "RECOMPUTED_RUN_EVENT_RANGE_COUNT_AND_FILE_SHA256",
            "run_count": len(frozen_rows),
            "event_count": sum(int(str(row["event_count"])) for row in frozen_rows),
        },
        "candidate_contract": {
            "alpha_family_count": 20,
            "exit_module_count": 5,
            "trial_count": 100,
            "same_historical_dataset_required": True,
            "screening_may_execute": True,
            "live_shadow_may_execute": False,
        },
        "runs": frozen_rows,
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "runtime_ai_enabled": False,
    }
    manifest["manifest_sha256"] = hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()
    return manifest
