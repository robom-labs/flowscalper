# macOS 자동 복구가 대형 원장의 안전 종료 유예를 보장하는지 검증한다.
"""LaunchAgent 유지관리 계약의 정적 회귀검사다."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from backend.app.api.dashboard import release_identity
from backend.app.storage.integrity import RuntimeSafetyViolation
from scripts import stage_macos_release as stage_macos_release_module
from scripts import verify_compatibility_runtime_preflight as legacy_preflight_module
from scripts.stage_macos_release import (
    _default_runtime_root,
    _verify_release_tree,
    activate_release,
    current_release,
    legacy_runtime_safety_fields_missing,
    migrate_legacy_release_manifest,
    prune_obsolete_releases,
    stage_release,
)
from scripts.verify_compatibility_runtime_preflight import (
    LegacyRuntimePreflightError,
    _parse_lsof_records,
    _read_json_object,
    _require_stable_process_binding,
    _verify_dashboard,
    _verify_ledger,
    verify_running_process_binding,
    verify_stopped_process_binding,
)
from scripts.verify_macos_ledger_maintenance import (
    _require_manual_pause_contract,
    _validate_initial_runtime,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _dashboard_install_contract_source() -> str:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    contract = installer[
        installer.index("dashboard_matches_install_contract()") : installer.index(
            "rollback_previous_release()"
        )
    ]
    source_marker = "    'import json,math,sys\n"
    source_start = contract.index(source_marker) + len("    '")
    source_end = contract.index('\' \\\n    "$expected_commit"', source_start)
    return contract[source_start:source_end]


def _safe_install_dashboard() -> dict[str, object]:
    return {
        "status": {
            "run_id": "run-v6-install-contract",
            "market_data_state": "LIVE",
            "execution_state": "PAPER",
            "real_orders_enabled": False,
            "auth_required": False,
        },
        "system": {
            "release_commit": "a" * 40,
            "release_isolated": True,
            "funding_readiness": "NOT_READY",
            "auth_headers": False,
            "private_api_enabled": False,
            "api_key_enabled": False,
            "wallet_enabled": False,
            "runtime_ai_order_decision_enabled": False,
            "lag_p95_ms": 1.0,
            "trade_lag_p95_ms": 1.0,
            "persistence_worker_warmed": True,
            "persistence_flush_count": 4,
            "persistence_flush_last_ms": 1.0,
            "persistence_flush_last_completed_ts_ms": 1_000,
            "persistence_fault_count": 0,
            "persistence_fault_active": False,
            "persistence_fault_recoverable": False,
            "persistence_recovery_count": 0,
            "persistence_last_error": "NONE",
            "persistence_last_recovered_ts_ms": None,
            "persistence_buffer_dropped": 0,
            "storage_entry_allowed": True,
        },
        "risk": {"paper_only": True},
        "operation_status": {
            "state": "MANUALLY_PAUSED",
            "market_observation_active": True,
            "paper_entry_active": False,
            "automatic_recovery": False,
        },
        "paper_entry_intent": {
            "state": "USER_PAUSED",
            "manual_pause_requested": True,
            "revision": 7,
        },
        "paused": True,
        "position": None,
        "focus_positions": [],
        "league_positions": [],
        "main_pending_entry_count": 0,
        "league_pending_entry_count": 0,
        "total_pending_entry_count": 0,
        "total_open_position_count": 0,
        "paper_portfolio_flat": True,
    }


def _run_dashboard_install_contract(
    payload: dict[str, object],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _dashboard_install_contract_source(),
            "a" * 40,
            "true",
            "false",
            "run-v6-install-contract",
            "USER_PAUSED",
            "7",
        ],
        input=json.dumps(payload),
        env={**os.environ, "PYTHONOPTIMIZE": "1"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


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
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
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
    assert 'TRUSTED_RUNNER_SCRIPT="$SUPPORT_DIR/run_macos_service.sh"' in installer
    assert 'write_launch_agent_plist "$TRUSTED_RUNNER_SCRIPT"' in installer
    assert "/usr/bin/osascript" not in installer
    assert "__RUNNER_SCRIPT__" in installer
    assert "__SERVICE_LOG__" in installer
    assert "__ERROR_LOG__" in installer
    assert 'LOG_DIR="$RUNTIME_ROOT/logs"' in runner
    assert "MAX_LOG_BYTES=10485760" in runner
    assert '/bin/cp -p "$log_file" "$log_file.previous"' in runner
    assert ': > "$log_file"' in runner

    setup = (PROJECT_ROOT / "scripts" / "setup_macos.sh").read_text(encoding="utf-8")
    assert 'export UV_PYTHON_INSTALL_DIR="$CACHE_ROOT/python"' in setup
    assert "uv python install 3.12 --no-bin" in setup
    assert "uv venv --python 3.12 --clear .venv" in setup
    assert "uv sync --python 3.12 --frozen --all-groups" in setup


def test_make_targets_keep_local_validation_cache_external_when_mounted() -> None:
    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "ROBOM_EXTERNAL_VOLUME ?= /Volumes/ROBOM_FLOWSCALPER" in makefile
    assert (
        "ROBOM_EXTERNAL_CACHE_ROOT ?= $(ROBOM_EXTERNAL_VOLUME)/03_CACHES/ROBOM_FlowScalper"
    ) in makefile
    for variable, relative_path in (
        ("UV_CACHE_DIR", "uv"),
        ("UV_PYTHON_INSTALL_DIR", "python"),
        ("XDG_CACHE_HOME", "xdg"),
        ("TMPDIR", "tmp/"),
        ("NPM_CONFIG_CACHE", "npm"),
        ("PLAYWRIGHT_BROWSERS_PATH", "playwright"),
    ):
        assert f"export {variable} := $(ROBOM_EXTERNAL_CACHE_ROOT)/{relative_path}" in makefile
    assert (
        "PNPM_INSTALL_STORE_ARG := --store-dir $(ROBOM_EXTERNAL_CACHE_ROOT)/pnpm-store"
    ) in makefile
    assert "/sbin/mount | /usr/bin/grep -Fq" in makefile
    for target in (
        "setup",
        "dev",
        "run",
        "test",
        "lint",
        "typecheck",
        "build",
        "network-smoke",
        "security-scan",
        "repo-hygiene",
        "regression-contracts",
    ):
        assert f"{target}: | external-cache-prepare" in makefile


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
    assert 'payload.get("status") != "STAGED"' in installer
    assert "stdout=sys.stderr" in stage
    assert "stderr=sys.stderr" in stage


def test_installer_reports_pass_only_after_safe_live_dashboard_is_ready() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")

    main_install_at = installer.index("\nif ! stop_loaded_service; then")
    kickstart_at = installer.index('launchctl kickstart "$SERVICE_TARGET"', main_install_at)
    readiness_at = installer.index("for readiness_wait in {1..180}")
    pruning_at = installer.index("--prune-only")
    pass_at = installer.index('echo "PASS: 자동 실행 서비스 설치 및 안전한 LIVE 준비 완료"')
    assert kickstart_at < readiness_at < pruning_at < pass_at
    readiness = installer[main_install_at:pruning_at]
    contract_at = installer.index("dashboard_matches_install_contract()")
    rollback_at = installer.index("rollback_previous_release()")
    contract = installer[contract_at:rollback_at]
    assert "http://127.0.0.1:8870/api/dashboard" in readiness
    assert 'system["release_commit"] == expected_commit' in contract
    assert 'system["release_isolated"] is True' in contract
    assert 'status["market_data_state"] == "LIVE"' in contract
    assert 'status["execution_state"] == "PAPER"' in contract
    assert 'status["real_orders_enabled"] is False' in contract
    assert 'status["auth_required"] is False' in contract
    assert 'operation["market_observation_active"] is True' in contract
    assert "type(value) in (int, float) and math.isfinite(value)" in contract
    assert '("lag_p95_ms", 500.0)' in contract
    assert '("trade_lag_p95_ms", 1000.0)' in contract
    assert '("persistence_flush_last_ms", 20000.0)' in contract
    assert "type(flush_count) is int and flush_count >= 4" in contract
    assert "type(fault_count) is int and fault_count >= 0" in contract
    assert "recovery_count == fault_count" in contract
    assert "type(dropped_count) is int and dropped_count >= 0" in contract
    assert 'system.get("persistence_fault_active") is False' in contract
    assert 'system.get("persistence_fault_recoverable") is False' in contract
    assert 'system.get("persistence_last_error") == "NONE"' in contract
    assert 'system.get("persistence_last_recovered_ts_ms")' in contract
    assert 'system.get("persistence_flush_last_completed_ts_ms")' in contract
    assert 'system.get("persistence_worker_warmed") is True' in contract
    assert 'system.get("storage_entry_allowed") is True' in contract
    assert 'rollback_previous_release "READINESS_FAILED"' in readiness
    assert "exit 6" in readiness


def test_installer_can_prepare_release_without_restarting_loaded_service() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")

    assert '--prepare-only)' in installer
    assert 'PREPARE_ONLY="true"' in installer
    prepare_only_at = installer.index('if [[ "$PREPARE_ONLY" == "true" ]]')
    bootout_at = installer.index('launchctl bootout "$SERVICE_TARGET"')
    assert prepare_only_at < bootout_at
    assert "exit 0" in installer[prepare_only_at:bootout_at]


def test_installer_shell_syntax_is_valid() -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh가 없는 Linux CI에서는 macOS installer 구문검사를 실행하지 않는다.")
    subprocess.run(
        [zsh, "-n", str(PROJECT_ROOT / "scripts" / "install_macos_service.sh")],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_installer_safety_checks_do_not_use_optimizable_assertions() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")

    assert not any(line.lstrip().startswith("assert ") for line in installer.splitlines())
    safe = _safe_install_dashboard()
    assert _run_dashboard_install_contract(safe).returncode == 0

    main_pending = _safe_install_dashboard()
    main_pending["main_pending_entry_count"] = 1
    main_pending["total_pending_entry_count"] = 1
    pending_result = _run_dashboard_install_contract(main_pending)
    assert pending_result.returncode != 0
    assert "main pending entry" in pending_result.stderr

    funding_ready = _safe_install_dashboard()
    funding_ready["system"]["funding_readiness"] = "READY"  # type: ignore[index]
    funding_result = _run_dashboard_install_contract(funding_ready)
    assert funding_result.returncode != 0
    assert "funding readiness" in funding_result.stderr

    non_paper = _safe_install_dashboard()
    non_paper["risk"]["paper_only"] = False  # type: ignore[index]
    paper_result = _run_dashboard_install_contract(non_paper)
    assert paper_result.returncode != 0
    assert "PAPER only" in paper_result.stderr


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("main_pending_entry_count", "0"),
        ("league_pending_entry_count", False),
        ("total_pending_entry_count", 0.0),
        ("total_open_position_count", "0"),
        ("paper_portfolio_flat", 1),
    ],
)
def test_installer_dashboard_flat_contract_rejects_type_coercion(
    field: str,
    unsafe_value: object,
) -> None:
    payload = _safe_install_dashboard()
    payload[field] = unsafe_value

    assert _run_dashboard_install_contract(payload).returncode != 0


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("lag_p95_ms", "-Infinity"),
        ("trade_lag_p95_ms", False),
        ("persistence_flush_count", "4"),
        ("persistence_flush_last_ms", "-Infinity"),
        ("persistence_fault_count", "0"),
        ("persistence_buffer_dropped", False),
    ],
)
def test_installer_dashboard_health_contract_rejects_type_and_infinity_bypasses(
    field: str,
    unsafe_value: object,
) -> None:
    payload = _safe_install_dashboard()
    payload["system"][field] = unsafe_value  # type: ignore[index]

    assert _run_dashboard_install_contract(payload).returncode != 0


def test_installer_dashboard_health_contract_allows_fully_recovered_history() -> None:
    payload = _safe_install_dashboard()
    payload["system"].update(  # type: ignore[union-attr]
        {
            "persistence_fault_count": 6,
            "persistence_fault_active": False,
            "persistence_fault_recoverable": False,
            "persistence_recovery_count": 6,
            "persistence_last_error": "NONE",
            "persistence_last_recovered_ts_ms": 8_000,
            "persistence_flush_last_completed_ts_ms": 9_000,
            "persistence_buffer_dropped": 5_104,
        }
    )

    result = _run_dashboard_install_contract(payload)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("persistence_fault_active", True),
        ("persistence_recovery_count", 5),
        ("persistence_last_error", "StoragePressureError"),
        ("persistence_flush_last_completed_ts_ms", 7_999),
    ],
)
def test_installer_dashboard_health_contract_rejects_active_or_unrecovered_fault(
    field: str,
    unsafe_value: object,
) -> None:
    payload = _safe_install_dashboard()
    payload["system"].update(  # type: ignore[union-attr]
        {
            "persistence_fault_count": 6,
            "persistence_fault_active": False,
            "persistence_fault_recoverable": False,
            "persistence_recovery_count": 6,
            "persistence_last_error": "NONE",
            "persistence_last_recovered_ts_ms": 8_000,
            "persistence_flush_last_completed_ts_ms": 9_000,
            "persistence_buffer_dropped": 5_104,
            field: unsafe_value,
        }
    )

    assert _run_dashboard_install_contract(payload).returncode != 0


def test_preflight_and_readiness_share_strict_flat_funding_contract() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    preflight = installer[
        installer.index('> "$PREFLIGHT_DASHBOARD"') : installer.index(
            'STAGE_RESULT="$SUPPORT_DIR/latest-release-stage.json"'
        )
    ]
    readiness = installer[
        installer.index("dashboard_matches_install_contract()") : installer.index(
            "rollback_previous_release()"
        )
    ]

    for contract in (preflight, readiness):
        assert "type(value) is int and value >= 0" in contract
        assert '"main_pending_entry_count"' in contract
        assert '"league_pending_entry_count"' in contract
        assert '"total_pending_entry_count"' in contract
        assert '"total_open_position_count"' in contract
        assert 'payload.get("paper_portfolio_flat") is True' in contract
        assert 'risk["paper_only"] is True' in contract
        assert "math.isfinite(value)" in contract
        assert "type(value) in (int, float)" in contract
        assert "type(flush_count) is int and flush_count >= 4" in contract
        assert "type(fault_count) is int and fault_count >= 0" in contract
        assert "recovery_count == fault_count" in contract
        assert "type(dropped_count) is int and dropped_count >= 0" in contract
        assert 'system.get("persistence_fault_active") is False' in contract
        assert 'system.get("persistence_fault_recoverable") is False' in contract
        assert 'system.get("persistence_last_error") == "NONE"' in contract
        assert 'system.get("persistence_last_recovered_ts_ms")' in contract
        assert 'system.get("persistence_flush_last_completed_ts_ms")' in contract
    assert 'system.get("funding_readiness") == "NOT_READY"' in preflight
    assert 'system["funding_readiness"] == "NOT_READY"' in readiness


def test_installer_preflight_is_saved_before_activation_and_first_install_is_exact() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")

    preflight_at = installer.index('> "$PREFLIGHT_DASHBOARD"')
    stage_at = installer.index(
        '--active-ledger-dir "$RUNTIME_ROOT/active-ledger" > "$STAGE_RESULT"'
    )
    prestop_at = installer.index("if ! verify_loaded_service_unchanged_before_stop")
    stop_at = installer.index("if ! stop_loaded_service", prestop_at)
    activate_at = installer.index("if ! activate_staged_release", stop_at)
    assert preflight_at < stage_at < prestop_at < stop_at < activate_at
    assert '[[ "$HAD_CURRENT" == "false" && "$HAD_JOB" == "false" ]]' in installer
    assert '[[ "$HAD_CURRENT" != "true" || "$HAD_JOB" != "true" ]]' in installer
    preflight = installer[preflight_at:stage_at]
    assert 'status["market_data_state"] == "LIVE"' in preflight
    assert 'status["execution_state"] == "PAPER"' in preflight
    assert 'status["real_orders_enabled"] is False' in preflight
    assert 'status["auth_required"] is False' in preflight
    assert 'risk["paper_only"] is True' in preflight
    assert 'operation["state"] == "MANUALLY_PAUSED"' in preflight
    assert 'operation["paper_entry_active"] is False' in preflight
    assert 'intent["manual_pause_requested"] is True' in preflight
    assert 'payload.get("position") is None' in preflight
    assert 'payload.get("focus_positions") == []' in preflight
    assert 'payload.get("league_positions") == []' in preflight
    assert "migrate_legacy_release_manifest(runtime_root, previous_release)" not in preflight
    assert "verify_legacy_runtime_preflight(" in preflight
    assert 'system["release_commit"] == manifest["commit"]' in preflight
    assert "legacy_runtime_safety_fields_missing(original_manifest, system)" in preflight
    identity_check_at = preflight.index(
        'require(system["release_commit"] == original_manifest.get("commit")'
    )
    helper_at = preflight.index("verify_legacy_runtime_preflight(")
    assert identity_check_at < helper_at
    assert preflight.index('require(system["release_isolated"] is True') < helper_at
    assert '"legacy_runtime_safety_fields_missing": bool(legacy_missing_fields)' in preflight
    assert '"legacy_runtime_safety_missing_fields": list(legacy_missing_fields)' in preflight
    offline_at = installer.index("if ! verify_legacy_offline_after_stop", stop_at)
    prepare_at = installer.index("if ! prepare_previous_release_for_rollback", offline_at)
    assert "migrate_legacy_release_manifest(runtime_root, previous_release)" in installer
    assert offline_at < prepare_at < activate_at


def test_installer_preserves_run_pause_revision_and_flat_state_after_restart() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    contract = installer[
        installer.index("dashboard_matches_install_contract()") : installer.index(
            "rollback_previous_release()"
        )
    ]

    assert 'status["run_id"] == expected_run' in contract
    assert 'intent["state"] == expected_pause_state' in contract
    assert 'intent["revision"] == int(expected_revision)' in contract
    assert 'payload["paused"] is True' in contract
    assert 'operation["state"] == "MANUALLY_PAUSED"' in contract
    assert 'operation["automatic_recovery"] is False' in contract
    assert 'payload.get("position") is None' in contract
    assert 'payload.get("focus_positions") == []' in contract
    assert 'payload.get("league_positions") == []' in contract
    assert 'system["auth_headers"] is False' in contract
    assert "if field not in system:" in contract
    assert 'require(allow_legacy_missing == "true"' in contract
    assert "require(system[field] is False" in contract
    assert 'dashboard_matches_install_contract "$EXPECTED_RELEASE_COMMIT"' in installer
    assert '"$PRESERVE_EXISTING_IDENTITY" "false"' in installer


def test_installer_rechecks_pause_revision_and_flat_contract_immediately_before_stop() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    function_at = installer.index("verify_loaded_service_unchanged_before_stop()")
    check_at = installer.index("if ! verify_loaded_service_unchanged_before_stop;")
    stop_at = installer.index("if ! stop_loaded_service;", check_at)
    pre_stop = installer[function_at:stop_at]

    assert function_at < check_at < stop_at
    assert "latest-install-prestop-dashboard.json" in pre_stop
    assert "http://127.0.0.1:8870/api/dashboard" in pre_stop
    assert 'dashboard_matches_install_contract "$PREVIOUS_RELEASE_COMMIT" "true"' in pre_stop
    assert "verify_persistence_counters_unchanged()" in installer
    assert '"$PREFLIGHT_DASHBOARD" "$prestop_dashboard"' in pre_stop
    assert '"$prestop_dashboard" "$bracket_dashboard"' in pre_stop
    assert 'abort_before_transition "PRESTOP_CONTRACT_CHANGED" 4' in pre_stop


def test_installer_rollback_uses_stage_target_and_revalidates_previous_dashboard() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    rollback = installer[
        installer.index("rollback_previous_release()") : installer.index(
            "\nif ! stop_loaded_service; then"
        )
    ]

    assert '["release"].get("rollback_release")' in installer
    assert '[[ "$ROLLBACK_RELEASE" != "$PREVIOUS_RELEASE" ]]' in installer
    assert "from scripts.stage_macos_release import activate_release" in rollback
    assert 'actor="CODEX_DEPLOY_ROLLBACK"' in rollback
    assert "verify_service_fully_stopped" in rollback
    assert 'write_text_file_atomic "$rollback_dashboard_file" "$rollback_dashboard"' in rollback
    assert "printf '%s\\n' \"$rollback_dashboard\"" not in rollback
    assert 'dashboard_matches_install_contract "$PREVIOUS_RELEASE_COMMIT" "true"' in rollback
    assert '"$LEGACY_RUNTIME_COMPATIBILITY"' in rollback
    assert "verify_legacy_runtime_contract_file" in rollback
    assert "for rollback_readiness_wait in {1..180}" in rollback
    assert 'rollback_previous_release "BOOTSTRAP_FAILED"' in installer
    assert 'rollback_previous_release "KICKSTART_FAILED"' in installer
    assert 'rollback_previous_release "READINESS_FAILED"' in installer


def test_installer_unverified_service_failures_leave_service_fail_closed() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    cleanup = installer[
        installer.index("fail_closed_unverified_service()") : installer.index(
            "rollback_previous_release()"
        )
    ]
    rollback = installer[
        installer.index("rollback_previous_release()") : installer.index(
            "\nif ! verify_loaded_service_unchanged_before_stop;"
        )
    ]

    assert "stop_loaded_service" in cleanup
    assert '/bin/launchctl bootout "$SERVICE_TARGET"' in cleanup
    assert "verify_service_fully_stopped" in cleanup
    assert 'UNVERIFIED_SERVICE_MAY_BE_RUNNING="false"' in cleanup
    for failure_reason in (
        "ROLLBACK_BOOTSTRAP_FAILED",
        "ROLLBACK_KICKSTART_FAILED",
        "ROLLBACK_EVIDENCE_WRITE_FAILED",
        "ROLLBACK_READINESS_FAILED",
    ):
        assert f'fail_closed_unverified_service "{failure_reason}"' in rollback


def test_installer_exit_trap_stops_any_readiness_unverified_service() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    exit_contract = installer[
        installer.index("UNVERIFIED_SERVICE_MAY_BE_RUNNING=") : installer.index(
            "write_text_file_atomic()"
        )
    ]
    rollback = installer[
        installer.index("rollback_previous_release()") : installer.index(
            "\nif ! verify_loaded_service_unchanged_before_stop;"
        )
    ]
    main_start = installer[installer.index("\nif ! verify_loaded_service_unchanged_before_stop;") :]

    assert "handle_install_exit()" in exit_contract
    assert exit_contract.index("set +e") < exit_contract.index(
        "설치 종료 시점에 readiness 미증명 서비스가 있어"
    )
    assert "trap 'handle_install_exit $?' EXIT" in exit_contract
    assert 'fail_closed_unverified_service "INSTALL_EXIT_${exit_code}"' in exit_contract
    assert "exit_code=70" in exit_contract
    rollback_marked_at = rollback.index('UNVERIFIED_SERVICE_MAY_BE_RUNNING="true"')
    rollback_bootstrap_at = rollback.index("if ! bootstrap_launch_agent", rollback_marked_at)
    rollback_readiness_failure_at = rollback.index(
        'if [[ "$rollback_ready" != "true" ]]', rollback_bootstrap_at
    )
    rollback_cleared_at = rollback.index(
        'UNVERIFIED_SERVICE_MAY_BE_RUNNING="false"', rollback_bootstrap_at
    )
    assert (
        rollback_marked_at
        < rollback_bootstrap_at
        < rollback_readiness_failure_at
        < rollback_cleared_at
    )
    main_marked_at = main_start.index('UNVERIFIED_SERVICE_MAY_BE_RUNNING="true"')
    main_bootstrap_at = main_start.index("if ! bootstrap_launch_agent", main_marked_at)
    main_readiness_failure_at = main_start.index(
        'if [[ "$service_ready" != "true" ]]', main_bootstrap_at
    )
    main_cleared_at = main_start.index(
        'UNVERIFIED_SERVICE_MAY_BE_RUNNING="false"', main_bootstrap_at
    )
    assert main_marked_at < main_bootstrap_at < main_readiness_failure_at < main_cleared_at


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="실제 /bin/zsh EXIT handler의 닫힌 stderr 동작은 macOS에서 검증한다.",
)
@pytest.mark.parametrize(("cleanup_status", "expected_exit"), [(0, 143), (1, 70)])
def test_installer_exit_handler_cleans_up_with_closed_stderr(
    cleanup_status: int,
    expected_exit: int,
) -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    handler = installer[
        installer.index("handle_install_exit()") : installer.index("handle_install_signal()")
    ]
    harness = f"""
set -e
UNVERIFIED_SERVICE_MAY_BE_RUNNING=true
release_install_lock() {{ print -r -- RELEASED >&3; }}
fail_closed_unverified_service() {{ print -r -- CLEANUP >&3; return {cleanup_status}; }}
{handler}
exec 3>&1
exec 2>&-
handle_install_exit 143
"""
    result = subprocess.run(
        ["/bin/zsh", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == expected_exit
    assert result.stdout.splitlines() == ["CLEANUP", "RELEASED"]


def test_installer_signal_traps_exit_and_atomic_writer_checks_failures_explicitly() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    lock_contract = installer[
        installer.index("release_install_lock()") : installer.index("SOURCE_PYTHON=")
    ]

    assert "trap release_install_lock EXIT HUP INT TERM" not in lock_contract
    assert "trap 'handle_install_exit $?' EXIT" in lock_contract
    assert "trap 'handle_install_signal 129' HUP" in lock_contract
    assert "trap 'handle_install_signal 130' INT" in lock_contract
    assert "trap 'handle_install_signal 143' TERM" in lock_contract
    assert "trap '' EXIT HUP INT TERM" in lock_contract
    assert "trap - EXIT\n" not in lock_contract
    assert 'exit "$exit_code"' in lock_contract
    assert 'if ! (umask 077; printf \'%s\\n\' "$payload" > "$temporary")' in lock_contract
    assert 'if ! /bin/mv -f "$temporary" "$target"; then' in lock_contract


def test_shutdown_or_offline_failure_never_activates_or_bootstraps_an_unproven_release() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    shutdown_failure = installer[
        installer.index("\nif ! stop_loaded_service; then") : installer.index(
            "\nif ! prepare_previous_release_for_rollback; then"
        )
    ]

    assert "activate_staged_release" not in shutdown_failure
    assert "bootstrap_launch_agent" not in shutdown_failure
    assert "rollback_previous_release" not in shutdown_failure
    assert "verify_service_fully_stopped" in shutdown_failure
    assert "verify_legacy_offline_after_stop" in shutdown_failure
    assert "서비스를 재시작하지 않습니다" in shutdown_failure


def test_installer_proves_exact_launchctl_absence_before_and_after_lsof() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    stopped = installer[
        installer.index("verify_service_fully_stopped()") : installer.index(
            "write_launch_agent_plist()"
        )
    ]

    assert "local launchctl_status=0" in stopped
    assert "local status=" not in stopped
    assert 'if (( launchctl_status != 113 )) || [[ "$output" != "$expected" ]]' in stopped
    assert 'Could not find service \\"$LABEL\\" in domain for user gui: $USER_ID' in stopped
    assert stopped.count("verify_launch_agent_absent_exact") == 3
    assert 'verify_launch_agent_absent_exact "초기"' in stopped
    assert 'verify_launch_agent_absent_exact "최종"' in stopped
    initial_absence_at = stopped.index('verify_launch_agent_absent_exact "초기"')
    lsof_self_probe_at = stopped.index('/usr/sbin/lsof -nP -p "$$"')
    listener_probe_at = stopped.index("/usr/sbin/lsof -nP -iTCP:8870 -sTCP:LISTEN")
    final_absence_at = stopped.index('verify_launch_agent_absent_exact "최종"')
    assert initial_absence_at < lsof_self_probe_at < listener_probe_at < final_absence_at


def test_first_install_prepares_active_ledger_and_failure_restores_none_state() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    stage_at = installer.index("STAGE_RESULT=")
    runtime_prepare = installer[:stage_at]
    cleanup = installer[
        installer.index("cleanup_failed_first_install()") : installer.index(
            "fail_closed_unverified_service()"
        )
    ]
    rollback = installer[
        installer.index("rollback_previous_release()") : installer.index(
            "\nif ! stop_loaded_service; then"
        )
    ]

    assert 'ACTIVE_LEDGER_DIR="$RUNTIME_ROOT/active-ledger"' in runtime_prepare
    assert (
        'for runtime_directory in "$SUPPORT_DIR" "$LOG_DIR" "$CACHE_DIR" "$ACTIVE_LEDGER_DIR"'
    ) in runtime_prepare
    assert '[[ -L "$runtime_directory"' in runtime_prepare
    assert 'pwd -P)" != "$runtime_directory"' in runtime_prepare
    for artifact in (
        '"$TARGET_PLIST"',
        '"$TRUSTED_RUNNER_SCRIPT"',
        '"$RELEASE_INTEGRITY_ANCHOR"',
        '"$RUNTIME_ROOT/current-deployment.json"',
    ):
        assert artifact in runtime_prepare
        assert artifact in cleanup
    assert "verify_service_fully_stopped" in cleanup
    assert 'pwd -P)" != "$VERIFIER_RELEASE"' in cleanup
    assert 'payload.get("previous_state") != "NONE"' in cleanup
    assert 'if ! /bin/unlink "$generated_artifact"; then' in cleanup
    assert cleanup.count("verify_service_fully_stopped") == 2
    assert "artifact가 정리 뒤에도 남아" in cleanup
    assert "cleanup_failed_first_install" in rollback
    assert "NONE 실행 상태로 fail-closed 복구" in rollback


def test_prepare_only_stages_without_mutating_loaded_release_or_runtime_artifacts() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    prepare = installer[
        installer.index('if [[ "$PREPARE_ONLY" == "true" ]]') : installer.index(
            "stop_loaded_service()"
        )
    ]

    assert "restore_previous_release_before_restart" not in prepare
    assert "activate_staged_release" not in prepare
    assert "install_transition_artifacts" not in prepare
    assert "write_release_integrity_anchor" not in prepare
    assert 'launchctl bootout "$SERVICE_TARGET"' not in prepare


def test_every_post_activation_failure_uses_verified_rollback_path() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    activation_at = installer.index("if ! activate_staged_release")
    post_activation = installer[activation_at:]

    for failure_reason in (
        "POSTSTOP_ACTIVATION_FAILED",
        "TRANSITION_ARTIFACT_INSTALL_FAILED",
        "BOOTSTRAP_FAILED",
        "KICKSTART_FAILED",
        "READINESS_FAILED",
    ):
        assert f'rollback_previous_release "{failure_reason}"' in post_activation
    rollback = installer[installer.index("rollback_previous_release()") : activation_at]
    assert 'write_release_integrity_anchor "$ROLLBACK_RELEASE" "$VERIFIER_RELEASE"' in rollback
    assert 'write_launch_agent_plist "$TRUSTED_RUNNER_SCRIPT"' in rollback
    assert "verify_legacy_runtime_contract_file" in rollback


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
    assert '--active-ledger-dir "$RUNTIME_ROOT/active-ledger" > "$STAGE_RESULT"' in installer
    assert 'PROJECT_DIR="$RUNTIME_ROOT/current"' in installer
    assert 'RELEASE_INTEGRITY_ANCHOR="$SUPPORT_DIR/current-release-integrity.json"' in installer
    assert 'write_release_integrity_anchor "$VERIFIER_RELEASE" "$VERIFIER_RELEASE"' in installer
    assert 'install_trusted_runner_from_release "$VERIFIER_RELEASE"' in installer
    assert 'write_launch_agent_plist "$TRUSTED_RUNNER_SCRIPT"' in installer
    assert 'RELEASE_MANIFEST="$PROJECT_DIR/release-manifest.json"' in runner
    assert "RELEASE_INTEGRITY_ANCHOR=" in runner
    assert 'export ROBOM_RELEASE_COMMIT="$RELEASE_COMMIT"' in runner
    assert 'export ROBOM_RELEASE_ISOLATED="true"' in runner
    assert 'export ROBOM_MARKET_ARCHIVE_PATH="$MARKET_ARCHIVE_PATH"' in runner
    assert 'export PYTHONPATH="$PROJECT_DIR"' in runner
    assert '"$RUNTIME_PYTHON" -I -P - "$PROJECT_DIR" "$RELEASE_MANIFEST"' in runner
    assert runner.count('"$RUNTIME_PYTHON" -I -P -c "$MANIFEST_VALUE"') == 3
    assert '"$RUNTIME_PYTHON" -c' not in runner
    assert "$RUNTIME_PYTHON -c" not in runner
    assert "import backend" in runner
    assert "BACKEND_PACKAGE_ROOT" in runner
    assert 'ROBOM_MARKET_ARCHIVE_PATH="$PROJECT_DIR/data/market-parquet-v6"' not in runner
    verify_at = runner.index(
        "target_commit, target_manifest_sha, target_manifest = verify_release_tree("
    )
    manifest_read_at = runner.index("MANIFEST_VALUE=")
    backend_import_at = runner.index("BACKEND_PACKAGE_ROOT=")
    assert verify_at < manifest_read_at < backend_import_at
    assert "actual_files == normalized_expected" in runner
    assert '"launcher_source_manifest_sha256"' in runner
    assert 'anchor.get("launcher_sha256") == launcher_sha == source_runner_sha' in runner
    assert "from scripts.stage_macos_release" not in runner
    assert "verify_active_ledger_binding()" in runner
    assert '[[ -L "$DEFAULT_ACTIVE_LEDGER_DIR"' in runner
    assert '"$DEFAULT_ACTIVE_LEDGER_DIR/run-ledger.sqlite3-wal"' in runner
    assert '"$DEFAULT_ACTIVE_LEDGER_DIR/run-ledger.sqlite3-shm"' in runner


def test_trusted_runner_is_copied_only_from_verified_immutable_release() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    stage_at = installer.index(
        '--active-ledger-dir "$RUNTIME_ROOT/active-ledger" > "$STAGE_RESULT"'
    )
    prestop_at = installer.index("if ! verify_loaded_service_unchanged_before_stop")
    stop_at = installer.index("if ! stop_loaded_service", prestop_at)
    offline_at = installer.index("if ! verify_legacy_offline_after_stop", stop_at)
    activate_at = installer.index("if ! activate_staged_release", offline_at)
    transition_at = installer.index("if ! install_transition_artifacts", activate_at)
    bootstrap_at = installer.index("if ! bootstrap_launch_agent", transition_at)
    transition_helper = installer[
        installer.index("install_transition_artifacts()") : installer.index(
            "dashboard_matches_install_contract()"
        )
    ]
    trusted_copy_at = transition_helper.index(
        'install_trusted_runner_from_release "$VERIFIER_RELEASE"'
    )
    anchor_at = transition_helper.index(
        'write_release_integrity_anchor "$VERIFIER_RELEASE" "$VERIFIER_RELEASE"'
    )
    plist_at = transition_helper.index('write_launch_agent_plist "$TRUSTED_RUNNER_SCRIPT"')
    helper = installer[
        installer.index("install_trusted_runner_from_release()") : installer.index("HAD_CURRENT=")
    ]

    assert stage_at < prestop_at < stop_at < offline_at < activate_at
    assert activate_at < transition_at < bootstrap_at
    assert trusted_copy_at < anchor_at < plist_at
    assert 'release_runner="$release_path/scripts/run_macos_service.sh"' in helper
    assert 'ditto "$release_runner" "$trusted_runner_temp"' in helper
    assert 'cmp -s "$release_runner" "$trusted_runner_temp"' in helper
    assert 'ditto "$SOURCE_PROJECT_DIR/scripts/run_macos_service.sh"' not in installer
    assert 'verifier_release_path / "scripts" / "run_macos_service.sh"' in installer
    assert 'install_trusted_runner_from_release "$PREVIOUS_RELEASE"' not in installer
    assert 'install_trusted_runner_from_release "$ROLLBACK_RELEASE"' not in installer


def test_poststage_control_plane_runs_only_from_staged_verifier_release() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_macos_service.sh").read_text(encoding="utf-8")
    stage_at = installer.index(
        '--active-ledger-dir "$RUNTIME_ROOT/active-ledger" > "$STAGE_RESULT"'
    )
    verifier_at = installer.index('VERIFIER_RELEASE="$(cd "$PROJECT_DIR" && pwd -P)"')
    first_legacy_call_at = installer.index("verify_legacy_runtime_contract_file \\", verifier_at)
    assert stage_at < verifier_at < first_legacy_call_at
    assert '"$RUNTIME_VENV/bin/python" -c' not in installer
    assert '"$RUNTIME_VENV/bin/python" - ' not in installer
    assert '"$SOURCE_PYTHON" -c' not in installer

    legacy_helpers = (
        installer[
            installer.index("verify_legacy_runtime_contract_file()") : installer.index(
                "verify_legacy_offline_after_stop()"
            )
        ],
        installer[
            installer.index("verify_legacy_offline_after_stop()") : installer.index(
                "abort_before_transition()"
            )
        ],
    )
    for helper in legacy_helpers:
        assert "PYTHONNOUSERSITE=1" in helper
        assert 'PYTHONPATH="$VERIFIER_RELEASE"' in helper
        assert '"$RUNTIME_VENV/bin/python" -P' in helper
        assert '"$VERIFIER_RELEASE/scripts/verify_compatibility_runtime_preflight.py"' in helper
        assert '"$SOURCE_PROJECT_DIR/.venv/bin/python"' not in helper
        assert (
            '"$SOURCE_PROJECT_DIR/scripts/verify_compatibility_runtime_preflight.py"'
            not in helper
        )

    dashboard_gate = installer[
        installer.index("dashboard_matches_install_contract()") : installer.index(
            "verify_loaded_service_unchanged_before_stop()"
        )
    ]
    assert "PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1" in dashboard_gate
    assert '"$RUNTIME_VENV/bin/python" -P -c' in dashboard_gate

    prestop_gate = installer[
        installer.index("verify_loaded_service_unchanged_before_stop()") : installer.index(
            "rollback_previous_release()"
        )
    ]
    first_bracket_parser = prestop_gate[
        prestop_gate.index("if ! IFS=$'\\t' read -r bracket_snapshot_id") : prestop_gate.index(
            'local bracket_dashboard="$SUPPORT_DIR/latest-install-prestop-bracket-dashboard.json"'
        )
    ]
    final_bracket_parser = prestop_gate[
        prestop_gate.index("if ! IFS=$'\\t' read -r EXPECTED_LEGACY_SNAPSHOT_ID") :
    ]
    for parser in (first_bracket_parser, final_bracket_parser):
        assert "PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1" in parser
        assert '"$RUNTIME_VENV/bin/python" -P -c' in parser

    plist_helper = installer[
        installer.index("write_launch_agent_plist()") : installer.index(
            "prepare_previous_release_for_rollback()"
        )
    ]
    assert 'verifier_template="$VERIFIER_RELEASE/packaging/macos/$LABEL.plist"' in plist_helper
    assert "PYTHONNOUSERSITE=1" in plist_helper
    assert '"$RUNTIME_VENV/bin/python" -P -' in plist_helper
    assert '"$SOURCE_PROJECT_DIR/packaging/macos/$LABEL.plist"' not in plist_helper
    assert '"$SOURCE_PROJECT_DIR/.venv/bin/python"' not in plist_helper

    rollback = installer[
        installer.index("rollback_previous_release()") : installer.index(
            "\nif ! stop_loaded_service; then", installer.index("rollback_previous_release()")
        )
    ]
    assert "PYTHONNOUSERSITE=1" in rollback
    assert 'PYTHONPATH="$VERIFIER_RELEASE" "$RUNTIME_VENV/bin/python" -P -' in rollback
    assert 'PYTHONPATH="$ROLLBACK_RELEASE"' not in rollback
    assert '"$ROLLBACK_RELEASE/.venv/bin/python"' not in rollback

    prune_at = installer.index(
        'PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="$VERIFIER_RELEASE"',
        installer.index('rollback_previous_release "READINESS_FAILED"'),
    )
    prune_pass_at = installer.index(
        'PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \\\n  "$RUNTIME_VENV/bin/python" -P -c',
        prune_at,
    )
    prune_end = installer.index('echo "PASS: 자동 실행 서비스 설치', prune_pass_at)
    prune_command = installer[prune_at:prune_pass_at]
    prune_pass_parser = installer[prune_pass_at:prune_end]
    assert (
        '"$RUNTIME_VENV/bin/python" -P "$VERIFIER_RELEASE/scripts/stage_macos_release.py"'
        in prune_command
    )
    assert "PYTHONNOUSERSITE=1" in prune_command
    assert "PYTHONNOUSERSITE=1" in prune_pass_parser
    assert '"$RUNTIME_VENV/bin/python" -P -c' in prune_pass_parser
    assert 'payload.get("status") != "PASS"' in prune_pass_parser
    assert '"$SOURCE_PROJECT_DIR/.venv/bin/python"' not in prune_command
    assert '"$SOURCE_PROJECT_DIR/scripts/stage_macos_release.py"' not in prune_command


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="실제 /bin/zsh LaunchAgent runner 계약은 macOS에서 검증한다.",
)
def test_service_runner_pins_backend_import_to_physical_release(tmp_path: Path) -> None:
    release, runtime_root, _runtime_python, output, commit, runner = _service_runner_fixture(
        tmp_path
    )

    subprocess.run(
        ["zsh", str(runner)],
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
    assert payload["release_commit"] == commit
    assert payload["release_isolated"] == "true"
    assert payload["real_orders_enabled"] == "false"
    assert payload["python_cache"] == str(runtime_root / "cache" / "python")
    assert payload["tmpdir"] == f"{runtime_root / 'tmp'}/"


@pytest.mark.parametrize("tamper", ("ledger_root", "database", "wal", "shm", "journal"))
@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="실제 /bin/zsh LaunchAgent runner 계약은 macOS에서 검증한다.",
)
def test_service_runner_rejects_ledger_root_and_db_sidecar_symlinks(
    tmp_path: Path,
    tamper: str,
) -> None:
    _release, runtime_root, _runtime_python, output, _commit, runner = _service_runner_fixture(
        tmp_path
    )
    active_ledger = runtime_root / "active-ledger"
    outside = tmp_path / "outside-ledger"
    outside.mkdir()
    if tamper == "ledger_root":
        active_ledger.rename(tmp_path / "saved-active-ledger")
        active_ledger.symlink_to(outside, target_is_directory=True)
    else:
        outside_file = outside / f"external-{tamper}.sqlite3"
        outside_file.write_bytes(b"external")
        suffix = {"database": "", "wal": "-wal", "shm": "-shm", "journal": "-journal"}[tamper]
        (active_ledger / f"run-ledger.sqlite3{suffix}").symlink_to(outside_file)

    result = subprocess.run(
        ["zsh", str(runner)],
        check=False,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "ROBOM_RUNNER_TEST_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 75
    assert not output.exists()


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="실제 /bin/zsh LaunchAgent runner 계약은 macOS에서 검증한다.",
)
def test_service_runner_rejects_manifest_and_backend_tamper_before_backend_start(
    tmp_path: Path,
) -> None:
    release, runtime_root, _runtime_python, output, _, runner = _service_runner_fixture(tmp_path)
    (release / "backend.py").write_text("RELEASE = 999\n", encoding="utf-8")
    manifest_path = release / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["backend.py"] = hashlib.sha256(  # type: ignore[index]
        (release / "backend.py").read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert _verify_release_tree(release)["commit"] == manifest["commit"]

    result = subprocess.run(
        ["zsh", str(runner)],
        check=False,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "ROBOM_RUNNER_TEST_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 75
    assert "v2 전체 tree SHA-256 무결성 검증에 실패" in result.stderr
    assert not output.exists()


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="실제 /bin/zsh LaunchAgent runner 계약은 macOS에서 검증한다.",
)
def test_service_runner_accepts_separate_launcher_source_and_rejects_source_tamper(
    tmp_path: Path,
) -> None:
    release, runtime_root, _runtime_python, output, _, runner = _service_runner_fixture(tmp_path)
    source_commit = "b" * 40
    source_release = runtime_root / "releases" / source_commit
    shutil.copytree(release, source_release)
    source_manifest_path = source_release / "release-manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["commit"] = source_commit
    source_manifest["release_id"] = source_commit
    source_manifest["release_path"] = str(source_release)
    source_runner = source_release / "scripts" / "run_macos_service.sh"
    source_runner.write_bytes(
        source_runner.read_bytes() + b"\n# distinct hardened launcher source\n"
    )
    source_manifest["files"]["scripts/run_macos_service.sh"] = hashlib.sha256(  # type: ignore[index]
        source_runner.read_bytes()
    ).hexdigest()
    source_manifest_path.write_text(json.dumps(source_manifest), encoding="utf-8")
    _verify_release_tree(source_release, expected_commit=source_commit)
    shutil.copy2(source_runner, runner)
    runner.chmod(0o700)
    anchor_path = runtime_root / "support" / "current-release-integrity.json"
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchor["launcher_sha256"] = hashlib.sha256(runner.read_bytes()).hexdigest()
    anchor["launcher_source_release_path"] = str(source_release)
    anchor["launcher_source_commit"] = source_commit
    anchor["launcher_source_manifest_sha256"] = hashlib.sha256(
        source_manifest_path.read_bytes()
    ).hexdigest()
    anchor_path.write_text(json.dumps(anchor), encoding="utf-8")
    assert (
        hashlib.sha256((release / "scripts" / "run_macos_service.sh").read_bytes()).hexdigest()
        != hashlib.sha256(source_runner.read_bytes()).hexdigest()
    )
    assert (
        hashlib.sha256(source_runner.read_bytes()).hexdigest()
        == hashlib.sha256(runner.read_bytes()).hexdigest()
    )

    subprocess.run(
        ["zsh", str(runner)],
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
    assert output.exists()

    output.unlink()
    source_runner.write_text(
        "#!/bin/zsh\n# tampered launcher source\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["zsh", str(runner)],
        check=False,
        env={
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "ROBOM_RUNNER_TEST_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 75
    assert not output.exists()


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


def _service_runner_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, str, Path]:
    source, runtime, market_archive, ledger = _release_fixture(tmp_path)
    scripts = source / "scripts"
    scripts.mkdir()
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "run_macos_service.sh",
        scripts / "run_macos_service.sh",
    )
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "stage_macos_release.py",
        scripts / "stage_macos_release.py",
    )
    (scripts / "run_server.py").write_text("# runner fixture\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "add immutable runner fixture")

    manifest = stage_release(
        source,
        runtime,
        market_archive,
        ledger,
        build_frontend=False,
        prebuilt_frontend_dist=source / "frontend" / "dist",
    )
    release = Path(str(manifest["release_path"]))
    activate_release(runtime, release)
    trusted_runner = runtime / "support" / "run_macos_service.sh"
    trusted_runner.parent.mkdir(parents=True)
    shutil.copy2(
        PROJECT_ROOT / "scripts" / "run_macos_service.sh",
        trusted_runner,
    )
    trusted_runner.chmod(0o700)
    manifest_path = release / "release-manifest.json"
    (runtime / "support" / "current-release-integrity.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "release_path": str(release),
                "release_commit": manifest["commit"],
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "launcher_path": str(trusted_runner),
                "launcher_sha256": hashlib.sha256(trusted_runner.read_bytes()).hexdigest(),
                "launcher_source_release_path": str(release),
                "launcher_source_commit": manifest["commit"],
                "launcher_source_manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
                "paper_only": True,
                "real_orders_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    runtime_python = runtime / "support" / "runtime-venv" / "bin" / "python"
    output = tmp_path / "runner-environment.json"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import json, os, pathlib, sys",
                "arguments = sys.argv[1:]",
                "while arguments and arguments[0] in {'-I', '-P'}:",
                "    arguments.pop(0)",
                "sys.argv = [sys.argv[0], *arguments]",
                "if sys.argv[1] == '-':",
                "    source = sys.stdin.read()",
                "    sys.argv = [sys.argv[1], *sys.argv[2:]]",
                "    exec(compile(source, '<stdin>', 'exec'), {'__name__': '__main__'})",
                "elif sys.argv[1] == '-c' and 'json.loads' in sys.argv[2]:",
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
    return (
        release,
        runtime,
        runtime_python,
        output,
        str(manifest["commit"]),
        trusted_runner,
    )


def _legacy_release_fixture(tmp_path: Path) -> tuple[Path, Path, bytes]:
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
    legacy = {key: value for key, value in manifest.items() if key not in {"file_count", "files"}}
    legacy["schema_version"] = 1
    legacy_bytes = (json.dumps(legacy, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    (release / "release-manifest.json").write_bytes(legacy_bytes)
    return runtime, release, legacy_bytes


@pytest.fixture
def approved_legacy_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, bytes]:
    runtime, release, legacy_bytes = _legacy_release_fixture(tmp_path)
    monkeypatch.setattr(
        stage_macos_release_module,
        "_APPROVED_LEGACY_RUNTIME_COMMIT",
        release.name,
    )
    monkeypatch.setattr(
        stage_macos_release_module,
        "_APPROVED_LEGACY_MANIFEST_SHA256",
        hashlib.sha256(legacy_bytes).hexdigest(),
    )
    return runtime, release, legacy_bytes


def _legacy_safety_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "commit": "50c3e8ae7af08667546e8a1f2e4a70890e92d0f6",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "wallet_paths_enabled": False,
    }


def _safe_legacy_dashboard() -> dict[str, object]:
    payload = _safe_install_dashboard()
    system = payload["system"]
    assert isinstance(system, dict)
    system["release_commit"] = "50c3e8ae7af08667546e8a1f2e4a70890e92d0f6"
    system.pop("funding_readiness")
    intent = payload["paper_entry_intent"]
    assert isinstance(intent, dict)
    intent["updated_ts_ms"] = 90
    accounts = [
        {
            "strategy_id": "SAFE_STRATEGY",
            "profile": "BASE",
            "pending_entries": 0,
            "open_positions": 0,
        }
    ]
    payload["shadow_accounts"] = accounts
    payload["league_accounts"] = [dict(accounts[0])]
    return payload


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _legacy_snapshot_payload(*, pending: bool = False) -> dict[str, object]:
    risk_state = {
        "open_positions": 0,
        "open_planned_risk": "0",
        "pending_planned_risk": "0",
        "gross_notional": "0",
        "pending_notional": "0",
    }
    return {
        "open_position": None,
        "snapshot_ts_ms": 100 if not pending else 101,
        "portfolio": {
            "schema_version": 5,
            "run_id": "run-legacy-test",
            "venue": "BINANCE_USDM",
            "snapshot_ts_ms": 100 if not pending else 101,
            "accounts": [
                {
                    "account_id": "MAIN:BASE",
                    "profile": "BASE",
                    "pending_entries": {"unsafe": {}} if pending else {},
                    "positions": {},
                    "risk_state": risk_state,
                },
                {
                    "account_id": "SAFE_STRATEGY:BASE",
                    "profile": "BASE",
                    "pending_entries": {},
                    "positions": {},
                    "risk_state": risk_state,
                },
            ],
            "shadow_ledger": {
                "accounts": [
                    {
                        "strategy_id": "SAFE_STRATEGY",
                        "profile": "BASE",
                        "open_positions": {},
                    }
                ]
            },
        },
    }


def _create_legacy_ledger(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("PRAGMA user_version=7")
    connection.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            venue TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            config_json TEXT NOT NULL,
            started_ts_ms INTEGER NOT NULL,
            finalized_ts_ms INTEGER,
            summary_json TEXT
        );
        CREATE TABLE app_settings (
            setting_key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_ts_ms INTEGER NOT NULL
        );
        CREATE TABLE snapshots (
            snapshot_id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            lifecycle_state TEXT NOT NULL,
            ts_ms INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            checksum TEXT NOT NULL
        );
        CREATE TABLE execution_audit (
            audit_id INTEGER PRIMARY KEY,
            run_id TEXT NOT NULL,
            ts_ms INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            checksum TEXT NOT NULL
        );
        """
    )
    config = _canonical_json({})
    connection.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
        (
            "run-legacy-test",
            "LIVE_SHADOW_PAPER",
            "BINANCE_USDM",
            hashlib.sha256(config.encode()).hexdigest(),
            config,
            1,
        ),
    )
    setting = _canonical_json(
        {
            "actor": "USER_UI",
            "manual_pause_requested": True,
            "reason": "TEST_PAUSE",
            "revision": 7,
            "run_id": "run-legacy-test",
        }
    )
    connection.execute(
        "INSERT INTO app_settings VALUES (?, ?, ?)",
        ("paper_entry_user_intent", setting, 110),
    )
    snapshot = _canonical_json(_legacy_snapshot_payload())
    connection.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
        (
            1,
            "run-legacy-test",
            "SCANNING",
            100,
            snapshot,
            hashlib.sha256(snapshot.encode()).hexdigest(),
        ),
    )
    audit = _canonical_json({})
    connection.execute(
        "INSERT INTO execution_audit VALUES (?, ?, ?, ?, ?, ?)",
        (
            1,
            "run-legacy-test",
            100,
            "EXIT_FILL",
            audit,
            hashlib.sha256(audit.encode()).hexdigest(),
        ),
    )
    connection.commit()
    return connection


def test_legacy_dashboard_allows_only_complete_or_fully_absent_flat_aggregate() -> None:
    payload = _safe_legacy_dashboard()
    for field in (
        "main_pending_entry_count",
        "league_pending_entry_count",
        "total_pending_entry_count",
        "total_open_position_count",
        "paper_portfolio_flat",
    ):
        payload.pop(field)

    run_id, revision, _, pairs = _verify_dashboard(
        payload,
        commit="50c3e8ae7af08667546e8a1f2e4a70890e92d0f6",
        expected_run_id=None,
        expected_pause_revision=None,
    )
    assert (run_id, revision, pairs) == (
        "run-v6-install-contract",
        7,
        {("SAFE_STRATEGY", "BASE")},
    )

    payload["paper_portfolio_flat"] = True
    with pytest.raises(LegacyRuntimePreflightError, match="일부만"):
        _verify_dashboard(
            payload,
            commit="50c3e8ae7af08667546e8a1f2e4a70890e92d0f6",
            expected_run_id=None,
            expected_pause_revision=None,
        )


def test_legacy_dashboard_rejects_explicit_false_flat_aggregate() -> None:
    payload = _safe_legacy_dashboard()
    payload["paper_portfolio_flat"] = False

    with pytest.raises(LegacyRuntimePreflightError, match="명시적 True"):
        _verify_dashboard(
            payload,
            commit="50c3e8ae7af08667546e8a1f2e4a70890e92d0f6",
            expected_run_id=None,
            expected_pause_revision=None,
        )


def test_lsof_parser_preserves_utf8_path_device_and_inode_record_boundaries() -> None:
    raw = (
        b"p15193\0\nfcwd\0D0x100001b\0i24\0n/Volumes/ROBOM_FLOWSCALPER/\xec\x9e\x90\xeb\x8f\x99\xeb\xa7\xa4\xeb\xa7\xa4\0\n"
        b"f17\0D0x100001b\0i99\0n/Volumes/ROBOM_FLOWSCALPER/run-ledger.sqlite3\0\n"
    )

    records = _parse_lsof_records(raw)

    assert records == [
        {"p": "15193"},
        {
            "f": "cwd",
            "D": "0x100001b",
            "i": "24",
            "n": "/Volumes/ROBOM_FLOWSCALPER/자동매매",
        },
        {
            "f": "17",
            "D": "0x100001b",
            "i": "99",
            "n": "/Volumes/ROBOM_FLOWSCALPER/run-ledger.sqlite3",
        },
    ]
    assert int(records[1]["D"], 0) == 0x100001B


def test_stopped_binding_rejects_lsof_diagnostic_even_when_no_owner_is_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    absent = subprocess.CompletedProcess(
        [],
        113,
        stdout=b"",
        stderr=(
            b'Bad request.\nCould not find service "kr.robom.flowscalper" '
            + f"in domain for user gui: {os.getuid()}\n".encode()
        ),
    )
    results = iter(
        (
            absent,
            subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"lsof diagnostic"),
            subprocess.CompletedProcess([], 1, stdout=b"", stderr=b""),
            absent,
        )
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        return next(results)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(LegacyRuntimePreflightError, match="lsof 진단이 실패"):
        verify_stopped_process_binding(ledger_path=tmp_path / "run-ledger.sqlite3")


def test_stopped_binding_rejects_noncanonical_launchctl_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    diagnostic = subprocess.CompletedProcess([], 2, stdout=b"", stderr=b"launchctl transport error")
    no_match = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"")
    results = iter((diagnostic, no_match, no_match, diagnostic))

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        return next(results)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(LegacyRuntimePreflightError, match="exact 계약"):
        verify_stopped_process_binding(ledger_path=tmp_path / "run-ledger.sqlite3")


def test_running_binding_rejects_partial_lsof_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    release = runtime_root / "releases" / ("a" * 40)
    ledger = runtime_root / "active-ledger" / "run-ledger.sqlite3"
    release.mkdir(parents=True)
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"ledger")
    (runtime_root / "current").symlink_to(Path("releases") / release.name)
    launchctl = subprocess.CompletedProcess([], 0, stdout="\tpid = 123\n", stderr="")
    lsof = subprocess.CompletedProcess(
        [],
        0,
        stdout=f"p123\0\nf7\0D0x{ledger.stat().st_dev:x}\0i{ledger.stat().st_ino}\0n{ledger}\0\n".encode(),
        stderr=b"partial lsof diagnostic",
    )
    results = iter((launchctl, lsof))

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[object]:
        del args, kwargs
        return next(results)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(LegacyRuntimePreflightError, match="부분 진단"):
        verify_running_process_binding(ledger_path=ledger, release_path=release)


def test_process_binding_continuity_rejects_same_pid_with_replaced_ledger_inode() -> None:
    before: dict[str, object] = {
        "service_pid": 123,
        "ledger_device": 10,
        "ledger_inode": 20,
        "cwd_device": 10,
        "cwd_inode": 30,
        "open_database_paths": ["/runtime/active-ledger/run-ledger.sqlite3"],
    }
    after = {**before, "ledger_inode": 21}

    with pytest.raises(LegacyRuntimePreflightError, match="ledger_inode"):
        _require_stable_process_binding(before, after)


@pytest.mark.parametrize(
    "raw, error",
    (
        ('{"status":{},"status":{}}', "중복 JSON key"),
        ('{"lag":NaN}', "비표준 숫자"),
    ),
)
def test_legacy_cli_json_reader_rejects_duplicate_keys_and_nonfinite_constants(
    tmp_path: Path,
    raw: str,
    error: str,
) -> None:
    payload = tmp_path / "payload.json"
    payload.write_text(raw, encoding="utf-8")

    with pytest.raises(LegacyRuntimePreflightError, match=error):
        _read_json_object(payload, "legacy test")


def test_legacy_ledger_reads_latest_unsafe_snapshot_from_wal(tmp_path: Path) -> None:
    ledger = tmp_path / "run-ledger.sqlite3"
    writer = _create_legacy_ledger(ledger)
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    unsafe = _canonical_json(_legacy_snapshot_payload(pending=True))
    writer.execute(
        "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?)",
        (
            2,
            "run-legacy-test",
            "SCANNING",
            101,
            unsafe,
            hashlib.sha256(unsafe.encode()).hexdigest(),
        ),
    )
    writer.commit()
    try:
        with pytest.raises(LegacyRuntimePreflightError, match="pending entry"):
            _verify_ledger(
                ledger,
                run_id="run-legacy-test",
                revision=7,
                intent_updated_ts_ms=90,
                dashboard_pairs={("SAFE_STRATEGY", "BASE")},
                minimum_snapshot_id=1,
                minimum_recovery_audit_id=1,
            )
    finally:
        writer.close()


def test_legacy_ledger_rejects_same_name_schema_without_setting_primary_key(
    tmp_path: Path,
) -> None:
    ledger = tmp_path / "run-ledger.sqlite3"
    writer = _create_legacy_ledger(ledger)
    safe_setting = writer.execute("SELECT value_json, updated_ts_ms FROM app_settings").fetchone()
    writer.executescript(
        """
        DROP TABLE app_settings;
        CREATE TABLE app_settings (
            setting_key TEXT,
            value_json TEXT NOT NULL,
            updated_ts_ms INTEGER NOT NULL
        );
        """
    )
    writer.execute(
        "INSERT INTO app_settings VALUES (?, ?, ?)",
        ("paper_entry_user_intent", safe_setting[0], safe_setting[1]),
    )
    unsafe_setting = _canonical_json(
        {
            "actor": "TAMPER",
            "manual_pause_requested": False,
            "reason": "UNSAFE_DUPLICATE",
            "revision": 8,
            "run_id": "run-legacy-test",
        }
    )
    writer.execute(
        "INSERT INTO app_settings VALUES (?, ?, ?)",
        ("paper_entry_user_intent", unsafe_setting, 111),
    )
    writer.commit()
    try:
        with pytest.raises(LegacyRuntimePreflightError, match="app_settings schema"):
            _verify_ledger(
                ledger,
                run_id="run-legacy-test",
                revision=7,
                intent_updated_ts_ms=90,
                dashboard_pairs={("SAFE_STRATEGY", "BASE")},
                minimum_snapshot_id=1,
                minimum_recovery_audit_id=1,
            )
    finally:
        writer.close()


def test_legacy_ledger_rejects_recovery_audit_before_run_start(tmp_path: Path) -> None:
    ledger = tmp_path / "run-ledger.sqlite3"
    writer = _create_legacy_ledger(ledger)
    writer.execute("UPDATE execution_audit SET ts_ms = 0 WHERE audit_id = 1")
    writer.commit()
    try:
        with pytest.raises(LegacyRuntimePreflightError, match="Run 시작보다 오래"):
            _verify_ledger(
                ledger,
                run_id="run-legacy-test",
                revision=7,
                intent_updated_ts_ms=90,
                dashboard_pairs={("SAFE_STRATEGY", "BASE")},
                minimum_snapshot_id=1,
                minimum_recovery_audit_id=1,
            )
    finally:
        writer.close()


def test_legacy_manifest_migration_rejects_nonallowlisted_commit_and_bytes(
    tmp_path: Path,
) -> None:
    runtime, release, legacy_bytes = _legacy_release_fixture(tmp_path)

    with pytest.raises(RuntimeError, match="승인 allowlist"):
        migrate_legacy_release_manifest(runtime, release)

    assert (release / "release-manifest.json").read_bytes() == legacy_bytes


def test_legacy_v1_old_dashboard_allows_only_missing_runtime_safety_evidence() -> None:
    old_system = {"auth_headers": False}

    missing = legacy_runtime_safety_fields_missing(
        _legacy_safety_manifest(),
        old_system,
    )

    assert missing == (
        "private_api_enabled",
        "api_key_enabled",
        "wallet_enabled",
        "runtime_ai_order_decision_enabled",
    )


@pytest.mark.parametrize(
    "field",
    [
        "private_api_enabled",
        "api_key_enabled",
        "wallet_enabled",
        "runtime_ai_order_decision_enabled",
    ],
)
def test_legacy_v1_old_dashboard_rejects_any_true_runtime_safety_field(field: str) -> None:
    old_system: dict[str, object] = {"auth_headers": False, field: True}

    with pytest.raises(RuntimeError, match="명시적 False"):
        legacy_runtime_safety_fields_missing(_legacy_safety_manifest(), old_system)


def test_v2_dashboard_requires_all_runtime_safety_fields_explicitly_false() -> None:
    manifest = {**_legacy_safety_manifest(), "schema_version": 2}
    with pytest.raises(RuntimeError, match="승인되지 않은 릴리스"):
        legacy_runtime_safety_fields_missing(manifest, {"auth_headers": False})

    system = {
        "auth_headers": False,
        "private_api_enabled": False,
        "api_key_enabled": False,
        "wallet_enabled": False,
        "runtime_ai_order_decision_enabled": False,
    }
    assert legacy_runtime_safety_fields_missing(manifest, system) == ()


def test_legacy_v1_release_manifest_migrates_metadata_without_touching_tree(
    approved_legacy_release: tuple[Path, Path, bytes],
) -> None:
    runtime, release, legacy_bytes = approved_legacy_release
    tree_before = {
        path.relative_to(release).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in release.rglob("*")
        if path.is_file() and path.name != "release-manifest.json"
    }

    migrated = migrate_legacy_release_manifest(runtime, release)

    tree_after = {
        path.relative_to(release).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in release.rglob("*")
        if path.is_file() and path.name != "release-manifest.json"
    }
    assert migrated["schema_version"] == 2
    assert migrated["legacy_schema_version"] == 1
    assert migrated["legacy_manifest_sha256"] == hashlib.sha256(legacy_bytes).hexdigest()
    assert migrated["file_count"] == len(tree_before)
    assert set(migrated["files"]) == set(tree_before)
    assert tree_after == tree_before


def test_legacy_manifest_migration_is_idempotent_for_verified_v2(
    approved_legacy_release: tuple[Path, Path, bytes],
) -> None:
    runtime, release, _ = approved_legacy_release
    first = migrate_legacy_release_manifest(runtime, release)
    manifest_path = release / "release-manifest.json"
    bytes_before = manifest_path.read_bytes()
    mtime_before = manifest_path.stat().st_mtime_ns

    second = migrate_legacy_release_manifest(runtime, release)

    assert second == first
    assert manifest_path.read_bytes() == bytes_before
    assert manifest_path.stat().st_mtime_ns == mtime_before


@pytest.mark.parametrize("mutation", ["modified", "missing", "added"])
def test_legacy_manifest_migration_rejects_source_tree_not_matching_git_commit(
    approved_legacy_release: tuple[Path, Path, bytes],
    mutation: str,
) -> None:
    runtime, release, legacy_bytes = approved_legacy_release
    backend = release / "backend.py"
    if mutation == "modified":
        backend.write_text("RELEASE = 999\n", encoding="utf-8")
    elif mutation == "missing":
        backend.unlink()
    else:
        (release / "unexpected.py").write_text("UNEXPECTED = True\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="source tree.*Git commit"):
        migrate_legacy_release_manifest(runtime, release)

    assert (release / "release-manifest.json").read_bytes() == legacy_bytes


def test_legacy_preflight_rejects_allowlisted_manifest_with_tampered_v1_tree(
    approved_legacy_release: tuple[Path, Path, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, release, legacy_bytes = approved_legacy_release
    (runtime / "current").symlink_to(Path("releases") / release.name)
    monkeypatch.setattr(
        legacy_preflight_module,
        "SUPPORTED_LEGACY_RUNTIME_COMMITS",
        frozenset({release.name}),
    )
    monkeypatch.setattr(
        legacy_preflight_module,
        "_LEGACY_MANIFEST_SHA256_BY_COMMIT",
        {release.name: hashlib.sha256(legacy_bytes).hexdigest()},
    )
    manifest = json.loads(legacy_bytes)
    legacy_preflight_module._verify_manifest(manifest, runtime_root=runtime)

    (release / "backend.py").write_text("RELEASE = 999\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="source tree.*Git commit"):
        legacy_preflight_module._verify_manifest(manifest, runtime_root=runtime)


def test_legacy_manifest_migration_rejects_frontend_not_matching_v1_hashes(
    approved_legacy_release: tuple[Path, Path, bytes],
) -> None:
    runtime, release, legacy_bytes = approved_legacy_release
    (release / "frontend" / "dist" / "index.html").write_text(
        "<html>tampered legacy frontend</html>",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="frontend.*v1 manifest"):
        migrate_legacy_release_manifest(runtime, release)

    assert (release / "release-manifest.json").read_bytes() == legacy_bytes


def test_legacy_manifest_migration_requires_resolvable_source_repository(
    approved_legacy_release: tuple[Path, Path, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, release, _ = approved_legacy_release
    manifest_path = release / "release-manifest.json"
    legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy["source_repository_path"] = str(tmp_path / "missing-source")
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(
        stage_macos_release_module,
        "_APPROVED_LEGACY_MANIFEST_SHA256",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(RuntimeError, match="source repository"):
        migrate_legacy_release_manifest(runtime, release)

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["schema_version"] == 1


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("paper_only", False),
        ("real_orders_enabled", True),
        ("auth_required", True),
        ("private_api_enabled", True),
        ("wallet_paths_enabled", True),
    ],
)
def test_legacy_manifest_migration_rejects_unsafe_release_flags(
    approved_legacy_release: tuple[Path, Path, bytes],
    field: str,
    unsafe_value: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, release, _ = approved_legacy_release
    manifest_path = release / "release-manifest.json"
    legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy[field] = unsafe_value
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(
        stage_macos_release_module,
        "_APPROVED_LEGACY_MANIFEST_SHA256",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(RuntimeError, match="PAPER 안전 불변조건"):
        migrate_legacy_release_manifest(runtime, release)

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_legacy_manifest_migration_rejects_commit_directory_mismatch(
    approved_legacy_release: tuple[Path, Path, bytes],
) -> None:
    runtime, release, _ = approved_legacy_release
    manifest_path = release / "release-manifest.json"
    legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
    other_commit = "f" * 40
    legacy["commit"] = other_commit
    legacy["release_id"] = other_commit
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")

    with pytest.raises(RuntimeError, match="디렉터리·release_id·commit"):
        migrate_legacy_release_manifest(runtime, release)


def test_legacy_manifest_migration_rejects_release_path_mismatch(
    approved_legacy_release: tuple[Path, Path, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, release, _ = approved_legacy_release
    manifest_path = release / "release-manifest.json"
    legacy = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy["release_path"] = "/tmp/not-the-legacy-release"
    manifest_path.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(
        stage_macos_release_module,
        "_APPROVED_LEGACY_MANIFEST_SHA256",
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(RuntimeError, match="release_path"):
        migrate_legacy_release_manifest(runtime, release)

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_legacy_manifest_migration_rejects_symlink_in_release_tree(
    approved_legacy_release: tuple[Path, Path, bytes],
) -> None:
    runtime, release, _ = approved_legacy_release
    (release / "linked.py").symlink_to(release / "backend.py")

    with pytest.raises(RuntimeError, match="tree link"):
        migrate_legacy_release_manifest(runtime, release)


def test_legacy_manifest_migration_requires_direct_runtime_release_child(
    approved_legacy_release: tuple[Path, Path, bytes],
) -> None:
    runtime, release, _ = approved_legacy_release
    nested_root = runtime / "releases" / "nested"
    nested_root.mkdir()
    nested_release = nested_root / release.name
    release.rename(nested_release)

    with pytest.raises(RuntimeError, match="releases 바로 아래"):
        migrate_legacy_release_manifest(runtime, nested_release)


def test_legacy_manifest_migration_rejects_symlinked_releases_root(
    approved_legacy_release: tuple[Path, Path, bytes],
    tmp_path: Path,
) -> None:
    runtime, release, _ = approved_legacy_release
    alias_runtime = tmp_path / "alias-runtime"
    alias_runtime.mkdir()
    (alias_runtime / "releases").symlink_to(runtime / "releases", target_is_directory=True)

    with pytest.raises(RuntimeError, match="releases symlink"):
        migrate_legacy_release_manifest(alias_runtime, release)


def test_legacy_manifest_migration_restores_original_manifest_if_verify_fails(
    approved_legacy_release: tuple[Path, Path, bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, release, legacy_bytes = approved_legacy_release

    def fail_verification(_: Path) -> dict[str, object]:
        raise RuntimeError("forced post-migration verification failure")

    monkeypatch.setattr(
        "scripts.stage_macos_release._verify_release_tree",
        fail_verification,
    )

    with pytest.raises(RuntimeError, match="forced post-migration"):
        migrate_legacy_release_manifest(runtime, release)

    assert (release / "release-manifest.json").read_bytes() == legacy_bytes


def test_staged_release_rejects_untracked_source_files(tmp_path: Path) -> None:
    source, runtime, market_archive, ledger = _release_fixture(tmp_path)
    (source / "untracked-v6-module.py").write_text("V6 = True\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="미추적"):
        stage_release(
            source,
            runtime,
            market_archive,
            ledger,
            build_frontend=False,
            prebuilt_frontend_dist=source / "frontend" / "dist",
        )


def test_staged_release_manifest_hashes_every_file_except_itself(tmp_path: Path) -> None:
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
    actual_files = {
        path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in release.rglob("*")
        if path.is_file() and path != release / "release-manifest.json"
    }

    assert manifest["schema_version"] == 2
    assert manifest["file_count"] == len(actual_files)
    assert manifest["files"] == dict(sorted(actual_files.items()))
    assert "release-manifest.json" not in manifest["files"]
    assert "backend.py" in manifest["files"]
    assert "frontend/dist/index.html" in manifest["files"]


def test_same_commit_release_reuse_rejects_modified_tree(tmp_path: Path) -> None:
    source, runtime, market_archive, ledger = _release_fixture(tmp_path)
    manifest = stage_release(
        source,
        runtime,
        market_archive,
        ledger,
        build_frontend=False,
        prebuilt_frontend_dist=source / "frontend" / "dist",
    )
    reused = stage_release(
        source,
        runtime,
        market_archive,
        ledger,
        build_frontend=False,
        prebuilt_frontend_dist=source / "frontend" / "dist",
    )
    assert reused == manifest

    release = Path(str(manifest["release_path"]))
    (release / "backend.py").write_text("RELEASE = 999\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="무결성.*modified"):
        stage_release(
            source,
            runtime,
            market_archive,
            ledger,
            build_frontend=False,
            prebuilt_frontend_dist=source / "frontend" / "dist",
        )


@pytest.mark.parametrize("mutation", ["modified", "missing", "added"])
def test_activate_release_rejects_any_tree_difference_before_pointer_switch(
    tmp_path: Path,
    mutation: str,
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
    target = release / "backend.py"
    if mutation == "modified":
        target.write_text("RELEASE = 999\n", encoding="utf-8")
    elif mutation == "missing":
        target.unlink()
    else:
        (release / "unexpected.py").write_text("UNEXPECTED = True\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=f"무결성.*{mutation}"):
        activate_release(runtime, release)

    assert not (runtime / "current").exists()
    assert not (runtime / "current-deployment.json").exists()


@pytest.mark.parametrize(
    ("field", "unsafe_value", "message"),
    [
        ("schema_version", 1, "schema"),
        ("release_id", "f" * 40, "release_id"),
        ("release_path", "/tmp/not-the-staged-release", "release_path"),
        ("paper_only", False, "PAPER 안전"),
        ("real_orders_enabled", True, "PAPER 안전"),
        ("auth_required", True, "PAPER 안전"),
        ("private_api_enabled", True, "PAPER 안전"),
        ("wallet_paths_enabled", True, "PAPER 안전"),
    ],
)
def test_activate_release_rejects_untrusted_manifest_metadata_before_pointer_switch(
    tmp_path: Path,
    field: str,
    unsafe_value: object,
    message: str,
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
    manifest_path = release / "release-manifest.json"
    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered[field] = unsafe_value
    manifest_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        activate_release(runtime, release)

    assert not (runtime / "current").exists()
    assert not (runtime / "current-deployment.json").exists()


def test_current_pointer_requires_direct_release_child(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    nested_release = runtime / "releases" / "nested" / ("a" * 40)
    nested_release.mkdir(parents=True)
    (runtime / "current").symlink_to(
        nested_release.relative_to(runtime),
        target_is_directory=True,
    )

    with pytest.raises(RuntimeError, match="releases 바로 아래"):
        current_release(runtime)


def test_activate_release_canonicalizes_runtime_root_symlink_alias(tmp_path: Path) -> None:
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
    runtime_alias = tmp_path / "runtime-alias"
    runtime_alias.symlink_to(runtime, target_is_directory=True)

    deployment = activate_release(runtime_alias, release)

    assert current_release(runtime_alias) == release.resolve()
    assert (runtime / "current").resolve() == release.resolve()
    assert deployment["new_state"] == str(release.resolve())


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


def test_release_retention_rejects_symlinked_releases_root_without_deleting_target(
    tmp_path: Path,
) -> None:
    external_releases = tmp_path / "external-releases"
    victim = external_releases / ("a" * 40)
    victim.mkdir(parents=True)
    sentinel = victim / "sentinel.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    runtime = tmp_path / "runtime-alias"
    runtime.mkdir()
    (runtime / "releases").symlink_to(external_releases, target_is_directory=True)

    with pytest.raises(RuntimeError, match="releases symlink"):
        prune_obsolete_releases(runtime)

    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_release_retention_preserves_anchor_source_separate_from_target_and_rollback(
    tmp_path: Path,
) -> None:
    source, runtime, market_archive, ledger = _release_fixture(tmp_path)
    (source / "scripts").mkdir()
    (source / "scripts" / "run_macos_service.sh").write_text(
        "#!/bin/zsh\n# fixture trusted runner\n",
        encoding="utf-8",
    )
    _git(source, "add", ".")
    _git(source, "commit", "-m", "add fixture trusted runner")
    releases: list[Path] = []
    manifests: list[dict[str, object]] = []
    for revision in range(3):
        if revision:
            (source / "backend.py").write_text(
                f"RELEASE = {revision + 1}\n",
                encoding="utf-8",
            )
            _git(source, "add", ".")
            _git(source, "commit", "-m", f"fixture anchored release {revision + 1}")
        manifest = stage_release(
            source,
            runtime,
            market_archive,
            ledger,
            build_frontend=False,
            prebuilt_frontend_dist=source / "frontend" / "dist",
        )
        release = Path(str(manifest["release_path"]))
        releases.append(release)
        manifests.append(manifest)
        activate_release(runtime, release)

    support = runtime / "support"
    support.mkdir()
    launcher = support / "run_macos_service.sh"
    source_runner = releases[0] / "scripts" / "run_macos_service.sh"
    shutil.copy2(source_runner, launcher)
    target_manifest = releases[2] / "release-manifest.json"
    source_manifest = releases[0] / "release-manifest.json"
    (support / "current-release-integrity.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "release_path": str(releases[2]),
                "release_commit": manifests[2]["commit"],
                "manifest_sha256": hashlib.sha256(target_manifest.read_bytes()).hexdigest(),
                "launcher_path": str(launcher),
                "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
                "launcher_source_release_path": str(releases[0]),
                "launcher_source_commit": manifests[0]["commit"],
                "launcher_source_manifest_sha256": hashlib.sha256(
                    source_manifest.read_bytes()
                ).hexdigest(),
                "paper_only": True,
                "real_orders_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    result = prune_obsolete_releases(runtime)

    assert result["status"] == "PASS"
    assert result["pruned_releases"] == []
    assert all(release.is_dir() for release in releases)
    assert set(result["retained_releases"]) == {str(release) for release in releases}
