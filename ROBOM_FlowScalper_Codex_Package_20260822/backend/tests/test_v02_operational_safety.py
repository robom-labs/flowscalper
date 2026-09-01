"""CPU·메모리·디스크 진단과 저장 실패 신규진입 차단을 실제 런타임에 검증한다."""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

from anyio import BrokenWorkerProcess

import backend.app.ops.resources as resources_module
import backend.app.runtime as runtime_module
import backend.app.storage.parquet as parquet_module
import backend.app.storage.sqlite as sqlite_module
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketDataState, MarketEvent, RuntimeMode, Venue
from backend.app.market_data.candles import Candle
from backend.app.market_data.supervisor import SupervisorTelemetry
from backend.app.ops import ProcessResourceSampler
from backend.app.runtime import PaperRuntime
from backend.app.storage.parquet import DiskUsage, ParquetEventStore, StoragePressureError
from backend.app.storage.sqlite import SQLiteLedger
from backend.app.strategies.process_evaluator import (
    ProcessStrategyEvaluator,
    StrategyEvaluationRequest,
    StrategyEvaluationResult,
)
from backend.tests.test_candidate_paper_portfolio import candidate_plan
from backend.tests.test_storage_replay_analytics import _sample_trade
from scripts.soak_live import maximum_observed_growth


def _slow_strategy_process_worker(
    request: StrategyEvaluationRequest,
) -> StrategyEvaluationResult:
    """실제 spawn 프로세스의 비동기 평가 경계를 검증한다."""

    assert request.snapshot.symbol == "BTCUSDT"
    time.sleep(0.25)
    return StrategyEvaluationResult(signals=(), condition_rows=())


class _AsyncFakeProcessEvaluator:
    """취소·단일 순서·중간 잠금을 결정적으로 제어하는 테스트 프로세스 경계다."""

    def __init__(
        self,
        *,
        delay_seconds: float = 0.0,
        release: asyncio.Event | None = None,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.release = release
        self.started = asyncio.Event()
        self.completed_symbols: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def request(**kwargs) -> StrategyEvaluationRequest:
        return StrategyEvaluationRequest.from_registry(**kwargs)

    async def evaluate_to_completion(
        self,
        request: StrategyEvaluationRequest,
    ) -> tuple[StrategyEvaluationResult, asyncio.CancelledError | None]:
        cancellation: asyncio.CancelledError | None = None
        async with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self.started.set()

            async def finish() -> StrategyEvaluationResult:
                if self.release is not None:
                    await self.release.wait()
                elif self.delay_seconds:
                    await asyncio.sleep(self.delay_seconds)
                self.completed_symbols.append(request.snapshot.symbol)
                return StrategyEvaluationResult(signals=(), condition_rows=())

            worker = asyncio.create_task(finish())
            try:
                while True:
                    try:
                        return await asyncio.shield(worker), cancellation
                    except asyncio.CancelledError as error:
                        cancellation = error
            finally:
                self.in_flight -= 1

    @staticmethod
    def accept_result(
        _request: StrategyEvaluationRequest,
        _result: StrategyEvaluationResult,
    ) -> bool:
        return True

    async def aclose(self) -> None:
        if self.release is not None:
            self.release.set()


def test_process_resource_sampler_reports_actual_cpu_memory_and_disk(tmp_path: Path) -> None:
    sampler = ProcessResourceSampler(tmp_path)
    first = sampler.sample()
    sum(index * index for index in range(20_000))
    second = sampler.sample()

    assert float(str(first["process_memory_mb"])) > 0
    assert first["process_memory_source"] in {
        "CURRENT_RSS_LIBPROC",
        "CURRENT_RSS_PROCFS",
        "CURRENT_WORKING_SET",
    }
    assert float(str(first["process_memory_peak_mb"])) >= float(str(first["process_memory_mb"]))
    assert str(first["process_memory_peak_source"]).startswith("PEAK_")
    assert float(str(second["process_cpu_percent"])) >= 0
    assert int(str(second["process_threads"])) >= 1
    assert float(str(second["disk_total_mb"])) > 0
    assert float(str(second["disk_free_mb"])) > 0
    assert 0 <= float(str(second["disk_free_ratio"])) <= 1


def test_process_resource_sampler_reuses_cached_disk_usage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[Path] = []

    def disk_usage(path: Path) -> DiskUsage:
        calls.append(path)
        return DiskUsage(total=1_000, used=250, free=750)

    monkeypatch.setattr(resources_module.shutil, "disk_usage", disk_usage)
    sampler = ProcessResourceSampler(tmp_path)

    first = sampler.sample()
    second = sampler.sample()

    assert calls == [tmp_path]
    assert first["disk_free_ratio"] == 0.75
    assert second["disk_free_ratio"] == 0.75

    sampler.refresh_storage_usage()
    assert calls == [tmp_path, tmp_path]


def test_current_memory_does_not_relabel_peak_rss_as_current(monkeypatch) -> None:
    current_bytes = 128 * 1024**2
    monkeypatch.setattr(resources_module.sys, "platform", "darwin")
    monkeypatch.setattr(resources_module, "_darwin_current_rss_bytes", lambda: current_bytes)

    measured_bytes, source = resources_module._process_memory_bytes()

    assert measured_bytes == current_bytes
    assert source == "CURRENT_RSS_LIBPROC"
    assert source != "PEAK_MAX_RSS"


def test_current_memory_fallback_is_labeled_as_peak(monkeypatch) -> None:
    peak_bytes = 192 * 1024**2
    monkeypatch.setattr(resources_module.sys, "platform", "darwin")
    monkeypatch.setattr(resources_module, "_darwin_current_rss_bytes", lambda: 0)
    monkeypatch.setattr(
        resources_module,
        "_peak_process_memory_bytes",
        lambda: (peak_bytes, "PEAK_MAX_RSS"),
    )

    measured_bytes, source = resources_module._process_memory_bytes()

    assert measured_bytes == peak_bytes
    assert source == "PEAK_MAX_RSS_FALLBACK"


def test_resource_sampler_never_reports_peak_below_current(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        resources_module,
        "_process_memory_bytes",
        lambda: (219 * 1024**2, "CURRENT_RSS_PROCFS"),
    )
    monkeypatch.setattr(
        resources_module,
        "_peak_process_memory_bytes",
        lambda: (218 * 1024**2, "PEAK_MAX_RSS"),
    )

    sample = ProcessResourceSampler(tmp_path).sample()

    assert sample["process_memory_mb"] == 219.0
    assert sample["process_memory_peak_mb"] == 219.0
    assert sample["process_memory_peak_source"] == "PEAK_MAX_RSS_FLOORED_BY_CURRENT"


def test_soak_current_and_peak_memory_growth_remain_independent() -> None:
    current_growth = maximum_observed_growth([96.0, 94.0, 95.0], 100.0)
    peak_growth = maximum_observed_growth([162.0, 170.0, 170.0], 150.0)

    assert current_growth == 0.0
    assert peak_growth == 20.0


def test_archive_worker_applies_macos_background_io_policy_once(monkeypatch) -> None:
    calls: list[list[str]] = []

    def record_policy(command, **options):
        calls.append(command)
        assert options == {
            "check": False,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(parquet_module, "_BACKGROUND_IO_POLICY_APPLIED", False)
    monkeypatch.setattr(parquet_module.sys, "platform", "darwin")
    monkeypatch.setattr(parquet_module.Path, "is_file", lambda _path: True)
    monkeypatch.setattr(parquet_module.subprocess, "run", record_policy)

    parquet_module._apply_background_io_policy()
    parquet_module._apply_background_io_policy()

    assert calls == [["/usr/sbin/taskpolicy", "-b", "-p", str(parquet_module.os.getpid())]]
    assert parquet_module._set_background_io_policy(False) is True
    assert parquet_module._set_background_io_policy(True) is True
    assert calls == [
        ["/usr/sbin/taskpolicy", "-b", "-p", str(parquet_module.os.getpid())],
        ["/usr/sbin/taskpolicy", "-B", "-p", str(parquet_module.os.getpid())],
        ["/usr/sbin/taskpolicy", "-b", "-p", str(parquet_module.os.getpid())],
    ]


def test_atomic_sqlite_commit_temporarily_leaves_background_priority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[object] = []
    archive = ParquetEventStore(
        tmp_path / "priority-archive",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(tmp_path / "priority-ledger.sqlite3")
    ledger.start_run(
        "run-priority",
        mode=RuntimeMode.LIVE_SHADOW_PAPER.value,
        venue=Venue.BINANCE_USDM.value,
        config={"execution": "PAPER"},
        started_ts_ms=1_000,
    )
    rows = [
        {
            "event_id": "priority-event",
            "run_id": "run-priority",
            "venue": Venue.BINANCE_USDM.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "receive_monotonic_ns": 1_000,
            "data": {"price": "100", "quantity": "1"},
        }
    ]

    monkeypatch.setattr(
        sqlite_module,
        "_apply_persistence_background_io_policy",
        lambda: calls.append("ARCHIVE_BACKGROUND"),
    )
    monkeypatch.setattr(
        sqlite_module,
        "_set_persistence_background_io_policy",
        lambda enabled: calls.append(enabled) or True,
    )

    timings = sqlite_module.persist_archives_and_candles_in_process(
        str(archive.root),
        0,
        0,
        str(ledger.path),
        [rows],
        [],
    )

    assert calls == ["ARCHIVE_BACKGROUND", False, True]
    assert timings["archive_batches"] == 1
    assert ledger.count("market_event_archives") == 1
    ledger.close()


def test_storage_worker_keeps_low_cpu_priority_without_accumulating(
    monkeypatch,
) -> None:
    current_niceness = 0
    adjustments: list[int] = []

    def adjust_niceness(increment: int) -> int:
        nonlocal current_niceness
        adjustments.append(increment)
        current_niceness = min(19, current_niceness + increment)
        return current_niceness

    monkeypatch.setattr(sqlite_module.os, "nice", adjust_niceness)

    sqlite_module._apply_storage_worker_cpu_priority()
    sqlite_module._apply_storage_worker_cpu_priority()

    assert adjustments == [0, 10, 0]
    assert current_niceness == 10


def test_archive_worker_warms_arrow_and_zstd_without_disk_write(monkeypatch) -> None:
    warmed = 0

    def record_policy() -> None:
        nonlocal warmed
        warmed += 1

    monkeypatch.setattr(parquet_module, "_apply_background_io_policy", record_policy)

    assert parquet_module.warm_market_event_worker_process() == parquet_module.os.getpid()
    assert warmed == 1


async def test_live_runtime_prewarms_archive_worker_once(tmp_path: Path, monkeypatch) -> None:
    archive = ParquetEventStore(
        tmp_path / "warm-market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-warm-worker",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        market_event_archive=archive,
    )
    calls: list[object] = []

    async def run_sync(function, *arguments):
        calls.append(function)
        assert arguments == ()
        return 123

    monkeypatch.setattr(runtime_module.to_process, "run_sync", run_sync)

    assert await runtime._warm_market_archive_worker() is True
    assert await runtime._warm_market_archive_worker() is True
    assert calls == [parquet_module.warm_market_event_worker_process]
    assert runtime._persistence_worker_warmed is True
    assert runtime._persistence_worker_warm_ms >= 0


async def test_live_sink_defers_changed_execution_persistence_to_worker(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-threaded-execution-ledger",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    event = MarketEvent(
        event_id="depth-threaded-ledger",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="DEPTH_UPDATE",
        venue_ts_ms=1_000,
        receive_monotonic_ns=1_000,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
    )
    calls: list[tuple[str, object]] = []

    def ingest(
        self: PaperRuntime,
        received: MarketEvent,
        *,
        defer_execution_persistence: bool = False,
    ) -> None:
        calls.append(("ingest", defer_execution_persistence))
        self.paper_portfolio.audit_events.append(
            {
                "run_id": self.run_id,
                "ts_ms": received.venue_ts_ms,
                "event": "LEAGUE_RISK_REJECTED",
            }
        )

    async def run_sync(function, *arguments):
        calls.append(("thread", arguments))
        return function(*arguments)

    monkeypatch.setattr(PaperRuntime, "ingest_live_event", ingest)
    monkeypatch.setattr(runtime_module.to_thread, "run_sync", run_sync)
    monkeypatch.setattr(PaperRuntime, "_persist_execution_state_safely", lambda *_: True)

    await runtime.ingest_live_event_async(event)

    assert calls == [("ingest", True), ("thread", (1_000,))]
    assert runtime._live_event_processing_count == 1
    assert runtime._live_event_processing_last_ms >= 0
    assert runtime._live_event_processing_max_event_type == "DEPTH_UPDATE"
    assert runtime._live_event_processing_max_symbol == "BTCUSDT"


async def test_live_strategy_evaluation_does_not_block_event_loop(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-threaded-strategy-evaluation",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    event = MarketEvent(
        event_id="depth-threaded-strategy-evaluation",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="DEPTH_UPDATE",
        venue_ts_ms=1_000,
        receive_monotonic_ns=1_000,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
    )
    stop_ticker = asyncio.Event()
    ticks_during_evaluation = 0

    async def ticker() -> None:
        nonlocal ticks_during_evaluation
        while not stop_ticker.is_set():
            await asyncio.sleep(0.005)
            ticks_during_evaluation += 1

    evaluator = ProcessStrategyEvaluator(worker_function=_slow_strategy_process_worker)
    runtime._live_strategy_evaluator = evaluator

    ticker_task: asyncio.Task[None] | None = None
    try:
        process_id = await evaluator.warm(runtime._strategy_process_state_key())
        assert process_id > 0
        ticker_task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        await runtime.ingest_live_event_async(event)
    finally:
        stop_ticker.set()
        if ticker_task is not None:
            await ticker_task
        await evaluator.aclose()

    assert ticks_during_evaluation >= 5


async def test_cancelled_live_evaluation_drains_single_worker_before_rethrow(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-cancelled-strategy-evaluation",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )

    def depth(symbol: str, ts_ms: int) -> MarketEvent:
        return MarketEvent(
            event_id=f"depth-cancelled-{symbol.lower()}",
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol=symbol,
            event_type="DEPTH_UPDATE",
            venue_ts_ms=ts_ms,
            receive_monotonic_ns=ts_ms,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
        )

    applied_events: list[str] = []
    lifecycle: list[str] = []
    evaluator = _AsyncFakeProcessEvaluator(delay_seconds=0.15)
    runtime._live_strategy_evaluator = evaluator  # type: ignore[assignment]

    def record_completion(
        _self: PaperRuntime,
        prepared,
        _signals,
        *,
        persist_execution: bool,
        process_request,
        process_result,
    ) -> None:
        assert persist_execution is False
        assert isinstance(process_request, StrategyEvaluationRequest)
        assert isinstance(process_result, StrategyEvaluationResult)
        applied_events.append(prepared.event.event_id)

    def record_persistence(_self: PaperRuntime, ts_ms: int) -> bool:
        lifecycle.append(f"persist:{ts_ms}")
        return True

    monkeypatch.setattr(PaperRuntime, "_complete_strategy_evaluation", record_completion)
    monkeypatch.setattr(PaperRuntime, "_has_unpersisted_execution_state", lambda _self: True)
    monkeypatch.setattr(PaperRuntime, "_persist_execution_state_safely", record_persistence)

    first = asyncio.create_task(runtime.ingest_live_event_async(depth("BTCUSDT", 1_000)))
    first.add_done_callback(lambda _task: lifecycle.append("cancelled_rethrown"))
    await asyncio.wait_for(evaluator.started.wait(), timeout=3)
    second = asyncio.create_task(runtime.ingest_live_event_async(depth("ETHUSDT", 2_000)))
    first.cancel()

    first_result, second_result = await asyncio.gather(
        first,
        second,
        return_exceptions=True,
    )

    assert isinstance(first_result, asyncio.CancelledError)
    assert second_result is None
    assert evaluator.completed_symbols == ["BTCUSDT", "ETHUSDT"]
    assert evaluator.max_in_flight == 1
    assert applied_events == ["depth-cancelled-ethusdt"]
    assert lifecycle.index("persist:1000") < lifecycle.index("cancelled_rethrown")


async def test_supervisor_lock_during_evaluation_pauses_completed_offer(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-mid-evaluation-supervisor-lock",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    event = MarketEvent(
        event_id="depth-mid-evaluation-supervisor-lock",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="DEPTH_UPDATE",
        venue_ts_ms=1_000,
        receive_monotonic_ns=1_000,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
    )
    release_evaluation = asyncio.Event()
    observed_entry_locks: list[bool] = []

    class SupervisorStub:
        selection = None

        def __init__(self) -> None:
            self.telemetry = SupervisorTelemetry(
                entry_locked=False,
                consumer_running=True,
            )

        @staticmethod
        def running() -> bool:
            return True

    def record_offer(_self, _plans, *, entries_paused: bool) -> None:
        observed_entry_locks.append(entries_paused)

    supervisor = SupervisorStub()
    runtime._supervisor = supervisor  # type: ignore[assignment]
    runtime.paused = False
    evaluator = _AsyncFakeProcessEvaluator(release=release_evaluation)
    runtime._live_strategy_evaluator = evaluator  # type: ignore[assignment]
    monkeypatch.setattr(type(runtime.paper_portfolio), "offer", record_offer)

    ingest_task = asyncio.create_task(runtime.ingest_live_event_async(event))
    await asyncio.wait_for(evaluator.started.wait(), timeout=3)
    supervisor.telemetry.entry_locked = True
    release_evaluation.set()
    await ingest_task

    assert observed_entry_locks == [True]
    assert runtime.paused is True
    assert "SUPERVISOR_ENTRY_LOCK" in runtime.runtime_health_flags


async def test_strategy_backpressure_skips_only_cpu_evaluation(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-strategy-backpressure-scope",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    event = MarketEvent(
        event_id="depth-strategy-backpressure-scope",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="DEPTH_UPDATE",
        venue_ts_ms=1_000,
        receive_monotonic_ns=1_000,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
    )

    class SupervisorStub:
        selection = None

        def __init__(self) -> None:
            self.telemetry = SupervisorTelemetry(
                queue_depth=64,
                queue_capacity=4_096,
                entry_locked=False,
                consumer_running=True,
            )

        @staticmethod
        def running() -> bool:
            return True

    lifecycle: list[str] = []
    supervisor = SupervisorStub()
    evaluator = _AsyncFakeProcessEvaluator()
    runtime._supervisor = supervisor  # type: ignore[assignment]
    runtime._live_strategy_evaluator = evaluator  # type: ignore[assignment]
    runtime.market_data_state = MarketDataState.LIVE
    runtime.paused = False
    runtime.runtime_health_flags = ["PUBLIC_SUPERVISOR_RUNNING"]
    runtime.ledger = object()  # type: ignore[assignment]
    original_on_book = runtime.paper_portfolio.on_book
    original_evaluate_health = runtime.paper_portfolio.evaluate_health

    def record_on_book(book) -> None:
        lifecycle.append("on_book")
        original_on_book(book)

    def record_health(*args, **kwargs) -> None:
        lifecycle.append("health")
        original_evaluate_health(*args, **kwargs)

    def record_persistence(_self: PaperRuntime, _ts_ms: int) -> bool:
        lifecycle.append("persistence")
        return True

    monkeypatch.setattr(runtime.paper_portfolio, "on_book", record_on_book)
    monkeypatch.setattr(runtime.paper_portfolio, "evaluate_health", record_health)
    monkeypatch.setattr(PaperRuntime, "_has_unpersisted_execution_state", lambda _self: True)
    monkeypatch.setattr(PaperRuntime, "_persist_execution_state_safely", record_persistence)

    await runtime.ingest_live_event_async(event)

    assert lifecycle == ["on_book", "health", "persistence"]
    assert len(runtime._market_event_buffer) == 1
    assert evaluator.completed_symbols == []
    assert evaluator.max_in_flight == 0
    assert runtime.paused is False
    assert "ENTRY_LOCK_EVENT_QUEUE_OVERLOAD" not in runtime.runtime_health_flags
    assert runtime._strategy_evaluation_backpressure_active is True
    assert runtime._strategy_evaluation_backpressure_skip_count == 1

    runtime.ledger = None
    system = runtime.dashboard()["system"]
    assert isinstance(system, dict)
    assert system["strategy_evaluation_backpressure_active"] is True
    assert system["strategy_evaluation_backpressure_skip_count"] == 1
    assert system["strategy_evaluation_backpressure_resume_count"] == 0
    assert system["strategy_evaluation_backpressure_high_water"] == 64
    assert system["strategy_evaluation_backpressure_low_water"] == 16


def test_strategy_backpressure_watermarks_cap_operational_queue() -> None:
    assert PaperRuntime._strategy_evaluation_queue_watermarks(4_096) == (64, 16)
    assert PaperRuntime._strategy_evaluation_queue_watermarks(64) == (32, 8)
    assert PaperRuntime._strategy_evaluation_queue_watermarks(1) == (1, 0)
    assert PaperRuntime._strategy_evaluation_queue_watermarks(0) == (0, 0)


async def test_strategy_backpressure_resumes_only_at_low_water() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-strategy-backpressure-hysteresis",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )

    def depth(ts_ms: int) -> MarketEvent:
        return MarketEvent(
            event_id=f"depth-strategy-backpressure-{ts_ms}",
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="DEPTH_UPDATE",
            venue_ts_ms=ts_ms,
            receive_monotonic_ns=ts_ms,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
        )

    class SupervisorStub:
        selection = None

        def __init__(self) -> None:
            self.telemetry = SupervisorTelemetry(
                queue_depth=64,
                queue_capacity=4_096,
                queue_overload_active=True,
                entry_locked=True,
                consumer_running=True,
            )

        @staticmethod
        def running() -> bool:
            return True

    supervisor = SupervisorStub()
    evaluator = _AsyncFakeProcessEvaluator()
    runtime._supervisor = supervisor  # type: ignore[assignment]
    runtime._live_strategy_evaluator = evaluator  # type: ignore[assignment]
    runtime.market_data_state = MarketDataState.LIVE
    runtime.runtime_health_flags = ["PUBLIC_SUPERVISOR_RUNNING"]

    await runtime.ingest_live_event_async(depth(1_000))
    assert runtime._strategy_evaluation_backpressure_active is True
    assert runtime.paused is True

    supervisor.telemetry.queue_overload_active = False
    supervisor.telemetry.entry_locked = False
    supervisor.telemetry.queue_depth = 17
    await runtime.ingest_live_event_async(depth(3_000))
    assert runtime._strategy_evaluation_backpressure_active is True
    assert runtime.paused is False

    supervisor.telemetry.queue_depth = 16
    await runtime.ingest_live_event_async(depth(5_000))

    assert runtime._strategy_evaluation_backpressure_active is False
    assert runtime._strategy_evaluation_backpressure_skip_count == 2
    assert runtime._strategy_evaluation_backpressure_resume_count == 1
    assert evaluator.completed_symbols == ["BTCUSDT"]
    assert evaluator.max_in_flight == 1


async def test_directional_change_observes_every_book_before_2s_strategy_cadence(
    monkeypatch,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-directional-change-cadence",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    evaluator = _AsyncFakeProcessEvaluator()
    runtime._live_strategy_evaluator = evaluator  # type: ignore[assignment]
    runtime.ledger = object()  # type: ignore[assignment]
    on_book_times: list[int] = []
    health_times: list[int] = []
    lifecycle: list[str] = []
    original_on_book = runtime.paper_portfolio.on_book
    original_evaluate_health = runtime.paper_portfolio.evaluate_health
    original_dc_update = runtime_module.DirectionalChangeEngine.update

    def depth(sequence: int, ts_ms: int, mid: Decimal) -> MarketEvent:
        half_spread = Decimal("0.01")
        return MarketEvent(
            event_id=f"depth-directional-change-{sequence}",
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="DEPTH_UPDATE",
            venue_ts_ms=ts_ms,
            receive_monotonic_ns=ts_ms * 1_000,
            sequence_start=sequence,
            sequence_end=sequence,
            previous_sequence_end=sequence - 1 if sequence > 1 else None,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={
                "bid": str(mid - half_spread),
                "bid_qty": "1",
                "ask": str(mid + half_spread),
                "ask_qty": "1",
            },
        )

    def record_on_book(book) -> None:
        on_book_times.append(book.ts_ms)
        lifecycle.append(f"on_book:{book.ts_ms}")
        original_on_book(book)

    def record_dc_update(engine, observation):
        lifecycle.append(f"dc:{engine.profile_id}:{observation.venue_ts_ms}")
        return original_dc_update(engine, observation)

    def record_health(snapshot, *args, **kwargs) -> None:
        health_times.append(snapshot.ts_ms)
        lifecycle.append(f"health:{snapshot.ts_ms}")
        original_evaluate_health(snapshot, *args, **kwargs)

    monkeypatch.setattr(runtime.paper_portfolio, "on_book", record_on_book)
    monkeypatch.setattr(runtime.paper_portfolio, "evaluate_health", record_health)
    monkeypatch.setattr(runtime_module.DirectionalChangeEngine, "update", record_dc_update)
    monkeypatch.setattr(PaperRuntime, "_has_unpersisted_execution_state", lambda _self: False)

    await runtime.ingest_live_event_async(depth(1, 1_000, Decimal("100")))
    await runtime.ingest_live_event_async(depth(2, 1_500, Decimal("100.50")))

    assert runtime.strategy_evaluation_interval_ms == 2_000
    assert evaluator.completed_symbols == ["BTCUSDT"]
    assert on_book_times == [1_000, 1_500]
    assert health_times == [1_000, 1_500]
    assert len(runtime._market_event_buffer) == 2
    assert lifecycle == [
        "on_book:1000",
        "dc:FAST:1000",
        "dc:SWING:1000",
        "health:1000",
        "on_book:1500",
        "dc:FAST:1500",
        "dc:SWING:1500",
        "health:1500",
    ]

    runtime.ledger = None
    system = runtime.dashboard()["system"]
    assert isinstance(system, dict)
    assert system["directional_change_mode"] == "OBSERVATION_ONLY"
    profiles = system["directional_change_profiles"]
    assert isinstance(profiles, dict)
    assert profiles["FAST"] == {
        "initialized": True,
        "event_count": 1,
        "last_direction": "UP_RUN",
        "last_confirmation_type": "UPTURN",
    }
    assert profiles["SWING"] == {
        "initialized": True,
        "event_count": 0,
        "last_direction": "UNINITIALIZED",
        "last_confirmation_type": "NONE",
    }


async def test_directional_change_quality_faults_reset_without_losing_last_confirmation() -> None:
    for fault_name, stale, sequence_valid in (
        ("stale", True, True),
        ("sequence-invalid", False, False),
    ):
        runtime = PaperRuntime(
            mode=RuntimeMode.LIVE_SHADOW_PAPER,
            run_id=f"run-directional-change-{fault_name}",
            venue=Venue.BINANCE_USDM,
            clock=DeterministicClock(),
        )
        evaluator = _AsyncFakeProcessEvaluator()
        runtime._live_strategy_evaluator = evaluator  # type: ignore[assignment]

        def depth(
            sequence: int,
            ts_ms: int,
            mid: Decimal,
            *,
            event_stale: bool = False,
            event_sequence_valid: bool = True,
            _runtime: PaperRuntime = runtime,
            _fault_name: str = fault_name,
        ) -> MarketEvent:
            half_spread = Decimal("0.01")
            return MarketEvent(
                event_id=f"depth-{_fault_name}-{sequence}",
                run_id=_runtime.run_id,
                venue=_runtime.venue,
                symbol="BTCUSDT",
                event_type="DEPTH_UPDATE",
                venue_ts_ms=ts_ms,
                receive_monotonic_ns=ts_ms * 1_000,
                sequence_start=sequence,
                sequence_end=sequence,
                previous_sequence_end=sequence - 1 if sequence > 1 else None,
                quality=DataQuality(
                    is_live=True,
                    is_stale=event_stale,
                    sequence_valid=event_sequence_valid,
                    lag_ms=0,
                ),
                data={
                    "bid": str(mid - half_spread),
                    "bid_qty": "1",
                    "ask": str(mid + half_spread),
                    "ask_qty": "1",
                },
            )

        await runtime.ingest_live_event_async(depth(1, 1_000, Decimal("100")))
        await runtime.ingest_live_event_async(depth(2, 1_500, Decimal("100.50")))
        await runtime.ingest_live_event_async(
            depth(
                3,
                1_600,
                Decimal("90"),
                event_stale=stale,
                event_sequence_valid=sequence_valid,
            )
        )

        for profile_id in ("FAST", "SWING"):
            snapshot = runtime._directional_change_engines[
                ("BTCUSDT", profile_id)
            ].snapshot
            assert snapshot.state.value == "UNINITIALIZED"
            assert snapshot.threshold is None
            assert snapshot.continuity_epoch == 1
        profiles = runtime.dashboard()["system"]["directional_change_profiles"]
        assert isinstance(profiles, dict)
        assert profiles["FAST"] == {
            "initialized": False,
            "event_count": 1,
            "last_direction": "UNINITIALIZED",
            "last_confirmation_type": "UPTURN",
        }
        assert profiles["SWING"]["initialized"] is False


def test_directional_change_runtime_memory_is_bounded() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-directional-change-bounded",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )

    def observation(symbol: str, sequence: int) -> MarketEvent:
        return MarketEvent(
            event_id=f"dc-bounded-{symbol}-{sequence}",
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol=symbol,
            event_type="DEPTH_UPDATE",
            venue_ts_ms=sequence,
            receive_monotonic_ns=sequence,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={},
        )

    for index in range(runtime_module._DIRECTIONAL_CHANGE_SYMBOL_LIMIT + 10):
        runtime._observe_directional_change(
            observation(f"S{index:02d}USDT", 1),
            bid=Decimal("99.99"),
            ask=Decimal("100.01"),
        )
    latest_symbol = f"S{runtime_module._DIRECTIONAL_CHANGE_SYMBOL_LIMIT + 9:02d}USDT"
    for sequence in range(2, 302):
        runtime._observe_directional_change(
            observation(latest_symbol, sequence),
            bid=Decimal("99.99"),
            ask=Decimal("100.01"),
        )

    assert len(runtime._directional_change_symbols) == (
        runtime_module._DIRECTIONAL_CHANGE_SYMBOL_LIMIT
    )
    assert len(runtime._directional_change_engines) == (
        runtime_module._DIRECTIONAL_CHANGE_SYMBOL_LIMIT * 2
    )
    assert all(
        len(engine._seen_event_ids) <= runtime_module._DIRECTIONAL_CHANGE_DEDUPE_CAPACITY
        for engine in runtime._directional_change_engines.values()
    )
    assert runtime.strategy_evaluation_count == 0
    assert runtime._candidate_plan_buffer == []


def test_semivariance_updates_once_per_completed_minute_and_not_on_depth(
    monkeypatch,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-semivariance-completed-minute",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    update_calls: list[int] = []
    original_update = runtime_module.SemivarianceJumpEngine.update

    def trade(event_id: str, ts_ms: int, price: str) -> MarketEvent:
        return MarketEvent(
            event_id=event_id,
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="TRADE",
            venue_ts_ms=ts_ms,
            transaction_ts_ms=ts_ms,
            receive_monotonic_ns=ts_ms * 1_000,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={
                "price": price,
                "quantity": "1",
                "buyer_is_aggressor": True,
            },
        )

    def depth(event_id: str, ts_ms: int) -> MarketEvent:
        return MarketEvent(
            event_id=event_id,
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="DEPTH_UPDATE",
            venue_ts_ms=ts_ms,
            receive_monotonic_ns=ts_ms * 1_000,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={"bid": "102.9", "bid_qty": "1", "ask": "103.1", "ask_qty": "1"},
        )

    def record_update(engine, observation):
        update_calls.append(observation.minute_start_ts_ms)
        return original_update(engine, observation)

    monkeypatch.setattr(runtime_module.SemivarianceJumpEngine, "update", record_update)

    runtime.ingest_live_event(trade("trade-0", 0, "100"))
    runtime.ingest_live_event(trade("trade-30", 30_000, "101"))
    runtime.ingest_live_event(depth("depth-40", 40_000))
    assert runtime._semivariance_engines == {}
    assert runtime.dashboard()["system"]["semivariance_observation"]["last_status"] == (
        "WAITING_COMPLETED_MINUTE"
    )

    runtime.ingest_live_event(trade("trade-60", 60_000, "102"))
    engine = runtime._semivariance_engines["BTCUSDT"]
    assert engine.buffer_sizes == (0, 0, 0)
    runtime.ingest_live_event(trade("trade-90", 90_000, "103"))
    runtime.ingest_live_event(depth("depth-100", 100_000))
    assert engine.buffer_sizes == (0, 0, 0)
    runtime.ingest_live_event(trade("trade-120", 120_000, "104"))

    assert update_calls == [60_000]
    assert engine.buffer_sizes == (1, 1, 0)
    snapshot = runtime._semivariance_latest_snapshots["BTCUSDT"]
    with localcontext() as context:
        context.prec = 50
        expected_return = (Decimal("103") / Decimal("101")).ln()
    assert snapshot.log_return == expected_return
    assert runtime.paper_portfolio.main.position is None
    assert runtime._candidate_plan_buffer == []
    summary = runtime.dashboard()["system"]["semivariance_observation"]
    assert summary == {
        "mode": "OBSERVATION_ONLY",
        "tracked_symbol_count": 1,
        "one_hour_ready_symbol_count": 0,
        "four_hour_ready_symbol_count": 0,
        "jump_ready_symbol_count": 0,
        "periodicity_status": "PERIODICITY_UNCALIBRATED",
        "last_symbol": "BTCUSDT",
        "last_completed_minute_ts_ms": 60_000,
        "last_status": "WARMUP_1H",
        "last_reset_reason": "NONE",
        "risk_multiplier_applied": False,
    }


def test_semivariance_gap_incomplete_and_out_of_order_fail_closed() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-semivariance-fail-closed",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )

    def trade(event_id: str, ts_ms: int, price: str) -> MarketEvent:
        return MarketEvent(
            event_id=event_id,
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="TRADE",
            venue_ts_ms=ts_ms,
            transaction_ts_ms=ts_ms,
            receive_monotonic_ns=ts_ms * 1_000,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={
                "price": price,
                "quantity": "1",
                "buyer_is_aggressor": True,
            },
        )

    runtime.ingest_live_event(trade("trade-0", 0, "100"))
    runtime.ingest_live_event(trade("trade-60", 60_000, "101"))
    runtime.ingest_live_event(trade("trade-120", 120_000, "102"))
    assert runtime._semivariance_engines["BTCUSDT"].buffer_sizes == (1, 1, 0)

    runtime.ingest_live_event(trade("trade-gap", 240_000, "103"))
    assert "BTCUSDT" not in runtime._semivariance_engines
    summary = runtime.dashboard()["system"]["semivariance_observation"]
    assert summary["last_status"] == "RESET"
    assert summary["last_reset_reason"] == "COMPLETED_MINUTE_GAP"

    runtime.ingest_live_event(trade("trade-300", 300_000, "104"))
    runtime.ingest_live_event(trade("trade-360", 360_000, "105"))
    assert runtime._semivariance_engines["BTCUSDT"].buffer_sizes == (1, 1, 0)
    runtime.ingest_live_event(trade("trade-old", 350_000, "99"))
    assert "BTCUSDT" not in runtime._semivariance_engines
    summary = runtime.dashboard()["system"]["semivariance_observation"]
    assert summary["last_status"] == "RESET"
    assert summary["last_reset_reason"] == "OUT_OF_ORDER_TRADE"

    runtime._observe_completed_minute_candle(
        Candle(
            symbol="BTCUSDT",
            interval_seconds=60,
            open_ts_ms=420_000,
            open=Decimal("105"),
            high=Decimal("105"),
            low=Decimal("105"),
            close=Decimal("105"),
            volume=Decimal("1"),
            trade_count=1,
        ),
        completed_ts_ms=479_999,
    )
    assert runtime._semivariance_last_status == "RESET"
    assert runtime._semivariance_last_reset_reason == "INCOMPLETE_OR_INVALID_MINUTE"


def test_semivariance_runtime_memory_is_bounded_and_risk_is_untouched() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-semivariance-bounded",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    initial_risk_state = replace(runtime.paper_portfolio.main.risk_state)

    def candle(symbol: str, minute: int, close: Decimal) -> Candle:
        return Candle(
            symbol=symbol,
            interval_seconds=60,
            open_ts_ms=minute * 60_000,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1"),
            trade_count=1,
        )

    for index in range(runtime_module._SEMIVARIANCE_SYMBOL_LIMIT + 10):
        symbol = f"S{index:02d}USDT"
        runtime._observe_completed_minute_candle(
            candle(symbol, 0, Decimal("100")),
            completed_ts_ms=60_000,
        )
        runtime._observe_completed_minute_candle(
            candle(symbol, 1, Decimal("101")),
            completed_ts_ms=120_000,
        )
    latest_symbol = f"S{runtime_module._SEMIVARIANCE_SYMBOL_LIMIT + 9:02d}USDT"
    for minute in range(2, 302):
        runtime._observe_completed_minute_candle(
            candle(latest_symbol, minute, Decimal("101")),
            completed_ts_ms=(minute + 1) * 60_000,
        )

    assert len(runtime._semivariance_symbols) == runtime_module._SEMIVARIANCE_SYMBOL_LIMIT
    assert len(runtime._semivariance_engines) == runtime_module._SEMIVARIANCE_SYMBOL_LIMIT
    assert len(runtime._semivariance_previous_completed_closes) == (
        runtime_module._SEMIVARIANCE_SYMBOL_LIMIT
    )
    assert len(runtime._semivariance_latest_snapshots) == (
        runtime_module._SEMIVARIANCE_SYMBOL_LIMIT
    )
    assert runtime._semivariance_engines[latest_symbol].buffer_sizes == (60, 240, 0)
    assert runtime.paper_portfolio.main.risk_state == initial_risk_state
    assert runtime.paper_portfolio.main.position is None
    assert runtime._candidate_plan_buffer == []


def test_live_book_event_reports_slowest_processing_phase(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-phase-diagnostics",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    event = MarketEvent(
        event_id="depth-live-phase-diagnostics",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="DEPTH_UPDATE",
        venue_ts_ms=1_000,
        receive_monotonic_ns=1_000,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
    )
    original_on_book = runtime.paper_portfolio.on_book

    def slow_on_book(book) -> None:
        time.sleep(0.03)
        original_on_book(book)

    monkeypatch.setattr(runtime.paper_portfolio, "on_book", slow_on_book)
    monkeypatch.setattr(
        PaperRuntime,
        "_evaluate_prepared_strategy",
        lambda _runtime, _prepared: (),
    )

    runtime.ingest_live_event(event, defer_execution_persistence=True)
    diagnostics = runtime._operational_diagnostics()

    assert diagnostics["live_event_phase_max_name"] == "PAPER_PORTFOLIO_ON_BOOK"
    assert float(str(diagnostics["live_event_phase_max_ms"])) >= 25
    assert diagnostics["live_event_phase_max_event_type"] == "DEPTH_UPDATE"
    assert diagnostics["live_event_phase_max_symbol"] == "BTCUSDT"
    assert "STRATEGY_EVALUATION" in diagnostics["live_event_phase_last_ms"]


def test_unchanged_execution_state_skips_sqlite_persistence(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-no-ledger-churn",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    writes = 0

    def record_write(_ts_ms: int) -> None:
        nonlocal writes
        writes += 1

    monkeypatch.setattr(
        PaperRuntime,
        "_persist_execution_state",
        lambda _self, ts: record_write(ts),
    )

    assert runtime._persist_execution_state_safely(1_000) is True
    assert writes == 0


def test_runtime_event_memory_rolls_one_event_at_fixed_capacity() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    first = MarketEvent(
        event_id="first",
        run_id="ready",
        venue=Venue.NONE,
        symbol="BTCUSDT",
        event_type="WIDE_TICKER",
        venue_ts_ms=1,
        receive_monotonic_ns=1,
        quality=DataQuality(
            is_live=False,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"last_price": "100"},
    )
    last = first.model_copy(update={"event_id": "last", "venue_ts_ms": 2})

    runtime._events.extend([first] * 10_000)
    runtime._events.append(last)

    assert runtime._events.maxlen == 10_000
    assert len(runtime.events) == 10_000
    assert runtime.events[-1] is last
    assert sum(event is first for event in runtime.events) == 9_999


def test_live_runtime_keeps_bounded_display_event_memory() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-event-memory",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    first = MarketEvent(
        event_id="first-live",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="BOOK_TICKER",
        venue_ts_ms=1,
        receive_monotonic_ns=1,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"bid": "100", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
    )
    last = first.model_copy(update={"event_id": "last-live", "venue_ts_ms": 2})

    runtime._events.extend([first] * 2_048)
    runtime._events.append(last)

    assert runtime._events.maxlen == 2_048
    assert len(runtime.events) == 2_048
    assert runtime.events[-1] is last
    assert runtime._operational_diagnostics()["event_memory_limit"] == 2_048


def test_runtime_plan_rejections_roll_one_row_at_fixed_capacity() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    first = {"event_id": "first"}
    last = {"event_id": "last"}

    runtime.plan_rejections.extend([first] * 2_000)
    runtime.plan_rejections.append(last)

    assert runtime.plan_rejections.maxlen == 2_000
    assert len(runtime.plan_rejections) == 2_000
    assert runtime.plan_rejections[-1] is last
    assert sum(row is first for row in runtime.plan_rejections) == 1_999


def test_live_dashboard_bounds_event_projection(monkeypatch) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-bounded-dashboard",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    event = MarketEvent(
        event_id="event",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="WIDE_TICKER",
        venue_ts_ms=1,
        receive_monotonic_ns=1,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"last_price": "100"},
    )
    runtime._events.extend(
        event.model_copy(update={"event_id": f"event-{index}", "venue_ts_ms": index})
        for index in range(1_000)
    )
    observed: dict[str, object] = {}

    def capture_dashboard(_status, events, **_kwargs):
        observed["events"] = events
        return {}

    monkeypatch.setattr(runtime_module, "build_dashboard_snapshot", capture_dashboard)

    runtime.dashboard()

    projected = observed["events"]
    assert isinstance(projected, tuple)
    assert len(projected) == 512
    assert projected[0].event_id == "event-488"
    assert projected[-1].event_id == "event-999"


def test_live_dashboard_strategy_statistics_are_cached_until_trade_state_changes(
    monkeypatch,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-performance-cache-a",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    calls = 0

    class CountingAnalytics:
        def strategy_reports(self, _trades, *, strategy_ids):
            nonlocal calls
            calls += 1
            return [
                {"strategy_id": strategy_id, "profile": profile}
                for strategy_id in strategy_ids
                for profile in ("BASE", "STRESS")
            ]

    monkeypatch.setattr(runtime_module, "TradeAnalytics", CountingAnalytics)

    first = runtime.strategy_performance(include_persisted=False)
    second = runtime.strategy_performance(include_persisted=False)
    runtime.run_id = "run-performance-cache-b"
    third = runtime.strategy_performance(include_persisted=False)

    assert first == second == third
    assert calls == 2


def test_live_dashboard_waits_for_versioned_trade_cache_before_showing_statistics(
    monkeypatch,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-performance-loading",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    recovered_as_current = {
        **_sample_trade("recovered-prior-version"),
        "sample_type": "LIVE_PUBLIC",
        "strategy_version": runtime_module.STRATEGY_VERSION,
    }
    monkeypatch.setattr(
        PaperRuntime,
        "_dashboard_live_shadow_trades",
        lambda _runtime: (recovered_as_current,),
    )
    runtime.dashboard_trade_cache_loading = True

    loading = next(
        row
        for row in runtime.strategy_performance(include_persisted=False)
        if row["strategy_id"] == "LSA_REVERSAL_V1" and row["profile"] == "BASE"
    )
    assert loading["sample_size"] == 0
    assert loading["data_state"] == "LOADING_HISTORY"
    assert runtime.strategy_symbol_performance(include_persisted=False) == []
    assert runtime.strategy_analytics_scope(include_persisted=False)["data_state"] == (
        "LOADING_HISTORY"
    )

    runtime.dashboard_trade_cache_loading = False
    runtime.dashboard_trade_cache_ready = True
    ready = next(
        row
        for row in runtime.strategy_performance(include_persisted=False)
        if row["strategy_id"] == "LSA_REVERSAL_V1" and row["profile"] == "BASE"
    )
    assert ready["sample_size"] == 1
    assert ready["data_state"] == "READY"


def test_live_strategy_symbol_analytics_reuses_warmed_trade_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-analytics-cache.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-analytics-cache",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    cached_trade = _sample_trade("shadow-current")
    cached_trade.update(
        {
            "shadow_trade_id": "shadow-current",
            "run_id": runtime.run_id,
            "closed_ts_ms": cached_trade["exit_ts_ms"],
            "sample_type": "LIVE_PUBLIC",
            "strategy_version": runtime_module.STRATEGY_VERSION,
        }
    )
    ledger.record_shadow_trade(cached_trade)
    runtime._refresh_dashboard_trade_cache()

    def reject_live_ledger_scan(*_args, **_kwargs):
        raise AssertionError("LIVE 분석 요청이 활성 원장을 다시 읽었습니다.")

    monkeypatch.setattr(SQLiteLedger, "list_shadow_trades", reject_live_ledger_scan)

    report = next(
        row
        for row in runtime.strategy_performance(include_persisted=False)
        if row["strategy_id"] == cached_trade["strategy_id"] and row["profile"] == "BASE"
    )
    symbols = runtime.strategy_symbol_performance(include_persisted=False)
    scope = runtime.strategy_analytics_scope(include_persisted=False)

    assert report["sample_size"] == 1
    assert symbols[0]["sample_size"] == 1
    assert scope["excluded_prior_version_samples"] == 0
    ledger.close()


def test_disk_pressure_is_connected_to_runtime_entry_gate_and_dashboard(
    tmp_path: Path,
) -> None:
    guard = ParquetEventStore(
        tmp_path / "parquet",
        minimum_free_bytes=200,
        minimum_free_ratio=0.10,
        disk_usage=lambda _: DiskUsage(total=1_000, used=950, free=50),
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-disk-pressure",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        storage_guard=guard,
    )
    runtime.market_data_state = MarketDataState.LIVE
    runtime.paused = False

    assert runtime._refresh_storage_safety(force=True) is False
    assert runtime.paused is True
    assert "STORAGE_PRESSURE_ENTRY_LOCK" in runtime.runtime_health_flags
    dashboard = runtime.dashboard()
    assert dashboard["system"]["storage_entry_allowed"] is False
    assert dashboard["system"]["disk_pressure_entry_lock"] is True
    assert dashboard["system"]["storage_lock_reason"] == "FREE_BYTES_BELOW_LIMIT"
    assert float(str(dashboard["system"]["process_memory_mb"])) > 0
    assert dashboard["operation_status"]["state"] == "SAFETY_WAITING"
    assert dashboard["operation_status"]["automatic_recovery"] is True

    plan = replace(
        candidate_plan(),
        run_id=runtime.run_id,
        venue=Venue.BINANCE_USDM,
    )
    runtime.paper_portfolio.offer((plan,), entries_paused=runtime.paused)
    assert runtime.paper_portfolio.main.pending_entry is None


def test_repeated_dashboard_snapshots_do_not_probe_storage_volume(
    tmp_path: Path,
) -> None:
    storage_probes = 0

    def disk_usage(_: Path) -> DiskUsage:
        nonlocal storage_probes
        storage_probes += 1
        return DiskUsage(total=1_000, used=100, free=900)

    guard = ParquetEventStore(
        tmp_path / "dashboard-cache-archive",
        minimum_free_bytes=100,
        minimum_free_ratio=0.10,
        disk_usage=disk_usage,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-dashboard-storage-cache",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        storage_guard=guard,
    )

    assert runtime._refresh_storage_safety(force=True) is True
    assert storage_probes == 1

    runtime.dashboard()
    runtime.dashboard()
    runtime.dashboard()

    assert storage_probes == 1


def test_runtime_checks_active_ledger_volume_as_well_as_archive(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "archive"
    ledger_path = tmp_path / "separate-ledger" / "ledger.sqlite3"

    def disk_usage(path: Path) -> DiskUsage:
        if path == ledger_path.parent:
            return DiskUsage(total=1_000, used=960, free=40)
        return DiskUsage(total=1_000, used=100, free=900)

    guard = ParquetEventStore(
        archive_path,
        minimum_free_bytes=100,
        minimum_free_ratio=0.10,
        disk_usage=disk_usage,
    )
    ledger = SQLiteLedger(ledger_path)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-ledger-pressure",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        storage_guard=guard,
        ledger=ledger,
    )
    runtime.market_data_state = MarketDataState.LIVE
    runtime.paused = False

    assert runtime._refresh_storage_safety(force=True) is False
    dashboard = runtime.dashboard()
    assert dashboard["system"]["storage_lock_reason"] == ("LEDGER_FREE_BYTES_BELOW_LIMIT")
    assert dashboard["system"]["archive_storage_free_bytes"] == 900
    assert dashboard["system"]["ledger_storage_free_bytes"] == 40
    assert runtime.paused is True
    ledger.close()


async def test_storage_health_refresh_runs_outside_event_loop(tmp_path: Path) -> None:
    calling_thread = threading.get_ident()
    health_threads: list[int] = []

    def slow_disk_usage(_: Path) -> DiskUsage:
        health_threads.append(threading.get_ident())
        time.sleep(0.05)
        return DiskUsage(total=1_000, used=100, free=900)

    guard = ParquetEventStore(
        tmp_path / "offloop-archive",
        minimum_free_bytes=100,
        minimum_free_ratio=0.10,
        disk_usage=slow_disk_usage,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-offloop-storage-health",
        venue=Venue.BINANCE_USDM,
        storage_guard=guard,
    )

    refresh = asyncio.create_task(runtime.refresh_storage_safety_async())
    heartbeat_ticks = 0
    while not refresh.done():
        heartbeat_ticks += 1
        await asyncio.sleep(0.005)

    assert await refresh is True
    assert heartbeat_ticks >= 5
    assert health_threads
    assert all(thread_id != calling_thread for thread_id in health_threads)
    dashboard = runtime.dashboard()
    assert dashboard["system"]["storage_health_refresh_count"] == 1
    assert float(str(dashboard["system"]["storage_health_refresh_last_ms"])) >= 50
    assert dashboard["system"]["storage_entry_allowed"] is True


async def test_storage_health_worker_recovers_transient_persistence_pressure(
    tmp_path: Path,
) -> None:
    free_bytes = 50

    def disk_usage(_: Path) -> DiskUsage:
        return DiskUsage(total=1_000, used=1_000 - free_bytes, free=free_bytes)

    guard = ParquetEventStore(
        tmp_path / "recoverable-pressure-archive",
        minimum_free_bytes=100,
        minimum_free_ratio=0.10,
        disk_usage=disk_usage,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-recoverable-pressure-worker",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        storage_guard=guard,
    )
    runtime._handle_persistence_fault(
        StoragePressureError("STORAGE_PRESSURE: FREE_BYTES_BELOW_LIMIT")
    )

    assert await runtime.refresh_storage_safety_async() is False
    assert runtime._persistence_fault_active is True
    assert runtime._persistence_recovery_count == 0

    free_bytes = 900
    assert await runtime.refresh_storage_safety_async() is True
    assert runtime._persistence_fault_active is False
    assert runtime._persistence_recovery_count == 1
    assert runtime.dashboard()["system"]["persistence_last_error"] == "NONE"


async def test_stale_storage_health_fails_closed_until_worker_refresh(
    tmp_path: Path,
) -> None:
    clock = DeterministicClock()
    guard = ParquetEventStore(
        tmp_path / "stale-archive",
        minimum_free_bytes=100,
        minimum_free_ratio=0.10,
        disk_usage=lambda _: DiskUsage(total=1_000, used=100, free=900),
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-stale-storage-health",
        venue=Venue.BINANCE_USDM,
        clock=clock,
        storage_guard=guard,
    )

    assert runtime._refresh_storage_safety(force=True) is True
    runtime.paused = False
    clock.advance_ms(5_001)

    assert runtime._refresh_storage_safety() is False
    assert runtime.paused is True
    assert "ENTRY_LOCK_STORAGE_HEALTH_STALE" in runtime.runtime_health_flags
    assert runtime.dashboard()["system"]["storage_lock_reason"] == "STORAGE_HEALTH_STALE"

    assert await runtime.refresh_storage_safety_async() is True
    assert "ENTRY_LOCK_STORAGE_HEALTH_STALE" not in runtime.runtime_health_flags
    assert runtime.dashboard()["system"]["storage_lock_reason"] == "NONE"


def test_runtime_persists_only_canonical_one_second_and_replay_candles(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "candles.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-bounded-candles",
        venue=Venue.FIXTURE,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    candles = [
        Candle(
            symbol="BTCUSDT",
            interval_seconds=interval,
            open_ts_ms=1_000,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
            volume=Decimal("2"),
            trade_count=3,
        )
        for interval in (1, 5, 15, 30, 60, 180, 300, 600, 900, 3600)
    ]

    runtime._buffer_completed_candles(candles)

    assert {row["interval_seconds"] for row in runtime._candle_buffer} == {1, 180}
    ledger.close()


def test_rejection_audit_does_not_duplicate_full_recovery_snapshots(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "bounded-snapshots.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-bounded-snapshots",
        venue=Venue.FIXTURE,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    initial_snapshots = ledger.count("snapshots")
    initial_account_snapshots = ledger.count("strategy_account_snapshots")
    runtime.paper_portfolio.audit_events.append(
        {
            "event": "LEAGUE_RISK_REJECTED",
            "run_id": runtime.run_id,
            "symbol": "BTCUSDT",
            "account_id": "LSA_REVERSAL_V1:BASE",
            "ts_ms": 1_000,
        }
    )

    runtime._persist_execution_state(1_000)

    assert ledger.count("execution_audit") == 1
    assert ledger.count("snapshots") == initial_snapshots
    assert ledger.count("strategy_account_snapshots") == initial_account_snapshots

    runtime.paper_portfolio.audit_events.append(
        {
            "event": "LEAGUE_CANDIDATE_ARMED",
            "run_id": runtime.run_id,
            "symbol": "BTCUSDT",
            "account_id": "LSA_REVERSAL_V1:BASE",
            "ts_ms": 2_000,
        }
    )
    runtime._persist_execution_state(2_000)

    assert ledger.count("execution_audit") == 2
    assert ledger.count("snapshots") == initial_snapshots + 1
    assert ledger.count("strategy_account_snapshots") == initial_account_snapshots + 1
    ledger.close()


def test_sqlite_write_fault_fails_closed_and_bounds_retry_buffer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "fault.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-storage-fault",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )

    def fail_write(_rows):
        raise OSError("simulated disk full")

    monkeypatch.setattr(ledger, "record_market_events", fail_write)
    runtime._market_event_buffer = [{"event_id": f"event-{index}"} for index in range(12_000)]
    runtime._flush_persistence()

    assert runtime.paused is True
    assert runtime.paper_portfolio.main.risk_state.faulted is True
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" in runtime.runtime_health_flags
    assert runtime._persistence_fault_count == 1
    assert runtime._persistence_buffer_dropped == 2_000
    assert len(runtime._market_event_buffer) == 10_000
    dashboard = runtime.dashboard()
    assert "OSError" in str(dashboard["system"]["persistence_last_error"])
    assert dashboard["operation_status"]["state"] == "SAFETY_BLOCKED"
    assert dashboard["operation_status"]["automatic_recovery"] is False
    ledger.close()


def test_transient_storage_pressure_recovers_and_preserves_incident_history(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "transient-storage-pressure.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-transient-storage-pressure",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(current_utc_ms=10_000),
        ledger=ledger,
    )

    runtime._handle_persistence_fault(
        StoragePressureError("STORAGE_PRESSURE: FREE_BYTES_BELOW_LIMIT")
    )

    assert runtime._persistence_fault_count == 1
    assert runtime._persistence_fault_active is True
    assert runtime._persistence_fault_recoverable is True
    assert runtime.paper_portfolio.main.risk_state.faulted is False
    assert "ENTRY_LOCK_TRANSIENT_PERSISTENCE" in runtime.runtime_health_flags
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" not in runtime.runtime_health_flags

    runtime._storage_entry_allowed = True
    assert runtime._recover_transient_persistence_fault_if_safe() is True
    diagnostics = runtime.dashboard()["system"]
    assert diagnostics["persistence_fault_count"] == 1
    assert diagnostics["persistence_fault_active"] is False
    assert diagnostics["persistence_fault_recoverable"] is False
    assert diagnostics["persistence_recovery_count"] == 1
    assert "StoragePressureError" in str(diagnostics["persistence_last_recovered_error"])
    assert diagnostics["persistence_last_error"] == "NONE"
    assert "ENTRY_LOCK_TRANSIENT_PERSISTENCE" not in runtime.runtime_health_flags
    ledger.close()


async def test_broken_worker_process_recovers_and_retries_preserved_batch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "recoverable-worker-archive",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "recoverable-worker.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-recoverable-broken-worker",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
        market_event_archive=archive,
    )
    runtime._market_event_buffer = [
        {
            "event_id": "recoverable-worker-event",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "receive_monotonic_ns": 1_000,
            "data": {"price": "100", "quantity": "1"},
        }
    ]
    calls = 0

    async def simulated_process(function, *_arguments):
        nonlocal calls
        assert function is runtime_module.persist_archives_and_candles_in_process
        calls += 1
        if calls == 1:
            raise BrokenWorkerProcess("simulated worker initialization failure")
        return {
            "gate_wait_ms": 0.0,
            "archive_ms": 0.0,
            "ledger_ms": 0.0,
            "ledger_connect_ms": 0.0,
            "ledger_begin_wait_ms": 0.0,
            "ledger_write_ms": 0.0,
            "ledger_commit_ms": 0.0,
            "ledger_close_ms": 0.0,
            "market_events": 1,
            "candles": 0,
            "archive_batches": 1,
            "wal_probe_ms": 0.0,
            "wal_log_frames": 0,
            "wal_checkpointed_frames": 0,
            "wal_page_size": 4_096,
        }

    monkeypatch.setattr(runtime_module.to_process, "run_sync", simulated_process)

    await runtime._flush_persistence_isolated(None)

    assert calls == 1
    assert len(runtime._market_event_buffer) == 1
    assert runtime._persistence_fault_active is True
    assert runtime._persistence_fault_recoverable is True
    assert runtime.paper_portfolio.main.risk_state.faulted is False
    assert "ENTRY_LOCK_TRANSIENT_PERSISTENCE" in runtime.runtime_health_flags
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" not in runtime.runtime_health_flags

    runtime._storage_entry_allowed = True
    assert runtime._recover_transient_persistence_fault_if_safe() is True
    await runtime._flush_persistence_isolated(None)

    assert calls == 2
    assert runtime._market_event_buffer == []
    assert runtime._persistence_fault_active is False
    assert runtime._persistence_recovery_count == 1
    assert "BrokenWorkerProcess" in str(runtime._last_recovered_persistence_error)
    ledger.close()


async def test_persistence_worker_resumes_after_recovered_incident_count(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "recovered-worker.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-recovered-worker",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime._persistence_fault_count = 1
    runtime._persistence_fault_active = False
    runtime._market_event_buffer = [
        {
            "event_id": f"recovered-{index}",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "WIDE_TICKER",
            "venue_ts_ms": index,
            "receive_monotonic_ns": index,
            "data": {"last_price": "100"},
        }
        for index in range(500)
    ]

    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    for _ in range(100):
        if ledger.count("market_events") == 500:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert ledger.count("market_events") == 500
    assert runtime._market_event_buffer == []
    assert runtime._persistence_fault_count == 1
    ledger.close()


def test_active_persistence_fault_keeps_ingest_retry_buffer_bounded(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "active-fault-bounded.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-active-fault-bounded",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime._persistence_fault_active = True

    for index in range(10_100):
        runtime.ingest_live_event(
            MarketEvent(
                event_id=f"bounded-{index}",
                run_id=runtime.run_id,
                venue=runtime.venue,
                symbol="BTCUSDT",
                event_type="WIDE_TICKER",
                venue_ts_ms=index,
                receive_monotonic_ns=index,
                quality=DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=0,
                ),
                data={"last_price": "100"},
            )
        )

    assert len(runtime._market_event_buffer) == 10_000
    assert runtime._persistence_buffer_dropped == 100
    assert runtime._market_event_buffer[0]["event_id"] == "bounded-100"
    ledger.close()


async def test_market_persistence_worker_flushes_outside_ingest_loop(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "worker.sqlite3")
    clock = DeterministicClock()
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-persistence-worker",
        venue=Venue.BINANCE_USDM,
        clock=clock,
        ledger=ledger,
    )
    for index in range(500):
        runtime.ingest_live_event(
            MarketEvent(
                event_id=f"wide-{index}",
                run_id=runtime.run_id,
                venue=runtime.venue,
                symbol="BTCUSDT",
                event_type="WIDE_TICKER",
                venue_ts_ms=index,
                receive_monotonic_ns=index,
                quality=DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=0,
                ),
                data={"last_price": "100"},
            )
        )

    assert ledger.count("market_events") == 0
    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    for _ in range(100):
        if ledger.count("market_events") == 500:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert ledger.count("market_events") == 500
    assert runtime._persistence_fault_count == 0
    ledger.close()


async def test_parquet_persistence_worker_keeps_event_loop_responsive(
    tmp_path: Path,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "worker-parquet.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-parquet-worker",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
        market_event_archive=archive,
    )
    assert await runtime._warm_market_archive_worker()
    runtime._wal_checkpoint_next_flush = 1
    for index in range(1_000):
        runtime.ingest_live_event(
            MarketEvent(
                event_id=f"wide-parquet-{index}",
                run_id=runtime.run_id,
                venue=runtime.venue,
                symbol="BTCUSDT",
                event_type="WIDE_TICKER",
                venue_ts_ms=index,
                receive_monotonic_ns=index,
                quality=DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=0,
                ),
                data={"last_price": "100", "quote_volume_24h": "1000000"},
            )
        )

    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    heartbeat_ticks = 0
    for _ in range(500):
        if ledger.count("market_event_archives") == 4:
            break
        heartbeat_ticks += 1
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert heartbeat_ticks >= 10
    assert ledger.count("market_event_archives") == 4
    assert ledger.market_event_symbols(runtime.run_id) == [
        {"symbol": "BTCUSDT", "event_count": 1_000}
    ]
    assert runtime._persistence_flush_count >= 1
    assert runtime._persistence_flush_last_completed_ts_ms == runtime.clock.utc_ms()
    assert runtime._persistence_flush_max_ts_ms == runtime.clock.utc_ms()
    assert runtime._persistence_flush_slowest_market_events == 250
    assert runtime._persistence_flush_slowest_candles >= 0
    assert runtime._persistence_flush_slowest_archive_batches == 1
    assert runtime._persistence_flush_slowest_gate_wait_ms >= 0
    assert runtime._persistence_flush_slowest_archive_ms >= 0
    assert runtime._persistence_flush_slowest_ledger_ms >= 0
    assert runtime._persistence_flush_slowest_ledger_connect_ms >= 0
    assert runtime._persistence_flush_slowest_ledger_begin_wait_ms >= 0
    assert runtime._persistence_flush_slowest_ledger_write_ms >= 0
    assert runtime._persistence_flush_slowest_ledger_commit_ms >= 0
    assert runtime._persistence_flush_slowest_ledger_close_ms >= 0
    assert runtime._wal_checkpoint_count == 0
    assert runtime._wal_checkpoint_deferred_count == 1
    assert runtime._wal_checkpoint_last_wal_bytes < 16 * 1024 * 1024
    assert runtime._wal_checkpoint_fault_count == 0
    assert runtime._persistence_fault_count == 0
    assert runtime._persistence_buffer_dropped == 0
    assert runtime._market_event_buffer == []
    ledger.close()


async def test_wal_checkpoint_runs_without_blocking_live_persistence_flushes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "nonblocking-checkpoint.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-nonblocking-checkpoint",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime._wal_checkpoint_next_flush = 1
    monkeypatch.setattr(runtime_module, "_WAL_CHECKPOINT_SOFT_BYTES", 0)
    for index in range(1_000):
        runtime.ingest_live_event(
            MarketEvent(
                event_id=f"nonblocking-checkpoint-{index}",
                run_id=runtime.run_id,
                venue=runtime.venue,
                symbol="BTCUSDT",
                event_type="WIDE_TICKER",
                venue_ts_ms=index,
                receive_monotonic_ns=index,
                quality=DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=0,
                ),
                data={"last_price": "100"},
            )
        )

    checkpoint_started = asyncio.Event()
    release_checkpoint = asyncio.Event()

    async def slow_checkpoint(function, *arguments):
        assert function is runtime_module.run_passive_wal_checkpoint_in_process
        assert arguments == (str(ledger.path), True)
        checkpoint_started.set()
        await release_checkpoint.wait()
        return (0, 10, 10)

    monkeypatch.setattr(runtime_module.to_process, "run_sync", slow_checkpoint)
    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    await asyncio.wait_for(checkpoint_started.wait(), timeout=2.0)
    for _ in range(200):
        if runtime._persistence_flush_count >= 4:
            break
        await asyncio.sleep(0.01)

    assert runtime._wal_checkpoint_task is not None
    assert runtime._wal_checkpoint_task.done() is False
    assert runtime._persistence_flush_count == 4
    assert runtime._market_event_buffer == []
    diagnostics = runtime.dashboard()["system"]
    assert isinstance(diagnostics, dict)
    assert diagnostics["wal_checkpoint_running"] is True
    assert diagnostics["wal_checkpoint_current_concurrent_flush_delta"] == 3

    release_checkpoint.set()
    for _ in range(100):
        if runtime._wal_checkpoint_count == 1:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert runtime._wal_checkpoint_count == 1
    assert runtime._wal_checkpoint_last_concurrent_flush_delta == 3
    assert runtime._wal_checkpoint_max_concurrent_flush_delta == 3
    assert runtime._wal_checkpoint_fault_count == 0
    assert runtime._persistence_fault_count == 0
    assert ledger.count("market_events") == 1_000
    ledger.close()


async def test_logical_wal_frames_prevent_repeat_checkpoint_on_retained_file_size(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "logical-wal-market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "logical-wal.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-logical-wal",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
        market_event_archive=archive,
    )
    runtime._wal_checkpoint_next_flush = 1
    ledger.close()
    wal_path = ledger.path.with_name(f"{ledger.path.name}-wal")
    with wal_path.open("wb") as retained_wal:
        retained_wal.truncate(32 * 1024 * 1024)
    for index in range(2_000):
        runtime.ingest_live_event(
            MarketEvent(
                event_id=f"logical-wal-{index}",
                run_id=runtime.run_id,
                venue=runtime.venue,
                symbol="BTCUSDT",
                event_type="WIDE_TICKER",
                venue_ts_ms=index,
                receive_monotonic_ns=index,
                quality=DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=0,
                ),
                data={"last_price": "100"},
            )
        )

    persistence_calls = 0
    checkpoint_calls = 0

    async def simulated_process(function, *arguments):
        nonlocal checkpoint_calls, persistence_calls
        if function is runtime_module.persist_archives_and_candles_in_process:
            persistence_calls += 1
            log_frames = 5_000 if persistence_calls == 1 else 100
            return {
                "gate_wait_ms": 0.0,
                "archive_ms": 0.0,
                "ledger_ms": 0.0,
                "ledger_connect_ms": 0.0,
                "ledger_begin_wait_ms": 0.0,
                "ledger_write_ms": 0.0,
                "ledger_commit_ms": 0.0,
                "ledger_close_ms": 0.0,
                "archive_batches": 1,
                "wal_probe_ms": 0.0,
                "wal_log_frames": log_frames,
                "wal_checkpointed_frames": 0,
                "wal_page_size": 4_096,
            }
        assert function is runtime_module.run_passive_wal_checkpoint_in_process
        assert arguments == (str(ledger.path), True)
        checkpoint_calls += 1
        return (0, 5_000, 5_000)

    monkeypatch.setattr(runtime_module.to_process, "run_sync", simulated_process)
    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    for _ in range(500):
        if (
            runtime._market_event_buffer == []
            and runtime._wal_checkpoint_task is None
            and runtime._persistence_flush_count == 8
        ):
            break
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert runtime._persistence_flush_count == 8
    assert persistence_calls == 8
    assert checkpoint_calls == 1
    assert runtime._wal_checkpoint_count == 1
    assert runtime._wal_checkpoint_deferred_count >= 1
    assert runtime._wal_checkpoint_last_wal_bytes == 32 * 1024 * 1024
    assert runtime._wal_checkpoint_pending_bytes == 100 * 4_096
    assert runtime._persistence_fault_count == 0


async def test_parquet_process_fault_restores_batch_and_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-fault",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "worker-parquet-fault.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-parquet-worker-fault",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
        market_event_archive=archive,
    )
    runtime._market_event_buffer = [
        {
            "event_id": "event-process-fault",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "receive_monotonic_ns": 1_000,
            "data": {"price": "100", "quantity": "1"},
        }
    ]

    async def fail_process(*_args: object) -> object:
        raise OSError("simulated process archive fault")

    monkeypatch.setattr(runtime_module.to_process, "run_sync", fail_process)
    await runtime._flush_persistence_isolated(None)

    assert [row["event_id"] for row in runtime._market_event_buffer] == ["event-process-fault"]
    assert runtime._persistence_fault_count == 1
    assert runtime._persistence_buffer_dropped == 0
    assert runtime.paused is True
    assert runtime.paper_portfolio.main.risk_state.faulted is True
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" in runtime.runtime_health_flags
    ledger.close()


async def test_incomplete_oversized_wal_checkpoint_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "oversized-wal.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-oversized-wal",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime._wal_checkpoint_next_flush = 1
    monkeypatch.setattr(runtime_module, "_WAL_CHECKPOINT_SOFT_BYTES", 0)
    runtime._market_event_buffer = [
        {
            "event_id": "event-oversized-wal",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "receive_monotonic_ns": 1_000,
            "data": {"price": "100", "quantity": "1"},
        }
    ]

    async def incomplete_checkpoint(function, *arguments):
        assert function is runtime_module.run_passive_wal_checkpoint_in_process
        assert arguments == (str(ledger.path), True)
        return (0, 20_000, 0)

    monkeypatch.setattr(runtime_module.to_process, "run_sync", incomplete_checkpoint)
    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    for _ in range(100):
        if runtime._persistence_fault_count == 1:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert runtime._wal_checkpoint_count == 1
    assert runtime._wal_checkpoint_busy_count == 1
    assert runtime._persistence_fault_count == 1
    assert runtime.paused is True
    assert runtime.paper_portfolio.main.risk_state.faulted is True
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" in runtime.runtime_health_flags
    ledger.close()


async def test_incomplete_checkpoint_with_small_pending_tail_retries_without_fault(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "small-pending-tail.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-small-pending-tail",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime._wal_checkpoint_next_flush = 1
    monkeypatch.setattr(runtime_module, "_WAL_CHECKPOINT_SOFT_BYTES", 0)
    runtime._market_event_buffer = [
        {
            "event_id": f"event-small-pending-tail-{index}",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": index,
            "receive_monotonic_ns": index,
            "data": {"price": "100", "quantity": "1"},
        }
        for index in range(500)
    ]

    checkpoint_calls = 0

    async def nearly_complete_checkpoint(function, *arguments):
        nonlocal checkpoint_calls
        assert function is runtime_module.run_passive_wal_checkpoint_in_process
        assert arguments == (str(ledger.path), True)
        checkpoint_calls += 1
        if checkpoint_calls == 1:
            return (0, 41_714, 41_507)
        return (0, 41_714, 41_714)

    monkeypatch.setattr(runtime_module.to_process, "run_sync", nearly_complete_checkpoint)
    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    for _ in range(200):
        if runtime._wal_checkpoint_count >= 2:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert runtime._wal_checkpoint_count == 2
    assert runtime._wal_checkpoint_busy_count == 1
    assert runtime._wal_checkpoint_pending_bytes == 0
    assert runtime._persistence_fault_count == 0
    assert runtime._persistence_fault_active is False
    assert runtime.paper_portfolio.main.risk_state.faulted is False
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" not in runtime.runtime_health_flags
    ledger.close()


async def test_atomic_ledger_fault_restores_market_and_candle_batches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-ledger-fault",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "worker-ledger-fault.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-atomic-ledger-fault",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
        market_event_archive=archive,
    )
    runtime._market_event_buffer = [
        {
            "event_id": "event-ledger-fault",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "receive_monotonic_ns": 1_000,
            "data": {"price": "100", "quantity": "1"},
        }
    ]
    runtime._candle_buffer = [
        {
            "run_id": runtime.run_id,
            "symbol": "BTCUSDT",
            "interval_seconds": 1,
            "open_ts_ms": 1_000,
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100.5",
            "volume": "1",
        }
    ]

    async def fail_ledger_commit(function, *_args: object) -> object:
        assert function is runtime_module.persist_archives_and_candles_in_process
        raise OSError("simulated atomic ledger fault")

    monkeypatch.setattr(runtime_module.to_process, "run_sync", fail_ledger_commit)
    await runtime._flush_persistence_isolated(None)

    assert [row["event_id"] for row in runtime._market_event_buffer] == ["event-ledger-fault"]
    assert [row["open_ts_ms"] for row in runtime._candle_buffer] == [1_000]
    assert ledger.count("market_event_archives") == 0
    assert ledger.count("candles") == 0
    assert runtime._persistence_fault_count == 1
    assert runtime._persistence_buffer_dropped == 0
    assert runtime.paused is True
    assert runtime.paper_portfolio.main.risk_state.faulted is True
    assert "PERSISTENCE_FAULT_ENTRY_LOCK" in runtime.runtime_health_flags
    ledger.close()


def test_market_persistence_batch_is_bounded_on_slow_storage(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "bounded-worker.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-bounded-worker",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime._market_event_buffer = [
        {
            "event_id": f"wide-{index}",
            "run_id": runtime.run_id,
            "venue": runtime.venue.value,
            "symbol": "BTCUSDT",
            "event_type": "WIDE_TICKER",
            "venue_ts_ms": index,
            "receive_monotonic_ns": index,
            "data": {"last_price": "100"},
        }
        for index in range(600)
    ]

    runtime._flush_persistence(250)

    assert ledger.count("market_events") == 250
    assert len(runtime._market_event_buffer) == 350
    runtime._flush_persistence(250)
    assert ledger.count("market_events") == 500
    assert len(runtime._market_event_buffer) == 100
    runtime._flush_persistence(250)
    assert ledger.count("market_events") == 600
    assert runtime._market_event_buffer == []
    ledger.close()


def test_persistence_backlog_fail_closes_once_and_recovers_with_hysteresis(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "backlog-safety.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-backlog-safety",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )

    runtime._refresh_persistence_backlog_safety(9_999)
    assert "PERSISTENCE_BACKLOG_ENTRY_LOCK" not in runtime.runtime_health_flags

    runtime._refresh_persistence_backlog_safety(10_000)
    runtime._refresh_persistence_backlog_safety(5_000)
    diagnostics = runtime.dashboard()["system"]
    assert isinstance(diagnostics, dict)
    assert runtime.paused is True
    assert diagnostics["entry_locked"] is True
    assert diagnostics["persistence_backlog_peak"] == 10_000
    assert diagnostics["persistence_backlog_entry_lock_count"] == 1
    assert "PERSISTENCE_BACKLOG_ENTRY_LOCK" in runtime.runtime_health_flags

    runtime._refresh_persistence_backlog_safety(2_001)
    assert "PERSISTENCE_BACKLOG_ENTRY_LOCK" in runtime.runtime_health_flags
    runtime._refresh_persistence_backlog_safety(2_000)
    assert "PERSISTENCE_BACKLOG_ENTRY_LOCK" not in runtime.runtime_health_flags
    assert runtime._persistence_backlog_entry_lock_count == 1
    ledger.close()


async def test_wal_checkpoint_defers_small_wal_while_persistence_backlog_exists(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "backlog-checkpoint.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-backlog-checkpoint",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime._wal_checkpoint_next_flush = 1
    for index in range(3_000):
        runtime.ingest_live_event(
            MarketEvent(
                event_id=f"backlog-{index}",
                run_id=runtime.run_id,
                venue=runtime.venue,
                symbol="BTCUSDT",
                event_type="WIDE_TICKER",
                venue_ts_ms=index,
                receive_monotonic_ns=index,
                quality=DataQuality(
                    is_live=True,
                    is_stale=False,
                    sequence_valid=True,
                    lag_ms=0,
                ),
                data={"last_price": "100"},
            )
        )

    stop = asyncio.Event()
    worker = asyncio.create_task(runtime.run_persistence_worker(stop))
    for _ in range(500):
        if runtime._market_event_buffer == []:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await worker

    assert runtime._market_event_buffer == []
    assert runtime._persistence_flush_count == 12
    assert runtime._wal_checkpoint_deferred_count == 3
    assert runtime._wal_checkpoint_count == 0
    assert runtime._wal_checkpoint_last_wal_bytes < 16 * 1024 * 1024
    assert runtime._wal_checkpoint_fault_count == 0
    ledger.close()


def test_stale_trade_is_archived_but_not_used_for_candles_or_strategy_features(
    monkeypatch,
) -> None:
    clock = DeterministicClock(current_utc_ms=2_000)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-stale-trade-gate",
        venue=Venue.BINANCE_USDM,
        clock=clock,
    )

    def depth(event_id: str, ts_ms: int) -> MarketEvent:
        return MarketEvent(
            event_id=event_id,
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="DEPTH_UPDATE",
            venue_ts_ms=ts_ms,
            receive_monotonic_ns=clock.monotonic_ns(),
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=10,
            ),
            data={
                "bid": "100",
                "bid_qty": "2",
                "ask": "100.1",
                "ask_qty": "2",
            },
        )

    def trade(event_id: str, ts_ms: int, *, stale: bool) -> MarketEvent:
        return MarketEvent(
            event_id=event_id,
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="TRADE",
            venue_ts_ms=ts_ms,
            transaction_ts_ms=ts_ms,
            receive_monotonic_ns=clock.monotonic_ns(),
            quality=DataQuality(
                is_live=True,
                is_stale=stale,
                sequence_valid=True,
                lag_ms=900 if stale else 10,
                flags=("TRADE_LAG_STALE",) if stale else (),
            ),
            data={
                "price": "100.05",
                "quantity": "1",
                "buyer_is_aggressor": True,
            },
        )

    cancellation_calls: list[tuple[int, str]] = []

    def record_pending_cancellation(
        *,
        now_ms: int,
        reason_code: str,
    ) -> tuple[str, ...]:
        cancellation_calls.append((now_ms, reason_code))
        return ()

    monkeypatch.setattr(
        runtime.paper_portfolio,
        "cancel_all_pending_entries",
        record_pending_cancellation,
    )
    initial_process_state_key = runtime._strategy_process_state_key()
    runtime.ingest_live_event(depth("depth-1", 2_000))
    evaluation_count = runtime.strategy_evaluation_count
    runtime.ingest_live_event(trade("trade-stale", 2_100, stale=True))
    runtime.ingest_live_event(depth("depth-2", 2_600))

    assert runtime.candle_builder.snapshot("BTCUSDT") == ()
    assert runtime.latest_features["BTCUSDT"].data_healthy is False
    assert runtime.latest_books["BTCUSDT"].ts_ms == 2_600
    assert runtime.strategy_evaluation_count == evaluation_count
    assert runtime.paused is True
    assert "ENTRY_LOCK_DATA_HEALTH" in runtime.runtime_health_flags
    assert runtime._strategy_process_state_key() != initial_process_state_key
    assert cancellation_calls == [(2_100, "DATA_HEALTH_STALE")]
    assert runtime.dashboard()["system"]["stale_trade_symbols"] == 1

    runtime.ingest_live_event(trade("trade-fresh", 2_700, stale=False))
    assert runtime.latest_features["BTCUSDT"].data_healthy is False
    assert "ENTRY_LOCK_DATA_HEALTH" in runtime.runtime_health_flags
    runtime.ingest_live_event(depth("depth-3", 3_200))

    assert runtime.candle_builder.snapshot("BTCUSDT")
    assert runtime.latest_features["BTCUSDT"].data_healthy is True
    assert runtime.strategy_evaluation_count == evaluation_count
    assert runtime.dashboard()["system"]["stale_trade_symbols"] == 0


def test_live_dashboard_never_waits_for_sqlite_writer_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "dashboard-cache.sqlite3")
    ledger.start_run(
        "run-prior-dashboard",
        mode="LIVE_SHADOW_PAPER",
        venue="BINANCE_USDM",
        config={"strategy_version": "prior-dashboard-version"},
        started_ts_ms=500,
    )
    ledger.record_shadow_trade(
        {
            **_sample_trade("prior-dashboard-trade"),
            "run_id": "run-prior-dashboard",
            "shadow_trade_id": "prior-dashboard-trade",
            "closed_ts_ms": 2_000,
            "sample_type": "LIVE_PUBLIC",
            "strategy_version": "prior-dashboard-version",
        }
    )
    ledger.record_trade(
        {
            **_sample_trade("prior-dashboard-main-trade"),
            "run_id": "run-prior-dashboard",
            "sample_type": "LIVE_PUBLIC",
            "strategy_version": "prior-dashboard-version",
        }
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-dashboard-cache",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
        ledger=ledger,
    )

    def fail_if_dashboard_reads(*_args, **_kwargs):
        raise AssertionError("LIVE dashboard는 SQLite를 다시 읽으면 안 됩니다.")

    monkeypatch.setattr(ledger, "list_trades", fail_if_dashboard_reads)
    monkeypatch.setattr(ledger, "list_shadow_trades", fail_if_dashboard_reads)

    dashboard = runtime.dashboard()

    assert dashboard["status"]["mode"] == "LIVE_SHADOW_PAPER"
    assert dashboard["history"] == []
    assert dashboard["history_scope"]["excluded_prior_version_samples"] == 1
    assert len(dashboard["strategies"]) == len(runtime.strategy_registry.strategy_ids) == 15
    lsa_base = dashboard["strategies"][0]["performance"]["BASE"]
    assert lsa_base["sample_size"] == 0
    assert lsa_base["excluded_prior_version_samples"] == 1
    ledger.close()
