# 같은 전략시험의 중복 실행을 막고 새 데이터의 순방향 검증만 허용한다.
"""파라미터·데이터·코드·비용 지문으로 append-only 연구시험 이력을 판정한다."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResearchTrialStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class ResearchTrialDecision(StrEnum):
    ALLOW_NEW_HYPOTHESIS = "ALLOW_NEW_HYPOTHESIS"
    ALLOW_PARAMETER_VARIANT = "ALLOW_PARAMETER_VARIANT"
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
    parameter_fingerprint: str
    dataset_fingerprint: str
    dataset_start_ts_ms: int
    dataset_end_ts_ms: int
    implementation_fingerprint: str
    cost_model_fingerprint: str
    dataset_member_fingerprints: tuple[str, ...] = ()
    paper_only: bool = True
    real_orders_enabled: bool = False

    def __post_init__(self) -> None:
        identifiers = (
            self.hypothesis_id,
            self.parameter_fingerprint,
            self.dataset_fingerprint,
            self.implementation_fingerprint,
            self.cost_model_fingerprint,
        )
        if any(not value.strip() for value in identifiers):
            raise ValueError("연구시험 지문과 가설 ID는 비어 있을 수 없습니다.")
        if self.dataset_start_ts_ms < 0 or self.dataset_end_ts_ms < self.dataset_start_ts_ms:
            raise ValueError("연구시험 데이터 시간범위가 올바르지 않습니다.")

    @property
    def candidate_key(self) -> tuple[str, str]:
        """데이터가 늘어도 같은 전략·파라미터인지 식별한다."""

        return (self.hypothesis_id, self.parameter_fingerprint)

    @property
    def exact_trial_key(self) -> tuple[str, ...]:
        """동일 계산을 다른 파일명으로 다시 실행하지 못하게 식별한다."""

        return (
            *self.candidate_key,
            self.dataset_fingerprint,
            self.implementation_fingerprint,
            self.cost_model_fingerprint,
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
        "distinct_parameter_variant_count_after_proposal": len(
            {
                record.proposal.parameter_fingerprint for record in same_hypothesis
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
    same_hypothesis = [
        record for record in history if record.proposal.hypothesis_id == proposal.hypothesis_id
    ]
    if not same_hypothesis:
        return ResearchTrialDecision.ALLOW_NEW_HYPOTHESIS

    same_candidate = [
        record
        for record in same_hypothesis
        if record.proposal.candidate_key == proposal.candidate_key
    ]
    if not same_candidate:
        return ResearchTrialDecision.ALLOW_PARAMETER_VARIANT

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
        for record in same_candidate
        if record.proposal.dataset_fingerprint == proposal.dataset_fingerprint
    ]
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
        record.proposal.dataset_end_ts_ms for record in same_candidate
    )
    earliest_dataset_start = min(
        record.proposal.dataset_start_ts_ms for record in same_candidate
    )
    known_dataset_fingerprints = {
        record.proposal.dataset_fingerprint for record in same_candidate
    }
    required_dataset_members = {
        member
        for record in same_candidate
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
