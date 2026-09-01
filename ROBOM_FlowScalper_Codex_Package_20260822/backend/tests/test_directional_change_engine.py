# V9 Directional Change의 실제 crossing과 threshold 동결 계약을 검증한다.
"""Directional Change 실제 crossing과 threshold 동결을 검증한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from backend.app.domain.models import Venue
from backend.app.features import (
    CompletedCandleThreshold,
    CompletedCandleThresholdProvider,
    DCEventType,
    DCMidObservation,
    DCState,
    DirectionalChangeEngine,
    FixedThresholdProvider,
    ThresholdDecision,
    ThresholdProviderError,
)


def _observation(
    sequence: int,
    mid: str,
    *,
    event_id: str | None = None,
    venue_ts_ms: int | None = None,
) -> DCMidObservation:
    midpoint = Decimal(mid)
    half_spread = Decimal("0.01")
    return DCMidObservation(
        run_id="run-dc",
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        event_id=event_id or f"event-{sequence}",
        venue_ts_ms=venue_ts_ms if venue_ts_ms is not None else sequence * 100,
        receive_monotonic_ns=sequence * 1_000,
        sequence_start=sequence,
        sequence_end=sequence,
        previous_sequence_end=sequence - 1 if sequence > 1 else None,
        bid=midpoint - half_spread,
        ask=midpoint + half_spread,
        lag_ms=Decimal("10"),
    )


@dataclass
class _QueuedThresholdProvider:
    thresholds: list[Decimal]
    calls: list[int] = field(default_factory=list)

    def next_threshold(
        self,
        *,
        venue: Venue,
        symbol: str,
        profile_id: str,
        as_of_ts_ms: int,
    ) -> ThresholdDecision | None:
        del venue, symbol
        self.calls.append(as_of_ts_ms)
        index = min(len(self.calls) - 1, len(self.thresholds) - 1)
        return ThresholdDecision(
            profile_id=profile_id,
            threshold=self.thresholds[index],
            provider_version="QUEUE_TEST_V1",
            source_kind="TEST",
            inputs_digest=f"decision-{index}",
        )


def _engine(
    provider: _QueuedThresholdProvider | FixedThresholdProvider | None = None,
) -> DirectionalChangeEngine:
    return DirectionalChangeEngine(
        run_id="run-dc",
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        profile_id="FAST",
        threshold_provider=provider
        or FixedThresholdProvider(profile_id="FAST", threshold=Decimal("0.01")),
    )


def test_records_first_observed_mid_instead_of_theoretical_threshold() -> None:
    engine = _engine()

    seeded = engine.update(_observation(1, "100"))
    confirmed = engine.update(_observation(2, "101.50"))

    assert seeded.event is None
    assert confirmed.event is not None
    assert confirmed.event.event_type is DCEventType.UPTURN
    assert confirmed.event.theoretical_confirmation_price == Decimal("101.00")
    assert confirmed.event.actual_confirmation_price == Decimal("101.50")
    assert confirmed.event.source_event_id == "event-2"
    assert confirmed.event.confirmation_ts_ms == 200
    assert confirmed.event.initialization is True
    assert confirmed.event.entry_eligible is False
    assert confirmed.snapshot.state is DCState.UP_RUN


def test_exact_threshold_confirms_and_smaller_move_does_not() -> None:
    engine = _engine()

    engine.update(_observation(1, "100"))
    below = engine.update(_observation(2, "100.9999"))
    exact = engine.update(_observation(3, "101"))

    assert below.event is None
    assert below.snapshot.state is DCState.UNINITIALIZED
    assert exact.event is not None
    assert exact.event.actual_confirmation_price == Decimal("101")


def test_threshold_is_frozen_for_event_and_refreshed_only_after_confirmation() -> None:
    provider = _QueuedThresholdProvider(
        [Decimal("0.01"), Decimal("0.02"), Decimal("0.03")]
    )
    engine = _engine(provider)

    engine.update(_observation(1, "100"))
    no_confirmation = engine.update(_observation(2, "100.50"))
    initial_upturn = engine.update(_observation(3, "101"))
    engine.update(_observation(4, "105"))
    still_up = engine.update(_observation(5, "102.91"))
    downturn = engine.update(_observation(6, "102.90"))

    assert no_confirmation.event is None
    assert provider.calls == [100, 300, 600]
    assert initial_upturn.event is not None
    assert initial_upturn.event.threshold == Decimal("0.01")
    assert initial_upturn.snapshot.threshold == Decimal("0.02")
    assert still_up.event is None
    assert downturn.event is not None
    assert downturn.event.event_type is DCEventType.DOWNTURN
    assert downturn.event.threshold == Decimal("0.02")
    assert downturn.event.theoretical_confirmation_price == Decimal("102.90")
    assert downturn.snapshot.threshold == Decimal("0.03")


def test_completed_overshoot_uses_frozen_threshold_of_completed_run() -> None:
    engine = _engine()

    engine.update(_observation(1, "100"))
    engine.update(_observation(2, "101"))
    engine.update(_observation(3, "105"))
    downturn = engine.update(_observation(4, "103.95"))

    assert downturn.event is not None
    assert downturn.event.event_type is DCEventType.DOWNTURN
    assert downturn.event.completed_overshoot == Decimal("4")
    assert downturn.event.completed_overshoot_ratio == Decimal("4") / Decimal("1.01")
    assert downturn.event.extreme_before_confirmation == Decimal("105")
    assert downturn.event.actual_confirmation_price == Decimal("103.95")


def test_downward_initialization_and_symmetric_upturn() -> None:
    engine = _engine()

    engine.update(_observation(1, "100"))
    initial_downturn = engine.update(_observation(2, "99"))
    engine.update(_observation(3, "95"))
    upturn = engine.update(_observation(4, "95.95"))

    assert initial_downturn.event is not None
    assert initial_downturn.event.event_type is DCEventType.DOWNTURN
    assert upturn.event is not None
    assert upturn.event.event_type is DCEventType.UPTURN
    assert upturn.event.theoretical_confirmation_price == Decimal("95.95")
    assert upturn.event.completed_overshoot == Decimal("4")
    assert upturn.snapshot.state is DCState.UP_RUN


def test_same_stream_produces_same_event_ids_and_checksums() -> None:
    first = _engine()
    second = _engine()
    observations = [
        _observation(1, "100"),
        _observation(2, "101.25"),
        _observation(3, "105"),
        _observation(4, "103.90"),
    ]

    first_updates = [first.update(observation) for observation in observations]
    second_updates = [second.update(observation) for observation in observations]

    assert [update.event for update in first_updates] == [
        update.event for update in second_updates
    ]
    assert [update.snapshot.checksum for update in first_updates] == [
        update.snapshot.checksum for update in second_updates
    ]


def test_dedupe_memory_is_fixed_capacity() -> None:
    engine = DirectionalChangeEngine(
        run_id="run-dc",
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        profile_id="FAST",
        threshold_provider=FixedThresholdProvider(
            profile_id="FAST",
            threshold=Decimal("0.50"),
        ),
        dedupe_capacity=8,
    )

    for sequence in range(1, 101):
        engine.update(_observation(sequence, "100"))

    assert len(engine._seen_event_ids) == 8
    assert len(engine._event_id_order) == 8


@dataclass
class _CompletedSource:
    completed: CompletedCandleThreshold | None
    calls: list[int] = field(default_factory=list)

    def latest_completed_threshold(
        self,
        *,
        venue: Venue,
        symbol: str,
        profile_id: str,
        as_of_ts_ms: int,
    ) -> CompletedCandleThreshold | None:
        del venue, symbol, profile_id
        self.calls.append(as_of_ts_ms)
        return self.completed


def test_completed_candle_provider_keeps_as_of_provenance() -> None:
    source = _CompletedSource(
        CompletedCandleThreshold(
            profile_id="FAST",
            threshold=Decimal("0.0042"),
            source_interval="15m",
            close_ts_ms=900,
            inputs_digest="atr14-close-v1",
        )
    )
    provider = CompletedCandleThresholdProvider(source=source, provider_version="ATR_FAST_V1")

    decision = provider.next_threshold(
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        profile_id="FAST",
        as_of_ts_ms=1_000,
    )

    assert decision is not None
    assert decision.threshold == Decimal("0.0042")
    assert decision.source_kind == "COMPLETED_CANDLE"
    assert decision.source_close_ts_ms == 900
    assert decision.inputs_digest == "atr14-close-v1"
    assert source.calls == [1_000]


def test_completed_candle_provider_rejects_future_candle() -> None:
    source = _CompletedSource(
        CompletedCandleThreshold(
            profile_id="FAST",
            threshold=Decimal("0.0042"),
            source_interval="15m",
            close_ts_ms=1_001,
            inputs_digest="future",
        )
    )
    provider = CompletedCandleThresholdProvider(source=source, provider_version="ATR_FAST_V1")

    with pytest.raises(ThresholdProviderError, match="미래 완료봉"):
        provider.next_threshold(
            venue=Venue.BINANCE_USDM,
            symbol="BTCUSDT",
            profile_id="FAST",
            as_of_ts_ms=1_000,
        )
