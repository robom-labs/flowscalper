"""연구 매니페스트·시계열 누수·선택편향 보정의 결정적 계약을 검증한다."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, replace
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
from backend.app.research.gates import EvidenceEpoch, HypothesisKey, HypothesisRegistry
from backend.app.research.protocol import validate_research_manifest
from backend.app.research.trial_history import ResearchTrialProposal


def _checksum(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _evidence_contract(
    protocol: ResearchProtocol,
    slices: tuple[DatasetSlice, ...],
) -> tuple[HypothesisRegistry, EvidenceEpoch]:
    ordered = sorted(slices, key=lambda row: (row.start_ts_ms, row.run_id))
    dataset_hash = _checksum([asdict(row) for row in ordered])
    parameter_hash = _checksum(
        {key: list(values) for key, values in sorted(protocol.parameter_grid.items())}
    )
    key = HypothesisKey(
        strategy_family="MICROSTRUCTURE",
        candidate_id=protocol.strategy_id,
        strategy_version=protocol.strategy_version,
        parameter_id="GRID-V1",
        exit_id="STRUCTURE-EXIT-V1",
        execution_policy="TAKER-IOC",
        filter_combination=("QUALITY-V1",),
        dataset_id="RESEARCH-DATASET-V1",
        parameter_hash=parameter_hash,
        cost_profile=protocol.cost_profile,
        dataset_hash=dataset_hash,
        feature_version=protocol.feature_version,
        label_version=protocol.label_version,
        engine_version=protocol.engine_version,
    )
    registry = HypothesisRegistry().register(protocol.hypothesis_id, key)
    epoch = EvidenceEpoch(
        epoch_id="EPOCH-RESEARCH-001",
        opened_ts_ms=0,
        closed_ts_ms=None,
        strategy_version=protocol.strategy_version,
        feature_version=protocol.feature_version,
        label_version=protocol.label_version,
        engine_version=protocol.engine_version,
        cost_model_version=protocol.cost_model_version,
        cost_profile=protocol.cost_profile,
        parameter_hash=parameter_hash,
        dataset_hash=dataset_hash,
        fee_model_version="FEE-V1",
        matching_model_version="MATCHING-V1",
        symbol_contract_version="SYMBOL-V1",
        data_adapter_version="ADAPTER-V1",
        hypothesis_registry_hash=registry.fingerprint(),
        hypothesis_key_fingerprint=key.fingerprint(),
    )
    return registry, epoch


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
        label_version="label-v1",
        engine_version="engine-v1",
        cost_model_version="cost-v1",
        cost_profile="BASE",
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
    registry, epoch = _evidence_contract(protocol, slices)

    first = protocol.manifest(
        slices,
        code_hash="c" * 40,
        config_hash="d" * 64,
        generated_ts_ms=1_000,
        hypothesis_registry=registry,
        evidence_epoch=epoch,
    )
    second = protocol.manifest(
        tuple(reversed(slices)),
        code_hash="c" * 40,
        config_hash="d" * 64,
        generated_ts_ms=1_000,
        hypothesis_registry=registry,
        evidence_epoch=epoch,
    )

    assert first == second
    assert first["schema_version"] == 2
    assert first["run_ids"] == ["run-a", "run-b"]
    assert first["protocol"]["horizon_seconds"] == (15, 30, 60, 180)
    assert len(first["dataset_hash"]) == len(first["parameter_hash"]) == 64
    assert first["paper_only"] is True
    assert first["real_orders_enabled"] is False
    assert first["auth_required"] is False
    assert first["hypothesis_registry"]["registry_hash"] == registry.fingerprint()
    assert first["evidence_epoch"]["epoch_fingerprint"] == epoch.fingerprint()
    validate_research_manifest(first)
    proposal = ResearchTrialProposal.from_manifest(
        first,
        dataset_member_fingerprints=("run-a:a", "run-b:b"),
    )
    assert proposal.evidence_epoch_id == epoch.epoch_id
    assert proposal.evidence_epoch_fingerprint == epoch.fingerprint()
    assert proposal.parameter_fingerprint == first["parameter_hash"]
    assert proposal.dataset_fingerprint == first["dataset_hash"]
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


def test_manifest_registry_epoch_collision_and_checksum_tampering_fail_closed() -> None:
    protocol = ResearchProtocol(
        hypothesis_id="HYP-MICRO-001",
        strategy_id="TEST_STRATEGY_V1",
        strategy_version="strategy-v1",
        feature_version="feature-v2",
        label_version="label-v1",
        engine_version="engine-v1",
        cost_model_version="cost-v1",
        cost_profile="BASE",
        parameter_grid={"threshold": (1.5, 2.0)},
        horizon_seconds=(15, 30),
        base_cost_bps=13,
        stress_cost_bps=25,
        seed=17,
        purge_ms=1_000,
        embargo_ms=1_000,
        falsification_criteria=("OOS EV <= 0",),
        baseline_ids=("NO_TRADE",),
    )
    slices = (
        DatasetSlice("run-a", "BINANCE_USDM", ("BTCUSDT",), 1_000, 2_000, 20, "a" * 64),
    )
    registry, epoch = _evidence_contract(protocol, slices)
    manifest = protocol.manifest(
        slices,
        code_hash="c" * 40,
        config_hash="d" * 64,
        generated_ts_ms=1_000,
        hypothesis_registry=registry,
        evidence_epoch=epoch,
    )

    tampered = copy.deepcopy(manifest)
    tampered["parameter_hash"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        validate_research_manifest(tampered)

    mixed_epoch = replace(epoch, cost_profile="STRESS")
    with pytest.raises(ValueError, match="섞였습니다"):
        protocol.manifest(
            slices,
            code_hash="c" * 40,
            config_hash="d" * 64,
            generated_ts_ms=1_000,
            hypothesis_registry=registry,
            evidence_epoch=mixed_epoch,
        )


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
            {row.observation_id for row in fold[name]} for name in ("train", "validation", "oos")
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


def test_pbo_is_not_invented_when_all_candidates_have_identical_fold_returns() -> None:
    pbo = probability_of_backtest_overfitting(
        {
            "candidate-a": (0.0, 0.0, 0.0, 0.0),
            "candidate-b": (0.0, 0.0, 0.0, 0.0),
        }
    )

    assert pbo == {
        "pbo": None,
        "combinations": 0,
        "logits": [],
        "status": "INSUFFICIENT_CROSS_SECTIONAL_VARIATION",
    }
