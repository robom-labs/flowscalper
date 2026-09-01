# V6 UI heartbeat와 전체 대시보드 재생성 주기 분리를 검증한다.

from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

import backend.app.main as main_module
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import RuntimeMode
from backend.app.main import create_app
from backend.app.runtime import PaperRuntime


async def test_ui_summary_reuses_full_dashboard_across_heartbeat_ticks(
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    original_dashboard = PaperRuntime.dashboard
    build_count = 0

    def counted_dashboard(active_runtime: PaperRuntime) -> dict[str, object]:
        nonlocal build_count
        build_count += 1
        return original_dashboard(active_runtime)

    monkeypatch.setattr(PaperRuntime, "dashboard", counted_dashboard)
    monkeypatch.setattr(main_module, "_UI_FULL_DASHBOARD_REFRESH_SECONDS", 0.08)
    transport = httpx.ASGITransport(app=create_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/ui/summary")
        await asyncio.sleep(0.03)
        second = await client.get("/api/ui/summary")
        await asyncio.sleep(0.06)
        third = await client.get("/api/ui/summary")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert first.json()["schema_version"] == 1
    assert first.json()["paper_only"] is True
    assert first.json()["real_orders_enabled"] is False
    assert first.json()["auth_required"] is False
    assert build_count == 2


async def test_ui_summary_identity_change_bypasses_longer_full_refresh_window(
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = PaperRuntime(mode=RuntimeMode.DEMO_FIXTURE, clock=DeterministicClock())
    runtime.paused = False
    original_dashboard = PaperRuntime.dashboard
    build_count = 0

    def counted_dashboard(active_runtime: PaperRuntime) -> dict[str, object]:
        nonlocal build_count
        build_count += 1
        return original_dashboard(active_runtime)

    monkeypatch.setattr(PaperRuntime, "dashboard", counted_dashboard)
    monkeypatch.setattr(main_module, "_UI_FULL_DASHBOARD_REFRESH_SECONDS", 60.0)
    transport = httpx.ASGITransport(app=create_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/ui/summary")
        runtime.set_paused(True, expected_revision=0, reason="UI_CACHE_IDENTITY_TEST")
        after = await client.get("/api/ui/summary")

    assert before.status_code == 200
    assert after.status_code == 200
    assert before.json()["paused"] is False
    assert after.json()["paused"] is True
    assert after.json()["paper_entry_intent"]["revision"] == 1
    assert build_count == 2


def test_ui_websocket_heartbeats_do_not_rebuild_full_dashboard(
    monkeypatch: MonkeyPatch,
) -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    original_dashboard = PaperRuntime.dashboard
    build_count = 0

    def counted_dashboard(active_runtime: PaperRuntime) -> dict[str, object]:
        nonlocal build_count
        build_count += 1
        return original_dashboard(active_runtime)

    monkeypatch.setattr(PaperRuntime, "dashboard", counted_dashboard)
    monkeypatch.setattr(main_module, "_DASHBOARD_REFRESH_SECONDS", 0.02)
    monkeypatch.setattr(main_module, "_UI_FULL_DASHBOARD_REFRESH_SECONDS", 0.2)

    with TestClient(create_app(runtime)) as client:
        with client.websocket_connect("/ws/ui") as websocket:
            snapshot = websocket.receive_json()
            first_heartbeat = websocket.receive_json()
            second_heartbeat = websocket.receive_json()

    assert snapshot["type"] == "snapshot"
    assert first_heartbeat["type"] == "heartbeat"
    assert second_heartbeat["type"] == "heartbeat"
    assert build_count == 1
