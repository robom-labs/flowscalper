# macOS 자동 복구가 대형 원장의 안전 종료 유예를 보장하는지 검증한다.
"""LaunchAgent 유지관리 계약의 정적 회귀검사다."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from backend.app.api.dashboard import release_identity
from backend.app.storage.integrity import RuntimeSafetyViolation
from scripts.stage_macos_release import (
    _default_runtime_root,
    activate_release,
    prune_obsolete_releases,
    stage_release,
)
from scripts.verify_macos_ledger_maintenance import (
    _require_manual_pause_contract,
    _validate_initial_runtime,
)

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
    assert payload["ProgramArguments"] == ["/bin/zsh", "__RUNNER_SCRIPT__"]
    assert payload["StandardOutPath"] == "__SERVICE_LOG__"
    assert payload["StandardErrorPath"] == "__ERROR_LOG__"


def test_macos_service_keeps_runtime_cache_and_logs_external() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(
        encoding="utf-8"
    )
    runner = (PROJECT_ROOT / "scripts" / "run_macos_service.sh").read_text(encoding="utf-8")
    for source in (installer, runner):
        assert "Library/Application Support/ROBOM FlowScalper" not in source
        assert "Library/Caches/ROBOM_FlowScalper" not in source
    assert (
        'RUNTIME_ROOT="${ROBOM_RUNTIME_ROOT:-$WORKSPACE_MOUNT/05_RUNTIME/ROBOM_FlowScalper}"'
        in installer
    )
    assert 'SUPPORT_DIR="$RUNTIME_ROOT/support"' in installer
    assert 'LOG_DIR="$RUNTIME_ROOT/logs"' in installer
    assert 'PYTHON_BASE="$SUPPORT_DIR/python-base"' in installer
    assert 'ditto "$SOURCE_PYTHON_BASE" "$PYTHON_BASE"' in installer
    assert 'ln -s "$PYTHON_BASE/bin/$PYTHON_BINARY" "$RUNTIME_VENV/bin/python"' in installer
    assert 'CACHE_DIR="$RUNTIME_ROOT/cache"' in runner
    assert 'export PYTHONPYCACHEPREFIX="$CACHE_DIR/python"' in runner
    assert 'export XDG_CACHE_HOME="$CACHE_DIR/xdg"' in runner
    assert 'export UV_CACHE_DIR="$CACHE_DIR/uv"' in runner
    assert 'export TMPDIR="$TMP_DIR/"' in runner
    assert 'RUNNER_SCRIPT="$PROJECT_DIR/scripts/run_macos_service.sh"' in installer
    assert "/usr/bin/osascript" not in installer
    assert '__RUNNER_SCRIPT__' in installer
    assert '__SERVICE_LOG__' in installer
    assert '__ERROR_LOG__' in installer
    assert 'LOG_DIR="$RUNTIME_ROOT/logs"' in runner
    assert 'MAX_LOG_BYTES=10485760' in runner
    assert '/bin/cp -p "$log_file" "$log_file.previous"' in runner
    assert ': > "$log_file"' in runner

    setup = (PROJECT_ROOT / "scripts" / "setup_macos.sh").read_text(encoding="utf-8")
    assert 'export UV_PYTHON_INSTALL_DIR="$CACHE_ROOT/python"' in setup
    assert "uv python install 3.12 --no-bin" in setup
    assert "uv venv --python 3.12 --clear .venv" in setup
    assert "uv sync --python 3.12 --frozen --all-groups" in setup


def test_default_release_runtime_rejects_internal_source() -> None:
    with pytest.raises(RuntimeError, match="외장 볼륨"):
        _default_runtime_root(Path("/tmp/flowscalper"))

    assert _default_runtime_root(Path("/Volumes/ROBOM_FLOWSCALPER/project")) == Path(
        "/Volumes/ROBOM_FLOWSCALPER/05_RUNTIME/ROBOM_FlowScalper"
    )


def test_installer_uses_launchd_graceful_bootout_before_new_bootstrap() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")

    bootout_at = installer.index('launchctl bootout "$SERVICE_TARGET"')
    bootstrap_at = installer.index('launchctl bootstrap "gui/$USER_ID" "$TARGET_PLIST"')
    assert bootout_at < bootstrap_at
    assert 'launchctl kickstart -k "$SERVICE_TARGET"' not in installer


def test_installer_retries_transient_bootstrap_and_keeps_stage_json_clean() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    stage = (PROJECT_ROOT / "scripts" / "stage_macos_release.py").read_text(encoding="utf-8")

    assert "for attempt in 1 2 3" in installer
    assert 'service_pid="$(printf' in installer
    assert "for shutdown_wait in {1..60}" in installer
    assert 'kill -0 "$service_pid"' in installer
    assert 'bootstrap_succeeded="true"' in installer
    assert "LaunchAgent 등록이 3회 연속 실패했습니다" in installer
    assert 'payload["status"] == "ACTIVATED"' in installer
    assert "stdout=sys.stderr" in stage
    assert "stderr=sys.stderr" in stage


def test_installer_reports_pass_only_after_safe_live_dashboard_is_ready() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")

    kickstart_at = installer.index('launchctl kickstart "$SERVICE_TARGET"')
    readiness_at = installer.index("for readiness_wait in {1..180}")
    pruning_at = installer.index("--prune-only")
    pass_at = installer.index('echo "PASS: 자동 실행 서비스 설치 및 안전한 LIVE 준비 완료"')
    assert kickstart_at < readiness_at < pruning_at < pass_at
    readiness = installer[readiness_at:pruning_at]
    assert "http://127.0.0.1:8870/api/dashboard" in readiness
    assert 'system["release_commit"] == expected' in readiness
    assert 'system["release_isolated"] is True' in readiness
    assert 'status["market_data_state"] == "LIVE"' in readiness
    assert 'status["execution_state"] == "PAPER"' in readiness
    assert 'status["real_orders_enabled"] is False' in readiness
    assert 'status["auth_required"] is False' in readiness
    assert 'operation["market_observation_active"] is True' in readiness
    assert 'operation["automatic_recovery"] is True' in readiness
    assert 'float(system["lag_p95_ms"]) <= 500.0' in readiness
    assert 'float(system["trade_lag_p95_ms"]) <= 1000.0' in readiness
    assert 'system["persistence_worker_warmed"] is True' in readiness
    assert 'int(system["persistence_flush_count"]) >= 4' in readiness
    assert 'float(system["persistence_flush_last_ms"]) <= 20000.0' in readiness
    assert 'int(system["persistence_fault_count"]) == 0' in readiness
    assert 'int(system["persistence_buffer_dropped"]) == 0' in readiness
    assert 'system["storage_entry_allowed"] is True' in readiness
    assert "exit 6" in readiness


def test_installer_can_prepare_release_without_restarting_loaded_service() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")

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


def test_maintenance_verified_manual_pause_allows_only_pause_state_codes() -> None:
    result = _validate_initial_runtime(
        ("OPERATION_NOT_RUNNING", "ENTRY_LOCKED"),
        allow_failed_runtime_recovery=False,
        verified_manual_pause=True,
    )

    assert result["override_applied"] is True
    assert result["reason"] == "VERIFIED_USER_ENTRY_PAUSE"
    with pytest.raises(RuntimeSafetyViolation, match="POSITION_OPENED"):
        _validate_initial_runtime(
            ("OPERATION_NOT_RUNNING", "ENTRY_LOCKED", "POSITION_OPENED"),
            allow_failed_runtime_recovery=False,
            verified_manual_pause=True,
        )


def test_maintenance_manual_pause_contract_requires_live_observation() -> None:
    payload = {
        "paper_entry_intent": {
            "state": "USER_PAUSED",
            "manual_pause_requested": True,
        },
        "operation_status": {
            "state": "MANUALLY_PAUSED",
            "market_observation_active": True,
            "paper_entry_active": False,
        },
    }

    _require_manual_pause_contract(payload)
    payload["operation_status"]["market_observation_active"] = False  # type: ignore[index]
    with pytest.raises(RuntimeSafetyViolation, match="시장 관찰"):
        _require_manual_pause_contract(payload)


def test_closed_snapshot_transfer_finishes_before_live_release_restart() -> None:
    maintenance = (PROJECT_ROOT / "scripts" / "verify_macos_ledger_maintenance.py").read_text(
        encoding="utf-8"
    )
    verification = maintenance[
        maintenance.index("def verify_with_maintenance") : maintenance.index("def parse_arguments")
    ]

    clone_at = verification.index("create_closed_ledger_clone(")
    transfer_at = verification.index("transfer_closed_snapshot(")
    restart_at = verification.index("controller.ensure_started()")
    monitor_at = verification.index("monitor.start()")
    integrity_at = verification.index("verify_closed_snapshot(")

    assert clone_at < transfer_at < restart_at < monitor_at < integrity_at


def test_service_uses_immutable_current_release_and_manifest_paths() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    runner = (PROJECT_ROOT / "scripts" / "run_macos_service.sh").read_text(encoding="utf-8")

    assert "scripts/stage_macos_release.py" in installer
    assert '--activate > "$STAGE_RESULT"' in installer
    assert 'PROJECT_DIR="$RUNTIME_ROOT/current"' in installer
    assert 'RELEASE_MANIFEST="$PROJECT_DIR/release-manifest.json"' in runner
    assert 'export ROBOM_RELEASE_COMMIT="$RELEASE_COMMIT"' in runner
    assert 'export ROBOM_RELEASE_ISOLATED="true"' in runner
    assert 'export ROBOM_MARKET_ARCHIVE_PATH="$MARKET_ARCHIVE_PATH"' in runner
    assert 'export PYTHONPATH="$PROJECT_DIR"' in runner
    assert "import backend" in runner
    assert "BACKEND_PACKAGE_ROOT" in runner
    assert 'ROBOM_MARKET_ARCHIVE_PATH="$PROJECT_DIR/data/market-parquet-v6"' not in runner


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="실제 /bin/zsh LaunchAgent runner 계약은 macOS에서 검증한다.",
)
def test_service_runner_pins_backend_import_to_physical_release(tmp_path: Path) -> None:
    release = tmp_path / "release"
    runtime_root = tmp_path / "runtime"
    support = runtime_root / "support"
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
    (release / "frontend" / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
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
                "        'python_cache': os.environ.get('PYTHONPYCACHEPREFIX'),",
                "        'tmpdir': os.environ.get('TMPDIR'),",
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
            "ROBOM_RUNTIME_ROOT": str(runtime_root),
            "ROBOM_RUNTIME_PYTHON": str(runtime_python),
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
    assert payload["python_cache"] == str(runtime_root / "cache" / "python")
    assert payload["tmpdir"] == f"{runtime_root / 'tmp'}/"


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
    assert (
        json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))[
            "real_orders_enabled"
        ]
        is False
    )


def test_release_archive_temp_file_stays_on_runtime_volume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, runtime, market_archive, ledger = _release_fixture(tmp_path)
    observed_directories: list[Path | None] = []
    real_named_temporary_file = tempfile.NamedTemporaryFile

    def observed_named_temporary_file(*args: object, **kwargs: object) -> object:
        directory = kwargs.get("dir")
        observed_directories.append(Path(directory) if directory is not None else None)
        return real_named_temporary_file(*args, **kwargs)

    monkeypatch.setattr(
        "scripts.stage_macos_release.tempfile.NamedTemporaryFile",
        observed_named_temporary_file,
    )

    stage_release(
        source,
        runtime,
        market_archive,
        ledger,
        build_frontend=False,
        prebuilt_frontend_dist=source / "frontend" / "dist",
    )

    assert observed_directories == [(runtime / "releases").resolve()]


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


def test_release_retention_keeps_current_and_one_verified_rollback(tmp_path: Path) -> None:
    source, runtime, market_archive, ledger = _release_fixture(tmp_path)
    release_paths: list[Path] = []
    for revision in range(1, 4):
        if revision > 1:
            (source / "backend.py").write_text(
                f"RELEASE = {revision}\n",
                encoding="utf-8",
            )
            _git(source, "add", ".")
            _git(source, "commit", "-m", f"fixture release {revision}")
        manifest = stage_release(
            source,
            runtime,
            market_archive,
            ledger,
            build_frontend=False,
            prebuilt_frontend_dist=source / "frontend" / "dist",
        )
        release_path = Path(str(manifest["release_path"]))
        release_paths.append(release_path)
        activate_release(runtime, release_path)

    unknown = runtime / "releases" / "operator-notes"
    unknown.mkdir()
    result = prune_obsolete_releases(runtime)

    assert result["status"] == "PASS"
    assert not release_paths[0].exists()
    assert release_paths[1].is_dir()
    assert release_paths[2].is_dir()
    assert unknown.is_dir()
    assert result["pruned_releases"] == [str(release_paths[0])]
    assert str(unknown) in result["skipped_paths"]
