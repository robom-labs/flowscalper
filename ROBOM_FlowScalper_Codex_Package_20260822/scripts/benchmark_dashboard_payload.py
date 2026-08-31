# V6 fixture의 화면 payload, 작은 delta, 변환 지연과 단일 프로세스 자원을 측정한다.

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.clocks import TestClock
from backend.app.domain.models import RuntimeMode
from backend.app.runtime import PaperRuntime
from backend.app.strategies.family import StrategyFamilyId, family_detail
from backend.app.ui_v6 import (
    compact_selected_family_detail,
    compact_ui_summary,
    payload_size_bytes,
    strategy_page_summary,
    ui_delta_messages,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "evidence/V6_DASHBOARD_PAYLOAD_BENCHMARK.json"
SOURCE_PATHS = (
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
    parser.add_argument("--fixture-events", type=int, default=100)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _source_revision() -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        [
            "git",
            "-C",
            str(PROJECT_ROOT),
            "status",
            "--porcelain",
            "--",
            *SOURCE_PATHS,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return commit, not status


def _timing_ms(operation: Callable[[], object], *, iterations: int = 50) -> dict[str, Any]:
    durations: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(durations)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "iterations": iterations,
        "minimum_ms": round(ordered[0], 6),
        "median_ms": round(ordered[len(ordered) // 2], 6),
        "p95_ms": round(ordered[p95_index], 6),
        "maximum_ms": round(ordered[-1], 6),
        "measurement_boundary": "IN_PROCESS_PURE_TRANSFORM_NOT_HTTP_OR_BROWSER_RENDER",
    }


def _selected_family_detail(
    runtime: PaperRuntime,
    dashboard: dict[str, Any],
) -> dict[str, object]:
    rows = [row for row in dashboard.get("strategies", []) if isinstance(row, dict)]
    performance = {
        str(row["strategy_id"]): row.get("performance")
        for row in rows
        if row.get("strategy_id") is not None
    }
    governance = {
        str(row["strategy_id"]): row.get("governance")
        for row in rows
        if row.get("strategy_id") is not None
    }
    detail = family_detail(
        runtime.strategy_registry,
        StrategyFamilyId.TREND_PULLBACK,
        performance,
        governance,
    )
    source_by_id = {
        str(row["strategy_id"]): row
        for row in rows
        if row.get("strategy_id") is not None
    }
    variants = detail.get("variants")
    if isinstance(variants, list):
        detail["variants"] = [
            dict(variant)
            | {"runtime_state": source_by_id.get(str(variant.get("strategy_id")), {})}
            for variant in variants
            if isinstance(variant, dict)
        ]
    detail["paper_only"] = True
    detail["real_orders_enabled"] = False
    detail["auth_required"] = False
    return compact_selected_family_detail(detail)


def _single_tick_delta(summary: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    current = deepcopy(summary)
    chart = current.get("chart")
    if not isinstance(chart, dict):
        raise ValueError("compact summary chart가 객체가 아닙니다.")
    points = chart.get("points")
    candles = chart.get("candles")
    if not isinstance(points, list) or not points:
        raise ValueError("fixture chart point가 없어 delta를 측정할 수 없습니다.")
    if not isinstance(candles, list) or not candles:
        raise ValueError("fixture candle이 없어 delta를 측정할 수 없습니다.")
    last_point = points[-1]
    last_candle = candles[-1]
    if not isinstance(last_point, dict) or not isinstance(last_candle, dict):
        raise ValueError("fixture chart 행이 객체가 아닙니다.")
    chart["points"] = [
        *points,
        dict(last_point)
        | {
            "index": int(str(last_point.get("index", len(points) - 1))) + 1,
            "ts_ms": int(str(last_point["ts_ms"])) + 1,
        },
    ]
    chart["candles"] = [
        *candles[:-1],
        dict(last_candle) | {"close": float(str(last_candle["close"])) + 0.01},
    ]
    messages = ui_delta_messages(summary, current)
    chart_messages = [message for message in messages if message.get("type") == "chart_delta"]
    if len(chart_messages) != 1:
        raise ValueError("한 tick 변경이 정확히 하나의 chart_delta를 만들지 않았습니다.")
    return current, chart_messages[0]


def build_report(
    *,
    fixture_events: int,
    browser_e2e: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if fixture_events <= 0:
        raise ValueError("fixture event 수는 양수여야 합니다.")
    source_commit, source_worktree_clean = _source_revision()
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        clock=TestClock(),
        run_id="run-v6-payload-benchmark",
    )
    runtime.boot_fixture(fixture_events)
    dashboard = runtime.dashboard()
    summary = compact_ui_summary(dashboard)
    strategy_summary = strategy_page_summary(dashboard)
    selected_detail = _selected_family_detail(runtime, dashboard)
    current_summary, chart_message = _single_tick_delta(summary)
    chart_delta = chart_message["data"]
    if not isinstance(chart_delta, dict):
        raise ValueError("chart delta data가 객체가 아닙니다.")
    dashboard_bytes = payload_size_bytes(dashboard)
    summary_bytes = payload_size_bytes(summary)
    strategy_summary_bytes = payload_size_bytes(strategy_summary)
    selected_detail_bytes = payload_size_bytes(selected_detail)
    chart_bytes = payload_size_bytes(current_summary["chart"])
    chart_delta_bytes = payload_size_bytes(chart_message)
    ratio = summary_bytes / dashboard_bytes
    strategy_ratio = strategy_summary_bytes / dashboard_bytes
    chart_delta_ratio = chart_delta_bytes / chart_bytes
    raw_system = dashboard.get("system")
    system: dict[str, Any] = dict(raw_system) if isinstance(raw_system, dict) else {}
    checks = {
        "summary_is_less_than_half_dashboard": ratio < 0.50,
        "strategy_summary_is_less_than_35_percent_dashboard": strategy_ratio < 0.35,
        "summary_omits_history": "history" not in summary,
        "summary_omits_strategy_detail": "strategies" not in summary,
        "summary_omits_account_detail": "league_accounts" not in summary,
        "single_tick_uses_incremental_chart_delta": chart_delta.get("refresh_required") is False,
        "single_tick_upserts_one_point": len(chart_delta.get("point_upserts", [])) == 1,
        "single_tick_upserts_at_most_one_candle": len(chart_delta.get("candle_upserts", [])) <= 1,
        "chart_delta_is_smaller_than_full_chart": chart_delta_bytes < chart_bytes,
        "paper_only": summary.get("paper_only") is True,
        "real_orders_disabled": summary.get("real_orders_enabled") is False,
        "auth_not_required": summary.get("auth_required") is False,
    }
    return {
        "schema_version": 2,
        "generated_ts_utc": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "source_commit": source_commit,
        "source_worktree_clean_at_measurement": source_worktree_clean,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "scope": "DEMO_FIXTURE_PAYLOAD_TRANSFORM_AND_SINGLE_PROCESS_SAMPLE",
        "fixture_events": fixture_events,
        "payload": {
            "baseline_endpoint": "/api/dashboard",
            "summary_endpoint": "/api/ui/summary",
            "strategy_list_endpoint": "/api/strategies/summary",
            "selected_detail_transport": "/ws/ui select_family",
            "dashboard_payload_bytes": dashboard_bytes,
            "summary_payload_bytes": summary_bytes,
            "strategy_summary_payload_bytes": strategy_summary_bytes,
            "selected_family_detail_payload_bytes": selected_detail_bytes,
            "summary_to_dashboard_ratio": round(ratio, 6),
            "strategy_summary_to_dashboard_ratio": round(strategy_ratio, 6),
            "target_summary_ratio_strictly_less_than": 0.50,
            "target_strategy_ratio_strictly_less_than": 0.35,
            "summary_reduction_percent": round((1 - ratio) * 100, 3),
        },
        "transform_latency": {
            "ui_summary": _timing_ms(lambda: compact_ui_summary(dashboard)),
            "strategy_list": _timing_ms(lambda: strategy_page_summary(dashboard)),
            "selected_family_detail": _timing_ms(
                lambda: _selected_family_detail(runtime, dashboard)
            ),
            "single_tick_delta": _timing_ms(
                lambda: ui_delta_messages(summary, current_summary)
            ),
        },
        "websocket_chart_delta": {
            "scope": "SYNTHETIC_SINGLE_TICK_FROM_DEMO_FIXTURE_CHART",
            "message_type": chart_message["type"],
            "refresh_required": chart_delta.get("refresh_required"),
            "point_upserts": len(chart_delta.get("point_upserts", [])),
            "point_removals": len(chart_delta.get("removed_point_ts_ms", [])),
            "candle_upserts": len(chart_delta.get("candle_upserts", [])),
            "candle_removals": len(chart_delta.get("removed_candle_open_ts_ms", [])),
            "delta_envelope_bytes": chart_delta_bytes,
            "full_chart_bytes": chart_bytes,
            "delta_to_full_chart_ratio": round(chart_delta_ratio, 6),
            "full_chart_repeated_for_measured_tick": False
            if chart_delta.get("refresh_required") is False
            else "NOT_PROVEN",
        },
        "single_process_resources": {
            "status": "MEASURED_NOT_SOAK",
            "scope": "ONE_DEMO_FIXTURE_DASHBOARD_SNAPSHOT",
            "process_cpu_percent": system.get("process_cpu_percent"),
            "process_memory_mb": system.get("process_memory_mb"),
            "queue_depth": system.get("queue_depth"),
            "queue_capacity": system.get("queue_capacity"),
            "interpretation": "단일 fixture 프로세스 순간값이며 장기 서비스 한도 판정이 아닙니다.",
        },
        "browser_e2e": browser_e2e
        or {
            "status": "NOT_RUN",
            "reason": "Playwright E2E 실행 전이거나 브라우저 측정 증거가 없습니다.",
        },
        "not_run": {
            "http_network_latency": "NOT_RUN_BY_THIS_SCRIPT",
            "running_service_cpu_memory_queue_soak": "NOT_RUN_BY_THIS_SCRIPT",
            "real_public_market_browser_performance": "NOT_RUN_BY_THIS_SCRIPT",
        },
        "checks": checks,
        "profitability": "NOT_PROVEN",
        "funding_readiness": "NOT_READY",
        "paper_only": True,
        "real_orders_enabled": False,
    }


def main() -> None:
    args = _parse_args()
    browser_e2e: dict[str, Any] | None = None
    try:
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("browser_e2e"), dict):
            prior_browser = existing["browser_e2e"]
            if prior_browser.get("status") != "NOT_RUN":
                browser_e2e = prior_browser
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        browser_e2e = None
    report = build_report(fixture_events=args.fixture_events, browser_e2e=browser_e2e)
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
                "dashboard_payload_bytes": report["payload"]["dashboard_payload_bytes"],
                "summary_payload_bytes": report["payload"]["summary_payload_bytes"],
                "ratio": report["payload"]["summary_to_dashboard_ratio"],
                "chart_delta_bytes": report["websocket_chart_delta"]["delta_envelope_bytes"],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
