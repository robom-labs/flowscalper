"""SQLite에 PAPER Run과 상태 전이를 불변 원장으로 저장한다."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any


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

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = FULL;
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
            self._connection.execute("PRAGMA user_version = 3")

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
                        raise LedgerInvariantError(
                            f"중복 시장 이벤트 payload 불일치: {row[0]}"
                        )
        return inserted

    def list_market_events(
        self,
        run_id: str,
        *,
        symbol: str | None = None,
        event_types: tuple[str, ...] = (),
        limit: int | None = None,
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
        query += " ORDER BY venue_ts_ms, receive_monotonic_ns, event_id"
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit은 양수여야 합니다.")
            query += " LIMIT ?"
            parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(query, tuple(parameters)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload_json = str(row["payload_json"])
            if hashlib.sha256(payload_json.encode()).hexdigest() != row["checksum"]:
                raise LedgerInvariantError("시장 이벤트 checksum 불일치로 리플레이를 차단했습니다.")
            decoded = json.loads(payload_json)
            if not isinstance(decoded, dict):
                raise LedgerInvariantError("시장 이벤트 payload는 객체여야 합니다.")
            result.append(decoded)
        return result

    def record_candles(self, candles: Sequence[Mapping[str, object]]) -> int:
        if not candles:
            return 0
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
        with self._transaction() as connection:
            self._assert_open_run(connection, run_id)
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
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload_json, checksum FROM candles
                WHERE run_id = ? AND symbol = ? AND interval_seconds = ?
                ORDER BY open_ts_ms
                """,
                (run_id, symbol, interval_seconds),
            ).fetchall()
        return self._verified_payload_rows(rows, "캔들")

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
                    _canonical_json(
                        {"reason_codes": candidate.get("reason_codes", [])}
                    ),
                    _canonical_json(candidate),
                ),
            )

    def record_strategy_setting(self, setting: Mapping[str, object]) -> None:
        self._record_versioned_payload(
            "strategy_settings",
            setting,
            identity_columns=("run_id", "strategy_id", "ts_ms"),
        )

    def list_strategy_settings(self, run_id: str) -> list[dict[str, Any]]:
        return self._verified_table_payloads(
            "strategy_settings", run_id, "ts_ms, setting_id"
        )

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
        query = "SELECT payload_json, checksum FROM shadow_trades"
        parameters: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            parameters = (run_id,)
        query += " ORDER BY closed_ts_ms, shadow_trade_id"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return self._verified_payload_rows(rows, "shadow 거래")

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

    def list_execution_audits(self, run_id: str) -> list[dict[str, Any]]:
        return self._verified_table_payloads(
            "execution_audit", run_id, "ts_ms, audit_id"
        )

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
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [json.loads(str(row["result_json"])) for row in rows]

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
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

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

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
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
            "candles",
            "strategy_settings",
            "strategy_account_snapshots",
            "shadow_trades",
            "execution_audit",
            "replay_runs",
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
                f"SELECT payload_json, checksum FROM {table} "
                f"WHERE run_id = ? ORDER BY {ordering}",
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
        self._connection.execute("BEGIN IMMEDIATE")
        return self._connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        try:
            self._connection.execute("COMMIT" if exc_type is None else "ROLLBACK")
        finally:
            self._lock.release()


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _optional_float(value: object | None) -> float | None:
    return None if value is None else float(str(value))
