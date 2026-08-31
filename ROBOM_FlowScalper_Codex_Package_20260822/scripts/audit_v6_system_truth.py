# V6 소스·설치·마지막 PAPER 실행 기준선을 기계판독 증거로 고정한다.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import plistlib
import re
import statistics
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Mapping
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app.analytics.opportunities import OpportunityKey
from backend.app.build_identity import STRATEGY_VERSION
from backend.app.ops.service_soak import (
    RunningServiceSample,
    RunningServiceSoakThresholds,
    StrategyState,
    summarize_running_service_soak,
)
from backend.app.strategies.family import (
    FAMILY_CATALOG,
    STRATEGY_VARIANT_CONTRACTS,
    validate_family_contract,
)
from backend.app.strategies.orderflow_confirmation import (
    ORDERFLOW_AFFECTED_STRATEGY_IDS,
    ORDERFLOW_CONFIRMATION_FILTER_ID,
    OrderflowConfirmationRuntime,
)
from backend.app.strategies.registry import StrategyRegistry
from backend.app.ui_v6 import compact_ui_summary, diagnostics_rows, settings_summary
from scripts import stage_macos_release
from scripts.verify_legacy_runtime_preflight import (
    LegacyRuntimePreflightError,
    verify_running_process_binding,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "evidence/V6_CURRENT_SYSTEM_TRUTH.json"
RUNTIME_ROOT = Path("/Volumes/ROBOM_FLOWSCALPER/05_RUNTIME/ROBOM_FlowScalper")
LAUNCH_AGENT_LABEL = "kr.robom.flowscalper"
LAUNCH_AGENT_PLIST_PATH = Path.home() / "Library/LaunchAgents/kr.robom.flowscalper.plist"
LAUNCH_AGENT_EVIDENCE_BOUNDARY = "EXACT_INSTALLED_PLIST_AND_CURRENT_RUNNING_PROCESS"
INSTALLED_RELEASE_EVIDENCE_BOUNDARY = (
    "CURRENT_DEPLOYMENT_RELEASE_TREE_EXACT_LAUNCH_AGENT_AND_RUNNING_PROCESS"
)
BASELINE_COMMIT = "ac5634a53da623721dc3bb6113427a32d4a677db"
EXPECTED_PAGE_IDS = ["market", "strategies", "trades", "settings"]
EXPECTED_OPPORTUNITY_KEY_FIELDS = [
    "run_id",
    "strategy_id",
    "strategy_version",
    "opportunity_id",
    "symbol",
    "side",
]
EXPECTED_ORDERFLOW_AFFECTED_STRATEGY_IDS = (
    "TREND_PULLBACK_RECLAIM_15M_V2",
    "BREAKOUT_RETEST_30M_V2",
)
REPORT_RELATIVE_PATH = "evidence/V6_CURRENT_SYSTEM_TRUTH.json"
EVIDENCE_PATHS = {
    "v2_v3_comparison": "evidence/V6_V2_V3_COMPARISON.json",
    "dashboard_payload_benchmark": "evidence/V6_DASHBOARD_PAYLOAD_BENCHMARK.json",
    "browser_e2e_after_latest_change": "evidence/V6_ACTUAL_8870_BROWSER.json",
    "full_suite_after_latest_change": "evidence/V6_FINAL_VALIDATION.json",
    "thirty_minute_soak": "evidence/WAVE142_V6_RUNNING_SERVICE_SOAK_30M.json",
}
BENCHMARK_REQUIRED_CHECKS = frozenset(
    {
        "summary_is_less_than_half_dashboard",
        "strategy_summary_is_less_than_35_percent_dashboard",
        "summary_omits_history",
        "summary_omits_strategy_detail",
        "summary_omits_account_detail",
        "single_tick_uses_incremental_chart_delta",
        "single_tick_upserts_one_point",
        "single_tick_upserts_at_most_one_candle",
        "chart_delta_is_smaller_than_full_chart",
        "paper_only",
        "real_orders_disabled",
        "auth_not_required",
    }
)
BENCHMARK_RAW_FORMATS = frozenset(
    {
        "dashboard_payload_json",
        "summary_payload_json",
        "strategy_summary_payload_json",
        "chart_delta_message_json",
        "full_chart_payload_json",
        "dashboard_latency_samples_json",
    }
)
BROWSER_REQUIRED_CHECKS = frozenset(
    {
        "all_four_pages_rendered",
        "desktop_project_passed",
        "tablet_project_passed",
        "mobile_project_passed",
        "keyboard_navigation_passed",
        "escape_and_focus_restore_passed",
        "interactive_targets_48px_passed",
        "horizontal_overflow_zero",
        "console_errors_zero",
        "zoom_200_percent_reflow_passed",
        "paper_safety_visible",
    }
)
BROWSER_EXPECTED_PAPER_SAFETY = {
    "paper_only": True,
    "real_orders_enabled": False,
    "auth_required": False,
    "private_api_enabled": False,
    "api_key_enabled": False,
    "wallet_enabled": False,
    "runtime_ai_order_decision_enabled": False,
    "funding_readiness": "NOT_READY",
}
BROWSER_PAPER_SAFETY_SOURCE_PATHS = {
    "paper_only": "risk.paper_only",
    "real_orders_enabled": "status.real_orders_enabled",
    "auth_required": "status.auth_required",
    "private_api_enabled": "system.private_api_enabled",
    "api_key_enabled": "system.api_key_enabled",
    "wallet_enabled": "system.wallet_enabled",
    "runtime_ai_order_decision_enabled": ("system.runtime_ai_order_decision_enabled"),
    "funding_readiness": "system.funding_readiness",
}
BROWSER_REQUIRED_TEST_PROJECTS = {
    "all_four_pages_rendered": frozenset({"desktop", "tablet", "mobile"}),
    "desktop_project_passed": frozenset({"desktop"}),
    "tablet_project_passed": frozenset({"tablet"}),
    "mobile_project_passed": frozenset({"mobile"}),
    "keyboard_navigation_passed": frozenset({"desktop", "tablet", "mobile"}),
    "escape_and_focus_restore_passed": frozenset({"desktop", "tablet", "mobile"}),
    "interactive_targets_48px_passed": frozenset({"desktop", "tablet", "mobile"}),
    "horizontal_overflow_zero": frozenset({"desktop", "tablet", "mobile"}),
    "console_errors_zero": frozenset({"desktop", "tablet", "mobile"}),
    "zoom_200_percent_reflow_passed": frozenset({"desktop"}),
    "paper_safety_visible": frozenset({"desktop", "tablet", "mobile"}),
}
FULL_SUITE_REQUIRED_CHECKS = frozenset(
    {
        "backend_pytest_passed",
        "backend_ruff_passed",
        "backend_mypy_passed",
        "frontend_tests_passed",
        "frontend_lint_passed",
        "frontend_typecheck_passed",
        "frontend_build_passed",
        "build_safety_passed",
        "setup_passed",
        "master_e2e_passed",
        "network_smoke_passed",
        "security_scan_passed",
        "repo_hygiene_passed",
    }
)
FULL_SUITE_COMMAND_ORDER = (
    "setup",
    "backend_pytest",
    "frontend_tests",
    "backend_ruff",
    "frontend_lint",
    "backend_mypy",
    "frontend_typecheck",
    "frontend_build",
    "build_safety",
    "master_e2e",
    "network_smoke",
    "security_scan",
    "repo_hygiene",
)
FULL_SUITE_COMMAND_NAMES = frozenset(FULL_SUITE_COMMAND_ORDER)
FULL_SUITE_REPORT_FORMATS = {
    "backend_pytest": "pytest_junit_xml",
    "backend_ruff": "ruff_json",
    "backend_mypy": "mypy_text",
    "frontend_tests": "vitest_json",
    "frontend_lint": "eslint_json",
    "frontend_typecheck": "tsc_list_files_text",
    "frontend_build": "vite_build_text",
    "build_safety": "paper_build_safety_text",
    "setup": "setup_validation_json",
    "master_e2e": "master_e2e_bundle_json",
    "network_smoke": "network_smoke_json",
    "security_scan": "security_scan_json",
    "repo_hygiene": "repo_hygiene_json",
}
FULL_SUITE_COMMAND_TOKENS = {
    "backend_pytest": ("pytest", "junit"),
    "backend_ruff": ("ruff", "json"),
    "backend_mypy": ("mypy",),
    "frontend_tests": ("vitest", "json"),
    "frontend_lint": ("eslint", "json"),
    "frontend_typecheck": ("tsc", "listFiles"),
    "frontend_build": ("vite", "build"),
    "build_safety": ("assert_build_safety.py",),
    "setup": ("make", "setup"),
    "master_e2e": ("make", "e2e"),
    "network_smoke": ("make", "network-smoke"),
    "security_scan": ("make", "security-scan"),
    "repo_hygiene": ("make", "repo-hygiene"),
}
FULL_SUITE_CANONICAL_COMMANDS = {
    "backend_pytest": (
        "uv",
        "run",
        "pytest",
        "--junitxml={report_path}",
    ),
    "backend_ruff": (
        "uv",
        "run",
        "ruff",
        "check",
        "backend",
        "--output-format",
        "json",
        "--output-file",
        "{report_path}",
    ),
    "backend_mypy": ("uv", "run", "mypy"),
    "frontend_tests": (
        "pnpm",
        "--dir",
        "frontend",
        "exec",
        "vitest",
        "run",
        "tests",
        "--environment",
        "jsdom",
        "--reporter=json",
        "--outputFile={report_path}",
    ),
    "frontend_lint": (
        "pnpm",
        "--dir",
        "frontend",
        "exec",
        "eslint",
        ".",
        "--format",
        "json",
        "--output-file",
        "{report_path}",
    ),
    "frontend_typecheck": (
        "pnpm",
        "--dir",
        "frontend",
        "exec",
        "tsc",
        "-b",
        "--pretty",
        "false",
        "--listFiles",
    ),
    "frontend_build": ("pnpm", "--dir", "frontend", "exec", "vite", "build"),
    "build_safety": ("uv", "run", "python", "scripts/assert_build_safety.py"),
    "setup": ("make", "setup"),
    "master_e2e": ("make", "e2e"),
    "network_smoke": ("make", "network-smoke"),
    "security_scan": ("make", "security-scan"),
    "repo_hygiene": ("make", "repo-hygiene"),
}
FULL_SUITE_COLLECTION_COMMANDS = {
    "backend": ["uv", "run", "pytest", "--collect-only", "-q"],
    "frontend": [
        "pnpm",
        "--dir",
        "frontend",
        "exec",
        "vitest",
        "list",
        "tests",
        "--environment",
        "jsdom",
    ],
}
SOAK_REQUIRED_CHECKS = frozenset(
    {
        "samples_present",
        "requested_duration_completed",
        "same_run",
        "operation_samples_safe",
        "final_running_live_paper",
        "real_orders_disabled",
        "auth_not_required",
        "source_worktree_clean_at_start",
        "source_worktree_clean_at_end",
        "source_commit_stable",
        "release_commit_stable",
        "release_isolated_throughout",
        "source_release_commit_match",
        "strategy_ids_stable_and_observed",
        "league_account_ids_stable_and_observed",
        "strategy_mode_counts_stable_and_observed",
    }
)
SOAK_PROVENANCE_CHECKS = frozenset(
    {
        "source_worktree_clean_at_start",
        "source_worktree_clean_at_end",
        "source_commit_stable",
        "release_commit_stable",
        "release_isolated_throughout",
        "source_release_commit_match",
        "strategy_ids_stable_and_observed",
        "league_account_ids_stable_and_observed",
        "strategy_mode_counts_stable_and_observed",
    }
)
SOAK_AUDIT_SAMPLE_FIELDS = frozenset(
    {
        "main_pending_entry_count",
        "league_pending_entry_count",
        "total_pending_entry_count",
        "total_open_position_count",
        "paper_portfolio_flat",
        "league_account_ids",
        "release_commit",
        "release_isolated",
    }
)
CHECKED_EVIDENCE_KINDS_BY_PATH = {
    EVIDENCE_PATHS["dashboard_payload_benchmark"]: "dashboard_payload_benchmark",
    EVIDENCE_PATHS["browser_e2e_after_latest_change"]: "browser_e2e_after_latest_change",
    EVIDENCE_PATHS["full_suite_after_latest_change"]: "full_suite_after_latest_change",
    EVIDENCE_PATHS["thirty_minute_soak"]: "thirty_minute_soak",
}
EVIDENCE_SOURCE_PATHS = (
    "AGENTS.md",
    "Makefile",
    "VERSION",
    "pyproject.toml",
    "uv.lock",
    "backend",
    "config",
    "data",
    "frontend",
    "packaging",
    "ROBOM_FlowScalper.app",
    "ROBOM_FlowScalper.command",
    "schemas",
    "scripts",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _remote_main_commit() -> str | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(PROJECT_ROOT),
                "ls-remote",
                "--exit-code",
                "origin",
                "refs/heads/main",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    first_line = result.stdout.strip().splitlines()
    if len(first_line) != 1:
        return None
    commit, _, reference = first_line[0].partition("\t")
    if reference != "refs/heads/main" or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        return None
    return commit


def _working_tree_changes() -> list[str]:
    """생성 대상 자체를 제외한 현재 package 변경을 보고한다."""

    status = _git("status", "--porcelain", "--untracked-files=all", "--", ".")
    return [
        line
        for line in status.splitlines()
        if not line.removeprefix("?? ").endswith(REPORT_RELATIVE_PATH)
    ]


def _source_working_tree_changes() -> list[str]:
    """검증 대상 코드·계약 범위의 미커밋 변경만 반환한다."""

    status = _git(
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        *EVIDENCE_SOURCE_PATHS,
    )
    return status.splitlines()


def _commits_have_equivalent_source(left: str, right: str) -> bool:
    """문서·증거 전용 후속 commit을 허용하되 실행 소스 차이는 거부한다."""

    if not all(re.fullmatch(r"[0-9a-f]{40}", commit) for commit in (left, right)):
        return False
    result = subprocess.run(
        [
            "git",
            "-C",
            str(PROJECT_ROOT),
            "diff",
            "--quiet",
            left,
            right,
            "--",
            *EVIDENCE_SOURCE_PATHS,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _release_commit_matches_source(release_commit: object, source_commit: object) -> bool:
    """40자 commit끼리만 실행 소스 동등성을 비교한다."""

    return (
        isinstance(release_commit, str)
        and isinstance(source_commit, str)
        and re.fullmatch(r"[0-9a-f]{40}", release_commit) is not None
        and re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None
        and _commits_have_equivalent_source(release_commit, source_commit)
    )


def _valid_evidence_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed <= datetime.now(tz=UTC)


def _installed_deployment() -> dict[str, Any]:
    path = RUNTIME_ROOT / "current-deployment.json"
    if not path.exists():
        return {
            "status": "NOT_RUN",
            "release_commit": None,
            "paper_only": None,
            "real_orders_enabled": None,
            "auth_required": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "status": "FAIL",
            "release_commit": None,
            "paper_only": None,
            "real_orders_enabled": None,
            "auth_required": None,
        }
    if not isinstance(payload, dict):
        return {
            "status": "FAIL",
            "release_commit": None,
            "paper_only": None,
            "real_orders_enabled": None,
            "auth_required": None,
        }
    return {
        "status": "PASS",
        "release_commit": payload.get("release_commit"),
        "paper_only": payload.get("paper_only"),
        "real_orders_enabled": payload.get("real_orders_enabled"),
        "auth_required": payload.get("auth_required"),
    }


def _localhost_json(path: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:8870{path}",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"localhost 응답이 object가 아닙니다: {path}")
    return payload


def _strict_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _live_runtime_observation() -> dict[str, Any]:
    try:
        dashboard = _localhost_json("/api/dashboard")
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        return {
            "available": False,
            "observation_status": "NOT_RUN",
            "service_state": "STOPPED_OR_UNREACHABLE_NOT_RUN",
            "error_type": type(error).__name__,
        }
    settings_error: str | None = None
    try:
        settings = _localhost_json("/api/settings/summary")
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        settings = {}
        settings_error = type(error).__name__

    status = dashboard.get("status")
    system = dashboard.get("system")
    paper_intent = dashboard.get("paper_entry_intent")
    operation = dashboard.get("operation_status")
    safety = settings.get("safety")
    if (
        not isinstance(status, dict)
        or not isinstance(system, dict)
        or not isinstance(paper_intent, dict)
        or not isinstance(operation, dict)
    ):
        return {
            "available": False,
            "observation_status": "FAIL",
            "service_state": "INVALID_RUNTIME_PAYLOAD",
            "error_type": "INVALID_REQUIRED_OBJECT",
        }
    if not isinstance(safety, dict):
        safety = {}

    focus_positions = dashboard.get("focus_positions", [])
    league_positions = dashboard.get("league_positions", [])
    league_accounts = dashboard.get("league_accounts", [])
    strategies = dashboard.get("strategies", [])
    if not all(
        isinstance(value, list)
        for value in (focus_positions, league_positions, league_accounts, strategies)
    ):
        return {
            "available": False,
            "observation_status": "FAIL",
            "service_state": "INVALID_RUNTIME_PAYLOAD",
            "error_type": "INVALID_REQUIRED_LIST",
        }

    league_account_pending_counts = [
        _strict_non_negative_int(account.get("pending_entries"))
        for account in league_accounts
        if isinstance(account, dict)
    ]
    derived_league_pending_entries = (
        sum(count for count in league_account_pending_counts if count is not None)
        if len(league_account_pending_counts) == len(league_accounts)
        and all(count is not None for count in league_account_pending_counts)
        else None
    )
    main_pending_entry_count = _strict_non_negative_int(dashboard.get("main_pending_entry_count"))
    league_pending_entry_count = _strict_non_negative_int(
        dashboard.get("league_pending_entry_count")
    )
    total_pending_entry_count = _strict_non_negative_int(dashboard.get("total_pending_entry_count"))
    total_open_position_count = _strict_non_negative_int(dashboard.get("total_open_position_count"))
    paper_portfolio_flat = dashboard.get("paper_portfolio_flat")
    pending_scope_valid = (
        main_pending_entry_count is not None
        and league_pending_entry_count is not None
        and league_pending_entry_count == derived_league_pending_entries
        and total_pending_entry_count is not None
        and total_pending_entry_count == main_pending_entry_count + league_pending_entry_count
        and total_open_position_count is not None
        and isinstance(paper_portfolio_flat, bool)
    )
    flat = (
        pending_scope_valid
        and paper_portfolio_flat is True
        and total_open_position_count == 0
        and len(focus_positions) == 0
        and total_pending_entry_count == 0
    )
    mode_counts = {
        mode: sum(row.get("mode") == mode for row in strategies if isinstance(row, dict))
        for mode in ("ACTIVE", "SHADOW", "OFF")
    }
    runtime_account_ids = sorted(
        str(account["account_id"])
        for account in league_accounts
        if isinstance(account, dict) and account.get("account_id") is not None
    )
    runtime_safety_observed = {
        "paper_only": safety.get("paper_only"),
        "real_orders_enabled": status.get("real_orders_enabled"),
        "auth_required": status.get("auth_required"),
        "private_api_enabled": safety.get("private_api_enabled"),
        "api_key_enabled": safety.get("api_key_enabled"),
        "wallet_enabled": safety.get("wallet_enabled"),
        "runtime_ai_order_decision_enabled": safety.get("runtime_ai_order_decision_enabled"),
        "funding_readiness": settings.get("funding_readiness"),
    }
    runtime_safety = {
        "paper_only": runtime_safety_observed["paper_only"] is True,
        "actual_orders_zero": runtime_safety_observed["real_orders_enabled"] is False,
        "auth_zero": runtime_safety_observed["auth_required"] is False,
        "private_api_zero": runtime_safety_observed["private_api_enabled"] is False,
        "api_key_zero": runtime_safety_observed["api_key_enabled"] is False,
        "wallet_zero": runtime_safety_observed["wallet_enabled"] is False,
        "runtime_ai_order_decision_zero": (
            runtime_safety_observed["runtime_ai_order_decision_enabled"] is False
        ),
        "funding_not_ready": runtime_safety_observed["funding_readiness"] == "NOT_READY",
    }
    manual_pause = paper_intent.get("manual_pause_requested") is True
    market_observation = operation.get("market_observation_active") is True
    service_state = (
        "LIVE_PAPER_MANUALLY_PAUSED_FLAT"
        if manual_pause and market_observation and flat and all(runtime_safety.values())
        else "LIVE_RUNTIME_CONTRACT_NOT_PROVEN"
    )
    return {
        "available": True,
        "observation_status": "PASS",
        "service_state": service_state,
        "release_commit": system.get("release_commit"),
        "release_isolated": system.get("release_isolated"),
        "run_id": status.get("run_id"),
        "runtime_mode": status.get("mode"),
        "market_data_state": status.get("market_data_state"),
        "execution_state": status.get("execution_state"),
        "operation_state": operation.get("state"),
        "market_observation_active": market_observation,
        "paper_entry_active": operation.get("paper_entry_active"),
        "manual_pause_requested": manual_pause,
        "pause_state": paper_intent.get("state"),
        "pause_revision": paper_intent.get("revision"),
        "pause_reason": paper_intent.get("reason"),
        "open_position_count": total_open_position_count,
        "total_open_position_count": total_open_position_count,
        "focus_position_count": len(focus_positions),
        "main_pending_entry_count": main_pending_entry_count,
        "league_pending_entry_count": league_pending_entry_count,
        "total_pending_entry_count": total_pending_entry_count,
        "pending_scope_valid": pending_scope_valid,
        "paper_portfolio_flat": paper_portfolio_flat,
        "flat": flat,
        "strategy_mode_counts": mode_counts,
        "strategy_ids": sorted(
            str(row["strategy_id"])
            for row in strategies
            if isinstance(row, dict) and row.get("strategy_id") is not None
        ),
        "league_account_count": len(league_accounts),
        "league_account_ids": runtime_account_ids,
        "runtime_safety": runtime_safety,
        "runtime_safety_observed": runtime_safety_observed,
        "settings_summary_error": settings_error,
    }


def _runtime_report_fields(observation: dict[str, Any]) -> dict[str, Any]:
    """현재 runtime 스칼라를 소스·과거 값으로 fallback하지 않는다."""

    if observation.get("available") is not True:
        return {
            "runtime_strategy_ids": None,
            "runtime_strategy_count": None,
            "current_runtime_mode_counts": None,
            "current_active_count": None,
            "current_shadow_count": None,
            "current_off_count": None,
            "open_position_count": None,
            "total_open_position_count": None,
            "main_pending_entry_count": None,
            "league_pending_entry_count": None,
            "total_pending_entry_count": None,
            "paper_portfolio_flat": None,
            "paper_only": None,
            "actual_orders_enabled": None,
            "auth_required": None,
            "private_api_enabled": None,
            "api_key_enabled": None,
            "wallet_enabled": None,
            "runtime_ai_order_decision_enabled": None,
            "funding_readiness": "NOT_PROVEN",
            "runtime_scalar_evidence": "NOT_PROVEN",
            "wallet_runtime_evidence": "NOT_PROVEN",
        }

    raw_strategy_ids = observation.get("strategy_ids")
    runtime_strategy_ids = (
        list(raw_strategy_ids)
        if isinstance(raw_strategy_ids, list)
        and all(isinstance(value, str) for value in raw_strategy_ids)
        else None
    )
    raw_mode_counts = observation.get("strategy_mode_counts")
    runtime_mode_counts = dict(raw_mode_counts) if isinstance(raw_mode_counts, dict) else None
    observed_safety = observation.get("runtime_safety_observed")
    if not isinstance(observed_safety, dict):
        observed_safety = {}
    total_open_position_count = _strict_non_negative_int(
        observation.get("total_open_position_count")
    )
    paper_portfolio_flat = observation.get("paper_portfolio_flat")
    if not isinstance(paper_portfolio_flat, bool):
        paper_portfolio_flat = None
    wallet_value = observed_safety.get("wallet_enabled")
    return {
        "runtime_strategy_ids": runtime_strategy_ids,
        "runtime_strategy_count": (
            len(runtime_strategy_ids) if runtime_strategy_ids is not None else None
        ),
        "current_runtime_mode_counts": runtime_mode_counts,
        "current_active_count": (
            _strict_non_negative_int(runtime_mode_counts.get("ACTIVE"))
            if runtime_mode_counts is not None
            else None
        ),
        "current_shadow_count": (
            _strict_non_negative_int(runtime_mode_counts.get("SHADOW"))
            if runtime_mode_counts is not None
            else None
        ),
        "current_off_count": (
            _strict_non_negative_int(runtime_mode_counts.get("OFF"))
            if runtime_mode_counts is not None
            else None
        ),
        "open_position_count": total_open_position_count,
        "total_open_position_count": total_open_position_count,
        "main_pending_entry_count": _strict_non_negative_int(
            observation.get("main_pending_entry_count")
        ),
        "league_pending_entry_count": _strict_non_negative_int(
            observation.get("league_pending_entry_count")
        ),
        "total_pending_entry_count": _strict_non_negative_int(
            observation.get("total_pending_entry_count")
        ),
        "paper_portfolio_flat": paper_portfolio_flat,
        "paper_only": observed_safety.get("paper_only"),
        "actual_orders_enabled": observed_safety.get("real_orders_enabled"),
        "auth_required": observed_safety.get("auth_required"),
        "private_api_enabled": observed_safety.get("private_api_enabled"),
        "api_key_enabled": observed_safety.get("api_key_enabled"),
        "wallet_enabled": wallet_value,
        "runtime_ai_order_decision_enabled": observed_safety.get(
            "runtime_ai_order_decision_enabled"
        ),
        "funding_readiness": observed_safety.get(
            "funding_readiness",
            "NOT_PROVEN",
        ),
        "runtime_scalar_evidence": "CURRENT_LOCALHOST_RUNTIME",
        "wallet_runtime_evidence": (
            "CURRENT_SETTINGS_SUMMARY" if isinstance(wallet_value, bool) else "NOT_PROVEN"
        ),
    }


def _runtime_contract_evidence(
    observation: dict[str, Any],
    *,
    latest_commit: str,
    expected_strategy_ids: list[str],
    expected_account_ids: list[str],
    expected_mode_counts: dict[str, int],
) -> dict[str, Any]:
    """현재 8870이 최신 격리 릴리스와 정확한 V6 범위를 실행할 때만 PASS한다."""

    if not observation.get("available"):
        return {
            "status": str(observation.get("observation_status", "NOT_RUN")),
            "reason": str(observation.get("service_state", "RUNTIME_NOT_OBSERVED")),
            "checks": {},
        }
    runtime_safety = observation.get("runtime_safety")
    if not isinstance(runtime_safety, dict):
        return {
            "status": "FAIL",
            "reason": "RUNTIME_SAFETY_OBJECT_MISSING",
            "checks": {},
        }
    checks = {
        "manual_pause": observation.get("manual_pause_requested") is True,
        "market_observation_active": observation.get("market_observation_active") is True,
        "flat": observation.get("flat") is True,
        "runtime_mode_live_shadow_paper": (observation.get("runtime_mode") == "LIVE_SHADOW_PAPER"),
        "market_data_live": observation.get("market_data_state") == "LIVE",
        "execution_paper": observation.get("execution_state") == "PAPER",
        "operation_manually_paused": (observation.get("operation_state") == "MANUALLY_PAUSED"),
        "paper_entry_inactive": observation.get("paper_entry_active") is False,
        "pending_scope_valid": observation.get("pending_scope_valid") is True,
        "paper_portfolio_flat": observation.get("paper_portfolio_flat") is True,
        "main_pending_entries_zero": observation.get("main_pending_entry_count") == 0,
        "league_pending_entries_zero": (observation.get("league_pending_entry_count") == 0),
        "total_pending_entries_zero": (observation.get("total_pending_entry_count") == 0),
        "total_open_positions_zero": (observation.get("total_open_position_count") == 0),
        "release_commit_matches_head": _release_commit_matches_source(
            observation.get("release_commit"), latest_commit
        ),
        "release_isolated": observation.get("release_isolated") is True,
        "strategy_ids_exact": observation.get("strategy_ids") == sorted(expected_strategy_ids),
        "league_account_ids_exact": observation.get("league_account_ids")
        == sorted(expected_account_ids),
        "league_account_count_exact": observation.get("league_account_count")
        == len(expected_account_ids),
        "strategy_mode_counts_exact": observation.get("strategy_mode_counts")
        == expected_mode_counts,
        "runtime_active_count_zero": (
            isinstance(observation.get("strategy_mode_counts"), dict)
            and observation["strategy_mode_counts"].get("ACTIVE") == 0
        ),
        "settings_summary_loaded": observation.get("settings_summary_error") is None,
        **{f"safety_{key}": value is True for key, value in runtime_safety.items()},
    }
    failed_safety = [
        name for name, passed in checks.items() if name.startswith("safety_") and not passed
    ]
    observed_safety = observation.get("runtime_safety_observed")
    if not isinstance(observed_safety, dict):
        observed_safety = {}
    explicit_safety_violation = (
        observed_safety.get("paper_only") is False
        or observed_safety.get("real_orders_enabled") is True
        or observed_safety.get("auth_required") is True
        or observed_safety.get("private_api_enabled") is True
        or observed_safety.get("api_key_enabled") is True
        or observed_safety.get("wallet_enabled") is True
        or observed_safety.get("runtime_ai_order_decision_enabled") is True
        or observed_safety.get("funding_readiness") == "READY"
    )
    if explicit_safety_violation:
        return {
            "status": "FAIL",
            "reason": "RUNTIME_PAPER_SAFETY_CONTRACT_FAILED",
            "checks": checks,
        }
    if failed_safety:
        return {
            "status": "NOT_PROVEN",
            "reason": "RUNTIME_PAPER_SAFETY_FIELDS_NOT_PROVEN",
            "checks": checks,
        }
    if not all(checks.values()):
        return {
            "status": "NOT_PROVEN",
            "reason": "RUNTIME_RELEASE_OR_V6_SCOPE_MISMATCH",
            "checks": checks,
        }
    return {
        "status": "PASS",
        "reason": "LATEST_ISOLATED_V6_RUNTIME_MANUALLY_PAUSED_FLAT",
        "checks": checks,
    }


def _navigation_contract() -> tuple[list[str], list[str]]:
    source = (PROJECT_ROOT / "frontend/src/components/Navigation.tsx").read_text(encoding="utf-8")
    rows = re.findall(r"\{ id: '([^']+)', label: '([^']+)' \}", source)
    return [row[0] for row in rows], [row[1] for row in rows]


def _legacy_ui_paths() -> list[str]:
    relative_paths = (
        "frontend/src/pages/LivePage.tsx",
        "frontend/src/pages/LeaguePositionsPage.tsx",
        "frontend/src/pages/PerformancePage.tsx",
        "frontend/src/pages/ReplayPage.tsx",
        "frontend/src/pages/RiskPage.tsx",
        "frontend/src/pages/SystemPage.tsx",
        "frontend/src/pages/StrategySymbolPage.tsx",
    )
    return [path for path in relative_paths if (PROJECT_ROOT / path).exists()]


def _universal_70_gate_source_hits() -> list[str]:
    relative_paths = (
        "backend/app/strategies/governor.py",
        "backend/app/research/survivor_watchlist.py",
        "scripts/research_runtime_strategy_replay.py",
        "scripts/compare_strategy_gate_trials.py",
        "scripts/compare_all_strategy_gate_trials.py",
    )
    legacy_tokens = (
        "BASE_WIN_RATE_LT_0_70_OR_MISSING",
        "STRESS_WIN_RATE_LT_0_70_OR_MISSING",
        "BASE_WIN_RATE_BELOW_70",
        "STRESS_WIN_RATE_BELOW_70",
        "MINIMUM_RANKING_WIN_RATE",
        "MINIMUM_WIN_RATE =",
        "observed_70_percent_gate_passed",
        "win_rate_at_least_70_percent",
        '"minimum_win_rate_per_profile":',
    )
    return [
        f"{relative_path}:{line_number}:{token}"
        for relative_path in relative_paths
        for line_number, line in enumerate(
            (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").splitlines(),
            start=1,
        )
        for token in legacy_tokens
        if token in line
    ]


def _survivor_rank_uses_raw_win_rate() -> bool:
    source = (PROJECT_ROOT / "backend/app/research/survivor_watchlist.py").read_text(
        encoding="utf-8"
    )
    rank_source = source.split("def _candidate_rank_key", maxsplit=1)[1].split(
        "def _strict_evidence_dominance",
        maxsplit=1,
    )[0]
    return re.search(r"candidate\.(?:base|stress)_win_rate(?!_ci95_lower)", rank_source) is not None


def _api_transport_source_contract() -> dict[str, Any]:
    main_source = (PROJECT_ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    delta_source = (PROJECT_ROOT / "backend/app/ui_v6.py").read_text(encoding="utf-8")
    source = f"{main_source}\n{delta_source}"
    rest_paths = (
        "/api/ui/summary",
        "/api/settings/summary",
        "/api/diagnostics",
        "/api/strategy-families",
        "/api/strategy-families/{family_id}",
        "/api/strategy-families/{family_id}/conditions",
        "/api/trades",
    )
    websocket_message_types = (
        "snapshot",
        "summary_delta",
        "chart_delta",
        "position_delta",
        "strategy_row_delta",
        "selected_detail_delta",
        "heartbeat",
    )
    return {
        "rest_paths": list(rest_paths),
        "rest_paths_present": all(path in source for path in rest_paths),
        "websocket_path": "/ws/ui",
        "websocket_path_present": '@app.websocket("/ws/ui")' in main_source,
        "websocket_message_types": list(websocket_message_types),
        "websocket_message_types_present": all(
            f'"{message_type}"' in source for message_type in websocket_message_types
        ),
        "client_selection_message": "select_family",
        "client_selection_message_present": '"select_family"' in source,
        "selected_detail_omits": ["conditions", "history", "entry_rules_ko"],
    }


def _source_safety_contract() -> dict[str, Any]:
    expected_safe = {
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "api_key_enabled": False,
        "wallet_enabled": False,
        "runtime_ai_order_decision_enabled": False,
        "funding_readiness": "NOT_READY",
    }
    expected_unsafe = {
        "paper_only": False,
        "real_orders_enabled": True,
        "auth_required": True,
        "private_api_enabled": True,
        "api_key_enabled": True,
        "wallet_enabled": True,
        "runtime_ai_order_decision_enabled": True,
        "funding_readiness": "READY",
    }
    safe_snapshot: dict[str, object] = {
        "paper_only": True,
        "status": {"real_orders_enabled": False, "auth_required": False},
        "system": {
            "private_api_enabled": False,
            "api_key_enabled": False,
            "wallet_enabled": False,
            "runtime_ai_order_decision_enabled": False,
            "funding_readiness": "NOT_READY",
        },
    }
    unsafe_snapshot: dict[str, object] = {
        "paper_only": False,
        "status": {"real_orders_enabled": True, "auth_required": True},
        "system": {
            "private_api_enabled": True,
            "api_key_enabled": True,
            "wallet_enabled": True,
            "runtime_ai_order_decision_enabled": True,
            "funding_readiness": "READY",
        },
    }

    def surfaces(snapshot: Mapping[str, object]) -> dict[str, dict[str, object]]:
        compact = compact_ui_summary(snapshot)
        diagnostics = diagnostics_rows(snapshot)
        settings = settings_summary(snapshot)
        settings_safety = settings.get("safety")
        normalized_settings = dict(settings_safety) if isinstance(settings_safety, Mapping) else {}
        normalized_settings["funding_readiness"] = settings.get("funding_readiness")
        return {
            "summary": compact,
            "diagnostics": diagnostics,
            "settings": normalized_settings,
        }

    checks: dict[str, bool] = {}
    for case, snapshot, expected in (
        ("safe", safe_snapshot, expected_safe),
        ("unsafe", unsafe_snapshot, expected_unsafe),
    ):
        for surface_name, payload in surfaces(snapshot).items():
            for field, expected_value in expected.items():
                checks[f"{case}_{surface_name}_{field}"] = payload.get(field) == expected_value
    for surface_name, payload in surfaces({}).items():
        for field in expected_safe:
            checks[f"missing_{surface_name}_{field}_not_proven"] = (
                payload.get(field) == "NOT_PROVEN"
            )
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
    }


def _command_succeeded(payload: Mapping[str, object], *, token: str) -> bool:
    command = payload.get("command")
    if isinstance(command, str):
        normalized = command.strip()
    elif (
        isinstance(command, list)
        and command
        and all(isinstance(part, str) and part.strip() for part in command)
    ):
        normalized = " ".join(command)
    else:
        return False
    exit_code = payload.get("exit_code")
    return (
        token in normalized
        and isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code == 0
    )


def _positive_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        return None
    return normalized


def _validate_artifacts(
    payload: Mapping[str, object],
    *,
    required_kinds: frozenset[str],
) -> tuple[str, str] | None:
    records = payload.get("artifacts")
    artifact_count = _strict_non_negative_int(payload.get("artifact_count"))
    if (
        not isinstance(records, list)
        or not records
        or artifact_count is None
        or artifact_count <= 0
        or artifact_count != len(records)
    ):
        return "NOT_PROVEN", "EVIDENCE_ARTIFACT_COUNT_NOT_POSITIVE_OR_EXACT"

    try:
        project_root = PROJECT_ROOT.resolve(strict=True)
    except OSError:
        return "NOT_PROVEN", "EVIDENCE_PROJECT_ROOT_NOT_RESOLVABLE"
    observed_kinds: set[str] = set()
    observed_paths: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            return "NOT_PROVEN", "EVIDENCE_ARTIFACT_RECORD_INVALID"
        kind = record.get("kind")
        raw_path = record.get("path")
        expected_sha256 = record.get("sha256")
        expected_bytes = _strict_non_negative_int(record.get("byte_count"))
        if (
            not isinstance(kind, str)
            or kind not in {"artifact", "log", "screenshot"}
            or not isinstance(raw_path, str)
            or not raw_path.strip()
            or Path(raw_path).is_absolute()
            or not isinstance(expected_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or expected_bytes is None
            or expected_bytes <= 0
            or raw_path in observed_paths
        ):
            return "NOT_PROVEN", "EVIDENCE_ARTIFACT_METADATA_INVALID"
        source_path = PROJECT_ROOT / raw_path
        try:
            resolved_path = source_path.resolve(strict=True)
        except OSError:
            return "NOT_PROVEN", "EVIDENCE_ARTIFACT_FILE_NOT_FOUND"
        if (
            source_path.is_symlink()
            or not resolved_path.is_relative_to(project_root)
            or not resolved_path.is_file()
        ):
            return "NOT_PROVEN", "EVIDENCE_ARTIFACT_PATH_OUTSIDE_PROJECT_OR_NOT_FILE"
        if resolved_path.stat().st_size != expected_bytes:
            return "FAIL", "EVIDENCE_ARTIFACT_BYTE_COUNT_MISMATCH"
        digest = hashlib.sha256()
        with resolved_path.open("rb") as artifact_file:
            for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            return "FAIL", "EVIDENCE_ARTIFACT_SHA256_MISMATCH"
        observed_kinds.add(kind)
        observed_paths.add(raw_path)
    if not required_kinds.issubset(observed_kinds):
        return "NOT_PROVEN", "EVIDENCE_REQUIRED_ARTIFACT_KIND_MISSING"
    return None


def _artifact_records(payload: Mapping[str, object]) -> list[dict[str, Any]]:
    records = payload.get("artifacts")
    if not isinstance(records, list):
        return []
    return [dict(record) for record in records if isinstance(record, dict)]


def _artifact_with_format(
    payload: Mapping[str, object],
    artifact_format: str,
) -> dict[str, Any] | None:
    matches = [
        record for record in _artifact_records(payload) if record.get("format") == artifact_format
    ]
    return matches[0] if len(matches) == 1 else None


def _artifact_path(record: Mapping[str, object]) -> Path | None:
    raw_path = record.get("path")
    if not isinstance(raw_path, str):
        return None
    path = PROJECT_ROOT / raw_path
    return path if path.is_file() else None


def _json_artifact(record: Mapping[str, object]) -> object | None:
    path = _artifact_path(record)
    if path is None:
        return None
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
        return loaded
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _png_dimensions(record: Mapping[str, object]) -> tuple[int, int] | None:
    path = _artifact_path(record)
    if path is None:
        return None
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if len(payload) < 45 or payload[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    offset = 8
    header: bytes | None = None
    compressed = bytearray()
    ended = False
    while offset + 12 <= len(payload):
        chunk_length = int.from_bytes(payload[offset : offset + 4], "big")
        chunk_type = payload[offset + 4 : offset + 8]
        chunk_end = offset + 12 + chunk_length
        if chunk_end > len(payload):
            return None
        chunk_data = payload[offset + 8 : offset + 8 + chunk_length]
        expected_crc = int.from_bytes(
            payload[offset + 8 + chunk_length : chunk_end],
            "big",
        )
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            return None
        if chunk_type == b"IHDR":
            if header is not None or chunk_length != 13 or offset != 8:
                return None
            header = chunk_data
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            if chunk_data or chunk_end != len(payload):
                return None
            ended = True
            break
        offset = chunk_end
    if header is None or not compressed or not ended:
        return None
    width = int.from_bytes(header[:4], "big")
    height = int.from_bytes(header[4:8], "big")
    bit_depth, color_type, compression, filtering, interlace = header[8:]
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if (
        width <= 0
        or height <= 0
        or bit_depth != 8
        or channels is None
        or compression != 0
        or filtering != 0
        or interlace != 0
    ):
        return None
    row_bytes = width * channels
    try:
        raw_pixels = zlib.decompress(bytes(compressed))
    except zlib.error:
        return None
    if len(raw_pixels) != (row_bytes + 1) * height:
        return None

    previous = bytearray(row_bytes)
    observed_colors: set[bytes] = set()
    cursor = 0
    for _ in range(height):
        filter_type = raw_pixels[cursor]
        filtered = raw_pixels[cursor + 1 : cursor + 1 + row_bytes]
        cursor += row_bytes + 1
        if filter_type not in {0, 1, 2, 3, 4}:
            return None
        current = bytearray(row_bytes)
        for index, encoded in enumerate(filtered):
            left = current[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                estimate = left + above - upper_left
                left_distance = abs(estimate - left)
                above_distance = abs(estimate - above)
                upper_left_distance = abs(estimate - upper_left)
                predictor = (
                    left
                    if left_distance <= above_distance and left_distance <= upper_left_distance
                    else above
                    if above_distance <= upper_left_distance
                    else upper_left
                )
            current[index] = (encoded + predictor) & 0xFF
        for index in range(0, row_bytes, channels):
            observed_colors.add(bytes(current[index : index + channels]))
            if len(observed_colors) > 1:
                break
        previous = current
    return (width, height) if len(observed_colors) >= 16 else None


def _playwright_report_summary(
    report: Mapping[str, object],
) -> (
    tuple[
        int,
        set[str],
        dict[str, frozenset[str]],
        dict[tuple[str, str], frozenset[str]],
    ]
    | None
):
    config = report.get("config")
    stats = report.get("stats")
    suites = report.get("suites")
    errors = report.get("errors", [])
    if (
        not isinstance(config, dict)
        or not isinstance(stats, dict)
        or not isinstance(suites, list)
        or not suites
        or errors != []
    ):
        return None
    configured_projects = config.get("projects")
    if not isinstance(configured_projects, list):
        return None
    project_names = {
        str(project["name"])
        for project in configured_projects
        if isinstance(project, dict) and isinstance(project.get("name"), str)
    }
    if project_names != {"desktop", "tablet", "mobile"}:
        return None
    expected_count = _strict_non_negative_int(stats.get("expected"))
    if (
        expected_count is None
        or expected_count <= 0
        or stats.get("unexpected") != 0
        or stats.get("flaky") != 0
        or stats.get("skipped") != 0
    ):
        return None

    observed_projects: set[str] = set()
    passed_expected_count = 0
    observed_check_projects: dict[str, set[str]] = {
        check_id: set() for check_id in BROWSER_REQUIRED_CHECKS
    }
    screenshot_attachments: dict[tuple[str, str], set[str]] = {}
    observed_spec_ids: set[str] = set()

    def visit_suite(raw_suite: object) -> bool:
        nonlocal passed_expected_count
        if not isinstance(raw_suite, dict):
            return False
        nested = raw_suite.get("suites", [])
        specs = raw_suite.get("specs", [])
        if not isinstance(nested, list) or not isinstance(specs, list):
            return False
        if not all(visit_suite(child) for child in nested):
            return False
        for spec in specs:
            if not isinstance(spec, dict):
                return False
            title = spec.get("title")
            if not isinstance(title, str) or not title.startswith("audit:"):
                return False
            check_id = title.removeprefix("audit:")
            if check_id not in BROWSER_REQUIRED_CHECKS or check_id in observed_spec_ids:
                return False
            observed_spec_ids.add(check_id)
            tests = spec.get("tests")
            if not isinstance(tests, list):
                return False
            for test in tests:
                if not isinstance(test, dict):
                    return False
                project_name = test.get("projectName")
                expected_status = test.get("expectedStatus")
                results = test.get("results")
                if not isinstance(project_name, str) or project_name not in project_names:
                    return False
                observed_projects.add(project_name)
                if expected_status == "skipped":
                    continue
                if expected_status != "passed" or not isinstance(results, list) or not results:
                    return False
                final_result = results[-1]
                if (
                    not isinstance(final_result, dict)
                    or final_result.get("status") != "passed"
                    or final_result.get("errors", []) != []
                ):
                    return False
                attachments = final_result.get("attachments")
                if not isinstance(attachments, list):
                    return False
                attachment_paths = {
                    str(attachment["path"])
                    for attachment in attachments
                    if isinstance(attachment, dict)
                    and attachment.get("contentType") == "image/png"
                    and isinstance(attachment.get("path"), str)
                    and attachment["path"]
                }
                if not attachment_paths:
                    return False
                if project_name in observed_check_projects[check_id]:
                    return False
                observed_check_projects[check_id].add(project_name)
                screenshot_attachments[(check_id, project_name)] = attachment_paths
                passed_expected_count += 1
        return True

    if not all(visit_suite(suite) for suite in suites):
        return None
    expected_total = sum(len(projects) for projects in BROWSER_REQUIRED_TEST_PROJECTS.values())
    if (
        passed_expected_count != expected_count
        or passed_expected_count != expected_total
        or observed_projects != project_names
        or observed_spec_ids != BROWSER_REQUIRED_CHECKS
        or any(
            observed_check_projects[check_id] != expected_projects
            for check_id, expected_projects in BROWSER_REQUIRED_TEST_PROJECTS.items()
        )
    ):
        return None
    return (
        passed_expected_count,
        project_names,
        {check_id: frozenset(projects) for check_id, projects in observed_check_projects.items()},
        {key: frozenset(paths) for key, paths in screenshot_attachments.items()},
    )


def _file_sha256(path: Path) -> str | None:
    try:
        content = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(content).hexdigest()


def _embedded_artifact(
    report: Mapping[str, object],
    key: str,
    artifact_format: str,
) -> dict[str, Any] | None:
    raw_record = report.get(key)
    if not isinstance(raw_record, dict) or raw_record.get("format") != artifact_format:
        return None
    record = dict(raw_record)
    failure = _validate_artifacts(
        {"artifact_count": 1, "artifacts": [record]},
        required_kinds=frozenset({"artifact"}),
    )
    return record if failure is None else None


def _playwright_e2e_case_ids(
    report: Mapping[str, object],
    *,
    require_results: bool,
) -> tuple[set[str], int] | None:
    config = report.get("config")
    errors = report.get("errors")
    stats = report.get("stats")
    suites = report.get("suites")
    if (
        not isinstance(config, dict)
        or not isinstance(stats, dict)
        or errors != []
        or not isinstance(suites, list)
    ):
        return None
    projects = config.get("projects")
    project_names = (
        {
            project.get("name")
            for project in projects
            if isinstance(project, dict) and isinstance(project.get("name"), str)
        }
        if isinstance(projects, list)
        else set()
    )
    if project_names != {"desktop", "tablet", "mobile"}:
        return None
    case_ids: set[str] = set()
    passed_count = 0
    skipped_count = 0

    def visit_suite(raw_suite: object, inherited_file: str | None = None) -> bool:
        nonlocal passed_count, skipped_count
        if not isinstance(raw_suite, dict):
            return False
        suite_file = raw_suite.get("file", inherited_file)
        nested = raw_suite.get("suites", [])
        specs = raw_suite.get("specs", [])
        if not isinstance(nested, list) or not isinstance(specs, list):
            return False
        if not all(visit_suite(child, suite_file) for child in nested):
            return False
        for spec in specs:
            if not isinstance(spec, dict):
                return False
            title = spec.get("title")
            spec_file = spec.get("file", suite_file)
            tests = spec.get("tests")
            if (
                not isinstance(title, str)
                or not title
                or spec_file != "dashboard.spec.ts"
                or not isinstance(tests, list)
                or not tests
            ):
                return False
            for test in tests:
                if not isinstance(test, dict):
                    return False
                project = test.get("projectName")
                if not isinstance(project, str) or project not in project_names:
                    return False
                case_id = f"frontend/e2e/{spec_file}::{title}::{project}"
                if case_id in case_ids:
                    return False
                case_ids.add(case_id)
                if not require_results:
                    continue
                results = test.get("results")
                if not isinstance(results, list) or not results:
                    return False
                final_result = results[-1]
                status = final_result.get("status") if isinstance(final_result, dict) else None
                if status == "passed" and final_result.get("errors", []) == []:
                    passed_count += 1
                elif status == "skipped":
                    skipped_count += 1
                else:
                    return False
        return True

    if not all(visit_suite(suite) for suite in suites) or not case_ids:
        return None
    if require_results:
        expected = _strict_non_negative_int(stats.get("expected"))
        skipped = _strict_non_negative_int(stats.get("skipped"))
        unexpected = _strict_non_negative_int(stats.get("unexpected"))
        flaky = _strict_non_negative_int(stats.get("flaky"))
        if (
            expected != passed_count
            or skipped != skipped_count
            or unexpected != 0
            or flaky != 0
            or passed_count <= 0
            or passed_count + skipped_count != len(case_ids)
        ):
            return None
    return case_ids, passed_count


def _current_fixture_e2e_test_ids() -> set[str] | None:
    try:
        completed = subprocess.run(
            [
                "uv",
                "run",
                "pytest",
                "backend/tests/test_fixture_app.py",
                "--collect-only",
                "-q",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return _parse_collection_test_ids(completed.stdout, "pytest_collection_text")


def _current_master_e2e_case_ids() -> set[str] | None:
    try:
        completed = subprocess.run(
            [
                "pnpm",
                "--dir",
                "frontend",
                "exec",
                "playwright",
                "test",
                "--config",
                "playwright.config.ts",
                "--list",
                "--reporter=json",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    summary = (
        _playwright_e2e_case_ids(report, require_results=False)
        if isinstance(report, dict)
        else None
    )
    return summary[0] if summary is not None else None


def _current_machine_check_report(command: list[str]) -> dict[str, object] | None:
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    decoder = json.JSONDecoder()
    combined = completed.stdout + completed.stderr
    for index, character in enumerate(combined):
        if character != "{":
            continue
        try:
            loaded, _ = decoder.raw_decode(combined[index:])
        except json.JSONDecodeError:
            continue
        return dict(loaded) if isinstance(loaded, dict) else None
    return None


def _current_security_scan_report() -> dict[str, object] | None:
    return _current_machine_check_report(["uv", "run", "python", "scripts/security_scan.py"])


def _current_repo_hygiene_report() -> dict[str, object] | None:
    return _current_machine_check_report(
        ["uv", "run", "python", "scripts/check_repository_hygiene.py"]
    )


def _full_suite_report_count(name: str, record: Mapping[str, object]) -> int | None:
    path = _artifact_path(record)
    if path is None:
        return None
    if name == "backend_pytest":
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            return None

        def declared_count(node: ET.Element, attribute: str) -> int | None:
            raw_value = node.get(attribute)
            if raw_value is None or re.fullmatch(r"[0-9]+", raw_value) is None:
                return None
            return int(raw_value)

        def computed_counts(node: ET.Element) -> tuple[int, int, int, int] | None:
            if node.tag not in {"testsuite", "testsuites"}:
                return None
            tests = failures = errors = skipped = 0
            for case in node.findall("testcase"):
                children = [
                    child.tag for child in case if child.tag in {"failure", "error", "skipped"}
                ]
                if len(children) > 1:
                    return None
                tests += 1
                failures += children == ["failure"]
                errors += children == ["error"]
                skipped += children == ["skipped"]
            for child_suite in node.findall("testsuite"):
                child_counts = computed_counts(child_suite)
                if child_counts is None:
                    return None
                child_tests, child_failures, child_errors, child_skipped = child_counts
                tests += child_tests
                failures += child_failures
                errors += child_errors
                skipped += child_skipped
            declared = tuple(
                declared_count(node, attribute)
                for attribute in ("tests", "failures", "errors", "skipped")
            )
            actual = (tests, failures, errors, skipped)
            if node.tag == "testsuites" and declared == (None, None, None, None):
                return actual
            return actual if declared == actual else None

        counts = computed_counts(root)
        if counts is None:
            return None
        tests, failures, errors, skipped = counts
        junit_passed = tests - failures - errors - skipped
        return junit_passed if junit_passed > 0 and failures == 0 and errors == 0 else None
    if name in {
        "backend_ruff",
        "frontend_tests",
        "frontend_lint",
        "setup",
        "master_e2e",
        "network_smoke",
        "security_scan",
        "repo_hygiene",
    }:
        report = _json_artifact(record)
        if name == "backend_ruff":
            return 1 if report == [] else None
        if name == "frontend_tests":
            if not isinstance(report, dict):
                return None
            total = _strict_non_negative_int(report.get("numTotalTests"))
            passed = _strict_non_negative_int(report.get("numPassedTests"))
            failed = _strict_non_negative_int(report.get("numFailedTests"))
            test_results = report.get("testResults")
            report_ids = _frontend_vitest_test_ids(record)
            if (
                total is None
                or total <= 0
                or passed != total
                or failed != 0
                or report.get("success") is not True
                or not isinstance(test_results, list)
                or not test_results
                or report_ids is None
                or len(report_ids) != total
            ):
                return None
            return total
        if name == "frontend_lint":
            if not isinstance(report, list) or not report:
                return None
            if any(
                not isinstance(row, dict)
                or row.get("errorCount") != 0
                or row.get("fatalErrorCount", 0) != 0
                or row.get("messages") != []
                for row in report
            ):
                return None
            return len(report)
        if not isinstance(report, dict):
            return None
        if name == "setup":
            lock_sha256 = report.get("lock_sha256")
            python_path = PROJECT_ROOT / ".venv/bin/python"
            node_modules_marker = PROJECT_ROOT / "frontend/node_modules/.modules.yaml"
            expected_locks = {
                "uv.lock": _file_sha256(PROJECT_ROOT / "uv.lock"),
                "frontend/pnpm-lock.yaml": _file_sha256(PROJECT_ROOT / "frontend/pnpm-lock.yaml"),
            }
            try:
                python_target = python_path.resolve(strict=True)
                marker_size = node_modules_marker.stat().st_size
                python_mode = python_target.stat().st_mode
            except OSError:
                return None
            if (
                report.get("schema") != "flowscalper.setup_validation.v1"
                or report.get("command") != ["make", "setup"]
                or report.get("exit_code") != 0
                or lock_sha256 != expected_locks
                or any(value is None for value in expected_locks.values())
                or report.get("python_executable_path") != ".venv/bin/python"
                or not python_target.is_file()
                or python_mode & 0o111 == 0
                or report.get("node_modules_marker_path") != "frontend/node_modules/.modules.yaml"
                or marker_size <= 0
            ):
                return None
            return 2
        if name == "master_e2e":
            if (
                report.get("schema") != "flowscalper.master_e2e_bundle.v1"
                or report.get("command") != ["make", "e2e"]
                or report.get("exit_code") != 0
            ):
                return None
            pytest_record = _embedded_artifact(
                report,
                "pytest_junit",
                "master_e2e_pytest_junit_xml",
            )
            playwright_record = _embedded_artifact(
                report,
                "playwright_json",
                "master_e2e_playwright_json",
            )
            if pytest_record is None or playwright_record is None:
                return None
            pytest_count = _full_suite_report_count("backend_pytest", pytest_record)
            pytest_ids = _pytest_junit_test_ids(pytest_record)
            trusted_pytest_ids = _current_fixture_e2e_test_ids()
            playwright_report = _json_artifact(playwright_record)
            playwright_summary = (
                _playwright_e2e_case_ids(playwright_report, require_results=True)
                if isinstance(playwright_report, dict)
                else None
            )
            trusted_playwright_ids = _current_master_e2e_case_ids()
            if (
                pytest_count is None
                or pytest_ids is None
                or pytest_ids != trusted_pytest_ids
                or playwright_summary is None
                or playwright_summary[0] != trusted_playwright_ids
            ):
                return None
            return pytest_count + playwright_summary[1]
        if name == "network_smoke":
            positive_fields = (
                "eligible_symbol_count",
                "binance_catalog_count",
                "upbit_krw_catalog_count",
                "binance_btcusdt_3m_candle_count",
                "binance_catalog_tail_3m_candle_count",
                "upbit_krw_btc_3m_candle_count",
                "websocket_events",
            )
            event_samples = report.get("event_samples")
            started_ts = report.get("started_ts_utc")
            completed_ts = report.get("completed_ts_utc")
            try:
                started_at = datetime.fromisoformat(str(started_ts).removesuffix("Z") + "+00:00")
                completed_at = datetime.fromisoformat(
                    str(completed_ts).removesuffix("Z") + "+00:00"
                )
            except ValueError:
                return None
            now = datetime.now(UTC)
            started_epoch_ms = started_at.timestamp() * 1_000
            completed_epoch_ms = completed_at.timestamp() * 1_000
            measured_window_ms = completed_epoch_ms - started_epoch_ms
            maximum_event_lag_ms = 60_000.0
            elapsed_ms = _positive_number(report.get("elapsed_ms"))
            event_lags: list[float] = []
            observed_streams: set[str] = set()
            if not isinstance(event_samples, list) or not event_samples:
                return None
            for sample in event_samples:
                if not isinstance(sample, dict):
                    return None
                stream = sample.get("stream")
                source_ts_ms = _positive_number(sample.get("source_ts_ms"))
                received_ts_ms = _positive_number(sample.get("received_ts_ms"))
                if (
                    stream not in {"binance-public-depth", "binance-market-aggtrade"}
                    or source_ts_ms is None
                    or received_ts_ms is None
                    or received_ts_ms < source_ts_ms
                    or received_ts_ms < started_epoch_ms
                    or received_ts_ms > completed_epoch_ms
                    or source_ts_ms < started_epoch_ms - maximum_event_lag_ms
                    or source_ts_ms > completed_epoch_ms
                    or received_ts_ms - source_ts_ms > maximum_event_lag_ms
                ):
                    return None
                assert isinstance(stream, str)
                observed_streams.add(stream)
                event_lags.append(received_ts_ms - source_ts_ms)
            recomputed_p50 = round(statistics.median(event_lags), 3)
            recomputed_p95 = round(max(event_lags), 3)
            stored_p50 = _positive_number(report.get("lag_p50_ms"))
            stored_p95 = _positive_number(report.get("lag_p95_ms"))
            if (
                report.get("status") != "PASS"
                or report.get("venue") != "BINANCE_USDM"
                or any(_positive_number(report.get(field)) is None for field in positive_fields)
                or report.get("websocket_events") != len(event_samples)
                or observed_streams != {"binance-public-depth", "binance-market-aggtrade"}
                or stored_p50 is None
                or stored_p95 is None
                or not math.isclose(
                    stored_p50,
                    recomputed_p50,
                    abs_tol=0.001,
                )
                or not math.isclose(
                    stored_p95,
                    recomputed_p95,
                    abs_tol=0.001,
                )
                or not isinstance(started_ts, str)
                or not started_ts.endswith("Z")
                or not isinstance(completed_ts, str)
                or not completed_ts.endswith("Z")
                or started_at > completed_at
                or completed_at > now
                or completed_at < now - timedelta(minutes=10)
                or measured_window_ms <= 0
                or elapsed_ms is None
                or not math.isclose(
                    elapsed_ms,
                    measured_window_ms,
                    abs_tol=max(1_000.0, measured_window_ms * 0.25),
                )
                or report.get("credentials_sent") is not False
                or report.get("authorization_header_sent") is not False
                or report.get("auth_required") is not False
                or report.get("real_orders_enabled") is not False
            ):
                return None
            return sum(int(report[field]) for field in positive_fields)
        if name == "security_scan":
            checked = _strict_non_negative_int(report.get("checked_source_files"))
            forbidden = report.get("forbidden_fragments")
            current_report = _current_security_scan_report()
            if (
                report.get("status") != "PASS"
                or checked is None
                or checked <= 0
                or not isinstance(forbidden, list)
                or not forbidden
                or not all(isinstance(item, str) and item for item in forbidden)
                or report.get("violations") != []
                or report.get("secret_like_files") != []
                or report.get("real_order_path") is not False
                or report != current_report
            ):
                return None
            return checked
        if name == "repo_hygiene":
            version_path = PROJECT_ROOT / "VERSION"
            try:
                version = version_path.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            current_report = _current_repo_hygiene_report()
            return (
                1
                if report.get("status") == "PASS"
                and report.get("version") == version
                and re.fullmatch(r"\d+\.\d+\.\d+-paper", version) is not None
                and report.get("violations") == []
                and report == current_report
                else None
            )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if name == "backend_mypy":
        match = re.fullmatch(
            r"Success: no issues found in ([1-9][0-9]*) source files?\s*",
            text,
        )
        return int(match.group(1)) if match is not None else None
    if name == "frontend_typecheck":
        if re.search(r"error TS[0-9]+", text) is not None:
            return None
        source_lines = [
            line for line in text.splitlines() if line.strip().endswith((".ts", ".tsx"))
        ]
        return len(source_lines) if source_lines else None
    if name == "frontend_build":
        modules = re.search(r"(?:✓|\b)([1-9][0-9]*) modules transformed", text)
        built = re.search(r"(?:✓|\b) built in [0-9.]+(?:ms|s)", text)
        return int(modules.group(1)) if modules is not None and built is not None else None
    if name == "build_safety":
        return 1 if text.strip() == "PASS: PAPER 전용 빌드 불변조건" else None
    return None


def _pytest_junit_test_ids(record: Mapping[str, object]) -> set[str] | None:
    path = _artifact_path(record)
    if path is None:
        return None
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    source_modules = {
        path.relative_to(PROJECT_ROOT).as_posix(): path.relative_to(PROJECT_ROOT)
        .with_suffix("")
        .as_posix()
        .replace("/", ".")
        for path in (PROJECT_ROOT / "backend/tests").glob("test_*.py")
        if path.is_file() and not path.is_symlink()
    }
    test_ids: set[str] = set()
    test_cases = list(root.iter("testcase"))
    for case in test_cases:
        class_name = case.get("classname")
        test_name = case.get("name")
        if (
            not isinstance(class_name, str)
            or not class_name
            or not isinstance(test_name, str)
            or not test_name
        ):
            return None
        matching_sources = [
            (relative_path, module)
            for relative_path, module in source_modules.items()
            if class_name == module or class_name.startswith(f"{module}.")
        ]
        if len(matching_sources) != 1:
            return None
        relative_path, module = matching_sources[0]
        class_suffix = class_name.removeprefix(module).lstrip(".")
        class_scope = "::".join(class_suffix.split("."))
        test_id = (
            f"{relative_path}::{class_scope}::{test_name}"
            if class_scope
            else f"{relative_path}::{test_name}"
        )
        if test_id in test_ids:
            return None
        test_ids.add(test_id)
    return test_ids if len(test_ids) == len(test_cases) and test_ids else None


def _parse_collection_test_ids(
    text: str,
    artifact_format: str,
) -> set[str] | None:
    lines = [line.strip() for line in text.splitlines()]
    if artifact_format == "pytest_collection_text":
        test_ids = {
            line
            for line in lines
            if re.fullmatch(r"backend/tests/test_[^:]+\.py::\S+", line) is not None
        }
        summary_counts = [
            int(match.group(1))
            for line in lines
            if (match := re.fullmatch(r"([1-9][0-9]*) tests? collected in [0-9.]+s", line))
            is not None
        ]
    elif artifact_format == "vitest_collection_text":
        listed = [line for line in lines if re.fullmatch(r"tests/\S+ > .+", line)]
        test_ids = {f"frontend/{relative_path.replace(' > ', '::', 1)}" for relative_path in listed}
        summary_counts = [len(listed)]
    else:
        return None
    if len(summary_counts) != 1 or summary_counts[0] != len(test_ids):
        return None
    return test_ids or None


def _collection_test_ids(
    payload: Mapping[str, object],
    artifact_format: str,
) -> set[str] | None:
    record = _artifact_with_format(payload, artifact_format)
    path = _artifact_path(record) if record is not None else None
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return _parse_collection_test_ids(text, artifact_format)


def _current_full_suite_collection_ids() -> dict[str, set[str]] | None:
    observed: dict[str, set[str]] = {}
    for section, artifact_format in (
        ("backend", "pytest_collection_text"),
        ("frontend", "vitest_collection_text"),
    ):
        try:
            completed = subprocess.run(
                FULL_SUITE_COLLECTION_COMMANDS[section],
                cwd=PROJECT_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        test_ids = _parse_collection_test_ids(completed.stdout, artifact_format)
        if test_ids is None:
            return None
        observed[section] = test_ids
    return observed


def _frontend_vitest_test_ids(record: Mapping[str, object]) -> set[str] | None:
    report = _json_artifact(record)
    if not isinstance(report, dict):
        return None
    test_results = report.get("testResults")
    if not isinstance(test_results, list) or not test_results:
        return None
    test_ids: set[str] = set()
    try:
        project_root = PROJECT_ROOT.resolve(strict=True)
    except OSError:
        return None
    for result in test_results:
        if not isinstance(result, dict):
            return None
        raw_path = result.get("name")
        assertions = result.get("assertionResults")
        if not isinstance(raw_path, str) or not isinstance(assertions, list) or not assertions:
            return None
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None
        if not resolved.is_relative_to(project_root):
            return None
        relative_path = resolved.relative_to(project_root).as_posix()
        if not relative_path.startswith("frontend/tests/"):
            return None
        for assertion in assertions:
            if not isinstance(assertion, dict):
                return None
            full_name = assertion.get("fullName")
            status = assertion.get("status")
            failure_messages = assertion.get("failureMessages", [])
            if (
                not isinstance(full_name, str)
                or not full_name
                or status not in {"passed", "pending", "skipped", "todo"}
                or failure_messages != []
            ):
                return None
            test_id = f"{relative_path}::{full_name}"
            if test_id in test_ids:
                return None
            test_ids.add(test_id)
    return test_ids or None


def _full_suite_manifest_test_ids(
    payload: Mapping[str, object],
    *,
    backend_report_ids: set[str],
    frontend_report_ids: set[str],
) -> tuple[str, str] | None:
    record = _artifact_with_format(payload, "full_suite_test_manifest_json")
    raw_manifest = _json_artifact(record) if record is not None else None
    if not isinstance(raw_manifest, dict):
        return "NOT_PROVEN", "FULL_SUITE_COLLECTION_MANIFEST_MISSING_OR_INVALID"
    if (
        raw_manifest.get("schema") != "flowscalper.full_suite_test_manifest.v1"
        or raw_manifest.get("status") != "PASS"
        or raw_manifest.get("source_commit") != payload.get("source_commit")
        or raw_manifest.get("collection_commands") != FULL_SUITE_COLLECTION_COMMANDS
    ):
        return "NOT_PROVEN", "FULL_SUITE_COLLECTION_MANIFEST_BINDING_MISMATCH"
    collection_ids = {
        "backend": _collection_test_ids(payload, "pytest_collection_text"),
        "frontend": _collection_test_ids(payload, "vitest_collection_text"),
    }
    if any(test_ids is None for test_ids in collection_ids.values()):
        return "NOT_PROVEN", "FULL_SUITE_COLLECTION_OUTPUT_MISSING_OR_INVALID"
    trusted_collection_ids = _current_full_suite_collection_ids()
    if trusted_collection_ids is None:
        return "NOT_PROVEN", "FULL_SUITE_TRUSTED_COLLECTION_NOT_RUN"
    if any(
        collection_ids[section] != trusted_collection_ids[section]
        for section in ("backend", "frontend")
    ):
        return "NOT_PROVEN", "FULL_SUITE_COLLECTION_DIFFERS_FROM_CURRENT_SOURCE"
    expected_paths = {
        "backend": sorted(
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in (PROJECT_ROOT / "backend/tests").glob("test_*.py")
            if path.is_file() and not path.is_symlink()
        ),
        "frontend": sorted(
            path.relative_to(PROJECT_ROOT).as_posix()
            for pattern in ("*.test.ts", "*.test.tsx")
            for path in (PROJECT_ROOT / "frontend/tests").glob(pattern)
            if path.is_file() and not path.is_symlink()
        ),
    }
    expected_report_ids = {
        "backend": backend_report_ids,
        "frontend": frontend_report_ids,
    }
    for section in ("backend", "frontend"):
        rows = raw_manifest.get(section)
        if not isinstance(rows, list) or len(rows) != len(expected_paths[section]):
            return "NOT_PROVEN", "FULL_SUITE_COLLECTION_MANIFEST_FILE_SCOPE_MISMATCH"
        observed_paths: list[str] = []
        observed_ids: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"path", "sha256", "test_ids"}:
                return "NOT_PROVEN", "FULL_SUITE_COLLECTION_MANIFEST_ROW_INVALID"
            relative_path = row.get("path")
            sha256 = row.get("sha256")
            test_ids = row.get("test_ids")
            if (
                not isinstance(relative_path, str)
                or relative_path in observed_paths
                or not isinstance(sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
                or not isinstance(test_ids, list)
                or not test_ids
                or any(not isinstance(test_id, str) or not test_id for test_id in test_ids)
                or len(set(test_ids)) != len(test_ids)
            ):
                return "NOT_PROVEN", "FULL_SUITE_COLLECTION_MANIFEST_ROW_INVALID"
            source_path = PROJECT_ROOT / relative_path
            try:
                content = source_path.read_bytes()
            except OSError:
                return "NOT_PROVEN", "FULL_SUITE_COLLECTION_MANIFEST_SOURCE_MISSING"
            if hashlib.sha256(content).hexdigest() != sha256:
                return "FAIL", "FULL_SUITE_COLLECTION_MANIFEST_SOURCE_SHA_MISMATCH"
            if section == "backend":
                if any(not test_id.startswith(f"{relative_path}::") for test_id in test_ids):
                    return "NOT_PROVEN", "FULL_SUITE_COLLECTION_TEST_ID_PATH_MISMATCH"
            elif any(not test_id.startswith(f"{relative_path}::") for test_id in test_ids):
                return "NOT_PROVEN", "FULL_SUITE_COLLECTION_TEST_ID_PATH_MISMATCH"
            observed_paths.append(relative_path)
            observed_ids.update(test_ids)
        if sorted(observed_paths) != expected_paths[section]:
            return "NOT_PROVEN", "FULL_SUITE_COLLECTION_MANIFEST_FILE_SCOPE_MISMATCH"
        if observed_ids != expected_report_ids[section] or observed_ids != collection_ids[section]:
            return "NOT_PROVEN", "FULL_SUITE_REPORT_IDS_DIFFER_FROM_COLLECTION_MANIFEST"
    return None


def _running_service_soak_measurement(
    payload: Mapping[str, object],
) -> dict[str, Any] | None:
    record = _artifact_with_format(payload, "running_service_soak_json")
    measurement = _json_artifact(record) if record is not None else None
    return dict(measurement) if isinstance(measurement, dict) else None


def _sample_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _strict_soak_scalar(value: object, annotation: str) -> object | None:
    if annotation == "bool":
        return value if isinstance(value, bool) else None
    if annotation == "int":
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )
    if annotation == "float":
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            return None
        return float(value)
    if annotation == "str":
        return value if isinstance(value, str) and value else None
    if annotation == "str | None":
        return value if value is None or isinstance(value, str) and value else None
    return None


def _strict_strategy_state(value: object) -> StrategyState | None:
    if not isinstance(value, dict):
        return None
    expected_fields = {field.name for field in fields(StrategyState)}
    if set(value) != expected_fields:
        return None
    normalized: dict[str, Any] = {}
    for field in fields(StrategyState):
        parsed = _strict_soak_scalar(value[field.name], str(field.type))
        if parsed is None:
            return None
        normalized[field.name] = parsed
    return StrategyState(**normalized)


def _strict_running_service_sample(value: object) -> RunningServiceSample | None:
    if not isinstance(value, dict):
        return None
    expected_fields = {field.name for field in fields(RunningServiceSample)}
    if set(value) != expected_fields | SOAK_AUDIT_SAMPLE_FIELDS:
        return None
    normalized: dict[str, Any] = {}
    for field in fields(RunningServiceSample):
        raw_value = value[field.name]
        if field.name == "strategy_states":
            if not isinstance(raw_value, list) or not raw_value:
                return None
            states = tuple(_strict_strategy_state(state) for state in raw_value)
            if any(state is None for state in states):
                return None
            normalized[field.name] = tuple(
                state for state in states if isinstance(state, StrategyState)
            )
            continue
        parsed = _strict_soak_scalar(raw_value, str(field.type))
        if parsed is None and not (str(field.type) == "str | None" and raw_value is None):
            return None
        normalized[field.name] = parsed
    pending_counts = tuple(
        _strict_non_negative_int(value[field_name])
        for field_name in (
            "main_pending_entry_count",
            "league_pending_entry_count",
            "total_pending_entry_count",
            "total_open_position_count",
        )
    )
    if any(count is None for count in pending_counts):
        return None
    main_pending, league_pending, total_pending, total_open = pending_counts
    assert main_pending is not None
    assert league_pending is not None
    assert total_pending is not None
    assert total_open is not None
    if (
        total_pending != main_pending + league_pending
        or total_open != normalized["position_count"]
        or value["paper_portfolio_flat"] is not (total_pending == 0 and total_open == 0)
    ):
        return None
    return RunningServiceSample(**normalized)


def _strict_soak_thresholds(value: object) -> RunningServiceSoakThresholds | None:
    if not isinstance(value, dict):
        return None
    expected_fields = {field.name for field in fields(RunningServiceSoakThresholds)}
    if set(value) != expected_fields:
        return None
    normalized: dict[str, Any] = {}
    for field in fields(RunningServiceSoakThresholds):
        parsed = _strict_soak_scalar(value[field.name], str(field.type))
        if parsed is None:
            return None
        normalized[field.name] = parsed
    try:
        return RunningServiceSoakThresholds(**normalized)
    except ValueError:
        return None


def _validate_running_service_soak(
    payload: Mapping[str, object],
    checks: Mapping[str, object],
) -> tuple[str, str]:
    if not SOAK_REQUIRED_CHECKS.issubset(checks):
        return "NOT_PROVEN", "SOAK_REQUIRED_CHECK_SET_MISMATCH"
    if not _command_succeeded(payload, token="observe_running_service.py"):
        return "NOT_PROVEN", "SOAK_COMMAND_OR_EXIT_CODE_NOT_PROVEN"
    artifact_failure = _validate_artifacts(
        payload,
        required_kinds=frozenset({"artifact"}),
    )
    if artifact_failure is not None:
        return artifact_failure
    measurement = _running_service_soak_measurement(payload)
    if measurement is None:
        return "NOT_PROVEN", "SOAK_MACHINE_REPORT_INVALID"
    generated_at = _sample_timestamp(measurement.get("generated_ts_utc"))
    started_at = _sample_timestamp(measurement.get("started_at"))
    completed_at = _sample_timestamp(measurement.get("completed_at"))
    wall_duration_seconds = _positive_number(measurement.get("wall_duration_seconds"))
    if (
        measurement.get("generated_ts_utc") != payload.get("generated_ts_utc")
        or generated_at is None
        or started_at is None
        or completed_at is None
        or any(
            timestamp.utcoffset() != timedelta(0)
            for timestamp in (generated_at, started_at, completed_at)
        )
        or generated_at > datetime.now(UTC) + timedelta(minutes=5)
        or completed_at != generated_at
        or started_at >= completed_at
        or wall_duration_seconds is None
        or not math.isclose(
            wall_duration_seconds,
            round((completed_at - started_at).total_seconds(), 3),
            abs_tol=0.001,
        )
    ):
        return "NOT_PROVEN", "SOAK_MEASUREMENT_TIME_BINDING_INVALID"
    measurement_checks = measurement.get("checks")
    if (
        measurement.get("status") != "PASS"
        or not isinstance(measurement_checks, dict)
        or not SOAK_REQUIRED_CHECKS.issubset(measurement_checks)
        or any(value is not True for value in measurement_checks.values())
        or measurement_checks != checks
        or measurement.get("source_commit") != payload.get("source_commit")
        or measurement.get("release_commit") != payload.get("release_commit")
        or measurement.get("run_id") != payload.get("run_id")
        or measurement.get("source_worktree_clean_at_measurement") is not True
        or measurement.get("release_isolated_throughout") is not True
    ):
        return "NOT_PROVEN", "SOAK_MACHINE_REPORT_BINDING_MISMATCH"
    if not isinstance(measurement.get("run_id"), str) or not measurement["run_id"].strip():
        return "NOT_PROVEN", "SOAK_RUN_BINDING_MISSING"
    requested_seconds = _positive_number(measurement.get("requested_duration_seconds"))
    declared_observed_seconds = _positive_number(measurement.get("observed_duration_seconds"))
    sample_interval = _positive_number(measurement.get("sample_seconds"))
    sample_count = _strict_non_negative_int(measurement.get("sample_count"))
    samples = measurement.get("samples")
    if (
        requested_seconds is None
        or requested_seconds < 1_800
        or declared_observed_seconds is None
        or sample_interval is None
        or sample_count is None
        or sample_count < 3
        or not isinstance(samples, list)
        or len(samples) != sample_count
    ):
        return "NOT_PROVEN", "SOAK_SAMPLE_COUNT_OR_DURATION_METADATA_INVALID"
    parsed_samples: list[tuple[RunningServiceSample, datetime]] = []
    root_strategy_ids = measurement.get("strategy_ids")
    if (
        not isinstance(root_strategy_ids, list)
        or not root_strategy_ids
        or any(
            not isinstance(strategy_id, str) or not strategy_id for strategy_id in root_strategy_ids
        )
        or len(set(root_strategy_ids)) != len(root_strategy_ids)
    ):
        return "NOT_PROVEN", "SOAK_STRATEGY_SCOPE_MISSING"
    expected_strategy_ids = set(root_strategy_ids)
    root_account_ids = measurement.get("league_account_ids")
    root_mode_counts = measurement.get("strategy_mode_counts")
    if (
        not isinstance(root_account_ids, list)
        or len(root_account_ids) != len(expected_strategy_ids) * 2
        or any(not isinstance(account_id, str) or not account_id for account_id in root_account_ids)
        or len(set(root_account_ids)) != len(root_account_ids)
        or not isinstance(root_mode_counts, dict)
        or set(root_mode_counts) != {"ACTIVE", "SHADOW", "OFF"}
        or any(
            _strict_non_negative_int(root_mode_counts.get(mode)) is None
            for mode in ("ACTIVE", "SHADOW", "OFF")
        )
        or sum(int(root_mode_counts[mode]) for mode in ("ACTIVE", "SHADOW", "OFF"))
        != len(expected_strategy_ids)
    ):
        return "NOT_PROVEN", "SOAK_ACCOUNT_OR_MODE_SCOPE_MISSING"
    for raw_sample in samples:
        if not isinstance(raw_sample, dict):
            return "NOT_PROVEN", "SOAK_SAMPLE_RECORD_INVALID"
        parsed_sample = _strict_running_service_sample(raw_sample)
        observed_at = _sample_timestamp(raw_sample.get("observed_at"))
        if parsed_sample is None or observed_at is None:
            return "NOT_PROVEN", "SOAK_SAMPLE_RECORD_INVALID"
        if (
            parsed_sample.run_id != measurement.get("run_id")
            or parsed_sample.execution_state != "PAPER"
            or parsed_sample.market_data_state != "LIVE"
            or parsed_sample.real_orders_enabled
            or parsed_sample.auth_required
            or parsed_sample.strategy_count != len(expected_strategy_ids)
            or parsed_sample.league_account_count != len(expected_strategy_ids) * 2
            or {state.strategy_id for state in parsed_sample.strategy_states}
            != expected_strategy_ids
            or len(parsed_sample.strategy_states) != len(expected_strategy_ids)
            or raw_sample.get("league_account_ids") != root_account_ids
            or raw_sample.get("release_commit") != measurement.get("release_commit")
            or raw_sample.get("release_isolated") is not True
            or {
                mode: sum(state.mode == mode for state in parsed_sample.strategy_states)
                for mode in ("ACTIVE", "SHADOW", "OFF")
            }
            != root_mode_counts
        ):
            return "NOT_PROVEN", "SOAK_SAMPLE_SCOPE_OR_PAPER_SAFETY_INVALID"
        parsed_samples.append((parsed_sample, observed_at))
    elapsed_values = [sample[0].elapsed_seconds for sample in parsed_samples]
    observed_times = [sample[1] for sample in parsed_samples]
    elapsed_gaps = [
        current - previous
        for previous, current in zip(elapsed_values, elapsed_values[1:], strict=False)
    ]
    timestamp_gaps = [
        (current - previous).total_seconds()
        for previous, current in zip(observed_times, observed_times[1:], strict=False)
    ]
    recomputed_observed_seconds = (observed_times[-1] - observed_times[0]).total_seconds()
    maximum_gap = max(sample_interval * 2.5, sample_interval + 5.0)
    if (
        any(timestamp.utcoffset() != timedelta(0) for timestamp in observed_times)
        or observed_times[0] < started_at
        or observed_times[-1] > completed_at
        or any(gap <= 0 or gap > maximum_gap for gap in elapsed_gaps)
        or any(gap <= 0 or gap > maximum_gap for gap in timestamp_gaps)
        or recomputed_observed_seconds < requested_seconds
        or elapsed_values[-1] - elapsed_values[0] < requested_seconds
        or abs(recomputed_observed_seconds - declared_observed_seconds) > maximum_gap
    ):
        return "NOT_PROVEN", "SOAK_RECOMPUTED_DURATION_OR_CONTINUITY_FAILED"
    thresholds = _strict_soak_thresholds(measurement.get("thresholds"))
    probe_error_count = _strict_non_negative_int(measurement.get("probe_error_count"))
    maximum_consecutive_probe_errors = _strict_non_negative_int(
        measurement.get("maximum_consecutive_probe_errors")
    )
    max_consecutive_probe_errors = _strict_non_negative_int(
        measurement.get("max_consecutive_probe_errors")
    )
    if (
        thresholds is None
        or probe_error_count is None
        or maximum_consecutive_probe_errors is None
        or max_consecutive_probe_errors in {None, 0}
    ):
        return "NOT_PROVEN", "SOAK_RECOMPUTATION_INPUT_MISSING_OR_INVALID"
    assert max_consecutive_probe_errors is not None
    recomputed = summarize_running_service_soak(
        [sample for sample, _observed_at in parsed_samples],
        requested_duration_seconds=requested_seconds,
        thresholds=thresholds,
        probe_error_count=probe_error_count,
        maximum_consecutive_probe_errors=maximum_consecutive_probe_errors,
        max_consecutive_probe_errors=max_consecutive_probe_errors,
        operator_aborted=False,
    )
    recomputed_checks = recomputed.get("checks")
    if not isinstance(recomputed_checks, dict):
        return "NOT_PROVEN", "SOAK_RECOMPUTED_CHECKS_MISSING"
    if measurement.get("baseline") != samples[0] or measurement.get("final") != samples[-1]:
        return "FAIL", "SOAK_BASELINE_OR_FINAL_DIFFERS_FROM_SAMPLE_SERIES"
    if (
        set(measurement_checks) != set(recomputed_checks) | SOAK_PROVENANCE_CHECKS
        or any(measurement_checks.get(name) != value for name, value in recomputed_checks.items())
        or any(measurement_checks.get(name) is not True for name in SOAK_PROVENANCE_CHECKS)
    ):
        return "FAIL", "SOAK_DECLARED_CHECKS_DIFFER_FROM_RECOMPUTED_SAMPLES"
    for name, value in recomputed.items():
        if (
            name not in {"checks", "baseline", "final", "samples"}
            and measurement.get(name) != value
        ):
            return "FAIL", "SOAK_DECLARED_RESULT_DIFFERS_FROM_RECOMPUTED_SAMPLES"
    if recomputed.get("status") != "PASS":
        return "FAIL", "SOAK_RECOMPUTED_SAMPLE_CHECKS_FAILED"
    release_commits = measurement.get("release_commits_observed")
    safety = measurement.get("paper_safety")
    if (
        release_commits != [measurement.get("release_commit")]
        or measurement.get("source_commit_at_end") != measurement.get("source_commit")
        or measurement.get("source_worktree_clean_at_start") is not True
        or measurement.get("source_worktree_clean_at_end") is not True
        or not isinstance(safety, dict)
        or safety.get("real_orders_enabled") is not False
        or safety.get("auth_required") is not False
        or safety.get("private_api_requested") is not False
        or safety.get("api_key_requested") is not False
        or safety.get("wallet_requested") is not False
        or safety.get("additional_market_connection_started") is not False
    ):
        return "FAIL", "SOAK_RELEASE_OR_PAPER_SAFETY_FAILED"
    return "PASS", "SOAK_MACHINE_SAMPLES_DURATION_SCOPE_AND_SAFETY_PASS"


def _validate_dashboard_benchmark(
    payload: Mapping[str, object],
    checks: Mapping[str, object],
) -> tuple[str, str]:
    if set(checks) != BENCHMARK_REQUIRED_CHECKS:
        return "NOT_PROVEN", "BENCHMARK_REQUIRED_CHECK_SET_MISMATCH"
    if not _command_succeeded(payload, token="benchmark_dashboard_payload.py"):
        return "NOT_PROVEN", "BENCHMARK_COMMAND_OR_EXIT_CODE_NOT_PROVEN"
    artifact_failure = _validate_artifacts(payload, required_kinds=frozenset({"artifact"}))
    if artifact_failure is not None:
        return artifact_failure
    measurement_record = _artifact_with_format(payload, "dashboard_benchmark_json")
    raw_measurement = _json_artifact(measurement_record) if measurement_record is not None else None
    if not isinstance(raw_measurement, dict):
        return "NOT_PROVEN", "BENCHMARK_MACHINE_REPORT_INVALID"
    raw_records = {
        artifact_format: _artifact_with_format(payload, artifact_format)
        for artifact_format in BENCHMARK_RAW_FORMATS
    }
    if any(record is None for record in raw_records.values()):
        return "NOT_PROVEN", "BENCHMARK_RAW_PAYLOAD_OR_LATENCY_ARTIFACT_MISSING"
    raw_artifacts = {
        artifact_format: _json_artifact(record)
        for artifact_format, record in raw_records.items()
        if record is not None
    }
    if set(raw_artifacts) != BENCHMARK_RAW_FORMATS or any(
        not isinstance(value, dict) for value in raw_artifacts.values()
    ):
        return "NOT_PROVEN", "BENCHMARK_RAW_PAYLOAD_OR_LATENCY_ARTIFACT_INVALID"
    measurement_checks = raw_measurement.get("checks")
    if (
        raw_measurement.get("status") != "PASS"
        or not isinstance(measurement_checks, dict)
        or measurement_checks != checks
        or set(measurement_checks) != BENCHMARK_REQUIRED_CHECKS
        or raw_measurement.get("source_commit") != payload.get("source_commit")
        or raw_measurement.get("source_worktree_clean_at_measurement") is not True
        or raw_measurement.get("fixture_events") != payload.get("fixture_events")
    ):
        return "NOT_PROVEN", "BENCHMARK_MACHINE_REPORT_BINDING_MISMATCH"
    if _strict_non_negative_int(raw_measurement.get("fixture_events")) in {None, 0}:
        return "NOT_PROVEN", "BENCHMARK_FIXTURE_EVENT_COUNT_NOT_POSITIVE"

    raw_metrics = raw_measurement.get("payload")
    raw_delta = raw_measurement.get("websocket_chart_delta")
    raw_latency = raw_measurement.get("transform_latency")
    if (
        not isinstance(raw_metrics, dict)
        or not isinstance(raw_delta, dict)
        or not isinstance(raw_latency, dict)
    ):
        return "NOT_PROVEN", "BENCHMARK_REQUIRED_MEASUREMENT_OBJECT_MISSING"
    dashboard_payload = raw_artifacts["dashboard_payload_json"]
    summary_payload = raw_artifacts["summary_payload_json"]
    strategy_payload = raw_artifacts["strategy_summary_payload_json"]
    delta_message = raw_artifacts["chart_delta_message_json"]
    full_chart = raw_artifacts["full_chart_payload_json"]
    latency_samples = raw_artifacts["dashboard_latency_samples_json"]
    assert isinstance(dashboard_payload, dict)
    assert isinstance(summary_payload, dict)
    assert isinstance(strategy_payload, dict)
    assert isinstance(delta_message, dict)
    assert isinstance(full_chart, dict)
    assert isinstance(latency_samples, dict)

    def encoded_size(value: object) -> int:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode()
        )

    dashboard_bytes = encoded_size(dashboard_payload)
    summary_bytes = encoded_size(summary_payload)
    strategy_bytes = encoded_size(strategy_payload)
    delta_bytes = encoded_size(delta_message)
    full_chart_bytes = encoded_size(full_chart)
    declared_dashboard_bytes = _positive_number(raw_metrics.get("dashboard_payload_bytes"))
    declared_summary_bytes = _positive_number(raw_metrics.get("summary_payload_bytes"))
    declared_strategy_bytes = _positive_number(raw_metrics.get("strategy_summary_payload_bytes"))
    stored_summary_ratio = _positive_number(raw_metrics.get("summary_to_dashboard_ratio"))
    stored_strategy_ratio = _positive_number(raw_metrics.get("strategy_summary_to_dashboard_ratio"))
    declared_delta_bytes = _positive_number(raw_delta.get("delta_envelope_bytes"))
    declared_full_chart_bytes = _positive_number(raw_delta.get("full_chart_bytes"))
    stored_delta_ratio = _positive_number(raw_delta.get("delta_to_full_chart_ratio"))
    measurements = (
        declared_dashboard_bytes,
        declared_summary_bytes,
        declared_strategy_bytes,
        stored_summary_ratio,
        stored_strategy_ratio,
        declared_delta_bytes,
        declared_full_chart_bytes,
        stored_delta_ratio,
    )
    if any(value is None for value in measurements):
        return "NOT_PROVEN", "BENCHMARK_BYTES_OR_RATIO_NOT_POSITIVE"
    assert declared_dashboard_bytes is not None
    assert declared_summary_bytes is not None
    assert declared_strategy_bytes is not None
    assert stored_summary_ratio is not None
    assert stored_strategy_ratio is not None
    assert declared_delta_bytes is not None
    assert declared_full_chart_bytes is not None
    assert stored_delta_ratio is not None
    summary_ratio = summary_bytes / dashboard_bytes
    strategy_ratio = strategy_bytes / dashboard_bytes
    delta_ratio = delta_bytes / full_chart_bytes
    delta_data = delta_message.get("data")
    system = dashboard_payload.get("system")
    target_summary = raw_metrics.get("target_summary_ratio_strictly_less_than")
    target_strategy = raw_metrics.get("target_strategy_ratio_strictly_less_than")
    calculations_valid = (
        isinstance(system, dict)
        and system.get("event_count") == raw_measurement.get("fixture_events")
        and declared_dashboard_bytes == dashboard_bytes
        and declared_summary_bytes == summary_bytes
        and declared_strategy_bytes == strategy_bytes
        and declared_delta_bytes == delta_bytes
        and declared_full_chart_bytes == full_chart_bytes
        and target_summary == 0.50
        and target_strategy == 0.35
        and summary_ratio < 0.50
        and strategy_ratio < 0.35
        and delta_ratio < 1.0
        and math.isclose(stored_summary_ratio, summary_ratio, abs_tol=0.000001)
        and math.isclose(stored_strategy_ratio, strategy_ratio, abs_tol=0.000001)
        and math.isclose(stored_delta_ratio, delta_ratio, abs_tol=0.000001)
        and delta_message.get("type") == "chart_delta"
        and isinstance(delta_data, dict)
        and delta_data.get("refresh_required") is False
        and isinstance(delta_data.get("point_upserts"), list)
        and len(delta_data["point_upserts"]) == 1
        and isinstance(delta_data.get("candle_upserts"), list)
        and len(delta_data["candle_upserts"]) <= 1
        and raw_delta.get("message_type") == delta_message.get("type")
        and raw_delta.get("refresh_required") == delta_data.get("refresh_required")
        and raw_delta.get("point_upserts") == len(delta_data["point_upserts"])
        and raw_delta.get("candle_upserts") == len(delta_data["candle_upserts"])
        and "history" not in summary_payload
        and "strategies" not in summary_payload
        and "league_accounts" not in summary_payload
        and summary_payload.get("paper_only") is True
        and summary_payload.get("real_orders_enabled") is False
        and summary_payload.get("auth_required") is False
        and raw_measurement.get("paper_only") is True
        and raw_measurement.get("real_orders_enabled") is False
    )
    if not calculations_valid:
        return "FAIL", "BENCHMARK_RECOMPUTED_TARGET_OR_DELTA_MISMATCH"
    required_latency_names = {
        "ui_summary",
        "strategy_list",
        "selected_family_detail",
        "single_tick_delta",
    }
    if set(raw_latency) != required_latency_names:
        return "NOT_PROVEN", "BENCHMARK_LATENCY_SCOPE_MISMATCH"
    if set(latency_samples) != required_latency_names:
        return "NOT_PROVEN", "BENCHMARK_LATENCY_RAW_SAMPLE_SCOPE_MISMATCH"
    for name, measurement in raw_latency.items():
        samples = latency_samples[name]
        stored_p95 = (
            _positive_number(measurement.get("p95_ms")) if isinstance(measurement, dict) else None
        )
        if (
            not isinstance(measurement, dict)
            or not isinstance(samples, list)
            or not samples
            or any(_positive_number(sample) is None for sample in samples)
            or stored_p95 is None
        ):
            return "NOT_PROVEN", "BENCHMARK_LATENCY_COUNT_OR_VALUE_NOT_POSITIVE"
        ordered_samples = sorted(float(sample) for sample in samples)
        p95_index = max(0, math.ceil(len(ordered_samples) * 0.95) - 1)
        assert stored_p95 is not None
        if measurement.get("iterations") != len(ordered_samples) or not math.isclose(
            stored_p95,
            round(ordered_samples[p95_index], 6),
            abs_tol=0.000001,
        ):
            return "FAIL", "BENCHMARK_RECOMPUTED_LATENCY_MISMATCH"
    return "PASS", "BENCHMARK_REQUIRED_CHECKS_COMMAND_ARTIFACTS_AND_FORMULAS_PASS"


def _validate_browser_e2e(
    payload: Mapping[str, object],
    checks: Mapping[str, object],
) -> tuple[str, str]:
    if set(checks) != BROWSER_REQUIRED_CHECKS:
        return "NOT_PROVEN", "BROWSER_REQUIRED_CHECK_SET_MISMATCH"
    if not _command_succeeded(payload, token="playwright"):
        return "NOT_PROVEN", "BROWSER_COMMAND_OR_EXIT_CODE_NOT_PROVEN"
    artifact_failure = _validate_artifacts(
        payload,
        required_kinds=frozenset({"artifact", "screenshot"}),
    )
    if artifact_failure is not None:
        return artifact_failure
    playwright_record = _artifact_with_format(payload, "playwright_json")
    measurement_record = _artifact_with_format(payload, "browser_measurements_json")
    raw_playwright = _json_artifact(playwright_record) if playwright_record is not None else None
    raw_measurements = (
        _json_artifact(measurement_record) if measurement_record is not None else None
    )
    if not isinstance(raw_playwright, dict) or not isinstance(raw_measurements, dict):
        return "NOT_PROVEN", "BROWSER_MACHINE_REPORT_INVALID"
    report_summary = _playwright_report_summary(raw_playwright)
    measurement_checks = raw_measurements.get("checks")
    measured_projects = raw_measurements.get("projects")
    if (
        report_summary is None
        or raw_measurements.get("schema") != "flowscalper.browser_measurements.v1"
        or not isinstance(measurement_checks, dict)
        or measurement_checks != checks
        or set(measurement_checks) != BROWSER_REQUIRED_CHECKS
        or not isinstance(measured_projects, dict)
        or set(measured_projects) != {"desktop", "tablet", "mobile"}
    ):
        return "NOT_PROVEN", "BROWSER_MACHINE_REPORT_BINDING_MISMATCH"
    paper_safety_at_start = payload.get("paper_safety_at_start")
    paper_safety_at_end = payload.get("paper_safety_at_end")
    measured_paper_safety_at_start = raw_measurements.get("paper_safety_at_start")
    measured_paper_safety_at_end = raw_measurements.get("paper_safety_at_end")

    def exact_paper_safety(value: object) -> bool:
        if not isinstance(value, dict) or set(value) != set(BROWSER_EXPECTED_PAPER_SAFETY):
            return False
        return (
            value.get("paper_only") is True
            and all(
                value.get(field) is False
                for field in (
                    "real_orders_enabled",
                    "auth_required",
                    "private_api_enabled",
                    "api_key_enabled",
                    "wallet_enabled",
                    "runtime_ai_order_decision_enabled",
                )
            )
            and value.get("funding_readiness") == "NOT_READY"
        )

    if (
        not exact_paper_safety(paper_safety_at_start)
        or not exact_paper_safety(paper_safety_at_end)
        or measured_paper_safety_at_start != paper_safety_at_start
        or measured_paper_safety_at_end != paper_safety_at_end
        or payload.get("paper_safety_source_paths") != BROWSER_PAPER_SAFETY_SOURCE_PATHS
        or raw_measurements.get("paper_safety_source_paths") != BROWSER_PAPER_SAFETY_SOURCE_PATHS
    ):
        return "NOT_PROVEN", "BROWSER_PAPER_SAFETY_BINDING_MISMATCH"
    source_commit = payload.get("source_commit")
    run_id = payload.get("run_id")
    runtime_provenance = raw_measurements.get("runtime_provenance")
    expected_runtime_provenance = {
        "run_id_at_start": run_id,
        "run_id_at_end": payload.get("run_id_at_end"),
        "execution_state_at_start": payload.get("execution_state"),
        "execution_state_at_end": payload.get("execution_state_at_end"),
        "release_commit_at_start": payload.get("release_commit"),
        "release_commit_at_end": payload.get("release_commit_at_end"),
        "release_isolated_at_start": payload.get("release_isolated"),
        "release_isolated_at_end": payload.get("release_isolated_at_end"),
    }
    if (
        not isinstance(source_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
        or payload.get("source_commit_at_end") != source_commit
        or payload.get("source_worktree_clean_at_start") is not True
        or payload.get("source_worktree_clean_at_end") is not True
        or payload.get("source_worktree_clean_at_measurement") is not True
        or payload.get("release_commit") != source_commit
        or payload.get("release_commit_at_end") != source_commit
        or payload.get("release_isolated") is not True
        or payload.get("release_isolated_at_end") is not True
        or not isinstance(run_id, str)
        or not run_id.strip()
        or payload.get("run_id_at_end") != run_id
        or payload.get("execution_state") != "PAPER"
        or payload.get("execution_state_at_end") != "PAPER"
        or runtime_provenance != expected_runtime_provenance
    ):
        return "NOT_PROVEN", "BROWSER_RUNTIME_PROVENANCE_BINDING_MISMATCH"
    (
        reporter_test_count,
        reporter_projects,
        reporter_check_projects,
        reporter_screenshot_attachments,
    ) = report_summary
    counts = payload.get("counts")
    artifacts = _artifact_records(payload)
    if not isinstance(counts, dict):
        return "NOT_PROVEN", "BROWSER_COUNTS_OR_ARTIFACTS_MISSING"
    if set(counts) != {
        "page_count",
        "project_count",
        "test_count",
        "screenshot_count",
        "console_error_count",
    }:
        return "NOT_PROVEN", "BROWSER_COUNT_SCOPE_MISMATCH"
    screenshot_records = [record for record in artifacts if record.get("kind") == "screenshot"]
    screenshot_projects: set[str] = set()
    screenshot_paths_by_project: dict[str, set[str]] = {
        "desktop": set(),
        "tablet": set(),
        "mobile": set(),
    }
    screenshot_sha256_by_project: dict[str, set[str]] = {
        "desktop": set(),
        "tablet": set(),
        "mobile": set(),
    }
    screenshot_paths_by_check_project: dict[tuple[str, str], set[str]] = {
        (check_id, project): set()
        for check_id, projects in BROWSER_REQUIRED_TEST_PROJECTS.items()
        for project in projects
    }
    minimum_dimensions = {
        "desktop": (1408, 900),
        "tablet": (820, 1180),
        "mobile": (390, 844),
    }
    for record in screenshot_records:
        project = record.get("project")
        raw_path = record.get("path")
        sha256 = record.get("sha256")
        check_ids = record.get("check_ids")
        dimensions = _png_dimensions(record)
        allowed_check_ids = (
            {
                check_id
                for check_id, projects in BROWSER_REQUIRED_TEST_PROJECTS.items()
                if isinstance(project, str) and project in projects
            }
            if isinstance(project, str)
            else set()
        )
        if (
            record.get("format") != "png"
            or not isinstance(project, str)
            or project not in minimum_dimensions
            or not isinstance(raw_path, str)
            or not isinstance(sha256, str)
            or sha256 in screenshot_sha256_by_project[project]
            or not isinstance(check_ids, list)
            or len(check_ids) != 1
            or check_ids != sorted(set(check_ids))
            or any(
                not isinstance(check_id, str) or check_id not in allowed_check_ids
                for check_id in check_ids
            )
            or dimensions is None
            or dimensions[0] < minimum_dimensions[project][0]
            or dimensions[1] < minimum_dimensions[project][1]
        ):
            return "NOT_PROVEN", "BROWSER_SCREENSHOT_PNG_OR_DIMENSIONS_INVALID"
        screenshot_projects.add(project)
        screenshot_sha256_by_project[project].add(sha256)
        screenshot_paths_by_project[project].add(raw_path)
        for check_id in check_ids:
            assert isinstance(check_id, str)
            screenshot_paths_by_check_project[(check_id, project)].add(raw_path)
    if any(
        reporter_screenshot_attachments[(check_id, project)]
        != frozenset(screenshot_paths_by_check_project[(check_id, project)])
        for check_id, projects in BROWSER_REQUIRED_TEST_PROJECTS.items()
        for project in projects
    ):
        return "NOT_PROVEN", "BROWSER_REPORTER_SCREENSHOT_BINDING_MISMATCH"
    measured_console_errors = 0
    for project, project_payload in measured_projects.items():
        if not isinstance(project_payload, dict):
            return "NOT_PROVEN", "BROWSER_PROJECT_MEASUREMENT_INVALID"
        console_errors = _strict_non_negative_int(project_payload.get("console_error_count"))
        screenshot_paths = project_payload.get("screenshot_paths")
        if (
            project_payload.get("status") != "PASS"
            or console_errors is None
            or console_errors != 0
            or not isinstance(screenshot_paths, list)
            or not screenshot_paths
            or set(screenshot_paths) != screenshot_paths_by_project[project]
        ):
            return "NOT_PROVEN", "BROWSER_PROJECT_MEASUREMENT_SCOPE_MISMATCH"
        measured_console_errors += console_errors
    screenshot_count = len(screenshot_records)
    expected_screenshot_count = sum(
        len(projects) for projects in BROWSER_REQUIRED_TEST_PROJECTS.values()
    )
    if (
        counts.get("page_count") != len(EXPECTED_PAGE_IDS)
        or counts.get("project_count") != len(reporter_projects)
        or counts.get("test_count") != reporter_test_count
        or counts.get("screenshot_count") != screenshot_count
        or screenshot_count != expected_screenshot_count
        or counts.get("console_error_count") != measured_console_errors
        or screenshot_projects != reporter_projects
        or payload.get("page_ids") != EXPECTED_PAGE_IDS
        or payload.get("projects") != ["desktop", "tablet", "mobile"]
        or payload.get("runtime_url") != "http://127.0.0.1:8870"
        or raw_measurements.get("page_ids") != EXPECTED_PAGE_IDS
        or raw_measurements.get("runtime_url") != "http://127.0.0.1:8870"
        or reporter_check_projects != BROWSER_REQUIRED_TEST_PROJECTS
    ):
        return "NOT_PROVEN", "BROWSER_POSITIVE_COUNTS_OR_EXACT_SCOPE_MISMATCH"
    return "PASS", "BROWSER_REQUIRED_CHECKS_COMMAND_COUNTS_AND_ARTIFACTS_PASS"


def _validate_full_suite(
    payload: Mapping[str, object],
    checks: Mapping[str, object],
) -> tuple[str, str]:
    if set(checks) != FULL_SUITE_REQUIRED_CHECKS:
        return "NOT_PROVEN", "FULL_SUITE_REQUIRED_CHECK_SET_MISMATCH"
    source_commit = payload.get("source_commit")
    if (
        not isinstance(source_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
        or payload.get("source_commit_at_end") != source_commit
        or payload.get("source_worktree_clean_at_start") is not True
        or payload.get("source_worktree_clean_at_end") is not True
        or payload.get("source_worktree_clean_at_measurement") is not True
    ):
        return "NOT_PROVEN", "FULL_SUITE_SOURCE_PROVENANCE_BINDING_MISMATCH"
    commands = payload.get("commands")
    counts = payload.get("counts")
    if not isinstance(commands, list) or not isinstance(counts, dict):
        return "NOT_PROVEN", "FULL_SUITE_COMMANDS_OR_COUNTS_MISSING"
    if set(counts) != {"command_count", "backend_test_count", "frontend_test_count"}:
        return "NOT_PROVEN", "FULL_SUITE_COUNT_SCOPE_MISMATCH"
    artifact_failure = _validate_artifacts(
        payload,
        required_kinds=frozenset({"artifact", "log"}),
    )
    if artifact_failure is not None:
        return artifact_failure
    artifacts = _artifact_records(payload)
    artifact_by_path = {
        str(record["path"]): record for record in artifacts if isinstance(record.get("path"), str)
    }
    manifest_record = _artifact_with_format(payload, "full_suite_test_manifest_json")
    manifest_path = manifest_record.get("path") if manifest_record is not None else None
    if not isinstance(manifest_path, str):
        return "NOT_PROVEN", "FULL_SUITE_COLLECTION_MANIFEST_MISSING_OR_INVALID"
    collection_paths: set[str] = set()
    for artifact_format in ("pytest_collection_text", "vitest_collection_text"):
        collection_record = _artifact_with_format(payload, artifact_format)
        collection_path = collection_record.get("path") if collection_record is not None else None
        if not isinstance(collection_path, str):
            return "NOT_PROVEN", "FULL_SUITE_COLLECTION_OUTPUT_MISSING_OR_INVALID"
        collection_paths.add(collection_path)
    master_e2e_record = _artifact_with_format(payload, "master_e2e_bundle_json")
    master_e2e_report = _json_artifact(master_e2e_record) if master_e2e_record is not None else None
    auxiliary_paths: set[str] = set()
    if not isinstance(master_e2e_report, dict):
        return "NOT_PROVEN", "FULL_SUITE_MASTER_E2E_BUNDLE_MISSING_OR_INVALID"
    for key, artifact_format in (
        ("pytest_junit", "master_e2e_pytest_junit_xml"),
        ("playwright_json", "master_e2e_playwright_json"),
    ):
        embedded_record = master_e2e_report.get(key)
        top_level_record = _artifact_with_format(payload, artifact_format)
        embedded_path = embedded_record.get("path") if isinstance(embedded_record, dict) else None
        if (
            not isinstance(embedded_path, str)
            or top_level_record is None
            or embedded_record != top_level_record
        ):
            return "NOT_PROVEN", "FULL_SUITE_MASTER_E2E_ARTIFACT_BINDING_MISMATCH"
        auxiliary_paths.add(embedded_path)
    command_names: set[str] = set()
    command_order: list[str] = []
    report_paths: set[str] = set()
    derived_counts: dict[str, int] = {}
    for command_record in commands:
        if not isinstance(command_record, dict):
            return "NOT_PROVEN", "FULL_SUITE_COMMAND_RECORD_INVALID"
        name = command_record.get("name")
        command = command_record.get("command")
        exit_code = command_record.get("exit_code")
        report_path = command_record.get("report_path")
        expected_command = (
            [part.format(report_path=report_path) for part in FULL_SUITE_CANONICAL_COMMANDS[name]]
            if isinstance(name, str)
            and name in FULL_SUITE_CANONICAL_COMMANDS
            and isinstance(report_path, str)
            else None
        )
        if (
            not isinstance(name, str)
            or name not in FULL_SUITE_COMMAND_NAMES
            or name in command_names
            or command != expected_command
            or not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or exit_code != 0
            or not isinstance(report_path, str)
            or not report_path.strip()
            or report_path in report_paths
        ):
            return "NOT_PROVEN", "FULL_SUITE_COMMAND_NAME_EXIT_OR_LOG_INVALID"
        artifact_record = artifact_by_path.get(report_path)
        if (
            artifact_record is None
            or artifact_record.get("format") != FULL_SUITE_REPORT_FORMATS[name]
        ):
            return "NOT_PROVEN", "FULL_SUITE_REPORT_FORMAT_OR_PATH_MISMATCH"
        derived_count = _full_suite_report_count(name, artifact_record)
        if derived_count is None or derived_count <= 0:
            return "NOT_PROVEN", "FULL_SUITE_MACHINE_REPORT_PARSE_FAILED"
        command_names.add(name)
        command_order.append(name)
        report_paths.add(report_path)
        derived_counts[name] = derived_count
    if command_names != FULL_SUITE_COMMAND_NAMES or command_order != list(FULL_SUITE_COMMAND_ORDER):
        return "NOT_PROVEN", "FULL_SUITE_REQUIRED_COMMAND_MISSING"
    if report_paths | {manifest_path} | collection_paths | auxiliary_paths != set(artifact_by_path):
        return "NOT_PROVEN", "FULL_SUITE_COMMAND_LOG_ARTIFACT_SCOPE_MISMATCH"
    if (
        counts.get("command_count") != len(FULL_SUITE_COMMAND_NAMES)
        or len(commands) != len(FULL_SUITE_COMMAND_NAMES)
        or counts.get("backend_test_count") != derived_counts["backend_pytest"]
        or counts.get("frontend_test_count") != derived_counts["frontend_tests"]
    ):
        return "NOT_PROVEN", "FULL_SUITE_RECOMPUTED_COUNTS_MISMATCH"
    backend_report = artifact_by_path[
        next(
            command["report_path"]
            for command in commands
            if isinstance(command, dict) and command.get("name") == "backend_pytest"
        )
    ]
    frontend_report = artifact_by_path[
        next(
            command["report_path"]
            for command in commands
            if isinstance(command, dict) and command.get("name") == "frontend_tests"
        )
    ]
    backend_report_ids = _pytest_junit_test_ids(backend_report)
    frontend_report_ids = _frontend_vitest_test_ids(frontend_report)
    if backend_report_ids is None or frontend_report_ids is None:
        return "NOT_PROVEN", "FULL_SUITE_MACHINE_TEST_IDS_MISSING"
    manifest_failure = _full_suite_manifest_test_ids(
        payload,
        backend_report_ids=backend_report_ids,
        frontend_report_ids=frontend_report_ids,
    )
    if manifest_failure is not None:
        return manifest_failure
    return "PASS", "FULL_SUITE_REQUIRED_CHECKS_COMMANDS_COUNTS_AND_LOGS_PASS"


def _validate_checked_evidence_kind(
    kind: str,
    payload: Mapping[str, object],
    checks: Mapping[str, object],
) -> tuple[str, str]:
    if kind == "dashboard_payload_benchmark":
        return _validate_dashboard_benchmark(payload, checks)
    if kind == "browser_e2e_after_latest_change":
        return _validate_browser_e2e(payload, checks)
    if kind == "full_suite_after_latest_change":
        return _validate_full_suite(payload, checks)
    if kind == "thirty_minute_soak":
        return _validate_running_service_soak(payload, checks)
    return "NOT_PROVEN", "EVIDENCE_KIND_NOT_SUPPORTED"


def _normalize_evidence_status(raw_status: object) -> str:
    if not isinstance(raw_status, str) or not raw_status.strip():
        return "NOT_PROVEN"
    status = raw_status.strip().upper()
    if "FAIL" in status or "ERROR" in status or status == "INVALID":
        return "FAIL"
    if "BLOCKED" in status or "ABORTED" in status:
        return "BLOCKED"
    if "NOT_RUN" in status:
        return "NOT_RUN"
    if "NOT_PROVEN" in status or status in {"PARTIAL", "UNKNOWN"}:
        return "NOT_PROVEN"
    if status == "PASS" or status.startswith("PASS_") or status.startswith("PASS_WITH_"):
        return "PASS"
    return "NOT_PROVEN"


def _load_evidence(
    relative_path: str,
    *,
    require_checks: bool = False,
    evidence_kind: str | None = None,
    expected_schema_version: int | None = None,
    expected_source_commit: str | None = None,
    source_working_tree_changes: list[str] | None = None,
    require_release_binding: bool = False,
) -> dict[str, Any]:
    path = PROJECT_ROOT / relative_path
    if not path.is_file():
        return {
            "status": "NOT_RUN",
            "path": relative_path,
            "raw_status": None,
            "reason": "EVIDENCE_FILE_NOT_FOUND",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "status": "FAIL",
            "path": relative_path,
            "raw_status": None,
            "reason": "EVIDENCE_FILE_INVALID_JSON",
        }
    if not isinstance(payload, dict):
        return {
            "status": "FAIL",
            "path": relative_path,
            "raw_status": None,
            "reason": "EVIDENCE_ROOT_NOT_OBJECT",
        }
    raw_status = payload.get("status")
    status = _normalize_evidence_status(raw_status)
    checks = payload.get("checks")
    if require_checks and status == "PASS":
        if not isinstance(checks, dict) or not checks:
            status = "NOT_PROVEN"
        elif any(value is not True for value in checks.values()):
            status = "FAIL"
    binding_reason = "DECLARED_STATUS_AND_CHECKS"
    if status == "PASS" and expected_schema_version is not None:
        if payload.get("schema_version") != expected_schema_version:
            status = "NOT_PROVEN"
            binding_reason = "EVIDENCE_SCHEMA_VERSION_MISMATCH"
    if status == "PASS" and not _valid_evidence_timestamp(payload.get("generated_ts_utc")):
        status = "NOT_PROVEN"
        binding_reason = "EVIDENCE_GENERATED_TIMESTAMP_INVALID"
    if status == "PASS" and expected_source_commit is not None:
        source_commit = payload.get("source_commit")
        if source_working_tree_changes:
            status = "NOT_PROVEN"
            binding_reason = "UNCOMMITTED_SOURCE_DIFFERS_FROM_EVIDENCE"
        elif payload.get("source_worktree_clean_at_measurement") is not True:
            status = "NOT_PROVEN"
            binding_reason = "EVIDENCE_SOURCE_WORKTREE_NOT_CLEAN_AT_MEASUREMENT"
        elif not isinstance(source_commit, str) or not _commits_have_equivalent_source(
            source_commit,
            expected_source_commit,
        ):
            status = "NOT_PROVEN"
            binding_reason = "EVIDENCE_SOURCE_COMMIT_NOT_EQUIVALENT_TO_HEAD"
        elif require_release_binding:
            release_commit = payload.get("release_commit")
            release_isolated = (
                payload.get("release_isolated") is True
                or payload.get("release_isolated_throughout") is True
            )
            if not release_isolated:
                status = "NOT_PROVEN"
                binding_reason = "EVIDENCE_RELEASE_NOT_ISOLATED"
            elif not isinstance(release_commit, str) or not (
                _commits_have_equivalent_source(release_commit, source_commit)
                and _commits_have_equivalent_source(release_commit, expected_source_commit)
            ):
                status = "NOT_PROVEN"
                binding_reason = "EVIDENCE_RELEASE_COMMIT_NOT_EQUIVALENT_TO_SOURCE"
    if status == "PASS" and require_checks:
        resolved_kind = evidence_kind or CHECKED_EVIDENCE_KINDS_BY_PATH.get(relative_path)
        if resolved_kind is None:
            status = "NOT_PROVEN"
            binding_reason = "EVIDENCE_KIND_REQUIRED_FOR_CHECK_VALIDATION"
        elif isinstance(checks, dict):
            status, binding_reason = _validate_checked_evidence_kind(
                resolved_kind,
                payload,
                checks,
            )
    return {
        "status": status,
        "path": relative_path,
        "raw_status": raw_status,
        "reason": (
            binding_reason
            if status == "PASS"
            else binding_reason
            if binding_reason != "DECLARED_STATUS_AND_CHECKS"
            else "DECLARED_STATUS_OR_REQUIRED_CHECKS_NOT_PASS"
        ),
        "payload": payload,
    }


def _soak_runtime_binding_failure(payload: dict[str, Any]) -> str | None:
    generated_ts_utc = payload.get("generated_ts_utc")
    run_id = payload.get("run_id")
    source_commit = payload.get("source_commit")
    release_commit = payload.get("release_commit")
    if not _valid_evidence_timestamp(generated_ts_utc):
        return "SOAK_TIMESTAMP_BINDING_MISSING"
    if not isinstance(run_id, str) or not run_id.strip():
        return "SOAK_RUN_BINDING_MISSING"
    if (
        not isinstance(source_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
        or payload.get("source_worktree_clean_at_measurement") is not True
    ):
        return "SOAK_SOURCE_BINDING_MISSING"
    if (
        not isinstance(release_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", release_commit) is None
        or payload.get("release_isolated_throughout") is not True
    ):
        return "SOAK_RELEASE_BINDING_MISSING"
    return None


def _validated_soak_runtime_observation(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """상위 검증을 통과한 soak에서 run·release·source 결합 과거값만 추린다."""

    if _soak_runtime_binding_failure(payload) is not None:
        return None
    generated_ts_utc = payload["generated_ts_utc"]
    run_id = payload["run_id"]
    source_commit = payload["source_commit"]
    release_commit = payload["release_commit"]
    final = payload.get("final")
    if not isinstance(final, dict):
        final = {}
    mode_counts = payload.get("strategy_mode_counts")
    if not isinstance(mode_counts, dict):
        mode_counts = None

    raw_rows = _strict_non_negative_int(payload.get("history_raw_rows"))
    unique_opportunities = _strict_non_negative_int(payload.get("unique_opportunities"))
    analytics_cache_ready = payload.get("analytics_cache_ready")
    if not isinstance(analytics_cache_ready, bool):
        analytics_cache_ready = None
    paper_portfolio_flat = final.get("paper_portfolio_flat")
    if not isinstance(paper_portfolio_flat, bool):
        paper_portfolio_flat = None
    return {
        "evidence_path": EVIDENCE_PATHS["thirty_minute_soak"],
        "generated_ts_utc": generated_ts_utc,
        "run_id": run_id,
        "source_commit": source_commit,
        "release_commit": release_commit,
        "release_isolated": True,
        "strategy_mode_counts": mode_counts,
        "open_positions": _strict_non_negative_int(final.get("total_open_position_count")),
        "main_pending_entry_count": _strict_non_negative_int(final.get("main_pending_entry_count")),
        "league_pending_entry_count": _strict_non_negative_int(
            final.get("league_pending_entry_count")
        ),
        "total_pending_entry_count": _strict_non_negative_int(
            final.get("total_pending_entry_count")
        ),
        "paper_portfolio_flat": paper_portfolio_flat,
        "history_raw_rows": raw_rows,
        "unique_opportunities": unique_opportunities,
        "analytics_cache_ready": analytics_cache_ready,
    }


def _thirty_minute_soak_evidence(
    *,
    expected_strategy_ids: list[str],
    expected_account_ids: list[str],
    expected_mode_counts: dict[str, int],
    expected_source_commit: str,
    source_working_tree_changes: list[str],
) -> dict[str, Any]:
    evidence = _load_evidence(
        EVIDENCE_PATHS["thirty_minute_soak"],
        require_checks=True,
        evidence_kind="thirty_minute_soak",
        expected_schema_version=1,
        expected_source_commit=expected_source_commit,
        source_working_tree_changes=source_working_tree_changes,
        require_release_binding=True,
    )
    if evidence["status"] != "PASS":
        evidence.pop("payload", None)
        return evidence
    wrapper_payload = evidence.pop("payload")
    if not isinstance(wrapper_payload, dict):
        evidence["status"] = "FAIL"
        evidence["reason"] = "EVIDENCE_ROOT_NOT_OBJECT"
        return evidence
    payload = _running_service_soak_measurement(wrapper_payload)
    if payload is None:
        evidence["status"] = "NOT_PROVEN"
        evidence["reason"] = "SOAK_MACHINE_REPORT_INVALID"
        return evidence
    final = payload.get("final")
    safety = payload.get("paper_safety")
    if not isinstance(final, dict) or not isinstance(safety, dict):
        evidence["status"] = "FAIL"
        evidence["reason"] = "SOAK_REQUIRED_OBJECT_MISSING"
        return evidence
    try:
        requested_seconds = float(str(payload.get("requested_duration_seconds")))
        observed_seconds = float(str(payload.get("observed_duration_seconds")))
        strategy_count = int(str(final.get("strategy_count")))
        account_count = int(str(final.get("league_account_count")))
    except (TypeError, ValueError):
        evidence["status"] = "FAIL"
        evidence["reason"] = "SOAK_REQUIRED_MEASUREMENT_INVALID"
        return evidence
    strategy_ids = payload.get("strategy_ids")
    account_ids = payload.get("league_account_ids")
    mode_counts = payload.get("strategy_mode_counts")
    scope_matches = (
        strategy_count == len(expected_strategy_ids)
        and account_count == len(expected_account_ids)
        and strategy_ids == sorted(expected_strategy_ids)
        and account_ids == sorted(expected_account_ids)
        and mode_counts == expected_mode_counts
        and isinstance(mode_counts, dict)
        and mode_counts.get("ACTIVE") == 0
    )
    if not scope_matches:
        evidence["status"] = "NOT_PROVEN"
        evidence["reason"] = "SOAK_V6_STRATEGY_ACCOUNT_OR_MODE_SCOPE_MISMATCH"
        return evidence
    safety_passed = (
        final.get("execution_state") == "PAPER"
        and final.get("real_orders_enabled") is False
        and final.get("auth_required") is False
        and safety.get("real_orders_enabled") is False
        and safety.get("auth_required") is False
        and safety.get("private_api_requested") is False
        and safety.get("api_key_requested") is False
        and safety.get("wallet_requested") is False
        and safety.get("additional_market_connection_started") is False
    )
    if not safety_passed:
        evidence["status"] = "FAIL"
        evidence["reason"] = "SOAK_PAPER_SAFETY_CONTRACT_FAILED"
    elif requested_seconds < 1_800 or observed_seconds < requested_seconds:
        evidence["status"] = "NOT_RUN"
        evidence["reason"] = "SOAK_30_MINUTE_DURATION_NOT_COMPLETED"
    else:
        validated_observation = _validated_soak_runtime_observation(payload)
        if validated_observation is None:
            evidence["status"] = "NOT_PROVEN"
            evidence["reason"] = (
                _soak_runtime_binding_failure(payload) or "SOAK_RUNTIME_PROVENANCE_BINDING_MISSING"
            )
        else:
            evidence["reason"] = "V6_30_MINUTE_SCOPE_AND_SAFETY_PASS"
            evidence["validated_runtime_observation"] = validated_observation
    return evidence


def _validated_current_thirty_minute_soak(
    expected_source_commit: str,
) -> dict[str, Any]:
    registry = StrategyRegistry()
    strategy_ids = list(registry.strategy_ids)
    registry_rows = registry.rows()
    account_ids = [
        f"{strategy_id}:{profile}" for strategy_id in strategy_ids for profile in ("BASE", "STRESS")
    ]
    mode_counts = {
        mode: sum(row["mode"] == mode for row in registry_rows)
        for mode in ("ACTIVE", "SHADOW", "OFF")
    }
    return _thirty_minute_soak_evidence(
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=mode_counts,
        expected_source_commit=expected_source_commit,
        source_working_tree_changes=[],
    )


def _launch_agent_evidence(
    deployment: dict[str, Any],
    *,
    runtime_observation: dict[str, Any],
) -> dict[str, Any]:
    """설치 plist와 현재 LaunchAgent 프로세스를 동일 릴리스에 결합한다."""

    plist_path = LAUNCH_AGENT_PLIST_PATH
    evidence: dict[str, Any] = {
        "status": "NOT_RUN",
        "reason": "LAUNCH_AGENT_PLIST_NOT_FOUND",
        "path": str(plist_path),
        "evidence_boundary": LAUNCH_AGENT_EVIDENCE_BOUNDARY,
        "plist_contract": {"status": "NOT_RUN"},
        "process_binding": {"status": "NOT_RUN"},
    }
    if plist_path.is_symlink():
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_PLIST_NOT_REGULAR_FILE"
        evidence["plist_contract"] = {"status": "FAIL"}
        return evidence
    if not plist_path.is_file():
        return evidence

    try:
        plist_bytes = plist_path.read_bytes()
        payload = plistlib.loads(plist_bytes)
    except (OSError, plistlib.InvalidFileException, ValueError):
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_PLIST_INVALID"
        evidence["plist_contract"] = {"status": "FAIL"}
        return evidence
    expected_runner = RUNTIME_ROOT / "support/run_macos_service.sh"
    expected_stdout = RUNTIME_ROOT / "logs/service.log"
    expected_stderr = RUNTIME_ROOT / "logs/service-error.log"
    expected_fields = {
        "Label",
        "ProgramArguments",
        "RunAtLoad",
        "KeepAlive",
        "ThrottleInterval",
        "ExitTimeOut",
        "ProcessType",
        "StandardOutPath",
        "StandardErrorPath",
    }
    arguments = payload.get("ProgramArguments") if isinstance(payload, dict) else None
    exact_contract = (
        isinstance(payload, dict)
        and set(payload) == expected_fields
        and type(payload.get("Label")) is str
        and payload.get("Label") == LAUNCH_AGENT_LABEL
        and type(arguments) is list
        and len(arguments) == 2
        and all(type(value) is str for value in arguments)
        and arguments == ["/bin/zsh", str(expected_runner)]
        and type(payload.get("RunAtLoad")) is bool
        and payload.get("RunAtLoad") is True
        and type(payload.get("KeepAlive")) is bool
        and payload.get("KeepAlive") is True
        and type(payload.get("ThrottleInterval")) is int
        and payload.get("ThrottleInterval") == 10
        and type(payload.get("ExitTimeOut")) is int
        and payload.get("ExitTimeOut") == 60
        and type(payload.get("ProcessType")) is str
        and payload.get("ProcessType") == "Background"
        and type(payload.get("StandardOutPath")) is str
        and payload.get("StandardOutPath") == str(expected_stdout)
        and type(payload.get("StandardErrorPath")) is str
        and payload.get("StandardErrorPath") == str(expected_stderr)
    )
    if not exact_contract:
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_PLIST_EXACT_CONTRACT_MISMATCH"
        evidence["plist_contract"] = {"status": "FAIL"}
        return evidence
    if expected_runner.is_symlink() or not expected_runner.is_file():
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_TRUSTED_RUNNER_NOT_REGULAR_FILE"
        evidence["plist_contract"] = {"status": "FAIL"}
        return evidence

    try:
        runner_sha256 = hashlib.sha256(expected_runner.read_bytes()).hexdigest()
    except OSError:
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_TRUSTED_RUNNER_NOT_READABLE"
        evidence["plist_contract"] = {"status": "FAIL"}
        return evidence
    plist_sha256 = hashlib.sha256(plist_bytes).hexdigest()
    evidence["plist_contract"] = {
        "status": "PASS",
        "label": LAUNCH_AGENT_LABEL,
        "program_arguments": arguments,
        "run_at_load": True,
        "keep_alive": True,
        "throttle_interval_seconds": 10,
        "exit_timeout_seconds": 60,
        "process_type": "Background",
        "stdout_path": str(expected_stdout),
        "stderr_path": str(expected_stderr),
        "sha256": plist_sha256,
        "trusted_runner_sha256": runner_sha256,
    }
    if runtime_observation.get("available") is not True:
        evidence["reason"] = "LAUNCH_AGENT_PROCESS_NOT_OBSERVED"
        return evidence

    release_commit = deployment.get("release_commit")
    if not isinstance(release_commit, str) or re.fullmatch(r"[0-9a-f]{40}", release_commit) is None:
        evidence["status"] = "NOT_PROVEN"
        evidence["reason"] = "LAUNCH_AGENT_DEPLOYMENT_COMMIT_INVALID"
        return evidence
    if (
        runtime_observation.get("release_commit") != release_commit
        or runtime_observation.get("release_isolated") is not True
    ):
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_RUNTIME_RELEASE_BINDING_MISMATCH"
        return evidence

    release_path = RUNTIME_ROOT / "releases" / release_commit
    ledger_path = RUNTIME_ROOT / "active-ledger/run-ledger.sqlite3"
    try:
        process_binding = verify_running_process_binding(
            ledger_path=ledger_path,
            release_path=release_path,
        )
        service_pid = process_binding.get("service_pid")
        binding_exact = (
            type(service_pid) is int
            and service_pid > 0
            and process_binding.get("launch_agent_label") == LAUNCH_AGENT_LABEL
            and process_binding.get("listener") == "127.0.0.1:8870"
            and process_binding.get("cwd") == str(release_path)
            and process_binding.get("ledger_open_by_service_pid") is True
        )
        plist_unchanged = (
            not plist_path.is_symlink()
            and plist_path.is_file()
            and hashlib.sha256(plist_path.read_bytes()).hexdigest() == plist_sha256
        )
        runner_unchanged = (
            not expected_runner.is_symlink()
            and expected_runner.is_file()
            and hashlib.sha256(expected_runner.read_bytes()).hexdigest() == runner_sha256
        )
    except (LegacyRuntimePreflightError, OSError):
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_RUNNING_PROCESS_BINDING_FAILED"
        evidence["process_binding"] = {"status": "FAIL"}
        return evidence
    if not binding_exact:
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_RUNNING_PROCESS_BINDING_MISMATCH"
        evidence["process_binding"] = {"status": "FAIL"}
        return evidence
    if not plist_unchanged:
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_PLIST_CHANGED_DURING_VERIFICATION"
        evidence["process_binding"] = {"status": "FAIL"}
        return evidence
    if not runner_unchanged:
        evidence["status"] = "FAIL"
        evidence["reason"] = "LAUNCH_AGENT_TRUSTED_RUNNER_CHANGED_DURING_VERIFICATION"
        evidence["process_binding"] = {"status": "FAIL"}
        return evidence

    evidence["status"] = "PASS"
    evidence["reason"] = "EXACT_PLIST_AND_RUNNING_PROCESS_BOUND_TO_INSTALLED_RELEASE"
    evidence["process_binding"] = {**process_binding, "status": "PASS"}
    return evidence


def _installed_release_evidence(
    deployment: dict[str, Any],
    *,
    latest_commit: str,
    working_tree_changes: list[str],
    release_package_evidence: dict[str, Any],
    launch_agent_evidence: dict[str, Any],
) -> dict[str, Any]:
    def result(status: str, reason: str) -> dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            "evidence_boundary": INSTALLED_RELEASE_EVIDENCE_BOUNDARY,
        }

    status = _normalize_evidence_status(deployment.get("status"))
    reason = "CURRENT_DEPLOYMENT_DECLARATION"
    if status != "PASS":
        return result(status, reason)
    if (
        deployment.get("paper_only") is not True
        or deployment.get("real_orders_enabled") is not False
        or deployment.get("auth_required") is not False
    ):
        return result("FAIL", "INSTALLED_PAPER_SAFETY_CONTRACT_FAILED")
    if release_package_evidence["status"] != "PASS":
        return result(
            str(release_package_evidence["status"]),
            f"INSTALLED_{release_package_evidence['reason']}",
        )
    launch_agent_status = _normalize_evidence_status(launch_agent_evidence.get("status"))
    if launch_agent_status != "PASS":
        return result(
            launch_agent_status,
            f"INSTALLED_{launch_agent_evidence.get('reason', 'LAUNCH_AGENT_NOT_PROVEN')}",
        )
    if not _release_commit_matches_source(deployment.get("release_commit"), latest_commit):
        return result("NOT_PROVEN", "INSTALLED_COMMIT_DIFFERS_FROM_HEAD")
    if working_tree_changes:
        return result("NOT_PROVEN", "WORKING_TREE_DIFFERS_FROM_INSTALLED_HEAD")
    return result("PASS", "INSTALLED_SAFE_COMMIT_LAUNCH_AGENT_AND_PROCESS_MATCH_CLEAN_HEAD")


def _strict_json_file_object(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> object:
        raise ValueError(f"비표준 JSON 숫자: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"중복 JSON key: {key}")
            result[key] = value
        return result

    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    if not isinstance(payload, dict):
        raise ValueError("JSON object가 아닙니다.")
    return payload


def _release_package_evidence(
    deployment: dict[str, Any],
    *,
    latest_commit: str,
    working_tree_changes: list[str],
) -> dict[str, Any]:
    release_commit = deployment.get("release_commit")
    if not isinstance(release_commit, str) or not release_commit:
        return {"status": "NOT_RUN", "reason": "INSTALLED_RELEASE_COMMIT_NOT_FOUND"}
    if re.fullmatch(r"[0-9a-f]{40}", release_commit) is None:
        return {
            "status": "NOT_PROVEN",
            "reason": "INSTALLED_RELEASE_COMMIT_INVALID",
        }
    relative_path = f"releases/{release_commit}/release-manifest.json"
    manifest_path = RUNTIME_ROOT / relative_path
    if not manifest_path.is_file():
        return {
            "status": "NOT_RUN",
            "path": relative_path,
            "reason": "RELEASE_MANIFEST_NOT_FOUND",
        }
    try:
        manifest = _strict_json_file_object(manifest_path)
    except (json.JSONDecodeError, OSError, UnicodeError, ValueError):
        return {"status": "FAIL", "path": relative_path, "reason": "INVALID_MANIFEST"}
    if not isinstance(manifest, dict):
        return {"status": "FAIL", "path": relative_path, "reason": "INVALID_MANIFEST"}
    current_link = RUNTIME_ROOT / "current"
    releases_path = RUNTIME_ROOT / "releases"
    if not current_link.is_symlink() or releases_path.is_symlink():
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "CURRENT_LINK_OR_RELEASES_ROOT_INVALID",
        }
    try:
        releases_root = releases_path.resolve(strict=True)
        current_release = current_link.resolve(strict=True)
    except OSError:
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "CURRENT_RELEASE_TARGET_NOT_RESOLVABLE",
        }
    if current_release.parent != releases_root or current_release.name != release_commit:
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "CURRENT_LINK_NOT_DIRECT_RELEASE_COMMIT_CHILD",
        }
    support_path = RUNTIME_ROOT / "support"
    anchor_path = support_path / "current-release-integrity.json"
    launcher_path = support_path / "run_macos_service.sh"
    if (
        support_path.is_symlink()
        or anchor_path.is_symlink()
        or not anchor_path.is_file()
        or launcher_path.is_symlink()
        or not launcher_path.is_file()
    ):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_OR_VERIFIER_INVALID",
        }
    try:
        anchor = _strict_json_file_object(anchor_path)
        resolved_launcher = launcher_path.resolve(strict=True)
        manifest_bytes = manifest_path.read_bytes()
        launcher_bytes = resolved_launcher.read_bytes()
    except (json.JSONDecodeError, OSError, UnicodeError, ValueError):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_OR_VERIFIER_INVALID",
        }
    expected_anchor_fields = {
        "schema_version",
        "release_path",
        "release_commit",
        "manifest_sha256",
        "launcher_path",
        "launcher_sha256",
        "launcher_source_release_path",
        "launcher_source_commit",
        "launcher_source_manifest_sha256",
        "paper_only",
        "real_orders_enabled",
    }
    if not isinstance(anchor, dict) or set(anchor) != expected_anchor_fields:
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_SCHEMA_INVALID",
        }
    if (
        type(anchor.get("schema_version")) is not int
        or anchor.get("schema_version") != 2
        or anchor.get("release_path") != str(current_release)
        or anchor.get("release_commit") != release_commit
        or anchor.get("launcher_path") != str(resolved_launcher)
        or anchor.get("paper_only") is not True
        or anchor.get("real_orders_enabled") is not False
    ):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_BINDING_MISMATCH",
        }
    source_release_value = anchor.get("launcher_source_release_path")
    source_commit = anchor.get("launcher_source_commit")
    if (
        not isinstance(source_release_value, str)
        or not source_release_value
        or not isinstance(source_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
    ):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_SOURCE_BINDING_INVALID",
        }
    source_release = Path(source_release_value)
    source_manifest_path = source_release / "release-manifest.json"
    source_launcher_path = source_release / "scripts/run_macos_service.sh"
    try:
        resolved_source_release = source_release.resolve(strict=True)
    except OSError:
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_SOURCE_NOT_RESOLVABLE",
        }
    if (
        source_release.is_symlink()
        or resolved_source_release != source_release.absolute()
        or resolved_source_release.parent != releases_root
        or resolved_source_release.name != source_commit
        or source_manifest_path.is_symlink()
        or not source_manifest_path.is_file()
        or source_launcher_path.is_symlink()
        or not source_launcher_path.is_file()
    ):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_SOURCE_BINDING_INVALID",
        }
    try:
        source_manifest_bytes = source_manifest_path.read_bytes()
        source_launcher_bytes = source_launcher_path.read_bytes()
        source_manifest = _strict_json_file_object(source_manifest_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_SOURCE_NOT_READABLE",
        }
    if (
        type(source_manifest.get("schema_version")) is not int
        or source_manifest.get("schema_version") != 2
        or source_manifest.get("commit") != source_commit
    ):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_SOURCE_MANIFEST_INVALID",
        }
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    launcher_sha256 = hashlib.sha256(launcher_bytes).hexdigest()
    if anchor.get("manifest_sha256") != manifest_sha256:
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_MANIFEST_SHA_MISMATCH",
        }
    source_manifest_sha256 = hashlib.sha256(source_manifest_bytes).hexdigest()
    if anchor.get("launcher_source_manifest_sha256") != source_manifest_sha256:
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_SOURCE_MANIFEST_SHA_MISMATCH",
        }
    if (
        anchor.get("launcher_sha256") != launcher_sha256
        or launcher_sha256 != hashlib.sha256(source_launcher_bytes).hexdigest()
    ):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_INTEGRITY_ANCHOR_VERIFIER_SHA_MISMATCH",
        }
    manifest_commit = manifest.get("commit")
    safety_passed = (
        manifest.get("paper_only") is True
        and manifest.get("real_orders_enabled") is False
        and manifest.get("auth_required") is False
        and manifest.get("private_api_enabled") is False
        and manifest.get("wallet_paths_enabled") is False
    )
    if not safety_passed:
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_MANIFEST_PAPER_SAFETY_FAILED",
        }
    if (
        type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 2
        or manifest_commit != release_commit
    ):
        return {
            "status": "NOT_PROVEN",
            "path": relative_path,
            "reason": "RELEASE_MANIFEST_NOT_V2_OR_COMMIT_MISMATCH",
        }
    try:
        stage_macos_release._verify_release_tree(  # noqa: SLF001
            current_release,
            expected_commit=release_commit,
        )
        stage_macos_release._verify_release_tree(  # noqa: SLF001
            resolved_source_release,
            expected_commit=source_commit,
        )
    except (RuntimeError, OSError, UnicodeError, json.JSONDecodeError):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_TREE_FULL_HASH_VERIFICATION_FAILED",
        }
    try:
        manifest_sha256_after_tree_verification = hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        source_manifest_sha256_after_tree_verification = hashlib.sha256(
            source_manifest_path.read_bytes()
        ).hexdigest()
        launcher_sha256_after_tree_verification = hashlib.sha256(
            launcher_path.read_bytes()
        ).hexdigest()
        source_launcher_sha256_after_tree_verification = hashlib.sha256(
            source_launcher_path.read_bytes()
        ).hexdigest()
    except OSError:
        manifest_sha256_after_tree_verification = ""
        source_manifest_sha256_after_tree_verification = ""
        launcher_sha256_after_tree_verification = ""
        source_launcher_sha256_after_tree_verification = ""
    if (
        manifest_sha256_after_tree_verification != manifest_sha256
        or source_manifest_sha256_after_tree_verification != source_manifest_sha256
        or launcher_sha256_after_tree_verification != launcher_sha256
        or source_launcher_sha256_after_tree_verification != launcher_sha256
    ):
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_MANIFEST_CHANGED_DURING_VERIFICATION",
        }
    if not _release_commit_matches_source(release_commit, latest_commit) or working_tree_changes:
        return {
            "status": "NOT_PROVEN",
            "path": relative_path,
            "reason": "RELEASE_PACKAGE_DIFFERS_FROM_CURRENT_SOURCE",
        }
    return {
        "status": "PASS",
        "path": relative_path,
        "reason": "V2_SAFE_RELEASE_TREE_AND_EXTERNAL_ANCHOR_MATCH_CLEAN_HEAD",
    }


def _remote_sync_evidence(
    *,
    latest_commit: str,
    working_tree_changes: list[str],
) -> dict[str, Any]:
    remote_commit = _remote_main_commit()
    if remote_commit is None:
        return {"status": "NOT_RUN", "reason": "REMOTE_ORIGIN_MAIN_NOT_OBSERVED"}
    if remote_commit != latest_commit:
        return {
            "status": "NOT_PROVEN",
            "reason": "HEAD_AND_ORIGIN_MAIN_DIFFER",
            "origin_main_commit": remote_commit,
        }
    if working_tree_changes:
        return {
            "status": "NOT_PROVEN",
            "reason": "UNCOMMITTED_SOURCE_NOT_PRESENT_ON_ORIGIN_MAIN",
            "origin_main_commit": remote_commit,
        }
    return {
        "status": "PASS",
        "reason": "CLEAN_HEAD_MATCHES_REMOTE_ORIGIN_MAIN",
        "origin_main_commit": remote_commit,
    }


def _overall_report_status(
    *,
    source_status: str,
    evidence_statuses: dict[str, str],
    runtime_status: str,
) -> str:
    required = (
        "dashboard_payload_benchmark",
        "browser_e2e_after_latest_change",
        "full_suite_after_latest_change",
        "thirty_minute_soak",
        "release_package",
        "launch_agent",
        "installed_release",
        "remote_push",
    )
    states = [evidence_statuses[name] for name in required]
    if source_status != "PASS":
        return "FAIL_SOURCE_CONTRACT"
    if "FAIL" in states or runtime_status == "FAIL":
        return "FAIL_REQUIRED_EVIDENCE_OR_RUNTIME"
    if "BLOCKED" in states or runtime_status == "BLOCKED":
        return "BLOCKED_REQUIRED_EVIDENCE_OR_RUNTIME"
    if runtime_status in {"NOT_RUN", "STOPPED"}:
        return "NOT_RUN_STOPPED_OR_UNREACHABLE_RUNTIME"
    if "NOT_RUN" in states:
        return "NOT_RUN_REQUIRED_EVIDENCE"
    if "NOT_PROVEN" in states or runtime_status != "PASS":
        return "NOT_PROVEN_REQUIRED_EVIDENCE_OR_RUNTIME"
    return "PASS_WITH_NOT_PROVEN_RESEARCH"


def build_report() -> dict[str, Any]:
    registry = StrategyRegistry()
    registry_rows = registry.rows()
    family_validation = validate_family_contract(registry)
    source_strategy_ids = list(registry.strategy_ids)
    page_ids, navigation_groups = _navigation_contract()
    deployment = _installed_deployment()
    legacy_paths = _legacy_ui_paths()
    latest_commit = _git("rev-parse", "HEAD")
    working_tree_changes = _working_tree_changes()
    source_working_tree_changes = _source_working_tree_changes()
    version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    account_ids = [
        f"{strategy_id}:{profile}"
        for strategy_id in source_strategy_ids
        for profile in ("BASE", "STRESS")
    ]
    expected_mode_counts = {
        mode: sum(row["mode"] == mode for row in registry_rows)
        for mode in ("ACTIVE", "SHADOW", "OFF")
    }
    universal_gate_hits = _universal_70_gate_source_hits()
    universal_gate = bool(universal_gate_hits)
    raw_win_rate_default_sort = _survivor_rank_uses_raw_win_rate()
    orderflow_filter = OrderflowConfirmationRuntime().status()
    api_transport_contract = _api_transport_source_contract()
    source_safety_contract = _source_safety_contract()
    opportunity_key_fields = [field.name for field in fields(OpportunityKey)]
    source_checks = {
        "exact_four_navigation_pages": page_ids == EXPECTED_PAGE_IDS,
        "legacy_ui_paths_zero": not legacy_paths,
        "universal_70_percent_gate_hits_zero": not universal_gate_hits,
        "raw_win_rate_default_sort_disabled": not raw_win_rate_default_sort,
        "family_count_eight": family_validation.get("family_count") == 8,
        "strategy_id_count_fifteen": len(source_strategy_ids) == 15,
        "account_count_thirty": len(account_ids) == 30,
        "source_active_count_zero": expected_mode_counts.get("ACTIVE") == 0,
        "opportunity_key_exact_six_fields": (
            opportunity_key_fields == EXPECTED_OPPORTUNITY_KEY_FIELDS
        ),
        "orderflow_affected_ids_exactly_two": (
            tuple(ORDERFLOW_AFFECTED_STRATEGY_IDS) == EXPECTED_ORDERFLOW_AFFECTED_STRATEGY_IDS
        ),
        "orderflow_filter_default_off": orderflow_filter.get("enabled") is False,
        "orderflow_filter_creates_no_candidate_plan": (
            orderflow_filter.get("creates_candidate_plan") is False
        ),
        "rest_paths_present": api_transport_contract["rest_paths_present"] is True,
        "websocket_path_present": api_transport_contract["websocket_path_present"] is True,
        "websocket_message_types_present": (
            api_transport_contract["websocket_message_types_present"] is True
        ),
        "websocket_client_selection_present": (
            api_transport_contract["client_selection_message_present"] is True
        ),
        "api_paper_safety_contract": source_safety_contract["status"] == "PASS",
    }
    source_contract = {
        "status": "PASS" if all(source_checks.values()) else "FAIL",
        "checks": source_checks,
        "failures": [name for name, passed in source_checks.items() if not passed],
    }
    api_transport_contract["safety"] = source_safety_contract
    api_transport_contract["status"] = (
        "PASS"
        if all(
            (
                api_transport_contract["rest_paths_present"],
                api_transport_contract["websocket_path_present"],
                api_transport_contract["websocket_message_types_present"],
                api_transport_contract["client_selection_message_present"],
                source_safety_contract["status"] == "PASS",
            )
        )
        else "FAIL"
    )
    live_runtime = _live_runtime_observation()
    runtime_evidence = _runtime_contract_evidence(
        live_runtime,
        latest_commit=latest_commit,
        expected_strategy_ids=source_strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=expected_mode_counts,
    )
    release_package_evidence = _release_package_evidence(
        deployment,
        latest_commit=latest_commit,
        working_tree_changes=working_tree_changes,
    )
    launch_agent_evidence = _launch_agent_evidence(
        deployment,
        runtime_observation=live_runtime,
    )
    evidence_details = {
        "v2_v3_comparison": _load_evidence(
            EVIDENCE_PATHS["v2_v3_comparison"],
            expected_schema_version=1,
            expected_source_commit=latest_commit,
            source_working_tree_changes=source_working_tree_changes,
        ),
        "dashboard_payload_benchmark": _load_evidence(
            EVIDENCE_PATHS["dashboard_payload_benchmark"],
            require_checks=True,
            evidence_kind="dashboard_payload_benchmark",
            expected_schema_version=2,
            expected_source_commit=latest_commit,
            source_working_tree_changes=source_working_tree_changes,
        ),
        "browser_e2e_after_latest_change": _load_evidence(
            EVIDENCE_PATHS["browser_e2e_after_latest_change"],
            require_checks=True,
            evidence_kind="browser_e2e_after_latest_change",
            expected_schema_version=1,
            expected_source_commit=latest_commit,
            source_working_tree_changes=source_working_tree_changes,
            require_release_binding=True,
        ),
        "full_suite_after_latest_change": _load_evidence(
            EVIDENCE_PATHS["full_suite_after_latest_change"],
            require_checks=True,
            evidence_kind="full_suite_after_latest_change",
            expected_schema_version=1,
            expected_source_commit=latest_commit,
            source_working_tree_changes=source_working_tree_changes,
        ),
        "thirty_minute_soak": _thirty_minute_soak_evidence(
            expected_strategy_ids=source_strategy_ids,
            expected_account_ids=account_ids,
            expected_mode_counts=expected_mode_counts,
            expected_source_commit=latest_commit,
            source_working_tree_changes=source_working_tree_changes,
        ),
        "release_package": release_package_evidence,
        "launch_agent": launch_agent_evidence,
        "installed_release": _installed_release_evidence(
            deployment,
            latest_commit=latest_commit,
            working_tree_changes=working_tree_changes,
            release_package_evidence=release_package_evidence,
            launch_agent_evidence=launch_agent_evidence,
        ),
        "remote_push": _remote_sync_evidence(
            latest_commit=latest_commit,
            working_tree_changes=working_tree_changes,
        ),
        "runtime": runtime_evidence,
    }
    for detail in evidence_details.values():
        detail.pop("payload", None)
    evidence_statuses = {name: str(detail["status"]) for name, detail in evidence_details.items()}
    runtime_available = live_runtime.get("available") is True
    runtime_report_fields = _runtime_report_fields(live_runtime)
    raw_past_runtime = evidence_details["thirty_minute_soak"].get("validated_runtime_observation")
    past_runtime = dict(raw_past_runtime) if isinstance(raw_past_runtime, dict) else None
    raw_past_mode_counts = (
        past_runtime.get("strategy_mode_counts") if past_runtime is not None else None
    )
    past_runtime_mode_counts = (
        dict(raw_past_mode_counts) if isinstance(raw_past_mode_counts, dict) else None
    )
    runtime_status = str(runtime_evidence["status"])
    report_status = _overall_report_status(
        source_status=str(source_contract["status"]),
        evidence_statuses=evidence_statuses,
        runtime_status=runtime_status,
    )
    unresolved: list[dict[str, Any]] = []
    if evidence_statuses["installed_release"] != "PASS":
        unresolved.append(
            {
                "id": "SOURCE_INSTALL_COMMIT_MISMATCH",
                "status": evidence_statuses["installed_release"],
                "detail": evidence_details["installed_release"],
            }
        )
    if evidence_statuses["launch_agent"] != "PASS":
        unresolved.append(
            {
                "id": "LAUNCH_AGENT_INSTALL_AND_PROCESS_BINDING",
                "status": evidence_statuses["launch_agent"],
                "detail": evidence_details["launch_agent"],
            }
        )
    unresolved.append(
        {
            "id": "V3_FIXED_INPUT_COMPARISON",
            "status": evidence_statuses["v2_v3_comparison"],
            "detail": evidence_details["v2_v3_comparison"],
        }
    )
    if not runtime_available:
        unresolved.append(
            {
                "id": "CURRENT_RUNTIME_DYNAMIC_STATE",
                "status": runtime_status,
                "detail": (
                    "서비스가 중지됐거나 도달할 수 없어 동적 상태를 실행 검증하지 않았습니다."
                ),
            }
        )
    elif runtime_status != "PASS":
        unresolved.append(
            {
                "id": "CURRENT_RUNTIME_SAFETY_CONTRACT",
                "status": runtime_status,
                "detail": runtime_evidence,
            }
        )
    if legacy_paths:
        unresolved.append(
            {
                "id": "OBSOLETE_UI_PATHS",
                "status": "OPEN",
                "detail": legacy_paths,
            }
        )
    if universal_gate:
        unresolved.append(
            {
                "id": "UNIVERSAL_70_PERCENT_GATE",
                "status": "OPEN",
                "detail": universal_gate_hits,
            }
        )
    if raw_win_rate_default_sort:
        unresolved.append(
            {
                "id": "RAW_WIN_RATE_DEFAULT_SORT",
                "status": "OPEN",
                "detail": "survivor 기본 순위가 raw win rate를 직접 사용합니다.",
            }
        )
    if source_contract["status"] != "PASS":
        unresolved.append(
            {
                "id": "V6_SOURCE_CONTRACT",
                "status": "FAIL",
                "detail": source_contract["failures"],
            }
        )
    return {
        "schema_version": 1,
        "generated_ts_utc": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "status": report_status,
        "baseline_commit_expected": BASELINE_COMMIT,
        "latest_git_commit": latest_commit,
        "git_commit_matches_requested_baseline": latest_commit == BASELINE_COMMIT,
        "installed_release_commit": deployment["release_commit"],
        "installed_release_state": deployment["status"],
        "installed_release_evidence_boundary": INSTALLED_RELEASE_EVIDENCE_BOUNDARY,
        "launch_agent_evidence_boundary": LAUNCH_AGENT_EVIDENCE_BOUNDARY,
        "report_provenance": {
            "head_scope": "TRACKED_HEAD_REFERENCE_AT_GENERATION_TIME",
            "working_tree_clean_excluding_this_report": not working_tree_changes,
            "working_tree_change_count_excluding_this_report": len(working_tree_changes),
            "generated_output_included_in_latest_commit": "NOT_CLAIMED",
            "self_referential_commit_match_claim": False,
        },
        "app_version": version,
        "strategy_implementation_revision": STRATEGY_VERSION,
        "source_strategy_ids": source_strategy_ids,
        "runtime_strategy_ids": runtime_report_fields["runtime_strategy_ids"],
        "runtime_strategy_ids_evidence": (
            "CURRENT_LOCALHOST_RUNTIME" if runtime_available else "NOT_PROVEN"
        ),
        "catalog_strategy_ids": source_strategy_ids,
        "frontend_fallback_strategy_ids": [],
        "source_strategy_count": len(source_strategy_ids),
        "runtime_strategy_count": runtime_report_fields["runtime_strategy_count"],
        "visible_strategy_count": sum(
            contract.user_visible_by_default for contract in STRATEGY_VARIANT_CONTRACTS.values()
        ),
        "account_count": len(account_ids),
        "account_ids": account_ids,
        "source_default_strategy_mode_counts": expected_mode_counts,
        "source_default_mode_counts_evidence": "SOURCE_REGISTRY",
        "source_default_active_count": expected_mode_counts["ACTIVE"],
        "source_default_shadow_count": expected_mode_counts["SHADOW"],
        "source_default_off_count": expected_mode_counts["OFF"],
        "current_active_count": runtime_report_fields["current_active_count"],
        "current_shadow_count": runtime_report_fields["current_shadow_count"],
        "current_off_count": runtime_report_fields["current_off_count"],
        "retired_count": sum(row["lifecycle"] == "RETIRED" for row in registry_rows),
        "research_off_count": sum(
            row["mode"] == "OFF" and row["lifecycle"] == "RESEARCH" for row in registry_rows
        ),
        "last_observed_runtime_mode_counts": past_runtime_mode_counts,
        "last_observed_runtime_mode_counts_evidence": (
            "VALIDATED_30_MINUTE_SOAK" if past_runtime is not None else "NOT_PROVEN"
        ),
        "current_runtime_mode_counts": runtime_report_fields["current_runtime_mode_counts"],
        "open_position_count": runtime_report_fields["open_position_count"],
        "total_open_position_count": runtime_report_fields["total_open_position_count"],
        "main_pending_entry_count": runtime_report_fields["main_pending_entry_count"],
        "league_pending_entry_count": runtime_report_fields["league_pending_entry_count"],
        "total_pending_entry_count": runtime_report_fields["total_pending_entry_count"],
        "paper_portfolio_flat": runtime_report_fields["paper_portfolio_flat"],
        "open_position_count_evidence": (runtime_report_fields["runtime_scalar_evidence"]),
        "page_ids": page_ids,
        "navigation_groups": navigation_groups,
        "duplicate_ui_metrics": {
            "obsolete_path_count": len(legacy_paths),
            "obsolete_paths": legacy_paths,
            "status": "RESOLVED" if not legacy_paths else "OPEN",
        },
        "duplicate_strategy_hypotheses": {
            "status": "MAPPED_TO_EIGHT_FAMILIES",
            "family_count": len(FAMILY_CATALOG),
            "variant_count": len(STRATEGY_VARIANT_CONTRACTS),
            "promotion_claim": "NOT_PROVEN",
        },
        "family_api_virtual_current_by_family": {
            "ORDERFLOW_CONFIRMATION": ORDERFLOW_CONFIRMATION_FILTER_ID,
        },
        "orderflow_confirmation_filter": {
            "filter_id": ORDERFLOW_CONFIRMATION_FILTER_ID,
            "family_id": orderflow_filter["family_id"],
            "role": orderflow_filter["role"],
            "registry_strategy": ORDERFLOW_CONFIRMATION_FILTER_ID in source_strategy_ids,
            "family_api_virtual_current": True,
            "default_research_enabled": orderflow_filter["enabled"],
            "affected_strategy_ids": list(ORDERFLOW_AFFECTED_STRATEGY_IDS),
            "creates_candidate_plan": orderflow_filter["creates_candidate_plan"],
            "registry_strategy_count_delta": 0,
            "account_count_delta": 0,
            "trade_count_delta": 0,
            "uplift_status": orderflow_filter["uplift_status"],
        },
        "universal_win_rate_gate_present": universal_gate,
        "universal_win_rate_gate_source_hits": universal_gate_hits,
        "raw_win_rate_default_sort": raw_win_rate_default_sort,
        "default_strategy_sort": "WILSON_95_LOWER_DESC",
        "base_stress_double_count_risk": {
            "status": "MITIGATED_IN_V6_GROUPED_API",
            "last_observed_raw_rows": (
                past_runtime.get("history_raw_rows") if past_runtime is not None else None
            ),
            "last_observed_unique_opportunities": (
                past_runtime.get("unique_opportunities") if past_runtime is not None else None
            ),
            "last_observation_evidence": (
                "VALIDATED_30_MINUTE_SOAK" if past_runtime is not None else "NOT_PROVEN"
            ),
            "rule": "BASE_STRESS_ARE_COST_RESULTS_OF_ONE_OPPORTUNITY",
        },
        "opportunity_aggregation_contract": {
            "key_fields": opportunity_key_fields,
            "partial_exits_create_new_opportunity": False,
            "account_group_fields": ["account_scope", "account_id"],
            "account_scopes": ["MAIN", "LEAGUE"],
            "profiles": ["BASE", "STRESS"],
            "unknown_legacy_linkage_status": "NOT_PROVEN",
        },
        "api_transport_contract": api_transport_contract,
        "source_contract": source_contract,
        "actual_orders_enabled": runtime_report_fields["actual_orders_enabled"],
        "auth_required": runtime_report_fields["auth_required"],
        "wallet_enabled": runtime_report_fields["wallet_enabled"],
        "wallet_runtime_evidence": runtime_report_fields["wallet_runtime_evidence"],
        "paper_only": runtime_report_fields["paper_only"],
        "private_api_enabled": runtime_report_fields["private_api_enabled"],
        "api_key_enabled": runtime_report_fields["api_key_enabled"],
        "runtime_ai_order_decision_enabled": runtime_report_fields[
            "runtime_ai_order_decision_enabled"
        ],
        "funding_readiness": runtime_report_fields["funding_readiness"],
        "runtime_scalar_evidence": runtime_report_fields["runtime_scalar_evidence"],
        "profitability": "NOT_PROVEN",
        "service_state": live_runtime["service_state"],
        "current_runtime": live_runtime,
        "last_observed_runtime": past_runtime,
        "last_observed_runtime_evidence": (
            "VALIDATED_30_MINUTE_SOAK" if past_runtime is not None else "NOT_PROVEN"
        ),
        "family_contract": family_validation,
        "unresolved_conflicts": unresolved,
        "evidence_classification": {
            "v2_v3_comparison": evidence_statuses["v2_v3_comparison"],
            "v3_profitability": "NOT_PROVEN",
            "conditions_telemetry_api_ui": evidence_statuses["full_suite_after_latest_change"],
            "dashboard_payload_benchmark": evidence_statuses["dashboard_payload_benchmark"],
            "browser_e2e_after_latest_change": evidence_statuses["browser_e2e_after_latest_change"],
            "full_suite_after_latest_change": evidence_statuses["full_suite_after_latest_change"],
            "thirty_minute_soak": evidence_statuses["thirty_minute_soak"],
            "six_hour_soak": "NOT_RUN",
            "twenty_four_hour_soak": "NOT_RUN",
            "release_package": evidence_statuses["release_package"],
            "launch_agent": evidence_statuses["launch_agent"],
            "installed_release": evidence_statuses["installed_release"],
            "remote_push": evidence_statuses["remote_push"],
            "runtime": runtime_status,
        },
        "evidence_details": evidence_details,
    }


def main() -> None:
    args = _parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(args.output),
                "source_strategy_count": report["source_strategy_count"],
                "family_count": report["family_contract"]["family_count"],
                "page_ids": report["page_ids"],
                "service_state": report["service_state"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
