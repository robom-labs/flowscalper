"""1,000 USDT PAPER 계좌의 수량·손실·drawdown·cooldown을 보수적으로 제한한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    risk_per_trade_fraction: Decimal = Decimal("0.001")
    max_open_positions: int = 1
    max_daily_trades: int = 12
    daily_loss_limit_usdt: Decimal = Decimal("5")
    weekly_loss_limit_usdt: Decimal = Decimal("15")
    maximum_drawdown_fraction: Decimal = Decimal("0.03")
    maximum_gross_notional_fraction: Decimal = Decimal("0.50")
    maximum_order_fraction_of_executable_depth: Decimal = Decimal("0.02")


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
        if -state.realized_today >= self.limits.daily_loss_limit_usdt:
            reasons.append("DAILY_LOSS_LOCK")
        if -state.realized_week >= self.limits.weekly_loss_limit_usdt:
            reasons.append("WEEKLY_LOSS_LOCK")
        if state.drawdown_fraction >= self.limits.maximum_drawdown_fraction:
            reasons.append("DRAWDOWN_LOCK")
        if state.cooldowns_until_ms.get(key, 0) > now_ms:
            reasons.append("COOLDOWN_ACTIVE")
        if state.cooldowns_until_ms.get("GLOBAL", 0) > now_ms:
            reasons.append("GLOBAL_COOLDOWN_ACTIVE")
        return tuple(reasons)

    def record_open(self, state: RiskState) -> None:
        if state.open_positions >= self.limits.max_open_positions:
            raise RuntimeError("동시 포지션 상한을 초과할 수 없습니다.")
        state.open_positions += 1
        state.daily_trade_count += 1

    def record_close(
        self,
        state: RiskState,
        net_pnl: Decimal,
        *,
        key: str,
        now_ms: int,
    ) -> None:
        if state.open_positions <= 0:
            raise RuntimeError("열린 포지션 없이 종료를 기록할 수 없습니다.")
        state.open_positions -= 1
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
