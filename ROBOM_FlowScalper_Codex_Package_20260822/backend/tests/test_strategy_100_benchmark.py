# 100후보 bounded benchmark가 자원 측정과 PAPER 안전 경계를 보존하는지 검증한다.

from __future__ import annotations

import json

from scripts.benchmark_strategy_100_candidates import build_benchmark_report
from scripts.research_strategy_100_candidates import (
    RunDiagnostics,
    _run_diagnostics_payload,
)


def test_benchmark_report_requires_observed_candidate_evaluations() -> None:
    report = build_benchmark_report(
        run_id="train-run",
        maximum_events=1_000,
        elapsed_seconds=2.0,
        diagnostics={
            "event_count": 1_000,
            "alpha_evaluation_count": 9_000,
            "plan_count": 20,
        },
        completed_trade_count=3,
        baseline_resources={"process_memory_mb": 100},
        final_resources={"process_memory_mb": 110, "process_cpu_percent": 50},
        source_hashes={"dataset_manifest_sha256": "a" * 64},
        generated_ts_utc="2026-08-28T00:00:00Z",
        input_binding={
            "explicit_archive_file_count": 100,
            "warmup_candle_count": 20_000,
            "warmup_symbol_count": 24,
        },
    )

    assert report["status"] == "PASS"
    assert report["events_per_second"] == 500
    assert report["candidate_evaluations_per_second"] == 4_500
    assert report["registered_trial_count"] == 100
    assert report["independent_paper_account_count"] == 180
    assert report["active_count"] == 0
    assert report["final_oos_processed"] is False
    assert report["real_orders_enabled"] is False
    assert report["checks"]["frozen_explicit_archive_files_bound"] is True
    assert report["checks"]["completed_public_warmup_bound"] is True

    insufficient = build_benchmark_report(
        run_id="train-run",
        maximum_events=1_000,
        elapsed_seconds=2.0,
        diagnostics={"event_count": 1_000, "alpha_evaluation_count": 0},
        completed_trade_count=0,
        baseline_resources={},
        final_resources={},
        source_hashes={},
        generated_ts_utc="2026-08-28T00:00:00Z",
    )
    assert insufficient["status"] == "INSUFFICIENT_BOUNDED_SAMPLE"
    assert insufficient["checks"]["candidate_evaluations_observed"] is False


def test_benchmark_report_serializes_real_counter_diagnostics() -> None:
    diagnostics = RunDiagnostics(run_id="train-run", split="TRAIN")
    diagnostics.event_count = 100
    diagnostics.alpha_evaluation_count = 900
    diagnostics.audit_counts["ENTRY_REJECTED"] = 2

    report = build_benchmark_report(
        run_id="train-run",
        maximum_events=100,
        elapsed_seconds=1.0,
        diagnostics=_run_diagnostics_payload(diagnostics),
        completed_trade_count=0,
        baseline_resources={},
        final_resources={},
        source_hashes={},
        generated_ts_utc="2026-08-30T00:00:00Z",
    )

    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)

    assert '"ENTRY_REJECTED": 2' in rendered
