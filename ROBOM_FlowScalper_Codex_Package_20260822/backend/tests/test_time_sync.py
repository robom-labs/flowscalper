# 공개 거래소 시각 보정과 지연 계산의 결정적 계약을 검증한다.

from __future__ import annotations

import pytest

from backend.app.time_sync import (
    VenueClockCalibration,
    estimate_venue_clock_calibration,
    estimate_venue_clock_offset_ms,
    venue_lag_ms,
)


async def test_clock_offset_uses_lowest_round_trip_sample() -> None:
    local_times = iter((1_000, 1_020, 1_100, 1_104, 1_200, 1_250))
    server_times = iter((1_510, 1_602, 1_725))

    async def fetch_server_time_ms() -> int:
        return next(server_times)

    offset_ms, round_trip_ms = await estimate_venue_clock_offset_ms(
        fetch_server_time_ms,
        lambda: next(local_times),
    )

    assert offset_ms == 500
    assert round_trip_ms == 4


async def test_clock_offset_rejects_empty_sampling() -> None:
    async def fetch_server_time_ms() -> int:
        return 1_000

    with pytest.raises(ValueError, match="양수"):
        await estimate_venue_clock_offset_ms(fetch_server_time_ms, lambda: 1_000, samples=0)


async def test_clock_calibration_uses_lowest_monotonic_round_trip_sample() -> None:
    local_times = iter((1_000, 1_020, 1_100, 1_104, 1_200, 1_250))
    monotonic_times = iter(
        (
            1_000_000_000,
            1_020_000_000,
            1_100_000_000,
            1_104_000_000,
            1_200_000_000,
            1_250_000_000,
            1_260_000_000,
        )
    )
    server_times = iter((1_510, 1_602, 1_725))

    async def fetch_server_time_ms() -> int:
        return next(server_times)

    calibration = await estimate_venue_clock_calibration(
        fetch_server_time_ms,
        lambda: next(local_times),
        lambda: next(monotonic_times),
    )

    assert calibration.measured_offset_ms == 500
    assert calibration.round_trip_ms == 4
    assert calibration.monotonic_anchor_ns == 1_260_000_000
    assert calibration.venue_anchor_ms == 1_760
    assert (
        calibration.lag_ms(
            venue_ts_ms=1_750,
            monotonic_ns=calibration.monotonic_anchor_ns,
        )
        == 10
    )


def test_venue_lag_corrects_local_clock_behind_exchange() -> None:
    assert venue_lag_ms(
        local_utc_ms=1_000,
        venue_ts_ms=2_800,
        venue_clock_offset_ms=2_000,
    ) == 200
    assert venue_lag_ms(
        local_utc_ms=1_000,
        venue_ts_ms=3_100,
        venue_clock_offset_ms=2_000,
    ) == 0


def test_monotonic_calibration_ignores_host_wall_clock_step() -> None:
    calibration = VenueClockCalibration(
        venue_anchor_ms=3_000,
        monotonic_anchor_ns=1_000_000_000,
        measured_offset_ms=2_000,
        round_trip_ms=10,
    )

    assert (
        calibration.lag_ms(
            venue_ts_ms=3_010,
            monotonic_ns=1_010_000_000,
        )
        == 0
    )
    assert (
        calibration.current_offset_ms(
            local_utc_ms=3_000,
            monotonic_ns=1_010_000_000,
        )
        == 10
    )
