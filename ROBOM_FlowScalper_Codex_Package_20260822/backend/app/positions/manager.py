"""고정 120초 종료 없이 근거 건강과 안전 한계로 PAPER 포지션을 관리한다."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum

from backend.app.domain.models import Side
from backend.app.execution.models import ProtectedPosition


class StopWideningError(ValueError):
    """초기 또는 현재 stop을 불리하게 넓히려 할 때 발생한다."""


class ManagementAction(StrEnum):
    HOLD = "HOLD"
    HOLD_DATA_GAP = "HOLD_DATA_GAP"
    EXIT_EDGE_DECAY = "EXIT_EDGE_DECAY"
    EXIT_PROFIT_PROTECTION = "EXIT_PROFIT_PROTECTION"
    EXIT_EMERGENCY_STALE = "EXIT_EMERGENCY_STALE"
    EXIT_MAX_HOLD = "EXIT_MAX_HOLD"


@dataclass(frozen=True, slots=True)
class PositionHealth:
    structure_health: float
    flow_health: float
    microprice_alignment: float
    liquidity_health: float
    spread_health: float
    opposite_aggression: float
    data_health: float
    remaining_edge: Decimal
    current_r: Decimal
    mfe_r: Decimal
    mae_r: Decimal

    def validate(self) -> None:
        bounded = (
            self.structure_health,
            self.flow_health,
            self.microprice_alignment,
            self.liquidity_health,
            self.spread_health,
            self.opposite_aggression,
            self.data_health,
        )
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in bounded):
            raise ValueError("건강 벡터 구성요소는 0..1 범위의 유한값이어야 합니다.")
        for value in (self.remaining_edge, self.current_r, self.mfe_r, self.mae_r):
            if not value.is_finite():
                raise ValueError("R/edge 값은 유한해야 합니다.")


@dataclass(frozen=True, slots=True)
class PositionManagerConfig:
    edge_decay_grace_ms: int = 10_000
    edge_decay_persistence_ms: int = 3_000
    edge_decay_minimum_adverse_signals: int = 2
    emergency_stale_absolute_ms: int = 15 * 60 * 1000
    profit_protection_monitor_r: Decimal = Decimal("0.8")
    breakeven_tighten_r: Decimal = Decimal("1.0")
    breakeven_cost_bps: Decimal = Decimal("13")


@dataclass(frozen=True, slots=True)
class ManagementDecision:
    action: ManagementAction
    reason_codes: tuple[str, ...]
    proposed_stop: Decimal | None
    holding_ms: int


@dataclass(slots=True)
class PositionManager:
    config: PositionManagerConfig = field(default_factory=PositionManagerConfig)
    _edge_adverse_since_ms: dict[str, int] = field(default_factory=dict)

    def evaluate(
        self,
        position: ProtectedPosition,
        health: PositionHealth,
        *,
        now_ms: int,
        data_stale: bool = False,
        recovered_gap_duration_ms: int = 0,
        maximum_holding_ms: int | None = None,
    ) -> ManagementDecision:
        health.validate()
        holding_ms = max(0, now_ms - position.opened_ts_ms)
        proposed_stop = self._profit_protection_stop(position, health)
        if data_stale:
            return ManagementDecision(
                ManagementAction.HOLD_DATA_GAP,
                ("PRESERVE_TP_SL_WAIT_SAME_VENUE",),
                proposed_stop,
                holding_ms,
            )
        if recovered_gap_duration_ms >= self.config.emergency_stale_absolute_ms:
            return ManagementDecision(
                ManagementAction.EXIT_EMERGENCY_STALE,
                ("EMERGENCY_STALE_LIMIT",),
                proposed_stop,
                holding_ms,
            )
        if maximum_holding_ms is not None and holding_ms >= maximum_holding_ms:
            return ManagementDecision(
                ManagementAction.EXIT_MAX_HOLD,
                ("MAXIMUM_HOLDING_TIME_REACHED",),
                proposed_stop,
                holding_ms,
            )
        adverse_reasons = self._adverse_reasons(health)
        if (
            adverse_reasons
            and holding_ms < self.config.edge_decay_grace_ms
            and health.mfe_r < self.config.profit_protection_monitor_r
        ):
            self._edge_adverse_since_ms.pop(position.trade_id, None)
            return ManagementDecision(
                ManagementAction.HOLD,
                ("EDGE_DECAY_GRACE_ACTIVE",),
                proposed_stop,
                holding_ms,
            )
        if len(adverse_reasons) >= self.config.edge_decay_minimum_adverse_signals:
            adverse_since = self._edge_adverse_since_ms.setdefault(position.trade_id, now_ms)
            if now_ms - adverse_since >= self.config.edge_decay_persistence_ms:
                action = (
                    ManagementAction.EXIT_PROFIT_PROTECTION
                    if health.mfe_r >= self.config.profit_protection_monitor_r
                    else ManagementAction.EXIT_EDGE_DECAY
                )
                return ManagementDecision(action, adverse_reasons, proposed_stop, holding_ms)
            return ManagementDecision(
                ManagementAction.HOLD,
                ("EDGE_DECAY_CONFIRMING",),
                proposed_stop,
                holding_ms,
            )
        self._edge_adverse_since_ms.pop(position.trade_id, None)
        if adverse_reasons:
            return ManagementDecision(
                ManagementAction.HOLD,
                ("EDGE_DECAY_INSUFFICIENT_CONFIRMATION", *adverse_reasons),
                proposed_stop,
                holding_ms,
            )
        return ManagementDecision(
            ManagementAction.HOLD,
            ("ENTRY_THESIS_HEALTHY",),
            proposed_stop,
            holding_ms,
        )

    def tighten_stop(
        self,
        position: ProtectedPosition,
        proposed_stop: Decimal,
    ) -> ProtectedPosition:
        if position.side is Side.LONG:
            if proposed_stop < position.current_stop or proposed_stop < position.initial_stop:
                raise StopWideningError("롱 stop은 아래로 넓힐 수 없습니다.")
        elif proposed_stop > position.current_stop or proposed_stop > position.initial_stop:
            raise StopWideningError("숏 stop은 위로 넓힐 수 없습니다.")
        return replace(position, current_stop=proposed_stop)

    def _profit_protection_stop(
        self,
        position: ProtectedPosition,
        health: PositionHealth,
    ) -> Decimal | None:
        if health.mfe_r < self.config.breakeven_tighten_r:
            return None
        adjustment = (
            position.entry_fill.average_price * self.config.breakeven_cost_bps / Decimal(10_000)
        )
        if position.side is Side.LONG:
            return max(position.current_stop, position.entry_fill.average_price + adjustment)
        return min(position.current_stop, position.entry_fill.average_price - adjustment)

    @staticmethod
    def _adverse_reasons(health: PositionHealth) -> tuple[str, ...]:
        reasons: list[str] = []
        if health.structure_health < 0.35:
            reasons.append("STRUCTURE_INVALIDATED")
        if health.flow_health < 0.25:
            reasons.append("FLOW_DECAY")
        if health.microprice_alignment < 0.25:
            reasons.append("MICROPRICE_ADVERSE")
        if health.liquidity_health < 0.25:
            reasons.append("SUPPORTING_LIQUIDITY_GONE")
        if health.spread_health < 0.25:
            reasons.append("SPREAD_COST_INVALIDATED")
        if health.opposite_aggression > 0.75:
            reasons.append("OPPOSITE_AGGRESSION_EFFICIENT")
        if health.data_health < 0.5:
            reasons.append("DATA_HEALTH_DEGRADED")
        if health.remaining_edge <= 0:
            reasons.append("REMAINING_EDGE_NON_POSITIVE")
        return tuple(reasons)
