"""FastAPI 앱을 구성하고 빌드된 로컬 대시보드를 제공한다."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.clocks import SystemClock
from backend.app.domain.models import MarketDataState, RuntimeMode, Venue
from backend.app.runtime import PaperRuntime
from backend.app.storage.parquet import ParquetEventStore
from backend.app.storage.sqlite import LedgerInvariantError, SQLiteLedger
from backend.app.strategies.registry import StrategyMode

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


class ChartSelectionRequest(BaseModel):
    """사용자가 볼 공개 종목과 로컬 캔들 시간구간을 제한한다."""

    symbol: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9]+$")
    interval_seconds: int


class StrategyConfigurationRequest(BaseModel):
    """전략의 main·shadow 참여와 양방향 허용을 명시적으로 변경한다."""

    mode: StrategyMode
    long_enabled: bool
    short_enabled: bool


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
    requested_mode = os.environ.get("ROBOM_MODE", RuntimeMode.READY.value)
    mode = RuntimeMode(requested_mode)
    default_database = PROJECT_ROOT / "data" / "run-ledger.sqlite3"
    database = Path(os.environ.get("ROBOM_DB_PATH", str(default_database)))
    archive_path = os.environ.get("ROBOM_MARKET_ARCHIVE_PATH")
    market_event_archive = (
        ParquetEventStore(
            Path(archive_path),
            minimum_free_bytes=int(
                os.environ.get("ROBOM_MIN_FREE_BYTES", str(2 * 1024**3))
            ),
            minimum_free_ratio=float(os.environ.get("ROBOM_MIN_FREE_RATIO", "0.05")),
        )
        if archive_path
        else None
    )
    ledger = SQLiteLedger(database, market_event_archive=market_event_archive)
    clock = SystemClock()
    recovery_error: LedgerInvariantError | None = None
    try:
        recovered = ledger.recover_latest(recovered_ts_ms=clock.utc_ms())
    except LedgerInvariantError as error:
        recovered = None
        recovery_error = error
        mode = RuntimeMode.READY
    run_id = None
    run_venue = Venue.NONE if mode is RuntimeMode.READY else Venue.FIXTURE
    if recovered is not None and mode is not RuntimeMode.READY:
        run = ledger.get_run(recovered.run_id)
        if run is not None and run["mode"] == mode.value:
            run_id = recovered.run_id
            run_venue = Venue(str(run["venue"]))
    runtime = PaperRuntime(
        mode=mode,
        clock=clock,
        run_id=run_id or ("ready" if mode is RuntimeMode.READY else f"run-{uuid4().hex[:12]}"),
        ledger=ledger,
        storage_guard=market_event_archive
        or ParquetEventStore(
            database.parent / "market-parquet",
            minimum_free_bytes=int(
                os.environ.get("ROBOM_MIN_FREE_BYTES", str(2 * 1024**3))
            ),
            minimum_free_ratio=float(os.environ.get("ROBOM_MIN_FREE_RATIO", "0.05")),
        ),
        market_event_archive=market_event_archive,
        venue=run_venue,
    )
    recovery_ok = True
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
    return runtime


def create_app(runtime: PaperRuntime | None = None) -> FastAPI:
    active_runtime = runtime or _runtime_from_environment()
    websocket_clients: set[WebSocket] = set()

    def dashboard_message() -> str:
        return json.dumps(
            {"type": "dashboard", "data": active_runtime.dashboard()},
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
        try:
            if active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER:
                await active_runtime.start_persistent_live()
            persistence_worker = asyncio.create_task(
                active_runtime.run_persistence_worker(persistence_stop),
                name="persistence-worker",
            )
            broadcaster = asyncio.create_task(
                broadcast_dashboard(), name="dashboard-broadcaster"
            )
            yield
        finally:
            if broadcaster is not None:
                broadcaster.cancel()
                await asyncio.gather(broadcaster, return_exceptions=True)
            persistence_stop.set()
            if persistence_worker is not None:
                await asyncio.gather(persistence_worker, return_exceptions=True)
            await active_runtime.shutdown()
            if active_runtime.ledger is not None:
                active_runtime.ledger.close()

    app = FastAPI(title="ROBOM FlowScalper", version="0.2.0-paper", lifespan=lifespan)
    app.state.runtime = active_runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "http://localhost:5173",
        ],
        allow_origin_regex=r"https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?",
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        return active_runtime.status().model_dump(mode="json")

    @app.get("/api/events")
    async def events() -> list[dict[str, object]]:
        return [event.model_dump(mode="json") for event in active_runtime.events[-100:]]

    @app.get("/api/dashboard")
    async def dashboard() -> dict[str, object]:
        return active_runtime.dashboard()

    @app.post("/api/control/pause")
    async def pause_entries() -> dict[str, object]:
        active_runtime.set_paused(True)
        return active_runtime.dashboard()

    @app.post("/api/control/resume")
    async def resume_entries() -> dict[str, object]:
        active_runtime.set_paused(False)
        return active_runtime.dashboard()

    @app.post("/api/control/emergency-close")
    async def emergency_paper_close() -> dict[str, object]:
        active_runtime.emergency_paper_close()
        return active_runtime.dashboard()

    @app.post("/api/control/start-live")
    async def start_live() -> dict[str, object]:
        await active_runtime.start_live_run()
        return active_runtime.dashboard()

    @app.post("/api/control/start-demo")
    async def start_demo() -> dict[str, object]:
        await active_runtime.shutdown_supervisor()
        active_runtime.start_demo_run()
        return active_runtime.dashboard()

    @app.post("/api/control/chart")
    async def select_chart(request: ChartSelectionRequest) -> dict[str, object]:
        try:
            active_runtime.set_chart_selection(request.symbol, request.interval_seconds)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return active_runtime.dashboard()

    @app.post("/api/strategies/{strategy_id}")
    async def configure_strategy(
        strategy_id: str,
        request: StrategyConfigurationRequest,
    ) -> dict[str, object]:
        try:
            active_runtime.configure_strategy(
                strategy_id,
                mode=request.mode,
                long_enabled=request.long_enabled,
                short_enabled=request.short_enabled,
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return active_runtime.dashboard()

    @app.get("/api/analytics/strategies")
    async def strategy_analytics() -> list[dict[str, object]]:
        return await asyncio.to_thread(active_runtime.strategy_performance)

    @app.get("/api/replay/runs")
    async def replay_runs() -> list[dict[str, object]]:
        return await asyncio.to_thread(active_runtime.replayable_runs)

    @app.get("/api/replay/results")
    async def replay_results() -> list[dict[str, object]]:
        if active_runtime.ledger is None:
            return []
        return await asyncio.to_thread(active_runtime.ledger.list_replay_runs)

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
            return await asyncio.to_thread(
                active_runtime.replay_timeline,
                run_id,
                symbol=symbol,
                limit=limit,
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/replay/{run_id}")
    async def replay_run(run_id: str, request: ReplayRequest) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                active_runtime.replay_stored_run,
                run_id,
                symbol=request.symbol,
            )
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/control/new-run")
    async def new_run() -> dict[str, object]:
        await active_runtime.shutdown_supervisor()
        try:
            active_runtime.start_new_run()
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            await active_runtime.start_persistent_live()
        return active_runtime.dashboard()

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

    if (
        (FRONTEND_DIST / "assets").is_dir()
        and (FRONTEND_DIST / "index.html").is_file()
    ):
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def static_frontend(path: str) -> FileResponse:
            candidate = FRONTEND_DIST / path
            if candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST.resolve()):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
