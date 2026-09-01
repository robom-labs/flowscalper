"""복구 실패 구간의 잘못된 자동 거버넌스 행만 좁게 제외하는지 검증한다."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import RuntimeMode, Venue
from backend.app.runtime import (
    PaperRuntime,
    _fail_closed_recovery_windows,
    _is_fail_closed_governance_contamination,
)
from backend.app.storage.sqlite import RecoveryState, SQLiteLedger

RUN_ID = "run-recovery-contamination"
STRATEGY_ID = "CBR_CONTINUATION_V1"
LEGACY_COMPONENT_IDS = (
    "AGGRESSOR_FLOW_CONTINUATION_V1",
    "MULTILEVEL_MICROPRICE_MOMENTUM_V1",
    "OFI_RETURN_CONFLUENCE_V1",
    "BOOK_SLOPE_ASYMMETRY_V1",
)


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


def _governance_row(
    *,
    ts_ms: int = 150,
    strategy_id: str = STRATEGY_ID,
) -> dict[str, object]:
    revision = 1
    transition_id = f"strategy-setting-{RUN_ID}-{strategy_id}-rev-{revision}"
    return {
        "run_id": RUN_ID,
        "ts_ms": ts_ms,
        "strategy_id": strategy_id,
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
                "strategy_id": strategy_id,
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
                "strategy_id": strategy_id,
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


def test_ignored_governor_revisions_are_reserved_before_v6_family_migration(
    tmp_path: Path,
) -> None:
    ledger = SQLiteLedger(tmp_path / "ignored-governor-revisions.sqlite3")
    PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=0),
        run_id=RUN_ID,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    ignored_rows: dict[str, dict[str, object]] = {}
    for strategy_id in LEGACY_COMPONENT_IDS:
        ignored_row = _governance_row(ts_ms=150, strategy_id=strategy_id)
        ignored_rows[strategy_id] = deepcopy(ignored_row)
        ledger.record_strategy_setting(ignored_row)
        governance_incident = _governance_incident(ignored_row)
        ledger.record_incident(
            str(governance_incident["incident_id"]),
            run_id=RUN_ID,
            severity="INFO",
            category=str(governance_incident["category"]),
            ts_ms=int(str(governance_incident["ts_ms"])),
            payload=governance_incident["payload"],  # type: ignore[arg-type]
        )
    for recovery_incident in (
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
    ):
        ledger.record_incident(
            str(recovery_incident["incident_id"]),
            run_id=RUN_ID,
            severity="INFO",
            category=str(recovery_incident["category"]),
            ts_ms=int(str(recovery_incident["ts_ms"])),
            payload=recovery_incident["payload"],  # type: ignore[arg-type]
        )

    first_recovery = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=300),
        run_id=RUN_ID,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    recovered = RecoveryState(
        run_id=RUN_ID,
        venue=Venue.BINANCE_USDM.value,
        lifecycle_state="SCANNING",
        payload={},
        transition_count=0,
        recovered_ts_ms=300,
    )

    assert first_recovery.restore_recovery_state(recovered) is True, (
        first_recovery.runtime_health_flags
    )
    assert len(first_recovery._recovery_ignored_governance_row_tokens) == 4
    assert len(set(first_recovery._recovery_ignored_governance_row_tokens)) == 4

    persisted_rows = ledger.list_strategy_settings(RUN_ID)
    for strategy_id in LEGACY_COMPONENT_IDS:
        setting = first_recovery.strategy_registry.setting(strategy_id)
        assert setting.mode.value == "OFF"
        assert setting.lifecycle.value == "RESEARCH"
        assert setting.revision == 2
        assert setting.change_reason == "V6_LEGACY_COMPONENT_HISTORY_ONLY"
        strategy_rows = [
            row for row in persisted_rows if row["strategy_id"] == strategy_id
        ]
        assert [row["settings_revision"] for row in strategy_rows] == [0, 1, 2]
        assert strategy_rows[1] == ignored_rows[strategy_id]
        migration = strategy_rows[2]
        assert migration["changed_by"] == "MIGRATION"
        assert migration["request_revision"] == 1
        assert migration["response_revision"] == 2
        assert migration["transition_id"] == (
            f"strategy-setting-{RUN_ID}-{strategy_id}-rev-2"
        )

    settings_count = ledger.count("strategy_settings")
    migration_incident_count = len(
        ledger.list_incidents(category="V6_FAMILY_RUNTIME_POLICY_MIGRATION")
    )
    second_recovery = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=400),
        run_id=RUN_ID,
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    recovered_again = RecoveryState(
        run_id=RUN_ID,
        venue=Venue.BINANCE_USDM.value,
        lifecycle_state="SCANNING",
        payload={},
        transition_count=0,
        recovered_ts_ms=400,
    )

    assert second_recovery.restore_recovery_state(recovered_again) is True, (
        second_recovery.runtime_health_flags
    )
    assert ledger.count("strategy_settings") == settings_count
    assert (
        len(ledger.list_incidents(category="V6_FAMILY_RUNTIME_POLICY_MIGRATION"))
        == migration_incident_count
        == 4
    )
    for strategy_id in LEGACY_COMPONENT_IDS:
        assert second_recovery.strategy_registry.setting(strategy_id).revision == 2
    ledger.close()
