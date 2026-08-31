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


def test_trade_fill_evidence_batch_isolated_ordered_and_reconciled(tmp_path: Path) -> None:
    ledger = _open_run(tmp_path)
    for order in (
        {
            "order_id": "order-trade-001-entry",
            "run_id": "run-001",
            "trade_id": "trade-001",
            "status": "FILLED",
            "side": "BUY",
            "intent": "ENTRY_IOC",
            "filled_qty": "1",
            "created_ts_ms": 1_100,
        },
        {
            "order_id": "order-trade-001-exit",
            "run_id": "run-001",
            "trade_id": "trade-001",
            "status": "FILLED",
            "side": "SELL",
            "intent": "TAKE_PROFIT",
            "filled_qty": "1",
            "created_ts_ms": 1_800,
        },
        {
            "order_id": "order-trade-001-lookalike-other",
            "run_id": "run-001",
            "trade_id": "trade-other",
            "status": "FILLED",
            "side": "SELL",
            "intent": "STOP_EXIT",
            "filled_qty": "99",
            "created_ts_ms": 1_050,
        },
    ):
        ledger.record_order(order)
    for fill in (
        {
            "fill_id": "fill-exit",
            "run_id": "run-001",
            "order_id": "order-trade-001-exit",
            "side": "SELL",
            "price": "101.90",
            "quantity": "1",
            "fee_usdt": "0.0612",
            "slippage_usdt": "0.12",
            "ts_ms": 2_000,
        },
        {
            "fill_id": "fill-entry",
            "run_id": "run-001",
            "order_id": "order-trade-001-entry",
            "side": "BUY",
            "price": "100.10",
            "quantity": "1",
            "fee_usdt": "0.06",
            "slippage_usdt": "0.08",
            "ts_ms": 1_200,
        },
        {
            "fill_id": "fill-other-trade",
            "run_id": "run-001",
            "order_id": "order-trade-001-lookalike-other",
            "side": "SELL",
            "price": "1",
            "quantity": "99",
            "fee_usdt": "9",
            "slippage_usdt": "9",
            "ts_ms": 1_150,
        },
    ):
        ledger.record_fill(fill)
    ledger.record_trade({**_sample_trade(), "entry_ts_ms": 1_200})
    traced_sql: list[str] = []
    ledger._read_connection.set_trace_callback(traced_sql.append)

    evidence = ledger.list_trade_fill_evidence([("run-001", "trade-001")])

    assert len([sql for sql in traced_sql if "WITH requested AS" in sql]) == 1
    assert evidence[("run-001", "trade-001")]["fill_evidence_state"] == "PRESENT"
    fills = evidence[("run-001", "trade-001")]["fills"]
    assert [fill["fill_id"] for fill in fills] == ["fill-entry", "fill-exit"]
    assert [fill["intent"] for fill in fills] == ["ENTRY_IOC", "TAKE_PROFIT"]
    assert sum(Decimal(fill["fee_usdt"]) for fill in fills) == Decimal("0.1212")
    assert sum(Decimal(fill["slippage_usdt"]) for fill in fills) == Decimal("0.20")
    assert all(fill["order_id"] != "order-trade-001-lookalike-other" for fill in fills)
    ledger.close()


def test_trade_fill_evidence_distinguishes_missing_sources_and_fails_closed(
    tmp_path: Path,
) -> None:
    ledger = _open_run(tmp_path)
    ledger.record_trade(_sample_trade("trade-legacy"))
    ledger.record_order(
        {
            "order_id": "order-current-no-fill",
            "run_id": "run-001",
            "trade_id": "trade-current-no-fill",
            "status": "FILLED",
            "side": "BUY",
            "intent": "ENTRY_IOC",
            "filled_qty": "1",
            "created_ts_ms": 1_100,
        }
    )
    ledger.record_trade(_sample_trade("trade-current-no-fill"))
    states = ledger.list_trade_fill_evidence(
        [
            ("run-001", "trade-legacy"),
            ("run-001", "trade-current-no-fill"),
            ("run-001", "trade-not-persisted"),
        ]
    )
    assert states[("run-001", "trade-legacy")]["fill_evidence_state"] == "LEGACY_UNAVAILABLE"
    assert (
        states[("run-001", "trade-current-no-fill")]["fill_evidence_state"]
        == "CURRENT_MAIN_NO_FILL"
    )
    assert (
        states[("run-001", "trade-not-persisted")]["fill_evidence_state"]
        == "CURRENT_MAIN_NO_FILL"
    )

    ledger.record_order(
        {
            "order_id": "order-mismatch",
            "run_id": "run-001",
            "trade_id": "trade-mismatch",
            "status": "FILLED",
            "side": "BUY",
            "intent": "ENTRY_IOC",
            "filled_qty": "1",
            "created_ts_ms": 1_100,
        }
    )
    ledger.record_fill(
        {
            "fill_id": "fill-mismatch",
            "run_id": "run-001",
            "order_id": "order-mismatch",
            "side": "BUY",
            "price": "100.10",
            "quantity": "1",
            "fee_usdt": "0.10",
            "slippage_usdt": "0.20",
            "ts_ms": 1_200,
        }
    )
    ledger.record_order(
        {
            "order_id": "order-mismatch-exit",
            "run_id": "run-001",
            "trade_id": "trade-mismatch",
            "status": "FILLED",
            "side": "SELL",
            "intent": "TAKE_PROFIT",
            "filled_qty": "1",
            "created_ts_ms": 1_800,
        }
    )
    ledger.record_fill(
        {
            "fill_id": "fill-mismatch-exit",
            "run_id": "run-001",
            "order_id": "order-mismatch-exit",
            "side": "SELL",
            "price": "101.90",
            "quantity": "1",
            "fee_usdt": "0",
            "slippage_usdt": "0",
            "ts_ms": 2_100,
        }
    )
    ledger.record_trade({**_sample_trade("trade-mismatch"), "entry_ts_ms": 1_200})
    with pytest.raises(LedgerInvariantError, match="fill 비용 합계"):
        ledger.list_trade_fill_evidence([("run-001", "trade-mismatch")])
    ledger.close()


@pytest.mark.parametrize(
    ("order_override", "fill_override", "expected_error"),
    [
        ({}, {"side": "SELL"}, "fill side"),
        ({}, {"quantity": "NaN"}, "수량은 유한한 양수"),
        ({}, {"ts_ms": 1_050}, "fill 시각이 주문 생성시각보다 빠릅니다"),
        ({"status": "REJECTED"}, {}, "미체결 상태 주문에 fill이 연결됐습니다"),
    ],
)
def test_trade_fill_evidence_rejects_malformed_present_payloads(
    tmp_path: Path,
    order_override: dict[str, object],
    fill_override: dict[str, object],
    expected_error: str,
) -> None:
    ledger = _open_run(tmp_path)
    ledger.record_order(
        {
            "order_id": "order-strict",
            "run_id": "run-001",
            "trade_id": "trade-001",
            "status": "FILLED",
            "side": "BUY",
            "intent": "ENTRY_IOC",
            "filled_qty": "1",
            "created_ts_ms": 1_100,
            **order_override,
        }
    )
    ledger.record_fill(
        {
            "fill_id": "fill-strict",
            "run_id": "run-001",
            "order_id": "order-strict",
            "side": "BUY",
            "price": "100.10",
            "quantity": "1",
            "fee_usdt": "0.1212",
            "slippage_usdt": "0.20",
            "ts_ms": 1_200,
            **fill_override,
        }
    )
    ledger.record_trade(_sample_trade())

    with pytest.raises(LedgerInvariantError, match=expected_error):
        ledger.list_trade_fill_evidence([("run-001", "trade-001")])
    ledger.close()


def test_trade_fill_evidence_rejects_oversized_entry_without_exit(tmp_path: Path) -> None:
    ledger = _open_run(tmp_path)
    ledger.record_order(
        {
            "order_id": "order-oversized-entry",
            "run_id": "run-001",
            "trade_id": "trade-001",
            "status": "FILLED",
            "side": "BUY",
            "intent": "ENTRY_IOC",
            "filled_qty": "999",
            "created_ts_ms": 1_100,
        }
    )
    ledger.record_fill(
        {
            "fill_id": "fill-oversized-entry",
            "run_id": "run-001",
            "order_id": "order-oversized-entry",
            "side": "BUY",
            "price": "100.10",
            "quantity": "999",
            "fee_usdt": "0.1212",
            "slippage_usdt": "0.20",
            "ts_ms": 1_200,
        }
    )
    ledger.record_trade({**_sample_trade(), "entry_ts_ms": 1_200})

    with pytest.raises(LedgerInvariantError, match="진입 fill과 청산 fill"):
        ledger.list_trade_fill_evidence([("run-001", "trade-001")])
    ledger.close()


def test_trade_fill_evidence_rejects_exit_before_entry(tmp_path: Path) -> None:
    ledger = _open_run(tmp_path)
    for order in (
        {
            "order_id": "order-reversed-entry",
            "run_id": "run-001",
            "trade_id": "trade-001",
            "status": "FILLED",
            "side": "BUY",
            "intent": "ENTRY_IOC",
            "filled_qty": "1",
            "created_ts_ms": 1_000,
        },
        {
            "order_id": "order-reversed-exit",
            "run_id": "run-001",
            "trade_id": "trade-001",
            "status": "FILLED",
            "side": "SELL",
            "intent": "TAKE_PROFIT",
            "filled_qty": "1",
            "created_ts_ms": 1_000,
        },
    ):
        ledger.record_order(order)
    for fill in (
        {
            "fill_id": "fill-reversed-entry",
            "run_id": "run-001",
            "order_id": "order-reversed-entry",
            "side": "BUY",
            "price": "100.10",
            "quantity": "1",
            "fee_usdt": "0.06",
            "slippage_usdt": "0.08",
            "ts_ms": 1_900,
        },
        {
            "fill_id": "fill-reversed-exit",
            "run_id": "run-001",
            "order_id": "order-reversed-exit",
            "side": "SELL",
            "price": "101.90",
            "quantity": "1",
            "fee_usdt": "0.0612",
            "slippage_usdt": "0.12",
            "ts_ms": 1_200,
        },
    ):
        ledger.record_fill(fill)
    ledger.record_trade(
        {**_sample_trade(), "entry_ts_ms": 1_200, "exit_ts_ms": 1_900}
    )

    with pytest.raises(LedgerInvariantError, match="청산 fill이 진입 fill보다 먼저"):
        ledger.list_trade_fill_evidence([("run-001", "trade-001")])
    ledger.close()


@pytest.mark.parametrize(
    ("entry_side", "exit_side", "fill_quantity", "expected_error"),
    [
        ("BUY", "SELL", "999", "진입·청산 fill 수량"),
        ("SELL", "BUY", "1", "진입 fill 방향"),
    ],
)
def test_trade_fill_evidence_rejects_complete_but_wrong_quantity_or_direction(
    tmp_path: Path,
    entry_side: str,
    exit_side: str,
    fill_quantity: str,
    expected_error: str,
) -> None:
    ledger = _open_run(tmp_path)
    for order in (
        {
            "order_id": "order-strict-entry",
            "run_id": "run-001",
            "trade_id": "trade-001",
            "status": "FILLED",
            "side": entry_side,
            "intent": "ENTRY_IOC",
            "filled_qty": fill_quantity,
            "created_ts_ms": 1_100,
        },
        {
            "order_id": "order-strict-exit",
            "run_id": "run-001",
            "trade_id": "trade-001",
            "status": "FILLED",
            "side": exit_side,
            "intent": "TAKE_PROFIT",
            "filled_qty": fill_quantity,
            "created_ts_ms": 1_800,
        },
    ):
        ledger.record_order(order)
    for fill in (
        {
            "fill_id": "fill-strict-entry",
            "run_id": "run-001",
            "order_id": "order-strict-entry",
            "side": entry_side,
            "price": "100.10",
            "quantity": fill_quantity,
            "fee_usdt": "0.06",
            "slippage_usdt": "0.08",
            "ts_ms": 1_200,
        },
        {
            "fill_id": "fill-strict-exit",
            "run_id": "run-001",
            "order_id": "order-strict-exit",
            "side": exit_side,
            "price": "101.90",
            "quantity": fill_quantity,
            "fee_usdt": "0.0612",
            "slippage_usdt": "0.12",
            "ts_ms": 2_000,
        },
    ):
        ledger.record_fill(fill)
    ledger.record_trade({**_sample_trade(), "entry_ts_ms": 1_200})

    with pytest.raises(LedgerInvariantError, match=expected_error):
        ledger.list_trade_fill_evidence([("run-001", "trade-001")])
    ledger.close()


def test_execution_state_batch_commits_all_recovery_rows_together(tmp_path: Path) -> None:
    ledger = _open_run(tmp_path)
    trade = _sample_trade()
    shadow_trade = {
        **_sample_trade("shadow-001"),
        "shadow_trade_id": "shadow-001",
        "closed_ts_ms": 2_100,
        "profile": "STRESS",
    }
    traced_sql: list[str] = []
    ledger._connection.set_trace_callback(traced_sql.append)
    ledger.record_execution_state_batch(
        run_id="run-001",
        candidates=(
            {
                "candidate_id": "candidate-batch",
                "run_id": "run-001",
                "signal_time_ms": 1_000,
                "reason_codes": ["STRUCTURE_CONFIRMED"],
            },
        ),
        orders=(
            {
                "order_id": "order-batch",
                "run_id": "run-001",
                "trade_id": "trade-001",
                "status": "FILLED",
                "created_ts_ms": 1_100,
            },
        ),
        fills=(
            {
                "fill_id": "fill-batch",
                "run_id": "run-001",
                "order_id": "order-batch",
                "price": "100.10",
                "quantity": "1",
                "ts_ms": 1_200,
            },
        ),
        trades=(trade,),
        shadow_trades=(shadow_trade,),
        audits=(
            {
                "run_id": "run-001",
                "ts_ms": 2_100,
                "event": "POSITION_CLOSED",
                "account_id": "LSA_REVERSAL_V1:STRESS",
            },
        ),
        account_snapshots=(
            {
                "run_id": "run-001",
                "strategy_id": "LSA_REVERSAL_V1",
                "profile": "STRESS",
                "ts_ms": 2_100,
                "equity_usdt": "999.5",
            },
        ),
        recovery_snapshot={
            "run_id": "run-001",
            "lifecycle_state": "OBSERVING",
            "ts_ms": 2_100,
            "payload": {"open_position": None, "portfolio": {"positions": []}},
        },
    )
    ledger._connection.set_trace_callback(None)

    assert sum(statement == "BEGIN IMMEDIATE" for statement in traced_sql) == 1
    assert sum(statement == "COMMIT" for statement in traced_sql) == 1
    assert ledger.count("candidates") == 1
    assert ledger.count("paper_orders") == 1
    assert ledger.count("fills") == 1
    assert ledger.list_trades("run-001") == [trade]
    assert ledger.list_shadow_trades("run-001") == [shadow_trade]
    assert ledger.count("execution_audit") == 1
    assert ledger.count("strategy_account_snapshots") == 1
    recovered = ledger.recover_latest(recovered_ts_ms=2_200)
    assert recovered is not None
    assert recovered.lifecycle_state == "OBSERVING"
    assert recovered.payload["open_position"] is None
    ledger.close()


def test_execution_state_batch_rolls_back_every_row_on_failure(tmp_path: Path) -> None:
    ledger = _open_run(tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        ledger.record_execution_state_batch(
            run_id="run-001",
            candidates=(
                {
                    "candidate_id": "candidate-rolled-back",
                    "run_id": "run-001",
                    "signal_time_ms": 1_000,
                },
            ),
            orders=(
                {
                    "order_id": "order-rolled-back",
                    "run_id": "run-001",
                    "trade_id": "trade-001",
                    "status": "FILLED",
                    "created_ts_ms": 1_100,
                },
            ),
            fills=(
                {
                    "fill_id": "fill-invalid-parent",
                    "run_id": "run-001",
                    "order_id": "missing-order",
                    "ts_ms": 1_200,
                },
            ),
            trades=(),
            shadow_trades=(),
            audits=(),
            account_snapshots=(),
            recovery_snapshot=None,
        )

    assert ledger.count("candidates") == 0
    assert ledger.count("paper_orders") == 0
    assert ledger.count("fills") == 0
    ledger.close()


def test_strategy_metrics_include_nonannualized_risk_turnover_and_regime_attribution() -> None:
    win = {
        **_sample_trade("metric-win"),
        "sample_type": "LIVE_PUBLIC",
        "regime": "RANGE",
        "time_to_tp1_ms": 60_000,
        "time_to_tp2_ms": 120_000,
        "time_to_stop_ms": None,
        "trailing_activation_ts_ms": 70_000,
        "runner_started_ts_ms": 75_000,
        "peak_unrealized_usdt": "2.0",
        "giveback_usdt": "0.4",
        "runner_net_pnl_usdt": "0.6",
        "trail_trigger_slippage_usdt": "0.05",
    }
    loss = {
        **_sample_trade(
            "metric-loss",
            net="-0.5",
            gross="-0.2",
            fees="0.1",
            slippage="0.2",
        ),
        "sample_type": "LIVE_PUBLIC",
        "regime": "TREND_DOWN",
        "time_to_tp1_ms": None,
        "time_to_tp2_ms": None,
        "time_to_stop_ms": 184_000,
    }

    report = TradeAnalytics().strategy_reports([win, loss])[0]

    assert Decimal(str(report["omega_ratio"])) > 0
    assert Decimal(str(report["downside_deviation_usdt"])) > 0
    assert report["sortino_ratio_per_trade"] is not None
    assert report["calmar_ratio_nonannualized"] is not None
    assert Decimal(str(report["turnover_usdt"])) > 0
    assert Decimal(str(report["turnover_ratio"])) > 0
    assert {row["regime"] for row in report["regime_contributions"]} == {
        "RANGE",
        "TREND_DOWN",
    }
    assert report["tp1_sample_size"] == 1
    assert report["tp2_sample_size"] == 1
    assert report["stop_sample_size"] == 1
    assert report["median_time_to_tp1_ms"] == 60_000
    assert report["median_time_to_tp2_ms"] == 120_000
    assert report["median_time_to_stop_ms"] == 184_000
    assert report["trail_activation_count"] == 1
    assert report["trail_activation_rate"] == "0.5"
    assert report["tp1_fill_rate"] == "0.5"
    assert report["runner_count"] == 1
    assert report["runner_rate"] == "0.5"
    assert report["runner_net_contribution_usdt"] == "0.6"
    assert report["mfe_capture_ratio_mean"] == "0.7394"
    assert report["average_peak_giveback_usdt"] == "0.2"
    assert report["median_peak_giveback_usdt"] == "0.2"
    assert report["p90_peak_giveback_usdt"] == "0.4"
    assert report["trailing_exit_count"] == 0
    assert report["stop_before_trail_activation_count"] == 1
    assert report["activation_after_net_negative_exit_count"] == 0
    assert report["trail_trigger_slippage_usdt"] == "0.05"
    assert report["metric_status"] == {
        "omega_ratio": "CALCULATED",
        "sortino_ratio_per_trade": "CALCULATED",
        "calmar_ratio_nonannualized": "CALCULATED",
        "turnover": "CALCULATED",
    }


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
        "opportunity_id": trade_id,
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
