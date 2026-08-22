"""실제 거래 틱만 사용해 1·5·15·60초 봉을 결정적으로 생성한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.app.domain.market import TradeTick


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


class CandleBuilder:
    ALLOWED_INTERVALS = (1, 5, 15, 60)

    def __init__(self) -> None:
        self._current: dict[tuple[str, int], Candle] = {}

    def add(self, trade: TradeTick) -> list[Candle]:
        completed: list[Candle] = []
        for seconds in self.ALLOWED_INTERVALS:
            key = (trade.symbol, seconds)
            interval_ms = seconds * 1000
            bucket = trade.trade_ts_ms - trade.trade_ts_ms % interval_ms
            current = self._current.get(key)
            if current is None or current.open_ts_ms != bucket:
                if current is not None:
                    completed.append(current)
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
                )
        return completed

    def snapshot(self, symbol: str) -> tuple[Candle, ...]:
        return tuple(
            self._current[(symbol, interval)]
            for interval in self.ALLOWED_INTERVALS
            if (symbol, interval) in self._current
        )
