"""페이퍼 전용 런의 상태와 fixture 수직 슬라이스를 관리한다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from uuid import uuid4

from backend.app.adapters.fixture import FixtureMarketData
from backend.app.clocks import Clock, SystemClock
from backend.app.domain.models import (
    MarketDataState,
    MarketEvent,
    RuntimeMode,
    SystemStatus,
    Venue,
)
from backend.app.domain.safety import assert_paper_only


@dataclass(slots=True)
class PaperRuntime:
    mode: RuntimeMode = RuntimeMode.FIXTURE_OFFLINE
    clock: Clock = field(default_factory=SystemClock)
    run_id: str = field(default_factory=lambda: f"run-{uuid4().hex[:12]}")
    _events: list[MarketEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        assert_paper_only(self.mode, os.environ)

    def boot_fixture(self, event_count: int = 40) -> None:
        if self.mode is not RuntimeMode.FIXTURE_OFFLINE:
            raise ValueError("fixture 부팅은 FIXTURE_OFFLINE 모드에서만 가능합니다.")
        generator = FixtureMarketData(self.clock, self.run_id)
        self._events.extend(generator.events(event_count))

    @property
    def events(self) -> tuple[MarketEvent, ...]:
        return tuple(self._events)

    def status(self) -> SystemStatus:
        symbols = {event.symbol for event in self._events}
        return SystemStatus(
            mode=self.mode,
            market_data_state=MarketDataState.FIXTURE,
            venue=Venue.FIXTURE,
            run_id=self.run_id,
            wide_symbols=len(symbols),
            deep_symbols=min(len(symbols), 10),
            health_flags=("OFFLINE_SIMULATION",),
        )

