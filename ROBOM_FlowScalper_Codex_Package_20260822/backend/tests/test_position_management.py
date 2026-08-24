"""120초 초과 보유, edge decay, 이익보호, stop 불변조건과 stale 한계를 검증한다."""

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.domain.models import Side, Venue
from backend.app.execution import BookSnapshot, PaperExecutionEngine
from backend.app.positions import (
    ManagementAction,
    PositionHealth,
    PositionManager,
    StopWideningError,
)
from backend.app.risk import RiskManager, RiskState


def opened_position(side: Side = Side.LONG):
    entry_book = BookSnapshot(
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        ts_ms=1_250,
        bids=((Decimal("99.9"), Decimal("5")),),
        asks=((Decimal("100.1"), Decimal("5")),),
    )
    result = PaperExecutionEngine().open_position(
        trade_id=f"trade-{side.value}",
        run_id="run",
        venue=Venue.FIXTURE,
        symbol="BTCUSDT",
        side=side,
        requested_quantity=Decimal("1"),
        reference_price=Decimal("100"),
        price_cap=Decimal("100.2") if side is Side.LONG else Decimal("99.8"),
        initial_stop=Decimal("99") if side is Side.LONG else Decimal("101"),
        take_profit=Decimal("102") if side is Side.LONG else Decimal("98"),
        decision_ts_ms=1_000,
        book_at_arrival=entry_book,
        minimum_quantity=Decimal("0.001"),
    )
    assert result.position is not None
    return result.position


def health(**overrides: object) -> PositionHealth:
    values: dict[str, object] = {
        "structure_health": 0.9,
        "flow_health": 0.9,
        "microprice_alignment": 0.9,
        "liquidity_health": 0.9,
        "spread_health": 0.9,
        "opposite_aggression": 0.1,
        "data_health": 1.0,
        "remaining_edge": Decimal("0.5"),
        "current_r": Decimal("0.2"),
        "mfe_r": Decimal("0.4"),
        "mae_r": Decimal("-0.1"),
    }
    values.update(overrides)
    return PositionHealth(**values)  # type: ignore[arg-type]


def test_healthy_thesis_holds_beyond_120_seconds() -> None:
    position = opened_position()
    decision = PositionManager().evaluate(
        position,
        health(),
        now_ms=position.opened_ts_ms + 121_000,
    )
    assert decision.action is ManagementAction.HOLD
    assert decision.holding_ms == 121_000
    assert decision.reason_codes == ("ENTRY_THESIS_HEALTHY",)


def test_edge_decay_cannot_exit_during_ten_second_grace() -> None:
    position = opened_position()
    manager = PositionManager()
    adverse = health(flow_health=0.1, remaining_edge=Decimal("-0.1"))
    first = manager.evaluate(position, adverse, now_ms=position.opened_ts_ms + 1_000)
    second = manager.evaluate(position, adverse, now_ms=position.opened_ts_ms + 2_000)
    assert first.action is ManagementAction.HOLD
    assert second.action is ManagementAction.HOLD
    assert first.reason_codes == ("EDGE_DECAY_GRACE_ACTIVE",)
    assert second.reason_codes == ("EDGE_DECAY_GRACE_ACTIVE",)


def test_edge_decay_exits_only_after_grace_and_multi_signal_persistence() -> None:
    position = opened_position()
    manager = PositionManager()
    adverse = health(flow_health=0.1, remaining_edge=Decimal("-0.1"))
    grace_end = manager.evaluate(
        position,
        adverse,
        now_ms=position.opened_ts_ms + 10_000,
    )
    confirming = manager.evaluate(
        position,
        adverse,
        now_ms=position.opened_ts_ms + 12_999,
    )
    exit_decision = manager.evaluate(
        position,
        adverse,
        now_ms=position.opened_ts_ms + 13_000,
    )
    assert grace_end.action is ManagementAction.HOLD
    assert grace_end.reason_codes == ("EDGE_DECAY_CONFIRMING",)
    assert confirming.action is ManagementAction.HOLD
    assert exit_decision.action is ManagementAction.EXIT_EDGE_DECAY
    assert exit_decision.holding_ms == 13_000
    assert exit_decision.reason_codes == ("FLOW_DECAY", "REMAINING_EDGE_NON_POSITIVE")


def test_single_adverse_signal_does_not_force_soft_exit() -> None:
    position = opened_position()
    manager = PositionManager()
    adverse = health(flow_health=0.1)
    first = manager.evaluate(
        position,
        adverse,
        now_ms=position.opened_ts_ms + 10_000,
    )
    much_later = manager.evaluate(
        position,
        adverse,
        now_ms=position.opened_ts_ms + 120_000,
    )
    assert first.action is ManagementAction.HOLD
    assert much_later.action is ManagementAction.HOLD
    assert much_later.reason_codes == (
        "EDGE_DECAY_INSUFFICIENT_CONFIRMATION",
        "FLOW_DECAY",
    )


def test_profit_protection_tightens_and_uses_early_exit() -> None:
    position = opened_position()
    manager = PositionManager()
    protected = health(
        flow_health=0.1,
        remaining_edge=Decimal("-0.1"),
        mfe_r=Decimal("1.1"),
    )
    first = manager.evaluate(position, protected, now_ms=position.opened_ts_ms + 1_000)
    second = manager.evaluate(position, protected, now_ms=position.opened_ts_ms + 4_000)
    assert first.proposed_stop is not None and first.proposed_stop > position.current_stop
    assert second.action is ManagementAction.EXIT_PROFIT_PROTECTION
    tightened = manager.tighten_stop(position, first.proposed_stop)
    assert tightened.current_stop == first.proposed_stop


def test_initial_stop_never_widens_for_long_or_short() -> None:
    manager = PositionManager()
    long = opened_position(Side.LONG)
    short = opened_position(Side.SHORT)
    with pytest.raises(StopWideningError):
        manager.tighten_stop(long, Decimal("98"))
    with pytest.raises(StopWideningError):
        manager.tighten_stop(short, Decimal("102"))
    assert manager.tighten_stop(long, Decimal("99.5")).initial_stop == Decimal("99")
    assert manager.tighten_stop(short, Decimal("100.5")).initial_stop == Decimal("101")


def test_data_gap_preserves_protection_then_emergency_exits_on_recovery() -> None:
    position = opened_position()
    manager = PositionManager()
    waiting = manager.evaluate(
        position,
        health(data_health=0.0),
        now_ms=position.opened_ts_ms + 899_000,
        data_stale=True,
        recovered_gap_duration_ms=899_000,
    )
    assert waiting.action is ManagementAction.HOLD_DATA_GAP
    assert position.protection_orders
    emergency = manager.evaluate(
        position,
        health(),
        now_ms=position.opened_ts_ms + 900_000,
        recovered_gap_duration_ms=900_000,
    )
    assert emergency.action is ManagementAction.EXIT_EMERGENCY_STALE
    assert emergency.reason_codes == ("EMERGENCY_STALE_LIMIT",)


def test_three_losses_create_global_cooldown() -> None:
    manager = RiskManager()
    state = RiskState()
    for index in range(3):
        manager.record_open(state)
        manager.record_close(
            state,
            Decimal("-1"),
            key=f"BTC:S{index}",
            now_ms=index,
        )
    assert state.global_consecutive_losses == 3
    assert "GLOBAL_COOLDOWN_ACTIVE" in manager.entry_rejections(state, "ETH:A", 4)


def test_health_vector_rejects_non_finite_values() -> None:
    with pytest.raises(ValueError):
        replace(health(), structure_health=float("nan")).validate()
