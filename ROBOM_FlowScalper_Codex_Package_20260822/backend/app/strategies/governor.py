"""전략 증거·운영상태·사용자 잠금을 함께 평가하는 보수적 PAPER governor다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from backend.app.strategies.family import StrategyFamilyId, StrategyRole
from backend.app.strategies.registry import (
    LifecycleTransition,
    StrategyChangeSource,
    StrategyLifecycle,
    StrategyManualLockConflict,
    StrategyRegistry,
    StrategyRevisionConflict,
)

GOVERNANCE_EVIDENCE_MAX_AGE_MS = 60_000


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _mapping_decimal(
    row: Mapping[str, object],
    field_name: str,
    nested_field_name: str | None = None,
) -> Decimal | None:
    value = row.get(field_name)
    if nested_field_name is not None:
        value = value.get(nested_field_name) if isinstance(value, Mapping) else None
    return _decimal(value)


def _first_decimal(*values: object | None) -> Decimal | None:
    return next((_decimal(value) for value in values if value is not None), None)


def _fresh_evidence_timestamp(
    evidence_ts_ms: object,
    *,
    assessment_ts_ms: object,
) -> bool:
    if (
        not isinstance(evidence_ts_ms, int)
        or isinstance(evidence_ts_ms, bool)
        or not isinstance(assessment_ts_ms, int)
        or isinstance(assessment_ts_ms, bool)
        or evidence_ts_ms <= 0
        or assessment_ts_ms <= 0
    ):
        return False
    age_ms = assessment_ts_ms - evidence_ts_ms
    return 0 <= age_ms <= GOVERNANCE_EVIDENCE_MAX_AGE_MS


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
    recent_stress_expectancy_usdt: Decimal | None = None
    recent_stress_profit_factor: Decimal | None = None
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
    operational_health_passed: bool | None = None
    operational_health_evaluated_ts_ms: int | None = None
    data_fault: bool = False
    operational_fault: bool = False
    drawdown_breach: bool = False
    base_win_rate: Decimal | None = None
    stress_win_rate: Decimal | None = None
    unique_opportunity_count: int = 0
    base_win_rate_ci95_lower: Decimal | None = None
    stress_win_rate_ci95_lower: Decimal | None = None
    base_payoff_ratio: Decimal | None = None
    stress_payoff_ratio: Decimal | None = None
    base_return_skew: Decimal | None = None
    stress_return_skew: Decimal | None = None
    base_largest_trade_contribution: Decimal | None = None
    stress_largest_trade_contribution: Decimal | None = None
    base_cost_coverage: Decimal | None = None
    stress_cost_coverage: Decimal | None = None

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
            recent_stress_expectancy_usdt=_decimal(testing.get("recent_stress_expectancy_usdt")),
            recent_stress_profit_factor=_decimal(testing.get("recent_stress_profit_factor")),
            parameter_robustness_passed=(
                testing.get("parameter_robustness_passed") is True
            ),
            risk_contract_passed=testing.get("risk_contract_passed") is True,
            independent_period_count=int(str(testing.get("independent_period_count", 0))),
            live_public_sample_size=int(str(testing.get("live_public_sample_size", 0))),
            cooldown_elapsed=testing.get("cooldown_elapsed") is True,
            strategy_correlation_abs=(
                float(str(testing["strategy_correlation_abs"]))
                if testing.get("strategy_correlation_abs") is not None
                else None
            ),
            full_oos_degraded_evaluations=int(str(testing.get("full_oos_degraded_evaluations", 0))),
            recent_oos_degraded_evaluations=int(
                str(testing.get("recent_oos_degraded_evaluations", 0))
            ),
            data_leakage=bool(health.get("data_leakage", False)),
            ledger_contamination=bool(health.get("ledger_contamination", False)),
            abnormal_order_loop=bool(health.get("abnormal_order_loop", False)),
            evaluation_period=str(
                health.get(
                    "evaluation_period",
                    testing.get("evaluation_period", "UNKNOWN"),
                )
            ),
            evaluated_ts_ms=int(
                str(
                    health.get(
                        "evaluated_ts_ms",
                        testing.get("evaluated_ts_ms", 0),
                    )
                )
            ),
            operational_health_passed=(
                True
                if health.get("operational_health_passed") is True
                else False
                if health.get("operational_health_passed") is False
                else None
            ),
            operational_health_evaluated_ts_ms=(
                int(str(health["operational_health_evaluated_ts_ms"]))
                if health.get("operational_health_evaluated_ts_ms") is not None
                else None
            ),
            data_fault=bool(health.get("data_fault", False)),
            operational_fault=bool(health.get("operational_fault", False)),
            drawdown_breach=bool(health.get("drawdown_breach", False)),
            base_win_rate=_decimal(base.get("win_rate")),
            stress_win_rate=_decimal(stress.get("win_rate")),
            unique_opportunity_count=int(
                str(
                    testing.get(
                        "unique_opportunity_count",
                        testing.get(
                            "unique_market_opportunity_count",
                            min(
                                int(str(base.get("unique_opportunity_count", 0))),
                                int(str(stress.get("unique_opportunity_count", 0))),
                            ),
                        ),
                    )
                )
            ),
            base_win_rate_ci95_lower=_first_decimal(
                testing.get("base_win_rate_ci95_lower"),
                _mapping_decimal(base, "win_rate_ci95", "lower"),
            ),
            stress_win_rate_ci95_lower=_first_decimal(
                testing.get("stress_win_rate_ci95_lower"),
                _mapping_decimal(stress, "win_rate_ci95", "lower"),
            ),
            base_payoff_ratio=_first_decimal(
                testing.get("base_payoff_ratio"), base.get("payoff_ratio")
            ),
            stress_payoff_ratio=_first_decimal(
                testing.get("stress_payoff_ratio"), stress.get("payoff_ratio")
            ),
            base_return_skew=_first_decimal(
                testing.get("base_return_skew"), base.get("return_skew")
            ),
            stress_return_skew=_first_decimal(
                testing.get("stress_return_skew"), stress.get("return_skew")
            ),
            base_largest_trade_contribution=_first_decimal(
                testing.get("base_largest_trade_contribution"),
                base.get("largest_trade_contribution"),
            ),
            stress_largest_trade_contribution=_first_decimal(
                testing.get("stress_largest_trade_contribution"),
                stress.get("largest_trade_contribution"),
            ),
            base_cost_coverage=_first_decimal(
                testing.get("base_cost_coverage"), base.get("cost_coverage")
            ),
            stress_cost_coverage=_first_decimal(
                testing.get("stress_cost_coverage"), stress.get("cost_coverage")
            ),
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
        *,
        assessment_ts_ms: int | None = None,
    ) -> GovernanceAssessment:
        setting = registry.setting(strategy_id)
        descriptor = registry.descriptor(strategy_id)
        current = setting.lifecycle
        supported_regime_count = len(descriptor.supported_regimes)
        shadow_required_regime_count = min(2, supported_regime_count)
        active_required_regime_count = min(3, supported_regime_count)
        champion_id = next(
            (
                row_id
                for row_id in registry.strategy_ids
                if registry.setting(row_id).lifecycle is StrategyLifecycle.ACTIVE
                and row_id != strategy_id
                and registry.descriptor(row_id).family_id is descriptor.family_id
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
        if descriptor.role is not StrategyRole.ENTRY:
            return GovernanceAssessment(
                strategy_id,
                current,
                current,
                (f"ROLE_{descriptor.role.value}_NOT_ENTRY_RANKED",),
                False,
                champion_id,
            )
        if not descriptor.is_current_variant:
            if current is StrategyLifecycle.ACTIVE:
                return GovernanceAssessment(
                    strategy_id,
                    current,
                    StrategyLifecycle.CHALLENGER,
                    ("NON_CURRENT_VARIANT_SHADOW_ONLY",),
                    True,
                    champion_id,
                )
            if current is StrategyLifecycle.CHALLENGER:
                return GovernanceAssessment(
                    strategy_id,
                    current,
                    current,
                    ("NON_CURRENT_VARIANT_NOT_ACTIVE_ELIGIBLE",),
                    False,
                    champion_id,
                )
        if current is StrategyLifecycle.ACTIVE:
            full_base_degraded = (
                evidence.base_sample_size >= 30
                and evidence.base_expectancy_usdt is not None
                and evidence.base_expectancy_usdt < 0
                and evidence.base_profit_factor is not None
                and evidence.base_profit_factor < Decimal("0.90")
            )
            full_stress_degraded = (
                evidence.stress_sample_size >= 30
                and evidence.stress_expectancy_usdt is not None
                and evidence.stress_expectancy_usdt < 0
                and evidence.stress_profit_factor is not None
                and evidence.stress_profit_factor < Decimal("0.90")
            )
            recent_base_degraded = (
                evidence.recent_expectancy_usdt is not None
                and evidence.recent_expectancy_usdt < 0
                and evidence.recent_profit_factor is not None
                and evidence.recent_profit_factor < Decimal("0.90")
            )
            recent_stress_degraded = (
                evidence.recent_stress_expectancy_usdt is not None
                and evidence.recent_stress_expectancy_usdt < 0
                and evidence.recent_stress_profit_factor is not None
                and evidence.recent_stress_profit_factor < Decimal("0.90")
            )
            cost_degraded = (
                (
                    (full_base_degraded and recent_base_degraded)
                    or (full_stress_degraded and recent_stress_degraded)
                )
                and evidence.full_oos_degraded_evaluations >= 2
                and evidence.recent_oos_degraded_evaluations >= 2
            )
            degraded = cost_degraded
            reason_codes = ("COST_AFTER_DEGRADATION",) if cost_degraded else ()
            return GovernanceAssessment(
                strategy_id,
                current,
                StrategyLifecycle.QUARANTINED if degraded else current,
                reason_codes if degraded else ("ACTIVE_GATES_HEALTHY",),
                degraded,
                champion_id,
            )
        common_missing = self._common_gate_failures(
            evidence,
            assessment_ts_ms=assessment_ts_ms,
        )
        if common_missing:
            return GovernanceAssessment(
                strategy_id,
                current,
                current,
                common_missing,
                False,
                champion_id,
            )
        family_missing = self.family_gate_failures(descriptor.family_id, evidence)
        if family_missing:
            return GovernanceAssessment(
                strategy_id,
                current,
                current,
                family_missing,
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
            shadow_failures = self._shadow_gate_failures(
                evidence,
                required_regime_count=shadow_required_regime_count,
            )
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
            *self._shadow_gate_failures(
                evidence,
                required_regime_count=shadow_required_regime_count,
            ),
            *self._active_gate_failures(
                evidence,
                required_regime_count=active_required_regime_count,
            ),
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
    def _common_gate_failures(
        evidence: GovernanceEvidence,
        *,
        assessment_ts_ms: int | None = None,
    ) -> tuple[str, ...]:
        gates = (
            (
                evidence.operational_health_passed is True
                and _fresh_evidence_timestamp(
                    evidence.operational_health_evaluated_ts_ms,
                    assessment_ts_ms=assessment_ts_ms,
                )
                and _fresh_evidence_timestamp(
                    evidence.evaluated_ts_ms,
                    assessment_ts_ms=assessment_ts_ms,
                )
                and isinstance(evidence.evaluation_period, str)
                and bool(evidence.evaluation_period.strip())
                and evidence.evaluation_period.strip().upper() != "UNKNOWN",
                "OPERATIONAL_HEALTH_NOT_PROVEN",
            ),
            (
                evidence.base_expectancy_usdt is not None and evidence.base_expectancy_usdt > 0,
                "BASE_EXPECTANCY_NOT_POSITIVE",
            ),
            (
                evidence.stress_expectancy_usdt is not None and evidence.stress_expectancy_usdt > 0,
                "STRESS_EXPECTANCY_NOT_POSITIVE",
            ),
            (
                evidence.dsr_probability is not None and evidence.dsr_probability >= 0.95,
                "DSR_LT_0_95_OR_MISSING",
            ),
            (evidence.pbo is not None and evidence.pbo <= 0.20, "PBO_GT_0_20_OR_MISSING"),
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
    def family_gate_failures(
        family_id: StrategyFamilyId,
        evidence: GovernanceEvidence,
    ) -> tuple[str, ...]:
        """사전등록된 family별 표본·payoff·PF 계약만 적용한다."""

        gates: tuple[tuple[bool, str], ...]
        if family_id is StrategyFamilyId.TREND_PULLBACK:
            gates = (
                (evidence.unique_opportunity_count >= 150, "UNIQUE_OPPORTUNITIES_LT_150"),
                (
                    evidence.base_win_rate is not None
                    and evidence.base_win_rate >= Decimal("0.40"),
                    "BASE_WIN_RATE_LT_0_40_OR_MISSING",
                ),
                (
                    evidence.stress_win_rate is not None
                    and evidence.stress_win_rate >= Decimal("0.40"),
                    "STRESS_WIN_RATE_LT_0_40_OR_MISSING",
                ),
                (
                    evidence.base_win_rate_ci95_lower is not None
                    and evidence.base_win_rate_ci95_lower >= Decimal("0.32"),
                    "BASE_WILSON_LOWER_LT_0_32_OR_MISSING",
                ),
                (
                    evidence.stress_win_rate_ci95_lower is not None
                    and evidence.stress_win_rate_ci95_lower >= Decimal("0.32"),
                    "STRESS_WILSON_LOWER_LT_0_32_OR_MISSING",
                ),
                (
                    evidence.base_payoff_ratio is not None
                    and evidence.base_payoff_ratio >= Decimal("1.50"),
                    "BASE_PAYOFF_LT_1_50_OR_MISSING",
                ),
                (
                    evidence.stress_payoff_ratio is not None
                    and evidence.stress_payoff_ratio >= Decimal("1.50"),
                    "STRESS_PAYOFF_LT_1_50_OR_MISSING",
                ),
                (
                    evidence.base_profit_factor is not None
                    and evidence.base_profit_factor >= Decimal("1.20"),
                    "BASE_PF_LT_1_20_OR_MISSING",
                ),
                (
                    evidence.stress_profit_factor is not None
                    and evidence.stress_profit_factor >= Decimal("1.20"),
                    "STRESS_PF_LT_1_20_OR_MISSING",
                ),
            )
        elif family_id is StrategyFamilyId.BREAKOUT_RUNNER:
            gates = (
                (evidence.unique_opportunity_count >= 150, "UNIQUE_OPPORTUNITIES_LT_150"),
                (
                    evidence.base_payoff_ratio is not None
                    and evidence.base_payoff_ratio >= Decimal("2.00"),
                    "BASE_PAYOFF_LT_2_OR_MISSING",
                ),
                (
                    evidence.stress_payoff_ratio is not None
                    and evidence.stress_payoff_ratio >= Decimal("2.00"),
                    "STRESS_PAYOFF_LT_2_OR_MISSING",
                ),
                (
                    evidence.base_profit_factor is not None
                    and evidence.base_profit_factor >= Decimal("1.25"),
                    "BASE_PF_LT_1_25_OR_MISSING",
                ),
                (
                    evidence.stress_profit_factor is not None
                    and evidence.stress_profit_factor >= Decimal("1.25"),
                    "STRESS_PF_LT_1_25_OR_MISSING",
                ),
                (
                    evidence.base_return_skew is not None and evidence.base_return_skew > 0,
                    "BASE_RETURN_SKEW_NOT_POSITIVE",
                ),
                (
                    evidence.stress_return_skew is not None and evidence.stress_return_skew > 0,
                    "STRESS_RETURN_SKEW_NOT_POSITIVE",
                ),
                (
                    evidence.base_largest_trade_contribution is not None
                    and evidence.base_largest_trade_contribution < Decimal("0.10"),
                    "BASE_LARGEST_TRADE_CONTRIBUTION_NOT_LT_0_10",
                ),
                (
                    evidence.stress_largest_trade_contribution is not None
                    and evidence.stress_largest_trade_contribution < Decimal("0.10"),
                    "STRESS_LARGEST_TRADE_CONTRIBUTION_NOT_LT_0_10",
                ),
            )
        elif family_id is StrategyFamilyId.EXHAUSTION_REVERSION:
            gates = (
                (evidence.unique_opportunity_count >= 150, "UNIQUE_OPPORTUNITIES_LT_150"),
                (
                    evidence.base_win_rate_ci95_lower is not None
                    and evidence.base_win_rate_ci95_lower >= Decimal("0.38"),
                    "BASE_WILSON_LOWER_LT_0_38_OR_MISSING",
                ),
                (
                    evidence.stress_win_rate_ci95_lower is not None
                    and evidence.stress_win_rate_ci95_lower >= Decimal("0.38"),
                    "STRESS_WILSON_LOWER_LT_0_38_OR_MISSING",
                ),
                (
                    evidence.base_payoff_ratio is not None
                    and evidence.base_payoff_ratio >= Decimal("1.30"),
                    "BASE_PAYOFF_LT_1_30_OR_MISSING",
                ),
                (
                    evidence.stress_payoff_ratio is not None
                    and evidence.stress_payoff_ratio >= Decimal("1.30"),
                    "STRESS_PAYOFF_LT_1_30_OR_MISSING",
                ),
                (
                    evidence.base_profit_factor is not None
                    and evidence.base_profit_factor >= Decimal("1.15"),
                    "BASE_PF_LT_1_15_OR_MISSING",
                ),
                (
                    evidence.stress_profit_factor is not None
                    and evidence.stress_profit_factor >= Decimal("1.15"),
                    "STRESS_PF_LT_1_15_OR_MISSING",
                ),
            )
        elif family_id is StrategyFamilyId.ORDERFLOW_CONFIRMATION:
            gates = (
                (evidence.unique_opportunity_count >= 1_000, "UNIQUE_OPPORTUNITIES_LT_1000"),
                (
                    evidence.base_cost_coverage is not None
                    and evidence.base_cost_coverage >= Decimal("4.0"),
                    "BASE_COST_COVERAGE_LT_4_OR_MISSING",
                ),
                (
                    evidence.stress_cost_coverage is not None
                    and evidence.stress_cost_coverage >= Decimal("4.0"),
                    "STRESS_COST_COVERAGE_LT_4_OR_MISSING",
                ),
                (
                    evidence.base_profit_factor is not None
                    and evidence.base_profit_factor >= Decimal("1.15"),
                    "BASE_PF_LT_1_15_OR_MISSING",
                ),
                (
                    evidence.stress_profit_factor is not None
                    and evidence.stress_profit_factor >= Decimal("1.15"),
                    "STRESS_PF_LT_1_15_OR_MISSING",
                ),
            )
        else:
            return ("FAMILY_GATE_NOT_PREREGISTERED",)
        return tuple(reason for passed, reason in gates if not passed)

    @staticmethod
    def _shadow_gate_failures(
        evidence: GovernanceEvidence,
        *,
        required_regime_count: int,
    ) -> tuple[str, ...]:
        gates = (
            (evidence.live_public_sample_size >= 30, "LIVE_PUBLIC_SAMPLE_LT_30"),
            (evidence.sample_span_days >= 7, "SPAN_LT_7_DAYS"),
            (
                evidence.regime_count >= required_regime_count,
                f"REGIME_COUNT_LT_{required_regime_count}",
            ),
            (evidence.cooldown_elapsed, "COOLDOWN_NOT_ELAPSED"),
        )
        return tuple(reason for passed, reason in gates if not passed)

    @staticmethod
    def _active_gate_failures(
        evidence: GovernanceEvidence,
        *,
        required_regime_count: int,
    ) -> tuple[str, ...]:
        gates = (
            (evidence.live_public_sample_size >= 100, "LIVE_PUBLIC_SAMPLE_LT_100"),
            (evidence.base_sample_size >= 100, "BASE_SAMPLE_LT_100"),
            (evidence.stress_sample_size >= 100, "STRESS_SAMPLE_LT_100"),
            (evidence.sample_span_days >= 21, "SPAN_LT_21_DAYS"),
            (
                evidence.regime_count >= required_regime_count,
                f"REGIME_COUNT_LT_{required_regime_count}",
            ),
            (
                evidence.base_profit_factor is not None
                and evidence.base_profit_factor >= Decimal("1.10"),
                "BASE_PF_LT_1_10",
            ),
            (
                evidence.dsr_probability is not None and evidence.dsr_probability >= 0.95,
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
                    and evidence.base_expectancy_usdt > evidence.champion_expectancy_usdt
                ),
                "DOES_NOT_BEAT_CHAMPION",
            ),
        )
        return tuple(reason for passed, reason in gates if not passed)
