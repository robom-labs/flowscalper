"""지연 후 올바른 호가 방향을 소진하는 보수적 IOC PAPER 체결을 구현한다."""

from __future__ import annotations

from decimal import Decimal
from typing import TypedDict
from uuid import uuid4

from backend.app.costing import CostModel, CostProfile
from backend.app.domain.models import Side, Venue
from backend.app.execution.models import (
    BookSnapshot,
    EntryResult,
    ExitReason,
    ExitResult,
    Fill,
    OrderIntent,
    OrderStatus,
    PaperOrder,
    ProtectedPosition,
)


class PaperExecutionError(RuntimeError):
    """안전한 PAPER 체결이 불가능할 때 발생한다."""


class OpenPositionArguments(TypedDict):
    trade_id: str
    run_id: str
    venue: Venue
    symbol: str
    side: Side
    requested_quantity: Decimal
    reference_price: Decimal
    price_cap: Decimal
    initial_stop: Decimal
    take_profit: Decimal
    decision_ts_ms: int
    book_at_arrival: BookSnapshot
    minimum_quantity: Decimal


class PaperExecutionEngine:
    def __init__(self, cost_model: CostModel | None = None) -> None:
        self.cost_model = cost_model or CostModel()

    def open_position(
        self,
        *,
        trade_id: str,
        run_id: str,
        venue: Venue,
        symbol: str,
        side: Side,
        requested_quantity: Decimal,
        reference_price: Decimal,
        price_cap: Decimal,
        initial_stop: Decimal,
        take_profit: Decimal,
        decision_ts_ms: int,
        book_at_arrival: BookSnapshot,
        minimum_quantity: Decimal,
        profile: CostProfile = CostProfile.BASE,
    ) -> EntryResult:
        self._validate_book(book_at_arrival, venue, symbol)
        arrival_ts = decision_ts_ms + self.cost_model.arrival_latency_ms(profile)
        if book_at_arrival.ts_ms < arrival_ts:
            raise PaperExecutionError("지연 도착시각 이전 호가로 체결할 수 없습니다.")
        levels = book_at_arrival.asks if side is Side.LONG else book_at_arrival.bids
        fill = self._consume(
            levels=levels,
            requested_quantity=requested_quantity,
            reference_price=reference_price,
            price_cap=price_cap,
            buy=side is Side.LONG,
            book_ts_ms=book_at_arrival.ts_ms,
            fee_bps=self.cost_model.fee_bps(entry=True, profile=profile),
            slippage_multiplier=self.cost_model.slippage_multiplier(profile),
        )
        status = OrderStatus.REJECTED
        reasons: tuple[str, ...] = ("NO_EXECUTABLE_DEPTH",)
        position: ProtectedPosition | None = None
        if fill is not None and fill.quantity >= minimum_quantity:
            status = (
                OrderStatus.FILLED
                if fill.quantity == requested_quantity
                else OrderStatus.PARTIALLY_FILLED
            )
            reasons = (
                ("PARTIAL_FILL", "IOC_REMAINDER_CANCELED")
                if status is OrderStatus.PARTIALLY_FILLED
                else ()
            )
            protection = self._protection_orders(
                trade_id=trade_id,
                run_id=run_id,
                venue=venue,
                symbol=symbol,
                side=side,
                quantity=fill.quantity,
                stop=initial_stop,
                target=take_profit,
                created_ts_ms=book_at_arrival.ts_ms,
            )
            position = ProtectedPosition(
                trade_id=trade_id,
                run_id=run_id,
                venue=venue,
                symbol=symbol,
                side=side,
                quantity=fill.quantity,
                entry_reference_price=reference_price,
                entry_fill=fill,
                initial_stop=initial_stop,
                current_stop=initial_stop,
                take_profit=take_profit,
                protection_orders=protection,
                opened_ts_ms=book_at_arrival.ts_ms,
                profile=profile,
            )
        elif fill is not None:
            reasons = ("DUST_FILL_REJECTED",)
        order = PaperOrder(
            order_id=f"paper-{uuid4().hex[:12]}",
            trade_id=trade_id,
            run_id=run_id,
            venue=venue,
            symbol=symbol,
            side="BUY" if side is Side.LONG else "SELL",
            intent=OrderIntent.ENTRY_IOC,
            status=status,
            requested_quantity=requested_quantity,
            filled_quantity=fill.quantity if fill else Decimal(0),
            price_cap=price_cap,
            trigger_price=None,
            fill=fill,
            created_ts_ms=decision_ts_ms,
            arrival_ts_ms=book_at_arrival.ts_ms,
            reason_codes=reasons,
        )
        return EntryResult(entry_order=order, position=position)

    def close_position(
        self,
        position: ProtectedPosition,
        *,
        reason: ExitReason,
        trigger_reference_price: Decimal,
        book_at_arrival: BookSnapshot,
        requested_quantity: Decimal | None = None,
        decision_ts_ms: int | None = None,
    ) -> ExitResult:
        self._validate_book(book_at_arrival, position.venue, position.symbol)
        quantity = requested_quantity or position.quantity
        if quantity <= 0 or quantity > position.quantity:
            raise PaperExecutionError("청산 수량은 양수이고 잔여 포지션 이하여야 합니다.")
        buy = position.side is Side.SHORT
        levels = book_at_arrival.asks if buy else book_at_arrival.bids
        fill = self._consume(
            levels=levels,
            requested_quantity=quantity,
            reference_price=trigger_reference_price,
            price_cap=None,
            buy=buy,
            book_ts_ms=book_at_arrival.ts_ms,
            fee_bps=self.cost_model.fee_bps(entry=False, profile=position.profile),
            slippage_multiplier=self.cost_model.slippage_multiplier(position.profile),
        )
        filled = fill.quantity if fill else Decimal(0)
        remaining = position.quantity - filled
        status = OrderStatus.FILLED if filled == quantity else OrderStatus.PARTIALLY_FILLED
        if fill is None:
            status = OrderStatus.REJECTED
        order = PaperOrder(
            order_id=f"paper-{uuid4().hex[:12]}",
            trade_id=position.trade_id,
            run_id=position.run_id,
            venue=position.venue,
            symbol=position.symbol,
            side="BUY" if buy else "SELL",
            intent=self._exit_intent(reason),
            status=status,
            requested_quantity=quantity,
            filled_quantity=filled,
            price_cap=None,
            trigger_price=trigger_reference_price,
            fill=fill,
            created_ts_ms=decision_ts_ms or book_at_arrival.ts_ms,
            arrival_ts_ms=book_at_arrival.ts_ms,
            reason_codes=("PARTIAL_EXIT",) if remaining else (),
        )
        return ExitResult(order, filled, remaining)

    @staticmethod
    def resolve_ambiguous_boundaries(
        *,
        take_profit_hit: bool,
        stop_hit: bool,
    ) -> tuple[ExitReason | None, tuple[str, ...]]:
        if take_profit_hit and stop_hit:
            return ExitReason.STOP, ("AMBIGUOUS_ORDERING_PESSIMISTIC",)
        if stop_hit:
            return ExitReason.STOP, ()
        if take_profit_hit:
            return ExitReason.TAKE_PROFIT, ()
        return None, ()

    @staticmethod
    def executable_trigger(position: ProtectedPosition, book: BookSnapshot) -> ExitReason | None:
        book.validate()
        best_bid = book.bids[0][0]
        best_ask = book.asks[0][0]
        if position.side is Side.LONG:
            if best_bid <= position.current_stop:
                return ExitReason.STOP
            if best_bid >= position.take_profit:
                return ExitReason.TAKE_PROFIT
        else:
            if best_ask >= position.current_stop:
                return ExitReason.STOP
            if best_ask <= position.take_profit:
                return ExitReason.TAKE_PROFIT
        return None

    @staticmethod
    def _consume(
        *,
        levels: tuple[tuple[Decimal, Decimal], ...],
        requested_quantity: Decimal,
        reference_price: Decimal,
        price_cap: Decimal | None,
        buy: bool,
        book_ts_ms: int,
        fee_bps: Decimal,
        slippage_multiplier: Decimal = Decimal(1),
    ) -> Fill | None:
        remaining = requested_quantity
        total_quantity = Decimal(0)
        notional = Decimal(0)
        levels_consumed = 0
        for price, available in levels:
            outside_cap = price_cap is not None and (
                (buy and price > price_cap) or (not buy and price < price_cap)
            )
            if outside_cap:
                break
            quantity = min(remaining, available)
            if quantity <= 0:
                continue
            total_quantity += quantity
            notional += price * quantity
            remaining -= quantity
            levels_consumed += 1
            if remaining == 0:
                break
        if total_quantity == 0:
            return None
        average = notional / total_quantity
        reference_notional = reference_price * total_quantity
        adverse_notional = notional - reference_notional if buy else reference_notional - notional
        slippage = max(Decimal(0), adverse_notional) * slippage_multiplier
        fee = notional * fee_bps / Decimal(10_000)
        return Fill(
            quantity=total_quantity,
            average_price=average,
            notional=notional,
            fee_usdt=fee,
            slippage_usdt=slippage,
            levels_consumed=levels_consumed,
            book_ts_ms=book_ts_ms,
        )

    @staticmethod
    def _validate_book(book: BookSnapshot, venue: Venue, symbol: str) -> None:
        book.validate()
        if book.venue is not venue or book.symbol != symbol:
            raise PaperExecutionError("포지션과 다른 거래소·종목 호가를 사용할 수 없습니다.")

    @staticmethod
    def _protection_orders(
        *,
        trade_id: str,
        run_id: str,
        venue: Venue,
        symbol: str,
        side: Side,
        quantity: Decimal,
        stop: Decimal,
        target: Decimal,
        created_ts_ms: int,
    ) -> tuple[PaperOrder, PaperOrder]:
        exit_side = "SELL" if side is Side.LONG else "BUY"
        take_profit = PaperOrder(
            order_id=f"paper-{uuid4().hex[:12]}",
            trade_id=trade_id,
            run_id=run_id,
            venue=venue,
            symbol=symbol,
            side=exit_side,
            intent=OrderIntent.TAKE_PROFIT,
            status=OrderStatus.CREATED,
            requested_quantity=quantity,
            filled_quantity=Decimal(0),
            price_cap=None,
            trigger_price=target,
            fill=None,
            created_ts_ms=created_ts_ms,
            arrival_ts_ms=None,
            reason_codes=(),
        )
        stop_exit = PaperOrder(
            order_id=f"paper-{uuid4().hex[:12]}",
            trade_id=trade_id,
            run_id=run_id,
            venue=venue,
            symbol=symbol,
            side=exit_side,
            intent=OrderIntent.STOP_EXIT,
            status=OrderStatus.CREATED,
            requested_quantity=quantity,
            filled_quantity=Decimal(0),
            price_cap=None,
            trigger_price=stop,
            fill=None,
            created_ts_ms=created_ts_ms,
            arrival_ts_ms=None,
            reason_codes=(),
        )
        return take_profit, stop_exit

    @staticmethod
    def _exit_intent(reason: ExitReason) -> OrderIntent:
        return {
            ExitReason.TAKE_PROFIT: OrderIntent.TAKE_PROFIT,
            ExitReason.STOP: OrderIntent.STOP_EXIT,
            ExitReason.EDGE_DECAY: OrderIntent.EDGE_DECAY_EXIT,
            ExitReason.PROFIT_PROTECTION: OrderIntent.EDGE_DECAY_EXIT,
            ExitReason.EMERGENCY_STALE: OrderIntent.EMERGENCY_EXIT,
            ExitReason.DATA_GAP: OrderIntent.EMERGENCY_EXIT,
            ExitReason.MANUAL_PAPER_EXIT: OrderIntent.MANUAL_PAPER_EXIT,
            ExitReason.FAULT: OrderIntent.EMERGENCY_EXIT,
        }[reason]
