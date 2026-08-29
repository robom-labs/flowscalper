# 동결 100후보와 분리된 비용회수형 PAPER exit 파라미터 변형을 정의한다.

"""동결 100후보와 분리된 비용회수형 PAPER exit 파라미터 변형을 정의한다."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from backend.app.research.candidate_registry import (
    ALPHA_FAMILIES,
    ExitModuleSpec,
    ResearchTrialSpec,
    TrialLifecycle,
    trailing_policy_for_exit,
)

COST_COVERED_EXIT_VARIANT_BATCH_ID = "COST_COVERED_EARLY_TP_RUNNER_V1"
COST_COVERED_EXIT_VARIANT_FAMILY_IDS = ("F17", "F18", "F19", "F20")
COST_COVERED_EXIT_VARIANT = ExitModuleSpec(
    exit_id="E06",
    name="Cost-covered early TP runner",
    activation_rule="TP1_EXECUTED",
    exit_rule=(
        "0.8R에서 70%를 부분익절하고 나머지 30%를 비용 반영 본전과 "
        "ATR trail로 보호하며 3R에서 종료한다."
    ),
    parameters=(
        ("initial_stop_r", "1.0"),
        ("tp1_r", "0.8"),
        ("tp1_fraction", "0.7"),
        ("tp2_r", "3.0"),
        ("runner_fraction", "0.3"),
        ("weighted_gross_reward_r", "1.46"),
        ("breakeven", "ROUNDTRIP_FEE_PLUS_BUFFER"),
        ("atr_period", "14"),
        ("atr_multiplier", "2.5"),
        ("base_expected_total_cost_bps", "13"),
        ("stress_expected_total_cost_bps", "25"),
        ("minimum_weighted_net_reward_r", "1.2"),
    ),
    source_ids=(
        "FLOWSCALPER_COST_AWARE_ENTRY_REFUSAL",
        "FLOWSCALPER_PARTIAL_TP",
    ),
)


def cost_covered_exit_variant_trials() -> tuple[ResearchTrialSpec, ...]:
    """미세구조 alpha 네 개에 E06만 붙인 별도 PAPER 연구 batch를 반환한다."""

    families = {family.family_id: family for family in ALPHA_FAMILIES}
    trials = tuple(
        ResearchTrialSpec(
            trial_id=f"ALPHA_{family_id}_EXIT_E06_V1",
            trial_number=100 + index,
            alpha=families[family_id],
            exit=COST_COVERED_EXIT_VARIANT,
            lifecycle=TrialLifecycle.RESEARCH,
            screening_eligible=True,
        )
        for index, family_id in enumerate(COST_COVERED_EXIT_VARIANT_FAMILY_IDS, start=1)
    )
    if (
        len(trials) != len(COST_COVERED_EXIT_VARIANT_FAMILY_IDS)
        or len({trial.trial_id for trial in trials}) != len(trials)
        or any(
            not trial.alpha.execution_allowed
            or not trial.paper_only
            or trial.runtime_active
            or trial.live_shadow_enabled
            for trial in trials
        )
    ):
        raise ValueError("비용회수형 exit 변형 batch의 PAPER 연구 계약이 잘못됐습니다.")
    return trials


def cost_covered_exit_variant_manifest(
    *,
    code_version: str,
    generated_ts_utc: str,
    source_checksums: Mapping[str, str],
    parent_trial_manifest: Mapping[str, str],
) -> dict[str, Any]:
    """E06 변형과 원래 100후보의 계보를 함께 고정한 manifest를 만든다."""

    if not source_checksums or any(
        not path
        or len(checksum) != 64
        or any(character not in "0123456789abcdef" for character in checksum)
        for path, checksum in source_checksums.items()
    ):
        raise ValueError("비용회수형 변형 manifest source checksum 계약이 잘못됐습니다.")
    required_parent_keys = {"path", "manifest_sha256", "file_sha256"}
    if set(parent_trial_manifest) != required_parent_keys or any(
        not parent_trial_manifest[key] for key in required_parent_keys
    ):
        raise ValueError("비용회수형 변형의 원본 100후보 계보가 잘못됐습니다.")
    trials = cost_covered_exit_variant_trials()
    rows: list[dict[str, Any]] = []
    for trial in trials:
        row = asdict(trial)
        row["alpha"]["parameters"] = dict(trial.alpha.parameters)
        row["alpha"]["evaluator_id"] = trial.alpha.evaluator_id
        row["exit"]["parameters"] = dict(trial.exit.parameters)
        trailing_policy = trailing_policy_for_exit(trial.exit.exit_id)
        if trailing_policy is None:
            raise ValueError("E06 비용회수형 변형에는 trailing 계약이 필요합니다.")
        row["paper_execution_binding"] = {
            "fixed_tp_sl": False,
            "trailing_policy": {
                "policy_id": trailing_policy.policy_id,
                "model": trailing_policy.model.value,
                "activation_rule": trailing_policy.activation_rule.value,
                "activation_r": str(trailing_policy.activation_r),
                "partial_tp_required": trailing_policy.partial_tp_required,
                "breakeven_buffer_bps": str(
                    trailing_policy.breakeven_buffer_bps
                ),
                "atr_multiplier": str(trailing_policy.atr_multiplier),
            },
            "uses_paper_portfolio_engine": True,
            "real_orders_enabled": False,
        }
        rows.append(row)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_kind": "COST_COVERED_EXIT_VARIANT_BATCH",
        "batch_id": COST_COVERED_EXIT_VARIANT_BATCH_ID,
        "status": "PREREGISTERED_NOT_EXECUTED",
        "generated_ts_utc": generated_ts_utc,
        "code_version": code_version,
        "parent_trial_manifest": dict(parent_trial_manifest),
        "source_checksums": dict(sorted(source_checksums.items())),
        "trial_count": len(rows),
        "alpha_family_count": len(COST_COVERED_EXIT_VARIANT_FAMILY_IDS),
        "exit_module_count": 1,
        "screening_eligible_count": len(rows),
        "blocked_count": 0,
        "runtime_active_count": 0,
        "live_shadow_count": 0,
        "selection_limit": len(rows),
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "runtime_ai_enabled": False,
        "cost_contract": {
            "base_expected_total_cost_bps": 13,
            "stress_expected_total_cost_bps": 25,
            "depth_slippage": "EXECUTABLE_BOOK_WALK",
            "partial_fill": True,
        },
        "risk_contract": {
            "starting_equity_per_trial_profile_usdt": "1000",
            "profiles_per_trial": ["BASE", "STRESS"],
            "risk_per_trade_fraction": "0.005",
        },
        "trials": rows,
    }
    checksum_material = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest["manifest_sha256"] = hashlib.sha256(checksum_material.encode()).hexdigest()
    return manifest
