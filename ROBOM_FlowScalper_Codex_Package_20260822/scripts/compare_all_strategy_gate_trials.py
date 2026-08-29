# 전 전략 일괄 gate 후보를 한 공통 기준선과 전략별로 분리해 비교한다.
"""일괄 계산은 재사용하되 각 전략의 무결성·비용·강건성 판정은 독립 유지한다."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from scripts.compare_strategy_gate_trials import compare_strategy_gate_trials
from scripts.research_runtime_strategy_replay import SIGNAL_GATE_TARGET_ALL

BASELINE_SIGNAL_GATE = "NONE"
_IMMUTABLE_RUN_FIELDS = (
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


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label}은 JSON 객체여야 합니다.")
    return value


def _rows(value: object, label: str) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError(f"{label}은 JSON 배열이어야 합니다.")
    return [_mapping(row, label) for row in value]


def _run_map(payload: Mapping[str, object], label: str) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for row in _rows(payload.get("runs"), f"{label}.runs"):
        run_id = str(row.get("run_id", ""))
        if not run_id or run_id in result:
            raise ValueError(f"{label}의 Run ID가 없거나 중복됐습니다.")
        result[run_id] = row
    return result


def _strategy_ids(payload: Mapping[str, object]) -> tuple[str, ...]:
    values = payload.get("strategy_ids")
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise ValueError("strategy_ids는 JSON 배열이어야 합니다.")
    result = tuple(str(value) for value in values)
    if not result or len(set(result)) != len(result):
        raise ValueError("strategy_ids가 비어 있거나 중복됐습니다.")
    return result


def _virtual_single_target_candidate(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    target_strategy_id: str,
) -> dict[str, object]:
    """공통 일괄 결과에서 한 전략의 gate 효과만 남겨 기존 엄격 비교기를 재사용한다."""

    virtual = deepcopy(dict(candidate))
    candidate_gate = str(candidate.get("signal_gate", ""))
    virtual["signal_gate_target_strategy_id"] = target_strategy_id
    virtual["signal_gate_trial_id"] = f"{candidate_gate}:{target_strategy_id}"
    baseline_runs = _run_map(baseline, "baseline")
    virtual_runs: list[dict[str, object]] = []
    for raw_candidate_run in _rows(candidate.get("runs"), "candidate.runs"):
        run_id = str(raw_candidate_run["run_id"])
        baseline_run = baseline_runs[run_id]
        candidate_run = deepcopy(dict(raw_candidate_run))
        baseline_decisions = _mapping(
            baseline_run.get("strategy_decision_diagnostics"),
            f"baseline.{run_id}.strategy_decision_diagnostics",
        )
        candidate_decisions = _mapping(
            raw_candidate_run.get("strategy_decision_diagnostics"),
            f"candidate.{run_id}.strategy_decision_diagnostics",
        )
        candidate_target = _mapping(
            candidate_decisions.get(target_strategy_id),
            f"candidate.{run_id}.{target_strategy_id}",
        )
        virtual_decisions = deepcopy(dict(baseline_decisions))
        virtual_decisions[target_strategy_id] = deepcopy(dict(candidate_target))
        candidate_run["strategy_decision_diagnostics"] = virtual_decisions

        baseline_counts = _mapping(
            baseline_run.get("candidate_plan_counts"),
            f"baseline.{run_id}.candidate_plan_counts",
        )
        candidate_counts = _mapping(
            raw_candidate_run.get("candidate_plan_counts"),
            f"candidate.{run_id}.candidate_plan_counts",
        )
        virtual_counts = deepcopy(dict(baseline_counts))
        virtual_counts[target_strategy_id] = candidate_counts.get(target_strategy_id, 0)
        candidate_run["candidate_plan_counts"] = virtual_counts

        baseline_trades = _rows(baseline_run.get("trade_rows", []), "baseline.trade_rows")
        candidate_trades = _rows(
            raw_candidate_run.get("trade_rows", []), "candidate.trade_rows"
        )
        candidate_run["trade_rows"] = [
            deepcopy(dict(row))
            for row in baseline_trades
            if str(row.get("strategy_id")) != target_strategy_id
        ] + [
            deepcopy(dict(row))
            for row in candidate_trades
            if str(row.get("strategy_id")) == target_strategy_id
        ]
        candidate_run["signal_gate_target_strategy_id"] = target_strategy_id
        candidate_run["signal_gate_trial_id"] = (
            f"{candidate_gate}:{target_strategy_id}"
        )
        candidate_run["signal_gate_diagnostics"] = {
            "signal_gate": candidate_gate,
            "strategy_logic": candidate.get("strategy_logic"),
            "baseline_qualified_count": candidate_target.get(
                "gate_baseline_qualified", 0
            ),
            "accepted_qualified_count": candidate_target.get(
                "gate_accepted_qualified", 0
            ),
            "rejected_qualified_count": candidate_target.get(
                "gate_rejected_qualified", 0
            ),
            "rejection_counts": deepcopy(
                candidate_target.get("gate_rejection_counts", {})
            ),
            "can_create_signals": False,
            "signal_gate_can_create_signals": False,
        }
        virtual_runs.append(candidate_run)
    virtual["runs"] = virtual_runs
    return virtual


def compare_all_strategy_gate_trials(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> dict[str, object]:
    """전략별 독립 비교 전에 일괄 실행의 공통 입력과 gate 회계를 검증한다."""

    violations: list[str] = []
    strategy_ids = _strategy_ids(candidate)
    candidate_gate = str(candidate.get("signal_gate", ""))
    if candidate.get("signal_gate_target_strategy_id") != SIGNAL_GATE_TARGET_ALL:
        violations.append("CANDIDATE_TARGET_NOT_ALL_REGISTERED_STRATEGIES")
    if candidate_gate in ("", BASELINE_SIGNAL_GATE):
        violations.append("CANDIDATE_SIGNAL_GATE_NOT_ACTIVE")
    if baseline.get("signal_gate") != BASELINE_SIGNAL_GATE:
        violations.append("BASELINE_SIGNAL_GATE_NOT_NONE")
    required = {
        "status": "RESEARCH_STRATEGY_LEAGUE_REPLAY_COMPLETE",
        "method": "ONE_PASS_ALL_REGISTERED_ACTUAL_PAPER_RUNTIME_PATH",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "runtime_ai_order_decision": False,
    }
    for label, payload in (("BASELINE", baseline), ("CANDIDATE", candidate)):
        for key, expected in required.items():
            if payload.get(key) != expected:
                violations.append(f"{label}_{key.upper()}_MISMATCH")
    for field_name in (
        "git_commit",
        "research_scope",
        "strategy_ids",
        "strategy_count",
        "strategy_account_count",
        "strategy_version",
        "strategy_logic",
    ):
        if baseline.get(field_name) != candidate.get(field_name):
            violations.append(f"{field_name.upper()}_MISMATCH")
    if int(str(candidate.get("strategy_count", -1))) != len(strategy_ids):
        violations.append("CANDIDATE_STRATEGY_COUNT_MISMATCH")
    if int(str(candidate.get("strategy_account_count", -1))) != len(strategy_ids) * 2:
        violations.append("CANDIDATE_STRATEGY_ACCOUNT_COUNT_MISMATCH")
    if candidate.get("signal_gate_trial_id") != (
        f"{candidate_gate}:{SIGNAL_GATE_TARGET_ALL}"
    ):
        violations.append("CANDIDATE_TRIAL_ID_MISMATCH")

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
        violations.append("FROZEN_DATASET_OR_BYTE_VERIFICATION_MISMATCH")

    baseline_runs = _run_map(baseline, "baseline")
    candidate_runs = _run_map(candidate, "candidate")
    if tuple(baseline_runs) != tuple(candidate_runs):
        violations.append("RUN_SCOPE_OR_ORDER_MISMATCH")
    if len(baseline_runs) != 13 or len(candidate_runs) != 13:
        violations.append("FULL_13_RUN_RESULT_SCOPE_MISSING")
    for run_id in sorted(baseline_runs.keys() & candidate_runs.keys()):
        baseline_run = baseline_runs[run_id]
        candidate_run = candidate_runs[run_id]
        if any(
            baseline_run.get(field_name) != candidate_run.get(field_name)
            for field_name in _IMMUTABLE_RUN_FIELDS
        ):
            violations.append(f"RUN_IMMUTABLE_INPUT_MISMATCH:{run_id}")
        if candidate_run.get("signal_gate_target_strategy_id") != SIGNAL_GATE_TARGET_ALL:
            violations.append(f"RUN_TARGET_NOT_ALL_REGISTERED_STRATEGIES:{run_id}")
        candidate_gate_diagnostics = _mapping(
            candidate_run.get("signal_gate_diagnostics"),
            f"candidate.{run_id}.signal_gate_diagnostics",
        )
        if candidate_gate_diagnostics.get("can_create_signals") is not False:
            violations.append(f"CANDIDATE_GATE_CAN_CREATE_SIGNALS:{run_id}")
        baseline_decisions = _mapping(
            baseline_run.get("strategy_decision_diagnostics"),
            f"baseline.{run_id}.strategy_decision_diagnostics",
        )
        candidate_decisions = _mapping(
            candidate_run.get("strategy_decision_diagnostics"),
            f"candidate.{run_id}.strategy_decision_diagnostics",
        )
        baseline_counts = _mapping(
            baseline_run.get("candidate_plan_counts"),
            f"baseline.{run_id}.candidate_plan_counts",
        )
        candidate_counts = _mapping(
            candidate_run.get("candidate_plan_counts"),
            f"candidate.{run_id}.candidate_plan_counts",
        )
        aggregate_baseline = 0
        aggregate_accepted = 0
        aggregate_rejected = 0
        aggregate_reasons: Counter[str] = Counter()
        for strategy_id in strategy_ids:
            baseline_row = _mapping(
                baseline_decisions.get(strategy_id), f"baseline.{run_id}.{strategy_id}"
            )
            candidate_row = _mapping(
                candidate_decisions.get(strategy_id), f"candidate.{run_id}.{strategy_id}"
            )
            if any(
                baseline_row.get(field_name) != candidate_row.get(field_name)
                for field_name in ("evaluated", "baseline_qualified", "rejection_counts")
            ):
                violations.append(f"BASELINE_SIGNAL_MISMATCH:{run_id}:{strategy_id}")
            gate_baseline = int(str(candidate_row.get("gate_baseline_qualified", -1)))
            gate_accepted = int(str(candidate_row.get("gate_accepted_qualified", -1)))
            gate_rejected = int(str(candidate_row.get("gate_rejected_qualified", -1)))
            if candidate_row.get("gate_targeted") is not True:
                violations.append(f"STRATEGY_NOT_GATE_TARGETED:{run_id}:{strategy_id}")
            if (
                gate_baseline != int(str(baseline_row.get("baseline_qualified", 0)))
                or gate_accepted + gate_rejected != gate_baseline
                or gate_accepted != int(str(candidate_row.get("post_gate_qualified", -1)))
            ):
                violations.append(f"STRATEGY_GATE_ACCOUNTING_MISMATCH:{run_id}:{strategy_id}")
            if int(str(candidate_row.get("post_gate_qualified", 0))) > int(
                str(baseline_row.get("post_gate_qualified", 0))
            ):
                violations.append(f"POST_GATE_SIGNAL_INCREASED:{run_id}:{strategy_id}")
            if int(str(candidate_counts.get(strategy_id, 0))) > int(
                str(baseline_counts.get(strategy_id, 0))
            ):
                violations.append(f"CANDIDATE_PLAN_INCREASED:{run_id}:{strategy_id}")
            aggregate_baseline += gate_baseline
            aggregate_accepted += gate_accepted
            aggregate_rejected += gate_rejected
            reasons = candidate_row.get("gate_rejection_counts")
            if isinstance(reasons, Mapping):
                aggregate_reasons.update(
                    {str(code): int(str(count)) for code, count in reasons.items()}
                )
        if (
            aggregate_baseline
            != int(str(candidate_gate_diagnostics.get("baseline_qualified_count", -1)))
            or aggregate_accepted
            != int(str(candidate_gate_diagnostics.get("accepted_qualified_count", -1)))
            or aggregate_rejected
            != int(str(candidate_gate_diagnostics.get("rejected_qualified_count", -1)))
            or dict(sorted(aggregate_reasons.items()))
            != candidate_gate_diagnostics.get("rejection_counts")
        ):
            violations.append(f"AGGREGATE_GATE_ACCOUNTING_MISMATCH:{run_id}")

    unique_violations = list(dict.fromkeys(violations))
    strategy_comparisons: dict[str, dict[str, object]] = {}
    if not unique_violations:
        for strategy_id in strategy_ids:
            comparison = compare_strategy_gate_trials(
                baseline,
                _virtual_single_target_candidate(
                    baseline,
                    candidate,
                    target_strategy_id=strategy_id,
                ),
            )
            strategy_comparisons[strategy_id] = comparison
            if comparison["status"] == "FAIL_INTEGRITY":
                comparison_violations = comparison.get("integrity_violations")
                if isinstance(comparison_violations, Sequence) and not isinstance(
                    comparison_violations, str | bytes
                ):
                    unique_violations.extend(
                        f"{strategy_id}:{code}" for code in comparison_violations
                    )
                else:
                    unique_violations.append(
                        f"{strategy_id}:COMPARISON_INTEGRITY_VIOLATIONS_MISSING"
                    )
    decision_counts = Counter(
        str(row.get("decision")) for row in strategy_comparisons.values()
    )
    historical_candidates = [
        strategy_id
        for strategy_id, row in strategy_comparisons.items()
        if row.get("historical_candidate_for_forward_shadow") is True
    ]
    status = "FAIL_INTEGRITY" if unique_violations else "PASS_COMPARISON_COMPLETE"
    return {
        "schema_version": 1,
        "status": status,
        "decision": (
            "INVALID_DO_NOT_USE"
            if unique_violations
            else "ALL_STRATEGIES_COMPARED_FORWARD_EVIDENCE_PENDING"
        ),
        "signal_gate": candidate_gate,
        "signal_gate_target_strategy_id": SIGNAL_GATE_TARGET_ALL,
        "strategy_count": len(strategy_ids),
        "strategy_ids": list(strategy_ids),
        "shared_integrity_violations": unique_violations,
        "same_frozen_input_and_strategy_accounting_passed": not unique_violations,
        "strategy_comparisons": strategy_comparisons,
        "strategy_decision_counts": dict(sorted(decision_counts.items())),
        "historical_candidate_forward_shadow_ids": historical_candidates,
        "survivor_watchlist_selection_required": True,
        "ranking_eligible_strategy_ids": [],
        "promotion_allowed": False,
        "profitability_status": "NOT_PROVEN",
        "independent_forward_live_public_required": True,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
    }


def _load(path: Path) -> Mapping[str, object]:
    return _mapping(json.loads(path.read_text(encoding="utf-8")), path.as_posix())


def _atomic_write(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise FileExistsError(f"기존 일괄 비교 결과를 덮어쓰지 않습니다: {path}")
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
    result = compare_all_strategy_gate_trials(
        _load(arguments.baseline),
        _load(arguments.candidate),
    )
    _atomic_write(arguments.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS_COMPARISON_COMPLETE" else 2)


if __name__ == "__main__":
    main()
