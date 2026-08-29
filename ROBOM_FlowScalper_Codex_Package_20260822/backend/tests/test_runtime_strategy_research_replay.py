# 연구 replay가 한 전략만 실제 PAPER 체결 경로로 처리하는지 검증한다.

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.app.domain.models import DataQuality, MarketEvent, Side, Venue
from backend.app.features import FeatureSnapshot
from backend.app.regime import Regime
from backend.app.strategies.base import CandidateDecision, CandidateStatus
from backend.app.strategies.runtime_evaluator import EvaluatedSignal
from scripts.research_runtime_strategy_replay import (
    ResearchSignalGateEvaluator,
    _summary,
    build_result,
    frozen_dataset_reference,
    replay_archive_run,
    tp1_feasibility_gate_rejections,
)


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


def _write_dataset_manifest(path: Path, run_id: str) -> None:
    payload: dict[str, object] = {
        "status": "FROZEN_HISTORICAL_FORWARD_PENDING",
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "runtime_ai_enabled": False,
        "archive_verification": {"status": "PASS", "run_count": 1, "event_count": 1},
        "runs": [
            {
                "run_id": run_id,
                "role": "FINAL_OOS",
                "event_count": 1,
                "start_ts_ms": 1_000,
                "end_ts_ms": 1_000,
                "checksum": "a" * 64,
            }
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["manifest_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    path.write_text(json.dumps(payload))


def _feature(side: Side = Side.LONG) -> FeatureSnapshot:
    direction = 1 if side is Side.LONG else -1
    return FeatureSnapshot(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        ts_ms=121_000,
        sample_count=300,
        warmup_seconds=120,
        data_healthy=True,
        lag_ms=10,
        mid=100,
        spread_bps=1,
        depth_bid_10=1_000_000,
        depth_ask_10=1_000_000,
        imbalance_top1=0.10 * direction,
        imbalance_top5=0.10 * direction,
        imbalance_top10=0.08 * direction,
        microprice=100 + 0.01 * direction,
        microprice_minus_mid_bps=1 * direction,
        ofi_250ms=10 * direction,
        ofi_1s=20 * direction,
        ofi_3s=30 * direction,
        ofi_10s=40 * direction,
        trade_imbalance_1s=0.20 * direction,
        trade_imbalance_3s=0.15 * direction,
        trade_imbalance_10s=0.10 * direction,
        signed_notional_3s=10_000 * direction,
        refill_ratio=0.6,
        cancel_ratio=0.4,
        price_response_efficiency=0.2,
        realized_volatility_30s=0.001,
        realized_volatility_120s=0.001,
        compression_ratio=1,
        efficiency_ratio_30s=0.5,
        micro_vwap_10s=100,
        multi_level_microprice_10=100 + 0.01 * direction,
        multi_level_microprice_10_minus_mid_bps=1 * direction,
        depth_adjusted_ofi_3s_bps=2 * direction,
        bid_book_slope_10=100,
        ask_book_slope_10=100,
        trade_count_3s=20,
        trade_notional_3s=100_000,
        bid_refill_ratio_3s=0.6,
        ask_refill_ratio_3s=0.6,
        bid_cancel_ratio_3s=0.4,
        ask_cancel_ratio_3s=0.4,
    )


def _qualified_signal(side: Side = Side.LONG) -> EvaluatedSignal:
    direction = Decimal(1) if side is Side.LONG else Decimal(-1)
    entry = Decimal("100")
    return EvaluatedSignal(
        symbol="BTCUSDT",
        regime=Regime.RANGE,
        decision=CandidateDecision(
            strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
            side=side,
            status=CandidateStatus.QUALIFIED,
            reason_codes=("BASELINE_QUALIFIED",),
            rejection_codes=(),
            planned_entry=entry,
            initial_stop=entry - direction * Decimal("0.30"),
            take_profit=entry + direction * Decimal("0.96"),
            expected_cost_bps=Decimal("13"),
            net_reward_risk=Decimal("2.0"),
        ),
        main_eligible=False,
        shadow_eligible=True,
    )


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
    with pytest.raises(ValueError, match="연구 신호 gate"):
        replay_archive_run(
            "run",
            tmp_path,
            strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
            signal_gate="UNKNOWN",
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
    assert result["overall"]["ranking_eligible"] is False
    assert "UNIQUE_MARKET_OPPORTUNITIES_BELOW_30" in result["overall"][
        "ranking_blockers"
    ]
    assert result["overall"]["profitability_status"] == "NOT_PROVEN"
    assert result["signal_gate"] == "NONE"
    assert result["overall"]["signal_gate_diagnostics"]["can_create_signals"] is False


def test_replay_summary_never_promotes_on_win_rate_without_robustness_gates() -> None:
    reports = [
        {
            "profile": profile,
            "sample_size": 30,
            "wins": 24,
            "losses": 6,
            "win_rate": "0.8",
            "expectancy_usdt": "0.1",
            "net_pnl": "3",
            "profit_factor": "2",
        }
        for profile in ("BASE", "STRESS")
    ]
    trades = [
        {
            "run_id": "RUN-A",
            "signal_event_id": f"signal-{index}",
            "strategy_id": "VWAP_EXHAUSTION_REVERSION_V1",
            "side": "LONG",
            "profile": profile,
            "exit_reason": "TAKE_PROFIT",
            "holding_ms": 60_000,
            "tp1_hit_ts_ms": 1,
            "tp2_hit_ts_ms": 2,
        }
        for index in range(30)
        for profile in ("BASE", "STRESS")
    ]
    with patch(
        "scripts.research_runtime_strategy_replay.TradeAnalytics.strategy_reports",
        return_value=reports,
    ):
        summary = _summary(
            ({"event_count": 100, "trade_rows": trades, "open_state": {}},),
            strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
        )

    assert summary["unique_market_opportunity_count"] == 30
    assert summary["observed_70_percent_gate_passed"] is True
    assert summary["cost_performance_gate_passed"] is True
    assert summary["robustness_gate_passed"] is False
    assert summary["ranking_eligible"] is False
    assert "DSR_NOT_EVALUATED" in summary["ranking_blockers"]
    assert "PBO_NOT_EVALUATED" in summary["ranking_blockers"]


def test_replay_summary_deduplicates_profiles_before_the_30_opportunity_gate() -> None:
    reports = [
        {
            "profile": profile,
            "sample_size": 30,
            "wins": 30,
            "losses": 0,
            "win_rate": "1",
            "expectancy_usdt": "1",
            "net_pnl": "30",
            "profit_factor": None,
        }
        for profile in ("BASE", "STRESS")
    ]
    trades = [
        {
            "run_id": "RUN-A",
            "signal_event_id": "same-signal",
            "strategy_id": "VWAP_EXHAUSTION_REVERSION_V1",
            "side": "LONG",
            "profile": profile,
            "exit_reason": "TAKE_PROFIT",
            "holding_ms": 60_000,
            "tp1_hit_ts_ms": 1,
            "tp2_hit_ts_ms": 2,
        }
        for _ in range(30)
        for profile in ("BASE", "STRESS")
    ]
    with patch(
        "scripts.research_runtime_strategy_replay.TradeAnalytics.strategy_reports",
        return_value=reports,
    ):
        summary = _summary(
            ({"event_count": 100, "trade_rows": trades, "open_state": {}},),
            strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
        )

    assert summary["unique_market_opportunity_count"] == 1
    assert summary["observed_70_percent_gate_passed"] is False
    assert summary["cost_performance_gate_passed"] is True
    assert summary["ranking_eligible"] is False
    assert "UNIQUE_MARKET_OPPORTUNITIES_BELOW_30" in summary["ranking_blockers"]


def test_runtime_strategy_replay_binds_the_selected_frozen_dataset_manifest(
    tmp_path: Path,
) -> None:
    run_id = "RUN-FROZEN-REFERENCE"
    run_dir = tmp_path / f"run={run_id}"
    run_dir.mkdir()
    _write_events(
        run_dir,
        [_event(run_id, event_id="depth-1", ts_ms=1_000, event_type="DEPTH_UPDATE")],
    )
    manifest_path = tmp_path / "dataset.json"
    _write_dataset_manifest(manifest_path, run_id)

    result = build_result(
        tmp_path,
        strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
        run_ids=(run_id,),
        dataset_manifest=manifest_path,
    )

    frozen = result["frozen_dataset"]
    assert frozen["manifest_status"] == "FROZEN_HISTORICAL_FORWARD_PENDING"
    assert frozen["selected_run_count"] == 1
    assert frozen["selected_event_count"] == 1
    assert frozen["current_archive_byte_reverification"] == "NOT_RUN"


def test_frozen_dataset_reference_rejects_tamper_and_unknown_run(tmp_path: Path) -> None:
    manifest_path = tmp_path / "dataset.json"
    _write_dataset_manifest(manifest_path, "RUN-KNOWN")
    payload = json.loads(manifest_path.read_text())
    payload["runs"][0]["event_count"] = 2
    manifest_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="내부 checksum"):
        frozen_dataset_reference(manifest_path, ("RUN-KNOWN",))

    _write_dataset_manifest(manifest_path, "RUN-KNOWN")
    with pytest.raises(ValueError, match="선택 Run"):
        frozen_dataset_reference(manifest_path, ("RUN-UNKNOWN",))


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_tp1_feasibility_gate_accepts_only_fixed_range_and_four_vote_confluence(
    side: Side,
) -> None:
    signal = _qualified_signal(side)
    snapshot = _feature(side)

    accepted = tp1_feasibility_gate_rejections(
        signal,
        snapshot,
        recent_range_bps=60,
        take_profit_1_r=Decimal("1.5"),
    )
    too_small = tp1_feasibility_gate_rejections(
        signal,
        snapshot,
        recent_range_bps=40,
        take_profit_1_r=Decimal("1.5"),
    )
    weak_confluence = tp1_feasibility_gate_rejections(
        signal,
        replace(
            snapshot,
            microprice_minus_mid_bps=0,
            multi_level_microprice_10_minus_mid_bps=0,
            imbalance_top5=0,
            imbalance_top10=0,
            trade_imbalance_1s=0,
            trade_imbalance_3s=0,
        ),
        recent_range_bps=60,
        take_profit_1_r=Decimal("1.5"),
    )

    assert accepted == ()
    assert too_small == ("TP1_GATE_RECENT_RANGE_TOO_SMALL",)
    assert weak_confluence == ("TP1_GATE_DIRECTIONAL_CONFLUENCE_BELOW_4_OF_6",)


def test_tp1_feasibility_gate_never_changes_an_already_rejected_signal() -> None:
    rejected = replace(
        _qualified_signal(),
        decision=replace(
            _qualified_signal().decision,
            status=CandidateStatus.REJECTED,
            reason_codes=(),
            rejection_codes=("BASELINE_REJECTED",),
        ),
    )

    assert (
        tp1_feasibility_gate_rejections(
            rejected,
            _feature(),
            recent_range_bps=None,
            take_profit_1_r=Decimal("1.5"),
        )
        == ()
    )


def test_gate_history_preserves_receive_order_when_venue_timestamp_moves_backward() -> None:
    evaluator = ResearchSignalGateEvaluator()
    evaluator._append_mid(replace(_feature(), ts_ms=121_000, mid=100))
    evaluator._append_mid(replace(_feature(), ts_ms=120_500, mid=99))

    range_bps = evaluator._recent_range_bps(
        replace(_feature(), ts_ms=121_500, mid=101)
    )

    assert range_bps is not None
    assert range_bps > 190
