"""fixture와 live 상태를 같은 한국어 대시보드 계약으로 직렬화한다."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast

from backend.app.domain.models import MarketEvent, SystemStatus


def build_dashboard_snapshot(
    status: SystemStatus,
    events: tuple[MarketEvent, ...],
    *,
    paused: bool,
    position_visible: bool,
    control_logs: tuple[dict[str, object], ...],
    archived_run_ids: tuple[str, ...],
    persisted_trades: tuple[Mapping[str, object], ...] = (),
    storage_label: str = "fixture memory",
) -> dict[str, Any]:
    symbols = sorted({event.symbol for event in events})
    latest_by_symbol = {event.symbol: event for event in events}
    scanner: list[dict[str, object]] = []
    regimes = ("RANGE", "TREND_UP", "TREND_DOWN", "WARMUP")
    for rank, symbol in enumerate(symbols, start=1):
        event = latest_by_symbol[symbol]
        bid = Decimal(str(event.data.get("bid", "0")))
        ask = Decimal(str(event.data.get("ask", "0")))
        mid = (bid + ask) / 2 if bid and ask else Decimal(0)
        spread_bps = float((ask - bid) / mid * 10_000) if mid else 0.0
        rejected = rank % 4 == 0
        scanner.append(
            {
                "rank": rank,
                "symbol": symbol,
                "depth": "DEEP" if rank <= status.deep_symbols else "WIDE",
                "regime": regimes[(rank - 1) % len(regimes)],
                "strategy": "LSA 반전" if rank % 2 else "CBR 추세",
                "side": "LONG" if rank % 2 else "SHORT",
                "score": None if rejected else round(max(0.45, 0.92 - rank * 0.035), 3),
                "net_rr": None if rejected else round(1.18 + rank * 0.03, 2),
                "expected_cost_bps": round(12.0 + rank * 0.2, 1),
                "spread_bps": round(spread_bps, 2),
                "data_health": "HEALTHY",
                "status": "REJECTED" if rejected else "OBSERVING",
                "reason": "비용 비중 34% > 허용 30%" if rejected else "구조·체결흐름 확인 중",
                "calibration": "CALIBRATING",
            }
        )
    selected = latest_by_symbol.get("SOLUSDT") or (events[-1] if events else None)
    chart = _chart_points(selected, events)
    position = None
    if position_visible and selected is not None:
        bid = Decimal(str(selected.data["bid"]))
        ask = Decimal(str(selected.data["ask"]))
        entry = (bid + ask) / 2
        position = {
            "symbol": selected.symbol,
            "venue": status.venue.value,
            "side": "LONG",
            "strategy": "LSA_REVERSAL_V1",
            "signal_time": selected.venue_ts_ms,
            "planned_entry": str(entry),
            "actual_entry": str(entry + Decimal("0.01")),
            "take_profit": str(entry + Decimal("0.75")),
            "initial_stop": str(entry - Decimal("0.45")),
            "quantity": "0.869",
            "notional": str((entry * Decimal("0.869")).quantize(Decimal("0.01"))),
            "risk_budget": "1.00",
            "maximum_planned_loss": "0.99",
            "gross_pnl": "0.31",
            "net_pnl": "0.12",
            "fees": "0.09",
            "slippage": "0.10",
            "elapsed_seconds": 121,
            "expected_resolution": "300초 진단값 · 강제종료 아님",
            "health": {
                "structure": 0.88,
                "flow": 0.72,
                "liquidity": 0.81,
                "edge": 0.67,
            },
            "management_reason": "진입 근거 유지 · 120초 강제종료 없음",
        }
    fixture_logs = [
        {
            "ts_ms": event.venue_ts_ms,
            "category": "MARKET_DATA",
            "level": "INFO",
            "message": f"{event.symbol} fixture 호가 수신 · LIVE 아님",
        }
        for event in events[-6:]
    ]
    return {
        "status": status.model_dump(mode="json"),
        "paused": paused,
        "scanner": scanner,
        "chart": chart,
        "position": position,
        "logs": [*control_logs[-10:], *fixture_logs],
        "history": _history_rows(status, archived_run_ids, persisted_trades),
        "performance": {
            "sample_size": 1,
            "gross_pnl": "1.80",
            "fees": "0.1212",
            "slippage": "0.20",
            "net_pnl": "1.4788",
            "max_drawdown": "0.00",
            "win_rate": "표본 1건 · 연구 판단 금지",
            "calibration": "CALIBRATING",
            "base_equity": "1001.4788",
            "stress_equity": "1001.2300",
        },
        "risk": {
            "risk_per_trade": "0.10%",
            "max_positions": 1,
            "daily_loss_limit": "5 USDT",
            "weekly_loss_limit": "15 USDT",
            "drawdown_lock": "3%",
            "active_locks": ["PAPER_ONLY"],
            "immutable_run": True,
        },
        "system": {
            "api_host": "127.0.0.1:8765",
            "public_endpoint_family": "BINANCE /public + /market",
            "auth_headers": False,
            "reconnects": 0,
            "sequence_gaps": 0,
            "resyncs": 0,
            "queue_depth": 0,
            "storage": storage_label,
            "retention_deep_book_days": 7,
            "retention_feature_days": 90,
            "trade_windows_retained": True,
            "disk_pressure_entry_lock": True,
            "app_version": "0.1.0-paper",
        },
    }


def _history_rows(
    status: SystemStatus,
    archived_run_ids: tuple[str, ...],
    persisted_trades: tuple[Mapping[str, object], ...],
) -> list[dict[str, object]]:
    if persisted_trades:
        return [
            {
                "run_id": str(trade["run_id"]),
                "trade_id": str(trade["trade_id"]),
                "symbol": str(trade["symbol"]),
                "strategy": str(trade["strategy_id"]),
                "side": str(trade["side"]),
                "entry": str(trade["entry_price"]),
                "exit": str(trade["exit_price"]),
                "exit_reason": str(trade["exit_reason"]),
                "gross_pnl": str(trade["gross_pnl_usdt"]),
                "fees": str(trade["fees_usdt"]),
                "slippage": str(trade["slippage_usdt"]),
                "net_pnl": str(trade["net_pnl_usdt"]),
                "holding_seconds": int(str(trade["holding_ms"])) // 1_000,
                "profile": str(trade.get("profile", "BASE")),
                "sample_type": "OFFLINE_FIXTURE",
            }
            for trade in reversed(persisted_trades)
        ]
    return [
        {
            "run_id": archived_run_ids[-1] if archived_run_ids else status.run_id,
            "trade_id": "fixture-trade-001",
            "symbol": "BTCUSDT",
            "strategy": "LSA_REVERSAL_V1",
            "side": "LONG",
            "entry": "100.10",
            "exit": "101.90",
            "exit_reason": "TAKE_PROFIT",
            "gross_pnl": "1.80",
            "fees": "0.1212",
            "slippage": "0.20",
            "net_pnl": "1.4788",
            "holding_seconds": 184,
            "profile": "BASE",
            "sample_type": "OFFLINE_FIXTURE",
        }
    ]


def _chart_points(
    selected: MarketEvent | None,
    events: tuple[MarketEvent, ...],
) -> dict[str, object]:
    symbol = selected.symbol if selected is not None else "BTCUSDT"
    matching = [event for event in events if event.symbol == symbol]
    if not matching:
        matching = list(events[-20:])
    points: list[dict[str, object]] = []
    for index, event in enumerate(matching[-30:]):
        bid = Decimal(str(event.data.get("bid", "100")))
        ask = Decimal(str(event.data.get("ask", "100.02")))
        mid = (bid + ask) / 2
        points.append(
            {
                "index": index,
                "ts_ms": event.venue_ts_ms,
                "bid": float(bid),
                "ask": float(ask),
                "mid": float(mid),
                "microprice": float(mid + Decimal("0.003")),
            }
        )
    entry = cast(float, points[-1]["mid"]) if points else 100.0
    return {
        "symbol": symbol,
        "interval": "1s",
        "points": points,
        "lines": {
            "entry": entry,
            "take_profit": entry + 0.75,
            "stop": entry - 0.45,
        },
        "fixture": True,
    }
