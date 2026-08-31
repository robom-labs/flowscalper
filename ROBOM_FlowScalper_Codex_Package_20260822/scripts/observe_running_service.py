# 기존 localhost PAPER 서비스만 읽어 30분·6시간·24시간 장시간 증거를 남긴다.
"""별도 시장연결·Run·writer를 만들지 않는 실행 서비스 soak CLI다."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from backend.app.ops.service_soak import (
    RunningServiceSample,
    RunningServiceSoakThresholds,
    parse_running_service_sample,
    summarize_running_service_soak,
)
from backend.app.storage.integrity import (
    RuntimeSafetyViolation,
    fetch_dashboard_payload,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source_revision() -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "status", "--porcelain", "--", "."],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return commit, not status


def observe_running_service(arguments: argparse.Namespace) -> dict[str, object]:
    """지정된 실시간 동안 기존 대시보드를 읽고 증거를 요약한다."""

    thresholds = RunningServiceSoakThresholds(
        max_queue_depth=arguments.max_queue_depth,
        max_processing_lag_p95_ms=arguments.max_processing_lag_p95_ms,
        max_trade_lag_p95_ms=arguments.max_trade_lag_p95_ms,
        max_event_loop_lag_ms=arguments.max_event_loop_lag_ms,
        max_event_stall_seconds=arguments.max_event_stall_seconds,
        max_memory_growth_mb=arguments.max_memory_growth_mb,
        max_market_persistence_buffer=arguments.max_market_persistence_buffer,
        max_persistence_flush_last_ms=arguments.max_persistence_flush_last_ms,
        max_wal_checkpoint_last_ms=arguments.max_wal_checkpoint_last_ms,
    )
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    source_commit, source_worktree_clean_at_start = _source_revision()
    samples: list[RunningServiceSample] = []
    observed_release_commits: set[str] = set()
    observed_release_isolation: set[bool] = set()
    observed_strategy_id_sets: set[tuple[str, ...]] = set()
    observed_account_id_sets: set[tuple[str, ...]] = set()
    observed_mode_counts: set[tuple[int, int, int]] = set()
    probe_error_count = 0
    consecutive_probe_errors = 0
    maximum_consecutive_probe_errors = 0
    probe_error_examples: list[str] = []
    operator_aborted = False
    fatal_probe_error: str | None = None
    dashboard_url = arguments.runtime_url.rstrip("/") + "/api/dashboard"
    try:
        while True:
            elapsed = time.monotonic() - started_monotonic
            try:
                payload = fetch_dashboard_payload(
                    dashboard_url,
                    timeout_seconds=arguments.request_timeout_seconds,
                )
                system = payload.get("system")
                if not isinstance(system, dict):
                    raise ValueError("system payload가 object가 아닙니다.")
                release_commit = system.get("release_commit")
                release_isolated = system.get("release_isolated")
                if not isinstance(release_commit, str) or not release_commit:
                    raise ValueError("system.release_commit이 없습니다.")
                if not isinstance(release_isolated, bool):
                    raise ValueError("system.release_isolated가 boolean이 아닙니다.")
                observed_release_commits.add(release_commit)
                observed_release_isolation.add(release_isolated)
                sample = parse_running_service_sample(
                    payload,
                    elapsed_seconds=elapsed,
                    observed_at=datetime.now(UTC).isoformat(),
                )
                strategy_ids = tuple(
                    sorted(state.strategy_id for state in sample.strategy_states)
                )
                league_accounts = payload.get("league_accounts")
                if not isinstance(league_accounts, list):
                    raise ValueError("league_accounts payload가 배열이 아닙니다.")
                account_ids = tuple(
                    sorted(
                        str(row["account_id"])
                        for row in league_accounts
                        if isinstance(row, dict) and row.get("account_id") is not None
                    )
                )
                if len(account_ids) != len(league_accounts):
                    raise ValueError("league account_id 범위를 완전하게 읽지 못했습니다.")
                mode_counts = tuple(
                    sum(state.mode == mode for state in sample.strategy_states)
                    for mode in ("ACTIVE", "SHADOW", "OFF")
                )
                observed_strategy_id_sets.add(strategy_ids)
                observed_account_id_sets.add(account_ids)
                observed_mode_counts.add(mode_counts)
            except (OSError, ValueError, RuntimeSafetyViolation) as error:
                probe_error_count += 1
                consecutive_probe_errors += 1
                maximum_consecutive_probe_errors = max(
                    maximum_consecutive_probe_errors,
                    consecutive_probe_errors,
                )
                detail = f"{type(error).__name__}: {error}"
                if len(probe_error_examples) < 10:
                    probe_error_examples.append(detail)
                if consecutive_probe_errors >= arguments.max_consecutive_probe_errors:
                    fatal_probe_error = detail
                    break
            else:
                consecutive_probe_errors = 0
                samples.append(sample)
            if elapsed >= arguments.duration_seconds:
                break
            time.sleep(
                max(
                    0.0,
                    min(
                        arguments.sample_seconds,
                        arguments.duration_seconds - elapsed,
                    ),
                )
            )
    except KeyboardInterrupt:
        operator_aborted = True
    result = summarize_running_service_soak(
        samples,
        requested_duration_seconds=arguments.duration_seconds,
        thresholds=thresholds,
        probe_error_count=probe_error_count,
        maximum_consecutive_probe_errors=maximum_consecutive_probe_errors,
        max_consecutive_probe_errors=arguments.max_consecutive_probe_errors,
        operator_aborted=operator_aborted,
    )
    if fatal_probe_error is not None:
        result["status"] = "FAIL"
        failures = result.setdefault("failures", [])
        if isinstance(failures, list) and "PROBE_FAILED_CONSECUTIVELY" not in failures:
            failures.append("PROBE_FAILED_CONSECUTIVELY")
    completed_at = datetime.now(UTC)
    source_commit_at_end, source_worktree_clean_at_end = _source_revision()
    release_commit = (
        next(iter(observed_release_commits))
        if len(observed_release_commits) == 1
        else None
    )
    provenance_checks = {
        "source_worktree_clean_at_start": source_worktree_clean_at_start,
        "source_worktree_clean_at_end": source_worktree_clean_at_end,
        "source_commit_stable": source_commit_at_end == source_commit,
        "release_commit_stable": len(observed_release_commits) == 1,
        "release_isolated_throughout": observed_release_isolation == {True},
        "source_release_commit_match": release_commit == source_commit,
        "strategy_ids_stable_and_observed": len(observed_strategy_id_sets) == 1,
        "league_account_ids_stable_and_observed": len(observed_account_id_sets) == 1,
        "strategy_mode_counts_stable_and_observed": len(observed_mode_counts) == 1,
    }
    checks = result.setdefault("checks", {})
    if isinstance(checks, dict):
        checks.update(provenance_checks)
    failed_provenance = [name for name, passed in provenance_checks.items() if not passed]
    if failed_provenance and result.get("status") == "PASS":
        result["status"] = "FAIL"
        failures = result.setdefault("failures", [])
        if isinstance(failures, list):
            failures.extend(
                name for name in failed_provenance if name not in failures
            )
    result.update(
        {
            "schema_version": 1,
            "generated_ts_utc": completed_at.isoformat().replace("+00:00", "Z"),
            "source_commit": source_commit,
            "source_commit_at_end": source_commit_at_end,
            "source_worktree_clean_at_start": source_worktree_clean_at_start,
            "source_worktree_clean_at_end": source_worktree_clean_at_end,
            "source_worktree_clean_at_measurement": (
                source_worktree_clean_at_start
                and source_worktree_clean_at_end
                and source_commit_at_end == source_commit
            ),
            "release_commit": release_commit,
            "release_commits_observed": sorted(observed_release_commits),
            "release_isolated_throughout": observed_release_isolation == {True},
            "strategy_ids": (
                list(next(iter(observed_strategy_id_sets)))
                if len(observed_strategy_id_sets) == 1
                else []
            ),
            "league_account_ids": (
                list(next(iter(observed_account_id_sets)))
                if len(observed_account_id_sets) == 1
                else []
            ),
            "strategy_mode_counts": (
                dict(
                    zip(
                        ("ACTIVE", "SHADOW", "OFF"),
                        next(iter(observed_mode_counts)),
                        strict=True,
                    )
                )
                if len(observed_mode_counts) == 1
                else {}
            ),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "wall_duration_seconds": round(
                (completed_at - started_at).total_seconds(),
                3,
            ),
            "runtime_url": arguments.runtime_url,
            "sample_seconds": arguments.sample_seconds,
            "request_timeout_seconds": arguments.request_timeout_seconds,
            "max_consecutive_probe_errors": arguments.max_consecutive_probe_errors,
            "probe_error_examples": probe_error_examples,
            "fatal_probe_error": fatal_probe_error,
        }
    )
    return result


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "기존 localhost PAPER 서비스를 재시작하거나 시장연결을 "
            "추가하지 않고 장시간 관찰합니다."
        )
    )
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--sample-seconds", type=float, default=10.0)
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8870")
    parser.add_argument("--request-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--max-consecutive-probe-errors", type=int, default=3)
    parser.add_argument("--max-queue-depth", type=int, default=64)
    parser.add_argument("--max-processing-lag-p95-ms", type=float, default=500.0)
    parser.add_argument("--max-trade-lag-p95-ms", type=float, default=1_000.0)
    parser.add_argument("--max-event-loop-lag-ms", type=float, default=500.0)
    parser.add_argument("--max-event-stall-seconds", type=float, default=30.0)
    parser.add_argument("--max-memory-growth-mb", type=float, default=256.0)
    parser.add_argument("--max-market-persistence-buffer", type=int, default=10_000)
    parser.add_argument("--max-persistence-flush-last-ms", type=float, default=20_000.0)
    parser.add_argument("--max-wal-checkpoint-last-ms", type=float, default=30_000.0)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if (
        arguments.duration_seconds <= 0
        or arguments.sample_seconds <= 0
        or arguments.request_timeout_seconds <= 0
        or arguments.max_consecutive_probe_errors <= 0
    ):
        parser.error("시간·간격·요청상한·연속 오류 상한은 양수여야 합니다.")
    return arguments


def main() -> None:
    arguments = parse_arguments()
    result = observe_running_service(arguments)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "samples"},
            ensure_ascii=False,
            indent=2,
        )
    )
    if result["status"] == "PASS":
        return
    if result["status"] == "ABORTED_OPERATOR":
        raise SystemExit(130)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
