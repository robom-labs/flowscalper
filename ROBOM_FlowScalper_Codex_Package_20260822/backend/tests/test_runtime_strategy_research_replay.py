# 연구 replay가 한 전략만 실제 PAPER 체결 경로로 처리하는지 검증한다.

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.app.domain.models import DataQuality, MarketEvent, Venue
from scripts.research_runtime_strategy_replay import build_result, replay_archive_run


def _event(run_id: str, *, event_id: str, ts_ms: int, event_type: str) -> MarketEvent:
    data: dict[str, object]
    if event_type == "TRADE":
        data = {
            "price": "100.05",
            "quantity": "0.5",
            "buyer_is_aggressor": True,
        }
    else:
        data = {
            "bid": "99.9",
            "bid_qty": "100",
            "ask": "100.1",
            "ask_qty": "100",
            "bids": [["99.9", "100"], ["99.8", "100"]],
            "asks": [["100.1", "100"], ["100.2", "100"]],
        }
    return MarketEvent(
        event_id=event_id,
        run_id=run_id,
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        event_type=event_type,
        venue_ts_ms=ts_ms,
        transaction_ts_ms=ts_ms if event_type == "TRADE" else None,
        receive_monotonic_ns=ts_ms * 1_000_000,
        sequence_start=ts_ms if event_type == "DEPTH_UPDATE" else None,
        sequence_end=ts_ms if event_type == "DEPTH_UPDATE" else None,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=10,
        ),
        data=data,
    )


def _write_events(path: Path, events: list[MarketEvent]) -> None:
    rows = []
    for event in events:
        payload = event.model_dump(mode="json")
        payload["receive_ts_ms"] = event.venue_ts_ms + 10
        rows.append(
            {
                "ts_ms": event.venue_ts_ms,
                "venue_ts_ms": event.venue_ts_ms,
                "symbol": event.symbol,
                "payload_json": json.dumps(payload),
            }
        )
    pq.write_table(pa.Table.from_pylist(rows), path / "events.parquet")


def test_runtime_strategy_replay_is_single_strategy_paper_only(tmp_path: Path) -> None:
    run_id = "run-research-test"
    _write_events(
        tmp_path,
        [
            _event(run_id, event_id="depth-1", ts_ms=1_000, event_type="DEPTH_UPDATE"),
            _event(run_id, event_id="trade-1", ts_ms=1_100, event_type="TRADE"),
            _event(run_id, event_id="depth-2", ts_ms=1_500, event_type="DEPTH_UPDATE"),
        ],
    )

    result = replay_archive_run(
        "RUN-RESEARCH-TEST",
        tmp_path,
        strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
    )

    assert result["event_count"] == 3
    assert result["run_id"] == "RUN-RESEARCH-TEST"
    assert result["runtime_run_id"] == run_id
    assert result["strategy_mode"] == "SHADOW"
    assert result["enabled_other_strategies"] == []
    assert result["real_orders_enabled"] is False
    assert result["auth_required"] is False
    assert result["ledger_attached"] is False
    assert result["trade_count"] == 0
    assert dict(result["open_state"])["censored_count"] == 0


def test_runtime_strategy_replay_rejects_unknown_strategy_and_bad_limit(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="알 수 없는 전략"):
        replay_archive_run("run", tmp_path, strategy_id="UNKNOWN")
    with pytest.raises(ValueError, match="양수"):
        replay_archive_run(
            "run",
            tmp_path,
            strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
            maximum_events=0,
        )


def test_runtime_strategy_replay_builds_not_proven_empty_summary(tmp_path: Path) -> None:
    run_id = "RUN-RESEARCH-SUMMARY"
    run_dir = tmp_path / f"run={run_id}"
    run_dir.mkdir()
    _write_events(
        run_dir,
        [_event(run_id, event_id="depth-1", ts_ms=1_000, event_type="DEPTH_UPDATE")],
    )

    result = build_result(
        tmp_path,
        strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
        run_ids=(run_id,),
    )

    assert result["real_orders_enabled"] is False
    assert result["overall"]["trade_row_count"] == 0
    assert result["overall"]["observed_70_percent_gate_passed"] is False
    assert result["overall"]["profitability_status"] == "NOT_PROVEN"
