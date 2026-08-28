"""추가 서비스 없이 현재 PAPER 프로세스의 CPU·메모리·디스크를 측정한다."""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import threading
import time
from functools import lru_cache
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
        self._disk_total_bytes = 0
        self._disk_used_bytes = 0
        self._disk_free_bytes = 0
        self.refresh_storage_usage()

    def refresh_storage_usage(self) -> None:
        """느릴 수 있는 파일시스템 조회를 호출자가 선택한 worker에서 갱신한다."""

        usage = shutil.disk_usage(self.storage_path)
        self._disk_total_bytes = usage.total
        self._disk_used_bytes = usage.used
        self._disk_free_bytes = usage.free

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
        memory_bytes, memory_source = _process_memory_bytes()
        peak_memory_bytes, peak_memory_source = _peak_process_memory_bytes()
        if peak_memory_bytes < memory_bytes:
            # 커널 계측원의 순간 차이가 최대값 불변조건을 깨지 않게 현재 RSS를 하한으로 쓴다.
            peak_memory_bytes = memory_bytes
            peak_memory_source = f"{peak_memory_source}_FLOORED_BY_CURRENT"
        return {
            "process_cpu_percent": round(self._last_cpu_percent, 3),
            "process_memory_mb": round(memory_bytes / 1024**2, 3),
            "process_memory_source": memory_source,
            "process_memory_peak_mb": round(peak_memory_bytes / 1024**2, 3),
            "process_memory_peak_source": peak_memory_source,
            "process_threads": threading.active_count(),
            "process_uptime_seconds": round(now - self._started_monotonic, 3),
            "disk_total_mb": round(self._disk_total_bytes / 1024**2, 3),
            "disk_used_mb": round(self._disk_used_bytes / 1024**2, 3),
            "disk_free_mb": round(self._disk_free_bytes / 1024**2, 3),
            "disk_free_ratio": (
                round(self._disk_free_bytes / self._disk_total_bytes, 6)
                if self._disk_total_bytes
                else 0.0
            ),
        }


def _process_memory_bytes() -> tuple[int, str]:
    if sys.platform == "darwin":
        resident_bytes = _darwin_current_rss_bytes()
        if resident_bytes > 0:
            return resident_bytes, "CURRENT_RSS_LIBPROC"
    if sys.platform.startswith("linux"):
        resident_bytes = _linux_current_rss_bytes()
        if resident_bytes > 0:
            return resident_bytes, "CURRENT_RSS_PROCFS"
    if os.name == "nt":  # pragma: no cover - Windows runner에서 검증한다.
        resident_bytes = _windows_working_set_bytes()
        if resident_bytes > 0:
            return resident_bytes, "CURRENT_WORKING_SET"
    peak_bytes, _ = _peak_process_memory_bytes()
    return peak_bytes, "PEAK_MAX_RSS_FALLBACK" if peak_bytes > 0 else "UNAVAILABLE"


def _peak_process_memory_bytes() -> tuple[int, str]:
    if resource is not None:
        maximum_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        multiplier = 1 if sys.platform == "darwin" else 1024
        peak_bytes = maximum_rss * multiplier
        return (peak_bytes, "PEAK_MAX_RSS") if peak_bytes > 0 else (0, "UNAVAILABLE")
    if os.name == "nt":  # pragma: no cover - Windows runner에서 검증한다.
        peak_bytes = _windows_peak_working_set_bytes()
        return (peak_bytes, "PEAK_WORKING_SET") if peak_bytes > 0 else (0, "UNAVAILABLE")
    return 0, "UNAVAILABLE"


class _DarwinProcTaskInfo(ctypes.Structure):
    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("total_user", ctypes.c_uint64),
        ("total_system", ctypes.c_uint64),
        ("threads_user", ctypes.c_uint64),
        ("threads_system", ctypes.c_uint64),
        ("policy", ctypes.c_int32),
        ("faults", ctypes.c_int32),
        ("pageins", ctypes.c_int32),
        ("cow_faults", ctypes.c_int32),
        ("messages_sent", ctypes.c_int32),
        ("messages_received", ctypes.c_int32),
        ("syscalls_mach", ctypes.c_int32),
        ("syscalls_unix", ctypes.c_int32),
        ("context_switches", ctypes.c_int32),
        ("thread_count", ctypes.c_int32),
        ("running_threads", ctypes.c_int32),
        ("priority", ctypes.c_int32),
    ]


@lru_cache(maxsize=1)
def _darwin_libproc() -> ctypes.CDLL:
    library = ctypes.CDLL("/usr/lib/libproc.dylib")
    library.proc_pidinfo.argtypes = [
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int32,
    ]
    library.proc_pidinfo.restype = ctypes.c_int32
    return library


def _darwin_current_rss_bytes() -> int:
    try:
        task_info = _DarwinProcTaskInfo()
        returned_size = _darwin_libproc().proc_pidinfo(
            os.getpid(),
            4,  # PROC_PIDTASKINFO
            0,
            ctypes.byref(task_info),
            ctypes.sizeof(task_info),
        )
    except OSError:
        return 0
    if returned_size != ctypes.sizeof(task_info):
        return 0
    return int(task_info.resident_size)


def _linux_current_rss_bytes() -> int:
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            fields = statm.read().split()
        resident_pages = int(fields[1])
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, IndexError):
        return 0
    return resident_pages * page_size


def _windows_working_set_bytes() -> int:
    working_set, _ = _windows_memory_bytes()
    return working_set


def _windows_peak_working_set_bytes() -> int:
    _, peak_working_set = _windows_memory_bytes()
    return peak_working_set


def _windows_memory_bytes() -> tuple[int, int]:
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
    if not success:
        return 0, 0
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
