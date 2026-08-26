"""FastAPI 앱을 구성하고 빌드된 로컬 대시보드를 제공한다."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from anyio import to_process
from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.clocks import SystemClock
from backend.app.control import (
    ControlAction,
    ControlOperationConflict,
    ControlOperationFailure,
    ControlOperationManager,
    ControlRevisionConflict,
)
from backend.app.control.operations import ControlRunner, ProgressCallback
from backend.app.domain.models import MarketDataState, RuntimeMode, Venue
from backend.app.market_explorer import MarketExplorerService
from backend.app.replay.operations import (
    ReplayOperationConflict,
    ReplayOperationManager,
)
from backend.app.replay.process import (
    replay_focus_session_from_paths,
    replay_stored_run_from_paths,
    replay_timeline_from_paths,
)
from backend.app.runtime import PaperRuntime
from backend.app.storage.parquet import ParquetEventStore
from backend.app.storage.sqlite import LedgerInvariantError, SQLiteLedger
from backend.app.strategies.registry import (
    StrategyLifecycle,
    StrategyMode,
    StrategyRevisionConflict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


async def _await_shutdown_task(task: asyncio.Task[None]) -> None:
    """종료 신호가 겹쳐도 이미 시작한 안전 저장 작업은 끝까지 기다린다."""

    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)


class ChartSelectionRequest(BaseModel):
    """사용자가 볼 공개 종목과 로컬 캔들 시간구간을 제한한다."""

    symbol: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9]+$")
    interval_seconds: int


class MarketSelectionRequest(BaseModel):
    """공개 시장 보기 선택만 바꾸고 거래 action을 만들지 않는다."""

    source: str = Field(pattern=r"^(BINANCE_USDM|UPBIT_KRW)$")
    symbol: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9-]+$")
    interval_seconds: int = 180
    pin_for_analysis: bool = False


class StrategyConfigurationRequest(BaseModel):
    """전략의 main·shadow 참여와 양방향 허용을 명시적으로 변경한다."""

    mode: StrategyMode
    long_enabled: bool
    short_enabled: bool
    expected_revision: int = Field(ge=0)
    manual_lock: bool = True
    reason: str = Field(default="USER_CONFIGURATION", min_length=3, max_length=120)
    lifecycle: StrategyLifecycle | None = None


class StrategyRollbackRequest(BaseModel):
    """과거 전략 설정을 기록을 지우지 않고 새 revision으로 복원한다."""

    target_revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="USER_ROLLBACK", min_length=3, max_length=120)


class ControlMutationRequest(BaseModel):
    """두 탭과 재전송이 같은 PAPER 제어 의도를 중복 실행하지 않게 한다."""

    expected_revision: int | None = Field(default=None, ge=0)
    reason: str = Field(default="USER_REQUEST", min_length=3, max_length=120)


class ReplayRequest(BaseModel):
    """저장 Run 전체 또는 특정 종목을 같은 PAPER 파이프라인으로 재처리한다."""

    symbol: str | None = Field(
        default=None,
        min_length=3,
        max_length=30,
        pattern=r"^[A-Za-z0-9]+$",
    )


def _local_browser_origin(origin: str | None) -> bool:
    if origin is None:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def _runtime_from_environment() -> PaperRuntime:
    startup_started = time.monotonic()
    requested_mode = os.environ.get("ROBOM_MODE", RuntimeMode.READY.value)
    mode = RuntimeMode(requested_mode)
    default_database = PROJECT_ROOT / "data" / "run-ledger.sqlite3"
    database = Path(os.environ.get("ROBOM_DB_PATH", str(default_database)))
    archive_path = os.environ.get("ROBOM_MARKET_ARCHIVE_PATH")
    storage_started = time.monotonic()
    market_event_archive = (
        ParquetEventStore(
            Path(archive_path),
            minimum_free_bytes=int(os.environ.get("ROBOM_MIN_FREE_BYTES", str(2 * 1024**3))),
            minimum_free_ratio=float(os.environ.get("ROBOM_MIN_FREE_RATIO", "0.05")),
        )
        if archive_path
        else None
    )
    storage_init_ms = (time.monotonic() - storage_started) * 1_000
    ledger_started = time.monotonic()
    ledger = SQLiteLedger(database, market_event_archive=market_event_archive)
    ledger_open_ms = (time.monotonic() - ledger_started) * 1_000
    clock = SystemClock()
    recovery_error: LedgerInvariantError | None = None
    recovery_lookup_started = time.monotonic()
    try:
        recovered = ledger.recover_latest(recovered_ts_ms=clock.utc_ms())
    except LedgerInvariantError as error:
        recovered = None
        recovery_error = error
        mode = RuntimeMode.READY
    recovery_lookup_ms = (time.monotonic() - recovery_lookup_started) * 1_000
    run_id = None
    run_venue = Venue.NONE if mode is RuntimeMode.READY else Venue.FIXTURE
    if recovered is not None and mode is not RuntimeMode.READY:
        run = ledger.get_run(recovered.run_id)
        if run is not None and run["mode"] == mode.value:
            run_id = recovered.run_id
            run_venue = Venue(str(run["venue"]))
    runtime_init_started = time.monotonic()
    runtime = PaperRuntime(
        mode=mode,
        clock=clock,
        run_id=run_id or ("ready" if mode is RuntimeMode.READY else f"run-{uuid4().hex[:12]}"),
        ledger=ledger,
        storage_guard=market_event_archive
        or ParquetEventStore(
            database.parent / "market-parquet",
            minimum_free_bytes=int(os.environ.get("ROBOM_MIN_FREE_BYTES", str(2 * 1024**3))),
            minimum_free_ratio=float(os.environ.get("ROBOM_MIN_FREE_RATIO", "0.05")),
        ),
        market_event_archive=market_event_archive,
        venue=run_venue,
    )
    runtime_init_ms = (time.monotonic() - runtime_init_started) * 1_000
    recovery_ok = True
    recovery_restore_started = time.monotonic()
    if recovery_error is not None:
        runtime._lock_recovery("RECOVERY_CHECKSUM_OR_SCHEMA_INVALID")
        recovery_ok = False
    elif recovered is not None and run_id is not None:
        recovery_ok = runtime.restore_recovery_state(recovered)
    if run_id is not None:
        ledger.record_incident(
            f"recovery-{run_id}-{runtime.clock.utc_ms()}",
            run_id=run_id,
            severity="INFO",
            category="PAPER_RESTART_RECOVERY",
            ts_ms=runtime.clock.utc_ms(),
            payload={
                "lifecycle_state": recovered.lifecycle_state if recovered else "RUN_OPEN",
                "recovery_ok": recovery_ok,
                "open_position": runtime.paper_portfolio.main.position is not None,
            },
        )
    if runtime.mode is RuntimeMode.DEMO_FIXTURE and recovery_ok:
        runtime.boot_demo()
        runtime.paused = False
        runtime.position_visible = True
        runtime.market_data_state = MarketDataState.FIXTURE
        runtime.runtime_health_flags = [
            "OFFLINE_DEMO_ISOLATED",
            "PAPER_STATE_RECOVERED",
        ]
    runtime.startup_storage_init_ms = storage_init_ms
    runtime.startup_ledger_open_ms = ledger_open_ms
    runtime.startup_recovery_lookup_ms = recovery_lookup_ms
    runtime.startup_runtime_init_ms = runtime_init_ms
    runtime.startup_recovery_restore_ms = (
        time.monotonic() - recovery_restore_started
    ) * 1_000
    runtime.startup_total_ms = (time.monotonic() - startup_started) * 1_000
    return runtime


def create_app(
    runtime: PaperRuntime | None = None,
    *,
    control_runners: Mapping[ControlAction, ControlRunner] | None = None,
    market_explorer: MarketExplorerService | None = None,
) -> FastAPI:
    active_runtime = runtime or _runtime_from_environment()
    active_market_explorer = market_explorer or MarketExplorerService()

    def audit_control_transition(operation: dict[str, object]) -> None:
        ledger = active_runtime.ledger
        if ledger is None:
            return
        run_id = (
            active_runtime.run_id
            if ledger.get_run(active_runtime.run_id) is not None
            else None
        )
        ledger.record_incident(
            f"{operation['operation_id']}-rev-{operation['revision']}",
            run_id=run_id,
            severity="INFO",
            category="CONTROL_STATE_TRANSITION",
            ts_ms=int(str(operation["updated_ts_ms"])),
            payload=operation,
        )

    operation_manager = ControlOperationManager(
        active_runtime.clock.utc_ms,
        audit=audit_control_transition,
    )

    def audit_replay_transition(operation: dict[str, object]) -> None:
        ledger = active_runtime.ledger
        if ledger is None:
            return
        source_run_id = str(operation["source_run_id"])
        run_id = source_run_id if ledger.get_run(source_run_id) is not None else None
        ledger.record_incident(
            f"{operation['operation_id']}-rev-{operation['revision']}",
            run_id=run_id,
            severity="INFO",
            category="REPLAY_STATE_TRANSITION",
            ts_ms=int(str(operation["updated_ts_ms"])),
            payload=operation,
        )

    replay_operation_manager = ReplayOperationManager(
        active_runtime.clock.utc_ms,
        audit=audit_replay_transition,
    )
    replay_process_lock = asyncio.Lock()
    replay_results_cache_lock = asyncio.Lock()
    replay_results_cache: list[dict[str, object]] | None = None

    def remember_replay_result(result: dict[str, object]) -> None:
        """완료 결과를 기존 캐시에 중복 없이 추가해 다음 화면 조회를 즉시 처리한다."""

        nonlocal replay_results_cache
        if replay_results_cache is None:
            return
        replay_id = str(result["replay_id"])
        replay_results_cache = [
            *(
                row
                for row in replay_results_cache
                if str(row.get("replay_id")) != replay_id
            ),
            dict(result),
        ]

    def ensure_replay_process_available() -> None:
        """긴 replay 중 다른 화면 조회를 대기열에 묶지 않고 즉시 안내한다."""

        active_operation = replay_operation_manager.active_public()
        if replay_process_lock.locked() or active_operation is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "REPLAY_BUSY",
                    "error_message_ko": (
                        "다른 저장 기록을 검증하고 있습니다. "
                        "진행 상태를 확인하거나 취소한 뒤 다시 시도하세요."
                    ),
                    "retryable": True,
                    "operation": active_operation,
                },
            )
    websocket_clients: set[WebSocket] = set()

    def dashboard_snapshot() -> dict[str, object]:
        return {
            **active_runtime.dashboard(),
            "control_operation": operation_manager.current_public(),
            "control_revision": operation_manager.revision,
        }

    def dashboard_message() -> str:
        return json.dumps(
            {"type": "dashboard", "data": dashboard_snapshot()},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    async def broadcast_dashboard() -> None:
        """연결 수와 무관하게 snapshot을 한 번만 생성·직렬화한다."""

        while True:
            if websocket_clients:
                payload = dashboard_message()
                clients = tuple(websocket_clients)
                results = await asyncio.gather(
                    *(client.send_text(payload) for client in clients),
                    return_exceptions=True,
                )
                for client, result in zip(clients, results, strict=True):
                    if isinstance(result, Exception):
                        websocket_clients.discard(client)
            await asyncio.sleep(0.5)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        broadcaster: asyncio.Task[None] | None = None
        persistence_stop = asyncio.Event()
        persistence_worker: asyncio.Task[None] | None = None
        trade_cache_task: asyncio.Task[None] | None = None
        try:
            if active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER:
                await active_runtime.start_persistent_live()
            if not active_runtime.dashboard_trade_cache_ready:
                active_runtime.dashboard_trade_cache_loading = True
                trade_cache_task = asyncio.create_task(
                    active_runtime.warm_dashboard_trade_cache(),
                    name="dashboard-trade-cache",
                )
            persistence_worker = asyncio.create_task(
                active_runtime.run_persistence_worker(persistence_stop),
                name="persistence-worker",
            )
            broadcaster = asyncio.create_task(broadcast_dashboard(), name="dashboard-broadcaster")
            yield
        finally:
            await operation_manager.shutdown()
            await replay_operation_manager.shutdown()
            if broadcaster is not None:
                broadcaster.cancel()
                await asyncio.gather(broadcaster, return_exceptions=True)
            persistence_stop.set()
            if persistence_worker is not None:
                await _await_shutdown_task(persistence_worker)
            if trade_cache_task is not None:
                await asyncio.gather(trade_cache_task, return_exceptions=True)
            await active_runtime.shutdown()
            if active_runtime.ledger is not None:
                active_runtime.ledger.close()

    app = FastAPI(title="ROBOM FlowScalper", version="0.2.0-paper", lifespan=lifespan)
    app.state.runtime = active_runtime
    app.state.control_operation_manager = operation_manager
    app.state.replay_operation_manager = replay_operation_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "http://localhost:5173",
        ],
        allow_origin_regex=r"https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?",
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        return active_runtime.status().model_dump(mode="json")

    @app.get("/api/events")
    async def events() -> list[dict[str, object]]:
        return [event.model_dump(mode="json") for event in active_runtime.events[-100:]]

    @app.get("/api/dashboard")
    async def dashboard() -> dict[str, object]:
        return dashboard_snapshot()

    @app.get("/api/markets/catalog")
    async def market_catalog(
        source: str | None = Query(default=None, pattern=r"^(BINANCE_USDM|UPBIT_KRW)$"),
        refresh: bool = Query(default=False),
    ) -> dict[str, object]:
        try:
            return await active_market_explorer.catalog(source=source, force=refresh)
        except (OSError, httpx.HTTPError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "PUBLIC_MARKET_CATALOG_UNAVAILABLE",
                    "error_message_ko": "공개 시장 목록을 불러오지 못했습니다.",
                    "retryable": True,
                },
            ) from error

    @app.get("/api/markets/candles")
    async def market_candles(
        source: str = Query(default="BINANCE_USDM", pattern=r"^(BINANCE_USDM|UPBIT_KRW)$"),
        symbol: str = Query(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9-]+$"),
        interval_seconds: int = Query(default=180),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, object]:
        try:
            return await active_market_explorer.candles(
                source,
                symbol,
                interval_seconds,
                limit,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "INVALID_MARKET_CANDLE_REQUEST",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        except (OSError, httpx.HTTPError) as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "PUBLIC_MARKET_CANDLES_UNAVAILABLE",
                    "error_message_ko": "공개 시장 캔들을 불러오지 못했습니다.",
                    "retryable": True,
                },
            ) from error

    @app.post("/api/markets/select")
    async def select_market(request: MarketSelectionRequest) -> dict[str, object]:
        if request.source == "BINANCE_USDM":
            active_runtime.set_chart_selection(request.symbol, request.interval_seconds)
        return {
            "source": request.source,
            "symbol": request.symbol.upper(),
            "interval_seconds": request.interval_seconds,
            "pin_for_analysis": request.pin_for_analysis and request.source == "BINANCE_USDM",
            "observation_only": request.source == "UPBIT_KRW",
            "auth_required": False,
            "real_orders_enabled": False,
        }

    @app.get("/api/markets/status")
    async def market_status() -> dict[str, object]:
        catalog = await active_market_explorer.catalog()
        return {
            "catalog_healthy": True,
            "catalog_counts": catalog["counts"],
            "candle_cache_count": 0,
            "auth_required": False,
            "real_orders_enabled": False,
        }

    @app.post("/api/control/pause")
    async def pause_entries() -> dict[str, object]:
        active_runtime.set_paused(True)
        return dashboard_snapshot()

    @app.post("/api/control/resume")
    async def resume_entries() -> dict[str, object]:
        active_runtime.set_paused(False)
        return dashboard_snapshot()

    @app.post("/api/control/emergency-close")
    async def emergency_paper_close() -> dict[str, object]:
        active_runtime.emergency_paper_close()
        return dashboard_snapshot()

    async def start_live_runner(progress: ProgressCallback) -> None:
        if active_runtime.live_observation_running():
            await progress("PREPARING", "이미 같은 PAPER Run에서 자동 관찰이 작동 중입니다")
            return
        blocked = active_runtime.live_start_block()
        if blocked is not None:
            raise ControlOperationFailure(
                code=blocked[0],
                message_ko=blocked[1],
                retryable=False,
            )
        started = await active_runtime.start_live_run(progress=progress)
        if not started:
            raise ControlOperationFailure(
                code="PUBLIC_DATA_UNAVAILABLE",
                message_ko=(
                    "공개시장에 연결하지 못했습니다. 네트워크 상태를 확인한 뒤 다시 시도하세요."
                ),
                retryable=True,
            )

    async def start_demo_runner(progress: ProgressCallback) -> None:
        await progress("PREPARING", "샘플 PAPER Run을 준비하고 있습니다")
        await active_runtime.shutdown_supervisor()
        active_runtime.start_demo_run()

    async def new_run_runner(progress: ProgressCallback) -> None:
        await progress("PREPARING", "기존 기록을 보존하고 새 PAPER Run을 준비합니다")
        await active_runtime.shutdown_supervisor()
        try:
            active_runtime.start_new_run()
        except ValueError as error:
            raise ControlOperationFailure(
                code="NEW_RUN_BLOCKED",
                message_ko=str(error),
                retryable=False,
            ) from error
        if active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            started = await active_runtime.start_persistent_live(progress=progress)
            if not started:
                raise ControlOperationFailure(
                    code="PUBLIC_DATA_UNAVAILABLE",
                    message_ko=(
                        "공개시장에 연결하지 못했습니다. 네트워크 상태를 확인한 뒤 다시 시도하세요."
                    ),
                    retryable=True,
                )

    async def submit_operation(
        action: ControlAction,
        runner: ControlRunner,
        *,
        request: ControlMutationRequest | None,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        try:
            return await operation_manager.submit(
                action,
                runner,
                idempotency_key=idempotency_key,
                expected_revision=request.expected_revision if request is not None else None,
                actor="USER_UI",
                reason=request.reason if request is not None else "USER_REQUEST",
            )
        except ControlRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "CONTROL_REVISION_CONFLICT",
                    "error_message_ko": (
                        "다른 화면에서 상태가 바뀌었습니다. 최신 상태를 다시 확인하세요."
                    ),
                    "retryable": True,
                    "expected_revision": error.expected_revision,
                    "current_revision": error.current_revision,
                    "current_operation": operation_manager.current_public(),
                },
            ) from error
        except ControlOperationConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "CONTROL_OPERATION_CONFLICT",
                    "error_message_ko": "다른 PAPER 실행 작업이 진행 중입니다.",
                    "retryable": False,
                    "current_operation": error.current_operation,
                },
            ) from error

    @app.post("/api/control/start-live", status_code=202)
    async def start_live(
        request: ControlMutationRequest | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, object]:
        runner = (control_runners or {}).get(ControlAction.START_LIVE, start_live_runner)
        return await submit_operation(
            ControlAction.START_LIVE,
            runner,
            request=request,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/control/start-demo", status_code=202)
    async def start_demo(
        request: ControlMutationRequest | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, object]:
        runner = (control_runners or {}).get(ControlAction.START_DEMO, start_demo_runner)
        return await submit_operation(
            ControlAction.START_DEMO,
            runner,
            request=request,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/control/operations/current")
    async def current_operation() -> dict[str, object] | None:
        return operation_manager.current_public()

    @app.get("/api/control/operations/{operation_id}")
    async def operation_by_id(operation_id: str) -> dict[str, object]:
        operation = operation_manager.get_public(operation_id)
        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "CONTROL_OPERATION_NOT_FOUND",
                    "error_message_ko": "요청한 실행 작업을 찾을 수 없습니다.",
                    "retryable": False,
                },
            )
        return operation

    @app.post("/api/control/operations/{operation_id}/cancel", status_code=202)
    async def cancel_operation(operation_id: str) -> dict[str, object]:
        operation = await operation_manager.cancel(operation_id)
        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "CONTROL_OPERATION_NOT_FOUND",
                    "error_message_ko": "취소할 실행 작업을 찾을 수 없습니다.",
                    "retryable": False,
                },
            )
        return operation

    @app.post("/api/control/chart")
    async def select_chart(request: ChartSelectionRequest) -> dict[str, object]:
        try:
            active_runtime.set_chart_selection(request.symbol, request.interval_seconds)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "INVALID_CHART_SELECTION",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        return dashboard_snapshot()

    @app.post("/api/strategies/{strategy_id}")
    async def configure_strategy(
        strategy_id: str,
        request: StrategyConfigurationRequest,
    ) -> dict[str, object]:
        try:
            await asyncio.to_thread(
                active_runtime.configure_strategy,
                strategy_id,
                mode=request.mode,
                long_enabled=request.long_enabled,
                short_enabled=request.short_enabled,
                expected_revision=request.expected_revision,
                manual_lock=request.manual_lock,
                lifecycle=request.lifecycle,
                source="USER_UI",
                reason=request.reason,
            )
        except StrategyRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "STRATEGY_SETTINGS_REVISION_CONFLICT",
                    "error_message_ko": (
                        "다른 화면에서 전략 설정이 바뀌었습니다. 최신 설정을 확인하세요."
                    ),
                    "retryable": True,
                    "current_strategy": error.current_setting,
                },
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "STRATEGY_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        return dashboard_snapshot()

    @app.post("/api/strategies/{strategy_id}/rollback")
    async def rollback_strategy(
        strategy_id: str,
        request: StrategyRollbackRequest,
    ) -> dict[str, object]:
        try:
            await asyncio.to_thread(
                active_runtime.rollback_strategy,
                strategy_id,
                target_revision=request.target_revision,
                expected_revision=request.expected_revision,
                reason=request.reason,
            )
        except StrategyRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "STRATEGY_SETTINGS_REVISION_CONFLICT",
                    "error_message_ko": (
                        "다른 화면에서 전략 설정이 바뀌었습니다. 최신 설정을 확인하세요."
                    ),
                    "retryable": True,
                    "current_strategy": error.current_setting,
                },
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "STRATEGY_ROLLBACK_INVALID",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        return dashboard_snapshot()

    @app.get("/api/governance")
    async def strategy_governance() -> dict[str, object]:
        include_persisted = active_runtime.mode is not RuntimeMode.LIVE_SHADOW_PAPER
        return await asyncio.to_thread(
            active_runtime.strategy_governance,
            include_persisted=include_persisted,
        )

    @app.get("/api/analytics/strategies")
    async def strategy_analytics() -> list[dict[str, object]]:
        include_persisted = active_runtime.mode is not RuntimeMode.LIVE_SHADOW_PAPER
        return await asyncio.to_thread(
            active_runtime.strategy_performance,
            include_persisted=include_persisted,
        )

    @app.get("/api/analytics/strategy-symbols")
    async def strategy_symbol_analytics() -> dict[str, object]:
        include_persisted = active_runtime.mode is not RuntimeMode.LIVE_SHADOW_PAPER
        rows = await asyncio.to_thread(
            active_runtime.strategy_symbol_performance,
            include_persisted=include_persisted,
        )
        scope = await asyncio.to_thread(
            active_runtime.strategy_analytics_scope,
            include_persisted=include_persisted,
        )
        return {
            "generated_ts_ms": active_runtime.clock.utc_ms(),
            "rows": rows,
            "ranking_rule": "표본 30건 이상에서 기대값·Profit Factor·표본을 함께 비교",
            "real_orders_enabled": False,
            "auth_required": False,
            **scope,
        }

    @app.get("/api/history")
    async def history_records(
        run_scope: str = Query(default="CURRENT", pattern=r"^(CURRENT|ALL)$"),
        account_scope: str = Query(default="MAIN", pattern=r"^(MAIN|LEAGUE|ALL)$"),
        profile: str = Query(default="ALL", pattern=r"^(BASE|STRESS|ALL)$"),
        version_scope: str = Query(default="CURRENT", pattern=r"^(CURRENT|ALL)$"),
        sample_type: str = Query(
            default="ALL",
            pattern=r"^(LIVE_PUBLIC|OFFLINE_FIXTURE|ALL)$",
        ),
        limit: int = Query(default=500, ge=1, le=2_000),
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            active_runtime.history_records,
            run_scope=run_scope,
            account_scope=account_scope,
            profile=profile,
            version_scope=version_scope,
            sample_type=sample_type,
            limit=limit,
        )

    @app.get("/api/replay/runs")
    async def replay_runs() -> list[dict[str, object]]:
        return await asyncio.to_thread(active_runtime.replayable_runs)

    @app.get("/api/replay/results")
    async def replay_results() -> list[dict[str, object]]:
        nonlocal replay_results_cache
        if active_runtime.ledger is None:
            return []
        if replay_results_cache is not None:
            return [dict(row) for row in replay_results_cache]
        async with replay_results_cache_lock:
            if replay_results_cache is None:
                replay_results_cache = await asyncio.to_thread(
                    active_runtime.ledger.list_replay_runs
                )
        cached_results = replay_results_cache
        assert cached_results is not None
        return [dict(row) for row in cached_results]

    @app.get("/api/replay/operations/current")
    async def current_replay_operation() -> dict[str, object] | None:
        """새로고침 뒤에도 현재 장시간 검증 상태를 다시 붙일 수 있게 한다."""

        return replay_operation_manager.current_public()

    @app.get("/api/replay/operations/{operation_id}")
    async def replay_operation(operation_id: str) -> dict[str, object]:
        operation = replay_operation_manager.get_public(operation_id)
        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_OPERATION_NOT_FOUND",
                    "error_message_ko": "저장 Run 검증 작업을 찾을 수 없습니다.",
                    "retryable": False,
                },
            )
        return operation

    @app.delete("/api/replay/operations/{operation_id}", status_code=202)
    async def cancel_replay_operation(operation_id: str) -> dict[str, object]:
        operation = await replay_operation_manager.cancel(operation_id)
        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_OPERATION_NOT_FOUND",
                    "error_message_ko": "취소할 저장 Run 검증 작업을 찾을 수 없습니다.",
                    "retryable": False,
                },
            )
        return operation

    @app.get("/api/replay/{run_id}/preview")
    async def replay_preview(
        run_id: str,
        symbol: str | None = Query(
            default=None,
            min_length=3,
            max_length=30,
            pattern=r"^[A-Za-z0-9]+$",
        ),
        candle_limit: int = Query(default=500, ge=1, le=2_000),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                active_runtime.replay_preview,
                run_id,
                symbol=symbol,
                candle_limit=candle_limit,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_PREVIEW_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error

    @app.get("/api/replay/{run_id}/timeline")
    async def replay_timeline(
        run_id: str,
        symbol: str | None = Query(
            default=None,
            min_length=3,
            max_length=30,
            pattern=r"^[A-Za-z0-9]+$",
        ),
        limit: int = Query(default=2_000, ge=1, le=2_000),
    ) -> dict[str, object]:
        try:
            if (
                active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER
                and active_runtime.ledger is not None
            ):
                ensure_replay_process_available()
                archive = active_runtime.ledger.market_event_archive
                async with replay_process_lock:
                    return await to_process.run_sync(
                        replay_timeline_from_paths,
                        str(active_runtime.ledger.path),
                        str(archive.root) if archive is not None else None,
                        run_id,
                        symbol,
                        limit,
                    )
            return await asyncio.to_thread(
                active_runtime.replay_timeline,
                run_id,
                symbol=symbol,
                limit=limit,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_TIMELINE_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error

    @app.get("/api/replay/{run_id}/focus")
    async def replay_focus(
        run_id: str,
        trade_id: str = Query(min_length=3, max_length=120),
        profile: str = Query(default="BASE", pattern=r"^(BASE|STRESS)$"),
    ) -> dict[str, object]:
        try:
            if (
                active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER
                and active_runtime.ledger is not None
            ):
                ensure_replay_process_available()
                await asyncio.to_thread(active_runtime.flush_storage)
                archive = active_runtime.ledger.market_event_archive
                async with replay_process_lock:
                    return await to_process.run_sync(
                        replay_focus_session_from_paths,
                        str(active_runtime.ledger.path),
                        str(archive.root) if archive is not None else None,
                        run_id,
                        trade_id,
                        profile,
                        active_runtime.clock.utc_ms(),
                    )
            return await asyncio.to_thread(
                active_runtime.replay_focus_session,
                run_id,
                trade_id=trade_id,
                profile=profile,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_FOCUS_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error

    @app.post("/api/replay/{run_id}", status_code=202)
    async def replay_run(run_id: str, request: ReplayRequest) -> dict[str, object]:
        if active_runtime.ledger is None or active_runtime.ledger.get_run(run_id) is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_RUN_NOT_FOUND",
                    "error_message_ko": f"알 수 없는 소스 Run: {run_id}",
                    "retryable": False,
                },
            )
        symbol = request.symbol.strip().upper() if request.symbol else None
        try:
            if replay_process_lock.locked():
                ensure_replay_process_available()
            total_events: int | None = None
            if symbol is not None:
                symbols = await asyncio.to_thread(
                    active_runtime.ledger.market_event_symbols,
                    run_id,
                )
                selected = next(
                    (row for row in symbols if str(row["symbol"]) == symbol),
                    None,
                )
                if selected is not None and selected.get("event_count") is not None:
                    total_events = int(str(selected["event_count"]))
            if total_events is None:
                replayable = await asyncio.to_thread(active_runtime.replayable_runs)
                selected_run = next(
                    (row for row in replayable if str(row["run_id"]) == run_id),
                    None,
                )
                if selected_run is not None and selected_run.get("market_event_count") is not None:
                    total_events = int(str(selected_run["market_event_count"]))

            async def runner(progress: ProgressCallback) -> dict[str, object]:
                await progress(
                    "PREPARING",
                    "저장 원장과 공개시장 이벤트를 안전하게 준비하고 있습니다",
                )
                if (
                    active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER
                    and active_runtime.ledger is not None
                ):
                    await asyncio.to_thread(active_runtime.flush_storage)
                    archive = active_runtime.ledger.market_event_archive
                    await progress(
                        "PROCESSING",
                        "저우선순위 프로세스에서 같은 전략 조건으로 검증하고 있습니다",
                    )
                    async with replay_process_lock:
                        completed = await to_process.run_sync(
                            replay_stored_run_from_paths,
                            str(active_runtime.ledger.path),
                            str(archive.root) if archive is not None else None,
                            run_id,
                            active_runtime.clock.utc_ms(),
                            symbol,
                            cancellable=True,
                        )
                    remember_replay_result(completed)
                    return completed
                await progress(
                    "PROCESSING",
                    "저장 이벤트를 같은 전략 조건으로 검증하고 있습니다",
                )
                completed = await asyncio.to_thread(
                    active_runtime.replay_stored_run,
                    run_id,
                    symbol=symbol,
                )
                remember_replay_result(completed)
                return completed

            return await replay_operation_manager.submit(
                source_run_id=run_id,
                symbol=symbol,
                total_events=total_events,
                runner=runner,
            )
        except ReplayOperationConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "REPLAY_BUSY",
                    "error_message_ko": (
                        "다른 저장 기록을 검증하고 있습니다. "
                        "진행 상태를 확인하거나 취소하세요."
                    ),
                    "retryable": True,
                    "operation": error.current_operation,
                },
            ) from error
        except HTTPException:
            raise
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_RUN_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error

    @app.post("/api/control/new-run", status_code=202)
    async def new_run(
        request: ControlMutationRequest | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, object]:
        runner = (control_runners or {}).get(ControlAction.NEW_RUN, new_run_runner)
        return await submit_operation(
            ControlAction.NEW_RUN,
            runner,
            request=request,
            idempotency_key=idempotency_key,
        )

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if not _local_browser_origin(origin):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            await websocket.send_text(dashboard_message())
            websocket_clients.add(websocket)
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return
        finally:
            websocket_clients.discard(websocket)

    @app.websocket("/ws/markets")
    async def market_websocket(websocket: WebSocket) -> None:
        """인증 없는 catalog snapshot을 dashboard 채널과 분리해 보낸다."""

        if not _local_browser_origin(websocket.headers.get("origin")):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            catalog = await active_market_explorer.catalog()
            await websocket.send_json({"type": "catalog_snapshot", "data": catalog})
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return

    if (FRONTEND_DIST / "assets").is_dir() and (FRONTEND_DIST / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def static_frontend(path: str) -> FileResponse:
            candidate = FRONTEND_DIST / path
            if candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST.resolve()):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
