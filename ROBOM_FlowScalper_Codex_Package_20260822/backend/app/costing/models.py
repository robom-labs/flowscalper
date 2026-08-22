"""실제 계정 수수료가 아닌 보수적 PAPER 비용·지연 가정을 정의한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class CostProfile(StrEnum):
    BASE = "BASE"
    STRESS = "STRESS"


@dataclass(frozen=True, slots=True)
class CostModel:
    name: str = "CONSERVATIVE_ASSUMED_V1"
    values_are_assumptions: bool = True
    entry_fee_bps: Decimal = Decimal("6")
    exit_fee_bps: Decimal = Decimal("6")
    additional_safety_bps: Decimal = Decimal("1")
    decision_to_arrival_latency_ms: int = 250
    cancel_latency_ms: int = 150
    stop_processing_latency_ms: int = 250
    stress_fee_multiplier: Decimal = Decimal("2")
    stress_latency_multiplier: Decimal = Decimal("2")

    def fee_bps(self, *, entry: bool, profile: CostProfile) -> Decimal:
        base = self.entry_fee_bps if entry else self.exit_fee_bps
        multiplier = self.stress_fee_multiplier if profile is CostProfile.STRESS else Decimal(1)
        return base * multiplier

    def fee(self, notional: Decimal, *, entry: bool, profile: CostProfile) -> Decimal:
        return notional * self.fee_bps(entry=entry, profile=profile) / Decimal(10_000)

    def arrival_latency_ms(self, profile: CostProfile) -> int:
        multiplier = self.stress_latency_multiplier if profile is CostProfile.STRESS else Decimal(1)
        return int(Decimal(self.decision_to_arrival_latency_ms) * multiplier)
