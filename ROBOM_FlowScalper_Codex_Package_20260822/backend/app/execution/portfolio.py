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
from backend.app.execution.trailing import (
    TrailingActivationNotFeeSafeError,
    TrailingActivationRule,
    TrailingDecision,
    TrailingModel,
    TrailingObservation,
    TrailingPolicy,
    TrailingState,
    TrailingStateMachine,
)
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

_PAPER_LIFECYCLE_TARGETS = {
    "MAIN_CANDIDATE_SELECTED": "ENTRY_PENDING",
    "LEAGUE_CANDIDATE_ARMED": "ENTRY_PENDING",
    "ENTRY_EXPIRED": "SCANNING",
    "ENTRY_REJECTED": "SCANNING",
    "ENTRY_UNFILLED": "SCANNING",
    "ENTRY_FILLED": "PROTECTED",
    "MAIN_MANUAL_EXIT_PENDING": "EXIT_PENDING",
    "MANAGEMENT_EXIT_ARMED": "EXIT_PENDING",
    "FORCED_EXIT_PENDING": "EXIT_PENDING",
    "STOP_EXIT_PENDING": "EXIT_PENDING",
    "TAKE_PROFIT_EXIT_PENDING": "EXIT_PENDING",
    "TRAIL_EXIT_PENDING": "EXIT_PENDING",
    "EXIT_REJECTED": "PROTECTED",
    "EXIT_UNFILLED": "PROTECTED",
}
_PAPER_LIFECYCLE_STATES = frozenset(
    {
        "OBSERVING",
        "SCANNING",
        "ARMED",
        "ENTRY_PENDING",
        "PROTECTED",
        "EXIT_PENDING",
        "CLOSED",
    }
)
_TRAILING_DATA_HEALTH_STATES = frozenset({"HEALTHY", "DEGRADED"})
_TRAILING_ADVERSE_REASON_CODES = frozenset(
    {
        "OFI_ADVERSE",
        "AGGRESSOR_FLOW_ADVERSE",
        "MICROPRICE_ADVERSE",
        "SPREAD_DEGRADED",
    }
)
_PAPER_LIFECYCLE_DESCRIPTIONS_KO = {
    "MAIN_CANDIDATE_SELECTED": "공동 PAPER 계좌가 진입 체결 대기를 시작했습니다.",
    "LEAGUE_CANDIDATE_ARMED": "전략별 PAPER 계좌가 진입 체결 대기를 시작했습니다.",
    "ENTRY_EXPIRED": "진입 계획의 유효시간이 지나 대기 상태로 돌아갔습니다.",
    "ENTRY_REJECTED": "보수적 PAPER 체결 조건을 통과하지 못해 진입을 취소했습니다.",
    "ENTRY_UNFILLED": "실행 가능한 호가에서 PAPER 진입이 체결되지 않았습니다.",
    "ENTRY_FILLED": "실행 가능한 호가로 PAPER 진입을 체결하고 보호관리를 시작했습니다.",
    "MAIN_MANUAL_EXIT_PENDING": "사용자가 공동 PAPER 포지션 종료를 요청했습니다.",
    "MANAGEMENT_EXIT_ARMED": "포지션 관리 규칙이 PAPER 종료 체결을 요청했습니다.",
    "FORCED_EXIT_PENDING": "안전 종료 사유가 발생해 PAPER 종료 체결을 대기합니다.",
    "STOP_EXIT_PENDING": "손절 가격에 도달해 PAPER 종료 체결을 대기합니다.",
    "TAKE_PROFIT_EXIT_PENDING": "목표 가격에 도달해 PAPER 청산 체결을 대기합니다.",
    "TRAIL_EXIT_PENDING": "러너가 단조 trailing 가격에 도달해 PAPER 종료 체결을 대기합니다.",
    "EXIT_REJECTED": "종료 체결 요청이 거부되어 포지션 보호관리를 계속합니다.",
    "EXIT_UNFILLED": "종료 호가가 체결되지 않아 포지션 보호관리를 계속합니다.",
    "EXIT_FILL": "PAPER 청산 호가가 체결되었습니다.",
}


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
    target_hit_ts_ms: dict[str, int] = field(default_factory=dict)
    exit_legs: list[ExitLeg] = field(default_factory=list)
    pending_exit: PendingExit | None = None
    mfe_r: Decimal = Decimal(0)
    mae_r: Decimal = Decimal(0)
    forced_exit_reason: ExitReason | None = None
    forced_exit_label: str | None = None
    trailing_machine: TrailingStateMachine | None = None
    trailing_audit_emitted_count: int = 0
    trailing_data_health: str = "HEALTHY"
    trailing_adverse_since_ms: int | None = None
    trailing_adverse_reason_count: int = 0
    trailing_adverse_active: bool = False
    trailing_adverse_reasons: tuple[str, ...] = ()

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
        self._transition_revisions: dict[str, int] = {}
        self._transition_states: dict[str, str] = {}
        self._last_execution_transition: dict[str, object] = {}

    @property
    def accounts(self) -> tuple[ExecutionAccount, ...]:
        return (self.main, *self.shadows.values())

    def offer(self, plans: tuple[CandidatePlan, ...], *, entries_paused: bool) -> None:
        """현재 호가에서 체결하지 않고 다음 유효 호가까지 pending으로 둔다."""

        valid = tuple(
            plan
            for plan in plans
            if plan.run_id == self.run_id and (self.venue is None or plan.venue is self.venue)
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
                        planned_notional=selected.position_size * selected.worst_allowed_entry,
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
            audit_ts_ms=now_ms,
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
        book: BookSnapshot | None = None,
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
            if book is not None and book.symbol == snapshot.symbol:
                mark = book.bids[0][0] if plan.direction is Side.LONG else book.asks[0][0]
            entry = managed.protected.entry_fill.average_price
            initial_risk = abs(entry - plan.initial_stop)
            if initial_risk <= 0:
                continue
            current_r = (mark - entry) * direction / initial_risk
            planned_risk = initial_risk * max(plan.position_size, plan.minimum_quantity)
            round_trip_cost_r = (
                (plan.expected_fees_usdt + plan.expected_slippage_usdt) / planned_risk
                if planned_risk > 0
                else Decimal(0)
            )
            managed.mfe_r = max(managed.mfe_r, current_r)
            managed.mae_r = min(managed.mae_r, current_r)
            next_target = managed.next_target() or plan.take_profit_targets[-1]
            remaining_edge = (
                (next_target.price - mark) * direction
                - plan.expected_fees_usdt / max(plan.position_size, plan.minimum_quantity)
                - plan.expected_slippage_usdt / max(plan.position_size, plan.minimum_quantity)
            )
            flow_aligned = snapshot.ofi_3s * float(direction)
            trade_aligned = snapshot.trade_imbalance_3s * float(direction)
            micro_aligned = (Decimal(str(snapshot.microprice)) - mark) * direction
            self._update_trailing_health(
                account,
                managed,
                snapshot=snapshot,
                now_ms=now_ms,
                flow_aligned=flow_aligned,
                trade_aligned=trade_aligned,
                micro_aligned=micro_aligned,
            )
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
                if snapshot.data_healthy and min(snapshot.depth_bid_10, snapshot.depth_ask_10) > 0
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
                round_trip_cost_r=round_trip_cost_r,
            )
            decision = self.position_manager.evaluate(
                managed.protected,
                health,
                now_ms=now_ms,
                data_stale=not snapshot.data_healthy,
                recovered_gap_duration_ms=recovered_gap_duration_ms,
                maximum_holding_ms=plan.maximum_holding_ms,
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
                ManagementAction.EXIT_MAX_HOLD: ExitReason.MAX_HOLD,
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
                    audit_ts_ms=now_ms,
                    account_id=account.account_id,
                    action=decision.action.value,
                    reason_codes=list(decision.reason_codes),
                    current_r=str(current_r),
                    round_trip_cost_r=str(round_trip_cost_r),
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
            "time_to_tp1_ms": _elapsed_from_open(
                managed.protected.opened_ts_ms,
                managed.target_hit_ts_ms.get("TP1"),
            ),
            "time_to_tp2_ms": _elapsed_from_open(
                managed.protected.opened_ts_ms,
                managed.target_hit_ts_ms.get("TP2"),
            ),
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
                (book.ts_ms if book else plan.signal_time_ms) - managed.protected.opened_ts_ms,
            )
            // 1_000,
            "management_reason": (
                "종료 체결 지연 대기 중"
                if managed.pending_exit is not None
                else (
                    "TP·SL·근거감쇠 관리 · "
                    + (
                        f"안전 최대 {plan.maximum_holding_ms // 3_600_000}시간"
                        if plan.maximum_holding_ms >= 3_600_000
                        else f"안전 최대 {plan.maximum_holding_ms // 60_000}분"
                    )
                )
            ),
            "management_policy": list(plan.management_policy),
            "maximum_holding_ms": plan.maximum_holding_ms,
            "trailing": self._trailing_view(managed),
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
            "schema_version": 5,
            "run_id": self.run_id,
            "venue": self.venue.value if self.venue is not None else None,
            "snapshot_ts_ms": snapshot_ts_ms,
            "execution_transition_revisions": dict(sorted(self._transition_revisions.items())),
            "execution_transition_states": dict(sorted(self._transition_states.items())),
            "last_execution_transition": dict(self._last_execution_transition),
            "strategy_registry": [dict(row) for row in registry_settings],
            "accounts": [
                _execution_account_payload(account)
                for account in sorted(self.accounts, key=lambda item: item.account_id)
            ],
            "shadow_ledger": self.shadow_ledger.recovery_state(),
        }

    def restore_state(self, payload: Mapping[str, object]) -> None:
        """기존 계좌는 엄격히 검증하고 신규 Registry 계좌만 빈 상태로 추가한다."""

        schema_version = int(str(payload.get("schema_version", 0)))
        if schema_version not in {1, 2, 3, 4, 5}:
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
        additive_registry_extension = False
        if schema_version >= 2 and seen != set(expected):
            registry_rows = payload.get("strategy_registry")
            snapshot_strategy_ids = (
                {
                    str(row["strategy_id"])
                    for row in registry_rows
                    if isinstance(row, Mapping) and row.get("strategy_id") is not None
                }
                if isinstance(registry_rows, list)
                else set()
            )
            seen_strategy_ids = {
                account_id.rsplit(":", 1)[0]
                for account_id in seen
                if account_id != self.MAIN_ACCOUNT_ID
            }
            missing_account_ids = set(expected) - seen
            missing_strategy_ids = {
                account_id.rsplit(":", 1)[0]
                for account_id in missing_account_ids
                if account_id != self.MAIN_ACCOUNT_ID
            }
            additive_registry_extension = bool(missing_account_ids) and (
                self.MAIN_ACCOUNT_ID in seen
                and snapshot_strategy_ids == seen_strategy_ids
                and missing_strategy_ids.isdisjoint(snapshot_strategy_ids)
                and all(
                    {
                        self._shadow_account_id(strategy_id, CostProfile.BASE),
                        self._shadow_account_id(strategy_id, CostProfile.STRESS),
                    }.issubset(missing_account_ids)
                    for strategy_id in missing_strategy_ids
                )
            )
            if not additive_registry_extension:
                raise ValueError("PAPER 복구 snapshot 계좌 집합이 Strategy Registry와 다릅니다.")
        if self.MAIN_ACCOUNT_ID not in seen:
            raise ValueError("PAPER 복구 snapshot에 main 계좌가 없습니다.")
        shadow_payload = payload.get("shadow_ledger")
        if not isinstance(shadow_payload, Mapping):
            raise ValueError("PAPER 복구 snapshot에 shadow 원장이 없습니다.")
        self.shadow_ledger.restore_state(
            shadow_payload,
            allow_missing=schema_version == 1 or additive_registry_extension,
        )
        if schema_version >= 4:
            self._restore_transition_tracking(payload, expected_account_ids=set(expected))
        else:
            self._derive_transition_tracking_from_accounts()
        self.audit_events = []
        self._new_main_trades = []

    @property
    def latest_execution_transition(self) -> dict[str, object]:
        """초보자 화면과 진단이 같은 마지막 PAPER 상태전환을 사용한다."""

        return dict(self._last_execution_transition)

    def remember_transition_audit(self, audit: Mapping[str, object]) -> None:
        """fixture를 포함한 외부 lifecycle 행도 동일한 revision cursor에 반영한다."""

        account_id = str(audit.get("account_id", ""))
        symbol = str(audit.get("symbol", ""))
        previous_state = str(audit.get("previous_state", ""))
        new_state = str(audit.get("new_state", ""))
        request_revision = int(str(audit.get("request_revision", -1)))
        response_revision = int(str(audit.get("response_revision", -1)))
        if not account_id or not symbol:
            raise ValueError("PAPER 상태전환에는 계좌와 종목이 필요합니다.")
        if previous_state not in _PAPER_LIFECYCLE_STATES | {"NONE"}:
            raise ValueError(f"알 수 없는 PAPER 이전 상태입니다: {previous_state}")
        if new_state not in _PAPER_LIFECYCLE_STATES:
            raise ValueError(f"알 수 없는 PAPER 신규 상태입니다: {new_state}")
        key = self._transition_scope_key(account_id, symbol)
        current_revision = self._transition_revisions.get(key, 0)
        if request_revision != current_revision or response_revision != current_revision + 1:
            raise ValueError("PAPER 상태전환 revision이 현재 cursor와 연속되지 않습니다.")
        self._transition_revisions[key] = response_revision
        self._transition_states[key] = new_state
        self._last_execution_transition = dict(audit)

    def _restore_transition_tracking(
        self,
        payload: Mapping[str, object],
        *,
        expected_account_ids: set[str],
    ) -> None:
        raw_revisions = payload.get("execution_transition_revisions")
        raw_states = payload.get("execution_transition_states")
        raw_last = payload.get("last_execution_transition")
        if not isinstance(raw_revisions, Mapping) or not isinstance(raw_states, Mapping):
            raise ValueError("PAPER 복구 snapshot에 상태전환 cursor가 없습니다.")
        if set(map(str, raw_revisions)) != set(map(str, raw_states)):
            raise ValueError("PAPER 상태전환 revision과 상태 범위가 다릅니다.")
        revisions: dict[str, int] = {}
        states: dict[str, str] = {}
        for raw_key, raw_revision in raw_revisions.items():
            key = str(raw_key)
            account_id, separator, symbol = key.partition("|")
            revision = int(str(raw_revision))
            state = str(raw_states[raw_key])
            if (
                separator != "|"
                or account_id not in expected_account_ids
                or not symbol
                or revision < 0
                or state not in _PAPER_LIFECYCLE_STATES
            ):
                raise ValueError(f"PAPER 상태전환 cursor가 잘못됐습니다: {key}")
            revisions[key] = revision
            states[key] = state
        if not isinstance(raw_last, Mapping):
            raise ValueError("PAPER 복구 snapshot의 마지막 상태전환 형식이 잘못됐습니다.")
        if raw_last:
            last_account_id = str(raw_last.get("account_id", ""))
            last_symbol = str(raw_last.get("symbol", ""))
            last_key = self._transition_scope_key(last_account_id, last_symbol)
            last_response_revision = int(str(raw_last.get("response_revision", -1)))
            if (
                not str(raw_last.get("transition_id", ""))
                or str(raw_last.get("run_id", "")) != self.run_id
                or last_account_id not in expected_account_ids
                or not last_symbol
                or last_key not in revisions
                or last_response_revision != revisions[last_key]
                or int(str(raw_last.get("request_revision", -1))) != last_response_revision - 1
                or str(raw_last.get("new_state", "")) != states[last_key]
            ):
                raise ValueError("PAPER 복구 snapshot의 마지막 상태전환이 cursor와 다릅니다.")
        self._transition_revisions = revisions
        self._transition_states = states
        self._last_execution_transition = dict(raw_last)

    def _derive_transition_tracking_from_accounts(self) -> None:
        """schema 1~3은 실제 pending·position 상태에서 안전한 cursor를 새로 시작한다."""

        self._transition_revisions = {}
        self._transition_states = {}
        self._last_execution_transition = {}
        for account in self.accounts:
            for symbol in account.pending_entries:
                self._transition_states[self._transition_scope_key(account.account_id, symbol)] = (
                    "ENTRY_PENDING"
                )
            for symbol, managed in account.positions.items():
                self._transition_states[self._transition_scope_key(account.account_id, symbol)] = (
                    "EXIT_PENDING" if managed.pending_exit is not None else "PROTECTED"
                )

    @staticmethod
    def _transition_scope_key(account_id: str, symbol: str) -> str:
        return f"{account_id}|{symbol}"

    def reconcile_persisted_main_trades(self, rows: Sequence[Mapping[str, object]]) -> None:
        """snapshot 직후 crash 창에서 이미 확정된 원장 거래를 최종 진실로 적용한다."""

        trades = [_paper_trade_from_payload(row) for row in rows]
        if len({trade.trade_id for trade in trades}) != len(trades):
            raise ValueError("복구할 main PAPER 거래 ID가 중복됩니다.")
        closed_ids = {trade.trade_id for trade in trades}
        if self.main.position is not None and self.main.position.protected.trade_id in closed_ids:
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
                (pending.plan.candidate_id for pending in execution.pending_entries.values()),
                None,
            )
            row["pending_entries"] = len(execution.pending_entries)
            row["execution_open_position"] = next(
                (position.protected.trade_id for position in execution.positions.values()),
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
                        account.risk_state.gross_notional / account.risk_state.current_equity
                    )
                    if account.risk_state.current_equity > 0
                    else "0",
                    "maximum_effective_leverage": str(
                        account.risk_state.maximum_effective_leverage
                    ),
                    "maximum_drawdown_usdt": str(shadow_account.maximum_drawdown_usdt),
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
                        "effective_leverage": str(notional / account.risk_state.current_equity)
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
                        "trailing": self._trailing_view(managed),
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

    @staticmethod
    def _trailing_view(managed: ManagedPaperPosition) -> dict[str, object]:
        machine = managed.trailing_machine
        if machine is None:
            return {
                "enabled": False,
                "state": "DISABLED",
                "policy_id": None,
                "model": None,
                "current_trail": None,
                "activation_price": None,
                "activation_ts_ms": None,
                "runner_quantity": "0",
                "giveback_usdt": "0",
                "data_health": managed.trailing_data_health,
                "adverse_active": False,
                "adverse_reasons": [],
                "reference_ts_ms": None,
                "reference_interval_seconds": None,
            }
        return {
            "enabled": True,
            "state": machine.state.value,
            "policy_id": machine.policy.policy_id,
            "model": machine.policy.model.value,
            "activation_rule": machine.policy.activation_rule.value,
            "activation_price": str(machine.activation_price),
            "activation_ts_ms": machine.activation_ts_ms,
            "current_trail": str(machine.current_trail)
            if machine.current_trail is not None
            else None,
            "previous_trail": str(machine.previous_trail)
            if machine.previous_trail is not None
            else None,
            "highest_favorable_bid": str(machine.highest_favorable_bid)
            if machine.highest_favorable_bid is not None
            else None,
            "lowest_favorable_ask": str(machine.lowest_favorable_ask)
            if machine.lowest_favorable_ask is not None
            else None,
            "fee_adjusted_breakeven": str(machine.fee_adjusted_breakeven),
            "runner_quantity": str(machine.runner_quantity),
            "realized_quantity": str(machine.realized_quantity),
            "mfe_r": str(machine.mfe_r),
            "mae_r": str(machine.mae_r),
            "peak_unrealized_usdt": str(machine.peak_unrealized),
            "current_unrealized_usdt": str(machine.current_unrealized),
            "giveback_usdt": str(machine.giveback),
            "data_health": managed.trailing_data_health,
            "adverse_active": managed.trailing_adverse_active,
            "adverse_reasons": list(managed.trailing_adverse_reasons),
            "reference_ts_ms": managed.plan.trailing_reference_ts_ms,
            "reference_interval_seconds": (managed.plan.trailing_reference_interval_seconds),
        }

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
            self._audit(
                "ENTRY_EXPIRED",
                plan,
                audit_ts_ms=book.ts_ms,
                account_id=account.account_id,
            )
            return
        arrival_ts = plan.signal_time_ms + self.cost_model.arrival_latency_ms(account.profile)
        if book.ts_ms < arrival_ts:
            return
        try:
            result = self.execution_engine.open_position(
                trade_id=(
                    f"paper-{plan.candidate_id}-{account.account_id.lower().replace(':', '-')}"
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
                audit_ts_ms=book.ts_ms,
                account_id=account.account_id,
                error_type=type(error).__name__,
            )
            return
        if result.position is None:
            account.entry_orders.append(result.entry_order)
            account.pending_entries.pop(plan.symbol, None)
            self._risk_manager_for(account).release_pending(
                account.risk_state,
                plan.max_planned_loss,
                plan.position_size * plan.worst_allowed_entry,
            )
            self._audit(
                "ENTRY_UNFILLED",
                plan,
                audit_ts_ms=book.ts_ms,
                account_id=account.account_id,
                reason_codes=list(result.entry_order.reason_codes),
            )
            return
        try:
            target_remaining = self._target_quantities(
                plan.take_profit_targets,
                result.position.quantity,
                plan.minimum_quantity,
            )
            trailing_machine = self._new_trailing_machine(
                account,
                plan,
                result.position,
            )
        except ValueError as error:
            account.pending_entries.pop(plan.symbol, None)
            self._risk_manager_for(account).release_pending(
                account.risk_state,
                plan.max_planned_loss,
                plan.position_size * plan.worst_allowed_entry,
            )
            reason_code = (
                "TRAILING_ACTIVATION_NOT_FEE_SAFE"
                if isinstance(error, TrailingActivationNotFeeSafeError)
                else "ENTRY_CONFIGURATION_INVALID"
            )
            self._audit(
                "ENTRY_REJECTED",
                plan,
                audit_ts_ms=book.ts_ms,
                account_id=account.account_id,
                error_type=type(error).__name__,
                reason_codes=[reason_code],
            )
            return
        account.entry_orders.append(result.entry_order)
        account.pending_entries.pop(plan.symbol, None)
        self._risk_manager_for(account).release_pending(
            account.risk_state,
            plan.max_planned_loss,
            plan.position_size * plan.worst_allowed_entry,
        )
        managed = ManagedPaperPosition(
            plan=plan,
            protected=result.position,
            original_quantity=result.position.quantity,
            remaining_quantity=result.position.quantity,
            target_remaining=target_remaining,
            trailing_machine=trailing_machine,
        )
        account.positions[plan.symbol] = managed
        actual_planned_risk = plan.max_planned_loss * result.position.quantity / plan.position_size
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
            audit_ts_ms=result.position.entry_fill.book_ts_ms,
            account_id=account.account_id,
            filled_quantity=str(result.position.quantity),
            fill_price=str(result.position.entry_fill.average_price),
            partial=result.position.quantity != plan.position_size,
        )
        self._emit_trailing_transitions(managed)

    def _new_trailing_machine(
        self,
        account: ExecutionAccount,
        plan: CandidatePlan,
        position: ProtectedPosition,
    ) -> TrailingStateMachine | None:
        policy = plan.trailing_policy
        if policy is None:
            return None
        if policy.activation_rule is TrailingActivationRule.TP1_TRIGGERED:
            policy = replace(
                policy,
                activation_price_override=plan.first_target.price,
            )
        roundtrip_fee_bps = self.cost_model.fee_bps(
            entry=True,
            profile=account.profile,
        ) + self.cost_model.fee_bps(entry=False, profile=account.profile)
        adjustment = position.entry_fill.average_price * roundtrip_fee_bps / Decimal(10_000)
        fee_adjusted_breakeven = (
            position.entry_fill.average_price + adjustment
            if plan.direction is Side.LONG
            else position.entry_fill.average_price - adjustment
        )
        machine = TrailingStateMachine(
            account_id=account.account_id,
            trade_id=position.trade_id,
            strategy_id=plan.strategy_id,
            strategy_version=plan.strategy_version,
            profile=account.profile.value,
            symbol=plan.symbol,
            side=plan.direction,
            entry_price=position.entry_fill.average_price,
            initial_stop=position.initial_stop,
            fee_adjusted_breakeven=fee_adjusted_breakeven,
            original_quantity=position.quantity,
            policy=policy,
        )
        machine.confirm_entry(
            event_time_ms=position.entry_fill.book_ts_ms,
            receive_time_ms=position.entry_fill.book_ts_ms,
        )
        return machine

    def _observe_trailing(
        self,
        managed: ManagedPaperPosition,
        book: BookSnapshot,
    ) -> TrailingDecision | None:
        machine = managed.trailing_machine
        if machine is None:
            return None
        protection_before = (
            machine.state,
            machine.highest_favorable_bid,
            machine.lowest_favorable_ask,
            machine.current_trail,
        )
        transition_count_before = len(machine.transitions)
        receive_ts_ms = book.receive_ts_ms if book.receive_ts_ms is not None else book.ts_ms
        current = self._current_pnl(managed, book)
        decision = machine.observe(
            TrailingObservation(
                event_id=(
                    f"{book.venue.value}:{book.symbol}:{book.ts_ms}:"
                    f"{book.bids[0][0]}:{book.asks[0][0]}"
                ),
                event_time_ms=book.ts_ms,
                receive_time_ms=receive_ts_ms,
                best_bid=book.bids[0][0],
                best_ask=book.asks[0][0],
                sequence_valid=book.sequence_valid,
                stale=book.stale,
                data_health=managed.trailing_data_health,
                remaining_quantity=managed.remaining_quantity,
                realized_quantity=managed.original_quantity - managed.remaining_quantity,
                current_unrealized=current["net"],
                atr=managed.plan.trailing_atr,
                completed_structure_stop=managed.plan.trailing_structure_stop,
                adverse_edge=managed.trailing_adverse_active,
            )
        )
        protection_after = (
            machine.state,
            machine.highest_favorable_bid,
            machine.lowest_favorable_ask,
            machine.current_trail,
        )
        if (
            protection_after != protection_before
            and len(machine.transitions) == transition_count_before
        ):
            self.audit_events.append(
                {
                    "event": "TRAILING_MARK_UPDATED",
                    "candidate_id": managed.plan.candidate_id,
                    "run_id": managed.plan.run_id,
                    "ts_ms": book.ts_ms,
                    **machine.audit_snapshot(
                        event_time_ms=book.ts_ms,
                        receive_time_ms=receive_ts_ms,
                        actor="POSITION_MANAGER",
                        reason_codes=("EXECUTABLE_FAVORABLE_MARK_OR_TRAIL_UPDATED",),
                        data_health=managed.trailing_data_health,
                    ),
                }
            )
        if machine.current_trail is not None:
            proposed = (
                max(managed.protected.current_stop, machine.current_trail)
                if managed.plan.direction is Side.LONG
                else min(managed.protected.current_stop, machine.current_trail)
            )
            managed.protected = self.position_manager.tighten_stop(
                managed.protected,
                proposed,
            )
        self._emit_trailing_transitions(managed)
        return decision

    def _update_trailing_health(
        self,
        account: ExecutionAccount,
        managed: ManagedPaperPosition,
        *,
        snapshot: FeatureSnapshot,
        now_ms: int,
        flow_aligned: float,
        trade_aligned: float,
        micro_aligned: Decimal,
    ) -> None:
        machine = managed.trailing_machine
        if machine is None:
            return
        before = (
            managed.trailing_data_health,
            managed.trailing_adverse_since_ms,
            managed.trailing_adverse_reason_count,
            managed.trailing_adverse_active,
            managed.trailing_adverse_reasons,
        )
        managed.trailing_data_health = "HEALTHY" if snapshot.data_healthy else "DEGRADED"
        reasons: list[str] = []
        if machine.policy.model is TrailingModel.EDGE_ADAPTIVE and machine.state in {
            TrailingState.TRAIL_ARMED,
            TrailingState.RUNNER_ACTIVE,
        }:
            if flow_aligned <= 0:
                reasons.append("OFI_ADVERSE")
            if trade_aligned < 0:
                reasons.append("AGGRESSOR_FLOW_ADVERSE")
            if micro_aligned < 0:
                reasons.append("MICROPRICE_ADVERSE")
            if snapshot.spread_bps >= 12:
                reasons.append("SPREAD_DEGRADED")
        managed.trailing_adverse_reasons = tuple(reasons)
        managed.trailing_adverse_reason_count = len(reasons)
        if (
            managed.trailing_data_health == "HEALTHY"
            and len(reasons) >= machine.policy.adverse_signal_count
        ):
            if managed.trailing_adverse_since_ms is None:
                managed.trailing_adverse_since_ms = now_ms
            managed.trailing_adverse_active = (
                now_ms - managed.trailing_adverse_since_ms >= machine.policy.adverse_persistence_ms
            )
        else:
            managed.trailing_adverse_since_ms = None
            managed.trailing_adverse_active = False
        after = (
            managed.trailing_data_health,
            managed.trailing_adverse_since_ms,
            managed.trailing_adverse_reason_count,
            managed.trailing_adverse_active,
            managed.trailing_adverse_reasons,
        )
        if after == before:
            return
        self.audit_events.append(
            {
                "event": "TRAILING_EDGE_STATE_UPDATED",
                "candidate_id": managed.plan.candidate_id,
                "run_id": managed.plan.run_id,
                "ts_ms": now_ms,
                "account_id": account.account_id,
                "data_health": managed.trailing_data_health,
                "adverse_since_ms": managed.trailing_adverse_since_ms,
                "adverse_reason_count": managed.trailing_adverse_reason_count,
                "adverse_active": managed.trailing_adverse_active,
                "adverse_reasons": list(managed.trailing_adverse_reasons),
                "state_checksum": machine.checksum(),
            }
        )

    def _emit_trailing_transitions(self, managed: ManagedPaperPosition) -> None:
        machine = managed.trailing_machine
        if machine is None:
            return
        rows = machine.to_payload()["transitions"]
        if not isinstance(rows, list):
            raise ValueError("trailing transition payload는 list여야 합니다.")
        for row in rows[managed.trailing_audit_emitted_count :]:
            if not isinstance(row, dict):
                raise ValueError("trailing transition row는 object여야 합니다.")
            self.audit_events.append(
                {
                    "event": "TRAILING_STATE_TRANSITION",
                    "candidate_id": managed.plan.candidate_id,
                    "run_id": managed.plan.run_id,
                    "ts_ms": row["event_time_ms"],
                    **row,
                    "state_checksum": machine.checksum(),
                }
            )
        managed.trailing_audit_emitted_count = len(rows)

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
                audit_ts_ms=book.ts_ms,
                account_id=account.account_id,
                reason=managed.forced_exit_reason.value,
            )
            return
        trailing_decision = self._observe_trailing(managed, book)
        if trailing_decision is not None and trailing_decision.trail_exit_triggered:
            trigger = trailing_decision.current_trail or best_executable
            managed.pending_exit = PendingExit(
                ExitReason.TRAILING_STOP,
                "TRAILING_STOP",
                managed.remaining_quantity,
                trigger,
                book.ts_ms,
            )
            self._audit(
                "TRAIL_EXIT_PENDING",
                managed.plan,
                audit_ts_ms=book.ts_ms,
                account_id=account.account_id,
                label="TRAILING_STOP",
                trigger_price=str(trigger),
            )
            return
        stop_hit = (
            best_executable <= managed.protected.current_stop
            if managed.plan.direction is Side.LONG
            else best_executable >= managed.protected.current_stop
        )
        target = managed.next_target()
        trailing = managed.trailing_machine
        fixed_target_allowed = trailing is None or (
            trailing.policy.partial_tp_required
            and trailing.state
            in {
                TrailingState.PROFIT_ACTIVATION_PENDING,
                TrailingState.TRAIL_ARMED,
                TrailingState.PARTIAL_TP_PENDING,
            }
        )
        target_hit = bool(
            fixed_target_allowed
            and target
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
                audit_ts_ms=book.ts_ms,
                account_id=account.account_id,
                trigger_price=str(managed.protected.current_stop),
            )
            return
        if target_hit and target is not None:
            if target.label == "TP1" and trailing is not None:
                trailing.mark_partial_tp_pending(
                    event_time_ms=book.ts_ms,
                    receive_time_ms=book.ts_ms,
                    data_health="HEALTHY",
                )
                self._emit_trailing_transitions(managed)
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
                audit_ts_ms=book.ts_ms,
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
                book.bids[0][0] if managed.plan.direction is Side.LONG else book.asks[0][0]
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
            self._restore_trailing_after_unfilled_exit(managed, pending, book.ts_ms)
            self._audit(
                "EXIT_REJECTED",
                managed.plan,
                audit_ts_ms=book.ts_ms,
                account_id=account.account_id,
                label=pending.label,
                error_type=type(error).__name__,
            )
            return
        account.exit_orders.append(result.exit_order)
        managed.pending_exit = None
        fill = result.exit_order.fill
        if fill is None:
            self._restore_trailing_after_unfilled_exit(managed, pending, book.ts_ms)
            self._audit(
                "EXIT_UNFILLED",
                managed.plan,
                audit_ts_ms=book.ts_ms,
                account_id=account.account_id,
                label=pending.label,
            )
            return
        managed.exit_legs.append(ExitLeg(pending.label, pending.reason, fill))
        if pending.label in managed.target_remaining:
            managed.target_hit_ts_ms.setdefault(pending.label, fill.book_ts_ms)
        managed.remaining_quantity -= fill.quantity
        if pending.label in managed.target_remaining:
            managed.target_remaining[pending.label] = max(
                Decimal(0), managed.target_remaining[pending.label] - fill.quantity
            )
        trailing = managed.trailing_machine
        if pending.label == "TP1" and trailing is not None and managed.remaining_quantity > 0:
            trailing.mark_partial_tp_filled(
                event_time_ms=fill.book_ts_ms,
                receive_time_ms=fill.book_ts_ms,
                realized_quantity=managed.original_quantity - managed.remaining_quantity,
                remaining_quantity=managed.remaining_quantity,
                target_complete=managed.target_remaining.get("TP1", Decimal(0)) == 0,
                data_health="HEALTHY",
            )
            self._emit_trailing_transitions(managed)
        if (
            pending.label == "TP1"
            and managed.plan.exit_style is ExitStyle.TREND_40_60
            and managed.remaining_quantity > 0
        ):
            fee_bps = self.cost_model.fee_bps(
                entry=True,
                profile=account.profile,
            ) + self.cost_model.fee_bps(entry=False, profile=account.profile)
            adjustment = managed.protected.entry_fill.average_price * fee_bps / Decimal(10_000)
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
            audit_ts_ms=fill.book_ts_ms,
            account_id=account.account_id,
            label=pending.label,
            filled_quantity=str(fill.quantity),
            remaining_quantity=str(managed.remaining_quantity),
            fill_price=str(fill.average_price),
        )
        if managed.remaining_quantity > 0:
            if pending.reason is ExitReason.TRAILING_STOP:
                managed.pending_exit = PendingExit(
                    reason=ExitReason.TRAILING_STOP,
                    label="TRAILING_STOP",
                    requested_quantity=managed.remaining_quantity,
                    trigger_reference_price=pending.trigger_reference_price,
                    trigger_ts_ms=book.ts_ms,
                )
            return
        if trailing is not None:
            trailing.mark_closed(
                event_time_ms=fill.book_ts_ms,
                receive_time_ms=fill.book_ts_ms,
                actor="PAPER_EXECUTION",
                reason_codes=(pending.label,),
                data_health="HEALTHY",
            )
            self._emit_trailing_transitions(managed)
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
            exit_quantity = sum((leg.fill.quantity for leg in managed.exit_legs), start=Decimal(0))
            exit_notional = sum((leg.fill.notional for leg in managed.exit_legs), start=Decimal(0))
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
                tp1_hit_ts_ms=trade.tp1_hit_ts_ms,
                tp2_hit_ts_ms=trade.tp2_hit_ts_ms,
                time_to_tp1_ms=trade.time_to_tp1_ms,
                time_to_tp2_ms=trade.time_to_tp2_ms,
                time_to_stop_ms=trade.time_to_stop_ms,
                trailing_activation_ts_ms=trade.trailing_activation_ts_ms,
                runner_started_ts_ms=trade.runner_started_ts_ms,
                peak_unrealized_usdt=trade.peak_unrealized_usdt,
                giveback_usdt=trade.giveback_usdt,
                runner_net_pnl_usdt=trade.runner_net_pnl_usdt,
                trail_trigger_slippage_usdt=trade.trail_trigger_slippage_usdt,
                trailing_state_checksum=trade.trailing_state_checksum,
            )
        account.positions.pop(managed.plan.symbol, None)

    def _restore_trailing_after_unfilled_exit(
        self,
        managed: ManagedPaperPosition,
        pending: PendingExit,
        event_time_ms: int,
    ) -> None:
        trailing = managed.trailing_machine
        if trailing is None:
            return
        if pending.label == "TP1":
            trailing.mark_partial_tp_rejected(
                event_time_ms=event_time_ms,
                receive_time_ms=event_time_ms,
                data_health="HEALTHY",
            )
        elif pending.reason is ExitReason.TRAILING_STOP:
            trailing.mark_exit_rejected(
                event_time_ms=event_time_ms,
                receive_time_ms=event_time_ms,
                data_health="HEALTHY",
            )
        self._emit_trailing_transitions(managed)

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
        trailing = managed.trailing_machine
        runner_net_pnl = self._runner_net_pnl(managed)
        trail_trigger_slippage = sum(
            (
                leg.fill.slippage_usdt
                for leg in managed.exit_legs
                if leg.reason is ExitReason.TRAILING_STOP
            ),
            start=Decimal(0),
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
            candidate_id=managed.plan.candidate_id,
            signal_event_id=managed.plan.signal_event_id,
            take_profit_1=managed.plan.take_profit_targets[0].price,
            take_profit_2=(
                managed.plan.take_profit_targets[1].price
                if len(managed.plan.take_profit_targets) > 1
                else None
            ),
            tp1_hit_ts_ms=managed.target_hit_ts_ms.get("TP1"),
            tp2_hit_ts_ms=managed.target_hit_ts_ms.get("TP2"),
            time_to_tp1_ms=_elapsed_from_open(
                managed.protected.opened_ts_ms,
                managed.target_hit_ts_ms.get("TP1"),
            ),
            time_to_tp2_ms=_elapsed_from_open(
                managed.protected.opened_ts_ms,
                managed.target_hit_ts_ms.get("TP2"),
            ),
            time_to_stop_ms=(
                max(0, closed_ts_ms - managed.protected.opened_ts_ms)
                if final_reason is ExitReason.STOP
                else None
            ),
            trailing_activation_ts_ms=(trailing.activation_ts_ms if trailing is not None else None),
            runner_started_ts_ms=(trailing.runner_started_ts_ms if trailing is not None else None),
            peak_unrealized_usdt=(trailing.peak_unrealized if trailing is not None else Decimal(0)),
            giveback_usdt=trailing.giveback if trailing is not None else Decimal(0),
            runner_net_pnl_usdt=runner_net_pnl,
            trail_trigger_slippage_usdt=trail_trigger_slippage,
            trailing_state_checksum=trailing.checksum() if trailing is not None else None,
        )

    @staticmethod
    def _runner_net_pnl(managed: ManagedPaperPosition) -> Decimal:
        """부분익절 뒤 러너 또는 전량 trailing 구간의 순기여를 보수적으로 배분한다."""

        trailing = managed.trailing_machine
        if trailing is None or trailing.activation_ts_ms is None:
            return Decimal(0)
        entry = managed.protected.entry_fill
        direction = Decimal(1) if managed.plan.direction is Side.LONG else Decimal(-1)
        entry_cost = entry.fee_usdt + entry.slippage_usdt
        total = Decimal(0)
        for leg in managed.exit_legs:
            if trailing.policy.partial_tp_required and leg.label == "TP1":
                continue
            quantity_share = leg.fill.quantity / entry.quantity
            gross = (leg.fill.average_price - entry.average_price) * leg.fill.quantity * direction
            total += (
                gross - leg.fill.fee_usdt - leg.fill.slippage_usdt - entry_cost * quantity_share
            )
        return total

    def _current_pnl(
        self,
        managed: ManagedPaperPosition,
        book: BookSnapshot | None,
    ) -> dict[str, Decimal]:
        entry = managed.protected.entry_fill
        direction = Decimal(1) if managed.plan.direction is Side.LONG else Decimal(-1)
        realized_gross = sum(
            (
                (leg.fill.average_price - entry.average_price) * leg.fill.quantity * direction
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
            if reason in {ExitReason.STOP, ExitReason.TRAILING_STOP}
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
        expected_slippage = quantity * plan.noise_buffer * Decimal("1.5") * slippage_multiplier
        net_reward = gross_reward - expected_fees - expected_slippage
        if net_reward <= 0 or sizing.planned_loss <= 0:
            return None
        net_rr = (net_reward / sizing.planned_loss).quantize(Decimal("0.0001"))
        effective_leverage = quantity * plan.worst_allowed_entry / account.risk_state.current_equity
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

    def _audit(
        self,
        event: str,
        plan: CandidatePlan,
        *,
        audit_ts_ms: int | None = None,
        **payload: object,
    ) -> None:
        timestamp = plan.signal_time_ms if audit_ts_ms is None else audit_ts_ms
        row: dict[str, object] = {
            "event": event,
            "candidate_id": plan.candidate_id,
            "run_id": plan.run_id,
            "symbol": plan.symbol,
            "strategy_id": plan.strategy_id,
            "side": plan.direction.value,
            "ts_ms": timestamp,
            **payload,
        }
        if event in _PAPER_LIFECYCLE_TARGETS or event == "EXIT_FILL":
            account_id = str(payload.get("account_id", self.MAIN_ACCOUNT_ID))
            key = self._transition_scope_key(account_id, plan.symbol)
            request_revision = self._transition_revisions.get(key, 0)
            previous_state = self._transition_states.get(key, "SCANNING")
            new_state = (
                "CLOSED"
                if event == "EXIT_FILL"
                and Decimal(str(payload.get("remaining_quantity", "0"))) <= 0
                else "PROTECTED"
                if event == "EXIT_FILL"
                else _PAPER_LIFECYCLE_TARGETS[event]
            )
            cause_code = str(
                payload.get("reason") or payload.get("action") or payload.get("label") or event
            )
            row.update(
                {
                    "transition_id": (
                        f"paper-execution-{plan.run_id}-{account_id}-{plan.symbol}-"
                        f"rev-{request_revision + 1}"
                    ),
                    "previous_state": previous_state,
                    "new_state": new_state,
                    "occurred_ts_ms": timestamp,
                    "cause": cause_code,
                    "cause_code": cause_code,
                    "description_ko": _PAPER_LIFECYCLE_DESCRIPTIONS_KO[event],
                    "actor": "USER_UI" if event == "MAIN_MANUAL_EXIT_PENDING" else "AUTO_SAFETY",
                    "run_id": plan.run_id,
                    "strategy_id": plan.strategy_id,
                    "account_id": account_id,
                    "symbol": plan.symbol,
                    "request_revision": request_revision,
                    "response_revision": request_revision + 1,
                    "reversible": event not in {"ENTRY_FILLED", "EXIT_FILL"}
                    and new_state != "CLOSED",
                }
            )
            self.remember_transition_audit(row)
        self.audit_events.append(row)


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
        "completed_trades": [_paper_trade_payload(trade) for trade in account.completed_trades],
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
            {restored_pending.plan.symbol: restored_pending} if restored_pending is not None else {}
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
            _managed_position_from_payload(position) if isinstance(position, Mapping) else None
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
        _paper_trade_from_payload(value) for value in completed if isinstance(value, Mapping)
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
            (position.protected.entry_fill.notional for position in account.positions.values()),
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
        maximum_effective_leverage=Decimal(str(payload.get("maximum_effective_leverage", "0"))),
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
        "target_remaining": {key: str(value) for key, value in position.target_remaining.items()},
        "target_hit_ts_ms": dict(position.target_hit_ts_ms),
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
        "trailing_machine": position.trailing_machine.to_payload()
        if position.trailing_machine is not None
        else None,
        "trailing_audit_emitted_count": position.trailing_audit_emitted_count,
        "trailing_data_health": position.trailing_data_health,
        "trailing_adverse_since_ms": position.trailing_adverse_since_ms,
        "trailing_adverse_reason_count": position.trailing_adverse_reason_count,
        "trailing_adverse_active": position.trailing_adverse_active,
        "trailing_adverse_reasons": list(position.trailing_adverse_reasons),
    }


def _managed_position_from_payload(payload: Mapping[str, object]) -> ManagedPaperPosition:
    plan_payload = payload.get("plan")
    protected_payload = payload.get("protected")
    remaining_payload = payload.get("target_remaining")
    target_hit_payload = payload.get("target_hit_ts_ms", {})
    leg_rows = payload.get("exit_legs")
    if (
        not isinstance(plan_payload, Mapping)
        or not isinstance(protected_payload, Mapping)
        or not isinstance(remaining_payload, Mapping)
        or not isinstance(target_hit_payload, Mapping)
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
    trailing_payload = payload.get("trailing_machine")
    trailing_health = payload.get("trailing_data_health", "HEALTHY")
    adverse_active = payload.get("trailing_adverse_active", False)
    adverse_reason_rows = payload.get("trailing_adverse_reasons", [])
    if trailing_health not in _TRAILING_DATA_HEALTH_STATES:
        raise ValueError("복구 trailing 데이터 건강상태가 잘못됐습니다.")
    if not isinstance(adverse_active, bool):
        raise ValueError("복구 trailing adverse 활성값은 boolean이어야 합니다.")
    if not isinstance(adverse_reason_rows, list) or not all(
        isinstance(value, str) and value in _TRAILING_ADVERSE_REASON_CODES
        for value in adverse_reason_rows
    ):
        raise ValueError("복구 trailing adverse 사유 형식이 잘못됐습니다.")
    if len(set(adverse_reason_rows)) != len(adverse_reason_rows):
        raise ValueError("복구 trailing adverse 사유가 중복됐습니다.")
    position = ManagedPaperPosition(
        plan=plan,
        protected=protected,
        original_quantity=original,
        remaining_quantity=remaining,
        target_remaining={
            str(key): Decimal(str(value)) for key, value in remaining_payload.items()
        },
        target_hit_ts_ms={str(key): int(str(value)) for key, value in target_hit_payload.items()},
        exit_legs=[
            _exit_leg_from_payload(value) for value in leg_rows if isinstance(value, Mapping)
        ],
        pending_exit=_pending_exit_from_payload(pending) if isinstance(pending, Mapping) else None,
        mfe_r=Decimal(str(payload["mfe_r"])),
        mae_r=Decimal(str(payload["mae_r"])),
        forced_exit_reason=ExitReason(str(payload["forced_exit_reason"]))
        if payload.get("forced_exit_reason") is not None
        else None,
        forced_exit_label=str(payload["forced_exit_label"])
        if payload.get("forced_exit_label") is not None
        else None,
        trailing_machine=TrailingStateMachine.from_payload(trailing_payload)
        if isinstance(trailing_payload, Mapping)
        else None,
        trailing_audit_emitted_count=int(str(payload.get("trailing_audit_emitted_count", 0))),
        trailing_data_health=str(trailing_health),
        trailing_adverse_since_ms=int(str(payload["trailing_adverse_since_ms"]))
        if payload.get("trailing_adverse_since_ms") is not None
        else None,
        trailing_adverse_reason_count=int(str(payload.get("trailing_adverse_reason_count", 0))),
        trailing_adverse_active=adverse_active,
        trailing_adverse_reasons=tuple(adverse_reason_rows),
    )
    if len(position.exit_legs) != len(leg_rows):
        raise ValueError("복구 포지션의 exit leg 형식이 잘못됐습니다.")
    if position.pending_exit is not None and position.pending_exit.requested_quantity > remaining:
        raise ValueError("복구 포지션의 대기 청산 수량이 잔여 수량을 넘습니다.")
    if (
        position.trailing_machine is not None
        and position.trailing_machine.trade_id != protected.trade_id
    ):
        raise ValueError("복구 trailing 상태와 보호 포지션 trade ID가 다릅니다.")
    if position.trailing_audit_emitted_count > len(
        position.trailing_machine.transitions if position.trailing_machine else ()
    ):
        raise ValueError("복구 trailing audit count가 transition 수를 넘습니다.")
    if position.trailing_adverse_reason_count != len(position.trailing_adverse_reasons):
        raise ValueError("복구 trailing adverse 사유 수가 목록과 다릅니다.")
    if position.trailing_adverse_reason_count < 0:
        raise ValueError("복구 trailing adverse 사유 수는 음수일 수 없습니다.")
    if position.trailing_adverse_since_ms is not None and position.trailing_adverse_since_ms < 0:
        raise ValueError("복구 trailing adverse 시작시각이 잘못됐습니다.")
    if position.trailing_machine is None and (
        position.trailing_data_health != "HEALTHY"
        or position.trailing_adverse_since_ms is not None
        or position.trailing_adverse_reason_count
        or position.trailing_adverse_active
        or position.trailing_adverse_reasons
    ):
        raise ValueError("trailing 없는 복구 포지션에 trailing 상태가 남았습니다.")
    if position.trailing_machine is not None:
        policy = position.trailing_machine.policy
        if policy.model is not TrailingModel.EDGE_ADAPTIVE and (
            position.trailing_adverse_since_ms is not None
            or position.trailing_adverse_reason_count
            or position.trailing_adverse_active
            or position.trailing_adverse_reasons
        ):
            raise ValueError("비적응형 trailing 복구에 adverse 상태가 남았습니다.")
        if (
            policy.model is TrailingModel.EDGE_ADAPTIVE
            and (position.trailing_adverse_since_ms is not None)
            and (
                position.trailing_data_health != "HEALTHY"
                or position.trailing_adverse_reason_count < policy.adverse_signal_count
            )
        ):
            raise ValueError("복구 adaptive trailing 지속시각의 근거가 부족합니다.")
        if (
            policy.model is TrailingModel.EDGE_ADAPTIVE
            and position.trailing_data_health == "HEALTHY"
            and position.trailing_adverse_reason_count >= policy.adverse_signal_count
            and position.trailing_adverse_since_ms is None
        ):
            raise ValueError("복구 adaptive trailing에 adverse 시작시각이 없습니다.")
        if position.trailing_adverse_active and (
            position.trailing_data_health != "HEALTHY"
            or position.trailing_adverse_since_ms is None
            or position.trailing_adverse_reason_count < policy.adverse_signal_count
        ):
            raise ValueError("복구 adaptive trailing 활성상태의 근거가 부족합니다.")
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
        "maximum_holding_ms": plan.maximum_holding_ms,
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
        "trailing_policy": _trailing_policy_payload(plan.trailing_policy)
        if plan.trailing_policy is not None
        else None,
        "trailing_atr": str(plan.trailing_atr) if plan.trailing_atr is not None else None,
        "trailing_structure_stop": (
            str(plan.trailing_structure_stop) if plan.trailing_structure_stop is not None else None
        ),
        "trailing_reference_ts_ms": plan.trailing_reference_ts_ms,
        "trailing_reference_interval_seconds": plan.trailing_reference_interval_seconds,
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
        maximum_holding_ms=int(str(payload.get("maximum_holding_ms", 900_000))),
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
        trailing_policy=_trailing_policy_from_payload(payload.get("trailing_policy")),
        trailing_atr=Decimal(str(payload["trailing_atr"]))
        if payload.get("trailing_atr") is not None
        else None,
        trailing_structure_stop=Decimal(str(payload["trailing_structure_stop"]))
        if payload.get("trailing_structure_stop") is not None
        else None,
        trailing_reference_ts_ms=int(str(payload["trailing_reference_ts_ms"]))
        if payload.get("trailing_reference_ts_ms") is not None
        else None,
        trailing_reference_interval_seconds=int(str(payload["trailing_reference_interval_seconds"]))
        if payload.get("trailing_reference_interval_seconds") is not None
        else None,
    )


def _trailing_policy_payload(policy: TrailingPolicy) -> dict[str, object]:
    return {
        "policy_id": policy.policy_id,
        "model": policy.model.value,
        "activation_rule": policy.activation_rule.value,
        "activation_r": str(policy.activation_r),
        "partial_tp_required": policy.partial_tp_required,
        "fixed_distance": str(policy.fixed_distance) if policy.fixed_distance is not None else None,
        "retracement_rate": str(policy.retracement_rate)
        if policy.retracement_rate is not None
        else None,
        "atr_multiplier": str(policy.atr_multiplier) if policy.atr_multiplier is not None else None,
        "adverse_atr_multiplier": str(policy.adverse_atr_multiplier)
        if policy.adverse_atr_multiplier is not None
        else None,
        "adverse_signal_count": policy.adverse_signal_count,
        "adverse_persistence_ms": policy.adverse_persistence_ms,
        "activation_price_override": str(policy.activation_price_override)
        if policy.activation_price_override is not None
        else None,
    }


def _trailing_policy_from_payload(payload: object) -> TrailingPolicy | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ValueError("복구 trailing policy 형식이 잘못됐습니다.")
    return TrailingPolicy(
        policy_id=str(payload["policy_id"]),
        model=TrailingModel(str(payload["model"])),
        activation_rule=TrailingActivationRule(str(payload["activation_rule"])),
        activation_r=Decimal(str(payload["activation_r"])),
        partial_tp_required=_strict_bool(
            payload.get("partial_tp_required"),
            "partial_tp_required",
        ),
        fixed_distance=Decimal(str(payload["fixed_distance"]))
        if payload.get("fixed_distance") is not None
        else None,
        retracement_rate=Decimal(str(payload["retracement_rate"]))
        if payload.get("retracement_rate") is not None
        else None,
        atr_multiplier=Decimal(str(payload["atr_multiplier"]))
        if payload.get("atr_multiplier") is not None
        else None,
        adverse_atr_multiplier=Decimal(str(payload["adverse_atr_multiplier"]))
        if payload.get("adverse_atr_multiplier") is not None
        else None,
        adverse_signal_count=int(str(payload.get("adverse_signal_count", 2))),
        adverse_persistence_ms=int(str(payload.get("adverse_persistence_ms", 3_000))),
        activation_price_override=Decimal(str(payload["activation_price_override"]))
        if payload.get("activation_price_override") is not None
        else None,
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
        "protection_orders": [_paper_order_payload(order) for order in position.protection_orders],
        "opened_ts_ms": position.opened_ts_ms,
        "profile": position.profile.value,
    }


def _protected_position_from_payload(payload: Mapping[str, object]) -> ProtectedPosition:
    fill_payload = payload.get("entry_fill")
    order_rows = payload.get("protection_orders")
    if not isinstance(fill_payload, Mapping) or not isinstance(order_rows, list):
        raise ValueError("복구 보호 포지션 형식이 잘못됐습니다.")
    orders = tuple(
        _paper_order_from_payload(value) for value in order_rows if isinstance(value, Mapping)
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
        "candidate_id": trade.candidate_id,
        "signal_event_id": trade.signal_event_id,
        "take_profit_1": (str(trade.take_profit_1) if trade.take_profit_1 is not None else None),
        "take_profit_2": (str(trade.take_profit_2) if trade.take_profit_2 is not None else None),
        "tp1_hit_ts_ms": trade.tp1_hit_ts_ms,
        "tp2_hit_ts_ms": trade.tp2_hit_ts_ms,
        "time_to_tp1_ms": trade.time_to_tp1_ms,
        "time_to_tp2_ms": trade.time_to_tp2_ms,
        "time_to_stop_ms": trade.time_to_stop_ms,
        "trailing_activation_ts_ms": trade.trailing_activation_ts_ms,
        "runner_started_ts_ms": trade.runner_started_ts_ms,
        "peak_unrealized_usdt": str(trade.peak_unrealized_usdt),
        "giveback_usdt": str(trade.giveback_usdt),
        "runner_net_pnl_usdt": str(trade.runner_net_pnl_usdt),
        "trail_trigger_slippage_usdt": str(trade.trail_trigger_slippage_usdt),
        "trailing_state_checksum": trade.trailing_state_checksum,
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
        candidate_id=(
            str(payload["candidate_id"]) if payload.get("candidate_id") is not None else None
        ),
        signal_event_id=(
            str(payload["signal_event_id"]) if payload.get("signal_event_id") is not None else None
        ),
        take_profit_1=(
            Decimal(str(payload["take_profit_1"]))
            if payload.get("take_profit_1") is not None
            else None
        ),
        take_profit_2=(
            Decimal(str(payload["take_profit_2"]))
            if payload.get("take_profit_2") is not None
            else None
        ),
        tp1_hit_ts_ms=_optional_int(payload.get("tp1_hit_ts_ms")),
        tp2_hit_ts_ms=_optional_int(payload.get("tp2_hit_ts_ms")),
        time_to_tp1_ms=_optional_int(payload.get("time_to_tp1_ms")),
        time_to_tp2_ms=_optional_int(payload.get("time_to_tp2_ms")),
        time_to_stop_ms=_optional_int(payload.get("time_to_stop_ms")),
        trailing_activation_ts_ms=_optional_int(payload.get("trailing_activation_ts_ms")),
        runner_started_ts_ms=_optional_int(payload.get("runner_started_ts_ms")),
        peak_unrealized_usdt=Decimal(str(payload.get("peak_unrealized_usdt", "0"))),
        giveback_usdt=Decimal(str(payload.get("giveback_usdt", "0"))),
        runner_net_pnl_usdt=Decimal(str(payload.get("runner_net_pnl_usdt", "0"))),
        trail_trigger_slippage_usdt=Decimal(str(payload.get("trail_trigger_slippage_usdt", "0"))),
        trailing_state_checksum=(
            str(payload["trailing_state_checksum"])
            if payload.get("trailing_state_checksum") is not None
            else None
        ),
    )


def _optional_int(value: object | None) -> int | None:
    return None if value is None else int(str(value))


def _strict_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"복구 payload의 {field_name}은 boolean이어야 합니다.")
    return value


def _elapsed_from_open(opened_ts_ms: int, milestone_ts_ms: int | None) -> int | None:
    return None if milestone_ts_ms is None else max(0, milestone_ts_ms - opened_ts_ms)


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
