# 100후보 screening이 모든 trial·BASE/STRESS 1,000 USDT 계좌·실패 gate를 보존하는지 검증한다.

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.research import (
    ScreeningAccountResult,
    ScreeningStatus,
    ScreeningTrade,
    TrialScreeningResult,
    build_screening_report,
    cost_covered_exit_variant_trials,
    point_in_time_volatility_regime,
    preregistered_trials,
)
from backend.app.research.screening import _profile


def _account(trial_id: str, profile: str) -> ScreeningAccountResult:
    return ScreeningAccountResult(
        account_id=f"research:{trial_id}:{profile}",
        trial_id=trial_id,
        profile=profile,
        starting_equity_usdt=Decimal("1000"),
        final_equity_usdt=Decimal("1000"),
        evaluated_event_count=10,
        signal_count=0,
        attempted_entry_count=0,
        rejected_entry_count=0,
        trades=(),
    )


def test_point_in_time_volatility_regime_uses_fixed_ratio_boundaries() -> None:
    assert point_in_time_volatility_regime(fast=0.74, slow=1.0) == "LOW"
    assert point_in_time_volatility_regime(fast=0.75, slow=1.0) == "NORMAL"
    assert point_in_time_volatility_regime(fast=1.5, slow=1.0) == "NORMAL"
    assert point_in_time_volatility_regime(fast=1.51, slow=1.0) == "HIGH"
    assert point_in_time_volatility_regime(fast=0.0, slow=0.0) == "UNKNOWN"
    with pytest.raises(ValueError, match="실현변동성"):
        point_in_time_volatility_regime(fast=-1.0, slow=1.0)


def _all_results() -> tuple[TrialScreeningResult, ...]:
    rows: list[TrialScreeningResult] = []
    for trial in preregistered_trials():
        if not trial.screening_eligible:
            rows.append(
                TrialScreeningResult(
                    trial_id=trial.trial_id,
                    status=ScreeningStatus.BLOCKED,
                    blocker_codes=trial.alpha.blocker_codes,
                    failure_code=None,
                    deterministic_signal_pass=False,
                    no_lookahead_pass=False,
                    recursive_dependency_pass=False,
                    accounts=(),
                )
            )
            continue
        rows.append(
            TrialScreeningResult(
                trial_id=trial.trial_id,
                status=ScreeningStatus.EXECUTED,
                blocker_codes=(),
                failure_code=None,
                deterministic_signal_pass=True,
                no_lookahead_pass=True,
                recursive_dependency_pass=True,
                accounts=(
                    _account(trial.trial_id, "BASE"),
                    _account(trial.trial_id, "STRESS"),
                ),
            )
        )
    return tuple(rows)


def _fold_returns() -> dict[str, tuple[float, ...]]:
    return {
        trial.trial_id: (0.0, 0.0, 0.0, 0.0)
        for trial in preregistered_trials()
        if trial.screening_eligible
    }


def test_screening_report_keeps_all_trials_and_two_independent_accounts() -> None:
    report = build_screening_report(
        _all_results(),
        trial_manifest_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        validation_fold_returns=_fold_returns(),
        generated_ts_utc="2026-08-28T00:00:00Z",
    )

    assert report["registered_trial_count"] == 100
    assert report["screening_eligible_count"] == 90
    assert report["blocked_trial_count"] == 10
    assert report["executed_trial_count"] == 90
    assert report["planned_independent_account_count"] == 200
    assert report["executed_independent_account_count"] == 180
    assert report["blocked_independent_account_count"] == 20
    assert report["starting_equity_per_account_usdt"] == "1000"
    assert report["selection_basis"] == "TRAIN_AND_VALIDATION_ONLY"
    assert report["final_oos_status"] == "SEALED_NOT_USED_FOR_SELECTION"
    assert report["selection_count"] == 0
    assert report["event_replay_selected"] == []
    assert report["global_multiple_testing"]["pbo"] is None
    assert report["global_multiple_testing"]["status"] == "INSUFFICIENT_CROSS_SECTIONAL_VARIATION"
    assert report["profitability_claim"] == "NOT_PROVEN_UNTIL_LATER_GATES"
    assert report["active_count"] == 0
    assert report["live_shadow_count"] == 0
    executed = next(row for row in report["results"] if row["statistics"]["status"] == "EXECUTED")
    assert set(executed["statistics"]["validation_bootstrap_expectancy_95pct"]) == {
        "BASE",
        "STRESS",
    }
    assert set(executed["statistics"]["validation_deflated_sharpe_ratio"]) == {
        "BASE",
        "STRESS",
    }
    assert "BASE_BOOTSTRAP_LOWER_BOUND_NOT_POSITIVE" in executed["statistics"]["gate"]["reasons"]
    assert "STRESS_DSR_BELOW_0_95_OR_MISSING" in executed["statistics"]["gate"]["reasons"]


def test_screening_report_accepts_a_separate_preregistered_variant_batch() -> None:
    trials = cost_covered_exit_variant_trials()
    results = tuple(
        TrialScreeningResult(
            trial_id=trial.trial_id,
            status=ScreeningStatus.FAILED,
            blocker_codes=(),
            failure_code="DATASET_WINDOW_INSUFFICIENT_MICRO_SCALP",
            deterministic_signal_pass=True,
            no_lookahead_pass=True,
            recursive_dependency_pass=True,
            accounts=(),
        )
        for trial in trials
    )

    report = build_screening_report(
        results,
        trial_manifest_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        validation_fold_returns={},
        generated_ts_utc="2026-08-29T00:00:00Z",
        trials=trials,
        selection_limit=4,
    )

    assert report["registered_trial_count"] == 4
    assert report["screening_eligible_count"] == 4
    assert report["blocked_trial_count"] == 0
    assert report["planned_independent_account_count"] == 8
    assert report["blocked_independent_account_count"] == 0
    assert report["selection_limit"] == 4
    assert report["status"] == "INCOMPLETE_TRIAL_FAILURES"
    assert report["profitability_claim"] == "NOT_PROVEN_UNTIL_LATER_GATES"


def test_failed_trial_preserves_both_account_diagnostics_without_counting_as_executed() -> None:
    results = list(_all_results())
    failed_index = next(
        index for index, result in enumerate(results) if result.status is ScreeningStatus.EXECUTED
    )
    original = results[failed_index]
    results[failed_index] = replace(
        original,
        status=ScreeningStatus.FAILED,
        failure_code="DATASET_WINDOW_INSUFFICIENT_FAST_INTRADAY",
    )
    folds = _fold_returns()
    folds.pop(original.trial_id)

    report = build_screening_report(
        results,
        trial_manifest_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        validation_fold_returns=folds,
        generated_ts_utc="2026-08-28T00:00:00Z",
    )

    assert report["executed_trial_count"] == 89
    assert report["failed_trial_count"] == 1
    assert report["executed_independent_account_count"] == 178
    assert report["failed_preserved_independent_account_count"] == 2
    assert report["observed_independent_account_count"] == 180
    failed = next(
        row for row in report["results"] if row["trial"]["trial_id"] == original.trial_id
    )["statistics"]
    assert failed["status"] == "FAILED"
    assert failed["failure_code"] == "DATASET_WINDOW_INSUFFICIENT_FAST_INTRADAY"
    assert set(failed["profiles"]) == {"BASE", "STRESS"}
    assert failed["profiles"]["BASE"]["account"]["evaluated_event_count"] == 10
    assert failed["gate"] == {
        "stage": "VALIDATION_SCREENING",
        "passed": False,
        "reasons": ["FAILED", "DATASET_WINDOW_INSUFFICIENT_FAST_INTRADAY"],
    }
    assert report["global_multiple_testing"]["status"] == "BLOCKED_TRIAL_FAILURES"

    with pytest.raises(ValueError, match="보존 계좌"):
        replace(results[failed_index], accounts=results[failed_index].accounts[:1])


def test_screening_report_rejects_missing_trial_and_hidden_pbo_trial() -> None:
    with pytest.raises(ValueError, match="100개 trial"):
        build_screening_report(
            _all_results()[:-1],
            trial_manifest_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            validation_fold_returns=_fold_returns(),
            generated_ts_utc="2026-08-28T00:00:00Z",
        )

    hidden = _fold_returns()
    hidden.pop(next(iter(hidden)))
    with pytest.raises(ValueError, match="90개 trial"):
        build_screening_report(
            _all_results(),
            trial_manifest_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            validation_fold_returns=hidden,
            generated_ts_utc="2026-08-28T00:00:00Z",
        )


def test_screening_account_reconciles_decimal_costs_exactly() -> None:
    trade = ScreeningTrade(
        trade_id="trade-1",
        trial_id="ALPHA_F03_EXIT_E01_V1",
        profile="BASE",
        split="FINAL_OOS",
        run_id="run-oos",
        symbol="BTCUSDT",
        regime="TREND_UP",
        side="LONG",
        entry_ts_ms=1_000,
        exit_ts_ms=2_000,
        gross_pnl_usdt=Decimal("1"),
        fee_usdt=Decimal("0.1"),
        slippage_usdt=Decimal("0.2"),
        net_pnl_usdt=Decimal("0.7"),
        net_return_bps=7,
        mfe_r=Decimal("1.2"),
        mae_r=Decimal("-0.2"),
        giveback_usdt=Decimal("0.1"),
    )
    account = replace(
        _account(trade.trial_id, "BASE"),
        final_equity_usdt=Decimal("1000.7"),
        signal_count=1,
        attempted_entry_count=1,
        trades=(trade,),
    )

    assert account.final_equity_usdt == Decimal("1000.7")
    with pytest.raises(ValueError, match="자산"):
        replace(account, final_equity_usdt=Decimal("1000.8"))


def test_stage_one_screening_rejects_final_oos_leakage() -> None:
    trial_id = "ALPHA_F03_EXIT_E01_V1"
    trade = ScreeningTrade(
        trade_id="leaked-final-oos-trade",
        trial_id=trial_id,
        profile="BASE",
        split="FINAL_OOS",
        run_id="sealed-run",
        symbol="BTCUSDT",
        regime="TREND_UP",
        side="LONG",
        entry_ts_ms=1_000,
        exit_ts_ms=2_000,
        gross_pnl_usdt=Decimal("0"),
        fee_usdt=Decimal("0"),
        slippage_usdt=Decimal("0"),
        net_pnl_usdt=Decimal("0"),
        net_return_bps=0,
        mfe_r=Decimal("0"),
        mae_r=Decimal("0"),
        giveback_usdt=Decimal("0"),
    )
    results = list(_all_results())
    result_index = next(
        index for index, result in enumerate(results) if result.trial_id == trial_id
    )
    original = results[result_index]
    base = next(account for account in original.accounts if account.profile == "BASE")
    changed_base = replace(base, trades=(trade,))
    results[result_index] = replace(
        original,
        accounts=tuple(
            changed_base if account.profile == "BASE" else account for account in original.accounts
        ),
    )

    with pytest.raises(ValueError, match="Final OOS"):
        build_screening_report(
            results,
            trial_manifest_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            validation_fold_returns=_fold_returns(),
            generated_ts_utc="2026-08-28T00:00:00Z",
        )


def test_trailing_screening_metrics_preserve_runner_and_giveback_evidence() -> None:
    trade = ScreeningTrade(
        trade_id="trail-1",
        trial_id="ALPHA_F03_EXIT_E03_V1",
        profile="BASE",
        split="VALIDATION",
        run_id="run-validation",
        symbol="BTCUSDT",
        regime="TREND_UP",
        side="LONG",
        entry_ts_ms=1_000,
        exit_ts_ms=3_000,
        gross_pnl_usdt=Decimal("1.4"),
        fee_usdt=Decimal("0.1"),
        slippage_usdt=Decimal("0.2"),
        net_pnl_usdt=Decimal("1.1"),
        net_return_bps=11,
        mfe_r=Decimal("2"),
        mae_r=Decimal("-0.1"),
        giveback_usdt=Decimal("0.6"),
        exit_reason="TRAILING_STOP",
        tp1_hit_ts_ms=2_200,
        trailing_activation_ts_ms=2_000,
        runner_started_ts_ms=2_500,
        peak_unrealized_usdt=Decimal("2.2"),
        runner_net_pnl_usdt=Decimal("1.1"),
        trail_trigger_slippage_usdt=Decimal("0.15"),
        trailing_state_checksum="a" * 64,
    )

    profile = _profile((trade,))

    assert profile["trail_activation_count"] == 1
    assert profile["trail_activation_rate"] == 1
    assert profile["tp1_fill_rate"] == 1
    assert profile["runner_count"] == 1
    assert profile["runner_rate"] == 1
    assert profile["runner_net_contribution_usdt"] == "1.1"
    assert profile["mfe_capture_ratio_mean"] == 0.5
    assert profile["average_peak_giveback_usdt"] == "0.6"
    assert profile["median_peak_giveback_usdt"] == "0.6"
    assert profile["p90_peak_giveback_usdt"] == "0.6"
    assert profile["trail_trigger_count"] == 1
    assert profile["stop_before_trail_activation_count"] == 0
    assert profile["trail_trigger_slippage_usdt"] == "0.15"
