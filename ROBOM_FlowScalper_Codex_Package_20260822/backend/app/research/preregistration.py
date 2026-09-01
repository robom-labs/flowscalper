# ResearchProtocol V2의 dataset·parameter hash와 registry·epoch를 한 계약으로 사전등록한다.

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict

from backend.app.research.gates import EvidenceEpoch, HypothesisKey, HypothesisRegistry
from backend.app.research.protocol import DatasetSlice, ResearchProtocol


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def canonical_protocol_hashes(
    protocol: ResearchProtocol,
    dataset: Sequence[DatasetSlice],
) -> tuple[str, str]:
    """manifest와 같은 정렬·직렬화로 dataset hash와 parameter hash를 계산한다."""

    if not dataset:
        raise ValueError("연구 사전등록 dataset이 비어 있습니다.")
    ordered = sorted(dataset, key=lambda row: (row.start_ts_ms, row.run_id))
    dataset_hash = _canonical_hash([asdict(row) for row in ordered])
    parameter_hash = _canonical_hash(
        {
            key: list(values)
            for key, values in sorted(protocol.parameter_grid.items())
        }
    )
    return dataset_hash, parameter_hash


def preregister_research_protocol(
    protocol: ResearchProtocol,
    dataset: Sequence[DatasetSlice],
    *,
    strategy_family: str,
    parameter_id_prefix: str,
    exit_id: str,
    execution_policy: str,
    filter_combination: tuple[str, ...],
    dataset_id_prefix: str,
    epoch_id_prefix: str,
    fee_model_version: str,
    matching_model_version: str,
    symbol_contract_version: str,
    data_adapter_version: str,
) -> tuple[HypothesisRegistry, EvidenceEpoch]:
    """실제 canonical 입력에 결합된 단일 가설 registry와 evidence epoch를 만든다."""

    dataset_hash, parameter_hash = canonical_protocol_hashes(protocol, dataset)
    key = HypothesisKey(
        strategy_family=strategy_family,
        candidate_id=protocol.strategy_id,
        strategy_version=protocol.strategy_version,
        parameter_id=f"{parameter_id_prefix}-{parameter_hash[:16]}",
        exit_id=exit_id,
        execution_policy=execution_policy,
        filter_combination=filter_combination,
        dataset_id=f"{dataset_id_prefix}-{dataset_hash[:16]}",
        parameter_hash=parameter_hash,
        cost_profile=protocol.cost_profile,
        dataset_hash=dataset_hash,
        feature_version=protocol.feature_version,
        label_version=protocol.label_version,
        engine_version=protocol.engine_version,
    )
    registry = HypothesisRegistry().register(protocol.hypothesis_id, key)
    epoch = EvidenceEpoch(
        epoch_id=(
            f"{epoch_id_prefix}-{dataset_hash[:12]}-{parameter_hash[:12]}"
        ),
        opened_ts_ms=min(row.start_ts_ms for row in dataset),
        closed_ts_ms=max(row.end_ts_ms for row in dataset),
        strategy_version=protocol.strategy_version,
        feature_version=protocol.feature_version,
        label_version=protocol.label_version,
        engine_version=protocol.engine_version,
        cost_model_version=protocol.cost_model_version,
        cost_profile=protocol.cost_profile,
        parameter_hash=parameter_hash,
        dataset_hash=dataset_hash,
        fee_model_version=fee_model_version,
        matching_model_version=matching_model_version,
        symbol_contract_version=symbol_contract_version,
        data_adapter_version=data_adapter_version,
        hypothesis_registry_hash=registry.fingerprint(),
        hypothesis_key_fingerprint=key.fingerprint(),
    )
    return registry, epoch


__all__ = ["canonical_protocol_hashes", "preregister_research_protocol"]
