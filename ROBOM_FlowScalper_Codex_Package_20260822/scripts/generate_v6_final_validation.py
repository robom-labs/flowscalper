# V6 전체 회귀 명령과 실제 수집 목록을 하나의 검증 가능한 증거 묶음으로 생성한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from scripts import audit_v6_system_truth as audit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / audit.EVIDENCE_PATHS["full_suite_after_latest_change"]


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


def _execute(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=1_800,
    )


def _json_from_output(completed: subprocess.CompletedProcess[str]) -> object | None:
    combined = completed.stdout + completed.stderr
    decoder = json.JSONDecoder()
    for index, character in enumerate(combined):
        if character not in "[{":
            continue
        try:
            parsed: object
            parsed, _ = decoder.raw_decode(combined[index:])
        except json.JSONDecodeError:
            continue
        return parsed
    return None


def _setup_report(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return {
        "schema": "flowscalper.setup_validation.v1",
        "command": ["make", "setup"],
        "exit_code": completed.returncode,
        "lock_sha256": {
            relative_path: hashlib.sha256(
                (PROJECT_ROOT / relative_path).read_bytes()
            ).hexdigest()
            if (PROJECT_ROOT / relative_path).is_file()
            else None
            for relative_path in ("uv.lock", "frontend/pnpm-lock.yaml")
        },
        "python_executable_path": ".venv/bin/python",
        "node_modules_marker_path": "frontend/node_modules/.modules.yaml",
    }


def _require_validated_thirty_minute_soak(source_commit: str) -> None:
    evidence = audit._validated_current_thirty_minute_soak(source_commit)  # noqa: SLF001
    if (
        evidence.get("status") != "PASS"
        or not isinstance(evidence.get("validated_runtime_observation"), dict)
    ):
        raise RuntimeError(
            "검증된 최신 30분 soak가 없어 full-suite evidence를 생성하지 않습니다."
        )


def _write_artifact(
    *,
    project_root: Path,
    path: Path,
    content: bytes,
    kind: str,
    artifact_format: str,
) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(project_root):
        raise ValueError("full-suite artifact는 project root 안에 있어야 합니다.")
    return {
        "kind": kind,
        "path": resolved.relative_to(project_root).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "byte_count": len(content),
        "format": artifact_format,
    }


def _source_manifest_rows(
    *,
    section: str,
    test_ids: set[str],
) -> list[dict[str, object]] | None:
    if section == "backend":
        paths = sorted((PROJECT_ROOT / "backend/tests").glob("test_*.py"))
    else:
        paths = sorted(
            {
                *list((PROJECT_ROOT / "frontend/tests").glob("*.test.ts")),
                *list((PROJECT_ROOT / "frontend/tests").glob("*.test.tsx")),
            }
        )
    rows: list[dict[str, object]] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            return None
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        path_ids = sorted(
            test_id for test_id in test_ids if test_id.startswith(f"{relative_path}::")
        )
        if not path_ids:
            return None
        rows.append(
            {
                "path": relative_path,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "test_ids": path_ids,
            }
        )
    return rows or None


def generate_evidence(output: Path) -> dict[str, object]:
    project_root = PROJECT_ROOT.resolve(strict=True)
    output_path = output.resolve()
    if not output_path.is_relative_to(project_root) or output.is_symlink():
        raise ValueError("full-suite output은 project root 내부 regular file이어야 합니다.")
    source_commit, clean_at_start = _source_revision()
    if not clean_at_start:
        raise RuntimeError("source worktree가 clean하지 않아 evidence를 생성하지 않습니다.")
    _require_validated_thirty_minute_soak(source_commit)
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact_root = output.parent / "artifacts" / output.stem.lower()
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, object]] = []

    collection_results: dict[str, subprocess.CompletedProcess[str]] = {}
    collection_ids: dict[str, set[str] | None] = {}
    collection_formats = {
        "backend": "pytest_collection_text",
        "frontend": "vitest_collection_text",
    }
    command_records: list[dict[str, object]] = []
    command_results: dict[str, subprocess.CompletedProcess[str]] = {}
    report_records: dict[str, dict[str, object]] = {}
    for name in audit.FULL_SUITE_COMMAND_ORDER:
        report_format = audit.FULL_SUITE_REPORT_FORMATS[name]
        suffix = (
            ".xml"
            if report_format.endswith("xml")
            else ".json"
            if report_format.endswith("json")
            else ".txt"
        )
        report_path = artifact_root / f"{name}{suffix}"
        relative_report_path = report_path.relative_to(project_root).as_posix()
        command = [
            part.format(report_path=relative_report_path)
            for part in audit.FULL_SUITE_CANONICAL_COMMANDS[name]
        ]
        command_env: dict[str, str] | None = None
        e2e_pytest_path = artifact_root / "master-e2e-pytest.xml"
        e2e_playwright_path = artifact_root / "master-e2e-playwright.json"
        if name == "master_e2e":
            command_env = dict(os.environ)
            existing_pytest_options = command_env.get("PYTEST_ADDOPTS", "").strip()
            junit_option = f"--junitxml={e2e_pytest_path}"
            command_env["PYTEST_ADDOPTS"] = " ".join(
                option for option in (existing_pytest_options, junit_option) if option
            )
            command_env["ROBOM_E2E_JSON_REPORT"] = str(e2e_playwright_path)
            command_env["ROBOM_E2E_CAPTURE"] = "0"
        completed = _execute(command, env=command_env)
        command_results[name] = completed
        if name == "setup":
            content = (
                json.dumps(_setup_report(completed), ensure_ascii=False, indent=2) + "\n"
            ).encode()
        elif name == "master_e2e":
            pytest_record = _write_artifact(
                project_root=project_root,
                path=e2e_pytest_path,
                content=(
                    e2e_pytest_path.read_bytes()
                    if e2e_pytest_path.is_file() and not e2e_pytest_path.is_symlink()
                    else b""
                ),
                kind="artifact",
                artifact_format="master_e2e_pytest_junit_xml",
            )
            playwright_record = _write_artifact(
                project_root=project_root,
                path=e2e_playwright_path,
                content=(
                    e2e_playwright_path.read_bytes()
                    if e2e_playwright_path.is_file()
                    and not e2e_playwright_path.is_symlink()
                    else b""
                ),
                kind="artifact",
                artifact_format="master_e2e_playwright_json",
            )
            artifacts.extend((pytest_record, playwright_record))
            bundle = {
                "schema": "flowscalper.master_e2e_bundle.v1",
                "command": ["make", "e2e"],
                "exit_code": completed.returncode,
                "pytest_junit": pytest_record,
                "playwright_json": playwright_record,
            }
            content = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode()
        elif name in {"network_smoke", "security_scan", "repo_hygiene"}:
            machine_output = _json_from_output(completed)
            content = (
                json.dumps(machine_output, ensure_ascii=False, indent=2) + "\n"
                if machine_output is not None
                else completed.stdout + completed.stderr
            ).encode()
        elif report_format.endswith(("json", "xml")):
            content = (
                report_path.read_bytes()
                if report_path.is_file() and not report_path.is_symlink()
                else (completed.stdout + completed.stderr).encode()
            )
        else:
            content = (completed.stdout + completed.stderr).encode()
        record = _write_artifact(
            project_root=project_root,
            path=report_path,
            content=content,
            kind="artifact" if report_format.endswith(("json", "xml")) else "log",
            artifact_format=report_format,
        )
        artifacts.append(record)
        report_records[name] = record
        command_records.append(
            {
                "name": name,
                "command": command,
                "exit_code": completed.returncode,
                "report_path": relative_report_path,
            }
        )

    for section, command in audit.FULL_SUITE_COLLECTION_COMMANDS.items():
        completed = _execute(list(command))
        collection_results[section] = completed
        content = (completed.stdout + completed.stderr).encode()
        collection_path = artifact_root / f"{section}-collection.txt"
        artifacts.append(
            _write_artifact(
                project_root=project_root,
                path=collection_path,
                content=content,
                kind="log",
                artifact_format=collection_formats[section],
            )
        )
        collection_ids[section] = (
            audit._parse_collection_test_ids(  # noqa: SLF001
                completed.stdout,
                collection_formats[section],
            )
            if completed.returncode == 0
            else None
        )

    backend_ids = collection_ids.get("backend")
    frontend_ids = collection_ids.get("frontend")
    backend_rows = (
        _source_manifest_rows(section="backend", test_ids=backend_ids)
        if isinstance(backend_ids, set)
        else None
    )
    frontend_rows = (
        _source_manifest_rows(section="frontend", test_ids=frontend_ids)
        if isinstance(frontend_ids, set)
        else None
    )
    manifest_status = (
        "PASS"
        if backend_rows is not None
        and frontend_rows is not None
        and all(result.returncode == 0 for result in collection_results.values())
        else "FAIL"
    )
    manifest = {
        "schema": "flowscalper.full_suite_test_manifest.v1",
        "status": manifest_status,
        "source_commit": source_commit,
        "collection_commands": audit.FULL_SUITE_COLLECTION_COMMANDS,
        "backend": backend_rows or [],
        "frontend": frontend_rows or [],
    }
    manifest_path = artifact_root / "full-suite-test-manifest.json"
    artifacts.append(
        _write_artifact(
            project_root=project_root,
            path=manifest_path,
            content=(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(),
            kind="artifact",
            artifact_format="full_suite_test_manifest_json",
        )
    )

    derived_counts = {
        name: audit._full_suite_report_count(name, record)  # noqa: SLF001
        for name, record in report_records.items()
    }
    checks = {
        "backend_pytest_passed": command_results["backend_pytest"].returncode == 0
        and derived_counts["backend_pytest"] is not None,
        "backend_ruff_passed": command_results["backend_ruff"].returncode == 0
        and derived_counts["backend_ruff"] is not None,
        "backend_mypy_passed": command_results["backend_mypy"].returncode == 0
        and derived_counts["backend_mypy"] is not None,
        "frontend_tests_passed": command_results["frontend_tests"].returncode == 0
        and derived_counts["frontend_tests"] is not None,
        "frontend_lint_passed": command_results["frontend_lint"].returncode == 0
        and derived_counts["frontend_lint"] is not None,
        "frontend_typecheck_passed": command_results["frontend_typecheck"].returncode == 0
        and derived_counts["frontend_typecheck"] is not None,
        "frontend_build_passed": command_results["frontend_build"].returncode == 0
        and derived_counts["frontend_build"] is not None,
        "build_safety_passed": command_results["build_safety"].returncode == 0
        and derived_counts["build_safety"] is not None,
        "setup_passed": command_results["setup"].returncode == 0
        and derived_counts["setup"] is not None,
        "master_e2e_passed": command_results["master_e2e"].returncode == 0
        and derived_counts["master_e2e"] is not None,
        "network_smoke_passed": command_results["network_smoke"].returncode == 0
        and derived_counts["network_smoke"] is not None,
        "security_scan_passed": command_results["security_scan"].returncode == 0
        and derived_counts["security_scan"] is not None,
        "repo_hygiene_passed": command_results["repo_hygiene"].returncode == 0
        and derived_counts["repo_hygiene"] is not None,
    }
    source_commit_at_end, clean_at_end = _source_revision()
    source_clean = clean_at_start and clean_at_end and source_commit_at_end == source_commit
    status = (
        "PASS"
        if all(checks.values()) and manifest_status == "PASS" and source_clean
        else "FAIL"
    )
    wrapper: dict[str, object] = {
        "schema_version": 1,
        "generated_ts_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_commit": source_commit,
        "source_commit_at_end": source_commit_at_end,
        "source_worktree_clean_at_start": clean_at_start,
        "source_worktree_clean_at_end": clean_at_end,
        "source_worktree_clean_at_measurement": source_clean,
        "status": status,
        "checks": checks,
        "commands": command_records,
        "counts": {
            "command_count": len(command_records),
            "backend_test_count": derived_counts["backend_pytest"] or 0,
            "frontend_test_count": derived_counts["frontend_tests"] or 0,
        },
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    output.write_text(
        json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return wrapper


def main() -> None:
    arguments = _parse_args()
    try:
        wrapper = generate_evidence(arguments.output)
    except RuntimeError as error:
        print(
            json.dumps(
                {"status": "NOT_RUN", "reason": str(error)},
                ensure_ascii=False,
            )
        )
        raise SystemExit(2) from error
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
