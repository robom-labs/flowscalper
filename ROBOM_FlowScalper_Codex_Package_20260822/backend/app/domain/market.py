"""공개 시장데이터와 동적 유니버스의 타입 계약을 정의한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.app.domain.models import Venue


@dataclass(frozen=True, slots=True)
class Instrument:
    venue: Venue
    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    contract_type: str
    tick_size: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    onboard_ts_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Ticker:
    venue: Venue
    symbol: str
    bid: Decimal
    ask: Decimal
    quote_turnover_24h: Decimal
    trade_count_24h: int

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal(2)

    @property
    def spread_bps(self) -> Decimal:
        if self.mid <= 0:
            return Decimal("Infinity")
        return (self.ask - self.bid) / self.mid * Decimal(10_000)


@dataclass(frozen=True, slots=True)
class TradeTick:
    venue: Venue
    symbol: str
    price: Decimal
    quantity: Decimal
    trade_ts_ms: int
    buyer_is_aggressor: bool
    event_id: str | None = None
