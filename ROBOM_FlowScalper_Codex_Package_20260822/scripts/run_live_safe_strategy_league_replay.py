# 11개 전략 전체 연구 리플레이를 LIVE PAPER 안전감시 아래 저우선순위로 실행한다.
"""동결 archive 전수 비교가 현재 공개시장 수신을 침범하면 즉시 중단하는 CLI다."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.build_identity import git_commit
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
from backend.app.strategies.registry import StrategyRegistry
from scripts.research_runtime_strategy_replay import (
    DEFAULT_RESEARCH_ARCHIVE_READ_MIB_PER_SECOND,
    DEFAULT_STRATEGY_ID,
    SIGNAL_GATE_NONE,
    SIGNAL_GATE_TARGET_ALL,
    SIGNAL_GATES,
    STRATEGY_LOGIC_CURRENT,
    STRATEGY_LOGICS,
)

_SINGLE_THREAD_ENVIRONMENT = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)
_IMPLEMENTATION_BOUND_PATHS = (
    "backend/app/candidates",
    "backend/app/costing",
    "backend/app/execution",
    "backend/app/features",
    "backend/app/positions",
    "backend/app/regime",
    "backend/app/risk",
    "backend/app/strategies",
    "scripts/research_runtime_strategy_replay.py",
)
_DEFAULT_RESEARCH_TARGET_CPU_RATIO = 0.25


def _default_live_ledger_path(project_root: Path) -> Path:
    """현재 설치형 macOS 서비스의 활성 PAPER 원장을 명시적 경로로 찾는다."""

    configured = os.environ.get("ROBOM_DB_PATH", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    parts = project_root.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        candidates.append(
            Path("/Volumes")
            / parts[2]
            / "05_RUNTIME"
            / "ROBOM_FlowScalper"
            / "active-ledger"
            / "run-ledger.sqlite3"
        )
    candidates.extend(
        (
            Path.home()
            / "Library"
            / "Application Support"
            / "ROBOM FlowScalper"
            / "active-ledger"
            / "run-ledger.sqlite3",
            project_root / "data" / "run-ledger.sqlite3",
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve(strict=True)
    raise FileNotFoundError(
        "LIVE 원장 우선순위 조율 경로를 찾지 못했습니다. --live-ledger-path를 지정하세요."
    )


class ResearchTrialHistoryBlocked(Exception):
    """완료된 동일 연구시험을 다시 실행하려 할 때 내부 제어흐름으로 사용한다."""


class ResearchReplayResourceBusy(Exception):
    """다른 archive 연구 리플레이가 이미 자원을 점유할 때 사용한다."""


@dataclass(slots=True)
class ChildState:
    """자식 종료와 출력 꼬리를 제어 증거에 남긴다."""

    command: tuple[str, ...] = ()
    return_code: int | None = None
    terminated_by_guard: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""
    hard_duty_cycle_supported: bool = False
    hard_duty_cycle_enabled: bool = False
    hard_duty_cycle_run_slice_seconds: float = 0.0
    hard_duty_cycle_stop_slice_seconds: float = 0.0
    hard_duty_cycle_stop_count: int = 0
    hard_duty_cycle_resume_count: int = 0


@dataclass(slots=True)
class SafetyObservations:
    """전체 표본을 저장하지 않고 LIVE 안전감시의 경계값만 집계한다."""

    sample_count: int = 0
    baseline: ReplayLiveSafetySnapshot | None = None
    latest: ReplayLiveSafetySnapshot | None = None
    maximum_queue_depth: int = 0
    maximum_lag_p95_ms: float = 0.0
    maximum_event_loop_lag_over_500ms_count: int = 0

    def record(self, sample: ReplayLiveSafetySnapshot) -> None:
        if self.baseline is None:
            self.baseline = sample
        self.latest = sample
        self.sample_count += 1
        self.maximum_queue_depth = max(self.maximum_queue_depth, sample.queue_depth)
        self.maximum_lag_p95_ms = max(self.maximum_lag_p95_ms, sample.lag_p95_ms)
        self.maximum_event_loop_lag_over_500ms_count = max(
            self.maximum_event_loop_lag_over_500ms_count,
            sample.event_loop_lag_over_500ms_count,
        )

    def report(self) -> dict[str, object]:
        baseline = self.baseline
        latest = self.latest
        return {
            "sample_count": self.sample_count,
            "baseline": asdict(baseline) if baseline is not None else None,
            "latest": asdict(latest) if latest is not None else None,
            "event_delta": (
                latest.event_count - baseline.event_count
                if baseline is not None and latest is not None
                else 0
            ),
            "event_loop_lag_over_500ms_delta": (
                latest.event_loop_lag_over_500ms_count - baseline.event_loop_lag_over_500ms_count
                if baseline is not None and latest is not None
                else 0
            ),
            "maximum_queue_depth": self.maximum_queue_depth,
            "maximum_lag_p95_ms": self.maximum_lag_p95_ms,
            "maximum_event_loop_lag_over_500ms_count": (
                self.maximum_event_loop_lag_over_500ms_count
            ),
        }


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_bundle_fingerprint(project_root: Path) -> str:
    rows: list[dict[str, str]] = []
    for relative in _IMPLEMENTATION_BOUND_PATHS:
        path = project_root / relative
        sources = sorted(path.rglob("*.py")) if path.is_dir() else [path]
        for source in sources:
            if not source.is_file():
                raise FileNotFoundError(f"연구시험 구현 지문 파일이 없습니다: {source}")
            rows.append(
                {
                    "path": source.relative_to(project_root).as_posix(),
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            )
    return _canonical_hash({"git_commit": git_commit(), "sources": rows})


def _trial_proposal_from_arguments(arguments: argparse.Namespace) -> ResearchTrialProposal:
    manifest = json.loads(arguments.dataset_manifest.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("runs"), list):
        raise ValueError("연구시험 dataset manifest의 Run 목록이 올바르지 않습니다.")
    requested_run_ids = tuple(arguments.run_id or ())
    selected_rows = [
        row
        for row in manifest["runs"]
        if isinstance(row, dict)
        and (not requested_run_ids or str(row.get("run_id")) in requested_run_ids)
    ]
    selected_ids = tuple(str(row.get("run_id")) for row in selected_rows)
    if not selected_rows or (
        requested_run_ids and set(selected_ids) != set(requested_run_ids)
    ):
        raise ValueError("연구시험 dataset manifest에서 요청 Run을 모두 찾지 못했습니다.")
    dataset_material = {
        "manifest_sha256": manifest.get("manifest_sha256"),
        "selected_runs": [
            {
                key: row.get(key)
                for key in ("run_id", "checksum", "start_ts_ms", "end_ts_ms", "event_count")
            }
            for row in selected_rows
        ],
        "maximum_events": arguments.maximum_events,
    }
    normalized_target = (
        "NO_TARGET"
        if str(arguments.signal_gate) == SIGNAL_GATE_NONE
        else str(arguments.signal_gate_target_strategy_id)
    )
    hypothesis_id = (
        "HYP-STRATEGY-LEAGUE-SMOKE-V1"
        if arguments.maximum_events is not None
        else f"HYP-STRATEGY-GATE-{arguments.signal_gate}-V1"
    )
    cost_paths = (
        arguments.project_root / "backend" / "app" / "costing" / "models.py",
        arguments.project_root / "backend" / "app" / "execution" / "simulator.py",
    )
    return ResearchTrialProposal(
        hypothesis_id=hypothesis_id,
        parameter_fingerprint=parameter_fingerprint(
            {
                "signal_gate": str(arguments.signal_gate),
                "target_strategy_id": normalized_target,
                "strategy_logic": str(arguments.strategy_logic),
                "maximum_events": arguments.maximum_events,
            }
        ),
        dataset_fingerprint=_canonical_hash(dataset_material),
        dataset_start_ts_ms=min(int(row["start_ts_ms"]) for row in selected_rows),
        dataset_end_ts_ms=max(int(row["end_ts_ms"]) for row in selected_rows),
        implementation_fingerprint=_source_bundle_fingerprint(arguments.project_root),
        cost_model_fingerprint=_canonical_hash(
            [
                {
                    "path": path.relative_to(arguments.project_root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in cost_paths
            ]
        ),
        dataset_member_fingerprints=tuple(
            f"{row.get('run_id')}:{row.get('checksum')}" for row in selected_rows
        ),
    )


def _trial_record_from_json(payload: object) -> ResearchTrialRecord:
    if not isinstance(payload, dict) or not isinstance(payload.get("proposal"), dict):
        raise ValueError("연구시험 이력 행이 올바른 JSON 객체가 아닙니다.")
    proposal_payload = dict(payload["proposal"])
    members = proposal_payload.get("dataset_member_fingerprints", ())
    if not isinstance(members, list | tuple):
        raise ValueError("연구시험 dataset member 지문이 JSON 배열이 아닙니다.")
    proposal_payload["dataset_member_fingerprints"] = tuple(str(row) for row in members)
    return ResearchTrialRecord(
        trial_id=str(payload.get("trial_id", "")),
        proposal=ResearchTrialProposal(**proposal_payload),
        status=ResearchTrialStatus(str(payload.get("status", ""))),
        evidence_path=str(payload.get("evidence_path", "")),
    )


def _load_trial_history(path: Path) -> tuple[ResearchTrialRecord, ...]:
    if not path.exists():
        return ()
    records: list[ResearchTrialRecord] = []
    with path.open(encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            lines = handle.read().splitlines()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(_trial_record_from_json(json.loads(line)))
        except (TypeError, ValueError, json.JSONDecodeError) as caught:
            raise ValueError(
                f"연구시험 이력 {path}:{line_number}을 읽을 수 없습니다."
            ) from caught
    return tuple(records)


def _append_trial_history(path: Path, record: ResearchTrialRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "flowscalper.research_trial_history_record.v1",
        "trial_id": record.trial_id,
        "proposal": asdict(record.proposal),
        "status": record.status.value,
        "evidence_path": record.evidence_path,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        offset = 0
        while offset < len(raw):
            offset += os.write(descriptor, raw[offset:])
        os.fsync(descriptor)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _acquire_replay_resource_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(descriptor, 0)
        os.write(
            descriptor,
            json.dumps(
                {"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat()},
                sort_keys=True,
            ).encode("utf-8"),
        )
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _release_replay_resource_lock(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for variable in _SINGLE_THREAD_ENVIRONMENT:
        environment[variable] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _research_arguments(
    arguments: argparse.Namespace,
    partial_output: Path,
) -> tuple[str, ...]:
    command = [
        sys.executable,
        str(arguments.project_root / "scripts" / "research_runtime_strategy_replay.py"),
        "--all-strategies",
        "--verify-archive-bytes",
        "--archive",
        str(arguments.archive),
        "--dataset-manifest",
        str(arguments.dataset_manifest),
        "--output",
        str(partial_output),
        "--signal-gate",
        str(arguments.signal_gate),
        "--signal-gate-target-strategy-id",
        str(arguments.signal_gate_target_strategy_id),
        "--strategy-logic",
        str(arguments.strategy_logic),
        "--target-cpu-ratio",
        str(arguments.target_cpu_ratio),
        "--target-archive-read-mib-per-second",
        str(arguments.target_archive_read_mib_per_second),
        "--live-ledger-path",
        str(arguments.live_ledger_path),
    ]
    for run_id in arguments.run_id or ():
        command.extend(("--run-id", run_id))
    if arguments.maximum_events is not None:
        command.extend(("--maximum-events", str(arguments.maximum_events)))
    return tuple(command)


def _low_priority_command(command: tuple[str, ...]) -> tuple[str, ...]:
    nice = Path("/usr/bin/nice")
    taskpolicy = Path("/usr/sbin/taskpolicy")
    if sys.platform == "darwin" and nice.is_file() and taskpolicy.is_file():
        return (str(nice), "-n", "19", str(taskpolicy), "-b", *command)
    if os.name != "nt" and nice.is_file():
        return (str(nice), "-n", "19", *command)
    return command


async def _terminate_child(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except TimeoutError:
        process.kill()
        await process.wait()


def _duty_cycle_slices(
    target_cpu_ratio: float,
    maximum_continuous_run_seconds: float,
) -> tuple[float, float]:
    """첫 archive row 이전의 native scan도 한 번에 짧게만 실행하도록 계산한다."""

    if not 0 < target_cpu_ratio <= 1:
        raise ValueError("외부 연구 CPU 목표 비율은 0 초과 1 이하여야 합니다.")
    if maximum_continuous_run_seconds <= 0:
        raise ValueError("외부 연구 연속 실행시간은 양수여야 합니다.")
    stopped_seconds = (
        0.0
        if target_cpu_ratio == 1
        else maximum_continuous_run_seconds * (1 - target_cpu_ratio) / target_cpu_ratio
    )
    return maximum_continuous_run_seconds, stopped_seconds


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    if seconds <= 0:
        return stop_event.is_set()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        return False
    return True


async def _enforce_hard_duty_cycle(
    process: asyncio.subprocess.Process,
    *,
    target_cpu_ratio: float,
    maximum_continuous_run_seconds: float,
    stop_event: asyncio.Event,
    state: ChildState,
) -> None:
    """협조 checkpoint 전후 모두에서 연구 자식을 짧은 SIGSTOP 구간으로 제한한다."""

    supported = os.name != "nt" and hasattr(signal, "SIGSTOP") and hasattr(signal, "SIGCONT")
    state.hard_duty_cycle_supported = supported
    run_seconds, stop_seconds = _duty_cycle_slices(
        target_cpu_ratio,
        maximum_continuous_run_seconds,
    )
    state.hard_duty_cycle_run_slice_seconds = run_seconds
    state.hard_duty_cycle_stop_slice_seconds = stop_seconds
    if not supported or stop_seconds <= 0:
        return
    state.hard_duty_cycle_enabled = True
    child_stopped = False
    try:
        while process.returncode is None and not stop_event.is_set():
            if await _wait_or_stop(stop_event, run_seconds):
                break
            if process.returncode is not None:
                break
            try:
                os.kill(process.pid, signal.SIGSTOP)
            except ProcessLookupError:
                break
            child_stopped = True
            state.hard_duty_cycle_stop_count += 1
            await _wait_or_stop(stop_event, stop_seconds)
            if process.returncode is None:
                try:
                    os.kill(process.pid, signal.SIGCONT)
                except ProcessLookupError:
                    break
                state.hard_duty_cycle_resume_count += 1
            child_stopped = False
    finally:
        if child_stopped and process.returncode is None:
            try:
                os.kill(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
            else:
                state.hard_duty_cycle_resume_count += 1


def _tail(raw: bytes, *, limit: int = 4_000) -> str:
    return raw.decode(errors="replace").strip()[-limit:]


async def _run_child(
    command: tuple[str, ...],
    *,
    project_root: Path,
    state: ChildState,
    hard_duty_cycle_target_ratio: float | None = None,
    maximum_continuous_run_seconds: float = 0.05,
) -> None:
    state.command = command
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(project_root),
        env=_child_environment(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    duty_cycle_stop = asyncio.Event()
    duty_cycle_task = (
        asyncio.create_task(
            _enforce_hard_duty_cycle(
                process,
                target_cpu_ratio=hard_duty_cycle_target_ratio,
                maximum_continuous_run_seconds=maximum_continuous_run_seconds,
                stop_event=duty_cycle_stop,
                state=state,
            )
        )
        if hard_duty_cycle_target_ratio is not None
        else None
    )
    try:
        stdout, stderr = await process.communicate()
    except asyncio.CancelledError:
        state.terminated_by_guard = True
        duty_cycle_stop.set()
        if duty_cycle_task is not None:
            await duty_cycle_task
        await _terminate_child(process)
        try:
            stdout, stderr = await process.communicate()
        except BaseException:
            stdout, stderr = b"", b""
        state.return_code = process.returncode
        state.stdout_tail = _tail(stdout)
        state.stderr_tail = _tail(stderr)
        raise
    finally:
        duty_cycle_stop.set()
        if duty_cycle_task is not None:
            await duty_cycle_task
    state.return_code = process.returncode
    state.stdout_tail = _tail(stdout)
    state.stderr_tail = _tail(stderr)
    if process.returncode != 0:
        raise RuntimeError(
            "전략리그 연구 자식 프로세스가 실패했습니다. "
            f"exit={process.returncode}; stderr={state.stderr_tail}"
        )


def _validate_result_payload(
    payload: object,
    *,
    full_frozen_replay: bool,
    signal_gate: str,
    signal_gate_target_strategy_id: str,
    strategy_logic: str,
    target_cpu_ratio: float,
    target_archive_read_mib_per_second: float,
) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("전략리그 결과가 JSON 객체가 아닙니다.")
    required = {
        "status": "RESEARCH_STRATEGY_LEAGUE_REPLAY_COMPLETE",
        "method": "ONE_PASS_ALL_REGISTERED_ACTUAL_PAPER_RUNTIME_PATH",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "runtime_ai_order_decision": False,
        "strategy_count": 11,
        "strategy_account_count": 22,
        "signal_gate": signal_gate,
        "signal_gate_target_strategy_id": signal_gate_target_strategy_id,
        "signal_gate_trial_id": f"{signal_gate}:{signal_gate_target_strategy_id}",
        "strategy_logic": strategy_logic,
        "cooperative_cpu_target_ratio": target_cpu_ratio,
    }
    mismatches = {
        key: {"expected": expected, "actual": payload.get(key)}
        for key, expected in required.items()
        if payload.get(key) != expected
    }
    if mismatches:
        raise ValueError(f"전략리그 결과 불변조건이 다릅니다: {mismatches}")
    if not isinstance(payload.get("runs"), list):
        raise ValueError("전략리그 결과의 Run 목록이 올바르지 않습니다.")
    frozen_dataset = payload.get("frozen_dataset")
    if not isinstance(frozen_dataset, dict):
        raise ValueError("전략리그 결과에 동결 dataset 증거가 없습니다.")
    byte_verification = frozen_dataset.get("current_archive_byte_reverification")
    if not isinstance(byte_verification, dict) or byte_verification.get("status") != "PASS":
        raise ValueError("현재 archive byte 재검증이 PASS가 아닙니다.")
    if (
        byte_verification.get("live_writer_io_priority_gate") is not True
        or byte_verification.get("target_read_mib_per_second")
        != target_archive_read_mib_per_second
    ):
        raise ValueError("archive 재검증의 LIVE 원장 I/O 우선순위 계약이 다릅니다.")
    if full_frozen_replay:
        if (
            frozen_dataset.get("selected_run_count") != 13
            or byte_verification.get("run_count") != 13
        ):
            raise ValueError("동결 13-Run 전수 범위가 완전하지 않습니다.")
    return payload


async def _execute(
    arguments: argparse.Namespace,
    *,
    partial_output: Path,
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

    research_command = _research_arguments(arguments, partial_output)
    command = _low_priority_command(research_command)
    thresholds = ReplayLiveSafetyThresholds(
        max_queue_depth=arguments.max_queue_depth,
        max_lag_p95_ms=arguments.max_lag_p95_ms,
        max_event_stall_seconds=arguments.max_event_stall_seconds,
        poll_seconds=arguments.poll_seconds,
        max_consecutive_probe_errors=arguments.max_consecutive_probe_errors,
        planned_rotation_lock_grace_seconds=(arguments.planned_rotation_lock_grace_seconds),
    )
    await asyncio.wait_for(
        run_with_live_safety(
            lambda: _run_child(
                command,
                project_root=arguments.project_root,
                state=child_state,
                hard_duty_cycle_target_ratio=arguments.target_cpu_ratio,
                maximum_continuous_run_seconds=(
                    arguments.maximum_continuous_run_seconds
                ),
            ),
            probe=probe,
            thresholds=thresholds,
        ),
        timeout=arguments.max_duration_seconds,
    )
    raw_result: Any = json.loads(partial_output.read_text(encoding="utf-8"))
    return _validate_result_payload(
        raw_result,
        full_frozen_replay=(arguments.maximum_events is None and not arguments.run_id),
        signal_gate=str(arguments.signal_gate),
        signal_gate_target_strategy_id=str(arguments.signal_gate_target_strategy_id),
        strategy_logic=str(arguments.strategy_logic),
        target_cpu_ratio=float(arguments.target_cpu_ratio),
        target_archive_read_mib_per_second=float(
            arguments.target_archive_read_mib_per_second
        ),
    )


def run(arguments: argparse.Namespace) -> tuple[int, dict[str, object]]:
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    output = arguments.output.resolve()
    control_output = arguments.control_output.resolve()
    if output == control_output:
        raise ValueError("전략 결과와 제어 증거는 서로 다른 경로여야 합니다.")
    if output.exists():
        raise FileExistsError(f"기존 전략 결과를 덮어쓰지 않습니다: {output}")
    if control_output.exists():
        raise FileExistsError(f"기존 제어 증거를 덮어쓰지 않습니다: {control_output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial_output = output.with_name(f".{output.name}.{uuid4().hex}.partial")
    observations = SafetyObservations()
    child_state = ChildState()
    proposal = _trial_proposal_from_arguments(arguments)
    history = _load_trial_history(arguments.trial_history_catalog)
    trial_history_decision = evaluate_trial_proposal(history, proposal)
    trial_record_id = f"RESEARCH-{uuid4().hex}"
    trial_history_recorded = False
    trial_history_error: dict[str, object] | None = None
    resource_lock_descriptor: int | None = None
    resource_lock_acquired = False
    research_started = False
    status = "FAIL"
    exit_code = 1
    error: dict[str, object] | None = None
    result_summary: dict[str, object] | None = None
    try:
        if trial_history_decision["execution_allowed"] is not True:
            status = "BLOCKED_DUPLICATE_RESEARCH_TRIAL"
            exit_code = 4
            error = {
                "type": "ResearchTrialHistoryBlocked",
                "message": str(trial_history_decision["decision"]),
            }
            raise ResearchTrialHistoryBlocked
        try:
            resource_lock_descriptor = _acquire_replay_resource_lock(
                arguments.resource_lock
            )
            resource_lock_acquired = True
        except BlockingIOError as caught:
            status = "BLOCKED_RESEARCH_RESOURCE_BUSY"
            exit_code = 6
            error = {
                "type": "ResearchReplayResourceBusy",
                "message": str(caught),
            }
            raise ResearchReplayResourceBusy from caught
        research_started = True
        result = asyncio.run(
            _execute(
                arguments,
                partial_output=partial_output,
                observations=observations,
                child_state=child_state,
            )
        )
        os.replace(partial_output, output)
        status = "PASS"
        exit_code = 0
        run_rows = result.get("runs")
        result_summary = {
            "git_commit": result.get("git_commit"),
            "strategy_version": result.get("strategy_version"),
            "strategy_count": result.get("strategy_count"),
            "strategy_account_count": result.get("strategy_account_count"),
            "signal_gate": result.get("signal_gate"),
            "signal_gate_target_strategy_id": result.get(
                "signal_gate_target_strategy_id"
            ),
            "signal_gate_trial_id": result.get("signal_gate_trial_id"),
            "strategy_logic": result.get("strategy_logic"),
            "cooperative_cpu_target_ratio": result.get("cooperative_cpu_target_ratio"),
            "cooperative_cpu_checkpoint_events": result.get(
                "cooperative_cpu_checkpoint_events"
            ),
            "archive_target_read_mib_per_second": (
                arguments.target_archive_read_mib_per_second
            ),
            "run_count": len(run_rows) if isinstance(run_rows, list) else 0,
            "ranking_eligible_strategy_ids": result.get("ranking_eligible_strategy_ids"),
            "profitability_status": result.get("profitability_status"),
        }
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
        error = {
            "type": type(caught).__name__,
            "message": "전략리그 전체 연구가 실행시간 상한을 넘었습니다.",
        }
    except KeyboardInterrupt as caught:
        status = "ABORTED_OPERATOR"
        exit_code = 130
        error = {
            "type": type(caught).__name__,
            "message": "사용자 또는 운영자가 중단했습니다.",
        }
    except ResearchTrialHistoryBlocked:
        pass
    except ResearchReplayResourceBusy:
        pass
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as caught:
        error = {"type": type(caught).__name__, "message": str(caught)}
    finally:
        partial_output.unlink(missing_ok=True)
        _release_replay_resource_lock(resource_lock_descriptor)
    if trial_history_decision["execution_allowed"] is True and research_started:
        record_status = (
            ResearchTrialStatus.COMPLETE
            if status == "PASS"
            else (
                ResearchTrialStatus.ABORTED
                if status.startswith("ABORTED")
                else ResearchTrialStatus.FAILED
            )
        )
        try:
            _append_trial_history(
                arguments.trial_history_catalog,
                ResearchTrialRecord(
                    trial_id=trial_record_id,
                    proposal=proposal,
                    status=record_status,
                    evidence_path=str(output if status == "PASS" else control_output),
                ),
            )
            trial_history_recorded = True
        except (OSError, TypeError, ValueError) as caught:
            trial_history_error = {
                "type": type(caught).__name__,
                "message": str(caught),
            }
            if status == "PASS":
                status = "FAIL_TRIAL_HISTORY_NOT_RECORDED"
                exit_code = 5
                error = trial_history_error
    completed_at = datetime.now(UTC)
    evidence = {
        "schema_version": 1,
        "status": status,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round(time.monotonic() - started_monotonic, 3),
        "runtime_url": arguments.runtime_url,
        "output_path": str(output),
        "output_written": output.is_file(),
        "partial_output_removed": not partial_output.exists(),
        "child": asdict(child_state),
        "runtime_safety": observations.report(),
        "result_summary": result_summary,
        "research_trial": {
            "signal_gate": str(arguments.signal_gate),
            "signal_gate_target_strategy_id": str(
                arguments.signal_gate_target_strategy_id
            ),
            "signal_gate_trial_id": (
                f"{arguments.signal_gate}:{arguments.signal_gate_target_strategy_id}"
            ),
            "strategy_logic": str(arguments.strategy_logic),
            "cooperative_cpu_target_ratio": float(arguments.target_cpu_ratio),
            "maximum_continuous_run_seconds": float(
                arguments.maximum_continuous_run_seconds
            ),
            "hard_duty_cycle_enforced": child_state.hard_duty_cycle_enabled,
            "archive_target_read_mib_per_second": float(
                arguments.target_archive_read_mib_per_second
            ),
            "live_ledger_io_priority_gate": True,
            "history_catalog": str(arguments.trial_history_catalog),
            "history_decision": trial_history_decision,
            "history_record_id": trial_record_id,
            "history_recorded": trial_history_recorded,
            "history_error": trial_history_error,
            "proposal": asdict(proposal),
        },
        "resource_lock": {
            "path": str(arguments.resource_lock),
            "acquired": resource_lock_acquired,
            "released": resource_lock_descriptor is not None,
            "single_archive_replay_enforced": True,
        },
        "error": error,
        "paper_safety": {
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_enabled": False,
            "wallet_paths_enabled": False,
            "runtime_ai_order_decision": False,
        },
    }
    _atomic_write_json(control_output, evidence)
    return exit_code, evidence


def parse_arguments() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "11개 전략·22개 독립 PAPER 계좌의 동결 archive 전수 replay를 "
            "LIVE 안전감시 아래 실행합니다."
        )
    )
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument(
        "--archive",
        type=Path,
        default=project_root / "data" / "market-parquet-v6" / "venue=BINANCE_USDM",
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=project_root / "evidence" / "STRATEGY_100_DATASET_MANIFEST.json",
    )
    parser.add_argument("--run-id", action="append")
    parser.add_argument("--maximum-events", type=int)
    parser.add_argument(
        "--target-cpu-ratio",
        type=float,
        default=_DEFAULT_RESEARCH_TARGET_CPU_RATIO,
        help="LIVE 우선을 위해 연구 자식 프로세스에 허용할 협조 CPU 목표 비율입니다.",
    )
    parser.add_argument("--maximum-continuous-run-seconds", type=float, default=0.05)
    parser.add_argument(
        "--target-archive-read-mib-per-second",
        type=float,
        default=DEFAULT_RESEARCH_ARCHIVE_READ_MIB_PER_SECOND,
        help="LIVE 원장 쓰기보다 낮게 조율할 archive 재검증 목표 속도입니다.",
    )
    parser.add_argument("--live-ledger-path", type=Path)
    parser.add_argument("--signal-gate", choices=SIGNAL_GATES, default=SIGNAL_GATE_NONE)
    parser.add_argument(
        "--signal-gate-target-strategy-id",
        choices=(*StrategyRegistry().strategy_ids, SIGNAL_GATE_TARGET_ALL),
        default=DEFAULT_STRATEGY_ID,
    )
    parser.add_argument(
        "--strategy-logic",
        choices=STRATEGY_LOGICS,
        default=STRATEGY_LOGIC_CURRENT,
    )
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8870")
    parser.add_argument("--request-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--max-consecutive-probe-errors", type=int, default=3)
    parser.add_argument("--max-queue-depth", type=int, default=64)
    parser.add_argument("--max-lag-p95-ms", type=float, default=500.0)
    parser.add_argument("--max-event-stall-seconds", type=float, default=30.0)
    parser.add_argument(
        "--planned-rotation-lock-grace-seconds",
        type=float,
        default=15.0,
    )
    parser.add_argument("--max-duration-seconds", type=float, default=28_800.0)
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--control-output", type=Path)
    arguments = parser.parse_args()
    arguments.project_root = arguments.project_root.resolve(strict=True)
    arguments.archive = arguments.archive.resolve(strict=True)
    arguments.dataset_manifest = arguments.dataset_manifest.resolve(strict=True)
    arguments.live_ledger_path = (
        arguments.live_ledger_path.resolve(strict=True)
        if arguments.live_ledger_path is not None
        else _default_live_ledger_path(arguments.project_root)
    )
    arguments.trial_history_catalog = arguments.trial_history_catalog.resolve()
    arguments.resource_lock = arguments.resource_lock.resolve()
    arguments.control_output = arguments.control_output or arguments.output.with_name(
        arguments.output.stem + "_LIVE_GUARD.json"
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
        or not 0 < arguments.maximum_continuous_run_seconds <= 0.1
        or arguments.target_archive_read_mib_per_second <= 0
        or (arguments.maximum_events is not None and arguments.maximum_events <= 0)
    ):
        parser.error("시간·감시·대기열·이벤트 상한은 올바른 양수여야 합니다.")
    return arguments


def main() -> None:
    arguments = parse_arguments()
    exit_code, evidence = run(arguments)
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
