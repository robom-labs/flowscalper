"""1,000 USDT PAPER 계좌의 수량·손실·drawdown·cooldown을 보수적으로 제한한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    risk_per_trade_fraction: Decimal = Decimal("0.001")
    max_open_positions: int = 1
    max_daily_trades: int = 12
    maximum_total_open_risk_fraction: Decimal = Decimal("0.001")
    daily_loss_limit_fraction: Decimal = Decimal("0.005")
    weekly_loss_limit_fraction: Decimal = Decimal("0.015")
    maximum_drawdown_fraction: Decimal = Decimal("0.03")
    maximum_gross_notional_fraction: Decimal = Decimal("0.50")
    maximum_order_fraction_of_executable_depth: Decimal = Decimal("0.02")


STRATEGY_LEAGUE_RISK_LIMITS = RiskLimits(
    risk_per_trade_fraction=Decimal("0.005"),
    max_open_positions=3,
    maximum_total_open_risk_fraction=Decimal("0.015"),
    daily_loss_limit_fraction=Decimal("0.02"),
    weekly_loss_limit_fraction=Decimal("0.05"),
    maximum_drawdown_fraction=Decimal("0.08"),
    maximum_gross_notional_fraction=Decimal("5.0"),
    maximum_order_fraction_of_executable_depth=Decimal("0.02"),
)


@dataclass(frozen=True, slots=True)
class RiskSizingInput:
    equity: Decimal
    entry_price: Decimal
    stop_price: Decimal
    entry_fee_per_unit: Decimal
    stop_fee_per_unit: Decimal
    p95_exit_slippage_per_unit: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    executable_depth_quantity: Decimal


@dataclass(frozen=True, slots=True)
class RiskSizingResult:
    quantity: Decimal | None
    risk_budget: Decimal
    planned_loss: Decimal | None
    rejection_codes: tuple[str, ...]


@dataclass(slots=True)
class RiskState:
    starting_equity: Decimal = Decimal("1000")
    current_equity: Decimal = Decimal("1000")
    peak_equity: Decimal = Decimal("1000")
    realized_today: Decimal = Decimal(0)
    realized_week: Decimal = Decimal(0)
    daily_trade_count: int = 0
    open_positions: int = 0
    open_planned_risk: Decimal = Decimal(0)
    pending_planned_risk: Decimal = Decimal(0)
    gross_notional: Decimal = Decimal(0)
    pending_notional: Decimal = Decimal(0)
    maximum_effective_leverage: Decimal = Decimal(0)
    global_consecutive_losses: int = 0
    paused: bool = False
    faulted: bool = False
    cooldowns_until_ms: dict[str, int] = field(default_factory=dict)

    @property
    def drawdown_fraction(self) -> Decimal:
        if self.peak_equity <= 0:
            return Decimal(0)
        return max(Decimal(0), (self.peak_equity - self.current_equity) / self.peak_equity)


class RiskManager:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def size(self, values: RiskSizingInput) -> RiskSizingResult:
        risk_budget = values.equity * self.limits.risk_per_trade_fraction
        loss_per_unit = (
            abs(values.entry_price - values.stop_price)
            + values.entry_fee_per_unit
            + values.stop_fee_per_unit
            + values.p95_exit_slippage_per_unit
        )
        if loss_per_unit <= 0 or values.quantity_step <= 0:
            return RiskSizingResult(None, risk_budget, None, ("INVALID_RISK_INPUT",))
        raw_quantity = risk_budget / loss_per_unit
        exposure_cap = (
            values.equity * self.limits.maximum_gross_notional_fraction / values.entry_price
        )
        depth_cap = (
            values.executable_depth_quantity
            * self.limits.maximum_order_fraction_of_executable_depth
        )
        capped = min(raw_quantity, exposure_cap, depth_cap)
        quantity = (capped / values.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        quantity *= values.quantity_step
        if quantity < values.minimum_quantity:
            return RiskSizingResult(None, risk_budget, None, ("QUANTITY_BELOW_MINIMUM",))
        planned_loss = quantity * loss_per_unit
        if planned_loss > risk_budget:
            return RiskSizingResult(
                None,
                risk_budget,
                planned_loss,
                ("PLANNED_LOSS_EXCEEDS_BUDGET",),
            )
        return RiskSizingResult(quantity, risk_budget, planned_loss, ())

    def entry_rejections(self, state: RiskState, key: str, now_ms: int) -> tuple[str, ...]:
        reasons: list[str] = []
        if state.paused:
            reasons.append("RUN_PAUSED")
        if state.faulted:
            reasons.append("RUN_FAULTED")
        if state.open_positions >= self.limits.max_open_positions:
            reasons.append("MAX_OPEN_POSITIONS")
        if state.daily_trade_count >= self.limits.max_daily_trades:
            reasons.append("MAX_DAILY_TRADES")
        if -state.realized_today >= state.current_equity * self.limits.daily_loss_limit_fraction:
            reasons.append("DAILY_LOSS_LOCK")
        if -state.realized_week >= state.current_equity * self.limits.weekly_loss_limit_fraction:
            reasons.append("WEEKLY_LOSS_LOCK")
        if state.drawdown_fraction >= self.limits.maximum_drawdown_fraction:
            reasons.append("DRAWDOWN_LOCK")
        if state.cooldowns_until_ms.get(key, 0) > now_ms:
            reasons.append("COOLDOWN_ACTIVE")
        if state.cooldowns_until_ms.get("GLOBAL", 0) > now_ms:
            reasons.append("GLOBAL_COOLDOWN_ACTIVE")
        return tuple(reasons)

    def pending_rejections(
        self,
        state: RiskState,
        *,
        planned_risk: Decimal,
        planned_notional: Decimal,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        total_risk_limit = (
            state.current_equity * self.limits.maximum_total_open_risk_fraction
        )
        if state.open_planned_risk + state.pending_planned_risk + planned_risk > total_risk_limit:
            reasons.append("MAXIMUM_TOTAL_OPEN_RISK")
        notional_limit = state.current_equity * self.limits.maximum_gross_notional_fraction
        if state.gross_notional + state.pending_notional + planned_notional > notional_limit:
            reasons.append("MAXIMUM_GROSS_NOTIONAL")
        return tuple(reasons)

    @staticmethod
    def reserve_pending(
        state: RiskState,
        planned_risk: Decimal,
        planned_notional: Decimal = Decimal(0),
    ) -> None:
        if planned_risk < 0 or planned_notional < 0:
            raise ValueError("대기 계획위험과 명목금액은 음수일 수 없습니다.")
        state.pending_planned_risk += planned_risk
        state.pending_notional += planned_notional

    @staticmethod
    def release_pending(
        state: RiskState,
        planned_risk: Decimal,
        planned_notional: Decimal = Decimal(0),
    ) -> None:
        if (
            planned_risk < 0
            or planned_risk > state.pending_planned_risk
            or planned_notional < 0
            or planned_notional > state.pending_notional
        ):
            raise RuntimeError("대기 계획위험과 명목금액 회계가 일치하지 않습니다.")
        state.pending_planned_risk -= planned_risk
        state.pending_notional -= planned_notional

    def record_open(
        self,
        state: RiskState,
        *,
        planned_risk: Decimal = Decimal(0),
        notional: Decimal = Decimal(0),
        effective_leverage: Decimal = Decimal(0),
    ) -> None:
        if state.open_positions >= self.limits.max_open_positions:
            raise RuntimeError("동시 포지션 상한을 초과할 수 없습니다.")
        state.open_positions += 1
        state.daily_trade_count += 1
        state.open_planned_risk += planned_risk
        state.gross_notional += notional
        aggregate_effective_leverage = (
            state.gross_notional / state.current_equity
            if state.current_equity > 0
            else Decimal(0)
        )
        state.maximum_effective_leverage = max(
            state.maximum_effective_leverage,
            effective_leverage,
            aggregate_effective_leverage,
        )

    def record_close(
        self,
        state: RiskState,
        net_pnl: Decimal,
        *,
        key: str,
        now_ms: int,
        planned_risk: Decimal = Decimal(0),
        notional: Decimal = Decimal(0),
    ) -> None:
        if state.open_positions <= 0:
            raise RuntimeError("열린 포지션 없이 종료를 기록할 수 없습니다.")
        state.open_positions -= 1
        state.open_planned_risk = max(Decimal(0), state.open_planned_risk - planned_risk)
        state.gross_notional = max(Decimal(0), state.gross_notional - notional)
        state.current_equity += net_pnl
        state.peak_equity = max(state.peak_equity, state.current_equity)
        state.realized_today += net_pnl
        state.realized_week += net_pnl
        if net_pnl < 0:
            state.global_consecutive_losses += 1
            if state.global_consecutive_losses >= 3:
                state.cooldowns_until_ms["GLOBAL"] = now_ms + 60 * 60 * 1000
            state.cooldowns_until_ms[key] = now_ms + 2 * 60 * 60 * 1000
        else:
            state.global_consecutive_losses = 0
