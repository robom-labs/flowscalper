# V9 Directional Change의 연속성·중복·데이터 품질 guard를 검증한다.
"""Directional Change 연속성·중복·데이터 품질 guard를 검증한다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.domain.models import Venue
from backend.app.features import (
    DCMidObservation,
    DCState,
    DCUpdateReason,
    DirectionalChangeEngine,
    DirectionalChangeInputError,
    FixedThresholdProvider,
)


def _engine(*, maximum_spread_bps: Decimal | None = None) -> DirectionalChangeEngine:
    return DirectionalChangeEngine(
        run_id="run-dc",
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        profile_id="FAST",
        threshold_provider=FixedThresholdProvider(
            profile_id="FAST",
            threshold=Decimal("0.01"),
        ),
        maximum_lag_ms=Decimal("500"),
        maximum_spread_bps=maximum_spread_bps,
    )


def _observation(
    sequence: int,
    mid: str,
    *,
    event_id: str | None = None,
    venue_ts_ms: int | None = None,
    receive_monotonic_ns: int | None = None,
    sequence_start: int | None = None,
    sequence_end: int | None = None,
    previous_sequence_end: int | None = None,
    sequence_valid: bool = True,
    stale: bool = False,
    lag_ms: Decimal | None = Decimal("10"),
    half_spread: Decimal = Decimal("0.01"),
) -> DCMidObservation:
    midpoint = Decimal(mid)
    return DCMidObservation(
        run_id="run-dc",
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        event_id=event_id or f"event-{sequence}",
        venue_ts_ms=venue_ts_ms if venue_ts_ms is not None else sequence * 100,
        receive_monotonic_ns=(
            receive_monotonic_ns if receive_monotonic_ns is not None else sequence * 1_000
        ),
        bid=midpoint - half_spread,
        ask=midpoint + half_spread,
        sequence_start=sequence if sequence_start is None else sequence_start,
        sequence_end=sequence if sequence_end is None else sequence_end,
        previous_sequence_end=(
            sequence - 1
            if previous_sequence_end is None and sequence > 1
            else previous_sequence_end
        ),
        sequence_valid=sequence_valid,
        stale=stale,
        lag_ms=lag_ms,
    )


def test_duplicate_is_ignored_without_reset_or_state_change() -> None:
    engine = _engine()
    observation = _observation(1, "100")

    first = engine.update(observation)
    duplicate = engine.update(observation)

    assert first.accepted is True
    assert duplicate.accepted is False
    assert duplicate.reason is DCUpdateReason.DUPLICATE_EVENT
    assert duplicate.reset is None
    assert duplicate.snapshot == first.snapshot


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"stale": True}, DCUpdateReason.STALE),
        ({"lag_ms": None}, DCUpdateReason.LAG_UNKNOWN),
        ({"lag_ms": Decimal("500.01")}, DCUpdateReason.LAG_EXCEEDED),
        ({"sequence_valid": False}, DCUpdateReason.SEQUENCE_INVALID),
    ],
)
def test_data_quality_fault_resets_continuity(
    overrides: dict[str, object],
    reason: DCUpdateReason,
) -> None:
    engine = _engine()
    engine.update(_observation(1, "100"))
    engine.update(_observation(2, "101"))

    update = engine.update(_observation(3, "90", **overrides))

    assert update.accepted is False
    assert update.reason is reason
    assert update.reset is not None
    assert update.reset.reason is reason
    assert update.snapshot.state is DCState.UNINITIALIZED
    assert update.snapshot.threshold is None
    assert update.snapshot.running_high is None
    assert update.snapshot.continuity_epoch == 1


def test_stale_gap_cannot_create_crossing_after_recovery() -> None:
    engine = _engine()
    engine.update(_observation(1, "100"))
    engine.update(_observation(2, "101"))
    stale = engine.update(_observation(3, "90", stale=True))

    reseeded = engine.update(_observation(4, "90"))
    below_new_threshold = engine.update(_observation(5, "90.89"))
    new_confirmation = engine.update(_observation(6, "90.90"))

    assert stale.reset is not None
    assert reseeded.event is None
    assert reseeded.snapshot.event_start_price == Decimal("90")
    assert below_new_threshold.event is None
    assert new_confirmation.event is not None
    assert new_confirmation.event.initialization is True
    assert new_confirmation.event.extreme_before_confirmation == Decimal("90")


def test_forward_sequence_gap_resets_and_uses_next_event_as_new_seed() -> None:
    engine = _engine()
    engine.update(_observation(1, "100"))
    engine.update(_observation(2, "101"))

    gap = engine.update(
        _observation(
            5,
            "90",
            sequence_start=5,
            sequence_end=5,
            previous_sequence_end=4,
        )
    )
    recovered = engine.update(_observation(6, "90"))

    assert gap.reason is DCUpdateReason.SEQUENCE_GAP
    assert gap.reset is not None
    assert gap.snapshot.last_sequence_end == 5
    assert recovered.accepted is True
    assert recovered.snapshot.event_start_price == Decimal("90")


def test_old_sequence_resets_without_moving_ordering_watermark_back() -> None:
    engine = _engine()
    engine.update(_observation(10, "100", previous_sequence_end=None))
    old = engine.update(
        _observation(
            9,
            "90",
            event_id="old-event",
            venue_ts_ms=900,
            receive_monotonic_ns=900,
            previous_sequence_end=None,
        )
    )

    assert old.reason is DCUpdateReason.SEQUENCE_OUT_OF_ORDER
    assert old.reset is not None
    assert old.snapshot.last_sequence_end == 10
    assert old.snapshot.last_venue_ts_ms == 1_000


def test_out_of_order_venue_timestamp_resets_continuity() -> None:
    engine = _engine()
    engine.update(_observation(1, "100", venue_ts_ms=1_000))

    update = engine.update(_observation(2, "101", venue_ts_ms=999))

    assert update.reason is DCUpdateReason.VENUE_TS_OUT_OF_ORDER
    assert update.reset is not None


def test_out_of_order_receive_time_resets_continuity() -> None:
    engine = _engine()
    engine.update(_observation(1, "100", receive_monotonic_ns=2_000))

    update = engine.update(_observation(2, "101", receive_monotonic_ns=1_999))

    assert update.reason is DCUpdateReason.RECEIVE_TIME_OUT_OF_ORDER
    assert update.reset is not None


def test_invalid_sequence_range_resets_continuity() -> None:
    engine = _engine()

    update = engine.update(
        _observation(
            2,
            "100",
            sequence_start=3,
            sequence_end=2,
            previous_sequence_end=None,
        )
    )

    assert update.reason is DCUpdateReason.SEQUENCE_RANGE_INVALID
    assert update.reset is not None


def test_spread_gate_resets_continuity() -> None:
    engine = _engine(maximum_spread_bps=Decimal("5"))

    update = engine.update(
        _observation(1, "100", half_spread=Decimal("0.10"))
    )

    assert update.reason is DCUpdateReason.SPREAD_EXCEEDED
    assert update.reset is not None


def test_engine_rejects_mixed_identity() -> None:
    engine = _engine()
    observation = DCMidObservation(
        run_id="other-run",
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        event_id="wrong-run",
        venue_ts_ms=100,
        receive_monotonic_ns=1_000,
        bid=Decimal("99.99"),
        ask=Decimal("100.01"),
    )

    with pytest.raises(DirectionalChangeInputError, match="섞을 수 없습니다"):
        engine.update(observation)


def test_reset_ids_and_checksums_are_deterministic() -> None:
    first = _engine()
    second = _engine()
    observations = [
        _observation(1, "100"),
        _observation(2, "101"),
        _observation(3, "90", stale=True),
    ]

    first_updates = [first.update(observation) for observation in observations]
    second_updates = [second.update(observation) for observation in observations]

    assert first_updates[-1].reset == second_updates[-1].reset
    assert first_updates[-1].snapshot.checksum == second_updates[-1].snapshot.checksum
