"""LIVE 전략 평가를 단일 전용 프로세스로 격리하고 순서화된 상태를 보존한다."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal
from multiprocessing import get_context
from threading import RLock

from backend.app.domain.models import Side
from backend.app.features import FeatureSnapshot
from backend.app.market_data import Candle
from backend.app.regime import Regime
from backend.app.strategies.registry import (
    StrategyChangeSource,
    StrategyLifecycle,
    StrategyMode,
    StrategyRegistry,
    StrategySetting,
)
from backend.app.strategies.runtime_evaluator import EvaluatedSignal, StrategySignalEvaluator


@dataclass(frozen=True, slots=True)
class StrategySettingSnapshot:
    """RLock과 revision 이력을 제외한 evaluator 전용 전략 설정이다."""

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

    @classmethod
    def from_registry(
        cls,
        registry: StrategyRegistry,
        strategy_id: str,
    ) -> StrategySettingSnapshot:
        setting = registry.setting(strategy_id)
        return cls(
            strategy_id=strategy_id,
            mode=setting.mode,
            lifecycle=setting.lifecycle,
            long_enabled=setting.long_enabled,
            short_enabled=setting.short_enabled,
            revision=setting.revision,
            manual_lock=setting.manual_lock,
            changed_by=setting.changed_by,
            change_reason=setting.change_reason,
            updated_ts_ms=setting.updated_ts_ms,
        )

    def to_setting(self) -> StrategySetting:
        return StrategySetting(
            mode=self.mode,
            lifecycle=self.lifecycle,
            long_enabled=self.long_enabled,
            short_enabled=self.short_enabled,
            revision=self.revision,
            manual_lock=self.manual_lock,
            changed_by=self.changed_by,
            change_reason=self.change_reason,
            updated_ts_ms=self.updated_ts_ms,
        )


@dataclass(frozen=True, slots=True)
class StrategyEvaluationRequest:
    """프로세스 경계를 통과하는 전략 평가의 완전한 불변 입력이다."""

    state_key: str
    settings: tuple[StrategySettingSnapshot, ...]
    snapshot: FeatureSnapshot
    regime: Regime
    tick_size: Decimal = Decimal("0.00000001")
    fifteen_minute_candles: tuple[Candle, ...] = ()
    thirty_minute_candles: tuple[Candle, ...] = ()
    hourly_candles: tuple[Candle, ...] = ()
    reuse_fifteen_minute_candles: bool = False
    reuse_thirty_minute_candles: bool = False
    reuse_hourly_candles: bool = False
    history_limit: int = 1_200

    @classmethod
    def from_registry(
        cls,
        *,
        state_key: str,
        registry: StrategyRegistry,
        snapshot: FeatureSnapshot,
        regime: Regime,
        tick_size: Decimal = Decimal("0.00000001"),
        fifteen_minute_candles: tuple[Candle, ...] = (),
        thirty_minute_candles: tuple[Candle, ...] = (),
        hourly_candles: tuple[Candle, ...] = (),
        reuse_fifteen_minute_candles: bool = False,
        reuse_thirty_minute_candles: bool = False,
        reuse_hourly_candles: bool = False,
        history_limit: int = 1_200,
    ) -> StrategyEvaluationRequest:
        if not state_key.strip():
            raise ValueError("전략 평가 프로세스 state key가 비어 있습니다.")
        if history_limit <= 0:
            raise ValueError("전략 평가 과거창 크기는 양수여야 합니다.")
        reuse_contracts = (
            (reuse_fifteen_minute_candles, fifteen_minute_candles),
            (reuse_thirty_minute_candles, thirty_minute_candles),
            (reuse_hourly_candles, hourly_candles),
        )
        if any(reuse and candles for reuse, candles in reuse_contracts):
            raise ValueError("재사용 캔들 요청은 중복 이력 payload를 포함할 수 없습니다.")
        with registry._setting_lock:
            settings = tuple(
                StrategySettingSnapshot.from_registry(registry, strategy_id)
                for strategy_id in registry.strategy_ids
            )
        return cls(
            state_key=state_key,
            settings=settings,
            snapshot=snapshot,
            regime=regime,
            tick_size=tick_size,
            fifteen_minute_candles=fifteen_minute_candles,
            thirty_minute_candles=thirty_minute_candles,
            hourly_candles=hourly_candles,
            reuse_fifteen_minute_candles=reuse_fifteen_minute_candles,
            reuse_thirty_minute_candles=reuse_thirty_minute_candles,
            reuse_hourly_candles=reuse_hourly_candles,
            history_limit=history_limit,
        )


@dataclass(frozen=True, slots=True)
class StrategyConditionRows:
    """자식 evaluator가 실제 사용한 방향별 조건행이다."""

    symbol: str
    strategy_id: str
    side: Side
    rows: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class StrategyEvaluationResult:
    """메인 프로세스가 후보와 UI 조건 상태를 원자적으로 채택할 결과다."""

    signals: tuple[EvaluatedSignal, ...]
    condition_rows: tuple[StrategyConditionRows, ...]


class StrategyCandleCacheMiss(RuntimeError):
    """전략 자식 프로세스에 요청한 완성봉 이력이 없을 때의 안전 재전송 신호다."""


@dataclass(slots=True)
class _WorkerState:
    state_key: str
    history_limit: int
    registry: StrategyRegistry
    evaluator: StrategySignalEvaluator
    candle_history: dict[tuple[str, int], tuple[Candle, ...]]


_WORKER_STATE: _WorkerState | None = None

# 현재 모든 캔들 기반 전략의 가장 긴 결정 창은 완성봉 200개다.
_PROCESS_CANDLE_LIMIT = 200


def _fresh_worker_state(state_key: str, history_limit: int) -> _WorkerState:
    return _WorkerState(
        state_key=state_key,
        history_limit=history_limit,
        registry=StrategyRegistry(),
        evaluator=StrategySignalEvaluator(history_limit=history_limit),
        candle_history={},
    )


def warm_strategy_worker(state_key: str, history_limit: int) -> int:
    """LIVE 연결 전에 자식 프로세스와 evaluator 상태를 미리 준비한다."""

    global _WORKER_STATE
    if not state_key.strip():
        raise ValueError("전략 평가 프로세스 state key가 비어 있습니다.")
    if history_limit <= 0:
        raise ValueError("전략 평가 과거창 크기는 양수여야 합니다.")
    if (
        _WORKER_STATE is None
        or _WORKER_STATE.state_key != state_key
        or _WORKER_STATE.history_limit != history_limit
    ):
        _WORKER_STATE = _fresh_worker_state(state_key, history_limit)
    return os.getpid()


def _apply_settings(
    registry: StrategyRegistry,
    settings: tuple[StrategySettingSnapshot, ...],
) -> None:
    expected_ids = registry.strategy_ids
    received_ids = tuple(setting.strategy_id for setting in settings)
    if len(received_ids) != len(set(received_ids)) or set(received_ids) != set(expected_ids):
        raise RuntimeError("전략 평가 프로세스의 전략 설정 집합이 현재 소스와 다릅니다.")
    registry._settings = {
        setting.strategy_id: setting.to_setting()
        for setting in settings
    }


def _resolve_worker_candles(
    state: _WorkerState,
    *,
    symbol: str,
    interval_seconds: int,
    candles: tuple[Candle, ...],
    reuse: bool,
) -> tuple[Candle, ...]:
    """새 완성봉만 캐시에 반영하고 재사용 요청은 캐시 미스를 숨기지 않는다."""

    key = (symbol, interval_seconds)
    if reuse:
        if key not in state.candle_history:
            raise StrategyCandleCacheMiss(
                f"전략 자식 프로세스 캔들 캐시 미스: {symbol} {interval_seconds}초"
            )
        return state.candle_history[key]
    if any(
        candle.symbol != symbol or candle.interval_seconds != interval_seconds
        for candle in candles
    ):
        raise ValueError("전략 캔들 payload의 종목 또는 시간구간이 요청과 다릅니다.")
    state.candle_history[key] = candles
    return candles


def evaluate_strategy_request(
    request: StrategyEvaluationRequest,
) -> StrategyEvaluationResult:
    """전용 자식 프로세스에서만 호출할 상태 보존 evaluator 진입점이다."""

    global _WORKER_STATE
    if (
        _WORKER_STATE is None
        or _WORKER_STATE.state_key != request.state_key
        or _WORKER_STATE.history_limit != request.history_limit
    ):
        _WORKER_STATE = _fresh_worker_state(request.state_key, request.history_limit)
    state = _WORKER_STATE
    symbol = request.snapshot.symbol
    fifteen_minute_candles = _resolve_worker_candles(
        state,
        symbol=symbol,
        interval_seconds=900,
        candles=request.fifteen_minute_candles,
        reuse=request.reuse_fifteen_minute_candles,
    )
    thirty_minute_candles = _resolve_worker_candles(
        state,
        symbol=symbol,
        interval_seconds=1_800,
        candles=request.thirty_minute_candles,
        reuse=request.reuse_thirty_minute_candles,
    )
    hourly_candles = _resolve_worker_candles(
        state,
        symbol=symbol,
        interval_seconds=3_600,
        candles=request.hourly_candles,
        reuse=request.reuse_hourly_candles,
    )
    _apply_settings(state.registry, request.settings)
    signals = state.evaluator.evaluate(
        state.registry,
        request.snapshot,
        request.regime,
        tick_size=request.tick_size,
        fifteen_minute_candles=fifteen_minute_candles,
        thirty_minute_candles=thirty_minute_candles,
        hourly_candles=hourly_candles,
    )
    condition_rows = tuple(
        StrategyConditionRows(
            symbol=signal.symbol,
            strategy_id=signal.decision.strategy_id,
            side=signal.decision.side,
            rows=tuple(
                dict(row)
                for row in state.evaluator.condition_rows(
                    signal.symbol,
                    signal.decision.strategy_id,
                    signal.decision.side,
                )
            ),
        )
        for signal in signals
    )
    return StrategyEvaluationResult(
        signals=signals,
        condition_rows=condition_rows,
    )


StrategyEvaluationWorker = Callable[
    [StrategyEvaluationRequest],
    StrategyEvaluationResult,
]


class ProcessStrategyEvaluator:
    """하나의 spawn 프로세스에서 LIVE 평가 순서와 evaluator 이력을 보존한다."""

    def __init__(
        self,
        history_limit: int = 1_200,
        *,
        worker_function: StrategyEvaluationWorker = evaluate_strategy_request,
    ) -> None:
        if history_limit <= 0:
            raise ValueError("전략 평가 과거창 크기는 양수여야 합니다.")
        self._history_limit = history_limit
        self._worker_function = worker_function
        self._executor: ProcessPoolExecutor | None = None
        self._executor_lock = RLock()
        self._condition_lock = RLock()
        self._closed = False
        self._latest_requested_state_key: str | None = None
        self._accepted_state_key: str | None = None
        self._candle_history_state_key: str | None = None
        self._candle_history_payloads: dict[
            tuple[str, int],
            tuple[Candle, ...],
        ] = {}
        self._candle_full_payload_count = 0
        self._candle_reuse_payload_count = 0
        self._candle_cache_miss_retry_count = 0
        self._latest_conditions: dict[
            tuple[str, str, Side],
            tuple[dict[str, object], ...],
        ] = {}

    @property
    def history_limit(self) -> int:
        return self._history_limit

    def request(
        self,
        *,
        state_key: str,
        registry: StrategyRegistry,
        snapshot: FeatureSnapshot,
        regime: Regime,
        tick_size: Decimal = Decimal("0.00000001"),
        fifteen_minute_candles: tuple[Candle, ...] = (),
        thirty_minute_candles: tuple[Candle, ...] = (),
        hourly_candles: tuple[Candle, ...] = (),
    ) -> StrategyEvaluationRequest:
        if not state_key.strip():
            raise ValueError("전략 평가 프로세스 state key가 비어 있습니다.")
        symbol = snapshot.symbol
        with self._executor_lock:
            if self._candle_history_state_key != state_key:
                self._candle_history_payloads.clear()
                self._candle_history_state_key = state_key
            fifteen_payload, reuse_fifteen = self._candle_payload_locked(
                symbol,
                900,
                fifteen_minute_candles[-_PROCESS_CANDLE_LIMIT:],
            )
            thirty_payload, reuse_thirty = self._candle_payload_locked(
                symbol,
                1_800,
                thirty_minute_candles[-_PROCESS_CANDLE_LIMIT:],
            )
            hourly_payload, reuse_hourly = self._candle_payload_locked(
                symbol,
                3_600,
                hourly_candles[-_PROCESS_CANDLE_LIMIT:],
            )
        return StrategyEvaluationRequest.from_registry(
            state_key=state_key,
            registry=registry,
            snapshot=snapshot,
            regime=regime,
            tick_size=tick_size,
            fifteen_minute_candles=fifteen_payload,
            thirty_minute_candles=thirty_payload,
            hourly_candles=hourly_payload,
            reuse_fifteen_minute_candles=reuse_fifteen,
            reuse_thirty_minute_candles=reuse_thirty,
            reuse_hourly_candles=reuse_hourly,
            history_limit=self._history_limit,
        )

    async def evaluate(
        self,
        request: StrategyEvaluationRequest,
    ) -> StrategyEvaluationResult:
        result, cancellation = await self.evaluate_to_completion(request)
        if cancellation is not None:
            raise cancellation
        self.accept_result(request, result)
        return result

    async def warm(self, state_key: str) -> int:
        """첫 LIVE 이벤트 전에 spawn과 registry 생성을 완료하고 자식 PID를 반환한다."""

        process_id = await self.run_sync(
            warm_strategy_worker,
            state_key,
            self._history_limit,
        )
        with self._executor_lock:
            self._latest_requested_state_key = state_key
            self._candle_history_payloads.clear()
            self._candle_history_state_key = state_key
        with self._condition_lock:
            if self._accepted_state_key != state_key:
                self._latest_conditions.clear()
                self._accepted_state_key = state_key
        return process_id

    async def evaluate_to_completion(
        self,
        request: StrategyEvaluationRequest,
    ) -> tuple[StrategyEvaluationResult, asyncio.CancelledError | None]:
        """호출 task 취소 뒤에도 한 평가를 완료까지 drain해 순서를 지킨다."""

        cancellation: asyncio.CancelledError | None = None
        evaluation_request = request
        cache_miss_retried = False
        while True:
            future = self._submit(evaluation_request)
            worker = asyncio.wrap_future(future)
            try:
                while True:
                    try:
                        result = await asyncio.shield(worker)
                        if not isinstance(result, StrategyEvaluationResult):
                            raise TypeError(
                                "전략 평가 프로세스가 올바르지 않은 결과를 반환했습니다."
                            )
                        return result, cancellation
                    except asyncio.CancelledError as error:
                        cancellation = error
            except StrategyCandleCacheMiss:
                if cache_miss_retried:
                    raise
                evaluation_request = self._full_candle_history_request(request)
                cache_miss_retried = True
                with self._executor_lock:
                    self._candle_cache_miss_retry_count += 1

    def submit[ResultT](
        self,
        function: Callable[..., ResultT],
        *arguments: object,
    ) -> Future[ResultT]:
        """같은 전용 프로세스에 pickle 가능한 module-level 함수를 제출한다."""

        with self._executor_lock:
            return self._submit_locked(function, *arguments)

    async def run_sync[ResultT](
        self,
        function: Callable[..., ResultT],
        *arguments: object,
    ) -> ResultT:
        """CPU-bound 진단·warmup도 생산 전략 평가와 같은 프로세스에서 실행한다."""

        worker = asyncio.wrap_future(self.submit(function, *arguments))
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                result = await asyncio.shield(worker)
                break
            except asyncio.CancelledError as error:
                cancellation = error
        if cancellation is not None:
            raise cancellation
        return result

    def accept_result(
        self,
        request: StrategyEvaluationRequest,
        result: StrategyEvaluationResult,
    ) -> bool:
        """현재 Run 결과만 메인 프로세스의 조건 상세 cache에 반영한다."""

        with self._executor_lock:
            if request.state_key != self._latest_requested_state_key:
                return False
        with self._condition_lock:
            if self._accepted_state_key != request.state_key:
                self._latest_conditions.clear()
                self._accepted_state_key = request.state_key
            for condition in result.condition_rows:
                self._latest_conditions[
                    (condition.symbol, condition.strategy_id, condition.side)
                ] = condition.rows
        return True

    def condition_rows(
        self,
        symbol: str,
        strategy_id: str,
        side: Side,
        *,
        state_key: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        with self._condition_lock:
            if state_key is not None and state_key != self._accepted_state_key:
                return ()
            return self._latest_conditions.get((symbol, strategy_id, side), ())

    def candle_cache_diagnostics(self) -> dict[str, int]:
        """운영 증거에 활용할 캔들 IPC 캐시 진단을 반환한다."""

        with self._executor_lock:
            return {
                "strategy_candle_ipc_cached_history_count": len(
                    self._candle_history_payloads
                ),
                "strategy_candle_ipc_full_payload_count": (
                    self._candle_full_payload_count
                ),
                "strategy_candle_ipc_reuse_payload_count": (
                    self._candle_reuse_payload_count
                ),
                "strategy_candle_ipc_cache_miss_retry_count": (
                    self._candle_cache_miss_retry_count
                ),
            }

    def close(self) -> None:
        """새 평가를 막고 전용 프로세스의 진행 중 작업까지 안전하게 종료한다."""

        with self._executor_lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)

    async def aclose(self) -> None:
        worker = asyncio.create_task(asyncio.to_thread(self.close))
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                await asyncio.shield(worker)
                break
            except asyncio.CancelledError as error:
                cancellation = error
        if cancellation is not None:
            raise cancellation

    def _submit(
        self,
        request: StrategyEvaluationRequest,
    ) -> Future[StrategyEvaluationResult]:
        if request.history_limit != self._history_limit:
            raise ValueError("전략 평가 요청의 과거창 크기가 프로세스 설정과 다릅니다.")
        with self._executor_lock:
            self._latest_requested_state_key = request.state_key
            return self._submit_locked(self._worker_function, request)

    def _candle_payload_locked(
        self,
        symbol: str,
        interval_seconds: int,
        candles: tuple[Candle, ...],
    ) -> tuple[tuple[Candle, ...], bool]:
        key = (symbol, interval_seconds)
        if key in self._candle_history_payloads:
            previous = self._candle_history_payloads[key]
            if previous == candles:
                self._candle_reuse_payload_count += 1
                return (), True
        self._candle_history_payloads[key] = candles
        self._candle_full_payload_count += 1
        return candles, False

    def _full_candle_history_request(
        self,
        request: StrategyEvaluationRequest,
    ) -> StrategyEvaluationRequest:
        with self._executor_lock:
            if self._candle_history_state_key != request.state_key:
                raise StrategyCandleCacheMiss(
                    "이전 상태의 캔들 캐시 미스는 현재 Run에 재전송할 수 없습니다."
                )
            symbol = request.snapshot.symbol
            try:
                fifteen = self._candle_history_payloads[(symbol, 900)]
                thirty = self._candle_history_payloads[(symbol, 1_800)]
                hourly = self._candle_history_payloads[(symbol, 3_600)]
            except KeyError as error:
                raise StrategyCandleCacheMiss(
                    "전략 평가 재시도에 필요한 캔들 이력이 없습니다."
                ) from error
        return replace(
            request,
            fifteen_minute_candles=fifteen,
            thirty_minute_candles=thirty,
            hourly_candles=hourly,
            reuse_fifteen_minute_candles=False,
            reuse_thirty_minute_candles=False,
            reuse_hourly_candles=False,
        )

    def _submit_locked[ResultT](
        self,
        function: Callable[..., ResultT],
        *arguments: object,
    ) -> Future[ResultT]:
        if self._closed:
            raise RuntimeError("종료된 전략 평가 프로세스에는 작업을 제출할 수 없습니다.")
        if self._executor is None:
            self._executor = ProcessPoolExecutor(
                max_workers=1,
                mp_context=get_context("spawn"),
            )
        return self._executor.submit(function, *arguments)
