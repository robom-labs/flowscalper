"""DuckDB SQL로 손익·비용·낙폭·MAE/MFE 집계를 생성한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from math import ceil, sqrt
from pathlib import Path
from statistics import median
from typing import Any

import duckdb
import pyarrow as pa

from backend.app.analytics.opportunities import group_trade_opportunities


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
        normalized = [
            _normalize_trade(trade, default_account_scope="LEAGUE") for trade in trades
        ]
        _require_league_strategy_analytics(normalized)
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

    def strategy_reports(
        self,
        trades: Sequence[Mapping[str, object]],
        *,
        strategy_ids: Sequence[str] = (),
    ) -> list[dict[str, object]]:
        """전략·비용프로필별 승률·기대값·PF·비용·낙폭·표본상태를 같이 보여준다."""

        normalized = [
            _normalize_trade(trade, default_account_scope="LEAGUE") for trade in trades
        ]
        _require_league_strategy_analytics(normalized)
        report_strategy_ids = sorted(
            {
                *(str(trade["strategy_id"]) for trade in normalized),
                *(str(strategy_id) for strategy_id in strategy_ids),
            }
        )
        reports: list[dict[str, object]] = []
        for strategy_id in report_strategy_ids:
            strategy_rows = [trade for trade in normalized if trade["strategy_id"] == strategy_id]
            opportunity_grouping = group_trade_opportunities(strategy_rows)
            for profile in ("BASE", "STRESS"):
                raw_group = [
                    trade
                    for trade in normalized
                    if trade["strategy_id"] == strategy_id and trade["profile"] == profile
                ]
                profile_grouping = group_trade_opportunities(raw_group)
                group = [
                    _aggregate_opportunity_rows(opportunity.rows)
                    for opportunity in profile_grouping.groups
                ]
                reports.append(
                    _strategy_report(
                        strategy_id,
                        profile,
                        group,
                        stress_verified=opportunity_grouping.stress_result_row_count > 0,
                        unique_opportunity_count=opportunity_grouping.unique_opportunity_count,
                        raw_ledger_row_count=len(strategy_rows),
                        resolved_ledger_row_count=opportunity_grouping.raw_result_row_count,
                        profile_raw_ledger_row_count=len(raw_group),
                        unresolved_ledger_row_count=(
                            opportunity_grouping.unresolved_result_row_count
                        ),
                        profile_unresolved_ledger_row_count=(
                            profile_grouping.unresolved_result_row_count
                        ),
                    )
                )
        return reports

    def strategy_symbol_reports(
        self,
        trades: Sequence[Mapping[str, object]],
        *,
        minimum_research_sample: int = 30,
    ) -> list[dict[str, object]]:
        """전략·비용프로필·종목을 고유 시장기회로 묶어 보수적으로 순위화한다."""

        normalized = [_normalize_trade(trade) for trade in trades]
        keys = sorted(
            {
                (str(trade["strategy_id"]), str(trade["profile"]), str(trade["symbol"]))
                for trade in normalized
            }
        )
        rows: list[dict[str, object]] = []
        for strategy_id, profile, symbol in keys:
            raw_group = [
                trade
                for trade in normalized
                if trade["strategy_id"] == strategy_id
                and trade["profile"] == profile
                and trade["symbol"] == symbol
            ]
            opportunity_grouping = group_trade_opportunities(raw_group)
            group = [
                _aggregate_opportunity_rows(opportunity.rows)
                for opportunity in opportunity_grouping.groups
            ]
            metrics = _window_metrics(
                sorted(group, key=lambda trade: (trade["exit_ts_ms"], trade["trade_id"]))
            )
            sample_size = len(group)
            expectancy = metrics["expectancy_usdt"]
            profit_factor = metrics["profit_factor"]
            eligible = sample_size >= minimum_research_sample
            score = None
            if eligible and expectancy is not None:
                score = float(str(expectancy)) * min(sample_size, 100) / 100
                if profit_factor is not None:
                    score *= min(float(str(profit_factor)), 3.0)
            rows.append(
                {
                    "strategy_id": strategy_id,
                    "profile": profile,
                    "symbol": symbol,
                    "sample_size": sample_size,
                    "unique_opportunity_count": opportunity_grouping.unique_opportunity_count,
                    "raw_ledger_row_count": len(raw_group),
                    "resolved_ledger_row_count": opportunity_grouping.raw_result_row_count,
                    "unresolved_ledger_row_count": (
                        opportunity_grouping.unresolved_result_row_count
                    ),
                    "opportunity_grouping_status": (
                        "NOT_PROVEN"
                        if opportunity_grouping.unresolved_result_row_count
                        else "PROVEN"
                    ),
                    "sample_status": "RESEARCH_SAMPLE" if eligible else "CALIBRATING",
                    "ranking_eligible": eligible,
                    "rank_score": round(score, 8) if score is not None else None,
                    "win_rate": metrics["win_rate"],
                    "expectancy_usdt": expectancy,
                    "profit_factor": profit_factor,
                    "fees": metrics["fees"],
                    "slippage": metrics["slippage"],
                    "net_pnl": metrics["net_pnl"],
                    "maximum_drawdown": metrics["maximum_drawdown"],
                }
            )
        eligible_rows = sorted(
            (row for row in rows if row["ranking_eligible"]),
            key=lambda row: (
                -float(str(row["rank_score"])),
                str(row["strategy_id"]),
                str(row["symbol"]),
            ),
        )
        rank_by_key = {
            (row["strategy_id"], row["profile"], row["symbol"]): index
            for index, row in enumerate(eligible_rows, start=1)
        }
        for row in rows:
            row["rank"] = rank_by_key.get((row["strategy_id"], row["profile"], row["symbol"]))
        return sorted(
            rows,
            key=lambda row: (
                row["rank"] is None,
                int(str(row["rank"] or 0)),
                str(row["strategy_id"]),
                str(row["profile"]),
                str(row["symbol"]),
            ),
        )


def _normalize_trade(
    trade: Mapping[str, object],
    *,
    default_account_scope: str = "MAIN",
) -> dict[str, object]:
    trade_id = str(trade["trade_id"])
    strategy_id = str(trade["strategy_id"])
    profile = str(trade.get("profile", "BASE")).strip().upper()
    account_scope = str(trade.get("account_scope", default_account_scope)).strip().upper()
    account_id = trade.get("account_id")
    if account_id is None:
        account_id = (
            "SHARED_PAPER"
            if account_scope == "MAIN"
            else f"{strategy_id}:{profile}"
            if account_scope == "LEAGUE"
            else None
        )
    opportunity_id = trade.get("opportunity_id")
    candidate_id = trade.get("candidate_id")
    signal_event_id = trade.get("signal_event_id")
    return {
        "trade_id": trade_id,
        "run_id": _optional_text(trade.get("run_id")),
        "strategy_id": strategy_id,
        "strategy_version": _optional_text(trade.get("strategy_version")),
        "opportunity_id": str(opportunity_id) if opportunity_id is not None else None,
        "candidate_id": str(candidate_id) if candidate_id is not None else None,
        "signal_event_id": str(signal_event_id) if signal_event_id is not None else None,
        "symbol": _optional_text(trade.get("symbol")),
        "side": _optional_text(trade.get("side")),
        "account_scope": account_scope,
        "account_id": _optional_text(account_id),
        "venue": str(trade["venue"]),
        "regime": str(trade.get("regime", "UNKNOWN")),
        "profile": profile,
        "exit_ts_ms": int(str(trade["exit_ts_ms"])),
        "entry_ts_ms": int(
            str(
                trade.get(
                    "entry_ts_ms",
                    int(str(trade["exit_ts_ms"])) - int(str(trade.get("holding_ms", 0))),
                )
            )
        ),
        "entry_price": str(trade.get("entry_price", "0")),
        "exit_price": str(trade.get("exit_price", "0")),
        "initial_stop": str(trade.get("initial_stop", "0")),
        "quantity": str(trade.get("quantity", "0")),
        "holding_ms": int(str(trade.get("holding_ms", 0))),
        "time_to_tp1_ms": _optional_int(trade.get("time_to_tp1_ms")),
        "time_to_tp2_ms": _optional_int(trade.get("time_to_tp2_ms")),
        "time_to_stop_ms": _optional_int(trade.get("time_to_stop_ms")),
        "exit_reason": str(trade.get("exit_reason", "UNKNOWN")),
        "trailing_activation_ts_ms": _optional_int(trade.get("trailing_activation_ts_ms")),
        "runner_started_ts_ms": _optional_int(trade.get("runner_started_ts_ms")),
        "peak_unrealized_usdt": str(trade.get("peak_unrealized_usdt", "0")),
        "giveback_usdt": str(trade.get("giveback_usdt", "0")),
        "runner_net_pnl_usdt": str(trade.get("runner_net_pnl_usdt", "0")),
        "trail_trigger_slippage_usdt": str(trade.get("trail_trigger_slippage_usdt", "0")),
        "expected_gross_mfe_usdt": (
            str(trade["expected_gross_mfe_usdt"])
            if trade.get("expected_gross_mfe_usdt") is not None
            else None
        ),
        "expected_total_cost_usdt": (
            str(trade["expected_total_cost_usdt"])
            if trade.get("expected_total_cost_usdt") is not None
            else None
        ),
        "gross_pnl_usdt": str(trade["gross_pnl_usdt"]),
        "fees_usdt": str(trade["fees_usdt"]),
        "slippage_usdt": str(trade["slippage_usdt"]),
        "net_pnl_usdt": str(trade["net_pnl_usdt"]),
        "mae_r": _optional_number(trade.get("mae_r")),
        "mfe_r": _optional_number(trade.get("mfe_r")),
    }


def _optional_text(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _require_league_strategy_analytics(trades: Sequence[Mapping[str, object]]) -> None:
    invalid_scopes = sorted(
        {
            str(trade.get("account_scope", "UNKNOWN"))
            for trade in trades
            if trade.get("account_scope") != "LEAGUE"
        }
    )
    if invalid_scopes:
        raise ValueError(
            "전략 성과 분석은 독립 LEAGUE PAPER 계좌만 허용합니다: "
            + ", ".join(invalid_scopes)
        )


def _strategy_report(
    strategy_id: str,
    profile: str,
    trades: list[dict[str, object]],
    *,
    stress_verified: bool,
    unique_opportunity_count: int,
    raw_ledger_row_count: int,
    resolved_ledger_row_count: int,
    profile_raw_ledger_row_count: int,
    unresolved_ledger_row_count: int,
    profile_unresolved_ledger_row_count: int,
) -> dict[str, object]:
    ordered = sorted(trades, key=lambda trade: (trade["exit_ts_ms"], trade["trade_id"]))
    all_metrics = _window_metrics(ordered)
    sample_size = len(ordered)
    regimes = sorted({str(trade["regime"]) for trade in ordered})
    symbols = sorted({str(trade["symbol"]) for trade in ordered})
    sides = {side: sum(trade["side"] == side for trade in ordered) for side in ("LONG", "SHORT")}
    span_days = (
        (int(str(ordered[-1]["exit_ts_ms"])) - int(str(ordered[0]["entry_ts_ms"]))) / 86_400_000
        if ordered
        else 0.0
    )
    sample_status = _sample_status(
        sample_size,
        span_days=span_days,
        regime_count=len(regimes),
        stress_verified=stress_verified,
    )
    expectancy = all_metrics["expectancy_usdt"]
    profit_factor = all_metrics["profit_factor"]
    if sample_size < 30:
        recommendation = "관찰"
    elif (
        expectancy is not None
        and Decimal(str(expectancy)) < 0
        and profit_factor is not None
        and Decimal(str(profit_factor)) < 1
    ):
        recommendation = "중지 검토"
    elif (
        expectancy is not None
        and Decimal(str(expectancy)) > 0
        and profit_factor is not None
        and Decimal(str(profit_factor)) >= Decimal("1.10")
    ):
        recommendation = "유지"
    else:
        recommendation = "관찰"
    windows: dict[str, object] = {}
    for label, size in (("recent_50", 50), ("recent_100", 100), ("recent_300", 300)):
        windows[label] = _window_metrics(ordered[-size:])
    windows["all"] = all_metrics
    return {
        "strategy_id": strategy_id,
        "profile": profile,
        **all_metrics,
        "unique_opportunity_count": unique_opportunity_count,
        "profile_unique_opportunity_count": len(
            {
                (
                    str(trade["run_id"]),
                    str(trade["strategy_id"]),
                    str(trade["strategy_version"]),
                    str(trade["opportunity_id"]),
                    str(trade["symbol"]),
                    str(trade["side"]),
                )
                for trade in ordered
            }
        ),
        "raw_ledger_row_count": raw_ledger_row_count,
        "resolved_ledger_row_count": resolved_ledger_row_count,
        "profile_raw_ledger_row_count": profile_raw_ledger_row_count,
        "unresolved_ledger_row_count": unresolved_ledger_row_count,
        "profile_unresolved_ledger_row_count": profile_unresolved_ledger_row_count,
        "opportunity_grouping_status": ("NOT_PROVEN" if unresolved_ledger_row_count else "PROVEN"),
        "sample_status": sample_status,
        "sample_span_days": round(span_days, 4),
        "regime_count": len(regimes),
        "regimes": regimes,
        "symbols": symbols,
        "sides": sides,
        "stress_verified": stress_verified,
        "recommendation": recommendation,
        "recommendation_is_advisory": True,
        "windows": windows,
    }


def _aggregate_opportunity_rows(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: (int(str(row["exit_ts_ms"])), str(row["trade_id"])),
    )
    if len(ordered) == 1:
        return ordered[0]
    first = ordered[0]
    last = ordered[-1]
    aggregate = dict(first)
    aggregate.update(
        {
            "trade_id": f"{first['opportunity_id']}:{first['profile']}:AGGREGATED",
            "entry_ts_ms": min(int(str(row["entry_ts_ms"])) for row in ordered),
            "exit_ts_ms": max(int(str(row["exit_ts_ms"])) for row in ordered),
            "exit_price": last["exit_price"],
            "exit_reason": last["exit_reason"],
            "holding_ms": max(int(str(row["holding_ms"])) for row in ordered),
        }
    )
    for field_name in (
        "quantity",
        "gross_pnl_usdt",
        "fees_usdt",
        "slippage_usdt",
        "net_pnl_usdt",
        "runner_net_pnl_usdt",
        "trail_trigger_slippage_usdt",
    ):
        aggregate[field_name] = str(
            sum((Decimal(str(row.get(field_name, "0"))) for row in ordered), Decimal(0))
        )
    for field_name in ("mae_r", "mfe_r"):
        decimal_values = [
            Decimal(str(row[field_name])) for row in ordered if row.get(field_name) is not None
        ]
        aggregate[field_name] = (
            str(min(decimal_values) if field_name == "mae_r" else max(decimal_values))
            if decimal_values
            else None
        )
    for field_name in ("peak_unrealized_usdt", "giveback_usdt"):
        aggregate[field_name] = str(max(Decimal(str(row.get(field_name, "0"))) for row in ordered))
    for field_name in ("expected_gross_mfe_usdt", "expected_total_cost_usdt"):
        values = [row.get(field_name) for row in ordered]
        aggregate[field_name] = (
            str(values[0])
            if values and all(value is not None and value == values[0] for value in values)
            else None
        )
    for field_name in (
        "time_to_tp1_ms",
        "time_to_tp2_ms",
        "time_to_stop_ms",
        "trailing_activation_ts_ms",
        "runner_started_ts_ms",
    ):
        timing_values = [
            int(str(row[field_name])) for row in ordered if row.get(field_name) is not None
        ]
        aggregate[field_name] = min(timing_values) if timing_values else None
    return aggregate


def _window_metrics(trades: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not trades:
        return {
            "sample_size": 0,
            "wins": 0,
            "losses": 0,
            "breakevens": 0,
            "win_rate": None,
            "win_rate_ci95": None,
            "average_win_usdt": None,
            "average_loss_usdt": None,
            "payoff_ratio": None,
            "expectancy_usdt": None,
            "expectancy_r": None,
            "expectancy_bps": None,
            "profit_factor": None,
            "return_skew": None,
            "largest_trade_contribution": None,
            "cost_coverage": None,
            "cost_coverage_status": "NOT_PROVEN_MISSING_EXPECTED_COST_MODEL",
            "omega_ratio": None,
            "sortino_ratio_per_trade": None,
            "calmar_ratio_nonannualized": None,
            "downside_deviation_usdt": None,
            "gross_pnl": "0",
            "fees": "0",
            "slippage": "0",
            "net_pnl": "0",
            "cost_burden": None,
            "maximum_drawdown": "0",
            "turnover_usdt": "0",
            "turnover_ratio": "0",
            "mae_r_mean": None,
            "mfe_r_mean": None,
            "median_hold_ms": None,
            "p90_hold_ms": None,
            "tp1_sample_size": 0,
            "tp2_sample_size": 0,
            "stop_sample_size": 0,
            "median_time_to_tp1_ms": None,
            "median_time_to_tp2_ms": None,
            "median_time_to_stop_ms": None,
            "trail_activation_count": 0,
            "trail_activation_rate": None,
            "tp1_fill_rate": None,
            "runner_count": 0,
            "runner_rate": None,
            "runner_net_contribution_usdt": "0",
            "mfe_capture_ratio_mean": None,
            "average_peak_giveback_usdt": "0",
            "median_peak_giveback_usdt": "0",
            "p90_peak_giveback_usdt": "0",
            "trailing_exit_count": 0,
            "stop_before_trail_activation_count": 0,
            "activation_after_net_negative_exit_count": 0,
            "trail_trigger_slippage_usdt": "0",
            "regime_contributions": [],
            "metric_status": {
                "omega_ratio": "NOT_AVAILABLE_NO_LOSSES",
                "sortino_ratio_per_trade": "NOT_AVAILABLE_NO_DOWNSIDE",
                "calmar_ratio_nonannualized": "NOT_AVAILABLE_NO_DRAWDOWN",
                "turnover": "NOT_AVAILABLE_NO_TRADES",
            },
        }
    pnl = [Decimal(str(trade["net_pnl_usdt"])) for trade in trades]
    gross = sum((Decimal(str(trade["gross_pnl_usdt"])) for trade in trades), Decimal(0))
    fees = sum((Decimal(str(trade["fees_usdt"])) for trade in trades), Decimal(0))
    slippage = sum((Decimal(str(trade["slippage_usdt"])) for trade in trades), Decimal(0))
    net = sum(pnl, Decimal(0))
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    breakevens = [value for value in pnl if value == 0]
    win_rate = Decimal(len(wins)) / Decimal(len(pnl))
    average_win = sum(wins, Decimal(0)) / len(wins) if wins else None
    average_loss = sum(losses, Decimal(0)) / len(losses) if losses else None
    payoff = (
        average_win / abs(average_loss)
        if average_win is not None and average_loss is not None and average_loss != 0
        else None
    )
    gross_losses = abs(sum(losses, Decimal(0)))
    profit_factor = sum(wins, Decimal(0)) / gross_losses if gross_losses else None
    mean_pnl = net / len(pnl)
    variance = sum(((value - mean_pnl) ** 2 for value in pnl), Decimal(0)) / len(pnl)
    return_skew = None
    if len(pnl) >= 3 and variance > 0:
        standard_deviation = variance.sqrt()
        return_skew = sum(
            (((value - mean_pnl) / standard_deviation) ** 3 for value in pnl),
            Decimal(0),
        ) / len(pnl)
    absolute_pnl = sum((abs(value) for value in pnl), Decimal(0))
    largest_trade_contribution = (
        max(abs(value) for value in pnl) / absolute_pnl if absolute_pnl > 0 else None
    )
    expected_gross_mfe_values = [trade.get("expected_gross_mfe_usdt") for trade in trades]
    expected_total_cost_values = [trade.get("expected_total_cost_usdt") for trade in trades]
    cost_coverage_inputs_complete = all(
        value is not None for value in (*expected_gross_mfe_values, *expected_total_cost_values)
    )
    expected_gross_mfe = (
        sum((Decimal(str(value)) for value in expected_gross_mfe_values), Decimal(0))
        if cost_coverage_inputs_complete
        else None
    )
    expected_total_cost = (
        sum((Decimal(str(value)) for value in expected_total_cost_values), Decimal(0))
        if cost_coverage_inputs_complete
        else None
    )
    cost_coverage = (
        expected_gross_mfe / expected_total_cost
        if expected_gross_mfe is not None
        and expected_total_cost is not None
        and expected_total_cost > 0
        else None
    )
    risk_values: list[Decimal] = []
    bps_values: list[Decimal] = []
    for trade, value in zip(trades, pnl, strict=True):
        entry = Decimal(str(trade["entry_price"]))
        stop = Decimal(str(trade["initial_stop"]))
        quantity = Decimal(str(trade["quantity"]))
        risk = abs(entry - stop) * quantity
        if risk > 0:
            risk_values.append(value / risk)
        notional = entry * quantity
        if notional > 0:
            bps_values.append(value / notional * Decimal(10_000))
    equity = Decimal("1000")
    peak = equity
    maximum_drawdown = Decimal(0)
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    downside_deviation = (
        sum((min(value, Decimal(0)) ** 2 for value in pnl), Decimal(0)) / Decimal(len(pnl))
    ).sqrt()
    expectancy = net / len(pnl)
    sortino_ratio = expectancy / downside_deviation if downside_deviation > 0 else None
    calmar_ratio = net / maximum_drawdown if maximum_drawdown > 0 else None
    turnover = sum(
        (
            (Decimal(str(trade["entry_price"])) + Decimal(str(trade["exit_price"])))
            * Decimal(str(trade["quantity"]))
            for trade in trades
        ),
        Decimal(0),
    )
    holds = sorted(int(str(trade["holding_ms"])) for trade in trades)
    times_to_tp1 = sorted(
        int(str(trade["time_to_tp1_ms"]))
        for trade in trades
        if trade.get("time_to_tp1_ms") is not None
    )
    times_to_tp2 = sorted(
        int(str(trade["time_to_tp2_ms"]))
        for trade in trades
        if trade.get("time_to_tp2_ms") is not None
    )
    times_to_stop = sorted(
        int(str(trade["time_to_stop_ms"]))
        for trade in trades
        if trade.get("time_to_stop_ms") is not None
    )
    mae_values = [
        Decimal(str(trade["mae_r"])) for trade in trades if trade.get("mae_r") is not None
    ]
    mfe_values = [
        Decimal(str(trade["mfe_r"])) for trade in trades if trade.get("mfe_r") is not None
    ]
    trail_activation_count = sum(
        trade.get("trailing_activation_ts_ms") is not None for trade in trades
    )
    runner_count = sum(trade.get("runner_started_ts_ms") is not None for trade in trades)
    runner_net_contribution = sum(
        (Decimal(str(trade.get("runner_net_pnl_usdt", "0"))) for trade in trades),
        Decimal(0),
    )
    givebacks = sorted(Decimal(str(trade.get("giveback_usdt", "0"))) for trade in trades)
    peak_capture_ratios = [
        Decimal(str(trade["net_pnl_usdt"])) / Decimal(str(trade.get("peak_unrealized_usdt", "0")))
        for trade in trades
        if Decimal(str(trade.get("peak_unrealized_usdt", "0"))) > 0
    ]
    trail_trigger_slippage = sum(
        (Decimal(str(trade.get("trail_trigger_slippage_usdt", "0"))) for trade in trades),
        Decimal(0),
    )
    cost_denominator = sum((abs(value) for value in pnl), Decimal(0)) + fees + slippage
    regime_contributions = []
    for regime in sorted({str(trade["regime"]) for trade in trades}):
        regime_pnl = [
            Decimal(str(trade["net_pnl_usdt"]))
            for trade in trades
            if str(trade["regime"]) == regime
        ]
        regime_contributions.append(
            {
                "regime": regime,
                "sample_size": len(regime_pnl),
                "net_pnl": str(sum(regime_pnl, Decimal(0))),
                "expectancy_usdt": str(sum(regime_pnl, Decimal(0)) / len(regime_pnl)),
            }
        )
    return {
        "sample_size": len(pnl),
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(breakevens),
        "win_rate": str(win_rate),
        "win_rate_ci95": _wilson_interval(len(wins), len(pnl)),
        "average_win_usdt": str(average_win) if average_win is not None else None,
        "average_loss_usdt": str(average_loss) if average_loss is not None else None,
        "payoff_ratio": str(payoff) if payoff is not None else None,
        "expectancy_usdt": str(expectancy),
        "expectancy_r": str(sum(risk_values, Decimal(0)) / len(risk_values))
        if risk_values
        else None,
        "expectancy_bps": str(sum(bps_values, Decimal(0)) / len(bps_values))
        if bps_values
        else None,
        "profit_factor": str(profit_factor) if profit_factor is not None else None,
        "return_skew": str(return_skew) if return_skew is not None else None,
        "largest_trade_contribution": (
            str(largest_trade_contribution) if largest_trade_contribution is not None else None
        ),
        "cost_coverage": str(cost_coverage) if cost_coverage is not None else None,
        "cost_coverage_status": (
            "PROVEN_EXPECTED_MFE_OVER_TOTAL_COST"
            if cost_coverage is not None
            else "NOT_PROVEN_MISSING_EXPECTED_COST_MODEL"
        ),
        "omega_ratio": str(profit_factor) if profit_factor is not None else None,
        "sortino_ratio_per_trade": (str(sortino_ratio) if sortino_ratio is not None else None),
        "calmar_ratio_nonannualized": (str(calmar_ratio) if calmar_ratio is not None else None),
        "downside_deviation_usdt": str(downside_deviation),
        "gross_pnl": str(gross),
        "fees": str(fees),
        "slippage": str(slippage),
        "net_pnl": str(net),
        "cost_burden": str((fees + slippage) / cost_denominator) if cost_denominator > 0 else None,
        "maximum_drawdown": str(maximum_drawdown),
        "turnover_usdt": str(turnover),
        "turnover_ratio": str(turnover / Decimal("1000")),
        "mae_r_mean": str(sum(mae_values, Decimal(0)) / len(mae_values)) if mae_values else None,
        "mfe_r_mean": str(sum(mfe_values, Decimal(0)) / len(mfe_values)) if mfe_values else None,
        "median_hold_ms": int(median(holds)),
        "p90_hold_ms": _percentile(holds, 0.90),
        "tp1_sample_size": len(times_to_tp1),
        "tp2_sample_size": len(times_to_tp2),
        "stop_sample_size": len(times_to_stop),
        "median_time_to_tp1_ms": (int(median(times_to_tp1)) if times_to_tp1 else None),
        "median_time_to_tp2_ms": (int(median(times_to_tp2)) if times_to_tp2 else None),
        "median_time_to_stop_ms": (int(median(times_to_stop)) if times_to_stop else None),
        "trail_activation_count": trail_activation_count,
        "trail_activation_rate": str(Decimal(trail_activation_count) / Decimal(len(trades))),
        "tp1_fill_rate": str(Decimal(len(times_to_tp1)) / Decimal(len(trades))),
        "runner_count": runner_count,
        "runner_rate": str(Decimal(runner_count) / Decimal(len(trades))),
        "runner_net_contribution_usdt": str(runner_net_contribution),
        "mfe_capture_ratio_mean": (
            str(sum(peak_capture_ratios, Decimal(0)) / len(peak_capture_ratios))
            if peak_capture_ratios
            else None
        ),
        "average_peak_giveback_usdt": str(sum(givebacks, Decimal(0)) / len(givebacks)),
        "median_peak_giveback_usdt": str(median(givebacks)),
        "p90_peak_giveback_usdt": str(givebacks[max(0, ceil(len(givebacks) * 0.9) - 1)]),
        "trailing_exit_count": sum(
            str(trade.get("exit_reason")) == "TRAILING_STOP" for trade in trades
        ),
        "stop_before_trail_activation_count": sum(
            str(trade.get("exit_reason")) == "STOP"
            and trade.get("trailing_activation_ts_ms") is None
            for trade in trades
        ),
        "activation_after_net_negative_exit_count": sum(
            trade.get("trailing_activation_ts_ms") is not None
            and Decimal(str(trade["net_pnl_usdt"])) < 0
            for trade in trades
        ),
        "trail_trigger_slippage_usdt": str(trail_trigger_slippage),
        "regime_contributions": regime_contributions,
        "metric_status": {
            "omega_ratio": (
                "CALCULATED" if profit_factor is not None else "NOT_AVAILABLE_NO_LOSSES"
            ),
            "sortino_ratio_per_trade": (
                "CALCULATED" if sortino_ratio is not None else "NOT_AVAILABLE_NO_DOWNSIDE"
            ),
            "calmar_ratio_nonannualized": (
                "CALCULATED" if calmar_ratio is not None else "NOT_AVAILABLE_NO_DRAWDOWN"
            ),
            "turnover": "CALCULATED",
        },
    }


def _wilson_interval(wins: int, total: int) -> dict[str, str] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = wins / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z * sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    )
    return {
        "lower": str(max(0.0, center - margin)),
        "upper": str(min(1.0, center + margin)),
    }


def _percentile(values: Sequence[int], fraction: float) -> int | None:
    if not values:
        return None
    index = (len(values) - 1) * fraction
    lower = int(index)
    upper = min(len(values) - 1, lower + 1)
    weight = index - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight)


def _sample_status(
    sample_size: int,
    *,
    span_days: float,
    regime_count: int,
    stress_verified: bool,
) -> str:
    if sample_size < 30:
        return "표본 부족"
    if sample_size < 100:
        return "초기 관찰"
    if sample_size < 300:
        return "비교 가능" if span_days >= 7 else "비교 기간 부족"
    if span_days >= 21 and regime_count >= 3 and stress_verified:
        return "상대평가 가능"
    return "상대평가 조건 부족"


def _required_row(row: tuple[Any, ...] | None) -> tuple[Any, ...]:
    if row is None:
        raise RuntimeError("DuckDB 집계가 행을 반환하지 않았습니다.")
    return row


def _optional_number(value: object | None) -> float | None:
    return None if value is None else float(str(value))


def _optional_int(value: object | None) -> int | None:
    return None if value is None else int(str(value))
