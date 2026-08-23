"""호가와 체결 이력에서 유한한 다중창 미세구조 피처를 계산한다."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, fields
from decimal import Decimal

from backend.app.domain.market import TradeTick
from backend.app.domain.models import Venue


class FeatureInputError(ValueError):
    """피처 계산 전 시장데이터 검증에 실패할 때 발생한다."""


@dataclass(frozen=True, slots=True)
class BookFrame:
    venue: Venue
    symbol: str
    ts_ms: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    sequence_valid: bool
    stale: bool
    lag_ms: float = 0.0

    @classmethod
    def from_levels(
        cls,
        *,
        venue: Venue,
        symbol: str,
        ts_ms: int,
        bids: Iterable[tuple[Decimal, Decimal]],
        asks: Iterable[tuple[Decimal, Decimal]],
        sequence_valid: bool = True,
        stale: bool = False,
        lag_ms: float = 0.0,
    ) -> BookFrame:
        frame = cls(
            venue=venue,
            symbol=symbol,
            ts_ms=ts_ms,
            bids=tuple(sorted(bids, reverse=True)),
            asks=tuple(sorted(asks)),
            sequence_valid=sequence_valid,
            stale=stale,
            lag_ms=lag_ms,
        )
        frame.validate()
        return frame

    def validate(self) -> None:
        if not self.bids or not self.asks:
            raise FeatureInputError("호가 양쪽이 필요합니다.")
        for price, quantity in (*self.bids, *self.asks):
            if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity < 0:
                raise FeatureInputError("호가 가격·수량은 유한하고 유효해야 합니다.")
        if self.bids[0][0] >= self.asks[0][0]:
            raise FeatureInputError("교차 호가장은 피처 엔진에 입력할 수 없습니다.")
        if not math.isfinite(self.lag_ms) or self.lag_ms < 0:
            raise FeatureInputError("lag는 유한한 음이 아닌 값이어야 합니다.")


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    venue: Venue
    symbol: str
    ts_ms: int
    sample_count: int
    warmup_seconds: float
    data_healthy: bool
    lag_ms: float
    mid: float
    spread_bps: float
    depth_bid_10: float
    depth_ask_10: float
    imbalance_top1: float
    imbalance_top5: float
    imbalance_top10: float
    microprice: float
    microprice_minus_mid_bps: float
    ofi_250ms: float
    ofi_1s: float
    ofi_3s: float
    ofi_10s: float
    trade_imbalance_1s: float
    trade_imbalance_3s: float
    trade_imbalance_10s: float
    signed_notional_3s: float
    refill_ratio: float
    cancel_ratio: float
    price_response_efficiency: float
    realized_volatility_30s: float
    realized_volatility_120s: float
    compression_ratio: float
    efficiency_ratio_30s: float
    micro_vwap_10s: float

    def assert_finite(self) -> None:
        for field in fields(self):
            name = field.name
            value = getattr(self, name)
            if isinstance(value, float) and not math.isfinite(value):
                raise FeatureInputError(f"{name} 피처가 유한하지 않습니다.")


class FeatureEngine:
    def __init__(self, retention_ms: int = 120_000) -> None:
        self.retention_ms = retention_ms
        self._books: deque[BookFrame] = deque()
        self._trades: deque[TradeTick] = deque()
        self._ofi: deque[tuple[int, float]] = deque()
        self._depth_changes: deque[tuple[int, float, float]] = deque()

    def ingest_book(self, frame: BookFrame) -> None:
        frame.validate()
        if self._books:
            previous = self._books[-1]
            if previous.venue is not frame.venue or previous.symbol != frame.symbol:
                raise FeatureInputError("피처 엔진 인스턴스에서 거래소나 종목을 섞을 수 없습니다.")
            self._ofi.append((frame.ts_ms, self._book_ofi(previous, frame)))
            self._depth_changes.append((frame.ts_ms, *self._depth_change(previous, frame)))
        self._books.append(frame)
        self._trim(frame.ts_ms)

    def ingest_trade(self, trade: TradeTick) -> None:
        if not trade.price.is_finite() or not trade.quantity.is_finite():
            raise FeatureInputError("체결 가격·수량이 유한하지 않습니다.")
        if trade.price <= 0 or trade.quantity <= 0:
            raise FeatureInputError("체결 가격·수량은 양수여야 합니다.")
        if self._books:
            latest = self._books[-1]
            if trade.venue is not latest.venue or trade.symbol != latest.symbol:
                raise FeatureInputError("호가와 체결의 거래소·종목이 다릅니다.")
        self._trades.append(trade)
        self._trim(trade.trade_ts_ms)

    def snapshot(self) -> FeatureSnapshot:
        if not self._books:
            raise FeatureInputError("피처 snapshot에 호가 이력이 필요합니다.")
        latest = self._books[-1]
        bid, bid_quantity = latest.bids[0]
        ask, ask_quantity = latest.asks[0]
        mid = (bid + ask) / Decimal(2)
        spread_bps = (ask - bid) / mid * Decimal(10_000)
        depth_bid_10 = sum((price * quantity for price, quantity in latest.bids[:10]), Decimal(0))
        depth_ask_10 = sum((price * quantity for price, quantity in latest.asks[:10]), Decimal(0))
        microprice = (ask * bid_quantity + bid * ask_quantity) / (bid_quantity + ask_quantity)
        mids = [
            (book.ts_ms, float((book.bids[0][0] + book.asks[0][0]) / 2)) for book in self._books
        ]
        ofi_250ms = 0.0
        ofi_1s = 0.0
        ofi_3s = 0.0
        ofi_10s = 0.0
        for timestamp, value in reversed(self._ofi):
            age_ms = latest.ts_ms - timestamp
            if age_ms > 10_000:
                break
            ofi_10s += value
            if age_ms <= 3_000:
                ofi_3s += value
            if age_ms <= 1_000:
                ofi_1s += value
            if age_ms <= 250:
                ofi_250ms += value

        trade_quantity_1s = Decimal(0)
        trade_quantity_3s = Decimal(0)
        trade_quantity_10s = Decimal(0)
        signed_quantity_1s = Decimal(0)
        signed_quantity_3s = Decimal(0)
        signed_quantity_10s = Decimal(0)
        signed_notional_3s = Decimal(0)
        notional_10s = Decimal(0)
        for trade in reversed(self._trades):
            age_ms = latest.ts_ms - trade.trade_ts_ms
            if age_ms > 10_000:
                break
            direction = Decimal(1) if trade.buyer_is_aggressor else Decimal(-1)
            trade_quantity_10s += trade.quantity
            signed_quantity_10s += trade.quantity * direction
            notional_10s += trade.price * trade.quantity
            if age_ms <= 3_000:
                trade_quantity_3s += trade.quantity
                signed_quantity_3s += trade.quantity * direction
                signed_notional_3s += trade.price * trade.quantity * direction
            if age_ms <= 1_000:
                trade_quantity_1s += trade.quantity
                signed_quantity_1s += trade.quantity * direction

        additions_3s = 0.0
        removals_3s = 0.0
        for timestamp, additions, removals in reversed(self._depth_changes):
            if latest.ts_ms - timestamp > 3_000:
                break
            additions_3s += additions
            removals_3s += removals
        depth_total_3s = additions_3s + removals_3s

        values_3s = [value for timestamp, value in mids if timestamp >= latest.ts_ms - 3_000]
        values_30s = [value for timestamp, value in mids if timestamp >= latest.ts_ms - 30_000]
        values_120s = [
            value for timestamp, value in mids if timestamp >= latest.ts_ms - 120_000
        ]
        volatility_30s = self._volatility_from_values(values_30s)
        volatility_120s = self._volatility_from_values(values_120s)
        path_30s = sum(
            abs(current - previous)
            for previous, current in zip(values_30s, values_30s[1:], strict=False)
        )
        ofi_3s_absolute = abs(ofi_3s)
        first_ts = self._books[0].ts_ms
        snapshot = FeatureSnapshot(
            venue=latest.venue,
            symbol=latest.symbol,
            ts_ms=latest.ts_ms,
            sample_count=len(self._books),
            warmup_seconds=max(0.0, (latest.ts_ms - first_ts) / 1000),
            data_healthy=latest.sequence_valid and not latest.stale and latest.lag_ms <= 500,
            lag_ms=latest.lag_ms,
            mid=float(mid),
            spread_bps=float(spread_bps),
            depth_bid_10=float(depth_bid_10),
            depth_ask_10=float(depth_ask_10),
            imbalance_top1=self._imbalance(latest, 1),
            imbalance_top5=self._imbalance(latest, 5),
            imbalance_top10=self._imbalance(latest, 10),
            microprice=float(microprice),
            microprice_minus_mid_bps=float((microprice - mid) / mid * Decimal(10_000)),
            ofi_250ms=ofi_250ms,
            ofi_1s=ofi_1s,
            ofi_3s=ofi_3s,
            ofi_10s=ofi_10s,
            trade_imbalance_1s=self._ratio(signed_quantity_1s, trade_quantity_1s),
            trade_imbalance_3s=self._ratio(signed_quantity_3s, trade_quantity_3s),
            trade_imbalance_10s=self._ratio(signed_quantity_10s, trade_quantity_10s),
            signed_notional_3s=float(signed_notional_3s),
            refill_ratio=additions_3s / depth_total_3s if depth_total_3s else 0.0,
            cancel_ratio=removals_3s / depth_total_3s if depth_total_3s else 0.0,
            price_response_efficiency=(
                abs(values_3s[-1] - values_3s[0]) / ofi_3s_absolute
                if len(values_3s) >= 2 and ofi_3s_absolute
                else 0.0
            ),
            realized_volatility_30s=volatility_30s,
            realized_volatility_120s=volatility_120s,
            compression_ratio=(
                volatility_30s / volatility_120s if volatility_120s > 0 else 0.0
            ),
            efficiency_ratio_30s=(
                abs(values_30s[-1] - values_30s[0]) / path_30s
                if len(values_30s) >= 2 and path_30s
                else 0.0
            ),
            micro_vwap_10s=(
                float(notional_10s / trade_quantity_10s)
                if trade_quantity_10s
                else float(mid)
            ),
        )
        snapshot.assert_finite()
        return snapshot

    def _trim(self, now_ms: int) -> None:
        book_cutoff = now_ms - self.retention_ms
        trade_cutoff = now_ms - min(self.retention_ms, 10_000)
        depth_cutoff = now_ms - min(self.retention_ms, 3_000)
        while self._books and self._books[0].ts_ms < book_cutoff:
            self._books.popleft()
        while self._trades and self._trades[0].trade_ts_ms < trade_cutoff:
            self._trades.popleft()
        while self._ofi and self._ofi[0][0] < trade_cutoff:
            self._ofi.popleft()
        while self._depth_changes and self._depth_changes[0][0] < depth_cutoff:
            self._depth_changes.popleft()

    @staticmethod
    def _ratio(numerator: Decimal, denominator: Decimal) -> float:
        return float(numerator / denominator) if denominator else 0.0

    @staticmethod
    def _volatility_from_values(values: list[float]) -> float:
        returns = [
            math.log(current / previous)
            for previous, current in zip(values, values[1:], strict=False)
        ]
        if len(returns) < 2:
            return 0.0
        mean = math.fsum(returns) / len(returns)
        variance = math.fsum((value - mean) ** 2 for value in returns) / len(returns)
        return math.sqrt(variance)

    @staticmethod
    def _imbalance(frame: BookFrame, depth: int) -> float:
        bid = sum((quantity for _, quantity in frame.bids[:depth]), Decimal(0))
        ask = sum((quantity for _, quantity in frame.asks[:depth]), Decimal(0))
        total = bid + ask
        return float((bid - ask) / total) if total else 0.0

    @staticmethod
    def _book_ofi(previous: BookFrame, current: BookFrame) -> float:
        previous_bid, previous_bid_qty = previous.bids[0]
        current_bid, current_bid_qty = current.bids[0]
        previous_ask, previous_ask_qty = previous.asks[0]
        current_ask, current_ask_qty = current.asks[0]
        bid_flow = (
            current_bid_qty
            if current_bid > previous_bid
            else -previous_bid_qty
            if current_bid < previous_bid
            else current_bid_qty - previous_bid_qty
        )
        ask_flow = (
            current_ask_qty
            if current_ask < previous_ask
            else -previous_ask_qty
            if current_ask > previous_ask
            else current_ask_qty - previous_ask_qty
        )
        return float(bid_flow - ask_flow)

    @staticmethod
    def _depth_change(previous: BookFrame, current: BookFrame) -> tuple[float, float]:
        old = {price: quantity for price, quantity in (*previous.bids[:10], *previous.asks[:10])}
        new = {price: quantity for price, quantity in (*current.bids[:10], *current.asks[:10])}
        refill = Decimal(0)
        cancel = Decimal(0)
        for price in old.keys() | new.keys():
            delta = new.get(price, Decimal(0)) - old.get(price, Decimal(0))
            if delta > 0:
                refill += delta
            else:
                cancel -= delta
        return float(refill), float(cancel)

    @staticmethod
    def _window_sum(values: deque[tuple[int, float]], now_ms: int, window_ms: int) -> float:
        return sum(value for timestamp, value in values if timestamp >= now_ms - window_ms)

    def _trade_imbalance(self, now_ms: int, window_ms: int) -> float:
        values = [trade for trade in self._trades if trade.trade_ts_ms >= now_ms - window_ms]
        total = sum((trade.quantity for trade in values), Decimal(0))
        if not total:
            return 0.0
        signed = sum(
            (trade.quantity if trade.buyer_is_aggressor else -trade.quantity for trade in values),
            Decimal(0),
        )
        return float(signed / total)

    def _signed_notional(self, now_ms: int, window_ms: int) -> float:
        return float(
            sum(
                (
                    trade.price * trade.quantity * (1 if trade.buyer_is_aggressor else -1)
                    for trade in self._trades
                    if trade.trade_ts_ms >= now_ms - window_ms
                ),
                Decimal(0),
            )
        )

    def _depth_ratio(self, now_ms: int, window_ms: int, *, refill: bool) -> float:
        changes = [item for item in self._depth_changes if item[0] >= now_ms - window_ms]
        additions = sum(item[1] for item in changes)
        removals = sum(item[2] for item in changes)
        total = additions + removals
        return (additions if refill else removals) / total if total else 0.0

    def _price_response(self, mids: list[tuple[int, float]], now_ms: int, window_ms: int) -> float:
        window = [value for timestamp, value in mids if timestamp >= now_ms - window_ms]
        ofi = abs(self._window_sum(self._ofi, now_ms, window_ms))
        if len(window) < 2 or ofi == 0:
            return 0.0
        return abs(window[-1] - window[0]) / ofi

    @staticmethod
    def _realized_volatility(mids: list[tuple[int, float]], now_ms: int, window_ms: int) -> float:
        values = [value for timestamp, value in mids if timestamp >= now_ms - window_ms]
        return FeatureEngine._volatility_from_values(values)

    def _compression(self, mids: list[tuple[int, float]], now_ms: int) -> float:
        short = self._realized_volatility(mids, now_ms, 30_000)
        long = self._realized_volatility(mids, now_ms, 120_000)
        return short / long if long > 0 else 0.0

    @staticmethod
    def _efficiency_ratio(mids: list[tuple[int, float]], now_ms: int, window_ms: int) -> float:
        values = [value for timestamp, value in mids if timestamp >= now_ms - window_ms]
        if len(values) < 2:
            return 0.0
        path = sum(
            abs(current - previous) for previous, current in zip(values, values[1:], strict=False)
        )
        return abs(values[-1] - values[0]) / path if path else 0.0

    def _micro_vwap(self, now_ms: int, window_ms: int, fallback: float) -> float:
        values = [trade for trade in self._trades if trade.trade_ts_ms >= now_ms - window_ms]
        quantity = sum((trade.quantity for trade in values), Decimal(0))
        if not quantity:
            return fallback
        notional = sum((trade.price * trade.quantity for trade in values), Decimal(0))
        return float(notional / quantity)
