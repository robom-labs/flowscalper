# 유지보수 정지 상태 릴리스 설치의 fail-closed 전환과 복구를 검증한다.
"""`--maintenance-stopped` macOS installer 계약 회귀검사다."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_PATH = PROJECT_ROOT / "scripts" / "install_macos_service.sh"


def _installer() -> str:
    return INSTALLER_PATH.read_text(encoding="utf-8")


def _shell_function(name: str, next_name: str) -> str:
    source = _installer()
    return source[source.index(f"{name}()") : source.index(f"{next_name}()")]


def _postflight_python_source() -> str:
    contract = _shell_function(
        "maintenance_postflight_contract",
        "verify_maintenance_postflight_stable",
    )
    marker = "    'import json,math,sys\n"
    start = contract.index(marker) + len("    '")
    argument_marker = "' " + "\\" + "\n    \"$MAINTENANCE_STOPPED_EVIDENCE\""
    end = contract.index(argument_marker, start)
    return contract[start:end]


def _offline_evidence_python_source() -> str:
    source = _installer()
    contract = source[
        source.index("verify_maintenance_stopped_evidence()") : source.index(
            'HAD_CURRENT="false"'
        )
    ]
    marker = (
        '    "$RUNTIME_ROOT/current-deployment.json" '
        '"$MAINTENANCE_STOPPED_EVIDENCE" <<\'PY\'\n'
    )
    start = contract.index(marker) + len(marker)
    end = contract.index("\nPY\n  then", start)
    return contract[start:end]


def _safe_postflight_system() -> dict[str, object]:
    return {
        "queue_depth": 8,
        "queue_overload_active": False,
        "queue_overload_drop_count": 0,
        "lag_p95_ms": 120.0,
        "trade_lag_p95_ms": 80.0,
        "critical_lag_active": False,
        "critical_lag_incident_count": 5,
        "persistence_fault_active": False,
        "persistence_buffer_dropped": 411,
        "persistence_fault_count": 6,
    }


def _run_postflight_contract(
    tmp_path: Path,
    *,
    earlier: dict[str, object],
    later: dict[str, object],
) -> subprocess.CompletedProcess[str]:
    evidence = tmp_path / "evidence.json"
    first = tmp_path / "first.json"
    final = tmp_path / "final.json"
    evidence.write_text(
        json.dumps(
            {
                "baseline": {
                    "queue_depth": 13,
                    "queue_overload_drop_count": 0,
                    "critical_lag_incident_count": 5,
                    "persistence_buffer_dropped": 411,
                    "persistence_fault_count": 6,
                }
            }
        ),
        encoding="utf-8",
    )
    first.write_text(json.dumps({"system": earlier}), encoding="utf-8")
    final.write_text(json.dumps({"system": later}), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _postflight_python_source(),
            str(evidence),
            str(first),
            str(final),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "PYTHONOPTIMIZE": "1"},
    )


def test_maintenance_stopped_requires_joined_offline_identity_and_critical_evidence() -> None:
    installer = _installer()
    evidence = installer[
        installer.index("verify_maintenance_stopped_evidence()") : installer.index(
            'HAD_CURRENT="false"'
        )
    ]

    assert '--maintenance-stopped)' in installer
    assert '[[ "$HAD_CURRENT" != "true" || "$HAD_JOB" != "false" ]]' in installer
    assert 'launchctl_command print-disabled "gui/$USER_ID"' in installer
    assert '\\"$LABEL\\" => disabled' in installer
    assert 'verify_maintenance_stopped_evidence "initial-maintenance"' in installer
    assert 'current_pointer.resolve(strict=True) == previous_release' in evidence
    assert 'anchor.get("release_commit") == commit' in evidence
    assert 'deployment.get("release_commit") == commit' in evidence
    assert 'identity.get("run_id") == run_id' in evidence
    assert 'identity.get("pause_revision") == revision' in evidence
    assert 'operation.get("market_observation_active") is True' in evidence
    assert 'setting.get("revision") == revision' in evidence
    assert 'setting.get("manual_pause_requested") is True' in evidence
    assert 'snapshot_payload.get("open_position") is None' in evidence
    assert 'active ledger positions에 open row' in evidence
    assert 'active ledger paper_orders에 pending row' in evidence
    assert 'for table in ("runs", "positions", "paper_orders", "trades", "transitions")' in evidence
    assert 'PRAGMA quick_check(\'{table}\')' in evidence
    assert 'immutable=1&cache=private' in evidence
    assert 'not sidecar.exists() and not sidecar.is_symlink()' in evidence
    assert '"real_orders_enabled": False' in evidence
    compile(
        _offline_evidence_python_source(),
        "maintenance-stopped-offline-evidence",
        "exec",
    )


def test_maintenance_transition_double_checks_absence_and_bootstraps_once() -> None:
    installer = _installer()
    main = installer[installer.index("\nif ! verify_loaded_service_unchanged_before_stop;") :]

    backup_at = main.index("if ! prepare_maintenance_artifact_backup")
    clean_commit_at = main.index("if ! verify_staged_source_commit_still_final")
    preactivation_at = main.index(
        'verify_stopped_process_absence_exact "maintenance-immediate-preactivation"'
    )
    activate_at = main.index("if ! activate_staged_release")
    transition_at = main.index("if ! install_transition_artifacts")
    prebootstrap_at = main.index(
        'verify_stopped_process_absence_exact "maintenance-immediate-prebootstrap"'
    )
    single_bootstrap_at = main.index("bootstrap_launch_agent_once")
    readiness_at = main.index('if [[ "$service_ready" != "true" ]]')
    postflight_at = main.index("verify_maintenance_postflight_stable", readiness_at)
    assert clean_commit_at < backup_at < preactivation_at < activate_at
    assert activate_at < transition_at < prebootstrap_at < single_bootstrap_at
    assert readiness_at < postflight_at

    final_commit = _shell_function(
        "verify_staged_source_commit_still_final",
        "stop_loaded_service",
    )
    assert "status --porcelain --untracked-files=all" in final_commit
    assert '"$source_commit" != "$EXPECTED_RELEASE_COMMIT"' in final_commit

    absence = _shell_function(
        "verify_stopped_process_absence_exact",
        "verify_maintenance_stopped_evidence",
    )
    assert absence.count('launchctl_command print "$SERVICE_TARGET"') == 2
    assert '-iTCP:8870 -sTCP:LISTEN' in absence
    assert 'for ledger_candidate in "$ledger_path" "$ledger_path-wal" "$ledger_path-shm"' in absence
    assert 'ledger_status != 1' in absence


def test_single_bootstrap_helper_calls_launchctl_exactly_once(tmp_path: Path) -> None:
    if not Path("/bin/zsh").is_file():
        pytest.skip("zsh가 없는 환경에서는 installer mock을 실행하지 않는다.")
    helper = _shell_function("bootstrap_launch_agent_once", "verify_service_fully_stopped")
    harness = f"""
set -e
USER_ID=501
TARGET_PLIST={str(tmp_path / 'agent.plist')!r}
launchctl_command() {{ print -r -- "$*"; }}
{helper}
bootstrap_launch_agent_once
"""
    result = subprocess.run(
        ["/bin/zsh", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout.splitlines() == [f"bootstrap gui/501 {tmp_path / 'agent.plist'}"]
    assert "kickstart" not in result.stdout


def test_maintenance_postflight_fixture_accepts_stable_bounded_runtime(tmp_path: Path) -> None:
    first = _safe_postflight_system()
    final = {**first, "queue_depth": 12}

    result = _run_postflight_contract(tmp_path, earlier=first, later=final)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ({"queue_overload_drop_count": 1}, "queue_overload_drop_count"),
        ({"critical_lag_incident_count": 6}, "critical_lag_incident_count"),
        ({"persistence_buffer_dropped": 412}, "persistence_buffer_dropped"),
        ({"persistence_fault_count": 7}, "persistence_fault_count"),
        ({"queue_depth": 65}, "queue depth"),
        ({"critical_lag_active": True}, "critical lag"),
    ],
)
def test_maintenance_postflight_fixture_fails_closed_on_regression(
    tmp_path: Path,
    mutation: dict[str, object],
    expected_error: str,
) -> None:
    first = _safe_postflight_system()
    final = {**first, **mutation}

    result = _run_postflight_contract(tmp_path, earlier=first, later=final)

    assert result.returncode != 0
    assert expected_error in result.stderr


@pytest.mark.parametrize(("restore_status", "expected_status"), [(0, 0), (1, 1)])
def test_maintenance_rollback_restores_artifacts_without_restarting_old_job(
    tmp_path: Path,
    restore_status: int,
    expected_status: int,
) -> None:
    if not Path("/bin/zsh").is_file():
        pytest.skip("zsh가 없는 환경에서는 installer rollback mock을 실행하지 않는다.")
    previous_release = tmp_path / "old-release"
    previous_release.mkdir()
    current = tmp_path / "current"
    current.symlink_to(previous_release, target_is_directory=True)
    helper = _shell_function(
        "rollback_previous_release_stopped",
        "dashboard_matches_install_contract",
    )
    harness = f"""
set +e
PREVIOUS_RELEASE={str(previous_release)!r}
ROLLBACK_RELEASE={str(previous_release)!r}
CURRENT_POINTER={str(current)!r}
SERVICE_TARGET=gui/501/kr.robom.flowscalper
UNVERIFIED_SERVICE_MAY_BE_RUNNING=true
MAINTENANCE_TRANSITION_STARTED=true
stop_loaded_service() {{ print -r -- STOP; return 0; }}
launchctl_command() {{ return 0; }}
verify_stopped_process_absence_exact() {{ print -r -- "VERIFY:$1"; return 0; }}
activate_previous_release_stopped() {{ print -r -- "ACTIVATE:$1"; return 0; }}
restore_maintenance_artifacts() {{ print -r -- RESTORE; return {restore_status}; }}
cleanup_maintenance_artifact_backup() {{ print -r -- CLEANUP; return 0; }}
{helper}
rollback_previous_release_stopped TEST_FAILURE
result_code=$?
print -r -- "STATUS:$result_code"
print -r -- "TRANSITION:$MAINTENANCE_TRANSITION_STARTED"
exit $result_code
"""
    result = subprocess.run(
        ["/bin/zsh", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == expected_status
    lines = result.stdout.splitlines()
    assert lines[:4] == [
        "STOP",
        "VERIFY:maintenance-rollback-before",
        "ACTIVATE:TEST_FAILURE",
        "RESTORE",
    ]
    assert not any("bootstrap" in line or "kickstart" in line or "enable" in line for line in lines)
    if restore_status == 0:
        assert "VERIFY:maintenance-rollback-after" in lines
        assert "CLEANUP" in lines
        assert "TRANSITION:false" in lines
    else:
        assert "VERIFY:maintenance-rollback-after" not in lines
        assert "CLEANUP" not in lines
        assert "TRANSITION:true" in lines


def test_maintenance_rollback_helper_contains_no_old_job_restart() -> None:
    helper = _shell_function(
        "rollback_previous_release_stopped",
        "dashboard_matches_install_contract",
    )

    assert "bootstrap_launch_agent" not in helper
    assert "kickstart" not in helper
    assert 'launchctl_command enable' not in helper
    assert 'verify_stopped_process_absence_exact "maintenance-rollback-after"' in helper
    assert 'MAINTENANCE_TRANSITION_STARTED="false"' in helper
