# 15개 registry 전략의 중복·충돌 정책 matrix를 결정적으로 생성한다.
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any

from backend.app.strategies.family import (
    ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
    StrategyRole,
)
from backend.app.strategies.orderflow_confirmation import OrderflowConfirmationRuntime
from backend.app.strategies.registry import StrategyDescriptor, StrategyRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "evidence" / "V6_STRATEGY_CONFLICT_MATRIX.json"
ARBITRATION_PRIORITY = (
    "evidence_tier",
    "stress_cost_adjusted_expectancy",
    "cost_coverage",
    "liquidity_quality",
    "setup_freshness",
    "diversification",
)
RUNTIME_POLICY_SOURCE = (
    "backend/app/strategies/family.py",
    "backend/app/strategies/registry.py",
    "pasted-text-1.txt:941-1023",
)


def _descriptor_metadata(descriptor: StrategyDescriptor) -> dict[str, object]:
    return {
        "family_id": descriptor.family_id.value,
        "role": descriptor.role.value,
        "horizon_class": descriptor.horizon_class,
        "is_current_variant": descriptor.is_current_variant,
        "user_visible_by_default": descriptor.user_visible_by_default,
        "default_research_enabled": descriptor.default_research_enabled,
        "final_ranking_eligible": descriptor.final_ranking_eligible,
        "superseded_by_strategy_id": descriptor.superseded_by_strategy_id,
    }


def _overlap_evidence(
    descriptor_a: StrategyDescriptor,
    descriptor_b: StrategyDescriptor,
) -> dict[str, object]:
    if StrategyRole.LEGACY in {descriptor_a.role, descriptor_b.role}:
        applicability = "HISTORICAL_ONLY_LEGACY_NO_NEW_ENTRY"
    elif StrategyRole.FILTER in {descriptor_a.role, descriptor_b.role}:
        applicability = "NOT_APPLICABLE_FILTER_DOES_NOT_CREATE_CANDIDATE_PLAN"
    else:
        applicability = "PAPER_RESEARCH_PAIR"
    return {
        "value": None,
        "evidence_status": "NOT_RUN",
        "applicability": applicability,
        "reason": "동일 입력의 pair-level signal timestamp overlap 측정을 실행하지 않았습니다.",
    }


def _shared_features(
    descriptor_a: StrategyDescriptor,
    descriptor_b: StrategyDescriptor,
) -> dict[str, object]:
    timeframes = sorted(
        set(descriptor_a.required_timeframes) & set(descriptor_b.required_timeframes)
    )
    regimes = sorted(
        regime.value
        for regime in set(descriptor_a.supported_regimes) & set(descriptor_b.supported_regimes)
    )
    market_data = sorted(
        set(descriptor_a.research_contract.required_market_data)
        & set(descriptor_b.research_contract.required_market_data)
    )
    return {
        "required_timeframes": timeframes,
        "supported_regimes": regimes,
        "required_market_data": market_data,
        "derivation": "exact registry set intersection",
    }


def _shared_exit(
    descriptor_a: StrategyDescriptor,
    descriptor_b: StrategyDescriptor,
) -> dict[str, object]:
    return {
        "same_exit_model": descriptor_a.exit_model == descriptor_b.exit_model,
        "exit_model_a": descriptor_a.exit_model,
        "exit_model_b": descriptor_b.exit_model,
        "same_exit_style": descriptor_a.exit_style is descriptor_b.exit_style,
        "exit_style_a": descriptor_a.exit_style.value,
        "exit_style_b": descriptor_b.exit_style.value,
        "same_tp_r_targets": (
            descriptor_a.take_profit_1_r == descriptor_b.take_profit_1_r
            and descriptor_a.take_profit_2_r == descriptor_b.take_profit_2_r
        ),
        "tp_r_targets_a": [
            str(descriptor_a.take_profit_1_r),
            str(descriptor_a.take_profit_2_r),
        ],
        "tp_r_targets_b": [
            str(descriptor_b.take_profit_1_r),
            str(descriptor_b.take_profit_2_r),
        ],
        "derivation": "exact registry exit contract comparison",
    }


def _resource_cost(
    descriptor_a: StrategyDescriptor,
    descriptor_b: StrategyDescriptor,
    shared_features: Mapping[str, object],
) -> dict[str, object]:
    entry_count = sum(
        descriptor.role is StrategyRole.ENTRY for descriptor in (descriptor_a, descriptor_b)
    )
    shared_timeframes = shared_features["required_timeframes"]
    if not isinstance(shared_timeframes, Sequence):
        raise TypeError("shared required_timeframes가 sequence가 아닙니다.")
    return {
        "shared_capital_position_slots": min(entry_count, 1),
        "strategy_league_independent_paper_accounts": 4,
        "strategy_league_entry_enabled_paper_accounts": entry_count * 2,
        "paper_profiles_per_registry_strategy": 2,
        "shared_timeframe_input_count": len(shared_timeframes),
        "horizon_compute_overlap": (
            "SAME_HORIZON_REUSE_POSSIBLE"
            if descriptor_a.horizon_class == descriptor_b.horizon_class
            else "MIXED_HORIZON_SEPARATE_WINDOWS"
        ),
        "evidence_status": "DERIVED_REGISTRY_CONTRACT",
        "latency_or_profitability_estimate": None,
    }


def _shared_capital_policy(
    descriptor_a: StrategyDescriptor,
    descriptor_b: StrategyDescriptor,
    current_by_family: Mapping[str, str],
) -> dict[str, object]:
    descriptors = (descriptor_a, descriptor_b)
    eligible = sorted(
        descriptor.strategy_id
        for descriptor in descriptors
        if descriptor.role is StrategyRole.ENTRY and descriptor.is_current_variant
    )
    if any(descriptor.role is StrategyRole.FILTER for descriptor in descriptors):
        code = (
            "CURRENT_ENTRY_WITH_FILTER_NO_CANDIDATE_PLAN"
            if eligible
            else "FILTER_NO_CANDIDATE_PLAN"
        )
    elif any(descriptor.role is StrategyRole.LEGACY for descriptor in descriptors):
        code = (
            "CURRENT_ENTRY_WITH_LEGACY_HISTORY_ONLY"
            if eligible
            else "LEGACY_HISTORY_ONLY_NO_NEW_ENTRY"
        )
    else:
        if descriptor_a.family_id is descriptor_b.family_id:
            code = "SAME_FAMILY_CURRENT_VARIANT_ONLY"
        elif len(eligible) == 2:
            code = "DIFFERENT_FAMILY_SINGLE_WINNER_BY_EVIDENCE"
        elif len(eligible) == 1:
            code = "CURRENT_VARIANT_ONLY"
        else:
            code = "NO_CURRENT_SHARED_CAPITAL_CANDIDATE"
    family_currents = {
        family_id: current_by_family[family_id]
        for family_id in sorted({descriptor_a.family_id.value, descriptor_b.family_id.value})
        if family_id in current_by_family
    }
    return {
        "code": code,
        "eligible_strategy_ids_in_pair": eligible,
        "family_current_variant_ids": family_currents,
        "same_side_rule": "AT_MOST_ONE_POSITION_PER_SYMBOL",
        "opposite_side_rule": "NO_LONG_SHORT_SIMULTANEOUS_ENTRY_NO_TRADE_IF_UNRESOLVED",
        "arbitration_priority": (
            list(ARBITRATION_PRIORITY)
            if code == "DIFFERENT_FAMILY_SINGLE_WINNER_BY_EVIDENCE"
            else []
        ),
        "raw_win_rate_priority_forbidden": True,
    }


def _league_policy(
    descriptor_a: StrategyDescriptor,
    descriptor_b: StrategyDescriptor,
) -> dict[str, object]:
    independent = sorted(
        descriptor.strategy_id
        for descriptor in (descriptor_a, descriptor_b)
        if descriptor.role is StrategyRole.ENTRY
    )
    visible = sorted(
        descriptor.strategy_id
        for descriptor in (descriptor_a, descriptor_b)
        if descriptor.role is StrategyRole.ENTRY and descriptor.is_current_variant
    )
    return {
        "code": (
            "INDEPENDENT_PAPER_ACCOUNTS"
            if independent
            else "NO_NEW_ACCOUNT_LEGACY_OR_FILTER_ONLY"
        ),
        "independent_account_strategy_ids": independent,
        "user_default_visible_strategy_ids": visible,
        "opposite_sides_may_be_recorded_independently": bool(independent),
        "mix_with_shared_capital_results": False,
    }


def _conflict_policy(
    descriptor_a: StrategyDescriptor,
    descriptor_b: StrategyDescriptor,
    current_by_family: Mapping[str, str],
) -> dict[str, object]:
    return {
        "shared_capital": _shared_capital_policy(
            descriptor_a,
            descriptor_b,
            current_by_family,
        ),
        "strategy_league": _league_policy(descriptor_a, descriptor_b),
        "filter_policy": {
            "candidate_plan_forbidden": any(
                descriptor.role is StrategyRole.FILTER
                for descriptor in (descriptor_a, descriptor_b)
            ),
            "trade_count_forbidden": any(
                descriptor.role is StrategyRole.FILTER
                for descriptor in (descriptor_a, descriptor_b)
            ),
            "evaluation_metric": "filter_uplift",
        },
        "legacy_policy": {
            "history_only": any(
                descriptor.role is StrategyRole.LEGACY
                for descriptor in (descriptor_a, descriptor_b)
            ),
            "new_entry_forbidden": any(
                descriptor.role is StrategyRole.LEGACY
                for descriptor in (descriptor_a, descriptor_b)
            ),
            "legacy_strategy_ids": sorted(
                descriptor.strategy_id
                for descriptor in (descriptor_a, descriptor_b)
                if descriptor.role is StrategyRole.LEGACY
            ),
            "new_entry_forbidden_strategy_ids": sorted(
                descriptor.strategy_id
                for descriptor in (descriptor_a, descriptor_b)
                if descriptor.role is StrategyRole.LEGACY
            ),
        },
    }


def _pair_row(
    descriptor_a: StrategyDescriptor,
    descriptor_b: StrategyDescriptor,
    current_by_family: Mapping[str, str],
) -> dict[str, object]:
    shared_features = _shared_features(descriptor_a, descriptor_b)
    return {
        "strategy_a": descriptor_a.strategy_id,
        "strategy_b": descriptor_b.strategy_id,
        "strategy_a_metadata": _descriptor_metadata(descriptor_a),
        "strategy_b_metadata": _descriptor_metadata(descriptor_b),
        "same_family": descriptor_a.family_id is descriptor_b.family_id,
        "same_horizon": descriptor_a.horizon_class == descriptor_b.horizon_class,
        "same_symbol": True,
        "same_symbol_scope": {
            "scope": "DYNAMIC_PUBLIC_MARKET_UNIVERSE_INTERSECTION",
            "meaning": "pair가 동일 symbol에 적용될 수 있음. 실제 동시 signal을 의미하지 않음.",
            "evidence_status": "DERIVED_TARGET_UNIVERSE_CONTRACT",
            "observed_concurrent_symbol_overlap": None,
        },
        "same_side_signal_overlap": _overlap_evidence(descriptor_a, descriptor_b),
        "opposite_side_overlap": _overlap_evidence(descriptor_a, descriptor_b),
        "PnL_correlation": {
            "value": None,
            "evidence_status": "NOT_RUN",
            "profile": None,
            "sample_size": 0,
            "reason": (
                "동일 Run·symbol·opportunity·profile pair PnL 상관 측정을 "
                "실행하지 않았습니다."
            ),
        },
        "shared_features": shared_features,
        "shared_exit": _shared_exit(descriptor_a, descriptor_b),
        "resource_cost": _resource_cost(descriptor_a, descriptor_b, shared_features),
        "conflict_policy": _conflict_policy(
            descriptor_a,
            descriptor_b,
            current_by_family,
        ),
    }


def build_conflict_matrix(registry: StrategyRegistry | None = None) -> dict[str, object]:
    selected_registry = registry or StrategyRegistry()
    strategy_ids = tuple(sorted(selected_registry.strategy_ids))
    descriptors = {
        strategy_id: selected_registry.descriptor(strategy_id) for strategy_id in strategy_ids
    }
    current_by_family = {
        descriptor.family_id.value: descriptor.strategy_id
        for descriptor in descriptors.values()
        if descriptor.is_current_variant
    }
    rows = [
        _pair_row(descriptors[strategy_a], descriptors[strategy_b], current_by_family)
        for strategy_a, strategy_b in combinations(strategy_ids, 2)
    ]
    expected_pair_count = len(strategy_ids) * (len(strategy_ids) - 1) // 2
    pair_keys = {(str(row["strategy_a"]), str(row["strategy_b"])) for row in rows}
    role_counts = {
        role.value: sum(descriptor.role is role for descriptor in descriptors.values())
        for role in StrategyRole
    }
    virtual_filter = OrderflowConfirmationRuntime().status()
    virtual_filter_contract = {
        "strategy_id": ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
        "included_in_registry_pair_matrix": (
            ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID in strategy_ids
        ),
        "creates_candidate_plan": virtual_filter.get("creates_candidate_plan"),
        "trade_count_delta": virtual_filter.get("trade_count_delta"),
        "account_count_delta": virtual_filter.get("account_count_delta"),
        "evidence_status": "DERIVED_RUNTIME_FILTER_CONTRACT",
    }
    family_counts = {
        family_id: sum(
            descriptor.family_id.value == family_id for descriptor in descriptors.values()
        )
        for family_id in sorted({descriptor.family_id.value for descriptor in descriptors.values()})
    }
    document: dict[str, object] = {
        "schema": "flowscalper.v6_strategy_conflict_matrix.v1",
        "generator": "scripts/build_v6_conflict_matrix.py",
        "policy_sources": list(RUNTIME_POLICY_SOURCE),
        "strategy_count": len(strategy_ids),
        "expected_unordered_pair_count": expected_pair_count,
        "actual_unordered_pair_count": len(rows),
        "coverage": {
            "all_registry_strategies": list(strategy_ids),
            "covered_strategy_count": len(
                {strategy_id for pair in pair_keys for strategy_id in pair}
            ),
            "unique_pair_count": len(pair_keys),
            "duplicate_pair_count": len(rows) - len(pair_keys),
            "unordered_pairs_complete": len(pair_keys) == expected_pair_count,
            "lexicographic_pair_order": all(a < b for a, b in pair_keys),
            "role_counts": role_counts,
            "family_counts": family_counts,
            "current_variant_ids": sorted(current_by_family.values()),
            "virtual_filter_contract": virtual_filter_contract,
        },
        "invariants": {
            "pair_count_formula": "n*(n-1)/2",
            "strategy_count_is_15": len(strategy_ids) == 15,
            "pair_count_is_105": expected_pair_count == len(rows) == 105,
            "all_pairs_unique": len(pair_keys) == len(rows),
            "all_pnl_correlations_not_run": all(
                isinstance(row["PnL_correlation"], Mapping)
                and row["PnL_correlation"]["value"] is None
                and row["PnL_correlation"]["evidence_status"] == "NOT_RUN"
                for row in rows
            ),
            "filter_never_creates_candidate_plan": (
                role_counts.get("FILTER") == 0
                and virtual_filter_contract["included_in_registry_pair_matrix"] is False
                and virtual_filter_contract["creates_candidate_plan"] is False
                and virtual_filter_contract["trade_count_delta"] == 0
                and virtual_filter_contract["account_count_delta"] == 0
            ),
            "legacy_and_superseded_default_on_forbidden": all(
                not descriptor.user_visible_by_default
                and not descriptor.default_research_enabled
                and not descriptor.final_ranking_eligible
                for descriptor in descriptors.values()
                if descriptor.role is StrategyRole.LEGACY
                or descriptor.superseded_by_strategy_id is not None
            ),
            "shared_and_league_results_never_mixed": True,
        },
        "policy_contract": {
            "shared_capital": {
                "same_family_same_side": "CURRENT_VARIANT_ONLY_CHALLENGER_SHADOW",
                "different_family_same_symbol_same_side": "ONE_WINNER_BY_ARBITRATION_PRIORITY",
                "opposite_side": "NO_SIMULTANEOUS_LONG_SHORT_NO_TRADE_IF_UNRESOLVED",
                "arbitration_priority": list(ARBITRATION_PRIORITY),
                "raw_win_rate_priority_forbidden": True,
            },
            "strategy_league": {
                "variant_accounts": "INDEPENDENT_PAPER_ACCOUNTS_ALLOWED",
                "user_default_visibility": "CURRENT_VARIANT_ONLY",
                "opposite_sides": "INDEPENDENT_RESEARCH_ACCOUNTS_ALLOWED",
                "mix_with_shared_capital_results": False,
            },
            "filter": {
                "candidate_plan_forbidden": True,
                "account_creation": "FORBIDDEN",
                "trade_count_forbidden": True,
                "evaluation_metric": "filter_uplift",
            },
            "legacy": {
                "default_on_forbidden": True,
                "new_entry_forbidden": True,
                "history_retained": True,
            },
        },
        "pairs": rows,
    }
    return document


def render_conflict_matrix(document: Mapping[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_conflict_matrix(output: Path = DEFAULT_OUTPUT) -> dict[str, object]:
    document = build_conflict_matrix()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_conflict_matrix(document), encoding="utf-8")
    return document


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="V6 strategy conflict matrix를 생성합니다.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = _parse_arguments()
    document = build_conflict_matrix()
    rendered = render_conflict_matrix(document)
    if arguments.check:
        if (
            not arguments.output.is_file()
            or arguments.output.read_text(encoding="utf-8") != rendered
        ):
            raise SystemExit(f"conflict matrix가 현재 registry와 다릅니다: {arguments.output}")
        print(f"PASS: V6 conflict matrix {document['actual_unordered_pair_count']}쌍 일치")
        return
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    print(
        f"PASS: V6 conflict matrix {document['actual_unordered_pair_count']}쌍 생성 · "
        f"{arguments.output}"
    )


if __name__ == "__main__":
    main()
