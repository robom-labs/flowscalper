# 100후보 PAPER screening 결과와 비용·표본·다중검정 gate를 fail-closed로 집계한다.

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from statistics import fmean, median
from typing import Any, cast

from backend.app.research.candidate_registry import ResearchTrialSpec, preregistered_trials
from backend.app.research.protocol import (
    bootstrap_mean_interval,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)

STARTING_EQUITY_USDT = Decimal("1000")
SCREENING_PROFILES = ("BASE", "STRESS")
SCREENING_SEED = 20260828
MINIMUM_FINAL_OOS_BY_HORIZON = {
    "MICRO_SCALP": {"trades": 1_000, "span_days": 60, "symbols": 20, "regimes": 4},
    "FAST_INTRADAY": {"trades": 300, "span_days": 180, "symbols": 10, "regimes": 4},
    "INTRADAY_SWING": {"trades": 150, "span_days": 365, "symbols": 8, "regimes": 4},
}
# Stage 1 선택이 Final OOS보다 약한 표본을 운 좋은 후보로 통과시키지 않도록 같은
# 사전등록 floor를 Validation에도 적용한다. Final OOS floor 자체를 대체하지는 않는다.
MINIMUM_VALIDATION_BY_HORIZON = MINIMUM_FINAL_OOS_BY_HORIZON


def point_in_time_volatility_regime(*, fast: float, slow: float) -> str:
    """신호 시점의 실현변동성 비율을 사전 고정된 holdout 구간으로 변환한다."""

    if not math.isfinite(fast) or not math.isfinite(slow) or fast < 0 or slow < 0:
        raise ValueError("실현변동성은 0 이상의 유한한 값이어야 합니다.")
    if slow == 0:
        return "UNKNOWN"
    ratio = fast / slow
    if ratio < 0.75:
        return "LOW"
    if ratio <= 1.5:
        return "NORMAL"
    return "HIGH"


class ScreeningStatus(StrEnum):
    BLOCKED = "BLOCKED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ScreeningTrade:
    trade_id: str
    trial_id: str
    profile: str
    split: str
    run_id: str
    symbol: str
    regime: str
    side: str
    entry_ts_ms: int
    exit_ts_ms: int
    gross_pnl_usdt: Decimal
    fee_usdt: Decimal
    slippage_usdt: Decimal
    net_pnl_usdt: Decimal
    net_return_bps: float
    mfe_r: Decimal
    mae_r: Decimal
    giveback_usdt: Decimal
    signal_event_id: str | None = None
    exit_reason: str = "UNKNOWN"
    tp1_hit_ts_ms: int | None = None
    trailing_activation_ts_ms: int | None = None
    runner_started_ts_ms: int | None = None
    peak_unrealized_usdt: Decimal = Decimal(0)
    runner_net_pnl_usdt: Decimal = Decimal(0)
    trail_trigger_slippage_usdt: Decimal = Decimal(0)
    trailing_state_checksum: str | None = None
    venue: str = "UNKNOWN"
    volatility_regime: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if (
            not self.trade_id
            or not self.trial_id
            or self.profile not in SCREENING_PROFILES
            or self.split not in {"TRAIN", "VALIDATION", "FINAL_OOS"}
            or not self.run_id
            or not self.symbol
            or not self.regime
            or not self.venue
            or not self.volatility_regime
            or self.side not in {"LONG", "SHORT"}
            or not self.exit_reason
        ):
            raise ValueError("screening 거래 식별자·범위가 잘못됐습니다.")
        if self.entry_ts_ms < 0 or self.exit_ts_ms < self.entry_ts_ms:
            raise ValueError("screening 거래 시각 순서가 잘못됐습니다.")
        milestone_times = (
            self.tp1_hit_ts_ms,
            self.trailing_activation_ts_ms,
            self.runner_started_ts_ms,
        )
        if any(
            timestamp is not None and (timestamp < self.entry_ts_ms or timestamp > self.exit_ts_ms)
            for timestamp in milestone_times
        ):
            raise ValueError("screening 거래 milestone 시각이 진입·종료 범위를 벗어났습니다.")
        if self.runner_started_ts_ms is not None and (
            self.trailing_activation_ts_ms is None
            or self.runner_started_ts_ms < self.trailing_activation_ts_ms
        ):
            raise ValueError("screening 러너는 trailing 활성화 후에만 시작할 수 있습니다.")
        decimals = (
            self.gross_pnl_usdt,
            self.fee_usdt,
            self.slippage_usdt,
            self.net_pnl_usdt,
            self.mfe_r,
            self.mae_r,
            self.giveback_usdt,
            self.peak_unrealized_usdt,
            self.runner_net_pnl_usdt,
            self.trail_trigger_slippage_usdt,
        )
        if any(not value.is_finite() for value in decimals) or not math.isfinite(
            self.net_return_bps
        ):
            raise ValueError("screening 거래 숫자는 유한해야 합니다.")
        if (
            self.fee_usdt < 0
            or self.slippage_usdt < 0
            or self.giveback_usdt < 0
            or self.peak_unrealized_usdt < 0
            or self.trail_trigger_slippage_usdt < 0
        ):
            raise ValueError("비용과 giveback은 음수일 수 없습니다.")
        if self.net_pnl_usdt != self.gross_pnl_usdt - self.fee_usdt - self.slippage_usdt:
            raise ValueError("screening 거래 순손익이 비용과 일치하지 않습니다.")


@dataclass(frozen=True, slots=True)
class ScreeningAccountResult:
    account_id: str
    trial_id: str
    profile: str
    starting_equity_usdt: Decimal
    final_equity_usdt: Decimal
    evaluated_event_count: int
    signal_count: int
    attempted_entry_count: int
    rejected_entry_count: int
    trades: tuple[ScreeningTrade, ...]
    rejection_counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.account_id
            or not self.trial_id
            or self.profile not in SCREENING_PROFILES
            or self.starting_equity_usdt != STARTING_EQUITY_USDT
        ):
            raise ValueError("screening 독립계좌 계약이 잘못됐습니다.")
        counts = (
            self.evaluated_event_count,
            self.signal_count,
            self.attempted_entry_count,
            self.rejected_entry_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("screening 계좌 count는 음수일 수 없습니다.")
        if self.signal_count > self.evaluated_event_count:
            raise ValueError("screening 신호 수가 평가 이벤트 수보다 많습니다.")
        if self.attempted_entry_count > self.signal_count:
            raise ValueError("screening 진입 시도가 신호 수보다 많습니다.")
        if self.rejected_entry_count > self.attempted_entry_count:
            raise ValueError("screening 거부 수가 진입 시도보다 많습니다.")
        if len({trade.trade_id for trade in self.trades}) != len(self.trades):
            raise ValueError("screening 거래 ID가 중복됐습니다.")
        if any(
            trade.trial_id != self.trial_id or trade.profile != self.profile
            for trade in self.trades
        ):
            raise ValueError("screening 계좌에 다른 trial 또는 profile 거래가 섞였습니다.")
        expected_equity = self.starting_equity_usdt + sum(
            (trade.net_pnl_usdt for trade in self.trades),
            start=Decimal(0),
        )
        if self.final_equity_usdt != expected_equity:
            raise ValueError("screening 계좌 자산이 거래 순손익과 일치하지 않습니다.")
        if self.final_equity_usdt < 0:
            raise ValueError("screening 계좌 자산은 음수가 될 수 없습니다.")
        if any(not reason or count <= 0 for reason, count in self.rejection_counts):
            raise ValueError("screening 거부 사유 집계가 잘못됐습니다.")


@dataclass(frozen=True, slots=True)
class TrialScreeningResult:
    trial_id: str
    status: ScreeningStatus
    blocker_codes: tuple[str, ...]
    failure_code: str | None
    deterministic_signal_pass: bool
    no_lookahead_pass: bool
    recursive_dependency_pass: bool
    accounts: tuple[ScreeningAccountResult, ...]

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ValueError("screening trial ID가 필요합니다.")
        if self.status is ScreeningStatus.BLOCKED:
            if not self.blocker_codes or self.accounts:
                raise ValueError("BLOCKED trial에는 blocker가 있고 계좌 실행은 없어야 합니다.")
            return
        if self.status is ScreeningStatus.FAILED:
            if not self.failure_code:
                raise ValueError("FAILED trial에는 failure code가 필요합니다.")
            if self.accounts and (
                len(self.accounts) != 2
                or {account.profile for account in self.accounts} != set(SCREENING_PROFILES)
                or any(account.trial_id != self.trial_id for account in self.accounts)
            ):
                raise ValueError("FAILED trial의 보존 계좌 격리가 잘못됐습니다.")
            return
        if self.failure_code is not None or self.blocker_codes:
            raise ValueError("EXECUTED trial에는 blocker나 failure가 없어야 합니다.")
        if {account.profile for account in self.accounts} != set(SCREENING_PROFILES):
            raise ValueError("EXECUTED trial은 BASE·STRESS 독립계좌가 모두 필요합니다.")
        if len(self.accounts) != 2 or any(
            account.trial_id != self.trial_id for account in self.accounts
        ):
            raise ValueError("screening trial 계좌 격리가 잘못됐습니다.")


def _profile(trades: Sequence[ScreeningTrade]) -> dict[str, object]:
    ordered = sorted(trades, key=lambda trade: (trade.entry_ts_ms, trade.trade_id))
    returns = [trade.net_return_bps for trade in ordered]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    equity = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    gross_loss = abs(sum(losses))
    activated = [trade for trade in ordered if trade.trailing_activation_ts_ms is not None]
    runners = [trade for trade in ordered if trade.runner_started_ts_ms is not None]
    givebacks = sorted(trade.giveback_usdt for trade in ordered)
    capture_ratios = [
        float(trade.net_pnl_usdt / trade.peak_unrealized_usdt)
        for trade in ordered
        if trade.peak_unrealized_usdt > 0
    ]
    p90_index = max(0, math.ceil(len(givebacks) * 0.9) - 1)
    return {
        "sample_size": len(ordered),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(ordered) if ordered else None,
        "expectancy_bps": fmean(returns) if returns else None,
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "net_pnl_usdt": str(sum((trade.net_pnl_usdt for trade in ordered), start=Decimal(0))),
        "fees_usdt": str(sum((trade.fee_usdt for trade in ordered), start=Decimal(0))),
        "slippage_usdt": str(sum((trade.slippage_usdt for trade in ordered), start=Decimal(0))),
        "maximum_drawdown_bps": maximum_drawdown,
        "symbols": sorted({trade.symbol for trade in ordered}),
        "regimes": sorted({trade.regime for trade in ordered}),
        "calendar_span_days": (
            (
                max(trade.exit_ts_ms for trade in ordered)
                - min(trade.entry_ts_ms for trade in ordered)
            )
            / 86_400_000
            if ordered
            else 0.0
        ),
        "single_symbol_pnl_contribution": _maximum_positive_contribution(
            (trade.symbol, trade.net_pnl_usdt) for trade in ordered
        ),
        "single_trade_pnl_contribution": _maximum_positive_contribution(
            (trade.trade_id, trade.net_pnl_usdt) for trade in ordered
        ),
        "trail_activation_count": len(activated),
        "trail_activation_rate": len(activated) / len(ordered) if ordered else None,
        "tp1_fill_rate": (
            sum(trade.tp1_hit_ts_ms is not None for trade in ordered) / len(ordered)
            if ordered
            else None
        ),
        "runner_count": len(runners),
        "runner_rate": len(runners) / len(ordered) if ordered else None,
        "runner_net_contribution_usdt": str(
            sum((trade.runner_net_pnl_usdt for trade in ordered), start=Decimal(0))
        ),
        "mfe_capture_ratio_mean": fmean(capture_ratios) if capture_ratios else None,
        "average_peak_giveback_usdt": str(
            sum(givebacks, start=Decimal(0)) / len(givebacks) if givebacks else Decimal(0)
        ),
        "median_peak_giveback_usdt": str(median(givebacks) if givebacks else Decimal(0)),
        "p90_peak_giveback_usdt": str(givebacks[p90_index] if givebacks else Decimal(0)),
        "trail_trigger_count": sum(trade.exit_reason == "TRAILING_STOP" for trade in ordered),
        "trail_trigger_slippage_usdt": str(
            sum(
                (trade.trail_trigger_slippage_usdt for trade in ordered),
                start=Decimal(0),
            )
        ),
        "activation_after_net_negative_exit_count": sum(
            trade.trailing_activation_ts_ms is not None and trade.net_pnl_usdt < 0
            for trade in ordered
        ),
        "stop_before_trail_activation_count": sum(
            trade.exit_reason == "STOP" and trade.trailing_activation_ts_ms is None
            for trade in ordered
        ),
    }


def _maximum_positive_contribution(
    rows: Iterable[tuple[str, Decimal]],
) -> float | None:
    grouped: dict[str, Decimal] = defaultdict(Decimal)
    for key, value in rows:
        grouped[key] += value
    positives = [value for value in grouped.values() if value > 0]
    total = sum(positives, start=Decimal(0))
    if total <= 0:
        return None
    return float(max(positives) / total)


def _trial_statistics(
    trial: ResearchTrialSpec,
    result: TrialScreeningResult,
    *,
    trials_count: int,
) -> dict[str, Any]:
    if result.status is ScreeningStatus.BLOCKED:
        return {
            "status": result.status.value,
            "blocker_codes": list(result.blocker_codes),
            "failure_code": result.failure_code,
            "profiles": {},
            "gate": {"passed": False, "reasons": [result.status.value]},
        }
    profiles: dict[str, object] = {}
    for account in sorted(result.accounts, key=lambda row: row.profile):
        split_profiles = {
            split: _profile([trade for trade in account.trades if trade.split == split])
            for split in ("TRAIN", "VALIDATION", "FINAL_OOS")
        }
        profiles[account.profile] = {
            "account": {
                "account_id": account.account_id,
                "starting_equity_usdt": str(account.starting_equity_usdt),
                "final_equity_usdt": str(account.final_equity_usdt),
                "evaluated_event_count": account.evaluated_event_count,
                "signal_count": account.signal_count,
                "attempted_entry_count": account.attempted_entry_count,
                "rejected_entry_count": account.rejected_entry_count,
                "rejection_counts": dict(account.rejection_counts),
            },
            "splits": split_profiles,
        }
    if result.status is ScreeningStatus.FAILED:
        return {
            "status": result.status.value,
            "blocker_codes": [],
            "failure_code": result.failure_code,
            "profiles": profiles,
            "gate": {
                "stage": "VALIDATION_SCREENING",
                "passed": False,
                "reasons": ["FAILED", str(result.failure_code)],
            },
        }
    base_account = next(account for account in result.accounts if account.profile == "BASE")
    stress_account = next(account for account in result.accounts if account.profile == "STRESS")
    base_validation_trades = [trade for trade in base_account.trades if trade.split == "VALIDATION"]
    stress_validation_trades = [
        trade for trade in stress_account.trades if trade.split == "VALIDATION"
    ]
    base_profile = _profile(base_validation_trades)
    stress_profile = _profile(stress_validation_trades)
    base_returns = [trade.net_return_bps for trade in base_validation_trades]
    stress_returns = [trade.net_return_bps for trade in stress_validation_trades]
    bootstrap = {
        "BASE": bootstrap_mean_interval(base_returns, seed=SCREENING_SEED),
        "STRESS": bootstrap_mean_interval(stress_returns, seed=SCREENING_SEED),
    }
    dsr = {
        "BASE": deflated_sharpe_ratio(base_returns, trials=trials_count),
        "STRESS": deflated_sharpe_ratio(stress_returns, trials=trials_count),
    }
    minimum = MINIMUM_VALIDATION_BY_HORIZON[trial.alpha.horizon]
    reasons: list[str] = []
    gates = (
        (result.deterministic_signal_pass, "DETERMINISM_NOT_PROVEN"),
        (result.no_lookahead_pass, "NO_LOOKAHEAD_NOT_PROVEN"),
        (result.recursive_dependency_pass, "RECURSIVE_DEPENDENCY_NOT_PROVEN"),
        (
            int(str(base_profile["sample_size"])) >= int(minimum["trades"]),
            "VALIDATION_TRADES_INSUFFICIENT",
        ),
        (
            float(str(base_profile["calendar_span_days"])) >= float(minimum["span_days"]),
            "VALIDATION_SPAN_INSUFFICIENT",
        ),
        (
            len(cast(Sequence[object], base_profile["symbols"])) >= int(minimum["symbols"]),
            "VALIDATION_SYMBOLS_INSUFFICIENT",
        ),
        (
            len(cast(Sequence[object], base_profile["regimes"])) >= int(minimum["regimes"]),
            "VALIDATION_REGIMES_INSUFFICIENT",
        ),
        (
            base_profile["expectancy_bps"] is not None
            and float(str(base_profile["expectancy_bps"])) > 0,
            "BASE_EXPECTANCY_NOT_POSITIVE",
        ),
        (
            stress_profile["expectancy_bps"] is not None
            and float(str(stress_profile["expectancy_bps"])) > 0,
            "STRESS_EXPECTANCY_NOT_POSITIVE",
        ),
        (
            base_profile["profit_factor"] is not None
            and float(str(base_profile["profit_factor"])) > 1,
            "BASE_PROFIT_FACTOR_NOT_ABOVE_ONE",
        ),
        (
            stress_profile["profit_factor"] is not None
            and float(str(stress_profile["profit_factor"])) > 1,
            "STRESS_PROFIT_FACTOR_NOT_ABOVE_ONE",
        ),
        (
            bootstrap["BASE"].get("lower") is not None
            and float(str(bootstrap["BASE"]["lower"])) > 0,
            "BASE_BOOTSTRAP_LOWER_BOUND_NOT_POSITIVE",
        ),
        (
            bootstrap["STRESS"].get("lower") is not None
            and float(str(bootstrap["STRESS"]["lower"])) > 0,
            "STRESS_BOOTSTRAP_LOWER_BOUND_NOT_POSITIVE",
        ),
        (
            dsr["BASE"].get("dsr_probability") is not None
            and float(str(dsr["BASE"]["dsr_probability"])) >= 0.95,
            "BASE_DSR_BELOW_0_95_OR_MISSING",
        ),
        (
            dsr["STRESS"].get("dsr_probability") is not None
            and float(str(dsr["STRESS"]["dsr_probability"])) >= 0.95,
            "STRESS_DSR_BELOW_0_95_OR_MISSING",
        ),
        (
            base_profile["single_symbol_pnl_contribution"] is not None
            and float(str(base_profile["single_symbol_pnl_contribution"])) < 0.25,
            "SYMBOL_CONCENTRATION_HIGH_OR_MISSING",
        ),
        (
            base_profile["single_trade_pnl_contribution"] is not None
            and float(str(base_profile["single_trade_pnl_contribution"])) < 0.10,
            "TRADE_CONCENTRATION_HIGH_OR_MISSING",
        ),
    )
    reasons.extend(reason for passed, reason in gates if not passed)
    return {
        "status": result.status.value,
        "blocker_codes": [],
        "failure_code": None,
        "profiles": profiles,
        "validation_bootstrap_expectancy_95pct": bootstrap,
        "validation_deflated_sharpe_ratio": dsr,
        "gate": {
            "stage": "VALIDATION_SCREENING",
            "passed": not reasons,
            "reasons": reasons,
        },
    }


def build_screening_report(
    results: Sequence[TrialScreeningResult],
    *,
    trial_manifest_sha256: str,
    dataset_manifest_sha256: str,
    validation_fold_returns: Mapping[str, Sequence[float]],
    generated_ts_utc: str,
    trials: Sequence[ResearchTrialSpec] | None = None,
    selection_limit: int = 25,
) -> dict[str, Any]:
    """Final OOS를 봉인한 채 Validation에서 최대 25개 event replay 후보만 고른다."""

    default_registry = trials is None
    registered_trials = tuple(trials) if trials is not None else preregistered_trials()
    if not registered_trials or selection_limit <= 0:
        raise ValueError("screening trial과 선택 한도는 양수여야 합니다.")
    expected = {trial.trial_id: trial for trial in registered_trials}
    if len(expected) != len(registered_trials):
        raise ValueError("screening trial ID가 중복됐습니다.")
    actual = {result.trial_id: result for result in results}
    if len(actual) != len(results) or set(actual) != set(expected):
        scope = "사전등록 100개 trial" if default_registry else "사전등록 trial"
        raise ValueError(f"screening 결과는 {scope}을 중복 없이 전부 포함해야 합니다.")
    if not trial_manifest_sha256 or not dataset_manifest_sha256 or not generated_ts_utc:
        raise ValueError("screening manifest checksum과 생성시각이 필요합니다.")
    for trial_id, trial in expected.items():
        result = actual[trial_id]
        if trial.screening_eligible and result.status is ScreeningStatus.BLOCKED:
            raise ValueError("실행가능 trial을 BLOCKED로 바꿀 수 없습니다.")
        if not trial.screening_eligible and (
            result.status is not ScreeningStatus.BLOCKED
            or tuple(result.blocker_codes) != tuple(trial.alpha.blocker_codes)
        ):
            raise ValueError("SIHO BLOCKED trial의 원래 blocker를 보존해야 합니다.")
    final_oos_trade_count = sum(
        trade.split == "FINAL_OOS"
        for result in results
        for account in result.accounts
        for trade in account.trades
    )
    if final_oos_trade_count:
        raise ValueError("Stage 1 screening에는 봉인된 Final OOS 거래를 넣을 수 없습니다.")
    executable_ids = sorted(
        trial_id for trial_id, trial in expected.items() if trial.screening_eligible
    )
    executed_ids = sorted(
        trial_id
        for trial_id in executable_ids
        if actual[trial_id].status is ScreeningStatus.EXECUTED
    )
    failed_ids = sorted(set(executable_ids) - set(executed_ids))
    if set(validation_fold_returns) != set(executed_ids):
        raise ValueError(
            f"Validation PBO fold 수익률은 실제 EXECUTED {len(executed_ids)}개 trial을 "
            "모두 포함해야 합니다."
        )
    pbo: dict[str, object]
    if failed_ids:
        pbo = {
            "pbo": None,
            "combinations": 0,
            "logits": [],
            "status": "BLOCKED_TRIAL_FAILURES",
            "failed_trial_ids": failed_ids,
        }
    else:
        pbo = probability_of_backtest_overfitting(validation_fold_returns)
    statistics = {
        trial.trial_id: _trial_statistics(
            trial,
            actual[trial.trial_id],
            trials_count=len(registered_trials),
        )
        for trial in registered_trials
    }
    pbo_passed = pbo.get("pbo") is not None and float(str(pbo["pbo"])) <= 0.20
    eligible = [
        trial_id
        for trial_id in executable_ids
        if statistics[trial_id]["gate"]["passed"] and pbo_passed
    ]
    eligible.sort(
        key=lambda trial_id: (
            -float(
                str(
                    statistics[trial_id]["profiles"]["STRESS"]["splits"]["VALIDATION"][
                        "expectancy_bps"
                    ]
                )
            ),
            -float(
                str(
                    statistics[trial_id]["profiles"]["BASE"]["splits"]["VALIDATION"][
                        "expectancy_bps"
                    ]
                )
            ),
            trial_id,
        )
    )
    selected = eligible[:selection_limit]
    blocked_count = sum(not trial.screening_eligible for trial in registered_trials)
    screening_eligible_count = len(registered_trials) - blocked_count
    return {
        "schema_version": 1,
        "status": "EXECUTED" if not failed_ids else "INCOMPLETE_TRIAL_FAILURES",
        "generated_ts_utc": generated_ts_utc,
        "trial_manifest_sha256": trial_manifest_sha256,
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "registered_trial_count": len(registered_trials),
        "screening_eligible_count": screening_eligible_count,
        "blocked_trial_count": blocked_count,
        "executed_trial_count": sum(
            result.status is ScreeningStatus.EXECUTED for result in results
        ),
        "failed_trial_count": sum(result.status is ScreeningStatus.FAILED for result in results),
        "planned_independent_account_count": len(registered_trials) * len(SCREENING_PROFILES),
        "executed_independent_account_count": sum(
            len(result.accounts) for result in results if result.status is ScreeningStatus.EXECUTED
        ),
        "failed_preserved_independent_account_count": sum(
            len(result.accounts) for result in results if result.status is ScreeningStatus.FAILED
        ),
        "observed_independent_account_count": sum(len(result.accounts) for result in results),
        "blocked_independent_account_count": blocked_count * len(SCREENING_PROFILES),
        "starting_equity_per_account_usdt": str(STARTING_EQUITY_USDT),
        "selection_basis": "TRAIN_AND_VALIDATION_ONLY",
        "final_oos_status": "SEALED_NOT_USED_FOR_SELECTION",
        "final_oos_minimums_preregistered": MINIMUM_FINAL_OOS_BY_HORIZON,
        "global_multiple_testing": {
            "basis": "VALIDATION_WALK_FORWARD_FOLDS",
            **pbo,
        },
        "results": [
            {"trial": asdict(expected[trial_id]), "statistics": statistics[trial_id]}
            for trial_id in sorted(expected, key=lambda key: expected[key].trial_number)
        ],
        "event_replay_selected": selected,
        "selection_count": len(selected),
        "selection_limit": selection_limit,
        "active_count": 0,
        "live_shadow_count": 0,
        "profitability_claim": "NOT_PROVEN_UNTIL_LATER_GATES",
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
    }
