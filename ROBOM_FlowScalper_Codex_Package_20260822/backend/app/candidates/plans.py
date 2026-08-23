"""전략 신호를 체결 전 고정되는 불변 PAPER 거래계획으로 변환한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from backend.app.costing import CostModel, CostProfile
from backend.app.domain.market import Instrument
from backend.app.domain.models import Side, Venue
from backend.app.execution.models import BookSnapshot
from backend.app.features import FeatureSnapshot
from backend.app.regime import Regime
from backend.app.risk import RiskManager, RiskSizingInput, RiskState
from backend.app.strategies.base import CandidateDecision, CandidateStatus
from backend.app.strategies.registry import ExitStyle


@dataclass(frozen=True, slots=True)
class TakeProfitTarget:
    label: str
    price: Decimal
    quantity_fraction: Decimal

    def __post_init__(self) -> None:
        if self.label not in {"TP1", "TP2"}:
            raise ValueError("익절 목표는 TP1 또는 TP2여야 합니다.")
        if self.price <= 0:
            raise ValueError("익절 가격은 양수여야 합니다.")
        if not Decimal(0) < self.quantity_fraction <= Decimal(1):
            raise ValueError("익절 수량 비율은 0보다 크고 1 이하여야 합니다.")


@dataclass(frozen=True, slots=True)
class CandidatePlan:
    candidate_id: str
    signal_event_id: str
    run_id: str
    venue: Venue
    symbol: str
    strategy_id: str
    strategy_version: str
    exit_style: ExitStyle
    direction: Side
    signal_time_ms: int
    expires_at_ms: int
    regime: Regime
    planned_entry: Decimal
    worst_allowed_entry: Decimal
    initial_stop: Decimal
    noise_buffer: Decimal
    take_profit_targets: tuple[TakeProfitTarget, ...]
    position_size: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    executable_depth_quantity: Decimal
    risk_budget: Decimal
    max_planned_loss: Decimal
    gross_reward_usdt: Decimal
    expected_fees_usdt: Decimal
    expected_slippage_usdt: Decimal
    net_reward_usdt: Decimal
    net_risk_usdt: Decimal
    net_reward_risk: Decimal
    data_quality: Decimal
    signal_quality: Decimal
    liquidity_quality: Decimal
    cost_burden: Decimal
    reason_codes: tuple[str, ...]
    plain_korean_explanation: tuple[str, ...]
    management_policy: tuple[str, ...]
    main_eligible: bool
    shadow_eligible: bool

    def __post_init__(self) -> None:
        if self.expires_at_ms <= self.signal_time_ms:
            raise ValueError("후보 유효시간은 신호 시각보다 뒤여야 합니다.")
        if self.position_size <= 0 or self.minimum_quantity <= 0:
            raise ValueError("수량과 최소 수량은 양수여야 합니다.")
        if not self.take_profit_targets:
            raise ValueError("진입 전에 최소 하나의 익절 목표가 확정돼야 합니다.")
        target_fraction = sum(
            (target.quantity_fraction for target in self.take_profit_targets),
            start=Decimal(0),
        )
        if target_fraction != Decimal(1):
            raise ValueError("익절 목표의 수량 비율 합은 100%여야 합니다.")
        if self.direction is Side.LONG:
            if not self.initial_stop < self.planned_entry <= self.worst_allowed_entry:
                raise ValueError("롱 계획의 stop·entry·worst entry 구조가 잘못됐습니다.")
            if any(target.price <= self.planned_entry for target in self.take_profit_targets):
                raise ValueError("롱 익절 가격은 계획 진입가보다 높아야 합니다.")
        else:
            if not self.worst_allowed_entry <= self.planned_entry < self.initial_stop:
                raise ValueError("숏 계획의 worst entry·entry·stop 구조가 잘못됐습니다.")
            if any(target.price >= self.planned_entry for target in self.take_profit_targets):
                raise ValueError("숏 익절 가격은 계획 진입가보다 낮아야 합니다.")
        if self.max_planned_loss > self.risk_budget:
            raise ValueError("최대 계획손실은 위험예산을 넘을 수 없습니다.")

    @property
    def first_target(self) -> TakeProfitTarget:
        return self.take_profit_targets[0]

    def arbitration_key(self) -> tuple[Decimal, Decimal, Decimal, Decimal, int, str, str]:
        """더 좋은 후보가 정렬 앞쪽으로 오는 결정론적 순서를 반환한다."""

        return (
            -self.data_quality,
            -self.liquidity_quality,
            -self.net_reward_risk,
            self.cost_burden,
            self.expires_at_ms,
            self.symbol,
            f"{self.strategy_id}:{self.direction.value}",
        )


@dataclass(frozen=True, slots=True)
class PlanBuildResult:
    plan: CandidatePlan | None
    rejection_codes: tuple[str, ...]


class CandidatePlanner:
    """실행가능 호가와 위험·비용 상한을 통과한 후보만 고정한다."""

    def __init__(
        self,
        risk_manager: RiskManager | None = None,
        cost_model: CostModel | None = None,
        *,
        validity_ms: int = 1_500,
    ) -> None:
        self.risk_manager = risk_manager or RiskManager()
        self.cost_model = cost_model or CostModel()
        self.validity_ms = validity_ms

    def build(
        self,
        *,
        signal_event_id: str,
        run_id: str,
        venue: Venue,
        decision: CandidateDecision,
        snapshot: FeatureSnapshot,
        regime: Regime,
        book: BookSnapshot,
        instrument: Instrument,
        signal_time_ms: int,
        risk_state: RiskState,
        main_eligible: bool,
        shadow_eligible: bool,
        exit_style: ExitStyle = ExitStyle.REVERSION_70_30,
        strategy_version: str = "1",
    ) -> PlanBuildResult:
        if decision.status is not CandidateStatus.QUALIFIED:
            return PlanBuildResult(None, ("STRATEGY_NOT_QUALIFIED",))
        if (
            decision.planned_entry is None
            or decision.initial_stop is None
            or decision.take_profit is None
            or decision.net_reward_risk is None
        ):
            return PlanBuildResult(None, ("INCOMPLETE_STRATEGY_PLAN",))
        try:
            book.validate()
        except ValueError:
            return PlanBuildResult(None, ("BOOK_NOT_EXECUTABLE",))
        if book.symbol != instrument.symbol or book.venue is not venue:
            return PlanBuildResult(None, ("INSTRUMENT_BOOK_MISMATCH",))

        side = decision.side
        entry = book.asks[0][0] if side is Side.LONG else book.bids[0][0]
        spread = book.asks[0][0] - book.bids[0][0]
        noise_buffer = max(
            instrument.tick_size * Decimal(2),
            spread * Decimal("1.5"),
            entry * Decimal("0.0001"),
        )
        worst_entry = entry + noise_buffer if side is Side.LONG else entry - noise_buffer
        stop = decision.initial_stop
        final_target = decision.take_profit
        if side is Side.LONG and not stop < entry < final_target:
            return PlanBuildResult(None, ("LIVE_BOOK_INVALIDATES_LONG_STRUCTURE",))
        if side is Side.SHORT and not final_target < entry < stop:
            return PlanBuildResult(None, ("LIVE_BOOK_INVALIDATES_SHORT_STRUCTURE",))
        if side is Side.LONG and worst_entry >= final_target:
            return PlanBuildResult(None, ("WORST_ENTRY_REACHES_TARGET",))
        if side is Side.SHORT and worst_entry <= final_target:
            return PlanBuildResult(None, ("WORST_ENTRY_REACHES_TARGET",))

        targets = self._targets(
            exit_style=exit_style,
            side=side,
            entry=entry,
            worst_entry=worst_entry,
            stop=stop,
            final_target=final_target,
            micro_vwap=Decimal(str(snapshot.micro_vwap_10s)),
            expected_cost_bps=decision.expected_cost_bps,
        )
        executable_levels = book.asks if side is Side.LONG else book.bids
        executable_depth = sum(
            (
                quantity
                for price, quantity in executable_levels
                if (side is Side.LONG and price <= worst_entry)
                or (side is Side.SHORT and price >= worst_entry)
            ),
            start=Decimal(0),
        )
        entry_fee_per_unit = worst_entry * self.cost_model.fee_bps(
            entry=True, profile=CostProfile.BASE
        ) / Decimal(10_000)
        stop_fee_per_unit = stop * self.cost_model.fee_bps(
            entry=False, profile=CostProfile.BASE
        ) / Decimal(10_000)
        sizing = self.risk_manager.size(
            RiskSizingInput(
                equity=risk_state.current_equity,
                entry_price=worst_entry,
                stop_price=stop,
                entry_fee_per_unit=entry_fee_per_unit,
                stop_fee_per_unit=stop_fee_per_unit,
                p95_exit_slippage_per_unit=noise_buffer,
                quantity_step=instrument.quantity_step,
                minimum_quantity=instrument.minimum_quantity,
                executable_depth_quantity=executable_depth,
            )
        )
        if sizing.quantity is None or sizing.planned_loss is None:
            return PlanBuildResult(None, sizing.rejection_codes or ("RISK_SIZE_REJECTED",))
        quantity = sizing.quantity
        weighted_reward_per_unit = sum(
            (
                abs(target.price - entry) * target.quantity_fraction
                for target in targets
            ),
            start=Decimal(0),
        )
        gross_reward = weighted_reward_per_unit * quantity
        weighted_exit = sum(
            (target.price * target.quantity_fraction for target in targets),
            start=Decimal(0),
        )
        expected_fees = quantity * (
            entry_fee_per_unit
            + weighted_exit
            * self.cost_model.fee_bps(entry=False, profile=CostProfile.BASE)
            / Decimal(10_000)
        )
        expected_slippage = quantity * noise_buffer * Decimal("1.5")
        net_reward = gross_reward - expected_fees - expected_slippage
        net_risk = sizing.planned_loss
        if net_reward <= 0 or net_risk <= 0:
            return PlanBuildResult(None, ("NON_POSITIVE_NET_REWARD",))
        net_rr = (net_reward / net_risk).quantize(Decimal("0.0001"))
        if net_rr < Decimal("1.20"):
            return PlanBuildResult(None, ("LIVE_PLAN_INADEQUATE_NET_REWARD_RISK",))
        cost_burden = ((expected_fees + expected_slippage) / gross_reward).quantize(
            Decimal("0.0001")
        )
        signal_quality = min(
            Decimal(1),
            max(Decimal(0), decision.net_reward_risk / Decimal(3)),
        )
        depth_notional = executable_depth * entry
        liquidity_quality = min(Decimal(1), depth_notional / Decimal("5000"))
        explanation = decision.korean_explanation(instrument.symbol)
        plan = CandidatePlan(
            candidate_id=f"candidate-{uuid4().hex[:16]}",
            signal_event_id=signal_event_id,
            run_id=run_id,
            venue=venue,
            symbol=instrument.symbol,
            strategy_id=decision.strategy_id,
            strategy_version=strategy_version,
            exit_style=exit_style,
            direction=side,
            signal_time_ms=signal_time_ms,
            expires_at_ms=signal_time_ms + self.validity_ms,
            regime=regime,
            planned_entry=entry,
            worst_allowed_entry=worst_entry,
            initial_stop=stop,
            noise_buffer=noise_buffer,
            take_profit_targets=targets,
            position_size=quantity,
            quantity_step=instrument.quantity_step,
            minimum_quantity=instrument.minimum_quantity,
            executable_depth_quantity=executable_depth,
            risk_budget=sizing.risk_budget,
            max_planned_loss=sizing.planned_loss,
            gross_reward_usdt=gross_reward,
            expected_fees_usdt=expected_fees,
            expected_slippage_usdt=expected_slippage,
            net_reward_usdt=net_reward,
            net_risk_usdt=net_risk,
            net_reward_risk=net_rr,
            data_quality=Decimal(1) if snapshot.data_healthy else Decimal(0),
            signal_quality=signal_quality,
            liquidity_quality=liquidity_quality,
            cost_burden=cost_burden,
            reason_codes=decision.reason_codes,
            plain_korean_explanation=explanation,
            management_policy=(
                "NO_FIXED_TIME_EXIT",
                "FEE_ADJUSTED_BREAKEVEN_AFTER_TP1"
                if exit_style is ExitStyle.TREND_40_60
                else "STRUCTURAL_REVERSION_EXIT",
                "STOP_NEVER_WIDENS",
                "EXIT_ON_PERSISTENT_EDGE_DECAY",
            ),
            main_eligible=main_eligible,
            shadow_eligible=shadow_eligible,
        )
        return PlanBuildResult(plan, ())

    @staticmethod
    def _targets(
        *,
        exit_style: ExitStyle,
        side: Side,
        entry: Decimal,
        worst_entry: Decimal,
        stop: Decimal,
        final_target: Decimal,
        micro_vwap: Decimal,
        expected_cost_bps: Decimal,
    ) -> tuple[TakeProfitTarget, ...]:
        risk_distance = abs(worst_entry - stop)
        minimum_reward = entry * expected_cost_bps / Decimal(10_000) * Decimal(2)
        direction = Decimal(1) if side is Side.LONG else Decimal(-1)
        if exit_style is ExitStyle.REVERSION_70_30:
            structural_tp1 = entry + direction * risk_distance * Decimal("1.2")
            # QUALIFIED strategy decision always carries a structural target.
            # The 2.2R fallback is reserved for a future target-less policy.
            structural_tp2 = final_target
            candidate_tp1 = micro_vwap
            valid_micro_vwap = (
                entry + minimum_reward < candidate_tp1 < structural_tp2
                if side is Side.LONG
                else structural_tp2 < candidate_tp1 < entry - minimum_reward
            )
            if valid_micro_vwap:
                structural_tp1 = candidate_tp1
            return (
                TakeProfitTarget("TP1", structural_tp1, Decimal("0.70")),
                TakeProfitTarget("TP2", structural_tp2, Decimal("0.30")),
            )
        return (
            TakeProfitTarget(
                "TP1",
                entry + direction * risk_distance * Decimal("1.5"),
                Decimal("0.40"),
            ),
            TakeProfitTarget(
                "TP2",
                entry + direction * risk_distance * Decimal("3.0"),
                Decimal("0.60"),
            ),
        )
