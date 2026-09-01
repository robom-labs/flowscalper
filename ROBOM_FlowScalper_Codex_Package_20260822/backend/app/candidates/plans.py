"""전략 신호를 체결 전 고정되는 불변 PAPER 거래계획으로 변환한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

from backend.app.costing import CostModel, CostProfile
from backend.app.domain.market import Instrument
from backend.app.domain.models import Side, Venue
from backend.app.execution.models import BookSnapshot
from backend.app.execution.trailing import TrailingModel, TrailingPolicy
from backend.app.features import FeatureSnapshot
from backend.app.regime import Regime
from backend.app.risk import RiskManager, RiskSizingInput, RiskState
from backend.app.strategies.base import CandidateDecision, CandidateStatus
from backend.app.strategies.registry import ExitStyle

_ATR_TRAILING_MODELS = {
    TrailingModel.ATR_CHANDELIER,
    TrailingModel.CHANDELIER_STRUCTURE,
    TrailingModel.EDGE_ADAPTIVE,
}
_STRUCTURE_TRAILING_MODELS = {
    TrailingModel.CHANDELIER_STRUCTURE,
    TrailingModel.STRUCTURE,
}
_COMPLETED_REFERENCE_MODELS = _ATR_TRAILING_MODELS | _STRUCTURE_TRAILING_MODELS


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
class SharedCapitalArbitrationEvidence:
    """Shared Capital의 V6 중재에 쓰는 사전등록 evidence만 보관한다."""

    evidence_tier: int = 0
    stress_cost_adjusted_expectancy_usdt: Decimal | None = None
    cost_coverage: Decimal | None = None
    diversification_score: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.evidence_tier < 0:
            raise ValueError("evidence tier는 0 이상이어야 합니다.")
        if self.cost_coverage is not None and self.cost_coverage < 0:
            raise ValueError("cost coverage는 0 이상이어야 합니다.")
        if not Decimal(0) <= self.diversification_score <= Decimal(1):
            raise ValueError("diversification score는 0 이상 1 이하여야 합니다.")

    def ranking_prefix(self) -> tuple[int, Decimal, Decimal]:
        missing = Decimal("-Infinity")
        return (
            -self.evidence_tier,
            -(
                self.stress_cost_adjusted_expectancy_usdt
                if self.stress_cost_adjusted_expectancy_usdt is not None
                else missing
            ),
            -(self.cost_coverage if self.cost_coverage is not None else missing),
        )


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
    maximum_holding_ms: int | None
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
    shared_capital_evidence: SharedCapitalArbitrationEvidence = field(
        default_factory=SharedCapitalArbitrationEvidence
    )
    trailing_policy: TrailingPolicy | None = None
    trailing_atr: Decimal | None = None
    trailing_structure_stop: Decimal | None = None
    trailing_reference_ts_ms: int | None = None
    trailing_reference_interval_seconds: int | None = None
    selected_margin_leverage: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if self.expires_at_ms <= self.signal_time_ms:
            raise ValueError("후보 유효시간은 신호 시각보다 뒤여야 합니다.")
        if self.maximum_holding_ms is not None and self.maximum_holding_ms <= 0:
            raise ValueError("최대 보유시간은 양수여야 합니다.")
        if self.position_size <= 0 or self.minimum_quantity <= 0:
            raise ValueError("수량과 최소 수량은 양수여야 합니다.")
        if (
            not self.selected_margin_leverage.is_finite()
            or not Decimal(1) <= self.selected_margin_leverage <= Decimal(100)
        ):
            raise ValueError("PAPER 선택 레버리지는 1배 이상 100배 이하여야 합니다.")
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
        if self.trailing_policy is None and any(
            value is not None
            for value in (
                self.trailing_atr,
                self.trailing_structure_stop,
                self.trailing_reference_ts_ms,
                self.trailing_reference_interval_seconds,
            )
        ):
            raise ValueError("trailing policy 없는 계획에 trailing 참조값이 남았습니다.")
        if (
            self.trailing_policy is not None
            and self.trailing_policy.model not in _ATR_TRAILING_MODELS
            and self.trailing_atr is not None
        ):
            raise ValueError("비ATR trailing 계획에 ATR 참조값이 남았습니다.")
        if (
            self.trailing_policy is not None
            and self.trailing_policy.model not in _STRUCTURE_TRAILING_MODELS
            and self.trailing_structure_stop is not None
        ):
            raise ValueError("비구조 trailing 계획에 구조 stop이 남았습니다.")
        if (
            self.trailing_policy is not None
            and self.trailing_policy.model not in _COMPLETED_REFERENCE_MODELS
            and (
                self.trailing_reference_ts_ms is not None
                or self.trailing_reference_interval_seconds is not None
            )
        ):
            raise ValueError("완료봉을 쓰지 않는 trailing 계획에 참조시각이 남았습니다.")
        if self.trailing_policy is not None and self.trailing_policy.model in _ATR_TRAILING_MODELS:
            if self.trailing_atr is None or self.trailing_atr <= 0:
                raise ValueError("ATR trailing 계획에는 진입 전 완성봉 ATR이 필요합니다.")
        if (
            self.trailing_policy is not None
            and self.trailing_policy.model in _STRUCTURE_TRAILING_MODELS
            and (self.trailing_structure_stop is None or self.trailing_structure_stop <= 0)
        ):
            raise ValueError("구조 trailing 계획에는 진입 전 완성봉 stop이 필요합니다.")
        if (
            self.trailing_policy is not None
            and self.trailing_policy.model in _COMPLETED_REFERENCE_MODELS
        ):
            if (
                self.trailing_reference_ts_ms is None
                or self.trailing_reference_ts_ms > self.signal_time_ms
                or self.trailing_reference_interval_seconds is None
                or self.trailing_reference_interval_seconds <= 0
            ):
                raise ValueError("trailing 참조봉은 신호시각 전에 완성돼야 합니다.")
            reference_age_ms = self.signal_time_ms - self.trailing_reference_ts_ms
            if reference_age_ms > self.trailing_reference_interval_seconds * 1_000:
                raise ValueError("trailing 완료봉 참조가 한 시간구간보다 오래됐습니다.")
        if self.trailing_structure_stop is not None and self.trailing_structure_stop <= 0:
            raise ValueError("trailing 구조 stop은 양수여야 합니다.")

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

    def shared_capital_arbitration_key(
        self,
    ) -> tuple[int, Decimal, Decimal, Decimal, int, Decimal, str, str]:
        """V6 우선순위로 Shared Capital 후보를 정렬하며 raw 승률은 사용하지 않는다."""

        evidence_tier, stress_expectancy, cost_coverage = (
            self.shared_capital_evidence.ranking_prefix()
        )
        return (
            evidence_tier,
            stress_expectancy,
            cost_coverage,
            -self.liquidity_quality,
            -self.signal_time_ms,
            -self.shared_capital_evidence.diversification_score,
            self.strategy_id,
            self.candidate_id,
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
        trend_take_profit_1_r: Decimal = Decimal("1.5"),
        trend_take_profit_2_r: Decimal = Decimal("3.0"),
        maximum_holding_ms: int | None = None,
        edge_decay_enabled: bool = False,
        strategy_version: str = "1",
        trailing_policy: TrailingPolicy | None = None,
        trailing_atr: Decimal | None = None,
        trailing_structure_stop: Decimal | None = None,
        trailing_reference_ts_ms: int | None = None,
        trailing_reference_interval_seconds: int | None = None,
        take_profit_targets_override: tuple[TakeProfitTarget, ...] | None = None,
        candidate_id_override: str | None = None,
        shared_capital_evidence: SharedCapitalArbitrationEvidence | None = None,
    ) -> PlanBuildResult:
        if decision.status is not CandidateStatus.QUALIFIED:
            return PlanBuildResult(None, ("STRATEGY_NOT_QUALIFIED",))
        if trend_take_profit_1_r <= 0 or trend_take_profit_2_r <= trend_take_profit_1_r:
            return PlanBuildResult(None, ("INVALID_TREND_TAKE_PROFIT_MULTIPLES",))
        if take_profit_targets_override is not None and not take_profit_targets_override:
            return PlanBuildResult(None, ("EMPTY_TAKE_PROFIT_TARGET_OVERRIDE",))
        if candidate_id_override is not None and not candidate_id_override.strip():
            return PlanBuildResult(None, ("INVALID_CANDIDATE_ID_OVERRIDE",))
        if (
            decision.planned_entry is None
            or decision.initial_stop is None
            or decision.take_profit is None
            or decision.net_reward_risk is None
        ):
            return PlanBuildResult(None, ("INCOMPLETE_STRATEGY_PLAN",))
        trailing_reference_values = (
            trailing_atr,
            trailing_structure_stop,
            trailing_reference_ts_ms,
            trailing_reference_interval_seconds,
        )
        if trailing_policy is None and any(
            value is not None for value in trailing_reference_values
        ):
            return PlanBuildResult(None, ("TRAILING_POLICY_MISSING",))
        if (
            trailing_policy is not None
            and trailing_policy.model not in _ATR_TRAILING_MODELS
            and trailing_atr is not None
        ):
            return PlanBuildResult(None, ("UNEXPECTED_TRAILING_ATR_REFERENCE",))
        if (
            trailing_policy is not None
            and trailing_policy.model not in _STRUCTURE_TRAILING_MODELS
            and trailing_structure_stop is not None
        ):
            return PlanBuildResult(None, ("UNEXPECTED_TRAILING_STRUCTURE_REFERENCE",))
        if (
            trailing_policy is not None
            and trailing_policy.model not in _COMPLETED_REFERENCE_MODELS
            and (
                trailing_reference_ts_ms is not None
                or trailing_reference_interval_seconds is not None
            )
        ):
            return PlanBuildResult(None, ("UNEXPECTED_TRAILING_CANDLE_REFERENCE",))
        if (
            trailing_policy is not None
            and trailing_policy.model in _ATR_TRAILING_MODELS
            and (trailing_atr is None or trailing_atr <= 0)
        ):
            return PlanBuildResult(None, ("TRAILING_COMPLETED_CANDLE_ATR_MISSING",))
        if (
            trailing_policy is not None
            and trailing_policy.model in _STRUCTURE_TRAILING_MODELS
            and (trailing_structure_stop is None or trailing_structure_stop <= 0)
        ):
            return PlanBuildResult(None, ("TRAILING_COMPLETED_STRUCTURE_MISSING",))
        if (
            trailing_policy is not None
            and trailing_policy.model in _COMPLETED_REFERENCE_MODELS
            and (
                trailing_reference_ts_ms is None
                or trailing_reference_ts_ms > signal_time_ms
                or trailing_reference_interval_seconds is None
                or trailing_reference_interval_seconds <= 0
            )
        ):
            return PlanBuildResult(None, ("TRAILING_COMPLETED_CANDLE_REFERENCE_MISSING",))
        if (
            trailing_policy is not None
            and trailing_policy.model in _COMPLETED_REFERENCE_MODELS
            and trailing_reference_ts_ms is not None
            and trailing_reference_interval_seconds is not None
            and signal_time_ms - trailing_reference_ts_ms
            > trailing_reference_interval_seconds * 1_000
        ):
            return PlanBuildResult(None, ("TRAILING_COMPLETED_CANDLE_REFERENCE_STALE",))
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

        targets = take_profit_targets_override or self._targets(
            exit_style=exit_style,
            side=side,
            entry=entry,
            worst_entry=worst_entry,
            stop=stop,
            final_target=final_target,
            micro_vwap=Decimal(str(snapshot.micro_vwap_10s)),
            expected_cost_bps=decision.expected_cost_bps,
            trend_take_profit_1_r=trend_take_profit_1_r,
            trend_take_profit_2_r=trend_take_profit_2_r,
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
        entry_fee_per_unit = (
            worst_entry
            * self.cost_model.fee_bps(entry=True, profile=CostProfile.BASE)
            / Decimal(10_000)
        )
        stop_fee_per_unit = (
            stop * self.cost_model.fee_bps(entry=False, profile=CostProfile.BASE) / Decimal(10_000)
        )
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
            (abs(target.price - entry) * target.quantity_fraction for target in targets),
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
            candidate_id=candidate_id_override or f"candidate-{uuid4().hex[:16]}",
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
            maximum_holding_ms=maximum_holding_ms,
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
                (
                    f"SAFETY_MAX_HOLD_{maximum_holding_ms // 1_000}S"
                    if maximum_holding_ms is not None
                    else "NO_TIME_BASED_EXIT_TP_SL_ONLY"
                ),
                "FEE_ADJUSTED_BREAKEVEN_AFTER_TP1"
                if exit_style is ExitStyle.TREND_40_60
                else "STRUCTURAL_REVERSION_EXIT",
                "STOP_NEVER_WIDENS",
                (
                    "EXIT_ON_PERSISTENT_EDGE_DECAY"
                    if edge_decay_enabled
                    else "NO_GENERAL_EDGE_DECAY_TP_SL_ONLY"
                ),
            ),
            main_eligible=main_eligible,
            shadow_eligible=shadow_eligible,
            shared_capital_evidence=(
                shared_capital_evidence
                if shared_capital_evidence is not None
                else SharedCapitalArbitrationEvidence()
            ),
            trailing_policy=trailing_policy,
            trailing_atr=trailing_atr,
            trailing_structure_stop=trailing_structure_stop,
            trailing_reference_ts_ms=trailing_reference_ts_ms,
            trailing_reference_interval_seconds=trailing_reference_interval_seconds,
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
        trend_take_profit_1_r: Decimal,
        trend_take_profit_2_r: Decimal,
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
                entry + direction * risk_distance * trend_take_profit_1_r,
                Decimal("0.40"),
            ),
            TakeProfitTarget(
                "TP2",
                entry + direction * risk_distance * trend_take_profit_2_r,
                Decimal("0.60"),
            ),
        )
