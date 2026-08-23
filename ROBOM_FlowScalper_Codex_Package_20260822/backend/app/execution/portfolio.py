"""main·shadow PAPER 계좌에 동일한 지연·호가소진·부분체결 규칙을 적용한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_DOWN, Decimal
from typing import Any

from backend.app.candidates import CandidatePlan, TakeProfitTarget
from backend.app.costing import CostModel, CostProfile
from backend.app.domain.models import Side, Venue
from backend.app.execution.models import (
    BookSnapshot,
    ExitReason,
    Fill,
    OrderIntent,
    OrderStatus,
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
from backend.app.risk import (
    STRATEGY_LEAGUE_RISK_LIMITS,
    RiskManager,
    RiskSizingInput,
    RiskState,
)
from backend.app.strategies.registry import ExitStyle
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
    edge_decay_evaluations: int = 0

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
    pending_entries: dict[str, PendingEntry] = field(default_factory=dict)
    positions: dict[str, ManagedPaperPosition] = field(default_factory=dict)
    completed_trades: list[PaperTrade] = field(default_factory=list)
    entry_orders: list[PaperOrder] = field(default_factory=list)
    exit_orders: list[PaperOrder] = field(default_factory=list)
    max_positions: int = 1

    @property
    def pending_entry(self) -> PendingEntry | None:
        """main 단일 계좌의 기존 읽기 계약만 유지한다."""

        if self.max_positions != 1:
            raise AttributeError("Strategy League 계좌는 pending_entries를 사용해야 합니다.")
        return next(iter(self.pending_entries.values()), None)

    @property
    def position(self) -> ManagedPaperPosition | None:
        """main 단일 계좌의 기존 읽기 계약만 유지한다."""

        if self.max_positions != 1:
            raise AttributeError("Strategy League 계좌는 positions를 사용해야 합니다.")
        return next(iter(self.positions.values()), None)


class PaperPortfolioEngine:
    """main 1개와 전략별 BASE·STRESS 계좌를 독립적으로 진행한다."""

    MAIN_ACCOUNT_ID = "MAIN:BASE"

    def __init__(
        self,
        *,
        run_id: str,
        strategy_ids: tuple[str, ...],
        shadow_ledger: ShadowLedger,
        venue: Venue | None = None,
        execution_engine: PaperExecutionEngine | None = None,
        risk_manager: RiskManager | None = None,
        cost_model: CostModel | None = None,
        position_manager: PositionManager | None = None,
    ) -> None:
        self.run_id = run_id
        self.venue = venue
        self.shadow_ledger = shadow_ledger
        self.execution_engine = execution_engine or PaperExecutionEngine()
        self.risk_manager = risk_manager or RiskManager()
        self.league_risk_manager = RiskManager(STRATEGY_LEAGUE_RISK_LIMITS)
        self.cost_model = cost_model or self.execution_engine.cost_model
        self.position_manager = position_manager or PositionManager()
        self.main = ExecutionAccount(
            self.MAIN_ACCOUNT_ID,
            CostProfile.BASE,
            max_positions=1,
        )
        self.shadows = {
            self._shadow_account_id(strategy_id, profile): ExecutionAccount(
                self._shadow_account_id(strategy_id, profile),
                profile,
                max_positions=3,
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

        valid = tuple(
            plan
            for plan in plans
            if plan.run_id == self.run_id
            and (self.venue is None or plan.venue is self.venue)
        )
        if entries_paused:
            if valid:
                self._audit(
                    "SYSTEM_ENTRY_PAUSED",
                    sorted(valid, key=CandidatePlan.arbitration_key)[0],
                )
            return
        if self._account_available(self.main):
            selected: CandidatePlan | None = None
            eligible = sorted(
                (plan for plan in valid if plan.main_eligible),
                key=CandidatePlan.arbitration_key,
            )
            if eligible:
                selected = self._plan_for_account(eligible[0], self.main)
                if selected is None:
                    self._audit("MAIN_SIZING_REJECTED", eligible[0])
                    selected = None
                if selected is None:
                    eligible = []
            if eligible and selected is not None:
                rejections = self.risk_manager.entry_rejections(
                    self.main.risk_state,
                    f"{selected.symbol}:{selected.strategy_id}",
                    selected.signal_time_ms,
                )
                rejections += self.risk_manager.pending_rejections(
                    self.main.risk_state,
                    planned_risk=selected.max_planned_loss,
                    planned_notional=selected.position_size * selected.worst_allowed_entry,
                )
                if not rejections:
                    self.main.pending_entries[selected.symbol] = PendingEntry(selected)
                    self.risk_manager.reserve_pending(
                        self.main.risk_state,
                        selected.max_planned_loss,
                        selected.position_size * selected.worst_allowed_entry,
                    )
                    self._audit(
                        "MAIN_CANDIDATE_SELECTED",
                        selected,
                        competing_candidate_ids=[plan.candidate_id for plan in eligible[1:]],
                    )
                else:
                    self._audit("MAIN_RISK_REJECTED", selected, reason_codes=list(rejections))

        by_strategy_symbol: dict[tuple[str, str], list[CandidatePlan]] = {}
        for plan in valid:
            if plan.shadow_eligible:
                by_strategy_symbol.setdefault((plan.strategy_id, plan.symbol), []).append(plan)
        selected_by_strategy: dict[str, list[CandidatePlan]] = {}
        for (strategy_id, _), strategy_plans in by_strategy_symbol.items():
            selected_by_strategy.setdefault(strategy_id, []).append(
                sorted(strategy_plans, key=CandidatePlan.arbitration_key)[0]
            )
        for strategy_id, strategy_plans in selected_by_strategy.items():
            for profile in CostProfile:
                account = self.shadows[self._shadow_account_id(strategy_id, profile)]
                for source in sorted(strategy_plans, key=CandidatePlan.arbitration_key):
                    if not self._account_available(account, source.symbol):
                        event = (
                            "LEAGUE_DUPLICATE_SYMBOL_REJECTED"
                            if source.symbol in account.pending_entries
                            or source.symbol in account.positions
                            else "LEAGUE_MAX_POSITIONS_REJECTED"
                        )
                        self._audit(event, source, account_id=account.account_id)
                        continue
                    selected = self._plan_for_account(source, account)
                    if selected is None:
                        self._audit(
                            "LEAGUE_SIZING_REJECTED",
                            source,
                            account_id=account.account_id,
                        )
                        continue
                    rejections = self.league_risk_manager.entry_rejections(
                        account.risk_state,
                        f"{selected.symbol}:{selected.strategy_id}",
                        selected.signal_time_ms,
                    )
                    rejections += self.league_risk_manager.pending_rejections(
                        account.risk_state,
                        planned_risk=selected.max_planned_loss,
                        planned_notional=selected.position_size
                        * selected.worst_allowed_entry,
                    )
                    if rejections:
                        self._audit(
                            "LEAGUE_RISK_REJECTED",
                            selected,
                            account_id=account.account_id,
                            reason_codes=list(rejections),
                        )
                        continue
                    account.pending_entries[selected.symbol] = PendingEntry(selected)
                    self.league_risk_manager.reserve_pending(
                        account.risk_state,
                        selected.max_planned_loss,
                        selected.position_size * selected.worst_allowed_entry,
                    )
                    self._audit(
                        "LEAGUE_CANDIDATE_ARMED",
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
                    "run_id": self.run_id,
                    "symbol": book.symbol,
                    "ts_ms": book.ts_ms,
                }
            )
            return
        for account in self.accounts:
            managed = account.positions.get(book.symbol)
            if managed is not None:
                self._advance_position(account, managed, book)
            pending = account.pending_entries.get(book.symbol)
            if pending is not None:
                self._advance_entry(account, pending, book)

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
        self._audit(
            "MAIN_MANUAL_EXIT_PENDING",
            managed.plan,
            account_id=self.main.account_id,
            reason=reason.value,
        )
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
            managed = account.positions.get(snapshot.symbol)
            if managed is None:
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
            runner_active = managed.target_remaining.get("TP1", Decimal(0)) == 0
            if plan.exit_style is ExitStyle.TREND_40_60 and runner_active:
                trend_broken = (
                    regime is not plan.regime
                    and flow_aligned <= 0
                    and micro_aligned < 0
                )
                managed.edge_decay_evaluations = (
                    managed.edge_decay_evaluations + 1 if trend_broken else 0
                )
                if managed.edge_decay_evaluations >= 2 and managed.pending_exit is None:
                    managed.forced_exit_reason = ExitReason.EDGE_DECAY
                    managed.forced_exit_label = "EDGE_DECAY"
                    managed.pending_exit = PendingExit(
                        reason=ExitReason.EDGE_DECAY,
                        label="EDGE_DECAY",
                        requested_quantity=managed.remaining_quantity,
                        trigger_reference_price=mark,
                        trigger_ts_ms=now_ms,
                    )
                    self._audit(
                        "TREND_EDGE_DECAY_EXIT_ARMED",
                        plan,
                        account_id=account.account_id,
                    )
                    continue
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

    def lifecycle_state(self) -> str:
        """main 계좌의 재시작 지점을 모호하지 않은 한 상태로 축약한다."""

        if self.main.position is not None:
            return "EXIT_PENDING" if self.main.position.pending_exit is not None else "PROTECTED"
        if self.main.pending_entry is not None:
            return "ENTRY_PENDING"
        return "SCANNING"

    def recovery_state(
        self,
        *,
        registry_settings: Sequence[Mapping[str, object]] = (),
        snapshot_ts_ms: int | None = None,
    ) -> dict[str, object]:
        """main·shadow 실행계좌와 shadow 회계를 checksum 가능한 JSON 값으로 만든다."""

        return {
            "schema_version": 2,
            "run_id": self.run_id,
            "venue": self.venue.value if self.venue is not None else None,
            "snapshot_ts_ms": snapshot_ts_ms,
            "strategy_registry": [dict(row) for row in registry_settings],
            "accounts": [
                _execution_account_payload(account)
                for account in sorted(self.accounts, key=lambda item: item.account_id)
            ],
            "shadow_ledger": self.shadow_ledger.recovery_state(),
        }

    def restore_state(self, payload: Mapping[str, object]) -> None:
        """현재 Run·Registry와 정확히 일치하는 검증 snapshot만 복구한다."""

        schema_version = int(str(payload.get("schema_version", 0)))
        if schema_version not in {1, 2}:
            raise ValueError("지원하지 않는 PAPER 복구 snapshot 버전입니다.")
        if str(payload.get("run_id")) != self.run_id:
            raise ValueError("다른 Run의 PAPER 상태를 복구할 수 없습니다.")
        payload_venue = payload.get("venue")
        if (
            payload_venue is not None
            and self.venue is not None
            and Venue(str(payload_venue)) is not self.venue
        ):
            raise ValueError("다른 거래소의 PAPER 상태를 복구할 수 없습니다.")
        account_rows = payload.get("accounts")
        if not isinstance(account_rows, list):
            raise ValueError("PAPER 복구 snapshot에 accounts가 없습니다.")
        expected = {account.account_id: account for account in self.accounts}
        seen: set[str] = set()
        for value in account_rows:
            if not isinstance(value, Mapping):
                raise ValueError("PAPER 복구 계좌 형식이 잘못됐습니다.")
            raw_account_id = str(value.get("account_id"))
            account_id = raw_account_id.removeprefix("SHADOW:")
            if account_id in seen or account_id not in expected:
                raise ValueError(f"PAPER 복구 계좌가 중복되거나 미등록입니다: {account_id}")
            seen.add(account_id)
            _restore_execution_account(expected[account_id], value, run_id=self.run_id)
        if schema_version == 2 and seen != set(expected):
            raise ValueError("PAPER 복구 snapshot 계좌 집합이 Strategy Registry와 다릅니다.")
        if self.MAIN_ACCOUNT_ID not in seen:
            raise ValueError("PAPER 복구 snapshot에 main 계좌가 없습니다.")
        shadow_payload = payload.get("shadow_ledger")
        if not isinstance(shadow_payload, Mapping):
            raise ValueError("PAPER 복구 snapshot에 shadow 원장이 없습니다.")
        self.shadow_ledger.restore_state(
            shadow_payload,
            allow_missing=schema_version == 1,
        )
        self.audit_events = []
        self._new_main_trades = []

    def reconcile_persisted_main_trades(
        self, rows: Sequence[Mapping[str, object]]
    ) -> None:
        """snapshot 직후 crash 창에서 이미 확정된 원장 거래를 최종 진실로 적용한다."""

        trades = [_paper_trade_from_payload(row) for row in rows]
        if len({trade.trade_id for trade in trades}) != len(trades):
            raise ValueError("복구할 main PAPER 거래 ID가 중복됩니다.")
        closed_ids = {trade.trade_id for trade in trades}
        if (
            self.main.position is not None
            and self.main.position.protected.trade_id in closed_ids
        ):
            self.main.positions.clear()
        self.main.completed_trades = trades
        risk = self.main.risk_state
        realized = sum((trade.net_pnl_usdt for trade in trades), start=Decimal(0))
        risk.current_equity = risk.starting_equity + realized
        running = risk.starting_equity
        peak = risk.starting_equity
        for trade in sorted(trades, key=lambda item: (item.closed_ts_ms, item.trade_id)):
            running += trade.net_pnl_usdt
            peak = max(peak, running)
        risk.peak_equity = peak
        risk.realized_today = realized
        risk.realized_week = realized
        risk.daily_trade_count = len(trades) + int(self.main.position is not None)
        risk.open_positions = int(self.main.position is not None)
        risk.open_planned_risk = sum(
            (
                position.plan.max_planned_loss
                * position.original_quantity
                / position.plan.position_size
                for position in self.main.positions.values()
            ),
            start=Decimal(0),
        )
        risk.pending_planned_risk = sum(
            (pending.plan.max_planned_loss for pending in self.main.pending_entries.values()),
            start=Decimal(0),
        )
        risk.gross_notional = sum(
            (position.protected.entry_fill.notional for position in self.main.positions.values()),
            start=Decimal(0),
        )
        risk.pending_notional = sum(
            (
                pending.plan.position_size * pending.plan.worst_allowed_entry
                for pending in self.main.pending_entries.values()
            ),
            start=Decimal(0),
        )
        if risk.current_equity > 0:
            risk.maximum_effective_leverage = max(
                risk.maximum_effective_leverage,
                risk.gross_notional / risk.current_equity,
            )
        risk.global_consecutive_losses = _trailing_losses(trades)

    def shadow_rows(self) -> list[dict[str, object]]:
        rows = self.shadow_ledger.rows()
        for row in rows:
            account_id = self._shadow_account_id(
                str(row["strategy_id"]), CostProfile(str(row["profile"]))
            )
            execution = self.shadows[account_id]
            row["pending_candidate"] = next(
                (
                    pending.plan.candidate_id
                    for pending in execution.pending_entries.values()
                ),
                None,
            )
            row["pending_entries"] = len(execution.pending_entries)
            row["execution_open_position"] = next(
                (
                    position.protected.trade_id
                    for position in execution.positions.values()
                ),
                None,
            )
            row["open_positions"] = len(execution.positions)
        return rows

    def league_account_rows(
        self,
        books: Mapping[str, BookSnapshot] | None = None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for account in self.shadows.values():
            strategy_id = account.account_id.rsplit(":", 1)[0]
            shadow_account = self.shadow_ledger.account(strategy_id, account.profile)
            trades = account.completed_trades
            unrealized = Decimal(0)
            fees = sum((trade.fees_usdt for trade in trades), start=Decimal(0))
            slippage = sum((trade.slippage_usdt for trade in trades), start=Decimal(0))
            for position in account.positions.values():
                current = self._current_pnl(
                    position,
                    books.get(position.plan.symbol) if books is not None else None,
                )
                unrealized += current["net"]
                fees += current["fees"]
                slippage += current["slippage"]
            realized = sum((trade.net_pnl_usdt for trade in trades), start=Decimal(0))
            wins = sum(trade.net_pnl_usdt > 0 for trade in trades)
            losses = sum(trade.net_pnl_usdt < 0 for trade in trades)
            rows.append(
                {
                    "account_id": account.account_id,
                    "strategy_id": strategy_id,
                    "profile": account.profile.value,
                    "starting_equity_usdt": str(account.risk_state.starting_equity),
                    "current_equity_usdt": str(account.risk_state.current_equity + unrealized),
                    "realized_pnl_usdt": str(realized),
                    "unrealized_pnl_usdt": str(unrealized),
                    "fees_usdt": str(fees),
                    "slippage_usdt": str(slippage),
                    "trade_count": len(trades),
                    "wins": wins,
                    "losses": losses,
                    "win_rate": str(Decimal(wins) / len(trades)) if trades else None,
                    "open_positions": len(account.positions),
                    "pending_entries": len(account.pending_entries),
                    "gross_notional_usdt": str(account.risk_state.gross_notional),
                    "effective_leverage": str(
                        account.risk_state.gross_notional
                        / account.risk_state.current_equity
                    )
                    if account.risk_state.current_equity > 0
                    else "0",
                    "maximum_effective_leverage": str(
                        account.risk_state.maximum_effective_leverage
                    ),
                    "maximum_drawdown_usdt": str(
                        shadow_account.maximum_drawdown_usdt
                    ),
                    "paused": account.risk_state.paused,
                    "faulted": account.risk_state.faulted,
                }
            )
        return rows

    def league_position_rows(
        self,
        books: Mapping[str, BookSnapshot] | None = None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for account in self.shadows.values():
            for managed in account.positions.values():
                plan = managed.plan
                current = self._current_pnl(
                    managed,
                    books.get(plan.symbol) if books is not None else None,
                )
                entry = managed.protected.entry_fill.average_price
                notional = entry * managed.remaining_quantity
                current_book = books.get(plan.symbol) if books is not None else None
                mark = entry
                if current_book is not None:
                    try:
                        current_book.validate()
                    except ValueError:
                        current_book = None
                if current_book is not None:
                    mark = (
                        current_book.bids[0][0]
                        if plan.direction is Side.LONG
                        else current_book.asks[0][0]
                    )
                rows.append(
                    {
                        "trade_id": managed.protected.trade_id,
                        "candidate_id": plan.candidate_id,
                        "account_id": account.account_id,
                        "strategy_id": plan.strategy_id,
                        "profile": account.profile.value,
                        "symbol": plan.symbol,
                        "side": plan.direction.value,
                        "signal_time": plan.signal_time_ms,
                        "opened_ts_ms": managed.protected.opened_ts_ms,
                        "actual_entry": str(entry),
                        "current_mark": str(mark),
                        "initial_stop": str(plan.initial_stop),
                        "current_stop": str(managed.protected.current_stop),
                        "TP1": str(plan.take_profit_targets[0].price),
                        "TP2": str(plan.take_profit_targets[1].price),
                        "original_quantity": str(managed.original_quantity),
                        "remaining_quantity": str(managed.remaining_quantity),
                        "notional": str(notional),
                        "effective_leverage": str(
                            notional / account.risk_state.current_equity
                        )
                        if account.risk_state.current_equity > 0
                        else "0",
                        "gross_pnl": str(current["gross"]),
                        "fees": str(current["fees"] + current["estimated_exit_fee"]),
                        "slippage": str(current["slippage"]),
                        "net_pnl": str(current["net"]),
                        "exit_style": plan.exit_style.value,
                        "management_reason": (
                            "종료 체결 지연 대기 중"
                            if managed.pending_exit is not None
                            else "TP·SL·근거감쇠 자동 관리"
                        ),
                        "elapsed_seconds": max(
                            0,
                            (
                                books[plan.symbol].ts_ms
                                if books is not None and plan.symbol in books
                                else plan.signal_time_ms
                            )
                            - managed.protected.opened_ts_ms,
                        )
                        // 1_000,
                    }
                )
        return rows

    def _advance_entry(
        self,
        account: ExecutionAccount,
        pending: PendingEntry,
        book: BookSnapshot,
    ) -> None:
        plan = pending.plan
        if book.ts_ms > plan.expires_at_ms:
            account.pending_entries.pop(plan.symbol, None)
            self._risk_manager_for(account).release_pending(
                account.risk_state,
                plan.max_planned_loss,
                plan.position_size * plan.worst_allowed_entry,
            )
            self._audit("ENTRY_EXPIRED", plan, account_id=account.account_id)
            return
        arrival_ts = plan.signal_time_ms + self.cost_model.arrival_latency_ms(account.profile)
        if book.ts_ms < arrival_ts:
            return
        try:
            result = self.execution_engine.open_position(
                trade_id=(
                    f"paper-{plan.candidate_id}-"
                    f"{account.account_id.lower().replace(':', '-')}"
                ),
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
            account.pending_entries.pop(plan.symbol, None)
            self._risk_manager_for(account).release_pending(
                account.risk_state,
                plan.max_planned_loss,
                plan.position_size * plan.worst_allowed_entry,
            )
            self._audit(
                "ENTRY_REJECTED",
                plan,
                account_id=account.account_id,
                error_type=type(error).__name__,
            )
            return
        account.entry_orders.append(result.entry_order)
        account.pending_entries.pop(plan.symbol, None)
        self._risk_manager_for(account).release_pending(
            account.risk_state,
            plan.max_planned_loss,
            plan.position_size * plan.worst_allowed_entry,
        )
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
        managed = ManagedPaperPosition(
            plan=plan,
            protected=result.position,
            original_quantity=result.position.quantity,
            remaining_quantity=result.position.quantity,
            target_remaining=target_remaining,
        )
        account.positions[plan.symbol] = managed
        actual_planned_risk = (
            plan.max_planned_loss * result.position.quantity / plan.position_size
        )
        actual_notional = result.position.entry_fill.notional
        effective_leverage = actual_notional / account.risk_state.current_equity
        self._risk_manager_for(account).record_open(
            account.risk_state,
            planned_risk=actual_planned_risk,
            notional=actual_notional,
            effective_leverage=effective_leverage,
        )
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

    def _advance_position(
        self,
        account: ExecutionAccount,
        managed: ManagedPaperPosition,
        book: BookSnapshot,
    ) -> None:
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
            self._audit(
                "FORCED_EXIT_PENDING",
                managed.plan,
                account_id=account.account_id,
                reason=managed.forced_exit_reason.value,
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
            self._audit(
                "STOP_EXIT_PENDING",
                managed.plan,
                account_id=account.account_id,
                trigger_price=str(managed.protected.current_stop),
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
            self._audit(
                "TAKE_PROFIT_EXIT_PENDING",
                managed.plan,
                account_id=account.account_id,
                label=target.label,
                trigger_price=str(target.price),
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
                decision_ts_ms=pending.trigger_ts_ms,
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
        if (
            pending.label == "TP1"
            and managed.plan.exit_style is ExitStyle.TREND_40_60
            and managed.remaining_quantity > 0
        ):
            fee_bps = self.cost_model.fee_bps(
                entry=True,
                profile=account.profile,
            ) + self.cost_model.fee_bps(entry=False, profile=account.profile)
            adjustment = (
                managed.protected.entry_fill.average_price * fee_bps / Decimal(10_000)
            )
            proposed_stop = (
                managed.protected.entry_fill.average_price + adjustment
                if managed.plan.direction is Side.LONG
                else managed.protected.entry_fill.average_price - adjustment
            )
            proposed_stop = (
                max(managed.protected.current_stop, proposed_stop)
                if managed.plan.direction is Side.LONG
                else min(managed.protected.current_stop, proposed_stop)
            )
            managed.protected = self.position_manager.tighten_stop(
                managed.protected,
                proposed_stop,
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
        self._risk_manager_for(account).record_close(
            account.risk_state,
            trade.net_pnl_usdt,
            key=f"{managed.plan.symbol}:{managed.plan.strategy_id}",
            now_ms=book.ts_ms,
            planned_risk=(
                managed.plan.max_planned_loss
                * managed.original_quantity
                / managed.plan.position_size
            ),
            notional=managed.protected.entry_fill.notional,
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
                shadow_trade_id=managed.protected.trade_id,
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
        account.positions.pop(managed.plan.symbol, None)

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
            opened_ts_ms=managed.protected.opened_ts_ms,
            closed_ts_ms=closed_ts_ms,
            holding_ms=max(0, closed_ts_ms - managed.protected.opened_ts_ms),
            regime=managed.plan.regime.value,
            mae_r=managed.mae_r,
            mfe_r=managed.mfe_r,
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
    def _account_available(
        account: ExecutionAccount,
        symbol: str | None = None,
    ) -> bool:
        if symbol is not None and (
            symbol in account.pending_entries or symbol in account.positions
        ):
            return False
        return len(account.pending_entries) + len(account.positions) < account.max_positions

    @staticmethod
    def _shadow_account_id(strategy_id: str, profile: CostProfile) -> str:
        return f"{strategy_id}:{profile.value}"

    def _risk_manager_for(self, account: ExecutionAccount) -> RiskManager:
        return self.risk_manager if account is self.main else self.league_risk_manager

    def _plan_for_account(
        self,
        plan: CandidatePlan,
        account: ExecutionAccount,
    ) -> CandidatePlan | None:
        manager = self._risk_manager_for(account)
        slippage_multiplier = Decimal(2) if account.profile is CostProfile.STRESS else Decimal(1)
        entry_fee_per_unit = (
            plan.worst_allowed_entry
            * self.cost_model.fee_bps(entry=True, profile=account.profile)
            / Decimal(10_000)
        )
        stop_fee_per_unit = (
            plan.initial_stop
            * self.cost_model.fee_bps(entry=False, profile=account.profile)
            / Decimal(10_000)
        )
        sizing = manager.size(
            RiskSizingInput(
                equity=account.risk_state.current_equity,
                entry_price=plan.worst_allowed_entry,
                stop_price=plan.initial_stop,
                entry_fee_per_unit=entry_fee_per_unit,
                stop_fee_per_unit=stop_fee_per_unit,
                p95_exit_slippage_per_unit=plan.noise_buffer * slippage_multiplier,
                quantity_step=plan.quantity_step,
                minimum_quantity=plan.minimum_quantity,
                executable_depth_quantity=plan.executable_depth_quantity,
            )
        )
        if sizing.quantity is None or sizing.planned_loss is None:
            return None
        quantity = sizing.quantity
        weighted_reward_per_unit = sum(
            (
                abs(target.price - plan.planned_entry) * target.quantity_fraction
                for target in plan.take_profit_targets
            ),
            start=Decimal(0),
        )
        gross_reward = weighted_reward_per_unit * quantity
        weighted_exit = sum(
            (target.price * target.quantity_fraction for target in plan.take_profit_targets),
            start=Decimal(0),
        )
        expected_fees = quantity * (
            entry_fee_per_unit
            + weighted_exit
            * self.cost_model.fee_bps(entry=False, profile=account.profile)
            / Decimal(10_000)
        )
        expected_slippage = (
            quantity * plan.noise_buffer * Decimal("1.5") * slippage_multiplier
        )
        net_reward = gross_reward - expected_fees - expected_slippage
        if net_reward <= 0 or sizing.planned_loss <= 0:
            return None
        net_rr = (net_reward / sizing.planned_loss).quantize(Decimal("0.0001"))
        effective_leverage = (
            quantity * plan.worst_allowed_entry / account.risk_state.current_equity
        )
        if effective_leverage > manager.limits.maximum_gross_notional_fraction:
            return None
        return replace(
            plan,
            position_size=quantity,
            risk_budget=sizing.risk_budget,
            max_planned_loss=sizing.planned_loss,
            gross_reward_usdt=gross_reward,
            expected_fees_usdt=expected_fees,
            expected_slippage_usdt=expected_slippage,
            net_reward_usdt=net_reward,
            net_risk_usdt=sizing.planned_loss,
            net_reward_risk=net_rr,
            cost_burden=((expected_fees + expected_slippage) / gross_reward).quantize(
                Decimal("0.0001")
            ),
        )

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


def _execution_account_payload(account: ExecutionAccount) -> dict[str, object]:
    return {
        "account_id": account.account_id,
        "profile": account.profile.value,
        "max_positions": account.max_positions,
        "risk_state": _risk_state_payload(account.risk_state),
        "pending_entries": {
            symbol: _candidate_plan_payload(pending.plan)
            for symbol, pending in account.pending_entries.items()
        },
        "positions": {
            symbol: _managed_position_payload(position)
            for symbol, position in account.positions.items()
        },
        "completed_trades": [
            _paper_trade_payload(trade) for trade in account.completed_trades
        ],
        "entry_orders": [_paper_order_payload(order) for order in account.entry_orders],
        "exit_orders": [_paper_order_payload(order) for order in account.exit_orders],
    }


def _restore_execution_account(
    account: ExecutionAccount,
    payload: Mapping[str, object],
    *,
    run_id: str,
) -> None:
    if CostProfile(str(payload.get("profile"))) is not account.profile:
        raise ValueError(f"복구 계좌 비용 프로필 불일치: {account.account_id}")
    risk_payload = payload.get("risk_state")
    if not isinstance(risk_payload, Mapping):
        raise ValueError("복구 계좌에 위험 상태가 없습니다.")
    account.risk_state = _risk_state_from_payload(risk_payload)
    max_positions = int(str(payload.get("max_positions", account.max_positions)))
    if max_positions != account.max_positions:
        raise ValueError("복구 계좌의 최대 포지션 계약이 다릅니다.")
    pending_rows = payload.get("pending_entries")
    if isinstance(pending_rows, Mapping):
        account.pending_entries = {
            str(symbol): PendingEntry(_candidate_plan_from_payload(value))
            for symbol, value in pending_rows.items()
            if isinstance(value, Mapping)
        }
        if len(account.pending_entries) != len(pending_rows):
            raise ValueError("복구 대기 진입 map 형식이 잘못됐습니다.")
    else:
        pending = payload.get("pending_entry")
        restored_pending = (
            PendingEntry(_candidate_plan_from_payload(pending))
            if isinstance(pending, Mapping)
            else None
        )
        account.pending_entries = (
            {restored_pending.plan.symbol: restored_pending}
            if restored_pending is not None
            else {}
        )
    position_rows = payload.get("positions")
    if isinstance(position_rows, Mapping):
        account.positions = {
            str(symbol): _managed_position_from_payload(value)
            for symbol, value in position_rows.items()
            if isinstance(value, Mapping)
        }
        if len(account.positions) != len(position_rows):
            raise ValueError("복구 포지션 map 형식이 잘못됐습니다.")
    else:
        position = payload.get("position")
        restored_position = (
            _managed_position_from_payload(position)
            if isinstance(position, Mapping)
            else None
        )
        account.positions = (
            {restored_position.plan.symbol: restored_position}
            if restored_position is not None
            else {}
        )
    completed = payload.get("completed_trades")
    entries = payload.get("entry_orders")
    exits = payload.get("exit_orders")
    if (
        not isinstance(completed, list)
        or not isinstance(entries, list)
        or not isinstance(exits, list)
    ):
        raise ValueError("복구 계좌의 거래·주문 목록 형식이 잘못됐습니다.")
    account.completed_trades = [
        _paper_trade_from_payload(value)
        for value in completed
        if isinstance(value, Mapping)
    ]
    account.entry_orders = [
        _paper_order_from_payload(value) for value in entries if isinstance(value, Mapping)
    ]
    account.exit_orders = [
        _paper_order_from_payload(value) for value in exits if isinstance(value, Mapping)
    ]
    if (
        len(account.completed_trades) != len(completed)
        or len(account.entry_orders) != len(entries)
        or len(account.exit_orders) != len(exits)
    ):
        raise ValueError("복구 계좌의 거래·주문 행 형식이 잘못됐습니다.")
    plans = [
        *(pending.plan for pending in account.pending_entries.values()),
        *(position.plan for position in account.positions.values()),
    ]
    if any(plan.run_id != run_id for plan in plans):
        raise ValueError("복구 계좌에 다른 Run의 계획이 포함됐습니다.")
    if any(trade.run_id != run_id for trade in account.completed_trades):
        raise ValueError("복구 계좌에 다른 Run의 거래가 포함됐습니다.")
    if "open_planned_risk" not in risk_payload:
        account.risk_state.open_planned_risk = sum(
            (
                position.plan.max_planned_loss
                * position.original_quantity
                / position.plan.position_size
                for position in account.positions.values()
            ),
            start=Decimal(0),
        )
    if "pending_planned_risk" not in risk_payload:
        account.risk_state.pending_planned_risk = sum(
            (pending.plan.max_planned_loss for pending in account.pending_entries.values()),
            start=Decimal(0),
        )
    if "gross_notional" not in risk_payload:
        account.risk_state.gross_notional = sum(
            (
                position.protected.entry_fill.notional
                for position in account.positions.values()
            ),
            start=Decimal(0),
        )
    if "pending_notional" not in risk_payload:
        account.risk_state.pending_notional = sum(
            (
                pending.plan.position_size * pending.plan.worst_allowed_entry
                for pending in account.pending_entries.values()
            ),
            start=Decimal(0),
        )
    if "maximum_effective_leverage" not in risk_payload:
        account.risk_state.maximum_effective_leverage = (
            account.risk_state.gross_notional / account.risk_state.current_equity
            if account.risk_state.current_equity > 0
            else Decimal(0)
        )
    expected_open = len(account.positions)
    if account.risk_state.open_positions != expected_open:
        raise ValueError("복구 계좌의 포지션 수와 위험 상태가 일치하지 않습니다.")
    if len(account.positions) > account.max_positions:
        raise ValueError("복구 계좌의 포지션 수가 상한을 넘습니다.")
    if len(account.positions) + len(account.pending_entries) > account.max_positions:
        raise ValueError("복구 계좌의 포지션·대기 진입 합이 상한을 넘습니다.")
    if set(account.pending_entries) & set(account.positions):
        raise ValueError("동일 종목에 대기 진입과 열린 포지션을 함께 복구할 수 없습니다.")


def _risk_state_payload(state: RiskState) -> dict[str, object]:
    return {
        "starting_equity": str(state.starting_equity),
        "current_equity": str(state.current_equity),
        "peak_equity": str(state.peak_equity),
        "realized_today": str(state.realized_today),
        "realized_week": str(state.realized_week),
        "daily_trade_count": state.daily_trade_count,
        "open_positions": state.open_positions,
        "open_planned_risk": str(state.open_planned_risk),
        "pending_planned_risk": str(state.pending_planned_risk),
        "gross_notional": str(state.gross_notional),
        "pending_notional": str(state.pending_notional),
        "maximum_effective_leverage": str(state.maximum_effective_leverage),
        "global_consecutive_losses": state.global_consecutive_losses,
        "paused": state.paused,
        "faulted": state.faulted,
        "cooldowns_until_ms": dict(state.cooldowns_until_ms),
    }


def _risk_state_from_payload(payload: Mapping[str, object]) -> RiskState:
    cooldowns = payload.get("cooldowns_until_ms", {})
    if not isinstance(cooldowns, Mapping):
        raise ValueError("복구 위험상태 cooldown 형식이 잘못됐습니다.")
    state = RiskState(
        starting_equity=Decimal(str(payload["starting_equity"])),
        current_equity=Decimal(str(payload["current_equity"])),
        peak_equity=Decimal(str(payload["peak_equity"])),
        realized_today=Decimal(str(payload["realized_today"])),
        realized_week=Decimal(str(payload["realized_week"])),
        daily_trade_count=int(str(payload["daily_trade_count"])),
        open_positions=int(str(payload["open_positions"])),
        open_planned_risk=Decimal(str(payload.get("open_planned_risk", "0"))),
        pending_planned_risk=Decimal(str(payload.get("pending_planned_risk", "0"))),
        gross_notional=Decimal(str(payload.get("gross_notional", "0"))),
        pending_notional=Decimal(str(payload.get("pending_notional", "0"))),
        maximum_effective_leverage=Decimal(
            str(payload.get("maximum_effective_leverage", "0"))
        ),
        global_consecutive_losses=int(str(payload["global_consecutive_losses"])),
        paused=bool(payload["paused"]),
        faulted=bool(payload["faulted"]),
        cooldowns_until_ms={str(key): int(str(value)) for key, value in cooldowns.items()},
    )
    if state.current_equity > state.peak_equity or state.starting_equity <= 0:
        raise ValueError("복구 위험상태의 자산 불변조건이 잘못됐습니다.")
    return state


def _managed_position_payload(position: ManagedPaperPosition) -> dict[str, object]:
    return {
        "plan": _candidate_plan_payload(position.plan),
        "protected": _protected_position_payload(position.protected),
        "original_quantity": str(position.original_quantity),
        "remaining_quantity": str(position.remaining_quantity),
        "target_remaining": {
            key: str(value) for key, value in position.target_remaining.items()
        },
        "exit_legs": [_exit_leg_payload(leg) for leg in position.exit_legs],
        "pending_exit": _pending_exit_payload(position.pending_exit)
        if position.pending_exit is not None
        else None,
        "mfe_r": str(position.mfe_r),
        "mae_r": str(position.mae_r),
        "forced_exit_reason": position.forced_exit_reason.value
        if position.forced_exit_reason is not None
        else None,
        "forced_exit_label": position.forced_exit_label,
        "edge_decay_evaluations": position.edge_decay_evaluations,
    }


def _managed_position_from_payload(payload: Mapping[str, object]) -> ManagedPaperPosition:
    plan_payload = payload.get("plan")
    protected_payload = payload.get("protected")
    remaining_payload = payload.get("target_remaining")
    leg_rows = payload.get("exit_legs")
    if (
        not isinstance(plan_payload, Mapping)
        or not isinstance(protected_payload, Mapping)
        or not isinstance(remaining_payload, Mapping)
        or not isinstance(leg_rows, list)
    ):
        raise ValueError("복구 포지션 payload 형식이 잘못됐습니다.")
    plan = _candidate_plan_from_payload(plan_payload)
    protected = _protected_position_from_payload(protected_payload)
    original = Decimal(str(payload["original_quantity"]))
    remaining = Decimal(str(payload["remaining_quantity"]))
    if protected.run_id != plan.run_id or protected.symbol != plan.symbol:
        raise ValueError("복구 포지션의 계획과 보호 주문이 일치하지 않습니다.")
    if not Decimal(0) < remaining <= original or protected.quantity != original:
        raise ValueError("복구 포지션의 수량 불변조건이 잘못됐습니다.")
    pending = payload.get("pending_exit")
    position = ManagedPaperPosition(
        plan=plan,
        protected=protected,
        original_quantity=original,
        remaining_quantity=remaining,
        target_remaining={
            str(key): Decimal(str(value)) for key, value in remaining_payload.items()
        },
        exit_legs=[
            _exit_leg_from_payload(value) for value in leg_rows if isinstance(value, Mapping)
        ],
        pending_exit=_pending_exit_from_payload(pending)
        if isinstance(pending, Mapping)
        else None,
        mfe_r=Decimal(str(payload["mfe_r"])),
        mae_r=Decimal(str(payload["mae_r"])),
        forced_exit_reason=ExitReason(str(payload["forced_exit_reason"]))
        if payload.get("forced_exit_reason") is not None
        else None,
        forced_exit_label=str(payload["forced_exit_label"])
        if payload.get("forced_exit_label") is not None
        else None,
        edge_decay_evaluations=int(str(payload.get("edge_decay_evaluations", 0))),
    )
    if len(position.exit_legs) != len(leg_rows):
        raise ValueError("복구 포지션의 exit leg 형식이 잘못됐습니다.")
    if (
        position.pending_exit is not None
        and position.pending_exit.requested_quantity > remaining
    ):
        raise ValueError("복구 포지션의 대기 청산 수량이 잔여 수량을 넘습니다.")
    return position


def _candidate_plan_payload(plan: CandidatePlan) -> dict[str, object]:
    return {
        "candidate_id": plan.candidate_id,
        "signal_event_id": plan.signal_event_id,
        "run_id": plan.run_id,
        "venue": plan.venue.value,
        "symbol": plan.symbol,
        "strategy_id": plan.strategy_id,
        "strategy_version": plan.strategy_version,
        "exit_style": plan.exit_style.value,
        "direction": plan.direction.value,
        "signal_time_ms": plan.signal_time_ms,
        "expires_at_ms": plan.expires_at_ms,
        "regime": plan.regime.value,
        "planned_entry": str(plan.planned_entry),
        "worst_allowed_entry": str(plan.worst_allowed_entry),
        "initial_stop": str(plan.initial_stop),
        "noise_buffer": str(plan.noise_buffer),
        "take_profit_targets": [
            {
                "label": target.label,
                "price": str(target.price),
                "quantity_fraction": str(target.quantity_fraction),
            }
            for target in plan.take_profit_targets
        ],
        "position_size": str(plan.position_size),
        "quantity_step": str(plan.quantity_step),
        "minimum_quantity": str(plan.minimum_quantity),
        "executable_depth_quantity": str(plan.executable_depth_quantity),
        "risk_budget": str(plan.risk_budget),
        "max_planned_loss": str(plan.max_planned_loss),
        "gross_reward_usdt": str(plan.gross_reward_usdt),
        "expected_fees_usdt": str(plan.expected_fees_usdt),
        "expected_slippage_usdt": str(plan.expected_slippage_usdt),
        "net_reward_usdt": str(plan.net_reward_usdt),
        "net_risk_usdt": str(plan.net_risk_usdt),
        "net_reward_risk": str(plan.net_reward_risk),
        "data_quality": str(plan.data_quality),
        "signal_quality": str(plan.signal_quality),
        "liquidity_quality": str(plan.liquidity_quality),
        "cost_burden": str(plan.cost_burden),
        "reason_codes": list(plan.reason_codes),
        "plain_korean_explanation": list(plan.plain_korean_explanation),
        "management_policy": list(plan.management_policy),
        "main_eligible": plan.main_eligible,
        "shadow_eligible": plan.shadow_eligible,
    }


def _candidate_plan_from_payload(payload: Mapping[str, object]) -> CandidatePlan:
    target_rows = payload.get("take_profit_targets")
    if not isinstance(target_rows, list):
        raise ValueError("복구 계획의 TP 목록 형식이 잘못됐습니다.")
    targets = tuple(
        TakeProfitTarget(
            label=str(value["label"]),
            price=Decimal(str(value["price"])),
            quantity_fraction=Decimal(str(value["quantity_fraction"])),
        )
        for value in target_rows
        if isinstance(value, Mapping)
    )
    if len(targets) != len(target_rows):
        raise ValueError("복구 계획의 TP 행 형식이 잘못됐습니다.")
    return CandidatePlan(
        candidate_id=str(payload["candidate_id"]),
        signal_event_id=str(payload["signal_event_id"]),
        run_id=str(payload["run_id"]),
        venue=Venue(str(payload["venue"])),
        symbol=str(payload["symbol"]),
        strategy_id=str(payload["strategy_id"]),
        strategy_version=str(payload["strategy_version"]),
        exit_style=ExitStyle(
            str(
                payload.get(
                    "exit_style",
                    ExitStyle.REVERSION_70_30.value
                    if str(payload["strategy_id"])
                    in {"LSA_REVERSAL_V1", "VWAP_EXHAUSTION_REVERSION_V1"}
                    else ExitStyle.TREND_40_60.value,
                )
            )
        ),
        direction=Side(str(payload["direction"])),
        signal_time_ms=int(str(payload["signal_time_ms"])),
        expires_at_ms=int(str(payload["expires_at_ms"])),
        regime=Regime(str(payload["regime"])),
        planned_entry=Decimal(str(payload["planned_entry"])),
        worst_allowed_entry=Decimal(str(payload["worst_allowed_entry"])),
        initial_stop=Decimal(str(payload["initial_stop"])),
        noise_buffer=Decimal(str(payload["noise_buffer"])),
        take_profit_targets=targets,
        position_size=Decimal(str(payload["position_size"])),
        quantity_step=Decimal(str(payload.get("quantity_step", payload["minimum_quantity"]))),
        minimum_quantity=Decimal(str(payload["minimum_quantity"])),
        executable_depth_quantity=Decimal(
            str(payload.get("executable_depth_quantity", payload["position_size"]))
        ),
        risk_budget=Decimal(str(payload["risk_budget"])),
        max_planned_loss=Decimal(str(payload["max_planned_loss"])),
        gross_reward_usdt=Decimal(str(payload["gross_reward_usdt"])),
        expected_fees_usdt=Decimal(str(payload["expected_fees_usdt"])),
        expected_slippage_usdt=Decimal(str(payload["expected_slippage_usdt"])),
        net_reward_usdt=Decimal(str(payload["net_reward_usdt"])),
        net_risk_usdt=Decimal(str(payload["net_risk_usdt"])),
        net_reward_risk=Decimal(str(payload["net_reward_risk"])),
        data_quality=Decimal(str(payload["data_quality"])),
        signal_quality=Decimal(str(payload["signal_quality"])),
        liquidity_quality=Decimal(str(payload["liquidity_quality"])),
        cost_burden=Decimal(str(payload["cost_burden"])),
        reason_codes=_strings(payload, "reason_codes"),
        plain_korean_explanation=_strings(payload, "plain_korean_explanation"),
        management_policy=_strings(payload, "management_policy"),
        main_eligible=bool(payload["main_eligible"]),
        shadow_eligible=bool(payload["shadow_eligible"]),
    )


def _protected_position_payload(position: ProtectedPosition) -> dict[str, object]:
    return {
        "trade_id": position.trade_id,
        "run_id": position.run_id,
        "venue": position.venue.value,
        "symbol": position.symbol,
        "side": position.side.value,
        "quantity": str(position.quantity),
        "entry_reference_price": str(position.entry_reference_price),
        "entry_fill": _fill_payload(position.entry_fill),
        "initial_stop": str(position.initial_stop),
        "current_stop": str(position.current_stop),
        "take_profit": str(position.take_profit),
        "protection_orders": [
            _paper_order_payload(order) for order in position.protection_orders
        ],
        "opened_ts_ms": position.opened_ts_ms,
        "profile": position.profile.value,
    }


def _protected_position_from_payload(payload: Mapping[str, object]) -> ProtectedPosition:
    fill_payload = payload.get("entry_fill")
    order_rows = payload.get("protection_orders")
    if not isinstance(fill_payload, Mapping) or not isinstance(order_rows, list):
        raise ValueError("복구 보호 포지션 형식이 잘못됐습니다.")
    orders = tuple(
        _paper_order_from_payload(value)
        for value in order_rows
        if isinstance(value, Mapping)
    )
    if len(orders) != 2 or len(order_rows) != 2:
        raise ValueError("복구 보호 포지션에는 TP와 SL 두 주문이 필요합니다.")
    return ProtectedPosition(
        trade_id=str(payload["trade_id"]),
        run_id=str(payload["run_id"]),
        venue=Venue(str(payload["venue"])),
        symbol=str(payload["symbol"]),
        side=Side(str(payload["side"])),
        quantity=Decimal(str(payload["quantity"])),
        entry_reference_price=Decimal(str(payload["entry_reference_price"])),
        entry_fill=_fill_from_payload(fill_payload),
        initial_stop=Decimal(str(payload["initial_stop"])),
        current_stop=Decimal(str(payload["current_stop"])),
        take_profit=Decimal(str(payload["take_profit"])),
        protection_orders=(orders[0], orders[1]),
        opened_ts_ms=int(str(payload["opened_ts_ms"])),
        profile=CostProfile(str(payload["profile"])),
    )


def _paper_order_payload(order: PaperOrder) -> dict[str, object]:
    return {
        "order_id": order.order_id,
        "trade_id": order.trade_id,
        "run_id": order.run_id,
        "venue": order.venue.value,
        "symbol": order.symbol,
        "side": order.side,
        "intent": order.intent.value,
        "status": order.status.value,
        "requested_quantity": str(order.requested_quantity),
        "filled_quantity": str(order.filled_quantity),
        "price_cap": str(order.price_cap) if order.price_cap is not None else None,
        "trigger_price": str(order.trigger_price) if order.trigger_price is not None else None,
        "fill": _fill_payload(order.fill) if order.fill is not None else None,
        "created_ts_ms": order.created_ts_ms,
        "arrival_ts_ms": order.arrival_ts_ms,
        "reason_codes": list(order.reason_codes),
    }


def _paper_order_from_payload(payload: Mapping[str, object]) -> PaperOrder:
    fill = payload.get("fill")
    return PaperOrder(
        order_id=str(payload["order_id"]),
        trade_id=str(payload["trade_id"]),
        run_id=str(payload["run_id"]),
        venue=Venue(str(payload["venue"])),
        symbol=str(payload["symbol"]),
        side=str(payload["side"]),
        intent=OrderIntent(str(payload["intent"])),
        status=OrderStatus(str(payload["status"])),
        requested_quantity=Decimal(str(payload["requested_quantity"])),
        filled_quantity=Decimal(str(payload["filled_quantity"])),
        price_cap=Decimal(str(payload["price_cap"]))
        if payload.get("price_cap") is not None
        else None,
        trigger_price=Decimal(str(payload["trigger_price"]))
        if payload.get("trigger_price") is not None
        else None,
        fill=_fill_from_payload(fill) if isinstance(fill, Mapping) else None,
        created_ts_ms=int(str(payload["created_ts_ms"])),
        arrival_ts_ms=int(str(payload["arrival_ts_ms"]))
        if payload.get("arrival_ts_ms") is not None
        else None,
        reason_codes=_strings(payload, "reason_codes"),
    )


def _fill_payload(fill: Fill) -> dict[str, object]:
    return {
        "quantity": str(fill.quantity),
        "average_price": str(fill.average_price),
        "notional": str(fill.notional),
        "fee_usdt": str(fill.fee_usdt),
        "slippage_usdt": str(fill.slippage_usdt),
        "levels_consumed": fill.levels_consumed,
        "book_ts_ms": fill.book_ts_ms,
    }


def _fill_from_payload(payload: Mapping[str, object]) -> Fill:
    return Fill(
        quantity=Decimal(str(payload["quantity"])),
        average_price=Decimal(str(payload["average_price"])),
        notional=Decimal(str(payload["notional"])),
        fee_usdt=Decimal(str(payload["fee_usdt"])),
        slippage_usdt=Decimal(str(payload["slippage_usdt"])),
        levels_consumed=int(str(payload["levels_consumed"])),
        book_ts_ms=int(str(payload["book_ts_ms"])),
    )


def _pending_exit_payload(pending: PendingExit) -> dict[str, object]:
    return {
        "reason": pending.reason.value,
        "label": pending.label,
        "requested_quantity": str(pending.requested_quantity),
        "trigger_reference_price": str(pending.trigger_reference_price),
        "trigger_ts_ms": pending.trigger_ts_ms,
    }


def _pending_exit_from_payload(payload: Mapping[str, object]) -> PendingExit:
    return PendingExit(
        reason=ExitReason(str(payload["reason"])),
        label=str(payload["label"]),
        requested_quantity=Decimal(str(payload["requested_quantity"])),
        trigger_reference_price=Decimal(str(payload["trigger_reference_price"])),
        trigger_ts_ms=int(str(payload["trigger_ts_ms"])),
    )


def _exit_leg_payload(leg: ExitLeg) -> dict[str, object]:
    return {
        "label": leg.label,
        "reason": leg.reason.value,
        "fill": _fill_payload(leg.fill),
    }


def _exit_leg_from_payload(payload: Mapping[str, object]) -> ExitLeg:
    fill = payload.get("fill")
    if not isinstance(fill, Mapping):
        raise ValueError("복구 exit leg에 fill이 없습니다.")
    return ExitLeg(
        label=str(payload["label"]),
        reason=ExitReason(str(payload["reason"])),
        fill=_fill_from_payload(fill),
    )


def _paper_trade_payload(trade: PaperTrade) -> dict[str, object]:
    return {
        "trade_id": trade.trade_id,
        "run_id": trade.run_id,
        "venue": trade.venue.value,
        "symbol": trade.symbol,
        "strategy_id": trade.strategy_id,
        "side": trade.side.value,
        "entry_price": str(trade.entry_price),
        "exit_price": str(trade.exit_price),
        "quantity": str(trade.quantity),
        "initial_stop": str(trade.initial_stop),
        "take_profit": str(trade.take_profit),
        "exit_reason": trade.exit_reason.value,
        "gross_pnl_usdt": str(trade.gross_pnl_usdt),
        "fees_usdt": str(trade.fees_usdt),
        "slippage_usdt": str(trade.slippage_usdt),
        "net_pnl_usdt": str(trade.net_pnl_usdt),
        "opened_ts_ms": trade.opened_ts_ms,
        "closed_ts_ms": trade.closed_ts_ms,
        "holding_ms": trade.holding_ms,
        "regime": trade.regime,
        "mae_r": str(trade.mae_r),
        "mfe_r": str(trade.mfe_r),
        "flags": list(trade.flags),
        "profile": trade.profile.value,
    }


def _paper_trade_from_payload(payload: Mapping[str, object]) -> PaperTrade:
    opened = payload.get("opened_ts_ms", payload.get("entry_ts_ms"))
    closed = payload.get("closed_ts_ms", payload.get("exit_ts_ms"))
    if opened is None or closed is None:
        raise ValueError("복구 거래의 진입·종료 시각이 없습니다.")
    return PaperTrade(
        trade_id=str(payload["trade_id"]),
        run_id=str(payload["run_id"]),
        venue=Venue(str(payload["venue"])),
        symbol=str(payload["symbol"]),
        strategy_id=str(payload["strategy_id"]),
        side=Side(str(payload["side"])),
        entry_price=Decimal(str(payload["entry_price"])),
        exit_price=Decimal(str(payload["exit_price"])),
        quantity=Decimal(str(payload["quantity"])),
        initial_stop=Decimal(str(payload["initial_stop"])),
        take_profit=Decimal(str(payload["take_profit"])),
        exit_reason=ExitReason(str(payload["exit_reason"])),
        gross_pnl_usdt=Decimal(str(payload["gross_pnl_usdt"])),
        fees_usdt=Decimal(str(payload["fees_usdt"])),
        slippage_usdt=Decimal(str(payload["slippage_usdt"])),
        net_pnl_usdt=Decimal(str(payload["net_pnl_usdt"])),
        opened_ts_ms=int(str(opened)),
        closed_ts_ms=int(str(closed)),
        holding_ms=int(str(payload["holding_ms"])),
        regime=str(payload["regime"]),
        mae_r=Decimal(str(payload["mae_r"])),
        mfe_r=Decimal(str(payload["mfe_r"])),
        flags=_strings(payload, "flags"),
        profile=CostProfile(str(payload.get("profile", CostProfile.BASE.value))),
    )


def _trailing_losses(trades: list[PaperTrade]) -> int:
    count = 0
    for trade in sorted(trades, key=lambda item: (item.closed_ts_ms, item.trade_id), reverse=True):
        if trade.net_pnl_usdt >= 0:
            break
        count += 1
    return count


def _strings(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    values = payload.get(key, [])
    if not isinstance(values, list | tuple):
        raise ValueError(f"복구 payload의 {key} 형식이 잘못됐습니다.")
    return tuple(str(value) for value in values)
