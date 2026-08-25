"""v0.2 시장이벤트 원장·종단 리플레이·전략 성과표를 검증한다."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

import backend.app.main as main_module
from backend.app.analytics.reports import TradeAnalytics
from backend.app.build_identity import STRATEGY_VERSION
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Venue
from backend.app.main import create_app
from backend.app.market_data.supervisor import ProviderSelection
from backend.app.replay.engine import ReplayEngine
from backend.app.replay.market import StoredMarketReplay, _candidate_plan_count
from backend.app.replay.process import _REPLAY_TARGET_CPU_RATIO, _ReplayCpuBudget
from backend.app.runtime import PaperRuntime
from backend.app.storage.parquet import ParquetEventStore
from backend.app.storage.sqlite import (
    LedgerInvariantError,
    SQLiteLedger,
    persist_archives_and_candles_in_process,
    run_passive_wal_checkpoint_in_process,
)
from backend.tests.test_candidate_paper_portfolio import book, candidate_plan
from backend.tests.test_storage_replay_analytics import _sample_trade


def market_event(
    run_id: str,
    *,
    event_id: str,
    ts_ms: int,
    event_type: str = "DEPTH_UPDATE",
) -> MarketEvent:
    data: dict[str, object]
    if event_type == "TRADE":
        data = {
            "price": "100.05",
            "quantity": "0.5",
            "buyer_is_aggressor": True,
        }
    else:
        data = {
            "bid": "99.9",
            "bid_qty": "100",
            "ask": "100.1",
            "ask_qty": "100",
            "bids": [["99.9", "100"], ["99.8", "100"]],
            "asks": [["100.1", "100"], ["100.2", "100"]],
        }
    return MarketEvent(
        event_id=event_id,
        run_id=run_id,
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        event_type=event_type,
        venue_ts_ms=ts_ms,
        transaction_ts_ms=ts_ms if event_type == "TRADE" else None,
        receive_monotonic_ns=ts_ms * 1_000_000,
        sequence_start=ts_ms if event_type == "DEPTH_UPDATE" else None,
        sequence_end=ts_ms if event_type == "DEPTH_UPDATE" else None,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=10,
        ),
        data=data,
    )


def candle_row(run_id: str, *, close: str = "100.1") -> dict[str, object]:
    return {
        "run_id": run_id,
        "symbol": "BTCUSDT",
        "interval_seconds": 1,
        "open_ts_ms": 1_000,
        "open": "100",
        "high": "100.2",
        "low": "99.9",
        "close": close,
        "volume": "1",
    }


def test_replay_candidate_count_deduplicates_main_and_league_accounts() -> None:
    audits = [
        {"event": "MAIN_CANDIDATE_SELECTED", "candidate_id": "candidate-1"},
        {"event": "LEAGUE_CANDIDATE_ARMED", "candidate_id": "candidate-1"},
        {"event": "LEAGUE_CANDIDATE_ARMED", "candidate_id": "candidate-1"},
        {"event": "LEAGUE_CANDIDATE_ARMED", "candidate_id": "candidate-2"},
        {"event": "ENTRY_FILLED", "candidate_id": "candidate-2"},
    ]
    assert _candidate_plan_count(audits) == 2


def test_cooperative_market_replay_preserves_canonical_checksum() -> None:
    events = [
        market_event("run-streaming", event_id="event-2", ts_ms=2_000).model_dump(
            mode="json"
        ),
        market_event("run-streaming", event_id="event-1", ts_ms=1_000).model_dump(
            mode="json"
        ),
    ]
    engine = ReplayEngine()
    baseline = engine.replay_market_path(
        events,
        config={"seed": 20260822},
        strategy_version="test-version",
        seed=20260822,
        decision_path=("SUMMARY:evaluated=0:qualified=0",),
        final_state="OBSERVING_NO_MAIN_TRADE",
    )
    checkpoints = 0

    def cooperate() -> None:
        nonlocal checkpoints
        checkpoints += 1

    cooperative = engine.replay_market_path(
        events,
        config={"seed": 20260822},
        strategy_version="test-version",
        seed=20260822,
        decision_path=("SUMMARY:evaluated=0:qualified=0",),
        final_state="OBSERVING_NO_MAIN_TRADE",
        cooperative_yield=cooperate,
    )

    assert cooperative == baseline
    assert checkpoints >= 2


def test_replay_cpu_budget_yields_without_carrying_unbounded_sleep_debt() -> None:
    assert _REPLAY_TARGET_CPU_RATIO == 0.05
    wall_values = iter((0.0, 1.0, 2.5))
    cpu_values = iter((0.0, 1.0, 1.1))
    sleeps: list[float] = []
    budget = _ReplayCpuBudget(
        target_cpu_ratio=0.25,
        max_sleep_seconds=0.50,
        monotonic=lambda: next(wall_values),
        process_time=lambda: next(cpu_values),
        sleeper=sleeps.append,
    )

    budget.checkpoint()
    budget.checkpoint()

    assert sleeps == [0.50]


def test_schema_v7_market_events_are_ordered_checksummed_immutable_and_counted(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    ledger.start_run(
        "run-market",
        mode="REPLAY",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    later = market_event("run-market", event_id="event-2", ts_ms=2_000)
    earlier = market_event("run-market", event_id="event-1", ts_ms=1_000)
    inserted = ledger.record_market_events(
        [later.model_dump(mode="json"), earlier.model_dump(mode="json")]
    )
    assert ledger.schema_version == 7
    assert ledger._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert ledger._connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0
    assert inserted == 2
    assert [row["event_id"] for row in ledger.list_market_events("run-market")] == [
        "event-1",
        "event-2",
    ]
    assert ledger.record_market_events([earlier.model_dump(mode="json")]) == 0
    conflicting = earlier.model_copy(
        update={"data": {**earlier.data, "bid": "1.0"}}
    )
    with pytest.raises(LedgerInvariantError, match="payload 불일치"):
        ledger.record_market_events([conflicting.model_dump(mode="json")])
    ledger.start_run(
        "run-market-2",
        mode="REPLAY",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    same_venue_event = market_event("run-market-2", event_id="event-1", ts_ms=1_000)
    assert ledger.record_market_events([same_venue_event.model_dump(mode="json")]) == 1
    assert ledger.count("market_events") == 3
    summaries = ledger.list_replayable_run_summaries()
    first_run = next(row for row in summaries if row["run_id"] == "run-market")
    assert first_run["market_event_count"] == 2
    assert ledger.market_event_symbols("run-market") == [
        {"symbol": "BTCUSDT", "event_count": 2}
    ]
    ledger.close()

    connection = sqlite3.connect(tmp_path / "ledger.sqlite3")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        connection.execute(
            "UPDATE market_events SET payload_json = '{}' WHERE event_id = 'event-1'"
        )
    connection.close()


def test_replayable_run_listing_does_not_force_live_buffer_flush(
    tmp_path: Path,
) -> None:
    class NonFlushingRuntime(PaperRuntime):
        def _flush_persistence(self, market_limit: int | None = None) -> None:
            raise AssertionError("리플레이 목록 조회가 LIVE 저장 버퍼를 동기 flush했습니다.")

    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    ledger.start_run(
        "run-listed",
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    event = market_event("run-listed", event_id="event-listed", ts_ms=1_000)
    assert ledger.record_market_events([event.model_dump(mode="json")]) == 1
    runtime = NonFlushingRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-listed",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )

    assert runtime.replayable_runs()[0]["run_id"] == "run-listed"
    ledger.close()


def test_replayable_run_listing_does_not_wait_for_live_writer_lock(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    ledger.start_run(
        "run-readable",
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    event = market_event("run-readable", event_id="event-readable", ts_ms=1_000)
    assert ledger.record_market_events([event.model_dump(mode="json")]) == 1
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def hold_writer_lock() -> None:
        with ledger._lock:
            lock_acquired.set()
            release_lock.wait(timeout=2)

    writer = threading.Thread(target=hold_writer_lock)
    writer.start()
    assert lock_acquired.wait(timeout=1)
    try:
        summaries = ledger.list_replayable_run_summaries()
    finally:
        release_lock.set()
        writer.join(timeout=1)

    assert summaries[0]["run_id"] == "run-readable"
    ledger.close()


def test_v2_global_event_identity_migrates_to_run_scoped_identity(tmp_path: Path) -> None:
    database = tmp_path / "legacy-v2.sqlite3"
    payload = market_event("legacy-run", event_id="shared-event", ts_ms=1_000).model_dump(
        mode="json"
    )
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        PRAGMA user_version = 2;
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            venue TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            config_json TEXT NOT NULL,
            started_ts_ms INTEGER NOT NULL,
            finalized_ts_ms INTEGER,
            summary_json TEXT
        );
        CREATE TABLE market_events (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            venue TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            venue_ts_ms INTEGER NOT NULL,
            receive_monotonic_ns INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            checksum TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
        (
            "legacy-run",
            "REPLAY",
            Venue.BINANCE_USDM.value,
            "legacy-hash",
            '{"seed":20260822}',
            1_000,
        ),
    )
    connection.execute(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "shared-event",
            "legacy-run",
            Venue.BINANCE_USDM.value,
            "BTCUSDT",
            "DEPTH_UPDATE",
            1_000,
            1_000_000_000,
            payload_json,
            hashlib.sha256(payload_json.encode()).hexdigest(),
        ),
    )
    connection.commit()
    connection.close()

    migrated = SQLiteLedger(database)
    assert migrated.schema_version == 7
    assert migrated.list_market_events("legacy-run")[0]["event_id"] == "shared-event"
    migrated.start_run(
        "new-run",
        mode="REPLAY",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=2_000,
    )
    same_id = market_event("new-run", event_id="shared-event", ts_ms=2_000)
    assert migrated.record_market_events([same_id.model_dump(mode="json")]) == 1
    migrated.close()


def test_runtime_batches_public_events_and_replays_same_pipeline_deterministically(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-public-recorded",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.paused = False
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="depth-1", ts_ms=1_000)
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="trade-1", ts_ms=1_100, event_type="TRADE")
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="depth-2", ts_ms=1_500)
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="trade-2", ts_ms=2_100, event_type="TRADE")
    )
    assert ledger.count("market_events") == 0
    runtime.flush_storage()
    assert ledger.count("market_events") == 4
    assert ledger.count("candles") == 1

    first = StoredMarketReplay().run(
        ledger,
        source_run_id=runtime.run_id,
        created_ts_ms=2_000,
    )
    second = StoredMarketReplay().run(
        ledger,
        source_run_id=runtime.run_id,
        created_ts_ms=2_001,
    )
    assert first.checksum == second.checksum
    assert first.event_count == 4
    assert first.event_type_counts == {"DEPTH_UPDATE": 2, "TRADE": 2}
    assert first.strategy_evaluation_count == 32
    assert first.qualified_signal_count == 0
    assert first.final_state == "OBSERVING_NO_MAIN_TRADE"
    assert first.real_orders_enabled is False
    assert first.auth_required is False
    assert len(ledger.list_replay_runs(runtime.run_id)) == 2
    ledger.close()


def test_external_parquet_market_archive_keeps_sqlite_small_and_replays(
    tmp_path: Path,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "ledger.sqlite3",
        market_event_archive=archive,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-parquet-market",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        market_event_archive=archive,
        clock=DeterministicClock(),
    )
    events = [
        market_event(
            runtime.run_id,
            event_id=f"event-{index}",
            ts_ms=1_000 + index,
            event_type="TRADE" if index % 2 else "DEPTH_UPDATE",
        )
        for index in range(4)
    ]
    persisted_rows = [
        runtime._persistable_market_event(event) for event in reversed(events)
    ]
    runtime._market_event_buffer = list(persisted_rows)

    runtime.flush_storage()

    assert ledger.count("market_events") == 0
    assert ledger.count("market_event_archives") == 1
    assert ledger.market_event_symbols(runtime.run_id) == [
        {"symbol": "BTCUSDT", "event_count": 4}
    ]
    assert [row["event_id"] for row in ledger.list_market_events(runtime.run_id)] == [
        f"event-{index}" for index in range(4)
    ]
    assert len(
        ledger.list_market_events(runtime.run_id, event_types=("TRADE",))
    ) == 2
    assert ledger.list_market_events(runtime.run_id, limit=2)[-1]["event_id"] == "event-1"
    files = archive.dataset_files()
    assert len(files) == 1
    assert files[0].suffix == ".parquet"
    assert files[0].stat().st_size > 0
    repeated = archive.write_market_event_batch(persisted_rows)
    assert ledger.record_market_event_archive(repeated, persisted_rows) == 0
    with pytest.raises(ValueError, match="배치 checksum"):
        archive.read_market_event_batch(files[0], expected_checksum="0" * 64)
    with pytest.raises(ValueError, match="배치 checksum"):
        archive.read_market_event_batch_filtered(
            files[0],
            expected_checksum="0" * 64,
            symbol="BTCUSDT",
        )
    complete_table = pq.ParquetFile(files[0]).read()
    truncated_file = files[0].with_name("tampered-truncated.parquet")
    pq.write_table(complete_table.slice(0, complete_table.num_rows - 1), truncated_file)
    with pytest.raises(ValueError, match="배치 checksum"):
        archive.read_market_event_batch_filtered(
            truncated_file,
            expected_checksum=repeated.checksum,
            symbol="BTCUSDT",
        )
    assert len(
        archive.read_market_event_batch_filtered(
            files[0],
            expected_checksum=repeated.checksum,
            symbol="BTCUSDT",
            event_types=("TRADE",),
            start_ts_ms=1_000,
            end_ts_ms=2_000,
        )
    ) == 2
    replay = StoredMarketReplay().run(
        ledger,
        source_run_id=runtime.run_id,
        created_ts_ms=3_000,
    )
    assert replay.event_count == 4
    ledger.close()


def test_archive_manifest_stats_and_candles_use_one_full_commit(tmp_path: Path) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-atomic",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "atomic-ledger.sqlite3",
        market_event_archive=archive,
    )
    run_id = "run-atomic-persistence"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    assert ledger._connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0
    event = market_event(run_id, event_id="atomic-event", ts_ms=1_000).model_dump(
        mode="json"
    )
    batch = archive.write_market_event_batch([event])
    statements: list[str] = []
    ledger._connection.set_trace_callback(statements.append)

    assert ledger.record_archives_and_candles(
        [(batch, [event])],
        [candle_row(run_id)],
    ) == (1, 1)

    assert sum(statement == "BEGIN IMMEDIATE" for statement in statements) == 1
    assert sum(statement == "COMMIT" for statement in statements) == 1
    assert ledger.count("market_event_archives") == 1
    assert ledger.count("candles") == 1
    assert ledger.market_event_symbols(run_id) == [
        {"symbol": "BTCUSDT", "event_count": 1}
    ]
    ledger.close()


def test_passive_checkpoint_runs_outside_commit_connection(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "passive-checkpoint.sqlite3")
    run_id = "run-passive-checkpoint"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    assert ledger.record_candles([candle_row(run_id)]) == 1

    busy, log_frames, checkpointed_frames = run_passive_wal_checkpoint_in_process(
        str(ledger.path)
    )

    assert busy == 0
    assert log_frames > 0
    assert checkpointed_frames == log_frames
    assert ledger.list_candles(
        run_id,
        symbol="BTCUSDT",
        interval_seconds=1,
    ) == [candle_row(run_id)]
    ledger.close()


def test_archive_and_full_commit_use_independent_connection(tmp_path: Path) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-process",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "process-ledger.sqlite3",
        market_event_archive=archive,
    )
    run_id = "run-process-persistence"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    event = market_event(run_id, event_id="process-event", ts_ms=1_000).model_dump(
        mode="json"
    )

    timings = persist_archives_and_candles_in_process(
        str(archive.root),
        archive.minimum_free_bytes,
        archive.minimum_free_ratio,
        str(ledger.path),
        [[event]],
        [candle_row(run_id)],
    )

    assert timings["archive_batches"] == 1
    assert float(timings["archive_ms"]) >= 0
    assert float(timings["ledger_ms"]) >= 0
    assert ledger.count("market_event_archives") == 1
    assert ledger.count("candles") == 1
    assert ledger.market_event_symbols(run_id) == [
        {"symbol": "BTCUSDT", "event_count": 1}
    ]
    ledger.close()


def test_atomic_persistence_rolls_back_manifest_and_stats_on_candle_fault(
    tmp_path: Path,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-rollback",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "rollback-ledger.sqlite3",
        market_event_archive=archive,
    )
    run_id = "run-atomic-rollback"
    ledger.start_run(
        run_id,
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    assert ledger.record_candles([candle_row(run_id)]) == 1
    event = market_event(run_id, event_id="rollback-event", ts_ms=1_000).model_dump(
        mode="json"
    )
    batch = archive.write_market_event_batch([event])

    with pytest.raises(LedgerInvariantError, match="중복 캔들 payload 불일치"):
        ledger.record_archives_and_candles(
            [(batch, [event])],
            [candle_row(run_id, close="101")],
        )

    assert ledger.count("market_event_archives") == 0
    assert ledger.market_event_symbols(run_id) == []
    assert ledger.count("candles") == 1
    ledger.close()


def test_market_archive_timeline_limit_stops_after_first_sufficient_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet-limited",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    ledger = SQLiteLedger(
        tmp_path / "limited-ledger.sqlite3",
        market_event_archive=archive,
    )
    ledger.start_run(
        "run-limited",
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    for index in range(3):
        event = market_event(
            "run-limited",
            event_id=f"limited-{index}",
            ts_ms=1_000 + index,
        ).model_dump(mode="json")
        batch = archive.write_market_event_batch([event])
        assert ledger.record_market_event_archive(batch, [event]) == 1
    original_read = archive.read_market_event_batch_filtered
    read_paths: list[Path] = []

    def counted_read(
        path: Path,
        *,
        expected_checksum: str,
        symbol: str | None = None,
        event_types: tuple[str, ...] = (),
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
    ) -> list[dict[str, object]]:
        read_paths.append(path)
        return original_read(
            path,
            expected_checksum=expected_checksum,
            symbol=symbol,
            event_types=event_types,
            start_ts_ms=start_ts_ms,
            end_ts_ms=end_ts_ms,
        )

    monkeypatch.setattr(archive, "read_market_event_batch_filtered", counted_read)
    events = ledger.list_market_events("run-limited", symbol="BTCUSDT", limit=1)

    assert [event["event_id"] for event in events] == ["limited-0"]
    assert len(read_paths) == 1
    ledger.close()


def test_market_archive_separates_runtime_partitions_and_replays_both(
    tmp_path: Path,
) -> None:
    archive = ParquetEventStore(
        tmp_path / "market-parquet",
        minimum_free_bytes=0,
        minimum_free_ratio=0,
    )
    first_rows = [
        {
            "run_id": "run-first",
            "event_id": "event-first",
            "venue": "BINANCE_USDM",
            "symbol": "BTCUSDT",
            "event_type": "TRADE",
            "venue_ts_ms": 1_000,
            "recv_ts_ms": 1_001,
            "data": {"price": "100"},
        }
    ]
    second_rows = [
        {
            **first_rows[0],
            "run_id": "run-second",
            "event_id": "event-second",
        }
    ]

    first = archive.write_market_event_batch(first_rows)
    second = archive.write_market_event_batch(second_rows)

    assert "run=RUN-FIRST" in first.path.parts
    assert "run=RUN-SECOND" in second.path.parts
    assert first.path.parent != second.path.parent
    assert archive.read_market_event_batch(
        first.path,
        expected_checksum=first.checksum,
    ) == first_rows
    assert archive.read_market_event_batch(
        second.path,
        expected_checksum=second.checksum,
    ) == second_rows


def test_runtime_persists_top10_book_without_mutating_live_event() -> None:
    event = market_event("run-top10", event_id="depth-top10", ts_ms=1_000)
    bids = [[str(100 - index / 10), "1"] for index in range(20)]
    asks = [[str(100.1 + index / 10), "1"] for index in range(20)]
    event.data["bids"] = bids
    event.data["asks"] = asks

    persisted = PaperRuntime._persistable_market_event(event)

    assert len(event.data["bids"]) == 20
    assert len(event.data["asks"]) == 20
    assert isinstance(persisted["data"], dict)
    assert len(persisted["data"]["bids"]) == 10
    assert len(persisted["data"]["asks"]) == 10
    assert persisted["data"]["bid"] == event.data["bid"]
    assert persisted["data"]["ask"] == event.data["ask"]
    expected = event.model_dump(mode="json")
    expected["data"]["bids"] = bids[:10]
    expected["data"]["asks"] = asks[:10]
    assert persisted == expected


def test_main_orders_fills_trade_and_shadow_trades_persist_from_real_engine(
    tmp_path: Path,
) -> None:
    plan = candidate_plan()
    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id=plan.run_id,
        venue=Venue.FIXTURE,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ledger.record_candidate(runtime._candidate_plan_row(plan))
    runtime.paper_portfolio.offer((plan,), entries_paused=False)
    runtime.paper_portfolio.on_book(book(1_250))
    runtime._persist_execution_state(1_250)
    runtime.paper_portfolio.on_book(book(1_500))
    runtime._persist_execution_state(1_500)

    tp1 = plan.take_profit_targets[0].price
    runtime.paper_portfolio.on_book(
        book(2_000, bids=((str(tp1 + Decimal("0.1")), "100"),), asks=(("107", "100"),))
    )
    runtime.paper_portfolio.on_book(
        book(2_250, bids=((str(tp1 + Decimal("0.05")), "100"),), asks=(("107", "100"),))
    )
    runtime._persist_execution_state(2_250)
    tp2 = plan.take_profit_targets[1].price
    runtime.paper_portfolio.on_book(
        book(3_000, bids=((str(tp2 + Decimal("0.1")), "100"),), asks=(("107", "100"),))
    )
    runtime.paper_portfolio.on_book(
        book(3_250, bids=((str(tp2 + Decimal("0.05")), "100"),), asks=(("107", "100"),))
    )
    runtime.paper_portfolio.on_book(
        book(3_750, bids=((str(tp2 + Decimal("0.05")), "100"),), asks=(("107", "100"),))
    )
    runtime.flush_storage()

    assert ledger.count("candidates") == 1
    assert ledger.count("paper_orders") == 3
    assert ledger.count("fills") == 3
    assert ledger.count("trades") == 1
    assert ledger.count("shadow_trades") == 2
    assert ledger.count("execution_audit") > 0
    trade = ledger.list_trades(runtime.run_id)[0]
    run = ledger.get_run(runtime.run_id)
    assert run is not None
    assert trade["config_hash"] == run["config_hash"]
    shadow_trades = ledger.list_shadow_trades(runtime.run_id)
    assert all(row["config_hash"] == run["config_hash"] for row in shadow_trades)
    assert all(row["strategy_version"] == STRATEGY_VERSION for row in shadow_trades)
    assert all(row["sample_type"] == "REPLAY" for row in shadow_trades)
    assert Decimal(str(trade["net_pnl_usdt"])) == (
        Decimal(str(trade["gross_pnl_usdt"]))
        - Decimal(str(trade["fees_usdt"]))
        - Decimal(str(trade["slippage_usdt"]))
    )
    ledger.close()


def test_strategy_reports_include_empty_profiles_costs_pf_expectancy_and_confidence() -> None:
    win = _sample_trade()
    loss = _sample_trade(
        "trade-loss",
        net="-0.50",
        gross="-0.20",
        fees="0.10",
        slippage="0.20",
    )
    stress = {**_sample_trade("trade-stress"), "profile": "STRESS"}
    reports = TradeAnalytics().strategy_reports(
        [win, loss, stress],
        strategy_ids=("LSA_REVERSAL_V1", "CBR_CONTINUATION_V1"),
    )
    assert len(reports) == 4
    base = next(
        report
        for report in reports
        if report["strategy_id"] == "LSA_REVERSAL_V1" and report["profile"] == "BASE"
    )
    assert base["sample_size"] == 2
    assert base["breakevens"] == 0
    assert base["win_rate"] == "0.5"
    assert base["profit_factor"] is not None
    assert base["expectancy_usdt"] == "0.4894"
    assert base["win_rate_ci95"] is not None
    assert base["sample_status"] == "표본 부족"
    assert base["recommendation"] == "관찰"
    assert base["stress_verified"] is True
    empty = next(
        report
        for report in reports
        if report["strategy_id"] == "CBR_CONTINUATION_V1"
        and report["profile"] == "STRESS"
    )
    assert empty["sample_size"] == 0
    assert empty["profit_factor"] is None


def test_runtime_strategy_analytics_do_not_double_count_shared_main_trade(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "strategy-analytics-ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-001",
        venue=Venue.FIXTURE,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    main_trade = _sample_trade()
    shadow_trade = {
        **_sample_trade("shadow-trade-001"),
        "shadow_trade_id": "shadow-trade-001",
        "closed_ts_ms": 2_100,
        "sample_type": "LIVE_PUBLIC",
        "strategy_version": STRATEGY_VERSION,
    }
    fixture_shadow_trade = {
        **_sample_trade("shadow-trade-fixture"),
        "shadow_trade_id": "shadow-trade-fixture",
        "closed_ts_ms": 2_200,
        "sample_type": "OFFLINE_FIXTURE",
        "strategy_version": STRATEGY_VERSION,
    }
    ledger.start_run(
        "run-prior-strategy",
        mode="LIVE_SHADOW_PAPER",
        venue="BINANCE_USDM",
        config={"strategy_version": "prior-version"},
        started_ts_ms=500,
    )
    prior_shadow_trade = {
        **_sample_trade("shadow-trade-prior"),
        "run_id": "run-prior-strategy",
        "shadow_trade_id": "shadow-trade-prior",
        "closed_ts_ms": 2_050,
        "sample_type": "LIVE_PUBLIC",
    }
    prior_shadow_trade.pop("strategy_version")
    ledger.record_trade(main_trade)
    ledger.record_shadow_trade(shadow_trade)
    ledger.record_shadow_trade(fixture_shadow_trade)
    ledger.record_shadow_trade(prior_shadow_trade)

    base = next(
        report
        for report in runtime.strategy_performance()
        if report["strategy_id"] == "LSA_REVERSAL_V1" and report["profile"] == "BASE"
    )
    symbol = runtime.strategy_symbol_performance()[0]

    assert base["sample_size"] == 1
    assert base["analysis_scope"] == "CURRENT_STRATEGY_VERSION"
    assert base["strategy_version"] == STRATEGY_VERSION
    assert base["excluded_prior_version_samples"] == 1
    assert symbol["sample_size"] == 1
    assert symbol["analysis_scope"] == "CURRENT_STRATEGY_VERSION"
    assert symbol["strategy_version"] == STRATEGY_VERSION
    assert symbol["excluded_prior_version_samples"] == 1
    enriched = ledger.list_shadow_trades("run-prior-strategy")[0]
    assert enriched["strategy_version"] == "prior-version"
    assert enriched["config_hash"]
    ledger.close()


def test_replay_and_strategy_analytics_are_connected_to_http_api(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "api-ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-api-replay",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="api-depth", ts_ms=1_000)
    )
    client = TestClient(create_app(runtime))

    runs = client.get("/api/replay/runs")
    assert runs.status_code == 200
    assert runs.json()[0]["run_id"] == runtime.run_id
    replay = client.post(f"/api/replay/{runtime.run_id}", json={})
    assert replay.status_code == 200
    assert replay.json()["event_count"] == 1
    timeline = client.get(f"/api/replay/{runtime.run_id}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["symbol"] == "BTCUSDT"
    assert timeline.json()["total_events"] == 1
    assert timeline.json()["events"][0]["event_id"] == "api-depth"
    assert timeline.json()["available_symbols"] == [
        {"symbol": "BTCUSDT", "event_count": 1}
    ]
    missing_timeline = client.get("/api/replay/unknown/timeline")
    assert missing_timeline.status_code == 404
    results = client.get("/api/replay/results")
    assert results.status_code == 200
    assert results.json()[0]["checksum"] == replay.json()["checksum"]
    analytics = client.get("/api/analytics/strategies")
    assert analytics.status_code == 200
    assert len(analytics.json()) == 20
    assert all(
        report["analysis_scope"] == "CURRENT_STRATEGY_VERSION" for report in analytics.json()
    )
    symbols = client.get("/api/analytics/strategy-symbols")
    assert symbols.status_code == 200
    assert symbols.json()["analysis_scope"] == "CURRENT_STRATEGY_VERSION"
    assert symbols.json()["strategy_version"] == STRATEGY_VERSION
    assert symbols.json()["excluded_prior_version_samples"] == 0
    ledger.close()


def test_live_http_replay_uses_isolated_process_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-api-ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-isolated-replay",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="isolated-depth", ts_ms=1_000)
    )
    calls: list[tuple[object, ...]] = []

    async def run_sync(function, *arguments):
        calls.append(arguments)
        return function(*arguments)

    monkeypatch.setattr(main_module.to_process, "run_sync", run_sync)
    response = TestClient(create_app(runtime)).post(
        f"/api/replay/{runtime.run_id}",
        json={},
    )

    assert response.status_code == 200
    assert response.json()["event_count"] == 1
    assert response.json()["real_orders_enabled"] is False
    assert len(calls) == 1
    assert calls[0][0] == str(ledger.path)
    ledger.close()


def test_live_timeline_and_focus_use_same_isolated_process_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-ui-replay-ledger.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-ui-replay",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    calls: list[tuple[str, tuple[object, ...]]] = []

    def timeline_stub(*arguments: object) -> dict[str, object]:
        calls.append(("timeline", arguments))
        return {
            "run_id": "run-live-ui-replay",
            "symbol": "BTCUSDT",
            "total_events": 1,
            "truncated": False,
            "available_symbols": [{"symbol": "BTCUSDT", "event_count": 1}],
            "events": [],
            "candles": [],
        }

    def focus_stub(*arguments: object) -> dict[str, object]:
        calls.append(("focus", arguments))
        return {"run_id": "run-live-ui-replay", "trade_id": "trade-live-focus"}

    async def run_sync(function, *arguments):
        return function(*arguments)

    monkeypatch.setattr(main_module, "replay_timeline_from_paths", timeline_stub)
    monkeypatch.setattr(main_module, "replay_focus_session_from_paths", focus_stub)
    monkeypatch.setattr(main_module.to_process, "run_sync", run_sync)
    client = TestClient(create_app(runtime))

    timeline = client.get("/api/replay/run-live-ui-replay/timeline?symbol=BTCUSDT")
    focus = client.get(
        "/api/replay/run-live-ui-replay/focus"
        "?trade_id=trade-live-focus&profile=BASE"
    )

    assert timeline.status_code == 200
    assert focus.status_code == 200
    assert [name for name, _ in calls] == ["timeline", "focus"]
    assert all(arguments[0] == str(ledger.path) for _, arguments in calls)
    ledger.close()


def test_live_replay_returns_busy_instead_of_hanging_other_ui_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "live-replay-busy.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-replay-busy",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    started = threading.Event()
    release = threading.Event()

    def timeline_stub(*_arguments: object) -> dict[str, object]:
        return {
            "run_id": runtime.run_id,
            "symbol": "BTCUSDT",
            "total_events": 0,
            "truncated": False,
            "available_symbols": [],
            "events": [],
            "candles": [],
        }

    async def blocking_run_sync(function, *arguments):
        started.set()
        await asyncio.to_thread(release.wait)
        return function(*arguments)

    monkeypatch.setattr(main_module, "replay_timeline_from_paths", timeline_stub)
    monkeypatch.setattr(main_module.to_process, "run_sync", blocking_run_sync)
    responses: list[object] = []

    with TestClient(create_app(runtime)) as client:
        worker = threading.Thread(
            target=lambda: responses.append(
                client.get(f"/api/replay/{runtime.run_id}/timeline")
            )
        )
        worker.start()
        assert started.wait(timeout=2)
        try:
            busy = client.post(f"/api/replay/{runtime.run_id}", json={})
            assert busy.status_code == 409
            assert busy.json()["detail"]["error_code"] == "REPLAY_BUSY"
            assert busy.json()["detail"]["retryable"] is True
        finally:
            release.set()
            worker.join(timeout=2)

    assert not worker.is_alive()
    assert responses and responses[0].status_code == 200
    ledger.close()


def test_live_scanner_uses_actual_strategy_decision_without_fake_score() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-live-scanner",
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )
    runtime.live_selection = ProviderSelection(
        venue=Venue.BINANCE_USDM,
        instruments={},
        tickers={},
        wide_symbols=("BTCUSDT",),
        deep_symbols=("BTCUSDT",),
        bootstrap_events=(),
    )
    runtime.ingest_live_event(
        market_event(runtime.run_id, event_id="scanner-depth", ts_ms=1_000)
    )

    scanner = runtime.dashboard()["scanner"]
    assert isinstance(scanner, list)
    assert len(scanner) == 1
    assert scanner[0]["symbol"] == "BTCUSDT"
    assert scanner[0]["score"] is None
    assert scanner[0]["status"] in {"QUALIFIED", "REJECTED"}
    assert scanner[0]["reason_codes"]
    assert scanner[0]["strategy"] != "WARMUP"
