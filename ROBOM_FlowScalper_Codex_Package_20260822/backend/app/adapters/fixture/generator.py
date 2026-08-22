"""같은 시드에서 동일한 공개시세 형태의 fixture 이벤트를 생성한다."""

from __future__ import annotations

import random
from collections.abc import Iterator
from decimal import Decimal

from backend.app.clocks import Clock
from backend.app.domain.models import DataQuality, MarketEvent, Venue

DEFAULT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
)


class FixtureMarketData:
    def __init__(self, clock: Clock, run_id: str, seed: int = 20260822) -> None:
        self._clock = clock
        self._run_id = run_id
        self._rng = random.Random(seed)
        self._sequence = 0
        self._prices = {
            symbol: Decimal(100 + index * 17) for index, symbol in enumerate(DEFAULT_SYMBOLS)
        }

    def events(self, count: int = 20) -> Iterator[MarketEvent]:
        for index in range(count):
            symbol = DEFAULT_SYMBOLS[index % len(DEFAULT_SYMBOLS)]
            self._sequence += 1
            delta = Decimal(str(self._rng.choice((-0.08, -0.03, 0.02, 0.05, 0.09))))
            self._prices[symbol] += delta
            mid = self._prices[symbol].quantize(Decimal("0.01"))
            spread = Decimal("0.02")
            yield MarketEvent(
                event_id=f"fixture-{self._sequence}",
                run_id=self._run_id,
                venue=Venue.FIXTURE,
                symbol=symbol,
                event_type="BOOK_TICKER",
                venue_ts_ms=self._clock.utc_ms(),
                receive_monotonic_ns=self._clock.monotonic_ns(),
                sequence_start=self._sequence,
                sequence_end=self._sequence,
                previous_sequence_end=self._sequence - 1 if self._sequence > 1 else None,
                quality=DataQuality(is_live=False, is_stale=False, sequence_valid=True),
                data={
                    "bid": str(mid - spread / 2),
                    "bid_qty": "12.5",
                    "ask": str(mid + spread / 2),
                    "ask_qty": "11.8",
                },
            )

