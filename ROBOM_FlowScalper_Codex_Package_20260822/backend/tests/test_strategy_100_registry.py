# 20개 alpha와 5개 exit의 100후보 사전등록·차단·PAPER 안전을 검증한다.

from __future__ import annotations

from backend.app.execution.trailing import TrailingModel
from backend.app.research import (
    ALPHA_EVALUATORS,
    ALPHA_FAMILIES,
    EXIT_MODULES,
    TrialLifecycle,
    cost_covered_exit_variant_manifest,
    cost_covered_exit_variant_trials,
    preregistered_trials,
    trailing_policy_for_exit,
    trial_manifest,
)


def test_registry_has_exactly_twenty_by_five_unique_trials() -> None:
    trials = preregistered_trials()

    assert len(ALPHA_FAMILIES) == 20
    assert len(EXIT_MODULES) == 5
    assert len(trials) == 100
    assert len({trial.trial_id for trial in trials}) == 100
    assert trials[0].trial_id == "ALPHA_F01_EXIT_E01_V1"
    assert trials[-1].trial_id == "ALPHA_F20_EXIT_E05_V1"
    assert [trial.trial_number for trial in trials] == list(range(1, 101))


def test_siho_unknown_rules_block_ten_trials_without_shrinking_registry() -> None:
    trials = preregistered_trials()
    siho_trials = [trial for trial in trials if trial.alpha.family_id in {"F01", "F02"}]

    assert len(siho_trials) == 10
    assert all(trial.lifecycle is TrialLifecycle.BLOCKED for trial in siho_trials)
    assert all(not trial.screening_eligible for trial in siho_trials)
    assert all(trial.alpha.blocker_codes for trial in siho_trials)
    assert sum(trial.screening_eligible for trial in trials) == 90


def test_every_screening_eligible_alpha_has_a_real_evaluator_binding() -> None:
    trials = preregistered_trials()
    eligible_families = {trial.alpha.family_id for trial in trials if trial.screening_eligible}

    assert eligible_families == set(ALPHA_EVALUATORS)
    assert eligible_families == {f"F{number:02d}" for number in range(3, 21)}
    assert all(
        trial.alpha.evaluator_id == f"ALPHA_EVALUATOR_{trial.alpha.family_id}_V1"
        for trial in trials
        if trial.screening_eligible
    )


def test_all_preregistered_trials_are_offline_paper_only_by_default() -> None:
    trials = preregistered_trials()

    assert all(trial.paper_only for trial in trials)
    assert all(not trial.runtime_active for trial in trials)
    assert all(not trial.live_shadow_enabled for trial in trials)


def test_exit_modules_bind_to_one_shared_paper_trailing_engine() -> None:
    e02 = trailing_policy_for_exit("E02")
    e03 = trailing_policy_for_exit("E03")
    e04 = trailing_policy_for_exit("E04")
    e05 = trailing_policy_for_exit("E05")
    e06 = trailing_policy_for_exit("E06")

    assert trailing_policy_for_exit("E01") is None
    assert e02 is not None and e02.partial_tp_required is True
    assert e03 is not None and e03.model is TrailingModel.FIXED_RATE
    assert e04 is not None and e04.model is TrailingModel.CHANDELIER_STRUCTURE
    assert e05 is not None and e05.model is TrailingModel.EDGE_ADAPTIVE
    assert e06 is not None and e06.partial_tp_required is True


def test_cost_covered_e06_batch_is_separate_from_the_frozen_100_trials() -> None:
    frozen = preregistered_trials()
    variants = cost_covered_exit_variant_trials()

    assert len(frozen) == 100
    assert len(variants) == 4
    assert {trial.alpha.family_id for trial in variants} == {"F17", "F18", "F19", "F20"}
    assert {trial.exit.exit_id for trial in variants} == {"E06"}
    assert {trial.trial_number for trial in variants} == {101, 102, 103, 104}
    assert not {trial.trial_id for trial in frozen}.intersection(
        trial.trial_id for trial in variants
    )
    assert all(
        trial.paper_only and not trial.runtime_active and not trial.live_shadow_enabled
        for trial in variants
    )


def test_cost_covered_e06_manifest_preserves_parent_lineage_and_paper_boundary() -> None:
    manifest = cost_covered_exit_variant_manifest(
        code_version="code+bundle",
        generated_ts_utc="2026-08-29T00:00:00Z",
        source_checksums={"variant.py": "a" * 64, "execution.py": "b" * 64},
        parent_trial_manifest={
            "path": "evidence/STRATEGY_100_TRIAL_MANIFEST.json",
            "manifest_sha256": "c" * 64,
            "file_sha256": "d" * 64,
        },
    )

    assert manifest["manifest_kind"] == "COST_COVERED_EXIT_VARIANT_BATCH"
    assert manifest["batch_id"] == "COST_COVERED_EARLY_TP_RUNNER_V1"
    assert manifest["trial_count"] == 4
    assert manifest["screening_eligible_count"] == 4
    assert manifest["blocked_count"] == 0
    assert manifest["selection_limit"] == 4
    assert manifest["runtime_active_count"] == 0
    assert manifest["live_shadow_count"] == 0
    assert manifest["paper_only"] is True
    assert manifest["real_orders_enabled"] is False
    assert manifest["private_api_enabled"] is False
    assert manifest["parent_trial_manifest"]["manifest_sha256"] == "c" * 64
    assert [row["trial_id"] for row in manifest["trials"]] == [
        trial.trial_id for trial in cost_covered_exit_variant_trials()
    ]
    assert all(
        row["paper_execution_binding"]["trailing_policy"]["policy_id"]
        == "E06_COST_COVERED_EARLY_TP_RUNNER_V1"
        for row in manifest["trials"]
    )


def test_manifest_freezes_cost_split_funnel_and_promotion_gates() -> None:
    source_checksums = {"registry.py": "a" * 64, "trailing.py": "b" * 64}
    first = trial_manifest(
        code_version="code",
        generated_ts_utc="2026-08-28T00:00:00Z",
        source_checksums=source_checksums,
    )
    second = trial_manifest(
        code_version="code",
        generated_ts_utc="2026-08-28T00:00:00Z",
        source_checksums=source_checksums,
    )

    assert first == second
    assert first["status"] == "PREREGISTERED_NOT_EXECUTED"
    assert first["trial_count"] == 100
    assert first["blocked_count"] == 10
    assert first["screening_eligible_count"] == 90
    assert first["runtime_active_count"] == 0
    assert first["source_checksums"] == source_checksums
    assert first["funnel"] == {
        "offline_screening": 100,
        "event_replay_maximum": 25,
        "full_paper_replay_maximum": 10,
        "live_shadow_minimum": 3,
        "live_shadow_maximum": 6,
    }
    assert first["data_split_contract"]["random_shuffle"] is False
    assert first["data_split_contract"]["dataset_manifest"] == (
        "evidence/STRATEGY_100_DATASET_MANIFEST.json"
    )
    assert first["data_split_contract"]["historical_screening_blocked_until_dataset_frozen"] is True
    assert first["data_split_contract"]["forward_live_public_must_remain_prospective"] is True
    assert first["data_split_contract"]["stage1_selection_splits"] == ["TRAIN", "VALIDATION"]
    assert first["data_split_contract"]["stage1_final_oos_access"] == "FORBIDDEN"
    assert first["data_split_contract"]["final_oos_may_be_opened_once"] is True
    assert first["data_split_contract"]["final_oos_may_not_be_used_for_retuning"] is True
    assert first["data_split_contract"]["purge_embargo_ms_by_horizon"] == {
        "MICRO_SCALP": 180_000,
        "FAST_INTRADAY": 3_600_000,
        "INTRADAY_SWING": 21_600_000,
    }
    assert (
        first["data_split_contract"]["horizon_without_four_usable_validation_folds_must_fail"]
        is True
    )
    assert first["risk_contract"]["starting_equity_per_trial_profile_usdt"] == "1000"
    assert first["risk_contract"]["profiles_per_trial"] == ["BASE", "STRESS"]
    assert first["risk_contract"]["F05_initial_stop_atr"] == "2.0"
    assert first["risk_contract"]["real_leverage_setting"] is False
    assert first["promotion_gates"]["deflated_sharpe_ratio_minimum"] == 0.95
    assert first["promotion_gates"]["probability_backtest_overfitting_maximum"] == 0.20
    assert first["real_orders_enabled"] is False
    assert first["private_api_enabled"] is False
    assert first["runtime_ai_enabled"] is False
    assert all(
        row["alpha"]["implementation_status"] == "EXECUTABLE" and row["alpha"]["evaluator_id"]
        for row in first["trials"]
        if row["screening_eligible"]
    )
    assert (
        sum(
            row["paper_execution_binding"]["trailing_policy"] is not None for row in first["trials"]
        )
        == 80
    )
