# 두 archive 연구 스크립트가 V2 canonical 가설 registry·epoch를 manifest에 연결하는지 검증한다.

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace

from backend.app.research import DatasetSlice, ResearchProtocol
from backend.app.research.gates import EvidenceEpoch, HypothesisRegistry
from backend.app.research.protocol import validate_research_manifest
from scripts.research_intraday_candidates import (
    _research_evidence_contract as intraday_evidence_contract,
)
from scripts.research_intraday_candidates import _research_protocol as intraday_protocol
from scripts.research_strategy_revision import (
    _research_evidence_contract as revision_evidence_contract,
)
from scripts.research_strategy_revision import _research_protocol as revision_protocol


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _dataset() -> tuple[DatasetSlice, ...]:
    return (
        DatasetSlice(
            "run-b",
            "BINANCE_USDM",
            ("ETHUSDT",),
            2_000,
            3_000,
            10,
            "b" * 64,
        ),
        DatasetSlice(
            "run-a",
            "BINANCE_USDM",
            ("BTCUSDT",),
            1_000,
            1_900,
            20,
            "a" * 64,
        ),
    )


def _expected_hashes(
    protocol: ResearchProtocol,
    dataset: tuple[DatasetSlice, ...],
) -> tuple[str, str]:
    ordered = sorted(dataset, key=lambda row: (row.start_ts_ms, row.run_id))
    return (
        _canonical_hash([asdict(row) for row in ordered]),
        _canonical_hash(
            {
                key: list(values)
                for key, values in sorted(protocol.parameter_grid.items())
            }
        ),
    )


def _manifest(
    protocol: ResearchProtocol,
    dataset: tuple[DatasetSlice, ...],
    registry: HypothesisRegistry,
    epoch: EvidenceEpoch,
) -> dict[str, object]:
    return protocol.manifest(
        tuple(reversed(dataset)),
        code_hash="c" * 40,
        config_hash="d" * 64,
        generated_ts_ms=4_000,
        hypothesis_registry=registry,
        evidence_epoch=epoch,
    )


def _assert_v2_binding(
    protocol: ResearchProtocol,
    dataset: tuple[DatasetSlice, ...],
    registry: HypothesisRegistry,
    epoch: EvidenceEpoch,
) -> dict[str, object]:
    dataset_hash, parameter_hash = _expected_hashes(protocol, dataset)
    registration = registry.registration(protocol.hypothesis_id)
    manifest = _manifest(protocol, dataset, registry, epoch)

    assert registration.key.dataset_hash == dataset_hash
    assert registration.key.parameter_hash == parameter_hash
    assert epoch.dataset_hash == dataset_hash
    assert epoch.parameter_hash == parameter_hash
    assert epoch.opened_ts_ms == 1_000
    assert epoch.closed_ts_ms == 3_000
    assert epoch.hypothesis_registry_hash == registry.fingerprint()
    assert epoch.hypothesis_key_fingerprint == registration.key_fingerprint
    assert manifest["schema_version"] == 2
    assert manifest["dataset_hash"] == dataset_hash
    assert manifest["parameter_hash"] == parameter_hash
    assert manifest["paper_only"] is True
    assert manifest["real_orders_enabled"] is False
    assert manifest["auth_required"] is False
    validate_research_manifest(manifest)
    return manifest


def test_intraday_script_preregisters_actual_dataset_and_parameter_hashes() -> None:
    dataset = _dataset()
    protocol = intraday_protocol()
    registry, epoch = intraday_evidence_contract(protocol, dataset)

    manifest = _assert_v2_binding(protocol, dataset, registry, epoch)
    key = registry.registration(protocol.hypothesis_id).key

    assert protocol.label_version == "STRUCTURE_EXIT_NET_BPS_V1"
    assert protocol.engine_version == "INTRADAY_CANDLE_RESEARCH_ENGINE_V1"
    assert protocol.cost_profile == "BASE_STRESS"
    assert key.exit_id == "STRUCTURE-TP1-TP2-STOP-MAX-HOLD-V1"
    assert key.execution_policy == "PAPER-EXECUTABLE-TOP-OF-BOOK-V1"
    assert manifest["hypothesis"]["hypothesis_id"] == protocol.hypothesis_id  # type: ignore[index]


def test_revision_script_preregisters_actual_dataset_and_parameter_hashes() -> None:
    dataset = _dataset()
    protocol = revision_protocol((15, 30, 60, 180))
    registry, epoch = revision_evidence_contract(protocol, dataset)

    _assert_v2_binding(protocol, dataset, registry, epoch)
    key = registry.registration(protocol.hypothesis_id).key

    assert protocol.label_version == "FIXED_HORIZON_NET_BPS_V1"
    assert protocol.engine_version == "STRATEGY_REVISION_REPLAY_ENGINE_V1"
    assert protocol.cost_profile == "BASE_STRESS"
    assert key.exit_id == "FIXED-HORIZON-EXECUTABLE-BOOK-V1"
    assert key.execution_policy == "PAPER-RECONSTRUCTED-TOP-OF-BOOK-V1"


def test_canonical_registry_and_epoch_change_with_dataset_or_parameter_grid() -> None:
    dataset = _dataset()
    protocol = revision_protocol((15, 30))
    registry, epoch = revision_evidence_contract(protocol, dataset)
    changed_dataset = (
        replace(dataset[0], checksum="e" * 64),
        dataset[1],
    )
    dataset_registry, dataset_epoch = revision_evidence_contract(
        protocol,
        changed_dataset,
    )
    changed_protocol = revision_protocol((15, 30, 60))
    parameter_registry, parameter_epoch = revision_evidence_contract(
        changed_protocol,
        dataset,
    )

    assert dataset_registry.fingerprint() != registry.fingerprint()
    assert dataset_epoch.epoch_id != epoch.epoch_id
    assert parameter_registry.fingerprint() != registry.fingerprint()
    assert parameter_epoch.epoch_id != epoch.epoch_id
