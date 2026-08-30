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
from backend.app.market_data import Candle
from backend.app.research import (
    ScreeningAccountResult,
    ScreeningStatus,
    ScreeningTrade,
    TrialScreeningResult,
    cost_covered_exit_variant_manifest,
    cost_covered_exit_variant_trials,
    preregistered_trials,
)
from backend.app.research.strategy100_dataset_v2 import (
    build_strategy_100_dataset_v2_manifest,
)
from backend.app.research.strategy100_dataset_v2 import (
    manifest_checksum as v2_manifest_checksum,
)
from backend.app.research.strategy100_warmup import FrozenStrategy100Warmup
from scripts.export_strategy_100_manifest import BOUND_SOURCE_FILES
from scripts.freeze_strategy_100_warmup import _ordered_complete_rows
from scripts.research_intraday_candidates import _event_rows, _trade_tick
from scripts.research_strategy_100_candidates import (
    DEFAULT_RESEARCH_OUTPUTS,
    RESEARCH_FEATURE_HISTORY_BARS,
    RESEARCH_FEATURE_SNAPSHOT_INTERVAL_MS,
    AccountCounters,
    ResearchAccountCarry,
    ResearchCpuBudget,
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
    _trials_for_manifest,
    _validate_output_contract,
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


def test_research_archive_reader_honors_external_spill_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    spill_root = tmp_path / "large-external-spill"
    rows = [
        {
            "ts_ms": 1_000,
            "venue_ts_ms": 1_000,
            "symbol": "BTCUSDT",
            "payload_json": json.dumps(
                {
                    "event_id": "external-spill",
                    "event_type": "TRADE",
                    "symbol": "BTCUSDT",
                    "venue_ts_ms": 1_000,
                    "receive_ts_ms": 1_001,
                    "receive_monotonic_ns": 1,
                }
            ),
        }
    ]
    pq.write_table(pa.Table.from_pylist(rows), archive / "events.parquet")
    monkeypatch.setenv("ROBOM_RESEARCH_SPILL_ROOT", str(spill_root))

    assert [row["event_id"] for row in _event_rows(archive)] == ["external-spill"]
    assert not (tmp_path / ".research-duckdb-spill").exists()
    assert not spill_root.exists()


def test_large_research_archive_requires_explicit_spill_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("ROBOM_RESEARCH_SPILL_ROOT", raising=False)
    archive = tmp_path / "large-archive"
    archive.mkdir()
    for index in range(500):
        (archive / f"events-{index:03d}.parquet").touch()

    with pytest.raises(ValueError, match="ROBOM_RESEARCH_SPILL_ROOT"):
        next(_event_rows(archive))


def test_research_archive_reader_uses_only_the_frozen_explicit_files(
    tmp_path: Path,
) -> None:
    def write(path: Path, event_id: str) -> None:
        payload = {
            "event_id": event_id,
            "event_type": "TRADE",
            "symbol": "BTCUSDT",
            "venue_ts_ms": 1_000,
            "receive_ts_ms": 2_000,
            "receive_monotonic_ns": 1,
        }
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "ts_ms": 1_000,
                        "venue_ts_ms": 1_000,
                        "symbol": "BTCUSDT",
                        "payload_json": json.dumps(payload),
                    }
                ]
            ),
            path,
        )

    frozen = tmp_path / "frozen.parquet"
    appended_later = tmp_path / "appended-later.parquet"
    write(frozen, "frozen")
    write(appended_later, "not-in-cut")

    assert [row["event_id"] for row in _event_rows(tmp_path, files=(frozen,))] == [
        "frozen"
    ]


def test_research_archive_reader_filters_frozen_symbols_before_limit(
    tmp_path: Path,
) -> None:
    rows = []
    for index, symbol in enumerate(("ETHUSDT", "BTCUSDT", "BTCUSDT"), start=1):
        payload = {
            "event_id": f"event-{index}",
            "event_type": "TRADE",
            "symbol": symbol,
            "venue_ts_ms": index * 1_000,
            "receive_ts_ms": index * 1_000,
            "receive_monotonic_ns": index,
        }
        rows.append(
            {
                "ts_ms": index * 1_000,
                "venue_ts_ms": index * 1_000,
                # 실제 MULTI archive처럼 parquet 상위 종목과 payload
                # 종목이 다른 경로를 고정해 payload 필터 회귀를 검출한다.
                "symbol": "MULTI",
                "payload_json": json.dumps(payload),
            }
        )
    pq.write_table(pa.Table.from_pylist(rows), tmp_path / "events.parquet")

    assert [
        row["event_id"]
        for row in _event_rows(
            tmp_path,
            symbols=("BTCUSDT",),
            maximum_events=1,
        )
    ] == ["event-2"]

    with pytest.raises(ValueError, match="research 종목"):
        next(_event_rows(tmp_path, symbols=()))


def test_frozen_public_warmup_aggregates_only_completed_past_bars(
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "data" / "warmup"
    evidence_root = tmp_path / "evidence"
    cache_root.mkdir(parents=True)
    evidence_root.mkdir()
    rows = [
        {
            "symbol": "BTCUSDT",
            "open_ts_ms": index * 300_000,
            "open": "100",
            "high": "102",
            "low": "99",
            "close": str(100 + index / 100),
            "volume": "10",
            "quote_volume": "1000",
            "trade_count": 20,
            "taker_buy_volume": "6",
            "taker_buy_quote_volume": "600",
        }
        for index in range(12)
    ]
    data_path = cache_root / "BTCUSDT.json"
    data_bytes = json.dumps(rows, separators=(",", ":")).encode() + b"\n"
    data_path.write_bytes(data_bytes)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "status": "FROZEN_PUBLIC_KLINE_WARMUP",
        "cache_root": "data/warmup",
        "cutoff_ts_ms": 3_600_000,
        "symbols": ["BTCUSDT"],
        "files": [
            {
                "symbol": "BTCUSDT",
                "relative_path": "BTCUSDT.json",
                "file_sha256": hashlib.sha256(data_bytes).hexdigest(),
                "bar_count": 12,
            }
        ],
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "auth_required": False,
    }
    manifest["manifest_sha256"] = v2_manifest_checksum(manifest)
    manifest_path = evidence_root / "warmup.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    warmup = FrozenStrategy100Warmup.load(manifest_path)
    candles = warmup.candles_before(3_600_000, maximum_bars=640)

    assert warmup.symbols == ("BTCUSDT",)
    assert len([row for row in candles if row.interval_seconds == 300]) == 12
    assert len([row for row in candles if row.interval_seconds == 900]) == 4
    assert len([row for row in candles if row.interval_seconds == 3_600]) == 1
    assert all(
        row.open_ts_ms + row.interval_seconds * 1_000 <= 3_600_000 for row in candles
    )


def test_public_warmup_cache_rejects_duplicate_or_missing_bars() -> None:
    complete = [
        {"symbol": "BTCUSDT", "open_ts_ms": index * 300_000}
        for index in range(3)
    ]

    assert len(
        _ordered_complete_rows(
            complete,
            symbol="BTCUSDT",
            start_ms=0,
            end_ms=900_000,
        )
    ) == 3
    with pytest.raises(RuntimeError, match="중복"):
        _ordered_complete_rows(
            [complete[0], complete[0], complete[2]],
            symbol="BTCUSDT",
            start_ms=0,
            end_ms=900_000,
        )
    with pytest.raises(RuntimeError, match="연속 구간"):
        _ordered_complete_rows(
            [complete[0], complete[2]],
            symbol="BTCUSDT",
            start_ms=0,
            end_ms=900_000,
        )


def test_v2_dataset_makes_disjoint_logical_runs_from_one_long_public_run() -> None:
    trial: dict[str, object] = {
        "status": "PREREGISTERED_NOT_EXECUTED",
        "trial_count": 100,
        "screening_eligible_count": 90,
        "runtime_active_count": 0,
        "live_shadow_count": 0,
        "code_version": "fixture",
        "source_checksums": {"fixture.py": "a" * 64},
    }
    trial["manifest_sha256"] = v2_manifest_checksum(trial)
    files = [
        {
            "relative_path": (
                "venue=BINANCE_USDM/run=RUN-LONG/date=2026-01-"
                f"{1 + index // 24:02d}/symbol=MULTI/hour={index % 24:02d}/"
                f"event_type=MARKET_EVENT/part-{index}.parquet"
            ),
            "event_count": 10,
            "first_ts_ms": index * 3_600_000,
            "last_ts_ms": (index + 1) * 3_600_000 - 1,
        }
        for index in range(101)
    ]
    cut: dict[str, object] = {
        "status": "FROZEN_LIVE_PUBLIC_CUT",
        "run_id": "RUN-LONG",
        "archive_root": "/tmp/archive",
        "file_count": 101,
        "event_count": 1_010,
        "files": files,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
    }
    cut["manifest_sha256"] = v2_manifest_checksum(cut)
    warmup: dict[str, object] = {
        "status": "FROZEN_PUBLIC_KLINE_WARMUP",
        "symbol_count": 24,
        "cutoff_ts_ms": 0,
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
    }
    warmup["manifest_sha256"] = v2_manifest_checksum(warmup)

    dataset = build_strategy_100_dataset_v2_manifest(
        trial_manifest=trial,
        trial_manifest_path="evidence/trial.json",
        trial_manifest_file_sha256="b" * 64,
        live_public_cut=cut,
        live_public_cut_path="evidence/cut.json",
        live_public_cut_file_sha256="c" * 64,
        warmup_manifest=warmup,
        warmup_manifest_path="evidence/warmup.json",
        warmup_manifest_file_sha256="d" * 64,
        train_hours=20,
        validation_hours_each=40,
        generated_ts_utc="2026-08-30T00:00:00Z",
    )

    assert [len(row["archive_partitions"]) for row in dataset["runs"]] == [
        20,
        40,
        40,
        1,
    ]
    assert [row["role"] for row in dataset["runs"]] == [
        "TRAIN",
        "VALIDATION",
        "VALIDATION",
        "FINAL_OOS",
    ]
    assert len(
        {
            partition
            for row in dataset["runs"]
            for partition in row["archive_partitions"]
        }
    ) == 101
    assert dataset["research_interpretation"]["promotion_eligible"] is False


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
    reader_kwargs: dict[str, object] = {}

    def event_rows(*_args, **kwargs):
        reader_kwargs.update(kwargs)
        return iter(
            (
                {"event_id": "future-exchange-time", "venue_ts_ms": 3_000},
                {"event_id": "late-in-window", "venue_ts_ms": 1_000},
            )
        )

    monkeypatch.setattr(
        "scripts.research_strategy_100_candidates._event_rows",
        event_rows,
    )

    assert executor.execute() == ()
    assert processed == ["late-in-window"]
    assert reader_kwargs["symbols"] is None


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


def test_research_feature_snapshot_matches_live_500ms_cadence_but_every_book_executes() -> None:
    trial = next(
        row
        for row in preregistered_trials()
        if row.screening_eligible and row.alpha.horizon == "MICRO_SCALP"
    )
    executor = Strategy100RunExecutor(
        run_id="run-feature-cadence",
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
                    window_id="run-feature-cadence:TRAIN",
                    horizon="MICRO_SCALP",
                    entry_start_ts_ms=0,
                    entry_cutoff_ts_ms=10_000,
                    observation_end_ts_ms=191_000,
                    maximum_holding_ms=180_000,
                    purge_embargo_ms=180_000,
                ),
            )
        },
        account_carry={},
        research_symbols=("BTCUSDT",),
    )

    class CountingFeatureEngine:
        def __init__(self) -> None:
            self.ingest_count = 0
            self.snapshot_count = 0

        def ingest_book(self, _frame) -> None:
            self.ingest_count += 1

        def snapshot(self):
            self.snapshot_count += 1
            raise FeatureInputError("fixture warmup")

    engine = CountingFeatureEngine()
    executor.feature_engines["BTCUSDT"] = engine
    execution_timestamps: list[int] = []
    executor.portfolio.on_book = lambda book: execution_timestamps.append(book.ts_ms)

    def payload(ts_ms: int, *, symbol: str = "BTCUSDT") -> dict[str, object]:
        return {
            "event_type": "DEPTH_UPDATE",
            "symbol": symbol,
            "venue_ts_ms": ts_ms,
            "quality": {
                "sequence_valid": True,
                "is_stale": False,
                "lag_ms": 0,
            },
            "data": {
                "bids": [["99.9", "10"]],
                "asks": [["100.1", "10"]],
            },
        }

    executor._process(payload(900, symbol="ETHUSDT"))
    for timestamp in (1_000, 1_100, 1_250, 1_500):
        executor._process(payload(timestamp))

    assert RESEARCH_FEATURE_SNAPSHOT_INTERVAL_MS == 500
    assert executor.diagnostics.event_count == 5
    assert executor.diagnostics.outside_research_universe_event_count == 1
    assert "ETHUSDT" not in executor.feature_engines
    assert execution_timestamps == [1_000, 1_100, 1_250, 1_500]
    assert engine.ingest_count == 4
    assert engine.snapshot_count == 2
    assert executor.diagnostics.feature_snapshot_count == 2
    assert executor.diagnostics.feature_snapshot_throttled_count == 2


def test_recursive_f20_audit_keeps_the_same_past_only_history_window() -> None:
    trial = next(
        row for row in preregistered_trials() if row.alpha.family_id == "F20"
    )
    executor = Strategy100RunExecutor(
        run_id="run-f20-history",
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
                    window_id="run-f20-history:TRAIN",
                    horizon="MICRO_SCALP",
                    entry_start_ts_ms=0,
                    entry_cutoff_ts_ms=1_000_000,
                    observation_end_ts_ms=1_181_000,
                    maximum_holding_ms=180_000,
                    purge_embargo_ms=180_000,
                ),
            )
        },
        account_carry={},
    )
    assert RESEARCH_FEATURE_HISTORY_BARS > 320
    for index in range(400):
        offset = Decimal(index) / Decimal("1000")
        candle = Candle(
            symbol="BTCUSDT",
            interval_seconds=1,
            open_ts_ms=index * 1_000,
            open=Decimal("100") + offset,
            high=Decimal("100.1") + offset,
            low=Decimal("99.9") + offset,
            close=Decimal("100.05") + offset,
            volume=Decimal("10") + index,
            trade_count=10 + index,
            quote_volume=Decimal("1000") + index,
            taker_buy_volume=Decimal("6"),
            taker_sell_volume=Decimal("4"),
        )
        executor.alpha_features.ingest_completed(candle)
        executor.recursive_features.ingest_completed(candle)

    decision_ts_ms = 400_000
    primary = executor.alpha_features.snapshot(
        "BTCUSDT", "F20", decision_ts_ms=decision_ts_ms
    )
    recursive = executor.recursive_features.snapshot(
        "BTCUSDT", "F20", decision_ts_ms=decision_ts_ms
    )

    assert primary is not None
    assert recursive == primary


def test_research_cpu_budget_yields_to_the_live_service() -> None:
    monotonic_values = iter((0.0, 0.25))
    process_values = iter((0.0, 0.1))
    sleeps: list[float] = []
    budget = ResearchCpuBudget(
        target_cpu_ratio=0.2,
        monotonic=lambda: next(monotonic_values),
        process_time=lambda: next(process_values),
        sleeper=sleeps.append,
    )

    budget.checkpoint()

    assert sleeps[0] == pytest.approx(0.25)


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


def test_variant_manifest_selects_only_the_separate_e06_trials() -> None:
    manifest = cost_covered_exit_variant_manifest(
        code_version="code",
        generated_ts_utc="2026-08-29T00:00:00Z",
        source_checksums={"variant.py": "a" * 64},
        parent_trial_manifest={
            "path": "evidence/STRATEGY_100_TRIAL_MANIFEST.json",
            "manifest_sha256": "b" * 64,
            "file_sha256": "c" * 64,
        },
    )

    assert _trials_for_manifest(manifest) == cost_covered_exit_variant_trials()
    tampered = dict(manifest)
    tampered["trials"] = list(reversed(manifest["trials"]))
    with pytest.raises(ValueError, match="순서 또는 ID"):
        _trials_for_manifest(tampered)


def test_variant_batch_cannot_overwrite_frozen_or_existing_research_outputs(
    tmp_path: Path,
) -> None:
    frozen = tuple(tmp_path / path.name for path in DEFAULT_RESEARCH_OUTPUTS)
    with pytest.raises(ValueError, match="동결 100후보와 분리"):
        _validate_output_contract(
            DEFAULT_RESEARCH_OUTPUTS,
            manifest_kind="COST_COVERED_EXIT_VARIANT_BATCH",
        )

    frozen[0].write_text("preserved", encoding="utf-8")
    with pytest.raises(FileExistsError, match="덮어쓰지"):
        _validate_output_contract(
            frozen,
            manifest_kind="STRATEGY_100_FROZEN_BATCH",
        )
    assert frozen[0].read_text(encoding="utf-8") == "preserved"


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
