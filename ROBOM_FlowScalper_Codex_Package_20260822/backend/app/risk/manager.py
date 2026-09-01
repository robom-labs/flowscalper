"""1,000 USDT PAPER 계좌의 수량·손실·drawdown·cooldown을 보수적으로 제한한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.research.gates import RiskOverlay

_DAY_MS = 24 * 60 * 60 * 1_000


def _utc_day_start_ms(timestamp_ms: int) -> int:
    if timestamp_ms < 0:
        raise ValueError("위험 기간 시각은 음수일 수 없습니다.")
    return timestamp_ms // _DAY_MS * _DAY_MS


def _utc_week_start_ms(timestamp_ms: int) -> int:
    """Unix epoch의 목요일 오프셋을 보정해 월요일 00:00 UTC를 반환한다."""

    day_index = timestamp_ms // _DAY_MS
    monday_day_index = ((day_index + 3) // 7) * 7 - 3
    return monday_day_index * _DAY_MS


@dataclass(frozen=True, slots=True)
class RiskLimits:
    risk_per_trade_fraction: Decimal = Decimal("0.001")
    max_open_positions: int = 1
    max_daily_trades: int | None = 12
    maximum_total_open_risk_fraction: Decimal = Decimal("0.001")
    daily_loss_limit_fraction: Decimal | None = Decimal("0.005")
    weekly_loss_limit_fraction: Decimal | None = Decimal("0.015")
    maximum_drawdown_fraction: Decimal = Decimal("0.03")
    maximum_gross_notional_fraction: Decimal = Decimal("0.50")
    maximum_order_fraction_of_executable_depth: Decimal = Decimal("0.02")
    loss_cooldowns_enabled: bool = True


STRATEGY_LEAGUE_RISK_LIMITS = RiskLimits(
    risk_per_trade_fraction=Decimal("0.005"),
    max_open_positions=3,
    maximum_total_open_risk_fraction=Decimal("0.015"),
    daily_loss_limit_fraction=Decimal("0.02"),
    weekly_loss_limit_fraction=Decimal("0.05"),
    maximum_drawdown_fraction=Decimal("0.08"),
    maximum_gross_notional_fraction=Decimal("10.0"),
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
    base_risk_budget: Decimal
    risk_multiplier: Decimal


@dataclass(slots=True)
class RiskState:
    starting_equity: Decimal = Decimal("1000")
    current_equity: Decimal = Decimal("1000")
    peak_equity: Decimal = Decimal("1000")
    realized_today: Decimal = Decimal(0)
    realized_week: Decimal = Decimal(0)
    daily_trade_count: int = 0
    daily_period_start_ms: int | None = None
    weekly_period_start_ms: int | None = None
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

    def size(
        self,
        values: RiskSizingInput,
        *,
        risk_overlay: RiskOverlay | None = None,
    ) -> RiskSizingResult:
        base_risk_budget = values.equity * self.limits.risk_per_trade_fraction
        risk_multiplier = (
            risk_overlay.multiplier if risk_overlay is not None else Decimal(1)
        )
        if (
            not risk_multiplier.is_finite()
            or risk_multiplier < 0
            or risk_multiplier > 1
        ):
            return RiskSizingResult(
                quantity=None,
                risk_budget=Decimal(0),
                planned_loss=None,
                rejection_codes=("INVALID_RISK_OVERLAY",),
                base_risk_budget=base_risk_budget,
                risk_multiplier=Decimal(0),
            )
        risk_budget = base_risk_budget * risk_multiplier
        if risk_multiplier == 0:
            return RiskSizingResult(
                quantity=None,
                risk_budget=risk_budget,
                planned_loss=None,
                rejection_codes=("RISK_OVERLAY_ZERO",),
                base_risk_budget=base_risk_budget,
                risk_multiplier=risk_multiplier,
            )
        loss_per_unit = (
            abs(values.entry_price - values.stop_price)
            + values.entry_fee_per_unit
            + values.stop_fee_per_unit
            + values.p95_exit_slippage_per_unit
        )
        if loss_per_unit <= 0 or values.quantity_step <= 0:
            return RiskSizingResult(
                quantity=None,
                risk_budget=risk_budget,
                planned_loss=None,
                rejection_codes=("INVALID_RISK_INPUT",),
                base_risk_budget=base_risk_budget,
                risk_multiplier=risk_multiplier,
            )
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
            return RiskSizingResult(
                quantity=None,
                risk_budget=risk_budget,
                planned_loss=None,
                rejection_codes=("QUANTITY_BELOW_MINIMUM",),
                base_risk_budget=base_risk_budget,
                risk_multiplier=risk_multiplier,
            )
        planned_loss = quantity * loss_per_unit
        if planned_loss > risk_budget:
            return RiskSizingResult(
                quantity=None,
                risk_budget=risk_budget,
                planned_loss=planned_loss,
                rejection_codes=("PLANNED_LOSS_EXCEEDS_BUDGET",),
                base_risk_budget=base_risk_budget,
                risk_multiplier=risk_multiplier,
            )
        return RiskSizingResult(
            quantity=quantity,
            risk_budget=risk_budget,
            planned_loss=planned_loss,
            rejection_codes=(),
            base_risk_budget=base_risk_budget,
            risk_multiplier=risk_multiplier,
        )

    def entry_rejections(self, state: RiskState, key: str, now_ms: int) -> tuple[str, ...]:
        self.refresh_periods(state, now_ms)
        reasons: list[str] = []
        if state.paused:
            reasons.append("RUN_PAUSED")
        if state.faulted:
            reasons.append("RUN_FAULTED")
        if state.open_positions >= self.limits.max_open_positions:
            reasons.append("MAX_OPEN_POSITIONS")
        if (
            self.limits.max_daily_trades is not None
            and state.daily_trade_count >= self.limits.max_daily_trades
        ):
            reasons.append("MAX_DAILY_TRADES")
        if (
            self.limits.daily_loss_limit_fraction is not None
            and -state.realized_today
            >= state.current_equity * self.limits.daily_loss_limit_fraction
        ):
            reasons.append("DAILY_LOSS_LOCK")
        if (
            self.limits.weekly_loss_limit_fraction is not None
            and -state.realized_week
            >= state.current_equity * self.limits.weekly_loss_limit_fraction
        ):
            reasons.append("WEEKLY_LOSS_LOCK")
        if state.drawdown_fraction >= self.limits.maximum_drawdown_fraction:
            reasons.append("DRAWDOWN_LOCK")
        if self.limits.loss_cooldowns_enabled:
            if state.cooldowns_until_ms.get(key, 0) > now_ms:
                reasons.append("COOLDOWN_ACTIVE")
            if state.cooldowns_until_ms.get("GLOBAL", 0) > now_ms:
                reasons.append("GLOBAL_COOLDOWN_ACTIVE")
        return tuple(reasons)

    @staticmethod
    def refresh_periods(state: RiskState, now_ms: int) -> None:
        """UTC 일·주 경계가 바뀌면 해당 기간 한도만 새로 시작한다."""

        daily_start = _utc_day_start_ms(now_ms)
        weekly_start = _utc_week_start_ms(now_ms)
        if state.daily_period_start_ms is None:
            state.daily_period_start_ms = daily_start
        elif daily_start > state.daily_period_start_ms:
            state.daily_period_start_ms = daily_start
            state.realized_today = Decimal(0)
            state.daily_trade_count = 0
        if state.weekly_period_start_ms is None:
            state.weekly_period_start_ms = weekly_start
        elif weekly_start > state.weekly_period_start_ms:
            state.weekly_period_start_ms = weekly_start
            state.realized_week = Decimal(0)

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
        now_ms: int,
        planned_risk: Decimal = Decimal(0),
        notional: Decimal = Decimal(0),
        effective_leverage: Decimal = Decimal(0),
    ) -> None:
        self.refresh_periods(state, now_ms)
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
        self.refresh_periods(state, now_ms)
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
            if self.limits.loss_cooldowns_enabled:
                if state.global_consecutive_losses >= 3:
                    state.cooldowns_until_ms["GLOBAL"] = now_ms + 60 * 60 * 1000
                state.cooldowns_until_ms[key] = now_ms + 2 * 60 * 60 * 1000
        else:
            state.global_consecutive_losses = 0
