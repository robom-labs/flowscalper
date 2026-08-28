"""동적 유니버스, 거래소 격리와 저시간봉 생성을 검증한다."""

from decimal import Decimal

import pytest

from backend.app.adapters.base import BackoffPolicy, ConnectionHealth, ConnectionSupervisor
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.market import Instrument, Ticker, TradeTick
from backend.app.domain.models import DataQuality, MarketEvent, Venue
from backend.app.domain.run_guard import RunVenueGuard, VenueMixingError
from backend.app.market_data import CandleBuilder
from backend.app.universe import UniverseObservation, UniverseSelector


def observation(symbol: str, turnover: str, spread: str = "0.10") -> UniverseObservation:
    instrument = Instrument(
        venue=Venue.BINANCE_USDM,
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        status="TRADING",
        contract_type="PERPETUAL",
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
    )
    mid = Decimal("100")
    half = Decimal(spread) / 2
    ticker = Ticker(
        venue=Venue.BINANCE_USDM,
        symbol=symbol,
        bid=mid - half,
        ask=mid + half,
        quote_turnover_24h=Decimal(turnover),
        trade_count_24h=int(Decimal(turnover) / 1000),
    )
    return UniverseObservation(
        instrument=instrument,
        ticker=ticker,
        executable_depth_usdt=Decimal("100000"),
        data_quality_score=1.0,
        gap_count=0,
    )


def test_universe_filters_and_ranks_without_venue_mix() -> None:
    selector = UniverseSelector(wide_max=2, deep_max=1)
    selected = selector.select(
        [
            observation("BTCUSDT", "100000000"),
            observation("ETHUSDT", "80000000"),
            observation("TINYUSDT", "1000"),
        ]
    )
    assert [item.symbol for item in selected.wide] == ["BTCUSDT", "ETHUSDT"]
    assert selected.deep[0].symbol == "BTCUSDT"
    assert selected.excluded["TINYUSDT"] == ("LOW_TURNOVER",)

    mixed = observation("SOLUSDT", "90000000")
    other_instrument = Instrument(
        venue=Venue.BYBIT_LINEAR,
        symbol=mixed.instrument.symbol,
        base_asset=mixed.instrument.base_asset,
        quote_asset=mixed.instrument.quote_asset,
        status=mixed.instrument.status,
        contract_type=mixed.instrument.contract_type,
        tick_size=mixed.instrument.tick_size,
        quantity_step=mixed.instrument.quantity_step,
        minimum_quantity=mixed.instrument.minimum_quantity,
    )
    with pytest.raises(ValueError):
        selector.select(
            [
                mixed,
                UniverseObservation(other_instrument, mixed.ticker, Decimal(1), 1.0, 0),
            ]
        )


def test_candles_use_only_observed_trades() -> None:
    builder = CandleBuilder()
    assert builder.ALLOWED_INTERVALS == (
        1,
        5,
        15,
        30,
        60,
        180,
        300,
        600,
        900,
        1_800,
        3_600,
        14_400,
        21_600,
    )
    builder.add(TradeTick(Venue.FIXTURE, "BTCUSDT", Decimal("100"), Decimal("2"), 1_000, True))
    builder.add(TradeTick(Venue.FIXTURE, "BTCUSDT", Decimal("101"), Decimal("3"), 1_500, True))
    completed = builder.add(
        TradeTick(Venue.FIXTURE, "BTCUSDT", Decimal("99"), Decimal("1"), 2_000, False)
    )
    one_second = next(item for item in completed if item.interval_seconds == 1)
    assert (one_second.open, one_second.high, one_second.low, one_second.close) == (
        Decimal("100"),
        Decimal("101"),
        Decimal("100"),
        Decimal("101"),
    )
    assert one_second.volume == Decimal("5")
    assert one_second.quote_volume == Decimal("503")
    assert one_second.taker_buy_volume == Decimal("5")
    assert one_second.taker_sell_volume == 0
    assert builder.series("BTCUSDT", 1) == (one_second, builder.snapshot("BTCUSDT")[0])


def test_candle_builder_ignores_identified_duplicates_and_late_trades() -> None:
    builder = CandleBuilder(intervals=(60,))
    original = TradeTick(
        Venue.FIXTURE,
        "BTCUSDT",
        Decimal("100"),
        Decimal("2"),
        61_000,
        True,
        "trade-1",
    )
    assert builder.add(original) == []
    assert builder.add(original) == []
    assert (
        builder.add(
            TradeTick(
                Venue.FIXTURE,
                "BTCUSDT",
                Decimal("999"),
                Decimal("9"),
                60_999,
                False,
                "trade-late",
            )
        )
        == []
    )
    current = builder.series("BTCUSDT", 60)[0]
    assert current.close == Decimal("100")
    assert current.volume == Decimal("2")
    assert builder.diagnostics.accepted_trades == 1
    assert builder.diagnostics.duplicate_events == 1
    assert builder.diagnostics.out_of_order_trades == 1


def test_candle_builder_aggregates_taker_flow_and_completed_only_series() -> None:
    builder = CandleBuilder(intervals=(60,))
    builder.add(TradeTick(Venue.FIXTURE, "ETHUSDT", Decimal("10"), Decimal("2"), 1_000, True))
    builder.add(TradeTick(Venue.FIXTURE, "ETHUSDT", Decimal("11"), Decimal("3"), 2_000, False))
    assert builder.completed_series("ETHUSDT", 60) == ()
    completed = builder.add(
        TradeTick(Venue.FIXTURE, "ETHUSDT", Decimal("12"), Decimal("1"), 60_000, True)
    )[0]
    assert completed.quote_volume == Decimal("53")
    assert completed.taker_buy_volume == Decimal("2")
    assert completed.taker_sell_volume == Decimal("3")
    assert completed.taker_buy_quote_volume == Decimal("20")
    assert completed.taker_sell_quote_volume == Decimal("33")
    assert builder.completed_series("ETHUSDT", 60) == (completed,)


def test_connection_rotation_staleness_and_venue_guard() -> None:
    clock = DeterministicClock()
    health = ConnectionHealth()
    health.mark_connected(clock.monotonic_ns())
    health.mark_verified_event(clock.monotonic_ns(), sequence_valid=True)
    supervisor = ConnectionSupervisor(planned_rotation_ms=1000, stale_after_ms=500)
    clock.advance_ms(1001)
    assert supervisor.should_rotate(health, clock.monotonic_ns())
    assert supervisor.is_stale(health, clock.monotonic_ns())

    event = MarketEvent(
        event_id="e",
        run_id="other",
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        event_type="BOOK_TICKER",
        venue_ts_ms=1,
        receive_monotonic_ns=1,
        quality=DataQuality(is_live=False, is_stale=False, sequence_valid=True),
        data={},
    )
    with pytest.raises(VenueMixingError):
        RunVenueGuard("run", Venue.FIXTURE).validate(event)


def test_backoff_is_bounded_and_seeded() -> None:
    import random

    rng_a = random.Random(7)
    rng_b = random.Random(7)
    policy = BackoffPolicy(initial_ms=100, maximum_ms=1000, jitter_fraction=0.1)
    values_a = [policy.delay_ms(attempt, rng_a) for attempt in range(8)]
    values_b = [policy.delay_ms(attempt, rng_b) for attempt in range(8)]
    assert values_a == values_b
    assert max(values_a) <= 1100
