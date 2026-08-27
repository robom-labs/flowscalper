"""LIVE 영속화가 장시간 replay archive 읽기보다 먼저 디스크를 쓰게 조정한다."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def storage_io_priority_gate(
    ledger_path: str | Path,
    *,
    exclusive: bool,
) -> Iterator[None]:
    """LIVE 저장은 배타, replay 파일 읽기는 공유 잠금으로 짧게 조율한다."""

    path = Path(ledger_path)
    lock_path = path.with_name(f"{path.name}.io-priority.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(
            descriptor,
            fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH,
        )
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
