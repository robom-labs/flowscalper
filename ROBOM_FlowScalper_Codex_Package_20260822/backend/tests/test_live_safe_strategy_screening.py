# 4후보·100후보 screening의 LIVE 안전감시·불변 이력·결과 계약을 검증한다.

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest

from scripts.run_live_safe_strategy_league_replay import (
    ChildState,
    _duty_cycle_slices,
    _enforce_hard_duty_cycle,
    _run_child,
)
from scripts.run_live_safe_strategy_screening import (
    _OUTPUT_FILENAMES,
    _RESEARCH_INFRASTRUCTURE_BOUND_PATHS,
    _research_spill_contract,
    _staged_paths,
    _trial_proposal,
    _validate_result,
)


class _RunningChild:
    pid = 12345
    returncode = None


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_research_infrastructure(project_root: Path) -> None:
    for relative_path in _RESEARCH_INFRASTRUCTURE_BOUND_PATHS:
        path = project_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture:{relative_path}\n", encoding="utf-8")


def test_e06_live_safe_proposal_binds_parameters_data_code_and_cost(tmp_path: Path) -> None:
    trial_manifest = tmp_path / "trials.json"
    dataset_manifest = tmp_path / "dataset.json"
    _write_research_infrastructure(tmp_path)
    _write_json(
        trial_manifest,
        {
            "manifest_kind": "COST_COVERED_EXIT_VARIANT_BATCH",
            "status": "PREREGISTERED_NOT_EXECUTED",
            "trial_count": 4,
            "batch_id": "COST_COVERED_EARLY_TP_RUNNER_V1",
            "paper_only": True,
            "real_orders_enabled": False,
            "private_api_enabled": False,
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
            project_root=tmp_path,
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

    launcher = tmp_path / "scripts" / "run_server.py"
    launcher.write_text("fixture:runtime-mitigation-v2\n", encoding="utf-8")
    changed = _trial_proposal(
        argparse.Namespace(
            trial_manifest=trial_manifest,
            dataset_manifest=dataset_manifest,
            project_root=tmp_path,
        )
    )

    assert changed.implementation_fingerprint != proposal.implementation_fingerprint
    assert changed.parameter_fingerprint == proposal.parameter_fingerprint
    assert changed.dataset_fingerprint == proposal.dataset_fingerprint
    assert changed.cost_model_fingerprint == proposal.cost_model_fingerprint


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
                    "trial_batch": {"manifest_kind": "COST_COVERED_EXIT_VARIANT_BATCH"},
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


def test_strategy_100_live_safe_proposal_binds_frozen_public_inputs(
    tmp_path: Path,
) -> None:
    trial_manifest = tmp_path / "trials.json"
    dataset_manifest = tmp_path / "dataset.json"
    _write_research_infrastructure(tmp_path)
    _write_json(
        trial_manifest,
        {
            "status": "PREREGISTERED_NOT_EXECUTED",
            "trial_count": 100,
            "screening_eligible_count": 90,
            "runtime_active_count": 0,
            "live_shadow_count": 0,
            "paper_only": True,
            "real_orders_enabled": False,
            "private_api_enabled": False,
            "manifest_sha256": "a" * 64,
            "source_checksums": {
                "backend/app/costing/models.py": "b" * 64,
                "backend/app/execution/portfolio.py": "c" * 64,
            },
            "trials": [{"trial_id": f"TRIAL-{index:03d}"} for index in range(100)],
        },
    )
    _write_json(
        dataset_manifest,
        {
            "manifest_sha256": "d" * 64,
            "live_public_cut": {"manifest_sha256": "e" * 64},
            "warmup_manifest": {"manifest_sha256": "f" * 64},
            "runs": [
                {
                    "run_id": "RUN-TRAIN",
                    "role": "TRAIN",
                    "checksum": None,
                    "start_ts_ms": 100,
                    "end_ts_ms": 200,
                    "event_count": 10,
                },
                {
                    "run_id": "RUN-VALIDATION",
                    "role": "VALIDATION",
                    "checksum": None,
                    "start_ts_ms": 201,
                    "end_ts_ms": 300,
                    "event_count": 20,
                },
                {
                    "run_id": "RUN-FINAL",
                    "role": "FINAL_OOS",
                    "checksum": None,
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
            project_root=tmp_path,
        )
    )

    assert proposal.hypothesis_id == "HYP-STRATEGY-100-FROZEN-BATCH-V2"
    assert proposal.dataset_start_ts_ms == 100
    assert proposal.dataset_end_ts_ms == 300
    assert proposal.dataset_member_fingerprints[0].startswith("RUN-TRAIN:")
    assert not proposal.dataset_member_fingerprints[0].endswith(":None")


def test_strategy_100_live_safe_result_accepts_100_registered_90_executable(
    tmp_path: Path,
) -> None:
    paths = _staged_paths(tmp_path)
    for name, path in paths.items():
        if name == "screening":
            _write_json(
                path,
                {
                    "status": "EXECUTED",
                    "registered_trial_count": 100,
                    "planned_independent_account_count": 200,
                    "executed_trial_count": 90,
                    "failed_trial_count": 0,
                    "selection_count": 0,
                    "active_count": 0,
                    "live_shadow_count": 0,
                    "final_oos_status": "SEALED_NOT_USED_FOR_SELECTION",
                    "profitability_claim": "NOT_PROVEN_UNTIL_LATER_GATES",
                    "paper_only": True,
                    "real_orders_enabled": False,
                    "private_api_enabled": False,
                    "trial_batch": {"manifest_kind": "STRATEGY_100_FROZEN_BATCH"},
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

    assert summary["manifest_kind"] == "STRATEGY_100_FROZEN_BATCH"
    assert summary["registered_trial_count"] == 100
    assert summary["planned_independent_account_count"] == 200


def test_hard_duty_cycle_caps_native_scan_before_cooperative_checkpoints() -> None:
    run_seconds, stop_seconds = _duty_cycle_slices(0.2, 0.05)

    assert run_seconds == pytest.approx(0.05)
    assert stop_seconds == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_hard_duty_cycle_always_resumes_cancelled_child(monkeypatch) -> None:
    if os.name == "nt" or not hasattr(signal, "SIGSTOP"):
        pytest.skip("SIGSTOP을 지원하는 운영체제에서만 검증합니다.")
    signals: list[int] = []
    monkeypatch.setattr(os, "kill", lambda _pid, value: signals.append(value))
    stop_event = asyncio.Event()
    state = ChildState()
    task = asyncio.create_task(
        _enforce_hard_duty_cycle(
            _RunningChild(),
            target_cpu_ratio=0.2,
            maximum_continuous_run_seconds=0.001,
            stop_event=stop_event,
            state=state,
        )
    )
    await asyncio.sleep(0.003)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert signal.SIGSTOP in signals
    assert signals[-1] == signal.SIGCONT
    assert state.hard_duty_cycle_enabled is True
    assert state.hard_duty_cycle_resume_count == state.hard_duty_cycle_stop_count


@pytest.mark.asyncio
async def test_research_child_process_is_hard_throttled_and_resumed(tmp_path: Path) -> None:
    if os.name == "nt" or not hasattr(signal, "SIGSTOP"):
        pytest.skip("SIGSTOP을 지원하는 운영체제에서만 검증합니다.")
    state = ChildState()

    await _run_child(
        (
            sys.executable,
            "-c",
            (
                "import time; end = time.process_time() + 0.03; "
                "exec('while time.process_time() < end:\\n pass')"
            ),
        ),
        project_root=tmp_path,
        state=state,
        hard_duty_cycle_target_ratio=0.2,
        maximum_continuous_run_seconds=0.01,
    )

    assert state.return_code == 0
    assert state.hard_duty_cycle_enabled is True
    assert state.hard_duty_cycle_stop_count > 0
    assert state.hard_duty_cycle_stop_count - state.hard_duty_cycle_resume_count in {
        0,
        1,
    }


@pytest.mark.asyncio
async def test_research_child_receives_explicit_spill_environment(tmp_path: Path) -> None:
    state = ChildState()

    await _run_child(
        (
            sys.executable,
            "-c",
            "import os; print(os.environ['ROBOM_RESEARCH_SPILL_ROOT'])",
        ),
        project_root=tmp_path,
        state=state,
        environment_overrides={"ROBOM_RESEARCH_SPILL_ROOT": "/external/research-spill"},
    )

    assert state.return_code == 0
    assert state.stdout_tail == "/external/research-spill"


def test_large_screening_requires_spill_on_another_device(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    spill = tmp_path / "spill"
    archive.mkdir()
    dataset = tmp_path / "dataset.json"
    _write_json(dataset, {"live_public_cut": {"file_count": 500}})
    arguments = argparse.Namespace(
        dataset_manifest=dataset,
        archive=archive,
        research_spill_root=None,
    )

    with pytest.raises(ValueError, match="research-spill-root"):
        _research_spill_contract(arguments)

    arguments.research_spill_root = spill
    with pytest.raises(ValueError, match="다른 저장장치"):
        _research_spill_contract(arguments)


@pytest.mark.asyncio
async def test_cancelled_research_child_is_resumed_before_termination(
    tmp_path: Path,
) -> None:
    if os.name == "nt" or not hasattr(signal, "SIGSTOP"):
        pytest.skip("SIGSTOP을 지원하는 운영체제에서만 검증합니다.")
    state = ChildState()
    task = asyncio.create_task(
        _run_child(
            (sys.executable, "-c", "while True: pass"),
            project_root=tmp_path,
            state=state,
            hard_duty_cycle_target_ratio=0.2,
            maximum_continuous_run_seconds=0.005,
        )
    )
    await asyncio.sleep(0.03)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert state.terminated_by_guard is True
    assert state.return_code is not None
    assert state.hard_duty_cycle_stop_count > 0
    assert state.hard_duty_cycle_resume_count == state.hard_duty_cycle_stop_count
