"""SQLite에 PAPER Run과 상태 전이를 불변 원장으로 저장한다."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
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

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return dict(row) if row is not None else None

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
