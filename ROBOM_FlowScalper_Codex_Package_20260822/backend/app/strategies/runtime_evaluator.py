"""LIVE 피처를 A/B/C/D/E/F 전략 문맥으로 변환하고 실제 확인 시간을 보존한다."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal

from backend.app.domain.models import Side
from backend.app.features import FeatureSnapshot
from backend.app.regime import Regime
from backend.app.strategies.aggressor_flow import (
    AggressorFlowContext,
    AggressorFlowStrategy,
    aggressor_alignment_ready,
)
from backend.app.strategies.base import CandidateDecision, PlanInputs
from backend.app.strategies.compression_breakout import (
    CompressionBreakoutContext,
    CompressionBreakoutStrategy,
)
from backend.app.strategies.liquidity_sweep import (
    LiquiditySweepContext,
    LiquiditySweepStrategy,
)
from backend.app.strategies.ofi_pullback import OfiPullbackContext, OfiPullbackStrategy
from backend.app.strategies.queue_microprice import (
    QueueMicropriceContext,
    QueueMicropriceStrategy,
    queue_alignment_ready,
)
from backend.app.strategies.registry import StrategyRegistry
from backend.app.strategies.statistics import robust_z, rolling_percentile
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


class StrategySignalEvaluator:
    """전략별 평가를 동일 snapshot과 동일 비용 가정에서 결정적으로 실행한다."""

    def __init__(self, history_limit: int = 1_200) -> None:
        self._history: dict[str, deque[FeatureSnapshot]] = defaultdict(
            lambda: deque(maxlen=history_limit)
        )
        self._confirmation_started_ms: dict[tuple[str, str, Side], int] = {}

    def evaluate(
        self,
        registry: StrategyRegistry,
        snapshot: FeatureSnapshot,
        regime: Regime,
        *,
        tick_size: Decimal = Decimal("0.00000001"),
    ) -> tuple[EvaluatedSignal, ...]:
        history = list(self._history[snapshot.symbol])
        history_statistics = self._history_statistics(history, snapshot)
        results: list[EvaluatedSignal] = []
        for strategy_id in registry.strategy_ids:
            descriptor = registry.descriptor(strategy_id)
            for side in Side:
                if not registry.evaluation_enabled(strategy_id, side):
                    continue
                decision = self._evaluate_one(
                    descriptor.evaluator,
                    side,
                    snapshot,
                    regime,
                    history_statistics,
                    tick_size=tick_size,
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
        self._history[snapshot.symbol].append(snapshot)
        return tuple(results)

    def _evaluate_one(
        self,
        evaluator: object,
        side: Side,
        snapshot: FeatureSnapshot,
        regime: Regime,
        history_statistics: _HistoryStatistics,
        *,
        tick_size: Decimal,
    ) -> CandidateDecision:
        plan = _plan(snapshot, side, tick_size)
        deviation_bps = (snapshot.mid - snapshot.micro_vwap_10s) / snapshot.mid * 10_000
        direction = 1 if side is Side.LONG else -1
        ofi_short = snapshot.ofi_250ms * direction
        ofi_medium = snapshot.ofi_3s * direction
        trade_flow = snapshot.trade_imbalance_3s * direction
        microprice_alignment = (snapshot.microprice - snapshot.mid) * direction > 0
        if isinstance(evaluator, LiquiditySweepStrategy):
            sweep_context = LiquiditySweepContext(
                side=side,
                features=snapshot,
                regime=regime,
                plan=plan,
                sweep_extension_noise_units=abs(deviation_bps)
                / max(snapshot.spread_bps, 0.01),
                aggressive_flow_robust_z=history_statistics.flow_z,
                price_response_efficiency_quantile=(
                    history_statistics.price_response_percentile
                ),
                refill_persistence_ms=1_000 if snapshot.refill_ratio >= 0.55 else 0,
                reentry_confirmation_ms=500
                if abs(deviation_bps) <= max(2.0, snapshot.spread_bps * 3)
                else 0,
                ofi_flip=ofi_short > 0 and ofi_medium < 0,
                microprice_reclaimed=microprice_alignment,
                range_reentered=abs(deviation_bps) <= max(2.0, snapshot.spread_bps * 3),
            )
            return evaluator.evaluate(sweep_context)
        if isinstance(evaluator, CompressionBreakoutStrategy):
            breakout_context = CompressionBreakoutContext(
                side=side,
                features=snapshot,
                regime=regime,
                plan=plan,
                compression_quantile=history_statistics.compression_percentile,
                breakout_confirmed=ofi_medium > 0 and trade_flow > 0.15,
                initial_impulse_extended=snapshot.realized_volatility_30s >= 0.0015,
                pullback_seconds=3.0,
                pullback_retrace_fraction=0.40,
                counterflow_price_impact_weak=snapshot.price_response_efficiency <= 0.50,
                refill_recovered=snapshot.refill_ratio >= 0.50,
                ofi_reaccelerated=ofi_short > 0 and ofi_medium > 0,
                microprice_aligned=microprice_alignment,
                confirmation_ms=500 if ofi_short > 0 else 0,
            )
            return evaluator.evaluate(breakout_context)
        if isinstance(evaluator, VwapExhaustionStrategy):
            excursion_valid = deviation_bps < 0 if side is Side.LONG else deviation_bps > 0
            vwap_context = VwapExhaustionContext(
                side=side,
                features=snapshot,
                regime=regime,
                plan=plan,
                vwap_deviation_robust_z=history_statistics.deviation_z,
                excursion_direction_valid=excursion_valid,
                aggressive_flow_robust_z=history_statistics.flow_z,
                price_progress_stalled=(
                    history_statistics.price_response_percentile <= 0.30
                ),
                opposite_depth_refilled=snapshot.refill_ratio >= 0.55,
                ofi_reversed=ofi_short > 0 and ofi_medium < 0,
                microprice_reversed=microprice_alignment,
                structure_reentered=abs(deviation_bps) <= max(8.0, snapshot.spread_bps * 8),
                confirmation_ms=500 if microprice_alignment else 0,
            )
            return evaluator.evaluate(vwap_context)
        if isinstance(evaluator, OfiPullbackStrategy):
            ofi_context = OfiPullbackContext(
                side=side,
                features=snapshot,
                regime=regime,
                plan=plan,
                multi_window_ofi_aligned=ofi_short > 0 and ofi_medium > 0,
                aggressive_trade_aligned=trade_flow > 0.15,
                microprice_aligned=microprice_alignment,
                price_efficiency_percentile=history_statistics.efficiency_percentile,
                pullback_seconds=5.0,
                pullback_retrace_fraction=0.35,
                counterflow_price_impact_weak=snapshot.price_response_efficiency <= 0.50,
                original_flow_reaccelerated=ofi_short > 0 and ofi_short >= ofi_medium * 0.1,
                confirmation_ms=500 if ofi_short > 0 else 0,
            )
            return evaluator.evaluate(ofi_context)
        if isinstance(evaluator, QueueMicropriceStrategy):
            plan = _momentum_plan(snapshot, side, tick_size)
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
            plan = _momentum_plan(snapshot, side, tick_size)
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
        raise TypeError(f"지원하지 않는 전략 evaluator: {type(evaluator).__name__}")

    @staticmethod
    def _history_statistics(
        history: list[FeatureSnapshot],
        snapshot: FeatureSnapshot,
    ) -> _HistoryStatistics:
        """동일 snapshot의 12개 전략·방향 평가에서 같은 정렬을 반복하지 않는다."""

        flow_history = [abs(item.signed_notional_3s) for item in history]
        deviation_history = [
            abs((item.mid - item.micro_vwap_10s) / item.mid * 10_000)
            for item in history
            if item.mid > 0
        ]
        deviation_bps = (snapshot.mid - snapshot.micro_vwap_10s) / snapshot.mid * 10_000
        signed_history = [item.signed_notional_3s for item in history]
        return _HistoryStatistics(
            flow_z=abs(robust_z(flow_history, abs(snapshot.signed_notional_3s))),
            deviation_z=abs(robust_z(deviation_history, abs(deviation_bps))),
            price_response_percentile=rolling_percentile(
                [item.price_response_efficiency for item in history],
                snapshot.price_response_efficiency,
            ),
            compression_percentile=rolling_percentile(
                [item.compression_ratio for item in history],
                snapshot.compression_ratio,
            ),
            efficiency_percentile=rolling_percentile(
                [item.efficiency_ratio_30s for item in history],
                snapshot.efficiency_ratio_30s,
            ),
            long_directional_flow_z=robust_z(
                signed_history,
                snapshot.signed_notional_3s,
            ),
            short_directional_flow_z=robust_z(
                [-value for value in signed_history],
                -snapshot.signed_notional_3s,
            ),
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


def _plan(snapshot: FeatureSnapshot, side: Side, tick_size: Decimal) -> PlanInputs:
    entry = Decimal(str(snapshot.mid))
    spread = entry * Decimal(str(snapshot.spread_bps)) / Decimal(10_000)
    noise = max(tick_size * 2, spread * Decimal("1.5"), entry * Decimal("0.0002"))
    risk_distance = max(noise * Decimal("1.2"), entry * Decimal("0.0015"))
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


def _momentum_plan(
    snapshot: FeatureSnapshot,
    side: Side,
    tick_size: Decimal,
) -> PlanInputs:
    """E/F의 3.2R 구조 target이 보수적 비용 gate를 통과하는 stop을 산정한다."""

    entry = Decimal(str(snapshot.mid))
    spread = entry * Decimal(str(snapshot.spread_bps)) / Decimal(10_000)
    noise = max(tick_size * 2, spread * Decimal("1.5"), entry * Decimal("0.0002"))
    risk_distance = max(noise * Decimal("1.2"), entry * Decimal("0.0020"))
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
