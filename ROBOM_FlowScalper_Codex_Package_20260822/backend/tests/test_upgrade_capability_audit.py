# V7·V8·V9 capability 감사의 fail-closed 상태·증거·안전 계약을 회귀 검증한다.

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import audit_upgrade_capabilities as upgrade_audit
from scripts.capability_audit_contract import (
    AuditStatus,
    CapabilityKind,
    CapabilitySpec,
    aggregate_status,
    audit_capability,
    audit_catalog,
    normalize_status,
    probe_source,
)


def _spec(
    *,
    capability_id: str = "V7.TEST_CAPABILITY",
    prerequisite_ids: tuple[str, ...] = ("V6.BASELINE",),
    kind: CapabilityKind = CapabilityKind.FILTER,
    creates_candidate_plan: bool = False,
    can_increase_risk: bool = False,
) -> CapabilitySpec:
    return CapabilitySpec(
        version="V7",
        capability_id=capability_id,
        kind=kind,
        prerequisite_ids=prerequisite_ids,
        source_symbols=(capability_id,),
        required_checks=("required_check",),
        creates_candidate_plan=creates_candidate_plan,
        can_increase_risk=can_increase_risk,
    )


def _write_source(project_root: Path, capability_id: str) -> None:
    path = project_root / "backend/app/capabilities.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'CAPABILITY_ID = "{capability_id}"\n', encoding="utf-8")


def _verification(
    project_root: Path,
    *,
    source_commit: str,
    check_value: bool = True,
) -> dict[str, object]:
    artifact = project_root / "evidence/capability-proof.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('{"paper_only":true}\n', encoding="utf-8")
    content = artifact.read_bytes()
    return {
        "status": "PASS",
        "source_commit": source_commit,
        "source_worktree_clean_at_measurement": True,
        "checks": {"required_check": check_value},
        "test_ids": ["backend/tests/test_capability.py::test_required_contract"],
        "safety": {
            "paper_only": True,
            "real_orders_enabled": False,
            "private_api_enabled": False,
            "api_key_enabled": False,
            "wallet_enabled": False,
            "runtime_ai_order_decision_enabled": False,
            "can_increase_risk": False,
            "creates_candidate_plan": False,
            "maximum_risk_multiplier": 1.0,
        },
        "artifacts": [
            {
                "path": "evidence/capability-proof.json",
                "sha256": hashlib.sha256(content).hexdigest(),
                "byte_count": len(content),
                "format": "capability_test_json",
            }
        ],
    }


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("PASS", AuditStatus.PASS),
        ("FAIL", AuditStatus.FAIL),
        ("BLOCKED", AuditStatus.BLOCKED),
        ("NOT_RUN", AuditStatus.NOT_RUN),
        ("PASS_WITH_NOT_RUN", AuditStatus.NOT_RUN),
        ("NOT_PROVEN", AuditStatus.NOT_PROVEN),
        ("UNKNOWN", AuditStatus.NOT_PROVEN),
        (None, AuditStatus.NOT_PROVEN),
    ],
)
def test_normalize_status_is_conservative(
    raw_status: object,
    expected: AuditStatus,
) -> None:
    assert normalize_status(raw_status) is expected


def test_aggregate_status_uses_fail_closed_precedence() -> None:
    assert aggregate_status(["PASS", "NOT_PROVEN"]) is AuditStatus.NOT_PROVEN
    assert aggregate_status(["PASS", "NOT_RUN", "NOT_PROVEN"]) is AuditStatus.NOT_RUN
    assert aggregate_status(["PASS", "BLOCKED", "NOT_RUN"]) is AuditStatus.BLOCKED
    assert aggregate_status(["PASS", "FAIL", "BLOCKED"]) is AuditStatus.FAIL


def test_missing_partial_and_unverified_source_never_pass(tmp_path: Path) -> None:
    spec = _spec()
    missing = audit_capability(
        spec,
        project_root=tmp_path,
        source_commit="a" * 40,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
    )
    assert missing["status"] == "NOT_RUN"
    assert missing["reason"] == "SOURCE_NOT_PRESENT"

    _write_source(tmp_path, "V7.UNRELATED_CAPABILITY")
    partial_spec = CapabilitySpec(
        version="V7",
        capability_id="V7.PARTIAL_CAPABILITY",
        kind=CapabilityKind.FILTER,
        prerequisite_ids=("V6.BASELINE",),
        source_symbols=("V7.PARTIAL_CAPABILITY", "SECOND_REQUIRED_SYMBOL"),
        required_checks=("required_check",),
    )
    source_path = tmp_path / "backend/app/capabilities.py"
    source_path.write_text('CAPABILITY_ID = "V7.PARTIAL_CAPABILITY"\n', encoding="utf-8")
    partial = audit_capability(
        partial_spec,
        project_root=tmp_path,
        source_commit="a" * 40,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
    )
    assert partial["status"] == "NOT_PROVEN"
    assert partial["reason"] == "SOURCE_PARTIAL"

    source_path.write_text(
        'CAPABILITY_ID = "V7.PARTIAL_CAPABILITY"\nSECOND_REQUIRED_SYMBOL = True\n',
        encoding="utf-8",
    )
    unverified = audit_capability(
        partial_spec,
        project_root=tmp_path,
        source_commit="a" * 40,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
    )
    assert unverified["status"] == "NOT_PROVEN"
    assert unverified["reason"] == "CURRENT_COMMIT_EVIDENCE_MISSING"


@pytest.mark.parametrize(
    "spec",
    [
        _spec(can_increase_risk=True),
        _spec(creates_candidate_plan=True),
    ],
)
def test_static_safety_violation_is_fail(tmp_path: Path, spec: CapabilitySpec) -> None:
    result = audit_capability(
        spec,
        project_root=tmp_path,
        source_commit="a" * 40,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
    )
    assert result["status"] == "FAIL"
    assert result["reason"] == "SAFETY_CONTRACT_VIOLATION"
    assert result["safety_violations"]


def test_evidence_safety_violation_is_fail_even_when_other_evidence_passes(
    tmp_path: Path,
) -> None:
    spec = _spec()
    _write_source(tmp_path, spec.capability_id)
    verification = _verification(tmp_path, source_commit="a" * 40)
    safety = verification["safety"]
    assert isinstance(safety, dict)
    safety["real_orders_enabled"] = True
    result = audit_capability(
        spec,
        project_root=tmp_path,
        source_commit="a" * 40,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
        verification=verification,
    )
    assert result["status"] == "FAIL"
    assert "SAFETY_REAL_ORDERS_ENABLED_VIOLATION" in result["safety_violations"]


def test_non_pass_prerequisite_blocks_capability(tmp_path: Path) -> None:
    spec = _spec()
    _write_source(tmp_path, spec.capability_id)
    result = audit_capability(
        spec,
        project_root=tmp_path,
        source_commit="a" * 40,
        prerequisite_statuses={"V6.BASELINE": "NOT_PROVEN"},
        verification=_verification(tmp_path, source_commit="a" * 40),
    )
    assert result["status"] == "BLOCKED"
    assert result["blockers"] == ["V6.BASELINE"]


def test_current_commit_source_test_and_artifact_evidence_can_pass(tmp_path: Path) -> None:
    spec = _spec()
    commit = "a" * 40
    _write_source(tmp_path, spec.capability_id)
    result = audit_capability(
        spec,
        project_root=tmp_path,
        source_commit=commit,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
        verification=_verification(tmp_path, source_commit=commit),
    )
    assert result["status"] == "PASS"
    assert result["reason"] == "CURRENT_COMMIT_SOURCE_TEST_EVIDENCE_PASS"
    assert len(result["artifacts"]) == 1


def test_stale_or_incomplete_pass_claim_is_not_proven(tmp_path: Path) -> None:
    spec = _spec()
    _write_source(tmp_path, spec.capability_id)
    stale = audit_capability(
        spec,
        project_root=tmp_path,
        source_commit="a" * 40,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
        verification=_verification(tmp_path, source_commit="b" * 40),
    )
    assert stale["status"] == "NOT_PROVEN"
    assert stale["reason"] == "EVIDENCE_SOURCE_COMMIT_MISMATCH"

    missing_check = _verification(tmp_path, source_commit="a" * 40)
    missing_check["checks"] = {}
    incomplete = audit_capability(
        spec,
        project_root=tmp_path,
        source_commit="a" * 40,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
        verification=missing_check,
    )
    assert incomplete["status"] == "NOT_PROVEN"
    assert incomplete["reason"] == "REQUIRED_CHECK_MISSING"


def test_false_required_check_or_tampered_artifact_is_fail(tmp_path: Path) -> None:
    spec = _spec()
    commit = "a" * 40
    _write_source(tmp_path, spec.capability_id)
    failed_check = audit_capability(
        spec,
        project_root=tmp_path,
        source_commit=commit,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
        verification=_verification(tmp_path, source_commit=commit, check_value=False),
    )
    assert failed_check["status"] == "FAIL"
    assert failed_check["reason"] == "DECLARED_PASS_CHECK_FAILED"

    tampered_verification = _verification(tmp_path, source_commit=commit)
    artifact = tmp_path / "evidence/capability-proof.json"
    artifact.write_text("tampered\n", encoding="utf-8")
    tampered = audit_capability(
        spec,
        project_root=tmp_path,
        source_commit=commit,
        prerequisite_statuses={"V6.BASELINE": "PASS"},
        verification=tampered_verification,
    )
    assert tampered["status"] == "FAIL"
    assert tampered["reason"] == "ARTIFACT_INTEGRITY_FAILURE"


def test_catalog_enforces_declared_dependency_order(tmp_path: Path) -> None:
    first = _spec(capability_id="V7.FIRST_CAPABILITY")
    second = _spec(
        capability_id="V7.SECOND_CAPABILITY",
        prerequisite_ids=("V7.FIRST_CAPABILITY",),
    )
    results = audit_catalog(
        (first, second),
        project_root=tmp_path,
        source_commit="a" * 40,
        external_prerequisites={"V6.BASELINE": "PASS"},
        verifications={},
    )
    assert [result["status"] for result in results] == ["NOT_RUN", "BLOCKED"]

    with pytest.raises(ValueError, match="선행 capability"):
        audit_catalog(
            (second,),
            project_root=tmp_path,
            source_commit="a" * 40,
            external_prerequisites={"V6.BASELINE": "PASS"},
            verifications={},
        )


def _init_git_repository(project_root: Path) -> str:
    subprocess.run(["git", "init"], cwd=project_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "codex@example.invalid"],
        cwd=project_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Codex Test"],
        cwd=project_root,
        check=True,
    )
    marker = project_root / "VERSION"
    marker.write_text("0.0.0-test\n", encoding="utf-8")
    subprocess.run(["git", "add", "VERSION"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "test baseline"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_build_report_honors_through_and_uses_one_versioned_envelope(tmp_path: Path) -> None:
    commit = _init_git_repository(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "V6_CURRENT_SYSTEM_TRUTH.json").write_text(
        json.dumps(
            {
                "status": "NOT_RUN_STOPPED_OR_UNREACHABLE_RUNTIME",
                "latest_git_commit": commit,
                "source_contract": {"status": "PASS"},
                "api_transport_contract": {"safety": {"status": "PASS"}},
            }
        ),
        encoding="utf-8",
    )
    (evidence / "V6_STRATEGY_CONFLICT_MATRIX.json").write_text(
        json.dumps(
            {
                "schema": "flowscalper.v6_strategy_conflict_matrix.v1",
                "invariants": {"all_pairs_unique": True},
            }
        ),
        encoding="utf-8",
    )
    report = upgrade_audit.build_report(
        project_root=tmp_path,
        through="V8",
        verification_manifest=evidence / "missing-verification.json",
    )
    assert report["schema"] == "flowscalper.capability_audit.v1"
    assert report["through"] == "V8"
    assert list(report["versions"]) == ["V7", "V8"]
    assert report["baseline"]["v6"]["status"] == "PASS"
    assert report["safety"]["paper_only"] is True
    assert report["artifact_count"] == len(report["artifacts"])
    assert "V9" not in report["versions"]


def test_stale_v6_commit_keeps_all_later_versions_blocked(tmp_path: Path) -> None:
    _init_git_repository(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "V6_CURRENT_SYSTEM_TRUTH.json").write_text(
        json.dumps(
            {
                "status": "PASS_WITH_NOT_PROVEN_RESEARCH",
                "latest_git_commit": "b" * 40,
                "source_contract": {"status": "PASS"},
                "api_transport_contract": {"safety": {"status": "PASS"}},
            }
        ),
        encoding="utf-8",
    )
    (evidence / "V6_STRATEGY_CONFLICT_MATRIX.json").write_text(
        json.dumps(
            {
                "schema": "flowscalper.v6_strategy_conflict_matrix.v1",
                "invariants": {"all_pairs_unique": True},
            }
        ),
        encoding="utf-8",
    )

    report = upgrade_audit.build_report(
        project_root=tmp_path,
        through="V9",
        verification_manifest=evidence / "missing-verification.json",
    )

    assert report["baseline"]["v6"]["status"] == "NOT_PROVEN"
    assert report["baseline"]["v6"]["reason"] == "V6_SYSTEM_TRUTH_COMMIT_MISMATCH"
    assert report["status"] == "BLOCKED"
    assert {
        version: payload["status"] for version, payload in report["versions"].items()
    } == {"V7": "BLOCKED", "V8": "BLOCKED", "V9": "BLOCKED"}


def test_current_new_modules_satisfy_path_scoped_source_symbol_contracts() -> None:
    expected_source_present = {
        "V7.RISK_REDUCTION_ONLY",
        "V8.EVIDENCE_EPOCH_CHECKPOINT",
        "V9.DIRECTIONAL_CHANGE_INTRINSIC_TIME",
        "V9.DC_OBSERVED_VS_INFERRED_CONFIRMATION",
        "V9.SEMIVARIANCE_JUMP_ROUTER",
        "V9.COPULA_PAIRS_TAIL_DEPENDENCE",
        "V9.HIERARCHICAL_SHRINKAGE",
        "V9.FDR_CONTROL",
        "V9.ANYTIME_E_VALUE",
        "V9.PARETO_SELECTION",
        "V9.HYSTERESIS_NO_TRADE_ZONE",
        "V9.EVIDENCE_FRESHNESS",
    }
    probes = {
        spec.capability_id: probe_source(upgrade_audit.PROJECT_ROOT, spec)
        for spec in upgrade_audit.CAPABILITY_SPECS
    }
    actual_source_present = {
        capability_id
        for capability_id, source in probes.items()
        if source["all_present"] is True
    }

    assert actual_source_present == expected_source_present
    assert probes["V7.RISK_REDUCTION_ONLY"]["matches"]["RISK_OVERLAY_ZERO"] == [
        "backend/app/risk/manager.py"
    ]
    assert probes["V9.SEMIVARIANCE_JUMP_ROUTER"]["matches"][
        "SemivarianceJumpEngine"
    ] == ["backend/app/features/semivariance.py"]
    assert probes["V9.FDR_CONTROL"]["matches"]["batch_fdr"] == [
        "backend/app/research/statistical_evidence.py"
    ]
    assert probes["V8.CLUSTER_EXPOSURE"]["all_present"] is False


def test_capability_catalog_and_json_schema_are_stage_complete() -> None:
    ids = [spec.capability_id for spec in upgrade_audit.CAPABILITY_SPECS]
    assert len(ids) == len(set(ids))
    assert {spec.version for spec in upgrade_audit.CAPABILITY_SPECS} == {"V7", "V8", "V9"}
    assert all(spec.source_symbols for spec in upgrade_audit.CAPABILITY_SPECS)
    assert all(not spec.can_increase_risk for spec in upgrade_audit.CAPABILITY_SPECS)
    assert all(
        not spec.creates_candidate_plan
        for spec in upgrade_audit.CAPABILITY_SPECS
        if spec.kind is not CapabilityKind.EXECUTION
    )
    schema_path = upgrade_audit.PROJECT_ROOT / "schemas/flowscalper.capability_audit.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema"]["const"] == "flowscalper.capability_audit.v1"
    assert schema["$defs"]["status"]["enum"] == [
        "PASS",
        "FAIL",
        "BLOCKED",
        "NOT_RUN",
        "NOT_PROVEN",
    ]


def test_v9_current_matrix_has_exact_required_surface_and_fails_closed(
    tmp_path: Path,
) -> None:
    commit = "a" * 40
    report: dict[str, object] = {
        "status": "NOT_PROVEN",
        "source": {"commit": commit},
        "safety": {"status": "PASS"},
        "versions": {
            "V9": {
                "capabilities": [
                    {"id": spec.capability_id, "status": "NOT_PROVEN"}
                    for spec in upgrade_audit.CAPABILITY_SPECS
                    if spec.version == "V9"
                ]
            }
        },
    }
    matrix = upgrade_audit.build_current_capability_matrix(
        report,
        runtime_root=tmp_path / "missing-runtime",
    )
    required = {
        "git_commit",
        "installed_commit",
        "strategy_families",
        "current_variants",
        "previous_variants",
        "directional_change_supported",
        "actual_confirmation_guard_supported",
        "semivariance_supported",
        "periodicity_adjusted_jump_supported",
        "copula_pairs_supported",
        "hierarchical_shrinkage_supported",
        "batch_fdr_supported",
        "e_value_supported",
        "e_bh_supported",
        "pareto_set_supported",
        "hysteresis_supported",
        "evidence_freshness_supported",
        "actual_orders_enabled",
        "private_api_enabled",
        "api_key_required",
        "wallet_enabled",
    }
    assert required <= matrix.keys()
    assert matrix["git_commit"] == commit
    assert matrix["installed_commit"] is None
    assert matrix["installation_status"] == "NOT_RUN"
    assert len(matrix["strategy_families"]) == 8
    assert len(matrix["current_variants"]) == 3
    assert len(matrix["challenger_variants"]) == 3
    assert len(matrix["previous_variants"]) == 9
    for field in required:
        if field.endswith("_supported"):
            assert matrix[field] is False
    assert matrix["actual_orders_enabled"] is None
    assert "COPULA_ENGINE_NOT_IMPLEMENTED" in matrix["known_gaps"]


def test_v9_current_matrix_only_marks_verified_capability_and_exact_install(
    tmp_path: Path,
) -> None:
    commit = "b" * 40
    release = tmp_path / "releases" / commit
    release.mkdir(parents=True)
    (tmp_path / "support").mkdir()
    (tmp_path / "current").symlink_to(release)
    (tmp_path / "current-deployment.json").write_text(
        json.dumps(
            {
                "release_commit": commit,
                "paper_only": True,
                "real_orders_enabled": False,
                "auth_required": False,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "support/current-release-integrity.json").write_text(
        json.dumps(
            {
                "release_commit": commit,
                "paper_only": True,
                "real_orders_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    report: dict[str, object] = {
        "status": "NOT_PROVEN",
        "source": {"commit": commit},
        "safety": {"status": "PASS"},
        "versions": {
            "V9": {
                "capabilities": [
                    {
                        "id": "V9.DIRECTIONAL_CHANGE_INTRINSIC_TIME",
                        "status": "PASS",
                    }
                ]
            }
        },
    }
    matrix = upgrade_audit.build_current_capability_matrix(
        report,
        runtime_root=tmp_path,
    )
    assert matrix["installed_commit"] == commit
    assert matrix["installation_status"] == "PASS"
    assert matrix["directional_change_supported"] is True
    assert matrix["actual_confirmation_guard_supported"] is False
    assert matrix["periodicity_adjusted_jump_supported"] is False
    assert matrix["actual_orders_enabled"] is False
    assert matrix["private_api_enabled"] is False
    assert matrix["api_key_required"] is False
    assert matrix["wallet_enabled"] is False


def test_v9_current_matrix_schema_requires_original_section_143_keys() -> None:
    schema_path = (
        upgrade_audit.PROJECT_ROOT
        / "schemas/flowscalper.v9_current_capability_matrix.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["schema"]["const"] == (
        "flowscalper.v9_current_capability_matrix.v1"
    )
    assert {
        "git_commit",
        "installed_commit",
        "strategy_families",
        "current_variants",
        "previous_variants",
        "directional_change_supported",
        "actual_confirmation_guard_supported",
        "semivariance_supported",
        "periodicity_adjusted_jump_supported",
        "copula_pairs_supported",
        "hierarchical_shrinkage_supported",
        "batch_fdr_supported",
        "e_value_supported",
        "e_bh_supported",
        "pareto_set_supported",
        "hysteresis_supported",
        "evidence_freshness_supported",
        "actual_orders_enabled",
        "private_api_enabled",
        "api_key_required",
        "wallet_enabled",
    } <= set(schema["required"])
