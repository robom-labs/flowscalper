"""전략 후보의 구조적 가격 계획, 비용 게이트와 설명 계약을 정의한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from backend.app.domain.models import Side


class CandidateStatus(StrEnum):
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class PlanInputs:
    entry: Decimal
    structural_stop: Decimal | None
    target: Decimal | None
    expected_total_cost_bps: Decimal
    minimum_net_reward_risk: Decimal = Decimal("1.20")
    maximum_cost_fraction_of_target: Decimal = Decimal("0.30")


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

    def korean_explanation(self, symbol: str) -> tuple[str, ...]:
        if self.status is CandidateStatus.REJECTED:
            return (
                f"{symbol} 진입 거부",
                *(f"- {code}" for code in self.rejection_codes),
            )
        direction = "롱" if self.side is Side.LONG else "숏"
        return (
            f"{symbol} {direction} 후보",
            *(f"- {code}" for code in self.reason_codes),
            f"- 예상 총비용 {self.expected_cost_bps}bp",
            f"- 구조적 순손익비 {self.net_reward_risk}",
        )


def costed_plan(side: Side, inputs: PlanInputs) -> tuple[CostedPlan | None, tuple[str, ...]]:
    if inputs.structural_stop is None:
        return None, ("NO_STRUCTURAL_STOP",)
    if inputs.target is None:
        return None, ("NO_VIABLE_TARGET",)
    entry = inputs.entry
    stop = inputs.structural_stop
    target = inputs.target
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
