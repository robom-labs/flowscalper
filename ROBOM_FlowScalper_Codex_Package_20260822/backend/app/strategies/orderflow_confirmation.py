# 주문흐름 구성요소를 직접 주문 없이 순수 confirmation score로 평가한다.
"""V6 주문흐름 FILTER의 사전등록 가중치와 결정 계약을 제공한다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from threading import RLock
from typing import Literal

from backend.app.domain.models import Side
from backend.app.features import FeatureSnapshot
from backend.app.strategies.family import StrategyRole

ORDERFLOW_CONFIRMATION_FILTER_ID = "ORDERFLOW_CONFIRMATION_FILTER_V2"
ORDERFLOW_CONFIRMATION_THRESHOLD = Decimal("0.65")
ORDERFLOW_COMPONENT_PASS_THRESHOLD = Decimal("0.50")
ORDERFLOW_MINIMUM_PASSED_COMPONENTS = 3
ORDERFLOW_MINIMUM_PERSISTENCE_MS = 500
ORDERFLOW_MAXIMUM_OBSERVATION_GAP_MS = 500
ORDERFLOW_COMPONENT_WEIGHTS = (
    ("normalized_ofi", Decimal("0.20")),
    ("aggressor_imbalance", Decimal("0.15")),
    ("microprice_displacement", Decimal("0.15")),
    ("multilevel_fair_price_displacement", Decimal("0.10")),
    ("queue_imbalance", Decimal("0.10")),
    ("book_slope", Decimal("0.10")),
    ("depth_adjusted_price_response", Decimal("0.10")),
    ("spread_health", Decimal("0.05")),
    ("book_resilience", Decimal("0.05")),
)
ORDERFLOW_DIRECTIONAL_COMPONENT_NAMES = tuple(
    component_name
    for component_name, _ in ORDERFLOW_COMPONENT_WEIGHTS
    if component_name not in {"spread_health", "book_resilience"}
)
ORDERFLOW_AFFECTED_STRATEGY_IDS = (
    "TREND_PULLBACK_RECLAIM_15M_V2",
    "BREAKOUT_RETEST_30M_V2",
)


@dataclass(frozen=True, slots=True)
class OrderflowConfirmationInputs:
    normalized_ofi: Decimal
    aggressor_imbalance: Decimal
    microprice_displacement: Decimal
    multilevel_fair_price_displacement: Decimal
    queue_imbalance: Decimal
    book_slope: Decimal
    depth_adjusted_price_response: Decimal
    spread_health: Decimal
    book_resilience: Decimal

    def __post_init__(self) -> None:
        for component_name, _ in ORDERFLOW_COMPONENT_WEIGHTS:
            value = getattr(self, component_name)
            if not value.is_finite() or not Decimal(0) <= value <= Decimal(1):
                raise ValueError(f"주문흐름 구성요소는 0~1 범위여야 합니다: {component_name}")


@dataclass(frozen=True, slots=True)
class OrderflowConfirmationDecision:
    filter_id: str
    role: StrategyRole
    score: Decimal
    passed_component_count: int
    persistence_ms: int
    allowed: bool
    reason_codes: tuple[str, ...]
    creates_candidate_plan: Literal[False] = False

    def as_dict(self) -> dict[str, object]:
        return {
            "filter_id": self.filter_id,
            "role": self.role.value,
            "score": str(self.score),
            "passed_component_count": self.passed_component_count,
            "persistence_ms": self.persistence_ms,
            "allowed": self.allowed,
            "reason_codes": list(self.reason_codes),
            "creates_candidate_plan": self.creates_candidate_plan,
        }


class OrderflowFilterRevisionConflict(RuntimeError):
    def __init__(self, current: dict[str, object]) -> None:
        super().__init__("주문흐름 필터 revision이 최신 상태와 다릅니다.")
        self.current = current


@dataclass(slots=True)
class OrderflowConfirmationRuntime:
    """방향별 500ms 지속상태와 사용자 ON/OFF 설정을 PAPER 메모리에 보존한다."""

    enabled: bool = False
    revision: int = 0
    updated_ts_ms: int = 0
    change_reason: str = "SAFE_DEFAULT_UNVALIDATED_FILTER_OFF"
    _streak_started_ts_ms: dict[tuple[str, Side], int] = field(
        default_factory=dict,
        repr=False,
    )
    _last_evaluated_ts_ms: dict[tuple[str, Side], int] = field(
        default_factory=dict,
        repr=False,
    )
    _latest: dict[tuple[str, Side], OrderflowConfirmationDecision] = field(
        default_factory=dict,
        repr=False,
    )
    _latest_inputs: dict[tuple[str, Side], OrderflowConfirmationInputs] = field(
        default_factory=dict,
        repr=False,
    )
    _latest_snapshots: dict[tuple[str, Side], FeatureSnapshot] = field(
        default_factory=dict,
        repr=False,
    )
    _lock: RLock = field(default_factory=RLock, repr=False)

    def configure(
        self,
        *,
        enabled: bool,
        expected_revision: int,
        updated_ts_ms: int,
        reason: str,
    ) -> dict[str, object]:
        with self._lock:
            if expected_revision < 0:
                raise ValueError("주문흐름 필터 expected revision은 음수일 수 없습니다.")
            if updated_ts_ms < 0:
                raise ValueError("주문흐름 필터 갱신 시각은 음수일 수 없습니다.")
            if expected_revision != self.revision:
                raise OrderflowFilterRevisionConflict(self.status())
            if enabled != self.enabled:
                self.enabled = enabled
                self.revision += 1
                self.updated_ts_ms = updated_ts_ms
                self.change_reason = reason
                self._clear_observations()
            return self.status()

    def evaluate(
        self,
        snapshot: FeatureSnapshot,
        side: Side,
    ) -> OrderflowConfirmationDecision:
        with self._lock:
            key = (snapshot.symbol, side)
            last_ts_ms = self._last_evaluated_ts_ms.get(key)
            if last_ts_ms is not None and snapshot.ts_ms < last_ts_ms:
                raise ValueError("주문흐름 필터 snapshot 시각은 뒤로 갈 수 없습니다.")
            if last_ts_ms == snapshot.ts_ms and key in self._latest:
                if self._latest_snapshots.get(key) != snapshot:
                    raise ValueError(
                        "같은 시각 주문흐름 snapshot 내용 또는 data health가 다릅니다."
                    )
                return self._latest[key]

            if (
                last_ts_ms is not None
                and snapshot.ts_ms - last_ts_ms > ORDERFLOW_MAXIMUM_OBSERVATION_GAP_MS
            ):
                self._streak_started_ts_ms.pop(key, None)

            inputs = orderflow_inputs_from_snapshot(snapshot, side)
            preliminary = evaluate_orderflow_confirmation(
                inputs,
                persistence_ms=0,
                data_healthy=snapshot.data_healthy,
            )
            preliminary_passed = not any(
                reason
                in {
                    "ORDERFLOW_DATA_UNHEALTHY",
                    "ORDERFLOW_SCORE_LT_0_65",
                    "ORDERFLOW_INDEPENDENT_COMPONENTS_LT_3",
                }
                for reason in preliminary.reason_codes
            )
            if preliminary_passed:
                started_ts_ms = self._streak_started_ts_ms.setdefault(key, snapshot.ts_ms)
                persistence_ms = snapshot.ts_ms - started_ts_ms
            else:
                self._streak_started_ts_ms.pop(key, None)
                persistence_ms = 0
            decision = evaluate_orderflow_confirmation(
                inputs,
                persistence_ms=persistence_ms,
                data_healthy=snapshot.data_healthy,
            )
            self._last_evaluated_ts_ms[key] = snapshot.ts_ms
            self._latest[key] = decision
            self._latest_inputs[key] = inputs
            self._latest_snapshots[key] = snapshot
            return decision

    def allows_strategy(self, strategy_id: str, side: Side, symbol: str) -> bool:
        with self._lock:
            if not self.enabled or strategy_id not in ORDERFLOW_AFFECTED_STRATEGY_IDS:
                return True
            decision = self._latest.get((symbol, side))
            return decision is not None and decision.allowed

    def decision_for(self, symbol: str, side: Side) -> OrderflowConfirmationDecision | None:
        with self._lock:
            return self._latest.get((symbol, side))

    def status(self, *, symbol: str | None = None) -> dict[str, object]:
        with self._lock:
            latest = [
                {
                    "symbol": row_symbol,
                    "side": side.value,
                    **decision.as_dict(),
                    "components": {
                        component_name: str(
                            getattr(
                                self._latest_inputs[(row_symbol, side)],
                                component_name,
                            )
                        )
                        for component_name, _ in ORDERFLOW_COMPONENT_WEIGHTS
                    },
                    "data_health": (
                        "HEALTHY"
                        if "ORDERFLOW_DATA_UNHEALTHY" not in decision.reason_codes
                        else "UNHEALTHY"
                    ),
                }
                for (row_symbol, side), decision in sorted(
                    self._latest.items(),
                    key=lambda item: (item[0][0], item[0][1].value),
                )
                if symbol is None or row_symbol == symbol
            ]
            return {
                "filter_id": ORDERFLOW_CONFIRMATION_FILTER_ID,
                "family_id": "ORDERFLOW_CONFIRMATION",
                "role": StrategyRole.FILTER.value,
                "enabled": self.enabled,
                "revision": self.revision,
                "updated_ts_ms": self.updated_ts_ms,
                "change_reason": self.change_reason,
                "threshold": str(ORDERFLOW_CONFIRMATION_THRESHOLD),
                "component_pass_threshold": str(ORDERFLOW_COMPONENT_PASS_THRESHOLD),
                "minimum_passed_components": ORDERFLOW_MINIMUM_PASSED_COMPONENTS,
                "minimum_persistence_ms": ORDERFLOW_MINIMUM_PERSISTENCE_MS,
                "maximum_observation_gap_ms": ORDERFLOW_MAXIMUM_OBSERVATION_GAP_MS,
                "independent_component_ids": list(
                    ORDERFLOW_DIRECTIONAL_COMPONENT_NAMES
                ),
                "affected_strategy_ids": list(ORDERFLOW_AFFECTED_STRATEGY_IDS),
                "latest": latest,
                "uplift_status": "NOT_PROVEN_NO_PAIRED_FILTER_SAMPLE",
                "creates_candidate_plan": False,
                "trade_count_delta": 0,
                "account_count_delta": 0,
                "paper_only": True,
            }

    def recovery_state(self) -> dict[str, object]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "revision": self.revision,
                "updated_ts_ms": self.updated_ts_ms,
                "change_reason": self.change_reason,
            }

    def restore_state(self, payload: dict[str, object]) -> None:
        with self._lock:
            revision = _non_negative_state_int(
                payload.get("revision"),
                field_name="revision",
            )
            updated_ts_ms = _non_negative_state_int(
                payload.get("updated_ts_ms"),
                field_name="updated_ts_ms",
            )
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("복구 주문흐름 필터 enabled는 boolean이어야 합니다.")
            change_reason = payload.get("change_reason")
            if not isinstance(change_reason, str) or not change_reason.strip():
                raise ValueError(
                    "복구 주문흐름 필터 change_reason은 빈 문자열이어서는 안 됩니다."
                )
            if revision < self.revision:
                return
            if revision == self.revision:
                if (
                    enabled == self.enabled
                    and updated_ts_ms == self.updated_ts_ms
                    and change_reason == self.change_reason
                ):
                    return
                raise ValueError(
                    "동일 revision 주문흐름 복구 상태가 현재 상태와 다릅니다."
                )
            if updated_ts_ms < self.updated_ts_ms:
                raise ValueError(
                    "새 revision 주문흐름 복구 시각이 현재 상태보다 이전입니다."
                )
            self.enabled = enabled
            self.revision = revision
            self.updated_ts_ms = updated_ts_ms
            self.change_reason = change_reason
            self._clear_observations()

    def clear_observations(self) -> None:
        with self._lock:
            self._clear_observations()

    def reset_configuration(self) -> None:
        with self._lock:
            self.enabled = False
            self.revision = 0
            self.updated_ts_ms = 0
            self.change_reason = "SAFE_DEFAULT_UNVALIDATED_FILTER_OFF"
            self._clear_observations()

    def _clear_observations(self) -> None:
        self._streak_started_ts_ms.clear()
        self._last_evaluated_ts_ms.clear()
        self._latest.clear()
        self._latest_inputs.clear()
        self._latest_snapshots.clear()


def orderflow_confirmation_score(inputs: OrderflowConfirmationInputs) -> Decimal:
    return sum(
        (
            getattr(inputs, component_name) * weight
            for component_name, weight in ORDERFLOW_COMPONENT_WEIGHTS
        ),
        start=Decimal(0),
    )


def orderflow_inputs_from_snapshot(
    snapshot: FeatureSnapshot,
    side: Side,
) -> OrderflowConfirmationInputs:
    """고정된 방향·scale 변환으로 실행 피처를 0~1 구성요소에 매핑한다."""

    direction = Decimal(1) if side is Side.LONG else Decimal(-1)

    def directional(value: float, scale: str) -> Decimal:
        normalized = (Decimal(1) + direction * Decimal(str(value)) / Decimal(scale)) / Decimal(2)
        return _clamp_unit(normalized)

    bid_slope = Decimal(str(snapshot.bid_book_slope_10))
    ask_slope = Decimal(str(snapshot.ask_book_slope_10))
    slope_total = abs(bid_slope) + abs(ask_slope)
    slope_imbalance = (bid_slope - ask_slope) / slope_total if slope_total > 0 else Decimal(0)
    support_refill = (
        snapshot.bid_refill_ratio_3s if side is Side.LONG else snapshot.ask_refill_ratio_3s
    )
    opposite_cancel = (
        snapshot.ask_cancel_ratio_3s if side is Side.LONG else snapshot.bid_cancel_ratio_3s
    )
    price_response = snapshot.depth_adjusted_ofi_3s_bps * min(
        1.0,
        max(0.0, snapshot.price_response_efficiency),
    )
    return OrderflowConfirmationInputs(
        normalized_ofi=directional(snapshot.depth_adjusted_ofi_3s_bps, "5"),
        aggressor_imbalance=directional(snapshot.trade_imbalance_3s, "1"),
        microprice_displacement=directional(snapshot.microprice_minus_mid_bps, "2"),
        multilevel_fair_price_displacement=directional(
            snapshot.multi_level_microprice_10_minus_mid_bps,
            "2",
        ),
        queue_imbalance=directional(snapshot.imbalance_top10, "1"),
        book_slope=_clamp_unit((Decimal(1) + direction * slope_imbalance) / Decimal(2)),
        depth_adjusted_price_response=directional(price_response, "2"),
        spread_health=_clamp_unit(
            Decimal(1) - Decimal(str(snapshot.spread_bps)) / Decimal("8")
        ),
        book_resilience=_clamp_unit(
            (Decimal(str(support_refill)) + Decimal(str(opposite_cancel))) / Decimal(2)
        ),
    )


def _clamp_unit(value: Decimal) -> Decimal:
    return min(Decimal(1), max(Decimal(0), value))


def evaluate_orderflow_confirmation(
    inputs: OrderflowConfirmationInputs,
    *,
    persistence_ms: int,
    data_healthy: bool = True,
) -> OrderflowConfirmationDecision:
    if persistence_ms < 0:
        raise ValueError("주문흐름 확인 지속시간은 음수일 수 없습니다.")
    score = orderflow_confirmation_score(inputs)
    passed_component_count = sum(
        getattr(inputs, component_name) > ORDERFLOW_COMPONENT_PASS_THRESHOLD
        for component_name in ORDERFLOW_DIRECTIONAL_COMPONENT_NAMES
    )
    failures = tuple(
        reason
        for passed, reason in (
            (data_healthy, "ORDERFLOW_DATA_UNHEALTHY"),
            (score >= ORDERFLOW_CONFIRMATION_THRESHOLD, "ORDERFLOW_SCORE_LT_0_65"),
            (
                passed_component_count >= ORDERFLOW_MINIMUM_PASSED_COMPONENTS,
                "ORDERFLOW_INDEPENDENT_COMPONENTS_LT_3",
            ),
            (
                persistence_ms >= ORDERFLOW_MINIMUM_PERSISTENCE_MS,
                "ORDERFLOW_PERSISTENCE_LT_500_MS",
            ),
        )
        if not passed
    )
    return OrderflowConfirmationDecision(
        filter_id=ORDERFLOW_CONFIRMATION_FILTER_ID,
        role=StrategyRole.FILTER,
        score=score,
        passed_component_count=passed_component_count,
        persistence_ms=persistence_ms,
        allowed=not failures,
        reason_codes=failures or ("ORDERFLOW_CONFIRMATION_PASSED",),
    )


def _non_negative_state_int(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"복구 주문흐름 필터 {field_name}는 정수여야 합니다.")
    if value < 0:
        raise ValueError(
            f"복구 주문흐름 필터 {field_name}는 음수일 수 없습니다."
        )
    return value
