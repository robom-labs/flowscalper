# macOS 자동 복구가 대형 원장의 안전 종료 유예를 보장하는지 검증한다.
"""LaunchAgent 유지관리 계약의 정적 회귀검사다."""

from __future__ import annotations

import plistlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_launch_agent_allows_graceful_paper_persistence_shutdown() -> None:
    plist_path = PROJECT_ROOT / "packaging" / "macos" / "kr.robom.flowscalper.plist"
    with plist_path.open("rb") as stream:
        payload = plistlib.load(stream)

    assert payload["Label"] == "kr.robom.flowscalper"
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["ExitTimeOut"] >= 60
    assert payload["ProcessType"] == "Background"


def test_installer_uses_launchd_graceful_bootout_before_new_bootstrap() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(
        encoding="utf-8"
    )

    bootout_at = installer.index('launchctl bootout "$SERVICE_TARGET"')
    bootstrap_at = installer.index('launchctl bootstrap "gui/$USER_ID" "$TARGET_PLIST"')
    assert bootout_at < bootstrap_at
    assert 'launchctl kickstart -k "$SERVICE_TARGET"' not in installer
