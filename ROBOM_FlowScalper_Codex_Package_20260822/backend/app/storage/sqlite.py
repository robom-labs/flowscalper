"""SQLite에 PAPER Run과 상태 전이를 불변 원장으로 저장한다."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zlib
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any

from backend.app.storage.io_priority import storage_io_priority_gate
from backend.app.storage.parquet import ArchivedEventBatch, ParquetEventStore
from backend.app.storage.parquet import (
    _apply_background_io_policy as _apply_persistence_background_io_policy,
)
from backend.app.storage.parquet import (
    _set_background_io_policy as _set_persistence_background_io_policy,
)


class LedgerInvariantError(RuntimeError):
    """감사 가능한 PAPER 원장의 불변조건을 어긴 작업을 차단한다."""


@dataclass(frozen=True, slots=True)
class RecoveryState:
    run_id: str
    venue: str
    lifecycle_state: str
    payload: dict[str, object]
    transition_count: int
    recovered_ts_ms: int


class SQLiteLedger:
    """WAL과 명시적 트랜잭션으로 PAPER 상태를 보존한다."""

    def __init__(
        self,
        path: Path,
        *,
        market_event_archive: ParquetEventStore | None = None,
    ) -> None:
        self.path = path
        self.market_event_archive = market_event_archive
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._initialize()
        self._read_lock = RLock()
        self._read_connection = sqlite3.connect(
            path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._read_connection.row_factory = sqlite3.Row
        self._read_connection.execute("PRAGMA query_only = ON")
        self._replay_read_lock = RLock()
        self._replay_read_connection = sqlite3.connect(
            path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._replay_read_connection.row_factory = sqlite3.Row
        self._replay_read_connection.execute("PRAGMA query_only = ON")
        self._focus_read_lock = RLock()
        self._focus_read_connection = sqlite3.connect(
            path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._focus_read_connection.row_factory = sqlite3.Row
        self._focus_read_connection.execute("PRAGMA query_only = ON")
        cached_focus_rows = self._focus_read_connection.execute(
            """
            SELECT source_run_id, trade_id, profile, session_version,
                   checksum, payload_zlib
            FROM replay_focus_cache
            """
        ).fetchall()
        self._focus_memory_cache = {
            (
                str(row["source_run_id"]),
                str(row["trade_id"]),
                str(row["profile"]),
                int(str(row["session_version"])),
            ): (str(row["checksum"]), bytes(row["payload_zlib"]))
            for row in cached_focus_rows
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()
        with self._read_lock:
            self._read_connection.close()
        with self._replay_read_lock:
            self._replay_read_connection.close()
        with self._focus_read_lock:
            self._focus_read_connection.close()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = FULL;
                PRAGMA wal_autocheckpoint = 0;
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    started_ts_ms INTEGER NOT NULL,
                    finalized_ts_ms INTEGER,
                    summary_json TEXT,
                    CHECK (finalized_ts_ms IS NULL OR finalized_ts_ms >= started_ts_ms)
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_ts_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS universe_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    ts_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transitions (
                    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    sequence INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE (run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    lifecycle_state TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    checksum TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_orders (
                    order_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    trade_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_ts_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fills (
                    fill_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
                    payload_json TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    trade_id TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    updated_ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    gross_pnl TEXT NOT NULL,
                    fees TEXT NOT NULL,
                    slippage TEXT NOT NULL,
                    net_pnl TEXT NOT NULL,
                    mae_r REAL,
                    mfe_r REAL,
                    exit_ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS trades_focus_compare
                ON trades(run_id, strategy_id, symbol, exit_ts_ms, trade_id);
                CREATE INDEX IF NOT EXISTS trades_history_all_order
                ON trades(exit_ts_ms, trade_id);
                CREATE INDEX IF NOT EXISTS trades_history_run_order
                ON trades(run_id, exit_ts_ms, trade_id);
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    run_id TEXT REFERENCES runs(run_id),
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_locks (
                    lock_id TEXT PRIMARY KEY,
                    run_id TEXT REFERENCES runs(run_id),
                    reason_code TEXT NOT NULL,
                    active INTEGER NOT NULL CHECK (active IN (0, 1)),
                    ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS market_events (
                    event_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    venue_ts_ms INTEGER NOT NULL,
                    receive_monotonic_ns INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    PRIMARY KEY (run_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS market_events_replay_order
                ON market_events(run_id, venue_ts_ms, receive_monotonic_ns, event_id);
                CREATE INDEX IF NOT EXISTS market_events_symbol_type
                ON market_events(run_id, symbol, event_type, venue_ts_ms);
                CREATE TABLE IF NOT EXISTS market_event_stats (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    symbol TEXT NOT NULL,
                    event_count INTEGER NOT NULL CHECK (event_count >= 0),
                    first_ts_ms INTEGER,
                    last_ts_ms INTEGER,
                    count_complete INTEGER NOT NULL CHECK (count_complete IN (0, 1)),
                    PRIMARY KEY (run_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS market_event_archives (
                    batch_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    path TEXT NOT NULL,
                    event_count INTEGER NOT NULL CHECK (event_count > 0),
                    first_ts_ms INTEGER NOT NULL,
                    last_ts_ms INTEGER NOT NULL,
                    symbols_json TEXT NOT NULL,
                    event_types_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    UNIQUE (run_id, path),
                    CHECK (last_ts_ms >= first_ts_ms)
                );
                CREATE INDEX IF NOT EXISTS market_event_archives_run_order
                ON market_event_archives(run_id, first_ts_ms, batch_id);
                CREATE TABLE IF NOT EXISTS candles (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    symbol TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    open_ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    PRIMARY KEY (run_id, symbol, interval_seconds, open_ts_ms)
                );
                CREATE TABLE IF NOT EXISTS strategy_settings (
                    setting_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    strategy_id TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    checksum TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_account_snapshots (
                    account_snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    strategy_id TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    checksum TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_trades (
                    shadow_trade_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    strategy_id TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    closed_ts_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    checksum TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS shadow_trades_focus_compare
                ON shadow_trades(
                    run_id, strategy_id, closed_ts_ms, shadow_trade_id
                );
                CREATE INDEX IF NOT EXISTS shadow_trades_history_all_order
                ON shadow_trades(closed_ts_ms, shadow_trade_id);
                CREATE INDEX IF NOT EXISTS shadow_trades_history_run_order
                ON shadow_trades(run_id, closed_ts_ms, shadow_trade_id);
                CREATE TABLE IF NOT EXISTS execution_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    ts_ms INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    checksum TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS replay_runs (
                    replay_id TEXT PRIMARY KEY,
                    source_run_id TEXT NOT NULL REFERENCES runs(run_id),
                    created_ts_ms INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS replay_runs_source_latest
                ON replay_runs(source_run_id, created_ts_ms DESC, replay_id DESC);
                CREATE TABLE IF NOT EXISTS replay_focus_cache (
                    source_run_id TEXT NOT NULL REFERENCES runs(run_id),
                    trade_id TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    session_version INTEGER NOT NULL,
                    created_ts_ms INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    payload_zlib BLOB NOT NULL,
                    PRIMARY KEY (source_run_id, trade_id, profile, session_version)
                );

                CREATE TRIGGER IF NOT EXISTS finalized_run_is_immutable
                BEFORE UPDATE ON runs
                WHEN OLD.finalized_ts_ms IS NOT NULL
                BEGIN
                    SELECT RAISE(ABORT, 'finalized run is immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS finalized_run_cannot_be_deleted
                BEFORE DELETE ON runs
                BEGIN
                    SELECT RAISE(ABORT, 'run deletion is prohibited');
                END;
                CREATE TRIGGER IF NOT EXISTS trade_is_immutable_update
                BEFORE UPDATE ON trades
                BEGIN
                    SELECT RAISE(ABORT, 'trade is immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS trade_is_immutable_delete
                BEFORE DELETE ON trades
                BEGIN
                    SELECT RAISE(ABORT, 'trade deletion is prohibited');
                END;
                CREATE TRIGGER IF NOT EXISTS market_event_is_immutable_update
                BEFORE UPDATE ON market_events
                BEGIN
                    SELECT RAISE(ABORT, 'market event is immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS market_event_is_immutable_delete
                BEFORE DELETE ON market_events
                BEGIN
                    SELECT RAISE(ABORT, 'market event deletion is prohibited');
                END;
                CREATE TRIGGER IF NOT EXISTS market_event_archive_is_immutable_update
                BEFORE UPDATE ON market_event_archives
                BEGIN
                    SELECT RAISE(ABORT, 'market event archive is immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS market_event_archive_is_immutable_delete
                BEFORE DELETE ON market_event_archives
                BEGIN
                    SELECT RAISE(ABORT, 'market event archive deletion is prohibited');
                END;
                CREATE TRIGGER IF NOT EXISTS shadow_trade_is_immutable_update
                BEFORE UPDATE ON shadow_trades
                BEGIN
                    SELECT RAISE(ABORT, 'shadow trade is immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS shadow_trade_is_immutable_delete
                BEFORE DELETE ON shadow_trades
                BEGIN
                    SELECT RAISE(ABORT, 'shadow trade deletion is prohibited');
                END;
                """
            )
            self._migrate_market_event_identity()
            self._initialize_market_event_statistics()
            self._connection.execute("PRAGMA user_version = 7")

    @property
    def schema_version(self) -> int:
        with self._lock:
            row = self._connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def _migrate_market_event_identity(self) -> None:
        columns = self._connection.execute("PRAGMA table_info(market_events)").fetchall()
        primary_key = [
            str(row["name"])
            for row in sorted(columns, key=lambda row: int(row["pk"]))
            if int(row["pk"]) > 0
        ]
        if primary_key != ["event_id"]:
            return
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            DROP TRIGGER IF EXISTS market_event_is_immutable_update;
            DROP TRIGGER IF EXISTS market_event_is_immutable_delete;
            DROP INDEX IF EXISTS market_events_replay_order;
            DROP INDEX IF EXISTS market_events_symbol_type;
            ALTER TABLE market_events RENAME TO market_events_legacy_v2;
            CREATE TABLE market_events (
                event_id TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                venue TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL,
                venue_ts_ms INTEGER NOT NULL,
                receive_monotonic_ns INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                checksum TEXT NOT NULL,
                PRIMARY KEY (run_id, event_id)
            );
            INSERT OR IGNORE INTO market_events
            SELECT * FROM market_events_legacy_v2;
            DROP TABLE market_events_legacy_v2;
            CREATE INDEX market_events_replay_order
            ON market_events(run_id, venue_ts_ms, receive_monotonic_ns, event_id);
            CREATE INDEX market_events_symbol_type
            ON market_events(run_id, symbol, event_type, venue_ts_ms);
            CREATE TRIGGER market_event_is_immutable_update
            BEFORE UPDATE ON market_events
            BEGIN
                SELECT RAISE(ABORT, 'market event is immutable');
            END;
            CREATE TRIGGER market_event_is_immutable_delete
            BEFORE DELETE ON market_events
            BEGIN
                SELECT RAISE(ABORT, 'market event deletion is prohibited');
            END;
            PRAGMA user_version = 3;
            COMMIT;
            """
        )

    def _initialize_market_event_statistics(self) -> None:
        """기존 대용량 원장을 재계수하지 않고 배치 단위로 신규 이벤트를 집계한다."""

        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            INSERT OR IGNORE INTO market_event_stats (
                run_id, symbol, event_count, first_ts_ms, last_ts_ms, count_complete
            )
            SELECT r.run_id, '*', 0, NULL, NULL, 0
            FROM runs r
            WHERE EXISTS (
                SELECT 1 FROM market_events e WHERE e.run_id = r.run_id LIMIT 1
            )
              AND NOT EXISTS (
                SELECT 1 FROM market_event_stats s WHERE s.run_id = r.run_id
            );
            DROP TRIGGER IF EXISTS market_event_stats_insert;
            PRAGMA user_version = 5;
            COMMIT;
            """
        )

    def start_run(
        self,
        run_id: str,
        *,
        mode: str,
        venue: str,
        config: Mapping[str, object],
        started_ts_ms: int,
    ) -> None:
        config_json = _canonical_json(config)
        config_hash = hashlib.sha256(config_json.encode()).hexdigest()
        with self._transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, mode, venue, config_hash, config_json, started_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, mode, venue, config_hash, config_json, started_ts_ms),
                )
            except sqlite3.IntegrityError as error:
                raise LedgerInvariantError(f"Run 생성 실패: {run_id}") from error

    def set_app_setting(
        self,
        setting_key: str,
        value: Mapping[str, object],
        *,
        updated_ts_ms: int,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (setting_key, value_json, updated_ts_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_ts_ms = excluded.updated_ts_ms
                """,
                (setting_key, _canonical_json(value), updated_ts_ms),
            )

    def get_app_setting(self, setting_key: str) -> dict[str, Any] | None:
        with self._read_lock:
            row = self._read_connection.execute(
                "SELECT value_json, updated_ts_ms FROM app_settings WHERE setting_key = ?",
                (setting_key,),
            ).fetchone()
        if row is None:
            return None
        value = json.loads(str(row["value_json"]))
        if not isinstance(value, dict):
            raise LedgerInvariantError(f"앱 설정 payload가 객체가 아닙니다: {setting_key}")
        value["updated_ts_ms"] = int(row["updated_ts_ms"])
        return value

    def finalize_run(
        self,
        run_id: str,
        *,
        finalized_ts_ms: int,
        summary: Mapping[str, object],
    ) -> None:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET finalized_ts_ms = ?, summary_json = ?
                WHERE run_id = ? AND finalized_ts_ms IS NULL
                """,
                (finalized_ts_ms, _canonical_json(summary), run_id),
            )
            if cursor.rowcount != 1:
                raise LedgerInvariantError(f"열린 Run만 종료할 수 있습니다: {run_id}")

    def finalize_superseded_open_runs(
        self,
        *,
        finalized_ts_ms: int,
        reason: str,
    ) -> tuple[str, ...]:
        """새 Run 직전에 남은 과거 열린 Run을 삭제 없이 일괄 보존 종료한다."""

        summary_json = _canonical_json(
            {
                "reason": reason,
                "preserved": True,
                "recovered_as_superseded": True,
            }
        )
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT run_id FROM runs
                WHERE finalized_ts_ms IS NULL
                ORDER BY started_ts_ms, run_id
                """
            ).fetchall()
            run_ids = tuple(str(row["run_id"]) for row in rows)
            if run_ids:
                connection.execute(
                    """
                    UPDATE runs
                    SET finalized_ts_ms = ?, summary_json = ?
                    WHERE finalized_ts_ms IS NULL
                    """,
                    (finalized_ts_ms, summary_json),
                )
        return run_ids

    def append_transition(
        self,
        run_id: str,
        *,
        state: str,
        ts_ms: int,
        payload: Mapping[str, object],
    ) -> int:
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM transitions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            sequence = int(row[0])
            connection.execute(
                """
                INSERT INTO transitions (run_id, sequence, state, ts_ms, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, sequence, state, ts_ms, _canonical_json(payload)),
            )
            return sequence

    def save_snapshot(
        self,
        run_id: str,
        *,
        lifecycle_state: str,
        ts_ms: int,
        payload: Mapping[str, object],
    ) -> str:
        payload_json = _canonical_json(payload)
        checksum = hashlib.sha256(payload_json.encode()).hexdigest()
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.execute(
                """
                INSERT INTO snapshots (
                    run_id, lifecycle_state, ts_ms, payload_json, checksum
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, lifecycle_state, ts_ms, payload_json, checksum),
            )
        return checksum

    def record_order(self, order: Mapping[str, object]) -> None:
        run_id = str(order["run_id"])
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.execute(
                """
                INSERT INTO paper_orders (
                    order_id, run_id, trade_id, status, payload_json, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(order["order_id"]),
                    run_id,
                    str(order["trade_id"]),
                    str(order["status"]),
                    _canonical_json(order),
                    int(str(order["created_ts_ms"])),
                ),
            )

    def record_fill(self, fill: Mapping[str, object]) -> None:
        run_id = str(fill["run_id"])
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.execute(
                """
                INSERT INTO fills (fill_id, run_id, order_id, payload_json, ts_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(fill["fill_id"]),
                    run_id,
                    str(fill["order_id"]),
                    _canonical_json(fill),
                    int(str(fill["ts_ms"])),
                ),
            )

    def record_trade(self, trade: Mapping[str, object]) -> None:
        gross = _decimal(trade["gross_pnl_usdt"])
        fees = _decimal(trade["fees_usdt"])
        slippage = _decimal(trade["slippage_usdt"])
        net = _decimal(trade["net_pnl_usdt"])
        if gross - fees - slippage != net:
            raise LedgerInvariantError(f"순손익 불일치: {gross} - {fees} - {slippage} != {net}")
        run_id = str(trade["run_id"])
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.execute(
                """
                INSERT INTO trades (
                    trade_id, run_id, venue, symbol, strategy_id, regime, profile,
                    gross_pnl, fees, slippage, net_pnl, mae_r, mfe_r, exit_ts_ms,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(trade["trade_id"]),
                    run_id,
                    str(trade["venue"]),
                    str(trade["symbol"]),
                    str(trade["strategy_id"]),
                    str(trade.get("regime", "UNKNOWN")),
                    str(trade.get("profile", "BASE")),
                    str(gross),
                    str(fees),
                    str(slippage),
                    str(net),
                    _optional_float(trade.get("mae_r")),
                    _optional_float(trade.get("mfe_r")),
                    int(str(trade["exit_ts_ms"])),
                    _canonical_json(trade),
                ),
            )

    def record_market_events(self, events: Sequence[Mapping[str, object]]) -> int:
        if not events:
            return 0
        rows: list[tuple[object, ...]] = []
        run_ids = {str(event["run_id"]) for event in events}
        if len(run_ids) != 1:
            raise LedgerInvariantError("한 배치에 여러 Run의 시장 이벤트를 섞을 수 없습니다.")
        run_id = next(iter(run_ids))
        for event in events:
            payload_json = _canonical_json(event)
            rows.append(
                (
                    str(event["event_id"]),
                    run_id,
                    str(event["venue"]),
                    str(event["symbol"]),
                    str(event["event_type"]),
                    int(str(event["venue_ts_ms"])),
                    int(str(event["receive_monotonic_ns"])),
                    payload_json,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                )
            )
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO market_events (
                    event_id, run_id, venue, symbol, event_type, venue_ts_ms,
                    receive_monotonic_ns, payload_json, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            inserted = connection.total_changes - before
            if inserted != len(rows):
                for row in rows:
                    existing = connection.execute(
                        """
                        SELECT checksum FROM market_events
                        WHERE run_id = ? AND event_id = ?
                        """,
                        (row[1], row[0]),
                    ).fetchone()
                    if existing is None or str(existing["checksum"]) != str(row[-1]):
                        raise LedgerInvariantError(f"중복 시장 이벤트 payload 불일치: {row[0]}")
                self._rebuild_market_event_stats(connection, run_id)
            else:
                self._increment_market_event_stats(connection, rows)
        return inserted

    def record_market_event_archive(
        self,
        batch: ArchivedEventBatch,
        events: Sequence[Mapping[str, object]],
    ) -> int:
        """외장 Parquet 배치 manifest와 종목별 건수만 내장 SQLite에 기록한다."""

        run_id, manifest, stat_rows = self._prepare_market_event_archive(batch, events)
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            return self._insert_market_event_archive(
                connection,
                manifest=manifest,
                stat_rows=stat_rows,
            )

    def record_archives_and_candles(
        self,
        archives: Sequence[tuple[ArchivedEventBatch, Sequence[Mapping[str, object]]]],
        candles: Sequence[Mapping[str, object]],
    ) -> tuple[int, int]:
        """Parquet manifest·통계·캔들을 한 번의 FULL 커밋으로 원자 저장한다."""

        prepared_archives = [
            self._prepare_market_event_archive(batch, events) for batch, events in archives
        ]
        prepared_candles = self._prepare_candles(candles) if candles else None
        run_ids = {run_id for run_id, _, _ in prepared_archives}
        if prepared_candles is not None:
            run_ids.add(prepared_candles[0])
        if not run_ids:
            return (0, 0)
        if len(run_ids) != 1:
            raise LedgerInvariantError("한 영속화 커밋에 여러 Run의 데이터가 섞였습니다.")
        run_id = next(iter(run_ids))
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            archived = sum(
                self._insert_market_event_archive(
                    connection,
                    manifest=manifest,
                    stat_rows=stat_rows,
                )
                for _, manifest, stat_rows in prepared_archives
            )
            inserted_candles = (
                self._insert_candles(connection, prepared_candles[1])
                if prepared_candles is not None
                else 0
            )
        return (archived, inserted_candles)

    @staticmethod
    def _prepare_market_event_archive(
        batch: ArchivedEventBatch,
        events: Sequence[Mapping[str, object]],
    ) -> tuple[str, tuple[object, ...], list[tuple[object, ...]]]:
        """아카이브 입력을 트랜잭션 전에 검증하고 SQLite row로 변환한다."""

        if not events or batch.event_count != len(events):
            raise LedgerInvariantError("시장 이벤트 아카이브 배치 건수가 일치하지 않습니다.")
        run_ids = {str(event["run_id"]) for event in events}
        if len(run_ids) != 1:
            raise LedgerInvariantError("한 아카이브에 여러 Run을 섞을 수 없습니다.")
        run_id = next(iter(run_ids))
        timestamps = [int(str(event["venue_ts_ms"])) for event in events]
        symbols = sorted({str(event["symbol"]) for event in events})
        event_types = sorted({str(event["event_type"]) for event in events})
        path = str(batch.path.resolve())
        batch_id = hashlib.sha256(f"{run_id}\n{path}\n{batch.checksum}".encode()).hexdigest()
        manifest: tuple[object, ...] = (
            batch_id,
            run_id,
            path,
            batch.event_count,
            min(timestamps),
            max(timestamps),
            _canonical_json(symbols),
            _canonical_json(event_types),
            batch.checksum,
        )
        stat_rows: list[tuple[object, ...]] = [
            (
                str(event["event_id"]),
                run_id,
                str(event["venue"]),
                str(event["symbol"]),
                str(event["event_type"]),
                int(str(event["venue_ts_ms"])),
            )
            for event in events
        ]
        return run_id, manifest, stat_rows

    @classmethod
    def _insert_market_event_archive(
        cls,
        connection: sqlite3.Connection,
        *,
        manifest: tuple[object, ...],
        stat_rows: Sequence[tuple[object, ...]],
    ) -> int:
        before = connection.total_changes
        connection.execute(
            """
            INSERT OR IGNORE INTO market_event_archives (
                batch_id, run_id, path, event_count, first_ts_ms, last_ts_ms,
                symbols_json, event_types_json, checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            manifest,
        )
        inserted = connection.total_changes - before
        if inserted == 0:
            existing = connection.execute(
                """
                SELECT run_id, path, event_count, first_ts_ms, last_ts_ms,
                       symbols_json, event_types_json, checksum
                FROM market_event_archives WHERE batch_id = ?
                """,
                (manifest[0],),
            ).fetchone()
            if existing is None or tuple(existing) != manifest[1:]:
                raise LedgerInvariantError("중복 시장 아카이브 manifest 불일치")
            return 0
        cls._increment_market_event_stats(connection, stat_rows)
        return int(str(manifest[3]))

    @staticmethod
    def _increment_market_event_stats(
        connection: sqlite3.Connection,
        rows: Sequence[tuple[object, ...]],
    ) -> None:
        """외장 디스크의 row별 trigger 쓰기를 피해 종목별로 한 번만 집계한다."""

        aggregates: dict[tuple[str, str], list[int]] = {}
        for row in rows:
            key = (str(row[1]), str(row[3]))
            venue_ts_ms = int(str(row[5]))
            aggregate = aggregates.setdefault(key, [0, venue_ts_ms, venue_ts_ms])
            aggregate[0] += 1
            aggregate[1] = min(aggregate[1], venue_ts_ms)
            aggregate[2] = max(aggregate[2], venue_ts_ms)
        connection.executemany(
            """
            INSERT INTO market_event_stats (
                run_id, symbol, event_count, first_ts_ms, last_ts_ms, count_complete
            ) VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(run_id, symbol) DO UPDATE SET
                event_count = event_count + excluded.event_count,
                first_ts_ms = MIN(first_ts_ms, excluded.first_ts_ms),
                last_ts_ms = MAX(last_ts_ms, excluded.last_ts_ms)
            """,
            (
                (run_id, symbol, count, first_ts_ms, last_ts_ms)
                for (run_id, symbol), (count, first_ts_ms, last_ts_ms) in aggregates.items()
            ),
        )

    @staticmethod
    def _rebuild_market_event_stats(connection: sqlite3.Connection, run_id: str) -> None:
        """중복 삽입 경로에서만 해당 Run의 정확한 집계를 복구한다."""

        connection.execute("DELETE FROM market_event_stats WHERE run_id = ?", (run_id,))
        connection.execute(
            """
            INSERT INTO market_event_stats (
                run_id, symbol, event_count, first_ts_ms, last_ts_ms, count_complete
            )
            SELECT run_id, symbol, COUNT(*), MIN(venue_ts_ms), MAX(venue_ts_ms), 1
            FROM market_events
            WHERE run_id = ?
            GROUP BY run_id, symbol
            """,
            (run_id,),
        )

    def list_market_events(
        self,
        run_id: str,
        *,
        symbol: str | None = None,
        event_types: tuple[str, ...] = (),
        limit: int | None = None,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
        cooperative_yield: Callable[[], None] | None = None,
        archive_batch_yield: Callable[[int], None] | None = None,
        archive_batch_guard: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT payload_json, checksum FROM market_events WHERE run_id = ?"
        parameters: list[object] = [run_id]
        if symbol is not None:
            query += " AND symbol = ?"
            parameters.append(symbol)
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            query += f" AND event_type IN ({placeholders})"
            parameters.extend(event_types)
        if start_ts_ms is not None:
            query += " AND venue_ts_ms >= ?"
            parameters.append(start_ts_ms)
        if end_ts_ms is not None:
            query += " AND venue_ts_ms <= ?"
            parameters.append(end_ts_ms)
        query += " ORDER BY receive_monotonic_ns, venue_ts_ms, event_id"
        archive_query = """
            SELECT path, checksum, symbols_json, event_types_json,
                   first_ts_ms, last_ts_ms
            FROM market_event_archives WHERE run_id = ?
        """
        archive_parameters: list[object] = [run_id]
        if start_ts_ms is not None:
            archive_query += " AND last_ts_ms >= ?"
            archive_parameters.append(start_ts_ms)
        if end_ts_ms is not None:
            archive_query += " AND first_ts_ms <= ?"
            archive_parameters.append(end_ts_ms)
        archive_query += " ORDER BY first_ts_ms, batch_id"
        with self._read_lock:
            archive_rows = self._read_connection.execute(
                archive_query,
                tuple(archive_parameters),
            ).fetchall()
        if limit is not None and limit <= 0:
            raise ValueError("limit은 양수여야 합니다.")
        with self._read_lock:
            rows = self._read_connection.execute(query, tuple(parameters)).fetchall()
        result: list[dict[str, Any]] = []

        def event_sort_key(event: Mapping[str, object]) -> tuple[int, int, int, str]:
            return (
                _market_event_receive_ts_ms(event),
                int(str(event["receive_monotonic_ns"])),
                int(str(event["venue_ts_ms"])),
                str(event["event_id"]),
            )

        for index, row in enumerate(rows, start=1):
            payload_json = str(row["payload_json"])
            if hashlib.sha256(payload_json.encode()).hexdigest() != row["checksum"]:
                raise LedgerInvariantError("시장 이벤트 checksum 불일치로 리플레이를 차단했습니다.")
            decoded = json.loads(payload_json)
            if not isinstance(decoded, dict):
                raise LedgerInvariantError("시장 이벤트 payload는 객체여야 합니다.")
            result.append(decoded)
            if cooperative_yield is not None and index % 512 == 0:
                cooperative_yield()
        if archive_rows:
            if self.market_event_archive is None:
                raise LedgerInvariantError("시장 이벤트 아카이브 저장소가 없습니다.")
            try:
                for archive in archive_rows:
                    archived_symbols = json.loads(str(archive["symbols_json"]))
                    archived_event_types = json.loads(str(archive["event_types_json"]))
                    if symbol is not None and symbol not in archived_symbols:
                        continue
                    if event_types and not set(event_types).intersection(archived_event_types):
                        continue
                    filtered_archive_read = any(
                        value is not None for value in (symbol, start_ts_ms, end_ts_ms)
                    ) or bool(event_types)
                    archive_events = (
                        self.market_event_archive.read_market_event_batch_filtered(
                            Path(str(archive["path"])),
                            expected_checksum=str(archive["checksum"]),
                            symbol=symbol,
                            event_types=event_types,
                            start_ts_ms=start_ts_ms,
                            end_ts_ms=end_ts_ms,
                            batch_guard=archive_batch_guard,
                            cooperative_yield=cooperative_yield,
                        )
                        if filtered_archive_read
                        else self.market_event_archive.read_market_event_batch(
                            Path(str(archive["path"])),
                            expected_checksum=str(archive["checksum"]),
                            batch_guard=archive_batch_guard,
                            cooperative_yield=cooperative_yield,
                        )
                    )
                    for decoded in archive_events:
                        if str(decoded.get("run_id")) != run_id:
                            raise LedgerInvariantError(
                                "시장 이벤트 아카이브에 다른 Run이 섞였습니다."
                            )
                        if symbol is not None and str(decoded.get("symbol")) != symbol:
                            continue
                        if event_types and str(decoded.get("event_type")) not in event_types:
                            continue
                        venue_ts_ms = int(str(decoded["venue_ts_ms"]))
                        if start_ts_ms is not None and venue_ts_ms < start_ts_ms:
                            continue
                        if end_ts_ms is not None and venue_ts_ms > end_ts_ms:
                            continue
                        result.append(decoded)
                    if archive_batch_yield is not None:
                        archive_batch_yield(Path(str(archive["path"])).stat().st_size)
                    if cooperative_yield is not None:
                        cooperative_yield()
            except (OSError, ValueError) as error:
                raise LedgerInvariantError(f"시장 이벤트 아카이브 검증 실패: {error}") from error
        result.sort(key=event_sort_key)
        if cooperative_yield is not None:
            cooperative_yield()
        return result[:limit] if limit is not None else result

    def list_recent_market_events(
        self,
        run_id: str,
        *,
        symbol: str,
        limit: int,
        cooperative_yield: Callable[[], None] | None = None,
    ) -> list[dict[str, Any]]:
        """화면 미리보기용 최신 검증 이벤트만 최근 archive부터 제한해 읽는다."""

        if limit <= 0:
            raise ValueError("최근 시장 이벤트 개수는 양수여야 합니다.")
        read_target = limit
        with self._replay_read_lock:
            stored_rows = self._replay_read_connection.execute(
                """
                SELECT payload_json, checksum FROM market_events
                INDEXED BY market_events_replay_order
                WHERE run_id = ? AND symbol = ?
                ORDER BY venue_ts_ms DESC, receive_monotonic_ns DESC, event_id DESC
                LIMIT ?
                """,
                (run_id, symbol, read_target),
            ).fetchall()
            archive_rows = self._replay_read_connection.execute(
                """
                SELECT path, checksum FROM market_event_archives
                WHERE run_id = ?
                  AND EXISTS (
                    SELECT 1 FROM json_each(symbols_json) WHERE value = ?
                  )
                ORDER BY first_ts_ms DESC, batch_id DESC
                LIMIT ?
                """,
                (run_id, symbol, read_target),
            ).fetchall()
        result = self._verified_payload_rows(stored_rows, "최근 시장 이벤트")
        archived_count = 0
        if archive_rows:
            if self.market_event_archive is None:
                raise LedgerInvariantError("시장 이벤트 아카이브 저장소가 없습니다.")
            try:
                for archive in archive_rows:
                    archive_events = self.market_event_archive.read_market_event_batch_filtered(
                        Path(str(archive["path"])),
                        expected_checksum=str(archive["checksum"]),
                        symbol=symbol,
                        cooperative_yield=cooperative_yield,
                    )
                    for event in archive_events:
                        if str(event.get("run_id")) != run_id:
                            raise LedgerInvariantError(
                                "시장 이벤트 아카이브에 다른 Run이 섞였습니다."
                            )
                        if str(event.get("symbol")) != symbol:
                            raise LedgerInvariantError(
                                "최근 시장 이벤트 아카이브의 종목이 일치하지 않습니다."
                            )
                        result.append(event)
                    archived_count += len(archive_events)
                    if cooperative_yield is not None:
                        cooperative_yield()
                    if archived_count >= read_target:
                        break
            except (OSError, ValueError) as error:
                raise LedgerInvariantError(
                    f"최근 시장 이벤트 아카이브 검증 실패: {error}"
                ) from error

        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for event in result:
            identity = (str(event.get("run_id")), str(event.get("event_id")))
            existing = unique.get(identity)
            if existing is not None and existing != event:
                raise LedgerInvariantError(f"최근 시장 이벤트 중복 payload 불일치: {identity[1]}")
            unique[identity] = event
        ordered = sorted(
            unique.values(),
            key=lambda event: (
                _market_event_receive_ts_ms(event),
                int(str(event["receive_monotonic_ns"])),
                int(str(event["venue_ts_ms"])),
                str(event["event_id"]),
            ),
        )
        if cooperative_yield is not None:
            cooperative_yield()
        return ordered[-limit:]

    def market_event_symbols(self, run_id: str) -> list[dict[str, object]]:
        """대용량 본문을 스캔하지 않고 종목별 저장 상태를 반환한다."""

        with self._replay_read_lock:
            incomplete = self._replay_read_connection.execute(
                """
                SELECT 1 FROM market_event_stats
                WHERE run_id = ? AND count_complete = 0 LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if incomplete is None:
                rows = self._replay_read_connection.execute(
                    """
                    SELECT symbol, event_count
                    FROM market_event_stats
                    WHERE run_id = ? AND symbol != '*'
                    ORDER BY event_count DESC, symbol
                    """,
                    (run_id,),
                ).fetchall()
                return [
                    {
                        "symbol": str(row["symbol"]),
                        "event_count": int(row["event_count"]),
                    }
                    for row in rows
                ]
            rows = self._replay_read_connection.execute(
                """
                SELECT c.symbol, s.event_count
                FROM (
                    SELECT DISTINCT symbol FROM candles
                    WHERE run_id = ? AND interval_seconds = 1
                ) c
                LEFT JOIN market_event_stats s
                  ON s.run_id = ? AND s.symbol = c.symbol
                ORDER BY c.symbol
                """,
                (run_id, run_id),
            ).fetchall()
        return [
            {
                "symbol": str(row["symbol"]),
                "event_count": None,
                "new_event_count": int(row["event_count"] or 0),
            }
            for row in rows
        ]

    def record_candles(self, candles: Sequence[Mapping[str, object]]) -> int:
        if not candles:
            return 0
        run_id, rows = self._prepare_candles(candles)
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            return self._insert_candles(connection, rows)

    @staticmethod
    def _prepare_candles(
        candles: Sequence[Mapping[str, object]],
    ) -> tuple[str, list[tuple[object, ...]]]:
        run_ids = {str(candle["run_id"]) for candle in candles}
        if len(run_ids) != 1:
            raise LedgerInvariantError("한 배치에 여러 Run의 캔들을 섞을 수 없습니다.")
        run_id = next(iter(run_ids))
        rows: list[tuple[object, ...]] = []
        for candle in candles:
            payload_json = _canonical_json(candle)
            rows.append(
                (
                    run_id,
                    str(candle["symbol"]),
                    int(str(candle["interval_seconds"])),
                    int(str(candle["open_ts_ms"])),
                    payload_json,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                )
            )
        return run_id, rows

    @staticmethod
    def _insert_candles(
        connection: sqlite3.Connection,
        rows: Sequence[tuple[object, ...]],
    ) -> int:
        before = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO candles (
                run_id, symbol, interval_seconds, open_ts_ms, payload_json, checksum
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        inserted = connection.total_changes - before
        if inserted != len(rows):
            for row in rows:
                existing = connection.execute(
                    """
                    SELECT checksum FROM candles
                    WHERE run_id = ? AND symbol = ?
                      AND interval_seconds = ? AND open_ts_ms = ?
                    """,
                    row[:4],
                ).fetchone()
                if existing is None or str(existing["checksum"]) != str(row[-1]):
                    raise LedgerInvariantError(
                        f"중복 캔들 payload 불일치: {row[1]}/{row[2]}/{row[3]}"
                    )
        return inserted

    def list_candles(
        self,
        run_id: str,
        *,
        symbol: str,
        interval_seconds: int,
        start_ts_ms: int | None = None,
        end_ts_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT payload_json, checksum FROM candles
            WHERE run_id = ? AND symbol = ? AND interval_seconds = ?
        """
        parameters: list[object] = [run_id, symbol, interval_seconds]
        if start_ts_ms is not None:
            query += " AND open_ts_ms >= ?"
            parameters.append(start_ts_ms)
        if end_ts_ms is not None:
            query += " AND open_ts_ms <= ?"
            parameters.append(end_ts_ms)
        query += " ORDER BY open_ts_ms"
        with self._read_lock:
            rows = self._read_connection.execute(query, tuple(parameters)).fetchall()
        return self._verified_payload_rows(rows, "캔들")

    def list_recent_candles(
        self,
        run_id: str,
        *,
        symbol: str,
        interval_seconds: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        """미리보기용 최근 캔들만 읽고 시간순으로 반환한다."""

        if not 1 <= limit <= 2_000:
            raise ValueError("최근 캔들 개수는 1..2000 범위여야 합니다.")
        with self._replay_read_lock:
            rows = self._replay_read_connection.execute(
                """
                SELECT payload_json, checksum FROM candles
                WHERE run_id = ? AND symbol = ? AND interval_seconds = ?
                ORDER BY open_ts_ms DESC LIMIT ?
                """,
                (run_id, symbol, interval_seconds, limit),
            ).fetchall()
        verified = self._verified_payload_rows(rows, "캔들")
        verified.reverse()
        return verified

    def record_candidate(self, candidate: Mapping[str, object]) -> None:
        run_id = str(candidate["run_id"])
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO candidates (
                    candidate_id, run_id, ts_ms, status, reason_codes_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(candidate["candidate_id"]),
                    run_id,
                    int(str(candidate["signal_time_ms"])),
                    str(candidate.get("status", "ARMED")),
                    _canonical_json({"reason_codes": candidate.get("reason_codes", [])}),
                    _canonical_json(candidate),
                ),
            )

    def list_candidates(self, run_id: str) -> list[dict[str, Any]]:
        """전략·종목 분석이 사용할 불변 후보 계획을 시간순으로 읽는다."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload_json FROM candidates
                WHERE run_id = ? ORDER BY ts_ms, candidate_id
                """,
                (run_id,),
            ).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

    def get_candidate(self, run_id: str, candidate_id: str) -> dict[str, Any] | None:
        """거래 집중 재생에서 한 건의 불변 진입계획만 인덱스로 읽는다."""

        with self._read_lock:
            row = self._read_connection.execute(
                """
                SELECT payload_json FROM candidates
                WHERE run_id = ? AND candidate_id = ?
                """,
                (run_id, candidate_id),
            ).fetchone()
        if row is None:
            return None
        decoded = json.loads(str(row["payload_json"]))
        if not isinstance(decoded, dict):
            raise LedgerInvariantError("후보 계획 payload는 객체여야 합니다.")
        return decoded

    def record_universe_snapshot(self, snapshot: Mapping[str, object]) -> None:
        """deep 유니버스 회전 결과를 수정 불가능한 새 행으로 남긴다."""

        run_id = str(snapshot["run_id"])
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.execute(
                """
                INSERT INTO universe_snapshots (snapshot_id, run_id, ts_ms, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(snapshot["snapshot_id"]),
                    run_id,
                    int(str(snapshot["ts_ms"])),
                    _canonical_json(snapshot),
                ),
            )

    def list_universe_snapshots(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload_json FROM universe_snapshots
                WHERE run_id = ? ORDER BY ts_ms, snapshot_id
                """,
                (run_id,),
            ).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

    def record_strategy_setting(self, setting: Mapping[str, object]) -> None:
        self._record_versioned_payload(
            "strategy_settings",
            setting,
            identity_columns=("run_id", "strategy_id", "ts_ms"),
        )

    def record_strategy_migration_batch(
        self,
        migrations: Sequence[tuple[Mapping[str, object], str]],
    ) -> None:
        """복구 전략 setting과 대응 incident를 하나의 SQLite transaction에 저장한다."""

        if not migrations:
            return
        try:
            with self._transaction() as connection:
                for payload, category in migrations:
                    run_id = str(payload["run_id"])
                    strategy_id = str(payload["strategy_id"])
                    ts_ms = int(str(payload["ts_ms"]))
                    transition_id = payload.get("transition_id")
                    if transition_id is None or not str(transition_id).strip():
                        raise LedgerInvariantError("전략 migration transition_id가 없습니다.")
                    if not category.strip():
                        raise LedgerInvariantError("전략 migration incident category가 없습니다.")
                    self._assert_open_run(connection, run_id)
                    payload_json = _canonical_json(payload)
                    connection.execute(
                        """
                        INSERT INTO strategy_settings (
                            run_id, strategy_id, ts_ms, payload_json, checksum
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            strategy_id,
                            ts_ms,
                            payload_json,
                            hashlib.sha256(payload_json.encode()).hexdigest(),
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO incidents (
                            incident_id, run_id, severity, category, ts_ms, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(transition_id),
                            run_id,
                            "INFO",
                            category,
                            ts_ms,
                            payload_json,
                        ),
                    )
        except sqlite3.Error as error:
            raise LedgerInvariantError(
                "전략 migration batch를 원자적으로 저장하지 못했습니다."
            ) from error

    def list_strategy_settings(self, run_id: str) -> list[dict[str, Any]]:
        return self._verified_table_payloads("strategy_settings", run_id, "ts_ms, setting_id")

    def record_strategy_account_snapshot(self, snapshot: Mapping[str, object]) -> None:
        self._record_versioned_payload(
            "strategy_account_snapshots",
            snapshot,
            identity_columns=("run_id", "strategy_id", "profile", "ts_ms"),
        )

    def record_shadow_trade(self, trade: Mapping[str, object]) -> None:
        run_id = str(trade["run_id"])
        payload_json = _canonical_json(trade)
        checksum = hashlib.sha256(payload_json.encode()).hexdigest()
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO shadow_trades (
                    shadow_trade_id, run_id, strategy_id, profile, closed_ts_ms,
                    payload_json, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(trade["shadow_trade_id"]),
                    run_id,
                    str(trade["strategy_id"]),
                    str(trade["profile"]),
                    int(str(trade["closed_ts_ms"])),
                    payload_json,
                    checksum,
                ),
            )

    def list_shadow_trades(self, run_id: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT s.payload_json, s.checksum, r.config_hash, r.config_json
            FROM shadow_trades s
            JOIN runs r ON r.run_id = s.run_id
        """
        parameters: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE s.run_id = ?"
            parameters = (run_id,)
        query += " ORDER BY s.closed_ts_ms, s.shadow_trade_id"
        with self._read_lock:
            rows = self._read_connection.execute(query, parameters).fetchall()
        payloads = self._verified_payload_rows(rows, "shadow 거래")
        for row, payload in zip(rows, payloads, strict=True):
            payload.setdefault("config_hash", str(row["config_hash"]))
            if not payload.get("strategy_version"):
                config = json.loads(str(row["config_json"]))
                if isinstance(config, dict) and config.get("strategy_version"):
                    payload["strategy_version"] = str(config["strategy_version"])
        return payloads

    def record_execution_audits(self, audits: Sequence[Mapping[str, object]]) -> None:
        if not audits:
            return
        run_ids = {str(audit["run_id"]) for audit in audits}
        if len(run_ids) != 1:
            raise LedgerInvariantError("실행 감사 배치에 여러 Run을 섞을 수 없습니다.")
        run_id = next(iter(run_ids))
        rows: list[tuple[object, ...]] = []
        for audit in audits:
            payload_json = _canonical_json(audit)
            rows.append(
                (
                    run_id,
                    int(str(audit["ts_ms"])),
                    str(audit["event"]),
                    payload_json,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                )
            )
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.executemany(
                """
                INSERT INTO execution_audit (
                    run_id, ts_ms, event_type, payload_json, checksum
                ) VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def record_execution_state_batch(
        self,
        *,
        run_id: str,
        candidates: Sequence[Mapping[str, object]] = (),
        orders: Sequence[Mapping[str, object]],
        fills: Sequence[Mapping[str, object]],
        trades: Sequence[Mapping[str, object]],
        shadow_trades: Sequence[Mapping[str, object]],
        audits: Sequence[Mapping[str, object]],
        account_snapshots: Sequence[Mapping[str, object]],
        recovery_snapshot: Mapping[str, object] | None,
    ) -> None:
        """한 이벤트의 후보계획과 PAPER 실행상태를 단일 FULL 커밋으로 보존한다."""

        groups = (
            ("후보계획", candidates),
            ("주문", orders),
            ("체결", fills),
            ("main 거래", trades),
            ("shadow 거래", shadow_trades),
            ("실행 감사", audits),
            ("전략 계좌", account_snapshots),
        )
        if not any(rows for _, rows in groups) and recovery_snapshot is None:
            return
        for label, rows in groups:
            mixed_run_ids = {
                str(row.get("run_id", "")) for row in rows if str(row.get("run_id", "")) != run_id
            }
            if mixed_run_ids:
                raise LedgerInvariantError(
                    f"{label} 실행상태 배치에 다른 Run을 섞을 수 없습니다: {sorted(mixed_run_ids)}"
                )
        if recovery_snapshot is not None and str(recovery_snapshot.get("run_id", "")) != run_id:
            raise LedgerInvariantError("복구 snapshot에 다른 Run을 섞을 수 없습니다.")

        # 손익 불변조건과 JSON 직렬화를 BEGIN 전에 끝내 잠금 보유 시간을 줄인다.
        candidate_rows = [
            (
                str(candidate["candidate_id"]),
                run_id,
                int(str(candidate["signal_time_ms"])),
                str(candidate.get("status", "ARMED")),
                _canonical_json({"reason_codes": candidate.get("reason_codes", [])}),
                _canonical_json(candidate),
            )
            for candidate in candidates
        ]
        order_rows = [
            (
                str(order["order_id"]),
                run_id,
                str(order["trade_id"]),
                str(order["status"]),
                _canonical_json(order),
                int(str(order["created_ts_ms"])),
            )
            for order in orders
        ]
        fill_rows = [
            (
                str(fill["fill_id"]),
                run_id,
                str(fill["order_id"]),
                _canonical_json(fill),
                int(str(fill["ts_ms"])),
            )
            for fill in fills
        ]
        trade_rows: list[tuple[object, ...]] = []
        for trade in trades:
            gross = _decimal(trade["gross_pnl_usdt"])
            fees = _decimal(trade["fees_usdt"])
            slippage = _decimal(trade["slippage_usdt"])
            net = _decimal(trade["net_pnl_usdt"])
            if gross - fees - slippage != net:
                raise LedgerInvariantError(f"순손익 불일치: {gross} - {fees} - {slippage} != {net}")
            trade_rows.append(
                (
                    str(trade["trade_id"]),
                    run_id,
                    str(trade["venue"]),
                    str(trade["symbol"]),
                    str(trade["strategy_id"]),
                    str(trade.get("regime", "UNKNOWN")),
                    str(trade.get("profile", "BASE")),
                    str(gross),
                    str(fees),
                    str(slippage),
                    str(net),
                    _optional_float(trade.get("mae_r")),
                    _optional_float(trade.get("mfe_r")),
                    int(str(trade["exit_ts_ms"])),
                    _canonical_json(trade),
                )
            )

        shadow_rows: list[tuple[object, ...]] = []
        for trade in shadow_trades:
            payload_json = _canonical_json(trade)
            shadow_rows.append(
                (
                    str(trade["shadow_trade_id"]),
                    run_id,
                    str(trade["strategy_id"]),
                    str(trade["profile"]),
                    int(str(trade["closed_ts_ms"])),
                    payload_json,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                )
            )
        audit_rows: list[tuple[object, ...]] = []
        for audit in audits:
            payload_json = _canonical_json(audit)
            audit_rows.append(
                (
                    run_id,
                    int(str(audit["ts_ms"])),
                    str(audit["event"]),
                    payload_json,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                )
            )
        account_rows: list[tuple[object, ...]] = []
        for snapshot in account_snapshots:
            payload_json = _canonical_json(snapshot)
            account_rows.append(
                (
                    run_id,
                    str(snapshot["strategy_id"]),
                    str(snapshot["profile"]),
                    int(str(snapshot["ts_ms"])),
                    payload_json,
                    hashlib.sha256(payload_json.encode()).hexdigest(),
                )
            )
        recovery_row: tuple[object, ...] | None = None
        if recovery_snapshot is not None:
            payload = recovery_snapshot["payload"]
            payload_json = _canonical_json(payload)
            recovery_row = (
                run_id,
                str(recovery_snapshot["lifecycle_state"]),
                int(str(recovery_snapshot["ts_ms"])),
                payload_json,
                hashlib.sha256(payload_json.encode()).hexdigest(),
            )

        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.executemany(
                """
                INSERT OR IGNORE INTO candidates (
                    candidate_id, run_id, ts_ms, status, reason_codes_json, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                candidate_rows,
            )
            connection.executemany(
                """
                INSERT INTO paper_orders (
                    order_id, run_id, trade_id, status, payload_json, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                order_rows,
            )
            connection.executemany(
                """
                INSERT INTO fills (fill_id, run_id, order_id, payload_json, ts_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                fill_rows,
            )
            connection.executemany(
                """
                INSERT INTO trades (
                    trade_id, run_id, venue, symbol, strategy_id, regime, profile,
                    gross_pnl, fees, slippage, net_pnl, mae_r, mfe_r, exit_ts_ms,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                trade_rows,
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO shadow_trades (
                    shadow_trade_id, run_id, strategy_id, profile, closed_ts_ms,
                    payload_json, checksum
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                shadow_rows,
            )
            connection.executemany(
                """
                INSERT INTO execution_audit (
                    run_id, ts_ms, event_type, payload_json, checksum
                ) VALUES (?, ?, ?, ?, ?)
                """,
                audit_rows,
            )
            connection.executemany(
                """
                INSERT INTO strategy_account_snapshots (
                    run_id, strategy_id, profile, ts_ms, payload_json, checksum
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                account_rows,
            )
            if recovery_row is not None:
                connection.execute(
                    """
                    INSERT INTO snapshots (
                        run_id, lifecycle_state, ts_ms, payload_json, checksum
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    recovery_row,
                )

    def list_execution_audits(self, run_id: str) -> list[dict[str, Any]]:
        return self._verified_table_payloads("execution_audit", run_id, "ts_ms, audit_id")

    def record_replay_run(self, replay: Mapping[str, object]) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO replay_runs (
                    replay_id, source_run_id, created_ts_ms, checksum, result_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(replay["replay_id"]),
                    str(replay["source_run_id"]),
                    int(str(replay["created_ts_ms"])),
                    str(replay["checksum"]),
                    _canonical_json(replay),
                ),
            )

    def list_replay_runs(self, source_run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT result_json FROM replay_runs"
        parameters: tuple[str, ...] = ()
        if source_run_id is not None:
            query += " WHERE source_run_id = ?"
            parameters = (source_run_id,)
        query += " ORDER BY created_ts_ms, replay_id"
        with self._read_lock:
            rows = self._read_connection.execute(query, parameters).fetchall()
        return [json.loads(str(row["result_json"])) for row in rows]

    def list_latest_replay_runs(self) -> list[dict[str, Any]]:
        """화면 목록에는 Run별 가장 최근 검증 결과 한 건만 반환한다."""

        with self._read_lock:
            rows = self._read_connection.execute(
                """
                SELECT current.result_json
                FROM replay_runs current
                WHERE current.replay_id = (
                    SELECT latest.replay_id
                    FROM replay_runs latest
                    WHERE latest.source_run_id = current.source_run_id
                    ORDER BY latest.created_ts_ms DESC, latest.replay_id DESC
                    LIMIT 1
                )
                ORDER BY current.created_ts_ms, current.replay_id
                """
            ).fetchall()
        return [json.loads(str(row["result_json"])) for row in rows]

    def get_replay_focus_session(
        self,
        source_run_id: str,
        trade_id: str,
        profile: str,
        *,
        session_version: int = 1,
    ) -> dict[str, Any] | None:
        """완성된 거래 집중 재생 캐시를 checksum 검증 후 반환한다."""

        key = (source_run_id, trade_id, profile, session_version)
        with self._focus_read_lock:
            cached = self._focus_memory_cache.get(key)
        if cached is not None:
            checksum, payload_zlib = cached
            return self._decode_replay_focus_session(checksum, payload_zlib)
        query = """
            SELECT checksum, payload_zlib FROM replay_focus_cache
            WHERE source_run_id = ? AND trade_id = ? AND profile = ?
              AND session_version = ?
        """
        parameters = (source_run_id, trade_id, profile, session_version)
        if self._read_lock.acquire(blocking=False):
            try:
                row = self._read_connection.execute(query, parameters).fetchone()
            finally:
                self._read_lock.release()
        else:
            with self._focus_read_lock:
                row = self._focus_read_connection.execute(query, parameters).fetchone()
        if row is None:
            return None
        checksum = str(row["checksum"])
        payload_zlib = bytes(row["payload_zlib"])
        with self._focus_read_lock:
            self._focus_memory_cache[key] = (checksum, payload_zlib)
        return self._decode_replay_focus_session(checksum, payload_zlib)

    @staticmethod
    def _decode_replay_focus_session(
        checksum: str,
        payload_zlib: bytes,
    ) -> dict[str, Any]:
        """검증된 압축 집중 재생 캐시를 메모리 또는 SQLite에서 동일하게 해제한다."""

        try:
            payload = zlib.decompress(payload_zlib)
        except zlib.error as error:
            raise LedgerInvariantError("거래 집중 재생 캐시 압축이 손상되었습니다.") from error
        if hashlib.sha256(payload).hexdigest() != checksum:
            raise LedgerInvariantError("거래 집중 재생 캐시 checksum이 일치하지 않습니다.")
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise LedgerInvariantError("거래 집중 재생 캐시는 객체여야 합니다.")
        return decoded

    def record_replay_focus_session(
        self,
        session: Mapping[str, object],
        *,
        created_ts_ms: int,
    ) -> int:
        """결정적 집중 재생 결과를 압축해 한 번만 보존한다."""

        source_run_id = str(session["run_id"])
        trade_id = str(session["trade_id"])
        profile = str(session["profile"])
        session_version = int(str(session["session_version"]))
        payload = _canonical_json(session).encode()
        checksum = hashlib.sha256(payload).hexdigest()
        # checksum은 비압축 payload로 고정되므로 대형 세션도 빠른 균형 압축을 쓴다.
        compressed = zlib.compress(payload, level=6)
        with self._transaction() as connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO replay_focus_cache (
                    source_run_id, trade_id, profile, session_version,
                    created_ts_ms, checksum, payload_zlib
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_run_id,
                    trade_id,
                    profile,
                    session_version,
                    created_ts_ms,
                    checksum,
                    sqlite3.Binary(compressed),
                ),
            )
            inserted = connection.total_changes - before
            if inserted == 0:
                existing = connection.execute(
                    """
                    SELECT checksum FROM replay_focus_cache
                    WHERE source_run_id = ? AND trade_id = ? AND profile = ?
                      AND session_version = ?
                    """,
                    (source_run_id, trade_id, profile, session_version),
                ).fetchone()
                if existing is None or str(existing["checksum"]) != checksum:
                    raise LedgerInvariantError("거래 집중 재생 캐시가 기존 결과와 다릅니다.")
        with self._focus_read_lock:
            self._focus_memory_cache[(source_run_id, trade_id, profile, session_version)] = (
                checksum,
                compressed,
            )
        return inserted

    def remember_replay_focus_session(
        self,
        session: Mapping[str, object],
    ) -> None:
        """선택적 SQLite 캐시가 잠겨도 완성된 세션을 현재 실행 동안 재사용한다."""

        source_run_id = str(session["run_id"])
        trade_id = str(session["trade_id"])
        profile = str(session["profile"])
        session_version = int(str(session["session_version"]))
        payload = _canonical_json(session).encode()
        checksum = hashlib.sha256(payload).hexdigest()
        compressed = zlib.compress(payload, level=6)
        with self._focus_read_lock:
            self._focus_memory_cache[(source_run_id, trade_id, profile, session_version)] = (
                checksum,
                compressed,
            )

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT r.*,
                  (SELECT COUNT(*) FROM market_events e WHERE e.run_id = r.run_id)
                    AS market_event_count,
                  (SELECT COUNT(*) FROM trades t WHERE t.run_id = r.run_id)
                    AS trade_count,
                  (SELECT COUNT(*) FROM shadow_trades s WHERE s.run_id = r.run_id)
                    AS shadow_trade_count
                FROM runs r ORDER BY started_ts_ms DESC, run_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_open_run(self) -> dict[str, Any] | None:
        """복구 시도 전 가장 최근의 열린 Run 식별자만 조회한다."""

        with self._read_lock:
            row = self._read_connection.execute(
                """
                SELECT run_id, mode, venue, started_ts_ms
                FROM runs
                WHERE finalized_ts_ms IS NULL
                ORDER BY started_ts_ms DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row is not None else None

    def list_replayable_run_summaries(self) -> list[dict[str, Any]]:
        """시장 이벤트 본문 COUNT 없이 리플레이 가능한 Run을 빠르게 조회한다."""

        with self._read_lock:
            rows = self._read_connection.execute(
                """
                SELECT r.*,
                  CASE
                    WHEN EXISTS (
                      SELECT 1 FROM market_event_stats incomplete
                      WHERE incomplete.run_id = r.run_id
                        AND incomplete.count_complete = 0
                    ) THEN NULL
                    ELSE (
                      SELECT COALESCE(SUM(stats.event_count), 0)
                      FROM market_event_stats stats
                      WHERE stats.run_id = r.run_id AND stats.symbol != '*'
                    )
                  END AS market_event_count,
                  EXISTS (
                    SELECT 1 FROM market_event_stats any_stats
                    WHERE any_stats.run_id = r.run_id
                  ) AS has_market_events,
                  (SELECT COUNT(*) FROM trades t WHERE t.run_id = r.run_id)
                    AS trade_count,
                  (SELECT COUNT(*) FROM shadow_trades s WHERE s.run_id = r.run_id)
                    AS shadow_trade_count
                FROM runs r
                ORDER BY r.started_ts_ms DESC, r.run_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def record_incident(
        self,
        incident_id: str,
        *,
        run_id: str | None,
        severity: str,
        category: str,
        ts_ms: int,
        payload: Mapping[str, object],
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO incidents (
                    incident_id, run_id, severity, category, ts_ms, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (incident_id, run_id, severity, category, ts_ms, _canonical_json(payload)),
            )

    def list_incidents(self, *, category: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM incidents"
        parameters: tuple[str, ...] = ()
        if category is not None:
            query += " WHERE category = ?"
            parameters = (category,)
        query += " ORDER BY ts_ms, incident_id"
        with self._read_lock:
            rows = self._read_connection.execute(query, parameters).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(str(row["payload_json"])),
            }
            for row in rows
        ]

    def recover_latest(self, *, recovered_ts_ms: int) -> RecoveryState | None:
        with self._lock:
            run = self._connection.execute(
                """
                SELECT run_id, venue FROM runs
                WHERE finalized_ts_ms IS NULL
                ORDER BY started_ts_ms DESC LIMIT 1
                """
            ).fetchone()
            if run is None:
                return None
            snapshot = self._connection.execute(
                """
                SELECT lifecycle_state, payload_json, checksum FROM snapshots
                WHERE run_id = ? ORDER BY snapshot_id DESC LIMIT 1
                """,
                (run["run_id"],),
            ).fetchone()
            count_row = self._connection.execute(
                "SELECT COUNT(*) FROM transitions WHERE run_id = ?", (run["run_id"],)
            ).fetchone()
            if snapshot is None:
                lifecycle_state = "RUN_OPEN"
                payload: dict[str, object] = {}
            else:
                payload_json = str(snapshot["payload_json"])
                expected = hashlib.sha256(payload_json.encode()).hexdigest()
                if expected != snapshot["checksum"]:
                    raise LedgerInvariantError("스냅샷 checksum 불일치로 복구를 차단했습니다.")
                lifecycle_state = str(snapshot["lifecycle_state"])
                decoded = json.loads(payload_json)
                if not isinstance(decoded, dict):
                    raise LedgerInvariantError("스냅샷 payload는 객체여야 합니다.")
                payload = decoded
            return RecoveryState(
                run_id=str(run["run_id"]),
                venue=str(run["venue"]),
                lifecycle_state=lifecycle_state,
                payload=payload,
                transition_count=int(count_row[0]),
                recovered_ts_ms=recovered_ts_ms,
            )

    def list_trades(self, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT payload_json FROM trades"
        parameters: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            parameters = (run_id,)
        query += " ORDER BY exit_ts_ms, trade_id"
        with self._read_lock:
            rows = self._read_connection.execute(query, parameters).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

    def get_paper_trade(
        self,
        run_id: str,
        trade_id: str,
        profile: str,
    ) -> dict[str, Any] | None:
        """집중 재생은 전체 거래표를 훑지 않고 불변 거래 한 건만 읽는다."""

        with self._read_lock:
            main = self._read_connection.execute(
                """
                SELECT payload_json FROM trades
                WHERE run_id = ? AND trade_id = ? AND profile = ?
                """,
                (run_id, trade_id, profile),
            ).fetchone()
            if main is not None:
                decoded = json.loads(str(main["payload_json"]))
                if not isinstance(decoded, dict):
                    raise LedgerInvariantError("PAPER 거래 payload는 객체여야 합니다.")
                return decoded
            shadow = self._read_connection.execute(
                """
                SELECT payload_json, checksum FROM shadow_trades
                WHERE run_id = ? AND shadow_trade_id = ? AND profile = ?
                """,
                (run_id, trade_id, profile),
            ).fetchone()
        if shadow is None:
            return None
        return self._verified_payload_rows([shadow], "shadow 거래")[0]

    def list_comparable_paper_trades(
        self,
        run_id: str,
        *,
        strategy_id: str,
        symbol: str,
        side: str,
    ) -> list[dict[str, Any]]:
        """집중 재생의 BASE·STRESS 비교에 필요한 전략 행만 제한해 읽는다."""

        with self._read_lock:
            main_rows = self._read_connection.execute(
                """
                SELECT payload_json FROM trades
                WHERE run_id = ? AND strategy_id = ? AND symbol = ?
                ORDER BY exit_ts_ms, trade_id
                """,
                (run_id, strategy_id, symbol),
            ).fetchall()
            shadow_rows = self._read_connection.execute(
                """
                SELECT payload_json, checksum FROM shadow_trades
                WHERE run_id = ? AND strategy_id = ?
                  AND json_extract(payload_json, '$.symbol') = ?
                  AND json_extract(payload_json, '$.side') = ?
                ORDER BY closed_ts_ms, shadow_trade_id
                """,
                (run_id, strategy_id, symbol, side),
            ).fetchall()
        main = [json.loads(str(row["payload_json"])) for row in main_rows]
        shadow = self._verified_payload_rows(shadow_rows, "shadow 거래")
        return [
            row
            for row in [*main, *shadow]
            if str(row.get("symbol")) == symbol and str(row.get("side")) == side
        ]

    def list_transitions(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT sequence, state, ts_ms, payload_json
                FROM transitions WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "state": str(row["state"]),
                "ts_ms": int(row["ts_ms"]),
                "payload": json.loads(str(row["payload_json"])),
            }
            for row in rows
        ]

    def list_orders(self, run_id: str) -> list[dict[str, Any]]:
        return self._list_payloads("paper_orders", run_id, "created_ts_ms, order_id")

    def list_fills(self, run_id: str) -> list[dict[str, Any]]:
        return self._list_payloads("fills", run_id, "ts_ms, fill_id")

    def list_trade_fill_evidence(
        self,
        trade_keys: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """요청한 main 거래의 주문·체결만 strict batch join해 비용까지 대조한다."""

        requested_keys = sorted(
            {
                (str(run_id).strip(), str(trade_id).strip())
                for run_id, trade_id in trade_keys
                if str(run_id).strip() and str(trade_id).strip()
            }
        )
        if not requested_keys:
            return {}
        requested_json = _canonical_json(
            [
                {"run_id": run_id, "trade_id": trade_id}
                for run_id, trade_id in requested_keys
            ]
        )
        with self._read_lock:
            rows = self._read_connection.execute(
                """
                WITH requested AS (
                    SELECT
                        CAST(json_extract(value, '$.run_id') AS TEXT) AS run_id,
                        CAST(json_extract(value, '$.trade_id') AS TEXT) AS trade_id
                    FROM json_each(?)
                )
                SELECT
                    requested.run_id AS requested_run_id,
                    requested.trade_id AS requested_trade_id,
                    trades.run_id AS trade_run_id,
                    trades.trade_id AS stored_trade_id,
                    trades.payload_json AS trade_payload_json,
                    paper_orders.run_id AS order_run_id,
                    paper_orders.trade_id AS order_trade_id,
                    paper_orders.order_id,
                    paper_orders.status AS order_status,
                    paper_orders.created_ts_ms,
                    paper_orders.payload_json AS order_payload_json,
                    fills.run_id AS fill_run_id,
                    fills.order_id AS fill_order_id,
                    fills.fill_id,
                    fills.ts_ms AS fill_ts_ms,
                    fills.payload_json AS fill_payload_json,
                    (
                        SELECT COUNT(*)
                        FROM fills AS foreign_fills
                        WHERE foreign_fills.order_id = paper_orders.order_id
                          AND foreign_fills.run_id != paper_orders.run_id
                    ) AS foreign_run_fill_count
                FROM requested
                LEFT JOIN trades
                  ON trades.run_id = requested.run_id
                 AND trades.trade_id = requested.trade_id
                LEFT JOIN paper_orders
                  ON paper_orders.run_id = trades.run_id
                 AND paper_orders.trade_id = trades.trade_id
                LEFT JOIN fills
                  ON fills.run_id = paper_orders.run_id
                 AND fills.order_id = paper_orders.order_id
                ORDER BY
                    requested.run_id,
                    requested.trade_id,
                    paper_orders.created_ts_ms,
                    paper_orders.order_id,
                    fills.ts_ms,
                    fills.fill_id
                """,
                (requested_json,),
            ).fetchall()

        grouped: dict[tuple[str, str], dict[str, Any]] = {
            key: {
                "stored_trade": None,
                "orders": {},
                "fills": [],
                "fill_ids": set(),
            }
            for key in requested_keys
        }
        valid_order_statuses = {
            "CREATED",
            "PENDING_LATENCY",
            "PARTIALLY_FILLED",
            "FILLED",
            "CANCELED",
            "REJECTED",
            "FINALIZED",
        }
        valid_order_intents = {
            "ENTRY_IOC",
            "TAKE_PROFIT",
            "STOP_EXIT",
            "EDGE_DECAY_EXIT",
            "EMERGENCY_EXIT",
            "MANUAL_PAPER_EXIT",
        }
        try:
            for row in rows:
                key = (str(row["requested_run_id"]), str(row["requested_trade_id"]))
                bucket = grouped[key]
                stored_trade_id = row["stored_trade_id"]
                if stored_trade_id is None:
                    continue
                trade_run_id = str(row["trade_run_id"])
                if (trade_run_id, str(stored_trade_id)) != key:
                    raise LedgerInvariantError("main 거래 batch join 식별자가 요청 키와 다릅니다.")
                trade_payload = json.loads(str(row["trade_payload_json"]))
                if not isinstance(trade_payload, dict):
                    raise LedgerInvariantError("main 거래 payload는 객체여야 합니다.")
                if (
                    str(trade_payload.get("run_id", "")),
                    str(trade_payload.get("trade_id", "")),
                ) != key:
                    raise LedgerInvariantError("main 거래 payload 식별자가 저장 컬럼과 다릅니다.")
                previous_trade = bucket["stored_trade"]
                if previous_trade is not None and previous_trade != trade_payload:
                    raise LedgerInvariantError("main 거래 batch join 중 payload가 바뀌었습니다.")
                bucket["stored_trade"] = trade_payload

                order_id_value = row["order_id"]
                if order_id_value is None:
                    continue
                order_id = str(order_id_value)
                if int(row["foreign_run_fill_count"] or 0) != 0:
                    raise LedgerInvariantError(
                        f"다른 Run의 fill이 main 주문에 연결되어 있습니다: {order_id}"
                    )
                if (
                    str(row["order_run_id"]),
                    str(row["order_trade_id"]),
                ) != key:
                    raise LedgerInvariantError("main 주문이 요청 거래 경계를 벗어났습니다.")
                order_payload = json.loads(str(row["order_payload_json"]))
                if not isinstance(order_payload, dict):
                    raise LedgerInvariantError("main 주문 payload는 객체여야 합니다.")
                if (
                    str(order_payload.get("run_id", "")),
                    str(order_payload.get("trade_id", "")),
                    str(order_payload.get("order_id", "")),
                ) != (key[0], key[1], order_id):
                    raise LedgerInvariantError("main 주문 payload 식별자가 저장 컬럼과 다릅니다.")
                order: dict[str, Any] = {
                    "order_id": order_id,
                    "status": str(row["order_status"]),
                    "created_ts_ms": int(row["created_ts_ms"]),
                    "side": str(order_payload.get("side", "UNKNOWN")),
                    "intent": str(order_payload.get("intent", "UNKNOWN")),
                    "filled_qty": order_payload.get("filled_qty"),
                    "fill_count": 0,
                    "fill_quantity": Decimal(0),
                }
                if order["status"] not in valid_order_statuses:
                    raise LedgerInvariantError(
                        f"알 수 없는 main 주문 상태입니다: {order['status']}"
                    )
                if order["intent"] not in valid_order_intents:
                    raise LedgerInvariantError(
                        f"알 수 없는 main 주문 intent입니다: {order['intent']}"
                    )
                if order["side"] not in {"BUY", "SELL"}:
                    raise LedgerInvariantError(f"알 수 없는 main 주문 side입니다: {order['side']}")
                previous_order = bucket["orders"].get(order_id)
                if previous_order is not None:
                    order["fill_count"] = previous_order["fill_count"]
                    order["fill_quantity"] = previous_order["fill_quantity"]
                    if {
                        **previous_order,
                        "fill_count": 0,
                        "fill_quantity": Decimal(0),
                    } != {
                        **order,
                        "fill_count": 0,
                        "fill_quantity": Decimal(0),
                    }:
                        raise LedgerInvariantError(
                            "main 주문 batch join 중 payload가 바뀌었습니다."
                        )
                bucket["orders"][order_id] = order

                fill_id_value = row["fill_id"]
                if fill_id_value is None:
                    continue
                fill_id = str(fill_id_value)
                if fill_id in bucket["fill_ids"]:
                    raise LedgerInvariantError(f"main 거래에 fill이 중복 연결됐습니다: {fill_id}")
                if (
                    str(row["fill_run_id"]),
                    str(row["fill_order_id"]),
                ) != (key[0], order_id):
                    raise LedgerInvariantError(
                        "main fill이 요청 Run 또는 주문 경계를 벗어났습니다."
                    )
                fill_payload = json.loads(str(row["fill_payload_json"]))
                if not isinstance(fill_payload, dict):
                    raise LedgerInvariantError("main fill payload는 객체여야 합니다.")
                fill_ts_ms = int(row["fill_ts_ms"])
                if (
                    str(fill_payload.get("run_id", "")),
                    str(fill_payload.get("order_id", "")),
                    str(fill_payload.get("fill_id", "")),
                    int(str(fill_payload.get("ts_ms"))),
                ) != (key[0], order_id, fill_id, fill_ts_ms):
                    raise LedgerInvariantError("main fill payload 식별자가 저장 컬럼과 다릅니다.")
                if fill_ts_ms < order["created_ts_ms"]:
                    raise LedgerInvariantError("main fill 시각이 주문 생성시각보다 빠릅니다.")
                fill_side = str(fill_payload.get("side", ""))
                if fill_side not in {"BUY", "SELL"} or fill_side != order["side"]:
                    raise LedgerInvariantError("main fill side가 주문 side와 일치하지 않습니다.")
                price = _decimal(fill_payload["price"])
                quantity = _decimal(fill_payload["quantity"])
                fee = _decimal(fill_payload["fee_usdt"])
                slippage = _decimal(fill_payload["slippage_usdt"])
                if not price.is_finite() or price <= 0:
                    raise LedgerInvariantError("main fill 가격은 유한한 양수여야 합니다.")
                if not quantity.is_finite() or quantity <= 0:
                    raise LedgerInvariantError("main fill 수량은 유한한 양수여야 합니다.")
                if (
                    not fee.is_finite()
                    or not slippage.is_finite()
                    or fee < 0
                    or slippage < 0
                ):
                    raise LedgerInvariantError("main fill 비용은 음수일 수 없습니다.")
                bucket["fill_ids"].add(fill_id)
                order["fill_count"] += 1
                order["fill_quantity"] += quantity
                bucket["fills"].append(
                    {
                        "fill_id": fill_id,
                        "order_id": order_id,
                        "ts_ms": fill_ts_ms,
                        "side": fill_side,
                        "intent": order["intent"],
                        "price": str(fill_payload["price"]),
                        "quantity": str(fill_payload["quantity"]),
                        "fee_usdt": str(fill_payload["fee_usdt"]),
                        "slippage_usdt": str(fill_payload["slippage_usdt"]),
                    }
                )
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            raise LedgerInvariantError(f"main 거래 fill payload 검증 실패: {error}") from error

        evidence: dict[tuple[str, str], dict[str, Any]] = {}
        for key, bucket in grouped.items():
            trade = bucket["stored_trade"]
            orders = bucket["orders"]
            fills = sorted(
                bucket["fills"],
                key=lambda fill: (int(fill["ts_ms"]), str(fill["fill_id"])),
            )
            if trade is None:
                evidence[key] = {
                    "fill_evidence_state": "CURRENT_MAIN_NO_FILL",
                    "fill_evidence_reason_ko": (
                        "현재 공동 가상계좌 거래가 아직 원시 체결 원장에 "
                        "확정되지 않았습니다."
                    ),
                    "fills": [],
                }
                continue
            if not orders:
                evidence[key] = {
                    "fill_evidence_state": "LEGACY_UNAVAILABLE",
                    "fill_evidence_reason_ko": (
                        "과거 공동 가상계좌 기록에는 원시 체결 연결 정보가 없습니다."
                    ),
                    "fills": [],
                }
                continue
            if not fills:
                evidence[key] = {
                    "fill_evidence_state": "CURRENT_MAIN_NO_FILL",
                    "fill_evidence_reason_ko": (
                        "현재 공동 가상계좌 거래에 연결된 원시 체결이 없습니다."
                    ),
                    "fills": [],
                }
                continue
            for order_id, order in orders.items():
                if order["filled_qty"] is None:
                    raise LedgerInvariantError(
                        f"main 주문 filled_qty가 없습니다: {order_id}"
                    )
                filled_quantity = _decimal(order["filled_qty"])
                if not filled_quantity.is_finite() or filled_quantity < 0:
                    raise LedgerInvariantError(
                        f"main 주문 filled_qty가 유한한 비음수가 아닙니다: {order_id}"
                    )
                if filled_quantity != order["fill_quantity"]:
                    raise LedgerInvariantError(
                        "main 주문 filled_qty와 fill 수량 합계가 일치하지 않습니다: "
                        f"{order_id} {filled_quantity}!={order['fill_quantity']}"
                    )
            unexpected_filled_orders = [
                order_id
                for order_id, order in orders.items()
                if order["status"] in {"CREATED", "PENDING_LATENCY", "REJECTED"}
                and order["fill_count"] > 0
            ]
            if unexpected_filled_orders:
                raise LedgerInvariantError(
                    "미체결 상태 주문에 fill이 연결됐습니다: "
                    + ", ".join(sorted(unexpected_filled_orders))
                )
            missing_filled_orders = [
                order_id
                for order_id, order in orders.items()
                if order["status"] in {"FILLED", "PARTIALLY_FILLED", "FINALIZED"}
                and order["fill_count"] == 0
            ]
            if missing_filled_orders:
                raise LedgerInvariantError(
                    "체결 완료 주문의 fill이 누락됐습니다: "
                    + ", ".join(sorted(missing_filled_orders))
                )
            trade_side = str(trade.get("side", ""))
            if trade_side not in {"LONG", "SHORT"}:
                raise LedgerInvariantError(f"알 수 없는 main 거래 side입니다: {trade_side}")
            trade_quantity = _decimal(trade["quantity"])
            if not trade_quantity.is_finite() or trade_quantity <= 0:
                raise LedgerInvariantError("main 거래 수량은 유한한 양수여야 합니다.")
            trade_entry_ts_ms = int(str(trade["entry_ts_ms"]))
            trade_exit_ts_ms = int(str(trade["exit_ts_ms"]))
            if trade_exit_ts_ms < trade_entry_ts_ms:
                raise LedgerInvariantError("main 거래 종료시각이 진입시각보다 빠릅니다.")
            entry_fills = [fill for fill in fills if fill["intent"] == "ENTRY_IOC"]
            exit_fills = [fill for fill in fills if fill["intent"] != "ENTRY_IOC"]
            if not entry_fills or not exit_fills:
                raise LedgerInvariantError(
                    "완료 main 거래에는 진입 fill과 청산 fill이 모두 필요합니다."
                )
            expected_entry_side = "BUY" if trade_side == "LONG" else "SELL"
            expected_exit_side = "SELL" if trade_side == "LONG" else "BUY"
            if any(fill["side"] != expected_entry_side for fill in entry_fills):
                raise LedgerInvariantError("main 진입 fill 방향이 완료 거래 방향과 다릅니다.")
            if any(fill["side"] != expected_exit_side for fill in exit_fills):
                raise LedgerInvariantError("main 청산 fill 방향이 완료 거래 방향과 다릅니다.")
            entry_quantity = sum(
                (_decimal(fill["quantity"]) for fill in entry_fills),
                start=Decimal(0),
            )
            exit_quantity = sum(
                (_decimal(fill["quantity"]) for fill in exit_fills),
                start=Decimal(0),
            )
            if entry_quantity != trade_quantity or exit_quantity != trade_quantity:
                raise LedgerInvariantError(
                    "main 거래와 진입·청산 fill 수량이 일치하지 않습니다: "
                    f"trade {trade_quantity}, entry {entry_quantity}, exit {exit_quantity}"
                )
            entry_fill_times = [int(fill["ts_ms"]) for fill in entry_fills]
            exit_fill_times = [int(fill["ts_ms"]) for fill in exit_fills]
            if max(entry_fill_times) > min(exit_fill_times):
                raise LedgerInvariantError("main 청산 fill이 진입 fill보다 먼저 기록됐습니다.")
            if (
                min(entry_fill_times) != trade_entry_ts_ms
                or max(exit_fill_times) != trade_exit_ts_ms
            ):
                raise LedgerInvariantError(
                    "main 거래 진입·종료시각이 원시 fill 경계와 일치하지 않습니다."
                )
            fill_fees = sum(
                (_decimal(fill["fee_usdt"]) for fill in fills),
                start=Decimal(0),
            )
            fill_slippage = sum(
                (_decimal(fill["slippage_usdt"]) for fill in fills),
                start=Decimal(0),
            )
            trade_fees = _decimal(trade["fees_usdt"])
            trade_slippage = _decimal(trade["slippage_usdt"])
            if fill_fees != trade_fees or fill_slippage != trade_slippage:
                raise LedgerInvariantError(
                    "main 거래와 fill 비용 합계가 일치하지 않습니다: "
                    f"{key[0]}/{key[1]} fee {fill_fees}!={trade_fees}, "
                    f"slippage {fill_slippage}!={trade_slippage}"
                )
            evidence[key] = {
                "fill_evidence_state": "PRESENT",
                "fill_evidence_reason_ko": "원시 PAPER 체결과 거래 비용 합계를 확인했습니다.",
                "fills": fills,
            }
        return evidence

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._replay_read_lock:
            row = self._replay_read_connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def _list_payloads(self, table: str, run_id: str, ordering: str) -> list[dict[str, Any]]:
        allowed = {
            ("paper_orders", "created_ts_ms, order_id"),
            ("fills", "ts_ms, fill_id"),
        }
        if (table, ordering) not in allowed:
            raise ValueError("허용되지 않은 payload 조회입니다.")
        with self._lock:
            rows = self._connection.execute(
                f"SELECT payload_json FROM {table} WHERE run_id = ? ORDER BY {ordering}",
                (run_id,),
            ).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

    def count(self, table: str) -> int:
        allowed = {
            "app_settings",
            "runs",
            "universe_snapshots",
            "candidates",
            "transitions",
            "snapshots",
            "paper_orders",
            "fills",
            "positions",
            "trades",
            "incidents",
            "risk_locks",
            "market_events",
            "market_event_stats",
            "market_event_archives",
            "candles",
            "strategy_settings",
            "strategy_account_snapshots",
            "shadow_trades",
            "execution_audit",
            "replay_runs",
            "replay_focus_cache",
        }
        if table not in allowed:
            raise ValueError(f"허용되지 않은 테이블: {table}")
        with self._lock:
            row = self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])

    def _assert_open_run(self, connection: sqlite3.Connection, run_id: str) -> None:
        row = connection.execute(
            "SELECT finalized_ts_ms FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["finalized_ts_ms"] is not None:
            raise LedgerInvariantError(f"열린 Run이 아닙니다: {run_id}")

    def _transaction(self) -> _Transaction:
        return _Transaction(self._connection, self._lock)

    def _verified_payload_rows(
        self,
        rows: Sequence[sqlite3.Row],
        label: str,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            payload_json = str(row["payload_json"])
            if hashlib.sha256(payload_json.encode()).hexdigest() != row["checksum"]:
                raise LedgerInvariantError(f"{label} checksum 불일치")
            decoded = json.loads(payload_json)
            if not isinstance(decoded, dict):
                raise LedgerInvariantError(f"{label} payload는 객체여야 합니다.")
            result.append(decoded)
        return result

    def _verified_table_payloads(
        self,
        table: str,
        run_id: str,
        ordering: str,
    ) -> list[dict[str, Any]]:
        allowed = {
            ("strategy_settings", "ts_ms, setting_id"),
            ("execution_audit", "ts_ms, audit_id"),
        }
        if (table, ordering) not in allowed:
            raise ValueError("허용되지 않은 검증 payload 조회입니다.")
        with self._lock:
            rows = self._connection.execute(
                f"SELECT payload_json, checksum FROM {table} WHERE run_id = ? ORDER BY {ordering}",
                (run_id,),
            ).fetchall()
        return self._verified_payload_rows(rows, table)

    def _record_versioned_payload(
        self,
        table: str,
        payload: Mapping[str, object],
        *,
        identity_columns: tuple[str, ...],
    ) -> None:
        allowed = {
            (
                "strategy_settings",
                ("run_id", "strategy_id", "ts_ms"),
            ),
            (
                "strategy_account_snapshots",
                ("run_id", "strategy_id", "profile", "ts_ms"),
            ),
        }
        if (table, identity_columns) not in allowed:
            raise ValueError("허용되지 않은 버전 payload 저장입니다.")
        run_id = str(payload["run_id"])
        payload_json = _canonical_json(payload)
        columns = ", ".join((*identity_columns, "payload_json", "checksum"))
        placeholders = ", ".join("?" for _ in range(len(identity_columns) + 2))
        values = tuple(payload[column] for column in identity_columns) + (
            payload_json,
            hashlib.sha256(payload_json.encode()).hexdigest(),
        )
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
            connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                values,
            )


class _Transaction:
    def __init__(self, connection: sqlite3.Connection, lock: RLock) -> None:
        self._connection = connection
        self._lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self._lock.acquire()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._lock.release()
            raise
        return self._connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        try:
            if exc_type is not None:
                self._connection.execute("ROLLBACK")
                return
            try:
                self._connection.execute("COMMIT")
            except BaseException as commit_error:
                if self._connection.in_transaction:
                    try:
                        self._connection.execute("ROLLBACK")
                    except BaseException as rollback_error:
                        commit_error.add_note(
                            "COMMIT 실패 뒤 후속 ROLLBACK도 실패했습니다."
                        )
                        raise commit_error from rollback_error
                raise
        finally:
            self._lock.release()


def _apply_storage_worker_cpu_priority() -> None:
    """저장 child의 CPU를 LIVE 이벤트 처리보다 낮게 유지한다."""

    try:
        current_niceness = os.nice(0)
        if current_niceness < 10:
            os.nice(10 - current_niceness)
    except OSError:
        return


def run_passive_wal_checkpoint_in_process(
    path: str,
    apply_low_cpu_priority: bool = False,
    apply_background_io_priority: bool = True,
) -> tuple[int, int, int]:
    """COMMIT 호출자와 분리된 process에서 비차단 PASSIVE checkpoint를 실행한다."""

    if apply_low_cpu_priority:
        _apply_storage_worker_cpu_priority()
    if apply_background_io_priority:
        _apply_persistence_background_io_policy()
    else:
        _set_persistence_background_io_policy(False)
    with storage_io_priority_gate(path, exclusive=True):
        connection = sqlite3.connect(path, timeout=0.0, isolation_level=None)
        try:
            connection.execute("PRAGMA wal_autocheckpoint = 0")
            row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            if row is None or len(row) != 3:
                raise LedgerInvariantError("WAL checkpoint 결과 형식이 올바르지 않습니다.")
            return (int(row[0]), int(row[1]), int(row[2]))
        finally:
            connection.close()


def persist_archives_and_candles_in_process(
    archive_root: str,
    minimum_free_bytes: int,
    minimum_free_ratio: float,
    ledger_path: str,
    market_groups: list[list[dict[str, object]]],
    candles: list[dict[str, object]],
    apply_low_cpu_priority: bool = False,
) -> dict[str, float | int]:
    """Parquet 작성과 FULL SQLite 커밋을 시장 처리 프로세스 밖에서 끝낸다."""

    import time

    if apply_low_cpu_priority:
        _apply_storage_worker_cpu_priority()
    gate_started = time.perf_counter()
    with storage_io_priority_gate(ledger_path, exclusive=True):
        gate_wait_ms = (time.perf_counter() - gate_started) * 1_000
        _apply_persistence_background_io_policy()
        archive_started = time.perf_counter()
        store = ParquetEventStore(
            Path(archive_root),
            minimum_free_bytes=minimum_free_bytes,
            minimum_free_ratio=minimum_free_ratio,
        )
        archive_records: list[tuple[ArchivedEventBatch, list[dict[str, object]]]] = []
        for rows in market_groups:
            archive_records.append((store.write_market_event_batch(rows), rows))
        archive_ms = (time.perf_counter() - archive_started) * 1_000

        prepared_archives = [
            SQLiteLedger._prepare_market_event_archive(batch, events)
            for batch, events in archive_records
        ]
        prepared_candles = SQLiteLedger._prepare_candles(candles) if candles else None
        run_ids = {run_id for run_id, _, _ in prepared_archives}
        if prepared_candles is not None:
            run_ids.add(prepared_candles[0])
        if not run_ids:
            return {
                "gate_wait_ms": gate_wait_ms,
                "archive_ms": archive_ms,
                "ledger_ms": 0.0,
                "ledger_connect_ms": 0.0,
                "ledger_begin_wait_ms": 0.0,
                "ledger_write_ms": 0.0,
                "ledger_commit_ms": 0.0,
                "ledger_close_ms": 0.0,
                "archive_batches": len(archive_records),
                "wal_probe_ms": 0.0,
                "wal_log_frames": -1,
                "wal_checkpointed_frames": -1,
                "wal_page_size": 4_096,
            }
        if len(run_ids) != 1:
            raise LedgerInvariantError("한 영속화 커밋에 여러 Run의 데이터가 섞였습니다.")
        run_id = next(iter(run_ids))

        ledger_started = time.perf_counter()
        ledger_connect_ms = 0.0
        ledger_begin_wait_ms = 0.0
        ledger_write_ms = 0.0
        ledger_commit_ms = 0.0
        ledger_close_ms = 0.0
        wal_probe_ms = 0.0
        wal_log_frames = -1
        wal_checkpointed_frames = -1
        wal_page_size = 4_096
        foreground_commit = _set_persistence_background_io_policy(False)
        try:
            connect_started = time.perf_counter()
            connection = sqlite3.connect(ledger_path, timeout=60.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            try:
                connection.executescript(
                    """
                    PRAGMA foreign_keys = ON;
                    PRAGMA synchronous = FULL;
                    PRAGMA wal_autocheckpoint = 0;
                    PRAGMA busy_timeout = 60000;
                    """
                )
                mode = connection.execute("PRAGMA journal_mode").fetchone()
                if mode is None or str(mode[0]).lower() != "wal":
                    raise LedgerInvariantError("분리 영속화 연결의 journal_mode가 WAL이 아닙니다.")
                ledger_connect_ms = (time.perf_counter() - connect_started) * 1_000
                begin_started = time.perf_counter()
                connection.execute("BEGIN IMMEDIATE")
                ledger_begin_wait_ms = (time.perf_counter() - begin_started) * 1_000
                try:
                    write_started = time.perf_counter()
                    run = connection.execute(
                        "SELECT finalized_ts_ms FROM runs WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()
                    if run is None or run["finalized_ts_ms"] is not None:
                        raise LedgerInvariantError(f"열린 Run이 아닙니다: {run_id}")
                    for _, manifest, stat_rows in prepared_archives:
                        SQLiteLedger._insert_market_event_archive(
                            connection,
                            manifest=manifest,
                            stat_rows=stat_rows,
                        )
                    if prepared_candles is not None:
                        SQLiteLedger._insert_candles(connection, prepared_candles[1])
                    ledger_write_ms = (time.perf_counter() - write_started) * 1_000
                    commit_started = time.perf_counter()
                    connection.execute("COMMIT")
                    ledger_commit_ms = (time.perf_counter() - commit_started) * 1_000
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
                wal_probe_started = time.perf_counter()
                wal_row = connection.execute("PRAGMA wal_checkpoint(NOOP)").fetchone()
                if wal_row is None or len(wal_row) != 3:
                    raise LedgerInvariantError("WAL NOOP probe 결과 형식이 올바르지 않습니다.")
                page_size_row = connection.execute("PRAGMA page_size").fetchone()
                if page_size_row is None:
                    raise LedgerInvariantError("SQLite page_size를 읽지 못했습니다.")
                wal_log_frames = int(wal_row[1])
                wal_checkpointed_frames = int(wal_row[2])
                wal_page_size = int(page_size_row[0])
                wal_probe_ms = (time.perf_counter() - wal_probe_started) * 1_000
            finally:
                close_started = time.perf_counter()
                connection.close()
                ledger_close_ms = (time.perf_counter() - close_started) * 1_000
        finally:
            if foreground_commit:
                _set_persistence_background_io_policy(True)
        return {
            "gate_wait_ms": gate_wait_ms,
            "archive_ms": archive_ms,
            "ledger_ms": (time.perf_counter() - ledger_started) * 1_000,
            "ledger_connect_ms": ledger_connect_ms,
            "ledger_begin_wait_ms": ledger_begin_wait_ms,
            "ledger_write_ms": ledger_write_ms,
            "ledger_commit_ms": ledger_commit_ms,
            "ledger_close_ms": ledger_close_ms,
            "archive_batches": len(archive_records),
            "wal_probe_ms": wal_probe_ms,
            "wal_log_frames": wal_log_frames,
            "wal_checkpointed_frames": wal_checkpointed_frames,
            "wal_page_size": wal_page_size,
        }


def _market_event_receive_ts_ms(event: Mapping[str, object]) -> int:
    venue_ts_ms = int(str(event["venue_ts_ms"]))
    explicit = event.get("receive_ts_ms")
    if explicit is not None:
        receive_ts_ms = int(str(explicit))
    else:
        quality = event.get("quality")
        lag_value = quality.get("lag_ms", 0) if isinstance(quality, Mapping) else 0
        try:
            lag_ms = max(0, round(float(lag_value)))
        except (OverflowError, TypeError, ValueError):
            lag_ms = 0
        receive_ts_ms = venue_ts_ms + lag_ms
    if receive_ts_ms < venue_ts_ms:
        raise LedgerInvariantError("시장 이벤트 수신시각이 거래소 시각보다 빠릅니다.")
    return receive_ts_ms


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _optional_float(value: object | None) -> float | None:
    return None if value is None else float(str(value))
