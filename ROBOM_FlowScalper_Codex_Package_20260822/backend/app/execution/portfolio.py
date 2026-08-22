"""main·shadow PAPER 계좌에 동일한 지연·호가소진·부분체결 규칙을 적용한다."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, Decimal
from typing import Any

from backend.app.candidates import CandidatePlan, TakeProfitTarget
from backend.app.costing import CostModel, CostProfile
from backend.app.domain.models import Side
from backend.app.execution.models import (
    BookSnapshot,
    ExitReason,
    Fill,
    PaperOrder,
    PaperTrade,
    ProtectedPosition,
)
from backend.app.execution.simulator import PaperExecutionEngine, PaperExecutionError
from backend.app.features import FeatureSnapshot
from backend.app.positions import (
    ManagementAction,
    PositionHealth,
    PositionManager,
)
from backend.app.regime import Regime
from backend.app.risk import RiskManager, RiskState
from backend.app.strategies.shadow import ShadowLedger, ShadowPosition


@dataclass(frozen=True, slots=True)
class PendingEntry:
    plan: CandidatePlan


@dataclass(frozen=True, slots=True)
class PendingExit:
    reason: ExitReason
    label: str
    requested_quantity: Decimal
    trigger_reference_price: Decimal
    trigger_ts_ms: int


@dataclass(frozen=True, slots=True)
class ExitLeg:
    label: str
    reason: ExitReason
    fill: Fill


@dataclass(slots=True)
class ManagedPaperPosition:
    plan: CandidatePlan
    protected: ProtectedPosition
    original_quantity: Decimal
    remaining_quantity: Decimal
    target_remaining: dict[str, Decimal]
    exit_legs: list[ExitLeg] = field(default_factory=list)
    pending_exit: PendingExit | None = None
    mfe_r: Decimal = Decimal(0)
    mae_r: Decimal = Decimal(0)
    forced_exit_reason: ExitReason | None = None
    forced_exit_label: str | None = None

    def next_target(self) -> TakeProfitTarget | None:
        for target in self.plan.take_profit_targets:
            if self.target_remaining.get(target.label, Decimal(0)) > 0:
                return target
        return None


@dataclass(slots=True)
class ExecutionAccount:
    account_id: str
    profile: CostProfile
    risk_state: RiskState = field(default_factory=RiskState)
    pending_entry: PendingEntry | None = None
    position: ManagedPaperPosition | None = None
    completed_trades: list[PaperTrade] = field(default_factory=list)
    entry_orders: list[PaperOrder] = field(default_factory=list)
    exit_orders: list[PaperOrder] = field(default_factory=list)


class PaperPortfolioEngine:
    """main 1개와 전략별 BASE·STRESS 계좌를 독립적으로 진행한다."""

    MAIN_ACCOUNT_ID = "MAIN:BASE"

    def __init__(
        self,
        *,
        run_id: str,
        strategy_ids: tuple[str, ...],
        shadow_ledger: ShadowLedger,
        execution_engine: PaperExecutionEngine | None = None,
        risk_manager: RiskManager | None = None,
        cost_model: CostModel | None = None,
        position_manager: PositionManager | None = None,
    ) -> None:
        self.run_id = run_id
        self.shadow_ledger = shadow_ledger
        self.execution_engine = execution_engine or PaperExecutionEngine()
        self.risk_manager = risk_manager or RiskManager()
        self.cost_model = cost_model or self.execution_engine.cost_model
        self.position_manager = position_manager or PositionManager()
        self.main = ExecutionAccount(self.MAIN_ACCOUNT_ID, CostProfile.BASE)
        self.shadows = {
            self._shadow_account_id(strategy_id, profile): ExecutionAccount(
                self._shadow_account_id(strategy_id, profile), profile
            )
            for strategy_id in strategy_ids
            for profile in CostProfile
        }
        self.audit_events: list[dict[str, Any]] = []
        self._new_main_trades: list[PaperTrade] = []

    @property
    def accounts(self) -> tuple[ExecutionAccount, ...]:
        return (self.main, *self.shadows.values())

    def offer(self, plans: tuple[CandidatePlan, ...], *, entries_paused: bool) -> None:
        """현재 호가에서 체결하지 않고 다음 유효 호가까지 pending으로 둔다."""

        valid = tuple(plan for plan in plans if plan.run_id == self.run_id)
        if not entries_paused and self._account_available(self.main):
            eligible = sorted(
                (plan for plan in valid if plan.main_eligible),
                key=CandidatePlan.arbitration_key,
            )
            if eligible:
                selected = eligible[0]
                rejections = self.risk_manager.entry_rejections(
                    self.main.risk_state,
                    f"{selected.symbol}:{selected.strategy_id}",
                    selected.signal_time_ms,
                )
                if not rejections:
                    self.main.pending_entry = PendingEntry(selected)
                    self._audit(
                        "MAIN_CANDIDATE_SELECTED",
                        selected,
                        competing_candidate_ids=[plan.candidate_id for plan in eligible[1:]],
                    )
                else:
                    self._audit("MAIN_RISK_REJECTED", selected, reason_codes=list(rejections))
        elif entries_paused and valid:
            self._audit(
                "MAIN_ENTRY_PAUSED",
                sorted(valid, key=CandidatePlan.arbitration_key)[0],
            )

        by_strategy: dict[str, list[CandidatePlan]] = {}
        for plan in valid:
            if plan.shadow_eligible:
                by_strategy.setdefault(plan.strategy_id, []).append(plan)
        for strategy_id, strategy_plans in by_strategy.items():
            selected = sorted(strategy_plans, key=CandidatePlan.arbitration_key)[0]
            for profile in CostProfile:
                account = self.shadows[self._shadow_account_id(strategy_id, profile)]
                if not self._account_available(account):
                    continue
                rejections = self.risk_manager.entry_rejections(
                    account.risk_state,
                    f"{selected.symbol}:{selected.strategy_id}",
                    selected.signal_time_ms,
                )
                if rejections:
                    self._audit(
                        "SHADOW_RISK_REJECTED",
                        selected,
                        account_id=account.account_id,
                        reason_codes=list(rejections),
                    )
                    continue
                account.pending_entry = PendingEntry(selected)
                self._audit(
                    "SHADOW_CANDIDATE_ARMED",
                    selected,
                    account_id=account.account_id,
                )

    def on_book(self, book: BookSnapshot) -> None:
        try:
            book.validate()
        except ValueError:
            self.audit_events.append(
                {
                    "event": "BOOK_REJECTED_FOR_EXECUTION",
                    "symbol": book.symbol,
                    "ts_ms": book.ts_ms,
                }
            )
            return
        for account in self.accounts:
            if account.position is not None and account.position.plan.symbol == book.symbol:
                self._advance_position(account, book)
            elif (
                account.pending_entry is not None
                and account.pending_entry.plan.symbol == book.symbol
            ):
                self._advance_entry(account, book)

    def request_main_exit(self, *, now_ms: int, reason: ExitReason) -> bool:
        managed = self.main.position
        if managed is None or managed.pending_exit is not None:
            return False
        managed.pending_exit = PendingExit(
            reason=reason,
            label=reason.value,
            requested_quantity=managed.remaining_quantity,
            trigger_reference_price=Decimal(0),
            trigger_ts_ms=now_ms,
        )
        managed.forced_exit_reason = reason
        managed.forced_exit_label = reason.value
        return True

    def evaluate_health(
        self,
        snapshot: FeatureSnapshot,
        regime: Regime,
        *,
        now_ms: int,
        recovered_gap_duration_ms: int = 0,
    ) -> None:
        """고정시간 대신 실제 근거·흐름·유동성 건강으로 종료를 결정한다."""

        for account in self.accounts:
            managed = account.position
            if managed is None or managed.plan.symbol != snapshot.symbol:
                continue
            plan = managed.plan
            direction = Decimal(1) if plan.direction is Side.LONG else Decimal(-1)
            mark = Decimal(str(snapshot.mid))
            entry = managed.protected.entry_fill.average_price
            initial_risk = abs(entry - plan.initial_stop)
            if initial_risk <= 0:
                continue
            current_r = (mark - entry) * direction / initial_risk
            managed.mfe_r = max(managed.mfe_r, current_r)
            managed.mae_r = min(managed.mae_r, current_r)
            next_target = managed.next_target() or plan.take_profit_targets[-1]
            remaining_edge = (
                (next_target.price - mark) * direction
                - plan.expected_fees_usdt / max(plan.position_size, plan.minimum_quantity)
                - plan.expected_slippage_usdt
                / max(plan.position_size, plan.minimum_quantity)
            )
            flow_aligned = snapshot.ofi_3s * float(direction)
            trade_aligned = snapshot.trade_imbalance_3s * float(direction)
            micro_aligned = (Decimal(str(snapshot.microprice)) - mark) * direction
            regime_health = (
                1.0
                if regime is plan.regime
                else 0.20
                if regime in {Regime.SHOCK, Regime.DEGRADED}
                else 0.45
            )
            flow_health = 0.80 if flow_aligned > 0 and trade_aligned >= 0 else 0.20
            microprice_health = 0.80 if micro_aligned >= 0 else 0.20
            spread_health = max(0.0, min(1.0, 1.0 - snapshot.spread_bps / 12.0))
            liquidity_health = (
                0.80
                if snapshot.data_healthy
                and min(snapshot.depth_bid_10, snapshot.depth_ask_10) > 0
                else 0.20
            )
            opposite_aggression = max(
                0.0,
                min(1.0, -trade_aligned),
            )
            health = PositionHealth(
                structure_health=regime_health,
                flow_health=flow_health,
                microprice_alignment=microprice_health,
                liquidity_health=liquidity_health,
                spread_health=spread_health,
                opposite_aggression=opposite_aggression,
                data_health=1.0 if snapshot.data_healthy else 0.0,
                remaining_edge=remaining_edge,
                current_r=current_r,
                mfe_r=managed.mfe_r,
                mae_r=managed.mae_r,
            )
            decision = self.position_manager.evaluate(
                managed.protected,
                health,
                now_ms=now_ms,
                data_stale=not snapshot.data_healthy,
                recovered_gap_duration_ms=recovered_gap_duration_ms,
            )
            if decision.proposed_stop is not None:
                managed.protected = self.position_manager.tighten_stop(
                    managed.protected,
                    decision.proposed_stop,
                )
            reason = {
                ManagementAction.EXIT_EDGE_DECAY: ExitReason.EDGE_DECAY,
                ManagementAction.EXIT_PROFIT_PROTECTION: ExitReason.PROFIT_PROTECTION,
                ManagementAction.EXIT_EMERGENCY_STALE: ExitReason.EMERGENCY_STALE,
            }.get(decision.action)
            if reason is not None and managed.pending_exit is None:
                managed.forced_exit_reason = reason
                managed.forced_exit_label = decision.action.value
                managed.pending_exit = PendingExit(
                    reason=reason,
                    label=decision.action.value,
                    requested_quantity=managed.remaining_quantity,
                    trigger_reference_price=mark,
                    trigger_ts_ms=now_ms,
                )
                self._audit(
                    "MANAGEMENT_EXIT_ARMED",
                    plan,
                    account_id=account.account_id,
                    action=decision.action.value,
                    reason_codes=list(decision.reason_codes),
                )

    def drain_new_main_trades(self) -> tuple[PaperTrade, ...]:
        rows = tuple(self._new_main_trades)
        self._new_main_trades.clear()
        return rows

    def main_position_snapshot(self, book: BookSnapshot | None = None) -> dict[str, object] | None:
        managed = self.main.position
        if managed is None:
            return None
        plan = managed.plan
        entry_fill = managed.protected.entry_fill
        current = self._current_pnl(managed, book)
        return {
            "trade_id": managed.protected.trade_id,
            "candidate_id": plan.candidate_id,
            "symbol": plan.symbol,
            "venue": plan.venue.value,
            "side": plan.direction.value,
            "strategy": plan.strategy_id,
            "regime": plan.regime.value,
            "signal_time": plan.signal_time_ms,
            "planned_entry": str(plan.planned_entry),
            "worst_allowed_entry": str(plan.worst_allowed_entry),
            "actual_entry": str(entry_fill.average_price),
            "take_profit": str(plan.take_profit_targets[0].price),
            "take_profit_1": str(plan.take_profit_targets[0].price),
            "take_profit_2": str(plan.take_profit_targets[1].price)
            if len(plan.take_profit_targets) > 1
            else None,
            "take_profit_targets": [
                {
                    "label": target.label,
                    "price": str(target.price),
                    "quantity_fraction": str(target.quantity_fraction),
                    "remaining_quantity": str(
                        managed.target_remaining.get(target.label, Decimal(0))
                    ),
                }
                for target in plan.take_profit_targets
            ],
            "initial_stop": str(plan.initial_stop),
            "current_stop": str(managed.protected.current_stop),
            "planned_quantity": str(plan.position_size),
            "quantity": str(managed.original_quantity),
            "remaining_quantity": str(managed.remaining_quantity),
            "notional": str(entry_fill.notional),
            "risk_budget": str(plan.risk_budget),
            "maximum_planned_loss": str(plan.max_planned_loss),
            "expected_fees": str(plan.expected_fees_usdt),
            "expected_slippage": str(plan.expected_slippage_usdt),
            "net_reward_risk": str(plan.net_reward_risk),
            "gross_pnl": str(current["gross"]),
            "net_pnl": str(current["net"]),
            "fees": str(current["fees"]),
            "estimated_exit_fee": str(current["estimated_exit_fee"]),
            "slippage": str(current["slippage"]),
            "elapsed_seconds": max(
                0,
                (book.ts_ms if book else plan.signal_time_ms)
                - managed.protected.opened_ts_ms,
            )
            // 1_000,
            "management_reason": (
                "종료 체결 지연 대기 중"
                if managed.pending_exit is not None
                else "고정시간 강제종료 없음 · TP·SL·근거감쇠 관리"
            ),
            "management_policy": list(plan.management_policy),
        }

    def main_summary(self, book: BookSnapshot | None = None) -> dict[str, Decimal | int]:
        realized = sum(
            (trade.net_pnl_usdt for trade in self.main.completed_trades),
            start=Decimal(0),
        )
        fees = sum(
            (trade.fees_usdt for trade in self.main.completed_trades),
            start=Decimal(0),
        )
        slippage = sum(
            (trade.slippage_usdt for trade in self.main.completed_trades),
            start=Decimal(0),
        )
        unrealized = Decimal(0)
        if self.main.position is not None:
            current = self._current_pnl(self.main.position, book)
            unrealized = current["net"]
            fees += current["fees"]
            slippage += current["slippage"]
        return {
            "realized": realized,
            "unrealized": unrealized,
            "fees": fees,
            "slippage": slippage,
            "trade_count": len(self.main.completed_trades),
            "equity": Decimal("1000") + realized + unrealized,
        }

    def shadow_rows(self) -> list[dict[str, object]]:
        rows = self.shadow_ledger.rows()
        for row in rows:
            account_id = self._shadow_account_id(
                str(row["strategy_id"]), CostProfile(str(row["profile"]))
            )
            execution = self.shadows[account_id]
            row["pending_candidate"] = (
                execution.pending_entry.plan.candidate_id
                if execution.pending_entry is not None
                else None
            )
            row["execution_open_position"] = (
                execution.position.protected.trade_id
                if execution.position is not None
                else None
            )
        return rows

    def _advance_entry(self, account: ExecutionAccount, book: BookSnapshot) -> None:
        pending = account.pending_entry
        if pending is None:
            return
        plan = pending.plan
        if book.ts_ms > plan.expires_at_ms:
            account.pending_entry = None
            self._audit("ENTRY_EXPIRED", plan, account_id=account.account_id)
            return
        arrival_ts = plan.signal_time_ms + self.cost_model.arrival_latency_ms(account.profile)
        if book.ts_ms < arrival_ts:
            return
        try:
            result = self.execution_engine.open_position(
                trade_id=f"paper-{plan.candidate_id}-{account.profile.value.lower()}",
                run_id=plan.run_id,
                venue=plan.venue,
                symbol=plan.symbol,
                side=plan.direction,
                requested_quantity=plan.position_size,
                reference_price=plan.planned_entry,
                price_cap=plan.worst_allowed_entry,
                initial_stop=plan.initial_stop,
                take_profit=plan.first_target.price,
                decision_ts_ms=plan.signal_time_ms,
                book_at_arrival=book,
                minimum_quantity=plan.minimum_quantity,
                profile=account.profile,
            )
        except (PaperExecutionError, ValueError) as error:
            account.pending_entry = None
            self._audit(
                "ENTRY_REJECTED",
                plan,
                account_id=account.account_id,
                error_type=type(error).__name__,
            )
            return
        account.entry_orders.append(result.entry_order)
        account.pending_entry = None
        if result.position is None:
            self._audit(
                "ENTRY_UNFILLED",
                plan,
                account_id=account.account_id,
                reason_codes=list(result.entry_order.reason_codes),
            )
            return
        target_remaining = self._target_quantities(
            plan.take_profit_targets,
            result.position.quantity,
            plan.minimum_quantity,
        )
        account.position = ManagedPaperPosition(
            plan=plan,
            protected=result.position,
            original_quantity=result.position.quantity,
            remaining_quantity=result.position.quantity,
            target_remaining=target_remaining,
        )
        self.risk_manager.record_open(account.risk_state)
        if account is not self.main:
            strategy_id = plan.strategy_id
            self.shadow_ledger.open(
                strategy_id,
                account.profile,
                ShadowPosition(
                    shadow_trade_id=result.position.trade_id,
                    symbol=plan.symbol,
                    side=plan.direction,
                    quantity=result.position.quantity,
                    entry_price=result.position.entry_fill.average_price,
                    entry_fee_usdt=result.position.entry_fill.fee_usdt,
                    entry_slippage_usdt=result.position.entry_fill.slippage_usdt,
                    opened_ts_ms=result.position.opened_ts_ms,
                ),
            )
        self._audit(
            "ENTRY_FILLED",
            plan,
            account_id=account.account_id,
            filled_quantity=str(result.position.quantity),
            fill_price=str(result.position.entry_fill.average_price),
            partial=result.position.quantity != plan.position_size,
        )

    def _advance_position(self, account: ExecutionAccount, book: BookSnapshot) -> None:
        managed = account.position
        if managed is None:
            return
        if managed.pending_exit is not None:
            latency = self._exit_latency_ms(managed.pending_exit.reason, account.profile)
            if book.ts_ms >= managed.pending_exit.trigger_ts_ms + latency:
                self._execute_exit(account, managed, book)
            return
        best_executable = (
            book.bids[0][0] if managed.plan.direction is Side.LONG else book.asks[0][0]
        )
        if managed.forced_exit_reason is not None:
            managed.pending_exit = PendingExit(
                managed.forced_exit_reason,
                managed.forced_exit_label or managed.forced_exit_reason.value,
                managed.remaining_quantity,
                best_executable,
                book.ts_ms,
            )
            return
        stop_hit = (
            best_executable <= managed.protected.current_stop
            if managed.plan.direction is Side.LONG
            else best_executable >= managed.protected.current_stop
        )
        target = managed.next_target()
        target_hit = bool(
            target
            and (
                best_executable >= target.price
                if managed.plan.direction is Side.LONG
                else best_executable <= target.price
            )
        )
        if stop_hit:
            managed.forced_exit_reason = ExitReason.STOP
            managed.forced_exit_label = "STOP_LOSS"
            managed.pending_exit = PendingExit(
                ExitReason.STOP,
                "STOP_LOSS",
                managed.remaining_quantity,
                managed.protected.current_stop,
                book.ts_ms,
            )
            return
        if target_hit and target is not None:
            managed.pending_exit = PendingExit(
                ExitReason.TAKE_PROFIT,
                target.label,
                min(
                    managed.remaining_quantity,
                    managed.target_remaining[target.label],
                ),
                target.price,
                book.ts_ms,
            )

    def _execute_exit(
        self,
        account: ExecutionAccount,
        managed: ManagedPaperPosition,
        book: BookSnapshot,
    ) -> None:
        pending = managed.pending_exit
        if pending is None:
            return
        trigger_reference = pending.trigger_reference_price
        if trigger_reference <= 0:
            trigger_reference = (
                book.bids[0][0]
                if managed.plan.direction is Side.LONG
                else book.asks[0][0]
            )
        position = replace(
            managed.protected,
            quantity=managed.remaining_quantity,
            take_profit=(managed.next_target() or managed.plan.first_target).price,
        )
        try:
            result = self.execution_engine.close_position(
                position,
                reason=pending.reason,
                trigger_reference_price=trigger_reference,
                book_at_arrival=book,
                requested_quantity=min(pending.requested_quantity, managed.remaining_quantity),
            )
        except (PaperExecutionError, ValueError) as error:
            managed.pending_exit = None
            self._audit(
                "EXIT_REJECTED",
                managed.plan,
                account_id=account.account_id,
                label=pending.label,
                error_type=type(error).__name__,
            )
            return
        account.exit_orders.append(result.exit_order)
        managed.pending_exit = None
        fill = result.exit_order.fill
        if fill is None:
            self._audit(
                "EXIT_UNFILLED",
                managed.plan,
                account_id=account.account_id,
                label=pending.label,
            )
            return
        managed.exit_legs.append(ExitLeg(pending.label, pending.reason, fill))
        managed.remaining_quantity -= fill.quantity
        if pending.label in managed.target_remaining:
            managed.target_remaining[pending.label] = max(
                Decimal(0), managed.target_remaining[pending.label] - fill.quantity
            )
        self._audit(
            "EXIT_FILL",
            managed.plan,
            account_id=account.account_id,
            label=pending.label,
            filled_quantity=str(fill.quantity),
            remaining_quantity=str(managed.remaining_quantity),
            fill_price=str(fill.average_price),
        )
        if managed.remaining_quantity > 0:
            return
        trade = self._finalize_trade(managed, pending.reason, book.ts_ms)
        account.completed_trades.append(trade)
        self.risk_manager.record_close(
            account.risk_state,
            trade.net_pnl_usdt,
            key=f"{managed.plan.symbol}:{managed.plan.strategy_id}",
            now_ms=book.ts_ms,
        )
        if account is self.main:
            self._new_main_trades.append(trade)
        else:
            exit_quantity = sum(
                (leg.fill.quantity for leg in managed.exit_legs), start=Decimal(0)
            )
            exit_notional = sum(
                (leg.fill.notional for leg in managed.exit_legs), start=Decimal(0)
            )
            self.shadow_ledger.close(
                managed.plan.strategy_id,
                account.profile,
                exit_price=exit_notional / exit_quantity,
                exit_fee_usdt=sum(
                    (leg.fill.fee_usdt for leg in managed.exit_legs), start=Decimal(0)
                ),
                exit_slippage_usdt=sum(
                    (leg.fill.slippage_usdt for leg in managed.exit_legs), start=Decimal(0)
                ),
                closed_ts_ms=book.ts_ms,
                exit_reason=pending.label,
            )
        account.position = None

    def _finalize_trade(
        self,
        managed: ManagedPaperPosition,
        final_reason: ExitReason,
        closed_ts_ms: int,
    ) -> PaperTrade:
        entry = managed.protected.entry_fill
        exit_quantity = sum((leg.fill.quantity for leg in managed.exit_legs), start=Decimal(0))
        exit_notional = sum((leg.fill.notional for leg in managed.exit_legs), start=Decimal(0))
        exit_price = exit_notional / exit_quantity
        direction = Decimal(1) if managed.plan.direction is Side.LONG else Decimal(-1)
        gross = (exit_notional - entry.average_price * exit_quantity) * direction
        fees = entry.fee_usdt + sum(
            (leg.fill.fee_usdt for leg in managed.exit_legs), start=Decimal(0)
        )
        slippage = entry.slippage_usdt + sum(
            (leg.fill.slippage_usdt for leg in managed.exit_legs), start=Decimal(0)
        )
        return PaperTrade(
            trade_id=managed.protected.trade_id,
            run_id=managed.plan.run_id,
            venue=managed.plan.venue,
            symbol=managed.plan.symbol,
            strategy_id=managed.plan.strategy_id,
            side=managed.plan.direction,
            entry_price=entry.average_price,
            exit_price=exit_price,
            quantity=managed.original_quantity,
            initial_stop=managed.plan.initial_stop,
            take_profit=managed.plan.take_profit_targets[-1].price,
            exit_reason=final_reason,
            gross_pnl_usdt=gross,
            fees_usdt=fees,
            slippage_usdt=slippage,
            net_pnl_usdt=gross - fees - slippage,
            holding_ms=max(0, closed_ts_ms - managed.protected.opened_ts_ms),
            flags=tuple(leg.label for leg in managed.exit_legs),
            profile=managed.protected.profile,
        )

    def _current_pnl(
        self,
        managed: ManagedPaperPosition,
        book: BookSnapshot | None,
    ) -> dict[str, Decimal]:
        entry = managed.protected.entry_fill
        direction = Decimal(1) if managed.plan.direction is Side.LONG else Decimal(-1)
        realized_gross = sum(
            (
                (leg.fill.average_price - entry.average_price)
                * leg.fill.quantity
                * direction
                for leg in managed.exit_legs
            ),
            start=Decimal(0),
        )
        mark_gross = Decimal(0)
        estimated_exit_fee = Decimal(0)
        if book is not None and book.symbol == managed.plan.symbol:
            try:
                book.validate()
            except ValueError:
                book = None
        if book is not None:
            mark = book.bids[0][0] if managed.plan.direction is Side.LONG else book.asks[0][0]
            mark_gross = (mark - entry.average_price) * managed.remaining_quantity * direction
            estimated_exit_fee = self.cost_model.fee(
                mark * managed.remaining_quantity,
                entry=False,
                profile=managed.protected.profile,
            )
        fees = entry.fee_usdt + sum(
            (leg.fill.fee_usdt for leg in managed.exit_legs), start=Decimal(0)
        )
        slippage = entry.slippage_usdt + sum(
            (leg.fill.slippage_usdt for leg in managed.exit_legs), start=Decimal(0)
        )
        gross = realized_gross + mark_gross
        return {
            "gross": gross,
            "fees": fees,
            "estimated_exit_fee": estimated_exit_fee,
            "slippage": slippage,
            "net": gross - fees - estimated_exit_fee - slippage,
        }

    def _exit_latency_ms(self, reason: ExitReason, profile: CostProfile) -> int:
        base = (
            self.cost_model.stop_processing_latency_ms
            if reason is ExitReason.STOP
            else self.cost_model.decision_to_arrival_latency_ms
        )
        multiplier = (
            self.cost_model.stress_latency_multiplier
            if profile is CostProfile.STRESS
            else Decimal(1)
        )
        return int(Decimal(base) * multiplier)

    @staticmethod
    def _target_quantities(
        targets: tuple[TakeProfitTarget, ...],
        filled_quantity: Decimal,
        minimum_quantity: Decimal,
    ) -> dict[str, Decimal]:
        if len(targets) == 1:
            return {targets[0].label: filled_quantity}
        first = (
            filled_quantity * targets[0].quantity_fraction / minimum_quantity
        ).to_integral_value(rounding=ROUND_DOWN)
        first *= minimum_quantity
        if first <= 0 or filled_quantity - first <= 0:
            return {targets[0].label: filled_quantity, targets[1].label: Decimal(0)}
        return {
            targets[0].label: first,
            targets[1].label: filled_quantity - first,
        }

    @staticmethod
    def _account_available(account: ExecutionAccount) -> bool:
        return account.pending_entry is None and account.position is None

    @staticmethod
    def _shadow_account_id(strategy_id: str, profile: CostProfile) -> str:
        return f"SHADOW:{strategy_id}:{profile.value}"

    def _audit(self, event: str, plan: CandidatePlan, **payload: object) -> None:
        self.audit_events.append(
            {
                "event": event,
                "candidate_id": plan.candidate_id,
                "run_id": plan.run_id,
                "symbol": plan.symbol,
                "strategy_id": plan.strategy_id,
                "side": plan.direction.value,
                "ts_ms": plan.signal_time_ms,
                **payload,
            }
        )
