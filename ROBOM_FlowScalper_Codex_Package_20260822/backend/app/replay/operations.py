"""장시간 저장 Run 검증을 즉시 응답하는 취소 가능한 작업으로 관리한다."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

ClockMs = Callable[[], int]
ProgressCallback = Callable[[str, str], Awaitable[None]]
ReplayRunner = Callable[[ProgressCallback], Awaitable[dict[str, object]]]
AuditCallback = Callable[[dict[str, object]], None]


class ReplayOperationState(StrEnum):
    REQUESTED = "REQUESTED"
    PREPARING = "PREPARING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_BLOCKED = "FAILED_BLOCKED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = {
    ReplayOperationState.COMPLETED,
    ReplayOperationState.FAILED_RETRYABLE,
    ReplayOperationState.FAILED_BLOCKED,
    ReplayOperationState.CANCELLED,
}


class ReplayOperationConflict(RuntimeError):
    def __init__(self, current_operation: dict[str, object]) -> None:
        super().__init__("다른 저장 Run 검증이 진행 중입니다.")
        self.current_operation = current_operation


class ReplayOperationFailure(RuntimeError):
    def __init__(self, *, code: str, message_ko: str, retryable: bool) -> None:
        super().__init__(message_ko)
        self.code = code
        self.message_ko = message_ko
        self.retryable = retryable


@dataclass(slots=True)
class ReplayOperation:
    operation_id: str
    source_run_id: str
    symbol: str | None
    total_events: int | None
    state: ReplayOperationState
    stage_ko: str
    started_ts_ms: int
    updated_ts_ms: int
    finished_ts_ms: int | None = None
    retryable: bool = False
    error_code: str | None = None
    error_message_ko: str | None = None
    result: dict[str, object] | None = None
    revision: int = 0
    actor: str = "USER_UI"
    reason: str = "USER_REPLAY_REQUEST"
    history: list[dict[str, object]] = field(default_factory=list)

    def public(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "source_run_id": self.source_run_id,
            "symbol": self.symbol,
            "total_events": self.total_events,
            "state": self.state.value,
            "stage_ko": self.stage_ko,
            "started_ts_ms": self.started_ts_ms,
            "updated_ts_ms": self.updated_ts_ms,
            "finished_ts_ms": self.finished_ts_ms,
            "retryable": self.retryable,
            "error_code": self.error_code,
            "error_message_ko": self.error_message_ko,
            "result": dict(self.result) if self.result is not None else None,
            "revision": self.revision,
            "actor": self.actor,
            "reason": self.reason,
            "history": [dict(item) for item in self.history],
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }


class ReplayOperationManager:
    """동시에 하나의 저장 Run 검증만 실행하고 상태와 취소를 공개한다."""

    def __init__(
        self,
        clock_ms: ClockMs,
        *,
        timeout_seconds: float = 14_400,
        audit: AuditCallback | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("replay timeout은 양수여야 합니다.")
        self._clock_ms = clock_ms
        self._timeout_seconds = timeout_seconds
        self._audit = audit
        self._lock = asyncio.Lock()
        self._current: ReplayOperation | None = None
        self._operations: dict[str, ReplayOperation] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._revision = 0

    def current_public(self) -> dict[str, object] | None:
        return self._current.public() if self._current is not None else None

    def active_public(self) -> dict[str, object] | None:
        current = self._current
        if current is None or current.state in TERMINAL_STATES:
            return None
        return current.public()

    def get_public(self, operation_id: str) -> dict[str, object] | None:
        operation = self._operations.get(operation_id)
        return operation.public() if operation is not None else None

    async def submit(
        self,
        *,
        source_run_id: str,
        symbol: str | None,
        total_events: int | None,
        runner: ReplayRunner,
    ) -> dict[str, object]:
        async with self._lock:
            current = self._current
            if current is not None and current.state not in TERMINAL_STATES:
                if current.source_run_id == source_run_id and current.symbol == symbol:
                    return current.public()
                raise ReplayOperationConflict(current.public())
            now = self._clock_ms()
            self._revision += 1
            operation = ReplayOperation(
                operation_id=f"replay-operation-{uuid4().hex}",
                source_run_id=source_run_id,
                symbol=symbol,
                total_events=total_events,
                state=ReplayOperationState.REQUESTED,
                stage_ko="저장 Run 검증 요청을 받았습니다",
                started_ts_ms=now,
                updated_ts_ms=now,
                revision=self._revision,
                history=[
                    {
                        "state": ReplayOperationState.REQUESTED.value,
                        "stage_ko": "저장 Run 검증 요청을 받았습니다",
                        "ts_ms": now,
                        "revision": self._revision,
                    }
                ],
            )
            self._current = operation
            self._operations[operation.operation_id] = operation
            self._record_audit(operation)
            task = asyncio.create_task(
                self._run(operation, runner),
                name=f"stored-replay-{operation.operation_id}",
            )
            self._tasks[operation.operation_id] = task
            task.add_done_callback(
                lambda completed: self._finish_task(operation, completed)
            )
            return operation.public()

    async def cancel(self, operation_id: str) -> dict[str, object] | None:
        async with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None:
                return None
            if operation.state in TERMINAL_STATES:
                return operation.public()
            self._transition(
                operation,
                ReplayOperationState.CANCELLING,
                "저장 Run 검증을 안전하게 취소하고 있습니다",
            )
            task = self._tasks.get(operation_id)
            if task is not None:
                task.cancel()
            return operation.public()

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(
        self,
        operation: ReplayOperation,
        runner: ReplayRunner,
    ) -> None:
        async def progress(state: str, stage_ko: str) -> None:
            self._transition(operation, ReplayOperationState(state), stage_ko)

        try:
            async with asyncio.timeout(self._timeout_seconds):
                operation.result = await runner(progress)
        except asyncio.CancelledError:
            self._transition(
                operation,
                ReplayOperationState.CANCELLED,
                "저장 Run 검증을 취소했습니다",
            )
        except TimeoutError:
            self._fail(
                operation,
                ReplayOperationFailure(
                    code="REPLAY_TIMEOUT",
                    message_ko=(
                        "저장 Run 검증 제한시간을 넘겨 중단했습니다. "
                        "범위를 확인한 뒤 다시 시도하세요."
                    ),
                    retryable=True,
                ),
            )
        except ReplayOperationFailure as error:
            self._fail(operation, error)
        except ValueError as error:
            self._fail(
                operation,
                ReplayOperationFailure(
                    code="REPLAY_RUN_NOT_FOUND",
                    message_ko=str(error),
                    retryable=False,
                ),
            )
        except Exception as error:
            self._fail(
                operation,
                ReplayOperationFailure(
                    code=f"REPLAY_{type(error).__name__.upper()}",
                    message_ko=(
                        "저장 Run 검증을 완료하지 못했습니다. "
                        "원장 상태를 확인하고 다시 시도하세요."
                    ),
                    retryable=True,
                ),
            )
        else:
            self._transition(
                operation,
                ReplayOperationState.COMPLETED,
                "저장 Run 전략 검증을 완료했습니다",
            )

    def _fail(self, operation: ReplayOperation, error: ReplayOperationFailure) -> None:
        operation.error_code = error.code
        operation.error_message_ko = error.message_ko
        operation.retryable = error.retryable
        state = (
            ReplayOperationState.FAILED_RETRYABLE
            if error.retryable
            else ReplayOperationState.FAILED_BLOCKED
        )
        self._transition(operation, state, error.message_ko)

    def _transition(
        self,
        operation: ReplayOperation,
        state: ReplayOperationState,
        stage_ko: str,
    ) -> None:
        if operation.state in TERMINAL_STATES:
            return
        now = self._clock_ms()
        self._revision += 1
        operation.state = state
        operation.stage_ko = stage_ko
        operation.updated_ts_ms = now
        operation.revision = self._revision
        if state in TERMINAL_STATES:
            operation.finished_ts_ms = now
        operation.history.append(
            {
                "state": state.value,
                "stage_ko": stage_ko,
                "ts_ms": now,
                "revision": self._revision,
            }
        )
        operation.history[:] = operation.history[-20:]
        self._record_audit(operation)

    def _record_audit(self, operation: ReplayOperation) -> None:
        if self._audit is not None:
            self._audit(operation.public())

    def _finish_task(
        self,
        operation: ReplayOperation,
        task: asyncio.Task[None],
    ) -> None:
        """coroutine 시작 전 취소도 CANCELLING에 남지 않게 확정한다."""

        self._tasks.pop(operation.operation_id, None)
        if task.cancelled() and operation.state not in TERMINAL_STATES:
            self._transition(
                operation,
                ReplayOperationState.CANCELLED,
                "저장 Run 검증을 취소했습니다",
            )
