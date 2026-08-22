"""시장 이벤트와 런타임 상태의 불변 도메인 모델을 정의한다."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuntimeMode(StrEnum):
    FIXTURE_OFFLINE = "FIXTURE_OFFLINE"
    LIVE_SHADOW_PAPER = "LIVE_SHADOW_PAPER"
    REPLAY = "REPLAY"


class Venue(StrEnum):
    BINANCE_USDM = "BINANCE_USDM"
    BYBIT_LINEAR = "BYBIT_LINEAR"
    FIXTURE = "FIXTURE"


class MarketDataState(StrEnum):
    LIVE = "LIVE"
    RECONNECTING = "RECONNECTING"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    FIXTURE = "FIXTURE"


class ExecutionState(StrEnum):
    PAPER = "PAPER"


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class DataQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_live: bool
    is_stale: bool
    sequence_valid: bool
    lag_ms: float | None = Field(default=None, ge=0)
    flags: tuple[str, ...] = ()


class MarketEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    run_id: str
    venue: Venue
    symbol: str
    event_type: str
    venue_ts_ms: int = Field(ge=0)
    transaction_ts_ms: int | None = Field(default=None, ge=0)
    receive_monotonic_ns: int = Field(ge=0)
    sequence_start: int | None = None
    sequence_end: int | None = None
    previous_sequence_end: int | None = None
    payload_version: str = "1"
    quality: DataQuality
    data: dict[str, Any]


class SystemStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: RuntimeMode
    market_data_state: MarketDataState
    execution_state: ExecutionState = ExecutionState.PAPER
    venue: Venue
    run_id: str
    real_orders_enabled: bool = False
    auth_required: bool = False
    starting_equity_usdt: float = Field(default=1000.0, ge=0)
    current_equity_usdt: float = 1000.0
    wide_symbols: int = Field(default=0, ge=0)
    deep_symbols: int = Field(default=0, ge=0)
    processing_lag_p95_ms: float | None = Field(default=None, ge=0)
    health_flags: tuple[str, ...] = ()
