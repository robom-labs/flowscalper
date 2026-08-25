"""전략 증거·운영상태·사용자 잠금을 함께 평가하는 보수적 PAPER governor다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from backend.app.strategies.registry import (
    LifecycleTransition,
    StrategyChangeSource,
    StrategyLifecycle,
    StrategyManualLockConflict,
    StrategyRegistry,
    StrategyRevisionConflict,
)


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class GovernanceEvidence:
    base_sample_size: int
    stress_sample_size: int
    base_expectancy_usdt: Decimal | None
    stress_expectancy_usdt: Decimal | None
    base_profit_factor: Decimal | None
    stress_profit_factor: Decimal | None
    sample_span_days: float
    regime_count: int
    dsr_probability: float | None
    pbo: float | None
    champion_expectancy_usdt: Decimal | None = None
    oos_expectancy_lower_bound_usdt: Decimal | None = None
    recent_expectancy_usdt: Decimal | None = None
    recent_profit_factor: Decimal | None = None
    parameter_robustness_passed: bool = False
    risk_contract_passed: bool = False
    independent_period_count: int = 0
    live_public_sample_size: int = 0
    cooldown_elapsed: bool = False
    strategy_correlation_abs: float | None = None
    full_oos_degraded_evaluations: int = 0
    recent_oos_degraded_evaluations: int = 0
    data_leakage: bool = False
    ledger_contamination: bool = False
    abnormal_order_loop: bool = False
    evaluation_period: str = "UNKNOWN"
    evaluated_ts_ms: int = 0
    data_fault: bool = False
    operational_fault: bool = False
    drawdown_breach: bool = False

    def as_dict(self) -> dict[str, object]:
        """원장과 증거 파일에 그대로 저장할 JSON 계약을 만든다."""

        return {
            field_name: str(value) if isinstance(value, Decimal) else value
            for field_name in self.__dataclass_fields__
            if (value := getattr(self, field_name)) is not None
        }

    @classmethod
    def from_reports(
        cls,
        base: Mapping[str, object],
        stress: Mapping[str, object],
        *,
        multiple_testing: Mapping[str, object] | None = None,
        champion_expectancy_usdt: object | None = None,
        operational: Mapping[str, object] | None = None,
    ) -> GovernanceEvidence:
        testing = multiple_testing or {}
        health = operational or {}
        return cls(
            base_sample_size=int(str(base.get("sample_size", 0))),
            stress_sample_size=int(str(stress.get("sample_size", 0))),
            base_expectancy_usdt=_decimal(base.get("expectancy_usdt")),
            stress_expectancy_usdt=_decimal(stress.get("expectancy_usdt")),
            base_profit_factor=_decimal(base.get("profit_factor")),
            stress_profit_factor=_decimal(stress.get("profit_factor")),
            sample_span_days=float(str(base.get("sample_span_days", 0))),
            regime_count=int(str(base.get("regime_count", 0))),
            dsr_probability=(
                float(str(testing["dsr_probability"]))
                if testing.get("dsr_probability") is not None
                else None
            ),
            pbo=float(str(testing["pbo"])) if testing.get("pbo") is not None else None,
            champion_expectancy_usdt=_decimal(champion_expectancy_usdt),
            oos_expectancy_lower_bound_usdt=_decimal(
                testing.get("oos_expectancy_lower_bound_usdt")
            ),
            recent_expectancy_usdt=_decimal(testing.get("recent_expectancy_usdt")),
            recent_profit_factor=_decimal(testing.get("recent_profit_factor")),
            parameter_robustness_passed=bool(
                testing.get("parameter_robustness_passed", False)
            ),
            risk_contract_passed=bool(testing.get("risk_contract_passed", False)),
            independent_period_count=int(str(testing.get("independent_period_count", 0))),
            live_public_sample_size=int(str(testing.get("live_public_sample_size", 0))),
            cooldown_elapsed=bool(testing.get("cooldown_elapsed", False)),
            strategy_correlation_abs=(
                float(str(testing["strategy_correlation_abs"]))
                if testing.get("strategy_correlation_abs") is not None
                else None
            ),
            full_oos_degraded_evaluations=int(
                str(testing.get("full_oos_degraded_evaluations", 0))
            ),
            recent_oos_degraded_evaluations=int(
                str(testing.get("recent_oos_degraded_evaluations", 0))
            ),
            data_leakage=bool(health.get("data_leakage", False)),
            ledger_contamination=bool(health.get("ledger_contamination", False)),
            abnormal_order_loop=bool(health.get("abnormal_order_loop", False)),
            evaluation_period=str(testing.get("evaluation_period", "UNKNOWN")),
            evaluated_ts_ms=int(str(testing.get("evaluated_ts_ms", 0))),
            data_fault=bool(health.get("data_fault", False)),
            operational_fault=bool(health.get("operational_fault", False)),
            drawdown_breach=bool(health.get("drawdown_breach", False)),
        )


@dataclass(frozen=True, slots=True)
class GovernanceAssessment:
    strategy_id: str
    current_lifecycle: StrategyLifecycle
    recommended_lifecycle: StrategyLifecycle
    reason_codes: tuple[str, ...]
    automatic_action_allowed: bool
    champion_id: str | None = None

    @property
    def transition_required(self) -> bool:
        return self.current_lifecycle is not self.recommended_lifecycle

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "current_lifecycle": self.current_lifecycle.value,
            "recommended_lifecycle": self.recommended_lifecycle.value,
            "reason_codes": list(self.reason_codes),
            "automatic_action_allowed": self.automatic_action_allowed,
            "transition_required": self.transition_required,
            "champion_id": self.champion_id,
        }


class StrategyGovernor:
    """충분한 비용후 OOS 증거 없이는 승격하지 않고 결함에는 빠르게 격리한다."""

    def assess(
        self,
        registry: StrategyRegistry,
        strategy_id: str,
        evidence: GovernanceEvidence,
    ) -> GovernanceAssessment:
        setting = registry.setting(strategy_id)
        current = setting.lifecycle
        champion_id = next(
            (
                row_id
                for row_id in registry.strategy_ids
                if registry.setting(row_id).lifecycle is StrategyLifecycle.ACTIVE
                and row_id != strategy_id
            ),
            None,
        )
        if setting.manual_lock:
            return GovernanceAssessment(
                strategy_id,
                current,
                current,
                ("USER_MANUAL_LOCK",),
                False,
                champion_id,
            )
        if current is StrategyLifecycle.RETIRED:
            return GovernanceAssessment(
                strategy_id,
                current,
                current,
                ("RETIRED_REQUIRES_USER_RESEARCH",),
                False,
                champion_id,
            )
        if any(
            (
                evidence.data_fault,
                evidence.operational_fault,
                evidence.drawdown_breach,
                evidence.data_leakage,
                evidence.ledger_contamination,
                evidence.abnormal_order_loop,
            )
        ):
            reasons = tuple(
                reason
                for active, reason in (
                    (evidence.data_fault, "DATA_FAULT"),
                    (evidence.operational_fault, "OPERATIONAL_FAULT"),
                    (evidence.drawdown_breach, "DRAWDOWN_BREACH"),
                    (evidence.data_leakage, "DATA_LEAKAGE"),
                    (evidence.ledger_contamination, "LEDGER_CONTAMINATION"),
                    (evidence.abnormal_order_loop, "ABNORMAL_ORDER_LOOP"),
                )
                if active
            )
            return GovernanceAssessment(
                strategy_id,
                current,
                StrategyLifecycle.QUARANTINED,
                reasons,
                True,
                champion_id,
            )
        if current is StrategyLifecycle.QUARANTINED:
            return GovernanceAssessment(
                strategy_id,
                current,
                current,
                ("QUARANTINE_REQUIRES_REVALIDATION",),
                False,
                champion_id,
            )
        if current is StrategyLifecycle.ACTIVE:
            full_degraded = (
                evidence.base_sample_size >= 30
                and evidence.base_expectancy_usdt is not None
                and evidence.base_expectancy_usdt < 0
                and evidence.base_profit_factor is not None
                and evidence.base_profit_factor < Decimal("0.90")
            )
            recent_degraded = (
                evidence.recent_expectancy_usdt is not None
                and evidence.recent_expectancy_usdt < 0
                and evidence.recent_profit_factor is not None
                and evidence.recent_profit_factor < Decimal("0.90")
            )
            degraded = (
                full_degraded
                and recent_degraded
                and evidence.full_oos_degraded_evaluations >= 2
                and evidence.recent_oos_degraded_evaluations >= 2
            )
            return GovernanceAssessment(
                strategy_id,
                current,
                StrategyLifecycle.QUARANTINED if degraded else current,
                ("COST_AFTER_DEGRADATION",) if degraded else ("ACTIVE_GATES_HEALTHY",),
                degraded,
                champion_id,
            )
        common_missing = self._common_gate_failures(evidence)
        if common_missing:
            return GovernanceAssessment(
                strategy_id,
                current,
                current,
                common_missing,
                False,
                champion_id,
            )
        if current is StrategyLifecycle.RESEARCH:
            return GovernanceAssessment(
                strategy_id,
                current,
                StrategyLifecycle.SHADOW,
                ("RESEARCH_OOS_GATES_PASSED",),
                True,
                champion_id,
            )
        if current is StrategyLifecycle.SHADOW:
            shadow_failures = self._shadow_gate_failures(evidence)
            if shadow_failures:
                return GovernanceAssessment(
                    strategy_id,
                    current,
                    current,
                    shadow_failures,
                    False,
                    champion_id,
                )
            return GovernanceAssessment(
                strategy_id,
                current,
                StrategyLifecycle.CHALLENGER,
                ("SHADOW_GATES_PASSED",),
                True,
                champion_id,
            )
        active_failures = (
            *self._shadow_gate_failures(evidence),
            *self._active_gate_failures(evidence),
        )
        if active_failures:
            return GovernanceAssessment(
                strategy_id,
                current,
                current,
                active_failures,
                False,
                champion_id,
            )
        return GovernanceAssessment(
            strategy_id,
            current,
            StrategyLifecycle.ACTIVE,
            ("CHALLENGER_BEATS_CHAMPION",),
            True,
            champion_id,
        )

    def apply(
        self,
        registry: StrategyRegistry,
        assessment: GovernanceAssessment,
        *,
        expected_revision: int,
        updated_ts_ms: int,
    ) -> tuple[dict[str, object], ...]:
        if not assessment.transition_required or not assessment.automatic_action_allowed:
            raise ValueError("자동 적용 가능한 lifecycle 전환이 아닙니다.")
        setting = registry.setting(assessment.strategy_id)
        if setting.revision != expected_revision:
            raise StrategyRevisionConflict(registry.setting_row(assessment.strategy_id))
        champion = (
            registry.setting(assessment.champion_id)
            if assessment.recommended_lifecycle is StrategyLifecycle.ACTIVE
            and assessment.champion_id is not None
            else None
        )
        if setting.manual_lock or (champion is not None and champion.manual_lock):
            raise StrategyManualLockConflict("사용자 고정으로 champion 교체를 차단했습니다.")
        transitions: list[LifecycleTransition] = []
        if champion is not None:
            transitions.append(
                LifecycleTransition(
                    strategy_id=assessment.champion_id or "",
                    lifecycle=StrategyLifecycle.CHALLENGER,
                    expected_revision=champion.revision,
                    reason=f"CHAMPION_REPLACED_BY:{assessment.strategy_id}",
                )
            )
        transitions.append(
            LifecycleTransition(
                strategy_id=assessment.strategy_id,
                lifecycle=assessment.recommended_lifecycle,
                expected_revision=expected_revision,
                reason=";".join(assessment.reason_codes),
            )
        )
        return registry.apply_lifecycle_transitions(
            tuple(transitions),
            source=StrategyChangeSource.AUTO_GOVERNOR,
            updated_ts_ms=updated_ts_ms,
        )

    @staticmethod
    def _common_gate_failures(evidence: GovernanceEvidence) -> tuple[str, ...]:
        gates = (
            (evidence.base_sample_size >= 30, "BASE_SAMPLE_LT_30"),
            (evidence.stress_sample_size >= 30, "STRESS_SAMPLE_LT_30"),
            (
                evidence.base_expectancy_usdt is not None
                and evidence.base_expectancy_usdt > 0,
                "BASE_EXPECTANCY_NOT_POSITIVE",
            ),
            (
                evidence.stress_expectancy_usdt is not None
                and evidence.stress_expectancy_usdt > 0,
                "STRESS_EXPECTANCY_NOT_POSITIVE",
            ),
            (
                evidence.base_profit_factor is not None
                and evidence.base_profit_factor >= Decimal("1.05"),
                "BASE_PF_LT_1_05",
            ),
            (
                evidence.stress_profit_factor is not None
                and evidence.stress_profit_factor >= Decimal("1.00"),
                "STRESS_PF_LT_1",
            ),
            (
                evidence.dsr_probability is not None
                and evidence.dsr_probability >= 0.80,
                "DSR_LT_0_80_OR_MISSING",
            ),
            (evidence.pbo is not None and evidence.pbo <= 0.50, "PBO_GT_0_50_OR_MISSING"),
            (
                evidence.oos_expectancy_lower_bound_usdt is not None
                and evidence.oos_expectancy_lower_bound_usdt > 0,
                "OOS_EXPECTANCY_LOWER_BOUND_NOT_POSITIVE",
            ),
            (evidence.parameter_robustness_passed, "PARAMETER_ROBUSTNESS_NOT_PASSED"),
            (evidence.risk_contract_passed, "RISK_CONTRACT_NOT_PASSED"),
            (evidence.independent_period_count >= 2, "INDEPENDENT_PERIODS_LT_2"),
        )
        return tuple(reason for passed, reason in gates if not passed)

    @staticmethod
    def _shadow_gate_failures(evidence: GovernanceEvidence) -> tuple[str, ...]:
        gates = (
            (evidence.live_public_sample_size >= 30, "LIVE_PUBLIC_SAMPLE_LT_30"),
            (evidence.sample_span_days >= 7, "SPAN_LT_7_DAYS"),
            (evidence.regime_count >= 2, "REGIME_COUNT_LT_2"),
            (evidence.cooldown_elapsed, "COOLDOWN_NOT_ELAPSED"),
        )
        return tuple(reason for passed, reason in gates if not passed)

    @staticmethod
    def _active_gate_failures(evidence: GovernanceEvidence) -> tuple[str, ...]:
        gates = (
            (evidence.live_public_sample_size >= 100, "LIVE_PUBLIC_SAMPLE_LT_100"),
            (evidence.base_sample_size >= 100, "BASE_SAMPLE_LT_100"),
            (evidence.stress_sample_size >= 100, "STRESS_SAMPLE_LT_100"),
            (evidence.sample_span_days >= 21, "SPAN_LT_21_DAYS"),
            (evidence.regime_count >= 3, "REGIME_COUNT_LT_3"),
            (
                evidence.base_profit_factor is not None
                and evidence.base_profit_factor >= Decimal("1.10"),
                "BASE_PF_LT_1_10",
            ),
            (
                evidence.dsr_probability is not None
                and evidence.dsr_probability >= 0.95,
                "DSR_LT_0_95",
            ),
            (evidence.pbo is not None and evidence.pbo <= 0.40, "PBO_GT_0_40"),
            (
                evidence.strategy_correlation_abs is not None
                and evidence.strategy_correlation_abs <= 0.80,
                "STRATEGY_CORRELATION_GT_0_80_OR_MISSING",
            ),
            (
                evidence.champion_expectancy_usdt is None
                or (
                    evidence.base_expectancy_usdt is not None
                    and evidence.base_expectancy_usdt
                    > evidence.champion_expectancy_usdt
                ),
                "DOES_NOT_BEAT_CHAMPION",
            ),
        )
        return tuple(reason for passed, reason in gates if not passed)
