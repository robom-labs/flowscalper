# 진행 중인 LIVE_PUBLIC Run에서 이미 완결된 Parquet만 동결하고 retention pin을 만든다.

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from backend.app.storage.io_priority import storage_io_priority_gate

DEFAULT_LIVE_LEDGER_PATH = Path(
    "/Volumes/ROBOM_FLOWSCALPER/05_RUNTIME/ROBOM_FlowScalper/active-ledger/"
    "run-ledger.sqlite3"
)
DEFAULT_RESOURCE_LOCK = Path("/tmp/robom-flowscalper-strategy-league-replay.lock")


class _ArchiveReadBudget:
    """LIVE 저장보다 낮은 속도로 동결 파일을 읽게 한다."""

    def __init__(
        self,
        *,
        target_bytes_per_second: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if target_bytes_per_second <= 0:
            raise ValueError("동결 archive 읽기 속도는 양수여야 합니다.")
        self._target_bytes_per_second = target_bytes_per_second
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._started = monotonic()
        self._bytes_read = 0

    def checkpoint(self, bytes_read: int) -> None:
        if bytes_read < 0:
            raise ValueError("동결 archive 읽기 byte는 음수일 수 없습니다.")
        self._bytes_read += bytes_read
        required_elapsed = self._bytes_read / self._target_bytes_per_second
        actual_elapsed = self._monotonic() - self._started
        if required_elapsed > actual_elapsed:
            self._sleeper(required_elapsed - actual_elapsed)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(
    path: Path,
    *,
    read_budget: _ArchiveReadBudget | None = None,
    read_guard: Callable[[], AbstractContextManager[None]] = nullcontext,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            with read_guard():
                chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if read_budget is not None:
                read_budget.checkpoint(len(chunk))
    return digest.hexdigest()


def _column_range(parquet: pq.ParquetFile, name: str) -> tuple[int, int]:
    column_index = parquet.schema_arrow.get_field_index(name)
    if column_index < 0:
        raise ValueError(f"동결 Parquet에 {name} column이 없습니다.")
    minimums: list[int] = []
    maximums: list[int] = []
    for index in range(parquet.metadata.num_row_groups):
        statistics = parquet.metadata.row_group(index).column(column_index).statistics
        if statistics is None or not statistics.has_min_max:
            values = parquet.read_row_group(index, columns=[name]).column(0).to_pylist()
            if not values:
                continue
            minimums.append(min(int(str(value)) for value in values))
            maximums.append(max(int(str(value)) for value in values))
        else:
            minimums.append(int(str(statistics.min)))
            maximums.append(int(str(statistics.max)))
    if not minimums:
        raise ValueError(f"동결 Parquet의 {name} 범위가 비어 있습니다.")
    return min(minimums), max(maximums)


def _batch_checksum(parquet: pq.ParquetFile) -> str:
    if parquet.schema_arrow.get_field_index("batch_checksum") < 0:
        raise ValueError("동결 Parquet에 batch_checksum column이 없습니다.")
    values: set[str] = set()
    for index in range(parquet.metadata.num_row_groups):
        column = parquet.read_row_group(index, columns=["batch_checksum"]).column(0)
        values.update(str(value) for value in column.unique().to_pylist())
    if (
        len(values) != 1
        or len(next(iter(values), "")) != 64
        or any(character not in "0123456789abcdef" for character in next(iter(values), ""))
    ):
        raise ValueError("동결 Parquet의 batch checksum이 유일한 SHA-256이 아닙니다.")
    return next(iter(values))


def freeze_live_public_cut(
    archive_root: Path,
    run_partition: Path,
    *,
    minimum_age_seconds: int = 120,
    now_ns: int | None = None,
    target_read_mib_per_second: float = 4.0,
    live_ledger_path: Path | None = None,
) -> dict[str, Any]:
    """새 파일이 계속 생겨도 완결·안정된 기존 파일만 정확한 목록으로 동결한다."""

    if minimum_age_seconds < 1:
        raise ValueError("동결 파일 최소 안정시간은 1초 이상이어야 합니다.")
    resolved_root = archive_root.resolve(strict=True)
    resolved_partition = (
        run_partition.resolve(strict=True)
        if run_partition.is_absolute()
        else (resolved_root / run_partition).resolve(strict=True)
    )
    if not resolved_partition.is_relative_to(resolved_root):
        raise ValueError("동결 Run partition이 archive root 밖에 있습니다.")
    run_parts = [part for part in resolved_partition.parts if part.startswith("run=")]
    if len(run_parts) != 1:
        raise ValueError("동결 경로에는 정확히 하나의 run= partition이 필요합니다.")
    observed_now_ns = time.time_ns() if now_ns is None else now_ns
    minimum_age_ns = minimum_age_seconds * 1_000_000_000
    read_budget = _ArchiveReadBudget(
        target_bytes_per_second=target_read_mib_per_second * 1024 * 1024
    )
    read_guard: Callable[[], AbstractContextManager[None]] = (
        (lambda: storage_io_priority_gate(live_ledger_path, exclusive=False))
        if live_ledger_path is not None
        else nullcontext
    )
    files: list[dict[str, object]] = []
    for path in sorted(resolved_partition.rglob("*.parquet")):
        before = path.stat()
        if observed_now_ns - before.st_mtime_ns < minimum_age_ns:
            continue
        parquet = pq.ParquetFile(path)
        if parquet.metadata.num_rows <= 0:
            raise ValueError(f"동결 Parquet가 비어 있습니다: {path}")
        first_ts_ms, last_ts_ms = _column_range(parquet, "venue_ts_ms")
        row = {
            "relative_path": path.relative_to(resolved_root).as_posix(),
            "size_bytes": before.st_size,
            "mtime_ns": before.st_mtime_ns,
            "event_count": parquet.metadata.num_rows,
            "first_ts_ms": first_ts_ms,
            "last_ts_ms": last_ts_ms,
            "batch_checksum": _batch_checksum(parquet),
            "file_sha256": _sha256_file(
                path,
                read_budget=read_budget,
                read_guard=read_guard,
            ),
            "inode": before.st_ino,
            "ctime_ns": before.st_ctime_ns,
        }
        after = path.stat()
        before_identity = (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_identity != after_identity:
            raise ValueError(f"동결 검사 중 Parquet가 변경됐습니다: {path}")
        files.append(row)
    if not files:
        raise ValueError("최소 안정시간을 지난 완결 Parquet가 없습니다.")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "FROZEN_LIVE_PUBLIC_CUT",
        "generated_ts_utc": datetime.now(UTC).isoformat(),
        "archive_root": str(resolved_root),
        "run_id": run_parts[0].split("=", maxsplit=1)[1],
        "run_partition": resolved_partition.relative_to(resolved_root).as_posix(),
        "minimum_file_age_seconds": minimum_age_seconds,
        "target_archive_read_mib_per_second": target_read_mib_per_second,
        "live_ledger_io_priority_gate": live_ledger_path is not None,
        "file_count": len(files),
        "event_count": sum(int(str(row["event_count"])) for row in files),
        "first_ts_ms": min(int(str(row["first_ts_ms"])) for row in files),
        "cutoff_ts_ms": max(int(str(row["last_ts_ms"])) for row in files),
        "total_size_bytes": sum(int(str(row["size_bytes"])) for row in files),
        "files": files,
        "append_only_source": True,
        "newer_unselected_files_may_continue": True,
        "retention_pin_required": True,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
    }
    manifest["manifest_sha256"] = hashlib.sha256(_canonical_json(manifest).encode()).hexdigest()
    return manifest


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == encoded:
            return
        raise FileExistsError(f"기존 동결 증거를 덮어쓰지 않습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)


def _acquire_resource_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _release_resource_lock(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def write_live_public_cut(
    manifest: dict[str, Any],
    *,
    output: Path,
    archive_root: Path,
) -> Path:
    """사용자 증거와 runtime retention pin을 같은 checksum으로 원자적으로 쓴다."""

    resolved_root = archive_root.resolve(strict=True)
    checksum_payload = dict(manifest)
    claimed_checksum = str(checksum_payload.pop("manifest_sha256", ""))
    actual_checksum = hashlib.sha256(_canonical_json(checksum_payload).encode()).hexdigest()
    if (
        manifest.get("status") != "FROZEN_LIVE_PUBLIC_CUT"
        or Path(str(manifest.get("archive_root", ""))).resolve() != resolved_root
        or claimed_checksum != actual_checksum
    ):
        raise ValueError("LIVE_PUBLIC cut의 상태·archive root·checksum이 잘못됐습니다.")
    checksum = str(manifest["manifest_sha256"])
    run_id = str(manifest["run_id"])
    pin_path = resolved_root / ".research-pins" / f"{run_id}-{checksum[:16]}.json"
    # 두 경로를 하나의 파일시스템 transaction으로 묶을 수 없으므로 보존 안전성이
    # 더 중요한 retention pin을 먼저 쓴다. 이후 evidence 쓰기가 실패해도 원자료는 남는다.
    _atomic_write_json(pin_path, manifest)
    _atomic_write_json(output, manifest)
    return pin_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, default=Path("data/market-parquet-v6"))
    parser.add_argument("--run-partition", type=Path, required=True)
    parser.add_argument("--minimum-age-seconds", type=int, default=120)
    parser.add_argument("--target-read-mib-per-second", type=float, default=4.0)
    parser.add_argument("--live-ledger-path", type=Path, default=DEFAULT_LIVE_LEDGER_PATH)
    parser.add_argument("--resource-lock", type=Path, default=DEFAULT_RESOURCE_LOCK)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.target_read_mib_per_second <= 0:
        raise ValueError("동결 archive 읽기 속도는 양수여야 합니다.")
    live_ledger_path = args.live_ledger_path.resolve(strict=True)
    resource_lock_descriptor: int | None = None
    try:
        resource_lock_descriptor = _acquire_resource_lock(args.resource_lock.resolve())
        try:
            os.nice(19)
        except OSError:
            pass
        manifest = freeze_live_public_cut(
            args.archive_root,
            args.run_partition,
            minimum_age_seconds=args.minimum_age_seconds,
            target_read_mib_per_second=args.target_read_mib_per_second,
            live_ledger_path=live_ledger_path,
        )
        pin_path = write_live_public_cut(
            manifest,
            output=args.output,
            archive_root=args.archive_root,
        )
    finally:
        _release_resource_lock(resource_lock_descriptor)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "run_id": manifest["run_id"],
                "file_count": manifest["file_count"],
                "event_count": manifest["event_count"],
                "cutoff_ts_ms": manifest["cutoff_ts_ms"],
                "manifest_sha256": manifest["manifest_sha256"],
                "retention_pin": pin_path.as_posix(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
