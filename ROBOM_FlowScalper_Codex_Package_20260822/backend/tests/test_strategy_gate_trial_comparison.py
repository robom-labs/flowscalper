# 전략별 고정 gate 비교기가 표본 축소와 입력 오염을 성공으로 오인하지 않는지 검증한다.

from __future__ import annotations

from copy import deepcopy

from scripts.compare_all_strategy_gate_trials import compare_all_strategy_gate_trials
from scripts.compare_strategy_gate_trials import compare_strategy_gate_trials
from scripts.research_runtime_strategy_replay import (
    SIGNAL_GATE_TARGET_ALL,
    SIGNAL_GATE_TP1_FEASIBILITY,
)

TARGET = "AGGRESSOR_FLOW_CONTINUATION_V1"
OTHER = "VWAP_EXHAUSTION_REVERSION_V1"


def _profile(*, sample: int, win_rate: float, expectancy: float, drawdown: str) -> dict:
    passed = sample >= 30 and win_rate >= 0.70 and expectancy > 0
    return {
        "sample_size": sample,
        "wins": round(sample * win_rate),
        "losses": sample - round(sample * win_rate),
        "win_rate": win_rate,
        "win_rate_ci95": {"lower": 0.35},
        "payoff_ratio": "2.2",
        "expectancy_bps": expectancy,
        "net_pnl_usdt": "10",
        "profit_factor": "2",
        "return_skew": "0.2",
        "largest_trade_contribution": "0.05",
        "cost_coverage": "5",
        "maximum_drawdown_usdt": drawdown,
        "maximum_drawdown_limit_usdt": "80",
        "expectancy_bootstrap_95": {"lower": 0.5, "upper": 3.0},
        "deflated_sharpe_ratio": {"dsr_probability": 0.99},
        "gates": {
            "sample_at_least_30": passed,
            "win_rate_at_least_70_percent": passed,
            "expectancy_bps_positive": passed,
            "net_pnl_positive": passed,
            "profit_factor_above_one": passed,
            "bootstrap_lower_positive": passed,
            "dsr_at_least_0_95": passed,
            "maximum_drawdown_within_league_8_percent_limit": passed,
        },
        "gate_passed": passed,
    }


def _run(run_id: str, *, candidate: bool) -> dict:
    return {
        "run_id": run_id,
        "runtime_run_id": run_id,
        "strategy_ids": [TARGET, OTHER],
        "strategy_count": 2,
        "strategy_account_count": 4,
        "strategy_version": "test",
        "event_order": ["receive_ts_ms"],
        "event_count": 100,
        "first_receive_ts_ms": 1,
        "last_receive_ts_ms": 100,
        "event_type_counts": {"DEPTH_UPDATE": 100},
        "strategy_evaluation_count": 200,
        "source_strategy_settings": {},
        "real_orders_enabled": False,
        "auth_required": False,
        "ledger_attached": False,
        "strategy_decision_diagnostics": {
            TARGET: {
                "evaluated": 100,
                "baseline_qualified": 10,
                "post_gate_qualified": 6 if candidate else 10,
                "rejection_counts": {},
            },
            OTHER: {
                "evaluated": 100,
                "baseline_qualified": 2,
                "post_gate_qualified": 2,
                "rejection_counts": {},
            },
        },
        "candidate_plan_counts": {TARGET: 6 if candidate else 10, OTHER: 2},
        "trade_rows": [
            {
                "trade_id": (
                    f"random-{run_id}-candidate"
                    if candidate
                    else f"random-{run_id}-baseline"
                ),
                "candidate_id": f"candidate-random-{run_id}",
                "signal_event_id": f"signal-other-{run_id}",
                "strategy_id": OTHER,
                "profile": "BASE",
                "entry_price": "100",
                "quantity": "1",
                "net_pnl_usdt": "1",
            }
        ],
        "signal_gate_diagnostics": {
            "baseline_qualified_count": 10,
            "accepted_qualified_count": 6 if candidate else 10,
            "rejected_qualified_count": 4 if candidate else 0,
            "can_create_signals": False,
        },
    }


def _result(
    *,
    signal_gate: str,
    target_opportunities: int = 30,
    historical_gates_passed: bool = True,
) -> dict:
    candidate = signal_gate != "NONE"
    target_profile = _profile(
        sample=target_opportunities,
        win_rate=0.80 if candidate else 0.60,
        expectancy=2.0 if candidate else 1.0,
        drawdown="2" if candidate else "4",
    )
    run_ids = [f"RUN-{index:02d}" for index in range(13)]
    return {
        "status": "RESEARCH_STRATEGY_LEAGUE_REPLAY_COMPLETE",
        "method": "ONE_PASS_ALL_REGISTERED_ACTUAL_PAPER_RUNTIME_PATH",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "runtime_ai_order_decision": False,
        "git_commit": "same-test-commit",
        "research_scope": "ALL_REGISTERED_STRATEGIES",
        "strategy_ids": [TARGET, OTHER],
        "strategy_count": 2,
        "strategy_account_count": 4,
        "strategy_version": "test",
        "signal_gate": signal_gate,
        "signal_gate_target_strategy_id": TARGET,
        "signal_gate_trial_id": f"{signal_gate}:{TARGET}",
        "strategy_logic": "CURRENT_FULL_CONFLUENCE",
        "frozen_dataset": {
            "file_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
            "selected_run_count": 13,
            "selected_event_count": 1_300,
            "selected_runs": [
                {"run_id": run_id, "checksum": "c" * 64}
                for run_id in run_ids
            ],
            "current_archive_byte_reverification": {
                "status": "PASS",
                "run_count": 13,
            },
        },
        "runs": [_run(run_id, candidate=candidate) for run_id in run_ids],
        "robustness_evaluation": {
            "status": "HISTORICAL_METRICS_CALCULATED_FORWARD_PENDING",
            "final_oos": {
                "opened_for_this_result": True,
                "no_retuning_after_open": True,
                "missing_run_ids": [],
            },
            "strategies": {
                TARGET: {
                    "final_oos_unique_market_opportunity_count": target_opportunities,
                    "final_oos_censored_count": 0,
                    "historical_cost_oos_statistical_and_concentration_gates_passed": (
                        historical_gates_passed
                    ),
                    "pbo_gate_by_profile": {
                        "BASE": historical_gates_passed,
                        "STRESS": historical_gates_passed,
                    },
                    "concentration": {"gate_passed": True},
                    "profiles": {
                        "BASE": deepcopy(target_profile),
                        "STRESS": deepcopy(target_profile),
                    },
                    "ranking_blockers": ["INDEPENDENT_FORWARD_LIVE_PUBLIC_NOT_EVALUATED"],
                }
            }
        },
    }


def _all_strategy_candidate() -> tuple[dict, dict]:
    baseline = _result(signal_gate="NONE")
    candidate = _result(signal_gate=SIGNAL_GATE_TP1_FEASIBILITY)
    baseline_robustness = baseline["robustness_evaluation"]["strategies"]
    candidate_robustness = candidate["robustness_evaluation"]["strategies"]
    baseline_robustness[OTHER] = deepcopy(baseline_robustness[TARGET])
    candidate_robustness[OTHER] = deepcopy(candidate_robustness[TARGET])
    candidate["signal_gate_target_strategy_id"] = SIGNAL_GATE_TARGET_ALL
    candidate["signal_gate_trial_id"] = (
        f"{SIGNAL_GATE_TP1_FEASIBILITY}:{SIGNAL_GATE_TARGET_ALL}"
    )
    for run in baseline["runs"]:
        run["signal_gate_target_strategy_id"] = TARGET
        run["signal_gate_trial_id"] = f"NONE:{TARGET}"
    for run in candidate["runs"]:
        run["signal_gate_target_strategy_id"] = SIGNAL_GATE_TARGET_ALL
        run["signal_gate_trial_id"] = (
            f"{SIGNAL_GATE_TP1_FEASIBILITY}:{SIGNAL_GATE_TARGET_ALL}"
        )
        rows = run["strategy_decision_diagnostics"]
        rows[TARGET].update(
            {
                "gate_targeted": True,
                "gate_baseline_qualified": 10,
                "gate_accepted_qualified": 6,
                "gate_rejected_qualified": 4,
                "gate_rejection_counts": {"TP1_GATE": 4},
            }
        )
        rows[OTHER].update(
            {
                "gate_targeted": True,
                "gate_baseline_qualified": 2,
                "gate_accepted_qualified": 2,
                "gate_rejected_qualified": 0,
                "gate_rejection_counts": {},
            }
        )
        run["signal_gate_diagnostics"] = {
            "baseline_qualified_count": 12,
            "accepted_qualified_count": 8,
            "rejected_qualified_count": 4,
            "rejection_counts": {"TP1_GATE": 4},
            "can_create_signals": False,
        }
    return baseline, candidate


def test_comparison_allows_only_a_historical_forward_shadow_candidate() -> None:
    result = compare_strategy_gate_trials(
        _result(signal_gate="NONE"),
        _result(signal_gate=SIGNAL_GATE_TP1_FEASIBILITY),
    )

    assert result["status"] == "PASS_COMPARISON_COMPLETE"
    assert result["decision"] == "HISTORICAL_CANDIDATE_FORWARD_SHADOW_PENDING"
    assert result["historical_candidate_for_forward_shadow"] is True
    assert result["promotion_allowed"] is False
    assert result["profitability_status"] == "NOT_PROVEN"


def test_comparison_reuses_one_verified_none_gate_baseline_for_another_target() -> None:
    baseline = _result(signal_gate="NONE")
    baseline["signal_gate_target_strategy_id"] = OTHER
    baseline["signal_gate_trial_id"] = f"NONE:{OTHER}"
    for run in baseline["runs"]:
        run["signal_gate_target_strategy_id"] = OTHER
        run["signal_gate_trial_id"] = f"NONE:{OTHER}"
        run["signal_gate_diagnostics"].update(
            {
                "baseline_qualified_count": 2,
                "accepted_qualified_count": 2,
                "rejected_qualified_count": 0,
            }
        )

    result = compare_strategy_gate_trials(
        baseline,
        _result(signal_gate=SIGNAL_GATE_TP1_FEASIBILITY),
    )

    assert result["status"] == "PASS_COMPARISON_COMPLETE"
    assert result["decision"] == "HISTORICAL_CANDIDATE_FORWARD_SHADOW_PENDING"
    assert result["baseline_shared_target_strategy_id"] == OTHER
    assert result["baseline_is_reusable_all_strategy_none_gate_reference"] is True


def test_comparison_keeps_fewer_than_30_oos_opportunities_not_proven() -> None:
    result = compare_strategy_gate_trials(
        _result(signal_gate="NONE"),
        _result(
            signal_gate=SIGNAL_GATE_TP1_FEASIBILITY,
            target_opportunities=29,
            historical_gates_passed=False,
        ),
    )

    assert result["decision"] == "NOT_PROVEN_INSUFFICIENT_OOS_SAMPLE"
    assert result["historical_candidate_for_forward_shadow"] is False


def test_comparison_rejects_failed_historical_gates_after_enough_samples() -> None:
    result = compare_strategy_gate_trials(
        _result(signal_gate="NONE"),
        _result(
            signal_gate=SIGNAL_GATE_TP1_FEASIBILITY,
            historical_gates_passed=False,
        ),
    )

    assert result["decision"] == "REJECTED_HISTORICAL_GATES"
    assert result["promotion_allowed"] is False


def test_comparison_does_not_reject_positive_low_win_candidate_by_legacy_70_gate() -> None:
    candidate = _result(signal_gate=SIGNAL_GATE_TP1_FEASIBILITY)
    for profile in ("BASE", "STRESS"):
        profile_result = candidate["robustness_evaluation"]["strategies"][TARGET][
            "profiles"
        ][profile]
        profile_result["win_rate"] = 0.55
        profile_result["wins"] = 16
        profile_result["losses"] = 14
        profile_result["win_rate_ci95"] = {"lower": 0.38}
        profile_result["gates"]["win_rate_at_least_70_percent"] = False
        profile_result["gate_passed"] = False

    result = compare_strategy_gate_trials(_result(signal_gate="NONE"), candidate)

    assert result["status"] == "PASS_COMPARISON_COMPLETE"
    assert result["decision"] == "HISTORICAL_CANDIDATE_FORWARD_SHADOW_PENDING"
    assert result["candidate_absolute_profile_gates"]["BASE"][
        "wilson_lower_positive"
    ] is True
    assert result["candidate_absolute_profile_gates"]["STRESS"][
        "expectancy_bps_positive"
    ] is True
    assert result["candidate_absolute_profile_gates"]["STRESS"][
        "profit_factor_above_one"
    ] is True
    assert not any("BELOW_70" in reason for reason in result["candidate_ranking_blockers"])


def test_comparison_invalidates_non_target_strategy_changes() -> None:
    baseline = _result(signal_gate="NONE")
    candidate = _result(signal_gate=SIGNAL_GATE_TP1_FEASIBILITY)
    candidate["runs"][0]["candidate_plan_counts"][OTHER] = 3

    result = compare_strategy_gate_trials(baseline, candidate)

    assert result["status"] == "FAIL_INTEGRITY"
    assert result["decision"] == "INVALID_DO_NOT_USE"
    assert any(
        str(code).startswith("NON_TARGET_CANDIDATE_PLAN_CHANGED")
        for code in result["integrity_violations"]
    )


def test_comparison_invalidates_target_signal_or_plan_growth() -> None:
    baseline = _result(signal_gate="NONE")
    candidate = _result(signal_gate=SIGNAL_GATE_TP1_FEASIBILITY)
    candidate["runs"][0]["strategy_decision_diagnostics"][TARGET][
        "post_gate_qualified"
    ] = 11
    candidate["runs"][0]["candidate_plan_counts"][TARGET] = 11

    result = compare_strategy_gate_trials(baseline, candidate)

    assert result["status"] == "FAIL_INTEGRITY"
    assert any(
        str(code).startswith("TARGET_POST_GATE_SIGNAL_INCREASED")
        for code in result["integrity_violations"]
    )
    assert any(
        str(code).startswith("TARGET_CANDIDATE_PLAN_INCREASED")
        for code in result["integrity_violations"]
    )


def test_all_strategy_comparison_reuses_one_batch_without_promoting() -> None:
    baseline, candidate = _all_strategy_candidate()

    result = compare_all_strategy_gate_trials(baseline, candidate)

    assert result["status"] == "PASS_COMPARISON_COMPLETE"
    assert result["same_frozen_input_and_strategy_accounting_passed"] is True
    assert set(result["strategy_comparisons"]) == {TARGET, OTHER}
    assert all(
        row["status"] == "PASS_COMPARISON_COMPLETE"
        for row in result["strategy_comparisons"].values()
    )
    assert result["promotion_allowed"] is False
    assert result["ranking_eligible_strategy_ids"] == []
    assert result["universal_win_rate_gate_required"] is False
    assert set(result["family_promotion_pending_strategy_ids"]) == {TARGET, OTHER}
    assert result["profitability_status"] == "NOT_PROVEN"
    assert result["real_orders_enabled"] is False
    assert result["auth_required"] is False


def test_all_strategy_comparison_rejects_missing_target_or_bad_accounting() -> None:
    baseline, candidate = _all_strategy_candidate()
    first = candidate["runs"][0]["strategy_decision_diagnostics"][OTHER]
    first["gate_targeted"] = False
    first["gate_rejected_qualified"] = 1

    result = compare_all_strategy_gate_trials(baseline, candidate)

    assert result["status"] == "FAIL_INTEGRITY"
    assert result["decision"] == "INVALID_DO_NOT_USE"
    assert any(
        str(code).startswith("STRATEGY_NOT_GATE_TARGETED")
        for code in result["shared_integrity_violations"]
    )
    assert any(
        str(code).startswith("STRATEGY_GATE_ACCOUNTING_MISMATCH")
        for code in result["shared_integrity_violations"]
    )


def test_all_strategy_comparison_rejects_candidate_plan_growth() -> None:
    baseline, candidate = _all_strategy_candidate()
    candidate["runs"][0]["candidate_plan_counts"][OTHER] = 3

    result = compare_all_strategy_gate_trials(baseline, candidate)

    assert result["status"] == "FAIL_INTEGRITY"
    assert any(
        str(code).startswith("CANDIDATE_PLAN_INCREASED")
        for code in result["shared_integrity_violations"]
    )


def test_comparison_invalidates_different_code_or_incomplete_archive_scope() -> None:
    baseline = _result(signal_gate="NONE")
    candidate = _result(signal_gate=SIGNAL_GATE_TP1_FEASIBILITY)
    candidate["git_commit"] = "different-commit"
    candidate["frozen_dataset"]["current_archive_byte_reverification"] = {
        "status": "PASS",
        "run_count": 12,
    }

    result = compare_strategy_gate_trials(baseline, candidate)

    assert result["status"] == "FAIL_INTEGRITY"
    assert "GIT_COMMIT_MISMATCH" in result["integrity_violations"]
    assert "FULL_13_RUN_CURRENT_ARCHIVE_VERIFICATION_NOT_PASS" in result[
        "integrity_violations"
    ]
