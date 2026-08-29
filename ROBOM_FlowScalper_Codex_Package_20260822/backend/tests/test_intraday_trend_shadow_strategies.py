"""중단기 추세 V2의 완성봉·양방향·비용·보유·SHADOW 계약을 검증한다."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.domain.models import RuntimeMode, Side
from backend.app.market_data import Candle
from backend.app.regime import Regime
from backend.app.runtime import PaperRuntime
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.intraday_trend import (
    IntradayTrendVariant,
    intraday_trend_state,
)
from backend.app.strategies.registry import StrategyMode, StrategyRegistry
from backend.app.strategies.runtime_evaluator import StrategySignalEvaluator
from backend.tests.test_strategies import features


def _candle(
    index: int,
    interval_seconds: int,
    *,
    close: float,
    direction: int,
) -> Candle:
    return Candle(
        symbol="BTCUSDT",
        interval_seconds=interval_seconds,
        open_ts_ms=index * interval_seconds * 1_000,
        open=Decimal(str(close - direction * 0.05)),
        high=Decimal(str(close + 0.15)),
        low=Decimal(str(close - 0.15)),
        close=Decimal(str(close)),
        volume=Decimal(str(100 + index % 5)),
        trade_count=100,
    )


def _trend_rows(
    interval_seconds: int,
    *,
    direction: int,
    count: int,
    step: float,
) -> tuple[Candle, ...]:
    return tuple(
        _candle(
            index,
            interval_seconds,
            close=100 + direction * index * step,
            direction=direction,
        )
        for index in range(count)
    )


def _reclaim_rows(
    interval_seconds: int,
    *,
    direction: int,
) -> tuple[Candle, ...]:
    rows = list(_trend_rows(interval_seconds, direction=direction, count=120, step=0.2))
    pivot = float(rows[-3].close)
    if direction == 1:
        rows[-2] = replace(
            rows[-2],
            open=Decimal(str(pivot + 0.1)),
            high=Decimal(str(pivot + 0.25)),
            low=Decimal(str(pivot - 2.0)),
            close=Decimal(str(pivot - 0.6)),
            volume=Decimal("110"),
        )
        rows[-1] = replace(
            rows[-1],
            open=Decimal(str(pivot - 0.3)),
            high=Decimal(str(pivot + 0.9)),
            low=Decimal(str(pivot - 0.4)),
            close=Decimal(str(pivot + 0.8)),
            volume=Decimal("120"),
        )
    else:
        rows[-2] = replace(
            rows[-2],
            open=Decimal(str(pivot - 0.1)),
            high=Decimal(str(pivot + 2.0)),
            low=Decimal(str(pivot - 0.25)),
            close=Decimal(str(pivot + 0.6)),
            volume=Decimal("110"),
        )
        rows[-1] = replace(
            rows[-1],
            open=Decimal(str(pivot + 0.3)),
            high=Decimal(str(pivot + 0.4)),
            low=Decimal(str(pivot - 0.9)),
            close=Decimal(str(pivot - 0.8)),
            volume=Decimal("120"),
        )
    return tuple(rows)


@pytest.mark.parametrize(
    ("direction", "side", "regime"),
    [
        (1, Side.LONG, Regime.TREND_UP),
        (-1, Side.SHORT, Regime.TREND_DOWN),
    ],
)
def test_completed_30m_breakout_retest_qualifies_both_sides_after_live_flow_confirmation(
    direction: int,
    side: Side,
    regime: Regime,
) -> None:
    thirty_minute = _trend_rows(1_800, direction=direction, count=120, step=0.2)
    hourly = _trend_rows(3_600, direction=direction, count=60, step=0.3)
    state = intraday_trend_state(
        thirty_minute,
        hourly,
        IntradayTrendVariant.BREAKOUT_RETEST_30M,
    )
    assert state.direction is side
    assert state.reason_codes == ()
    assert state.signal_ts_ms is not None

    registry = StrategyRegistry()
    target = "BREAKOUT_RETEST_30M_V2"
    for strategy_id in registry.strategy_ids:
        registry.configure(
            strategy_id,
            mode=StrategyMode.SHADOW if strategy_id == target else StrategyMode.OFF,
            long_enabled=True,
            short_enabled=True,
        )
    midpoint = float(thirty_minute[-1].close)
    snapshot = replace(
        features(),
        ts_ms=state.signal_ts_ms,
        mid=midpoint,
        microprice=midpoint + direction * 0.01,
        microprice_minus_mid_bps=float(direction),
        micro_vwap_10s=midpoint,
        ofi_250ms=float(direction),
        ofi_1s=float(direction * 2),
        ofi_3s=float(direction * 3),
        trade_imbalance_1s=direction * 0.4,
        trade_imbalance_3s=direction * 0.3,
        trade_imbalance_10s=direction * 0.2,
    )
    evaluator = StrategySignalEvaluator()
    first = evaluator.evaluate(
        registry,
        snapshot,
        regime,
        thirty_minute_candles=thirty_minute,
        hourly_candles=hourly,
    )
    second = evaluator.evaluate(
        registry,
        replace(snapshot, ts_ms=snapshot.ts_ms + 1_000),
        regime,
        thirty_minute_candles=thirty_minute,
        hourly_candles=hourly,
    )

    first_target = next(row for row in first if row.decision.side is side)
    second_target = next(row for row in second if row.decision.side is side)
    assert first_target.decision.status is CandidateStatus.REJECTED
    assert "PUBLIC_BOOK_FLOW_CONFIRMATION_PENDING" in first_target.decision.rejection_codes
    assert second_target.decision.status is CandidateStatus.QUALIFIED
    assert second_target.shadow_eligible
    assert not second_target.main_eligible
    assert second_target.decision.initial_stop is not None
    assert second_target.decision.take_profit is not None
    assert second_target.decision.net_reward_risk is not None
    assert second_target.decision.net_reward_risk >= Decimal("1.20")


@pytest.mark.parametrize(
    ("interval_seconds", "variant"),
    [
        (900, IntradayTrendVariant.PULLBACK_RECLAIM_15M),
        (1_800, IntradayTrendVariant.MULTISPEED_RECLAIM_30M),
    ],
)
@pytest.mark.parametrize(("direction", "side"), [(1, Side.LONG), (-1, Side.SHORT)])
def test_pullback_and_multispeed_reclaim_are_symmetric_completed_bar_setups(
    interval_seconds: int,
    variant: IntradayTrendVariant,
    direction: int,
    side: Side,
) -> None:
    state = intraday_trend_state(
        _reclaim_rows(interval_seconds, direction=direction),
        _trend_rows(3_600, direction=direction, count=60, step=0.3),
        variant,
    )

    assert state.direction is side
    assert state.reason_codes == ()
    assert state.structural_stop is not None


def test_intraday_state_fails_closed_on_warmup_and_recent_gap() -> None:
    hourly = _trend_rows(3_600, direction=1, count=60, step=0.3)
    warmup = _trend_rows(900, direction=1, count=99, step=0.2)
    gapped = list(_trend_rows(900, direction=1, count=100, step=0.2))
    gapped[-1] = replace(gapped[-1], open_ts_ms=gapped[-1].open_ts_ms + 900_000)

    warmup_state = intraday_trend_state(
        warmup,
        hourly,
        IntradayTrendVariant.PULLBACK_RECLAIM_15M,
    )
    gap_state = intraday_trend_state(
        tuple(gapped),
        hourly,
        IntradayTrendVariant.PULLBACK_RECLAIM_15M,
    )
    hourly_gapped = list(hourly)
    hourly_gapped[-1] = replace(
        hourly_gapped[-1],
        open_ts_ms=hourly_gapped[-1].open_ts_ms + 3_600_000,
    )
    higher_gap_state = intraday_trend_state(
        _reclaim_rows(900, direction=1),
        tuple(hourly_gapped),
        IntradayTrendVariant.PULLBACK_RECLAIM_15M,
    )

    assert warmup_state.reason_codes == ("INTRADAY_HISTORY_WARMUP",)
    assert gap_state.reason_codes == ("INTRADAY_CANDLE_GAP",)
    assert "HIGHER_TIMEFRAME_HISTORY_OR_TREND_NOT_READY" in higher_gap_state.reason_codes


def test_runtime_public_warmup_excludes_incomplete_bar_and_keeps_interval_isolated() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.LIVE_SHADOW_PAPER, run_id="run-intraday-warmup")
    interval_seconds = 900
    rows = [
        {
            "open_ts_ms": index * interval_seconds * 1_000,
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100.5",
            "volume": "10",
            "trade_count": 20,
        }
        for index in range(3)
    ]
    now_ms = 2 * interval_seconds * 1_000

    count = runtime.set_strategy_public_history(
        "btcusdt",
        interval_seconds,
        rows,
        now_ms=now_ms,
    )

    assert count == 2
    completed = runtime.strategy_completed_candles("BTCUSDT", interval_seconds)
    assert [row.open_ts_ms for row in completed] == [0, 900_000]
    assert runtime.hourly_completed_candles("BTCUSDT") == ()


def test_intraday_registry_keeps_ten_simultaneous_shadow_hypotheses() -> None:
    registry = StrategyRegistry()
    rows = registry.rows()
    shadow = [row for row in rows if row["mode"] == "SHADOW"]
    retired = [row for row in rows if row["mode"] == "OFF"]

    assert len(shadow) == 10
    assert len(retired) == 5
    assert all(row["long_enabled"] and row["short_enabled"] for row in shadow)
    assert all(row["lifecycle"] == "RETIRED" for row in retired)
    assert all(row["paper_only"] for row in rows)
