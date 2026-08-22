"""오프라인 fixture가 PAPER 상태로 끝까지 부팅되는지 검증한다."""

from fastapi.testclient import TestClient

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import RuntimeMode
from backend.app.main import create_app
from backend.app.runtime import PaperRuntime


def test_fixture_boot_is_honestly_labeled() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.FIXTURE_OFFLINE,
        clock=DeterministicClock(),
        run_id="run-test",
    )
    runtime.boot_fixture()
    client = TestClient(create_app(runtime))

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "FIXTURE_OFFLINE",
        "market_data_state": "FIXTURE",
        "execution_state": "PAPER",
        "venue": "FIXTURE",
        "run_id": "run-test",
        "real_orders_enabled": False,
        "auth_required": False,
        "starting_equity_usdt": 1000.0,
        "current_equity_usdt": 1000.0,
        "wide_symbols": 10,
        "deep_symbols": 10,
        "processing_lag_p95_ms": None,
        "health_flags": ["OFFLINE_SIMULATION"],
    }


def test_fixture_events_are_deterministic() -> None:
    first = PaperRuntime(clock=DeterministicClock(), run_id="run-a")
    second = PaperRuntime(clock=DeterministicClock(), run_id="run-a")
    first.boot_fixture(20)
    second.boot_fixture(20)
    assert first.events == second.events
    assert all(not event.quality.is_live for event in first.events)

