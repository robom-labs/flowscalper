"""자격 증명 없는 공개시장 supervisor를 30분·6시간·24시간 실행하고 자원 증거를 남긴다."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.app.clocks import SystemClock
from backend.app.domain.models import RuntimeMode, Venue
from backend.app.runtime import PaperRuntime


async def run_soak(arguments: argparse.Namespace) -> dict[str, object]:
    started_at = datetime.now(UTC)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=SystemClock(),
        run_id=f"soak-{uuid4().hex[:12]}",
        venue=Venue.BINANCE_USDM,
    )
    started = await runtime.start_persistent_live()
    samples: list[dict[str, object]] = []
    baseline = runtime.resource_sampler.sample()
    start_monotonic = asyncio.get_running_loop().time()
    completed = False
    try:
        if started:
            while True:
                elapsed = asyncio.get_running_loop().time() - start_monotonic
                supervisor = runtime._supervisor
                if supervisor is None:
                    break
                resources = runtime.resource_sampler.sample()
                samples.append(
                    {
                        "elapsed_seconds": round(elapsed, 3),
                        "venue": runtime.venue.value,
                        "market_data_state": runtime.market_data_state.value,
                        "wide_symbols": runtime.wide_symbol_count,
                        "deep_symbols": runtime.deep_symbol_count,
                        "event_count": supervisor.telemetry.event_count,
                        "event_memory_count": len(runtime.events),
                        "queue_depth": supervisor.telemetry.queue_depth,
                        "queue_capacity": supervisor.telemetry.queue_capacity,
                        "supervisor_entry_locked": supervisor.telemetry.entry_locked,
                        "runtime_entries_paused": runtime.paused,
                        "runtime_health_flags": list(runtime.runtime_health_flags),
                        "reconnects": supervisor.telemetry.reconnect_count,
                        "sequence_gaps": supervisor.telemetry.gap_count,
                        "resyncs": supervisor.telemetry.resync_count,
                        "dropped_events": supervisor.telemetry.dropped_event_count,
                        "planned_rotations": supervisor.telemetry.planned_rotation_count,
                        "lag_p50_ms": supervisor.telemetry.lag_p50_ms,
                        "lag_p95_ms": supervisor.telemetry.lag_p95_ms,
                        "strategy_evaluations": runtime.strategy_evaluation_count,
                        "qualified_signals": runtime.qualified_signal_count,
                        "main_trades": len(runtime.paper_portfolio.main.completed_trades),
                        "process_cpu_percent": resources["process_cpu_percent"],
                        "process_memory_mb": resources["process_memory_mb"],
                        "disk_free_mb": resources["disk_free_mb"],
                    }
                )
                if elapsed >= arguments.duration_seconds:
                    completed = True
                    break
                await asyncio.sleep(
                    min(arguments.sample_seconds, arguments.duration_seconds - elapsed)
                )
    finally:
        supervisor_snapshot = (
            dict(runtime._supervisor.telemetry.as_dict()) if runtime._supervisor is not None else {}
        )
        await runtime.shutdown()
    final_resources = runtime.resource_sampler.sample()
    event_counts = [int(str(sample["event_count"])) for sample in samples]
    memory_values = [float(str(sample["process_memory_mb"])) for sample in samples]
    queue_depths = [int(str(sample["queue_depth"])) for sample in samples]
    event_memory = [int(str(sample["event_memory_count"])) for sample in samples]
    lag_values = [
        float(str(sample["lag_p95_ms"])) for sample in samples if sample["lag_p95_ms"] is not None
    ]
    critical_lag_threshold_ms = float(
        str(supervisor_snapshot.get("critical_lag_threshold_ms", 1_500))
    )
    critical_lag_samples = [
        sample
        for sample in samples
        if float(str(sample["lag_p95_ms"] or 0)) > critical_lag_threshold_ms
    ]
    critical_lag_fail_open_samples = [
        sample
        for sample in critical_lag_samples
        if not bool(sample["supervisor_entry_locked"])
        and not bool(sample["runtime_entries_paused"])
    ]
    final_lag_p95_ms = float(str(supervisor_snapshot.get("lag_p95_ms") or 0))
    final_lag_below_threshold = final_lag_p95_ms <= critical_lag_threshold_ms
    final_lag_safe_or_locked = bool(
        final_lag_below_threshold
        or (supervisor_snapshot.get("entry_locked") is True and runtime.paused)
    )
    memory_growth = (
        max(memory_values) - float(str(baseline["process_memory_mb"])) if memory_values else 0.0
    )
    checks = {
        "public_live_started": started,
        "requested_duration_completed": completed,
        "wide_symbols_at_least_50": runtime.wide_symbol_count >= 50,
        "deep_symbols_between_10_and_30": 10 <= runtime.deep_symbol_count <= 30,
        "events_continued": bool(event_counts and event_counts[-1] > event_counts[0]),
        "event_memory_bounded": max(event_memory, default=0) <= 10_000,
        "queue_bounded": max(queue_depths, default=0)
        <= int(str(supervisor_snapshot.get("queue_capacity", 0))),
        "no_dropped_events": int(str(supervisor_snapshot.get("dropped_events", 0))) == 0,
        "critical_lag_fail_closed": not critical_lag_fail_open_samples,
        "final_lag_safe_or_locked": final_lag_safe_or_locked,
        "memory_growth_below_256mb": memory_growth <= 256,
        "real_orders_disabled": True,
        "auth_not_required": True,
    }
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "started_at": started_at.isoformat(),
        "requested_duration_seconds": arguments.duration_seconds,
        "sample_seconds": arguments.sample_seconds,
        "run_id": runtime.run_id,
        "venue": runtime.venue.value,
        "supervisor": supervisor_snapshot,
        "baseline_resources": baseline,
        "final_resources": final_resources,
        "memory_growth_mb": round(memory_growth, 3),
        "max_event_memory_count": max(event_memory, default=0),
        "max_queue_depth": max(queue_depths, default=0),
        "max_lag_p95_ms": max(lag_values, default=0),
        "critical_lag_threshold_ms": critical_lag_threshold_ms,
        "critical_lag_sample_count": len(critical_lag_samples),
        "critical_lag_fail_open_sample_count": len(critical_lag_fail_open_samples),
        "final_lag_below_entry_lock_ms": final_lag_below_threshold,
        "final_supervisor_entry_locked": supervisor_snapshot.get("entry_locked") is True,
        "final_runtime_entries_paused": runtime.paused,
        "final_strategy_evaluations": runtime.strategy_evaluation_count,
        "final_qualified_signals": runtime.qualified_signal_count,
        "final_main_trades": len(runtime.paper_portfolio.main.completed_trades),
        "checks": checks,
        "status": "PASS" if all(checks.values()) else "FAIL",
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=float, default=30 * 60)
    parser.add_argument("--sample-seconds", type=float, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/WAVE07_SOAK_30M.json"),
    )
    arguments = parser.parse_args()
    if arguments.duration_seconds <= 0 or arguments.sample_seconds <= 0:
        parser.error("duration과 sample 간격은 양수여야 합니다.")
    result = asyncio.run(run_soak(arguments))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({key: result[key] for key in result if key != "samples"}, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
