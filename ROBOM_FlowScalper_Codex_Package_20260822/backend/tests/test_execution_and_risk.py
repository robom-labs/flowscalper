"""호가소진·비용·부분체결·보호·회계·위험잠금 수명주기를 검증한다."""

from decimal import Decimal

import pytest

from backend.app.costing import CostModel, CostProfile
from backend.app.domain.models import Side, Venue
from backend.app.execution import (
    BookSnapshot,
    ExitReason,
    LifecycleState,
    PaperExecutionEngine,
    PaperTradeService,
    PortfolioSet,
)
from backend.app.execution.models import OrderStatus
from backend.app.execution.simulator import PaperExecutionError
from backend.app.risk import RiskManager, RiskSizingInput, RiskState


def book(
    *,
    ts_ms: int = 1_250,
    bids: tuple[tuple[str, str], ...] = (("99.9", "5"), ("99.8", "5")),
    asks: tuple[tuple[str, str], ...] = (("100.1", "1"), ("100.2", "0.5"), ("100.5", "5")),
    stale: bool = False,
) -> BookSnapshot:
    return BookSnapshot(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        ts_ms=ts_ms,
        bids=tuple((Decimal(price), Decimal(quantity)) for price, quantity in bids),
        asks=tuple((Decimal(price), Decimal(quantity)) for price, quantity in asks),
        sequence_valid=not stale,
        stale=stale,
    )


def open_arguments(side: Side, requested: str = "1") -> dict[str, object]:
    return {
        "trade_id": "trade-001",
        "run_id": "run-001",
        "venue": Venue.FIXTURE,
        "symbol": "BTCUSDT",
        "side": side,
        "requested_quantity": Decimal(requested),
        "reference_price": Decimal("100"),
        "price_cap": Decimal("100.2") if side is Side.LONG else Decimal("99.8"),
        "initial_stop": Decimal("99") if side is Side.LONG else Decimal("101"),
        "take_profit": Decimal("102") if side is Side.LONG else Decimal("98"),
        "decision_ts_ms": 1_000,
        "book_at_arrival": book(),
        "minimum_quantity": Decimal("0.001"),
    }


def test_long_entry_consumes_asks_with_partial_ioc_and_exact_costs() -> None:
    result = PaperExecutionEngine().open_position(**open_arguments(Side.LONG, "2"))
    assert result.entry_order.status is OrderStatus.PARTIALLY_FILLED
    assert result.entry_order.filled_quantity == Decimal("1.5")
    assert result.entry_order.fill is not None
    assert result.entry_order.fill.average_price == Decimal("150.20") / Decimal("1.5")
    assert result.entry_order.fill.fee_usdt == Decimal("150.20") * Decimal("6") / Decimal(10_000)
    assert result.entry_order.fill.slippage_usdt == Decimal("0.20")
    assert result.position is not None
    assert all(
        order.requested_quantity == Decimal("1.5") for order in result.position.protection_orders
    )


def test_short_entry_consumes_bids_and_price_cap_can_zero_fill() -> None:
    arguments = open_arguments(Side.SHORT, "2")
    arguments["book_at_arrival"] = book(
        bids=(("99.9", "1"), ("99.8", "0.5"), ("99.7", "5")),
        asks=(("100.1", "5"),),
    )
    result = PaperExecutionEngine().open_position(**arguments)
    assert result.entry_order.status is OrderStatus.PARTIALLY_FILLED
    assert result.entry_order.filled_quantity == Decimal("1.5")
    assert result.entry_order.fill is not None
    assert result.entry_order.fill.average_price == Decimal("149.8") / Decimal("1.5")

    arguments["price_cap"] = Decimal("100.5")
    rejected = PaperExecutionEngine().open_position(**arguments)
    assert rejected.position is None
    assert rejected.entry_order.status is OrderStatus.REJECTED


def test_latency_and_stale_book_fail_closed() -> None:
    early = open_arguments(Side.LONG)
    early["book_at_arrival"] = book(ts_ms=1_249)
    with pytest.raises(PaperExecutionError):
        PaperExecutionEngine().open_position(**early)

    stale = open_arguments(Side.LONG)
    stale["book_at_arrival"] = book(stale=True)
    with pytest.raises(ValueError):
        PaperExecutionEngine().open_position(**stale)


@pytest.mark.parametrize(
    ("bids", "asks"),
    (
        ((("99.9", "0"),), (("100.1", "1"),)),
        ((("NaN", "1"),), (("100.1", "1"),)),
        ((("99.9", "1"),), (("100.1", "Infinity"),)),
    ),
)
def test_nonpositive_or_nonfinite_execution_book_fails_closed(
    bids: tuple[tuple[str, str], ...],
    asks: tuple[tuple[str, str], ...],
) -> None:
    snapshot = book(bids=bids, asks=asks)

    with pytest.raises(ValueError, match="유한한 양수"):
        snapshot.validate()


def test_exit_uses_executable_side_and_ambiguous_ordering_is_pessimistic() -> None:
    engine = PaperExecutionEngine()
    opened = engine.open_position(**open_arguments(Side.LONG)).position
    assert opened is not None
    exit_result = engine.close_position(
        opened,
        reason=ExitReason.STOP,
        trigger_reference_price=Decimal("99.9"),
        book_at_arrival=book(ts_ms=2_000, bids=(("99.7", "0.4"), ("99.5", "1"))),
    )
    assert exit_result.exit_order.fill is not None
    assert exit_result.exit_order.fill.average_price == Decimal("99.58")
    assert exit_result.exit_order.fill.slippage_usdt == Decimal("0.32")
    reason, flags = engine.resolve_ambiguous_boundaries(take_profit_hit=True, stop_hit=True)
    assert reason is ExitReason.STOP
    assert flags == ("AMBIGUOUS_ORDERING_PESSIMISTIC",)


def test_risk_sizing_rounds_down_and_locks_operate() -> None:
    manager = RiskManager()
    result = manager.size(
        RiskSizingInput(
            equity=Decimal("1000"),
            entry_price=Decimal("100"),
            stop_price=Decimal("99"),
            entry_fee_per_unit=Decimal("0.06"),
            stop_fee_per_unit=Decimal("0.06"),
            p95_exit_slippage_per_unit=Decimal("0.03"),
            quantity_step=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            executable_depth_quantity=Decimal("1000"),
        )
    )
    assert result.quantity == Decimal("0.869")
    assert result.planned_loss is not None and result.planned_loss <= Decimal("1")

    state = RiskState(realized_today=Decimal("-5"))
    assert "DAILY_LOSS_LOCK" in manager.entry_rejections(state, "BTC:A", 0)
    state = RiskState(open_positions=1)
    assert manager.entry_rejections(state, "BTC:A", 0) == ("MAX_OPEN_POSITIONS",)


def test_daily_and_weekly_risk_limits_roll_at_utc_boundaries() -> None:
    manager = RiskManager()
    friday_ms = 86_400_000
    state = RiskState(
        realized_today=Decimal("-5"),
        realized_week=Decimal("-6"),
        daily_trade_count=12,
    )

    assert "MAX_DAILY_TRADES" in manager.entry_rejections(state, "BTC:A", friday_ms)
    saturday_ms = friday_ms + 24 * 60 * 60 * 1_000
    assert manager.entry_rejections(state, "BTC:A", saturday_ms) == ()
    assert state.daily_trade_count == 0
    assert state.realized_today == 0
    assert state.realized_week == Decimal("-6")

    monday_ms = friday_ms + 3 * 24 * 60 * 60 * 1_000
    assert manager.entry_rejections(state, "BTC:A", monday_ms) == ()
    assert state.realized_week == 0


def test_record_open_and_close_use_the_new_utc_period() -> None:
    manager = RiskManager()
    state = RiskState(realized_today=Decimal("-4"), daily_trade_count=12)
    first_day_ms = 86_400_000
    manager.entry_rejections(state, "BTC:A", first_day_ms)

    next_day_ms = first_day_ms + 24 * 60 * 60 * 1_000
    manager.record_open(state, now_ms=next_day_ms)
    manager.record_close(state, Decimal("1"), key="BTC:A", now_ms=next_day_ms + 1)

    assert state.daily_trade_count == 1
    assert state.realized_today == Decimal("1")


def test_candidate_to_closed_trade_reconciles_exactly_and_is_idempotent() -> None:
    state = RiskState()
    service = PaperTradeService(PaperExecutionEngine(), RiskManager(), state)
    opened = service.open(
        event_id="candidate-1",
        risk_key="BTCUSDT:LSA",
        now_ms=1_000,
        engine_arguments=open_arguments(Side.LONG),
    )
    assert opened is not None
    assert service.machine.state is LifecycleState.MANAGING
    assert state.open_positions == 1
    assert (
        service.open(
            event_id="candidate-1",
            risk_key="BTCUSDT:LSA",
            now_ms=1_000,
            engine_arguments=open_arguments(Side.LONG),
        )
        is opened
    )

    trade = service.close(
        event_id="exit-1",
        reason=ExitReason.TAKE_PROFIT,
        trigger_reference_price=Decimal("102"),
        book_at_arrival=book(ts_ms=5_000, bids=(("101.9", "5"),), asks=(("102.1", "5"),)),
        risk_key="BTCUSDT:LSA",
    )
    assert trade is not None
    assert service.position is None
    assert state.open_positions == 0
    assert service.machine.state is LifecycleState.COOLDOWN
    assert trade.gross_pnl_usdt == (Decimal("101.9") - Decimal("100.1"))
    assert trade.net_pnl_usdt == trade.gross_pnl_usdt - trade.fees_usdt
    equity_after = state.current_equity
    assert (
        service.close(
            event_id="exit-1",
            reason=ExitReason.TAKE_PROFIT,
            trigger_reference_price=Decimal("102"),
            book_at_arrival=book(ts_ms=5_000),
            risk_key="BTCUSDT:LSA",
        )
        is trade
    )
    assert state.current_equity == equity_after


def test_base_and_stress_portfolios_are_separate() -> None:
    portfolios = PortfolioSet()
    assert portfolios.states[CostProfile.BASE] is not portfolios.states[CostProfile.STRESS]
    model = CostModel()
    assert model.fee_bps(entry=True, profile=CostProfile.STRESS) == Decimal("12")
    assert model.arrival_latency_ms(CostProfile.STRESS) == 500
    base = PaperExecutionEngine().open_position(**open_arguments(Side.LONG, "2"))
    stress_arguments = open_arguments(Side.LONG, "2")
    stress_arguments["profile"] = CostProfile.STRESS
    stress_arguments["book_at_arrival"] = book(ts_ms=1_500)
    stress = PaperExecutionEngine().open_position(**stress_arguments)
    assert base.entry_order.fill is not None
    assert stress.entry_order.fill is not None
    assert stress.entry_order.fill.fee_usdt == base.entry_order.fill.fee_usdt * 2
    assert stress.entry_order.fill.slippage_usdt == base.entry_order.fill.slippage_usdt * 2


def test_stale_exit_faults_closed_without_losing_protection_state() -> None:
    state = RiskState()
    service = PaperTradeService(PaperExecutionEngine(), RiskManager(), state)
    opened = service.open(
        event_id="candidate-stale",
        risk_key="BTCUSDT:LSA",
        now_ms=1_000,
        engine_arguments=open_arguments(Side.LONG),
    )
    assert opened is not None
    with pytest.raises(ValueError):
        service.close(
            event_id="exit-stale",
            reason=ExitReason.DATA_GAP,
            trigger_reference_price=Decimal("99"),
            book_at_arrival=book(ts_ms=5_000, stale=True),
            risk_key="BTCUSDT:LSA",
        )
    assert service.machine.state is LifecycleState.FAULTED
    assert service.position is opened
    assert state.open_positions == 1
