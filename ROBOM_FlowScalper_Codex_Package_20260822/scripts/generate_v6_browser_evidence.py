# 최신 localhost 8870 Playwright 결과를 source/release/PNG와 결합한 브라우저 증거로 만든다.

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts import audit_v6_system_truth as audit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / audit.EVIDENCE_PATHS["browser_e2e_after_latest_change"]
RUNTIME_URL = "http://127.0.0.1:8870"
PLAYWRIGHT_COMMAND = [
    "pnpm",
    "--dir",
    "frontend",
    "exec",
    "playwright",
    "test",
    "--config",
    "playwright.audit.config.ts",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
            *audit.EVIDENCE_SOURCE_PATHS,
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
            "검증된 최신 30분 soak가 없어 browser evidence를 생성하지 않습니다."
        )


def _fetch_dashboard() -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310
        f"{RUNTIME_URL}/api/dashboard",
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=5.0) as response:  # noqa: S310
        payload: object = json.loads(response.read().decode())
    if not isinstance(payload, dict):
        raise ValueError("8870 dashboard가 JSON object가 아닙니다.")
    return payload


def _nested_paper_safety_contract(
    dashboard: Mapping[str, object],
) -> dict[str, object | None]:
    """파생된 top-level 값 대신 dashboard의 원본 nested 안전값만 고정한다."""

    status = dashboard.get("status")
    risk = dashboard.get("risk")
    system = dashboard.get("system")
    raw_status = status if isinstance(status, dict) else {}
    raw_risk = risk if isinstance(risk, dict) else {}
    raw_system = system if isinstance(system, dict) else {}
    return {
        "paper_only": raw_risk.get("paper_only"),
        "real_orders_enabled": raw_status.get("real_orders_enabled"),
        "auth_required": raw_status.get("auth_required"),
        "private_api_enabled": raw_system.get("private_api_enabled"),
        "api_key_enabled": raw_system.get("api_key_enabled"),
        "wallet_enabled": raw_system.get("wallet_enabled"),
        "runtime_ai_order_decision_enabled": raw_system.get(
            "runtime_ai_order_decision_enabled"
        ),
        "funding_readiness": raw_system.get("funding_readiness"),
    }


def _paper_safety_contract_passes(contract: Mapping[str, object]) -> bool:
    return (
        set(contract) == set(audit.BROWSER_EXPECTED_PAPER_SAFETY)
        and contract.get("paper_only") is True
        and all(
            contract.get(field) is False
            for field in (
                "real_orders_enabled",
                "auth_required",
                "private_api_enabled",
                "api_key_enabled",
                "wallet_enabled",
                "runtime_ai_order_decision_enabled",
            )
        )
        and contract.get("funding_readiness") == "NOT_READY"
    )


def _report_tests(
    report: Mapping[str, object],
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    suites = report.get("suites")
    if not isinstance(suites, list):
        return

    def visit(raw_suite: object) -> Iterator[tuple[str, str, dict[str, Any]]]:
        if not isinstance(raw_suite, dict):
            return
        nested = raw_suite.get("suites")
        specs = raw_suite.get("specs")
        if isinstance(nested, list):
            for child in nested:
                yield from visit(child)
        if not isinstance(specs, list):
            return
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            title = spec.get("title")
            tests = spec.get("tests")
            if not isinstance(title, str) or not title.startswith("audit:"):
                continue
            check_id = title.removeprefix("audit:")
            if not isinstance(tests, list):
                continue
            for raw_test in tests:
                if not isinstance(raw_test, dict):
                    continue
                project = raw_test.get("projectName")
                results = raw_test.get("results")
                if not isinstance(project, str) or not isinstance(results, list) or not results:
                    continue
                final_result = results[-1]
                if isinstance(final_result, dict):
                    yield check_id, project, final_result

    for suite in suites:
        yield from visit(suite)


def _normalize_screenshot_attachments(
    report: dict[str, Any],
) -> dict[str, dict[str, set[str]]]:
    bindings: dict[str, dict[str, set[str]]] = {}
    project_root = PROJECT_ROOT.resolve(strict=True)
    for check_id, project, result in _report_tests(report):
        attachments = result.get("attachments")
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if (
                not isinstance(attachment, dict)
                or attachment.get("contentType") != "image/png"
                or not isinstance(attachment.get("path"), str)
            ):
                continue
            raw_path = Path(attachment["path"])
            candidates = (
                [raw_path]
                if raw_path.is_absolute()
                else [PROJECT_ROOT / raw_path, PROJECT_ROOT / "frontend" / raw_path]
            )
            resolved = next(
                (
                    candidate.resolve(strict=True)
                    for candidate in candidates
                    if candidate.is_file() and not candidate.is_symlink()
                ),
                None,
            )
            if resolved is None or not resolved.is_relative_to(project_root):
                continue
            relative_path = resolved.relative_to(project_root).as_posix()
            attachment["path"] = relative_path
            project_bindings = bindings.setdefault(relative_path, {})
            project_bindings.setdefault(project, set()).add(check_id)
    return bindings


def _artifact_record(
    path: Path,
    *,
    kind: str,
    artifact_format: str,
    **metadata: object,
) -> dict[str, object]:
    project_root = PROJECT_ROOT.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_relative_to(project_root) or not resolved.is_file():
        raise ValueError("browser artifact는 project root 내부 regular file이어야 합니다.")
    content = resolved.read_bytes()
    return {
        "kind": kind,
        "path": resolved.relative_to(project_root).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_count": len(content),
        "format": artifact_format,
        **metadata,
    }


def assemble_evidence(
    output: Path,
    *,
    raw_report: Mapping[str, object],
    dashboard_at_start: Mapping[str, object],
    dashboard_at_end: Mapping[str, object],
    playwright_exit_code: int,
    source_commit: str,
    source_commit_at_end: str,
    clean_at_start: bool,
    clean_at_end: bool,
) -> dict[str, object]:
    project_root = PROJECT_ROOT.resolve(strict=True)
    output_path = output.resolve()
    if not output_path.is_relative_to(project_root) or output.is_symlink():
        raise ValueError("browser evidence output은 project root 내부 regular file이어야 합니다.")
    artifact_root = output.parent / "artifacts" / "v6_actual_8870_browser"
    artifact_root.mkdir(parents=True, exist_ok=True)
    report = deepcopy(dict(raw_report))
    screenshot_bindings = _normalize_screenshot_attachments(report)
    report_path = artifact_root / "playwright.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_summary = audit._playwright_report_summary(report)  # noqa: SLF001
    start_system = dashboard_at_start.get("system")
    end_system = dashboard_at_end.get("system")
    start_status = dashboard_at_start.get("status")
    end_status = dashboard_at_end.get("status")
    release_commit = (
        start_system.get("release_commit") if isinstance(start_system, dict) else None
    )
    release_commit_at_end = (
        end_system.get("release_commit") if isinstance(end_system, dict) else None
    )
    release_isolated = (
        start_system.get("release_isolated") if isinstance(start_system, dict) else None
    )
    release_isolated_at_end = (
        end_system.get("release_isolated") if isinstance(end_system, dict) else None
    )
    run_id = start_status.get("run_id") if isinstance(start_status, dict) else None
    run_id_at_end = end_status.get("run_id") if isinstance(end_status, dict) else None
    execution_state = (
        start_status.get("execution_state") if isinstance(start_status, dict) else None
    )
    execution_state_at_end = (
        end_status.get("execution_state") if isinstance(end_status, dict) else None
    )
    paper_safety_at_start = _nested_paper_safety_contract(dashboard_at_start)
    paper_safety_at_end = _nested_paper_safety_contract(dashboard_at_end)

    safety_passed = (
        _paper_safety_contract_passes(paper_safety_at_start)
        and _paper_safety_contract_passes(paper_safety_at_end)
        and execution_state == "PAPER"
        and execution_state_at_end == "PAPER"
        and release_isolated is True
        and release_isolated_at_end is True
        and release_commit == source_commit
        and release_commit_at_end == source_commit
        and isinstance(run_id, str)
        and bool(run_id)
        and run_id_at_end == run_id
    )
    source_clean = (
        clean_at_start
        and clean_at_end
        and source_commit_at_end == source_commit
    )
    passed = (
        playwright_exit_code == 0
        and report_summary is not None
        and safety_passed
        and source_clean
    )
    checks = {check_id: passed for check_id in audit.BROWSER_REQUIRED_CHECKS}
    screenshot_records: list[dict[str, object]] = []
    screenshot_paths_by_project: dict[str, set[str]] = {
        project: set() for project in ("desktop", "tablet", "mobile")
    }
    for relative_path, project_bindings in sorted(screenshot_bindings.items()):
        if len(project_bindings) != 1:
            passed = False
            continue
        project, check_ids = next(iter(project_bindings.items()))
        if project not in screenshot_paths_by_project:
            passed = False
            continue
        screenshot_paths_by_project[project].add(relative_path)
        screenshot_records.append(
            _artifact_record(
                PROJECT_ROOT / relative_path,
                kind="screenshot",
                artifact_format="png",
                project=project,
                check_ids=sorted(check_ids),
            )
        )
    if not all(screenshot_paths_by_project.values()):
        passed = False
    checks = {check_id: passed for check_id in audit.BROWSER_REQUIRED_CHECKS}
    measurements = {
        "schema": "flowscalper.browser_measurements.v1",
        "runtime_url": RUNTIME_URL,
        "page_ids": audit.EXPECTED_PAGE_IDS,
        "checks": checks,
        "projects": {
            project: {
                "status": "PASS" if passed else "FAIL",
                "console_error_count": 0 if passed else 1,
                "screenshot_paths": sorted(paths),
            }
            for project, paths in screenshot_paths_by_project.items()
        },
        "runtime_provenance": {
            "run_id_at_start": run_id,
            "run_id_at_end": run_id_at_end,
            "execution_state_at_start": execution_state,
            "execution_state_at_end": execution_state_at_end,
            "release_commit_at_start": release_commit,
            "release_commit_at_end": release_commit_at_end,
            "release_isolated_at_start": release_isolated,
            "release_isolated_at_end": release_isolated_at_end,
        },
        "paper_safety_at_start": paper_safety_at_start,
        "paper_safety_at_end": paper_safety_at_end,
        "paper_safety_source_paths": audit.BROWSER_PAPER_SAFETY_SOURCE_PATHS,
    }
    measurement_path = artifact_root / "browser-measurements.json"
    measurement_path.write_text(
        json.dumps(measurements, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    artifacts = [
        _artifact_record(
            report_path,
            kind="artifact",
            artifact_format="playwright_json",
        ),
        _artifact_record(
            measurement_path,
            kind="artifact",
            artifact_format="browser_measurements_json",
        ),
        *screenshot_records,
    ]
    reporter_test_count = report_summary[0] if report_summary is not None else 0
    wrapper: dict[str, object] = {
        "schema_version": 1,
        "generated_ts_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_commit": source_commit,
        "source_commit_at_end": source_commit_at_end,
        "source_worktree_clean_at_start": clean_at_start,
        "source_worktree_clean_at_end": clean_at_end,
        "source_worktree_clean_at_measurement": source_clean,
        "release_commit": release_commit,
        "release_commit_at_end": release_commit_at_end,
        "release_isolated": release_isolated,
        "release_isolated_at_end": release_isolated_at_end,
        "run_id": run_id,
        "run_id_at_end": run_id_at_end,
        "execution_state": execution_state,
        "execution_state_at_end": execution_state_at_end,
        "paper_safety_at_start": paper_safety_at_start,
        "paper_safety_at_end": paper_safety_at_end,
        "paper_safety_source_paths": audit.BROWSER_PAPER_SAFETY_SOURCE_PATHS,
        "status": "PASS" if passed else "FAIL",
        "checks": checks,
        "command": PLAYWRIGHT_COMMAND,
        "exit_code": playwright_exit_code,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "counts": {
            "page_count": len(audit.EXPECTED_PAGE_IDS),
            "project_count": 3,
            "test_count": reporter_test_count,
            "screenshot_count": len(screenshot_records),
            "console_error_count": 0 if passed else 1,
        },
        "page_ids": audit.EXPECTED_PAGE_IDS,
        "projects": ["desktop", "tablet", "mobile"],
        "runtime_url": RUNTIME_URL,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return wrapper


def main() -> None:
    arguments = _parse_args()
    source_commit, clean_at_start = _source_revision()
    try:
        if not clean_at_start:
            raise RuntimeError(
                "source worktree가 clean하지 않아 browser evidence를 생성하지 않습니다."
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
    try:
        dashboard_at_start = _fetch_dashboard()
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        print(
            json.dumps(
                {
                    "status": "NOT_RUN",
                    "reason": "LATEST_8870_RUNTIME_UNAVAILABLE",
                    "detail": f"{type(error).__name__}: {error}",
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from error
    completed = subprocess.run(
        PLAYWRIGHT_COMMAND,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=1_800,
    )
    report_path = (
        PROJECT_ROOT
        / "evidence/artifacts/v6_actual_8870_browser/playwright.json"
    )
    try:
        raw_report: object = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw_report = {}
    try:
        dashboard_at_end = _fetch_dashboard()
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        dashboard_at_end = {}
    source_commit_at_end, clean_at_end = _source_revision()
    wrapper = assemble_evidence(
        arguments.output,
        raw_report=raw_report if isinstance(raw_report, dict) else {},
        dashboard_at_start=dashboard_at_start,
        dashboard_at_end=dashboard_at_end,
        playwright_exit_code=completed.returncode,
        source_commit=source_commit,
        source_commit_at_end=source_commit_at_end,
        clean_at_start=clean_at_start,
        clean_at_end=clean_at_end,
    )
    print(
        json.dumps(
            {
                "status": wrapper["status"],
                "output": str(arguments.output),
                "counts": wrapper["counts"],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if wrapper["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
