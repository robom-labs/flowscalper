"""전략 Registry 설정과 전략별 BASE·STRESS shadow 계좌 격리를 검증한다."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

import backend.app.strategies.runtime_evaluator as runtime_evaluator_module
from backend.app.build_identity import STRATEGY_IDS, STRATEGY_VERSION
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.costing import CostProfile
from backend.app.domain.models import DataQuality, MarketEvent, RuntimeMode, Side, Venue
from backend.app.regime import Regime
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import SQLiteLedger
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.governor import GovernanceEvidence, StrategyGovernor
from backend.app.strategies.registry import (
    StrategyChangeSource,
    StrategyLifecycle,
    StrategyManualLockConflict,
    StrategyMode,
    StrategyRegistry,
    StrategyRevisionConflict,
)
from backend.app.strategies.runtime_evaluator import (
    StrategySignalEvaluator,
    _pullback_metrics,
)
from backend.app.strategies.shadow import ShadowLedger, ShadowPosition
from backend.tests.test_strategies import features


def test_registry_exposes_ten_strategies_and_honors_mode_and_direction() -> None:
    registry = StrategyRegistry()
    assert registry.strategy_ids == (
        "LSA_REVERSAL_V1",
        "CBR_CONTINUATION_V1",
        "VWAP_EXHAUSTION_REVERSION_V1",
        "OFI_CONTINUATION_PULLBACK_V1",
        "QUEUE_MICROPRICE_MOMENTUM_V1",
        "AGGRESSOR_FLOW_CONTINUATION_V1",
        "MULTILEVEL_MICROPRICE_MOMENTUM_V1",
        "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        "OFI_RETURN_CONFLUENCE_V1",
        "BOOK_SLOPE_ASYMMETRY_V1",
    )
    assert STRATEGY_IDS == registry.strategy_ids
    assert STRATEGY_VERSION.startswith("+".join(registry.strategy_ids) + "@")
    assert [row["mode"] for row in registry.rows()] == [
        "OFF",
        "ACTIVE",
        "SHADOW",
        "OFF",
        "OFF",
        "SHADOW",
        "SHADOW",
        "OFF",
        "SHADOW",
        "SHADOW",
    ]
    assert [row["lifecycle"] for row in registry.rows()] == [
        "RETIRED",
        "ACTIVE",
        "SHADOW",
        "RETIRED",
        "RETIRED",
        "SHADOW",
        "SHADOW",
        "RETIRED",
        "SHADOW",
        "SHADOW",
    ]
    assert all(row["long_enabled"] and row["short_enabled"] for row in registry.rows())
    registry.configure(
        "VWAP_EXHAUSTION_REVERSION_V1",
        mode=StrategyMode.OFF,
        long_enabled=True,
        short_enabled=True,
    )
    registry.configure(
        "LSA_REVERSAL_V1",
        mode=StrategyMode.SHADOW,
        long_enabled=True,
        short_enabled=False,
    )

    evaluator = StrategySignalEvaluator()
    decisions = evaluator.evaluate(registry, features(), Regime.WARMUP)

    assert len(decisions) == 11
    assert all(item.decision.status is CandidateStatus.REJECTED for item in decisions)
    lsa = next(item for item in decisions if item.decision.strategy_id == "LSA_REVERSAL_V1")
    assert lsa.decision.side is Side.LONG
    assert not lsa.main_eligible
    assert lsa.shadow_eligible
    assert not any(
        item.decision.strategy_id == "VWAP_EXHAUSTION_REVERSION_V1" for item in decisions
    )
    assert not any(
        item.decision.strategy_id
        in {
            "OFI_CONTINUATION_PULLBACK_V1",
            "QUEUE_MICROPRICE_MOMENTUM_V1",
            "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        }
        for item in decisions
    )


def test_strategy_settings_cas_and_manual_lock_block_automatic_override() -> None:
    registry = StrategyRegistry()
    strategy_id = "CBR_CONTINUATION_V1"
    changed = registry.configure(
        strategy_id,
        mode=StrategyMode.SHADOW,
        long_enabled=True,
        short_enabled=False,
        expected_revision=0,
        source=StrategyChangeSource.USER_UI,
        reason="USER_RESEARCH_ONLY",
        updated_ts_ms=1_000,
    )

    assert changed.revision == 1
    assert changed.manual_lock is True
    with pytest.raises(StrategyRevisionConflict):
        registry.configure(
            strategy_id,
            mode=StrategyMode.OFF,
            long_enabled=False,
            short_enabled=False,
            expected_revision=0,
        )
    with pytest.raises(StrategyManualLockConflict):
        registry.configure(
            strategy_id,
            mode=StrategyMode.ACTIVE,
            long_enabled=True,
            short_enabled=True,
            expected_revision=1,
            source=StrategyChangeSource.AUTO_GOVERNOR,
            reason="AUTO_PROMOTION",
        )
    assert registry.rows()[1]["mode"] == "SHADOW"


def test_strategy_rollback_creates_new_revision_without_deleting_audit_history() -> None:
    registry = StrategyRegistry()
    strategy_id = "CBR_CONTINUATION_V1"
    registry.configure(
        strategy_id,
        mode=StrategyMode.SHADOW,
        lifecycle=StrategyLifecycle.SHADOW,
        long_enabled=True,
        short_enabled=False,
        expected_revision=0,
        source=StrategyChangeSource.USER_UI,
        reason="USER_TEST_CHANGE",
        updated_ts_ms=1_000,
    )

    restored = registry.rollback(
        strategy_id,
        target_revision=0,
        expected_revision=1,
        source=StrategyChangeSource.USER_UI,
        reason="USER_ROLLBACK_TO_REV_0",
        updated_ts_ms=2_000,
    )

    assert restored.revision == 2
    assert restored.mode is StrategyMode.ACTIVE
    assert restored.lifecycle is StrategyLifecycle.ACTIVE
    assert restored.short_enabled is True
    assert restored.manual_lock is True
    assert restored.change_reason == "USER_ROLLBACK_TO_REV_0"


def test_governor_requires_multiple_testing_then_swaps_champion_atomically() -> None:
    registry = StrategyRegistry()
    governor = StrategyGovernor()
    strategy_id = "VWAP_EXHAUSTION_REVERSION_V1"
    insufficient = GovernanceEvidence(
        base_sample_size=35,
        stress_sample_size=35,
        base_expectancy_usdt=Decimal("0.10"),
        stress_expectancy_usdt=Decimal("0.03"),
        base_profit_factor=Decimal("1.20"),
        stress_profit_factor=Decimal("1.05"),
        sample_span_days=3,
        regime_count=1,
        dsr_probability=None,
        pbo=None,
    )
    waiting = governor.assess(registry, strategy_id, insufficient)
    assert waiting.recommended_lifecycle is StrategyLifecycle.SHADOW
    assert "DSR_LT_0_80_OR_MISSING" in waiting.reason_codes
    assert waiting.automatic_action_allowed is False

    shadow_pass = replace(
        insufficient,
        sample_span_days=8,
        regime_count=2,
        dsr_probability=0.90,
        pbo=0.30,
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=2,
        live_public_sample_size=35,
        cooldown_elapsed=True,
    )
    challenger = governor.assess(registry, strategy_id, shadow_pass)
    assert challenger.recommended_lifecycle is StrategyLifecycle.CHALLENGER
    governor.apply(registry, challenger, expected_revision=0, updated_ts_ms=2_000)
    assert registry.setting(strategy_id).lifecycle is StrategyLifecycle.CHALLENGER

    active_pass = GovernanceEvidence(
        base_sample_size=120,
        stress_sample_size=120,
        base_expectancy_usdt=Decimal("0.20"),
        stress_expectancy_usdt=Decimal("0.05"),
        base_profit_factor=Decimal("1.30"),
        stress_profit_factor=Decimal("1.10"),
        sample_span_days=30,
        regime_count=3,
        dsr_probability=0.98,
        pbo=0.20,
        champion_expectancy_usdt=Decimal("0.10"),
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=3,
        live_public_sample_size=120,
        cooldown_elapsed=True,
        strategy_correlation_abs=0.40,
    )
    promotion = governor.assess(registry, strategy_id, active_pass)
    assert promotion.champion_id == "CBR_CONTINUATION_V1"
    changed = governor.apply(registry, promotion, expected_revision=1, updated_ts_ms=3_000)

    assert len(changed) == 2
    assert registry.setting(strategy_id).lifecycle is StrategyLifecycle.ACTIVE
    assert registry.setting("CBR_CONTINUATION_V1").lifecycle is StrategyLifecycle.CHALLENGER
    assert registry.setting("CBR_CONTINUATION_V1").mode is StrategyMode.SHADOW


def test_governor_quarantines_fault_but_never_overrides_user_lock() -> None:
    governor = StrategyGovernor()
    registry = StrategyRegistry()
    evidence = GovernanceEvidence(
        base_sample_size=0,
        stress_sample_size=0,
        base_expectancy_usdt=None,
        stress_expectancy_usdt=None,
        base_profit_factor=None,
        stress_profit_factor=None,
        sample_span_days=0,
        regime_count=0,
        dsr_probability=None,
        pbo=None,
        operational_fault=True,
    )
    assessment = governor.assess(registry, "CBR_CONTINUATION_V1", evidence)
    assert assessment.recommended_lifecycle is StrategyLifecycle.QUARANTINED
    governor.apply(registry, assessment, expected_revision=0, updated_ts_ms=1_000)
    assert registry.setting("CBR_CONTINUATION_V1").mode is StrategyMode.OFF

    locked = StrategyRegistry()
    locked.configure(
        "CBR_CONTINUATION_V1",
        mode=StrategyMode.ACTIVE,
        lifecycle=StrategyLifecycle.ACTIVE,
        long_enabled=True,
        short_enabled=True,
        expected_revision=0,
        manual_lock=True,
        source=StrategyChangeSource.USER_UI,
    )
    blocked = governor.assess(locked, "CBR_CONTINUATION_V1", evidence)
    assert blocked.reason_codes == ("USER_MANUAL_LOCK",)
    assert blocked.automatic_action_allowed is False


def test_governor_never_quarantines_active_strategy_from_one_bad_evaluation() -> None:
    registry = StrategyRegistry()
    governor = StrategyGovernor()
    one_bad_cycle = GovernanceEvidence(
        base_sample_size=120,
        stress_sample_size=120,
        base_expectancy_usdt=Decimal("-0.10"),
        stress_expectancy_usdt=Decimal("-0.20"),
        base_profit_factor=Decimal("0.80"),
        stress_profit_factor=Decimal("0.70"),
        sample_span_days=30,
        regime_count=3,
        dsr_probability=0.10,
        pbo=0.90,
        recent_expectancy_usdt=Decimal("-0.20"),
        recent_profit_factor=Decimal("0.70"),
        full_oos_degraded_evaluations=1,
        recent_oos_degraded_evaluations=1,
    )

    assessment = governor.assess(registry, "CBR_CONTINUATION_V1", one_bad_cycle)

    assert assessment.recommended_lifecycle is StrategyLifecycle.ACTIVE
    assert assessment.reason_codes == ("ACTIVE_GATES_HEALTHY",)
    assert assessment.automatic_action_allowed is False

    second_bad_cycle = replace(
        one_bad_cycle,
        full_oos_degraded_evaluations=2,
        recent_oos_degraded_evaluations=2,
    )
    assessment = governor.assess(registry, "CBR_CONTINUATION_V1", second_bad_cycle)
    assert assessment.recommended_lifecycle is StrategyLifecycle.QUARANTINED
    assert assessment.reason_codes == ("COST_AFTER_DEGRADATION",)


def test_runtime_persists_auto_governor_evidence_and_audit(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "governor.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(),
        run_id="run-governor-audit",
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    evidence = GovernanceEvidence(
        base_sample_size=35,
        stress_sample_size=35,
        base_expectancy_usdt=Decimal("0.10"),
        stress_expectancy_usdt=Decimal("0.03"),
        base_profit_factor=Decimal("1.20"),
        stress_profit_factor=Decimal("1.05"),
        sample_span_days=8,
        regime_count=2,
        dsr_probability=0.90,
        pbo=0.30,
        oos_expectancy_lower_bound_usdt=Decimal("0.01"),
        parameter_robustness_passed=True,
        risk_contract_passed=True,
        independent_period_count=2,
        live_public_sample_size=35,
        cooldown_elapsed=True,
        evaluation_period="WALK_FORWARD_OOS_2026Q3",
        evaluated_ts_ms=1_000,
    )

    changed = runtime.apply_strategy_governance(
        "VWAP_EXHAUSTION_REVERSION_V1",
        evidence,
        expected_revision=0,
    )

    assert changed[0]["lifecycle"] == "CHALLENGER"
    settings = ledger.list_strategy_settings(runtime.run_id)
    latest = [
        row
        for row in settings
        if row["strategy_id"] == "VWAP_EXHAUSTION_REVERSION_V1"
    ][-1]
    assert latest["changed_by"] == "AUTO_GOVERNOR"
    assert latest["change_evidence"]["evidence"]["evaluation_period"] == (
        "WALK_FORWARD_OOS_2026Q3"
    )
    incidents = ledger.list_incidents(category="AUTO_GOVERNOR_TRANSITION")
    assert len(incidents) == 1
    assert incidents[0]["payload"]["assessment"]["automatic_action_allowed"] is True
    ledger.close()


def test_strategy_history_statistics_are_computed_once_per_snapshot(monkeypatch) -> None:
    robust_calls = 0
    percentile_calls = 0
    original_robust_z = runtime_evaluator_module.robust_z_from_sorted
    original_percentile = runtime_evaluator_module.rolling_percentile_from_sorted

    def counted_robust_z(history, current: float) -> float:
        nonlocal robust_calls
        robust_calls += 1
        return original_robust_z(history, current)

    def counted_percentile(history, current: float) -> float:
        nonlocal percentile_calls
        percentile_calls += 1
        return original_percentile(history, current)

    monkeypatch.setattr(runtime_evaluator_module, "robust_z_from_sorted", counted_robust_z)
    monkeypatch.setattr(
        runtime_evaluator_module,
        "rolling_percentile_from_sorted",
        counted_percentile,
    )

    decisions = StrategySignalEvaluator().evaluate(
        StrategyRegistry(),
        features(),
        Regime.RANGE,
    )

    assert len(decisions) == 12
    assert robust_calls == 4
    assert percentile_calls == 5


def test_strategy_sorted_history_evicts_with_same_exact_window() -> None:
    evaluator = StrategySignalEvaluator(history_limit=3)
    registry = StrategyRegistry()
    snapshots = [
        replace(
            features(),
            ts_ms=index * 500,
            signed_notional_3s=float(index - 2),
            price_response_efficiency=index / 10,
            compression_ratio=index / 20,
            efficiency_ratio_30s=index / 30,
            micro_vwap_10s=99.0 + index / 10,
        )
        for index in range(5)
    ]

    for snapshot in snapshots:
        evaluator.evaluate(registry, snapshot, Regime.RANGE)

    window = list(evaluator._history[snapshots[-1].symbol])
    ordered = evaluator._sorted_history[snapshots[-1].symbol]
    assert window == snapshots[-3:]
    assert ordered.flow == sorted(abs(item.signed_notional_3s) for item in window)
    assert ordered.price_response == sorted(item.price_response_efficiency for item in window)
    assert ordered.compression == sorted(item.compression_ratio for item in window)
    assert ordered.efficiency == sorted(item.efficiency_ratio_30s for item in window)
    assert ordered.signed_notional == sorted(item.signed_notional_3s for item in window)
    assert ordered.depth_adjusted_ofi == sorted(item.depth_adjusted_ofi_3s_bps for item in window)
    assert ordered.bid_book_slope == sorted(item.bid_book_slope_10 for item in window)
    assert ordered.ask_book_slope == sorted(item.ask_book_slope_10 for item in window)


@pytest.mark.parametrize(
    ("side", "prices"),
    [
        (Side.LONG, (100.0, 102.0, 101.0, 101.2)),
        (Side.SHORT, (100.0, 98.0, 99.0, 98.8)),
    ],
)
def test_pullback_metrics_use_prefix_event_time_and_require_price_reacceleration(
    side: Side,
    prices: tuple[float, ...],
) -> None:
    snapshots = [
        replace(features(), ts_ms=timestamp, mid=price)
        for timestamp, price in zip((0, 1_000, 2_000, 2_500), prices, strict=True)
    ]
    metrics = _pullback_metrics(
        snapshots[:-1],
        snapshots[-1],
        side,
        maximum_duration_seconds=10,
    )
    assert metrics.duration_seconds == 1.5
    assert metrics.maximum_retrace_fraction == pytest.approx(0.5)
    assert metrics.price_reaccelerated

    no_reacceleration = _pullback_metrics(
        snapshots[:-2],
        snapshots[-2],
        side,
        maximum_duration_seconds=10,
    )
    assert not no_reacceleration.price_reaccelerated

    future = replace(features(), ts_ms=9_000, mid=1_000 if side is Side.LONG else 1.0)
    with_future_in_history = _pullback_metrics(
        [*snapshots[:-1], future],
        snapshots[-1],
        side,
        maximum_duration_seconds=10,
    )
    assert with_future_in_history == metrics


def test_runtime_temporal_gate_uses_event_time_and_resets() -> None:
    evaluator = StrategySignalEvaluator()
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_000, aligned=True) == 0
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_299, aligned=True) == 299
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_300, aligned=True) == 300
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 1_400, aligned=False) == 0
    assert evaluator._confirmation_ms("A", "BTCUSDT", Side.LONG, 2_000, aligned=True) == 0


def test_shadow_accounts_are_independent_by_strategy_and_cost_profile() -> None:
    registry = StrategyRegistry()
    ledger = ShadowLedger(registry.strategy_ids)
    position = ShadowPosition(
        shadow_trade_id="shadow-lsa-base-1",
        symbol="BTCUSDT",
        side=Side.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        entry_fee_usdt=Decimal("0.05"),
        entry_slippage_usdt=Decimal("0.02"),
        opened_ts_ms=1_000,
    )
    ledger.open("LSA_REVERSAL_V1", CostProfile.BASE, position)
    trade = ledger.close(
        "LSA_REVERSAL_V1",
        CostProfile.BASE,
        exit_price=Decimal("101"),
        exit_fee_usdt=Decimal("0.05"),
        exit_slippage_usdt=Decimal("0.03"),
        closed_ts_ms=2_000,
        exit_reason="TAKE_PROFIT_1",
    )

    assert trade.gross_pnl_usdt == Decimal("1")
    assert trade.net_pnl_usdt == Decimal("0.85")
    assert ledger.account("LSA_REVERSAL_V1", CostProfile.BASE).current_equity_usdt == Decimal(
        "1000.85"
    )
    assert ledger.account("LSA_REVERSAL_V1", CostProfile.STRESS).current_equity_usdt == Decimal(
        "1000"
    )
    assert ledger.account("CBR_CONTINUATION_V1", CostProfile.BASE).current_equity_usdt == Decimal(
        "1000"
    )


def test_live_depth_skips_retired_strategies_without_fake_probability() -> None:
    clock = DeterministicClock()
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-registry-live",
        clock=clock,
    )
    runtime.ingest_live_event(
        MarketEvent(
            event_id="depth-1",
            run_id=runtime.run_id,
            venue=runtime.venue,
            symbol="BTCUSDT",
            event_type="DEPTH_UPDATE",
            venue_ts_ms=clock.utc_ms(),
            receive_monotonic_ns=clock.monotonic_ns(),
            sequence_start=1,
            sequence_end=1,
            quality=DataQuality(
                is_live=True,
                is_stale=False,
                sequence_valid=True,
                lag_ms=0,
            ),
            data={
                "bid": "99.9",
                "bid_qty": "5",
                "ask": "100.1",
                "ask_qty": "5",
                "bids": [["99.9", "5"], ["99.8", "8"]],
                "asks": [["100.1", "5"], ["100.2", "8"]],
            },
        )
    )

    decisions = runtime.strategy_decisions()
    assert runtime.strategy_evaluation_count == 12
    assert {decision.strategy_id for decision in decisions} == set(
        runtime.strategy_registry.strategy_ids
    ) - {
        "LSA_REVERSAL_V1",
        "OFI_CONTINUATION_PULLBACK_V1",
        "QUEUE_MICROPRICE_MOMENTUM_V1",
        "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
    }
    assert all(decision.tp_probability is None for decision in decisions)
    assert len(runtime.dashboard()["shadow_accounts"]) == 20
    assert len(runtime.dashboard()["league_accounts"]) == 20
