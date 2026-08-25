"""실제 거래 틱만 사용해 지원 시간구간 봉을 결정적으로 생성한다."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal

from backend.app.domain.market import TradeTick
from backend.app.market_data.timeframes import TIMEFRAME_REGISTRY


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    interval_seconds: int
    open_ts_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int
    quote_volume: Decimal = Decimal(0)
    taker_buy_volume: Decimal = Decimal(0)
    taker_sell_volume: Decimal = Decimal(0)
    taker_buy_quote_volume: Decimal = Decimal(0)
    taker_sell_quote_volume: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class CandleBuilderDiagnostics:
    accepted_trades: int
    duplicate_events: int
    out_of_order_trades: int


class CandleBuilder:
    ALLOWED_INTERVALS = TIMEFRAME_REGISTRY.builder_intervals

    def __init__(
        self,
        maximum_bars: int = 1_000,
        *,
        intervals: tuple[int, ...] | None = None,
    ) -> None:
        if maximum_bars <= 0:
            raise ValueError("maximum_bars는 양수여야 합니다.")
        selected_intervals = self.ALLOWED_INTERVALS if intervals is None else intervals
        if not selected_intervals or len(set(selected_intervals)) != len(selected_intervals):
            raise ValueError("캔들 시간구간은 중복 없는 비어 있지 않은 값이어야 합니다.")
        for seconds in selected_intervals:
            TIMEFRAME_REGISTRY.validate_builder(seconds)
        self.maximum_bars = maximum_bars
        self.intervals = selected_intervals
        self._current: dict[tuple[str, int], Candle] = {}
        self._history: dict[tuple[str, int], deque[Candle]] = defaultdict(
            lambda: deque(maxlen=self.maximum_bars)
        )
        self._last_trade_ts_ms: dict[str, int] = {}
        self._seen_event_ids: set[str] = set()
        self._event_id_order: deque[str] = deque()
        self._event_id_limit = max(10_000, maximum_bars * len(selected_intervals) * 10)
        self._accepted_trades = 0
        self._duplicate_events = 0
        self._out_of_order_trades = 0

    def add(self, trade: TradeTick) -> list[Candle]:
        if trade.price <= 0 or trade.quantity <= 0:
            raise ValueError("체결 가격과 수량은 양수여야 합니다.")
        if trade.event_id is not None and trade.event_id in self._seen_event_ids:
            self._duplicate_events += 1
            return []
        last_ts_ms = self._last_trade_ts_ms.get(trade.symbol)
        if last_ts_ms is not None and trade.trade_ts_ms < last_ts_ms:
            self._out_of_order_trades += 1
            return []
        if trade.event_id is not None:
            self._seen_event_ids.add(trade.event_id)
            self._event_id_order.append(trade.event_id)
            if len(self._event_id_order) > self._event_id_limit:
                self._seen_event_ids.discard(self._event_id_order.popleft())
        self._last_trade_ts_ms[trade.symbol] = trade.trade_ts_ms
        self._accepted_trades += 1
        completed: list[Candle] = []
        quote_notional = trade.price * trade.quantity
        for seconds in self.intervals:
            key = (trade.symbol, seconds)
            interval_ms = seconds * 1000
            bucket = trade.trade_ts_ms - trade.trade_ts_ms % interval_ms
            current = self._current.get(key)
            if current is None or current.open_ts_ms != bucket:
                if current is not None:
                    completed.append(current)
                    self._history[key].append(current)
                self._current[key] = Candle(
                    symbol=trade.symbol,
                    interval_seconds=seconds,
                    open_ts_ms=bucket,
                    open=trade.price,
                    high=trade.price,
                    low=trade.price,
                    close=trade.price,
                    volume=trade.quantity,
                    trade_count=1,
                    quote_volume=quote_notional,
                    taker_buy_volume=(trade.quantity if trade.buyer_is_aggressor else Decimal(0)),
                    taker_sell_volume=(
                        trade.quantity if not trade.buyer_is_aggressor else Decimal(0)
                    ),
                    taker_buy_quote_volume=(
                        quote_notional if trade.buyer_is_aggressor else Decimal(0)
                    ),
                    taker_sell_quote_volume=(
                        quote_notional if not trade.buyer_is_aggressor else Decimal(0)
                    ),
                )
            else:
                self._current[key] = Candle(
                    symbol=current.symbol,
                    interval_seconds=current.interval_seconds,
                    open_ts_ms=current.open_ts_ms,
                    open=current.open,
                    high=max(current.high, trade.price),
                    low=min(current.low, trade.price),
                    close=trade.price,
                    volume=current.volume + trade.quantity,
                    trade_count=current.trade_count + 1,
                    quote_volume=current.quote_volume + quote_notional,
                    taker_buy_volume=(
                        current.taker_buy_volume
                        + (trade.quantity if trade.buyer_is_aggressor else Decimal(0))
                    ),
                    taker_sell_volume=(
                        current.taker_sell_volume
                        + (trade.quantity if not trade.buyer_is_aggressor else Decimal(0))
                    ),
                    taker_buy_quote_volume=(
                        current.taker_buy_quote_volume
                        + (quote_notional if trade.buyer_is_aggressor else Decimal(0))
                    ),
                    taker_sell_quote_volume=(
                        current.taker_sell_quote_volume
                        + (quote_notional if not trade.buyer_is_aggressor else Decimal(0))
                    ),
                )
        return completed

    def snapshot(self, symbol: str) -> tuple[Candle, ...]:
        return tuple(
            self._current[(symbol, interval)]
            for interval in self.intervals
            if (symbol, interval) in self._current
        )

    def series(self, symbol: str, interval_seconds: int) -> tuple[Candle, ...]:
        TIMEFRAME_REGISTRY.validate_builder(interval_seconds)
        if interval_seconds not in self.intervals:
            raise ValueError("이 CandleBuilder에 설정하지 않은 시간구간입니다.")
        key = (symbol, interval_seconds)
        current = self._current.get(key)
        return (*self._history.get(key, ()), *((current,) if current is not None else ()))

    def completed_series(self, symbol: str, interval_seconds: int) -> tuple[Candle, ...]:
        TIMEFRAME_REGISTRY.validate_builder(interval_seconds)
        if interval_seconds not in self.intervals:
            raise ValueError("이 CandleBuilder에 설정하지 않은 시간구간입니다.")
        return tuple(self._history.get((symbol, interval_seconds), ()))

    @property
    def diagnostics(self) -> CandleBuilderDiagnostics:
        return CandleBuilderDiagnostics(
            accepted_trades=self._accepted_trades,
            duplicate_events=self._duplicate_events,
            out_of_order_trades=self._out_of_order_trades,
        )
