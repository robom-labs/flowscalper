"""PAPER Run 제어를 즉시 응답하는 취소 가능한 background 작업으로 관리한다."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import uuid4

ClockMs = Callable[[], int]
ProgressCallback = Callable[[str, str], Awaitable[None]]
ControlRunner = Callable[[ProgressCallback], Awaitable[None]]


class ControlAction(StrEnum):
    START_LIVE = "START_LIVE"
    START_DEMO = "START_DEMO"
    NEW_RUN = "NEW_RUN"


class ControlState(StrEnum):
    REQUESTED = "REQUESTED"
    PREPARING = "PREPARING"
    CONNECTING_PRIMARY = "CONNECTING_PRIMARY"
    CONNECTING_FALLBACK = "CONNECTING_FALLBACK"
    COMPLETED = "COMPLETED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_BLOCKED = "FAILED_BLOCKED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = {
    ControlState.COMPLETED,
    ControlState.FAILED_RETRYABLE,
    ControlState.FAILED_BLOCKED,
    ControlState.CANCELLED,
}


class ControlOperationConflict(RuntimeError):
    def __init__(self, current_operation: dict[str, object]) -> None:
        super().__init__("다른 PAPER 실행 작업이 진행 중입니다.")
        self.current_operation = current_operation


class ControlOperationFailure(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message_ko: str,
        retryable: bool,
    ) -> None:
        super().__init__(message_ko)
        self.code = code
        self.message_ko = message_ko
        self.retryable = retryable


@dataclass(slots=True)
class ControlOperation:
    operation_id: str
    action: ControlAction
    state: ControlState
    stage_ko: str
    started_ts_ms: int
    updated_ts_ms: int
    finished_ts_ms: int | None = None
    retryable: bool = False
    error_code: str | None = None
    error_message_ko: str | None = None
    history: list[dict[str, object]] = field(default_factory=list)

    def public(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "action": self.action.value,
            "state": self.state.value,
            "stage_ko": self.stage_ko,
            "started_ts_ms": self.started_ts_ms,
            "updated_ts_ms": self.updated_ts_ms,
            "finished_ts_ms": self.finished_ts_ms,
            "retryable": self.retryable,
            "error_code": self.error_code,
            "error_message_ko": self.error_message_ko,
            "history": [dict(item) for item in self.history],
        }


class ControlOperationManager:
    """동시에 하나의 장시간 Run 변경만 허용하고 상태 이력을 보존한다."""

    def __init__(self, clock_ms: ClockMs) -> None:
        self._clock_ms = clock_ms
        self._lock = asyncio.Lock()
        self._current: ControlOperation | None = None
        self._operations: dict[str, ControlOperation] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def current_public(self) -> dict[str, object] | None:
        return self._current.public() if self._current is not None else None

    def get_public(self, operation_id: str) -> dict[str, object] | None:
        operation = self._operations.get(operation_id)
        return operation.public() if operation is not None else None

    async def submit(
        self,
        action: ControlAction,
        runner: ControlRunner,
    ) -> dict[str, object]:
        async with self._lock:
            current = self._current
            if current is not None and current.state not in TERMINAL_STATES:
                if current.action is action:
                    return current.public()
                raise ControlOperationConflict(current.public())
            now = self._clock_ms()
            operation = ControlOperation(
                operation_id=f"control-{uuid4().hex}",
                action=action,
                state=ControlState.REQUESTED,
                stage_ko="요청을 받았습니다",
                started_ts_ms=now,
                updated_ts_ms=now,
                history=[
                    {
                        "state": ControlState.REQUESTED.value,
                        "stage_ko": "요청을 받았습니다",
                        "ts_ms": now,
                    }
                ],
            )
            self._current = operation
            self._operations[operation.operation_id] = operation
            task = asyncio.create_task(
                self._run(operation, runner),
                name=f"{operation.action.value.lower()}-{operation.operation_id}",
            )
            self._tasks[operation.operation_id] = task
            task.add_done_callback(lambda _: self._tasks.pop(operation.operation_id, None))
            return operation.public()

    async def cancel(self, operation_id: str) -> dict[str, object] | None:
        async with self._lock:
            operation = self._operations.get(operation_id)
            if operation is None:
                return None
            if operation.state in TERMINAL_STATES:
                return operation.public()
            self._transition(operation, ControlState.CANCELLING, "연결 작업을 취소하고 있습니다")
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

    async def _run(self, operation: ControlOperation, runner: ControlRunner) -> None:
        async def progress(state: str, stage_ko: str) -> None:
            self._transition(operation, ControlState(state), stage_ko)

        try:
            await runner(progress)
        except asyncio.CancelledError:
            self._transition(operation, ControlState.CANCELLED, "연결 작업을 취소했습니다")
        except ControlOperationFailure as error:
            self._fail(operation, error)
        except Exception as error:  # 작업 예외를 유실하지 않고 안전한 오류 상태로 바꾼다.
            self._fail(
                operation,
                ControlOperationFailure(
                    code=f"CONTROL_{type(error).__name__.upper()}",
                    message_ko="PAPER 실행 작업을 완료하지 못했습니다. 다시 시도하세요.",
                    retryable=True,
                ),
            )
        else:
            self._transition(operation, ControlState.COMPLETED, self._completed_stage(operation))

    def _fail(self, operation: ControlOperation, error: ControlOperationFailure) -> None:
        operation.error_code = error.code
        operation.error_message_ko = error.message_ko
        operation.retryable = error.retryable
        state = (
            ControlState.FAILED_RETRYABLE
            if error.retryable
            else ControlState.FAILED_BLOCKED
        )
        self._transition(operation, state, error.message_ko)

    def _transition(
        self,
        operation: ControlOperation,
        state: ControlState,
        stage_ko: str,
    ) -> None:
        if operation.state in TERMINAL_STATES:
            return
        now = self._clock_ms()
        operation.state = state
        operation.stage_ko = stage_ko
        operation.updated_ts_ms = now
        if state in TERMINAL_STATES:
            operation.finished_ts_ms = now
        operation.history.append(
            {"state": state.value, "stage_ko": stage_ko, "ts_ms": now}
        )
        operation.history[:] = operation.history[-20:]

    @staticmethod
    def _completed_stage(operation: ControlOperation) -> str:
        if operation.action is ControlAction.START_DEMO:
            return "샘플 화면을 열었습니다. 실제 LIVE 데이터가 아닙니다."
        if operation.action is ControlAction.NEW_RUN:
            return "기존 기록을 보존하고 새 PAPER Run을 시작했습니다"
        return "자동 관찰을 시작했습니다"
