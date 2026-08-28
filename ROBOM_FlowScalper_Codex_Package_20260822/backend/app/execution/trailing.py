"""실행가능 bid·ask만으로 부분익절 러너와 단조 trailing 상태를 관리한다."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

from backend.app.domain.models import Side
from backend.app.market_data import Candle


class TrailingState(StrEnum):
    ENTRY_PENDING = "ENTRY_PENDING"
    INITIAL_PROTECTION = "INITIAL_PROTECTION"
    PROFIT_ACTIVATION_PENDING = "PROFIT_ACTIVATION_PENDING"
    TRAIL_ARMED = "TRAIL_ARMED"
    PARTIAL_TP_PENDING = "PARTIAL_TP_PENDING"
    RUNNER_ACTIVE = "RUNNER_ACTIVE"
    TRAIL_EXIT_PENDING = "TRAIL_EXIT_PENDING"
    CLOSED = "CLOSED"


class TrailingModel(StrEnum):
    FIXED_DISTANCE = "FIXED_DISTANCE"
    FIXED_RATE = "FIXED_RATE"
    ATR_CHANDELIER = "ATR_CHANDELIER"
    CHANDELIER_STRUCTURE = "CHANDELIER_STRUCTURE"
    STRUCTURE = "STRUCTURE"
    EDGE_ADAPTIVE = "EDGE_ADAPTIVE"


class TrailingActivationRule(StrEnum):
    R_MULTIPLE = "R_MULTIPLE"
    TP1_TRIGGERED = "TP1_TRIGGERED"


class TrailingActivationNotFeeSafeError(ValueError):
    """실제 체결 뒤 trailing 활성화가 비용 본전을 넘지 못했음을 구분한다."""


@dataclass(frozen=True, slots=True)
class TrailingReference:
    symbol: str
    interval_seconds: int
    reference_ts_ms: int
    atr: Decimal
    completed_structure_stop: Decimal
    source_bar_count: int


def trailing_reference_from_completed_candles(
    candles: Sequence[Candle],
    *,
    side: Side,
    as_of_ts_ms: int,
    atr_period: int = 14,
    structure_lookback: int = 3,
) -> TrailingReference:
    """as-of 이전에 끝난 동일 시간봉만으로 ATR과 구조 stop을 고정한다."""

    if atr_period < 2 or structure_lookback <= 0 or as_of_ts_ms < 0:
        raise ValueError("trailing ATR 기간·구조 lookback·as-of 시각이 잘못됐습니다.")
    required_bar_count = max(atr_period + 1, structure_lookback)
    if len(candles) < required_bar_count:
        raise ValueError("trailing ATR 계산에 필요한 완성봉이 부족합니다.")
    ordered = tuple(sorted(candles, key=lambda candle: candle.open_ts_ms))
    symbol = ordered[0].symbol
    interval_seconds = ordered[0].interval_seconds
    if any(
        candle.symbol != symbol or candle.interval_seconds != interval_seconds for candle in ordered
    ):
        raise ValueError("trailing 참조봉은 같은 종목과 시간구간이어야 합니다.")
    if len({candle.open_ts_ms for candle in ordered}) != len(ordered):
        raise ValueError("trailing 참조봉 open 시각이 중복됐습니다.")
    interval_ms = interval_seconds * 1_000
    if any(candle.open_ts_ms < 0 or candle.open_ts_ms % interval_ms for candle in ordered):
        raise ValueError("trailing 참조봉 open 시각이 시간구간 경계와 맞지 않습니다.")
    if any(
        current.open_ts_ms - previous.open_ts_ms != interval_ms
        for previous, current in zip(ordered[:-1], ordered[1:], strict=True)
    ):
        raise ValueError("trailing 참조봉 사이에 누락된 시간구간이 있습니다.")
    if any(
        candle.open <= 0
        or candle.high <= 0
        or candle.low <= 0
        or candle.close <= 0
        or candle.high < max(candle.open, candle.close)
        or candle.low > min(candle.open, candle.close)
        or candle.high < candle.low
        or candle.volume < 0
        or candle.trade_count < 0
        for candle in ordered
    ):
        raise ValueError("trailing 참조봉 OHLCV가 잘못됐습니다.")
    if any(candle.open_ts_ms + interval_ms > as_of_ts_ms for candle in ordered):
        raise ValueError("완료되지 않은 미래봉은 trailing 참조에 사용할 수 없습니다.")
    selected = ordered[-(atr_period + 1) :]
    true_ranges = tuple(
        max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        for previous, current in zip(selected[:-1], selected[1:], strict=True)
    )
    atr = sum(true_ranges, start=Decimal(0)) / Decimal(len(true_ranges))
    if atr <= 0:
        raise ValueError("trailing ATR은 양수여야 합니다.")
    structure_rows = ordered[-structure_lookback:]
    structure_stop = (
        min(candle.low for candle in structure_rows)
        if side is Side.LONG
        else max(candle.high for candle in structure_rows)
    )
    last = ordered[-1]
    return TrailingReference(
        symbol=symbol,
        interval_seconds=interval_seconds,
        reference_ts_ms=last.open_ts_ms + interval_ms,
        atr=atr,
        completed_structure_stop=structure_stop,
        source_bar_count=len(ordered),
    )


_ALLOWED_TRANSITIONS = {
    TrailingState.ENTRY_PENDING: {TrailingState.INITIAL_PROTECTION, TrailingState.CLOSED},
    TrailingState.INITIAL_PROTECTION: {
        TrailingState.PROFIT_ACTIVATION_PENDING,
        TrailingState.CLOSED,
    },
    TrailingState.PROFIT_ACTIVATION_PENDING: {
        TrailingState.TRAIL_ARMED,
        TrailingState.CLOSED,
    },
    TrailingState.TRAIL_ARMED: {
        TrailingState.PARTIAL_TP_PENDING,
        TrailingState.RUNNER_ACTIVE,
        TrailingState.CLOSED,
    },
    TrailingState.PARTIAL_TP_PENDING: {
        TrailingState.TRAIL_ARMED,
        TrailingState.RUNNER_ACTIVE,
        TrailingState.CLOSED,
    },
    TrailingState.RUNNER_ACTIVE: {
        TrailingState.TRAIL_EXIT_PENDING,
        TrailingState.CLOSED,
    },
    TrailingState.TRAIL_EXIT_PENDING: {
        TrailingState.RUNNER_ACTIVE,
        TrailingState.CLOSED,
    },
    TrailingState.CLOSED: set(),
}

_PROCESSED_EVENT_WINDOW = 256
_VALID_DATA_HEALTH = {"HEALTHY", "DEGRADED"}


@dataclass(frozen=True, slots=True)
class TrailingPolicy:
    policy_id: str
    model: TrailingModel
    activation_rule: TrailingActivationRule
    activation_r: Decimal
    partial_tp_required: bool
    fixed_distance: Decimal | None = None
    retracement_rate: Decimal | None = None
    atr_multiplier: Decimal | None = None
    adverse_atr_multiplier: Decimal | None = None
    adverse_signal_count: int = 2
    adverse_persistence_ms: int = 3_000
    activation_price_override: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("trailing policy ID가 필요합니다.")
        if self.activation_r <= 0:
            raise ValueError("trailing activation R은 양수여야 합니다.")
        if self.activation_price_override is not None and self.activation_price_override <= 0:
            raise ValueError("trailing activation price override는 양수여야 합니다.")
        if self.model is TrailingModel.FIXED_DISTANCE and (
            self.fixed_distance is None or self.fixed_distance <= 0
        ):
            raise ValueError("고정거리 trailing에는 양수 distance가 필요합니다.")
        if self.model is TrailingModel.FIXED_RATE and (
            self.retracement_rate is None or not Decimal(0) < self.retracement_rate < Decimal(1)
        ):
            raise ValueError("고정비율 trailing rate는 0과 1 사이여야 합니다.")
        if self.model in {
            TrailingModel.ATR_CHANDELIER,
            TrailingModel.CHANDELIER_STRUCTURE,
            TrailingModel.EDGE_ADAPTIVE,
        } and (self.atr_multiplier is None or self.atr_multiplier <= 0):
            raise ValueError("ATR trailing에는 양수 multiplier가 필요합니다.")
        if self.model is TrailingModel.EDGE_ADAPTIVE:
            if (
                self.atr_multiplier is None
                or self.adverse_atr_multiplier is None
                or self.adverse_atr_multiplier <= 0
                or self.adverse_atr_multiplier > self.atr_multiplier
                or self.adverse_signal_count < 2
                or self.adverse_persistence_ms <= 0
            ):
                raise ValueError("적응형 adverse 설정과 persistence가 올바르지 않습니다.")
        if (
            self.activation_rule is TrailingActivationRule.TP1_TRIGGERED
            and not self.partial_tp_required
        ):
            raise ValueError("TP1 활성화 trailing에는 부분익절 계약이 필요합니다.")


@dataclass(frozen=True, slots=True)
class TrailingObservation:
    event_id: str
    event_time_ms: int
    receive_time_ms: int
    best_bid: Decimal
    best_ask: Decimal
    sequence_valid: bool
    stale: bool
    data_health: str
    remaining_quantity: Decimal
    realized_quantity: Decimal
    current_unrealized: Decimal
    atr: Decimal | None = None
    completed_structure_stop: Decimal | None = None
    adverse_edge: bool = False

    def validate(self) -> None:
        if not self.event_id:
            raise ValueError("trailing 관찰 event ID가 필요합니다.")
        if self.event_time_ms < 0 or self.receive_time_ms < self.event_time_ms:
            raise ValueError("event·receive 시각 순서가 잘못됐습니다.")
        if self.best_bid <= 0 or self.best_ask <= self.best_bid:
            raise ValueError("비교차 executable bid·ask가 필요합니다.")
        if self.remaining_quantity < 0 or self.realized_quantity < 0:
            raise ValueError("trailing 수량은 음수일 수 없습니다.")
        if self.atr is not None and self.atr <= 0:
            raise ValueError("ATR은 양수여야 합니다.")
        if self.completed_structure_stop is not None and self.completed_structure_stop <= 0:
            raise ValueError("완료봉 구조 stop은 양수여야 합니다.")
        if self.data_health not in _VALID_DATA_HEALTH:
            raise ValueError("trailing 데이터 건강상태가 잘못됐습니다.")


@dataclass(frozen=True, slots=True)
class TrailingDecision:
    ignored: bool
    ignore_reason: str | None
    trail_exit_triggered: bool
    current_trail: Decimal | None
    state: TrailingState


@dataclass(frozen=True, slots=True)
class TrailingTransition:
    transition_id: str
    from_state: TrailingState
    to_state: TrailingState
    account_id: str
    trade_id: str
    strategy_id: str
    strategy_version: str
    profile: str
    symbol: str
    side: Side
    event_time_ms: int
    receive_time_ms: int
    activation_rule: str
    activation_price: Decimal
    activation_ts_ms: int | None
    highest_favorable_bid: Decimal | None
    lowest_favorable_ask: Decimal | None
    current_trail: Decimal | None
    previous_trail: Decimal | None
    initial_stop: Decimal
    fee_adjusted_breakeven: Decimal
    original_quantity: Decimal
    realized_quantity: Decimal
    runner_quantity: Decimal
    mfe_r: Decimal
    mae_r: Decimal
    peak_unrealized: Decimal
    current_unrealized: Decimal
    giveback: Decimal
    transition_actor: str
    reason_codes: tuple[str, ...]
    data_health: str


@dataclass(slots=True)
class TrailingStateMachine:
    account_id: str
    trade_id: str
    strategy_id: str
    strategy_version: str
    profile: str
    symbol: str
    side: Side
    entry_price: Decimal
    initial_stop: Decimal
    fee_adjusted_breakeven: Decimal
    original_quantity: Decimal
    policy: TrailingPolicy
    state: TrailingState = TrailingState.ENTRY_PENDING
    activation_ts_ms: int | None = None
    highest_favorable_bid: Decimal | None = None
    lowest_favorable_ask: Decimal | None = None
    current_trail: Decimal | None = None
    previous_trail: Decimal | None = None
    realized_quantity: Decimal = Decimal(0)
    runner_quantity: Decimal = Decimal(0)
    mfe_r: Decimal = Decimal(0)
    mae_r: Decimal = Decimal(0)
    peak_unrealized: Decimal = Decimal(0)
    current_unrealized: Decimal = Decimal(0)
    giveback: Decimal = Decimal(0)
    last_event_id: str | None = None
    last_event_time_ms: int | None = None
    processed_event_ids: list[str] = field(default_factory=list)
    transitions: list[TrailingTransition] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not all(
            (
                self.account_id,
                self.trade_id,
                self.strategy_id,
                self.strategy_version,
                self.profile,
                self.symbol,
            )
        ):
            raise ValueError("trailing 상태에는 계좌·거래·전략·종목 식별자가 필요합니다.")
        if self.entry_price <= 0 or self.initial_stop <= 0:
            raise ValueError("trailing entry와 initial stop은 양수여야 합니다.")
        if self.original_quantity <= 0:
            raise ValueError("trailing original quantity는 양수여야 합니다.")
        if self.side is Side.LONG and self.initial_stop >= self.entry_price:
            raise ValueError("롱 initial stop은 entry 아래여야 합니다.")
        if self.side is Side.SHORT and self.initial_stop <= self.entry_price:
            raise ValueError("숏 initial stop은 entry 위여야 합니다.")
        if self.side is Side.LONG and self.fee_adjusted_breakeven < self.entry_price:
            raise ValueError("롱 fee-adjusted breakeven은 entry 이상이어야 합니다.")
        if self.side is Side.SHORT and self.fee_adjusted_breakeven > self.entry_price:
            raise ValueError("숏 fee-adjusted breakeven은 entry 이하여야 합니다.")
        if self.side is Side.LONG and self.activation_price <= self.fee_adjusted_breakeven:
            raise TrailingActivationNotFeeSafeError(
                "롱 trailing 활성화 가격은 비용 반영 본전 위여야 합니다."
            )
        if self.side is Side.SHORT and self.activation_price >= self.fee_adjusted_breakeven:
            raise TrailingActivationNotFeeSafeError(
                "숏 trailing 활성화 가격은 비용 반영 본전 아래여야 합니다."
            )
        if len(self.processed_event_ids) > _PROCESSED_EVENT_WINDOW:
            raise ValueError("trailing 중복 방지 event window를 초과했습니다.")
        if len(set(self.processed_event_ids)) != len(self.processed_event_ids):
            raise ValueError("trailing 중복 방지 event ID는 고유해야 합니다.")

    @property
    def activation_price(self) -> Decimal:
        if self.policy.activation_price_override is not None:
            return self.policy.activation_price_override
        distance = abs(self.entry_price - self.initial_stop) * self.policy.activation_r
        return (
            self.entry_price + distance if self.side is Side.LONG else self.entry_price - distance
        )

    @property
    def initial_risk(self) -> Decimal:
        return abs(self.entry_price - self.initial_stop)

    @property
    def runner_started_ts_ms(self) -> int | None:
        return next(
            (
                transition.event_time_ms
                for transition in self.transitions
                if transition.to_state is TrailingState.RUNNER_ACTIVE
            ),
            None,
        )

    def confirm_entry(self, *, event_time_ms: int, receive_time_ms: int) -> None:
        self.runner_quantity = self.original_quantity
        self._transition(
            TrailingState.INITIAL_PROTECTION,
            event_time_ms=event_time_ms,
            receive_time_ms=receive_time_ms,
            actor="PAPER_EXECUTION",
            reason_codes=("ENTRY_FILLED_INITIAL_STOP_CONFIRMED",),
            data_health="HEALTHY",
        )
        self._transition(
            TrailingState.PROFIT_ACTIVATION_PENDING,
            event_time_ms=event_time_ms,
            receive_time_ms=receive_time_ms,
            actor="POSITION_MANAGER",
            reason_codes=("INITIAL_PROTECTION_ACTIVE",),
            data_health="HEALTHY",
        )

    def observe(self, observation: TrailingObservation) -> TrailingDecision:
        observation.validate()
        ignore_reason = self._ignore_reason(observation)
        if ignore_reason is not None:
            return TrailingDecision(
                True,
                ignore_reason,
                False,
                self.current_trail,
                self.state,
            )
        self.last_event_id = observation.event_id
        self.last_event_time_ms = observation.event_time_ms
        self.processed_event_ids.append(observation.event_id)
        if len(self.processed_event_ids) > _PROCESSED_EVENT_WINDOW:
            del self.processed_event_ids[:-_PROCESSED_EVENT_WINDOW]
        self.realized_quantity = observation.realized_quantity
        self.runner_quantity = observation.remaining_quantity
        executable_price = observation.best_bid if self.side is Side.LONG else observation.best_ask
        current_r = (
            (executable_price - self.entry_price) / self.initial_risk
            if self.side is Side.LONG
            else (self.entry_price - executable_price) / self.initial_risk
        )
        self.mfe_r = max(self.mfe_r, current_r)
        self.mae_r = min(self.mae_r, current_r)
        self.current_unrealized = observation.current_unrealized
        self.peak_unrealized = max(self.peak_unrealized, observation.current_unrealized)
        self.giveback = max(Decimal(0), self.peak_unrealized - self.current_unrealized)
        if self.side is Side.LONG:
            self.highest_favorable_bid = max(
                self.highest_favorable_bid or observation.best_bid,
                observation.best_bid,
            )
        else:
            self.lowest_favorable_ask = min(
                self.lowest_favorable_ask or observation.best_ask,
                observation.best_ask,
            )

        if self.state is TrailingState.PROFIT_ACTIVATION_PENDING and self._activation_hit(
            executable_price
        ):
            self.activation_ts_ms = observation.event_time_ms
            self._transition(
                TrailingState.TRAIL_ARMED,
                event_time_ms=observation.event_time_ms,
                receive_time_ms=observation.receive_time_ms,
                actor="POSITION_MANAGER",
                reason_codes=("PROFIT_ACTIVATION_REACHED",),
                data_health=observation.data_health,
            )
            self._update_trail(observation)
            if not self.policy.partial_tp_required:
                self._transition(
                    TrailingState.RUNNER_ACTIVE,
                    event_time_ms=observation.event_time_ms,
                    receive_time_ms=observation.receive_time_ms,
                    actor="POSITION_MANAGER",
                    reason_codes=("FULL_POSITION_RUNNER_ACTIVE",),
                    data_health=observation.data_health,
                )

        if self.state is TrailingState.RUNNER_ACTIVE:
            self._update_trail(observation)
            if self._trail_hit(executable_price):
                self._transition(
                    TrailingState.TRAIL_EXIT_PENDING,
                    event_time_ms=observation.event_time_ms,
                    receive_time_ms=observation.receive_time_ms,
                    actor="POSITION_MANAGER",
                    reason_codes=("EXECUTABLE_PRICE_CROSSED_MONOTONIC_TRAIL",),
                    data_health=observation.data_health,
                )
                return TrailingDecision(
                    False,
                    None,
                    True,
                    self.current_trail,
                    self.state,
                )
        return TrailingDecision(False, None, False, self.current_trail, self.state)

    def mark_partial_tp_pending(
        self,
        *,
        event_time_ms: int,
        receive_time_ms: int,
        data_health: str,
    ) -> None:
        if (
            self.state is TrailingState.PROFIT_ACTIVATION_PENDING
            and self.policy.activation_rule is TrailingActivationRule.TP1_TRIGGERED
        ):
            self.activation_ts_ms = event_time_ms
            self._transition(
                TrailingState.TRAIL_ARMED,
                event_time_ms=event_time_ms,
                receive_time_ms=receive_time_ms,
                actor="POSITION_MANAGER",
                reason_codes=("TP1_TRIGGER_ARMED_TRAIL",),
                data_health=data_health,
            )
        if self.state is not TrailingState.TRAIL_ARMED:
            return
        self._transition(
            TrailingState.PARTIAL_TP_PENDING,
            event_time_ms=event_time_ms,
            receive_time_ms=receive_time_ms,
            actor="POSITION_MANAGER",
            reason_codes=("PARTIAL_TP_TRIGGERED",),
            data_health=data_health,
        )

    def mark_partial_tp_filled(
        self,
        *,
        event_time_ms: int,
        receive_time_ms: int,
        realized_quantity: Decimal,
        remaining_quantity: Decimal,
        target_complete: bool,
        data_health: str,
    ) -> None:
        if self.state is not TrailingState.PARTIAL_TP_PENDING:
            return
        if realized_quantity <= 0 or remaining_quantity <= 0:
            raise ValueError("부분익절 뒤 realized와 runner 수량은 양수여야 합니다.")
        if realized_quantity + remaining_quantity > self.original_quantity:
            raise ValueError("부분익절 수량 합이 original quantity를 넘습니다.")
        self.realized_quantity = realized_quantity
        self.runner_quantity = remaining_quantity
        if not target_complete:
            return
        self._transition(
            TrailingState.RUNNER_ACTIVE,
            event_time_ms=event_time_ms,
            receive_time_ms=receive_time_ms,
            actor="PAPER_EXECUTION",
            reason_codes=("PARTIAL_TP_FILLED_RUNNER_REMAINS",),
            data_health=data_health,
        )

    def mark_partial_tp_rejected(
        self,
        *,
        event_time_ms: int,
        receive_time_ms: int,
        data_health: str,
    ) -> None:
        if self.state is not TrailingState.PARTIAL_TP_PENDING:
            return
        self._transition(
            TrailingState.TRAIL_ARMED,
            event_time_ms=event_time_ms,
            receive_time_ms=receive_time_ms,
            actor="PAPER_EXECUTION",
            reason_codes=("PARTIAL_TP_NOT_FILLED_PROTECTION_CONTINUES",),
            data_health=data_health,
        )

    def mark_exit_rejected(
        self,
        *,
        event_time_ms: int,
        receive_time_ms: int,
        data_health: str,
    ) -> None:
        if self.state is not TrailingState.TRAIL_EXIT_PENDING:
            return
        self._transition(
            TrailingState.RUNNER_ACTIVE,
            event_time_ms=event_time_ms,
            receive_time_ms=receive_time_ms,
            actor="PAPER_EXECUTION",
            reason_codes=("TRAIL_EXIT_REJECTED_REMAINING_QUANTITY_PROTECTED",),
            data_health=data_health,
        )

    def mark_closed(
        self,
        *,
        event_time_ms: int,
        receive_time_ms: int,
        actor: str,
        reason_codes: tuple[str, ...],
        data_health: str,
    ) -> None:
        if self.state is TrailingState.CLOSED:
            return
        self.runner_quantity = Decimal(0)
        self.realized_quantity = self.original_quantity
        self._transition(
            TrailingState.CLOSED,
            event_time_ms=event_time_ms,
            receive_time_ms=receive_time_ms,
            actor=actor,
            reason_codes=reason_codes,
            data_health=data_health,
        )

    def _ignore_reason(self, observation: TrailingObservation) -> str | None:
        if self.state is TrailingState.CLOSED:
            return "POSITION_ALREADY_CLOSED"
        if observation.event_id in self.processed_event_ids:
            return "DUPLICATE_EVENT"
        if (
            self.last_event_time_ms is not None
            and observation.event_time_ms < self.last_event_time_ms
        ):
            return "OUT_OF_ORDER_EVENT"
        if observation.stale:
            return "STALE_EVENT"
        if not observation.sequence_valid:
            return "SEQUENCE_INVALID_BOOK"
        if observation.data_health != "HEALTHY":
            return "DATA_HEALTH_NOT_EXECUTABLE"
        return None

    def _activation_hit(self, executable_price: Decimal) -> bool:
        if self.policy.activation_rule is not TrailingActivationRule.R_MULTIPLE:
            return False
        return (
            executable_price >= self.activation_price
            if self.side is Side.LONG
            else executable_price <= self.activation_price
        )

    def _update_trail(self, observation: TrailingObservation) -> None:
        candidate = self._trail_candidate(observation)
        if candidate is None:
            return
        floor_or_ceiling = (
            max(self.initial_stop, self.fee_adjusted_breakeven)
            if self.side is Side.LONG
            else min(self.initial_stop, self.fee_adjusted_breakeven)
        )
        candidate = (
            max(candidate, floor_or_ceiling)
            if self.side is Side.LONG
            else min(candidate, floor_or_ceiling)
        )
        previous = self.current_trail
        if previous is None:
            updated = candidate
        elif self.side is Side.LONG:
            updated = max(previous, candidate)
        else:
            updated = min(previous, candidate)
        if updated != self.current_trail:
            self.previous_trail = self.current_trail
            self.current_trail = updated

    def _trail_candidate(self, observation: TrailingObservation) -> Decimal | None:
        favorable = (
            self.highest_favorable_bid if self.side is Side.LONG else self.lowest_favorable_ask
        )
        if favorable is None:
            return None
        if self.policy.model is TrailingModel.FIXED_DISTANCE:
            assert self.policy.fixed_distance is not None
            offset = self.policy.fixed_distance
        elif self.policy.model is TrailingModel.FIXED_RATE:
            assert self.policy.retracement_rate is not None
            return (
                favorable * (Decimal(1) - self.policy.retracement_rate)
                if self.side is Side.LONG
                else favorable * (Decimal(1) + self.policy.retracement_rate)
            )
        elif self.policy.model in {
            TrailingModel.ATR_CHANDELIER,
            TrailingModel.CHANDELIER_STRUCTURE,
            TrailingModel.EDGE_ADAPTIVE,
        }:
            if observation.atr is None:
                return None
            multiplier = self.policy.atr_multiplier
            if self.policy.model is TrailingModel.EDGE_ADAPTIVE and observation.adverse_edge:
                multiplier = self.policy.adverse_atr_multiplier
            assert multiplier is not None
            offset = observation.atr * multiplier
            atr_candidate = favorable - offset if self.side is Side.LONG else favorable + offset
            if (
                self.policy.model is TrailingModel.CHANDELIER_STRUCTURE
                and observation.completed_structure_stop is not None
            ):
                return (
                    max(atr_candidate, observation.completed_structure_stop)
                    if self.side is Side.LONG
                    else min(atr_candidate, observation.completed_structure_stop)
                )
            return atr_candidate
        else:
            return observation.completed_structure_stop
        return favorable - offset if self.side is Side.LONG else favorable + offset

    def _trail_hit(self, executable_price: Decimal) -> bool:
        if self.current_trail is None:
            return False
        return (
            executable_price <= self.current_trail
            if self.side is Side.LONG
            else executable_price >= self.current_trail
        )

    def _transition(
        self,
        to_state: TrailingState,
        *,
        event_time_ms: int,
        receive_time_ms: int,
        actor: str,
        reason_codes: tuple[str, ...],
        data_health: str,
    ) -> None:
        if event_time_ms < 0 or receive_time_ms < event_time_ms:
            raise ValueError("trailing 상태전이 시각 순서가 잘못됐습니다.")
        if not actor or not reason_codes:
            raise ValueError("trailing 상태전이 actor와 reason code가 필요합니다.")
        if self.state is to_state:
            return
        from_state = self.state
        if to_state not in _ALLOWED_TRANSITIONS[from_state]:
            raise ValueError(
                f"허용되지 않은 trailing 상태전이입니다: {from_state.value}->{to_state.value}"
            )
        material = "|".join(
            (
                self.account_id,
                self.trade_id,
                str(len(self.transitions) + 1),
                from_state.value,
                to_state.value,
                str(event_time_ms),
            )
        )
        transition = TrailingTransition(
            transition_id=f"trail-{hashlib.sha256(material.encode()).hexdigest()[:24]}",
            from_state=from_state,
            to_state=to_state,
            account_id=self.account_id,
            trade_id=self.trade_id,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            profile=self.profile,
            symbol=self.symbol,
            side=self.side,
            event_time_ms=event_time_ms,
            receive_time_ms=receive_time_ms,
            activation_rule=self.policy.activation_rule.value,
            activation_price=self.activation_price,
            activation_ts_ms=self.activation_ts_ms,
            highest_favorable_bid=self.highest_favorable_bid,
            lowest_favorable_ask=self.lowest_favorable_ask,
            current_trail=self.current_trail,
            previous_trail=self.previous_trail,
            initial_stop=self.initial_stop,
            fee_adjusted_breakeven=self.fee_adjusted_breakeven,
            original_quantity=self.original_quantity,
            realized_quantity=self.realized_quantity,
            runner_quantity=self.runner_quantity,
            mfe_r=self.mfe_r,
            mae_r=self.mae_r,
            peak_unrealized=self.peak_unrealized,
            current_unrealized=self.current_unrealized,
            giveback=self.giveback,
            transition_actor=actor,
            reason_codes=reason_codes,
            data_health=data_health,
        )
        self.state = to_state
        self.transitions.append(transition)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return cast(dict[str, Any], _encode(payload))

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TrailingStateMachine:
        policy_payload = payload.get("policy")
        transition_rows = payload.get("transitions", [])
        processed_event_rows = payload.get(
            "processed_event_ids",
            [payload["last_event_id"]] if payload.get("last_event_id") is not None else [],
        )
        if not isinstance(policy_payload, Mapping) or not isinstance(transition_rows, list):
            raise ValueError("trailing 복구 payload 형식이 잘못됐습니다.")
        if not isinstance(processed_event_rows, list) or not all(
            isinstance(value, str) for value in processed_event_rows
        ):
            raise ValueError("trailing 복구 중복 방지 event 목록이 잘못됐습니다.")
        policy = TrailingPolicy(
            policy_id=str(policy_payload["policy_id"]),
            model=TrailingModel(str(policy_payload["model"])),
            activation_rule=TrailingActivationRule(str(policy_payload["activation_rule"])),
            activation_r=Decimal(str(policy_payload["activation_r"])),
            partial_tp_required=_required_bool(
                policy_payload.get("partial_tp_required"),
                "partial_tp_required",
            ),
            fixed_distance=_optional_decimal(policy_payload.get("fixed_distance")),
            retracement_rate=_optional_decimal(policy_payload.get("retracement_rate")),
            atr_multiplier=_optional_decimal(policy_payload.get("atr_multiplier")),
            adverse_atr_multiplier=_optional_decimal(policy_payload.get("adverse_atr_multiplier")),
            adverse_signal_count=int(str(policy_payload.get("adverse_signal_count", 2))),
            adverse_persistence_ms=int(str(policy_payload.get("adverse_persistence_ms", 3_000))),
            activation_price_override=_optional_decimal(
                policy_payload.get("activation_price_override")
            ),
        )
        machine = cls(
            account_id=str(payload["account_id"]),
            trade_id=str(payload["trade_id"]),
            strategy_id=str(payload["strategy_id"]),
            strategy_version=str(payload["strategy_version"]),
            profile=str(payload["profile"]),
            symbol=str(payload["symbol"]),
            side=Side(str(payload["side"])),
            entry_price=Decimal(str(payload["entry_price"])),
            initial_stop=Decimal(str(payload["initial_stop"])),
            fee_adjusted_breakeven=Decimal(str(payload["fee_adjusted_breakeven"])),
            original_quantity=Decimal(str(payload["original_quantity"])),
            policy=policy,
            state=TrailingState(str(payload["state"])),
            activation_ts_ms=_optional_int(payload.get("activation_ts_ms")),
            highest_favorable_bid=_optional_decimal(payload.get("highest_favorable_bid")),
            lowest_favorable_ask=_optional_decimal(payload.get("lowest_favorable_ask")),
            current_trail=_optional_decimal(payload.get("current_trail")),
            previous_trail=_optional_decimal(payload.get("previous_trail")),
            realized_quantity=Decimal(str(payload["realized_quantity"])),
            runner_quantity=Decimal(str(payload["runner_quantity"])),
            mfe_r=Decimal(str(payload["mfe_r"])),
            mae_r=Decimal(str(payload["mae_r"])),
            peak_unrealized=Decimal(str(payload["peak_unrealized"])),
            current_unrealized=Decimal(str(payload["current_unrealized"])),
            giveback=Decimal(str(payload["giveback"])),
            last_event_id=str(payload["last_event_id"])
            if payload.get("last_event_id") is not None
            else None,
            last_event_time_ms=_optional_int(payload.get("last_event_time_ms")),
            processed_event_ids=list(processed_event_rows),
            transitions=[_transition_from_payload(row) for row in transition_rows],
        )
        if machine.realized_quantity + machine.runner_quantity > machine.original_quantity:
            raise ValueError("trailing 복구 수량 합이 original quantity를 넘습니다.")
        if machine.realized_quantity < 0 or machine.runner_quantity < 0:
            raise ValueError("trailing 복구 수량은 음수일 수 없습니다.")
        if machine.last_event_id is not None and (
            not machine.processed_event_ids
            or machine.processed_event_ids[-1] != machine.last_event_id
        ):
            raise ValueError("trailing 복구 마지막 event와 중복 방지 목록이 다릅니다.")
        if (machine.last_event_id is None) != (machine.last_event_time_ms is None):
            raise ValueError("trailing 복구 마지막 event ID와 시각이 함께 필요합니다.")
        machine._validate_recovered_protection()
        machine._validate_recovered_transitions()
        return machine

    def _validate_recovered_protection(self) -> None:
        favorable = (
            self.highest_favorable_bid if self.side is Side.LONG else self.lowest_favorable_ask
        )
        if favorable is not None and favorable <= 0:
            raise ValueError("trailing 복구 favorable executable 가격이 잘못됐습니다.")
        if self.activation_ts_ms is not None and self.activation_ts_ms < 0:
            raise ValueError("trailing 복구 activation 시각이 잘못됐습니다.")
        if self.current_trail is not None:
            protective_boundary = (
                max(self.initial_stop, self.fee_adjusted_breakeven)
                if self.side is Side.LONG
                else min(self.initial_stop, self.fee_adjusted_breakeven)
            )
            if (self.side is Side.LONG and self.current_trail < protective_boundary) or (
                self.side is Side.SHORT and self.current_trail > protective_boundary
            ):
                raise ValueError("trailing 복구 trail이 보호 경계보다 불리합니다.")
        if self.current_trail is not None and self.previous_trail is not None:
            if (self.side is Side.LONG and self.current_trail < self.previous_trail) or (
                self.side is Side.SHORT and self.current_trail > self.previous_trail
            ):
                raise ValueError("trailing 복구 trail 단조성이 깨졌습니다.")
        activated_states = {
            TrailingState.TRAIL_ARMED,
            TrailingState.PARTIAL_TP_PENDING,
            TrailingState.RUNNER_ACTIVE,
            TrailingState.TRAIL_EXIT_PENDING,
        }
        if self.state in activated_states and self.activation_ts_ms is None:
            raise ValueError("활성화된 trailing 복구 상태에 activation 시각이 없습니다.")

    def _validate_recovered_transitions(self) -> None:
        expected_state = TrailingState.ENTRY_PENDING
        previous_event_time_ms = -1
        for index, transition in enumerate(self.transitions, start=1):
            if transition.from_state is not expected_state:
                raise ValueError("trailing 복구 transition 연결이 끊겼습니다.")
            if transition.to_state not in _ALLOWED_TRANSITIONS[transition.from_state]:
                raise ValueError("trailing 복구 transition이 허용된 상태전이가 아닙니다.")
            if transition.event_time_ms < previous_event_time_ms:
                raise ValueError("trailing 복구 transition 시각이 역행합니다.")
            if transition.receive_time_ms < transition.event_time_ms:
                raise ValueError("trailing 복구 transition 수신시각이 역행합니다.")
            if (
                transition.account_id != self.account_id
                or transition.trade_id != self.trade_id
                or transition.strategy_id != self.strategy_id
                or transition.strategy_version != self.strategy_version
                or transition.profile != self.profile
                or transition.symbol != self.symbol
                or transition.side is not self.side
            ):
                raise ValueError("trailing 복구 transition 식별자가 상태와 다릅니다.")
            material = "|".join(
                (
                    self.account_id,
                    self.trade_id,
                    str(index),
                    transition.from_state.value,
                    transition.to_state.value,
                    str(transition.event_time_ms),
                )
            )
            expected_id = f"trail-{hashlib.sha256(material.encode()).hexdigest()[:24]}"
            if transition.transition_id != expected_id:
                raise ValueError("trailing 복구 transition ID checksum이 다릅니다.")
            expected_state = transition.to_state
            previous_event_time_ms = transition.event_time_ms
        if self.transitions and expected_state is not self.state:
            raise ValueError("trailing 복구 최종 transition과 현재 상태가 다릅니다.")
        if not self.transitions and self.state is not TrailingState.ENTRY_PENDING:
            raise ValueError("trailing 복구 상태에 transition 근거가 없습니다.")

    def checksum(self) -> str:
        canonical = json.dumps(
            self.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def audit_snapshot(
        self,
        *,
        event_time_ms: int,
        receive_time_ms: int,
        actor: str,
        reason_codes: tuple[str, ...],
        data_health: str,
    ) -> dict[str, Any]:
        """상태전이 없이 trail 보호값만 바뀐 시점의 복구 근거를 만든다."""

        if event_time_ms < 0 or receive_time_ms < event_time_ms:
            raise ValueError("trailing 감사 snapshot 시각 순서가 잘못됐습니다.")
        if not actor or not reason_codes:
            raise ValueError("trailing 감사 snapshot actor와 reason code가 필요합니다.")
        return cast(
            dict[str, Any],
            _encode(
                {
                    "state": self.state,
                    "account_id": self.account_id,
                    "trade_id": self.trade_id,
                    "strategy_id": self.strategy_id,
                    "strategy_version": self.strategy_version,
                    "profile": self.profile,
                    "symbol": self.symbol,
                    "side": self.side,
                    "event_time_ms": event_time_ms,
                    "receive_time_ms": receive_time_ms,
                    "activation_rule": self.policy.activation_rule,
                    "activation_price": self.activation_price,
                    "activation_ts_ms": self.activation_ts_ms,
                    "highest_favorable_bid": self.highest_favorable_bid,
                    "lowest_favorable_ask": self.lowest_favorable_ask,
                    "current_trail": self.current_trail,
                    "previous_trail": self.previous_trail,
                    "initial_stop": self.initial_stop,
                    "fee_adjusted_breakeven": self.fee_adjusted_breakeven,
                    "original_quantity": self.original_quantity,
                    "realized_quantity": self.realized_quantity,
                    "runner_quantity": self.runner_quantity,
                    "mfe_r": self.mfe_r,
                    "mae_r": self.mae_r,
                    "peak_unrealized": self.peak_unrealized,
                    "current_unrealized": self.current_unrealized,
                    "giveback": self.giveback,
                    "transition_actor": actor,
                    "reason_codes": reason_codes,
                    "data_health": data_health,
                    "state_checksum": self.checksum(),
                }
            ),
        )


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _encode(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_encode(nested) for nested in value]
    if isinstance(value, tuple):
        return [_encode(nested) for nested in value]
    return value


def _optional_decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(str(value)) if value is not None else None


def _required_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"trailing {field_name}은 boolean이어야 합니다.")
    return value


def _transition_from_payload(payload: Any) -> TrailingTransition:
    if not isinstance(payload, Mapping):
        raise ValueError("trailing transition payload 형식이 잘못됐습니다.")
    reason_rows = payload.get("reason_codes")
    if (
        not isinstance(reason_rows, list)
        or not reason_rows
        or not all(isinstance(value, str) and value for value in reason_rows)
    ):
        raise ValueError("trailing transition reason code 형식이 잘못됐습니다.")
    actor = payload.get("transition_actor")
    data_health = payload.get("data_health")
    if not isinstance(actor, str) or not actor:
        raise ValueError("trailing transition actor가 필요합니다.")
    if data_health not in _VALID_DATA_HEALTH:
        raise ValueError("trailing transition 데이터 건강상태가 잘못됐습니다.")
    return TrailingTransition(
        transition_id=str(payload["transition_id"]),
        from_state=TrailingState(str(payload["from_state"])),
        to_state=TrailingState(str(payload["to_state"])),
        account_id=str(payload["account_id"]),
        trade_id=str(payload["trade_id"]),
        strategy_id=str(payload["strategy_id"]),
        strategy_version=str(payload["strategy_version"]),
        profile=str(payload["profile"]),
        symbol=str(payload["symbol"]),
        side=Side(str(payload["side"])),
        event_time_ms=int(str(payload["event_time_ms"])),
        receive_time_ms=int(str(payload["receive_time_ms"])),
        activation_rule=str(payload["activation_rule"]),
        activation_price=Decimal(str(payload["activation_price"])),
        activation_ts_ms=_optional_int(payload.get("activation_ts_ms")),
        highest_favorable_bid=_optional_decimal(payload.get("highest_favorable_bid")),
        lowest_favorable_ask=_optional_decimal(payload.get("lowest_favorable_ask")),
        current_trail=_optional_decimal(payload.get("current_trail")),
        previous_trail=_optional_decimal(payload.get("previous_trail")),
        initial_stop=Decimal(str(payload["initial_stop"])),
        fee_adjusted_breakeven=Decimal(str(payload["fee_adjusted_breakeven"])),
        original_quantity=Decimal(str(payload["original_quantity"])),
        realized_quantity=Decimal(str(payload["realized_quantity"])),
        runner_quantity=Decimal(str(payload["runner_quantity"])),
        mfe_r=Decimal(str(payload["mfe_r"])),
        mae_r=Decimal(str(payload["mae_r"])),
        peak_unrealized=Decimal(str(payload["peak_unrealized"])),
        current_unrealized=Decimal(str(payload["current_unrealized"])),
        giveback=Decimal(str(payload["giveback"])),
        transition_actor=actor,
        reason_codes=tuple(reason_rows),
        data_health=str(data_health),
    )
