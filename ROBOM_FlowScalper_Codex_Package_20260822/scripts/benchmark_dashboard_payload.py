# V6 fixture의 화면 payload, 작은 delta, 변환 지연과 단일 프로세스 자원을 측정한다.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
from scripts import audit_v6_system_truth as audit

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


def _require_validated_thirty_minute_soak(source_commit: str) -> None:
    evidence = audit._validated_current_thirty_minute_soak(source_commit)  # noqa: SLF001
    if (
        evidence.get("status") != "PASS"
        or not isinstance(evidence.get("validated_runtime_observation"), dict)
    ):
        raise RuntimeError(
            "검증된 최신 30분 soak가 없어 benchmark evidence를 생성하지 않습니다."
        )


def _timing_ms(operation: Callable[[], object], *, iterations: int = 50) -> dict[str, Any]:
    durations: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
    samples = [round(duration, 6) for duration in durations]
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "iterations": iterations,
        "minimum_ms": round(ordered[0], 6),
        "median_ms": round(ordered[len(ordered) // 2], 6),
        "p95_ms": round(ordered[p95_index], 6),
        "maximum_ms": round(ordered[-1], 6),
        "measurement_boundary": "IN_PROCESS_PURE_TRANSFORM_NOT_HTTP_OR_BROWSER_RENDER",
        "samples_ms": samples,
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
    return cast(dict[str, object], compact_selected_family_detail(detail))


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


def _build_report_bundle(
    *,
    fixture_events: int,
    browser_e2e: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, object]]:
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
    timing_with_samples = {
        "ui_summary": _timing_ms(lambda: compact_ui_summary(dashboard)),
        "strategy_list": _timing_ms(lambda: strategy_page_summary(dashboard)),
        "selected_family_detail": _timing_ms(
            lambda: _selected_family_detail(runtime, dashboard)
        ),
        "single_tick_delta": _timing_ms(
            lambda: ui_delta_messages(summary, current_summary)
        ),
    }
    latency_samples = {
        name: measurement.pop("samples_ms")
        for name, measurement in timing_with_samples.items()
    }
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
    report = {
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
        "transform_latency": timing_with_samples,
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
    return report, {
        "dashboard_payload_json": dashboard,
        "summary_payload_json": summary,
        "strategy_summary_payload_json": strategy_summary,
        "chart_delta_message_json": chart_message,
        "full_chart_payload_json": current_summary["chart"],
        "dashboard_latency_samples_json": latency_samples,
    }


def build_report(
    *,
    fixture_events: int,
    browser_e2e: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """기존 호출자에는 측정 report만 반환한다."""

    report, _raw_artifacts = _build_report_bundle(
        fixture_events=fixture_events,
        browser_e2e=browser_e2e,
    )
    return report


def main() -> None:
    args = _parse_args()
    source_commit, source_clean = _source_revision()
    try:
        if not source_clean:
            raise RuntimeError(
                "source worktree가 clean하지 않아 benchmark evidence를 생성하지 않습니다."
            )
        _require_validated_thirty_minute_soak(source_commit)
    except RuntimeError as error:
        print(
            json.dumps(
                {"status": "NOT_RUN", "reason": str(error)},
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from error
    browser_e2e: dict[str, Any] | None = None
    try:
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and isinstance(existing.get("browser_e2e"), dict):
            prior_browser = existing["browser_e2e"]
            if prior_browser.get("status") != "NOT_RUN":
                browser_e2e = prior_browser
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        browser_e2e = None
    report, raw_artifacts = _build_report_bundle(
        fixture_events=args.fixture_events,
        browser_e2e=browser_e2e,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    project_root = PROJECT_ROOT.resolve(strict=True)
    output_path = args.output.resolve()
    if not output_path.is_relative_to(project_root):
        raise ValueError("benchmark 증거 output은 project root 안에 있어야 합니다.")
    artifact_root = args.output.parent / "artifacts" / args.output.stem.lower()
    artifact_root.mkdir(parents=True, exist_ok=True)

    def write_artifact(name: str, artifact_format: str, value: object) -> dict[str, object]:
        artifact_path = artifact_root / f"{name}.json"
        content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
        artifact_path.write_bytes(content)
        return {
            "kind": "artifact",
            "path": artifact_path.resolve().relative_to(project_root).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_count": len(content),
            "format": artifact_format,
        }

    artifacts = [
        write_artifact("measurement", "dashboard_benchmark_json", report),
        *[
            write_artifact(name, artifact_format, raw_payload)
            for artifact_format, raw_payload in raw_artifacts.items()
            for name in (artifact_format.removesuffix("_json"),)
        ],
    ]
    relative_output = output_path.relative_to(project_root).as_posix()
    wrapper = dict(report) | {
        "command": [
            "uv",
            "run",
            "python",
            "scripts/benchmark_dashboard_payload.py",
            "--fixture-events",
            str(args.fixture_events),
            "--output",
            relative_output,
        ],
        "exit_code": 0,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    args.output.write_text(
        json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n",
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
