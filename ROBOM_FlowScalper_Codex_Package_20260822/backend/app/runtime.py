"""페이퍼 전용 런의 상태와 fixture 수직 슬라이스를 관리한다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from uuid import uuid4

from backend.app.adapters.fixture import FixtureMarketData
from backend.app.api.dashboard import build_dashboard_snapshot
from backend.app.build_identity import APP_VERSION, STRATEGY_VERSION, git_commit
from backend.app.clocks import Clock, SystemClock
from backend.app.domain.models import (
    MarketDataState,
    MarketEvent,
    RuntimeMode,
    SystemStatus,
    Venue,
)
from backend.app.domain.safety import assert_paper_only
from backend.app.live_public import (
    LiveBootstrapProbe,
    LivePublicBootstrapper,
    PublicDataUnavailable,
)
from backend.app.storage.sqlite import SQLiteLedger


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
    ledger: SQLiteLedger | None = None
    venue: Venue = Venue.FIXTURE
    market_data_state: MarketDataState = MarketDataState.FIXTURE
    wide_symbol_count: int = 0
    deep_symbol_count: int = 0
    processing_lag_p95_ms: float | None = None
    runtime_health_flags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        assert_paper_only(self.mode, os.environ)
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            if self.venue is Venue.FIXTURE:
                self.venue = Venue.BINANCE_USDM
            self.market_data_state = MarketDataState.DISCONNECTED
            self.paused = True
            self.position_visible = False
            self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        if self.ledger is not None and self.ledger.get_run(self.run_id) is None:
            self._start_ledger_run()

    def boot_fixture(self, event_count: int = 40) -> None:
        if self.mode is not RuntimeMode.FIXTURE_OFFLINE:
            raise ValueError("fixture 부팅은 FIXTURE_OFFLINE 모드에서만 가능합니다.")
        generator = FixtureMarketData(self.clock, self.run_id)
        self._events.extend(generator.events(event_count))
        self._ensure_fixture_completed_trade()

    @property
    def events(self) -> tuple[MarketEvent, ...]:
        return tuple(self._events)

    def status(self) -> SystemStatus:
        symbols = {event.symbol for event in self._events}
        current_equity = 1000.0
        if self.ledger is not None:
            net = sum(
                float(str(trade["net_pnl_usdt"])) for trade in self.ledger.list_trades(self.run_id)
            )
            current_equity += net
        flags = (
            ["OFFLINE_SIMULATION"]
            if self.mode is RuntimeMode.FIXTURE_OFFLINE
            else list(self.runtime_health_flags)
        )
        if self.paused:
            flags.append("PAPER_ENTRIES_PAUSED")
        return SystemStatus(
            mode=self.mode,
            market_data_state=self.market_data_state,
            venue=self.venue,
            run_id=self.run_id,
            current_equity_usdt=current_equity,
            wide_symbols=self.wide_symbol_count or len(symbols),
            deep_symbols=self.deep_symbol_count or min(len(symbols), 10),
            processing_lag_p95_ms=self.processing_lag_p95_ms,
            health_flags=tuple(flags),
        )

    async def boot_live_public(self, probe: LiveBootstrapProbe | None = None) -> bool:
        if self.mode is not RuntimeMode.LIVE_SHADOW_PAPER:
            raise ValueError("LIVE 부트스트랩은 LIVE_SHADOW_PAPER 모드에서만 가능합니다.")
        active_probe = probe or LivePublicBootstrapper()
        self.market_data_state = MarketDataState.RECONNECTING
        self.paused = True
        self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        other_venue = Venue.BYBIT_LINEAR if self.venue is Venue.BINANCE_USDM else Venue.BINANCE_USDM
        for candidate_venue in (self.venue, other_venue):
            if candidate_venue is not self.venue:
                self._switch_venue_run(candidate_venue)
            try:
                result = await active_probe.bootstrap(
                    candidate_venue, run_id=self.run_id, clock=self.clock
                )
            except PublicDataUnavailable as error:
                self._record_public_failure(candidate_venue, error)
                continue
            if result.venue is not candidate_venue:
                self._record_public_failure(
                    candidate_venue,
                    PublicDataUnavailable("probe 거래소 식별자 불일치"),
                )
                continue
            self.venue = result.venue
            self._events = list(result.events)
            self.wide_symbol_count = result.wide_symbol_count
            self.deep_symbol_count = result.deep_symbol_count
            self.processing_lag_p95_ms = result.websocket_lag_ms
            self.market_data_state = MarketDataState.LIVE
            self.runtime_health_flags = ["PUBLIC_DATA_VERIFIED", "NO_AUTH_HEADERS"]
            self.paused = result.websocket_lag_ms > 1_500
            if self.paused:
                self.runtime_health_flags.append("CRITICAL_MARKET_LAG_ENTRY_LOCK")
            self._log(
                "MARKET_DATA",
                f"{result.venue.value} 공개 이벤트 검증 · "
                f"{result.eligible_symbol_count}개 eligible · 자격 증명 없음",
            )
            return True
        self.market_data_state = MarketDataState.DISCONNECTED
        self.runtime_health_flags.append("PUBLIC_DATA_UNAVAILABLE")
        return False

    def dashboard(self) -> dict[str, object]:
        persisted_trades = tuple(self.ledger.list_trades()) if self.ledger is not None else ()
        return build_dashboard_snapshot(
            self.status(),
            self.events,
            paused=self.paused,
            position_visible=self.position_visible,
            control_logs=tuple(self.control_logs),
            archived_run_ids=tuple(self.archived_run_ids),
            persisted_trades=persisted_trades,
            storage_label="SQLite transactional ledger"
            if self.ledger is not None
            else "fixture memory",
            api_host=(
                f"{os.environ.get('ROBOM_HOST', '127.0.0.1')}:"
                f"{os.environ.get('ROBOM_PORT', '8765')}"
            ),
        )

    def set_paused(self, paused: bool) -> None:
        if (
            not paused
            and self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            and (
                self.market_data_state is not MarketDataState.LIVE
                or "CRITICAL_MARKET_LAG_ENTRY_LOCK" in self.runtime_health_flags
            )
        ):
            self.paused = True
            self._log("RISK", "검증된 LIVE 데이터가 없어 PAPER 진입 재개 차단")
            return
        self.paused = paused
        self._log("RISK", "페이퍼 신규 진입 일시정지" if paused else "페이퍼 신규 진입 재개")

    def emergency_paper_close(self) -> None:
        self.position_visible = False
        self._log("EXIT", "현재 fixture 페이퍼 포지션 비상종료 시뮬레이션")

    def start_new_run(self) -> str:
        previous_run_id = self.run_id
        self.archived_run_ids.append(self.run_id)
        if self.ledger is not None:
            trades = self.ledger.list_trades(previous_run_id)
            self.ledger.finalize_run(
                previous_run_id,
                finalized_ts_ms=self.clock.utc_ms(),
                summary={"trade_count": len(trades), "preserved": True},
            )
        self.run_id = f"run-{uuid4().hex[:12]}"
        self._events.clear()
        self.paused = False
        self.position_visible = True
        if self.ledger is not None:
            self._start_ledger_run()
        if self.mode is RuntimeMode.FIXTURE_OFFLINE:
            self.boot_fixture()
        else:
            self.market_data_state = MarketDataState.DISCONNECTED
            self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
            self.paused = True
        self._log("RISK", "기존 Run 보존 후 새 PAPER Run 생성")
        return self.run_id

    def _start_ledger_run(self) -> None:
        if self.ledger is None:
            return
        self.ledger.start_run(
            self.run_id,
            mode=self.mode.value,
            venue=self.venue.value,
            config={
                "starting_equity_usdt": "1000",
                "execution": "PAPER",
                "seed": 20260822,
                "app_version": APP_VERSION,
                "strategy_version": STRATEGY_VERSION,
                "git_commit": git_commit(),
            },
            started_ts_ms=self.clock.utc_ms(),
        )

    def _ensure_fixture_completed_trade(self) -> None:
        if self.ledger is None or self.ledger.list_trades(self.run_id):
            return
        timestamp = self.clock.utc_ms()
        fixture_path = (
            ("OBSERVING", "FIXTURE_BOOK_VALID"),
            ("ARMED", "LSA_CONFIRMED"),
            ("ENTRY_PENDING", "ENTRY_IOC"),
            ("PROTECTED", "FULL_FILL_WITH_PROTECTION"),
            ("CLOSED", "TAKE_PROFIT"),
        )
        for offset, (state, reason_code) in enumerate(fixture_path):
            self.ledger.append_transition(
                self.run_id,
                state=state,
                ts_ms=timestamp - (len(fixture_path) - offset) * 1_000,
                payload={
                    "trade_id": f"{self.run_id}-fixture-trade-001",
                    "reason_code": reason_code,
                    "sample_type": "OFFLINE_FIXTURE",
                },
            )
        self.ledger.save_snapshot(
            self.run_id,
            lifecycle_state="CLOSED",
            ts_ms=timestamp,
            payload={"open_position": None, "last_exit_reason": "TAKE_PROFIT"},
        )
        self.ledger.record_trade(
            {
                "trade_id": f"{self.run_id}-fixture-trade-001",
                "run_id": self.run_id,
                "venue": Venue.FIXTURE.value,
                "symbol": "BTCUSDT",
                "strategy_id": "LSA_REVERSAL_V1",
                "side": "LONG",
                "entry_ts_ms": timestamp - 184_000,
                "exit_ts_ms": timestamp,
                "entry_price": "100.10",
                "exit_price": "101.90",
                "initial_stop": "99.55",
                "take_profit": "101.90",
                "quantity": "1",
                "exit_reason": "TAKE_PROFIT",
                "gross_pnl_usdt": "1.80",
                "fees_usdt": "0.1212",
                "slippage_usdt": "0.20",
                "net_pnl_usdt": "1.4788",
                "mae_r": -0.22,
                "mfe_r": 1.41,
                "holding_ms": 184_000,
                "flags": ["OFFLINE_FIXTURE"],
                "config_hash": "fixture-config-sha256",
                "strategy_version": "1",
                "regime": "RANGE",
                "profile": "BASE",
            }
        )

    def _switch_venue_run(self, venue: Venue) -> None:
        previous_run_id = self.run_id
        self.archived_run_ids.append(previous_run_id)
        if self.ledger is not None:
            self.ledger.finalize_run(
                previous_run_id,
                finalized_ts_ms=self.clock.utc_ms(),
                summary={"reason": "PUBLIC_VENUE_FAILOVER", "preserved": True},
            )
        self.run_id = f"run-{uuid4().hex[:12]}"
        self.venue = venue
        self._events.clear()
        self.wide_symbol_count = 0
        self.deep_symbol_count = 0
        if self.ledger is not None:
            self._start_ledger_run()

    def _record_public_failure(self, venue: Venue, error: PublicDataUnavailable) -> None:
        flag = f"PUBLIC_DATA_BOOTSTRAP_FAILED_{venue.value}"
        self.runtime_health_flags.append(flag)
        self._log("MARKET_DATA", f"{flag} · LIVE 전환 차단")
        if self.ledger is not None:
            self.ledger.record_incident(
                f"public-failure-{self.run_id}-{venue.value}-{uuid4().hex[:8]}",
                run_id=self.run_id,
                severity="WARN",
                category="PUBLIC_DATA_BOOTSTRAP",
                ts_ms=self.clock.utc_ms(),
                payload={"venue": venue.value, "error_type": type(error).__name__},
            )

    def _log(self, category: str, message: str) -> None:
        self.control_logs.append(
            {
                "ts_ms": self.clock.utc_ms(),
                "category": category,
                "level": "INFO",
                "message": message,
            }
        )
