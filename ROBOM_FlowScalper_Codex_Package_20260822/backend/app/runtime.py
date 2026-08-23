"""READY·LIVE·DEMO·REPLAY를 격리한 페이퍼 전용 Run 상태를 관리한다."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from backend.app.adapters.fixture import FixtureMarketData
from backend.app.analytics.reports import TradeAnalytics
from backend.app.api.dashboard import build_dashboard_snapshot
from backend.app.build_identity import APP_VERSION, STRATEGY_VERSION, git_commit
from backend.app.candidates import CandidatePlan, CandidatePlanner
from backend.app.clocks import Clock, SystemClock
from backend.app.control.operations import ProgressCallback
from backend.app.costing import CostProfile
from backend.app.domain.market import Instrument, TradeTick
from backend.app.domain.models import (
    MarketDataState,
    MarketEvent,
    RuntimeMode,
    SystemStatus,
    Venue,
)
from backend.app.domain.safety import assert_paper_only
from backend.app.execution import BookSnapshot, ExitReason
from backend.app.execution.models import PaperOrder, PaperTrade
from backend.app.execution.portfolio import PaperPortfolioEngine
from backend.app.features import BookFrame, FeatureEngine, FeatureInputError, FeatureSnapshot
from backend.app.live_public import (
    LiveBootstrapProbe,
    LivePublicBootstrapper,
    PublicDataUnavailable,
)
from backend.app.market_data.candles import Candle, CandleBuilder
from backend.app.market_data.supervisor import (
    BinancePersistentProvider,
    BybitPersistentProvider,
    PersistentPublicSupervisor,
    ProviderSelection,
    PublicStreamProvider,
)
from backend.app.ops import ProcessResourceSampler
from backend.app.regime import Regime, RegimeClassifier
from backend.app.storage.parquet import ParquetEventStore
from backend.app.storage.sqlite import RecoveryState, SQLiteLedger
from backend.app.strategies.base import CandidateDecision
from backend.app.strategies.registry import StrategyMode, StrategyRegistry
from backend.app.strategies.runtime_evaluator import EvaluatedSignal, StrategySignalEvaluator
from backend.app.strategies.shadow import ShadowLedger

_MARKET_PERSISTENCE_FLUSH_THRESHOLD = 2_000
_MARKET_PERSISTENCE_BATCH_SIZE = 2_000


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
    storage_guard: ParquetEventStore | None = None
    market_event_archive: ParquetEventStore | None = None
    venue: Venue = Venue.NONE
    market_data_state: MarketDataState = MarketDataState.DISCONNECTED
    wide_symbol_count: int = 0
    deep_symbol_count: int = 0
    processing_lag_p95_ms: float | None = None
    runtime_health_flags: list[str] = field(default_factory=list)
    unrealized_pnl_usdt: float = 0.0
    candle_builder: CandleBuilder = field(default_factory=CandleBuilder)
    selected_symbol: str = "BTCUSDT"
    selected_interval_seconds: int = 180
    live_selection: ProviderSelection | None = None
    _supervisor: PersistentPublicSupervisor | None = field(default=None, init=False, repr=False)
    strategy_registry: StrategyRegistry = field(default_factory=StrategyRegistry)
    strategy_evaluator: StrategySignalEvaluator = field(default_factory=StrategySignalEvaluator)
    regime_classifier: RegimeClassifier = field(default_factory=RegimeClassifier)
    feature_engines: dict[str, FeatureEngine] = field(default_factory=dict)
    latest_features: dict[str, FeatureSnapshot] = field(default_factory=dict)
    latest_regimes: dict[str, Regime] = field(default_factory=dict)
    strategy_signals: dict[tuple[str, str, str], EvaluatedSignal] = field(default_factory=dict)
    strategy_evaluation_count: int = 0
    qualified_signal_count: int = 0
    shadow_ledger: ShadowLedger = field(init=False)
    paper_portfolio: PaperPortfolioEngine = field(init=False)
    latest_books: dict[str, BookSnapshot] = field(default_factory=dict)
    candidate_planner: CandidatePlanner = field(default_factory=CandidatePlanner)
    plan_rejections: list[dict[str, object]] = field(default_factory=list)
    data_gap_since_ms: dict[str, int] = field(default_factory=dict)
    strategy_evaluation_interval_ms: int = 500
    _last_strategy_evaluation_ms: dict[str, int] = field(default_factory=dict)
    _market_event_buffer: list[dict[str, object]] = field(default_factory=list)
    _candle_buffer: list[dict[str, object]] = field(default_factory=list)
    _persisted_main_order_ids: set[str] = field(default_factory=set)
    _persisted_main_trade_ids: set[str] = field(default_factory=set)
    _persisted_shadow_trade_ids: set[str] = field(default_factory=set)
    _persisted_audit_count: int = 0
    _persistence_fault_count: int = 0
    _persistence_buffer_dropped: int = 0
    _last_persistence_error: str | None = None
    _persistence_flush_count: int = 0
    _persistence_flush_last_ms: float = 0.0
    _persistence_flush_max_ms: float = 0.0
    _historical_live_trades: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _historical_shadow_trades: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _last_storage_check_ns: int | None = None
    _storage_entry_allowed: bool = True
    _storage_health_snapshot: dict[str, object] = field(default_factory=dict)
    _recovery_revalidation_symbols: set[str] = field(default_factory=set)
    _manual_pause_requested: bool = False
    resource_sampler: ProcessResourceSampler = field(init=False, repr=False)
    _persistence_lock: RLock = field(default_factory=RLock, repr=False)

    def __post_init__(self) -> None:
        assert_paper_only(self.mode, os.environ)
        storage_path = self.ledger.path.parent if self.ledger is not None else Path.cwd()
        self.resource_sampler = ProcessResourceSampler(storage_path)
        self.shadow_ledger = ShadowLedger(self.strategy_registry.strategy_ids)
        self.paper_portfolio = PaperPortfolioEngine(
            run_id=self.run_id,
            strategy_ids=self.strategy_registry.strategy_ids,
            shadow_ledger=self.shadow_ledger,
        )
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
        self.paper_portfolio.venue = self.venue
        if (
            self.mode is not RuntimeMode.READY
            and self.ledger is not None
            and self.ledger.get_run(self.run_id) is None
        ):
            self._start_ledger_run()
        elif self.ledger is not None:
            self._refresh_dashboard_trade_cache()

    def boot_demo(self, event_count: int = 240) -> None:
        if self.mode is not RuntimeMode.DEMO_FIXTURE:
            raise ValueError("fixture 부팅은 DEMO_FIXTURE 모드에서만 가능합니다.")
        generator = FixtureMarketData(self.clock, self.run_id)
        events = tuple(generator.events(event_count))
        self._events.extend(events)
        for index, event in enumerate(events):
            bid = Decimal(str(event.data["bid"]))
            ask = Decimal(str(event.data["ask"]))
            self.candle_builder.add(
                TradeTick(
                    venue=event.venue,
                    symbol=event.symbol,
                    price=(bid + ask) / Decimal(2),
                    quantity=Decimal("0.1") + Decimal(index % 5) / Decimal(100),
                    trade_ts_ms=event.venue_ts_ms,
                    buyer_is_aggressor=index % 2 == 0,
                )
            )
        if self.ledger is not None:
            with self._persistence_lock:
                self._market_event_buffer.extend(
                    self._persistable_market_event(event) for event in events
                )
            fixture_symbols = sorted({event.symbol for event in events})
            self._buffer_completed_candles(
                [
                    candle
                    for symbol in fixture_symbols
                    for interval in (1, 180)
                    for candle in self.candle_builder.series(symbol, interval)
                ]
            )
            self._flush_persistence()
        self._ensure_fixture_completed_trade()

    def boot_fixture(self, event_count: int = 240) -> None:
        """0.1 호출 호환용 별칭이며 DEMO_FIXTURE에서만 동작한다."""

        self.boot_demo(event_count)

    @property
    def events(self) -> tuple[MarketEvent, ...]:
        return tuple(self._events)

    @property
    def _recovery_revalidation_symbol(self) -> str | None:
        """기존 단일 포지션 검증 계약에 대한 읽기 호환 속성이다."""

        return next(iter(sorted(self._recovery_revalidation_symbols)), None)

    def status(self) -> SystemStatus:
        symbols = {event.symbol for event in self._events}
        realized = 0.0
        fees = 0.0
        slippage = 0.0
        trade_count = 0
        current_equity = 1000.0
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            summary = self.paper_portfolio.main_summary(self._current_main_book())
            realized = float(summary["realized"])
            self.unrealized_pnl_usdt = float(summary["unrealized"])
            fees = float(summary["fees"])
            slippage = float(summary["slippage"])
            trade_count = int(summary["trade_count"])
            current_equity = float(summary["equity"])
        elif self.ledger is not None:
            trades = self.ledger.list_trades(self.run_id)
            trade_count = len(trades)
            realized = sum(float(str(trade["net_pnl_usdt"])) for trade in trades)
            fees = sum(float(str(trade["fees_usdt"])) for trade in trades)
            slippage = sum(float(str(trade["slippage_usdt"])) for trade in trades)
            current_equity = 1000.0 + realized + self.unrealized_pnl_usdt
        flags = list(self.runtime_health_flags)
        if self.paused:
            flags.append("PAPER_ENTRIES_PAUSED")
        return SystemStatus(
            mode=self.mode,
            market_data_state=self.market_data_state,
            venue=self.venue,
            run_id=self.run_id,
            current_equity_usdt=current_equity,
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

    def restore_recovery_state(self, recovered: RecoveryState) -> bool:
        """checksum 검증된 최신 Run의 전략 설정·계좌·포지션·거래를 복구한다."""

        if recovered.run_id != self.run_id or recovered.venue != self.venue.value:
            self._lock_recovery("RECOVERY_RUN_OR_VENUE_MISMATCH")
            return False
        if self.ledger is None:
            self._lock_recovery("RECOVERY_LEDGER_MISSING")
            return False
        try:
            latest_settings: dict[str, Mapping[str, object]] = {}
            for setting_row in self.ledger.list_strategy_settings(self.run_id):
                latest_settings[str(setting_row["strategy_id"])] = setting_row
            for strategy_id, latest_setting in latest_settings.items():
                self.strategy_registry.configure(
                    strategy_id,
                    mode=StrategyMode(str(latest_setting["mode"])),
                    long_enabled=bool(latest_setting["long_enabled"]),
                    short_enabled=bool(latest_setting["short_enabled"]),
                )
            portfolio_payload = recovered.payload.get("portfolio")
            if isinstance(portfolio_payload, Mapping):
                self.paper_portfolio.restore_state(portfolio_payload)
                self.paper_portfolio.reconcile_persisted_main_trades(
                    self.ledger.list_trades(self.run_id)
                )
            elif recovered.lifecycle_state not in {"SCANNING", "CLOSED"}:
                raise ValueError("열린 lifecycle snapshot에 복구 가능한 portfolio가 없습니다.")
            self._persisted_main_order_ids = {
                str(order["order_id"]) for order in self.ledger.list_orders(self.run_id)
            }
            self._persisted_main_trade_ids = {
                str(trade["trade_id"]) for trade in self.ledger.list_trades(self.run_id)
            }
            self._persisted_shadow_trade_ids = {
                str(trade["shadow_trade_id"])
                for trade in self.ledger.list_shadow_trades(self.run_id)
            }
        except (KeyError, TypeError, ValueError) as error:
            self._lock_recovery(f"RECOVERY_STATE_REJECTED:{type(error).__name__}")
            return False
        self.position_visible = self.paper_portfolio.main.position is not None
        recovery_plan = (
            self.paper_portfolio.main.position.plan
            if self.paper_portfolio.main.position is not None
            else self.paper_portfolio.main.pending_entry.plan
            if self.paper_portfolio.main.pending_entry is not None
            else None
        )
        recovery_symbols = {
            *(
                pending.plan.symbol
                for account in self.paper_portfolio.accounts
                for pending in account.pending_entries.values()
            ),
            *(
                position.plan.symbol
                for account in self.paper_portfolio.accounts
                for position in account.positions.values()
            ),
        }
        if recovery_plan is not None:
            plan = recovery_plan
            self.selected_symbol = plan.symbol
        elif recovery_symbols:
            self.selected_symbol = sorted(recovery_symbols)[0]
        snapshot_ts = int(
            str(
                recovered.payload.get(
                    "snapshot_ts_ms",
                    recovery_plan.signal_time_ms if recovery_plan is not None else 0,
                )
            )
        )
        self._recovery_revalidation_symbols = recovery_symbols
        for symbol in recovery_symbols:
            self.data_gap_since_ms[symbol] = snapshot_ts
        self.paused = True
        self.runtime_health_flags = [
            "PAPER_STATE_RECOVERED",
            "ENTRY_LOCK_RECOVERY_REVALIDATION",
        ]
        self._log(
            "RECOVERY",
            f"{recovered.lifecycle_state} PAPER 상태 복구 · fresh 공개호가 전 신규진입 잠금",
        )
        return True

    def _lock_recovery(self, reason: str) -> None:
        self._recovery_revalidation_symbols.clear()
        self.paused = True
        self.position_visible = False
        self.paper_portfolio.main.risk_state.faulted = True
        self.runtime_health_flags = ["RECOVERY_FAIL_CLOSED", reason]
        self._log("RECOVERY", f"복구 무결성 실패 · 신규 PAPER 진입 차단 · {reason}")

    def _refresh_storage_safety(self, *, force: bool = False) -> bool:
        if self.storage_guard is None:
            self._storage_entry_allowed = True
            self._storage_health_snapshot = {
                "storage_entry_allowed": True,
                "disk_pressure_entry_lock": False,
                "storage_guard_enabled": False,
            }
            return True
        now_ns = self.clock.monotonic_ns()
        if (
            not force
            and self._last_storage_check_ns is not None
            and now_ns - self._last_storage_check_ns < 1_000_000_000
        ):
            return self._storage_entry_allowed
        self._last_storage_check_ns = now_ns
        try:
            health = self.storage_guard.health()
            self._storage_entry_allowed = health.entry_allowed
            self._storage_health_snapshot = {
                "storage_entry_allowed": health.entry_allowed,
                "disk_pressure_entry_lock": not health.entry_allowed,
                "storage_guard_enabled": True,
                "storage_free_bytes": health.free_bytes,
                "storage_free_ratio": round(health.free_ratio, 6),
                "storage_lock_reason": health.reason or "NONE",
            }
        except OSError as error:
            self._storage_entry_allowed = False
            self._storage_health_snapshot = {
                "storage_entry_allowed": False,
                "disk_pressure_entry_lock": True,
                "storage_guard_enabled": True,
                "storage_lock_reason": f"STORAGE_HEALTH_ERROR:{type(error).__name__}",
            }
        if not self._storage_entry_allowed and self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            self.paused = True
            if "STORAGE_PRESSURE_ENTRY_LOCK" not in self.runtime_health_flags:
                self.runtime_health_flags.append("STORAGE_PRESSURE_ENTRY_LOCK")
        else:
            self.runtime_health_flags = [
                flag for flag in self.runtime_health_flags if flag != "STORAGE_PRESSURE_ENTRY_LOCK"
            ]
        return self._storage_entry_allowed

    def _operational_diagnostics(self) -> dict[str, object]:
        self._refresh_storage_safety()
        return {
            "server_time_ms": self.clock.utc_ms(),
            "display_timezone": "Asia/Seoul",
            **self.resource_sampler.sample(),
            **self._storage_health_snapshot,
            "persistence_fault_count": self._persistence_fault_count,
            "persistence_buffer_dropped": self._persistence_buffer_dropped,
            "persistence_last_error": self._last_persistence_error or "NONE",
            "persistence_flush_count": self._persistence_flush_count,
            "persistence_flush_last_ms": round(self._persistence_flush_last_ms, 3),
            "persistence_flush_max_ms": round(self._persistence_flush_max_ms, 3),
            "event_memory_count": len(self._events),
            "event_memory_limit": 10_000,
            "market_persistence_buffer": len(self._market_event_buffer),
            "candle_persistence_buffer": len(self._candle_buffer),
            "strategy_evaluation_interval_ms": self.strategy_evaluation_interval_ms,
            "manual_pause_requested": self._manual_pause_requested,
            "automatic_recovery_enabled": True,
        }

    def _handle_persistence_fault(self, error: Exception) -> None:
        self._persistence_fault_count += 1
        self._last_persistence_error = f"{type(error).__name__}: {error}"
        self.paused = True
        self.paper_portfolio.main.risk_state.faulted = True
        if "PERSISTENCE_FAULT_ENTRY_LOCK" not in self.runtime_health_flags:
            self.runtime_health_flags.append("PERSISTENCE_FAULT_ENTRY_LOCK")
        self._log(
            "STORAGE",
            f"원장 저장 실패 · 신규 PAPER 진입 영구 차단 · {type(error).__name__}",
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

    async def start_persistent_live(
        self,
        progress: ProgressCallback | None = None,
    ) -> bool:
        if self.mode is not RuntimeMode.LIVE_SHADOW_PAPER:
            raise ValueError("지속 LIVE supervisor는 LIVE_SHADOW_PAPER에서만 시작합니다.")
        await self.shutdown_supervisor()
        pinned_symbols = tuple(sorted(self._recovery_revalidation_symbols))
        providers: dict[Venue, PublicStreamProvider] = {
            Venue.BINANCE_USDM: BinancePersistentProvider(pinned_symbols=pinned_symbols),
            Venue.BYBIT_LINEAR: BybitPersistentProvider(pinned_symbols=pinned_symbols),
        }
        primary_venue = self.venue if self.venue in providers else Venue.BINANCE_USDM
        candidate_venues = (
            (primary_venue,)
            if self._recovery_revalidation_symbols
            else (
                primary_venue,
                Venue.BYBIT_LINEAR if primary_venue is Venue.BINANCE_USDM else Venue.BINANCE_USDM,
            )
        )
        self.market_data_state = MarketDataState.RECONNECTING
        self.paused = True
        self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        for index, candidate_venue in enumerate(candidate_venues):
            if progress is not None:
                await progress(
                    "CONNECTING_PRIMARY" if index == 0 else "CONNECTING_FALLBACK",
                    "주 거래소 공개시장과 정상 호가를 확인하고 있습니다"
                    if index == 0
                    else "대체 거래소 공개시장과 정상 호가를 확인하고 있습니다",
                )
            provider = providers[candidate_venue]
            if candidate_venue is not self.venue:
                self._switch_venue_run(candidate_venue)
            supervisor = PersistentPublicSupervisor(
                provider,
                run_id=self.run_id,
                clock=self.clock,
                sink=self.ingest_live_event,
                protected_symbols=self._protected_deep_symbols,
            )
            try:
                selection = await supervisor.start()
            except asyncio.CancelledError:
                await supervisor.stop()
                self.paused = True
                self.market_data_state = MarketDataState.DISCONNECTED
                self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
                raise
            except PublicDataUnavailable as error:
                await supervisor.stop()
                self._record_public_failure(candidate_venue, error)
                continue
            self._supervisor = supervisor
            self.live_selection = selection
            self._record_universe_selection(selection, reason="INITIAL_DEEP_SELECTION")
            self.venue = selection.venue
            self.wide_symbol_count = len(selection.wide_symbols)
            self.deep_symbol_count = len(selection.deep_symbols)
            self.selected_symbol = (
                "BTCUSDT" if "BTCUSDT" in selection.deep_symbols else selection.deep_symbols[0]
            )
            self.processing_lag_p95_ms = supervisor.telemetry.lag_p95_ms
            self.market_data_state = MarketDataState.LIVE
            self.paused = (
                supervisor.telemetry.entry_locked
                or self.paper_portfolio.main.risk_state.faulted
                or bool(self._recovery_revalidation_symbols)
            )
            self.runtime_health_flags = ["PUBLIC_SUPERVISOR_RUNNING", "NO_AUTH_HEADERS"]
            self._refresh_supervisor_entry_safety()
            if self.paper_portfolio.main.risk_state.faulted:
                self.runtime_health_flags.append("RECOVERY_FAIL_CLOSED")
            if self._recovery_revalidation_symbols:
                self.runtime_health_flags.append("ENTRY_LOCK_RECOVERY_REVALIDATION")
            if not self._refresh_storage_safety(force=True):
                self.paused = True
            self._log(
                "MARKET_DATA",
                f"{selection.venue.value} 지속 공개 supervisor 시작 · "
                f"wide {len(selection.wide_symbols)} · deep {len(selection.deep_symbols)}",
            )
            return True
        self.market_data_state = MarketDataState.DISCONNECTED
        if self._recovery_revalidation_symbols:
            self.runtime_health_flags.append("RECOVERED_POSITION_PUBLIC_DATA_UNAVAILABLE")
        self.runtime_health_flags.append("PUBLIC_DATA_UNAVAILABLE")
        return False

    def ingest_live_event(self, event: MarketEvent) -> None:
        if event.run_id != self.run_id or event.venue is not self.venue:
            raise ValueError("다른 Run 또는 거래소 이벤트를 LIVE 런타임에 섞을 수 없습니다.")
        self._refresh_supervisor_entry_safety()
        self._events.append(event)
        if self.ledger is not None and self.mode is not RuntimeMode.READY:
            with self._persistence_lock:
                self._market_event_buffer.append(self._persistable_market_event(event))
        if event.quality.is_stale or not event.quality.sequence_valid:
            self.data_gap_since_ms.setdefault(event.symbol, event.venue_ts_ms)
        if len(self._events) > 10_000:
            del self._events[:2_500]
        if event.event_type == "TRADE":
            trade = TradeTick(
                venue=event.venue,
                symbol=event.symbol,
                price=Decimal(str(event.data["price"])),
                quantity=Decimal(str(event.data["quantity"])),
                trade_ts_ms=int(event.transaction_ts_ms or event.venue_ts_ms),
                buyer_is_aggressor=bool(event.data["buyer_is_aggressor"]),
            )
            completed_candles = self.candle_builder.add(trade)
            self._buffer_completed_candles(completed_candles)
            feature_engine = self.feature_engines.get(event.symbol)
            if feature_engine is not None:
                feature_engine.ingest_trade(trade)
        elif event.event_type in {"DEPTH_UPDATE", "ORDERBOOK"}:
            self._evaluate_book_event(event)
        if event.event_type == "HEALTH" or not event.quality.sequence_valid:
            self.paused = True
            if "ENTRY_LOCK_DATA_HEALTH" not in self.runtime_health_flags:
                self.runtime_health_flags.append("ENTRY_LOCK_DATA_HEALTH")
        if self._supervisor is not None:
            self.processing_lag_p95_ms = self._supervisor.telemetry.lag_p95_ms

    @staticmethod
    def _persistable_market_event(event: MarketEvent) -> dict[str, object]:
        """리플레이에 필요한 상위 10단계 호가만 저장하고 LIVE 원본은 유지한다."""

        data = dict(event.data)
        payload: dict[str, object] = {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "venue": event.venue.value,
            "symbol": event.symbol,
            "event_type": event.event_type,
            "venue_ts_ms": event.venue_ts_ms,
            "transaction_ts_ms": event.transaction_ts_ms,
            "receive_monotonic_ns": event.receive_monotonic_ns,
            "sequence_start": event.sequence_start,
            "sequence_end": event.sequence_end,
            "previous_sequence_end": event.previous_sequence_end,
            "payload_version": event.payload_version,
            "quality": {
                "is_live": event.quality.is_live,
                "is_stale": event.quality.is_stale,
                "sequence_valid": event.quality.sequence_valid,
                "lag_ms": event.quality.lag_ms,
                "flags": list(event.quality.flags),
            },
            "data": data,
        }
        if event.event_type not in {"DEPTH_UPDATE", "ORDERBOOK"}:
            return payload
        bids = data.get("bids")
        asks = data.get("asks")
        if isinstance(bids, list):
            data["bids"] = bids[:10]
        if isinstance(asks, list):
            data["asks"] = asks[:10]
        return payload

    def _refresh_supervisor_entry_safety(self) -> None:
        """공개시장 지연 임계 초과를 신규 PAPER 진입 잠금에 즉시 연결한다."""

        if self._supervisor is None:
            return
        selection = self._supervisor.selection
        if selection is not None and selection is not self.live_selection:
            self.live_selection = selection
            self.wide_symbol_count = len(selection.wide_symbols)
            self.deep_symbol_count = len(selection.deep_symbols)
            self._record_universe_selection(selection, reason="SAFE_DEEP_ROTATION")
        telemetry = self._supervisor.telemetry
        self.processing_lag_p95_ms = telemetry.lag_p95_ms
        if telemetry.entry_locked:
            self.paused = True
            if "SUPERVISOR_ENTRY_LOCK" not in self.runtime_health_flags:
                self.runtime_health_flags.append("SUPERVISOR_ENTRY_LOCK")
        else:
            self.runtime_health_flags = [
                flag for flag in self.runtime_health_flags if flag != "SUPERVISOR_ENTRY_LOCK"
            ]
        critical_lag = telemetry.critical_lag_active
        if critical_lag:
            self.paused = True
            if "CRITICAL_MARKET_LAG_ENTRY_LOCK" not in self.runtime_health_flags:
                self.runtime_health_flags.append("CRITICAL_MARKET_LAG_ENTRY_LOCK")
            return
        self.runtime_health_flags = [
            flag for flag in self.runtime_health_flags if flag != "CRITICAL_MARKET_LAG_ENTRY_LOCK"
        ]
        blocking_flags = {
            flag
            for flag in self.runtime_health_flags
            if flag.startswith("ENTRY_LOCK_")
            or flag
            in {
                "PERSISTENCE_FAULT_ENTRY_LOCK",
                "RECOVERY_FAIL_CLOSED",
                "STORAGE_PRESSURE_ENTRY_LOCK",
                "SUPERVISOR_ENTRY_LOCK",
            }
        }
        if (
            self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            and self.market_data_state is MarketDataState.LIVE
            and not self._manual_pause_requested
            and not blocking_flags
            and not self.paper_portfolio.main.risk_state.faulted
            and self._storage_entry_allowed
        ):
            self.paused = False

    def _protected_deep_symbols(self) -> tuple[str, ...]:
        protected = [self.selected_symbol]
        pending = self.paper_portfolio.main.pending_entry
        if pending is not None:
            protected.append(pending.plan.symbol)
        position = self.paper_portfolio.main.position
        if position is not None:
            protected.append(position.plan.symbol)
        for account in self.paper_portfolio.shadows.values():
            protected.extend(entry.plan.symbol for entry in account.pending_entries.values())
            protected.extend(item.plan.symbol for item in account.positions.values())
        return tuple(dict.fromkeys(protected))

    def _record_universe_selection(
        self,
        selection: ProviderSelection,
        *,
        reason: str,
    ) -> None:
        if self.ledger is None or self.mode is RuntimeMode.READY:
            return
        timestamp = self.clock.utc_ms()
        self.ledger.record_universe_snapshot(
            {
                "snapshot_id": f"universe-{self.run_id}-{timestamp}-{uuid4().hex[:6]}",
                "run_id": self.run_id,
                "ts_ms": timestamp,
                "venue": selection.venue.value,
                "wide_symbols": list(selection.wide_symbols),
                "deep_symbols": list(selection.deep_symbols),
                "reason": reason,
                "rotation_interval_seconds": 900,
                "minimum_residency_seconds": 1800,
                "maximum_replacements": 4,
                "protected_symbols": list(self._protected_deep_symbols()),
            }
        )

    def _evaluate_book_event(self, event: MarketEvent) -> None:
        bids_value = event.data.get("bids")
        asks_value = event.data.get("asks")
        bids = (
            tuple((Decimal(str(row[0])), Decimal(str(row[1]))) for row in bids_value)
            if isinstance(bids_value, list)
            else ((Decimal(str(event.data["bid"])), Decimal(str(event.data["bid_qty"]))),)
        )
        asks = (
            tuple((Decimal(str(row[0])), Decimal(str(row[1]))) for row in asks_value)
            if isinstance(asks_value, list)
            else ((Decimal(str(event.data["ask"])), Decimal(str(event.data["ask_qty"]))),)
        )
        book = BookSnapshot(
            venue=event.venue,
            symbol=event.symbol,
            ts_ms=event.venue_ts_ms,
            bids=bids,
            asks=asks,
            sequence_valid=event.quality.sequence_valid,
            stale=event.quality.is_stale,
        )
        self.latest_books[event.symbol] = book
        self.paper_portfolio.on_book(book)
        self._persist_execution_state_safely(event.venue_ts_ms)
        if (
            event.symbol in self._recovery_revalidation_symbols
            and event.quality.sequence_valid
            and not event.quality.is_stale
        ):
            self._recovery_revalidation_symbols.discard(event.symbol)
            if not self._recovery_revalidation_symbols:
                self.runtime_health_flags = [
                    flag
                    for flag in self.runtime_health_flags
                    if flag != "ENTRY_LOCK_RECOVERY_REVALIDATION"
                ]
                self.paused = (
                    self.paper_portfolio.main.risk_state.faulted
                    or (self._supervisor is not None and self._supervisor.telemetry.entry_locked)
                    or not self._refresh_storage_safety(force=True)
                )
            self._log(
                "RECOVERY",
                f"{event.symbol} fresh sequence-valid 호가 재검증 완료",
            )
        self.position_visible = self.paper_portfolio.main.position is not None
        portfolio_summary = self.paper_portfolio.main_summary(self._current_main_book())
        self.unrealized_pnl_usdt = float(portfolio_summary["unrealized"])
        engine = self.feature_engines.setdefault(event.symbol, FeatureEngine())
        try:
            engine.ingest_book(
                BookFrame.from_levels(
                    venue=event.venue,
                    symbol=event.symbol,
                    ts_ms=event.venue_ts_ms,
                    bids=bids,
                    asks=asks,
                    sequence_valid=event.quality.sequence_valid,
                    stale=event.quality.is_stale,
                    lag_ms=event.quality.lag_ms or 0.0,
                )
            )
            last_evaluation = self._last_strategy_evaluation_ms.get(event.symbol)
            if (
                last_evaluation is not None
                and event.venue_ts_ms - last_evaluation < self.strategy_evaluation_interval_ms
            ):
                return
            self._last_strategy_evaluation_ms[event.symbol] = event.venue_ts_ms
            snapshot = engine.snapshot()
        except (FeatureInputError, KeyError, IndexError, ValueError) as error:
            self.paused = True
            if "ENTRY_LOCK_FEATURE_INPUT" not in self.runtime_health_flags:
                self.runtime_health_flags.append("ENTRY_LOCK_FEATURE_INPUT")
            self._log("MARKET_DATA", f"{event.symbol} 피처 입력 거부 · {type(error).__name__}")
            return
        regime = self.regime_classifier.classify(snapshot)
        self.latest_features[event.symbol] = snapshot
        self.latest_regimes[event.symbol] = regime
        gap_started = self.data_gap_since_ms.pop(event.symbol, None)
        self.paper_portfolio.evaluate_health(
            snapshot,
            regime,
            now_ms=event.venue_ts_ms,
            recovered_gap_duration_ms=(
                max(0, event.venue_ts_ms - gap_started) if gap_started is not None else 0
            ),
        )
        instrument = (
            self.live_selection.instruments.get(event.symbol)
            if self.live_selection is not None
            else None
        )
        tick_size = instrument.tick_size if instrument is not None else Decimal("0.00000001")
        signals = self.strategy_evaluator.evaluate(
            self.strategy_registry,
            snapshot,
            regime,
            tick_size=tick_size,
        )
        self.strategy_evaluation_count += len(signals)
        self.qualified_signal_count += sum(
            signal.decision.status.value == "QUALIFIED" for signal in signals
        )
        for signal in signals:
            key = (
                signal.symbol,
                signal.decision.strategy_id,
                signal.decision.side.value,
            )
            self.strategy_signals[key] = signal
        plans = self._build_candidate_plans(event, snapshot, regime, book, signals)
        storage_ready = self._refresh_storage_safety()
        self.paper_portfolio.offer(
            plans,
            entries_paused=self.paused or not storage_ready,
        )
        self._persist_execution_state_safely(event.venue_ts_ms)

    def _build_candidate_plans(
        self,
        event: MarketEvent,
        snapshot: FeatureSnapshot,
        regime: Regime,
        book: BookSnapshot,
        signals: tuple[EvaluatedSignal, ...],
    ) -> tuple[CandidatePlan, ...]:
        instrument = (
            self.live_selection.instruments.get(event.symbol)
            if self.live_selection is not None
            else None
        )
        if instrument is None:
            instrument = Instrument(
                venue=event.venue,
                symbol=event.symbol,
                base_asset=event.symbol.removesuffix("USDT"),
                quote_asset="USDT",
                status="TEST",
                contract_type="PAPER",
                tick_size=Decimal("0.00000001"),
                quantity_step=Decimal("0.001"),
                minimum_quantity=Decimal("0.001"),
            )
        plans: list[CandidatePlan] = []
        for signal in signals:
            result = self.candidate_planner.build(
                signal_event_id=event.event_id,
                run_id=self.run_id,
                venue=self.venue,
                decision=signal.decision,
                snapshot=snapshot,
                regime=regime,
                book=book,
                instrument=instrument,
                signal_time_ms=event.venue_ts_ms,
                risk_state=self.paper_portfolio.main.risk_state,
                main_eligible=signal.main_eligible,
                shadow_eligible=signal.shadow_eligible,
                exit_style=self.strategy_registry.descriptor(
                    signal.decision.strategy_id
                ).exit_style,
                strategy_version=STRATEGY_VERSION,
            )
            if result.plan is not None:
                plans.append(result.plan)
                if self.ledger is not None:
                    self.ledger.record_candidate(self._candidate_plan_row(result.plan))
            elif result.rejection_codes != ("STRATEGY_NOT_QUALIFIED",):
                self.plan_rejections.append(
                    {
                        "event_id": event.event_id,
                        "symbol": event.symbol,
                        "strategy_id": signal.decision.strategy_id,
                        "side": signal.decision.side.value,
                        "reason_codes": list(result.rejection_codes),
                    }
                )
        if len(self.plan_rejections) > 2_000:
            del self.plan_rejections[:500]
        return tuple(plans)

    def configure_strategy(
        self,
        strategy_id: str,
        *,
        mode: StrategyMode,
        long_enabled: bool,
        short_enabled: bool,
    ) -> None:
        self.strategy_registry.configure(
            strategy_id,
            mode=mode,
            long_enabled=long_enabled,
            short_enabled=short_enabled,
        )
        self._log(
            "STRATEGY",
            f"{strategy_id} {mode.value} · LONG {long_enabled} · SHORT {short_enabled}",
        )
        if self.ledger is not None and self.mode is not RuntimeMode.READY:
            self.ledger.record_strategy_setting(
                {
                    "run_id": self.run_id,
                    "strategy_id": strategy_id,
                    "ts_ms": self.clock.utc_ms(),
                    "mode": mode.value,
                    "long_enabled": long_enabled,
                    "short_enabled": short_enabled,
                }
            )

    def strategy_decisions(self) -> tuple[CandidateDecision, ...]:
        return tuple(
            signal.decision
            for _, signal in sorted(self.strategy_signals.items(), key=lambda item: item[0])
        )

    def strategy_performance(self, *, include_persisted: bool = True) -> list[dict[str, object]]:
        trades: list[dict[str, object]] = []
        if self.ledger is not None and include_persisted:
            trades.extend(
                trade
                for trade in self.ledger.list_trades()
                if trade.get("sample_type", "LIVE_PUBLIC") == "LIVE_PUBLIC"
            )
            trades.extend(self.ledger.list_shadow_trades())
        elif self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            trades.extend(self._dashboard_live_main_trades())
            trades.extend(self._dashboard_live_shadow_trades())
        else:
            trades.extend(
                self._paper_trade_row(trade) for trade in self.paper_portfolio.main.completed_trades
            )
            for account in self.paper_portfolio.shadows.values():
                trades.extend(self._paper_trade_row(trade) for trade in account.completed_trades)
        return TradeAnalytics().strategy_reports(
            trades,
            strategy_ids=self.strategy_registry.strategy_ids,
        )

    def strategy_symbol_performance(self) -> list[dict[str, object]]:
        """실제 원장 거래만 전략·프로필·종목별로 분리해 보여준다."""

        if self.ledger is None:
            trades = [
                self._paper_trade_row(trade)
                for trade in self.paper_portfolio.main.completed_trades
            ]
            for account in self.paper_portfolio.shadows.values():
                trades.extend(self._paper_trade_row(trade) for trade in account.completed_trades)
        else:
            trades = [
                trade
                for trade in self.ledger.list_trades()
                if trade.get("sample_type", "LIVE_PUBLIC") == "LIVE_PUBLIC"
            ]
            trades.extend(self.ledger.list_shadow_trades())
        return TradeAnalytics().strategy_symbol_reports(trades)

    def focus_positions(self) -> list[dict[str, object]]:
        """체결이 확인된 공동계좌와 전략계좌 포지션을 한 계약으로 정규화한다."""

        stage_names = {
            "ENTRY_FILLED": "진입 체결",
            "PROTECTION_ACTIVE": "익절·손절 보호 중",
            "TP1_FILLED": "1차 익절 완료",
            "RUNNER_ACTIVE": "남은 수량 추세 추적",
            "STOP_TIGHTENED": "손절선 조정",
            "EXIT_PENDING": "종료 체결 대기",
            "DATA_LOCKED": "데이터 안전잠금",
            "RECOVERED_REVALIDATING": "재시작 후 공개호가 확인 중",
        }

        def stage_for(managed: Any) -> str:
            pending_exit = getattr(managed, "pending_exit", None)
            if pending_exit is not None:
                return "EXIT_PENDING"
            protected = managed.protected
            plan = managed.plan
            if protected.current_stop != plan.initial_stop:
                return "STOP_TIGHTENED"
            if managed.remaining_quantity < managed.original_quantity:
                return "RUNNER_ACTIVE"
            return "PROTECTION_ACTIVE"

        def health() -> str:
            locked = self.paused or (
                self._supervisor is not None and self._supervisor.telemetry.entry_locked
            )
            return "신규진입 안전잠금" if locked else "정상"

        rows: list[dict[str, object]] = []
        main = self.paper_portfolio.main_position_snapshot(self._current_main_book())
        if main is not None:
            managed = self.paper_portfolio.main.position
            assert managed is not None
            descriptor = self.strategy_registry.descriptor(str(main["strategy"]))
            stage = stage_for(managed)
            summary = self.paper_portfolio.main_summary(self._current_main_book())
            entry_fee = managed.protected.entry_fill.fee_usdt
            realized_exit_fees = sum(
                (leg.fill.fee_usdt for leg in managed.exit_legs), start=Decimal(0)
            )
            remaining_fraction = (
                managed.remaining_quantity / managed.original_quantity
                if managed.original_quantity > 0
                else Decimal(0)
            )
            effective_leverage = (
                Decimal(str(main["notional"])) / Decimal(str(summary["equity"]))
                if Decimal(str(summary["equity"])) > 0
                else Decimal(0)
            )
            rows.append(
                {
                    **main,
                    "focus_key": f"MAIN:{main['trade_id']}",
                    "account_id": "SHARED_PAPER",
                    "profile": "BASE",
                    "strategy_id": str(main["strategy"]),
                    "strategy_display_name_ko": descriptor.display_name_ko,
                    "exit_style": descriptor.exit_style.value,
                    "opened_ts_ms": managed.protected.opened_ts_ms,
                    "current_mark": str(
                        self.latest_books[str(main["symbol"])].bids[0][0]
                        if str(main["symbol"]) in self.latest_books
                        and str(main["side"]) == "LONG"
                        else self.latest_books[str(main["symbol"])].asks[0][0]
                        if str(main["symbol"]) in self.latest_books
                        else main["actual_entry"]
                    ),
                    "stage": stage,
                    "stage_ko": stage_names[stage],
                    "effective_leverage": str(min(effective_leverage, Decimal(5))),
                    "margin_usdt": str(
                        Decimal(str(main["notional"]))
                        / max(effective_leverage, Decimal(1))
                    ),
                    "margin_used_usdt": str(
                        Decimal(str(main["notional"]))
                        / max(effective_leverage, Decimal(1))
                    ),
                    "original_quantity": str(main["quantity"]),
                    "entry_fee_usdt": str(entry_fee),
                    "realized_exit_fees_usdt": str(realized_exit_fees),
                    "estimated_exit_fee_usdt": str(main["estimated_exit_fee"]),
                    "slippage_usdt": str(main["slippage"]),
                    "gross_pnl_usdt": str(main["gross_pnl"]),
                    "net_pnl_usdt": str(main["net_pnl"]),
                    "return_on_margin_pct": str(
                        Decimal(str(main["net_pnl"]))
                        / max(Decimal(str(main["notional"])), Decimal("0.00000001"))
                        * Decimal(100)
                    ),
                    "account_starting_equity_usdt": "1000",
                    "account_current_equity_usdt": str(summary["equity"]),
                    "remaining_planned_loss_usdt": str(
                        Decimal(str(main["maximum_planned_loss"])) * remaining_fraction
                    ),
                    "maximum_planned_loss_usdt": str(main["maximum_planned_loss"]),
                    "risk_budget_usdt": str(main["risk_budget"]),
                    "notional_usdt": str(main["notional"]),
                    "signal_ts_ms": int(str(main["signal_time"])),
                    "management_reason_ko": str(main["management_reason"]),
                    "data_health": health(),
                    "recovered": False,
                    "auto_focus_eligible": True,
                    "paper_only": True,
                    "real_orders_enabled": False,
                    "auth_required": False,
                }
            )
        league_accounts = {
            str(row["account_id"]): row
            for row in self.paper_portfolio.league_account_rows(self.latest_books)
        }
        for position in self.paper_portfolio.league_position_rows(self.latest_books):
            account_id = str(position["account_id"])
            account = self.paper_portfolio.shadows[account_id]
            managed = account.positions[str(position["symbol"])]
            plan = managed.plan
            descriptor = self.strategy_registry.descriptor(str(position["strategy_id"]))
            account_row = league_accounts[account_id]
            stage = stage_for(managed)
            entry_fee = managed.protected.entry_fill.fee_usdt
            realized_exit_fees = sum(
                (leg.fill.fee_usdt for leg in managed.exit_legs), start=Decimal(0)
            )
            total_fees = Decimal(str(position["fees"]))
            estimated_exit_fee = max(Decimal(0), total_fees - entry_fee - realized_exit_fees)
            remaining_fraction = (
                managed.remaining_quantity / managed.original_quantity
                if managed.original_quantity > 0
                else Decimal(0)
            )
            margin = Decimal(str(position["notional"])) / max(
                Decimal(str(position["effective_leverage"])), Decimal(1)
            )
            rows.append(
                {
                    **position,
                    "focus_key": f"{position['account_id']}:{position['trade_id']}",
                    "strategy": position["strategy_id"],
                    "strategy_display_name_ko": descriptor.display_name_ko,
                    "venue": plan.venue.value,
                    "planned_entry": position["actual_entry"],
                    "take_profit": position["TP1"],
                    "take_profit_1": position["TP1"],
                    "take_profit_2": position["TP2"],
                    "quantity": position["original_quantity"],
                    "risk_budget": str(plan.risk_budget),
                    "risk_budget_usdt": str(plan.risk_budget),
                    "maximum_planned_loss": str(plan.max_planned_loss),
                    "maximum_planned_loss_usdt": str(plan.max_planned_loss),
                    "remaining_planned_loss_usdt": str(
                        plan.max_planned_loss * remaining_fraction
                    ),
                    "margin_usdt": str(margin),
                    "margin_used_usdt": str(margin),
                    "notional_usdt": str(position["notional"]),
                    "signal_ts_ms": int(str(position["signal_time"])),
                    "entry_fee_usdt": str(entry_fee),
                    "realized_exit_fees_usdt": str(realized_exit_fees),
                    "estimated_exit_fee_usdt": str(estimated_exit_fee),
                    "slippage_usdt": str(position["slippage"]),
                    "gross_pnl_usdt": str(position["gross_pnl"]),
                    "net_pnl_usdt": str(position["net_pnl"]),
                    "return_on_margin_pct": str(
                        Decimal(str(position["net_pnl"]))
                        / max(margin, Decimal("0.00000001"))
                        * Decimal(100)
                    ),
                    "account_starting_equity_usdt": str(account_row["starting_equity_usdt"]),
                    "account_current_equity_usdt": str(account_row["current_equity_usdt"]),
                    "management_reason_ko": str(position["management_reason"]),
                    "stage": stage,
                    "stage_ko": stage_names[stage],
                    "data_health": health(),
                    "recovered": False,
                    "auto_focus_eligible": position["profile"] == "BASE",
                    "paper_only": True,
                    "real_orders_enabled": False,
                    "auth_required": False,
                }
            )
        return sorted(
            rows,
            key=lambda row: (
                row["profile"] != "BASE",
                row["account_id"] != "SHARED_PAPER",
                int(str(row.get("opened_ts_ms", row.get("signal_time", 0)))),
                str(row["focus_key"]),
            ),
        )

    def replayable_runs(self) -> list[dict[str, object]]:
        if self.ledger is None:
            return []
        with self._persistence_lock:
            buffered_by_run: dict[str, int] = {}
            for event in self._market_event_buffer:
                run_id = str(event.get("run_id", ""))
                buffered_by_run[run_id] = buffered_by_run.get(run_id, 0) + 1
        rows: list[dict[str, object]] = []
        for run in self.ledger.list_replayable_run_summaries():
            run_id = str(run["run_id"])
            buffered_count = buffered_by_run.get(run_id, 0)
            persisted_count = (
                int(str(run["market_event_count"]))
                if run["market_event_count"] is not None
                else None
            )
            has_events = bool(run["has_market_events"]) or buffered_count > 0
            if not has_events:
                continue
            rows.append(
                {
                    "run_id": str(run["run_id"]),
                    "mode": str(run["mode"]),
                    "venue": str(run["venue"]),
                    "started_ts_ms": int(str(run["started_ts_ms"])),
                    "finalized_ts_ms": int(str(run["finalized_ts_ms"]))
                    if run["finalized_ts_ms"] is not None
                    else None,
                    "market_event_count": (
                        persisted_count + buffered_count if persisted_count is not None else None
                    ),
                    "events_saved": has_events,
                    "trade_count": int(str(run["trade_count"])),
                    "shadow_trade_count": int(str(run["shadow_trade_count"])),
                }
            )
        return rows

    def flush_storage(self) -> None:
        """현재 메모리 배치와 PAPER 실행 결과를 불변 원장에 반영한다."""

        self._persist_execution_state_safely(self.clock.utc_ms())
        self._flush_persistence()

    def replay_stored_run(
        self,
        source_run_id: str,
        *,
        symbol: str | None = None,
    ) -> dict[str, object]:
        if self.ledger is None:
            raise ValueError("영속 원장이 없어 리플레이할 수 없습니다.")
        self._flush_persistence()
        from backend.app.replay.market import StoredMarketReplay

        result = StoredMarketReplay().run(
            self.ledger,
            source_run_id=source_run_id,
            created_ts_ms=self.clock.utc_ms(),
            symbol=symbol.strip().upper() if symbol else None,
        )
        return result.as_dict()

    def replay_timeline(
        self,
        source_run_id: str,
        *,
        symbol: str | None = None,
        limit: int = 2_000,
    ) -> dict[str, object]:
        """저장된 공개시장 이벤트와 실제 집계 캔들을 UI 재생 프레임으로 제공한다."""

        if self.ledger is None:
            raise ValueError("영속 원장이 없어 리플레이할 수 없습니다.")
        if self.ledger.get_run(source_run_id) is None:
            raise ValueError(f"저장 Run을 찾을 수 없습니다: {source_run_id}")
        available_symbols = self.ledger.market_event_symbols(source_run_id)
        selected_symbol = symbol.strip().upper() if symbol else None
        if selected_symbol is None and available_symbols:
            selected_symbol = str(available_symbols[0]["symbol"])
        if selected_symbol is not None and selected_symbol not in {
            str(row["symbol"]) for row in available_symbols
        }:
            raise ValueError(f"저장 Run에 없는 종목입니다: {selected_symbol}")
        events = self.ledger.list_market_events(
            source_run_id,
            symbol=selected_symbol,
            limit=limit,
        )
        stored_candles = (
            self.ledger.list_candles(
                source_run_id,
                symbol=selected_symbol,
                interval_seconds=1,
            )
            if selected_symbol is not None
            else []
        )
        candles = [
            {
                "time": int(str(candle["open_ts_ms"])) // 1_000,
                "open_ts_ms": int(str(candle["open_ts_ms"])),
                "open": float(str(candle["open"])),
                "high": float(str(candle["high"])),
                "low": float(str(candle["low"])),
                "close": float(str(candle["close"])),
                "volume": float(str(candle["volume"])),
                "trade_count": int(str(candle["trade_count"])),
            }
            for candle in stored_candles
        ]
        total_events = next(
            (
                int(str(row["event_count"])) if row["event_count"] is not None else None
                for row in available_symbols
                if row["symbol"] == selected_symbol
            ),
            0,
        )
        return {
            "run_id": source_run_id,
            "symbol": selected_symbol,
            "total_events": total_events,
            "truncated": total_events is None or total_events > len(events),
            "available_symbols": available_symbols,
            "events": events,
            "candles": candles,
        }

    def replay_focus_session(
        self,
        source_run_id: str,
        *,
        trade_id: str,
        profile: str = "BASE",
    ) -> dict[str, object]:
        """저장된 실제 PAPER 거래의 포지션 집중 리플레이를 생성한다."""

        if self.ledger is None:
            raise ValueError("영속 원장이 없어 리플레이할 수 없습니다.")
        self._flush_persistence()
        from backend.app.replay.focus import ReplayFocusSessionBuilder

        return ReplayFocusSessionBuilder().build(
            self.ledger,
            run_id=source_run_id,
            trade_id=trade_id,
            profile=profile,
            created_ts_ms=self.clock.utc_ms(),
        )

    def candles(
        self,
        symbol: str | None = None,
        interval_seconds: int | None = None,
    ) -> tuple[Candle, ...]:
        return self.candle_builder.series(
            symbol or self.selected_symbol,
            interval_seconds or self.selected_interval_seconds,
        )

    def set_chart_selection(self, symbol: str, interval_seconds: int) -> None:
        normalized = symbol.strip().upper()
        if interval_seconds not in CandleBuilder.ALLOWED_INTERVALS:
            raise ValueError("지원하지 않는 캔들 시간구간입니다.")
        self.selected_symbol = normalized
        self.selected_interval_seconds = interval_seconds

    async def shutdown_supervisor(self) -> None:
        supervisor = self._supervisor
        self._supervisor = None
        if supervisor is not None:
            await supervisor.stop()

    async def shutdown(self) -> None:
        await self.shutdown_supervisor()
        self._flush_persistence()

    def _live_scanner_rows(self) -> tuple[dict[str, object], ...]:
        """정밀 분석 종목의 실제 전략 판단과 비용을 확률 없이 UI 행으로 만든다."""

        if self.mode is not RuntimeMode.LIVE_SHADOW_PAPER:
            return ()
        deep_symbols = self.live_selection.deep_symbols if self.live_selection is not None else ()
        unsorted: list[dict[str, object]] = []
        for symbol in deep_symbols:
            feature = self.latest_features.get(symbol)
            regime = self.latest_regimes.get(symbol)
            signals = [
                signal for signal in self.strategy_signals.values() if signal.symbol == symbol
            ]
            if feature is None or regime is None or not signals:
                unsorted.append(
                    {
                        "rank": 0,
                        "symbol": symbol,
                        "depth": "DEEP",
                        "regime": "WARMUP",
                        "strategy": "분석 준비",
                        "side": "NONE",
                        "score": None,
                        "net_rr": None,
                        "expected_cost_bps": 0.0,
                        "spread_bps": round(feature.spread_bps, 4) if feature else 0.0,
                        "data_health": "HEALTHY" if feature and feature.data_healthy else "WARMUP",
                        "status": "CALIBRATING",
                        "reason": "실제 정밀 호가·체결 이력을 축적하는 중",
                        "reason_codes": ["CALIBRATING"],
                        "calibration": "CALIBRATING",
                    }
                )
                continue
            selected = min(
                signals,
                key=lambda signal: (
                    signal.decision.status.value != "QUALIFIED",
                    -float(signal.decision.net_reward_risk or Decimal(0)),
                    float(signal.decision.expected_cost_bps),
                    signal.decision.strategy_id,
                    signal.decision.side.value,
                ),
            )
            decision = selected.decision
            reason_codes = list(decision.reason_codes or decision.rejection_codes)
            unsorted.append(
                {
                    "rank": 0,
                    "symbol": symbol,
                    "depth": "DEEP",
                    "regime": regime.value,
                    "strategy": self.strategy_registry.descriptor(decision.strategy_id).short_name,
                    "side": decision.side.value,
                    "score": None,
                    "net_rr": float(decision.net_reward_risk)
                    if decision.net_reward_risk is not None
                    else None,
                    "expected_cost_bps": float(decision.expected_cost_bps),
                    "spread_bps": round(feature.spread_bps, 4),
                    "data_health": "HEALTHY" if feature.data_healthy else "STALE",
                    "status": decision.status.value,
                    "reason": " · ".join(reason_codes)
                    if reason_codes
                    else "구조·체결흐름 조건 확인 중",
                    "reason_codes": reason_codes,
                    "calibration": decision.calibration_status,
                }
            )
        ordered = sorted(
            unsorted,
            key=lambda row: (
                row["status"] != "QUALIFIED",
                -(float(str(row["net_rr"])) if row["net_rr"] is not None else -1.0),
                float(str(row["expected_cost_bps"])),
                str(row["symbol"]),
            ),
        )
        return tuple({**row, "rank": rank} for rank, row in enumerate(ordered, 1))

    def dashboard(self) -> dict[str, object]:
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            persisted_trades = tuple(
                self._paper_trade_row(trade) for trade in self.paper_portfolio.main.completed_trades
            )
        else:
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
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            history_trades = self._dashboard_live_main_trades()
        else:
            history_trades = (
                tuple(
                    trade
                    for trade in self.ledger.list_trades()
                    if trade.get("sample_type", "LIVE_PUBLIC") == sample_type
                )
                if self.ledger is not None and sample_type is not None
                else ()
            )
        candle_rows = tuple(
            {
                "time": candle.open_ts_ms // 1_000,
                "open_ts_ms": candle.open_ts_ms,
                "open": float(candle.open),
                "high": float(candle.high),
                "low": float(candle.low),
                "close": float(candle.close),
                "volume": float(candle.volume),
                "trade_count": candle.trade_count,
            }
            for candle in self.candles()
        )
        diagnostics: dict[str, object] = (
            self._supervisor.telemetry.as_dict()
            if self._supervisor is not None
            else {
                "connection_state": self.market_data_state.value,
                "event_count": len(self._events),
                "reconnects": 0,
                "sequence_gaps": 0,
                "resyncs": 0,
                "dropped_events": 0,
                "queue_depth": 0,
                "queue_capacity": 0,
                "entry_locked": self.paused,
            }
        )
        diagnostics.update(self._operational_diagnostics())
        current_position = self.paper_portfolio.main_position_snapshot(self._current_main_book())
        strategy_rows: list[dict[str, object]] = []
        performance_by_key = {
            (str(report["strategy_id"]), str(report["profile"])): report
            for report in self.strategy_performance(include_persisted=False)
        }
        for row in self.strategy_registry.rows():
            strategy_id = str(row["strategy_id"])
            signals = [
                signal
                for signal in self.strategy_signals.values()
                if signal.decision.strategy_id == strategy_id
            ]
            latest = max(signals, key=lambda item: item.decision.expected_cost_bps, default=None)
            strategy_rows.append(
                {
                    **row,
                    "evaluated_paths": len(signals),
                    "qualified_paths": sum(
                        signal.decision.status.value == "QUALIFIED" for signal in signals
                    ),
                    "latest_status": latest.decision.status.value if latest else "WAITING_DATA",
                    "latest_reasons": list(
                        latest.decision.reason_codes or latest.decision.rejection_codes
                    )
                    if latest
                    else [],
                    "performance": {
                        profile: performance_by_key[(strategy_id, profile)]
                        for profile in ("BASE", "STRESS")
                    },
                }
            )
        snapshot = build_dashboard_snapshot(
            self.status(),
            self.events,
            paused=self.paused,
            position_visible=self.position_visible,
            control_logs=tuple(self.control_logs),
            archived_run_ids=tuple(self.archived_run_ids),
            persisted_trades=persisted_trades,
            history_trades=history_trades,
            candle_rows=candle_rows,
            chart_symbol=self.selected_symbol,
            chart_interval_seconds=self.selected_interval_seconds,
            runtime_diagnostics=diagnostics,
            scanner_rows=self._live_scanner_rows()
            if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            else None,
            strategies=tuple(strategy_rows),
            shadow_accounts=tuple(self.paper_portfolio.shadow_rows()),
            league_accounts=tuple(
                self.paper_portfolio.league_account_rows(self.latest_books)
            ),
            league_positions=tuple(
                self.paper_portfolio.league_position_rows(self.latest_books)
            ),
            risk_contract=self._risk_dashboard_contract(),
            current_position=current_position,
            execution_audit=tuple(self.paper_portfolio.audit_events[-100:]),
            storage_label="SQLite transactional ledger"
            if self.ledger is not None
            else "fixture memory",
            api_host=(
                f"{os.environ.get('ROBOM_HOST', '127.0.0.1')}:"
                f"{os.environ.get('ROBOM_PORT', '8765')}"
            ),
        )
        snapshot["focus_positions"] = self.focus_positions()
        return snapshot

    def set_paused(self, paused: bool) -> None:
        if self.mode is RuntimeMode.READY:
            self.paused = True
            self._log("RISK", "실시간 PAPER 시작 전에는 진입할 수 없음")
            return
        self._manual_pause_requested = paused
        if (
            not paused
            and self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            and (
                self.market_data_state is not MarketDataState.LIVE
                or "CRITICAL_MARKET_LAG_ENTRY_LOCK" in self.runtime_health_flags
                or (self._supervisor is not None and self._supervisor.telemetry.entry_locked)
                or self.paper_portfolio.main.risk_state.faulted
                or not self._refresh_storage_safety(force=True)
            )
        ):
            self.paused = True
            self._log("RISK", "검증된 LIVE 데이터가 없어 PAPER 진입 재개 차단")
            return
        self.paused = paused
        self._log("RISK", "페이퍼 신규 진입 일시정지" if paused else "페이퍼 신규 진입 재개")

    def emergency_paper_close(self) -> None:
        if self.mode is RuntimeMode.DEMO_FIXTURE:
            self.position_visible = False
            self._log("EXIT", "격리된 DEMO PAPER 포지션 표시 종료")
            return
        requested = self.paper_portfolio.request_main_exit(
            now_ms=self.clock.utc_ms(),
            reason=ExitReason.MANUAL_PAPER_EXIT,
        )
        self._log(
            "EXIT",
            "현재 PAPER 포지션 비상종료 지연 요청"
            if requested
            else "비상종료할 실제 PAPER 포지션 없음",
        )

    async def start_live_run(
        self,
        probe: LiveBootstrapProbe | None = None,
        progress: ProgressCallback | None = None,
    ) -> bool:
        if progress is not None:
            await progress("PREPARING", "새 PAPER Run을 준비하고 있습니다")
        await self.shutdown_supervisor()
        self._archive_current_run("USER_START_LIVE")
        self.mode = RuntimeMode.LIVE_SHADOW_PAPER
        self._manual_pause_requested = False
        self.run_id = f"run-{uuid4().hex[:12]}"
        self.venue = Venue.BINANCE_USDM
        self.market_data_state = MarketDataState.DISCONNECTED
        self._events.clear()
        self._reset_research_state()
        self.paused = True
        self.position_visible = False
        self.unrealized_pnl_usdt = 0.0
        self.wide_symbol_count = 0
        self.deep_symbol_count = 0
        self.processing_lag_p95_ms = None
        self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        self._start_ledger_run()
        self._log("RUN", "Fresh LIVE PAPER Run 생성 · 자산과 손익·비용·거래 0")
        if probe is not None:
            return await self.boot_live_public(probe)
        return await self.start_persistent_live(progress=progress)

    def live_start_block(self) -> tuple[str, str] | None:
        """공개시장 연결 전에 해제할 수 없는 안전잠금만 제어 API에 설명한다."""

        blocked_flags = {
            "RECOVERY_FAIL_CLOSED": (
                "RECOVERY_SAFETY_LOCK",
                "복구 안전검사가 완료되지 않아 자동 관찰을 시작할 수 없습니다.",
            ),
            "PERSISTENCE_FAULT_ENTRY_LOCK": (
                "PERSISTENCE_SAFETY_LOCK",
                "저장 안전오류가 있어 자동 관찰을 시작할 수 없습니다.",
            ),
            "STORAGE_PRESSURE_ENTRY_LOCK": (
                "STORAGE_SAFETY_LOCK",
                "저장공간 안전잠금이 있어 자동 관찰을 시작할 수 없습니다.",
            ),
        }
        for flag in self.runtime_health_flags:
            if flag in blocked_flags:
                return blocked_flags[flag]
        if self.paper_portfolio.main.risk_state.faulted:
            return (
                "PAPER_RECOVERY_SAFETY_LOCK",
                "PAPER 계좌 복구 안전잠금이 있어 자동 관찰을 시작할 수 없습니다.",
            )
        return None

    def _risk_dashboard_contract(self) -> dict[str, object]:
        """실제 실행 상수에서 Shared Capital과 Strategy League 위험표를 만든다."""

        shared = self.paper_portfolio.risk_manager.limits
        league = self.paper_portfolio.league_risk_manager.limits
        cost = self.paper_portfolio.cost_model
        starting = self.paper_portfolio.main.risk_state.starting_equity

        def percentage(value: Decimal) -> str:
            return f"{value * 100:.2f}%"

        def bps(value: Decimal) -> str:
            return f"{value.normalize()}bp"

        return {
            "paper_only": True,
            "active_locks": ["PAPER_ONLY", *self.runtime_health_flags],
            "immutable_run": True,
            "shared_capital": {
                "starting_equity_usdt": str(starting),
                "risk_per_position": percentage(shared.risk_per_trade_fraction),
                "max_positions": shared.max_open_positions,
                "daily_loss_limit": (
                    f"{(starting * shared.daily_loss_limit_fraction).normalize()} USDT"
                ),
                "weekly_loss_limit": (
                    f"{(starting * shared.weekly_loss_limit_fraction).normalize()} USDT"
                ),
                "drawdown_lock": percentage(shared.maximum_drawdown_fraction),
            },
            "strategy_league": {
                "account_count": len(self.paper_portfolio.shadows),
                "starting_equity_per_account_usdt": str(starting),
                "risk_per_position": percentage(league.risk_per_trade_fraction),
                "max_positions_per_account": league.max_open_positions,
                "maximum_total_open_risk": percentage(
                    league.maximum_total_open_risk_fraction
                ),
                "maximum_effective_leverage": (
                    f"{league.maximum_gross_notional_fraction:.2f}x"
                ),
                "maximum_depth_fraction": percentage(
                    league.maximum_order_fraction_of_executable_depth
                ),
                "daily_loss_limit": percentage(league.daily_loss_limit_fraction),
                "weekly_loss_limit": percentage(league.weekly_loss_limit_fraction),
                "drawdown_lock": percentage(league.maximum_drawdown_fraction),
                "base_entry_fee": bps(cost.fee_bps(entry=True, profile=CostProfile.BASE)),
                "base_exit_fee": bps(cost.fee_bps(entry=False, profile=CostProfile.BASE)),
                "stress_entry_fee": bps(
                    cost.fee_bps(entry=True, profile=CostProfile.STRESS)
                ),
                "stress_exit_fee": bps(
                    cost.fee_bps(entry=False, profile=CostProfile.STRESS)
                ),
            },
        }

    def start_demo_run(self) -> str:
        self._archive_current_run("USER_START_DEMO")
        self.mode = RuntimeMode.DEMO_FIXTURE
        self._manual_pause_requested = False
        self.run_id = f"demo-{uuid4().hex[:12]}"
        self.venue = Venue.FIXTURE
        self.market_data_state = MarketDataState.FIXTURE
        self.live_selection = None
        self.wide_symbol_count = 0
        self.deep_symbol_count = 0
        self.processing_lag_p95_ms = None
        self._events.clear()
        self._reset_research_state()
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
        self._flush_persistence()
        self.archived_run_ids.append(self.run_id)
        if self.ledger is not None:
            trades = self.ledger.list_trades(previous_run_id)
            self.ledger.finalize_run(
                previous_run_id,
                finalized_ts_ms=self.clock.utc_ms(),
                summary={"trade_count": len(trades), "preserved": True},
            )
        self.run_id = f"run-{uuid4().hex[:12]}"
        self._manual_pause_requested = False
        self._events.clear()
        self._reset_research_state()
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
        self._flush_persistence()
        current = self.ledger.get_run(self.run_id)
        if current is None or current["finalized_ts_ms"] is not None:
            return
        self.archived_run_ids.append(self.run_id)
        self.ledger.finalize_run(
            self.run_id,
            finalized_ts_ms=self.clock.utc_ms(),
            summary={"reason": reason, "preserved": True},
        )

    def _reset_research_state(self) -> None:
        self.candle_builder = CandleBuilder()
        self.feature_engines.clear()
        self.latest_features.clear()
        self.latest_regimes.clear()
        self.strategy_signals.clear()
        self.strategy_evaluator = StrategySignalEvaluator()
        self.shadow_ledger = ShadowLedger(self.strategy_registry.strategy_ids)
        self.paper_portfolio = PaperPortfolioEngine(
            run_id=self.run_id,
            strategy_ids=self.strategy_registry.strategy_ids,
            shadow_ledger=self.shadow_ledger,
            venue=self.venue,
        )
        self.latest_books.clear()
        self.plan_rejections.clear()
        self.data_gap_since_ms.clear()
        self._last_strategy_evaluation_ms.clear()
        self._recovery_revalidation_symbols.clear()
        with self._persistence_lock:
            self._market_event_buffer.clear()
            self._candle_buffer.clear()
        self._persisted_main_order_ids.clear()
        self._persisted_main_trade_ids.clear()
        self._persisted_shadow_trade_ids.clear()
        self._persisted_audit_count = 0
        self.strategy_evaluation_count = 0
        self.qualified_signal_count = 0

    def _current_main_book(self) -> BookSnapshot | None:
        position = self.paper_portfolio.main.position
        if position is None:
            return self.latest_books.get(self.selected_symbol)
        return self.latest_books.get(position.plan.symbol)

    @staticmethod
    def _paper_trade_row(trade: PaperTrade) -> dict[str, object]:
        return {
            "trade_id": trade.trade_id,
            "run_id": trade.run_id,
            "venue": trade.venue.value,
            "symbol": trade.symbol,
            "strategy_id": trade.strategy_id,
            "side": trade.side.value,
            "entry_price": str(trade.entry_price),
            "exit_price": str(trade.exit_price),
            "initial_stop": str(trade.initial_stop),
            "take_profit": str(trade.take_profit),
            "quantity": str(trade.quantity),
            "exit_reason": trade.exit_reason.value,
            "gross_pnl_usdt": str(trade.gross_pnl_usdt),
            "fees_usdt": str(trade.fees_usdt),
            "slippage_usdt": str(trade.slippage_usdt),
            "net_pnl_usdt": str(trade.net_pnl_usdt),
            "entry_ts_ms": trade.opened_ts_ms,
            "exit_ts_ms": trade.closed_ts_ms,
            "holding_ms": trade.holding_ms,
            "regime": trade.regime,
            "mae_r": str(trade.mae_r),
            "mfe_r": str(trade.mfe_r),
            "flags": list(trade.flags),
            "profile": trade.profile.value,
            "sample_type": "LIVE_PUBLIC",
        }

    @staticmethod
    def _candidate_plan_row(plan: CandidatePlan) -> dict[str, object]:
        return {
            "candidate_id": plan.candidate_id,
            "signal_event_id": plan.signal_event_id,
            "run_id": plan.run_id,
            "venue": plan.venue.value,
            "symbol": plan.symbol,
            "strategy_id": plan.strategy_id,
            "strategy_version": plan.strategy_version,
            "exit_style": plan.exit_style.value,
            "direction": plan.direction.value,
            "signal_time_ms": plan.signal_time_ms,
            "expires_at_ms": plan.expires_at_ms,
            "regime": plan.regime.value,
            "planned_entry": str(plan.planned_entry),
            "worst_allowed_entry": str(plan.worst_allowed_entry),
            "initial_stop": str(plan.initial_stop),
            "noise_buffer": str(plan.noise_buffer),
            "take_profit_targets": [
                {
                    "label": target.label,
                    "price": str(target.price),
                    "quantity_fraction": str(target.quantity_fraction),
                }
                for target in plan.take_profit_targets
            ],
            "position_size": str(plan.position_size),
            "quantity_step": str(plan.quantity_step),
            "minimum_quantity": str(plan.minimum_quantity),
            "executable_depth_quantity": str(plan.executable_depth_quantity),
            "risk_budget": str(plan.risk_budget),
            "max_planned_loss": str(plan.max_planned_loss),
            "gross_reward_usdt": str(plan.gross_reward_usdt),
            "expected_fees_usdt": str(plan.expected_fees_usdt),
            "expected_slippage_usdt": str(plan.expected_slippage_usdt),
            "net_reward_usdt": str(plan.net_reward_usdt),
            "net_risk_usdt": str(plan.net_risk_usdt),
            "net_reward_risk": str(plan.net_reward_risk),
            "data_quality": str(plan.data_quality),
            "signal_quality": str(plan.signal_quality),
            "liquidity_quality": str(plan.liquidity_quality),
            "cost_burden": str(plan.cost_burden),
            "reason_codes": list(plan.reason_codes),
            "plain_korean_explanation": list(plan.plain_korean_explanation),
            "management_policy": list(plan.management_policy),
            "main_eligible": plan.main_eligible,
            "shadow_eligible": plan.shadow_eligible,
            "status": "ARMED",
        }

    def _buffer_completed_candles(self, candles: list[Candle]) -> None:
        if self.ledger is None:
            return
        with self._persistence_lock:
            self._candle_buffer.extend(
                {
                    "run_id": self.run_id,
                    "symbol": candle.symbol,
                    "interval_seconds": candle.interval_seconds,
                    "open_ts_ms": candle.open_ts_ms,
                    "open": str(candle.open),
                    "high": str(candle.high),
                    "low": str(candle.low),
                    "close": str(candle.close),
                    "volume": str(candle.volume),
                    "trade_count": candle.trade_count,
                }
                for candle in candles
            )

    def _persist_execution_state_safely(self, ts_ms: int) -> bool:
        try:
            self._persist_execution_state(ts_ms)
        except Exception as error:
            self._handle_persistence_fault(error)
            return False
        return True

    def _persist_execution_state(self, ts_ms: int) -> None:
        if self.ledger is None or self.mode is RuntimeMode.READY:
            return
        main_orders = (
            *self.paper_portfolio.main.entry_orders,
            *self.paper_portfolio.main.exit_orders,
        )
        for order in main_orders:
            if order.order_id in self._persisted_main_order_ids:
                continue
            row = self._paper_order_row(order, ts_ms)
            self.ledger.record_order(row)
            if order.fill is not None:
                self.ledger.record_fill(
                    {
                        "fill_id": f"fill-{order.order_id}",
                        "run_id": order.run_id,
                        "order_id": order.order_id,
                        "side": order.side,
                        "planned_price": str(
                            order.price_cap or order.trigger_price or order.fill.average_price
                        ),
                        "price": str(order.fill.average_price),
                        "quantity": str(order.fill.quantity),
                        "fee_usdt": str(order.fill.fee_usdt),
                        "slippage_usdt": str(order.fill.slippage_usdt),
                        "ts_ms": order.fill.book_ts_ms,
                    }
                )
            self._persisted_main_order_ids.add(order.order_id)
        for trade in self.paper_portfolio.main.completed_trades:
            if trade.trade_id in self._persisted_main_trade_ids:
                continue
            row = self._paper_trade_row(trade)
            run = self.ledger.get_run(self.run_id)
            if run is None:
                raise RuntimeError("완료 PAPER 거래가 참조할 Run이 없습니다.")
            row["config_hash"] = str(run["config_hash"])
            row["strategy_version"] = STRATEGY_VERSION
            self.ledger.record_trade(row)
            self._persisted_main_trade_ids.add(trade.trade_id)
        for account in self.paper_portfolio.shadows.values():
            for trade in account.completed_trades:
                if trade.trade_id in self._persisted_shadow_trade_ids:
                    continue
                row = self._paper_trade_row(trade)
                row["shadow_trade_id"] = trade.trade_id
                row["closed_ts_ms"] = trade.closed_ts_ms
                self.ledger.record_shadow_trade(row)
                self._persisted_shadow_trade_ids.add(trade.trade_id)
        new_audits = self.paper_portfolio.audit_events[self._persisted_audit_count :]
        if new_audits:
            self.ledger.record_execution_audits(new_audits)
            self._persisted_audit_count = len(self.paper_portfolio.audit_events)
            for account_row in self.paper_portfolio.shadow_rows():
                self.ledger.record_strategy_account_snapshot(
                    {
                        "run_id": self.run_id,
                        "strategy_id": account_row["strategy_id"],
                        "profile": account_row["profile"],
                        "ts_ms": ts_ms,
                        **account_row,
                    }
                )
            position = self.paper_portfolio.main_position_snapshot(self._current_main_book())
            self.ledger.save_snapshot(
                self.run_id,
                lifecycle_state=self.paper_portfolio.lifecycle_state(),
                ts_ms=ts_ms,
                payload={
                    "snapshot_ts_ms": ts_ms,
                    "open_position": position,
                    "portfolio": self.paper_portfolio.recovery_state(
                        registry_settings=self.strategy_registry.rows(),
                        snapshot_ts_ms=ts_ms,
                    ),
                },
            )

    @staticmethod
    def _paper_order_row(order: PaperOrder, fallback_ts_ms: int) -> dict[str, object]:
        fill = order.fill
        created_ts = order.created_ts_ms or fallback_ts_ms
        return {
            "order_id": order.order_id,
            "run_id": order.run_id,
            "trade_id": order.trade_id,
            "venue": order.venue.value,
            "symbol": order.symbol,
            "side": order.side,
            "intent": order.intent.value,
            "status": order.status.value,
            "requested_qty": str(order.requested_quantity),
            "filled_qty": str(order.filled_quantity),
            "price_cap": str(order.price_cap) if order.price_cap is not None else None,
            "trigger_price": str(order.trigger_price) if order.trigger_price is not None else None,
            "average_fill_price": str(fill.average_price) if fill is not None else None,
            "created_ts_ms": created_ts,
            "arrival_ts_ms": order.arrival_ts_ms,
            "finalized_ts_ms": fill.book_ts_ms if fill is not None else created_ts,
            "fee_usdt": str(fill.fee_usdt) if fill is not None else "0",
            "slippage_usdt": str(fill.slippage_usdt) if fill is not None else "0",
            "reason_codes": list(order.reason_codes),
        }

    def _flush_persistence(self, market_limit: int | None = None) -> None:
        with self._persistence_lock:
            if market_limit is None:
                market_batch = self._market_event_buffer
                self._market_event_buffer = []
            else:
                market_batch = self._market_event_buffer[:market_limit]
                del self._market_event_buffer[: len(market_batch)]
            candle_batch = self._candle_buffer
            self._candle_buffer = []
        if self.ledger is None:
            return
        try:
            if market_batch:
                if self.market_event_archive is None:
                    self.ledger.record_market_events(market_batch)
                else:
                    grouped: dict[tuple[str, str, int], list[dict[str, object]]] = {}
                    for event in market_batch:
                        key = (
                            str(event["run_id"]),
                            str(event["venue"]),
                            int(str(event["venue_ts_ms"])) // 3_600_000,
                        )
                        grouped.setdefault(key, []).append(event)
                    for rows in grouped.values():
                        archive = self.market_event_archive.write_market_event_batch(rows)
                        self.ledger.record_market_event_archive(archive, rows)
            if candle_batch:
                self.ledger.record_candles(candle_batch)
        except Exception as error:
            with self._persistence_lock:
                market_rows = [*market_batch, *self._market_event_buffer]
                candle_rows = [*candle_batch, *self._candle_buffer]
                if len(market_rows) > 10_000:
                    self._persistence_buffer_dropped += len(market_rows) - 10_000
                    market_rows = market_rows[-10_000:]
                if len(candle_rows) > 5_000:
                    self._persistence_buffer_dropped += len(candle_rows) - 5_000
                    candle_rows = candle_rows[-5_000:]
                self._market_event_buffer = market_rows
                self._candle_buffer = candle_rows
            self._handle_persistence_fault(error)

    async def run_persistence_worker(self, stop: asyncio.Event) -> None:
        """시장 직렬화·fsync를 시장데이터 이벤트 루프 밖에서 실행한다."""

        async def flush(limit: int | None) -> None:
            started = asyncio.get_running_loop().time()
            await asyncio.to_thread(self._flush_persistence, limit)
            elapsed_ms = (asyncio.get_running_loop().time() - started) * 1_000
            self._persistence_flush_count += 1
            self._persistence_flush_last_ms = elapsed_ms
            self._persistence_flush_max_ms = max(
                self._persistence_flush_max_ms,
                elapsed_ms,
            )

        while not stop.is_set():
            with self._persistence_lock:
                should_flush = (
                    len(self._market_event_buffer) >= _MARKET_PERSISTENCE_FLUSH_THRESHOLD
                    and self._persistence_fault_count == 0
                )
            if should_flush:
                await flush(_MARKET_PERSISTENCE_BATCH_SIZE)
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except TimeoutError:
                continue
        with self._persistence_lock:
            has_pending = bool(self._market_event_buffer or self._candle_buffer)
        if has_pending and self._persistence_fault_count == 0:
            await flush(None)

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
        for row in self.strategy_registry.rows():
            self.ledger.record_strategy_setting(
                {
                    "run_id": self.run_id,
                    "strategy_id": row["strategy_id"],
                    "ts_ms": self.clock.utc_ms(),
                    "mode": row["mode"],
                    "long_enabled": row["long_enabled"],
                    "short_enabled": row["short_enabled"],
                }
            )
        timestamp = self.clock.utc_ms()
        self.ledger.save_snapshot(
            self.run_id,
            lifecycle_state="SCANNING",
            ts_ms=timestamp,
            payload={
                "snapshot_ts_ms": timestamp,
                "open_position": None,
                "portfolio": self.paper_portfolio.recovery_state(
                    registry_settings=self.strategy_registry.rows(),
                    snapshot_ts_ms=timestamp,
                ),
            },
        )
        self._refresh_dashboard_trade_cache()

    def _refresh_dashboard_trade_cache(self) -> None:
        if self.ledger is None:
            self._historical_live_trades = ()
            self._historical_shadow_trades = ()
            return
        self._historical_live_trades = tuple(
            trade
            for trade in self.ledger.list_trades()
            if trade.get("sample_type", "LIVE_PUBLIC") == "LIVE_PUBLIC"
        )
        self._historical_shadow_trades = tuple(self.ledger.list_shadow_trades())

    def _dashboard_live_main_trades(self) -> tuple[dict[str, object], ...]:
        rows = {str(trade["trade_id"]): trade for trade in self._historical_live_trades}
        rows.update(
            {
                trade.trade_id: self._paper_trade_row(trade)
                for trade in self.paper_portfolio.main.completed_trades
            }
        )
        return tuple(rows.values())

    def _dashboard_live_shadow_trades(self) -> tuple[dict[str, object], ...]:
        rows = {str(trade["trade_id"]): trade for trade in self._historical_shadow_trades}
        for account in self.paper_portfolio.shadows.values():
            rows.update(
                {trade.trade_id: self._paper_trade_row(trade) for trade in account.completed_trades}
            )
        return tuple(rows.values())

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
            payload={
                "snapshot_ts_ms": timestamp,
                "open_position": None,
                "last_exit_reason": "TAKE_PROFIT",
                "portfolio": self.paper_portfolio.recovery_state(
                    registry_settings=self.strategy_registry.rows(),
                    snapshot_ts_ms=timestamp,
                ),
            },
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
        self._flush_persistence()
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
        self._reset_research_state()
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
