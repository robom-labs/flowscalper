"""100후보 공통 데이터셋이 순서·checksum·split 변경을 닫는지 검증한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import pytest

from backend.app.research import DatasetSlice, build_strategy_100_dataset_manifest


def _slice(run_id: str, start: int, end: int, checksum: str) -> DatasetSlice:
    return DatasetSlice(
        run_id=run_id,
        venue="BINANCE_USDM",
        symbols=("BTCUSDT", "ETHUSDT"),
        start_ts_ms=start,
        end_ts_ms=end,
        event_count=10,
        checksum=checksum * 64,
    )


def _source() -> tuple[dict[str, object], tuple[DatasetSlice, ...]]:
    rows = (
        _slice("train", 1_000, 1_900, "a"),
        _slice("validation", 2_000, 2_900, "b"),
        _slice("oos", 3_000, 3_900, "c"),
    )
    source: dict[str, object] = {
        "manifest": {
            "status": "EXECUTED",
            "dataset_hash": "d" * 64,
            "dataset": [asdict(row) for row in rows],
            "run_ids": [row.run_id for row in rows],
        },
        "result": {
            "execution_scope": "FULL_PREREGISTERED_ARCHIVE",
            "splits": {
                "train": {"run_ids": ["train"]},
                "validation": {"run_ids": ["validation"]},
                "oos": {"run_ids": ["oos"]},
            },
        },
    }
    return source, rows


def _trial_manifest() -> dict[str, object]:
    manifest: dict[str, object] = {
        "status": "PREREGISTERED_NOT_EXECUTED",
        "trial_count": 100,
        "alpha_family_count": 20,
        "exit_module_count": 5,
        "runtime_active_count": 0,
        "live_shadow_count": 0,
        "code_version": "test-code",
        "source_checksums": {"registry.py": "a" * 64},
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return manifest


def test_dataset_freeze_is_deterministic_and_keeps_forward_prospective() -> None:
    source, rows = _source()

    first = build_strategy_100_dataset_manifest(
        source,
        rows,
        source_evidence_path="evidence/source.json",
        source_evidence_sha256="e" * 64,
        trial_manifest=_trial_manifest(),
        trial_manifest_path="evidence/trials.json",
        trial_manifest_file_sha256="f" * 64,
        generated_ts_utc="2026-08-28T00:00:00+00:00",
    )
    second = build_strategy_100_dataset_manifest(
        source,
        tuple(reversed(rows)),
        source_evidence_path="evidence/source.json",
        source_evidence_sha256="e" * 64,
        trial_manifest=_trial_manifest(),
        trial_manifest_path="evidence/trials.json",
        trial_manifest_file_sha256="f" * 64,
        generated_ts_utc="2026-08-28T00:00:00+00:00",
    )

    assert first == second
    assert first["schema_version"] == 2
    assert first["status"] == "FROZEN_HISTORICAL_FORWARD_PENDING"
    assert first["archive_verification"]["status"] == "PASS"
    assert first["forward_live_public"]["status"] == "NOT_STARTED"
    assert first["candidate_contract"]["trial_count"] == 100
    assert first["candidate_contract"]["live_shadow_may_execute"] is False
    assert first["trial_manifest"]["manifest_sha256"] == _trial_manifest()["manifest_sha256"]
    assert first["historical_splits"]["purge_embargo_ms_by_horizon"] == {
        "MICRO_SCALP": 180_000,
        "FAST_INTRADAY": 3_600_000,
        "INTRADAY_SWING": 21_600_000,
    }
    assert [row["role"] for row in first["runs"]] == [
        "TRAIN",
        "VALIDATION",
        "FINAL_OOS",
    ]


def test_dataset_freeze_rejects_archive_checksum_or_partial_scope_change() -> None:
    source, rows = _source()
    changed = list(rows)
    changed[-1] = _slice("oos", 3_000, 3_900, "f")

    with pytest.raises(ValueError, match="checksum"):
        build_strategy_100_dataset_manifest(
            source,
            changed,
            source_evidence_path="source.json",
            source_evidence_sha256="e" * 64,
            trial_manifest=_trial_manifest(),
            trial_manifest_path="trials.json",
            trial_manifest_file_sha256="f" * 64,
            generated_ts_utc="2026-08-28T00:00:00+00:00",
        )

    result = source["result"]
    assert isinstance(result, dict)
    result["execution_scope"] = "PARTIAL_DIAGNOSTIC_NOT_EVIDENCE"
    with pytest.raises(ValueError, match="부분"):
        build_strategy_100_dataset_manifest(
            source,
            rows,
            source_evidence_path="source.json",
            source_evidence_sha256="e" * 64,
            trial_manifest=_trial_manifest(),
            trial_manifest_path="trials.json",
            trial_manifest_file_sha256="f" * 64,
            generated_ts_utc="2026-08-28T00:00:00+00:00",
        )


def test_dataset_freeze_rejects_tampered_or_active_trial_manifest() -> None:
    source, rows = _source()
    tampered = _trial_manifest()
    tampered["trial_count"] = 99

    with pytest.raises(ValueError, match="checksum"):
        build_strategy_100_dataset_manifest(
            source,
            rows,
            source_evidence_path="source.json",
            source_evidence_sha256="e" * 64,
            trial_manifest=tampered,
            trial_manifest_path="trials.json",
            trial_manifest_file_sha256="f" * 64,
            generated_ts_utc="2026-08-28T00:00:00+00:00",
        )

    active = _trial_manifest()
    active["runtime_active_count"] = 1
    material = dict(active)
    material.pop("manifest_sha256")
    active["manifest_sha256"] = hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match="사전등록"):
        build_strategy_100_dataset_manifest(
            source,
            rows,
            source_evidence_path="source.json",
            source_evidence_sha256="e" * 64,
            trial_manifest=active,
            trial_manifest_path="trials.json",
            trial_manifest_file_sha256="f" * 64,
            generated_ts_utc="2026-08-28T00:00:00+00:00",
        )
