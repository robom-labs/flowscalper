# ADX·DMI 추세 확인 후보의 사전등록, 완성봉 계산과 안전 경계를 검증한다.

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.research_adx_dmi_diversified_asymmetric_runner import (
    ADX_MINIMUM,
    ADX_RISE_LOOKBACK,
    CANDIDATE_SUFFIX,
    PREREGISTERED_ADX_DMI_DIVERSIFIED_CANDIDATES,
    REENTRY_COOLDOWN_HOURS,
    AdxDmiSignalGate,
    DirectionalMovement,
    _source_candidate_id,
    build_directional_movement,
)
from scripts.research_public_intraday_trend_candidates import IntradayBar

INTERVAL_MS = 4 * 60 * 60 * 1_000


def _bar(index: int, *, close: float | None = None) -> IntradayBar:
    value = 100 + index if close is None else close
    return IntradayBar(
        symbol="BTCUSDT",
        interval_minutes=240,
        open_ts_ms=index * INTERVAL_MS,
        open=value - 0.4,
        high=value + 1.0,
        low=value - 1.0,
        close=value,
        volume=1_000,
    )


def test_hyp133_preregisters_only_four_frozen_sources_with_168h_cooldown() -> None:
    specs = PREREGISTERED_ADX_DMI_DIVERSIFIED_CANDIDATES

    assert len(specs) == 4
    assert len({spec.candidate_id for spec in specs}) == 4
    assert all(spec.candidate_id.endswith(CANDIDATE_SUFFIX) for spec in specs)
    assert all(spec.entry.cooldown_hours == REENTRY_COOLDOWN_HOURS for spec in specs)
    assert {_source_candidate_id(spec.candidate_id) for spec in specs} == {
        "T131_OBV_MA_CROSS_4H_BOTH_BALANCED_CHAND22_ATR3",
        "T131_OBV_PRICE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR3",
        "T131_SQUEEZE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR4",
        "T131_OBV_FIRST_PULLBACK_4H_BOTH_BALANCED_CHAND22_ATR4",
    }


def test_directional_movement_uses_no_future_bar_and_detects_uptrend() -> None:
    rows = tuple(_bar(index) for index in range(70))
    changed_future = (*rows[:-1], _bar(69, close=1_000))

    baseline = build_directional_movement(rows)
    changed = build_directional_movement(changed_future)

    assert baseline[:-1] == changed[:-1]
    latest = baseline[-1]
    assert latest is not None
    assert latest.adx >= ADX_MINIMUM
    assert latest.plus_di > latest.minus_di


def test_directional_movement_rejects_invalid_period() -> None:
    with pytest.raises(ValueError, match="DMI period"):
        build_directional_movement((_bar(0),), period=0)


def test_adx_dmi_gate_requires_strength_rise_and_matching_direction() -> None:
    spec = PREREGISTERED_ADX_DMI_DIVERSIFIED_CANDIDATES[0]
    values: list[DirectionalMovement | None] = [None] * 12
    values[3] = DirectionalMovement(plus_di=30, minus_di=20, adx=24)
    values[6] = DirectionalMovement(plus_di=35, minus_di=15, adx=27)
    gate = AdxDmiSignalGate({"BTCUSDT": values}, (spec,))

    assert ADX_RISE_LOOKBACK == 3
    assert gate(spec, "BTCUSDT", 6, 1) is True
    gate.observe_qualified(spec, "BTCUSDT", 6, 1, cooldown_blocked=True)
    assert gate(spec, "BTCUSDT", 6, -1) is False

    audit = gate.audit[spec.candidate_id]
    assert audit["original_qualified_count"] == 2
    assert audit["dmi_gate_pass_count"] == 1
    assert audit["direction_mismatch_count"] == 1
    assert audit["cooldown_168h_blocked_count"] == 1


@pytest.mark.parametrize(
    ("current", "previous", "reason"),
    [
        (
            DirectionalMovement(plus_di=35, minus_di=15, adx=24.9),
            DirectionalMovement(plus_di=30, minus_di=20, adx=20),
            "adx_below_25_count",
        ),
        (
            DirectionalMovement(plus_di=35, minus_di=15, adx=27),
            DirectionalMovement(plus_di=30, minus_di=20, adx=27),
            "adx_not_rising_over_3_bars_count",
        ),
    ],
)
def test_adx_dmi_gate_records_one_primary_rejection_reason(
    current: DirectionalMovement,
    previous: DirectionalMovement,
    reason: str,
) -> None:
    spec = replace(
        PREREGISTERED_ADX_DMI_DIVERSIFIED_CANDIDATES[0],
        candidate_id="T133_TEST_ADX25_RISE3_DMI_COOLDOWN168H",
    )
    values: list[DirectionalMovement | None] = [None] * 12
    values[3] = previous
    values[6] = current
    gate = AdxDmiSignalGate({"BTCUSDT": values}, (spec,))

    assert gate(spec, "BTCUSDT", 6, 1) is False
    audit = gate.audit[spec.candidate_id]
    assert audit["original_qualified_count"] == 1
    assert audit[reason] == 1
    assert sum(
        audit[key]
        for key in (
            "missing_dmi_count",
            "adx_below_25_count",
            "adx_not_rising_over_3_bars_count",
            "direction_mismatch_count",
            "dmi_gate_pass_count",
        )
    ) == 1
