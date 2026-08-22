"""READY·LIVE·DEMO·REPLAY를 격리한 페이퍼 전용 Run 상태를 관리한다."""

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
    mode: RuntimeMode = RuntimeMode.READY
    clock: Clock = field(default_factory=SystemClock)
    run_id: str = "ready"
    _events: list[MarketEvent] = field(default_factory=list)
    paused: bool = True
    position_visible: bool = False
    archived_run_ids: list[str] = field(default_factory=list)
    control_logs: list[dict[str, object]] = field(default_factory=list)
    ledger: SQLiteLedger | None = None
    venue: Venue = Venue.NONE
    market_data_state: MarketDataState = MarketDataState.DISCONNECTED
    wide_symbol_count: int = 0
    deep_symbol_count: int = 0
    processing_lag_p95_ms: float | None = None
    runtime_health_flags: list[str] = field(default_factory=list)
    unrealized_pnl_usdt: float = 0.0

    def __post_init__(self) -> None:
        assert_paper_only(self.mode, os.environ)
        if self.mode is RuntimeMode.READY:
            self.run_id = "ready"
            self.venue = Venue.NONE
            self.market_data_state = MarketDataState.DISCONNECTED
            self.paused = True
            self.position_visible = False
            self.runtime_health_flags = ["READY_NOT_STARTED"]
        elif self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            if self.venue is Venue.FIXTURE:
                self.venue = Venue.BINANCE_USDM
            if self.venue is Venue.NONE:
                self.venue = Venue.BINANCE_USDM
            self.market_data_state = MarketDataState.DISCONNECTED
            self.paused = True
            self.position_visible = False
            self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        elif self.mode is RuntimeMode.DEMO_FIXTURE:
            self.venue = Venue.FIXTURE
            self.market_data_state = MarketDataState.FIXTURE
            self.paused = False
            self.position_visible = True
            self.runtime_health_flags = ["OFFLINE_DEMO_ISOLATED"]
        elif self.mode is RuntimeMode.REPLAY:
            self.paused = True
            self.position_visible = False
            self.runtime_health_flags = ["REPLAY_READ_ONLY"]
        if (
            self.mode is not RuntimeMode.READY
            and self.ledger is not None
            and self.ledger.get_run(self.run_id) is None
        ):
            self._start_ledger_run()

    def boot_demo(self, event_count: int = 40) -> None:
        if self.mode is not RuntimeMode.DEMO_FIXTURE:
            raise ValueError("fixture 부팅은 DEMO_FIXTURE 모드에서만 가능합니다.")
        generator = FixtureMarketData(self.clock, self.run_id)
        self._events.extend(generator.events(event_count))
        self._ensure_fixture_completed_trade()

    def boot_fixture(self, event_count: int = 40) -> None:
        """0.1 호출 호환용 별칭이며 DEMO_FIXTURE에서만 동작한다."""

        self.boot_demo(event_count)

    @property
    def events(self) -> tuple[MarketEvent, ...]:
        return tuple(self._events)

    def status(self) -> SystemStatus:
        symbols = {event.symbol for event in self._events}
        realized = 0.0
        fees = 0.0
        slippage = 0.0
        trade_count = 0
        if self.ledger is not None:
            trades = self.ledger.list_trades(self.run_id)
            trade_count = len(trades)
            realized = sum(float(str(trade["net_pnl_usdt"])) for trade in trades)
            fees = sum(float(str(trade["fees_usdt"])) for trade in trades)
            slippage = sum(float(str(trade["slippage_usdt"])) for trade in trades)
        flags = list(self.runtime_health_flags)
        if self.paused:
            flags.append("PAPER_ENTRIES_PAUSED")
        return SystemStatus(
            mode=self.mode,
            market_data_state=self.market_data_state,
            venue=self.venue,
            run_id=self.run_id,
            current_equity_usdt=1000.0 + realized + self.unrealized_pnl_usdt,
            realized_pnl_usdt=realized,
            unrealized_pnl_usdt=self.unrealized_pnl_usdt,
            cumulative_fees_usdt=fees,
            cumulative_slippage_usdt=slippage,
            trade_count=trade_count,
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
        persisted_trades = (
            tuple(self.ledger.list_trades(self.run_id))
            if self.ledger is not None and self.mode is not RuntimeMode.READY
            else ()
        )
        sample_type = (
            "DEMO_FIXTURE"
            if self.mode is RuntimeMode.DEMO_FIXTURE
            else "LIVE_PUBLIC"
            if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            else None
        )
        history_trades = (
            tuple(
                trade
                for trade in self.ledger.list_trades()
                if trade.get("sample_type", "LIVE_PUBLIC") == sample_type
            )
            if self.ledger is not None and sample_type is not None
            else ()
        )
        return build_dashboard_snapshot(
            self.status(),
            self.events,
            paused=self.paused,
            position_visible=self.position_visible,
            control_logs=tuple(self.control_logs),
            archived_run_ids=tuple(self.archived_run_ids),
            persisted_trades=persisted_trades,
            history_trades=history_trades,
            storage_label="SQLite transactional ledger"
            if self.ledger is not None
            else "fixture memory",
            api_host=(
                f"{os.environ.get('ROBOM_HOST', '127.0.0.1')}:"
                f"{os.environ.get('ROBOM_PORT', '8765')}"
            ),
        )

    def set_paused(self, paused: bool) -> None:
        if self.mode is RuntimeMode.READY:
            self.paused = True
            self._log("RISK", "실시간 PAPER 시작 전에는 진입할 수 없음")
            return
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
        self._log("EXIT", "현재 PAPER 포지션 비상종료 요청")

    async def start_live_run(self, probe: LiveBootstrapProbe | None = None) -> bool:
        self._archive_current_run("USER_START_LIVE")
        self.mode = RuntimeMode.LIVE_SHADOW_PAPER
        self.run_id = f"run-{uuid4().hex[:12]}"
        self.venue = Venue.BINANCE_USDM
        self.market_data_state = MarketDataState.DISCONNECTED
        self._events.clear()
        self.paused = True
        self.position_visible = False
        self.unrealized_pnl_usdt = 0.0
        self.wide_symbol_count = 0
        self.deep_symbol_count = 0
        self.processing_lag_p95_ms = None
        self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        self._start_ledger_run()
        self._log("RUN", "Fresh LIVE PAPER Run 생성 · 자산과 손익·비용·거래 0")
        return await self.boot_live_public(probe)

    def start_demo_run(self) -> str:
        self._archive_current_run("USER_START_DEMO")
        self.mode = RuntimeMode.DEMO_FIXTURE
        self.run_id = f"demo-{uuid4().hex[:12]}"
        self.venue = Venue.FIXTURE
        self.market_data_state = MarketDataState.FIXTURE
        self._events.clear()
        self.paused = False
        self.position_visible = True
        self.unrealized_pnl_usdt = 0.0
        self.runtime_health_flags = ["OFFLINE_DEMO_ISOLATED"]
        self._start_ledger_run()
        self.boot_demo()
        self._log("RUN", "LIVE 성과와 분리된 오프라인 DEMO Run 생성")
        return self.run_id

    def start_new_run(self) -> str:
        if self.mode is RuntimeMode.READY:
            raise ValueError("READY에서는 먼저 LIVE 또는 DEMO를 시작해야 합니다.")
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
        if self.mode is RuntimeMode.DEMO_FIXTURE:
            self.boot_demo()
        else:
            self.market_data_state = MarketDataState.DISCONNECTED
            self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
            self.paused = True
        self._log("RISK", "기존 Run 보존 후 새 PAPER Run 생성")
        return self.run_id

    def _archive_current_run(self, reason: str) -> None:
        if self.mode is RuntimeMode.READY or self.ledger is None:
            return
        current = self.ledger.get_run(self.run_id)
        if current is None or current["finalized_ts_ms"] is not None:
            return
        self.archived_run_ids.append(self.run_id)
        self.ledger.finalize_run(
            self.run_id,
            finalized_ts_ms=self.clock.utc_ms(),
            summary={"reason": reason, "preserved": True},
        )

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
                "sample_type": (
                    "DEMO_FIXTURE"
                    if self.mode is RuntimeMode.DEMO_FIXTURE
                    else "LIVE_PUBLIC"
                    if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
                    else "REPLAY"
                ),
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
            ("OBSERVING", "FIXTURE_BOOK_VALID", 185_000),
            ("ARMED", "LSA_CONFIRMED", 184_500),
            ("ENTRY_PENDING", "ENTRY_IOC", 184_075),
            ("PROTECTED", "FULL_FILL_WITH_PROTECTION", 184_000),
            ("CLOSED", "TAKE_PROFIT", 0),
        )
        for state, reason_code, age_ms in fixture_path:
            evidence: dict[str, object] = {}
            if state == "ARMED":
                evidence = {
                    "planned_entry": "100.00",
                    "planned_take_profit": "102.00",
                    "planned_stop": "99.55",
                }
            elif state == "PROTECTED":
                evidence = {"actual_entry": "100.10", "protected_quantity": "1"}
            elif state == "CLOSED":
                evidence = {"actual_exit": "101.90", "remaining_quantity": "0"}
            self.ledger.append_transition(
                self.run_id,
                state=state,
                ts_ms=timestamp - age_ms,
                payload={
                    "trade_id": f"{self.run_id}-fixture-trade-001",
                    "reason_code": reason_code,
                    "sample_type": "OFFLINE_FIXTURE",
                    **evidence,
                },
            )
        self.ledger.save_snapshot(
            self.run_id,
            lifecycle_state="CLOSED",
            ts_ms=timestamp,
            payload={"open_position": None, "last_exit_reason": "TAKE_PROFIT"},
        )
        self.ledger.record_order(
            {
                "order_id": f"{self.run_id}-entry-order",
                "run_id": self.run_id,
                "trade_id": f"{self.run_id}-fixture-trade-001",
                "venue": Venue.FIXTURE.value,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "intent": "ENTRY_IOC",
                "status": "FILLED",
                "requested_qty": "1",
                "filled_qty": "1",
                "price_cap": "100.10",
                "average_fill_price": "100.10",
                "created_ts_ms": timestamp - 184_075,
                "arrival_ts_ms": timestamp - 184_000,
                "finalized_ts_ms": timestamp - 184_000,
                "fee_usdt": "0.06006",
                "slippage_usdt": "0.10",
                "reason_codes": ["FIXTURE_DEPTH_WALK"],
            }
        )
        self.ledger.record_fill(
            {
                "fill_id": f"{self.run_id}-entry-fill",
                "run_id": self.run_id,
                "order_id": f"{self.run_id}-entry-order",
                "side": "BUY",
                "planned_price": "100.00",
                "price": "100.10",
                "quantity": "1",
                "fee_usdt": "0.06006",
                "slippage_usdt": "0.10",
                "ts_ms": timestamp - 184_000,
            }
        )
        self.ledger.record_order(
            {
                "order_id": f"{self.run_id}-exit-order",
                "run_id": self.run_id,
                "trade_id": f"{self.run_id}-fixture-trade-001",
                "venue": Venue.FIXTURE.value,
                "symbol": "BTCUSDT",
                "side": "SELL",
                "intent": "TAKE_PROFIT",
                "status": "FILLED",
                "requested_qty": "1",
                "filled_qty": "1",
                "trigger_price": "102.00",
                "average_fill_price": "101.90",
                "created_ts_ms": timestamp - 75,
                "arrival_ts_ms": timestamp,
                "finalized_ts_ms": timestamp,
                "fee_usdt": "0.06114",
                "slippage_usdt": "0.10",
                "reason_codes": ["FIXTURE_EXECUTABLE_BID"],
            }
        )
        self.ledger.record_fill(
            {
                "fill_id": f"{self.run_id}-exit-fill",
                "run_id": self.run_id,
                "order_id": f"{self.run_id}-exit-order",
                "side": "SELL",
                "planned_price": "102.00",
                "price": "101.90",
                "quantity": "1",
                "fee_usdt": "0.06114",
                "slippage_usdt": "0.10",
                "ts_ms": timestamp,
            }
        )
        run_record = self.ledger.get_run(self.run_id)
        if run_record is None:
            raise RuntimeError(f"fixture 거래가 참조할 Run이 없습니다: {self.run_id}")
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
                "take_profit": "102.00",
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
                "sample_type": "DEMO_FIXTURE",
                "config_hash": str(run_record["config_hash"]),
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
