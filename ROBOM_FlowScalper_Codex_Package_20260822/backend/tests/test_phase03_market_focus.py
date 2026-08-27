"""3차 시장 탐색·안전 회전·포지션 리플레이의 공개 계약을 검증한다."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.analytics.reports import TradeAnalytics
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import RuntimeMode, Venue
from backend.app.main import create_app
from backend.app.market_data.supervisor import _safe_rotate_deep
from backend.app.market_explorer import CatalogRow, MarketExplorerService
from backend.app.replay.focus import ReplayFocusSessionBuilder
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import SQLiteLedger
from backend.tests.test_candidate_paper_portfolio import book, candidate_plan


async def test_market_catalog_and_candles_remain_public_and_role_separated() -> None:
    async def binance() -> list[CatalogRow]:
        return [
            CatalogRow(
                "BINANCE_USDM",
                "BTCUSDT",
                "BTC/USDT",
                "BTC",
                "USDT",
                "PAPER_EXECUTION",
                100,
                99.9,
                100.1,
                1.2,
                1_000_000,
                50_000,
                "ACTIVE",
            )
        ]

    async def upbit() -> list[CatalogRow]:
        return [
            CatalogRow(
                "UPBIT_KRW",
                "KRW-BTC",
                "BTC/KRW",
                "BTC",
                "KRW",
                "OBSERVATION_ONLY",
                100_000,
                0,
                0,
                0.5,
                2_000_000,
                0,
                "ACTIVE",
                korean_name="비트코인",
                english_name="Bitcoin",
                strategy_eligible=False,
            )
        ]

    async def candles(symbol: str, interval: int, limit: int) -> list[dict[str, object]]:
        assert (symbol, interval, limit) == ("BTCUSDT", 180, 200)
        return [
            {
                "time": 2,
                "open_ts_ms": 2_000,
                "open": 101,
                "high": 102,
                "low": 100,
                "close": 101,
                "volume": 2,
                "trade_count": 2,
            },
            {
                "time": 1,
                "open_ts_ms": 1_000,
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1,
                "trade_count": 1,
            },
            {
                "time": 2,
                "open_ts_ms": 2_000,
                "open": 101,
                "high": 103,
                "low": 100,
                "close": 102,
                "volume": 3,
                "trade_count": 3,
            },
        ]

    async def upbit_candles(symbol: str, interval: int, limit: int) -> list[dict[str, object]]:
        assert (symbol, interval, limit) == ("KRW-BTC", 180, 200)
        return [
            {
                "time": 2,
                "open_ts_ms": 2_000,
                "open": 101,
                "high": 102,
                "low": 100,
                "close": 101,
                "volume": 2,
                "trade_count": 0,
            },
            {
                "time": 1,
                "open_ts_ms": 1_000,
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 1,
                "trade_count": 0,
            },
        ]

    service = MarketExplorerService(
        binance_catalog_loader=binance,
        upbit_catalog_loader=upbit,
        binance_candle_loader=candles,
        upbit_candle_loader=upbit_candles,
    )
    client = TestClient(create_app(PaperRuntime(mode=RuntimeMode.READY), market_explorer=service))

    catalog = client.get("/api/markets/catalog").json()
    dashboard = client.get("/api/dashboard").json()
    history = client.get(
        "/api/markets/candles?symbol=btcusdt&interval_seconds=180&limit=200"
    ).json()
    upbit_history = client.get(
        "/api/markets/candles?source=UPBIT_KRW&symbol=KRW-BTC&interval_seconds=180&limit=200"
    ).json()

    assert catalog["counts"] == {"BINANCE_USDM": 1, "UPBIT_KRW": 1, "total": 2}
    assert [row["market_role"] for row in catalog["rows"]] == [
        "PAPER_EXECUTION",
        "OBSERVATION_ONLY",
    ]
    assert catalog["auth_required"] is False and catalog["real_orders_enabled"] is False
    assert [row["interval_seconds"] for row in dashboard["timeframes"]] == [
        60,
        180,
        300,
        900,
        1_800,
        3_600,
        14_400,
    ]
    assert [row["open_ts_ms"] for row in history["candles"]] == [1_000, 2_000]
    assert history["candles"][-1]["close"] == 102
    assert [row["open_ts_ms"] for row in upbit_history["candles"]] == [1_000, 2_000]
    assert upbit_history["observation_only"] is True
    assert upbit_history["real_orders_enabled"] is False


async def test_every_ui_timeframe_is_supported_by_both_public_chart_sources() -> None:
    calls: list[tuple[str, int, int]] = []

    async def loader(symbol: str, interval: int, limit: int) -> list[dict[str, object]]:
        calls.append((symbol, interval, limit))
        return []

    service = MarketExplorerService(
        binance_candle_loader=loader,
        upbit_candle_loader=loader,
    )
    intervals = (60, 180, 300, 900, 1_800, 3_600, 14_400)
    for source, symbol in (("BINANCE_USDM", "BTCUSDT"), ("UPBIT_KRW", "KRW-BTC")):
        for interval in intervals:
            response = await service.candles(source, symbol, interval)
            assert response["interval_seconds"] == interval

    assert len(calls) == len(intervals) * 2
    with pytest.raises(ValueError, match="지원하지 않는 차트 시간구간"):
        await service.candles("BINANCE_USDM", "BTCUSDT", 600)


def test_safe_rotation_caps_changes_and_protects_positions() -> None:
    previous = tuple(f"S{index:02d}USDT" for index in range(20))
    proposed = tuple(f"S{index:02d}USDT" for index in range(10, 30))
    since = {symbol: 0 for symbol in previous}

    rotated = _safe_rotate_deep(
        previous,
        proposed,
        ranked_symbols=(*proposed, *previous),
        now_ms=31 * 60 * 1_000,
        since_ms=since,
        pinned_symbols=("S19USDT",),
    )

    assert len(set(previous) - set(rotated)) == 4
    assert len(set(rotated) - set(previous)) == 4
    assert "S19USDT" in rotated


def test_strategy_symbol_report_requires_thirty_samples() -> None:
    rows = []
    for index in range(30):
        rows.append(
            {
                "trade_id": f"trade-{index}",
                "strategy_id": "LSA_REVERSAL_V1",
                "profile": "BASE",
                "venue": "BINANCE_USDM",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "net_pnl_usdt": "1" if index % 2 == 0 else "-0.5",
                "gross_pnl_usdt": "1.2" if index % 2 == 0 else "-0.3",
                "fees_usdt": "0.1",
                "slippage_usdt": "0.1",
                "entry_ts_ms": index * 1_000,
                "exit_ts_ms": index * 1_000 + 500,
            }
        )
    report = TradeAnalytics().strategy_symbol_reports(rows)[0]

    assert report["sample_size"] == 30
    assert report["ranking_eligible"] is True
    assert report["sample_status"] == "RESEARCH_SAMPLE"


def test_trade_focus_replay_hides_future_markers_and_is_deterministic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "phase03.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        run_id="run-phase03",
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime.boot_fixture()
    trade = ledger.list_trades(runtime.run_id)[0]

    def broad_trade_scan_forbidden(*_args, **_kwargs):
        raise AssertionError("거래 집중 재생은 전체 거래표를 읽으면 안 됩니다.")

    monkeypatch.setattr(ledger, "list_trades", broad_trade_scan_forbidden)
    monkeypatch.setattr(ledger, "list_shadow_trades", broad_trade_scan_forbidden)
    client = TestClient(create_app(runtime))
    path = f"/api/replay/{runtime.run_id}/focus?trade_id={trade['trade_id']}&profile=BASE"

    first = client.get(path)
    second = client.get(path)

    assert first.status_code == 200
    session = first.json()
    assert session == second.json()
    assert ledger.count("replay_focus_cache") == 1
    assert session["session_version"] == 7
    assert session["default_speed"] == 5
    assert session["speeds"] == [0.5, 1, 2, 5, 10, 20, 40, 80]
    assert session["paper_only"] is True
    assert session["real_orders_enabled"] is False and session["auth_required"] is False
    assert session["reconciliation"]["applicable"] is False
    assert session["reconciliation"]["matched"] is None
    assert session["reconciliation"]["reason"] == "OFFLINE_FIXTURE_UI_ONLY"
    assert all(
        all(marker["ts_ms"] <= frame["ts_ms"] for marker in frame["markers"])
        for frame in session["frames"]
    )
    assert {frame["phase"] for frame in session["frames"]} == {
        "PRE_ENTRY",
        "OPEN",
        "CLOSED",
    }
    assert session["frames"][-1]["event_type"] == "PAPER_LEDGER_TRANSITION"
    assert session["frames"][-1]["phase"] == "CLOSED"
    assert [marker["kind"] for marker in session["frames"][-1]["markers"]] == [
        "SIGNAL",
        "ENTRY",
        "EXIT",
    ]
    assert session["levels"] == {
        "signal_ts_ms": trade["entry_ts_ms"],
        "entry": trade["entry_price"],
        "initial_stop": trade["initial_stop"],
        "take_profit_1": trade["take_profit"],
        "take_profit_2": None,
    }
    assert sum(Decimal(fill["fee_usdt"]) for fill in session["fills"]) == Decimal(
        trade["fees_usdt"]
    )
    assert sum(
        Decimal(fill["slippage_usdt"]) for fill in session["fills"]
    ) == Decimal(trade["slippage_usdt"])
    assert [fill["intent"] for fill in session["fills"]] == ["ENTRY", "EXIT"]
    assert session["reconciliation"]["replay_final_state"] == "NOT_RUN"


def test_trade_focus_replay_returns_session_when_optional_cache_is_busy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(tmp_path / "phase03-cache-busy.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        run_id="run-phase03-cache-busy",
        clock=DeterministicClock(),
        ledger=ledger,
    )
    runtime.boot_fixture()
    trade = ledger.list_trades(runtime.run_id)[0]

    def cache_busy(*_args, **_kwargs) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ledger, "record_replay_focus_session", cache_busy)
    client = TestClient(create_app(runtime))
    response = client.get(
        f"/api/replay/{runtime.run_id}/focus"
        f"?trade_id={trade['trade_id']}&profile=BASE"
    )

    assert response.status_code == 200
    assert response.json()["trade_id"] == trade["trade_id"]
    assert ledger.count("replay_focus_cache") == 0


def test_trade_focus_reuses_verified_replay_covering_trade_window(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "covered-focus.sqlite3")
    ledger.start_run(
        "run-covered-focus",
        mode="LIVE_SHADOW_PAPER",
        venue=Venue.BINANCE_USDM.value,
        config={"seed": 20260822},
        started_ts_ms=1_000,
    )
    replay = {
        "replay_id": "replay-covered-focus",
        "source_run_id": "run-covered-focus",
        "created_ts_ms": 4_000,
        "checksum": "a" * 64,
        "first_ts_ms": 1_000,
        "last_ts_ms": 3_000,
        "main_trade_count": 1,
        "shadow_trade_count": 2,
        "final_state": "MAIN_TRADES_CLOSED",
        "real_orders_enabled": False,
        "auth_required": False,
    }
    ledger.record_replay_run(replay)

    covered = ReplayFocusSessionBuilder._covering_replay_result(
        ledger,
        run_id="run-covered-focus",
        entry_ts_ms=1_500,
        exit_ts_ms=2_500,
    )

    assert covered == replay
    assert ReplayFocusSessionBuilder._covering_replay_result(
        ledger,
        run_id="run-covered-focus",
        entry_ts_ms=1_500,
        exit_ts_ms=3_500,
    ) is None
    ledger.close()


def test_universe_snapshots_are_append_only(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "universe.sqlite3")
    ledger.start_run(
        "run-universe", mode="LIVE_SHADOW_PAPER", venue="BINANCE_USDM", config={}, started_ts_ms=1
    )
    ledger.record_universe_snapshot(
        {
            "snapshot_id": "u-1",
            "run_id": "run-universe",
            "ts_ms": 10,
            "wide_symbols": ["BTCUSDT"],
            "deep_symbols": ["BTCUSDT"],
        }
    )
    ledger.record_universe_snapshot(
        {
            "snapshot_id": "u-2",
            "run_id": "run-universe",
            "ts_ms": 20,
            "wide_symbols": ["BTCUSDT", "ETHUSDT"],
            "deep_symbols": ["BTCUSDT", "ETHUSDT"],
        }
    )

    assert [row["snapshot_id"] for row in ledger.list_universe_snapshots("run-universe")] == [
        "u-1",
        "u-2",
    ]


def test_focus_position_contract_is_fill_backed_and_permanently_paper() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id="run-live-1",
        venue=Venue.FIXTURE,
        paused=False,
    )
    plan = replace(candidate_plan(), venue=Venue.BINANCE_USDM)
    runtime.paper_portfolio.offer((plan,), entries_paused=False)
    runtime.paper_portfolio.on_book(replace(book(1_249), venue=Venue.BINANCE_USDM))
    live_book = replace(book(1_250), venue=Venue.BINANCE_USDM)
    runtime.paper_portfolio.on_book(live_book)
    runtime.latest_books["BTCUSDT"] = live_book
    row = runtime.focus_positions()[0]
    required = {
        "account_id",
        "trade_id",
        "candidate_id",
        "venue",
        "strategy_id",
        "strategy_display_name_ko",
        "profile",
        "symbol",
        "side",
        "actual_entry",
        "initial_stop",
        "current_stop",
        "take_profit_1",
        "current_mark",
        "original_quantity",
        "remaining_quantity",
        "notional_usdt",
        "margin_used_usdt",
        "effective_leverage",
        "maximum_planned_loss_usdt",
        "entry_fee_usdt",
        "estimated_exit_fee_usdt",
        "slippage_usdt",
        "gross_pnl_usdt",
        "net_pnl_usdt",
        "account_current_equity_usdt",
        "stage",
        "stage_ko",
        "data_health",
        "paper_only",
        "real_orders_enabled",
        "auth_required",
    }
    assert required <= row.keys()
    managed = runtime.paper_portfolio.main.position
    assert managed is not None
    assert row["actual_entry"] == str(managed.protected.entry_fill.average_price)
    assert Decimal(str(row["effective_leverage"])) <= Decimal(5)
    assert row["paper_only"] is True
    assert row["real_orders_enabled"] is False
    assert row["auth_required"] is False
