"""저장된 공개시장 이벤트를 동일 런타임 파이프라인에 재입력한다."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from uuid import uuid4

from backend.app.build_identity import STRATEGY_VERSION
from backend.app.domain.models import MarketDataState, MarketEvent, RuntimeMode, Venue
from backend.app.replay.engine import ReplayEngine
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import LedgerInvariantError, SQLiteLedger
from backend.app.strategies.registry import StrategyMode


@dataclass(frozen=True, slots=True)
class StoredMarketReplayResult:
    replay_id: str
    source_run_id: str
    scope_symbol: str | None
    created_ts_ms: int
    checksum: str
    input_checksum: str
    event_count: int
    first_ts_ms: int | None
    last_ts_ms: int | None
    event_type_counts: dict[str, int]
    symbol_counts: dict[str, int]
    strategy_evaluation_count: int
    qualified_signal_count: int
    candidate_plan_count: int
    main_trade_count: int
    shadow_trade_count: int
    decision_path: tuple[str, ...]
    final_state: str
    real_orders_enabled: bool
    auth_required: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "replay_id": self.replay_id,
            "source_run_id": self.source_run_id,
            "scope_symbol": self.scope_symbol,
            "created_ts_ms": self.created_ts_ms,
            "checksum": self.checksum,
            "input_checksum": self.input_checksum,
            "event_count": self.event_count,
            "first_ts_ms": self.first_ts_ms,
            "last_ts_ms": self.last_ts_ms,
            "event_type_counts": self.event_type_counts,
            "symbol_counts": self.symbol_counts,
            "strategy_evaluation_count": self.strategy_evaluation_count,
            "qualified_signal_count": self.qualified_signal_count,
            "candidate_plan_count": self.candidate_plan_count,
            "main_trade_count": self.main_trade_count,
            "shadow_trade_count": self.shadow_trade_count,
            "decision_path": list(self.decision_path),
            "final_state": self.final_state,
            "real_orders_enabled": self.real_orders_enabled,
            "auth_required": self.auth_required,
        }


class StoredMarketReplay:
    """SQLite checksum 검증 이벤트를 신규 메모리 PAPER 런타임으로 재처리한다."""

    def __init__(self, replay_engine: ReplayEngine | None = None) -> None:
        self.replay_engine = replay_engine or ReplayEngine()

    def run(
        self,
        ledger: SQLiteLedger,
        *,
        source_run_id: str,
        created_ts_ms: int,
        symbol: str | None = None,
        event_limit: int | None = None,
        cooperative_yield: Callable[[], None] | None = None,
        archive_batch_yield: Callable[[int], None] | None = None,
        archive_batch_guard: Callable[[], AbstractContextManager[None]] | None = None,
        persist_result: bool = True,
    ) -> StoredMarketReplayResult:
        run = ledger.get_run(source_run_id)
        if run is None:
            raise ValueError(f"알 수 없는 소스 Run: {source_run_id}")
        if event_limit is not None and event_limit <= 0:
            raise ValueError("리플레이 이벤트 고정 범위는 양수여야 합니다.")
        scope_symbol = symbol.strip().upper() if symbol else None
        events = ledger.list_market_events(
            source_run_id,
            symbol=scope_symbol,
            limit=event_limit,
            cooperative_yield=cooperative_yield,
            archive_batch_yield=archive_batch_yield,
            archive_batch_guard=archive_batch_guard,
        )
        if event_limit is not None and len(events) != event_limit:
            raise LedgerInvariantError(
                "요청한 리플레이 이벤트 고정 범위를 원장에서 모두 읽지 못했습니다."
            )
        if cooperative_yield is not None:
            cooperative_yield()
        venue = Venue(str(run["venue"]))
        runtime = PaperRuntime(
            mode=RuntimeMode.REPLAY,
            run_id=source_run_id,
            venue=venue,
        )
        runtime.market_data_state = MarketDataState.LIVE
        runtime.paused = False
        runtime.runtime_health_flags = ["STORED_PUBLIC_MARKET_REPLAY", "NO_AUTH_HEADERS"]
        self._restore_strategy_settings(runtime, ledger, source_run_id)
        for index, payload in enumerate(events, start=1):
            event = MarketEvent.model_validate(payload)
            runtime.ingest_live_event(event)
            if cooperative_yield is not None and index % 16 == 0:
                cooperative_yield()
        if cooperative_yield is not None:
            cooperative_yield()
        decisions = tuple(
            f"LATEST:{signal.symbol}:{signal.decision.strategy_id}:"
            f"{signal.decision.side.value}:{signal.decision.status.value}:"
            f"{','.join(signal.decision.rejection_codes)}"
            for _, signal in sorted(runtime.strategy_signals.items())
        )
        audits = tuple(
            f"{audit.get('event', 'UNKNOWN')}:{audit.get('strategy_id', 'NONE')}:"
            f"{audit.get('side', 'NONE')}:{audit.get('account_id', 'NONE')}"
            for audit in runtime.paper_portfolio.audit_events
        )
        decision_path = (
            *decisions,
            *audits,
            f"SUMMARY:evaluated={runtime.strategy_evaluation_count}:"
            f"qualified={runtime.qualified_signal_count}",
        )
        if runtime.paper_portfolio.main.position is not None:
            final_state = "MAIN_POSITION_OPEN"
        elif runtime.paper_portfolio.main.completed_trades:
            final_state = "MAIN_TRADES_CLOSED"
        else:
            final_state = "OBSERVING_NO_MAIN_TRADE"
        config_value = json.loads(str(run["config_json"]))
        if not isinstance(config_value, dict):
            raise LedgerInvariantError("Run config_json은 객체여야 합니다.")
        digest = self.replay_engine.replay_market_path(
            events,
            config=config_value,
            strategy_version=STRATEGY_VERSION,
            seed=int(str(config_value.get("seed", 20260822))),
            decision_path=decision_path,
            final_state=final_state,
            cooperative_yield=cooperative_yield,
        )
        result = StoredMarketReplayResult(
            replay_id=f"replay-{uuid4().hex[:16]}",
            source_run_id=source_run_id,
            scope_symbol=scope_symbol,
            created_ts_ms=created_ts_ms,
            checksum=digest.checksum,
            input_checksum=digest.input_checksum,
            event_count=digest.event_count,
            first_ts_ms=digest.first_ts_ms,
            last_ts_ms=digest.last_ts_ms,
            event_type_counts=digest.event_type_counts,
            symbol_counts=digest.symbol_counts,
            strategy_evaluation_count=runtime.strategy_evaluation_count,
            qualified_signal_count=runtime.qualified_signal_count,
            candidate_plan_count=_candidate_plan_count(
                runtime.paper_portfolio.audit_events
            ),
            main_trade_count=len(runtime.paper_portfolio.main.completed_trades),
            shadow_trade_count=sum(
                len(account.completed_trades)
                for account in runtime.paper_portfolio.shadows.values()
            ),
            decision_path=digest.decision_path,
            final_state=digest.final_state,
            real_orders_enabled=runtime.status().real_orders_enabled,
            auth_required=runtime.status().auth_required,
        )
        if persist_result:
            ledger.record_replay_run(result.as_dict())
        return result

    @staticmethod
    def _restore_strategy_settings(
        runtime: PaperRuntime,
        ledger: SQLiteLedger,
        run_id: str,
    ) -> None:
        latest: dict[str, dict[str, object]] = {}
        for setting in ledger.list_strategy_settings(run_id):
            latest[str(setting["strategy_id"])] = setting
        for strategy_id, setting in latest.items():
            runtime.strategy_registry.configure(
                strategy_id,
                mode=StrategyMode(str(setting["mode"])),
                long_enabled=bool(setting["long_enabled"]),
                short_enabled=bool(setting["short_enabled"]),
            )


def _candidate_plan_count(audits: tuple[dict[str, object], ...] | list[dict[str, object]]) -> int:
    """main과 League 계좌에 중복 배포된 같은 후보를 한 번만 센다."""

    candidate_events = {
        "MAIN_CANDIDATE_SELECTED",
        "LEAGUE_CANDIDATE_ARMED",
        "SHADOW_CANDIDATE_ARMED",
    }
    return len(
        {
            str(audit["candidate_id"])
            for audit in audits
            if audit.get("event") in candidate_events and audit.get("candidate_id")
        }
    )
