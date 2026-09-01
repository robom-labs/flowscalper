# 같은 전략시험의 중복 실행을 막고 새 데이터의 순방향 검증만 허용한다.
"""파라미터·데이터·코드·비용 지문으로 append-only 연구시험 이력을 판정한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from backend.app.research.protocol import validate_research_manifest


def _is_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


class ResearchTrialStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class ResearchTrialDecision(StrEnum):
    ALLOW_NEW_HYPOTHESIS = "ALLOW_NEW_HYPOTHESIS"
    ALLOW_PARAMETER_VARIANT = "ALLOW_PARAMETER_VARIANT"
    ALLOW_EVIDENCE_EPOCH_REFRESH = "ALLOW_EVIDENCE_EPOCH_REFRESH"
    ALLOW_FORWARD_DATA_REFRESH = "ALLOW_FORWARD_DATA_REFRESH"
    ALLOW_IMPLEMENTATION_REVALIDATION = "ALLOW_IMPLEMENTATION_REVALIDATION"
    ALLOW_COST_MODEL_REVALIDATION = "ALLOW_COST_MODEL_REVALIDATION"
    ALLOW_RETRY_INCOMPLETE = "ALLOW_RETRY_INCOMPLETE"
    BLOCK_DUPLICATE_COMPLETE_TRIAL = "BLOCK_DUPLICATE_COMPLETE_TRIAL"
    BLOCK_NON_FORWARD_DATA_RESAMPLE = "BLOCK_NON_FORWARD_DATA_RESAMPLE"
    BLOCK_PAPER_SAFETY = "BLOCK_PAPER_SAFETY"


@dataclass(frozen=True, slots=True)
class ResearchTrialProposal:
    """실행 전 고정해야 하는 연구시험 정체성이다."""

    hypothesis_id: str
    strategy_id: str
    hypothesis_key_fingerprint: str
    parameter_fingerprint: str
    dataset_fingerprint: str
    dataset_start_ts_ms: int
    dataset_end_ts_ms: int
    implementation_fingerprint: str
    cost_model_fingerprint: str
    evidence_epoch_id: str
    evidence_epoch_fingerprint: str
    cost_profile: str
    feature_version: str
    label_version: str
    engine_version: str
    dataset_member_fingerprints: tuple[str, ...] = ()
    paper_only: bool = True
    real_orders_enabled: bool = False

    def __post_init__(self) -> None:
        identifiers = (
            self.hypothesis_id,
            self.strategy_id,
            self.implementation_fingerprint,
            self.cost_model_fingerprint,
            self.evidence_epoch_id,
            self.cost_profile,
            self.feature_version,
            self.label_version,
            self.engine_version,
        )
        if any(not value.strip() for value in identifiers):
            raise ValueError("연구시험 지문과 가설 ID는 비어 있을 수 없습니다.")
        if self.dataset_start_ts_ms < 0 or self.dataset_end_ts_ms < self.dataset_start_ts_ms:
            raise ValueError("연구시험 데이터 시간범위가 올바르지 않습니다.")
        for field_name in (
            "hypothesis_key_fingerprint",
            "parameter_fingerprint",
            "dataset_fingerprint",
            "evidence_epoch_fingerprint",
        ):
            if not _is_sha256(getattr(self, field_name)):
                raise ValueError(f"{field_name}는 소문자 SHA-256이어야 합니다.")
        if len(self.dataset_member_fingerprints) != len(set(self.dataset_member_fingerprints)):
            raise ValueError("dataset member fingerprint를 중복할 수 없습니다.")
        if any(not member.strip() for member in self.dataset_member_fingerprints):
            raise ValueError("dataset member fingerprint는 비어 있을 수 없습니다.")

    @property
    def candidate_key(self) -> tuple[str, ...]:
        """같은 hypothesis key와 evidence epoch 안의 후보인지 식별한다."""

        return (
            self.strategy_id,
            self.hypothesis_key_fingerprint,
            self.evidence_epoch_id,
            self.evidence_epoch_fingerprint,
            self.parameter_fingerprint,
        )

    @property
    def exact_trial_key(self) -> tuple[str, ...]:
        """동일 계산을 다른 파일명으로 다시 실행하지 못하게 식별한다."""

        return (
            *self.candidate_key,
            self.dataset_fingerprint,
            self.implementation_fingerprint,
            self.cost_model_fingerprint,
            self.cost_profile,
            self.feature_version,
            self.label_version,
            self.engine_version,
        )

    @classmethod
    def from_manifest(
        cls,
        manifest: Mapping[str, object],
        *,
        dataset_member_fingerprints: tuple[str, ...] | None = None,
    ) -> ResearchTrialProposal:
        """검증된 V2 manifest를 append-only trial proposal로 연결한다."""

        validate_research_manifest(manifest)
        if manifest.get("schema_version") != 2:
            raise ValueError("evidence epoch trial에는 V2 research manifest가 필요합니다.")
        hypothesis = manifest.get("hypothesis")
        versions = manifest.get("versions")
        epoch = manifest.get("evidence_epoch")
        time_range = manifest.get("time_range")
        if not all(isinstance(value, dict) for value in (hypothesis, versions, epoch, time_range)):
            raise ValueError("V2 research manifest 연결 필드가 누락됐습니다.")
        assert isinstance(hypothesis, dict)
        assert isinstance(versions, dict)
        assert isinstance(epoch, dict)
        assert isinstance(time_range, dict)
        required_strings = {
            "hypothesis_id": hypothesis.get("hypothesis_id"),
            "strategy_id": hypothesis.get("strategy_id"),
            "hypothesis_key_fingerprint": hypothesis.get("key_fingerprint"),
            "parameter_fingerprint": manifest.get("parameter_hash"),
            "dataset_fingerprint": manifest.get("dataset_hash"),
            "implementation_fingerprint": versions.get("code_hash"),
            "cost_model_fingerprint": versions.get("cost_model_version"),
            "evidence_epoch_id": epoch.get("epoch_id"),
            "evidence_epoch_fingerprint": epoch.get("epoch_fingerprint"),
            "cost_profile": versions.get("cost_profile"),
            "feature_version": versions.get("feature_version"),
            "label_version": versions.get("label_version"),
            "engine_version": versions.get("engine_version"),
        }
        if any(not isinstance(value, str) for value in required_strings.values()):
            raise ValueError("V2 research manifest 연결 식별자가 문자열이 아닙니다.")
        start_ts_ms = time_range.get("start_ts_ms")
        end_ts_ms = time_range.get("end_ts_ms")
        if not isinstance(start_ts_ms, int) or not isinstance(end_ts_ms, int):
            raise ValueError("V2 research manifest 데이터 시간범위가 정수가 아닙니다.")
        if dataset_member_fingerprints is None:
            dataset = manifest.get("dataset")
            if not isinstance(dataset, list):
                raise ValueError("V2 research manifest dataset이 배열이 아닙니다.")
            derived_members: list[str] = []
            for row in dataset:
                if not isinstance(row, dict) or not isinstance(
                    row.get("run_id"), str
                ) or not isinstance(row.get("checksum"), str):
                    raise ValueError("V2 research manifest dataset member가 올바르지 않습니다.")
                derived_members.append(f"{row['run_id']}:{row['checksum']}")
            dataset_member_fingerprints = tuple(derived_members)
        return cls(
            **{name: str(value) for name, value in required_strings.items()},
            dataset_start_ts_ms=start_ts_ms,
            dataset_end_ts_ms=end_ts_ms,
            dataset_member_fingerprints=dataset_member_fingerprints,
            paper_only=manifest.get("paper_only") is True,
            real_orders_enabled=manifest.get("real_orders_enabled") is True,
        )


@dataclass(frozen=True, slots=True)
class ResearchTrialRecord:
    """삭제하지 않는 한 번의 연구시험 결과 색인이다."""

    trial_id: str
    proposal: ResearchTrialProposal
    status: ResearchTrialStatus
    evidence_path: str

    def __post_init__(self) -> None:
        if not self.trial_id.strip() or not self.evidence_path.strip():
            raise ValueError("연구시험 ID와 증거경로는 비어 있을 수 없습니다.")


def evaluate_trial_proposal(
    history: tuple[ResearchTrialRecord, ...],
    proposal: ResearchTrialProposal,
) -> dict[str, object]:
    """완료시험 중복은 막고 파라미터 변형·새 데이터 검증은 명시적으로 분리한다."""

    trial_ids = [record.trial_id for record in history]
    if len(set(trial_ids)) != len(trial_ids):
        raise ValueError("연구시험 이력의 trial_id가 중복됐습니다.")
    if any(
        not record.proposal.paper_only or record.proposal.real_orders_enabled
        for record in history
    ):
        raise ValueError("기존 연구시험 이력의 PAPER 안전 계약이 깨졌습니다.")
    _validate_history_identity(history, proposal)

    completed_exact = [
        record
        for record in history
        if record.status is ResearchTrialStatus.COMPLETE
        and record.proposal.exact_trial_key == proposal.exact_trial_key
    ]
    if len(completed_exact) > 1:
        raise ValueError("같은 완료 연구시험이 이력에 둘 이상 존재합니다.")

    if not proposal.paper_only or proposal.real_orders_enabled:
        decision = ResearchTrialDecision.BLOCK_PAPER_SAFETY
    elif completed_exact:
        decision = ResearchTrialDecision.BLOCK_DUPLICATE_COMPLETE_TRIAL
    else:
        decision = _allowed_or_blocked_history_decision(history, proposal)

    blocked = decision.value.startswith("BLOCK_")
    same_hypothesis = [
        record for record in history if record.proposal.hypothesis_id == proposal.hypothesis_id
    ]
    same_strategy = [
        record for record in history if record.proposal.strategy_id == proposal.strategy_id
    ]
    same_candidate = [
        record
        for record in same_hypothesis
        if record.proposal.candidate_key == proposal.candidate_key
    ]
    return {
        "schema": "flowscalper.research_trial_history_decision.v1",
        "status": "BLOCKED" if blocked else "PASS",
        "decision": decision.value,
        "execution_allowed": not blocked,
        "history_record_count": len(history),
        "same_hypothesis_trial_count": len(same_hypothesis),
        "same_candidate_trial_count": len(same_candidate),
        "evidence_epoch_id": proposal.evidence_epoch_id,
        "evidence_epoch_fingerprint": proposal.evidence_epoch_fingerprint,
        "distinct_parameter_variant_count_after_proposal": len(
            {
                record.proposal.parameter_fingerprint for record in same_strategy
            }
            | {proposal.parameter_fingerprint}
        ),
        "completed_evidence_paths_preserved": [
            record.evidence_path
            for record in history
            if record.status is ResearchTrialStatus.COMPLETE
        ],
        "historical_records_preserved": True,
        "paper_only": True,
        "real_orders_enabled": False,
        "profitability_status": "NOT_PROVEN",
    }


def _allowed_or_blocked_history_decision(
    history: tuple[ResearchTrialRecord, ...],
    proposal: ResearchTrialProposal,
) -> ResearchTrialDecision:
    same_strategy = [
        record for record in history if record.proposal.strategy_id == proposal.strategy_id
    ]
    if not same_strategy:
        return ResearchTrialDecision.ALLOW_NEW_HYPOTHESIS

    same_parameter = [
        record
        for record in same_strategy
        if record.proposal.parameter_fingerprint == proposal.parameter_fingerprint
    ]
    if not same_parameter:
        return ResearchTrialDecision.ALLOW_PARAMETER_VARIANT

    same_candidate = [
        record
        for record in same_parameter
        if record.proposal.candidate_key == proposal.candidate_key
    ]

    exact_incomplete = [
        record
        for record in same_candidate
        if record.proposal.exact_trial_key == proposal.exact_trial_key
        and record.status is not ResearchTrialStatus.COMPLETE
    ]
    if exact_incomplete:
        return ResearchTrialDecision.ALLOW_RETRY_INCOMPLETE

    same_dataset = [
        record
        for record in same_parameter
        if record.proposal.dataset_fingerprint == proposal.dataset_fingerprint
    ]
    if same_dataset and not same_candidate:
        return ResearchTrialDecision.ALLOW_EVIDENCE_EPOCH_REFRESH
    if same_dataset and all(
        record.proposal.implementation_fingerprint
        != proposal.implementation_fingerprint
        for record in same_dataset
    ):
        return ResearchTrialDecision.ALLOW_IMPLEMENTATION_REVALIDATION
    if same_dataset and all(
        record.proposal.cost_model_fingerprint != proposal.cost_model_fingerprint
        for record in same_dataset
    ):
        return ResearchTrialDecision.ALLOW_COST_MODEL_REVALIDATION

    latest_dataset_end = max(
        record.proposal.dataset_end_ts_ms for record in same_parameter
    )
    earliest_dataset_start = min(
        record.proposal.dataset_start_ts_ms for record in same_parameter
    )
    known_dataset_fingerprints = {
        record.proposal.dataset_fingerprint for record in same_parameter
    }
    required_dataset_members = {
        member
        for record in same_parameter
        for member in record.proposal.dataset_member_fingerprints
    }
    if (
        proposal.dataset_end_ts_ms > latest_dataset_end
        and proposal.dataset_start_ts_ms <= earliest_dataset_start
        and proposal.dataset_fingerprint not in known_dataset_fingerprints
        and required_dataset_members.issubset(proposal.dataset_member_fingerprints)
    ):
        return ResearchTrialDecision.ALLOW_FORWARD_DATA_REFRESH
    return ResearchTrialDecision.BLOCK_NON_FORWARD_DATA_RESAMPLE


def _validate_history_identity(
    history: tuple[ResearchTrialRecord, ...],
    proposal: ResearchTrialProposal,
) -> None:
    hypothesis_fingerprints: dict[str, str] = {}
    epoch_fingerprints: dict[str, str] = {}
    key_contracts: dict[str, tuple[str, ...]] = {}
    epoch_contracts: dict[tuple[str, str], tuple[str, ...]] = {}
    proposals = tuple(record.proposal for record in history) + (proposal,)
    for current in proposals:
        evidence_axes = (
            current.parameter_fingerprint,
            current.dataset_fingerprint,
            current.cost_profile,
            current.feature_version,
            current.label_version,
            current.engine_version,
        )
        previous_hypothesis = hypothesis_fingerprints.setdefault(
            current.hypothesis_id,
            current.hypothesis_key_fingerprint,
        )
        if previous_hypothesis != current.hypothesis_key_fingerprint:
            raise ValueError("동일 hypothesis ID에 서로 다른 canonical key가 섞였습니다.")
        previous_epoch = epoch_fingerprints.setdefault(
            current.evidence_epoch_id,
            current.evidence_epoch_fingerprint,
        )
        if previous_epoch != current.evidence_epoch_fingerprint:
            raise ValueError("동일 evidence epoch ID에 서로 다른 contract가 섞였습니다.")
        previous_key_contract = key_contracts.setdefault(
            current.hypothesis_key_fingerprint,
            evidence_axes,
        )
        if previous_key_contract != evidence_axes:
            raise ValueError("동일 hypothesis key에 서로 다른 evidence 분리축이 섞였습니다.")
        previous_epoch_contract = epoch_contracts.setdefault(
            (current.evidence_epoch_id, current.evidence_epoch_fingerprint),
            evidence_axes,
        )
        if previous_epoch_contract != evidence_axes:
            raise ValueError("동일 evidence epoch에 서로 다른 evidence 분리축이 섞였습니다.")
