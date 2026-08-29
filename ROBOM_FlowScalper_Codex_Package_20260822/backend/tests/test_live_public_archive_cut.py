# 진행 중인 LIVE_PUBLIC Run의 완결 파일 동결과 retention 보호를 검증한다.

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.storage import ParquetEventStore
from scripts.freeze_live_public_archive_cut import (
    _acquire_resource_lock,
    _ArchiveReadBudget,
    _release_resource_lock,
    freeze_live_public_cut,
    write_live_public_cut,
)


def _event(event_id: str, ts_ms: int) -> dict[str, object]:
    return {
        "event_id": event_id,
        "run_id": "run-cut",
        "venue": "BINANCE_USDM",
        "symbol": "BTCUSDT",
        "event_type": "TRADE",
        "venue_ts_ms": ts_ms,
        "receive_ts_ms": ts_ms + 10,
        "receive_monotonic_ns": ts_ms * 1_000_000,
        "data": {"price": "100", "quantity": "1"},
    }


def test_live_public_cut_pins_only_stable_complete_parquet(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = ParquetEventStore(root, minimum_free_bytes=0, minimum_free_ratio=0)
    old_ts_ms = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1_000)
    batch = store.write_market_event_batch([_event("event-1", old_ts_ms)])
    observed_now_ns = batch.path.stat().st_mtime_ns + 121 * 1_000_000_000
    manifest = freeze_live_public_cut(
        root,
        batch.path.parents[4],
        minimum_age_seconds=120,
        now_ns=observed_now_ns,
    )
    pin = write_live_public_cut(
        manifest,
        output=tmp_path / "evidence.json",
        archive_root=root,
    )

    assert manifest["status"] == "FROZEN_LIVE_PUBLIC_CUT"
    assert manifest["file_count"] == 1
    assert manifest["event_count"] == 1
    assert pin.is_file()
    assert store.apply_retention(now=datetime(2026, 8, 22, tzinfo=UTC)) == ()
    assert batch.path.is_file()


def test_live_public_cut_excludes_file_inside_stability_window(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = ParquetEventStore(root, minimum_free_bytes=0, minimum_free_ratio=0)
    batch = store.write_market_event_batch([_event("event-1", 1_735_689_600_000)])

    with pytest.raises(ValueError, match="완결 Parquet"):
        freeze_live_public_cut(
            root,
            batch.path.parents[4],
            minimum_age_seconds=120,
            now_ns=batch.path.stat().st_mtime_ns + 119 * 1_000_000_000,
        )


def test_tampered_research_pin_fails_closed_before_retention(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = ParquetEventStore(root, minimum_free_bytes=0, minimum_free_ratio=0)
    old_ts = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1_000)
    path = store.write_events(
        venue="BINANCE_USDM",
        symbol="BTCUSDT",
        event_type="deep_book",
        rows=[{"ts_ms": old_ts, "bid": 100, "ask": 101}],
    )
    pin_directory = root / ".research-pins"
    pin_directory.mkdir()
    (pin_directory / "tampered.json").write_text(
        json.dumps(
            {
                "status": "FROZEN_LIVE_PUBLIC_CUT",
                "archive_root": str(root.resolve()),
                "files": [{"relative_path": path.relative_to(root).as_posix()}],
                "manifest_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checksum"):
        store.apply_retention(now=datetime(2026, 8, 22, tzinfo=UTC))
    assert path.is_file()


def test_changed_pinned_parquet_fails_closed_before_retention(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = ParquetEventStore(root, minimum_free_bytes=0, minimum_free_ratio=0)
    old_ts_ms = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1_000)
    batch = store.write_market_event_batch([_event("event-1", old_ts_ms)])
    manifest = freeze_live_public_cut(
        root,
        batch.path.parents[4],
        minimum_age_seconds=120,
        now_ns=batch.path.stat().st_mtime_ns + 121 * 1_000_000_000,
    )
    write_live_public_cut(
        manifest,
        output=tmp_path / "evidence.json",
        archive_root=root,
    )
    batch.path.touch()

    with pytest.raises(ValueError, match="동결 뒤 변경"):
        store.apply_retention(now=datetime(2026, 8, 22, tzinfo=UTC))
    assert batch.path.is_file()


def test_archive_cut_read_budget_enforces_the_declared_rate() -> None:
    sleeps: list[float] = []
    budget = _ArchiveReadBudget(
        target_bytes_per_second=4,
        monotonic=lambda: 0.0,
        sleeper=sleeps.append,
    )

    budget.checkpoint(4)

    assert sleeps == [1.0]


def test_archive_cut_does_not_overwrite_existing_evidence(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = ParquetEventStore(root, minimum_free_bytes=0, minimum_free_ratio=0)
    old_ts_ms = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1_000)
    batch = store.write_market_event_batch([_event("event-1", old_ts_ms)])
    manifest = freeze_live_public_cut(
        root,
        batch.path.parents[4],
        minimum_age_seconds=120,
        now_ns=batch.path.stat().st_mtime_ns + 121 * 1_000_000_000,
    )
    output = tmp_path / "evidence.json"
    output.write_text("preserved\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="덮어쓰지"):
        write_live_public_cut(manifest, output=output, archive_root=root)

    assert output.read_text(encoding="utf-8") == "preserved\n"
    assert len(tuple((root / ".research-pins").glob("*.json"))) == 1


def test_archive_cut_uses_the_same_single_research_resource_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "research.lock"
    descriptor = _acquire_resource_lock(lock_path)
    try:
        with pytest.raises(BlockingIOError):
            _acquire_resource_lock(lock_path)
    finally:
        _release_resource_lock(descriptor)
