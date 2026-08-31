# V6 진실 감사의 보수적 증거 상태와 30분 범위 판정을 회귀 검증한다.

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts import audit_v6_system_truth as audit
from scripts import stage_macos_release


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("PASS", "PASS"),
        ("PASS_WITH_NOT_RUN", "NOT_RUN"),
        ("NOT_RUN", "NOT_RUN"),
        ("NOT_PROVEN", "NOT_PROVEN"),
        ("BLOCKED", "BLOCKED"),
        ("FAIL", "FAIL"),
        ("PARTIAL", "NOT_PROVEN"),
        (None, "NOT_PROVEN"),
    ],
)
def test_normalize_evidence_status_is_conservative(
    raw_status: object,
    expected: str,
) -> None:
    assert audit._normalize_evidence_status(raw_status) == expected


def test_missing_and_not_run_evidence_never_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    assert audit._load_evidence("evidence/missing.json")["status"] == "NOT_RUN"

    evidence = tmp_path / "evidence/not-run.json"
    evidence.parent.mkdir()
    evidence.write_text(
        json.dumps({"status": "NOT_RUN", "checks": {"not_executed": False}}),
        encoding="utf-8",
    )
    assert audit._load_evidence(
        "evidence/not-run.json",
        require_checks=True,
    )["status"] == "NOT_RUN"


def test_declared_pass_with_failed_check_is_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    evidence = tmp_path / "evidence/result.json"
    evidence.parent.mkdir()
    evidence.write_text(
        json.dumps({"status": "PASS", "checks": {"required": False}}),
        encoding="utf-8",
    )
    assert audit._load_evidence(
        "evidence/result.json",
        require_checks=True,
    )["status"] == "FAIL"


def _artifact_record(
    root: Path,
    relative_path: str,
    *,
    kind: str,
    content: bytes,
) -> dict[str, object]:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "kind": kind,
        "path": relative_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_count": len(content),
    }


@pytest.mark.parametrize(
    ("kind", "schema_version", "require_release_binding", "expected_reason"),
    [
        (
            "dashboard_payload_benchmark",
            2,
            False,
            "BENCHMARK_REQUIRED_CHECK_SET_MISMATCH",
        ),
        (
            "browser_e2e_after_latest_change",
            1,
            True,
            "BROWSER_REQUIRED_CHECK_SET_MISMATCH",
        ),
        (
            "full_suite_after_latest_change",
            1,
            False,
            "FULL_SUITE_REQUIRED_CHECK_SET_MISMATCH",
        ),
    ],
)
def test_fabricated_check_name_cannot_pass_required_evidence_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    schema_version: int,
    require_release_binding: bool,
    expected_reason: str,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    commit = "a" * 40
    relative_path = f"evidence/{kind}.json"
    path = tmp_path / relative_path
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "generated_ts_utc": "2026-08-31T12:00:00Z",
                "source_commit": commit,
                "source_worktree_clean_at_measurement": True,
                "release_commit": commit,
                "release_isolated": True,
                "status": "PASS",
                "checks": {"totally_fabricated": True},
            }
        ),
        encoding="utf-8",
    )

    result = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind=kind,
        expected_schema_version=schema_version,
        expected_source_commit=commit,
        source_working_tree_changes=[],
        require_release_binding=require_release_binding,
    )

    assert result["status"] == "NOT_PROVEN"
    assert result["reason"] == expected_reason


def test_generic_pass_checks_require_a_declared_evidence_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    path = tmp_path / "evidence/generic.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "generated_ts_utc": "2026-08-31T12:00:00Z",
                "status": "PASS",
                "checks": {"totally_fabricated": True},
            }
        ),
        encoding="utf-8",
    )

    result = audit._load_evidence("evidence/generic.json", require_checks=True)

    assert result["status"] == "NOT_PROVEN"
    assert result["reason"] == "EVIDENCE_KIND_REQUIRED_FOR_CHECK_VALIDATION"


def test_dashboard_benchmark_pass_requires_real_log_and_recomputed_ratios(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    commit = "b" * 40
    artifact = _artifact_record(
        tmp_path,
        "evidence/logs/dashboard-benchmark.log",
        kind="log",
        content=b"dashboard benchmark passed\n",
    )
    relative_path = "evidence/dashboard-benchmark.json"
    path = tmp_path / relative_path
    latency = {
        name: {"iterations": 50, "p95_ms": 1.0}
        for name in (
            "ui_summary",
            "strategy_list",
            "selected_family_detail",
            "single_tick_delta",
        )
    }
    payload: dict[str, Any] = {
        "schema_version": 2,
        "generated_ts_utc": "2026-08-31T12:00:00Z",
        "source_commit": commit,
        "source_worktree_clean_at_measurement": True,
        "status": "PASS",
        "checks": {name: True for name in audit.BENCHMARK_REQUIRED_CHECKS},
        "command": ["uv", "run", "python", "scripts/benchmark_dashboard_payload.py"],
        "exit_code": 0,
        "artifact_count": 1,
        "artifacts": [artifact],
        "fixture_events": 100,
        "payload": {
            "dashboard_payload_bytes": 1_000,
            "summary_payload_bytes": 400,
            "strategy_summary_payload_bytes": 300,
            "summary_to_dashboard_ratio": 0.4,
            "strategy_summary_to_dashboard_ratio": 0.3,
            "target_summary_ratio_strictly_less_than": 0.5,
            "target_strategy_ratio_strictly_less_than": 0.35,
        },
        "websocket_chart_delta": {
            "message_type": "chart_delta",
            "refresh_required": False,
            "point_upserts": 1,
            "candle_upserts": 1,
            "delta_envelope_bytes": 100,
            "full_chart_bytes": 500,
            "delta_to_full_chart_ratio": 0.2,
        },
        "transform_latency": latency,
        "paper_only": True,
        "real_orders_enabled": False,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="dashboard_payload_benchmark",
        expected_schema_version=2,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert result["status"] == "PASS"

    payload["payload"]["summary_payload_bytes"] = 600
    path.write_text(json.dumps(payload), encoding="utf-8")
    contradicted = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="dashboard_payload_benchmark",
        expected_schema_version=2,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert contradicted["status"] == "FAIL"
    assert contradicted["reason"] == "BENCHMARK_RECOMPUTED_TARGET_OR_DELTA_MISMATCH"


def test_browser_e2e_pass_requires_actual_log_screenshots_and_positive_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    commit = "c" * 40
    artifacts = [
        _artifact_record(
            tmp_path,
            "evidence/logs/browser.log",
            kind="log",
            content=b"playwright passed\n",
        ),
        *[
            _artifact_record(
                tmp_path,
                f"evidence/screenshots/{project}.png",
                kind="screenshot",
                content=f"{project} screenshot".encode(),
            )
            for project in ("desktop", "tablet", "mobile")
        ],
    ]
    relative_path = "evidence/browser.json"
    path = tmp_path / relative_path
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_ts_utc": "2026-08-31T12:00:00Z",
                "source_commit": commit,
                "source_worktree_clean_at_measurement": True,
                "release_commit": commit,
                "release_isolated": True,
                "status": "PASS",
                "checks": {name: True for name in audit.BROWSER_REQUIRED_CHECKS},
                "command": ["pnpm", "exec", "playwright", "test"],
                "exit_code": 0,
                "artifact_count": len(artifacts),
                "artifacts": artifacts,
                "counts": {
                    "page_count": 4,
                    "project_count": 3,
                    "test_count": 12,
                    "screenshot_count": 3,
                    "console_error_count": 0,
                },
                "page_ids": audit.EXPECTED_PAGE_IDS,
                "projects": ["desktop", "tablet", "mobile"],
                "runtime_url": "http://127.0.0.1:8870",
            }
        ),
        encoding="utf-8",
    )

    result = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="browser_e2e_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
        require_release_binding=True,
    )

    assert result["status"] == "PASS"


def test_full_suite_pass_requires_each_command_log_and_positive_test_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    commit = "d" * 40
    artifacts = []
    commands = []
    for name in sorted(audit.FULL_SUITE_COMMAND_NAMES):
        relative_log = f"evidence/logs/{name}.log"
        artifacts.append(
            _artifact_record(
                tmp_path,
                relative_log,
                kind="log",
                content=f"{name} passed\n".encode(),
            )
        )
        commands.append(
            {
                "name": name,
                "command": ["run", name],
                "exit_code": 0,
                "log_path": relative_log,
            }
        )
    relative_path = "evidence/full-suite.json"
    path = tmp_path / relative_path
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_ts_utc": "2026-08-31T12:00:00Z",
                "source_commit": commit,
                "source_worktree_clean_at_measurement": True,
                "status": "PASS",
                "checks": {name: True for name in audit.FULL_SUITE_REQUIRED_CHECKS},
                "commands": commands,
                "counts": {
                    "command_count": len(commands),
                    "backend_test_count": 125,
                    "frontend_test_count": 26,
                },
                "artifact_count": len(artifacts),
                "artifacts": artifacts,
            }
        ),
        encoding="utf-8",
    )

    result = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="full_suite_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )

    assert result["status"] == "PASS"


def test_wave142_soak_requires_current_v6_strategy_and_account_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    path = tmp_path / audit.EVIDENCE_PATHS["thirty_minute_soak"]
    path.parent.mkdir()
    commit = "a" * 40
    strategy_ids = [f"STRATEGY_{index:02d}" for index in range(15)]
    account_ids = [
        f"{strategy_id}:{profile}"
        for strategy_id in strategy_ids
        for profile in ("BASE", "STRESS")
    ]
    mode_counts = {"ACTIVE": 0, "SHADOW": 6, "OFF": 9}
    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_ts_utc": "2026-08-31T12:00:00Z",
        "source_commit": commit,
        "source_worktree_clean_at_measurement": True,
        "release_commit": commit,
        "release_isolated_throughout": True,
        "run_id": "run-v6-soak",
        "status": "PASS",
        "requested_duration_seconds": 1800,
        "observed_duration_seconds": 1800.1,
        "checks": {name: True for name in audit.SOAK_REQUIRED_CHECKS},
        "strategy_ids": strategy_ids[:11],
        "league_account_ids": account_ids[:22],
        "strategy_mode_counts": {"ACTIVE": 0, "SHADOW": 6, "OFF": 5},
        "final": {
            "strategy_count": 11,
            "league_account_count": 22,
            "position_count": 0,
            "main_pending_entry_count": 0,
            "league_pending_entry_count": 0,
            "total_pending_entry_count": 0,
            "paper_portfolio_flat": True,
            "execution_state": "PAPER",
            "real_orders_enabled": False,
            "auth_required": False,
        },
        "paper_safety": {
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_requested": False,
            "api_key_requested": False,
            "wallet_requested": False,
            "additional_market_connection_started": False,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    stale = audit._thirty_minute_soak_evidence(
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=mode_counts,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert stale["status"] == "NOT_PROVEN"
    assert stale["reason"] == "SOAK_V6_STRATEGY_ACCOUNT_OR_MODE_SCOPE_MISMATCH"

    payload["final"]["strategy_count"] = 15
    payload["final"]["league_account_count"] = 30
    payload["strategy_ids"] = strategy_ids
    payload["league_account_ids"] = account_ids
    payload["strategy_mode_counts"] = mode_counts
    path.write_text(json.dumps(payload), encoding="utf-8")
    current = audit._thirty_minute_soak_evidence(
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=mode_counts,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert current["status"] == "PASS"
    assert current["validated_runtime_observation"] == {
        "evidence_path": audit.EVIDENCE_PATHS["thirty_minute_soak"],
        "generated_ts_utc": "2026-08-31T12:00:00Z",
        "run_id": "run-v6-soak",
        "source_commit": commit,
        "release_commit": commit,
        "release_isolated": True,
        "strategy_mode_counts": mode_counts,
        "open_positions": 0,
        "main_pending_entry_count": 0,
        "league_pending_entry_count": 0,
        "total_pending_entry_count": 0,
        "paper_portfolio_flat": True,
        "history_raw_rows": None,
        "unique_opportunities": None,
        "analytics_cache_ready": None,
    }

    del payload["run_id"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    unbound = audit._thirty_minute_soak_evidence(
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=mode_counts,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert unbound["status"] == "NOT_PROVEN"
    assert unbound["reason"] == "SOAK_RUN_BINDING_MISSING"
    assert "validated_runtime_observation" not in unbound


def test_pass_evidence_requires_schema_timestamp_clean_source_and_isolated_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    path = tmp_path / "evidence/result.json"
    path.parent.mkdir()
    commit = "b" * 40
    complete = {
        "schema_version": 1,
        "generated_ts_utc": "2026-08-31T12:00:00Z",
        "source_commit": commit,
        "source_worktree_clean_at_measurement": True,
        "release_commit": commit,
        "release_isolated": True,
        "status": "PASS",
        "checks": {"required": True},
    }

    for missing_field in (
        "schema_version",
        "generated_ts_utc",
        "source_commit",
        "source_worktree_clean_at_measurement",
        "release_commit",
        "release_isolated",
    ):
        payload = dict(complete)
        del payload[missing_field]
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = audit._load_evidence(
            "evidence/result.json",
            expected_schema_version=1,
            expected_source_commit=commit,
            source_working_tree_changes=[],
            require_release_binding=True,
        )
        assert result["status"] == "NOT_PROVEN", missing_field

    path.write_text(json.dumps(complete), encoding="utf-8")
    proven = audit._load_evidence(
        "evidence/result.json",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
        require_release_binding=True,
    )
    assert proven["status"] == "PASS"

    dirty = audit._load_evidence(
        "evidence/result.json",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[" M backend/app/runtime.py"],
        require_release_binding=True,
    )
    assert dirty["status"] == "NOT_PROVEN"
    assert dirty["reason"] == "UNCOMMITTED_SOURCE_DIFFERS_FROM_EVIDENCE"


def _valid_runtime_observation(commit: str) -> dict[str, Any]:
    strategy_ids = [f"STRATEGY_{index:02d}" for index in range(15)]
    return {
        "available": True,
        "observation_status": "PASS",
        "service_state": "LIVE_PAPER_MANUALLY_PAUSED_FLAT",
        "release_commit": commit,
        "release_isolated": True,
        "runtime_mode": "LIVE_SHADOW_PAPER",
        "market_data_state": "LIVE",
        "execution_state": "PAPER",
        "operation_state": "MANUALLY_PAUSED",
        "market_observation_active": True,
        "paper_entry_active": False,
        "manual_pause_requested": True,
        "pending_scope_valid": True,
        "main_pending_entry_count": 0,
        "league_pending_entry_count": 0,
        "total_pending_entry_count": 0,
        "total_open_position_count": 0,
        "paper_portfolio_flat": True,
        "flat": True,
        "strategy_ids": strategy_ids,
        "league_account_ids": [
            f"{strategy_id}:{profile}"
            for strategy_id in strategy_ids
            for profile in ("BASE", "STRESS")
        ],
        "league_account_count": 30,
        "strategy_mode_counts": {"ACTIVE": 0, "SHADOW": 6, "OFF": 9},
        "settings_summary_error": None,
        "runtime_safety": {
            "paper_only": True,
            "actual_orders_zero": True,
            "auth_zero": True,
            "private_api_zero": True,
            "api_key_zero": True,
            "wallet_zero": True,
            "runtime_ai_order_decision_zero": True,
            "funding_not_ready": True,
        },
        "runtime_safety_observed": {
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_enabled": False,
            "api_key_enabled": False,
            "wallet_enabled": False,
            "runtime_ai_order_decision_enabled": False,
            "funding_readiness": "NOT_READY",
        },
    }


def test_runtime_report_fields_do_not_fallback_when_runtime_is_unavailable() -> None:
    fields = audit._runtime_report_fields(
        {
            "available": False,
            "runtime_safety_observed": {
                "paper_only": True,
                "real_orders_enabled": False,
                "funding_readiness": "NOT_READY",
            },
            "total_open_position_count": 0,
            "main_pending_entry_count": 0,
        }
    )
    assert fields["runtime_strategy_ids"] is None
    assert fields["runtime_strategy_count"] is None
    assert fields["current_runtime_mode_counts"] is None
    assert fields["current_active_count"] is None
    assert fields["current_shadow_count"] is None
    assert fields["current_off_count"] is None
    assert fields["open_position_count"] is None
    assert fields["main_pending_entry_count"] is None
    assert fields["paper_portfolio_flat"] is None
    assert fields["paper_only"] is None
    assert fields["actual_orders_enabled"] is None
    assert fields["auth_required"] is None
    assert fields["private_api_enabled"] is None
    assert fields["api_key_enabled"] is None
    assert fields["wallet_enabled"] is None
    assert fields["runtime_ai_order_decision_enabled"] is None
    assert fields["funding_readiness"] == "NOT_PROVEN"
    assert fields["runtime_scalar_evidence"] == "NOT_PROVEN"


def test_runtime_report_fields_preserve_observed_unsafe_values() -> None:
    observation = _valid_runtime_observation("e" * 40)
    observation["runtime_safety_observed"] = {
        "paper_only": False,
        "real_orders_enabled": True,
        "auth_required": True,
        "private_api_enabled": True,
        "api_key_enabled": True,
        "wallet_enabled": True,
        "runtime_ai_order_decision_enabled": True,
        "funding_readiness": "READY",
    }
    fields = audit._runtime_report_fields(observation)
    assert fields["paper_only"] is False
    assert fields["actual_orders_enabled"] is True
    assert fields["auth_required"] is True
    assert fields["private_api_enabled"] is True
    assert fields["api_key_enabled"] is True
    assert fields["wallet_enabled"] is True
    assert fields["runtime_ai_order_decision_enabled"] is True
    assert fields["funding_readiness"] == "READY"
    assert fields["open_position_count"] == 0
    assert fields["total_pending_entry_count"] == 0
    assert fields["current_active_count"] == 0
    assert fields["current_shadow_count"] == 6
    assert fields["current_off_count"] == 9


def test_main_only_pending_entry_prevents_flat_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = {
        "status": {
            "run_id": "run-main-pending",
            "mode": "LIVE_SHADOW_PAPER",
            "market_data_state": "LIVE",
            "execution_state": "PAPER",
            "real_orders_enabled": False,
            "auth_required": False,
        },
        "system": {"release_commit": "f" * 40, "release_isolated": True},
        "paper_entry_intent": {
            "manual_pause_requested": True,
            "state": "PAUSED",
            "revision": 1,
            "reason": "TEST",
        },
        "operation_status": {
            "state": "MANUALLY_PAUSED",
            "market_observation_active": True,
            "paper_entry_active": False,
        },
        "focus_positions": [],
        "league_positions": [],
        "league_accounts": [],
        "strategies": [],
        "main_pending_entry_count": 1,
        "league_pending_entry_count": 0,
        "total_pending_entry_count": 1,
        "total_open_position_count": 0,
        "paper_portfolio_flat": True,
    }
    settings = {
        "safety": {
            "paper_only": True,
            "private_api_enabled": False,
            "api_key_enabled": False,
            "wallet_enabled": False,
            "runtime_ai_order_decision_enabled": False,
        },
        "funding_readiness": "NOT_READY",
    }
    monkeypatch.setattr(
        audit,
        "_localhost_json",
        lambda path: settings if path == "/api/settings/summary" else dashboard,
    )

    observation = audit._live_runtime_observation()

    assert observation["pending_scope_valid"] is True
    assert observation["main_pending_entry_count"] == 1
    assert observation["league_pending_entry_count"] == 0
    assert observation["total_pending_entry_count"] == 1
    assert observation["total_open_position_count"] == 0
    assert observation["flat"] is False
    assert observation["service_state"] == "LIVE_RUNTIME_CONTRACT_NOT_PROVEN"


def test_runtime_pass_requires_exact_isolated_v6_scope_and_zero_active() -> None:
    commit = "c" * 40
    observation = _valid_runtime_observation(commit)
    strategy_ids = list(observation["strategy_ids"])
    account_ids = list(observation["league_account_ids"])
    expected_modes = {"ACTIVE": 0, "SHADOW": 6, "OFF": 9}
    result = audit._runtime_contract_evidence(
        observation,
        latest_commit=commit,
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=expected_modes,
    )
    assert result["status"] == "PASS"

    old_release = dict(observation) | {"release_commit": "d" * 40}
    assert audit._runtime_contract_evidence(
        old_release,
        latest_commit=commit,
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=expected_modes,
    )["status"] == "NOT_PROVEN"

    unsafe_safety = dict(observation) | {
        "runtime_safety": dict(observation["runtime_safety"]) | {"actual_orders_zero": False},
        "runtime_safety_observed": dict(observation["runtime_safety_observed"])
        | {"real_orders_enabled": True},
    }
    assert audit._runtime_contract_evidence(
        unsafe_safety,
        latest_commit=commit,
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=expected_modes,
    )["status"] == "FAIL"

    active = dict(observation) | {
        "strategy_mode_counts": {"ACTIVE": 1, "SHADOW": 5, "OFF": 9}
    }
    assert audit._runtime_contract_evidence(
        active,
        latest_commit=commit,
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts={"ACTIVE": 1, "SHADOW": 5, "OFF": 9},
    )["status"] == "NOT_PROVEN"


def test_stopped_runtime_cannot_produce_overall_pass() -> None:
    evidence_statuses = {
        "dashboard_payload_benchmark": "PASS",
        "browser_e2e_after_latest_change": "PASS",
        "full_suite_after_latest_change": "PASS",
        "thirty_minute_soak": "PASS",
        "release_package": "PASS",
        "installed_release": "PASS",
        "remote_push": "PASS",
    }
    assert audit._overall_report_status(
        source_status="PASS",
        evidence_statuses=evidence_statuses,
        runtime_status="NOT_RUN",
    ) == "NOT_RUN_STOPPED_OR_UNREACHABLE_RUNTIME"


def test_release_package_pass_requires_current_direct_child_and_full_hash_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "a" * 40
    release = tmp_path / "releases" / commit
    release.mkdir(parents=True)
    manifest = {
        "schema_version": 2,
        "commit": commit,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "wallet_paths_enabled": False,
    }
    (release / "release-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (tmp_path / "current").symlink_to(Path("releases") / commit)
    verified: list[tuple[Path, str]] = []

    def verify_release_tree(path: Path, *, expected_commit: str) -> dict[str, Any]:
        verified.append((path, expected_commit))
        return manifest

    monkeypatch.setattr(audit, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(
        stage_macos_release,
        "_verify_release_tree",
        verify_release_tree,
    )
    result = audit._release_package_evidence(
        {"release_commit": commit},
        latest_commit=commit,
        working_tree_changes=[],
    )
    assert result["status"] == "PASS"
    assert verified == [(release, commit)]
