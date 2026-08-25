"""PAPER 제어가 즉시 응답하고 취소·충돌·오류 상태를 정직하게 남기는지 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.control import (
    ControlAction,
    ControlOperationFailure,
    ControlOperationManager,
)
from backend.app.domain.models import RuntimeMode, Venue
from backend.app.main import create_app
from backend.app.market_data.supervisor import ProviderSelection
from backend.app.runtime import PaperRuntime


async def _wait_for_terminal(manager: ControlOperationManager) -> dict[str, object]:
    for _ in range(50):
        operation = manager.current_public()
        if operation is not None and operation["state"] in {
            "COMPLETED",
            "FAILED_RETRYABLE",
            "FAILED_BLOCKED",
            "CANCELLED",
        }:
            return operation
        await asyncio.sleep(0)
    raise AssertionError("control operation이 종료되지 않았습니다.")


async def test_slow_start_live_returns_202_and_deduplicates(monkeypatch) -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_start(
        self: PaperRuntime,
        probe: object = None,
        progress: object = None,
    ) -> bool:
        del self, probe
        started.set()
        if progress is not None:
            await progress("PREPARING", "새 PAPER Run을 준비하고 있습니다")
            await progress("CONNECTING_PRIMARY", "주 거래소를 확인하고 있습니다")
        await release.wait()
        return True

    monkeypatch.setattr(PaperRuntime, "start_live_run", slow_start)
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    transport = httpx.ASGITransport(app=create_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await asyncio.wait_for(client.post("/api/control/start-live"), timeout=0.2)
        assert first.status_code == 202
        assert await asyncio.wait_for(started.wait(), timeout=0.2)
        second = await client.post("/api/control/start-live")
        conflict = await client.post("/api/control/new-run")
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert first.json()["operation_id"] == second.json()["operation_id"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error_code"] == "CONTROL_OPERATION_CONFLICT"


async def test_manager_records_stages_retryable_failure_and_bounded_history() -> None:
    clock = DeterministicClock()
    manager = ControlOperationManager(clock.utc_ms)

    async def runner(progress: object) -> None:
        for index in range(24):
            clock.advance_ms(1)
            await progress("PREPARING", f"준비 {index}")
        await progress("CONNECTING_PRIMARY", "주 거래소 확인")
        await progress("CONNECTING_FALLBACK", "대체 거래소 확인")
        raise ControlOperationFailure(
            code="PUBLIC_DATA_UNAVAILABLE",
            message_ko="공개시장에 연결하지 못했습니다.",
            retryable=True,
        )

    await manager.submit(ControlAction.START_LIVE, runner)
    operation = await _wait_for_terminal(manager)

    assert operation["state"] == "FAILED_RETRYABLE"
    assert operation["retryable"] is True
    assert operation["error_code"] == "PUBLIC_DATA_UNAVAILABLE"
    assert len(operation["history"]) == 20
    assert [item["state"] for item in operation["history"]][-3:] == [
        "CONNECTING_PRIMARY",
        "CONNECTING_FALLBACK",
        "FAILED_RETRYABLE",
    ]


async def test_cancel_moves_through_cancelling_to_cancelled() -> None:
    manager = ControlOperationManager(DeterministicClock().utc_ms)
    release = asyncio.Event()

    async def runner(progress: object) -> None:
        await progress("PREPARING", "준비 중")
        await release.wait()

    operation = await manager.submit(ControlAction.START_LIVE, runner)
    await asyncio.sleep(0)
    cancelling = await manager.cancel(str(operation["operation_id"]))
    assert cancelling is not None
    assert cancelling["state"] == "CANCELLING"
    terminal = await _wait_for_terminal(manager)
    assert terminal["state"] == "CANCELLED"


async def test_blocked_start_is_failed_blocked_and_dashboard_exposes_operation() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    runtime.runtime_health_flags = ["RECOVERY_FAIL_CLOSED"]
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/control/start-live")
        assert response.status_code == 202
        await asyncio.sleep(0)
        current = await client.get("/api/control/operations/current")
        dashboard = await client.get("/api/dashboard")
        missing = await client.get("/api/control/operations/control-missing")

    assert current.json()["state"] == "FAILED_BLOCKED"
    assert current.json()["retryable"] is False
    assert dashboard.json()["control_operation"]["state"] == "FAILED_BLOCKED"
    assert missing.status_code == 404


class NeverReadyProvider:
    venue = Venue.BINANCE_USDM

    async def prepare(
        self,
        *,
        run_id: str,
        clock: DeterministicClock,
    ) -> ProviderSelection:
        del run_id, clock
        symbols = tuple(f"S{index:02d}USDT" for index in range(50))
        return ProviderSelection(
            venue=self.venue,
            instruments={},
            tickers={},
            wide_symbols=symbols,
            deep_symbols=symbols[:10],
            bootstrap_events=(),
        )

    async def events(
        self,
        selection: ProviderSelection,
        *,
        run_id: str,
        clock: DeterministicClock,
    ) -> AsyncIterator[object]:
        del selection, run_id, clock
        await asyncio.Event().wait()
        if False:
            yield object()


async def test_cancel_during_supervisor_start_leaves_no_tasks(monkeypatch) -> None:
    provider = NeverReadyProvider()
    monkeypatch.setattr("backend.app.runtime.BinancePersistentProvider", lambda **_: provider)
    monkeypatch.setattr("backend.app.runtime.BybitPersistentProvider", lambda **_: provider)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-cancel-start",
        clock=DeterministicClock(),
    )

    task = asyncio.create_task(runtime.start_persistent_live())
    for _ in range(20):
        if any(
            child.get_name() == "public-producer-run-cancel-start"
            for child in asyncio.all_tasks()
        ):
            break
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    leaked = [
        child.get_name()
        for child in asyncio.all_tasks()
        if not child.done()
        and child.get_name()
        in {
            "public-producer-run-cancel-start",
            "paper-consumer-run-cancel-start",
        }
    ]
    assert leaked == []
    assert runtime.paused is True
    assert runtime.market_data_state.value != "LIVE"


def test_dashboard_has_eighteen_accounts_and_split_risk_contract() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    dashboard = runtime.dashboard()

    assert len(dashboard["league_accounts"]) == 20
    assert dashboard["league_positions"] == []
    assert dashboard["risk"]["shared_capital"]["risk_per_position"] == "0.10%"
    assert dashboard["risk"]["strategy_league"]["account_count"] == 20
    assert dashboard["risk"]["strategy_league"]["maximum_effective_leverage"] == "5.00x"
    assert dashboard["status"]["real_orders_enabled"] is False
    assert dashboard["status"]["auth_required"] is False
