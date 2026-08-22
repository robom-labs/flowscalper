"""Decimal 호가와 snapshot/delta 연속성으로 보수적인 로컬 호가장을 유지한다."""

from __future__ import annotations

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

    def _apply_levels(
        self,
        bid_updates: Iterable[Iterable[object]],
        ask_updates: Iterable[Iterable[object]],
    ) -> None:
        for side, updates in ((self.bids, bid_updates), (self.asks, ask_updates)):
            for row in updates:
                pair = tuple(row)
                if len(pair) != 2:
                    raise InvalidBook("호가 업데이트는 가격과 수량 쌍이어야 합니다.")
                price = Decimal(str(pair[0]))
                quantity = Decimal(str(pair[1]))
                if not price.is_finite() or not quantity.is_finite() or price <= 0 or quantity < 0:
                    raise InvalidBook("호가 업데이트 값이 유효하지 않습니다.")
                if quantity == 0:
                    side.pop(price, None)
                else:
                    side[price] = quantity
        self._validate_uncrossed()

    def _validate_uncrossed(self) -> None:
        if not self.bids or not self.asks:
            raise InvalidBook("호가 양쪽에 한 레벨 이상이 필요합니다.")
        if max(self.bids) >= min(self.asks):
            self.sequence_valid = False
            self.stale = True
            raise InvalidBook("교차 호가장은 사용할 수 없습니다.")

    def top(
        self, depth: int = 20
    ) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        bids = sorted(self.bids.items(), reverse=True)[:depth]
        asks = sorted(self.asks.items())[:depth]
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
            if first_update_id > self.last_update_id or final_update_id < self.last_update_id:
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
