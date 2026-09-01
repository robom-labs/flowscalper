# V9 연구 후보가 모두 관찰 ON이면서 runtime 진입은 차단되는지 검증한다.

from __future__ import annotations

import pytest

from backend.app.research.v9_candidates import (
    V9CandidateRole,
    V9CandidateSpec,
    V9Readiness,
    v9_candidate_manifest,
    v9_candidate_specs,
)

EXPECTED_IDS = {
    "DC_OVERSHOOT_CONTINUATION_V1",
    "DC_OVERSHOOT_EXHAUSTION_REVERSAL_V1",
    "COPULA_COINTEGRATED_PAIRS_1H_V2",
    "SEMIVARIANCE_MOMENTUM_REVERSAL_ROUTER_V1",
    "DOWNSIDE_SEMIVARIANCE_RISK_OVERLAY_V1",
    "HYSTERESIS_SETUP_GATE_V1",
    "EVIDENCE_FRESHNESS_GATE_V1",
    "HIERARCHICAL_PERFORMANCE_SHRINKAGE_V1",
    "BATCH_FDR_HARVEY_LIU_V1",
    "ANYTIME_EPROCESS_V1",
    "E_BH_STRATEGY_SELECTION_V1",
    "PARETO_ROBUST_SET_V1",
}


def test_v9_registry_has_exact_candidates_and_separates_direction_count() -> None:
    rows = v9_candidate_specs()

    assert {row.candidate_id for row in rows} == EXPECTED_IDS
    assert len(rows) == 12
    assert sum(row.counts_as_direction_strategy for row in rows) == 2
    assert sum(row.counts_as_market_neutral_strategy for row in rows) == 1
    assert sum(row.role is V9CandidateRole.STATISTICS for row in rows) == 3
    assert all(row.monitoring_enabled for row in rows)
    assert all(row.source_ids for row in rows)
    assert all(
        source_id.startswith("SRC-")
        for row in rows
        for source_id in row.source_ids
    )


def test_all_v9_monitoring_switches_are_on_without_enabling_entry_or_active() -> None:
    rows = v9_candidate_specs()

    assert all(row.monitoring_enabled for row in rows)
    assert not any(row.entry_enabled for row in rows)
    assert not any(row.active_enabled for row in rows)
    assert not any(row.runtime_entry_registered for row in rows)
    assert not any(row.can_increase_risk for row in rows)
    assert all(row.paper_only for row in rows)


def test_dc_sources_are_partial_and_runtime_entry_stays_unconnected() -> None:
    dc_rows = [
        row for row in v9_candidate_specs() if row.candidate_id.startswith("DC_OVERSHOOT_")
    ]

    assert len(dc_rows) == 2
    assert all(
        row.readiness is V9Readiness.PARTIAL_SOURCE_NOT_CONNECTED
        for row in dc_rows
    )
    assert not any(row.runtime_entry_registered for row in dc_rows)
    assert not any(row.entry_enabled for row in dc_rows)


def test_partial_semivariance_and_missing_harvey_liu_are_not_overstated() -> None:
    rows = {row.candidate_id: row for row in v9_candidate_specs()}

    assert (
        rows["SEMIVARIANCE_MOMENTUM_REVERSAL_ROUTER_V1"].readiness
        is V9Readiness.PARTIAL_SOURCE_NOT_CONNECTED
    )
    assert (
        rows["DOWNSIDE_SEMIVARIANCE_RISK_OVERLAY_V1"].readiness
        is V9Readiness.PARTIAL_SOURCE_NOT_CONNECTED
    )
    assert (
        rows["EVIDENCE_FRESHNESS_GATE_V1"].readiness
        is V9Readiness.SOURCE_IMPLEMENTED_NOT_CONNECTED
    )
    assert (
        rows["BATCH_FDR_HARVEY_LIU_V1"].readiness
        is V9Readiness.BLOCKED_PREREQUISITE
    )


def test_copula_candidate_remains_blocked_until_multi_leg_engine_exists() -> None:
    copula = next(
        row
        for row in v9_candidate_specs()
        if row.candidate_id == "COPULA_COINTEGRATED_PAIRS_1H_V2"
    )

    assert copula.role is V9CandidateRole.MARKET_NEUTRAL_MULTI_LEG
    assert copula.readiness is V9Readiness.BLOCKED_ENGINE
    assert copula.monitoring_enabled is True
    assert copula.entry_enabled is False


def test_non_entry_capability_cannot_create_entry() -> None:
    with pytest.raises(ValueError, match="진입을 만들 수 없습니다"):
        V9CandidateSpec(
            candidate_id="BAD_STATISTIC",
            label_ko="잘못된 통계",
            role=V9CandidateRole.STATISTICS,
            family_id=None,
            prerequisite_capability_ids=("V9.FDR_CONTROL",),
            source_ids=("SRC-FDR-BH-1995",),
            readiness=V9Readiness.BLOCKED_PREREQUISITE,
            entry_enabled=True,
        )


def test_candidate_cannot_increase_risk() -> None:
    with pytest.raises(ValueError, match="위험을 늘릴 수 없습니다"):
        V9CandidateSpec(
            candidate_id="BAD_RISK",
            label_ko="잘못된 위험",
            role=V9CandidateRole.RISK_OVERLAY,
            family_id=None,
            prerequisite_capability_ids=("V7.RISK_REDUCTION_ONLY",),
            source_ids=("SRC-REALIZED-SEMIVARIANCE-MOMREV-2023",),
            readiness=V9Readiness.BLOCKED_PREREQUISITE,
            can_increase_risk=True,
        )


def test_manifest_is_deterministic_and_preserves_paper_safety() -> None:
    first = v9_candidate_manifest(source_commit="a" * 40)
    second = v9_candidate_manifest(source_commit="a" * 40)

    assert first == second
    assert first["candidate_count"] == 12
    assert first["monitoring_on_count"] == 12
    assert first["direction_strategy_count"] == 2
    assert first["runtime_entry_registered_count"] == 0
    assert first["entry_enabled_count"] == 0
    assert first["active_count"] == 0
    assert first["paper_only"] is True
    assert first["real_orders_enabled"] is False
    assert first["auth_required"] is False
    assert first["private_api_enabled"] is False
    assert first["api_key_enabled"] is False
    assert first["wallet_enabled"] is False
    assert first["runtime_ai_order_decision_enabled"] is False
    assert len(str(first["manifest_sha256"])) == 64
