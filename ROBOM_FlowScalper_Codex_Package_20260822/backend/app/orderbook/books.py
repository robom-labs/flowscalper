"""Decimal 호가와 snapshot/delta 연속성으로 보수적인 로컬 호가장을 유지한다."""

from __future__ import annotations

from bisect import bisect_left, insort
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


class InvalidBook(ValueError):
    """호가 입력이 유효한 주문장을 만들 수 없을 때 발생한다."""


class SequenceGap(RuntimeError):
    """연속성이 깨져 새 snapshot이 필요할 때 발생한다."""


def _levels(values: Iterable[Iterable[object]]) -> dict[Decimal, Decimal]:
    result: dict[Decimal, Decimal] = {}
    for row in values:
        pair = tuple(row)
        if len(pair) != 2:
            raise InvalidBook("호가 레벨은 가격과 수량 쌍이어야 합니다.")
        try:
            price = Decimal(str(pair[0]))
            quantity = Decimal(str(pair[1]))
        except InvalidOperation as exc:
            raise InvalidBook("호가 숫자를 Decimal로 변환할 수 없습니다.") from exc
        if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity < 0:
            raise InvalidBook("호가는 유한한 양수 가격과 음이 아닌 수량이어야 합니다.")
        if quantity > 0:
            result[price] = quantity
    return result


@dataclass(slots=True)
class LocalOrderBook:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    sequence_valid: bool = False
    stale: bool = True
    _bid_prices: list[Decimal] = field(default_factory=list, init=False, repr=False)
    _ask_prices: list[Decimal] = field(default_factory=list, init=False, repr=False)
    _top_bid_prices: list[Decimal] = field(default_factory=list, init=False, repr=False)
    _top_ask_prices: list[Decimal] = field(default_factory=list, init=False, repr=False)
    _cached_depth: int = field(default=20, init=False, repr=False)

    def _apply_levels(
        self,
        bid_updates: Iterable[Iterable[object]],
        ask_updates: Iterable[Iterable[object]],
    ) -> None:
        if (
            len(self._bid_prices) != len(self.bids)
            or len(self._ask_prices) != len(self.asks)
        ):
            self._reset_top_cache()
        for side, prices, updates in (
            (self.bids, self._bid_prices, bid_updates),
            (self.asks, self._ask_prices, ask_updates),
        ):
            for row in updates:
                pair = tuple(row)
                if len(pair) != 2:
                    raise InvalidBook("호가 업데이트는 가격과 수량 쌍이어야 합니다.")
                price = Decimal(str(pair[0]))
                quantity = Decimal(str(pair[1]))
                if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity < 0:
                    raise InvalidBook("호가 업데이트 값이 유효하지 않습니다.")
                existing = price in side
                if quantity == 0:
                    if existing:
                        side.pop(price)
                        index = bisect_left(prices, price)
                        if index >= len(prices) or prices[index] != price:
                            raise InvalidBook("호가 가격 인덱스가 원장과 일치하지 않습니다.")
                        prices.pop(index)
                else:
                    side[price] = quantity
                    if not existing:
                        insort(prices, price)
        self._sync_top_cache()
        self._validate_uncrossed()

    def _reset_top_cache(self) -> None:
        self._bid_prices = sorted(self.bids)
        self._ask_prices = sorted(self.asks)
        self._sync_top_cache()

    def _sync_top_cache(self) -> None:
        self._top_bid_prices = list(reversed(self._bid_prices[-self._cached_depth :]))
        self._top_ask_prices = self._ask_prices[: self._cached_depth]

    def _validate_uncrossed(self) -> None:
        if not self.bids or not self.asks:
            raise InvalidBook("호가 양쪽에 한 레벨 이상이 필요합니다.")
        if not self._top_bid_prices or not self._top_ask_prices:
            self._reset_top_cache()
        if self._top_bid_prices[0] >= self._top_ask_prices[0]:
            self.sequence_valid = False
            self.stale = True
            raise InvalidBook("교차 호가장은 사용할 수 없습니다.")

    def top(
        self, depth: int = 20
    ) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        if depth <= 0:
            return [], []
        if not self._top_bid_prices or not self._top_ask_prices:
            self._reset_top_cache()
        if depth <= self._cached_depth:
            bid_prices = self._top_bid_prices[:depth]
            ask_prices = self._top_ask_prices[:depth]
        else:
            bid_prices = list(reversed(self._bid_prices[-depth:]))
            ask_prices = self._ask_prices[:depth]
        bids = [(price, self.bids[price]) for price in bid_prices]
        asks = [(price, self.asks[price]) for price in ask_prices]
        return bids, asks


@dataclass(slots=True)
class BinanceOrderBook(LocalOrderBook):
    last_update_id: int | None = None
    _bridged_snapshot: bool = False

    def reset_snapshot(
        self,
        last_update_id: int,
        bids: Iterable[Iterable[object]],
        asks: Iterable[Iterable[object]],
    ) -> None:
        self.bids = _levels(bids)
        self.asks = _levels(asks)
        self._reset_top_cache()
        self.last_update_id = last_update_id
        self._bridged_snapshot = False
        self._validate_uncrossed()
        self.sequence_valid = True
        self.stale = False

    def apply_delta(
        self,
        first_update_id: int,
        final_update_id: int,
        previous_final_update_id: int | None,
        bids: Iterable[Iterable[object]],
        asks: Iterable[Iterable[object]],
    ) -> bool:
        if self.last_update_id is None:
            raise SequenceGap("snapshot 전 delta는 적용할 수 없습니다.")
        if final_update_id < self.last_update_id or (
            self._bridged_snapshot and final_update_id <= self.last_update_id
        ):
            return False
        if not self._bridged_snapshot:
            snapshot_bridge = self.last_update_id
            next_bridge = self.last_update_id + 1
            bridges_snapshot = (
                first_update_id <= snapshot_bridge <= final_update_id
                or first_update_id <= next_bridge <= final_update_id
            )
            if not bridges_snapshot:
                self._mark_gap("첫 delta가 snapshot update ID를 연결하지 않습니다.")
            self._bridged_snapshot = True
        elif previous_final_update_id != self.last_update_id:
            self._mark_gap("Binance pu가 이전 u와 일치하지 않습니다.")
        self._apply_levels(bids, asks)
        self.last_update_id = final_update_id
        self.sequence_valid = True
        self.stale = False
        return True

    def _mark_gap(self, message: str) -> None:
        self.sequence_valid = False
        self.stale = True
        raise SequenceGap(message)


@dataclass(slots=True)
class BybitOrderBook(LocalOrderBook):
    update_id: int | None = None
    cross_sequence: int | None = None

    def apply(
        self,
        message_type: str,
        update_id: int,
        cross_sequence: int,
        bids: Iterable[Iterable[object]],
        asks: Iterable[Iterable[object]],
    ) -> bool:
        if message_type == "snapshot" or update_id == 1:
            self.bids = _levels(bids)
            self.asks = _levels(asks)
            self._reset_top_cache()
            self.update_id = update_id
            self.cross_sequence = cross_sequence
            self._validate_uncrossed()
            self.sequence_valid = True
            self.stale = False
            return True
        if message_type != "delta" or self.update_id is None or self.cross_sequence is None:
            self.sequence_valid = False
            self.stale = True
            raise SequenceGap("Bybit snapshot 전 delta 또는 알 수 없는 메시지입니다.")
        if update_id <= self.update_id or cross_sequence <= self.cross_sequence:
            return False
        self._apply_levels(bids, asks)
        self.update_id = update_id
        self.cross_sequence = cross_sequence
        self.sequence_valid = True
        self.stale = False
        return True
