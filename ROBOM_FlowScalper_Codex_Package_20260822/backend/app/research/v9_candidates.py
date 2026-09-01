# V9 전략·라우터·통계 후보를 방향전략 수와 분리해 사전등록한다.

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum


class V9CandidateRole(StrEnum):
    ENTRY = "ENTRY"
    MARKET_NEUTRAL_MULTI_LEG = "MARKET_NEUTRAL_MULTI_LEG"
    ROUTER = "ROUTER"
    RISK_OVERLAY = "RISK_OVERLAY"
    FILTER = "FILTER"
    STATISTICS = "STATISTICS"
    SELECTION = "SELECTION"


class V9Readiness(StrEnum):
    PARTIAL_SOURCE_NOT_CONNECTED = "PARTIAL_SOURCE_NOT_CONNECTED"
    SOURCE_IMPLEMENTED_NOT_CONNECTED = "SOURCE_IMPLEMENTED_NOT_CONNECTED"
    BLOCKED_PREREQUISITE = "BLOCKED_PREREQUISITE"
    BLOCKED_ENGINE = "BLOCKED_ENGINE"


_DIRECTION_ROLES = frozenset({V9CandidateRole.ENTRY})
_NON_ENTRY_ROLES = frozenset(
    {
        V9CandidateRole.ROUTER,
        V9CandidateRole.RISK_OVERLAY,
        V9CandidateRole.FILTER,
        V9CandidateRole.STATISTICS,
        V9CandidateRole.SELECTION,
    }
)


@dataclass(frozen=True, slots=True)
class V9CandidateSpec:
    candidate_id: str
    label_ko: str
    role: V9CandidateRole
    family_id: str | None
    prerequisite_capability_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    readiness: V9Readiness
    monitoring_enabled: bool = True
    entry_enabled: bool = False
    active_enabled: bool = False
    runtime_entry_registered: bool = False
    can_increase_risk: bool = False
    paper_only: bool = True

    def __post_init__(self) -> None:
        if not self.candidate_id or self.candidate_id != self.candidate_id.strip():
            raise ValueError("V9 후보 ID가 필요합니다.")
        if (
            not self.label_ko.strip()
            or not self.prerequisite_capability_ids
            or not self.source_ids
        ):
            raise ValueError("V9 후보 표시명·선행 capability·출처가 필요합니다.")
        if len(set(self.prerequisite_capability_ids)) != len(
            self.prerequisite_capability_ids
        ):
            raise ValueError("V9 후보의 선행 capability를 중복할 수 없습니다.")
        if len(set(self.source_ids)) != len(self.source_ids) or any(
            not source_id.startswith("SRC-") for source_id in self.source_ids
        ):
            raise ValueError("V9 후보의 Source ID는 중복 없는 SRC- 식별자여야 합니다.")
        if self.role in _DIRECTION_ROLES and not self.family_id:
            raise ValueError("방향전략 후보에는 family가 필요합니다.")
        if self.role in _NON_ENTRY_ROLES and self.entry_enabled:
            raise ValueError("Filter·Router·Statistics는 진입을 만들 수 없습니다.")
        if self.active_enabled or self.runtime_entry_registered or self.entry_enabled:
            raise ValueError("검증 전 V9 후보는 runtime 진입이나 ACTIVE가 될 수 없습니다.")
        if self.can_increase_risk:
            raise ValueError("V9 연구 capability는 위험을 늘릴 수 없습니다.")
        if not self.paper_only:
            raise ValueError("V9 후보는 공개시장 PAPER 전용이어야 합니다.")

    @property
    def counts_as_direction_strategy(self) -> bool:
        return self.role in _DIRECTION_ROLES

    @property
    def counts_as_market_neutral_strategy(self) -> bool:
        return self.role is V9CandidateRole.MARKET_NEUTRAL_MULTI_LEG


def v9_candidate_specs() -> tuple[V9CandidateSpec, ...]:
    """사용자 요청대로 연구 관찰은 ON이지만 진입은 차단된 V9 후보를 반환한다."""

    rows = (
        V9CandidateSpec(
            candidate_id="DC_OVERSHOOT_CONTINUATION_V1",
            label_ko="DC Overshoot 추세 지속",
            role=V9CandidateRole.ENTRY,
            family_id="BREAKOUT_RUNNER",
            prerequisite_capability_ids=(
                "V9.DIRECTIONAL_CHANGE_INTRINSIC_TIME",
                "V9.DC_OBSERVED_VS_INFERRED_CONFIRMATION",
                "V9.SEMIVARIANCE_JUMP_ROUTER",
            ),
            source_ids=(
                "SRC-DC-ALGO-TRADING-2022",
                "SRC-DC-ACTUAL-CONFIRMATION-2024",
                "SRC-DC-MULTI-THRESHOLD-2026",
            ),
            readiness=V9Readiness.PARTIAL_SOURCE_NOT_CONNECTED,
        ),
        V9CandidateSpec(
            candidate_id="DC_OVERSHOOT_EXHAUSTION_REVERSAL_V1",
            label_ko="DC Overshoot 소진 반전",
            role=V9CandidateRole.ENTRY,
            family_id="EXHAUSTION_REVERSION",
            prerequisite_capability_ids=(
                "V9.DIRECTIONAL_CHANGE_INTRINSIC_TIME",
                "V9.DC_OBSERVED_VS_INFERRED_CONFIRMATION",
                "V9.HYSTERESIS_NO_TRADE_ZONE",
            ),
            source_ids=(
                "SRC-DC-ACTUAL-CONFIRMATION-2024",
                "SRC-DC-TSFDC-2018",
                "SRC-REALIZED-SEMIVARIANCE-MOMREV-2023",
            ),
            readiness=V9Readiness.PARTIAL_SOURCE_NOT_CONNECTED,
        ),
        V9CandidateSpec(
            candidate_id="COPULA_COINTEGRATED_PAIRS_1H_V2",
            label_ko="Copula 비선형 시장중립 Pairs",
            role=V9CandidateRole.MARKET_NEUTRAL_MULTI_LEG,
            family_id="MARKET_NEUTRAL",
            prerequisite_capability_ids=(
                "V9.COPULA_PAIRS_TAIL_DEPENDENCE",
                "V8.CLUSTER_EXPOSURE",
            ),
            source_ids=("SRC-COPULA-CRYPTO-PAIRS-2025",),
            readiness=V9Readiness.BLOCKED_ENGINE,
        ),
        V9CandidateSpec(
            candidate_id="SEMIVARIANCE_MOMENTUM_REVERSAL_ROUTER_V1",
            label_ko="상승·하락 Semivariance Router",
            role=V9CandidateRole.ROUTER,
            family_id=None,
            prerequisite_capability_ids=("V9.SEMIVARIANCE_JUMP_ROUTER",),
            source_ids=(
                "SRC-REALIZED-SEMIVARIANCE-MOMREV-2023",
                "SRC-BTC-JUMP-STRUCTURAL-BREAK-2020",
                "SRC-CRYPTO-JUMP-TICK-2024",
                "SRC-INTRAWEEK-PERIODICITY-JUMP",
                "SRC-CRYPTO-ASYMMETRIC-PERSISTENCE-2026",
            ),
            readiness=V9Readiness.PARTIAL_SOURCE_NOT_CONNECTED,
        ),
        V9CandidateSpec(
            candidate_id="DOWNSIDE_SEMIVARIANCE_RISK_OVERLAY_V1",
            label_ko="하방 Semivariance 위험축소",
            role=V9CandidateRole.RISK_OVERLAY,
            family_id=None,
            prerequisite_capability_ids=(
                "V9.SEMIVARIANCE_JUMP_ROUTER",
                "V7.RISK_REDUCTION_ONLY",
            ),
            source_ids=(
                "SRC-REALIZED-SEMIVARIANCE-MOMREV-2023",
                "SRC-CRYPTO-ASYMMETRIC-PERSISTENCE-2026",
            ),
            readiness=V9Readiness.PARTIAL_SOURCE_NOT_CONNECTED,
        ),
        V9CandidateSpec(
            candidate_id="HYSTERESIS_SETUP_GATE_V1",
            label_ko="Setup Hysteresis Gate",
            role=V9CandidateRole.FILTER,
            family_id=None,
            prerequisite_capability_ids=("V9.HYSTERESIS_NO_TRADE_ZONE",),
            source_ids=("SRC-DC-MULTI-THRESHOLD-2026",),
            readiness=V9Readiness.SOURCE_IMPLEMENTED_NOT_CONNECTED,
        ),
        V9CandidateSpec(
            candidate_id="EVIDENCE_FRESHNESS_GATE_V1",
            label_ko="최근 근거 신선도 Gate",
            role=V9CandidateRole.SELECTION,
            family_id=None,
            prerequisite_capability_ids=("V9.EVIDENCE_FRESHNESS",),
            source_ids=(
                "SRC-EVALUE-DYNAMIC-VOLATILITY-2025",
                "SRC-ANYTIME-VALID-2026",
            ),
            readiness=V9Readiness.SOURCE_IMPLEMENTED_NOT_CONNECTED,
        ),
        V9CandidateSpec(
            candidate_id="HIERARCHICAL_PERFORMANCE_SHRINKAGE_V1",
            label_ko="계층적 성과 보정",
            role=V9CandidateRole.STATISTICS,
            family_id=None,
            prerequisite_capability_ids=("V9.HIERARCHICAL_SHRINKAGE",),
            source_ids=("SRC-HIERARCHICAL-SHRINKAGE-2013",),
            readiness=V9Readiness.SOURCE_IMPLEMENTED_NOT_CONNECTED,
        ),
        V9CandidateSpec(
            candidate_id="BATCH_FDR_HARVEY_LIU_V1",
            label_ko="Batch FDR 검증",
            role=V9CandidateRole.STATISTICS,
            family_id=None,
            prerequisite_capability_ids=("V9.FDR_CONTROL",),
            source_ids=(
                "SRC-FDR-BH-1995",
                "SRC-FALSE-DISCOVERIES-FINANCE-2020",
                "SRC-PHACKING-TRADING-STRATEGIES",
            ),
            readiness=V9Readiness.BLOCKED_PREREQUISITE,
        ),
        V9CandidateSpec(
            candidate_id="ANYTIME_EPROCESS_V1",
            label_ko="Anytime E-process",
            role=V9CandidateRole.STATISTICS,
            family_id=None,
            prerequisite_capability_ids=("V9.ANYTIME_E_VALUE",),
            source_ids=(
                "SRC-EVALUE-DYNAMIC-VOLATILITY-2025",
                "SRC-ANYTIME-VALID-2026",
            ),
            readiness=V9Readiness.SOURCE_IMPLEMENTED_NOT_CONNECTED,
        ),
        V9CandidateSpec(
            candidate_id="E_BH_STRATEGY_SELECTION_V1",
            label_ko="e-BH 전략 선별",
            role=V9CandidateRole.SELECTION,
            family_id=None,
            prerequisite_capability_ids=("V9.ANYTIME_E_VALUE", "V9.FDR_CONTROL"),
            source_ids=("SRC-E-BH-2022", "SRC-ANYTIME-VALID-2026"),
            readiness=V9Readiness.SOURCE_IMPLEMENTED_NOT_CONNECTED,
        ),
        V9CandidateSpec(
            candidate_id="PARETO_ROBUST_SET_V1",
            label_ko="Pareto 강건 후보집합",
            role=V9CandidateRole.SELECTION,
            family_id=None,
            prerequisite_capability_ids=("V9.PARETO_SELECTION", "V9.FDR_CONTROL"),
            source_ids=("SRC-DC-MULTIOBJECTIVE-2026",),
            readiness=V9Readiness.SOURCE_IMPLEMENTED_NOT_CONNECTED,
        ),
    )
    _validate_candidate_set(rows)
    return rows


def _validate_candidate_set(rows: tuple[V9CandidateSpec, ...]) -> None:
    candidate_ids = [row.candidate_id for row in rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("V9 후보 ID가 중복됐습니다.")
    direction_count = sum(row.counts_as_direction_strategy for row in rows)
    market_neutral_count = sum(row.counts_as_market_neutral_strategy for row in rows)
    filter_router_count = sum(
        row.role
        in {
            V9CandidateRole.FILTER,
            V9CandidateRole.ROUTER,
            V9CandidateRole.RISK_OVERLAY,
        }
        for row in rows
    )
    if direction_count > 10:
        raise ValueError("V9 방향전략 후보는 최대 10개입니다.")
    if market_neutral_count > 3:
        raise ValueError("V9 시장중립 후보는 최대 3개입니다.")
    if filter_router_count > 5:
        raise ValueError("V9 기본 ON Filter·Router·Risk Overlay는 최대 5개입니다.")


def v9_candidate_manifest(*, source_commit: str) -> dict[str, object]:
    """연구 ON과 runtime 진입 OFF를 분리한 기계판독 manifest를 만든다."""

    if not source_commit.strip():
        raise ValueError("source commit이 필요합니다.")
    rows = v9_candidate_specs()
    payload_rows = [
        asdict(row)
        | {
            "role": row.role.value,
            "readiness": row.readiness.value,
            "counts_as_direction_strategy": row.counts_as_direction_strategy,
            "counts_as_market_neutral_strategy": row.counts_as_market_neutral_strategy,
        }
        for row in rows
    ]
    manifest: dict[str, object] = {
        "schema": "flowscalper.v9_candidate_registry.v1",
        "status": "MONITORING_ON_ENTRY_BLOCKED",
        "source_commit": source_commit,
        "candidate_count": len(rows),
        "monitoring_on_count": sum(row.monitoring_enabled for row in rows),
        "direction_strategy_count": sum(
            row.counts_as_direction_strategy for row in rows
        ),
        "market_neutral_strategy_count": sum(
            row.counts_as_market_neutral_strategy for row in rows
        ),
        "runtime_entry_registered_count": sum(
            row.runtime_entry_registered for row in rows
        ),
        "active_count": sum(row.active_enabled for row in rows),
        "entry_enabled_count": sum(row.entry_enabled for row in rows),
        "candidates": payload_rows,
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "api_key_enabled": False,
        "wallet_enabled": False,
        "runtime_ai_order_decision_enabled": False,
        "funding_readiness": "NOT_READY",
    }
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return manifest


__all__ = [
    "V9CandidateRole",
    "V9CandidateSpec",
    "V9Readiness",
    "v9_candidate_manifest",
    "v9_candidate_specs",
]
