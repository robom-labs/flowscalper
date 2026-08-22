"""DuckDB SQL로 손익·비용·낙폭·MAE/MFE 집계를 생성한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa


class TradeAnalytics:
    """표본 수와 가정을 숨기지 않는 PAPER 성과 리포트를 만든다."""

    def report(
        self,
        trades: Sequence[Mapping[str, object]],
        *,
        starting_equity: Decimal = Decimal("1000"),
    ) -> dict[str, Any]:
        if not trades:
            return {
                "sample_size": 0,
                "calibration": "CALIBRATING",
                "gross_pnl": "0",
                "fees": "0",
                "slippage": "0",
                "net_pnl": "0",
                "max_drawdown": "0",
                "profit_factor": None,
                "expectancy": None,
                "mae_r_mean": None,
                "mfe_r_mean": None,
                "contributions": [],
            }
        normalized = [_normalize_trade(trade) for trade in trades]
        table = pa.Table.from_pylist(normalized)
        connection = duckdb.connect(":memory:")
        try:
            connection.register("trade_rows", table)
            totals = _required_row(
                connection.execute(
                    """
                    SELECT
                      COUNT(*),
                      SUM(CAST(gross_pnl_usdt AS DECIMAL(38, 8))),
                      SUM(CAST(fees_usdt AS DECIMAL(38, 8))),
                      SUM(CAST(slippage_usdt AS DECIMAL(38, 8))),
                      SUM(CAST(net_pnl_usdt AS DECIMAL(38, 8))),
                      AVG(CAST(mae_r AS DOUBLE)),
                      AVG(CAST(mfe_r AS DOUBLE)),
                      SUM(CASE WHEN CAST(net_pnl_usdt AS DECIMAL(38, 8)) > 0
                          THEN CAST(net_pnl_usdt AS DECIMAL(38, 8)) ELSE 0 END),
                      ABS(SUM(CASE WHEN CAST(net_pnl_usdt AS DECIMAL(38, 8)) < 0
                          THEN CAST(net_pnl_usdt AS DECIMAL(38, 8)) ELSE 0 END))
                    FROM trade_rows
                    """
                ).fetchone()
            )
            drawdown = _required_row(
                connection.execute(
                    """
                    WITH curve AS (
                      SELECT exit_ts_ms, trade_id,
                        CAST(? AS DECIMAL(38, 8))
                        + SUM(CAST(net_pnl_usdt AS DECIMAL(38, 8))) OVER (
                          ORDER BY exit_ts_ms, trade_id
                        ) AS equity
                      FROM trade_rows
                    ), peaks AS (
                      SELECT *, MAX(equity) OVER (
                        ORDER BY exit_ts_ms, trade_id ROWS UNBOUNDED PRECEDING
                      ) AS peak
                      FROM curve
                    )
                    SELECT COALESCE(MAX(peak - equity), 0) FROM peaks
                    """,
                    [str(starting_equity)],
                ).fetchone()
            )[0]
            contribution_rows = connection.execute(
                """
                SELECT strategy_id, venue, regime, profile,
                  COUNT(*) AS sample_size,
                  SUM(CAST(net_pnl_usdt AS DECIMAL(38, 8))) AS net_pnl
                FROM trade_rows
                GROUP BY strategy_id, venue, regime, profile
                ORDER BY strategy_id, venue, regime, profile
                """
            ).fetchall()
        finally:
            connection.close()
        losses = Decimal(str(totals[8]))
        profit_factor = None if losses == 0 else str(Decimal(str(totals[7])) / losses)
        sample_size = int(totals[0])
        net = Decimal(str(totals[4]))
        return {
            "sample_size": sample_size,
            "calibration": "CALIBRATING" if sample_size < 30 else "RESEARCH_SAMPLE",
            "gross_pnl": str(totals[1]),
            "fees": str(totals[2]),
            "slippage": str(totals[3]),
            "net_pnl": str(totals[4]),
            "max_drawdown": str(drawdown),
            "profit_factor": profit_factor,
            "expectancy": str(net / sample_size),
            "mae_r_mean": _optional_number(totals[5]),
            "mfe_r_mean": _optional_number(totals[6]),
            "contributions": [
                {
                    "strategy_id": row[0],
                    "venue": row[1],
                    "regime": row[2],
                    "profile": row[3],
                    "sample_size": row[4],
                    "net_pnl": str(row[5]),
                }
                for row in contribution_rows
            ],
        }

    def parquet_event_counts(self, files: Sequence[Path]) -> list[dict[str, object]]:
        if not files:
            return []
        connection = duckdb.connect(":memory:")
        try:
            rows = connection.execute(
                """
                SELECT venue, symbol, event_type, COUNT(*) AS event_count
                FROM read_parquet(?, hive_partitioning = true)
                GROUP BY venue, symbol, event_type
                ORDER BY venue, symbol, event_type
                """,
                [[str(path) for path in files]],
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                "venue": str(row[0]),
                "symbol": str(row[1]),
                "event_type": str(row[2]),
                "event_count": int(row[3]),
            }
            for row in rows
        ]


def _normalize_trade(trade: Mapping[str, object]) -> dict[str, object]:
    return {
        "trade_id": str(trade["trade_id"]),
        "strategy_id": str(trade["strategy_id"]),
        "venue": str(trade["venue"]),
        "regime": str(trade.get("regime", "UNKNOWN")),
        "profile": str(trade.get("profile", "BASE")),
        "exit_ts_ms": int(str(trade["exit_ts_ms"])),
        "gross_pnl_usdt": str(trade["gross_pnl_usdt"]),
        "fees_usdt": str(trade["fees_usdt"]),
        "slippage_usdt": str(trade["slippage_usdt"]),
        "net_pnl_usdt": str(trade["net_pnl_usdt"]),
        "mae_r": _optional_number(trade.get("mae_r")),
        "mfe_r": _optional_number(trade.get("mfe_r")),
    }


def _required_row(row: tuple[Any, ...] | None) -> tuple[Any, ...]:
    if row is None:
        raise RuntimeError("DuckDB 집계가 행을 반환하지 않았습니다.")
    return row


def _optional_number(value: object | None) -> float | None:
    return None if value is None else float(str(value))
