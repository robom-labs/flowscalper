# 대형 PAPER 원장을 잠시 닫고 APFS clone에서만 전수 무결성을 검증한다.
"""macOS LaunchAgent와 닫힌 SQLite clone을 이용한 안전 유지관리 CLI다."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from backend.app.storage.integrity import (
    LedgerIntegrityError,
    RuntimeSafetyMonitor,
    RuntimeSafetySample,
    RuntimeSafetyThresholds,
    RuntimeSafetyViolation,
    checkpoint_closed_ledger,
    create_closed_ledger_clone,
    fetch_dashboard_payload,
    parse_runtime_safety_sample,
    remove_snapshot,
    result_as_dict,
    runtime_safety_violations,
    transfer_closed_snapshot,
    verify_closed_snapshot,
)
from backend.app.storage.parquet import _apply_background_io_policy


class LaunchAgentController:
    """로드된 서비스를 충분한 종료 유예로 닫고 다시 복구한다."""

    def __init__(self, *, label: str, plist_path: Path, shutdown_timeout: float) -> None:
        self.label = label
        self.plist_path = plist_path.resolve()
        self.shutdown_timeout = shutdown_timeout
        self.domain = f"gui/{os.getuid()}"
        self.service_target = f"{self.domain}/{label}"

    def validate_contract(self) -> dict[str, object]:
        if sys.platform != "darwin":
            raise LedgerIntegrityError("LaunchAgent 유지관리는 macOS에서만 지원합니다.")
        if not self.plist_path.is_file():
            raise LedgerIntegrityError(f"LaunchAgent plist가 없습니다: {self.plist_path}")
        with self.plist_path.open("rb") as stream:
            payload = plistlib.load(stream)
        if not isinstance(payload, dict):
            raise LedgerIntegrityError("LaunchAgent plist가 dictionary가 아닙니다.")
        if payload.get("Label") != self.label:
            raise LedgerIntegrityError("LaunchAgent Label이 요청과 다릅니다.")
        exit_timeout = int(payload.get("ExitTimeOut", 0))
        if exit_timeout < int(self.shutdown_timeout):
            raise LedgerIntegrityError(
                "LaunchAgent ExitTimeOut이 안전 종료 상한보다 짧습니다: "
                f"configured={exit_timeout}, required={self.shutdown_timeout}"
            )
        if payload.get("KeepAlive") is not True or payload.get("RunAtLoad") is not True:
            raise LedgerIntegrityError("LaunchAgent 자동 복구 계약이 활성화되지 않았습니다.")
        return {
            "label": self.label,
            "plist_path": str(self.plist_path),
            "exit_timeout_seconds": exit_timeout,
            "keep_alive": True,
            "run_at_load": True,
        }

    def loaded(self) -> bool:
        result = _command(
            ["/bin/launchctl", "print", self.service_target], check=False
        )
        return result.returncode == 0

    def running(self) -> bool:
        result = _command(["/bin/launchctl", "print", self.service_target], check=False)
        return result.returncode == 0 and "\tpid = " in result.stdout

    def stop_gracefully(self, source_path: Path) -> dict[str, object]:
        if not self.loaded() or not self.running():
            raise LedgerIntegrityError("LaunchAgent가 실행 중인 기준선이 아닙니다.")
        started = time.monotonic()
        try:
            _command(["/bin/launchctl", "bootout", self.service_target])
            deadline = started + self.shutdown_timeout
            last_holders: tuple[int, ...] = ()
            while time.monotonic() < deadline:
                last_holders = _open_pids(source_path)
                if not self.running() and not last_holders:
                    break
                time.sleep(0.25)
            else:
                raise LedgerIntegrityError(
                    "PAPER 저장 종료가 유예시간 안에 끝나지 않았습니다: "
                    f"holders={last_holders}"
                )
        except BaseException:
            self.ensure_started()
            raise
        return {
            "stop_command": "launchctl bootout",
            "forced_kill_requested": False,
            "source_open_pids_after": list(_open_pids(source_path)),
            "duration_seconds": round(time.monotonic() - started, 3),
        }

    def ensure_started(self) -> None:
        if not self.loaded():
            _command(["/bin/launchctl", "bootstrap", self.domain, str(self.plist_path)])
        _command(["/bin/launchctl", "enable", self.service_target])
        _command(["/bin/launchctl", "kickstart", self.service_target], check=False)


def _command(
    arguments: list[str],
    *,
    check: bool = True,
    timeout: float = 70.0,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        arguments,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise LedgerIntegrityError(
            f"유지관리 명령 실패({arguments[1]}): {detail or result.returncode}"
        )
    return result


def _open_pids(source_path: Path) -> tuple[int, ...]:
    result = _command(
        ["/usr/sbin/lsof", "-t", "--", str(source_path.resolve())],
        check=False,
        timeout=5.0,
    )
    if result.returncode not in {0, 1}:
        raise LedgerIntegrityError(f"lsof 원장 handle 확인 실패: {result.stderr.strip()}")
    return tuple(sorted({int(row) for row in result.stdout.splitlines() if row.strip()}))


def _probe(runtime_url: str, timeout_seconds: float) -> RuntimeSafetySample:
    return parse_runtime_safety_sample(
        fetch_dashboard_payload(
            runtime_url.rstrip("/") + "/api/dashboard",
            timeout_seconds=timeout_seconds,
        )
    )


def _wait_for_recovered_runtime(
    runtime_url: str,
    *,
    expected_run_id: str,
    thresholds: RuntimeSafetyThresholds,
    timeout_seconds: float,
) -> RuntimeSafetySample:
    deadline = time.monotonic() + timeout_seconds
    last_error = "응답 없음"
    while time.monotonic() < deadline:
        try:
            sample = _probe(runtime_url, thresholds.request_timeout_seconds)
            violations = runtime_safety_violations(sample, sample, thresholds)
            if sample.run_id != expected_run_id:
                last_error = f"Run 불일치: {sample.run_id}"
            elif violations:
                last_error = ", ".join(violations)
            else:
                return sample
        except (OSError, RuntimeSafetyViolation, ValueError) as error:
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(0.5)
    raise LedgerIntegrityError(f"재기동 후 안전한 동일 Run 복구 실패: {last_error}")


def _validate_initial_runtime(
    violations: tuple[str, ...],
    *,
    allow_failed_runtime_recovery: bool,
) -> dict[str, object]:
    """멈춘 소비경로 복구 시에만 이미 활성인 fail-closed 잠금을 허용한다."""

    if not violations:
        return {
            "override_requested": allow_failed_runtime_recovery,
            "override_applied": False,
            "violations": [],
        }
    allowed_recovery_violations = {"ENTRY_LOCKED", "QUEUE_LIMIT_EXCEEDED"}
    disallowed = sorted(set(violations) - allowed_recovery_violations)
    if not allow_failed_runtime_recovery or disallowed:
        detail = ", ".join(violations)
        if disallowed:
            detail += f" · 복구 허용 밖: {', '.join(disallowed)}"
        raise RuntimeSafetyViolation("유지관리 기준선 실패: " + detail)
    return {
        "override_requested": True,
        "override_applied": True,
        "violations": list(violations),
        "allowed_violations": sorted(allowed_recovery_violations),
        "reason": "FAILED_CONSUMER_FAIL_CLOSED_RECOVERY",
    }


def verify_with_maintenance(arguments: argparse.Namespace) -> dict[str, object]:
    started_at = datetime.now(UTC)
    source_path = arguments.source.resolve()
    snapshot_dir = arguments.snapshot_dir.resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    descriptor, raw_snapshot_path = tempfile.mkstemp(
        prefix="flowscalper-closed-ledger-",
        suffix=".sqlite3",
        dir=snapshot_dir,
    )
    os.close(descriptor)
    snapshot_path = Path(raw_snapshot_path)
    snapshot_path.unlink()
    verification_dir = arguments.verification_dir.resolve()
    verification_dir.mkdir(parents=True, exist_ok=True)
    verification_descriptor, raw_verification_path = tempfile.mkstemp(
        prefix="flowscalper-ledger-verification-",
        suffix=".sqlite3",
        dir=verification_dir,
    )
    os.close(verification_descriptor)
    verification_path = Path(raw_verification_path)
    verification_path.unlink()
    thresholds = RuntimeSafetyThresholds(
        max_queue_depth=arguments.max_queue_depth,
        max_lag_p95_ms=arguments.max_lag_p95_ms,
        max_event_stall_seconds=arguments.max_event_stall_seconds,
        poll_seconds=arguments.poll_seconds,
        request_timeout_seconds=arguments.request_timeout_seconds,
        max_consecutive_probe_errors=arguments.max_consecutive_probe_errors,
        planned_rotation_lock_grace_seconds=(
            arguments.planned_rotation_lock_grace_seconds
        ),
    )
    controller = LaunchAgentController(
        label=arguments.service_label,
        plist_path=arguments.plist,
        shutdown_timeout=arguments.shutdown_timeout_seconds,
    )
    status = "FAIL"
    error: dict[str, str] | None = None
    baseline: RuntimeSafetySample | None = None
    recovered: RuntimeSafetySample | None = None
    launch_contract: dict[str, object] | None = None
    baseline_recovery_override: dict[str, object] | None = None
    shutdown_result: dict[str, object] | None = None
    checkpoint_result: dict[str, object] | None = None
    clone_result: dict[str, object] | None = None
    transfer_result: dict[str, object] | None = None
    integrity_result: dict[str, object] | None = None
    recovery_result: dict[str, object] | None = None
    monitor: RuntimeSafetyMonitor | None = None
    monitor_started = False
    maintenance_started = False
    service_restart_requested = False
    snapshot_removed = False
    verification_removed = False
    downtime_started: float | None = None
    downtime_seconds: float | None = None
    try:
        launch_contract = controller.validate_contract()
        baseline = _probe(arguments.runtime_url, thresholds.request_timeout_seconds)
        initial_violations = runtime_safety_violations(baseline, baseline, thresholds)
        baseline_recovery_override = _validate_initial_runtime(
            initial_violations,
            allow_failed_runtime_recovery=arguments.allow_failed_runtime_recovery,
        )
        downtime_started = time.monotonic()
        maintenance_started = True
        shutdown_result = controller.stop_gracefully(source_path)
        if _open_pids(source_path):
            raise LedgerIntegrityError("종료 후에도 원장을 연 process가 있습니다.")
        checkpoint_result = result_as_dict(checkpoint_closed_ledger(source_path))
        clone_result = result_as_dict(
            create_closed_ledger_clone(
                source_path,
                snapshot_path,
                minimum_free_headroom_bytes=arguments.minimum_free_headroom_bytes,
            )
        )
        transfer_result = result_as_dict(
            transfer_closed_snapshot(
                snapshot_path,
                verification_path,
                minimum_free_headroom_bytes=(
                    arguments.minimum_verification_headroom_bytes
                ),
                chunk_bytes=arguments.transfer_chunk_bytes,
                chunk_sleep_seconds=arguments.transfer_chunk_sleep_ms / 1_000,
            )
        )
        remove_snapshot(snapshot_path)
        snapshot_removed = not snapshot_path.exists()
        controller.ensure_started()
        service_restart_requested = True
        recovered = _wait_for_recovered_runtime(
            arguments.runtime_url,
            expected_run_id=baseline.run_id,
            thresholds=thresholds,
            timeout_seconds=arguments.startup_timeout_seconds,
        )
        downtime_seconds = round(time.monotonic() - downtime_started, 3)
        recovery_result = {
            "same_run_recovered": recovered.run_id == baseline.run_id,
            "previous_process_uptime_seconds": baseline.process_uptime_seconds,
            "recovered_process_uptime_seconds": recovered.process_uptime_seconds,
            "process_restarted": (
                recovered.process_uptime_seconds < baseline.process_uptime_seconds
            ),
            "downtime_seconds": downtime_seconds,
            "sample": asdict(recovered),
        }
        if not recovery_result["process_restarted"]:
            raise LedgerIntegrityError("유지관리 종료·복구를 확인하지 못했습니다.")
        monitor = RuntimeSafetyMonitor(
            lambda: _probe(arguments.runtime_url, thresholds.request_timeout_seconds),
            thresholds=thresholds,
        )
        monitor.start()
        monitor_started = True
        integrity_result = result_as_dict(
            verify_closed_snapshot(
                verification_path,
                progress_opcodes=arguments.check_progress_opcodes,
                progress_sleep_seconds=arguments.check_sleep_ms / 1_000,
                safety=monitor,
            )
        )
        monitor.set_stage("FINAL_RUNTIME_CHECK")
        monitor.stop()
        monitor_started = False
        status = "PASS"
    except RuntimeSafetyViolation as caught:
        status = "ABORTED_RUNTIME_SAFETY"
        error = {"type": type(caught).__name__, "message": str(caught)}
    except KeyboardInterrupt as caught:
        status = "ABORTED_OPERATOR"
        error = {
            "type": type(caught).__name__,
            "message": "사용자 또는 운영자가 중단했습니다.",
        }
    except (LedgerIntegrityError, OSError, ValueError) as caught:
        status = "FAIL"
        error = {"type": type(caught).__name__, "message": str(caught)}
    finally:
        if maintenance_started:
            try:
                controller.ensure_started()
                service_restart_requested = True
            except (LedgerIntegrityError, OSError) as restart_error:
                status = "FAIL_SERVICE_RECOVERY"
                error = {
                    "type": type(restart_error).__name__,
                    "message": str(restart_error),
                }
        if monitor_started and monitor is not None:
            try:
                monitor.stop()
            except RuntimeSafetyViolation as caught:
                if status == "PASS":
                    status = "ABORTED_RUNTIME_SAFETY"
                    error = {"type": type(caught).__name__, "message": str(caught)}
        preserve_failed = bool(arguments.keep_failed_snapshot and status != "PASS")
        if not arguments.keep_snapshot and not preserve_failed:
            remove_snapshot(snapshot_path)
            snapshot_removed = not snapshot_path.exists()
            if verification_path != snapshot_path:
                remove_snapshot(verification_path)
                verification_removed = not verification_path.exists()
    completed_at = datetime.now(UTC)
    return {
        "schema": "flowscalper.macos_ledger_maintenance_integrity.v1",
        "status": status,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
        "source_path": str(source_path),
        "snapshot_path": str(snapshot_path),
        "snapshot_kept": snapshot_path.exists(),
        "temporary_snapshot_removed": snapshot_removed,
        "verification_path": str(verification_path),
        "verification_copy_kept": verification_path.exists(),
        "temporary_verification_copy_removed": verification_removed,
        "launch_agent_contract": launch_contract,
        "baseline_recovery_override": baseline_recovery_override,
        "baseline": asdict(baseline) if baseline is not None else None,
        "shutdown": shutdown_result,
        "closed_wal_checkpoint": checkpoint_result,
        "closed_clone": clone_result,
        "cross_device_transfer": transfer_result,
        "handoff_order": (
            [
                "GRACEFUL_STOP",
                "CLOSED_WAL_CHECKPOINT",
                "APFS_CLONE",
                "CROSS_DEVICE_TRANSFER_AND_SHA256",
                "IMMUTABLE_RELEASE_RESTART",
                "SAME_RUN_RECOVERY",
                "CROSS_DEVICE_QUICK_CHECK_WITH_LIVE_MONITOR",
            ]
            if transfer_result is not None
            else None
        ),
        "service_restart_requested": service_restart_requested,
        "recovery": recovery_result,
        "integrity": integrity_result,
        "runtime_monitor": monitor.report() if monitor is not None else None,
        "error": error,
        "paper_safety": {
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_requested": False,
            "api_key_requested": False,
            "wallet_requested": False,
            "active_ledger_quick_check_requested": False,
            "snapshot_only_quick_check_requested": True,
            "forced_kill_requested": False,
        },
        "thresholds": {
            "max_queue_depth": thresholds.max_queue_depth,
            "max_lag_p95_ms": thresholds.max_lag_p95_ms,
            "max_event_stall_seconds": thresholds.max_event_stall_seconds,
            "poll_seconds": thresholds.poll_seconds,
            "request_timeout_seconds": thresholds.request_timeout_seconds,
            "max_consecutive_probe_errors": thresholds.max_consecutive_probe_errors,
            "planned_rotation_lock_grace_seconds": (
                thresholds.planned_rotation_lock_grace_seconds
            ),
            "shutdown_timeout_seconds": arguments.shutdown_timeout_seconds,
            "startup_timeout_seconds": arguments.startup_timeout_seconds,
            "minimum_free_headroom_bytes": arguments.minimum_free_headroom_bytes,
            "minimum_verification_headroom_bytes": (
                arguments.minimum_verification_headroom_bytes
            ),
            "transfer_chunk_bytes": arguments.transfer_chunk_bytes,
            "transfer_chunk_sleep_ms": arguments.transfer_chunk_sleep_ms,
            "check_progress_opcodes": arguments.check_progress_opcodes,
            "check_sleep_ms": arguments.check_sleep_ms,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "PAPER LaunchAgent를 안전 정지하고 닫힌 APFS clone만 전수검사합니다."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verification-dir", type=Path, required=True)
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8870")
    parser.add_argument("--service-label", default="kr.robom.flowscalper")
    parser.add_argument(
        "--plist",
        type=Path,
        default=(
            Path.home() / "Library" / "LaunchAgents" / "kr.robom.flowscalper.plist"
        ),
    )
    parser.add_argument("--shutdown-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--startup-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--check-progress-opcodes", type=int, default=5_000)
    parser.add_argument("--check-sleep-ms", type=float, default=0.0)
    parser.add_argument("--max-queue-depth", type=int, default=64)
    parser.add_argument("--max-lag-p95-ms", type=float, default=500.0)
    parser.add_argument("--max-event-stall-seconds", type=float, default=15.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--max-consecutive-probe-errors", type=int, default=3)
    parser.add_argument(
        "--planned-rotation-lock-grace-seconds",
        type=float,
        default=15.0,
    )
    parser.add_argument(
        "--minimum-free-headroom-bytes",
        type=int,
        default=5 * 1024**3,
    )
    parser.add_argument(
        "--minimum-verification-headroom-bytes",
        type=int,
        default=2 * 1024**3,
    )
    parser.add_argument("--transfer-chunk-bytes", type=int, default=4 * 1024**2)
    parser.add_argument("--transfer-chunk-sleep-ms", type=float, default=50.0)
    parser.add_argument("--keep-snapshot", action="store_true")
    parser.add_argument("--keep-failed-snapshot", action="store_true")
    parser.add_argument(
        "--allow-failed-runtime-recovery",
        action="store_true",
        help=(
            "포지션·실주문·인증 문제가 없고 기존 ENTRY_LOCKED 또는 queue 포화만 "
            "있는 소비경로 사고를 단일 유지관리 전환으로 복구합니다."
        ),
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    _apply_background_io_policy()
    try:
        os.nice(10)
    except OSError:
        pass
    result = verify_with_maintenance(arguments)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        key: value
        for key, value in result.items()
        if key not in {"runtime_monitor", "baseline"}
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result["status"] == "PASS":
        return
    if result["status"] == "ABORTED_RUNTIME_SAFETY":
        raise SystemExit(2)
    if result["status"] == "ABORTED_OPERATOR":
        raise SystemExit(130)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
