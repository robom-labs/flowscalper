# 연구시험 이력이 완료 중복을 막고 새 데이터·파라미터 검증만 허용하는지 검증한다.

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.app.research.trial_history import (
    ResearchTrialProposal,
    ResearchTrialRecord,
    ResearchTrialStatus,
    evaluate_trial_proposal,
)


def _proposal(**overrides: object) -> ResearchTrialProposal:
    values: dict[str, object] = {
        "hypothesis_id": "HYP-OFI-V1",
        "strategy_id": "OFI-V1",
        "hypothesis_key_fingerprint": "c" * 64,
        "parameter_fingerprint": "a" * 64,
        "dataset_fingerprint": "b" * 64,
        "dataset_start_ts_ms": 100,
        "dataset_end_ts_ms": 200,
        "implementation_fingerprint": "code-a",
        "cost_model_fingerprint": "cost-a",
        "evidence_epoch_id": "EPOCH-001",
        "evidence_epoch_fingerprint": "d" * 64,
        "cost_profile": "BASE",
        "feature_version": "FEATURE-V9",
        "label_version": "LABEL-V9",
        "engine_version": "ENGINE-V9",
        "dataset_member_fingerprints": ("RUN-A:checksum-a",),
    }
    values.update(overrides)
    return ResearchTrialProposal(**values)  # type: ignore[arg-type]


def _record(
    proposal: ResearchTrialProposal | None = None,
    *,
    status: ResearchTrialStatus = ResearchTrialStatus.COMPLETE,
    trial_id: str = "TRIAL-001",
) -> ResearchTrialRecord:
    return ResearchTrialRecord(
        trial_id=trial_id,
        proposal=proposal or _proposal(),
        status=status,
        evidence_path=f"evidence/{trial_id}.json",
    )


def test_completed_exact_trial_is_blocked_even_with_a_new_output_name() -> None:
    result = evaluate_trial_proposal((_record(),), _proposal())

    assert result["status"] == "BLOCKED"
    assert result["decision"] == "BLOCK_DUPLICATE_COMPLETE_TRIAL"
    assert result["execution_allowed"] is False
    assert result["completed_evidence_paths_preserved"] == [
        "evidence/TRIAL-001.json"
    ]


def test_incomplete_exact_trial_can_retry_without_becoming_a_new_variant() -> None:
    result = evaluate_trial_proposal(
        (_record(status=ResearchTrialStatus.ABORTED),),
        _proposal(),
    )

    assert result["decision"] == "ALLOW_RETRY_INCOMPLETE"
    assert result["execution_allowed"] is True
    assert result["distinct_parameter_variant_count_after_proposal"] == 1


def test_same_candidate_can_refresh_only_with_strictly_later_data() -> None:
    history = (_record(),)
    later = _proposal(
        hypothesis_id="HYP-OFI-V1-DATA-B",
        hypothesis_key_fingerprint="e" * 64,
        dataset_fingerprint="e" * 64,
        dataset_end_ts_ms=300,
        dataset_member_fingerprints=("RUN-A:checksum-a", "RUN-B:checksum-b"),
        evidence_epoch_id="EPOCH-002",
        evidence_epoch_fingerprint="f" * 64,
    )
    resampled_past = _proposal(
        hypothesis_id="HYP-OFI-V1-DATA-C",
        hypothesis_key_fingerprint="1" * 64,
        dataset_fingerprint="f" * 64,
        dataset_end_ts_ms=200,
        evidence_epoch_id="EPOCH-003",
        evidence_epoch_fingerprint="2" * 64,
    )

    allowed = evaluate_trial_proposal(history, later)
    blocked = evaluate_trial_proposal(history, resampled_past)

    assert allowed["decision"] == "ALLOW_FORWARD_DATA_REFRESH"
    assert blocked["decision"] == "BLOCK_NON_FORWARD_DATA_RESAMPLE"


def test_later_dataset_cannot_drop_an_existing_immutable_run() -> None:
    result = evaluate_trial_proposal(
        (_record(),),
        _proposal(
            hypothesis_id="HYP-OFI-V1-DATA-B",
            hypothesis_key_fingerprint="e" * 64,
            dataset_fingerprint="e" * 64,
            dataset_end_ts_ms=300,
            dataset_member_fingerprints=("RUN-B:checksum-b",),
            evidence_epoch_id="EPOCH-002",
            evidence_epoch_fingerprint="f" * 64,
        ),
    )

    assert result["decision"] == "BLOCK_NON_FORWARD_DATA_RESAMPLE"


def test_changed_parameters_are_counted_as_a_variant_not_a_duplicate() -> None:
    result = evaluate_trial_proposal(
        (_record(),),
        _proposal(
            hypothesis_id="HYP-OFI-V1-PARAM-B",
            hypothesis_key_fingerprint="e" * 64,
            parameter_fingerprint="e" * 64,
            evidence_epoch_id="EPOCH-002",
            evidence_epoch_fingerprint="f" * 64,
        ),
    )

    assert result["decision"] == "ALLOW_PARAMETER_VARIANT"
    assert result["distinct_parameter_variant_count_after_proposal"] == 2
    assert result["profitability_status"] == "NOT_PROVEN"


def test_same_dataset_allows_code_or_cost_contract_revalidation() -> None:
    history = (_record(),)

    code_result = evaluate_trial_proposal(
        history,
        _proposal(implementation_fingerprint="code-b"),
    )
    cost_result = evaluate_trial_proposal(
        history,
        _proposal(cost_model_fingerprint="cost-b"),
    )

    assert code_result["decision"] == "ALLOW_IMPLEMENTATION_REVALIDATION"
    assert cost_result["decision"] == "ALLOW_COST_MODEL_REVALIDATION"


def test_trial_history_fails_closed_on_paper_safety_and_corruption() -> None:
    unsafe = replace(_proposal(), paper_only=False, real_orders_enabled=True)

    result = evaluate_trial_proposal((), unsafe)

    assert result["decision"] == "BLOCK_PAPER_SAFETY"
    assert result["real_orders_enabled"] is False
    with pytest.raises(ValueError, match="trial_id가 중복"):
        evaluate_trial_proposal((_record(), _record()), _proposal())
    unsafe_history = (
        _record(proposal=_proposal(paper_only=False, real_orders_enabled=True)),
    )
    with pytest.raises(ValueError, match="PAPER 안전 계약"):
        evaluate_trial_proposal(unsafe_history, _proposal())


def test_new_evidence_epoch_is_separate_but_epoch_id_collision_fails_closed() -> None:
    history = (_record(),)
    refreshed = _proposal(
        evidence_epoch_id="EPOCH-002",
        evidence_epoch_fingerprint="e" * 64,
    )

    result = evaluate_trial_proposal(history, refreshed)

    assert result["decision"] == "ALLOW_EVIDENCE_EPOCH_REFRESH"
    with pytest.raises(ValueError, match="epoch ID"):
        evaluate_trial_proposal(
            history,
            _proposal(evidence_epoch_fingerprint="e" * 64),
        )


def test_hypothesis_id_key_collision_fails_closed() -> None:
    with pytest.raises(ValueError, match="hypothesis ID"):
        evaluate_trial_proposal(
            (_record(),),
            _proposal(hypothesis_key_fingerprint="e" * 64),
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("parameter_fingerprint", "e" * 64),
        ("dataset_fingerprint", "f" * 64),
        ("cost_profile", "STRESS"),
        ("feature_version", "FEATURE-V10"),
        ("label_version", "LABEL-V10"),
        ("engine_version", "ENGINE-V10"),
    ],
)
def test_evidence_axis_change_cannot_reuse_epoch(
    field_name: str,
    replacement: str,
) -> None:
    with pytest.raises(ValueError, match="evidence 분리축"):
        evaluate_trial_proposal(
            (_record(),),
            _proposal(**{field_name: replacement}),
        )
