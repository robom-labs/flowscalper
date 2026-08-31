"""fixture와 live 상태를 같은 한국어 대시보드 계약으로 직렬화한다."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, cast

from backend.app.build_identity import APP_VERSION
from backend.app.domain.models import MarketEvent, SystemStatus
from backend.app.market_data.timeframes import TIMEFRAME_REGISTRY

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def release_identity() -> tuple[str, bool]:
    """실행환경이 선언한 불변 릴리스 commit과 격리 여부만 공개한다."""

    candidate = os.environ.get("ROBOM_RELEASE_COMMIT", "").strip().lower()
    commit = candidate if _COMMIT_PATTERN.fullmatch(candidate) else "development"
    isolated = commit != "development" and os.environ.get(
        "ROBOM_RELEASE_ISOLATED", "false"
    ).lower() in {"1", "true", "yes"}
    return commit, isolated


def build_dashboard_snapshot(
    status: SystemStatus,
    events: tuple[MarketEvent, ...],
    *,
    paused: bool,
    position_visible: bool,
    control_logs: tuple[dict[str, object], ...],
    archived_run_ids: tuple[str, ...],
    persisted_trades: tuple[Mapping[str, object], ...] = (),
    history_trades: tuple[Mapping[str, object], ...] | None = None,
    candle_rows: tuple[Mapping[str, object], ...] = (),
    chart_symbol: str | None = None,
    chart_interval_seconds: int = 1,
    runtime_diagnostics: Mapping[str, object] | None = None,
    scanner_rows: tuple[Mapping[str, object], ...] | None = None,
    strategies: tuple[Mapping[str, object], ...] = (),
    shadow_accounts: tuple[Mapping[str, object], ...] = (),
    league_accounts: tuple[Mapping[str, object], ...] = (),
    league_positions: tuple[Mapping[str, object], ...] = (),
    risk_contract: Mapping[str, object] | None = None,
    current_position: Mapping[str, object] | None = None,
    execution_audit: tuple[Mapping[str, object], ...] = (),
    storage_label: str = "fixture memory",
    api_host: str = "127.0.0.1:8765",
) -> dict[str, Any]:
    fixture_mode = status.mode.value == "DEMO_FIXTURE"
    ready_mode = status.mode.value == "READY"
    quoted_events = [event for event in events if "bid" in event.data and "ask" in event.data]
    symbols = sorted({event.symbol for event in quoted_events})
    latest_by_symbol = {event.symbol: event for event in quoted_events}
    scanner: list[dict[str, object]] = []
    regimes = ("RANGE", "TREND_UP", "TREND_DOWN", "WARMUP")
    for rank, symbol in enumerate(symbols, start=1):
        event = latest_by_symbol[symbol]
        bid = Decimal(str(event.data.get("bid", "0")))
        ask = Decimal(str(event.data.get("ask", "0")))
        mid = (bid + ask) / 2 if bid and ask else Decimal(0)
        spread_bps = float((ask - bid) / mid * 10_000) if mid else 0.0
        rejected = rank % 4 == 0
        live_calibrating = event.quality.is_live
        scanner.append(
            {
                "rank": rank,
                "symbol": symbol,
                "depth": (
                    "DEEP"
                    if event.quality.is_live and event.event_type in {"DEPTH_UPDATE", "ORDERBOOK"}
                    else "WIDE"
                    if event.quality.is_live
                    else "DEEP"
                    if rank <= status.deep_symbols
                    else "WIDE"
                ),
                "regime": "WARMUP" if live_calibrating else regimes[(rank - 1) % len(regimes)],
                "strategy": "WARMUP"
                if live_calibrating
                else "LSA 반전"
                if rank % 2
                else "CBR 추세",
                "side": "NONE" if live_calibrating else "LONG" if rank % 2 else "SHORT",
                "score": None
                if live_calibrating or rejected
                else round(max(0.45, 0.92 - rank * 0.035), 3),
                "net_rr": None if live_calibrating or rejected else round(1.18 + rank * 0.03, 2),
                "expected_cost_bps": round(spread_bps + 12.0, 1),
                "spread_bps": round(spread_bps, 2),
                "data_health": "HEALTHY" if event.quality.sequence_valid else "STALE",
                "status": "CALIBRATING"
                if live_calibrating
                else "REJECTED"
                if rejected
                else "OBSERVING",
                "reason": "실제 공개호가 수신 · feature warmup 중"
                if live_calibrating
                else "비용 비중 34% > 허용 30%"
                if rejected
                else "구조·체결흐름 확인 중",
                "calibration": "CALIBRATING",
            }
        )
    if scanner_rows is not None:
        scanner = [dict(row) for row in scanner_rows]
    depth_events = [event for event in events if event.event_type in {"DEPTH_UPDATE", "ORDERBOOK"}]
    selected = (
        depth_events[-1]
        if depth_events
        else latest_by_symbol.get("SOLUSDT") or (events[-1] if events else None)
    )
    if chart_symbol is not None:
        selected_events = [event for event in quoted_events if event.symbol == chart_symbol]
        if selected_events:
            selected = selected_events[-1]
    chart = _chart_points(selected, events, fixture_mode=fixture_mode)
    if chart_symbol is not None:
        chart["symbol"] = chart_symbol
    chart["interval"] = _interval_label(chart_interval_seconds)
    chart["candles"] = [dict(row) for row in candle_rows]
    position: dict[str, object] | None = None
    if position_visible and selected is not None and fixture_mode:
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
            "take_profit_1": str(entry + Decimal("0.45")),
            "take_profit_2": str(entry + Decimal("0.75")),
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
    elif current_position is not None:
        position = dict(current_position)
        chart["lines"] = {
            "entry": float(str(current_position["actual_entry"])),
            "take_profit": float(str(current_position["take_profit_1"])),
            "take_profit_2": (
                float(str(current_position["take_profit_2"]))
                if current_position.get("take_profit_2") is not None
                else None
            ),
            "stop": float(str(current_position["initial_stop"])),
        }
    fixture_logs = [
        {
            "ts_ms": event.venue_ts_ms,
            "category": "MARKET_DATA",
            "level": "INFO",
            "message": (
                f"{event.symbol} 검증된 공개 호가 수신 · LIVE"
                if event.quality.is_live
                else f"{event.symbol} fixture 호가 수신 · LIVE 아님"
            ),
        }
        for event in events[-6:]
    ]
    diagnostics = dict(runtime_diagnostics or {})
    release_commit, release_isolated = release_identity()
    return {
        "status": status.model_dump(mode="json"),
        "paused": paused,
        "operation_status": _operation_status(status, paused, diagnostics),
        "scanner": scanner,
        "chart": chart,
        "timeframes": TIMEFRAME_REGISTRY.public_rows(),
        "position": position,
        "logs": [*control_logs[-10:], *fixture_logs],
        "history": _history_rows(
            status,
            archived_run_ids,
            persisted_trades if history_trades is None else history_trades,
        ),
        "performance": _performance_rows(persisted_trades, fixture_mode=fixture_mode),
        "strategies": [dict(row) for row in strategies],
        "shadow_accounts": [dict(row) for row in shadow_accounts],
        "league_accounts": [dict(row) for row in league_accounts],
        "league_positions": [dict(row) for row in league_positions],
        "execution_audit": [dict(row) for row in execution_audit],
        "risk": dict(
            risk_contract
            or _default_risk_contract(
                account_count=len(league_accounts) or len(shadow_accounts),
            )
        ),
        "system": {
            "api_host": api_host,
            "public_endpoint_family": (
                "BYBIT V5 public linear"
                if status.venue.value == "BYBIT_LINEAR"
                else "BINANCE /public + /market"
            ),
            "auth_headers": False,
            "private_api_enabled": False,
            "api_key_enabled": False,
            "wallet_enabled": False,
            "runtime_ai_order_decision_enabled": False,
            "funding_readiness": "NOT_READY",
            "reconnects": 0,
            "sequence_gaps": 0,
            "resyncs": 0,
            "queue_depth": 0,
            "storage": storage_label,
            "retention_deep_book_days": 7,
            "retention_feature_days": 90,
            "trade_windows_retained": True,
            "disk_pressure_entry_lock": True,
            "app_version": APP_VERSION,
            "release_commit": release_commit,
            "release_isolated": release_isolated,
            "runtime_ready": ready_mode,
            **diagnostics,
        },
    }


def _operation_status(
    status: SystemStatus,
    paused: bool,
    diagnostics: Mapping[str, object],
) -> dict[str, object]:
    """초보자 화면에서 관찰 지속 여부와 PAPER 진입 상태를 분리한다."""

    mode = status.mode.value
    market_state = status.market_data_state.value
    flags = set(status.health_flags)
    lag = status.processing_lag_p95_ms
    if mode == "READY":
        return {
            "state": "READY",
            "title_ko": "시작 전",
            "detail_ko": "자동 관찰 시작을 한 번 누르면 공개시장 연결을 계속 유지합니다.",
            "market_observation_active": False,
            "paper_entry_active": False,
            "automatic_recovery": True,
            "recommended_action": "START",
            "lag_p95_ms": lag,
        }
    if mode == "DEMO_FIXTURE":
        return {
            "state": "DEMO_PAUSED" if paused else "DEMO_RUNNING",
            "title_ko": "샘플 멈춤" if paused else "샘플 작동 중",
            "detail_ko": "저장된 샘플 PAPER 화면이며 실제 공개시장은 아닙니다.",
            "market_observation_active": not paused,
            "paper_entry_active": not paused,
            "automatic_recovery": False,
            "recommended_action": "RESUME" if paused else "PAUSE",
            "lag_p95_ms": lag,
        }
    hard_blocked = bool(
        flags
        & {
            "PERSISTENCE_FAULT_ENTRY_LOCK",
            "RECOVERY_FAIL_CLOSED",
        }
    )
    if mode == "LIVE_SHADOW_PAPER" and paused and diagnostics.get("consumer_running") is False:
        return {
            "state": "SAFETY_BLOCKED",
            "title_ko": (
                "시장 처리 멈춤 · 안전 확인 필요"
                if hard_blocked
                else "시장 처리 멈춤 · 다시 시작 필요"
            ),
            "detail_ko": (
                "내부 시장 처리 작업이 멈췄고 저장 또는 복구 안전문제도 남아 있어 "
                "자동 재시작을 차단했습니다. 고급 진단의 원장 오류를 확인하세요."
                if hard_blocked
                else (
                    "공개시장 연결 화면은 남아 있지만 내부 시장 처리 작업이 멈췄습니다. "
                    "자동 관찰 시작을 누르면 같은 Run에서 안전하게 다시 연결합니다."
                )
            ),
            "market_observation_active": False,
            "paper_entry_active": False,
            "automatic_recovery": False,
            "recommended_action": "NONE" if hard_blocked else "START",
            "lag_p95_ms": lag,
        }
    if mode == "LIVE_SHADOW_PAPER" and paused and diagnostics.get("supervisor_running") is False:
        return {
            "state": "SAFETY_BLOCKED",
            "title_ko": (
                "시장 관찰 멈춤 · 안전 확인 필요"
                if hard_blocked
                else "시장 관찰 멈춤 · 다시 시작 필요"
            ),
            "detail_ko": (
                "공개시장 연결 작업이 종료됐고 저장 또는 복구 안전문제도 남아 있어 "
                "자동 재시작을 차단했습니다. 고급 진단의 원장 오류를 확인하세요."
                if hard_blocked
                else (
                    "공개시장 연결 작업이 종료돼 새 이벤트를 안전하게 처리할 수 없습니다. "
                    "자동 관찰 시작을 누르면 같은 Run에서 다시 연결합니다."
                )
            ),
            "market_observation_active": False,
            "paper_entry_active": False,
            "automatic_recovery": False,
            "recommended_action": "NONE" if hard_blocked else "START",
            "lag_p95_ms": lag,
        }
    if mode == "LIVE_SHADOW_PAPER" and paused and hard_blocked:
        return {
            "state": "SAFETY_BLOCKED",
            "title_ko": "작동 중 · 안전 확인 필요",
            "detail_ko": (
                "시장 관찰은 계속 중이지만 저장 또는 복구 안전문제로 새 PAPER 진입을 막았습니다."
            ),
            "market_observation_active": market_state == "LIVE",
            "paper_entry_active": False,
            "automatic_recovery": False,
            "recommended_action": "NONE",
            "lag_p95_ms": lag,
        }
    if mode == "LIVE_SHADOW_PAPER" and market_state != "LIVE":
        return {
            "state": "RECONNECTING",
            "title_ko": "시장 다시 연결 중",
            "detail_ko": "연결이 돌아오면 시장 관찰과 안전 확인을 자동으로 이어갑니다.",
            "market_observation_active": False,
            "paper_entry_active": False,
            "automatic_recovery": True,
            "recommended_action": "NONE",
            "lag_p95_ms": lag,
        }
    if mode == "LIVE_SHADOW_PAPER" and paused:
        manual_pause = bool(diagnostics.get("manual_pause_requested", False))
        if manual_pause:
            return {
                "state": "MANUALLY_PAUSED",
                "title_ko": "사용자가 일시정지",
                "detail_ko": (
                    "시장 관찰은 계속 중입니다. 버튼을 누르면 새 PAPER 진입을 다시 시작합니다."
                ),
                "market_observation_active": True,
                "paper_entry_active": False,
                "automatic_recovery": False,
                "recommended_action": "RESUME",
                "lag_p95_ms": lag,
            }
        return {
            "state": "SAFETY_WAITING",
            "title_ko": "작동 중 · 안전 대기",
            "detail_ko": (
                "시장 관찰은 계속 중입니다. 데이터가 정상화되면 "
                "새 PAPER 진입도 자동으로 다시 시작합니다."
            ),
            "market_observation_active": True,
            "paper_entry_active": False,
            "automatic_recovery": True,
            "recommended_action": "NONE",
            "lag_p95_ms": lag,
        }
    if mode == "LIVE_SHADOW_PAPER":
        return {
            "state": "RUNNING",
            "title_ko": "작동 중",
            "detail_ko": "공개시장을 계속 관찰하며 조건이 맞을 때만 PAPER 진입을 기록합니다.",
            "market_observation_active": True,
            "paper_entry_active": True,
            "automatic_recovery": True,
            "recommended_action": "PAUSE",
            "lag_p95_ms": lag,
        }
    return {
        "state": "REPLAY_RUNNING" if not paused else "REPLAY_PAUSED",
        "title_ko": "리플레이 작동 중" if not paused else "리플레이 멈춤",
        "detail_ko": "저장된 공개시장 이벤트를 PAPER 경로로 재생하고 있습니다.",
        "market_observation_active": not paused,
        "paper_entry_active": not paused,
        "automatic_recovery": False,
        "recommended_action": "RESUME" if paused else "PAUSE",
        "lag_p95_ms": lag,
    }


def _default_risk_contract(*, account_count: int = 0) -> dict[str, object]:
    """직접 serializer를 호출하는 격리 fixture에도 PAPER 기본계약을 제공한다."""

    return {
        "paper_only": True,
        "active_locks": ["PAPER_ONLY"],
        "immutable_run": True,
        "shared_capital": {
            "starting_equity_usdt": "1000",
            "risk_per_position": "0.10%",
            "max_positions": 1,
            "daily_loss_limit": "5 USDT",
            "weekly_loss_limit": "15 USDT",
            "drawdown_lock": "3.00%",
        },
        "strategy_league": {
            "account_count": account_count,
            "starting_equity_per_account_usdt": "1000",
            "risk_per_position": "0.50%",
            "max_positions_per_account": 3,
            "maximum_total_open_risk": "1.50%",
            "maximum_effective_leverage": "5.00x",
            "maximum_depth_fraction": "2.00%",
            "daily_loss_limit": "2.00%",
            "weekly_loss_limit": "5.00%",
            "drawdown_lock": "8.00%",
            "base_entry_fee": "6bp",
            "base_exit_fee": "6bp",
            "stress_entry_fee": "12bp",
            "stress_exit_fee": "12bp",
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
                "candidate_id": (
                    str(trade["candidate_id"])
                    if trade.get("candidate_id") is not None
                    else None
                ),
                "signal_event_id": (
                    str(trade["signal_event_id"])
                    if trade.get("signal_event_id") is not None
                    else None
                ),
                "opportunity_id": (
                    str(trade.get("candidate_id") or trade.get("signal_event_id"))
                    if trade.get("candidate_id") or trade.get("signal_event_id")
                    else "|".join(
                        (
                            str(trade["run_id"]),
                            str(trade["strategy_id"]),
                            str(trade["symbol"]),
                            str(trade["side"]),
                            str(trade["entry_ts_ms"]),
                        )
                    )
                ),
                "symbol": str(trade["symbol"]),
                "strategy": str(trade["strategy_id"]),
                "side": str(trade["side"]),
                "entry": str(trade["entry_price"]),
                "exit": str(trade["exit_price"]),
                "entry_ts_ms": int(str(trade["entry_ts_ms"])),
                "exit_ts_ms": int(str(trade["exit_ts_ms"])),
                "initial_stop": str(trade.get("initial_stop", "—")),
                "take_profit": str(trade.get("take_profit", "—")),
                "take_profit_1": (
                    str(trade["take_profit_1"]) if trade.get("take_profit_1") is not None else None
                ),
                "take_profit_2": (
                    str(trade["take_profit_2"]) if trade.get("take_profit_2") is not None else None
                ),
                "tp1_hit_ts_ms": _optional_int(trade.get("tp1_hit_ts_ms")),
                "tp2_hit_ts_ms": _optional_int(trade.get("tp2_hit_ts_ms")),
                "time_to_tp1_ms": _optional_int(trade.get("time_to_tp1_ms")),
                "time_to_tp2_ms": _optional_int(trade.get("time_to_tp2_ms")),
                "time_to_stop_ms": _optional_int(trade.get("time_to_stop_ms")),
                "trailing_activation_ts_ms": _optional_int(trade.get("trailing_activation_ts_ms")),
                "runner_started_ts_ms": _optional_int(trade.get("runner_started_ts_ms")),
                "peak_unrealized_usdt": str(trade.get("peak_unrealized_usdt", "0")),
                "giveback_usdt": str(trade.get("giveback_usdt", "0")),
                "runner_net_pnl_usdt": str(trade.get("runner_net_pnl_usdt", "0")),
                "trail_trigger_slippage_usdt": str(trade.get("trail_trigger_slippage_usdt", "0")),
                "trailing_state_checksum": (
                    str(trade["trailing_state_checksum"])
                    if trade.get("trailing_state_checksum") is not None
                    else None
                ),
                "quantity": str(trade.get("quantity", "—")),
                "exit_reason": str(trade["exit_reason"]),
                "gross_pnl": str(trade["gross_pnl_usdt"]),
                "fees": str(trade["fees_usdt"]),
                "slippage": str(trade["slippage_usdt"]),
                "net_pnl": str(trade["net_pnl_usdt"]),
                "holding_ms": int(str(trade["holding_ms"])),
                "holding_seconds": int(str(trade["holding_ms"])) // 1_000,
                "profile": str(trade.get("profile", "BASE")),
                "sample_type": str(trade.get("sample_type", "LIVE_PUBLIC")),
            }
            for trade in reversed(persisted_trades)
        ]
    if status.mode.value != "DEMO_FIXTURE":
        return []
    return [
        {
            "run_id": archived_run_ids[-1] if archived_run_ids else status.run_id,
            "trade_id": "fixture-trade-001",
            "candidate_id": "fixture-candidate-001",
            "signal_event_id": "fixture-signal-001",
            "opportunity_id": "fixture-candidate-001",
            "symbol": "BTCUSDT",
            "strategy": "LSA_REVERSAL_V1",
            "side": "LONG",
            "entry": "100.10",
            "exit": "101.90",
            "entry_ts_ms": 1_721_000_001_000,
            "exit_ts_ms": 1_721_000_185_000,
            "initial_stop": "99.65",
            "take_profit": "101.85",
            "take_profit_1": "101.40",
            "take_profit_2": "101.85",
            "tp1_hit_ts_ms": 1_721_000_121_000,
            "tp2_hit_ts_ms": 1_721_000_185_000,
            "time_to_tp1_ms": 120_000,
            "time_to_tp2_ms": 184_000,
            "time_to_stop_ms": None,
            "quantity": "0.869",
            "exit_reason": "TAKE_PROFIT",
            "gross_pnl": "1.80",
            "fees": "0.1212",
            "slippage": "0.20",
            "net_pnl": "1.4788",
            "holding_ms": 184_000,
            "holding_seconds": 184,
            "profile": "BASE",
            "sample_type": "OFFLINE_FIXTURE",
        }
    ]


def _optional_int(value: object | None) -> int | None:
    return None if value is None else int(str(value))


def _performance_rows(
    persisted_trades: tuple[Mapping[str, object], ...], *, fixture_mode: bool
) -> dict[str, object]:
    if persisted_trades:
        gross = sum(
            (Decimal(str(trade["gross_pnl_usdt"])) for trade in persisted_trades),
            start=Decimal(0),
        )
        fees = sum(
            (Decimal(str(trade["fees_usdt"])) for trade in persisted_trades),
            start=Decimal(0),
        )
        slippage = sum(
            (Decimal(str(trade["slippage_usdt"])) for trade in persisted_trades),
            start=Decimal(0),
        )
        net = sum(
            (Decimal(str(trade["net_pnl_usdt"])) for trade in persisted_trades),
            start=Decimal(0),
        )
        sample_size = len(persisted_trades)
        return {
            "sample_size": sample_size,
            "gross_pnl": str(gross),
            "fees": str(fees),
            "slippage": str(slippage),
            "net_pnl": str(net),
            "max_drawdown": "0.00",
            "win_rate": f"표본 {sample_size}건 · 연구 판단 주의",
            "calibration": "CALIBRATING" if sample_size < 30 else "RESEARCH_SAMPLE",
            "base_equity": str(Decimal("1000") + net),
            "stress_equity": "별도 STRESS 리플레이 필요",
        }
    if not fixture_mode:
        return {
            "sample_size": 0,
            "gross_pnl": "0",
            "fees": "0",
            "slippage": "0",
            "net_pnl": "0",
            "max_drawdown": "0",
            "win_rate": "표본 없음",
            "calibration": "CALIBRATING",
            "base_equity": "1000",
            "stress_equity": "표본 없음",
        }
    return {
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
    }


def _chart_points(
    selected: MarketEvent | None,
    events: tuple[MarketEvent, ...],
    *,
    fixture_mode: bool,
) -> dict[str, object]:
    symbol = selected.symbol if selected is not None else "BTCUSDT"
    matching = [
        event
        for event in events
        if event.symbol == symbol and "bid" in event.data and "ask" in event.data
    ]
    if not matching:
        matching = [event for event in events[-20:] if "bid" in event.data and "ask" in event.data]
    points: list[dict[str, object]] = []
    for index, event in enumerate(matching[-30:]):
        bid = Decimal(str(event.data["bid"]))
        ask = Decimal(str(event.data["ask"]))
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
    is_fixture = fixture_mode
    return {
        "symbol": symbol,
        "interval": "1s",
        "points": points,
        "lines": {
            "entry": entry if fixture_mode and points else None,
            "take_profit": entry + 0.45 if fixture_mode and points else None,
            "take_profit_2": entry + 0.75 if fixture_mode and points else None,
            "stop": entry - 0.45 if fixture_mode and points else None,
        },
        "fixture": is_fixture,
    }


def _interval_label(seconds: int) -> str:
    return TIMEFRAME_REGISTRY.label(seconds)
