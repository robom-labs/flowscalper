# V9 Semivariance·Periodicity·Jump·Router·위험축소 독립 계약을 검증한다.

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext
from functools import lru_cache

import pytest

from backend.app.domain.models import Side
from backend.app.features.semivariance import (
    CompletedMinuteReturn,
    CompletedPeriodicityWeek,
    DownsideRiskStatus,
    FeatureReadiness,
    RouterIntent,
    RouterStatus,
    SemivarianceInputError,
    SemivarianceJumpEngine,
    SemivarianceRouterMetrics,
    build_periodicity_calibration,
    downside_semivariance_risk_multiplier,
    evaluate_semivariance_router,
    five_minute_slot_utc,
)

_MINUTE_MS = 60_000
_DAY_MS = 24 * 60 * _MINUTE_MS
_WEEK_MS = 7 * _DAY_MS
_FIRST_MONDAY_MS = 4 * _DAY_MS
_SLOTS_PER_WEEK = 2016
_PI = Decimal("3.1415926535897932384626433832795028841971693993751")


def _weeks(
    count: int,
    *,
    base_return: Decimal = Decimal("0.01"),
    overrides: dict[int, Decimal] | None = None,
) -> tuple[CompletedPeriodicityWeek, ...]:
    values = [base_return] * _SLOTS_PER_WEEK
    for slot, value in (overrides or {}).items():
        values[slot] = value
    return tuple(
        CompletedPeriodicityWeek(
            week_start_ts_ms=_FIRST_MONDAY_MS + index * _WEEK_MS,
            five_minute_log_returns=tuple(values),
        )
        for index in range(count)
    )


def _current_week_start(completed_week_count: int = 8) -> int:
    return _FIRST_MONDAY_MS + completed_week_count * _WEEK_MS


def _minute_return(
    index: int,
    value: Decimal,
    *,
    start_ts_ms: int | None = None,
) -> CompletedMinuteReturn:
    start = (start_ts_ms if start_ts_ms is not None else _current_week_start())
    minute_start = start + index * _MINUTE_MS
    return CompletedMinuteReturn(
        minute_start_ts_ms=minute_start,
        completed_ts_ms=minute_start + _MINUTE_MS,
        log_return=value,
    )


@lru_cache(maxsize=1)
def _ready_periodicity():
    return build_periodicity_calibration(_weeks(8))


def _router_metrics(**overrides: object) -> SemivarianceRouterMetrics:
    values: dict[str, object] = {
        "semivariance_1h_ready": True,
        "semivariance_4h_ready": True,
        "jump_1h_ready": True,
        "upside_share_1h": Decimal("0.60"),
        "downside_share_1h": Decimal("0.40"),
        "upside_share_4h": Decimal("0.60"),
        "downside_share_4h": Decimal("0.40"),
        "jump_ratio_1h": Decimal("0.20"),
        "positive_jump_share_1h": Decimal("0.10"),
        "negative_jump_share_1h": Decimal("0.10"),
        "efficiency_ratio_20_1h": Decimal("0.40"),
        "trend_condition_passed": True,
        "close_5m": Decimal("101"),
        "session_vwap": Decimal("100"),
        "oi_change_z": Decimal("-1.50"),
        "long_liquidation_z": Decimal("2.00"),
        "short_liquidation_z": Decimal("2.00"),
    }
    values.update(overrides)
    return SemivarianceRouterMetrics(**values)  # type: ignore[arg-type]


def test_periodicity_uses_previous_completed_weeks_median_and_clamp() -> None:
    calibration = build_periodicity_calibration(
        _weeks(
            8,
            overrides={
                0: Decimal("0.10"),
                1: Decimal(0),
                2: Decimal("0.02"),
            },
        )
    )
    current_week = _current_week_start()

    assert calibration.status is FeatureReadiness.READY
    assert calibration.completed_week_count == 8
    assert len(calibration.slot_scales) == _SLOTS_PER_WEEK
    assert calibration.scale_for(current_week) == Decimal("4.00")
    assert calibration.scale_for(current_week + 5 * _MINUTE_MS) == Decimal("0.25")
    assert calibration.scale_for(current_week + 10 * _MINUTE_MS) == Decimal("2")
    assert five_minute_slot_utc(current_week) == 0


def test_periodicity_under_eight_weeks_blocks_jump_filter() -> None:
    calibration = build_periodicity_calibration(_weeks(7))
    engine = SemivarianceJumpEngine(periodicity=calibration)

    updates = [engine.update(_minute_return(index, Decimal("0.01"))) for index in range(60)]
    final = updates[-1]

    assert calibration.status is FeatureReadiness.PERIODICITY_UNCALIBRATED
    assert calibration.reason_codes == (
        "PERIODICITY_UNCALIBRATED",
        "COMPLETED_WEEKS_LT_8",
    )
    assert final.one_hour.status is FeatureReadiness.READY
    assert final.jump_one_hour.status is FeatureReadiness.PERIODICITY_UNCALIBRATED
    assert final.jump_one_hour.sample_count == 0


def test_current_calibration_week_is_never_used_as_current_input() -> None:
    calibration = _ready_periodicity()
    engine = SemivarianceJumpEngine(periodicity=calibration)
    assert calibration.calibration_through_week_start_ms is not None

    with pytest.raises(SemivarianceInputError, match="현재 주나 미래 주"):
        engine.update(
            _minute_return(
                0,
                Decimal("0.01"),
                start_ts_ms=calibration.calibration_through_week_start_ms,
            )
        )
    assert engine.buffer_sizes == (0, 0, 0)


def test_realized_semivariance_uses_exact_60_and_240_return_windows() -> None:
    engine = SemivarianceJumpEngine(periodicity=_ready_periodicity())
    final = None
    for index in range(240):
        value = Decimal("0.01") if index % 2 == 0 else Decimal("-0.02")
        final = engine.update(_minute_return(index, value))

    assert final is not None
    assert final.one_hour.sample_count == 60
    assert final.four_hour.sample_count == 240
    assert final.one_hour.rs_plus == Decimal("0.0030")
    assert final.one_hour.rs_minus == Decimal("0.0120")
    assert final.one_hour.realized_variance == Decimal("0.0150")
    assert final.one_hour.upside_share == Decimal("0.2")
    assert final.one_hour.downside_share == Decimal("0.8")
    assert final.one_hour.imbalance == Decimal("-0.6")
    assert final.four_hour.rs_plus == Decimal("0.0120")
    assert final.four_hour.rs_minus == Decimal("0.0480")


def test_jump_ratio_uses_periodicity_adjusted_rv_and_bipower_variation() -> None:
    engine = SemivarianceJumpEngine(periodicity=_ready_periodicity())
    adjusted_returns = [Decimal("0.01")] * 59 + [Decimal("0.10")]
    final = None
    for index, value in enumerate(adjusted_returns):
        final = engine.update(_minute_return(index, value))

    assert final is not None
    jump = final.jump_one_hour
    with localcontext() as context:
        context.prec = 50
        expected_rv = sum((value * value for value in adjusted_returns), Decimal(0))
        pair_sum = sum(
            (
                abs(left) * abs(right)
                for left, right in zip(
                    adjusted_returns,
                    adjusted_returns[1:],
                    strict=False,
                )
            ),
            Decimal(0),
        )
        expected_bv = _PI / Decimal(2) * pair_sum
        expected_jump = max(expected_rv - expected_bv, Decimal(0))
        expected_ratio = expected_jump / expected_rv
    assert jump.status is FeatureReadiness.READY
    assert jump.realized_variance == expected_rv
    assert jump.bipower_variation == expected_bv
    assert jump.jump_variation == expected_jump
    assert jump.jump_ratio == expected_ratio


def test_invalid_incomplete_gap_and_out_of_order_inputs_fail_without_mutation() -> None:
    with pytest.raises(SemivarianceInputError, match="완료된 1분"):
        replace(_minute_return(0, Decimal("0.01")), completed=False)
    with pytest.raises(SemivarianceInputError, match="유한한 Decimal"):
        _minute_return(0, Decimal("NaN"))

    engine = SemivarianceJumpEngine(periodicity=_ready_periodicity())
    engine.update(_minute_return(0, Decimal("0.01")))
    before = engine.buffer_sizes
    with pytest.raises(SemivarianceInputError, match="중복 또는 역순"):
        engine.update(_minute_return(0, Decimal("0.02")))
    assert engine.buffer_sizes == before
    with pytest.raises(SemivarianceInputError, match="gap"):
        engine.update(_minute_return(2, Decimal("0.02")))
    assert engine.buffer_sizes == before


def test_replay_is_deterministic_and_rolling_memory_is_fixed() -> None:
    first = SemivarianceJumpEngine(periodicity=_ready_periodicity())
    second = SemivarianceJumpEngine(periodicity=_ready_periodicity())
    stream = tuple(
        _minute_return(
            index,
            Decimal("0.01") if index % 3 else Decimal("-0.015"),
        )
        for index in range(1_000)
    )

    first_updates = tuple(first.update(observation) for observation in stream)
    second_updates = tuple(second.update(observation) for observation in stream)

    assert first_updates == second_updates
    assert first.buffer_sizes == second.buffer_sizes == (60, 240, 60)


def test_long_and_short_momentum_router_are_symmetric_and_do_not_create_side() -> None:
    long_decision = evaluate_semivariance_router(
        candidate_side=Side.LONG,
        intent=RouterIntent.MOMENTUM,
        metrics=_router_metrics(),
    )
    short_decision = evaluate_semivariance_router(
        candidate_side=Side.SHORT,
        intent=RouterIntent.MOMENTUM,
        metrics=_router_metrics(
            upside_share_1h=Decimal("0.40"),
            downside_share_1h=Decimal("0.60"),
            upside_share_4h=Decimal("0.40"),
            downside_share_4h=Decimal("0.60"),
            close_5m=Decimal("99"),
        ),
    )

    assert long_decision.status is short_decision.status is RouterStatus.MOMENTUM_PASS
    assert long_decision.candidate_side is Side.LONG
    assert short_decision.candidate_side is Side.SHORT
    assert long_decision.creates_direction is short_decision.creates_direction is False
    assert long_decision.route_allowed is short_decision.route_allowed is True


def test_momentum_veto_is_ablatable_and_warmup_waits() -> None:
    metrics = _router_metrics(
        upside_share_1h=Decimal("0.35"),
        downside_share_1h=Decimal("0.65"),
    )
    vetoed = evaluate_semivariance_router(
        candidate_side=Side.LONG,
        intent=RouterIntent.MOMENTUM,
        metrics=metrics,
    )
    ablation = evaluate_semivariance_router(
        candidate_side=Side.LONG,
        intent=RouterIntent.MOMENTUM,
        metrics=metrics,
        veto_enabled=False,
    )
    warmup = evaluate_semivariance_router(
        candidate_side=Side.LONG,
        intent=RouterIntent.MOMENTUM,
        metrics=replace(metrics, semivariance_4h_ready=False),
    )

    assert vetoed.status is RouterStatus.MOMENTUM_VETO
    assert "ADVERSE_SEMIVARIANCE_VETO" in vetoed.reason_codes
    assert ablation.status is RouterStatus.MOMENTUM_BLOCKED
    assert warmup.status is RouterStatus.WAIT


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_reversal_router_only_arms_existing_candidate_side(side: Side) -> None:
    metrics = _router_metrics(
        upside_share_1h=Decimal("0.25") if side is Side.LONG else Decimal("0.75"),
        downside_share_1h=Decimal("0.75") if side is Side.LONG else Decimal("0.25"),
    )

    decision = evaluate_semivariance_router(
        candidate_side=side,
        intent=RouterIntent.REVERSAL,
        metrics=metrics,
    )

    assert decision.status is RouterStatus.REVERSAL_ARMED
    assert decision.candidate_side is side
    assert decision.creates_direction is False


@pytest.mark.parametrize(
    ("downside_count", "expected_breadth", "expected_multiplier"),
    [
        (39, Decimal("0.39"), Decimal("1.00")),
        (40, Decimal("0.40"), Decimal("0.75")),
        (60, Decimal("0.60"), Decimal("0.50")),
        (75, Decimal("0.75"), Decimal("0.25")),
    ],
)
def test_downside_breadth_risk_multiplier_uses_exact_boundaries(
    downside_count: int,
    expected_breadth: Decimal,
    expected_multiplier: Decimal,
) -> None:
    shares = [Decimal("0.65")] * downside_count + [Decimal("0.40")] * (
        100 - downside_count
    )

    decision = downside_semivariance_risk_multiplier(shares)

    assert decision.status is DownsideRiskStatus.READY
    assert decision.valid_symbol_count == 100
    assert decision.downside_symbol_count == downside_count
    assert decision.downside_breadth == expected_breadth
    assert decision.multiplier == expected_multiplier
    assert decision.multiplier <= Decimal(1)


def test_downside_breadth_excludes_unavailable_and_rejects_invalid_values() -> None:
    waiting = downside_semivariance_risk_multiplier((None, None))
    valid = downside_semivariance_risk_multiplier(
        (None, Decimal("0.70"), Decimal("0.50"))
    )

    assert waiting.status is DownsideRiskStatus.WAIT
    assert waiting.multiplier is None
    assert valid.valid_symbol_count == 2
    assert valid.downside_breadth == Decimal("0.5")
    assert valid.multiplier == Decimal("0.75")
    with pytest.raises(SemivarianceInputError, match="downside_share_1h"):
        downside_semivariance_risk_multiplier((Decimal("NaN"),))
