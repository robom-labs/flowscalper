# 저장 공개시장 이벤트를 실제 PAPER 전략·체결·TP/SL 경로로 재생한다.

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from itertools import chain
from pathlib import Path
from typing import cast

from backend.app.analytics.reports import TradeAnalytics
from backend.app.build_identity import STRATEGY_VERSION, git_commit
from backend.app.domain.models import MarketDataState, MarketEvent, RuntimeMode, Side, Venue
from backend.app.features import FeatureSnapshot
from backend.app.market_data import Candle
from backend.app.regime import Regime
from backend.app.research.protocol import (
    bootstrap_mean_interval,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)
from backend.app.risk.manager import STRATEGY_LEAGUE_RISK_LIMITS
from backend.app.runtime import PaperRuntime
from backend.app.strategies.base import CandidateStatus
from backend.app.strategies.registry import (
    StrategyChangeSource,
    StrategyMode,
    StrategyRegistry,
)
from backend.app.strategies.runtime_evaluator import EvaluatedSignal, StrategySignalEvaluator
from scripts.research_intraday_candidates import _dataset_slice, _event_rows
from scripts.research_strategy_revision import (
    DEFAULT_OOS_RUNS,
    DEFAULT_RESEARCH_TRAIN_RUNS,
    DEFAULT_VALIDATION_RUNS,
)

DEFAULT_STRATEGY_ID = "VWAP_EXHAUSTION_REVERSION_V1"
SIGNAL_GATE_TARGET_ALL = "ALL_REGISTERED_STRATEGIES"
SIGNAL_GATE_NONE = "NONE"
SIGNAL_GATE_TP1_FEASIBILITY = "TP1_FEASIBILITY_CONFLUENCE_V1"
SIGNAL_GATES = (SIGNAL_GATE_NONE, SIGNAL_GATE_TP1_FEASIBILITY)
STRATEGY_LOGIC_CURRENT = "CURRENT_FULL_CONFLUENCE"
STRATEGY_LOGIC_WAVE102 = "WAVE102_PARTIAL_CONFIRMATION_BASELINE"
STRATEGY_LOGICS = (STRATEGY_LOGIC_CURRENT, STRATEGY_LOGIC_WAVE102)
TP1_FEASIBILITY_LOOKBACK_MS = 120_000
DEFAULT_DATASET_MANIFEST = Path("evidence/STRATEGY_100_DATASET_MANIFEST.json")
ROBUSTNESS_BOOTSTRAP_SEED = 105_070
MINIMUM_RANKING_OPPORTUNITIES = 30
MINIMUM_RANKING_WIN_RATE = 0.70
MINIMUM_DSR_PROBABILITY = 0.95
MAXIMUM_PBO = 0.20
MINIMUM_CONCENTRATION_SYMBOLS = 3
MINIMUM_CONCENTRATION_RUNS = 3
MAXIMUM_SINGLE_SYMBOL_OPPORTUNITY_SHARE = 0.50
MAXIMUM_SINGLE_RUN_OPPORTUNITY_SHARE = 0.50
MINIMUM_MULTI_REGIME_COUNT = 2
MAXIMUM_SINGLE_REGIME_OPPORTUNITY_SHARE = 0.80
PAPER_STARTING_EQUITY_USDT = Decimal("1000")
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


def verify_frozen_archive_bytes(
    archive: Path,
    dataset_manifest: Path,
    run_ids: Sequence[str],
) -> dict[str, object]:
    """동결 manifest의 선택 Run을 현재 parquet bytes와 다시 대조한다."""

    reference = frozen_dataset_reference(dataset_manifest, run_ids)
    selected_rows = cast(Sequence[Mapping[str, object]], reference["selected_runs"])
    expected_by_id = {str(row["run_id"]): row for row in selected_rows}
    recomputed = [
        asdict(_dataset_slice(run_id, archive / f"run={run_id}"))
        for run_id in run_ids
    ]
    compared_fields = (
        "run_id",
        "venue",
        "symbols",
        "start_ts_ms",
        "end_ts_ms",
        "event_count",
        "checksum",
    )
    mismatches: dict[str, dict[str, object]] = {}
    for row in recomputed:
        run_id = str(row["run_id"])
        expected = expected_by_id[run_id]
        differences: dict[str, object] = {}
        for field_name in compared_fields:
            expected_value = expected[field_name]
            actual_value = row[field_name]
            if field_name == "symbols":
                expected_symbols = cast(Sequence[object], expected_value)
                actual_symbols = cast(Sequence[object], actual_value)
                expected_value = [str(value) for value in expected_symbols]
                actual_value = [str(value) for value in actual_symbols]
            if expected_value != actual_value:
                differences[field_name] = {
                    "expected": expected_value,
                    "actual": actual_value,
                }
        if differences:
            mismatches[run_id] = differences
    if mismatches:
        raise ValueError(
            "현재 archive bytes가 동결 dataset manifest와 다릅니다: "
            + ", ".join(sorted(mismatches))
        )
    return {
        "status": "PASS",
        "method": "RECOMPUTED_RUN_EVENT_RANGE_COUNT_AND_FILE_SHA256",
        "run_count": len(recomputed),
        "event_count": sum(int(str(row["event_count"])) for row in recomputed),
        "run_ids": list(run_ids),
        "manifest_file_sha256": reference["file_sha256"],
        "manifest_sha256": reference["manifest_sha256"],
        "compared_fields": list(compared_fields),
        "mismatch_count": 0,
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
    strategy_evaluation_counts: Counter[str] = field(default_factory=Counter)
    strategy_baseline_qualified_counts: Counter[str] = field(default_factory=Counter)
    strategy_post_gate_qualified_counts: Counter[str] = field(default_factory=Counter)
    strategy_side_evaluation_counts: Counter[tuple[str, str]] = field(
        default_factory=Counter
    )
    strategy_side_baseline_qualified_counts: Counter[tuple[str, str]] = field(
        default_factory=Counter
    )
    strategy_side_post_gate_qualified_counts: Counter[tuple[str, str]] = field(
        default_factory=Counter
    )
    strategy_rejection_counts: dict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    strategy_gate_baseline_qualified_counts: Counter[str] = field(
        default_factory=Counter
    )
    strategy_gate_accepted_qualified_counts: Counter[str] = field(
        default_factory=Counter
    )
    strategy_gate_rejected_qualified_counts: Counter[str] = field(
        default_factory=Counter
    )
    strategy_gate_rejection_counts: dict[str, Counter[str]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
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
        for signal in signals:
            strategy_id = signal.decision.strategy_id
            side = signal.decision.side.value
            self.strategy_evaluation_counts[strategy_id] += 1
            self.strategy_side_evaluation_counts[(strategy_id, side)] += 1
            if signal.decision.status is CandidateStatus.QUALIFIED:
                self.strategy_baseline_qualified_counts[strategy_id] += 1
                self.strategy_side_baseline_qualified_counts[(strategy_id, side)] += 1
            else:
                self.strategy_rejection_counts[strategy_id].update(
                    signal.decision.rejection_codes
                )
        recent_range_bps = self._recent_range_bps(snapshot)
        filtered: list[EvaluatedSignal] = []
        for signal in signals:
            strategy_id = signal.decision.strategy_id
            if (
                not self._is_gate_target(strategy_id)
                or signal.decision.status is not CandidateStatus.QUALIFIED
            ):
                filtered.append(signal)
                continue
            self.baseline_qualified_count += 1
            self.strategy_gate_baseline_qualified_counts[strategy_id] += 1
            rejections: tuple[str, ...] = ()
            if self.signal_gate == SIGNAL_GATE_TP1_FEASIBILITY:
                descriptor = registry.descriptor(strategy_id)
                rejections = tp1_feasibility_gate_rejections(
                    signal,
                    snapshot,
                    recent_range_bps=recent_range_bps,
                    take_profit_1_r=descriptor.take_profit_1_r,
                )
            if not rejections:
                self.accepted_qualified_count += 1
                self.strategy_gate_accepted_qualified_counts[strategy_id] += 1
                filtered.append(signal)
                continue
            self.rejected_qualified_count += 1
            self.rejection_counts.update(rejections)
            self.strategy_gate_rejected_qualified_counts[strategy_id] += 1
            self.strategy_gate_rejection_counts[strategy_id].update(rejections)
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
        for signal in filtered:
            if signal.decision.status is not CandidateStatus.QUALIFIED:
                continue
            strategy_id = signal.decision.strategy_id
            side = signal.decision.side.value
            self.strategy_post_gate_qualified_counts[strategy_id] += 1
            self.strategy_side_post_gate_qualified_counts[(strategy_id, side)] += 1
        return tuple(filtered)

    def diagnostics(self) -> dict[str, object]:
        strategy_ids = sorted(self.strategy_evaluation_counts)
        return {
            "signal_gate": self.signal_gate,
            "strategy_logic": self.strategy_logic,
            "baseline_qualified_count": self.baseline_qualified_count,
            "accepted_qualified_count": self.accepted_qualified_count,
            "rejected_qualified_count": self.rejected_qualified_count,
            "rejection_counts": dict(sorted(self.rejection_counts.items())),
            "can_create_signals": False,
            "signal_gate_can_create_signals": False,
            "strategies": {
                strategy_id: {
                    "evaluated": self.strategy_evaluation_counts[strategy_id],
                    "baseline_qualified": self.strategy_baseline_qualified_counts[
                        strategy_id
                    ],
                    "post_gate_qualified": self.strategy_post_gate_qualified_counts[
                        strategy_id
                    ],
                    "gate_targeted": self._is_gate_target(strategy_id),
                    "gate_baseline_qualified": (
                        self.strategy_gate_baseline_qualified_counts[strategy_id]
                    ),
                    "gate_accepted_qualified": (
                        self.strategy_gate_accepted_qualified_counts[strategy_id]
                    ),
                    "gate_rejected_qualified": (
                        self.strategy_gate_rejected_qualified_counts[strategy_id]
                    ),
                    "gate_rejection_counts": dict(
                        sorted(self.strategy_gate_rejection_counts[strategy_id].items())
                    ),
                    "rejection_counts": dict(
                        sorted(self.strategy_rejection_counts[strategy_id].items())
                    ),
                    "sides": {
                        side.value: {
                            "evaluated": self.strategy_side_evaluation_counts[
                                (strategy_id, side.value)
                            ],
                            "baseline_qualified": (
                                self.strategy_side_baseline_qualified_counts[
                                    (strategy_id, side.value)
                                ]
                            ),
                            "post_gate_qualified": (
                                self.strategy_side_post_gate_qualified_counts[
                                    (strategy_id, side.value)
                                ]
                            ),
                        }
                        for side in Side
                    },
                }
                for strategy_id in strategy_ids
            },
        }

    def _is_gate_target(self, strategy_id: str) -> bool:
        return self.target_strategy_id in (strategy_id, SIGNAL_GATE_TARGET_ALL)

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
    all_strategy_gate = signal_gate_target_strategy_id == SIGNAL_GATE_TARGET_ALL
    if not all_strategy_gate and signal_gate_target_strategy_id not in selected_strategy_ids:
        raise ValueError("연구 신호 gate 대상 전략이 선택 전략에 없습니다.")
    if all_strategy_gate and selected_strategy_ids != StrategyRegistry().strategy_ids:
        raise ValueError("전체 전략 연구 gate는 등록 전략 전체 replay에서만 사용할 수 있습니다.")
    if signal_gate not in SIGNAL_GATES:
        raise ValueError(f"알 수 없는 연구 신호 gate입니다: {signal_gate}")
    if strategy_logic not in STRATEGY_LOGICS:
        raise ValueError(f"알 수 없는 연구 전략 로직입니다: {strategy_logic}")
    if (
        strategy_logic == STRATEGY_LOGIC_WAVE102
        and signal_gate_target_strategy_id != DEFAULT_STRATEGY_ID
    ):
        raise ValueError("Wave102 기준선 대상은 VWAP 전략이어야 합니다.")
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
    target_setting = (
        None
        if all_strategy_gate
        else runtime.strategy_registry.setting(signal_gate_target_strategy_id)
    )
    enabled_other_strategies = [
        current_id
        for current_id in runtime.strategy_registry.strategy_ids
        if not all_strategy_gate
        and current_id != signal_gate_target_strategy_id
        and runtime.strategy_registry.setting(current_id).mode is not StrategyMode.OFF
    ]
    strategy_modes = {
        strategy_id: runtime.strategy_registry.setting(strategy_id).mode.value
        for strategy_id in selected_strategy_ids
    }
    research_diagnostics = gated_evaluator.diagnostics()
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
        "signal_gate_trial_id": (
            f"{signal_gate}:{signal_gate_target_strategy_id}"
        ),
        "strategy_logic": strategy_logic,
        "signal_gate_diagnostics": research_diagnostics,
        "strategy_decision_diagnostics": research_diagnostics["strategies"],
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
        "strategy_mode": (
            "MULTI_STRATEGY" if target_setting is None else target_setting.mode.value
        ),
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
    signal_gate_target_strategy_id: str = DEFAULT_STRATEGY_ID,
    signal_gate: str = SIGNAL_GATE_NONE,
    strategy_logic: str = STRATEGY_LOGIC_CURRENT,
    maximum_events: int | None = None,
) -> dict[str, object]:
    """등록 전략 전체를 22개 독립계좌의 한 무원장 PAPER 런타임에서 재생한다."""

    return _replay_archive_run_for_strategies(
        run_id,
        run_dir,
        strategy_ids=StrategyRegistry().strategy_ids,
        signal_gate_target_strategy_id=signal_gate_target_strategy_id,
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
        run_gate_target = run.get("signal_gate_target_strategy_id", strategy_id)
        if run_gate_target not in (strategy_id, SIGNAL_GATE_TARGET_ALL):
            continue
        diagnostics = run.get("signal_gate_diagnostics")
        if not isinstance(diagnostics, Mapping):
            continue
        strategy_diagnostics = diagnostics.get("strategies")
        strategy_gate_diagnostics = (
            strategy_diagnostics.get(strategy_id)
            if isinstance(strategy_diagnostics, Mapping)
            else None
        )
        if isinstance(strategy_gate_diagnostics, Mapping):
            gate_baseline_qualified_count += int(
                str(strategy_gate_diagnostics.get("gate_baseline_qualified", 0))
            )
            gate_accepted_qualified_count += int(
                str(strategy_gate_diagnostics.get("gate_accepted_qualified", 0))
            )
            gate_rejected_qualified_count += int(
                str(strategy_gate_diagnostics.get("gate_rejected_qualified", 0))
            )
            rejection_counts = strategy_gate_diagnostics.get("gate_rejection_counts")
        else:
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


def _result_strategy_trade_rows(
    runs: Sequence[Mapping[str, object]],
    *,
    strategy_id: str,
    profile: str | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run in runs:
        raw_rows = run.get("trade_rows")
        if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, str | bytes):
            continue
        rows.extend(
            dict(row)
            for row in raw_rows
            if isinstance(row, Mapping)
            and str(row.get("strategy_id")) == strategy_id
            and (profile is None or str(row.get("profile")) == profile)
        )
    return rows


def _trade_net_bps(row: Mapping[str, object]) -> float:
    entry = Decimal(str(row["entry_price"]))
    quantity = Decimal(str(row["quantity"]))
    net_pnl = Decimal(str(row["net_pnl_usdt"]))
    if not all(value.is_finite() for value in (entry, quantity, net_pnl)):
        raise ValueError("전략 replay 거래 수익률 입력은 유한해야 합니다.")
    notional = entry * quantity
    if notional <= 0:
        raise ValueError("전략 replay 거래 진입명목은 양수여야 합니다.")
    return float(net_pnl / notional * Decimal(10_000))


def _profile_oos_robustness(
    trades: Sequence[Mapping[str, object]],
    *,
    trials: int,
    seed: int,
) -> dict[str, object]:
    ordered = sorted(
        trades,
        key=lambda row: (
            int(str(row.get("exit_ts_ms", 0))),
            str(row.get("trade_id", "")),
        ),
    )
    returns_bps = [_trade_net_bps(row) for row in ordered]
    pnl = [Decimal(str(row["net_pnl_usdt"])) for row in ordered]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    net_pnl = sum(pnl, Decimal(0))
    expectancy_bps = (
        sum(returns_bps) / len(returns_bps) if returns_bps else None
    )
    profit_factor = (
        sum(wins, Decimal(0)) / abs(sum(losses, Decimal(0))) if losses else None
    )
    equity = PAPER_STARTING_EQUITY_USDT
    peak = equity
    maximum_drawdown = Decimal(0)
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    drawdown_limit = (
        PAPER_STARTING_EQUITY_USDT
        * STRATEGY_LEAGUE_RISK_LIMITS.maximum_drawdown_fraction
    )
    bootstrap = bootstrap_mean_interval(returns_bps, seed=seed)
    dsr = deflated_sharpe_ratio(returns_bps, trials=trials)
    win_rate = len(wins) / len(pnl) if pnl else None
    no_loss_positive_sample = not losses and bool(wins)
    gates = {
        "sample_at_least_30": len(pnl) >= MINIMUM_RANKING_OPPORTUNITIES,
        "win_rate_at_least_70_percent": (
            win_rate is not None and win_rate >= MINIMUM_RANKING_WIN_RATE
        ),
        "expectancy_bps_positive": (
            expectancy_bps is not None and expectancy_bps > 0
        ),
        "net_pnl_positive": net_pnl > 0,
        "profit_factor_above_one": (
            no_loss_positive_sample
            or (profit_factor is not None and profit_factor > Decimal(1))
        ),
        "bootstrap_lower_positive": (
            bootstrap.get("lower") is not None
            and float(str(bootstrap["lower"])) > 0
        ),
        "dsr_at_least_0_95": (
            dsr.get("dsr_probability") is not None
            and float(str(dsr["dsr_probability"])) >= MINIMUM_DSR_PROBABILITY
        ),
        "maximum_drawdown_within_league_8_percent_limit": (
            maximum_drawdown <= drawdown_limit
        ),
    }
    return {
        "sample_size": len(pnl),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "expectancy_bps": expectancy_bps,
        "net_pnl_usdt": str(net_pnl),
        "profit_factor": str(profit_factor) if profit_factor is not None else None,
        "maximum_drawdown_usdt": str(maximum_drawdown),
        "maximum_drawdown_limit_usdt": str(drawdown_limit),
        "expectancy_bootstrap_95": bootstrap,
        "deflated_sharpe_ratio": dsr,
        "gates": gates,
        "gate_passed": all(gates.values()),
        "returns_are_nonannualized_net_bps": True,
    }


def _strategy_censored_count(
    runs: Sequence[Mapping[str, object]],
    *,
    strategy_id: str,
) -> int:
    censored = 0
    for run in runs:
        open_state = run.get("open_state")
        if not isinstance(open_state, Mapping):
            continue
        positions = open_state.get("positions")
        if isinstance(positions, Sequence) and not isinstance(positions, str | bytes):
            censored += sum(
                isinstance(position, Mapping)
                and str(position.get("strategy_id")) == strategy_id
                for position in positions
            )
        pending = open_state.get("pending_entry_counts")
        if isinstance(pending, Mapping):
            censored += int(str(pending.get(strategy_id, 0)))
    return censored


def _strategy_oos_concentration(
    trades: Sequence[Mapping[str, object]],
    *,
    supported_regimes: Sequence[str] = (),
) -> dict[str, object]:
    """BASE·STRESS 중복을 제거한 OOS 기회의 종목·Run·레짐 집중도를 계산한다."""

    opportunity_rows: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    metadata_conflicts: set[tuple[str, str, str, str]] = set()
    for row in trades:
        key = (
            str(row.get("run_id", "")),
            str(row.get("signal_event_id", "")),
            str(row.get("strategy_id", "")),
            str(row.get("side", "")),
        )
        metadata = (
            str(row.get("symbol", "UNKNOWN")),
            str(row.get("regime", "UNKNOWN")),
        )
        previous = opportunity_rows.setdefault(key, metadata)
        if previous != metadata:
            metadata_conflicts.add(key)

    opportunity_count = len(opportunity_rows)
    symbol_counts = Counter(metadata[0] for metadata in opportunity_rows.values())
    regime_counts = Counter(metadata[1] for metadata in opportunity_rows.values())
    run_counts = Counter(key[0] for key in opportunity_rows)

    def maximum_share(counts: Counter[str]) -> float | None:
        if not opportunity_count or not counts:
            return None
        return max(counts.values()) / opportunity_count

    maximum_symbol_share = maximum_share(symbol_counts)
    maximum_run_share = maximum_share(run_counts)
    maximum_regime_share = maximum_share(regime_counts)
    normalized_supported_regimes = tuple(dict.fromkeys(map(str, supported_regimes)))
    observed_regimes = set(regime_counts)
    supported_regime_set = set(normalized_supported_regimes)
    regimes_within_contract = bool(observed_regimes) and (
        not supported_regime_set or observed_regimes <= supported_regime_set
    )
    single_regime_contract = len(normalized_supported_regimes) == 1
    regime_diversification_passed = (
        regimes_within_contract
        if single_regime_contract
        else (
            regimes_within_contract
            and len(regime_counts) >= MINIMUM_MULTI_REGIME_COUNT
            and maximum_regime_share is not None
            and maximum_regime_share <= MAXIMUM_SINGLE_REGIME_OPPORTUNITY_SHARE
        )
    )
    gates = {
        "sample_at_least_30_unique_opportunities": (
            opportunity_count >= MINIMUM_RANKING_OPPORTUNITIES
        ),
        "profile_metadata_consistent": not metadata_conflicts,
        "distinct_symbols_at_least_3": (len(symbol_counts) >= MINIMUM_CONCENTRATION_SYMBOLS),
        "maximum_single_symbol_share_at_most_0_50": (
            maximum_symbol_share is not None
            and maximum_symbol_share <= MAXIMUM_SINGLE_SYMBOL_OPPORTUNITY_SHARE
        ),
        "distinct_runs_at_least_3": len(run_counts) >= MINIMUM_CONCENTRATION_RUNS,
        "maximum_single_run_share_at_most_0_50": (
            maximum_run_share is not None
            and maximum_run_share <= MAXIMUM_SINGLE_RUN_OPPORTUNITY_SHARE
        ),
        "regimes_within_strategy_contract": regimes_within_contract,
        "regime_diversification_matches_strategy_contract": (regime_diversification_passed),
    }
    return {
        "unique_market_opportunity_count": opportunity_count,
        "symbol_counts": dict(sorted(symbol_counts.items())),
        "run_counts": dict(sorted(run_counts.items())),
        "regime_counts": dict(sorted(regime_counts.items())),
        "maximum_single_symbol_opportunity_share": maximum_symbol_share,
        "maximum_single_run_opportunity_share": maximum_run_share,
        "maximum_single_regime_opportunity_share": maximum_regime_share,
        "supported_regimes": list(normalized_supported_regimes),
        "single_regime_strategy_contract": single_regime_contract,
        "metadata_conflict_count": len(metadata_conflicts),
        "gates": gates,
        "gate_passed": all(gates.values()),
    }


def strategy_league_robustness(
    runs: Sequence[Mapping[str, object]],
    *,
    strategy_ids: Sequence[str],
    train_validation_run_ids: Sequence[str],
    oos_run_ids: Sequence[str],
    supported_regimes_by_strategy: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, object]:
    """고정 전략 전체의 시간순 OOS·bootstrap·DSR·PBO를 같은 결과에서 계산한다."""

    ordered_strategy_ids = tuple(dict.fromkeys(strategy_ids))
    if not ordered_strategy_ids:
        raise ValueError("전략 강건성 평가에는 전략이 필요합니다.")
    run_by_id = {str(run.get("run_id")): run for run in runs}
    missing_train_validation = [
        run_id for run_id in train_validation_run_ids if run_id not in run_by_id
    ]
    missing_oos = [run_id for run_id in oos_run_ids if run_id not in run_by_id]
    train_validation_runs = [
        run_by_id[run_id]
        for run_id in train_validation_run_ids
        if run_id in run_by_id
    ]
    oos_runs = [run_by_id[run_id] for run_id in oos_run_ids if run_id in run_by_id]
    complete_pbo_input = (
        not missing_train_validation
        and len(train_validation_runs) >= 4
        and len(train_validation_runs) % 2 == 0
        and len(ordered_strategy_ids) >= 2
    )
    pbo_by_profile: dict[str, dict[str, object]] = {}
    fold_returns_by_profile: dict[str, dict[str, list[float]]] = {}
    for profile in ("BASE", "STRESS"):
        fold_returns = {
            strategy_id: [
                (
                    sum(values) / len(values)
                    if (
                        values := [
                            _trade_net_bps(row)
                            for row in _result_strategy_trade_rows(
                                (run,),
                                strategy_id=strategy_id,
                                profile=profile,
                            )
                        ]
                    )
                    else 0.0
                )
                for run in train_validation_runs
            ]
            for strategy_id in ordered_strategy_ids
        }
        fold_returns_by_profile[profile] = fold_returns
        if complete_pbo_input:
            pbo_by_profile[profile] = probability_of_backtest_overfitting(
                fold_returns
            )
        else:
            pbo_by_profile[profile] = {
                "pbo": None,
                "combinations": 0,
                "logits": [],
                "status": "REQUIRED_RUN_FOLDS_MISSING_OR_INSUFFICIENT",
            }

    pbo_gates = {
        profile: (
            report.get("pbo") is not None
            and float(str(report["pbo"])) <= MAXIMUM_PBO
        )
        for profile, report in pbo_by_profile.items()
    }
    strategy_results: dict[str, dict[str, object]] = {}
    supported_regime_map = supported_regimes_by_strategy or {}
    for strategy_index, strategy_id in enumerate(ordered_strategy_ids):
        trades = _result_strategy_trade_rows(oos_runs, strategy_id=strategy_id)
        opportunities = {
            (
                str(row.get("run_id")),
                str(row.get("signal_event_id")),
                str(row.get("strategy_id")),
                str(row.get("side")),
            )
            for row in trades
        }
        profile_results = {
            profile: _profile_oos_robustness(
                [row for row in trades if str(row.get("profile")) == profile],
                trials=len(ordered_strategy_ids),
                seed=ROBUSTNESS_BOOTSTRAP_SEED
                + strategy_index * 10
                + (0 if profile == "BASE" else 1),
            )
            for profile in ("BASE", "STRESS")
        }
        concentration = _strategy_oos_concentration(
            trades,
            supported_regimes=supported_regime_map.get(strategy_id, ()),
        )
        censored_count = _strategy_censored_count(
            oos_runs,
            strategy_id=strategy_id,
        )
        blockers: list[str] = []
        if missing_train_validation:
            blockers.append("TRAIN_VALIDATION_RUNS_MISSING")
        if missing_oos:
            blockers.append("FINAL_OOS_RUNS_MISSING")
        if len(opportunities) < MINIMUM_RANKING_OPPORTUNITIES:
            blockers.append("OOS_UNIQUE_MARKET_OPPORTUNITIES_BELOW_30")
        for profile, result in profile_results.items():
            gates = cast(Mapping[str, object], result["gates"])
            for gate, passed in gates.items():
                if not passed:
                    blockers.append(f"{profile}_OOS_{gate.upper()}")
            if not pbo_gates[profile]:
                blockers.append(f"{profile}_PBO_ABOVE_0_20_OR_UNAVAILABLE")
        if censored_count:
            blockers.append("FINAL_OOS_CENSORED_POSITIONS_OR_PENDING_ENTRIES")
        concentration_gates = cast(Mapping[str, object], concentration["gates"])
        for gate, passed in concentration_gates.items():
            if not passed:
                blockers.append(f"OOS_CONCENTRATION_{gate.upper()}")
        historical_gates_passed = (
            not missing_train_validation
            and not missing_oos
            and len(opportunities) >= MINIMUM_RANKING_OPPORTUNITIES
            and censored_count == 0
            and all(result["gate_passed"] for result in profile_results.values())
            and all(pbo_gates.values())
        )
        blockers.extend(
            (
                "PARAMETER_ROBUSTNESS_NOT_EVALUATED",
                "INDEPENDENT_FORWARD_LIVE_PUBLIC_NOT_EVALUATED",
            )
        )
        strategy_results[strategy_id] = {
            "final_oos_unique_market_opportunity_count": len(opportunities),
            "final_oos_censored_count": censored_count,
            "profiles": profile_results,
            "concentration": concentration,
            "pbo_gate_by_profile": pbo_gates,
            "historical_cost_oos_statistical_gates_passed": historical_gates_passed,
            "historical_concentration_gate_passed": concentration["gate_passed"],
            "historical_cost_oos_statistical_and_concentration_gates_passed": (
                historical_gates_passed and bool(concentration["gate_passed"])
            ),
            "ranking_eligible": False,
            "profitability_status": "NOT_PROVEN",
            "ranking_blockers": list(dict.fromkeys(blockers)),
        }
    return {
        "status": (
            "HISTORICAL_METRICS_CALCULATED_FORWARD_PENDING"
            if not missing_train_validation and not missing_oos
            else "INCOMPLETE_REQUIRED_RUNS"
        ),
        "trial_count": len(ordered_strategy_ids),
        "trial_strategy_ids": list(ordered_strategy_ids),
        "selection_folds": {
            "method": "TIME_ORDERED_TRAIN_PLUS_VALIDATION_RUN_MEAN_NET_BPS",
            "missing_trade_fold_score_bps": 0.0,
            "run_ids": list(train_validation_run_ids),
            "missing_run_ids": missing_train_validation,
            "returns_by_profile_and_strategy": fold_returns_by_profile,
        },
        "final_oos": {
            "run_ids": list(oos_run_ids),
            "missing_run_ids": missing_oos,
            "opened_for_this_result": not missing_oos,
            "no_retuning_after_open": True,
        },
        "pbo_by_profile": pbo_by_profile,
        "pbo_gate_by_profile": pbo_gates,
        "thresholds": {
            "minimum_unique_market_opportunities": MINIMUM_RANKING_OPPORTUNITIES,
            "minimum_samples_per_profile": MINIMUM_RANKING_OPPORTUNITIES,
            "minimum_win_rate_per_profile": MINIMUM_RANKING_WIN_RATE,
            "minimum_dsr_probability": MINIMUM_DSR_PROBABILITY,
            "maximum_pbo": MAXIMUM_PBO,
            "minimum_distinct_symbols": MINIMUM_CONCENTRATION_SYMBOLS,
            "minimum_distinct_runs": MINIMUM_CONCENTRATION_RUNS,
            "maximum_single_symbol_opportunity_share": (MAXIMUM_SINGLE_SYMBOL_OPPORTUNITY_SHARE),
            "maximum_single_run_opportunity_share": (MAXIMUM_SINGLE_RUN_OPPORTUNITY_SHARE),
            "minimum_multi_regime_count": MINIMUM_MULTI_REGIME_COUNT,
            "maximum_single_regime_opportunity_share": (MAXIMUM_SINGLE_REGIME_OPPORTUNITY_SHARE),
            "maximum_drawdown_fraction": str(
                STRATEGY_LEAGUE_RISK_LIMITS.maximum_drawdown_fraction
            ),
        },
        "strategies": strategy_results,
        "ranking_eligible_strategy_ids": [],
        "profitability_status": "NOT_PROVEN",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
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
        "signal_gate_target_strategy_id": strategy_id,
        "signal_gate": signal_gate,
        "signal_gate_trial_id": f"{signal_gate}:{strategy_id}",
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
    signal_gate_target_strategy_id: str = DEFAULT_STRATEGY_ID,
    signal_gate: str = SIGNAL_GATE_NONE,
    strategy_logic: str = STRATEGY_LOGIC_CURRENT,
    dataset_manifest: Path | None = None,
    maximum_events: int | None = None,
) -> dict[str, object]:
    """등록 전략 전체를 각 Run당 한 번만 읽어 동일 PAPER 입력으로 비교한다."""

    registry = StrategyRegistry()
    strategy_ids = registry.strategy_ids
    dataset_reference = (
        frozen_dataset_reference(dataset_manifest, run_ids)
        if dataset_manifest is not None
        else {"status": "NOT_PROVIDED"}
    )
    runs = [
        replay_strategy_league_archive_run(
            run_id,
            archive / f"run={run_id}",
            signal_gate_target_strategy_id=signal_gate_target_strategy_id,
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
    robustness = strategy_league_robustness(
        runs,
        strategy_ids=strategy_ids,
        train_validation_run_ids=(
            *DEFAULT_RESEARCH_TRAIN_RUNS,
            *DEFAULT_VALIDATION_RUNS,
        ),
        oos_run_ids=DEFAULT_OOS_RUNS,
        supported_regimes_by_strategy={
            strategy_id: tuple(
                regime.value for regime in registry.descriptor(strategy_id).supported_regimes
            )
            for strategy_id in strategy_ids
        },
    )
    replaced_robustness_blockers = {
        "TIME_ORDERED_OOS_ROBUSTNESS_NOT_EVALUATED",
        "BOOTSTRAP_EXPECTANCY_LOWER_BOUND_NOT_EVALUATED",
        "DSR_NOT_EVALUATED",
        "PBO_NOT_EVALUATED",
        "DRAWDOWN_GATE_NOT_EVALUATED",
    }
    robustness_by_strategy = cast(
        Mapping[str, Mapping[str, object]],
        robustness["strategies"],
    )
    for strategy_id, summary in overall_by_strategy.items():
        strategy_robustness = robustness_by_strategy[strategy_id]
        existing_blockers = cast(Sequence[str], summary["ranking_blockers"])
        robustness_blockers = cast(
            Sequence[str],
            strategy_robustness["ranking_blockers"],
        )
        summary["robustness_evaluation"] = strategy_robustness
        summary["historical_cost_oos_statistical_gates_passed"] = strategy_robustness[
            "historical_cost_oos_statistical_gates_passed"
        ]
        summary["ranking_blockers"] = list(
            dict.fromkeys(
                [
                    blocker
                    for blocker in existing_blockers
                    if blocker not in replaced_robustness_blockers
                ]
                + list(robustness_blockers)
            )
        )
    return {
        "schema_version": 4,
        "status": "RESEARCH_STRATEGY_LEAGUE_REPLAY_COMPLETE",
        "method": "ONE_PASS_ALL_REGISTERED_ACTUAL_PAPER_RUNTIME_PATH",
        "git_commit": git_commit(),
        "research_scope": "ALL_REGISTERED_STRATEGIES",
        "strategy_ids": list(strategy_ids),
        "strategy_count": len(strategy_ids),
        "strategy_account_count": len(strategy_ids) * 2,
        "signal_gate_target_strategy_id": signal_gate_target_strategy_id,
        "signal_gate": signal_gate,
        "signal_gate_trial_id": (
            f"{signal_gate}:{signal_gate_target_strategy_id}"
        ),
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
        "robustness_evaluation": robustness,
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
        "--signal-gate-target-strategy-id",
        choices=(*StrategyRegistry().strategy_ids, SIGNAL_GATE_TARGET_ALL),
        help=(
            "전체 전략 replay에서 연구 gate를 적용할 한 전략 또는 등록 전략 전체입니다. "
            "한 전략 replay에서는 --strategy-id와 같아야 합니다."
        ),
    )
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
    parser.add_argument(
        "--verify-archive-bytes",
        action="store_true",
        help="리플레이 전에 선택 Run의 현재 parquet bytes를 동결 manifest와 다시 대조합니다.",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    default_full_run_ids = (
        *DEFAULT_RESEARCH_TRAIN_RUNS,
        *DEFAULT_VALIDATION_RUNS,
        *DEFAULT_OOS_RUNS,
    )
    run_ids = tuple(
        args.run_id or default_full_run_ids
    )
    full_frozen_replay = args.maximum_events is None and run_ids == default_full_run_ids
    if full_frozen_replay and not args.verify_archive_bytes:
        raise ValueError(
            "동결 13-Run 전체 결과는 --verify-archive-bytes 없이는 실행할 수 없습니다."
        )
    archive_verification = (
        verify_frozen_archive_bytes(
            args.archive,
            args.dataset_manifest,
            run_ids,
        )
        if args.verify_archive_bytes
        else None
    )
    if args.all_strategies:
        signal_gate_target_strategy_id = str(
            args.signal_gate_target_strategy_id or DEFAULT_STRATEGY_ID
        )
        result = build_strategy_league_result(
            args.archive,
            run_ids=run_ids,
            signal_gate_target_strategy_id=signal_gate_target_strategy_id,
            signal_gate=str(args.signal_gate),
            strategy_logic=str(args.strategy_logic),
            dataset_manifest=args.dataset_manifest,
            maximum_events=args.maximum_events,
        )
    else:
        if args.signal_gate_target_strategy_id == SIGNAL_GATE_TARGET_ALL:
            raise ValueError("전체 전략 연구 gate는 --all-strategies에서만 사용할 수 있습니다.")
        if (
            args.signal_gate_target_strategy_id is not None
            and args.signal_gate_target_strategy_id != args.strategy_id
        ):
            raise ValueError(
                "한 전략 replay의 연구 gate 대상은 --strategy-id와 같아야 합니다."
            )
        result = build_result(
            args.archive,
            strategy_id=str(args.strategy_id),
            run_ids=run_ids,
            signal_gate=str(args.signal_gate),
            strategy_logic=str(args.strategy_logic),
            dataset_manifest=args.dataset_manifest,
            maximum_events=args.maximum_events,
        )
    if archive_verification is not None:
        frozen_dataset = result.get("frozen_dataset")
        if not isinstance(frozen_dataset, dict):
            raise ValueError("리플레이 결과에 동결 dataset 참조가 없습니다.")
        frozen_dataset["current_archive_byte_reverification"] = archive_verification
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
