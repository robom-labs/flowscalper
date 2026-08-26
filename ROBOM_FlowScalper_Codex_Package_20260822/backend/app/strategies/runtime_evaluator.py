"""LIVE 피처를 A/B/C/D/E/F/G/H/I/J/K 전략 문맥으로 변환하고 실제 확인 시간을 보존한다."""

from __future__ import annotations

from bisect import bisect_left, insort
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal

from backend.app.domain.models import Side
from backend.app.features import FeatureSnapshot
from backend.app.market_data import Candle
from backend.app.regime import Regime
from backend.app.strategies.aggressor_flow import (
    AggressorFlowContext,
    AggressorFlowStrategy,
    aggressor_alignment_ready,
)
from backend.app.strategies.base import CandidateDecision, PlanInputs
from backend.app.strategies.book_slope_asymmetry import (
    BookSlopeAsymmetryContext,
    BookSlopeAsymmetryStrategy,
    book_slope_asymmetry_ready,
)
from backend.app.strategies.compression_breakout import (
    CompressionBreakoutContext,
    CompressionBreakoutStrategy,
)
from backend.app.strategies.depth_adjusted_ofi import (
    DepthAdjustedOfiContext,
    DepthAdjustedOfiStrategy,
    depth_adjusted_ofi_ready,
)
from backend.app.strategies.hourly_momentum_breakout import (
    HourlyMomentumBreakoutContext,
    HourlyMomentumBreakoutStrategy,
    HourlyMomentumState,
    hourly_momentum_state,
)
from backend.app.strategies.liquidity_sweep import (
    LiquiditySweepContext,
    LiquiditySweepStrategy,
)
from backend.app.strategies.multilevel_microprice import (
    MultilevelMicropriceContext,
    MultilevelMicropriceStrategy,
    multilevel_alignment_ready,
)
from backend.app.strategies.ofi_pullback import OfiPullbackContext, OfiPullbackStrategy
from backend.app.strategies.ofi_return_confluence import (
    OfiReturnConfluenceContext,
    OfiReturnConfluenceStrategy,
    ofi_return_confluence_ready,
)
from backend.app.strategies.queue_microprice import (
    QueueMicropriceContext,
    QueueMicropriceStrategy,
    queue_alignment_ready,
)
from backend.app.strategies.registry import ExitStyle, StrategyRegistry
from backend.app.strategies.statistics import (
    robust_z_from_sorted,
    rolling_percentile_from_sorted,
)
from backend.app.strategies.vwap_exhaustion import (
    VwapExhaustionContext,
    VwapExhaustionStrategy,
)


@dataclass(frozen=True, slots=True)
class EvaluatedSignal:
    symbol: str
    regime: Regime
    decision: CandidateDecision
    main_eligible: bool
    shadow_eligible: bool


@dataclass(frozen=True, slots=True)
class _HistoryStatistics:
    """한 snapshot의 전략별 평가에서 공통으로 사용하는 강건 통계다."""

    flow_z: float
    deviation_z: float
    price_response_percentile: float
    compression_percentile: float
    efficiency_percentile: float
    long_directional_flow_z: float
    short_directional_flow_z: float
    long_depth_adjusted_ofi_z: float
    short_depth_adjusted_ofi_z: float
    bid_slope_percentile: float
    ask_slope_percentile: float
    history_sample_count: int


@dataclass(frozen=True, slots=True)
class _PullbackMetrics:
    """현재 시점 이전 가격 경로에서 계산한 실제 눌림과 재가속이다."""

    duration_seconds: float
    maximum_retrace_fraction: float
    price_reaccelerated: bool


def _vwap_deviation_bps(snapshot: FeatureSnapshot) -> float | None:
    if snapshot.mid <= 0:
        return None
    return (snapshot.mid - snapshot.micro_vwap_10s) / snapshot.mid * 10_000


@dataclass(slots=True)
class _SortedFeatureHistory:
    """동일 10분 과거창의 통계 입력을 결정적으로 정렬 보존한다."""

    flow: list[float] = field(default_factory=list)
    deviation: list[float] = field(default_factory=list)
    price_response: list[float] = field(default_factory=list)
    compression: list[float] = field(default_factory=list)
    efficiency: list[float] = field(default_factory=list)
    signed_notional: list[float] = field(default_factory=list)
    depth_adjusted_ofi: list[float] = field(default_factory=list)
    bid_book_slope: list[float] = field(default_factory=list)
    ask_book_slope: list[float] = field(default_factory=list)

    def add(self, snapshot: FeatureSnapshot) -> None:
        insort(self.flow, abs(snapshot.signed_notional_3s))
        deviation = _vwap_deviation_bps(snapshot)
        if deviation is not None:
            insort(self.deviation, abs(deviation))
        insort(self.price_response, snapshot.price_response_efficiency)
        insort(self.compression, snapshot.compression_ratio)
        insort(self.efficiency, snapshot.efficiency_ratio_30s)
        insort(self.signed_notional, snapshot.signed_notional_3s)
        insort(self.depth_adjusted_ofi, snapshot.depth_adjusted_ofi_3s_bps)
        insort(self.bid_book_slope, snapshot.bid_book_slope_10)
        insort(self.ask_book_slope, snapshot.ask_book_slope_10)

    def remove(self, snapshot: FeatureSnapshot) -> None:
        self._remove(self.flow, abs(snapshot.signed_notional_3s))
        deviation = _vwap_deviation_bps(snapshot)
        if deviation is not None:
            self._remove(self.deviation, abs(deviation))
        self._remove(self.price_response, snapshot.price_response_efficiency)
        self._remove(self.compression, snapshot.compression_ratio)
        self._remove(self.efficiency, snapshot.efficiency_ratio_30s)
        self._remove(self.signed_notional, snapshot.signed_notional_3s)
        self._remove(self.depth_adjusted_ofi, snapshot.depth_adjusted_ofi_3s_bps)
        self._remove(self.bid_book_slope, snapshot.bid_book_slope_10)
        self._remove(self.ask_book_slope, snapshot.ask_book_slope_10)

    @staticmethod
    def _remove(values: list[float], value: float) -> None:
        index = bisect_left(values, value)
        if index >= len(values) or values[index] != value:
            raise RuntimeError("정렬 전략 통계창이 원본 과거창과 일치하지 않습니다.")
        values.pop(index)


class StrategySignalEvaluator:
    """전략별 평가를 동일 snapshot과 동일 비용 가정에서 결정적으로 실행한다."""

    def __init__(self, history_limit: int = 1_200) -> None:
        if history_limit <= 0:
            raise ValueError("전략 과거창 크기는 양수여야 합니다.")
        self._history_limit = history_limit
        self._history: dict[str, deque[FeatureSnapshot]] = defaultdict(
            lambda: deque(maxlen=history_limit)
        )
        self._sorted_history: dict[str, _SortedFeatureHistory] = defaultdict(_SortedFeatureHistory)
        self._confirmation_started_ms: dict[tuple[str, str, Side], int] = {}
        self._hourly_state_cache: dict[str, tuple[int | None, HourlyMomentumState]] = {}

    def evaluate(
        self,
        registry: StrategyRegistry,
        snapshot: FeatureSnapshot,
        regime: Regime,
        *,
        tick_size: Decimal = Decimal("0.00000001"),
        hourly_candles: tuple[Candle, ...] = (),
    ) -> tuple[EvaluatedSignal, ...]:
        history_window = self._history[snapshot.symbol]
        history = list(history_window)
        sorted_history = self._sorted_history[snapshot.symbol]
        history_statistics = self._history_statistics(sorted_history, snapshot)
        trailing_return_3s_bps = _trailing_return_bps(history, snapshot)
        results: list[EvaluatedSignal] = []
        plans: dict[tuple[Side, ExitStyle], PlanInputs] = {}
        for strategy_id in registry.strategy_ids:
            descriptor = registry.descriptor(strategy_id)
            for side in Side:
                if not registry.evaluation_enabled(strategy_id, side):
                    continue
                plan_key = (side, descriptor.exit_style)
                plan = plans.get(plan_key)
                if plan is None:
                    plan = _plan(
                        snapshot,
                        side,
                        tick_size,
                        exit_style=descriptor.exit_style,
                    )
                    plans[plan_key] = plan
                decision = self._evaluate_one(
                    descriptor.evaluator,
                    side,
                    snapshot,
                    regime,
                    history,
                    history_statistics,
                    trailing_return_3s_bps,
                    plan,
                    hourly_candles,
                )
                results.append(
                    EvaluatedSignal(
                        symbol=snapshot.symbol,
                        regime=regime,
                        decision=decision,
                        main_eligible=registry.main_enabled(strategy_id, side),
                        shadow_eligible=registry.shadow_enabled(strategy_id, side),
                    )
                )
        if len(history_window) == self._history_limit:
            sorted_history.remove(history_window[0])
        history_window.append(snapshot)
        sorted_history.add(snapshot)
        return tuple(results)

    def _evaluate_one(
        self,
        evaluator: object,
        side: Side,
        snapshot: FeatureSnapshot,
        regime: Regime,
        history: list[FeatureSnapshot],
        history_statistics: _HistoryStatistics,
        trailing_return_3s_bps: float | None,
        plan: PlanInputs,
        hourly_candles: tuple[Candle, ...],
    ) -> CandidateDecision:
        deviation_bps = (snapshot.mid - snapshot.micro_vwap_10s) / snapshot.mid * 10_000
        direction = 1 if side is Side.LONG else -1
        ofi_short = snapshot.ofi_250ms * direction
        ofi_medium = snapshot.ofi_3s * direction
        trade_flow = snapshot.trade_imbalance_3s * direction
        microprice_alignment = (snapshot.microprice - snapshot.mid) * direction > 0
        if isinstance(evaluator, LiquiditySweepStrategy):
            supported_regime = regime not in {
                Regime.SHOCK,
                Regime.DEGRADED,
                Regime.WARMUP,
            }
            refill_ready = (
                snapshot.data_healthy and supported_regime and snapshot.refill_ratio >= 0.55
            )
            range_reentered = abs(deviation_bps) <= max(
                2.0,
                snapshot.spread_bps * 3,
            )
            sweep_context = LiquiditySweepContext(
                side=side,
                features=snapshot,
                regime=regime,
                plan=plan,
                sweep_extension_noise_units=abs(deviation_bps) / max(snapshot.spread_bps, 0.01),
                aggressive_flow_robust_z=history_statistics.flow_z,
                price_response_efficiency_quantile=(history_statistics.price_response_percentile),
                refill_persistence_ms=self._confirmation_ms(
                    f"{evaluator.strategy_id}:REFILL",
                    snapshot.symbol,
                    side,
                    snapshot.ts_ms,
                    aligned=refill_ready,
                ),
                reentry_confirmation_ms=self._confirmation_ms(
                    f"{evaluator.strategy_id}:REENTRY",
                    snapshot.symbol,
                    side,
                    snapshot.ts_ms,
                    aligned=snapshot.data_healthy and supported_regime and range_reentered,
                ),
                ofi_flip=ofi_short > 0 and ofi_medium < 0,
                microprice_reclaimed=microprice_alignment,
                range_reentered=range_reentered,
            )
            return evaluator.evaluate(sweep_context)
        if isinstance(evaluator, CompressionBreakoutStrategy):
            pullback = _pullback_metrics(
                history,
                snapshot,
                side,
                maximum_duration_seconds=10,
            )
            expected_regime = Regime.TREND_UP if side is Side.LONG else Regime.TREND_DOWN
            reacceleration_ready = (
                snapshot.data_healthy
                and regime is expected_regime
                and snapshot.spread_bps <= 12
                and pullback.price_reaccelerated
                and ofi_short > 0
                and ofi_medium > 0
                and trade_flow > 0.15
                and microprice_alignment
            )
            breakout_context = CompressionBreakoutContext(
                side=side,
                features=snapshot,
                regime=regime,
                plan=plan,
                compression_quantile=history_statistics.compression_percentile,
                breakout_confirmed=ofi_medium > 0 and trade_flow > 0.15,
                initial_impulse_extended=snapshot.realized_volatility_30s >= 0.0015,
                pullback_seconds=pullback.duration_seconds,
                pullback_retrace_fraction=pullback.maximum_retrace_fraction,
                counterflow_price_impact_weak=snapshot.price_response_efficiency <= 0.50,
                refill_recovered=snapshot.refill_ratio >= 0.50,
                ofi_reaccelerated=ofi_short > 0 and ofi_medium > 0,
                microprice_aligned=microprice_alignment,
                confirmation_ms=self._confirmation_ms(
                    f"{evaluator.strategy_id}:REACCELERATION",
                    snapshot.symbol,
                    side,
                    snapshot.ts_ms,
                    aligned=reacceleration_ready,
                ),
            )
            return evaluator.evaluate(breakout_context)
        if isinstance(evaluator, VwapExhaustionStrategy):
            excursion_valid = deviation_bps < 0 if side is Side.LONG else deviation_bps > 0
            structure_reentered = abs(deviation_bps) <= max(
                8.0,
                snapshot.spread_bps * 8,
            )
            vwap_context = VwapExhaustionContext(
                side=side,
                features=snapshot,
                regime=regime,
                plan=plan,
                vwap_deviation_robust_z=history_statistics.deviation_z,
                excursion_direction_valid=excursion_valid,
                aggressive_flow_robust_z=history_statistics.flow_z,
                price_progress_stalled=(history_statistics.price_response_percentile <= 0.30),
                opposite_depth_refilled=snapshot.refill_ratio >= 0.55,
                ofi_reversed=ofi_short > 0 and ofi_medium < 0,
                microprice_reversed=microprice_alignment,
                structure_reentered=structure_reentered,
                confirmation_ms=self._confirmation_ms(
                    f"{evaluator.strategy_id}:REENTRY",
                    snapshot.symbol,
                    side,
                    snapshot.ts_ms,
                    aligned=snapshot.data_healthy
                    and regime is Regime.RANGE
                    and structure_reentered
                    and microprice_alignment,
                ),
            )
            return evaluator.evaluate(vwap_context)
        if isinstance(evaluator, OfiPullbackStrategy):
            pullback = _pullback_metrics(
                history,
                snapshot,
                side,
                maximum_duration_seconds=15,
            )
            expected_regime = Regime.TREND_UP if side is Side.LONG else Regime.TREND_DOWN
            reacceleration_ready = (
                snapshot.data_healthy
                and regime is expected_regime
                and snapshot.spread_bps <= 12
                and pullback.price_reaccelerated
                and ofi_short > 0
                and ofi_medium > 0
                and trade_flow > 0.15
                and microprice_alignment
            )
            ofi_context = OfiPullbackContext(
                side=side,
                features=snapshot,
                regime=regime,
                plan=plan,
                multi_window_ofi_aligned=ofi_short > 0 and ofi_medium > 0,
                aggressive_trade_aligned=trade_flow > 0.15,
                microprice_aligned=microprice_alignment,
                price_efficiency_percentile=history_statistics.efficiency_percentile,
                pullback_seconds=pullback.duration_seconds,
                pullback_retrace_fraction=pullback.maximum_retrace_fraction,
                counterflow_price_impact_weak=snapshot.price_response_efficiency <= 0.50,
                original_flow_reaccelerated=(
                    pullback.price_reaccelerated and ofi_short > 0 and ofi_short >= ofi_medium * 0.1
                ),
                confirmation_ms=self._confirmation_ms(
                    f"{evaluator.strategy_id}:REACCELERATION",
                    snapshot.symbol,
                    side,
                    snapshot.ts_ms,
                    aligned=reacceleration_ready,
                ),
            )
            return evaluator.evaluate(ofi_context)
        if isinstance(evaluator, QueueMicropriceStrategy):
            aligned = queue_alignment_ready(side, snapshot, regime)
            confirmation_ms = self._confirmation_ms(
                evaluator.strategy_id,
                snapshot.symbol,
                side,
                snapshot.ts_ms,
                aligned=aligned,
            )
            return evaluator.evaluate(
                QueueMicropriceContext(
                    side=side,
                    features=snapshot,
                    regime=regime,
                    plan=plan,
                    confirmation_ms=confirmation_ms,
                )
            )
        if isinstance(evaluator, AggressorFlowStrategy):
            directional_flow_z = (
                history_statistics.long_directional_flow_z
                if side is Side.LONG
                else history_statistics.short_directional_flow_z
            )
            aligned = aggressor_alignment_ready(
                side,
                snapshot,
                regime,
                directional_flow_z,
            )
            confirmation_ms = self._confirmation_ms(
                evaluator.strategy_id,
                snapshot.symbol,
                side,
                snapshot.ts_ms,
                aligned=aligned,
            )
            return evaluator.evaluate(
                AggressorFlowContext(
                    side=side,
                    features=snapshot,
                    regime=regime,
                    plan=plan,
                    aggressive_signed_notional_robust_z=directional_flow_z,
                    confirmation_ms=confirmation_ms,
                )
            )
        if isinstance(evaluator, MultilevelMicropriceStrategy):
            aligned = multilevel_alignment_ready(side, snapshot, regime)
            confirmation_ms = self._confirmation_ms(
                evaluator.strategy_id,
                snapshot.symbol,
                side,
                snapshot.ts_ms,
                aligned=aligned,
            )
            return evaluator.evaluate(
                MultilevelMicropriceContext(
                    side=side,
                    features=snapshot,
                    regime=regime,
                    plan=plan,
                    confirmation_ms=confirmation_ms,
                )
            )
        if isinstance(evaluator, DepthAdjustedOfiStrategy):
            directional_depth_adjusted_ofi_z = (
                history_statistics.long_depth_adjusted_ofi_z
                if side is Side.LONG
                else history_statistics.short_depth_adjusted_ofi_z
            )
            aligned = depth_adjusted_ofi_ready(
                side,
                snapshot,
                regime,
                directional_depth_adjusted_ofi_z,
            )
            confirmation_ms = self._confirmation_ms(
                evaluator.strategy_id,
                snapshot.symbol,
                side,
                snapshot.ts_ms,
                aligned=aligned,
            )
            return evaluator.evaluate(
                DepthAdjustedOfiContext(
                    side=side,
                    features=snapshot,
                    regime=regime,
                    plan=plan,
                    directional_depth_adjusted_ofi_robust_z=(directional_depth_adjusted_ofi_z),
                    confirmation_ms=confirmation_ms,
                )
            )
        if isinstance(evaluator, OfiReturnConfluenceStrategy):
            directional_depth_adjusted_ofi_z = (
                history_statistics.long_depth_adjusted_ofi_z
                if side is Side.LONG
                else history_statistics.short_depth_adjusted_ofi_z
            )
            aligned = ofi_return_confluence_ready(
                side,
                snapshot,
                regime,
                directional_depth_adjusted_ofi_z,
                trailing_return_3s_bps,
            )
            confirmation_ms = self._confirmation_ms(
                evaluator.strategy_id,
                snapshot.symbol,
                side,
                snapshot.ts_ms,
                aligned=aligned,
            )
            return evaluator.evaluate(
                OfiReturnConfluenceContext(
                    side=side,
                    features=snapshot,
                    regime=regime,
                    plan=plan,
                    directional_depth_adjusted_ofi_robust_z=(directional_depth_adjusted_ofi_z),
                    trailing_return_3s_bps=trailing_return_3s_bps,
                    confirmation_ms=confirmation_ms,
                )
            )
        if isinstance(evaluator, BookSlopeAsymmetryStrategy):
            aligned = book_slope_asymmetry_ready(
                side,
                snapshot,
                regime,
                history_statistics.bid_slope_percentile,
                history_statistics.ask_slope_percentile,
                history_statistics.history_sample_count,
            )
            confirmation_ms = self._confirmation_ms(
                evaluator.strategy_id,
                snapshot.symbol,
                side,
                snapshot.ts_ms,
                aligned=aligned,
            )
            return evaluator.evaluate(
                BookSlopeAsymmetryContext(
                    side=side,
                    features=snapshot,
                    regime=regime,
                    plan=plan,
                    bid_slope_percentile=history_statistics.bid_slope_percentile,
                    ask_slope_percentile=history_statistics.ask_slope_percentile,
                    history_sample_count=history_statistics.history_sample_count,
                    confirmation_ms=confirmation_ms,
                )
            )
        if isinstance(evaluator, HourlyMomentumBreakoutStrategy):
            latest_open_ts_ms = hourly_candles[-1].open_ts_ms if hourly_candles else None
            cached = self._hourly_state_cache.get(snapshot.symbol)
            if cached is None or cached[0] != latest_open_ts_ms:
                state = hourly_momentum_state(hourly_candles)
                self._hourly_state_cache[snapshot.symbol] = (latest_open_ts_ms, state)
            else:
                state = cached[1]
            entry = Decimal(str(snapshot.mid))
            expected_cost_bps = max(
                Decimal("13"),
                Decimal(str(snapshot.spread_bps)) + Decimal("12"),
            )
            if state.atr is None or state.atr <= 0:
                hourly_plan = PlanInputs(
                    entry=entry,
                    structural_stop=None,
                    target=None,
                    expected_total_cost_bps=expected_cost_bps,
                )
            else:
                risk = max(Decimal(str(state.atr)) * Decimal("1.8"), entry * Decimal("0.003"))
                plan_direction = Decimal(1) if side is Side.LONG else Decimal(-1)
                hourly_plan = PlanInputs(
                    entry=entry,
                    structural_stop=entry - plan_direction * risk,
                    target=entry + plan_direction * risk * Decimal("4.5"),
                    expected_total_cost_bps=expected_cost_bps,
                )
            signal_ts_ms = state.signal_ts_ms
            return evaluator.evaluate(
                HourlyMomentumBreakoutContext(
                    side=side,
                    features=snapshot,
                    regime=regime,
                    plan=hourly_plan,
                    state=state,
                    signal_age_ms=(
                        snapshot.ts_ms - signal_ts_ms if signal_ts_ms is not None else None
                    ),
                )
            )
        raise TypeError(f"지원하지 않는 전략 evaluator: {type(evaluator).__name__}")

    @staticmethod
    def _history_statistics(
        history: _SortedFeatureHistory,
        snapshot: FeatureSnapshot,
    ) -> _HistoryStatistics:
        """동일 snapshot의 등록된 전략·방향이 같은 정렬 통계를 공유한다."""

        deviation_bps = _vwap_deviation_bps(snapshot) or 0.0
        directional_flow_z = robust_z_from_sorted(
            history.signed_notional,
            snapshot.signed_notional_3s,
        )
        directional_depth_adjusted_ofi_z = robust_z_from_sorted(
            history.depth_adjusted_ofi,
            snapshot.depth_adjusted_ofi_3s_bps,
        )
        return _HistoryStatistics(
            flow_z=abs(robust_z_from_sorted(history.flow, abs(snapshot.signed_notional_3s))),
            deviation_z=abs(robust_z_from_sorted(history.deviation, abs(deviation_bps))),
            price_response_percentile=rolling_percentile_from_sorted(
                history.price_response,
                snapshot.price_response_efficiency,
            ),
            compression_percentile=rolling_percentile_from_sorted(
                history.compression,
                snapshot.compression_ratio,
            ),
            efficiency_percentile=rolling_percentile_from_sorted(
                history.efficiency,
                snapshot.efficiency_ratio_30s,
            ),
            long_directional_flow_z=directional_flow_z,
            short_directional_flow_z=(0.0 if directional_flow_z == 0 else -directional_flow_z),
            long_depth_adjusted_ofi_z=directional_depth_adjusted_ofi_z,
            short_depth_adjusted_ofi_z=(
                0.0 if directional_depth_adjusted_ofi_z == 0 else -directional_depth_adjusted_ofi_z
            ),
            bid_slope_percentile=rolling_percentile_from_sorted(
                history.bid_book_slope,
                snapshot.bid_book_slope_10,
            ),
            ask_slope_percentile=rolling_percentile_from_sorted(
                history.ask_book_slope,
                snapshot.ask_book_slope_10,
            ),
            history_sample_count=len(history.bid_book_slope),
        )

    def _confirmation_ms(
        self,
        strategy_id: str,
        symbol: str,
        side: Side,
        timestamp_ms: int,
        *,
        aligned: bool,
    ) -> int:
        key = (strategy_id, symbol, side)
        if not aligned:
            self._confirmation_started_ms.pop(key, None)
            return 0
        started = self._confirmation_started_ms.get(key)
        if started is None or timestamp_ms < started:
            self._confirmation_started_ms[key] = timestamp_ms
            return 0
        return timestamp_ms - started


def _pullback_metrics(
    history: list[FeatureSnapshot],
    snapshot: FeatureSnapshot,
    side: Side,
    *,
    maximum_duration_seconds: int,
) -> _PullbackMetrics:
    """미래 표본 없이 impulse→눌림→현재 재가속 경로를 event time으로 계산한다."""

    window_start_ms = snapshot.ts_ms - maximum_duration_seconds * 1_000
    window = [item for item in history if window_start_ms <= item.ts_ms < snapshot.ts_ms]
    window.append(snapshot)
    if len(window) < 4:
        return _PullbackMetrics(0.0, 0.0, False)
    direction = 1 if side is Side.LONG else -1
    directional_prices = [item.mid * direction for item in window]
    peak_index = max(range(len(window)), key=directional_prices.__getitem__)
    if peak_index == 0 or peak_index >= len(window) - 2:
        return _PullbackMetrics(0.0, 0.0, False)
    impulse_origin = min(directional_prices[: peak_index + 1])
    impulse_distance = directional_prices[peak_index] - impulse_origin
    if impulse_distance <= 0:
        return _PullbackMetrics(0.0, 0.0, False)
    post_peak_prices = directional_prices[peak_index:]
    pullback_low_offset = min(
        range(len(post_peak_prices)),
        key=post_peak_prices.__getitem__,
    )
    pullback_low_index = peak_index + pullback_low_offset
    pullback_distance = directional_prices[peak_index] - directional_prices[pullback_low_index]
    current_price = directional_prices[-1]
    duration_seconds = (snapshot.ts_ms - window[peak_index].ts_ms) / 1_000
    return _PullbackMetrics(
        duration_seconds=max(0.0, duration_seconds),
        maximum_retrace_fraction=max(0.0, pullback_distance / impulse_distance),
        price_reaccelerated=(
            pullback_low_index < len(window) - 1
            and current_price > directional_prices[pullback_low_index]
        ),
    )


def _trailing_return_bps(
    history: list[FeatureSnapshot],
    snapshot: FeatureSnapshot,
    *,
    horizon_ms: int = 3_000,
    maximum_anchor_age_ms: int = 1_500,
) -> float | None:
    """현재보다 3초 이전의 가장 가까운 prefix 가격만 사용해 수익률을 계산한다."""

    target_ts_ms = snapshot.ts_ms - horizon_ms
    earliest_ts_ms = target_ts_ms - maximum_anchor_age_ms
    eligible = (
        item for item in history if earliest_ts_ms <= item.ts_ms <= target_ts_ms and item.mid > 0
    )
    anchor = max(eligible, key=lambda item: item.ts_ms, default=None)
    if anchor is None or snapshot.mid <= 0:
        return None
    return (snapshot.mid - anchor.mid) / anchor.mid * 10_000


def _plan(
    snapshot: FeatureSnapshot,
    side: Side,
    tick_size: Decimal,
    *,
    exit_style: ExitStyle,
) -> PlanInputs:
    entry = Decimal(str(snapshot.mid))
    spread = entry * Decimal(str(snapshot.spread_bps)) / Decimal(10_000)
    noise = max(tick_size * 2, spread * Decimal("1.5"), entry * Decimal("0.0002"))
    # 13bp 이상의 왕복 비용을 손익 양쪽에 반영해도 최종 CandidatePlan의
    # net R:R 1.20을 넘도록 exit 비중별 최소 구조 거리를 보수적으로 둔다.
    minimum_risk_fraction = (
        Decimal("0.0080") if exit_style is ExitStyle.REVERSION_70_30 else Decimal("0.0030")
    )
    risk_distance = max(noise * Decimal("1.2"), entry * minimum_risk_fraction)
    target_distance = risk_distance * Decimal("3.2")
    if side is Side.LONG:
        stop = entry - risk_distance
        target = entry + target_distance
    else:
        stop = entry + risk_distance
        target = entry - target_distance
    return PlanInputs(
        entry=entry,
        structural_stop=stop,
        target=target,
        expected_total_cost_bps=max(
            Decimal("13"),
            Decimal(str(snapshot.spread_bps)) + Decimal("12"),
        ),
    )
