"""영속 원장·복구·리플레이·성과 집계의 불변조건을 검증한다."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from backend.app.analytics.reports import TradeAnalytics
from backend.app.exports.run_exporter import RunExporter
from backend.app.replay.engine import ReplayEngine, ReplayIntegrityError
from backend.app.storage.parquet import DiskUsage, ParquetEventStore, StoragePressureError
from backend.app.storage.sqlite import LedgerInvariantError, SQLiteLedger


@pytest.mark.parametrize(
    "lifecycle_state",
    ["CANDIDATE_CREATED", "ENTRY_PARTIALLY_FILLED", "PROTECTION_CREATED", "EXIT_PENDING"],
)
def test_restart_recovers_every_nonfinal_lifecycle_state(
    tmp_path: Path, lifecycle_state: str
) -> None:
    database = tmp_path / "ledger.sqlite3"
    ledger = SQLiteLedger(database)
    ledger.start_run(
        "run-recovery",
        mode="FIXTURE_OFFLINE",
        venue="FIXTURE",
        config={"risk": "0.10%", "seed": 17},
        started_ts_ms=1_000,
    )
    ledger.append_transition(
        "run-recovery", state=lifecycle_state, ts_ms=1_100, payload={"trade_id": "trade-001"}
    )
    ledger.save_snapshot(
        "run-recovery",
        lifecycle_state=lifecycle_state,
        ts_ms=1_100,
        payload={"trade_id": "trade-001", "venue": "FIXTURE", "quantity": "0.869"},
    )
    ledger.close()

    reopened = SQLiteLedger(database)
    recovered = reopened.recover_latest(recovered_ts_ms=2_000)

    assert recovered is not None
    assert recovered.run_id == "run-recovery"
    assert recovered.venue == "FIXTURE"
    assert recovered.lifecycle_state == lifecycle_state
    assert recovered.payload["trade_id"] == "trade-001"
    assert recovered.transition_count == 1
    reopened.close()


def test_completed_trade_accounting_and_finalized_run_are_immutable(tmp_path: Path) -> None:
    ledger = _open_run(tmp_path)
    trade = _sample_trade()
    ledger.record_order(
        {
            "order_id": "order-001",
            "run_id": "run-001",
            "trade_id": "trade-001",
            "status": "FILLED",
            "created_ts_ms": 1_100,
        }
    )
    ledger.record_fill(
        {
            "fill_id": "fill-001",
            "run_id": "run-001",
            "order_id": "order-001",
            "price": "100.10",
            "quantity": "1",
            "ts_ms": 1_200,
        }
    )
    ledger.record_trade(trade)
    ledger.finalize_run("run-001", finalized_ts_ms=2_100, summary={"net_pnl_usdt": "1.4788"})

    assert ledger.count("trades") == 1
    assert ledger.list_trades("run-001") == [trade]
    with pytest.raises(LedgerInvariantError):
        ledger.append_transition(
            "run-001", state="ILLEGAL_REOPEN", ts_ms=2_200, payload={"reason": "test"}
        )
    with pytest.raises(LedgerInvariantError):
        ledger.finalize_run("run-001", finalized_ts_ms=2_300, summary={})
    ledger.close()


def test_corrupt_snapshot_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    ledger = _open_run(tmp_path)
    ledger.save_snapshot(
        "run-001", lifecycle_state="PROTECTION_CREATED", ts_ms=1_500, payload={"stop": "99.55"}
    )
    ledger.close()
    connection = sqlite3.connect(database)
    connection.execute("UPDATE snapshots SET payload_json = ?", ('{"stop":"0"}',))
    connection.commit()
    connection.close()

    reopened = SQLiteLedger(database)
    with pytest.raises(LedgerInvariantError, match="checksum"):
        reopened.recover_latest(recovered_ts_ms=2_000)
    reopened.close()


def test_parquet_partitions_retention_and_duckdb_metrics(tmp_path: Path) -> None:
    store = ParquetEventStore(tmp_path / "parquet", minimum_free_bytes=0, minimum_free_ratio=0)
    old_ts = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * 1000)
    market_path = store.write_events(
        venue="BINANCE_USDM",
        symbol="BTCUSDT",
        event_type="deep_book",
        rows=[{"ts_ms": old_ts, "bid": 100.0, "ask": 100.1}],
    )
    protected_path = store.write_events(
        venue="BINANCE_USDM",
        symbol="BTCUSDT",
        event_type="trade_window",
        rows=[{"ts_ms": old_ts, "trade_id": "trade-001", "mid": 100.05}],
    )
    table = pq.read_table(market_path)
    assert table.num_rows == 1
    assert "venue=BINANCE_USDM" in str(market_path)
    assert "event_type=DEEP_BOOK" in str(market_path)
    event_counts = TradeAnalytics().parquet_event_counts(store.dataset_files())
    assert event_counts == [
        {
            "venue": "BINANCE_USDM",
            "symbol": "BTCUSDT",
            "event_type": "DEEP_BOOK",
            "event_count": 1,
        },
        {
            "venue": "BINANCE_USDM",
            "symbol": "BTCUSDT",
            "event_type": "TRADE_WINDOW",
            "event_count": 1,
        },
    ]

    removed = store.apply_retention(now=datetime(2026, 8, 22, tzinfo=UTC))
    assert market_path in removed
    assert not market_path.exists()
    assert protected_path.exists()

    report = TradeAnalytics().report(
        [
            _sample_trade(),
            _sample_trade("trade-002", net="-0.50", gross="-0.20", fees="0.10", slippage="0.20"),
        ],
        starting_equity=Decimal("1000"),
    )
    assert report["sample_size"] == 2
    assert Decimal(str(report["gross_pnl"])) == Decimal("1.60")
    assert Decimal(str(report["fees"])) == Decimal("0.2212")
    assert Decimal(str(report["slippage"])) == Decimal("0.40")
    assert Decimal(str(report["net_pnl"])) == Decimal("0.9788")
    assert Decimal(str(report["max_drawdown"])) == Decimal("0.50")
    assert report["calibration"] == "CALIBRATING"
    assert len(report["contributions"]) == 1


def test_disk_pressure_locks_new_entries_before_write(tmp_path: Path) -> None:
    store = ParquetEventStore(
        tmp_path / "parquet",
        minimum_free_bytes=200,
        minimum_free_ratio=0.10,
        disk_usage=lambda _: DiskUsage(total=1_000, used=950, free=50),
    )
    assert store.health().entry_allowed is False
    with pytest.raises(StoragePressureError, match="STORAGE_PRESSURE"):
        store.write_events(
            venue="FIXTURE",
            symbol="BTCUSDT",
            event_type="feature_1s",
            rows=[{"ts_ms": 1_700_000_000_000, "mid": 100.0}],
        )
    assert store.dataset_files() == ()


def test_replay_and_exports_are_deterministic_and_complete(tmp_path: Path) -> None:
    events = _replay_events()
    config = {"fee_bps": "6", "latency_ms": 75, "mode": "FIXTURE_OFFLINE"}
    engine = ReplayEngine()
    first = engine.replay(events, config=config, strategy_version="LSA_REVERSAL_V1", seed=17)
    second = engine.replay(
        list(reversed(events)), config=config, strategy_version="LSA_REVERSAL_V1", seed=17
    )
    assert first == second
    assert first.final_state == "CLOSED"
    assert first.decision_path == (
        "DECISION:LSA_CONFIRMED",
        "ORDER:ENTRY_IOC",
        "FILL:FULL_FILL",
        "EXIT:TAKE_PROFIT",
    )

    bundle = tmp_path / "replay.zip"
    engine.write_bundle(
        bundle,
        events,
        config=config,
        strategy_version="LSA_REVERSAL_V1",
        seed=17,
    )
    assert engine.replay_bundle(bundle) == first
    with zipfile.ZipFile(bundle) as archive:
        event_text = archive.read("events.jsonl")
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"checksum": "tampered"}))
        archive.writestr("events.jsonl", event_text)
    with pytest.raises((ReplayIntegrityError, KeyError)):
        engine.replay_bundle(bundle)

    ledger = _open_run(tmp_path / "export-ledger")
    ledger.record_trade(_sample_trade())
    exported = RunExporter(ledger).export_run(
        tmp_path / "exports",
        run_id="run-001",
        config=config,
        events=events,
        logs=[{"level": "INFO", "message": "fixture PAPER complete"}],
        strategy_version="LSA_REVERSAL_V1",
        seed=17,
    )
    assert [path.suffix for path in exported] == [".csv", ".json", ".html", ".zip", ".jsonl"]
    assert all(path.exists() and path.stat().st_size > 0 for path in exported)
    assert "1.4788" in exported[0].read_text(encoding="utf-8")
    ledger.close()


def _open_run(tmp_path: Path) -> SQLiteLedger:
    ledger = SQLiteLedger(tmp_path / "ledger.sqlite3")
    ledger.start_run(
        "run-001",
        mode="FIXTURE_OFFLINE",
        venue="FIXTURE",
        config={"risk": "0.10%", "seed": 17},
        started_ts_ms=1_000,
    )
    return ledger


def _sample_trade(
    trade_id: str = "trade-001",
    *,
    net: str = "1.4788",
    gross: str = "1.80",
    fees: str = "0.1212",
    slippage: str = "0.20",
) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "run_id": "run-001",
        "venue": "FIXTURE",
        "symbol": "BTCUSDT",
        "strategy_id": "LSA_REVERSAL_V1",
        "side": "LONG",
        "entry_ts_ms": 1_100,
        "exit_ts_ms": 2_000 if trade_id == "trade-001" else 2_100,
        "entry_price": "100.10",
        "exit_price": "101.90",
        "initial_stop": "99.55",
        "take_profit": "101.90",
        "quantity": "1",
        "exit_reason": "TAKE_PROFIT" if Decimal(net) > 0 else "STOP",
        "gross_pnl_usdt": gross,
        "fees_usdt": fees,
        "slippage_usdt": slippage,
        "net_pnl_usdt": net,
        "mae_r": -0.22,
        "mfe_r": 1.41,
        "holding_ms": 184_000,
        "flags": ["OFFLINE_FIXTURE"],
        "config_hash": "fixture-config-sha256",
        "strategy_version": "1",
        "regime": "RANGE",
        "profile": "BASE",
    }


def _replay_events() -> list[dict[str, object]]:
    return [
        {"sequence": 1, "ts_ms": 1_000, "event_type": "MARKET", "state": "OBSERVING"},
        {
            "sequence": 2,
            "ts_ms": 1_100,
            "event_type": "DECISION",
            "state": "ARMED",
            "reason_code": "LSA_CONFIRMED",
        },
        {
            "sequence": 3,
            "ts_ms": 1_200,
            "event_type": "ORDER",
            "state": "ENTRY_PENDING",
            "reason_code": "ENTRY_IOC",
        },
        {
            "sequence": 4,
            "ts_ms": 1_275,
            "event_type": "FILL",
            "state": "PROTECTED",
            "reason_code": "FULL_FILL",
        },
        {
            "sequence": 5,
            "ts_ms": 2_000,
            "event_type": "EXIT",
            "state": "CLOSED",
            "reason_code": "TAKE_PROFIT",
        },
    ]
