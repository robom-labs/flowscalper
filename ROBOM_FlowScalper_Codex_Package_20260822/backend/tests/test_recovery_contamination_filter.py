"""복구 실패 구간의 잘못된 자동 거버넌스 행만 좁게 제외하는지 검증한다."""

from __future__ import annotations

from copy import deepcopy

import pytest

from backend.app.runtime import (
    _fail_closed_recovery_windows,
    _is_fail_closed_governance_contamination,
)

RUN_ID = "run-recovery-contamination"
STRATEGY_ID = "CBR_CONTINUATION_V1"


def _recovery_incident(
    *,
    ts_ms: int,
    new_state: str,
    recovery_ok: bool,
) -> dict[str, object]:
    transition_id = f"recovery-{RUN_ID}-{ts_ms}"
    return {
        "incident_id": transition_id,
        "run_id": RUN_ID,
        "category": "PAPER_RESTART_RECOVERY",
        "ts_ms": ts_ms,
        "payload": {
            "transition_id": transition_id,
            "run_id": RUN_ID,
            "occurred_ts_ms": ts_ms,
            "actor": "RECOVERY",
            "new_state": new_state,
            "recovery_ok": recovery_ok,
            "reversible": recovery_ok,
        },
    }


def _governance_row(*, ts_ms: int = 150) -> dict[str, object]:
    revision = 1
    transition_id = f"strategy-setting-{RUN_ID}-{STRATEGY_ID}-rev-{revision}"
    return {
        "run_id": RUN_ID,
        "ts_ms": ts_ms,
        "strategy_id": STRATEGY_ID,
        "mode": "OFF",
        "lifecycle": "QUARANTINED",
        "long_enabled": True,
        "short_enabled": True,
        "settings_revision": revision,
        "manual_lock": False,
        "changed_by": "AUTO_GOVERNOR",
        "actor": "AUTO_GOVERNOR",
        "change_reason": "OPERATIONAL_FAULT",
        "cause": "OPERATIONAL_FAULT",
        "cause_code": "OPERATIONAL_FAULT",
        "settings_updated_ts_ms": ts_ms,
        "occurred_ts_ms": ts_ms,
        "request_revision": 0,
        "response_revision": revision,
        "account_id": None,
        "symbol": None,
        "transition_id": transition_id,
        "change_evidence": {
            "assessment": {
                "strategy_id": STRATEGY_ID,
                "reason_codes": ["OPERATIONAL_FAULT"],
                "recommended_lifecycle": "QUARANTINED",
                "automatic_action_allowed": True,
                "transition_required": True,
            },
            "evidence": {
                "operational_fault": True,
                "operational_health_passed": False,
                "evaluated_ts_ms": ts_ms,
            },
            "lineage": {
                "schema_version": 1,
                "run_id": RUN_ID,
                "strategy_id": STRATEGY_ID,
                "settings_revision": revision,
                "assessment_ts_ms": ts_ms,
                "release_commit": "UNAVAILABLE",
            },
        },
    }


def _governance_incident(row: dict[str, object]) -> dict[str, object]:
    return {
        "incident_id": row["transition_id"],
        "run_id": RUN_ID,
        "category": "AUTO_GOVERNOR_TRANSITION",
        "ts_ms": row["ts_ms"],
        "payload": dict(row),
    }


def test_exact_governance_row_is_ignored_only_inside_closed_recovery_window() -> None:
    incidents = (
        _recovery_incident(
            ts_ms=100,
            new_state="RECOVERY_FAIL_CLOSED",
            recovery_ok=False,
        ),
        _recovery_incident(
            ts_ms=200,
            new_state="RECOVERY_REVALIDATION_LOCKED",
            recovery_ok=True,
        ),
    )
    windows = _fail_closed_recovery_windows(incidents, run_id=RUN_ID)
    row = _governance_row()

    assert windows == ((100, 200),)
    assert _is_fail_closed_governance_contamination(
        row,
        run_id=RUN_ID,
        windows=windows,
        governance_incidents=(_governance_incident(row),),
    ) is True


def test_open_recovery_failure_window_never_relaxes_ledger_validation() -> None:
    windows = _fail_closed_recovery_windows(
        (
            _recovery_incident(
                ts_ms=100,
                new_state="RECOVERY_FAIL_CLOSED",
                recovery_ok=False,
            ),
        ),
        run_id=RUN_ID,
    )
    row = _governance_row()

    assert windows == ()
    assert _is_fail_closed_governance_contamination(
        row,
        run_id=RUN_ID,
        windows=windows,
        governance_incidents=(_governance_incident(row),),
    ) is False


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("actor",), "USER_UI"),
        (("change_reason",), "USER_OVERRIDE"),
        (("ts_ms",), 200),
        (("change_evidence", "lineage", "release_commit"), "real-commit"),
    ),
)
def test_near_match_governance_rows_are_never_ignored(
    path: tuple[str, ...],
    value: object,
) -> None:
    windows = ((100, 200),)
    row = _governance_row()
    target: dict[str, object] = row
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    assert _is_fail_closed_governance_contamination(
        row,
        run_id=RUN_ID,
        windows=windows,
        governance_incidents=(_governance_incident(_governance_row()),),
    ) is False


def test_missing_or_divergent_governance_incident_anchor_is_never_ignored() -> None:
    row = _governance_row()
    divergent_incident = deepcopy(_governance_incident(row))
    divergent_incident["incident_id"] = "different-transition"

    assert _is_fail_closed_governance_contamination(
        row,
        run_id=RUN_ID,
        windows=((100, 200),),
        governance_incidents=(divergent_incident,),
    ) is False
