"""호가 깊이를 소진하는 PAPER 실행기와 명시적 수명주기를 공개한다."""

from backend.app.execution.lifecycle import PaperTradeService, PortfolioSet
from backend.app.execution.models import (
    BookSnapshot,
    ExitReason,
    LifecycleState,
    OrderIntent,
    OrderStatus,
    ProtectedPosition,
)
from backend.app.execution.simulator import PaperExecutionEngine
from backend.app.execution.trailing import (
    TrailingActivationNotFeeSafeError,
    TrailingActivationRule,
    TrailingDecision,
    TrailingModel,
    TrailingObservation,
    TrailingPolicy,
    TrailingReference,
    TrailingState,
    TrailingStateMachine,
    TrailingTransition,
    trailing_reference_from_completed_candles,
)

__all__ = [
    "BookSnapshot",
    "ExitReason",
    "LifecycleState",
    "OrderIntent",
    "OrderStatus",
    "PaperExecutionEngine",
    "PaperTradeService",
    "PortfolioSet",
    "ProtectedPosition",
    "TrailingActivationNotFeeSafeError",
    "TrailingActivationRule",
    "TrailingDecision",
    "TrailingModel",
    "TrailingObservation",
    "TrailingPolicy",
    "TrailingReference",
    "TrailingState",
    "TrailingStateMachine",
    "TrailingTransition",
    "trailing_reference_from_completed_candles",
]
