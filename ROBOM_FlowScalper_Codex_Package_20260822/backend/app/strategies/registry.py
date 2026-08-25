"""A/B/C/D/E/F/G/H/I/J 전략 메타데이터와 Strategy League 설정을 중앙 관리한다."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.app.domain.models import Side
from backend.app.regime import Regime
from backend.app.strategies.aggressor_flow import AggressorFlowStrategy
from backend.app.strategies.book_slope_asymmetry import BookSlopeAsymmetryStrategy
from backend.app.strategies.compression_breakout import CompressionBreakoutStrategy
from backend.app.strategies.depth_adjusted_ofi import DepthAdjustedOfiStrategy
from backend.app.strategies.liquidity_sweep import LiquiditySweepStrategy
from backend.app.strategies.multilevel_microprice import MultilevelMicropriceStrategy
from backend.app.strategies.ofi_pullback import OfiPullbackStrategy
from backend.app.strategies.ofi_return_confluence import OfiReturnConfluenceStrategy
from backend.app.strategies.queue_microprice import QueueMicropriceStrategy
from backend.app.strategies.vwap_exhaustion import VwapExhaustionStrategy


class StrategyMode(StrEnum):
    ACTIVE = "ACTIVE"
    SHADOW = "SHADOW"
    OFF = "OFF"


class StrategyLifecycle(StrEnum):
    RESEARCH = "RESEARCH"
    SHADOW = "SHADOW"
    CHALLENGER = "CHALLENGER"
    ACTIVE = "ACTIVE"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


class StrategyChangeSource(StrEnum):
    USER_UI = "USER_UI"
    AUTO_GOVERNOR = "AUTO_GOVERNOR"
    RECOVERY = "RECOVERY"
    MIGRATION = "MIGRATION"


class StrategyRevisionConflict(RuntimeError):
    def __init__(self, current_setting: dict[str, object]) -> None:
        super().__init__("전략 설정 revision이 최신 상태와 다릅니다.")
        self.current_setting = current_setting


class StrategyManualLockConflict(RuntimeError):
    """사용자가 고정한 전략 설정을 자동 governor가 덮어쓰지 못하게 한다."""


class StrategyStability(StrEnum):
    STABLE = "STABLE"
    EXPERIMENTAL = "EXPERIMENTAL"


class ExitStyle(StrEnum):
    REVERSION_70_30 = "REVERSION_70_30"
    TREND_40_60 = "TREND_40_60"


StrategyEvaluator = (
    LiquiditySweepStrategy
    | CompressionBreakoutStrategy
    | VwapExhaustionStrategy
    | OfiPullbackStrategy
    | QueueMicropriceStrategy
    | AggressorFlowStrategy
    | MultilevelMicropriceStrategy
    | DepthAdjustedOfiStrategy
    | OfiReturnConfluenceStrategy
    | BookSlopeAsymmetryStrategy
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
    exit_style: ExitStyle
    paper_only: bool = True


@dataclass(slots=True)
class StrategySetting:
    mode: StrategyMode = StrategyMode.ACTIVE
    lifecycle: StrategyLifecycle = StrategyLifecycle.RESEARCH
    long_enabled: bool = True
    short_enabled: bool = True
    revision: int = 0
    manual_lock: bool = False
    changed_by: StrategyChangeSource = StrategyChangeSource.MIGRATION
    change_reason: str = "SAFE_DEFAULT"
    updated_ts_ms: int = 0

    def direction_enabled(self, side: Side) -> bool:
        return self.long_enabled if side is Side.LONG else self.short_enabled


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    strategy_id: str
    lifecycle: StrategyLifecycle
    expected_revision: int
    reason: str


class StrategyRegistry:
    """설정 변경은 명시적 사용자 동작으로만 허용하고 자동 승격·중지는 하지 않는다."""

    def __init__(self) -> None:
        descriptors = (
            StrategyDescriptor(
                strategy_id="LSA_REVERSAL_V1",
                display_name_ko="급락·급등 쓸기 반전",
                short_name="LSA 반전",
                summary_ko=(
                    "반전 가설을 연구했으나 비용후 train·holdout 실패로 기본 중지됐습니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=LiquiditySweepStrategy(),
                exit_style=ExitStyle.REVERSION_70_30,
            ),
            StrategyDescriptor(
                strategy_id="CBR_CONTINUATION_V1",
                display_name_ko="압축 돌파 재가속",
                short_name="CBR 돌파",
                summary_ko="압축 뒤 돌파를 추격하지 않고 눌림과 재가속을 확인합니다.",
                stability=StrategyStability.STABLE,
                supported_regimes=(Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=CompressionBreakoutStrategy(),
                exit_style=ExitStyle.TREND_40_60,
            ),
            StrategyDescriptor(
                strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
                display_name_ko="VWAP 과도이탈 평균복귀",
                short_name="VWAP 소진",
                summary_ko="범위장에서 micro-VWAP 이탈과 공격 흐름 소진을 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE,),
                evaluator=VwapExhaustionStrategy(),
                exit_style=ExitStyle.REVERSION_70_30,
            ),
            StrategyDescriptor(
                strategy_id="OFI_CONTINUATION_PULLBACK_V1",
                display_name_ko="OFI 추세 눌림 지속",
                short_name="OFI 눌림",
                summary_ko=(
                    "다중 OFI 눌림 가설을 연구했으나 저장 train과 후기 자연표본이 "
                    "모두 비용후 실패해 기본 중지됐습니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=OfiPullbackStrategy(),
                exit_style=ExitStyle.TREND_40_60,
            ),
            StrategyDescriptor(
                strategy_id="QUEUE_MICROPRICE_MOMENTUM_V1",
                display_name_ko="호가 쏠림 순간추세",
                short_name="호가 쏠림",
                summary_ko=(
                    "호가 불균형·OFI·체결 흐름을 연구했으나 비용후 검증 실패로 기본 중지됐습니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=QueueMicropriceStrategy(),
                exit_style=ExitStyle.TREND_40_60,
            ),
            StrategyDescriptor(
                strategy_id="AGGRESSOR_FLOW_CONTINUATION_V1",
                display_name_ko="강한 체결 흐름 지속",
                short_name="체결흐름",
                summary_ko="강한 공격 체결이 실제 가격 반응과 함께 지속되는지 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=AggressorFlowStrategy(),
                exit_style=ExitStyle.TREND_40_60,
            ),
            StrategyDescriptor(
                strategy_id="MULTILEVEL_MICROPRICE_MOMENTUM_V1",
                display_name_ko="다중호가 공정가 추세",
                short_name="다중호가",
                summary_ko="10단계 호가 공정가·OFI·체결 흐름의 같은 방향 지속을 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=MultilevelMicropriceStrategy(),
                exit_style=ExitStyle.TREND_40_60,
            ),
            StrategyDescriptor(
                strategy_id="DEPTH_ADJUSTED_OFI_IMPULSE_V1",
                display_name_ko="깊이보정 OFI 충격",
                short_name="깊이 OFI",
                summary_ko=(
                    "호가 깊이보정 OFI를 연구했으나 보수적 비용후 검증 실패로 기본 중지됐습니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=DepthAdjustedOfiStrategy(),
                exit_style=ExitStyle.TREND_40_60,
            ),
            StrategyDescriptor(
                strategy_id="OFI_RETURN_CONFLUENCE_V1",
                display_name_ko="OFI·단기수익률 동행",
                short_name="OFI·가격동행",
                summary_ko="깊이보정 주문흐름과 최근 가격 방향이 함께 이어지는지 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=OfiReturnConfluenceStrategy(),
                exit_style=ExitStyle.TREND_40_60,
            ),
            StrategyDescriptor(
                strategy_id="BOOK_SLOPE_ASYMMETRY_V1",
                display_name_ko="호가 기울기 비대칭",
                short_name="호가 기울기",
                summary_ko=(
                    "10단계 호가의 한쪽이 얇고 반대쪽 지지가 두꺼운 상태의 지속을 확인합니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=BookSlopeAsymmetryStrategy(),
                exit_style=ExitStyle.TREND_40_60,
            ),
        )
        self._descriptors = {item.strategy_id: item for item in descriptors}
        active_ids = {"CBR_CONTINUATION_V1"}
        retired_ids = {
            "LSA_REVERSAL_V1",
            "OFI_CONTINUATION_PULLBACK_V1",
            "QUEUE_MICROPRICE_MOMENTUM_V1",
            "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        }
        self._settings = {
            item.strategy_id: StrategySetting(
                mode=(
                    StrategyMode.ACTIVE
                    if item.strategy_id in active_ids
                    else StrategyMode.OFF
                    if item.strategy_id in retired_ids
                    else StrategyMode.SHADOW
                ),
                lifecycle=(
                    StrategyLifecycle.ACTIVE
                    if item.strategy_id in active_ids
                    else StrategyLifecycle.RETIRED
                    if item.strategy_id in retired_ids
                    else StrategyLifecycle.SHADOW
                ),
            )
            for item in descriptors
        }
        self._revision_history = {
            strategy_id: {0: self._setting_row(strategy_id)}
            for strategy_id in self._settings
        }

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
        expected_revision: int | None = None,
        manual_lock: bool | None = None,
        lifecycle: StrategyLifecycle | None = None,
        source: StrategyChangeSource = StrategyChangeSource.USER_UI,
        reason: str = "USER_CONFIGURATION",
        updated_ts_ms: int = 0,
    ) -> StrategySetting:
        setting = self.setting(strategy_id)
        if expected_revision is not None and expected_revision != setting.revision:
            raise StrategyRevisionConflict(self._setting_row(strategy_id))
        if source is StrategyChangeSource.AUTO_GOVERNOR and setting.manual_lock:
            raise StrategyManualLockConflict(
                f"사용자가 고정한 전략 설정입니다: {strategy_id}"
            )
        resolved_lifecycle = lifecycle or self.lifecycle_for_mode(mode)
        if mode is not self.mode_for_lifecycle(resolved_lifecycle):
            raise ValueError("전략 lifecycle과 실행 mode가 일치하지 않습니다.")
        setting.mode = mode
        setting.lifecycle = resolved_lifecycle
        setting.long_enabled = long_enabled
        setting.short_enabled = short_enabled
        setting.revision += 1
        setting.manual_lock = (
            source is StrategyChangeSource.USER_UI
            if manual_lock is None
            else manual_lock
        )
        setting.changed_by = source
        setting.change_reason = reason
        setting.updated_ts_ms = updated_ts_ms
        self._revision_history[strategy_id][setting.revision] = self._setting_row(strategy_id)
        return setting

    def apply_lifecycle_transitions(
        self,
        transitions: tuple[LifecycleTransition, ...],
        *,
        source: StrategyChangeSource,
        updated_ts_ms: int,
    ) -> tuple[dict[str, object], ...]:
        """여러 전략의 lifecycle 교체를 먼저 전체 검증한 뒤 한 번에 반영한다."""

        if not transitions:
            raise ValueError("반영할 lifecycle 전환이 없습니다.")
        strategy_ids = [transition.strategy_id for transition in transitions]
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("하나의 전략을 한 전환에서 여러 번 바꿀 수 없습니다.")

        for transition in transitions:
            setting = self.setting(transition.strategy_id)
            if transition.expected_revision != setting.revision:
                raise StrategyRevisionConflict(self._setting_row(transition.strategy_id))
            if source is StrategyChangeSource.AUTO_GOVERNOR and setting.manual_lock:
                raise StrategyManualLockConflict(
                    f"사용자가 고정한 전략 설정입니다: {transition.strategy_id}"
                )

        changed_rows: list[dict[str, object]] = []
        for transition in transitions:
            setting = self.setting(transition.strategy_id)
            setting.lifecycle = transition.lifecycle
            setting.mode = self.mode_for_lifecycle(transition.lifecycle)
            setting.revision += 1
            setting.manual_lock = source is StrategyChangeSource.USER_UI
            setting.changed_by = source
            setting.change_reason = transition.reason
            setting.updated_ts_ms = updated_ts_ms
            row = self._setting_row(transition.strategy_id)
            self._revision_history[transition.strategy_id][setting.revision] = row
            changed_rows.append(row)
        return tuple(changed_rows)

    def restore_setting(
        self,
        strategy_id: str,
        *,
        mode: StrategyMode,
        long_enabled: bool,
        short_enabled: bool,
        revision: int,
        manual_lock: bool,
        changed_by: StrategyChangeSource,
        change_reason: str,
        updated_ts_ms: int,
        lifecycle: StrategyLifecycle | None = None,
    ) -> StrategySetting:
        setting = self.setting(strategy_id)
        if revision < setting.revision:
            return setting
        setting.mode = mode
        setting.lifecycle = lifecycle or self.lifecycle_for_mode(mode)
        setting.long_enabled = long_enabled
        setting.short_enabled = short_enabled
        setting.revision = revision
        setting.manual_lock = manual_lock
        setting.changed_by = changed_by
        setting.change_reason = change_reason
        setting.updated_ts_ms = updated_ts_ms
        self._revision_history[strategy_id][setting.revision] = self._setting_row(strategy_id)
        return setting

    def rollback(
        self,
        strategy_id: str,
        *,
        target_revision: int,
        expected_revision: int,
        source: StrategyChangeSource,
        reason: str,
        updated_ts_ms: int,
    ) -> StrategySetting:
        """과거 설정을 새 revision으로 복원해 감사 이력을 삭제하지 않는다."""

        setting = self.setting(strategy_id)
        if expected_revision != setting.revision:
            raise StrategyRevisionConflict(self._setting_row(strategy_id))
        if source is StrategyChangeSource.AUTO_GOVERNOR and setting.manual_lock:
            raise StrategyManualLockConflict(
                f"사용자가 고정한 전략 설정입니다: {strategy_id}"
            )
        target = self._revision_history[strategy_id].get(target_revision)
        if target is None:
            raise ValueError(f"복원할 전략 revision을 찾을 수 없습니다: {target_revision}")
        setting.mode = StrategyMode(str(target["mode"]))
        setting.lifecycle = StrategyLifecycle(str(target["lifecycle"]))
        setting.long_enabled = bool(target["long_enabled"])
        setting.short_enabled = bool(target["short_enabled"])
        setting.revision += 1
        setting.manual_lock = source is StrategyChangeSource.USER_UI
        setting.changed_by = source
        setting.change_reason = reason
        setting.updated_ts_ms = updated_ts_ms
        self._revision_history[strategy_id][setting.revision] = self._setting_row(strategy_id)
        return setting

    def evaluation_enabled(self, strategy_id: str, side: Side) -> bool:
        setting = self.setting(strategy_id)
        return setting.mode is not StrategyMode.OFF and setting.direction_enabled(side)

    def main_enabled(self, strategy_id: str, side: Side) -> bool:
        setting = self.setting(strategy_id)
        return setting.mode is StrategyMode.ACTIVE and setting.direction_enabled(side)

    def shadow_enabled(self, strategy_id: str, side: Side) -> bool:
        setting = self.setting(strategy_id)
        return setting.mode in {
            StrategyMode.ACTIVE,
            StrategyMode.SHADOW,
        } and setting.direction_enabled(side)

    def rows(self) -> list[dict[str, object]]:
        return [
            self._setting_row(descriptor.strategy_id)
            | {
                "strategy_id": descriptor.strategy_id,
                "display_name_ko": descriptor.display_name_ko,
                "short_name": descriptor.short_name,
                "summary_ko": descriptor.summary_ko,
                "stability": descriptor.stability.value,
                "supported_regimes": [regime.value for regime in descriptor.supported_regimes],
                "exit_style": descriptor.exit_style.value,
                "paper_only": descriptor.paper_only,
            }
            for descriptor in self._descriptors.values()
        ]

    def setting_row(self, strategy_id: str) -> dict[str, object]:
        """현재 설정과 revision을 공개 계약으로 복사한다."""

        return dict(self._setting_row(strategy_id))

    def revision_history(self, strategy_id: str) -> tuple[dict[str, object], ...]:
        """복구된 과거를 포함한 전략 설정 변경 이력을 revision 순으로 복사한다."""

        self.setting(strategy_id)
        return tuple(
            dict(row)
            for _, row in sorted(self._revision_history[strategy_id].items())
        )

    @staticmethod
    def mode_for_lifecycle(lifecycle: StrategyLifecycle) -> StrategyMode:
        if lifecycle is StrategyLifecycle.ACTIVE:
            return StrategyMode.ACTIVE
        if lifecycle in {StrategyLifecycle.SHADOW, StrategyLifecycle.CHALLENGER}:
            return StrategyMode.SHADOW
        return StrategyMode.OFF

    @staticmethod
    def lifecycle_for_mode(mode: StrategyMode) -> StrategyLifecycle:
        if mode is StrategyMode.ACTIVE:
            return StrategyLifecycle.ACTIVE
        if mode is StrategyMode.SHADOW:
            return StrategyLifecycle.SHADOW
        return StrategyLifecycle.RETIRED

    def _setting_row(self, strategy_id: str) -> dict[str, object]:
        setting = self.setting(strategy_id)
        return {
            "strategy_id": strategy_id,
            "mode": setting.mode.value,
            "lifecycle": setting.lifecycle.value,
            "long_enabled": setting.long_enabled,
            "short_enabled": setting.short_enabled,
            "settings_revision": setting.revision,
            "manual_lock": setting.manual_lock,
            "changed_by": setting.changed_by.value,
            "change_reason": setting.change_reason,
            "settings_updated_ts_ms": setting.updated_ts_ms,
        }
