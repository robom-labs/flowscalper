# 전략 생존 감시목록이 작은 표본·중복·잦은 교체를 차단하는지 검증한다.

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.research.survivor_watchlist import (
    SurvivorCandidateEvidence,
    parameter_fingerprint,
    select_survivor_watchlist,
)
from backend.app.strategies.family import StrategyFamilyId


def _candidate(
    index: int,
    *,
    win_rate: str = "0.75",
    ci_lower: str = "0.61",
    expectancy_bps: str = "4.0",
    profit_factor: str = "1.40",
    bootstrap_lower_bps: str = "0.8",
) -> SurvivorCandidateEvidence:
    return SurvivorCandidateEvidence(
        candidate_id=f"CANDIDATE-{index:02d}",
        hypothesis_id=f"HYP-{index:02d}",
        parameter_fingerprint=f"PARAM-{index:02d}",
        unique_opportunities=150 + index,
        base_sample_size=150 + index,
        stress_sample_size=150 + index,
        base_win_rate=Decimal(win_rate),
        stress_win_rate=Decimal(win_rate),
        base_win_rate_ci95_lower=Decimal(ci_lower),
        stress_win_rate_ci95_lower=Decimal(ci_lower),
        base_expectancy_bps=Decimal(expectancy_bps),
        stress_expectancy_bps=Decimal(expectancy_bps),
        base_profit_factor=Decimal(profit_factor),
        stress_profit_factor=Decimal(profit_factor),
        base_bootstrap_lower_bps=Decimal(bootstrap_lower_bps),
        stress_bootstrap_lower_bps=Decimal(bootstrap_lower_bps),
        base_dsr_probability=Decimal("0.97"),
        stress_dsr_probability=Decimal("0.97"),
        base_pbo=Decimal("0.10"),
        stress_pbo=Decimal("0.10"),
        chronological_oos_passed=True,
        parameter_robustness_passed=True,
        concentration_passed=True,
        drawdown_passed=True,
        cost_model_passed=True,
        no_lookahead_passed=True,
        family_id=StrategyFamilyId.BREAKOUT_RUNNER,
        base_payoff_ratio=Decimal("2.10"),
        stress_payoff_ratio=Decimal("2.10"),
        base_return_skew=Decimal("0.20"),
        stress_return_skew=Decimal("0.20"),
        base_largest_trade_contribution=Decimal("0.05"),
        stress_largest_trade_contribution=Decimal("0.05"),
    )


def test_watchlist_never_fills_capacity_with_small_or_weak_samples() -> None:
    strong = _candidate(1)
    small = replace(
        _candidate(2),
        unique_opportunities=29,
        base_sample_size=29,
        stress_sample_size=29,
    )
    weak = replace(
        _candidate(3),
        base_win_rate=Decimal("0.69"),
        stress_expectancy_bps=Decimal("-0.1"),
    )

    result = select_survivor_watchlist((strong, small, weak))

    assert result["watchlist_candidate_ids"] == [strong.candidate_id]
    assert result["watchlist_count"] == 1
    assert result["unproven_candidates_used_to_fill_capacity"] is False
    reasons = {
        row["candidate_id"]: row["reason_codes"]
        for row in result["excluded_ineligible_candidates"]
    }
    assert "UNIQUE_OPPORTUNITIES_BELOW_30" in reasons[small.candidate_id]
    assert "STRESS_EXPECTANCY_NOT_POSITIVE" in reasons[weak.candidate_id]
    assert not any("BELOW_70" in reason for reason in reasons[weak.candidate_id])


def test_positive_low_win_runner_is_not_rejected_by_a_universal_70_percent_gate() -> None:
    candidate = _candidate(
        1,
        win_rate="0.45",
        ci_lower="0.36",
        expectancy_bps="5.0",
        profit_factor="1.40",
        bootstrap_lower_bps="0.7",
    )

    result = select_survivor_watchlist((candidate,))

    assert result["watchlist_candidate_ids"] == [candidate.candidate_id]
    assert result["excluded_ineligible_candidates"] == []
    assert not any("BELOW_70" in reason for reason in candidate.eligibility_blockers())


def test_watchlist_keeps_at_most_ten_and_deduplicates_identical_parameters() -> None:
    candidates = tuple(
        _candidate(
            index,
            win_rate=str(Decimal("0.90") - Decimal(index) / Decimal("100")),
        )
        for index in range(1, 13)
    )
    duplicate = replace(
        _candidate(99, win_rate="0.99"),
        hypothesis_id=candidates[0].hypothesis_id,
        parameter_fingerprint=candidates[0].parameter_fingerprint,
    )

    result = select_survivor_watchlist((*candidates, duplicate))

    assert result["watchlist_count"] == 10
    assert len(set(result["watchlist_candidate_ids"])) == 10
    assert result["excluded_duplicate_candidates"] == [
        {
            "candidate_id": candidates[0].candidate_id,
            "kept_candidate_id": duplicate.candidate_id,
            "reason": "DUPLICATE_HYPOTHESIS_AND_PARAMETER_FINGERPRINT",
        }
    ]
    assert result["automatic_strategy_deletion"] is False
    assert result["historical_records_preserved"] is True


def test_challenger_replaces_weakest_only_with_strict_multi_metric_dominance() -> None:
    incumbents = tuple(_candidate(index) for index in range(1, 11))
    better = _candidate(
        11,
        win_rate="0.78",
        ci_lower="0.64",
        expectancy_bps="4.5",
        profit_factor="1.50",
        bootstrap_lower_bps="1.0",
    )
    noisy = _candidate(
        12,
        win_rate="0.80",
        ci_lower="0.55",
        expectancy_bps="3.0",
        profit_factor="1.20",
        bootstrap_lower_bps="0.4",
    )

    result = select_survivor_watchlist(
        (*incumbents, better, noisy),
        previous_candidate_ids=tuple(row.candidate_id for row in incumbents),
    )

    assert better.candidate_id in result["watchlist_candidate_ids"]
    assert noisy.candidate_id not in result["watchlist_candidate_ids"]
    assert len(result["replacement_events"]) == 1
    assert result["replacement_events"][0]["historical_records_preserved"] is True


def test_watchlist_rejects_invalid_capacity_and_duplicate_candidate_id() -> None:
    candidate = _candidate(1)

    with pytest.raises(ValueError, match="1~10"):
        select_survivor_watchlist((candidate,), capacity=11)
    with pytest.raises(ValueError, match="후보 ID가 중복"):
        select_survivor_watchlist((candidate, candidate))


def test_watchlist_output_cannot_promote_or_enable_real_orders() -> None:
    result = select_survivor_watchlist((_candidate(1),))

    assert result["selection_or_promotion_performed"] is False
    assert result["profitability_status"] == "NOT_PROVEN"
    assert result["paper_only"] is True
    assert result["real_orders_enabled"] is False
    assert result["auth_required"] is False


def test_parameter_fingerprint_ignores_key_order_but_changes_with_actual_value() -> None:
    first = parameter_fingerprint(
        {
            "threshold": Decimal("0.700"),
            "nested": {"window": 30, "sides": ["LONG", "SHORT"]},
        }
    )
    reordered = parameter_fingerprint(
        {
            "nested": {"sides": ("LONG", "SHORT"), "window": Decimal("30.0")},
            "threshold": 0.7,
        }
    )
    changed = parameter_fingerprint(
        {
            "nested": {"sides": ("LONG", "SHORT"), "window": Decimal("31.0")},
            "threshold": 0.7,
        }
    )

    assert first == reordered
    assert first != changed
    assert len(first) == 64
