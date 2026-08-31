# V6 소스·설치·마지막 PAPER 실행 기준선을 기계판독 증거로 고정한다.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.analytics.opportunities import OpportunityKey
from backend.app.build_identity import STRATEGY_VERSION
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "evidence/V6_CURRENT_SYSTEM_TRUTH.json"
RUNTIME_ROOT = Path("/Volumes/ROBOM_FLOWSCALPER/05_RUNTIME/ROBOM_FlowScalper")
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
    }
)
FULL_SUITE_COMMAND_NAMES = frozenset(
    {
        "backend_pytest",
        "backend_ruff",
        "backend_mypy",
        "frontend_tests",
        "frontend_lint",
        "frontend_typecheck",
        "frontend_build",
        "build_safety",
    }
)
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
    "frontend",
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
    main_pending_entry_count = _strict_non_negative_int(
        dashboard.get("main_pending_entry_count")
    )
    league_pending_entry_count = _strict_non_negative_int(
        dashboard.get("league_pending_entry_count")
    )
    total_pending_entry_count = _strict_non_negative_int(
        dashboard.get("total_pending_entry_count")
    )
    total_open_position_count = _strict_non_negative_int(
        dashboard.get("total_open_position_count")
    )
    paper_portfolio_flat = dashboard.get("paper_portfolio_flat")
    pending_scope_valid = (
        main_pending_entry_count is not None
        and league_pending_entry_count is not None
        and league_pending_entry_count == derived_league_pending_entries
        and total_pending_entry_count is not None
        and total_pending_entry_count
        == main_pending_entry_count + league_pending_entry_count
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
        mode: sum(
            row.get("mode") == mode
            for row in strategies
            if isinstance(row, dict)
        )
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
        "runtime_ai_order_decision_enabled": safety.get(
            "runtime_ai_order_decision_enabled"
        ),
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
    runtime_mode_counts = (
        dict(raw_mode_counts) if isinstance(raw_mode_counts, dict) else None
    )
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
            "CURRENT_SETTINGS_SUMMARY"
            if isinstance(wallet_value, bool)
            else "NOT_PROVEN"
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
        "runtime_mode_live_shadow_paper": (
            observation.get("runtime_mode") == "LIVE_SHADOW_PAPER"
        ),
        "market_data_live": observation.get("market_data_state") == "LIVE",
        "execution_paper": observation.get("execution_state") == "PAPER",
        "operation_manually_paused": (
            observation.get("operation_state") == "MANUALLY_PAUSED"
        ),
        "paper_entry_inactive": observation.get("paper_entry_active") is False,
        "pending_scope_valid": observation.get("pending_scope_valid") is True,
        "paper_portfolio_flat": observation.get("paper_portfolio_flat") is True,
        "main_pending_entries_zero": observation.get("main_pending_entry_count") == 0,
        "league_pending_entries_zero": (
            observation.get("league_pending_entry_count") == 0
        ),
        "total_pending_entries_zero": (
            observation.get("total_pending_entry_count") == 0
        ),
        "total_open_positions_zero": (
            observation.get("total_open_position_count") == 0
        ),
        "release_commit_matches_head": observation.get("release_commit") == latest_commit,
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
        normalized_settings = (
            dict(settings_safety) if isinstance(settings_safety, Mapping) else {}
        )
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
                checks[f"{case}_{surface_name}_{field}"] = (
                    payload.get(field) == expected_value
                )
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
    elif isinstance(command, list) and command and all(
        isinstance(part, str) and part.strip() for part in command
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


def _validate_dashboard_benchmark(
    payload: Mapping[str, object],
    checks: Mapping[str, object],
) -> tuple[str, str]:
    if set(checks) != BENCHMARK_REQUIRED_CHECKS:
        return "NOT_PROVEN", "BENCHMARK_REQUIRED_CHECK_SET_MISMATCH"
    if not _command_succeeded(payload, token="benchmark_dashboard_payload.py"):
        return "NOT_PROVEN", "BENCHMARK_COMMAND_OR_EXIT_CODE_NOT_PROVEN"
    artifact_failure = _validate_artifacts(payload, required_kinds=frozenset({"log"}))
    if artifact_failure is not None:
        return artifact_failure
    if _strict_non_negative_int(payload.get("fixture_events")) in {None, 0}:
        return "NOT_PROVEN", "BENCHMARK_FIXTURE_EVENT_COUNT_NOT_POSITIVE"

    raw_metrics = payload.get("payload")
    raw_delta = payload.get("websocket_chart_delta")
    raw_latency = payload.get("transform_latency")
    if (
        not isinstance(raw_metrics, dict)
        or not isinstance(raw_delta, dict)
        or not isinstance(raw_latency, dict)
    ):
        return "NOT_PROVEN", "BENCHMARK_REQUIRED_MEASUREMENT_OBJECT_MISSING"
    dashboard_bytes = _positive_number(raw_metrics.get("dashboard_payload_bytes"))
    summary_bytes = _positive_number(raw_metrics.get("summary_payload_bytes"))
    strategy_bytes = _positive_number(raw_metrics.get("strategy_summary_payload_bytes"))
    stored_summary_ratio = _positive_number(raw_metrics.get("summary_to_dashboard_ratio"))
    stored_strategy_ratio = _positive_number(raw_metrics.get("strategy_summary_to_dashboard_ratio"))
    delta_bytes = _positive_number(raw_delta.get("delta_envelope_bytes"))
    full_chart_bytes = _positive_number(raw_delta.get("full_chart_bytes"))
    stored_delta_ratio = _positive_number(raw_delta.get("delta_to_full_chart_ratio"))
    measurements = (
        dashboard_bytes,
        summary_bytes,
        strategy_bytes,
        stored_summary_ratio,
        stored_strategy_ratio,
        delta_bytes,
        full_chart_bytes,
        stored_delta_ratio,
    )
    if any(value is None for value in measurements):
        return "NOT_PROVEN", "BENCHMARK_BYTES_OR_RATIO_NOT_POSITIVE"
    assert dashboard_bytes is not None
    assert summary_bytes is not None
    assert strategy_bytes is not None
    assert stored_summary_ratio is not None
    assert stored_strategy_ratio is not None
    assert delta_bytes is not None
    assert full_chart_bytes is not None
    assert stored_delta_ratio is not None
    summary_ratio = summary_bytes / dashboard_bytes
    strategy_ratio = strategy_bytes / dashboard_bytes
    delta_ratio = delta_bytes / full_chart_bytes
    target_summary = raw_metrics.get("target_summary_ratio_strictly_less_than")
    target_strategy = raw_metrics.get("target_strategy_ratio_strictly_less_than")
    calculations_valid = (
        target_summary == 0.50
        and target_strategy == 0.35
        and summary_ratio < 0.50
        and strategy_ratio < 0.35
        and delta_ratio < 1.0
        and math.isclose(stored_summary_ratio, summary_ratio, abs_tol=0.000001)
        and math.isclose(stored_strategy_ratio, strategy_ratio, abs_tol=0.000001)
        and math.isclose(stored_delta_ratio, delta_ratio, abs_tol=0.000001)
        and raw_delta.get("message_type") == "chart_delta"
        and raw_delta.get("refresh_required") is False
        and raw_delta.get("point_upserts") == 1
        and isinstance(raw_delta.get("candle_upserts"), int)
        and not isinstance(raw_delta.get("candle_upserts"), bool)
        and 0 <= raw_delta["candle_upserts"] <= 1
        and payload.get("paper_only") is True
        and payload.get("real_orders_enabled") is False
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
    for measurement in raw_latency.values():
        if (
            not isinstance(measurement, dict)
            or _strict_non_negative_int(measurement.get("iterations")) in {None, 0}
            or _positive_number(measurement.get("p95_ms")) is None
        ):
            return "NOT_PROVEN", "BENCHMARK_LATENCY_COUNT_OR_VALUE_NOT_POSITIVE"
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
        required_kinds=frozenset({"log", "screenshot"}),
    )
    if artifact_failure is not None:
        return artifact_failure
    counts = payload.get("counts")
    artifacts = payload.get("artifacts")
    if not isinstance(counts, dict) or not isinstance(artifacts, list):
        return "NOT_PROVEN", "BROWSER_COUNTS_OR_ARTIFACTS_MISSING"
    if set(counts) != {
        "page_count",
        "project_count",
        "test_count",
        "screenshot_count",
        "console_error_count",
    }:
        return "NOT_PROVEN", "BROWSER_COUNT_SCOPE_MISMATCH"
    screenshot_count = sum(
        isinstance(record, dict) and record.get("kind") == "screenshot"
        for record in artifacts
    )
    if (
        counts.get("page_count") != len(EXPECTED_PAGE_IDS)
        or counts.get("project_count") != 3
        or _strict_non_negative_int(counts.get("test_count")) in {None, 0}
        or counts.get("screenshot_count") != screenshot_count
        or screenshot_count < 3
        or counts.get("console_error_count") != 0
        or payload.get("page_ids") != EXPECTED_PAGE_IDS
        or payload.get("projects") != ["desktop", "tablet", "mobile"]
        or payload.get("runtime_url") != "http://127.0.0.1:8870"
    ):
        return "NOT_PROVEN", "BROWSER_POSITIVE_COUNTS_OR_EXACT_SCOPE_MISMATCH"
    return "PASS", "BROWSER_REQUIRED_CHECKS_COMMAND_COUNTS_AND_ARTIFACTS_PASS"


def _validate_full_suite(
    payload: Mapping[str, object],
    checks: Mapping[str, object],
) -> tuple[str, str]:
    if set(checks) != FULL_SUITE_REQUIRED_CHECKS:
        return "NOT_PROVEN", "FULL_SUITE_REQUIRED_CHECK_SET_MISMATCH"
    commands = payload.get("commands")
    counts = payload.get("counts")
    if not isinstance(commands, list) or not isinstance(counts, dict):
        return "NOT_PROVEN", "FULL_SUITE_COMMANDS_OR_COUNTS_MISSING"
    if set(counts) != {"command_count", "backend_test_count", "frontend_test_count"}:
        return "NOT_PROVEN", "FULL_SUITE_COUNT_SCOPE_MISMATCH"
    if (
        counts.get("command_count") != len(FULL_SUITE_COMMAND_NAMES)
        or len(commands) != len(FULL_SUITE_COMMAND_NAMES)
        or _strict_non_negative_int(counts.get("backend_test_count")) in {None, 0}
        or _strict_non_negative_int(counts.get("frontend_test_count")) in {None, 0}
    ):
        return "NOT_PROVEN", "FULL_SUITE_POSITIVE_COUNTS_NOT_PROVEN"
    command_names: set[str] = set()
    log_paths: set[str] = set()
    for command_record in commands:
        if not isinstance(command_record, dict):
            return "NOT_PROVEN", "FULL_SUITE_COMMAND_RECORD_INVALID"
        name = command_record.get("name")
        command = command_record.get("command")
        exit_code = command_record.get("exit_code")
        log_path = command_record.get("log_path")
        if (
            not isinstance(name, str)
            or name not in FULL_SUITE_COMMAND_NAMES
            or name in command_names
            or not (
                isinstance(command, str)
                and command.strip()
                or isinstance(command, list)
                and command
                and all(isinstance(part, str) and part.strip() for part in command)
            )
            or not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or exit_code != 0
            or not isinstance(log_path, str)
            or not log_path.strip()
            or log_path in log_paths
        ):
            return "NOT_PROVEN", "FULL_SUITE_COMMAND_NAME_EXIT_OR_LOG_INVALID"
        command_names.add(name)
        log_paths.add(log_path)
    if command_names != FULL_SUITE_COMMAND_NAMES:
        return "NOT_PROVEN", "FULL_SUITE_REQUIRED_COMMAND_MISSING"
    artifact_failure = _validate_artifacts(payload, required_kinds=frozenset({"log"}))
    if artifact_failure is not None:
        return artifact_failure
    artifacts = payload.get("artifacts")
    assert isinstance(artifacts, list)
    artifact_log_paths = {
        str(record["path"])
        for record in artifacts
        if isinstance(record, dict) and record.get("kind") == "log"
    }
    if log_paths != artifact_log_paths:
        return "NOT_PROVEN", "FULL_SUITE_COMMAND_LOG_ARTIFACT_SCOPE_MISMATCH"
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
        if not SOAK_REQUIRED_CHECKS.issubset(checks):
            return "NOT_PROVEN", "SOAK_REQUIRED_CHECK_SET_MISMATCH"
        return "PASS", "SOAK_REQUIRED_CHECK_SET_PRESENT"
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
    unique_opportunities = _strict_non_negative_int(
        payload.get("unique_opportunities")
    )
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
        "open_positions": _strict_non_negative_int(final.get("position_count")),
        "main_pending_entry_count": _strict_non_negative_int(
            final.get("main_pending_entry_count")
        ),
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
    payload = evidence.pop("payload")
    if not isinstance(payload, dict):
        evidence["status"] = "FAIL"
        evidence["reason"] = "EVIDENCE_ROOT_NOT_OBJECT"
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
                _soak_runtime_binding_failure(payload)
                or "SOAK_RUNTIME_PROVENANCE_BINDING_MISSING"
            )
        else:
            evidence["reason"] = "V6_30_MINUTE_SCOPE_AND_SAFETY_PASS"
            evidence["validated_runtime_observation"] = validated_observation
    return evidence


def _installed_release_evidence(
    deployment: dict[str, Any],
    *,
    latest_commit: str,
    working_tree_changes: list[str],
    release_package_evidence: dict[str, Any],
) -> dict[str, Any]:
    status = _normalize_evidence_status(deployment.get("status"))
    reason = "CURRENT_DEPLOYMENT_DECLARATION"
    if status != "PASS":
        return {"status": status, "reason": reason}
    if (
        deployment.get("paper_only") is not True
        or deployment.get("real_orders_enabled") is not False
        or deployment.get("auth_required") is not False
    ):
        return {"status": "FAIL", "reason": "INSTALLED_PAPER_SAFETY_CONTRACT_FAILED"}
    if release_package_evidence["status"] != "PASS":
        return {
            "status": release_package_evidence["status"],
            "reason": f"INSTALLED_{release_package_evidence['reason']}",
        }
    if deployment.get("release_commit") != latest_commit:
        return {"status": "NOT_PROVEN", "reason": "INSTALLED_COMMIT_DIFFERS_FROM_HEAD"}
    if working_tree_changes:
        return {
            "status": "NOT_PROVEN",
            "reason": "WORKING_TREE_DIFFERS_FROM_INSTALLED_HEAD",
        }
    return {"status": "PASS", "reason": "INSTALLED_SAFE_COMMIT_MATCHES_CLEAN_HEAD"}


def _release_package_evidence(
    deployment: dict[str, Any],
    *,
    latest_commit: str,
    working_tree_changes: list[str],
) -> dict[str, Any]:
    release_commit = deployment.get("release_commit")
    if not isinstance(release_commit, str) or not release_commit:
        return {"status": "NOT_RUN", "reason": "INSTALLED_RELEASE_COMMIT_NOT_FOUND"}
    relative_path = f"releases/{release_commit}/release-manifest.json"
    manifest_path = RUNTIME_ROOT / relative_path
    if not manifest_path.is_file():
        return {
            "status": "NOT_RUN",
            "path": relative_path,
            "reason": "RELEASE_MANIFEST_NOT_FOUND",
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
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
    if manifest.get("schema_version") != 2 or manifest_commit != release_commit:
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
    except RuntimeError:
        return {
            "status": "FAIL",
            "path": relative_path,
            "reason": "RELEASE_TREE_FULL_HASH_VERIFICATION_FAILED",
        }
    if release_commit != latest_commit or working_tree_changes:
        return {
            "status": "NOT_PROVEN",
            "path": relative_path,
            "reason": "RELEASE_PACKAGE_DIFFERS_FROM_CURRENT_SOURCE",
        }
    return {
        "status": "PASS",
        "path": relative_path,
        "reason": "V2_SAFE_RELEASE_TREE_HASHES_MATCH_CLEAN_HEAD",
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
            tuple(ORDERFLOW_AFFECTED_STRATEGY_IDS)
            == EXPECTED_ORDERFLOW_AFFECTED_STRATEGY_IDS
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
        "installed_release": _installed_release_evidence(
            deployment,
            latest_commit=latest_commit,
            working_tree_changes=working_tree_changes,
            release_package_evidence=release_package_evidence,
        ),
        "remote_push": _remote_sync_evidence(
            latest_commit=latest_commit,
            working_tree_changes=working_tree_changes,
        ),
        "runtime": runtime_evidence,
    }
    for detail in evidence_details.values():
        detail.pop("payload", None)
    evidence_statuses = {
        name: str(detail["status"])
        for name, detail in evidence_details.items()
    }
    runtime_available = live_runtime.get("available") is True
    runtime_report_fields = _runtime_report_fields(live_runtime)
    raw_past_runtime = evidence_details["thirty_minute_soak"].get(
        "validated_runtime_observation"
    )
    past_runtime = (
        dict(raw_past_runtime) if isinstance(raw_past_runtime, dict) else None
    )
    raw_past_mode_counts = (
        past_runtime.get("strategy_mode_counts")
        if past_runtime is not None
        else None
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
        unresolved.append({
            "id": "SOURCE_INSTALL_COMMIT_MISMATCH",
            "status": evidence_statuses["installed_release"],
            "detail": evidence_details["installed_release"],
        })
    unresolved.append(
        {
            "id": "V3_FIXED_INPUT_COMPARISON",
            "status": evidence_statuses["v2_v3_comparison"],
            "detail": evidence_details["v2_v3_comparison"],
        }
    )
    if not runtime_available:
        unresolved.append({
            "id": "CURRENT_RUNTIME_DYNAMIC_STATE",
            "status": runtime_status,
            "detail": "서비스가 중지됐거나 도달할 수 없어 동적 상태를 실행 검증하지 않았습니다.",
        })
    elif runtime_status != "PASS":
        unresolved.append({
            "id": "CURRENT_RUNTIME_SAFETY_CONTRACT",
            "status": runtime_status,
            "detail": runtime_evidence,
        })
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
            "CURRENT_LOCALHOST_RUNTIME"
            if runtime_available
            else "NOT_PROVEN"
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
            row["mode"] == "OFF" and row["lifecycle"] == "RESEARCH"
            for row in registry_rows
        ),
        "last_observed_runtime_mode_counts": past_runtime_mode_counts,
        "last_observed_runtime_mode_counts_evidence": (
            "VALIDATED_30_MINUTE_SOAK" if past_runtime is not None else "NOT_PROVEN"
        ),
        "current_runtime_mode_counts": runtime_report_fields[
            "current_runtime_mode_counts"
        ],
        "open_position_count": runtime_report_fields["open_position_count"],
        "total_open_position_count": runtime_report_fields[
            "total_open_position_count"
        ],
        "main_pending_entry_count": runtime_report_fields[
            "main_pending_entry_count"
        ],
        "league_pending_entry_count": runtime_report_fields[
            "league_pending_entry_count"
        ],
        "total_pending_entry_count": runtime_report_fields[
            "total_pending_entry_count"
        ],
        "paper_portfolio_flat": runtime_report_fields["paper_portfolio_flat"],
        "open_position_count_evidence": (
            runtime_report_fields["runtime_scalar_evidence"]
        ),
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
                past_runtime.get("history_raw_rows")
                if past_runtime is not None
                else None
            ),
            "last_observed_unique_opportunities": (
                past_runtime.get("unique_opportunities")
                if past_runtime is not None
                else None
            ),
            "last_observation_evidence": (
                "VALIDATED_30_MINUTE_SOAK"
                if past_runtime is not None
                else "NOT_PROVEN"
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
            "conditions_telemetry_api_ui": evidence_statuses[
                "full_suite_after_latest_change"
            ],
            "dashboard_payload_benchmark": evidence_statuses[
                "dashboard_payload_benchmark"
            ],
            "browser_e2e_after_latest_change": evidence_statuses[
                "browser_e2e_after_latest_change"
            ],
            "full_suite_after_latest_change": evidence_statuses[
                "full_suite_after_latest_change"
            ],
            "thirty_minute_soak": evidence_statuses["thirty_minute_soak"],
            "six_hour_soak": "NOT_RUN",
            "twenty_four_hour_soak": "NOT_RUN",
            "release_package": evidence_statuses["release_package"],
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
