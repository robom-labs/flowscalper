# 비용회수형 전략 screening을 LIVE PAPER 안전감시와 불변 연구이력 아래 실행한다.

"""사전등록 후보 계산이 현재 공개시장 수신을 침범하면 결과 승격 없이 중단한다."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.replay.safety import (
    ReplayLiveSafetySnapshot,
    ReplayLiveSafetyThresholds,
    ReplayLiveSafetyViolation,
    replay_live_safety_snapshot_from_dashboard,
    run_with_live_safety,
)
from backend.app.research.survivor_watchlist import parameter_fingerprint
from backend.app.research.trial_history import (
    ResearchTrialProposal,
    ResearchTrialRecord,
    ResearchTrialStatus,
    evaluate_trial_proposal,
)
from backend.app.storage.integrity import fetch_dashboard_payload
from scripts.run_live_safe_strategy_league_replay import (
    ChildState,
    ResearchReplayResourceBusy,
    ResearchTrialHistoryBlocked,
    SafetyObservations,
    _acquire_replay_resource_lock,
    _append_trial_history,
    _atomic_write_json,
    _canonical_hash,
    _load_trial_history,
    _low_priority_command,
    _release_replay_resource_lock,
    _run_child,
)

_OUTPUT_FILENAMES = {
    "screening": "SCREENING.json",
    "trades": "SCREENING_TRADES.jsonl",
    "audit": "SCREENING_AUDIT.json",
    "trailing": "TRAILING_ABLATION.json",
    "walk_forward": "WALK_FORWARD_RESULTS.json",
    "multiple_testing": "MULTIPLE_TESTING_RESULTS.json",
}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"연구 JSON은 object여야 합니다: {path}")
    return payload


def _selected_dataset_rows(dataset: Mapping[str, object]) -> list[dict[str, object]]:
    rows = dataset.get("runs")
    if not isinstance(rows, list):
        raise ValueError("후보 연구 dataset Run 목록이 없습니다.")
    selected = [
        dict(row)
        for row in rows
        if isinstance(row, Mapping) and row.get("role") in {"TRAIN", "VALIDATION"}
    ]
    if not selected or any(row.get("role") == "FINAL_OOS" for row in selected):
        raise ValueError("후보 연구 입력에 올바른 Train·Validation Run이 없습니다.")
    return sorted(
        selected,
        key=lambda row: (int(str(row["start_ts_ms"])), str(row["run_id"])),
    )


def _trial_proposal(arguments: argparse.Namespace) -> ResearchTrialProposal:
    trial = _read_json(arguments.trial_manifest)
    dataset = _read_json(arguments.dataset_manifest)
    rows = _selected_dataset_rows(dataset)
    if (
        trial.get("manifest_kind") != "COST_COVERED_EXIT_VARIANT_BATCH"
        or trial.get("status") != "PREREGISTERED_NOT_EXECUTED"
        or trial.get("trial_count") != 4
        or trial.get("paper_only") is not True
        or trial.get("real_orders_enabled") is not False
    ):
        raise ValueError("LIVE 안전 screening은 사전등록된 E06 PAPER 4후보만 허용합니다.")
    source_checksums = trial.get("source_checksums")
    if not isinstance(source_checksums, Mapping):
        raise ValueError("E06 source checksum이 없습니다.")
    cost_sources = {
        str(path): str(checksum)
        for path, checksum in source_checksums.items()
        if any(
            marker in str(path)
            for marker in (
                "/costing/",
                "/execution/",
                "/positions/",
                "/risk/",
            )
        )
    }
    if not cost_sources:
        raise ValueError("E06 비용·체결 구현 지문이 없습니다.")
    dataset_material = {
        "manifest_sha256": dataset.get("manifest_sha256"),
        "runs": [
            {
                key: row.get(key)
                for key in ("run_id", "checksum", "start_ts_ms", "end_ts_ms", "event_count")
            }
            for row in rows
        ],
    }
    return ResearchTrialProposal(
        hypothesis_id="HYP-COST-COVERED-EARLY-TP-RUNNER-E06-V1",
        parameter_fingerprint=parameter_fingerprint(
            {
                "batch_id": trial.get("batch_id"),
                "trials": trial.get("trials"),
                "cost_contract": trial.get("cost_contract"),
                "risk_contract": trial.get("risk_contract"),
            }
        ),
        dataset_fingerprint=_canonical_hash(dataset_material),
        dataset_start_ts_ms=min(int(str(row["start_ts_ms"])) for row in rows),
        dataset_end_ts_ms=max(int(str(row["end_ts_ms"])) for row in rows),
        implementation_fingerprint=_canonical_hash(
            {
                "manifest_sha256": trial.get("manifest_sha256"),
                "source_checksums": dict(sorted(source_checksums.items())),
            }
        ),
        cost_model_fingerprint=_canonical_hash(cost_sources),
        dataset_member_fingerprints=tuple(
            f"{row.get('run_id')}:{row.get('checksum')}" for row in rows
        ),
    )


def _staged_paths(directory: Path) -> dict[str, Path]:
    return {name: directory / filename for name, filename in _OUTPUT_FILENAMES.items()}


def _child_command(
    arguments: argparse.Namespace,
    paths: Mapping[str, Path],
) -> tuple[str, ...]:
    command = (
        sys.executable,
        str(arguments.project_root / "scripts" / "research_strategy_100_candidates.py"),
        "--archive",
        str(arguments.archive),
        "--trial-manifest",
        str(arguments.trial_manifest),
        "--dataset-manifest",
        str(arguments.dataset_manifest),
        "--instrument-manifest",
        str(arguments.instrument_manifest),
        "--output",
        str(paths["screening"]),
        "--trades-output",
        str(paths["trades"]),
        "--audit-output",
        str(paths["audit"]),
        "--trailing-ablation-output",
        str(paths["trailing"]),
        "--walk-forward-output",
        str(paths["walk_forward"]),
        "--multiple-testing-output",
        str(paths["multiple_testing"]),
        "--target-cpu-ratio",
        str(arguments.target_cpu_ratio),
        "--cpu-checkpoint-events",
        str(arguments.cpu_checkpoint_events),
    )
    return _low_priority_command(command)


def _validate_result(paths: Mapping[str, Path]) -> dict[str, object]:
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError(f"후보 screening 결과 파일이 누락됐습니다: {missing}")
    report = _read_json(paths["screening"])
    audit = _read_json(paths["audit"])
    expected = {
        "registered_trial_count": 4,
        "planned_independent_account_count": 8,
        "active_count": 0,
        "live_shadow_count": 0,
        "final_oos_status": "SEALED_NOT_USED_FOR_SELECTION",
        "profitability_claim": "NOT_PROVEN_UNTIL_LATER_GATES",
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
    }
    mismatches = {
        key: {"expected": value, "actual": report.get(key)}
        for key, value in expected.items()
        if report.get(key) != value
    }
    trial_batch = report.get("trial_batch")
    if (
        mismatches
        or report.get("status") not in {"EXECUTED", "INCOMPLETE_TRIAL_FAILURES"}
        or not isinstance(trial_batch, Mapping)
        or trial_batch.get("manifest_kind") != "COST_COVERED_EXIT_VARIANT_BATCH"
        or audit.get("final_oos_processed") is not False
        or audit.get("processed_roles") != ["TRAIN", "VALIDATION"]
    ):
        raise ValueError(f"E06 screening 결과 불변조건이 다릅니다: {mismatches}")
    return {
        "status": report["status"],
        "registered_trial_count": report["registered_trial_count"],
        "planned_independent_account_count": report["planned_independent_account_count"],
        "executed_trial_count": report.get("executed_trial_count"),
        "failed_trial_count": report.get("failed_trial_count"),
        "selection_count": report.get("selection_count"),
        "final_oos_status": report["final_oos_status"],
        "profitability_claim": report["profitability_claim"],
    }


async def _execute(
    arguments: argparse.Namespace,
    *,
    paths: Mapping[str, Path],
    observations: SafetyObservations,
    child_state: ChildState,
) -> dict[str, object]:
    dashboard_url = arguments.runtime_url.rstrip("/") + "/api/dashboard"

    def probe() -> ReplayLiveSafetySnapshot:
        payload = fetch_dashboard_payload(
            dashboard_url,
            timeout_seconds=arguments.request_timeout_seconds,
        )
        snapshot = replay_live_safety_snapshot_from_dashboard(payload)
        observations.record(snapshot)
        return snapshot

    thresholds = ReplayLiveSafetyThresholds(
        max_queue_depth=arguments.max_queue_depth,
        max_lag_p95_ms=arguments.max_lag_p95_ms,
        max_event_stall_seconds=arguments.max_event_stall_seconds,
        poll_seconds=arguments.poll_seconds,
        max_consecutive_probe_errors=arguments.max_consecutive_probe_errors,
        planned_rotation_lock_grace_seconds=arguments.planned_rotation_lock_grace_seconds,
    )
    await asyncio.wait_for(
        run_with_live_safety(
            lambda: _run_child(
                _child_command(arguments, paths),
                project_root=arguments.project_root,
                state=child_state,
            ),
            probe=probe,
            thresholds=thresholds,
        ),
        timeout=arguments.max_duration_seconds,
    )
    return _validate_result(paths)


def run(arguments: argparse.Namespace) -> tuple[int, dict[str, object]]:
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    output_directory = arguments.output_directory.resolve()
    control_output = arguments.control_output.resolve()
    if output_directory.exists():
        raise FileExistsError(f"기존 screening 증거를 덮어쓰지 않습니다: {output_directory}")
    if control_output.exists():
        raise FileExistsError(f"기존 제어 증거를 덮어쓰지 않습니다: {control_output}")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_directory.name}.",
            dir=output_directory.parent,
        )
    )
    paths = _staged_paths(staging)
    observations = SafetyObservations()
    child_state = ChildState()
    proposal = _trial_proposal(arguments)
    history = _load_trial_history(arguments.trial_history_catalog)
    history_decision = evaluate_trial_proposal(history, proposal)
    record_id = f"RESEARCH-{uuid4().hex}"
    history_recorded = False
    history_error: dict[str, object] | None = None
    resource_lock_descriptor: int | None = None
    resource_lock_acquired = False
    research_started = False
    status = "FAIL"
    exit_code = 1
    error: dict[str, object] | None = None
    result_summary: dict[str, object] | None = None
    try:
        if history_decision["execution_allowed"] is not True:
            status = "BLOCKED_DUPLICATE_RESEARCH_TRIAL"
            exit_code = 4
            error = {
                "type": "ResearchTrialHistoryBlocked",
                "message": str(history_decision["decision"]),
            }
            raise ResearchTrialHistoryBlocked
        try:
            resource_lock_descriptor = _acquire_replay_resource_lock(arguments.resource_lock)
            resource_lock_acquired = True
        except BlockingIOError as caught:
            status = "BLOCKED_RESEARCH_RESOURCE_BUSY"
            exit_code = 6
            error = {"type": type(caught).__name__, "message": str(caught)}
            raise ResearchReplayResourceBusy from caught
        research_started = True
        result_summary = asyncio.run(
            _execute(
                arguments,
                paths=paths,
                observations=observations,
                child_state=child_state,
            )
        )
        os.replace(staging, output_directory)
        status = "PASS"
        exit_code = 0
    except ReplayLiveSafetyViolation as caught:
        status = "ABORTED_RUNTIME_SAFETY"
        exit_code = 2
        error = {
            "type": type(caught).__name__,
            "message": str(caught),
            "violation_codes": list(caught.violations),
        }
    except TimeoutError as caught:
        status = "ABORTED_TIMEOUT"
        exit_code = 3
        error = {"type": type(caught).__name__, "message": str(caught)}
    except (ResearchTrialHistoryBlocked, ResearchReplayResourceBusy):
        pass
    except KeyboardInterrupt as caught:
        status = "ABORTED_OPERATOR"
        exit_code = 130
        error = {"type": type(caught).__name__, "message": "운영자가 중단했습니다."}
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as caught:
        error = {"type": type(caught).__name__, "message": str(caught)}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        _release_replay_resource_lock(resource_lock_descriptor)
    if history_decision["execution_allowed"] is True and research_started:
        record_status = (
            ResearchTrialStatus.COMPLETE
            if status == "PASS"
            else ResearchTrialStatus.ABORTED
            if status.startswith("ABORTED")
            else ResearchTrialStatus.FAILED
        )
        try:
            _append_trial_history(
                arguments.trial_history_catalog,
                ResearchTrialRecord(
                    trial_id=record_id,
                    proposal=proposal,
                    status=record_status,
                    evidence_path=str(
                        output_directory if status == "PASS" else control_output
                    ),
                ),
            )
            history_recorded = True
        except (OSError, TypeError, ValueError) as caught:
            history_error = {"type": type(caught).__name__, "message": str(caught)}
            if status == "PASS":
                status = "FAIL_TRIAL_HISTORY_NOT_RECORDED"
                exit_code = 5
                error = history_error
    evidence: dict[str, object] = {
        "schema_version": 1,
        "status": status,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "runtime_url": arguments.runtime_url,
        "output_directory": str(output_directory),
        "output_written": output_directory.is_dir(),
        "child": asdict(child_state),
        "runtime_safety": observations.report(),
        "result_summary": result_summary,
        "research_trial": {
            "history_catalog": str(arguments.trial_history_catalog),
            "history_decision": history_decision,
            "history_record_id": record_id,
            "history_recorded": history_recorded,
            "history_error": history_error,
            "proposal": asdict(proposal),
        },
        "resource_contract": {
            "target_cpu_ratio": arguments.target_cpu_ratio,
            "cpu_checkpoint_events": arguments.cpu_checkpoint_events,
            "resource_lock": str(arguments.resource_lock),
            "resource_lock_acquired": resource_lock_acquired,
            "single_archive_research_enforced": True,
        },
        "paper_safety": {
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_enabled": False,
            "wallet_paths_enabled": False,
            "runtime_ai_order_decision": False,
        },
        "error": error,
    }
    _atomic_write_json(control_output, evidence)
    return exit_code, evidence


def parse_arguments() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument(
        "--archive",
        type=Path,
        default=project_root / "data" / "market-parquet-v6" / "venue=BINANCE_USDM",
    )
    parser.add_argument(
        "--trial-manifest",
        type=Path,
        default=project_root / "evidence" / "COST_COVERED_EXIT_VARIANT_MANIFEST.json",
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=project_root / "evidence" / "STRATEGY_100_DATASET_MANIFEST.json",
    )
    parser.add_argument(
        "--instrument-manifest",
        type=Path,
        default=project_root / "evidence" / "STRATEGY_100_INSTRUMENTS.json",
    )
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8870")
    parser.add_argument("--request-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--max-consecutive-probe-errors", type=int, default=3)
    parser.add_argument("--max-queue-depth", type=int, default=64)
    parser.add_argument("--max-lag-p95-ms", type=float, default=500.0)
    parser.add_argument("--max-event-stall-seconds", type=float, default=30.0)
    parser.add_argument("--planned-rotation-lock-grace-seconds", type=float, default=15.0)
    parser.add_argument("--max-duration-seconds", type=float, default=28_800.0)
    parser.add_argument("--target-cpu-ratio", type=float, default=0.15)
    parser.add_argument("--cpu-checkpoint-events", type=int, default=256)
    parser.add_argument(
        "--trial-history-catalog",
        type=Path,
        default=project_root / "evidence" / "RESEARCH_TRIAL_HISTORY.jsonl",
    )
    parser.add_argument(
        "--resource-lock",
        type=Path,
        default=Path("/tmp/robom-flowscalper-strategy-league-replay.lock"),
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--control-output", type=Path)
    arguments = parser.parse_args()
    arguments.project_root = arguments.project_root.resolve(strict=True)
    arguments.archive = arguments.archive.resolve(strict=True)
    arguments.trial_manifest = arguments.trial_manifest.resolve(strict=True)
    arguments.dataset_manifest = arguments.dataset_manifest.resolve(strict=True)
    arguments.instrument_manifest = arguments.instrument_manifest.resolve(strict=True)
    arguments.trial_history_catalog = arguments.trial_history_catalog.resolve()
    arguments.resource_lock = arguments.resource_lock.resolve()
    arguments.control_output = arguments.control_output or arguments.output_directory.with_name(
        arguments.output_directory.name + "_LIVE_GUARD.json"
    )
    if (
        arguments.request_timeout_seconds <= 0
        or arguments.poll_seconds <= 0
        or arguments.max_consecutive_probe_errors <= 0
        or arguments.max_queue_depth < 0
        or arguments.max_lag_p95_ms <= 0
        or arguments.max_event_stall_seconds <= 0
        or arguments.planned_rotation_lock_grace_seconds <= 0
        or arguments.max_duration_seconds <= 0
        or not 0 < arguments.target_cpu_ratio <= 1
        or arguments.cpu_checkpoint_events <= 0
    ):
        parser.error("LIVE 감시·CPU·시간 설정이 잘못됐습니다.")
    return arguments


def main() -> None:
    arguments = parse_arguments()
    exit_code, evidence = run(arguments)
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
