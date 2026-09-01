# V8·V9 공통 연구 gate의 불변조건과 결정적 상태전이를 검증한다.

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.domain.models import Side
from backend.app.research.gates import (
    EvidenceEpoch,
    EvidenceFreshnessStatus,
    EvidenceHorizon,
    EvidenceSample,
    FilterDecision,
    FilterResult,
    HypothesisKey,
    HypothesisRegistry,
    HysteresisConfig,
    HysteresisDecision,
    HysteresisMode,
    HysteresisState,
    RiskOverlay,
    RiskOverlayComponent,
    advance_hysteresis,
    assess_evidence_freshness,
    combine_filter_results,
)

_DAY_MS = 24 * 60 * 60 * 1_000


def _filter_result(
    filter_id: str,
    decision: FilterDecision,
    *,
    reason_codes: tuple[str, ...] = (),
    quality_multiplier: Decimal = Decimal(1),
) -> FilterResult:
    return FilterResult(
        filter_id=filter_id,
        decision=decision,
        reason_codes=reason_codes,
        observed_ts_ms=1_000,
        valid_until_ts_ms=2_000,
        quality_multiplier=quality_multiplier,
    )


def _hypothesis_key(
    *,
    parameter_id: str = "PARAM-A",
    filters: tuple[str, ...] = ("PD01", "CF01"),
) -> HypothesisKey:
    return HypothesisKey(
        strategy_family="TREND_PULLBACK",
        candidate_id="TREND_PULLBACK_V3",
        strategy_version="TREND_PULLBACK_V3",
        parameter_id=parameter_id,
        exit_id="STRUCTURE_TP_V1",
        execution_policy="TAKER_IOC",
        filter_combination=filters,
        dataset_id="BINANCE_USDM_2026H1",
        parameter_hash="a" * 64,
        cost_profile="BASE",
        dataset_hash="b" * 64,
        feature_version="FEATURE-V8",
        label_version="LABEL-V9",
        engine_version="ENGINE-V9",
    )


def _epoch(*, closed_ts_ms: int | None = None) -> EvidenceEpoch:
    key = _hypothesis_key()
    registry = HypothesisRegistry().register("HYP-A", key)
    return EvidenceEpoch(
        epoch_id="EPOCH-2026-01",
        opened_ts_ms=1_000,
        closed_ts_ms=closed_ts_ms,
        strategy_version="TREND_PULLBACK_V3",
        feature_version="FEATURE-V8",
        label_version="LABEL-V9",
        engine_version="ENGINE-V9",
        cost_model_version="COST-V3",
        cost_profile="BASE",
        parameter_hash=key.parameter_hash,
        dataset_hash=key.dataset_hash,
        fee_model_version="FEE-V2",
        matching_model_version="MATCHING-V2",
        symbol_contract_version="BINANCE-USDM-V1",
        data_adapter_version="BINANCE-PUBLIC-V3",
        hypothesis_registry_hash=registry.fingerprint(),
        hypothesis_key_fingerprint=key.fingerprint(),
    )


def _samples(
    count: int,
    *,
    as_of_ts_ms: int,
    epoch_id: str = "EPOCH-2026-01",
    strategy_version: str = "TREND_PULLBACK_V3",
    duplicate_profiles: bool = False,
) -> tuple[EvidenceSample, ...]:
    rows: list[EvidenceSample] = []
    for index in range(count):
        opportunity_id = f"OPP-{index:04d}"
        rows.append(
            EvidenceSample(
                opportunity_id=opportunity_id,
                observed_ts_ms=as_of_ts_ms - 1_000 - index,
                evidence_epoch_id=epoch_id,
                strategy_version=strategy_version,
                profile="BASE",
            )
        )
        if duplicate_profiles:
            rows.append(replace(rows[-1], profile="STRESS"))
    return tuple(rows)


def test_filter_assessment_uses_fixed_precedence_and_keeps_quality_separate() -> None:
    results = (
        _filter_result(FilterDecision.PASS.value, FilterDecision.PASS),
        _filter_result(
            "QUALITY",
            FilterDecision.QUALITY_DOWNGRADE,
            reason_codes=("LOW_COVERAGE",),
            quality_multiplier=Decimal("0.70"),
        ),
        _filter_result("WAIT", FilterDecision.WAIT, reason_codes=("CALIBRATING",)),
        _filter_result("SKIP", FilterDecision.SKIP, reason_codes=("COST_GAP",)),
    )

    assessment = combine_filter_results(results)

    assert assessment.decision is FilterDecision.SKIP
    assert assessment.execution_allowed is False
    assert assessment.retryable is False
    assert assessment.quality_multiplier == Decimal("0.70")
    assert assessment.reason_codes == ("LOW_COVERAGE", "CALIBRATING", "COST_GAP")
    assert assessment.results == results


def test_empty_filters_pass_and_wait_is_the_only_retryable_decision() -> None:
    empty = combine_filter_results(())
    waiting = combine_filter_results(
        (_filter_result("WAIT", FilterDecision.WAIT, reason_codes=("DATA_PENDING",)),)
    )

    assert empty.decision is FilterDecision.PASS
    assert empty.execution_allowed is True
    assert empty.quality_multiplier == Decimal(1)
    assert waiting.execution_allowed is False
    assert waiting.retryable is True


def test_filter_result_rejects_ambiguous_multiplier_and_duplicate_filter() -> None:
    with pytest.raises(ValueError, match="QUALITY_DOWNGRADE"):
        _filter_result("QUALITY", FilterDecision.QUALITY_DOWNGRADE)
    with pytest.raises(ValueError, match="품질 배수"):
        _filter_result("PASS", FilterDecision.PASS, quality_multiplier=Decimal("0.9"))
    duplicate = _filter_result("SAME", FilterDecision.PASS)
    with pytest.raises(ValueError, match="동일 필터"):
        combine_filter_results((duplicate, duplicate))


def test_risk_overlay_uses_minimum_multiplier_and_never_exceeds_one() -> None:
    overlay = RiskOverlay(
        (
            RiskOverlayComponent("VOLATILITY", Decimal("0.75"), ("HIGH_VOL",), 1_000),
            RiskOverlayComponent("DRAWDOWN", Decimal("0.50"), ("DRAWDOWN",), 1_000),
            RiskOverlayComponent("TAIL", Decimal(1), (), 1_000),
        )
    )

    assert RiskOverlay().multiplier == Decimal(1)
    assert overlay.multiplier == Decimal("0.50")
    assert overlay.reason_codes == ("HIGH_VOL", "DRAWDOWN")
    assert overlay.multiplier <= Decimal(1)


@pytest.mark.parametrize("multiplier", [Decimal("-0.01"), Decimal("1.01"), Decimal("NaN")])
def test_risk_overlay_rejects_out_of_contract_multiplier(multiplier: Decimal) -> None:
    with pytest.raises(ValueError, match="risk multiplier"):
        RiskOverlayComponent("INVALID", multiplier, ("INVALID",))


def test_risk_overlay_rejects_unexplained_reduction_and_duplicate_component() -> None:
    with pytest.raises(ValueError, match="사유 코드"):
        RiskOverlayComponent("TAIL", Decimal("0.8"))
    component = RiskOverlayComponent("TAIL", Decimal(1))
    with pytest.raises(ValueError, match="동일 위험 overlay"):
        RiskOverlay((component, component))


def test_hypothesis_fingerprint_is_canonical_for_filter_order() -> None:
    first = _hypothesis_key(filters=("PD01", "CF01"))
    second = _hypothesis_key(filters=("CF01", "PD01"))

    assert first == second
    assert first.filter_combination == ("CF01", "PD01")
    assert first.fingerprint() == second.fingerprint()
    assert len(first.fingerprint()) == 64


def test_hypothesis_registry_is_deterministic_idempotent_and_collision_safe() -> None:
    first_key = _hypothesis_key()
    second_key = _hypothesis_key(parameter_id="PARAM-B")
    first = HypothesisRegistry().register("HYP-A", first_key).register("HYP-B", second_key)
    second = HypothesisRegistry().register("HYP-B", second_key).register("HYP-A", first_key)

    assert first == second
    assert first.fingerprint() == second.fingerprint()
    assert first.register("HYP-A", first_key) is first
    assert first.registration("HYP-B").key == second_key
    with pytest.raises(ValueError, match="fingerprint가 바뀌"):
        first.register("HYP-A", _hypothesis_key(parameter_id="PARAM-C"))
    with pytest.raises(ValueError, match="다른 ID"):
        first.register("HYP-C", first_key)
    with pytest.raises(ValueError, match="중복"):
        _hypothesis_key(filters=("PD01", "PD01"))


def test_registry_and_epoch_persistence_round_trip_rejects_tampering() -> None:
    key = _hypothesis_key()
    registry = HypothesisRegistry().register("HYP-A", key)
    epoch = _epoch()

    assert HypothesisRegistry.from_payload(registry.canonical_payload()) == registry
    assert EvidenceEpoch.from_payload(epoch.canonical_payload()) == epoch
    epoch.validate_binding(registry, "HYP-A")

    registry_payload = registry.canonical_payload()
    registry_payload[0]["key_fingerprint"] = "0" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        HypothesisRegistry.from_payload(registry_payload)
    epoch_payload = epoch.canonical_payload()
    epoch_payload["dataset_hash"] = "c" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        EvidenceEpoch.from_payload(epoch_payload)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("parameter_hash", "c" * 64),
        ("cost_profile", "STRESS"),
        ("dataset_hash", "d" * 64),
        ("feature_version", "FEATURE-V9"),
        ("label_version", "LABEL-V10"),
        ("engine_version", "ENGINE-V10"),
    ],
)
def test_evidence_axes_create_distinct_epoch_identity(
    field_name: str,
    replacement: str,
) -> None:
    first = _epoch()
    second = replace(first, **{field_name: replacement})

    assert first.fingerprint() != second.fingerprint()


def test_evidence_epoch_fingerprint_changes_with_market_contract() -> None:
    first = _epoch()
    second = replace(first, matching_model_version="MATCHING-V3")

    assert first.fingerprint() != second.fingerprint()
    with pytest.raises(ValueError, match="SHA-256"):
        replace(first, hypothesis_registry_hash="not-a-hash")
    with pytest.raises(ValueError, match="종료시각"):
        replace(first, closed_ts_ms=999)


@pytest.mark.parametrize(
    ("horizon", "minimum", "window_days"),
    [
        (EvidenceHorizon.MICRO, 200, 60),
        (EvidenceHorizon.FAST, 50, 90),
        (EvidenceHorizon.SWING, 30, 180),
        (EvidenceHorizon.MARKET_NEUTRAL, 20, 180),
    ],
)
def test_freshness_uses_v9_horizon_thresholds_and_unique_opportunities(
    horizon: EvidenceHorizon,
    minimum: int,
    window_days: int,
) -> None:
    as_of_ts_ms = 200 * _DAY_MS
    samples = _samples(
        minimum,
        as_of_ts_ms=as_of_ts_ms,
        duplicate_profiles=True,
    )

    assessment = assess_evidence_freshness(
        samples,
        horizon=horizon,
        as_of_ts_ms=as_of_ts_ms,
        epoch=_epoch(),
    )

    assert assessment.status is EvidenceFreshnessStatus.FRESH
    assert assessment.promotion_allowed is True
    assert assessment.minimum_unique_samples == minimum
    assert assessment.observed_unique_samples == minimum
    assert assessment.window_days == window_days


def test_freshness_alias_and_stale_boundary_fail_closed() -> None:
    as_of_ts_ms = 200 * _DAY_MS
    assessment = assess_evidence_freshness(
        _samples(199, as_of_ts_ms=as_of_ts_ms),
        horizon="MICRO_SCALP",
        as_of_ts_ms=as_of_ts_ms,
        epoch=_epoch(),
    )

    assert assessment.horizon is EvidenceHorizon.MICRO
    assert assessment.status is EvidenceFreshnessStatus.STALE_EVIDENCE
    assert assessment.promotion_allowed is False
    assert assessment.reason_codes == ("STALE_EVIDENCE", "UNIQUE_SAMPLES_LT_200")


def test_freshness_rejects_epoch_contamination_and_future_lookahead() -> None:
    as_of_ts_ms = 200 * _DAY_MS
    contaminated = (
        *_samples(200, as_of_ts_ms=as_of_ts_ms),
        *_samples(1, as_of_ts_ms=as_of_ts_ms, epoch_id="OLD-EPOCH"),
    )

    mismatch = assess_evidence_freshness(
        contaminated,
        horizon=EvidenceHorizon.MICRO,
        as_of_ts_ms=as_of_ts_ms,
        epoch=_epoch(),
    )

    assert mismatch.status is EvidenceFreshnessStatus.EPOCH_MISMATCH
    assert mismatch.promotion_allowed is False
    assert "EVIDENCE_EPOCH_ID_MISMATCH" in mismatch.reason_codes
    future = EvidenceSample(
        "FUTURE",
        as_of_ts_ms + 1,
        "EPOCH-2026-01",
        "TREND_PULLBACK_V3",
    )
    with pytest.raises(ValueError, match="미래 표본"):
        assess_evidence_freshness(
            (future,),
            horizon="FAST",
            as_of_ts_ms=as_of_ts_ms,
            epoch=_epoch(),
        )


def test_fast_hysteresis_requires_two_distinct_trigger_bars_and_is_pure() -> None:
    initial = HysteresisState()
    first = advance_hysteresis(
        initial,
        config=HysteresisConfig(),
        score=Decimal("0.65"),
        proposed_side=Side.LONG,
        completed_trigger_bar=True,
        trigger_bar_index=1,
    )
    duplicate = advance_hysteresis(
        first.state,
        config=HysteresisConfig(),
        score=Decimal("0.70"),
        proposed_side=Side.LONG,
        completed_trigger_bar=True,
        trigger_bar_index=1,
    )
    second = advance_hysteresis(
        duplicate.state,
        config=HysteresisConfig(),
        score=Decimal("0.70"),
        proposed_side=Side.LONG,
        completed_trigger_bar=True,
        trigger_bar_index=2,
    )

    assert initial == HysteresisState()
    assert first.decision is HysteresisDecision.CONFIRMING
    assert first.state.trigger_confirmations == 1
    assert duplicate.state.trigger_confirmations == 1
    assert "DUPLICATE_OR_OUT_OF_ORDER_TRIGGER_BAR" in duplicate.reason_codes
    assert second.decision is HysteresisDecision.ARMED
    assert second.state.armed is True
    assert second.state.side is Side.LONG


def test_no_trade_zone_holds_state_and_disarm_threshold_is_conservative() -> None:
    armed = HysteresisState(armed=True, side=Side.LONG, last_trigger_bar_index=2)
    held = advance_hysteresis(
        armed,
        config=HysteresisConfig(),
        score=Decimal("0.60"),
        proposed_side=Side.SHORT,
    )
    disarmed = advance_hysteresis(
        held.state,
        config=HysteresisConfig(),
        score=Decimal("0.50"),
        proposed_side=Side.LONG,
    )

    assert held.decision is HysteresisDecision.HELD
    assert held.state == armed
    assert disarmed.decision is HysteresisDecision.DISARMED
    assert disarmed.state.armed is False
    assert disarmed.state.side is None


def test_fast_event_flow_can_confirm_without_waiting_for_two_bars() -> None:
    transition = advance_hysteresis(
        HysteresisState(),
        config=HysteresisConfig(),
        score=Decimal("0.80"),
        proposed_side=Side.SHORT,
        event_flow_confirmation_ms=500,
    )

    assert transition.decision is HysteresisDecision.ARMED
    assert transition.state.side is Side.SHORT


def test_swing_hysteresis_requires_setup_and_trigger_confirmation() -> None:
    config = HysteresisConfig(confirmation_mode=HysteresisMode.SWING)
    setup = advance_hysteresis(
        HysteresisState(),
        config=config,
        score=Decimal("0.70"),
        proposed_side=Side.LONG,
        completed_setup_bar=True,
    )
    trigger = advance_hysteresis(
        setup.state,
        config=config,
        score=Decimal("0.70"),
        proposed_side=Side.LONG,
        completed_trigger_bar=True,
        trigger_bar_index=1,
    )

    assert setup.decision is HysteresisDecision.CONFIRMING
    assert setup.state.setup_confirmed is True
    assert trigger.decision is HysteresisDecision.ARMED


def test_side_flip_requires_invalidation_opposite_confirmation_and_cooldown() -> None:
    config = HysteresisConfig()
    armed = HysteresisState(armed=True, side=Side.LONG, last_trigger_bar_index=2)
    blocked = advance_hysteresis(
        armed,
        config=config,
        score=Decimal("0.80"),
        proposed_side=Side.SHORT,
        completed_trigger_bar=True,
        trigger_bar_index=3,
    )
    first = advance_hysteresis(
        blocked.state,
        config=config,
        score=Decimal("0.80"),
        proposed_side=Side.SHORT,
        completed_trigger_bar=True,
        trigger_bar_index=4,
        structure_invalidated=True,
        opposite_structure_confirmed=True,
    )
    second = advance_hysteresis(
        first.state,
        config=config,
        score=Decimal("0.80"),
        proposed_side=Side.SHORT,
        completed_trigger_bar=True,
        trigger_bar_index=5,
        structure_invalidated=True,
        opposite_structure_confirmed=True,
    )

    assert blocked.decision is HysteresisDecision.SIDE_FLIP_BLOCKED
    assert blocked.state.side is Side.LONG
    assert first.decision is HysteresisDecision.CONFIRMING
    assert "SIDE_FLIP_COOLDOWN_ACTIVE" in first.reason_codes
    assert second.decision is HysteresisDecision.SIDE_FLIPPED
    assert second.state.side is Side.SHORT
