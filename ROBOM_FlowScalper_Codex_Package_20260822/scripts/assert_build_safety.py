"""빌드 시 실제 주문 불변조건과 localhost 바인딩을 재확인한다."""

from __future__ import annotations

import os

from backend.app.domain.models import RuntimeMode
from backend.app.domain.safety import assert_paper_only

assert os.environ.get("REAL_TRADING", "false").lower() == "false"
assert_paper_only(RuntimeMode.FIXTURE_OFFLINE, os.environ)
print("PASS: PAPER 전용 빌드 불변조건")

