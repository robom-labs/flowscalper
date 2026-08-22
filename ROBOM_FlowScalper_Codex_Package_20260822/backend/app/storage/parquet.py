"""시장·특징 이벤트를 압축 Parquet 파티션으로 보존한다."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


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

    def health(self) -> StorageHealth:
        usage = self._disk_usage(self.root)
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
        partition = (
            self.root
            / f"venue={_safe_partition(venue)}"
            / f"date={first:%Y-%m-%d}"
            / f"symbol={_safe_partition(symbol)}"
            / f"hour={first:%H}"
            / f"event_type={_safe_partition(event_type)}"
        )
        partition.mkdir(parents=True, exist_ok=True)
        material = json.dumps(rows, default=str, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(material.encode()).hexdigest()[:16]
        destination = partition / f"part-{digest}.parquet"
        table = pa.Table.from_pylist([dict(row) for row in rows])
        pq.write_table(table, destination, compression="zstd", write_statistics=True)
        return destination

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


def _default_disk_usage(path: Path) -> DiskUsage:
    usage = shutil.disk_usage(path)
    return DiskUsage(total=usage.total, used=usage.used, free=usage.free)


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
