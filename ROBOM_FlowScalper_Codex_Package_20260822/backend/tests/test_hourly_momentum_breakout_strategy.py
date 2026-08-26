# 완성 1시간 모멘텀 돌파 전략의 워밍·방향·시각·비용 게이트를 검증한다.

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from backend.app.candidates import CandidatePlanner
from backend.app.domain.models import RuntimeMode, Side, Venue
from backend.app.market_data import Candle
from backend.app.regime import Regime
from backend.app.runtime import PaperRuntime
from backend.app.strategies.base import CandidateStatus, PlanInputs
from backend.app.strategies.hourly_momentum_breakout import (
    HourlyMomentumBreakoutContext,
    HourlyMomentumBreakoutStrategy,
    hourly_momentum_state,
)
from backend.app.strategies.registry import ExitStyle
from backend.tests.test_strategies import features


def rising_hourly_candles(count: int = 220) -> tuple[Candle, ...]:
    rows: list[Candle] = []
    for index in range(count):
        close = Decimal("50") + Decimal(index) * Decimal("0.15")
        rows.append(
            Candle(
                symbol="BTCUSDT",
                interval_seconds=3_600,
                open_ts_ms=index * 3_600_000,
                open=close - Decimal("0.05"),
                high=close + Decimal("0.05"),
                low=close - Decimal("0.10"),
                close=close,
                volume=Decimal("150") if index == count - 1 else Decimal("100"),
                trade_count=100,
            )
        )
    return tuple(rows)


def test_hourly_state_requires_two_hundred_completed_candles() -> None:
    state = hourly_momentum_state(rising_hourly_candles(199))
    assert state.direction is None
    assert state.reason_codes == ("HOURLY_HISTORY_WARMUP",)


def test_hourly_state_confirms_long_trend_momentum_breakout_and_volume() -> None:
    state = hourly_momentum_state(rising_hourly_candles())
    assert state.direction is Side.LONG
    assert state.reason_codes == ()
    assert state.atr is not None and state.atr > 0
    assert state.adx is not None and state.adx >= 20
    assert state.relative_volume is not None and state.relative_volume >= 1.1
    assert state.momentum_24h is not None and state.momentum_24h >= 0.02


def test_hourly_strategy_qualifies_only_matching_fresh_completed_hour_signal() -> None:
    state = hourly_momentum_state(rising_hourly_candles())
    assert state.atr is not None
    entry = Decimal("100")
    risk = Decimal(str(state.atr)) * Decimal("1.8")
    context = HourlyMomentumBreakoutContext(
        side=Side.LONG,
        features=replace(features(), ts_ms=state.signal_ts_ms or 0),
        regime=Regime.TREND_UP,
        plan=PlanInputs(
            entry=entry,
            structural_stop=entry - risk,
            target=entry + risk * Decimal("4.5"),
            expected_total_cost_bps=Decimal("13"),
        ),
        state=state,
        signal_age_ms=0,
    )
    strategy = HourlyMomentumBreakoutStrategy()
    qualified = strategy.evaluate(context)
    assert qualified.status is CandidateStatus.QUALIFIED
    assert "ACTUAL_BOOK_ENTRY_REQUIRED" in qualified.reason_codes

    wrong_direction = strategy.evaluate(replace(context, side=Side.SHORT))
    assert wrong_direction.status is CandidateStatus.REJECTED
    assert "HOURLY_DIRECTION_MISMATCH" in wrong_direction.rejection_codes

    expired = strategy.evaluate(replace(context, signal_age_ms=5_001))
    assert expired.status is CandidateStatus.REJECTED
    assert "NO_NEW_COMPLETED_HOUR_SIGNAL" in expired.rejection_codes


def test_runtime_accepts_only_completed_public_hourly_candles() -> None:
    candles = rising_hourly_candles(201)
    rows = [
        {
            "open_ts_ms": candle.open_ts_ms,
            "open": str(candle.open),
            "high": str(candle.high),
            "low": str(candle.low),
            "close": str(candle.close),
            "volume": str(candle.volume),
            "trade_count": candle.trade_count,
        }
        for candle in candles
    ]
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-hourly-public-history",
        venue=Venue.BINANCE_USDM,
    )
    count = runtime.set_hourly_public_history(
        "btcusdt",
        rows,
        now_ms=200 * 3_600_000,
    )
    assert count == 200
    completed = runtime.hourly_completed_candles("BTCUSDT")
    assert len(completed) == 200
    assert completed[-1].open_ts_ms == 199 * 3_600_000


def test_hourly_runtime_targets_match_research_multiples() -> None:
    targets = CandidatePlanner._targets(
        exit_style=ExitStyle.TREND_40_60,
        side=Side.LONG,
        entry=Decimal("100"),
        worst_entry=Decimal("100.2"),
        stop=Decimal("99.2"),
        final_target=Decimal("104.5"),
        micro_vwap=Decimal("100"),
        expected_cost_bps=Decimal("13"),
        trend_take_profit_1_r=Decimal("2.2"),
        trend_take_profit_2_r=Decimal("4.5"),
    )
    assert targets[0].price == Decimal("102.20")
    assert targets[1].price == Decimal("104.50")
    assert [target.quantity_fraction for target in targets] == [
        Decimal("0.40"),
        Decimal("0.60"),
    ]
