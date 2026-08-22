"""추가 서비스 없이 현재 PAPER 프로세스의 CPU·메모리·디스크를 측정한다."""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - Windows 전용 fallback은 별도 함수가 담당한다.
    resource = None  # type: ignore[assignment]


class ProcessResourceSampler:
    """연속 두 관측 사이의 CPU와 현재 프로세스·디스크 상태를 반환한다."""

    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path
        self._started_monotonic = time.monotonic()
        self._last_monotonic = self._started_monotonic
        self._last_process_seconds = time.process_time()
        self._last_cpu_percent = 0.0

    def sample(self) -> dict[str, object]:
        now = time.monotonic()
        process_seconds = time.process_time()
        wall_delta = now - self._last_monotonic
        if wall_delta > 0:
            self._last_cpu_percent = max(
                0.0,
                (process_seconds - self._last_process_seconds) / wall_delta * 100,
            )
        self._last_monotonic = now
        self._last_process_seconds = process_seconds
        usage = shutil.disk_usage(self.storage_path)
        memory_bytes, memory_source = _process_memory_bytes()
        return {
            "process_cpu_percent": round(self._last_cpu_percent, 3),
            "process_memory_mb": round(memory_bytes / 1024**2, 3),
            "process_memory_source": memory_source,
            "process_threads": threading.active_count(),
            "process_uptime_seconds": round(now - self._started_monotonic, 3),
            "disk_total_mb": round(usage.total / 1024**2, 3),
            "disk_used_mb": round(usage.used / 1024**2, 3),
            "disk_free_mb": round(usage.free / 1024**2, 3),
            "disk_free_ratio": round(usage.free / usage.total, 6) if usage.total else 0.0,
        }


def _process_memory_bytes() -> tuple[int, str]:
    if resource is not None:
        maximum_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        multiplier = 1 if sys.platform == "darwin" else 1024
        return maximum_rss * multiplier, "MAX_RSS"
    if os.name == "nt":  # pragma: no cover - Windows runner에서 검증한다.
        return _windows_working_set_bytes(), "WORKING_SET"
    return 0, "UNAVAILABLE"


def _windows_working_set_bytes() -> int:
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
    success = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
        process,
        ctypes.byref(counters),
        counters.cb,
    )
    return int(counters.WorkingSetSize) if success else 0
