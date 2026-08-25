# 장중 archive 연구 실행기의 보수적 체결과 purge·embargo를 검증한다.

from __future__ import annotations

from decimal import Decimal

from backend.app.domain.models import Side, Venue
from backend.app.features import BookFrame
from backend.app.intraday import (
    CandidateFamily,
    HorizonClass,
    ResearchPricePlan,
    ResearchVariantKind,
    SignalVariant,
)
from backend.app.research import DatasetSlice
from scripts.research_intraday_candidates import (
    PendingTrade,
    ResearchOutcome,
    _advance_pending,
    _mirror_signal_parity,
    _preregistered_keys,
    _purged_split_outcomes,
    _selection_report,
)


def _frame(ts_ms: int, bid: str, ask: str) -> BookFrame:
    return BookFrame.from_levels(
        venue=Venue.BINANCE_USDM,
        symbol="BTCUSDT",
        ts_ms=ts_ms,
        bids=((Decimal(bid), Decimal("10")),),
        asks=((Decimal(ask), Decimal("10")),),
        sequence_valid=True,
        stale=False,
        lag_ms=10,
    )


def _outcome(
    *,
    run_id: str,
    horizon: HorizonClass,
    entry_ts_ms: int,
    exit_ts_ms: int,
) -> ResearchOutcome:
    return ResearchOutcome(
        key=f"{horizon.value}:60:TEST:ORIGINAL",
        run_id=run_id,
        symbol="BTCUSDT",
        family="TEST",
        variant="ORIGINAL",
        horizon=horizon.value,
        interval_seconds=60,
        side="LONG",
        information_set_id=f"info-{entry_ts_ms}",
        entry_ts_ms=entry_ts_ms,
        exit_ts_ms=exit_ts_ms,
        holding_ms=exit_ts_ms - entry_ts_ms,
        exit_reason="MAX_HOLD",
        gross_bps=10,
        base_net_bps=-3,
        stress_net_bps=-15,
        regime="RANGE",
    )


def test_pending_trade_uses_bid_exit_and_staged_targets() -> None:
    signal = SignalVariant(
        candidate_id="TEST",
        variant=ResearchVariantKind.ORIGINAL,
        symbol="BTCUSDT",
        side=Side.LONG,
        signal_ts_ms=1_000,
        interval_seconds=60,
        information_set_id="info",
    )
    plan = ResearchPricePlan(
        side=Side.LONG,
        signal_ts_ms=1_000,
        entry=Decimal("100"),
        stop=Decimal("99"),
        take_profit_1=Decimal("101.2"),
        take_profit_2=Decimal("102.2"),
        risk_distance=Decimal("1"),
        maximum_holding_ms=60_000,
    )
    pending = PendingTrade(
        key="FAST_INTRADAY:60:TEST:ORIGINAL",
        run_id="run",
        signal=signal,
        family=CandidateFamily.FLOW_TREND_PULLBACK,
        horizon=HorizonClass.FAST_INTRADAY,
        plan=plan,
        regime="TREND_UP",
    )
    assert _advance_pending(pending, _frame(2_000, "101.3", "101.4")) is None
    outcome = _advance_pending(pending, _frame(3_000, "102.3", "102.4"))
    assert outcome is not None
    assert outcome.exit_reason == "TP2"
    assert abs(outcome.gross_bps - 150) < 1e-9
    assert abs(outcome.base_net_bps - 137) < 1e-9


def test_horizon_specific_purge_and_embargo_exclude_boundary_leakage() -> None:
    train_run = "RUN-94899287D623"
    validation_run = "RUN-ED214939F990"
    oos_run = "RUN-4C905F26DA0D"
    dataset = (
        DatasetSlice(train_run, "BINANCE_USDM", ("BTCUSDT",), 0, 10_000_000, 1, "a"),
        DatasetSlice(
            validation_run,
            "BINANCE_USDM",
            ("BTCUSDT",),
            10_100_000,
            20_000_000,
            1,
            "b",
        ),
        DatasetSlice(
            oos_run,
            "BINANCE_USDM",
            ("BTCUSDT",),
            20_100_000,
            40_000_000,
            1,
            "c",
        ),
    )
    by_run = {
        train_run: [
            _outcome(
                run_id=train_run,
                horizon=HorizonClass.MICRO_SCALP,
                entry_ts_ms=9_700_000,
                exit_ts_ms=9_900_000,
            )
        ],
        validation_run: [
            _outcome(
                run_id=validation_run,
                horizon=HorizonClass.MICRO_SCALP,
                entry_ts_ms=10_100_000,
                exit_ts_ms=10_200_000,
            ),
            _outcome(
                run_id=validation_run,
                horizon=HorizonClass.MICRO_SCALP,
                entry_ts_ms=10_300_000,
                exit_ts_ms=10_400_000,
            ),
        ],
        oos_run: [
            _outcome(
                run_id=oos_run,
                horizon=HorizonClass.INTRADAY_SWING,
                entry_ts_ms=21_000_000,
                exit_ts_ms=22_000_000,
            )
        ],
    }
    split, evidence = _purged_split_outcomes(
        by_run,
        dataset,
        (train_run, validation_run, oos_run),
    )
    assert split["train"] == []
    assert [row.entry_ts_ms for row in split["validation"]] == [10_300_000]
    assert split["oos"] == []
    assert evidence["status"] == "APPLIED"


def test_original_and_mechanical_mirror_signal_counts_must_match() -> None:
    base = "MICRO_SCALP:15:FLOW_TREND_PULLBACK"
    passing = _mirror_signal_parity(
        {
            "run": {
                "signals_by_key": {
                    f"{base}:ORIGINAL": 3,
                    f"{base}:MECHANICAL_MIRROR": 3,
                }
            }
        }
    )
    failing = _mirror_signal_parity(
        {
            "run": {
                "signals_by_key": {
                    f"{base}:ORIGINAL": 3,
                    f"{base}:MECHANICAL_MIRROR": 2,
                }
            }
        }
    )
    assert passing["status"] == "PASS"
    assert failing["status"] == "FAIL"


def test_all_preregistered_hypotheses_count_toward_multiple_testing() -> None:
    keys = _preregistered_keys()

    assert len(keys) == 12 * len(CandidateFamily) * len(ResearchVariantKind)
    assert any("HYPOTHESIS_REVERSE" in key for key in keys)
    assert any("MECHANICAL_MIRROR" in key for key in keys)

    selection = _selection_report(
        [],
        [],
        keys=keys,
        train_validation_run_ids=("train-a", "train-b", "validation-a", "validation-b"),
    )
    assert selection["candidate_count"] == 12 * len(CandidateFamily) * 2
    assert selection["selected_on_train_validation"] is None
