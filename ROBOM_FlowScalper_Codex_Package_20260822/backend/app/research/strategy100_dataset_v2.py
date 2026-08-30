# 동결 LIVE_PUBLIC 파일을 겹치지 않는 시간순 100후보 연구구간으로 바인딩한다.

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.app.research.candidate_registry import HORIZON_MAXIMUM_HOLD_MS

_HOUR_PARTITION = re.compile(
    r"^(venue=[^/]+/run=[^/]+/date=(\d{4}-\d{2}-\d{2})/"
    r"symbol=MULTI/hour=(\d{2}))/"
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def manifest_checksum(manifest: Mapping[str, object]) -> str:
    material = dict(manifest)
    material.pop("manifest_sha256", None)
    return hashlib.sha256(canonical_json(material).encode()).hexdigest()


def resolve_manifest_path(path: str, *, binding_path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve(strict=True)
    project_root = binding_path.resolve(strict=True).parent.parent
    return (project_root / candidate).resolve(strict=True)


def load_bound_manifest(
    reference: Mapping[str, object],
    *,
    binding_path: Path,
    expected_status: str,
    name: str,
) -> tuple[dict[str, Any], Path, str]:
    path = resolve_manifest_path(str(reference.get("path", "")), binding_path=binding_path)
    payload_bytes = path.read_bytes()
    file_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    payload: dict[str, Any] = json.loads(payload_bytes)
    internal_sha256 = manifest_checksum(payload)
    if (
        payload.get("status") != expected_status
        or payload.get("manifest_sha256") != internal_sha256
        or reference.get("manifest_sha256") != internal_sha256
        or reference.get("file_sha256") != file_sha256
        or payload.get("paper_only") is not True
        or payload.get("real_orders_enabled") is not False
    ):
        raise ValueError(f"{name} manifest 연결·checksum·PAPER 계약이 잘못됐습니다.")
    return payload, path, file_sha256


def _hour_partition(relative_path: str) -> tuple[str, int]:
    matched = _HOUR_PARTITION.match(relative_path)
    if matched is None:
        raise ValueError(f"LIVE_PUBLIC 파일의 시간 partition이 잘못됐습니다: {relative_path}")
    start = datetime.fromisoformat(f"{matched.group(2)}T{matched.group(3)}:00:00+00:00")
    return matched.group(1), int(start.timestamp() * 1_000)


def build_strategy_100_dataset_v2_manifest(
    *,
    trial_manifest: Mapping[str, object],
    trial_manifest_path: str,
    trial_manifest_file_sha256: str,
    live_public_cut: Mapping[str, object],
    live_public_cut_path: str,
    live_public_cut_file_sha256: str,
    warmup_manifest: Mapping[str, object],
    warmup_manifest_path: str,
    warmup_manifest_file_sha256: str,
    train_hours: int,
    validation_hours_each: int,
    generated_ts_utc: str,
) -> dict[str, Any]:
    if (
        trial_manifest.get("status") != "PREREGISTERED_NOT_EXECUTED"
        or trial_manifest.get("trial_count") != 100
        or trial_manifest.get("screening_eligible_count") != 90
        or trial_manifest.get("runtime_active_count") != 0
        or trial_manifest.get("live_shadow_count") != 0
        or trial_manifest.get("manifest_sha256") != manifest_checksum(trial_manifest)
    ):
        raise ValueError("100후보 V2 trial manifest 계약이 잘못됐습니다.")
    if (
        live_public_cut.get("status") != "FROZEN_LIVE_PUBLIC_CUT"
        or live_public_cut.get("manifest_sha256") != manifest_checksum(live_public_cut)
        or live_public_cut.get("paper_only") is not True
        or live_public_cut.get("real_orders_enabled") is not False
        or live_public_cut.get("auth_required") is not False
    ):
        raise ValueError("100후보 V2 LIVE_PUBLIC cut 계약이 잘못됐습니다.")
    if (
        warmup_manifest.get("status") != "FROZEN_PUBLIC_KLINE_WARMUP"
        or warmup_manifest.get("manifest_sha256") != manifest_checksum(warmup_manifest)
        or warmup_manifest.get("paper_only") is not True
        or warmup_manifest.get("real_orders_enabled") is not False
        or warmup_manifest.get("private_api_enabled") is not False
    ):
        raise ValueError("100후보 V2 공개봉 워밍업 계약이 잘못됐습니다.")
    if train_hours < 20 or validation_hours_each < 40:
        raise ValueError("100후보 V2는 Train 20시간·Validation 각 40시간 이상이 필요합니다.")
    file_rows = live_public_cut.get("files")
    if not isinstance(file_rows, list) or not file_rows:
        raise ValueError("100후보 V2 LIVE_PUBLIC 파일 목록이 없습니다.")
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    hour_start_by_partition: dict[str, int] = {}
    for row in file_rows:
        if not isinstance(row, Mapping):
            raise ValueError("100후보 V2 LIVE_PUBLIC 파일 행이 잘못됐습니다.")
        partition, hour_start = _hour_partition(str(row.get("relative_path", "")))
        grouped[partition].append(row)
        hour_start_by_partition[partition] = hour_start
    ordered_partitions = tuple(
        sorted(grouped, key=lambda value: (hour_start_by_partition[value], value))
    )
    starts = [hour_start_by_partition[value] for value in ordered_partitions]
    if any(right - left != 3_600_000 for left, right in zip(starts, starts[1:], strict=False)):
        raise ValueError("100후보 V2 LIVE_PUBLIC 시간 partition이 연속되지 않습니다.")
    required_hours = train_hours + validation_hours_each * 2 + 1
    if len(ordered_partitions) < required_hours:
        raise ValueError(
            f"100후보 V2 시간 partition이 부족합니다: {len(ordered_partitions)} < {required_hours}"
        )
    source_run_id = str(live_public_cut.get("run_id", ""))
    if not source_run_id:
        raise ValueError("100후보 V2 source Run ID가 없습니다.")
    boundaries = (
        ("TRAIN", 0, train_hours),
        ("VALIDATION", train_hours, train_hours + validation_hours_each),
        (
            "VALIDATION",
            train_hours + validation_hours_each,
            train_hours + validation_hours_each * 2,
        ),
        ("FINAL_OOS", train_hours + validation_hours_each * 2, len(ordered_partitions)),
    )
    role_counts: dict[str, int] = defaultdict(int)
    runs: list[dict[str, object]] = []
    for role, start_index, end_index in boundaries:
        selected = ordered_partitions[start_index:end_index]
        if not selected:
            raise ValueError(f"100후보 V2 {role} partition이 비어 있습니다.")
        role_counts[role] += 1
        logical_run_id = f"{source_run_id}-{role}-{role_counts[role]}-V2"
        selected_rows = [row for partition in selected for row in grouped[partition]]
        run_start = hour_start_by_partition[selected[0]]
        run_end = hour_start_by_partition[selected[-1]] + 3_600_000 - 1
        runs.append(
            {
                "run_id": logical_run_id,
                "source_run_id": source_run_id,
                "role": role,
                "start_ts_ms": run_start,
                "end_ts_ms": run_end,
                "archive_partitions": list(selected),
                "archive_file_count": len(selected_rows),
                "event_count": sum(int(str(row["event_count"])) for row in selected_rows),
                "first_event_ts_ms": min(
                    int(str(row["first_ts_ms"])) for row in selected_rows
                ),
                "last_event_ts_ms": max(
                    int(str(row["last_ts_ms"])) for row in selected_rows
                ),
            }
        )
    train = runs[0]
    validation = runs[1:3]
    final_oos = runs[3]
    manifest: dict[str, Any] = {
        "schema_version": 3,
        "status": "FROZEN_HISTORICAL_FORWARD_PENDING",
        "generated_ts_utc": generated_ts_utc,
        "trial_manifest": {
            "path": trial_manifest_path,
            "file_sha256": trial_manifest_file_sha256,
            "manifest_sha256": trial_manifest["manifest_sha256"],
            "code_version": trial_manifest["code_version"],
            "source_checksums": trial_manifest["source_checksums"],
        },
        "live_public_cut": {
            "path": live_public_cut_path,
            "file_sha256": live_public_cut_file_sha256,
            "manifest_sha256": live_public_cut["manifest_sha256"],
            "source_run_id": source_run_id,
            "archive_root": live_public_cut["archive_root"],
            "file_count": live_public_cut["file_count"],
            "event_count": live_public_cut["event_count"],
        },
        "warmup_manifest": {
            "path": warmup_manifest_path,
            "file_sha256": warmup_manifest_file_sha256,
            "manifest_sha256": warmup_manifest["manifest_sha256"],
            "symbol_count": warmup_manifest["symbol_count"],
            "cutoff_ts_ms": warmup_manifest["cutoff_ts_ms"],
        },
        "historical_splits": {
            "random_shuffle": False,
            "train_run_ids": [train["run_id"]],
            "validation_run_ids": [row["run_id"] for row in validation],
            "final_oos_run_ids": [final_oos["run_id"]],
            "train_end_ts_ms": train["end_ts_ms"],
            "validation_start_ts_ms": validation[0]["start_ts_ms"],
            "validation_end_ts_ms": validation[-1]["end_ts_ms"],
            "final_oos_start_ts_ms": final_oos["start_ts_ms"],
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
            "method": "BOUND_FROZEN_LIVE_PUBLIC_CUT_EXPLICIT_FILE_PARTITIONS",
            "source_run_count": 1,
            "logical_run_count": len(runs),
            "file_count": sum(int(str(row["archive_file_count"])) for row in runs),
            "event_count": sum(int(str(row["event_count"])) for row in runs),
            "disjoint_logical_partitions": True,
        },
        "candidate_contract": {
            "alpha_family_count": 20,
            "exit_module_count": 5,
            "trial_count": 100,
            "screening_eligible_count": 90,
            "same_historical_dataset_required": True,
            "independent_base_stress_accounts": 180,
            "screening_may_execute": True,
            "live_shadow_may_execute": False,
        },
        "research_interpretation": {
            "retrospective_diagnostic_only": True,
            "promotion_eligible": False,
            "final_oos_sealed_during_stage1": True,
            "profitability_claim_allowed": False,
        },
        "runs": runs,
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "runtime_ai_enabled": False,
    }
    if manifest["archive_verification"]["file_count"] != live_public_cut["file_count"]:
        raise ValueError("100후보 V2 논리구간이 동결 파일 전체를 정확히 덮지 않습니다.")
    manifest["manifest_sha256"] = manifest_checksum(manifest)
    return manifest


def archive_files_for_logical_run(
    *,
    live_public_cut: Mapping[str, object],
    logical_run: Mapping[str, object],
) -> tuple[Path, ...]:
    root = Path(str(live_public_cut.get("archive_root", ""))).resolve(strict=True)
    partitions = logical_run.get("archive_partitions")
    file_rows = live_public_cut.get("files")
    if not isinstance(partitions, list) or not isinstance(file_rows, list):
        raise ValueError("100후보 V2 논리구간 또는 동결 파일 목록이 없습니다.")
    prefixes = tuple(f"{str(partition).rstrip('/')}/" for partition in partitions)
    selected_rows = [
        row
        for row in file_rows
        if isinstance(row, Mapping)
        and str(row.get("relative_path", "")).startswith(prefixes)
    ]
    if len(selected_rows) != int(str(logical_run.get("archive_file_count", -1))):
        raise ValueError("100후보 V2 논리구간 파일수가 manifest와 다릅니다.")
    paths: list[Path] = []
    for row in selected_rows:
        path = (root / str(row["relative_path"])).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("100후보 V2 archive 파일이 root 밖에 있습니다.")
        stat = path.stat()
        if (
            stat.st_size != int(str(row["size_bytes"]))
            or stat.st_mtime_ns != int(str(row["mtime_ns"]))
            or stat.st_ctime_ns != int(str(row["ctime_ns"]))
            or stat.st_ino != int(str(row["inode"]))
        ):
            raise ValueError(f"100후보 V2 동결 archive 파일 identity가 다릅니다: {path}")
        paths.append(path)
    return tuple(sorted(paths))
