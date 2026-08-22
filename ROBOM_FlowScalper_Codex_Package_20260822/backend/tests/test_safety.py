"""실제 주문 경로가 구조적으로 생기지 않는지 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.domain.models import RuntimeMode
from backend.app.domain.safety import RealTradingDisabledError, assert_paper_only
from backend.app.main import _local_browser_origin
from scripts.run_server import RemoteBindingDisabledError, validate_local_host
from scripts.select_local_port import choose_local_port


def test_runtime_mode_has_no_real_trading_member() -> None:
    assert {mode.value for mode in RuntimeMode} == {
        "READY",
        "LIVE_SHADOW_PAPER",
        "DEMO_FIXTURE",
        "REPLAY",
    }
    with pytest.raises(ValueError):
        RuntimeMode("LIVE_TRADING")


def test_real_trading_environment_is_rejected() -> None:
    with pytest.raises(RealTradingDisabledError):
        assert_paper_only(RuntimeMode.READY, {"REAL_TRADING": "true"})


def test_runtime_source_has_no_exchange_execution_routes() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    forbidden_fragments = ("/fapi/v1/order", "/v5/order/create", "api_secret", "withdraw")
    production = "\n".join(path.read_text() for path in root.rglob("*.py"))
    assert not any(fragment in production.lower() for fragment in forbidden_fragments)


def test_supported_launcher_rejects_remote_bindings() -> None:
    assert validate_local_host("127.0.0.1") == "127.0.0.1"
    assert validate_local_host("localhost") == "localhost"
    assert validate_local_host("::1") == "::1"
    for host in ("0.0.0.0", "192.168.0.10", "example.com", ""):
        with pytest.raises(RemoteBindingDisabledError):
            validate_local_host(host)


def test_websocket_origin_accepts_only_local_browser_hosts() -> None:
    assert _local_browser_origin(None)
    assert _local_browser_origin("http://127.0.0.1:8876")
    assert _local_browser_origin("http://localhost:5173")
    assert not _local_browser_origin("https://example.com")
    assert not _local_browser_origin("file:///tmp/index.html")


def test_clickable_launcher_selects_a_bounded_local_port() -> None:
    selected = choose_local_port(48_000, attempts=10)

    assert 48_000 <= selected < 48_010
    with pytest.raises(ValueError):
        choose_local_port(0)
    with pytest.raises(ValueError):
        choose_local_port(65_535, attempts=2)
