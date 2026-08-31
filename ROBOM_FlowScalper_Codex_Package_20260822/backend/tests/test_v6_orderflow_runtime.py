# 주문흐름 확인 필터의 지속성·CAS·복구와 후보계획 비생성 계약을 검증한다.
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from backend.app.candidates import PlanBuildResult
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import RuntimeMode, Side, Venue
from backend.app.features import FeatureSnapshot
from backend.app.main import create_app
from backend.app.regime import Regime
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import SQLiteLedger
from backend.app.strategies.base import CandidateDecision
from backend.app.strategies.orderflow_confirmation import (
    ORDERFLOW_CONFIRMATION_FILTER_ID,
    OrderflowConfirmationInputs,
    OrderflowConfirmationRuntime,
    OrderflowFilterRevisionConflict,
    evaluate_orderflow_confirmation,
)
from backend.app.strategies.runtime_evaluator import EvaluatedSignal
from backend.tests.test_candidate_paper_portfolio import (
    book,
    candidate_plan,
    qualified_decision,
)
from backend.tests.test_strategies import features
from backend.tests.test_v02_storage_market_replay import market_event


def _aligned_snapshot(ts_ms: int, *, healthy: bool = True) -> FeatureSnapshot:
    return replace(
        features(healthy=healthy, spread_bps=0),
        ts_ms=ts_ms,
        imbalance_top10=1.0,
        microprice_minus_mid_bps=2.0,
        trade_imbalance_3s=1.0,
        price_response_efficiency=1.0,
        multi_level_microprice_10_minus_mid_bps=2.0,
        depth_adjusted_ofi_3s_bps=5.0,
        bid_book_slope_10=2.0,
        ask_book_slope_10=0.0,
        bid_refill_ratio_3s=1.0,
        ask_refill_ratio_3s=0.0,
        bid_cancel_ratio_3s=0.0,
        ask_cancel_ratio_3s=1.0,
    )


def _signal(strategy_id: str) -> EvaluatedSignal:
    return EvaluatedSignal(
        symbol="BTCUSDT",
        regime=Regime.TREND_UP,
        decision=qualified_decision(strategy_id),
        main_eligible=True,
        shadow_eligible=True,
    )


def test_orderflow_filter_requires_500ms_and_is_directional_and_idempotent() -> None:
    runtime = OrderflowConfirmationRuntime()

    first = runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    duplicate = runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    almost = runtime.evaluate(_aligned_snapshot(1_499), Side.LONG)
    allowed = runtime.evaluate(_aligned_snapshot(1_500), Side.LONG)
    opposite = runtime.evaluate(_aligned_snapshot(1_500), Side.SHORT)

    assert first is duplicate
    assert first.persistence_ms == 0
    assert first.allowed is False
    assert almost.persistence_ms == 499
    assert almost.allowed is False
    assert allowed.persistence_ms == 500
    assert allowed.allowed is True
    assert opposite.score < allowed.score
    assert opposite.allowed is False
    assert allowed.creates_candidate_plan is False
    assert runtime.status()["trade_count_delta"] == 0
    assert runtime.status()["account_count_delta"] == 0

    with pytest.raises(ValueError, match="뒤로"):
        runtime.evaluate(_aligned_snapshot(1_499), Side.LONG)


def test_same_timestamp_divergent_snapshot_fails_closed() -> None:
    runtime = OrderflowConfirmationRuntime()
    snapshot = _aligned_snapshot(1_000)
    first = runtime.evaluate(snapshot, Side.LONG)

    assert runtime.evaluate(snapshot, Side.LONG) is first
    with pytest.raises(ValueError, match="같은 시각.*다릅니다"):
        runtime.evaluate(replace(snapshot, depth_adjusted_ofi_3s_bps=0.0), Side.LONG)
    with pytest.raises(ValueError, match="같은 시각.*data health"):
        runtime.evaluate(replace(snapshot, data_healthy=False), Side.LONG)


def test_observation_gap_over_500ms_resets_streak_but_exact_boundary_does_not() -> None:
    exact_boundary = OrderflowConfirmationRuntime()
    exact_boundary.evaluate(_aligned_snapshot(1_000), Side.LONG)
    assert exact_boundary.evaluate(_aligned_snapshot(1_500), Side.LONG).allowed is True

    runtime = OrderflowConfirmationRuntime()
    runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    after_gap = runtime.evaluate(_aligned_snapshot(1_501), Side.LONG)
    exact_after_reset = runtime.evaluate(_aligned_snapshot(2_001), Side.LONG)

    assert after_gap.persistence_ms == 0
    assert after_gap.allowed is False
    assert exact_after_reset.persistence_ms == 500
    assert exact_after_reset.allowed is True


def test_neutral_directional_components_and_health_do_not_count_as_independent() -> None:
    inputs = OrderflowConfirmationInputs(
        normalized_ofi=Decimal("1.00"),
        aggressor_imbalance=Decimal("0.50"),
        microprice_displacement=Decimal("0.50"),
        multilevel_fair_price_displacement=Decimal("0.50"),
        queue_imbalance=Decimal("0.50"),
        book_slope=Decimal("0.50"),
        depth_adjusted_price_response=Decimal("0.50"),
        spread_health=Decimal("1.00"),
        book_resilience=Decimal("1.00"),
    )

    decision = evaluate_orderflow_confirmation(
        inputs,
        persistence_ms=500,
        data_healthy=True,
    )

    assert decision.score == Decimal("0.6500")
    assert decision.passed_component_count == 1
    assert decision.allowed is False
    assert "ORDERFLOW_INDEPENDENT_COMPONENTS_LT_3" in decision.reason_codes


def test_unhealthy_snapshot_resets_persistence_streak() -> None:
    runtime = OrderflowConfirmationRuntime()
    runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    assert runtime.evaluate(_aligned_snapshot(1_500), Side.LONG).allowed is True

    unhealthy = runtime.evaluate(_aligned_snapshot(2_000, healthy=False), Side.LONG)
    restarted = runtime.evaluate(_aligned_snapshot(2_500), Side.LONG)

    assert unhealthy.allowed is False
    assert "ORDERFLOW_DATA_UNHEALTHY" in unhealthy.reason_codes
    assert restarted.persistence_ms == 0
    assert restarted.allowed is False


def test_filter_configuration_is_default_off_cas_and_recovery_safe() -> None:
    runtime = OrderflowConfirmationRuntime()
    assert runtime.status()["enabled"] is False

    configured = runtime.configure(
        enabled=True,
        expected_revision=0,
        updated_ts_ms=10,
        reason="PAPER_FILTER_RESEARCH",
    )
    runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    with pytest.raises(OrderflowFilterRevisionConflict) as conflict:
        runtime.configure(
            enabled=False,
            expected_revision=0,
            updated_ts_ms=20,
            reason="STALE_SCREEN",
        )

    recovered = OrderflowConfirmationRuntime()
    recovered.restore_state(runtime.recovery_state())

    assert configured["revision"] == 1
    assert conflict.value.current["revision"] == 1
    assert recovered.status()["enabled"] is True
    assert recovered.status()["latest"] == []
    assert recovered.evaluate(_aligned_snapshot(1_500), Side.LONG).persistence_ms == 0


def test_restore_same_revision_is_exact_noop_and_divergence_fails_closed() -> None:
    runtime = OrderflowConfirmationRuntime()
    runtime.configure(
        enabled=True,
        expected_revision=0,
        updated_ts_ms=10,
        reason="PAPER_FILTER_RESEARCH",
    )
    decision = runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    recovery_state = runtime.recovery_state()

    runtime.restore_state(dict(recovery_state))
    assert runtime.decision_for("BTCUSDT", Side.LONG) is decision

    for divergent in (
        {**recovery_state, "enabled": False},
        {**recovery_state, "updated_ts_ms": 11},
        {**recovery_state, "change_reason": "DIVERGENT_RECOVERY"},
    ):
        with pytest.raises(ValueError, match="동일 revision.*다릅니다"):
            runtime.restore_state(divergent)

    runtime.restore_state(
        {
            "enabled": False,
            "revision": 0,
            "updated_ts_ms": 0,
            "change_reason": "STALE_RECOVERY",
        }
    )
    assert runtime.recovery_state() == recovery_state
    assert runtime.decision_for("BTCUSDT", Side.LONG) is decision


@pytest.mark.parametrize(
    "payload, match",
    (
        (
            {
                "enabled": False,
                "revision": -1,
                "updated_ts_ms": 0,
                "change_reason": "INVALID",
            },
            "revision.*음수",
        ),
        (
            {
                "enabled": False,
                "revision": 0,
                "updated_ts_ms": -1,
                "change_reason": "INVALID",
            },
            "updated_ts_ms.*음수",
        ),
    ),
)
def test_restore_rejects_negative_revision_and_timestamp(
    payload: dict[str, object],
    match: str,
) -> None:
    runtime = OrderflowConfirmationRuntime()

    with pytest.raises(ValueError, match=match):
        runtime.restore_state(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "enabled": True,
            "revision": "1",
            "updated_ts_ms": 10,
            "change_reason": "PAPER_FILTER_RESEARCH",
        },
        {
            "enabled": False,
            "revision": 2,
            "updated_ts_ms": "20",
            "change_reason": "FUTURE_STRING_TIMESTAMP",
        },
        {
            "enabled": False,
            "revision": 0,
            "updated_ts_ms": 0,
            "change_reason": "",
        },
        {
            "enabled": False,
            "revision": 2,
            "updated_ts_ms": 20,
            "change_reason": 123,
        },
    ),
)
def test_restore_rejects_noncanonical_state_before_revision_handling(
    payload: dict[str, object],
) -> None:
    runtime = OrderflowConfirmationRuntime()
    runtime.configure(
        enabled=True,
        expected_revision=0,
        updated_ts_ms=10,
        reason="PAPER_FILTER_RESEARCH",
    )
    before = runtime.recovery_state()

    with pytest.raises(ValueError):
        runtime.restore_state(payload)

    assert runtime.recovery_state() == before


def test_restore_rejects_future_revision_with_regressed_timestamp() -> None:
    runtime = OrderflowConfirmationRuntime()
    runtime.configure(
        enabled=True,
        expected_revision=0,
        updated_ts_ms=10,
        reason="PAPER_FILTER_RESEARCH",
    )
    before = runtime.recovery_state()

    with pytest.raises(ValueError, match="시각.*이전"):
        runtime.restore_state(
            {
                "enabled": False,
                "revision": 2,
                "updated_ts_ms": 9,
                "change_reason": "REGRESSED_TIMESTAMP",
            }
        )

    assert runtime.recovery_state() == before


def test_configure_rejects_negative_revision_and_timestamp() -> None:
    runtime = OrderflowConfirmationRuntime()

    with pytest.raises(ValueError, match="revision.*음수"):
        runtime.configure(
            enabled=True,
            expected_revision=-1,
            updated_ts_ms=0,
            reason="INVALID",
        )
    with pytest.raises(ValueError, match="갱신 시각.*음수"):
        runtime.configure(
            enabled=True,
            expected_revision=0,
            updated_ts_ms=-1,
            reason="INVALID",
        )


def test_runtime_filter_blocks_only_affected_qualified_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-live-1",
        venue=Venue.FIXTURE,
        clock=DeterministicClock(),
    )
    runtime.configure_orderflow_confirmation_filter(
        enabled=True,
        expected_revision=0,
        reason="PAPER_FILTER_RESEARCH",
    )
    runtime.orderflow_confirmation_runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    affected = _signal("TREND_PULLBACK_RECLAIM_15M_V2")
    unaffected = _signal("VWAP_EXHAUSTION_REVERSION_V1")
    unaffected_plan = candidate_plan(strategy_id="VWAP_EXHAUSTION_REVERSION_V1")
    build_calls: list[str] = []

    def build(**kwargs: object) -> PlanBuildResult:
        decision = cast(CandidateDecision, kwargs["decision"])
        strategy_id = decision.strategy_id
        build_calls.append(strategy_id)
        return PlanBuildResult(unaffected_plan, ())

    monkeypatch.setattr(runtime.candidate_planner, "build", build)
    event = market_event("run-live-1", event_id="filter-gate", ts_ms=1_000).model_copy(
        update={"venue": Venue.FIXTURE}
    )

    plans = runtime._build_candidate_plans(
        event,
        _aligned_snapshot(1_000),
        Regime.TREND_UP,
        book(1_000),
        (affected, unaffected),
    )

    assert plans == (unaffected_plan,)
    assert build_calls == ["VWAP_EXHAUSTION_REVERSION_V1"]
    assert runtime.plan_rejections[-1]["filter_id"] == ORDERFLOW_CONFIRMATION_FILTER_ID
    assert runtime.plan_rejections[-1]["creates_candidate_plan"] is False
    assert runtime.paper_portfolio.main.completed_trades == []


def test_runtime_filter_off_bypasses_and_on_allows_after_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-live-1",
        venue=Venue.FIXTURE,
        clock=DeterministicClock(),
    )
    signal = _signal("BREAKOUT_RETEST_30M_V2")
    plan = candidate_plan(strategy_id="BREAKOUT_RETEST_30M_V2")
    monkeypatch.setattr(
        runtime.candidate_planner,
        "build",
        lambda **_kwargs: PlanBuildResult(plan, ()),
    )
    event = market_event("run-live-1", event_id="filter-allow", ts_ms=1_500).model_copy(
        update={"venue": Venue.FIXTURE}
    )

    assert runtime._build_candidate_plans(
        event,
        _aligned_snapshot(1_500),
        Regime.TREND_UP,
        book(1_500),
        (signal,),
    ) == (plan,)

    runtime.configure_orderflow_confirmation_filter(
        enabled=True,
        expected_revision=0,
        reason="PAPER_FILTER_RESEARCH",
    )
    runtime.orderflow_confirmation_runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    runtime.orderflow_confirmation_runtime.evaluate(_aligned_snapshot(1_500), Side.LONG)
    assert runtime._build_candidate_plans(
        event,
        _aligned_snapshot(1_500),
        Regime.TREND_UP,
        book(1_500),
        (signal,),
    ) == (plan,)


def test_runtime_persists_filter_configuration_but_not_streak(tmp_path: Path) -> None:
    database = tmp_path / "orderflow-filter-recovery.sqlite3"
    ledger = SQLiteLedger(database)
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-filter-recovery",
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    runtime.configure_orderflow_confirmation_filter(
        enabled=True,
        expected_revision=0,
        reason="PAPER_FILTER_RESEARCH",
    )
    runtime.orderflow_confirmation_runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    recovered = ledger.recover_latest(recovered_ts_ms=2_000)
    assert recovered is not None

    reopened = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id=runtime.run_id,
        venue=Venue.BINANCE_USDM,
        ledger=ledger,
        clock=DeterministicClock(),
    )
    assert reopened.restore_recovery_state(recovered) is True

    status = reopened.orderflow_confirmation_filter_status()
    assert status["enabled"] is True
    assert status["revision"] == 1
    assert status["latest"] == []
    assert reopened.orderflow_confirmation_runtime.evaluate(
        _aligned_snapshot(1_500),
        Side.LONG,
    ).persistence_ms == 0
    ledger.close()


def test_family_api_exposes_virtual_filter_conditions_and_cas() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    runtime.orderflow_confirmation_runtime.evaluate(_aligned_snapshot(1_000), Side.LONG)
    runtime.orderflow_confirmation_runtime.evaluate(_aligned_snapshot(1_500), Side.LONG)

    with TestClient(create_app(runtime)) as client:
        catalog = client.get("/api/strategy-families")
        detail = client.get("/api/strategy-families/ORDERFLOW_CONFIRMATION")
        conditions = client.get(
            "/api/strategy-families/ORDERFLOW_CONFIRMATION/conditions"
        )

        assert catalog.status_code == 200
        orderflow = next(
            row
            for row in catalog.json()["families"]
            if row["family_id"] == "ORDERFLOW_CONFIRMATION"
        )
        assert orderflow["current_variant_id"] == ORDERFLOW_CONFIRMATION_FILTER_ID
        assert orderflow["variants"][0]["role"] == "FILTER"
        assert detail.json()["variants"][0]["setting"]["research_enabled"] is False
        assert conditions.json()["setup_state"] == "FILTER_OFF"
        assert conditions.json()["passed"] == conditions.json()["total"] == 13
        assert conditions.json()["creates_candidate_plan"] is False
        assert conditions.json()["filter"]["uplift_status"].startswith("NOT_PROVEN")

        enabled = client.patch(
            "/api/strategy-families/ORDERFLOW_CONFIRMATION/research-enabled",
            json={
                "research_enabled": True,
                "expected_revision": 0,
                "reason": "PAPER_FILTER_RESEARCH",
            },
        )
        stale = client.patch(
            "/api/strategy-families/ORDERFLOW_CONFIRMATION/research-enabled",
            json={
                "research_enabled": False,
                "expected_revision": 0,
                "reason": "STALE_FILTER_SCREEN",
            },
        )

    assert enabled.status_code == 200
    assert enabled.json()["variants"][0]["setting"]["research_enabled"] is True
    assert enabled.json()["variants"][0]["setting"]["settings_revision"] == 1
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "ORDERFLOW_FILTER_REVISION_CONFLICT"
    assert runtime.paper_portfolio.main.completed_trades == []


def test_family_condition_api_uses_evaluator_measurements() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    snapshot = _aligned_snapshot(1_000)
    signals = runtime.strategy_evaluator.evaluate(
        runtime.strategy_registry,
        snapshot,
        Regime.TREND_UP,
    )
    for signal in signals:
        runtime.strategy_signals[
            (
                signal.symbol,
                signal.decision.strategy_id,
                signal.decision.side.value,
            )
        ] = signal

    with TestClient(create_app(runtime)) as client:
        response = client.get("/api/strategy-families/TREND_PULLBACK/conditions")

    payload = response.json()
    assert response.status_code == 200
    assert payload["strategy_id"] == "TREND_PULLBACK_RECLAIM_15M_V2"
    assert payload["total"] >= 10
    assert payload["passed"] < payload["total"]
    assert payload["conditions"]
    assert all(row["status"] != "NOT_AVAILABLE" for row in payload["conditions"])
    assert any(row["current_value"] is not None for row in payload["conditions"])
    assert payload["paper_only"] is True
    assert payload["real_orders_enabled"] is False
