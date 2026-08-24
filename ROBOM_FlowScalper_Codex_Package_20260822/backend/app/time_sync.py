"""공개 거래소 시각과 로컬 시각의 오프셋을 인증 없이 측정한다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VenueClockCalibration:
    """로컬 wall clock 변화와 분리된 거래소 시각 기준점을 보관한다."""

    venue_anchor_ms: float
    monotonic_anchor_ns: int
    measured_offset_ms: float
    round_trip_ms: float

    def venue_now_ms(self, monotonic_ns: int) -> float:
        elapsed_ms = (monotonic_ns - self.monotonic_anchor_ns) / 1_000_000
        return self.venue_anchor_ms + elapsed_ms

    def current_offset_ms(self, *, local_utc_ms: int, monotonic_ns: int) -> float:
        return self.venue_now_ms(monotonic_ns) - float(local_utc_ms)

    def lag_ms(self, *, venue_ts_ms: int, monotonic_ns: int) -> float:
        return max(0.0, self.venue_now_ms(monotonic_ns) - float(venue_ts_ms))


async def estimate_venue_clock_offset_ms(
    fetch_server_time_ms: Callable[[], Awaitable[int]],
    local_utc_ms: Callable[[], int],
    *,
    samples: int = 3,
) -> tuple[float, float]:
    """RTT가 가장 작은 공개 time 응답의 중간시각 기준 오프셋을 사용한다."""

    if samples <= 0:
        raise ValueError("시각 동기화 표본 수는 양수여야 합니다.")
    observations: list[tuple[float, float]] = []
    for _ in range(samples):
        started_ms = local_utc_ms()
        server_time_ms = await fetch_server_time_ms()
        finished_ms = local_utc_ms()
        round_trip_ms = max(0.0, float(finished_ms - started_ms))
        local_midpoint_ms = (started_ms + finished_ms) / 2
        observations.append((round_trip_ms, float(server_time_ms) - local_midpoint_ms))
    round_trip_ms, offset_ms = min(observations, key=lambda item: item[0])
    return offset_ms, round_trip_ms


async def estimate_venue_clock_calibration(
    fetch_server_time_ms: Callable[[], Awaitable[int]],
    local_utc_ms: Callable[[], int],
    monotonic_ns: Callable[[], int],
    *,
    samples: int = 3,
) -> VenueClockCalibration:
    """최저 RTT 공개 응답으로 wall clock 점프에 안전한 거래소 시각을 고정한다."""

    if samples <= 0:
        raise ValueError("시각 동기화 표본 수는 양수여야 합니다.")
    observations: list[tuple[float, float, int, float]] = []
    for _ in range(samples):
        started_utc_ms = local_utc_ms()
        started_monotonic_ns = monotonic_ns()
        server_time_ms = await fetch_server_time_ms()
        finished_monotonic_ns = monotonic_ns()
        finished_utc_ms = local_utc_ms()
        round_trip_ms = max(
            0.0,
            (finished_monotonic_ns - started_monotonic_ns) / 1_000_000,
        )
        midpoint_monotonic_ns = (started_monotonic_ns + finished_monotonic_ns) // 2
        local_midpoint_ms = (started_utc_ms + finished_utc_ms) / 2
        observations.append(
            (
                round_trip_ms,
                float(server_time_ms),
                midpoint_monotonic_ns,
                float(server_time_ms) - local_midpoint_ms,
            )
        )
    (
        round_trip_ms,
        observed_server_time_ms,
        midpoint_monotonic_ns,
        measured_offset_ms,
    ) = min(
        observations,
        key=lambda item: item[0],
    )
    anchor_monotonic_ns = monotonic_ns()
    venue_anchor_ms = (
        observed_server_time_ms + (anchor_monotonic_ns - midpoint_monotonic_ns) / 1_000_000
    )
    return VenueClockCalibration(
        venue_anchor_ms=venue_anchor_ms,
        monotonic_anchor_ns=anchor_monotonic_ns,
        measured_offset_ms=measured_offset_ms,
        round_trip_ms=round_trip_ms,
    )


def venue_lag_ms(
    *,
    local_utc_ms: int,
    venue_ts_ms: int,
    venue_clock_offset_ms: float,
) -> float:
    """거래소 시각 오프셋을 보정한 전송 지연을 0 이상으로 반환한다."""

    return max(0.0, float(local_utc_ms) + venue_clock_offset_ms - venue_ts_ms)
