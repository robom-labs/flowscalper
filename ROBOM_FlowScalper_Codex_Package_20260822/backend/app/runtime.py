"""READY·LIVE·DEMO·REPLAY를 격리한 페이퍼 전용 Run 상태를 관리한다."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from decimal import Decimal, localcontext
from itertools import islice
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from anyio import BrokenWorkerProcess, to_process, to_thread

from backend.app.adapters.fixture import FixtureMarketData
from backend.app.analytics.reports import TradeAnalytics
from backend.app.api.dashboard import build_dashboard_snapshot
from backend.app.build_identity import APP_VERSION, STRATEGY_VERSION, git_commit
from backend.app.candidates import (
    CandidatePlan,
    CandidatePlanner,
    SharedCapitalArbitrationEvidence,
)
from backend.app.clocks import Clock, SystemClock
from backend.app.control.operations import ProgressCallback
from backend.app.costing import CostProfile
from backend.app.domain.market import Instrument, TradeTick
from backend.app.domain.models import (
    MarketDataState,
    MarketEvent,
    RuntimeMode,
    Side,
    SystemStatus,
    Venue,
)
from backend.app.domain.safety import assert_paper_only
from backend.app.execution import BookSnapshot, ExitReason
from backend.app.execution.models import PaperOrder, PaperTrade
from backend.app.execution.portfolio import PaperPortfolioEngine
from backend.app.features import (
    BookFrame,
    DCMidObservation,
    DCState,
    DirectionalChangeEngine,
    FeatureEngine,
    FeatureInputError,
    FeatureSnapshot,
    FixedThresholdProvider,
)
from backend.app.features.semivariance import (
    CompletedMinuteReturn,
    SemivarianceInputError,
    SemivarianceJumpEngine,
    SemivarianceJumpSnapshot,
)
from backend.app.features.semivariance import (
    FeatureReadiness as SemivarianceReadiness,
)
from backend.app.live_public import (
    LiveBootstrapProbe,
    LivePublicBootstrapper,
    PublicDataUnavailable,
)
from backend.app.market_data.candles import Candle, CandleBuilder
from backend.app.market_data.supervisor import (
    BinancePersistentProvider,
    BybitPersistentProvider,
    PersistentPublicSupervisor,
    ProviderSelection,
    PublicStreamProvider,
)
from backend.app.market_data.timeframes import TIMEFRAME_REGISTRY
from backend.app.ops import ProcessResourceSampler
from backend.app.regime import Regime, RegimeClassifier
from backend.app.risk import (
    STRATEGY_LEAGUE_RISK_LIMITS,
    RiskLimits,
    RiskManager,
)
from backend.app.storage.parquet import (
    ArchivedEventBatch,
    ParquetEventStore,
    StorageHealth,
    StoragePressureError,
    warm_market_event_worker_process,
)
from backend.app.storage.sqlite import (
    LedgerInvariantError,
    RecoveryState,
    SQLiteLedger,
    persist_archives_and_candles_in_process,
    run_passive_wal_checkpoint_in_process,
)
from backend.app.strategies.base import CandidateDecision, RunnerManagement
from backend.app.strategies.family import StrategyRole
from backend.app.strategies.governor import (
    GOVERNANCE_EVIDENCE_MAX_AGE_MS,
    GovernanceEvidence,
    StrategyGovernor,
)
from backend.app.strategies.orderflow_confirmation import (
    ORDERFLOW_AFFECTED_STRATEGY_IDS,
    OrderflowConfirmationRuntime,
)
from backend.app.strategies.process_evaluator import (
    ProcessStrategyEvaluator,
    StrategyEvaluationRequest,
    StrategyEvaluationResult,
)
from backend.app.strategies.registry import (
    StrategyChangeSource,
    StrategyDescriptor,
    StrategyLifecycle,
    StrategyMode,
    StrategyRegistry,
)
from backend.app.strategies.runtime_evaluator import EvaluatedSignal, StrategySignalEvaluator
from backend.app.strategies.shadow import ShadowLedger

if TYPE_CHECKING:
    from backend.app.replay.safety import ReplayLiveSafetySnapshot


def _dashboard_performance_report(report: Mapping[str, object]) -> dict[str, object]:
    """실시간 화면에는 구간별 표본 수만 남기고 정밀 통계 중복을 제거한다."""

    compact_windows: dict[str, object] = {}
    windows = report.get("windows")
    if isinstance(windows, Mapping):
        for label, window in windows.items():
            compact_windows[str(label)] = {
                "sample_size": window.get("sample_size", 0) if isinstance(window, Mapping) else 0
            }
    return dict(report) | {"windows": compact_windows}


def _strict_recovery_int(row: Mapping[str, object], field_name: str) -> int:
    value = row.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"복구 Governor 증거의 {field_name}가 정수가 아닙니다.")
    return value


def _strict_recovery_bool(row: Mapping[str, object], field_name: str) -> bool:
    value = row.get(field_name)
    if not isinstance(value, bool):
        raise ValueError(f"복구 Governor 증거의 {field_name}가 bool이 아닙니다.")
    return value


def _strict_recovery_setting_bool(row: Mapping[str, object], field_name: str) -> bool:
    value = row.get(field_name)
    if not isinstance(value, bool):
        raise ValueError(f"복구 전략 설정의 {field_name}가 bool이 아닙니다.")
    return value


def _strict_recovery_setting_int(row: Mapping[str, object], field_name: str) -> int:
    value = row.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"복구 전략 설정의 {field_name}가 음수가 아닌 정수가 아닙니다.")
    return value


def _strict_recovery_setting_text(row: Mapping[str, object], field_name: str) -> str:
    value = row.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"복구 전략 설정의 {field_name}가 빈 문자열입니다.")
    return value


def _recovery_row_token(row: Mapping[str, object]) -> str:
    canonical = json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fail_closed_recovery_windows(
    incidents: Sequence[Mapping[str, object]],
    *,
    run_id: str,
) -> tuple[tuple[int, int], ...]:
    """복구 실패 뒤 다음 성공 복구 전까지의 fail-closed 구간만 반환한다."""

    events: list[tuple[int, str, bool]] = []
    for incident in incidents:
        payload = incident.get("payload")
        if incident.get("run_id") != run_id or not isinstance(payload, Mapping):
            continue
        ts_ms = incident.get("ts_ms")
        new_state = payload.get("new_state")
        recovery_ok = payload.get("recovery_ok") is True
        if (
            isinstance(ts_ms, int)
            and not isinstance(ts_ms, bool)
            and ts_ms >= 0
            and isinstance(new_state, str)
            and incident.get("category") == "PAPER_RESTART_RECOVERY"
            and incident.get("incident_id") == payload.get("transition_id")
            and payload.get("run_id") == run_id
            and payload.get("occurred_ts_ms") == ts_ms
            and payload.get("actor") == "RECOVERY"
            and (
                (
                    new_state == "RECOVERY_FAIL_CLOSED"
                    and payload.get("recovery_ok") is False
                    and payload.get("reversible") is False
                )
                or (
                    new_state == "RECOVERY_REVALIDATION_LOCKED"
                    and payload.get("recovery_ok") is True
                    and payload.get("reversible") is True
                )
            )
        ):
            events.append((ts_ms, new_state, recovery_ok))
    events.sort()
    windows: list[tuple[int, int]] = []
    opened_at: int | None = None
    for ts_ms, new_state, recovery_ok in events:
        if new_state == "RECOVERY_FAIL_CLOSED" and not recovery_ok:
            if opened_at is None:
                opened_at = ts_ms
            continue
        if opened_at is not None and recovery_ok and new_state == "RECOVERY_REVALIDATION_LOCKED":
            windows.append((opened_at, ts_ms))
            opened_at = None
    return tuple(windows)


def _is_fail_closed_governance_contamination(
    row: Mapping[str, object],
    *,
    run_id: str,
    windows: Sequence[tuple[int, int]],
    governance_incidents: Sequence[Mapping[str, object]],
) -> bool:
    """복구 실패 중 실행돼서는 안 됐던 AUTO_GOVERNOR 행만 좁게 식별한다."""

    strategy_id = row.get("strategy_id")
    revision = row.get("settings_revision")
    ts_ms = row.get("ts_ms")
    transition_id = row.get("transition_id")
    change_evidence = row.get("change_evidence")
    if (
        not isinstance(strategy_id, str)
        or not strategy_id
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
        or not isinstance(ts_ms, int)
        or isinstance(ts_ms, bool)
        or not isinstance(change_evidence, Mapping)
    ):
        return False
    evidence = change_evidence.get("evidence")
    assessment = change_evidence.get("assessment")
    lineage = change_evidence.get("lineage")
    if (
        not isinstance(evidence, Mapping)
        or not isinstance(assessment, Mapping)
        or not isinstance(lineage, Mapping)
    ):
        return False
    expected_transition_id = f"strategy-setting-{run_id}-{strategy_id}-rev-{revision}"
    exact_fail_closed_transition = (
        row.get("run_id") == run_id
        and row.get("changed_by") == StrategyChangeSource.AUTO_GOVERNOR.value
        and row.get("actor") == StrategyChangeSource.AUTO_GOVERNOR.value
        and row.get("change_reason") == "OPERATIONAL_FAULT"
        and row.get("cause") == "OPERATIONAL_FAULT"
        and row.get("cause_code") == "OPERATIONAL_FAULT"
        and row.get("mode") == StrategyMode.OFF.value
        and row.get("lifecycle") == StrategyLifecycle.QUARANTINED.value
        and row.get("manual_lock") is False
        and row.get("long_enabled") is True
        and row.get("short_enabled") is True
        and row.get("account_id") is None
        and row.get("symbol") is None
        and transition_id == expected_transition_id
        and row.get("request_revision") == revision - 1
        and row.get("response_revision") == revision
        and row.get("settings_updated_ts_ms") == ts_ms
        and row.get("occurred_ts_ms") == ts_ms
        and assessment.get("strategy_id") == strategy_id
        and assessment.get("reason_codes") == ["OPERATIONAL_FAULT"]
        and assessment.get("recommended_lifecycle") == StrategyLifecycle.QUARANTINED.value
        and assessment.get("automatic_action_allowed") is True
        and assessment.get("transition_required") is True
        and evidence.get("operational_fault") is True
        and evidence.get("operational_health_passed") is False
        and evidence.get("evaluated_ts_ms") == ts_ms
        and lineage.get("schema_version") == 1
        and lineage.get("run_id") == run_id
        and lineage.get("strategy_id") == strategy_id
        and lineage.get("settings_revision") == revision
        and lineage.get("assessment_ts_ms") == ts_ms
        and lineage.get("release_commit") == "UNAVAILABLE"
    )
    if not exact_fail_closed_transition:
        return False
    inside_closed_window = any(
        start_ts_ms <= ts_ms < end_ts_ms for start_ts_ms, end_ts_ms in windows
    )
    if not inside_closed_window:
        return False
    for incident in governance_incidents:
        incident_payload = incident.get("payload")
        if not isinstance(incident_payload, Mapping):
            continue
        if (
            incident.get("incident_id") == transition_id
            and incident.get("run_id") == run_id
            and incident.get("category") == "AUTO_GOVERNOR_TRANSITION"
            and incident.get("ts_ms") == ts_ms
            and incident_payload.get("transition_id") == transition_id
            and incident_payload.get("run_id") == run_id
            and incident_payload.get("strategy_id") == strategy_id
            and incident_payload.get("settings_revision") == revision
            and incident_payload.get("changed_by") == StrategyChangeSource.AUTO_GOVERNOR.value
            and incident_payload.get("change_reason") == "OPERATIONAL_FAULT"
            and incident_payload.get("mode") == StrategyMode.OFF.value
            and incident_payload.get("lifecycle") == StrategyLifecycle.QUARANTINED.value
            and incident_payload.get("settings_updated_ts_ms") == ts_ms
            and incident_payload.get("occurred_ts_ms") == ts_ms
            and incident_payload.get("actor") == StrategyChangeSource.AUTO_GOVERNOR.value
            and incident_payload.get("cause_code") == "OPERATIONAL_FAULT"
            and incident_payload.get("change_evidence") == change_evidence
        ):
            return True
    return False


def _recovery_decimal(
    row: Mapping[str, object],
    field_name: str,
    *,
    required: bool = False,
) -> Decimal | None:
    value = row.get(field_name)
    if value is None:
        if required:
            raise ValueError(f"복구 Governor 증거에 {field_name}가 없습니다.")
        return None
    if isinstance(value, bool):
        raise ValueError(f"복구 Governor 증거의 {field_name}가 숫자가 아닙니다.")
    normalized = Decimal(str(value))
    if not normalized.is_finite():
        raise ValueError(f"복구 Governor 증거의 {field_name}가 유한수가 아닙니다.")
    return normalized


def _recovery_float(
    row: Mapping[str, object],
    field_name: str,
    *,
    required: bool = False,
) -> float | None:
    value = _recovery_decimal(row, field_name, required=required)
    return float(value) if value is not None else None


def _governance_evidence_from_recovery(
    row: Mapping[str, object],
) -> GovernanceEvidence:
    evaluation_period = row.get("evaluation_period")
    if not isinstance(evaluation_period, str) or not evaluation_period.strip():
        raise ValueError("복구 Governor 증거의 evaluation_period가 없습니다.")
    return GovernanceEvidence(
        base_sample_size=_strict_recovery_int(row, "base_sample_size"),
        stress_sample_size=_strict_recovery_int(row, "stress_sample_size"),
        base_expectancy_usdt=_recovery_decimal(row, "base_expectancy_usdt", required=True),
        stress_expectancy_usdt=_recovery_decimal(row, "stress_expectancy_usdt", required=True),
        base_profit_factor=_recovery_decimal(row, "base_profit_factor", required=True),
        stress_profit_factor=_recovery_decimal(row, "stress_profit_factor", required=True),
        sample_span_days=_recovery_float(row, "sample_span_days", required=True) or 0.0,
        regime_count=_strict_recovery_int(row, "regime_count"),
        dsr_probability=_recovery_float(row, "dsr_probability", required=True),
        pbo=_recovery_float(row, "pbo", required=True),
        champion_expectancy_usdt=_recovery_decimal(row, "champion_expectancy_usdt"),
        oos_expectancy_lower_bound_usdt=_recovery_decimal(
            row, "oos_expectancy_lower_bound_usdt", required=True
        ),
        recent_expectancy_usdt=_recovery_decimal(row, "recent_expectancy_usdt"),
        recent_profit_factor=_recovery_decimal(row, "recent_profit_factor"),
        recent_stress_expectancy_usdt=_recovery_decimal(row, "recent_stress_expectancy_usdt"),
        recent_stress_profit_factor=_recovery_decimal(row, "recent_stress_profit_factor"),
        parameter_robustness_passed=_strict_recovery_bool(row, "parameter_robustness_passed"),
        risk_contract_passed=_strict_recovery_bool(row, "risk_contract_passed"),
        independent_period_count=_strict_recovery_int(row, "independent_period_count"),
        live_public_sample_size=_strict_recovery_int(row, "live_public_sample_size"),
        cooldown_elapsed=_strict_recovery_bool(row, "cooldown_elapsed"),
        strategy_correlation_abs=_recovery_float(row, "strategy_correlation_abs", required=True),
        full_oos_degraded_evaluations=_strict_recovery_int(row, "full_oos_degraded_evaluations"),
        recent_oos_degraded_evaluations=_strict_recovery_int(
            row, "recent_oos_degraded_evaluations"
        ),
        data_leakage=_strict_recovery_bool(row, "data_leakage"),
        ledger_contamination=_strict_recovery_bool(row, "ledger_contamination"),
        abnormal_order_loop=_strict_recovery_bool(row, "abnormal_order_loop"),
        evaluation_period=evaluation_period,
        evaluated_ts_ms=_strict_recovery_int(row, "evaluated_ts_ms"),
        operational_health_passed=_strict_recovery_bool(row, "operational_health_passed"),
        operational_health_evaluated_ts_ms=_strict_recovery_int(
            row, "operational_health_evaluated_ts_ms"
        ),
        data_fault=_strict_recovery_bool(row, "data_fault"),
        operational_fault=_strict_recovery_bool(row, "operational_fault"),
        drawdown_breach=_strict_recovery_bool(row, "drawdown_breach"),
        base_win_rate=_recovery_decimal(row, "base_win_rate"),
        stress_win_rate=_recovery_decimal(row, "stress_win_rate"),
        unique_opportunity_count=_strict_recovery_int(row, "unique_opportunity_count"),
        base_win_rate_ci95_lower=_recovery_decimal(row, "base_win_rate_ci95_lower"),
        stress_win_rate_ci95_lower=_recovery_decimal(row, "stress_win_rate_ci95_lower"),
        base_payoff_ratio=_recovery_decimal(row, "base_payoff_ratio"),
        stress_payoff_ratio=_recovery_decimal(row, "stress_payoff_ratio"),
        base_return_skew=_recovery_decimal(row, "base_return_skew"),
        stress_return_skew=_recovery_decimal(row, "stress_return_skew"),
        base_largest_trade_contribution=_recovery_decimal(row, "base_largest_trade_contribution"),
        stress_largest_trade_contribution=_recovery_decimal(
            row, "stress_largest_trade_contribution"
        ),
        base_cost_coverage=_recovery_decimal(row, "base_cost_coverage"),
        stress_cost_coverage=_recovery_decimal(row, "stress_cost_coverage"),
    )


_MARKET_PERSISTENCE_FLUSH_THRESHOLD = 250
_MARKET_PERSISTENCE_BATCH_SIZE = 250
_SLOW_PERSISTENCE_FLUSH_MS = 2_000.0
_STORAGE_HEALTH_REFRESH_SECONDS = 1.0
_STORAGE_HEALTH_STALE_NS = 5_000_000_000
_PAPER_LEVERAGE_CHOICES = (1, 2, 3, 5, 10, 20, 25, 50, 75, 100)
_DEFAULT_PAPER_LEVERAGE = Decimal("10")
_PAPER_RESEARCH_SETTING_KEY = "paper_research_configuration_v1"
_MAINTENANCE_PAUSE_PREFIXES = (
    "DEPLOYMENT_MAINTENANCE_",
    "LEDGER_MAINTENANCE_",
)


class PaperEntryIntentConflict(RuntimeError):
    """오래된 화면 또는 충돌한 재전송이 PAPER 진입 의도를 덮어쓰지 못하게 한다."""

    def __init__(
        self,
        *,
        error_code: str,
        expected_revision: int | None,
        current_revision: int,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.expected_revision = expected_revision
        self.current_revision = current_revision


class PaperResearchConfigurationConflict(RuntimeError):
    """오래된 설정 화면이 PAPER 연구 배수를 덮어쓰지 못하게 한다."""

    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__("PAPER_RESEARCH_CONFIGURATION_REVISION_CONFLICT")
        self.expected_revision = expected_revision
        self.current_revision = current_revision


@dataclass(frozen=True, slots=True)
class _RecoveredStrategySetting:
    """복구 원장 행을 live registry에 적용하기 전에 완전히 파싱한다."""

    source: Mapping[str, object]
    strategy_id: str
    mode: StrategyMode
    lifecycle: StrategyLifecycle
    long_enabled: bool
    short_enabled: bool
    revision: int
    manual_lock: bool
    changed_by: StrategyChangeSource
    change_reason: str
    updated_ts_ms: int
    recovery_row_token: str


@dataclass(frozen=True, slots=True)
class _IgnoredRecoveryStrategyRevision:
    """상태에는 적용하지 않고 immutable revision cursor에만 남길 원장 행이다."""

    source: Mapping[str, object]
    strategy_id: str
    revision: int
    updated_ts_ms: int
    recovery_row_token: str


_WAL_CHECKPOINT_FLUSH_INTERVAL = 4
_SLOW_WAL_CHECKPOINT_MS = 2_000.0
_WAL_CHECKPOINT_SOFT_BYTES = 16 * 1024 * 1024
_MAX_WAL_BYTES_WITHOUT_CHECKPOINT = 64 * 1024 * 1024
_MAX_WAL_FRAMES_WITHOUT_CHECKPOINT = _MAX_WAL_BYTES_WITHOUT_CHECKPOINT // 4_096
_PERSISTENCE_BACKLOG_ENTRY_LOCK_EVENTS = 10_000
_PERSISTENCE_BACKLOG_RECOVERY_EVENTS = 2_000
_PERSISTED_CANDLE_INTERVALS = frozenset({1, 180})
_LIVE_WIDE_SYMBOL_TARGET = 80
_LIVE_DEEP_SYMBOL_TARGET = 16
_LIVE_DASHBOARD_EVENT_LIMIT = 512
_DEFAULT_EVENT_MEMORY_LIMIT = 10_000
_LIVE_EVENT_MEMORY_LIMIT = 2_048
_STRATEGY_EVALUATION_QUEUE_HIGH_WATER_DIVISOR = 2
_STRATEGY_EVALUATION_QUEUE_HIGH_WATER_MAX = 64
_STRATEGY_EVALUATION_QUEUE_LOW_WATER_DIVISOR = 4
_DIRECTIONAL_CHANGE_PROFILES = (
    ("FAST", Decimal("0.0050")),
    ("SWING", Decimal("0.0100")),
)
_DIRECTIONAL_CHANGE_SYMBOL_LIMIT = _LIVE_DEEP_SYMBOL_TARGET * 2
_DIRECTIONAL_CHANGE_DEDUPE_CAPACITY = 256
_SEMIVARIANCE_SYMBOL_LIMIT = _LIVE_DEEP_SYMBOL_TARGET * 2
_SEMIVARIANCE_MINUTE_SECONDS = 60
_SEMIVARIANCE_MINUTE_MS = _SEMIVARIANCE_MINUTE_SECONDS * 1_000
_DEFER_STRATEGY_EVALUATION: ContextVar[bool] = ContextVar(
    "defer_strategy_evaluation",
    default=False,
)
_RECOVERY_STATE_AUDIT_EVENTS = frozenset(
    {
        "MAIN_CANDIDATE_SELECTED",
        "LEAGUE_CANDIDATE_ARMED",
        "MAIN_MANUAL_EXIT_PENDING",
        "MANAGEMENT_EXIT_ARMED",
        "ENTRY_EXPIRED",
        "ENTRY_REJECTED",
        "ENTRY_UNFILLED",
        "ENTRY_FILLED",
        "FORCED_EXIT_PENDING",
        "STOP_EXIT_PENDING",
        "TAKE_PROFIT_EXIT_PENDING",
        "TRAILING_STATE_TRANSITION",
        "TRAILING_MARK_UPDATED",
        "TRAILING_EDGE_STATE_UPDATED",
        "TRAIL_EXIT_PENDING",
        "EXIT_REJECTED",
        "EXIT_UNFILLED",
        "EXIT_FILL",
    }
)


@dataclass(frozen=True, slots=True)
class _PreparedStrategyEvaluation:
    """LIVE 이벤트 루프 밖에서 평가할 불변 전략 입력을 보존한다."""

    event: MarketEvent
    snapshot: FeatureSnapshot
    regime: Regime
    book: BookSnapshot
    strategy_registry: StrategyRegistry
    settings_revisions: tuple[tuple[str, int], ...]
    tick_size: Decimal
    fifteen_minute_candles: tuple[Candle, ...]
    thirty_minute_candles: tuple[Candle, ...]
    hourly_candles: tuple[Candle, ...]


@dataclass(slots=True)
class PaperRuntime:
    mode: RuntimeMode = RuntimeMode.READY
    clock: Clock = field(default_factory=SystemClock)
    run_id: str = "ready"
    _events: deque[MarketEvent] = field(
        default_factory=lambda: deque(maxlen=_DEFAULT_EVENT_MEMORY_LIMIT),
    )
    paused: bool = True
    position_visible: bool = False
    archived_run_ids: list[str] = field(default_factory=list)
    control_logs: list[dict[str, object]] = field(default_factory=list)
    ledger: SQLiteLedger | None = None
    storage_guard: ParquetEventStore | None = None
    market_event_archive: ParquetEventStore | None = None
    venue: Venue = Venue.NONE
    market_data_state: MarketDataState = MarketDataState.DISCONNECTED
    wide_symbol_count: int = 0
    deep_symbol_count: int = 0
    processing_lag_p95_ms: float | None = None
    runtime_health_flags: list[str] = field(default_factory=list)
    unrealized_pnl_usdt: float = 0.0
    candle_builder: CandleBuilder = field(default_factory=CandleBuilder)
    hourly_public_history: dict[str, tuple[Candle, ...]] = field(default_factory=dict)
    strategy_public_history: dict[tuple[str, int], tuple[Candle, ...]] = field(default_factory=dict)
    _strategy_candle_cache: dict[
        tuple[str, int], tuple[tuple[int, int | None, int, int | None], tuple[Candle, ...]]
    ] = field(default_factory=dict, repr=False)
    selected_symbol: str = "BTCUSDT"
    selected_interval_seconds: int = 180
    live_selection: ProviderSelection | None = None
    _supervisor: PersistentPublicSupervisor | None = field(default=None, init=False, repr=False)
    strategy_registry: StrategyRegistry = field(default_factory=StrategyRegistry)
    strategy_governor: StrategyGovernor = field(default_factory=StrategyGovernor)
    _governance_last_sample_size: dict[str, int] = field(default_factory=dict, repr=False)
    _governance_full_degraded_cycles: dict[str, int] = field(default_factory=dict, repr=False)
    _governance_recent_degraded_cycles: dict[str, int] = field(
        default_factory=dict,
        repr=False,
    )
    _governance_last_cycle_ts_ms: int | None = None
    strategy_evaluator: StrategySignalEvaluator = field(default_factory=StrategySignalEvaluator)
    _live_strategy_evaluator: ProcessStrategyEvaluator = field(
        default_factory=ProcessStrategyEvaluator,
        init=False,
        repr=False,
    )
    _live_strategy_process_pid: int | None = field(default=None, init=False, repr=False)
    _strategy_evaluation_lock: RLock = field(
        default_factory=RLock,
        init=False,
        repr=False,
    )
    orderflow_confirmation_runtime: OrderflowConfirmationRuntime = field(
        default_factory=OrderflowConfirmationRuntime,
    )
    regime_classifier: RegimeClassifier = field(default_factory=RegimeClassifier)
    feature_engines: dict[str, FeatureEngine] = field(default_factory=dict)
    latest_features: dict[str, FeatureSnapshot] = field(default_factory=dict)
    latest_regimes: dict[str, Regime] = field(default_factory=dict)
    strategy_signals: dict[tuple[str, str, str], EvaluatedSignal] = field(default_factory=dict)
    strategy_evaluation_count: int = 0
    _strategy_evaluation_backpressure_active: bool = False
    _strategy_evaluation_backpressure_skip_count: int = 0
    _strategy_evaluation_backpressure_resume_count: int = 0
    _directional_change_engines: dict[tuple[str, str], DirectionalChangeEngine] = field(
        default_factory=dict,
        repr=False,
    )
    _directional_change_symbols: dict[str, None] = field(default_factory=dict, repr=False)
    _directional_change_initialized: dict[str, bool] = field(
        default_factory=lambda: {"FAST": False, "SWING": False},
        repr=False,
    )
    _directional_change_event_counts: dict[str, int] = field(
        default_factory=lambda: {"FAST": 0, "SWING": 0},
        repr=False,
    )
    _directional_change_last_directions: dict[str, DCState] = field(
        default_factory=lambda: {
            "FAST": DCState.UNINITIALIZED,
            "SWING": DCState.UNINITIALIZED,
        },
        repr=False,
    )
    _directional_change_last_confirmation_types: dict[str, str] = field(
        default_factory=lambda: {"FAST": "NONE", "SWING": "NONE"},
        repr=False,
    )
    _semivariance_symbols: dict[str, None] = field(default_factory=dict, repr=False)
    _semivariance_engines: dict[str, SemivarianceJumpEngine] = field(
        default_factory=dict,
        repr=False,
    )
    _semivariance_previous_completed_closes: dict[str, tuple[int, Decimal]] = field(
        default_factory=dict,
        repr=False,
    )
    _semivariance_latest_snapshots: dict[str, SemivarianceJumpSnapshot] = field(
        default_factory=dict,
        repr=False,
    )
    _semivariance_last_symbol: str = "NONE"
    _semivariance_last_completed_minute_ts_ms: int | None = None
    _semivariance_last_status: str = "WAITING_COMPLETED_MINUTE"
    _semivariance_last_reset_reason: str = "NONE"
    qualified_signal_count: int = 0
    shadow_ledger: ShadowLedger = field(init=False)
    paper_portfolio: PaperPortfolioEngine = field(init=False)
    latest_books: dict[str, BookSnapshot] = field(default_factory=dict)
    candidate_planner: CandidatePlanner = field(default_factory=CandidatePlanner)
    plan_rejections: deque[dict[str, object]] = field(
        default_factory=lambda: deque(maxlen=2_000),
    )
    data_gap_since_ms: dict[str, int] = field(default_factory=dict)
    _stale_trade_symbols: set[str] = field(default_factory=set, repr=False)
    _strategy_data_health_epoch: int = field(default=0, repr=False)
    _feature_input_fault_symbols: set[str] = field(default_factory=set, repr=False)
    strategy_evaluation_interval_ms: int = 2_000
    _last_strategy_evaluation_ms: dict[str, int] = field(default_factory=dict)
    _market_event_buffer: list[dict[str, object]] = field(default_factory=list)
    _candle_buffer: list[dict[str, object]] = field(default_factory=list)
    _candidate_plan_buffer: list[dict[str, object]] = field(default_factory=list)
    _universe_snapshot_buffer: list[dict[str, object]] = field(default_factory=list)
    _persisted_main_order_ids: set[str] = field(default_factory=set)
    _persisted_main_trade_ids: set[str] = field(default_factory=set)
    _persisted_shadow_trade_ids: set[str] = field(default_factory=set)
    _persisted_audit_count: int = 0
    _persistence_fault_count: int = 0
    _persistence_fault_active: bool = False
    _persistence_fault_recoverable: bool = False
    _persistence_recovery_count: int = 0
    _persistence_last_recovered_ts_ms: int | None = None
    _last_recovered_persistence_error: str | None = None
    _persistence_buffer_dropped: int = 0
    _persistence_backlog_peak: int = 0
    _persistence_backlog_entry_lock_count: int = 0
    _last_persistence_error: str | None = None
    _persistence_flush_count: int = 0
    _persistence_flush_last_ms: float = 0.0
    _persistence_flush_max_ms: float = 0.0
    _persistence_flush_last_completed_ts_ms: int | None = None
    _persistence_flush_max_ts_ms: int | None = None
    _persistence_flush_slow_count: int = 0
    _persistence_flush_last_slow_ts_ms: int | None = None
    _persistence_flush_slowest_gate_wait_ms: float = 0.0
    _persistence_flush_slowest_archive_ms: float = 0.0
    _persistence_flush_slowest_ledger_ms: float = 0.0
    _persistence_flush_slowest_ledger_connect_ms: float = 0.0
    _persistence_flush_slowest_ledger_begin_wait_ms: float = 0.0
    _persistence_flush_slowest_ledger_write_ms: float = 0.0
    _persistence_flush_slowest_ledger_commit_ms: float = 0.0
    _persistence_flush_slowest_ledger_close_ms: float = 0.0
    _persistence_flush_slowest_market_events: int = 0
    _persistence_flush_slowest_candles: int = 0
    _persistence_flush_slowest_archive_batches: int = 0
    _execution_persistence_count: int = 0
    _execution_persistence_last_ms: float = 0.0
    _execution_persistence_max_ms: float = 0.0
    _execution_persistence_last_completed_ts_ms: int | None = None
    _execution_persistence_max_ts_ms: int | None = None
    _execution_persistence_last_items: int = 0
    _live_event_processing_count: int = 0
    _live_event_processing_last_ms: float = 0.0
    _live_event_processing_max_ms: float = 0.0
    _live_event_processing_over_100ms_count: int = 0
    _live_event_processing_max_ts_ms: int | None = None
    _live_event_processing_max_event_type: str = "NONE"
    _live_event_processing_max_symbol: str = "NONE"
    _live_event_phase_last_ms: dict[str, float] = field(default_factory=dict)
    _live_event_phase_max_ms: float = 0.0
    _live_event_phase_max_name: str = "NONE"
    _live_event_phase_max_ts_ms: int | None = None
    _live_event_phase_max_event_type: str = "NONE"
    _live_event_phase_max_symbol: str = "NONE"
    _live_event_phase_over_100ms_count: int = 0
    _wal_checkpoint_next_flush: int = _WAL_CHECKPOINT_FLUSH_INTERVAL
    _wal_checkpoint_count: int = 0
    _wal_checkpoint_last_ms: float = 0.0
    _wal_checkpoint_max_ms: float = 0.0
    _wal_checkpoint_slow_count: int = 0
    _wal_checkpoint_busy_count: int = 0
    _wal_checkpoint_log_frames: int = 0
    _wal_checkpointed_frames: int = 0
    _wal_checkpoint_last_completed_ts_ms: int | None = None
    _wal_checkpoint_fault_count: int = 0
    _wal_checkpoint_last_error: str | None = None
    _wal_checkpoint_deferred_count: int = 0
    _wal_checkpoint_last_wal_bytes: int = 0
    _wal_checkpoint_probe_log_frames: int = -1
    _wal_checkpoint_probe_checkpointed_frames: int = -1
    _wal_checkpoint_probe_page_size: int = 4_096
    _wal_checkpoint_pending_bytes: int = 0
    _wal_checkpoint_task: asyncio.Task[tuple[int, int, int]] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _wal_checkpoint_task_started_at: float | None = None
    _wal_checkpoint_task_started_flush_count: int = 0
    _wal_checkpoint_last_concurrent_flush_delta: int = 0
    _wal_checkpoint_max_concurrent_flush_delta: int = 0
    _persistence_worker_warmed: bool = False
    _persistence_worker_warm_ms: float = 0.0
    _universe_snapshot_persisted_count: int = 0
    _universe_snapshot_persistence_last_ms: float = 0.0
    _universe_snapshot_persistence_max_ms: float = 0.0
    _historical_live_trades: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _historical_prior_version_live_trades: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _historical_shadow_trades: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _historical_prior_version_shadow_trades: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _historical_all_main_trades: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _historical_all_shadow_trades: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _historical_replay_run_summaries: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _replay_run_persisted_deltas: dict[str, int] = field(default_factory=dict, repr=False)
    _dashboard_strategy_performance_cache_key: tuple[object, ...] | None = field(
        default=None, repr=False
    )
    _dashboard_strategy_performance_cache: tuple[dict[str, object], ...] = field(
        default_factory=tuple, repr=False
    )
    _strategy_arbitration_evidence_cache: dict[str, SharedCapitalArbitrationEvidence] = field(
        default_factory=dict, repr=False
    )
    _strategy_arbitration_evidence_ready: bool = False
    dashboard_trade_cache_ready: bool = False
    dashboard_trade_cache_loading: bool = False
    dashboard_trade_cache_last_ms: float = 0.0
    dashboard_trade_cache_completed_ts_ms: int | None = None
    _last_storage_check_ns: int | None = None
    _storage_entry_allowed: bool = True
    _storage_health_snapshot: dict[str, object] = field(default_factory=dict)
    _storage_health_refresh_count: int = 0
    _storage_health_refresh_last_ms: float = 0.0
    _storage_health_refresh_max_ms: float = 0.0
    _storage_health_refresh_completed_ts_ms: int | None = None
    _recovery_revalidation_symbols: set[str] = field(default_factory=set)
    _recovery_ignored_governance_row_tokens: tuple[str, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    _recovery_reserved_governance_revisions: tuple[dict[str, object], ...] = field(
        default_factory=tuple,
        repr=False,
    )
    _manual_pause_requested: bool = False
    _paper_entry_intent_revision: int = 0
    _paper_entry_intent_actor: str = "RECOVERY"
    _paper_entry_intent_reason: str = "INITIAL_STATE"
    _paper_entry_intent_updated_ts_ms: int | None = None
    _paper_entry_intent_idempotency: dict[str, bool] = field(
        default_factory=dict,
        repr=False,
    )
    _selected_margin_leverage: Decimal = _DEFAULT_PAPER_LEVERAGE
    _paper_research_configuration_revision: int = 0
    _paper_research_configuration_updated_ts_ms: int | None = None
    startup_storage_init_ms: float = 0.0
    startup_ledger_open_ms: float = 0.0
    startup_recovery_lookup_ms: float = 0.0
    startup_runtime_init_ms: float = 0.0
    startup_recovery_restore_ms: float = 0.0
    startup_recovery_audit: dict[str, object] = field(default_factory=dict, repr=False)
    startup_total_ms: float = 0.0
    startup_portfolio_init_ms: float = 0.0
    startup_trade_cache_ms: float = 0.0
    startup_post_init_total_ms: float = 0.0
    resource_sampler: ProcessResourceSampler = field(init=False, repr=False)
    _persistence_lock: RLock = field(default_factory=RLock, repr=False)
    _dashboard_trade_cache_lock: RLock = field(default_factory=RLock, repr=False)
    _paper_entry_intent_lock: RLock = field(default_factory=RLock, repr=False)
    _paper_research_configuration_lock: RLock = field(default_factory=RLock, repr=False)

    def _restore_paper_research_configuration(self) -> None:
        """전역 PAPER 연구 배수를 원장에서 복구하고 기본 10배를 유지한다."""

        if self.ledger is None:
            return
        stored = self.ledger.get_app_setting(_PAPER_RESEARCH_SETTING_KEY)
        if stored is None:
            return
        leverage_value = stored.get("selected_leverage")
        revision_value = stored.get("revision")
        updated_value = stored.get("updated_ts_ms")
        if (
            isinstance(leverage_value, bool)
            or not isinstance(leverage_value, int)
            or leverage_value not in _PAPER_LEVERAGE_CHOICES
        ):
            raise LedgerInvariantError("저장된 PAPER 레버리지 설정이 허용 범위를 벗어났습니다.")
        if (
            isinstance(revision_value, bool)
            or not isinstance(revision_value, int)
            or revision_value < 0
        ):
            raise LedgerInvariantError("저장된 PAPER 레버리지 revision이 잘못됐습니다.")
        if (
            isinstance(updated_value, bool)
            or not isinstance(updated_value, int)
            or updated_value < 0
        ):
            raise LedgerInvariantError("저장된 PAPER 레버리지 변경 시각이 잘못됐습니다.")
        self._selected_margin_leverage = Decimal(leverage_value)
        self._paper_research_configuration_revision = revision_value
        self._paper_research_configuration_updated_ts_ms = updated_value

    def _continuous_limits(self, base: RiskLimits) -> RiskLimits:
        """시간·손실 quota는 풀되 계획손실·호가·낙폭 안전경계는 유지한다."""

        return replace(
            base,
            max_daily_trades=None,
            daily_loss_limit_fraction=None,
            weekly_loss_limit_fraction=None,
            maximum_gross_notional_fraction=self._selected_margin_leverage,
            loss_cooldowns_enabled=False,
        )

    def _new_paper_portfolio(
        self,
        shadow_ledger: ShadowLedger,
        *,
        strategy_registry: StrategyRegistry | None = None,
    ) -> PaperPortfolioEngine:
        registry = strategy_registry or self.strategy_registry
        return PaperPortfolioEngine(
            run_id=self.run_id,
            strategy_ids=registry.strategy_ids,
            shadow_ledger=shadow_ledger,
            venue=self.venue,
            risk_manager=RiskManager(self._continuous_limits(RiskLimits())),
            league_risk_manager=RiskManager(self._continuous_limits(STRATEGY_LEAGUE_RISK_LIMITS)),
            selected_margin_leverage=self._selected_margin_leverage,
            enforce_v6_family_conflicts=True,
        )

    def __post_init__(self) -> None:
        post_init_started = time.monotonic()
        assert_paper_only(self.mode, os.environ)
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            # LIVE 원본은 archive에 전량 보존하므로 화면용 메모리는 짧게 유지한다.
            self._events = deque(self._events, maxlen=_LIVE_EVENT_MEMORY_LIMIT)
        storage_path = self.ledger.path.parent if self.ledger is not None else Path.cwd()
        self.resource_sampler = ProcessResourceSampler(storage_path)
        self._restore_paper_research_configuration()
        portfolio_started = time.monotonic()
        self.shadow_ledger = ShadowLedger(self.strategy_registry.strategy_ids)
        self.paper_portfolio = self._new_paper_portfolio(self.shadow_ledger)
        self.startup_portfolio_init_ms = (time.monotonic() - portfolio_started) * 1_000
        if self.mode is RuntimeMode.READY:
            self.run_id = "ready"
            self.venue = Venue.NONE
            self.market_data_state = MarketDataState.DISCONNECTED
            self.paused = True
            self.position_visible = False
            self.runtime_health_flags = ["READY_NOT_STARTED"]
        elif self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            if self.venue is Venue.FIXTURE:
                self.venue = Venue.BINANCE_USDM
            if self.venue is Venue.NONE:
                self.venue = Venue.BINANCE_USDM
            self.market_data_state = MarketDataState.DISCONNECTED
            self.paused = True
            self.position_visible = False
            self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        elif self.mode is RuntimeMode.DEMO_FIXTURE:
            self.venue = Venue.FIXTURE
            self.market_data_state = MarketDataState.FIXTURE
            self.paused = False
            self.position_visible = True
            self.runtime_health_flags = ["OFFLINE_DEMO_ISOLATED"]
        elif self.mode is RuntimeMode.REPLAY:
            self.paused = True
            self.position_visible = False
            self.runtime_health_flags = ["REPLAY_READ_ONLY"]
        self.paper_portfolio.venue = self.venue
        if (
            self.mode is not RuntimeMode.READY
            and self.ledger is not None
            and self.ledger.get_run(self.run_id) is None
        ):
            self._start_ledger_run()
            self._paper_entry_intent_updated_ts_ms = self.clock.utc_ms()
            self._persist_paper_entry_intent(updated_ts_ms=self._paper_entry_intent_updated_ts_ms)
        elif self.ledger is not None and self.mode is not RuntimeMode.READY:
            # 대용량 원장의 과거 거래·Replay 색인은 FastAPI lifespan에서
            # 백그라운드로 준비해 HTTP 포트 개방과 복구 상태 확인을 막지 않는다.
            self.dashboard_trade_cache_ready = False
        self.startup_post_init_total_ms = (time.monotonic() - post_init_started) * 1_000

    def boot_demo(self, event_count: int = 240) -> None:
        if self.mode is not RuntimeMode.DEMO_FIXTURE:
            raise ValueError("fixture 부팅은 DEMO_FIXTURE 모드에서만 가능합니다.")
        generator = FixtureMarketData(self.clock, self.run_id)
        events = tuple(generator.events(event_count))
        self._events.extend(events)
        for index, event in enumerate(events):
            bid = Decimal(str(event.data["bid"]))
            ask = Decimal(str(event.data["ask"]))
            self.candle_builder.add(
                TradeTick(
                    venue=event.venue,
                    symbol=event.symbol,
                    price=(bid + ask) / Decimal(2),
                    quantity=Decimal("0.1") + Decimal(index % 5) / Decimal(100),
                    trade_ts_ms=event.venue_ts_ms,
                    buyer_is_aggressor=index % 2 == 0,
                    event_id=event.event_id,
                )
            )
        if self.ledger is not None:
            with self._persistence_lock:
                self._market_event_buffer.extend(
                    self._persistable_market_event(event) for event in events
                )
            fixture_symbols = sorted({event.symbol for event in events})
            self._buffer_completed_candles(
                [
                    candle
                    for symbol in fixture_symbols
                    for interval in (1, 180)
                    for candle in self.candle_builder.series(symbol, interval)
                ]
            )
            self._flush_persistence()
        self._ensure_fixture_completed_trade()

    def boot_fixture(self, event_count: int = 240) -> None:
        """0.1 호출 호환용 별칭이며 DEMO_FIXTURE에서만 동작한다."""

        self.boot_demo(event_count)

    @property
    def events(self) -> tuple[MarketEvent, ...]:
        return tuple(self._events)

    @property
    def _recovery_revalidation_symbol(self) -> str | None:
        """기존 단일 포지션 검증 계약에 대한 읽기 호환 속성이다."""

        return next(iter(sorted(self._recovery_revalidation_symbols)), None)

    def status(self) -> SystemStatus:
        symbols = (
            set(self.live_selection.wide_symbols)
            if self.mode is RuntimeMode.LIVE_SHADOW_PAPER and self.live_selection is not None
            else {event.symbol for event in self._events}
        )
        realized = 0.0
        fees = 0.0
        slippage = 0.0
        trade_count = 0
        current_equity = 1000.0
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            summary = self.paper_portfolio.main_summary(self._current_main_book())
            realized = float(summary["realized"])
            self.unrealized_pnl_usdt = float(summary["unrealized"])
            fees = float(summary["fees"])
            slippage = float(summary["slippage"])
            trade_count = int(summary["trade_count"])
            current_equity = float(summary["equity"])
        elif self.ledger is not None:
            trades = self.ledger.list_trades(self.run_id)
            trade_count = len(trades)
            realized = sum(float(str(trade["net_pnl_usdt"])) for trade in trades)
            fees = sum(float(str(trade["fees_usdt"])) for trade in trades)
            slippage = sum(float(str(trade["slippage_usdt"])) for trade in trades)
            current_equity = 1000.0 + realized + self.unrealized_pnl_usdt
        flags = list(self.runtime_health_flags)
        if self.paused:
            flags.append("PAPER_ENTRIES_PAUSED")
        return SystemStatus(
            mode=self.mode,
            market_data_state=self.market_data_state,
            venue=self.venue,
            run_id=self.run_id,
            current_equity_usdt=current_equity,
            realized_pnl_usdt=realized,
            unrealized_pnl_usdt=self.unrealized_pnl_usdt,
            cumulative_fees_usdt=fees,
            cumulative_slippage_usdt=slippage,
            trade_count=trade_count,
            wide_symbols=self.wide_symbol_count or len(symbols),
            deep_symbols=self.deep_symbol_count or min(len(symbols), 10),
            processing_lag_p95_ms=self.processing_lag_p95_ms,
            health_flags=tuple(flags),
        )

    def replay_live_safety_snapshot(self) -> ReplayLiveSafetySnapshot:
        """대용량 replay가 양보할 LIVE PAPER 최소 상태만 가볍게 읽는다."""

        from backend.app.replay.safety import ReplayLiveSafetySnapshot

        self._refresh_storage_safety()
        telemetry = self._supervisor.telemetry if self._supervisor is not None else None
        status = self.status()
        started_monotonic_ns = telemetry.started_monotonic_ns if telemetry is not None else None
        position_count = len(self.paper_portfolio.main.positions) + sum(
            len(account.positions) for account in self.paper_portfolio.shadows.values()
        )
        return ReplayLiveSafetySnapshot(
            run_id=self.run_id,
            runtime_mode=self.mode.value,
            operation_state="SAFETY_WAITING" if self.paused else "RUNNING",
            market_data_state=self.market_data_state.value,
            execution_state=status.execution_state.value,
            process_uptime_seconds=(
                max(0.0, (self.clock.monotonic_ns() - started_monotonic_ns) / 1_000_000_000)
                if started_monotonic_ns is not None
                else 0.0
            ),
            event_count=telemetry.event_count if telemetry is not None else len(self._events),
            queue_depth=telemetry.queue_depth if telemetry is not None else 0,
            lag_p95_ms=float(
                telemetry.lag_p95_ms
                if telemetry is not None and telemetry.lag_p95_ms is not None
                else self.processing_lag_p95_ms or 0.0
            ),
            reconnects=telemetry.reconnect_count if telemetry is not None else 0,
            planned_rotations=(telemetry.planned_rotation_count if telemetry is not None else 0),
            unplanned_reconnects=(
                max(0, telemetry.reconnect_count - telemetry.planned_rotation_count)
                if telemetry is not None
                else 0
            ),
            sequence_gaps=telemetry.gap_count if telemetry is not None else 0,
            resyncs=telemetry.resync_count if telemetry is not None else 0,
            dropped_events=(telemetry.dropped_event_count if telemetry is not None else 0),
            persistence_fault_count=self._persistence_fault_count,
            persistence_buffer_dropped=self._persistence_buffer_dropped,
            event_loop_lag_over_500ms_count=(
                telemetry.event_loop_lag_over_500ms_count if telemetry is not None else 0
            ),
            critical_lag_incident_count=(
                telemetry.critical_lag_incident_count if telemetry is not None else 0
            ),
            critical_lag_active=(telemetry.critical_lag_active if telemetry is not None else False),
            entry_locked=(
                (telemetry.entry_locked if telemetry is not None else False) or self.paused
            ),
            position_count=position_count,
            storage_entry_allowed=self._storage_entry_allowed,
            real_orders_enabled=status.real_orders_enabled,
            auth_required=status.auth_required,
            last_error=telemetry.last_error if telemetry is not None else None,
        )

    def _validated_governor_active_recovery_revision(
        self,
        setting_row: Mapping[str, object],
        *,
        recovery_ts_ms: int,
    ) -> bool:
        """현재 release·Run·gate·60초 계약을 모두 만족한 ACTIVE revision만 인정한다."""

        try:
            strategy_id = str(setting_row["strategy_id"])
            descriptor = self.strategy_registry.descriptor(strategy_id)
            if (
                setting_row.get("mode") != StrategyMode.ACTIVE.value
                or setting_row.get("lifecycle") != StrategyLifecycle.ACTIVE.value
                or setting_row.get("changed_by") != StrategyChangeSource.AUTO_GOVERNOR.value
                or setting_row.get("manual_lock") is not False
                or setting_row.get("change_reason") != "CHALLENGER_BEATS_CHAMPION"
                or setting_row.get("run_id") != self.run_id
                or descriptor.role is not StrategyRole.ENTRY
                or not descriptor.is_current_variant
                or not descriptor.default_research_enabled
                or self.strategy_registry.is_policy_retired(strategy_id)
            ):
                return False
            revision = _strict_recovery_int(setting_row, "settings_revision")
            setting_ts_ms = _strict_recovery_int(setting_row, "settings_updated_ts_ms")
            persisted_ts_ms = _strict_recovery_int(setting_row, "ts_ms")
            change_evidence = setting_row.get("change_evidence")
            if not isinstance(change_evidence, Mapping):
                return False
            assessment = change_evidence.get("assessment")
            evidence_row = change_evidence.get("evidence")
            lineage = change_evidence.get("lineage")
            if (
                not isinstance(assessment, Mapping)
                or not isinstance(evidence_row, Mapping)
                or not isinstance(lineage, Mapping)
            ):
                return False
            assessment_ts_ms = _strict_recovery_int(lineage, "assessment_ts_ms")
            release_commit = git_commit()
            persisted_run = self.ledger.get_run(self.run_id) if self.ledger is not None else None
            if persisted_run is None or not isinstance(persisted_run.get("config_json"), str):
                return False
            run_config = json.loads(str(persisted_run["config_json"]))
            if not isinstance(run_config, Mapping):
                return False
            if (
                lineage.get("schema_version") != 1
                or lineage.get("run_id") != self.run_id
                or lineage.get("strategy_id") != strategy_id
                or lineage.get("strategy_version") != STRATEGY_VERSION
                or lineage.get("descriptor_strategy_version")
                != descriptor.research_contract.strategy_version
                or lineage.get("app_version") != APP_VERSION
                or release_commit == "UNAVAILABLE"
                or lineage.get("release_commit") != release_commit
                or persisted_run.get("run_id") != self.run_id
                or persisted_run.get("mode") != RuntimeMode.LIVE_SHADOW_PAPER.value
                or persisted_run.get("venue") != Venue.BINANCE_USDM.value
                or run_config.get("execution") != "PAPER"
                or run_config.get("sample_type") != "LIVE_PUBLIC"
                or run_config.get("app_version") != APP_VERSION
                or run_config.get("strategy_version") != STRATEGY_VERSION
                or run_config.get("git_commit") != release_commit
                or _strict_recovery_int(lineage, "settings_revision") != revision
                or assessment_ts_ms != setting_ts_ms
                or assessment_ts_ms != persisted_ts_ms
                or not 0 <= recovery_ts_ms - assessment_ts_ms <= GOVERNANCE_EVIDENCE_MAX_AGE_MS
            ):
                return False
            if (
                assessment.get("strategy_id") != strategy_id
                or assessment.get("current_lifecycle") != StrategyLifecycle.CHALLENGER.value
                or assessment.get("recommended_lifecycle") != StrategyLifecycle.ACTIVE.value
                or assessment.get("reason_codes") != ["CHALLENGER_BEATS_CHAMPION"]
                or assessment.get("automatic_action_allowed") is not True
                or assessment.get("transition_required") is not True
            ):
                return False
            evidence = _governance_evidence_from_recovery(evidence_row)
            if any(
                (
                    evidence.data_fault,
                    evidence.operational_fault,
                    evidence.drawdown_breach,
                    evidence.data_leakage,
                    evidence.ledger_contamination,
                    evidence.abnormal_order_loop,
                )
            ):
                return False
            supported_regime_count = len(descriptor.supported_regimes)
            common_failures = self.strategy_governor._common_gate_failures(
                evidence,
                assessment_ts_ms=recovery_ts_ms,
            )
            family_failures = self.strategy_governor.family_gate_failures(
                descriptor.family_id,
                evidence,
            )
            shadow_failures = self.strategy_governor._shadow_gate_failures(
                evidence,
                required_regime_count=min(2, supported_regime_count),
            )
            active_failures = self.strategy_governor._active_gate_failures(
                evidence,
                required_regime_count=min(3, supported_regime_count),
            )
            return not (common_failures or family_failures or shadow_failures or active_failures)
        except (KeyError, TypeError, ValueError):
            return False

    def restore_recovery_state(self, recovered: RecoveryState) -> bool:
        """checksum 검증된 최신 Run의 전략 설정·계좌·포지션·거래를 복구한다."""

        if recovered.run_id != self.run_id or recovered.venue != self.venue.value:
            self._lock_recovery("RECOVERY_RUN_OR_VENUE_MISMATCH")
            return False
        if self.ledger is None:
            self._lock_recovery("RECOVERY_LEDGER_MISSING")
            return False
        try:
            staged_persisted_main_order_ids = {
                str(order["order_id"]) for order in self.ledger.list_orders(self.run_id)
            }
            staged_persisted_main_trade_ids = {
                str(trade["trade_id"]) for trade in self.ledger.list_trades(self.run_id)
            }
            staged_persisted_shadow_trade_ids = {
                str(trade["shadow_trade_id"])
                for trade in self.ledger.list_shadow_trades(self.run_id)
            }
            recovery_validation_ts_ms = self.clock.utc_ms()
            staged_strategy_registry = StrategyRegistry()
            staged_shadow_ledger = ShadowLedger(staged_strategy_registry.strategy_ids)
            staged_paper_portfolio = self._new_paper_portfolio(
                staged_shadow_ledger,
                strategy_registry=staged_strategy_registry,
            )
            staged_orderflow_runtime = OrderflowConfirmationRuntime()
            staged_orderflow_runtime.restore_state(
                self.orderflow_confirmation_runtime.recovery_state()
            )
            staged_manual_pause_requested = self._manual_pause_requested
            staged_intent_revision = self._paper_entry_intent_revision
            staged_intent_actor = self._paper_entry_intent_actor
            staged_intent_reason = self._paper_entry_intent_reason
            staged_intent_updated_ts_ms = self._paper_entry_intent_updated_ts_ms
            staged_intent_idempotency = dict(self._paper_entry_intent_idempotency)
            auto_resumed_user_pause = False
            validated_active_tokens: dict[str, dict[int, str]] = {}
            recovery_windows = _fail_closed_recovery_windows(
                self.ledger.list_incidents(category="PAPER_RESTART_RECOVERY"),
                run_id=self.run_id,
            )
            governance_incidents = self.ledger.list_incidents(category="AUTO_GOVERNOR_TRANSITION")
            setting_rows = self.ledger.list_strategy_settings(self.run_id)
            recovery_setting_actions: list[
                _RecoveredStrategySetting | _IgnoredRecoveryStrategyRevision
            ] = []
            seen_recovery_tokens: dict[tuple[str, int], str] = {}
            seen_ignored_tokens: dict[tuple[str, int], str] = {}
            ignored_governance_row_tokens: list[str] = []
            reserved_governance_revisions: list[dict[str, object]] = []
            for setting_row in setting_rows:
                if _is_fail_closed_governance_contamination(
                    setting_row,
                    run_id=self.run_id,
                    windows=recovery_windows,
                    governance_incidents=governance_incidents,
                ):
                    strategy_id = _strict_recovery_setting_text(setting_row, "strategy_id")
                    staged_strategy_registry.descriptor(strategy_id)
                    revision = _strict_recovery_setting_int(setting_row, "settings_revision")
                    updated_ts_ms = _strict_recovery_setting_int(
                        setting_row, "settings_updated_ts_ms"
                    )
                    recovery_row_token = _recovery_row_token(setting_row)
                    revision_key = (strategy_id, revision)
                    previous_token = seen_ignored_tokens.get(revision_key)
                    if previous_token is not None and previous_token != recovery_row_token:
                        raise ValueError(
                            "동일 ignored strategy revision의 복구 원장 행이 다릅니다."
                        )
                    seen_ignored_tokens[revision_key] = recovery_row_token
                    ignored_governance_row_tokens.append(recovery_row_token)
                    recovery_setting_actions.append(
                        _IgnoredRecoveryStrategyRevision(
                            source=setting_row,
                            strategy_id=strategy_id,
                            revision=revision,
                            updated_ts_ms=updated_ts_ms,
                            recovery_row_token=recovery_row_token,
                        )
                    )
                    continue
                if setting_row.get("run_id") != self.run_id:
                    raise ValueError("복구 전략 설정의 Run이 현재 Run과 다릅니다.")
                strategy_id = _strict_recovery_setting_text(setting_row, "strategy_id")
                staged_strategy_registry.descriptor(strategy_id)
                mode = StrategyMode(_strict_recovery_setting_text(setting_row, "mode"))
                lifecycle = (
                    StrategyLifecycle(_strict_recovery_setting_text(setting_row, "lifecycle"))
                    if setting_row.get("lifecycle") is not None
                    else staged_strategy_registry.lifecycle_for_mode(mode)
                )
                if staged_strategy_registry.mode_for_lifecycle(lifecycle) is not mode:
                    raise ValueError("복구 전략 설정의 mode와 lifecycle이 서로 다릅니다.")
                revision = _strict_recovery_setting_int(setting_row, "settings_revision")
                _strict_recovery_setting_int(setting_row, "ts_ms")
                updated_ts_ms = _strict_recovery_setting_int(setting_row, "settings_updated_ts_ms")
                changed_by = StrategyChangeSource(
                    _strict_recovery_setting_text(setting_row, "changed_by")
                )
                change_reason = _strict_recovery_setting_text(setting_row, "change_reason")
                long_enabled = _strict_recovery_setting_bool(setting_row, "long_enabled")
                short_enabled = _strict_recovery_setting_bool(setting_row, "short_enabled")
                manual_lock = _strict_recovery_setting_bool(setting_row, "manual_lock")
                revision_key = (strategy_id, revision)
                recovery_row_token = _recovery_row_token(setting_row)
                previous_token = seen_recovery_tokens.get(revision_key)
                if previous_token is not None and previous_token != recovery_row_token:
                    raise ValueError("동일 strategy settings revision의 복구 원장 행이 다릅니다.")
                seen_recovery_tokens[revision_key] = recovery_row_token
                recovery_setting_actions.append(
                    _RecoveredStrategySetting(
                        source=setting_row,
                        strategy_id=strategy_id,
                        mode=mode,
                        lifecycle=lifecycle,
                        long_enabled=long_enabled,
                        short_enabled=short_enabled,
                        revision=revision,
                        manual_lock=manual_lock,
                        changed_by=changed_by,
                        change_reason=change_reason,
                        updated_ts_ms=updated_ts_ms,
                        recovery_row_token=recovery_row_token,
                    )
                )
            for recovery_action in recovery_setting_actions:
                if isinstance(recovery_action, _IgnoredRecoveryStrategyRevision):
                    reserved_row = staged_strategy_registry.reserve_ignored_recovery_revision(
                        recovery_action.strategy_id,
                        revision=recovery_action.revision,
                        updated_ts_ms=recovery_action.updated_ts_ms,
                        recovery_row_token=recovery_action.recovery_row_token,
                    )
                    if reserved_row is not None:
                        source_ts_ms = _strict_recovery_setting_int(recovery_action.source, "ts_ms")
                        recovery_window = next(
                            (
                                (start_ts_ms, end_ts_ms)
                                for start_ts_ms, end_ts_ms in recovery_windows
                                if start_ts_ms <= source_ts_ms < end_ts_ms
                            ),
                            None,
                        )
                        if recovery_window is None:
                            raise ValueError(
                                "ignored strategy revision의 닫힌 복구 구간이 없습니다."
                            )
                        reserved_governance_revisions.append(
                            {
                                "strategy_id": recovery_action.strategy_id,
                                "ignored_revision": recovery_action.revision,
                                "effective_previous_revision": reserved_row[
                                    "effective_previous_revision"
                                ],
                                "recovery_source_row_token": (recovery_action.recovery_row_token),
                                "ignored_transition_id": recovery_action.source.get(
                                    "transition_id"
                                ),
                                "fail_closed_started_ts_ms": recovery_window[0],
                                "recovery_succeeded_ts_ms": recovery_window[1],
                                "ignored_source_applied": False,
                                "data_deleted": False,
                                "duplicate_revision_relaxed": False,
                            }
                        )
                    continue
                parsed_setting = recovery_action
                valid_governor_active = self._validated_governor_active_recovery_revision(
                    parsed_setting.source,
                    recovery_ts_ms=recovery_validation_ts_ms,
                )
                staged_strategy_registry.restore_setting(
                    parsed_setting.strategy_id,
                    mode=parsed_setting.mode,
                    long_enabled=parsed_setting.long_enabled,
                    short_enabled=parsed_setting.short_enabled,
                    revision=parsed_setting.revision,
                    manual_lock=parsed_setting.manual_lock,
                    changed_by=parsed_setting.changed_by,
                    change_reason=parsed_setting.change_reason,
                    updated_ts_ms=parsed_setting.updated_ts_ms,
                    lifecycle=parsed_setting.lifecycle,
                    recovery_row_token=parsed_setting.recovery_row_token,
                )
                if valid_governor_active:
                    strategy_tokens = validated_active_tokens.setdefault(
                        parsed_setting.strategy_id, {}
                    )
                    existing_token = strategy_tokens.get(parsed_setting.revision)
                    if (
                        existing_token is not None
                        and existing_token != parsed_setting.recovery_row_token
                    ):
                        raise ValueError("동일 ACTIVE settings revision의 검증 원장 행이 다릅니다.")
                    strategy_tokens[parsed_setting.revision] = parsed_setting.recovery_row_token
            retirement_migrations = staged_strategy_registry.enforce_policy_retirements(
                updated_ts_ms=self.clock.utc_ms()
            )
            family_migrations = staged_strategy_registry.enforce_v6_family_runtime_policy(
                updated_ts_ms=self.clock.utc_ms()
            )
            operational_recovery_migrations = (
                staged_strategy_registry.restore_operationally_quarantined_research_defaults(
                    updated_ts_ms=self.clock.utc_ms()
                )
            )
            shadow_migrations = staged_strategy_registry.enforce_unproven_active_defaults(
                updated_ts_ms=self.clock.utc_ms(),
                validated_governor_active_tokens=validated_active_tokens,
            )
            portfolio_payload = recovered.payload.get("portfolio")
            if isinstance(portfolio_payload, Mapping):
                staged_paper_portfolio.restore_state(portfolio_payload)
                staged_paper_portfolio.reconcile_persisted_main_trades(
                    self.ledger.list_trades(self.run_id),
                    as_of_ts_ms=self.clock.utc_ms(),
                )
            elif recovered.lifecycle_state not in {"SCANNING", "CLOSED"}:
                raise ValueError("열린 lifecycle snapshot에 복구 가능한 portfolio가 없습니다.")
            user_intent = self.ledger.get_app_setting("paper_entry_user_intent")
            if user_intent is not None and user_intent.get("run_id") == self.run_id:
                manual_pause = user_intent.get("manual_pause_requested")
                intent_revision = user_intent.get("revision")
                actor = user_intent.get("actor")
                reason = user_intent.get("reason")
                intent_updated_value = user_intent.get("updated_ts_ms")
                if not isinstance(manual_pause, bool):
                    raise ValueError("복구 PAPER 진입 의도의 pause 값이 bool이 아닙니다.")
                if (
                    isinstance(intent_revision, bool)
                    or not isinstance(intent_revision, int)
                    or intent_revision < 0
                ):
                    raise ValueError("복구 PAPER 진입 의도의 revision이 잘못됐습니다.")
                if not isinstance(actor, str) or not actor.strip():
                    raise ValueError("복구 PAPER 진입 의도의 actor가 잘못됐습니다.")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError("복구 PAPER 진입 의도의 reason이 잘못됐습니다.")
                if (
                    isinstance(intent_updated_value, bool)
                    or not isinstance(intent_updated_value, int)
                    or intent_updated_value < 0
                ):
                    raise ValueError("복구 PAPER 진입 의도의 시각이 잘못됐습니다.")
                records = user_intent.get("idempotency_records", [])
                if not isinstance(records, Sequence) or isinstance(records, str | bytes):
                    raise ValueError("복구 PAPER 진입 의도의 idempotency 목록이 잘못됐습니다.")
                idempotency: dict[str, bool] = {}
                for record in records:
                    if not isinstance(record, Mapping):
                        raise ValueError("복구 PAPER 진입 의도의 idempotency 행이 잘못됐습니다.")
                    key = record.get("key")
                    paused = record.get("paused")
                    if not isinstance(key, str) or not key.strip() or key in idempotency:
                        raise ValueError("복구 PAPER 진입 의도의 idempotency key가 잘못됐습니다.")
                    if not isinstance(paused, bool):
                        raise ValueError("복구 PAPER 진입 의도의 idempotency 값이 bool이 아닙니다.")
                    idempotency[key] = paused
                if manual_pause and not reason.startswith(_MAINTENANCE_PAUSE_PREFIXES):
                    manual_pause = False
                    intent_revision += 1
                    actor = "SYSTEM_AUTO_START"
                    reason = "AUTO_ENTRY_ENABLED_ON_RESTART"
                    intent_updated_value = recovery_validation_ts_ms
                    idempotency = {}
                    auto_resumed_user_pause = True
                staged_manual_pause_requested = manual_pause
                staged_intent_revision = intent_revision
                staged_intent_actor = actor
                staged_intent_reason = reason
                staged_intent_updated_ts_ms = intent_updated_value
                staged_intent_idempotency = idempotency
            orderflow_filter = self.ledger.get_app_setting("orderflow_confirmation_filter_v2")
            if orderflow_filter is not None and orderflow_filter.get("run_id") == self.run_id:
                staged_orderflow_runtime.restore_state(orderflow_filter)

            migration_rows = (
                (
                    retirement_migrations,
                    {
                        "policy": "COST_ADJUSTED_RESEARCH_RETIREMENT",
                        "legacy_setting_reactivation_blocked": True,
                    },
                    "STRATEGY_POLICY_MIGRATION",
                ),
                (
                    family_migrations,
                    {
                        "policy": "V6_FAMILY_LEGACY_COMPONENT_HISTORY_ONLY",
                        "history_preserved": True,
                        "new_independent_entry_blocked": True,
                    },
                    "V6_FAMILY_RUNTIME_POLICY_MIGRATION",
                ),
                (
                    operational_recovery_migrations,
                    {
                        "policy": "V9_OPERATIONAL_QUARANTINE_SHADOW_DEFAULT_RECOVERY",
                        "eligible_entry_research_only": True,
                        "user_and_manual_settings_preserved": True,
                        "active_promotion_blocked": True,
                    },
                    "V9_OPERATIONAL_QUARANTINE_RECOVERY_MIGRATION",
                ),
                (
                    shadow_migrations,
                    {
                        "policy": "NO_ACTIVE_STRATEGY_WITHOUT_COST_ADJUSTED_PROOF",
                        "promotion_requires_governor_gates": True,
                    },
                    "STRATEGY_POLICY_MIGRATION",
                ),
            )
            migration_records: list[tuple[dict[str, object], str]] = []
            reservation_by_strategy = {
                str(row["strategy_id"]): row for row in reserved_governance_revisions
            }
            for rows, evidence, category in migration_rows:
                for migrated in rows:
                    migration_ts_ms = int(str(migrated["settings_updated_ts_ms"]))
                    transition = self._strategy_transition_payload(
                        migrated,
                        strategy_registry=staged_strategy_registry,
                    )
                    migration_evidence = dict(evidence)
                    reservation = reservation_by_strategy.get(str(migrated["strategy_id"]))
                    if (
                        reservation is not None
                        and int(str(migrated["settings_revision"]))
                        == int(str(reservation["ignored_revision"])) + 1
                    ):
                        migration_evidence["recovery_contamination_policy_reassertion"] = dict(
                            reservation
                        )
                    migration_records.append(
                        (
                            {
                                "run_id": self.run_id,
                                "ts_ms": migration_ts_ms,
                                **transition,
                                "change_evidence": migration_evidence,
                            },
                            category,
                        )
                    )

            staged_position_visible = staged_paper_portfolio.main.position is not None
            staged_recovery_plan = (
                staged_paper_portfolio.main.position.plan
                if staged_paper_portfolio.main.position is not None
                else staged_paper_portfolio.main.pending_entry.plan
                if staged_paper_portfolio.main.pending_entry is not None
                else None
            )
            staged_recovery_symbols = {
                *(
                    pending.plan.symbol
                    for account in staged_paper_portfolio.accounts
                    for pending in account.pending_entries.values()
                ),
                *(
                    position.plan.symbol
                    for account in staged_paper_portfolio.accounts
                    for position in account.positions.values()
                ),
            }
            if any(
                not isinstance(symbol, str) or not symbol.strip()
                for symbol in staged_recovery_symbols
            ):
                raise ValueError("복구 PAPER exposure의 symbol이 잘못됐습니다.")
            staged_selected_symbol = self.selected_symbol
            if staged_recovery_plan is not None:
                staged_selected_symbol = staged_recovery_plan.symbol
            elif staged_recovery_symbols:
                staged_selected_symbol = sorted(staged_recovery_symbols)[0]
            snapshot_ts_value = recovered.payload.get(
                "snapshot_ts_ms",
                (staged_recovery_plan.signal_time_ms if staged_recovery_plan is not None else 0),
            )
            if (
                isinstance(snapshot_ts_value, bool)
                or not isinstance(snapshot_ts_value, int)
                or snapshot_ts_value < 0
            ):
                raise ValueError("복구 snapshot_ts_ms가 음수가 아닌 정수가 아닙니다.")
            staged_data_gap_since_ms = dict(self.data_gap_since_ms)
            for symbol in staged_recovery_symbols:
                staged_data_gap_since_ms[symbol] = snapshot_ts_value
            staged_runtime_health_flags = [
                "PAPER_STATE_RECOVERED",
                "ENTRY_LOCK_RECOVERY_REVALIDATION",
            ]
            staged_recovery_log = {
                "ts_ms": self.clock.utc_ms(),
                "category": "RECOVERY",
                "level": "INFO",
                "message": (
                    f"{recovered.lifecycle_state} PAPER 상태 복구 · fresh 공개호가 전 신규진입 잠금"
                ),
            }
            self.ledger.record_strategy_migration_batch(migration_records)

            self.strategy_registry = staged_strategy_registry
            self.shadow_ledger = staged_shadow_ledger
            self.paper_portfolio = staged_paper_portfolio
            self.orderflow_confirmation_runtime = staged_orderflow_runtime
            self._manual_pause_requested = staged_manual_pause_requested
            self._paper_entry_intent_revision = staged_intent_revision
            self._paper_entry_intent_actor = staged_intent_actor
            self._paper_entry_intent_reason = staged_intent_reason
            self._paper_entry_intent_updated_ts_ms = staged_intent_updated_ts_ms
            self._paper_entry_intent_idempotency = staged_intent_idempotency
            if auto_resumed_user_pause:
                self._persist_paper_entry_intent(
                    updated_ts_ms=staged_intent_updated_ts_ms or recovery_validation_ts_ms
                )
                self._log(
                    "RISK",
                    "일반 사용자 일시정지는 재시작 뒤 자동 해제 · PAPER 진입 의도 복구",
                )
            self._persisted_main_order_ids = staged_persisted_main_order_ids
            self._persisted_main_trade_ids = staged_persisted_main_trade_ids
            self._persisted_shadow_trade_ids = staged_persisted_shadow_trade_ids
            self.position_visible = staged_position_visible
            self.selected_symbol = staged_selected_symbol
            self._recovery_revalidation_symbols = staged_recovery_symbols
            self._recovery_ignored_governance_row_tokens = tuple(ignored_governance_row_tokens)
            self._recovery_reserved_governance_revisions = tuple(
                dict(row) for row in reserved_governance_revisions
            )
            self.data_gap_since_ms = staged_data_gap_since_ms
            self.paused = True
            self.runtime_health_flags = staged_runtime_health_flags
            self.control_logs.append(staged_recovery_log)
        except (KeyError, TypeError, ValueError, LedgerInvariantError) as error:
            self._lock_recovery(f"RECOVERY_STATE_REJECTED:{type(error).__name__}")
            return False
        return True

    def _lock_recovery(self, reason: str) -> None:
        self._recovery_revalidation_symbols.clear()
        self.paused = True
        self.position_visible = False
        self.paper_portfolio.main.risk_state.faulted = True
        self.runtime_health_flags = ["RECOVERY_FAIL_CLOSED", reason]
        self._log("RECOVERY", f"복구 무결성 실패 · 신규 PAPER 진입 차단 · {reason}")

    def _measure_storage_safety(
        self,
    ) -> tuple[StorageHealth | None, StorageHealth | None]:
        """파일시스템 호출을 한 worker 실행 단위로 모은다."""

        self.resource_sampler.refresh_storage_usage()
        if self.storage_guard is None:
            return None, None
        archive_health = self.storage_guard.health()
        ledger_health = (
            self.storage_guard.health(self.ledger.path.parent) if self.ledger is not None else None
        )
        return archive_health, ledger_health

    def _apply_storage_safety(
        self,
        archive_health: StorageHealth | None,
        ledger_health: StorageHealth | None,
        *,
        error: OSError | None = None,
    ) -> None:
        """worker 결과만 이벤트 루프 상태에 적용하고 신규 진입을 fail-close한다."""

        self._last_storage_check_ns = self.clock.monotonic_ns()
        self.runtime_health_flags = [
            flag for flag in self.runtime_health_flags if flag != "ENTRY_LOCK_STORAGE_HEALTH_STALE"
        ]
        if self.storage_guard is None:
            self._storage_entry_allowed = True
            self._storage_health_snapshot = {
                "storage_entry_allowed": True,
                "disk_pressure_entry_lock": False,
                "storage_guard_enabled": False,
            }
        elif error is None and archive_health is not None:
            health_rows = [archive_health]
            if ledger_health is not None:
                health_rows.append(ledger_health)
            self._storage_entry_allowed = all(health.entry_allowed for health in health_rows)
            lock_reasons: list[str] = []
            if not archive_health.entry_allowed:
                prefix = "ARCHIVE_" if ledger_health is not None else ""
                lock_reasons.append(f"{prefix}{archive_health.reason}")
            if ledger_health is not None and not ledger_health.entry_allowed:
                lock_reasons.append(f"LEDGER_{ledger_health.reason}")
            self._storage_health_snapshot = {
                "storage_entry_allowed": self._storage_entry_allowed,
                "disk_pressure_entry_lock": not self._storage_entry_allowed,
                "storage_guard_enabled": True,
                "storage_free_bytes": min(health.free_bytes for health in health_rows),
                "storage_free_ratio": round(min(health.free_ratio for health in health_rows), 6),
                "archive_storage_free_bytes": archive_health.free_bytes,
                "archive_storage_free_ratio": round(archive_health.free_ratio, 6),
                "ledger_storage_free_bytes": (
                    ledger_health.free_bytes if ledger_health is not None else None
                ),
                "ledger_storage_free_ratio": (
                    round(ledger_health.free_ratio, 6) if ledger_health is not None else None
                ),
                "storage_lock_reason": "+".join(lock_reasons) or "NONE",
            }
        else:
            self._storage_entry_allowed = False
            self._storage_health_snapshot = {
                "storage_entry_allowed": False,
                "disk_pressure_entry_lock": True,
                "storage_guard_enabled": True,
                "storage_lock_reason": (
                    f"STORAGE_HEALTH_ERROR:{type(error).__name__}"
                    if error is not None
                    else "STORAGE_HEALTH_ERROR:UNKNOWN"
                ),
            }
        if not self._storage_entry_allowed and self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            self.paused = True
            if "STORAGE_PRESSURE_ENTRY_LOCK" not in self.runtime_health_flags:
                self.runtime_health_flags.append("STORAGE_PRESSURE_ENTRY_LOCK")
        else:
            self.runtime_health_flags = [
                flag for flag in self.runtime_health_flags if flag != "STORAGE_PRESSURE_ENTRY_LOCK"
            ]

    def _recover_transient_persistence_fault_if_safe(self) -> bool:
        """저장공간 또는 격리 worker의 일시 장애가 해소되면 적체 저장을 재개한다."""

        if (
            not self._persistence_fault_active
            or not self._persistence_fault_recoverable
            or not self._storage_entry_allowed
        ):
            return False
        self._persistence_fault_active = False
        self._persistence_fault_recoverable = False
        self._persistence_recovery_count += 1
        self._persistence_last_recovered_ts_ms = self.clock.utc_ms()
        self._last_recovered_persistence_error = self._last_persistence_error
        self._last_persistence_error = None
        self.runtime_health_flags = [
            flag for flag in self.runtime_health_flags if flag != "ENTRY_LOCK_TRANSIENT_PERSISTENCE"
        ]
        self._log(
            "STORAGE",
            "저장공간 안전선 회복 · 누적 버퍼 저장 자동 재개",
        )
        return True

    def _record_storage_health_refresh(self, elapsed_ms: float) -> None:
        self._storage_health_refresh_count += 1
        self._storage_health_refresh_last_ms = elapsed_ms
        self._storage_health_refresh_max_ms = max(
            self._storage_health_refresh_max_ms,
            elapsed_ms,
        )
        self._storage_health_refresh_completed_ts_ms = self.clock.utc_ms()

    def _refresh_storage_safety(self, *, force: bool = False) -> bool:
        """평상시에는 캐시만 읽고 명시적 동기 호출에서만 파일시스템을 검사한다."""

        if force:
            started = time.monotonic()
            try:
                archive_health, ledger_health = self._measure_storage_safety()
                self._apply_storage_safety(archive_health, ledger_health)
            except OSError as error:
                self._apply_storage_safety(None, None, error=error)
            self._recover_transient_persistence_fault_if_safe()
            self._record_storage_health_refresh((time.monotonic() - started) * 1_000)
        elif (
            self.storage_guard is not None
            and self._last_storage_check_ns is not None
            and self.clock.monotonic_ns() - self._last_storage_check_ns > _STORAGE_HEALTH_STALE_NS
        ):
            self._storage_entry_allowed = False
            self._storage_health_snapshot = {
                **self._storage_health_snapshot,
                "storage_entry_allowed": False,
                "disk_pressure_entry_lock": True,
                "storage_guard_enabled": True,
                "storage_lock_reason": "STORAGE_HEALTH_STALE",
            }
            if "ENTRY_LOCK_STORAGE_HEALTH_STALE" not in self.runtime_health_flags:
                self.runtime_health_flags.append("ENTRY_LOCK_STORAGE_HEALTH_STALE")
            if self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
                self.paused = True
        return self._storage_entry_allowed

    async def refresh_storage_safety_async(self) -> bool:
        """디스크·볼륨 상태를 이벤트 루프 밖에서 갱신한다."""

        started = asyncio.get_running_loop().time()
        try:
            archive_health, ledger_health = await to_thread.run_sync(self._measure_storage_safety)
            self._apply_storage_safety(archive_health, ledger_health)
        except OSError as error:
            self._apply_storage_safety(None, None, error=error)
        self._recover_transient_persistence_fault_if_safe()
        self._record_storage_health_refresh((asyncio.get_running_loop().time() - started) * 1_000)
        self._refresh_supervisor_entry_safety()
        return self._storage_entry_allowed

    async def run_storage_health_worker(self, stop: asyncio.Event) -> None:
        """저장소 상태를 주기적으로 갱신하되 시장 이벤트 루프를 막지 않는다."""

        while not stop.is_set():
            await self.refresh_storage_safety_async()
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=_STORAGE_HEALTH_REFRESH_SECONDS,
                )
            except TimeoutError:
                continue

    def _operational_diagnostics(self) -> dict[str, object]:
        self._refresh_storage_safety()
        strategy_candle_cache_diagnostics = (
            self._live_strategy_evaluator.candle_cache_diagnostics()
            if isinstance(self._live_strategy_evaluator, ProcessStrategyEvaluator)
            else {}
        )
        recovery_audit = self.startup_recovery_audit
        paper_transition = self.paper_portfolio.latest_execution_transition
        queue_capacity = (
            self._supervisor.telemetry.queue_capacity if self._supervisor is not None else 0
        )
        strategy_high_water, strategy_low_water = self._strategy_evaluation_queue_watermarks(
            queue_capacity
        )
        semivariance_snapshots = tuple(self._semivariance_latest_snapshots.values())
        return {
            "server_time_ms": self.clock.utc_ms(),
            "display_timezone": "Asia/Seoul",
            **self.resource_sampler.sample(),
            **self._storage_health_snapshot,
            "storage_health_refresh_count": self._storage_health_refresh_count,
            "storage_health_refresh_last_ms": round(
                self._storage_health_refresh_last_ms,
                3,
            ),
            "storage_health_refresh_max_ms": round(
                self._storage_health_refresh_max_ms,
                3,
            ),
            "storage_health_refresh_completed_ts_ms": (
                self._storage_health_refresh_completed_ts_ms
            ),
            "persistence_fault_count": self._persistence_fault_count,
            "persistence_fault_active": self._persistence_fault_active,
            "persistence_fault_recoverable": self._persistence_fault_recoverable,
            "persistence_recovery_count": self._persistence_recovery_count,
            "persistence_last_recovered_ts_ms": self._persistence_last_recovered_ts_ms,
            "persistence_last_recovered_error": (self._last_recovered_persistence_error or "NONE"),
            "persistence_buffer_dropped": self._persistence_buffer_dropped,
            "persistence_backlog_peak": self._persistence_backlog_peak,
            "persistence_backlog_entry_lock_count": (self._persistence_backlog_entry_lock_count),
            "persistence_backlog_entry_lock_events": (_PERSISTENCE_BACKLOG_ENTRY_LOCK_EVENTS),
            "persistence_backlog_recovery_events": (_PERSISTENCE_BACKLOG_RECOVERY_EVENTS),
            "persistence_last_error": self._last_persistence_error or "NONE",
            "persistence_flush_count": self._persistence_flush_count,
            "persistence_flush_last_ms": round(self._persistence_flush_last_ms, 3),
            "persistence_flush_max_ms": round(self._persistence_flush_max_ms, 3),
            "persistence_flush_last_completed_ts_ms": (
                self._persistence_flush_last_completed_ts_ms
            ),
            "persistence_flush_max_ts_ms": self._persistence_flush_max_ts_ms,
            "persistence_flush_slow_count": self._persistence_flush_slow_count,
            "persistence_flush_last_slow_ts_ms": self._persistence_flush_last_slow_ts_ms,
            "persistence_flush_slowest_gate_wait_ms": round(
                self._persistence_flush_slowest_gate_wait_ms,
                3,
            ),
            "persistence_flush_slowest_archive_ms": round(
                self._persistence_flush_slowest_archive_ms,
                3,
            ),
            "persistence_flush_slowest_ledger_ms": round(
                self._persistence_flush_slowest_ledger_ms,
                3,
            ),
            "persistence_flush_slowest_ledger_connect_ms": round(
                self._persistence_flush_slowest_ledger_connect_ms,
                3,
            ),
            "persistence_flush_slowest_ledger_begin_wait_ms": round(
                self._persistence_flush_slowest_ledger_begin_wait_ms,
                3,
            ),
            "persistence_flush_slowest_ledger_write_ms": round(
                self._persistence_flush_slowest_ledger_write_ms,
                3,
            ),
            "persistence_flush_slowest_ledger_commit_ms": round(
                self._persistence_flush_slowest_ledger_commit_ms,
                3,
            ),
            "persistence_flush_slowest_ledger_close_ms": round(
                self._persistence_flush_slowest_ledger_close_ms,
                3,
            ),
            "persistence_flush_slowest_market_events": (
                self._persistence_flush_slowest_market_events
            ),
            "persistence_flush_slowest_candles": self._persistence_flush_slowest_candles,
            "persistence_flush_slowest_archive_batches": (
                self._persistence_flush_slowest_archive_batches
            ),
            "execution_persistence_count": self._execution_persistence_count,
            "execution_persistence_last_ms": round(
                self._execution_persistence_last_ms,
                3,
            ),
            "execution_persistence_max_ms": round(
                self._execution_persistence_max_ms,
                3,
            ),
            "execution_persistence_last_completed_ts_ms": (
                self._execution_persistence_last_completed_ts_ms
            ),
            "execution_persistence_max_ts_ms": self._execution_persistence_max_ts_ms,
            "execution_persistence_last_items": self._execution_persistence_last_items,
            "live_event_processing_count": self._live_event_processing_count,
            "live_event_processing_last_ms": round(
                self._live_event_processing_last_ms,
                3,
            ),
            "live_event_processing_max_ms": round(
                self._live_event_processing_max_ms,
                3,
            ),
            "live_event_processing_over_100ms_count": (
                self._live_event_processing_over_100ms_count
            ),
            "live_event_processing_max_ts_ms": self._live_event_processing_max_ts_ms,
            "live_event_processing_max_event_type": (self._live_event_processing_max_event_type),
            "live_event_processing_max_symbol": self._live_event_processing_max_symbol,
            "live_event_phase_last_ms": {
                name: round(elapsed_ms, 3)
                for name, elapsed_ms in self._live_event_phase_last_ms.items()
            },
            "live_event_phase_max_ms": round(self._live_event_phase_max_ms, 3),
            "live_event_phase_max_name": self._live_event_phase_max_name,
            "live_event_phase_max_ts_ms": self._live_event_phase_max_ts_ms,
            "live_event_phase_max_event_type": self._live_event_phase_max_event_type,
            "live_event_phase_max_symbol": self._live_event_phase_max_symbol,
            "live_event_phase_over_100ms_count": self._live_event_phase_over_100ms_count,
            "wal_autocheckpoint_pages": 0,
            "wal_checkpoint_flush_interval": _WAL_CHECKPOINT_FLUSH_INTERVAL,
            "wal_checkpoint_count": self._wal_checkpoint_count,
            "wal_checkpoint_last_ms": round(self._wal_checkpoint_last_ms, 3),
            "wal_checkpoint_max_ms": round(self._wal_checkpoint_max_ms, 3),
            "wal_checkpoint_slow_count": self._wal_checkpoint_slow_count,
            "wal_checkpoint_busy_count": self._wal_checkpoint_busy_count,
            "wal_checkpoint_log_frames": self._wal_checkpoint_log_frames,
            "wal_checkpointed_frames": self._wal_checkpointed_frames,
            "wal_checkpoint_last_completed_ts_ms": (self._wal_checkpoint_last_completed_ts_ms),
            "wal_checkpoint_fault_count": self._wal_checkpoint_fault_count,
            "wal_checkpoint_last_error": self._wal_checkpoint_last_error or "NONE",
            "wal_checkpoint_deferred_count": self._wal_checkpoint_deferred_count,
            "wal_checkpoint_last_wal_bytes": self._wal_checkpoint_last_wal_bytes,
            "wal_checkpoint_soft_bytes": _WAL_CHECKPOINT_SOFT_BYTES,
            "wal_checkpoint_probe_log_frames": self._wal_checkpoint_probe_log_frames,
            "wal_checkpoint_probe_checkpointed_frames": (
                self._wal_checkpoint_probe_checkpointed_frames
            ),
            "wal_checkpoint_probe_page_size": self._wal_checkpoint_probe_page_size,
            "wal_checkpoint_pending_bytes": self._wal_checkpoint_pending_bytes,
            "wal_checkpoint_running": (
                self._wal_checkpoint_task is not None and not self._wal_checkpoint_task.done()
            ),
            "wal_checkpoint_current_concurrent_flush_delta": (
                max(
                    0,
                    self._persistence_flush_count - self._wal_checkpoint_task_started_flush_count,
                )
                if self._wal_checkpoint_task is not None
                else 0
            ),
            "wal_checkpoint_last_concurrent_flush_delta": (
                self._wal_checkpoint_last_concurrent_flush_delta
            ),
            "wal_checkpoint_max_concurrent_flush_delta": (
                self._wal_checkpoint_max_concurrent_flush_delta
            ),
            "persistence_worker_warmed": self._persistence_worker_warmed,
            "persistence_worker_warm_ms": round(self._persistence_worker_warm_ms, 3),
            "event_memory_count": len(self._events),
            "event_memory_limit": self._events.maxlen or 0,
            "market_persistence_buffer": len(self._market_event_buffer),
            "candle_persistence_buffer": len(self._candle_buffer),
            "candidate_persistence_buffer": len(self._candidate_plan_buffer),
            "universe_snapshot_persistence_buffer": len(self._universe_snapshot_buffer),
            "universe_snapshot_persisted_count": (self._universe_snapshot_persisted_count),
            "universe_snapshot_persistence_last_ms": round(
                self._universe_snapshot_persistence_last_ms,
                3,
            ),
            "universe_snapshot_persistence_max_ms": round(
                self._universe_snapshot_persistence_max_ms,
                3,
            ),
            "entry_locked": self.paused,
            "stale_trade_symbols": len(self._stale_trade_symbols),
            "feature_input_fault_symbols": len(self._feature_input_fault_symbols),
            "strategy_evaluation_interval_ms": self.strategy_evaluation_interval_ms,
            "strategy_evaluation_count": self.strategy_evaluation_count,
            "strategy_evaluation_backpressure_active": (
                self._strategy_evaluation_backpressure_active
            ),
            "strategy_evaluation_backpressure_skip_count": (
                self._strategy_evaluation_backpressure_skip_count
            ),
            "strategy_evaluation_backpressure_resume_count": (
                self._strategy_evaluation_backpressure_resume_count
            ),
            "strategy_evaluation_backpressure_high_water": strategy_high_water,
            "strategy_evaluation_backpressure_low_water": strategy_low_water,
            "directional_change_mode": "OBSERVATION_ONLY",
            "directional_change_profiles": {
                profile_id: {
                    "initialized": self._directional_change_initialized[profile_id],
                    "event_count": self._directional_change_event_counts[profile_id],
                    "last_direction": self._directional_change_last_directions[profile_id].value,
                    "last_confirmation_type": (
                        self._directional_change_last_confirmation_types[profile_id]
                    ),
                }
                for profile_id, _threshold in _DIRECTIONAL_CHANGE_PROFILES
            },
            "semivariance_observation": {
                "mode": "OBSERVATION_ONLY",
                "tracked_symbol_count": len(self._semivariance_symbols),
                "one_hour_ready_symbol_count": sum(
                    snapshot.one_hour.status is SemivarianceReadiness.READY
                    for snapshot in semivariance_snapshots
                ),
                "four_hour_ready_symbol_count": sum(
                    snapshot.four_hour.status is SemivarianceReadiness.READY
                    for snapshot in semivariance_snapshots
                ),
                "jump_ready_symbol_count": sum(
                    snapshot.jump_one_hour.status is SemivarianceReadiness.READY
                    for snapshot in semivariance_snapshots
                ),
                "periodicity_status": "PERIODICITY_UNCALIBRATED",
                "last_symbol": self._semivariance_last_symbol,
                "last_completed_minute_ts_ms": (self._semivariance_last_completed_minute_ts_ms),
                "last_status": self._semivariance_last_status,
                "last_reset_reason": self._semivariance_last_reset_reason,
                "risk_multiplier_applied": False,
            },
            "strategy_evaluation_executor": (
                "DEDICATED_PROCESS" if self.mode is RuntimeMode.LIVE_SHADOW_PAPER else "SYNCHRONOUS"
            ),
            "strategy_evaluation_process_pid": self._live_strategy_process_pid,
            **strategy_candle_cache_diagnostics,
            "qualified_signal_count": self.qualified_signal_count,
            "manual_pause_requested": self._manual_pause_requested,
            "paper_entry_intent_revision": self._paper_entry_intent_revision,
            "automatic_recovery_enabled": True,
            "startup_storage_init_ms": round(self.startup_storage_init_ms, 3),
            "startup_ledger_open_ms": round(self.startup_ledger_open_ms, 3),
            "startup_recovery_lookup_ms": round(self.startup_recovery_lookup_ms, 3),
            "startup_runtime_init_ms": round(self.startup_runtime_init_ms, 3),
            "startup_recovery_restore_ms": round(self.startup_recovery_restore_ms, 3),
            "startup_recovery_transition_id": str(recovery_audit.get("transition_id", "NONE")),
            "startup_recovery_previous_state": str(
                recovery_audit.get("previous_state", "NO_OPEN_RUN")
            ),
            "startup_recovery_state": str(recovery_audit.get("new_state", "NO_RECOVERY_NEEDED")),
            "startup_recovery_cause_code": str(recovery_audit.get("cause_code", "NO_OPEN_RUN")),
            "startup_recovery_actor": str(recovery_audit.get("actor", "RECOVERY")),
            "startup_recovery_run_id": str(recovery_audit.get("run_id", "NONE")),
            "startup_recovery_occurred_ts_ms": int(str(recovery_audit.get("occurred_ts_ms", 0))),
            "startup_recovery_reversible": bool(recovery_audit.get("reversible", True)),
            "last_paper_transition_id": str(paper_transition.get("transition_id", "NONE")),
            "last_paper_transition_previous_state": str(
                paper_transition.get("previous_state", "NO_PAPER_TRANSITION")
            ),
            "last_paper_transition_state": str(
                paper_transition.get("new_state", "NO_PAPER_TRANSITION")
            ),
            "last_paper_transition_cause_code": str(
                paper_transition.get("cause_code", "NO_PAPER_TRANSITION")
            ),
            "last_paper_transition_actor": str(paper_transition.get("actor", "AUTO_SAFETY")),
            "last_paper_transition_account_id": str(paper_transition.get("account_id", "NONE")),
            "last_paper_transition_symbol": str(paper_transition.get("symbol", "NONE")),
            "last_paper_transition_occurred_ts_ms": int(
                str(paper_transition.get("occurred_ts_ms", 0))
            ),
            "last_paper_transition_reversible": bool(paper_transition.get("reversible", True)),
            "startup_total_ms": round(self.startup_total_ms, 3),
            "startup_portfolio_init_ms": round(self.startup_portfolio_init_ms, 3),
            "startup_trade_cache_ms": round(self.startup_trade_cache_ms, 3),
            "startup_post_init_total_ms": round(self.startup_post_init_total_ms, 3),
            "dashboard_trade_cache_ready": self.dashboard_trade_cache_ready,
            "dashboard_trade_cache_loading": self.dashboard_trade_cache_loading,
            "dashboard_trade_cache_last_ms": round(self.dashboard_trade_cache_last_ms, 3),
            "dashboard_trade_cache_completed_ts_ms": (self.dashboard_trade_cache_completed_ts_ms),
        }

    def _handle_persistence_fault(self, error: Exception) -> None:
        self._persistence_fault_count += 1
        self._last_persistence_error = f"{type(error).__name__}: {error}"
        already_hard_faulted = (
            self._persistence_fault_active and not self._persistence_fault_recoverable
        ) or self.paper_portfolio.main.risk_state.faulted
        recoverable_transient_fault = (
            isinstance(
                error,
                StoragePressureError | BrokenWorkerProcess,
            )
            and not already_hard_faulted
        )
        self._persistence_fault_active = True
        self.paused = True
        if recoverable_transient_fault:
            self._persistence_fault_recoverable = True
            if "ENTRY_LOCK_TRANSIENT_PERSISTENCE" not in self.runtime_health_flags:
                self.runtime_health_flags.append("ENTRY_LOCK_TRANSIENT_PERSISTENCE")
            cause = (
                "저장공간 압력"
                if isinstance(error, StoragePressureError)
                else "격리 저장 worker 일시 장애"
            )
            self._log(
                "STORAGE",
                f"{cause} · 신규 PAPER 진입 일시 차단 · 안전 확인 후 자동 재개",
            )
            return
        self._persistence_fault_recoverable = False
        self.paper_portfolio.main.risk_state.faulted = True
        if "PERSISTENCE_FAULT_ENTRY_LOCK" not in self.runtime_health_flags:
            self.runtime_health_flags.append("PERSISTENCE_FAULT_ENTRY_LOCK")
        self._log(
            "STORAGE",
            f"원장 저장 실패 · 신규 PAPER 진입 영구 차단 · {type(error).__name__}",
        )

    async def boot_live_public(self, probe: LiveBootstrapProbe | None = None) -> bool:
        if self.mode is not RuntimeMode.LIVE_SHADOW_PAPER:
            raise ValueError("LIVE 부트스트랩은 LIVE_SHADOW_PAPER 모드에서만 가능합니다.")
        active_probe = probe or LivePublicBootstrapper()
        self.market_data_state = MarketDataState.RECONNECTING
        self.paused = True
        self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        other_venue = Venue.BYBIT_LINEAR if self.venue is Venue.BINANCE_USDM else Venue.BINANCE_USDM
        for candidate_venue in (self.venue, other_venue):
            if candidate_venue is not self.venue:
                self._switch_venue_run(candidate_venue)
            try:
                result = await active_probe.bootstrap(
                    candidate_venue, run_id=self.run_id, clock=self.clock
                )
            except PublicDataUnavailable as error:
                self._record_public_failure(candidate_venue, error)
                continue
            if result.venue is not candidate_venue:
                self._record_public_failure(
                    candidate_venue,
                    PublicDataUnavailable("probe 거래소 식별자 불일치"),
                )
                continue
            self.venue = result.venue
            self._events = deque(result.events, maxlen=_LIVE_EVENT_MEMORY_LIMIT)
            self.wide_symbol_count = result.wide_symbol_count
            self.deep_symbol_count = result.deep_symbol_count
            self.processing_lag_p95_ms = result.websocket_lag_ms
            self.market_data_state = MarketDataState.LIVE
            self.runtime_health_flags = ["PUBLIC_DATA_VERIFIED", "NO_AUTH_HEADERS"]
            self.paused = self._manual_pause_requested or result.websocket_lag_ms > 1_500
            if self.paused:
                self.runtime_health_flags.append("CRITICAL_MARKET_LAG_ENTRY_LOCK")
            self._log(
                "MARKET_DATA",
                f"{result.venue.value} 공개 이벤트 검증 · "
                f"{result.eligible_symbol_count}개 eligible · 자격 증명 없음",
            )
            return True
        self.market_data_state = MarketDataState.DISCONNECTED
        self.runtime_health_flags.append("PUBLIC_DATA_UNAVAILABLE")
        return False

    async def start_persistent_live(
        self,
        progress: ProgressCallback | None = None,
    ) -> bool:
        if self.mode is not RuntimeMode.LIVE_SHADOW_PAPER:
            raise ValueError("지속 LIVE supervisor는 LIVE_SHADOW_PAPER에서만 시작합니다.")
        await self.shutdown_supervisor()
        if not await self._warm_market_archive_worker():
            return False
        pinned_symbols = tuple(sorted(self._recovery_revalidation_symbols))
        providers: dict[Venue, PublicStreamProvider] = {
            Venue.BINANCE_USDM: BinancePersistentProvider(
                wide_max=_LIVE_WIDE_SYMBOL_TARGET,
                deep_max=_LIVE_DEEP_SYMBOL_TARGET,
                pinned_symbols=pinned_symbols,
            ),
            Venue.BYBIT_LINEAR: BybitPersistentProvider(
                wide_max=_LIVE_WIDE_SYMBOL_TARGET,
                deep_max=_LIVE_DEEP_SYMBOL_TARGET,
                pinned_symbols=pinned_symbols,
            ),
        }
        primary_venue = self.venue if self.venue in providers else Venue.BINANCE_USDM
        candidate_venues = (
            (primary_venue,)
            if self._recovery_revalidation_symbols
            else (
                primary_venue,
                Venue.BYBIT_LINEAR if primary_venue is Venue.BINANCE_USDM else Venue.BINANCE_USDM,
            )
        )
        self.market_data_state = MarketDataState.RECONNECTING
        self.paused = True
        self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        for index, candidate_venue in enumerate(candidate_venues):
            if progress is not None:
                await progress(
                    "CONNECTING_PRIMARY" if index == 0 else "CONNECTING_FALLBACK",
                    "주 거래소 공개시장과 정상 호가를 확인하고 있습니다"
                    if index == 0
                    else "대체 거래소 공개시장과 정상 호가를 확인하고 있습니다",
                )
            provider = providers[candidate_venue]
            if candidate_venue is not self.venue:
                self._switch_venue_run(candidate_venue)
            self._live_strategy_process_pid = None
            try:
                self._live_strategy_process_pid = await self._live_strategy_evaluator.warm(
                    self._strategy_process_state_key()
                )
            except asyncio.CancelledError:
                self.paused = True
                self.market_data_state = MarketDataState.DISCONNECTED
                self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
                raise
            except Exception as error:
                self.paused = True
                self.market_data_state = MarketDataState.DISCONNECTED
                self.runtime_health_flags = ["ENTRY_LOCK_STRATEGY_PROCESS_UNAVAILABLE"]
                self._log(
                    "STRATEGY",
                    f"LIVE 전략 평가 프로세스 준비 실패 · {type(error).__name__}",
                )
                return False
            supervisor = PersistentPublicSupervisor(
                provider,
                run_id=self.run_id,
                clock=self.clock,
                sink=self.ingest_live_event_async,
                protected_symbols=self._protected_deep_symbols,
            )
            try:
                selection = await supervisor.start()
            except asyncio.CancelledError:
                await supervisor.stop()
                self.paused = True
                self.market_data_state = MarketDataState.DISCONNECTED
                self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
                raise
            except PublicDataUnavailable as error:
                await supervisor.stop()
                self._record_public_failure(candidate_venue, error)
                continue
            self._supervisor = supervisor
            self.live_selection = selection
            self._record_universe_selection(selection, reason="INITIAL_DEEP_SELECTION")
            self.venue = selection.venue
            self.wide_symbol_count = len(selection.wide_symbols)
            self.deep_symbol_count = len(selection.deep_symbols)
            self.selected_symbol = (
                "BTCUSDT" if "BTCUSDT" in selection.deep_symbols else selection.deep_symbols[0]
            )
            self.processing_lag_p95_ms = supervisor.telemetry.lag_p95_ms
            self.market_data_state = MarketDataState.LIVE
            self.paused = (
                self._manual_pause_requested
                or supervisor.telemetry.entry_locked
                or self.paper_portfolio.main.risk_state.faulted
                or bool(self._recovery_revalidation_symbols)
            )
            self.runtime_health_flags = ["PUBLIC_SUPERVISOR_RUNNING", "NO_AUTH_HEADERS"]
            self._refresh_supervisor_entry_safety()
            if self.paper_portfolio.main.risk_state.faulted:
                self.runtime_health_flags.append("RECOVERY_FAIL_CLOSED")
            if self._recovery_revalidation_symbols:
                self.runtime_health_flags.append("ENTRY_LOCK_RECOVERY_REVALIDATION")
            if not await self.refresh_storage_safety_async():
                self.paused = True
            self._log(
                "MARKET_DATA",
                f"{selection.venue.value} 지속 공개 supervisor 시작 · "
                f"wide {len(selection.wide_symbols)} · deep {len(selection.deep_symbols)}",
            )
            return True
        self.market_data_state = MarketDataState.DISCONNECTED
        if self._recovery_revalidation_symbols:
            self.runtime_health_flags.append("RECOVERED_POSITION_PUBLIC_DATA_UNAVAILABLE")
        self.runtime_health_flags.append("PUBLIC_DATA_UNAVAILABLE")
        return False

    async def _warm_market_archive_worker(self) -> bool:
        """첫 공개 이벤트 전에 process·Arrow·zstd 초기화 정지를 흡수한다."""

        if self.market_event_archive is None or self._persistence_worker_warmed:
            return True
        started = asyncio.get_running_loop().time()
        try:
            await to_process.run_sync(warm_market_event_worker_process)
        except Exception as error:
            self._handle_persistence_fault(error)
            return False
        self._persistence_worker_warm_ms = (asyncio.get_running_loop().time() - started) * 1_000
        self._persistence_worker_warmed = True
        return True

    async def ingest_live_event_async(self, event: MarketEvent) -> None:
        """시장 판단 순서를 유지하며 전략 평가와 SQLite I/O를 worker로 보낸다."""

        started = asyncio.get_running_loop().time()
        defer_token = _DEFER_STRATEGY_EVALUATION.set(True)
        cancellation: asyncio.CancelledError | None = None
        worker_error: Exception | None = None
        try:
            prepared = self.ingest_live_event(event, defer_execution_persistence=True)
            skip_strategy_evaluation = self._refresh_strategy_evaluation_backpressure()
            if prepared is not None and skip_strategy_evaluation:
                self._strategy_evaluation_backpressure_skip_count += 1
            elif prepared is not None:
                phase_started = time.perf_counter()
                process_request = self._live_strategy_evaluator.request(
                    state_key=self._strategy_process_state_key(),
                    registry=prepared.strategy_registry,
                    snapshot=prepared.snapshot,
                    regime=prepared.regime,
                    tick_size=prepared.tick_size,
                    fifteen_minute_candles=prepared.fifteen_minute_candles,
                    thirty_minute_candles=prepared.thirty_minute_candles,
                    hourly_candles=prepared.hourly_candles,
                )
                (
                    process_result,
                    cancellation,
                ) = await self._live_strategy_evaluator.evaluate_to_completion(process_request)
                self._record_live_event_phase("STRATEGY_EVALUATION", phase_started, event)
                if cancellation is None:
                    self._refresh_supervisor_entry_safety()
                    self._complete_strategy_evaluation(
                        prepared,
                        process_result.signals,
                        persist_execution=False,
                        process_request=process_request,
                        process_result=process_result,
                    )
        except Exception as error:
            worker_error = error
        finally:
            _DEFER_STRATEGY_EVALUATION.reset(defer_token)
            elapsed_ms = (asyncio.get_running_loop().time() - started) * 1_000
            self._live_event_processing_count += 1
            self._live_event_processing_last_ms = elapsed_ms
            if elapsed_ms >= 100:
                self._live_event_processing_over_100ms_count += 1
            if elapsed_ms > self._live_event_processing_max_ms:
                self._live_event_processing_max_ms = elapsed_ms
                self._live_event_processing_max_ts_ms = self.clock.utc_ms()
                self._live_event_processing_max_event_type = event.event_type
                self._live_event_processing_max_symbol = event.symbol
        if self._has_unpersisted_execution_state():
            _, persistence_cancellation = await self._run_worker_to_completion(
                self._persist_execution_state_safely,
                event.venue_ts_ms,
            )
            cancellation = persistence_cancellation or cancellation
        if cancellation is not None:
            raise cancellation
        if worker_error is not None:
            raise worker_error

    @staticmethod
    def _strategy_evaluation_queue_watermarks(capacity: int) -> tuple[int, int]:
        """작은 queue는 비율로, 운영 queue는 64건 이내에서 CPU 평가를 줄인다."""

        bounded_capacity = max(0, capacity)
        if bounded_capacity == 0:
            return 0, 0
        high_water = min(
            _STRATEGY_EVALUATION_QUEUE_HIGH_WATER_MAX,
            max(1, bounded_capacity // _STRATEGY_EVALUATION_QUEUE_HIGH_WATER_DIVISOR),
        )
        return high_water, high_water // _STRATEGY_EVALUATION_QUEUE_LOW_WATER_DIVISOR

    def _refresh_strategy_evaluation_backpressure(self) -> bool:
        """supervisor 큐가 안전한 low-water로 회복될 때까지 CPU 평가만 줄인다."""

        if self._supervisor is None:
            return self._strategy_evaluation_backpressure_active
        telemetry = self._supervisor.telemetry
        capacity = max(0, telemetry.queue_capacity)
        depth = max(0, telemetry.queue_depth)
        high_water, low_water = self._strategy_evaluation_queue_watermarks(capacity)
        if self._strategy_evaluation_backpressure_active:
            if not telemetry.queue_overload_active and depth <= low_water:
                self._strategy_evaluation_backpressure_active = False
                self._strategy_evaluation_backpressure_resume_count += 1
                return False
            return True
        if telemetry.queue_overload_active or (capacity > 0 and depth >= high_water):
            self._strategy_evaluation_backpressure_active = True
            return True
        return False

    @staticmethod
    async def _run_worker_to_completion[WorkerResult](
        function: Callable[..., WorkerResult],
        *arguments: object,
    ) -> tuple[WorkerResult, asyncio.CancelledError | None]:
        """호출 task 취소 뒤에도 안전 경계 worker를 완료까지 drain한다."""

        worker = asyncio.create_task(
            to_thread.run_sync(
                function,
                *arguments,
            )
        )
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                return await asyncio.shield(worker), cancellation
            except asyncio.CancelledError as error:
                cancellation = error

    def ingest_live_event(
        self,
        event: MarketEvent,
        *,
        defer_execution_persistence: bool = False,
    ) -> _PreparedStrategyEvaluation | None:
        if event.run_id != self.run_id or event.venue is not self.venue:
            raise ValueError("다른 Run 또는 거래소 이벤트를 LIVE 런타임에 섞을 수 없습니다.")
        depth_event = event.event_type in {"DEPTH_UPDATE", "ORDERBOOK"}
        pre_dispatch_started = time.perf_counter()
        if depth_event:
            self._live_event_phase_last_ms.clear()
        self._refresh_supervisor_entry_safety()
        self._events.append(event)
        if self.ledger is not None and self.mode is not RuntimeMode.READY:
            with self._persistence_lock:
                self._market_event_buffer.append(self._persistable_market_event(event))
                if self._persistence_fault_active and len(self._market_event_buffer) > 10_000:
                    overflow = len(self._market_event_buffer) - 10_000
                    self._persistence_buffer_dropped += overflow
                    del self._market_event_buffer[:overflow]
                persistence_backlog = len(self._market_event_buffer)
            self._refresh_persistence_backlog_safety(persistence_backlog)
        data_health_fault = event.quality.is_stale or not event.quality.sequence_valid
        if depth_event and data_health_fault:
            self._observe_directional_change(event)
        if data_health_fault:
            self._reset_semivariance_symbol(event.symbol, "DATA_GAP")
            entering_gap = event.symbol not in self.data_gap_since_ms
            self.data_gap_since_ms.setdefault(event.symbol, event.venue_ts_ms)
            if entering_gap:
                # 진행 중인 이전 healthy 프로세스 결과를 거부하고, 다음 평가에서
                # 자식 evaluator의 confirmation 이력을 새 상태로 시작한다.
                self._strategy_data_health_epoch += 1
            latest_feature = self.latest_features.get(event.symbol)
            if latest_feature is not None and latest_feature.data_healthy:
                self.latest_features[event.symbol] = replace(
                    latest_feature,
                    data_healthy=False,
                )
            self.paused = True
            if "ENTRY_LOCK_DATA_HEALTH" not in self.runtime_health_flags:
                self.runtime_health_flags.append("ENTRY_LOCK_DATA_HEALTH")
            cancelled_accounts = self.paper_portfolio.cancel_all_pending_entries(
                now_ms=event.venue_ts_ms,
                reason_code=(
                    "DATA_HEALTH_STALE"
                    if event.quality.is_stale
                    else "DATA_HEALTH_SEQUENCE_INVALID"
                ),
            )
            if cancelled_accounts and not defer_execution_persistence:
                self._persist_execution_state_safely(event.venue_ts_ms)
        if depth_event:
            self._record_live_event_phase(
                "INGEST_PRE_DISPATCH",
                pre_dispatch_started,
                event,
            )
        prepared: _PreparedStrategyEvaluation | None = None
        if event.event_type == "TRADE":
            if data_health_fault:
                self._stale_trade_symbols.add(event.symbol)
            else:
                self._stale_trade_symbols.discard(event.symbol)
                try:
                    trade = TradeTick(
                        venue=event.venue,
                        symbol=event.symbol,
                        price=Decimal(str(event.data["price"])),
                        quantity=Decimal(str(event.data["quantity"])),
                        trade_ts_ms=int(event.transaction_ts_ms or event.venue_ts_ms),
                        buyer_is_aggressor=bool(event.data["buyer_is_aggressor"]),
                        event_id=event.event_id,
                    )
                    if (
                        not trade.price.is_finite()
                        or not trade.quantity.is_finite()
                        or trade.price <= 0
                        or trade.quantity <= 0
                    ):
                        raise FeatureInputError("체결 가격과 수량은 유한한 양수여야 합니다.")
                    out_of_order_trade_count = self.candle_builder.diagnostics.out_of_order_trades
                    completed_candles = self.candle_builder.add(trade)
                    feature_engine = self.feature_engines.get(event.symbol)
                    if feature_engine is not None:
                        feature_engine.ingest_trade(trade)
                except (
                    ArithmeticError,
                    FeatureInputError,
                    KeyError,
                    TypeError,
                    ValueError,
                ) as error:
                    self._record_feature_input_fault(event.symbol, error)
                else:
                    if (
                        self.candle_builder.diagnostics.out_of_order_trades
                        > out_of_order_trade_count
                    ):
                        self._reset_semivariance_symbol(
                            event.symbol,
                            "OUT_OF_ORDER_TRADE",
                        )
                    else:
                        self._observe_completed_minute_candles(
                            completed_candles,
                            completed_ts_ms=trade.trade_ts_ms,
                        )
                    self._buffer_completed_candles(completed_candles)
        elif event.event_type in {"DEPTH_UPDATE", "ORDERBOOK"}:
            prepared = self._evaluate_book_event(
                event,
                persist_execution=not defer_execution_persistence,
            )
        post_dispatch_started = time.perf_counter()
        if event.event_type == "HEALTH" or not event.quality.sequence_valid:
            self.paused = True
            if "ENTRY_LOCK_DATA_HEALTH" not in self.runtime_health_flags:
                self.runtime_health_flags.append("ENTRY_LOCK_DATA_HEALTH")
        if self._supervisor is not None:
            self.processing_lag_p95_ms = self._supervisor.telemetry.lag_p95_ms
        if depth_event:
            self._record_live_event_phase(
                "INGEST_POST_DISPATCH",
                post_dispatch_started,
                event,
            )
        return prepared

    def _record_live_event_phase(
        self,
        name: str,
        started: float,
        event: MarketEvent,
    ) -> None:
        """호가 처리의 가장 느린 동기 단계를 낮은 비용으로 식별한다."""

        elapsed_ms = (time.perf_counter() - started) * 1_000
        self._live_event_phase_last_ms[name] = elapsed_ms
        if elapsed_ms >= 100:
            self._live_event_phase_over_100ms_count += 1
        if elapsed_ms <= self._live_event_phase_max_ms:
            return
        self._live_event_phase_max_ms = elapsed_ms
        self._live_event_phase_max_name = name
        self._live_event_phase_max_ts_ms = self.clock.utc_ms()
        self._live_event_phase_max_event_type = event.event_type
        self._live_event_phase_max_symbol = event.symbol

    @staticmethod
    def _persistable_market_event(event: MarketEvent) -> dict[str, object]:
        """리플레이에 필요한 상위 10단계 호가만 저장하고 LIVE 원본은 유지한다."""

        data = dict(event.data)
        receive_ts_ms = event.venue_ts_ms + max(
            0,
            round(event.quality.lag_ms if event.quality.lag_ms is not None else 0),
        )
        payload: dict[str, object] = {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "venue": event.venue.value,
            "symbol": event.symbol,
            "event_type": event.event_type,
            "venue_ts_ms": event.venue_ts_ms,
            "receive_ts_ms": receive_ts_ms,
            "transaction_ts_ms": event.transaction_ts_ms,
            "receive_monotonic_ns": event.receive_monotonic_ns,
            "sequence_start": event.sequence_start,
            "sequence_end": event.sequence_end,
            "previous_sequence_end": event.previous_sequence_end,
            "payload_version": event.payload_version,
            "quality": {
                "is_live": event.quality.is_live,
                "is_stale": event.quality.is_stale,
                "sequence_valid": event.quality.sequence_valid,
                "lag_ms": event.quality.lag_ms,
                "flags": list(event.quality.flags),
            },
            "data": data,
        }
        if event.event_type not in {"DEPTH_UPDATE", "ORDERBOOK"}:
            return payload
        bids = data.get("bids")
        asks = data.get("asks")
        if isinstance(bids, list):
            data["bids"] = bids[:10]
        if isinstance(asks, list):
            data["asks"] = asks[:10]
        return payload

    def _refresh_supervisor_entry_safety(self) -> None:
        """공개시장 지연 임계 초과를 신규 PAPER 진입 잠금에 즉시 연결한다."""

        if self._supervisor is None:
            return
        selection = self._supervisor.selection
        if selection is not None and selection is not self.live_selection:
            self.live_selection = selection
            self.wide_symbol_count = len(selection.wide_symbols)
            self.deep_symbol_count = len(selection.deep_symbols)
            self._record_universe_selection(selection, reason="SAFE_DEEP_ROTATION")
        telemetry = self._supervisor.telemetry
        self.processing_lag_p95_ms = telemetry.lag_p95_ms
        supervisor_flags = {
            "ENTRY_LOCK_PUBLIC_SUPERVISOR_NOT_RUNNING": not self._supervisor.running(),
            "ENTRY_LOCK_CONSUMER_NOT_RUNNING": not telemetry.consumer_running,
            "ENTRY_LOCK_CONSUMER_DELIVERY_FAULT": telemetry.consumer_fault_active,
            "ENTRY_LOCK_EVENT_QUEUE_OVERLOAD": telemetry.queue_overload_active,
        }
        for flag, active in supervisor_flags.items():
            if active and flag not in self.runtime_health_flags:
                self.runtime_health_flags.append(flag)
            elif not active:
                self.runtime_health_flags = [
                    current for current in self.runtime_health_flags if current != flag
                ]
        if telemetry.entry_locked:
            self.paused = True
            if "SUPERVISOR_ENTRY_LOCK" not in self.runtime_health_flags:
                self.runtime_health_flags.append("SUPERVISOR_ENTRY_LOCK")
        else:
            self.runtime_health_flags = [
                flag for flag in self.runtime_health_flags if flag != "SUPERVISOR_ENTRY_LOCK"
            ]
        critical_lag = telemetry.critical_lag_active
        if critical_lag:
            self.paused = True
            if "CRITICAL_MARKET_LAG_ENTRY_LOCK" not in self.runtime_health_flags:
                self.runtime_health_flags.append("CRITICAL_MARKET_LAG_ENTRY_LOCK")
            return
        self.runtime_health_flags = [
            flag for flag in self.runtime_health_flags if flag != "CRITICAL_MARKET_LAG_ENTRY_LOCK"
        ]
        blocking_flags = {
            flag
            for flag in self.runtime_health_flags
            if flag.startswith("ENTRY_LOCK_")
            or flag
            in {
                "PERSISTENCE_FAULT_ENTRY_LOCK",
                "PERSISTENCE_BACKLOG_ENTRY_LOCK",
                "RECOVERY_FAIL_CLOSED",
                "STORAGE_PRESSURE_ENTRY_LOCK",
                "SUPERVISOR_ENTRY_LOCK",
            }
        }
        if (
            self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            and self.market_data_state is MarketDataState.LIVE
            and not self._manual_pause_requested
            and not blocking_flags
            and not self.paper_portfolio.main.risk_state.faulted
            and self._storage_entry_allowed
        ):
            self.paused = False

    def _refresh_data_health_entry_safety(self) -> None:
        """모든 gap과 stale trade가 fresh depth로 회복된 뒤에만 잠금을 푼다."""

        if self.data_gap_since_ms or self._stale_trade_symbols:
            return
        if "ENTRY_LOCK_DATA_HEALTH" not in self.runtime_health_flags:
            return
        self.runtime_health_flags = [
            flag for flag in self.runtime_health_flags if flag != "ENTRY_LOCK_DATA_HEALTH"
        ]
        self._log("MARKET_DATA", "모든 공개시장 데이터 건강 재검증 완료")
        self._refresh_supervisor_entry_safety()

    def _record_feature_input_fault(self, symbol: str, error: Exception) -> None:
        self._feature_input_fault_symbols.add(symbol)
        self.paused = True
        if "ENTRY_LOCK_FEATURE_INPUT" not in self.runtime_health_flags:
            self.runtime_health_flags.append("ENTRY_LOCK_FEATURE_INPUT")
        self._log("MARKET_DATA", f"{symbol} 피처 입력 거부 · {type(error).__name__}")

    def _refresh_feature_input_entry_safety(self, symbol: str) -> None:
        self._feature_input_fault_symbols.discard(symbol)
        if self._feature_input_fault_symbols:
            return
        if "ENTRY_LOCK_FEATURE_INPUT" not in self.runtime_health_flags:
            return
        self.runtime_health_flags = [
            flag for flag in self.runtime_health_flags if flag != "ENTRY_LOCK_FEATURE_INPUT"
        ]
        self._log("MARKET_DATA", "모든 피처 입력 재검증 완료")
        self._refresh_supervisor_entry_safety()

    def _directional_change_engines_for_symbol(
        self,
        symbol: str,
    ) -> tuple[tuple[str, DirectionalChangeEngine], ...]:
        """회전 심볼을 유한하게 유지하며 종목별 관찰 엔진을 반환한다."""

        if symbol not in self._directional_change_symbols:
            if len(self._directional_change_symbols) >= _DIRECTIONAL_CHANGE_SYMBOL_LIMIT:
                expired_symbol = next(iter(self._directional_change_symbols))
                self._directional_change_symbols.pop(expired_symbol, None)
                for profile_id, _threshold in _DIRECTIONAL_CHANGE_PROFILES:
                    self._directional_change_engines.pop(
                        (expired_symbol, profile_id),
                        None,
                    )
            self._directional_change_symbols[symbol] = None
        engines: list[tuple[str, DirectionalChangeEngine]] = []
        for profile_id, threshold in _DIRECTIONAL_CHANGE_PROFILES:
            key = (symbol, profile_id)
            engine = self._directional_change_engines.get(key)
            if engine is None:
                engine = DirectionalChangeEngine(
                    run_id=self.run_id,
                    venue=self.venue,
                    symbol=symbol,
                    profile_id=profile_id,
                    threshold_provider=FixedThresholdProvider(
                        profile_id=profile_id,
                        threshold=threshold,
                    ),
                    dedupe_capacity=_DIRECTIONAL_CHANGE_DEDUPE_CAPACITY,
                )
                self._directional_change_engines[key] = engine
            engines.append((profile_id, engine))
        return tuple(engines)

    def _discard_directional_change_symbol(self, symbol: str) -> None:
        """안전한 mid를 만들 수 없으면 해당 종목 연속성을 즉시 폐기한다."""

        self._directional_change_symbols.pop(symbol, None)
        for profile_id, _threshold in _DIRECTIONAL_CHANGE_PROFILES:
            self._directional_change_engines.pop((symbol, profile_id), None)
            self._directional_change_initialized[profile_id] = False
            self._directional_change_last_directions[profile_id] = DCState.UNINITIALIZED

    @staticmethod
    def _directional_change_top_prices(event: MarketEvent) -> tuple[Decimal, Decimal]:
        bids_value = event.data.get("bids")
        asks_value = event.data.get("asks")
        bid = (
            Decimal(str(bids_value[0][0]))
            if isinstance(bids_value, list) and bids_value
            else Decimal(str(event.data["bid"]))
        )
        ask = (
            Decimal(str(asks_value[0][0]))
            if isinstance(asks_value, list) and asks_value
            else Decimal(str(event.data["ask"]))
        )
        return bid, ask

    def _observe_directional_change(
        self,
        event: MarketEvent,
        *,
        bid: Decimal | None = None,
        ask: Decimal | None = None,
    ) -> None:
        """실제 depth mid를 진입과 분리된 FAST·SWING DC 상태에만 반영한다."""

        quality_fault = event.quality.is_stale or not event.quality.sequence_valid
        try:
            if bid is None or ask is None:
                try:
                    bid, ask = self._directional_change_top_prices(event)
                except (ArithmeticError, KeyError, IndexError, TypeError, ValueError):
                    previous_book = self.latest_books.get(event.symbol)
                    if not quality_fault or previous_book is None:
                        raise
                    bid = previous_book.bids[0][0]
                    ask = previous_book.asks[0][0]
            observation = DCMidObservation(
                run_id=self.run_id,
                venue=self.venue,
                symbol=event.symbol,
                event_id=event.event_id,
                venue_ts_ms=event.venue_ts_ms,
                receive_monotonic_ns=event.receive_monotonic_ns,
                bid=bid,
                ask=ask,
                sequence_start=event.sequence_start,
                sequence_end=event.sequence_end,
                previous_sequence_end=event.previous_sequence_end,
                sequence_valid=event.quality.sequence_valid,
                stale=event.quality.is_stale,
                lag_ms=(
                    Decimal(str(event.quality.lag_ms)) if event.quality.lag_ms is not None else None
                ),
            )
            updates = tuple(
                (profile_id, engine.update(observation))
                for profile_id, engine in self._directional_change_engines_for_symbol(event.symbol)
            )
        except (ArithmeticError, KeyError, IndexError, TypeError, ValueError):
            self._discard_directional_change_symbol(event.symbol)
            return
        if quality_fault and any(update.reset is None for _profile_id, update in updates):
            # 중복 event ID도 stale·sequence-invalid 상태를 유지시키지 않는다.
            self._discard_directional_change_symbol(event.symbol)
            return
        for profile_id, update in updates:
            snapshot = update.snapshot
            self._directional_change_initialized[profile_id] = (
                snapshot.threshold is not None and snapshot.event_start_price is not None
            )
            self._directional_change_last_directions[profile_id] = snapshot.state
            if update.event is not None:
                self._directional_change_event_counts[profile_id] += 1
                self._directional_change_last_confirmation_types[profile_id] = (
                    update.event.event_type.value
                )

    def _semivariance_engine_for_symbol(self, symbol: str) -> SemivarianceJumpEngine:
        """완료 1분봉 관찰 종목을 최대 24개로 유지한다."""

        if symbol not in self._semivariance_symbols:
            if len(self._semivariance_symbols) >= _SEMIVARIANCE_SYMBOL_LIMIT:
                expired_symbol = next(iter(self._semivariance_symbols))
                self._semivariance_symbols.pop(expired_symbol, None)
                self._semivariance_engines.pop(expired_symbol, None)
                self._semivariance_previous_completed_closes.pop(expired_symbol, None)
                self._semivariance_latest_snapshots.pop(expired_symbol, None)
            self._semivariance_symbols[symbol] = None
        engine = self._semivariance_engines.get(symbol)
        if engine is None:
            # 8주 완료 보정 자료가 없으므로 Jump는 미보정 WAIT을 유지한다.
            engine = SemivarianceJumpEngine(periodicity=None)
            self._semivariance_engines[symbol] = engine
        return engine

    def _reset_semivariance_symbol(self, symbol: str, reason: str) -> None:
        """공백·역순·불완전 입력이 발생하면 종목 연속성을 폐기한다."""

        self._semivariance_symbols.pop(symbol, None)
        self._semivariance_engines.pop(symbol, None)
        self._semivariance_previous_completed_closes.pop(symbol, None)
        self._semivariance_latest_snapshots.pop(symbol, None)
        self._semivariance_last_symbol = symbol
        self._semivariance_last_status = "RESET"
        self._semivariance_last_reset_reason = reason

    def _observe_completed_minute_candles(
        self,
        candles: Sequence[Candle],
        *,
        completed_ts_ms: int,
    ) -> None:
        """CandleBuilder가 반환한 새 완료 1분봉만 한 번씩 관찰한다."""

        for candle in candles:
            if candle.interval_seconds == _SEMIVARIANCE_MINUTE_SECONDS:
                self._observe_completed_minute_candle(
                    candle,
                    completed_ts_ms=completed_ts_ms,
                )

    def _observe_completed_minute_candle(
        self,
        candle: Candle,
        *,
        completed_ts_ms: int,
    ) -> None:
        if (
            candle.interval_seconds != _SEMIVARIANCE_MINUTE_SECONDS
            or candle.open_ts_ms < 0
            or candle.open_ts_ms % _SEMIVARIANCE_MINUTE_MS != 0
            or not candle.close.is_finite()
            or candle.close <= 0
            or completed_ts_ms < candle.open_ts_ms + _SEMIVARIANCE_MINUTE_MS
        ):
            self._reset_semivariance_symbol(candle.symbol, "INCOMPLETE_OR_INVALID_MINUTE")
            return
        completion_bucket = completed_ts_ms - completed_ts_ms % _SEMIVARIANCE_MINUTE_MS
        if completion_bucket != candle.open_ts_ms + _SEMIVARIANCE_MINUTE_MS:
            self._reset_semivariance_symbol(candle.symbol, "COMPLETED_MINUTE_GAP")
            return

        engine = self._semivariance_engine_for_symbol(candle.symbol)
        previous = self._semivariance_previous_completed_closes.get(candle.symbol)
        if previous is None:
            self._semivariance_previous_completed_closes[candle.symbol] = (
                candle.open_ts_ms,
                candle.close,
            )
            self._semivariance_last_symbol = candle.symbol
            self._semivariance_last_completed_minute_ts_ms = candle.open_ts_ms
            self._semivariance_last_status = "WAITING_PREVIOUS_CLOSE"
            return
        previous_ts_ms, previous_close = previous
        if candle.open_ts_ms == previous_ts_ms:
            if candle.close != previous_close:
                self._reset_semivariance_symbol(
                    candle.symbol,
                    "DUPLICATE_MINUTE_CONFLICT",
                )
            return
        if candle.open_ts_ms < previous_ts_ms:
            self._reset_semivariance_symbol(
                candle.symbol,
                "OUT_OF_ORDER_COMPLETED_MINUTE",
            )
            return
        if candle.open_ts_ms != previous_ts_ms + _SEMIVARIANCE_MINUTE_MS:
            self._reset_semivariance_symbol(candle.symbol, "COMPLETED_MINUTE_GAP")
            self._semivariance_engine_for_symbol(candle.symbol)
            self._semivariance_previous_completed_closes[candle.symbol] = (
                candle.open_ts_ms,
                candle.close,
            )
            self._semivariance_last_completed_minute_ts_ms = candle.open_ts_ms
            self._semivariance_last_status = "WAITING_PREVIOUS_CLOSE"
            return
        try:
            with localcontext() as context:
                context.prec = 50
                log_return = (candle.close / previous_close).ln()
            snapshot = engine.update(
                CompletedMinuteReturn(
                    minute_start_ts_ms=candle.open_ts_ms,
                    completed_ts_ms=completed_ts_ms,
                    log_return=log_return,
                )
            )
        except (ArithmeticError, SemivarianceInputError):
            self._reset_semivariance_symbol(candle.symbol, "SEMIVARIANCE_INPUT_REJECTED")
            return
        self._semivariance_previous_completed_closes[candle.symbol] = (
            candle.open_ts_ms,
            candle.close,
        )
        self._semivariance_latest_snapshots[candle.symbol] = snapshot
        self._semivariance_last_symbol = candle.symbol
        self._semivariance_last_completed_minute_ts_ms = candle.open_ts_ms
        self._semivariance_last_reset_reason = "NONE"
        if snapshot.four_hour.status is SemivarianceReadiness.READY:
            self._semivariance_last_status = "SEMIVARIANCE_READY_JUMP_UNCALIBRATED"
        elif snapshot.one_hour.status is SemivarianceReadiness.READY:
            self._semivariance_last_status = "WARMUP_4H"
        else:
            self._semivariance_last_status = "WARMUP_1H"

    def _protected_deep_symbols(self) -> tuple[str, ...]:
        protected = [self.selected_symbol]
        pending = self.paper_portfolio.main.pending_entry
        if pending is not None:
            protected.append(pending.plan.symbol)
        position = self.paper_portfolio.main.position
        if position is not None:
            protected.append(position.plan.symbol)
        for account in self.paper_portfolio.shadows.values():
            protected.extend(entry.plan.symbol for entry in account.pending_entries.values())
            protected.extend(item.plan.symbol for item in account.positions.values())
        return tuple(dict.fromkeys(protected))

    def _record_universe_selection(
        self,
        selection: ProviderSelection,
        *,
        reason: str,
    ) -> None:
        """회전 감사행을 이벤트 루프 밖에서 저장하도록 유실 없는 큐에 넣는다."""

        if self.ledger is None or self.mode is RuntimeMode.READY:
            return
        timestamp = self.clock.utc_ms()
        snapshot: dict[str, object] = {
            "snapshot_id": f"universe-{self.run_id}-{timestamp}-{uuid4().hex[:6]}",
            "run_id": self.run_id,
            "ts_ms": timestamp,
            "venue": selection.venue.value,
            "wide_symbols": list(selection.wide_symbols),
            "deep_symbols": list(selection.deep_symbols),
            "reason": reason,
            "rotation_interval_seconds": 900,
            "minimum_residency_seconds": 1800,
            "maximum_replacements": 4,
            "selection_policy": "LIQUIDITY_CORE_PLUS_ABSOLUTE_24H_CHANGE",
            "liquidity_core_target": _LIVE_DEEP_SYMBOL_TARGET // 2,
            "opportunity_target": _LIVE_DEEP_SYMBOL_TARGET // 2,
            "protected_symbols": list(self._protected_deep_symbols()),
        }
        with self._persistence_lock:
            self._universe_snapshot_buffer.append(snapshot)

    def _evaluate_book_event(
        self,
        event: MarketEvent,
        *,
        persist_execution: bool = True,
    ) -> _PreparedStrategyEvaluation | None:
        prepared = self._prepare_strategy_evaluation(
            event,
            persist_execution=persist_execution,
        )
        if prepared is None:
            return None
        if _DEFER_STRATEGY_EVALUATION.get():
            return prepared
        phase_started = time.perf_counter()
        signals = self._evaluate_prepared_strategy(prepared)
        self._record_live_event_phase("STRATEGY_EVALUATION", phase_started, event)
        self._complete_strategy_evaluation(
            prepared,
            signals,
            persist_execution=persist_execution,
        )
        return None

    def _prepare_strategy_evaluation(
        self,
        event: MarketEvent,
        *,
        persist_execution: bool,
    ) -> _PreparedStrategyEvaluation | None:
        if event.quality.is_stale or not event.quality.sequence_valid:
            # 원본 이벤트와 data-gap 시작점은 보존하지만, 오래되거나 끊긴 호가로
            # 최신 체결호가·피처 이력·전략 후보·포지션 관리를 갱신하지 않는다.
            return None
        phase_started = time.perf_counter()
        try:
            bids_value = event.data.get("bids")
            asks_value = event.data.get("asks")
            bids = (
                tuple((Decimal(str(row[0])), Decimal(str(row[1]))) for row in bids_value)
                if isinstance(bids_value, list)
                else ((Decimal(str(event.data["bid"])), Decimal(str(event.data["bid_qty"]))),)
            )
            asks = (
                tuple((Decimal(str(row[0])), Decimal(str(row[1]))) for row in asks_value)
                if isinstance(asks_value, list)
                else ((Decimal(str(event.data["ask"])), Decimal(str(event.data["ask_qty"]))),)
            )
            frame = BookFrame.from_levels(
                venue=event.venue,
                symbol=event.symbol,
                ts_ms=event.venue_ts_ms,
                bids=bids,
                asks=asks,
                sequence_valid=event.quality.sequence_valid,
                stale=event.quality.is_stale,
                lag_ms=event.quality.lag_ms or 0.0,
            )
            book = BookSnapshot(
                venue=event.venue,
                symbol=event.symbol,
                ts_ms=event.venue_ts_ms,
                bids=frame.bids,
                asks=frame.asks,
                sequence_valid=event.quality.sequence_valid,
                stale=event.quality.is_stale,
                receive_ts_ms=event.venue_ts_ms
                + max(
                    0,
                    round(event.quality.lag_ms if event.quality.lag_ms is not None else 0),
                ),
            )
            book.validate()
        except (
            ArithmeticError,
            FeatureInputError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as error:
            self._record_live_event_phase("BOOK_BUILD", phase_started, event)
            self._record_feature_input_fault(event.symbol, error)
            return None
        self._record_live_event_phase("BOOK_BUILD", phase_started, event)
        phase_started = time.perf_counter()
        self.latest_books[event.symbol] = book
        self.paper_portfolio.on_book(book)
        self._record_live_event_phase("PAPER_PORTFOLIO_ON_BOOK", phase_started, event)
        self._observe_directional_change(
            event,
            bid=book.bids[0][0],
            ask=book.asks[0][0],
        )
        if persist_execution:
            phase_started = time.perf_counter()
            self._persist_execution_state_safely(event.venue_ts_ms)
            self._record_live_event_phase("EXECUTION_PERSISTENCE", phase_started, event)
        phase_started = time.perf_counter()
        if (
            event.symbol in self._recovery_revalidation_symbols
            and event.quality.sequence_valid
            and not event.quality.is_stale
        ):
            self._recovery_revalidation_symbols.discard(event.symbol)
            if not self._recovery_revalidation_symbols:
                self.runtime_health_flags = [
                    flag
                    for flag in self.runtime_health_flags
                    if flag != "ENTRY_LOCK_RECOVERY_REVALIDATION"
                ]
                self.paused = (
                    self._manual_pause_requested
                    or self.paper_portfolio.main.risk_state.faulted
                    or (self._supervisor is not None and self._supervisor.telemetry.entry_locked)
                    or not self._refresh_storage_safety()
                )
            self._log(
                "RECOVERY",
                f"{event.symbol} fresh sequence-valid 호가 재검증 완료",
            )
        self.position_visible = self.paper_portfolio.main.position is not None
        portfolio_summary = self.paper_portfolio.main_summary(self._current_main_book())
        self.unrealized_pnl_usdt = float(portfolio_summary["unrealized"])
        self._record_live_event_phase("POSITION_STATE", phase_started, event)
        phase_started = time.perf_counter()
        engine = self.feature_engines.setdefault(event.symbol, FeatureEngine())
        try:
            engine.ingest_book(frame)
            snapshot = engine.snapshot()
            if event.symbol in self._stale_trade_symbols:
                snapshot = replace(snapshot, data_healthy=False)
            regime = self.regime_classifier.classify(snapshot)
        except (FeatureInputError, KeyError, IndexError, ValueError) as error:
            self._record_live_event_phase("FEATURE_SNAPSHOT", phase_started, event)
            self._record_feature_input_fault(event.symbol, error)
            return None
        self._record_live_event_phase("FEATURE_SNAPSHOT", phase_started, event)
        phase_started = time.perf_counter()
        self._refresh_feature_input_entry_safety(event.symbol)
        self.latest_features[event.symbol] = snapshot
        self.latest_regimes[event.symbol] = regime
        gap_started = self.data_gap_since_ms.pop(event.symbol, None)
        self.paper_portfolio.evaluate_health(
            snapshot,
            regime,
            now_ms=event.venue_ts_ms,
            book=book,
            recovered_gap_duration_ms=(
                max(0, event.venue_ts_ms - gap_started) if gap_started is not None else 0
            ),
        )
        self._refresh_data_health_entry_safety()
        self._record_live_event_phase("HEALTH_EVALUATION", phase_started, event)
        last_evaluation = self._last_strategy_evaluation_ms.get(event.symbol)
        if (
            last_evaluation is not None
            and event.venue_ts_ms - last_evaluation < self.strategy_evaluation_interval_ms
        ):
            return None
        self._last_strategy_evaluation_ms[event.symbol] = event.venue_ts_ms
        for side in (Side.LONG, Side.SHORT):
            self.orderflow_confirmation_runtime.evaluate(snapshot, side)
        instrument = (
            self.live_selection.instruments.get(event.symbol)
            if self.live_selection is not None
            else None
        )
        tick_size = instrument.tick_size if instrument is not None else Decimal("0.00000001")
        strategy_registry, settings_revisions = self._strategy_evaluation_registry_snapshot()
        return _PreparedStrategyEvaluation(
            event=event,
            snapshot=snapshot,
            regime=regime,
            book=book,
            strategy_registry=strategy_registry,
            settings_revisions=settings_revisions,
            tick_size=tick_size,
            fifteen_minute_candles=self.strategy_completed_candles(event.symbol, 900),
            thirty_minute_candles=self.strategy_completed_candles(event.symbol, 1_800),
            hourly_candles=self.hourly_completed_candles(event.symbol),
        )

    def _strategy_evaluation_registry_snapshot(
        self,
    ) -> tuple[StrategyRegistry, tuple[tuple[str, int], ...]]:
        """한 평가가 동일한 전략 설정 revision만 읽도록 얕은 사본을 만든다."""

        registry = self.strategy_registry
        with registry._setting_lock:
            snapshot = copy.copy(registry)
            snapshot._setting_lock = RLock()
            snapshot._settings = {
                strategy_id: replace(registry.setting(strategy_id))
                for strategy_id in registry.strategy_ids
            }
            revisions = tuple(
                (strategy_id, setting.revision)
                for strategy_id, setting in snapshot._settings.items()
            )
        return snapshot, revisions

    def _strategy_process_state_key(self) -> str:
        """Run·거래소·전략 버전·데이터 gap이 바뀌면 자식 이력을 분리한다."""

        return (
            f"{self.run_id}:{self.venue.value}:{STRATEGY_VERSION}:"
            f"health-{self._strategy_data_health_epoch}"
        )

    def _strategy_settings_revisions(self) -> tuple[tuple[str, int], ...]:
        registry = self.strategy_registry
        with registry._setting_lock:
            return tuple(
                (strategy_id, registry.setting(strategy_id).revision)
                for strategy_id in registry.strategy_ids
            )

    def _evaluate_prepared_strategy(
        self,
        prepared: _PreparedStrategyEvaluation,
    ) -> tuple[EvaluatedSignal, ...]:
        """fixture·replay의 결정적 동기 경로에서 기존 evaluator를 실행한다."""

        with self._strategy_evaluation_lock:
            return self.strategy_evaluator.evaluate(
                prepared.strategy_registry,
                prepared.snapshot,
                prepared.regime,
                tick_size=prepared.tick_size,
                fifteen_minute_candles=prepared.fifteen_minute_candles,
                thirty_minute_candles=prepared.thirty_minute_candles,
                hourly_candles=prepared.hourly_candles,
            )

    def _complete_strategy_evaluation(
        self,
        prepared: _PreparedStrategyEvaluation,
        signals: tuple[EvaluatedSignal, ...],
        *,
        persist_execution: bool,
        process_request: StrategyEvaluationRequest | None = None,
        process_result: StrategyEvaluationResult | None = None,
    ) -> None:
        event = prepared.event
        if self._strategy_settings_revisions() != prepared.settings_revisions:
            self._log(
                "STRATEGY",
                f"{event.symbol} 평가 중 설정 revision 변경 · 오래된 후보 적용 생략",
            )
            return
        if (process_request is None) is not (process_result is None):
            raise RuntimeError("전략 프로세스 요청과 결과는 함께 적용해야 합니다.")
        if process_request is not None and process_result is not None:
            if process_request.state_key != self._strategy_process_state_key():
                self._log(
                    "STRATEGY",
                    f"{event.symbol} 이전 Run 전략 평가 결과 적용 생략",
                )
                return
            if not self._live_strategy_evaluator.accept_result(
                process_request,
                process_result,
            ):
                self._log(
                    "STRATEGY",
                    f"{event.symbol} 이전 Run 전략 평가 결과 적용 생략",
                )
                return
        self.strategy_evaluation_count += len(signals)
        self.qualified_signal_count += sum(
            signal.decision.status.value == "QUALIFIED" for signal in signals
        )
        for signal in signals:
            key = (
                signal.symbol,
                signal.decision.strategy_id,
                signal.decision.side.value,
            )
            self.strategy_signals[key] = signal
        phase_started = time.perf_counter()
        plans = self._build_candidate_plans(
            event,
            prepared.snapshot,
            prepared.regime,
            prepared.book,
            signals,
        )
        self._record_live_event_phase("CANDIDATE_PLANNING", phase_started, event)
        phase_started = time.perf_counter()
        storage_ready = self._refresh_storage_safety()
        self._record_live_event_phase("STORAGE_SAFETY", phase_started, event)
        phase_started = time.perf_counter()
        self.paper_portfolio.offer(
            plans,
            entries_paused=self.paused or not storage_ready,
        )
        self._record_live_event_phase("PORTFOLIO_OFFER", phase_started, event)
        if persist_execution:
            phase_started = time.perf_counter()
            self._persist_execution_state_safely(event.venue_ts_ms)
            self._record_live_event_phase("EXECUTION_PERSISTENCE", phase_started, event)

    def _build_candidate_plans(
        self,
        event: MarketEvent,
        snapshot: FeatureSnapshot,
        regime: Regime,
        book: BookSnapshot,
        signals: tuple[EvaluatedSignal, ...],
    ) -> tuple[CandidatePlan, ...]:
        instrument = (
            self.live_selection.instruments.get(event.symbol)
            if self.live_selection is not None
            else None
        )
        if instrument is None:
            instrument = Instrument(
                venue=event.venue,
                symbol=event.symbol,
                base_asset=event.symbol.removesuffix("USDT"),
                quote_asset="USDT",
                status="TEST",
                contract_type="PAPER",
                tick_size=Decimal("0.00000001"),
                quantity_step=Decimal("0.001"),
                minimum_quantity=Decimal("0.001"),
            )
        plans: list[CandidatePlan] = []
        for signal in signals:
            if (
                signal.decision.status.value == "QUALIFIED"
                and signal.decision.strategy_id in ORDERFLOW_AFFECTED_STRATEGY_IDS
                and not self.orderflow_confirmation_runtime.allows_strategy(
                    signal.decision.strategy_id,
                    signal.decision.side,
                    event.symbol,
                )
            ):
                filter_decision = self.orderflow_confirmation_runtime.decision_for(
                    event.symbol,
                    signal.decision.side,
                )
                self.plan_rejections.append(
                    {
                        "event_id": event.event_id,
                        "symbol": event.symbol,
                        "strategy_id": signal.decision.strategy_id,
                        "side": signal.decision.side.value,
                        "reason_codes": [
                            "ORDERFLOW_CONFIRMATION_FILTER_BLOCKED",
                            *(
                                filter_decision.reason_codes
                                if filter_decision is not None
                                else ("ORDERFLOW_CONFIRMATION_MISSING",)
                            ),
                        ],
                        "filter_id": "ORDERFLOW_CONFIRMATION_FILTER_V2",
                        "creates_candidate_plan": False,
                    }
                )
                continue
            descriptor = self.strategy_registry.descriptor(signal.decision.strategy_id)
            result = self.candidate_planner.build(
                signal_event_id=event.event_id,
                run_id=self.run_id,
                venue=self.venue,
                decision=signal.decision,
                snapshot=snapshot,
                regime=regime,
                book=book,
                instrument=instrument,
                signal_time_ms=event.venue_ts_ms,
                risk_state=self.paper_portfolio.main.risk_state,
                main_eligible=signal.main_eligible,
                shadow_eligible=signal.shadow_eligible,
                exit_style=descriptor.exit_style,
                trend_take_profit_1_r=descriptor.take_profit_1_r,
                trend_take_profit_2_r=descriptor.take_profit_2_r,
                maximum_holding_ms=(
                    descriptor.max_hold_seconds * 1_000
                    if descriptor.max_hold_seconds is not None
                    else None
                ),
                edge_decay_enabled=descriptor.edge_decay_enabled,
                strategy_version=STRATEGY_VERSION,
                shared_capital_evidence=self._shared_capital_arbitration_evidence(
                    signal.decision.strategy_id
                ),
            )
            if result.plan is not None:
                plans.append(result.plan)
                if self.ledger is not None:
                    # 후보 SQLite FULL 커밋은 LIVE 판단 이벤트 루프에서 실행하지 않는다.
                    # 같은 이벤트의 주문·감사·복구 상태와 worker thread의 원자 배치로 저장한다.
                    with self._persistence_lock:
                        self._candidate_plan_buffer.append(self._candidate_plan_row(result.plan))
            elif result.rejection_codes != ("STRATEGY_NOT_QUALIFIED",):
                self.plan_rejections.append(
                    {
                        "event_id": event.event_id,
                        "symbol": event.symbol,
                        "strategy_id": signal.decision.strategy_id,
                        "side": signal.decision.side.value,
                        "reason_codes": list(result.rejection_codes),
                    }
                )
        return tuple(plans)

    def _shared_capital_arbitration_evidence(
        self,
        strategy_id: str,
    ) -> SharedCapitalArbitrationEvidence:
        """무거운 원장 조회 없이 마지막 검증 cache만 Shared Capital 중재에 사용한다."""

        lifecycle_tiers = {
            StrategyLifecycle.RESEARCH: 1,
            StrategyLifecycle.SHADOW: 2,
            StrategyLifecycle.CHALLENGER: 3,
            StrategyLifecycle.ACTIVE: 4,
        }
        tier = lifecycle_tiers.get(
            self.strategy_registry.setting(strategy_id).lifecycle,
            0,
        )
        cached = (
            self._strategy_arbitration_evidence_cache.get(strategy_id)
            if self._strategy_arbitration_evidence_ready
            else None
        )
        return SharedCapitalArbitrationEvidence(
            evidence_tier=tier,
            stress_cost_adjusted_expectancy_usdt=(
                cached.stress_cost_adjusted_expectancy_usdt if cached is not None else None
            ),
            cost_coverage=cached.cost_coverage if cached is not None else None,
            diversification_score=(
                cached.diversification_score if cached is not None else Decimal(0)
            ),
        )

    def _hydrate_strategy_arbitration_evidence(
        self,
        reports: Sequence[Mapping[str, object]],
    ) -> None:
        """UI 접근과 무관한 current-version STRESS evidence cache를 교체한다."""

        cache: dict[str, SharedCapitalArbitrationEvidence] = {}
        for report in reports:
            if str(report.get("profile", "")) != "STRESS":
                continue
            strategy_id = str(report.get("strategy_id", ""))
            if not strategy_id:
                continue
            expectancy_value = report.get("expectancy_usdt")
            coverage_value = report.get("cost_coverage")
            diversification_value = report.get("diversification_score")
            diversification = (
                Decimal(str(diversification_value))
                if diversification_value is not None
                else Decimal(0)
            )
            cache[strategy_id] = SharedCapitalArbitrationEvidence(
                stress_cost_adjusted_expectancy_usdt=(
                    Decimal(str(expectancy_value)) if expectancy_value is not None else None
                ),
                cost_coverage=(
                    Decimal(str(coverage_value)) if coverage_value is not None else None
                ),
                diversification_score=max(Decimal(0), min(Decimal(1), diversification)),
            )
        self._strategy_arbitration_evidence_cache = cache
        self._strategy_arbitration_evidence_ready = True

    def configure_strategy(
        self,
        strategy_id: str,
        *,
        mode: StrategyMode,
        long_enabled: bool,
        short_enabled: bool,
        expected_revision: int | None = None,
        manual_lock: bool | None = None,
        lifecycle: StrategyLifecycle | None = None,
        source: str = "USER_UI",
        reason: str = "USER_CONFIGURATION",
    ) -> None:
        if self.strategy_registry.is_policy_retired(strategy_id) and mode is not StrategyMode.OFF:
            raise ValueError("비용후 연구에서 퇴역한 전략은 새 검증 전 재활성화할 수 없습니다.")
        timestamp = self.clock.utc_ms()
        current_setting = self.strategy_registry.setting(strategy_id)
        resolved_lifecycle = lifecycle
        if resolved_lifecycle is None and mode is not current_setting.mode:
            resolved_lifecycle = self.strategy_registry.lifecycle_for_mode(mode)
        setting = self.strategy_registry.configure(
            strategy_id,
            mode=mode,
            long_enabled=long_enabled,
            short_enabled=short_enabled,
            expected_revision=expected_revision,
            manual_lock=manual_lock,
            lifecycle=resolved_lifecycle,
            source=StrategyChangeSource(source),
            reason=reason,
            updated_ts_ms=timestamp,
        )
        self._log(
            "STRATEGY",
            f"{strategy_id} {mode.value} · LONG {long_enabled} · SHORT {short_enabled}"
            f" · rev {setting.revision} · {source}",
        )
        transition = self._persist_strategy_setting(
            self.strategy_registry.setting_row(strategy_id),
            timestamp=timestamp,
        )
        self._record_strategy_incident(
            category="STRATEGY_SETTINGS_TRANSITION",
            timestamp=timestamp,
            payload=transition,
        )

    def rollback_strategy(
        self,
        strategy_id: str,
        *,
        target_revision: int,
        expected_revision: int,
        reason: str,
    ) -> dict[str, object]:
        """과거 전략 설정을 새 revision으로 복원하고 불변 이력을 남긴다."""

        if self.strategy_registry.is_policy_retired(strategy_id):
            raise ValueError(
                "비용후 검증으로 퇴역한 전략은 새 연구 승인 전 과거 설정으로 복원할 수 없습니다."
            )
        timestamp = self.clock.utc_ms()
        self.strategy_registry.rollback(
            strategy_id,
            target_revision=target_revision,
            expected_revision=expected_revision,
            source=StrategyChangeSource.USER_UI,
            reason=reason,
            updated_ts_ms=timestamp,
        )
        row = self.strategy_registry.setting_row(strategy_id)
        transition = self._persist_strategy_setting(
            row,
            timestamp=timestamp,
            evidence={"rollback_target_revision": target_revision},
        )
        self._record_strategy_incident(
            category="STRATEGY_SETTINGS_ROLLBACK",
            timestamp=timestamp,
            payload=transition | {"rollback_target_revision": target_revision},
        )
        return row

    def apply_strategy_governance(
        self,
        strategy_id: str,
        evidence: GovernanceEvidence,
        *,
        expected_revision: int,
        assessment_ts_ms: int | None = None,
    ) -> tuple[dict[str, object], ...]:
        """검증된 증거로만 governor 전환을 적용하고 이유와 기간을 저장한다."""

        timestamp = self.clock.utc_ms() if assessment_ts_ms is None else assessment_ts_ms
        assessment = self.strategy_governor.assess(
            self.strategy_registry,
            strategy_id,
            evidence,
            assessment_ts_ms=timestamp,
        )
        changed = self.strategy_governor.apply(
            self.strategy_registry,
            assessment,
            expected_revision=expected_revision,
            updated_ts_ms=timestamp,
        )
        metadata = {
            "assessment": assessment.as_dict(),
            "evidence": evidence.as_dict(),
        }
        release_commit = git_commit()
        for row in changed:
            row_strategy_id = str(row["strategy_id"])
            row_metadata = metadata | {
                "lineage": {
                    "schema_version": 1,
                    "run_id": self.run_id,
                    "strategy_id": row_strategy_id,
                    "strategy_version": STRATEGY_VERSION,
                    "descriptor_strategy_version": self.strategy_registry.descriptor(
                        row_strategy_id
                    ).research_contract.strategy_version,
                    "app_version": APP_VERSION,
                    "release_commit": release_commit,
                    "assessment_ts_ms": timestamp,
                    "settings_revision": int(str(row["settings_revision"])),
                }
            }
            transition = self._persist_strategy_setting(
                row,
                timestamp=timestamp,
                evidence=row_metadata,
            )
            self._record_strategy_incident(
                category="AUTO_GOVERNOR_TRANSITION",
                timestamp=timestamp,
                payload=transition | row_metadata,
            )
        return changed

    def _strategy_governance_operational_evidence(
        self,
        strategy_id: str,
        accounts: Sequence[Mapping[str, object]],
        *,
        evaluated_ts_ms: int,
    ) -> dict[str, object]:
        """두 격리 계좌와 runtime fault를 같은 cycle 시각에 fail-closed 평가한다."""

        strategy_accounts = [
            account for account in accounts if account.get("strategy_id") == strategy_id
        ]
        expected_profiles = {CostProfile.BASE.value, CostProfile.STRESS.value}
        expected_account_ids = {f"{strategy_id}:{profile}" for profile in expected_profiles}
        accounts_proven_healthy = (
            len(strategy_accounts) == 2
            and {account.get("profile") for account in strategy_accounts} == expected_profiles
            and {account.get("account_id") for account in strategy_accounts} == expected_account_ids
            and all(account.get("faulted") is False for account in strategy_accounts)
        )
        account_fault = any(account.get("faulted") is True for account in strategy_accounts)
        main_risk_fault = self.paper_portfolio.main.risk_state.faulted is True
        persistence_fault = self._persistence_fault_active is True
        supervisor = self._supervisor
        telemetry = getattr(supervisor, "telemetry", None)
        try:
            supervisor_running = supervisor is not None and supervisor.running() is True
        except Exception:
            supervisor_running = False
        consumer_running = getattr(telemetry, "consumer_running", None) is True
        consumer_fault = getattr(telemetry, "consumer_fault_active", None) is True
        queue_fault = getattr(telemetry, "queue_overload_active", None) is True
        supervisor_proven_healthy = (
            supervisor_running
            and consumer_running
            and getattr(telemetry, "consumer_fault_active", None) is False
            and getattr(telemetry, "queue_overload_active", None) is False
            and getattr(telemetry, "entry_locked", None) is False
            and getattr(telemetry, "critical_lag_active", None) is False
        )
        runtime_scope_proven_healthy = (
            self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            and self.market_data_state is MarketDataState.LIVE
            and self._manual_pause_requested is False
            and self.paused is False
            and self._storage_entry_allowed is True
        )
        data_health_proven = (
            not self.data_gap_since_ms
            and not self._stale_trade_symbols
            and not self._feature_input_fault_symbols
            and not self._recovery_revalidation_symbols
        )
        health_flags_proven = all(
            isinstance(flag, str) and "ENTRY_LOCK" not in flag and "RECOVERY" not in flag
            for flag in self.runtime_health_flags
        )
        operational_health_passed = (
            accounts_proven_healthy
            and not main_risk_fault
            and not persistence_fault
            and supervisor_proven_healthy
            and runtime_scope_proven_healthy
            and data_health_proven
            and health_flags_proven
        )
        return {
            "operational_fault": (
                account_fault
                or main_risk_fault
                or persistence_fault
                or consumer_fault
                or queue_fault
            ),
            "operational_health_passed": operational_health_passed,
            "operational_health_evaluated_ts_ms": (
                evaluated_ts_ms if operational_health_passed else None
            ),
        }

    def _restore_live_operational_quarantine(
        self,
        operational_by_strategy: Mapping[str, Mapping[str, object]],
        *,
        evaluated_ts_ms: int,
    ) -> tuple[dict[str, object], ...]:
        """현재 LIVE 운영건강을 다시 입증한 전역 격리 cohort만 복구한다."""

        eligible_strategy_ids = tuple(
            strategy_id
            for strategy_id in self.strategy_registry.strategy_ids
            if (
                self.strategy_registry.descriptor(strategy_id).role is StrategyRole.ENTRY
                and self.strategy_registry.descriptor(strategy_id).default_research_enabled
                and not self.strategy_registry.is_policy_retired(strategy_id)
            )
        )
        if not eligible_strategy_ids or any(
            operational_by_strategy.get(strategy_id, {}).get("operational_health_passed")
            is not True
            or operational_by_strategy.get(strategy_id, {}).get("operational_fault") is not False
            for strategy_id in eligible_strategy_ids
        ):
            return ()

        changed = self.strategy_registry.restore_operationally_quarantined_research_defaults(
            updated_ts_ms=evaluated_ts_ms,
            source=StrategyChangeSource.RECOVERY,
        )
        release_commit = git_commit()
        for row in changed:
            strategy_id = str(row["strategy_id"])
            metadata = {
                "recovery_scope": "STRICT_GLOBAL_OPERATIONAL_QUARANTINE_COHORT",
                "operational": dict(operational_by_strategy[strategy_id]),
                "lineage": {
                    "schema_version": 1,
                    "run_id": self.run_id,
                    "strategy_id": strategy_id,
                    "strategy_version": STRATEGY_VERSION,
                    "descriptor_strategy_version": self.strategy_registry.descriptor(
                        strategy_id
                    ).research_contract.strategy_version,
                    "app_version": APP_VERSION,
                    "release_commit": release_commit,
                    "revalidated_ts_ms": evaluated_ts_ms,
                    "settings_revision": int(str(row["settings_revision"])),
                },
            }
            transition = self._persist_strategy_setting(
                row,
                timestamp=evaluated_ts_ms,
                evidence=metadata,
            )
            self._record_strategy_incident(
                category="AUTO_GOVERNOR_OPERATIONAL_RECOVERY",
                timestamp=evaluated_ts_ms,
                payload=transition | metadata,
            )
        return changed

    def _active_champion_expectancy_by_family(
        self,
        reports_by_key: Mapping[tuple[str, str], Mapping[str, object]],
    ) -> dict[str, object | None]:
        """같은 family의 ACTIVE만 challenger 비교 기준으로 사용한다."""

        champion_by_family: dict[str, object | None] = {}
        for strategy_id in self.strategy_registry.strategy_ids:
            if (
                self.strategy_registry.setting(strategy_id).lifecycle
                is not StrategyLifecycle.ACTIVE
            ):
                continue
            family_id = self.strategy_registry.descriptor(strategy_id).family_id.value
            if family_id in champion_by_family:
                continue
            report = reports_by_key.get((strategy_id, "BASE"))
            champion_by_family[family_id] = (
                report.get("expectancy_usdt") if report is not None else None
            )
        return champion_by_family

    def run_strategy_governance_cycle(self) -> dict[str, object]:
        """새 자연표본 또는 운영 결함이 있을 때만 보수적 자동 전환을 적용한다."""

        recovery_failed = self.startup_recovery_audit.get("new_state") == "RECOVERY_FAIL_CLOSED"
        if recovery_failed:
            return {
                "evaluated_ts_ms": self.clock.utc_ms(),
                "evaluation_period": "CURRENT_STRATEGY_VERSION_LIVE_PUBLIC",
                "assessments": [],
                "changes": [],
                "blocked_reason": "RECOVERY_FAIL_CLOSED",
                "promotion_without_formal_oos_evidence": False,
                "paper_only": True,
                "real_orders_enabled": False,
                "auth_required": False,
            }
        reports = self.strategy_performance(include_persisted=True)
        reports_by_key = {
            (str(report["strategy_id"]), str(report["profile"])): report for report in reports
        }
        champion_expectancy_by_family = self._active_champion_expectancy_by_family(reports_by_key)
        accounts = self.paper_portfolio.league_account_rows(self.latest_books)
        evaluated_ts_ms = self.clock.utc_ms()
        operational_by_strategy = {
            strategy_id: self._strategy_governance_operational_evidence(
                strategy_id,
                accounts,
                evaluated_ts_ms=evaluated_ts_ms,
            )
            for strategy_id in self.strategy_registry.strategy_ids
        }
        assessments: list[dict[str, object]] = []
        recovery_changes = self._restore_live_operational_quarantine(
            operational_by_strategy,
            evaluated_ts_ms=evaluated_ts_ms,
        )
        changes: list[dict[str, object]] = [dict(row) for row in recovery_changes]
        for strategy_id in self.strategy_registry.strategy_ids:
            base = reports_by_key[(strategy_id, "BASE")]
            stress = reports_by_key[(strategy_id, "STRESS")]
            windows = base.get("windows")
            recent = windows.get("recent_50", {}) if isinstance(windows, Mapping) else {}
            stress_windows = stress.get("windows")
            recent_stress = (
                stress_windows.get("recent_50", {}) if isinstance(stress_windows, Mapping) else {}
            )
            sample_size = min(
                int(str(base["sample_size"])),
                int(str(stress["sample_size"])),
            )
            previous_sample_size = self._governance_last_sample_size.get(strategy_id, -1)
            if sample_size > previous_sample_size:
                full_degraded = (
                    any(
                        report.get("expectancy_usdt") is not None
                        and Decimal(str(report["expectancy_usdt"])) < 0
                        and report.get("profit_factor") is not None
                        and Decimal(str(report["profit_factor"])) < Decimal("0.90")
                        for report in (base, stress)
                    )
                    and sample_size >= 30
                )
                recent_degraded = any(
                    report.get("expectancy_usdt") is not None
                    and Decimal(str(report["expectancy_usdt"])) < 0
                    and report.get("profit_factor") is not None
                    and Decimal(str(report["profit_factor"])) < Decimal("0.90")
                    for report in (recent, recent_stress)
                )
                self._governance_full_degraded_cycles[strategy_id] = (
                    self._governance_full_degraded_cycles.get(strategy_id, 0) + 1
                    if full_degraded
                    else 0
                )
                self._governance_recent_degraded_cycles[strategy_id] = (
                    self._governance_recent_degraded_cycles.get(strategy_id, 0) + 1
                    if recent_degraded
                    else 0
                )
                self._governance_last_sample_size[strategy_id] = sample_size
            evidence = GovernanceEvidence.from_reports(
                base,
                stress,
                multiple_testing={
                    "recent_expectancy_usdt": recent.get("expectancy_usdt"),
                    "recent_profit_factor": recent.get("profit_factor"),
                    "recent_stress_expectancy_usdt": recent_stress.get("expectancy_usdt"),
                    "recent_stress_profit_factor": recent_stress.get("profit_factor"),
                    "live_public_sample_size": sample_size,
                    "unique_opportunity_count": base.get("unique_opportunity_count", 0),
                    "full_oos_degraded_evaluations": (
                        self._governance_full_degraded_cycles.get(strategy_id, 0)
                    ),
                    "recent_oos_degraded_evaluations": (
                        self._governance_recent_degraded_cycles.get(strategy_id, 0)
                    ),
                    "evaluation_period": "CURRENT_STRATEGY_VERSION_LIVE_PUBLIC",
                    "evaluated_ts_ms": evaluated_ts_ms,
                },
                champion_expectancy_usdt=champion_expectancy_by_family.get(
                    self.strategy_registry.descriptor(strategy_id).family_id.value
                ),
                operational=operational_by_strategy[strategy_id],
            )
            assessment = self.strategy_governor.assess(
                self.strategy_registry,
                strategy_id,
                evidence,
                assessment_ts_ms=evaluated_ts_ms,
            )
            assessments.append(assessment.as_dict())
            if assessment.transition_required and assessment.automatic_action_allowed:
                setting = self.strategy_registry.setting(strategy_id)
                changed = self.apply_strategy_governance(
                    strategy_id,
                    evidence,
                    expected_revision=setting.revision,
                    assessment_ts_ms=evaluated_ts_ms,
                )
                changes.extend(changed)
        self._governance_last_cycle_ts_ms = evaluated_ts_ms
        return {
            "evaluated_ts_ms": evaluated_ts_ms,
            "evaluation_period": "CURRENT_STRATEGY_VERSION_LIVE_PUBLIC",
            "assessments": assessments,
            "changes": changes,
            "promotion_without_formal_oos_evidence": False,
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }

    def strategy_governance(
        self,
        *,
        include_persisted: bool = True,
        include_history: bool = True,
    ) -> dict[str, object]:
        """현재 버전 LIVE_PUBLIC 자연표본으로 governor 대기 이유를 계산한다."""

        reports = self.strategy_performance(include_persisted=include_persisted)
        reports_by_key = {
            (str(report["strategy_id"]), str(report["profile"])): report for report in reports
        }
        champion_expectancy_by_family = self._active_champion_expectancy_by_family(reports_by_key)
        accounts = self.paper_portfolio.league_account_rows(self.latest_books)
        rows: list[dict[str, object]] = []
        evaluated_ts_ms = self.clock.utc_ms()
        for strategy_id in self.strategy_registry.strategy_ids:
            base = reports_by_key[(strategy_id, "BASE")]
            stress = reports_by_key[(strategy_id, "STRESS")]
            windows = base.get("windows")
            recent = windows.get("recent_50", {}) if isinstance(windows, Mapping) else {}
            stress_windows = stress.get("windows")
            recent_stress = (
                stress_windows.get("recent_50", {}) if isinstance(stress_windows, Mapping) else {}
            )
            evidence = GovernanceEvidence.from_reports(
                base,
                stress,
                multiple_testing={
                    "recent_expectancy_usdt": recent.get("expectancy_usdt"),
                    "recent_profit_factor": recent.get("profit_factor"),
                    "recent_stress_expectancy_usdt": recent_stress.get("expectancy_usdt"),
                    "recent_stress_profit_factor": recent_stress.get("profit_factor"),
                    "live_public_sample_size": min(
                        int(str(base["sample_size"])),
                        int(str(stress["sample_size"])),
                    ),
                    "unique_opportunity_count": base.get("unique_opportunity_count", 0),
                    "evaluation_period": "CURRENT_STRATEGY_VERSION_LIVE_PUBLIC",
                    "evaluated_ts_ms": evaluated_ts_ms,
                },
                champion_expectancy_usdt=champion_expectancy_by_family.get(
                    self.strategy_registry.descriptor(strategy_id).family_id.value
                ),
                operational=self._strategy_governance_operational_evidence(
                    strategy_id,
                    accounts,
                    evaluated_ts_ms=evaluated_ts_ms,
                ),
            )
            assessment = self.strategy_governor.assess(
                self.strategy_registry,
                strategy_id,
                evidence,
                assessment_ts_ms=evaluated_ts_ms,
            )
            setting = self.strategy_registry.setting(strategy_id)
            required_samples = (
                100
                if setting.lifecycle is StrategyLifecycle.CHALLENGER
                else 30
                if setting.lifecycle in {StrategyLifecycle.RESEARCH, StrategyLifecycle.SHADOW}
                else 0
            )
            required_days = (
                21
                if setting.lifecycle is StrategyLifecycle.CHALLENGER
                else 7
                if setting.lifecycle is StrategyLifecycle.SHADOW
                else 0
            )
            rows.append(
                {
                    **assessment.as_dict(),
                    "last_evaluated_ts_ms": evaluated_ts_ms,
                    "evaluation_period": evidence.evaluation_period,
                    "evidence_status": "NOT_PROVEN",
                    "remaining_live_samples": max(
                        0,
                        required_samples - evidence.live_public_sample_size,
                    ),
                    "remaining_days": max(0.0, required_days - evidence.sample_span_days),
                    "manual_lock": setting.manual_lock,
                    "settings_revision": setting.revision,
                }
            )
        history = (
            {
                strategy_id: list(self._strategy_transition_history(strategy_id)[-20:])
                for strategy_id in self.strategy_registry.strategy_ids
            }
            if include_history
            else {}
        )
        champion_ids_by_family = {
            self.strategy_registry.descriptor(strategy_id).family_id.value: strategy_id
            for strategy_id in self.strategy_registry.strategy_ids
            if self.strategy_registry.setting(strategy_id).lifecycle is StrategyLifecycle.ACTIVE
        }
        champion_id = next(iter(champion_ids_by_family.values()), None)
        return {
            "rows": rows,
            "history": history,
            "champion_id": champion_id,
            "champion_ids_by_family": champion_ids_by_family,
            "strategy_version": STRATEGY_VERSION,
            "analysis_scope": "CURRENT_STRATEGY_VERSION_LIVE_PUBLIC",
            "last_automatic_cycle_ts_ms": self._governance_last_cycle_ts_ms,
            "automatic_cycle_interval_ms": 900_000,
            "profitability_status": "NOT_PROVEN",
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }

    @staticmethod
    def _strategy_setting_state(row: Mapping[str, object]) -> str:
        return "|".join(
            (
                str(row.get("lifecycle", "UNKNOWN")),
                str(row.get("mode", "UNKNOWN")),
                f"LONG={'ON' if bool(row.get('long_enabled')) else 'OFF'}",
                f"SHORT={'ON' if bool(row.get('short_enabled')) else 'OFF'}",
                f"MANUAL_LOCK={'ON' if bool(row.get('manual_lock')) else 'OFF'}",
            )
        )

    def _strategy_transition_payload(
        self,
        row: Mapping[str, object],
        *,
        previous: Mapping[str, object] | None = None,
        strategy_registry: StrategyRegistry | None = None,
    ) -> dict[str, object]:
        """전략 설정 revision을 UI·API·원장이 공유하는 상태 전환 계약으로 만든다."""

        registry = strategy_registry or self.strategy_registry
        strategy_id = str(row["strategy_id"])
        response_revision = int(str(row.get("settings_revision", 0)))
        if previous is None and response_revision > 0:
            previous = next(
                (
                    item
                    for item in registry.revision_history(strategy_id)
                    if int(str(item.get("settings_revision", 0))) == response_revision - 1
                ),
                None,
            )
        changed_by = str(row.get("changed_by", "RECOVERY"))
        actor = "RECOVERY" if changed_by == "MIGRATION" else changed_by
        lifecycle = str(row.get("lifecycle", "UNKNOWN"))
        mode = str(row.get("mode", "UNKNOWN"))
        descriptor = registry.descriptor(strategy_id)
        transition_id = f"strategy-setting-{self.run_id}-{strategy_id}-rev-{response_revision}"
        return {
            **dict(row),
            "transition_id": transition_id,
            "previous_state": (
                self._strategy_setting_state(previous) if previous is not None else "NONE"
            ),
            "new_state": self._strategy_setting_state(row),
            "occurred_ts_ms": int(str(row.get("settings_updated_ts_ms", 0))),
            "cause": str(row.get("change_reason", "STRATEGY_SETTINGS_CHANGE")),
            "cause_code": str(row.get("change_reason", "STRATEGY_SETTINGS_CHANGE")),
            "description_ko": (
                f"{descriptor.display_name_ko} 전략 설정을 {lifecycle}·{mode} 상태로 변경했습니다."
            ),
            "actor": actor,
            "run_id": self.run_id,
            "strategy_id": strategy_id,
            "account_id": None,
            "symbol": None,
            "request_revision": (
                int(str(previous.get("settings_revision", response_revision - 1)))
                if previous is not None
                else 0
            ),
            "response_revision": response_revision,
            "reversible": not registry.is_policy_retired(strategy_id),
        }

    def _strategy_transition_history(
        self,
        strategy_id: str,
    ) -> tuple[dict[str, object], ...]:
        history = self.strategy_registry.revision_history(strategy_id)
        return tuple(
            self._strategy_transition_payload(
                row,
                previous=(history[index - 1] if index > 0 else None),
            )
            for index, row in enumerate(history)
        )

    def _persist_strategy_setting(
        self,
        row: Mapping[str, object],
        *,
        timestamp: int,
        evidence: Mapping[str, object] | None = None,
        strategy_registry: StrategyRegistry | None = None,
    ) -> dict[str, object]:
        transition = self._strategy_transition_payload(
            row,
            strategy_registry=strategy_registry,
        )
        if self.ledger is None or self.mode is RuntimeMode.READY:
            return transition
        payload = {
            "run_id": self.run_id,
            "ts_ms": timestamp,
            **transition,
        }
        if evidence is not None:
            payload["change_evidence"] = dict(evidence)
        self.ledger.record_strategy_setting(payload)
        return payload

    def _record_strategy_incident(
        self,
        *,
        category: str,
        timestamp: int,
        payload: Mapping[str, object],
    ) -> None:
        if self.ledger is None or self.mode is RuntimeMode.READY:
            return
        transition_id = payload.get("transition_id")
        self.ledger.record_incident(
            (
                str(transition_id)
                if transition_id is not None
                else f"{category.lower()}-{self.run_id}-{timestamp}"
            ),
            run_id=self.run_id,
            severity="INFO",
            category=category,
            ts_ms=timestamp,
            payload=payload,
        )

    def live_observation_running(self) -> bool:
        """같은 Run의 검증된 공개시장 supervisor가 이미 진행 중인지 반환한다."""

        return (
            self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            and self.market_data_state is MarketDataState.LIVE
            and self._supervisor is not None
            and self._supervisor.running()
            and "PUBLIC_SUPERVISOR_RUNNING" in self.runtime_health_flags
        )

    def strategy_decisions(self) -> tuple[CandidateDecision, ...]:
        return tuple(
            signal.decision
            for _, signal in sorted(self.strategy_signals.items(), key=lambda item: item[0])
        )

    def strategy_performance(self, *, include_persisted: bool = True) -> list[dict[str, object]]:
        """현재 전략 버전의 독립 League LIVE_PUBLIC 거래만 집계한다."""

        trades: list[dict[str, object]] = []
        prior_version_trades: list[dict[str, object]] = []
        data_state = "READY"
        if self.ledger is not None and include_persisted:
            trades, prior_version_trades = self._current_strategy_version_trades(
                self.ledger.list_shadow_trades()
            )
        elif self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            cache_key = self._dashboard_strategy_cache_key()
            if cache_key == self._dashboard_strategy_performance_cache_key:
                return list(self._dashboard_strategy_performance_cache)
            if self.dashboard_trade_cache_ready:
                trades.extend(self._dashboard_live_shadow_trades())
                prior_version_trades.extend(self._historical_prior_version_shadow_trades)
            else:
                data_state = (
                    "LOADING_HISTORY"
                    if self.dashboard_trade_cache_loading
                    else "HISTORY_UNAVAILABLE"
                )
        else:
            for account in self.paper_portfolio.shadows.values():
                trades.extend(self._paper_trade_row(trade) for trade in account.completed_trades)
        reports = TradeAnalytics().strategy_reports(
            trades,
            strategy_ids=self.strategy_registry.strategy_ids,
        )
        excluded_counts: dict[tuple[str, str], int] = {}
        for trade in prior_version_trades:
            key = (str(trade.get("strategy_id", "")), str(trade.get("profile", "BASE")))
            excluded_counts[key] = excluded_counts.get(key, 0) + 1
        for report in reports:
            report["analysis_scope"] = "CURRENT_STRATEGY_VERSION"
            report["strategy_version"] = STRATEGY_VERSION
            report["data_state"] = data_state
            report["excluded_prior_version_samples"] = excluded_counts.get(
                (str(report["strategy_id"]), str(report["profile"])),
                0,
            )
        if data_state == "READY":
            self._hydrate_strategy_arbitration_evidence(reports)
        if not include_persisted and self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            self._dashboard_strategy_performance_cache_key = cache_key
            self._dashboard_strategy_performance_cache = tuple(reports)
        return reports

    def _dashboard_strategy_cache_key(self) -> tuple[object, ...]:
        """저장 거래가 바뀐 때만 실시간 전략 통계를 다시 계산한다."""

        account_versions = tuple(
            (
                account.account_id,
                len(account.completed_trades),
                account.completed_trades[-1].trade_id if account.completed_trades else None,
            )
            for account in self.paper_portfolio.shadows.values()
        )
        return (
            self.run_id,
            self.dashboard_trade_cache_ready,
            self.dashboard_trade_cache_loading,
            len(self._historical_shadow_trades),
            len(self._historical_prior_version_shadow_trades),
            account_versions,
        )

    def strategy_symbol_performance(
        self,
        *,
        include_persisted: bool = True,
    ) -> list[dict[str, object]]:
        """현재 전략 버전 거래를 전략·프로필·종목별로 분리한다."""

        trades: list[dict[str, object]]
        prior_version_trades: list[dict[str, object]]
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER and not include_persisted:
            if self.dashboard_trade_cache_ready:
                trades = list(self._dashboard_live_shadow_trades())
                prior_version_trades = list(self._historical_prior_version_shadow_trades)
            else:
                trades = []
                prior_version_trades = []
        elif self.ledger is None:
            trades = []
            for account in self.paper_portfolio.shadows.values():
                trades.extend(self._paper_trade_row(trade) for trade in account.completed_trades)
            prior_version_trades = []
        else:
            trades, prior_version_trades = self._current_strategy_version_trades(
                self.ledger.list_shadow_trades()
            )
        rows = TradeAnalytics().strategy_symbol_reports(trades)
        excluded_counts: dict[tuple[str, str, str], int] = {}
        for trade in prior_version_trades:
            key = (
                str(trade.get("strategy_id", "")),
                str(trade.get("profile", "BASE")),
                str(trade.get("symbol", "UNKNOWN")),
            )
            excluded_counts[key] = excluded_counts.get(key, 0) + 1
        for row in rows:
            row["analysis_scope"] = "CURRENT_STRATEGY_VERSION"
            row["strategy_version"] = STRATEGY_VERSION
            row["data_state"] = self._strategy_analytics_data_state()
            row["excluded_prior_version_samples"] = excluded_counts.get(
                (str(row["strategy_id"]), str(row["profile"]), str(row["symbol"])),
                0,
            )
        return rows

    def strategy_analytics_scope(self, *, include_persisted: bool = True) -> dict[str, object]:
        """성과 API가 제외한 과거 버전 표본 수를 투명하게 공개한다."""

        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER and not include_persisted:
            excluded_count = len(self._historical_prior_version_shadow_trades)
        else:
            source = self.ledger.list_shadow_trades() if self.ledger is not None else ()
            _, excluded = self._current_strategy_version_trades(source)
            excluded_count = len(excluded)
        return {
            "analysis_scope": "CURRENT_STRATEGY_VERSION",
            "strategy_version": STRATEGY_VERSION,
            "data_state": self._strategy_analytics_data_state(),
            "excluded_prior_version_samples": excluded_count,
        }

    def _strategy_analytics_data_state(self) -> str:
        """LIVE 통계가 버전이 확정된 원장 캐시를 사용하는지 공개한다."""

        if self.mode is not RuntimeMode.LIVE_SHADOW_PAPER:
            return "READY"
        if self.dashboard_trade_cache_ready:
            return "READY"
        return "LOADING_HISTORY" if self.dashboard_trade_cache_loading else "HISTORY_UNAVAILABLE"

    @staticmethod
    def _current_strategy_version_trades(
        trades: Sequence[Mapping[str, object]],
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        live_public = [dict(trade) for trade in trades if trade.get("sample_type") == "LIVE_PUBLIC"]
        current = [
            trade for trade in live_public if trade.get("strategy_version") == STRATEGY_VERSION
        ]
        prior = [
            trade for trade in live_public if trade.get("strategy_version") != STRATEGY_VERSION
        ]
        return current, prior

    def focus_positions(self) -> list[dict[str, object]]:
        """체결이 확인된 공동계좌와 전략계좌 포지션을 한 계약으로 정규화한다."""

        stage_names = {
            "ENTRY_FILLED": "진입 체결",
            "PROTECTION_ACTIVE": "익절·손절 보호 중",
            "TP1_FILLED": "1차 익절 완료",
            "RUNNER_ACTIVE": "남은 수량 추세 추적",
            "STOP_TIGHTENED": "손절선 조정",
            "EXIT_PENDING": "종료 체결 대기",
            "DATA_LOCKED": "데이터 안전잠금",
            "RECOVERED_REVALIDATING": "재시작 후 공개호가 확인 중",
        }

        def stage_for(managed: Any) -> str:
            pending_exit = getattr(managed, "pending_exit", None)
            if pending_exit is not None:
                return "EXIT_PENDING"
            protected = managed.protected
            plan = managed.plan
            if protected.current_stop != plan.initial_stop:
                return "STOP_TIGHTENED"
            if managed.remaining_quantity < managed.original_quantity:
                return "RUNNER_ACTIVE"
            return "PROTECTION_ACTIVE"

        def health() -> str:
            locked = self.paused or (
                self._supervisor is not None and self._supervisor.telemetry.entry_locked
            )
            return "신규진입 안전잠금" if locked else "정상"

        rows: list[dict[str, object]] = []
        main = self.paper_portfolio.main_position_snapshot(self._current_main_book())
        if main is not None:
            managed = self.paper_portfolio.main.position
            assert managed is not None
            descriptor = self.strategy_registry.descriptor(str(main["strategy"]))
            stage = stage_for(managed)
            summary = self.paper_portfolio.main_summary(self._current_main_book())
            entry_fee = managed.protected.entry_fill.fee_usdt
            realized_exit_fees = sum(
                (leg.fill.fee_usdt for leg in managed.exit_legs), start=Decimal(0)
            )
            remaining_fraction = (
                managed.remaining_quantity / managed.original_quantity
                if managed.original_quantity > 0
                else Decimal(0)
            )
            effective_leverage = (
                Decimal(str(main["notional"])) / Decimal(str(summary["equity"]))
                if Decimal(str(summary["equity"])) > 0
                else Decimal(0)
            )
            rows.append(
                {
                    **main,
                    "focus_key": f"MAIN:{main['trade_id']}",
                    "account_id": "SHARED_PAPER",
                    "profile": "BASE",
                    "strategy_id": str(main["strategy"]),
                    "strategy_display_name_ko": descriptor.display_name_ko,
                    "exit_style": descriptor.exit_style.value,
                    "opened_ts_ms": managed.protected.opened_ts_ms,
                    "current_mark": str(
                        self.latest_books[str(main["symbol"])].bids[0][0]
                        if str(main["symbol"]) in self.latest_books and str(main["side"]) == "LONG"
                        else self.latest_books[str(main["symbol"])].asks[0][0]
                        if str(main["symbol"]) in self.latest_books
                        else main["actual_entry"]
                    ),
                    "stage": stage,
                    "stage_ko": stage_names[stage],
                    "effective_leverage": str(effective_leverage),
                    "selected_leverage": str(main["selected_leverage"]),
                    "margin_usdt": str(main["margin_used_usdt"]),
                    "margin_used_usdt": str(main["margin_used_usdt"]),
                    "original_quantity": str(main["quantity"]),
                    "entry_fee_usdt": str(entry_fee),
                    "realized_exit_fees_usdt": str(realized_exit_fees),
                    "estimated_exit_fee_usdt": str(main["estimated_exit_fee"]),
                    "slippage_usdt": str(main["slippage"]),
                    "gross_pnl_usdt": str(main["gross_pnl"]),
                    "net_pnl_usdt": str(main["net_pnl"]),
                    "return_on_margin_pct": str(
                        Decimal(str(main["net_pnl"]))
                        / max(
                            Decimal(str(main["margin_used_usdt"])),
                            Decimal("0.00000001"),
                        )
                        * Decimal(100)
                    ),
                    "account_starting_equity_usdt": "1000",
                    "account_current_equity_usdt": str(summary["equity"]),
                    "remaining_planned_loss_usdt": str(
                        Decimal(str(main["maximum_planned_loss"])) * remaining_fraction
                    ),
                    "maximum_planned_loss_usdt": str(main["maximum_planned_loss"]),
                    "risk_budget_usdt": str(main["risk_budget"]),
                    "notional_usdt": str(main["notional"]),
                    "signal_ts_ms": int(str(main["signal_time"])),
                    "management_reason_ko": str(main["management_reason"]),
                    "data_health": health(),
                    "recovered": False,
                    "auto_focus_eligible": True,
                    "paper_only": True,
                    "real_orders_enabled": False,
                    "auth_required": False,
                }
            )
        league_accounts = {
            str(row["account_id"]): row
            for row in self.paper_portfolio.league_account_rows(self.latest_books)
        }
        for position in self.paper_portfolio.league_position_rows(self.latest_books):
            account_id = str(position["account_id"])
            account = self.paper_portfolio.shadows[account_id]
            managed = account.positions[str(position["symbol"])]
            plan = managed.plan
            descriptor = self.strategy_registry.descriptor(str(position["strategy_id"]))
            account_row = league_accounts[account_id]
            stage = stage_for(managed)
            entry_fee = managed.protected.entry_fill.fee_usdt
            realized_exit_fees = sum(
                (leg.fill.fee_usdt for leg in managed.exit_legs), start=Decimal(0)
            )
            total_fees = Decimal(str(position["fees"]))
            estimated_exit_fee = max(Decimal(0), total_fees - entry_fee - realized_exit_fees)
            remaining_fraction = (
                managed.remaining_quantity / managed.original_quantity
                if managed.original_quantity > 0
                else Decimal(0)
            )
            margin = Decimal(str(position["margin_used_usdt"]))
            rows.append(
                {
                    **position,
                    "focus_key": f"{position['account_id']}:{position['trade_id']}",
                    "strategy": position["strategy_id"],
                    "strategy_display_name_ko": descriptor.display_name_ko,
                    "venue": plan.venue.value,
                    "planned_entry": position["actual_entry"],
                    "take_profit": position["TP1"],
                    "take_profit_1": position["TP1"],
                    "take_profit_2": position["TP2"],
                    "quantity": position["original_quantity"],
                    "risk_budget": str(plan.risk_budget),
                    "risk_budget_usdt": str(plan.risk_budget),
                    "maximum_planned_loss": str(plan.max_planned_loss),
                    "maximum_planned_loss_usdt": str(plan.max_planned_loss),
                    "remaining_planned_loss_usdt": str(plan.max_planned_loss * remaining_fraction),
                    "margin_usdt": str(margin),
                    "margin_used_usdt": str(margin),
                    "selected_leverage": str(position["selected_leverage"]),
                    "notional_usdt": str(position["notional"]),
                    "signal_ts_ms": int(str(position["signal_time"])),
                    "entry_fee_usdt": str(entry_fee),
                    "realized_exit_fees_usdt": str(realized_exit_fees),
                    "estimated_exit_fee_usdt": str(estimated_exit_fee),
                    "slippage_usdt": str(position["slippage"]),
                    "gross_pnl_usdt": str(position["gross_pnl"]),
                    "net_pnl_usdt": str(position["net_pnl"]),
                    "return_on_margin_pct": str(
                        Decimal(str(position["net_pnl"]))
                        / max(margin, Decimal("0.00000001"))
                        * Decimal(100)
                    ),
                    "account_starting_equity_usdt": str(account_row["starting_equity_usdt"]),
                    "account_current_equity_usdt": str(account_row["current_equity_usdt"]),
                    "management_reason_ko": str(position["management_reason"]),
                    "stage": stage,
                    "stage_ko": stage_names[stage],
                    "data_health": health(),
                    "recovered": False,
                    "auto_focus_eligible": position["profile"] == "BASE",
                    "paper_only": True,
                    "real_orders_enabled": False,
                    "auth_required": False,
                }
            )
        return sorted(
            rows,
            key=lambda row: (
                row["profile"] != "BASE",
                row["account_id"] != "SHARED_PAPER",
                int(str(row.get("opened_ts_ms", row.get("signal_time", 0)))),
                str(row["focus_key"]),
            ),
        )

    def replayable_runs(self) -> list[dict[str, object]]:
        if self.ledger is None:
            return []
        with self._persistence_lock:
            buffered_by_run: dict[str, int] = {}
            for event in self._market_event_buffer:
                run_id = str(event.get("run_id", ""))
                buffered_by_run[run_id] = buffered_by_run.get(run_id, 0) + 1
        use_live_cache = (
            self.mode is RuntimeMode.LIVE_SHADOW_PAPER and self.dashboard_trade_cache_ready
        )
        if use_live_cache:
            with self._dashboard_trade_cache_lock:
                summaries = [dict(row) for row in self._historical_replay_run_summaries]
                persisted_deltas = dict(self._replay_run_persisted_deltas)
        else:
            summaries = self.ledger.list_replayable_run_summaries()
            persisted_deltas = {}
        current_main_count = (
            sum(1 for trade in self._history_main_trades() if trade.get("run_id") == self.run_id)
            if use_live_cache
            else 0
        )
        current_shadow_count = (
            sum(1 for trade in self._history_shadow_trades() if trade.get("run_id") == self.run_id)
            if use_live_cache
            else 0
        )
        rows: list[dict[str, object]] = []
        for run in summaries:
            run_id = str(run["run_id"])
            buffered_count = buffered_by_run.get(run_id, 0)
            persisted_delta = persisted_deltas.get(run_id, 0)
            persisted_count = (
                int(str(run["market_event_count"]))
                if run["market_event_count"] is not None
                else None
            )
            has_events = bool(run["has_market_events"]) or persisted_delta > 0 or buffered_count > 0
            if not has_events:
                continue
            rows.append(
                {
                    "run_id": str(run["run_id"]),
                    "mode": str(run["mode"]),
                    "venue": str(run["venue"]),
                    "started_ts_ms": int(str(run["started_ts_ms"])),
                    "finalized_ts_ms": int(str(run["finalized_ts_ms"]))
                    if run["finalized_ts_ms"] is not None
                    else None,
                    "market_event_count": (
                        persisted_count + persisted_delta + buffered_count
                        if persisted_count is not None
                        else None
                    ),
                    "events_saved": has_events,
                    "trade_count": (
                        current_main_count
                        if use_live_cache and run_id == self.run_id
                        else int(str(run["trade_count"]))
                    ),
                    "shadow_trade_count": (
                        current_shadow_count
                        if use_live_cache and run_id == self.run_id
                        else int(str(run["shadow_trade_count"]))
                    ),
                }
            )
        return rows

    def history_records(
        self,
        *,
        run_scope: str = "CURRENT",
        account_scope: str = "MAIN",
        profile: str = "ALL",
        version_scope: str = "CURRENT",
        sample_type: str = "ALL",
        limit: int = 500,
    ) -> dict[str, object]:
        """main·League 불변 원장을 명시한 범위로 조회해 화면 계약으로 변환한다."""

        valid_values = {
            "run_scope": ({"CURRENT", "ALL"}, run_scope),
            "account_scope": ({"MAIN", "LEAGUE", "ALL"}, account_scope),
            "profile": ({"BASE", "STRESS", "ALL"}, profile),
            "version_scope": ({"CURRENT", "ALL"}, version_scope),
            "sample_type": ({"LIVE_PUBLIC", "OFFLINE_FIXTURE", "ALL"}, sample_type),
        }
        for name, (allowed, value) in valid_values.items():
            if value not in allowed:
                raise ValueError(f"지원하지 않는 거래내역 {name} 값입니다: {value}")
        if not 1 <= limit <= 2_000:
            raise ValueError("거래내역 개수는 1..2000 범위여야 합니다.")

        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER and self.dashboard_trade_cache_ready:
            main_trades = list(self._history_main_trades())
            league_trades = list(self._history_shadow_trades())
        else:
            main_trades = []
            league_trades = []
            if self.ledger is not None:
                main_trades.extend(self.ledger.list_trades())
                league_trades.extend(self.ledger.list_shadow_trades())
            main_trades.extend(
                self._paper_trade_row(trade) for trade in self.paper_portfolio.main.completed_trades
            )
            for account in self.paper_portfolio.shadows.values():
                league_trades.extend(
                    self._paper_trade_row(trade) for trade in account.completed_trades
                )

        replayable_run_ids = {str(row["run_id"]) for row in self.replayable_runs()}
        selected: dict[tuple[str, str, str], dict[str, object]] = {}
        sources: tuple[tuple[str, list[dict[str, object]]], ...] = (
            (("MAIN", main_trades),)
            if account_scope == "MAIN"
            else (("LEAGUE", league_trades),)
            if account_scope == "LEAGUE"
            else (("MAIN", main_trades), ("LEAGUE", league_trades))
        )
        for account_kind, trades in sources:
            for trade in trades:
                normalized = self._history_record_row(
                    trade,
                    account_scope=account_kind,
                    replayable_run_ids=replayable_run_ids,
                )
                if run_scope == "CURRENT" and normalized["run_id"] != self.run_id:
                    continue
                if profile != "ALL" and normalized["profile"] != profile:
                    continue
                if (
                    version_scope == "CURRENT"
                    and normalized["strategy_version"] != STRATEGY_VERSION
                ):
                    continue
                if sample_type != "ALL" and normalized["sample_type"] != sample_type:
                    continue
                key = (
                    account_kind,
                    str(normalized["run_id"]),
                    str(normalized["trade_id"]),
                )
                selected[key] = normalized
        ordered = sorted(
            selected.values(),
            key=lambda row: (
                int(str(row["exit_ts_ms"])),
                str(row["trade_id"]),
            ),
            reverse=True,
        )[:limit]
        return {
            "rows": ordered,
            "scope": {
                "run_scope": run_scope,
                "account_scope": account_scope,
                "profile": profile,
                "version_scope": version_scope,
                "sample_type": sample_type,
                "strategy_version": STRATEGY_VERSION,
                "returned_count": len(ordered),
                "limit": limit,
            },
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }

    @staticmethod
    def _history_record_row(
        trade: Mapping[str, object],
        *,
        account_scope: str,
        replayable_run_ids: set[str],
    ) -> dict[str, object]:
        raw_sample = str(trade.get("sample_type", "LIVE_PUBLIC"))
        normalized_sample = (
            "OFFLINE_FIXTURE" if raw_sample in {"DEMO_FIXTURE", "OFFLINE_FIXTURE"} else raw_sample
        )
        strategy_id = str(trade.get("strategy_id", "UNKNOWN"))
        profile = str(trade.get("profile", "BASE"))
        run_id = str(trade["run_id"])
        trade_id = str(trade.get("trade_id", trade.get("shadow_trade_id", "UNKNOWN")))
        candidate_id = (
            str(trade["candidate_id"]).strip() if trade.get("candidate_id") is not None else ""
        )
        signal_event_id = (
            str(trade["signal_event_id"]).strip()
            if trade.get("signal_event_id") is not None
            else ""
        )
        explicit_opportunity_id = (
            str(trade["opportunity_id"]).strip() if trade.get("opportunity_id") is not None else ""
        )
        if candidate_id.upper() == "UNKNOWN":
            candidate_id = ""
        if signal_event_id.upper() == "UNKNOWN":
            signal_event_id = ""
        if explicit_opportunity_id.upper() == "UNKNOWN":
            explicit_opportunity_id = ""
        opportunity_id = (
            explicit_opportunity_id
            or candidate_id
            or signal_event_id
            or "|".join(
                (
                    run_id,
                    strategy_id,
                    str(trade["symbol"]),
                    str(trade["side"]),
                    str(trade["entry_ts_ms"]),
                )
            )
        )
        return {
            "run_id": run_id,
            "trade_id": trade_id,
            "candidate_id": candidate_id or None,
            "signal_event_id": signal_event_id or None,
            "opportunity_id": opportunity_id,
            "account_scope": account_scope,
            "account_id": (
                "SHARED_PAPER" if account_scope == "MAIN" else f"{strategy_id}:{profile}"
            ),
            "symbol": str(trade["symbol"]),
            "strategy": strategy_id,
            "side": str(trade["side"]),
            "entry": str(trade["entry_price"]),
            "exit": str(trade["exit_price"]),
            "entry_ts_ms": int(str(trade["entry_ts_ms"])),
            "exit_ts_ms": int(str(trade["exit_ts_ms"])),
            "initial_stop": str(trade.get("initial_stop", "—")),
            "take_profit": str(trade.get("take_profit", "—")),
            "take_profit_1": (
                str(trade["take_profit_1"]) if trade.get("take_profit_1") is not None else None
            ),
            "take_profit_2": (
                str(trade["take_profit_2"]) if trade.get("take_profit_2") is not None else None
            ),
            "tp1_hit_ts_ms": _optional_int(trade.get("tp1_hit_ts_ms")),
            "tp2_hit_ts_ms": _optional_int(trade.get("tp2_hit_ts_ms")),
            "time_to_tp1_ms": _optional_int(trade.get("time_to_tp1_ms")),
            "time_to_tp2_ms": _optional_int(trade.get("time_to_tp2_ms")),
            "time_to_stop_ms": _optional_int(trade.get("time_to_stop_ms")),
            "trailing_activation_ts_ms": _optional_int(trade.get("trailing_activation_ts_ms")),
            "runner_started_ts_ms": _optional_int(trade.get("runner_started_ts_ms")),
            "peak_unrealized_usdt": (
                str(trade["peak_unrealized_usdt"])
                if trade.get("peak_unrealized_usdt") is not None
                else None
            ),
            "giveback_usdt": (
                str(trade["giveback_usdt"]) if trade.get("giveback_usdt") is not None else None
            ),
            "runner_net_pnl_usdt": (
                str(trade["runner_net_pnl_usdt"])
                if trade.get("runner_net_pnl_usdt") is not None
                else None
            ),
            "trail_trigger_slippage_usdt": (
                str(trade["trail_trigger_slippage_usdt"])
                if trade.get("trail_trigger_slippage_usdt") is not None
                else None
            ),
            "trailing_state_checksum": (
                str(trade["trailing_state_checksum"])
                if trade.get("trailing_state_checksum") is not None
                else None
            ),
            "selected_margin_leverage": str(trade.get("selected_margin_leverage", "1")),
            "entry_notional_usdt": str(
                trade.get(
                    "entry_notional_usdt",
                    Decimal(str(trade.get("entry_price", "0")))
                    * Decimal(str(trade.get("quantity", "0"))),
                )
            ),
            "margin_used_usdt": str(
                trade.get(
                    "margin_used_usdt",
                    Decimal(str(trade.get("entry_price", "0")))
                    * Decimal(str(trade.get("quantity", "0"))),
                )
            ),
            "quantity": str(trade.get("quantity", "—")),
            "exit_reason": str(trade["exit_reason"]),
            "gross_pnl": str(trade["gross_pnl_usdt"]),
            "fees": str(trade["fees_usdt"]),
            "slippage": str(trade["slippage_usdt"]),
            "net_pnl": str(trade["net_pnl_usdt"]),
            "holding_ms": int(str(trade["holding_ms"])),
            "holding_seconds": int(str(trade["holding_ms"])) // 1_000,
            "mae_r": str(trade["mae_r"]) if trade.get("mae_r") is not None else None,
            "mfe_r": str(trade["mfe_r"]) if trade.get("mfe_r") is not None else None,
            "profile": profile,
            "sample_type": normalized_sample,
            "strategy_version": str(trade.get("strategy_version", "UNKNOWN")),
            "config_hash": str(trade.get("config_hash", "UNKNOWN")),
            "replay_available": run_id in replayable_run_ids,
        }

    def flush_storage(self) -> None:
        """현재 메모리 배치와 PAPER 실행 결과를 불변 원장에 반영한다."""

        self._persist_execution_state_safely(self.clock.utc_ms())
        self._flush_persistence()

    def replay_stored_run(
        self,
        source_run_id: str,
        *,
        symbol: str | None = None,
        event_limit: int | None = None,
    ) -> dict[str, object]:
        if self.ledger is None:
            raise ValueError("영속 원장이 없어 리플레이할 수 없습니다.")
        self.flush_storage()
        from backend.app.replay.market import StoredMarketReplay

        result = StoredMarketReplay().run(
            self.ledger,
            source_run_id=source_run_id,
            created_ts_ms=self.clock.utc_ms(),
            symbol=symbol.strip().upper() if symbol else None,
            event_limit=event_limit,
        )
        return result.as_dict()

    def replay_timeline(
        self,
        source_run_id: str,
        *,
        symbol: str | None = None,
        limit: int = 2_000,
    ) -> dict[str, object]:
        """저장된 공개시장 이벤트와 실제 집계 캔들을 UI 재생 프레임으로 제공한다."""

        if self.ledger is None:
            raise ValueError("영속 원장이 없어 리플레이할 수 없습니다.")
        from backend.app.replay.timeline import build_replay_timeline

        return build_replay_timeline(
            self.ledger,
            source_run_id,
            symbol=symbol,
            limit=limit,
        )

    def replay_preview(
        self,
        source_run_id: str,
        *,
        symbol: str | None = None,
        candle_limit: int = 500,
    ) -> dict[str, object]:
        """대용량 이벤트 본문을 읽지 않고 저장 종목과 최근 캔들을 미리 보여 준다."""

        if self.ledger is None:
            raise ValueError("영속 원장이 없어 리플레이할 수 없습니다.")
        from backend.app.replay.timeline import build_replay_preview

        return build_replay_preview(
            self.ledger,
            source_run_id,
            symbol=symbol,
            candle_limit=candle_limit,
        )

    def replay_focus_session(
        self,
        source_run_id: str,
        *,
        trade_id: str,
        profile: str = "BASE",
        persist_cache: bool = True,
    ) -> dict[str, object]:
        """저장된 실제 PAPER 거래의 포지션 집중 리플레이를 생성한다."""

        if self.ledger is None:
            raise ValueError("영속 원장이 없어 리플레이할 수 없습니다.")
        from backend.app.replay.focus import ReplayFocusSessionBuilder

        return ReplayFocusSessionBuilder().build(
            self.ledger,
            run_id=source_run_id,
            trade_id=trade_id,
            profile=profile,
            created_ts_ms=self.clock.utc_ms(),
            persist_cache=persist_cache,
        )

    def candles(
        self,
        symbol: str | None = None,
        interval_seconds: int | None = None,
    ) -> tuple[Candle, ...]:
        return self.candle_builder.series(
            symbol or self.selected_symbol,
            interval_seconds or self.selected_interval_seconds,
        )

    def set_chart_selection(self, symbol: str, interval_seconds: int) -> None:
        normalized = symbol.strip().upper()
        TIMEFRAME_REGISTRY.validate_builder(interval_seconds)
        self.selected_symbol = normalized
        self.selected_interval_seconds = interval_seconds

    def set_strategy_public_history(
        self,
        symbol: str,
        interval_seconds: int,
        rows: Sequence[Mapping[str, object]],
        *,
        now_ms: int,
    ) -> int:
        """인증 없는 공개 완성봉만 SHADOW 추세 워밍업에 보관한다."""

        normalized = symbol.strip().upper()
        if interval_seconds not in {900, 1_800, 3_600}:
            raise ValueError("전략 워밍업은 15분·30분·1시간 완성봉만 지원합니다.")
        candles: dict[int, Candle] = {}
        for row in rows:
            open_ts_ms = int(str(row["open_ts_ms"]))
            if open_ts_ms + interval_seconds * 1_000 > now_ms:
                continue
            candle = Candle(
                symbol=normalized,
                interval_seconds=interval_seconds,
                open_ts_ms=open_ts_ms,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                trade_count=int(str(row.get("trade_count", 0))),
            )
            if not (
                candle.open > 0
                and candle.low > 0
                and candle.high >= max(candle.open, candle.close)
                and candle.low <= min(candle.open, candle.close)
                and candle.volume >= 0
            ):
                raise ValueError("공개 전략 완성봉 가격·거래량이 올바르지 않습니다.")
            candles[open_ts_ms] = candle
        ordered = tuple(candles[key] for key in sorted(candles))[-500:]
        self.strategy_public_history[(normalized, interval_seconds)] = ordered
        if interval_seconds == 3_600:
            self.hourly_public_history[normalized] = ordered
        self._strategy_candle_cache.pop((normalized, interval_seconds), None)
        return len(ordered)

    def set_hourly_public_history(
        self,
        symbol: str,
        rows: Sequence[Mapping[str, object]],
        *,
        now_ms: int,
    ) -> int:
        """기존 1시간 전략 워밍업 호출을 일반 완성봉 계약으로 연결한다."""

        return self.set_strategy_public_history(
            symbol,
            3_600,
            rows,
            now_ms=now_ms,
        )

    def strategy_completed_candles(
        self,
        symbol: str,
        interval_seconds: int,
    ) -> tuple[Candle, ...]:
        normalized = symbol.strip().upper()
        public_rows = self.strategy_public_history.get((normalized, interval_seconds), ())
        if interval_seconds == 3_600 and not public_rows:
            public_rows = self.hourly_public_history.get(normalized, ())
        local_rows = self.candle_builder.completed_series(normalized, interval_seconds)
        signature = (
            len(public_rows),
            public_rows[-1].open_ts_ms if public_rows else None,
            len(local_rows),
            local_rows[-1].open_ts_ms if local_rows else None,
        )
        cache_key = (normalized, interval_seconds)
        cached = self._strategy_candle_cache.get(cache_key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        merged = {candle.open_ts_ms: candle for candle in public_rows}
        merged.update({candle.open_ts_ms: candle for candle in local_rows})
        ordered = tuple(merged[key] for key in sorted(merged))[-500:]
        self._strategy_candle_cache[cache_key] = (signature, ordered)
        return ordered

    def hourly_completed_candles(self, symbol: str) -> tuple[Candle, ...]:
        return self.strategy_completed_candles(symbol, 3_600)

    async def shutdown_supervisor(self) -> None:
        supervisor = self._supervisor
        self._supervisor = None
        if supervisor is not None:
            await supervisor.stop()

    async def shutdown(self) -> None:
        await self.shutdown_supervisor()
        self.flush_storage()
        await self._live_strategy_evaluator.aclose()

    def _live_scanner_rows(self) -> tuple[dict[str, object], ...]:
        """정밀 분석 종목의 실제 전략 판단과 비용을 확률 없이 UI 행으로 만든다."""

        if self.mode is not RuntimeMode.LIVE_SHADOW_PAPER:
            return ()
        deep_symbols = self.live_selection.deep_symbols if self.live_selection is not None else ()
        unsorted: list[dict[str, object]] = []
        for symbol in deep_symbols:
            feature = self.latest_features.get(symbol)
            regime = self.latest_regimes.get(symbol)
            signals = [
                signal for signal in self.strategy_signals.values() if signal.symbol == symbol
            ]
            if feature is None or regime is None or not signals:
                unsorted.append(
                    {
                        "rank": 0,
                        "symbol": symbol,
                        "depth": "DEEP",
                        "regime": "WARMUP",
                        "strategy": "분석 준비",
                        "side": "NONE",
                        "score": None,
                        "net_rr": None,
                        "expected_cost_bps": 0.0,
                        "spread_bps": round(feature.spread_bps, 4) if feature else 0.0,
                        "data_health": "HEALTHY" if feature and feature.data_healthy else "WARMUP",
                        "status": "CALIBRATING",
                        "reason": "실제 정밀 호가·체결 이력을 축적하는 중",
                        "reason_codes": ["CALIBRATING"],
                        "calibration": "CALIBRATING",
                    }
                )
                continue
            selected = min(
                signals,
                key=lambda signal: (
                    signal.decision.status.value != "QUALIFIED",
                    -float(signal.decision.net_reward_risk or Decimal(0)),
                    float(signal.decision.expected_cost_bps),
                    signal.decision.strategy_id,
                    signal.decision.side.value,
                ),
            )
            decision = selected.decision
            reason_codes = list(decision.reason_codes or decision.rejection_codes)
            unsorted.append(
                {
                    "rank": 0,
                    "symbol": symbol,
                    "depth": "DEEP",
                    "regime": regime.value,
                    "strategy": self.strategy_registry.descriptor(decision.strategy_id).short_name,
                    "side": decision.side.value,
                    "score": None,
                    "net_rr": float(decision.net_reward_risk)
                    if decision.net_reward_risk is not None
                    else None,
                    "expected_cost_bps": float(decision.expected_cost_bps),
                    "spread_bps": round(feature.spread_bps, 4),
                    "data_health": "HEALTHY" if feature.data_healthy else "STALE",
                    "status": decision.status.value,
                    "reason": " · ".join(reason_codes)
                    if reason_codes
                    else "구조·체결흐름 조건 확인 중",
                    "reason_codes": reason_codes,
                    "calibration": decision.calibration_status,
                }
            )
        ordered = sorted(
            unsorted,
            key=lambda row: (
                row["status"] != "QUALIFIED",
                -(float(str(row["net_rr"])) if row["net_rr"] is not None else -1.0),
                float(str(row["expected_cost_bps"])),
                str(row["symbol"]),
            ),
        )
        return tuple({**row, "rank": rank} for rank, row in enumerate(ordered, 1))

    def dashboard(self) -> dict[str, object]:
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER and self._supervisor is not None:
            self._refresh_supervisor_entry_safety()
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            persisted_trades = tuple(
                self._paper_trade_row(trade) for trade in self.paper_portfolio.main.completed_trades
            )
        else:
            persisted_trades = (
                tuple(self.ledger.list_trades(self.run_id))
                if self.ledger is not None and self.mode is not RuntimeMode.READY
                else ()
            )
        sample_type = (
            "DEMO_FIXTURE"
            if self.mode is RuntimeMode.DEMO_FIXTURE
            else "LIVE_PUBLIC"
            if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            else None
        )
        if self.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            history_trades = self._dashboard_live_main_trades()
        else:
            history_trades = (
                tuple(
                    trade
                    for trade in self.ledger.list_trades()
                    if trade.get("sample_type", "LIVE_PUBLIC") == sample_type
                )
                if self.ledger is not None and sample_type is not None
                else ()
            )
        candle_rows = tuple(
            {
                "time": candle.open_ts_ms // 1_000,
                "open_ts_ms": candle.open_ts_ms,
                "open": float(candle.open),
                "high": float(candle.high),
                "low": float(candle.low),
                "close": float(candle.close),
                "volume": float(candle.volume),
                "trade_count": candle.trade_count,
                "quote_volume": str(candle.quote_volume),
                "taker_buy_volume": str(candle.taker_buy_volume),
                "taker_sell_volume": str(candle.taker_sell_volume),
                "taker_buy_quote_volume": str(candle.taker_buy_quote_volume),
                "taker_sell_quote_volume": str(candle.taker_sell_quote_volume),
            }
            for candle in self.candles()
        )
        diagnostics: dict[str, object] = (
            self._supervisor.telemetry.as_dict()
            if self._supervisor is not None
            else {
                "connection_state": self.market_data_state.value,
                "event_count": len(self._events),
                "reconnects": 0,
                "sequence_gaps": 0,
                "resyncs": 0,
                "dropped_events": 0,
                "queue_depth": 0,
                "queue_capacity": 0,
                "entry_locked": self.paused,
            }
        )
        if self._supervisor is not None:
            diagnostics["supervisor_running"] = self._supervisor.running()
        diagnostics.update(self._operational_diagnostics())
        deep_symbols = self.live_selection.deep_symbols if self.live_selection is not None else ()
        hourly_counts = {
            symbol: len(self.hourly_completed_candles(symbol)) for symbol in deep_symbols
        }
        diagnostics["hourly_strategy_history"] = {
            "required_completed_candles": 200,
            "ready_symbols": sum(count >= 200 for count in hourly_counts.values()),
            "total_symbols": len(hourly_counts),
            "completed_candles_by_symbol": hourly_counts,
            "source": "PUBLIC_BINANCE_USDM_COMPLETED_1H",
        }
        current_position = self.paper_portfolio.main_position_snapshot(self._current_main_book())
        strategy_rows: list[dict[str, object]] = []
        performance_by_key = {
            (str(report["strategy_id"]), str(report["profile"])): report
            for report in self.strategy_performance(include_persisted=False)
        }
        governance_payload = self.strategy_governance(
            include_persisted=False,
            include_history=False,
        )
        governance_rows = governance_payload["rows"]
        if not isinstance(governance_rows, list):
            raise RuntimeError("governor 화면 계약이 list가 아닙니다.")
        governance_by_id = {
            str(row["strategy_id"]): row for row in governance_rows if isinstance(row, Mapping)
        }
        for row in self.strategy_registry.rows():
            strategy_id = str(row["strategy_id"])
            signals = [
                signal
                for signal in self.strategy_signals.values()
                if signal.decision.strategy_id == strategy_id
            ]
            latest = max(signals, key=lambda item: item.decision.expected_cost_bps, default=None)
            strategy_rows.append(
                {
                    **row,
                    "evaluated_paths": len(signals),
                    "qualified_paths": sum(
                        signal.decision.status.value == "QUALIFIED" for signal in signals
                    ),
                    "latest_status": latest.decision.status.value if latest else "WAITING_DATA",
                    "latest_reasons": list(
                        latest.decision.reason_codes or latest.decision.rejection_codes
                    )
                    if latest
                    else [],
                    "performance": {
                        profile: _dashboard_performance_report(
                            performance_by_key[(strategy_id, profile)]
                        )
                        for profile in ("BASE", "STRESS")
                    },
                    "governance": dict(governance_by_id[strategy_id])
                    | {
                        "change_history": list(self._strategy_transition_history(strategy_id)[-20:])
                    },
                }
            )
        dashboard_events = (
            tuple(reversed(tuple(islice(reversed(self._events), _LIVE_DASHBOARD_EVENT_LIMIT))))
            if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            else self.events
        )
        snapshot = build_dashboard_snapshot(
            self.status(),
            dashboard_events,
            paused=self.paused,
            position_visible=self.position_visible,
            control_logs=tuple(self.control_logs),
            archived_run_ids=tuple(self.archived_run_ids),
            persisted_trades=persisted_trades,
            history_trades=history_trades,
            candle_rows=candle_rows,
            chart_symbol=self.selected_symbol,
            chart_interval_seconds=self.selected_interval_seconds,
            runtime_diagnostics=diagnostics,
            scanner_rows=self._live_scanner_rows()
            if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            else None,
            strategies=tuple(strategy_rows),
            shadow_accounts=tuple(self.paper_portfolio.shadow_rows()),
            league_accounts=tuple(self.paper_portfolio.league_account_rows(self.latest_books)),
            league_positions=tuple(self.paper_portfolio.league_position_rows(self.latest_books)),
            risk_contract=self._risk_dashboard_contract(),
            current_position=current_position,
            execution_audit=tuple(self.paper_portfolio.audit_events[-100:]),
            storage_label="SQLite transactional ledger"
            if self.ledger is not None
            else "fixture memory",
            api_host=(
                f"{os.environ.get('ROBOM_HOST', '127.0.0.1')}:"
                f"{os.environ.get('ROBOM_PORT', '8765')}"
            ),
        )
        snapshot["focus_positions"] = self.focus_positions()
        main_pending_entry_count = len(self.paper_portfolio.main.pending_entries)
        league_pending_entry_count = sum(
            len(account.pending_entries) for account in self.paper_portfolio.shadows.values()
        )
        total_open_position_count = len(self.paper_portfolio.main.positions) + sum(
            len(account.positions) for account in self.paper_portfolio.shadows.values()
        )
        total_pending_entry_count = main_pending_entry_count + league_pending_entry_count
        snapshot["main_pending_entry_count"] = main_pending_entry_count
        snapshot["league_pending_entry_count"] = league_pending_entry_count
        snapshot["total_pending_entry_count"] = total_pending_entry_count
        snapshot["total_open_position_count"] = total_open_position_count
        snapshot["paper_portfolio_flat"] = (
            total_open_position_count == 0 and total_pending_entry_count == 0
        )
        snapshot["paper_entry_intent"] = self.paper_entry_intent()
        snapshot["paper_research_configuration"] = self.paper_research_configuration()
        snapshot["orderflow_confirmation_filter"] = self.orderflow_confirmation_filter_status(
            symbol=self.selected_symbol
        )
        snapshot["history_scope"] = {
            "analysis_scope": "CURRENT_STRATEGY_VERSION",
            "strategy_version": STRATEGY_VERSION,
            "excluded_prior_version_samples": len(self._historical_prior_version_live_trades),
        }
        return snapshot

    def paper_research_configuration(self) -> dict[str, object]:
        """현재와 다음 PAPER 진입에 적용되는 연속 연구·배수 계약을 반환한다."""

        return {
            "selected_leverage": int(self._selected_margin_leverage),
            "allowed_leverages": list(_PAPER_LEVERAGE_CHOICES),
            "default_leverage": int(_DEFAULT_PAPER_LEVERAGE),
            "maximum_available_leverage": max(_PAPER_LEVERAGE_CHOICES),
            "continuous_entry_mode": True,
            "daily_trade_limit_enabled": False,
            "daily_loss_lock_enabled": False,
            "weekly_loss_lock_enabled": False,
            "loss_cooldown_enabled": False,
            "risk_sized_quantity": True,
            "dollar_risk_preserved": True,
            "fees_on_actual_notional": True,
            "margin_formula_ko": "실제 명목금액 ÷ 선택 레버리지",
            "applies_to_new_entries": True,
            "revision": self._paper_research_configuration_revision,
            "updated_ts_ms": self._paper_research_configuration_updated_ts_ms,
            "paper_only": True,
            "real_orders_enabled": False,
        }

    def configure_paper_research(
        self,
        *,
        selected_leverage: int,
        expected_revision: int,
        actor: str = "USER_UI",
        reason: str = "USER_PAPER_LEVERAGE_CONFIGURATION",
    ) -> dict[str, object]:
        """선택 배수를 새 진입에 원자적으로 적용하고 기존 거래 기록은 보존한다."""

        if isinstance(selected_leverage, bool) or selected_leverage not in _PAPER_LEVERAGE_CHOICES:
            raise ValueError("PAPER 레버리지는 화면에 제시된 1배부터 100배 중에서 선택하세요.")
        with self._paper_research_configuration_lock:
            if expected_revision != self._paper_research_configuration_revision:
                raise PaperResearchConfigurationConflict(
                    expected_revision=expected_revision,
                    current_revision=self._paper_research_configuration_revision,
                )
            if Decimal(selected_leverage) == self._selected_margin_leverage:
                return self.paper_research_configuration()
            previous_leverage = self._selected_margin_leverage
            timestamp = self.clock.utc_ms()
            self._selected_margin_leverage = Decimal(selected_leverage)
            self._paper_research_configuration_revision += 1
            self._paper_research_configuration_updated_ts_ms = timestamp
            self.paper_portfolio.risk_manager = RiskManager(self._continuous_limits(RiskLimits()))
            self.paper_portfolio.league_risk_manager = RiskManager(
                self._continuous_limits(STRATEGY_LEAGUE_RISK_LIMITS)
            )
            self.paper_portfolio.selected_margin_leverage = self._selected_margin_leverage
            for account in self.paper_portfolio.accounts:
                account.risk_state.cooldowns_until_ms.clear()
            if self.ledger is not None:
                self.ledger.set_app_setting(
                    _PAPER_RESEARCH_SETTING_KEY,
                    {
                        "selected_leverage": selected_leverage,
                        "revision": self._paper_research_configuration_revision,
                        "continuous_entry_mode": True,
                        "paper_only": True,
                    },
                    updated_ts_ms=timestamp,
                )
                if self.mode is not RuntimeMode.READY:
                    self.ledger.record_incident(
                        f"paper-research-config-{uuid4().hex}",
                        run_id=self.run_id,
                        severity="INFO",
                        category="PAPER_RESEARCH_CONFIGURATION",
                        ts_ms=timestamp,
                        payload={
                            "previous_leverage": str(previous_leverage),
                            "selected_leverage": selected_leverage,
                            "revision": self._paper_research_configuration_revision,
                            "actor": actor,
                            "reason": reason,
                            "applies_to_new_entries": True,
                            "open_positions_preserve_original_leverage": True,
                            "daily_weekly_and_cooldown_entry_locks_disabled": True,
                            "fees_on_actual_notional": True,
                            "real_orders_enabled": False,
                        },
                    )
            self._log(
                "RISK",
                f"PAPER 선택 레버리지 {selected_leverage}배 · 연속 연구 진입 유지",
            )
            return self.paper_research_configuration()

    def paper_entry_intent(self) -> dict[str, object]:
        """사용자 진입 의도를 자동 안전잠금과 분리한 공개 상태로 반환한다."""

        return {
            "state": "USER_PAUSED" if self._manual_pause_requested else "ENTRY_ENABLED",
            "manual_pause_requested": self._manual_pause_requested,
            "revision": self._paper_entry_intent_revision,
            "actor": self._paper_entry_intent_actor,
            "reason": self._paper_entry_intent_reason,
            "updated_ts_ms": self._paper_entry_intent_updated_ts_ms,
            "reversible": True,
        }

    def _persist_paper_entry_intent(self, *, updated_ts_ms: int) -> None:
        if self.ledger is None or self.mode is RuntimeMode.READY:
            return
        self.ledger.set_app_setting(
            "paper_entry_user_intent",
            {
                "run_id": self.run_id,
                "manual_pause_requested": self._manual_pause_requested,
                "revision": self._paper_entry_intent_revision,
                "actor": self._paper_entry_intent_actor,
                "reason": self._paper_entry_intent_reason,
                "idempotency_records": [
                    {"key": key, "paused": target}
                    for key, target in list(self._paper_entry_intent_idempotency.items())[-32:]
                ],
            },
            updated_ts_ms=updated_ts_ms,
        )

    def orderflow_confirmation_filter_status(
        self,
        *,
        symbol: str | None = None,
    ) -> dict[str, object]:
        return self.orderflow_confirmation_runtime.status(symbol=symbol)

    def strategy_condition_detail(
        self,
        strategy_id: str,
        *,
        symbol: str | None = None,
    ) -> dict[str, object]:
        """현재 evaluator가 실제 사용한 방향별 조건과 실행계획 값을 반환한다."""

        resolved_symbol = (symbol or self.selected_symbol).strip().upper()
        descriptor = self.strategy_registry.descriptor(strategy_id)
        condition_evaluator = (
            self._live_strategy_evaluator
            if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            else self.strategy_evaluator
        )
        side_payloads: list[dict[str, object]] = []
        for side in Side:
            condition_rows = list(
                self._live_strategy_evaluator.condition_rows(
                    resolved_symbol,
                    strategy_id,
                    side,
                    state_key=self._strategy_process_state_key(),
                )
                if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
                else condition_evaluator.condition_rows(
                    resolved_symbol,
                    strategy_id,
                    side,
                )
            )
            signal = self.strategy_signals.get((resolved_symbol, strategy_id, side.value))
            if not condition_rows and signal is None:
                continue
            passed_count = sum(row.get("status") == "PASSED" for row in condition_rows)
            blocked_rows = [
                row for row in condition_rows if row.get("status") in {"BLOCKED", "WAITING_DATA"}
            ]
            decision = signal.decision if signal is not None else None
            side_payloads.append(
                {
                    "side": side.value,
                    "setup_state": (
                        decision.status.value if decision is not None else "WAITING_DATA"
                    ),
                    "passed": passed_count,
                    "total": len(condition_rows),
                    "top_blockers": [
                        str(row.get("label_ko", "조건 실측 대기")) for row in blocked_rows[:3]
                    ],
                    "conditions": condition_rows,
                    "execution": self._decision_execution_detail(
                        descriptor,
                        decision,
                    ),
                }
            )
        side_payloads.sort(
            key=lambda row: (
                str(row["setup_state"]) != "QUALIFIED",
                -int(str(row["passed"])),
                str(row["side"]) != Side.LONG.value,
            )
        )
        selected = side_payloads[0] if side_payloads else None
        open_positions = [
            row
            for row in self.focus_positions()
            if row.get("strategy_id") == strategy_id and row.get("symbol") == resolved_symbol
        ]
        pending_count = sum(
            pending.plan.strategy_id == strategy_id and pending.plan.symbol == resolved_symbol
            for account in self.paper_portfolio.accounts
            for pending in account.pending_entries.values()
        )
        setting = self.strategy_registry.setting(strategy_id)
        setup_state = (
            "OPEN"
            if open_positions
            else "PENDING"
            if pending_count
            else "RESEARCH_OFF"
            if setting.mode is StrategyMode.OFF
            else str(selected["setup_state"])
            if selected is not None
            else "WAITING_DATA"
        )
        waiting_conditions = [
            {
                "condition_id": f"ENTRY_{index}",
                "label_ko": rule,
                "threshold_ko": rule,
                "current_value": None,
                "status": "WAITING_DATA",
                "reason_ko": "현재 종목의 첫 evaluator 실측을 기다리고 있습니다.",
            }
            for index, rule in enumerate(descriptor.entry_rules_ko, start=1)
        ]
        execution = (
            dict(selected["execution"])
            if selected is not None and isinstance(selected["execution"], Mapping)
            else {}
        )
        if open_positions:
            position = open_positions[0]
            execution.update(
                {
                    "entry": position.get("actual_entry", position.get("planned_entry")),
                    "initial_stop": position.get("initial_stop"),
                    "take_profit_1": position.get("TP1", position.get("take_profit_1")),
                    "take_profit_2": position.get("TP2", position.get("take_profit_2")),
                    "current_trail": position.get("current_stop"),
                    "remaining_quantity": position.get("remaining_quantity"),
                }
            )
        return {
            "schema_version": 1,
            "strategy_id": strategy_id,
            "symbol": resolved_symbol,
            "setup_state": setup_state,
            "passed": selected["passed"] if selected is not None else 0,
            "total": (selected["total"] if selected is not None else len(waiting_conditions)),
            "top_blockers": selected["top_blockers"]
            if selected is not None
            else ["현재 종목의 첫 evaluator 실측을 기다리고 있습니다."],
            "conditions": (selected["conditions"] if selected is not None else waiting_conditions),
            "sides": side_payloads,
            "execution": execution,
            "pending_count": pending_count,
            "open_count": len(open_positions),
            "open_positions": open_positions,
            "research_source_ids": list(descriptor.research_contract.research_source_ids),
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }

    @staticmethod
    def _decision_execution_detail(
        descriptor: StrategyDescriptor,
        decision: CandidateDecision | None,
    ) -> dict[str, object]:
        if decision is None:
            return {}
        entry = decision.planned_entry
        stop = decision.initial_stop
        structural_exit = decision.structural_exit
        if structural_exit is not None:
            runner_explanation = {
                RunnerManagement.FIXED_SECOND_TARGET: (
                    "TP1 부분익절 뒤 계획된 두 번째 구조 가격까지 관리"
                ),
                RunnerManagement.TP1_ATR_CHANDELIER: (
                    "TP1 부분익절 뒤 완성봉 ATR 추적선으로 잔량 보호"
                ),
                RunnerManagement.TP1_STRUCTURE_DISTANCE: (
                    "TP1 부분익절 뒤 확인된 가격 구조폭으로 잔량 보호"
                ),
            }[structural_exit.runner_management]
            return {
                "side": decision.side.value,
                "entry": str(entry) if entry is not None else None,
                "initial_stop": str(stop) if stop is not None else None,
                "take_profit_1": str(structural_exit.take_profit_1),
                "take_profit_2": str(structural_exit.take_profit_2),
                "trailing_activation": runner_explanation,
                "current_trail": None,
                "remaining_quantity": None,
                "stop_rationale_ko": structural_exit.stop_rationale_ko,
                "take_profit_1_rationale_ko": (structural_exit.take_profit_1_rationale_ko),
                "take_profit_2_rationale_ko": (structural_exit.take_profit_2_rationale_ko),
                "reference_timeframes_ko": list(structural_exit.reference_timeframes_ko),
                "runner_management_ko": runner_explanation,
                "expected_cost_bps": str(decision.expected_cost_bps),
                "net_reward_risk": (
                    str(decision.net_reward_risk) if decision.net_reward_risk is not None else None
                ),
            }
        if "STRUCTUR" in descriptor.exit_model:
            return {
                "side": decision.side.value,
                "entry": str(entry) if entry is not None else None,
                "initial_stop": str(stop) if stop is not None else None,
                "take_profit_1": None,
                "take_profit_2": (
                    str(decision.take_profit) if decision.take_profit is not None else None
                ),
                "trailing_activation": "구조 가격 확정 전",
                "current_trail": None,
                "remaining_quantity": None,
                "expected_cost_bps": str(decision.expected_cost_bps),
                "net_reward_risk": (
                    str(decision.net_reward_risk) if decision.net_reward_risk is not None else None
                ),
            }
        direction = Decimal(1) if decision.side is Side.LONG else Decimal(-1)
        risk = abs(entry - stop) if entry is not None and stop is not None else None
        take_profit_1_r = descriptor.take_profit_1_r
        take_profit_2_r = descriptor.take_profit_2_r
        return {
            "side": decision.side.value,
            "entry": str(entry) if entry is not None else None,
            "initial_stop": str(stop) if stop is not None else None,
            "take_profit_1": (
                str(entry + direction * risk * take_profit_1_r)
                if entry is not None and risk is not None
                else None
            ),
            "take_profit_2": (
                str(entry + direction * risk * take_profit_2_r)
                if entry is not None and risk is not None
                else str(decision.take_profit)
                if decision.take_profit is not None
                else None
            ),
            "trailing_activation": "TP1 이후 비용 보전 방향으로만 조정",
            "current_trail": None,
            "remaining_quantity": None,
            "expected_cost_bps": str(decision.expected_cost_bps),
            "net_reward_risk": (
                str(decision.net_reward_risk) if decision.net_reward_risk is not None else None
            ),
        }

    def orderflow_confirmation_condition_detail(
        self,
        *,
        symbol: str | None = None,
    ) -> dict[str, object]:
        """주문흐름 필터의 구성요소·점수·지속시간을 방향별로 공개한다."""

        resolved_symbol = (symbol or self.selected_symbol).strip().upper()
        status = self.orderflow_confirmation_filter_status(symbol=resolved_symbol)
        latest_rows = status.get("latest")
        latest = (
            [row for row in latest_rows if isinstance(row, Mapping)]
            if isinstance(latest_rows, list)
            else []
        )
        side_payloads: list[dict[str, object]] = []
        component_labels = {
            "normalized_ofi": "정규화 OFI",
            "aggressor_imbalance": "공격 체결 불균형",
            "microprice_displacement": "microprice 변위",
            "multilevel_fair_price_displacement": "top10 공정가격 변위",
            "queue_imbalance": "큐 불균형",
            "book_slope": "호가 기울기",
            "depth_adjusted_price_response": "깊이보정 가격반응",
            "spread_health": "스프레드 건전성",
            "book_resilience": "호가 복원력",
        }
        for row in latest:
            side = str(row.get("side", "UNKNOWN"))
            components = row.get("components")
            component_rows = (
                [
                    {
                        "condition_id": f"COMPONENT_{name.upper()}",
                        "label_ko": component_labels.get(str(name), str(name)),
                        "threshold_ko": (f"{status['component_pass_threshold']} 이상"),
                        "current_value": value,
                        "status": (
                            "PASSED"
                            if Decimal(str(value))
                            >= Decimal(str(status["component_pass_threshold"]))
                            else "BLOCKED"
                        ),
                        "reason_ko": "필터 구성요소의 방향별 정규화 값입니다.",
                        "side": side,
                    }
                    for name, value in components.items()
                ]
                if isinstance(components, Mapping)
                else []
            )
            condition_rows = [
                {
                    "condition_id": "DATA_HEALTH",
                    "label_ko": "공개시장 데이터 상태",
                    "threshold_ko": "HEALTHY",
                    "current_value": row.get("data_health"),
                    "status": ("PASSED" if row.get("data_health") == "HEALTHY" else "BLOCKED"),
                    "reason_ko": "sequence·stale 상태를 함께 반영합니다.",
                    "side": side,
                },
                *component_rows,
                {
                    "condition_id": "WEIGHTED_SCORE",
                    "label_ko": "가중 confirmation score",
                    "threshold_ko": f"{status['threshold']} 이상",
                    "current_value": row.get("score"),
                    "status": (
                        "PASSED"
                        if Decimal(str(row.get("score", 0))) >= Decimal(str(status["threshold"]))
                        else "BLOCKED"
                    ),
                    "reason_ko": "사전등록된 9개 가중치를 사용합니다.",
                    "side": side,
                },
                {
                    "condition_id": "INDEPENDENT_COMPONENTS",
                    "label_ko": "독립 구성요소 통과 수",
                    "threshold_ko": f"{status['minimum_passed_components']}개 이상",
                    "current_value": row.get("passed_component_count"),
                    "status": (
                        "PASSED"
                        if int(str(row.get("passed_component_count", 0)))
                        >= int(str(status["minimum_passed_components"]))
                        else "BLOCKED"
                    ),
                    "reason_ko": "한 요소만 강한 경우를 차단합니다.",
                    "side": side,
                },
                {
                    "condition_id": "PERSISTENCE",
                    "label_ko": "confirmation 지속시간",
                    "threshold_ko": f"{status['minimum_persistence_ms']}ms 이상",
                    "current_value": row.get("persistence_ms"),
                    "status": (
                        "PASSED"
                        if int(str(row.get("persistence_ms", 0)))
                        >= int(str(status["minimum_persistence_ms"]))
                        else "BLOCKED"
                    ),
                    "reason_ko": "짧은 호가 잡음을 그대로 진입에 쓰지 않습니다.",
                    "side": side,
                },
            ]
            passed_count = sum(item["status"] == "PASSED" for item in condition_rows)
            side_payloads.append(
                {
                    "side": side,
                    "setup_state": "PASSED" if bool(row.get("allowed")) else "BLOCKED",
                    "passed": passed_count,
                    "total": len(condition_rows),
                    "top_blockers": [
                        str(item["label_ko"])
                        for item in condition_rows
                        if item["status"] != "PASSED"
                    ][:3],
                    "conditions": condition_rows,
                }
            )
        side_payloads.sort(
            key=lambda row: (
                row["setup_state"] != "PASSED",
                -int(str(row["passed"])),
                row["side"] != Side.LONG.value,
            )
        )
        selected = side_payloads[0] if side_payloads else None
        return {
            "schema_version": 1,
            "strategy_id": str(status["filter_id"]),
            "symbol": resolved_symbol,
            "setup_state": (
                "FILTER_OFF"
                if not bool(status["enabled"])
                else str(selected["setup_state"])
                if selected is not None
                else "WAITING_DATA"
            ),
            "passed": selected["passed"] if selected is not None else 0,
            "total": selected["total"] if selected is not None else 13,
            "top_blockers": selected["top_blockers"]
            if selected is not None
            else ["현재 종목의 주문흐름 실측을 기다리고 있습니다."],
            "conditions": selected["conditions"] if selected is not None else [],
            "sides": side_payloads,
            "execution": {},
            "filter": status,
            "creates_candidate_plan": False,
            "pending_count": 0,
            "open_count": 0,
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }

    def configure_orderflow_confirmation_filter(
        self,
        *,
        enabled: bool,
        expected_revision: int,
        reason: str,
    ) -> dict[str, object]:
        updated_ts_ms = self.clock.utc_ms()
        status = self.orderflow_confirmation_runtime.configure(
            enabled=enabled,
            expected_revision=expected_revision,
            updated_ts_ms=updated_ts_ms,
            reason=reason,
        )
        self._persist_orderflow_confirmation_filter()
        self._log(
            "STRATEGY_FILTER",
            (
                f"ORDERFLOW confirmation filter "
                f"{'ON' if enabled else 'OFF'} · rev {status['revision']}"
            ),
        )
        return status

    def _persist_orderflow_confirmation_filter(self) -> None:
        if self.ledger is None or self.mode is RuntimeMode.READY:
            return
        recovery_state = self.orderflow_confirmation_runtime.recovery_state()
        self.ledger.set_app_setting(
            "orderflow_confirmation_filter_v2",
            {
                "run_id": self.run_id,
                **recovery_state,
            },
            updated_ts_ms=int(str(recovery_state["updated_ts_ms"])),
        )

    def _reset_paper_entry_intent(
        self,
        *,
        manual_pause_requested: bool = False,
        actor: str = "USER_UI",
        reason: str = "RUN_STARTED",
        persist: bool = False,
    ) -> None:
        self._manual_pause_requested = manual_pause_requested
        self._paper_entry_intent_revision = 0
        self._paper_entry_intent_actor = actor
        self._paper_entry_intent_reason = reason
        self._paper_entry_intent_updated_ts_ms = self.clock.utc_ms()
        self._paper_entry_intent_idempotency.clear()
        if persist:
            self._persist_paper_entry_intent(updated_ts_ms=self._paper_entry_intent_updated_ts_ms)

    def set_paused(
        self,
        paused: bool,
        *,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
        actor: str = "USER_UI",
        reason: str | None = None,
    ) -> dict[str, object]:
        if self.mode is RuntimeMode.READY:
            self.paused = True
            self._log("RISK", "실시간 PAPER 시작 전에는 진입할 수 없음")
            return self.paper_entry_intent()
        with self._paper_entry_intent_lock:
            if idempotency_key is not None:
                previous_target = self._paper_entry_intent_idempotency.get(idempotency_key)
                if (
                    previous_target is not None
                    or idempotency_key in self._paper_entry_intent_idempotency
                ):
                    if previous_target != paused:
                        raise PaperEntryIntentConflict(
                            error_code="PAPER_ENTRY_IDEMPOTENCY_CONFLICT",
                            expected_revision=expected_revision,
                            current_revision=self._paper_entry_intent_revision,
                        )
                    return self.paper_entry_intent()
            if (
                expected_revision is not None
                and expected_revision != self._paper_entry_intent_revision
            ):
                raise PaperEntryIntentConflict(
                    error_code="PAPER_ENTRY_REVISION_CONFLICT",
                    expected_revision=expected_revision,
                    current_revision=self._paper_entry_intent_revision,
                )
            if paused == self._manual_pause_requested:
                if idempotency_key is not None:
                    self._paper_entry_intent_idempotency[idempotency_key] = paused
                    self._persist_paper_entry_intent(updated_ts_ms=self.clock.utc_ms())
                return self.paper_entry_intent()

            previous_state = "USER_PAUSED" if self._manual_pause_requested else "ENTRY_ENABLED"
            request_revision = self._paper_entry_intent_revision
            timestamp = self.clock.utc_ms()
            transition_id = f"paper-entry-{uuid4().hex}"
            self._manual_pause_requested = paused
            self._paper_entry_intent_revision += 1
            self._paper_entry_intent_actor = actor
            self._paper_entry_intent_reason = reason or ("USER_PAUSE" if paused else "USER_RESUME")
            self._paper_entry_intent_updated_ts_ms = timestamp
            if idempotency_key is not None:
                self._paper_entry_intent_idempotency[idempotency_key] = paused
                self._paper_entry_intent_idempotency = dict(
                    list(self._paper_entry_intent_idempotency.items())[-32:]
                )
            self._persist_paper_entry_intent(updated_ts_ms=timestamp)

            if (
                not paused
                and self.mode is RuntimeMode.LIVE_SHADOW_PAPER
                and (
                    self.market_data_state is not MarketDataState.LIVE
                    or "CRITICAL_MARKET_LAG_ENTRY_LOCK" in self.runtime_health_flags
                    or "PERSISTENCE_BACKLOG_ENTRY_LOCK" in self.runtime_health_flags
                    or (self._supervisor is not None and self._supervisor.telemetry.entry_locked)
                    or self.paper_portfolio.main.risk_state.faulted
                    or not self._refresh_storage_safety(force=True)
                )
            ):
                self.paused = True
                self._log("RISK", "사용자 재개 의도 저장 · 자동 안전대기는 계속 유지")
            else:
                self.paused = paused
                self._log(
                    "RISK",
                    "페이퍼 신규 진입 일시정지" if paused else "페이퍼 신규 진입 재개",
                )

            if self.ledger is not None:
                self.ledger.record_incident(
                    transition_id,
                    run_id=self.run_id,
                    severity="INFO",
                    category="PAPER_ENTRY_INTENT_TRANSITION",
                    ts_ms=timestamp,
                    payload={
                        "transition_id": transition_id,
                        "previous_state": previous_state,
                        "new_state": ("USER_PAUSED" if paused else "ENTRY_ENABLED"),
                        "occurred_ts_ms": timestamp,
                        "cause": self._paper_entry_intent_reason,
                        "cause_code": self._paper_entry_intent_reason,
                        "description_ko": (
                            "사용자가 새 PAPER 진입을 잠시 멈췄습니다. 시장 관찰은 계속됩니다."
                            if paused
                            else "사용자가 새 PAPER 진입 재개를 요청했습니다. "
                            "자동 안전잠금은 별도로 유지됩니다."
                        ),
                        "actor": actor,
                        "run_id": self.run_id,
                        "strategy_id": None,
                        "account_id": None,
                        "symbol": None,
                        "request_revision": request_revision,
                        "response_revision": self._paper_entry_intent_revision,
                        "reversible": True,
                        "idempotency_key": idempotency_key,
                        "runtime_paused": self.paused,
                    },
                )
            return self.paper_entry_intent()

    def emergency_paper_close(self) -> None:
        if self.mode is RuntimeMode.DEMO_FIXTURE:
            self.position_visible = False
            self._log("EXIT", "격리된 DEMO PAPER 포지션 표시 종료")
            return
        requested = self.paper_portfolio.request_main_exit(
            now_ms=self.clock.utc_ms(),
            reason=ExitReason.MANUAL_PAPER_EXIT,
        )
        self._log(
            "EXIT",
            "현재 PAPER 포지션 비상종료 지연 요청"
            if requested
            else "비상종료할 실제 PAPER 포지션 없음",
        )

    async def start_live_run(
        self,
        probe: LiveBootstrapProbe | None = None,
        progress: ProgressCallback | None = None,
    ) -> bool:
        blocked = self.live_start_block()
        if blocked is not None:
            raise ValueError(blocked[1])
        if progress is not None:
            await progress("PREPARING", "새 PAPER Run을 준비하고 있습니다")
        await self.shutdown_supervisor()
        self._archive_current_run("USER_START_LIVE")
        self._archive_superseded_open_runs("SUPERSEDED_BY_START_LIVE")
        self.mode = RuntimeMode.LIVE_SHADOW_PAPER
        self.run_id = f"run-{uuid4().hex[:12]}"
        self._reset_paper_entry_intent(reason="USER_START_LIVE")
        self.venue = Venue.BINANCE_USDM
        self.market_data_state = MarketDataState.DISCONNECTED
        self._events.clear()
        self._reset_research_state()
        self.paused = True
        self.position_visible = False
        self.unrealized_pnl_usdt = 0.0
        self.wide_symbol_count = 0
        self.deep_symbol_count = 0
        self.processing_lag_p95_ms = None
        self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
        self._start_ledger_run()
        self._persist_paper_entry_intent(updated_ts_ms=self.clock.utc_ms())
        self._log("RUN", "Fresh LIVE PAPER Run 생성 · 자산과 손익·비용·거래 0")
        if probe is not None:
            return await self.boot_live_public(probe)
        return await self.start_persistent_live(progress=progress)

    def live_start_block(self) -> tuple[str, str] | None:
        """공개시장 연결 전에 해제할 수 없는 안전잠금만 제어 API에 설명한다."""

        blocked_flags = {
            "RECOVERY_FAIL_CLOSED": (
                "RECOVERY_SAFETY_LOCK",
                "복구 안전검사가 완료되지 않아 자동 관찰을 시작할 수 없습니다.",
            ),
            "PERSISTENCE_FAULT_ENTRY_LOCK": (
                "PERSISTENCE_SAFETY_LOCK",
                "저장 안전오류가 있어 자동 관찰을 시작할 수 없습니다.",
            ),
            "PERSISTENCE_BACKLOG_ENTRY_LOCK": (
                "PERSISTENCE_BACKLOG_SAFETY_LOCK",
                "시장데이터 저장 적체가 회복될 때까지 새 PAPER Run을 시작할 수 없습니다.",
            ),
            "STORAGE_PRESSURE_ENTRY_LOCK": (
                "STORAGE_SAFETY_LOCK",
                "저장공간 안전잠금이 있어 자동 관찰을 시작할 수 없습니다.",
            ),
        }
        for flag in self.runtime_health_flags:
            if flag in blocked_flags:
                return blocked_flags[flag]
        if any(
            account.positions or account.pending_entries
            for account in self.paper_portfolio.accounts
        ):
            return (
                "OPEN_PAPER_EXPOSURE",
                "진행 중인 PAPER 진입 또는 포지션이 있어 새 Run 시작을 차단했습니다.",
            )
        if self.mode is RuntimeMode.READY and self.ledger is not None:
            recovered = self.ledger.recover_latest(recovered_ts_ms=self.clock.utc_ms())
            if recovered is not None and self._recovery_payload_has_exposure(recovered.payload):
                return (
                    "RECOVERY_OPEN_PAPER_EXPOSURE",
                    "이전 Run에 복구할 PAPER 진입 또는 포지션이 있어 새 Run 시작을 차단했습니다.",
                )
        if self.paper_portfolio.main.risk_state.faulted:
            return (
                "PAPER_RECOVERY_SAFETY_LOCK",
                "PAPER 계좌 복구 안전잠금이 있어 자동 관찰을 시작할 수 없습니다.",
            )
        return None

    @staticmethod
    def _recovery_payload_has_exposure(payload: Mapping[str, object]) -> bool:
        if any(payload.get(key) is not None for key in ("open_position", "pending_entry")):
            return True
        portfolio = payload.get("portfolio")
        if portfolio is None:
            return False
        if not isinstance(portfolio, Mapping):
            return True
        exposure_keys = (
            ("positions", "position"),
            ("pending_entries", "pending_entry"),
        )
        for plural_key, singular_key in exposure_keys:
            if plural_key in portfolio:
                plural = portfolio.get(plural_key)
                if isinstance(plural, Mapping | Sequence) and not isinstance(
                    plural,
                    str | bytes,
                ):
                    if plural:
                        return True
                elif plural is not None:
                    return True
            if singular_key in portfolio and portfolio.get(singular_key) is not None:
                return True
        accounts = portfolio.get("accounts")
        if accounts is None:
            return not any(
                key in portfolio
                for key in ("positions", "position", "pending_entries", "pending_entry")
            )
        if not isinstance(accounts, Sequence) or isinstance(accounts, str | bytes):
            return True
        if not accounts:
            return True
        for account in accounts:
            if not isinstance(account, Mapping):
                return True
            recognized_exposure_state = False
            for plural_key, singular_key in exposure_keys:
                recognized_exposure_state = recognized_exposure_state or (
                    plural_key in account or singular_key in account
                )
                plural = account.get(plural_key)
                singular = account.get(singular_key)
                if isinstance(plural, Mapping):
                    if plural:
                        return True
                elif plural is not None:
                    return True
                if singular is not None:
                    return True
            if not recognized_exposure_state:
                return True
        return False

    def _risk_dashboard_contract(self) -> dict[str, object]:
        """실제 실행 상수에서 Shared Capital과 Strategy League 위험표를 만든다."""

        shared = self.paper_portfolio.risk_manager.limits
        league = self.paper_portfolio.league_risk_manager.limits
        cost = self.paper_portfolio.cost_model
        starting = self.paper_portfolio.main.risk_state.starting_equity

        def percentage(value: Decimal) -> str:
            return f"{value * 100:.2f}%"

        def bps(value: Decimal) -> str:
            return f"{value.normalize()}bp"

        return {
            "paper_only": True,
            "active_locks": ["PAPER_ONLY", *self.runtime_health_flags],
            "immutable_run": True,
            "shared_capital": {
                "starting_equity_usdt": str(starting),
                "risk_per_position": percentage(shared.risk_per_trade_fraction),
                "max_positions": shared.max_open_positions,
                "daily_loss_limit": (
                    "중단 없음"
                    if shared.daily_loss_limit_fraction is None
                    else f"{(starting * shared.daily_loss_limit_fraction).normalize()} USDT"
                ),
                "weekly_loss_limit": (
                    "중단 없음"
                    if shared.weekly_loss_limit_fraction is None
                    else f"{(starting * shared.weekly_loss_limit_fraction).normalize()} USDT"
                ),
                "drawdown_lock": percentage(shared.maximum_drawdown_fraction),
            },
            "strategy_league": {
                "account_count": len(self.paper_portfolio.shadows),
                "starting_equity_per_account_usdt": str(starting),
                "risk_per_position": percentage(league.risk_per_trade_fraction),
                "max_positions_per_account": league.max_open_positions,
                "maximum_total_open_risk": percentage(league.maximum_total_open_risk_fraction),
                "maximum_effective_leverage": (f"{league.maximum_gross_notional_fraction:.2f}x"),
                "selected_margin_leverage": f"{int(self._selected_margin_leverage)}x",
                "maximum_depth_fraction": percentage(
                    league.maximum_order_fraction_of_executable_depth
                ),
                "daily_loss_limit": (
                    "중단 없음"
                    if league.daily_loss_limit_fraction is None
                    else percentage(league.daily_loss_limit_fraction)
                ),
                "weekly_loss_limit": (
                    "중단 없음"
                    if league.weekly_loss_limit_fraction is None
                    else percentage(league.weekly_loss_limit_fraction)
                ),
                "daily_trade_limit": (
                    "중단 없음" if league.max_daily_trades is None else str(league.max_daily_trades)
                ),
                "loss_cooldown": ("사용 안 함" if not league.loss_cooldowns_enabled else "사용"),
                "drawdown_lock": percentage(league.maximum_drawdown_fraction),
                "base_entry_fee": bps(cost.fee_bps(entry=True, profile=CostProfile.BASE)),
                "base_exit_fee": bps(cost.fee_bps(entry=False, profile=CostProfile.BASE)),
                "stress_entry_fee": bps(cost.fee_bps(entry=True, profile=CostProfile.STRESS)),
                "stress_exit_fee": bps(cost.fee_bps(entry=False, profile=CostProfile.STRESS)),
            },
        }

    def start_demo_run(self) -> str:
        self._archive_current_run("USER_START_DEMO")
        self._archive_superseded_open_runs("SUPERSEDED_BY_START_DEMO")
        self.mode = RuntimeMode.DEMO_FIXTURE
        self.run_id = f"demo-{uuid4().hex[:12]}"
        self._reset_paper_entry_intent(reason="USER_START_DEMO")
        self.venue = Venue.FIXTURE
        self.market_data_state = MarketDataState.FIXTURE
        self.live_selection = None
        self.wide_symbol_count = 0
        self.deep_symbol_count = 0
        self.processing_lag_p95_ms = None
        self._events.clear()
        self._reset_research_state()
        self.paused = False
        self.position_visible = True
        self.unrealized_pnl_usdt = 0.0
        self.runtime_health_flags = ["OFFLINE_DEMO_ISOLATED"]
        self._start_ledger_run()
        self._persist_paper_entry_intent(updated_ts_ms=self.clock.utc_ms())
        self.boot_demo()
        self._log("RUN", "LIVE 성과와 분리된 오프라인 DEMO Run 생성")
        return self.run_id

    def start_new_run(self) -> str:
        if self.mode is RuntimeMode.READY:
            raise ValueError("READY에서는 먼저 LIVE 또는 DEMO를 시작해야 합니다.")
        previous_run_id = self.run_id
        self.flush_storage()
        self.archived_run_ids.append(self.run_id)
        if self.ledger is not None:
            trades = self.ledger.list_trades(previous_run_id)
            self.ledger.finalize_run(
                previous_run_id,
                finalized_ts_ms=self.clock.utc_ms(),
                summary={"trade_count": len(trades), "preserved": True},
            )
        self._archive_superseded_open_runs("SUPERSEDED_BY_NEW_RUN")
        self.run_id = f"run-{uuid4().hex[:12]}"
        self._reset_paper_entry_intent(reason="USER_NEW_RUN")
        self._events.clear()
        self._reset_research_state()
        self.paused = False
        self.position_visible = True
        if self.ledger is not None:
            self._start_ledger_run()
            self._persist_paper_entry_intent(updated_ts_ms=self.clock.utc_ms())
        if self.mode is RuntimeMode.DEMO_FIXTURE:
            self.boot_demo()
        else:
            self.market_data_state = MarketDataState.DISCONNECTED
            self.runtime_health_flags = ["ENTRY_LOCK_DATA_NOT_VERIFIED"]
            self.paused = True
        self._log("RISK", "기존 Run 보존 후 새 PAPER Run 생성")
        return self.run_id

    def _archive_current_run(self, reason: str) -> None:
        if self.mode is RuntimeMode.READY or self.ledger is None:
            return
        self.flush_storage()
        current = self.ledger.get_run(self.run_id)
        if current is None or current["finalized_ts_ms"] is not None:
            return
        self.archived_run_ids.append(self.run_id)
        self.ledger.finalize_run(
            self.run_id,
            finalized_ts_ms=self.clock.utc_ms(),
            summary={"reason": reason, "preserved": True},
        )

    def _archive_superseded_open_runs(self, reason: str) -> None:
        if self.ledger is None:
            return
        archived = self.ledger.finalize_superseded_open_runs(
            finalized_ts_ms=self.clock.utc_ms(),
            reason=reason,
        )
        self.archived_run_ids.extend(
            run_id for run_id in archived if run_id not in self.archived_run_ids
        )

    def _reset_research_state(self) -> None:
        self.candle_builder = CandleBuilder()
        self.feature_engines.clear()
        self.latest_features.clear()
        self.latest_regimes.clear()
        self.strategy_signals.clear()
        self.strategy_evaluator = StrategySignalEvaluator()
        self.orderflow_confirmation_runtime.reset_configuration()
        self.shadow_ledger = ShadowLedger(self.strategy_registry.strategy_ids)
        self.paper_portfolio = self._new_paper_portfolio(self.shadow_ledger)
        self.latest_books.clear()
        self.plan_rejections.clear()
        self.data_gap_since_ms.clear()
        self._stale_trade_symbols.clear()
        self._strategy_data_health_epoch = 0
        self._feature_input_fault_symbols.clear()
        self._last_strategy_evaluation_ms.clear()
        self._recovery_revalidation_symbols.clear()
        with self._persistence_lock:
            self._market_event_buffer.clear()
            self._candle_buffer.clear()
            self._candidate_plan_buffer.clear()
        self._persisted_main_order_ids.clear()
        self._persisted_main_trade_ids.clear()
        self._persisted_shadow_trade_ids.clear()
        self._persisted_audit_count = 0
        self._dashboard_strategy_performance_cache_key = None
        self._dashboard_strategy_performance_cache = ()
        self._strategy_arbitration_evidence_cache = {}
        self._strategy_arbitration_evidence_ready = False
        self.strategy_evaluation_count = 0
        self._strategy_evaluation_backpressure_active = False
        self._strategy_evaluation_backpressure_skip_count = 0
        self._strategy_evaluation_backpressure_resume_count = 0
        self._directional_change_engines.clear()
        self._directional_change_symbols.clear()
        for profile_id, _threshold in _DIRECTIONAL_CHANGE_PROFILES:
            self._directional_change_initialized[profile_id] = False
            self._directional_change_event_counts[profile_id] = 0
            self._directional_change_last_directions[profile_id] = DCState.UNINITIALIZED
            self._directional_change_last_confirmation_types[profile_id] = "NONE"
        self._semivariance_symbols.clear()
        self._semivariance_engines.clear()
        self._semivariance_previous_completed_closes.clear()
        self._semivariance_latest_snapshots.clear()
        self._semivariance_last_symbol = "NONE"
        self._semivariance_last_completed_minute_ts_ms = None
        self._semivariance_last_status = "WAITING_COMPLETED_MINUTE"
        self._semivariance_last_reset_reason = "NONE"
        self.qualified_signal_count = 0

    def _current_main_book(self) -> BookSnapshot | None:
        position = self.paper_portfolio.main.position
        if position is None:
            return self.latest_books.get(self.selected_symbol)
        return self.latest_books.get(position.plan.symbol)

    def _paper_trade_row(self, trade: PaperTrade) -> dict[str, object]:
        sample_type = (
            "LIVE_PUBLIC"
            if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
            else "DEMO_FIXTURE"
            if self.mode is RuntimeMode.DEMO_FIXTURE
            else "REPLAY"
        )
        return {
            "trade_id": trade.trade_id,
            "run_id": trade.run_id,
            "venue": trade.venue.value,
            "symbol": trade.symbol,
            "strategy_id": trade.strategy_id,
            "side": trade.side.value,
            "entry_price": str(trade.entry_price),
            "exit_price": str(trade.exit_price),
            "initial_stop": str(trade.initial_stop),
            "take_profit": str(trade.take_profit),
            "candidate_id": trade.candidate_id,
            "signal_event_id": trade.signal_event_id,
            "take_profit_1": (
                str(trade.take_profit_1)
                if trade.take_profit_1 is not None
                else str(trade.take_profit)
            ),
            "take_profit_2": (
                str(trade.take_profit_2) if trade.take_profit_2 is not None else None
            ),
            "tp1_hit_ts_ms": trade.tp1_hit_ts_ms,
            "tp2_hit_ts_ms": trade.tp2_hit_ts_ms,
            "time_to_tp1_ms": trade.time_to_tp1_ms,
            "time_to_tp2_ms": trade.time_to_tp2_ms,
            "time_to_stop_ms": trade.time_to_stop_ms,
            "trailing_activation_ts_ms": trade.trailing_activation_ts_ms,
            "runner_started_ts_ms": trade.runner_started_ts_ms,
            "peak_unrealized_usdt": str(trade.peak_unrealized_usdt),
            "giveback_usdt": str(trade.giveback_usdt),
            "runner_net_pnl_usdt": str(trade.runner_net_pnl_usdt),
            "trail_trigger_slippage_usdt": str(trade.trail_trigger_slippage_usdt),
            "trailing_state_checksum": trade.trailing_state_checksum,
            "selected_margin_leverage": str(trade.selected_margin_leverage),
            "entry_notional_usdt": str(trade.entry_notional_usdt),
            "margin_used_usdt": str(trade.margin_used_usdt),
            "quantity": str(trade.quantity),
            "exit_reason": trade.exit_reason.value,
            "gross_pnl_usdt": str(trade.gross_pnl_usdt),
            "fees_usdt": str(trade.fees_usdt),
            "slippage_usdt": str(trade.slippage_usdt),
            "net_pnl_usdt": str(trade.net_pnl_usdt),
            "entry_ts_ms": trade.opened_ts_ms,
            "exit_ts_ms": trade.closed_ts_ms,
            "holding_ms": trade.holding_ms,
            "regime": trade.regime,
            "mae_r": str(trade.mae_r),
            "mfe_r": str(trade.mfe_r),
            "flags": list(trade.flags),
            "profile": trade.profile.value,
            "sample_type": sample_type,
            "strategy_version": trade.strategy_version,
        }

    @staticmethod
    def _candidate_plan_row(plan: CandidatePlan) -> dict[str, object]:
        return {
            "candidate_id": plan.candidate_id,
            "signal_event_id": plan.signal_event_id,
            "run_id": plan.run_id,
            "venue": plan.venue.value,
            "symbol": plan.symbol,
            "strategy_id": plan.strategy_id,
            "strategy_version": plan.strategy_version,
            "exit_style": plan.exit_style.value,
            "direction": plan.direction.value,
            "signal_time_ms": plan.signal_time_ms,
            "expires_at_ms": plan.expires_at_ms,
            "maximum_holding_ms": plan.maximum_holding_ms,
            "regime": plan.regime.value,
            "planned_entry": str(plan.planned_entry),
            "worst_allowed_entry": str(plan.worst_allowed_entry),
            "initial_stop": str(plan.initial_stop),
            "noise_buffer": str(plan.noise_buffer),
            "take_profit_targets": [
                {
                    "label": target.label,
                    "price": str(target.price),
                    "quantity_fraction": str(target.quantity_fraction),
                }
                for target in plan.take_profit_targets
            ],
            "position_size": str(plan.position_size),
            "quantity_step": str(plan.quantity_step),
            "minimum_quantity": str(plan.minimum_quantity),
            "executable_depth_quantity": str(plan.executable_depth_quantity),
            "risk_budget": str(plan.risk_budget),
            "max_planned_loss": str(plan.max_planned_loss),
            "gross_reward_usdt": str(plan.gross_reward_usdt),
            "expected_fees_usdt": str(plan.expected_fees_usdt),
            "expected_slippage_usdt": str(plan.expected_slippage_usdt),
            "net_reward_usdt": str(plan.net_reward_usdt),
            "net_risk_usdt": str(plan.net_risk_usdt),
            "net_reward_risk": str(plan.net_reward_risk),
            "data_quality": str(plan.data_quality),
            "signal_quality": str(plan.signal_quality),
            "liquidity_quality": str(plan.liquidity_quality),
            "cost_burden": str(plan.cost_burden),
            "reason_codes": list(plan.reason_codes),
            "plain_korean_explanation": list(plan.plain_korean_explanation),
            "management_policy": list(plan.management_policy),
            "stop_rationale_ko": plan.stop_rationale_ko,
            "take_profit_1_rationale_ko": plan.take_profit_1_rationale_ko,
            "take_profit_2_rationale_ko": plan.take_profit_2_rationale_ko,
            "reference_timeframes_ko": list(plan.reference_timeframes_ko),
            "selected_margin_leverage": str(plan.selected_margin_leverage),
            "planned_margin_usdt": str(
                plan.position_size * plan.worst_allowed_entry / plan.selected_margin_leverage
            ),
            "main_eligible": plan.main_eligible,
            "shadow_eligible": plan.shadow_eligible,
            "shared_capital_evidence": {
                "evidence_tier": plan.shared_capital_evidence.evidence_tier,
                "stress_cost_adjusted_expectancy_usdt": (
                    str(plan.shared_capital_evidence.stress_cost_adjusted_expectancy_usdt)
                    if plan.shared_capital_evidence.stress_cost_adjusted_expectancy_usdt is not None
                    else None
                ),
                "cost_coverage": (
                    str(plan.shared_capital_evidence.cost_coverage)
                    if plan.shared_capital_evidence.cost_coverage is not None
                    else None
                ),
                "diversification_score": str(plan.shared_capital_evidence.diversification_score),
            },
            "status": "ARMED",
        }

    def _buffer_completed_candles(self, candles: list[Candle]) -> None:
        if self.ledger is None:
            return
        with self._persistence_lock:
            self._candle_buffer.extend(
                {
                    "run_id": self.run_id,
                    "symbol": candle.symbol,
                    "interval_seconds": candle.interval_seconds,
                    "open_ts_ms": candle.open_ts_ms,
                    "open": str(candle.open),
                    "high": str(candle.high),
                    "low": str(candle.low),
                    "close": str(candle.close),
                    "volume": str(candle.volume),
                    "trade_count": candle.trade_count,
                    "quote_volume": str(candle.quote_volume),
                    "taker_buy_volume": str(candle.taker_buy_volume),
                    "taker_sell_volume": str(candle.taker_sell_volume),
                    "taker_buy_quote_volume": str(candle.taker_buy_quote_volume),
                    "taker_sell_quote_volume": str(candle.taker_sell_quote_volume),
                }
                for candle in candles
                if candle.interval_seconds in _PERSISTED_CANDLE_INTERVALS
            )
            if self._persistence_fault_active and len(self._candle_buffer) > 5_000:
                overflow = len(self._candle_buffer) - 5_000
                self._persistence_buffer_dropped += overflow
                del self._candle_buffer[:overflow]

    def _persist_execution_state_safely(self, ts_ms: int) -> bool:
        if not self._has_unpersisted_execution_state():
            return True
        started = time.monotonic()
        item_count = 0
        try:
            item_count = self._persist_execution_state(ts_ms)
        except Exception as error:
            self._handle_persistence_fault(error)
            return False
        finally:
            elapsed_ms = (time.monotonic() - started) * 1_000
            completed_ts_ms = self.clock.utc_ms()
            self._execution_persistence_count += 1
            self._execution_persistence_last_ms = elapsed_ms
            self._execution_persistence_last_completed_ts_ms = completed_ts_ms
            self._execution_persistence_last_items = item_count
            if elapsed_ms > self._execution_persistence_max_ms:
                self._execution_persistence_max_ms = elapsed_ms
                self._execution_persistence_max_ts_ms = completed_ts_ms
        return True

    def _has_unpersisted_execution_state(self) -> bool:
        """후보·감사·주문·거래가 실제로 바뀐 경우에만 외장 SQLite를 호출한다."""

        with self._persistence_lock:
            if self._candidate_plan_buffer:
                return True
        if len(self.paper_portfolio.audit_events) > self._persisted_audit_count:
            return True
        main_orders = (
            *self.paper_portfolio.main.entry_orders,
            *self.paper_portfolio.main.exit_orders,
        )
        if any(order.order_id not in self._persisted_main_order_ids for order in main_orders):
            return True
        if any(
            trade.trade_id not in self._persisted_main_trade_ids
            for trade in self.paper_portfolio.main.completed_trades
        ):
            return True
        return any(
            trade.trade_id not in self._persisted_shadow_trade_ids
            for account in self.paper_portfolio.shadows.values()
            for trade in account.completed_trades
        )

    def _persist_execution_state(self, ts_ms: int) -> int:
        if self.ledger is None or self.mode is RuntimeMode.READY:
            return 0
        with self._persistence_lock:
            candidate_rows = list(self._candidate_plan_buffer)
        main_orders = (
            *self.paper_portfolio.main.entry_orders,
            *self.paper_portfolio.main.exit_orders,
        )
        new_orders = [
            order for order in main_orders if order.order_id not in self._persisted_main_order_ids
        ]
        new_main_trades = [
            trade
            for trade in self.paper_portfolio.main.completed_trades
            if trade.trade_id not in self._persisted_main_trade_ids
        ]
        new_shadow_trades = [
            trade
            for account in self.paper_portfolio.shadows.values()
            for trade in account.completed_trades
            if trade.trade_id not in self._persisted_shadow_trade_ids
        ]
        new_audits = list(self.paper_portfolio.audit_events[self._persisted_audit_count :])

        order_rows = [self._paper_order_row(order, ts_ms) for order in new_orders]
        fill_rows = [
            {
                "fill_id": f"fill-{order.order_id}",
                "run_id": order.run_id,
                "order_id": order.order_id,
                "side": order.side,
                "planned_price": str(
                    order.price_cap or order.trigger_price or order.fill.average_price
                ),
                "price": str(order.fill.average_price),
                "quantity": str(order.fill.quantity),
                "fee_usdt": str(order.fill.fee_usdt),
                "slippage_usdt": str(order.fill.slippage_usdt),
                "ts_ms": order.fill.book_ts_ms,
            }
            for order in new_orders
            if order.fill is not None
        ]

        run = None
        if new_main_trades or new_shadow_trades:
            run = self.ledger.get_run(self.run_id)
            if run is None:
                raise RuntimeError("완료 PAPER 거래가 참조할 Run이 없습니다.")
        main_trade_rows: list[dict[str, object]] = []
        for trade in new_main_trades:
            row = self._paper_trade_row(trade)
            assert run is not None
            row["config_hash"] = str(run["config_hash"])
            main_trade_rows.append(row)
        shadow_trade_rows: list[dict[str, object]] = []
        for trade in new_shadow_trades:
            row = self._paper_trade_row(trade)
            row["shadow_trade_id"] = trade.trade_id
            row["closed_ts_ms"] = trade.closed_ts_ms
            assert run is not None
            row["config_hash"] = str(run["config_hash"])
            shadow_trade_rows.append(row)

        account_snapshots: list[dict[str, object]] = []
        recovery_snapshot: dict[str, object] | None = None
        state_audits = [
            audit
            for audit in new_audits
            if str(audit.get("event", "")) in _RECOVERY_STATE_AUDIT_EVENTS
        ]
        if state_audits:
            changed_account_ids = {
                str(audit["account_id"])
                for audit in state_audits
                if audit.get("account_id") is not None
                and str(audit["account_id"]) != self.paper_portfolio.MAIN_ACCOUNT_ID
            }
            for account_row in self.paper_portfolio.league_account_rows():
                if str(account_row["account_id"]) not in changed_account_ids:
                    continue
                account_snapshots.append(
                    {
                        "run_id": self.run_id,
                        "strategy_id": account_row["strategy_id"],
                        "profile": account_row["profile"],
                        "ts_ms": ts_ms,
                        **account_row,
                    }
                )
            position = self.paper_portfolio.main_position_snapshot(self._current_main_book())
            recovery_snapshot = {
                "run_id": self.run_id,
                "lifecycle_state": self.paper_portfolio.lifecycle_state(),
                "ts_ms": ts_ms,
                "payload": {
                    "snapshot_ts_ms": ts_ms,
                    "open_position": position,
                    "portfolio": self.paper_portfolio.recovery_state(
                        registry_settings=self.strategy_registry.rows(),
                        snapshot_ts_ms=ts_ms,
                    ),
                },
            }

        # 식별자 cache는 원자적 커밋이 성공한 뒤에만 전진시킨다.
        self.ledger.record_execution_state_batch(
            run_id=self.run_id,
            candidates=candidate_rows,
            orders=order_rows,
            fills=fill_rows,
            trades=main_trade_rows,
            shadow_trades=shadow_trade_rows,
            audits=new_audits,
            account_snapshots=account_snapshots,
            recovery_snapshot=recovery_snapshot,
        )
        if candidate_rows:
            with self._persistence_lock:
                persisted_candidate_ids = {
                    str(candidate["candidate_id"]) for candidate in candidate_rows
                }
                self._candidate_plan_buffer = [
                    candidate
                    for candidate in self._candidate_plan_buffer
                    if str(candidate["candidate_id"]) not in persisted_candidate_ids
                ]
        self._persisted_main_order_ids.update(order.order_id for order in new_orders)
        self._persisted_main_trade_ids.update(trade.trade_id for trade in new_main_trades)
        self._persisted_shadow_trade_ids.update(trade.trade_id for trade in new_shadow_trades)
        self._persisted_audit_count = len(self.paper_portfolio.audit_events)
        return (
            len(candidate_rows)
            + len(order_rows)
            + len(fill_rows)
            + len(main_trade_rows)
            + len(shadow_trade_rows)
            + len(new_audits)
            + len(account_snapshots)
            + (1 if recovery_snapshot is not None else 0)
        )

    @staticmethod
    def _paper_order_row(order: PaperOrder, fallback_ts_ms: int) -> dict[str, object]:
        fill = order.fill
        created_ts = order.created_ts_ms or fallback_ts_ms
        return {
            "order_id": order.order_id,
            "run_id": order.run_id,
            "trade_id": order.trade_id,
            "venue": order.venue.value,
            "symbol": order.symbol,
            "side": order.side,
            "intent": order.intent.value,
            "status": order.status.value,
            "requested_qty": str(order.requested_quantity),
            "filled_qty": str(order.filled_quantity),
            "price_cap": str(order.price_cap) if order.price_cap is not None else None,
            "trigger_price": str(order.trigger_price) if order.trigger_price is not None else None,
            "average_fill_price": str(fill.average_price) if fill is not None else None,
            "created_ts_ms": created_ts,
            "arrival_ts_ms": order.arrival_ts_ms,
            "finalized_ts_ms": fill.book_ts_ms if fill is not None else created_ts,
            "fee_usdt": str(fill.fee_usdt) if fill is not None else "0",
            "slippage_usdt": str(fill.slippage_usdt) if fill is not None else "0",
            "reason_codes": list(order.reason_codes),
        }

    def _take_persistence_batch(
        self,
        market_limit: int | None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        with self._persistence_lock:
            if market_limit is None:
                market_batch = self._market_event_buffer
                self._market_event_buffer = []
            else:
                market_batch = self._market_event_buffer[:market_limit]
                del self._market_event_buffer[: len(market_batch)]
            candle_batch = self._candle_buffer
            self._candle_buffer = []
            persistence_backlog = len(self._market_event_buffer)
        self._refresh_persistence_backlog_safety(persistence_backlog)
        return market_batch, candle_batch

    def _restore_persistence_batch(
        self,
        market_batch: list[dict[str, object]],
        candle_batch: list[dict[str, object]],
    ) -> None:
        with self._persistence_lock:
            market_rows = [*market_batch, *self._market_event_buffer]
            candle_rows = [*candle_batch, *self._candle_buffer]
            if len(market_rows) > 10_000:
                self._persistence_buffer_dropped += len(market_rows) - 10_000
                market_rows = market_rows[-10_000:]
            if len(candle_rows) > 5_000:
                self._persistence_buffer_dropped += len(candle_rows) - 5_000
                candle_rows = candle_rows[-5_000:]
            self._market_event_buffer = market_rows
            self._candle_buffer = candle_rows
            persistence_backlog = len(self._market_event_buffer)
        self._refresh_persistence_backlog_safety(persistence_backlog)

    def _refresh_persistence_backlog_safety(self, event_count: int) -> None:
        """저장 적체가 커지면 유실 없이 신규 PAPER 진입만 가역적으로 잠근다."""

        self._persistence_backlog_peak = max(self._persistence_backlog_peak, event_count)
        flag = "PERSISTENCE_BACKLOG_ENTRY_LOCK"
        already_locked = flag in self.runtime_health_flags
        should_lock = event_count >= _PERSISTENCE_BACKLOG_ENTRY_LOCK_EVENTS or (
            already_locked and event_count > _PERSISTENCE_BACKLOG_RECOVERY_EVENTS
        )
        if should_lock:
            self.paused = True
            if not already_locked:
                self._persistence_backlog_entry_lock_count += 1
                self.runtime_health_flags.append(flag)
            return
        if already_locked:
            self.runtime_health_flags = [
                current for current in self.runtime_health_flags if current != flag
            ]

    @staticmethod
    def _group_market_archive_rows(
        market_batch: list[dict[str, object]],
    ) -> list[list[dict[str, object]]]:
        grouped: dict[tuple[str, str, int], list[dict[str, object]]] = {}
        for event in market_batch:
            key = (
                str(event["run_id"]),
                str(event["venue"]),
                int(str(event["venue_ts_ms"])) // 3_600_000,
            )
            grouped.setdefault(key, []).append(event)
        return list(grouped.values())

    def _persist_batches(
        self,
        market_batch: list[dict[str, object]],
        candle_batch: list[dict[str, object]],
    ) -> None:
        if self.ledger is None:
            return
        if market_batch:
            if self.market_event_archive is None:
                self.ledger.record_market_events(market_batch)
            else:
                archive_records: list[tuple[ArchivedEventBatch, list[dict[str, object]]]] = []
                for rows in self._group_market_archive_rows(market_batch):
                    archive = self.market_event_archive.write_market_event_batch(rows)
                    archive_records.append((archive, rows))
                self.ledger.record_archives_and_candles(
                    archive_records,
                    candle_batch,
                )
                candle_batch = []
        if candle_batch:
            self.ledger.record_candles(candle_batch)

    def _flush_persistence(self, market_limit: int | None = None) -> None:
        market_batch, candle_batch = self._take_persistence_batch(market_limit)
        try:
            self._persist_batches(market_batch, candle_batch)
        except Exception as error:
            self._restore_persistence_batch(market_batch, candle_batch)
            self._handle_persistence_fault(error)
        else:
            self._record_replay_persisted_events(market_batch)

    async def _flush_persistence_isolated(
        self,
        market_limit: int | None,
    ) -> dict[str, float | int]:
        """Parquet과 SQLite FULL 커밋을 시장 처리 프로세스 밖에서 확정한다."""

        market_batch, candle_batch = self._take_persistence_batch(market_limit)
        timings: dict[str, float | int] = {
            "gate_wait_ms": 0.0,
            "archive_ms": 0.0,
            "ledger_ms": 0.0,
            "ledger_connect_ms": 0.0,
            "ledger_begin_wait_ms": 0.0,
            "ledger_write_ms": 0.0,
            "ledger_commit_ms": 0.0,
            "ledger_close_ms": 0.0,
            "market_events": len(market_batch),
            "candles": len(candle_batch),
            "archive_batches": 0,
            "wal_probe_ms": 0.0,
            "wal_log_frames": -1,
            "wal_checkpointed_frames": -1,
            "wal_page_size": 4_096,
        }
        if self.ledger is None:
            return timings
        loop = asyncio.get_running_loop()
        try:
            if market_batch:
                if self.market_event_archive is None:
                    started = loop.time()
                    await asyncio.to_thread(self.ledger.record_market_events, market_batch)
                    timings["ledger_ms"] += (loop.time() - started) * 1_000
                else:
                    store = self.market_event_archive
                    groups = self._group_market_archive_rows(market_batch)
                    process_timings = await to_process.run_sync(
                        persist_archives_and_candles_in_process,
                        str(store.root),
                        store.minimum_free_bytes,
                        store.minimum_free_ratio,
                        str(self.ledger.path),
                        groups,
                        candle_batch,
                        True,
                    )
                    timings.update(process_timings)
                    candle_batch = []
            if candle_batch:
                started = loop.time()
                await asyncio.to_thread(self.ledger.record_candles, candle_batch)
                timings["ledger_ms"] += (loop.time() - started) * 1_000
        except Exception as error:
            self._restore_persistence_batch(market_batch, candle_batch)
            self._handle_persistence_fault(error)
        else:
            self._record_replay_persisted_events(market_batch)
        return timings

    def _record_replay_persisted_events(
        self,
        market_batch: list[dict[str, object]],
    ) -> None:
        if not market_batch or not self.dashboard_trade_cache_ready:
            return
        counts: dict[str, int] = {}
        for event in market_batch:
            run_id = str(event.get("run_id", ""))
            counts[run_id] = counts.get(run_id, 0) + 1
        with self._dashboard_trade_cache_lock:
            for run_id, count in counts.items():
                self._replay_run_persisted_deltas[run_id] = (
                    self._replay_run_persisted_deltas.get(run_id, 0) + count
                )

    async def run_persistence_worker(self, stop: asyncio.Event) -> None:
        """시장 직렬화·fsync를 시장데이터 이벤트 루프 밖에서 실행한다."""

        async def flush_universe_snapshots() -> None:
            if self.ledger is None:
                return
            with self._persistence_lock:
                snapshots = self._universe_snapshot_buffer
                self._universe_snapshot_buffer = []
            if not snapshots:
                return
            started = asyncio.get_running_loop().time()
            try:
                await asyncio.to_thread(self._persist_universe_snapshot_batch, snapshots)
            except Exception as error:
                with self._persistence_lock:
                    self._universe_snapshot_buffer = [
                        *snapshots,
                        *self._universe_snapshot_buffer,
                    ]
                self._handle_persistence_fault(error)
                return
            elapsed_ms = (asyncio.get_running_loop().time() - started) * 1_000
            self._universe_snapshot_persisted_count += len(snapshots)
            self._universe_snapshot_persistence_last_ms = elapsed_ms
            self._universe_snapshot_persistence_max_ms = max(
                self._universe_snapshot_persistence_max_ms,
                elapsed_ms,
            )

        async def finish_wal_checkpoint(*, wait: bool = False) -> None:
            task = self._wal_checkpoint_task
            if task is None or (not wait and not task.done()):
                return
            started = self._wal_checkpoint_task_started_at
            started_flush_count = self._wal_checkpoint_task_started_flush_count
            wal_path = (
                self.ledger.path.with_name(f"{self.ledger.path.name}-wal")
                if self.ledger is not None
                else None
            )
            try:
                busy, log_frames, checkpointed_frames = await task
            except Exception as error:
                self._wal_checkpoint_fault_count += 1
                self._wal_checkpoint_last_error = f"{type(error).__name__}: {error}"
                if "WAL_CHECKPOINT_DEGRADED" not in self.runtime_health_flags:
                    self.runtime_health_flags.append("WAL_CHECKPOINT_DEGRADED")
                self._wal_checkpoint_next_flush = self._persistence_flush_count + 1
                wal_size = (
                    wal_path.stat().st_size if wal_path is not None and wal_path.exists() else 0
                )
                self._wal_checkpoint_last_wal_bytes = wal_size
                if wal_size >= _MAX_WAL_BYTES_WITHOUT_CHECKPOINT:
                    self._handle_persistence_fault(
                        RuntimeError(
                            "WAL_CHECKPOINT_FAILED_AND_WAL_TOO_LARGE: "
                            f"bytes={wal_size}; error={self._wal_checkpoint_last_error}"
                        )
                    )
                return
            finally:
                self._wal_checkpoint_task = None
                self._wal_checkpoint_task_started_at = None

            elapsed_ms = (
                (asyncio.get_running_loop().time() - started) * 1_000
                if started is not None
                else 0.0
            )
            concurrent_flush_delta = max(
                0,
                self._persistence_flush_count - started_flush_count,
            )
            self._wal_checkpoint_last_concurrent_flush_delta = concurrent_flush_delta
            self._wal_checkpoint_max_concurrent_flush_delta = max(
                self._wal_checkpoint_max_concurrent_flush_delta,
                concurrent_flush_delta,
            )
            self._wal_checkpoint_count += 1
            self._wal_checkpoint_last_ms = elapsed_ms
            self._wal_checkpoint_max_ms = max(self._wal_checkpoint_max_ms, elapsed_ms)
            self._wal_checkpoint_log_frames = log_frames
            self._wal_checkpointed_frames = checkpointed_frames
            self._wal_checkpoint_probe_log_frames = log_frames
            self._wal_checkpoint_probe_checkpointed_frames = checkpointed_frames
            self._wal_checkpoint_pending_bytes = max(
                0,
                (log_frames - checkpointed_frames) * self._wal_checkpoint_probe_page_size,
            )
            self._wal_checkpoint_last_completed_ts_ms = self.clock.utc_ms()
            self._wal_checkpoint_last_error = None
            self.runtime_health_flags = [
                flag for flag in self.runtime_health_flags if flag != "WAL_CHECKPOINT_DEGRADED"
            ]
            if elapsed_ms >= _SLOW_WAL_CHECKPOINT_MS:
                self._wal_checkpoint_slow_count += 1
            incomplete = busy != 0 or checkpointed_frames < log_frames
            if incomplete:
                self._wal_checkpoint_busy_count += 1
                self._wal_checkpoint_next_flush = self._persistence_flush_count + 1
                pending_frames = max(0, log_frames - checkpointed_frames)
                if pending_frames >= _MAX_WAL_FRAMES_WITHOUT_CHECKPOINT:
                    self._handle_persistence_fault(
                        RuntimeError(
                            "WAL_CHECKPOINT_INCOMPLETE_AND_WAL_TOO_LARGE: "
                            f"frames={log_frames}; checkpointed={checkpointed_frames}; "
                            f"pending={pending_frames}"
                        )
                    )
            else:
                self._wal_checkpoint_next_flush = (
                    self._persistence_flush_count + _WAL_CHECKPOINT_FLUSH_INTERVAL
                )

        async def checkpoint_wal_if_due() -> None:
            await finish_wal_checkpoint()
            if self.ledger is None:
                return
            if self._wal_checkpoint_task is not None:
                return
            if self._persistence_flush_count < self._wal_checkpoint_next_flush:
                return
            wal_path = self.ledger.path.with_name(f"{self.ledger.path.name}-wal")
            wal_size = wal_path.stat().st_size if wal_path.exists() else 0
            self._wal_checkpoint_last_wal_bytes = wal_size
            logical_probe_available = (
                self._wal_checkpoint_probe_log_frames >= 0
                and self._wal_checkpoint_probe_checkpointed_frames >= 0
            )
            checkpoint_pressure_bytes = (
                self._wal_checkpoint_pending_bytes if logical_probe_available else wal_size
            )
            if checkpoint_pressure_bytes < _WAL_CHECKPOINT_SOFT_BYTES:
                self._wal_checkpoint_deferred_count += 1
                self._wal_checkpoint_next_flush = (
                    self._persistence_flush_count + _WAL_CHECKPOINT_FLUSH_INTERVAL
                )
                return
            self._wal_checkpoint_task_started_at = asyncio.get_running_loop().time()
            self._wal_checkpoint_task_started_flush_count = self._persistence_flush_count
            self._wal_checkpoint_task = asyncio.create_task(
                to_process.run_sync(
                    run_passive_wal_checkpoint_in_process,
                    str(self.ledger.path),
                    True,
                )
            )

        async def flush(limit: int | None) -> None:
            started = asyncio.get_running_loop().time()
            timings = await self._flush_persistence_isolated(limit)
            elapsed_ms = (asyncio.get_running_loop().time() - started) * 1_000
            completed_ts_ms = self.clock.utc_ms()
            self._persistence_flush_count += 1
            wal_log_frames = int(timings["wal_log_frames"])
            wal_checkpointed_frames = int(timings["wal_checkpointed_frames"])
            if wal_log_frames >= 0 and wal_checkpointed_frames >= 0:
                self._wal_checkpoint_probe_log_frames = wal_log_frames
                self._wal_checkpoint_probe_checkpointed_frames = wal_checkpointed_frames
                self._wal_checkpoint_probe_page_size = int(timings["wal_page_size"])
                self._wal_checkpoint_pending_bytes = max(
                    0,
                    (wal_log_frames - wal_checkpointed_frames)
                    * self._wal_checkpoint_probe_page_size,
                )
            self._persistence_flush_last_ms = elapsed_ms
            self._persistence_flush_last_completed_ts_ms = completed_ts_ms
            if elapsed_ms > self._persistence_flush_max_ms:
                self._persistence_flush_max_ms = elapsed_ms
                self._persistence_flush_max_ts_ms = completed_ts_ms
                self._persistence_flush_slowest_gate_wait_ms = float(timings["gate_wait_ms"])
                self._persistence_flush_slowest_archive_ms = float(timings["archive_ms"])
                self._persistence_flush_slowest_ledger_ms = float(timings["ledger_ms"])
                self._persistence_flush_slowest_ledger_connect_ms = float(
                    timings["ledger_connect_ms"]
                )
                self._persistence_flush_slowest_ledger_begin_wait_ms = float(
                    timings["ledger_begin_wait_ms"]
                )
                self._persistence_flush_slowest_ledger_write_ms = float(timings["ledger_write_ms"])
                self._persistence_flush_slowest_ledger_commit_ms = float(
                    timings["ledger_commit_ms"]
                )
                self._persistence_flush_slowest_ledger_close_ms = float(timings["ledger_close_ms"])
                self._persistence_flush_slowest_market_events = int(timings["market_events"])
                self._persistence_flush_slowest_candles = int(timings["candles"])
                self._persistence_flush_slowest_archive_batches = int(timings["archive_batches"])
            if elapsed_ms >= _SLOW_PERSISTENCE_FLUSH_MS:
                self._persistence_flush_slow_count += 1
                self._persistence_flush_last_slow_ts_ms = completed_ts_ms
            await checkpoint_wal_if_due()

        while not stop.is_set():
            await finish_wal_checkpoint()
            with self._persistence_lock:
                should_flush = (
                    len(self._market_event_buffer) >= _MARKET_PERSISTENCE_FLUSH_THRESHOLD
                    and not self._persistence_fault_active
                )
                should_flush_universe = bool(self._universe_snapshot_buffer) and (
                    not self._persistence_fault_active
                )
            if should_flush_universe:
                await flush_universe_snapshots()
            if should_flush:
                await flush(_MARKET_PERSISTENCE_BATCH_SIZE)
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.25)
            except TimeoutError:
                continue
        with self._persistence_lock:
            has_pending = bool(self._market_event_buffer or self._candle_buffer)
            has_pending_universe = bool(self._universe_snapshot_buffer)
        if has_pending_universe and not self._persistence_fault_active:
            await flush_universe_snapshots()
        if has_pending and not self._persistence_fault_active:
            await flush(None)
        await finish_wal_checkpoint(wait=True)

    def _persist_universe_snapshot_batch(
        self,
        snapshots: Sequence[Mapping[str, object]],
    ) -> None:
        """한 worker thread에서 회전 감사행을 순서대로 확정한다."""

        if self.ledger is None:
            return
        for snapshot in snapshots:
            self.ledger.record_universe_snapshot(snapshot)

    def _start_ledger_run(self) -> None:
        if self.ledger is None:
            return
        self.ledger.start_run(
            self.run_id,
            mode=self.mode.value,
            venue=self.venue.value,
            config={
                "starting_equity_usdt": "1000",
                "execution": "PAPER",
                "sample_type": (
                    "DEMO_FIXTURE"
                    if self.mode is RuntimeMode.DEMO_FIXTURE
                    else "LIVE_PUBLIC"
                    if self.mode is RuntimeMode.LIVE_SHADOW_PAPER
                    else "REPLAY"
                ),
                "seed": 20260822,
                "app_version": APP_VERSION,
                "strategy_version": STRATEGY_VERSION,
                "git_commit": git_commit(),
            },
            started_ts_ms=self.clock.utc_ms(),
        )
        for row in self.strategy_registry.rows():
            self.ledger.record_strategy_setting(
                {
                    "run_id": self.run_id,
                    "strategy_id": row["strategy_id"],
                    "ts_ms": self.clock.utc_ms(),
                    "mode": row["mode"],
                    "lifecycle": row["lifecycle"],
                    "long_enabled": row["long_enabled"],
                    "short_enabled": row["short_enabled"],
                    "settings_revision": row["settings_revision"],
                    "manual_lock": row["manual_lock"],
                    "changed_by": row["changed_by"],
                    "change_reason": row["change_reason"],
                    "settings_updated_ts_ms": row["settings_updated_ts_ms"],
                }
            )
        timestamp = self.clock.utc_ms()
        self.ledger.save_snapshot(
            self.run_id,
            lifecycle_state="SCANNING",
            ts_ms=timestamp,
            payload={
                "snapshot_ts_ms": timestamp,
                "open_position": None,
                "portfolio": self.paper_portfolio.recovery_state(
                    registry_settings=self.strategy_registry.rows(),
                    snapshot_ts_ms=timestamp,
                ),
            },
        )
        self._persist_orderflow_confirmation_filter()
        if not self.dashboard_trade_cache_loading:
            self._refresh_dashboard_trade_cache()

    def _refresh_dashboard_trade_cache(self) -> None:
        started = time.monotonic()
        with self._dashboard_trade_cache_lock:
            self.dashboard_trade_cache_loading = True
            succeeded = False
            try:
                if self.ledger is None:
                    self._historical_live_trades = ()
                    self._historical_prior_version_live_trades = ()
                    self._historical_shadow_trades = ()
                    self._historical_prior_version_shadow_trades = ()
                    self._historical_all_main_trades = ()
                    self._historical_all_shadow_trades = ()
                    self._historical_replay_run_summaries = ()
                    self._replay_run_persisted_deltas = {}
                    succeeded = True
                    return
                all_main_trades = self.ledger.list_trades()
                all_shadow_trades = self.ledger.list_shadow_trades()
                replay_run_summaries = self.ledger.list_replayable_run_summaries()
                self._historical_all_main_trades = tuple(all_main_trades)
                self._historical_all_shadow_trades = tuple(all_shadow_trades)
                self._historical_replay_run_summaries = tuple(
                    dict(row) for row in replay_run_summaries
                )
                self._replay_run_persisted_deltas = {}
                current_live_trades, prior_version_live_trades = (
                    self._current_strategy_version_trades(all_main_trades)
                )
                self._historical_live_trades = tuple(current_live_trades)
                self._historical_prior_version_live_trades = tuple(prior_version_live_trades)
                current_shadow_trades, prior_version_shadow_trades = (
                    self._current_strategy_version_trades(all_shadow_trades)
                )
                self._historical_shadow_trades = tuple(current_shadow_trades)
                self._historical_prior_version_shadow_trades = tuple(prior_version_shadow_trades)
                self._dashboard_strategy_performance_cache_key = None
                self._dashboard_strategy_performance_cache = ()
                arbitration_reports = TradeAnalytics().strategy_reports(
                    current_shadow_trades,
                    strategy_ids=self.strategy_registry.strategy_ids,
                )
                self._hydrate_strategy_arbitration_evidence(arbitration_reports)
                succeeded = True
            finally:
                self.dashboard_trade_cache_last_ms = (time.monotonic() - started) * 1_000
                self.dashboard_trade_cache_completed_ts_ms = self.clock.utc_ms()
                self.dashboard_trade_cache_loading = False
                self.dashboard_trade_cache_ready = succeeded

    async def warm_dashboard_trade_cache(self) -> None:
        await asyncio.to_thread(self._refresh_dashboard_trade_cache)

    def _dashboard_live_main_trades(self) -> tuple[dict[str, object], ...]:
        rows = {str(trade["trade_id"]): trade for trade in self._historical_live_trades}
        persisted_ids = {str(trade["trade_id"]) for trade in self._historical_all_main_trades}
        for trade in self.paper_portfolio.main.completed_trades:
            if trade.trade_id not in persisted_ids:
                rows[trade.trade_id] = self._paper_trade_row(trade)
        return tuple(rows.values())

    def _dashboard_live_shadow_trades(self) -> tuple[dict[str, object], ...]:
        rows = {str(trade["trade_id"]): trade for trade in self._historical_shadow_trades}
        persisted_ids = {
            str(trade.get("trade_id", trade.get("shadow_trade_id")))
            for trade in self._historical_all_shadow_trades
        }
        for account in self.paper_portfolio.shadows.values():
            for trade in account.completed_trades:
                if trade.trade_id not in persisted_ids:
                    rows[trade.trade_id] = self._paper_trade_row(trade)
        return tuple(rows.values())

    def _history_main_trades(self) -> tuple[dict[str, object], ...]:
        rows = {str(trade["trade_id"]): trade for trade in self._historical_all_main_trades}
        for trade in self.paper_portfolio.main.completed_trades:
            if trade.trade_id not in rows:
                rows[trade.trade_id] = self._paper_trade_row(trade)
        return tuple(rows.values())

    def _history_shadow_trades(self) -> tuple[dict[str, object], ...]:
        rows = {
            str(trade.get("trade_id", trade.get("shadow_trade_id"))): trade
            for trade in self._historical_all_shadow_trades
        }
        for account in self.paper_portfolio.shadows.values():
            for trade in account.completed_trades:
                if trade.trade_id not in rows:
                    rows[trade.trade_id] = self._paper_trade_row(trade)
        return tuple(rows.values())

    def _ensure_fixture_completed_trade(self) -> None:
        if self.ledger is None or self.ledger.list_trades(self.run_id):
            return
        timestamp = self.clock.utc_ms()
        fixture_opportunity_id = f"{self.run_id}-fixture-opportunity-001"
        fixture_candidate_id = f"{self.run_id}-fixture-candidate-001"
        fixture_signal_event_id = f"{self.run_id}-fixture-signal-001"
        fixture_path = (
            ("OBSERVING", "FIXTURE_BOOK_VALID", 185_000),
            ("ARMED", "LSA_CONFIRMED", 184_500),
            ("ENTRY_PENDING", "ENTRY_IOC", 184_075),
            ("PROTECTED", "FULL_FILL_WITH_PROTECTION", 184_000),
            ("CLOSED", "TAKE_PROFIT", 0),
        )
        previous_state = "NONE"
        fixture_descriptions = {
            "OBSERVING": "오프라인 DEMO 호가 관찰을 시작했습니다.",
            "ARMED": "오프라인 DEMO 진입 계획을 확정했습니다.",
            "ENTRY_PENDING": "오프라인 DEMO 진입 체결을 대기합니다.",
            "PROTECTED": "오프라인 DEMO 진입을 체결하고 보호관리를 시작했습니다.",
            "CLOSED": "오프라인 DEMO 거래를 종료했습니다.",
        }
        for revision, (state, reason_code, age_ms) in enumerate(fixture_path, start=1):
            evidence: dict[str, object] = {}
            if state == "ARMED":
                evidence = {
                    "planned_entry": "100.00",
                    "planned_take_profit": "102.00",
                    "planned_stop": "99.55",
                }
            elif state == "PROTECTED":
                evidence = {"actual_entry": "100.10", "protected_quantity": "1"}
            elif state == "CLOSED":
                evidence = {"actual_exit": "101.90", "remaining_quantity": "0"}
            transition = {
                "transition_id": f"fixture-transition-{self.run_id}-rev-{revision}",
                "previous_state": previous_state,
                "new_state": state,
                "occurred_ts_ms": timestamp - age_ms,
                "cause": reason_code,
                "cause_code": reason_code,
                "description_ko": fixture_descriptions[state],
                "actor": "AUTO_SAFETY",
                "run_id": self.run_id,
                "strategy_id": "LSA_REVERSAL_V1",
                "account_id": self.paper_portfolio.MAIN_ACCOUNT_ID,
                "symbol": "BTCUSDT",
                "request_revision": revision - 1,
                "response_revision": revision,
                "reversible": state not in {"PROTECTED", "CLOSED"},
                "trade_id": f"{self.run_id}-fixture-trade-001",
                "reason_code": reason_code,
                "sample_type": "OFFLINE_FIXTURE",
                **evidence,
            }
            self.ledger.append_transition(
                self.run_id,
                state=state,
                ts_ms=timestamp - age_ms,
                payload=transition,
            )
            self.paper_portfolio.remember_transition_audit(transition)
            previous_state = state
        self.ledger.save_snapshot(
            self.run_id,
            lifecycle_state="CLOSED",
            ts_ms=timestamp,
            payload={
                "snapshot_ts_ms": timestamp,
                "open_position": None,
                "last_exit_reason": "TAKE_PROFIT",
                "portfolio": self.paper_portfolio.recovery_state(
                    registry_settings=self.strategy_registry.rows(),
                    snapshot_ts_ms=timestamp,
                ),
            },
        )
        self.ledger.record_order(
            {
                "order_id": f"{self.run_id}-entry-order",
                "run_id": self.run_id,
                "trade_id": f"{self.run_id}-fixture-trade-001",
                "venue": Venue.FIXTURE.value,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "intent": "ENTRY_IOC",
                "status": "FILLED",
                "requested_qty": "1",
                "filled_qty": "1",
                "price_cap": "100.10",
                "average_fill_price": "100.10",
                "created_ts_ms": timestamp - 184_075,
                "arrival_ts_ms": timestamp - 184_000,
                "finalized_ts_ms": timestamp - 184_000,
                "fee_usdt": "0.06006",
                "slippage_usdt": "0.10",
                "reason_codes": ["FIXTURE_DEPTH_WALK"],
            }
        )
        self.ledger.record_fill(
            {
                "fill_id": f"{self.run_id}-entry-fill",
                "run_id": self.run_id,
                "order_id": f"{self.run_id}-entry-order",
                "side": "BUY",
                "planned_price": "100.00",
                "price": "100.10",
                "quantity": "1",
                "fee_usdt": "0.06006",
                "slippage_usdt": "0.10",
                "ts_ms": timestamp - 184_000,
            }
        )
        self.ledger.record_order(
            {
                "order_id": f"{self.run_id}-exit-order",
                "run_id": self.run_id,
                "trade_id": f"{self.run_id}-fixture-trade-001",
                "venue": Venue.FIXTURE.value,
                "symbol": "BTCUSDT",
                "side": "SELL",
                "intent": "TAKE_PROFIT",
                "status": "FILLED",
                "requested_qty": "1",
                "filled_qty": "1",
                "trigger_price": "102.00",
                "average_fill_price": "101.90",
                "created_ts_ms": timestamp - 75,
                "arrival_ts_ms": timestamp,
                "finalized_ts_ms": timestamp,
                "fee_usdt": "0.06114",
                "slippage_usdt": "0.10",
                "reason_codes": ["FIXTURE_EXECUTABLE_BID"],
            }
        )
        self.ledger.record_fill(
            {
                "fill_id": f"{self.run_id}-exit-fill",
                "run_id": self.run_id,
                "order_id": f"{self.run_id}-exit-order",
                "side": "SELL",
                "planned_price": "102.00",
                "price": "101.90",
                "quantity": "1",
                "fee_usdt": "0.06114",
                "slippage_usdt": "0.10",
                "ts_ms": timestamp,
            }
        )
        run_record = self.ledger.get_run(self.run_id)
        if run_record is None:
            raise RuntimeError(f"fixture 거래가 참조할 Run이 없습니다: {self.run_id}")
        self.ledger.record_trade(
            {
                "trade_id": f"{self.run_id}-fixture-trade-001",
                "run_id": self.run_id,
                "venue": Venue.FIXTURE.value,
                "symbol": "BTCUSDT",
                "strategy_id": "LSA_REVERSAL_V1",
                "candidate_id": fixture_candidate_id,
                "signal_event_id": fixture_signal_event_id,
                "opportunity_id": fixture_opportunity_id,
                "side": "LONG",
                "entry_ts_ms": timestamp - 184_000,
                "exit_ts_ms": timestamp,
                "entry_price": "100.10",
                "exit_price": "101.90",
                "initial_stop": "99.55",
                "take_profit": "102.00",
                "quantity": "1",
                "exit_reason": "TAKE_PROFIT",
                "gross_pnl_usdt": "1.80",
                "fees_usdt": "0.1212",
                "slippage_usdt": "0.20",
                "net_pnl_usdt": "1.4788",
                "mae_r": -0.22,
                "mfe_r": 1.41,
                "holding_ms": 184_000,
                "flags": ["OFFLINE_FIXTURE"],
                "sample_type": "DEMO_FIXTURE",
                "config_hash": str(run_record["config_hash"]),
                "strategy_version": "1",
                "regime": "RANGE",
                "profile": "BASE",
            }
        )

    def _switch_venue_run(self, venue: Venue) -> None:
        previous_run_id = self.run_id
        self.flush_storage()
        self.archived_run_ids.append(previous_run_id)
        if self.ledger is not None:
            self.ledger.finalize_run(
                previous_run_id,
                finalized_ts_ms=self.clock.utc_ms(),
                summary={"reason": "PUBLIC_VENUE_FAILOVER", "preserved": True},
            )
        self._archive_superseded_open_runs("SUPERSEDED_BY_VENUE_FAILOVER")
        self.run_id = f"run-{uuid4().hex[:12]}"
        self.venue = venue
        self._events.clear()
        self._reset_research_state()
        self.wide_symbol_count = 0
        self.deep_symbol_count = 0
        if self.ledger is not None:
            self._start_ledger_run()
            self._persist_paper_entry_intent(updated_ts_ms=self.clock.utc_ms())

    def _record_public_failure(self, venue: Venue, error: PublicDataUnavailable) -> None:
        flag = f"PUBLIC_DATA_BOOTSTRAP_FAILED_{venue.value}"
        self.runtime_health_flags.append(flag)
        self._log("MARKET_DATA", f"{flag} · LIVE 전환 차단")
        if self.ledger is not None:
            self.ledger.record_incident(
                f"public-failure-{self.run_id}-{venue.value}-{uuid4().hex[:8]}",
                run_id=self.run_id,
                severity="WARN",
                category="PUBLIC_DATA_BOOTSTRAP",
                ts_ms=self.clock.utc_ms(),
                payload={"venue": venue.value, "error_type": type(error).__name__},
            )

    def _log(self, category: str, message: str) -> None:
        self.control_logs.append(
            {
                "ts_ms": self.clock.utc_ms(),
                "category": category,
                "level": "INFO",
                "message": message,
            }
        )


def _optional_int(value: object | None) -> int | None:
    return None if value is None else int(str(value))
