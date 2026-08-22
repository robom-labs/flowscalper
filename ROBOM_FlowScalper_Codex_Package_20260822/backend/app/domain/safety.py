"""실제 주문 모드를 구조적으로 거부하는 안전 경계를 제공한다."""

from __future__ import annotations

from collections.abc import Mapping

from backend.app.domain.models import RuntimeMode


class RealTradingDisabledError(RuntimeError):
    """실제 주문 기능을 요청했을 때 발생한다."""


def assert_paper_only(mode: RuntimeMode, environ: Mapping[str, str] | None = None) -> None:
    """허용 모드와 환경이 페이퍼 전용임을 확인한다."""

    if mode not in set(RuntimeMode):
        raise RealTradingDisabledError("지원하지 않는 런타임 모드입니다.")
    values = environ or {}
    enabled = values.get("REAL_TRADING", "false").strip().lower()
    if enabled not in {"false", "0", "no", "off", ""}:
        raise RealTradingDisabledError("REAL_TRADING은 이 제품에서 영구 비활성화되어 있습니다.")
