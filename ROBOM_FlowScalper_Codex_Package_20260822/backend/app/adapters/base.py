"""공개 거래소 어댑터의 공통 인터페이스와 연결 감독을 정의한다."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from backend.app.domain.market import Instrument, Ticker
from backend.app.domain.models import Venue


class PublicVenueAdapter(Protocol):
    venue: Venue

    async def fetch_instruments(self) -> list[Instrument]: ...

    async def fetch_tickers(self) -> list[Ticker]: ...


class ConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    LIVE = "LIVE"
    RECONNECTING = "RECONNECTING"
    STALE = "STALE"


@dataclass(slots=True)
class ConnectionHealth:
    state: ConnectionState = ConnectionState.DISCONNECTED
    connected_monotonic_ns: int | None = None
    last_event_monotonic_ns: int | None = None
    reconnect_count: int = 0
    gap_count: int = 0
    resync_count: int = 0

    def mark_connected(self, now_ns: int) -> None:
        self.state = ConnectionState.CONNECTING
        self.connected_monotonic_ns = now_ns

    def mark_verified_event(self, now_ns: int, *, sequence_valid: bool) -> None:
        self.last_event_monotonic_ns = now_ns
        self.state = ConnectionState.LIVE if sequence_valid else ConnectionState.STALE

    def mark_gap(self) -> None:
        self.gap_count += 1
        self.state = ConnectionState.STALE

    def mark_resync(self) -> None:
        self.resync_count += 1
        self.state = ConnectionState.CONNECTING

    def mark_reconnecting(self) -> None:
        self.reconnect_count += 1
        self.state = ConnectionState.RECONNECTING


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    initial_ms: int = 500
    maximum_ms: int = 30_000
    jitter_fraction: float = 0.20

    def delay_ms(self, attempt: int, rng: random.Random) -> int:
        base = min(self.maximum_ms, self.initial_ms * (2 ** max(attempt, 0)))
        jitter = base * self.jitter_fraction
        return max(0, round(rng.uniform(base - jitter, base + jitter)))


@dataclass(frozen=True, slots=True)
class ConnectionSupervisor:
    planned_rotation_ms: int = 23 * 60 * 60 * 1000
    stale_after_ms: int = 1_000

    def should_rotate(self, health: ConnectionHealth, now_ns: int) -> bool:
        if health.connected_monotonic_ns is None:
            return False
        return (now_ns - health.connected_monotonic_ns) // 1_000_000 >= self.planned_rotation_ms

    def is_stale(self, health: ConnectionHealth, now_ns: int) -> bool:
        if health.last_event_monotonic_ns is None:
            return True
        return (now_ns - health.last_event_monotonic_ns) // 1_000_000 > self.stale_after_ms
