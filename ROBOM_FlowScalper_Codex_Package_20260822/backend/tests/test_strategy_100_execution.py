# 100후보가 사전등록 risk·exit와 공개 instrument 필터로 기존 PAPER 계획을 만드는지 검증한다.

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from backend.app.domain.market import Instrument
from backend.app.domain.models import Side, Venue
from backend.app.execution import BookSnapshot
from backend.app.features import FeatureSnapshot
from backend.app.market_data import Candle
from backend.app.regime import Regime
from backend.app.research import (
    ALPHA_EVALUATION_INTERVAL_SECONDS,
    AlphaFeatureBuilder,
    AlphaSignal,
    InstrumentMetadataEvidence,
    ResearchCandidatePlanBuilder,
    ResearchInstrumentMetadata,
    preregistered_trials,
)
from backend.app.risk import RiskState


def _alpha_snapshot(family_id: str):
    interval = ALPHA_EVALUATION_INTERVAL_SECONDS[family_id]
    builder = AlphaFeatureBuilder()
    for index in range(80):
        close = Decimal("100") + Decimal(index) / Decimal(10)
        volume = Decimal("100")
        builder.ingest_completed(
            Candle(
                symbol="BTCUSDT",
                interval_seconds=interval,
                open_ts_ms=index * interval * 1_000,
                open=close - Decimal("0.05"),
                high=close + Decimal("3"),
                low=close - Decimal("3"),
                close=close,
                volume=volume,
                trade_count=100,
                quote_volume=close * volume,
                taker_buy_volume=Decimal("60"),
                taker_sell_volume=Decimal("40"),
            )
        )
    decision_ts_ms = 80 * interval * 1_000
    snapshot = builder.snapshot("BTCUSDT", family_id, decision_ts_ms=decision_ts_ms)
    assert snapshot is not None
    return snapshot


def _market_snapshot(*, ts_ms: int, mid: float) -> FeatureSnapshot:
    return FeatureSnapshot(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        ts_ms=ts_ms,
        sample_count=100,
        warmup_seconds=120,
        data_healthy=True,
        lag_ms=10,
        mid=mid,
        spread_bps=2,
        depth_bid_10=100_000,
        depth_ask_10=100_000,
        imbalance_top1=0.1,
        imbalance_top5=0.1,
        imbalance_top10=0.1,
        microprice=mid,
        microprice_minus_mid_bps=0,
        ofi_250ms=1,
        ofi_1s=1,
        ofi_3s=1,
        ofi_10s=1,
        trade_imbalance_1s=0.1,
        trade_imbalance_3s=0.1,
        trade_imbalance_10s=0.1,
        signed_notional_3s=1_000,
        refill_ratio=0.5,
        cancel_ratio=0.5,
        price_response_efficiency=0.1,
        realized_volatility_30s=0.0001,
        realized_volatility_120s=0.0001,
        compression_ratio=1,
        efficiency_ratio_30s=0.5,
        micro_vwap_10s=mid,
    )


def _inputs(family_id: str, exit_id: str, *, evidence: InstrumentMetadataEvidence):
    alpha = _alpha_snapshot(family_id)
    bid = Decimal(str(alpha.close)) - Decimal("0.1")
    ask = Decimal(str(alpha.close)) + Decimal("0.1")
    book = BookSnapshot(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        ts_ms=alpha.decision_ts_ms,
        bids=((bid, Decimal("1000")),),
        asks=((ask, Decimal("1000")),),
    )
    metadata = ResearchInstrumentMetadata(
        instrument=Instrument(
            venue=Venue.FIXTURE,
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            status="TRADING",
            contract_type="PERPETUAL",
            tick_size=Decimal("0.1"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
        ),
        minimum_notional=Decimal("5"),
        snapshot_ts_ms=alpha.decision_ts_ms,
        source_checksum="a" * 64,
        evidence=evidence,
    )
    trial = next(
        trial
        for trial in preregistered_trials()
        if trial.alpha.family_id == family_id and trial.exit.exit_id == exit_id
    )
    signal = AlphaSignal(
        family_id=family_id,
        symbol="BTCUSDT",
        side=Side.LONG,
        signal_ts_ms=alpha.decision_ts_ms,
        completed_candle_close_ts_ms=alpha.completed_candle_close_ts_ms,
        reason_codes=("TEST_SIGNAL",),
    )
    return trial, signal, alpha, book, metadata


def _build(
    family_id: str, exit_id: str, *, evidence=InstrumentMetadataEvidence.POINT_IN_TIME_PUBLIC
):
    trial, signal, alpha, book, metadata = _inputs(
        family_id,
        exit_id,
        evidence=evidence,
    )
    result = ResearchCandidatePlanBuilder().build(
        trial=trial,
        signal=signal,
        alpha_snapshot=alpha,
        market_snapshot=_market_snapshot(ts_ms=alpha.decision_ts_ms, mid=alpha.close),
        book=book,
        metadata=metadata,
        regime=Regime.TREND_UP,
        run_id="RUN-RESEARCH",
        signal_event_id="signal-1",
        risk_state=RiskState(),
    )
    return result


def test_fixed_exit_and_partial_runner_are_bound_before_entry() -> None:
    fixed = _build("F04", "E01")
    runner = _build("F04", "E02")

    assert fixed.plan is not None
    assert fixed.rejection_codes == ()
    assert len(fixed.plan.take_profit_targets) == 1
    assert fixed.plan.take_profit_targets[0].quantity_fraction == Decimal("1")
    assert fixed.plan.trailing_policy is None
    assert runner.plan is not None
    assert [target.quantity_fraction for target in runner.plan.take_profit_targets] == [
        Decimal("0.4"),
        Decimal("0.6"),
    ]
    assert runner.plan.trailing_policy is not None
    assert runner.plan.trailing_policy.partial_tp_required is True
    assert runner.plan.trailing_atr is not None


def test_research_candidate_id_is_deterministic_and_exit_specific() -> None:
    first = _build("F04", "E01")
    repeated = _build("F04", "E01")
    different_exit = _build("F04", "E02")

    assert first.plan is not None
    assert repeated.plan is not None
    assert different_exit.plan is not None
    assert first.plan.candidate_id == repeated.plan.candidate_id
    assert first.plan.candidate_id.startswith("research-")
    assert first.plan.candidate_id != different_exit.plan.candidate_id


def test_f05_uses_two_atr_initial_stop_while_shared_family_uses_one() -> None:
    shared = _build("F04", "E01")
    turtle = _build("F05", "E01")

    assert shared.plan is not None
    assert turtle.plan is not None
    shared_distance = shared.plan.planned_entry - shared.plan.initial_stop
    turtle_distance = turtle.plan.planned_entry - turtle.plan.initial_stop
    assert turtle_distance > shared_distance


def test_current_only_instrument_metadata_keeps_diagnostic_plan_but_blocks_promotion() -> None:
    result = _build(
        "F04",
        "E04",
        evidence=InstrumentMetadataEvidence.CURRENT_PUBLIC_CONSERVATIVE,
    )

    assert result.plan is not None
    assert result.rejection_codes == ()
    assert result.evidence_codes == ("INSTRUMENT_METADATA_CURRENT_NOT_POINT_IN_TIME",)
    assert result.instrument_metadata_promotion_eligible is False


def test_minimum_notional_filter_fails_closed() -> None:
    trial, signal, alpha, book, metadata = _inputs(
        "F04",
        "E01",
        evidence=InstrumentMetadataEvidence.POINT_IN_TIME_PUBLIC,
    )
    metadata = replace(metadata, minimum_notional=Decimal("1000000"))

    result = ResearchCandidatePlanBuilder().build(
        trial=trial,
        signal=signal,
        alpha_snapshot=alpha,
        market_snapshot=_market_snapshot(ts_ms=alpha.decision_ts_ms, mid=alpha.close),
        book=book,
        metadata=metadata,
        regime=Regime.TREND_UP,
        run_id="RUN-RESEARCH",
        signal_event_id="signal-1",
        risk_state=RiskState(),
    )

    assert result.plan is None
    assert result.rejection_codes == ("MINIMUM_NOTIONAL_NOT_MET",)
