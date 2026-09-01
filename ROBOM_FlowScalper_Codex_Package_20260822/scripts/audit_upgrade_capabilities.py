# V6 선행계약과 V7·V8·V9 연구 capability를 단일 기계판독 증거로 감사한다.

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.strategies.family import (
    FAMILY_CATALOG,
    STRATEGY_VARIANT_CONTRACTS,
    StrategyRole,
)
from scripts.capability_audit_contract import (
    AuditStatus,
    CapabilityKind,
    CapabilitySpec,
    aggregate_status,
    audit_catalog,
    git_source_provenance,
    normalize_status,
    result_summary,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "evidence/V9_CAPABILITY_AUDIT.json"
DEFAULT_CURRENT_MATRIX_OUTPUT = PROJECT_ROOT / "evidence/V9_CURRENT_CAPABILITY_MATRIX.json"
DEFAULT_VERIFICATION_MANIFEST = PROJECT_ROOT / "evidence/V9_CAPABILITY_VERIFICATION.json"
V6_SYSTEM_TRUTH_PATH = PROJECT_ROOT / "evidence/V6_CURRENT_SYSTEM_TRUTH.json"
V6_CONFLICT_MATRIX_PATH = PROJECT_ROOT / "evidence/V6_STRATEGY_CONFLICT_MATRIX.json"
REPORT_SCHEMA = "flowscalper.capability_audit.v1"
CURRENT_MATRIX_SCHEMA = "flowscalper.v9_current_capability_matrix.v1"
VERIFICATION_SCHEMA = "flowscalper.capability_verification.v1"
VERSION_ORDER = ("V7", "V8", "V9")
DEFAULT_RUNTIME_ROOT = Path("/Volumes/ROBOM_FLOWSCALPER/05_RUNTIME/ROBOM_FlowScalper")

CORE_CHECKS = (
    "paper_only",
    "real_orders_disabled",
    "private_api_disabled",
    "deterministic_fixture",
)
STRATEGY_CHECKS = CORE_CHECKS + (
    "cost_aware_rejection",
    "no_lookahead",
    "parameter_contract_documented",
)
EXECUTION_CHECKS = STRATEGY_CHECKS + (
    "duplicate_events",
    "partial_fills",
    "stale_data",
    "disconnect_reconnect",
    "pessimistic_ambiguous_ordering",
    "state_recovery",
    "absence_of_real_order_calls",
)
STATISTICS_CHECKS = CORE_CHECKS + (
    "fixed_input",
    "base_stress_separated",
    "multiple_testing_control",
)


def _capability(
    version: str,
    name: str,
    kind: CapabilityKind,
    *,
    prerequisite_ids: tuple[str, ...],
    required_checks: tuple[str, ...],
    creates_candidate_plan: bool = False,
    can_increase_risk: bool = False,
    requires_observed_event_time: bool = False,
    same_opportunity_execution_comparison: bool = False,
    source_symbols: tuple[str, ...] | None = None,
    source_roots: tuple[str, ...] = ("backend/app", "config"),
) -> CapabilitySpec:
    capability_id = f"{version}.{name}"
    return CapabilitySpec(
        version=version,
        capability_id=capability_id,
        kind=kind,
        prerequisite_ids=prerequisite_ids,
        source_symbols=source_symbols or (capability_id,),
        required_checks=required_checks,
        source_roots=source_roots,
        creates_candidate_plan=creates_candidate_plan,
        can_increase_risk=can_increase_risk,
        requires_observed_event_time=requires_observed_event_time,
        same_opportunity_execution_comparison=same_opportunity_execution_comparison,
    )


CAPABILITY_SPECS: tuple[CapabilitySpec, ...] = (
    _capability(
        "V7",
        "EXECUTION_TAKER_BASELINE",
        CapabilityKind.EXECUTION,
        prerequisite_ids=("V6.BASELINE",),
        required_checks=EXECUTION_CHECKS,
        requires_observed_event_time=True,
        same_opportunity_execution_comparison=True,
    ),
    _capability(
        "V7",
        "EXECUTION_PASSIVE_MAKER",
        CapabilityKind.EXECUTION,
        prerequisite_ids=("V7.EXECUTION_TAKER_BASELINE",),
        required_checks=EXECUTION_CHECKS + ("queue_uncertainty", "non_fill", "adverse_selection"),
        requires_observed_event_time=True,
        same_opportunity_execution_comparison=True,
    ),
    _capability(
        "V7",
        "EXECUTION_MAKER_TAKER_HYBRID",
        CapabilityKind.EXECUTION,
        prerequisite_ids=("V7.EXECUTION_PASSIVE_MAKER",),
        required_checks=EXECUTION_CHECKS + ("alpha_decay_cancel", "same_signal_comparison"),
        requires_observed_event_time=True,
        same_opportunity_execution_comparison=True,
    ),
    _capability(
        "V7",
        "QUEUE_NONFILL_ADVERSE_SELECTION",
        CapabilityKind.FILTER,
        prerequisite_ids=("V7.EXECUTION_PASSIVE_MAKER",),
        required_checks=STRATEGY_CHECKS + ("queue_uncertainty", "non_fill", "adverse_selection"),
        requires_observed_event_time=True,
    ),
    _capability(
        "V7",
        "TRANSITION_CUSUM",
        CapabilityKind.FILTER,
        prerequisite_ids=("V6.BASELINE",),
        required_checks=STRATEGY_CHECKS + ("transition_detection",),
    ),
    _capability(
        "V7",
        "TRANSITION_BOCPD",
        CapabilityKind.FILTER,
        prerequisite_ids=("V6.BASELINE",),
        required_checks=STRATEGY_CHECKS + ("transition_detection",),
    ),
    _capability(
        "V7",
        "BTC_ETH_RESIDUAL_STRENGTH",
        CapabilityKind.FILTER,
        prerequisite_ids=("V6.BASELINE",),
        required_checks=STRATEGY_CHECKS + ("beta_removed",),
    ),
    _capability(
        "V7",
        "RISK_REDUCTION_ONLY",
        CapabilityKind.RISK_OVERLAY,
        prerequisite_ids=("V6.BASELINE",),
        required_checks=STRATEGY_CHECKS + ("risk_multiplier_at_most_one",),
        source_symbols=(
            "RiskOverlay",
            "risk_overlay: RiskOverlay",
            "base_risk_budget",
            "RISK_OVERLAY_ZERO",
        ),
        source_roots=(
            "backend/app/research/gates.py",
            "backend/app/risk/manager.py",
        ),
    ),
    _capability(
        "V7",
        "FROZEN_LOCAL_META_FILTER",
        CapabilityKind.FILTER,
        prerequisite_ids=("V7.TRANSITION_CUSUM", "V7.BTC_ETH_RESIDUAL_STRENGTH"),
        required_checks=STRATEGY_CHECKS + ("frozen_model", "rules_first_warmup"),
    ),
    _capability(
        "V7",
        "SPA",
        CapabilityKind.STATISTICS,
        prerequisite_ids=("V6.BASELINE",),
        required_checks=STATISTICS_CHECKS + ("hansen_spa",),
    ),
    _capability(
        "V7",
        "MODEL_CONFIDENCE_SET",
        CapabilityKind.STATISTICS,
        prerequisite_ids=("V7.SPA",),
        required_checks=STATISTICS_CHECKS + ("model_confidence_set",),
    ),
    _capability(
        "V7",
        "WHITE_REALITY_CHECK",
        CapabilityKind.STATISTICS,
        prerequisite_ids=("V7.SPA",),
        required_checks=STATISTICS_CHECKS + ("white_reality_check",),
    ),
    _capability(
        "V7",
        "COST_ADJUSTED_R_DRIFT",
        CapabilityKind.STATISTICS,
        prerequisite_ids=("V6.BASELINE",),
        required_checks=STATISTICS_CHECKS + ("cost_adjusted_r_drift",),
    ),
    _capability(
        "V8",
        "PRICE_DISCOVERY_LEADER",
        CapabilityKind.FILTER,
        prerequisite_ids=("V7.GATE",),
        required_checks=STRATEGY_CHECKS + ("leader_not_permanently_fixed", "venue_time_aligned"),
        requires_observed_event_time=True,
    ),
    _capability(
        "V8",
        "WORLD_FLOW_SYNC",
        CapabilityKind.FILTER,
        prerequisite_ids=("V7.GATE",),
        required_checks=STRATEGY_CHECKS + ("quote_currency_normalized", "venue_time_aligned"),
        requires_observed_event_time=True,
    ),
    _capability(
        "V8",
        "HAWKES_FLOW_QUALITY",
        CapabilityKind.FILTER,
        prerequisite_ids=("V7.GATE",),
        required_checks=STRATEGY_CHECKS + ("intensity_not_entry_signal",),
        requires_observed_event_time=True,
    ),
    _capability(
        "V8",
        "MARKET_INTEGRITY_FILTER",
        CapabilityKind.FILTER,
        prerequisite_ids=("V7.GATE",),
        required_checks=STRATEGY_CHECKS + ("no_wash_trading_claim",),
        requires_observed_event_time=True,
    ),
    _capability(
        "V8",
        "PERMUTATION_ENTROPY_QUALITY",
        CapabilityKind.FILTER,
        prerequisite_ids=("V7.GATE",),
        required_checks=STRATEGY_CHECKS + ("entropy_not_entry_signal",),
    ),
    _capability(
        "V8",
        "CONFORMAL_NET_EDGE_LOWER_BOUND",
        CapabilityKind.FILTER,
        prerequisite_ids=("V7.GATE",),
        required_checks=STRATEGY_CHECKS + ("net_edge_lower_bound", "risk_not_increased"),
    ),
    _capability(
        "V8",
        "TAIL_EXPECTED_SHORTFALL",
        CapabilityKind.RISK_OVERLAY,
        prerequisite_ids=("V7.GATE",),
        required_checks=STRATEGY_CHECKS + ("expected_shortfall", "risk_multiplier_at_most_one"),
    ),
    _capability(
        "V8",
        "CLUSTER_EXPOSURE",
        CapabilityKind.RISK_OVERLAY,
        prerequisite_ids=("V7.GATE",),
        required_checks=STRATEGY_CHECKS + ("beta_cluster_limit", "risk_multiplier_at_most_one"),
        source_symbols=("ClusterExposureAssessment", "cluster_exposure_risk_multiplier"),
        source_roots=("backend/app/research/gates.py",),
    ),
    _capability(
        "V8",
        "EVIDENCE_EPOCH_CHECKPOINT",
        CapabilityKind.STATISTICS,
        prerequisite_ids=("V7.GATE",),
        required_checks=STATISTICS_CHECKS + ("checkpoint_predeclared", "no_optional_stopping"),
        source_symbols=("EvidenceEpoch", "HypothesisRegistry"),
        source_roots=("backend/app/research/gates.py",),
    ),
    _capability(
        "V9",
        "DIRECTIONAL_CHANGE_INTRINSIC_TIME",
        CapabilityKind.FILTER,
        prerequisite_ids=("V8.GATE",),
        required_checks=STRATEGY_CHECKS + ("intrinsic_time", "threshold_preregistered"),
        requires_observed_event_time=True,
        source_symbols=(
            "DirectionalChangeEngine",
            "DirectionalChangeEvent",
            "DC_OVERSHOOT_CONTINUATION_V1",
        ),
        source_roots=(
            "backend/app/features/directional_change.py",
            "backend/app/research/v9_candidates.py",
        ),
    ),
    _capability(
        "V9",
        "DC_OBSERVED_VS_INFERRED_CONFIRMATION",
        CapabilityKind.FILTER,
        prerequisite_ids=("V9.DIRECTIONAL_CHANGE_INTRINSIC_TIME",),
        required_checks=STRATEGY_CHECKS + ("observed_inferred_separated",),
        requires_observed_event_time=True,
        source_symbols=(
            "actual_confirmation_price",
            "theoretical_confirmation_price",
            "entry_eligible",
            "DC_OVERSHOOT_EXHAUSTION_REVERSAL_V1",
        ),
        source_roots=(
            "backend/app/features/directional_change.py",
            "backend/app/research/v9_candidates.py",
        ),
    ),
    _capability(
        "V9",
        "SEMIVARIANCE_JUMP_ROUTER",
        CapabilityKind.ROUTER,
        prerequisite_ids=("V8.GATE",),
        required_checks=STRATEGY_CHECKS
        + ("up_down_semivariance_separated", "jump_component_separated"),
        source_symbols=(
            "SemivarianceJumpEngine",
            "evaluate_semivariance_router",
            "downside_semivariance_risk_multiplier",
            "SEMIVARIANCE_MOMENTUM_REVERSAL_ROUTER_V1",
            "DOWNSIDE_SEMIVARIANCE_RISK_OVERLAY_V1",
        ),
        source_roots=(
            "backend/app/features/semivariance.py",
            "backend/app/research/v9_candidates.py",
        ),
    ),
    _capability(
        "V9",
        "COPULA_PAIRS_TAIL_DEPENDENCE",
        CapabilityKind.FILTER,
        prerequisite_ids=("V8.GATE",),
        required_checks=STRATEGY_CHECKS + ("tail_dependence", "linear_baseline_comparison"),
        source_symbols=(
            "COPULA_COINTEGRATED_PAIRS_1H_V2",
            "V9Readiness.BLOCKED_ENGINE",
        ),
        source_roots=("backend/app/research/v9_candidates.py",),
    ),
    _capability(
        "V9",
        "HIERARCHICAL_SHRINKAGE",
        CapabilityKind.STATISTICS,
        prerequisite_ids=("V8.GATE",),
        required_checks=STATISTICS_CHECKS + ("family_partial_pooling", "small_sample_shrinkage"),
        source_symbols=(
            "shrink_win_rate",
            "shrink_net_r_cells",
            "HIERARCHICAL_PERFORMANCE_SHRINKAGE_V1",
        ),
        source_roots=(
            "backend/app/research/statistical_evidence.py",
            "backend/app/research/v9_candidates.py",
        ),
    ),
    _capability(
        "V9",
        "FDR_CONTROL",
        CapabilityKind.STATISTICS,
        prerequisite_ids=("V9.HIERARCHICAL_SHRINKAGE",),
        required_checks=STATISTICS_CHECKS + ("false_discovery_rate", "tested_hypothesis_count"),
        source_symbols=("FDRMethod", "batch_fdr", "BATCH_FDR_HARVEY_LIU_V1"),
        source_roots=(
            "backend/app/research/statistical_evidence.py",
            "backend/app/research/v9_candidates.py",
        ),
    ),
    _capability(
        "V9",
        "ANYTIME_E_VALUE",
        CapabilityKind.STATISTICS,
        prerequisite_ids=("V9.FDR_CONTROL",),
        required_checks=STATISTICS_CHECKS + ("anytime_valid", "optional_stopping_safe"),
        source_symbols=(
            "new_e_process",
            "update_e_process",
            "e_bh",
            "ANYTIME_EPROCESS_V1",
            "E_BH_STRATEGY_SELECTION_V1",
        ),
        source_roots=(
            "backend/app/research/statistical_evidence.py",
            "backend/app/research/v9_candidates.py",
        ),
    ),
    _capability(
        "V9",
        "PARETO_SELECTION",
        CapabilityKind.SELECTION,
        prerequisite_ids=("V9.FDR_CONTROL",),
        required_checks=STATISTICS_CHECKS + ("non_dominated_set", "no_hidden_scalar_score"),
        source_symbols=("ParetoCandidate", "pareto_robust_set", "PARETO_ROBUST_SET_V1"),
        source_roots=(
            "backend/app/research/statistical_evidence.py",
            "backend/app/research/v9_candidates.py",
        ),
    ),
    _capability(
        "V9",
        "HYSTERESIS_NO_TRADE_ZONE",
        CapabilityKind.FILTER,
        prerequisite_ids=("V8.GATE",),
        required_checks=STRATEGY_CHECKS + ("separate_on_off_thresholds", "no_trade_zone"),
        source_symbols=("HysteresisConfig", "advance_hysteresis", "HYSTERESIS_SETUP_GATE_V1"),
        source_roots=(
            "backend/app/research/gates.py",
            "backend/app/research/v9_candidates.py",
        ),
    ),
    _capability(
        "V9",
        "EVIDENCE_FRESHNESS",
        CapabilityKind.SELECTION,
        prerequisite_ids=("V8.GATE",),
        required_checks=STATISTICS_CHECKS + ("evidence_age", "stale_not_current"),
        source_symbols=(
            "EvidenceFreshness",
            "EvidenceCategory",
            "assess_evidence_freshness",
            "EVIDENCE_FRESHNESS_GATE_V1",
        ),
        source_roots=(
            "backend/app/research/statistical_evidence.py",
            "backend/app/research/v9_candidates.py",
        ),
    ),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", choices=VERSION_ORDER, default="V9")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--matrix-output",
        type=Path,
        default=DEFAULT_CURRENT_MATRIX_OUTPUT,
    )
    parser.add_argument(
        "--verification-manifest",
        type=Path,
        default=DEFAULT_VERIFICATION_MANIFEST,
    )
    return parser.parse_args()


def _installed_release(runtime_root: Path) -> dict[str, object]:
    """설치 포인터 세 종류가 같은 불변 PAPER 릴리스를 가리킬 때만 commit을 확정한다."""

    deployment_path = runtime_root / "current-deployment.json"
    anchor_path = runtime_root / "support/current-release-integrity.json"
    current_path = runtime_root / "current"
    result: dict[str, object] = {
        "status": AuditStatus.NOT_RUN.value,
        "commit": None,
        "reason": "INSTALLED_RELEASE_EVIDENCE_MISSING",
    }
    if (
        not deployment_path.is_file()
        or deployment_path.is_symlink()
        or not anchor_path.is_file()
        or anchor_path.is_symlink()
        or not current_path.is_symlink()
    ):
        return result
    try:
        deployment: object = json.loads(deployment_path.read_text(encoding="utf-8"))
        anchor: object = json.loads(anchor_path.read_text(encoding="utf-8"))
        resolved_current = current_path.resolve(strict=True)
    except (json.JSONDecodeError, OSError):
        return result | {
            "status": AuditStatus.FAIL.value,
            "reason": "INSTALLED_RELEASE_EVIDENCE_INVALID",
        }
    if not isinstance(deployment, Mapping) or not isinstance(anchor, Mapping):
        return result | {
            "status": AuditStatus.FAIL.value,
            "reason": "INSTALLED_RELEASE_EVIDENCE_INVALID",
        }
    deployment_commit = deployment.get("release_commit")
    anchor_commit = anchor.get("release_commit")
    current_commit = resolved_current.name
    commits = (deployment_commit, anchor_commit, current_commit)
    if any(
        not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None
        for commit in commits
    ):
        return result | {
            "status": AuditStatus.FAIL.value,
            "reason": "INSTALLED_RELEASE_COMMIT_INVALID",
        }
    if len(set(commits)) != 1:
        return result | {
            "status": AuditStatus.FAIL.value,
            "reason": "INSTALLED_RELEASE_COMMIT_MISMATCH",
        }
    if (
        deployment.get("paper_only") is not True
        or deployment.get("real_orders_enabled") is not False
        or deployment.get("auth_required") is not False
        or anchor.get("paper_only") is not True
        or anchor.get("real_orders_enabled") is not False
    ):
        return result | {
            "status": AuditStatus.FAIL.value,
            "reason": "INSTALLED_RELEASE_PAPER_SAFETY_INVALID",
        }
    return {
        "status": AuditStatus.PASS.value,
        "commit": deployment_commit,
        "reason": "DEPLOYMENT_ANCHOR_CURRENT_POINTER_MATCH",
    }


def _capability_rows(report: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    versions = report.get("versions")
    if not isinstance(versions, Mapping):
        return {}
    v9 = versions.get("V9")
    if not isinstance(v9, Mapping):
        return {}
    raw_rows = v9.get("capabilities")
    if not isinstance(raw_rows, list):
        return {}
    return {
        str(row["id"]): row
        for row in raw_rows
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }


def build_current_capability_matrix(
    report: Mapping[str, object],
    *,
    runtime_root: Path | None = None,
) -> dict[str, object]:
    """V9 원문의 필수 키를 현재 commit·설치·검증 증거로 보수적으로 작성한다."""

    source = report.get("source")
    git_commit = source.get("commit") if isinstance(source, Mapping) else None
    if not isinstance(git_commit, str) or re.fullmatch(r"[0-9a-f]{40}", git_commit) is None:
        raise ValueError("capability audit의 git commit이 올바르지 않습니다.")
    configured_runtime_root = runtime_root or Path(
        os.environ.get("ROBOM_RUNTIME_ROOT", str(DEFAULT_RUNTIME_ROOT))
    )
    installed = _installed_release(configured_runtime_root)
    rows = _capability_rows(report)

    capability_ids = {
        "directional_change_supported": "V9.DIRECTIONAL_CHANGE_INTRINSIC_TIME",
        "actual_confirmation_guard_supported": "V9.DC_OBSERVED_VS_INFERRED_CONFIRMATION",
        "semivariance_supported": "V9.SEMIVARIANCE_JUMP_ROUTER",
        "copula_pairs_supported": "V9.COPULA_PAIRS_TAIL_DEPENDENCE",
        "hierarchical_shrinkage_supported": "V9.HIERARCHICAL_SHRINKAGE",
        "batch_fdr_supported": "V9.FDR_CONTROL",
        "e_value_supported": "V9.ANYTIME_E_VALUE",
        "e_bh_supported": "V9.ANYTIME_E_VALUE",
        "pareto_set_supported": "V9.PARETO_SELECTION",
        "hysteresis_supported": "V9.HYSTERESIS_NO_TRADE_ZONE",
        "evidence_freshness_supported": "V9.EVIDENCE_FRESHNESS",
    }
    support = {
        key: (
            isinstance(rows.get(capability_id), Mapping)
            and rows[capability_id].get("status") == AuditStatus.PASS.value
        )
        for key, capability_id in capability_ids.items()
    }
    # 현재 Semivariance 코어는 signed POS/NEG jump와 완전한 주기성 보정 검증이 없다.
    support["periodicity_adjusted_jump_supported"] = False

    current_variants = sorted(
        strategy_id
        for strategy_id, contract in STRATEGY_VARIANT_CONTRACTS.items()
        if contract.role is StrategyRole.ENTRY and contract.is_current_variant
    )
    challenger_variants = sorted(
        strategy_id
        for strategy_id, contract in STRATEGY_VARIANT_CONTRACTS.items()
        if contract.role is StrategyRole.ENTRY and not contract.is_current_variant
    )
    previous_variants = sorted(
        strategy_id
        for strategy_id, contract in STRATEGY_VARIANT_CONTRACTS.items()
        if contract.role is StrategyRole.LEGACY
    )
    capability_status = {
        key: (
            str(rows[capability_id].get("status"))
            if isinstance(rows.get(capability_id), Mapping)
            else AuditStatus.NOT_RUN.value
        )
        for key, capability_id in capability_ids.items()
    }
    capability_status["periodicity_adjusted_jump_supported"] = (
        "PARTIAL_SOURCE_NOT_CONNECTED"
    )
    safety = report.get("safety")
    safety_proven = (
        isinstance(safety, Mapping)
        and safety.get("status") == AuditStatus.PASS.value
        and installed.get("status") == AuditStatus.PASS.value
    )
    return {
        "schema": CURRENT_MATRIX_SCHEMA,
        "schema_version": 1,
        "generated_ts_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": report.get("status", AuditStatus.NOT_PROVEN.value),
        "git_commit": git_commit,
        "installed_commit": installed.get("commit"),
        "installation_status": installed.get("status"),
        "installation_reason": installed.get("reason"),
        "strategy_families": [family.family_id.value for family in FAMILY_CATALOG],
        "current_variants": current_variants,
        "challenger_variants": challenger_variants,
        "previous_variants": previous_variants,
        **support,
        "actual_orders_enabled": False if safety_proven else None,
        "private_api_enabled": False if safety_proven else None,
        "api_key_required": False if safety_proven else None,
        "wallet_enabled": False if safety_proven else None,
        "capability_status": capability_status,
        "known_gaps": [
            "COPULA_ENGINE_NOT_IMPLEMENTED",
            "HARVEY_LIU_DOUBLE_BOOTSTRAP_NOT_IMPLEMENTED",
            "SIGNED_POS_NEG_JUMP_NOT_IMPLEMENTED",
            "V9_RUNTIME_ENTRY_NOT_CONNECTED",
            "FULL_REPLAY_OOS_FORWARD_NOT_PROVEN",
        ],
    }


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file() or path.is_symlink():
        return None, "FILE_NOT_FOUND"
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, "INVALID_JSON"
    if not isinstance(payload, dict):
        return None, "ROOT_NOT_OBJECT"
    return payload, None


def _v6_baseline(project_root: Path, source_commit: str) -> dict[str, object]:
    truth_path = project_root / V6_SYSTEM_TRUTH_PATH.relative_to(PROJECT_ROOT)
    matrix_path = project_root / V6_CONFLICT_MATRIX_PATH.relative_to(PROJECT_ROOT)
    truth, truth_error = _read_json_object(truth_path)
    matrix, matrix_error = _read_json_object(matrix_path)
    result: dict[str, object] = {
        "status": AuditStatus.NOT_RUN.value,
        "reason": "V6_EVIDENCE_MISSING",
        "system_truth_path": truth_path.relative_to(project_root).as_posix(),
        "conflict_matrix_path": matrix_path.relative_to(project_root).as_posix(),
        "system_truth_report_status": truth.get("status") if truth is not None else None,
        "source_contract_status": None,
        "source_safety_status": None,
        "conflict_matrix_schema": matrix.get("schema") if matrix is not None else None,
        "violations": [],
    }
    if truth_error is not None or matrix_error is not None:
        result["errors"] = [
            error
            for error in (
                f"SYSTEM_TRUTH_{truth_error}" if truth_error is not None else None,
                f"CONFLICT_MATRIX_{matrix_error}" if matrix_error is not None else None,
            )
            if error is not None
        ]
        return result
    assert truth is not None
    assert matrix is not None
    source_contract = truth.get("source_contract")
    transport = truth.get("api_transport_contract")
    safety = transport.get("safety") if isinstance(transport, Mapping) else None
    source_contract_status = (
        normalize_status(source_contract.get("status"))
        if isinstance(source_contract, Mapping)
        else AuditStatus.NOT_PROVEN
    )
    safety_status = (
        normalize_status(safety.get("status"))
        if isinstance(safety, Mapping)
        else AuditStatus.NOT_PROVEN
    )
    result["source_contract_status"] = source_contract_status.value
    result["source_safety_status"] = safety_status.value
    if source_contract_status is AuditStatus.FAIL or safety_status is AuditStatus.FAIL:
        result.update(
            status=AuditStatus.FAIL.value,
            reason="V6_SOURCE_OR_SAFETY_CONTRACT_FAILED",
            violations=["V6_SOURCE_OR_SAFETY_CONTRACT_FAILED"],
        )
        return result
    if source_contract_status is not AuditStatus.PASS or safety_status is not AuditStatus.PASS:
        result.update(status=AuditStatus.NOT_PROVEN.value, reason="V6_SOURCE_OR_SAFETY_NOT_PROVEN")
        return result
    if truth.get("latest_git_commit") != source_commit:
        result.update(status=AuditStatus.NOT_PROVEN.value, reason="V6_SYSTEM_TRUTH_COMMIT_MISMATCH")
        return result
    invariants = matrix.get("invariants")
    if matrix.get("schema") != "flowscalper.v6_strategy_conflict_matrix.v1":
        result.update(status=AuditStatus.FAIL.value, reason="V6_CONFLICT_MATRIX_SCHEMA_INVALID")
        return result
    if not isinstance(invariants, Mapping) or not invariants or any(
        value is not True for value in invariants.values()
    ):
        result.update(status=AuditStatus.FAIL.value, reason="V6_CONFLICT_MATRIX_INVARIANT_FAILED")
        return result
    result.update(status=AuditStatus.PASS.value, reason="V6_SOURCE_FAMILY_GOVERNOR_BASELINE_PASS")
    return result


def _load_verifications(path: Path) -> tuple[dict[str, Mapping[str, object]], dict[str, object]]:
    payload, error = _read_json_object(path)
    summary: dict[str, object] = {
        "path": path.as_posix(),
        "status": AuditStatus.NOT_RUN.value,
        "schema": None,
        "capability_count": 0,
        "reason": "VERIFICATION_MANIFEST_NOT_FOUND",
    }
    if error == "FILE_NOT_FOUND":
        return {}, summary
    if error is not None or payload is None:
        summary.update(status=AuditStatus.FAIL.value, reason=f"VERIFICATION_MANIFEST_{error}")
        return {}, summary
    summary["schema"] = payload.get("schema")
    raw_capabilities = payload.get("capabilities")
    if payload.get("schema") != VERIFICATION_SCHEMA or not isinstance(raw_capabilities, Mapping):
        summary.update(status=AuditStatus.FAIL.value, reason="VERIFICATION_MANIFEST_SCHEMA_INVALID")
        return {}, summary
    verifications = {
        capability_id: verification
        for capability_id, verification in raw_capabilities.items()
        if isinstance(capability_id, str) and isinstance(verification, Mapping)
    }
    if len(verifications) != len(raw_capabilities):
        summary.update(status=AuditStatus.FAIL.value, reason="VERIFICATION_CAPABILITY_ROW_INVALID")
        return {}, summary
    summary.update(
        status=AuditStatus.PASS.value,
        capability_count=len(verifications),
        reason="VERIFICATION_MANIFEST_LOADED",
    )
    return verifications, summary


def _selected_versions(through: str) -> tuple[str, ...]:
    return VERSION_ORDER[: VERSION_ORDER.index(through) + 1]


def build_report(
    *,
    project_root: Path = PROJECT_ROOT,
    through: str = "V9",
    verification_manifest: Path | None = None,
) -> dict[str, object]:
    if through not in VERSION_ORDER:
        raise ValueError(f"지원하지 않는 --through 값입니다: {through}")
    start = git_source_provenance(project_root)
    source_commit = str(start["commit"])
    baseline = _v6_baseline(project_root, source_commit)
    manifest_path = verification_manifest or (
        project_root / DEFAULT_VERIFICATION_MANIFEST.relative_to(PROJECT_ROOT)
    )
    verifications, manifest_summary = _load_verifications(manifest_path)
    selected_versions = _selected_versions(through)
    gates: dict[str, AuditStatus | str] = {"V6.BASELINE": str(baseline["status"])}
    versions: dict[str, object] = {}
    all_results: list[dict[str, object]] = []
    for version in selected_versions:
        specs = [spec for spec in CAPABILITY_SPECS if spec.version == version]
        results = audit_catalog(
            specs,
            project_root=project_root,
            source_commit=source_commit,
            external_prerequisites=gates,
            verifications=verifications,
        )
        version_status = aggregate_status([str(result["status"]) for result in results])
        versions[version] = {
            "status": version_status.value,
            "prerequisites": [
                "V6.BASELINE" if version == "V7" else f"V{int(version[1:]) - 1}.GATE"
            ],
            "summary": result_summary(results),
            "capabilities": results,
        }
        gates[f"{version}.GATE"] = version_status
        all_results.extend(results)

    capability_violations: list[str] = []
    for result in all_results:
        raw_violations = result.get("safety_violations")
        if isinstance(raw_violations, list):
            capability_violations.extend(
                violation for violation in raw_violations if isinstance(violation, str)
            )
    raw_baseline_violations = baseline.get("violations")
    baseline_violations = (
        [
            violation
            for violation in raw_baseline_violations
            if isinstance(violation, str)
        ]
        if isinstance(raw_baseline_violations, list)
        else []
    )
    all_violations = baseline_violations + capability_violations
    version_statuses = [
        str(version_payload["status"])
        for version_payload in versions.values()
        if isinstance(version_payload, Mapping)
    ]
    overall_inputs = [str(baseline["status"]), *version_statuses]
    if manifest_summary["status"] == AuditStatus.FAIL.value:
        overall_inputs.append(AuditStatus.FAIL.value)
    if all_violations:
        overall_inputs.append(AuditStatus.FAIL.value)
    overall_status = aggregate_status(overall_inputs)
    artifacts: list[dict[str, object]] = []
    seen_artifacts: set[tuple[str, str]] = set()
    for result in all_results:
        raw_artifacts = result.get("artifacts")
        if not isinstance(raw_artifacts, list):
            continue
        for artifact in raw_artifacts:
            if not isinstance(artifact, dict):
                continue
            key = (str(artifact.get("path")), str(artifact.get("sha256")))
            if key not in seen_artifacts:
                seen_artifacts.add(key)
                artifacts.append(artifact)
    unresolved = [
        {
            "id": result["id"],
            "version": result["version"],
            "status": result["status"],
            "reason": result["reason"],
            "blockers": result["blockers"],
        }
        for result in all_results
        if result["status"] != AuditStatus.PASS.value
    ]
    end = git_source_provenance(project_root)
    source_stable = start["commit"] == end["commit"]
    if not source_stable:
        overall_status = AuditStatus.FAIL
        unresolved.append(
            {
                "id": "SOURCE_CHANGED_DURING_AUDIT",
                "version": "BASELINE",
                "status": AuditStatus.FAIL.value,
                "reason": "SOURCE_COMMIT_CHANGED_DURING_AUDIT",
                "blockers": [],
            }
        )
    source_safety_proven = baseline.get("source_safety_status") == AuditStatus.PASS.value
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": 1,
        "generated_ts_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "through": through,
        "status": overall_status.value,
        "source": {
            "commit": start["commit"],
            "commit_at_end": end["commit"],
            "clean_at_start": start["worktree_clean"],
            "clean_at_end": end["worktree_clean"],
            "stable_during_audit": source_stable,
            "change_count_at_start": start["change_count"],
            "change_count_at_end": end["change_count"],
        },
        "baseline": {
            "v6": baseline,
            "verification_manifest": manifest_summary,
        },
        "safety": {
            "status": (
                AuditStatus.FAIL.value
                if all_violations
                else AuditStatus.PASS.value
                if source_safety_proven
                else AuditStatus.NOT_PROVEN.value
            ),
            "paper_only": True if source_safety_proven else None,
            "real_orders_enabled": False if source_safety_proven else None,
            "private_api_enabled": False if source_safety_proven else None,
            "risk_increase_capability_count": sum(
                bool(role_contract.get("can_increase_risk"))
                for result in all_results
                if isinstance((role_contract := result.get("role_contract")), Mapping)
            ),
            "violations": all_violations,
        },
        "versions": versions,
        "summary": result_summary(all_results),
        "unresolved": unresolved,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }


def main() -> None:
    arguments = _parse_args()
    report = build_report(
        through=arguments.through,
        verification_manifest=arguments.verification_manifest,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    matrix_output: str | None = None
    if arguments.through == "V9":
        matrix = build_current_capability_matrix(report)
        arguments.matrix_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.matrix_output.write_text(
            json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        matrix_output = str(arguments.matrix_output)
    print(
        json.dumps(
            {
                "status": report["status"],
                "through": report["through"],
                "output": str(arguments.output),
                "matrix_output": matrix_output,
                "summary": report["summary"],
            },
            ensure_ascii=False,
        )
    )
    if report["status"] == AuditStatus.FAIL.value:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
