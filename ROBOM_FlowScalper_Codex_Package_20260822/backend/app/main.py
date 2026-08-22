"""FastAPI 앱을 구성하고 빌드된 로컬 대시보드를 제공한다."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.domain.models import RuntimeMode
from backend.app.runtime import PaperRuntime

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


def _runtime_from_environment() -> PaperRuntime:
    requested_mode = os.environ.get("ROBOM_MODE", RuntimeMode.FIXTURE_OFFLINE.value)
    runtime = PaperRuntime(mode=RuntimeMode(requested_mode))
    if runtime.mode is RuntimeMode.FIXTURE_OFFLINE:
        runtime.boot_fixture()
    return runtime


def create_app(runtime: PaperRuntime | None = None) -> FastAPI:
    active_runtime = runtime or _runtime_from_environment()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

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
            await websocket.send_json(
                {"type": "status", "data": active_runtime.status().model_dump(mode="json")}
            )
            await websocket.send_json(
                {
                    "type": "events",
                    "data": [
                        event.model_dump(mode="json") for event in active_runtime.events[-20:]
                    ],
                }
            )
            while True:
                await websocket.receive_text()
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
