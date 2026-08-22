"""A/B/C/D 전략 메타데이터와 ACTIVE·SHADOW·OFF·방향 설정을 중앙 관리한다."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.app.domain.models import Side
from backend.app.regime import Regime
from backend.app.strategies.compression_breakout import CompressionBreakoutStrategy
from backend.app.strategies.liquidity_sweep import LiquiditySweepStrategy
from backend.app.strategies.ofi_pullback import OfiPullbackStrategy
from backend.app.strategies.vwap_exhaustion import VwapExhaustionStrategy


class StrategyMode(StrEnum):
    ACTIVE = "ACTIVE"
    SHADOW = "SHADOW"
    OFF = "OFF"


class StrategyStability(StrEnum):
    STABLE = "STABLE"
    EXPERIMENTAL = "EXPERIMENTAL"


StrategyEvaluator = (
    LiquiditySweepStrategy
    | CompressionBreakoutStrategy
    | VwapExhaustionStrategy
    | OfiPullbackStrategy
)


@dataclass(frozen=True, slots=True)
class StrategyDescriptor:
    strategy_id: str
    display_name_ko: str
    short_name: str
    summary_ko: str
    stability: StrategyStability
    supported_regimes: tuple[Regime, ...]
    evaluator: StrategyEvaluator
    paper_only: bool = True


@dataclass(slots=True)
class StrategySetting:
    mode: StrategyMode = StrategyMode.ACTIVE
    long_enabled: bool = True
    short_enabled: bool = True

    def direction_enabled(self, side: Side) -> bool:
        return self.long_enabled if side is Side.LONG else self.short_enabled


class StrategyRegistry:
    """설정 변경은 명시적 사용자 동작으로만 허용하고 자동 승격·중지는 하지 않는다."""

    def __init__(self) -> None:
        descriptors = (
            StrategyDescriptor(
                strategy_id="LSA_REVERSAL_V1",
                display_name_ko="유동성 쓸기 반전",
                short_name="LSA 반전",
                summary_ko="쓸기·흡수·호가 재충전·범위 복귀를 확인합니다.",
                stability=StrategyStability.STABLE,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=LiquiditySweepStrategy(),
            ),
            StrategyDescriptor(
                strategy_id="CBR_CONTINUATION_V1",
                display_name_ko="압축 돌파 재가속",
                short_name="CBR 돌파",
                summary_ko="압축 뒤 돌파를 추격하지 않고 눌림과 재가속을 확인합니다.",
                stability=StrategyStability.STABLE,
                supported_regimes=(Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=CompressionBreakoutStrategy(),
            ),
            StrategyDescriptor(
                strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
                display_name_ko="VWAP 과도이탈 평균복귀",
                short_name="VWAP 소진",
                summary_ko="범위장에서 micro-VWAP 이탈과 공격 흐름 소진을 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE,),
                evaluator=VwapExhaustionStrategy(),
            ),
            StrategyDescriptor(
                strategy_id="OFI_CONTINUATION_PULLBACK_V1",
                display_name_ko="OFI 추세 눌림 지속",
                short_name="OFI 눌림",
                summary_ko="추세장에서 다중 OFI와 약한 역방향 눌림 뒤 재가속을 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=OfiPullbackStrategy(),
            ),
        )
        self._descriptors = {item.strategy_id: item for item in descriptors}
        self._settings = {item.strategy_id: StrategySetting() for item in descriptors}

    @property
    def strategy_ids(self) -> tuple[str, ...]:
        return tuple(self._descriptors)

    def descriptor(self, strategy_id: str) -> StrategyDescriptor:
        try:
            return self._descriptors[strategy_id]
        except KeyError as error:
            raise ValueError(f"알 수 없는 전략: {strategy_id}") from error

    def setting(self, strategy_id: str) -> StrategySetting:
        self.descriptor(strategy_id)
        return self._settings[strategy_id]

    def configure(
        self,
        strategy_id: str,
        *,
        mode: StrategyMode,
        long_enabled: bool,
        short_enabled: bool,
    ) -> StrategySetting:
        setting = self.setting(strategy_id)
        setting.mode = mode
        setting.long_enabled = long_enabled
        setting.short_enabled = short_enabled
        return setting

    def evaluation_enabled(self, strategy_id: str, side: Side) -> bool:
        setting = self.setting(strategy_id)
        return setting.mode is not StrategyMode.OFF and setting.direction_enabled(side)

    def main_enabled(self, strategy_id: str, side: Side) -> bool:
        setting = self.setting(strategy_id)
        return setting.mode is StrategyMode.ACTIVE and setting.direction_enabled(side)

    def shadow_enabled(self, strategy_id: str, side: Side) -> bool:
        setting = self.setting(strategy_id)
        return (
            setting.mode in {StrategyMode.ACTIVE, StrategyMode.SHADOW}
            and setting.direction_enabled(side)
        )

    def rows(self) -> list[dict[str, object]]:
        return [
            {
                "strategy_id": descriptor.strategy_id,
                "display_name_ko": descriptor.display_name_ko,
                "short_name": descriptor.short_name,
                "summary_ko": descriptor.summary_ko,
                "stability": descriptor.stability.value,
                "supported_regimes": [regime.value for regime in descriptor.supported_regimes],
                "paper_only": descriptor.paper_only,
                "mode": self.setting(descriptor.strategy_id).mode.value,
                "long_enabled": self.setting(descriptor.strategy_id).long_enabled,
                "short_enabled": self.setting(descriptor.strategy_id).short_enabled,
            }
            for descriptor in self._descriptors.values()
        ]
