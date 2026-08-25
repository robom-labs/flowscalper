# 서로 다른 시장 가설의 장중 연구 후보를 실행 레지스트리와 분리해 평가한다.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.app.domain.models import Side
from backend.app.intraday.features import TimeframeFeatureSnapshot
from backend.app.intraday.mirror import ResearchVariantKind, SignalVariant


class CandidateFamily(StrEnum):
    FLOW_TREND_PULLBACK = "FLOW_TREND_PULLBACK"
    COMPRESSION_RVOL_BREAKOUT = "COMPRESSION_RVOL_BREAKOUT"
    RANGE_VWAP_REVERSION = "RANGE_VWAP_REVERSION"
    HTF_TREND_ENTRY = "HTF_TREND_ENTRY"
    ABSORPTION_REFILL_REVERSE = "ABSORPTION_REFILL_REVERSE"


@dataclass(frozen=True, slots=True)
class ResearchCandidateSignal:
    candidate_id: str
    family: CandidateFamily
    variant: ResearchVariantKind
    symbol: str
    side: Side
    signal_ts_ms: int
    interval_seconds: int
    information_set_id: str
    reason_codes: tuple[str, ...]

    def as_variant(self) -> SignalVariant:
        return SignalVariant(
            candidate_id=self.candidate_id,
            variant=self.variant,
            symbol=self.symbol,
            side=self.side,
            signal_ts_ms=self.signal_ts_ms,
            interval_seconds=self.interval_seconds,
            information_set_id=self.information_set_id,
        )


class IntradayCandidateEvaluator:
    """사전 고정된 소수의 이질적 가설만 완성 봉 시점에 평가한다."""

    def evaluate(
        self,
        snapshot: TimeframeFeatureSnapshot,
        *,
        decision_ts_ms: int | None = None,
    ) -> tuple[ResearchCandidateSignal, ...]:
        signal_ts_ms = decision_ts_ms or snapshot.feature_ts_ms
        if signal_ts_ms < snapshot.feature_ts_ms:
            raise ValueError("신호 결정 시각은 완성 봉 피처 시각보다 빠를 수 없습니다.")
        candidates: list[ResearchCandidateSignal] = []
        squeeze = (
            snapshot.bollinger_upper - snapshot.bollinger_lower
            < snapshot.keltner_upper - snapshot.keltner_lower
        )
        trend_side = (
            Side.LONG
            if snapshot.higher_timeframe_trend == "UP"
            else Side.SHORT
            if snapshot.higher_timeframe_trend == "DOWN"
            else None
        )
        if trend_side is not None:
            direction = 1 if trend_side is Side.LONG else -1
            if (
                (snapshot.ema_fast - snapshot.ema_slow) * direction > 0
                and snapshot.taker_flow_ratio * direction >= 0.15
                and abs(snapshot.close - snapshot.ema_fast) <= max(snapshot.atr, 1e-12)
            ):
                candidates.append(
                    self._signal(
                        CandidateFamily.FLOW_TREND_PULLBACK,
                        trend_side,
                        snapshot,
                        ("HTF_TREND_ALIGNED", "EMA_PULLBACK", "TAKER_FLOW_CONFIRMED"),
                        signal_ts_ms,
                    )
                )
            breakout = (
                snapshot.close > snapshot.donchian_high
                if trend_side is Side.LONG
                else snapshot.close < snapshot.donchian_low
            )
            if squeeze and snapshot.relative_volume >= 1.5 and breakout:
                candidates.append(
                    self._signal(
                        CandidateFamily.COMPRESSION_RVOL_BREAKOUT,
                        trend_side,
                        snapshot,
                        ("VOLATILITY_COMPRESSION", "RVOL_EXPANSION", "DONCHIAN_BREAK"),
                        signal_ts_ms,
                    )
                )
            if breakout and snapshot.taker_flow_ratio * direction >= 0.20:
                candidates.append(
                    self._signal(
                        CandidateFamily.HTF_TREND_ENTRY,
                        trend_side,
                        snapshot,
                        ("HTF_TREND_ALIGNED", "COMPLETED_CANDLE_BREAK", "FLOW_CONFIRMED"),
                        signal_ts_ms,
                    )
                )
        if snapshot.regime == "RANGE" and abs(snapshot.close_zscore) >= 2:
            side = Side.SHORT if snapshot.close_zscore > 0 else Side.LONG
            direction = 1 if side is Side.LONG else -1
            if snapshot.taker_flow_ratio * direction >= 0:
                candidates.append(
                    self._signal(
                        CandidateFamily.RANGE_VWAP_REVERSION,
                        side,
                        snapshot,
                        ("RANGE_REGIME", "BOLLINGER_EXTREME", "FLOW_NOT_ADVERSE"),
                        signal_ts_ms,
                    )
                )
        candle_direction = 1 if snapshot.close >= snapshot.session_vwap else -1
        if (
            snapshot.relative_volume >= 1.8
            and snapshot.taker_flow_ratio * candle_direction <= -0.35
            and abs(snapshot.close_zscore) >= 1.5
        ):
            candidates.append(
                self._signal(
                    CandidateFamily.ABSORPTION_REFILL_REVERSE,
                    Side.SHORT if candle_direction > 0 else Side.LONG,
                    snapshot,
                    ("HIGH_RVOL", "PRICE_FLOW_DIVERGENCE", "EXTENDED_FROM_MEAN"),
                    signal_ts_ms,
                )
            )
        return tuple(candidates)

    def evaluate_reverse_hypotheses(
        self,
        snapshot: TimeframeFeatureSnapshot,
        *,
        decision_ts_ms: int | None = None,
    ) -> tuple[ResearchCandidateSignal, ...]:
        """원본 방향 반전이 아니라 별도 반증조건을 가진 역가설을 평가한다."""

        signal_ts_ms = decision_ts_ms or snapshot.feature_ts_ms
        if signal_ts_ms < snapshot.feature_ts_ms:
            raise ValueError("신호 결정 시각은 완성 봉 피처 시각보다 빠를 수 없습니다.")
        signals: list[ResearchCandidateSignal] = []
        if snapshot.regime == "RANGE" and abs(snapshot.close_zscore) >= 2:
            outward_side = Side.LONG if snapshot.close_zscore > 0 else Side.SHORT
            direction = 1 if outward_side is Side.LONG else -1
            if snapshot.taker_flow_ratio * direction >= 0.35 and snapshot.relative_volume >= 1.3:
                signals.append(
                    self._signal(
                        CandidateFamily.RANGE_VWAP_REVERSION,
                        outward_side,
                        snapshot,
                        ("REVERSE_HYPOTHESIS", "EXTREME_CONTINUATION", "FLOW_ACCELERATION"),
                        signal_ts_ms,
                        variant=ResearchVariantKind.HYPOTHESIS_REVERSE,
                    )
                )
        if snapshot.close > snapshot.donchian_high and snapshot.taker_flow_ratio <= -0.25:
            signals.append(
                self._signal(
                    CandidateFamily.COMPRESSION_RVOL_BREAKOUT,
                    Side.SHORT,
                    snapshot,
                    ("REVERSE_HYPOTHESIS", "UPSIDE_FALSE_BREAK", "ADVERSE_FLOW"),
                    signal_ts_ms,
                    variant=ResearchVariantKind.HYPOTHESIS_REVERSE,
                )
            )
        elif snapshot.close < snapshot.donchian_low and snapshot.taker_flow_ratio >= 0.25:
            signals.append(
                self._signal(
                    CandidateFamily.COMPRESSION_RVOL_BREAKOUT,
                    Side.LONG,
                    snapshot,
                    ("REVERSE_HYPOTHESIS", "DOWNSIDE_FALSE_BREAK", "ADVERSE_FLOW"),
                    signal_ts_ms,
                    variant=ResearchVariantKind.HYPOTHESIS_REVERSE,
                )
            )
        return tuple(signals)

    @staticmethod
    def hypothesis_reverse(
        original: ResearchCandidateSignal,
    ) -> ResearchCandidateSignal:
        opposite = Side.SHORT if original.side is Side.LONG else Side.LONG
        return ResearchCandidateSignal(
            candidate_id=f"{original.candidate_id}_REVERSE",
            family=original.family,
            variant=ResearchVariantKind.HYPOTHESIS_REVERSE,
            symbol=original.symbol,
            side=opposite,
            signal_ts_ms=original.signal_ts_ms,
            interval_seconds=original.interval_seconds,
            information_set_id=original.information_set_id,
            reason_codes=(*original.reason_codes, "SEPARATE_REVERSE_HYPOTHESIS"),
        )

    @staticmethod
    def _signal(
        family: CandidateFamily,
        side: Side,
        snapshot: TimeframeFeatureSnapshot,
        reasons: tuple[str, ...],
        signal_ts_ms: int,
        *,
        variant: ResearchVariantKind = ResearchVariantKind.ORIGINAL,
    ) -> ResearchCandidateSignal:
        information_set_id = (
            f"{snapshot.symbol}:{snapshot.interval_seconds}:"
            f"{snapshot.feature_ts_ms}:{signal_ts_ms}"
        )
        return ResearchCandidateSignal(
            candidate_id=(
                family.value
                if variant is ResearchVariantKind.ORIGINAL
                else f"{family.value}_REVERSE_HYPOTHESIS"
            ),
            family=family,
            variant=variant,
            symbol=snapshot.symbol,
            side=side,
            signal_ts_ms=signal_ts_ms,
            interval_seconds=snapshot.interval_seconds,
            information_set_id=information_set_id,
            reason_codes=reasons,
        )
