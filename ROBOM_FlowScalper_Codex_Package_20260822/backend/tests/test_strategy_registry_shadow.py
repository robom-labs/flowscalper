"""전략 Registry 설정과 전략별 BASE·STRESS shadow 계좌 격리를 검증한다."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.app.strategies.runtime_evaluator as runtime_evaluator_module
from backend.app.build_identity import (
    APP_VERSION,
    STRATEGY_IDS,
    STRATEGY_VERSION,
    git_commit,
)
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.costing import CostProfile
from backend.app.domain.models import (
    DataQuality,
    MarketDataState,
    MarketEvent,
    RuntimeMode,
    Side,
    Venue,
)
from backend.app.regime import Regime
from backend.app.research.gates import (
    EvidenceEpoch,
    EvidenceHorizon,
    EvidenceSample,
)
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import LedgerInvariantError, RecoveryState, SQLiteLedger
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.governor import GovernanceEvidence, StrategyGovernor
from backend.app.strategies.registry import (
    StrategyChangeSource,
    StrategyLifecycle,
    StrategyManualLockConflict,
    StrategyMode,
    StrategyRegistry,
    StrategyRevisionConflict,
)
from backend.app.strategies.runtime_evaluator import (
    StrategySignalEvaluator,
    _pullback_metrics,
)
from backend.app.strategies.shadow import ShadowLedger, ShadowPosition
from backend.tests.test_strategies import features

_EVIDENCE_HASH = "a" * 64
_ELIGIBLE_DIRECTION_RESEARCH_IDS = (
    "BREAKOUT_RETEST_15M_V2",
    "BREAKOUT_RETEST_30M_V2",
    "CBR_CONTINUATION_V1",
    "MULTISPEED_TREND_RECLAIM_30M_V2",
    "TREND_PULLBACK_RECLAIM_15M_V2",
    "VWAP_EXHAUSTION_REVERSION_V1",
)
_CURRENT_DIRECTION_RESEARCH_IDS = {
    "BREAKOUT_RETEST_30M_V2",
    "TREND_PULLBACK_RECLAIM_15M_V2",
    "VWAP_EXHAUSTION_REVERSION_V1",
}


def _restore_operational_quarantine_cohort(
    registry: StrategyRegistry,
    *,
    omit_strategy_id: str | None = None,
    overrides: dict[str, dict[str, object]] | None = None,
) -> None:
    for strategy_id in _ELIGIBLE_DIRECTION_RESEARCH_IDS:
        if strategy_id == omit_strategy_id:
            continue
        values: dict[str, object] = {
            "mode": StrategyMode.OFF,
            "lifecycle": StrategyLifecycle.QUARANTINED,
            "long_enabled": True,
            "short_enabled": True,
            "revision": 1,
            "manual_lock": False,
            "changed_by": StrategyChangeSource.AUTO_GOVERNOR,
            "change_reason": "OPERATIONAL_FAULT",
            "updated_ts_ms": 1_000,
        }
        values.update((overrides or {}).get(strategy_id, {}))
        registry.restore_setting(strategy_id, **values)  # type: ignore[arg-type]


def _governance_freshness_inputs(
    *,
    assessment_ts_ms: int,
    sample_count: int = 30,
) -> dict[str, object]:
    epoch = EvidenceEpoch(
        epoch_id="EPOCH-V9-REGISTRY",
        opened_ts_ms=0,
        closed_ts_ms=None,
        strategy_version="v9-current",
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
    samples = tuple(
        EvidenceSample(
            opportunity_id=f"REGISTRY-OPP-{index:04d}",
            observed_ts_ms=assessment_ts_ms,
            evidence_epoch_id=epoch.epoch_id,
            strategy_version=epoch.strategy_version,
        )
        for index in range(sample_count)
    )
    return {
        "evidence_samples": samples,
        "evidence_epoch": epoch,
        "evidence_horizon": EvidenceHorizon.SWING,
    }


def test_registry_exposes_fifteen_strategies_and_honors_mode_and_direction() -> None:
    registry = StrategyRegistry()
    assert registry.strategy_ids == (
        "LSA_REVERSAL_V1",
        "CBR_CONTINUATION_V1",
        "VWAP_EXHAUSTION_REVERSION_V1",
        "OFI_CONTINUATION_PULLBACK_V1",
        "QUEUE_MICROPRICE_MOMENTUM_V1",
        "AGGRESSOR_FLOW_CONTINUATION_V1",
        "MULTILEVEL_MICROPRICE_MOMENTUM_V1",
        "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        "OFI_RETURN_CONFLUENCE_V1",
        "BOOK_SLOPE_ASYMMETRY_V1",
        "HOURLY_MOMENTUM_BREAKOUT_V1",
        "TREND_PULLBACK_RECLAIM_15M_V2",
        "BREAKOUT_RETEST_15M_V2",
        "BREAKOUT_RETEST_30M_V2",
        "MULTISPEED_TREND_RECLAIM_30M_V2",
    )
    assert STRATEGY_IDS == registry.strategy_ids
    assert STRATEGY_VERSION.startswith("+".join(registry.strategy_ids) + "@")
    assert [row["mode"] for row in registry.rows()] == [
        "OFF",
        "SHADOW",
        "SHADOW",
        "OFF",
        "OFF",
        "OFF",
        "OFF",
        "OFF",
        "OFF",
        "OFF",
        "OFF",
        "SHADOW",
        "SHADOW",
        "SHADOW",
        "SHADOW",
    ]
    assert [row["lifecycle"] for row in registry.rows()] == [
        "RETIRED",
        "SHADOW",
        "SHADOW",
        "RETIRED",
        "RETIRED",
        "RESEARCH",
        "RESEARCH",
        "RETIRED",
        "RESEARCH",
        "RESEARCH",
        "RETIRED",
        "SHADOW",
        "SHADOW",
        "SHADOW",
        "SHADOW",
    ]
    assert all(row["long_enabled"] and row["short_enabled"] for row in registry.rows())
    required_descriptor_contract = {
        "strategy_version",
        "required_market_data",
        "minimum_warmup_ko",
        "entry_hypothesis_ko",
        "falsification_conditions_ko",
        "edge_decay_policy_ko",
        "risk_budget_rule_ko",
        "target_universe_ko",
        "data_leakage_guards_ko",
        "research_source_ids",
    }
    research_foundations = Path("docs/20_RESEARCH_FOUNDATIONS_AND_ADAPTATION.md").read_text()
    for row in registry.rows():
        assert required_descriptor_contract <= row.keys()
        assert row["strategy_version"] in {"V1", "V2"}
        assert row["required_market_data"]
        assert row["minimum_warmup_ko"]
        assert row["entry_hypothesis_ko"]
        assert row["falsification_conditions_ko"]
        assert row["edge_decay_policy_ko"]
        assert row["risk_budget_rule_ko"] == ("공동 PAPER 0.10%·독립 PAPER 0.50% 계좌자산 위험예산")
        assert row["target_universe_ko"]
        assert row["data_leakage_guards_ko"]
        assert row["research_source_ids"]
        assert all(
            f"| {source_id} |" in research_foundations for source_id in row["research_source_ids"]
        )
        assert row["change_reason"]
    micro_rows = registry.rows()[:10]
    assert all(row["horizon_class"] == "MICRO_SCALP" for row in micro_rows)
    assert all(row["expected_holding_seconds"] == [10, 180] for row in micro_rows)
    assert all(row["signal_half_life_seconds"] == 30 for row in micro_rows)
    assert all(row["max_hold_seconds"] is None for row in micro_rows)
    assert all(not row["edge_decay_enabled"] for row in micro_rows)
    assert all(row["exit_model"].endswith("NO_TIME_EXIT") for row in micro_rows)
    hourly = registry.rows()[10]
    assert hourly["strategy_id"] == "HOURLY_MOMENTUM_BREAKOUT_V1"
    assert hourly["change_reason"] == "FIXED_HISTORICAL_REPLICATION_FAILED_WAVE46"
    assert hourly["horizon_class"] == "INTRADAY_SWING"
    assert hourly["expected_holding_seconds"] == [3_600, 129_600]
    assert hourly["signal_half_life_seconds"] == 5
    assert hourly["take_profit_1_r"] == "2.2"
    assert hourly["take_profit_2_r"] == "4.5"
    assert hourly["max_hold_seconds"] is None
    assert not hourly["edge_decay_enabled"]
    assert hourly["exit_model"].endswith("NO_TIME_EXIT")
    assert hourly["minimum_warmup_ko"] == "완성 1시간봉 200개 이상"
    assert "SRC-CRYPTO-MOMENTUM-2018" in hourly["research_source_ids"]
    intraday_rows = registry.rows()[11:]
    assert len(intraday_rows) == 4
    assert all(row["mode"] == "SHADOW" for row in intraday_rows)
    assert all(row["lifecycle"] == "SHADOW" for row in intraday_rows)
    assert all(row["strategy_version"] == "V2" for row in intraday_rows)
    assert all(row["horizon_class"] == "INTRADAY_SWING" for row in intraday_rows)
    assert all(row["signal_half_life_seconds"] == 5 for row in intraday_rows)
    assert all(not row["edge_decay_enabled"] for row in intraday_rows)
    assert all(row["max_hold_seconds"] is None for row in intraday_rows)
    assert all(row["exit_model"].endswith("NO_TIME_EXIT") for row in intraday_rows)
    assert all(
        row["cost_model_version"] == "TOP_OF_BOOK_BASE13_STRESS25_V1" for row in registry.rows()
    )
    registry.configure(
        "VWAP_EXHAUSTION_REVERSION_V1",
        mode=StrategyMode.OFF,
        long_enabled=True,
        short_enabled=True,
    )
    registry.configure(
        "CBR_CONTINUATION_V1",
        mode=StrategyMode.SHADOW,
        long_enabled=True,
        short_enabled=False,
    )

    evaluator = StrategySignalEvaluator()
    decisions = evaluator.evaluate(registry, features(), Regime.WARMUP)

    assert len(decisions) == sum(
        registry.evaluation_enabled(strategy_id, side)
        for strategy_id in registry.strategy_ids
        for side in Side
    )
    assert all(item.decision.status is CandidateStatus.REJECTED for item in decisions)
    cbr = next(item for item in decisions if item.decision.strategy_id == "CBR_CONTINUATION_V1")
    assert cbr.decision.side is Side.LONG
    assert not cbr.main_eligible
    assert cbr.shadow_eligible
    assert not any(
        item.decision.strategy_id == "VWAP_EXHAUSTION_REVERSION_V1" for item in decisions
    )
    assert not any(
        item.decision.strategy_id
        in {
            "OFI_CONTINUATION_PULLBACK_V1",
            "QUEUE_MICROPRICE_MOMENTUM_V1",
            "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        }
        for item in decisions
    )


def test_all_registered_strategies_default_to_structure_tp_sl_without_time_exit() -> None:
    rows = StrategyRegistry().rows()

    assert rows
    assert all(row["max_hold_seconds"] is None for row in rows)
    assert all(not row["edge_decay_enabled"] for row in rows)
    assert all(row["exit_model"].endswith("NO_TIME_EXIT") for row in rows)


def test_legacy_manual_setting_cannot_revive_policy_retired_strategy() -> None:
    registry = StrategyRegistry()
    registry.configure(
        "LSA_REVERSAL_V1",
        mode=StrategyMode.SHADOW,
        lifecycle=StrategyLifecycle.SHADOW,
        long_enabled=True,
        short_enabled=True,
        manual_lock=True,
        source=StrategyChangeSource.RECOVERY,
        reason="LEGACY_USER_CONFIGURATION",
        updated_ts_ms=1_000,
    )

    migrated = registry.enforce_policy_retirements(updated_ts_ms=2_000)
    setting = registry.setting("LSA_REVERSAL_V1")

    assert len(migrated) == 1
    assert setting.mode is StrategyMode.OFF
    assert setting.lifecycle is StrategyLifecycle.RETIRED
    assert setting.revision == 2
    assert setting.manual_lock is False
    assert setting.changed_by is StrategyChangeSource.MIGRATION
    assert registry.enforce_policy_retirements(updated_ts_ms=3_000) == ()


def test_retired_hourly_strategy_repairs_legacy_generic_reason() -> None:
    registry = StrategyRegistry()
    registry.restore_setting(
        "HOURLY_MOMENTUM_BREAKOUT_V1",
        mode=StrategyMode.OFF,
        lifecycle=StrategyLifecycle.RETIRED,
        long_enabled=True,
        short_enabled=True,
        revision=1,
        manual_lock=False,
        changed_by=StrategyChangeSource.MIGRATION,
        change_reason="SAFE_DEFAULT",
        updated_ts_ms=1_000,
    )

    migrated = registry.enforce_policy_retirements(updated_ts_ms=2_000)
    setting = registry.setting("HOURLY_MOMENTUM_BREAKOUT_V1")

    assert len(migrated) == 1
    assert setting.revision == 2
    assert setting.change_reason == "FIXED_HISTORICAL_REPLICATION_FAILED_WAVE46"


def test_global_operational_quarantine_restores_only_six_eligible_entries() -> None:
    registry = StrategyRegistry()
    _restore_operational_quarantine_cohort(registry)
    protected_ids = ("LSA_REVERSAL_V1", "AGGRESSOR_FLOW_CONTINUATION_V1")
    for strategy_id in protected_ids:
        registry.restore_setting(
            strategy_id,
            mode=StrategyMode.OFF,
            lifecycle=StrategyLifecycle.QUARANTINED,
            long_enabled=True,
            short_enabled=True,
            revision=1,
            manual_lock=False,
            changed_by=StrategyChangeSource.AUTO_GOVERNOR,
            change_reason="OPERATIONAL_FAULT",
            updated_ts_ms=1_000,
        )

    migrated = registry.restore_operationally_quarantined_research_defaults(
        updated_ts_ms=2_000
    )

    assert tuple(row["strategy_id"] for row in migrated) == (
        _ELIGIBLE_DIRECTION_RESEARCH_IDS
    )
    for row in migrated:
        strategy_id = str(row["strategy_id"])
        assert row["mode"] == "SHADOW"
        assert row["lifecycle"] == (
            "SHADOW" if strategy_id in _CURRENT_DIRECTION_RESEARCH_IDS else "CHALLENGER"
        )
        assert row["settings_revision"] == 2
        assert row["manual_lock"] is False
        assert row["changed_by"] == "MIGRATION"
        assert row["change_reason"] == (
            "V9_USER_REQUESTED_SHADOW_DEFAULT_ON_AFTER_GLOBAL_OPERATIONAL_RECOVERY"
        )
    for strategy_id in protected_ids:
        protected = registry.setting(strategy_id)
        assert protected.mode is StrategyMode.OFF
        assert protected.lifecycle is StrategyLifecycle.QUARANTINED
        assert protected.revision == 1
    assert (
        registry.restore_operationally_quarantined_research_defaults(updated_ts_ms=3_000)
        == ()
    )


@pytest.mark.parametrize(
    ("omit_strategy_id", "overrides"),
    (
        ("CBR_CONTINUATION_V1", None),
        (None, {"CBR_CONTINUATION_V1": {"updated_ts_ms": 1_001}}),
        (
            None,
            {
                "CBR_CONTINUATION_V1": {
                    "changed_by": StrategyChangeSource.USER_UI,
                    "manual_lock": True,
                }
            },
        ),
        (None, {"CBR_CONTINUATION_V1": {"manual_lock": True}}),
        (
            None,
            {"CBR_CONTINUATION_V1": {"change_reason": "INDIVIDUAL_OPERATIONAL_FAULT"}},
        ),
    ),
)
def test_operational_quarantine_recovery_rejects_partial_or_individual_cohort(
    omit_strategy_id: str | None,
    overrides: dict[str, dict[str, object]] | None,
) -> None:
    registry = StrategyRegistry()
    _restore_operational_quarantine_cohort(
        registry,
        omit_strategy_id=omit_strategy_id,
        overrides=overrides,
    )

    assert (
        registry.restore_operationally_quarantined_research_defaults(updated_ts_ms=2_000)
        == ()
    )
    for strategy_id in _ELIGIBLE_DIRECTION_RESEARCH_IDS:
        setting = registry.setting(strategy_id)
        if strategy_id == omit_strategy_id:
            assert setting.mode is StrategyMode.SHADOW
            assert setting.revision == 0
            continue
        assert setting.mode is StrategyMode.OFF
        assert setting.lifecycle is StrategyLifecycle.QUARANTINED
        assert setting.revision == 1


def test_runtime_rejects_direct_reactivation_of_policy_retired_strategy() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())

    with pytest.raises(ValueError, match="퇴역"):
        runtime.configure_strategy(
            "LSA_REVERSAL_V1",
            mode=StrategyMode.SHADOW,
            long_enabled=True,
            short_enabled=True,
        )


def test_legacy_default_active_is_migrated_to_shadow_until_proven() -> None:
    registry = StrategyRegistry()
    registry.restore_setting(
        "CBR_CONTINUATION_V1",
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        revision=1,
        manual_lock=False,
        changed_by=StrategyChangeSource.MIGRATION,
        change_reason="LEGACY_ACTIVE_DEFAULT",
        updated_ts_ms=1_000,
    )

    migrated = registry.enforce_unproven_active_defaults(updated_ts_ms=2_000)
    setting = registry.setting("CBR_CONTINUATION_V1")

    assert len(migrated) == 1
    assert setting.mode is StrategyMode.SHADOW
    assert setting.lifecycle is StrategyLifecycle.SHADOW
    assert setting.revision == 2
    assert registry.enforce_unproven_active_defaults(updated_ts_ms=3_000) == ()


def test_recovery_preserves_only_revalidated_governor_active_lineage() -> None:
    strategy_id = "BREAKOUT_RETEST_30M_V2"
    user_active = StrategyRegistry()
    user_active.restore_setting(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        revision=7,
        manual_lock=True,
        changed_by=StrategyChangeSource.USER_UI,
        change_reason="LEGACY_USER_ACTIVE",
        updated_ts_ms=1_000,
    )

    migrated = user_active.enforce_unproven_active_defaults(updated_ts_ms=2_000)
    setting = user_active.setting(strategy_id)
    history = user_active.revision_history(strategy_id)

    assert len(migrated) == 1
    assert setting.mode is StrategyMode.SHADOW
    assert setting.lifecycle is StrategyLifecycle.SHADOW
    assert setting.revision == 8
    assert setting.manual_lock is False
    assert setting.changed_by is StrategyChangeSource.MIGRATION
    assert setting.change_reason == "V6_UNPROVEN_ACTIVE_RECOVERY_DOWNGRADED"
    assert [row["settings_revision"] for row in history] == [0, 7, 8]
    assert history[-2]["mode"] == "ACTIVE"
    assert history[-2]["changed_by"] == "USER_UI"
    assert history[-1]["mode"] == "SHADOW"
    assert user_active.main_enabled(strategy_id, Side.LONG) is False

    governor_active = StrategyRegistry()
    governor_active.restore_setting(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        revision=7,
        manual_lock=False,
        changed_by=StrategyChangeSource.AUTO_GOVERNOR,
        change_reason="FORMAL_OOS_GATES_PASSED",
        updated_ts_ms=1_000,
    )

    string_only_migration = governor_active.enforce_unproven_active_defaults(
        updated_ts_ms=2_000
    )
    assert len(string_only_migration) == 1
    assert governor_active.setting(strategy_id).mode is StrategyMode.SHADOW
    assert governor_active.main_enabled(strategy_id, Side.LONG) is False

    validated_governor_active = StrategyRegistry()
    validated_governor_active.restore_setting(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        revision=7,
        manual_lock=False,
        changed_by=StrategyChangeSource.AUTO_GOVERNOR,
        change_reason="CHALLENGER_BEATS_CHAMPION",
        updated_ts_ms=1_000,
        recovery_row_token="validated-rev-7",
    )
    assert (
        validated_governor_active.enforce_unproven_active_defaults(
            updated_ts_ms=2_000,
            validated_governor_active_tokens={strategy_id: {7: "validated-rev-7"}},
        )
        == ()
    )
    assert validated_governor_active.setting(strategy_id).mode is StrategyMode.ACTIVE
    assert validated_governor_active.main_enabled(strategy_id, Side.LONG) is True

    locked_governor_active = StrategyRegistry()
    locked_governor_active.restore_setting(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        revision=6,
        manual_lock=False,
        changed_by=StrategyChangeSource.AUTO_GOVERNOR,
        change_reason="FORMAL_OOS_GATES_PASSED",
        updated_ts_ms=900,
        recovery_row_token="locked-rev-6",
    )
    locked_governor_active.restore_setting(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=False,
        revision=7,
        manual_lock=True,
        changed_by=StrategyChangeSource.USER_UI,
        change_reason="USER_LOCKS_PROVEN_ACTIVE",
        updated_ts_ms=1_000,
    )

    assert (
        locked_governor_active.enforce_unproven_active_defaults(
            updated_ts_ms=2_000,
            validated_governor_active_tokens={strategy_id: {6: "locked-rev-6"}},
        )
        == ()
    )
    locked_setting = locked_governor_active.setting(strategy_id)
    assert locked_setting.mode is StrategyMode.ACTIVE
    assert locked_setting.manual_lock is True
    assert locked_setting.short_enabled is False
    assert locked_governor_active.main_enabled(strategy_id, Side.LONG) is True

    invalid_latest_governor = StrategyRegistry()
    for revision in (6, 7):
        invalid_latest_governor.restore_setting(
            strategy_id,
            mode=StrategyMode.ACTIVE,
            lifecycle=StrategyLifecycle.ACTIVE,
            long_enabled=True,
            short_enabled=True,
            revision=revision,
            manual_lock=False,
            changed_by=StrategyChangeSource.AUTO_GOVERNOR,
            change_reason="CHALLENGER_BEATS_CHAMPION",
            updated_ts_ms=900 + revision,
            recovery_row_token=f"invalid-latest-rev-{revision}",
        )
    assert len(
        invalid_latest_governor.enforce_unproven_active_defaults(
            updated_ts_ms=2_000,
            validated_governor_active_tokens={
                strategy_id: {6: "invalid-latest-rev-6"}
            },
        )
    ) == 1
    assert invalid_latest_governor.setting(strategy_id).mode is StrategyMode.SHADOW

    broken_lineage = StrategyRegistry()
    broken_lineage.restore_setting(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        revision=5,
        manual_lock=False,
        changed_by=StrategyChangeSource.AUTO_GOVERNOR,
        change_reason="OLD_FORMAL_OOS_GATES_PASSED",
        updated_ts_ms=700,
        recovery_row_token="broken-rev-5",
    )
    broken_lineage.restore_setting(
        strategy_id,
        mode=StrategyMode.SHADOW,
        lifecycle=StrategyLifecycle.SHADOW,
        long_enabled=True,
        short_enabled=True,
        revision=6,
        manual_lock=False,
        changed_by=StrategyChangeSource.AUTO_GOVERNOR,
        change_reason="OLD_ACTIVE_DEMOTED",
        updated_ts_ms=800,
    )
    broken_lineage.restore_setting(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        revision=7,
        manual_lock=True,
        changed_by=StrategyChangeSource.USER_UI,
        change_reason="LEGACY_USER_REACTIVATION",
        updated_ts_ms=1_000,
    )

    assert len(
        broken_lineage.enforce_unproven_active_defaults(
            updated_ts_ms=2_000,
            validated_governor_active_tokens={strategy_id: {5: "broken-rev-5"}},
        )
    ) == 1
    assert broken_lineage.setting(strategy_id).mode is StrategyMode.SHADOW


def test_restore_setting_accepts_only_exact_equal_revision_duplicate() -> None:
    registry = StrategyRegistry()
    strategy_id = "BREAKOUT_RETEST_30M_V2"
    restore_kwargs = {
        "mode": StrategyMode.ACTIVE,
        "lifecycle": StrategyLifecycle.ACTIVE,
        "long_enabled": True,
        "short_enabled": True,
        "revision": 7,
        "manual_lock": False,
        "changed_by": StrategyChangeSource.AUTO_GOVERNOR,
        "change_reason": "CHALLENGER_BEATS_CHAMPION",
        "updated_ts_ms": 1_000,
        "recovery_row_token": "canonical-row-token",
    }

    restored = registry.restore_setting(strategy_id, **restore_kwargs)
    duplicate = registry.restore_setting(strategy_id, **restore_kwargs)

    assert duplicate is restored
    assert registry.setting(strategy_id).revision == 7
    with pytest.raises(ValueError, match="복구 상태가 다릅니다"):
        registry.restore_setting(
            strategy_id,
            **(restore_kwargs | {"change_reason": "DIVERGENT_REASON"}),
        )
    with pytest.raises(ValueError, match="복구 원장 행이 다릅니다"):
        registry.restore_setting(
            strategy_id,
            **(restore_kwargs | {"recovery_row_token": "divergent-evidence-token"}),
        )


def test_restore_setting_replaces_only_the_first_persisted_revision_zero() -> None:
    strategy_id = "CBR_CONTINUATION_V1"
    registry = StrategyRegistry()
    legacy_revision_zero = {
        "mode": StrategyMode.SHADOW,
        "lifecycle": StrategyLifecycle.SHADOW,
        "long_enabled": True,
        "short_enabled": True,
        "revision": 0,
        "manual_lock": False,
        "changed_by": StrategyChangeSource.MIGRATION,
        "change_reason": "SAFE_DEFAULT",
        "updated_ts_ms": 0,
        "recovery_row_token": "legacy-revision-zero-row",
    }

    restored = registry.restore_setting(strategy_id, **legacy_revision_zero)
    duplicate = registry.restore_setting(strategy_id, **legacy_revision_zero)

    assert duplicate is restored
    assert registry.setting(strategy_id).revision == 0
    assert registry.setting(strategy_id).change_reason == "SAFE_DEFAULT"
    assert registry.revision_history(strategy_id)[0]["change_reason"] == "SAFE_DEFAULT"
    with pytest.raises(ValueError, match="복구 상태가 다릅니다"):
        registry.restore_setting(
            strategy_id,
            **(legacy_revision_zero | {"change_reason": "DIVERGENT_REASON"}),
        )
    with pytest.raises(ValueError, match="복구 원장 행이 다릅니다"):
        registry.restore_setting(
            strategy_id,
            **(
                legacy_revision_zero
                | {"recovery_row_token": "divergent-revision-zero-row"}
            ),
        )

    out_of_order = StrategyRegistry()
    out_of_order.restore_setting(
        strategy_id,
        mode=StrategyMode.SHADOW,
        lifecycle=StrategyLifecycle.CHALLENGER,
        long_enabled=True,
        short_enabled=True,
        revision=7,
        manual_lock=False,
        changed_by=StrategyChangeSource.RECOVERY,
        change_reason="NEWER_RECOVERY_ROW",
        updated_ts_ms=700,
        recovery_row_token="newer-revision-seven-row",
    )
    with pytest.raises(ValueError, match="복구 상태가 다릅니다"):
        out_of_order.restore_setting(strategy_id, **legacy_revision_zero)

    assert out_of_order.setting(strategy_id).revision == 7
    assert out_of_order.setting(strategy_id).change_reason == "NEWER_RECOVERY_ROW"


def test_ignored_recovery_revision_reserves_safe_tombstone_before_policy() -> None:
    strategy_id = "AGGRESSOR_FLOW_CONTINUATION_V1"
    registry = StrategyRegistry()
    before = registry.setting_row(strategy_id)

    reserved = registry.reserve_ignored_recovery_revision(
        strategy_id,
        revision=1,
        updated_ts_ms=100,
        recovery_row_token="ignored-governance-revision-one",
    )

    assert reserved is not None
    assert reserved["mode"] == before["mode"]
    assert reserved["lifecycle"] == before["lifecycle"]
    assert reserved["settings_revision"] == 1
    assert reserved["changed_by"] == StrategyChangeSource.RECOVERY.value
    assert reserved["change_reason"] == "IGNORED_FAIL_CLOSED_GOVERNANCE_REVISION"
    assert reserved["recovery_revision_reserved"] is True
    assert reserved["ignored_source_applied"] is False
    assert reserved["effective_previous_revision"] == 0
    assert reserved["data_deleted"] is False
    assert reserved["duplicate_revision_relaxed"] is False
    assert (
        registry.reserve_ignored_recovery_revision(
            strategy_id,
            revision=1,
            updated_ts_ms=100,
            recovery_row_token="ignored-governance-revision-one",
        )
        == reserved
    )
    with pytest.raises(ValueError, match="복구 원장 행이 다릅니다"):
        registry.reserve_ignored_recovery_revision(
            strategy_id,
            revision=1,
            updated_ts_ms=100,
            recovery_row_token="different-ignored-governance-row",
        )

    migrations = registry.enforce_v6_family_runtime_policy(updated_ts_ms=200)

    assert len(migrations) == 1
    assert migrations[0]["strategy_id"] == strategy_id
    assert migrations[0]["settings_revision"] == 2
    assert migrations[0]["change_reason"] == "V6_LEGACY_COMPONENT_HISTORY_ONLY"
    assert [
        row["settings_revision"] for row in registry.revision_history(strategy_id)
    ] == [0, 1, 2]
    with pytest.raises(ValueError, match="연속되지 않습니다"):
        registry.reserve_ignored_recovery_revision(
            strategy_id,
            revision=4,
            updated_ts_ms=300,
            recovery_row_token="skipped-revision-four",
        )


def _complete_active_governance_evidence(
    *,
    timestamp: int,
) -> GovernanceEvidence:
    return GovernanceEvidence(
        base_sample_size=150,
        stress_sample_size=150,
        base_expectancy_usdt=Decimal("0.10"),
        stress_expectancy_usdt=Decimal("0.03"),
        base_profit_factor=Decimal("1.30"),
        stress_profit_factor=Decimal("1.25"),
        sample_span_days=30,
        regime_count=1,
        dsr_probability=0.96,
        pbo=0.15,
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=2,
        live_public_sample_size=150,
        cooldown_elapsed=True,
        strategy_correlation_abs=0.25,
        evaluation_period="CURRENT_STRATEGY_VERSION_LIVE_PUBLIC",
        evaluated_ts_ms=timestamp,
        operational_health_passed=True,
        operational_health_evaluated_ts_ms=timestamp,
        base_win_rate=Decimal("0.60"),
        stress_win_rate=Decimal("0.58"),
        unique_opportunity_count=150,
        base_win_rate_ci95_lower=Decimal("0.45"),
        stress_win_rate_ci95_lower=Decimal("0.43"),
        base_payoff_ratio=Decimal("1.50"),
        stress_payoff_ratio=Decimal("1.40"),
        **_governance_freshness_inputs(assessment_ts_ms=timestamp),
    )


def _complete_active_recovery_setting(
    *,
    run_id: str,
    timestamp: int,
) -> dict[str, object]:
    strategy_id = "VWAP_EXHAUSTION_REVERSION_V1"
    descriptor = StrategyRegistry().descriptor(strategy_id)
    evidence = _complete_active_governance_evidence(timestamp=timestamp)
    return {
        "run_id": run_id,
        "ts_ms": timestamp,
        "strategy_id": strategy_id,
        "mode": "ACTIVE",
        "lifecycle": "ACTIVE",
        "long_enabled": True,
        "short_enabled": True,
        "settings_revision": 7,
        "manual_lock": False,
        "changed_by": "AUTO_GOVERNOR",
        "change_reason": "CHALLENGER_BEATS_CHAMPION",
        "settings_updated_ts_ms": timestamp,
        "change_evidence": {
            "assessment": {
                "strategy_id": strategy_id,
                "current_lifecycle": "CHALLENGER",
                "recommended_lifecycle": "ACTIVE",
                "reason_codes": ["CHALLENGER_BEATS_CHAMPION"],
                "automatic_action_allowed": True,
                "transition_required": True,
                "champion_id": None,
            },
            "evidence": evidence.as_dict(),
            "lineage": {
                "schema_version": 1,
                "run_id": run_id,
                "strategy_id": strategy_id,
                "strategy_version": STRATEGY_VERSION,
                "descriptor_strategy_version": (
                    descriptor.research_contract.strategy_version
                ),
                "app_version": APP_VERSION,
                "release_commit": git_commit(),
                "assessment_ts_ms": timestamp,
                "settings_revision": 7,
            },
        },
    }


def _restore_active_recovery_setting(
    database: Path,
    setting_row: dict[str, object],
    *,
    recovery_ts_ms: int,
) -> tuple[PaperRuntime, SQLiteLedger]:
    restored, recovered_runtime, ledger = _restore_active_recovery_settings(
        database,
        (setting_row,),
        recovery_ts_ms=recovery_ts_ms,
    )
    assert restored is True
    return recovered_runtime, ledger


def _restore_active_recovery_settings(
    database: Path,
    setting_rows: tuple[dict[str, object], ...],
    *,
    recovery_ts_ms: int,
) -> tuple[bool, PaperRuntime, SQLiteLedger]:
    first_setting_row = setting_rows[0]
    run_id = str(first_setting_row["run_id"])
    ledger = SQLiteLedger(database)
    PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(
            current_utc_ms=int(str(first_setting_row["ts_ms"])) - 1
        ),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    for setting_row in setting_rows:
        ledger.record_strategy_setting(setting_row)
    recovered_runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=recovery_ts_ms),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    recovered_runtime._persisted_main_order_ids = {"pre-restore-order"}
    recovered_runtime._persisted_main_trade_ids = {"pre-restore-trade"}
    recovered_runtime._persisted_shadow_trade_ids = {"pre-restore-shadow"}
    recovered = RecoveryState(
        run_id=run_id,
        venue=Venue.BINANCE_USDM.value,
        lifecycle_state="SCANNING",
        payload={},
        transition_count=0,
        recovered_ts_ms=recovery_ts_ms,
    )
    restored = recovered_runtime.restore_recovery_state(recovered)
    return restored, recovered_runtime, ledger


def test_strategy_migration_batch_rolls_back_setting_when_incident_conflicts(
    tmp_path: Path,
) -> None:
    run_id = "run-atomic-migration-batch"
    timestamp = 8_500
    ledger = SQLiteLedger(tmp_path / "atomic-migration-batch.sqlite3")
    PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=timestamp - 1),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    transition_id = "strategy-migration-conflict"
    payload = {
        "run_id": run_id,
        "strategy_id": "HOURLY_MOMENTUM_BREAKOUT_V1",
        "ts_ms": timestamp,
        "transition_id": transition_id,
    }
    ledger.record_incident(
        transition_id,
        run_id=run_id,
        severity="INFO",
        category="PREEXISTING_TEST_INCIDENT",
        ts_ms=timestamp - 1,
        payload={"run_id": run_id},
    )
    settings_before = ledger.count("strategy_settings")
    incidents_before = ledger.count("incidents")

    with pytest.raises(LedgerInvariantError, match="원자적으로 저장"):
        ledger.record_strategy_migration_batch(
            ((payload, "STRATEGY_POLICY_MIGRATION"),)
        )

    assert ledger.count("strategy_settings") == settings_before
    assert ledger.count("incidents") == incidents_before
    ledger.close()


def test_runtime_recovery_batch_failure_keeps_live_state_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-recovery-batch-failure"
    timestamp = 8_750
    ledger = SQLiteLedger(tmp_path / "recovery-batch-failure.sqlite3")
    PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=timestamp - 1),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    ledger.record_strategy_setting(
        {
            "run_id": run_id,
            "ts_ms": timestamp,
            "strategy_id": "HOURLY_MOMENTUM_BREAKOUT_V1",
            "mode": "ACTIVE",
            "lifecycle": "ACTIVE",
            "long_enabled": True,
            "short_enabled": True,
            "settings_revision": 1,
            "manual_lock": True,
            "changed_by": "USER_UI",
            "change_reason": "RECOVERY_ATOMICITY_TEST",
            "settings_updated_ts_ms": timestamp,
        }
    )
    recovered_runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=timestamp),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    recovered_runtime._persisted_main_order_ids = {"pre-restore-order"}
    recovered_runtime._persisted_main_trade_ids = {"pre-restore-trade"}
    recovered_runtime._persisted_shadow_trade_ids = {"pre-restore-shadow"}
    registry_before = recovered_runtime.strategy_registry
    portfolio_before = recovered_runtime.paper_portfolio
    shadow_before = recovered_runtime.shadow_ledger

    def fail_batch(
        migrations: object,
    ) -> None:
        assert migrations
        raise LedgerInvariantError("INJECTED_ATOMIC_BATCH_FAILURE")

    monkeypatch.setattr(ledger, "record_strategy_migration_batch", fail_batch)
    recovered = RecoveryState(
        run_id=run_id,
        venue=Venue.BINANCE_USDM.value,
        lifecycle_state="SCANNING",
        payload={},
        transition_count=0,
        recovered_ts_ms=timestamp,
    )

    assert recovered_runtime.restore_recovery_state(recovered) is False
    assert recovered_runtime.strategy_registry is registry_before
    assert recovered_runtime.paper_portfolio is portfolio_before
    assert recovered_runtime.shadow_ledger is shadow_before
    assert recovered_runtime._persisted_main_order_ids == {"pre-restore-order"}
    assert recovered_runtime._persisted_main_trade_ids == {"pre-restore-trade"}
    assert recovered_runtime._persisted_shadow_trade_ids == {"pre-restore-shadow"}
    assert recovered_runtime.paused is True
    assert recovered_runtime.paper_portfolio.main.risk_state.faulted is True
    ledger.close()


def test_runtime_recovery_rejects_malformed_snapshot_before_live_state_swap(
    tmp_path: Path,
) -> None:
    run_id = "run-recovery-malformed-snapshot-ts"
    timestamp = 8_900
    ledger = SQLiteLedger(tmp_path / "recovery-malformed-snapshot-ts.sqlite3")
    PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=timestamp - 1),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    recovered_runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=timestamp),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    recovered_runtime._persisted_main_order_ids = {"pre-restore-order"}
    recovered_runtime._persisted_main_trade_ids = {"pre-restore-trade"}
    recovered_runtime._persisted_shadow_trade_ids = {"pre-restore-shadow"}
    registry_before = recovered_runtime.strategy_registry
    portfolio_before = recovered_runtime.paper_portfolio
    shadow_before = recovered_runtime.shadow_ledger
    orderflow_before = recovered_runtime.orderflow_confirmation_runtime
    main_orders_before = recovered_runtime._persisted_main_order_ids
    main_trades_before = recovered_runtime._persisted_main_trade_ids
    shadow_trades_before = recovered_runtime._persisted_shadow_trade_ids
    recovered = RecoveryState(
        run_id=run_id,
        venue=Venue.BINANCE_USDM.value,
        lifecycle_state="SCANNING",
        payload={"snapshot_ts_ms": "bogus"},
        transition_count=0,
        recovered_ts_ms=timestamp,
    )

    assert recovered_runtime.restore_recovery_state(recovered) is False
    assert recovered_runtime.strategy_registry is registry_before
    assert recovered_runtime.paper_portfolio is portfolio_before
    assert recovered_runtime.shadow_ledger is shadow_before
    assert recovered_runtime.orderflow_confirmation_runtime is orderflow_before
    assert recovered_runtime._persisted_main_order_ids is main_orders_before
    assert recovered_runtime._persisted_main_trade_ids is main_trades_before
    assert recovered_runtime._persisted_shadow_trade_ids is shadow_trades_before
    assert recovered_runtime.runtime_health_flags == [
        "RECOVERY_FAIL_CLOSED",
        "RECOVERY_STATE_REJECTED:ValueError",
    ]
    assert recovered_runtime.paused is True
    assert recovered_runtime.paper_portfolio.main.risk_state.faulted is True
    ledger.close()


def test_runtime_recovery_preserves_exact_duplicate_active_row(tmp_path: Path) -> None:
    timestamp = 9_000
    setting_row = _complete_active_recovery_setting(
        run_id="run-exact-duplicate-active",
        timestamp=timestamp,
    )

    restored, recovered_runtime, ledger = _restore_active_recovery_settings(
        tmp_path / "exact-duplicate-active.sqlite3",
        (setting_row, dict(setting_row)),
        recovery_ts_ms=timestamp,
    )

    setting = recovered_runtime.strategy_registry.setting(
        "VWAP_EXHAUSTION_REVERSION_V1"
    )
    assert restored is True
    assert setting.mode is StrategyMode.ACTIVE
    assert setting.lifecycle is StrategyLifecycle.ACTIVE
    assert setting.revision == 7
    assert recovered_runtime.strategy_registry.main_enabled(
        "VWAP_EXHAUSTION_REVERSION_V1", Side.LONG
    ) is True
    ledger.close()


def test_runtime_recovery_rejects_divergent_equal_revision_active_row(
    tmp_path: Path,
) -> None:
    timestamp = 9_500
    setting_row = _complete_active_recovery_setting(
        run_id="run-divergent-equal-revision-active",
        timestamp=timestamp,
    )
    divergent_row = dict(setting_row)
    divergent_row["ts_ms"] = timestamp + 1
    divergent_row["settings_updated_ts_ms"] = timestamp + 1
    divergent_row["change_reason"] = "DIVERGENT_WITHOUT_VALID_LINEAGE"
    divergent_row.pop("change_evidence")

    restored, recovered_runtime, ledger = _restore_active_recovery_settings(
        tmp_path / "divergent-equal-revision-active.sqlite3",
        (setting_row, divergent_row),
        recovery_ts_ms=timestamp + 1,
    )

    setting = recovered_runtime.strategy_registry.setting(
        "VWAP_EXHAUSTION_REVERSION_V1"
    )
    assert restored is False
    assert setting.mode is StrategyMode.SHADOW
    assert setting.lifecycle is StrategyLifecycle.SHADOW
    assert recovered_runtime.strategy_registry.main_enabled(
        "VWAP_EXHAUSTION_REVERSION_V1", Side.LONG
    ) is False
    assert recovered_runtime.paused is True
    assert recovered_runtime.paper_portfolio.main.risk_state.faulted is True
    assert recovered_runtime._persisted_main_order_ids == {"pre-restore-order"}
    assert recovered_runtime._persisted_main_trade_ids == {"pre-restore-trade"}
    assert recovered_runtime._persisted_shadow_trade_ids == {"pre-restore-shadow"}
    ledger.close()


def test_runtime_recovery_does_not_partially_apply_rows_before_later_malformed_row(
    tmp_path: Path,
) -> None:
    timestamp = 9_600
    setting_row = _complete_active_recovery_setting(
        run_id="run-atomic-malformed-setting",
        timestamp=timestamp,
    )
    malformed_row = dict(setting_row)
    malformed_row.update(
        {
            "strategy_id": "BREAKOUT_RETEST_30M_V2",
            "mode": "INVALID",
            "lifecycle": "SHADOW",
            "settings_revision": 1,
            "settings_updated_ts_ms": timestamp + 1,
            "ts_ms": timestamp + 1,
            "changed_by": "RECOVERY",
            "change_reason": "MALFORMED_LATER_ROW",
        }
    )
    malformed_row.pop("change_evidence")

    restored, recovered_runtime, ledger = _restore_active_recovery_settings(
        tmp_path / "atomic-malformed-setting.sqlite3",
        (setting_row, malformed_row),
        recovery_ts_ms=timestamp + 1,
    )

    setting = recovered_runtime.strategy_registry.setting(
        "VWAP_EXHAUSTION_REVERSION_V1"
    )
    assert restored is False
    assert setting.mode is StrategyMode.SHADOW
    assert setting.lifecycle is StrategyLifecycle.SHADOW
    assert setting.revision == 0
    assert recovered_runtime.strategy_registry.main_enabled(
        "VWAP_EXHAUSTION_REVERSION_V1", Side.LONG
    ) is False
    assert recovered_runtime.paused is True
    assert recovered_runtime.paper_portfolio.main.risk_state.faulted is True
    ledger.close()


def test_runtime_recovery_rejects_string_direction_boolean(tmp_path: Path) -> None:
    timestamp = 9_750
    setting_row = _complete_active_recovery_setting(
        run_id="run-string-direction-active",
        timestamp=timestamp,
    )
    setting_row["long_enabled"] = "false"

    restored, recovered_runtime, ledger = _restore_active_recovery_settings(
        tmp_path / "string-direction-active.sqlite3",
        (setting_row,),
        recovery_ts_ms=timestamp,
    )

    setting = recovered_runtime.strategy_registry.setting(
        "VWAP_EXHAUSTION_REVERSION_V1"
    )
    assert restored is False
    assert setting.mode is StrategyMode.SHADOW
    assert setting.lifecycle is StrategyLifecycle.SHADOW
    assert recovered_runtime.strategy_registry.main_enabled(
        "VWAP_EXHAUSTION_REVERSION_V1", Side.LONG
    ) is False
    assert recovered_runtime.paused is True
    assert recovered_runtime.paper_portfolio.main.risk_state.faulted is True
    ledger.close()


def test_runtime_recovery_preserves_complete_current_active_lineage(tmp_path: Path) -> None:
    timestamp = 10_000
    run_id = "run-valid-active-recovery"
    strategy_id = "VWAP_EXHAUSTION_REVERSION_V1"
    ledger = SQLiteLedger(tmp_path / "valid-active-recovery.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=timestamp),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    evidence = _complete_active_governance_evidence(timestamp=timestamp)
    challenger = runtime.apply_strategy_governance(
        strategy_id,
        evidence,
        expected_revision=0,
        assessment_ts_ms=timestamp,
    )
    active = runtime.apply_strategy_governance(
        strategy_id,
        evidence,
        expected_revision=1,
        assessment_ts_ms=timestamp,
    )
    assert challenger[-1]["lifecycle"] == "CHALLENGER"
    assert active[-1]["lifecycle"] == "ACTIVE"

    recovered_runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=timestamp + 60_000),
        run_id=run_id,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    recovered = RecoveryState(
        run_id=run_id,
        venue=Venue.BINANCE_USDM.value,
        lifecycle_state="SCANNING",
        payload={},
        transition_count=0,
        recovered_ts_ms=timestamp + 60_000,
    )
    assert recovered_runtime.restore_recovery_state(recovered) is True

    setting = recovered_runtime.strategy_registry.setting(strategy_id)
    assert setting.mode is StrategyMode.ACTIVE
    assert setting.lifecycle is StrategyLifecycle.ACTIVE
    assert setting.revision == 2
    assert recovered_runtime.strategy_registry.main_enabled(strategy_id, Side.LONG) is True
    ledger.close()


@pytest.mark.parametrize(
    "invalid_case",
    (
        "MISSING_EVIDENCE",
        "STALE_EVIDENCE",
        "FUTURE_ASSESSMENT",
        "FUTURE_EVIDENCE",
        "WRONG_STRATEGY_VERSION",
        "WRONG_RELEASE",
        "WRONG_RUN_LINEAGE",
        "COMMON_GATE_FAILED",
        "FAMILY_GATE_FAILED",
    ),
)
def test_runtime_recovery_downgrades_incomplete_or_mismatched_active_lineage(
    tmp_path: Path,
    invalid_case: str,
) -> None:
    timestamp = 20_000
    run_id = f"run-invalid-active-{invalid_case.lower()}"
    setting_row = _complete_active_recovery_setting(
        run_id=run_id,
        timestamp=timestamp,
    )
    recovery_ts_ms = timestamp
    change_evidence = setting_row["change_evidence"]
    assert isinstance(change_evidence, dict)
    lineage = change_evidence["lineage"]
    evidence = change_evidence["evidence"]
    assert isinstance(lineage, dict)
    assert isinstance(evidence, dict)
    if invalid_case == "MISSING_EVIDENCE":
        setting_row.pop("change_evidence")
    elif invalid_case == "STALE_EVIDENCE":
        recovery_ts_ms = timestamp + 60_001
    elif invalid_case == "FUTURE_ASSESSMENT":
        future_ts_ms = timestamp + 1
        setting_row["ts_ms"] = future_ts_ms
        setting_row["settings_updated_ts_ms"] = future_ts_ms
        lineage["assessment_ts_ms"] = future_ts_ms
    elif invalid_case == "FUTURE_EVIDENCE":
        evidence["evaluated_ts_ms"] = timestamp + 1
        evidence["operational_health_evaluated_ts_ms"] = timestamp + 1
    elif invalid_case == "WRONG_STRATEGY_VERSION":
        lineage["strategy_version"] = "OLD-STRATEGY-VERSION"
    elif invalid_case == "WRONG_RELEASE":
        lineage["release_commit"] = "0" * 40
    elif invalid_case == "WRONG_RUN_LINEAGE":
        lineage["run_id"] = "run-other"
    elif invalid_case == "COMMON_GATE_FAILED":
        evidence["operational_health_passed"] = False
    elif invalid_case == "FAMILY_GATE_FAILED":
        evidence["unique_opportunity_count"] = 149

    recovered_runtime, ledger = _restore_active_recovery_setting(
        tmp_path / f"{invalid_case.lower()}.sqlite3",
        setting_row,
        recovery_ts_ms=recovery_ts_ms,
    )

    setting = recovered_runtime.strategy_registry.setting(
        "VWAP_EXHAUSTION_REVERSION_V1"
    )
    assert setting.mode is StrategyMode.SHADOW
    assert setting.lifecycle is StrategyLifecycle.SHADOW
    assert setting.revision == 8
    assert setting.change_reason == "V6_UNPROVEN_ACTIVE_RECOVERY_DOWNGRADED"
    assert recovered_runtime.strategy_registry.main_enabled(
        "VWAP_EXHAUSTION_REVERSION_V1", Side.LONG
    ) is False
    ledger.close()


def test_strategy_settings_cas_and_manual_lock_block_automatic_override() -> None:
    registry = StrategyRegistry()
    strategy_id = "CBR_CONTINUATION_V1"
    changed = registry.configure(
        strategy_id,
        mode=StrategyMode.SHADOW,
        long_enabled=True,
        short_enabled=False,
        expected_revision=0,
        source=StrategyChangeSource.USER_UI,
        reason="USER_RESEARCH_ONLY",
        updated_ts_ms=1_000,
    )

    assert changed.revision == 1
    assert changed.manual_lock is True
    with pytest.raises(StrategyRevisionConflict):
        registry.configure(
            strategy_id,
            mode=StrategyMode.OFF,
            long_enabled=False,
            short_enabled=False,
            expected_revision=0,
        )
    with pytest.raises(StrategyManualLockConflict):
        registry.configure(
            strategy_id,
            mode=StrategyMode.ACTIVE,
            long_enabled=True,
            short_enabled=True,
            expected_revision=1,
            source=StrategyChangeSource.AUTO_GOVERNOR,
            reason="AUTO_PROMOTION",
        )
    assert registry.rows()[1]["mode"] == "SHADOW"


def test_strategy_rollback_creates_new_revision_without_deleting_audit_history() -> None:
    registry = StrategyRegistry()
    strategy_id = "CBR_CONTINUATION_V1"
    registry.configure(
        strategy_id,
        mode=StrategyMode.SHADOW,
        lifecycle=StrategyLifecycle.SHADOW,
        long_enabled=True,
        short_enabled=False,
        expected_revision=0,
        source=StrategyChangeSource.USER_UI,
        reason="USER_TEST_CHANGE",
        updated_ts_ms=1_000,
    )

    restored = registry.rollback(
        strategy_id,
        target_revision=0,
        expected_revision=1,
        source=StrategyChangeSource.USER_UI,
        reason="USER_ROLLBACK_TO_REV_0",
        updated_ts_ms=2_000,
    )

    assert restored.revision == 2
    assert restored.mode is StrategyMode.SHADOW
    assert restored.lifecycle is StrategyLifecycle.SHADOW
    assert restored.short_enabled is True
    assert restored.manual_lock is True
    assert restored.change_reason == "USER_ROLLBACK_TO_REV_0"


def test_governor_requires_multiple_testing_then_swaps_champion_atomically() -> None:
    registry = StrategyRegistry()
    governor = StrategyGovernor()
    # V6 이전 recovery snapshot에 남아 있을 수 있는 같은-family old champion을 재현한다.
    registry.restore_setting(
        "CBR_CONTINUATION_V1",
        mode=StrategyMode.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        revision=1,
        manual_lock=False,
        changed_by=StrategyChangeSource.RECOVERY,
        change_reason="TEST_PRE_V6_RECOVERED_CHAMPION",
        updated_ts_ms=1_000,
        lifecycle=StrategyLifecycle.ACTIVE,
    )
    strategy_id = "BREAKOUT_RETEST_30M_V2"
    insufficient = GovernanceEvidence(
        base_sample_size=35,
        stress_sample_size=35,
        base_expectancy_usdt=Decimal("0.10"),
        stress_expectancy_usdt=Decimal("0.03"),
        base_profit_factor=Decimal("1.20"),
        stress_profit_factor=Decimal("1.05"),
        sample_span_days=3,
        regime_count=3,
        dsr_probability=None,
        pbo=None,
    )
    waiting = governor.assess(registry, strategy_id, insufficient)
    assert waiting.recommended_lifecycle is StrategyLifecycle.SHADOW
    assert "DSR_LT_0_95_OR_MISSING" in waiting.reason_codes
    assert waiting.automatic_action_allowed is False

    shadow_pass = replace(
        insufficient,
        base_sample_size=150,
        stress_sample_size=150,
        base_profit_factor=Decimal("1.30"),
        stress_profit_factor=Decimal("1.30"),
        sample_span_days=8,
        regime_count=2,
        dsr_probability=0.95,
        pbo=0.20,
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=2,
        live_public_sample_size=150,
        cooldown_elapsed=True,
        unique_opportunity_count=150,
        base_payoff_ratio=Decimal("2.10"),
        stress_payoff_ratio=Decimal("2.10"),
        base_return_skew=Decimal("0.20"),
        stress_return_skew=Decimal("0.20"),
        base_largest_trade_contribution=Decimal("0.09"),
        stress_largest_trade_contribution=Decimal("0.09"),
        operational_health_passed=True,
        operational_health_evaluated_ts_ms=1_000,
        evaluation_period="FIXED_OOS_TEST_PERIOD",
        evaluated_ts_ms=1_000,
    )
    challenger = governor.assess(
        registry,
        strategy_id,
        shadow_pass,
        assessment_ts_ms=1_000,
    )
    assert challenger.recommended_lifecycle is StrategyLifecycle.CHALLENGER
    governor.apply(registry, challenger, expected_revision=0, updated_ts_ms=2_000)
    assert registry.setting(strategy_id).lifecycle is StrategyLifecycle.CHALLENGER

    active_pass = GovernanceEvidence(
        base_sample_size=150,
        stress_sample_size=150,
        base_expectancy_usdt=Decimal("0.20"),
        stress_expectancy_usdt=Decimal("0.05"),
        base_profit_factor=Decimal("1.30"),
        stress_profit_factor=Decimal("1.30"),
        sample_span_days=30,
        regime_count=3,
        dsr_probability=0.98,
        pbo=0.20,
        champion_expectancy_usdt=Decimal("0.10"),
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=3,
        live_public_sample_size=150,
        cooldown_elapsed=True,
        strategy_correlation_abs=0.40,
        base_win_rate=Decimal("0.74"),
        stress_win_rate=Decimal("0.72"),
        unique_opportunity_count=150,
        base_payoff_ratio=Decimal("2.10"),
        stress_payoff_ratio=Decimal("2.10"),
        base_return_skew=Decimal("0.20"),
        stress_return_skew=Decimal("0.20"),
        base_largest_trade_contribution=Decimal("0.09"),
        stress_largest_trade_contribution=Decimal("0.09"),
        operational_health_passed=True,
        operational_health_evaluated_ts_ms=2_000,
        evaluation_period="FIXED_OOS_TEST_PERIOD",
        evaluated_ts_ms=2_000,
        **_governance_freshness_inputs(assessment_ts_ms=2_000),
    )
    promotion = governor.assess(
        registry,
        strategy_id,
        active_pass,
        assessment_ts_ms=2_000,
    )
    assert promotion.champion_id == "CBR_CONTINUATION_V1"
    changed = governor.apply(registry, promotion, expected_revision=1, updated_ts_ms=3_000)

    assert len(changed) == 2
    assert registry.setting(strategy_id).lifecycle is StrategyLifecycle.ACTIVE
    assert registry.setting("CBR_CONTINUATION_V1").lifecycle is StrategyLifecycle.CHALLENGER
    assert registry.setting("CBR_CONTINUATION_V1").mode is StrategyMode.SHADOW


def test_governor_quarantines_fault_but_never_overrides_user_lock() -> None:
    governor = StrategyGovernor()
    registry = StrategyRegistry()
    evidence = GovernanceEvidence(
        base_sample_size=0,
        stress_sample_size=0,
        base_expectancy_usdt=None,
        stress_expectancy_usdt=None,
        base_profit_factor=None,
        stress_profit_factor=None,
        sample_span_days=0,
        regime_count=0,
        dsr_probability=None,
        pbo=None,
        operational_fault=True,
    )
    assessment = governor.assess(registry, "CBR_CONTINUATION_V1", evidence)
    assert assessment.recommended_lifecycle is StrategyLifecycle.QUARANTINED
    governor.apply(registry, assessment, expected_revision=0, updated_ts_ms=1_000)
    assert registry.setting("CBR_CONTINUATION_V1").mode is StrategyMode.OFF

    locked = StrategyRegistry()
    locked.configure(
        "BREAKOUT_RETEST_30M_V2",
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        expected_revision=0,
        manual_lock=False,
        source=StrategyChangeSource.AUTO_GOVERNOR,
        reason="TEST_PROVEN_CHAMPION",
    )
    locked.configure(
        "BREAKOUT_RETEST_30M_V2",
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        expected_revision=1,
        manual_lock=True,
        source=StrategyChangeSource.USER_UI,
        reason="USER_LOCKS_PROVEN_CHAMPION",
    )
    blocked = governor.assess(locked, "BREAKOUT_RETEST_30M_V2", evidence)
    assert blocked.reason_codes == ("USER_MANUAL_LOCK",)
    assert blocked.automatic_action_allowed is False


def test_governor_does_not_retire_low_win_high_payoff_breakout() -> None:
    governor = StrategyGovernor()
    registry = StrategyRegistry()
    mature = GovernanceEvidence(
        base_sample_size=150,
        stress_sample_size=150,
        base_expectancy_usdt=Decimal("0.10"),
        stress_expectancy_usdt=Decimal("0.03"),
        base_profit_factor=Decimal("1.30"),
        stress_profit_factor=Decimal("1.30"),
        sample_span_days=8,
        regime_count=2,
        dsr_probability=0.95,
        pbo=0.20,
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=2,
        live_public_sample_size=150,
        cooldown_elapsed=True,
        base_win_rate=Decimal("0.35"),
        stress_win_rate=Decimal("0.38"),
        unique_opportunity_count=150,
        base_payoff_ratio=Decimal("2.10"),
        stress_payoff_ratio=Decimal("2.10"),
        base_return_skew=Decimal("0.20"),
        stress_return_skew=Decimal("0.20"),
        base_largest_trade_contribution=Decimal("0.09"),
        stress_largest_trade_contribution=Decimal("0.09"),
        operational_health_passed=True,
        operational_health_evaluated_ts_ms=1_000,
        evaluation_period="FIXED_OOS_TEST_PERIOD",
        evaluated_ts_ms=1_000,
    )

    assessment = governor.assess(
        registry,
        "CBR_CONTINUATION_V1",
        mature,
        assessment_ts_ms=1_000,
    )

    assert assessment.recommended_lifecycle is StrategyLifecycle.CHALLENGER
    assert assessment.reason_codes == ("SHADOW_GATES_PASSED",)
    changed = governor.apply(registry, assessment, expected_revision=0, updated_ts_ms=1_000)
    assert changed[0]["mode"] == "SHADOW"
    assert len(registry.revision_history("CBR_CONTINUATION_V1")) == 2


def test_governor_still_requires_two_regimes_for_multi_regime_strategy() -> None:
    governor = StrategyGovernor()
    registry = StrategyRegistry()
    one_regime = GovernanceEvidence(
        base_sample_size=150,
        stress_sample_size=150,
        base_expectancy_usdt=Decimal("0.10"),
        stress_expectancy_usdt=Decimal("0.03"),
        base_profit_factor=Decimal("1.30"),
        stress_profit_factor=Decimal("1.30"),
        sample_span_days=8,
        regime_count=1,
        dsr_probability=0.95,
        pbo=0.20,
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=2,
        live_public_sample_size=150,
        cooldown_elapsed=True,
        base_win_rate=Decimal("0.72"),
        stress_win_rate=Decimal("0.70"),
        unique_opportunity_count=150,
        base_payoff_ratio=Decimal("2.10"),
        stress_payoff_ratio=Decimal("2.10"),
        base_return_skew=Decimal("0.20"),
        stress_return_skew=Decimal("0.20"),
        base_largest_trade_contribution=Decimal("0.09"),
        stress_largest_trade_contribution=Decimal("0.09"),
        operational_health_passed=True,
        operational_health_evaluated_ts_ms=1_000,
        evaluation_period="FIXED_OOS_TEST_PERIOD",
        evaluated_ts_ms=1_000,
    )

    assessment = governor.assess(
        registry,
        "CBR_CONTINUATION_V1",
        one_regime,
        assessment_ts_ms=1_000,
    )

    assert assessment.recommended_lifecycle is StrategyLifecycle.SHADOW
    assert assessment.reason_codes == ("REGIME_COUNT_LT_2",)
    assert assessment.automatic_action_allowed is False


def test_governor_does_not_retire_or_promote_sparse_100_percent_sample() -> None:
    governor = StrategyGovernor()
    registry = StrategyRegistry()
    sparse = GovernanceEvidence(
        base_sample_size=1,
        stress_sample_size=1,
        base_expectancy_usdt=Decimal("0.05"),
        stress_expectancy_usdt=Decimal("0.01"),
        base_profit_factor=Decimal("1.30"),
        stress_profit_factor=Decimal("1.30"),
        sample_span_days=0,
        regime_count=1,
        dsr_probability=0.95,
        pbo=0.20,
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=2,
        live_public_sample_size=1,
        base_win_rate=Decimal("1"),
        stress_win_rate=Decimal("1"),
        unique_opportunity_count=1,
        base_payoff_ratio=Decimal("2.10"),
        stress_payoff_ratio=Decimal("2.10"),
        base_return_skew=Decimal("0.20"),
        stress_return_skew=Decimal("0.20"),
        base_largest_trade_contribution=Decimal("0.09"),
        stress_largest_trade_contribution=Decimal("0.09"),
        operational_health_passed=True,
        operational_health_evaluated_ts_ms=1_000,
        evaluation_period="FIXED_OOS_TEST_PERIOD",
        evaluated_ts_ms=1_000,
    )

    assessment = governor.assess(
        registry,
        "CBR_CONTINUATION_V1",
        sparse,
        assessment_ts_ms=1_000,
    )

    assert assessment.recommended_lifecycle is StrategyLifecycle.SHADOW
    assert "UNIQUE_OPPORTUNITIES_LT_150" in assessment.reason_codes
    assert assessment.automatic_action_allowed is False


def test_governance_evidence_reads_family_metrics_without_universal_win_gate() -> None:
    evidence = GovernanceEvidence.from_reports(
        {
            "sample_size": 30,
            "expectancy_usdt": "0.10",
            "profit_factor": "1.20",
            "win_rate": "0.70",
            "win_rate_ci95": {"lower": "0.52", "upper": "0.83"},
            "payoff_ratio": "2.10",
            "return_skew": "0.40",
            "largest_trade_contribution": "0.09",
            "unique_opportunity_count": 30,
            "sample_span_days": 8,
            "regime_count": 2,
        },
        {
            "sample_size": 30,
            "expectancy_usdt": "0.03",
            "profit_factor": "1.05",
            "win_rate": "0.73",
            "win_rate_ci95": {"lower": "0.54", "upper": "0.85"},
            "payoff_ratio": "2.00",
            "return_skew": "0.30",
            "largest_trade_contribution": "0.08",
            "unique_opportunity_count": 30,
        },
    )

    assert evidence.base_win_rate == Decimal("0.70")
    assert evidence.stress_win_rate == Decimal("0.73")
    assert evidence.unique_opportunity_count == 30
    assert evidence.base_win_rate_ci95_lower == Decimal("0.52")
    assert evidence.stress_win_rate_ci95_lower == Decimal("0.54")
    assert evidence.base_payoff_ratio == Decimal("2.10")
    assert evidence.stress_return_skew == Decimal("0.30")


def test_governance_evidence_does_not_coerce_string_false_positive_gates() -> None:
    evidence = GovernanceEvidence.from_reports(
        {"sample_size": 150, "expectancy_usdt": "0.20", "profit_factor": "1.30"},
        {"sample_size": 150, "expectancy_usdt": "0.10", "profit_factor": "1.30"},
        multiple_testing={
            "parameter_robustness_passed": "false",
            "risk_contract_passed": "false",
            "cooldown_elapsed": "false",
        },
    )

    assert evidence.parameter_robustness_passed is False
    assert evidence.risk_contract_passed is False
    assert evidence.cooldown_elapsed is False


def test_runtime_governance_cycle_quarantines_fault_but_does_not_promote_empty_sample() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    faulted = runtime.paper_portfolio.shadows["CBR_CONTINUATION_V1:BASE"]
    faulted.risk_state.faulted = True

    result = runtime.run_strategy_governance_cycle()

    assert result["promotion_without_formal_oos_evidence"] is False
    assert len(result["changes"]) == 1
    assert runtime.strategy_registry.setting("CBR_CONTINUATION_V1").lifecycle is (
        StrategyLifecycle.QUARANTINED
    )
    assert runtime.strategy_registry.setting("VWAP_EXHAUSTION_REVERSION_V1").lifecycle is (
        StrategyLifecycle.SHADOW
    )


def _runtime_with_healthy_governance_supervisor(
    *,
    ledger: SQLiteLedger | None = None,
) -> PaperRuntime:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    runtime.market_data_state = MarketDataState.LIVE
    runtime.paused = False
    runtime._manual_pause_requested = False
    runtime._storage_entry_allowed = True
    runtime.runtime_health_flags = []
    runtime._supervisor = SimpleNamespace(  # type: ignore[assignment]
        running=lambda: True,
        telemetry=SimpleNamespace(
            consumer_running=True,
            consumer_fault_active=False,
            queue_overload_active=False,
            entry_locked=False,
            critical_lag_active=False,
        ),
    )
    return runtime


def test_live_governance_restores_only_revalidated_global_quarantine(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-operational-recovery.sqlite3")
    runtime = _runtime_with_healthy_governance_supervisor(ledger=ledger)
    _restore_operational_quarantine_cohort(runtime.strategy_registry)
    settings_before = ledger.count("strategy_settings")
    incidents_before = ledger.count("incidents")

    result = runtime.run_strategy_governance_cycle()

    assert tuple(row["strategy_id"] for row in result["changes"]) == (
        _ELIGIBLE_DIRECTION_RESEARCH_IDS
    )
    for strategy_id in _ELIGIBLE_DIRECTION_RESEARCH_IDS:
        setting = runtime.strategy_registry.setting(strategy_id)
        assert setting.mode is StrategyMode.SHADOW
        assert setting.lifecycle is (
            StrategyLifecycle.SHADOW
            if strategy_id in _CURRENT_DIRECTION_RESEARCH_IDS
            else StrategyLifecycle.CHALLENGER
        )
        assert setting.changed_by is StrategyChangeSource.RECOVERY
        assert setting.change_reason == (
            "V9_USER_REQUESTED_SHADOW_DEFAULT_ON_AFTER_GLOBAL_OPERATIONAL_RECOVERY"
        )
    assert ledger.count("strategy_settings") == settings_before + 6
    assert ledger.count("incidents") == incidents_before + 6
    ledger.close()


def test_live_governance_keeps_global_quarantine_until_health_is_proven() -> None:
    runtime = _runtime_with_healthy_governance_supervisor()
    _restore_operational_quarantine_cohort(runtime.strategy_registry)
    runtime._supervisor.telemetry.critical_lag_active = True  # type: ignore[union-attr]

    result = runtime.run_strategy_governance_cycle()

    assert result["changes"] == []
    for strategy_id in _ELIGIBLE_DIRECTION_RESEARCH_IDS:
        setting = runtime.strategy_registry.setting(strategy_id)
        assert setting.mode is StrategyMode.OFF
        assert setting.lifecycle is StrategyLifecycle.QUARANTINED
        assert setting.changed_by is StrategyChangeSource.AUTO_GOVERNOR


def test_runtime_operational_health_requires_exact_two_strategy_accounts() -> None:
    runtime = _runtime_with_healthy_governance_supervisor()
    strategy_id = "CBR_CONTINUATION_V1"
    accounts = runtime.paper_portfolio.league_account_rows()
    evaluated_ts_ms = runtime.clock.utc_ms()

    healthy = runtime._strategy_governance_operational_evidence(
        strategy_id,
        accounts,
        evaluated_ts_ms=evaluated_ts_ms,
    )
    missing_account = runtime._strategy_governance_operational_evidence(
        strategy_id,
        [
            account
            for account in accounts
            if account.get("account_id") != f"{strategy_id}:STRESS"
        ],
        evaluated_ts_ms=evaluated_ts_ms,
    )

    assert healthy == {
        "operational_fault": False,
        "operational_health_passed": True,
        "operational_health_evaluated_ts_ms": evaluated_ts_ms,
    }
    assert missing_account["operational_fault"] is False
    assert missing_account["operational_health_passed"] is False
    assert missing_account["operational_health_evaluated_ts_ms"] is None


@pytest.mark.parametrize(
    "unhealthy_state",
    (
        "READY_MODE",
        "MARKET_DISCONNECTED",
        "PAUSED",
        "MANUAL_PAUSE",
        "SUPERVISOR_NOT_RUNNING",
        "CONSUMER_NOT_RUNNING",
        "SUPERVISOR_ENTRY_LOCK",
        "CRITICAL_LAG",
        "STORAGE_BLOCKED",
        "DATA_GAP",
        "STALE_TRADE",
        "FEATURE_INPUT_FAULT",
        "RECOVERY_REVALIDATION",
        "ENTRY_LOCK_HEALTH_FLAG",
        "RECOVERY_HEALTH_FLAG",
    ),
)
def test_runtime_unclear_or_locked_state_is_never_positive_operational_health(
    unhealthy_state: str,
) -> None:
    runtime = _runtime_with_healthy_governance_supervisor()
    telemetry = runtime._supervisor.telemetry  # type: ignore[union-attr]
    if unhealthy_state == "READY_MODE":
        runtime.mode = RuntimeMode.READY
    elif unhealthy_state == "MARKET_DISCONNECTED":
        runtime.market_data_state = MarketDataState.DISCONNECTED
    elif unhealthy_state == "PAUSED":
        runtime.paused = True
    elif unhealthy_state == "MANUAL_PAUSE":
        runtime._manual_pause_requested = True
    elif unhealthy_state == "SUPERVISOR_NOT_RUNNING":
        runtime._supervisor.running = lambda: False  # type: ignore[method-assign,union-attr]
    elif unhealthy_state == "CONSUMER_NOT_RUNNING":
        telemetry.consumer_running = False
    elif unhealthy_state == "SUPERVISOR_ENTRY_LOCK":
        telemetry.entry_locked = True
    elif unhealthy_state == "CRITICAL_LAG":
        telemetry.critical_lag_active = True
    elif unhealthy_state == "STORAGE_BLOCKED":
        runtime._storage_entry_allowed = False
    elif unhealthy_state == "DATA_GAP":
        runtime.data_gap_since_ms["BTCUSDT"] = runtime.clock.utc_ms()
    elif unhealthy_state == "STALE_TRADE":
        runtime._stale_trade_symbols.add("BTCUSDT")
    elif unhealthy_state == "FEATURE_INPUT_FAULT":
        runtime._feature_input_fault_symbols.add("BTCUSDT")
    elif unhealthy_state == "RECOVERY_REVALIDATION":
        runtime._recovery_revalidation_symbols.add("BTCUSDT")
    elif unhealthy_state == "ENTRY_LOCK_HEALTH_FLAG":
        runtime.runtime_health_flags.append("ENTRY_LOCK_TEST")
    else:
        runtime.runtime_health_flags.append("RECOVERY_TEST_LOCK")

    evidence = runtime._strategy_governance_operational_evidence(
        "CBR_CONTINUATION_V1",
        runtime.paper_portfolio.league_account_rows(),
        evaluated_ts_ms=runtime.clock.utc_ms(),
    )

    assert evidence["operational_health_passed"] is False
    assert evidence["operational_health_evaluated_ts_ms"] is None


def test_runtime_report_governance_paths_share_cycle_health_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_with_healthy_governance_supervisor()
    observed: list[tuple[int | None, int, int | None, bool | None]] = []
    original_assess = runtime.strategy_governor.assess

    def capture_assessment(
        registry: StrategyRegistry,
        strategy_id: str,
        evidence: GovernanceEvidence,
        *,
        assessment_ts_ms: int | None = None,
    ):
        observed.append(
            (
                assessment_ts_ms,
                evidence.evaluated_ts_ms,
                evidence.operational_health_evaluated_ts_ms,
                evidence.operational_health_passed,
            )
        )
        return original_assess(
            registry,
            strategy_id,
            evidence,
            assessment_ts_ms=assessment_ts_ms,
        )

    monkeypatch.setattr(runtime.strategy_governor, "assess", capture_assessment)
    runtime.run_strategy_governance_cycle()
    runtime.strategy_governance(include_history=False)

    assert len(observed) == len(runtime.strategy_registry.strategy_ids) * 2
    assert all(
        assessment_ts_ms
        == evaluated_ts_ms
        == operational_health_evaluated_ts_ms
        == runtime.clock.utc_ms()
        and operational_health_passed is True
        for (
            assessment_ts_ms,
            evaluated_ts_ms,
            operational_health_evaluated_ts_ms,
            operational_health_passed,
        ) in observed
    )


@pytest.mark.parametrize(
    "fault_source",
    ("ACCOUNT", "MAIN_RISK", "PERSISTENCE", "CONSUMER", "QUEUE"),
)
def test_runtime_operational_faults_never_produce_positive_health(
    fault_source: str,
) -> None:
    runtime = _runtime_with_healthy_governance_supervisor()
    strategy_id = "CBR_CONTINUATION_V1"
    telemetry = runtime._supervisor.telemetry  # type: ignore[union-attr]
    if fault_source == "ACCOUNT":
        runtime.paper_portfolio.shadows[f"{strategy_id}:BASE"].risk_state.faulted = True
    elif fault_source == "MAIN_RISK":
        runtime.paper_portfolio.main.risk_state.faulted = True
    elif fault_source == "PERSISTENCE":
        runtime._persistence_fault_active = True
    elif fault_source == "CONSUMER":
        telemetry.consumer_fault_active = True
    else:
        telemetry.queue_overload_active = True

    evidence = runtime._strategy_governance_operational_evidence(
        strategy_id,
        runtime.paper_portfolio.league_account_rows(),
        evaluated_ts_ms=runtime.clock.utc_ms(),
    )

    assert evidence["operational_fault"] is True
    assert evidence["operational_health_passed"] is False
    assert evidence["operational_health_evaluated_ts_ms"] is None


def test_governor_never_quarantines_active_strategy_from_one_bad_evaluation() -> None:
    registry = StrategyRegistry()
    governor = StrategyGovernor()
    strategy_id = "BREAKOUT_RETEST_30M_V2"
    registry.configure(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        expected_revision=0,
        source=StrategyChangeSource.AUTO_GOVERNOR,
        reason="TEST_PROVEN_CHAMPION",
    )
    one_bad_cycle = GovernanceEvidence(
        base_sample_size=120,
        stress_sample_size=120,
        base_expectancy_usdt=Decimal("-0.10"),
        stress_expectancy_usdt=Decimal("-0.20"),
        base_profit_factor=Decimal("0.80"),
        stress_profit_factor=Decimal("0.70"),
        sample_span_days=30,
        regime_count=3,
        dsr_probability=0.10,
        pbo=0.90,
        recent_expectancy_usdt=Decimal("-0.20"),
        recent_profit_factor=Decimal("0.70"),
        full_oos_degraded_evaluations=1,
        recent_oos_degraded_evaluations=1,
    )

    assessment = governor.assess(registry, strategy_id, one_bad_cycle)

    assert assessment.recommended_lifecycle is StrategyLifecycle.ACTIVE
    assert assessment.reason_codes == (
        "ACTIVE_GATES_HEALTHY",
        "EVIDENCE_FRESHNESS_NOT_PROVEN",
    )
    assert assessment.automatic_action_allowed is False

    second_bad_cycle = replace(
        one_bad_cycle,
        full_oos_degraded_evaluations=2,
        recent_oos_degraded_evaluations=2,
    )
    assessment = governor.assess(registry, strategy_id, second_bad_cycle)
    assert assessment.recommended_lifecycle is StrategyLifecycle.QUARANTINED
    assert assessment.reason_codes == ("COST_AFTER_DEGRADATION",)


def test_governor_never_quarantines_active_only_for_low_win_rate() -> None:
    registry = StrategyRegistry()
    governor = StrategyGovernor()
    strategy_id = "BREAKOUT_RETEST_30M_V2"
    registry.configure(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        expected_revision=0,
        source=StrategyChangeSource.AUTO_GOVERNOR,
        reason="TEST_PROVEN_CHAMPION",
    )
    one_low_cycle = GovernanceEvidence(
        base_sample_size=120,
        stress_sample_size=120,
        base_expectancy_usdt=Decimal("0.10"),
        stress_expectancy_usdt=Decimal("0.03"),
        base_profit_factor=Decimal("1.20"),
        stress_profit_factor=Decimal("1.05"),
        sample_span_days=30,
        regime_count=3,
        dsr_probability=0.95,
        pbo=0.30,
        base_win_rate=Decimal("0.69"),
        stress_win_rate=Decimal("0.72"),
    )

    first = governor.assess(registry, strategy_id, one_low_cycle)
    assert first.recommended_lifecycle is StrategyLifecycle.ACTIVE
    assert first.reason_codes == (
        "ACTIVE_GATES_HEALTHY",
        "EVIDENCE_FRESHNESS_NOT_PROVEN",
    )

    second = governor.assess(registry, strategy_id, one_low_cycle)
    assert second.recommended_lifecycle is StrategyLifecycle.ACTIVE
    assert second.reason_codes == (
        "ACTIVE_GATES_HEALTHY",
        "EVIDENCE_FRESHNESS_NOT_PROVEN",
    )


def test_governor_quarantines_repeated_stress_cost_degradation_only_after_hysteresis() -> None:
    registry = StrategyRegistry()
    governor = StrategyGovernor()
    strategy_id = "BREAKOUT_RETEST_30M_V2"
    registry.configure(
        strategy_id,
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        expected_revision=0,
        source=StrategyChangeSource.AUTO_GOVERNOR,
        reason="TEST_PROVEN_CHAMPION",
    )
    first_stress_failure = GovernanceEvidence(
        base_sample_size=120,
        stress_sample_size=120,
        base_expectancy_usdt=Decimal("0.10"),
        stress_expectancy_usdt=Decimal("-0.20"),
        base_profit_factor=Decimal("1.20"),
        stress_profit_factor=Decimal("0.70"),
        sample_span_days=30,
        regime_count=3,
        dsr_probability=0.95,
        pbo=0.30,
        recent_stress_expectancy_usdt=Decimal("-0.10"),
        recent_stress_profit_factor=Decimal("0.80"),
        full_oos_degraded_evaluations=1,
        recent_oos_degraded_evaluations=1,
    )

    first = governor.assess(registry, strategy_id, first_stress_failure)
    second = governor.assess(
        registry,
        strategy_id,
        replace(
            first_stress_failure,
            full_oos_degraded_evaluations=2,
            recent_oos_degraded_evaluations=2,
        ),
    )

    assert first.recommended_lifecycle is StrategyLifecycle.ACTIVE
    assert first.automatic_action_allowed is False
    assert second.recommended_lifecycle is StrategyLifecycle.QUARANTINED
    assert second.reason_codes == ("COST_AFTER_DEGRADATION",)


def test_runtime_persists_auto_governor_evidence_and_audit(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "governor.sqlite3")
    clock = DeterministicClock(current_utc_ms=1_000)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=clock,
        run_id="run-governor-audit",
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    evidence = GovernanceEvidence(
        base_sample_size=150,
        stress_sample_size=150,
        base_expectancy_usdt=Decimal("0.10"),
        stress_expectancy_usdt=Decimal("0.03"),
        base_profit_factor=Decimal("1.20"),
        stress_profit_factor=Decimal("1.15"),
        sample_span_days=8,
        regime_count=2,
        dsr_probability=0.95,
        pbo=0.20,
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=2,
        live_public_sample_size=150,
        cooldown_elapsed=True,
        base_win_rate=Decimal("0.72"),
        stress_win_rate=Decimal("0.70"),
        unique_opportunity_count=150,
        base_win_rate_ci95_lower=Decimal("0.40"),
        stress_win_rate_ci95_lower=Decimal("0.40"),
        base_payoff_ratio=Decimal("1.30"),
        stress_payoff_ratio=Decimal("1.30"),
        operational_health_passed=True,
        operational_health_evaluated_ts_ms=1_000,
        evaluation_period="WALK_FORWARD_OOS_2026Q3",
        evaluated_ts_ms=1_000,
    )

    changed = runtime.apply_strategy_governance(
        "VWAP_EXHAUSTION_REVERSION_V1",
        evidence,
        expected_revision=0,
    )

    assert changed[0]["lifecycle"] == "CHALLENGER"
    settings = ledger.list_strategy_settings(runtime.run_id)
    latest = [row for row in settings if row["strategy_id"] == "VWAP_EXHAUSTION_REVERSION_V1"][-1]
    assert latest["changed_by"] == "AUTO_GOVERNOR"
    assert latest["change_evidence"]["evidence"]["evaluation_period"] == ("WALK_FORWARD_OOS_2026Q3")
    lineage = latest["change_evidence"]["lineage"]
    assert lineage == {
        "schema_version": 1,
        "run_id": runtime.run_id,
        "strategy_id": "VWAP_EXHAUSTION_REVERSION_V1",
        "strategy_version": STRATEGY_VERSION,
        "descriptor_strategy_version": "V1",
        "app_version": APP_VERSION,
        "release_commit": git_commit(),
        "assessment_ts_ms": 1_000,
        "settings_revision": 1,
    }
    incidents = ledger.list_incidents(category="AUTO_GOVERNOR_TRANSITION")
    assert len(incidents) == 1
    assert incidents[0]["payload"]["assessment"]["automatic_action_allowed"] is True
    transition = incidents[0]["payload"]
    assert transition["previous_state"] == ("SHADOW|SHADOW|LONG=ON|SHORT=ON|MANUAL_LOCK=OFF")
    assert transition["new_state"] == ("CHALLENGER|SHADOW|LONG=ON|SHORT=ON|MANUAL_LOCK=OFF")
    assert transition["actor"] == "AUTO_GOVERNOR"
    assert transition["strategy_id"] == "VWAP_EXHAUSTION_REVERSION_V1"
    assert transition["request_revision"] == 0
    assert transition["response_revision"] == 1
    assert transition["reversible"] is True
    assert latest["transition_id"] == transition["transition_id"]
    assert latest["previous_state"] == transition["previous_state"]
    assert latest["new_state"] == transition["new_state"]
    ledger.close()


def test_strategy_history_statistics_are_computed_once_per_snapshot(monkeypatch) -> None:
    robust_calls = 0
    percentile_calls = 0
    original_robust_z = runtime_evaluator_module.robust_z_from_sorted
    original_percentile = runtime_evaluator_module.rolling_percentile_from_sorted

    def counted_robust_z(history, current: float) -> float:
        nonlocal robust_calls
        robust_calls += 1
        return original_robust_z(history, current)

    def counted_percentile(history, current: float) -> float:
        nonlocal percentile_calls
        percentile_calls += 1
        return original_percentile(history, current)

    monkeypatch.setattr(runtime_evaluator_module, "robust_z_from_sorted", counted_robust_z)
    monkeypatch.setattr(
        runtime_evaluator_module,
        "rolling_percentile_from_sorted",
        counted_percentile,
    )

    decisions = StrategySignalEvaluator().evaluate(
        StrategyRegistry(),
        features(),
        Regime.RANGE,
    )

    assert len(decisions) == 12
    assert robust_calls == 4
    assert percentile_calls == 5


def test_strategy_sorted_history_evicts_with_same_exact_window() -> None:
    evaluator = StrategySignalEvaluator(history_limit=3)
    registry = StrategyRegistry()
    snapshots = [
        replace(
            features(),
            ts_ms=index * 500,
            signed_notional_3s=float(index - 2),
            price_response_efficiency=index / 10,
            compression_ratio=index / 20,
            efficiency_ratio_30s=index / 30,
            micro_vwap_10s=99.0 + index / 10,
        )
        for index in range(5)
    ]

    for snapshot in snapshots:
        evaluator.evaluate(registry, snapshot, Regime.RANGE)

    window = list(evaluator._history[snapshots[-1].symbol])
    ordered = evaluator._sorted_history[snapshots[-1].symbol]
    assert window == snapshots[-3:]
    assert ordered.flow == sorted(abs(item.signed_notional_3s) for item in window)
    assert ordered.price_response == sorted(item.price_response_efficiency for item in window)
    assert ordered.compression == sorted(item.compression_ratio for item in window)
    assert ordered.efficiency == sorted(item.efficiency_ratio_30s for item in window)
    assert ordered.signed_notional == sorted(item.signed_notional_3s for item in window)
    assert ordered.depth_adjusted_ofi == sorted(item.depth_adjusted_ofi_3s_bps for item in window)
    assert ordered.bid_book_slope == sorted(item.bid_book_slope_10 for item in window)
    assert ordered.ask_book_slope == sorted(item.ask_book_slope_10 for item in window)


@pytest.mark.parametrize(
    ("side", "prices"),
    [
        (Side.LONG, (100.0, 102.0, 101.0, 101.2)),
        (Side.SHORT, (100.0, 98.0, 99.0, 98.8)),
    ],
)
def test_pullback_metrics_use_prefix_event_time_and_require_price_reacceleration(
    side: Side,
    prices: tuple[float, ...],
) -> None:
    snapshots = [
        replace(features(), ts_ms=timestamp, mid=price)
        for timestamp, price in zip((0, 1_000, 2_000, 2_500), prices, strict=True)
    ]
    metrics = _pullback_metrics(
        snapshots[:-1],
        snapshots[-1],
        side,
        maximum_duration_seconds=10,
    )
    assert metrics.duration_seconds == 1.5
    assert metrics.maximum_retrace_fraction == pytest.approx(0.5)
    assert metrics.price_reaccelerated

    no_reacceleration = _pullback_metrics(
        snapshots[:-2],
        snapshots[-2],
        side,
        maximum_duration_seconds=10,
    )
    assert not no_reacceleration.price_reaccelerated

    future = replace(features(), ts_ms=9_000, mid=1_000 if side is Side.LONG else 1.0)
    with_future_in_history = _pullback_metrics(
        [*snapshots[:-1], future],
        snapshots[-1],
        side,
        maximum_duration_seconds=10,
    )
    assert with_future_in_history == metrics


def test_runtime_temporal_gate_uses_event_time_and_resets() -> None:
    evaluator = StrategySignalEvaluator()
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_000, aligned=True) == 0
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_299, aligned=True) == 299
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_300, aligned=True) == 300
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_400, aligned=False) == 0
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 2_000, aligned=True) == 0


def test_shadow_accounts_are_independent_by_strategy_and_cost_profile() -> None:
    registry = StrategyRegistry()
    ledger = ShadowLedger(registry.strategy_ids)
    position = ShadowPosition(
        shadow_trade_id="shadow-lsa-base-1",
        symbol="BTCUSDT",
        side=Side.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        entry_fee_usdt=Decimal("0.05"),
        entry_slippage_usdt=Decimal("0.02"),
        opened_ts_ms=1_000,
    )
    ledger.open("LSA_REVERSAL_V1", CostProfile.BASE, position)
    trade = ledger.close(
        "LSA_REVERSAL_V1",
        CostProfile.BASE,
        exit_price=Decimal("101"),
        exit_fee_usdt=Decimal("0.05"),
        exit_slippage_usdt=Decimal("0.03"),
        closed_ts_ms=2_000,
        exit_reason="TAKE_PROFIT_1",
    )

    assert trade.gross_pnl_usdt == Decimal("1")
    assert trade.net_pnl_usdt == Decimal("0.85")
    assert ledger.account("LSA_REVERSAL_V1", CostProfile.BASE).current_equity_usdt == Decimal(
        "1000.85"
    )
    assert ledger.account("LSA_REVERSAL_V1", CostProfile.STRESS).current_equity_usdt == Decimal(
        "1000"
    )
    assert ledger.account("CBR_CONTINUATION_V1", CostProfile.BASE).current_equity_usdt == Decimal(
        "1000"
    )


def test_live_depth_skips_retired_strategies_without_fake_probability() -> None:
    clock = DeterministicClock()
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-registry-live",
        clock=clock,
    )
    runtime.ingest_live_event(
        MarketEvent(
            event_id="depth-1",
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="DEPTH_UPDATE",
            venue_ts_ms=clock.utc_ms(),
            receive_monotonic_ns=clock.monotonic_ns(),
            sequence_start=1,
            sequence_end=1,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={
                "bid": "99.9",
                "bid_qty": "5",
                "ask": "100.1",
                "ask_qty": "5",
                "bids": [["99.9", "5"], ["99.8", "8"]],
                "asks": [["100.1", "5"], ["100.2", "8"]],
            },
        )
    )

    decisions = runtime.strategy_decisions()
    assert runtime.strategy_evaluation_count == 12
    assert {decision.strategy_id for decision in decisions} == set(
        runtime.strategy_registry.strategy_ids
    ) - {
        "LSA_REVERSAL_V1",
        "OFI_CONTINUATION_PULLBACK_V1",
        "QUEUE_MICROPRICE_MOMENTUM_V1",
        "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        "HOURLY_MOMENTUM_BREAKOUT_V1",
        "AGGRESSOR_FLOW_CONTINUATION_V1",
        "MULTILEVEL_MICROPRICE_MOMENTUM_V1",
        "OFI_RETURN_CONFLUENCE_V1",
        "BOOK_SLOPE_ASYMMETRY_V1",
    }
    assert all(decision.tp_probability is None for decision in decisions)
    assert len(runtime.dashboard()["shadow_accounts"]) == 30
    assert len(runtime.dashboard()["league_accounts"]) == 30
