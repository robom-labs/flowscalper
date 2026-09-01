# V7·V8·V9 capability 감사의 상태·증거·안전 계약을 공통 판정한다.

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class AuditStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
    NOT_PROVEN = "NOT_PROVEN"


class CapabilityKind(StrEnum):
    EXECUTION = "EXECUTION"
    FILTER = "FILTER"
    ROUTER = "ROUTER"
    RISK_OVERLAY = "RISK_OVERLAY"
    STATISTICS = "STATISTICS"
    SELECTION = "SELECTION"


SUPPORTED_VERSIONS = ("V7", "V8", "V9")
SOURCE_FILE_SUFFIXES = frozenset({".json", ".py", ".toml", ".yaml", ".yml"})
NON_ENTRY_KINDS = frozenset(
    {
        CapabilityKind.FILTER,
        CapabilityKind.ROUTER,
        CapabilityKind.RISK_OVERLAY,
        CapabilityKind.STATISTICS,
        CapabilityKind.SELECTION,
    }
)
REQUIRED_SAFETY_VALUES: dict[str, object] = {
    "paper_only": True,
    "real_orders_enabled": False,
    "private_api_enabled": False,
    "api_key_enabled": False,
    "wallet_enabled": False,
    "runtime_ai_order_decision_enabled": False,
    "can_increase_risk": False,
}
STATUS_PRECEDENCE = (
    AuditStatus.FAIL,
    AuditStatus.BLOCKED,
    AuditStatus.NOT_RUN,
    AuditStatus.NOT_PROVEN,
    AuditStatus.PASS,
)


@dataclass(frozen=True)
class CapabilitySpec:
    version: str
    capability_id: str
    kind: CapabilityKind
    prerequisite_ids: tuple[str, ...]
    source_symbols: tuple[str, ...]
    required_checks: tuple[str, ...]
    source_roots: tuple[str, ...] = ("backend/app", "config")
    creates_candidate_plan: bool = False
    can_increase_risk: bool = False
    requires_observed_event_time: bool = False
    same_opportunity_execution_comparison: bool = False

    def __post_init__(self) -> None:
        if self.version not in SUPPORTED_VERSIONS:
            raise ValueError(f"지원하지 않는 capability version입니다: {self.version}")
        if re.fullmatch(r"V[789]\.[A-Z0-9_]+", self.capability_id) is None:
            raise ValueError(f"capability_id 형식이 올바르지 않습니다: {self.capability_id}")
        if not self.source_symbols:
            raise ValueError(f"source_symbols가 필요합니다: {self.capability_id}")
        if not self.required_checks:
            raise ValueError(f"required_checks가 필요합니다: {self.capability_id}")


def normalize_status(raw_status: object) -> AuditStatus:
    if raw_status == "PASS":
        return AuditStatus.PASS
    if raw_status == "FAIL":
        return AuditStatus.FAIL
    if raw_status == "BLOCKED":
        return AuditStatus.BLOCKED
    if raw_status in {"NOT_RUN", "PASS_WITH_NOT_RUN"}:
        return AuditStatus.NOT_RUN
    return AuditStatus.NOT_PROVEN


def aggregate_status(statuses: Sequence[AuditStatus | str]) -> AuditStatus:
    normalized = [normalize_status(status) for status in statuses]
    if not normalized:
        return AuditStatus.NOT_RUN
    for status in STATUS_PRECEDENCE:
        if status in normalized:
            return status
    return AuditStatus.NOT_PROVEN


def git_source_provenance(project_root: Path) -> dict[str, object]:
    commit = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    changes = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "AGENTS.md",
            "Makefile",
            "VERSION",
            "backend",
            "config",
            "schemas",
            "scripts",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return {
        "commit": commit,
        "worktree_clean": not changes,
        "change_count": len(changes),
        "changes": changes,
    }


def _source_files(project_root: Path, source_roots: Sequence[str]) -> list[Path]:
    files: set[Path] = set()
    resolved_root = project_root.resolve(strict=True)
    for relative_root in source_roots:
        candidate = project_root / relative_root
        if not candidate.exists() or candidate.is_symlink():
            continue
        candidates = [candidate] if candidate.is_file() else candidate.rglob("*")
        for path in candidates:
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix in SOURCE_FILE_SUFFIXES
                and path.resolve(strict=True).is_relative_to(resolved_root)
            ):
                files.add(path)
    return sorted(files)


def probe_source(project_root: Path, spec: CapabilitySpec) -> dict[str, object]:
    matches: dict[str, list[str]] = {symbol: [] for symbol in spec.source_symbols}
    for path in _source_files(project_root, spec.source_roots):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative_path = path.relative_to(project_root).as_posix()
        for symbol in spec.source_symbols:
            if symbol in content:
                matches[symbol].append(relative_path)
    present_count = sum(bool(paths) for paths in matches.values())
    return {
        "roots": list(spec.source_roots),
        "required_symbols": list(spec.source_symbols),
        "matches": matches,
        "present_symbol_count": present_count,
        "required_symbol_count": len(spec.source_symbols),
        "any_present": present_count > 0,
        "all_present": present_count == len(spec.source_symbols),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifact(
    project_root: Path,
    raw: object,
) -> tuple[dict[str, object] | None, str | None]:
    if not isinstance(raw, Mapping):
        return None, "ARTIFACT_NOT_OBJECT"
    raw_path = raw.get("path")
    if not isinstance(raw_path, str) or not raw_path or Path(raw_path).is_absolute():
        return None, "ARTIFACT_PATH_INVALID"
    candidate = project_root / raw_path
    try:
        resolved_root = project_root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None, "ARTIFACT_NOT_FOUND"
    if (
        candidate.is_symlink()
        or not resolved.is_file()
        or not resolved.is_relative_to(resolved_root)
    ):
        return None, "ARTIFACT_NOT_REGULAR_PROJECT_FILE"
    expected_sha256 = raw.get("sha256")
    expected_bytes = raw.get("byte_count")
    artifact_format = raw.get("format")
    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        return None, "ARTIFACT_SHA256_INVALID"
    if type(expected_bytes) is not int or expected_bytes < 0:
        return None, "ARTIFACT_BYTE_COUNT_INVALID"
    if not isinstance(artifact_format, str) or not artifact_format:
        return None, "ARTIFACT_FORMAT_INVALID"
    actual_bytes = resolved.stat().st_size
    actual_sha256 = _sha256(resolved)
    if expected_bytes != actual_bytes:
        return None, "ARTIFACT_BYTE_COUNT_MISMATCH"
    if expected_sha256 != actual_sha256:
        return None, "ARTIFACT_SHA256_MISMATCH"
    return {
        "path": resolved.relative_to(resolved_root).as_posix(),
        "sha256": actual_sha256,
        "byte_count": actual_bytes,
        "format": artifact_format,
    }, None


def safety_violations(
    spec: CapabilitySpec,
    verification: Mapping[str, object] | None,
) -> list[str]:
    violations: list[str] = []
    if spec.can_increase_risk:
        violations.append("SPEC_CAN_INCREASE_RISK")
    if spec.kind in NON_ENTRY_KINDS and spec.creates_candidate_plan:
        violations.append("NON_ENTRY_CAPABILITY_CREATES_CANDIDATE_PLAN")
    if verification is None:
        return violations
    raw_safety = verification.get("safety")
    if not isinstance(raw_safety, Mapping):
        return violations
    for field, expected in REQUIRED_SAFETY_VALUES.items():
        if field in raw_safety and raw_safety[field] != expected:
            violations.append(f"SAFETY_{field.upper()}_VIOLATION")
    if (
        spec.kind in NON_ENTRY_KINDS
        and raw_safety.get("creates_candidate_plan") is True
    ):
        violations.append("EVIDENCE_NON_ENTRY_CAPABILITY_CREATES_CANDIDATE_PLAN")
    risk_multiplier = raw_safety.get("maximum_risk_multiplier")
    if isinstance(risk_multiplier, bool):
        violations.append("SAFETY_MAXIMUM_RISK_MULTIPLIER_INVALID")
    elif isinstance(risk_multiplier, int | float) and risk_multiplier > 1.0:
        violations.append("SAFETY_MAXIMUM_RISK_MULTIPLIER_ABOVE_ONE")
    return violations


def _evidence_summary(verification: Mapping[str, object] | None) -> dict[str, object]:
    if verification is None:
        return {
            "declared_status": None,
            "source_commit": None,
            "test_ids": [],
            "artifact_count": 0,
            "validation_errors": [],
        }
    raw_test_ids = verification.get("test_ids")
    raw_artifacts = verification.get("artifacts")
    return {
        "declared_status": verification.get("status"),
        "source_commit": verification.get("source_commit"),
        "test_ids": list(raw_test_ids) if isinstance(raw_test_ids, list) else [],
        "artifact_count": len(raw_artifacts) if isinstance(raw_artifacts, list) else 0,
        "validation_errors": [],
    }


def _base_result(
    spec: CapabilitySpec,
    *,
    source: Mapping[str, object],
    evidence: dict[str, object],
    violations: Sequence[str],
) -> dict[str, object]:
    return {
        "id": spec.capability_id,
        "version": spec.version,
        "kind": spec.kind.value,
        "status": AuditStatus.NOT_PROVEN.value,
        "reason": "UNCLASSIFIED",
        "prerequisite_ids": list(spec.prerequisite_ids),
        "source": dict(source),
        "required_checks": list(spec.required_checks),
        "evidence": evidence,
        "role_contract": {
            "creates_candidate_plan": spec.creates_candidate_plan,
            "can_increase_risk": spec.can_increase_risk,
            "requires_observed_event_time": spec.requires_observed_event_time,
            "same_opportunity_execution_comparison": (
                spec.same_opportunity_execution_comparison
            ),
        },
        "safety_violations": list(violations),
        "blockers": [],
        "artifacts": [],
    }


def audit_capability(
    spec: CapabilitySpec,
    *,
    project_root: Path,
    source_commit: str,
    prerequisite_statuses: Mapping[str, AuditStatus | str],
    verification: Mapping[str, object] | None = None,
) -> dict[str, object]:
    source = probe_source(project_root, spec)
    evidence = _evidence_summary(verification)
    violations = safety_violations(spec, verification)
    result = _base_result(spec, source=source, evidence=evidence, violations=violations)
    if violations:
        result.update(status=AuditStatus.FAIL.value, reason="SAFETY_CONTRACT_VIOLATION")
        return result

    blockers = [
        prerequisite
        for prerequisite in spec.prerequisite_ids
        if normalize_status(prerequisite_statuses.get(prerequisite)) is not AuditStatus.PASS
    ]
    if blockers:
        result.update(
            status=AuditStatus.BLOCKED.value,
            reason="PREREQUISITE_NOT_PASS",
            blockers=blockers,
        )
        return result

    if source.get("all_present") is not True:
        status = (
            AuditStatus.NOT_PROVEN
            if source.get("any_present") is True
            else AuditStatus.NOT_RUN
        )
        result.update(
            status=status.value,
            reason="SOURCE_PARTIAL" if status is AuditStatus.NOT_PROVEN else "SOURCE_NOT_PRESENT",
        )
        return result
    if verification is None:
        result.update(
            status=AuditStatus.NOT_PROVEN.value,
            reason="CURRENT_COMMIT_EVIDENCE_MISSING",
        )
        return result

    declared_status = normalize_status(verification.get("status"))
    if declared_status is not AuditStatus.PASS:
        result.update(status=declared_status.value, reason="EVIDENCE_DECLARED_NON_PASS")
        return result
    if verification.get("source_commit") != source_commit:
        result.update(status=AuditStatus.NOT_PROVEN.value, reason="EVIDENCE_SOURCE_COMMIT_MISMATCH")
        return result
    if verification.get("source_worktree_clean_at_measurement") is not True:
        result.update(status=AuditStatus.NOT_PROVEN.value, reason="EVIDENCE_SOURCE_NOT_CLEAN")
        return result

    raw_safety = verification.get("safety")
    if not isinstance(raw_safety, Mapping) or any(
        raw_safety.get(field) != expected for field, expected in REQUIRED_SAFETY_VALUES.items()
    ):
        result.update(status=AuditStatus.NOT_PROVEN.value, reason="EVIDENCE_SAFETY_INCOMPLETE")
        return result
    raw_checks = verification.get("checks")
    if not isinstance(raw_checks, Mapping):
        result.update(status=AuditStatus.NOT_PROVEN.value, reason="EVIDENCE_CHECKS_MISSING")
        return result
    missing_checks = [check for check in spec.required_checks if check not in raw_checks]
    failed_checks = [
        check
        for check in spec.required_checks
        if check in raw_checks and raw_checks.get(check) is not True
    ]
    if failed_checks:
        evidence["validation_errors"] = [f"CHECK_FAILED:{check}" for check in failed_checks]
        result.update(status=AuditStatus.FAIL.value, reason="DECLARED_PASS_CHECK_FAILED")
        return result
    if missing_checks:
        evidence["validation_errors"] = [f"CHECK_MISSING:{check}" for check in missing_checks]
        result.update(status=AuditStatus.NOT_PROVEN.value, reason="REQUIRED_CHECK_MISSING")
        return result
    raw_test_ids = verification.get("test_ids")
    if (
        not isinstance(raw_test_ids, list)
        or not raw_test_ids
        or not all(isinstance(test_id, str) and test_id for test_id in raw_test_ids)
    ):
        result.update(status=AuditStatus.NOT_PROVEN.value, reason="TEST_IDS_MISSING")
        return result
    raw_artifacts = verification.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        result.update(status=AuditStatus.NOT_PROVEN.value, reason="ARTIFACTS_MISSING")
        return result
    artifact_records: list[dict[str, object]] = []
    artifact_errors: list[str] = []
    for raw_artifact in raw_artifacts:
        record, error = validate_artifact(project_root, raw_artifact)
        if error is not None:
            artifact_errors.append(error)
        elif record is not None:
            artifact_records.append(record)
    if artifact_errors:
        evidence["validation_errors"] = artifact_errors
        result.update(status=AuditStatus.FAIL.value, reason="ARTIFACT_INTEGRITY_FAILURE")
        return result
    result.update(
        status=AuditStatus.PASS.value,
        reason="CURRENT_COMMIT_SOURCE_TEST_EVIDENCE_PASS",
        artifacts=artifact_records,
    )
    return result


def audit_catalog(
    specs: Sequence[CapabilitySpec],
    *,
    project_root: Path,
    source_commit: str,
    external_prerequisites: Mapping[str, AuditStatus | str],
    verifications: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    ids = [spec.capability_id for spec in specs]
    if len(ids) != len(set(ids)):
        raise ValueError("capability_id가 중복됐었습니다.")
    statuses: dict[str, AuditStatus | str] = dict(external_prerequisites)
    results: list[dict[str, object]] = []
    for spec in specs:
        unknown = [
            prerequisite
            for prerequisite in spec.prerequisite_ids
            if prerequisite not in statuses
        ]
        if unknown:
            raise ValueError(
                f"{spec.capability_id}의 선행 capability가 선언되지 않았습니다: {unknown}"
            )
        result = audit_capability(
            spec,
            project_root=project_root,
            source_commit=source_commit,
            prerequisite_statuses=statuses,
            verification=verifications.get(spec.capability_id),
        )
        statuses[spec.capability_id] = str(result["status"])
        results.append(result)
    return results


def result_summary(results: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    statuses = [normalize_status(result.get("status")) for result in results]
    return {
        "declared": len(results),
        "source_present": sum(
            isinstance(result.get("source"), Mapping)
            and result["source"].get("all_present") is True
            for result in results
        ),
        "verified": statuses.count(AuditStatus.PASS),
        "not_proven": statuses.count(AuditStatus.NOT_PROVEN),
        "not_run": statuses.count(AuditStatus.NOT_RUN),
        "blocked": statuses.count(AuditStatus.BLOCKED),
        "failed": statuses.count(AuditStatus.FAIL),
    }
