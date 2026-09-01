# V9 shrinkage·FDR·E-process·Pareto·근거 신선도 경계를 검증한다.
"""V9 통계 선별 코어의 결정성과 fail-closed 동작을 검증한다."""

from __future__ import annotations

from math import isfinite

import pytest

from backend.app.research.statistical_evidence import (
    DIRECTIONAL_MAXIMIZE,
    DIRECTIONAL_MINIMIZE,
    EvidenceCategory,
    EvidenceFreshnessStatus,
    FDRBatchFamily,
    FDRMethod,
    HypothesisEValue,
    HypothesisPValue,
    NetRCell,
    ParetoCandidate,
    ParetoCategory,
    StatisticalEvidenceError,
    assess_evidence_freshness,
    batch_fdr,
    e_bh,
    new_e_process,
    pareto_robust_set,
    shrink_net_r_cells,
    shrink_win_rate,
    update_e_process,
)


def test_win_rate_shrinkage_uses_family_prior_and_beta_lower_bound() -> None:
    result = shrink_win_rate(
        cell_wins=1,
        cell_losses_and_breakevens=0,
        family_wins=70,
        family_losses_and_breakevens=30,
    )

    assert result.family_rate == pytest.approx(70.5 / 101)
    assert result.posterior_alpha == pytest.approx(1 + 20 * result.family_rate)
    assert result.posterior_beta == pytest.approx(20 * (1 - result.family_rate))
    assert result.raw_win_rate == 1.0
    assert result.family_rate < result.shrunk_win_rate < result.raw_win_rate
    assert 0 < result.shrunk_win_lower_95 < result.shrunk_win_rate
    assert result.supports_promotion is False


def test_zero_sample_win_rate_reports_prior_without_raw_rate() -> None:
    result = shrink_win_rate(
        cell_wins=0,
        cell_losses_and_breakevens=0,
        family_wins=4,
        family_losses_and_breakevens=6,
    )

    assert result.raw_win_rate is None
    assert result.shrunk_win_rate == pytest.approx(result.family_rate)
    assert result.supports_promotion is False


def test_net_r_shrinkage_never_flips_raw_loss_positive() -> None:
    results = shrink_net_r_cells(
        [
            NetRCell(
                cell_id="loss-cell",
                family_id="FLOW",
                evidence_version="v9-e1",
                net_returns=(-10.0, 9.8),
            ),
            NetRCell(
                cell_id="positive-cell",
                family_id="FLOW",
                evidence_version="v9-e1",
                net_returns=(1.0,) * 10,
            ),
        ]
    )
    loss = next(result for result in results if result.cell_id == "loss-cell")

    assert loss.raw_mean == pytest.approx(-0.1)
    assert loss.family_mean > 0
    assert loss.formula_shrunk_mean > 0
    assert loss.raw_loss_guard_applied is True
    assert loss.shrunk_mean == 0
    assert loss.shrunk_ev_lower <= 0
    assert loss.supports_promotion is False


def test_net_r_shrinkage_zero_variance_and_small_sample_are_safe() -> None:
    results = shrink_net_r_cells(
        [
            NetRCell(
                cell_id="a",
                family_id="FLOW",
                evidence_version="v9-e1",
                net_returns=(0.2, 0.2, 0.2, 0.2),
            ),
            NetRCell(
                cell_id="b",
                family_id="FLOW",
                evidence_version="v9-e1",
                net_returns=(0.1, 0.1, 0.1, 0.1, 0.1),
            ),
        ]
    )
    by_id = {result.cell_id: result for result in results}

    assert by_id["a"].posterior_variance == 0
    assert by_id["a"].shrunk_mean == pytest.approx(0.2)
    assert by_id["a"].rank_eligible is False
    assert by_id["a"].supports_promotion is False
    assert by_id["b"].rank_eligible is True


def test_net_r_shrinkage_is_input_order_invariant() -> None:
    cells = [
        NetRCell("z", "FLOW", "v9-e1", (0.1, 0.2, 0.3, 0.4, 0.5)),
        NetRCell("a", "FLOW", "v9-e1", (-0.2, 0.1, 0.2, 0.3, 0.4)),
    ]

    assert shrink_net_r_cells(cells) == shrink_net_r_cells(list(reversed(cells)))


def test_net_r_shrinkage_rejects_mixed_versions_and_duplicate_cells() -> None:
    mixed = [
        NetRCell("a", "FLOW", "old", (0.1,)),
        NetRCell("b", "FLOW", "current", (0.2,)),
    ]
    duplicate = [
        NetRCell("a", "FLOW", "current", (0.1,)),
        NetRCell("a", "FLOW", "current", (0.2,)),
    ]

    with pytest.raises(StatisticalEvidenceError, match="evidence version"):
        shrink_net_r_cells(mixed)
    with pytest.raises(StatisticalEvidenceError, match="중복 cell"):
        shrink_net_r_cells(duplicate)


def _p_values() -> list[HypothesisPValue]:
    return [
        HypothesisPValue("h3", FDRBatchFamily.ENTRY_STRATEGY, 0.20),
        HypothesisPValue("h1", FDRBatchFamily.ENTRY_STRATEGY, 0.001),
        HypothesisPValue("h2", FDRBatchFamily.ENTRY_STRATEGY, 0.02),
    ]


def test_bh_is_order_invariant_and_by_is_more_conservative() -> None:
    hypotheses = _p_values()

    bh = batch_fdr(hypotheses, method=FDRMethod.BH)
    reversed_bh = batch_fdr(list(reversed(hypotheses)), method=FDRMethod.BH)
    by = batch_fdr(hypotheses, method=FDRMethod.BY)

    assert bh == reversed_bh
    assert bh.selected_ids == ("h1", "h2")
    assert bh.rejection_threshold == pytest.approx(0.02)
    assert by.selected_ids == ("h1",)


def test_bh_includes_exact_boundary() -> None:
    result = batch_fdr(
        [
            HypothesisPValue("boundary", FDRBatchFamily.FILTER, 0.025),
            HypothesisPValue("large", FDRBatchFamily.FILTER, 0.50),
        ],
        target_fdr=0.05,
    )

    assert result.selected_ids == ("boundary",)
    assert result.rejection_threshold == pytest.approx(0.025)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -0.01, 1.01])
def test_batch_fdr_rejects_invalid_p_values(invalid: float) -> None:
    with pytest.raises(StatisticalEvidenceError):
        batch_fdr(
            [HypothesisPValue("h1", FDRBatchFamily.ENTRY_STRATEGY, invalid)]
        )


def test_batch_fdr_rejects_duplicate_hypotheses_and_mixed_families() -> None:
    with pytest.raises(StatisticalEvidenceError, match="중복 hypothesis"):
        batch_fdr(
            [
                HypothesisPValue("same", FDRBatchFamily.ENTRY_STRATEGY, 0.01),
                HypothesisPValue("same", FDRBatchFamily.ENTRY_STRATEGY, 0.02),
            ]
        )
    with pytest.raises(StatisticalEvidenceError, match="batch family"):
        batch_fdr(
            [
                HypothesisPValue("entry", FDRBatchFamily.ENTRY_STRATEGY, 0.01),
                HypothesisPValue("exit", FDRBatchFamily.EXIT_VARIANT, 0.02),
            ]
        )


def test_e_process_clips_scores_and_keeps_nonnegative_values() -> None:
    state = new_e_process()
    positive = update_e_process(state, 6.0)
    negative_monitor = update_e_process(state, -6.0, negative_drift=True)

    assert positive.sample_count == 1
    assert positive.latest_clipped_x == 1.0
    assert negative_monitor.latest_clipped_x == 1.0
    assert all(isfinite(value) and value >= 0 for value in positive.e_values)
    assert isfinite(positive.mixture_e_value)
    assert positive.mixture_e_value >= 0


def test_e_process_final_state_is_order_invariant_for_same_opportunities() -> None:
    first = new_e_process()
    second = new_e_process()
    values = (3.0, -1.0, 2.0, 0.0)
    for value in values:
        first = update_e_process(first, value)
    for value in reversed(values):
        second = update_e_process(second, value)

    assert first.sample_count == second.sample_count
    assert first.cumulative_sum_x == pytest.approx(second.cumulative_sum_x)
    assert first.log_e_values == pytest.approx(second.log_e_values)
    assert first.mixture_e_value == pytest.approx(second.mixture_e_value)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_e_process_rejects_nonfinite_net_r(invalid: float) -> None:
    with pytest.raises(StatisticalEvidenceError, match="net R"):
        update_e_process(new_e_process(), invalid)


def test_e_process_rejects_duplicate_lambda() -> None:
    with pytest.raises(StatisticalEvidenceError, match="중복"):
        new_e_process(lambdas=(0.1, 0.1))


def test_e_bh_is_order_invariant_and_uses_dependence_safe_boundary() -> None:
    hypotheses = [
        HypothesisEValue("weak", 1.0),
        HypothesisEValue("strong-a", 70.0),
        HypothesisEValue("strong-b", 35.0),
    ]

    result = e_bh(hypotheses)
    reversed_result = e_bh(list(reversed(hypotheses)))

    assert result == reversed_result
    assert result.selected_ids == ("strong-a", "strong-b")
    assert result.selected_count == 2
    assert result.selection_threshold == pytest.approx(30.0)


def test_e_bh_includes_exact_boundary() -> None:
    result = e_bh(
        [HypothesisEValue("boundary", 40.0), HypothesisEValue("weak", 0.0)],
        target_fdr=0.05,
    )

    assert result.selected_ids == ("boundary",)
    assert result.selection_threshold == pytest.approx(40.0)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -0.01])
def test_e_bh_rejects_invalid_e_values(invalid: float) -> None:
    with pytest.raises(StatisticalEvidenceError):
        e_bh([HypothesisEValue("h1", invalid)])


def test_e_bh_rejects_duplicate_hypothesis() -> None:
    with pytest.raises(StatisticalEvidenceError, match="중복 hypothesis"):
        e_bh([HypothesisEValue("same", 100.0), HypothesisEValue("same", 50.0)])


def _directional_metrics(
    *,
    maximized: float,
    minimized: float,
    final_oos_override: float | None = None,
) -> dict[str, float]:
    metrics = {name: maximized for name in DIRECTIONAL_MAXIMIZE}
    metrics.update({name: minimized for name in DIRECTIONAL_MINIMIZE})
    if final_oos_override is not None:
        metrics["final_oos_ev_lower"] = final_oos_override
    return metrics


def _candidate(
    candidate_id: str,
    cluster_id: str,
    metrics: dict[str, float],
    *,
    economic_gate_passed: bool = True,
    mcs_survivor: bool = True,
    fdr_passed: bool = True,
) -> ParetoCandidate:
    return ParetoCandidate(
        candidate_id=candidate_id,
        category=ParetoCategory.DIRECTIONAL,
        cluster_id=cluster_id,
        metrics=metrics,
        economic_gate_passed=economic_gate_passed,
        mcs_survivor=mcs_survivor,
        fdr_passed=fdr_passed,
    )


def test_pareto_fronts_domination_and_cluster_diversity() -> None:
    candidates = [
        _candidate("a", "cluster-flow", _directional_metrics(maximized=2.0, minimized=1.0)),
        _candidate("b", "cluster-book", _directional_metrics(maximized=1.0, minimized=2.0)),
        _candidate(
            "c",
            "cluster-flow",
            _directional_metrics(
                maximized=1.5,
                minimized=1.5,
                final_oos_override=3.0,
            ),
        ),
        _candidate("d", "cluster-depth", _directional_metrics(maximized=1.8, minimized=1.2)),
        _candidate(
            "excluded",
            "cluster-other",
            _directional_metrics(maximized=10.0, minimized=0.1),
            fdr_passed=False,
        ),
    ]

    result = pareto_robust_set(candidates, category=ParetoCategory.DIRECTIONAL)

    assert result.fronts[0] == ("a", "c")
    assert result.fronts[1] == ("d",)
    assert result.fronts[2] == ("b",)
    assert result.robust_candidate_ids == ("a", "d", "b")
    assert result.cluster_suppressed_ids == ("c",)
    assert result.hard_gate_excluded_ids == ("excluded",)


def test_pareto_is_input_order_invariant() -> None:
    candidates = [
        _candidate("a", "one", _directional_metrics(maximized=2.0, minimized=1.0)),
        _candidate("b", "two", _directional_metrics(maximized=1.0, minimized=2.0)),
    ]

    assert pareto_robust_set(
        candidates,
        category=ParetoCategory.DIRECTIONAL,
    ) == pareto_robust_set(
        list(reversed(candidates)),
        category=ParetoCategory.DIRECTIONAL,
    )


def test_equal_pareto_candidates_share_front_but_cluster_limit_keeps_one() -> None:
    metrics = _directional_metrics(maximized=1.0, minimized=1.0)
    result = pareto_robust_set(
        [_candidate("a", "same", metrics), _candidate("b", "same", dict(metrics))],
        category=ParetoCategory.DIRECTIONAL,
    )

    assert result.fronts == (("a", "b"),)
    assert result.robust_candidate_ids == ("a",)
    assert result.cluster_suppressed_ids == ("b",)


def test_pareto_rejects_mixed_category_missing_nan_and_duplicate() -> None:
    valid = _candidate("valid", "one", _directional_metrics(maximized=1.0, minimized=1.0))
    mixed = ParetoCandidate(
        candidate_id="neutral",
        category=ParetoCategory.MARKET_NEUTRAL,
        cluster_id="neutral",
        metrics={},
        economic_gate_passed=True,
        mcs_survivor=True,
        fdr_passed=True,
    )
    missing = _candidate("missing", "two", _directional_metrics(maximized=1.0, minimized=1.0))
    missing.metrics.pop("es95")
    nonfinite_metrics = _directional_metrics(maximized=1.0, minimized=1.0)
    nonfinite_metrics["stress_ev"] = float("nan")
    nonfinite = _candidate("nan", "three", nonfinite_metrics)

    with pytest.raises(StatisticalEvidenceError, match="섞을 수 없습니다"):
        pareto_robust_set([valid, mixed], category=ParetoCategory.DIRECTIONAL)
    with pytest.raises(StatisticalEvidenceError, match="목적 집합"):
        pareto_robust_set([missing], category=ParetoCategory.DIRECTIONAL)
    with pytest.raises(StatisticalEvidenceError, match="유한"):
        pareto_robust_set([nonfinite], category=ParetoCategory.DIRECTIONAL)
    with pytest.raises(StatisticalEvidenceError, match="중복 candidate"):
        pareto_robust_set([valid, valid], category=ParetoCategory.DIRECTIONAL)


@pytest.mark.parametrize(
    ("category", "required", "lookback"),
    [
        (EvidenceCategory.FAST, 50, 90),
        (EvidenceCategory.SWING, 30, 180),
        (EvidenceCategory.MICRO, 200, 60),
        (EvidenceCategory.MARKET_NEUTRAL, 20, 180),
    ],
)
def test_evidence_freshness_exact_boundary(
    category: EvidenceCategory,
    required: int,
    lookback: int,
) -> None:
    fresh = assess_evidence_freshness(
        category=category,
        observed_unique_opportunities=required,
        evidence_version="current",
        current_evidence_version="current",
    )
    stale = assess_evidence_freshness(
        category=category,
        observed_unique_opportunities=required - 1,
        evidence_version="current",
        current_evidence_version="current",
    )

    assert fresh.status is EvidenceFreshnessStatus.FRESH
    assert fresh.lookback_days == lookback
    assert fresh.promotion_allowed is True
    assert stale.status is EvidenceFreshnessStatus.STALE_EVIDENCE
    assert stale.reason == "RECENT_SAMPLE_INSUFFICIENT"
    assert stale.promotion_allowed is False


def test_evidence_version_change_forces_stale_evidence() -> None:
    result = assess_evidence_freshness(
        category=EvidenceCategory.FAST,
        observed_unique_opportunities=1_000,
        evidence_version="old-fee-model",
        current_evidence_version="new-fee-model",
    )

    assert result.status is EvidenceFreshnessStatus.STALE_EVIDENCE
    assert result.reason == "EVIDENCE_VERSION_MISMATCH"
    assert result.promotion_allowed is False


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_shrinkage_rejects_nonfinite_returns(invalid: float) -> None:
    with pytest.raises(StatisticalEvidenceError, match="유한"):
        shrink_net_r_cells([NetRCell("a", "FLOW", "v9-e1", (invalid,))])
