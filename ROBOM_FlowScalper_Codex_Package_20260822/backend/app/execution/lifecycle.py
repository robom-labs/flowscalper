"""중복에 안전한 상태머신과 BASE/STRESS 분리 회계를 제공한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from backend.app.costing import CostProfile
from backend.app.execution.models import (
    BookSnapshot,
    ExitReason,
    LifecycleState,
    PaperTrade,
    ProtectedPosition,
)
from backend.app.execution.simulator import (
    OpenPositionArguments,
    PaperExecutionEngine,
    PaperExecutionError,
)
from backend.app.risk import RiskManager, RiskState

_ALLOWED: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.SCANNING: {
        LifecycleState.CANDIDATE,
        LifecycleState.PAUSED,
        LifecycleState.FAULTED,
    },
    LifecycleState.CANDIDATE: {LifecycleState.ARMED, LifecycleState.SCANNING},
    LifecycleState.ARMED: {LifecycleState.ENTRY_PENDING, LifecycleState.SCANNING},
    LifecycleState.ENTRY_PENDING: {
        LifecycleState.PARTIALLY_FILLED,
        LifecycleState.PROTECTION_PENDING,
        LifecycleState.SCANNING,
        LifecycleState.FAULTED,
    },
    LifecycleState.PARTIALLY_FILLED: {
        LifecycleState.PROTECTION_PENDING,
        LifecycleState.EXIT_PENDING,
    },
    LifecycleState.PROTECTION_PENDING: {LifecycleState.PROTECTED, LifecycleState.FAULTED},
    LifecycleState.PROTECTED: {LifecycleState.MANAGING, LifecycleState.EXIT_PENDING},
    LifecycleState.MANAGING: {LifecycleState.EXIT_PENDING, LifecycleState.FAULTED},
    LifecycleState.EXIT_PENDING: {LifecycleState.RECONCILING, LifecycleState.FAULTED},
    LifecycleState.RECONCILING: {LifecycleState.CLOSED, LifecycleState.FAULTED},
    LifecycleState.CLOSED: {LifecycleState.COOLDOWN},
    LifecycleState.COOLDOWN: {LifecycleState.SCANNING},
    LifecycleState.PAUSED: {LifecycleState.SCANNING, LifecycleState.FAULTED},
    LifecycleState.FAULTED: set(),
}


@dataclass(slots=True)
class PaperStateMachine:
    state: LifecycleState = LifecycleState.SCANNING
    processed_event_ids: set[str] = field(default_factory=set)
    transitions: list[tuple[str, LifecycleState]] = field(default_factory=list)

    def transition(self, event_id: str, target: LifecycleState) -> bool:
        if event_id in self.processed_event_ids:
            return False
        if target not in _ALLOWED[self.state]:
            raise RuntimeError(f"허용되지 않은 상태 전이: {self.state} -> {target}")
        self.processed_event_ids.add(event_id)
        self.state = target
        self.transitions.append((event_id, target))
        return True


@dataclass(slots=True)
class PortfolioSet:
    states: dict[CostProfile, RiskState] = field(
        default_factory=lambda: {
            CostProfile.BASE: RiskState(),
            CostProfile.STRESS: RiskState(),
        }
    )


@dataclass(slots=True)
class PaperTradeService:
    engine: PaperExecutionEngine
    risk_manager: RiskManager
    risk_state: RiskState
    profile: CostProfile = CostProfile.BASE
    machine: PaperStateMachine = field(default_factory=PaperStateMachine)
    position: ProtectedPosition | None = None
    trade: PaperTrade | None = None

    def open(
        self,
        *,
        event_id: str,
        risk_key: str,
        now_ms: int,
        engine_arguments: OpenPositionArguments,
    ) -> ProtectedPosition | None:
        if event_id in self.machine.processed_event_ids:
            return self.position
        rejections = self.risk_manager.entry_rejections(self.risk_state, risk_key, now_ms)
        if rejections:
            return None
        self.machine.transition(f"{event_id}:candidate", LifecycleState.CANDIDATE)
        self.machine.transition(f"{event_id}:armed", LifecycleState.ARMED)
        self.machine.transition(f"{event_id}:pending", LifecycleState.ENTRY_PENDING)
        try:
            result = self.engine.open_position(profile=self.profile, **engine_arguments)
        except (PaperExecutionError, ValueError):
            self.machine.transition(f"{event_id}:rejected", LifecycleState.SCANNING)
            self.machine.processed_event_ids.add(event_id)
            return None
        if result.position is None:
            self.machine.transition(f"{event_id}:no-fill", LifecycleState.SCANNING)
            self.machine.processed_event_ids.add(event_id)
            return None
        if result.entry_order.status.value == "PARTIALLY_FILLED":
            self.machine.transition(f"{event_id}:partial", LifecycleState.PARTIALLY_FILLED)
        self.machine.transition(f"{event_id}:protection-pending", LifecycleState.PROTECTION_PENDING)
        self.position = result.position
        protection_mismatch = any(
            order.requested_quantity != self.position.quantity
            for order in self.position.protection_orders
        )
        if protection_mismatch:
            self.machine.transition(f"{event_id}:protection-fault", LifecycleState.FAULTED)
            raise RuntimeError("보호주문 수량이 체결 포지션과 다릅니다.")
        self.machine.transition(f"{event_id}:protected", LifecycleState.PROTECTED)
        self.machine.transition(f"{event_id}:managing", LifecycleState.MANAGING)
        self.machine.processed_event_ids.add(event_id)
        self.risk_manager.record_open(self.risk_state, now_ms=now_ms)
        return self.position

    def close(
        self,
        *,
        event_id: str,
        reason: ExitReason,
        trigger_reference_price: Decimal,
        book_at_arrival: BookSnapshot,
        risk_key: str,
        flags: tuple[str, ...] = (),
    ) -> PaperTrade | None:
        if event_id in self.machine.processed_event_ids:
            return self.trade
        if self.position is None:
            return None
        self.machine.transition(f"{event_id}:exit", LifecycleState.EXIT_PENDING)
        try:
            result = self.engine.close_position(
                self.position,
                reason=reason,
                trigger_reference_price=trigger_reference_price,
                book_at_arrival=book_at_arrival,
            )
        except (PaperExecutionError, ValueError):
            self.machine.transition(f"{event_id}:exit-fault", LifecycleState.FAULTED)
            self.machine.processed_event_ids.add(event_id)
            raise
        if result.remaining_quantity != 0 or result.exit_order.fill is None:
            self.machine.transition(f"{event_id}:partial-fault", LifecycleState.FAULTED)
            raise PaperExecutionError("v0.1 종료는 전체 포지션 체결이 필요합니다.")
        self.machine.transition(f"{event_id}:reconcile", LifecycleState.RECONCILING)
        exit_fill = result.exit_order.fill
        entry_fill = self.position.entry_fill
        direction = Decimal(1) if self.position.side.value == "LONG" else Decimal(-1)
        gross = (
            (exit_fill.average_price - entry_fill.average_price)
            * self.position.quantity
            * direction
        )
        fees = entry_fill.fee_usdt + exit_fill.fee_usdt
        net = gross - fees
        self.trade = PaperTrade(
            trade_id=self.position.trade_id,
            run_id=self.position.run_id,
            venue=self.position.venue,
            symbol=self.position.symbol,
            strategy_id="LEGACY_V01",
            side=self.position.side,
            entry_price=entry_fill.average_price,
            exit_price=exit_fill.average_price,
            quantity=self.position.quantity,
            initial_stop=self.position.initial_stop,
            take_profit=self.position.take_profit,
            exit_reason=reason,
            gross_pnl_usdt=gross,
            fees_usdt=fees,
            slippage_usdt=entry_fill.slippage_usdt + exit_fill.slippage_usdt,
            net_pnl_usdt=net,
            opened_ts_ms=self.position.opened_ts_ms,
            closed_ts_ms=book_at_arrival.ts_ms,
            holding_ms=max(0, book_at_arrival.ts_ms - self.position.opened_ts_ms),
            regime="UNKNOWN",
            mae_r=Decimal(0),
            mfe_r=Decimal(0),
            flags=flags,
            profile=self.profile,
            strategy_version="LEGACY_V01",
        )
        self.risk_manager.record_close(
            self.risk_state,
            net,
            key=risk_key,
            now_ms=book_at_arrival.ts_ms,
        )
        self.position = None
        self.machine.transition(f"{event_id}:closed", LifecycleState.CLOSED)
        self.machine.transition(f"{event_id}:cooldown", LifecycleState.COOLDOWN)
        self.machine.processed_event_ids.add(event_id)
        return self.trade
