# 활성 SQLite 원장을 건드리지 않고 닫힌 스냅샷에서 무결성을 검증한다.
"""대형 PAPER 원장의 온라인 스냅샷과 런타임 안전검사를 제공한다."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Mapping
from ctypes import CDLL, c_char_p, c_int, get_errno
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen


class LedgerIntegrityError(RuntimeError):
    """스냅샷 생성 또는 무결성 검증 실패를 명시한다."""


class RuntimeSafetyViolation(LedgerIntegrityError):
    """검증 부하 중 LIVE PAPER 안전조건이 깨지면 작업을 중단한다."""


class SafetyCheckpoint(Protocol):
    """장시간 저장 작업이 협력적으로 확인할 안전감시 계약이다."""

    def set_stage(self, stage: str) -> None: ...

    def checkpoint(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RuntimeSafetyThresholds:
    max_queue_depth: int = 64
    max_lag_p95_ms: float = 500.0
    max_event_stall_seconds: float = 15.0
    poll_seconds: float = 1.0
    request_timeout_seconds: float = 2.0
    max_consecutive_probe_errors: int = 3
    planned_rotation_lock_grace_seconds: float = 15.0

    def __post_init__(self) -> None:
        if self.max_queue_depth < 0:
            raise ValueError("max_queue_depth는 0 이상이어야 합니다.")
        if self.max_lag_p95_ms <= 0:
            raise ValueError("max_lag_p95_ms는 양수여야 합니다.")
        if self.max_event_stall_seconds <= 0:
            raise ValueError("max_event_stall_seconds는 양수여야 합니다.")
        if (
            self.poll_seconds <= 0
            or self.request_timeout_seconds <= 0
            or self.planned_rotation_lock_grace_seconds <= 0
        ):
            raise ValueError("감시 간격과 요청 제한시간은 양수여야 합니다.")
        if self.max_consecutive_probe_errors <= 0:
            raise ValueError("연속 감시 요청 실패 상한은 양수여야 합니다.")


@dataclass(frozen=True, slots=True)
class RuntimeSafetySample:
    observed_at: str
    run_id: str
    operation_state: str
    market_data_state: str
    execution_state: str
    event_count: int
    queue_depth: int
    queue_capacity: int
    lag_p95_ms: float
    critical_lag_threshold_ms: float
    reconnects: int
    planned_rotations: int
    unplanned_reconnects: int
    sequence_gaps: int
    resyncs: int
    dropped_events: int
    persistence_fault_count: int
    persistence_buffer_dropped: int
    critical_lag_incident_count: int
    critical_lag_active: bool
    entry_locked: bool
    position_count: int
    real_orders_enabled: bool
    auth_required: bool
    storage_entry_allowed: bool
    process_uptime_seconds: float
    last_error: str | None


@dataclass(frozen=True, slots=True)
class OnlineSnapshotResult:
    source_path: str
    snapshot_path: str
    source_size_bytes: int
    snapshot_size_bytes: int
    source_page_size: int
    source_page_count_at_start: int
    snapshot_page_count: int
    user_version: int
    source_journal_mode: str
    backup_iterations: int
    backup_restart_count: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ClosedLedgerCheckpointResult:
    source_path: str
    busy: int
    log_frame_count: int
    checkpointed_frame_count: int
    wal_size_bytes_after: int
    page_count: int
    user_version: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ClosedLedgerCloneResult:
    source_path: str
    snapshot_path: str
    source_size_bytes: int
    snapshot_size_bytes: int
    device_id: int
    free_bytes_before: int
    free_bytes_after: int
    clone_api: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ClosedSnapshotTransferResult:
    source_path: str
    verification_path: str
    source_device_id: int
    verification_device_id: int
    copied_bytes: int
    sha256: str
    verification_sha256: str
    free_bytes_before: int
    free_bytes_after: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class SnapshotIntegrityResult:
    snapshot_path: str
    quick_check: str
    quick_check_rows: tuple[str, ...]
    foreign_key_violation_count: int
    foreign_key_violation_examples: tuple[tuple[object, ...], ...]
    page_count: int
    freelist_count: int
    user_version: int
    table_count: int
    duration_seconds: float


def fetch_dashboard_payload(url: str, *, timeout_seconds: float) -> dict[str, object]:
    """localhost 대시보드를 인증 헤더 없이 읽는다."""

    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeSafetyViolation(f"대시보드 HTTP 상태가 {response.status}입니다.")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeSafetyViolation("대시보드 응답이 객체가 아닙니다.")
    return payload


def parse_runtime_safety_sample(payload: Mapping[str, object]) -> RuntimeSafetySample:
    """대시보드 응답에서 원장검사 안전에 필요한 값만 엄격하게 읽는다."""

    status = _mapping(payload.get("status"), "status")
    operation = _mapping(payload.get("operation_status"), "operation_status")
    system = _mapping(payload.get("system"), "system")
    position_count = int(payload.get("position") is not None)
    position_count += len(_sequence(payload.get("league_positions"), "league_positions"))
    return RuntimeSafetySample(
        observed_at=datetime.now(UTC).isoformat(),
        run_id=_string(status.get("run_id"), "status.run_id"),
        operation_state=_string(operation.get("state"), "operation_status.state"),
        market_data_state=_string(status.get("market_data_state"), "status.market_data_state"),
        execution_state=_string(status.get("execution_state"), "status.execution_state"),
        event_count=_integer(system.get("event_count"), "system.event_count"),
        queue_depth=_integer(system.get("queue_depth"), "system.queue_depth"),
        queue_capacity=_integer(system.get("queue_capacity"), "system.queue_capacity"),
        lag_p95_ms=_number(system.get("lag_p95_ms"), "system.lag_p95_ms"),
        critical_lag_threshold_ms=_number(
            system.get("critical_lag_threshold_ms"), "system.critical_lag_threshold_ms"
        ),
        reconnects=_integer(system.get("reconnects"), "system.reconnects"),
        planned_rotations=_integer(system.get("planned_rotations"), "system.planned_rotations"),
        unplanned_reconnects=_integer(
            system.get("unplanned_reconnects"), "system.unplanned_reconnects"
        ),
        sequence_gaps=_integer(system.get("sequence_gaps"), "system.sequence_gaps"),
        resyncs=_integer(system.get("resyncs"), "system.resyncs"),
        dropped_events=_integer(system.get("dropped_events"), "system.dropped_events"),
        persistence_fault_count=_integer(
            system.get("persistence_fault_count"), "system.persistence_fault_count"
        ),
        persistence_buffer_dropped=_integer(
            system.get("persistence_buffer_dropped"),
            "system.persistence_buffer_dropped",
        ),
        critical_lag_incident_count=_integer(
            system.get("critical_lag_incident_count"),
            "system.critical_lag_incident_count",
        ),
        critical_lag_active=_boolean(
            system.get("critical_lag_active"), "system.critical_lag_active"
        ),
        entry_locked=_boolean(system.get("entry_locked"), "system.entry_locked"),
        position_count=position_count,
        real_orders_enabled=_boolean(
            status.get("real_orders_enabled"), "status.real_orders_enabled"
        ),
        auth_required=_boolean(status.get("auth_required"), "status.auth_required"),
        storage_entry_allowed=_boolean(
            system.get("storage_entry_allowed"), "system.storage_entry_allowed"
        ),
        process_uptime_seconds=_number(
            system.get("process_uptime_seconds"), "system.process_uptime_seconds"
        ),
        last_error=(
            None
            if system.get("last_error") is None
            else _string(system.get("last_error"), "system.last_error")
        ),
    )


def runtime_safety_violations(
    baseline: RuntimeSafetySample,
    sample: RuntimeSafetySample,
    thresholds: RuntimeSafetyThresholds,
    *,
    allow_planned_rotation_transition: bool = False,
) -> tuple[str, ...]:
    """기준선 이후 새로 생긴 LIVE 안전 위반을 원인 코드로 반환한다."""

    violations: list[str] = []
    if sample.run_id != baseline.run_id:
        violations.append("RUN_CHANGED")
    if sample.process_uptime_seconds < baseline.process_uptime_seconds:
        violations.append("PROCESS_RESTARTED")
    planned_rotation_waiting = bool(
        allow_planned_rotation_transition
        and sample.operation_state == "SAFETY_WAITING"
        and sample.entry_locked
    )
    if sample.operation_state != "RUNNING" and not planned_rotation_waiting:
        violations.append("OPERATION_NOT_RUNNING")
    if sample.market_data_state != "LIVE":
        violations.append("MARKET_NOT_LIVE")
    if sample.execution_state != "PAPER":
        violations.append("EXECUTION_NOT_PAPER")
    if sample.real_orders_enabled:
        violations.append("REAL_ORDERS_ENABLED")
    if sample.auth_required:
        violations.append("AUTH_REQUIRED")
    if not sample.storage_entry_allowed:
        violations.append("STORAGE_ENTRY_BLOCKED")
    if sample.entry_locked and not allow_planned_rotation_transition:
        violations.append("ENTRY_LOCKED")
    if sample.position_count:
        violations.append("POSITION_OPENED")
    if sample.last_error is not None:
        violations.append("RUNTIME_ERROR")
    if sample.queue_depth > thresholds.max_queue_depth:
        violations.append("QUEUE_LIMIT_EXCEEDED")
    lag_limit = min(thresholds.max_lag_p95_ms, sample.critical_lag_threshold_ms)
    if sample.lag_p95_ms > lag_limit:
        violations.append("LAG_LIMIT_EXCEEDED")
    if sample.critical_lag_active:
        violations.append("CRITICAL_LAG_ACTIVE")
    counter_fields = (
        ("UNPLANNED_RECONNECT", baseline.unplanned_reconnects, sample.unplanned_reconnects),
        ("SEQUENCE_GAP", baseline.sequence_gaps, sample.sequence_gaps),
        ("RESYNC", baseline.resyncs, sample.resyncs),
        ("EVENT_DROP", baseline.dropped_events, sample.dropped_events),
        (
            "PERSISTENCE_FAULT",
            baseline.persistence_fault_count,
            sample.persistence_fault_count,
        ),
        (
            "PERSISTENCE_BUFFER_DROP",
            baseline.persistence_buffer_dropped,
            sample.persistence_buffer_dropped,
        ),
        (
            "CRITICAL_LAG_INCIDENT",
            baseline.critical_lag_incident_count,
            sample.critical_lag_incident_count,
        ),
    )
    violations.extend(code for code, before, current in counter_fields if current > before)
    planned_delta = sample.planned_rotations - baseline.planned_rotations
    reconnect_delta = sample.reconnects - baseline.reconnects
    planned_transition_counts = bool(
        allow_planned_rotation_transition
        and planned_delta == reconnect_delta + 1
        and planned_delta > 0
    )
    if (
        planned_delta < 0
        or reconnect_delta < 0
        or (reconnect_delta != planned_delta and not planned_transition_counts)
    ):
        violations.append("RECONNECT_NOT_PLANNED_ROTATION")
    return tuple(dict.fromkeys(violations))


class RuntimeSafetyMonitor:
    """별도 thread에서 LIVE 상태를 관찰하고 장시간 검증을 fail-closed한다."""

    def __init__(
        self,
        probe: Callable[[], RuntimeSafetySample],
        *,
        thresholds: RuntimeSafetyThresholds | None = None,
        allowed_violation_codes: frozenset[str] | None = None,
    ) -> None:
        self._probe = probe
        self.thresholds = thresholds or RuntimeSafetyThresholds()
        self.allowed_violation_codes = allowed_violation_codes or frozenset()
        # 수동 진입 일시정지는 시장 관찰 상태와 진입 잠금만 예외로 취급한다.
        unsupported_allowed_codes = self.allowed_violation_codes - {
            "ENTRY_LOCKED",
            "OPERATION_NOT_RUNNING",
        }
        if unsupported_allowed_codes:
            raise ValueError(
                "허용할 수 없는 런타임 안전 예외: " + ", ".join(sorted(unsupported_allowed_codes))
            )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stage = "NOT_STARTED"
        self._baseline: RuntimeSafetySample | None = None
        self._latest: RuntimeSafetySample | None = None
        self._samples: list[RuntimeSafetySample] = []
        self._violations: list[str] = []
        self._last_event_count = 0
        self._last_event_progress_monotonic = 0.0
        self._probe_error: str | None = None
        self._probe_error_count = 0
        self._consecutive_probe_errors = 0
        self._maximum_consecutive_probe_errors = 0
        self._probe_error_examples: list[str] = []
        self._last_planned_rotations = 0
        self._planned_lock_deadline_monotonic = 0.0

    def start(self) -> RuntimeSafetySample:
        if self._thread is not None:
            raise RuntimeError("런타임 안전감시는 한 번만 시작할 수 있습니다.")
        baseline = self._probe()
        initial = tuple(
            code
            for code in runtime_safety_violations(
                baseline,
                baseline,
                self.thresholds,
            )
            if code not in self.allowed_violation_codes
        )
        if initial:
            raise RuntimeSafetyViolation("초기 안전조건 실패: " + ", ".join(initial))
        with self._lock:
            self._baseline = baseline
            self._latest = baseline
            self._samples.append(baseline)
            self._last_event_count = baseline.event_count
            self._last_event_progress_monotonic = time.monotonic()
            self._last_planned_rotations = baseline.planned_rotations
            self._stage = "BASELINE_READY"
        self._thread = threading.Thread(
            target=self._run,
            name="ledger-integrity-runtime-monitor",
            daemon=True,
        )
        self._thread.start()
        return baseline

    def set_stage(self, stage: str) -> None:
        with self._lock:
            self._stage = stage

    def checkpoint(self) -> None:
        with self._lock:
            violations = tuple(self._violations)
            probe_error = self._probe_error
        if probe_error is not None:
            raise RuntimeSafetyViolation(f"런타임 감시 요청 실패: {probe_error}")
        if violations:
            raise RuntimeSafetyViolation("런타임 안전조건 실패: " + ", ".join(violations))

    def stop(self) -> None:
        if self._thread is not None and not self._stop.is_set():
            for _attempt in range(self.thresholds.max_consecutive_probe_errors):
                try:
                    self._record(self._probe())
                    break
                except BaseException as error:
                    self._record_probe_error(error)
                    with self._lock:
                        fatal_probe_error = self._probe_error is not None
                    if fatal_probe_error:
                        break
                    time.sleep(min(0.2, self.thresholds.poll_seconds))
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(2.0, self.thresholds.request_timeout_seconds + 1.0))
        self.checkpoint()

    def report(self) -> dict[str, object]:
        with self._lock:
            samples = list(self._samples)
            baseline = self._baseline
            latest = self._latest
            violations = list(self._violations)
            probe_error = self._probe_error
            probe_error_count = self._probe_error_count
            maximum_consecutive_probe_errors = self._maximum_consecutive_probe_errors
            probe_error_examples = list(self._probe_error_examples)
            stage = self._stage
        return {
            "stage": stage,
            "sample_count": len(samples),
            "baseline": asdict(baseline) if baseline is not None else None,
            "latest": asdict(latest) if latest is not None else None,
            "event_delta": (
                latest.event_count - baseline.event_count
                if baseline is not None and latest is not None
                else 0
            ),
            "maximum_queue_depth": max((sample.queue_depth for sample in samples), default=0),
            "maximum_lag_p95_ms": max((sample.lag_p95_ms for sample in samples), default=0.0),
            "allowed_violation_codes": sorted(self.allowed_violation_codes),
            "violations": violations,
            "probe_error": probe_error,
            "probe_error_count": probe_error_count,
            "maximum_consecutive_probe_errors": maximum_consecutive_probe_errors,
            "probe_error_examples": probe_error_examples,
            "samples": [asdict(sample) for sample in samples],
        }

    def _run(self) -> None:
        while not self._stop.wait(self.thresholds.poll_seconds):
            try:
                sample = self._probe()
                self._record(sample)
            except BaseException as error:
                self._record_probe_error(error)

    def _record_probe_error(self, error: BaseException) -> None:
        detail = f"{type(error).__name__}: {error}"
        with self._lock:
            self._probe_error_count += 1
            self._consecutive_probe_errors += 1
            self._maximum_consecutive_probe_errors = max(
                self._maximum_consecutive_probe_errors,
                self._consecutive_probe_errors,
            )
            if len(self._probe_error_examples) < 10:
                self._probe_error_examples.append(detail)
            if self._consecutive_probe_errors >= self.thresholds.max_consecutive_probe_errors:
                self._probe_error = detail
                self._stop.set()

    def _record(self, sample: RuntimeSafetySample) -> None:
        now = time.monotonic()
        with self._lock:
            self._consecutive_probe_errors = 0
            baseline = self._baseline
            if baseline is None:
                self._probe_error = "감시 기준선이 없습니다."
                self._stop.set()
                return
            if sample.event_count > self._last_event_count:
                self._last_event_count = sample.event_count
                self._last_event_progress_monotonic = now
            elif (
                now - self._last_event_progress_monotonic > self.thresholds.max_event_stall_seconds
            ):
                self._violations.append("EVENT_STREAM_STALLED")
            if sample.planned_rotations > self._last_planned_rotations:
                self._last_planned_rotations = sample.planned_rotations
                self._planned_lock_deadline_monotonic = (
                    now + self.thresholds.planned_rotation_lock_grace_seconds
                )
            planned_delta = sample.planned_rotations - baseline.planned_rotations
            reconnect_delta = sample.reconnects - baseline.reconnects
            allow_planned_transition = bool(
                planned_delta > 0
                and planned_delta in {reconnect_delta, reconnect_delta + 1}
                and now <= self._planned_lock_deadline_monotonic
            )
            self._latest = sample
            self._samples.append(sample)
            self._violations.extend(
                code
                for code in runtime_safety_violations(
                    baseline,
                    sample,
                    self.thresholds,
                    allow_planned_rotation_transition=allow_planned_transition,
                )
                if code not in self.allowed_violation_codes
            )
            self._violations = list(dict.fromkeys(self._violations))
            if self._violations:
                self._stop.set()


def create_online_snapshot(
    source_path: Path,
    snapshot_path: Path,
    *,
    pages_per_step: int = 64,
    step_sleep_seconds: float = 0.01,
    minimum_free_headroom_bytes: int = 5 * 1024**3,
    max_duration_seconds: float = 300.0,
    max_progress_stall_seconds: float = 30.0,
    safety: SafetyCheckpoint | None = None,
) -> OnlineSnapshotResult:
    """Online Backup API로 짧은 읽기 구간만 사용하는 닫힌 사본을 만든다."""

    if pages_per_step <= 0:
        raise ValueError("pages_per_step은 양수여야 합니다.")
    if step_sleep_seconds < 0 or minimum_free_headroom_bytes < 0:
        raise ValueError("sleep과 최소 여유공간은 음수일 수 없습니다.")
    if max_duration_seconds <= 0 or max_progress_stall_seconds <= 0:
        raise ValueError("백업 시간과 무진행 상한은 양수여야 합니다.")
    source_path = source_path.resolve()
    snapshot_path = snapshot_path.resolve()
    if not source_path.is_file():
        raise LedgerIntegrityError(f"원장 파일이 없습니다: {source_path}")
    if source_path == snapshot_path:
        raise LedgerIntegrityError("활성 원장을 snapshot 대상으로 덮어쓸 수 없습니다.")
    if snapshot_path.exists():
        raise LedgerIntegrityError(f"snapshot 대상이 이미 있습니다: {snapshot_path}")
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    source_size = source_path.stat().st_size
    free_bytes = shutil.disk_usage(snapshot_path.parent).free
    if free_bytes - source_size < minimum_free_headroom_bytes:
        raise LedgerIntegrityError(
            "snapshot 뒤 최소 여유공간이 부족합니다: "
            f"free={free_bytes}, source={source_size}, "
            f"required_headroom={minimum_free_headroom_bytes}"
        )

    if safety is not None:
        safety.set_stage("ONLINE_SNAPSHOT")
        safety.checkpoint()
    started = time.monotonic()
    iterations = 0
    backup_restarts = 0
    previous_remaining: int | None = None
    lowest_remaining: int | None = None
    last_progress = started
    source_uri = f"file:{quote(str(source_path), safe='/')}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True, timeout=0.25, isolation_level=None)
    destination = sqlite3.connect(snapshot_path, timeout=5.0, isolation_level=None)
    try:
        source.execute("PRAGMA query_only = ON")
        source.execute("PRAGMA busy_timeout = 250")
        source_page_size = int(source.execute("PRAGMA page_size").fetchone()[0])
        source_page_count = int(source.execute("PRAGMA page_count").fetchone()[0])
        user_version = int(source.execute("PRAGMA user_version").fetchone()[0])
        source_journal_mode = str(source.execute("PRAGMA journal_mode").fetchone()[0])

        def progress(_status: int, remaining: int, _total: int) -> None:
            nonlocal backup_restarts, iterations, last_progress
            nonlocal lowest_remaining, previous_remaining
            iterations += 1
            now = time.monotonic()
            if previous_remaining is not None and remaining > previous_remaining:
                backup_restarts += 1
            previous_remaining = remaining
            if lowest_remaining is None or remaining < lowest_remaining:
                lowest_remaining = remaining
                last_progress = now
            if now - started > max_duration_seconds:
                raise LedgerIntegrityError(
                    "온라인 snapshot 시간 상한을 넘었습니다: "
                    f"elapsed={now - started:.3f}s, restarts={backup_restarts}"
                )
            if now - last_progress > max_progress_stall_seconds:
                raise LedgerIntegrityError(
                    "온라인 snapshot이 진행하지 않습니다: "
                    f"stall={now - last_progress:.3f}s, restarts={backup_restarts}, "
                    f"remaining={remaining}"
                )
            if safety is not None:
                safety.checkpoint()
            if step_sleep_seconds:
                time.sleep(step_sleep_seconds)

        source.backup(
            destination,
            pages=pages_per_step,
            progress=progress,
            sleep=max(0.01, step_sleep_seconds),
        )
        destination.commit()
        snapshot_page_count = int(destination.execute("PRAGMA page_count").fetchone()[0])
    except BaseException:
        destination.close()
        source.close()
        _remove_sqlite_files(snapshot_path)
        raise
    else:
        destination.close()
        source.close()
    if safety is not None:
        safety.checkpoint()
    return OnlineSnapshotResult(
        source_path=str(source_path),
        snapshot_path=str(snapshot_path),
        source_size_bytes=source_size,
        snapshot_size_bytes=snapshot_path.stat().st_size,
        source_page_size=source_page_size,
        source_page_count_at_start=source_page_count,
        snapshot_page_count=snapshot_page_count,
        user_version=user_version,
        source_journal_mode=source_journal_mode,
        backup_iterations=iterations,
        backup_restart_count=backup_restarts,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def checkpoint_closed_ledger(source_path: Path) -> ClosedLedgerCheckpointResult:
    """외부 writer가 종료된 원장의 WAL을 본 파일로 완전히 체크포인트한다."""

    source_path = source_path.resolve()
    if not source_path.is_file():
        raise LedgerIntegrityError(f"원장 파일이 없습니다: {source_path}")
    started = time.monotonic()
    connection = sqlite3.connect(source_path, timeout=5.0, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or len(checkpoint) != 3:
            raise LedgerIntegrityError("WAL checkpoint 결과가 완전하지 않습니다.")
        busy, log_frames, checkpointed_frames = (int(value) for value in checkpoint)
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.DatabaseError as error:
        raise LedgerIntegrityError(f"닫힌 원장 WAL checkpoint 실패: {error}") from error
    finally:
        connection.close()
    wal_path = Path(f"{source_path}-wal")
    wal_size = wal_path.stat().st_size if wal_path.exists() else 0
    if busy != 0 or log_frames != checkpointed_frames or wal_size != 0:
        raise LedgerIntegrityError(
            "닫힌 원장의 WAL이 완전히 정리되지 않았습니다: "
            f"busy={busy}, log={log_frames}, checkpointed={checkpointed_frames}, "
            f"wal_bytes={wal_size}"
        )
    return ClosedLedgerCheckpointResult(
        source_path=str(source_path),
        busy=busy,
        log_frame_count=log_frames,
        checkpointed_frame_count=checkpointed_frames,
        wal_size_bytes_after=wal_size,
        page_count=page_count,
        user_version=user_version,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def create_closed_ledger_clone(
    source_path: Path,
    snapshot_path: Path,
    *,
    minimum_free_headroom_bytes: int = 5 * 1024**3,
    clone_file: Callable[[Path, Path], None] | None = None,
) -> ClosedLedgerCloneResult:
    """닫힌 SQLite를 같은 device의 clonefile(2) 사본으로 고정한다."""

    if minimum_free_headroom_bytes < 0:
        raise ValueError("최소 여유공간은 음수일 수 없습니다.")
    source_path = source_path.resolve()
    snapshot_path = snapshot_path.resolve()
    if not source_path.is_file():
        raise LedgerIntegrityError(f"원장 파일이 없습니다: {source_path}")
    if source_path == snapshot_path:
        raise LedgerIntegrityError("원장을 clone 대상으로 덮어쓸 수 없습니다.")
    if snapshot_path.exists():
        raise LedgerIntegrityError(f"clone 대상이 이미 있습니다: {snapshot_path}")
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    source_stat = source_path.stat()
    parent_stat = snapshot_path.parent.stat()
    if source_stat.st_dev != parent_stat.st_dev:
        raise LedgerIntegrityError("clone 원본과 대상은 같은 device에 있어야 합니다.")
    free_before = shutil.disk_usage(snapshot_path.parent).free
    if free_before < minimum_free_headroom_bytes:
        raise LedgerIntegrityError(
            "clone 전 최소 여유공간이 부족합니다: "
            f"free={free_before}, required={minimum_free_headroom_bytes}"
        )
    started = time.monotonic()
    clone_implementation = clone_file or _darwin_clonefile
    try:
        clone_implementation(source_path, snapshot_path)
        snapshot_size = snapshot_path.stat().st_size
        source_after = source_path.stat()
        if source_after.st_size != source_stat.st_size or snapshot_size != source_stat.st_size:
            raise LedgerIntegrityError(
                "clone 전후 원장 또는 사본 크기가 달라졌습니다: "
                f"before={source_stat.st_size}, source_after={source_after.st_size}, "
                f"snapshot={snapshot_size}"
            )
    except BaseException:
        _remove_sqlite_files(snapshot_path)
        raise
    free_after = shutil.disk_usage(snapshot_path.parent).free
    return ClosedLedgerCloneResult(
        source_path=str(source_path),
        snapshot_path=str(snapshot_path),
        source_size_bytes=source_stat.st_size,
        snapshot_size_bytes=snapshot_size,
        device_id=source_stat.st_dev,
        free_bytes_before=free_before,
        free_bytes_after=free_after,
        clone_api="clonefile(2)" if clone_file is None else "injected-test-clone",
        duration_seconds=round(time.monotonic() - started, 3),
    )


def _darwin_clonefile(source_path: Path, snapshot_path: Path) -> None:
    """macOS clonefile(2)를 직접 호출해 일반 copy fallback을 금지한다."""

    if sys.platform != "darwin":
        raise LedgerIntegrityError("clonefile(2) 유지관리는 macOS에서만 지원합니다.")
    library = CDLL(None, use_errno=True)
    clonefile = library.clonefile
    clonefile.argtypes = [c_char_p, c_char_p, c_int]
    clonefile.restype = c_int
    result = clonefile(os.fsencode(source_path), os.fsencode(snapshot_path), 0)
    if result != 0:
        error_number = get_errno()
        raise OSError(error_number, os.strerror(error_number), str(snapshot_path))


def transfer_closed_snapshot(
    source_path: Path,
    verification_path: Path,
    *,
    minimum_free_headroom_bytes: int = 2 * 1024**3,
    chunk_bytes: int = 4 * 1024**2,
    chunk_sleep_seconds: float = 0.05,
    require_different_device: bool = True,
    safety: SafetyCheckpoint | None = None,
) -> ClosedSnapshotTransferResult:
    """닫힌 snapshot을 별도 device로 제한 복사하고 SHA-256로 대조한다."""

    if minimum_free_headroom_bytes < 0 or chunk_sleep_seconds < 0:
        raise ValueError("여유공간과 chunk sleep은 음수일 수 없습니다.")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes는 양수여야 합니다.")
    source_path = source_path.resolve()
    verification_path = verification_path.resolve()
    if not source_path.is_file():
        raise LedgerIntegrityError(f"전송할 snapshot이 없습니다: {source_path}")
    if source_path == verification_path:
        raise LedgerIntegrityError("snapshot을 같은 경로로 전송할 수 없습니다.")
    if verification_path.exists():
        raise LedgerIntegrityError(f"검증 대상이 이미 있습니다: {verification_path}")
    verification_path.parent.mkdir(parents=True, exist_ok=True)
    source_stat = source_path.stat()
    target_parent_stat = verification_path.parent.stat()
    if require_different_device and source_stat.st_dev == target_parent_stat.st_dev:
        raise LedgerIntegrityError("검증 전송 대상은 원본과 다른 device에 있어야 합니다.")
    free_before = shutil.disk_usage(verification_path.parent).free
    if free_before - source_stat.st_size < minimum_free_headroom_bytes:
        raise LedgerIntegrityError(
            "검증 device의 최소 여유공간이 부족합니다: "
            f"free={free_before}, source={source_stat.st_size}, "
            f"required_headroom={minimum_free_headroom_bytes}"
        )
    if safety is not None:
        safety.set_stage("TRANSFER_SNAPSHOT_TO_VERIFICATION_DEVICE")
        safety.checkpoint()
    started = time.monotonic()
    source_digest = hashlib.sha256()
    copied_bytes = 0
    try:
        with source_path.open("rb", buffering=0) as source_stream:
            with verification_path.open("xb", buffering=0) as target_stream:
                while chunk := source_stream.read(chunk_bytes):
                    source_digest.update(chunk)
                    target_stream.write(chunk)
                    copied_bytes += len(chunk)
                    if safety is not None:
                        safety.checkpoint()
                    if chunk_sleep_seconds:
                        time.sleep(chunk_sleep_seconds)
                os.fsync(target_stream.fileno())
        if copied_bytes != source_stat.st_size:
            raise LedgerIntegrityError(
                "snapshot 전송 크기가 다릅니다: "
                f"source={source_stat.st_size}, copied={copied_bytes}"
            )
        verification_digest = hashlib.sha256()
        with verification_path.open("rb", buffering=0) as verification_stream:
            while chunk := verification_stream.read(chunk_bytes):
                verification_digest.update(chunk)
                if safety is not None:
                    safety.checkpoint()
        source_hexdigest = source_digest.hexdigest()
        verification_hexdigest = verification_digest.hexdigest()
        if source_hexdigest != verification_hexdigest:
            raise LedgerIntegrityError("snapshot 전송 SHA-256 대조가 실패했습니다.")
    except BaseException:
        _remove_sqlite_files(verification_path)
        raise
    if safety is not None:
        safety.checkpoint()
    return ClosedSnapshotTransferResult(
        source_path=str(source_path),
        verification_path=str(verification_path),
        source_device_id=source_stat.st_dev,
        verification_device_id=target_parent_stat.st_dev,
        copied_bytes=copied_bytes,
        sha256=source_hexdigest,
        verification_sha256=verification_hexdigest,
        free_bytes_before=free_before,
        free_bytes_after=shutil.disk_usage(verification_path.parent).free,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def verify_closed_snapshot(
    snapshot_path: Path,
    *,
    progress_opcodes: int = 5_000,
    progress_sleep_seconds: float = 0.001,
    safety: SafetyCheckpoint | None = None,
) -> SnapshotIntegrityResult:
    """활성 원장이 아닌 immutable snapshot에서만 전수 검사를 실행한다."""

    if progress_opcodes <= 0 or progress_sleep_seconds < 0:
        raise ValueError("progress_opcodes는 양수이고 sleep은 0 이상이어야 합니다.")
    snapshot_path = snapshot_path.resolve()
    if not snapshot_path.is_file():
        raise LedgerIntegrityError(f"검증할 snapshot이 없습니다: {snapshot_path}")
    if safety is not None:
        safety.set_stage("OFFLINE_SNAPSHOT_QUICK_CHECK")
        safety.checkpoint()
    started = time.monotonic()
    snapshot_uri = f"file:{quote(str(snapshot_path), safe='/')}?mode=ro&immutable=1"
    connection = sqlite3.connect(snapshot_uri, uri=True, timeout=0.0, isolation_level=None)
    interrupted_by_safety = False

    def progress() -> int:
        nonlocal interrupted_by_safety
        try:
            if safety is not None:
                safety.checkpoint()
        except RuntimeSafetyViolation:
            interrupted_by_safety = True
            return 1
        if progress_sleep_seconds:
            time.sleep(progress_sleep_seconds)
        return 0

    connection.set_progress_handler(progress, progress_opcodes)
    try:
        quick_rows = tuple(str(row[0]) for row in connection.execute("PRAGMA quick_check"))
        foreign_examples: list[tuple[object, ...]] = []
        foreign_count = 0
        for row in connection.execute("PRAGMA foreign_key_check"):
            foreign_count += 1
            if len(foreign_examples) < 25:
                foreign_examples.append(tuple(row))
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        table_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE type = 'table'"
            ).fetchone()[0]
        )
    except sqlite3.DatabaseError as error:
        if interrupted_by_safety:
            raise RuntimeSafetyViolation("snapshot 전수검사를 안전감시가 중단했습니다.") from error
        raise LedgerIntegrityError(f"snapshot SQLite 검사 실패: {error}") from error
    finally:
        connection.set_progress_handler(None, 0)
        connection.close()
    if safety is not None:
        safety.checkpoint()
    quick_status = "ok" if quick_rows == ("ok",) else "failed"
    if quick_status != "ok" or foreign_count:
        raise LedgerIntegrityError(
            f"snapshot 무결성 실패: quick_check={quick_rows}, foreign_keys={foreign_count}"
        )
    return SnapshotIntegrityResult(
        snapshot_path=str(snapshot_path),
        quick_check=quick_status,
        quick_check_rows=quick_rows,
        foreign_key_violation_count=foreign_count,
        foreign_key_violation_examples=tuple(foreign_examples),
        page_count=page_count,
        freelist_count=freelist_count,
        user_version=user_version,
        table_count=table_count,
        duration_seconds=round(time.monotonic() - started, 3),
    )


def remove_snapshot(snapshot_path: Path) -> None:
    """검증용 임시 SQLite와 sidecar만 명시적으로 제거한다."""

    _remove_sqlite_files(snapshot_path.resolve())


def _remove_sqlite_files(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeSafetyViolation(f"{field}가 객체가 아닙니다.")
    return value


def _sequence(value: object, field: str) -> tuple[object, ...]:
    if not isinstance(value, list):
        raise RuntimeSafetyViolation(f"{field}가 배열이 아닙니다.")
    return tuple(value)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeSafetyViolation(f"{field}가 문자열이 아닙니다.")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeSafetyViolation(f"{field}가 정수가 아닙니다.")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RuntimeSafetyViolation(f"{field}가 숫자가 아닙니다.")
    return float(value)


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeSafetyViolation(f"{field}가 불리언이 아닙니다.")
    return value


def result_as_dict(
    result: (
        OnlineSnapshotResult
        | ClosedLedgerCheckpointResult
        | ClosedLedgerCloneResult
        | ClosedSnapshotTransferResult
        | SnapshotIntegrityResult
    ),
) -> dict[str, Any]:
    """slots dataclass 결과를 기계판독 증거용 dict로 변환한다."""

    return asdict(result)
