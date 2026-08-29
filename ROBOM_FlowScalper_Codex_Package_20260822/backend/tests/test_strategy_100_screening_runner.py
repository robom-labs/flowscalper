# 100후보 실행기가 Final OOS 봉인·purge·PBO 시간순 fold를 지키는지 검증한다.

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.app.costing import CostProfile
from backend.app.features import FeatureInputError
from backend.app.research import (
    ScreeningAccountResult,
    ScreeningStatus,
    ScreeningTrade,
    TrialScreeningResult,
    preregistered_trials,
)
from scripts.export_strategy_100_manifest import BOUND_SOURCE_FILES
from scripts.research_intraday_candidates import _event_rows, _trade_tick
from scripts.research_strategy_100_candidates import (
    AccountCounters,
    ResearchAccountCarry,
    ResearchExecutionWindow,
    RunDiagnostics,
    Strategy100RunExecutor,
    TrialIntegrity,
    ValidationFold,
    _effective_validation_folds,
    _manifest_checksum,
    _run_diagnostics_payload,
    _run_execution_windows,
    _trade_inside_purged_split,
    _validation_fold_returns,
    _validation_folds,
    _verify_current_bound_sources,
    _verify_manifest,
)
from scripts.research_strategy_revision import _event_rows as revision_event_rows


def test_research_archive_reader_uses_observed_receive_order(tmp_path: Path) -> None:
    rows = [
        {
            "ts_ms": 1_000,
            "venue_ts_ms": 1_000,
            "symbol": "BTCUSDT",
            "payload_json": json.dumps(
                {
                    "event_id": "received-second",
                    "event_type": "TRADE",
                    "symbol": "BTCUSDT",
                    "venue_ts_ms": 1_000,
                    "receive_ts_ms": 4_000,
                    "receive_monotonic_ns": 20,
                }
            ),
        },
        {
            "ts_ms": 2_000,
            "venue_ts_ms": 2_000,
            "symbol": "BTCUSDT",
            "payload_json": json.dumps(
                {
                    "event_id": "received-first",
                    "event_type": "TRADE",
                    "symbol": "BTCUSDT",
                    "venue_ts_ms": 2_000,
                    "receive_ts_ms": 3_000,
                    "receive_monotonic_ns": 10,
                }
            ),
        },
    ]
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "events.parquet")

    assert [row["event_id"] for row in _event_rows(tmp_path)] == [
        "received-first",
        "received-second",
    ]
    assert revision_event_rows is _event_rows


def test_research_trade_rejects_nonfinite_input_before_candle_path() -> None:
    payload = {
        "event_id": "invalid-trade",
        "symbol": "BTCUSDT",
        "venue_ts_ms": 1_000,
        "data": {
            "price": "NaN",
            "quantity": "1",
            "buyer_is_aggressor": True,
        },
    }

    with pytest.raises(FeatureInputError, match="유한한 양수"):
        _trade_tick(payload)


def test_run_diagnostics_counter_serializes_with_string_event_keys() -> None:
    diagnostics = RunDiagnostics(run_id="run-audit", split="VALIDATION")
    diagnostics.audit_counts.update(
        {
            "ENTRY_REJECTED": 2,
            "TRAILING_STATE_TRANSITION": 3,
        }
    )

    payload = _run_diagnostics_payload(diagnostics)
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert json.loads(rendered)["audit_counts"] == {
        "ENTRY_REJECTED": 2,
        "TRAILING_STATE_TRANSITION": 3,
    }
    assert all(isinstance(key, str) for key in payload["audit_counts"])


def test_trial_manifest_rejects_stale_bound_source_checksum() -> None:
    source_checksums = {
        path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in BOUND_SOURCE_FILES
    }
    _verify_current_bound_sources({"source_checksums": source_checksums})
    source_checksums[BOUND_SOURCE_FILES[0].as_posix()] = "0" * 64
    with pytest.raises(ValueError, match="현재 코드와 다릅니다"):
        _verify_current_bound_sources({"source_checksums": source_checksums})


def test_executor_keeps_scanning_receive_order_after_future_exchange_event(
    monkeypatch,
    tmp_path: Path,
) -> None:
    executor = object.__new__(Strategy100RunExecutor)
    executor.archive_dir = tmp_path
    executor.observation_end_ts_ms = 2_000
    executor.portfolio = SimpleNamespace(shadows={})
    executor.diagnostics = SimpleNamespace(
        censored_position_count=0,
        censored_pending_entry_count=0,
    )
    processed: list[str] = []
    executor._process = lambda payload: processed.append(str(payload["event_id"]))
    executor._drain_audit = lambda: None
    monkeypatch.setattr(
        "scripts.research_strategy_100_candidates._event_rows",
        lambda *_args, **_kwargs: iter(
            (
                {"event_id": "future-exchange-time", "venue_ts_ms": 3_000},
                {"event_id": "late-in-window", "venue_ts_ms": 1_000},
            )
        ),
    )

    assert executor.execute() == ()
    assert processed == ["late-in-window"]


def test_execution_windows_pause_embargo_and_leave_full_horizon_to_close() -> None:
    raw_folds = (
        ValidationFold("validation-run:A", 30_000_000, 60_000_000),
        ValidationFold("validation-run:B", 70_000_000, 100_000_000),
    )
    folds = _effective_validation_folds(
        raw_folds,
        maximum_holding_ms=180_000,
        purge_embargo_ms=180_000,
    )
    windows = _run_execution_windows(
        {
            "run_id": "validation-run",
            "role": "VALIDATION",
            "start_ts_ms": 20_000_000,
            "end_ts_ms": 110_000_000,
        },
        validation_folds=folds,
        validation_start_ms=20_000_000,
        horizon="MICRO_SCALP",
        maximum_holding_ms=180_000,
        purge_embargo_ms=180_000,
    )

    assert windows == (
        ResearchExecutionWindow(
            window_id="validation-run:A",
            horizon="MICRO_SCALP",
            entry_start_ts_ms=30_180_000,
            entry_cutoff_ts_ms=59_639_000,
            observation_end_ts_ms=59_820_000,
            maximum_holding_ms=180_000,
            purge_embargo_ms=180_000,
        ),
        ResearchExecutionWindow(
            window_id="validation-run:B",
            horizon="MICRO_SCALP",
            entry_start_ts_ms=70_180_000,
            entry_cutoff_ts_ms=99_639_000,
            observation_end_ts_ms=99_820_000,
            maximum_holding_ms=180_000,
            purge_embargo_ms=180_000,
        ),
    )
    assert windows[0].permits_entry(59_638_999)
    assert not windows[0].permits_entry(59_639_000)
    assert not windows[0].permits_entry(65_000_000)


def test_horizon_specific_folds_fail_closed_when_validation_is_too_short() -> None:
    raw = (
        ValidationFold("run-a:A", 0, 800_000),
        ValidationFold("run-a:B", 800_000, 1_600_000),
        ValidationFold("run-b:A", 2_000_000, 2_800_000),
        ValidationFold("run-b:B", 2_800_000, 3_600_000),
    )

    assert (
        len(
            _effective_validation_folds(
                raw,
                maximum_holding_ms=180_000,
                purge_embargo_ms=180_000,
            )
        )
        == 4
    )
    assert (
        _effective_validation_folds(
            raw,
            maximum_holding_ms=3_600_000,
            purge_embargo_ms=3_600_000,
        )
        == ()
    )


def test_research_account_carry_rejects_equity_above_peak() -> None:
    try:
        ResearchAccountCarry(
            current_equity_usdt=Decimal("1001"),
            peak_equity_usdt=Decimal("1000"),
        )
    except ValueError as error:
        assert "carry" in str(error)
    else:
        raise AssertionError("peak보다 큰 research 자산 carry가 허용됐습니다.")


def test_research_account_carry_seeds_both_execution_and_shadow_ledgers() -> None:
    trial = next(
        row
        for row in preregistered_trials()
        if row.screening_eligible and row.alpha.horizon == "MICRO_SCALP"
    )
    carries = {
        (trial.trial_id, "BASE"): ResearchAccountCarry(
            current_equity_usdt=Decimal("990"),
            peak_equity_usdt=Decimal("1000"),
            global_consecutive_losses=2,
        ),
        (trial.trial_id, "STRESS"): ResearchAccountCarry(
            current_equity_usdt=Decimal("1010"),
            peak_equity_usdt=Decimal("1015"),
        ),
    }
    executor = Strategy100RunExecutor(
        run_id="run-carry",
        split="TRAIN",
        archive_dir=Path("unused-archive"),
        trials=(trial,),
        instruments={},
        account_counters={
            (trial.trial_id, profile.value): AccountCounters() for profile in CostProfile
        },
        trial_integrity={trial.trial_id: TrialIntegrity()},
        execution_windows_by_horizon={
            "MICRO_SCALP": (
                ResearchExecutionWindow(
                    window_id="run-carry:TRAIN",
                    horizon="MICRO_SCALP",
                    entry_start_ts_ms=100,
                    entry_cutoff_ts_ms=200,
                    observation_end_ts_ms=181_200,
                    maximum_holding_ms=180_000,
                    purge_embargo_ms=180_000,
                ),
            )
        },
        account_carry=carries,
    )

    for profile in CostProfile:
        key = (trial.trial_id, profile.value)
        execution_account = executor.portfolio.shadows[f"{trial.trial_id}:{profile.value}"]
        shadow_account = executor.portfolio.shadow_ledger.account(trial.trial_id, profile)
        assert execution_account.risk_state.current_equity == carries[key].current_equity_usdt
        assert execution_account.risk_state.peak_equity == carries[key].peak_equity_usdt
        assert shadow_account.current_equity_usdt == carries[key].current_equity_usdt
        assert shadow_account.peak_equity_usdt == carries[key].peak_equity_usdt
    assert executor.ending_account_carry() == carries


def _trade(
    trade_id: str,
    *,
    entry_ts_ms: int,
    exit_ts_ms: int,
    split: str = "VALIDATION",
) -> ScreeningTrade:
    return ScreeningTrade(
        trade_id=trade_id,
        trial_id="ALPHA_F03_EXIT_E01_V1",
        profile="STRESS",
        split=split,
        run_id="validation-run",
        symbol="BTCUSDT",
        regime="TREND_UP",
        side="LONG",
        entry_ts_ms=entry_ts_ms,
        exit_ts_ms=exit_ts_ms,
        gross_pnl_usdt=Decimal("1"),
        fee_usdt=Decimal("0.1"),
        slippage_usdt=Decimal("0.1"),
        net_pnl_usdt=Decimal("0.8"),
        net_return_bps=8,
        mfe_r=Decimal("1"),
        mae_r=Decimal("-0.1"),
        giveback_usdt=Decimal("0.2"),
    )


def test_manifest_checksum_rejects_tampering() -> None:
    manifest: dict[str, object] = {"status": "FROZEN", "trial_count": 100}
    manifest["manifest_sha256"] = _manifest_checksum(manifest)
    assert _verify_manifest(manifest, name="fixture") == manifest["manifest_sha256"]

    tampered = dict(manifest)
    tampered["trial_count"] = 99
    try:
        _verify_manifest(tampered, name="fixture")
    except ValueError as error:
        assert "checksum" in str(error)
    else:
        raise AssertionError("변조된 screening manifest가 허용됐습니다.")


def test_validation_runs_are_split_into_four_chronological_folds() -> None:
    dataset = {
        "runs": [
            {
                "run_id": "run-a",
                "role": "VALIDATION",
                "start_ts_ms": 1_000,
                "end_ts_ms": 21_000,
            },
            {
                "run_id": "run-b",
                "role": "VALIDATION",
                "start_ts_ms": 30_000,
                "end_ts_ms": 70_000,
            },
        ]
    }

    folds = _validation_folds(dataset)

    assert folds == (
        ValidationFold("run-a:A", 1_000, 11_000),
        ValidationFold("run-a:B", 11_000, 21_000),
        ValidationFold("run-b:A", 30_000, 50_000),
        ValidationFold("run-b:B", 50_000, 70_000),
    )

    purged = _effective_validation_folds(
        folds,
        maximum_holding_ms=100,
        purge_embargo_ms=100,
    )
    assert purged == (
        ValidationFold("run-a:A", 1_100, 10_900),
        ValidationFold("run-a:B", 11_100, 20_900),
        ValidationFold("run-b:A", 30_100, 49_900),
        ValidationFold("run-b:B", 50_100, 69_900),
    )


def test_purge_embargo_and_fold_crossing_trade_fail_closed() -> None:
    inside = _trade("inside", entry_ts_ms=310_000, exit_ts_ms=315_000)
    crossing = _trade("crossing", entry_ts_ms=318_000, exit_ts_ms=501_000)
    execution_windows = {
        "MICRO_SCALP": (
            ResearchExecutionWindow(
                window_id="A",
                horizon="MICRO_SCALP",
                entry_start_ts_ms=300_000,
                entry_cutoff_ts_ms=319_000,
                observation_end_ts_ms=500_000,
                maximum_holding_ms=180_000,
                purge_embargo_ms=180_000,
            ),
        )
    }
    trial_horizons = {"ALPHA_F03_EXIT_E01_V1": "MICRO_SCALP"}
    assert _trade_inside_purged_split(
        inside,
        execution_windows_by_horizon=execution_windows,
        trial_horizon_by_id=trial_horizons,
    )
    assert not _trade_inside_purged_split(
        replace(inside, entry_ts_ms=299_000, exit_ts_ms=305_000),
        execution_windows_by_horizon=execution_windows,
        trial_horizon_by_id=trial_horizons,
    )
    assert not _trade_inside_purged_split(
        replace(inside, entry_ts_ms=319_000, exit_ts_ms=320_000),
        execution_windows_by_horizon=execution_windows,
        trial_horizon_by_id=trial_horizons,
    )

    stress = ScreeningAccountResult(
        account_id="ALPHA_F03_EXIT_E01_V1:STRESS",
        trial_id="ALPHA_F03_EXIT_E01_V1",
        profile="STRESS",
        starting_equity_usdt=Decimal("1000"),
        final_equity_usdt=Decimal("1001.6"),
        evaluated_event_count=2,
        signal_count=2,
        attempted_entry_count=2,
        rejected_entry_count=0,
        trades=(inside, crossing),
    )
    base = replace(
        stress,
        account_id="ALPHA_F03_EXIT_E01_V1:BASE",
        profile="BASE",
        trades=tuple(replace(trade, profile="BASE") for trade in stress.trades),
    )
    result = TrialScreeningResult(
        trial_id="ALPHA_F03_EXIT_E01_V1",
        status=ScreeningStatus.EXECUTED,
        blocker_codes=(),
        failure_code=None,
        deterministic_signal_pass=True,
        no_lookahead_pass=True,
        recursive_dependency_pass=True,
        accounts=(base, stress),
    )

    returns, trade_counts, excluded = _validation_fold_returns(
        (result,),
        {
            "MICRO_SCALP": (
                ValidationFold("A", 300_000, 400_000),
                ValidationFold("B", 400_000, 500_000),
                ValidationFold("C", 500_000, 600_000),
                ValidationFold("D", 600_000, 700_000),
            )
        },
        trial_horizons,
    )

    assert returns[result.trial_id] == (8.0, 0.0, 0.0, 0.0)
    assert trade_counts[result.trial_id] == (1, 0, 0, 0)
    assert excluded == 1
