# V6 연구 후보의 사전등록과 ARMED_SETUP 상태 전이를 검증한다.

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.research.v6_candidates import (
    ArmedSetup,
    V6ResearchState,
    v6_preregistered_variants,
    v6_preregistration_manifest,
)
from scripts import compare_v2_v3


def _valid_comparison_row() -> dict[str, object]:
    return {
        "same_frozen_input": True,
        "base_sample_size": 100,
        "stress_sample_size": 100,
        "base_expectancy": 0.20,
        "stress_expectancy": 0.10,
        "base_cost_coverage": 2.5,
        "stress_cost_coverage": 1.5,
        "base_expectancy_delta": 0.02,
        "stress_expectancy_delta": 0.01,
        "drawdown_delta": 0.0,
        "cost_burden_delta": -0.01,
        "oos_lower_bound": 0.10,
        "dsr": 1.0,
        "pbo": 0.0,
        "operational_regression": False,
    }


def _write_json(path: Path, payload: object) -> str:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return compare_v2_v3._sha256(path)


def _raw_result_rows(
    *,
    strategy_id: str,
    measurement_id: str,
    run_id: str,
    replay_id: str,
    base_expectancy: float,
    stress_expectancy: float,
    base_cost: float,
    stress_cost: float,
    base_cost_coverage: float,
    stress_cost_coverage: float,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for profile, expectancy, cost, coverage in (
        ("BASE", base_expectancy, base_cost, base_cost_coverage),
        ("STRESS", stress_expectancy, stress_cost, stress_cost_coverage),
    ):
        for index in range(100):
            risk = 10.0
            net = round(expectancy * risk, 8)
            per_cost = round(cost / len(compare_v2_v3.RAW_COST_FIELDS), 8)
            rows.append(
                {
                    "run_id": run_id,
                    "replay_id": replay_id,
                    "measurement_id": measurement_id,
                    "strategy_id": strategy_id,
                    "strategy_version": "fixture-v1",
                    "profile": profile,
                    "evaluation_scope": "OOS",
                    "opportunity_id": f"opportunity-{index}",
                    "symbol": "BTCUSDT",
                    "side": "LONG" if index % 2 == 0 else "SHORT",
                    "entry_ts_ms": 1_000 + index,
                    "exit_ts_ms": 1_000 + index,
                    "period_id": f"period-{index % 4}",
                    "dataset_record_id": f"candle-{index}",
                    "event_id": f"trade-{index}",
                    "gross_pnl_usdt": round(net + cost, 8),
                    "fee_usdt": per_cost,
                    "spread_cost_usdt": per_cost,
                    "slippage_usdt": per_cost,
                    "latency_penalty_usdt": per_cost,
                    "funding_usdt": per_cost,
                    "net_pnl_usdt": net,
                    "initial_risk_usdt": risk,
                    "return_r": expectancy,
                    "gross_mfe_usdt": round(coverage * cost, 8),
                }
            )
    return rows


def _raw_measurement_payload(
    *,
    generated_ts: str,
    role: str,
    measurement_id: str,
    strategy_ids: list[str],
    binding: dict[str, object],
    rows: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "schema": compare_v2_v3.RAW_MEASUREMENT_SCHEMA,
        "kind": "V6_RAW_MEASUREMENT_RESULTS",
        "generated_ts_utc": generated_ts,
        "source_worktree_clean_at_measurement": True,
        "role": role,
        "measurement_id": measurement_id,
        "strategy_ids": strategy_ids,
        "result_count": len(rows),
        "results": rows,
        "cscv_splits": [
            {
                "split_id": "split-0",
                "in_sample_period_ids": ["period-0", "period-1"],
                "out_of_sample_period_ids": ["period-2", "period-3"],
            },
            {
                "split_id": "split-1",
                "in_sample_period_ids": ["period-0", "period-2"],
                "out_of_sample_period_ids": ["period-1", "period-3"],
            },
            {
                "split_id": "split-2",
                "in_sample_period_ids": ["period-0", "period-3"],
                "out_of_sample_period_ids": ["period-1", "period-2"],
            },
            {
                "split_id": "split-3",
                "in_sample_period_ids": ["period-1", "period-2"],
                "out_of_sample_period_ids": ["period-0", "period-3"],
            },
        ],
        "operational_checks": {
            check_id: True for check_id in compare_v2_v3.OPERATIONAL_CHECK_IDS
        },
        **binding,
    }


def _fixed_input_payload_with_real_artifacts(tmp_path: Path) -> Path:
    generated_ts = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    source_commit = "a" * 40
    run_id = "run-v6-fixed-input"
    replay_id = "replay-v6-fixed-input"
    cost_model_version = "v6-base-stress-1"
    window = {"start_ts_ms": 1_000, "end_ts_ms": 2_000}
    dataset_records = [
        {
            "source_type": "PUBLIC_MARKET",
            "source": "BINANCE_PUBLIC",
            "symbol": "BTCUSDT",
            "record_id": f"candle-{index}",
            "open_ts_ms": 1_000 + index,
            "interval": "1m",
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 10.0 + index,
        }
        for index in range(100)
    ]
    event_records = [
        {
            "source_type": "PUBLIC_MARKET",
            "source": "BINANCE_PUBLIC",
            "symbol": "BTCUSDT",
            "event_id": f"trade-{index}",
            "event_ts_ms": 1_000 + index,
            "event_type": "TRADE",
            "sequence": index,
            "price": 100.5 + index,
            "quantity": 1.0 + index / 100,
            "aggressor_side": "BUY" if index % 2 == 0 else "SELL",
        }
        for index in range(100)
    ]

    dataset_path = tmp_path / "dataset.json"
    event_path = tmp_path / "events.json"
    dataset_artifact_sha = _write_json(dataset_path, dataset_records)
    event_artifact_sha = _write_json(event_path, event_records)
    observed_window = {"start_ts_ms": 1_000, "end_ts_ms": 1_099}
    manifest_common = {
        "schema_version": 1,
        "schema": compare_v2_v3.DATA_MANIFEST_SCHEMA,
        "run_id": run_id,
        "replay_id": replay_id,
        "window": window,
        "observed_window": observed_window,
        "symbols": ["BTCUSDT"],
        "public_sources": {
            "BINANCE_PUBLIC": "https://api.binance.com/api/v3/klines"
        },
    }
    dataset_manifest_path = tmp_path / "dataset-manifest.json"
    dataset_manifest_sha = _write_json(
        dataset_manifest_path,
        manifest_common
        | {
            "kind": "DATASET",
            "record_count": 100,
            "intervals": ["1m"],
            "artifacts": [
                {
                    "path": dataset_path.name,
                    "sha256": dataset_artifact_sha,
                    "format": "JSON",
                    "record_count": 100,
                    "timestamp_field": "open_ts_ms",
                    **observed_window,
                }
            ],
        },
    )
    event_manifest_path = tmp_path / "event-manifest.json"
    event_manifest_sha = _write_json(
        event_manifest_path,
        manifest_common
        | {
            "kind": "EVENT_SET",
            "event_count": 100,
            "event_types": ["TRADE"],
            "artifacts": [
                {
                    "path": event_path.name,
                    "sha256": event_artifact_sha,
                    "format": "JSON",
                    "event_count": 100,
                    "timestamp_field": "event_ts_ms",
                    **observed_window,
                }
            ],
        },
    )
    binding = {
        "run_id": run_id,
        "replay_id": replay_id,
        "cost_model_version": cost_model_version,
        "profile_ids": ["BASE", "STRESS"],
        "window": window,
        "dataset_manifest_sha256": dataset_manifest_sha,
        "event_set_manifest_sha256": event_manifest_sha,
        "dataset_record_count": 100,
        "event_set_event_count": 100,
        "source_commit": source_commit,
    }
    binding["sample_lineage_sha256"] = compare_v2_v3._fixed_input_lineage_sha256(
        binding
    )
    comparison_rows: list[dict[str, object]] = []
    for index, variant in enumerate(v6_preregistered_variants()):
        baseline_id = f"baseline-{index}"
        candidate_id = f"candidate-{index}"
        baseline_strategy_ids = list(variant.baseline_strategy_ids)
        candidate_strategy_ids = [variant.strategy_id]
        measurement_common = {
            "schema_version": 1,
            "schema": compare_v2_v3.MEASUREMENT_SCHEMA,
            "kind": "V6_STRATEGY_MEASUREMENT",
            "generated_ts_utc": generated_ts,
            "source_worktree_clean_at_measurement": True,
            **binding,
        }
        baseline_raw_path = tmp_path / f"baseline-{index}-raw.json"
        baseline_raw_sha = _write_json(
            baseline_raw_path,
            _raw_measurement_payload(
                generated_ts=generated_ts,
                role="BASELINE",
                measurement_id=baseline_id,
                strategy_ids=baseline_strategy_ids,
                binding=binding,
                rows=_raw_result_rows(
                    strategy_id=baseline_strategy_ids[0],
                    measurement_id=baseline_id,
                    run_id=run_id,
                    replay_id=replay_id,
                    base_expectancy=0.18,
                    stress_expectancy=0.09,
                    base_cost=0.5,
                    stress_cost=0.6,
                    base_cost_coverage=2.0,
                    stress_cost_coverage=1.5,
                ),
            ),
        )
        baseline_path = tmp_path / f"baseline-{index}.json"
        baseline_sha = _write_json(
            baseline_path,
            measurement_common
            | {
                "role": "BASELINE",
                "measurement_id": baseline_id,
                "strategy_ids": baseline_strategy_ids,
                "raw_results_path": baseline_raw_path.name,
                "raw_results_sha256": baseline_raw_sha,
                "metrics": {
                    "base_expectancy": 0.18,
                    "stress_expectancy": 0.09,
                    "drawdown": 0.0,
                    "cost_burden": 0.06,
                },
            },
        )
        candidate_raw_path = tmp_path / f"candidate-{index}-raw.json"
        candidate_raw_sha = _write_json(
            candidate_raw_path,
            _raw_measurement_payload(
                generated_ts=generated_ts,
                role="CANDIDATE",
                measurement_id=candidate_id,
                strategy_ids=candidate_strategy_ids,
                binding=binding,
                rows=_raw_result_rows(
                    strategy_id=variant.strategy_id,
                    measurement_id=candidate_id,
                    run_id=run_id,
                    replay_id=replay_id,
                    base_expectancy=0.20,
                    stress_expectancy=0.10,
                    base_cost=0.4,
                    stress_cost=0.5,
                    base_cost_coverage=2.5,
                    stress_cost_coverage=1.5,
                ),
            ),
        )
        candidate_path = tmp_path / f"candidate-{index}.json"
        candidate_sha = _write_json(
            candidate_path,
            measurement_common
            | {
                "role": "CANDIDATE",
                "measurement_id": candidate_id,
                "strategy_ids": candidate_strategy_ids,
                "raw_results_path": candidate_raw_path.name,
                "raw_results_sha256": candidate_raw_sha,
                "metrics": {
                    "base_sample_size": 100,
                    "stress_sample_size": 100,
                    "base_expectancy": 0.20,
                    "stress_expectancy": 0.10,
                    "base_cost_coverage": 2.5,
                    "stress_cost_coverage": 1.5,
                    "drawdown": 0.0,
                    "cost_burden": 0.05,
                    "oos_lower_bound": 0.10,
                    "dsr": 1.0,
                    "pbo": 0.0,
                    "operational_regression": False,
                },
            },
        )
        comparison = _valid_comparison_row()
        comparison_manifest_path = tmp_path / f"comparison-{index}.json"
        comparison_manifest_sha = _write_json(
            comparison_manifest_path,
            {
                "schema_version": 1,
                "schema": compare_v2_v3.COMPARISON_MANIFEST_SCHEMA,
                "kind": "V2_V3_COMPARISON",
                "generated_ts_utc": generated_ts,
                "source_worktree_clean_at_measurement": True,
                "strategy_id": variant.strategy_id,
                "baseline_strategy_ids": baseline_strategy_ids,
                "baseline_measurement_id": baseline_id,
                "candidate_measurement_id": candidate_id,
                "baseline_measurement_path": baseline_path.name,
                "baseline_measurement_sha256": baseline_sha,
                "candidate_measurement_path": candidate_path.name,
                "candidate_measurement_sha256": candidate_sha,
                "comparison": comparison,
                **binding,
            },
        )
        comparison_rows.append(
            {
                "strategy_id": variant.strategy_id,
                "baseline_strategy_ids": baseline_strategy_ids,
                "baseline_measurement_id": baseline_id,
                "candidate_measurement_id": candidate_id,
                "sample_lineage_sha256": binding["sample_lineage_sha256"],
                "comparison_manifest_path": comparison_manifest_path.name,
                "comparison_manifest_sha256": comparison_manifest_sha,
                **comparison,
            }
        )
    input_path = tmp_path / "fixed-input.json"
    _write_json(
        input_path,
        {
            "schema_version": 1,
            "schema": compare_v2_v3.FIXED_INPUT_SCHEMA,
            "generated_ts_utc": generated_ts,
            "source_commit": source_commit,
            "source_worktree_clean_at_measurement": True,
            "run_id": run_id,
            "replay_id": replay_id,
            "cost_model_version": cost_model_version,
            "profile_ids": ["BASE", "STRESS"],
            "window": window,
            "dataset_manifest_path": dataset_manifest_path.name,
            "dataset_manifest_sha256": dataset_manifest_sha,
            "event_set_manifest_path": event_manifest_path.name,
            "event_set_manifest_sha256": event_manifest_sha,
            "comparisons": comparison_rows,
        },
    )
    return input_path


def test_v6_variants_are_offline_challengers_and_do_not_replace_v2() -> None:
    variants = v6_preregistered_variants()

    assert {variant.strategy_id for variant in variants} == {
        "TREND_PULLBACK_RECLAIM_15M_V3",
        "MULTISPEED_TREND_RECLAIM_30M_V3",
        "BREAKOUT_RETEST_30M_V3",
        "EXHAUSTION_VWAP_REENTRY_V2",
    }
    assert all(variant.paper_only for variant in variants)
    assert all(not variant.current_variant for variant in variants)
    assert all(not variant.runtime_registered for variant in variants)
    assert all(not variant.live_shadow_enabled for variant in variants)
    assert all(variant.baseline_strategy_ids for variant in variants)
    assert all(variant.exit_ablation_ids for variant in variants)

    manifest = v6_preregistration_manifest(source_commit="ac5634a")
    assert manifest["status"] == "PREREGISTERED_NOT_EXECUTED"
    assert manifest["current_variant_changes"] == 0
    assert manifest["selection_or_promotion_performed"] is False
    assert manifest["real_orders_enabled"] is False
    assert manifest["funding_readiness"] == "NOT_READY"
    assert len(str(manifest["manifest_sha256"])) == 64


def test_armed_setup_triggers_once_and_cannot_be_reused() -> None:
    setup = ArmedSetup(
        strategy_id="TREND_PULLBACK_RECLAIM_15M_V3",
        setup_id="setup-1",
        side="LONG",
        armed_ts_ms=1_000,
        expires_ts_ms=46_000,
        maximum_completed_trigger_bars=9,
    )

    waiting = setup.advance(
        completed_bar_ts_ms=6_000,
        structure_valid=True,
        trigger_passed=False,
    )
    triggered = waiting.advance(
        completed_bar_ts_ms=11_000,
        structure_valid=True,
        trigger_passed=True,
    )
    repeated = triggered.advance(
        completed_bar_ts_ms=16_000,
        structure_valid=True,
        trigger_passed=True,
    )

    assert waiting.state is V6ResearchState.ARMED_SETUP
    assert waiting.completed_trigger_bars == 1
    assert triggered.state is V6ResearchState.TRIGGERED
    assert triggered.completed_trigger_bars == 2
    assert repeated == triggered


def test_armed_setup_invalidates_or_expires_fail_closed() -> None:
    setup = ArmedSetup(
        strategy_id="BREAKOUT_RETEST_30M_V3",
        setup_id="setup-2",
        side="SHORT",
        armed_ts_ms=10_000,
        expires_ts_ms=40_000,
        maximum_completed_trigger_bars=3,
    )

    invalidated = setup.advance(
        completed_bar_ts_ms=20_000,
        structure_valid=False,
        trigger_passed=True,
    )
    expired_by_time = setup.advance(
        completed_bar_ts_ms=50_000,
        structure_valid=True,
        trigger_passed=True,
    )
    near_limit = replace(setup, completed_trigger_bars=2)
    expired_by_count = near_limit.advance(
        completed_bar_ts_ms=30_000,
        structure_valid=True,
        trigger_passed=False,
    )

    assert invalidated.state is V6ResearchState.INVALIDATED
    assert expired_by_time.state is V6ResearchState.EXPIRED
    assert expired_by_count.state is V6ResearchState.EXPIRED
    assert expired_by_count.completed_trigger_bars == 3


def test_breakout_retest_uses_full_three_30m_bar_trigger_window() -> None:
    variant = next(
        row
        for row in v6_preregistered_variants()
        if row.strategy_id == "BREAKOUT_RETEST_30M_V3"
    )
    armed_ts_ms = 1_000
    expires_ts_ms = armed_ts_ms + variant.validity_minutes * 60_000
    setup = ArmedSetup(
        strategy_id=variant.strategy_id,
        setup_id="breakout-90m-window",
        side="LONG",
        armed_ts_ms=armed_ts_ms,
        expires_ts_ms=expires_ts_ms,
        maximum_completed_trigger_bars=variant.maximum_completed_trigger_bars,
    )

    waiting = setup
    for bar_number in range(1, 4):
        waiting = waiting.advance(
            completed_bar_ts_ms=armed_ts_ms
            + bar_number * variant.trigger_timeframe_seconds * 1_000,
            structure_valid=True,
            trigger_passed=False,
        )

    assert variant.validity_minutes == 90
    assert variant.trigger_timeframe_seconds == 300
    assert variant.maximum_completed_trigger_bars == 18
    assert any(
        "3개 완료 30분봉" in rule and "18개" in rule
        for rule in variant.entry_rules_ko
    )
    assert any(
        "3개 완료 30분봉" in rule and "18개" in rule
        for rule in variant.invalidation_rules_ko
    )
    assert waiting.state is V6ResearchState.ARMED_SETUP
    assert waiting.completed_trigger_bars == 3

    for bar_number in range(4, 19):
        waiting = waiting.advance(
            completed_bar_ts_ms=armed_ts_ms
            + bar_number * variant.trigger_timeframe_seconds * 1_000,
            structure_valid=True,
            trigger_passed=False,
        )

    assert waiting.state is V6ResearchState.EXPIRED
    assert waiting.completed_trigger_bars == 18

    expired_by_time = setup.advance(
        completed_bar_ts_ms=expires_ts_ms + 1,
        structure_valid=True,
        trigger_passed=True,
    )
    assert expired_by_time.state is V6ResearchState.EXPIRED


def test_armed_setup_completed_bar_consumption_is_idempotent_and_monotonic() -> None:
    setup = ArmedSetup(
        strategy_id="TREND_PULLBACK_RECLAIM_15M_V3",
        setup_id="setup-event-time",
        side="LONG",
        armed_ts_ms=1_000,
        expires_ts_ms=46_000,
        maximum_completed_trigger_bars=9,
    )
    first = setup.advance(
        completed_bar_ts_ms=6_000,
        structure_valid=True,
        trigger_passed=False,
    )

    duplicate = first.advance(
        completed_bar_ts_ms=6_000,
        structure_valid=True,
        trigger_passed=False,
    )

    assert duplicate == first
    assert duplicate.completed_trigger_bars == 1
    with pytest.raises(ValueError, match="서로 다릅니다"):
        first.advance(
            completed_bar_ts_ms=6_000,
            structure_valid=True,
            trigger_passed=True,
        )

    triggered_first = setup.advance(
        completed_bar_ts_ms=6_000,
        structure_valid=True,
        trigger_passed=True,
    )
    assert triggered_first.state is V6ResearchState.TRIGGERED
    with pytest.raises(ValueError, match="서로 다릅니다"):
        triggered_first.advance(
            completed_bar_ts_ms=6_000,
            structure_valid=True,
            trigger_passed=False,
        )
    with pytest.raises(ValueError, match="서로 다릅니다"):
        triggered_first.advance(
            completed_bar_ts_ms=6_000,
            structure_valid=False,
            trigger_passed=True,
        )
    with pytest.raises(ValueError, match="event-time"):
        first.advance(
            completed_bar_ts_ms=5_000,
            structure_valid=True,
            trigger_passed=False,
        )


def test_v3_comparison_requires_positive_absolute_ev_sample_and_cost_coverage() -> None:
    variant = v6_preregistered_variants()[0]
    valid = compare_v2_v3._evaluated_row(
        variant,
        _valid_comparison_row(),
        lineage_errors=[],
    )
    assert valid["comparison_status"] == "EVIDENCE_GATE_PASS"
    assert valid["promotion_eligible"] is True

    negative_absolute = _valid_comparison_row() | {"base_expectancy": -0.01}
    negative = compare_v2_v3._evaluated_row(
        variant,
        negative_absolute,
        lineage_errors=[],
    )
    assert negative["comparison_status"] == "EVIDENCE_GATE_FAIL"
    assert negative["promotion_eligible"] is False

    sparse = _valid_comparison_row() | {"base_sample_size": 99}
    sparse_row = compare_v2_v3._evaluated_row(variant, sparse, lineage_errors=[])
    assert sparse_row["comparison_status"] == "EVIDENCE_GATE_FAIL"

    uncovered = _valid_comparison_row() | {"stress_cost_coverage": 1.49}
    uncovered_row = compare_v2_v3._evaluated_row(
        variant,
        uncovered,
        lineage_errors=[],
    )
    assert uncovered_row["comparison_status"] == "EVIDENCE_GATE_FAIL"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_expectancy", float("nan")),
        ("stress_expectancy", float("inf")),
        ("same_frozen_input", "false"),
        ("operational_regression", "false"),
        ("base_sample_size", 100.5),
    ],
)
def test_v3_comparison_invalid_types_and_nonfinite_values_are_not_proven(
    field: str,
    value: object,
) -> None:
    variant = v6_preregistered_variants()[0]
    result = compare_v2_v3._evaluated_row(
        variant,
        _valid_comparison_row() | {field: value},
        lineage_errors=[],
    )
    assert result["data_status"] == "NOT_PROVEN_INVALID_FIELDS"
    assert result["promotion_eligible"] is False


def test_v3_comparison_rejects_missing_absolute_fields_and_duplicate_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variant = v6_preregistered_variants()[0]
    missing = _valid_comparison_row()
    del missing["base_expectancy"]
    missing_result = compare_v2_v3._evaluated_row(
        variant,
        missing,
        lineage_errors=[],
    )
    assert missing_result["data_status"] == "NOT_PROVEN_MISSING_FIELDS"

    duplicate = _valid_comparison_row() | {"strategy_id": variant.strategy_id}
    input_path = tmp_path / "duplicates.json"
    input_path.write_text(
        json.dumps({"comparisons": [duplicate, duplicate]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(compare_v2_v3, "_git_commit", lambda: "a" * 40)
    report = compare_v2_v3.build_report(input_path)
    assert report["status"] == "NOT_PROVEN"
    assert report["duplicate_strategy_ids"] == [variant.strategy_id]
    row = next(
        item for item in report["comparisons"] if item["strategy_id"] == variant.strategy_id
    )
    assert row["data_status"] == "NOT_PROVEN_DUPLICATE_STRATEGY_ID"
    assert report["promotion_performed"] is False


def test_v3_comparison_stays_not_proven_without_replay_recomputed_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = _fixed_input_payload_with_real_artifacts(tmp_path)
    monkeypatch.setattr(compare_v2_v3, "_git_commit", lambda: "a" * 40)
    monkeypatch.setattr(compare_v2_v3, "_source_worktree_clean", lambda: True)
    monkeypatch.setattr(
        compare_v2_v3,
        "_commits_have_equivalent_source",
        lambda _left, _right: True,
    )

    report = compare_v2_v3.build_report(input_path)

    assert report["status"] == "NOT_PROVEN"
    assert report["input_status"] == "FIXED_INPUT_RESULT_PROVIDED"
    assert report["input_binding_errors"] == []
    assert report["all_candidates_promotion_eligible"] is False
    assert all(
        row["comparison_status"] == "NOT_PROVEN"
        and "ROW_REPLAY_OUTCOMES_NOT_INDEPENDENTLY_RECOMPUTED"
        in row["lineage_errors"]
        for row in report["comparisons"]
    )
    assert report["promotion_performed"] is False


def test_v3_fixed_input_rejects_timestamp_value_rows_as_public_market_data(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "not-market-data.json"
    artifact_sha = _write_json(
        artifact_path,
        [{"ts_ms": 1_000 + index, "value": index} for index in range(2)],
    )
    manifest_path = tmp_path / "not-market-data-manifest.json"
    manifest = {
        "record_count": 2,
        "observed_window": {"start_ts_ms": None, "end_ts_ms": None},
        "symbols": [],
        "public_sources": {},
        "intervals": [],
        "artifacts": [
            {
                "path": artifact_path.name,
                "sha256": artifact_sha,
                "format": "JSON",
                "record_count": 2,
                "timestamp_field": "open_ts_ms",
            }
        ],
    }

    errors, _ = compare_v2_v3._validate_data_manifest_artifacts(
        manifest,
        manifest_path=manifest_path,
        kind="DATASET",
        count_field="record_count",
        expected_window={"start_ts_ms": 1_000, "end_ts_ms": 2_000},
    )

    assert "DATASET_ARTIFACT_0_ROW_0_SOURCE_TYPE_NOT_PUBLIC_MARKET" in errors
    assert "DATASET_ARTIFACT_0_ROW_0_IDENTITY_MISSING" in errors
    assert "DATASET_ARTIFACT_0_TIMESTAMP_INVALID" in errors


def test_v3_comparison_rejects_self_attested_manifests_without_data_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated_ts = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    source_commit = "a" * 40
    run_id = "fabricated-run"
    replay_id = "fabricated-replay"
    window = {"start_ts_ms": 1_000, "end_ts_ms": 2_000}
    common_manifest = {
        "schema_version": 1,
        "schema": compare_v2_v3.DATA_MANIFEST_SCHEMA,
        "run_id": run_id,
        "replay_id": replay_id,
        "window": window,
        "observed_window": {"start_ts_ms": 1_000, "end_ts_ms": 1_000},
    }
    dataset_manifest_path = tmp_path / "fabricated-dataset-manifest.json"
    dataset_manifest_sha = _write_json(
        dataset_manifest_path,
        common_manifest | {"kind": "DATASET", "record_count": 1},
    )
    event_manifest_path = tmp_path / "fabricated-event-manifest.json"
    event_manifest_sha = _write_json(
        event_manifest_path,
        common_manifest | {"kind": "EVENT_SET", "event_count": 1},
    )
    comparison_rows: list[dict[str, object]] = []
    for index, variant in enumerate(v6_preregistered_variants()):
        comparison_manifest_path = tmp_path / f"fabricated-comparison-{index}.json"
        comparison_manifest_sha = _write_json(
            comparison_manifest_path,
            {
                "schema_version": 1,
                "schema": compare_v2_v3.COMPARISON_MANIFEST_SCHEMA,
                "kind": "V2_V3_COMPARISON",
                "comparison": _valid_comparison_row(),
            },
        )
        comparison_rows.append(
            {
                "strategy_id": variant.strategy_id,
                "baseline_strategy_ids": list(variant.baseline_strategy_ids),
                "baseline_measurement_id": f"fabricated-baseline-{index}",
                "candidate_measurement_id": f"fabricated-candidate-{index}",
                "sample_lineage_sha256": "b" * 64,
                "comparison_manifest_path": comparison_manifest_path.name,
                "comparison_manifest_sha256": comparison_manifest_sha,
                **_valid_comparison_row(),
            }
        )
    input_path = tmp_path / "fabricated-fixed-input.json"
    _write_json(
        input_path,
        {
            "schema_version": 1,
            "schema": compare_v2_v3.FIXED_INPUT_SCHEMA,
            "generated_ts_utc": generated_ts,
            "source_commit": source_commit,
            "source_worktree_clean_at_measurement": True,
            "run_id": run_id,
            "replay_id": replay_id,
            "cost_model_version": "fabricated-cost-model",
            "profile_ids": ["BASE", "STRESS"],
            "window": window,
            "dataset_manifest_path": dataset_manifest_path.name,
            "dataset_manifest_sha256": dataset_manifest_sha,
            "event_set_manifest_path": event_manifest_path.name,
            "event_set_manifest_sha256": event_manifest_sha,
            "comparisons": comparison_rows,
        },
    )
    monkeypatch.setattr(compare_v2_v3, "_git_commit", lambda: source_commit)
    monkeypatch.setattr(compare_v2_v3, "_source_worktree_clean", lambda: True)
    monkeypatch.setattr(
        compare_v2_v3,
        "_commits_have_equivalent_source",
        lambda _left, _right: True,
    )

    report = compare_v2_v3.build_report(input_path)

    assert report["status"] == "NOT_PROVEN"
    assert report["all_candidates_promotion_eligible"] is False
    assert "DATASET_ARTIFACTS_MISSING" in report["input_binding_errors"]
    assert "EVENT_SET_ARTIFACTS_MISSING" in report["input_binding_errors"]
    assert all(
        row["data_status"] == "NOT_PROVEN_INPUT_LINEAGE"
        for row in report["comparisons"]
    )
    assert report["promotion_performed"] is False
