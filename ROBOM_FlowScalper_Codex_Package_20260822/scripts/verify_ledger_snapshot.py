# 활성 원장을 온라인 스냅샷으로 복제하고 닫힌 사본에서만 전수검사한다.
"""런타임 안전감시를 포함한 SQLite 원장 무결성 검증 CLI다."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from backend.app.storage.integrity import (
    LedgerIntegrityError,
    RuntimeSafetyMonitor,
    RuntimeSafetyThresholds,
    RuntimeSafetyViolation,
    create_online_snapshot,
    fetch_dashboard_payload,
    parse_runtime_safety_sample,
    remove_snapshot,
    result_as_dict,
    verify_closed_snapshot,
)
from backend.app.storage.parquet import _apply_background_io_policy


def verify_ledger(arguments: argparse.Namespace) -> dict[str, object]:
    started_at = datetime.now(UTC)
    snapshot_dir = arguments.snapshot_dir.resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    descriptor, raw_snapshot_path = tempfile.mkstemp(
        prefix="flowscalper-ledger-integrity-",
        suffix=".sqlite3",
        dir=snapshot_dir,
    )
    os.close(descriptor)
    snapshot_path = Path(raw_snapshot_path)
    snapshot_path.unlink()
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
    dashboard_url = arguments.runtime_url.rstrip("/") + "/api/dashboard"
    monitor = RuntimeSafetyMonitor(
        lambda: parse_runtime_safety_sample(
            fetch_dashboard_payload(
                dashboard_url,
                timeout_seconds=thresholds.request_timeout_seconds,
            )
        ),
        thresholds=thresholds,
    )
    snapshot_result: dict[str, object] | None = None
    integrity_result: dict[str, object] | None = None
    status = "FAIL"
    error: dict[str, str] | None = None
    removed = False
    monitor_started = False
    try:
        monitor.start()
        monitor_started = True
        snapshot_result = result_as_dict(
            create_online_snapshot(
                arguments.source,
                snapshot_path,
                pages_per_step=arguments.backup_pages,
                step_sleep_seconds=arguments.backup_sleep_ms / 1_000,
                minimum_free_headroom_bytes=arguments.minimum_free_headroom_bytes,
                max_duration_seconds=arguments.max_backup_duration_seconds,
                max_progress_stall_seconds=(
                    arguments.max_backup_progress_stall_seconds
                ),
                safety=monitor,
            )
        )
        integrity_result = result_as_dict(
            verify_closed_snapshot(
                snapshot_path,
                progress_opcodes=arguments.check_progress_opcodes,
                progress_sleep_seconds=arguments.check_sleep_ms / 1_000,
                safety=monitor,
            )
        )
        monitor.set_stage("FINAL_RUNTIME_CHECK")
        monitor.stop()
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
        if monitor_started:
            try:
                monitor.stop()
            except RuntimeSafetyViolation as caught:
                if status == "PASS":
                    status = "ABORTED_RUNTIME_SAFETY"
                    error = {"type": type(caught).__name__, "message": str(caught)}
        if not arguments.keep_snapshot:
            remove_snapshot(snapshot_path)
            removed = not snapshot_path.exists()
    completed_at = datetime.now(UTC)
    return {
        "schema": "flowscalper.ledger_snapshot_integrity.v1",
        "status": status,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
        "source_path": str(arguments.source.resolve()),
        "snapshot_path": str(snapshot_path),
        "snapshot_kept": bool(arguments.keep_snapshot and snapshot_path.exists()),
        "temporary_snapshot_removed": removed,
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
            "minimum_free_headroom_bytes": arguments.minimum_free_headroom_bytes,
            "backup_pages": arguments.backup_pages,
            "backup_sleep_ms": arguments.backup_sleep_ms,
            "max_backup_duration_seconds": arguments.max_backup_duration_seconds,
            "max_backup_progress_stall_seconds": (
                arguments.max_backup_progress_stall_seconds
            ),
            "check_progress_opcodes": arguments.check_progress_opcodes,
            "check_sleep_ms": arguments.check_sleep_ms,
        },
        "snapshot": snapshot_result,
        "integrity": integrity_result,
        "runtime_monitor": monitor.report(),
        "error": error,
        "paper_safety": {
            "real_orders_enabled": False,
            "auth_required": False,
            "source_mutation_requested": False,
            "active_ledger_quick_check_requested": False,
        },
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="활성 SQLite를 증분 snapshot으로 복제해 닫힌 사본만 검사합니다."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8870")
    parser.add_argument("--backup-pages", type=int, default=64)
    parser.add_argument("--backup-sleep-ms", type=float, default=10.0)
    parser.add_argument("--max-backup-duration-seconds", type=float, default=300.0)
    parser.add_argument(
        "--max-backup-progress-stall-seconds",
        type=float,
        default=30.0,
    )
    parser.add_argument("--check-progress-opcodes", type=int, default=5_000)
    parser.add_argument("--check-sleep-ms", type=float, default=1.0)
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
    parser.add_argument("--keep-snapshot", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    _apply_background_io_policy()
    if sys.platform != "win32":
        try:
            os.nice(10)
        except OSError:
            pass
    result = verify_ledger(arguments)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        key: value for key, value in result.items() if key != "runtime_monitor"
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result["status"] != "PASS":
        if result["status"] == "ABORTED_RUNTIME_SAFETY":
            raise SystemExit(2)
        if result["status"] == "ABORTED_OPERATOR":
            raise SystemExit(130)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
