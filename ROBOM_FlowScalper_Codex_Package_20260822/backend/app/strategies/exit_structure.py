"""완료봉과 과거 공개시장 경로만으로 전략별 손절·익절 근거를 확정한다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from backend.app.domain.models import Side
from backend.app.execution.trailing import trailing_reference_from_completed_candles
from backend.app.features import FeatureSnapshot
from backend.app.market_data import Candle
from backend.app.strategies.base import (
    PlanInputs,
    RunnerManagement,
    StructuralExitPlan,
)
from backend.app.strategies.intraday_trend import IntradayTrendVariant


@dataclass(frozen=True, slots=True)
class _PriceLevel:
    price: Decimal
    rationale_ko: str


def compression_breakout_exit_plan(
    history: Sequence[FeatureSnapshot],
    snapshot: FeatureSnapshot,
    side: Side,
    *,
    tick_size: Decimal,
    expected_cost_bps: Decimal,
    maximum_path_seconds: int = 10,
) -> PlanInputs:
    """실제 충격·눌림·재가속 경로에서 CBR 보호선과 측정 목표를 만든다."""

    entry = Decimal(str(snapshot.mid))
    window_start_ms = snapshot.ts_ms - maximum_path_seconds * 1_000
    window = sorted(
        (row for row in history if window_start_ms <= row.ts_ms < snapshot.ts_ms and row.mid > 0),
        key=lambda row: row.ts_ms,
    )
    window.append(snapshot)
    if len(window) < 6:
        return _missing_structure(entry, expected_cost_bps)
    direction = Decimal(1) if side is Side.LONG else Decimal(-1)
    directional = [Decimal(str(row.mid)) * direction for row in window]
    peak_index = max(range(len(directional) - 2), key=directional.__getitem__)
    if peak_index < 2 or peak_index >= len(directional) - 2:
        return _missing_structure(entry, expected_cost_bps)
    impulse_origin = min(directional[: peak_index + 1])
    impulse_peak = directional[peak_index]
    impulse_distance = impulse_peak - impulse_origin
    post_peak = directional[peak_index + 1 :]
    trough_offset = min(range(len(post_peak)), key=post_peak.__getitem__)
    trough_index = peak_index + 1 + trough_offset
    if impulse_distance <= 0 or trough_index >= len(directional) - 1:
        return _missing_structure(entry, expected_cost_bps)
    pullback_trough = directional[trough_index]
    pullback_distance = impulse_peak - pullback_trough
    if pullback_distance <= 0 or directional[-1] <= pullback_trough:
        return _missing_structure(entry, expected_cost_bps)
    spread_buffer = entry * Decimal(str(snapshot.spread_bps)) / Decimal(10_000)
    buffer = max(
        tick_size * Decimal(2),
        spread_buffer * Decimal("1.5"),
        impulse_distance * Decimal("0.05"),
    )
    stop = (pullback_trough - buffer) * direction
    tp1 = impulse_peak * direction
    tp2 = (impulse_peak + impulse_distance) * direction
    stop = _round_price(stop, tick_size)
    tp1 = _round_price(tp1, tick_size)
    tp2 = _round_price(tp2, tick_size)
    minimum_reward = _minimum_reward(entry, expected_cost_bps, tick_size)
    if not _valid_target_order(side, entry, stop, tp1, tp2, minimum_reward):
        return _missing_structure(entry, expected_cost_bps)
    trailing_distance = _round_price(max(pullback_distance, buffer * Decimal(2)), tick_size)
    structure = StructuralExitPlan(
        take_profit_1=tp1,
        take_profit_2=tp2,
        stop_rationale_ko=(
            "최근 10초 돌파 뒤 실제 눌림 저점·고점 바깥에 호가차와 충격폭 완충을 더했습니다."
        ),
        take_profit_1_rationale_ko="눌림 전에 확인된 실제 돌파 충격의 이전 고점·저점입니다.",
        take_profit_2_rationale_ko=(
            "압축 뒤 확인된 첫 충격폭을 돌파점에서 한 번 더 투영한 측정 목표입니다."
        ),
        reference_timeframes_ko=("최근 10초 실거래 경로", "현재 공개 bid·ask"),
        runner_management=RunnerManagement.TP1_STRUCTURE_DISTANCE,
        trailing_distance=trailing_distance,
    )
    return PlanInputs(
        entry=entry,
        structural_stop=stop,
        target=tp2,
        expected_total_cost_bps=expected_cost_bps,
        structural_exit=structure,
    )


def vwap_reversion_exit_plan(
    history: Sequence[FeatureSnapshot],
    snapshot: FeatureSnapshot,
    side: Side,
    *,
    tick_size: Decimal,
    expected_cost_bps: Decimal,
    maximum_path_seconds: int = 120,
) -> PlanInputs:
    """이탈 전 실제 범위와 이탈 극값으로 평균복귀 계획을 만든다."""

    entry = Decimal(str(snapshot.mid))
    window_start_ms = snapshot.ts_ms - maximum_path_seconds * 1_000
    prices = [
        Decimal(str(row.mid))
        for row in sorted(history, key=lambda item: item.ts_ms)
        if window_start_ms <= row.ts_ms < snapshot.ts_ms and row.mid > 0
    ]
    prices.append(entry)
    if len(prices) < 20:
        return _missing_structure(entry, expected_cost_bps)
    direction = Decimal(1) if side is Side.LONG else Decimal(-1)
    minimum_reward = _minimum_reward(entry, expected_cost_bps, tick_size)
    historical_prices = prices[:-1]
    extreme_index = (
        min(range(len(historical_prices)), key=historical_prices.__getitem__)
        if side is Side.LONG
        else max(range(len(historical_prices)), key=historical_prices.__getitem__)
    )
    pre_excursion_prices = historical_prices[:extreme_index]
    if len(pre_excursion_prices) < 5:
        return _missing_structure(entry, expected_cost_bps)
    excursion_extreme = historical_prices[extreme_index]
    if (entry - excursion_extreme) * direction <= tick_size * Decimal(2):
        return _missing_structure(entry, expected_cost_bps)
    ordered_pre_excursion = sorted(pre_excursion_prices)
    center_index = len(ordered_pre_excursion) // 2
    if len(ordered_pre_excursion) % 2:
        range_center = ordered_pre_excursion[center_index]
    else:
        range_center = (
            ordered_pre_excursion[center_index - 1] + ordered_pre_excursion[center_index]
        ) / Decimal(2)
    if not _favorable(side, entry, range_center, minimum_reward):
        return _missing_structure(entry, expected_cost_bps)
    excursion = abs(range_center - excursion_extreme)
    spread_buffer = entry * Decimal(str(snapshot.spread_bps)) / Decimal(10_000)
    buffer = max(
        tick_size * Decimal(2),
        spread_buffer * Decimal("1.5"),
        excursion * Decimal("0.08"),
    )
    stop = excursion_extreme - direction * buffer
    stop = _round_price(stop, tick_size)
    levels = [
        _PriceLevel(
            _round_price(range_center, tick_size),
            "과도이탈 전에 실제 거래가 모였던 최근 2분 가격대의 중앙값입니다.",
        )
    ]
    pivot = _nearest_micro_pivot(
        pre_excursion_prices,
        side,
        range_center,
        minimum_reward,
    )
    if pivot is not None:
        levels.append(
            _PriceLevel(
                _round_price(pivot, tick_size),
                "과도이탈 전에 실제로 확인된 반대편 미세 고점·저점입니다.",
            )
        )
    range_edge = max(pre_excursion_prices) if side is Side.LONG else min(pre_excursion_prices)
    levels.append(
        _PriceLevel(
            _round_price(range_edge, tick_size),
            "과도이탈 전 최근 2분 가격대의 반대편 실제 경계입니다.",
        )
    )
    levels.append(
        _PriceLevel(
            _round_price(range_center + direction * excursion, tick_size),
            "과도이탈 전 가격대 중앙에서 실제 이탈폭을 반대편에 투영한 범위 목표입니다.",
        )
    )
    selected = _nearest_distinct_levels(levels, side, entry)
    if not selected:
        return _missing_structure(entry, expected_cost_bps, stop=stop)
    expected_cost = entry * expected_cost_bps / Decimal(10_000)
    minimum_final_distance = expected_cost + Decimal("1.20") * (abs(entry - stop) + expected_cost)
    tp1 = selected[0]
    tp2 = next(
        (level for level in selected[1:] if abs(level.price - entry) >= minimum_final_distance),
        None,
    )
    if tp2 is None:
        return _missing_structure(entry, expected_cost_bps, stop=stop)
    if not _valid_target_order(
        side,
        entry,
        stop,
        tp1.price,
        tp2.price,
        minimum_reward,
    ):
        return _missing_structure(entry, expected_cost_bps)
    structure = StructuralExitPlan(
        take_profit_1=tp1.price,
        take_profit_2=tp2.price,
        stop_rationale_ko="최근 2분 과도이탈의 실제 극값 바깥에 호가차와 이탈폭 완충을 더했습니다.",
        take_profit_1_rationale_ko=tp1.rationale_ko,
        take_profit_2_rationale_ko=tp2.rationale_ko,
        reference_timeframes_ko=(
            "최근 2분 실거래 경로",
            "10초 micro-VWAP 재진입 확인",
            "현재 공개 bid·ask",
        ),
        runner_management=RunnerManagement.FIXED_SECOND_TARGET,
    )
    return PlanInputs(
        entry=entry,
        structural_stop=stop,
        target=tp2.price,
        expected_total_cost_bps=expected_cost_bps,
        structural_exit=structure,
    )


def intraday_structural_exit_plan(
    *,
    side: Side,
    entry: Decimal,
    structural_stop: Decimal | None,
    expected_cost_bps: Decimal,
    tick_size: Decimal,
    signal_ts_ms: int,
    base_candles: Sequence[Candle],
    hourly_candles: Sequence[Candle],
    variant: IntradayTrendVariant,
) -> PlanInputs:
    """완성 15·30분과 1시간 가격대에서 중단기 TP1·TP2·runner를 확정한다."""

    if structural_stop is None:
        return _missing_structure(entry, expected_cost_bps)
    interval_seconds = 900 if "15M" in variant.value else 1_800
    eligible_base = _completed_rows(base_candles, interval_seconds, signal_ts_ms)
    eligible_hourly = _completed_rows(hourly_candles, 3_600, signal_ts_ms)
    if len(eligible_base) < 40 or len(eligible_hourly) < 24:
        return _missing_structure(entry, expected_cost_bps, stop=structural_stop)
    try:
        trailing = trailing_reference_from_completed_candles(
            eligible_base,
            side=side,
            as_of_ts_ms=signal_ts_ms,
        )
    except ValueError:
        return _missing_structure(entry, expected_cost_bps, stop=structural_stop)
    minimum_reward = _minimum_reward(entry, expected_cost_bps, tick_size)
    levels = [
        *_confirmed_pivot_levels(
            eligible_base[-120:], side, entry, minimum_reward, interval_seconds
        ),
        *_confirmed_pivot_levels(eligible_hourly[-120:], side, entry, minimum_reward, 3_600),
        *_previous_day_levels(eligible_hourly, side, entry, minimum_reward),
    ]
    if variant in {
        IntradayTrendVariant.BREAKOUT_RETEST_15M,
        IntradayTrendVariant.BREAKOUT_RETEST_30M,
    }:
        lookback = 32 if interval_seconds == 900 else 24
        setup_rows = eligible_base[-(lookback + 2) : -2]
        if len(setup_rows) == lookback:
            setup_high = max(row.high for row in setup_rows)
            setup_low = min(row.low for row in setup_rows)
            width = setup_high - setup_low
            anchor = setup_high if side is Side.LONG else setup_low
            direction = Decimal(1) if side is Side.LONG else Decimal(-1)
            for fraction, label in (
                (Decimal("0.5"), "돌파 전 완성봉 범위의 절반 측정폭"),
                (Decimal(1), "돌파 전 완성봉 전체 측정폭"),
            ):
                price = _round_price(anchor + direction * width * fraction, tick_size)
                if _favorable(side, entry, price, minimum_reward):
                    levels.append(_PriceLevel(price, label))
    direction = Decimal(1) if side is Side.LONG else Decimal(-1)
    for multiplier in (Decimal(1), Decimal(2), Decimal(3), Decimal(4)):
        label = f"완성 {interval_seconds // 60}분봉 {multiplier} ATR 추세 채널"
        price = _round_price(entry + direction * trailing.atr * multiplier, tick_size)
        if _favorable(side, entry, price, minimum_reward):
            levels.append(_PriceLevel(price, label))
    selected = _nearest_distinct_levels(levels, side, entry)
    if not selected:
        return _missing_structure(entry, expected_cost_bps, stop=structural_stop)
    rounded_stop = _round_price(structural_stop, tick_size)
    expected_cost = entry * expected_cost_bps / Decimal(10_000)
    minimum_final_distance = expected_cost + Decimal("1.20") * (
        abs(entry - rounded_stop) + expected_cost
    )
    tp1 = selected[0]
    tp2 = next(
        (level for level in selected[1:] if abs(level.price - entry) >= minimum_final_distance),
        None,
    )
    if tp2 is None:
        return _missing_structure(entry, expected_cost_bps, stop=rounded_stop)
    if not _valid_target_order(
        side,
        entry,
        rounded_stop,
        tp1.price,
        tp2.price,
        minimum_reward,
    ):
        return _missing_structure(entry, expected_cost_bps, stop=rounded_stop)
    stop_reason = (
        "돌파 기준선과 재확인 완료봉 바깥에 ATR 완충을 둔 초기 보호선입니다."
        if variant
        in {
            IntradayTrendVariant.BREAKOUT_RETEST_15M,
            IntradayTrendVariant.BREAKOUT_RETEST_30M,
        }
        else "EMA 조정 뒤 회복을 만든 최근 완성봉 저점·고점 바깥에 ATR 완충을 둔 보호선입니다."
    )
    structure = StructuralExitPlan(
        take_profit_1=tp1.price,
        take_profit_2=tp2.price,
        stop_rationale_ko=stop_reason,
        take_profit_1_rationale_ko=tp1.rationale_ko,
        take_profit_2_rationale_ko=tp2.rationale_ko,
        reference_timeframes_ko=(
            f"완성 {interval_seconds // 60}분봉",
            "완성 1시간봉",
            "현재 공개 bid·ask",
        ),
        runner_management=RunnerManagement.TP1_ATR_CHANDELIER,
        trailing_atr=trailing.atr,
        trailing_reference_ts_ms=trailing.reference_ts_ms,
        trailing_reference_interval_seconds=trailing.interval_seconds,
    )
    return PlanInputs(
        entry=entry,
        structural_stop=rounded_stop,
        target=tp2.price,
        expected_total_cost_bps=expected_cost_bps,
        structural_exit=structure,
    )


def _missing_structure(
    entry: Decimal,
    expected_cost_bps: Decimal,
    *,
    stop: Decimal | None = None,
) -> PlanInputs:
    return PlanInputs(
        entry=entry,
        structural_stop=stop,
        target=None,
        expected_total_cost_bps=expected_cost_bps,
    )


def _minimum_reward(entry: Decimal, expected_cost_bps: Decimal, tick_size: Decimal) -> Decimal:
    return max(entry * expected_cost_bps / Decimal(10_000) * Decimal(2), tick_size * Decimal(2))


def _round_price(value: Decimal, tick_size: Decimal) -> Decimal:
    if tick_size <= 0:
        raise ValueError("tick size는 양수여야 합니다.")
    ticks = (value / tick_size).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return ticks * tick_size


def _valid_target_order(
    side: Side,
    entry: Decimal,
    stop: Decimal,
    tp1: Decimal,
    tp2: Decimal,
    minimum_reward: Decimal,
) -> bool:
    if min(entry, stop, tp1, tp2) <= 0:
        return False
    if side is Side.LONG:
        return stop < entry and entry + minimum_reward < tp1 < tp2
    return tp2 < tp1 < entry - minimum_reward and entry < stop


def _favorable(side: Side, entry: Decimal, price: Decimal, minimum_reward: Decimal) -> bool:
    return price > entry + minimum_reward if side is Side.LONG else price < entry - minimum_reward


def _nearest_micro_pivot(
    prices: Sequence[Decimal],
    side: Side,
    center: Decimal,
    minimum_reward: Decimal,
) -> Decimal | None:
    pivots: list[Decimal] = []
    for index in range(2, len(prices) - 2):
        window = prices[index - 2 : index + 3]
        current = prices[index]
        if side is Side.LONG and current == max(window) and current > center + minimum_reward:
            pivots.append(current)
        if side is Side.SHORT and current == min(window) and current < center - minimum_reward:
            pivots.append(current)
    if not pivots:
        return None
    return min(pivots) if side is Side.LONG else max(pivots)


def _completed_rows(
    candles: Sequence[Candle],
    interval_seconds: int,
    as_of_ts_ms: int,
) -> tuple[Candle, ...]:
    ordered = tuple(
        sorted(
            (
                row
                for row in candles
                if row.interval_seconds == interval_seconds
                and row.open_ts_ms + interval_seconds * 1_000 <= as_of_ts_ms
            ),
            key=lambda row: row.open_ts_ms,
        )
    )
    if len({row.open_ts_ms for row in ordered}) != len(ordered):
        return ()
    recent = ordered[-120:]
    if any(
        current.open_ts_ms - previous.open_ts_ms != interval_seconds * 1_000
        for previous, current in zip(recent, recent[1:], strict=False)
    ):
        return ()
    return ordered


def _confirmed_pivot_levels(
    candles: Sequence[Candle],
    side: Side,
    entry: Decimal,
    minimum_reward: Decimal,
    interval_seconds: int,
) -> list[_PriceLevel]:
    levels: list[_PriceLevel] = []
    label = f"완성 {interval_seconds // 60}분봉 확정 피벗"
    for index in range(2, len(candles) - 2):
        window = candles[index - 2 : index + 3]
        current = candles[index]
        price = current.high if side is Side.LONG else current.low
        boundary = (
            max(row.high for row in window) if side is Side.LONG else min(row.low for row in window)
        )
        if price == boundary and _favorable(side, entry, price, minimum_reward):
            levels.append(_PriceLevel(price, label))
    return levels


def _previous_day_levels(
    candles: Sequence[Candle],
    side: Side,
    entry: Decimal,
    minimum_reward: Decimal,
) -> list[_PriceLevel]:
    if not candles:
        return []
    latest_day = candles[-1].open_ts_ms // 86_400_000
    previous = [row for row in candles if row.open_ts_ms // 86_400_000 == latest_day - 1]
    if len(previous) < 20:
        return []
    high = max(row.high for row in previous)
    low = min(row.low for row in previous)
    price = high if side is Side.LONG else low
    return (
        [_PriceLevel(price, "이전 UTC 일봉의 실제 고가·저가")]
        if _favorable(side, entry, price, minimum_reward)
        else []
    )


def _nearest_distinct_levels(
    levels: Sequence[_PriceLevel],
    side: Side,
    entry: Decimal,
) -> list[_PriceLevel]:
    by_price: dict[Decimal, _PriceLevel] = {}
    for level in levels:
        by_price.setdefault(level.price, level)
    return (
        sorted(
            by_price.values(),
            key=lambda level: abs(level.price - entry),
        )
        if side in {Side.LONG, Side.SHORT}
        else []
    )
