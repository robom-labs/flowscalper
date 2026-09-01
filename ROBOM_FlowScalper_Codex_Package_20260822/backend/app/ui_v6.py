# V6의 작은 실시간 요약과 사용자·전문가 진단 API 계약을 만든다.

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from backend.app.strategies.family import FAMILY_CATALOG_BY_ID, StrategyFamilyId

_SUMMARY_SYSTEM_KEYS = (
    "app_version",
    "release_commit",
    "connection_state",
    "runtime_ready",
    "entry_locked",
    "private_api_enabled",
    "api_key_enabled",
    "wallet_enabled",
    "runtime_ai_order_decision_enabled",
    "funding_readiness",
    "storage_entry_allowed",
    "storage_lock_reason",
    "disk_free_mb",
    "disk_free_ratio",
    "queue_depth",
    "queue_capacity",
    "lag_p95_ms",
    "trade_lag_p95_ms",
    "last_error",
    "dashboard_trade_cache_ready",
)

_SUMMARY_DELTA_KEYS = (
    "status",
    "paused",
    "operation_status",
    "paper_entry_intent",
    "paper_only",
    "real_orders_enabled",
    "auth_required",
    "private_api_enabled",
    "api_key_enabled",
    "wallet_enabled",
    "runtime_ai_order_decision_enabled",
    "funding_readiness",
    "main_pending_entry_count",
    "league_pending_entry_count",
    "total_pending_entry_count",
    "total_open_position_count",
    "paper_portfolio_flat",
    "scanner",
    "performance",
    "control_operation",
    "control_revision",
    "system",
    "logs",
)

_POSITION_DELTA_KEYS = (
    "position",
    "focus_positions",
    "league_positions",
)

_STRATEGY_PERFORMANCE_KEYS = (
    "profile",
    "sample_size",
    "unique_opportunity_count",
    "profile_unique_opportunity_count",
    "wins",
    "losses",
    "breakevens",
    "win_rate",
    "win_rate_ci95",
    "payoff_ratio",
    "expectancy_usdt",
    "expectancy_r",
    "profit_factor",
    "net_pnl",
    "maximum_drawdown",
    "sample_status",
    "strategy_version",
    "excluded_prior_version_samples",
)

_STRATEGY_SUMMARY_KEYS = (
    "strategy_id",
    "family_id",
    "role",
    "variant_id",
    "variant_label_ko",
    "is_current_variant",
    "supersedes_strategy_ids",
    "superseded_by_strategy_id",
    "user_visible_by_default",
    "default_research_enabled",
    "final_ranking_eligible",
    "reason_code",
    "reason_ko",
    "reason_group",
    "blocking",
    "display_name_ko",
    "short_name",
    "mode",
    "lifecycle",
    "long_enabled",
    "short_enabled",
    "strategy_version",
    "evaluated_paths",
    "qualified_paths",
    "latest_status",
    "latest_reasons",
)

_STRATEGY_ACCOUNT_KEYS = (
    "account_id",
    "strategy_id",
    "profile",
    "starting_equity_usdt",
    "current_equity_usdt",
    "realized_pnl_usdt",
    "unrealized_pnl_usdt",
    "fees_usdt",
    "slippage_usdt",
    "trade_count",
    "daily_trade_count",
    "max_daily_trades",
    "realized_today_usdt",
    "realized_week_usdt",
    "daily_period_start_ms",
    "weekly_period_start_ms",
    "wins",
    "losses",
    "win_rate",
    "open_positions",
    "pending_entries",
    "gross_notional_usdt",
    "effective_leverage",
    "maximum_effective_leverage",
    "maximum_drawdown_usdt",
    "paused",
    "faulted",
)

_HEAVY_STRATEGY_PERFORMANCE_KEYS = {
    "metric_status",
    "regime_contributions",
    "windows",
}

_MAX_CHART_DELTA_BYTES = 16_384

_DIAGNOSTIC_LABELS: dict[str, tuple[str, str, bool]] = {
    "startup_recovery_state": ("\uc2dc\uc791 \ubcf5\uad6c \uacb0\uacfc", "RUNTIME", True),
    "last_paper_transition_state": (
        "\ub9c8\uc9c0\ub9c9 PAPER \uc804\ud658 \uacb0\uacfc",
        "RUNTIME",
        True,
    ),
    "connection_state": ("공개시장 연결", "CONNECTION", True),
    "runtime_ready": ("실행 준비", "RUNTIME", True),
    "automatic_recovery_enabled": ("PAPER 상태 자동 복구 계약", "RUNTIME", True),
    "entry_locked": ("신규 진입 안전잠금", "SAFETY", True),
    "storage_entry_allowed": ("저장상태 진입 허용", "PERSISTENCE", True),
    "storage_lock_reason": ("저장상태 잠금 이유", "PERSISTENCE", True),
    "disk_free_mb": ("남은 저장공간 MB", "PERSISTENCE", False),
    "disk_free_ratio": ("남은 저장공간 비율", "PERSISTENCE", False),
    "queue_depth": ("시장 이벤트 대기열", "QUEUE", False),
    "queue_capacity": ("시장 이벤트 대기열 한도", "QUEUE", False),
    "lag_p95_ms": ("전체 처리 지연 p95", "PERFORMANCE", False),
    "trade_lag_p95_ms": ("공개 체결 지연 p95", "PERFORMANCE", False),
    "event_loop_lag_last_ms": ("이벤트 루프 최근 지연", "PERFORMANCE", False),
    "persistence_backlog_peak": ("저장 대기열 최대치", "PERSISTENCE", False),
    "wal_checkpoint_last_ms": ("최근 WAL 정리 시간", "PERSISTENCE", False),
    "wal_checkpoint_pending_bytes": ("WAL 정리 대기 바이트", "PERSISTENCE", False),
    "reconnects": ("재연결 횟수", "CONNECTION", False),
    "sequence_gaps": ("순서 누락 횟수", "CONNECTION", False),
    "dropped_events": ("버린 이벤트 수", "QUEUE", False),
    "process_cpu_percent": ("프로세스 CPU", "PERFORMANCE", False),
    "process_memory_mb": ("프로세스 메모리 MB", "PERFORMANCE", False),
    "last_error": ("최근 오류", "RUNTIME", True),
}


def compact_ui_summary(snapshot: Mapping[str, object]) -> dict[str, object]:
    """전략상세·원장·원시진단을 제외한 실시간 화면 delta를 만든다."""

    raw_system = _mapping(snapshot.get("system"))
    compact_system = {key: raw_system[key] for key in _SUMMARY_SYSTEM_KEYS if key in raw_system}
    logs = _sequence_of_mappings(snapshot.get("logs"))
    actionable_logs = [
        dict(row)
        for row in logs
        if str(row.get("level", "")).upper() in {"WARNING", "ERROR", "CRITICAL"}
    ][-20:]
    strategies = _sequence_of_mappings(snapshot.get("strategies"))
    compact_strategy_state = [
        _compact_strategy_row(row)
        for row in strategies
        if bool(row.get("user_visible_by_default", row.get("is_current_variant", False)))
    ]
    safety = paper_safety_contract(snapshot)
    summary = {
        "schema_version": 1,
        "status": snapshot.get("status", {}),
        "paused": bool(snapshot.get("paused", True)),
        "operation_status": snapshot.get("operation_status", {}),
        "paper_entry_intent": snapshot.get("paper_entry_intent", {}),
        "main_pending_entry_count": snapshot.get("main_pending_entry_count", "NOT_PROVEN"),
        "league_pending_entry_count": snapshot.get(
            "league_pending_entry_count", "NOT_PROVEN"
        ),
        "total_pending_entry_count": snapshot.get(
            "total_pending_entry_count", "NOT_PROVEN"
        ),
        "total_open_position_count": snapshot.get(
            "total_open_position_count", "NOT_PROVEN"
        ),
        "paper_portfolio_flat": snapshot.get("paper_portfolio_flat", "NOT_PROVEN"),
        "scanner": list(_sequence(snapshot.get("scanner")))[:10],
        "chart": snapshot.get("chart", {}),
        "position": snapshot.get("position"),
        "focus_positions": list(_sequence(snapshot.get("focus_positions"))),
        "league_positions": list(_sequence(snapshot.get("league_positions"))),
        "strategy_state": compact_strategy_state,
        "performance": snapshot.get("performance", {}),
        "control_operation": snapshot.get("control_operation"),
        "control_revision": int(str(snapshot.get("control_revision", 0))),
        "system": compact_system,
        "logs": actionable_logs,
    } | safety
    return summary


def compact_mutation_summary(snapshot: Mapping[str, object]) -> dict[str, object]:
    """제어 응답에서 scanner·chart·상세 통계를 제외한 즉시 확인값만 반환한다."""

    summary = compact_ui_summary(snapshot)
    return {
        key: summary[key]
        for key in (
            "schema_version",
            "status",
            "paused",
            "operation_status",
            "paper_entry_intent",
            "main_pending_entry_count",
            "league_pending_entry_count",
            "total_pending_entry_count",
            "total_open_position_count",
            "paper_portfolio_flat",
            "position",
            "focus_positions",
            "league_positions",
            "strategy_state",
            "control_operation",
            "control_revision",
            "system",
            "paper_only",
            "real_orders_enabled",
            "auth_required",
            "private_api_enabled",
            "api_key_enabled",
            "wallet_enabled",
            "runtime_ai_order_decision_enabled",
            "funding_readiness",
        )
    }


def strategy_page_summary(snapshot: Mapping[str, object]) -> dict[str, object]:
    """전략 화면 표에 필요한 current/default-visible 행과 계좌만 만든다."""

    source_rows = _sequence_of_mappings(snapshot.get("strategies"))
    enabled_directional_entry_candidate_count = sum(
        1
        for row in source_rows
        if str(row.get("role", "")) == "ENTRY"
        and row.get("superseded_by_strategy_id") is None
        and str(row.get("mode", "")) in {"SHADOW", "ACTIVE"}
        and str(row.get("lifecycle", ""))
        in {"SHADOW", "CHALLENGER", "ACTIVE"}
        and (bool(row.get("long_enabled")) or bool(row.get("short_enabled")))
    )
    visible_rows = [
        row
        for row in source_rows
        if bool(row.get("user_visible_by_default"))
        or (
            row.get("is_current_variant") is True
            and str(row.get("role", "")) != "LEGACY"
        )
    ]
    strategies = [_strategy_summary_row(row) for row in visible_rows]
    visible_ids = {str(row["strategy_id"]) for row in strategies}
    league_accounts = [
        {
            key: account.get(key)
            for key in _STRATEGY_ACCOUNT_KEYS
            if key in account
        }
        for account in _sequence_of_mappings(snapshot.get("league_accounts"))
        if str(account.get("strategy_id")) in visible_ids
        and str(account.get("profile")) in {"BASE", "STRESS"}
    ]
    safety = paper_safety_contract(snapshot)
    return {
        "schema_version": 1,
        "analysis_scope": "CURRENT_STRATEGY_VERSION",
        "strategies": strategies,
        "league_accounts": league_accounts,
        "strategy_count": len(strategies),
        "league_account_count": len(league_accounts),
        "enabled_directional_entry_candidate_count": (
            enabled_directional_entry_candidate_count
        ),
    } | safety


def ui_delta_messages(
    previous: Mapping[str, object],
    current: Mapping[str, object],
) -> list[dict[str, object]]:
    """두 compact snapshot 사이의 V6 화면별 작은 변경만 반환한다."""

    messages: list[dict[str, object]] = []
    summary_delta = {
        key: current.get(key)
        for key in _SUMMARY_DELTA_KEYS
        if previous.get(key) != current.get(key)
    }
    if summary_delta:
        messages.append({"type": "summary_delta", "data": summary_delta})

    chart_delta = _chart_delta(previous.get("chart"), current.get("chart"))
    if chart_delta is not None:
        messages.append({"type": "chart_delta", "data": chart_delta})

    position_delta = {
        key: current.get(key)
        for key in _POSITION_DELTA_KEYS
        if previous.get(key) != current.get(key)
    }
    if position_delta:
        messages.append({"type": "position_delta", "data": position_delta})

    previous_rows = _strategy_rows_by_id(previous.get("strategy_state"))
    current_rows = _strategy_rows_by_id(current.get("strategy_state"))
    changed_rows = [
        current_rows[strategy_id]
        for strategy_id in sorted(current_rows)
        if previous_rows.get(strategy_id) != current_rows[strategy_id]
    ]
    removed_strategy_ids = sorted(set(previous_rows) - set(current_rows))
    if changed_rows or removed_strategy_ids:
        messages.append(
            {
                "type": "strategy_row_delta",
                "data": {
                    "rows": changed_rows,
                    "removed_strategy_ids": removed_strategy_ids,
                },
            }
        )
    return messages


def compact_selected_family_detail(detail: Mapping[str, object]) -> dict[str, object]:
    """선택 family 상세에서 원장·조건식·원시진단을 제외한다."""

    variants: list[dict[str, object]] = []
    for raw_variant in _sequence_of_mappings(detail.get("variants")):
        setting = _mapping(raw_variant.get("setting"))
        runtime_state = _mapping(raw_variant.get("runtime_state"))
        variants.append(
            {
                key: raw_variant.get(key)
                for key in (
                    "strategy_id",
                    "family_id",
                    "role",
                    "variant_id",
                    "variant_label_ko",
                    "is_current_variant",
                    "supersedes_strategy_ids",
                    "superseded_by_strategy_id",
                    "user_visible_by_default",
                    "default_research_enabled",
                    "final_ranking_eligible",
                    "research_sources",
                )
                if key in raw_variant
            }
            | {
                "setting": {
                    key: setting.get(key)
                    for key in (
                        "mode",
                        "lifecycle",
                        "long_enabled",
                        "short_enabled",
                        "settings_revision",
                        "research_enabled",
                        "enabled",
                        "revision",
                        "manual_lock",
                        "change_reason",
                    )
                    if key in setting
                },
                "runtime_state": _compact_strategy_row(runtime_state),
            }
        )

    return {
        key: detail.get(key)
        for key in (
            "family_id",
            "label_ko",
            "category_ko",
            "description_ko",
            "display_order",
            "current_variant_id",
            "variant_count",
            "availability_state",
            "availability_label_ko",
            "availability_reason_ko",
            "paper_only",
            "real_orders_enabled",
            "auth_required",
            "private_api_enabled",
            "api_key_enabled",
            "wallet_enabled",
            "runtime_ai_order_decision_enabled",
            "funding_readiness",
        )
        if key in detail
    } | {
        "variants": variants,
        "offline_challengers": [
            {
                key: row.get(key)
                for key in (
                    "strategy_id",
                    "family_id",
                    "baseline_strategy_ids",
                    "state",
                    "current_variant",
                    "runtime_registered",
                    "live_shadow_enabled",
                    "paper_only",
                )
                if key in row
            }
            for row in _sequence_of_mappings(detail.get("offline_challengers"))
        ],
    }


def settings_summary(snapshot: Mapping[str, object]) -> dict[str, object]:
    status = _mapping(snapshot.get("status"))
    system = _mapping(snapshot.get("system"))
    risk = _mapping(snapshot.get("risk"))
    paper_intent = _mapping(snapshot.get("paper_entry_intent"))
    paper_state_recovery = system.get("automatic_recovery_enabled")
    safety = paper_safety_contract(snapshot)
    return {
        "schema_version": 1,
        "run": {
            "run_id": status.get("run_id", "UNKNOWN"),
            "mode": status.get("mode", "READY"),
            "venue": status.get("venue", "NONE"),
            "new_run_preserves_history": True,
        },
        "safety": {
            key: safety[key]
            for key in (
                "paper_only",
                "real_orders_enabled",
                "auth_required",
                "private_api_enabled",
                "api_key_enabled",
                "wallet_enabled",
                "runtime_ai_order_decision_enabled",
            )
        }
        | {
            "entry_state": paper_intent.get("state", "USER_PAUSED"),
            "entry_revision": int(str(paper_intent.get("revision", 0))),
            "active_locks": list(_sequence(risk.get("active_locks"))),
        },
        "costs": _mapping(_mapping(risk.get("strategy_league"))),
        "storage": {
            "label": system.get("storage", "외장 APFS PAPER 저장소"),
            "free_mb": system.get("disk_free_mb"),
            "free_ratio": system.get("disk_free_ratio"),
            "entry_allowed": system.get("storage_entry_allowed"),
            "lock_reason": system.get("storage_lock_reason"),
        },
        "connection": {
            "state": system.get("connection_state", status.get("market_data_state")),
            "public_market_only": True,
        },
        "autostart": {
            "state": "NOT_PROVEN",
            "paper_state_recovery_reported": paper_state_recovery,
            "launch_agent_verified": False,
            "read_only": True,
            "evidence_source": "LAUNCH_AGENT_NOT_INSPECTED",
            "evidence_ko": (
                "이 화면에서는 macOS LaunchAgent 등록 상태를 조회하거나 변경하지 않았습니다. "
                "PAPER 상태 자동 복구 보고값은 로그인·재부팅 자동 시작의 증거가 아닙니다."
            ),
        },
        "local_preferences": {
            "research_detail_default": False,
            "research_detail_affects_execution": False,
        },
        "funding_readiness": safety["funding_readiness"],
    }


def diagnostics_rows(snapshot: Mapping[str, object]) -> dict[str, object]:
    """Backend가 한국어 label과 severity를 정해 frontend 문자열 추론을 없앤다."""

    system = _mapping(snapshot.get("system"))
    safety = paper_safety_contract(snapshot)
    rows: list[dict[str, object]] = []
    for key, (label_ko, group, user_visible) in _DIAGNOSTIC_LABELS.items():
        if key not in system:
            continue
        value = system[key]
        rows.append(
            {
                "key": key,
                "label_ko": label_ko,
                "value": value,
                "severity": _diagnostic_severity(key, value),
                "user_visible": user_visible,
                "group": group,
            }
        )
    return {
        "schema_version": 1,
        "rows": rows,
        "raw": dict(system),
    } | safety


def stable_etag(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f'"{hashlib.sha256(canonical.encode()).hexdigest()}"'


def payload_size_bytes(payload: object) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode()
    )


def _diagnostic_severity(key: str, value: object) -> str:
    if key == "last_error":
        return "CRITICAL" if value not in {None, "", "NONE"} else "OK"
    if key in {"entry_locked", "storage_entry_allowed", "runtime_ready"}:
        healthy = not bool(value) if key == "entry_locked" else bool(value)
        return "OK" if healthy else "WARNING"
    if key == "storage_lock_reason":
        return "OK" if value in {None, "", "NONE"} else "WARNING"
    if key in {"dropped_events", "sequence_gaps"}:
        return "OK" if int(str(value or 0)) == 0 else "WARNING"
    return "INFO"


def paper_safety_contract(snapshot: Mapping[str, object]) -> dict[str, object]:
    """안전값을 만들지 않고 dashboard의 명시된 원본만 전달한다."""

    status = _mapping(snapshot.get("status"))
    system = _mapping(snapshot.get("system"))
    safety = _mapping(snapshot.get("safety"))
    risk = _mapping(snapshot.get("risk"))

    def consistent(key: str, *sources: Mapping[str, object]) -> object:
        values = [source[key] for source in sources if key in source]
        if not values:
            return "NOT_PROVEN"
        expected = values[0]
        if any(
            type(value) is not type(expected) or value != expected
            for value in values[1:]
        ):
            return "NOT_PROVEN"
        return expected

    real_orders = consistent("real_orders_enabled", snapshot, safety, status, system)
    auth_required = consistent("auth_required", snapshot, safety, status, system)
    private_api = consistent("private_api_enabled", snapshot, safety, system, status)
    api_key = consistent("api_key_enabled", snapshot, safety, system, status)
    wallet = consistent("wallet_enabled", snapshot, safety, system, status)
    runtime_ai = consistent(
        "runtime_ai_order_decision_enabled", snapshot, safety, system, status
    )
    funding = consistent("funding_readiness", snapshot, safety, system, status)
    paper_only = consistent("paper_only", snapshot, safety, status, system, risk)

    return {
        "paper_only": paper_only,
        "real_orders_enabled": real_orders,
        "auth_required": auth_required,
        "private_api_enabled": private_api,
        "api_key_enabled": api_key,
        "wallet_enabled": wallet,
        "runtime_ai_order_decision_enabled": runtime_ai,
        "funding_readiness": funding,
    }


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, str | bytes) else ()


def _sequence_of_mappings(value: object) -> list[Mapping[str, object]]:
    return [row for row in _sequence(value) if isinstance(row, Mapping)]


def _strategy_rows_by_id(value: object) -> dict[str, dict[str, object]]:
    return {
        str(row["strategy_id"]): dict(row)
        for row in _sequence_of_mappings(value)
        if row.get("strategy_id") is not None
    }


def _compact_strategy_row(row: Mapping[str, object]) -> dict[str, object]:
    compact = {
        key: row.get(key)
        for key in (
            "strategy_id",
            "family_id",
            "mode",
            "lifecycle",
            "long_enabled",
            "short_enabled",
            "settings_revision",
            "manual_lock",
            "policy_reactivation_locked",
            "latest_status",
            "latest_reasons",
            "reason_code",
            "reason_ko",
            "reason_group",
            "blocking",
            "evaluated_paths",
            "qualified_paths",
        )
        if key in row
    }
    raw_performance = _mapping(row.get("performance"))
    performance = {
        profile: {
            key: profile_row.get(key)
            for key in _STRATEGY_PERFORMANCE_KEYS
            if key in profile_row
        }
        for profile in ("BASE", "STRESS")
        if (profile_row := _mapping(raw_performance.get(profile)))
    }
    if performance:
        compact["performance"] = performance
    return compact


def _strategy_summary_row(row: Mapping[str, object]) -> dict[str, object]:
    compact = {
        key: row.get(key)
        for key in _STRATEGY_SUMMARY_KEYS
        if key in row
    }
    family_id = str(row.get("family_id", ""))
    try:
        family = FAMILY_CATALOG_BY_ID[StrategyFamilyId(family_id)]
    except ValueError:
        family = None
    if family is not None:
        compact["family_label_ko"] = family.label_ko
    raw_performance = _mapping(row.get("performance"))
    compact["performance"] = {
        profile: {
            key: value
            for key, value in profile_row.items()
            if key not in _HEAVY_STRATEGY_PERFORMANCE_KEYS
        }
        for profile in ("BASE", "STRESS")
        if (profile_row := _mapping(raw_performance.get(profile)))
    }
    compact["paper_only"] = True
    return compact


def _chart_delta(previous_value: object, current_value: object) -> dict[str, object] | None:
    previous = _mapping(previous_value)
    current = _mapping(current_value)
    if previous == current:
        return None

    selection = {
        "symbol": current.get("symbol"),
        "interval": current.get("interval"),
        "fixture": bool(current.get("fixture", False)),
    }
    if any(previous.get(key) != current.get(key) for key in selection):
        return selection | {"refresh_required": True}

    previous_points = _rows_by_integer_key(previous.get("points"), "ts_ms")
    current_points = _rows_by_integer_key(current.get("points"), "ts_ms")
    previous_candles = _rows_by_integer_key(previous.get("candles"), "open_ts_ms")
    current_candles = _rows_by_integer_key(current.get("candles"), "open_ts_ms")
    if previous_points is None or current_points is None:
        return selection | {"refresh_required": True}
    if previous_candles is None or current_candles is None:
        return selection | {"refresh_required": True}

    point_upserts = [
        current_points[key]
        for key in sorted(current_points)
        if previous_points.get(key) != current_points[key]
    ]
    candle_upserts = [
        current_candles[key]
        for key in sorted(current_candles)
        if previous_candles.get(key) != current_candles[key]
    ]
    delta: dict[str, object] = selection | {
        "refresh_required": False,
        "point_upserts": point_upserts,
        "removed_point_ts_ms": sorted(set(previous_points) - set(current_points)),
        "candle_upserts": candle_upserts,
        "removed_candle_open_ts_ms": sorted(
            set(previous_candles) - set(current_candles)
        ),
    }
    if previous.get("lines") != current.get("lines"):
        delta["lines"] = current.get("lines", {})

    known_keys = {"symbol", "interval", "fixture", "points", "candles", "lines"}
    extra_keys = (set(previous) | set(current)) - known_keys
    if any(previous.get(key) != current.get(key) for key in extra_keys):
        return selection | {"refresh_required": True}
    if payload_size_bytes(delta) > _MAX_CHART_DELTA_BYTES:
        return selection | {"refresh_required": True}
    return delta


def _rows_by_integer_key(
    value: object,
    key: str,
) -> dict[int, dict[str, object]] | None:
    rows: dict[int, dict[str, object]] = {}
    for row in _sequence_of_mappings(value):
        raw_key = row.get(key)
        if not isinstance(raw_key, int):
            return None
        rows[raw_key] = dict(row)
    return rows
