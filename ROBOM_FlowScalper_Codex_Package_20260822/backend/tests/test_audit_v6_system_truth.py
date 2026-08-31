# V6 진실 감사의 보수적 증거 상태와 30분 범위 판정을 회귀 검증한다.

from __future__ import annotations

import hashlib
import json
import plistlib
import struct
import subprocess
import zlib
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.app.ops.service_soak import (
    RunningServiceSample,
    RunningServiceSoakThresholds,
    StrategyState,
    summarize_running_service_soak,
)
from scripts import audit_v6_system_truth as audit
from scripts import benchmark_dashboard_payload as benchmark
from scripts import generate_v6_browser_evidence as browser_evidence
from scripts import generate_v6_final_validation as final_validation
from scripts import stage_macos_release
from scripts.verify_compatibility_runtime_preflight import LegacyRuntimePreflightError


def _current_network_timing_evidence(
    *,
    completed_offset: timedelta = timedelta(seconds=1),
) -> dict[str, object]:
    completed_at = datetime.now(UTC) - completed_offset
    started_at = completed_at - timedelta(seconds=1)
    started_epoch_ms = started_at.timestamp() * 1_000
    return {
        "event_samples": [
            {
                "stream": "binance-public-depth",
                "source_ts_ms": started_epoch_ms + 100,
                "received_ts_ms": started_epoch_ms + 101,
            },
            {
                "stream": "binance-market-aggtrade",
                "source_ts_ms": started_epoch_ms + 200,
                "received_ts_ms": started_epoch_ms + 202,
            },
        ],
        "elapsed_ms": 1_000.0,
        "started_ts_utc": started_at.isoformat().replace("+00:00", "Z"),
        "completed_ts_utc": completed_at.isoformat().replace("+00:00", "Z"),
    }


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


@pytest.mark.parametrize(
    "source_path",
    [
        "data/runtime.json",
        "packaging/macos/kr.robom.flowscalper.plist",
        "ROBOM_FlowScalper.app/Contents/MacOS/ROBOM_FlowScalper",
        "ROBOM_FlowScalper.command",
        "schemas/trade_record.schema.json",
    ],
)
def test_evidence_only_commit_equivalence_rejects_deployable_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_path: str,
) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "codex@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Codex Test"],
        cwd=tmp_path,
        check=True,
    )
    backend = tmp_path / "backend/app.py"
    docs = tmp_path / "docs/evidence.md"
    backend.parent.mkdir(parents=True)
    docs.parent.mkdir(parents=True)
    backend.write_text("PAPER_ONLY = True\n", encoding="utf-8")
    docs.write_text("C1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "C1 source"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    c1 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    docs.write_text("C2 evidence only\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "C2 evidence"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    c2 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    changed_source = tmp_path / source_path
    changed_source.parent.mkdir(parents=True, exist_ok=True)
    changed_source.write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "source change"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    c3 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    assert audit._commits_have_equivalent_source(c1, c2) is True
    assert audit._commits_have_equivalent_source(c2, c3) is False


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
    assert (
        audit._load_evidence(
            "evidence/not-run.json",
            require_checks=True,
        )["status"]
        == "NOT_RUN"
    )


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
    assert (
        audit._load_evidence(
            "evidence/result.json",
            require_checks=True,
        )["status"]
        == "FAIL"
    )


def _artifact_record(
    root: Path,
    relative_path: str,
    *,
    kind: str,
    content: bytes,
    **metadata: object,
) -> dict[str, object]:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "kind": kind,
        "path": relative_path,
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_count": len(content),
        **metadata,
    }


def _png_bytes(
    width: int,
    height: int,
    *,
    varied: bool = True,
    seed: int = 0,
) -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = b"".join(
        b"\x00"
        + (bytes([(row_index + seed) % 251 + 1]) + bytes(width - 1) if varied else bytes(width))
        for row_index in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


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


def test_dashboard_benchmark_requires_raw_payloads_and_latency_samples(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    commit = "b" * 40
    relative_path = "evidence/dashboard-benchmark.json"
    path = tmp_path / relative_path
    dashboard_payload = {
        "system": {"event_count": 100},
        "history": [{"trade": "x" * 1_200}],
        "strategies": [{"detail": "y" * 1_200}],
        "league_accounts": [{"detail": "z" * 1_200}],
    }
    summary_payload = {
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "chart": {"points": [{"ts_ms": 1, "value": 1}]},
    }
    strategy_payload = {"rows": [{"strategy_id": "A", "mode": "SHADOW"}]}
    chart_delta_message = {
        "type": "chart_delta",
        "data": {
            "refresh_required": False,
            "point_upserts": [{"ts_ms": 2, "value": 2}],
            "candle_upserts": [],
        },
    }
    full_chart_payload = {"points": [{"ts_ms": index, "value": index} for index in range(100)]}
    latency_samples = {
        name: [round(0.1 + index / 100, 6) for index in range(50)]
        for name in (
            "ui_summary",
            "strategy_list",
            "selected_family_detail",
            "single_tick_delta",
        )
    }

    def encoded_size(value: object) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())

    latency = {
        name: {"iterations": len(samples), "p95_ms": sorted(samples)[47]}
        for name, samples in latency_samples.items()
    }
    dashboard_bytes = encoded_size(dashboard_payload)
    summary_bytes = encoded_size(summary_payload)
    strategy_bytes = encoded_size(strategy_payload)
    delta_bytes = encoded_size(chart_delta_message)
    full_chart_bytes = encoded_size(full_chart_payload)
    measurement: dict[str, Any] = {
        "schema_version": 2,
        "generated_ts_utc": "2026-08-31T12:00:00Z",
        "source_commit": commit,
        "source_worktree_clean_at_measurement": True,
        "status": "PASS",
        "checks": {name: True for name in audit.BENCHMARK_REQUIRED_CHECKS},
        "fixture_events": 100,
        "payload": {
            "dashboard_payload_bytes": dashboard_bytes,
            "summary_payload_bytes": summary_bytes,
            "strategy_summary_payload_bytes": strategy_bytes,
            "summary_to_dashboard_ratio": round(summary_bytes / dashboard_bytes, 6),
            "strategy_summary_to_dashboard_ratio": round(
                strategy_bytes / dashboard_bytes,
                6,
            ),
            "target_summary_ratio_strictly_less_than": 0.5,
            "target_strategy_ratio_strictly_less_than": 0.35,
        },
        "websocket_chart_delta": {
            "message_type": "chart_delta",
            "refresh_required": False,
            "point_upserts": 1,
            "candle_upserts": 0,
            "delta_envelope_bytes": delta_bytes,
            "full_chart_bytes": full_chart_bytes,
            "delta_to_full_chart_ratio": round(delta_bytes / full_chart_bytes, 6),
        },
        "transform_latency": latency,
        "paper_only": True,
        "real_orders_enabled": False,
    }
    artifact = _artifact_record(
        tmp_path,
        "evidence/artifacts/dashboard-benchmark.json",
        kind="artifact",
        content=json.dumps(measurement).encode(),
        format="dashboard_benchmark_json",
    )
    payload: dict[str, Any] = dict(measurement) | {
        "command": ["uv", "run", "python", "scripts/benchmark_dashboard_payload.py"],
        "exit_code": 0,
        "artifact_count": 1,
        "artifacts": [artifact],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    missing_raw = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="dashboard_payload_benchmark",
        expected_schema_version=2,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert missing_raw["status"] == "NOT_PROVEN"
    assert missing_raw["reason"] == "BENCHMARK_RAW_PAYLOAD_OR_LATENCY_ARTIFACT_MISSING"

    raw_payloads = {
        "dashboard_payload_json": dashboard_payload,
        "summary_payload_json": summary_payload,
        "strategy_summary_payload_json": strategy_payload,
        "chart_delta_message_json": chart_delta_message,
        "full_chart_payload_json": full_chart_payload,
        "dashboard_latency_samples_json": latency_samples,
    }
    raw_artifacts = [
        _artifact_record(
            tmp_path,
            f"evidence/artifacts/{artifact_format}.json",
            kind="artifact",
            content=json.dumps(raw_payload, ensure_ascii=False).encode(),
            format=artifact_format,
        )
        for artifact_format, raw_payload in raw_payloads.items()
    ]
    payload["artifacts"] = [artifact, *raw_artifacts]
    payload["artifact_count"] = len(payload["artifacts"])
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="dashboard_payload_benchmark",
        expected_schema_version=2,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert result["status"] == "PASS", result

    measurement["payload"]["summary_payload_bytes"] = summary_bytes + 1
    payload["artifacts"] = [
        _artifact_record(
            tmp_path,
            "evidence/artifacts/dashboard-benchmark.json",
            kind="artifact",
            content=json.dumps(measurement).encode(),
            format="dashboard_benchmark_json",
        ),
        *raw_artifacts,
    ]
    payload["artifact_count"] = len(payload["artifacts"])
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


def test_dashboard_benchmark_producer_writes_validator_backed_raw_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "b" * 40
    output = tmp_path / audit.EVIDENCE_PATHS["dashboard_payload_benchmark"]
    monkeypatch.setattr(benchmark, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(benchmark, "_source_revision", lambda: (commit, True))
    monkeypatch.setattr(
        benchmark,
        "_require_validated_thirty_minute_soak",
        lambda _commit: None,
    )
    monkeypatch.setattr(
        benchmark,
        "_parse_args",
        lambda: type("Args", (), {"fixture_events": 10, "output": output})(),
    )

    with pytest.raises(SystemExit) as exit_result:
        benchmark.main()
    assert exit_result.value.code == 0

    wrapper = json.loads(output.read_text(encoding="utf-8"))
    assert wrapper["artifact_count"] == 7
    assert {
        artifact["format"] for artifact in wrapper["artifacts"] if isinstance(artifact, dict)
    } == {"dashboard_benchmark_json", *audit.BENCHMARK_RAW_FORMATS}
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    result = audit._load_evidence(
        audit.EVIDENCE_PATHS["dashboard_payload_benchmark"],
        require_checks=True,
        evidence_kind="dashboard_payload_benchmark",
        expected_schema_version=2,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert result["status"] == "PASS", result


def test_browser_e2e_pass_requires_playwright_report_and_valid_png_screenshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    commit = "c" * 40
    screenshot_paths = {
        (check_id, project): f"evidence/screenshots/{check_id}-{project}.png"
        for check_id, projects in audit.BROWSER_REQUIRED_TEST_PROJECTS.items()
        for project in projects
    }
    trivial_playwright_report = {
        "config": {"projects": [{"name": project} for project in ("desktop", "tablet", "mobile")]},
        "errors": [],
        "stats": {"expected": 3, "unexpected": 0, "flaky": 0, "skipped": 0},
        "suites": [
            {
                "title": "dashboard",
                "suites": [],
                "specs": [
                    {
                        "title": "renders",
                        "tests": [
                            {
                                "projectName": project,
                                "expectedStatus": "passed",
                                "results": [{"status": "passed", "errors": []}],
                            }
                            for project in ("desktop", "tablet", "mobile")
                        ],
                    }
                ],
            }
        ],
    }
    expected_test_count = sum(
        len(projects) for projects in audit.BROWSER_REQUIRED_TEST_PROJECTS.values()
    )
    playwright_report = {
        "config": trivial_playwright_report["config"],
        "errors": [],
        "stats": {
            "expected": expected_test_count,
            "unexpected": 0,
            "flaky": 0,
            "skipped": 0,
        },
        "suites": [
            {
                "title": "dashboard audit",
                "suites": [],
                "specs": [
                    {
                        "title": f"audit:{check_id}",
                        "tests": [
                            {
                                "projectName": project,
                                "expectedStatus": "passed",
                                "results": [
                                    {
                                        "status": "passed",
                                        "errors": [],
                                        "attachments": [
                                            {
                                                "name": f"{check_id}-{project}",
                                                "contentType": "image/png",
                                                "path": screenshot_paths[(check_id, project)],
                                            }
                                        ],
                                    }
                                ],
                            }
                            for project in sorted(projects)
                        ],
                    }
                    for check_id, projects in sorted(audit.BROWSER_REQUIRED_TEST_PROJECTS.items())
                ],
            }
        ],
    }
    browser_measurements = {
        "schema": "flowscalper.browser_measurements.v1",
        "runtime_url": "http://127.0.0.1:8870",
        "page_ids": audit.EXPECTED_PAGE_IDS,
        "checks": {name: True for name in audit.BROWSER_REQUIRED_CHECKS},
        "runtime_provenance": {
            "run_id_at_start": "run-browser-audit",
            "run_id_at_end": "run-browser-audit",
            "execution_state_at_start": "PAPER",
            "execution_state_at_end": "PAPER",
            "release_commit_at_start": commit,
            "release_commit_at_end": commit,
            "release_isolated_at_start": True,
            "release_isolated_at_end": True,
        },
        "paper_safety_at_start": dict(audit.BROWSER_EXPECTED_PAPER_SAFETY),
        "paper_safety_at_end": dict(audit.BROWSER_EXPECTED_PAPER_SAFETY),
        "paper_safety_source_paths": audit.BROWSER_PAPER_SAFETY_SOURCE_PATHS,
        "projects": {
            project: {
                "status": "PASS",
                "console_error_count": 0,
                "screenshot_paths": sorted(
                    path
                    for (check_id, screenshot_project), path in screenshot_paths.items()
                    if screenshot_project == project
                ),
            }
            for project in ("desktop", "tablet", "mobile")
        },
    }
    artifacts = [
        _artifact_record(
            tmp_path,
            "evidence/artifacts/playwright.json",
            kind="artifact",
            content=json.dumps(playwright_report).encode(),
            format="playwright_json",
        ),
        _artifact_record(
            tmp_path,
            "evidence/artifacts/browser-measurements.json",
            kind="artifact",
            content=json.dumps(browser_measurements).encode(),
            format="browser_measurements_json",
        ),
        *[
            _artifact_record(
                tmp_path,
                screenshot_paths[(check_id, project)],
                kind="screenshot",
                content=_png_bytes(
                    *{
                        "desktop": (1408, 900),
                        "tablet": (820, 1180),
                        "mobile": (390, 844),
                    }[project],
                    seed=screenshot_index,
                ),
                format="png",
                project=project,
                check_ids=[check_id],
            )
            for screenshot_index, (check_id, project) in enumerate(screenshot_paths)
        ],
    ]
    relative_path = "evidence/browser.json"
    path = tmp_path / relative_path
    wrapper = {
        "schema_version": 1,
        "generated_ts_utc": "2026-08-31T12:00:00Z",
        "source_commit": commit,
        "source_commit_at_end": commit,
        "source_worktree_clean_at_start": True,
        "source_worktree_clean_at_end": True,
        "source_worktree_clean_at_measurement": True,
        "release_commit": commit,
        "release_commit_at_end": commit,
        "release_isolated": True,
        "release_isolated_at_end": True,
        "run_id": "run-browser-audit",
        "run_id_at_end": "run-browser-audit",
        "execution_state": "PAPER",
        "execution_state_at_end": "PAPER",
        "paper_safety_at_start": dict(audit.BROWSER_EXPECTED_PAPER_SAFETY),
        "paper_safety_at_end": dict(audit.BROWSER_EXPECTED_PAPER_SAFETY),
        "paper_safety_source_paths": audit.BROWSER_PAPER_SAFETY_SOURCE_PATHS,
        "status": "PASS",
        "checks": {name: True for name in audit.BROWSER_REQUIRED_CHECKS},
        "command": ["pnpm", "exec", "playwright", "test"],
        "exit_code": 0,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "counts": {
            "page_count": 4,
            "project_count": 3,
            "test_count": expected_test_count,
            "screenshot_count": expected_test_count,
            "console_error_count": 0,
        },
        "page_ids": audit.EXPECTED_PAGE_IDS,
        "projects": ["desktop", "tablet", "mobile"],
        "runtime_url": "http://127.0.0.1:8870",
    }
    trivial_artifact = _artifact_record(
        tmp_path,
        "evidence/artifacts/playwright.json",
        kind="artifact",
        content=json.dumps(trivial_playwright_report).encode(),
        format="playwright_json",
    )
    wrapper["artifacts"] = [trivial_artifact, *artifacts[1:]]
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    trivial = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="browser_e2e_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
        require_release_binding=True,
    )
    assert trivial["status"] == "NOT_PROVEN"
    assert trivial["reason"] == "BROWSER_MACHINE_REPORT_BINDING_MISMATCH"

    artifacts[0] = _artifact_record(
        tmp_path,
        "evidence/artifacts/playwright.json",
        kind="artifact",
        content=json.dumps(playwright_report).encode(),
        format="playwright_json",
    )
    wrapper["artifacts"] = artifacts
    path.write_text(json.dumps(wrapper), encoding="utf-8")

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

    desktop_keys = [key for key in screenshot_paths if key[1] == "desktop"]
    source_key, target_key = desktop_keys[:2]
    duplicate_target = _artifact_record(
        tmp_path,
        screenshot_paths[target_key],
        kind="screenshot",
        content=(tmp_path / screenshot_paths[source_key]).read_bytes(),
        format="png",
        project="desktop",
        check_ids=[target_key[0]],
    )
    wrapper["artifacts"] = [
        duplicate_target if artifact.get("path") == screenshot_paths[target_key] else artifact
        for artifact in artifacts
    ]
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    duplicate_content = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="browser_e2e_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
        require_release_binding=True,
    )
    assert duplicate_content["status"] == "NOT_PROVEN"
    assert duplicate_content["reason"] == "BROWSER_SCREENSHOT_PNG_OR_DIMENSIONS_INVALID"
    target_index = list(screenshot_paths).index(target_key)
    target_path = tmp_path / screenshot_paths[target_key]
    target_path.write_bytes(_png_bytes(1408, 900, seed=target_index))
    wrapper["artifacts"] = artifacts

    solid_check_id = "all_four_pages_rendered"
    solid_mobile_path = screenshot_paths[(solid_check_id, "mobile")]
    solid_mobile = _artifact_record(
        tmp_path,
        solid_mobile_path,
        kind="screenshot",
        content=_png_bytes(390, 844, varied=False),
        format="png",
        project="mobile",
        check_ids=[solid_check_id],
    )
    wrapper["artifacts"] = [
        solid_mobile if artifact.get("path") == solid_mobile_path else artifact
        for artifact in artifacts
    ]
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    solid = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="browser_e2e_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
        require_release_binding=True,
    )
    assert solid["status"] == "NOT_PROVEN"
    assert solid["reason"] == "BROWSER_SCREENSHOT_PNG_OR_DIMENSIONS_INVALID"


def test_browser_producer_binds_exact_specs_png_and_stable_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "c" * 40
    screenshot_paths = {
        (check_id, project): f"evidence/screenshots/{check_id}-{project}.png"
        for check_id, projects in audit.BROWSER_REQUIRED_TEST_PROJECTS.items()
        for project in projects
    }
    dimensions_by_project = {
        "desktop": (1408, 900),
        "tablet": (820, 1180),
        "mobile": (390, 844),
    }
    for screenshot_index, ((_check_id, project), screenshot_path) in enumerate(
        screenshot_paths.items()
    ):
        screenshot = tmp_path / screenshot_path
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        screenshot.write_bytes(_png_bytes(*dimensions_by_project[project], seed=screenshot_index))
    expected_test_count = sum(
        len(projects) for projects in audit.BROWSER_REQUIRED_TEST_PROJECTS.values()
    )
    report = {
        "config": {"projects": [{"name": project} for project in ("desktop", "tablet", "mobile")]},
        "errors": [],
        "stats": {
            "expected": expected_test_count,
            "unexpected": 0,
            "flaky": 0,
            "skipped": 0,
        },
        "suites": [
            {
                "title": "8870 audit",
                "suites": [],
                "specs": [
                    {
                        "title": f"audit:{check_id}",
                        "tests": [
                            {
                                "projectName": project,
                                "expectedStatus": "passed",
                                "results": [
                                    {
                                        "status": "passed",
                                        "errors": [],
                                        "attachments": [
                                            {
                                                "name": f"{check_id}-{project}",
                                                "contentType": "image/png",
                                                "path": screenshot_paths[(check_id, project)],
                                            }
                                        ],
                                    }
                                ],
                            }
                            for project in sorted(projects)
                        ],
                    }
                    for check_id, projects in sorted(audit.BROWSER_REQUIRED_TEST_PROJECTS.items())
                ],
            }
        ],
    }
    dashboard = {
        "status": {
            "run_id": "run-browser-audit",
            "execution_state": "PAPER",
            "real_orders_enabled": False,
            "auth_required": False,
        },
        "risk": {"paper_only": True},
        "system": {
            "release_commit": commit,
            "release_isolated": True,
            "private_api_enabled": False,
            "api_key_enabled": False,
            "wallet_enabled": False,
            "runtime_ai_order_decision_enabled": False,
            "funding_readiness": "NOT_READY",
        },
    }
    output = tmp_path / audit.EVIDENCE_PATHS["browser_e2e_after_latest_change"]
    monkeypatch.setattr(browser_evidence, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    wrapper = browser_evidence.assemble_evidence(
        output,
        raw_report=report,
        dashboard_at_start=dashboard,
        dashboard_at_end=dashboard,
        playwright_exit_code=0,
        source_commit=commit,
        source_commit_at_end=commit,
        clean_at_start=True,
        clean_at_end=True,
    )

    assert wrapper["status"] == "PASS"
    assert wrapper["counts"] == {
        "page_count": 4,
        "project_count": 3,
        "test_count": expected_test_count,
        "screenshot_count": expected_test_count,
        "console_error_count": 0,
    }
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    result = audit._load_evidence(
        audit.EVIDENCE_PATHS["browser_e2e_after_latest_change"],
        require_checks=True,
        evidence_kind="browser_e2e_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
        require_release_binding=True,
    )
    assert result["status"] == "PASS", result

    for field, value, remove in (
        ("api_key_enabled", None, True),
        ("wallet_enabled", True, False),
        ("funding_readiness", "READY", False),
    ):
        safety_tamper = deepcopy(wrapper)
        raw_safety = safety_tamper["paper_safety_at_start"]
        assert isinstance(raw_safety, dict)
        if remove:
            raw_safety.pop(field)
        else:
            raw_safety[field] = value
        output.write_text(json.dumps(safety_tamper), encoding="utf-8")
        tampered_safety = audit._load_evidence(
            audit.EVIDENCE_PATHS["browser_e2e_after_latest_change"],
            require_checks=True,
            evidence_kind="browser_e2e_after_latest_change",
            expected_schema_version=1,
            expected_source_commit=commit,
            source_working_tree_changes=[],
            require_release_binding=True,
        )
        assert tampered_safety["status"] == "NOT_PROVEN"
        assert tampered_safety["reason"] == "BROWSER_PAPER_SAFETY_BINDING_MISMATCH"

    measurement_record = next(
        artifact
        for artifact in wrapper["artifacts"]
        if artifact.get("format") == "browser_measurements_json"
    )
    measurement_path = tmp_path / str(measurement_record["path"])
    original_measurement = measurement_path.read_bytes()
    tampered_measurement = json.loads(original_measurement)
    tampered_measurement["paper_safety_at_end"]["private_api_enabled"] = True
    replacement_measurement_record = _artifact_record(
        tmp_path,
        str(measurement_record["path"]),
        kind="artifact",
        content=json.dumps(tampered_measurement).encode(),
        format="browser_measurements_json",
    )
    measurement_wrapper = deepcopy(wrapper)
    measurement_wrapper["artifacts"] = [
        replacement_measurement_record
        if artifact.get("format") == "browser_measurements_json"
        else artifact
        for artifact in measurement_wrapper["artifacts"]
    ]
    output.write_text(json.dumps(measurement_wrapper), encoding="utf-8")
    measurement_tamper = audit._load_evidence(
        audit.EVIDENCE_PATHS["browser_e2e_after_latest_change"],
        require_checks=True,
        evidence_kind="browser_e2e_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
        require_release_binding=True,
    )
    assert measurement_tamper["status"] == "NOT_PROVEN"
    assert measurement_tamper["reason"] == "BROWSER_PAPER_SAFETY_BINDING_MISMATCH"
    measurement_path.write_bytes(original_measurement)
    output.write_text(json.dumps(wrapper), encoding="utf-8")

    tampered_wrapper = deepcopy(wrapper)
    tampered_wrapper["run_id_at_end"] = "run-rewrapped-after-test"
    output.write_text(json.dumps(tampered_wrapper), encoding="utf-8")
    tampered = audit._load_evidence(
        audit.EVIDENCE_PATHS["browser_e2e_after_latest_change"],
        require_checks=True,
        evidence_kind="browser_e2e_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
        require_release_binding=True,
    )
    assert tampered["status"] == "NOT_PROVEN"
    assert tampered["reason"] == "BROWSER_RUNTIME_PROVENANCE_BINDING_MISMATCH"

    restarted_dashboard = deepcopy(dashboard)
    restarted_status = restarted_dashboard["status"]
    assert isinstance(restarted_status, dict)
    restarted_status["run_id"] = "run-restarted"
    changed_runtime = browser_evidence.assemble_evidence(
        output,
        raw_report=report,
        dashboard_at_start=dashboard,
        dashboard_at_end=restarted_dashboard,
        playwright_exit_code=0,
        source_commit=commit,
        source_commit_at_end=commit,
        clean_at_start=True,
        clean_at_end=True,
    )
    assert changed_runtime["status"] == "FAIL"

    for section, field, value, remove in (
        ("system", "api_key_enabled", None, True),
        ("system", "wallet_enabled", True, False),
        ("system", "funding_readiness", "READY", False),
    ):
        unsafe_dashboard = deepcopy(dashboard)
        nested = unsafe_dashboard[section]
        assert isinstance(nested, dict)
        if remove:
            nested.pop(field)
        else:
            nested[field] = value
        unsafe_runtime = browser_evidence.assemble_evidence(
            output,
            raw_report=report,
            dashboard_at_start=unsafe_dashboard,
            dashboard_at_end=dashboard,
            playwright_exit_code=0,
            source_commit=commit,
            source_commit_at_end=commit,
            clean_at_start=True,
            clean_at_end=True,
        )
        assert unsafe_runtime["status"] == "FAIL"


def test_browser_producer_requires_validated_soak_before_playwright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit,
        "_validated_current_thirty_minute_soak",
        lambda _commit: {"status": "NOT_RUN"},
    )
    with pytest.raises(RuntimeError, match="30분 soak"):
        browser_evidence._require_validated_thirty_minute_soak("c" * 40)


def test_full_suite_pass_requires_parseable_runner_reports_and_derived_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    commit = "d" * 40
    backend_source = tmp_path / "backend/tests/test_full_suite_fixture.py"
    frontend_source = tmp_path / "frontend/tests/full-suite.test.ts"
    backend_source.parent.mkdir(parents=True)
    frontend_source.parent.mkdir(parents=True)
    backend_source.write_text(
        "def test_one(): pass\n\ndef test_two(): pass\n",
        encoding="utf-8",
    )
    frontend_source.write_text(
        "import { test } from 'vitest'\ntest('one', () => {})\ntest('two', () => {})\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("uv-lock\n", encoding="utf-8")
    (tmp_path / "frontend/pnpm-lock.yaml").write_text("pnpm-lock\n", encoding="utf-8")
    python_executable = tmp_path / ".venv/bin/python"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    python_executable.chmod(0o755)
    node_modules_marker = tmp_path / "frontend/node_modules/.modules.yaml"
    node_modules_marker.parent.mkdir(parents=True)
    node_modules_marker.write_text("modules: {}\n", encoding="utf-8")
    (tmp_path / "VERSION").write_text("0.3.0-paper\n", encoding="utf-8")
    e2e_pytest_record = _artifact_record(
        tmp_path,
        "evidence/reports/master-e2e-pytest.xml",
        kind="artifact",
        content=(
            b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase classname="backend.tests.test_full_suite_fixture" '
            b'name="test_one"/></testsuite></testsuites>'
        ),
        format="master_e2e_pytest_junit_xml",
    )
    e2e_playwright_report = {
        "config": {"projects": [{"name": project} for project in ("desktop", "tablet", "mobile")]},
        "errors": [],
        "stats": {"expected": 3, "skipped": 0, "unexpected": 0, "flaky": 0},
        "suites": [
            {
                "title": "dashboard.spec.ts",
                "file": "dashboard.spec.ts",
                "specs": [
                    {
                        "title": "fixture dashboard",
                        "file": "dashboard.spec.ts",
                        "tests": [
                            {
                                "projectName": project,
                                "results": [{"status": "passed", "errors": []}],
                            }
                            for project in ("desktop", "tablet", "mobile")
                        ],
                    }
                ],
            }
        ],
    }
    e2e_playwright_record = _artifact_record(
        tmp_path,
        "evidence/reports/master-e2e-playwright.json",
        kind="artifact",
        content=json.dumps(e2e_playwright_report).encode(),
        format="master_e2e_playwright_json",
    )
    artifacts = [e2e_pytest_record, e2e_playwright_record]
    commands = []
    report_contents: dict[str, bytes] = {
        "backend_pytest": (
            b'<testsuites name="pytest tests"><testsuite tests="2" failures="0" '
            b'errors="0" skipped="0">'
            b'<testcase classname="backend.tests.test_full_suite_fixture" '
            b'name="test_one"/><testcase '
            b'classname="backend.tests.test_full_suite_fixture" name="test_two"/>'
            b"</testsuite></testsuites>"
        ),
        "backend_ruff": b"[]",
        "backend_mypy": b"Success: no issues found in 42 source files\n",
        "frontend_tests": json.dumps(
            {
                "numTotalTests": 2,
                "numPassedTests": 2,
                "numFailedTests": 0,
                "success": True,
                "testResults": [
                    {
                        "name": str(frontend_source),
                        "assertionResults": [
                            {
                                "fullName": "one",
                                "status": "passed",
                                "failureMessages": [],
                            },
                            {
                                "fullName": "two",
                                "status": "passed",
                                "failureMessages": [],
                            },
                        ],
                    }
                ],
            }
        ).encode(),
        "frontend_lint": json.dumps(
            [
                {
                    "filePath": "src/App.tsx",
                    "messages": [],
                    "errorCount": 0,
                    "fatalErrorCount": 0,
                }
            ]
        ).encode(),
        "frontend_typecheck": b"/project/src/main.ts\n/project/src/App.tsx\n",
        "frontend_build": "✓ 123 modules transformed.\n✓ built in 1.00s\n".encode(),
        "build_safety": "PASS: PAPER 전용 빌드 불변조건\n".encode(),
        "setup": json.dumps(
            {
                "schema": "flowscalper.setup_validation.v1",
                "command": ["make", "setup"],
                "exit_code": 0,
                "lock_sha256": {
                    "uv.lock": hashlib.sha256((tmp_path / "uv.lock").read_bytes()).hexdigest(),
                    "frontend/pnpm-lock.yaml": hashlib.sha256(
                        (tmp_path / "frontend/pnpm-lock.yaml").read_bytes()
                    ).hexdigest(),
                },
                "python_executable_path": ".venv/bin/python",
                "node_modules_marker_path": "frontend/node_modules/.modules.yaml",
            }
        ).encode(),
        "master_e2e": json.dumps(
            {
                "schema": "flowscalper.master_e2e_bundle.v1",
                "command": ["make", "e2e"],
                "exit_code": 0,
                "pytest_junit": e2e_pytest_record,
                "playwright_json": e2e_playwright_record,
            }
        ).encode(),
        "network_smoke": json.dumps(
            {
                "status": "PASS",
                "venue": "BINANCE_USDM",
                "eligible_symbol_count": 100,
                "binance_catalog_count": 100,
                "upbit_krw_catalog_count": 100,
                "binance_btcusdt_3m_candle_count": 200,
                "binance_catalog_tail_3m_candle_count": 200,
                "upbit_krw_btc_3m_candle_count": 200,
                "websocket_events": 2,
                **_current_network_timing_evidence(),
                "lag_p50_ms": 1.5,
                "lag_p95_ms": 2.0,
                "credentials_sent": False,
                "authorization_header_sent": False,
                "auth_required": False,
                "real_orders_enabled": False,
            }
        ).encode(),
        "security_scan": json.dumps(
            {
                "status": "PASS",
                "checked_source_files": 10,
                "forbidden_fragments": ["/fapi/v1/order"],
                "violations": [],
                "secret_like_files": [],
                "real_order_path": False,
            }
        ).encode(),
        "repo_hygiene": json.dumps(
            {"status": "PASS", "version": "0.3.0-paper", "violations": []}
        ).encode(),
    }
    for name in audit.FULL_SUITE_COMMAND_ORDER:
        report_format = audit.FULL_SUITE_REPORT_FORMATS[name]
        relative_report = f"evidence/reports/{name}.out"
        artifacts.append(
            _artifact_record(
                tmp_path,
                relative_report,
                kind=("artifact" if report_format.endswith(("json", "xml")) else "log"),
                content=report_contents[name],
                format=report_format,
            )
        )
        commands.append(
            {
                "name": name,
                "command": [
                    part.format(report_path=relative_report)
                    for part in audit.FULL_SUITE_CANONICAL_COMMANDS[name]
                ],
                "exit_code": 0,
                "report_path": relative_report,
            }
        )
    backend_ids = [
        "backend/tests/test_full_suite_fixture.py::test_one",
        "backend/tests/test_full_suite_fixture.py::test_two",
    ]
    frontend_ids = [
        "frontend/tests/full-suite.test.ts::one",
        "frontend/tests/full-suite.test.ts::two",
    ]
    trusted_collection_ids = {
        "backend": set(backend_ids),
        "frontend": set(frontend_ids),
    }
    monkeypatch.setattr(
        audit,
        "_current_full_suite_collection_ids",
        lambda: trusted_collection_ids,
    )
    monkeypatch.setattr(
        audit,
        "_current_fixture_e2e_test_ids",
        lambda: {backend_ids[0]},
    )
    monkeypatch.setattr(
        audit,
        "_current_master_e2e_case_ids",
        lambda: {
            f"frontend/e2e/dashboard.spec.ts::fixture dashboard::{project}"
            for project in ("desktop", "tablet", "mobile")
        },
    )
    monkeypatch.setattr(
        audit,
        "_current_security_scan_report",
        lambda: json.loads(report_contents["security_scan"]),
    )
    monkeypatch.setattr(
        audit,
        "_current_repo_hygiene_report",
        lambda: json.loads(report_contents["repo_hygiene"]),
    )
    manifest = {
        "schema": "flowscalper.full_suite_test_manifest.v1",
        "status": "PASS",
        "source_commit": commit,
        "collection_commands": audit.FULL_SUITE_COLLECTION_COMMANDS,
        "backend": [
            {
                "path": "backend/tests/test_full_suite_fixture.py",
                "sha256": hashlib.sha256(backend_source.read_bytes()).hexdigest(),
                "test_ids": backend_ids,
            }
        ],
        "frontend": [
            {
                "path": "frontend/tests/full-suite.test.ts",
                "sha256": hashlib.sha256(frontend_source.read_bytes()).hexdigest(),
                "test_ids": frontend_ids,
            }
        ],
    }
    artifacts.append(
        _artifact_record(
            tmp_path,
            "evidence/reports/full-suite-manifest.json",
            kind="artifact",
            content=json.dumps(manifest).encode(),
            format="full_suite_test_manifest_json",
        )
    )
    artifacts.extend(
        [
            _artifact_record(
                tmp_path,
                "evidence/reports/pytest-collection.txt",
                kind="log",
                content=(
                    b"backend/tests/test_full_suite_fixture.py::test_one\n"
                    b"backend/tests/test_full_suite_fixture.py::test_two\n\n"
                    b"2 tests collected in 0.01s\n"
                ),
                format="pytest_collection_text",
            ),
            _artifact_record(
                tmp_path,
                "evidence/reports/vitest-collection.txt",
                kind="log",
                content=(b"tests/full-suite.test.ts > one\ntests/full-suite.test.ts > two\n"),
                format="vitest_collection_text",
            ),
        ]
    )
    relative_path = "evidence/full-suite.json"
    path = tmp_path / relative_path
    wrapper = {
        "schema_version": 1,
        "generated_ts_utc": "2026-08-31T12:00:00Z",
        "source_commit": commit,
        "source_commit_at_end": commit,
        "source_worktree_clean_at_start": True,
        "source_worktree_clean_at_end": True,
        "source_worktree_clean_at_measurement": True,
        "status": "PASS",
        "checks": {name: True for name in audit.FULL_SUITE_REQUIRED_CHECKS},
        "commands": commands,
        "counts": {
            "command_count": len(commands),
            "backend_test_count": 2,
            "frontend_test_count": 2,
        },
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    path.write_text(json.dumps(wrapper), encoding="utf-8")

    result = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="full_suite_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )

    assert result["status"] == "PASS", result

    monkeypatch.setattr(
        audit,
        "_current_full_suite_collection_ids",
        lambda: {
            "backend": {"backend/tests/test_full_suite_fixture.py::test_real"},
            "frontend": {"frontend/tests/full-suite.test.ts::real"},
        },
    )
    fabricated_ids = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="full_suite_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert fabricated_ids["status"] == "NOT_PROVEN"
    assert fabricated_ids["reason"] == "FULL_SUITE_COLLECTION_DIFFERS_FROM_CURRENT_SOURCE"
    monkeypatch.setattr(
        audit,
        "_current_full_suite_collection_ids",
        lambda: trusted_collection_ids,
    )

    selected_commands = [dict(command) for command in commands]
    selected_command = selected_commands[0]["command"]
    assert isinstance(selected_command, list)
    selected_commands[0]["command"] = [*selected_command, "trivial.py"]
    wrapper["commands"] = selected_commands
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    selected = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="full_suite_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert selected["status"] == "NOT_PROVEN"
    assert selected["reason"] == "FULL_SUITE_COMMAND_NAME_EXIT_OR_LOG_INVALID"

    wrapper["commands"] = [commands[1], commands[0], *commands[2:]]
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    reordered = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="full_suite_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert reordered["status"] == "NOT_PROVEN"
    assert reordered["reason"] == "FULL_SUITE_REQUIRED_COMMAND_MISSING"

    wrapper["commands"] = commands
    unlisted = tmp_path / "backend/tests/test_unlisted.py"
    unlisted.write_text("def test_unlisted(): pass\n", encoding="utf-8")
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    partial = audit._load_evidence(
        relative_path,
        require_checks=True,
        evidence_kind="full_suite_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert partial["status"] == "NOT_PROVEN"
    assert partial["reason"] == "FULL_SUITE_COLLECTION_MANIFEST_FILE_SCOPE_MISMATCH"


def test_pytest_junit_counts_real_passes_and_rejects_all_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    relative_path = "evidence/reports/pytest.xml"
    passed = _artifact_record(
        tmp_path,
        relative_path,
        kind="artifact",
        content=(
            b'<testsuites name="pytest tests"><testsuite name="pytest" errors="0" '
            b'failures="0" skipped="0" tests="1"><testcase classname="test_audit" '
            b'name="test_pass" time="0.001" /></testsuite></testsuites>'
        ),
        format="pytest_junit_xml",
    )
    assert audit._full_suite_report_count("backend_pytest", passed) == 1

    skipped = _artifact_record(
        tmp_path,
        relative_path,
        kind="artifact",
        content=(
            b'<testsuites name="pytest tests"><testsuite name="pytest" errors="0" '
            b'failures="0" skipped="2" tests="2"><testcase name="one"><skipped />'
            b'</testcase><testcase name="two"><skipped /></testcase>'
            b"</testsuite></testsuites>"
        ),
        format="pytest_junit_xml",
    )
    assert audit._full_suite_report_count("backend_pytest", skipped) is None


def test_master_machine_reports_recompute_security_hygiene_and_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    (tmp_path / "VERSION").write_text("0.3.0-paper\n", encoding="utf-8")
    security = {
        "status": "PASS",
        "checked_source_files": 10,
        "forbidden_fragments": ["/fapi/v1/order"],
        "violations": [],
        "secret_like_files": [],
        "real_order_path": False,
    }
    monkeypatch.setattr(audit, "_current_security_scan_report", lambda: security)
    security_record = _artifact_record(
        tmp_path,
        "evidence/reports/security.json",
        kind="artifact",
        content=json.dumps(security).encode(),
        format="security_scan_json",
    )
    assert audit._full_suite_report_count("security_scan", security_record) == 10
    fabricated_security = dict(security) | {
        "checked_source_files": 1,
        "forbidden_fragments": ["totally_fabricated"],
    }
    fabricated_security_record = _artifact_record(
        tmp_path,
        "evidence/reports/security.json",
        kind="artifact",
        content=json.dumps(fabricated_security).encode(),
        format="security_scan_json",
    )
    assert audit._full_suite_report_count("security_scan", fabricated_security_record) is None

    hygiene = {"status": "PASS", "version": "0.3.0-paper", "violations": []}
    monkeypatch.setattr(audit, "_current_repo_hygiene_report", lambda: hygiene)
    hygiene_record = _artifact_record(
        tmp_path,
        "evidence/reports/hygiene.json",
        kind="artifact",
        content=json.dumps(hygiene).encode(),
        format="repo_hygiene_json",
    )
    assert audit._full_suite_report_count("repo_hygiene", hygiene_record) == 1
    monkeypatch.setattr(
        audit,
        "_current_repo_hygiene_report",
        lambda: dict(hygiene) | {"status": "FAIL", "violations": ["tracked artifact"]},
    )
    assert audit._full_suite_report_count("repo_hygiene", hygiene_record) is None

    network = {
        "status": "PASS",
        "venue": "BINANCE_USDM",
        "eligible_symbol_count": 100,
        "binance_catalog_count": 100,
        "upbit_krw_catalog_count": 100,
        "binance_btcusdt_3m_candle_count": 200,
        "binance_catalog_tail_3m_candle_count": 200,
        "upbit_krw_btc_3m_candle_count": 200,
        "websocket_events": 2,
        **_current_network_timing_evidence(),
        "lag_p50_ms": 1.5,
        "lag_p95_ms": 2.0,
        "credentials_sent": False,
        "authorization_header_sent": False,
        "auth_required": False,
        "real_orders_enabled": False,
    }
    network_record = _artifact_record(
        tmp_path,
        "evidence/reports/network.json",
        kind="artifact",
        content=json.dumps(network).encode(),
        format="network_smoke_json",
    )
    assert audit._full_suite_report_count("network_smoke", network_record) is not None
    network_without_samples = dict(network)
    network_without_samples.pop("event_samples")
    missing_samples_record = _artifact_record(
        tmp_path,
        "evidence/reports/network.json",
        kind="artifact",
        content=json.dumps(network_without_samples).encode(),
        format="network_smoke_json",
    )
    assert audit._full_suite_report_count("network_smoke", missing_samples_record) is None

    for tampered_network in (
        dict(network)
        | {
            "event_samples": [
                {
                    "stream": "binance-public-depth",
                    "source_ts_ms": 1_000.0,
                    "received_ts_ms": 1_001.0,
                },
                network["event_samples"][1],
            ]
        },
        dict(network)
        | {**_current_network_timing_evidence(completed_offset=timedelta(minutes=11))},
    ):
        tampered_record = _artifact_record(
            tmp_path,
            "evidence/reports/network.json",
            kind="artifact",
            content=json.dumps(tampered_network).encode(),
            format="network_smoke_json",
        )
        assert audit._full_suite_report_count("network_smoke", tampered_record) is None


def test_full_suite_producer_runs_canonical_commands_and_passes_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "d" * 40
    backend_source = tmp_path / "backend/tests/test_full_suite_fixture.py"
    frontend_source = tmp_path / "frontend/tests/full-suite.test.ts"
    backend_source.parent.mkdir(parents=True)
    frontend_source.parent.mkdir(parents=True)
    backend_source.write_text("def test_one(): pass\n", encoding="utf-8")
    frontend_source.write_text(
        "import { test } from 'vitest'\ntest('one', () => {})\n",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("uv-lock\n", encoding="utf-8")
    (tmp_path / "frontend/pnpm-lock.yaml").write_text("pnpm-lock\n", encoding="utf-8")
    python_executable = tmp_path / ".venv/bin/python"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text("#!/bin/sh\n", encoding="utf-8")
    python_executable.chmod(0o755)
    node_modules_marker = tmp_path / "frontend/node_modules/.modules.yaml"
    node_modules_marker.parent.mkdir(parents=True)
    node_modules_marker.write_text("modules: {}\n", encoding="utf-8")
    (tmp_path / "VERSION").write_text("0.3.0-paper\n", encoding="utf-8")
    backend_id = "backend/tests/test_full_suite_fixture.py::test_one"
    frontend_id = "frontend/tests/full-suite.test.ts::one"
    e2e_case_ids = {
        f"frontend/e2e/dashboard.spec.ts::fixture dashboard::{project}"
        for project in ("desktop", "tablet", "mobile")
    }

    def completed(command: list[str], stdout: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    def execute(
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if command == audit.FULL_SUITE_COLLECTION_COMMANDS["backend"]:
            return completed(command, f"{backend_id}\n\n1 test collected in 0.01s\n")
        if command == audit.FULL_SUITE_COLLECTION_COMMANDS["frontend"]:
            return completed(command, "tests/full-suite.test.ts > one\n")
        joined = " ".join(command)
        report_argument = next(
            (
                argument.split("=", 1)[1]
                for argument in command
                if argument.startswith(("--junitxml=", "--outputFile="))
            ),
            None,
        )
        if report_argument is None and "--output-file" in command:
            report_argument = command[command.index("--output-file") + 1]
        if report_argument is not None:
            report_path = tmp_path / report_argument
            report_path.parent.mkdir(parents=True, exist_ok=True)
            if "pytest" in command:
                report_path.write_text(
                    '<testsuites name="pytest tests"><testsuite tests="1" '
                    'failures="0" errors="0" skipped="0"><testcase '
                    'classname="backend.tests.test_full_suite_fixture" '
                    'name="test_one"/></testsuite></testsuites>',
                    encoding="utf-8",
                )
            elif "ruff" in command:
                report_path.write_text("[]", encoding="utf-8")
            elif "vitest" in command:
                report_path.write_text(
                    json.dumps(
                        {
                            "numTotalTests": 1,
                            "numPassedTests": 1,
                            "numFailedTests": 0,
                            "success": True,
                            "testResults": [
                                {
                                    "name": str(frontend_source),
                                    "assertionResults": [
                                        {
                                            "fullName": "one",
                                            "status": "passed",
                                            "failureMessages": [],
                                        }
                                    ],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
            elif "eslint" in command:
                report_path.write_text(
                    json.dumps(
                        [
                            {
                                "filePath": str(frontend_source),
                                "messages": [],
                                "errorCount": 0,
                                "fatalErrorCount": 0,
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
            return completed(command)
        if "mypy" in command:
            return completed(command, "Success: no issues found in 1 source file\n")
        if "tsc" in command:
            return completed(command, f"{frontend_source}\n")
        if "vite build" in joined:
            return completed(command, "✓ 1 modules transformed.\n✓ built in 1.00s\n")
        if "assert_build_safety.py" in joined:
            return completed(command, "PASS: PAPER 전용 빌드 불변조건\n")
        if command == ["make", "setup"]:
            return completed(command)
        if command == ["make", "e2e"]:
            assert env is not None
            assert env["ROBOM_E2E_CAPTURE"] == "0"
            junit_path = Path(env["PYTEST_ADDOPTS"].split("--junitxml=", 1)[1])
            playwright_path = Path(env["ROBOM_E2E_JSON_REPORT"])
            junit_path.parent.mkdir(parents=True, exist_ok=True)
            junit_path.write_text(
                '<testsuites><testsuite tests="1" failures="0" errors="0" '
                'skipped="0"><testcase '
                'classname="backend.tests.test_full_suite_fixture" '
                'name="test_one"/></testsuite></testsuites>',
                encoding="utf-8",
            )
            playwright_path.write_text(
                json.dumps(
                    {
                        "config": {
                            "projects": [
                                {"name": project} for project in ("desktop", "tablet", "mobile")
                            ]
                        },
                        "errors": [],
                        "stats": {
                            "expected": 3,
                            "skipped": 0,
                            "unexpected": 0,
                            "flaky": 0,
                        },
                        "suites": [
                            {
                                "title": "dashboard.spec.ts",
                                "file": "dashboard.spec.ts",
                                "specs": [
                                    {
                                        "title": "fixture dashboard",
                                        "file": "dashboard.spec.ts",
                                        "tests": [
                                            {
                                                "projectName": project,
                                                "results": [
                                                    {
                                                        "status": "passed",
                                                        "errors": [],
                                                    }
                                                ],
                                            }
                                            for project in (
                                                "desktop",
                                                "tablet",
                                                "mobile",
                                            )
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return completed(command)
        if command == ["make", "network-smoke"]:
            return completed(
                command,
                json.dumps(
                    {
                        "status": "PASS",
                        "venue": "BINANCE_USDM",
                        "eligible_symbol_count": 100,
                        "binance_catalog_count": 100,
                        "upbit_krw_catalog_count": 100,
                        "binance_btcusdt_3m_candle_count": 200,
                        "binance_catalog_tail_3m_candle_count": 200,
                        "upbit_krw_btc_3m_candle_count": 200,
                        "websocket_events": 2,
                        **_current_network_timing_evidence(),
                        "lag_p50_ms": 1.5,
                        "lag_p95_ms": 2.0,
                        "credentials_sent": False,
                        "authorization_header_sent": False,
                        "auth_required": False,
                        "real_orders_enabled": False,
                    }
                ),
            )
        if command == ["make", "security-scan"]:
            return completed(
                command,
                json.dumps(
                    {
                        "status": "PASS",
                        "checked_source_files": 10,
                        "forbidden_fragments": ["/fapi/v1/order"],
                        "violations": [],
                        "secret_like_files": [],
                        "real_order_path": False,
                    }
                ),
            )
        if command == ["make", "repo-hygiene"]:
            return completed(
                command,
                json.dumps(
                    {
                        "status": "PASS",
                        "version": "0.3.0-paper",
                        "violations": [],
                    }
                ),
            )
        raise AssertionError(command)

    output = tmp_path / audit.EVIDENCE_PATHS["full_suite_after_latest_change"]
    monkeypatch.setattr(final_validation, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(final_validation, "_source_revision", lambda: (commit, True))
    monkeypatch.setattr(
        final_validation,
        "_require_validated_thirty_minute_soak",
        lambda _commit: None,
    )
    monkeypatch.setattr(final_validation, "_execute", execute)
    monkeypatch.setattr(
        audit,
        "_current_fixture_e2e_test_ids",
        lambda: {backend_id},
    )
    monkeypatch.setattr(
        audit,
        "_current_master_e2e_case_ids",
        lambda: e2e_case_ids,
    )
    monkeypatch.setattr(
        audit,
        "_current_security_scan_report",
        lambda: json.loads(execute(["make", "security-scan"]).stdout),
    )
    monkeypatch.setattr(
        audit,
        "_current_repo_hygiene_report",
        lambda: json.loads(execute(["make", "repo-hygiene"]).stdout),
    )
    wrapper = final_validation.generate_evidence(output)

    assert wrapper["status"] == "PASS"
    assert wrapper["artifact_count"] == 18
    assert [command["name"] for command in wrapper["commands"]] == list(
        audit.FULL_SUITE_COMMAND_ORDER
    )
    monkeypatch.setattr(audit, "_commits_have_equivalent_source", lambda _a, _b: True)
    monkeypatch.setattr(
        audit,
        "_current_full_suite_collection_ids",
        lambda: {"backend": {backend_id}, "frontend": {frontend_id}},
    )
    result = audit._load_evidence(
        audit.EVIDENCE_PATHS["full_suite_after_latest_change"],
        require_checks=True,
        evidence_kind="full_suite_after_latest_change",
        expected_schema_version=1,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert result["status"] == "PASS", result


def test_full_suite_producer_writes_nothing_before_validated_soak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "d" * 40
    output = tmp_path / "evidence/full-suite.json"
    monkeypatch.setattr(final_validation, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(final_validation, "_source_revision", lambda: (commit, True))

    def reject_soak(_commit: str) -> None:
        raise RuntimeError("30 minute soak missing")

    monkeypatch.setattr(
        final_validation,
        "_require_validated_thirty_minute_soak",
        reject_soak,
    )
    with pytest.raises(RuntimeError, match="30 minute soak missing"):
        final_validation.generate_evidence(output)
    assert not output.exists()
    assert not output.parent.exists()


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
        f"{strategy_id}:{profile}" for strategy_id in strategy_ids for profile in ("BASE", "STRESS")
    ]
    mode_counts = {"ACTIVE": 0, "SHADOW": 6, "OFF": 9}

    def write_soak(
        measured_strategy_ids: list[str],
        measured_account_ids: list[str],
        measured_mode_counts: dict[str, int],
        *,
        include_artifact: bool,
        run_id: str | None = "run-v6-soak",
        unsafe_samples: bool = False,
    ) -> None:
        modes = [
            mode for mode in ("ACTIVE", "SHADOW", "OFF") for _ in range(measured_mode_counts[mode])
        ]
        assert len(modes) == len(measured_strategy_ids)
        strategy_states = tuple(
            StrategyState(
                strategy_id=strategy_id,
                mode=mode,
                lifecycle="MONITORED",
                settings_revision=1,
                manual_lock=False,
                changed_by="INITIAL_CONFIG",
                change_reason="V6_SCOPE",
            )
            for strategy_id, mode in zip(measured_strategy_ids, modes, strict=True)
        )

        def sample(index: int, elapsed: float, observed_at: str) -> RunningServiceSample:
            values: dict[str, Any] = {}
            for field in fields(RunningServiceSample):
                annotation = str(field.type)
                values[field.name] = (
                    False
                    if annotation == "bool"
                    else 0
                    if annotation == "int"
                    else 0.0
                    if annotation == "float"
                    else None
                    if annotation == "str | None"
                    else "NONE"
                )
            values.update(
                {
                    "elapsed_seconds": elapsed,
                    "observed_at": observed_at,
                    "run_id": run_id or "run-v6-soak",
                    "operation_state": "RUNNING",
                    "market_observation_active": True,
                    "paper_entry_active": True,
                    "market_data_state": "LIVE",
                    "execution_state": "PAPER",
                    "event_count": 100 + index,
                    "strategy_evaluation_count": 1_000 + index,
                    "qualified_signal_count": index,
                    "queue_capacity": 4_096,
                    "supervisor_running": True,
                    "consumer_running": True,
                    "consumer_delivery_count": 100 + index,
                    "persistence_flush_count": 10 + index,
                    "wal_checkpoint_count": 2 + index,
                    "wal_checkpoint_log_frames": 10 + index,
                    "wal_checkpointed_frames": 10 + index,
                    "storage_entry_allowed": True,
                    "process_memory_mb": 100.0,
                    "process_memory_peak_mb": 100.0,
                    "process_uptime_seconds": 100.0 + elapsed,
                    "strategy_count": len(measured_strategy_ids),
                    "league_account_count": len(measured_account_ids),
                    "independent_account_shape_valid": True,
                    "strategy_states": strategy_states,
                    "persistence_last_error": "NONE",
                    "wal_checkpoint_last_error": "NONE",
                }
            )
            return RunningServiceSample(**values)

        samples = [
            sample(index, elapsed, observed_at)
            for index, (elapsed, observed_at) in enumerate(
                (
                    (0.0, "2026-08-31T12:00:00Z"),
                    (900.0, "2026-08-31T12:15:00Z"),
                    (1800.0, "2026-08-31T12:30:00Z"),
                )
            )
        ]
        thresholds = RunningServiceSoakThresholds()
        summary = summarize_running_service_soak(
            samples,
            requested_duration_seconds=1_800,
            thresholds=thresholds,
            probe_error_count=0,
            maximum_consecutive_probe_errors=0,
            max_consecutive_probe_errors=3,
        )
        assert summary["status"] == "PASS"
        raw_samples = [
            asdict(observed_sample)
            | {
                "main_pending_entry_count": 0,
                "league_pending_entry_count": 0,
                "total_pending_entry_count": 0,
                "total_open_position_count": 0,
                "paper_portfolio_flat": True,
                "league_account_ids": measured_account_ids,
                "release_commit": commit,
                "release_isolated": True,
            }
            for observed_sample in samples
        ]
        if run_id is None:
            for raw_sample in raw_samples:
                raw_sample["run_id"] = None
        if unsafe_samples:
            for raw_sample in raw_samples:
                raw_sample.update(
                    {
                        "consumer_running": False,
                        "event_count": 100,
                        "position_count": 1,
                        "protected_position_count": 0,
                        "total_open_position_count": 1,
                        "paper_portfolio_flat": False,
                    }
                )
        summary_checks = summary["checks"]
        assert isinstance(summary_checks, dict)
        checks = dict(summary_checks) | {name: True for name in audit.SOAK_PROVENANCE_CHECKS}
        measurement: dict[str, Any] = dict(summary) | {
            "schema_version": 1,
            "generated_ts_utc": "2026-08-31T12:30:00Z",
            "started_at": "2026-08-31T12:00:00+00:00",
            "completed_at": "2026-08-31T12:30:00+00:00",
            "wall_duration_seconds": 1_800.0,
            "source_commit": commit,
            "source_commit_at_end": commit,
            "source_worktree_clean_at_start": True,
            "source_worktree_clean_at_end": True,
            "source_worktree_clean_at_measurement": True,
            "release_commit": commit,
            "release_commits_observed": [commit],
            "release_isolated_throughout": True,
            "run_id": run_id,
            "status": "PASS",
            "sample_seconds": 900.0,
            "max_consecutive_probe_errors": 3,
            "samples": raw_samples,
            "checks": checks,
            "strategy_ids": measured_strategy_ids,
            "league_account_ids": measured_account_ids,
            "strategy_mode_counts": measured_mode_counts,
            "baseline": raw_samples[0],
            "final": raw_samples[-1],
        }
        wrapper = dict(measurement)
        if include_artifact:
            artifact = _artifact_record(
                tmp_path,
                "evidence/artifacts/running-service-soak.json",
                kind="artifact",
                content=json.dumps(measurement).encode(),
                format="running_service_soak_json",
            )
            wrapper.update(
                {
                    "command": [
                        "uv",
                        "run",
                        "python",
                        "scripts/observe_running_service.py",
                    ],
                    "exit_code": 0,
                    "artifact_count": 1,
                    "artifacts": [artifact],
                }
            )
        path.write_text(json.dumps(wrapper), encoding="utf-8")

    write_soak(
        strategy_ids[:11],
        account_ids[:22],
        {"ACTIVE": 0, "SHADOW": 6, "OFF": 5},
        include_artifact=False,
    )
    unbacked = audit._thirty_minute_soak_evidence(
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=mode_counts,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert unbacked["status"] == "NOT_PROVEN"
    assert unbacked["reason"] == "SOAK_COMMAND_OR_EXIT_CODE_NOT_PROVEN"

    write_soak(
        strategy_ids[:11],
        account_ids[:22],
        {"ACTIVE": 0, "SHADOW": 6, "OFF": 5},
        include_artifact=True,
    )
    stale = audit._thirty_minute_soak_evidence(
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=mode_counts,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert stale["status"] == "NOT_PROVEN"
    assert stale["reason"] == "SOAK_V6_STRATEGY_ACCOUNT_OR_MODE_SCOPE_MISMATCH"

    write_soak(
        strategy_ids,
        account_ids,
        mode_counts,
        include_artifact=True,
    )
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
        "generated_ts_utc": "2026-08-31T12:30:00Z",
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

    wrapper = json.loads(path.read_text(encoding="utf-8"))
    measurement_path = tmp_path / "evidence/artifacts/running-service-soak.json"
    future_measurement = json.loads(measurement_path.read_text(encoding="utf-8"))
    future_measurement["generated_ts_utc"] = "2099-08-31T12:30:00Z"
    future_measurement["completed_at"] = "2099-08-31T12:30:00+00:00"
    future_measurement["started_at"] = "2099-08-31T12:00:00+00:00"
    future_artifact = _artifact_record(
        tmp_path,
        "evidence/artifacts/running-service-soak.json",
        kind="artifact",
        content=json.dumps(future_measurement).encode(),
        format="running_service_soak_json",
    )
    wrapper["artifacts"] = [future_artifact]
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    future = audit._thirty_minute_soak_evidence(
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=mode_counts,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert future["status"] == "NOT_PROVEN"
    assert future["reason"] == "SOAK_MEASUREMENT_TIME_BINDING_INVALID"

    write_soak(
        strategy_ids,
        account_ids,
        mode_counts,
        include_artifact=True,
        unsafe_samples=True,
    )
    self_attested_unsafe = audit._thirty_minute_soak_evidence(
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=mode_counts,
        expected_source_commit=commit,
        source_working_tree_changes=[],
    )
    assert self_attested_unsafe["status"] == "FAIL"
    assert self_attested_unsafe["reason"] == "SOAK_DECLARED_CHECKS_DIFFER_FROM_RECOMPUTED_SAMPLES"

    write_soak(
        strategy_ids,
        account_ids,
        mode_counts,
        include_artifact=True,
        run_id=None,
    )
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


def test_runtime_pass_requires_exact_isolated_v6_scope_and_zero_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "c" * 40
    docs_only_commit = "d" * 40
    source_changing_commit = "e" * 40
    monkeypatch.setattr(
        audit,
        "_commits_have_equivalent_source",
        lambda left, right: left == commit and right in {commit, docs_only_commit},
    )
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

    docs_only_result = audit._runtime_contract_evidence(
        observation,
        latest_commit=docs_only_commit,
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=expected_modes,
    )
    assert docs_only_result["status"] == "PASS"

    source_changed_result = audit._runtime_contract_evidence(
        observation,
        latest_commit=source_changing_commit,
        expected_strategy_ids=strategy_ids,
        expected_account_ids=account_ids,
        expected_mode_counts=expected_modes,
    )
    assert source_changed_result["status"] == "NOT_PROVEN"

    old_release = dict(observation) | {"release_commit": "f" * 40}
    assert (
        audit._runtime_contract_evidence(
            old_release,
            latest_commit=commit,
            expected_strategy_ids=strategy_ids,
            expected_account_ids=account_ids,
            expected_mode_counts=expected_modes,
        )["status"]
        == "NOT_PROVEN"
    )

    unsafe_safety = dict(observation) | {
        "runtime_safety": dict(observation["runtime_safety"]) | {"actual_orders_zero": False},
        "runtime_safety_observed": dict(observation["runtime_safety_observed"])
        | {"real_orders_enabled": True},
    }
    assert (
        audit._runtime_contract_evidence(
            unsafe_safety,
            latest_commit=commit,
            expected_strategy_ids=strategy_ids,
            expected_account_ids=account_ids,
            expected_mode_counts=expected_modes,
        )["status"]
        == "FAIL"
    )

    active = dict(observation) | {"strategy_mode_counts": {"ACTIVE": 1, "SHADOW": 5, "OFF": 9}}
    assert (
        audit._runtime_contract_evidence(
            active,
            latest_commit=commit,
            expected_strategy_ids=strategy_ids,
            expected_account_ids=account_ids,
            expected_mode_counts={"ACTIVE": 1, "SHADOW": 5, "OFF": 9},
        )["status"]
        == "NOT_PROVEN"
    )


def _write_exact_launch_agent_plist(runtime_root: Path, plist_path: Path) -> None:
    runner = runtime_root / "support/run_macos_service.sh"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/zsh\n", encoding="utf-8")
    plist_path.parent.mkdir(parents=True)
    plist_path.write_bytes(
        plistlib.dumps(
            {
                "Label": audit.LAUNCH_AGENT_LABEL,
                "ProgramArguments": ["/bin/zsh", str(runner)],
                "RunAtLoad": True,
                "KeepAlive": True,
                "ThrottleInterval": 10,
                "ExitTimeOut": 60,
                "ProcessType": "Background",
                "StandardOutPath": str(runtime_root / "logs/service.log"),
                "StandardErrorPath": str(runtime_root / "logs/service-error.log"),
            }
        )
    )


def test_launch_agent_evidence_requires_exact_plist_and_running_process_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    plist_path = tmp_path / "LaunchAgents/kr.robom.flowscalper.plist"
    _write_exact_launch_agent_plist(runtime_root, plist_path)
    commit = "a" * 40
    observed_calls: list[tuple[Path, Path]] = []

    def verify_process(*, ledger_path: Path, release_path: Path) -> dict[str, object]:
        observed_calls.append((ledger_path, release_path))
        return {
            "launch_agent_label": audit.LAUNCH_AGENT_LABEL,
            "service_pid": 123,
            "listener": "127.0.0.1:8870",
            "cwd": str(release_path),
            "ledger_open_by_service_pid": True,
        }

    monkeypatch.setattr(audit, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(audit, "LAUNCH_AGENT_PLIST_PATH", plist_path)
    monkeypatch.setattr(audit, "verify_running_process_binding", verify_process)
    result = audit._launch_agent_evidence(
        {"release_commit": commit},
        runtime_observation={
            "available": True,
            "release_commit": commit,
            "release_isolated": True,
        },
    )

    assert result["status"] == "PASS"
    assert result["evidence_boundary"] == audit.LAUNCH_AGENT_EVIDENCE_BOUNDARY
    assert result["plist_contract"]["status"] == "PASS"
    assert result["process_binding"]["status"] == "PASS"
    assert observed_calls == [
        (
            runtime_root / "active-ledger/run-ledger.sqlite3",
            runtime_root / "releases" / commit,
        )
    ]

    payload = plistlib.loads(plist_path.read_bytes())
    payload["UnexpectedKey"] = True
    plist_path.write_bytes(plistlib.dumps(payload))
    mismatch = audit._launch_agent_evidence(
        {"release_commit": commit},
        runtime_observation={
            "available": True,
            "release_commit": commit,
            "release_isolated": True,
        },
    )
    assert mismatch["status"] == "FAIL"
    assert mismatch["reason"] == "LAUNCH_AGENT_PLIST_EXACT_CONTRACT_MISMATCH"
    assert len(observed_calls) == 1


def test_launch_agent_evidence_separates_stopped_and_failed_process_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    plist_path = tmp_path / "LaunchAgents/kr.robom.flowscalper.plist"
    _write_exact_launch_agent_plist(runtime_root, plist_path)
    commit = "b" * 40
    monkeypatch.setattr(audit, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(audit, "LAUNCH_AGENT_PLIST_PATH", plist_path)

    def unexpected_process_probe(**_kwargs: object) -> dict[str, object]:
        pytest.fail("stopped runtime은 process binding을 PASS로 관측하지 않아야 한다.")

    monkeypatch.setattr(audit, "verify_running_process_binding", unexpected_process_probe)
    stopped = audit._launch_agent_evidence(
        {"release_commit": commit},
        runtime_observation={"available": False},
    )
    assert stopped["status"] == "NOT_RUN"
    assert stopped["reason"] == "LAUNCH_AGENT_PROCESS_NOT_OBSERVED"
    assert stopped["plist_contract"]["status"] == "PASS"
    assert stopped["process_binding"]["status"] == "NOT_RUN"

    def failed_process_probe(**_kwargs: object) -> dict[str, object]:
        raise LegacyRuntimePreflightError("PID mismatch")

    monkeypatch.setattr(audit, "verify_running_process_binding", failed_process_probe)
    failed = audit._launch_agent_evidence(
        {"release_commit": commit},
        runtime_observation={
            "available": True,
            "release_commit": commit,
            "release_isolated": True,
        },
    )
    assert failed["status"] == "FAIL"
    assert failed["reason"] == "LAUNCH_AGENT_RUNNING_PROCESS_BINDING_FAILED"
    assert failed["process_binding"]["status"] == "FAIL"


def test_installed_release_allows_evidence_only_followup_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_commit = "a" * 40
    docs_only_commit = "b" * 40
    source_changing_commit = "c" * 40
    monkeypatch.setattr(
        audit,
        "_commits_have_equivalent_source",
        lambda left, right: left == release_commit and right in {release_commit, docs_only_commit},
    )
    deployment = {
        "status": "PASS",
        "release_commit": release_commit,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
    }
    release_package = {"status": "PASS", "reason": "VERIFIED"}
    launch_agent = {"status": "PASS", "reason": "VERIFIED"}

    docs_only = audit._installed_release_evidence(
        deployment,
        latest_commit=docs_only_commit,
        working_tree_changes=[],
        release_package_evidence=release_package,
        launch_agent_evidence=launch_agent,
    )
    assert docs_only["status"] == "PASS"
    assert docs_only["evidence_boundary"] == audit.INSTALLED_RELEASE_EVIDENCE_BOUNDARY

    source_changed = audit._installed_release_evidence(
        deployment,
        latest_commit=source_changing_commit,
        working_tree_changes=[],
        release_package_evidence=release_package,
        launch_agent_evidence=launch_agent,
    )
    assert source_changed == {
        "status": "NOT_PROVEN",
        "reason": "INSTALLED_COMMIT_DIFFERS_FROM_HEAD",
        "evidence_boundary": audit.INSTALLED_RELEASE_EVIDENCE_BOUNDARY,
    }

    invalid = audit._installed_release_evidence(
        dict(deployment) | {"release_commit": "not-a-commit"},
        latest_commit=docs_only_commit,
        working_tree_changes=[],
        release_package_evidence=release_package,
        launch_agent_evidence=launch_agent,
    )
    assert invalid["status"] == "NOT_PROVEN"

    launch_agent_failed = audit._installed_release_evidence(
        deployment,
        latest_commit=docs_only_commit,
        working_tree_changes=[],
        release_package_evidence=release_package,
        launch_agent_evidence={"status": "FAIL", "reason": "PLIST_MISMATCH"},
    )
    assert launch_agent_failed == {
        "status": "FAIL",
        "reason": "INSTALLED_PLIST_MISMATCH",
        "evidence_boundary": audit.INSTALLED_RELEASE_EVIDENCE_BOUNDARY,
    }


def test_stopped_runtime_cannot_produce_overall_pass() -> None:
    evidence_statuses = {
        "dashboard_payload_benchmark": "PASS",
        "browser_e2e_after_latest_change": "PASS",
        "full_suite_after_latest_change": "PASS",
        "thirty_minute_soak": "PASS",
        "release_package": "PASS",
        "launch_agent": "PASS",
        "installed_release": "PASS",
        "remote_push": "PASS",
    }
    assert (
        audit._overall_report_status(
            source_status="PASS",
            evidence_statuses=evidence_statuses,
            runtime_status="NOT_RUN",
        )
        == "NOT_RUN_STOPPED_OR_UNREACHABLE_RUNTIME"
    )


def test_launch_agent_is_independently_required_for_overall_pass() -> None:
    evidence_statuses = {
        "dashboard_payload_benchmark": "PASS",
        "browser_e2e_after_latest_change": "PASS",
        "full_suite_after_latest_change": "PASS",
        "thirty_minute_soak": "PASS",
        "release_package": "PASS",
        "launch_agent": "NOT_RUN",
        "installed_release": "PASS",
        "remote_push": "PASS",
    }

    assert (
        audit._overall_report_status(
            source_status="PASS",
            evidence_statuses=evidence_statuses,
            runtime_status="PASS",
        )
        == "NOT_RUN_REQUIRED_EVIDENCE"
    )


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
    support = tmp_path / "support"
    support.mkdir()
    launcher = support / "run_macos_service.sh"
    source_launcher = release / "scripts/run_macos_service.sh"
    source_launcher.parent.mkdir()
    source_launcher.write_bytes((audit.PROJECT_ROOT / "scripts/run_macos_service.sh").read_bytes())
    launcher.write_bytes(source_launcher.read_bytes())
    manifest_path = release / "release-manifest.json"
    anchor_path = support / "current-release-integrity.json"
    anchor_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "release_path": str(release),
                "release_commit": commit,
                "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                "launcher_path": str(launcher),
                "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
                "launcher_source_release_path": str(release),
                "launcher_source_commit": commit,
                "launcher_source_manifest_sha256": hashlib.sha256(
                    manifest_path.read_bytes()
                ).hexdigest(),
                "paper_only": True,
                "real_orders_enabled": False,
            }
        ),
        encoding="utf-8",
    )
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
    docs_only_commit = "b" * 40
    source_changing_commit = "c" * 40
    monkeypatch.setattr(
        audit,
        "_commits_have_equivalent_source",
        lambda left, right: left == commit and right in {commit, docs_only_commit},
    )
    result = audit._release_package_evidence(
        {"release_commit": commit},
        latest_commit=commit,
        working_tree_changes=[],
    )
    assert result["status"] == "PASS"
    assert result["reason"] == "V2_SAFE_RELEASE_TREE_AND_EXTERNAL_ANCHOR_MATCH_CLEAN_HEAD"
    assert verified == [(release, commit), (release, commit)]

    docs_only = audit._release_package_evidence(
        {"release_commit": commit},
        latest_commit=docs_only_commit,
        working_tree_changes=[],
    )
    assert docs_only["status"] == "PASS"

    source_changed = audit._release_package_evidence(
        {"release_commit": commit},
        latest_commit=source_changing_commit,
        working_tree_changes=[],
    )
    assert source_changed == {
        "status": "NOT_PROVEN",
        "path": f"releases/{commit}/release-manifest.json",
        "reason": "RELEASE_PACKAGE_DIFFERS_FROM_CURRENT_SOURCE",
    }

    (release / "backend.py").write_text("# tampered release\n", encoding="utf-8")
    resigned_manifest = dict(manifest) | {
        "files": {"backend.py": hashlib.sha256((release / "backend.py").read_bytes()).hexdigest()},
        "file_count": 1,
    }
    manifest_path.write_text(json.dumps(resigned_manifest), encoding="utf-8")

    tampered = audit._release_package_evidence(
        {"release_commit": commit},
        latest_commit=commit,
        working_tree_changes=[],
    )
    assert tampered == {
        "status": "FAIL",
        "path": f"releases/{commit}/release-manifest.json",
        "reason": "RELEASE_INTEGRITY_ANCHOR_MANIFEST_SHA_MISMATCH",
    }


def test_release_audit_accepts_separate_launcher_source_and_rejects_source_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_commit = "a" * 40
    source_commit = "b" * 40
    target = tmp_path / "releases" / target_commit
    source = tmp_path / "releases" / source_commit
    for release, commit in ((target, target_commit), (source, source_commit)):
        (release / "scripts").mkdir(parents=True)
        (release / "release-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "commit": commit,
                    "paper_only": True,
                    "real_orders_enabled": False,
                    "auth_required": False,
                    "private_api_enabled": False,
                    "wallet_paths_enabled": False,
                }
            ),
            encoding="utf-8",
        )
    target_runner = target / "scripts" / "run_macos_service.sh"
    source_runner = source / "scripts" / "run_macos_service.sh"
    target_runner.write_text("#!/bin/zsh\n# old target runner\n", encoding="utf-8")
    source_runner.write_text("#!/bin/zsh\n# hardened source runner\n", encoding="utf-8")
    (tmp_path / "current").symlink_to(Path("releases") / target_commit)
    support = tmp_path / "support"
    support.mkdir()
    launcher = support / "run_macos_service.sh"
    launcher.write_bytes(source_runner.read_bytes())
    target_manifest = target / "release-manifest.json"
    source_manifest = source / "release-manifest.json"
    (support / "current-release-integrity.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "release_path": str(target),
                "release_commit": target_commit,
                "manifest_sha256": hashlib.sha256(target_manifest.read_bytes()).hexdigest(),
                "launcher_path": str(launcher),
                "launcher_sha256": hashlib.sha256(launcher.read_bytes()).hexdigest(),
                "launcher_source_release_path": str(source),
                "launcher_source_commit": source_commit,
                "launcher_source_manifest_sha256": hashlib.sha256(
                    source_manifest.read_bytes()
                ).hexdigest(),
                "paper_only": True,
                "real_orders_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    verified: list[tuple[Path, str]] = []

    def verify_release_tree(path: Path, *, expected_commit: str) -> dict[str, Any]:
        verified.append((path, expected_commit))
        return {"commit": expected_commit}

    monkeypatch.setattr(audit, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(
        stage_macos_release,
        "_verify_release_tree",
        verify_release_tree,
    )
    monkeypatch.setattr(
        audit,
        "_commits_have_equivalent_source",
        lambda left, right: left == right == target_commit,
    )

    result = audit._release_package_evidence(
        {"release_commit": target_commit},
        latest_commit=target_commit,
        working_tree_changes=[],
    )
    assert result["status"] == "PASS"
    assert verified == [(target, target_commit), (source, source_commit)]
    assert (
        hashlib.sha256(target_runner.read_bytes()).hexdigest()
        != hashlib.sha256(source_runner.read_bytes()).hexdigest()
    )

    source_manifest.write_bytes(source_manifest.read_bytes() + b"\n")
    tampered = audit._release_package_evidence(
        {"release_commit": target_commit},
        latest_commit=target_commit,
        working_tree_changes=[],
    )
    assert tampered["status"] == "FAIL"
    assert tampered["reason"] == "RELEASE_INTEGRITY_ANCHOR_SOURCE_MANIFEST_SHA_MISMATCH"
