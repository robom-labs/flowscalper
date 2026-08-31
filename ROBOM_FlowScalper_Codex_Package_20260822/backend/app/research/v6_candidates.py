# V6의 기존 전략 승계 후보와 ARMED_SETUP 연구 경계를 사전등록한다.

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum


class V6ResearchState(StrEnum):
    PREREGISTERED = "PREREGISTERED"
    ARMED_SETUP = "ARMED_SETUP"
    INVALIDATED = "INVALIDATED"
    TRIGGERED = "TRIGGERED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class V6VariantSpec:
    strategy_id: str
    family_id: str
    baseline_strategy_ids: tuple[str, ...]
    setup_timeframe_seconds: int
    trigger_timeframe_seconds: int
    validity_minutes: int
    maximum_completed_trigger_bars: int
    entry_rules_ko: tuple[str, ...]
    invalidation_rules_ko: tuple[str, ...]
    exit_ablation_ids: tuple[str, ...]
    base_cost_coverage_minimum: float
    stress_cost_coverage_minimum: float
    base_sample_size_minimum: int = 100
    stress_sample_size_minimum: int = 100
    current_variant: bool = False
    runtime_registered: bool = False
    live_shadow_enabled: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.strategy_id or not self.family_id or not self.baseline_strategy_ids:
            raise ValueError("V6 후보의 전략·family·baseline 식별자가 필요합니다.")
        if not self.strategy_id.endswith(("_V2", "_V3")):
            raise ValueError("V6 후보 ID에는 의미 변경 버전이 포함돼야 합니다.")
        if self.setup_timeframe_seconds <= 0 or self.trigger_timeframe_seconds <= 0:
            raise ValueError("setup과 trigger 시간구간은 양수여야 합니다.")
        if self.validity_minutes <= 0 or self.maximum_completed_trigger_bars <= 0:
            raise ValueError("setup 유효시간과 trigger 봉 수는 양수여야 합니다.")
        if not self.entry_rules_ko or not self.invalidation_rules_ko:
            raise ValueError("V6 후보의 진입·무효화 규칙을 사전등록해야 합니다.")
        if not self.exit_ablation_ids:
            raise ValueError("V6 후보는 exit ablation을 사전등록해야 합니다.")
        if self.stress_cost_coverage_minimum > self.base_cost_coverage_minimum:
            raise ValueError("STRESS 비용회수 하한은 BASE 하한보다 클 수 없습니다.")
        if self.base_sample_size_minimum <= 0 or self.stress_sample_size_minimum <= 0:
            raise ValueError("V6 비교 표본 하한은 양수여야 합니다.")
        if (
            self.current_variant
            or self.runtime_registered
            or self.live_shadow_enabled
            or not self.paper_only
        ):
            raise ValueError("검증 전 V6 후보는 offline PAPER 연구에만 머물러야 합니다.")


@dataclass(frozen=True, slots=True)
class ArmedSetup:
    strategy_id: str
    setup_id: str
    side: str
    armed_ts_ms: int
    expires_ts_ms: int
    maximum_completed_trigger_bars: int
    completed_trigger_bars: int = 0
    last_completed_bar_ts_ms: int | None = None
    last_structure_valid: bool | None = None
    last_trigger_passed: bool | None = None
    state: V6ResearchState = V6ResearchState.ARMED_SETUP

    def __post_init__(self) -> None:
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("ARMED_SETUP 방향은 LONG 또는 SHORT여야 합니다.")
        if self.expires_ts_ms <= self.armed_ts_ms:
            raise ValueError("ARMED_SETUP 만료시각은 시작 뒤여야 합니다.")
        if not 0 <= self.completed_trigger_bars <= self.maximum_completed_trigger_bars:
            raise ValueError("완료 trigger 봉 수가 사전등록 상한을 벗어났습니다.")
        if (
            self.last_completed_bar_ts_ms is not None
            and self.last_completed_bar_ts_ms < self.armed_ts_ms
        ):
            raise ValueError("마지막 완료 trigger 봉은 ARMED_SETUP 시작 전일 수 없습니다.")
        fingerprint = (
            self.last_completed_bar_ts_ms,
            self.last_structure_valid,
            self.last_trigger_passed,
        )
        if any(value is None for value in fingerprint) and any(
            value is not None for value in fingerprint
        ):
            raise ValueError("마지막 trigger 평가 fingerprint는 전체가 함께 존재해야 합니다.")

    def advance(
        self,
        *,
        completed_bar_ts_ms: int,
        structure_valid: bool,
        trigger_passed: bool,
    ) -> ArmedSetup:
        """완료봉 하나만 소비하고 한 setup의 중복 trigger를 막는다."""

        if completed_bar_ts_ms < self.armed_ts_ms:
            raise ValueError("ARMED_SETUP 이전 봉은 trigger 평가에 사용할 수 없습니다.")
        if self.last_completed_bar_ts_ms is not None:
            if completed_bar_ts_ms == self.last_completed_bar_ts_ms:
                if (
                    self.last_structure_valid is structure_valid
                    and self.last_trigger_passed is trigger_passed
                ):
                    return self
                raise ValueError(
                    "동일 완료 trigger 봉의 구조·trigger 결과가 서로 다릅니다."
                )
            if completed_bar_ts_ms < self.last_completed_bar_ts_ms:
                raise ValueError("완료 trigger 봉은 event-time 순서대로 평가해야 합니다.")
        if self.state is not V6ResearchState.ARMED_SETUP:
            return self
        if not structure_valid:
            return self._with_state(
                V6ResearchState.INVALIDATED,
                last_completed_bar_ts_ms=completed_bar_ts_ms,
                last_structure_valid=structure_valid,
                last_trigger_passed=trigger_passed,
            )
        next_count = self.completed_trigger_bars + 1
        if completed_bar_ts_ms > self.expires_ts_ms:
            return self._with_state(
                V6ResearchState.EXPIRED,
                completed_bars=next_count,
                last_completed_bar_ts_ms=completed_bar_ts_ms,
                last_structure_valid=structure_valid,
                last_trigger_passed=trigger_passed,
            )
        if trigger_passed:
            return self._with_state(
                V6ResearchState.TRIGGERED,
                completed_bars=next_count,
                last_completed_bar_ts_ms=completed_bar_ts_ms,
                last_structure_valid=structure_valid,
                last_trigger_passed=trigger_passed,
            )
        if next_count >= self.maximum_completed_trigger_bars:
            return self._with_state(
                V6ResearchState.EXPIRED,
                completed_bars=next_count,
                last_completed_bar_ts_ms=completed_bar_ts_ms,
                last_structure_valid=structure_valid,
                last_trigger_passed=trigger_passed,
            )
        return ArmedSetup(
            strategy_id=self.strategy_id,
            setup_id=self.setup_id,
            side=self.side,
            armed_ts_ms=self.armed_ts_ms,
            expires_ts_ms=self.expires_ts_ms,
            maximum_completed_trigger_bars=self.maximum_completed_trigger_bars,
            completed_trigger_bars=next_count,
            last_completed_bar_ts_ms=completed_bar_ts_ms,
            last_structure_valid=structure_valid,
            last_trigger_passed=trigger_passed,
        )

    def _with_state(
        self,
        state: V6ResearchState,
        *,
        completed_bars: int | None = None,
        last_completed_bar_ts_ms: int | None = None,
        last_structure_valid: bool | None = None,
        last_trigger_passed: bool | None = None,
    ) -> ArmedSetup:
        return ArmedSetup(
            strategy_id=self.strategy_id,
            setup_id=self.setup_id,
            side=self.side,
            armed_ts_ms=self.armed_ts_ms,
            expires_ts_ms=self.expires_ts_ms,
            maximum_completed_trigger_bars=self.maximum_completed_trigger_bars,
            completed_trigger_bars=(
                self.completed_trigger_bars if completed_bars is None else completed_bars
            ),
            last_completed_bar_ts_ms=(
                self.last_completed_bar_ts_ms
                if last_completed_bar_ts_ms is None
                else last_completed_bar_ts_ms
            ),
            last_structure_valid=(
                self.last_structure_valid
                if last_structure_valid is None
                else last_structure_valid
            ),
            last_trigger_passed=(
                self.last_trigger_passed
                if last_trigger_passed is None
                else last_trigger_passed
            ),
            state=state,
        )


def v6_preregistered_variants() -> tuple[V6VariantSpec, ...]:
    """V2를 덮어쓰지 않는 네 개의 offline challenger를 반환한다."""

    common_trend_rules = (
        "완료 4시간 close > EMA20 > EMA50와 EMA20 기울기/ATR >= 0.10",
        "완료 1시간 close > EMA20 > EMA50, ADX14 >= 22, DMI 차이 >= 5, ER20 >= 0.35",
        "완료 5분 trigger의 body·CLV·RVOL·taker imbalance와 주문흐름 3개 중 2개",
    )
    common_invalidations = (
        "상위 시간구간 추세 해제",
        "구조 origin 또는 EMA50 허용범위 이탈",
        "공개시장 데이터 degraded",
        "사전등록 유효시간 만료",
    )
    variants = (
        V6VariantSpec(
            strategy_id="TREND_PULLBACK_RECLAIM_15M_V3",
            family_id="TREND_PULLBACK",
            baseline_strategy_ids=("TREND_PULLBACK_RECLAIM_15M_V2",),
            setup_timeframe_seconds=900,
            trigger_timeframe_seconds=300,
            validity_minutes=45,
            maximum_completed_trigger_bars=9,
            entry_rules_ko=common_trend_rules
            + ("직전 impulse >= 1.5ATR, 되돌림 0.25..0.65, EMA20 주변 눌림",),
            invalidation_rules_ko=common_invalidations,
            exit_ablation_ids=("V2_FIXED_TP", "V3_PARTIAL_RUNNER_2_8_ATR"),
            base_cost_coverage_minimum=2.5,
            stress_cost_coverage_minimum=1.5,
        ),
        V6VariantSpec(
            strategy_id="MULTISPEED_TREND_RECLAIM_30M_V3",
            family_id="TREND_PULLBACK",
            baseline_strategy_ids=("MULTISPEED_TREND_RECLAIM_30M_V2",),
            setup_timeframe_seconds=1_800,
            trigger_timeframe_seconds=900,
            validity_minutes=90,
            maximum_completed_trigger_bars=6,
            entry_rules_ko=common_trend_rules + ("완료 30분 조정 뒤 1시간 추세 방향 재합류",),
            invalidation_rules_ko=common_invalidations,
            exit_ablation_ids=("V2_FIXED_TP", "V3_PARTIAL_RUNNER_2_8_ATR"),
            base_cost_coverage_minimum=2.5,
            stress_cost_coverage_minimum=1.5,
        ),
        V6VariantSpec(
            strategy_id="BREAKOUT_RETEST_30M_V3",
            family_id="BREAKOUT_RUNNER",
            baseline_strategy_ids=("BREAKOUT_RETEST_30M_V2",),
            setup_timeframe_seconds=1_800,
            trigger_timeframe_seconds=300,
            validity_minutes=90,
            maximum_completed_trigger_bars=18,
            entry_rules_ko=(
                "완료 1시간 추세, ADX14 >= 22, ER20 >= 0.35",
                "완료 30분 Donchian20 + 0.10ATR 돌파와 body/CLV/RVOL/extension 제한",
                "다음 3개 완료 30분봉(= 완료 5분 trigger 최대 18개) 안 retest와 재돌파",
            ),
            invalidation_rules_ko=(
                "retest가 돌파수준 허용범위를 이탈",
                "1시간 추세 해제",
                "공개시장 데이터 degraded",
                "3개 완료 30분봉(= 완료 5분 trigger 18개) 또는 90분 만료",
            ),
            exit_ablation_ids=("V2_FIXED_TP", "V3_PARTIAL_RUNNER_3_5_ATR_DONCHIAN10"),
            base_cost_coverage_minimum=2.5,
            stress_cost_coverage_minimum=1.5,
        ),
        V6VariantSpec(
            strategy_id="EXHAUSTION_VWAP_REENTRY_V2",
            family_id="EXHAUSTION_REVERSION",
            baseline_strategy_ids=("VWAP_EXHAUSTION_REVERSION_V1", "LSA_REVERSAL_V1"),
            setup_timeframe_seconds=900,
            trigger_timeframe_seconds=300,
            validity_minutes=45,
            maximum_completed_trigger_bars=9,
            entry_rules_ko=(
                "완료 1시간 ADX14 < 18, ER20 < 0.25인 비추세 regime",
                "VWAP robust z 또는 1시간 return z 과잉이탈",
                "청산·OI·가격진전·refill·microprice·CVD 소진 조건 3개 이상",
                "완료 15분 VWAP 재진입과 완료 5분 구조 재진입",
            ),
            invalidation_rules_ko=(
                "TREND regime 전환",
                "flush 구조 이탈",
                "공개시장 데이터 degraded",
                "사전등록 유효시간 만료",
            ),
            exit_ablation_ids=("V1_REVERSION_FIXED", "V2_POC_EMA50_1_5R_CONSERVATIVE"),
            base_cost_coverage_minimum=2.5,
            stress_cost_coverage_minimum=1.5,
        ),
    )
    if len({variant.strategy_id for variant in variants}) != len(variants):
        raise ValueError("V6 연구 후보 ID가 중복됐습니다.")
    return variants


def v6_preregistration_manifest(*, source_commit: str) -> dict[str, object]:
    variants = v6_preregistered_variants()
    rows = [asdict(variant) for variant in variants]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "status": "PREREGISTERED_NOT_EXECUTED",
        "source_commit": source_commit,
        "variant_count": len(rows),
        "current_variant_changes": 0,
        "runtime_registered_count": 0,
        "live_shadow_count": 0,
        "selection_or_promotion_performed": False,
        "comparison_required": [
            "same frozen input V2 vs V3 entry",
            "same entry fixed vs runner exit",
            "orderflow filter OFF vs ON",
            "bar approximation vs event replay",
        ],
        "promotion_gates": {
            "base_expectancy_positive": True,
            "stress_expectancy_positive": True,
            "v3_expectancy_better_than_v2": True,
            "drawdown_not_worse": True,
            "cost_burden_improved": True,
            "variant_profile_sample_minimums_enforced": True,
            "variant_cost_coverage_minimums_enforced": True,
            "oos_lower_bound_positive": True,
            "dsr_minimum": 0.95,
            "pbo_maximum": 0.20,
            "operational_regression": False,
        },
        "variants": rows,
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "auth_required": False,
        "funding_readiness": "NOT_READY",
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return manifest
