"""여덟 결정적 PAPER 전략, 레지스트리와 공통 후보 계약을 공개한다."""

from backend.app.strategies.aggressor_flow import (
    AggressorFlowContext,
    AggressorFlowStrategy,
)
from backend.app.strategies.base import CandidateDecision, PlanInputs
from backend.app.strategies.compression_breakout import (
    CompressionBreakoutContext,
    CompressionBreakoutStrategy,
)
from backend.app.strategies.depth_adjusted_ofi import (
    DepthAdjustedOfiContext,
    DepthAdjustedOfiStrategy,
    depth_adjusted_ofi_ready,
)
from backend.app.strategies.liquidity_sweep import (
    LiquiditySweepContext,
    LiquiditySweepStrategy,
)
from backend.app.strategies.multilevel_microprice import (
    MultilevelMicropriceContext,
    MultilevelMicropriceStrategy,
    multilevel_alignment_ready,
)
from backend.app.strategies.ofi_pullback import OfiPullbackContext, OfiPullbackStrategy
from backend.app.strategies.queue_microprice import (
    QueueMicropriceContext,
    QueueMicropriceStrategy,
)
from backend.app.strategies.registry import ExitStyle, StrategyMode, StrategyRegistry
from backend.app.strategies.vwap_exhaustion import (
    VwapExhaustionContext,
    VwapExhaustionStrategy,
)

__all__ = [
    "CandidateDecision",
    "AggressorFlowContext",
    "AggressorFlowStrategy",
    "CompressionBreakoutContext",
    "CompressionBreakoutStrategy",
    "DepthAdjustedOfiContext",
    "DepthAdjustedOfiStrategy",
    "LiquiditySweepContext",
    "LiquiditySweepStrategy",
    "MultilevelMicropriceContext",
    "MultilevelMicropriceStrategy",
    "PlanInputs",
    "OfiPullbackContext",
    "OfiPullbackStrategy",
    "QueueMicropriceContext",
    "QueueMicropriceStrategy",
    "ExitStyle",
    "StrategyMode",
    "StrategyRegistry",
    "VwapExhaustionContext",
    "VwapExhaustionStrategy",
    "depth_adjusted_ofi_ready",
    "multilevel_alignment_ready",
]
