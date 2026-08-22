"""분해 가능한 점수와 하드 거절 코드로 후보 순위를 계산한다."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.features import FeatureSnapshot
from backend.app.regime import Regime


@dataclass(frozen=True, slots=True)
class CandidateSeed:
    symbol: str
    strategy_id: str
    structure_quality: float
    flow_confirmation: float
    price_response_quality: float
    liquidity_quality: float
    regime_fit: float
    cost_penalty: float
    latency_penalty: float
    uncertainty_penalty: float


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    symbol: str
    strategy_id: str
    score: float | None
    components: dict[str, float]
    rejection_codes: tuple[str, ...]
    calibration_status: str = "CALIBRATING"
    tp_probability: None = None


class CandidateRanker:
    BLOCKED_REGIMES = {Regime.SHOCK, Regime.DEGRADED, Regime.WARMUP}

    def rank(
        self,
        candidates: list[tuple[CandidateSeed, FeatureSnapshot, Regime]],
        limit: int = 3,
    ) -> tuple[RankedCandidate, ...]:
        results = [self.evaluate(seed, snapshot, regime) for seed, snapshot, regime in candidates]
        return tuple(
            sorted(
                results,
                key=lambda item: (
                    bool(item.rejection_codes),
                    -(item.score if item.score is not None else -1.0),
                    item.symbol,
                ),
            )[:limit]
        )

    def evaluate(
        self,
        seed: CandidateSeed,
        snapshot: FeatureSnapshot,
        regime: Regime,
    ) -> RankedCandidate:
        components = {
            "structure_quality": seed.structure_quality,
            "flow_confirmation": seed.flow_confirmation,
            "price_response_quality": seed.price_response_quality,
            "liquidity_quality": seed.liquidity_quality,
            "regime_fit": seed.regime_fit,
            "cost_penalty": seed.cost_penalty,
            "latency_penalty": seed.latency_penalty,
            "uncertainty_penalty": seed.uncertainty_penalty,
        }
        rejections: list[str] = []
        if not snapshot.data_healthy:
            rejections.append("STALE_OR_DEGRADED_DATA")
        if regime in self.BLOCKED_REGIMES:
            rejections.append(f"REGIME_{regime.value}")
        if snapshot.spread_bps > 12:
            rejections.append("WIDE_SPREAD")
        score = None
        if not rejections:
            raw = (
                seed.structure_quality
                + seed.flow_confirmation
                + seed.price_response_quality
                + seed.liquidity_quality
                + seed.regime_fit
                - seed.cost_penalty
                - seed.latency_penalty
                - seed.uncertainty_penalty
            ) / 5
            score = max(0.0, min(1.0, round(raw, 8)))
        return RankedCandidate(
            symbol=seed.symbol,
            strategy_id=seed.strategy_id,
            score=score,
            components=components,
            rejection_codes=tuple(rejections),
        )

