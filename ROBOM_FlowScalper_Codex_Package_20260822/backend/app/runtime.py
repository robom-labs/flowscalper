"""페이퍼 전용 런의 상태와 fixture 수직 슬라이스를 관리한다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from uuid import uuid4

from backend.app.adapters.fixture import FixtureMarketData
from backend.app.api.dashboard import build_dashboard_snapshot
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
    paused: bool = False
    position_visible: bool = True
    archived_run_ids: list[str] = field(default_factory=list)
    control_logs: list[dict[str, object]] = field(default_factory=list)

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
        flags = ["OFFLINE_SIMULATION"]
        if self.paused:
            flags.append("PAPER_ENTRIES_PAUSED")
        return SystemStatus(
            mode=self.mode,
            market_data_state=MarketDataState.FIXTURE,
            venue=Venue.FIXTURE,
            run_id=self.run_id,
            wide_symbols=len(symbols),
            deep_symbols=min(len(symbols), 10),
            health_flags=tuple(flags),
        )

    def dashboard(self) -> dict[str, object]:
        return build_dashboard_snapshot(
            self.status(),
            self.events,
            paused=self.paused,
            position_visible=self.position_visible,
            control_logs=tuple(self.control_logs),
            archived_run_ids=tuple(self.archived_run_ids),
        )

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._log("RISK", "페이퍼 신규 진입 일시정지" if paused else "페이퍼 신규 진입 재개")

    def emergency_paper_close(self) -> None:
        self.position_visible = False
        self._log("EXIT", "현재 fixture 페이퍼 포지션 비상종료 시뮬레이션")

    def start_new_run(self) -> str:
        self.archived_run_ids.append(self.run_id)
        self.run_id = f"run-{uuid4().hex[:12]}"
        self._events.clear()
        self.paused = False
        self.position_visible = True
        self.boot_fixture()
        self._log("RISK", "기존 Run 보존 후 새 PAPER Run 생성")
        return self.run_id

    def _log(self, category: str, message: str) -> None:
        self.control_logs.append(
            {
                "ts_ms": self.clock.utc_ms(),
                "category": category,
                "level": "INFO",
                "message": message,
            }
        )
