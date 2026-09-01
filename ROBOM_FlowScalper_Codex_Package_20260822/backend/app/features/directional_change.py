# 실제 관측 mid로 lookahead 없는 Directional Change 상태를 갱신한다.
"""Directional Change 독립 코어를 제공한다."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from backend.app.domain.models import Venue

_BPS = Decimal(10_000)


class DirectionalChangeInputError(ValueError):
    """Directional Change 입력 또는 설정이 유효하지 않을 때 발생한다."""


class ThresholdProviderError(ValueError):
    """Threshold provider가 lookahead 또는 잘못된 결정을 반환할 때 발생한다."""


class DCState(StrEnum):
    UNINITIALIZED = "UNINITIALIZED"
    UP_RUN = "UP_RUN"
    DOWN_RUN = "DOWN_RUN"


class DCEventType(StrEnum):
    UPTURN = "UPTURN"
    DOWNTURN = "DOWNTURN"


class DCUpdateReason(StrEnum):
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    THRESHOLD_UNAVAILABLE = "THRESHOLD_UNAVAILABLE"
    STALE = "STALE"
    LAG_UNKNOWN = "LAG_UNKNOWN"
    LAG_EXCEEDED = "LAG_EXCEEDED"
    SEQUENCE_INVALID = "SEQUENCE_INVALID"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    SEQUENCE_OUT_OF_ORDER = "SEQUENCE_OUT_OF_ORDER"
    SEQUENCE_RANGE_INVALID = "SEQUENCE_RANGE_INVALID"
    VENUE_TS_OUT_OF_ORDER = "VENUE_TS_OUT_OF_ORDER"
    RECEIVE_TIME_OUT_OF_ORDER = "RECEIVE_TIME_OUT_OF_ORDER"
    SPREAD_EXCEEDED = "SPREAD_EXCEEDED"


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise DirectionalChangeInputError("Directional Change 값은 유한해야 합니다.")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DCMidObservation:
    run_id: str
    venue: Venue
    symbol: str
    event_id: str
    venue_ts_ms: int
    receive_monotonic_ns: int
    bid: Decimal
    ask: Decimal
    sequence_start: int | None = None
    sequence_end: int | None = None
    previous_sequence_end: int | None = None
    sequence_valid: bool = True
    stale: bool = False
    lag_ms: Decimal | None = Decimal(0)

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.symbol.strip() or not self.event_id.strip():
            raise DirectionalChangeInputError("run, symbol, event ID가 필요합니다.")
        if self.venue_ts_ms < 0 or self.receive_monotonic_ns < 0:
            raise DirectionalChangeInputError("이벤트 시각은 음수일 수 없습니다.")
        if not self.bid.is_finite() or not self.ask.is_finite():
            raise DirectionalChangeInputError("bid와 ask는 유한해야 합니다.")
        if self.bid <= 0 or self.ask <= self.bid:
            raise DirectionalChangeInputError("bid는 양수이고 ask는 bid보다 커야 합니다.")
        if self.lag_ms is not None and (not self.lag_ms.is_finite() or self.lag_ms < 0):
            raise DirectionalChangeInputError("lag는 유한한 음이 아닌 값이어야 합니다.")

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal(2)

    @property
    def spread_bps(self) -> Decimal:
        return (self.ask - self.bid) / self.mid * _BPS


@dataclass(frozen=True, slots=True)
class ThresholdDecision:
    profile_id: str
    threshold: Decimal
    provider_version: str
    source_kind: str
    source_interval: str | None = None
    source_close_ts_ms: int | None = None
    inputs_digest: str | None = None
    decision_id: str = ""

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.provider_version.strip():
            raise ThresholdProviderError("Threshold profile과 provider version이 필요합니다.")
        if not self.source_kind.strip():
            raise ThresholdProviderError("Threshold source kind가 필요합니다.")
        if not self.threshold.is_finite() or not Decimal(0) < self.threshold < Decimal(1):
            raise ThresholdProviderError("Threshold는 0보다 크고 1보다 작아야 합니다.")
        if self.source_close_ts_ms is not None and self.source_close_ts_ms < 0:
            raise ThresholdProviderError("완료봉 시각은 음수일 수 없습니다.")
        canonical = {
            "inputs_digest": self.inputs_digest,
            "profile_id": self.profile_id,
            "provider_version": self.provider_version,
            "source_close_ts_ms": self.source_close_ts_ms,
            "source_interval": self.source_interval,
            "source_kind": self.source_kind,
            "threshold": _decimal_text(self.threshold),
        }
        expected_id = f"dc-threshold-{_digest(canonical)[:32]}"
        if self.decision_id and self.decision_id != expected_id:
            raise ThresholdProviderError("Threshold decision ID가 내용과 일치하지 않습니다.")
        object.__setattr__(self, "decision_id", expected_id)


class ThresholdProvider(Protocol):
    def next_threshold(
        self,
        *,
        venue: Venue,
        symbol: str,
        profile_id: str,
        as_of_ts_ms: int,
    ) -> ThresholdDecision | None:
        """as-of 시점에 알 수 있는 다음 event threshold를 반환한다."""


@dataclass(frozen=True, slots=True)
class CompletedCandleThreshold:
    profile_id: str
    threshold: Decimal
    source_interval: str
    close_ts_ms: int
    inputs_digest: str

    def __post_init__(self) -> None:
        if not self.profile_id.strip() or not self.source_interval.strip():
            raise ThresholdProviderError("완료봉 threshold의 profile과 interval이 필요합니다.")
        if not self.inputs_digest.strip():
            raise ThresholdProviderError("완료봉 threshold의 입력 digest가 필요합니다.")
        if self.close_ts_ms < 0:
            raise ThresholdProviderError("완료봉 시각은 음수일 수 없습니다.")
        if not self.threshold.is_finite() or not Decimal(0) < self.threshold < Decimal(1):
            raise ThresholdProviderError("Threshold는 0보다 크고 1보다 작아야 합니다.")


class CompletedCandleThresholdSource(Protocol):
    def latest_completed_threshold(
        self,
        *,
        venue: Venue,
        symbol: str,
        profile_id: str,
        as_of_ts_ms: int,
    ) -> CompletedCandleThreshold | None:
        """as-of 시점까지 확정된 가장 최근 완료봉 결정을 반환한다."""


@dataclass(frozen=True, slots=True)
class FixedThresholdProvider:
    profile_id: str
    threshold: Decimal
    provider_version: str = "FIXED_THRESHOLD_V1"

    def next_threshold(
        self,
        *,
        venue: Venue,
        symbol: str,
        profile_id: str,
        as_of_ts_ms: int,
    ) -> ThresholdDecision | None:
        del venue, symbol, as_of_ts_ms
        if profile_id != self.profile_id:
            raise ThresholdProviderError("요청한 threshold profile이 fixed provider와 다릅니다.")
        return ThresholdDecision(
            profile_id=profile_id,
            threshold=self.threshold,
            provider_version=self.provider_version,
            source_kind="FIXED",
        )


@dataclass(frozen=True, slots=True)
class CompletedCandleThresholdProvider:
    source: CompletedCandleThresholdSource
    provider_version: str

    def next_threshold(
        self,
        *,
        venue: Venue,
        symbol: str,
        profile_id: str,
        as_of_ts_ms: int,
    ) -> ThresholdDecision | None:
        completed = self.source.latest_completed_threshold(
            venue=venue,
            symbol=symbol,
            profile_id=profile_id,
            as_of_ts_ms=as_of_ts_ms,
        )
        if completed is None:
            return None
        if completed.profile_id != profile_id:
            raise ThresholdProviderError("완료봉 source가 다른 profile을 반환했습니다.")
        if completed.close_ts_ms > as_of_ts_ms:
            raise ThresholdProviderError("미래 완료봉을 threshold에 사용할 수 없습니다.")
        return ThresholdDecision(
            profile_id=profile_id,
            threshold=completed.threshold,
            provider_version=self.provider_version,
            source_kind="COMPLETED_CANDLE",
            source_interval=completed.source_interval,
            source_close_ts_ms=completed.close_ts_ms,
            inputs_digest=completed.inputs_digest,
        )


@dataclass(frozen=True, slots=True)
class DirectionalChangeEvent:
    event_id: str
    checksum: str
    run_id: str
    venue: Venue
    symbol: str
    profile_id: str
    dc_sequence: int
    continuity_epoch: int
    event_type: DCEventType
    source_event_id: str
    confirmation_ts_ms: int
    actual_confirmation_price: Decimal
    theoretical_confirmation_price: Decimal
    confirmation_slippage_bps: Decimal
    threshold: Decimal
    threshold_decision_id: str
    threshold_provider_version: str
    extreme_before_confirmation: Decimal
    extreme_ts_ms: int
    event_start_ts_ms: int
    event_duration_ms: int
    tick_count: int
    maximum_spread_bps: Decimal
    completed_overshoot: Decimal | None
    completed_overshoot_ratio: Decimal | None
    initialization: bool
    entry_eligible: bool


@dataclass(frozen=True, slots=True)
class DirectionalChangeReset:
    reset_id: str
    checksum: str
    run_id: str
    venue: Venue
    symbol: str
    profile_id: str
    continuity_epoch: int
    source_event_id: str
    ts_ms: int
    reason: DCUpdateReason


@dataclass(frozen=True, slots=True)
class DirectionalChangeSnapshot:
    state: DCState
    threshold: Decimal | None
    threshold_decision_id: str | None
    threshold_provider_version: str | None
    event_start_ts_ms: int | None
    event_start_price: Decimal | None
    running_high: Decimal | None
    running_high_ts_ms: int | None
    running_low: Decimal | None
    running_low_ts_ms: int | None
    last_confirmation_ts_ms: int | None
    last_confirmation_price: Decimal | None
    last_event_type: DCEventType | None
    overshoot_start_price: Decimal | None
    dc_sequence: int
    continuity_epoch: int
    valid_tick_count: int
    maximum_spread_bps: Decimal
    last_source_event_id: str | None
    last_sequence_end: int | None
    last_venue_ts_ms: int | None
    last_receive_monotonic_ns: int | None
    checksum: str


@dataclass(frozen=True, slots=True)
class DirectionalChangeUpdate:
    accepted: bool
    reason: DCUpdateReason | None
    snapshot: DirectionalChangeSnapshot
    event: DirectionalChangeEvent | None = None
    reset: DirectionalChangeReset | None = None


class DirectionalChangeEngine:
    """한 Run·거래소·종목·threshold profile의 DC 상태를 O(1)로 갱신한다."""

    def __init__(
        self,
        *,
        run_id: str,
        venue: Venue,
        symbol: str,
        profile_id: str,
        threshold_provider: ThresholdProvider,
        maximum_lag_ms: Decimal = Decimal(500),
        maximum_spread_bps: Decimal | None = None,
        dedupe_capacity: int = 4_096,
    ) -> None:
        if not run_id.strip() or not symbol.strip() or not profile_id.strip():
            raise DirectionalChangeInputError("run, symbol, threshold profile이 필요합니다.")
        if not maximum_lag_ms.is_finite() or maximum_lag_ms < 0:
            raise DirectionalChangeInputError("maximum lag는 유한한 음이 아닌 값이어야 합니다.")
        if maximum_spread_bps is not None and (
            not maximum_spread_bps.is_finite() or maximum_spread_bps <= 0
        ):
            raise DirectionalChangeInputError("maximum spread는 유한한 양수여야 합니다.")
        if dedupe_capacity < 1:
            raise DirectionalChangeInputError("dedupe capacity는 1 이상이어야 합니다.")
        self.run_id = run_id
        self.venue = venue
        self.symbol = symbol
        self.profile_id = profile_id
        self.threshold_provider = threshold_provider
        self.maximum_lag_ms = maximum_lag_ms
        self.maximum_spread_bps = maximum_spread_bps
        self._dedupe_capacity = dedupe_capacity
        self._seen_event_ids: set[str] = set()
        self._event_id_order: deque[str] = deque()

        self._state = DCState.UNINITIALIZED
        self._threshold: ThresholdDecision | None = None
        self._event_start_ts_ms: int | None = None
        self._event_start_price: Decimal | None = None
        self._running_high: Decimal | None = None
        self._running_high_ts_ms: int | None = None
        self._running_low: Decimal | None = None
        self._running_low_ts_ms: int | None = None
        self._last_confirmation_ts_ms: int | None = None
        self._last_confirmation_price: Decimal | None = None
        self._last_event_type: DCEventType | None = None
        self._overshoot_start_price: Decimal | None = None
        self._dc_sequence = 0
        self._continuity_epoch = 0
        self._valid_tick_count = 0
        self._maximum_spread_bps = Decimal(0)

        self._last_source_event_id: str | None = None
        self._last_sequence_end: int | None = None
        self._last_venue_ts_ms: int | None = None
        self._last_receive_monotonic_ns: int | None = None

    @property
    def snapshot(self) -> DirectionalChangeSnapshot:
        payload = self._snapshot_payload()
        return DirectionalChangeSnapshot(
            state=self._state,
            threshold=self._threshold.threshold if self._threshold is not None else None,
            threshold_decision_id=(
                self._threshold.decision_id if self._threshold is not None else None
            ),
            threshold_provider_version=(
                self._threshold.provider_version if self._threshold is not None else None
            ),
            event_start_ts_ms=self._event_start_ts_ms,
            event_start_price=self._event_start_price,
            running_high=self._running_high,
            running_high_ts_ms=self._running_high_ts_ms,
            running_low=self._running_low,
            running_low_ts_ms=self._running_low_ts_ms,
            last_confirmation_ts_ms=self._last_confirmation_ts_ms,
            last_confirmation_price=self._last_confirmation_price,
            last_event_type=self._last_event_type,
            overshoot_start_price=self._overshoot_start_price,
            dc_sequence=self._dc_sequence,
            continuity_epoch=self._continuity_epoch,
            valid_tick_count=self._valid_tick_count,
            maximum_spread_bps=self._maximum_spread_bps,
            last_source_event_id=self._last_source_event_id,
            last_sequence_end=self._last_sequence_end,
            last_venue_ts_ms=self._last_venue_ts_ms,
            last_receive_monotonic_ns=self._last_receive_monotonic_ns,
            checksum=_digest(payload),
        )

    def update(self, observation: DCMidObservation) -> DirectionalChangeUpdate:
        self._assert_identity(observation)
        if observation.event_id in self._seen_event_ids:
            return DirectionalChangeUpdate(
                accepted=False,
                reason=DCUpdateReason.DUPLICATE_EVENT,
                snapshot=self.snapshot,
            )
        self._remember_event_id(observation.event_id)

        ordering_fault = self._ordering_fault(observation)
        if ordering_fault is not None:
            if ordering_fault not in {
                DCUpdateReason.SEQUENCE_OUT_OF_ORDER,
                DCUpdateReason.VENUE_TS_OUT_OF_ORDER,
                DCUpdateReason.RECEIVE_TIME_OUT_OF_ORDER,
            }:
                self._advance_watermark(observation)
            return self._reset(observation, ordering_fault)

        self._advance_watermark(observation)
        quality_fault = self._quality_fault(observation)
        if quality_fault is not None:
            return self._reset(observation, quality_fault)

        if self._state is DCState.UNINITIALIZED and self._event_start_ts_ms is None:
            return self._seed_or_wait(observation)
        return self._advance_run(observation)

    def _seed_or_wait(self, observation: DCMidObservation) -> DirectionalChangeUpdate:
        if self._threshold is None:
            self._threshold = self._next_threshold(observation.venue_ts_ms)
        if self._threshold is None:
            return DirectionalChangeUpdate(
                accepted=False,
                reason=DCUpdateReason.THRESHOLD_UNAVAILABLE,
                snapshot=self.snapshot,
            )
        self._event_start_ts_ms = observation.venue_ts_ms
        self._event_start_price = observation.mid
        self._running_high = observation.mid
        self._running_high_ts_ms = observation.venue_ts_ms
        self._running_low = observation.mid
        self._running_low_ts_ms = observation.venue_ts_ms
        self._valid_tick_count = 1
        self._maximum_spread_bps = observation.spread_bps
        return DirectionalChangeUpdate(accepted=True, reason=None, snapshot=self.snapshot)

    def _advance_run(self, observation: DCMidObservation) -> DirectionalChangeUpdate:
        if self._threshold is None:
            raise RuntimeError("초기화된 DC 상태에 threshold가 없습니다.")
        self._valid_tick_count += 1
        self._maximum_spread_bps = max(self._maximum_spread_bps, observation.spread_bps)

        if self._state is DCState.UP_RUN:
            return self._advance_up_run(observation)
        if self._state is DCState.DOWN_RUN:
            return self._advance_down_run(observation)
        return self._advance_initial_run(observation)

    def _advance_initial_run(self, observation: DCMidObservation) -> DirectionalChangeUpdate:
        if self._threshold is None or self._event_start_price is None:
            raise RuntimeError("초기 DC 상태가 완전하지 않습니다.")
        if observation.mid > self._running_high_or_raise():
            self._running_high = observation.mid
            self._running_high_ts_ms = observation.venue_ts_ms
        if observation.mid < self._running_low_or_raise():
            self._running_low = observation.mid
            self._running_low_ts_ms = observation.venue_ts_ms

        up_threshold = self._event_start_price * (Decimal(1) + self._threshold.threshold)
        down_threshold = self._event_start_price * (Decimal(1) - self._threshold.threshold)
        if observation.mid >= up_threshold:
            return self._confirm(
                observation,
                event_type=DCEventType.UPTURN,
                theoretical_price=up_threshold,
                extreme=self._event_start_price,
                extreme_ts_ms=self._event_start_ts_or_raise(),
                initialization=True,
            )
        if observation.mid <= down_threshold:
            return self._confirm(
                observation,
                event_type=DCEventType.DOWNTURN,
                theoretical_price=down_threshold,
                extreme=self._event_start_price,
                extreme_ts_ms=self._event_start_ts_or_raise(),
                initialization=True,
            )
        return DirectionalChangeUpdate(accepted=True, reason=None, snapshot=self.snapshot)

    def _advance_up_run(self, observation: DCMidObservation) -> DirectionalChangeUpdate:
        if self._threshold is None:
            raise RuntimeError("UP_RUN 상태에 threshold가 없습니다.")
        if observation.mid > self._running_high_or_raise():
            self._running_high = observation.mid
            self._running_high_ts_ms = observation.venue_ts_ms
        high = self._running_high_or_raise()
        theoretical = high * (Decimal(1) - self._threshold.threshold)
        if observation.mid <= theoretical:
            return self._confirm(
                observation,
                event_type=DCEventType.DOWNTURN,
                theoretical_price=theoretical,
                extreme=high,
                extreme_ts_ms=self._running_high_ts_or_raise(),
                initialization=False,
            )
        return DirectionalChangeUpdate(accepted=True, reason=None, snapshot=self.snapshot)

    def _advance_down_run(self, observation: DCMidObservation) -> DirectionalChangeUpdate:
        if self._threshold is None:
            raise RuntimeError("DOWN_RUN 상태에 threshold가 없습니다.")
        if observation.mid < self._running_low_or_raise():
            self._running_low = observation.mid
            self._running_low_ts_ms = observation.venue_ts_ms
        low = self._running_low_or_raise()
        theoretical = low * (Decimal(1) + self._threshold.threshold)
        if observation.mid >= theoretical:
            return self._confirm(
                observation,
                event_type=DCEventType.UPTURN,
                theoretical_price=theoretical,
                extreme=low,
                extreme_ts_ms=self._running_low_ts_or_raise(),
                initialization=False,
            )
        return DirectionalChangeUpdate(accepted=True, reason=None, snapshot=self.snapshot)

    def _confirm(
        self,
        observation: DCMidObservation,
        *,
        event_type: DCEventType,
        theoretical_price: Decimal,
        extreme: Decimal,
        extreme_ts_ms: int,
        initialization: bool,
    ) -> DirectionalChangeUpdate:
        if self._threshold is None:
            raise RuntimeError("확인 시점에 threshold가 없습니다.")
        completed_overshoot, completed_ratio = self._completed_overshoot()
        frozen_threshold = self._threshold
        self._dc_sequence += 1
        event = self._build_event(
            observation,
            event_type=event_type,
            theoretical_price=theoretical_price,
            extreme=extreme,
            extreme_ts_ms=extreme_ts_ms,
            threshold=frozen_threshold,
            completed_overshoot=completed_overshoot,
            completed_ratio=completed_ratio,
            initialization=initialization,
        )

        next_threshold = self._next_threshold(observation.venue_ts_ms)
        if next_threshold is None:
            reset = self._clear_continuity(
                observation,
                DCUpdateReason.THRESHOLD_UNAVAILABLE,
            )
            return DirectionalChangeUpdate(
                accepted=True,
                reason=DCUpdateReason.THRESHOLD_UNAVAILABLE,
                snapshot=self.snapshot,
                event=event,
                reset=reset,
            )

        self._state = DCState.UP_RUN if event_type is DCEventType.UPTURN else DCState.DOWN_RUN
        self._threshold = next_threshold
        self._event_start_ts_ms = observation.venue_ts_ms
        self._event_start_price = observation.mid
        self._running_high = observation.mid
        self._running_high_ts_ms = observation.venue_ts_ms
        self._running_low = observation.mid
        self._running_low_ts_ms = observation.venue_ts_ms
        self._last_confirmation_ts_ms = observation.venue_ts_ms
        self._last_confirmation_price = observation.mid
        self._last_event_type = event_type
        self._overshoot_start_price = observation.mid
        self._valid_tick_count = 1
        self._maximum_spread_bps = observation.spread_bps
        return DirectionalChangeUpdate(
            accepted=True,
            reason=None,
            snapshot=self.snapshot,
            event=event,
        )

    def _build_event(
        self,
        observation: DCMidObservation,
        *,
        event_type: DCEventType,
        theoretical_price: Decimal,
        extreme: Decimal,
        extreme_ts_ms: int,
        threshold: ThresholdDecision,
        completed_overshoot: Decimal | None,
        completed_ratio: Decimal | None,
        initialization: bool,
    ) -> DirectionalChangeEvent:
        identity = {
            "continuity_epoch": self._continuity_epoch,
            "dc_sequence": self._dc_sequence,
            "event_type": event_type.value,
            "profile_id": self.profile_id,
            "run_id": self.run_id,
            "source_event_id": observation.event_id,
            "symbol": self.symbol,
            "venue": self.venue.value,
        }
        event_id = f"dc-{_digest(identity)[:32]}"
        if event_type is DCEventType.UPTURN:
            slippage = (observation.mid - theoretical_price) / theoretical_price * _BPS
        else:
            slippage = (theoretical_price - observation.mid) / theoretical_price * _BPS
        payload: dict[str, object] = {
            **identity,
            "actual_confirmation_price": _decimal_text(observation.mid),
            "completed_overshoot": (
                _decimal_text(completed_overshoot) if completed_overshoot is not None else None
            ),
            "completed_overshoot_ratio": (
                _decimal_text(completed_ratio) if completed_ratio is not None else None
            ),
            "confirmation_slippage_bps": _decimal_text(slippage),
            "confirmation_ts_ms": observation.venue_ts_ms,
            "entry_eligible": not initialization,
            "event_duration_ms": observation.venue_ts_ms - self._event_start_ts_or_raise(),
            "event_id": event_id,
            "event_start_ts_ms": self._event_start_ts_or_raise(),
            "extreme_before_confirmation": _decimal_text(extreme),
            "extreme_ts_ms": extreme_ts_ms,
            "initialization": initialization,
            "maximum_spread_bps": _decimal_text(self._maximum_spread_bps),
            "theoretical_confirmation_price": _decimal_text(theoretical_price),
            "threshold": _decimal_text(threshold.threshold),
            "threshold_decision_id": threshold.decision_id,
            "threshold_provider_version": threshold.provider_version,
            "tick_count": self._valid_tick_count,
        }
        return DirectionalChangeEvent(
            event_id=event_id,
            checksum=_digest(payload),
            run_id=self.run_id,
            venue=self.venue,
            symbol=self.symbol,
            profile_id=self.profile_id,
            dc_sequence=self._dc_sequence,
            continuity_epoch=self._continuity_epoch,
            event_type=event_type,
            source_event_id=observation.event_id,
            confirmation_ts_ms=observation.venue_ts_ms,
            actual_confirmation_price=observation.mid,
            theoretical_confirmation_price=theoretical_price,
            confirmation_slippage_bps=slippage,
            threshold=threshold.threshold,
            threshold_decision_id=threshold.decision_id,
            threshold_provider_version=threshold.provider_version,
            extreme_before_confirmation=extreme,
            extreme_ts_ms=extreme_ts_ms,
            event_start_ts_ms=self._event_start_ts_or_raise(),
            event_duration_ms=observation.venue_ts_ms - self._event_start_ts_or_raise(),
            tick_count=self._valid_tick_count,
            maximum_spread_bps=self._maximum_spread_bps,
            completed_overshoot=completed_overshoot,
            completed_overshoot_ratio=completed_ratio,
            initialization=initialization,
            entry_eligible=not initialization,
        )

    def _completed_overshoot(self) -> tuple[Decimal | None, Decimal | None]:
        if (
            self._last_confirmation_price is None
            or self._threshold is None
            or self._state is DCState.UNINITIALIZED
        ):
            return None, None
        if self._state is DCState.UP_RUN:
            overshoot = self._running_high_or_raise() - self._last_confirmation_price
        else:
            overshoot = self._last_confirmation_price - self._running_low_or_raise()
        denominator = self._last_confirmation_price * self._threshold.threshold
        return overshoot, overshoot / denominator

    def _next_threshold(self, as_of_ts_ms: int) -> ThresholdDecision | None:
        decision = self.threshold_provider.next_threshold(
            venue=self.venue,
            symbol=self.symbol,
            profile_id=self.profile_id,
            as_of_ts_ms=as_of_ts_ms,
        )
        if decision is not None and decision.profile_id != self.profile_id:
            raise ThresholdProviderError("Provider가 다른 threshold profile을 반환했습니다.")
        if decision is not None and (
            decision.source_close_ts_ms is not None
            and decision.source_close_ts_ms > as_of_ts_ms
        ):
            raise ThresholdProviderError("미래 완료봉을 threshold에 사용할 수 없습니다.")
        return decision

    def _ordering_fault(self, observation: DCMidObservation) -> DCUpdateReason | None:
        if (
            observation.sequence_start is not None
            and observation.sequence_end is not None
            and observation.sequence_end < observation.sequence_start
        ):
            return DCUpdateReason.SEQUENCE_RANGE_INVALID
        if self._last_sequence_end is not None and observation.sequence_end is not None:
            if observation.sequence_end <= self._last_sequence_end:
                return DCUpdateReason.SEQUENCE_OUT_OF_ORDER
            if (
                observation.sequence_start is not None
                and observation.sequence_start > self._last_sequence_end + 1
            ):
                return DCUpdateReason.SEQUENCE_GAP
            if (
                observation.previous_sequence_end is not None
                and observation.previous_sequence_end != self._last_sequence_end
            ):
                return DCUpdateReason.SEQUENCE_GAP
        if (
            self._last_venue_ts_ms is not None
            and observation.venue_ts_ms < self._last_venue_ts_ms
        ):
            return DCUpdateReason.VENUE_TS_OUT_OF_ORDER
        if (
            self._last_receive_monotonic_ns is not None
            and observation.receive_monotonic_ns < self._last_receive_monotonic_ns
        ):
            return DCUpdateReason.RECEIVE_TIME_OUT_OF_ORDER
        return None

    def _quality_fault(self, observation: DCMidObservation) -> DCUpdateReason | None:
        if not observation.sequence_valid:
            return DCUpdateReason.SEQUENCE_INVALID
        if observation.stale:
            return DCUpdateReason.STALE
        if observation.lag_ms is None:
            return DCUpdateReason.LAG_UNKNOWN
        if observation.lag_ms > self.maximum_lag_ms:
            return DCUpdateReason.LAG_EXCEEDED
        if (
            self.maximum_spread_bps is not None
            and observation.spread_bps > self.maximum_spread_bps
        ):
            return DCUpdateReason.SPREAD_EXCEEDED
        return None

    def _reset(
        self,
        observation: DCMidObservation,
        reason: DCUpdateReason,
    ) -> DirectionalChangeUpdate:
        reset = self._clear_continuity(observation, reason)
        return DirectionalChangeUpdate(
            accepted=False,
            reason=reason,
            snapshot=self.snapshot,
            reset=reset,
        )

    def _clear_continuity(
        self,
        observation: DCMidObservation,
        reason: DCUpdateReason,
    ) -> DirectionalChangeReset:
        self._continuity_epoch += 1
        self._state = DCState.UNINITIALIZED
        self._threshold = None
        self._event_start_ts_ms = None
        self._event_start_price = None
        self._running_high = None
        self._running_high_ts_ms = None
        self._running_low = None
        self._running_low_ts_ms = None
        self._last_confirmation_ts_ms = None
        self._last_confirmation_price = None
        self._last_event_type = None
        self._overshoot_start_price = None
        self._valid_tick_count = 0
        self._maximum_spread_bps = Decimal(0)
        identity = {
            "continuity_epoch": self._continuity_epoch,
            "profile_id": self.profile_id,
            "reason": reason.value,
            "run_id": self.run_id,
            "source_event_id": observation.event_id,
            "symbol": self.symbol,
            "venue": self.venue.value,
        }
        reset_id = f"dc-reset-{_digest(identity)[:32]}"
        payload: dict[str, object] = {
            **identity,
            "reset_id": reset_id,
            "ts_ms": observation.venue_ts_ms,
        }
        return DirectionalChangeReset(
            reset_id=reset_id,
            checksum=_digest(payload),
            run_id=self.run_id,
            venue=self.venue,
            symbol=self.symbol,
            profile_id=self.profile_id,
            continuity_epoch=self._continuity_epoch,
            source_event_id=observation.event_id,
            ts_ms=observation.venue_ts_ms,
            reason=reason,
        )

    def _assert_identity(self, observation: DCMidObservation) -> None:
        if (
            observation.run_id != self.run_id
            or observation.venue is not self.venue
            or observation.symbol != self.symbol
        ):
            raise DirectionalChangeInputError(
                "Directional Change 엔진에서 run, 거래소 또는 종목을 섞을 수 없습니다."
            )

    def _remember_event_id(self, event_id: str) -> None:
        self._seen_event_ids.add(event_id)
        self._event_id_order.append(event_id)
        if len(self._event_id_order) > self._dedupe_capacity:
            self._seen_event_ids.discard(self._event_id_order.popleft())

    def _advance_watermark(self, observation: DCMidObservation) -> None:
        self._last_source_event_id = observation.event_id
        if observation.sequence_end is not None:
            self._last_sequence_end = observation.sequence_end
        self._last_venue_ts_ms = observation.venue_ts_ms
        self._last_receive_monotonic_ns = observation.receive_monotonic_ns

    def _snapshot_payload(self) -> dict[str, object]:
        return {
            "continuity_epoch": self._continuity_epoch,
            "dc_sequence": self._dc_sequence,
            "event_start_price": (
                _decimal_text(self._event_start_price)
                if self._event_start_price is not None
                else None
            ),
            "event_start_ts_ms": self._event_start_ts_ms,
            "last_confirmation_price": (
                _decimal_text(self._last_confirmation_price)
                if self._last_confirmation_price is not None
                else None
            ),
            "last_confirmation_ts_ms": self._last_confirmation_ts_ms,
            "last_event_type": self._last_event_type.value if self._last_event_type else None,
            "last_receive_monotonic_ns": self._last_receive_monotonic_ns,
            "last_sequence_end": self._last_sequence_end,
            "last_source_event_id": self._last_source_event_id,
            "last_venue_ts_ms": self._last_venue_ts_ms,
            "maximum_spread_bps": _decimal_text(self._maximum_spread_bps),
            "overshoot_start_price": (
                _decimal_text(self._overshoot_start_price)
                if self._overshoot_start_price is not None
                else None
            ),
            "profile_id": self.profile_id,
            "run_id": self.run_id,
            "running_high": (
                _decimal_text(self._running_high) if self._running_high is not None else None
            ),
            "running_high_ts_ms": self._running_high_ts_ms,
            "running_low": (
                _decimal_text(self._running_low) if self._running_low is not None else None
            ),
            "running_low_ts_ms": self._running_low_ts_ms,
            "state": self._state.value,
            "symbol": self.symbol,
            "threshold": (
                _decimal_text(self._threshold.threshold) if self._threshold is not None else None
            ),
            "threshold_decision_id": (
                self._threshold.decision_id if self._threshold is not None else None
            ),
            "threshold_provider_version": (
                self._threshold.provider_version if self._threshold is not None else None
            ),
            "valid_tick_count": self._valid_tick_count,
            "venue": self.venue.value,
        }

    def _running_high_or_raise(self) -> Decimal:
        if self._running_high is None:
            raise RuntimeError("running high가 초기화되지 않았습니다.")
        return self._running_high

    def _running_low_or_raise(self) -> Decimal:
        if self._running_low is None:
            raise RuntimeError("running low가 초기화되지 않았습니다.")
        return self._running_low

    def _running_high_ts_or_raise(self) -> int:
        if self._running_high_ts_ms is None:
            raise RuntimeError("running high 시각이 초기화되지 않았습니다.")
        return self._running_high_ts_ms

    def _running_low_ts_or_raise(self) -> int:
        if self._running_low_ts_ms is None:
            raise RuntimeError("running low 시각이 초기화되지 않았습니다.")
        return self._running_low_ts_ms

    def _event_start_ts_or_raise(self) -> int:
        if self._event_start_ts_ms is None:
            raise RuntimeError("event 시작 시각이 초기화되지 않았습니다.")
        return self._event_start_ts_ms


__all__ = [
    "CompletedCandleThreshold",
    "CompletedCandleThresholdProvider",
    "CompletedCandleThresholdSource",
    "DCEventType",
    "DCMidObservation",
    "DCState",
    "DCUpdateReason",
    "DirectionalChangeEngine",
    "DirectionalChangeEvent",
    "DirectionalChangeInputError",
    "DirectionalChangeReset",
    "DirectionalChangeSnapshot",
    "DirectionalChangeUpdate",
    "FixedThresholdProvider",
    "ThresholdDecision",
    "ThresholdProvider",
    "ThresholdProviderError",
]
