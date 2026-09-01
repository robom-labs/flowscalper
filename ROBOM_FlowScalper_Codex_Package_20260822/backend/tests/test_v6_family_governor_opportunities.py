# V6 family, governor, 순수 filter, 고유기회 집계 계약을 검증한다.

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from threading import Barrier, BrokenBarrierError

import pytest

from backend.app.analytics.opportunities import (
    group_trade_opportunities,
    wilson_lower_bound,
)
from backend.app.analytics.reports import TradeAnalytics
from backend.app.candidates import SharedCapitalArbitrationEvidence, TakeProfitTarget
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import RuntimeMode, Side
from backend.app.execution.portfolio import PaperPortfolioEngine
from backend.app.research.gates import (
    EvidenceEpoch,
    EvidenceHorizon,
    EvidenceSample,
)
from backend.app.runtime import PaperRuntime
from backend.app.strategies.family import (
    FAMILY_CATALOG,
    ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
    ORDERFLOW_CONFIRMATION_LEGACY_COMPONENT_IDS,
    STRATEGY_VARIANT_CONTRACTS,
    VIRTUAL_VARIANT_SUPERSESSION_CONTRACTS,
    StrategyFamilyId,
    StrategyRole,
    family_detail,
    strategy_family_catalog,
    validate_family_contract,
    validate_variant_contracts,
)
from backend.app.strategies.governor import GovernanceEvidence, StrategyGovernor
from backend.app.strategies.orderflow_confirmation import (
    ORDERFLOW_CONFIRMATION_FILTER_ID,
    OrderflowConfirmationInputs,
    evaluate_orderflow_confirmation,
)
from backend.app.strategies.registry import (
    StrategyChangeSource,
    StrategyLifecycle,
    StrategyMode,
    StrategyRegistry,
    StrategyRevisionConflict,
)
from backend.app.strategies.shadow import ShadowLedger
from backend.tests.test_candidate_paper_portfolio import candidate_plan
from scripts.build_v6_conflict_matrix import build_conflict_matrix

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EVIDENCE_HASH = "a" * 64


def _evidence_epoch(*, strategy_version: str = "v9-current") -> EvidenceEpoch:
    return EvidenceEpoch(
        epoch_id="EPOCH-V9-CURRENT",
        opened_ts_ms=0,
        closed_ts_ms=None,
        strategy_version=strategy_version,
        feature_version="feature-v9",
        label_version="label-v9",
        engine_version="engine-v9",
        cost_model_version="cost-v9",
        cost_profile="BASE_STRESS",
        parameter_hash=_EVIDENCE_HASH,
        dataset_hash=_EVIDENCE_HASH,
        fee_model_version="fee-v9",
        matching_model_version="matching-v9",
        symbol_contract_version="symbol-v9",
        data_adapter_version="adapter-v9",
        hypothesis_registry_hash=_EVIDENCE_HASH,
        hypothesis_key_fingerprint=_EVIDENCE_HASH,
    )


def _freshness_inputs(
    *,
    horizon: EvidenceHorizon,
    sample_count: int,
    assessment_ts_ms: int = 1_000,
    sample_strategy_version: str = "v9-current",
) -> dict[str, object]:
    samples = tuple(
        EvidenceSample(
            opportunity_id=f"OPP-{index:04d}",
            observed_ts_ms=assessment_ts_ms,
            evidence_epoch_id="EPOCH-V9-CURRENT",
            strategy_version=sample_strategy_version,
        )
        for index in range(sample_count)
    )
    return {
        "evidence_samples": samples,
        "evidence_epoch": _evidence_epoch(),
        "evidence_horizon": horizon,
    }


def _evidence(**overrides: object) -> GovernanceEvidence:
    values: dict[str, object] = {
        "base_sample_size": 150,
        "stress_sample_size": 150,
        "base_expectancy_usdt": Decimal("0.20"),
        "stress_expectancy_usdt": Decimal("0.10"),
        "base_profit_factor": Decimal("1.30"),
        "stress_profit_factor": Decimal("1.30"),
        "sample_span_days": 8,
        "regime_count": 2,
        "dsr_probability": 0.95,
        "pbo": 0.20,
        "oos_expectancy_lower_bound_usdt": Decimal("0.01"),
        "parameter_robustness_passed": True,
        "risk_contract_passed": True,
        "independent_period_count": 2,
        "live_public_sample_size": 150,
        "cooldown_elapsed": True,
        "unique_opportunity_count": 150,
        "base_win_rate": Decimal("0.45"),
        "stress_win_rate": Decimal("0.45"),
        "base_win_rate_ci95_lower": Decimal("0.40"),
        "stress_win_rate_ci95_lower": Decimal("0.40"),
        "base_payoff_ratio": Decimal("2.10"),
        "stress_payoff_ratio": Decimal("2.10"),
        "base_return_skew": Decimal("0.20"),
        "stress_return_skew": Decimal("0.20"),
        "base_largest_trade_contribution": Decimal("0.09"),
        "stress_largest_trade_contribution": Decimal("0.09"),
        "base_cost_coverage": Decimal("4.0"),
        "stress_cost_coverage": Decimal("4.0"),
        "operational_health_passed": True,
        "operational_health_evaluated_ts_ms": 1_000,
        "evaluation_period": "V6_FIXED_OOS_TEST_PERIOD",
        "evaluated_ts_ms": 1_000,
    }
    values.update(overrides)
    return GovernanceEvidence(**values)  # type: ignore[arg-type]


def _challenger_registry(
    strategy_id: str = "BREAKOUT_RETEST_30M_V2",
) -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.restore_setting(
        strategy_id,
        mode=StrategyMode.SHADOW,
        lifecycle=StrategyLifecycle.CHALLENGER,
        long_enabled=True,
        short_enabled=True,
        revision=1,
        manual_lock=False,
        changed_by=StrategyChangeSource.RECOVERY,
        change_reason="TEST_PROVEN_CHALLENGER",
        updated_ts_ms=1_000,
    )
    return registry


def _active_promotion_evidence(**overrides: object) -> GovernanceEvidence:
    return _evidence(
        sample_span_days=30,
        regime_count=3,
        independent_period_count=3,
        strategy_correlation_abs=0.40,
        **overrides,
    )


def test_eight_family_catalog_maps_every_existing_strategy_once() -> None:
    registry = StrategyRegistry()
    validation = validate_family_contract(registry)

    assert len(FAMILY_CATALOG) == 8
    assert validation["strategy_count"] == len(registry.strategy_ids) == 15
    mapped_families = {
        descriptor.family_id for descriptor in map(registry.descriptor, registry.strategy_ids)
    }
    assert mapped_families == {
        StrategyFamilyId.TREND_PULLBACK,
        StrategyFamilyId.BREAKOUT_RUNNER,
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        StrategyFamilyId.EXHAUSTION_REVERSION,
    }
    for family_id in StrategyFamilyId:
        current = [
            registry.descriptor(strategy_id)
            for strategy_id in registry.strategy_ids
            if registry.descriptor(strategy_id).family_id is family_id
            and registry.descriptor(strategy_id).is_current_variant
        ]
        assert len(current) <= 1
    assert all(
        not descriptor.user_visible_by_default and not descriptor.final_ranking_eligible
        for descriptor in map(registry.descriptor, registry.strategy_ids)
        if descriptor.role is StrategyRole.LEGACY
    )
    assert all(
        {
            "family_id",
            "role",
            "variant_id",
            "variant_label_ko",
            "is_current_variant",
            "supersedes_strategy_ids",
            "superseded_by_strategy_id",
            "user_visible_by_default",
            "default_research_enabled",
            "final_ranking_eligible",
        }
        <= row.keys()
        for row in registry.rows()
    )
    assert all(
        descriptor.final_ranking_eligible
        == (descriptor.role is StrategyRole.ENTRY and descriptor.is_current_variant)
        for descriptor in map(registry.descriptor, registry.strategy_ids)
    )


def test_conflict_matrix_covers_all_15_registry_strategies_and_105_pairs() -> None:
    registry = StrategyRegistry()
    document = build_conflict_matrix(registry)
    strategy_ids = tuple(sorted(registry.strategy_ids))
    expected_pairs = set(combinations(strategy_ids, 2))
    pairs = document["pairs"]
    assert isinstance(pairs, list)
    actual_pairs = {(row["strategy_a"], row["strategy_b"]) for row in pairs}

    assert document["strategy_count"] == 15
    assert document["expected_unordered_pair_count"] == 105
    assert document["actual_unordered_pair_count"] == 105
    assert len(pairs) == 105
    assert actual_pairs == expected_pairs
    assert document["coverage"]["unordered_pairs_complete"] is True  # type: ignore[index]
    assert document["invariants"]["pair_count_is_105"] is True  # type: ignore[index]


def test_conflict_matrix_rows_have_required_evidence_without_invented_correlation() -> None:
    pairs = build_conflict_matrix()["pairs"]
    assert isinstance(pairs, list)
    required = {
        "strategy_a",
        "strategy_b",
        "same_family",
        "same_horizon",
        "same_symbol",
        "same_side_signal_overlap",
        "opposite_side_overlap",
        "PnL_correlation",
        "shared_features",
        "shared_exit",
        "resource_cost",
        "conflict_policy",
    }
    for row in pairs:
        assert required <= row.keys()
        assert row["same_symbol"] is True
        assert row["same_symbol_scope"]["observed_concurrent_symbol_overlap"] is None
        assert row["same_side_signal_overlap"]["value"] is None
        assert row["same_side_signal_overlap"]["evidence_status"] == "NOT_RUN"
        assert row["opposite_side_overlap"]["value"] is None
        assert row["opposite_side_overlap"]["evidence_status"] == "NOT_RUN"
        assert row["PnL_correlation"]["value"] is None
        assert row["PnL_correlation"]["evidence_status"] == "NOT_RUN"
        assert row["PnL_correlation"]["sample_size"] == 0
        assert row["resource_cost"]["latency_or_profitability_estimate"] is None


def test_conflict_matrix_derives_family_current_legacy_and_account_policies() -> None:
    pairs = build_conflict_matrix()["pairs"]
    assert isinstance(pairs, list)
    by_pair = {(row["strategy_a"], row["strategy_b"]): row for row in pairs}

    same_family = by_pair[("BREAKOUT_RETEST_15M_V2", "BREAKOUT_RETEST_30M_V2")]
    assert same_family["same_family"] is True
    assert (
        same_family["conflict_policy"]["shared_capital"]["code"]
        == "SAME_FAMILY_CURRENT_VARIANT_ONLY"
    )
    assert same_family["conflict_policy"]["shared_capital"]["eligible_strategy_ids_in_pair"] == [
        "BREAKOUT_RETEST_30M_V2"
    ]
    assert same_family["conflict_policy"]["strategy_league"][
        "independent_account_strategy_ids"
    ] == ["BREAKOUT_RETEST_15M_V2", "BREAKOUT_RETEST_30M_V2"]
    assert same_family["conflict_policy"]["strategy_league"][
        "user_default_visible_strategy_ids"
    ] == ["BREAKOUT_RETEST_30M_V2"]

    different_current = by_pair[
        ("BREAKOUT_RETEST_30M_V2", "TREND_PULLBACK_RECLAIM_15M_V2")
    ]
    assert (
        different_current["conflict_policy"]["shared_capital"]["code"]
        == "DIFFERENT_FAMILY_SINGLE_WINNER_BY_EVIDENCE"
    )
    assert different_current["conflict_policy"]["shared_capital"][
        "raw_win_rate_priority_forbidden"
    ] is True

    legacy = by_pair[("AGGRESSOR_FLOW_CONTINUATION_V1", "BOOK_SLOPE_ASYMMETRY_V1")]
    assert (
        legacy["conflict_policy"]["shared_capital"]["code"]
        == "LEGACY_HISTORY_ONLY_NO_NEW_ENTRY"
    )
    assert legacy["conflict_policy"]["legacy_policy"]["new_entry_forbidden"] is True

    mixed = by_pair[("BREAKOUT_RETEST_30M_V2", "HOURLY_MOMENTUM_BREAKOUT_V1")]
    assert (
        mixed["conflict_policy"]["shared_capital"]["code"]
        == "CURRENT_ENTRY_WITH_LEGACY_HISTORY_ONLY"
    )
    assert mixed["conflict_policy"]["shared_capital"][
        "eligible_strategy_ids_in_pair"
    ] == ["BREAKOUT_RETEST_30M_V2"]
    assert mixed["conflict_policy"]["legacy_policy"][
        "new_entry_forbidden_strategy_ids"
    ] == ["HOURLY_MOMENTUM_BREAKOUT_V1"]

    assert different_current["resource_cost"][
        "strategy_league_independent_paper_accounts"
    ] == 4
    assert different_current["resource_cost"][
        "strategy_league_entry_enabled_paper_accounts"
    ] == 4


def test_conflict_matrix_evidence_is_reproducible_from_current_registry() -> None:
    evidence_path = PROJECT_ROOT / "evidence" / "V6_STRATEGY_CONFLICT_MATRIX.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    assert evidence == build_conflict_matrix()
    assert evidence["policy_contract"]["filter"] == {
        "account_creation": "FORBIDDEN",
        "candidate_plan_forbidden": True,
        "evaluation_metric": "filter_uplift",
        "trade_count_forbidden": True,
    }
    virtual_filter = evidence["coverage"]["virtual_filter_contract"]
    assert virtual_filter == {
        "strategy_id": ORDERFLOW_CONFIRMATION_FILTER_ID,
        "included_in_registry_pair_matrix": False,
        "creates_candidate_plan": False,
        "trade_count_delta": 0,
        "account_count_delta": 0,
        "evidence_status": "DERIVED_RUNTIME_FILTER_CONTRACT",
    }
    assert evidence["invariants"]["filter_never_creates_candidate_plan"] is True


def test_family_contract_rejects_two_current_or_default_on_superseded_variant() -> None:
    contracts = tuple(STRATEGY_VARIANT_CONTRACTS.values())
    duplicate_current = tuple(
        replace(contract, is_current_variant=True, user_visible_by_default=True)
        if contract.strategy_id == "CBR_CONTINUATION_V1"
        else contract
        for contract in contracts
    )
    with pytest.raises(ValueError, match="current variant"):
        validate_variant_contracts(duplicate_current)

    superseded_default_on = tuple(
        replace(contract, superseded_by_strategy_id="BREAKOUT_RETEST_30M_V2")
        if contract.strategy_id == "CBR_CONTINUATION_V1"
        else contract
        for contract in contracts
    )
    with pytest.raises(ValueError, match="superseded"):
        validate_variant_contracts(superseded_default_on)


def test_only_implemented_orderflow_filter_has_bidirectional_supersession() -> None:
    assert ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID == ORDERFLOW_CONFIRMATION_FILTER_ID
    assert VIRTUAL_VARIANT_SUPERSESSION_CONTRACTS == {
        ORDERFLOW_CONFIRMATION_FILTER_ID: ORDERFLOW_CONFIRMATION_LEGACY_COMPONENT_IDS,
    }
    for strategy_id in ORDERFLOW_CONFIRMATION_LEGACY_COMPONENT_IDS:
        contract = STRATEGY_VARIANT_CONTRACTS[strategy_id]
        assert contract.role is StrategyRole.LEGACY
        assert contract.superseded_by_strategy_id == ORDERFLOW_CONFIRMATION_FILTER_ID
        assert contract.user_visible_by_default is False
        assert contract.default_research_enabled is False
        assert contract.final_ranking_eligible is False

    truthful_non_lineage = {
        "LSA_REVERSAL_V1",
        "HOURLY_MOMENTUM_BREAKOUT_V1",
        "CBR_CONTINUATION_V1",
        "BREAKOUT_RETEST_15M_V2",
        "MULTISPEED_TREND_RECLAIM_30M_V2",
        "TREND_PULLBACK_RECLAIM_15M_V2",
        "BREAKOUT_RETEST_30M_V2",
        "VWAP_EXHAUSTION_REVERSION_V1",
    }
    assert all(
        STRATEGY_VARIANT_CONTRACTS[strategy_id].superseded_by_strategy_id is None
        for strategy_id in truthful_non_lineage
    )
    assert not any("_V3" in strategy_id for strategy_id in STRATEGY_VARIANT_CONTRACTS)


def test_virtual_filter_supersession_rejects_missing_reverse_link() -> None:
    contracts = tuple(
        replace(contract, superseded_by_strategy_id=None)
        if contract.strategy_id == ORDERFLOW_CONFIRMATION_LEGACY_COMPONENT_IDS[0]
        else contract
        for contract in STRATEGY_VARIANT_CONTRACTS.values()
    )

    with pytest.raises(ValueError, match="virtual strategy supersession"):
        validate_variant_contracts(contracts)


def test_legacy_components_are_off_and_recovery_migrates_old_shadow_setting() -> None:
    registry = StrategyRegistry()
    legacy_id = "AGGRESSOR_FLOW_CONTINUATION_V1"

    assert registry.setting(legacy_id).mode is StrategyMode.OFF
    assert registry.setting(legacy_id).lifecycle is StrategyLifecycle.RESEARCH
    with pytest.raises(ValueError, match="legacy"):
        registry.configure(
            legacy_id,
            mode=StrategyMode.SHADOW,
            lifecycle=StrategyLifecycle.SHADOW,
            long_enabled=True,
            short_enabled=True,
        )

    registry.restore_setting(
        legacy_id,
        mode=StrategyMode.SHADOW,
        lifecycle=StrategyLifecycle.SHADOW,
        long_enabled=True,
        short_enabled=True,
        revision=7,
        manual_lock=False,
        changed_by=StrategyChangeSource.MIGRATION,
        change_reason="V5_RECOVERY",
        updated_ts_ms=1_000,
    )
    migrations = registry.enforce_v6_family_runtime_policy(updated_ts_ms=2_000)

    assert len(migrations) == 1
    assert migrations[0]["strategy_id"] == legacy_id
    assert migrations[0]["mode"] == "OFF"
    assert migrations[0]["lifecycle"] == "RESEARCH"
    assert migrations[0]["settings_revision"] == 8
    with pytest.raises(ValueError, match="legacy"):
        registry.rollback(
            legacy_id,
            target_revision=7,
            expected_revision=8,
            source=StrategyChangeSource.USER_UI,
            reason="USER_LEGACY_ROLLBACK_BLOCK_TEST",
            updated_ts_ms=3_000,
        )
    assert registry.setting(legacy_id).revision == 8
    assert registry.evaluation_enabled(legacy_id, Side.LONG) is False


def test_strategy_configuration_cas_allows_only_one_same_revision_writer() -> None:
    registry = StrategyRegistry()
    strategy_id = "TREND_PULLBACK_RECLAIM_15M_V2"
    expected_revision = registry.setting(strategy_id).revision
    original_mode_for_lifecycle = registry.mode_for_lifecycle
    writers_after_revision_check = Barrier(2)

    def delayed_mode_for_lifecycle(lifecycle: StrategyLifecycle) -> StrategyMode:
        try:
            writers_after_revision_check.wait(timeout=0.2)
        except BrokenBarrierError:
            pass
        return original_mode_for_lifecycle(lifecycle)

    registry.mode_for_lifecycle = delayed_mode_for_lifecycle  # type: ignore[method-assign]

    def mutate(mode: StrategyMode, lifecycle: StrategyLifecycle) -> str:
        try:
            registry.configure(
                strategy_id,
                mode=mode,
                lifecycle=lifecycle,
                long_enabled=True,
                short_enabled=True,
                expected_revision=expected_revision,
            )
        except StrategyRevisionConflict:
            return "CONFLICT"
        return "OK"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            future.result()
            for future in (
                pool.submit(mutate, StrategyMode.SHADOW, StrategyLifecycle.SHADOW),
                pool.submit(mutate, StrategyMode.OFF, StrategyLifecycle.RESEARCH),
            )
        )

    assert sorted(outcomes) == ["CONFLICT", "OK"]
    assert registry.setting(strategy_id).revision == expected_revision + 1


def test_family_catalog_helpers_merge_optional_performance_and_governance() -> None:
    registry = StrategyRegistry()
    catalog = strategy_family_catalog(
        registry,
        {"BREAKOUT_RETEST_30M_V2": {"sample_size": 150}},
        {"BREAKOUT_RETEST_30M_V2": {"status": "PASSED"}},
    )
    detail = family_detail(registry, StrategyFamilyId.BREAKOUT_RUNNER)

    assert len(catalog) == 8
    breakout = next(row for row in catalog if row["family_id"] == "BREAKOUT_RUNNER")
    current = next(
        row
        for row in breakout["variants"]
        if row["strategy_id"] == "BREAKOUT_RETEST_30M_V2"  # type: ignore[index]
    )
    assert current["performance"] == {"sample_size": 150}
    assert current["governance"] == {"status": "PASSED"}
    assert detail["label_ko"] == "돌파·큰 추세"


def test_orderflow_filter_returns_score_only_and_never_candidate_plan() -> None:
    inputs = OrderflowConfirmationInputs(
        normalized_ofi=Decimal("0.8"),
        aggressor_imbalance=Decimal("0.8"),
        microprice_displacement=Decimal("0.8"),
        multilevel_fair_price_displacement=Decimal("0.8"),
        queue_imbalance=Decimal("0.8"),
        book_slope=Decimal("0.8"),
        depth_adjusted_price_response=Decimal("0.8"),
        spread_health=Decimal("0.8"),
        book_resilience=Decimal("0.8"),
    )

    decision = evaluate_orderflow_confirmation(inputs, persistence_ms=500)
    waiting = evaluate_orderflow_confirmation(inputs, persistence_ms=499)

    assert decision.role is StrategyRole.FILTER
    assert decision.score == Decimal("0.800")
    assert decision.passed_component_count == 7
    assert decision.allowed is True
    assert decision.creates_candidate_plan is False
    assert waiting.allowed is False
    assert waiting.reason_codes == ("ORDERFLOW_PERSISTENCE_LT_500_MS",)
    assert not hasattr(decision, "candidate_plan")


def test_opportunity_grouping_deduplicates_profiles_partial_exits_and_versions() -> None:
    common = {
        "run_id": "run-1",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "opportunity_id": "opportunity-1",
        "symbol": "BTCUSDT",
        "side": "LONG",
    }
    rows = [
        {**common, "strategy_version": "V2", "profile": "BASE", "exit_leg": "TP1"},
        {**common, "strategy_version": "V2", "profile": "BASE", "exit_leg": "TP2"},
        {**common, "strategy_version": "V2", "profile": "STRESS", "exit_leg": "STOP"},
        {**common, "strategy_version": "V1", "profile": "BASE", "exit_leg": "TP1"},
    ]

    all_versions = group_trade_opportunities(rows)
    current = group_trade_opportunities(rows, strategy_version="V2")

    assert all_versions.unique_opportunity_count == 2
    assert current.unique_opportunity_count == 1
    assert current.raw_result_row_count == 3
    assert current.base_result_row_count == 2
    assert current.stress_result_row_count == 1
    assert current.groups[0].profiles == ("BASE", "STRESS")
    assert current.groups[0].partial_exit_row_count == 1


def test_opportunity_grouping_keeps_main_and_league_accounts_independent() -> None:
    common = {
        "run_id": "run-account-isolation",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": "opportunity-account-isolation",
        "symbol": "BTCUSDT",
        "side": "LONG",
    }
    rows = [
        {
            **common,
            "trade_id": "main-base-tp1",
            "account_scope": "MAIN",
            "account_id": "SHARED_PAPER",
            "profile": "BASE",
        },
        {
            **common,
            "trade_id": "main-base-tp2",
            "account_scope": "MAIN",
            "account_id": "SHARED_PAPER",
            "profile": "BASE",
        },
        {
            **common,
            "trade_id": "league-base",
            "account_scope": "LEAGUE",
            "account_id": "BREAKOUT_RETEST_30M_V2:BASE",
            "profile": "BASE",
        },
        {
            **common,
            "trade_id": "league-stress",
            "account_scope": "LEAGUE",
            "account_id": "BREAKOUT_RETEST_30M_V2:STRESS",
            "profile": "STRESS",
        },
    ]

    grouping = group_trade_opportunities(rows)

    assert grouping.unique_opportunity_count == 1
    assert grouping.raw_result_row_count == 4
    assert grouping.groups[0].key.as_tuple() == (
        "run-account-isolation",
        "BREAKOUT_RETEST_30M_V2",
        "V2",
        "opportunity-account-isolation",
        "BTCUSDT",
        "LONG",
    )
    assert grouping.groups[0].partial_exit_row_count == 1
    assert [
        (account.account_scope, account.account_id, account.partial_exit_row_count)
        for account in grouping.groups[0].accounts
    ] == [
        ("MAIN", "SHARED_PAPER", 1),
        ("LEAGUE", "BREAKOUT_RETEST_30M_V2:BASE", 0),
        ("LEAGUE", "BREAKOUT_RETEST_30M_V2:STRESS", 0),
    ]


def test_opportunity_grouping_quarantines_unlinked_legacy_rows() -> None:
    legacy_row = {
        "run_id": "run-legacy",
        "trade_id": "legacy-unknown",
        "strategy_id": "UNKNOWN",
        "strategy_version": "UNKNOWN",
        "opportunity_id": None,
        "candidate_id": None,
        "signal_event_id": None,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "profile": "BASE",
    }

    grouping = group_trade_opportunities([legacy_row])

    assert grouping.unique_opportunity_count == 0
    assert grouping.raw_result_row_count == 0
    assert grouping.unresolved_result_row_count == 1
    assert grouping.unresolved_rows[0].status == "NOT_PROVEN"
    assert grouping.unresolved_rows[0].reason_code == ("MISSING_VERIFIABLE_OPPORTUNITY_LINKAGE")


def test_opportunity_grouping_quarantines_mismatched_account_identity() -> None:
    row = {
        "run_id": "run-account-mismatch",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": "opportunity-account-mismatch",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "account_scope": "LEAGUE",
        "account_id": "BREAKOUT_RETEST_30M_V2:STRESS",
        "profile": "BASE",
    }

    grouping = group_trade_opportunities([row])

    assert grouping.unique_opportunity_count == 0
    assert grouping.unresolved_result_row_count == 1
    assert grouping.unresolved_rows[0].reason_code == "INVALID_ACCOUNT_IDENTITY"


def test_opportunity_grouping_quarantines_unknown_cost_profile() -> None:
    row = {
        "run_id": "run-invalid-profile",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": "opportunity-invalid-profile",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "account_scope": "LEAGUE",
        "account_id": "BREAKOUT_RETEST_30M_V2:FOO",
        "profile": "FOO",
    }

    grouping = group_trade_opportunities([row])

    assert grouping.unique_opportunity_count == 0
    assert grouping.unresolved_result_row_count == 1
    assert grouping.unresolved_rows[0].reason_code == "INVALID_COST_PROFILE"


def test_strategy_report_excludes_unlinked_rows_from_governor_samples() -> None:
    row = {
        "trade_id": "unlinked-result",
        "run_id": "run-unlinked",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": None,
        "candidate_id": None,
        "signal_event_id": None,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "venue": "BINANCE_USDM",
        "regime": "TREND_UP",
        "profile": "BASE",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "entry_price": "100",
        "exit_price": "110",
        "initial_stop": "99",
        "quantity": "1",
        "holding_ms": 1_000,
        "gross_pnl_usdt": "10",
        "fees_usdt": "0.1",
        "slippage_usdt": "0.1",
        "net_pnl_usdt": "9.8",
    }

    base = next(
        report for report in TradeAnalytics().strategy_reports([row]) if report["profile"] == "BASE"
    )

    assert base["sample_size"] == 0
    assert base["unique_opportunity_count"] == 0
    assert base["unresolved_ledger_row_count"] == 1
    assert base["profile_unresolved_ledger_row_count"] == 1
    assert base["opportunity_grouping_status"] == "NOT_PROVEN"


def test_strategy_report_separates_raw_and_resolved_ledger_rows() -> None:
    common = {
        "run_id": "run-ledger-counts",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "venue": "BINANCE_USDM",
        "regime": "TREND_UP",
        "profile": "BASE",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "entry_price": "100",
        "exit_price": "110",
        "initial_stop": "99",
        "quantity": "1",
        "holding_ms": 1_000,
        "gross_pnl_usdt": "10",
        "fees_usdt": "0.1",
        "slippage_usdt": "0.1",
        "net_pnl_usdt": "9.8",
    }
    rows = [
        {
            **common,
            "trade_id": "resolved-result",
            "opportunity_id": "verified-opportunity",
        },
        {
            **common,
            "trade_id": "unresolved-legacy-result",
            "opportunity_id": None,
            "candidate_id": None,
            "signal_event_id": None,
        },
    ]

    base = next(
        report for report in TradeAnalytics().strategy_reports(rows) if report["profile"] == "BASE"
    )

    assert base["raw_ledger_row_count"] == 2
    assert base["resolved_ledger_row_count"] == 1
    assert base["raw_ledger_row_count"] > base["resolved_ledger_row_count"]
    assert base["unresolved_ledger_row_count"] == 1
    assert base["sample_size"] == 1
    assert base["opportunity_grouping_status"] == "NOT_PROVEN"


def test_strategy_report_does_not_invent_missing_exact_key_fields() -> None:
    row = {
        "trade_id": "candidate-with-missing-exact-fields",
        "candidate_id": "candidate-verified-link",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "opportunity_id": None,
        "signal_event_id": None,
        "venue": "BINANCE_USDM",
        "regime": "TREND_UP",
        "profile": "BASE",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "entry_price": "100",
        "exit_price": "110",
        "initial_stop": "99",
        "quantity": "1",
        "holding_ms": 1_000,
        "gross_pnl_usdt": "10",
        "fees_usdt": "0.1",
        "slippage_usdt": "0.1",
        "net_pnl_usdt": "9.8",
    }

    base = next(
        report for report in TradeAnalytics().strategy_reports([row]) if report["profile"] == "BASE"
    )

    assert base["sample_size"] == 0
    assert base["unique_opportunity_count"] == 0
    assert base["unresolved_ledger_row_count"] == 1
    assert base["profile_unresolved_ledger_row_count"] == 1
    assert base["opportunity_grouping_status"] == "NOT_PROVEN"


def test_strategy_report_rejects_mixed_main_and_league_accounts() -> None:
    common = {
        "run_id": "run-mixed-accounts",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": "opportunity-mixed-accounts",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "venue": "BINANCE_USDM",
        "regime": "TREND_UP",
        "profile": "BASE",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "entry_price": "100",
        "exit_price": "101",
        "initial_stop": "99",
        "quantity": "1",
        "holding_ms": 1_000,
        "gross_pnl_usdt": "1",
        "fees_usdt": "0",
        "slippage_usdt": "0",
    }
    rows = [
        {
            **common,
            "trade_id": "main-result",
            "account_scope": "MAIN",
            "account_id": "SHARED_PAPER",
            "net_pnl_usdt": "1",
        },
        {
            **common,
            "trade_id": "league-result",
            "account_scope": "LEAGUE",
            "account_id": "BREAKOUT_RETEST_30M_V2:BASE",
            "net_pnl_usdt": "2",
        },
    ]

    with pytest.raises(ValueError, match="독립 LEAGUE PAPER 계좌만 허용"):
        TradeAnalytics().strategy_reports(rows)


def test_strategy_report_quarantines_unknown_profile_from_global_sample() -> None:
    row = {
        "trade_id": "unknown-profile-result",
        "run_id": "run-unknown-profile",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": "opportunity-unknown-profile",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "account_scope": "LEAGUE",
        "account_id": "BREAKOUT_RETEST_30M_V2:FOO",
        "venue": "BINANCE_USDM",
        "regime": "TREND_UP",
        "profile": "FOO",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "entry_price": "100",
        "exit_price": "101",
        "initial_stop": "99",
        "quantity": "1",
        "holding_ms": 1_000,
        "gross_pnl_usdt": "1",
        "fees_usdt": "0",
        "slippage_usdt": "0",
        "net_pnl_usdt": "1",
    }

    reports = TradeAnalytics().strategy_reports([row])

    assert all(report["unique_opportunity_count"] == 0 for report in reports)
    assert all(report["sample_size"] == 0 for report in reports)
    assert all(report["unresolved_ledger_row_count"] == 1 for report in reports)
    assert all(report["opportunity_grouping_status"] == "NOT_PROVEN" for report in reports)


def test_strategy_symbol_report_counts_thirty_partial_exits_as_one_opportunity() -> None:
    common = {
        "run_id": "run-symbol-partial-exits",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": "opportunity-symbol-partial-exits",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "venue": "BINANCE_USDM",
        "regime": "TREND_UP",
        "profile": "BASE",
        "entry_ts_ms": 1_000,
        "entry_price": "100",
        "exit_price": "101",
        "initial_stop": "99",
        "quantity": "0.1",
        "holding_ms": 1_000,
        "gross_pnl_usdt": "1",
        "fees_usdt": "0.1",
        "slippage_usdt": "0.1",
        "net_pnl_usdt": "0.8",
    }
    rows = [
        {
            **common,
            "trade_id": f"partial-exit-{index:02d}",
            "exit_ts_ms": 2_000 + index,
        }
        for index in range(30)
    ]

    report = TradeAnalytics().strategy_symbol_reports(rows)[0]

    assert report["raw_ledger_row_count"] == 30
    assert report["resolved_ledger_row_count"] == 30
    assert report["unique_opportunity_count"] == 1
    assert report["sample_size"] == 1
    assert report["net_pnl"] == "24.0"
    assert report["ranking_eligible"] is False
    assert report["rank"] is None
    assert report["opportunity_grouping_status"] == "PROVEN"


def test_strategy_symbol_report_excludes_unlinked_rows_and_exposes_status() -> None:
    unlinked = {
        "trade_id": "symbol-unlinked-result",
        "run_id": "run-symbol-unlinked",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": None,
        "candidate_id": None,
        "signal_event_id": None,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "venue": "BINANCE_USDM",
        "regime": "TREND_UP",
        "profile": "BASE",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "entry_price": "100",
        "exit_price": "110",
        "initial_stop": "99",
        "quantity": "1",
        "holding_ms": 1_000,
        "gross_pnl_usdt": "10",
        "fees_usdt": "0.1",
        "slippage_usdt": "0.1",
        "net_pnl_usdt": "9.8",
    }

    report = TradeAnalytics().strategy_symbol_reports([unlinked])[0]

    assert report["raw_ledger_row_count"] == 1
    assert report["resolved_ledger_row_count"] == 0
    assert report["unresolved_ledger_row_count"] == 1
    assert report["unique_opportunity_count"] == 0
    assert report["sample_size"] == 0
    assert report["ranking_eligible"] is False
    assert report["rank"] is None
    assert report["opportunity_grouping_status"] == "NOT_PROVEN"


def test_wilson_lower_bound_is_null_for_zero_and_matches_known_interval() -> None:
    assert wilson_lower_bound(0, 0) is None
    lower = wilson_lower_bound(8, 10)
    assert lower is not None
    assert Decimal("0.49") < lower < Decimal("0.50")
    with pytest.raises(ValueError):
        wilson_lower_bound(2, 1)


def test_strategy_report_counts_unique_opportunity_and_collapses_partial_exits() -> None:
    common = {
        "run_id": "run-1",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": "opportunity-1",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "venue": "BINANCE_USDM",
        "regime": "TREND_UP",
        "entry_ts_ms": 1_000,
        "entry_price": "100",
        "initial_stop": "99",
        "exit_price": "102",
        "quantity": "0.5",
        "holding_ms": 1_000,
        "mae_r": "-0.2",
        "mfe_r": "2.0",
        "exit_reason": "TAKE_PROFIT",
    }
    rows = [
        {
            **common,
            "trade_id": "base-tp1",
            "profile": "BASE",
            "exit_ts_ms": 2_000,
            "gross_pnl_usdt": "1.0",
            "fees_usdt": "0.1",
            "slippage_usdt": "0.1",
            "net_pnl_usdt": "0.8",
        },
        {
            **common,
            "trade_id": "base-tp2",
            "profile": "BASE",
            "exit_ts_ms": 3_000,
            "gross_pnl_usdt": "1.5",
            "fees_usdt": "0.1",
            "slippage_usdt": "0.1",
            "net_pnl_usdt": "1.3",
        },
        {
            **common,
            "trade_id": "stress-exit",
            "profile": "STRESS",
            "exit_ts_ms": 3_000,
            "gross_pnl_usdt": "2.5",
            "fees_usdt": "0.3",
            "slippage_usdt": "0.3",
            "net_pnl_usdt": "1.9",
        },
    ]

    reports = TradeAnalytics().strategy_reports(rows)
    base = next(row for row in reports if row["profile"] == "BASE")
    stress = next(row for row in reports if row["profile"] == "STRESS")

    assert base["unique_opportunity_count"] == 1
    assert stress["unique_opportunity_count"] == 1
    assert base["raw_ledger_row_count"] == 3
    assert base["profile_raw_ledger_row_count"] == 2
    assert base["profile_unique_opportunity_count"] == 1
    assert base["sample_size"] == 1
    assert base["net_pnl"] == "2.1"
    assert base["cost_coverage"] is None
    assert base["cost_coverage_status"] == "NOT_PROVEN_MISSING_EXPECTED_COST_MODEL"
    assert stress["sample_size"] == 1


def test_cost_coverage_requires_expected_mfe_and_total_cost_inputs() -> None:
    common = {
        "trade_id": "coverage-row",
        "run_id": "run-coverage",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": "coverage-opportunity",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "venue": "BINANCE_USDM",
        "regime": "TREND_UP",
        "profile": "BASE",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "entry_price": "100",
        "exit_price": "1",
        "initial_stop": "99",
        "quantity": "1",
        "holding_ms": 1_000,
        "gross_pnl_usdt": "-100",
        "fees_usdt": "0.5",
        "slippage_usdt": "0.5",
        "net_pnl_usdt": "-101",
    }

    missing = TradeAnalytics().strategy_reports([common])[0]
    proven = TradeAnalytics().strategy_reports(
        [
            {
                **common,
                "expected_gross_mfe_usdt": "8",
                "expected_total_cost_usdt": "2",
            }
        ]
    )[0]

    assert missing["cost_coverage"] is None
    assert missing["cost_coverage_status"] == "NOT_PROVEN_MISSING_EXPECTED_COST_MODEL"
    assert proven["cost_coverage"] == "4"
    assert proven["cost_coverage_status"] == "PROVEN_EXPECTED_MFE_OVER_TOTAL_COST"


@pytest.mark.parametrize(
    ("family_id", "evidence"),
    (
        (StrategyFamilyId.TREND_PULLBACK, _evidence()),
        (
            StrategyFamilyId.BREAKOUT_RUNNER,
            _evidence(base_win_rate=Decimal("0.20"), stress_win_rate=Decimal("0.20")),
        ),
        (StrategyFamilyId.EXHAUSTION_REVERSION, _evidence()),
        (
            StrategyFamilyId.ORDERFLOW_CONFIRMATION,
            _evidence(unique_opportunity_count=1_000),
        ),
    ),
)
def test_family_preregistered_gates_pass_at_required_boundaries(
    family_id: StrategyFamilyId,
    evidence: GovernanceEvidence,
) -> None:
    assert StrategyGovernor.family_gate_failures(family_id, evidence) == ()


def test_family_gates_report_threshold_specific_failures() -> None:
    assert StrategyGovernor.family_gate_failures(
        StrategyFamilyId.TREND_PULLBACK,
        _evidence(base_win_rate=Decimal("0.39")),
    ) == ("BASE_WIN_RATE_LT_0_40_OR_MISSING",)
    assert StrategyGovernor.family_gate_failures(
        StrategyFamilyId.BREAKOUT_RUNNER,
        _evidence(base_largest_trade_contribution=Decimal("0.10")),
    ) == ("BASE_LARGEST_TRADE_CONTRIBUTION_NOT_LT_0_10",)
    assert StrategyGovernor.family_gate_failures(
        StrategyFamilyId.EXHAUSTION_REVERSION,
        _evidence(stress_win_rate_ci95_lower=Decimal("0.37")),
    ) == ("STRESS_WILSON_LOWER_LT_0_38_OR_MISSING",)
    assert StrategyGovernor.family_gate_failures(
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        _evidence(unique_opportunity_count=999),
    ) == ("UNIQUE_OPPORTUNITIES_LT_1000",)


def test_low_win_high_payoff_breakout_is_not_retired_by_universal_gate() -> None:
    assessment = StrategyGovernor().assess(
        StrategyRegistry(),
        "CBR_CONTINUATION_V1",
        _evidence(base_win_rate=Decimal("0.20"), stress_win_rate=Decimal("0.20")),
        assessment_ts_ms=1_000,
    )

    assert assessment.current_lifecycle is StrategyLifecycle.SHADOW
    assert assessment.recommended_lifecycle is StrategyLifecycle.CHALLENGER
    assert not any("0_70" in reason or "BELOW_70" in reason for reason in assessment.reason_codes)


@pytest.mark.parametrize(
    "overrides",
    (
        {"operational_health_passed": None},
        {"operational_health_passed": "UNKNOWN"},
        {"operational_health_evaluated_ts_ms": None},
        {"evaluation_period": "   "},
        {"evaluation_period": "UNKNOWN"},
        {"evaluated_ts_ms": 0},
    ),
)
def test_governor_requires_current_explicit_operational_health_evidence(
    overrides: dict[str, object],
) -> None:
    assessment = StrategyGovernor().assess(
        StrategyRegistry(),
        "CBR_CONTINUATION_V1",
        _evidence(**overrides),
        assessment_ts_ms=1_000,
    )

    assert assessment.current_lifecycle is StrategyLifecycle.SHADOW
    assert assessment.recommended_lifecycle is StrategyLifecycle.SHADOW
    assert assessment.reason_codes == ("OPERATIONAL_HEALTH_NOT_PROVEN",)
    assert assessment.automatic_action_allowed is False


def test_from_reports_without_operational_payload_is_not_health_proof() -> None:
    evidence = GovernanceEvidence.from_reports(
        {"sample_size": 150, "expectancy_usdt": "0.20", "profit_factor": "1.30"},
        {"sample_size": 150, "expectancy_usdt": "0.10", "profit_factor": "1.30"},
        multiple_testing={
            "evaluation_period": "V6_FIXED_OOS_TEST_PERIOD",
            "evaluated_ts_ms": 1_000,
        },
    )

    assert evidence.operational_health_passed is None
    assessment = StrategyGovernor().assess(
        StrategyRegistry(),
        "CBR_CONTINUATION_V1",
        evidence,
        assessment_ts_ms=1_000,
    )
    assert "OPERATIONAL_HEALTH_NOT_PROVEN" in assessment.reason_codes
    assert assessment.automatic_action_allowed is False


def test_high_win_negative_expectancy_still_fails_common_gate() -> None:
    assessment = StrategyGovernor().assess(
        StrategyRegistry(),
        "CBR_CONTINUATION_V1",
        _evidence(
            base_win_rate=Decimal("0.90"),
            stress_win_rate=Decimal("0.90"),
            base_expectancy_usdt=Decimal("-0.01"),
        ),
        assessment_ts_ms=1_000,
    )

    assert assessment.recommended_lifecycle is StrategyLifecycle.SHADOW
    assert assessment.reason_codes == ("BASE_EXPECTANCY_NOT_POSITIVE",)


@pytest.mark.parametrize(
    ("timestamp_field", "evidence_ts_ms", "assessment_ts_ms"),
    (
        ("evaluated_ts_ms", 1, 60_002),
        ("operational_health_evaluated_ts_ms", 1, 60_002),
        ("evaluated_ts_ms", 1_001, 1_000),
        ("operational_health_evaluated_ts_ms", 1_001, 1_000),
    ),
)
def test_governor_rejects_stale_and_future_evidence_timestamps(
    timestamp_field: str,
    evidence_ts_ms: int,
    assessment_ts_ms: int,
) -> None:
    evidence = _evidence(**{timestamp_field: evidence_ts_ms})

    assessment = StrategyGovernor().assess(
        StrategyRegistry(),
        "CBR_CONTINUATION_V1",
        evidence,
        assessment_ts_ms=assessment_ts_ms,
    )

    assert assessment.recommended_lifecycle is StrategyLifecycle.SHADOW
    assert assessment.reason_codes == ("OPERATIONAL_HEALTH_NOT_PROVEN",)
    assert assessment.automatic_action_allowed is False


def test_governor_requires_assessment_timestamp_and_accepts_fresh_positive() -> None:
    evidence = _evidence()
    missing_timestamp = StrategyGovernor().assess(
        StrategyRegistry(),
        "CBR_CONTINUATION_V1",
        evidence,
    )
    fresh = StrategyGovernor().assess(
        StrategyRegistry(),
        "CBR_CONTINUATION_V1",
        evidence,
        assessment_ts_ms=1_000,
    )

    assert missing_timestamp.recommended_lifecycle is StrategyLifecycle.SHADOW
    assert missing_timestamp.reason_codes == ("OPERATIONAL_HEALTH_NOT_PROVEN",)
    assert missing_timestamp.automatic_action_allowed is False
    assert fresh.recommended_lifecycle is StrategyLifecycle.CHALLENGER
    assert fresh.reason_codes == ("SHADOW_GATES_PASSED",)
    assert fresh.automatic_action_allowed is True


@pytest.mark.parametrize(
    ("category", "required", "lookback_days"),
    (
        (EvidenceHorizon.FAST, 50, 90),
        (EvidenceHorizon.SWING, 30, 180),
        (EvidenceHorizon.MICRO, 200, 60),
        (EvidenceHorizon.MARKET_NEUTRAL, 20, 180),
    ),
)
def test_governor_blocks_active_promotion_below_v9_freshness_thresholds(
    category: EvidenceHorizon,
    required: int,
    lookback_days: int,
) -> None:
    registry = _challenger_registry()

    assessment = StrategyGovernor().assess(
        registry,
        "BREAKOUT_RETEST_30M_V2",
        _active_promotion_evidence(
            **_freshness_inputs(
                horizon=category,
                sample_count=required - 1,
            )
        ),
        assessment_ts_ms=1_000,
    )

    assert assessment.current_lifecycle is StrategyLifecycle.CHALLENGER
    assert assessment.recommended_lifecycle is StrategyLifecycle.CHALLENGER
    assert assessment.reason_codes == (
        "STALE_EVIDENCE",
        f"UNIQUE_SAMPLES_LT_{required}",
    )
    assert assessment.automatic_action_allowed is False
    assert assessment.evidence_freshness is not None
    assert assessment.evidence_freshness.window_days == lookback_days
    assert assessment.evidence_freshness.minimum_unique_samples == required
    assert (
        registry.setting("BREAKOUT_RETEST_30M_V2").lifecycle
        is StrategyLifecycle.CHALLENGER
    )


def test_governor_blocks_active_promotion_on_evidence_version_mismatch() -> None:
    registry = _challenger_registry()

    assessment = StrategyGovernor().assess(
        registry,
        "BREAKOUT_RETEST_30M_V2",
        _active_promotion_evidence(
            **_freshness_inputs(
                horizon=EvidenceHorizon.FAST,
                sample_count=1_000,
                sample_strategy_version="v9-old-strategy",
            )
        ),
        assessment_ts_ms=1_000,
    )

    assert assessment.recommended_lifecycle is StrategyLifecycle.CHALLENGER
    assert assessment.reason_codes == (
        "STALE_EVIDENCE",
        "EVIDENCE_STRATEGY_VERSION_MISMATCH",
    )
    assert assessment.automatic_action_allowed is False
    serialized = assessment.as_dict()["evidence_freshness"]
    assert isinstance(serialized, dict)
    assert serialized["promotion_allowed"] is False
    assert serialized["reason_codes"] == ["EVIDENCE_STRATEGY_VERSION_MISMATCH"]


def test_governor_rejects_aggregate_only_or_future_freshness_claims() -> None:
    aggregate_only = GovernanceEvidence.from_reports(
        {},
        {},
        multiple_testing={
            "evidence_category": "FAST",
            "observed_unique_opportunities": 50_000,
            "evidence_version": "v9-current",
            "current_evidence_version": "v9-current",
        },
    )
    assert aggregate_only.evidence_samples is None
    assert aggregate_only.evidence_epoch is None
    assert aggregate_only.evidence_horizon is None

    raw = _active_promotion_evidence(
        **_freshness_inputs(
            horizon=EvidenceHorizon.SWING,
            sample_count=30,
        )
    )
    restored_raw = GovernanceEvidence.from_reports(
        {},
        {},
        multiple_testing=raw.as_dict(),
    )
    assert restored_raw.evidence_samples == raw.evidence_samples
    assert restored_raw.evidence_epoch == raw.evidence_epoch
    assert restored_raw.evidence_horizon is EvidenceHorizon.SWING

    future_inputs = _freshness_inputs(
        horizon=EvidenceHorizon.FAST,
        sample_count=50,
        assessment_ts_ms=1_001,
    )
    future = StrategyGovernor().assess(
        _challenger_registry(),
        "BREAKOUT_RETEST_30M_V2",
        _active_promotion_evidence(**future_inputs),
        assessment_ts_ms=1_000,
    )
    aggregate = StrategyGovernor().assess(
        _challenger_registry(),
        "BREAKOUT_RETEST_30M_V2",
        _active_promotion_evidence(),
        assessment_ts_ms=1_000,
    )

    assert future.reason_codes == ("EVIDENCE_FRESHNESS_NOT_PROVEN",)
    assert aggregate.reason_codes == ("EVIDENCE_FRESHNESS_NOT_PROVEN",)
    assert future.automatic_action_allowed is aggregate.automatic_action_allowed is False


def test_governor_requires_freshness_evidence_and_allows_exact_boundary() -> None:
    missing = StrategyGovernor().assess(
        _challenger_registry(),
        "BREAKOUT_RETEST_30M_V2",
        _active_promotion_evidence(),
        assessment_ts_ms=1_000,
    )
    promoted = StrategyGovernor().assess(
        _challenger_registry(),
        "BREAKOUT_RETEST_30M_V2",
        _active_promotion_evidence(
            **_freshness_inputs(
                horizon=EvidenceHorizon.SWING,
                sample_count=30,
            )
        ),
        assessment_ts_ms=1_000,
    )

    assert missing.recommended_lifecycle is StrategyLifecycle.CHALLENGER
    assert missing.reason_codes == ("EVIDENCE_FRESHNESS_NOT_PROVEN",)
    assert missing.automatic_action_allowed is False
    assert promoted.recommended_lifecycle is StrategyLifecycle.ACTIVE
    assert promoted.reason_codes == ("CHALLENGER_BEATS_CHAMPION",)
    assert promoted.automatic_action_allowed is True
    assert promoted.evidence_freshness is not None
    assert promoted.evidence_freshness.promotion_allowed is True


def test_governor_retains_existing_active_when_freshness_is_stale() -> None:
    registry = StrategyRegistry()
    registry.restore_setting(
        "BREAKOUT_RETEST_30M_V2",
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        revision=1,
        manual_lock=False,
        changed_by=StrategyChangeSource.RECOVERY,
        change_reason="TEST_EXISTING_ACTIVE",
        updated_ts_ms=1_000,
    )
    history_before = registry.revision_history("BREAKOUT_RETEST_30M_V2")
    assessment = StrategyGovernor().assess(
        registry,
        "BREAKOUT_RETEST_30M_V2",
        _active_promotion_evidence(
            **_freshness_inputs(
                horizon=EvidenceHorizon.MICRO,
                sample_count=199,
            )
        ),
        assessment_ts_ms=1_000,
    )

    assert assessment.recommended_lifecycle is StrategyLifecycle.ACTIVE
    assert assessment.reason_codes == (
        "ACTIVE_GATES_HEALTHY",
        "STALE_EVIDENCE",
        "UNIQUE_SAMPLES_LT_200",
    )
    assert assessment.automatic_action_allowed is False
    assert assessment.evidence_freshness is not None
    assert assessment.evidence_freshness.promotion_allowed is False
    assert registry.setting("BREAKOUT_RETEST_30M_V2").mode is StrategyMode.ACTIVE
    assert registry.revision_history("BREAKOUT_RETEST_30M_V2") == history_before


def test_runtime_champion_expectancy_is_scoped_by_family() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    for strategy_id in ("BREAKOUT_RETEST_30M_V2", "VWAP_EXHAUSTION_REVERSION_V1"):
        runtime.strategy_registry.configure(
            strategy_id,
            mode=StrategyMode.ACTIVE,
            lifecycle=StrategyLifecycle.ACTIVE,
            long_enabled=True,
            short_enabled=True,
            source=StrategyChangeSource.MIGRATION,
        )
    reports = {
        ("BREAKOUT_RETEST_30M_V2", "BASE"): {"expectancy_usdt": "0.10"},
        ("VWAP_EXHAUSTION_REVERSION_V1", "BASE"): {"expectancy_usdt": "0.90"},
    }

    by_family = runtime._active_champion_expectancy_by_family(reports)

    assert by_family == {
        "BREAKOUT_RUNNER": "0.10",
        "EXHAUSTION_REVERSION": "0.90",
    }


def test_conflict_same_family_shared_capital_uses_current_variant_only() -> None:
    registry = StrategyRegistry()
    registry.configure(
        "TREND_PULLBACK_RECLAIM_15M_V2",
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        source=StrategyChangeSource.MIGRATION,
    )
    with pytest.raises(ValueError, match="non-current"):
        registry.configure(
            "MULTISPEED_TREND_RECLAIM_30M_V2",
            mode=StrategyMode.ACTIVE,
            lifecycle=StrategyLifecycle.ACTIVE,
            long_enabled=True,
            short_enabled=True,
            source=StrategyChangeSource.MIGRATION,
        )
    current = replace(
        candidate_plan(),
        candidate_id="trend-current",
        strategy_id="TREND_PULLBACK_RECLAIM_15M_V2",
        data_quality=Decimal("0.5"),
        main_eligible=registry.main_enabled("TREND_PULLBACK_RECLAIM_15M_V2", Side.LONG),
    )
    challenger = replace(
        candidate_plan(),
        candidate_id="trend-challenger",
        strategy_id="MULTISPEED_TREND_RECLAIM_30M_V2",
        data_quality=Decimal("1"),
        main_eligible=registry.main_enabled("MULTISPEED_TREND_RECLAIM_30M_V2", Side.LONG),
    )
    engine = PaperPortfolioEngine(
        run_id=current.run_id,
        strategy_ids=(current.strategy_id, challenger.strategy_id),
        shadow_ledger=ShadowLedger((current.strategy_id, challenger.strategy_id)),
        enforce_v6_family_conflicts=True,
    )

    engine.offer((challenger, current), entries_paused=False)

    assert engine.main.pending_entry is not None
    assert engine.main.pending_entry.plan.strategy_id == current.strategy_id


def test_conflict_same_symbol_same_side_uses_cost_aware_arbitration() -> None:
    lower_quality = replace(
        candidate_plan(),
        candidate_id="trend-lower-quality",
        strategy_id="TREND_PULLBACK_RECLAIM_15M_V2",
        data_quality=Decimal("1"),
        shared_capital_evidence=SharedCapitalArbitrationEvidence(
            evidence_tier=2,
            stress_cost_adjusted_expectancy_usdt=Decimal("10"),
            cost_coverage=Decimal("10"),
            diversification_score=Decimal("1"),
        ),
    )
    higher_quality = replace(
        candidate_plan(),
        candidate_id="breakout-higher-quality",
        strategy_id="BREAKOUT_RETEST_30M_V2",
        data_quality=Decimal("0"),
        shared_capital_evidence=SharedCapitalArbitrationEvidence(
            evidence_tier=3,
            stress_cost_adjusted_expectancy_usdt=Decimal("0"),
            cost_coverage=Decimal("0"),
            diversification_score=Decimal("0"),
        ),
    )
    engine = PaperPortfolioEngine(
        run_id=lower_quality.run_id,
        strategy_ids=(lower_quality.strategy_id, higher_quality.strategy_id),
        shadow_ledger=ShadowLedger((lower_quality.strategy_id, higher_quality.strategy_id)),
        enforce_v6_family_conflicts=True,
    )

    engine.offer((lower_quality, higher_quality), entries_paused=False)

    assert engine.main.pending_entry is not None
    assert engine.main.pending_entry.plan.candidate_id == higher_quality.candidate_id
    selection = next(
        row for row in engine.audit_events if row["event"] == "MAIN_CANDIDATE_SELECTED"
    )
    assert selection["competing_candidate_ids"] == [lower_quality.candidate_id]


def test_conflict_same_side_missing_required_evidence_fails_closed() -> None:
    trend = replace(
        candidate_plan(),
        candidate_id="trend-missing-evidence",
        strategy_id="TREND_PULLBACK_RECLAIM_15M_V2",
    )
    breakout = replace(
        candidate_plan(),
        candidate_id="breakout-proven-evidence",
        strategy_id="BREAKOUT_RETEST_30M_V2",
        shared_capital_evidence=SharedCapitalArbitrationEvidence(
            evidence_tier=3,
            stress_cost_adjusted_expectancy_usdt=Decimal("0.1"),
            cost_coverage=Decimal("2.5"),
        ),
    )
    engine = PaperPortfolioEngine(
        run_id=trend.run_id,
        strategy_ids=(trend.strategy_id, breakout.strategy_id),
        shadow_ledger=ShadowLedger((trend.strategy_id, breakout.strategy_id)),
        enforce_v6_family_conflicts=True,
    )

    engine.offer((trend, breakout), entries_paused=False)

    assert engine.main.pending_entry is None
    conflict = next(
        row
        for row in engine.audit_events
        if row["event"] == "MAIN_SAME_SIDE_EVIDENCE_INCOMPLETE_NO_TRADE"
    )
    assert {conflict["candidate_id"], *conflict["competing_candidate_ids"]} == {
        trend.candidate_id,
        breakout.candidate_id,
    }


def test_conflict_v6_arbitration_uses_every_preregistered_priority_in_order() -> None:
    template = replace(
        candidate_plan(),
        strategy_id="TREND_PULLBACK_RECLAIM_15M_V2",
        shared_capital_evidence=SharedCapitalArbitrationEvidence(
            evidence_tier=2,
            stress_cost_adjusted_expectancy_usdt=Decimal("1"),
            cost_coverage=Decimal("2"),
            diversification_score=Decimal("0.5"),
        ),
    )

    def evidence(
        *,
        tier: int = 2,
        expectancy: str = "1",
        coverage: str = "2",
        diversification: str = "0.5",
    ) -> SharedCapitalArbitrationEvidence:
        return SharedCapitalArbitrationEvidence(
            evidence_tier=tier,
            stress_cost_adjusted_expectancy_usdt=Decimal(expectancy),
            cost_coverage=Decimal(coverage),
            diversification_score=Decimal(diversification),
        )

    cases = (
        (
            {"shared_capital_evidence": evidence(tier=3, expectancy="0", coverage="0")},
            {
                "shared_capital_evidence": evidence(
                    tier=2, expectancy="100", coverage="100", diversification="1"
                ),
                "liquidity_quality": Decimal("1"),
                "signal_time_ms": 9_000,
                "expires_at_ms": 10_500,
            },
        ),
        (
            {"shared_capital_evidence": evidence(expectancy="2", coverage="0")},
            {
                "shared_capital_evidence": evidence(
                    expectancy="1", coverage="100", diversification="1"
                ),
                "liquidity_quality": Decimal("1"),
            },
        ),
        (
            {"shared_capital_evidence": evidence(coverage="3")},
            {
                "shared_capital_evidence": evidence(coverage="2", diversification="1"),
                "liquidity_quality": Decimal("1"),
            },
        ),
        (
            {"liquidity_quality": Decimal("0.9")},
            {
                "liquidity_quality": Decimal("0.8"),
                "signal_time_ms": 9_000,
                "expires_at_ms": 10_500,
                "shared_capital_evidence": evidence(diversification="1"),
            },
        ),
        (
            {"signal_time_ms": 2_000, "expires_at_ms": 3_500},
            {
                "signal_time_ms": 1_000,
                "expires_at_ms": 2_500,
                "shared_capital_evidence": evidence(diversification="1"),
            },
        ),
        (
            {"shared_capital_evidence": evidence(diversification="0.8")},
            {"shared_capital_evidence": evidence(diversification="0.7")},
        ),
    )

    for index, (winner_overrides, loser_overrides) in enumerate(cases):
        winner = replace(template, candidate_id=f"winner-{index}", **winner_overrides)
        loser = replace(
            template,
            candidate_id=f"loser-{index}",
            strategy_id="BREAKOUT_RETEST_30M_V2",
            **loser_overrides,
        )
        assert (
            min(
                (loser, winner),
                key=lambda plan: plan.shared_capital_arbitration_key(),
            )
            is winner
        )
        assert not hasattr(winner.shared_capital_evidence, "win_rate")


def test_conflict_opposite_sides_produce_no_shared_trade() -> None:
    long_plan = replace(
        candidate_plan(),
        candidate_id="trend-long",
        strategy_id="TREND_PULLBACK_RECLAIM_15M_V2",
    )
    short_plan = replace(
        candidate_plan(),
        candidate_id="breakout-short",
        strategy_id="BREAKOUT_RETEST_30M_V2",
        direction=Side.SHORT,
        planned_entry=Decimal("100"),
        worst_allowed_entry=Decimal("99.8"),
        initial_stop=Decimal("101"),
        take_profit_targets=(
            TakeProfitTarget("TP1", Decimal("98.5"), Decimal("0.7")),
            TakeProfitTarget("TP2", Decimal("97"), Decimal("0.3")),
        ),
    )
    engine = PaperPortfolioEngine(
        run_id=long_plan.run_id,
        strategy_ids=(long_plan.strategy_id, short_plan.strategy_id),
        shadow_ledger=ShadowLedger((long_plan.strategy_id, short_plan.strategy_id)),
        enforce_v6_family_conflicts=True,
    )

    engine.offer((long_plan, short_plan), entries_paused=False)

    assert engine.main.pending_entry is None
    conflict = next(
        row for row in engine.audit_events if row["event"] == "MAIN_OPPOSITE_SIDE_CONFLICT_NO_TRADE"
    )
    assert {conflict["candidate_id"], *conflict["competing_candidate_ids"]} == {
        long_plan.candidate_id,
        short_plan.candidate_id,
    }
