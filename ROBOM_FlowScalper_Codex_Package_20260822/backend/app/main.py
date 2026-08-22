"""FastAPI 앱을 구성하고 빌드된 로컬 대시보드를 제공한다."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.clocks import SystemClock
from backend.app.domain.models import RuntimeMode
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import SQLiteLedger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def _runtime_from_environment() -> PaperRuntime:
    requested_mode = os.environ.get("ROBOM_MODE", RuntimeMode.FIXTURE_OFFLINE.value)
    mode = RuntimeMode(requested_mode)
    default_database = PROJECT_ROOT / "data" / "run-ledger.sqlite3"
    database = Path(os.environ.get("ROBOM_DB_PATH", str(default_database)))
    ledger = SQLiteLedger(database)
    clock = SystemClock()
    recovered = ledger.recover_latest(recovered_ts_ms=clock.utc_ms())
    run_id = None
    if recovered is not None:
        run = ledger.get_run(recovered.run_id)
        if run is not None and run["mode"] == mode.value:
            run_id = recovered.run_id
        else:
            ledger.finalize_run(
                recovered.run_id,
                finalized_ts_ms=recovered.recovered_ts_ms,
                summary={"reason": "RUNTIME_MODE_CHANGED", "preserved": True},
            )
    runtime = PaperRuntime(
        mode=mode,
        clock=clock,
        run_id=run_id or f"run-{uuid4().hex[:12]}",
        ledger=ledger,
    )
    if run_id is not None:
        ledger.record_incident(
            f"recovery-{run_id}-{runtime.clock.utc_ms()}",
            run_id=run_id,
            severity="INFO",
            category="PAPER_RESTART_RECOVERY",
            ts_ms=runtime.clock.utc_ms(),
            payload={"lifecycle_state": recovered.lifecycle_state if recovered else "RUN_OPEN"},
        )
    if runtime.mode is RuntimeMode.FIXTURE_OFFLINE:
        runtime.boot_fixture()
    return runtime


def create_app(runtime: PaperRuntime | None = None) -> FastAPI:
    active_runtime = runtime or _runtime_from_environment()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if active_runtime.ledger is not None:
                active_runtime.ledger.close()

    app = FastAPI(title="ROBOM FlowScalper", version="0.1.0-paper", lifespan=lifespan)
    app.state.runtime = active_runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "http://localhost:5173",
        ],
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

    @app.post("/api/control/new-run")
    async def new_run() -> dict[str, object]:
        active_runtime.start_new_run()
        return active_runtime.dashboard()

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        allowed = {
            None,
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "http://localhost:5173",
        }
        if origin not in allowed:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_json({"type": "dashboard", "data": active_runtime.dashboard()})
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            return

    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def static_frontend(path: str) -> FileResponse:
            candidate = FRONTEND_DIST / path
            if candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST.resolve()):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
