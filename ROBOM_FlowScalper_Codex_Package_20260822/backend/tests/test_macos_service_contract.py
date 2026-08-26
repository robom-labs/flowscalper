# macOS 자동 복구가 대형 원장의 안전 종료 유예를 보장하는지 검증한다.
"""LaunchAgent 유지관리 계약의 정적 회귀검사다."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path

from backend.app.api.dashboard import release_identity
from scripts.stage_macos_release import activate_release, stage_release

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


def test_service_uses_immutable_current_release_and_manifest_paths() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(
        encoding="utf-8"
    )
    runner = (PROJECT_ROOT / "scripts" / "run_macos_service.sh").read_text(
        encoding="utf-8"
    )

    assert "scripts/stage_macos_release.py" in installer
    assert '--activate > "$STAGE_RESULT"' in installer
    assert 'PROJECT_DIR="$RUNTIME_ROOT/current"' in installer
    assert 'RELEASE_MANIFEST="$PROJECT_DIR/release-manifest.json"' in runner
    assert 'export ROBOM_RELEASE_COMMIT="$RELEASE_COMMIT"' in runner
    assert 'export ROBOM_RELEASE_ISOLATED="true"' in runner
    assert 'export ROBOM_MARKET_ARCHIVE_PATH="$MARKET_ARCHIVE_PATH"' in runner
    assert 'ROBOM_MARKET_ARCHIVE_PATH="$PROJECT_DIR/data/market-parquet-v6"' not in runner


def test_dashboard_release_identity_is_development_or_exact_immutable_commit(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ROBOM_RELEASE_COMMIT", raising=False)
    monkeypatch.delenv("ROBOM_RELEASE_ISOLATED", raising=False)
    assert release_identity() == ("development", False)

    commit = "a" * 40
    monkeypatch.setenv("ROBOM_RELEASE_COMMIT", commit.upper())
    monkeypatch.setenv("ROBOM_RELEASE_ISOLATED", "true")
    assert release_identity() == (commit, True)


def _git(repository: Path, *arguments: str) -> str:
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "FlowScalper Test",
        "GIT_AUTHOR_EMAIL": "test@localhost",
        "GIT_COMMITTER_NAME": "FlowScalper Test",
        "GIT_COMMITTER_EMAIL": "test@localhost",
    }
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _release_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"
    market_archive = tmp_path / "market-archive"
    ledger = runtime / "active-ledger"
    (source / "frontend" / "dist" / "assets").mkdir(parents=True)
    market_archive.mkdir()
    ledger.mkdir(parents=True)
    (source / "frontend" / "dist" / "index.html").write_text(
        '<html><meta name="robom-release-commit" content="development"></html>',
        encoding="utf-8",
    )
    (source / "frontend" / "dist" / "assets" / "app.js").write_text(
        "window.release = 1\n", encoding="utf-8"
    )
    (source / "backend.py").write_text("RELEASE = 1\n", encoding="utf-8")
    _git(source, "init")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture release one")
    return source, runtime, market_archive, ledger


def test_staged_release_is_unchanged_when_worktree_assets_and_source_change(
    tmp_path: Path,
) -> None:
    source, runtime, market_archive, ledger = _release_fixture(tmp_path)
    manifest = stage_release(
        source,
        runtime,
        market_archive,
        ledger,
        build_frontend=False,
        prebuilt_frontend_dist=source / "frontend" / "dist",
    )
    release = Path(str(manifest["release_path"]))
    release_index = release / "frontend" / "dist" / "index.html"
    release_backend = release / "backend.py"
    index_before = release_index.read_bytes()
    backend_before = release_backend.read_bytes()

    (source / "frontend" / "dist" / "index.html").write_text(
        "<html>mixed worktree asset</html>", encoding="utf-8"
    )
    (source / "backend.py").write_text("RELEASE = 999\n", encoding="utf-8")

    assert release_index.read_bytes() == index_before
    assert release_backend.read_bytes() == backend_before
    assert json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))[
        "real_orders_enabled"
    ] is False


def test_release_pointer_switch_records_rollback_and_can_restore_previous_release(
    tmp_path: Path,
) -> None:
    source, runtime, market_archive, ledger = _release_fixture(tmp_path)
    first = stage_release(
        source,
        runtime,
        market_archive,
        ledger,
        build_frontend=False,
        prebuilt_frontend_dist=source / "frontend" / "dist",
    )
    first_path = Path(str(first["release_path"]))
    activate_release(runtime, first_path)

    (source / "backend.py").write_text("RELEASE = 2\n", encoding="utf-8")
    (source / "frontend" / "dist" / "assets" / "app.js").write_text(
        "window.release = 2\n", encoding="utf-8"
    )
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture release two")
    second = stage_release(
        source,
        runtime,
        market_archive,
        ledger,
        build_frontend=False,
        prebuilt_frontend_dist=source / "frontend" / "dist",
    )
    second_path = Path(str(second["release_path"]))
    transition = activate_release(runtime, second_path)

    assert (runtime / "current").resolve() == second_path
    assert transition["actor"] == "CODEX_DEPLOY"
    assert transition["rollback_release"] == str(first_path)
    assert transition["real_orders_enabled"] is False

    rollback = activate_release(
        runtime,
        first_path,
        reason="OPERATOR_ROLLBACK_TO_VERIFIED_RELEASE",
    )
    assert (runtime / "current").resolve() == first_path
    assert rollback["rollback_release"] == str(second_path)
