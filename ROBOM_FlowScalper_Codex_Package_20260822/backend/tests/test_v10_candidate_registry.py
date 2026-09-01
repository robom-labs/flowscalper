# V10 후보의 역할·차단상태·비노출·출처·PAPER 안전 계약을 검증한다.

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from backend.app.research import v10_candidates
from backend.app.research.source_metadata import research_source_metadata_rows
from backend.app.research.v10_candidates import (
    V10CandidateRole,
    V10Readiness,
    v10_candidate_manifest,
    v10_candidate_specs,
)
from backend.app.strategies.registry import StrategyRegistry

EXPECTED_CONTRACT = {
    "SWING_MULTI_HORIZON_TREND_4H1D_V1": (
        V10CandidateRole.ENTRY,
        V10Readiness.RESEARCH_SPEC,
    ),
    "DAILY_DONCHIAN_RETEST_1D4H_V1": (
        V10CandidateRole.ENTRY,
        V10Readiness.RESEARCH_SPEC,
    ),
    "CFTC_CME_BITCOIN_CROWDING_FILTER_V1": (
        V10CandidateRole.FILTER,
        V10Readiness.BLOCKED_SOURCE_PIPELINE,
    ),
    "CRYPTO_FUTURES_CURVE_REGIME_FILTER_V1": (
        V10CandidateRole.FILTER,
        V10Readiness.BLOCKED_SOURCE_PIPELINE,
    ),
    "RESIDUAL_14D_RELATIVE_STRENGTH_V1": (
        V10CandidateRole.ENTRY_RESEARCH,
        V10Readiness.BLOCKED_POINT_IN_TIME_UNIVERSE,
    ),
    "BASIS_MOMENTUM_CROSS_SECTIONAL_RESEARCH_V1": (
        V10CandidateRole.MARKET_NEUTRAL_MULTI_LEG,
        V10Readiness.BLOCKED_ENGINE,
    ),
}


def test_v10_registry_has_exact_ids_roles_readiness_and_counts() -> None:
    rows = v10_candidate_specs()

    assert len(rows) == 6
    assert {
        row.candidate_id: (row.role, row.readiness) for row in rows
    } == EXPECTED_CONTRACT
    assert sum(row.counts_as_direction_strategy for row in rows) == 3
    assert sum(row.counts_as_filter for row in rows) == 2
    assert sum(row.counts_as_market_neutral_strategy for row in rows) == 1


def test_v10_candidates_do_not_change_runtime_registry_or_default_exposure() -> None:
    rows = v10_candidate_specs()
    runtime_ids = set(StrategyRegistry().strategy_ids)

    assert len(runtime_ids) == 15
    assert runtime_ids.isdisjoint(EXPECTED_CONTRACT)
    assert not any(row.research_enabled for row in rows)
    assert not any(row.user_visible_by_default for row in rows)
    assert not any(row.final_ranking_eligible for row in rows)
    assert not any(row.runtime_entry_registered for row in rows)
    assert not any(row.entry_enabled for row in rows)
    assert not any(row.active_enabled for row in rows)
    assert all(row.trial_ledger_included for row in rows)


def test_filters_never_create_direction_and_all_blocked_rows_cannot_execute() -> None:
    rows = v10_candidate_specs()
    filters = [row for row in rows if row.role is V10CandidateRole.FILTER]
    blocked = [row for row in rows if row.readiness.value.startswith("BLOCKED_")]

    assert len(filters) == 2
    assert not any(row.can_create_direction_now for row in filters)
    assert all(
        any(contract.endswith("CANNOT_CREATE_DIRECTION") for contract in row.entry_contract)
        for row in filters
    )
    assert len(blocked) == 4
    assert not any(row.runtime_entry_registered for row in blocked)
    assert not any(row.entry_enabled for row in blocked)
    assert not any(row.active_enabled for row in blocked)
    assert not any(row.can_create_direction_now for row in blocked)


def test_cftc_timestamp_contract_separates_report_schedule_and_observation() -> None:
    cftc = next(
        row
        for row in v10_candidate_specs()
        if row.candidate_id == "CFTC_CME_BITCOIN_CROWDING_FILTER_V1"
    )

    assert "CFTC_TFF_FUTURES_ONLY_BITCOIN_133741_PRIMARY" in cftc.entry_contract
    assert "MICRO_BITCOIN_133742_SENSITIVITY_ONLY" in cftc.entry_contract
    assert "REPORT_DATE_IS_POSITION_DATE_NOT_AVAILABILITY" in cftc.entry_contract
    assert (
        "SCHEDULED_RELEASE_AT_AMERICA_NEW_YORK_15_30_WITH_HOLIDAY_SCHEDULE"
        in cftc.entry_contract
    )
    assert "FIRST_OBSERVED_AT_OR_INGESTED_AT_REQUIRED" in cftc.entry_contract
    assert (
        "FIRST_OBSERVED_AT_IS_NOT_OFFICIAL_ACTUAL_RELEASE_TIMESTAMP"
        in cftc.entry_contract
    )
    assert "ACTUAL_RELEASE_TIMESTAMP_ONLY" not in cftc.entry_contract
    assert "DATA_AGE_GT_10D_IS_UNAVAILABLE" in cftc.entry_contract


def test_swing_parameters_and_max_hold_are_safety_reviews_only() -> None:
    rows = {row.candidate_id: row for row in v10_candidate_specs()}
    swing = rows["SWING_MULTI_HORIZON_TREND_4H1D_V1"]
    donchian = rows["DAILY_DONCHIAN_RETEST_1D4H_V1"]
    residual = rows["RESIDUAL_14D_RELATIVE_STRENGTH_V1"]

    assert "VOL_NORMALIZED_TREND_SCORE_12H_24H_72H_GE_2" in swing.entry_contract
    assert "BASE_COST_COVERAGE_GE_3.00" in swing.entry_contract
    assert "STRESS_COST_COVERAGE_GE_1.75" in swing.entry_contract
    assert "RISK_DISTANCE_1.0_TO_3.0_ATR4H" in swing.exit_contract
    assert "TP1_1.50R_CLOSE_25_PERCENT" in swing.exit_contract
    assert "MAX_HOLD_7D_SAFETY_ONLY_NOT_TIME_EXIT" in swing.exit_contract
    assert "1D_CLOSE_GT_PREVIOUS_DONCHIAN_HIGH55_PLUS_0.10_ATR20" in (
        donchian.entry_contract
    )
    assert "RETEST_WITHIN_NEXT_6_COMPLETED_4H_BARS" in donchian.entry_contract
    assert "TP1_2.00R_CLOSE_20_PERCENT" in donchian.exit_contract
    assert "MAX_HOLD_14D_SAFETY_ONLY_NOT_TIME_EXIT" in donchian.exit_contract
    assert "MAX_HOLD_7D_SAFETY_REVIEW" in residual.exit_contract
    assert all(row.max_hold_safety_review_only for row in rows.values())


def test_candidate_level_paper_safety_and_completed_bar_invariants_are_explicit() -> None:
    rows = v10_candidate_specs()

    assert all(row.completed_bars_only for row in rows)
    assert not any(row.can_widen_stop for row in rows)
    assert not any(row.can_increase_risk for row in rows)
    assert not any(row.averaging_down for row in rows)
    assert not any(row.martingale for row in rows)
    assert not any(row.pyramiding for row in rows)
    assert not any(row.real_orders_enabled for row in rows)
    assert not any(row.private_api_enabled for row in rows)
    assert not any(row.api_key_enabled for row in rows)
    assert not any(row.wallet_enabled for row in rows)
    assert not any(row.runtime_ai_order_decision_enabled for row in rows)
    assert all(row.paper_only for row in rows)
    assert all(row.profitability_status == "NOT_PROVEN" for row in rows)
    assert all(row.funding_readiness == "NOT_READY" for row in rows)


def test_every_v10_source_id_resolves_without_not_proven_fallback() -> None:
    manifest = v10_candidate_manifest(source_commit="1" * 40)
    source_ids = {
        source_id
        for row in v10_candidate_specs()
        for source_id in row.source_ids
    }
    rejected = manifest["rejected_hypotheses"]
    assert isinstance(rejected, list)
    source_ids.update(rejected[0]["source_ids"])
    metadata_rows = research_source_metadata_rows(sorted(source_ids))

    assert all(row["metadata_status"] != "NOT_PROVEN" for row in metadata_rows)
    official_ids = {
        "SRC-CFTC-TFF-FUTURES-ONLY",
        "SRC-CFTC-COT-RELEASE-SCHEDULE",
        "SRC-CFTC-COT-HISTORICAL-VIEWABLE",
        "SRC-CME-CRYPTO-24X7-LAUNCH-2026",
        "SRC-CME-GLOBEX-CRYPTO-24X7-20260525",
        "SRC-CME-CRYPTO-24X7-REGIME-2026",
    }
    official_rows = [row for row in metadata_rows if row["source_id"] in official_ids]
    assert len(official_rows) == len(official_ids)
    assert all(
        row["metadata_status"] == "OFFICIAL_PRIMARY_SOURCE"
        for row in official_rows
    )
    unverified_ids = {
        "SRC-DYNAMIC-CRYPTO-TSMOM-2021",
        "SRC-CRYPTO-MOMENTUM-REVERSAL-2021",
        "SRC-CRYPTO-FUTURES-RISK-FACTORS-2023",
    }
    unverified_rows = [
        row for row in metadata_rows if row["source_id"] in unverified_ids
    ]
    assert len(unverified_rows) == len(unverified_ids)
    assert all(
        row["metadata_status"] == "REGISTERED_FROM_V10_SPEC_UNVERIFIED"
        for row in unverified_rows
    )
    assert not any(row["url"] for row in unverified_rows)


def test_cme_weekend_gap_is_machine_readable_rejected_obsolete_regime() -> None:
    manifest = v10_candidate_manifest(source_commit="2" * 40)
    rejected = manifest["rejected_hypotheses"]

    assert isinstance(rejected, list)
    assert len(rejected) == 1
    contract = rejected[0]
    assert contract["hypothesis_id"] == "CME_WEEKEND_GAP_FILL"
    assert contract["status"] == "REJECTED"
    assert contract["reason"] == "OBSOLETE_REGIME"
    assert contract["readiness"] == "OBSOLETE_REGIME"
    assert contract["cutover_local_ts"] == "2026-05-29T16:00:00"
    assert contract["cutover_timezone"] == "America/Chicago"
    assert contract["pre_post_cutover_mixing_allowed"] is False
    assert contract["post_cutover_runtime_entry_registered"] is False
    assert contract["weekly_maintenance_min_hours"] == 2
    assert contract["weekend_trade_date_is_next_business_day"] is True
    assert not any(
        "WEEKEND_GAP" in candidate_id for candidate_id in EXPECTED_CONTRACT
    )


def test_manifest_is_deterministic_and_preserves_counts_and_false_flags() -> None:
    first = v10_candidate_manifest(source_commit="a" * 40)
    second = v10_candidate_manifest(source_commit="a" * 40)

    assert first == second
    assert first["candidate_count"] == 6
    assert first["research_enabled_count"] == 0
    assert first["default_visible_count"] == 0
    assert first["trial_ledger_included_count"] == 6
    assert first["final_ranking_eligible_count"] == 0
    assert first["direction_strategy_count"] == 3
    assert first["filter_count"] == 2
    assert first["market_neutral_strategy_count"] == 1
    assert first["runtime_entry_registered_count"] == 0
    assert first["active_count"] == 0
    assert first["entry_enabled_count"] == 0
    assert first["paper_only"] is True
    assert first["completed_bars_only"] is True
    assert first["max_hold_safety_review_only"] is True
    assert first["initial_stop_never_widens"] is True
    assert first["funding_readiness"] == "NOT_READY"
    false_flags = (
        "real_orders_enabled",
        "real_order_endpoints_enabled",
        "auth_required",
        "private_api_enabled",
        "api_key_enabled",
        "secret_enabled",
        "login_enabled",
        "real_account_access_enabled",
        "wallet_enabled",
        "transfer_enabled",
        "tradingview_webhook_orders_enabled",
        "runtime_ai_order_decision_enabled",
        "averaging_down_enabled",
        "martingale_enabled",
        "pyramiding_enabled",
        "automatic_risk_increase_enabled",
    )
    assert all(first[field] is False for field in false_flags)

    unhashed = dict(first)
    digest = str(unhashed.pop("manifest_sha256"))
    canonical = json.dumps(
        unhashed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert digest == hashlib.sha256(canonical.encode()).hexdigest()


def test_candidate_rejects_empty_id_duplicate_id_and_unsafe_mutations() -> None:
    rows = v10_candidate_specs()

    with pytest.raises(ValueError, match="후보 ID"):
        replace(rows[0], candidate_id="")
    duplicate = replace(rows[1], candidate_id=rows[0].candidate_id)
    with pytest.raises(ValueError, match="중복"):
        v10_candidates._validate_candidate_set((rows[0], duplicate, *rows[2:]))
    for unsafe_change in (
        {"user_visible_by_default": True},
        {"runtime_entry_registered": True},
        {"active_enabled": True},
        {"can_widen_stop": True},
        {"completed_bars_only": False},
        {"max_hold_safety_review_only": False},
        {"averaging_down": True},
        {"martingale": True},
        {"pyramiding": True},
        {"real_orders_enabled": True},
    ):
        with pytest.raises(ValueError):
            replace(rows[0], **unsafe_change)
