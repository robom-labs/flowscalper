# 저장 공개시장 이벤트를 실제 PAPER 전략·체결·TP/SL 경로로 재생한다.

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from itertools import chain
from pathlib import Path

from backend.app.analytics.reports import TradeAnalytics
from backend.app.build_identity import STRATEGY_VERSION, git_commit
from backend.app.domain.models import MarketDataState, MarketEvent, RuntimeMode, Side, Venue
from backend.app.features import FeatureSnapshot
from backend.app.market_data import Candle
from backend.app.regime import Regime
from backend.app.runtime import PaperRuntime
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.registry import (
    StrategyChangeSource,
    StrategyMode,
    StrategyRegistry,
)
from backend.app.strategies.runtime_evaluator import EvaluatedSignal, StrategySignalEvaluator
from scripts.research_intraday_candidates import _event_rows
from scripts.research_strategy_revision import (
    DEFAULT_OOS_RUNS,
    DEFAULT_RESEARCH_TRAIN_RUNS,
    DEFAULT_VALIDATION_RUNS,
)

DEFAULT_STRATEGY_ID = "VWAP_EXHAUSTION_REVERSION_V1"
SIGNAL_GATE_NONE = "NONE"
SIGNAL_GATE_TP1_FEASIBILITY = "TP1_FEASIBILITY_CONFLUENCE_V1"
SIGNAL_GATES = (SIGNAL_GATE_NONE, SIGNAL_GATE_TP1_FEASIBILITY)
TP1_FEASIBILITY_LOOKBACK_MS = 120_000
_CANDIDATE_EVENTS = {
    "MAIN_CANDIDATE_SELECTED",
    "LEAGUE_CANDIDATE_ARMED",
    "SHADOW_CANDIDATE_ARMED",
}


def tp1_feasibility_gate_rejections(
    signal: EvaluatedSignal,
    snapshot: FeatureSnapshot,
    *,
    recent_range_bps: float | None,
    take_profit_1_r: Decimal,
) -> tuple[str, ...]:
    """사전등록된 비용·TP1·방향합의 입력이 부족하면 기존 신호를 거부한다."""

    decision = signal.decision
    if decision.status is not CandidateStatus.QUALIFIED:
        return ()
    if (
        decision.planned_entry is None
        or decision.initial_stop is None
        or decision.planned_entry <= 0
        or take_profit_1_r <= 0
    ):
        return ("TP1_GATE_PLAN_MISSING_OR_INVALID",)
    numeric_values = [
        snapshot.mid,
        snapshot.microprice_minus_mid_bps,
        snapshot.multi_level_microprice_10_minus_mid_bps,
        snapshot.imbalance_top5,
        snapshot.imbalance_top10,
        snapshot.trade_imbalance_1s,
        snapshot.trade_imbalance_3s,
        float(decision.expected_cost_bps),
        float(take_profit_1_r),
    ]
    if recent_range_bps is not None:
        numeric_values.append(recent_range_bps)
    if not snapshot.data_healthy or snapshot.mid <= 0 or not all(
        math.isfinite(value) for value in numeric_values
    ):
        return ("TP1_GATE_INPUT_UNHEALTHY_OR_NONFINITE",)

    entry = decision.planned_entry
    risk_bps = float(abs(entry - decision.initial_stop) / entry * Decimal(10_000))
    tp1_required_bps = risk_bps * float(take_profit_1_r)
    minimum_range_bps = max(
        tp1_required_bps,
        float(decision.expected_cost_bps) * 2,
    )
    rejections: list[str] = []
    if recent_range_bps is None:
        rejections.append("TP1_GATE_120S_HISTORY_INSUFFICIENT")
    elif recent_range_bps < minimum_range_bps:
        rejections.append("TP1_GATE_RECENT_RANGE_TOO_SMALL")

    direction = 1 if decision.side is Side.LONG else -1
    votes = (
        snapshot.microprice_minus_mid_bps * direction > 0,
        snapshot.multi_level_microprice_10_minus_mid_bps * direction > 0,
        snapshot.imbalance_top5 * direction >= 0.05,
        snapshot.imbalance_top10 * direction >= 0.03,
        snapshot.trade_imbalance_1s * direction >= 0.10,
        snapshot.trade_imbalance_3s * direction >= 0.05,
    )
    if sum(votes) < 4:
        rejections.append("TP1_GATE_DIRECTIONAL_CONFLUENCE_BELOW_4_OF_6")
    return tuple(rejections)


@dataclass(slots=True)
class ResearchSignalGateEvaluator(StrategySignalEvaluator):
    """기존 전략 신호를 만들지 않고 사전등록 연구필터로만 거부한다."""

    target_strategy_id: str = DEFAULT_STRATEGY_ID
    signal_gate: str = SIGNAL_GATE_NONE
    baseline_qualified_count: int = 0
    accepted_qualified_count: int = 0
    rejected_qualified_count: int = 0
    rejection_counts: Counter[str] = field(default_factory=Counter)
    _mid_history: dict[str, deque[tuple[int, float]]] = field(
        default_factory=lambda: defaultdict(deque),
        repr=False,
    )

    def __post_init__(self) -> None:
        StrategySignalEvaluator.__init__(self)
        if self.signal_gate not in SIGNAL_GATES:
            raise ValueError(f"알 수 없는 연구 신호 gate입니다: {self.signal_gate}")

    def evaluate(
        self,
        registry: StrategyRegistry,
        snapshot: FeatureSnapshot,
        regime: Regime,
        *,
        tick_size: Decimal = Decimal("0.00000001"),
        hourly_candles: tuple[Candle, ...] = (),
    ) -> tuple[EvaluatedSignal, ...]:
        signals = StrategySignalEvaluator.evaluate(
            self,
            registry,
            snapshot,
            regime,
            tick_size=tick_size,
            hourly_candles=hourly_candles,
        )
        recent_range_bps = self._recent_range_bps(snapshot)
        filtered: list[EvaluatedSignal] = []
        for signal in signals:
            if (
                signal.decision.strategy_id != self.target_strategy_id
                or signal.decision.status is not CandidateStatus.QUALIFIED
            ):
                filtered.append(signal)
                continue
            self.baseline_qualified_count += 1
            rejections: tuple[str, ...] = ()
            if self.signal_gate == SIGNAL_GATE_TP1_FEASIBILITY:
                descriptor = registry.descriptor(self.target_strategy_id)
                rejections = tp1_feasibility_gate_rejections(
                    signal,
                    snapshot,
                    recent_range_bps=recent_range_bps,
                    take_profit_1_r=descriptor.take_profit_1_r,
                )
            if not rejections:
                self.accepted_qualified_count += 1
                filtered.append(signal)
                continue
            self.rejected_qualified_count += 1
            self.rejection_counts.update(rejections)
            filtered.append(
                replace(
                    signal,
                    decision=replace(
                        signal.decision,
                        status=CandidateStatus.REJECTED,
                        reason_codes=(),
                        rejection_codes=tuple(
                            dict.fromkeys((*signal.decision.rejection_codes, *rejections))
                        ),
                    ),
                )
            )
        self._append_mid(snapshot)
        return tuple(filtered)

    def diagnostics(self) -> dict[str, object]:
        return {
            "signal_gate": self.signal_gate,
            "baseline_qualified_count": self.baseline_qualified_count,
            "accepted_qualified_count": self.accepted_qualified_count,
            "rejected_qualified_count": self.rejected_qualified_count,
            "rejection_counts": dict(sorted(self.rejection_counts.items())),
            "can_create_signals": False,
        }

    def _recent_range_bps(self, snapshot: FeatureSnapshot) -> float | None:
        history = self._mid_history[snapshot.symbol]
        reference_ts_ms = max(
            snapshot.ts_ms,
            max((timestamp for timestamp, _ in history), default=snapshot.ts_ms),
        )
        cutoff = reference_ts_ms - TP1_FEASIBILITY_LOOKBACK_MS
        values = [mid for timestamp, mid in history if timestamp >= cutoff]
        values.append(snapshot.mid)
        if len(values) < 2 or snapshot.mid <= 0:
            return None
        return (max(values) - min(values)) / snapshot.mid * 10_000

    def _append_mid(self, snapshot: FeatureSnapshot) -> None:
        history = self._mid_history[snapshot.symbol]
        history.append((snapshot.ts_ms, snapshot.mid))
        reference_ts_ms = max(timestamp for timestamp, _ in history)
        cutoff = reference_ts_ms - TP1_FEASIBILITY_LOOKBACK_MS
        self._mid_history[snapshot.symbol] = deque(
            (timestamp, mid) for timestamp, mid in history if timestamp >= cutoff
        )


def _configure_target_strategy(runtime: PaperRuntime, strategy_id: str) -> None:
    if strategy_id not in runtime.strategy_registry.strategy_ids:
        raise ValueError(f"알 수 없는 전략입니다: {strategy_id}")
    for current_id in runtime.strategy_registry.strategy_ids:
        runtime.strategy_registry.configure(
            current_id,
            mode=(
                StrategyMode.SHADOW
                if current_id == strategy_id
                else StrategyMode.OFF
            ),
            long_enabled=True,
            short_enabled=True,
            source=StrategyChangeSource.RECOVERY,
            reason="RESEARCH_SINGLE_STRATEGY_REPLAY",
        )


def _target_trade_rows(
    runtime: PaperRuntime,
    strategy_id: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for profile in ("BASE", "STRESS"):
        account = runtime.paper_portfolio.shadows[f"{strategy_id}:{profile}"]
        rows.extend(runtime._paper_trade_row(trade) for trade in account.completed_trades)
    return rows


def _open_state(runtime: PaperRuntime, strategy_id: str) -> dict[str, object]:
    positions: list[dict[str, object]] = []
    pending_entries = 0
    for profile in ("BASE", "STRESS"):
        account = runtime.paper_portfolio.shadows[f"{strategy_id}:{profile}"]
        pending_entries += len(account.pending_entries)
        positions.extend(
            {
                "profile": profile,
                "trade_id": managed.protected.trade_id,
                "candidate_id": managed.plan.candidate_id,
                "symbol": managed.plan.symbol,
                "side": managed.plan.direction.value,
                "opened_ts_ms": managed.protected.opened_ts_ms,
                "entry_price": str(managed.protected.entry_fill.average_price),
                "initial_stop": str(managed.protected.initial_stop),
                "take_profit_1": str(managed.plan.take_profit_targets[0].price),
                "take_profit_2": (
                    str(managed.plan.take_profit_targets[1].price)
                    if len(managed.plan.take_profit_targets) > 1
                    else None
                ),
            }
            for managed in account.positions.values()
        )
    return {
        "open_position_count": len(positions),
        "pending_entry_count": pending_entries,
        "censored_count": len(positions) + pending_entries,
        "positions": positions,
    }


def replay_archive_run(
    run_id: str,
    run_dir: Path,
    *,
    strategy_id: str = DEFAULT_STRATEGY_ID,
    signal_gate: str = SIGNAL_GATE_NONE,
    maximum_events: int | None = None,
) -> dict[str, object]:
    """한 저장 Run을 신규 무원장 PAPER 런타임에서 수신순으로 재생한다."""

    if maximum_events is not None and maximum_events <= 0:
        raise ValueError("최대 이벤트 수는 양수여야 합니다.")
    if strategy_id not in StrategyRegistry().strategy_ids:
        raise ValueError(f"알 수 없는 전략입니다: {strategy_id}")
    if signal_gate not in SIGNAL_GATES:
        raise ValueError(f"알 수 없는 연구 신호 gate입니다: {signal_gate}")
    event_iterator = iter(_event_rows(run_dir, maximum_events=maximum_events))
    first_payload = next(event_iterator, None)
    runtime_run_id = (
        str(first_payload.get("run_id", run_id))
        if first_payload is not None
        else run_id
    )
    gated_evaluator = ResearchSignalGateEvaluator(
        target_strategy_id=strategy_id,
        signal_gate=signal_gate,
    )
    runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id=runtime_run_id,
        venue=Venue.BINANCE_USDM,
        strategy_evaluator=gated_evaluator,
    )
    runtime.market_data_state = MarketDataState.LIVE
    runtime.paused = False
    runtime.runtime_health_flags = [
        "STORED_PUBLIC_MARKET_REPLAY",
        "NO_AUTH_HEADERS",
        "RESEARCH_NO_PERSISTENCE",
    ]
    _configure_target_strategy(runtime, strategy_id)

    event_count = 0
    first_receive_ts_ms: int | None = None
    last_receive_ts_ms: int | None = None
    event_types: Counter[str] = Counter()
    payloads = chain((first_payload,), event_iterator) if first_payload is not None else ()
    for payload in payloads:
        event = MarketEvent.model_validate(payload)
        lag_ms = event.quality.lag_ms or 0.0
        receive_ts_ms = int(
            payload.get("receive_ts_ms")
            or event.venue_ts_ms + max(0, round(lag_ms))
        )
        first_receive_ts_ms = (
            receive_ts_ms
            if first_receive_ts_ms is None
            else min(first_receive_ts_ms, receive_ts_ms)
        )
        last_receive_ts_ms = (
            receive_ts_ms
            if last_receive_ts_ms is None
            else max(last_receive_ts_ms, receive_ts_ms)
        )
        event_count += 1
        event_types[event.event_type] += 1
        runtime.ingest_live_event(event)

    trades = _target_trade_rows(runtime, strategy_id)
    reports = TradeAnalytics().strategy_reports(trades, strategy_ids=(strategy_id,))
    audit_events = [
        row
        for row in runtime.paper_portfolio.audit_events
        if row.get("strategy_id") == strategy_id
    ]
    candidate_ids = {
        str(row["candidate_id"])
        for row in audit_events
        if row.get("event") in _CANDIDATE_EVENTS and row.get("candidate_id")
    }
    status = runtime.status()
    target_setting = runtime.strategy_registry.setting(strategy_id)
    enabled_other_strategies = [
        current_id
        for current_id in runtime.strategy_registry.strategy_ids
        if current_id != strategy_id
        and runtime.strategy_registry.setting(current_id).mode is not StrategyMode.OFF
    ]
    return {
        "run_id": run_id,
        "runtime_run_id": runtime_run_id,
        "strategy_id": strategy_id,
        "signal_gate": signal_gate,
        "signal_gate_diagnostics": gated_evaluator.diagnostics(),
        "strategy_version": STRATEGY_VERSION,
        "event_order": [
            "receive_ts_ms",
            "receive_monotonic_ns",
            "venue_ts_ms",
            "symbol",
            "payload_json",
        ],
        "event_count": event_count,
        "first_receive_ts_ms": first_receive_ts_ms,
        "last_receive_ts_ms": last_receive_ts_ms,
        "event_type_counts": dict(sorted(event_types.items())),
        "strategy_mode": target_setting.mode.value,
        "enabled_other_strategies": enabled_other_strategies,
        "strategy_evaluation_count": runtime.strategy_evaluation_count,
        "qualified_signal_count": runtime.qualified_signal_count,
        "candidate_plan_count": len(candidate_ids),
        "trade_count": len(trades),
        "trade_rows": trades,
        "reports": reports,
        "open_state": _open_state(runtime, strategy_id),
        "real_orders_enabled": status.real_orders_enabled,
        "auth_required": status.auth_required,
        "ledger_attached": runtime.ledger is not None,
    }


def _summary(
    runs: Sequence[Mapping[str, object]],
    *,
    strategy_id: str,
) -> dict[str, object]:
    trades: list[dict[str, object]] = []
    censored_count = 0
    for run in runs:
        run_trades = run.get("trade_rows")
        if isinstance(run_trades, Sequence) and not isinstance(run_trades, str | bytes):
            trades.extend(dict(row) for row in run_trades if isinstance(row, Mapping))
        open_state = run.get("open_state")
        if isinstance(open_state, Mapping):
            censored_count += int(str(open_state.get("censored_count", 0)))
    reports = TradeAnalytics().strategy_reports(trades, strategy_ids=(strategy_id,))
    report_by_profile = {str(row["profile"]): row for row in reports}
    opportunities = {
        (
            str(row["run_id"]),
            str(row["signal_event_id"]),
            str(row["strategy_id"]),
            str(row["side"]),
        )
        for row in trades
    }
    exit_reasons: dict[str, Counter[str]] = {
        profile: Counter(
            str(row["exit_reason"])
            for row in trades
            if str(row["profile"]) == profile
        )
        for profile in ("BASE", "STRESS")
    }
    base = report_by_profile["BASE"]
    stress = report_by_profile["STRESS"]
    observed_70_gate = (
        int(str(base["sample_size"])) >= 30
        and int(str(stress["sample_size"])) >= 30
        and base.get("win_rate") is not None
        and float(str(base["win_rate"])) >= 0.70
        and stress.get("win_rate") is not None
        and float(str(stress["win_rate"])) >= 0.70
    )
    gate_rejection_counts: Counter[str] = Counter()
    gate_baseline_qualified_count = 0
    gate_accepted_qualified_count = 0
    gate_rejected_qualified_count = 0
    for run in runs:
        diagnostics = run.get("signal_gate_diagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        gate_baseline_qualified_count += int(
            str(diagnostics.get("baseline_qualified_count", 0))
        )
        gate_accepted_qualified_count += int(
            str(diagnostics.get("accepted_qualified_count", 0))
        )
        gate_rejected_qualified_count += int(
            str(diagnostics.get("rejected_qualified_count", 0))
        )
        rejection_counts = diagnostics.get("rejection_counts")
        if isinstance(rejection_counts, Mapping):
            gate_rejection_counts.update(
                {
                    str(code): int(str(count))
                    for code, count in rejection_counts.items()
                }
            )
    return {
        "run_count": len(runs),
        "event_count": sum(int(str(run.get("event_count", 0))) for run in runs),
        "unique_market_opportunity_count": len(opportunities),
        "trade_row_count": len(trades),
        "reports": reports,
        "exit_reason_counts": {
            profile: dict(sorted(counter.items()))
            for profile, counter in exit_reasons.items()
        },
        "holding_le_15_seconds": sum(
            int(str(row["holding_ms"])) <= 15_000 for row in trades
        ),
        "tp1_hit_count": sum(row.get("tp1_hit_ts_ms") is not None for row in trades),
        "tp2_hit_count": sum(row.get("tp2_hit_ts_ms") is not None for row in trades),
        "stop_exit_count": sum(row.get("exit_reason") == "STOP" for row in trades),
        "censored_count": censored_count,
        "signal_gate_diagnostics": {
            "baseline_qualified_count": gate_baseline_qualified_count,
            "accepted_qualified_count": gate_accepted_qualified_count,
            "rejected_qualified_count": gate_rejected_qualified_count,
            "rejection_counts": dict(sorted(gate_rejection_counts.items())),
            "can_create_signals": False,
        },
        "observed_70_percent_gate_passed": observed_70_gate,
        "ranking_eligible": observed_70_gate,
        "profitability_status": "NOT_PROVEN",
        "profitability_claim_allowed": False,
        "trade_rows": trades,
    }


def build_result(
    archive: Path,
    *,
    strategy_id: str,
    run_ids: Sequence[str],
    signal_gate: str = SIGNAL_GATE_NONE,
    maximum_events: int | None = None,
) -> dict[str, object]:
    runs = [
        replay_archive_run(
            run_id,
            archive / f"run={run_id}",
            strategy_id=strategy_id,
            signal_gate=signal_gate,
            maximum_events=maximum_events,
        )
        for run_id in run_ids
    ]
    run_by_id = {str(run["run_id"]): run for run in runs}
    split_ids = {
        "train": DEFAULT_RESEARCH_TRAIN_RUNS,
        "validation": DEFAULT_VALIDATION_RUNS,
        "oos": DEFAULT_OOS_RUNS,
    }
    split_summaries = {
        split: _summary(
            [run_by_id[run_id] for run_id in ids if run_id in run_by_id],
            strategy_id=strategy_id,
        )
        for split, ids in split_ids.items()
    }
    return {
        "schema_version": 1,
        "status": "RESEARCH_REPLAY_COMPLETE",
        "method": "ACTUAL_PAPER_RUNTIME_ENTRY_FILL_TP_SL_MANAGEMENT_PATH",
        "git_commit": git_commit(),
        "strategy_id": strategy_id,
        "signal_gate": signal_gate,
        "strategy_version": STRATEGY_VERSION,
        "event_order": "OBSERVED_RECEIVE_ORDER_ADR_080",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "runtime_ai_order_decision": False,
        "runs": runs,
        "splits": split_summaries,
        "overall": _summary(runs, strategy_id=strategy_id),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("data/market-parquet-v6/venue=BINANCE_USDM"),
    )
    parser.add_argument("--strategy-id", default=DEFAULT_STRATEGY_ID)
    parser.add_argument("--signal-gate", choices=SIGNAL_GATES, default=SIGNAL_GATE_NONE)
    parser.add_argument("--run-id", action="append")
    parser.add_argument("--maximum-events", type=int)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_ids = tuple(
        args.run_id
        or (
            *DEFAULT_RESEARCH_TRAIN_RUNS,
            *DEFAULT_VALIDATION_RUNS,
            *DEFAULT_OOS_RUNS,
        )
    )
    result = build_result(
        args.archive,
        strategy_id=str(args.strategy_id),
        run_ids=run_ids,
        signal_gate=str(args.signal_gate),
        maximum_events=args.maximum_events,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
