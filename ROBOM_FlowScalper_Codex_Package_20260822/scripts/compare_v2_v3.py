# V6 사전등록 V3와 기존 V2를 같은 동결입력에서 비교하고 증거 부재를 fail-closed한다.

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import NormalDist, fmean, stdev
from typing import Any

from backend.app.research.v6_candidates import (
    V6VariantSpec,
    v6_preregistered_variants,
    v6_preregistration_manifest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "evidence/V6_V2_V3_FIXED_INPUT_RESULTS.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "evidence/V6_V2_V3_COMPARISON.json"
SOURCE_PATHS = (
    "AGENTS.md",
    "Makefile",
    "VERSION",
    "pyproject.toml",
    "uv.lock",
    "backend",
    "config",
    "frontend",
    "scripts",
)
FIXED_INPUT_SCHEMA = "flowscalper.v6_v2_v3_fixed_input.v1"
DATA_MANIFEST_SCHEMA = "flowscalper.v6_fixed_input_data_manifest.v1"
COMPARISON_MANIFEST_SCHEMA = "flowscalper.v6_strategy_comparison.v1"
MEASUREMENT_SCHEMA = "flowscalper.v6_strategy_measurement.v1"
RAW_MEASUREMENT_SCHEMA = "flowscalper.v6_raw_measurement_results.v1"
RAW_COST_FIELDS = (
    "fee_usdt",
    "spread_cost_usdt",
    "slippage_usdt",
    "latency_penalty_usdt",
    "funding_usdt",
)
OPERATIONAL_CHECK_IDS = (
    "paper_only",
    "real_orders_disabled",
    "auth_disabled",
    "private_api_disabled",
    "api_key_disabled",
    "wallet_disabled",
    "runtime_ai_order_decisions_disabled",
    "entry_history_parity",
    "no_console_errors",
)
COMPARISON_NUMERIC_FIELDS = {
    "base_sample_size",
    "stress_sample_size",
    "base_expectancy",
    "stress_expectancy",
    "base_cost_coverage",
    "stress_cost_coverage",
    "base_expectancy_delta",
    "stress_expectancy_delta",
    "drawdown_delta",
    "cost_burden_delta",
    "oos_lower_bound",
    "dsr",
    "pbo",
}
COMPARISON_REQUIRED_FIELDS = COMPARISON_NUMERIC_FIELDS | {
    "same_frozen_input",
    "operational_regression",
}
BASELINE_MEASUREMENT_NUMERIC_FIELDS = {
    "base_expectancy",
    "stress_expectancy",
    "drawdown",
    "cost_burden",
}
CANDIDATE_MEASUREMENT_NUMERIC_FIELDS = {
    "base_sample_size",
    "stress_sample_size",
    "base_expectancy",
    "stress_expectancy",
    "base_cost_coverage",
    "stress_cost_coverage",
    "drawdown",
    "cost_burden",
    "oos_lower_bound",
    "dsr",
    "pbo",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _git_commit() -> str:
    return subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_worktree_clean() -> bool:
    return not subprocess.run(
        [
            "git",
            "-C",
            str(PROJECT_ROOT),
            "status",
            "--porcelain",
            "--",
            *SOURCE_PATHS,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commits_have_equivalent_source(left: str, right: str) -> bool:
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (left, right)):
        return False
    return subprocess.run(
        [
            "git",
            "-C",
            str(PROJECT_ROOT),
            "diff",
            "--quiet",
            left,
            right,
            "--",
            *SOURCE_PATHS,
        ],
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed <= datetime.now(tz=UTC)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _evidence_path(input_path: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    return path if path.is_absolute() else input_path.parent / path


def _load_bound_manifest(
    input_path: Path,
    *,
    path_value: object,
    sha_value: object,
) -> tuple[dict[str, object] | None, str | None]:
    path = _evidence_path(input_path, path_value)
    if path is None or not path.is_file():
        return None, "MANIFEST_FILE_NOT_FOUND"
    if not _valid_sha256(sha_value) or _sha256(path) != sha_value:
        return None, "MANIFEST_SHA256_MISMATCH"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, "MANIFEST_INVALID_JSON"
    if not isinstance(payload, dict):
        return None, "MANIFEST_ROOT_NOT_OBJECT"
    return payload, None


def _record_timestamp(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    return None


def _artifact_records(
    path: Path,
    *,
    artifact_format: object,
    collection_key: str,
) -> tuple[list[Mapping[str, object]] | None, str | None]:
    if artifact_format == "JSONL":
        records: list[Mapping[str, object]] = []
        try:
            with path.open(encoding="utf-8") as source:
                for raw_line in source:
                    if not raw_line.strip():
                        continue
                    row = json.loads(raw_line)
                    if not isinstance(row, Mapping):
                        return None, "ARTIFACT_ROW_NOT_OBJECT"
                    records.append(row)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None, "ARTIFACT_INVALID_JSONL"
        return records, None
    if artifact_format == "JSON":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None, "ARTIFACT_INVALID_JSON"
        raw_records = (
            payload
            if isinstance(payload, list)
            else payload.get(collection_key)
            if isinstance(payload, Mapping)
            else None
        )
        if not isinstance(raw_records, list) or not all(
            isinstance(row, Mapping) for row in raw_records
        ):
            return None, "ARTIFACT_RECORDS_NOT_ARRAY"
        return list(raw_records), None
    if artifact_format == "CSV":
        try:
            with path.open(encoding="utf-8", newline="") as source:
                return list(csv.DictReader(source)), None
        except (csv.Error, OSError, UnicodeDecodeError):
            return None, "ARTIFACT_INVALID_CSV"
    return None, "ARTIFACT_FORMAT_UNSUPPORTED"


def _validate_public_market_record(
    row: Mapping[str, object],
    *,
    kind: str,
) -> tuple[str | None, str | None, str | None, list[str]]:
    errors: list[str] = []
    source = row.get("source")
    symbol = row.get("symbol")
    identity_field = "record_id" if kind == "DATASET" else "event_id"
    identity = row.get(identity_field)
    if row.get("source_type") != "PUBLIC_MARKET":
        errors.append("SOURCE_TYPE_NOT_PUBLIC_MARKET")
    if not isinstance(source, str) or not source.strip():
        errors.append("SOURCE_MISSING")
    if not isinstance(symbol, str) or re.fullmatch(r"[A-Z0-9]{5,30}", symbol) is None:
        errors.append("SYMBOL_INVALID")
    if not isinstance(identity, str) or not identity.strip():
        errors.append("IDENTITY_MISSING")
    if kind == "DATASET":
        if not isinstance(row.get("interval"), str) or not row.get("interval"):
            errors.append("INTERVAL_MISSING")
        open_price = _finite_number(row.get("open"))
        high_price = _finite_number(row.get("high"))
        low_price = _finite_number(row.get("low"))
        close_price = _finite_number(row.get("close"))
        volume = _finite_number(row.get("volume"))
        if any(
            value is None or value <= 0
            for value in (open_price, high_price, low_price, close_price)
        ):
            errors.append("OHLC_INVALID")
        if volume is None or volume < 0:
            errors.append("VOLUME_INVALID")
        if all(
            value is not None
            for value in (open_price, high_price, low_price, close_price)
        ):
            assert open_price is not None
            assert high_price is not None
            assert low_price is not None
            assert close_price is not None
            if high_price < max(open_price, low_price, close_price):
                errors.append("HIGH_INCONSISTENT")
            if low_price > min(open_price, high_price, close_price):
                errors.append("LOW_INCONSISTENT")
    else:
        event_type = row.get("event_type")
        sequence = row.get("sequence")
        if event_type not in {"TRADE", "BOOK", "DEPTH", "TICKER"}:
            errors.append("EVENT_TYPE_INVALID")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            errors.append("SEQUENCE_INVALID")
        if event_type == "TRADE":
            price = _finite_number(row.get("price"))
            quantity = _finite_number(row.get("quantity"))
            if price is None or price <= 0 or quantity is None or quantity <= 0:
                errors.append("TRADE_PRICE_QUANTITY_INVALID")
            if row.get("aggressor_side") not in {"BUY", "SELL"}:
                errors.append("TRADE_SIDE_INVALID")
        elif event_type in {"BOOK", "DEPTH", "TICKER"}:
            bid = _finite_number(row.get("bid"))
            ask = _finite_number(row.get("ask"))
            bid_quantity = _finite_number(row.get("bid_quantity"))
            ask_quantity = _finite_number(row.get("ask_quantity"))
            if (
                bid is None
                or ask is None
                or bid <= 0
                or ask < bid
                or bid_quantity is None
                or ask_quantity is None
                or bid_quantity < 0
                or ask_quantity < 0
            ):
                errors.append("BOOK_FIELDS_INVALID")
    return (
        source if isinstance(source, str) else None,
        symbol if isinstance(symbol, str) else None,
        identity if isinstance(identity, str) else None,
        errors,
    )


def _validate_data_manifest_artifacts(
    manifest: Mapping[str, object],
    *,
    manifest_path: Path,
    kind: str,
    count_field: str,
    expected_window: Mapping[str, object],
) -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        return [f"{kind}_ARTIFACTS_MISSING"], {
            "identities": set(),
            "symbols": set(),
            "sources": set(),
        }
    collection_key = "records" if kind == "DATASET" else "events"
    expected_timestamp_field = "open_ts_ms" if kind == "DATASET" else "event_ts_ms"
    expected_total = manifest.get(count_field)
    measured_total = 0
    measured_start: int | None = None
    measured_end: int | None = None
    seen_paths: set[Path] = set()
    seen_identities: set[str] = set()
    observed_symbols: set[str] = set()
    observed_sources: set[str] = set()
    observed_intervals: set[str] = set()
    observed_event_types: set[str] = set()
    for index, raw_artifact in enumerate(raw_artifacts):
        prefix = f"{kind}_ARTIFACT_{index}"
        if not isinstance(raw_artifact, Mapping):
            errors.append(f"{prefix}_NOT_OBJECT")
            continue
        artifact_path = _evidence_path(manifest_path, raw_artifact.get("path"))
        if artifact_path is None or not artifact_path.is_file():
            errors.append(f"{prefix}_FILE_NOT_FOUND")
            continue
        resolved_path = artifact_path.resolve()
        if resolved_path in seen_paths:
            errors.append(f"{prefix}_DUPLICATE_PATH")
            continue
        seen_paths.add(resolved_path)
        declared_sha = raw_artifact.get("sha256")
        if not _valid_sha256(declared_sha) or _sha256(artifact_path) != declared_sha:
            errors.append(f"{prefix}_SHA256_MISMATCH")
            continue
        records, record_error = _artifact_records(
            artifact_path,
            artifact_format=raw_artifact.get("format"),
            collection_key=collection_key,
        )
        if record_error is not None or records is None:
            errors.append(f"{prefix}_{record_error}")
            continue
        declared_count = raw_artifact.get(count_field)
        if (
            isinstance(declared_count, bool)
            or not isinstance(declared_count, int)
            or declared_count <= 0
            or declared_count != len(records)
        ):
            errors.append(f"{prefix}_COUNT_MISMATCH")
        if not records:
            errors.append(f"{prefix}_EMPTY")
            continue
        timestamp_field = raw_artifact.get("timestamp_field")
        if timestamp_field != expected_timestamp_field:
            errors.append(f"{prefix}_TIMESTAMP_FIELD_MISMATCH")
            continue
        for row_index, row in enumerate(records):
            source, symbol, identity, row_errors = _validate_public_market_record(
                row,
                kind=kind,
            )
            errors.extend(
                f"{prefix}_ROW_{row_index}_{error}" for error in row_errors
            )
            if source is not None:
                observed_sources.add(source)
            if symbol is not None:
                observed_symbols.add(symbol)
            if identity is not None:
                if identity in seen_identities:
                    errors.append(f"{prefix}_ROW_{row_index}_IDENTITY_DUPLICATE")
                seen_identities.add(identity)
            if kind == "DATASET" and isinstance(row.get("interval"), str):
                observed_intervals.add(str(row["interval"]))
            if kind == "EVENT_SET" and isinstance(row.get("event_type"), str):
                observed_event_types.add(str(row["event_type"]))
        timestamps = [_record_timestamp(row.get(timestamp_field)) for row in records]
        if any(timestamp is None for timestamp in timestamps):
            errors.append(f"{prefix}_TIMESTAMP_INVALID")
            continue
        valid_timestamps = [int(timestamp) for timestamp in timestamps if timestamp is not None]
        artifact_start = min(valid_timestamps)
        artifact_end = max(valid_timestamps)
        if raw_artifact.get("start_ts_ms") != artifact_start:
            errors.append(f"{prefix}_START_TS_MISMATCH")
        if raw_artifact.get("end_ts_ms") != artifact_end:
            errors.append(f"{prefix}_END_TS_MISMATCH")
        window_start = expected_window.get("start_ts_ms")
        window_end = expected_window.get("end_ts_ms")
        if (
            not isinstance(window_start, int)
            or not isinstance(window_end, int)
            or artifact_start < window_start
            or artifact_end > window_end
        ):
            errors.append(f"{prefix}_OUTSIDE_FIXED_WINDOW")
        measured_total += len(records)
        measured_start = (
            artifact_start
            if measured_start is None
            else min(measured_start, artifact_start)
        )
        measured_end = (
            artifact_end if measured_end is None else max(measured_end, artifact_end)
        )
    if expected_total != measured_total or measured_total <= 0:
        errors.append(f"{kind}_TOTAL_COUNT_MISMATCH")
    measured_window = (
        {"start_ts_ms": measured_start, "end_ts_ms": measured_end}
        if measured_start is not None and measured_end is not None
        else None
    )
    if manifest.get("observed_window") != measured_window:
        errors.append(f"{kind}_OBSERVED_WINDOW_MISMATCH")
    if manifest.get("symbols") != sorted(observed_symbols):
        errors.append(f"{kind}_SYMBOLS_MISMATCH")
    public_sources = manifest.get("public_sources")
    if not isinstance(public_sources, Mapping) or set(public_sources) != observed_sources:
        errors.append(f"{kind}_PUBLIC_SOURCES_MISMATCH")
    else:
        for source, url in public_sources.items():
            if (
                not isinstance(source, str)
                or not isinstance(url, str)
                or re.fullmatch(
                    r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[^?#]*)?",
                    url,
                )
                is None
            ):
                errors.append(f"{kind}_PUBLIC_SOURCE_URL_INVALID")
    if kind == "DATASET" and manifest.get("intervals") != sorted(observed_intervals):
        errors.append("DATASET_INTERVALS_MISMATCH")
    if kind == "EVENT_SET" and manifest.get("event_types") != sorted(
        observed_event_types
    ):
        errors.append("EVENT_SET_EVENT_TYPES_MISMATCH")
    return sorted(set(errors)), {
        "identities": seen_identities,
        "symbols": observed_symbols,
        "sources": observed_sources,
    }


def _fixed_input_lineage_sha256(binding: Mapping[str, object]) -> str:
    material = {
        key: binding.get(key)
        for key in (
            "run_id",
            "replay_id",
            "cost_model_version",
            "profile_ids",
            "window",
            "dataset_manifest_sha256",
            "event_set_manifest_sha256",
            "dataset_record_count",
            "event_set_event_count",
        )
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate_input_binding(
    payload: Mapping[str, object],
    *,
    input_path: Path,
    source_commit: str,
    source_worktree_clean: bool,
) -> tuple[dict[str, object], dict[str, object], list[str]]:
    errors: list[str] = []
    data_context: dict[str, object] = {
        "dataset_identities": set(),
        "event_set_identities": set(),
        "dataset_symbols": set(),
        "event_set_symbols": set(),
    }
    if payload.get("schema_version") != 1 or payload.get("schema") != FIXED_INPUT_SCHEMA:
        errors.append("FIXED_INPUT_SCHEMA_MISMATCH")
    if not _valid_timestamp(payload.get("generated_ts_utc")):
        errors.append("FIXED_INPUT_TIMESTAMP_INVALID")
    declared_commit = payload.get("source_commit")
    if not isinstance(declared_commit, str) or not _commits_have_equivalent_source(
        declared_commit,
        source_commit,
    ):
        errors.append("FIXED_INPUT_SOURCE_COMMIT_NOT_EQUIVALENT")
    if payload.get("source_worktree_clean_at_measurement") is not True:
        errors.append("FIXED_INPUT_SOURCE_NOT_CLEAN_AT_MEASUREMENT")
    if not source_worktree_clean:
        errors.append("CURRENT_SOURCE_WORKTREE_NOT_CLEAN")

    run_id = payload.get("run_id")
    replay_id = payload.get("replay_id")
    cost_model_version = payload.get("cost_model_version")
    if not isinstance(run_id, str) or not run_id.strip():
        errors.append("FIXED_INPUT_RUN_ID_MISSING")
    if not isinstance(replay_id, str) or not replay_id.strip():
        errors.append("FIXED_INPUT_REPLAY_ID_MISSING")
    if not isinstance(cost_model_version, str) or not cost_model_version.strip():
        errors.append("FIXED_INPUT_COST_MODEL_VERSION_MISSING")
    if payload.get("profile_ids") != ["BASE", "STRESS"]:
        errors.append("FIXED_INPUT_PROFILE_IDS_MISMATCH")

    window = payload.get("window")
    if not isinstance(window, Mapping):
        errors.append("FIXED_INPUT_WINDOW_MISSING")
        window = {}
    start_ts_ms = window.get("start_ts_ms")
    end_ts_ms = window.get("end_ts_ms")
    if (
        isinstance(start_ts_ms, bool)
        or not isinstance(start_ts_ms, int)
        or isinstance(end_ts_ms, bool)
        or not isinstance(end_ts_ms, int)
        or start_ts_ms < 0
        or end_ts_ms <= start_ts_ms
    ):
        errors.append("FIXED_INPUT_WINDOW_INVALID")

    manifests: dict[str, dict[str, object]] = {}
    for kind, count_field, declared_kind in (
        ("dataset", "record_count", "DATASET"),
        ("event_set", "event_count", "EVENT_SET"),
    ):
        manifest, error = _load_bound_manifest(
            input_path,
            path_value=payload.get(f"{kind}_manifest_path"),
            sha_value=payload.get(f"{kind}_manifest_sha256"),
        )
        if error is not None or manifest is None:
            errors.append(f"{kind.upper()}_{error}")
            continue
        manifests[kind] = manifest
        if (
            manifest.get("schema_version") != 1
            or manifest.get("schema") != DATA_MANIFEST_SCHEMA
            or manifest.get("kind") != declared_kind
        ):
            errors.append(f"{kind.upper()}_MANIFEST_SCHEMA_MISMATCH")
        count = manifest.get(count_field)
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            errors.append(f"{kind.upper()}_MANIFEST_EMPTY")
        for field, expected in (
            ("run_id", run_id),
            ("replay_id", replay_id),
            ("window", dict(window)),
        ):
            if manifest.get(field) != expected:
                errors.append(f"{kind.upper()}_MANIFEST_{field.upper()}_MISMATCH")
        manifest_path = _evidence_path(input_path, payload.get(f"{kind}_manifest_path"))
        if manifest_path is None:
            errors.append(f"{kind.upper()}_MANIFEST_FILE_NOT_FOUND")
        else:
            artifact_errors, artifact_context = _validate_data_manifest_artifacts(
                manifest,
                manifest_path=manifest_path,
                kind=declared_kind,
                count_field=count_field,
                expected_window=window,
            )
            errors.extend(artifact_errors)
            identity_key = (
                "dataset_identities"
                if declared_kind == "DATASET"
                else "event_set_identities"
            )
            data_context[identity_key] = artifact_context["identities"]
            symbol_key = (
                "dataset_symbols"
                if declared_kind == "DATASET"
                else "event_set_symbols"
            )
            data_context[symbol_key] = artifact_context["symbols"]

    if data_context["dataset_symbols"] != data_context["event_set_symbols"]:
        errors.append("FIXED_INPUT_DATA_EVENT_SYMBOLS_MISMATCH")

    binding = {
        "run_id": run_id,
        "replay_id": replay_id,
        "cost_model_version": cost_model_version,
        "profile_ids": ["BASE", "STRESS"],
        "window": dict(window),
        "dataset_manifest_sha256": payload.get("dataset_manifest_sha256"),
        "event_set_manifest_sha256": payload.get("event_set_manifest_sha256"),
        "dataset_record_count": manifests.get("dataset", {}).get("record_count"),
        "event_set_event_count": manifests.get("event_set", {}).get("event_count"),
        "source_commit": declared_commit,
    }
    binding["sample_lineage_sha256"] = _fixed_input_lineage_sha256(binding)
    return binding, data_context, sorted(set(errors))


def _validated_measurement_metrics(
    payload: Mapping[str, object],
    *,
    role: str,
) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    raw_metrics = payload.get("metrics")
    if not isinstance(raw_metrics, Mapping):
        return {}, [f"{role}_MEASUREMENT_METRICS_MISSING"]
    numeric_fields = (
        BASELINE_MEASUREMENT_NUMERIC_FIELDS
        if role == "BASELINE"
        else CANDIDATE_MEASUREMENT_NUMERIC_FIELDS
    )
    normalized: dict[str, object] = {}
    for field in sorted(numeric_fields):
        value = _finite_number(raw_metrics.get(field))
        if value is None:
            errors.append(f"{role}_MEASUREMENT_{field.upper()}_INVALID")
        else:
            normalized[field] = value
    if role == "CANDIDATE":
        for sample_field in ("base_sample_size", "stress_sample_size"):
            sample_value = normalized.get(sample_field)
            if (
                not isinstance(sample_value, float)
                or sample_value <= 0
                or not sample_value.is_integer()
            ):
                errors.append(f"{role}_MEASUREMENT_{sample_field.upper()}_INVALID")
        for bounded_field in ("dsr", "pbo"):
            bounded_value = normalized.get(bounded_field)
            if not isinstance(bounded_value, float) or not 0 <= bounded_value <= 1:
                errors.append(f"{role}_MEASUREMENT_{bounded_field.upper()}_INVALID")
        operational_regression = raw_metrics.get("operational_regression")
        if not isinstance(operational_regression, bool):
            errors.append("CANDIDATE_MEASUREMENT_OPERATIONAL_REGRESSION_INVALID")
        else:
            normalized["operational_regression"] = operational_regression
    return normalized, sorted(set(errors))


def _finite_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = Decimal(str(value))
    return parsed if parsed.is_finite() else None


def _mean_decimal(values: list[Decimal]) -> Decimal:
    return sum(values, start=Decimal(0)) / Decimal(len(values))


def _raw_result_fingerprint(row: Mapping[str, object]) -> tuple[object, ...]:
    return (
        row["opportunity_id"],
        row["symbol"],
        row["side"],
        row["entry_ts_ms"],
        row["exit_ts_ms"],
        row["period_id"],
        row["dataset_record_id"],
        row["event_id"],
    )


def _validate_raw_result_row(
    raw: Mapping[str, object],
    *,
    role: str,
    measurement_id: object,
    strategy_ids: list[str],
    binding: Mapping[str, object],
    data_context: Mapping[str, object],
) -> tuple[dict[str, object] | None, list[str]]:
    errors: list[str] = []
    normalized: dict[str, object] = {}
    expected_values = {
        "run_id": binding.get("run_id"),
        "replay_id": binding.get("replay_id"),
        "measurement_id": measurement_id,
    }
    for field, expected in expected_values.items():
        if raw.get(field) != expected:
            errors.append(f"{field.upper()}_MISMATCH")
    strategy_id = raw.get("strategy_id")
    if strategy_id not in strategy_ids:
        errors.append("STRATEGY_ID_MISMATCH")
    for field in (
        "strategy_version",
        "opportunity_id",
        "period_id",
        "dataset_record_id",
        "event_id",
    ):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field.upper()}_INVALID")
        else:
            normalized[field] = value
    if raw.get("evaluation_scope") != "OOS":
        errors.append("EVALUATION_SCOPE_NOT_OOS")
    profile = raw.get("profile")
    if profile not in {"BASE", "STRESS"}:
        errors.append("PROFILE_INVALID")
    else:
        normalized["profile"] = profile
    symbol = raw.get("symbol")
    symbols = data_context.get("dataset_symbols")
    if (
        not isinstance(symbol, str)
        or not isinstance(symbols, set)
        or symbol not in symbols
    ):
        errors.append("SYMBOL_NOT_IN_FIXED_INPUT")
    else:
        normalized["symbol"] = symbol
    side = raw.get("side")
    if side not in {"LONG", "SHORT"}:
        errors.append("SIDE_INVALID")
    else:
        normalized["side"] = side
    dataset_identities = data_context.get("dataset_identities")
    event_identities = data_context.get("event_set_identities")
    if (
        not isinstance(dataset_identities, set)
        or raw.get("dataset_record_id") not in dataset_identities
    ):
        errors.append("DATASET_RECORD_ID_NOT_IN_FIXED_INPUT")
    if (
        not isinstance(event_identities, set)
        or raw.get("event_id") not in event_identities
    ):
        errors.append("EVENT_ID_NOT_IN_FIXED_INPUT")

    window = binding.get("window")
    entry_ts_ms = raw.get("entry_ts_ms")
    exit_ts_ms = raw.get("exit_ts_ms")
    window_start = window.get("start_ts_ms") if isinstance(window, Mapping) else None
    window_end = window.get("end_ts_ms") if isinstance(window, Mapping) else None
    if (
        isinstance(entry_ts_ms, bool)
        or not isinstance(entry_ts_ms, int)
        or isinstance(exit_ts_ms, bool)
        or not isinstance(exit_ts_ms, int)
        or not isinstance(window_start, int)
        or not isinstance(window_end, int)
        or entry_ts_ms < window_start
        or exit_ts_ms < entry_ts_ms
        or exit_ts_ms > window_end
    ):
        errors.append("TIMESTAMP_OUTSIDE_FIXED_WINDOW")
    else:
        normalized["entry_ts_ms"] = entry_ts_ms
        normalized["exit_ts_ms"] = exit_ts_ms

    numeric_fields = (
        "gross_pnl_usdt",
        *RAW_COST_FIELDS,
        "net_pnl_usdt",
        "initial_risk_usdt",
        "return_r",
        "gross_mfe_usdt",
    )
    numeric = {field: _finite_decimal(raw.get(field)) for field in numeric_fields}
    errors.extend(
        f"{field.upper()}_INVALID" for field, value in numeric.items() if value is None
    )
    if not errors:
        assert all(value is not None for value in numeric.values())
        values = {field: value for field, value in numeric.items() if value is not None}
        if any(values[field] < 0 for field in RAW_COST_FIELDS):
            errors.append("COST_NEGATIVE")
        if values["initial_risk_usdt"] <= 0:
            errors.append("INITIAL_RISK_NOT_POSITIVE")
        if values["gross_mfe_usdt"] < 0:
            errors.append("GROSS_MFE_NEGATIVE")
        total_cost = sum((values[field] for field in RAW_COST_FIELDS), Decimal(0))
        expected_net = values["gross_pnl_usdt"] - total_cost
        if values["net_pnl_usdt"] != expected_net:
            errors.append("NET_PNL_NOT_RECOMPUTABLE")
        expected_return = expected_net / values["initial_risk_usdt"]
        if values["return_r"] != expected_return:
            errors.append("RETURN_R_NOT_RECOMPUTABLE")
        normalized.update({field: float(value) for field, value in values.items()})
        normalized["total_cost_usdt"] = float(total_cost)
    normalized["run_id"] = raw.get("run_id")
    normalized["replay_id"] = raw.get("replay_id")
    normalized["measurement_id"] = raw.get("measurement_id")
    normalized["strategy_id"] = strategy_id
    normalized["strategy_version"] = raw.get("strategy_version")
    return (normalized if not errors else None), sorted(set(errors))


def _validate_cscv_splits(
    raw_splits: object,
    *,
    period_ids: set[str],
) -> tuple[list[dict[str, object]], list[str]]:
    if not isinstance(raw_splits, list) or len(raw_splits) < 4:
        return [], ["CSCV_SPLITS_INSUFFICIENT"]
    errors: list[str] = []
    normalized: list[dict[str, object]] = []
    seen_split_ids: set[str] = set()
    for index, raw_split in enumerate(raw_splits):
        prefix = f"CSCV_SPLIT_{index}"
        if not isinstance(raw_split, Mapping):
            errors.append(f"{prefix}_NOT_OBJECT")
            continue
        split_id = raw_split.get("split_id")
        in_periods = raw_split.get("in_sample_period_ids")
        out_periods = raw_split.get("out_of_sample_period_ids")
        if not isinstance(split_id, str) or not split_id.strip():
            errors.append(f"{prefix}_ID_INVALID")
            continue
        if split_id in seen_split_ids:
            errors.append(f"{prefix}_ID_DUPLICATE")
        seen_split_ids.add(split_id)
        if (
            not isinstance(in_periods, list)
            or not in_periods
            or not all(isinstance(value, str) for value in in_periods)
            or len(set(in_periods)) != len(in_periods)
            or not isinstance(out_periods, list)
            or not out_periods
            or not all(isinstance(value, str) for value in out_periods)
            or len(set(out_periods)) != len(out_periods)
        ):
            errors.append(f"{prefix}_PERIODS_INVALID")
            continue
        in_set = set(in_periods)
        out_set = set(out_periods)
        if in_set & out_set or in_set | out_set != period_ids:
            errors.append(f"{prefix}_PERIOD_PARTITION_INVALID")
            continue
        normalized.append(
            {
                "split_id": split_id,
                "in_sample_period_ids": sorted(in_set),
                "out_of_sample_period_ids": sorted(out_set),
            }
        )
    return normalized, sorted(set(errors))


def _validated_number(value: object) -> float:
    parsed = _finite_number(value)
    assert parsed is not None
    return parsed


def _validated_integer(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


def _profile_metrics(rows: Sequence[Mapping[str, object]]) -> dict[str, float]:
    returns = [_validated_number(row["return_r"]) for row in rows]
    costs = [_validated_number(row["total_cost_usdt"]) for row in rows]
    risks = [_validated_number(row["initial_risk_usdt"]) for row in rows]
    total_cost = sum(costs)
    coverage = (
        sum(_validated_number(row["gross_mfe_usdt"]) for row in rows) / total_cost
    )
    cumulative = 0.0
    peak = 0.0
    drawdown = 0.0
    for row in sorted(
        rows,
        key=lambda item: (
            _validated_integer(item["exit_ts_ms"]),
            str(item["opportunity_id"]),
        ),
    ):
        cumulative += _validated_number(row["net_pnl_usdt"])
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    standard_error = stdev(returns) / math.sqrt(len(returns)) if len(returns) > 1 else math.inf
    return {
        "sample_size": float(len(rows)),
        "expectancy": fmean(returns),
        "cost_coverage": coverage,
        "drawdown": drawdown,
        "cost_burden": fmean(cost / risk for cost, risk in zip(costs, risks, strict=True)),
        "oos_lower_bound": fmean(returns) - 1.959963984540054 * standard_error,
    }


def _validate_raw_measurement_results(
    payload: Mapping[str, object],
    *,
    role: str,
    measurement_id: object,
    strategy_ids: list[str],
    binding: Mapping[str, object],
    data_context: Mapping[str, object],
) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    if (
        payload.get("schema_version") != 1
        or payload.get("schema") != RAW_MEASUREMENT_SCHEMA
        or payload.get("kind") != "V6_RAW_MEASUREMENT_RESULTS"
    ):
        errors.append("RAW_RESULTS_SCHEMA_MISMATCH")
    for field, expected in (
        ("role", role),
        ("measurement_id", measurement_id),
        ("strategy_ids", strategy_ids),
    ):
        if payload.get(field) != expected:
            errors.append(f"RAW_RESULTS_{field.upper()}_MISMATCH")
    if not _valid_timestamp(payload.get("generated_ts_utc")):
        errors.append("RAW_RESULTS_TIMESTAMP_INVALID")
    if payload.get("source_worktree_clean_at_measurement") is not True:
        errors.append("RAW_RESULTS_SOURCE_NOT_CLEAN")
    for field in (
        "run_id",
        "replay_id",
        "cost_model_version",
        "profile_ids",
        "window",
        "dataset_manifest_sha256",
        "event_set_manifest_sha256",
        "dataset_record_count",
        "event_set_event_count",
        "source_commit",
        "sample_lineage_sha256",
    ):
        if payload.get(field) != binding.get(field):
            errors.append(f"RAW_RESULTS_{field.upper()}_MISMATCH")
    operational_checks = payload.get("operational_checks")
    if (
        not isinstance(operational_checks, Mapping)
        or set(operational_checks) != set(OPERATIONAL_CHECK_IDS)
        or any(operational_checks.get(check_id) is not True for check_id in OPERATIONAL_CHECK_IDS)
    ):
        errors.append("RAW_RESULTS_OPERATIONAL_CHECKS_INVALID")

    raw_results = payload.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        return {}, sorted(set(errors + ["RAW_RESULTS_MISSING"]))
    if payload.get("result_count") != len(raw_results):
        errors.append("RAW_RESULTS_COUNT_MISMATCH")
    rows: list[dict[str, object]] = []
    for index, raw_result in enumerate(raw_results):
        if not isinstance(raw_result, Mapping):
            errors.append(f"RAW_RESULT_{index}_NOT_OBJECT")
            continue
        row, row_errors = _validate_raw_result_row(
            raw_result,
            role=role,
            measurement_id=measurement_id,
            strategy_ids=strategy_ids,
            binding=binding,
            data_context=data_context,
        )
        errors.extend(f"RAW_RESULT_{index}_{error}" for error in row_errors)
        if row is not None:
            rows.append(row)

    by_profile: dict[str, dict[str, dict[str, object]]] = {"BASE": {}, "STRESS": {}}
    for row in rows:
        profile = str(row["profile"])
        opportunity_id = str(row["opportunity_id"])
        if opportunity_id in by_profile[profile]:
            errors.append(f"RAW_RESULTS_{profile}_OPPORTUNITY_DUPLICATE")
        by_profile[profile][opportunity_id] = row
    base_ids = set(by_profile["BASE"])
    stress_ids = set(by_profile["STRESS"])
    if not base_ids or base_ids != stress_ids:
        errors.append("RAW_RESULTS_PROFILE_OPPORTUNITIES_MISMATCH")
    for opportunity_id in sorted(base_ids & stress_ids):
        base = by_profile["BASE"][opportunity_id]
        stress = by_profile["STRESS"][opportunity_id]
        if _raw_result_fingerprint(base) != _raw_result_fingerprint(stress):
            errors.append("RAW_RESULTS_PROFILE_FINGERPRINT_MISMATCH")
        if _validated_number(stress["total_cost_usdt"]) < _validated_number(
            base["total_cost_usdt"]
        ):
            errors.append("RAW_RESULTS_STRESS_COST_BELOW_BASE")
    for profile, profile_rows in by_profile.items():
        dataset_refs = [str(row["dataset_record_id"]) for row in profile_rows.values()]
        event_refs = [str(row["event_id"]) for row in profile_rows.values()]
        if len(set(dataset_refs)) != len(dataset_refs):
            errors.append(f"RAW_RESULTS_{profile}_DATASET_REFERENCE_DUPLICATE")
        if len(set(event_refs)) != len(event_refs):
            errors.append(f"RAW_RESULTS_{profile}_EVENT_REFERENCE_DUPLICATE")

    period_ids = {str(row["period_id"]) for row in by_profile["STRESS"].values()}
    splits, split_errors = _validate_cscv_splits(
        payload.get("cscv_splits"),
        period_ids=period_ids,
    )
    errors.extend(split_errors)
    if errors:
        return {}, sorted(set(errors))

    base_rows = list(by_profile["BASE"].values())
    stress_rows = list(by_profile["STRESS"].values())
    base_metrics = _profile_metrics(base_rows)
    stress_metrics = _profile_metrics(stress_rows)
    derived: dict[str, object] = {
        "base_sample_size": base_metrics["sample_size"],
        "stress_sample_size": stress_metrics["sample_size"],
        "base_expectancy": base_metrics["expectancy"],
        "stress_expectancy": stress_metrics["expectancy"],
        "base_cost_coverage": base_metrics["cost_coverage"],
        "stress_cost_coverage": stress_metrics["cost_coverage"],
        "drawdown": max(base_metrics["drawdown"], stress_metrics["drawdown"]),
        "cost_burden": stress_metrics["cost_burden"],
        "oos_lower_bound": stress_metrics["oos_lower_bound"],
        "operational_regression": False,
        "_rows_by_profile": by_profile,
        "_cscv_splits": splits,
    }
    return derived, []


def _validate_measurement_artifact(
    payload: Mapping[str, object],
    *,
    measurement_path: Path,
    role: str,
    measurement_id: object,
    strategy_ids: list[str],
    binding: Mapping[str, object],
    data_context: Mapping[str, object],
) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []
    if (
        payload.get("schema_version") != 1
        or payload.get("schema") != MEASUREMENT_SCHEMA
        or payload.get("kind") != "V6_STRATEGY_MEASUREMENT"
    ):
        errors.append(f"{role}_MEASUREMENT_SCHEMA_MISMATCH")
    if payload.get("role") != role:
        errors.append(f"{role}_MEASUREMENT_ROLE_MISMATCH")
    if payload.get("measurement_id") != measurement_id:
        errors.append(f"{role}_MEASUREMENT_ID_MISMATCH")
    if payload.get("strategy_ids") != strategy_ids:
        errors.append(f"{role}_MEASUREMENT_STRATEGY_IDS_MISMATCH")
    if not _valid_timestamp(payload.get("generated_ts_utc")):
        errors.append(f"{role}_MEASUREMENT_TIMESTAMP_INVALID")
    if payload.get("source_worktree_clean_at_measurement") is not True:
        errors.append(f"{role}_MEASUREMENT_SOURCE_NOT_CLEAN")
    for field in (
        "run_id",
        "replay_id",
        "cost_model_version",
        "profile_ids",
        "window",
        "dataset_manifest_sha256",
        "event_set_manifest_sha256",
        "dataset_record_count",
        "event_set_event_count",
        "source_commit",
        "sample_lineage_sha256",
    ):
        if payload.get(field) != binding.get(field):
            errors.append(f"{role}_MEASUREMENT_{field.upper()}_MISMATCH")
    metrics, metric_errors = _validated_measurement_metrics(payload, role=role)
    errors.extend(metric_errors)
    raw_results, raw_results_error = _load_bound_manifest(
        measurement_path,
        path_value=payload.get("raw_results_path"),
        sha_value=payload.get("raw_results_sha256"),
    )
    if raw_results_error is not None or raw_results is None:
        errors.append(f"{role}_RAW_RESULTS_{raw_results_error}")
        return {}, sorted(set(errors))
    derived, raw_errors = _validate_raw_measurement_results(
        raw_results,
        role=role,
        measurement_id=measurement_id,
        strategy_ids=strategy_ids,
        binding=binding,
        data_context=data_context,
    )
    errors.extend(f"{role}_{error}" for error in raw_errors)
    for field, declared_value in metrics.items():
        if field in {"dsr", "pbo"}:
            continue
        if field not in derived or not _comparison_value_matches(
            declared_value,
            derived[field],
        ):
            errors.append(f"{role}_MEASUREMENT_{field.upper()}_NOT_RECOMPUTABLE")
    if role == "CANDIDATE":
        derived["_declared_dsr"] = metrics.get("dsr")
        derived["_declared_pbo"] = metrics.get("pbo")
    return derived, sorted(set(errors))


def _paired_deflated_confidence(
    baseline_returns: list[float],
    candidate_returns: list[float],
) -> float:
    """Return a deterministic one-sided confidence from paired OOS return deltas."""
    deltas = [
        candidate - baseline
        for baseline, candidate in zip(
            baseline_returns,
            candidate_returns,
            strict=True,
        )
    ]
    mean_delta = fmean(deltas)
    if len(deltas) < 2:
        return 0.0
    delta_stdev = stdev(deltas)
    if delta_stdev == 0:
        return 1.0 if mean_delta > 0 else 0.0
    z_score = mean_delta / (delta_stdev / math.sqrt(len(deltas)))
    return NormalDist().cdf(z_score)


def _period_return_mean(
    rows: Mapping[str, Mapping[str, object]],
    period_ids: set[str],
) -> float | None:
    values = [
        _validated_number(row["return_r"])
        for row in rows.values()
        if row["period_id"] in period_ids
    ]
    return fmean(values) if values else None


def _cscv_pbo(
    baseline_rows: Mapping[str, Mapping[str, object]],
    candidate_rows: Mapping[str, Mapping[str, object]],
    splits: list[Mapping[str, object]],
) -> float | None:
    underperformed = 0
    for split in splits:
        raw_in = split.get("in_sample_period_ids")
        raw_out = split.get("out_of_sample_period_ids")
        if not isinstance(raw_in, list) or not isinstance(raw_out, list):
            return None
        in_periods = {str(value) for value in raw_in}
        out_periods = {str(value) for value in raw_out}
        baseline_in = _period_return_mean(baseline_rows, in_periods)
        candidate_in = _period_return_mean(candidate_rows, in_periods)
        baseline_out = _period_return_mean(baseline_rows, out_periods)
        candidate_out = _period_return_mean(candidate_rows, out_periods)
        if any(
            value is None
            for value in (baseline_in, candidate_in, baseline_out, candidate_out)
        ):
            return None
        assert baseline_in is not None
        assert candidate_in is not None
        assert baseline_out is not None
        assert candidate_out is not None
        if baseline_in == candidate_in:
            return None
        selected_candidate = candidate_in > baseline_in
        if (
            selected_candidate
            and candidate_out <= baseline_out
            or not selected_candidate
            and baseline_out <= candidate_out
        ):
            underperformed += 1
    return underperformed / len(splits) if splits else None


def _comparison_from_measurements(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> tuple[dict[str, object], list[str]]:
    errors: list[str] = []

    def difference(field: str) -> float:
        return float(Decimal(str(candidate[field])) - Decimal(str(baseline[field])))

    baseline_profiles = baseline.get("_rows_by_profile")
    candidate_profiles = candidate.get("_rows_by_profile")
    baseline_splits = baseline.get("_cscv_splits")
    candidate_splits = candidate.get("_cscv_splits")
    if (
        not isinstance(baseline_profiles, Mapping)
        or not isinstance(candidate_profiles, Mapping)
        or not isinstance(baseline_splits, list)
        or baseline_splits != candidate_splits
    ):
        return {}, ["ROW_RAW_MEASUREMENT_STRUCTURE_MISMATCH"]
    baseline_stress = baseline_profiles.get("STRESS")
    candidate_stress = candidate_profiles.get("STRESS")
    if not isinstance(baseline_stress, Mapping) or not isinstance(
        candidate_stress,
        Mapping,
    ):
        return {}, ["ROW_RAW_STRESS_RESULTS_MISSING"]
    if set(baseline_stress) != set(candidate_stress):
        return {}, ["ROW_RAW_OPPORTUNITY_SET_MISMATCH"]
    opportunity_ids = sorted(str(value) for value in baseline_stress)
    for profile in ("BASE", "STRESS"):
        baseline_rows = baseline_profiles.get(profile)
        candidate_rows = candidate_profiles.get(profile)
        if not isinstance(baseline_rows, Mapping) or not isinstance(
            candidate_rows,
            Mapping,
        ):
            errors.append(f"ROW_RAW_{profile}_RESULTS_MISSING")
            continue
        if set(baseline_rows) != set(candidate_rows):
            errors.append(f"ROW_RAW_{profile}_OPPORTUNITY_SET_MISMATCH")
            continue
        for opportunity_id in baseline_rows:
            baseline_row = baseline_rows[opportunity_id]
            candidate_row = candidate_rows[opportunity_id]
            if (
                not isinstance(baseline_row, Mapping)
                or not isinstance(candidate_row, Mapping)
                or _raw_result_fingerprint(baseline_row)
                != _raw_result_fingerprint(candidate_row)
            ):
                errors.append(f"ROW_RAW_{profile}_FINGERPRINT_MISMATCH")
                break
    baseline_returns = [
        float(baseline_stress[opportunity_id]["return_r"])
        for opportunity_id in opportunity_ids
    ]
    candidate_returns = [
        float(candidate_stress[opportunity_id]["return_r"])
        for opportunity_id in opportunity_ids
    ]
    dsr = _paired_deflated_confidence(baseline_returns, candidate_returns)
    pbo = _cscv_pbo(baseline_stress, candidate_stress, baseline_splits)
    if pbo is None:
        errors.append("ROW_RAW_PBO_NOT_RECOMPUTABLE")
        pbo = 1.0
    if not _comparison_value_matches(candidate.get("_declared_dsr"), dsr):
        errors.append("ROW_CANDIDATE_MEASUREMENT_DSR_NOT_RECOMPUTABLE")
    if not _comparison_value_matches(candidate.get("_declared_pbo"), pbo):
        errors.append("ROW_CANDIDATE_MEASUREMENT_PBO_NOT_RECOMPUTABLE")
    comparison = {
        "same_frozen_input": True,
        "base_sample_size": candidate["base_sample_size"],
        "stress_sample_size": candidate["stress_sample_size"],
        "base_expectancy": candidate["base_expectancy"],
        "stress_expectancy": candidate["stress_expectancy"],
        "base_cost_coverage": candidate["base_cost_coverage"],
        "stress_cost_coverage": candidate["stress_cost_coverage"],
        "base_expectancy_delta": difference("base_expectancy"),
        "stress_expectancy_delta": difference("stress_expectancy"),
        "drawdown_delta": difference("drawdown"),
        "cost_burden_delta": difference("cost_burden"),
        "oos_lower_bound": candidate["oos_lower_bound"],
        "dsr": dsr,
        "pbo": pbo,
        "operational_regression": candidate["operational_regression"],
    }
    return comparison, sorted(set(errors))


def _comparison_value_matches(actual: object, expected: object) -> bool:
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    actual_number = _finite_number(actual)
    expected_number = _finite_number(expected)
    if actual_number is not None and expected_number is not None:
        return Decimal(str(actual_number)) == Decimal(str(expected_number))
    return actual == expected


def _validate_row_lineage(
    variant: V6VariantSpec,
    raw: Mapping[str, object],
    *,
    input_path: Path,
    binding: Mapping[str, object],
    data_context: Mapping[str, object],
) -> list[str]:
    errors: list[str] = []
    required = {
        "baseline_strategy_ids",
        "baseline_measurement_id",
        "candidate_measurement_id",
        "sample_lineage_sha256",
        "comparison_manifest_path",
        "comparison_manifest_sha256",
    }
    missing = sorted(required - raw.keys())
    if missing:
        return [f"ROW_LINEAGE_FIELDS_MISSING:{','.join(missing)}"]
    if raw.get("baseline_strategy_ids") != list(variant.baseline_strategy_ids):
        errors.append("ROW_BASELINE_STRATEGY_IDS_MISMATCH")
    baseline_measurement_id = raw.get("baseline_measurement_id")
    candidate_measurement_id = raw.get("candidate_measurement_id")
    if not isinstance(baseline_measurement_id, str) or not baseline_measurement_id.strip():
        errors.append("ROW_BASELINE_MEASUREMENT_ID_MISSING")
    if not isinstance(candidate_measurement_id, str) or not candidate_measurement_id.strip():
        errors.append("ROW_CANDIDATE_MEASUREMENT_ID_MISSING")
    if baseline_measurement_id == candidate_measurement_id:
        errors.append("ROW_MEASUREMENT_IDS_NOT_DISTINCT")
    if not _valid_sha256(raw.get("sample_lineage_sha256")):
        errors.append("ROW_SAMPLE_LINEAGE_SHA256_INVALID")
    if raw.get("sample_lineage_sha256") != binding.get("sample_lineage_sha256"):
        errors.append("ROW_SAMPLE_LINEAGE_SHA256_MISMATCH")

    comparison_manifest_path = _evidence_path(
        input_path, raw.get("comparison_manifest_path")
    )
    manifest, manifest_error = _load_bound_manifest(
        input_path,
        path_value=raw.get("comparison_manifest_path"),
        sha_value=raw.get("comparison_manifest_sha256"),
    )
    if (
        manifest_error is not None
        or manifest is None
        or comparison_manifest_path is None
    ):
        errors.append(f"ROW_COMPARISON_{manifest_error}")
        return sorted(set(errors))
    expected_manifest = {
        "schema_version": 1,
        "schema": COMPARISON_MANIFEST_SCHEMA,
        "kind": "V2_V3_COMPARISON",
        "strategy_id": variant.strategy_id,
        "baseline_strategy_ids": list(variant.baseline_strategy_ids),
        "baseline_measurement_id": baseline_measurement_id,
        "candidate_measurement_id": candidate_measurement_id,
        **dict(binding),
    }
    for field, expected in expected_manifest.items():
        if manifest.get(field) != expected:
            errors.append(f"ROW_COMPARISON_MANIFEST_{field.upper()}_MISMATCH")
    if not _valid_timestamp(manifest.get("generated_ts_utc")):
        errors.append("ROW_COMPARISON_MANIFEST_TIMESTAMP_INVALID")
    if manifest.get("source_worktree_clean_at_measurement") is not True:
        errors.append("ROW_COMPARISON_MANIFEST_SOURCE_NOT_CLEAN")

    measurements: dict[str, dict[str, object]] = {}
    for role, measurement_id, strategy_ids in (
        ("BASELINE", baseline_measurement_id, list(variant.baseline_strategy_ids)),
        ("CANDIDATE", candidate_measurement_id, [variant.strategy_id]),
    ):
        key_prefix = role.lower()
        measurement_path = _evidence_path(
            comparison_manifest_path,
            manifest.get(f"{key_prefix}_measurement_path"),
        )
        measurement, measurement_error = _load_bound_manifest(
            comparison_manifest_path,
            path_value=manifest.get(f"{key_prefix}_measurement_path"),
            sha_value=manifest.get(f"{key_prefix}_measurement_sha256"),
        )
        if (
            measurement_error is not None
            or measurement is None
            or measurement_path is None
        ):
            errors.append(f"ROW_{role}_MEASUREMENT_{measurement_error}")
            continue
        metrics, measurement_errors = _validate_measurement_artifact(
            measurement,
            measurement_path=measurement_path,
            role=role,
            measurement_id=measurement_id,
            strategy_ids=strategy_ids,
            binding=binding,
            data_context=data_context,
        )
        errors.extend(f"ROW_{error}" for error in measurement_errors)
        if not measurement_errors:
            measurements[role] = metrics

    manifest_comparison = manifest.get("comparison")
    if not isinstance(manifest_comparison, Mapping):
        errors.append("ROW_COMPARISON_MANIFEST_METRICS_MISSING")
    if {"BASELINE", "CANDIDATE"} <= measurements.keys():
        derived_comparison, comparison_errors = _comparison_from_measurements(
            measurements["BASELINE"], measurements["CANDIDATE"]
        )
        errors.extend(comparison_errors)
        if comparison_errors:
            return sorted(set(errors))
        for sample_field, count_field in (
            ("base_sample_size", "dataset_record_count"),
            ("base_sample_size", "event_set_event_count"),
            ("stress_sample_size", "dataset_record_count"),
            ("stress_sample_size", "event_set_event_count"),
        ):
            available_count = binding.get(count_field)
            sample_value = _finite_number(derived_comparison[sample_field])
            if (
                isinstance(available_count, bool)
                or not isinstance(available_count, int)
                or sample_value is None
                or sample_value > available_count
            ):
                errors.append(
                    f"ROW_{sample_field.upper()}_EXCEEDS_{count_field.upper()}"
                )
        if any(
            not _comparison_value_matches(raw.get(field), derived_comparison[field])
            for field in COMPARISON_REQUIRED_FIELDS
        ):
            errors.append("ROW_METRICS_NOT_DERIVED_FROM_MEASUREMENTS")
        if isinstance(manifest_comparison, Mapping) and any(
            not _comparison_value_matches(
                manifest_comparison.get(field), derived_comparison[field]
            )
            for field in COMPARISON_REQUIRED_FIELDS
        ):
            errors.append("ROW_COMPARISON_MANIFEST_METRICS_MISMATCH")
        # Raw outcome tables can prove arithmetic consistency, not that strategy rules
        # actually produced their entries/fills. Promotion stays fail-closed until the
        # repository replay engine independently emits and replays those decisions.
        errors.append("ROW_REPLAY_OUTCOMES_NOT_INDEPENDENTLY_RECOMPUTED")
    return sorted(set(errors))


def _not_proven_row(variant: V6VariantSpec) -> dict[str, Any]:
    return {
        "strategy_id": variant.strategy_id,
        "family_id": variant.family_id,
        "baseline_strategy_ids": list(variant.baseline_strategy_ids),
        "comparison_status": "NOT_PROVEN",
        "data_status": "NOT_RUN_NO_FIXED_INPUT_RESULT",
        "base_sample_size": None,
        "stress_sample_size": None,
        "base_expectancy": None,
        "stress_expectancy": None,
        "base_cost_coverage": None,
        "stress_cost_coverage": None,
        "base_expectancy_delta": None,
        "stress_expectancy_delta": None,
        "drawdown_delta": None,
        "cost_burden_delta": None,
        "oos_lower_bound": None,
        "dsr": None,
        "pbo": None,
        "base_sample_size_minimum": variant.base_sample_size_minimum,
        "stress_sample_size_minimum": variant.stress_sample_size_minimum,
        "base_cost_coverage_minimum": variant.base_cost_coverage_minimum,
        "stress_cost_coverage_minimum": variant.stress_cost_coverage_minimum,
        "promotion_eligible": False,
        "promotion_performed": False,
    }


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _evaluated_row(
    variant: V6VariantSpec,
    raw: Mapping[str, object],
    *,
    lineage_errors: list[str] | None = None,
) -> dict[str, Any]:
    effective_lineage_errors = (
        ["FIXED_INPUT_LINEAGE_NOT_VALIDATED"]
        if lineage_errors is None
        else lineage_errors
    )
    if effective_lineage_errors:
        row = _not_proven_row(variant)
        row["data_status"] = "NOT_PROVEN_INPUT_LINEAGE"
        row["lineage_errors"] = effective_lineage_errors
        return row
    missing = sorted(COMPARISON_REQUIRED_FIELDS - raw.keys())
    if missing:
        row = _not_proven_row(variant)
        row["data_status"] = "NOT_PROVEN_MISSING_FIELDS"
        row["missing_fields"] = missing
        return row
    normalized = {
        field: _finite_number(raw[field]) for field in COMPARISON_NUMERIC_FIELDS
    }
    invalid = sorted(field for field, value in normalized.items() if value is None)
    if not isinstance(raw["same_frozen_input"], bool):
        invalid.append("same_frozen_input")
    if not isinstance(raw["operational_regression"], bool):
        invalid.append("operational_regression")
    dsr = normalized["dsr"]
    pbo = normalized["pbo"]
    if dsr is not None and not 0 <= dsr <= 1:
        invalid.append("dsr")
    if pbo is not None and not 0 <= pbo <= 1:
        invalid.append("pbo")
    for sample_field in ("base_sample_size", "stress_sample_size"):
        sample = normalized[sample_field]
        if sample is not None and (sample < 0 or not sample.is_integer()):
            invalid.append(sample_field)
    if invalid:
        row = _not_proven_row(variant)
        row["data_status"] = "NOT_PROVEN_INVALID_FIELDS"
        row["invalid_fields"] = sorted(set(invalid))
        return row
    numbers = {field: float(value) for field, value in normalized.items() if value is not None}
    eligible = (
        raw["same_frozen_input"] is True
        and numbers["base_sample_size"] >= variant.base_sample_size_minimum
        and numbers["stress_sample_size"] >= variant.stress_sample_size_minimum
        and numbers["base_expectancy"] > 0
        and numbers["stress_expectancy"] > 0
        and numbers["base_cost_coverage"] >= variant.base_cost_coverage_minimum
        and numbers["stress_cost_coverage"] >= variant.stress_cost_coverage_minimum
        and numbers["base_expectancy_delta"] > 0
        and numbers["stress_expectancy_delta"] > 0
        and numbers["drawdown_delta"] <= 0
        and numbers["cost_burden_delta"] < 0
        and numbers["oos_lower_bound"] > 0
        and numbers["dsr"] >= 0.95
        and numbers["pbo"] <= 0.20
        and raw["operational_regression"] is False
    )
    return {
        "strategy_id": variant.strategy_id,
        "family_id": variant.family_id,
        "baseline_strategy_ids": list(variant.baseline_strategy_ids),
        "base_sample_size_minimum": variant.base_sample_size_minimum,
        "stress_sample_size_minimum": variant.stress_sample_size_minimum,
        "base_cost_coverage_minimum": variant.base_cost_coverage_minimum,
        "stress_cost_coverage_minimum": variant.stress_cost_coverage_minimum,
        "comparison_status": "EVIDENCE_GATE_PASS" if eligible else "EVIDENCE_GATE_FAIL",
        "data_status": "FIXED_INPUT_RESULT_PROVIDED",
        "same_frozen_input": raw["same_frozen_input"],
        **{key: numbers[key] for key in sorted(COMPARISON_NUMERIC_FIELDS)},
        "operational_regression": raw["operational_regression"],
        "promotion_eligible": eligible,
        "promotion_performed": False,
    }


def build_report(input_path: Path) -> dict[str, Any]:
    commit = _git_commit()
    source_worktree_clean = _source_worktree_clean()
    variants = v6_preregistered_variants()
    input_rows: dict[str, Mapping[str, object]] = {}
    duplicate_strategy_ids: list[str] = []
    unexpected_strategy_ids: list[str] = []
    input_binding: dict[str, object] = {}
    input_data_context: dict[str, object] = {}
    input_binding_errors: list[str] = []
    input_status = "NOT_RUN_NO_FIXED_INPUT_RESULT"
    if input_path.exists():
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("fixed input evidence root는 object여야 합니다.")
        input_binding, input_data_context, input_binding_errors = _validate_input_binding(
            payload,
            input_path=input_path,
            source_commit=commit,
            source_worktree_clean=source_worktree_clean,
        )
        rows = payload.get("comparisons", [])
        if not isinstance(rows, list):
            raise ValueError("comparisons는 배열이어야 합니다.")
        parsed_rows = [
            (str(row["strategy_id"]), row)
            for row in rows
            if isinstance(row, Mapping) and "strategy_id" in row
        ]
        counts: dict[str, int] = {}
        for strategy_id, _ in parsed_rows:
            counts[strategy_id] = counts.get(strategy_id, 0) + 1
        duplicate_strategy_ids = sorted(
            strategy_id for strategy_id, count in counts.items() if count > 1
        )
        input_rows = {
            strategy_id: row
            for strategy_id, row in parsed_rows
            if counts[strategy_id] == 1
        }
        expected_ids = {variant.strategy_id for variant in variants}
        unexpected_strategy_ids = sorted(set(counts) - expected_ids)
        input_status = (
            "NOT_PROVEN_AMBIGUOUS_FIXED_INPUT_ROWS"
            if duplicate_strategy_ids or unexpected_strategy_ids
            else "NOT_PROVEN_FIXED_INPUT_BINDING"
            if input_binding_errors
            else "FIXED_INPUT_RESULT_PROVIDED"
        )
    comparisons = [
        (
            _not_proven_row(variant)
            | {
                "data_status": "NOT_PROVEN_DUPLICATE_STRATEGY_ID",
                "duplicate_strategy_id": variant.strategy_id,
            }
        )
        if variant.strategy_id in duplicate_strategy_ids
        else _evaluated_row(
            variant,
            input_rows[variant.strategy_id],
            lineage_errors=(
                input_binding_errors
                + _validate_row_lineage(
                    variant,
                    input_rows[variant.strategy_id],
                    input_path=input_path,
                    binding=input_binding,
                    data_context=input_data_context,
                )
            ),
        )
        if variant.strategy_id in input_rows
        else _not_proven_row(variant)
        for variant in variants
    ]
    all_proven = (
        not duplicate_strategy_ids
        and not unexpected_strategy_ids
        and not input_binding_errors
        and source_worktree_clean
        and all(row["promotion_eligible"] for row in comparisons)
    )
    return {
        "schema_version": 1,
        "generated_ts_utc": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "status": "EVIDENCE_GATE_PASS_NO_PROMOTION" if all_proven else "NOT_PROVEN",
        "input_status": input_status,
        "duplicate_strategy_ids": duplicate_strategy_ids,
        "unexpected_strategy_ids": unexpected_strategy_ids,
        "input_binding": input_binding,
        "input_binding_errors": input_binding_errors,
        "input_path": str(input_path),
        "source_commit": commit,
        "source_worktree_clean_at_measurement": source_worktree_clean,
        "preregistration": v6_preregistration_manifest(source_commit=commit),
        "comparisons": comparisons,
        "all_candidates_promotion_eligible": all_proven,
        "promotion_performed": False,
        "registry_changed": False,
        "live_shadow_changed": False,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "wallet_enabled": False,
        "profitability": "NOT_PROVEN" if not all_proven else "EVIDENCE_REVIEW_REQUIRED",
        "funding_readiness": "NOT_READY",
    }


def main() -> None:
    args = _parse_args()
    report = build_report(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output),
                "candidate_count": len(report["comparisons"]),
                "promotion_performed": report["promotion_performed"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
