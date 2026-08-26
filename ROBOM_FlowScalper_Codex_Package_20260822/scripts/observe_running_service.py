# 기존 localhost PAPER 서비스만 읽어 30분·6시간·24시간 장시간 증거를 남긴다.
"""별도 시장연결·Run·writer를 만들지 않는 실행 서비스 soak CLI다."""

from __future__ import annotations

import argparse
import json
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


def observe_running_service(arguments: argparse.Namespace) -> dict[str, object]:
    """지정된 실시간 동안 기존 대시보드를 읽고 증거를 요약한다."""

    thresholds = RunningServiceSoakThresholds(
        max_queue_depth=arguments.max_queue_depth,
        max_processing_lag_p95_ms=arguments.max_processing_lag_p95_ms,
        max_trade_lag_p95_ms=arguments.max_trade_lag_p95_ms,
        max_event_stall_seconds=arguments.max_event_stall_seconds,
        max_memory_growth_mb=arguments.max_memory_growth_mb,
        max_persistence_flush_last_ms=arguments.max_persistence_flush_last_ms,
        max_wal_checkpoint_last_ms=arguments.max_wal_checkpoint_last_ms,
    )
    started_at = datetime.now(UTC)
    started_monotonic = time.monotonic()
    samples: list[RunningServiceSample] = []
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
                sample = parse_running_service_sample(
                    payload,
                    elapsed_seconds=elapsed,
                    observed_at=datetime.now(UTC).isoformat(),
                )
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
    result.update(
        {
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
    parser.add_argument("--max-event-stall-seconds", type=float, default=30.0)
    parser.add_argument("--max-memory-growth-mb", type=float, default=256.0)
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
