"""FastAPI 앱을 구성하고 빌드된 로컬 대시보드를 제공한다."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from anyio import to_process
from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.app.analytics.opportunities import OpportunityAccountGroup, group_trade_opportunities
from backend.app.clocks import SystemClock
from backend.app.control import (
    ControlAction,
    ControlOperationConflict,
    ControlOperationFailure,
    ControlOperationManager,
    ControlRevisionConflict,
)
from backend.app.control.operations import ControlRunner, ProgressCallback
from backend.app.domain.models import MarketDataState, RuntimeMode, Venue
from backend.app.market_explorer import MarketExplorerService
from backend.app.replay.operations import (
    ReplayOperationConflict,
    ReplayOperationFailure,
    ReplayOperationManager,
)
from backend.app.replay.process import (
    replay_focus_from_paths,
    replay_preview_from_paths,
    replay_stored_run_in_subprocess,
    replay_timeline_from_paths,
)
from backend.app.replay.safety import ReplayLiveSafetyViolation, run_with_live_safety
from backend.app.research.source_metadata import research_source_metadata_rows
from backend.app.runtime import PaperEntryIntentConflict, PaperRuntime
from backend.app.storage.parquet import ParquetEventStore
from backend.app.storage.sqlite import LedgerInvariantError, RecoveryState, SQLiteLedger
from backend.app.strategies.family import (
    StrategyFamilyId,
    family_detail,
    strategy_family_catalog,
)
from backend.app.strategies.orderflow_confirmation import (
    ORDERFLOW_CONFIRMATION_FILTER_ID,
    OrderflowFilterRevisionConflict,
)
from backend.app.strategies.registry import (
    StrategyLifecycle,
    StrategyMode,
    StrategyRevisionConflict,
)
from backend.app.ui_v6 import (
    compact_mutation_summary,
    compact_selected_family_detail,
    compact_ui_summary,
    diagnostics_rows,
    paper_safety_contract,
    settings_summary,
    stable_etag,
    strategy_page_summary,
    ui_delta_messages,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

_TERMINAL_OPERATION_STATES = {
    "COMPLETED",
    "FAILED_RETRYABLE",
    "FAILED_BLOCKED",
    "CANCELLED",
}

_DASHBOARD_REFRESH_SECONDS = 1.0

_ORDERFLOW_RESEARCH_SOURCE_IDS = (
    "SRC-OFI-2010",
    "SRC-QI-2015",
    "SRC-MLOFI-2019",
    "SRC-MICROPRICE-2017",
    "SRC-BINANCE-DEPTH",
    "SRC-BINANCE-AGGTRADE",
)

_FAMILY_AVAILABILITY = {
    StrategyFamilyId.POSITIONING_LIQUIDATION.value: {
        "availability_state": "RESEARCH_PREPARATION",
        "availability_label_ko": "연구 준비",
        "availability_reason_ko": "공개 OI·funding·basis 입력의 시간 정렬 검증이 필요합니다.",
    },
    StrategyFamilyId.MARKET_REGIME_FILTERS.value: {
        "availability_state": "ROUTER_ONLY",
        "availability_label_ko": "라우터 전용",
        "availability_reason_ko": "독립 수익전략이 아니라 current entry의 시장상태 라우팅용입니다.",
    },
    StrategyFamilyId.SESSION_PROFILE.value: {
        "availability_state": "RESEARCH_PREPARATION",
        "availability_label_ko": "연구 준비",
        "availability_reason_ko": "세션·Volume Profile 실측 계약을 준비하고 있습니다.",
    },
    StrategyFamilyId.MARKET_NEUTRAL.value: {
        "availability_state": "ENGINE_VALIDATION_REQUIRED",
        "availability_label_ko": "엔진 검증 필요",
        "availability_reason_ko": (
            "다중 leg PAPER 체결·복구 엔진이 검증되기 전에는 실행하지 않습니다."
        ),
    },
}


def _operation_transition_audit(
    operation: Mapping[str, object],
    *,
    transition_id: str,
    run_id: str | None,
    symbol: str | None,
) -> dict[str, object]:
    """실행·replay 작업 스냅샷을 한 행의 불변 상태 전환 계약으로 정규화한다."""

    raw_history = operation.get("history")
    history = (
        [row for row in raw_history if isinstance(row, Mapping)]
        if isinstance(raw_history, list)
        else []
    )
    current = history[-1] if history else {}
    previous = history[-2] if len(history) >= 2 else {}
    response_revision = int(str(operation.get("revision", 0)))
    request_revision = int(str(previous.get("revision", max(0, response_revision - 1))))
    new_state = str(operation.get("state", current.get("state", "UNKNOWN")))
    cause_code = str(
        operation.get("error_code")
        or operation.get("reason")
        or "OPERATION_STATE_CHANGE"
    )
    description_ko = str(
        operation.get("stage_ko")
        or current.get("stage_ko")
        or "작업 상태가 변경되었습니다."
    )
    return {
        **dict(operation),
        "transition_id": transition_id,
        "previous_state": str(previous.get("state", "NONE")),
        "new_state": new_state,
        "occurred_ts_ms": int(str(operation.get("updated_ts_ms", 0))),
        "cause": cause_code,
        "cause_code": cause_code,
        "description_ko": description_ko,
        "actor": str(operation.get("actor", "RECOVERY")),
        "run_id": run_id,
        "strategy_id": None,
        "account_id": None,
        "symbol": symbol,
        "request_revision": request_revision,
        "response_revision": response_revision,
        "reversible": new_state not in _TERMINAL_OPERATION_STATES,
    }


def _restart_recovery_transition_audit(
    *,
    transition_id: str,
    occurred_ts_ms: int,
    requested_mode: RuntimeMode,
    recovered: RecoveryState | None,
    latest_open_run: Mapping[str, object] | None,
    resumed_run_id: str | None,
    recovery_error: LedgerInvariantError | None,
    recovery_ok: bool,
    runtime: PaperRuntime,
) -> dict[str, object]:
    """프로세스 시작 복구 결과를 하나의 불변 상태 전이 계약으로 정규화한다."""

    audit_run_id = (
        recovered.run_id
        if recovered is not None
        else str(latest_open_run["run_id"])
        if latest_open_run is not None
        else None
    )
    previous_state = (
        recovered.lifecycle_state if recovered is not None else "OPEN_RUN_UNVERIFIED"
    )
    applied = recovered is not None and resumed_run_id is not None and recovery_ok
    if recovery_error is not None:
        new_state = "RECOVERY_FAIL_CLOSED"
        cause_code = "RECOVERY_CHECKSUM_OR_SCHEMA_INVALID"
        description_ko = (
            "저장된 PAPER 상태의 checksum 또는 schema 검증에 실패해 신규 진입을 잠갔습니다."
        )
    elif recovered is not None and resumed_run_id is None:
        new_state = "RECOVERY_DEFERRED"
        cause_code = (
            "RECOVERY_DEFERRED_READY_MODE"
            if requested_mode is RuntimeMode.READY
            else "RECOVERY_RUN_MODE_MISMATCH"
        )
        description_ko = (
            "열린 PAPER Run을 찾았지만 현재 시작 모드와 달라 상태를 적용하지 않았습니다."
        )
    elif not recovery_ok:
        new_state = "RECOVERY_FAIL_CLOSED"
        cause_code = next(
            (
                flag
                for flag in runtime.runtime_health_flags
                if flag.startswith("RECOVERY_") and flag != "RECOVERY_FAIL_CLOSED"
            ),
            "RECOVERY_STATE_REJECTED",
        )
        description_ko = (
            "저장된 PAPER 상태를 안전하게 적용하지 못해 신규 진입을 잠갔습니다."
        )
    elif requested_mode is RuntimeMode.DEMO_FIXTURE:
        new_state = "FIXTURE_STATE_RECOVERED"
        cause_code = "PAPER_FIXTURE_STATE_RECOVERED"
        description_ko = (
            "저장된 오프라인 샘플 PAPER 상태를 복구했습니다. LIVE 시장데이터가 아닙니다."
        )
    else:
        new_state = "RECOVERY_REVALIDATION_LOCKED"
        cause_code = "PAPER_STATE_RECOVERED"
        description_ko = (
            "저장된 PAPER 상태를 복구했고 새 공개호가 확인 전까지 신규 진입을 잠갔습니다."
        )
    return {
        "transition_id": transition_id,
        "previous_state": previous_state,
        "new_state": new_state,
        "occurred_ts_ms": occurred_ts_ms,
        "cause": cause_code,
        "cause_code": cause_code,
        "description_ko": description_ko,
        "actor": "RECOVERY",
        "run_id": audit_run_id,
        "strategy_id": None,
        "account_id": None,
        "symbol": None,
        "request_revision": 0,
        "response_revision": 1,
        "reversible": new_state != "RECOVERY_FAIL_CLOSED",
        "lifecycle_state": previous_state,
        "recovery_ok": applied,
        "ignored_fail_closed_governance_setting_count": len(
            runtime._recovery_ignored_governance_row_tokens
        ),
        "ignored_fail_closed_governance_setting_tokens": list(
            runtime._recovery_ignored_governance_row_tokens
        ),
        "ignored_fail_closed_governance_data_deleted": False,
        "ignored_fail_closed_governance_duplicate_revision_relaxed": False,
        "ignored_fail_closed_governance_revision_reservations": [
            dict(row) for row in runtime._recovery_reserved_governance_revisions
        ],
        "open_position": runtime.paper_portfolio.main.position is not None,
        "requested_mode": requested_mode.value,
    }


async def _await_shutdown_task(task: asyncio.Task[None]) -> None:
    """종료 신호가 겹쳐도 이미 시작한 안전 저장 작업은 끝까지 기다린다."""

    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)


class ChartSelectionRequest(BaseModel):
    """사용자가 볼 공개 종목과 로컬 캔들 시간구간을 제한한다."""

    symbol: str = Field(min_length=3, max_length=30, pattern=r"^[\w-]+$")
    interval_seconds: int


class MarketSelectionRequest(BaseModel):
    """공개 시장 보기 선택만 바꾸고 거래 action을 만들지 않는다."""

    source: str = Field(pattern=r"^(BINANCE_USDM|UPBIT_KRW)$")
    symbol: str = Field(min_length=3, max_length=30, pattern=r"^[\w-]+$")
    interval_seconds: int = 180
    pin_for_analysis: bool = False


class StrategyConfigurationRequest(BaseModel):
    """전략의 main·shadow 참여와 양방향 허용을 명시적으로 변경한다."""

    mode: StrategyMode
    long_enabled: bool
    short_enabled: bool
    expected_revision: int = Field(ge=0)
    manual_lock: bool = True
    reason: str = Field(default="USER_CONFIGURATION", min_length=3, max_length=120)
    lifecycle: StrategyLifecycle | None = None


class StrategyRollbackRequest(BaseModel):
    """과거 전략 설정을 기록을 지우지 않고 새 revision으로 복원한다."""

    target_revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="USER_ROLLBACK", min_length=3, max_length=120)


class FamilyResearchEnabledRequest(BaseModel):
    """Family current variant의 신규 PAPER 평가 의도를 CAS로 변경한다."""

    research_enabled: bool
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="USER_FAMILY_RESEARCH_CONFIGURATION", min_length=3, max_length=120)


class ControlMutationRequest(BaseModel):
    """두 탭과 재전송이 같은 PAPER 제어 의도를 중복 실행하지 않게 한다."""

    expected_revision: int | None = Field(default=None, ge=0)
    reason: str = Field(default="USER_REQUEST", min_length=3, max_length=120)


class PaperEntryIntentRequest(BaseModel):
    """사용자 일시정지 의도를 자동 안전잠금과 별도 revision으로 변경한다."""

    expected_revision: int | None = Field(default=None, ge=0)
    reason: str = Field(default="USER_REQUEST", min_length=3, max_length=120)


class ReplayRequest(BaseModel):
    """저장 Run 전체 또는 특정 종목을 같은 PAPER 파이프라인으로 재처리한다."""

    symbol: str | None = Field(
        default=None,
        min_length=3,
        max_length=30,
        pattern=r"^[A-Za-z0-9]+$",
    )
    event_limit: int | None = Field(default=None, ge=1)


def _local_browser_origin(origin: str | None) -> bool:
    if origin is None:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def _runtime_from_environment() -> PaperRuntime:
    startup_started = time.monotonic()
    requested_mode_value = os.environ.get("ROBOM_MODE", RuntimeMode.READY.value)
    requested_runtime_mode = RuntimeMode(requested_mode_value)
    mode = requested_runtime_mode
    default_database = PROJECT_ROOT / "data" / "run-ledger.sqlite3"
    database = Path(os.environ.get("ROBOM_DB_PATH", str(default_database)))
    archive_path = os.environ.get("ROBOM_MARKET_ARCHIVE_PATH")
    storage_started = time.monotonic()
    market_event_archive = (
        ParquetEventStore(
            Path(archive_path),
            minimum_free_bytes=int(os.environ.get("ROBOM_MIN_FREE_BYTES", str(2 * 1024**3))),
            minimum_free_ratio=float(os.environ.get("ROBOM_MIN_FREE_RATIO", "0.05")),
        )
        if archive_path
        else None
    )
    storage_init_ms = (time.monotonic() - storage_started) * 1_000
    ledger_started = time.monotonic()
    ledger = SQLiteLedger(database, market_event_archive=market_event_archive)
    ledger_open_ms = (time.monotonic() - ledger_started) * 1_000
    clock = SystemClock()
    latest_open_run = ledger.latest_open_run()
    recovery_error: LedgerInvariantError | None = None
    recovery_lookup_started = time.monotonic()
    try:
        recovered = ledger.recover_latest(recovered_ts_ms=clock.utc_ms())
    except LedgerInvariantError as error:
        recovered = None
        recovery_error = error
        mode = RuntimeMode.READY
    recovery_lookup_ms = (time.monotonic() - recovery_lookup_started) * 1_000
    run_id = None
    run_venue = Venue.NONE if mode is RuntimeMode.READY else Venue.FIXTURE
    if recovered is not None and mode is not RuntimeMode.READY:
        run = ledger.get_run(recovered.run_id)
        if run is not None and run["mode"] == mode.value:
            run_id = recovered.run_id
            run_venue = Venue(str(run["venue"]))
    runtime_init_started = time.monotonic()
    runtime = PaperRuntime(
        mode=mode,
        clock=clock,
        run_id=run_id or ("ready" if mode is RuntimeMode.READY else f"run-{uuid4().hex[:12]}"),
        ledger=ledger,
        storage_guard=market_event_archive
        or ParquetEventStore(
            database.parent / "market-parquet",
            minimum_free_bytes=int(os.environ.get("ROBOM_MIN_FREE_BYTES", str(2 * 1024**3))),
            minimum_free_ratio=float(os.environ.get("ROBOM_MIN_FREE_RATIO", "0.05")),
        ),
        market_event_archive=market_event_archive,
        venue=run_venue,
    )
    runtime_init_ms = (time.monotonic() - runtime_init_started) * 1_000
    recovery_ok = True
    recovery_restore_started = time.monotonic()
    if recovery_error is not None:
        runtime._lock_recovery("RECOVERY_CHECKSUM_OR_SCHEMA_INVALID")
        recovery_ok = False
    elif recovered is not None and run_id is not None:
        recovery_ok = runtime.restore_recovery_state(recovered)
    audit_run_id = (
        recovered.run_id
        if recovered is not None
        else str(latest_open_run["run_id"])
        if latest_open_run is not None
        else None
    )
    if audit_run_id is not None:
        occurred_ts_ms = runtime.clock.utc_ms()
        transition_id = f"recovery-{audit_run_id}-{uuid4().hex}"
        recovery_audit = _restart_recovery_transition_audit(
            transition_id=transition_id,
            occurred_ts_ms=occurred_ts_ms,
            requested_mode=requested_runtime_mode,
            recovered=recovered,
            latest_open_run=latest_open_run,
            resumed_run_id=run_id,
            recovery_error=recovery_error,
            recovery_ok=recovery_ok,
            runtime=runtime,
        )
        runtime.startup_recovery_audit = recovery_audit
        ledger.record_incident(
            transition_id,
            run_id=audit_run_id,
            severity=(
                "ERROR"
                if recovery_audit["new_state"] == "RECOVERY_FAIL_CLOSED"
                else "INFO"
            ),
            category="PAPER_RESTART_RECOVERY",
            ts_ms=occurred_ts_ms,
            payload=recovery_audit,
        )
    if runtime.mode is RuntimeMode.DEMO_FIXTURE and recovery_ok:
        runtime.boot_demo()
        runtime.paused = False
        runtime.position_visible = True
        runtime.market_data_state = MarketDataState.FIXTURE
        runtime.runtime_health_flags = [
            "OFFLINE_DEMO_ISOLATED",
            "PAPER_STATE_RECOVERED",
        ]
    runtime.startup_storage_init_ms = storage_init_ms
    runtime.startup_ledger_open_ms = ledger_open_ms
    runtime.startup_recovery_lookup_ms = recovery_lookup_ms
    runtime.startup_runtime_init_ms = runtime_init_ms
    runtime.startup_recovery_restore_ms = (time.monotonic() - recovery_restore_started) * 1_000
    runtime.startup_total_ms = (time.monotonic() - startup_started) * 1_000
    return runtime


def create_app(
    runtime: PaperRuntime | None = None,
    *,
    control_runners: Mapping[ControlAction, ControlRunner] | None = None,
    market_explorer: MarketExplorerService | None = None,
) -> FastAPI:
    active_runtime = runtime or _runtime_from_environment()
    active_market_explorer = market_explorer or MarketExplorerService()

    def audit_control_transition(operation: dict[str, object]) -> None:
        ledger = active_runtime.ledger
        if ledger is None:
            return
        run_id = (
            active_runtime.run_id if ledger.get_run(active_runtime.run_id) is not None else None
        )
        transition_id = f"{operation['operation_id']}-rev-{operation['revision']}"
        ledger.record_incident(
            transition_id,
            run_id=run_id,
            severity="INFO",
            category="CONTROL_STATE_TRANSITION",
            ts_ms=int(str(operation["updated_ts_ms"])),
            payload=_operation_transition_audit(
                operation,
                transition_id=transition_id,
                run_id=run_id,
                symbol=None,
            ),
        )

    operation_manager = ControlOperationManager(
        active_runtime.clock.utc_ms,
        audit=audit_control_transition,
    )

    def audit_replay_transition(operation: dict[str, object]) -> None:
        ledger = active_runtime.ledger
        if ledger is None:
            return
        source_run_id = str(operation["source_run_id"])
        run_id = source_run_id if ledger.get_run(source_run_id) is not None else None
        transition_id = f"{operation['operation_id']}-rev-{operation['revision']}"
        ledger.record_incident(
            transition_id,
            run_id=run_id,
            severity="INFO",
            category="REPLAY_STATE_TRANSITION",
            ts_ms=int(str(operation["updated_ts_ms"])),
            payload=_operation_transition_audit(
                operation,
                transition_id=transition_id,
                run_id=run_id,
                symbol=(str(operation["symbol"]) if operation.get("symbol") else None),
            ),
        )

    replay_operation_manager = ReplayOperationManager(
        active_runtime.clock.utc_ms,
        audit=audit_replay_transition,
    )
    replay_process_lock = asyncio.Lock()
    replay_preview_lock = asyncio.Lock()
    replay_preview_cache: dict[tuple[str, str | None, int], tuple[float, dict[str, object]]] = {}
    replay_focus_cache: dict[tuple[str, str, str], tuple[float, dict[str, object]]] = {}
    replay_results_cache_lock = asyncio.Lock()
    replay_results_cache: list[dict[str, object]] | None = None

    def compact_replay_result(result: Mapping[str, object]) -> dict[str, object]:
        """목록 화면이 사용하는 최근 결정 20개만 전송하고 원장 원본은 보존한다."""

        compact = dict(result)
        decision_path = result.get("decision_path")
        if isinstance(decision_path, list):
            compact["decision_path"] = decision_path[-20:]
        return compact

    def remember_replay_result(result: dict[str, object]) -> None:
        """완료 결과를 기존 캐시에 중복 없이 추가해 다음 화면 조회를 즉시 처리한다."""

        nonlocal replay_results_cache
        if replay_results_cache is None:
            return
        replay_id = str(result["replay_id"])
        replay_results_cache = [
            *(row for row in replay_results_cache if str(row.get("replay_id")) != replay_id),
            compact_replay_result(result),
        ]

    def ensure_replay_process_available() -> None:
        """긴 replay 중 다른 화면 조회를 대기열에 묶지 않고 즉시 안내한다."""

        active_operation = replay_operation_manager.active_public()
        if replay_process_lock.locked() or active_operation is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "REPLAY_BUSY",
                    "error_message_ko": (
                        "다른 저장 기록을 검증하고 있습니다. "
                        "진행 상태를 확인하거나 취소한 뒤 다시 시도하세요."
                    ),
                    "retryable": True,
                    "operation": active_operation,
                },
            )

    websocket_clients: set[WebSocket] = set()
    dashboard_build_lock = asyncio.Lock()
    dashboard_snapshot_cache: dict[str, object] | None = None
    dashboard_websocket_message_cache: str | None = None
    dashboard_http_message_cache: str | None = None
    dashboard_cache_identity: tuple[object, ...] | None = None
    dashboard_cache_created_monotonic = 0.0
    dashboard_build_count = 0
    dashboard_build_last_ms = 0.0
    dashboard_build_max_ms = 0.0
    dashboard_build_last_completed_ts_ms = 0
    dashboard_build_max_ts_ms = 0
    dashboard_serialize_count = 0
    dashboard_serialize_last_ms = 0.0
    dashboard_serialize_max_ms = 0.0
    dashboard_serialize_last_completed_ts_ms = 0
    dashboard_serialize_max_ts_ms = 0

    def dashboard_snapshot() -> dict[str, object]:
        nonlocal dashboard_build_count
        nonlocal dashboard_build_last_ms
        nonlocal dashboard_build_max_ms
        nonlocal dashboard_build_last_completed_ts_ms
        nonlocal dashboard_build_max_ts_ms
        started = time.monotonic()
        snapshot = {
            **active_runtime.dashboard(),
            "control_operation": operation_manager.current_public(),
            "control_revision": operation_manager.revision,
        }
        elapsed_ms = (time.monotonic() - started) * 1_000
        completed_ts_ms = active_runtime.clock.utc_ms()
        dashboard_build_count += 1
        dashboard_build_last_ms = elapsed_ms
        dashboard_build_last_completed_ts_ms = completed_ts_ms
        if elapsed_ms > dashboard_build_max_ms:
            dashboard_build_max_ms = elapsed_ms
            dashboard_build_max_ts_ms = completed_ts_ms
        system = snapshot.get("system")
        if isinstance(system, dict):
            system.update(
                {
                    "dashboard_refresh_seconds": _DASHBOARD_REFRESH_SECONDS,
                    "dashboard_build_count": dashboard_build_count,
                    "dashboard_build_last_ms": round(dashboard_build_last_ms, 3),
                    "dashboard_build_max_ms": round(dashboard_build_max_ms, 3),
                    "dashboard_build_last_completed_ts_ms": (
                        dashboard_build_last_completed_ts_ms
                    ),
                    "dashboard_build_max_ts_ms": dashboard_build_max_ts_ms,
                    "dashboard_serialize_count": dashboard_serialize_count,
                    "dashboard_serialize_last_ms": round(dashboard_serialize_last_ms, 3),
                    "dashboard_serialize_max_ms": round(dashboard_serialize_max_ms, 3),
                    "dashboard_serialize_last_completed_ts_ms": (
                        dashboard_serialize_last_completed_ts_ms
                    ),
                    "dashboard_serialize_max_ts_ms": dashboard_serialize_max_ts_ms,
                }
            )
        return snapshot

    async def cached_dashboard(
        *,
        force: bool = False,
        serialization: str | None = None,
    ) -> tuple[dict[str, object], str | None]:
        """화면 한 주기에는 같은 전체 snapshot과 JSON을 모든 소비자가 공유한다."""

        nonlocal dashboard_snapshot_cache
        nonlocal dashboard_websocket_message_cache
        nonlocal dashboard_http_message_cache
        nonlocal dashboard_cache_identity
        nonlocal dashboard_cache_created_monotonic
        nonlocal dashboard_serialize_count
        nonlocal dashboard_serialize_last_ms
        nonlocal dashboard_serialize_max_ms
        nonlocal dashboard_serialize_last_completed_ts_ms
        nonlocal dashboard_serialize_max_ts_ms
        async with dashboard_build_lock:
            now = asyncio.get_running_loop().time()
            current_identity = (
                active_runtime.run_id,
                active_runtime.mode.value,
                active_runtime.market_data_state.value,
                active_runtime.paused,
                active_runtime.paper_entry_intent()["revision"],
                active_runtime.selected_symbol,
                active_runtime.selected_interval_seconds,
                operation_manager.revision,
            )
            expired = (
                dashboard_snapshot_cache is None
                or dashboard_cache_identity != current_identity
                or now - dashboard_cache_created_monotonic >= _DASHBOARD_REFRESH_SECONDS
            )
            if force or expired:
                dashboard_snapshot_cache = await asyncio.to_thread(dashboard_snapshot)
                dashboard_websocket_message_cache = None
                dashboard_http_message_cache = None
                dashboard_cache_created_monotonic = asyncio.get_running_loop().time()
                dashboard_cache_identity = (
                    active_runtime.run_id,
                    active_runtime.mode.value,
                    active_runtime.market_data_state.value,
                    active_runtime.paused,
                    active_runtime.paper_entry_intent()["revision"],
                    active_runtime.selected_symbol,
                    active_runtime.selected_interval_seconds,
                    operation_manager.revision,
                )
            payload = (
                dashboard_websocket_message_cache
                if serialization == "websocket"
                else dashboard_http_message_cache
                if serialization == "http"
                else None
            )
            if serialization is not None and payload is None:
                started = asyncio.get_running_loop().time()
                serialized_value: object = (
                    {"type": "dashboard", "data": dashboard_snapshot_cache}
                    if serialization == "websocket"
                    else dashboard_snapshot_cache
                )
                payload = await asyncio.to_thread(
                    json.dumps,
                    serialized_value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if serialization == "websocket":
                    dashboard_websocket_message_cache = payload
                elif serialization == "http":
                    dashboard_http_message_cache = payload
                else:
                    raise ValueError("unsupported dashboard serialization")
                elapsed_ms = (asyncio.get_running_loop().time() - started) * 1_000
                completed_ts_ms = active_runtime.clock.utc_ms()
                dashboard_serialize_count += 1
                dashboard_serialize_last_ms = elapsed_ms
                dashboard_serialize_last_completed_ts_ms = completed_ts_ms
                if elapsed_ms > dashboard_serialize_max_ms:
                    dashboard_serialize_max_ms = elapsed_ms
                    dashboard_serialize_max_ts_ms = completed_ts_ms
            if dashboard_snapshot_cache is None:
                raise RuntimeError("dashboard snapshot cache was not initialized")
            return dashboard_snapshot_cache, payload

    async def dashboard_message(*, force: bool = False) -> str:
        _, payload = await cached_dashboard(force=force, serialization="websocket")
        if payload is None:
            raise RuntimeError("dashboard message cache was not initialized")
        return payload

    async def dashboard_response(*, force: bool = False) -> Response:
        """HTTP도 worker에서 만든 JSON을 재사용해 이벤트 루프 직렬화를 피한다."""

        _, payload = await cached_dashboard(force=force, serialization="http")
        if payload is None:
            raise RuntimeError("dashboard HTTP cache was not initialized")
        return Response(
            content=payload,
            media_type="application/json",
        )

    async def compact_mutation_response(*, force: bool = False) -> Response:
        """화면 변경 응답은 전체 dashboard 대신 작은 상태 확인값만 직렬화한다."""

        snapshot, _ = await cached_dashboard(force=force)
        payload = compact_mutation_summary(snapshot)
        serialized = await asyncio.to_thread(
            json.dumps,
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return Response(content=serialized, media_type="application/json")

    def orderflow_virtual_variant() -> dict[str, object]:
        filter_status = active_runtime.orderflow_confirmation_filter_status(
            symbol=active_runtime.selected_symbol
        )
        enabled = bool(filter_status["enabled"])
        return {
            "strategy_id": ORDERFLOW_CONFIRMATION_FILTER_ID,
            "family_id": StrategyFamilyId.ORDERFLOW_CONFIRMATION.value,
            "role": "FILTER",
            "variant_id": ORDERFLOW_CONFIRMATION_FILTER_ID,
            "variant_label_ko": "주문흐름 확인 필터 V2",
            "is_current_variant": True,
            "supersedes_strategy_ids": [
                "OFI_CONTINUATION_PULLBACK_V1",
                "QUEUE_MICROPRICE_MOMENTUM_V1",
                "AGGRESSOR_FLOW_CONTINUATION_V1",
                "MULTILEVEL_MICROPRICE_MOMENTUM_V1",
                "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
                "OFI_RETURN_CONFLUENCE_V1",
                "BOOK_SLOPE_ASYMMETRY_V1",
            ],
            "superseded_by_strategy_id": None,
            "user_visible_by_default": True,
            "default_research_enabled": False,
            "final_ranking_eligible": False,
            "setting": {
                "research_enabled": enabled,
                "enabled": enabled,
                "mode": "SHADOW" if enabled else "OFF",
                "lifecycle": "RESEARCH",
                "long_enabled": False,
                "short_enabled": False,
                "settings_revision": filter_status["revision"],
                "revision": filter_status["revision"],
                "manual_lock": True,
                "change_reason": filter_status["change_reason"],
                "settings_updated_ts_ms": filter_status["updated_ts_ms"],
            },
            "performance": None,
            "governance": {
                "evidence_status": filter_status["uplift_status"],
                "final_ranking_eligible": False,
            },
            "runtime_state": filter_status,
            "research_sources": research_source_metadata_rows(
                _ORDERFLOW_RESEARCH_SOURCE_IDS
            ),
        }

    def family_payload(
        snapshot: Mapping[str, object],
        family_id: StrategyFamilyId | str,
    ) -> dict[str, object]:
        strategy_rows = snapshot.get("strategies")
        rows = (
            [row for row in strategy_rows if isinstance(row, Mapping)]
            if isinstance(strategy_rows, list)
            else []
        )
        performance = {
            str(row["strategy_id"]): row.get("performance")
            for row in rows
            if row.get("strategy_id") is not None
        }
        governance = {
            str(row["strategy_id"]): row.get("governance")
            for row in rows
            if row.get("strategy_id") is not None
        }
        detail = family_detail(
            active_runtime.strategy_registry,
            family_id,
            performance,
            governance,
        )
        source_by_id = {
            str(row["strategy_id"]): row for row in rows if row.get("strategy_id") is not None
        }
        variants = detail.get("variants")
        if isinstance(variants, list):
            enriched_variants: list[dict[str, object]] = []
            for variant in variants:
                if not isinstance(variant, Mapping):
                    continue
                strategy_id = str(variant.get("strategy_id"))
                runtime_state = source_by_id.get(strategy_id, {})
                raw_source_ids = runtime_state.get("research_source_ids")
                if isinstance(raw_source_ids, list):
                    source_ids = [str(source_id) for source_id in raw_source_ids]
                else:
                    source_ids = list(
                        active_runtime.strategy_registry.descriptor(
                            strategy_id
                        ).research_contract.research_source_ids
                    )
                enriched_variants.append(
                    dict(variant)
                    | {
                        "runtime_state": runtime_state,
                        "research_sources": research_source_metadata_rows(source_ids),
                    }
                )
            detail["variants"] = enriched_variants
        from backend.app.research.v6_candidates import v6_preregistered_variants

        resolved_family_id = StrategyFamilyId(str(family_id)).value
        detail.update(
            _FAMILY_AVAILABILITY.get(
                resolved_family_id,
                {
                    "availability_state": "RUNTIME_CURRENT",
                    "availability_label_ko": "모의평가 가능",
                    "availability_reason_ko": (
                        "현재 runtime variant의 독립 PAPER 결과를 표시합니다."
                    ),
                },
            )
        )
        if resolved_family_id == StrategyFamilyId.ORDERFLOW_CONFIRMATION.value:
            virtual_variant = orderflow_virtual_variant()
            raw_variants = detail.get("variants")
            variants = (
                [dict(row) for row in raw_variants if isinstance(row, Mapping)]
                if isinstance(raw_variants, list)
                else []
            )
            detail["current_variant_id"] = ORDERFLOW_CONFIRMATION_FILTER_ID
            detail["variants"] = [virtual_variant, *variants]
            detail["variant_count"] = len(variants) + 1
        detail["offline_challengers"] = [
            {
                "strategy_id": variant.strategy_id,
                "family_id": variant.family_id,
                "baseline_strategy_ids": list(variant.baseline_strategy_ids),
                "state": "PREREGISTERED_NOT_EXECUTED",
                "current_variant": variant.current_variant,
                "runtime_registered": variant.runtime_registered,
                "live_shadow_enabled": variant.live_shadow_enabled,
                "paper_only": variant.paper_only,
            }
            for variant in v6_preregistered_variants()
            if variant.family_id == resolved_family_id
        ]
        detail.update(paper_safety_contract(snapshot))
        return detail

    def static_family_catalog() -> list[dict[str, object]]:
        rows = strategy_family_catalog(active_runtime.strategy_registry)
        catalog: list[dict[str, object]] = []
        for row in rows:
            raw_variants = row.get("variants")
            variants = raw_variants if isinstance(raw_variants, list) else []
            if row.get("family_id") == StrategyFamilyId.ORDERFLOW_CONFIRMATION.value:
                row = dict(row) | {
                    "current_variant_id": ORDERFLOW_CONFIRMATION_FILTER_ID,
                    "variant_count": len(variants) + 1,
                }
                variants = [orderflow_virtual_variant(), *variants]
            family_id = str(row.get("family_id"))
            availability = _FAMILY_AVAILABILITY.get(
                family_id,
                {
                    "availability_state": "RUNTIME_CURRENT",
                    "availability_label_ko": "모의평가 가능",
                    "availability_reason_ko": (
                        "현재 runtime variant의 독립 PAPER 결과를 표시합니다."
                    ),
                },
            )
            catalog.append(
                {key: value for key, value in row.items() if key != "variants"}
                | availability
                | {
                    "variants": [
                        {
                            key: value
                            for key, value in variant.items()
                            if key
                            not in {
                                "setting",
                                "performance",
                                "governance",
                                "runtime_state",
                            }
                        }
                        for variant in variants
                        if isinstance(variant, Mapping)
                    ]
                }
            )
        return catalog

    def history_with_trade_fill_evidence(
        history: Mapping[str, object],
    ) -> dict[str, object]:
        """`/api/trades` 행에만 strict main fill 증거와 shadow 부재 상태를 붙인다."""

        raw_rows = history.get("rows")
        rows = (
            [dict(row) for row in raw_rows if isinstance(row, Mapping)]
            if isinstance(raw_rows, list)
            else []
        )
        main_keys = [
            (str(row.get("run_id", "")), str(row.get("trade_id", "")))
            for row in rows
            if str(row.get("account_scope", "MAIN")).upper() == "MAIN"
        ]
        main_evidence = (
            active_runtime.ledger.list_trade_fill_evidence(main_keys)
            if active_runtime.ledger is not None
            else {}
        )
        enriched: list[dict[str, object]] = []
        for row in rows:
            if str(row.get("account_scope", "MAIN")).upper() == "LEAGUE":
                fill_evidence: dict[str, object] = {
                    "fill_evidence_state": "SHADOW_UNAVAILABLE",
                    "fill_evidence_reason_ko": (
                        "전략별 가상계좌 기록은 집계 결과만 보존되어 원시 fill이 제공되지 않습니다."
                    ),
                    "fills": [],
                }
            else:
                key = (str(row.get("run_id", "")), str(row.get("trade_id", "")))
                fill_evidence = main_evidence.get(
                    key,
                    {
                        "fill_evidence_state": "CURRENT_MAIN_NO_FILL",
                        "fill_evidence_reason_ko": (
                            "현재 공동 가상계좌 거래의 원시 체결을 확인하지 못했습니다."
                        ),
                        "fills": [],
                    },
                )
            enriched.append(row | fill_evidence)
        return dict(history) | {"rows": enriched}

    def grouped_trade_payload(
        history: Mapping[str, object],
        *,
        limit: int,
    ) -> dict[str, object]:
        """거래 원장을 실제 시장 기회와 BASE·STRESS 결과로 분리한다."""

        raw_rows = history.get("rows")
        rows = (
            [dict(row) for row in raw_rows if isinstance(row, Mapping)]
            if isinstance(raw_rows, list)
            else []
        )
        normalized_rows = [
            dict(row) | {"strategy_id": row.get("strategy_id", row.get("strategy"))}
            for row in rows
        ]
        grouping = group_trade_opportunities(normalized_rows)
        family_labels = {
            str(row["family_id"]): str(row["label_ko"])
            for row in strategy_family_catalog(active_runtime.strategy_registry)
        }
        all_opportunities: list[dict[str, object]] = []
        for group in grouping.groups:
            ordered_rows = sorted(
                (dict(row) for row in group.rows),
                key=lambda row: (
                    0 if str(row.get("account_scope", "MAIN")).upper() == "MAIN" else 1,
                    str(row.get("account_id", "SHARED_PAPER")),
                    0 if str(row.get("profile", "BASE")).upper() == "BASE" else 1,
                    -int(str(row.get("exit_ts_ms", 0))),
                    str(row.get("trade_id", "")),
                ),
            )
            descriptor = (
                active_runtime.strategy_registry.descriptor(group.key.strategy_id)
                if group.key.strategy_id in active_runtime.strategy_registry.strategy_ids
                else None
            )
            account_payloads: list[dict[str, object]] = []
            for account in group.accounts:
                account_rows = sorted(
                    (dict(row) for row in account.rows),
                    key=lambda row: (
                        0 if str(row.get("profile", "BASE")).upper() == "BASE" else 1,
                        -int(str(row.get("exit_ts_ms", 0))),
                        str(row.get("trade_id", "")),
                    ),
                )
                account_profiles: dict[str, object] = {}
                for profile in ("BASE", "STRESS"):
                    results = [
                        row
                        for row in account_rows
                        if str(row.get("profile", "BASE")).upper() == profile
                    ]
                    if not results:
                        continue
                    shaped_results: object = results[0] if len(results) == 1 else results
                    account_profiles[profile] = shaped_results
                account_payloads.append(
                    {
                        "account_scope": account.account_scope,
                        "account_id": account.account_id,
                        "profiles": account_profiles,
                        "rows": account_rows,
                        "raw_result_row_count": account.raw_result_row_count,
                        "base_result_row_count": account.base_result_row_count,
                        "stress_result_row_count": account.stress_result_row_count,
                        "partial_exit_row_count": account.partial_exit_row_count,
                    }
                )
            account_sets: list[
                tuple[str, str, tuple[OpportunityAccountGroup, ...]]
            ] = []
            for account in group.accounts:
                if account.account_scope == "MAIN":
                    account_sets.append(("MAIN", account.account_id, (account,)))
            league_accounts = tuple(
                account for account in group.accounts if account.account_scope == "LEAGUE"
            )
            if league_accounts:
                account_sets.append(("LEAGUE", group.key.strategy_id, league_accounts))
            account_group_payloads: list[dict[str, object]] = []
            display_profiles: dict[str, object] = {}
            profile_account_refs: dict[str, dict[str, str]] = {}
            for account_scope, account_group_id, accounts in account_sets:
                account_group_rows = sorted(
                    (dict(row) for account in accounts for row in account.rows),
                    key=lambda row: (
                        0 if str(row.get("profile", "BASE")).upper() == "BASE" else 1,
                        -int(str(row.get("exit_ts_ms", 0))),
                        str(row.get("trade_id", "")),
                    ),
                )
                account_group_profiles: dict[str, object] = {}
                account_group_refs: dict[str, dict[str, str]] = {}
                for profile in ("BASE", "STRESS"):
                    profile_accounts = tuple(
                        account for account in accounts if profile in account.profiles
                    )
                    if not profile_accounts:
                        continue
                    source_account = profile_accounts[0]
                    results = [
                        dict(row)
                        for row in source_account.rows
                        if str(row.get("profile", "BASE")).upper() == profile
                    ]
                    account_group_profiles[profile] = (
                        results[0] if len(results) == 1 else results
                    )
                    account_group_refs[profile] = {
                        "account_scope": source_account.account_scope,
                        "account_id": source_account.account_id,
                    }
                if not account_group_payloads:
                    display_profiles = dict(account_group_profiles)
                    profile_account_refs = dict(account_group_refs)
                account_group_payloads.append(
                    {
                        "account_scope": account_scope,
                        "account_group_id": account_group_id,
                        "account_ids": [account.account_id for account in accounts],
                        "profiles": account_group_profiles,
                        "profile_account_refs": account_group_refs,
                        "rows": account_group_rows,
                        "raw_result_row_count": sum(
                            account.raw_result_row_count for account in accounts
                        ),
                        "base_result_row_count": sum(
                            account.base_result_row_count for account in accounts
                        ),
                        "stress_result_row_count": sum(
                            account.stress_result_row_count for account in accounts
                        ),
                        "partial_exit_row_count": sum(
                            account.partial_exit_row_count for account in accounts
                        ),
                    }
                )
            all_opportunities.append(
                {
                    "key": {
                        "run_id": group.key.run_id,
                        "strategy_id": group.key.strategy_id,
                        "strategy_version": group.key.strategy_version,
                        "opportunity_id": group.key.opportunity_id,
                        "symbol": group.key.symbol,
                        "side": group.key.side,
                    },
                    "family_id": descriptor.family_id.value if descriptor is not None else None,
                    "family_label_ko": (
                        family_labels.get(descriptor.family_id.value)
                        if descriptor is not None
                        else None
                    ),
                    "variant_label_ko": (
                        descriptor.variant_label_ko if descriptor is not None else None
                    ),
                    "entry_ts_ms": min(int(str(row.get("entry_ts_ms", 0))) for row in ordered_rows),
                    "exit_ts_ms": max(int(str(row.get("exit_ts_ms", 0))) for row in ordered_rows),
                    "profiles": display_profiles,
                    "profile_account_refs": profile_account_refs,
                    "accounts": account_payloads,
                    "account_groups": account_group_payloads,
                    "rows": ordered_rows,
                    "raw_result_row_count": group.raw_result_row_count,
                    "base_result_row_count": group.base_result_row_count,
                    "stress_result_row_count": group.stress_result_row_count,
                    "partial_exit_row_count": group.partial_exit_row_count,
                    "replay_available": any(
                        bool(row.get("replay_available")) for row in ordered_rows
                    ),
                }
            )
        all_opportunities.sort(
            key=lambda row: (int(str(row["exit_ts_ms"])), str(row["key"])),
            reverse=True,
        )
        opportunities = all_opportunities[:limit]
        raw_scope = history.get("scope")
        scope = dict(raw_scope) if isinstance(raw_scope, Mapping) else {}
        source_raw_limit = int(str(scope.get("limit") or 0))
        source_returned_count = int(str(scope.get("returned_count") or len(rows)))
        source_limit_boundary = (
            source_raw_limit > 0 and source_returned_count >= source_raw_limit
        )
        scope.update(
            {
                "limit": limit,
                "returned_count": len(opportunities),
                "source_raw_limit": source_raw_limit or None,
                "source_grouping_complete": not source_limit_boundary,
            }
        )
        return {
            "schema_version": 1,
            "opportunities": opportunities,
            "unresolved": [
                {
                    "status": unresolved.status,
                    "reason_code": unresolved.reason_code,
                    "reason_ko": unresolved.reason_ko,
                    "row": dict(unresolved.row),
                }
                for unresolved in grouping.unresolved_rows
            ],
            "grouping_status": (
                "NOT_PROVEN"
                if grouping.unresolved_result_row_count or source_limit_boundary
                else "PROVEN"
            ),
            "source_status": (
                "NOT_PROVEN_RAW_LIMIT_BOUNDARY" if source_limit_boundary else "COMPLETE"
            ),
            "counts": {
                "unique_opportunities": grouping.unique_opportunity_count,
                "returned_opportunities": len(opportunities),
                "raw_result_rows": grouping.raw_result_row_count,
                "base_result_rows": grouping.base_result_row_count,
                "stress_result_rows": grouping.stress_result_row_count,
                "unresolved_result_rows": grouping.unresolved_result_row_count,
                "source_raw_result_rows": len(rows),
            },
            "scope": scope,
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }

    async def broadcast_dashboard() -> None:
        """연결 수와 무관하게 snapshot을 한 번만 생성·직렬화한다."""

        while True:
            if websocket_clients:
                payload = await dashboard_message()
                clients = tuple(websocket_clients)
                results = await asyncio.gather(
                    *(client.send_text(payload) for client in clients),
                    return_exceptions=True,
                )
                for client, result in zip(clients, results, strict=True):
                    if isinstance(result, Exception):
                        websocket_clients.discard(client)
            await asyncio.sleep(_DASHBOARD_REFRESH_SECONDS)

    async def maintain_strategy_candle_history() -> None:
        """새 deep 종목에 인증 없는 15분·30분·1시간 완성봉을 보충한다."""

        while True:
            selection = active_runtime.live_selection
            symbols = selection.deep_symbols if selection is not None else ()
            for symbol in symbols:
                for interval_seconds, minimum_bars, label_ko in (
                    (900, 100, "15분"),
                    (1_800, 100, "30분"),
                    (3_600, 200, "1시간"),
                ):
                    event_now_ms = (
                        active_runtime.events[-1].venue_ts_ms
                        if active_runtime.events
                        else active_runtime.clock.utc_ms()
                    )
                    completed = active_runtime.strategy_completed_candles(
                        symbol,
                        interval_seconds,
                    )
                    interval_ms = interval_seconds * 1_000
                    latest_expected_open_ms = (
                        event_now_ms // interval_ms - 1
                    ) * interval_ms
                    if (
                        len(completed) >= minimum_bars
                        and completed[-1].open_ts_ms >= latest_expected_open_ms
                    ):
                        continue
                    try:
                        payload = await active_market_explorer.candles(
                            "BINANCE_USDM",
                            symbol,
                            interval_seconds,
                            500,
                        )
                        rows = payload.get("candles", [])
                        if not isinstance(rows, list):
                            raise ValueError("공개 전략 완성봉 응답이 배열이 아닙니다.")
                        count = active_runtime.set_strategy_public_history(
                            symbol,
                            interval_seconds,
                            rows,
                            now_ms=event_now_ms,
                        )
                        active_runtime._log(
                            "STRATEGY",
                            f"{symbol} 추세 봉 보충 · 완성 {label_ko} 봉 {count}개",
                        )
                    except (OSError, httpx.HTTPError, RuntimeError, ValueError) as error:
                        active_runtime._log(
                            "STRATEGY",
                            f"{symbol} {label_ko} 공개 워밍업 대기 · {type(error).__name__}",
                        )
                    await asyncio.sleep(0.15)
            await asyncio.sleep(60)

    async def maintain_strategy_governance() -> None:
        """15분마다 신규 자연표본·운영 결함만 평가해 검증된 격리를 적용한다."""

        await asyncio.sleep(15)
        while True:
            try:
                result = await asyncio.to_thread(active_runtime.run_strategy_governance_cycle)
                changes = result.get("changes", [])
                if isinstance(changes, list) and changes:
                    active_runtime._log(
                        "STRATEGY",
                        f"자동 전략 평가 전환 {len(changes)}건 · 충분한 증거만 반영",
                    )
            except (OSError, RuntimeError, ValueError) as error:
                active_runtime._log(
                    "STRATEGY",
                    f"자동 전략 평가 보류 · {type(error).__name__}",
                )
            await asyncio.sleep(900)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        broadcaster: asyncio.Task[None] | None = None
        persistence_stop = asyncio.Event()
        persistence_worker: asyncio.Task[None] | None = None
        storage_health_stop = asyncio.Event()
        storage_health_worker: asyncio.Task[None] | None = None
        trade_cache_task: asyncio.Task[None] | None = None
        hourly_history_task: asyncio.Task[None] | None = None
        governance_task: asyncio.Task[None] | None = None
        try:
            if active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER:
                await active_runtime.start_persistent_live()
                hourly_history_task = asyncio.create_task(
                    maintain_strategy_candle_history(),
                    name="hourly-strategy-history",
                )
                governance_task = asyncio.create_task(
                    maintain_strategy_governance(),
                    name="strategy-governance",
                )
            if not active_runtime.dashboard_trade_cache_ready:
                active_runtime.dashboard_trade_cache_loading = True
                trade_cache_task = asyncio.create_task(
                    active_runtime.warm_dashboard_trade_cache(),
                    name="dashboard-trade-cache",
                )
            persistence_worker = asyncio.create_task(
                active_runtime.run_persistence_worker(persistence_stop),
                name="persistence-worker",
            )
            storage_health_worker = asyncio.create_task(
                active_runtime.run_storage_health_worker(storage_health_stop),
                name="storage-health-worker",
            )
            broadcaster = asyncio.create_task(broadcast_dashboard(), name="dashboard-broadcaster")
            yield
        finally:
            await operation_manager.shutdown()
            await replay_operation_manager.shutdown()
            if broadcaster is not None:
                broadcaster.cancel()
                await asyncio.gather(broadcaster, return_exceptions=True)
            persistence_stop.set()
            if persistence_worker is not None:
                await _await_shutdown_task(persistence_worker)
            storage_health_stop.set()
            if storage_health_worker is not None:
                await _await_shutdown_task(storage_health_worker)
            if trade_cache_task is not None:
                await asyncio.gather(trade_cache_task, return_exceptions=True)
            if hourly_history_task is not None:
                hourly_history_task.cancel()
                await asyncio.gather(hourly_history_task, return_exceptions=True)
            if governance_task is not None:
                governance_task.cancel()
                await asyncio.gather(governance_task, return_exceptions=True)
            await active_runtime.shutdown()
            if active_runtime.ledger is not None:
                active_runtime.ledger.close()

    app = FastAPI(title="ROBOM FlowScalper", version="0.3.0-paper", lifespan=lifespan)
    app.state.runtime = active_runtime
    app.state.control_operation_manager = operation_manager
    app.state.replay_operation_manager = replay_operation_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "http://localhost:5173",
        ],
        allow_origin_regex=r"https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Idempotency-Key", "If-None-Match"],
    )

    @app.get("/api/status")
    async def status() -> dict[str, object]:
        return active_runtime.status().model_dump(mode="json")

    @app.get("/api/events")
    async def events() -> list[dict[str, object]]:
        return [event.model_dump(mode="json") for event in active_runtime.events[-100:]]

    @app.get("/api/dashboard")
    async def dashboard() -> Response:
        return await dashboard_response()

    @app.get("/api/ui/summary")
    async def ui_summary() -> dict[str, object]:
        snapshot, _ = await cached_dashboard()
        return compact_ui_summary(snapshot)

    @app.get("/api/strategies/summary")
    async def strategies_summary(
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    ) -> Response:
        snapshot, _ = await cached_dashboard()
        payload = strategy_page_summary(snapshot)
        etag = stable_etag(payload)
        headers = {"ETag": etag, "Cache-Control": "private, max-age=2"}
        if if_none_match == etag:
            return Response(status_code=304, headers=headers)
        serialized = await asyncio.to_thread(
            json.dumps,
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return Response(
            content=serialized,
            media_type="application/json",
            headers=headers,
        )

    @app.get("/api/settings/summary")
    async def user_settings_summary() -> dict[str, object]:
        snapshot, _ = await cached_dashboard()
        return settings_summary(snapshot)

    @app.get("/api/diagnostics")
    async def advanced_diagnostics() -> dict[str, object]:
        snapshot, _ = await cached_dashboard()
        return diagnostics_rows(snapshot)

    @app.get("/api/strategy-families")
    async def strategy_families(
        if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    ) -> Response:
        snapshot, _ = await cached_dashboard()
        catalog = static_family_catalog()
        payload = {
            "schema_version": 1,
            "families": catalog,
        } | paper_safety_contract(snapshot)
        etag = stable_etag(payload)
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            media_type="application/json",
            headers={"ETag": etag, "Cache-Control": "private, max-age=60"},
        )

    @app.get("/api/strategy-families/{family_id}")
    async def strategy_family_detail(family_id: StrategyFamilyId) -> dict[str, object]:
        snapshot, _ = await cached_dashboard()
        return family_payload(snapshot, family_id)

    @app.get("/api/strategy-families/{family_id}/conditions")
    async def strategy_family_conditions(family_id: StrategyFamilyId) -> dict[str, object]:
        if family_id is StrategyFamilyId.ORDERFLOW_CONFIRMATION:
            return {
                "family_id": family_id.value,
                **active_runtime.orderflow_confirmation_condition_detail(),
                "research_sources": research_source_metadata_rows(
                    _ORDERFLOW_RESEARCH_SOURCE_IDS
                ),
            }
        catalog_row = next(
            row
            for row in strategy_family_catalog(active_runtime.strategy_registry)
            if row["family_id"] == family_id.value
        )
        current_id = catalog_row.get("current_variant_id")
        if current_id is None:
            return {
                "schema_version": 1,
                "family_id": family_id.value,
                "strategy_id": None,
                "symbol": active_runtime.selected_symbol,
                "setup_state": "RESEARCH_NOT_IMPLEMENTED",
                "passed": 0,
                "total": 0,
                "top_blockers": ["현재 실행 가능한 current variant가 없습니다."],
                "conditions": [],
                "sides": [],
                "execution": {},
                "research_sources": [],
                "paper_only": True,
                "real_orders_enabled": False,
                "auth_required": False,
            }
        condition_detail = active_runtime.strategy_condition_detail(str(current_id))
        raw_source_ids = condition_detail.get("research_source_ids")
        source_ids = (
            [str(source_id) for source_id in raw_source_ids]
            if isinstance(raw_source_ids, list)
            else []
        )
        return {
            "family_id": family_id.value,
            **condition_detail,
            "research_sources": research_source_metadata_rows(source_ids),
        }

    @app.patch("/api/strategy-families/{family_id}/research-enabled")
    async def configure_family_research(
        family_id: StrategyFamilyId,
        request: FamilyResearchEnabledRequest,
    ) -> dict[str, object]:
        if family_id is StrategyFamilyId.ORDERFLOW_CONFIRMATION:
            try:
                await asyncio.to_thread(
                    active_runtime.configure_orderflow_confirmation_filter,
                    enabled=request.research_enabled,
                    expected_revision=request.expected_revision,
                    reason=request.reason,
                )
            except OrderflowFilterRevisionConflict as error:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "ORDERFLOW_FILTER_REVISION_CONFLICT",
                        "error_message_ko": "다른 화면에서 주문흐름 필터 설정이 바뀌었습니다.",
                        "retryable": True,
                        "current_filter": error.current,
                    },
                ) from error
            snapshot, _ = await cached_dashboard(force=True)
            return family_payload(snapshot, family_id)
        catalog_row = next(
            row
            for row in strategy_family_catalog(active_runtime.strategy_registry)
            if row["family_id"] == family_id.value
        )
        current_id = catalog_row.get("current_variant_id")
        if current_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "FAMILY_RUNTIME_ENTRY_NOT_IMPLEMENTED",
                    "error_message_ko": "이 family에는 검증된 runtime current variant가 없습니다.",
                    "retryable": False,
                },
            )
        current_setting = active_runtime.strategy_registry.setting(str(current_id))
        try:
            await asyncio.to_thread(
                active_runtime.configure_strategy,
                str(current_id),
                mode=(StrategyMode.SHADOW if request.research_enabled else StrategyMode.OFF),
                lifecycle=(
                    StrategyLifecycle.SHADOW
                    if request.research_enabled
                    else StrategyLifecycle.RESEARCH
                ),
                long_enabled=current_setting.long_enabled,
                short_enabled=current_setting.short_enabled,
                expected_revision=request.expected_revision,
                manual_lock=True,
                source="USER_UI",
                reason=request.reason,
            )
        except StrategyRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "STRATEGY_SETTINGS_REVISION_CONFLICT",
                    "error_message_ko": "다른 화면에서 family 설정이 바뀌었습니다.",
                    "retryable": True,
                    "current_strategy": error.current_setting,
                },
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "FAMILY_RESEARCH_CONFIGURATION_INVALID",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        snapshot, _ = await cached_dashboard(force=True)
        return family_payload(snapshot, family_id)

    @app.get("/api/markets/catalog")
    async def market_catalog(
        source: str | None = Query(default=None, pattern=r"^(BINANCE_USDM|UPBIT_KRW)$"),
        refresh: bool = Query(default=False),
    ) -> dict[str, object]:
        try:
            return await active_market_explorer.catalog(source=source, force=refresh)
        except (OSError, httpx.HTTPError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "PUBLIC_MARKET_CATALOG_UNAVAILABLE",
                    "error_message_ko": "공개 시장 목록을 불러오지 못했습니다.",
                    "retryable": True,
                },
            ) from error

    @app.get("/api/markets/candles")
    async def market_candles(
        source: str = Query(default="BINANCE_USDM", pattern=r"^(BINANCE_USDM|UPBIT_KRW)$"),
        symbol: str = Query(min_length=3, max_length=30, pattern=r"^[\w-]+$"),
        interval_seconds: int = Query(default=180),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, object]:
        try:
            return await active_market_explorer.candles(
                source,
                symbol,
                interval_seconds,
                limit,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "INVALID_MARKET_CANDLE_REQUEST",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        except (OSError, httpx.HTTPError) as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "PUBLIC_MARKET_CANDLES_UNAVAILABLE",
                    "error_message_ko": "공개 시장 캔들을 불러오지 못했습니다.",
                    "retryable": True,
                },
            ) from error

    @app.post("/api/markets/select")
    async def select_market(request: MarketSelectionRequest) -> dict[str, object]:
        if request.source == "BINANCE_USDM":
            active_runtime.set_chart_selection(request.symbol, request.interval_seconds)
        return {
            "source": request.source,
            "symbol": request.symbol.upper(),
            "interval_seconds": request.interval_seconds,
            "pin_for_analysis": request.pin_for_analysis and request.source == "BINANCE_USDM",
            "observation_only": request.source == "UPBIT_KRW",
            "auth_required": False,
            "real_orders_enabled": False,
        }

    @app.get("/api/markets/status")
    async def market_status() -> dict[str, object]:
        catalog = await active_market_explorer.catalog()
        return {
            "catalog_healthy": True,
            "catalog_counts": catalog["counts"],
            "candle_cache_count": 0,
            "auth_required": False,
            "real_orders_enabled": False,
        }

    async def change_paper_entry_intent(
        paused: bool,
        *,
        request: PaperEntryIntentRequest | None,
        idempotency_key: str | None,
    ) -> Response:
        try:
            await asyncio.to_thread(
                active_runtime.set_paused,
                paused,
                expected_revision=(request.expected_revision if request is not None else None),
                idempotency_key=idempotency_key,
                actor="USER_UI",
                reason=(
                    request.reason
                    if request is not None
                    else "USER_PAUSE"
                    if paused
                    else "USER_RESUME"
                ),
            )
        except PaperEntryIntentConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": error.error_code,
                    "error_message_ko": (
                        "같은 요청 식별자가 서로 다른 동작에 사용되었습니다. "
                        "화면을 새로 확인하세요."
                        if error.error_code == "PAPER_ENTRY_IDEMPOTENCY_CONFLICT"
                        else "다른 화면에서 진입 상태가 바뀌었습니다. 최신 상태를 다시 확인하세요."
                    ),
                    "retryable": True,
                    "expected_revision": error.expected_revision,
                    "current_revision": error.current_revision,
                    "current_intent": active_runtime.paper_entry_intent(),
                },
            ) from error
        return await compact_mutation_response(force=True)

    @app.post("/api/control/pause")
    async def pause_entries(
        request: PaperEntryIntentRequest | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Response:
        return await change_paper_entry_intent(
            True,
            request=request,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/control/resume")
    async def resume_entries(
        request: PaperEntryIntentRequest | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Response:
        return await change_paper_entry_intent(
            False,
            request=request,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/control/emergency-close")
    async def emergency_paper_close() -> Response:
        active_runtime.emergency_paper_close()
        return await compact_mutation_response(force=True)

    async def start_live_runner(progress: ProgressCallback) -> None:
        if active_runtime.live_observation_running():
            await progress("PREPARING", "이미 같은 PAPER Run에서 자동 관찰이 작동 중입니다")
            return
        blocked = active_runtime.live_start_block()
        if blocked is not None:
            raise ControlOperationFailure(
                code=blocked[0],
                message_ko=blocked[1],
                retryable=False,
            )
        started = (
            await active_runtime.start_persistent_live(progress=progress)
            if active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER
            else await active_runtime.start_live_run(progress=progress)
        )
        if not started:
            raise ControlOperationFailure(
                code="PUBLIC_DATA_UNAVAILABLE",
                message_ko=(
                    "공개시장에 연결하지 못했습니다. 네트워크 상태를 확인한 뒤 다시 시도하세요."
                ),
                retryable=True,
            )

    async def start_demo_runner(progress: ProgressCallback) -> None:
        await progress("PREPARING", "샘플 PAPER Run을 준비하고 있습니다")
        await active_runtime.shutdown_supervisor()
        active_runtime.start_demo_run()

    async def new_run_runner(progress: ProgressCallback) -> None:
        await progress("PREPARING", "기존 기록을 보존하고 새 PAPER Run을 준비합니다")
        await active_runtime.shutdown_supervisor()
        try:
            active_runtime.start_new_run()
        except ValueError as error:
            raise ControlOperationFailure(
                code="NEW_RUN_BLOCKED",
                message_ko=str(error),
                retryable=False,
            ) from error
        if active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER:
            started = await active_runtime.start_persistent_live(progress=progress)
            if not started:
                raise ControlOperationFailure(
                    code="PUBLIC_DATA_UNAVAILABLE",
                    message_ko=(
                        "공개시장에 연결하지 못했습니다. 네트워크 상태를 확인한 뒤 다시 시도하세요."
                    ),
                    retryable=True,
                )

    async def submit_operation(
        action: ControlAction,
        runner: ControlRunner,
        *,
        request: ControlMutationRequest | None,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        try:
            return await operation_manager.submit(
                action,
                runner,
                idempotency_key=idempotency_key,
                expected_revision=request.expected_revision if request is not None else None,
                actor="USER_UI",
                reason=request.reason if request is not None else "USER_REQUEST",
            )
        except ControlRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "CONTROL_REVISION_CONFLICT",
                    "error_message_ko": (
                        "다른 화면에서 상태가 바뀌었습니다. 최신 상태를 다시 확인하세요."
                    ),
                    "retryable": True,
                    "expected_revision": error.expected_revision,
                    "current_revision": error.current_revision,
                    "current_operation": operation_manager.current_public(),
                },
            ) from error
        except ControlOperationConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "CONTROL_OPERATION_CONFLICT",
                    "error_message_ko": "다른 PAPER 실행 작업이 진행 중입니다.",
                    "retryable": False,
                    "current_operation": error.current_operation,
                },
            ) from error

    @app.post("/api/control/start-live", status_code=202)
    async def start_live(
        request: ControlMutationRequest | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, object]:
        runner = (control_runners or {}).get(ControlAction.START_LIVE, start_live_runner)
        return await submit_operation(
            ControlAction.START_LIVE,
            runner,
            request=request,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/control/start-demo", status_code=202)
    async def start_demo(
        request: ControlMutationRequest | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, object]:
        runner = (control_runners or {}).get(ControlAction.START_DEMO, start_demo_runner)
        return await submit_operation(
            ControlAction.START_DEMO,
            runner,
            request=request,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/control/operations/current")
    async def current_operation() -> dict[str, object] | None:
        return operation_manager.current_public()

    @app.get("/api/control/operations/{operation_id}")
    async def operation_by_id(operation_id: str) -> dict[str, object]:
        operation = operation_manager.get_public(operation_id)
        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "CONTROL_OPERATION_NOT_FOUND",
                    "error_message_ko": "요청한 실행 작업을 찾을 수 없습니다.",
                    "retryable": False,
                },
            )
        return operation

    @app.post("/api/control/operations/{operation_id}/cancel", status_code=202)
    async def cancel_operation(operation_id: str) -> dict[str, object]:
        operation = await operation_manager.cancel(operation_id)
        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "CONTROL_OPERATION_NOT_FOUND",
                    "error_message_ko": "취소할 실행 작업을 찾을 수 없습니다.",
                    "retryable": False,
                },
            )
        return operation

    @app.post("/api/control/chart")
    async def select_chart(request: ChartSelectionRequest) -> Response:
        try:
            active_runtime.set_chart_selection(request.symbol, request.interval_seconds)
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "INVALID_CHART_SELECTION",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        return await compact_mutation_response(force=True)

    @app.post("/api/strategies/{strategy_id}")
    async def configure_strategy(
        strategy_id: str,
        request: StrategyConfigurationRequest,
    ) -> Response:
        try:
            active_runtime.strategy_registry.descriptor(strategy_id)
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "STRATEGY_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        try:
            await asyncio.to_thread(
                active_runtime.configure_strategy,
                strategy_id,
                mode=request.mode,
                long_enabled=request.long_enabled,
                short_enabled=request.short_enabled,
                expected_revision=request.expected_revision,
                manual_lock=request.manual_lock,
                lifecycle=request.lifecycle,
                source="USER_UI",
                reason=request.reason,
            )
        except StrategyRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "STRATEGY_SETTINGS_REVISION_CONFLICT",
                    "error_message_ko": (
                        "다른 화면에서 전략 설정이 바뀌었습니다. 최신 설정을 확인하세요."
                    ),
                    "retryable": True,
                    "current_strategy": error.current_setting,
                },
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "STRATEGY_CONFIGURATION_INVALID",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        return await compact_mutation_response(force=True)

    @app.post("/api/strategies/{strategy_id}/rollback")
    async def rollback_strategy(
        strategy_id: str,
        request: StrategyRollbackRequest,
    ) -> Response:
        try:
            active_runtime.strategy_registry.descriptor(strategy_id)
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "STRATEGY_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        try:
            await asyncio.to_thread(
                active_runtime.rollback_strategy,
                strategy_id,
                target_revision=request.target_revision,
                expected_revision=request.expected_revision,
                reason=request.reason,
            )
        except StrategyRevisionConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "STRATEGY_SETTINGS_REVISION_CONFLICT",
                    "error_message_ko": (
                        "다른 화면에서 전략 설정이 바뀌었습니다. 최신 설정을 확인하세요."
                    ),
                    "retryable": True,
                    "current_strategy": error.current_setting,
                },
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "STRATEGY_ROLLBACK_INVALID",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        return await compact_mutation_response(force=True)

    @app.get("/api/governance")
    async def strategy_governance() -> dict[str, object]:
        include_persisted = active_runtime.mode is not RuntimeMode.LIVE_SHADOW_PAPER
        return await asyncio.to_thread(
            active_runtime.strategy_governance,
            include_persisted=include_persisted,
        )

    @app.post("/api/governance/evaluate")
    async def evaluate_strategy_governance() -> dict[str, object]:
        return await asyncio.to_thread(active_runtime.run_strategy_governance_cycle)

    @app.get("/api/analytics/strategies")
    async def strategy_analytics() -> list[dict[str, object]]:
        include_persisted = active_runtime.mode is not RuntimeMode.LIVE_SHADOW_PAPER
        return await asyncio.to_thread(
            active_runtime.strategy_performance,
            include_persisted=include_persisted,
        )

    @app.get("/api/analytics/strategy-symbols")
    async def strategy_symbol_analytics() -> dict[str, object]:
        include_persisted = active_runtime.mode is not RuntimeMode.LIVE_SHADOW_PAPER
        rows = await asyncio.to_thread(
            active_runtime.strategy_symbol_performance,
            include_persisted=include_persisted,
        )
        scope = await asyncio.to_thread(
            active_runtime.strategy_analytics_scope,
            include_persisted=include_persisted,
        )
        return {
            "generated_ts_ms": active_runtime.clock.utc_ms(),
            "rows": rows,
            "ranking_rule": "표본 30건 이상에서 기대값·Profit Factor·표본을 함께 비교",
            "real_orders_enabled": False,
            "auth_required": False,
            **scope,
        }

    @app.get("/api/trades")
    async def grouped_trades(
        run_scope: str = Query(default="CURRENT", pattern=r"^(CURRENT|ALL)$"),
        account_scope: str = Query(default="ALL", pattern=r"^(MAIN|LEAGUE|ALL)$"),
        profile: str = Query(default="ALL", pattern=r"^(BASE|STRESS|ALL)$"),
        version_scope: str = Query(default="CURRENT", pattern=r"^(CURRENT|ALL)$"),
        sample_type: str = Query(
            default="ALL",
            pattern=r"^(LIVE_PUBLIC|OFFLINE_FIXTURE|ALL)$",
        ),
        limit: int = Query(default=1_000, ge=1, le=2_000),
    ) -> dict[str, object]:
        history = await asyncio.to_thread(
            active_runtime.history_records,
            run_scope=run_scope,
            account_scope=account_scope,
            profile=profile,
            version_scope=version_scope,
            sample_type=sample_type,
            limit=2_000,
        )
        try:
            enriched_history = await asyncio.to_thread(history_with_trade_fill_evidence, history)
        except LedgerInvariantError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "TRADE_FILL_LEDGER_INVARIANT",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error
        return grouped_trade_payload(enriched_history, limit=limit)

    @app.get("/api/history")
    async def history_records(
        run_scope: str = Query(default="CURRENT", pattern=r"^(CURRENT|ALL)$"),
        account_scope: str = Query(default="MAIN", pattern=r"^(MAIN|LEAGUE|ALL)$"),
        profile: str = Query(default="ALL", pattern=r"^(BASE|STRESS|ALL)$"),
        version_scope: str = Query(default="CURRENT", pattern=r"^(CURRENT|ALL)$"),
        sample_type: str = Query(
            default="ALL",
            pattern=r"^(LIVE_PUBLIC|OFFLINE_FIXTURE|ALL)$",
        ),
        limit: int = Query(default=500, ge=1, le=2_000),
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            active_runtime.history_records,
            run_scope=run_scope,
            account_scope=account_scope,
            profile=profile,
            version_scope=version_scope,
            sample_type=sample_type,
            limit=limit,
        )

    @app.get("/api/replay/runs")
    async def replay_runs() -> list[dict[str, object]]:
        return await asyncio.to_thread(active_runtime.replayable_runs)

    @app.get("/api/replay/results")
    async def replay_results() -> list[dict[str, object]]:
        nonlocal replay_results_cache
        if active_runtime.ledger is None:
            return []
        if replay_results_cache is not None:
            return [dict(row) for row in replay_results_cache]
        async with replay_results_cache_lock:
            if replay_results_cache is None:
                stored_results = await asyncio.to_thread(
                    active_runtime.ledger.list_latest_replay_runs
                )
                replay_results_cache = [compact_replay_result(row) for row in stored_results]
        cached_results = replay_results_cache
        assert cached_results is not None
        return [dict(row) for row in cached_results]

    @app.get("/api/replay/operations/current")
    async def current_replay_operation() -> dict[str, object] | None:
        """새로고침 뒤에도 현재 장시간 검증 상태를 다시 붙일 수 있게 한다."""

        return replay_operation_manager.current_public()

    @app.get("/api/replay/operations/{operation_id}")
    async def replay_operation(operation_id: str) -> dict[str, object]:
        operation = replay_operation_manager.get_public(operation_id)
        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_OPERATION_NOT_FOUND",
                    "error_message_ko": "저장 Run 검증 작업을 찾을 수 없습니다.",
                    "retryable": False,
                },
            )
        return operation

    @app.delete("/api/replay/operations/{operation_id}", status_code=202)
    async def cancel_replay_operation(operation_id: str) -> dict[str, object]:
        operation = await replay_operation_manager.cancel(operation_id)
        if operation is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_OPERATION_NOT_FOUND",
                    "error_message_ko": "취소할 저장 Run 검증 작업을 찾을 수 없습니다.",
                    "retryable": False,
                },
            )
        return operation

    @app.get("/api/replay/{run_id}/preview")
    async def replay_preview(
        run_id: str,
        symbol: str | None = Query(
            default=None,
            min_length=3,
            max_length=30,
            pattern=r"^[A-Za-z0-9]+$",
        ),
        candle_limit: int = Query(default=500, ge=1, le=2_000),
    ) -> dict[str, object]:
        normalized_symbol = symbol.upper() if symbol else None
        cache_key = (run_id, normalized_symbol, candle_limit)
        now = time.monotonic()
        cached = replay_preview_cache.get(cache_key)
        if cached is not None and now - cached[0] <= 10.0:
            return dict(cached[1])
        try:
            async with replay_preview_lock:
                now = time.monotonic()
                cached = replay_preview_cache.get(cache_key)
                if cached is not None and now - cached[0] <= 10.0:
                    return dict(cached[1])
                if (
                    active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER
                    and active_runtime.ledger is not None
                ):
                    ensure_replay_process_available()
                    archive = active_runtime.ledger.market_event_archive
                    async with replay_process_lock:
                        preview = await to_process.run_sync(
                            replay_preview_from_paths,
                            str(active_runtime.ledger.path),
                            str(archive.root) if archive is not None else None,
                            run_id,
                            normalized_symbol,
                            candle_limit,
                        )
                else:
                    preview = await asyncio.to_thread(
                        active_runtime.replay_preview,
                        run_id,
                        symbol=normalized_symbol,
                        candle_limit=candle_limit,
                    )
                completed_at = time.monotonic()
                replay_preview_cache[cache_key] = (completed_at, preview)
                if len(replay_preview_cache) > 16:
                    oldest_key = min(
                        replay_preview_cache,
                        key=lambda key: replay_preview_cache[key][0],
                    )
                    replay_preview_cache.pop(oldest_key, None)
                return dict(preview)
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_PREVIEW_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error

    @app.get("/api/replay/{run_id}/timeline")
    async def replay_timeline(
        run_id: str,
        symbol: str | None = Query(
            default=None,
            min_length=3,
            max_length=30,
            pattern=r"^[A-Za-z0-9]+$",
        ),
        limit: int = Query(default=2_000, ge=1, le=2_000),
    ) -> dict[str, object]:
        try:
            if (
                active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER
                and active_runtime.ledger is not None
            ):
                ensure_replay_process_available()
                archive = active_runtime.ledger.market_event_archive
                async with replay_process_lock:
                    return await to_process.run_sync(
                        replay_timeline_from_paths,
                        str(active_runtime.ledger.path),
                        str(archive.root) if archive is not None else None,
                        run_id,
                        symbol,
                        limit,
                    )
            return await asyncio.to_thread(
                active_runtime.replay_timeline,
                run_id,
                symbol=symbol,
                limit=limit,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_TIMELINE_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error

    @app.get("/api/replay/{run_id}/focus")
    async def replay_focus(
        run_id: str,
        trade_id: str = Query(min_length=3, max_length=120),
        profile: str = Query(default="BASE", pattern=r"^(BASE|STRESS)$"),
    ) -> dict[str, object]:
        cache_key = (run_id, trade_id, profile)
        cached = replay_focus_cache.get(cache_key)
        if cached is not None:
            return dict(cached[1])
        try:
            if (
                active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER
                and active_runtime.ledger is not None
            ):
                ensure_replay_process_available()
                archive = active_runtime.ledger.market_event_archive
                async with replay_process_lock:
                    focus = await to_process.run_sync(
                        replay_focus_from_paths,
                        str(active_runtime.ledger.path),
                        str(archive.root) if archive is not None else None,
                        run_id,
                        trade_id,
                        profile,
                        active_runtime.clock.utc_ms(),
                    )
                completed_at = time.monotonic()
                replay_focus_cache[cache_key] = (completed_at, focus)
                if len(replay_focus_cache) > 16:
                    oldest_key = min(
                        replay_focus_cache,
                        key=lambda key: replay_focus_cache[key][0],
                    )
                    replay_focus_cache.pop(oldest_key, None)
                return dict(focus)
            focus = await asyncio.to_thread(
                active_runtime.replay_focus_session,
                run_id,
                trade_id=trade_id,
                profile=profile,
            )
            replay_focus_cache[cache_key] = (time.monotonic(), focus)
            return dict(focus)
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_FOCUS_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error

    @app.post("/api/replay/{run_id}", status_code=202)
    async def replay_run(run_id: str, request: ReplayRequest) -> dict[str, object]:
        if active_runtime.ledger is None or active_runtime.ledger.get_run(run_id) is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_RUN_NOT_FOUND",
                    "error_message_ko": f"알 수 없는 소스 Run: {run_id}",
                    "retryable": False,
                },
            )
        symbol = request.symbol.strip().upper() if request.symbol else None
        try:
            if replay_process_lock.locked():
                ensure_replay_process_available()
            total_events: int | None = None
            if symbol is not None:
                symbols = await asyncio.to_thread(
                    active_runtime.ledger.market_event_symbols,
                    run_id,
                )
                selected = next(
                    (row for row in symbols if str(row["symbol"]) == symbol),
                    None,
                )
                if selected is not None and selected.get("event_count") is not None:
                    total_events = int(str(selected["event_count"]))
            if total_events is None:
                replayable = await asyncio.to_thread(
                    active_runtime.ledger.list_replayable_run_summaries
                    if active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER
                    else active_runtime.replayable_runs
                )
                selected_run = next(
                    (row for row in replayable if str(row["run_id"]) == run_id),
                    None,
                )
                if selected_run is not None and selected_run.get("market_event_count") is not None:
                    total_events = int(str(selected_run["market_event_count"]))
            if total_events is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "REPLAY_SCOPE_COUNT_UNAVAILABLE",
                        "error_message_ko": (
                            "저장 이벤트 전체 건수를 확인할 수 없어 같은 입력 범위를 "
                            "고정하지 못했습니다. 잠시 뒤 다시 시도하세요."
                        ),
                        "retryable": True,
                    },
                )
            if request.event_limit is not None:
                if request.event_limit > total_events:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error_code": "REPLAY_SCOPE_NOT_AVAILABLE",
                            "error_message_ko": (
                                "요청한 저장 이벤트 고정 범위가 현재 원장보다 큽니다. "
                                "정밀 이벤트를 다시 불러온 뒤 시도하세요."
                            ),
                            "retryable": True,
                        },
                    )
                total_events = request.event_limit

            async def runner(progress: ProgressCallback) -> dict[str, object]:
                await progress(
                    "PREPARING",
                    "저장 원장과 공개시장 이벤트를 안전하게 준비하고 있습니다",
                )
                ledger = active_runtime.ledger
                if active_runtime.mode is RuntimeMode.LIVE_SHADOW_PAPER and ledger is not None:
                    archive = ledger.market_event_archive
                    await progress(
                        "PROCESSING",
                        (
                            "LIVE 저장을 강제하지 않고 이미 확정된 고정 범위를 "
                            "저우선순위 프로세스에서 검증하고 있습니다"
                        ),
                    )
                    async with replay_process_lock:

                        async def start_process_replay() -> dict[str, object]:
                            return await replay_stored_run_in_subprocess(
                                str(ledger.path),
                                str(archive.root) if archive is not None else None,
                                run_id,
                                active_runtime.clock.utc_ms(),
                                symbol,
                                total_events,
                            )

                        try:
                            completed = await run_with_live_safety(
                                start_process_replay,
                                probe=active_runtime.replay_live_safety_snapshot,
                            )
                        except ReplayLiveSafetyViolation as error:
                            reason_codes = ", ".join(error.violations)
                            raise ReplayOperationFailure(
                                code="REPLAY_ABORTED_LIVE_SAFETY",
                                message_ko=(
                                    "공개시장 PAPER 관찰 안전조건이 흔들려 저장 Run "
                                    "검증을 자동 중단했습니다. 시장 관찰과 실제 주문 0은 "
                                    f"유지됩니다. 원인: {reason_codes}"
                                ),
                                retryable=True,
                            ) from error
                    await asyncio.to_thread(
                        ledger.record_replay_run,
                        completed,
                    )
                    remember_replay_result(completed)
                    return completed
                await progress(
                    "PROCESSING",
                    "저장 이벤트를 같은 전략 조건으로 검증하고 있습니다",
                )
                completed = await asyncio.to_thread(
                    active_runtime.replay_stored_run,
                    run_id,
                    symbol=symbol,
                    event_limit=total_events,
                )
                remember_replay_result(completed)
                return completed

            return await replay_operation_manager.submit(
                source_run_id=run_id,
                symbol=symbol,
                total_events=total_events,
                runner=runner,
            )
        except ReplayOperationConflict as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "REPLAY_BUSY",
                    "error_message_ko": (
                        "다른 저장 기록을 검증하고 있습니다. 진행 상태를 확인하거나 취소하세요."
                    ),
                    "retryable": True,
                    "operation": error.current_operation,
                },
            ) from error
        except HTTPException:
            raise
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "error_code": "REPLAY_RUN_NOT_FOUND",
                    "error_message_ko": str(error),
                    "retryable": False,
                },
            ) from error

    @app.post("/api/control/new-run", status_code=202)
    async def new_run(
        request: ControlMutationRequest | None = None,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, object]:
        runner = (control_runners or {}).get(ControlAction.NEW_RUN, new_run_runner)
        return await submit_operation(
            ControlAction.NEW_RUN,
            runner,
            request=request,
            idempotency_key=idempotency_key,
        )

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if not _local_browser_origin(origin):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            await websocket.send_text(await dashboard_message())
            websocket_clients.add(websocket)
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return
        finally:
            websocket_clients.discard(websocket)

    @app.websocket("/ws/markets")
    async def market_websocket(websocket: WebSocket) -> None:
        """인증 없는 catalog snapshot을 dashboard 채널과 분리해 보낸다."""

        if not _local_browser_origin(websocket.headers.get("origin")):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            catalog = await active_market_explorer.catalog()
            await websocket.send_json({"type": "catalog_snapshot", "data": catalog})
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/ui")
    async def ui_websocket(websocket: WebSocket) -> None:
        """클라이언트별 snapshot 뒤 작은 V6 delta와 heartbeat만 보낸다."""

        if not _local_browser_origin(websocket.headers.get("origin")):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        sequence = 0
        selected_family_id: StrategyFamilyId | None = None
        selected_detail: dict[str, object] | None = None

        async def send_ui_message(message_type: str, data: object) -> None:
            nonlocal sequence
            sequence += 1
            await websocket.send_json(
                {
                    "schema_version": 1,
                    "sequence": sequence,
                    "type": message_type,
                    "data": data,
                }
            )

        async def selected_family_snapshot(
            family_id: StrategyFamilyId,
        ) -> dict[str, object]:
            dashboard, _ = await cached_dashboard()
            return compact_selected_family_detail(family_payload(dashboard, family_id))

        try:
            dashboard, _ = await cached_dashboard()
            previous_summary = compact_ui_summary(dashboard)
            await send_ui_message("snapshot", previous_summary)
            while True:
                try:
                    raw_message = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=_DASHBOARD_REFRESH_SECONDS,
                    )
                except TimeoutError:
                    dashboard, _ = await cached_dashboard()
                    current_summary = compact_ui_summary(dashboard)
                    messages = ui_delta_messages(previous_summary, current_summary)
                    previous_summary = current_summary
                    if selected_family_id is not None:
                        current_detail = compact_selected_family_detail(
                            family_payload(dashboard, selected_family_id)
                        )
                        if current_detail != selected_detail:
                            selected_detail = current_detail
                            messages.append(
                                {
                                    "type": "selected_detail_delta",
                                    "data": {
                                        "family_id": selected_family_id.value,
                                        "detail": current_detail,
                                    },
                                }
                            )
                    if messages:
                        for message in messages:
                            await send_ui_message(str(message["type"]), message["data"])
                    else:
                        await send_ui_message(
                            "heartbeat",
                            {"server_ts_ms": active_runtime.clock.utc_ms()},
                        )
                    continue

                try:
                    request = json.loads(raw_message)
                except json.JSONDecodeError:
                    await send_ui_message(
                        "error",
                        {
                            "error_code": "INVALID_UI_WEBSOCKET_MESSAGE",
                            "error_message_ko": "화면 선택 요청이 올바른 JSON이 아닙니다.",
                            "retryable": False,
                        },
                    )
                    continue
                if not isinstance(request, Mapping):
                    await send_ui_message(
                        "error",
                        {
                            "error_code": "INVALID_UI_WEBSOCKET_MESSAGE",
                            "error_message_ko": "화면 선택 요청은 객체여야 합니다.",
                            "retryable": False,
                        },
                    )
                    continue
                request_type = str(request.get("type", ""))
                if request_type == "ping":
                    await send_ui_message(
                        "heartbeat",
                        {"server_ts_ms": active_runtime.clock.utc_ms()},
                    )
                    continue
                if request_type != "select_family":
                    await send_ui_message(
                        "error",
                        {
                            "error_code": "UNSUPPORTED_UI_WEBSOCKET_MESSAGE",
                            "error_message_ko": "지원하지 않는 화면 실시간 요청입니다.",
                            "retryable": False,
                        },
                    )
                    continue
                requested_family_id = request.get("family_id")
                if requested_family_id is None:
                    selected_family_id = None
                    selected_detail = None
                    await send_ui_message(
                        "selected_detail_delta",
                        {"family_id": None, "detail": None},
                    )
                    continue
                try:
                    selected_family_id = StrategyFamilyId(str(requested_family_id))
                    selected_detail = await selected_family_snapshot(selected_family_id)
                except ValueError:
                    selected_family_id = None
                    selected_detail = None
                    await send_ui_message(
                        "error",
                        {
                            "error_code": "STRATEGY_FAMILY_NOT_FOUND",
                            "error_message_ko": "요청한 전략 family를 찾지 못했습니다.",
                            "retryable": False,
                        },
                    )
                    continue
                await send_ui_message(
                    "selected_detail_delta",
                    {
                        "family_id": selected_family_id.value,
                        "detail": selected_detail,
                    },
                )
        except WebSocketDisconnect:
            return

    if (FRONTEND_DIST / "assets").is_dir() and (FRONTEND_DIST / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def static_frontend(path: str) -> FileResponse:
            candidate = FRONTEND_DIST / path
            if candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST.resolve()):
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


app = create_app()
