# 100후보의 paired exit, walk-forward, 다중검정 증거가 선택 없이 생성되는지 검증한다.

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.research import (
    ScreeningAccountResult,
    ScreeningStatus,
    ScreeningTrade,
    TrialScreeningResult,
    build_multiple_testing_report,
    build_screening_report,
    build_trailing_ablation_report,
    build_walk_forward_report,
    preregistered_trials,
)

GENERATED_TS = "2026-08-28T00:00:00Z"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _trade(trial_id: str, profile: str, exit_number: int) -> ScreeningTrade:
    net = Decimal("0.5") + Decimal(exit_number) / Decimal(10)
    return ScreeningTrade(
        trade_id=f"{trial_id}:{profile}:trade",
        trial_id=trial_id,
        profile=profile,
        split="VALIDATION",
        run_id="validation-run",
        symbol="BTCUSDT",
        regime="TREND_UP",
        side="LONG",
        entry_ts_ms=10_000,
        exit_ts_ms=11_000 + exit_number,
        gross_pnl_usdt=net + Decimal("0.2"),
        fee_usdt=Decimal("0.1"),
        slippage_usdt=Decimal("0.1"),
        net_pnl_usdt=net,
        net_return_bps=float(net * Decimal(10)),
        mfe_r=Decimal("1.5"),
        mae_r=Decimal("-0.2"),
        giveback_usdt=Decimal("0.1"),
        signal_event_id="shared-f03-signal",
        exit_reason="TRAILING_STOP" if exit_number > 1 else "TAKE_PROFIT",
        tp1_hit_ts_ms=10_400 if exit_number > 1 else None,
        trailing_activation_ts_ms=10_500 if exit_number > 1 else None,
        runner_started_ts_ms=10_600 if exit_number > 1 else None,
        peak_unrealized_usdt=Decimal("1.2"),
        runner_net_pnl_usdt=net if exit_number > 1 else Decimal(0),
        trail_trigger_slippage_usdt=Decimal("0.05") if exit_number > 1 else Decimal(0),
        trailing_state_checksum=(str(exit_number) * 64) if exit_number > 1 else None,
        venue="BINANCE_USDM",
        volatility_regime="NORMAL",
    )


def _account(
    trial_id: str,
    profile: str,
    trade: ScreeningTrade | tuple[ScreeningTrade, ...] | None,
) -> ScreeningAccountResult:
    if trade is None:
        trades: tuple[ScreeningTrade, ...] = ()
    elif isinstance(trade, ScreeningTrade):
        trades = (trade,)
    else:
        trades = trade
    return ScreeningAccountResult(
        account_id=f"{trial_id}:{profile}",
        trial_id=trial_id,
        profile=profile,
        starting_equity_usdt=Decimal("1000"),
        final_equity_usdt=Decimal("1000")
        + sum((row.net_pnl_usdt for row in trades), start=Decimal(0)),
        evaluated_event_count=10,
        signal_count=len(trades),
        attempted_entry_count=len(trades),
        rejected_entry_count=0,
        trades=trades,
    )


def _screening_fixture() -> tuple[dict[str, object], tuple[ScreeningTrade, ...]]:
    results: list[TrialScreeningResult] = []
    trades: list[ScreeningTrade] = []
    fold_returns: dict[str, tuple[float, ...]] = {}
    for trial in preregistered_trials():
        if not trial.screening_eligible:
            results.append(
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
        exit_number = int(trial.exit.exit_id[1:])
        has_paired_trade = trial.alpha.family_id == "F03"
        base_trade = _trade(trial.trial_id, "BASE", exit_number) if has_paired_trade else None
        stress_trade = (
            replace(base_trade, trade_id=f"{trial.trial_id}:STRESS:trade", profile="STRESS")
            if base_trade is not None
            else None
        )
        base_trades = (base_trade,) if base_trade is not None else ()
        stress_trades = (stress_trade,) if stress_trade is not None else ()
        if base_trade is not None and stress_trade is not None and exit_number == 1:
            second_base = replace(
                base_trade,
                trade_id=f"{trial.trial_id}:BASE:trade-b",
                entry_ts_ms=30_000,
                exit_ts_ms=31_001,
                symbol="ETHUSDT",
                regime="TREND_DOWN",
                signal_event_id="second-f03-e01-signal",
                venue="SECOND_TEST_VENUE",
                volatility_regime="HIGH",
            )
            base_trades += (second_base,)
            stress_trades += (
                replace(
                    second_base,
                    trade_id=f"{trial.trial_id}:STRESS:trade-b",
                    profile="STRESS",
                ),
            )
        trades.extend((*base_trades, *stress_trades))
        results.append(
            TrialScreeningResult(
                trial_id=trial.trial_id,
                status=ScreeningStatus.EXECUTED,
                blocker_codes=(),
                failure_code=None,
                deterministic_signal_pass=True,
                no_lookahead_pass=True,
                recursive_dependency_pass=True,
                accounts=(
                    _account(trial.trial_id, "BASE", base_trades),
                    _account(trial.trial_id, "STRESS", stress_trades),
                ),
            )
        )
        fold_returns[trial.trial_id] = (
            (
                stress_trades[0].net_return_bps,
                stress_trades[1].net_return_bps if len(stress_trades) > 1 else 0.0,
                0.0,
                0.0,
            )
            if stress_trades
            else (0.0, 0.0, 0.0, 0.0)
        )
    report = build_screening_report(
        results,
        trial_manifest_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        validation_fold_returns=fold_returns,
        generated_ts_utc=GENERATED_TS,
    )
    report["manifest_sha256"] = hashlib.sha256(_canonical_json(report).encode()).hexdigest()
    return report, tuple(trades)


def test_secondary_reports_preserve_paired_cohorts_and_final_oos_seal() -> None:
    screening, trades = _screening_fixture()
    screening["event_replay_selected"] = ["ALPHA_F03_EXIT_E01_V1"]
    screening["selection_count"] = 1
    screening.pop("manifest_sha256")
    screening["manifest_sha256"] = hashlib.sha256(_canonical_json(screening).encode()).hexdigest()
    executable_ids = {
        trial.trial_id for trial in preregistered_trials() if trial.screening_eligible
    }
    fold_returns = {trial_id: (0.0, 0.0, 0.0, 0.0) for trial_id in executable_ids}
    fold_counts = {trial_id: (0, 0, 0, 0) for trial_id in executable_ids}
    for trial_id in executable_ids:
        if "_F03_" in trial_id:
            exit_number = int(trial_id.split("_EXIT_E", 1)[1].split("_", 1)[0])
            expectancy_bps = float(
                (Decimal("0.5") + Decimal(exit_number) / Decimal(10)) * Decimal(10)
            )
            fold_returns[trial_id] = (expectancy_bps, 0.0, 0.0, 0.0)
            fold_counts[trial_id] = (1, 0, 0, 0)
    fold_counts["ALPHA_F03_EXIT_E01_V1"] = (1, 1, 0, 0)
    fold_returns["ALPHA_F03_EXIT_E01_V1"] = (6.0, 6.0, 0.0, 0.0)

    trailing = build_trailing_ablation_report(
        screening,
        trades,
        generated_ts_utc=GENERATED_TS,
    )
    walk_forward = build_walk_forward_report(
        screening,
        trades=trades,
        folds_by_horizon={
            horizon: (
                {"fold_id": "A", "start_ts_ms": 0, "end_ts_ms": 20_000},
                {"fold_id": "B", "start_ts_ms": 20_000, "end_ts_ms": 40_000},
                {"fold_id": "C", "start_ts_ms": 40_000, "end_ts_ms": 60_000},
                {"fold_id": "D", "start_ts_ms": 60_000, "end_ts_ms": 80_000},
            )
            for horizon in ("MICRO_SCALP", "FAST_INTRADAY", "INTRADAY_SWING")
        },
        fold_returns=fold_returns,
        fold_trade_counts=fold_counts,
        fold_crossing_excluded_count=0,
        generated_ts_utc=GENERATED_TS,
    )
    multiple = build_multiple_testing_report(screening, generated_ts_utc=GENERATED_TS)

    assert trailing["paired_complete_cohort_count"] == 2
    f03 = next(row for row in trailing["families"] if row["family_id"] == "F03")
    assert f03["paired_same_signal_exit_ablation"]["BASE"]["status"] == "EXECUTED"
    assert f03["paired_same_signal_exit_ablation"]["STRESS"]["complete_five_exit_cohort_count"] == 1
    assert walk_forward["required_fold_count_per_executed_horizon"] == 4
    assert walk_forward["fold_count_by_horizon"] == {
        "MICRO_SCALP": 4,
        "FAST_INTRADAY": 4,
        "INTRADAY_SWING": 4,
    }
    assert walk_forward["executed_trial_count"] == 90
    assert walk_forward["trials_with_validation_trades"] == 5
    assert walk_forward["trials_with_base_validation_trades"] == 5
    assert walk_forward["trials_with_stress_validation_trades"] == 5
    f03_trial = next(
        row for row in walk_forward["trials"] if row["trial_id"] == "ALPHA_F03_EXIT_E01_V1"
    )
    assert f03_trial["profiles"]["BASE"]["folds_with_trades"] == 2
    assert f03_trial["profiles"]["STRESS"]["folds_with_trades"] == 2
    assert f03_trial["profiles"]["BASE"]["anchored_walk_forward"]["window_count"] == 3
    assert f03_trial["profiles"]["BASE"]["rolling_walk_forward"]["window_count"] == 3
    assert f03_trial["profiles"]["BASE"]["anchored_walk_forward"]["status"] == (
        "EXECUTED_FIXED_PARAMETERS"
    )
    assert f03_trial["profiles"]["BASE"]["rolling_walk_forward"]["status"] == (
        "EXECUTED_FIXED_PARAMETERS"
    )
    assert walk_forward["walk_forward_methods"]["ANCHORED"]["training_policy"] == (
        "ALL_PRIOR_FOLDS"
    )
    assert walk_forward["walk_forward_methods"]["ROLLING"]["training_policy"] == (
        "IMMEDIATELY_PRECEDING_FOLD"
    )
    assert walk_forward["holdout_diagnostics"]["venue"]["status"] == (
        "EXECUTED_FIXED_PARAMETERS_DIAGNOSTIC_ONLY"
    )
    assert (
        walk_forward["holdout_diagnostics"]["volatility"]["status"]
        == "EXECUTED_FIXED_PARAMETERS_DIAGNOSTIC_ONLY"
    )
    assert (
        walk_forward["holdout_diagnostics"]["cost_profile"]["status"]
        == "EXECUTED_FIXED_PARAMETERS_DIAGNOSTIC_ONLY"
    )
    assert f03_trial["holdout_diagnostics"]["symbol"]["status"] == (
        "EXECUTED_FIXED_PARAMETERS_DIAGNOSTIC_ONLY"
    )
    single_group_trial = next(
        row for row in walk_forward["trials"] if row["trial_id"] == "ALPHA_F03_EXIT_E02_V1"
    )
    assert single_group_trial["holdout_diagnostics"]["venue"]["status"] == (
        "INSUFFICIENT_GROUP_VARIATION"
    )
    assert walk_forward["event_replay_primary_gate_candidates"] == ["ALPHA_F03_EXIT_E01_V1"]
    assert walk_forward["event_replay_selected_after_secondary_gates"] == []
    assert walk_forward["event_replay_selection_status"] == ("NO_SECONDARY_ELIGIBLE_CANDIDATES")
    assert walk_forward["event_replay_secondary_gate_results"] == [
        {
            "trial_id": "ALPHA_F03_EXIT_E01_V1",
            "passed": False,
            "reasons": ["HOLDOUT_BULL_BEAR_RANGE_INSUFFICIENT"],
        }
    ]
    assert walk_forward["final_oos_used"] is False
    assert multiple["registered_trial_count"] == 100
    assert multiple["active_count"] == 0
    assert multiple["final_oos_status"] == "SEALED_NOT_USED_FOR_SELECTION"
    assert trailing["screening_manifest_sha256"] == screening["manifest_sha256"]
    assert walk_forward["screening_manifest_sha256"] == screening["manifest_sha256"]
    assert multiple["screening_manifest_sha256"] == screening["manifest_sha256"]


def test_secondary_report_rejects_tampered_screening_checksum() -> None:
    screening, trades = _screening_fixture()
    tampered = dict(screening)
    tampered["active_count"] = 1

    with pytest.raises(ValueError, match="checksum"):
        build_trailing_ablation_report(tampered, trades, generated_ts_utc=GENERATED_TS)


def test_walk_forward_rejects_trade_projection_tampering_and_final_oos() -> None:
    screening, trades = _screening_fixture()
    injected = replace(trades[0], trade_id="injected-validation-trade")

    with pytest.raises(ValueError, match="screening Validation"):
        build_walk_forward_report(
            screening,
            trades=(*trades, injected),
            folds_by_horizon={},
            fold_returns={},
            fold_trade_counts={},
            fold_crossing_excluded_count=0,
            generated_ts_utc=GENERATED_TS,
        )

    with pytest.raises(ValueError, match="Final OOS"):
        build_walk_forward_report(
            screening,
            trades=(replace(trades[0], split="FINAL_OOS"), *trades[1:]),
            folds_by_horizon={},
            fold_returns={},
            fold_trade_counts={},
            fold_crossing_excluded_count=0,
            generated_ts_utc=GENERATED_TS,
        )
