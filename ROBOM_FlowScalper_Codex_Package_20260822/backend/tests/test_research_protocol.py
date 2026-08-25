"""연구 매니페스트·시계열 누수·선택편향 보정의 결정적 계약을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.research import (
    DatasetSlice,
    ResearchObservation,
    ResearchProtocol,
    bootstrap_mean_interval,
    chronological_split,
    deflated_sharpe_ratio,
    finalize_research_manifest,
    probability_of_backtest_overfitting,
    walk_forward_folds,
)


def _observations(count: int = 20) -> list[ResearchObservation]:
    return [
        ResearchObservation(
            observation_id=f"obs-{index}",
            run_id="run-research",
            symbol="BTCUSDT",
            signal_ts_ms=index * 1_000,
            outcome_ts_ms=index * 1_000 + 400,
            net_bps=float(index % 3 - 1),
            regime="RANGE" if index % 2 else "TREND_UP",
        )
        for index in range(count)
    ]


def test_manifest_is_deterministic_complete_and_paper_only() -> None:
    protocol = ResearchProtocol(
        hypothesis_id="HYP-MICRO-001",
        strategy_id="TEST_STRATEGY_V1",
        strategy_version="strategy-v1",
        feature_version="feature-v2",
        cost_model_version="cost-v1",
        parameter_grid={"threshold": (1.5, 2.0), "confirmation_ms": (500, 1_000)},
        horizon_seconds=(15, 30, 60, 180),
        base_cost_bps=13,
        stress_cost_bps=25,
        seed=20260825,
        purge_ms=5_000,
        embargo_ms=5_000,
        falsification_criteria=("OOS BASE expectancy <= 0", "STRESS PF < 1"),
        baseline_ids=("NO_TRADE", "RANDOM_DIRECTION"),
    )
    slices = (
        DatasetSlice("run-b", "BINANCE_USDM", ("ETHUSDT",), 2_000, 3_000, 10, "b" * 64),
        DatasetSlice("run-a", "BINANCE_USDM", ("BTCUSDT",), 1_000, 1_900, 20, "a" * 64),
    )

    first = protocol.manifest(
        slices,
        code_hash="c" * 40,
        config_hash="d" * 64,
        generated_ts_ms=1_000,
    )
    second = protocol.manifest(
        tuple(reversed(slices)),
        code_hash="c" * 40,
        config_hash="d" * 64,
        generated_ts_ms=1_000,
    )

    assert first == second
    assert first["run_ids"] == ["run-a", "run-b"]
    assert first["protocol"]["horizon_seconds"] == (15, 30, 60, 180)
    assert len(first["dataset_hash"]) == len(first["parameter_hash"]) == 64
    assert first["paper_only"] is True
    assert first["real_orders_enabled"] is False
    assert first["auth_required"] is False
    executed = finalize_research_manifest(
        first,
        result={"oos": {"expectancy_bps": -1.0}},
        completed_ts_ms=2_000,
    )
    assert executed["status"] == "EXECUTED"
    assert len(executed["result_hash"]) == 64
    assert executed["manifest_checksum"] != first["manifest_checksum"]
    schema = json.loads(Path("schemas/research_manifest.schema.json").read_text())
    assert set(schema["required"]) <= set(first)


def test_chronological_split_enforces_purge_embargo_and_no_overlap() -> None:
    split = chronological_split(
        _observations(10),
        train_end_ts_ms=4_000,
        validation_end_ts_ms=7_000,
        purge_ms=500,
        embargo_ms=500,
    )

    assert [row.observation_id for row in split["train"]] == [
        "obs-0",
        "obs-1",
        "obs-2",
        "obs-3",
    ]
    assert [row.observation_id for row in split["validation"]] == ["obs-5", "obs-6"]
    assert [row.observation_id for row in split["oos"]] == ["obs-8", "obs-9"]
    assert max(row.outcome_ts_ms for row in split["train"]) < min(
        row.signal_ts_ms for row in split["validation"]
    )
    assert max(row.outcome_ts_ms for row in split["validation"]) < min(
        row.signal_ts_ms for row in split["oos"]
    )


def test_future_outcome_and_invalid_time_order_fail_closed() -> None:
    with pytest.raises(ValueError, match="outcome"):
        ResearchObservation("future", "run", "BTCUSDT", 2_000, 1_000, 1.0)
    with pytest.raises(ValueError, match="train"):
        chronological_split(
            _observations(),
            train_end_ts_ms=5_000,
            validation_end_ts_ms=5_000,
            purge_ms=0,
            embargo_ms=0,
        )


def test_walk_forward_folds_are_deterministic_and_disjoint() -> None:
    first = walk_forward_folds(
        _observations(30),
        train_size=10,
        validation_size=5,
        oos_size=5,
        purge_ms=100,
        embargo_ms=100,
        step_size=5,
    )
    second = walk_forward_folds(
        list(reversed(_observations(30))),
        train_size=10,
        validation_size=5,
        oos_size=5,
        purge_ms=100,
        embargo_ms=100,
        step_size=5,
    )

    assert first == second
    assert len(first) == 3
    for fold in first:
        identifiers = [
            {row.observation_id for row in fold[name]}
            for name in ("train", "validation", "oos")
        ]
        assert not identifiers[0] & identifiers[1]
        assert not identifiers[1] & identifiers[2]


def test_multiple_testing_and_bootstrap_are_reported_without_annualization() -> None:
    folds = {
        "stable": (1.0, 0.8, 0.9, 1.1, 0.7, 1.0),
        "overfit": (4.0, 4.0, 4.0, -3.0, -3.0, -3.0),
        "noise": (-1.0, 1.0, -1.0, 1.0, -1.0, 1.0),
    }
    pbo = probability_of_backtest_overfitting(folds)
    one_trial = deflated_sharpe_ratio(folds["stable"], trials=1)
    many_trials = deflated_sharpe_ratio(folds["stable"], trials=20)
    interval_a = bootstrap_mean_interval(folds["stable"], seed=17, resamples=500)
    interval_b = bootstrap_mean_interval(folds["stable"], seed=17, resamples=500)

    assert 0 <= float(pbo["pbo"]) <= 1
    assert pbo["combinations"] == 10
    assert one_trial["status"] == many_trials["status"] == "CALCULATED"
    assert float(many_trials["dsr_probability"]) <= float(one_trial["dsr_probability"])
    assert interval_a == interval_b
    assert float(interval_a["lower"]) <= float(interval_a["upper"])
