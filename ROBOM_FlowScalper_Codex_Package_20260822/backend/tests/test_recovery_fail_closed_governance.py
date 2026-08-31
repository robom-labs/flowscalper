"""복구 fail-closed와 일반 운영 결함 평가의 자동 전략 거버넌스 경계를 검증한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import RuntimeMode, Venue
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import SQLiteLedger
from backend.app.strategies.registry import (
    StrategyChangeSource,
    StrategyLifecycle,
)


def test_recovery_fail_closed_governance_cycle_is_read_only(tmp_path: Path) -> None:
    ledger = SQLiteLedger(tmp_path / "recovery-fail-closed-governance.sqlite3")
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=1_000),
        run_id="run-recovery-fail-closed-governance",
        ledger=ledger,
        venue=Venue.BINANCE_USDM,
    )
    runtime._lock_recovery("RECOVERY_STATE_REJECTED:ValueError")
    runtime.startup_recovery_audit = {
        "new_state": "RECOVERY_FAIL_CLOSED",
        "recovery_ok": False,
    }
    registry_before = {
        strategy_id: runtime.strategy_registry.setting_row(strategy_id)
        for strategy_id in runtime.strategy_registry.strategy_ids
    }
    settings_before = ledger.count("strategy_settings")
    incidents_before = ledger.count("incidents")
    last_cycle_before = runtime._governance_last_cycle_ts_ms
    sample_sizes_before = dict(runtime._governance_last_sample_size)

    result = runtime.run_strategy_governance_cycle()

    assert result["blocked_reason"] == "RECOVERY_FAIL_CLOSED"
    assert result["assessments"] == []
    assert result["changes"] == []
    assert {
        strategy_id: runtime.strategy_registry.setting_row(strategy_id)
        for strategy_id in runtime.strategy_registry.strategy_ids
    } == registry_before
    assert ledger.count("strategy_settings") == settings_before
    assert ledger.count("incidents") == incidents_before
    assert runtime._governance_last_cycle_ts_ms is last_cycle_before
    assert runtime._governance_last_sample_size == sample_sizes_before
    ledger.close()


@pytest.mark.parametrize("locked_state", ("HEALTH_FLAG_ONLY", "MANUAL_PAUSE_ONLY"))
def test_non_recovery_locks_do_not_suppress_account_operational_fault(
    locked_state: str,
) -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        clock=DeterministicClock(current_utc_ms=2_000),
        venue=Venue.BINANCE_USDM,
    )
    if locked_state == "HEALTH_FLAG_ONLY":
        runtime.runtime_health_flags = ["RECOVERY_FAIL_CLOSED"]
    else:
        runtime._manual_pause_requested = True
        runtime.paused = True
    strategy_id = "CBR_CONTINUATION_V1"
    runtime.paper_portfolio.shadows[f"{strategy_id}:BASE"].risk_state.faulted = True

    result = runtime.run_strategy_governance_cycle()

    assert "blocked_reason" not in result
    changes = result["changes"]
    assert isinstance(changes, list)
    assert len(changes) == 1
    assert changes[0]["strategy_id"] == strategy_id
    setting = runtime.strategy_registry.setting(strategy_id)
    assert setting.lifecycle is StrategyLifecycle.QUARANTINED
    assert setting.changed_by is StrategyChangeSource.AUTO_GOVERNOR
    assert setting.change_reason == "OPERATIONAL_FAULT"
    assert runtime._governance_last_cycle_ts_ms == runtime.clock.utc_ms()
