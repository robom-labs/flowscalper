# 100후보 bounded benchmark가 자원 측정과 PAPER 안전 경계를 보존하는지 검증한다.

from __future__ import annotations

from scripts.benchmark_strategy_100_candidates import build_benchmark_report


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
    )

    assert report["status"] == "PASS"
    assert report["events_per_second"] == 500
    assert report["candidate_evaluations_per_second"] == 4_500
    assert report["registered_trial_count"] == 100
    assert report["independent_paper_account_count"] == 180
    assert report["active_count"] == 0
    assert report["final_oos_processed"] is False
    assert report["real_orders_enabled"] is False

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
