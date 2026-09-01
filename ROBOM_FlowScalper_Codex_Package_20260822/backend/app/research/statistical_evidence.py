# V9 후보의 보수적 통계 근거와 다중검정·Pareto 선별을 결정적으로 계산한다.
"""V9 통계 선별에 필요한 순수하고 fail-closed인 계산 API를 제공한다."""

from __future__ import annotations

import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median, variance

_DEFAULT_LAMBDAS = (0.10, 0.20, 0.40, 0.80)
_MAX_LOG_FLOAT = math.log(sys.float_info.max)


class StatisticalEvidenceError(ValueError):
    """통계 입력이 불완전하거나 비유한 값일 때 발생한다."""


class FDRMethod(StrEnum):
    BH = "BH"
    BY = "BY"


class FDRBatchFamily(StrEnum):
    ENTRY_STRATEGY = "ENTRY_STRATEGY"
    EXIT_VARIANT = "EXIT_VARIANT"
    EXECUTION = "EXECUTION"
    FILTER = "FILTER"
    MARKET_NEUTRAL = "MARKET_NEUTRAL"


class ParetoCategory(StrEnum):
    DIRECTIONAL = "DIRECTIONAL"
    MARKET_NEUTRAL = "MARKET_NEUTRAL"


class EvidenceCategory(StrEnum):
    FAST = "FAST"
    SWING = "SWING"
    MICRO = "MICRO"
    MARKET_NEUTRAL = "MARKET_NEUTRAL"


class EvidenceFreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE_EVIDENCE = "STALE_EVIDENCE"


DIRECTIONAL_MAXIMIZE = (
    "final_oos_ev_lower",
    "stress_ev",
    "wilson_lower",
    "opportunity_retention",
    "fill_retention",
)
DIRECTIONAL_MINIMIZE = (
    "es95",
    "max_drawdown",
    "cost_burden",
    "turnover",
    "adverse_selection",
)
MARKET_NEUTRAL_MAXIMIZE = (
    "net_carry_yield",
    "cycle_ev",
    "capital_utilization",
)
MARKET_NEUTRAL_MINIMIZE = (
    "legging_loss",
    "basis_es",
    "drawdown",
    "venue_concentration",
    "atomic_failure",
)


def _finite(value: float, name: str) -> float:
    converted = float(value)
    if not math.isfinite(converted):
        raise StatisticalEvidenceError(f"{name} 값은 유한해야 합니다.")
    return converted


def _probability(value: float, name: str, *, zero_allowed: bool = True) -> float:
    converted = _finite(value, name)
    lower_ok = converted >= 0 if zero_allowed else converted > 0
    if not lower_ok or converted > 1:
        boundary = "[0, 1]" if zero_allowed else "(0, 1]"
        raise StatisticalEvidenceError(f"{name} 값은 {boundary} 범위여야 합니다.")
    return converted


def _nonnegative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StatisticalEvidenceError(f"{name} 값은 음이 아닌 정수여야 합니다.")
    return value


def _unique_nonempty(values: Iterable[str], name: str) -> tuple[str, ...]:
    normalized = tuple(values)
    if any(not value.strip() for value in normalized):
        raise StatisticalEvidenceError(f"{name} 식별자는 비어 있을 수 없습니다.")
    if len(set(normalized)) != len(normalized):
        raise StatisticalEvidenceError(f"중복 {name} 식별자를 허용하지 않습니다.")
    return normalized


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    maximum_iterations = 240
    epsilon = 3.0e-14
    minimum = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < minimum:
        d = minimum
    d = 1.0 / d
    result = d
    for iteration in range(1, maximum_iterations + 1):
        even = 2 * iteration
        numerator = iteration * (b - iteration) * x / ((qam + even) * (a + even))
        d = 1.0 + numerator * d
        if abs(d) < minimum:
            d = minimum
        c = 1.0 + numerator / c
        if abs(c) < minimum:
            c = minimum
        d = 1.0 / d
        result *= d * c

        numerator = -(a + iteration) * (qab + iteration) * x
        numerator /= (a + even) * (qap + even)
        d = 1.0 + numerator * d
        if abs(d) < minimum:
            d = minimum
        c = 1.0 + numerator / c
        if abs(c) < minimum:
            c = minimum
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) <= epsilon:
            return result
    raise StatisticalEvidenceError("Beta quantile 계산이 수렴하지 않았습니다.")


def _regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_term = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    term = math.exp(log_term)
    if x < (a + 1.0) / (a + b + 2.0):
        return term * _beta_continued_fraction(a, b, x) / a
    return 1.0 - term * _beta_continued_fraction(b, a, 1.0 - x) / b


def _beta_quantile(probability: float, alpha: float, beta: float) -> float:
    if alpha <= 0 or beta <= 0:
        raise StatisticalEvidenceError("Beta posterior 모수는 양수여야 합니다.")
    target = _probability(probability, "Beta quantile probability")
    if target == 0:
        return 0.0
    if target == 1:
        return 1.0
    low = 0.0
    high = 1.0
    for _ in range(96):
        midpoint = (low + high) / 2.0
        if _regularized_incomplete_beta(midpoint, alpha, beta) < target:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


@dataclass(frozen=True, slots=True)
class WinRateShrinkage:
    cell_wins: int
    cell_losses_and_breakevens: int
    family_wins: int
    family_losses_and_breakevens: int
    family_rate: float
    posterior_alpha: float
    posterior_beta: float
    raw_win_rate: float | None
    shrunk_win_rate: float
    shrunk_win_lower_95: float
    supports_promotion: bool = False


def shrink_win_rate(
    *,
    cell_wins: int,
    cell_losses_and_breakevens: int,
    family_wins: int,
    family_losses_and_breakevens: int,
    kappa: float = 20.0,
) -> WinRateShrinkage:
    """V9의 Jeffreys-pooled family prior로 cell 승률을 보수화한다."""

    cell_wins = _nonnegative_int(cell_wins, "cell wins")
    cell_losses_and_breakevens = _nonnegative_int(
        cell_losses_and_breakevens,
        "cell losses and breakevens",
    )
    family_wins = _nonnegative_int(family_wins, "family wins")
    family_losses_and_breakevens = _nonnegative_int(
        family_losses_and_breakevens,
        "family losses and breakevens",
    )
    kappa = _finite(kappa, "kappa")
    if kappa <= 0:
        raise StatisticalEvidenceError("kappa는 양수여야 합니다.")
    family_n = family_wins + family_losses_and_breakevens
    family_rate = (family_wins + 0.5) / (family_n + 1.0)
    posterior_alpha = cell_wins + kappa * family_rate
    posterior_beta = cell_losses_and_breakevens + kappa * (1.0 - family_rate)
    shrunk = posterior_alpha / (posterior_alpha + posterior_beta)
    cell_n = cell_wins + cell_losses_and_breakevens
    raw = cell_wins / cell_n if cell_n else None
    return WinRateShrinkage(
        cell_wins=cell_wins,
        cell_losses_and_breakevens=cell_losses_and_breakevens,
        family_wins=family_wins,
        family_losses_and_breakevens=family_losses_and_breakevens,
        family_rate=family_rate,
        posterior_alpha=posterior_alpha,
        posterior_beta=posterior_beta,
        raw_win_rate=raw,
        shrunk_win_rate=shrunk,
        shrunk_win_lower_95=_beta_quantile(0.05, posterior_alpha, posterior_beta),
    )


@dataclass(frozen=True, slots=True)
class NetRCell:
    cell_id: str
    family_id: str
    evidence_version: str
    net_returns: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class NetRShrinkage:
    cell_id: str
    sample_count: int
    raw_mean: float
    raw_se_squared: float
    family_mean: float
    tau_squared: float
    local_weight: float
    formula_shrunk_mean: float
    shrunk_mean: float
    posterior_variance: float
    shrunk_ev_lower: float
    rank_eligible: bool
    supports_promotion: bool
    raw_loss_guard_applied: bool


def shrink_net_r_cells(
    cells: Sequence[NetRCell],
    *,
    minimum_tau_squared: float = 1.0e-6,
) -> tuple[NetRShrinkage, ...]:
    """같은 family·evidence version의 Net-R cell을 계층적으로 보수화한다."""

    if not cells:
        raise StatisticalEvidenceError("Net-R shrinkage에는 최소 한 cell이 필요합니다.")
    _unique_nonempty((cell.cell_id for cell in cells), "cell")
    families = _unique_nonempty(set(cell.family_id for cell in cells), "family")
    versions = _unique_nonempty(set(cell.evidence_version for cell in cells), "evidence version")
    if len(families) != 1:
        raise StatisticalEvidenceError("서로 다른 family를 한 shrinkage에 섞을 수 없습니다.")
    if len(versions) != 1:
        raise StatisticalEvidenceError("서로 다른 evidence version을 섞을 수 없습니다.")
    minimum_tau_squared = _finite(minimum_tau_squared, "minimum tau squared")
    if minimum_tau_squared <= 0:
        raise StatisticalEvidenceError("minimum tau squared는 양수여야 합니다.")

    local: list[tuple[NetRCell, tuple[float, ...], float, float]] = []
    for cell in sorted(cells, key=lambda item: item.cell_id):
        if not cell.family_id.strip() or not cell.evidence_version.strip():
            raise StatisticalEvidenceError("family와 evidence version은 비어 있을 수 없습니다.")
        values = tuple(_finite(value, f"{cell.cell_id} net R") for value in cell.net_returns)
        if not values:
            raise StatisticalEvidenceError("각 Net-R cell에는 최소 한 표본이 필요합니다.")
        local_mean = math.fsum(values) / len(values)
        sample_variance = variance(values) if len(values) >= 2 else 0.0
        standard_error_squared = sample_variance / len(values)
        local.append((cell, values, local_mean, standard_error_squared))

    total_samples = sum(len(values) for _, values, _, _ in local)
    family_mean = math.fsum(mean * len(values) for _, values, mean, _ in local) / total_samples
    local_means = [mean for _, _, mean, _ in local]
    between_variance = variance(local_means) if len(local_means) >= 2 else 0.0
    median_se_squared = median(se_squared for _, _, _, se_squared in local)
    tau_squared = max(between_variance - median_se_squared, minimum_tau_squared)

    results: list[NetRShrinkage] = []
    for cell, values, raw_mean, se_squared in local:
        if se_squared == 0:
            weight = 1.0
            posterior_variance = 0.0
        else:
            weight = tau_squared / (tau_squared + se_squared)
            posterior_variance = 1.0 / (1.0 / se_squared + 1.0 / tau_squared)
        formula_shrunk = weight * raw_mean + (1.0 - weight) * family_mean
        raw_loss_guard = raw_mean <= 0 and formula_shrunk > 0
        shrunk = min(formula_shrunk, 0.0) if raw_loss_guard else formula_shrunk
        lower = shrunk - 1.96 * math.sqrt(posterior_variance)
        rank_eligible = len(values) >= 5
        supports_promotion = (
            rank_eligible and raw_mean > 0 and family_mean > 0 and lower > 0
        )
        results.append(
            NetRShrinkage(
                cell_id=cell.cell_id,
                sample_count=len(values),
                raw_mean=raw_mean,
                raw_se_squared=se_squared,
                family_mean=family_mean,
                tau_squared=tau_squared,
                local_weight=weight,
                formula_shrunk_mean=formula_shrunk,
                shrunk_mean=shrunk,
                posterior_variance=posterior_variance,
                shrunk_ev_lower=lower,
                rank_eligible=rank_eligible,
                supports_promotion=supports_promotion,
                raw_loss_guard_applied=raw_loss_guard,
            )
        )
    return tuple(results)


@dataclass(frozen=True, slots=True)
class HypothesisPValue:
    hypothesis_id: str
    batch_family: FDRBatchFamily
    p_value: float


@dataclass(frozen=True, slots=True)
class AdjustedPValue:
    hypothesis_id: str
    raw_p_value: float
    adjusted_p_value: float
    rank: int
    selected: bool


@dataclass(frozen=True, slots=True)
class BatchFDRResult:
    method: FDRMethod
    batch_family: FDRBatchFamily
    target_fdr: float
    hypothesis_count: int
    selected_ids: tuple[str, ...]
    rejection_threshold: float | None
    adjusted: tuple[AdjustedPValue, ...]


def batch_fdr(
    hypotheses: Sequence[HypothesisPValue],
    *,
    method: FDRMethod = FDRMethod.BH,
    target_fdr: float = 0.05,
) -> BatchFDRResult:
    """한 batch family의 BH 또는 arbitrary-dependence BY 선별을 계산한다."""

    if not hypotheses:
        raise StatisticalEvidenceError("Batch FDR에는 최소 한 hypothesis가 필요합니다.")
    _unique_nonempty((item.hypothesis_id for item in hypotheses), "hypothesis")
    families = {item.batch_family for item in hypotheses}
    if len(families) != 1:
        raise StatisticalEvidenceError("서로 다른 batch family를 한 FDR 계산에 섞을 수 없습니다.")
    target_fdr = _probability(target_fdr, "target FDR", zero_allowed=False)
    ranked = sorted(
        (
            (item.hypothesis_id, _probability(item.p_value, "p-value"))
            for item in hypotheses
        ),
        key=lambda item: (item[1], item[0]),
    )
    count = len(ranked)
    dependence_factor = (
        math.fsum(1.0 / index for index in range(1, count + 1))
        if method is FDRMethod.BY
        else 1.0
    )
    selected_count = 0
    for rank, (_, p_value) in enumerate(ranked, start=1):
        if p_value <= target_fdr * rank / (count * dependence_factor):
            selected_count = rank
    selected = {identifier for identifier, _ in ranked[:selected_count]}

    adjusted_by_id: dict[str, float] = {}
    running_minimum = 1.0
    for rank in range(count, 0, -1):
        identifier, p_value = ranked[rank - 1]
        adjusted = min(1.0, p_value * count * dependence_factor / rank)
        running_minimum = min(running_minimum, adjusted)
        adjusted_by_id[identifier] = running_minimum
    rank_by_id = {identifier: rank for rank, (identifier, _) in enumerate(ranked, start=1)}
    raw_by_id = dict(ranked)
    adjusted_rows = tuple(
        AdjustedPValue(
            hypothesis_id=identifier,
            raw_p_value=raw_by_id[identifier],
            adjusted_p_value=adjusted_by_id[identifier],
            rank=rank_by_id[identifier],
            selected=identifier in selected,
        )
        for identifier in sorted(raw_by_id)
    )
    return BatchFDRResult(
        method=method,
        batch_family=next(iter(families)),
        target_fdr=target_fdr,
        hypothesis_count=count,
        selected_ids=tuple(sorted(selected)),
        rejection_threshold=ranked[selected_count - 1][1] if selected_count else None,
        adjusted=adjusted_rows,
    )


@dataclass(frozen=True, slots=True)
class EProcessState:
    lambdas: tuple[float, ...]
    clipping_scale: float
    sample_count: int
    cumulative_sum_x: float
    latest_clipped_x: float | None
    log_e_values: tuple[float, ...]
    e_values: tuple[float, ...]
    log_mixture_e_value: float
    mixture_e_value: float


def new_e_process(
    *,
    lambdas: Sequence[float] = _DEFAULT_LAMBDAS,
    clipping_scale: float = 3.0,
) -> EProcessState:
    normalized_lambdas = tuple(_finite(value, "lambda") for value in lambdas)
    if not normalized_lambdas or any(value <= 0 for value in normalized_lambdas):
        raise StatisticalEvidenceError("E-process lambda는 하나 이상의 양수여야 합니다.")
    if len(set(normalized_lambdas)) != len(normalized_lambdas):
        raise StatisticalEvidenceError("E-process lambda grid에 중복을 허용하지 않습니다.")
    clipping_scale = _finite(clipping_scale, "clipping scale")
    if clipping_scale <= 0:
        raise StatisticalEvidenceError("clipping scale은 양수여야 합니다.")
    zeros = tuple(0.0 for _ in normalized_lambdas)
    ones = tuple(1.0 for _ in normalized_lambdas)
    return EProcessState(
        lambdas=normalized_lambdas,
        clipping_scale=clipping_scale,
        sample_count=0,
        cumulative_sum_x=0.0,
        latest_clipped_x=None,
        log_e_values=zeros,
        e_values=ones,
        log_mixture_e_value=0.0,
        mixture_e_value=1.0,
    )


def _finite_exp(log_value: float) -> float:
    return math.exp(min(log_value, _MAX_LOG_FLOAT))


def update_e_process(
    state: EProcessState,
    net_r: float,
    *,
    negative_drift: bool = False,
) -> EProcessState:
    """비중복 opportunity Net-R 한 건으로 nonnegative mixture E-value를 갱신한다."""

    net_r = _finite(net_r, "net R")
    if state.sample_count < 0 or not math.isfinite(state.cumulative_sum_x):
        raise StatisticalEvidenceError("E-process state가 유효하지 않습니다.")
    if len(state.lambdas) != len(state.log_e_values) or len(state.lambdas) != len(
        state.e_values
    ):
        raise StatisticalEvidenceError("E-process state의 lambda와 E-value 수가 다릅니다.")
    signed_net_r = -net_r if negative_drift else net_r
    clipped = max(-1.0, min(1.0, signed_net_r / state.clipping_scale))
    sample_count = state.sample_count + 1
    cumulative = state.cumulative_sum_x + clipped
    log_values = tuple(
        value * cumulative - 0.5 * value * value * sample_count for value in state.lambdas
    )
    values = tuple(_finite_exp(value) for value in log_values)
    maximum_log = max(log_values)
    log_mixture = maximum_log + math.log(
        math.fsum(math.exp(value - maximum_log) for value in log_values) / len(log_values)
    )
    return EProcessState(
        lambdas=state.lambdas,
        clipping_scale=state.clipping_scale,
        sample_count=sample_count,
        cumulative_sum_x=cumulative,
        latest_clipped_x=clipped,
        log_e_values=log_values,
        e_values=values,
        log_mixture_e_value=log_mixture,
        mixture_e_value=_finite_exp(log_mixture),
    )


@dataclass(frozen=True, slots=True)
class HypothesisEValue:
    hypothesis_id: str
    e_value: float


@dataclass(frozen=True, slots=True)
class EBHResult:
    target_fdr: float
    hypothesis_count: int
    selected_ids: tuple[str, ...]
    selected_count: int
    selection_threshold: float | None


def e_bh(
    hypotheses: Sequence[HypothesisEValue],
    *,
    target_fdr: float = 0.05,
) -> EBHResult:
    """arbitrary dependence에 유효한 e-BH top-k 선별을 계산한다."""

    if not hypotheses:
        raise StatisticalEvidenceError("e-BH에는 최소 한 hypothesis가 필요합니다.")
    _unique_nonempty((item.hypothesis_id for item in hypotheses), "hypothesis")
    target_fdr = _probability(target_fdr, "target FDR", zero_allowed=False)
    ranked = sorted(
        (
            (item.hypothesis_id, _finite(item.e_value, "E-value"))
            for item in hypotheses
        ),
        key=lambda item: (-item[1], item[0]),
    )
    if any(value < 0 for _, value in ranked):
        raise StatisticalEvidenceError("E-value는 음수일 수 없습니다.")
    count = len(ranked)
    selected_count = 0
    for rank, (_, value) in enumerate(ranked, start=1):
        if value >= count / (target_fdr * rank):
            selected_count = rank
    selected = tuple(sorted(identifier for identifier, _ in ranked[:selected_count]))
    return EBHResult(
        target_fdr=target_fdr,
        hypothesis_count=count,
        selected_ids=selected,
        selected_count=selected_count,
        selection_threshold=(
            count / (target_fdr * selected_count) if selected_count else None
        ),
    )


@dataclass(frozen=True, slots=True)
class ParetoCandidate:
    candidate_id: str
    category: ParetoCategory
    cluster_id: str
    metrics: Mapping[str, float]
    economic_gate_passed: bool
    mcs_survivor: bool
    fdr_passed: bool


@dataclass(frozen=True, slots=True)
class ParetoResult:
    category: ParetoCategory
    fronts: tuple[tuple[str, ...], ...]
    robust_candidate_ids: tuple[str, ...]
    cluster_suppressed_ids: tuple[str, ...]
    hard_gate_excluded_ids: tuple[str, ...]


def _pareto_objectives(category: ParetoCategory) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if category is ParetoCategory.DIRECTIONAL:
        return DIRECTIONAL_MAXIMIZE, DIRECTIONAL_MINIMIZE
    return MARKET_NEUTRAL_MAXIMIZE, MARKET_NEUTRAL_MINIMIZE


def _dominates(
    left: Mapping[str, float],
    right: Mapping[str, float],
    maximize: Sequence[str],
    minimize: Sequence[str],
) -> bool:
    never_worse = all(left[name] >= right[name] for name in maximize) and all(
        left[name] <= right[name] for name in minimize
    )
    strictly_better = any(left[name] > right[name] for name in maximize) or any(
        left[name] < right[name] for name in minimize
    )
    return never_worse and strictly_better


def pareto_robust_set(
    candidates: Sequence[ParetoCandidate],
    *,
    category: ParetoCategory,
    limit: int = 10,
    maximum_per_cluster: int = 1,
) -> ParetoResult:
    """hard gate 이후 Pareto front와 cluster-diverse robust set을 반환한다."""

    if not candidates:
        raise StatisticalEvidenceError("Pareto 계산에는 최소 한 candidate가 필요합니다.")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise StatisticalEvidenceError("Pareto limit은 1 이상의 정수여야 합니다.")
    if (
        isinstance(maximum_per_cluster, bool)
        or not isinstance(maximum_per_cluster, int)
        or maximum_per_cluster < 1
    ):
        raise StatisticalEvidenceError("cluster 제한은 1 이상의 정수여야 합니다.")
    _unique_nonempty((candidate.candidate_id for candidate in candidates), "candidate")
    if any(candidate.category is not category for candidate in candidates):
        raise StatisticalEvidenceError("Directional과 market-neutral Pareto를 섞을 수 없습니다.")
    maximize, minimize = _pareto_objectives(category)
    required = set(maximize) | set(minimize)
    normalized: dict[str, dict[str, float]] = {}
    by_id: dict[str, ParetoCandidate] = {}
    for candidate in candidates:
        if not candidate.cluster_id.strip():
            raise StatisticalEvidenceError("Pareto cluster ID는 비어 있을 수 없습니다.")
        if set(candidate.metrics) != required:
            raise StatisticalEvidenceError(
                f"{candidate.candidate_id}의 Pareto metric이 목적 집합과 다릅니다."
            )
        normalized[candidate.candidate_id] = {
            name: _finite(candidate.metrics[name], f"{candidate.candidate_id}.{name}")
            for name in sorted(required)
        }
        by_id[candidate.candidate_id] = candidate

    hard_gate_excluded = tuple(
        sorted(
            candidate.candidate_id
            for candidate in candidates
            if not (
                candidate.economic_gate_passed
                and candidate.mcs_survivor
                and candidate.fdr_passed
            )
        )
    )
    remaining = {
        candidate.candidate_id
        for candidate in candidates
        if candidate.candidate_id not in hard_gate_excluded
    }
    fronts: list[tuple[str, ...]] = []
    while remaining:
        front = tuple(
            sorted(
                candidate_id
                for candidate_id in remaining
                if not any(
                    other_id != candidate_id
                    and _dominates(
                        normalized[other_id],
                        normalized[candidate_id],
                        maximize,
                        minimize,
                    )
                    for other_id in remaining
                )
            )
        )
        if not front:
            raise StatisticalEvidenceError("Pareto front 계산이 진행되지 않았습니다.")
        fronts.append(front)
        remaining.difference_update(front)

    selected: list[str] = []
    suppressed: list[str] = []
    cluster_counts: dict[str, int] = {}
    for front in fronts:
        for candidate_id in front:
            if len(selected) >= limit:
                break
            cluster_id = by_id[candidate_id].cluster_id
            if cluster_counts.get(cluster_id, 0) >= maximum_per_cluster:
                suppressed.append(candidate_id)
                continue
            selected.append(candidate_id)
            cluster_counts[cluster_id] = cluster_counts.get(cluster_id, 0) + 1
        if len(selected) >= limit:
            break
    return ParetoResult(
        category=category,
        fronts=tuple(fronts),
        robust_candidate_ids=tuple(selected),
        cluster_suppressed_ids=tuple(sorted(suppressed)),
        hard_gate_excluded_ids=hard_gate_excluded,
    )


@dataclass(frozen=True, slots=True)
class EvidenceFreshness:
    category: EvidenceCategory
    status: EvidenceFreshnessStatus
    lookback_days: int
    required_unique_opportunities: int
    observed_unique_opportunities: int
    evidence_version: str
    current_evidence_version: str
    promotion_allowed: bool
    reason: str


_FRESHNESS_REQUIREMENTS: dict[EvidenceCategory, tuple[int, int]] = {
    EvidenceCategory.FAST: (90, 50),
    EvidenceCategory.SWING: (180, 30),
    EvidenceCategory.MICRO: (60, 200),
    EvidenceCategory.MARKET_NEUTRAL: (180, 20),
}


def assess_evidence_freshness(
    *,
    category: EvidenceCategory,
    observed_unique_opportunities: int,
    evidence_version: str,
    current_evidence_version: str,
) -> EvidenceFreshness:
    """V9 고정 lookback으로 현재 evidence version의 최근 표본 충분성을 판정한다."""

    observed = _nonnegative_int(observed_unique_opportunities, "unique opportunities")
    if not evidence_version.strip() or not current_evidence_version.strip():
        raise StatisticalEvidenceError("evidence version은 비어 있을 수 없습니다.")
    lookback_days, required = _FRESHNESS_REQUIREMENTS[category]
    version_matches = evidence_version == current_evidence_version
    fresh = version_matches and observed >= required
    reason = (
        "FRESH"
        if fresh
        else "EVIDENCE_VERSION_MISMATCH"
        if not version_matches
        else "RECENT_SAMPLE_INSUFFICIENT"
    )
    return EvidenceFreshness(
        category=category,
        status=(
            EvidenceFreshnessStatus.FRESH
            if fresh
            else EvidenceFreshnessStatus.STALE_EVIDENCE
        ),
        lookback_days=lookback_days,
        required_unique_opportunities=required,
        observed_unique_opportunities=observed,
        evidence_version=evidence_version,
        current_evidence_version=current_evidence_version,
        promotion_allowed=fresh,
        reason=reason,
    )


__all__ = [
    "AdjustedPValue",
    "BatchFDRResult",
    "DIRECTIONAL_MAXIMIZE",
    "DIRECTIONAL_MINIMIZE",
    "EBHResult",
    "EProcessState",
    "EvidenceCategory",
    "EvidenceFreshness",
    "EvidenceFreshnessStatus",
    "FDRBatchFamily",
    "FDRMethod",
    "HypothesisEValue",
    "HypothesisPValue",
    "MARKET_NEUTRAL_MAXIMIZE",
    "MARKET_NEUTRAL_MINIMIZE",
    "NetRCell",
    "NetRShrinkage",
    "ParetoCandidate",
    "ParetoCategory",
    "ParetoResult",
    "StatisticalEvidenceError",
    "WinRateShrinkage",
    "assess_evidence_freshness",
    "batch_fdr",
    "e_bh",
    "new_e_process",
    "pareto_robust_set",
    "shrink_net_r_cells",
    "shrink_win_rate",
    "update_e_process",
]
