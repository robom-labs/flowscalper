# 비정상 대형 WAL을 닫힌 원장 복제본 보존 뒤 안전하게 체크포인트한다.
"""macOS PAPER 서비스 시작 전 대형 WAL 복구 계약을 실행한다."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.storage.integrity import (
    LedgerIntegrityError,
    _darwin_clonefile,
    checkpoint_closed_ledger,
)

DEFAULT_MAX_WAL_BYTES = 64 * 1024**2


def _open_pids(paths: Sequence[Path]) -> tuple[int, ...]:
    existing = [str(path.resolve()) for path in paths if path.exists()]
    if not existing:
        return ()
    result = subprocess.run(  # noqa: S603
        ["/usr/sbin/lsof", "-t", "--", *existing],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise LedgerIntegrityError(f"대형 WAL handle 확인 실패: {result.stderr.strip()}")
    return tuple(sorted({int(row) for row in result.stdout.splitlines() if row.strip()}))


def _result_mapping(value: object) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    raise TypeError("checkpoint 결과가 기계판독 객체가 아닙니다.")


def recover_oversized_closed_wal(
    source_path: Path,
    snapshot_root: Path,
    *,
    max_wal_bytes: int = DEFAULT_MAX_WAL_BYTES,
    clone_file: Callable[[Path, Path], None] | None = None,
    open_pids: Callable[[Sequence[Path]], tuple[int, ...]] | None = None,
    checkpoint: Callable[[Path], object] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """외부 writer가 없는 비정상 대형 WAL만 복제·체크포인트한다."""

    if max_wal_bytes <= 0:
        raise ValueError("WAL 상한은 양수여야 합니다.")
    source_path = source_path.resolve()
    wal_path = Path(f"{source_path}-wal")
    shm_path = Path(f"{source_path}-shm")
    wal_size = wal_path.stat().st_size if wal_path.exists() else 0
    base_result: dict[str, Any] = {
        "schema": "flowscalper.startup_oversized_wal_recovery.v1",
        "source_path": str(source_path),
        "wal_path": str(wal_path),
        "wal_size_bytes_before": wal_size,
        "max_wal_bytes": max_wal_bytes,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_requested": False,
    }
    if not source_path.exists() and wal_size == 0:
        return {**base_result, "status": "NO_ACTION_NEW_LEDGER", "snapshot_path": None}
    if not source_path.is_file():
        raise LedgerIntegrityError(f"대형 WAL 원장 파일이 없습니다: {source_path}")
    if wal_size <= max_wal_bytes:
        return {**base_result, "status": "NO_ACTION_WITHIN_LIMIT", "snapshot_path": None}

    candidates = (source_path, wal_path, shm_path)
    holders = (open_pids or _open_pids)(candidates)
    if holders:
        raise LedgerIntegrityError(f"대형 WAL 원장을 연 process가 있습니다: {holders}")

    snapshot_root = snapshot_root.resolve()
    snapshot_root.mkdir(parents=True, exist_ok=True)
    if source_path.stat().st_dev != snapshot_root.stat().st_dev:
        raise LedgerIntegrityError("대형 WAL 복구본은 원장과 같은 APFS device에 있어야 합니다.")
    timestamp = (now or (lambda: datetime.now(UTC)))().strftime("%Y%m%dT%H%M%S.%fZ")
    snapshot_dir = snapshot_root / f"startup-oversized-wal-{timestamp}"
    snapshot_dir.mkdir(mode=0o700)
    clone_implementation = clone_file or _darwin_clonefile
    cloned: list[dict[str, object]] = []
    for source_file in candidates:
        if not source_file.exists():
            continue
        target_file = snapshot_dir / source_file.name
        clone_implementation(source_file, target_file)
        source_size = source_file.stat().st_size
        target_size = target_file.stat().st_size
        if source_size != target_size:
            raise LedgerIntegrityError(
                f"대형 WAL 복구본 크기가 다릅니다: {source_file.name} "
                f"source={source_size}, snapshot={target_size}"
            )
        cloned.append(
            {
                "source": str(source_file),
                "snapshot": str(target_file),
                "size_bytes": source_size,
            }
        )

    checkpoint_result = _result_mapping((checkpoint or checkpoint_closed_ledger)(source_path))
    wal_size_after = wal_path.stat().st_size if wal_path.exists() else 0
    if wal_size_after != 0:
        raise LedgerIntegrityError(f"시작 전 대형 WAL이 0byte가 아닙니다: {wal_size_after}")
    result = {
        **base_result,
        "status": "RECOVERED",
        "snapshot_path": str(snapshot_dir),
        "snapshot_files": cloned,
        "checkpoint": checkpoint_result,
        "wal_size_bytes_after": wal_size_after,
    }
    _write_json_atomic(snapshot_dir / "recovery-manifest.json", result)
    return result


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="닫힌 PAPER 원장의 비정상 대형 WAL을 복구합니다.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-wal-bytes", type=int, default=DEFAULT_MAX_WAL_BYTES)
    arguments = parser.parse_args()
    try:
        result = recover_oversized_closed_wal(
            arguments.source,
            arguments.snapshot_root,
            max_wal_bytes=arguments.max_wal_bytes,
        )
    except (LedgerIntegrityError, OSError, ValueError) as error:
        result = {
            "schema": "flowscalper.startup_oversized_wal_recovery.v1",
            "status": "FAIL",
            "source_path": str(arguments.source.resolve()),
            "error": {"type": type(error).__name__, "message": str(error)},
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_requested": False,
        }
        _write_json_atomic(arguments.output.resolve(), result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(1) from error
    _write_json_atomic(arguments.output.resolve(), result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
