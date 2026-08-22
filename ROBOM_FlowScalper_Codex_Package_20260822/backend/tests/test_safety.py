"""실제 주문 경로가 구조적으로 생기지 않는지 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.domain.models import RuntimeMode
from backend.app.domain.safety import RealTradingDisabledError, assert_paper_only


def test_runtime_mode_has_no_real_trading_member() -> None:
    assert {mode.value for mode in RuntimeMode} == {
        "FIXTURE_OFFLINE",
        "LIVE_SHADOW_PAPER",
        "REPLAY",
    }
    with pytest.raises(ValueError):
        RuntimeMode("LIVE_TRADING")


def test_real_trading_environment_is_rejected() -> None:
    with pytest.raises(RealTradingDisabledError):
        assert_paper_only(RuntimeMode.FIXTURE_OFFLINE, {"REAL_TRADING": "true"})


def test_runtime_source_has_no_exchange_execution_routes() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    forbidden_fragments = ("/fapi/v1/order", "/v5/order/create", "api_secret", "withdraw")
    production = "\n".join(path.read_text() for path in root.rglob("*.py"))
    assert not any(fragment in production.lower() for fragment in forbidden_fragments)
