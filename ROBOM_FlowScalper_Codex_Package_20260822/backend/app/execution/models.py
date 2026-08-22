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

    def validate(self) -> None:
        if not self.sequence_valid or self.stale:
            raise ValueError("stale 또는 sequence-invalid 호가를 체결에 사용할 수 없습니다.")
        if not self.bids or not self.asks or self.bids[0][0] >= self.asks[0][0]:
            raise ValueError("실행 가능한 비교차 양방향 호가가 필요합니다.")


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
    holding_ms: int
    flags: tuple[str, ...]
    profile: CostProfile
