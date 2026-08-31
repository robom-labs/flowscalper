# 전략별 baseline과 고정 gate 후보를 동일 입력·강건성 계약으로 비교한다.
"""표본 축소나 다른 전략 결과를 고승률 후보로 오인하지 않게 fail-closed 판정한다."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from backend.app.analytics.opportunities import wilson_lower_bound
from scripts.research_runtime_strategy_replay import family_promotion_assessment

BASELINE_SIGNAL_GATE = "NONE"
MINIMUM_OPPORTUNITIES = 30
MINIMUM_DSR_PROBABILITY = 0.95
_RANDOM_ID_FIELDS = {"candidate_id", "trade_id", "trailing_state_checksum"}


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}은 JSON 객체여야 합니다.")
    return value


def _rows(value: object, label: str) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError(f"{label}은 JSON 배열이어야 합니다.")
    rows: list[Mapping[str, object]] = []
    for row in value:
        rows.append(_mapping(row, label))
    return rows


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _run_map(payload: Mapping[str, object], label: str) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for row in _rows(payload.get("runs"), f"{label}.runs"):
        run_id = str(row.get("run_id", ""))
        if not run_id or run_id in result:
            raise ValueError(f"{label}의 Run ID가 없거나 중복됐습니다.")
        result[run_id] = row
    return result


def _normalized_non_target_trades(
    run: Mapping[str, object],
    *,
    target_strategy_id: str,
) -> list[str]:
    normalized: list[str] = []
    for trade in _rows(run.get("trade_rows", []), "run.trade_rows"):
        if str(trade.get("strategy_id")) == target_strategy_id:
            continue
        normalized.append(
            _canonical(
                {
                    key: value
                    for key, value in trade.items()
                    if key not in _RANDOM_ID_FIELDS
                }
            )
        )
    return sorted(normalized)


def _profile_metrics(
    payload: Mapping[str, object],
    *,
    target_strategy_id: str,
    profile: str,
) -> Mapping[str, object]:
    robustness = _mapping(payload.get("robustness_evaluation"), "robustness_evaluation")
    strategies = _mapping(robustness.get("strategies"), "robustness_evaluation.strategies")
    strategy = _mapping(strategies.get(target_strategy_id), target_strategy_id)
    profiles = _mapping(strategy.get("profiles"), f"{target_strategy_id}.profiles")
    return _mapping(profiles.get(profile), f"{target_strategy_id}.{profile}")


def _strategy_robustness(
    payload: Mapping[str, object],
    *,
    target_strategy_id: str,
) -> Mapping[str, object]:
    robustness = _mapping(payload.get("robustness_evaluation"), "robustness_evaluation")
    strategies = _mapping(robustness.get("strategies"), "robustness_evaluation.strategies")
    return _mapping(strategies.get(target_strategy_id), target_strategy_id)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(str(value))


def _not_lower(candidate: object, baseline: object) -> bool:
    candidate_value = _optional_float(candidate)
    baseline_value = _optional_float(baseline)
    if candidate_value is None:
        return False
    return baseline_value is None or candidate_value >= baseline_value


def _not_higher_decimal(candidate: object, baseline: object) -> bool:
    candidate_value = Decimal(str(candidate))
    baseline_value = Decimal(str(baseline))
    return (
        candidate_value.is_finite()
        and baseline_value.is_finite()
        and candidate_value <= baseline_value
    )


def _candidate_profile_absolute_gates(
    profile: Mapping[str, object],
) -> dict[str, bool]:
    gates = _mapping(profile.get("gates"), "candidate profile gates")
    bootstrap = _mapping(
        profile.get("expectancy_bootstrap_95"),
        "candidate expectancy_bootstrap_95",
    )
    dsr = _mapping(
        profile.get("deflated_sharpe_ratio"),
        "candidate deflated_sharpe_ratio",
    )
    losses = int(str(profile.get("losses", 0)))
    wins = int(str(profile.get("wins", 0)))
    sample_size = int(str(profile.get("sample_size", 0)))
    profit_factor = _optional_float(profile.get("profit_factor"))
    interval = profile.get("win_rate_ci95")
    reported_wilson_lower = (
        interval.get("lower") if isinstance(interval, Mapping) else None
    )
    wilson_lower = (
        Decimal(str(reported_wilson_lower))
        if reported_wilson_lower is not None
        else wilson_lower_bound(wins, sample_size)
    )
    current_embedded_gates = {
        str(name): value
        for name, value in gates.items()
        if not (
            str(name).startswith("win_rate_at_least_")
            and str(name).endswith("_percent")
        )
    }
    return {
        "sample_at_least_30": sample_size >= MINIMUM_OPPORTUNITIES,
        "wilson_lower_positive": (
            wilson_lower is not None and wilson_lower.is_finite() and wilson_lower > 0
        ),
        "expectancy_bps_positive": (
            (expectancy := _optional_float(profile.get("expectancy_bps"))) is not None
            and expectancy > 0
        ),
        "net_pnl_positive": Decimal(str(profile.get("net_pnl_usdt", "-Infinity")))
        > 0,
        "profit_factor_above_one": (
            (losses == 0 and wins > 0)
            or (profit_factor is not None and profit_factor > 1)
        ),
        "bootstrap_lower_positive": (
            (bootstrap_lower := _optional_float(bootstrap.get("lower"))) is not None
            and bootstrap_lower > 0
        ),
        "dsr_at_least_0_95": (
            (dsr_probability := _optional_float(dsr.get("dsr_probability")))
            is not None
            and dsr_probability >= MINIMUM_DSR_PROBABILITY
        ),
        "maximum_drawdown_within_limit": _not_higher_decimal(
            profile.get("maximum_drawdown_usdt", "Infinity"),
            profile.get("maximum_drawdown_limit_usdt", "-Infinity"),
        ),
        "embedded_nonlegacy_profile_gates_all_passed": bool(current_embedded_gates)
        and all(value is True for value in current_embedded_gates.values()),
    }


def _legacy_universal_win_rate_marker(value: object) -> bool:
    normalized = str(value).upper()
    return "WIN_RATE" in normalized and "70" in normalized


def compare_strategy_gate_trials(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> dict[str, object]:
    """동일 target의 baseline과 후보를 무결성·성과·향후검증 단계로 분리한다."""

    target_strategy_id = str(candidate.get("signal_gate_target_strategy_id", ""))
    baseline_target_strategy_id = str(
        baseline.get("signal_gate_target_strategy_id", "")
    )
    candidate_gate = str(candidate.get("signal_gate", ""))
    baseline_gate = str(baseline.get("signal_gate", ""))
    strategy_logic = str(candidate.get("strategy_logic", ""))
    integrity_violations: list[str] = []

    required_top_level = {
        "status": "RESEARCH_STRATEGY_LEAGUE_REPLAY_COMPLETE",
        "method": "ONE_PASS_ALL_REGISTERED_ACTUAL_PAPER_RUNTIME_PATH",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "runtime_ai_order_decision": False,
    }
    for label, payload in (("BASELINE", baseline), ("CANDIDATE", candidate)):
        for key, expected in required_top_level.items():
            if payload.get(key) != expected:
                integrity_violations.append(f"{label}_{key.upper()}_MISMATCH")
    if not target_strategy_id:
        integrity_violations.append("TARGET_STRATEGY_MISSING")
    if baseline_gate != BASELINE_SIGNAL_GATE:
        integrity_violations.append("BASELINE_SIGNAL_GATE_NOT_NONE")
    if not candidate_gate or candidate_gate == BASELINE_SIGNAL_GATE:
        integrity_violations.append("CANDIDATE_SIGNAL_GATE_NOT_ACTIVE")
    if baseline.get("strategy_logic") != strategy_logic:
        integrity_violations.append("STRATEGY_LOGIC_MISMATCH")
    shared_top_level_fields = (
        "git_commit",
        "research_scope",
        "strategy_ids",
        "strategy_count",
        "strategy_account_count",
        "strategy_version",
    )
    for field_name in shared_top_level_fields:
        if baseline.get(field_name) != candidate.get(field_name):
            integrity_violations.append(f"{field_name.upper()}_MISMATCH")
    strategy_ids = [str(value) for value in _rows_as_values(candidate.get("strategy_ids"))]
    if target_strategy_id not in strategy_ids:
        integrity_violations.append("TARGET_STRATEGY_NOT_REGISTERED_IN_RESULT")
    if baseline_target_strategy_id not in strategy_ids:
        integrity_violations.append("BASELINE_TARGET_STRATEGY_NOT_REGISTERED_IN_RESULT")
    for label, payload in (("BASELINE", baseline), ("CANDIDATE", candidate)):
        if int(str(payload.get("strategy_count", -1))) != len(strategy_ids):
            integrity_violations.append(f"{label}_STRATEGY_COUNT_MISMATCH")
        if int(str(payload.get("strategy_account_count", -1))) != len(strategy_ids) * 2:
            integrity_violations.append(f"{label}_STRATEGY_ACCOUNT_COUNT_MISMATCH")
    if candidate.get("signal_gate_trial_id") != f"{candidate_gate}:{target_strategy_id}":
        integrity_violations.append("CANDIDATE_TRIAL_ID_MISMATCH")
    if baseline.get("signal_gate_trial_id") != (
        f"{baseline_gate}:{baseline_target_strategy_id}"
    ):
        integrity_violations.append("BASELINE_TRIAL_ID_MISMATCH")

    baseline_frozen = _mapping(baseline.get("frozen_dataset"), "baseline.frozen_dataset")
    candidate_frozen = _mapping(candidate.get("frozen_dataset"), "candidate.frozen_dataset")
    frozen_fields = (
        "file_sha256",
        "manifest_sha256",
        "selected_run_count",
        "selected_event_count",
        "selected_runs",
        "current_archive_byte_reverification",
    )
    if any(baseline_frozen.get(key) != candidate_frozen.get(key) for key in frozen_fields):
        integrity_violations.append("FROZEN_DATASET_OR_BYTE_VERIFICATION_MISMATCH")
    byte_verification = candidate_frozen.get("current_archive_byte_reverification")
    if (
        candidate_frozen.get("selected_run_count") != 13
        or not isinstance(byte_verification, Mapping)
        or byte_verification.get("status") != "PASS"
        or byte_verification.get("run_count") != 13
    ):
        integrity_violations.append("FULL_13_RUN_CURRENT_ARCHIVE_VERIFICATION_NOT_PASS")

    baseline_runs = _run_map(baseline, "baseline")
    candidate_runs = _run_map(candidate, "candidate")
    if tuple(baseline_runs) != tuple(candidate_runs):
        integrity_violations.append("RUN_SCOPE_OR_ORDER_MISMATCH")
    if len(baseline_runs) != 13 or len(candidate_runs) != 13:
        integrity_violations.append("FULL_13_RUN_RESULT_SCOPE_MISSING")
    immutable_run_fields = (
        "runtime_run_id",
        "strategy_ids",
        "strategy_count",
        "strategy_account_count",
        "strategy_version",
        "event_order",
        "event_count",
        "first_receive_ts_ms",
        "last_receive_ts_ms",
        "event_type_counts",
        "strategy_evaluation_count",
        "source_strategy_settings",
        "real_orders_enabled",
        "auth_required",
        "ledger_attached",
    )
    for run_id in sorted(baseline_runs.keys() & candidate_runs.keys()):
        baseline_run = baseline_runs[run_id]
        candidate_run = candidate_runs[run_id]
        if any(
            baseline_run.get(key) != candidate_run.get(key)
            for key in immutable_run_fields
        ):
            integrity_violations.append(f"RUN_IMMUTABLE_INPUT_MISMATCH:{run_id}")
        baseline_decisions = _mapping(
            baseline_run.get("strategy_decision_diagnostics"),
            f"baseline.{run_id}.strategy_decision_diagnostics",
        )
        candidate_decisions = _mapping(
            candidate_run.get("strategy_decision_diagnostics"),
            f"candidate.{run_id}.strategy_decision_diagnostics",
        )
        baseline_target = _mapping(baseline_decisions.get(target_strategy_id), "baseline target")
        baseline_gate_target = _mapping(
            baseline_decisions.get(baseline_target_strategy_id),
            "baseline gate target",
        )
        candidate_target = _mapping(candidate_decisions.get(target_strategy_id), "candidate target")
        if (
            baseline_target.get("evaluated") != candidate_target.get("evaluated")
            or baseline_target.get("baseline_qualified")
            != candidate_target.get("baseline_qualified")
            or baseline_target.get("rejection_counts")
            != candidate_target.get("rejection_counts")
        ):
            integrity_violations.append(f"TARGET_BASELINE_SIGNAL_MISMATCH:{run_id}")
        if int(str(candidate_target.get("post_gate_qualified", 0))) > int(
            str(baseline_target.get("post_gate_qualified", 0))
        ):
            integrity_violations.append(f"TARGET_POST_GATE_SIGNAL_INCREASED:{run_id}")
        baseline_gate_diagnostics = _mapping(
            baseline_run.get("signal_gate_diagnostics"),
            "baseline.signal_gate_diagnostics",
        )
        candidate_gate_diagnostics = _mapping(
            candidate_run.get("signal_gate_diagnostics"),
            "candidate.signal_gate_diagnostics",
        )
        baseline_qualified = int(
            str(baseline_gate_diagnostics.get("baseline_qualified_count", 0))
        )
        candidate_baseline_qualified = int(
            str(candidate_gate_diagnostics.get("baseline_qualified_count", 0))
        )
        candidate_accepted = int(
            str(candidate_gate_diagnostics.get("accepted_qualified_count", 0))
        )
        candidate_rejected = int(
            str(candidate_gate_diagnostics.get("rejected_qualified_count", 0))
        )
        if (
            baseline_gate_diagnostics.get("can_create_signals") is not False
            or baseline_qualified
            != int(str(baseline_gate_target.get("baseline_qualified", 0)))
            or int(str(baseline_gate_diagnostics.get("accepted_qualified_count", 0)))
            != baseline_qualified
            or int(str(baseline_gate_diagnostics.get("rejected_qualified_count", 0)))
            != 0
        ):
            integrity_violations.append(f"BASELINE_GATE_CHANGED_SIGNAL:{run_id}")
        if (
            candidate_gate_diagnostics.get("can_create_signals") is not False
            or candidate_baseline_qualified
            != int(str(baseline_target.get("baseline_qualified", 0)))
            or candidate_accepted + candidate_rejected != candidate_baseline_qualified
            or candidate_accepted
            != int(str(candidate_target.get("post_gate_qualified", 0)))
        ):
            integrity_violations.append(f"CANDIDATE_GATE_ACCOUNTING_MISMATCH:{run_id}")
        baseline_counts = _mapping(
            baseline_run.get("candidate_plan_counts"), "baseline.candidate_plan_counts"
        )
        candidate_counts = _mapping(
            candidate_run.get("candidate_plan_counts"), "candidate.candidate_plan_counts"
        )
        if int(str(candidate_counts.get(target_strategy_id, 0))) > int(
            str(baseline_counts.get(target_strategy_id, 0))
        ):
            integrity_violations.append(f"TARGET_CANDIDATE_PLAN_INCREASED:{run_id}")
        for strategy_id in strategy_ids:
            if strategy_id == target_strategy_id:
                continue
            if baseline_decisions.get(strategy_id) != candidate_decisions.get(strategy_id):
                integrity_violations.append(
                    f"NON_TARGET_DECISION_CHANGED:{run_id}:{strategy_id}"
                )
            if baseline_counts.get(strategy_id) != candidate_counts.get(strategy_id):
                integrity_violations.append(
                    f"NON_TARGET_CANDIDATE_PLAN_CHANGED:{run_id}:{strategy_id}"
                )
        if _normalized_non_target_trades(
            baseline_run,
            target_strategy_id=target_strategy_id,
        ) != _normalized_non_target_trades(
            candidate_run,
            target_strategy_id=target_strategy_id,
        ):
            integrity_violations.append(f"NON_TARGET_TRADES_CHANGED:{run_id}")
    candidate_robustness = _strategy_robustness(
        candidate,
        target_strategy_id=target_strategy_id,
    )
    for label, payload in (("BASELINE", baseline), ("CANDIDATE", candidate)):
        robustness = _mapping(payload.get("robustness_evaluation"), "robustness_evaluation")
        final_oos = _mapping(robustness.get("final_oos"), "robustness_evaluation.final_oos")
        if (
            robustness.get("status")
            != "HISTORICAL_METRICS_CALCULATED_FORWARD_PENDING"
            or final_oos.get("opened_for_this_result") is not True
            or final_oos.get("no_retuning_after_open") is not True
            or final_oos.get("missing_run_ids") != []
        ):
            integrity_violations.append(f"{label}_TIME_ORDERED_OOS_NOT_COMPLETE")
    profile_comparisons: dict[str, dict[str, object]] = {}
    comparative_gates: dict[str, bool] = {}
    absolute_profile_gates: dict[str, dict[str, bool]] = {}
    candidate_profiles: dict[str, Mapping[str, object]] = {}
    for profile in ("BASE", "STRESS"):
        baseline_profile = _profile_metrics(
            baseline,
            target_strategy_id=target_strategy_id,
            profile=profile,
        )
        candidate_profile = _profile_metrics(
            candidate,
            target_strategy_id=target_strategy_id,
            profile=profile,
        )
        candidate_profiles[profile] = candidate_profile
        absolute_gates = _candidate_profile_absolute_gates(candidate_profile)
        absolute_profile_gates[profile] = absolute_gates
        gates = {
            "expectancy_bps_not_lower": _not_lower(
                candidate_profile.get("expectancy_bps"),
                baseline_profile.get("expectancy_bps"),
            ),
            "maximum_drawdown_not_higher": _not_higher_decimal(
                candidate_profile.get("maximum_drawdown_usdt", "Infinity"),
                baseline_profile.get("maximum_drawdown_usdt", "-Infinity"),
            ),
        }
        comparative_gates.update(
            {f"{profile}_{name}": passed for name, passed in gates.items()}
        )
        profile_comparisons[profile] = {
            "baseline": dict(baseline_profile),
            "candidate": dict(candidate_profile),
            "candidate_absolute_gates": absolute_gates,
            "comparative_gates": gates,
        }

    candidate_opportunities = int(
        str(candidate_robustness.get("final_oos_unique_market_opportunity_count", 0))
    )
    candidate_pbo_gates = _mapping(
        candidate_robustness.get("pbo_gate_by_profile"),
        "candidate pbo_gate_by_profile",
    )
    candidate_concentration = _mapping(
        candidate_robustness.get("concentration"),
        "candidate concentration",
    )
    family_promotion = family_promotion_assessment(
        target_strategy_id,
        unique_opportunities=candidate_opportunities,
        profiles=candidate_profiles,
        pbo_by_profile={
            profile: {"pbo": 0 if candidate_pbo_gates.get(profile) is True else None}
            for profile in ("BASE", "STRESS")
        },
    )
    historical_gate_components = {
        "minimum_unique_opportunities": candidate_opportunities
        >= MINIMUM_OPPORTUNITIES,
        "no_censored_final_oos_state": int(
            str(candidate_robustness.get("final_oos_censored_count", -1))
        )
        == 0,
        "base_profile_absolute_gates": all(absolute_profile_gates["BASE"].values()),
        "stress_profile_absolute_gates": all(
            absolute_profile_gates["STRESS"].values()
        ),
        "base_pbo_gate": candidate_pbo_gates.get("BASE") is True,
        "stress_pbo_gate": candidate_pbo_gates.get("STRESS") is True,
        "concentration_gate": candidate_concentration.get("gate_passed") is True,
    }
    historical_gates_passed = all(historical_gate_components.values())
    blockers = list(
        dict.fromkeys(
            str(value)
            for value in _rows_as_values(candidate_robustness.get("ranking_blockers", []))
            if not _legacy_universal_win_rate_marker(value)
        )
    )
    if integrity_violations:
        status = "FAIL_INTEGRITY"
        decision = "INVALID_DO_NOT_USE"
    elif candidate_opportunities < MINIMUM_OPPORTUNITIES:
        status = "PASS_COMPARISON_COMPLETE"
        decision = "NOT_PROVEN_INSUFFICIENT_OOS_SAMPLE"
    elif not historical_gates_passed:
        status = "PASS_COMPARISON_COMPLETE"
        decision = "REJECTED_HISTORICAL_GATES"
    elif not all(comparative_gates.values()):
        status = "PASS_COMPARISON_COMPLETE"
        decision = "REJECTED_NO_BASELINE_IMPROVEMENT"
    else:
        status = "PASS_COMPARISON_COMPLETE"
        decision = "HISTORICAL_CANDIDATE_FORWARD_SHADOW_PENDING"

    return {
        "schema_version": 1,
        "status": status,
        "decision": decision,
        "target_strategy_id": target_strategy_id,
        "baseline_shared_target_strategy_id": baseline_target_strategy_id,
        "baseline_is_reusable_all_strategy_none_gate_reference": True,
        "baseline_trial_id": baseline.get("signal_gate_trial_id"),
        "candidate_trial_id": candidate.get("signal_gate_trial_id"),
        "strategy_logic": strategy_logic,
        "integrity_violations": list(dict.fromkeys(integrity_violations)),
        "same_input_and_non_target_invariance_passed": not integrity_violations,
        "candidate_final_oos_unique_market_opportunities": candidate_opportunities,
        "minimum_opportunities": MINIMUM_OPPORTUNITIES,
        "universal_minimum_win_rate_per_profile": None,
        "positive_wilson_lower_required": True,
        "candidate_historical_cost_oos_statistical_and_concentration_gates_passed": (
            historical_gates_passed
        ),
        "candidate_historical_gate_components": historical_gate_components,
        "candidate_absolute_profile_gates": absolute_profile_gates,
        "comparative_gates": comparative_gates,
        "profile_comparisons": profile_comparisons,
        "candidate_ranking_blockers": blockers,
        "candidate_family_promotion": family_promotion,
        "candidate_family_promotion_gate_passed": family_promotion["gate_passed"],
        "family_promotion_required_before_promotion": True,
        "historical_candidate_for_forward_shadow": (
            decision == "HISTORICAL_CANDIDATE_FORWARD_SHADOW_PENDING"
        ),
        "ranking_eligible": False,
        "promotion_allowed": False,
        "profitability_status": "NOT_PROVEN",
        "profitability_claim_allowed": False,
        "independent_forward_live_public_required": True,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
    }


def _rows_as_values(value: object) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError("예상한 JSON 배열이 아닙니다.")
    return list(value)


def _load(path: Path) -> Mapping[str, object]:
    return _mapping(json.loads(path.read_text(encoding="utf-8")), path.as_posix())


def _atomic_write(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(f"기존 비교 결과를 덮어쓰지 않습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = compare_strategy_gate_trials(
        _load(arguments.baseline),
        _load(arguments.candidate),
    )
    _atomic_write(arguments.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS_COMPARISON_COMPLETE" else 2)


if __name__ == "__main__":
    main()
