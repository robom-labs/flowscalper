# 100후보 screening에서 paired exit·walk-forward·다중검정 증거를 분리해 만든다.

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from backend.app.research.candidate_registry import (
    HORIZON_MAXIMUM_HOLD_MS,
    preregistered_trials,
)
from backend.app.research.screening import ScreeningTrade

_PROFILES = ("BASE", "STRESS")
_EXITS = ("E01", "E02", "E03", "E04", "E05")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _with_checksum(payload: dict[str, Any]) -> dict[str, Any]:
    payload["manifest_sha256"] = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    return payload


def _verified_screening_rows(report: Mapping[str, object]) -> list[Mapping[str, object]]:
    material = dict(report)
    claimed = material.pop("manifest_sha256", None)
    actual = hashlib.sha256(_canonical_json(material).encode()).hexdigest()
    if claimed != actual:
        raise ValueError("screening report 내부 checksum이 다릅니다.")
    if (
        report.get("registered_trial_count") != 100
        or report.get("screening_eligible_count") != 90
        or report.get("blocked_trial_count") != 10
        or report.get("final_oos_status") != "SEALED_NOT_USED_FOR_SELECTION"
        or report.get("active_count") != 0
        or report.get("live_shadow_count") != 0
        or report.get("paper_only") is not True
        or report.get("real_orders_enabled") is not False
        or report.get("private_api_enabled") is not False
    ):
        raise ValueError("screening report의 trial·Final OOS·PAPER 경계가 잘못됐습니다.")
    rows = report.get("results")
    if not isinstance(rows, list) or len(rows) != 100:
        raise ValueError("screening report에는 100개 trial 결과가 필요합니다.")
    normalized = [row for row in rows if isinstance(row, Mapping)]
    if len(normalized) != 100:
        raise ValueError("screening trial 결과 형식이 잘못됐습니다.")
    selected = report.get("event_replay_selected")
    selection_count = report.get("selection_count")
    if (
        not isinstance(selected, list)
        or any(not isinstance(trial_id, str) for trial_id in selected)
        or len(selected) > 25
        or len(set(selected)) != len(selected)
        or selection_count != len(selected)
    ):
        raise ValueError("screening event replay 선택 계약이 잘못됐습니다.")
    return normalized


def _trial_parts(row: Mapping[str, object]) -> tuple[str, str, str, Mapping[str, object]]:
    trial = row.get("trial")
    statistics = row.get("statistics")
    if not isinstance(trial, Mapping) or not isinstance(statistics, Mapping):
        raise ValueError("screening trial·statistics 형식이 잘못됐습니다.")
    alpha = trial.get("alpha")
    exit_spec = trial.get("exit")
    trial_id = trial.get("trial_id")
    if (
        not isinstance(alpha, Mapping)
        or not isinstance(exit_spec, Mapping)
        or not isinstance(trial_id, str)
        or not isinstance(alpha.get("family_id"), str)
        or not isinstance(exit_spec.get("exit_id"), str)
    ):
        raise ValueError("screening trial 식별자가 잘못됐습니다.")
    return trial_id, str(alpha["family_id"]), str(exit_spec["exit_id"]), statistics


def _screening_split_statistics(
    statistics: Mapping[str, object],
    profile: str,
    split: str,
) -> Mapping[str, object] | None:
    profiles = statistics.get("profiles")
    profile_row = profiles.get(profile) if isinstance(profiles, Mapping) else None
    splits = profile_row.get("splits") if isinstance(profile_row, Mapping) else None
    split_row = splits.get(split) if isinstance(splits, Mapping) else None
    return split_row if isinstance(split_row, Mapping) else None


def _verify_validation_trade_projection(
    statistics_by_trial_id: Mapping[str, Mapping[str, object]],
    trades: Sequence[ScreeningTrade],
) -> None:
    if any(trade.split == "FINAL_OOS" for trade in trades):
        raise ValueError("walk-forward 입력에 Final OOS 거래를 섞을 수 없습니다.")
    if len({trade.trade_id for trade in trades}) != len(trades):
        raise ValueError("walk-forward 입력 거래 ID가 중복됐습니다.")
    for trial_id, statistics in statistics_by_trial_id.items():
        for profile in _PROFILES:
            expected = _screening_split_statistics(statistics, profile, "VALIDATION")
            observed = [
                trade
                for trade in trades
                if trade.trial_id == trial_id
                and trade.profile == profile
                and trade.split == "VALIDATION"
            ]
            expected_count = int(str(expected.get("sample_size", 0))) if expected else 0
            expected_net = (
                Decimal(str(expected.get("net_pnl_usdt", "0"))) if expected else Decimal(0)
            )
            expected_fees = Decimal(str(expected.get("fees_usdt", "0"))) if expected else Decimal(0)
            expected_slippage = (
                Decimal(str(expected.get("slippage_usdt", "0"))) if expected else Decimal(0)
            )
            if (
                len(observed) != expected_count
                or sum((trade.net_pnl_usdt for trade in observed), start=Decimal(0)) != expected_net
                or sum((trade.fee_usdt for trade in observed), start=Decimal(0)) != expected_fees
                or sum((trade.slippage_usdt for trade in observed), start=Decimal(0))
                != expected_slippage
            ):
                raise ValueError(
                    "walk-forward 거래가 screening Validation 표본·비용·순손익과 "
                    f"다릅니다: {trial_id}:{profile}"
                )


def _validation_profile(statistics: Mapping[str, object], profile: str) -> dict[str, object]:
    profiles = statistics.get("profiles")
    if not isinstance(profiles, Mapping):
        return {"status": str(statistics.get("status", "UNKNOWN")), "sample_size": 0}
    profile_row = profiles.get(profile)
    if not isinstance(profile_row, Mapping):
        return {"status": "MISSING_PROFILE", "sample_size": 0}
    splits = profile_row.get("splits")
    validation = splits.get("VALIDATION") if isinstance(splits, Mapping) else None
    if not isinstance(validation, Mapping):
        return {"status": "MISSING_VALIDATION", "sample_size": 0}
    fields = (
        "sample_size",
        "expectancy_bps",
        "profit_factor",
        "net_pnl_usdt",
        "fees_usdt",
        "slippage_usdt",
        "maximum_drawdown_bps",
        "trail_activation_count",
        "trail_activation_rate",
        "tp1_fill_rate",
        "runner_count",
        "runner_rate",
        "runner_net_contribution_usdt",
        "mfe_capture_ratio_mean",
        "average_peak_giveback_usdt",
        "median_peak_giveback_usdt",
        "p90_peak_giveback_usdt",
        "trail_trigger_count",
        "trail_trigger_slippage_usdt",
        "activation_after_net_negative_exit_count",
        "stop_before_trail_activation_count",
    )
    source_status = str(statistics.get("status", "UNKNOWN"))
    status = "EXECUTED" if source_status == "EXECUTED" else f"{source_status}_PRESERVED"
    return {"status": status, **{field: validation.get(field) for field in fields}}


def _weighted_expectancy(
    returns: Sequence[float | None],
    counts: Sequence[int],
    indexes: Sequence[int],
) -> float | None:
    total_count = sum(counts[index] for index in indexes)
    if total_count <= 0:
        return None
    weighted_total = 0.0
    for index in indexes:
        value = returns[index]
        if value is not None and counts[index] > 0:
            weighted_total += value * counts[index]
    return weighted_total / total_count


def _walk_forward_windows(
    folds: Sequence[Mapping[str, object]],
    returns: Sequence[float | None],
    counts: Sequence[int],
    *,
    mode: str,
) -> dict[str, object]:
    if mode not in {"ANCHORED", "ROLLING"}:
        raise ValueError("walk-forward mode가 잘못됐습니다.")
    windows: list[dict[str, object]] = []
    for evaluation_index in range(1, len(folds)):
        training_indexes = (
            list(range(evaluation_index)) if mode == "ANCHORED" else [evaluation_index - 1]
        )
        training_count = sum(counts[index] for index in training_indexes)
        evaluation_count = counts[evaluation_index]
        windows.append(
            {
                "window_id": f"{mode}-{evaluation_index}",
                "training_fold_ids": [str(folds[index]["fold_id"]) for index in training_indexes],
                "evaluation_fold_id": str(folds[evaluation_index]["fold_id"]),
                "training_trade_count": training_count,
                "evaluation_trade_count": evaluation_count,
                "training_expectancy_bps": _weighted_expectancy(
                    returns,
                    counts,
                    training_indexes,
                ),
                "evaluation_expectancy_bps": (
                    returns[evaluation_index] if evaluation_count > 0 else None
                ),
                "status": (
                    "EXECUTED_FIXED_PARAMETERS"
                    if training_count > 0 and evaluation_count > 0
                    else "INSUFFICIENT_DATA"
                ),
            }
        )
    return {
        "mode": mode,
        "parameter_policy": "FIXED_PREREGISTERED_NO_RETUNING",
        "window_count": len(windows),
        "executed_window_count": sum(
            row["status"] == "EXECUTED_FIXED_PARAMETERS" for row in windows
        ),
        "status": (
            "EXECUTED_FIXED_PARAMETERS"
            if any(row["status"] == "EXECUTED_FIXED_PARAMETERS" for row in windows)
            else "INSUFFICIENT_DATA"
        ),
        "windows": windows,
    }


def _bull_bear_range(regime: str) -> str:
    normalized = regime.upper()
    if normalized in {"TREND_UP", "BULL", "UP"}:
        return "BULL"
    if normalized in {"TREND_DOWN", "BEAR", "DOWN"}:
        return "BEAR"
    if normalized in {"RANGE", "SIDEWAYS"}:
        return "RANGE"
    return "UNKNOWN"


def _holdout_value(trade: ScreeningTrade, dimension: str) -> str:
    if dimension == "bull_bear_range":
        return _bull_bear_range(trade.regime)
    if dimension == "symbol":
        return trade.symbol
    if dimension == "venue":
        return trade.venue
    if dimension == "regime":
        return trade.regime
    if dimension == "volatility":
        return trade.volatility_regime
    if dimension == "cost_profile":
        return trade.profile
    raise ValueError("holdout 차원이 잘못됐습니다.")


def _holdout_dimension(
    trades: Sequence[ScreeningTrade],
    *,
    dimension: str,
    required_groups: frozenset[str] = frozenset(),
) -> dict[str, object]:
    grouped: dict[str, list[ScreeningTrade]] = defaultdict(list)
    unknown_count = 0
    for trade in trades:
        group = _holdout_value(trade, dimension)
        if group == "UNKNOWN":
            unknown_count += 1
            continue
        grouped[group].append(trade)
    labeled_count = sum(len(rows) for rows in grouped.values())
    groups: list[dict[str, object]] = []
    for group in sorted(grouped):
        rows = grouped[group]
        groups.append(
            {
                "group": group,
                "holdout_trade_count": len(rows),
                "development_trade_count": labeled_count - len(rows),
                "trial_count": len({trade.trial_id for trade in rows}),
                "run_count": len({trade.run_id for trade in rows}),
                "symbol_count": len({trade.symbol for trade in rows}),
                "BASE_trade_count": sum(trade.profile == "BASE" for trade in rows),
                "STRESS_trade_count": sum(trade.profile == "STRESS" for trade in rows),
                "holdout_expectancy_bps": (
                    sum(trade.net_return_bps for trade in rows) / len(rows) if rows else None
                ),
                "holdout_net_pnl_usdt": str(
                    sum((trade.net_pnl_usdt for trade in rows), start=Decimal(0))
                ),
            }
        )
    missing_required = sorted(required_groups - set(grouped))
    if not trades:
        status = "INSUFFICIENT_DATA_NO_TRADES"
    elif unknown_count:
        status = "INCOMPLETE_POINT_IN_TIME_LABELS"
    elif len(grouped) < 2:
        status = "INSUFFICIENT_GROUP_VARIATION"
    elif missing_required:
        status = "INSUFFICIENT_REQUIRED_GROUP_COVERAGE"
    else:
        status = "EXECUTED_FIXED_PARAMETERS_DIAGNOSTIC_ONLY"
    return {
        "dimension": dimension,
        "method": "LEAVE_ONE_GROUP_OUT_FIXED_PREREGISTERED_PARAMETERS",
        "status": status,
        "group_count": len(grouped),
        "labeled_trade_count": labeled_count,
        "unknown_label_trade_count": unknown_count,
        "missing_required_groups": missing_required,
        "groups": groups,
        "selection_or_retuning_performed": False,
    }


def _holdout_diagnostics(trades: Sequence[ScreeningTrade]) -> dict[str, object]:
    return {
        "symbol": _holdout_dimension(trades, dimension="symbol"),
        "venue": _holdout_dimension(trades, dimension="venue"),
        "regime": _holdout_dimension(trades, dimension="regime"),
        "volatility": _holdout_dimension(trades, dimension="volatility"),
        "bull_bear_range": _holdout_dimension(
            trades,
            dimension="bull_bear_range",
            required_groups=frozenset({"BULL", "BEAR", "RANGE"}),
        ),
        "cost_profile": _holdout_dimension(
            trades,
            dimension="cost_profile",
            required_groups=frozenset(_PROFILES),
        ),
    }


def _secondary_event_replay_gate_reasons(trial: Mapping[str, object]) -> list[str]:
    reasons: list[str] = []
    profiles = trial.get("profiles")
    for profile in _PROFILES:
        profile_row = profiles.get(profile) if isinstance(profiles, Mapping) else None
        if not isinstance(profile_row, Mapping):
            reasons.append(f"{profile}_PROFILE_MISSING")
            continue
        for method in ("anchored_walk_forward", "rolling_walk_forward"):
            method_row = profile_row.get(method)
            if (
                not isinstance(method_row, Mapping)
                or method_row.get("status") != "EXECUTED_FIXED_PARAMETERS"
            ):
                reasons.append(f"{profile}_{method.upper()}_INSUFFICIENT")
    holdouts = trial.get("holdout_diagnostics")
    for dimension in (
        "symbol",
        "venue",
        "regime",
        "volatility",
        "bull_bear_range",
        "cost_profile",
    ):
        row = holdouts.get(dimension) if isinstance(holdouts, Mapping) else None
        if (
            not isinstance(row, Mapping)
            or row.get("status") != "EXECUTED_FIXED_PARAMETERS_DIAGNOSTIC_ONLY"
        ):
            reasons.append(f"HOLDOUT_{dimension.upper()}_INSUFFICIENT")
    return reasons


def _paired_exit_rows(
    trades: Sequence[ScreeningTrade],
    *,
    family_id: str,
    profile: str,
) -> dict[str, object]:
    trial_to_exit = {
        trial.trial_id: trial.exit.exit_id
        for trial in preregistered_trials()
        if trial.alpha.family_id == family_id
    }
    cohorts: dict[tuple[str, str, str, str, str], dict[str, ScreeningTrade]] = defaultdict(dict)
    for trade in trades:
        if (
            trade.profile != profile
            or trade.split != "VALIDATION"
            or trade.signal_event_id is None
            or trade.trial_id not in trial_to_exit
        ):
            continue
        key = (
            trade.run_id,
            trade.signal_event_id,
            trade.symbol,
            trade.side,
            trade.profile,
        )
        exit_id = trial_to_exit[trade.trial_id]
        if exit_id in cohorts[key]:
            raise ValueError("같은 signal cohort·exit에 screening 거래가 중복됐습니다.")
        cohorts[key][exit_id] = trade
    complete = [rows for rows in cohorts.values() if set(rows) == set(_EXITS)]
    comparisons: dict[str, object] = {}
    for exit_id in _EXITS:
        if exit_id == "E01":
            comparisons[exit_id] = {
                "paired_sample_size": len(complete),
                "net_pnl_total_delta_vs_e01_usdt": "0",
                "net_pnl_mean_delta_vs_e01_usdt": "0",
                "better_than_e01_count": 0,
                "worse_than_e01_count": 0,
                "tie_count": len(complete),
            }
            continue
        deltas = [rows[exit_id].net_pnl_usdt - rows["E01"].net_pnl_usdt for rows in complete]
        comparisons[exit_id] = {
            "paired_sample_size": len(deltas),
            "net_pnl_total_delta_vs_e01_usdt": str(sum(deltas, start=Decimal(0))),
            "net_pnl_mean_delta_vs_e01_usdt": str(
                sum(deltas, start=Decimal(0)) / len(deltas) if deltas else Decimal(0)
            ),
            "better_than_e01_count": sum(delta > 0 for delta in deltas),
            "worse_than_e01_count": sum(delta < 0 for delta in deltas),
            "tie_count": sum(delta == 0 for delta in deltas),
        }
    return {
        "status": "EXECUTED" if complete else "INSUFFICIENT_PAIRED_COHORTS",
        "observed_signal_cohort_count": len(cohorts),
        "complete_five_exit_cohort_count": len(complete),
        "comparisons": comparisons,
    }


def build_trailing_ablation_report(
    screening_report: Mapping[str, object],
    trades: Sequence[ScreeningTrade],
    *,
    generated_ts_utc: str,
) -> dict[str, Any]:
    rows = _verified_screening_rows(screening_report)
    grouped: dict[str, dict[str, tuple[str, Mapping[str, object]]]] = defaultdict(dict)
    for row in rows:
        trial_id, family_id, exit_id, statistics = _trial_parts(row)
        if exit_id in grouped[family_id]:
            raise ValueError("alpha family 안에 exit trial이 중복됐습니다.")
        grouped[family_id][exit_id] = (trial_id, statistics)
    if len(grouped) != 20 or any(set(values) != set(_EXITS) for values in grouped.values()):
        raise ValueError("trailing ablation에는 20 alpha × 5 exit가 필요합니다.")
    families: list[dict[str, object]] = []
    paired_total = 0
    for family_id in sorted(grouped):
        exits: list[dict[str, object]] = []
        for exit_id in _EXITS:
            trial_id, statistics = grouped[family_id][exit_id]
            exits.append(
                {
                    "trial_id": trial_id,
                    "exit_id": exit_id,
                    "status": statistics.get("status"),
                    "BASE_validation": _validation_profile(statistics, "BASE"),
                    "STRESS_validation": _validation_profile(statistics, "STRESS"),
                }
            )
        paired = {
            profile: _paired_exit_rows(trades, family_id=family_id, profile=profile)
            for profile in _PROFILES
        }
        paired_total += sum(
            int(str(paired[profile]["complete_five_exit_cohort_count"])) for profile in _PROFILES
        )
        families.append(
            {
                "family_id": family_id,
                "exit_trials": exits,
                "paired_same_signal_exit_ablation": paired,
            }
        )
    return _with_checksum(
        {
            "schema_version": 1,
            "status": (
                "EXECUTED_PAIRED_COHORTS_AVAILABLE"
                if paired_total
                else "EXECUTED_INSUFFICIENT_PAIRED_COHORTS"
            ),
            "generated_ts_utc": generated_ts_utc,
            "screening_manifest_sha256": screening_report["manifest_sha256"],
            "alpha_family_count": 20,
            "exit_module_count": 5,
            "paired_complete_cohort_count": paired_total,
            "families": families,
            "selection_or_promotion_performed": False,
            "final_oos_used": False,
            "profitability_claim": "NOT_PROVEN",
            "paper_only": True,
            "real_orders_enabled": False,
            "private_api_enabled": False,
        }
    )


def build_walk_forward_report(
    screening_report: Mapping[str, object],
    *,
    trades: Sequence[ScreeningTrade],
    folds_by_horizon: Mapping[str, Sequence[Mapping[str, object]]],
    fold_returns: Mapping[str, Sequence[float]],
    fold_trade_counts: Mapping[str, Sequence[int]],
    fold_crossing_excluded_count: int,
    generated_ts_utc: str,
) -> dict[str, Any]:
    rows = _verified_screening_rows(screening_report)
    trial_horizon_by_id: dict[str, str] = {}
    statistics_by_trial_id: dict[str, Mapping[str, object]] = {}
    for row in rows:
        trial_id, _, _, statistics = _trial_parts(row)
        trial = row.get("trial")
        alpha = trial.get("alpha") if isinstance(trial, Mapping) else None
        horizon = alpha.get("horizon") if isinstance(alpha, Mapping) else None
        if not isinstance(horizon, str):
            raise ValueError("screening trial horizon이 없습니다.")
        trial_horizon_by_id[trial_id] = horizon
        statistics_by_trial_id[trial_id] = statistics
    executed_ids = {
        trial_id
        for trial_id, statistics in statistics_by_trial_id.items()
        if statistics.get("status") == "EXECUTED"
    }
    _verify_validation_trade_projection(statistics_by_trial_id, trades)
    if (
        set(folds_by_horizon) != set(HORIZON_MAXIMUM_HOLD_MS)
        or set(fold_returns) != executed_ids
        or set(fold_trade_counts) != executed_ids
        or fold_crossing_excluded_count < 0
    ):
        raise ValueError("walk-forward는 실행 trial 전체와 정확히 네 fold를 요구합니다.")
    normalized_folds_by_horizon: dict[str, list[dict[str, object]]] = {}
    for horizon, folds in folds_by_horizon.items():
        normalized_folds: list[dict[str, object]] = []
        previous_end: int | None = None
        for row in folds:
            fold_id = str(row.get("fold_id", ""))
            start = int(str(row.get("start_ts_ms", -1)))
            end = int(str(row.get("end_ts_ms", -1)))
            if (
                not fold_id
                or start < 0
                or end <= start
                or (previous_end is not None and start < previous_end)
            ):
                raise ValueError("walk-forward fold 시각 순서가 잘못됐습니다.")
            normalized_folds.append({"fold_id": fold_id, "start_ts_ms": start, "end_ts_ms": end})
            previous_end = end
        if normalized_folds and len(normalized_folds) != 4:
            raise ValueError("실행 가능한 horizon의 walk-forward는 정확히 네 fold여야 합니다.")
        normalized_folds_by_horizon[horizon] = normalized_folds
    unknown_trade_ids = {
        trade.trial_id for trade in trades if trade.trial_id not in trial_horizon_by_id
    }
    if unknown_trade_ids:
        raise ValueError("walk-forward에 실행되지 않은 trial 거래가 섞였습니다.")
    trials: list[dict[str, object]] = []
    for trial_id in sorted(executed_ids):
        horizon = trial_horizon_by_id[trial_id]
        normalized_folds = normalized_folds_by_horizon.get(horizon, [])
        if len(normalized_folds) != 4:
            raise ValueError("실행 trial horizon에는 정확히 네 Validation fold가 필요합니다.")
        returns = list(fold_returns[trial_id])
        counts = list(fold_trade_counts[trial_id])
        if len(returns) != 4 or len(counts) != 4 or any(count < 0 for count in counts):
            raise ValueError("walk-forward trial의 fold 수익률·거래 수가 잘못됐습니다.")
        profiles: dict[str, dict[str, object]] = {}
        for profile in _PROFILES:
            profile_returns: list[float | None] = []
            profile_counts: list[int] = []
            for fold in normalized_folds:
                contained = [
                    trade.net_return_bps
                    for trade in trades
                    if trade.trial_id == trial_id
                    and trade.profile == profile
                    and trade.split == "VALIDATION"
                    and trade.entry_ts_ms >= int(str(fold["start_ts_ms"]))
                    and trade.exit_ts_ms <= int(str(fold["end_ts_ms"]))
                ]
                profile_returns.append(sum(contained) / len(contained) if contained else None)
                profile_counts.append(len(contained))
            pbo_values = [value if value is not None else 0.0 for value in profile_returns]
            if profile == "STRESS" and (profile_counts != counts or pbo_values != returns):
                raise ValueError("walk-forward STRESS fold가 PBO 입력과 다릅니다.")
            observed = [
                value
                for value, count in zip(profile_returns, profile_counts, strict=True)
                if count > 0 and value is not None
            ]
            anchored = _walk_forward_windows(
                normalized_folds,
                profile_returns,
                profile_counts,
                mode="ANCHORED",
            )
            rolling = _walk_forward_windows(
                normalized_folds,
                profile_returns,
                profile_counts,
                mode="ROLLING",
            )
            profiles[profile] = {
                "fold_expectancy_bps": profile_returns,
                "fold_trade_counts": profile_counts,
                "folds_with_trades": len(observed),
                "positive_fold_count": sum(value > 0 for value in observed),
                "negative_fold_count": sum(value < 0 for value in observed),
                "worst_fold_expectancy_bps": min(observed) if observed else None,
                "anchored_walk_forward": anchored,
                "rolling_walk_forward": rolling,
                "status": "EXECUTED" if observed else "INSUFFICIENT_DATA_NO_TRADES",
            }
        trial_validation_trades = [
            trade for trade in trades if trade.trial_id == trial_id and trade.split == "VALIDATION"
        ]
        trials.append(
            {
                "trial_id": trial_id,
                "horizon": horizon,
                "profiles": profiles,
                "stress_validation_fold_expectancy_bps_for_pbo": returns,
                "stress_validation_fold_trade_counts": counts,
                "holdout_diagnostics": _holdout_diagnostics(trial_validation_trades),
                "status": (
                    "EXECUTED"
                    if any(profiles[profile]["status"] == "EXECUTED" for profile in _PROFILES)
                    else "INSUFFICIENT_DATA_NO_TRADES"
                ),
            }
        )
    primary_value = screening_report.get("event_replay_selected")
    primary_ids = [str(value) for value in primary_value] if isinstance(primary_value, list) else []
    trials_by_id = {str(row["trial_id"]): row for row in trials}
    if any(trial_id not in trials_by_id for trial_id in primary_ids):
        raise ValueError("screening이 실행되지 않은 trial을 event replay로 선택했습니다.")
    secondary_gate_results: list[dict[str, object]] = []
    secondary_selected: list[str] = []
    for trial_id in primary_ids:
        reasons = _secondary_event_replay_gate_reasons(trials_by_id[trial_id])
        passed = not reasons
        secondary_gate_results.append(
            {
                "trial_id": trial_id,
                "passed": passed,
                "reasons": reasons,
            }
        )
        if passed:
            secondary_selected.append(trial_id)
    traded = sum(row["status"] == "EXECUTED" for row in trials)
    base_traded = sum(
        row["profiles"]["BASE"]["status"] == "EXECUTED"  # type: ignore[index]
        for row in trials
    )
    stress_traded = sum(
        row["profiles"]["STRESS"]["status"] == "EXECUTED"  # type: ignore[index]
        for row in trials
    )
    validation_trades = [
        trade for trade in trades if trade.split == "VALIDATION" and trade.trial_id in executed_ids
    ]
    excluded_by_profile = {
        profile: sum(
            trade.profile == profile
            and not any(
                trade.entry_ts_ms >= int(str(fold["start_ts_ms"]))
                and trade.exit_ts_ms <= int(str(fold["end_ts_ms"]))
                for fold in normalized_folds_by_horizon[trial_horizon_by_id[trade.trial_id]]
            )
            for trade in validation_trades
        )
        for profile in _PROFILES
    }
    if excluded_by_profile["STRESS"] != fold_crossing_excluded_count:
        raise ValueError("walk-forward STRESS fold 제외 건수가 실행 집계와 다릅니다.")
    nonexecuted_validation_by_profile = {
        profile: sum(
            trade.profile == profile
            and trade.trial_id in trial_horizon_by_id
            and trade.trial_id not in executed_ids
            for trade in trades
            if trade.split == "VALIDATION"
        )
        for profile in _PROFILES
    }
    return _with_checksum(
        {
            "schema_version": 2,
            "status": "EXECUTED_VALIDATION_ONLY" if traded else "INSUFFICIENT_DATA_NO_TRADES",
            "generated_ts_utc": generated_ts_utc,
            "screening_manifest_sha256": screening_report["manifest_sha256"],
            "required_fold_count_per_executed_horizon": 4,
            "fold_count_by_horizon": {
                horizon: len(folds) for horizon, folds in normalized_folds_by_horizon.items()
            },
            "effective_folds_by_horizon": normalized_folds_by_horizon,
            "purge_embargo_ms_by_horizon": dict(HORIZON_MAXIMUM_HOLD_MS),
            "fold_crossing_excluded_stress_trade_count": fold_crossing_excluded_count,
            "fold_boundary_or_crossing_excluded_trade_count_by_profile": excluded_by_profile,
            "nonexecuted_trial_validation_trade_count_by_profile": (
                nonexecuted_validation_by_profile
            ),
            "executed_trial_count": len(trials),
            "trials_with_validation_trades": traded,
            "trials_with_base_validation_trades": base_traded,
            "trials_with_stress_validation_trades": stress_traded,
            "walk_forward_methods": {
                "ANCHORED": {
                    "training_policy": "ALL_PRIOR_FOLDS",
                    "evaluation_policy": "NEXT_FOLD",
                    "parameter_policy": "FIXED_PREREGISTERED_NO_RETUNING",
                },
                "ROLLING": {
                    "training_policy": "IMMEDIATELY_PRECEDING_FOLD",
                    "evaluation_policy": "NEXT_FOLD",
                    "parameter_policy": "FIXED_PREREGISTERED_NO_RETUNING",
                },
            },
            "holdout_policy": (
                "LEAVE_ONE_GROUP_OUT_FIXED_PREREGISTERED_PARAMETERS_DIAGNOSTIC_ONLY"
            ),
            "holdout_diagnostics": _holdout_diagnostics(validation_trades),
            "event_replay_primary_gate_candidates": primary_ids,
            "event_replay_secondary_gate_results": secondary_gate_results,
            "event_replay_selected_after_secondary_gates": secondary_selected,
            "event_replay_selection_count": len(secondary_selected),
            "event_replay_selection_limit": 25,
            "event_replay_selection_status": (
                "ELIGIBLE"
                if secondary_selected
                else (
                    "NO_SECONDARY_ELIGIBLE_CANDIDATES"
                    if primary_ids
                    else "NO_PRIMARY_ELIGIBLE_CANDIDATES"
                )
            ),
            "trials": trials,
            "global_pbo": screening_report["global_multiple_testing"],
            "selection_basis": "VALIDATION_ONLY",
            "selection_or_promotion_performed": False,
            "final_oos_used": False,
            "profitability_claim": "NOT_PROVEN",
            "paper_only": True,
            "real_orders_enabled": False,
            "private_api_enabled": False,
        }
    )


def build_multiple_testing_report(
    screening_report: Mapping[str, object],
    *,
    generated_ts_utc: str,
) -> dict[str, Any]:
    rows = _verified_screening_rows(screening_report)
    trials: list[dict[str, object]] = []
    for row in rows:
        trial_id, family_id, exit_id, statistics = _trial_parts(row)
        trials.append(
            {
                "trial_id": trial_id,
                "family_id": family_id,
                "exit_id": exit_id,
                "status": statistics.get("status"),
                "validation_bootstrap_expectancy_95pct": statistics.get(
                    "validation_bootstrap_expectancy_95pct"
                ),
                "validation_deflated_sharpe_ratio": statistics.get(
                    "validation_deflated_sharpe_ratio"
                ),
                "validation_gate": statistics.get("gate"),
            }
        )
    passed = sum(
        isinstance(row["validation_gate"], Mapping) and row["validation_gate"].get("passed") is True
        for row in trials
    )
    return _with_checksum(
        {
            "schema_version": 1,
            "status": "EXECUTED_VALIDATION_ONLY",
            "generated_ts_utc": generated_ts_utc,
            "screening_manifest_sha256": screening_report["manifest_sha256"],
            "registered_trial_count": 100,
            "global_pbo": screening_report["global_multiple_testing"],
            "validation_gate_pass_count_before_later_stages": passed,
            "trials": trials,
            "final_oos_status": "SEALED_NOT_USED_FOR_SELECTION",
            "active_count": 0,
            "live_shadow_count": 0,
            "selection_or_promotion_performed": False,
            "profitability_claim": "NOT_PROVEN",
            "paper_only": True,
            "real_orders_enabled": False,
            "private_api_enabled": False,
        }
    )
