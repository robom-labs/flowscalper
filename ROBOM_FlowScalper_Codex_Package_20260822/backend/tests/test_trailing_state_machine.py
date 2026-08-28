"""부분익절 러너 trailing의 bid·ask·단조·복구·중복 불변조건을 검증한다."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.domain.models import Side
from backend.app.execution.trailing import (
    TrailingActivationRule,
    TrailingModel,
    TrailingObservation,
    TrailingPolicy,
    TrailingState,
    TrailingStateMachine,
    trailing_reference_from_completed_candles,
)
from backend.app.market_data import Candle


def policy(*, partial: bool = True) -> TrailingPolicy:
    return TrailingPolicy(
        policy_id="PERCENT_1R_1PCT_V1",
        model=TrailingModel.FIXED_RATE,
        activation_rule=TrailingActivationRule.R_MULTIPLE,
        activation_r=Decimal("1"),
        partial_tp_required=partial,
        retracement_rate=Decimal("0.01"),
    )


def machine(*, side: Side = Side.LONG, partial: bool = True) -> TrailingStateMachine:
    row = TrailingStateMachine(
        account_id="STRATEGY:BASE",
        trade_id="trade-1",
        strategy_id="STRATEGY",
        strategy_version="V1",
        profile="BASE",
        symbol="BTCUSDT",
        side=side,
        entry_price=Decimal("100"),
        initial_stop=Decimal("99") if side is Side.LONG else Decimal("101"),
        fee_adjusted_breakeven=(Decimal("100.13") if side is Side.LONG else Decimal("99.87")),
        original_quantity=Decimal("1"),
        policy=policy(partial=partial),
    )
    row.confirm_entry(event_time_ms=1_000, receive_time_ms=1_001)
    return row


@pytest.mark.parametrize(
    ("side", "breakeven"),
    (
        (Side.LONG, Decimal("101")),
        (Side.SHORT, Decimal("99")),
    ),
)
def test_activation_must_leave_room_beyond_fee_adjusted_breakeven(
    side: Side,
    breakeven: Decimal,
) -> None:
    with pytest.raises(ValueError, match="활성화 가격"):
        TrailingStateMachine(
            account_id="MAIN:BASE",
            trade_id="trade-no-runner-room",
            strategy_id="TEST_V1",
            strategy_version="1.0.0",
            profile="BASE",
            symbol="BTCUSDT",
            side=side,
            entry_price=Decimal("100"),
            initial_stop=Decimal("99") if side is Side.LONG else Decimal("101"),
            fee_adjusted_breakeven=breakeven,
            original_quantity=Decimal("1"),
            policy=policy(),
        )


def observation(
    event_id: str,
    ts_ms: int,
    *,
    bid: str,
    ask: str,
    remaining: str = "1",
    realized: str = "0",
    unrealized: str = "0",
    stale: bool = False,
    sequence_valid: bool = True,
    data_health: str = "HEALTHY",
) -> TrailingObservation:
    return TrailingObservation(
        event_id=event_id,
        event_time_ms=ts_ms,
        receive_time_ms=ts_ms + 1,
        best_bid=Decimal(bid),
        best_ask=Decimal(ask),
        sequence_valid=sequence_valid,
        stale=stale,
        data_health=data_health,
        remaining_quantity=Decimal(remaining),
        realized_quantity=Decimal(realized),
        current_unrealized=Decimal(unrealized),
    )


def test_long_partial_runner_uses_favorable_bid_and_monotonic_trail() -> None:
    state = machine()

    armed = state.observe(observation("book-1", 2_000, bid="101.5", ask="101.6", unrealized="1.5"))
    assert armed.state is TrailingState.TRAIL_ARMED
    assert state.highest_favorable_bid == Decimal("101.5")
    assert state.lowest_favorable_ask is None
    assert state.current_trail == Decimal("100.485")

    state.mark_partial_tp_pending(
        event_time_ms=2_100,
        receive_time_ms=2_101,
        data_health="HEALTHY",
    )
    state.mark_partial_tp_filled(
        event_time_ms=2_350,
        receive_time_ms=2_351,
        realized_quantity=Decimal("0.4"),
        remaining_quantity=Decimal("0.6"),
        target_complete=True,
        data_health="HEALTHY",
    )
    assert state.state is TrailingState.RUNNER_ACTIVE
    assert state.runner_started_ts_ms == 2_350

    state.observe(
        observation(
            "book-2",
            3_000,
            bid="103",
            ask="103.1",
            remaining="0.6",
            realized="0.4",
            unrealized="2.2",
        )
    )
    assert state.current_trail == Decimal("101.97")
    assert state.giveback == 0

    retrace = state.observe(
        observation(
            "book-3",
            3_500,
            bid="101.9",
            ask="102",
            remaining="0.6",
            realized="0.4",
            unrealized="1.1",
        )
    )
    assert retrace.trail_exit_triggered is True
    assert retrace.state is TrailingState.TRAIL_EXIT_PENDING
    assert state.current_trail == Decimal("101.97")
    assert state.giveback == Decimal("1.1")


def test_short_runner_uses_favorable_ask_and_never_raises_trail() -> None:
    state = machine(side=Side.SHORT, partial=False)

    state.observe(observation("book-1", 2_000, bid="98.4", ask="98.5"))
    first_trail = state.current_trail
    assert state.state is TrailingState.RUNNER_ACTIVE
    assert state.runner_started_ts_ms == 2_000
    assert state.lowest_favorable_ask == Decimal("98.5")
    assert state.highest_favorable_bid is None

    state.observe(observation("book-2", 3_000, bid="96.9", ask="97"))
    assert first_trail is not None
    assert state.current_trail is not None
    assert state.current_trail < first_trail
    updated = state.current_trail

    decision = state.observe(observation("book-3", 4_000, bid="98.1", ask="98.2"))
    assert decision.trail_exit_triggered is True
    assert state.current_trail == updated


def test_duplicate_stale_invalid_and_out_of_order_books_cannot_move_favorable_mark() -> None:
    state = machine(partial=False)
    state.observe(observation("book-1", 2_000, bid="101.5", ask="101.6"))
    favorable = state.highest_favorable_bid
    trail = state.current_trail

    duplicate = state.observe(observation("book-1", 2_000, bid="110", ask="110.1"))
    stale = state.observe(observation("book-stale", 2_100, bid="111", ask="111.1", stale=True))
    invalid = state.observe(
        observation(
            "book-invalid",
            2_200,
            bid="112",
            ask="112.1",
            sequence_valid=False,
        )
    )
    out_of_order = state.observe(observation("book-old", 1_999, bid="113", ask="113.1"))

    assert duplicate.ignore_reason == "DUPLICATE_EVENT"
    assert stale.ignore_reason == "STALE_EVENT"
    assert invalid.ignore_reason == "SEQUENCE_INVALID_BOOK"
    assert out_of_order.ignore_reason == "OUT_OF_ORDER_EVENT"
    assert state.highest_favorable_bid == favorable
    assert state.current_trail == trail


def test_non_adjacent_duplicate_event_is_rejected_even_with_newer_timestamp() -> None:
    state = machine(partial=False)
    state.observe(observation("book-1", 2_000, bid="101.5", ask="101.6"))
    state.observe(observation("book-2", 2_100, bid="102", ask="102.1"))
    favorable = state.highest_favorable_bid
    trail = state.current_trail

    duplicate = state.observe(observation("book-1", 2_200, bid="110", ask="110.1"))

    assert duplicate.ignore_reason == "DUPLICATE_EVENT"
    assert state.highest_favorable_bid == favorable
    assert state.current_trail == trail


def test_rejected_trail_exit_returns_to_runner_and_recovery_checksum_is_stable() -> None:
    state = machine(partial=False)
    state.observe(observation("book-1", 2_000, bid="103", ask="103.1"))
    state.observe(observation("book-2", 3_000, bid="101.9", ask="102"))
    assert state.state is TrailingState.TRAIL_EXIT_PENDING

    state.mark_exit_rejected(
        event_time_ms=3_250,
        receive_time_ms=3_251,
        data_health="HEALTHY",
    )
    assert state.state is TrailingState.RUNNER_ACTIVE
    assert state.current_trail == Decimal("101.97")

    payload = state.to_payload()
    restored = TrailingStateMachine.from_payload(payload)

    assert restored.to_payload() == payload
    assert restored.checksum() == state.checksum()
    assert [row.to_state for row in restored.transitions] == [
        TrailingState.INITIAL_PROTECTION,
        TrailingState.PROFIT_ACTIVATION_PENDING,
        TrailingState.TRAIL_ARMED,
        TrailingState.RUNNER_ACTIVE,
        TrailingState.TRAIL_EXIT_PENDING,
        TrailingState.RUNNER_ACTIVE,
    ]


def test_tp1_triggered_activation_does_not_arm_from_price_alone() -> None:
    state = machine()
    state.policy = TrailingPolicy(
        policy_id="TP1_TRIGGERED_V1",
        model=TrailingModel.FIXED_RATE,
        activation_rule=TrailingActivationRule.TP1_TRIGGERED,
        activation_r=Decimal("1"),
        partial_tp_required=True,
        retracement_rate=Decimal("0.01"),
    )

    state.observe(observation("book-1", 2_000, bid="105", ask="105.1"))
    assert state.state is TrailingState.PROFIT_ACTIVATION_PENDING

    state.mark_partial_tp_pending(
        event_time_ms=2_100,
        receive_time_ms=2_101,
        data_health="HEALTHY",
    )
    assert state.state is TrailingState.PARTIAL_TP_PENDING


def test_r_multiple_policy_does_not_arm_from_tp1_before_activation() -> None:
    state = machine()

    state.mark_partial_tp_pending(
        event_time_ms=1_500,
        receive_time_ms=1_501,
        data_health="HEALTHY",
    )

    assert state.state is TrailingState.PROFIT_ACTIVATION_PENDING
    assert state.activation_ts_ms is None


def test_recovery_rejects_tampered_transition_identity_and_checksum() -> None:
    state = machine(partial=False)
    state.observe(observation("book-1", 2_000, bid="102", ask="102.1"))
    payload = state.to_payload()
    transitions = payload["transitions"]
    assert isinstance(transitions, list)
    assert isinstance(transitions[0], dict)
    transitions[0]["transition_id"] = "trail-tampered"

    try:
        TrailingStateMachine.from_payload(payload)
    except ValueError as error:
        assert "checksum" in str(error)
    else:
        raise AssertionError("변조된 trailing transition 복구가 거부돼야 합니다.")


def test_chandelier_structure_uses_completed_structure_without_widening() -> None:
    state = TrailingStateMachine(
        account_id="STRATEGY:BASE",
        trade_id="trade-structure",
        strategy_id="STRATEGY",
        strategy_version="V1",
        profile="BASE",
        symbol="BTCUSDT",
        side=Side.LONG,
        entry_price=Decimal("100"),
        initial_stop=Decimal("99"),
        fee_adjusted_breakeven=Decimal("100.13"),
        original_quantity=Decimal("1"),
        policy=TrailingPolicy(
            policy_id="STRUCTURE_V1",
            model=TrailingModel.CHANDELIER_STRUCTURE,
            activation_rule=TrailingActivationRule.R_MULTIPLE,
            activation_r=Decimal("1"),
            partial_tp_required=False,
            atr_multiplier=Decimal("2"),
        ),
    )
    state.confirm_entry(event_time_ms=1_000, receive_time_ms=1_001)

    state.observe(
        TrailingObservation(
            event_id="book-1",
            event_time_ms=2_000,
            receive_time_ms=2_001,
            best_bid=Decimal("103"),
            best_ask=Decimal("103.1"),
            sequence_valid=True,
            stale=False,
            data_health="HEALTHY",
            remaining_quantity=Decimal("1"),
            realized_quantity=Decimal("0"),
            current_unrealized=Decimal("3"),
            atr=Decimal("1"),
            completed_structure_stop=Decimal("101.5"),
        )
    )
    assert state.current_trail == Decimal("101.5")

    state.observe(
        TrailingObservation(
            event_id="book-2",
            event_time_ms=3_000,
            receive_time_ms=3_001,
            best_bid=Decimal("103.2"),
            best_ask=Decimal("103.3"),
            sequence_valid=True,
            stale=False,
            data_health="HEALTHY",
            remaining_quantity=Decimal("1"),
            realized_quantity=Decimal("0"),
            current_unrealized=Decimal("3.2"),
            atr=Decimal("1.2"),
            completed_structure_stop=Decimal("101.2"),
        )
    )
    assert state.current_trail == Decimal("101.5")


def test_completed_candle_reference_freezes_atr_and_side_specific_structure() -> None:
    candles = tuple(
        Candle(
            symbol="BTCUSDT",
            interval_seconds=60,
            open_ts_ms=index * 60_000,
            open=Decimal(str(100 + index)),
            high=Decimal(str(102 + index)),
            low=Decimal(str(99 + index)),
            close=Decimal(str(101 + index)),
            volume=Decimal("10"),
            trade_count=5,
        )
        for index in range(4)
    )

    long_reference = trailing_reference_from_completed_candles(
        candles,
        side=Side.LONG,
        as_of_ts_ms=240_000,
        atr_period=3,
        structure_lookback=2,
    )
    short_reference = trailing_reference_from_completed_candles(
        candles,
        side=Side.SHORT,
        as_of_ts_ms=240_000,
        atr_period=3,
        structure_lookback=2,
    )

    assert long_reference.reference_ts_ms == 240_000
    assert long_reference.atr == Decimal("3")
    assert long_reference.completed_structure_stop == Decimal("101")
    assert short_reference.completed_structure_stop == Decimal("105")


def test_completed_candle_reference_rejects_incomplete_gap_and_short_lookback() -> None:
    candles = tuple(
        Candle(
            symbol="BTCUSDT",
            interval_seconds=60,
            open_ts_ms=index * 60_000,
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("10"),
            trade_count=5,
        )
        for index in range(4)
    )

    with pytest.raises(ValueError, match="완료되지 않은 미래봉"):
        trailing_reference_from_completed_candles(
            candles,
            side=Side.LONG,
            as_of_ts_ms=239_999,
            atr_period=3,
            structure_lookback=2,
        )
    with pytest.raises(ValueError, match="누락된 시간구간"):
        trailing_reference_from_completed_candles(
            (*candles[:2], replace(candles[2], open_ts_ms=180_000)),
            side=Side.LONG,
            as_of_ts_ms=240_000,
            atr_period=2,
            structure_lookback=2,
        )
    with pytest.raises(ValueError, match="필요한 완성봉이 부족"):
        trailing_reference_from_completed_candles(
            candles,
            side=Side.LONG,
            as_of_ts_ms=240_000,
            atr_period=3,
            structure_lookback=5,
        )


def test_edge_adaptive_trail_only_narrows_after_precomputed_adverse_state() -> None:
    state = TrailingStateMachine(
        account_id="STRATEGY:BASE",
        trade_id="trade-adaptive",
        strategy_id="STRATEGY",
        strategy_version="V1",
        profile="BASE",
        symbol="BTCUSDT",
        side=Side.LONG,
        entry_price=Decimal("100"),
        initial_stop=Decimal("99"),
        fee_adjusted_breakeven=Decimal("100.13"),
        original_quantity=Decimal("1"),
        policy=TrailingPolicy(
            policy_id="EDGE_ADAPTIVE_V1",
            model=TrailingModel.EDGE_ADAPTIVE,
            activation_rule=TrailingActivationRule.R_MULTIPLE,
            activation_r=Decimal("1"),
            partial_tp_required=False,
            atr_multiplier=Decimal("2"),
            adverse_atr_multiplier=Decimal("1.2"),
        ),
    )
    state.confirm_entry(event_time_ms=1_000, receive_time_ms=1_001)
    state.observe(
        replace(
            observation("book-1", 2_000, bid="102", ask="102.1"),
            atr=Decimal("1"),
        )
    )
    assert state.current_trail == Decimal("100.13")

    state.observe(
        replace(
            observation("book-2", 3_000, bid="103", ask="103.1"),
            atr=Decimal("1"),
            adverse_edge=True,
        )
    )
    assert state.current_trail == Decimal("101.8")


def test_recovery_rejects_string_boolean_and_invalid_transition_reasons() -> None:
    state = machine(partial=False)
    state.observe(observation("book-1", 2_000, bid="102", ask="102.1"))

    boolean_payload = state.to_payload()
    assert isinstance(boolean_payload["policy"], dict)
    boolean_payload["policy"]["partial_tp_required"] = "false"
    with pytest.raises(ValueError, match="boolean"):
        TrailingStateMachine.from_payload(boolean_payload)

    reason_payload = state.to_payload()
    assert isinstance(reason_payload["transitions"], list)
    reason_payload["transitions"][0]["reason_codes"] = "NOT_A_LIST"
    with pytest.raises(ValueError, match="reason code"):
        TrailingStateMachine.from_payload(reason_payload)
