"""모든 Run에 앱·전략·Git 빌드 식별자를 기록하기 위해 제공한다."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from backend.app.strategies.registry import StrategyRegistry

APP_VERSION = "0.2.0-paper"
STRATEGY_IDS = StrategyRegistry().strategy_ids
STRATEGY_IMPLEMENTATION_REVISION = "2026-08-30-wave116k"
STRATEGY_VERSION = f"{'+'.join(STRATEGY_IDS)}@{STRATEGY_IMPLEMENTATION_REVISION}"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def git_commit() -> str:
    environment_value = os.environ.get("ROBOM_GIT_COMMIT", "").strip()
    if environment_value:
        return environment_value
    packaged = PROJECT_ROOT / "BUILD_COMMIT"
    if packaged.is_file():
        value = packaged.read_text(encoding="utf-8").strip()
        if value:
            return value
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else "UNAVAILABLE"
