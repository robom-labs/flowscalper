# 검증된 V1 호환 PAPER 서비스의 누락 안전 필드를 dashboard와 읽기 전용 원장으로 동등 증명한다.
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import subprocess
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast
from urllib.parse import quote

from scripts.stage_macos_release import _verified_legacy_release_tree, _verify_release_tree

SUPPORTED_LEGACY_RUNTIME_COMMITS = frozenset({"50c3e8ae7af08667546e8a1f2e4a70890e92d0f6"})
_LEGACY_MANIFEST_SHA256_BY_COMMIT = {
    "50c3e8ae7af08667546e8a1f2e4a70890e92d0f6": (
        "21bd37ece3cd9bf72317c6fb878bef2a93d3a4c15d85ebacadded2c0c235a73e"
    )
}
_SHA256_LENGTH = 64
_LAUNCH_AGENT_LABEL = "kr.robom.flowscalper"
_LAUNCHCTL_PID = re.compile(r"^\s*pid = ([1-9][0-9]*)\s*$", re.MULTILINE)
_RECOVERY_STATE_EVENTS = frozenset(
    {
        "MAIN_CANDIDATE_SELECTED",
        "LEAGUE_CANDIDATE_ARMED",
        "MAIN_MANUAL_EXIT_PENDING",
        "MANAGEMENT_EXIT_ARMED",
        "ENTRY_EXPIRED",
        "ENTRY_REJECTED",
        "ENTRY_UNFILLED",
        "ENTRY_FILLED",
        "FORCED_EXIT_PENDING",
        "STOP_EXIT_PENDING",
        "TAKE_PROFIT_EXIT_PENDING",
        "TRAILING_STATE_TRANSITION",
        "TRAILING_MARK_UPDATED",
        "TRAILING_EDGE_STATE_UPDATED",
        "TRAIL_EXIT_PENDING",
        "EXIT_REJECTED",
        "EXIT_UNFILLED",
        "EXIT_FILL",
    }
)
_EXPECTED_TABLE_SCHEMA = {
    "runs": (
        ("run_id", "TEXT", 0, 1),
        ("mode", "TEXT", 1, 0),
        ("venue", "TEXT", 1, 0),
        ("config_hash", "TEXT", 1, 0),
        ("config_json", "TEXT", 1, 0),
        ("started_ts_ms", "INTEGER", 1, 0),
        ("finalized_ts_ms", "INTEGER", 0, 0),
        ("summary_json", "TEXT", 0, 0),
    ),
    "app_settings": (
        ("setting_key", "TEXT", 0, 1),
        ("value_json", "TEXT", 1, 0),
        ("updated_ts_ms", "INTEGER", 1, 0),
    ),
    "snapshots": (
        ("snapshot_id", "INTEGER", 0, 1),
        ("run_id", "TEXT", 1, 0),
        ("lifecycle_state", "TEXT", 1, 0),
        ("ts_ms", "INTEGER", 1, 0),
        ("payload_json", "TEXT", 1, 0),
        ("checksum", "TEXT", 1, 0),
    ),
    "execution_audit": (
        ("audit_id", "INTEGER", 0, 1),
        ("run_id", "TEXT", 1, 0),
        ("ts_ms", "INTEGER", 1, 0),
        ("event_type", "TEXT", 1, 0),
        ("payload_json", "TEXT", 1, 0),
        ("checksum", "TEXT", 1, 0),
    ),
}


class LegacyRuntimePreflightError(RuntimeError):
    """V1 런타임의 동등 안전 증명을 만들 수 없을 때 설치를 차단한다."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LegacyRuntimePreflightError(message)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    _require(isinstance(value, Mapping), f"{label}가 JSON object가 아닙니다.")
    return cast(Mapping[str, object], value)


def _list(value: object, label: str) -> list[object]:
    _require(isinstance(value, list), f"{label}가 JSON list가 아닙니다.")
    return cast(list[object], value)


def _exact_non_negative_int(value: object, label: str) -> int:
    _require(type(value) is int and value >= 0, f"{label}가 0 이상의 int가 아닙니다.")
    return cast(int, value)


def _finite_number(value: object, label: str) -> float:
    _require(type(value) in (int, float), f"{label}가 숫자가 아닙니다.")
    number = cast(int | float, value)
    _require(
        math.isfinite(float(number)),
        f"{label}가 유한한 숫자가 아닙니다.",
    )
    return float(number)


def _zero_decimal(value: object, label: str) -> None:
    _require(type(value) in (int, float, str), f"{label}가 숫자 문자열이 아닙니다.")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise LegacyRuntimePreflightError(f"{label}가 Decimal이 아닙니다.") from error
    _require(number.is_finite() and number == 0, f"{label}가 유한한 0이 아닙니다.")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _strict_json_loads(raw: str, label: str) -> object:
    def reject_constant(value: str) -> object:
        raise LegacyRuntimePreflightError(f"{label}에 비표준 숫자가 있습니다: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise LegacyRuntimePreflightError(f"{label}에 중복 JSON key가 있습니다: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except json.JSONDecodeError as error:
        raise LegacyRuntimePreflightError(f"{label} JSON을 해석할 수 없습니다.") from error


def _decode_canonical_json(raw: object, label: str) -> Mapping[str, object]:
    _require(isinstance(raw, str), f"{label} 원문이 문자열이 아닙니다.")
    canonical_raw = cast(str, raw)
    decoded = _strict_json_loads(canonical_raw, label)
    payload = _mapping(decoded, label)
    _require(_canonical_json(payload) == canonical_raw, f"{label}가 canonical JSON이 아닙니다.")
    return payload


def _require_sha256(value: object, label: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value),
        f"{label}가 소문자 SHA-256이 아닙니다.",
    )
    return cast(str, value)


def _require_stable_process_binding(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> None:
    for field in (
        "service_pid",
        "ledger_device",
        "ledger_inode",
        "cwd_device",
        "cwd_inode",
        "open_database_paths",
    ):
        _require(
            before.get(field) == after.get(field),
            f"legacy process binding {field}가 검증 중 바뀌었습니다.",
        )


def _verify_manifest(
    manifest: Mapping[str, object],
    *,
    runtime_root: Path,
) -> tuple[str, int, Path, Path]:
    schema_version = manifest.get("schema_version")
    _require(
        type(schema_version) is int and schema_version in {1, 2},
        "legacy manifest schema가 1 또는 검증 승격된 2가 아닙니다.",
    )
    commit = manifest.get("commit")
    _require(
        isinstance(commit, str) and commit in SUPPORTED_LEGACY_RUNTIME_COMMITS,
        "원장 동등 증명이 허용된 legacy commit이 아닙니다.",
    )
    _require(manifest.get("release_id") == commit, "legacy release_id와 commit이 다릅니다.")
    release_path_value = manifest.get("release_path")
    _require(
        isinstance(release_path_value, str) and bool(release_path_value),
        "legacy manifest release_path가 없습니다.",
    )
    release_path = Path(cast(str, release_path_value))
    _require(not release_path.is_symlink(), "legacy release root symlink는 허용하지 않습니다.")
    try:
        resolved_release_path = release_path.resolve(strict=True)
    except OSError as error:
        raise LegacyRuntimePreflightError("legacy release_path를 확인할 수 없습니다.") from error
    _require(
        resolved_release_path == release_path.absolute(),
        "legacy release_path의 canonical 경로가 다릅니다.",
    )
    _require(resolved_release_path.name == commit, "legacy release 디렉터리와 commit이 다릅니다.")
    for field, expected in (
        ("paper_only", True),
        ("real_orders_enabled", False),
        ("auth_required", False),
        ("private_api_enabled", False),
        ("wallet_paths_enabled", False),
    ):
        _require(manifest.get(field) is expected, f"legacy manifest {field}가 안전값이 아닙니다.")
    _require(not runtime_root.is_symlink(), "legacy runtime root symlink는 허용하지 않습니다.")
    try:
        resolved_runtime_root = runtime_root.resolve(strict=True)
    except OSError as error:
        raise LegacyRuntimePreflightError("legacy runtime root를 확인할 수 없습니다.") from error
    _require(
        resolved_runtime_root == runtime_root.absolute(),
        "legacy runtime root의 canonical 경로가 다릅니다.",
    )
    _require(
        resolved_release_path.parent == resolved_runtime_root / "releases",
        "legacy release가 runtime releases의 direct child가 아닙니다.",
    )
    current_pointer = resolved_runtime_root / "current"
    _require(current_pointer.is_symlink(), "legacy runtime current 포인터가 symlink가 아닙니다.")
    try:
        current_release = current_pointer.resolve(strict=True)
    except OSError as error:
        raise LegacyRuntimePreflightError("legacy current 포인터를 확인할 수 없습니다.") from error
    _require(
        current_release == resolved_release_path,
        "legacy current 포인터가 실행 릴리스와 다릅니다.",
    )
    active_ledger_dir = resolved_runtime_root / "active-ledger"
    _require(
        not active_ledger_dir.is_symlink(), "legacy active-ledger symlink는 허용하지 않습니다."
    )
    try:
        resolved_active_ledger_dir = active_ledger_dir.resolve(strict=True)
    except OSError as error:
        raise LegacyRuntimePreflightError(
            "legacy active-ledger 경로를 확인할 수 없습니다."
        ) from error
    _require(
        resolved_active_ledger_dir == active_ledger_dir,
        "legacy active-ledger canonical 경로가 다릅니다.",
    )
    _require(
        manifest.get("active_ledger_dir") == str(resolved_active_ledger_dir),
        "legacy manifest active_ledger_dir가 실제 런타임과 다릅니다.",
    )
    if schema_version == 2:
        _require(
            manifest.get("legacy_schema_version") == 1,
            "schema v2 manifest가 검증 승격된 V1 릴리스가 아닙니다.",
        )
        _require(
            manifest.get("legacy_source_commit_verified") is True,
            "legacy source commit 검증 표지가 없습니다.",
        )
        _require(
            manifest.get("legacy_frontend_manifest_verified") is True,
            "legacy frontend 검증 표지가 없습니다.",
        )
        original_manifest_sha = _require_sha256(
            manifest.get("legacy_manifest_sha256"),
            "legacy 원본 manifest checksum",
        )
        _require(
            original_manifest_sha == _LEGACY_MANIFEST_SHA256_BY_COMMIT[cast(str, commit)],
            "legacy 원본 manifest checksum이 allowlist와 다릅니다.",
        )
        _require(
            _verify_release_tree(resolved_release_path) == dict(manifest),
            "검증 승격된 legacy v2 release tree가 manifest와 다릅니다.",
        )
    else:
        manifest_path = resolved_release_path / "release-manifest.json"
        _require(
            not manifest_path.is_symlink() and manifest_path.is_file(),
            "legacy v1 manifest가 regular file이 아닙니다.",
        )
        _require(
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            == _LEGACY_MANIFEST_SHA256_BY_COMMIT[cast(str, commit)],
            "legacy v1 manifest bytes가 allowlist와 다릅니다.",
        )
        _verified_legacy_release_tree(
            runtime_root=resolved_runtime_root,
            release_path=resolved_release_path,
            manifest=manifest,
            commit=cast(str, commit),
        )
    return (
        cast(str, commit),
        cast(int, schema_version),
        resolved_active_ledger_dir,
        resolved_release_path,
    )


def _dashboard_account_pairs(payload: Mapping[str, object]) -> set[tuple[str, str]]:
    pair_sets: list[set[tuple[str, str]]] = []
    for field in ("shadow_accounts", "league_accounts"):
        rows = _list(payload.get(field), f"dashboard {field}")
        _require(bool(rows), f"dashboard {field}가 비어 있습니다.")
        pairs: set[tuple[str, str]] = set()
        for index, value in enumerate(rows):
            row = _mapping(value, f"dashboard {field}[{index}]")
            strategy_id = row.get("strategy_id")
            profile = row.get("profile")
            _require(
                isinstance(strategy_id, str) and bool(strategy_id),
                f"dashboard {field}[{index}] strategy_id가 없습니다.",
            )
            _require(
                profile in {"BASE", "STRESS"}, f"dashboard {field}[{index}] profile이 잘못됐습니다."
            )
            pair = (cast(str, strategy_id), str(profile))
            _require(pair not in pairs, f"dashboard {field} 계좌가 중복됩니다: {pair}")
            pairs.add(pair)
            _require(
                _exact_non_negative_int(
                    row.get("pending_entries"),
                    f"dashboard {field}[{index}].pending_entries",
                )
                == 0,
                f"dashboard {field}[{index}]에 pending entry가 남아 있습니다.",
            )
            _require(
                _exact_non_negative_int(
                    row.get("open_positions"),
                    f"dashboard {field}[{index}].open_positions",
                )
                == 0,
                f"dashboard {field}[{index}]에 open position이 남아 있습니다.",
            )
        pair_sets.append(pairs)
    _require(
        pair_sets[0] == pair_sets[1],
        "dashboard shadow 계좌와 league 계좌 집합이 다릅니다.",
    )
    return pair_sets[0]


def _verify_dashboard(
    payload: Mapping[str, object],
    *,
    commit: str,
    expected_run_id: str | None,
    expected_pause_revision: int | None,
) -> tuple[str, int, int, set[tuple[str, str]]]:
    status = _mapping(payload.get("status"), "dashboard status")
    system = _mapping(payload.get("system"), "dashboard system")
    risk = _mapping(payload.get("risk"), "dashboard risk")
    operation = _mapping(payload.get("operation_status"), "dashboard operation_status")
    intent = _mapping(payload.get("paper_entry_intent"), "dashboard paper_entry_intent")

    run_id = status.get("run_id")
    _require(
        isinstance(run_id, str) and bool(run_id) and "\t" not in run_id,
        "dashboard run_id가 올바르지 않습니다.",
    )
    if expected_run_id is not None:
        _require(run_id == expected_run_id, "dashboard run_id가 preflight와 달라졌습니다.")
    revision = _exact_non_negative_int(intent.get("revision"), "dashboard pause revision")
    if expected_pause_revision is not None:
        _require(
            revision == expected_pause_revision,
            "dashboard pause revision이 preflight와 달라졌습니다.",
        )
    intent_updated_ts_ms = _exact_non_negative_int(
        intent.get("updated_ts_ms"), "dashboard pause updated_ts_ms"
    )
    for field, maximum in (
        ("lag_p95_ms", 500.0),
        ("trade_lag_p95_ms", 1000.0),
        ("persistence_flush_last_ms", 20000.0),
    ):
        value = _finite_number(system.get(field), f"dashboard {field}")
        _require(0 <= value <= maximum, f"dashboard {field}가 0..{maximum} 범위를 벗어났습니다.")
    persistence_flush_count = _exact_non_negative_int(
        system.get("persistence_flush_count"),
        "dashboard persistence_flush_count",
    )
    _require(
        persistence_flush_count >= 4,
        "dashboard persistence_flush_count가 4보다 작습니다.",
    )
    persistence_flush_last_completed_ts_ms = _exact_non_negative_int(
        system.get("persistence_flush_last_completed_ts_ms"),
        "dashboard persistence_flush_last_completed_ts_ms",
    )
    persistence_fault_count = _exact_non_negative_int(
        system.get("persistence_fault_count"),
        "dashboard persistence_fault_count",
    )
    persistence_recovery_count = _exact_non_negative_int(
        system.get("persistence_recovery_count"),
        "dashboard persistence_recovery_count",
    )
    _exact_non_negative_int(
        system.get("persistence_buffer_dropped"),
        "dashboard persistence_buffer_dropped",
    )
    _require(
        system.get("persistence_fault_active") is False,
        "dashboard persistence fault가 현재 활성 상태입니다.",
    )
    _require(
        system.get("persistence_fault_recoverable") is False,
        "dashboard persistence fault recovery가 아직 진행 상태입니다.",
    )
    _require(
        persistence_recovery_count == persistence_fault_count,
        "dashboard persistence fault와 recovery 누적 횟수가 다릅니다.",
    )
    _require(
        system.get("persistence_last_error") == "NONE",
        "dashboard persistence에 현재 error가 남아 있습니다.",
    )
    persistence_last_recovered_ts_ms = system.get("persistence_last_recovered_ts_ms")
    if persistence_recovery_count == 0:
        _require(
            persistence_last_recovered_ts_ms is None,
            "dashboard persistence recovery 이력 없이 복구 시각이 보고됐습니다.",
        )
    else:
        recovered_ts_ms = _exact_non_negative_int(
            persistence_last_recovered_ts_ms,
            "dashboard persistence_last_recovered_ts_ms",
        )
        _require(
            recovered_ts_ms <= persistence_flush_last_completed_ts_ms,
            "dashboard persistence 복구 뒤 성공 flush가 확인되지 않았습니다.",
        )
    _require(
        system.get("persistence_worker_warmed") is True,
        "legacy persistence worker가 warmed 상태가 아닙니다.",
    )
    _require(
        system.get("storage_entry_allowed") is True, "legacy storage entry가 허용 상태가 아닙니다."
    )

    _require(status.get("market_data_state") == "LIVE", "legacy market data가 LIVE가 아닙니다.")
    _require(status.get("execution_state") == "PAPER", "legacy execution이 PAPER가 아닙니다.")
    _require(status.get("real_orders_enabled") is False, "legacy real order가 비활성이 아닙니다.")
    _require(status.get("auth_required") is False, "legacy auth가 비활성이 아닙니다.")
    _require(risk.get("paper_only") is True, "legacy PAPER only가 명시적 True가 아닙니다.")
    _require(system.get("release_commit") == commit, "legacy dashboard release commit이 다릅니다.")
    _require(system.get("release_isolated") is True, "legacy release_isolated가 True가 아닙니다.")
    _require(system.get("auth_headers") is False, "legacy auth header가 비활성이 아닙니다.")
    for field in (
        "private_api_enabled",
        "api_key_enabled",
        "wallet_enabled",
        "runtime_ai_order_decision_enabled",
    ):
        _require(
            field not in system or system.get(field) is False,
            f"legacy dashboard {field}가 비활성이 아닙니다.",
        )
    _require(
        "funding_readiness" not in system or system.get("funding_readiness") == "NOT_READY",
        "legacy funding readiness가 안전한 누락 또는 NOT_READY가 아닙니다.",
    )
    _require(payload.get("paused") is True, "legacy PAPER entry가 일시정지되지 않았습니다.")
    _require(operation.get("state") == "MANUALLY_PAUSED", "legacy 수동 일시정지 상태가 아닙니다.")
    _require(
        operation.get("market_observation_active") is True, "legacy 시장 관찰이 활성이 아닙니다."
    )
    _require(
        operation.get("paper_entry_active") is False, "legacy PAPER entry가 비활성이 아닙니다."
    )
    _require(
        operation.get("automatic_recovery") is False, "legacy 수동 정지에서 자동 복구가 활성입니다."
    )
    _require(intent.get("state") == "USER_PAUSED", "legacy entry intent가 USER_PAUSED가 아닙니다.")
    _require(
        intent.get("manual_pause_requested") is True, "legacy manual pause 의도가 True가 아닙니다."
    )
    _require(payload.get("position") is None, "legacy main position이 flat이 아닙니다.")
    _require(payload.get("focus_positions") == [], "legacy focus position이 flat이 아닙니다.")
    _require(payload.get("league_positions") == [], "legacy league position이 flat이 아닙니다.")
    flat_fields = (
        "main_pending_entry_count",
        "league_pending_entry_count",
        "total_pending_entry_count",
        "total_open_position_count",
        "paper_portfolio_flat",
    )
    reported_flat_fields = [field for field in flat_fields if field in payload]
    _require(
        len(reported_flat_fields) in {0, len(flat_fields)},
        "legacy dashboard flat 집계 필드가 일부만 보고됐습니다.",
    )
    if reported_flat_fields:
        main_pending = _exact_non_negative_int(
            payload.get("main_pending_entry_count"),
            "legacy dashboard main_pending_entry_count",
        )
        league_pending = _exact_non_negative_int(
            payload.get("league_pending_entry_count"),
            "legacy dashboard league_pending_entry_count",
        )
        total_pending = _exact_non_negative_int(
            payload.get("total_pending_entry_count"),
            "legacy dashboard total_pending_entry_count",
        )
        total_open = _exact_non_negative_int(
            payload.get("total_open_position_count"),
            "legacy dashboard total_open_position_count",
        )
        _require(
            total_pending == main_pending + league_pending,
            "legacy dashboard total pending이 main+league와 다릅니다.",
        )
        _require(main_pending == 0, "legacy dashboard main pending entry가 남아 있습니다.")
        _require(league_pending == 0, "legacy dashboard league pending entry가 남아 있습니다.")
        _require(total_pending == 0, "legacy dashboard total pending entry가 남아 있습니다.")
        _require(total_open == 0, "legacy dashboard open PAPER position이 남아 있습니다.")
        _require(
            payload.get("paper_portfolio_flat") is True,
            "legacy dashboard paper_portfolio_flat이 명시적 True가 아닙니다.",
        )
    account_pairs = _dashboard_account_pairs(payload)
    return cast(str, run_id), revision, intent_updated_ts_ms, account_pairs


def _verify_table_columns(connection: sqlite3.Connection) -> None:
    _require(
        connection.execute("PRAGMA user_version").fetchone()[0] == 7,
        "legacy 원장 user_version이 7이 아닙니다.",
    )
    for table, expected in _EXPECTED_TABLE_SCHEMA.items():
        schema_objects = connection.execute(
            "SELECT type, name, tbl_name FROM sqlite_schema WHERE name = ?",
            (table,),
        ).fetchall()
        _require(
            [tuple(row) for row in schema_objects] == [("table", table, table)],
            f"legacy 원장 {table} object가 exact table이 아닙니다.",
        )
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        actual = tuple((str(row[1]), str(row[2]), int(row[3]), int(row[5])) for row in rows)
        _require(actual == expected, f"legacy 원장 {table} schema가 검증 계약과 다릅니다.")


def _verify_execution_accounts(
    portfolio: Mapping[str, object],
    *,
    dashboard_pairs: set[tuple[str, str]],
) -> tuple[int, int]:
    accounts = _list(portfolio.get("accounts"), "snapshot portfolio.accounts")
    _require(bool(accounts), "snapshot execution account가 비어 있습니다.")
    account_ids: set[str] = set()
    execution_pairs: set[tuple[str, str]] = set()
    main_count = 0
    for index, value in enumerate(accounts):
        account = _mapping(value, f"snapshot account[{index}]")
        account_id = account.get("account_id")
        profile = account.get("profile")
        _require(
            isinstance(account_id, str) and bool(account_id),
            f"snapshot account[{index}] account_id가 없습니다.",
        )
        account_id_text = cast(str, account_id)
        _require(
            account_id_text not in account_ids,
            f"snapshot account가 중복됩니다: {account_id_text}",
        )
        account_ids.add(account_id_text)
        _require(
            profile in {"BASE", "STRESS"}, f"snapshot account profile이 잘못됐습니다: {account_id}"
        )
        pending_entries = account.get("pending_entries")
        positions = account.get("positions")
        _require(
            isinstance(pending_entries, dict) and not pending_entries,
            f"snapshot account에 pending entry가 남아 있습니다: {account_id}",
        )
        _require(
            isinstance(positions, dict) and not positions,
            f"snapshot account에 open position이 남아 있습니다: {account_id}",
        )
        risk_state = _mapping(account.get("risk_state"), f"snapshot {account_id}.risk_state")
        _require(
            _exact_non_negative_int(
                risk_state.get("open_positions"),
                f"snapshot {account_id}.risk_state.open_positions",
            )
            == 0,
            f"snapshot account risk에 open position이 남아 있습니다: {account_id}",
        )
        for field in (
            "open_planned_risk",
            "pending_planned_risk",
            "gross_notional",
            "pending_notional",
        ):
            _zero_decimal(risk_state.get(field), f"snapshot {account_id}.risk_state.{field}")
        if account_id_text == "MAIN:BASE":
            main_count += 1
        else:
            strategy_id, separator, account_profile = account_id_text.rpartition(":")
            _require(
                separator == ":" and bool(strategy_id) and account_profile == profile,
                f"snapshot account_id와 profile이 다릅니다: {account_id}",
            )
            execution_pairs.add((strategy_id, cast(str, profile)))
    _require(main_count == 1, "snapshot MAIN:BASE 계좌가 정확히 하나가 아닙니다.")
    _require(
        execution_pairs == dashboard_pairs, "snapshot 실행계좌와 dashboard 계좌 집합이 다릅니다."
    )

    shadow_ledger = _mapping(portfolio.get("shadow_ledger"), "snapshot shadow_ledger")
    shadow_accounts = _list(shadow_ledger.get("accounts"), "snapshot shadow_ledger.accounts")
    shadow_pairs: set[tuple[str, str]] = set()
    for index, value in enumerate(shadow_accounts):
        account = _mapping(value, f"snapshot shadow account[{index}]")
        shadow_strategy_id = account.get("strategy_id")
        shadow_profile = account.get("profile")
        _require(
            isinstance(shadow_strategy_id, str)
            and bool(shadow_strategy_id)
            and shadow_profile in {"BASE", "STRESS"},
            f"snapshot shadow account[{index}] 식별자가 잘못됐습니다.",
        )
        pair = (cast(str, shadow_strategy_id), cast(str, shadow_profile))
        _require(pair not in shadow_pairs, f"snapshot shadow account가 중복됩니다: {pair}")
        shadow_pairs.add(pair)
        open_positions = account.get("open_positions")
        _require(
            isinstance(open_positions, dict) and not open_positions,
            f"snapshot shadow account에 open position이 남아 있습니다: {pair}",
        )
    _require(
        shadow_pairs == dashboard_pairs, "snapshot shadow ledger와 dashboard 계좌 집합이 다릅니다."
    )
    return len(accounts), len(shadow_accounts)


def _open_read_only_ledger(ledger_path: Path) -> tuple[sqlite3.Connection, Path]:
    _require(not ledger_path.is_symlink(), "active ledger symlink는 허용하지 않습니다.")
    try:
        resolved = ledger_path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as error:
        raise LegacyRuntimePreflightError("active ledger를 읽을 수 없습니다.") from error
    _require(stat.S_ISREG(metadata.st_mode), "active ledger가 regular file이 아닙니다.")
    _require(resolved.name == "run-ledger.sqlite3", "active ledger 파일명이 검증 계약과 다릅니다.")
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{resolved}{suffix}")
        _require(not sidecar.is_symlink(), f"active ledger {suffix} symlink는 허용하지 않습니다.")
        if sidecar.exists():
            try:
                sidecar_metadata = sidecar.stat()
            except OSError as error:
                raise LegacyRuntimePreflightError(
                    f"active ledger {suffix} sidecar를 확인할 수 없습니다."
                ) from error
            _require(
                stat.S_ISREG(sidecar_metadata.st_mode),
                f"active ledger {suffix}가 regular file이 아닙니다.",
            )
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro&cache=private"
    try:
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=1.0,
            isolation_level=None,
        )
    except sqlite3.Error as error:
        raise LegacyRuntimePreflightError("active ledger를 mode=ro로 열 수 없습니다.") from error
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA busy_timeout=1000")
        _require(
            connection.execute("PRAGMA query_only").fetchone()[0] == 1,
            "SQLite query_only가 활성화되지 않았습니다.",
        )
        _require(
            str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal",
            "legacy active ledger가 WAL mode가 아닙니다.",
        )
    except BaseException:
        connection.close()
        raise
    return connection, resolved


def _parse_lsof_records(raw: bytes) -> list[dict[str, str]]:
    """NUL 구분 lsof 출력을 process/file 레코드 경계를 보존해 해석한다."""

    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_field in raw.split(b"\0"):
        starts_record = raw_field.startswith(b"\n")
        normalized = raw_field.lstrip(b"\n")
        if starts_record and current:
            records.append(current)
            current = {}
        if len(normalized) < 2:
            continue
        try:
            key = normalized[:1].decode("ascii")
            value = normalized[1:].decode("utf-8")
        except UnicodeDecodeError as error:
            raise LegacyRuntimePreflightError("lsof field를 해석할 수 없습니다.") from error
        _require(key not in current, f"lsof 레코드에 중복 field가 있습니다: {key}")
        current[key] = value
    if current:
        records.append(current)
    return records


def verify_running_process_binding(
    *,
    ledger_path: Path,
    release_path: Path,
    port: int = 8870,
    expected_service_pid: int | None = None,
) -> dict[str, object]:
    """LaunchAgent PID가 localhost listener와 검증 대상 원장을 함께 소유하는지 확인한다."""

    service_target = f"gui/{os.getuid()}/{_LAUNCH_AGENT_LABEL}"

    def loaded_pid() -> int:
        try:
            launchctl = subprocess.run(
                ["/bin/launchctl", "print", service_target],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise LegacyRuntimePreflightError(
                "legacy LaunchAgent PID를 확인할 수 없습니다."
            ) from error
        _require(bool(launchctl.stdout), "legacy launchctl 출력이 비어 있습니다.")
        _require(not launchctl.stderr, "legacy launchctl이 진단 오류를 보고했습니다.")
        matches = _LAUNCHCTL_PID.findall(launchctl.stdout)
        _require(len(matches) == 1, "legacy LaunchAgent PID가 정확히 하나가 아닙니다.")
        return int(matches[0])

    def lsof_records(*arguments: str, timeout: int = 3) -> list[dict[str, str]]:
        try:
            result = subprocess.run(
                ["/usr/sbin/lsof", "-nP", "-F0pfnDi", *arguments],
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise LegacyRuntimePreflightError(
                "legacy process lsof binding을 확인할 수 없습니다."
            ) from error
        _require(bool(result.stdout), "legacy process lsof 출력이 비어 있습니다.")
        _require(not result.stderr, "legacy process lsof가 부분 진단 오류를 보고했습니다.")
        records = _parse_lsof_records(result.stdout)
        process_ids = {record["p"] for record in records if "p" in record}
        _require(process_ids == {str(pid)}, "lsof 출력의 process PID가 LaunchAgent와 다릅니다.")
        return records

    def require_current_pointer() -> None:
        runtime_root = release_path.parent.parent
        current_pointer = runtime_root / "current"
        _require(current_pointer.is_symlink(), "legacy current 포인터가 symlink가 아닙니다.")
        try:
            current_release = current_pointer.resolve(strict=True)
        except OSError as error:
            raise LegacyRuntimePreflightError(
                "legacy current 포인터를 확인할 수 없습니다."
            ) from error
        _require(
            current_release == release_path,
            "legacy current 포인터가 실행 프로세스 릴리스와 다릅니다.",
        )

    def require_path_identity(
        records: list[dict[str, str]],
        expected_path: Path,
        label: str,
        *,
        file_descriptor: str | None = None,
    ) -> list[dict[str, str]]:
        expected = expected_path.stat()
        matches = [
            record
            for record in records
            if record.get("n") == str(expected_path)
            and (file_descriptor is None or record.get("f") == file_descriptor)
        ]
        _require(bool(matches), f"{label} lsof 레코드가 없습니다.")
        try:
            identities = {
                (int(record["D"], 0), int(record["i"], 10))
                for record in matches
                if "D" in record and "i" in record
            }
        except ValueError as error:
            raise LegacyRuntimePreflightError(
                f"{label} device/inode를 해석할 수 없습니다."
            ) from error
        _require(
            len(identities) == 1
            and identities == {(expected.st_dev, expected.st_ino)}
            and len(identities) == len({(record.get("D"), record.get("i")) for record in matches}),
            f"{label} lsof device/inode가 실제 경로와 다릅니다.",
        )
        _require(
            all("D" in record and "i" in record for record in matches),
            f"{label} lsof device/inode field가 누락됐습니다.",
        )
        return matches

    pid = loaded_pid()
    if expected_service_pid is not None:
        _require(pid == expected_service_pid, "legacy LaunchAgent PID가 preflight와 달라졌습니다.")
    require_current_pointer()
    ledger_owner = lsof_records("-a", "-p", str(pid), str(ledger_path))
    listener = lsof_records(
        "-a",
        "-p",
        str(pid),
        f"-iTCP:{port}",
        "-sTCP:LISTEN",
    )
    cwd_owner = lsof_records("-a", "-p", str(pid), "-d", "cwd")
    all_open_files = lsof_records("-a", "-p", str(pid), timeout=5)
    try:
        ledger_metadata = ledger_path.stat()
        release_metadata = release_path.stat()
    except OSError as error:
        raise LegacyRuntimePreflightError(
            "legacy ledger/release 경로 identity를 확인할 수 없습니다."
        ) from error
    require_path_identity(ledger_owner, ledger_path, "legacy active ledger")
    require_path_identity(
        cwd_owner,
        release_path,
        "legacy cwd",
        file_descriptor="cwd",
    )
    open_database_paths = {
        record["n"]
        for record in all_open_files
        if "n" in record
        for value in (record["n"],)
        if Path(value).name
        in {"run-ledger.sqlite3", "run-ledger.sqlite3-wal", "run-ledger.sqlite3-shm"}
    }
    allowed_database_paths = {
        str(ledger_path),
        f"{ledger_path}-wal",
        f"{ledger_path}-shm",
    }
    _require(
        bool(open_database_paths)
        and str(ledger_path) in open_database_paths
        and open_database_paths <= allowed_database_paths,
        "legacy LaunchAgent가 다른 run-ledger.sqlite3 계열 파일을 열고 있습니다.",
    )
    listener_names = {record["n"] for record in listener if "n" in record}
    _require(
        f"127.0.0.1:{port}" in listener_names,
        "legacy LaunchAgent가 127.0.0.1 listener를 소유하지 않습니다.",
    )
    require_current_pointer()
    _require(loaded_pid() == pid, "legacy LaunchAgent PID가 process binding 중 바뀌었습니다.")
    return {
        "launch_agent_label": _LAUNCH_AGENT_LABEL,
        "service_pid": pid,
        "listener": f"127.0.0.1:{port}",
        "cwd": str(release_path),
        "open_database_paths": sorted(open_database_paths),
        "ledger_device": ledger_metadata.st_dev,
        "ledger_inode": ledger_metadata.st_ino,
        "cwd_device": release_metadata.st_dev,
        "cwd_inode": release_metadata.st_ino,
        "ledger_open_by_service_pid": True,
    }


def verify_stopped_process_binding(*, ledger_path: Path, port: int = 8870) -> dict[str, object]:
    """graceful bootout 뒤 writer와 localhost listener가 모두 사라졌는지 확인한다."""

    service_target = f"gui/{os.getuid()}/{_LAUNCH_AGENT_LABEL}"
    try:
        launchctl_before = subprocess.run(
            ["/bin/launchctl", "print", service_target],
            check=False,
            capture_output=True,
            timeout=3,
        )
        ledger_owner = subprocess.run(
            ["/usr/sbin/lsof", "-nP", "-F0pfnDi", str(ledger_path)],
            check=False,
            capture_output=True,
            timeout=3,
        )
        listener = subprocess.run(
            [
                "/usr/sbin/lsof",
                "-nP",
                "-F0pfnDi",
                f"-iTCP:{port}",
                "-sTCP:LISTEN",
            ],
            check=False,
            capture_output=True,
            timeout=3,
        )
        launchctl_after = subprocess.run(
            ["/bin/launchctl", "print", service_target],
            check=False,
            capture_output=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LegacyRuntimePreflightError(
            "legacy service 중지 상태를 확인할 수 없습니다."
        ) from error
    expected_absence = (
        "Bad request.\n"
        f'Could not find service "{_LAUNCH_AGENT_LABEL}" in domain for user gui: {os.getuid()}\n'
    ).encode()
    for label, launchctl in (
        ("초기", launchctl_before),
        ("최종", launchctl_after),
    ):
        _require(
            launchctl.returncode == 113
            and not launchctl.stdout
            and launchctl.stderr == expected_absence,
            f"legacy LaunchAgent {label} 부재 응답이 exact 계약과 다릅니다.",
        )
    _require(
        ledger_owner.returncode == 1 and not ledger_owner.stdout and not ledger_owner.stderr,
        "legacy active ledger writer가 남았거나 lsof 진단이 실패했습니다.",
    )
    _require(
        listener.returncode == 1 and not listener.stdout and not listener.stderr,
        f"TCP {port} listener가 남았거나 lsof 진단이 실패했습니다.",
    )
    return {
        "launch_agent_absent": True,
        "ledger_writer_absent": True,
        "listener_absent": True,
        "port": port,
    }


def _verify_ledger(
    ledger_path: Path,
    *,
    run_id: str,
    revision: int,
    intent_updated_ts_ms: int,
    dashboard_pairs: set[tuple[str, str]],
    minimum_snapshot_id: int | None,
    minimum_recovery_audit_id: int | None,
) -> dict[str, object]:
    connection, resolved = _open_read_only_ledger(ledger_path)
    try:
        connection.execute("BEGIN")
        _verify_table_columns(connection)
        open_runs = connection.execute(
            """
            SELECT run_id, mode, venue, config_hash, config_json, started_ts_ms
            FROM runs
            WHERE finalized_ts_ms IS NULL
            ORDER BY started_ts_ms DESC, run_id
            LIMIT 2
            """
        ).fetchall()
        _require(len(open_runs) == 1, "legacy 원장의 open Run이 정확히 하나가 아닙니다.")
        open_run = open_runs[0]
        _require(open_run["run_id"] == run_id, "legacy 원장과 dashboard Run이 다릅니다.")
        _require(
            open_run["mode"] == "LIVE_SHADOW_PAPER",
            "legacy open Run mode가 LIVE_SHADOW_PAPER가 아닙니다.",
        )
        _require(
            open_run["venue"] == "BINANCE_USDM", "legacy open Run venue가 BINANCE_USDM가 아닙니다."
        )
        started_ts_ms = _exact_non_negative_int(
            open_run["started_ts_ms"], "legacy Run started_ts_ms"
        )
        config_hash = _require_sha256(open_run["config_hash"], "legacy Run config_hash")
        config_raw = open_run["config_json"]
        _decode_canonical_json(config_raw, "legacy Run config")
        _require(
            hashlib.sha256(str(config_raw).encode()).hexdigest() == config_hash,
            "legacy Run config checksum이 다릅니다.",
        )

        settings = connection.execute(
            """
            SELECT value_json, updated_ts_ms
            FROM app_settings
            WHERE setting_key = 'paper_entry_user_intent'
            LIMIT 2
            """
        ).fetchall()
        _require(
            len(settings) == 1,
            "legacy 원장의 paper_entry_user_intent가 정확히 하나가 아닙니다.",
        )
        setting = settings[0]
        setting_payload = _decode_canonical_json(setting["value_json"], "legacy pause setting")
        setting_updated_ts_ms = _exact_non_negative_int(
            setting["updated_ts_ms"], "legacy pause setting updated_ts_ms"
        )
        _require(setting_payload.get("run_id") == run_id, "legacy pause setting Run이 다릅니다.")
        _require(
            setting_payload.get("manual_pause_requested") is True,
            "legacy pause setting이 True가 아닙니다.",
        )
        _require(
            _exact_non_negative_int(
                setting_payload.get("revision"), "legacy pause setting revision"
            )
            == revision,
            "legacy pause setting revision이 dashboard와 다릅니다.",
        )
        _require(
            setting_updated_ts_ms >= intent_updated_ts_ms,
            "legacy pause setting 시각이 dashboard logical 시각보다 오래됐습니다.",
        )
        _require(
            setting_updated_ts_ms >= started_ts_ms,
            "legacy pause setting이 Run 시작보다 오래됐습니다.",
        )
        for field in ("actor", "reason"):
            _require(
                isinstance(setting_payload.get(field), str)
                and bool(str(setting_payload[field]).strip()),
                f"legacy pause setting {field}가 비어 있습니다.",
            )

        snapshot = connection.execute(
            """
            SELECT snapshot_id, run_id, lifecycle_state, ts_ms, payload_json, checksum
            FROM snapshots
            WHERE run_id = ?
            ORDER BY snapshot_id DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        _require(snapshot is not None, "legacy 원장에 복구 snapshot이 없습니다.")
        _require(snapshot["run_id"] == run_id, "최신 legacy snapshot Run이 dashboard와 다릅니다.")
        snapshot_id = _exact_non_negative_int(snapshot["snapshot_id"], "legacy snapshot_id")
        if minimum_snapshot_id is not None:
            _require(
                snapshot_id >= minimum_snapshot_id,
                "legacy snapshot_id가 최초 preflight보다 뒤로 갔습니다.",
            )
        snapshot_ts_ms = _exact_non_negative_int(snapshot["ts_ms"], "legacy snapshot ts_ms")
        _require(snapshot_ts_ms >= started_ts_ms, "legacy snapshot이 Run 시작보다 오래됐습니다.")
        _require(
            snapshot["lifecycle_state"] == "SCANNING",
            "legacy snapshot lifecycle이 SCANNING이 아닙니다.",
        )
        snapshot_checksum = _require_sha256(snapshot["checksum"], "legacy snapshot checksum")
        snapshot_raw = snapshot["payload_json"]
        _require(isinstance(snapshot_raw, str), "legacy snapshot payload가 문자열이 아닙니다.")
        _require(
            hashlib.sha256(snapshot_raw.encode()).hexdigest() == snapshot_checksum,
            "legacy snapshot checksum이 다릅니다.",
        )
        snapshot_payload = _decode_canonical_json(snapshot_raw, "legacy snapshot")
        _require(
            snapshot_payload.get("open_position") is None,
            "legacy snapshot main position이 flat이 아닙니다.",
        )
        _require(
            snapshot_payload.get("snapshot_ts_ms") == snapshot_ts_ms,
            "legacy snapshot 내부 시각이 row와 다릅니다.",
        )
        portfolio = _mapping(snapshot_payload.get("portfolio"), "legacy snapshot portfolio")
        _require(portfolio.get("schema_version") == 5, "legacy portfolio schema가 5가 아닙니다.")
        _require(portfolio.get("run_id") == run_id, "legacy portfolio Run이 다릅니다.")
        _require(portfolio.get("venue") == "BINANCE_USDM", "legacy portfolio venue가 다릅니다.")
        _require(
            portfolio.get("snapshot_ts_ms") == snapshot_ts_ms,
            "legacy portfolio 시각이 snapshot row와 다릅니다.",
        )
        execution_account_count, shadow_account_count = _verify_execution_accounts(
            portfolio,
            dashboard_pairs=dashboard_pairs,
        )

        placeholders = ",".join("?" for _ in _RECOVERY_STATE_EVENTS)
        audit = connection.execute(
            f"""
            SELECT audit_id, run_id, ts_ms, event_type, payload_json, checksum
            FROM execution_audit
            WHERE run_id = ? AND event_type IN ({placeholders})
            ORDER BY audit_id DESC
            LIMIT 1
            """,
            (run_id, *sorted(_RECOVERY_STATE_EVENTS)),
        ).fetchone()
        _require(audit is not None, "legacy 원장에 복구 상태 audit가 없습니다.")
        audit_id = _exact_non_negative_int(audit["audit_id"], "legacy recovery audit_id")
        if minimum_recovery_audit_id is not None:
            _require(
                audit_id >= minimum_recovery_audit_id,
                "legacy recovery audit_id가 최초 preflight보다 뒤로 갔습니다.",
            )
        audit_ts_ms = _exact_non_negative_int(audit["ts_ms"], "legacy recovery audit ts_ms")
        _require(audit["run_id"] == run_id, "legacy recovery audit Run이 다릅니다.")
        _require(
            audit["event_type"] in _RECOVERY_STATE_EVENTS, "legacy recovery audit 유형이 다릅니다."
        )
        audit_checksum = _require_sha256(audit["checksum"], "legacy recovery audit checksum")
        audit_raw = audit["payload_json"]
        _require(isinstance(audit_raw, str), "legacy recovery audit payload가 문자열이 아닙니다.")
        _require(
            hashlib.sha256(audit_raw.encode()).hexdigest() == audit_checksum,
            "legacy recovery audit checksum이 다릅니다.",
        )
        _decode_canonical_json(audit_raw, "legacy recovery audit")
        _require(
            audit_ts_ms >= started_ts_ms,
            "최신 legacy 복구 audit이 Run 시작보다 오래됐습니다.",
        )
        _require(
            audit_ts_ms <= snapshot_ts_ms,
            "최신 legacy 복구 audit 시각이 snapshot보다 미래입니다.",
        )
        connection.execute("ROLLBACK")
    except sqlite3.Error as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise LegacyRuntimePreflightError("legacy 원장 read-only 검증이 실패했습니다.") from error
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()
    return {
        "ledger_path": str(resolved),
        "sqlite_open_mode": "mode=ro",
        "sqlite_query_only": True,
        "sqlite_wal_included": True,
        "open_run_count": 1,
        "pause_setting_updated_ts_ms": setting_updated_ts_ms,
        "snapshot_id": snapshot_id,
        "snapshot_ts_ms": snapshot_ts_ms,
        "snapshot_checksum": snapshot_checksum,
        "recovery_audit_id": audit_id,
        "recovery_audit_ts_ms": audit_ts_ms,
        "recovery_audit_event": str(audit["event_type"]),
        "recovery_audit_checksum": audit_checksum,
        "execution_account_count": execution_account_count,
        "shadow_account_count": shadow_account_count,
        "pending_entry_count": 0,
        "open_position_count": 0,
        "causal_freshness": "ALLOWLISTED_SOURCE_ATOMIC_RECOVERY_BATCH_AND_REPROBED_SNAPSHOT",
    }


def verify_legacy_runtime_preflight(
    dashboard: Mapping[str, object],
    *,
    ledger_path: Path,
    runtime_root: Path,
    manifest: Mapping[str, object],
    expected_run_id: str | None = None,
    expected_pause_revision: int | None = None,
    expected_service_pid: int | None = None,
    minimum_snapshot_id: int | None = None,
    minimum_recovery_audit_id: int | None = None,
) -> dict[str, object]:
    """지원 대상으로 고정한 V1 서비스만 dashboard+WAL 원장으로 fail-closed 검증한다."""

    commit, manifest_schema_version, active_ledger_dir, release_path = _verify_manifest(
        manifest,
        runtime_root=runtime_root,
    )
    expected_ledger = active_ledger_dir / "run-ledger.sqlite3"
    _require(
        ledger_path.absolute() == expected_ledger,
        "검증 대상 ledger가 manifest active_ledger_dir와 다릅니다.",
    )
    process_binding_before = verify_running_process_binding(
        ledger_path=expected_ledger,
        release_path=release_path,
        expected_service_pid=expected_service_pid,
    )
    run_id, revision, intent_updated_ts_ms, dashboard_pairs = _verify_dashboard(
        dashboard,
        commit=commit,
        expected_run_id=expected_run_id,
        expected_pause_revision=expected_pause_revision,
    )
    ledger = _verify_ledger(
        expected_ledger,
        run_id=run_id,
        revision=revision,
        intent_updated_ts_ms=intent_updated_ts_ms,
        dashboard_pairs=dashboard_pairs,
        minimum_snapshot_id=minimum_snapshot_id,
        minimum_recovery_audit_id=minimum_recovery_audit_id,
    )
    process_binding_after = verify_running_process_binding(
        ledger_path=expected_ledger,
        release_path=release_path,
        expected_service_pid=_exact_non_negative_int(
            process_binding_before["service_pid"],
            "legacy process binding service_pid",
        ),
    )
    _require_stable_process_binding(process_binding_before, process_binding_after)
    return {
        "schema_version": 1,
        "status": "PASS",
        "evidence_mode": "VERIFIED_V1_DASHBOARD_AND_READ_ONLY_LEDGER_EQUIVALENCE",
        "release_commit": commit,
        "manifest_schema_version": manifest_schema_version,
        "run_id": run_id,
        "pause_state": "USER_PAUSED",
        "pause_revision": revision,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "wallet_paths_enabled": False,
        "runtime_ai_order_decision_enabled": False,
        "funding_readiness_observed": (
            _mapping(dashboard.get("system"), "dashboard system").get("funding_readiness")
            == "NOT_READY"
        ),
        "funding_readiness_equivalence": (
            "LEGACY_APPROVED_COMMIT_WITH_NO_PRIVATE_API_OR_WALLET_PATHS"
        ),
        "dashboard_account_count": len(dashboard_pairs),
        "process_binding_before": process_binding_before,
        "process_binding_after": process_binding_after,
        "process_binding_stable": True,
        "ledger": ledger,
    }


def verify_legacy_runtime_offline_after_stop(
    dashboard: Mapping[str, object],
    *,
    ledger_path: Path,
    runtime_root: Path,
    manifest: Mapping[str, object],
    expected_run_id: str,
    expected_pause_revision: int,
    minimum_snapshot_id: int,
    minimum_recovery_audit_id: int,
) -> dict[str, object]:
    """writer가 사라진 뒤 저장된 pause·flat snapshot을 마지막으로 다시 확인한다."""

    commit, manifest_schema_version, active_ledger_dir, _ = _verify_manifest(
        manifest,
        runtime_root=runtime_root,
    )
    expected_ledger = active_ledger_dir / "run-ledger.sqlite3"
    _require(
        ledger_path.absolute() == expected_ledger, "offline ledger 경로가 manifest와 다릅니다."
    )
    stopped = verify_stopped_process_binding(ledger_path=expected_ledger)
    run_id, revision, intent_updated_ts_ms, dashboard_pairs = _verify_dashboard(
        dashboard,
        commit=commit,
        expected_run_id=expected_run_id,
        expected_pause_revision=expected_pause_revision,
    )
    ledger = _verify_ledger(
        expected_ledger,
        run_id=run_id,
        revision=revision,
        intent_updated_ts_ms=intent_updated_ts_ms,
        dashboard_pairs=dashboard_pairs,
        minimum_snapshot_id=minimum_snapshot_id,
        minimum_recovery_audit_id=minimum_recovery_audit_id,
    )
    return {
        "schema_version": 1,
        "status": "PASS",
        "evidence_mode": "VERIFIED_V1_OFFLINE_POSTSTOP_LEDGER_EQUIVALENCE",
        "release_commit": commit,
        "manifest_schema_version": manifest_schema_version,
        "run_id": run_id,
        "pause_state": "USER_PAUSED",
        "pause_revision": revision,
        "stopped_process_binding": stopped,
        "ledger": ledger,
    }


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise LegacyRuntimePreflightError(f"{label} JSON을 읽을 수 없습니다.") from error
    payload = _strict_json_loads(raw, label)
    return _mapping(payload, label)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="V1 PAPER 서비스의 설치 전 안전 동등성을 검증합니다."
    )
    parser.add_argument("--dashboard", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-run-id")
    parser.add_argument("--expected-pause-revision", type=int)
    parser.add_argument("--expected-service-pid", type=int)
    parser.add_argument("--minimum-snapshot-id", type=int)
    parser.add_argument("--minimum-recovery-audit-id", type=int)
    parser.add_argument("--require-stopped", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    dashboard = _read_json_object(arguments.dashboard, "legacy dashboard")
    manifest = _read_json_object(arguments.manifest, "legacy manifest")
    if arguments.require_stopped:
        if (
            arguments.expected_run_id is None
            or arguments.expected_pause_revision is None
            or arguments.minimum_snapshot_id is None
            or arguments.minimum_recovery_audit_id is None
        ):
            raise LegacyRuntimePreflightError(
                "offline 검증에는 expected Run·pause revision·minimum snapshot이 필요합니다."
            )
        result = verify_legacy_runtime_offline_after_stop(
            dashboard,
            ledger_path=arguments.ledger,
            runtime_root=arguments.runtime_root,
            manifest=manifest,
            expected_run_id=arguments.expected_run_id,
            expected_pause_revision=arguments.expected_pause_revision,
            minimum_snapshot_id=arguments.minimum_snapshot_id,
            minimum_recovery_audit_id=arguments.minimum_recovery_audit_id,
        )
    else:
        result = verify_legacy_runtime_preflight(
            dashboard,
            ledger_path=arguments.ledger,
            runtime_root=arguments.runtime_root,
            manifest=manifest,
            expected_run_id=arguments.expected_run_id,
            expected_pause_revision=arguments.expected_pause_revision,
            expected_service_pid=arguments.expected_service_pid,
            minimum_snapshot_id=arguments.minimum_snapshot_id,
            minimum_recovery_audit_id=arguments.minimum_recovery_audit_id,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
