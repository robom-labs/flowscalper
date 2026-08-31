# macOS 서비스 시작 전 대형 WAL의 보존·거부·체크포인트 계약을 검증한다.
"""비정상 대형 WAL 복구 회귀검사다."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.app.storage.integrity import LedgerIntegrityError
from scripts.recover_oversized_wal import recover_oversized_closed_wal


def test_new_ledger_requires_no_startup_recovery(tmp_path: Path) -> None:
    result = recover_oversized_closed_wal(
        tmp_path / "new.sqlite3",
        tmp_path / "snapshots",
        max_wal_bytes=10,
    )

    assert result["status"] == "NO_ACTION_NEW_LEDGER"
    assert result["real_orders_enabled"] is False


def test_small_wal_requires_no_startup_recovery(tmp_path: Path) -> None:
    source = tmp_path / "ledger.sqlite3"
    source.write_bytes(b"database")
    Path(f"{source}-wal").write_bytes(b"small")

    result = recover_oversized_closed_wal(
        source,
        tmp_path / "snapshots",
        max_wal_bytes=10,
    )

    assert result["status"] == "NO_ACTION_WITHIN_LIMIT"
    assert result["wal_size_bytes_before"] == 5


def test_oversized_wal_refuses_open_writer(tmp_path: Path) -> None:
    source = tmp_path / "ledger.sqlite3"
    source.write_bytes(b"database")
    Path(f"{source}-wal").write_bytes(b"oversized-wal")

    with pytest.raises(LedgerIntegrityError, match="연 process"):
        recover_oversized_closed_wal(
            source,
            tmp_path / "snapshots",
            max_wal_bytes=5,
            open_pids=lambda _paths: (1234,),
        )


def test_oversized_closed_wal_is_cloned_before_checkpoint(tmp_path: Path) -> None:
    source = tmp_path / "ledger.sqlite3"
    wal = Path(f"{source}-wal")
    shm = Path(f"{source}-shm")
    source.write_bytes(b"database-before-checkpoint")
    wal.write_bytes(b"oversized-wal-before-checkpoint")
    shm.write_bytes(b"shared-memory")
    checkpoint_observations: list[bytes] = []

    def checkpoint(path: Path) -> dict[str, object]:
        snapshot_dirs = list((tmp_path / "snapshots").iterdir())
        assert len(snapshot_dirs) == 1
        checkpoint_observations.append((snapshot_dirs[0] / wal.name).read_bytes())
        wal.unlink()
        return {
            "source_path": str(path),
            "busy": 0,
            "log_frame_count": 0,
            "checkpointed_frame_count": 0,
            "wal_size_bytes_after": 0,
        }

    result = recover_oversized_closed_wal(
        source,
        tmp_path / "snapshots",
        max_wal_bytes=5,
        clone_file=lambda before, after: shutil.copy2(before, after),
        open_pids=lambda _paths: (),
        checkpoint=checkpoint,
    )

    snapshot = Path(str(result["snapshot_path"]))
    assert result["status"] == "RECOVERED"
    assert result["wal_size_bytes_after"] == 0
    assert checkpoint_observations == [b"oversized-wal-before-checkpoint"]
    assert (snapshot / source.name).read_bytes() == b"database-before-checkpoint"
    assert (snapshot / wal.name).read_bytes() == b"oversized-wal-before-checkpoint"
    assert (snapshot / shm.name).read_bytes() == b"shared-memory"
    manifest = json.loads((snapshot / "recovery-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "RECOVERED"
    assert manifest["real_orders_enabled"] is False
