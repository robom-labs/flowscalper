"""공개 거래소 시각과 로컬 시각의 오프셋을 인증 없이 측정한다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


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


def venue_lag_ms(
    *,
    local_utc_ms: int,
    venue_ts_ms: int,
    venue_clock_offset_ms: float,
) -> float:
    """거래소 시각 오프셋을 보정한 전송 지연을 0 이상으로 반환한다."""

    return max(0.0, float(local_utc_ms) + venue_clock_offset_ms - venue_ts_ms)
