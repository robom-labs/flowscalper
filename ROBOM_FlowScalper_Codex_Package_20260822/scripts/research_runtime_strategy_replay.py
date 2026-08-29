# 저장 공개시장 이벤트를 실제 PAPER 전략·체결·TP/SL 경로로 재생한다.

from __future__ import annotations

import argparse
import hashlib
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
STRATEGY_LOGIC_CURRENT = "CURRENT_FULL_CONFLUENCE"
STRATEGY_LOGIC_WAVE102 = "WAVE102_PARTIAL_CONFIRMATION_BASELINE"
STRATEGY_LOGICS = (STRATEGY_LOGIC_CURRENT, STRATEGY_LOGIC_WAVE102)
TP1_FEASIBILITY_LOOKBACK_MS = 120_000
DEFAULT_DATASET_MANIFEST = Path("evidence/STRATEGY_100_DATASET_MANIFEST.json")
_CANDIDATE_EVENTS = {
    "MAIN_CANDIDATE_SELECTED",
    "LEAGUE_CANDIDATE_ARMED",
    "SHADOW_CANDIDATE_ARMED",
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def frozen_dataset_reference(
    path: Path,
    run_ids: Sequence[str],
) -> dict[str, object]:
    """동결 manifest 자체와 선택 Run의 checksum 계약을 검증해 결과에 고정한다."""

    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("동결 dataset manifest는 JSON object여야 합니다.")
    material = dict(payload)
    claimed_manifest_sha256 = material.pop("manifest_sha256", None)
    actual_manifest_sha256 = hashlib.sha256(_canonical_json(material).encode()).hexdigest()
    if claimed_manifest_sha256 != actual_manifest_sha256:
        raise ValueError("동결 dataset manifest 내부 checksum이 다릅니다.")
    if (
        payload.get("status") != "FROZEN_HISTORICAL_FORWARD_PENDING"
        or payload.get("paper_only") is not True
        or payload.get("real_orders_enabled") is not False
        or payload.get("private_api_enabled") is not False
        or payload.get("runtime_ai_enabled") is not False
    ):
        raise ValueError("동결 dataset manifest의 PAPER·미래표본 경계가 잘못됐습니다.")
    rows = payload.get("runs")
    if not isinstance(rows, list) or not rows:
        raise ValueError("동결 dataset manifest의 Run 목록이 없습니다.")
    run_by_id: dict[str, Mapping[str, object]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("run_id"), str):
            raise ValueError("동결 dataset manifest의 Run 행이 잘못됐습니다.")
        run_id = str(row["run_id"])
        checksum = row.get("checksum")
        if (
            run_id in run_by_id
            or not isinstance(checksum, str)
            or len(checksum) != 64
        ):
            raise ValueError("동결 dataset manifest의 Run ID 또는 checksum이 잘못됐습니다.")
        run_by_id[run_id] = row
    missing = [run_id for run_id in run_ids if run_id not in run_by_id]
    if missing:
        raise ValueError(f"동결 dataset manifest에 선택 Run이 없습니다: {missing}")
    selected = [dict(run_by_id[run_id]) for run_id in run_ids]
    archive_verification = payload.get("archive_verification")
    if (
        not isinstance(archive_verification, Mapping)
        or archive_verification.get("status") != "PASS"
    ):
        raise ValueError("동결 dataset manifest의 archive 검증상태가 PASS가 아닙니다.")
    return {
        "path": path.as_posix(),
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_sha256": actual_manifest_sha256,
        "manifest_status": payload["status"],
        "historical_archive_verification": dict(archive_verification),
        "current_archive_byte_reverification": "NOT_RUN",
        "selected_run_count": len(selected),
        "selected_event_count": sum(int(str(row["event_count"])) for row in selected),
        "selected_runs": selected,
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
    strategy_logic: str = STRATEGY_LOGIC_CURRENT
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
        if self.strategy_logic not in STRATEGY_LOGICS:
            raise ValueError(f"알 수 없는 연구 전략 로직입니다: {self.strategy_logic}")

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
            "strategy_logic": self.strategy_logic,
            "baseline_qualified_count": self.baseline_qualified_count,
            "accepted_qualified_count": self.accepted_qualified_count,
            "rejected_qualified_count": self.rejected_qualified_count,
            "rejection_counts": dict(sorted(self.rejection_counts.items())),
            "can_create_signals": False,
            "signal_gate_can_create_signals": False,
        }

    def _vwap_reentry_confirmation_aligned(
        self,
        *,
        full_confluence_ready: bool,
        data_healthy: bool,
        regime: Regime,
        structure_reentered: bool,
        microprice_alignment: bool,
    ) -> bool:
        """동일 현행 런타임에서 Wave102의 부분 확인 계약만 연구 기준선으로 재현한다."""

        if self.strategy_logic == STRATEGY_LOGIC_WAVE102:
            return (
                data_healthy
                and regime is Regime.RANGE
                and structure_reentered
                and microprice_alignment
            )
        return StrategySignalEvaluator._vwap_reentry_confirmation_aligned(
            self,
            full_confluence_ready=full_confluence_ready,
            data_healthy=data_healthy,
            regime=regime,
            structure_reentered=structure_reentered,
            microprice_alignment=microprice_alignment,
        )

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


def _validated_strategy_ids(strategy_ids: Sequence[str]) -> tuple[str, ...]:
    available = StrategyRegistry().strategy_ids
    selected = tuple(strategy_ids)
    if not selected:
        raise ValueError("연구할 전략이 없습니다.")
    if len(selected) != len(set(selected)):
        raise ValueError("연구 전략 ID가 중복됐습니다.")
    unknown = [strategy_id for strategy_id in selected if strategy_id not in available]
    if unknown:
        raise ValueError(f"알 수 없는 전략입니다: {unknown}")
    return selected


def _configure_research_strategies(
    runtime: PaperRuntime,
    strategy_ids: Sequence[str],
) -> None:
    selected = set(_validated_strategy_ids(strategy_ids))
    reason = (
        "RESEARCH_ALL_STRATEGY_LEAGUE_REPLAY"
        if len(selected) == len(runtime.strategy_registry.strategy_ids)
        else "RESEARCH_SELECTED_STRATEGY_REPLAY"
    )
    for current_id in runtime.strategy_registry.strategy_ids:
        runtime.strategy_registry.configure(
            current_id,
            mode=(
                StrategyMode.SHADOW
                if current_id in selected
                else StrategyMode.OFF
            ),
            long_enabled=True,
            short_enabled=True,
            source=StrategyChangeSource.RECOVERY,
            reason=reason,
        )


def _strategy_trade_rows(
    runtime: PaperRuntime,
    strategy_ids: Sequence[str],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for strategy_id in strategy_ids:
        for profile in ("BASE", "STRESS"):
            account = runtime.paper_portfolio.shadows[f"{strategy_id}:{profile}"]
            rows.extend(runtime._paper_trade_row(trade) for trade in account.completed_trades)
    return rows


def _open_state(
    runtime: PaperRuntime,
    strategy_ids: Sequence[str],
) -> dict[str, object]:
    positions: list[dict[str, object]] = []
    pending_entries = 0
    pending_entry_counts: dict[str, int] = {}
    for strategy_id in strategy_ids:
        strategy_pending_entries = 0
        for profile in ("BASE", "STRESS"):
            account = runtime.paper_portfolio.shadows[f"{strategy_id}:{profile}"]
            profile_pending_entries = len(account.pending_entries)
            pending_entries += profile_pending_entries
            strategy_pending_entries += profile_pending_entries
            positions.extend(
                {
                    "strategy_id": strategy_id,
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
        pending_entry_counts[strategy_id] = strategy_pending_entries
    return {
        "open_position_count": len(positions),
        "pending_entry_count": pending_entries,
        "pending_entry_counts": pending_entry_counts,
        "censored_count": len(positions) + pending_entries,
        "positions": positions,
    }


def _replay_archive_run_for_strategies(
    run_id: str,
    run_dir: Path,
    *,
    strategy_ids: Sequence[str],
    signal_gate_target_strategy_id: str,
    signal_gate: str = SIGNAL_GATE_NONE,
    strategy_logic: str = STRATEGY_LOGIC_CURRENT,
    maximum_events: int | None = None,
) -> dict[str, object]:
    """선택 전략을 한 신규 무원장 PAPER 런타임에서 동시에 수신순 재생한다."""

    if maximum_events is not None and maximum_events <= 0:
        raise ValueError("최대 이벤트 수는 양수여야 합니다.")
    selected_strategy_ids = _validated_strategy_ids(strategy_ids)
    if signal_gate_target_strategy_id not in selected_strategy_ids:
        raise ValueError("연구 신호 gate 대상 전략이 선택 전략에 없습니다.")
    if signal_gate not in SIGNAL_GATES:
        raise ValueError(f"알 수 없는 연구 신호 gate입니다: {signal_gate}")
    if strategy_logic not in STRATEGY_LOGICS:
        raise ValueError(f"알 수 없는 연구 전략 로직입니다: {strategy_logic}")
    if (
        strategy_logic == STRATEGY_LOGIC_WAVE102
        and DEFAULT_STRATEGY_ID not in selected_strategy_ids
    ):
        raise ValueError("Wave102 기준선은 VWAP 전략을 포함해야 합니다.")
    if (
        signal_gate != SIGNAL_GATE_NONE
        and signal_gate_target_strategy_id != DEFAULT_STRATEGY_ID
    ):
        raise ValueError("사전등록 TP1 신호 gate 대상은 VWAP 전략이어야 합니다.")
    event_iterator = iter(_event_rows(run_dir, maximum_events=maximum_events))
    first_payload = next(event_iterator, None)
    runtime_run_id = (
        str(first_payload.get("run_id", run_id))
        if first_payload is not None
        else run_id
    )
    gated_evaluator = ResearchSignalGateEvaluator(
        target_strategy_id=signal_gate_target_strategy_id,
        signal_gate=signal_gate,
        strategy_logic=strategy_logic,
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
    source_strategy_settings = {
        strategy_id: {
            "mode": runtime.strategy_registry.setting(strategy_id).mode.value,
            "lifecycle": runtime.strategy_registry.setting(strategy_id).lifecycle.value,
        }
        for strategy_id in selected_strategy_ids
    }
    _configure_research_strategies(runtime, selected_strategy_ids)

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

    trades = _strategy_trade_rows(runtime, selected_strategy_ids)
    reports = TradeAnalytics().strategy_reports(
        trades,
        strategy_ids=selected_strategy_ids,
    )
    audit_events = [
        row
        for row in runtime.paper_portfolio.audit_events
        if row.get("strategy_id") in selected_strategy_ids
    ]
    candidate_ids_by_strategy = {
        strategy_id: {
            str(row["candidate_id"])
            for row in audit_events
            if row.get("strategy_id") == strategy_id
            and row.get("event") in _CANDIDATE_EVENTS
            and row.get("candidate_id")
        }
        for strategy_id in selected_strategy_ids
    }
    status = runtime.status()
    target_setting = runtime.strategy_registry.setting(signal_gate_target_strategy_id)
    enabled_other_strategies = [
        current_id
        for current_id in runtime.strategy_registry.strategy_ids
        if current_id != signal_gate_target_strategy_id
        and runtime.strategy_registry.setting(current_id).mode is not StrategyMode.OFF
    ]
    strategy_modes = {
        strategy_id: runtime.strategy_registry.setting(strategy_id).mode.value
        for strategy_id in selected_strategy_ids
    }
    return {
        "run_id": run_id,
        "runtime_run_id": runtime_run_id,
        "research_scope": (
            "ALL_REGISTERED_STRATEGIES"
            if len(selected_strategy_ids) == len(runtime.strategy_registry.strategy_ids)
            else "SELECTED_STRATEGIES"
        ),
        "strategy_id": signal_gate_target_strategy_id,
        "strategy_ids": list(selected_strategy_ids),
        "strategy_count": len(selected_strategy_ids),
        "strategy_account_count": len(selected_strategy_ids) * 2,
        "signal_gate_target_strategy_id": signal_gate_target_strategy_id,
        "signal_gate": signal_gate,
        "strategy_logic": strategy_logic,
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
        "strategy_modes": strategy_modes,
        "source_strategy_settings": source_strategy_settings,
        "research_shadow_reactivation_is_ephemeral": True,
        "enabled_other_strategies": enabled_other_strategies,
        "strategy_evaluation_count": runtime.strategy_evaluation_count,
        "qualified_signal_count": runtime.qualified_signal_count,
        "candidate_plan_count": sum(map(len, candidate_ids_by_strategy.values())),
        "candidate_plan_counts": {
            strategy_id: len(candidate_ids)
            for strategy_id, candidate_ids in candidate_ids_by_strategy.items()
        },
        "trade_count": len(trades),
        "trade_rows": trades,
        "reports": reports,
        "open_state": _open_state(runtime, selected_strategy_ids),
        "real_orders_enabled": status.real_orders_enabled,
        "auth_required": status.auth_required,
        "ledger_attached": runtime.ledger is not None,
    }


def replay_archive_run(
    run_id: str,
    run_dir: Path,
    *,
    strategy_id: str = DEFAULT_STRATEGY_ID,
    signal_gate: str = SIGNAL_GATE_NONE,
    strategy_logic: str = STRATEGY_LOGIC_CURRENT,
    maximum_events: int | None = None,
) -> dict[str, object]:
    """한 전략을 신규 무원장 PAPER 런타임에서 수신순으로 재생한다."""

    return _replay_archive_run_for_strategies(
        run_id,
        run_dir,
        strategy_ids=(strategy_id,),
        signal_gate_target_strategy_id=strategy_id,
        signal_gate=signal_gate,
        strategy_logic=strategy_logic,
        maximum_events=maximum_events,
    )


def replay_strategy_league_archive_run(
    run_id: str,
    run_dir: Path,
    *,
    signal_gate: str = SIGNAL_GATE_NONE,
    strategy_logic: str = STRATEGY_LOGIC_CURRENT,
    maximum_events: int | None = None,
) -> dict[str, object]:
    """등록 전략 전체를 22개 독립계좌의 한 무원장 PAPER 런타임에서 재생한다."""

    return _replay_archive_run_for_strategies(
        run_id,
        run_dir,
        strategy_ids=StrategyRegistry().strategy_ids,
        signal_gate_target_strategy_id=DEFAULT_STRATEGY_ID,
        signal_gate=signal_gate,
        strategy_logic=strategy_logic,
        maximum_events=maximum_events,
    )


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
            trades.extend(
                dict(row)
                for row in run_trades
                if isinstance(row, Mapping)
                and str(row.get("strategy_id")) == strategy_id
            )
        open_state = run.get("open_state")
        if isinstance(open_state, Mapping):
            positions = open_state.get("positions")
            if isinstance(positions, Sequence) and not isinstance(positions, str | bytes):
                censored_count += sum(
                    isinstance(position, Mapping)
                    and str(position.get("strategy_id")) == strategy_id
                    for position in positions
                )
            pending_counts = open_state.get("pending_entry_counts")
            if isinstance(pending_counts, Mapping):
                censored_count += int(str(pending_counts.get(strategy_id, 0)))
            elif run.get("strategy_ids") == [strategy_id]:
                censored_count += int(str(open_state.get("pending_entry_count", 0)))
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
    minimum_opportunities = 30
    observed_70_gate = (
        len(opportunities) >= minimum_opportunities
        and int(str(base["sample_size"])) >= minimum_opportunities
        and int(str(stress["sample_size"])) >= minimum_opportunities
        and base.get("win_rate") is not None
        and float(str(base["win_rate"])) >= 0.70
        and stress.get("win_rate") is not None
        and float(str(stress["win_rate"])) >= 0.70
    )
    cost_performance_gate = all(
        Decimal(str(report.get("expectancy_usdt", "0") or "0")) > 0
        and Decimal(str(report.get("net_pnl", "0") or "0")) > 0
        and (
            (
                report.get("profit_factor") is not None
                and Decimal(str(report["profit_factor"])) > 1
            )
            or (
                int(str(report.get("losses", 0))) == 0
                and int(str(report.get("wins", 0))) > 0
            )
        )
        for report in (base, stress)
    )
    ranking_blockers: list[str] = []
    if len(opportunities) < minimum_opportunities:
        ranking_blockers.append("UNIQUE_MARKET_OPPORTUNITIES_BELOW_30")
    for profile, report in (("BASE", base), ("STRESS", stress)):
        if int(str(report["sample_size"])) < minimum_opportunities:
            ranking_blockers.append(f"{profile}_SAMPLE_BELOW_30")
        if report.get("win_rate") is None or float(str(report["win_rate"])) < 0.70:
            ranking_blockers.append(f"{profile}_WIN_RATE_BELOW_70_PERCENT")
        if Decimal(str(report.get("expectancy_usdt", "0") or "0")) <= 0:
            ranking_blockers.append(f"{profile}_EXPECTANCY_NOT_POSITIVE")
        if Decimal(str(report.get("net_pnl", "0") or "0")) <= 0:
            ranking_blockers.append(f"{profile}_NET_PNL_NOT_POSITIVE")
        profit_factor = report.get("profit_factor")
        no_loss_positive_sample = (
            int(str(report.get("losses", 0))) == 0
            and int(str(report.get("wins", 0))) > 0
        )
        if not no_loss_positive_sample and (
            profit_factor is None or Decimal(str(profit_factor)) <= 1
        ):
            ranking_blockers.append(f"{profile}_PROFIT_FACTOR_NOT_ABOVE_ONE")
    ranking_blockers.extend(
        (
            "TIME_ORDERED_OOS_ROBUSTNESS_NOT_EVALUATED",
            "BOOTSTRAP_EXPECTANCY_LOWER_BOUND_NOT_EVALUATED",
            "DSR_NOT_EVALUATED",
            "PBO_NOT_EVALUATED",
            "DRAWDOWN_GATE_NOT_EVALUATED",
            "INDEPENDENT_FORWARD_LIVE_PUBLIC_NOT_EVALUATED",
        )
    )
    gate_rejection_counts: Counter[str] = Counter()
    gate_baseline_qualified_count = 0
    gate_accepted_qualified_count = 0
    gate_rejected_qualified_count = 0
    for run in runs:
        if run.get("signal_gate_target_strategy_id", strategy_id) != strategy_id:
            continue
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
        "cost_performance_gate_passed": cost_performance_gate,
        "robustness_gate_passed": False,
        "ranking_eligible": False,
        "ranking_blockers": ranking_blockers,
        "ranking_contract": {
            "minimum_unique_market_opportunities": minimum_opportunities,
            "minimum_samples_per_profile": minimum_opportunities,
            "minimum_win_rate_per_profile": 0.70,
            "positive_expectancy_required": True,
            "profit_factor_above_one_required": True,
            "time_ordered_oos_required": True,
            "bootstrap_lower_bound_required": True,
            "dsr_required": True,
            "pbo_required": True,
            "drawdown_gate_required": True,
            "independent_forward_live_public_required": True,
        },
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
    strategy_logic: str = STRATEGY_LOGIC_CURRENT,
    dataset_manifest: Path | None = None,
    maximum_events: int | None = None,
) -> dict[str, object]:
    dataset_reference = (
        frozen_dataset_reference(dataset_manifest, run_ids)
        if dataset_manifest is not None
        else {"status": "NOT_PROVIDED"}
    )
    runs = [
        replay_archive_run(
            run_id,
            archive / f"run={run_id}",
            strategy_id=strategy_id,
            signal_gate=signal_gate,
            strategy_logic=strategy_logic,
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
        "schema_version": 2,
        "status": "RESEARCH_REPLAY_COMPLETE",
        "method": "ACTUAL_PAPER_RUNTIME_ENTRY_FILL_TP_SL_MANAGEMENT_PATH",
        "git_commit": git_commit(),
        "strategy_id": strategy_id,
        "signal_gate": signal_gate,
        "strategy_logic": strategy_logic,
        "strategy_version": STRATEGY_VERSION,
        "event_order": "OBSERVED_RECEIVE_ORDER_ADR_080",
        "frozen_dataset": dataset_reference,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "runtime_ai_order_decision": False,
        "runs": runs,
        "splits": split_summaries,
        "overall": _summary(runs, strategy_id=strategy_id),
    }


def build_strategy_league_result(
    archive: Path,
    *,
    run_ids: Sequence[str],
    signal_gate: str = SIGNAL_GATE_NONE,
    strategy_logic: str = STRATEGY_LOGIC_CURRENT,
    dataset_manifest: Path | None = None,
    maximum_events: int | None = None,
) -> dict[str, object]:
    """등록 전략 전체를 각 Run당 한 번만 읽어 동일 PAPER 입력으로 비교한다."""

    strategy_ids = StrategyRegistry().strategy_ids
    dataset_reference = (
        frozen_dataset_reference(dataset_manifest, run_ids)
        if dataset_manifest is not None
        else {"status": "NOT_PROVIDED"}
    )
    runs = [
        replay_strategy_league_archive_run(
            run_id,
            archive / f"run={run_id}",
            signal_gate=signal_gate,
            strategy_logic=strategy_logic,
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
        split: {
            strategy_id: _summary(
                [run_by_id[run_id] for run_id in ids if run_id in run_by_id],
                strategy_id=strategy_id,
            )
            for strategy_id in strategy_ids
        }
        for split, ids in split_ids.items()
    }
    overall_by_strategy = {
        strategy_id: _summary(runs, strategy_id=strategy_id)
        for strategy_id in strategy_ids
    }
    return {
        "schema_version": 2,
        "status": "RESEARCH_STRATEGY_LEAGUE_REPLAY_COMPLETE",
        "method": "ONE_PASS_ALL_REGISTERED_ACTUAL_PAPER_RUNTIME_PATH",
        "git_commit": git_commit(),
        "research_scope": "ALL_REGISTERED_STRATEGIES",
        "strategy_ids": list(strategy_ids),
        "strategy_count": len(strategy_ids),
        "strategy_account_count": len(strategy_ids) * 2,
        "signal_gate_target_strategy_id": DEFAULT_STRATEGY_ID,
        "signal_gate": signal_gate,
        "strategy_logic": strategy_logic,
        "strategy_version": STRATEGY_VERSION,
        "event_order": "OBSERVED_RECEIVE_ORDER_ADR_080",
        "frozen_dataset": dataset_reference,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "runtime_ai_order_decision": False,
        "retired_strategy_reactivation_scope": "EPHEMERAL_RESEARCH_REPLAY_ONLY",
        "ranking_eligible_strategy_ids": [],
        "profitability_status": "NOT_PROVEN",
        "runs": runs,
        "splits": split_summaries,
        "overall_by_strategy": overall_by_strategy,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("data/market-parquet-v6/venue=BINANCE_USDM"),
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--strategy-id", default=DEFAULT_STRATEGY_ID)
    selection.add_argument("--all-strategies", action="store_true")
    parser.add_argument("--signal-gate", choices=SIGNAL_GATES, default=SIGNAL_GATE_NONE)
    parser.add_argument(
        "--strategy-logic",
        choices=STRATEGY_LOGICS,
        default=STRATEGY_LOGIC_CURRENT,
    )
    parser.add_argument(
        "--dataset-manifest",
        type=Path,
        default=DEFAULT_DATASET_MANIFEST,
    )
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
    if args.all_strategies:
        result = build_strategy_league_result(
            args.archive,
            run_ids=run_ids,
            signal_gate=str(args.signal_gate),
            strategy_logic=str(args.strategy_logic),
            dataset_manifest=args.dataset_manifest,
            maximum_events=args.maximum_events,
        )
    else:
        result = build_result(
            args.archive,
            strategy_id=str(args.strategy_id),
            run_ids=run_ids,
            signal_gate=str(args.signal_gate),
            strategy_logic=str(args.strategy_logic),
            dataset_manifest=args.dataset_manifest,
            maximum_events=args.maximum_events,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
