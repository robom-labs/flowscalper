# 연구 신호의 진입·손절·두 익절과 최대 보유시간을 신호 시점에 고정한다.

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.app.domain.models import Side
from backend.app.intraday.features import HorizonClass


@dataclass(frozen=True, slots=True)
class ResearchPricePlan:
    side: Side
    signal_ts_ms: int
    entry: Decimal
    stop: Decimal
    take_profit_1: Decimal
    take_profit_2: Decimal
    risk_distance: Decimal
    maximum_holding_ms: int

    def __post_init__(self) -> None:
        if self.signal_ts_ms < 0 or self.entry <= 0 or self.risk_distance <= 0:
            raise ValueError("연구 가격계획의 시각·가격·위험거리가 올바르지 않습니다.")
        if self.side is Side.LONG and not (
            self.stop < self.entry < self.take_profit_1 < self.take_profit_2
        ):
            raise ValueError("롱 연구 가격계획의 가격 순서가 잘못됐습니다.")
        if self.side is Side.SHORT and not (
            self.take_profit_2 < self.take_profit_1 < self.entry < self.stop
        ):
            raise ValueError("숏 연구 가격계획의 가격 순서가 잘못됐습니다.")


def build_research_price_plan(
    *,
    side: Side,
    signal_ts_ms: int,
    executable_entry: Decimal,
    atr: Decimal,
    horizon: HorizonClass,
) -> ResearchPricePlan:
    if atr <= 0:
        raise ValueError("ATR은 양수여야 합니다.")
    risk_multipliers = {
        HorizonClass.MICRO_SCALP: Decimal("0.8"),
        HorizonClass.FAST_INTRADAY: Decimal("1.1"),
        HorizonClass.INTRADAY_SWING: Decimal("1.5"),
    }
    holding_ms = {
        HorizonClass.MICRO_SCALP: 180_000,
        HorizonClass.FAST_INTRADAY: 3_600_000,
        HorizonClass.INTRADAY_SWING: 21_600_000,
    }
    risk = atr * risk_multipliers[horizon]
    direction = Decimal(1) if side is Side.LONG else Decimal(-1)
    return ResearchPricePlan(
        side=side,
        signal_ts_ms=signal_ts_ms,
        entry=executable_entry,
        stop=executable_entry - direction * risk,
        take_profit_1=executable_entry + direction * risk * Decimal("1.2"),
        take_profit_2=executable_entry + direction * risk * Decimal("2.2"),
        risk_distance=risk,
        maximum_holding_ms=holding_ms[horizon],
    )
