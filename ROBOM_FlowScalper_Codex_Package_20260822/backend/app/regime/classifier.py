"""건전성·변동성·방향 효율로 시장 레짐을 투명하게 분류한다."""

from __future__ import annotations

from enum import StrEnum

from backend.app.features import FeatureSnapshot


class Regime(StrEnum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    SHOCK = "SHOCK"
    DEGRADED = "DEGRADED"
    WARMUP = "WARMUP"


class RegimeClassifier:
    def __init__(
        self,
        *,
        minimum_warmup_seconds: float = 10.0,
        shock_volatility: float = 0.002,
        shock_spread_bps: float = 20.0,
        trend_efficiency: float = 0.55,
    ) -> None:
        self.minimum_warmup_seconds = minimum_warmup_seconds
        self.shock_volatility = shock_volatility
        self.shock_spread_bps = shock_spread_bps
        self.trend_efficiency = trend_efficiency

    def classify(self, snapshot: FeatureSnapshot) -> Regime:
        if not snapshot.data_healthy:
            return Regime.DEGRADED
        if snapshot.warmup_seconds < self.minimum_warmup_seconds:
            return Regime.WARMUP
        if (
            snapshot.realized_volatility_30s >= self.shock_volatility
            or snapshot.spread_bps >= self.shock_spread_bps
        ):
            return Regime.SHOCK
        direction = snapshot.micro_vwap_10s - snapshot.mid
        aligned_flow = snapshot.ofi_3s + snapshot.trade_imbalance_3s
        if snapshot.efficiency_ratio_30s >= self.trend_efficiency:
            if direction >= 0 and aligned_flow > 0:
                return Regime.TREND_UP
            if direction <= 0 and aligned_flow < 0:
                return Regime.TREND_DOWN
        return Regime.RANGE
