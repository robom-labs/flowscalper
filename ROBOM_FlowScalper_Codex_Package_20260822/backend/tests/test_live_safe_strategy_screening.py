# E06 후보 screening의 LIVE 안전감시·불변 이력·결과 계약을 검증한다.

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.run_live_safe_strategy_screening import (
    _OUTPUT_FILENAMES,
    _staged_paths,
    _trial_proposal,
    _validate_result,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_e06_live_safe_proposal_binds_parameters_data_code_and_cost(tmp_path: Path) -> None:
    trial_manifest = tmp_path / "trials.json"
    dataset_manifest = tmp_path / "dataset.json"
    _write_json(
        trial_manifest,
        {
            "manifest_kind": "COST_COVERED_EXIT_VARIANT_BATCH",
            "status": "PREREGISTERED_NOT_EXECUTED",
            "trial_count": 4,
            "batch_id": "COST_COVERED_EARLY_TP_RUNNER_V1",
            "paper_only": True,
            "real_orders_enabled": False,
            "manifest_sha256": "a" * 64,
            "source_checksums": {
                "backend/app/costing/models.py": "b" * 64,
                "backend/app/research/execution.py": "c" * 64,
            },
            "cost_contract": {"base": 13, "stress": 25},
            "risk_contract": {"risk": "0.005"},
            "trials": [{"trial_id": f"E06-{index}"} for index in range(4)],
        },
    )
    _write_json(
        dataset_manifest,
        {
            "manifest_sha256": "d" * 64,
            "runs": [
                {
                    "run_id": "RUN-TRAIN",
                    "role": "TRAIN",
                    "checksum": "e" * 64,
                    "start_ts_ms": 100,
                    "end_ts_ms": 200,
                    "event_count": 10,
                },
                {
                    "run_id": "RUN-VALIDATION",
                    "role": "VALIDATION",
                    "checksum": "f" * 64,
                    "start_ts_ms": 201,
                    "end_ts_ms": 300,
                    "event_count": 20,
                },
                {
                    "run_id": "RUN-FINAL",
                    "role": "FINAL_OOS",
                    "checksum": "0" * 64,
                    "start_ts_ms": 301,
                    "end_ts_ms": 400,
                    "event_count": 30,
                },
            ],
        },
    )

    proposal = _trial_proposal(
        argparse.Namespace(
            trial_manifest=trial_manifest,
            dataset_manifest=dataset_manifest,
        )
    )

    assert proposal.hypothesis_id == "HYP-COST-COVERED-EARLY-TP-RUNNER-E06-V1"
    assert proposal.dataset_start_ts_ms == 100
    assert proposal.dataset_end_ts_ms == 300
    assert proposal.dataset_member_fingerprints == (
        f"RUN-TRAIN:{'e' * 64}",
        f"RUN-VALIDATION:{'f' * 64}",
    )
    assert proposal.parameter_fingerprint
    assert proposal.implementation_fingerprint
    assert proposal.cost_model_fingerprint


def test_e06_live_safe_result_requires_sealed_oos_and_paper_boundaries(
    tmp_path: Path,
) -> None:
    paths = _staged_paths(tmp_path)
    for name, path in paths.items():
        if name == "screening":
            _write_json(
                path,
                {
                    "status": "INCOMPLETE_TRIAL_FAILURES",
                    "registered_trial_count": 4,
                    "planned_independent_account_count": 8,
                    "executed_trial_count": 2,
                    "failed_trial_count": 2,
                    "selection_count": 0,
                    "active_count": 0,
                    "live_shadow_count": 0,
                    "final_oos_status": "SEALED_NOT_USED_FOR_SELECTION",
                    "profitability_claim": "NOT_PROVEN_UNTIL_LATER_GATES",
                    "paper_only": True,
                    "real_orders_enabled": False,
                    "private_api_enabled": False,
                    "trial_batch": {
                        "manifest_kind": "COST_COVERED_EXIT_VARIANT_BATCH"
                    },
                },
            )
        elif name == "audit":
            _write_json(
                path,
                {
                    "final_oos_processed": False,
                    "processed_roles": ["TRAIN", "VALIDATION"],
                },
            )
        else:
            path.write_text("\n", encoding="utf-8")

    summary = _validate_result(paths)

    assert summary["registered_trial_count"] == 4
    assert summary["planned_independent_account_count"] == 8
    assert summary["profitability_claim"] == "NOT_PROVEN_UNTIL_LATER_GATES"
    assert set(path.name for path in paths.values()) == set(_OUTPUT_FILENAMES.values())
