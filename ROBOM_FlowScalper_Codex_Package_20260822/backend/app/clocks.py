"""실시간·리플레이·테스트에 주입 가능한 시계를 제공한다."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol


class Clock(Protocol):
    def utc_ms(self) -> int: ...

    def monotonic_ns(self) -> int: ...


class SystemClock:
    def utc_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


@dataclass(slots=True)
class TestClock:
    current_utc_ms: int = 1_750_000_000_000
    current_monotonic_ns: int = 1_000_000_000

    def utc_ms(self) -> int:
        return self.current_utc_ms

    def monotonic_ns(self) -> int:
        return self.current_monotonic_ns

    def advance_ms(self, milliseconds: int) -> None:
        self.current_utc_ms += milliseconds
        self.current_monotonic_ns += milliseconds * 1_000_000
