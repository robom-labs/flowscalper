"""시장·특징 이벤트를 압축 Parquet 파티션으로 보존한다."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

_BACKGROUND_IO_POLICY_APPLIED = False


class StoragePressureError(RuntimeError):
    """원장 유실 전에 신규 PAPER 진입을 차단하기 위한 저장소 알람이다."""


@dataclass(frozen=True, slots=True)
class DiskUsage:
    total: int
    used: int
    free: int


@dataclass(frozen=True, slots=True)
class StorageHealth:
    entry_allowed: bool
    free_bytes: int
    free_ratio: float
    reason: str | None


@dataclass(frozen=True, slots=True)
class ArchivedEventBatch:
    """외장 압축 이벤트 배치의 불변 manifest에 필요한 정보다."""

    path: Path
    checksum: str
    event_count: int


class ParquetEventStore:
    """파티션 계약과 보존 기간, 디스크 압박 차단을 같이 적용한다."""

    def __init__(
        self,
        root: Path,
        *,
        minimum_free_bytes: int = 2 * 1024**3,
        minimum_free_ratio: float = 0.05,
        disk_usage: Callable[[Path], DiskUsage] | None = None,
    ) -> None:
        self.root = root
        self.minimum_free_bytes = minimum_free_bytes
        self.minimum_free_ratio = minimum_free_ratio
        self._disk_usage = disk_usage or _default_disk_usage
        root.mkdir(parents=True, exist_ok=True)

    def health(self, path: Path | None = None) -> StorageHealth:
        """아카이브 또는 같은 안전기준을 적용할 별도 원장 경로를 검사한다."""

        usage = self._disk_usage(path or self.root)
        ratio = usage.free / usage.total if usage.total else 0.0
        if usage.free < self.minimum_free_bytes:
            return StorageHealth(False, usage.free, ratio, "FREE_BYTES_BELOW_LIMIT")
        if ratio < self.minimum_free_ratio:
            return StorageHealth(False, usage.free, ratio, "FREE_RATIO_BELOW_LIMIT")
        return StorageHealth(True, usage.free, ratio, None)

    def assert_entry_storage_ready(self) -> None:
        health = self.health()
        if not health.entry_allowed:
            raise StoragePressureError(
                f"STORAGE_PRESSURE: {health.reason}; free={health.free_bytes}; "
                f"ratio={health.free_ratio:.4f}"
            )

    def write_events(
        self,
        *,
        venue: str,
        symbol: str,
        event_type: str,
        rows: Sequence[Mapping[str, object]],
        partition_run_id: str | None = None,
        content_digest: str | None = None,
    ) -> Path:
        if not rows:
            raise ValueError("빈 이벤트 묶음은 저장하지 않습니다.")
        self.assert_entry_storage_ready()
        timestamps = {int(str(row["ts_ms"])) for row in rows}
        first = datetime.fromtimestamp(min(timestamps) / 1000, tz=UTC)
        if any(
            datetime.fromtimestamp(timestamp / 1000, tz=UTC).strftime("%Y-%m-%d/%H")
            != first.strftime("%Y-%m-%d/%H")
            for timestamp in timestamps
        ):
            raise ValueError("하나의 파일은 같은 UTC 날짜·시간에만 속해야 합니다.")
        partition = self.root / f"venue={_safe_partition(venue)}"
        if partition_run_id is not None:
            partition /= f"run={_safe_partition(partition_run_id)}"
        partition = (
            partition
            / f"date={first:%Y-%m-%d}"
            / f"symbol={_safe_partition(symbol)}"
            / f"hour={first:%H}"
            / f"event_type={_safe_partition(event_type)}"
        )
        partition.mkdir(parents=True, exist_ok=True)
        if content_digest is None:
            material = json.dumps(rows, default=str, sort_keys=True, separators=(",", ":"))
            content_digest = hashlib.sha256(material.encode()).hexdigest()
        digest = content_digest[:16]
        destination = partition / f"part-{digest}.parquet"
        table = pa.Table.from_pylist([dict(row) for row in rows])
        pq.write_table(table, destination, compression="zstd", write_statistics=True)
        return destination

    def write_market_event_batch(
        self,
        rows: Sequence[Mapping[str, object]],
    ) -> ArchivedEventBatch:
        """리플레이 payload를 외장 Parquet에 순차 압축 저장한다."""

        if not rows:
            raise ValueError("빈 시장 이벤트 배치는 아카이브하지 않습니다.")
        archived_rows: list[dict[str, object]] = []
        row_checksums: list[str] = []
        for row in rows:
            payload_json = json.dumps(
                row,
                default=str,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            checksum = hashlib.sha256(payload_json.encode()).hexdigest()
            row_checksums.append(checksum)
            archived_rows.append(
                {
                    "ts_ms": int(str(row["venue_ts_ms"])),
                    "venue_ts_ms": int(str(row["venue_ts_ms"])),
                    "receive_ts_ms": _market_event_receive_ts_ms(row),
                    "receive_monotonic_ns": _market_event_receive_monotonic_ns(row),
                    "symbol": str(row["symbol"]),
                    "event_type": str(row["event_type"]),
                    "payload_json": payload_json,
                    "checksum": checksum,
                }
            )
        batch_checksum = hashlib.sha256("\n".join(row_checksums).encode()).hexdigest()
        for archived_row in archived_rows:
            archived_row["batch_checksum"] = batch_checksum
        path = self.write_events(
            venue=str(rows[0]["venue"]),
            symbol="MULTI",
            event_type="MARKET_EVENT",
            rows=archived_rows,
            partition_run_id=str(rows[0]["run_id"]),
            content_digest=batch_checksum,
        )
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
        return ArchivedEventBatch(
            path=path,
            checksum=batch_checksum,
            event_count=len(archived_rows),
        )

    def read_market_event_batch_filtered(
        self,
        path: Path,
        *,
        expected_checksum: str,
        symbol: str | None = None,
        event_types: tuple[str, ...] = (),
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        batch_guard: Callable[[], AbstractContextManager[None]] | None = None,
        cooperative_yield: Callable[[], None] | None = None,
        batch_size: int = 128,
    ) -> list[dict[str, object]]:
        """신규 Parquet을 작은 조각으로 전수검증하고 필요한 row만 읽는다."""

        resolved_root = self.root.resolve()
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_root):
            raise ValueError("시장 이벤트 배치가 허용된 저장소 밖을 참조합니다.")
        if batch_size <= 0:
            raise ValueError("Parquet replay batch_size는 양수여야 합니다.")
        guard = batch_guard or nullcontext
        with guard():
            parquet = pq.ParquetFile(resolved_path)
        filter_columns = {"symbol", "event_type", "venue_ts_ms", "batch_checksum"}
        if not filter_columns.issubset(parquet.schema_arrow.names):
            legacy_events = self.read_market_event_batch(
                path,
                expected_checksum=expected_checksum,
                batch_guard=batch_guard,
                cooperative_yield=cooperative_yield,
                batch_size=batch_size,
            )
            return [
                event
                for event in legacy_events
                if (symbol is None or str(event.get("symbol")) == symbol)
                and (not event_types or str(event.get("event_type")) in event_types)
                and (start_ts_ms is None or int(str(event["venue_ts_ms"])) >= start_ts_ms)
                and (end_ts_ms is None or int(str(event["venue_ts_ms"])) <= end_ts_ms)
            ]
        columns = [
            "payload_json",
            "checksum",
            "batch_checksum",
            "symbol",
            "event_type",
            "venue_ts_ms",
        ]
        batches = parquet.iter_batches(
            batch_size=batch_size,
            columns=columns,
            use_threads=False,
        )
        total_rows = parquet.metadata.num_rows
        processed_rows = 0
        row_checksums: list[str] = []
        stored_batch_checksums: set[str] = set()
        events: list[dict[str, object]] = []
        while processed_rows < total_rows:
            with guard():
                batch = next(batches)
            processed_rows += batch.num_rows
            for row in batch.to_pylist():
                payload_json = str(row["payload_json"])
                checksum = str(row["checksum"])
                if hashlib.sha256(payload_json.encode()).hexdigest() != checksum:
                    raise ValueError("시장 이벤트 Parquet row checksum이 일치하지 않습니다.")
                row_checksums.append(checksum)
                stored_batch_checksums.add(str(row["batch_checksum"]))
                if symbol is not None and str(row["symbol"]) != symbol:
                    continue
                if event_types and str(row["event_type"]) not in event_types:
                    continue
                venue_ts_ms = int(str(row["venue_ts_ms"]))
                if start_ts_ms is not None and venue_ts_ms < start_ts_ms:
                    continue
                if end_ts_ms is not None and venue_ts_ms > end_ts_ms:
                    continue
                decoded = json.loads(payload_json)
                if not isinstance(decoded, dict):
                    raise ValueError("시장 이벤트 Parquet payload는 객체여야 합니다.")
                events.append(decoded)
            if cooperative_yield is not None:
                cooperative_yield()
        actual_batch_checksum = hashlib.sha256("\n".join(row_checksums).encode()).hexdigest()
        if (
            stored_batch_checksums != {expected_checksum}
            or actual_batch_checksum != expected_checksum
        ):
            raise ValueError("시장 이벤트 Parquet 배치 checksum이 일치하지 않습니다.")
        return events

    def read_market_event_batch(
        self,
        path: Path,
        *,
        expected_checksum: str,
        batch_guard: Callable[[], AbstractContextManager[None]] | None = None,
        cooperative_yield: Callable[[], None] | None = None,
        batch_size: int = 128,
    ) -> list[dict[str, object]]:
        """manifest 경로·배치·row checksum을 모두 검증해 리플레이한다."""

        resolved_root = self.root.resolve()
        resolved_path = path.resolve()
        if not resolved_path.is_relative_to(resolved_root):
            raise ValueError("시장 이벤트 배치가 허용된 저장소 밖을 참조합니다.")
        if batch_size <= 0:
            raise ValueError("Parquet replay batch_size는 양수여야 합니다.")
        guard = batch_guard or nullcontext
        with guard():
            parquet = pq.ParquetFile(resolved_path)
        batches = parquet.iter_batches(
            batch_size=batch_size,
            columns=["payload_json", "checksum"],
            use_threads=False,
        )
        events: list[dict[str, object]] = []
        row_checksums: list[str] = []
        total_rows = parquet.metadata.num_rows
        processed_rows = 0
        while processed_rows < total_rows:
            with guard():
                batch = next(batches)
            processed_rows += batch.num_rows
            for row in batch.to_pylist():
                payload_json = str(row["payload_json"])
                checksum = str(row["checksum"])
                if hashlib.sha256(payload_json.encode()).hexdigest() != checksum:
                    raise ValueError("시장 이벤트 Parquet row checksum이 일치하지 않습니다.")
                decoded = json.loads(payload_json)
                if not isinstance(decoded, dict):
                    raise ValueError("시장 이벤트 Parquet payload는 객체여야 합니다.")
                row_checksums.append(checksum)
                events.append(decoded)
            if cooperative_yield is not None:
                cooperative_yield()
        actual_checksum = hashlib.sha256("\n".join(row_checksums).encode()).hexdigest()
        if actual_checksum != expected_checksum:
            raise ValueError("시장 이벤트 Parquet 배치 checksum이 일치하지 않습니다.")
        return events

    def apply_retention(
        self,
        *,
        now: datetime,
        deep_book_days: int = 7,
        feature_days: int = 90,
    ) -> tuple[Path, ...]:
        protected_types = {"candidate_window", "trade_window", "decision", "fill"}
        removed: list[Path] = []
        for path in self.root.rglob("*.parquet"):
            partitions = _partitions(path)
            event_type = partitions.get("event_type", "").lower()
            if event_type in protected_types:
                continue
            date_text = partitions.get("date")
            if date_text is None:
                continue
            partition_date = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=UTC)
            days = feature_days if event_type in {"feature_1s", "candle_1s"} else deep_book_days
            if partition_date < now.astimezone(UTC) - timedelta(days=days):
                path.unlink()
                removed.append(path)
        return tuple(sorted(removed))

    def dataset_files(self) -> tuple[Path, ...]:
        return tuple(sorted(self.root.rglob("*.parquet")))


def write_market_event_batch_in_process(
    root: str,
    minimum_free_bytes: int,
    minimum_free_ratio: float,
    rows: list[dict[str, object]],
) -> ArchivedEventBatch:
    """직렬화·압축·fsync를 호출 프로세스 밖에서 수행할 수 있게 한다."""

    _apply_background_io_policy()
    store = ParquetEventStore(
        Path(root),
        minimum_free_bytes=minimum_free_bytes,
        minimum_free_ratio=minimum_free_ratio,
    )
    return store.write_market_event_batch(rows)


def warm_market_event_worker_process() -> int:
    """LIVE 연결 전에 archive worker와 Arrow·zstd 초기화를 끝낸다."""

    _apply_background_io_policy()
    table = pa.Table.from_pylist([{"worker_warmup": 1}])
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd")
    sink.getvalue()
    return os.getpid()


def _apply_background_io_policy() -> None:
    """macOS archive worker를 background I/O로 제한해 활성 원장을 우선한다."""

    global _BACKGROUND_IO_POLICY_APPLIED
    if _BACKGROUND_IO_POLICY_APPLIED:
        return
    _set_background_io_policy(True)


def _set_background_io_policy(enabled: bool) -> bool:
    """archive 구간에만 Darwin background 우선순위를 적용하거나 해제한다."""

    global _BACKGROUND_IO_POLICY_APPLIED
    if sys.platform != "darwin":
        _BACKGROUND_IO_POLICY_APPLIED = enabled
        return True
    taskpolicy = Path("/usr/sbin/taskpolicy")
    if not taskpolicy.is_file():
        return False
    result = subprocess.run(
        [
            str(taskpolicy),
            "-b" if enabled else "-B",
            "-p",
            str(os.getpid()),
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        _BACKGROUND_IO_POLICY_APPLIED = enabled
        return True
    return False


def _default_disk_usage(path: Path) -> DiskUsage:
    usage = shutil.disk_usage(path)
    return DiskUsage(total=usage.total, used=usage.used, free=usage.free)


def _market_event_receive_ts_ms(event: Mapping[str, object]) -> int:
    venue_ts_ms = int(str(event["venue_ts_ms"]))
    explicit = event.get("receive_ts_ms", event.get("recv_ts_ms"))
    if explicit is not None:
        receive_ts_ms = int(str(explicit))
    else:
        quality = event.get("quality")
        lag_value = quality.get("lag_ms", 0) if isinstance(quality, Mapping) else 0
        try:
            lag_ms = max(0, round(float(lag_value)))
        except (OverflowError, TypeError, ValueError):
            lag_ms = 0
        receive_ts_ms = venue_ts_ms + lag_ms
    if receive_ts_ms < venue_ts_ms:
        raise ValueError("시장 이벤트 수신시각이 거래소 시각보다 빠릅니다.")
    return receive_ts_ms


def _market_event_receive_monotonic_ns(event: Mapping[str, object]) -> int:
    explicit = event.get("receive_monotonic_ns")
    if explicit is not None:
        value = int(str(explicit))
        if value < 0:
            raise ValueError("시장 이벤트 monotonic 수신시각은 음수일 수 없습니다.")
        return value
    # 구 archive 입력은 monotonic 필드가 없을 수 있다. payload는 바꾸지 않고
    # 검증된 wall-clock 수신시각으로 결정적 정렬 보조값만 복구한다.
    return _market_event_receive_ts_ms(event) * 1_000_000


def _safe_partition(value: str) -> str:
    if not value or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in value.upper()
    ):
        raise ValueError(f"안전하지 않은 파티션 값: {value}")
    return value.upper()


def _partitions(path: Path) -> dict[str, str]:
    return {
        key: value
        for part in path.parts
        if "=" in part
        for key, value in [part.split("=", maxsplit=1)]
    }
