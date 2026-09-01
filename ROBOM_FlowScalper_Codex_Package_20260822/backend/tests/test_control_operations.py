"""PAPER 제어가 즉시 응답하고 취소·충돌·오류 상태를 정직하게 남기는지 검증한다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.control import (
    ControlAction,
    ControlOperationFailure,
    ControlOperationManager,
    ControlRevisionConflict,
)
from backend.app.domain.models import RuntimeMode, Venue
from backend.app.main import create_app
from backend.app.market_data.supervisor import ProviderSelection
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import SQLiteLedger


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


async def test_completed_idempotency_key_reuses_operation_and_stale_revision_fails() -> None:
    manager = ControlOperationManager(DeterministicClock().utc_ms)
    calls = 0

    async def runner(_progress: object) -> None:
        nonlocal calls
        calls += 1

    first = await manager.submit(
        ControlAction.START_LIVE,
        runner,
        idempotency_key="same-user-intent",
        expected_revision=0,
    )
    completed = await _wait_for_terminal(manager)
    repeated = await manager.submit(
        ControlAction.START_LIVE,
        runner,
        idempotency_key="same-user-intent",
        expected_revision=0,
    )

    assert repeated["operation_id"] == first["operation_id"] == completed["operation_id"]
    assert calls == 1
    with pytest.raises(ControlRevisionConflict):
        await manager.submit(
            ControlAction.NEW_RUN,
            runner,
            idempotency_key="different-intent",
            expected_revision=0,
        )


async def test_dev_origin_preflight_allows_idempotency_header() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    transport = httpx.ASGITransport(app=create_app(runtime))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/control/start-live",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,idempotency-key",
            },
        )

    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "idempotency-key" in allowed


async def test_start_live_while_same_run_is_observed_is_noop(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id="run-already-live",
        venue=Venue.BINANCE_USDM,
    )
    monkeypatch.setattr(PaperRuntime, "live_observation_running", lambda _self: True)

    async def fail_if_restarted(*_args, **_kwargs):
        raise AssertionError("이미 작동 중인 Run을 다시 만들면 안 됩니다.")

    monkeypatch.setattr(PaperRuntime, "start_live_run", fail_if_restarted)
    transport = httpx.ASGITransport(app=create_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/control/start-live",
            headers={"Idempotency-Key": "already-live"},
            json={"expected_revision": 0, "reason": "USER_START_LIVE"},
        )
        assert response.status_code == 202
        for _ in range(20):
            current = (await client.get("/api/control/operations/current")).json()
            if current["state"] == "COMPLETED":
                break
            await asyncio.sleep(0)

    assert runtime.run_id == "run-already-live"
    assert current["state"] == "COMPLETED"
    assert "이미 같은 PAPER Run" in current["history"][-2]["stage_ko"]


async def test_start_live_restarts_stopped_supervisor_without_creating_new_run(
    monkeypatch,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id="run-restart-same-live",
        venue=Venue.BINANCE_USDM,
    )
    monkeypatch.setattr(PaperRuntime, "live_observation_running", lambda _self: False)
    restarts: list[str] = []

    async def restart_same_run(_self, progress=None):
        restarts.append(_self.run_id)
        if progress is not None:
            await progress("CONNECTING_PRIMARY", "같은 PAPER Run을 다시 연결하고 있습니다")
        return True

    async def fail_if_new_run_created(*_args, **_kwargs):
        raise AssertionError("멈춘 LIVE supervisor 복구가 새 Run을 만들면 안 됩니다.")

    monkeypatch.setattr(PaperRuntime, "start_persistent_live", restart_same_run)
    monkeypatch.setattr(PaperRuntime, "start_live_run", fail_if_new_run_created)
    transport = httpx.ASGITransport(app=create_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/control/start-live",
            headers={"Idempotency-Key": "restart-same-live"},
            json={"expected_revision": 0, "reason": "USER_RESTART_LIVE"},
        )
        assert response.status_code == 202
        for _ in range(20):
            current = (await client.get("/api/control/operations/current")).json()
            if current["state"] in {"COMPLETED", "FAILED_BLOCKED", "FAILED_RETRYABLE"}:
                break
            await asyncio.sleep(0)

    assert current["state"] == "COMPLETED"
    assert runtime.run_id == "run-restart-same-live"
    assert restarts == ["run-restart-same-live"]


async def test_control_transition_actor_and_reason_are_written_to_ledger(tmp_path) -> None:
    ledger = SQLiteLedger(tmp_path / "control-audit.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.READY,
        clock=DeterministicClock(),
        ledger=ledger,
    )

    async def runner(_progress: object) -> None:
        return None

    app = create_app(runtime, control_runners={ControlAction.START_LIVE: runner})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/control/start-live",
            headers={"Idempotency-Key": "audited-intent"},
            json={"expected_revision": 0, "reason": "USER_START_LIVE"},
        )
        assert response.status_code == 202
        for _ in range(20):
            operation = (await client.get("/api/control/operations/current")).json()
            if operation["state"] == "COMPLETED":
                break
            await asyncio.sleep(0)

    rows = ledger.list_incidents(category="CONTROL_STATE_TRANSITION")
    assert [row["payload"]["state"] for row in rows] == ["REQUESTED", "COMPLETED"]
    assert all(row["payload"]["actor"] == "USER_UI" for row in rows)
    assert all(row["payload"]["reason"] == "USER_START_LIVE" for row in rows)
    assert [row["payload"]["previous_state"] for row in rows] == ["NONE", "REQUESTED"]
    assert [row["payload"]["new_state"] for row in rows] == ["REQUESTED", "COMPLETED"]
    assert [row["payload"]["request_revision"] for row in rows] == [0, 1]
    assert [row["payload"]["response_revision"] for row in rows] == [1, 2]
    assert [row["payload"]["reversible"] for row in rows] == [True, False]
    assert all(
        row["payload"]["transition_id"] == row["incident_id"] for row in rows
    )
    assert all(row["payload"]["run_id"] is None for row in rows)
    assert all(row["payload"]["strategy_id"] is None for row in rows)
    assert all(row["payload"]["account_id"] is None for row in rows)
    assert all(row["payload"]["symbol"] is None for row in rows)
    assert all(row["payload"]["cause_code"] == "USER_START_LIVE" for row in rows)
    assert rows[-1]["payload"]["description_ko"] == "자동 관찰을 시작했습니다"
    ledger.close()


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
    provider_options: list[dict[str, object]] = []

    def provider_factory(**options: object) -> NeverReadyProvider:
        provider_options.append(options)
        return provider

    monkeypatch.setattr("backend.app.runtime.BinancePersistentProvider", provider_factory)
    monkeypatch.setattr("backend.app.runtime.BybitPersistentProvider", provider_factory)
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
            "event-loop-watchdog-run-cancel-start",
        }
    ]
    assert leaked == []
    assert [options["wide_max"] for options in provider_options] == [80, 80]
    assert [options["deep_max"] for options in provider_options] == [16, 16]
    assert runtime.paused is True
    assert runtime.market_data_state.value != "LIVE"


def test_dashboard_has_registry_derived_accounts_and_split_risk_contract() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    dashboard = runtime.dashboard()

    assert len(dashboard["league_accounts"]) == 30
    assert dashboard["league_positions"] == []
    assert dashboard["risk"]["shared_capital"]["risk_per_position"] == "0.10%"
    assert dashboard["risk"]["strategy_league"]["account_count"] == 30
    assert dashboard["risk"]["strategy_league"]["maximum_effective_leverage"] == "5.00x"
    assert dashboard["status"]["real_orders_enabled"] is False
    assert dashboard["status"]["auth_required"] is False
