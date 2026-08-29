# 비용·강건성 증거를 통과한 PAPER 연구후보만 최대 10개 감시목록으로 유지한다.
"""중복 후보와 작은 표본을 차단하는 결정적 전략 생존 감시목록 선택기다."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

MAX_SURVIVOR_WATCHLIST_SIZE = 10
MINIMUM_UNIQUE_OPPORTUNITIES = 30
MINIMUM_WIN_RATE = Decimal("0.70")
MINIMUM_BASE_PROFIT_FACTOR = Decimal("1.05")
MINIMUM_STRESS_PROFIT_FACTOR = Decimal("1.00")
MINIMUM_DSR_PROBABILITY = Decimal("0.95")
MAXIMUM_PBO = Decimal("0.20")


def parameter_fingerprint(parameters: Mapping[str, object]) -> str:
    """이름·키 순서가 아닌 실제 파라미터 값으로 후보 중복 지문을 만든다."""

    normalized = _normalize_parameter(parameters)
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SurvivorCandidateEvidence:
    """한 파라미터 후보의 동결 OOS·비용·강건성 증거다."""

    candidate_id: str
    hypothesis_id: str
    parameter_fingerprint: str
    unique_opportunities: int
    base_sample_size: int
    stress_sample_size: int
    base_win_rate: Decimal | None
    stress_win_rate: Decimal | None
    base_win_rate_ci95_lower: Decimal | None
    stress_win_rate_ci95_lower: Decimal | None
    base_expectancy_bps: Decimal | None
    stress_expectancy_bps: Decimal | None
    base_profit_factor: Decimal | None
    stress_profit_factor: Decimal | None
    base_bootstrap_lower_bps: Decimal | None
    stress_bootstrap_lower_bps: Decimal | None
    base_dsr_probability: Decimal | None
    stress_dsr_probability: Decimal | None
    base_pbo: Decimal | None
    stress_pbo: Decimal | None
    chronological_oos_passed: bool
    parameter_robustness_passed: bool
    concentration_passed: bool
    drawdown_passed: bool
    cost_model_passed: bool
    no_lookahead_passed: bool
    paper_only: bool = True
    real_orders_enabled: bool = False

    @property
    def evidence_key(self) -> tuple[str, str]:
        return (self.hypothesis_id, self.parameter_fingerprint)

    @property
    def worst_win_rate(self) -> Decimal:
        return min(_required(self.base_win_rate), _required(self.stress_win_rate))

    @property
    def worst_ci95_lower(self) -> Decimal:
        return min(
            _required(self.base_win_rate_ci95_lower),
            _required(self.stress_win_rate_ci95_lower),
        )

    @property
    def worst_expectancy_bps(self) -> Decimal:
        return min(
            _required(self.base_expectancy_bps),
            _required(self.stress_expectancy_bps),
        )

    @property
    def worst_profit_factor(self) -> Decimal:
        return min(
            _required(self.base_profit_factor),
            _required(self.stress_profit_factor),
        )

    @property
    def worst_bootstrap_lower_bps(self) -> Decimal:
        return min(
            _required(self.base_bootstrap_lower_bps),
            _required(self.stress_bootstrap_lower_bps),
        )

    def eligibility_blockers(self) -> tuple[str, ...]:
        """감시목록 순위를 허용하지 않는 근거를 모두 반환한다."""

        blockers: list[str] = []
        if not self.paper_only or self.real_orders_enabled:
            blockers.append("PAPER_SAFETY_CONTRACT_FAILED")
        if self.unique_opportunities < MINIMUM_UNIQUE_OPPORTUNITIES:
            blockers.append("UNIQUE_OPPORTUNITIES_BELOW_30")
        if self.base_sample_size < MINIMUM_UNIQUE_OPPORTUNITIES:
            blockers.append("BASE_SAMPLE_BELOW_30")
        if self.stress_sample_size < MINIMUM_UNIQUE_OPPORTUNITIES:
            blockers.append("STRESS_SAMPLE_BELOW_30")
        _minimum_decimal(
            blockers,
            self.base_win_rate,
            MINIMUM_WIN_RATE,
            "BASE_WIN_RATE_BELOW_70_OR_MISSING",
        )
        _minimum_decimal(
            blockers,
            self.stress_win_rate,
            MINIMUM_WIN_RATE,
            "STRESS_WIN_RATE_BELOW_70_OR_MISSING",
        )
        _positive_decimal(
            blockers,
            self.base_expectancy_bps,
            "BASE_EXPECTANCY_NOT_POSITIVE",
        )
        _positive_decimal(
            blockers,
            self.stress_expectancy_bps,
            "STRESS_EXPECTANCY_NOT_POSITIVE",
        )
        _minimum_decimal(
            blockers,
            self.base_profit_factor,
            MINIMUM_BASE_PROFIT_FACTOR,
            "BASE_PROFIT_FACTOR_BELOW_1_05_OR_MISSING",
        )
        _minimum_decimal(
            blockers,
            self.stress_profit_factor,
            MINIMUM_STRESS_PROFIT_FACTOR,
            "STRESS_PROFIT_FACTOR_BELOW_1_OR_MISSING",
            strict=True,
        )
        _positive_decimal(
            blockers,
            self.base_bootstrap_lower_bps,
            "BASE_BOOTSTRAP_LOWER_NOT_POSITIVE",
        )
        _positive_decimal(
            blockers,
            self.stress_bootstrap_lower_bps,
            "STRESS_BOOTSTRAP_LOWER_NOT_POSITIVE",
        )
        _minimum_decimal(
            blockers,
            self.base_dsr_probability,
            MINIMUM_DSR_PROBABILITY,
            "BASE_DSR_BELOW_0_95_OR_MISSING",
        )
        _minimum_decimal(
            blockers,
            self.stress_dsr_probability,
            MINIMUM_DSR_PROBABILITY,
            "STRESS_DSR_BELOW_0_95_OR_MISSING",
        )
        _maximum_decimal(
            blockers,
            self.base_pbo,
            MAXIMUM_PBO,
            "BASE_PBO_ABOVE_0_20_OR_MISSING",
        )
        _maximum_decimal(
            blockers,
            self.stress_pbo,
            MAXIMUM_PBO,
            "STRESS_PBO_ABOVE_0_20_OR_MISSING",
        )
        for passed, reason in (
            (self.chronological_oos_passed, "CHRONOLOGICAL_OOS_NOT_PASSED"),
            (self.parameter_robustness_passed, "PARAMETER_ROBUSTNESS_NOT_PASSED"),
            (self.concentration_passed, "CONCENTRATION_NOT_PASSED"),
            (self.drawdown_passed, "DRAWDOWN_NOT_PASSED"),
            (self.cost_model_passed, "COST_MODEL_NOT_PASSED"),
            (self.no_lookahead_passed, "NO_LOOKAHEAD_NOT_PASSED"),
        ):
            if not passed:
                blockers.append(reason)
        required_values = (
            self.base_win_rate_ci95_lower,
            self.stress_win_rate_ci95_lower,
        )
        if any(value is None or not value.is_finite() for value in required_values):
            blockers.append("WIN_RATE_CI95_LOWER_MISSING")
        return tuple(dict.fromkeys(blockers))

    def as_watchlist_row(self, *, position: int) -> dict[str, object]:
        return {
            "position": position,
            "candidate_id": self.candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "parameter_fingerprint": self.parameter_fingerprint,
            "unique_opportunities": self.unique_opportunities,
            "worst_profile_win_rate": str(self.worst_win_rate),
            "worst_profile_win_rate_ci95_lower": str(self.worst_ci95_lower),
            "worst_profile_expectancy_bps": str(self.worst_expectancy_bps),
            "worst_profile_profit_factor": str(self.worst_profit_factor),
            "worst_profile_bootstrap_lower_bps": str(
                self.worst_bootstrap_lower_bps
            ),
            "status": "FORWARD_LIVE_PUBLIC_MONITORING_REQUIRED",
            "promotion_allowed": False,
        }


def select_survivor_watchlist(
    candidates: tuple[SurvivorCandidateEvidence, ...],
    *,
    previous_candidate_ids: tuple[str, ...] = (),
    capacity: int = MAX_SURVIVOR_WATCHLIST_SIZE,
) -> dict[str, object]:
    """증거가 우월한 후보만 기존 최대 10개 감시목록과 결정적으로 교체한다."""

    if capacity < 1 or capacity > MAX_SURVIVOR_WATCHLIST_SIZE:
        raise ValueError("전략 생존 감시목록 크기는 1~10이어야 합니다.")
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("전략 생존 후보 ID가 중복됐습니다.")

    deduplicated: dict[tuple[str, str], SurvivorCandidateEvidence] = {}
    duplicate_rows: list[dict[str, object]] = []
    for candidate in sorted(candidates, key=_candidate_rank_key):
        existing = deduplicated.get(candidate.evidence_key)
        if existing is None:
            deduplicated[candidate.evidence_key] = candidate
            continue
        duplicate_rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "kept_candidate_id": existing.candidate_id,
                "reason": "DUPLICATE_HYPOTHESIS_AND_PARAMETER_FINGERPRINT",
            }
        )

    unique_candidates = tuple(deduplicated.values())
    blockers_by_id = {
        candidate.candidate_id: candidate.eligibility_blockers()
        for candidate in unique_candidates
    }
    eligible = {
        candidate.candidate_id: candidate
        for candidate in unique_candidates
        if not blockers_by_id[candidate.candidate_id]
    }
    previous_unique = tuple(dict.fromkeys(previous_candidate_ids))
    selected = [
        eligible[candidate_id]
        for candidate_id in previous_unique
        if candidate_id in eligible
    ][:capacity]
    selected_ids = {candidate.candidate_id for candidate in selected}
    challengers = sorted(
        (
            candidate
            for candidate in eligible.values()
            if candidate.candidate_id not in selected_ids
        ),
        key=_candidate_rank_key,
    )
    while challengers and len(selected) < capacity:
        candidate = challengers.pop(0)
        selected.append(candidate)
        selected_ids.add(candidate.candidate_id)

    replacements: list[dict[str, object]] = []
    for challenger in challengers:
        weakest = max(selected, key=_candidate_rank_key)
        if not _strict_evidence_dominance(challenger, weakest):
            continue
        selected.remove(weakest)
        selected.append(challenger)
        selected_ids.remove(weakest.candidate_id)
        selected_ids.add(challenger.candidate_id)
        replacements.append(
            {
                "removed_candidate_id": weakest.candidate_id,
                "added_candidate_id": challenger.candidate_id,
                "reason": "CHALLENGER_STRICTLY_DOMINATES_WEAKEST_SURVIVOR",
                "historical_records_preserved": True,
            }
        )

    selected.sort(key=_candidate_rank_key)
    excluded = [
        {
            "candidate_id": candidate.candidate_id,
            "reason_codes": list(blockers_by_id[candidate.candidate_id]),
        }
        for candidate in sorted(unique_candidates, key=lambda row: row.candidate_id)
        if blockers_by_id[candidate.candidate_id]
    ]
    removed_previous = [
        candidate_id
        for candidate_id in previous_unique
        if candidate_id not in selected_ids
    ]
    return {
        "schema": "flowscalper.survivor_watchlist.v1",
        "capacity": capacity,
        "watchlist_count": len(selected),
        "watchlist_candidate_ids": [candidate.candidate_id for candidate in selected],
        "watchlist": [
            candidate.as_watchlist_row(position=position)
            for position, candidate in enumerate(selected, start=1)
        ],
        "replacement_events": replacements,
        "removed_previous_candidate_ids": removed_previous,
        "excluded_ineligible_candidates": excluded,
        "excluded_duplicate_candidates": duplicate_rows,
        "unproven_candidates_used_to_fill_capacity": False,
        "automatic_strategy_deletion": False,
        "historical_records_preserved": True,
        "selection_or_promotion_performed": False,
        "profitability_status": "NOT_PROVEN",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
    }


def _candidate_rank_key(candidate: SurvivorCandidateEvidence) -> tuple[object, ...]:
    blockers = candidate.eligibility_blockers()
    return (
        bool(blockers),
        -_optional(candidate.base_win_rate, Decimal("-Infinity")),
        -_optional(candidate.stress_win_rate, Decimal("-Infinity")),
        -_optional(candidate.base_win_rate_ci95_lower, Decimal("-Infinity")),
        -_optional(candidate.stress_win_rate_ci95_lower, Decimal("-Infinity")),
        -_optional(candidate.base_expectancy_bps, Decimal("-Infinity")),
        -_optional(candidate.stress_expectancy_bps, Decimal("-Infinity")),
        -candidate.unique_opportunities,
        candidate.candidate_id,
    )


def _strict_evidence_dominance(
    challenger: SurvivorCandidateEvidence,
    incumbent: SurvivorCandidateEvidence,
) -> bool:
    """작은 표시승률 흔들림이 아니라 하한·기대값까지 개선될 때만 교체한다."""

    return (
        challenger.worst_win_rate > incumbent.worst_win_rate
        and challenger.worst_ci95_lower >= incumbent.worst_ci95_lower
        and challenger.worst_expectancy_bps >= incumbent.worst_expectancy_bps
        and challenger.worst_profit_factor >= incumbent.worst_profit_factor
        and challenger.worst_bootstrap_lower_bps
        >= incumbent.worst_bootstrap_lower_bps
    )


def _optional(value: Decimal | None, fallback: Decimal) -> Decimal:
    return value if value is not None and value.is_finite() else fallback


def _required(value: Decimal | None) -> Decimal:
    if value is None or not value.is_finite():
        raise ValueError("감시목록 순위값이 없거나 유한하지 않습니다.")
    return value


def _minimum_decimal(
    blockers: list[str],
    value: Decimal | None,
    minimum: Decimal,
    reason: str,
    *,
    strict: bool = False,
) -> None:
    if value is None or not value.is_finite() or (
        value <= minimum if strict else value < minimum
    ):
        blockers.append(reason)


def _maximum_decimal(
    blockers: list[str],
    value: Decimal | None,
    maximum: Decimal,
    reason: str,
) -> None:
    if value is None or not value.is_finite() or value > maximum:
        blockers.append(reason)


def _positive_decimal(
    blockers: list[str],
    value: Decimal | None,
    reason: str,
) -> None:
    if value is None or not value.is_finite() or value <= 0:
        blockers.append(reason)


def _normalize_parameter(value: object) -> object:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int):
        return {"$number": str(value)}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("전략 파라미터 Decimal은 유한해야 합니다.")
        return {"$number": format(value.normalize(), "f")}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("전략 파라미터 float는 유한해야 합니다.")
        return {"$number": format(Decimal(str(value)).normalize(), "f")}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("전략 파라미터 객체 키는 문자열이어야 합니다.")
        return {
            str(key): _normalize_parameter(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, list | tuple):
        return [_normalize_parameter(item) for item in value]
    raise ValueError(
        f"지원하지 않는 전략 파라미터 형식입니다: {type(value).__name__}"
    )
