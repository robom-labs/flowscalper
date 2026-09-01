"""LIVE 전략 프로세스 실패와 Run 경계가 PAPER 상태를 오염시키지 않는지 검증한다."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Side, Venue
from backend.app.features import FeatureSnapshot
from backend.app.regime import Regime
from backend.app.runtime import PaperRuntime
from backend.app.strategies.process_evaluator import (
    ProcessStrategyEvaluator,
    StrategyConditionRows,
    StrategyEvaluationRequest,
    StrategyEvaluationResult,
)


class _ExpectedProcessFailure(RuntimeError):
    """프로세스 원예외 재발생 순서를 식별하는 테스트 오류다."""


class _RaisingProcessEvaluator:
    def __init__(self, error: Exception, lifecycle: list[str]) -> None:
        self.error = error
        self.lifecycle = lifecycle

    @staticmethod
    def request(**kwargs) -> StrategyEvaluationRequest:
        return StrategyEvaluationRequest.from_registry(**kwargs)

    async def evaluate_to_completion(
        self,
        _request: StrategyEvaluationRequest,
    ) -> tuple[StrategyEvaluationResult, None]:
        self.lifecycle.append("process-error")
        raise self.error


class _RecordingAdoptionEvaluator:
    def __init__(self) -> None:
        self.accepted: list[
            tuple[StrategyEvaluationRequest, StrategyEvaluationResult]
        ] = []

    def accept_result(
        self,
        request: StrategyEvaluationRequest,
        result: StrategyEvaluationResult,
    ) -> bool:
        self.accepted.append((request, result))
        return True


def _runtime(run_id: str = "run-process-safety") -> PaperRuntime:
    return PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id=run_id,
        venue=Venue.BINANCE_USDM,
        clock=DeterministicClock(),
    )


def _depth_event(runtime: PaperRuntime, *, ts_ms: int = 1_000) -> MarketEvent:
    return MarketEvent(
        event_id=f"depth-process-safety-{ts_ms}",
        run_id=runtime.run_id,
        venue=runtime.venue,
        symbol="BTCUSDT",
        event_type="DEPTH_UPDATE",
        venue_ts_ms=ts_ms,
        receive_monotonic_ns=ts_ms,
        quality=DataQuality(
            is_live=True,
            is_stale=False,
            sequence_valid=True,
            lag_ms=0,
        ),
        data={"bid": "99", "bid_qty": "1", "ask": "101", "ask_qty": "1"},
    )


def _feature_snapshot(*, ts_ms: int = 1_000) -> FeatureSnapshot:
    return FeatureSnapshot(
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        ts_ms=ts_ms,
        sample_count=1,
        warmup_seconds=0.0,
        data_healthy=True,
        lag_ms=0.0,
        mid=100.0,
        spread_bps=1.0,
        depth_bid_10=1_000.0,
        depth_ask_10=1_000.0,
        imbalance_top1=0.0,
        imbalance_top5=0.0,
        imbalance_top10=0.0,
        microprice=100.0,
        microprice_minus_mid_bps=0.0,
        ofi_250ms=0.0,
        ofi_1s=0.0,
        ofi_3s=0.0,
        ofi_10s=0.0,
        trade_imbalance_1s=0.0,
        trade_imbalance_3s=0.0,
        trade_imbalance_10s=0.0,
        signed_notional_3s=0.0,
        refill_ratio=0.0,
        cancel_ratio=0.0,
        price_response_efficiency=0.0,
        realized_volatility_30s=0.0,
        realized_volatility_120s=0.0,
        compression_ratio=1.0,
        efficiency_ratio_30s=0.0,
        micro_vwap_10s=100.0,
    )


async def test_process_failure_persists_dirty_execution_before_reraising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime("run-process-error-persistence")
    lifecycle: list[str] = []
    expected_error = _ExpectedProcessFailure("strategy child failed")
    runtime._live_strategy_evaluator = _RaisingProcessEvaluator(  # type: ignore[assignment]
        expected_error,
        lifecycle,
    )

    monkeypatch.setattr(
        PaperRuntime,
        "_has_unpersisted_execution_state",
        lambda _self: True,
    )

    def persist(_self: PaperRuntime, ts_ms: int) -> bool:
        lifecycle.append(f"persist:{ts_ms}")
        return True

    monkeypatch.setattr(PaperRuntime, "_persist_execution_state_safely", persist)

    with pytest.raises(_ExpectedProcessFailure) as captured:
        await runtime.ingest_live_event_async(_depth_event(runtime))

    assert captured.value is expected_error
    assert lifecycle == ["process-error", "persist:1000"]


def test_stale_process_state_key_rejects_signals_conditions_and_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime("run-current-process-state")
    event = _depth_event(runtime)
    prepared = runtime._prepare_strategy_evaluation(event, persist_execution=False)
    assert prepared is not None
    signals = runtime._evaluate_prepared_strategy(prepared)
    assert signals
    signal = signals[0]
    stale_request = StrategyEvaluationRequest.from_registry(
        state_key="run-previous:BINANCE_USDM:previous-strategy-version",
        registry=prepared.strategy_registry,
        snapshot=prepared.snapshot,
        regime=prepared.regime,
        tick_size=prepared.tick_size,
        fifteen_minute_candles=prepared.fifteen_minute_candles,
        thirty_minute_candles=prepared.thirty_minute_candles,
        hourly_candles=prepared.hourly_candles,
    )
    stale_result = StrategyEvaluationResult(
        signals=(signal,),
        condition_rows=(
            StrategyConditionRows(
                symbol=signal.symbol,
                strategy_id=signal.decision.strategy_id,
                side=signal.decision.side,
                rows=(
                    {
                        "condition_id": "OLD_RUN_CONDITION",
                        "label_ko": "이전 Run 조건",
                        "status": "PASSED",
                    },
                ),
            ),
        ),
    )
    adoption = _RecordingAdoptionEvaluator()
    runtime._live_strategy_evaluator = adoption  # type: ignore[assignment]
    candidate_attempts: list[str] = []

    def build_candidates(_self: PaperRuntime, *_args: object) -> tuple[object, ...]:
        candidate_attempts.append("built")
        return ()

    monkeypatch.setattr(PaperRuntime, "_build_candidate_plans", build_candidates)

    runtime._complete_strategy_evaluation(
        prepared,
        stale_result.signals,
        persist_execution=False,
        process_request=stale_request,
        process_result=stale_result,
    )

    assert adoption.accepted == []
    assert runtime.strategy_signals == {}
    assert runtime.strategy_evaluation_count == 0
    assert runtime.qualified_signal_count == 0
    assert candidate_attempts == []
    assert any(
        "이전 Run 전략 평가 결과 적용 생략" in str(row.get("message"))
        for row in runtime.control_logs
    )


def test_live_condition_detail_hides_cache_from_another_process_state() -> None:
    runtime = _runtime("run-old-condition-cache")
    evaluator = ProcessStrategyEvaluator()
    runtime._live_strategy_evaluator = evaluator
    strategy_id = "VWAP_EXHAUSTION_REVERSION_V1"
    old_state_key = runtime._strategy_process_state_key()
    old_request = evaluator.request(
        state_key=old_state_key,
        registry=runtime.strategy_registry,
        snapshot=_feature_snapshot(),
        regime=Regime.WARMUP,
        tick_size=Decimal("0.01"),
    )
    old_result = StrategyEvaluationResult(
        signals=(),
        condition_rows=(
            StrategyConditionRows(
                symbol="BTCUSDT",
                strategy_id=strategy_id,
                side=Side.LONG,
                rows=(
                    {
                        "condition_id": "OLD_RUN_SENTINEL",
                        "label_ko": "이전 Run에서만 측정된 조건",
                        "status": "PASSED",
                    },
                ),
            ),
        ),
    )
    evaluator._latest_requested_state_key = old_state_key
    assert evaluator.accept_result(old_request, old_result) is True
    assert evaluator.condition_rows(
        "BTCUSDT",
        strategy_id,
        Side.LONG,
        state_key=old_state_key,
    )

    runtime.run_id = "run-new-condition-cache"
    detail = runtime.strategy_condition_detail(strategy_id, symbol="BTCUSDT")

    assert detail["setup_state"] == "WAITING_DATA"
    assert detail["passed"] == 0
    assert detail["sides"] == []
    assert all(
        row.get("condition_id") != "OLD_RUN_SENTINEL"
        for row in detail["conditions"]
    )
