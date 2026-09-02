"""전략 후보의 구조적 가격 계획, 비용 게이트와 설명 계약을 정의한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from backend.app.domain.models import Side


class CandidateStatus(StrEnum):
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"


class RunnerManagement(StrEnum):
    """TP1 뒤 남은 수량을 어떤 가격 근거로 관리할지 고정한다."""

    FIXED_SECOND_TARGET = "FIXED_SECOND_TARGET"
    TP1_ATR_CHANDELIER = "TP1_ATR_CHANDELIER"
    TP1_STRUCTURE_DISTANCE = "TP1_STRUCTURE_DISTANCE"


@dataclass(frozen=True, slots=True)
class StructuralExitPlan:
    """진입 전에 확정한 구조 기반 TP1·TP2와 수익보호 근거다."""

    take_profit_1: Decimal
    take_profit_2: Decimal
    stop_rationale_ko: str
    take_profit_1_rationale_ko: str
    take_profit_2_rationale_ko: str
    reference_timeframes_ko: tuple[str, ...]
    runner_management: RunnerManagement
    trailing_distance: Decimal | None = None
    trailing_atr: Decimal | None = None
    trailing_structure_stop: Decimal | None = None
    trailing_reference_ts_ms: int | None = None
    trailing_reference_interval_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.take_profit_1 <= 0 or self.take_profit_2 <= 0:
            raise ValueError("구조 기반 익절 가격은 양수여야 합니다.")
        if not all(
            value.strip()
            for value in (
                self.stop_rationale_ko,
                self.take_profit_1_rationale_ko,
                self.take_profit_2_rationale_ko,
            )
        ):
            raise ValueError("손절·익절 가격의 한국어 근거가 필요합니다.")
        if not self.reference_timeframes_ko:
            raise ValueError("구조 기반 종료 계획에는 확인 시간구간이 필요합니다.")
        if self.runner_management is RunnerManagement.TP1_STRUCTURE_DISTANCE:
            if self.trailing_distance is None or self.trailing_distance <= 0:
                raise ValueError("구조폭 trailing에는 양수 거리가 필요합니다.")
        elif self.trailing_distance is not None:
            raise ValueError("구조폭 trailing이 아닌 계획에 trailing 거리가 남았습니다.")
        if self.runner_management is RunnerManagement.TP1_ATR_CHANDELIER:
            if self.trailing_atr is None or self.trailing_atr <= 0:
                raise ValueError("ATR trailing 계획에는 양수 ATR이 필요합니다.")
            if (
                self.trailing_reference_ts_ms is None
                or self.trailing_reference_interval_seconds is None
                or self.trailing_reference_interval_seconds <= 0
            ):
                raise ValueError("ATR trailing 계획에는 완료봉 참조가 필요합니다.")
        elif any(
            value is not None
            for value in (
                self.trailing_atr,
                self.trailing_structure_stop,
                self.trailing_reference_ts_ms,
                self.trailing_reference_interval_seconds,
            )
        ):
            raise ValueError("ATR trailing이 아닌 계획에 완료봉 참조가 남았습니다.")


@dataclass(frozen=True, slots=True)
class PlanInputs:
    entry: Decimal
    structural_stop: Decimal | None
    target: Decimal | None
    expected_total_cost_bps: Decimal
    minimum_net_reward_risk: Decimal = Decimal("1.20")
    maximum_cost_fraction_of_target: Decimal = Decimal("0.30")
    structural_exit: StructuralExitPlan | None = None


@dataclass(frozen=True, slots=True)
class CostedPlan:
    entry: Decimal
    stop: Decimal
    target: Decimal
    expected_cost: Decimal
    net_reward_risk: Decimal


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    strategy_id: str
    side: Side
    status: CandidateStatus
    reason_codes: tuple[str, ...]
    rejection_codes: tuple[str, ...]
    planned_entry: Decimal | None
    initial_stop: Decimal | None
    take_profit: Decimal | None
    expected_cost_bps: Decimal
    net_reward_risk: Decimal | None
    calibration_status: str = "CALIBRATING"
    tp_probability: None = None
    structural_exit: StructuralExitPlan | None = None

    def korean_explanation(self, symbol: str) -> tuple[str, ...]:
        if self.status is CandidateStatus.REJECTED:
            return (
                f"{symbol} 진입 거부",
                *(f"- {code}" for code in self.rejection_codes),
            )
        direction = "롱" if self.side is Side.LONG else "숏"
        explanation = [
            f"{symbol} {direction} 후보",
            *(f"- {code}" for code in self.reason_codes),
            f"- 예상 총비용 {self.expected_cost_bps}bp",
            f"- 구조적 순손익비 {self.net_reward_risk}",
        ]
        if self.structural_exit is not None:
            explanation.extend(
                (
                    f"- 손절 근거: {self.structural_exit.stop_rationale_ko}",
                    f"- 1차 익절 근거: {self.structural_exit.take_profit_1_rationale_ko}",
                    f"- 2차 익절 근거: {self.structural_exit.take_profit_2_rationale_ko}",
                    "- 확인 구간: " + " · ".join(self.structural_exit.reference_timeframes_ko),
                )
            )
        return tuple(explanation)


def costed_plan(side: Side, inputs: PlanInputs) -> tuple[CostedPlan | None, tuple[str, ...]]:
    if inputs.structural_stop is None:
        return None, ("NO_STRUCTURAL_STOP",)
    if inputs.target is None:
        return None, ("NO_VIABLE_TARGET",)
    entry = inputs.entry
    stop = inputs.structural_stop
    target = inputs.target
    if inputs.structural_exit is not None:
        structural = inputs.structural_exit
        if target != structural.take_profit_2:
            return None, ("STRUCTURAL_TP2_MISMATCH",)
        if side is Side.LONG and not entry < structural.take_profit_1 < target:
            return None, ("INVALID_LONG_STRUCTURAL_TARGET_ORDER",)
        if side is Side.SHORT and not target < structural.take_profit_1 < entry:
            return None, ("INVALID_SHORT_STRUCTURAL_TARGET_ORDER",)
    if side is Side.LONG and not stop < entry < target:
        return None, ("INVALID_LONG_PRICE_STRUCTURE",)
    if side is Side.SHORT and not target < entry < stop:
        return None, ("INVALID_SHORT_PRICE_STRUCTURE",)
    gross_reward = abs(target - entry)
    gross_risk = abs(entry - stop)
    expected_cost = entry * inputs.expected_total_cost_bps / Decimal(10_000)
    if expected_cost >= gross_reward:
        return None, ("COST_EXCEEDS_TARGET",)
    net_reward = gross_reward - expected_cost
    net_risk = gross_risk + expected_cost
    net_reward_risk = net_reward / net_risk
    rejections: list[str] = []
    if expected_cost / gross_reward > inputs.maximum_cost_fraction_of_target:
        rejections.append("COST_FRACTION_TOO_HIGH")
    if net_reward_risk < inputs.minimum_net_reward_risk:
        rejections.append("INADEQUATE_NET_REWARD_RISK")
    if rejections:
        return None, tuple(rejections)
    return (
        CostedPlan(
            entry=entry,
            stop=stop,
            target=target,
            expected_cost=expected_cost,
            net_reward_risk=net_reward_risk.quantize(Decimal("0.0001")),
        ),
        (),
    )
