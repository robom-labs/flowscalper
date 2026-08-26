# macOS 자동 복구가 대형 원장의 안전 종료 유예를 보장하는지 검증한다.
"""LaunchAgent 유지관리 계약의 정적 회귀검사다."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.api.dashboard import release_identity
from backend.app.storage.integrity import RuntimeSafetyViolation
from scripts.stage_macos_release import activate_release, stage_release
from scripts.verify_macos_ledger_maintenance import _validate_initial_runtime

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


def test_installer_can_prepare_release_without_restarting_loaded_service() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(
        encoding="utf-8"
    )

    assert '"${1:-}" == "--prepare-only"' in installer
    prepare_only_at = installer.index('if [[ "$PREPARE_ONLY" == "true" ]]')
    bootout_at = installer.index('launchctl bootout "$SERVICE_TARGET"')
    assert prepare_only_at < bootout_at
    assert "exit 0" in installer[prepare_only_at:bootout_at]


def test_maintenance_recovery_override_allows_only_existing_fail_closed_state() -> None:
    result = _validate_initial_runtime(
        ("ENTRY_LOCKED", "QUEUE_LIMIT_EXCEEDED"),
        allow_failed_runtime_recovery=True,
    )

    assert result["override_applied"] is True
    assert result["reason"] == "FAILED_CONSUMER_FAIL_CLOSED_RECOVERY"


def test_maintenance_recovery_override_rejects_live_safety_expansion() -> None:
    with pytest.raises(RuntimeSafetyViolation, match="복구 허용 밖"):
        _validate_initial_runtime(
            ("ENTRY_LOCKED", "POSITION_OPENED"),
            allow_failed_runtime_recovery=True,
        )


def test_closed_snapshot_transfer_finishes_before_live_release_restart() -> None:
    maintenance = (
        PROJECT_ROOT / "scripts" / "verify_macos_ledger_maintenance.py"
    ).read_text(encoding="utf-8")
    verification = maintenance[
        maintenance.index("def verify_with_maintenance") : maintenance.index(
            "def parse_arguments"
        )
    ]

    clone_at = verification.index("create_closed_ledger_clone(")
    transfer_at = verification.index("transfer_closed_snapshot(")
    restart_at = verification.index("controller.ensure_started()")
    monitor_at = verification.index("monitor.start()")
    integrity_at = verification.index("verify_closed_snapshot(")

    assert clone_at < transfer_at < restart_at < monitor_at < integrity_at


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
    assert 'export PYTHONPATH="$PROJECT_DIR"' in runner
    assert 'import backend' in runner
    assert 'BACKEND_PACKAGE_ROOT' in runner
    assert 'ROBOM_MARKET_ARCHIVE_PATH="$PROJECT_DIR/data/market-parquet-v6"' not in runner


def test_service_runner_pins_backend_import_to_physical_release(tmp_path: Path) -> None:
    release = tmp_path / "release"
    support = tmp_path / "home" / "Library" / "Application Support" / "ROBOM FlowScalper"
    runtime_python = support / "runtime-venv" / "bin" / "python"
    market_archive = tmp_path / "market-archive"
    active_ledger = tmp_path / "active-ledger"
    output = tmp_path / "runner-environment.json"
    (release / "scripts").mkdir(parents=True)
    (release / "frontend" / "dist").mkdir(parents=True)
    (release / "backend").mkdir()
    runtime_python.parent.mkdir(parents=True)
    market_archive.mkdir()
    active_ledger.mkdir()
    (release / "frontend" / "dist" / "index.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    (release / "scripts" / "run_server.py").write_text("# fixture\n", encoding="utf-8")
    (release / "release-manifest.json").write_text(
        json.dumps(
            {
                "commit": "a" * 40,
                "market_archive_path": str(market_archive),
                "active_ledger_dir": str(active_ledger),
            }
        ),
        encoding="utf-8",
    )
    runner = PROJECT_ROOT / "scripts" / "run_macos_service.sh"
    (release / "scripts" / "run_macos_service.sh").write_bytes(runner.read_bytes())
    (release / "scripts" / "run_macos_service.sh").chmod(0o755)
    runtime_python.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import json, os, pathlib, sys",
                "if sys.argv[1] == '-c' and 'json.loads' in sys.argv[2]:",
                "    print(json.loads(pathlib.Path(sys.argv[3]).read_text())[sys.argv[4]])",
                "elif sys.argv[1] == '-c' and 'import backend' in sys.argv[2]:",
                (
                    "    print(pathlib.Path(os.environ.get('PYTHONPATH', "
                    "'/editable/source')) / 'backend')"
                ),
                "else:",
                "    pathlib.Path(os.environ['ROBOM_RUNNER_TEST_OUTPUT']).write_text(json.dumps({",
                "        'pythonpath': os.environ.get('PYTHONPATH'),",
                "        'release_commit': os.environ.get('ROBOM_RELEASE_COMMIT'),",
                "        'release_isolated': os.environ.get('ROBOM_RELEASE_ISOLATED'),",
                "        'real_orders_enabled': os.environ.get('REAL_TRADING', 'false'),",
                "    }))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_python.chmod(0o755)

    subprocess.run(
        ["zsh", str(release / "scripts" / "run_macos_service.sh")],
        check=True,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "ROBOM_RUNNER_TEST_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["pythonpath"] == str(release.resolve())
    assert payload["release_commit"] == "a" * 40
    assert payload["release_isolated"] == "true"
    assert payload["real_orders_enabled"] == "false"


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
