"""PAPER 주문, 체결, 보호 포지션과 종료 레코드의 Decimal 모델을 정의한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from backend.app.costing import CostProfile
from backend.app.domain.models import Side, Venue


class OrderIntent(StrEnum):
    ENTRY_IOC = "ENTRY_IOC"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_EXIT = "STOP_EXIT"
    EDGE_DECAY_EXIT = "EDGE_DECAY_EXIT"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"
    MANUAL_PAPER_EXIT = "MANUAL_PAPER_EXIT"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    PENDING_LATENCY = "PENDING_LATENCY"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    FINALIZED = "FINALIZED"


class LifecycleState(StrEnum):
    SCANNING = "SCANNING"
    CANDIDATE = "CANDIDATE"
    ARMED = "ARMED"
    ENTRY_PENDING = "ENTRY_PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTED = "PROTECTED"
    MANAGING = "MANAGING"
    EXIT_PENDING = "EXIT_PENDING"
    RECONCILING = "RECONCILING"
    CLOSED = "CLOSED"
    COOLDOWN = "COOLDOWN"
    PAUSED = "PAUSED"
    FAULTED = "FAULTED"


class ExitReason(StrEnum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP = "STOP"
    EDGE_DECAY = "EDGE_DECAY"
    PROFIT_PROTECTION = "PROFIT_PROTECTION"
    TRAILING_STOP = "TRAILING_STOP"
    MAX_HOLD = "MAX_HOLD"
    EMERGENCY_STALE = "EMERGENCY_STALE"
    DATA_GAP = "DATA_GAP"
    MANUAL_PAPER_EXIT = "MANUAL_PAPER_EXIT"
    FAULT = "FAULT"


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    venue: Venue
    symbol: str
    ts_ms: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    sequence_valid: bool = True
    stale: bool = False
    receive_ts_ms: int | None = None

    def validate(self) -> None:
        if not self.sequence_valid or self.stale:
            raise ValueError("stale 또는 sequence-invalid 호가를 체결에 사용할 수 없습니다.")
        if not self.bids or not self.asks:
            raise ValueError("실행 가능한 비교차 양방향 호가가 필요합니다.")
        if any(
            not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity <= 0
            for price, quantity in (*self.bids, *self.asks)
        ):
            raise ValueError("체결 호가 가격과 수량은 유한한 양수여야 합니다.")
        if self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("실행 가능한 비교차 양방향 호가가 필요합니다.")
        if self.receive_ts_ms is not None and self.receive_ts_ms < self.ts_ms:
            raise ValueError("호가 receive 시각은 event 시각보다 빠를 수 없습니다.")


@dataclass(frozen=True, slots=True)
class Fill:
    quantity: Decimal
    average_price: Decimal
    notional: Decimal
    fee_usdt: Decimal
    slippage_usdt: Decimal
    levels_consumed: int
    book_ts_ms: int


@dataclass(frozen=True, slots=True)
class PaperOrder:
    order_id: str
    trade_id: str
    run_id: str
    venue: Venue
    symbol: str
    side: str
    intent: OrderIntent
    status: OrderStatus
    requested_quantity: Decimal
    filled_quantity: Decimal
    price_cap: Decimal | None
    trigger_price: Decimal | None
    fill: Fill | None
    created_ts_ms: int
    arrival_ts_ms: int | None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProtectedPosition:
    trade_id: str
    run_id: str
    venue: Venue
    symbol: str
    side: Side
    quantity: Decimal
    entry_reference_price: Decimal
    entry_fill: Fill
    initial_stop: Decimal
    current_stop: Decimal
    take_profit: Decimal
    protection_orders: tuple[PaperOrder, PaperOrder]
    opened_ts_ms: int
    profile: CostProfile


@dataclass(frozen=True, slots=True)
class EntryResult:
    entry_order: PaperOrder
    position: ProtectedPosition | None


@dataclass(frozen=True, slots=True)
class ExitResult:
    exit_order: PaperOrder
    filled_quantity: Decimal
    remaining_quantity: Decimal


@dataclass(frozen=True, slots=True)
class PaperTrade:
    trade_id: str
    run_id: str
    venue: Venue
    symbol: str
    strategy_id: str
    side: Side
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    initial_stop: Decimal
    take_profit: Decimal
    exit_reason: ExitReason
    gross_pnl_usdt: Decimal
    fees_usdt: Decimal
    slippage_usdt: Decimal
    net_pnl_usdt: Decimal
    opened_ts_ms: int
    closed_ts_ms: int
    holding_ms: int
    regime: str
    mae_r: Decimal
    mfe_r: Decimal
    flags: tuple[str, ...]
    profile: CostProfile
    strategy_version: str = "LEGACY_UNVERSIONED"
    candidate_id: str | None = None
    signal_event_id: str | None = None
    take_profit_1: Decimal | None = None
    take_profit_2: Decimal | None = None
    tp1_hit_ts_ms: int | None = None
    tp2_hit_ts_ms: int | None = None
    time_to_tp1_ms: int | None = None
    time_to_tp2_ms: int | None = None
    time_to_stop_ms: int | None = None
    trailing_activation_ts_ms: int | None = None
    runner_started_ts_ms: int | None = None
    peak_unrealized_usdt: Decimal = Decimal(0)
    giveback_usdt: Decimal = Decimal(0)
    runner_net_pnl_usdt: Decimal = Decimal(0)
    trail_trigger_slippage_usdt: Decimal = Decimal(0)
    trailing_state_checksum: str | None = None
    selected_margin_leverage: Decimal = Decimal("1")
    entry_notional_usdt: Decimal = Decimal(0)
    margin_used_usdt: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not self.strategy_version.strip():
            raise ValueError("PAPER 거래의 전략 버전은 비어 있을 수 없습니다.")
        if (
            not self.selected_margin_leverage.is_finite()
            or not Decimal(1) <= self.selected_margin_leverage <= Decimal(100)
        ):
            raise ValueError("PAPER 거래 레버리지는 1배 이상 100배 이하여야 합니다.")
        if self.closed_ts_ms < self.opened_ts_ms:
            raise ValueError("PAPER 거래 종료 시각은 진입 이전일 수 없습니다.")
        milestones = (
            self.tp1_hit_ts_ms,
            self.tp2_hit_ts_ms,
            self.trailing_activation_ts_ms,
            self.runner_started_ts_ms,
        )
        if any(
            timestamp is not None
            and (timestamp < self.opened_ts_ms or timestamp > self.closed_ts_ms)
            for timestamp in milestones
        ):
            raise ValueError("PAPER 거래 milestone 시각이 진입·종료 범위를 벗어났습니다.")
        if self.runner_started_ts_ms is not None and (
            self.trailing_activation_ts_ms is None
            or self.runner_started_ts_ms < self.trailing_activation_ts_ms
        ):
            raise ValueError("PAPER runner는 trailing 활성화 후에만 시작할 수 있습니다.")
