# 100후보 PAPER 연구 경로의 실제 공개시장 bounded 처리량과 자원을 측정한다.

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.costing import CostProfile
from backend.app.ops import ProcessResourceSampler
from backend.app.research import (
    HORIZON_MAXIMUM_HOLD_MS,
    load_research_instruments,
    preregistered_trials,
)
from scripts.research_strategy_100_candidates import (
    AccountCounters,
    ResearchAccountCarry,
    Strategy100RunExecutor,
    TrialIntegrity,
    _account_key,
    _canonical_json,
    _effective_validation_folds,
    _load_inputs,
    _run_execution_windows,
    _screening_boundaries,
    _validation_folds,
)


def build_benchmark_report(
    *,
    run_id: str,
    maximum_events: int,
    elapsed_seconds: float,
    diagnostics: Mapping[str, object],
    completed_trade_count: int,
    baseline_resources: Mapping[str, object],
    final_resources: Mapping[str, object],
    source_hashes: Mapping[str, object],
    generated_ts_utc: str,
) -> dict[str, Any]:
    """선택·승격에 쓰지 않는 bounded 진단 보고서를 fail-closed로 만든다."""

    if maximum_events <= 0 or elapsed_seconds <= 0:
        raise ValueError("benchmark event 수와 경과시간은 양수여야 합니다.")
    processed_events = int(str(diagnostics.get("event_count", 0)))
    alpha_evaluations = int(str(diagnostics.get("alpha_evaluation_count", 0)))
    registered = len(preregistered_trials())
    executable = sum(trial.screening_eligible for trial in preregistered_trials())
    checks = {
        "exactly_100_preregistered_trials": registered == 100,
        "exactly_90_executable_trials": executable == 90,
        "exactly_180_independent_base_stress_accounts": executable * 2 == 180,
        "bounded_event_limit_respected": 0 < processed_events <= maximum_events,
        "candidate_evaluations_observed": alpha_evaluations > 0,
        "final_oos_not_processed": True,
        "selection_or_promotion_not_performed": True,
        "paper_only": True,
        "real_orders_disabled": True,
        "private_api_disabled": True,
        "runtime_ai_disabled": True,
    }
    status = "PASS" if all(checks.values()) else "INSUFFICIENT_BOUNDED_SAMPLE"
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "generated_ts_utc": generated_ts_utc,
        "scope": "BOUNDED_DIAGNOSTIC_NOT_PROMOTION_EVIDENCE",
        "run_id": run_id,
        "maximum_events": maximum_events,
        "processed_events": processed_events,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "events_per_second": round(processed_events / elapsed_seconds, 3),
        "candidate_evaluation_count": alpha_evaluations,
        "candidate_evaluations_per_second": round(alpha_evaluations / elapsed_seconds, 3),
        "plan_count": int(str(diagnostics.get("plan_count", 0))),
        "completed_trade_count": completed_trade_count,
        "registered_trial_count": registered,
        "executable_trial_count": executable,
        "independent_paper_account_count": executable * 2,
        "baseline_resources": dict(baseline_resources),
        "final_resources": dict(final_resources),
        "queue_model": "SYNCHRONOUS_OFFLINE_NO_QUEUE",
        "maximum_queue_depth": 0,
        "persistence_latency": "NOT_APPLICABLE_STAGE1_IN_MEMORY",
        "dashboard_serialization": "NOT_MEASURED_BY_THIS_BENCHMARK",
        "replay_contention": "NOT_MEASURED_BY_THIS_BENCHMARK",
        "source_hashes": dict(source_hashes),
        "diagnostics": dict(diagnostics),
        "checks": checks,
        "selection_or_promotion_performed": False,
        "final_oos_processed": False,
        "active_count": 0,
        "live_shadow_count": 0,
        "profitability_claim": "NOT_PROVEN",
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "runtime_ai_enabled": False,
    }
    report["manifest_sha256"] = hashlib.sha256(_canonical_json(report).encode()).hexdigest()
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("data/market-parquet-v6/venue=BINANCE_USDM"),
    )
    parser.add_argument(
        "--trial-manifest",
        type=Path,
        default=Path("evidence/STRATEGY_100_TRIAL_MANIFEST.json"),
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=Path("evidence/STRATEGY_100_DATASET_MANIFEST.json"),
    )
    parser.add_argument(
        "--instrument-manifest",
        type=Path,
        default=Path("evidence/STRATEGY_100_INSTRUMENTS.json"),
    )
    parser.add_argument("--run-id")
    parser.add_argument("--maximum-events", type=int, default=200_000)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/STRATEGY_100_RESOURCE_BENCHMARK.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.maximum_events <= 0:
        raise SystemExit("--maximum-events는 양수여야 합니다.")
    _, dataset, instrument_manifest, source_hashes = _load_inputs(
        args.trial_manifest,
        args.dataset_manifest,
        args.instrument_manifest,
    )
    run_rows = dataset.get("runs")
    if not isinstance(run_rows, list):
        raise ValueError("dataset manifest에 Run이 없습니다.")
    train_rows = [
        row
        for row in run_rows
        if isinstance(row, Mapping)
        and row.get("role") == "TRAIN"
        and (args.run_id is None or row.get("run_id") == args.run_id)
    ]
    if len(train_rows) != 1 and args.run_id is not None:
        raise ValueError("지정한 Train Run을 하나만 찾을 수 없습니다.")
    if not train_rows:
        raise ValueError("benchmark에 사용할 Train Run이 없습니다.")
    selected = train_rows[0]
    run_id = str(selected["run_id"])
    trials = preregistered_trials()
    executable = tuple(trial for trial in trials if trial.screening_eligible)
    account_counters = {
        _account_key(trial.trial_id, profile): AccountCounters()
        for trial in executable
        for profile in CostProfile
    }
    integrity = {trial.trial_id: TrialIntegrity() for trial in executable}
    instruments = load_research_instruments(instrument_manifest)
    validation_start_ms, _ = _screening_boundaries(dataset)
    raw_validation_folds = _validation_folds(dataset)
    effective_folds_by_horizon = {
        horizon: _effective_validation_folds(
            raw_validation_folds,
            maximum_holding_ms=maximum_holding_ms,
            purge_embargo_ms=maximum_holding_ms,
        )
        for horizon, maximum_holding_ms in HORIZON_MAXIMUM_HOLD_MS.items()
    }
    executor = Strategy100RunExecutor(
        run_id=run_id,
        split="TRAIN",
        archive_dir=args.archive / f"run={run_id}",
        trials=trials,
        instruments=instruments,
        account_counters=account_counters,
        trial_integrity=integrity,
        execution_windows_by_horizon={
            horizon: _run_execution_windows(
                selected,
                validation_folds=effective_folds_by_horizon[horizon],
                validation_start_ms=validation_start_ms,
                horizon=horizon,
                maximum_holding_ms=maximum_holding_ms,
                purge_embargo_ms=maximum_holding_ms,
            )
            for horizon, maximum_holding_ms in HORIZON_MAXIMUM_HOLD_MS.items()
        },
        account_carry={
            _account_key(trial.trial_id, profile): ResearchAccountCarry()
            for trial in executable
            for profile in CostProfile
        },
    )
    sampler = ProcessResourceSampler(args.archive)
    baseline = sampler.sample()
    started = time.perf_counter()
    trades = executor.execute(maximum_events=args.maximum_events)
    elapsed = time.perf_counter() - started
    final_resources = sampler.sample()
    report = build_benchmark_report(
        run_id=run_id,
        maximum_events=args.maximum_events,
        elapsed_seconds=elapsed,
        diagnostics=asdict(executor.diagnostics),
        completed_trade_count=len(trades),
        baseline_resources=baseline,
        final_resources=final_resources,
        source_hashes=source_hashes,
        generated_ts_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "output": args.output.as_posix(),
                "status": report["status"],
                "processed_events": report["processed_events"],
                "events_per_second": report["events_per_second"],
                "candidate_evaluations_per_second": report["candidate_evaluations_per_second"],
                "manifest_sha256": report["manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
