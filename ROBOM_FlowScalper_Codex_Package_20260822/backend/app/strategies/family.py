# 전략 family·role·variant·승계 메타데이터와 읽기 전용 catalog를 제공한다.
"""개별 전략 ID를 보존하면서 사용자·governor 계약을 family 중심으로 고정한다."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.strategies.registry import StrategyRegistry


class StrategyFamilyId(StrEnum):
    TREND_PULLBACK = "TREND_PULLBACK"
    BREAKOUT_RUNNER = "BREAKOUT_RUNNER"
    ORDERFLOW_CONFIRMATION = "ORDERFLOW_CONFIRMATION"
    EXHAUSTION_REVERSION = "EXHAUSTION_REVERSION"
    POSITIONING_LIQUIDATION = "POSITIONING_LIQUIDATION"
    MARKET_REGIME_FILTERS = "MARKET_REGIME_FILTERS"
    SESSION_PROFILE = "SESSION_PROFILE"
    MARKET_NEUTRAL = "MARKET_NEUTRAL"


class StrategyRole(StrEnum):
    ENTRY = "ENTRY"
    FILTER = "FILTER"
    ROUTER = "ROUTER"
    MARKET_NEUTRAL_MULTI_LEG = "MARKET_NEUTRAL_MULTI_LEG"
    LEGACY = "LEGACY"


@dataclass(frozen=True, slots=True)
class StrategyFamilyCatalogEntry:
    family_id: StrategyFamilyId
    label_ko: str
    category_ko: str
    description_ko: str
    display_order: int
    current_variant_id: str | None


@dataclass(frozen=True, slots=True)
class StrategyVariantContract:
    strategy_id: str
    family_id: StrategyFamilyId
    role: StrategyRole
    variant_id: str
    variant_label_ko: str
    is_current_variant: bool
    supersedes_strategy_ids: tuple[str, ...]
    superseded_by_strategy_id: str | None
    user_visible_by_default: bool
    default_research_enabled: bool
    final_ranking_eligible: bool


FAMILY_CATALOG = (
    StrategyFamilyCatalogEntry(
        StrategyFamilyId.TREND_PULLBACK,
        "추세 눌림·재합류",
        "방향성 진입",
        "상위 추세의 눌림과 재합류를 비용후 확인하는 entry family입니다.",
        1,
        "TREND_PULLBACK_RECLAIM_15M_V2",
    ),
    StrategyFamilyCatalogEntry(
        StrategyFamilyId.BREAKOUT_RUNNER,
        "돌파·큰 추세",
        "방향성 진입",
        "돌파와 재확인 뒤 비대칭 payoff를 추구하는 entry family입니다.",
        2,
        "BREAKOUT_RETEST_30M_V2",
    ),
    StrategyFamilyCatalogEntry(
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        "주문흐름 실행확인",
        "실행 확인",
        "OFI·체결·호가 구성요소를 직접 주문이 아닌 확인 점수로 결합합니다.",
        3,
        None,
    ),
    StrategyFamilyCatalogEntry(
        StrategyFamilyId.EXHAUSTION_REVERSION,
        "소진·평균복귀",
        "평균회귀 진입",
        "과잉 이탈 뒤 소진과 구조 재진입을 확인하는 entry family입니다.",
        4,
        "VWAP_EXHAUSTION_REVERSION_V1",
    ),
    StrategyFamilyCatalogEntry(
        StrategyFamilyId.POSITIONING_LIQUIDATION,
        "파생 포지셔닝·청산",
        "시장 필터",
        "OI·funding·basis·청산을 allow·veto·quality 신호로 제공합니다.",
        5,
        None,
    ),
    StrategyFamilyCatalogEntry(
        StrategyFamilyId.MARKET_REGIME_FILTERS,
        "시장 레짐 필터",
        "시장 필터",
        "추세·범위·디레버리징 상태에 맞지 않는 entry를 차단합니다.",
        6,
        None,
    ),
    StrategyFamilyCatalogEntry(
        StrategyFamilyId.SESSION_PROFILE,
        "세션·Volume Profile",
        "세션 구조",
        "POC·VAH·VAL과 세션 구조 후보를 검증 전 연구 상태로 유지합니다.",
        7,
        None,
    ),
    StrategyFamilyCatalogEntry(
        StrategyFamilyId.MARKET_NEUTRAL,
        "시장중립",
        "시장중립",
        "방향전략과 분리된 multi-leg PAPER 후보를 위한 family입니다.",
        8,
        None,
    ),
)
FAMILY_CATALOG_BY_ID = {entry.family_id: entry for entry in FAMILY_CATALOG}


def _variant(
    strategy_id: str,
    family_id: StrategyFamilyId,
    label_ko: str,
    *,
    role: StrategyRole = StrategyRole.ENTRY,
    current: bool = False,
    research_enabled: bool = True,
    ranking_eligible: bool | None = None,
    supersedes: tuple[str, ...] = (),
    superseded_by: str | None = None,
) -> StrategyVariantContract:
    eligible = (
        role is StrategyRole.ENTRY and current if ranking_eligible is None else ranking_eligible
    )
    return StrategyVariantContract(
        strategy_id=strategy_id,
        family_id=family_id,
        role=role,
        variant_id=strategy_id,
        variant_label_ko=label_ko,
        is_current_variant=current,
        supersedes_strategy_ids=supersedes,
        superseded_by_strategy_id=superseded_by,
        user_visible_by_default=current,
        default_research_enabled=research_enabled,
        final_ranking_eligible=eligible,
    )


ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID = "ORDERFLOW_CONFIRMATION_FILTER_V2"
ORDERFLOW_CONFIRMATION_LEGACY_COMPONENT_IDS = (
    "OFI_CONTINUATION_PULLBACK_V1",
    "QUEUE_MICROPRICE_MOMENTUM_V1",
    "AGGRESSOR_FLOW_CONTINUATION_V1",
    "MULTILEVEL_MICROPRICE_MOMENTUM_V1",
    "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
    "OFI_RETURN_CONFLUENCE_V1",
    "BOOK_SLOPE_ASYMMETRY_V1",
)
VIRTUAL_VARIANT_SUPERSESSION_CONTRACTS = {
    ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID: ORDERFLOW_CONFIRMATION_LEGACY_COMPONENT_IDS,
}


STRATEGY_VARIANT_CONTRACTS = {
    "LSA_REVERSAL_V1": _variant(
        "LSA_REVERSAL_V1",
        StrategyFamilyId.EXHAUSTION_REVERSION,
        "급락·급등 쓸기 반전 V1",
        role=StrategyRole.LEGACY,
        research_enabled=False,
    ),
    "CBR_CONTINUATION_V1": _variant(
        "CBR_CONTINUATION_V1",
        StrategyFamilyId.BREAKOUT_RUNNER,
        "압축 돌파 지속 V1",
    ),
    "VWAP_EXHAUSTION_REVERSION_V1": _variant(
        "VWAP_EXHAUSTION_REVERSION_V1",
        StrategyFamilyId.EXHAUSTION_REVERSION,
        "VWAP 소진 반전 V1",
        current=True,
    ),
    "OFI_CONTINUATION_PULLBACK_V1": _variant(
        "OFI_CONTINUATION_PULLBACK_V1",
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        "OFI 눌림 지속 V1",
        role=StrategyRole.LEGACY,
        research_enabled=False,
        superseded_by=ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
    ),
    "QUEUE_MICROPRICE_MOMENTUM_V1": _variant(
        "QUEUE_MICROPRICE_MOMENTUM_V1",
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        "큐·마이크로프라이스 V1",
        role=StrategyRole.LEGACY,
        research_enabled=False,
        superseded_by=ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
    ),
    "AGGRESSOR_FLOW_CONTINUATION_V1": _variant(
        "AGGRESSOR_FLOW_CONTINUATION_V1",
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        "공격적 체결 지속 V1",
        role=StrategyRole.LEGACY,
        research_enabled=False,
        superseded_by=ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
    ),
    "MULTILEVEL_MICROPRICE_MOMENTUM_V1": _variant(
        "MULTILEVEL_MICROPRICE_MOMENTUM_V1",
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        "다중호가 마이크로프라이스 V1",
        role=StrategyRole.LEGACY,
        research_enabled=False,
        superseded_by=ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
    ),
    "DEPTH_ADJUSTED_OFI_IMPULSE_V1": _variant(
        "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        "깊이보정 OFI V1",
        role=StrategyRole.LEGACY,
        research_enabled=False,
        superseded_by=ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
    ),
    "OFI_RETURN_CONFLUENCE_V1": _variant(
        "OFI_RETURN_CONFLUENCE_V1",
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        "OFI·수익률 합류 V1",
        role=StrategyRole.LEGACY,
        research_enabled=False,
        superseded_by=ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
    ),
    "BOOK_SLOPE_ASYMMETRY_V1": _variant(
        "BOOK_SLOPE_ASYMMETRY_V1",
        StrategyFamilyId.ORDERFLOW_CONFIRMATION,
        "호가 기울기 비대칭 V1",
        role=StrategyRole.LEGACY,
        research_enabled=False,
        superseded_by=ORDERFLOW_CONFIRMATION_FILTER_VIRTUAL_ID,
    ),
    "HOURLY_MOMENTUM_BREAKOUT_V1": _variant(
        "HOURLY_MOMENTUM_BREAKOUT_V1",
        StrategyFamilyId.BREAKOUT_RUNNER,
        "1시간 모멘텀 돌파 V1",
        role=StrategyRole.LEGACY,
        research_enabled=False,
    ),
    "TREND_PULLBACK_RECLAIM_15M_V2": _variant(
        "TREND_PULLBACK_RECLAIM_15M_V2",
        StrategyFamilyId.TREND_PULLBACK,
        "15분 추세 눌림 재상승 V2",
        current=True,
    ),
    "BREAKOUT_RETEST_15M_V2": _variant(
        "BREAKOUT_RETEST_15M_V2",
        StrategyFamilyId.BREAKOUT_RUNNER,
        "15분 돌파 후 재확인 V2",
    ),
    "BREAKOUT_RETEST_30M_V2": _variant(
        "BREAKOUT_RETEST_30M_V2",
        StrategyFamilyId.BREAKOUT_RUNNER,
        "30분 돌파 후 재확인 V2",
        current=True,
    ),
    "MULTISPEED_TREND_RECLAIM_30M_V2": _variant(
        "MULTISPEED_TREND_RECLAIM_30M_V2",
        StrategyFamilyId.TREND_PULLBACK,
        "30분·1시간 추세 재합류 V2",
    ),
}


def strategy_variant_contract(strategy_id: str) -> StrategyVariantContract:
    try:
        return STRATEGY_VARIANT_CONTRACTS[strategy_id]
    except KeyError as error:
        raise ValueError(f"family 계약이 없는 전략입니다: {strategy_id}") from error


def validate_variant_contracts(contracts: Iterable[StrategyVariantContract]) -> None:
    rows = tuple(contracts)
    by_id = {row.strategy_id: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("strategy family 계약에 중복 strategy ID가 있습니다.")
    variant_keys = {(row.family_id, row.variant_id) for row in rows}
    if len(variant_keys) != len(rows):
        raise ValueError("같은 family에 중복 variant ID가 있습니다.")
    for family_id in StrategyFamilyId:
        current = [row for row in rows if row.family_id is family_id and row.is_current_variant]
        if len(current) > 1:
            raise ValueError(f"family current variant는 최대 1개입니다: {family_id.value}")
        challengers = [
            row
            for row in rows
            if row.family_id is family_id
            and row.role is StrategyRole.ENTRY
            and not row.is_current_variant
            and row.superseded_by_strategy_id is None
        ]
        if len(challengers) > 2:
            raise ValueError(f"family challenger는 최대 2개입니다: {family_id.value}")
    live_shadow_entries = [
        row for row in rows if row.role is StrategyRole.ENTRY and row.default_research_enabled
    ]
    if len(live_shadow_entries) > 10:
        raise ValueError("LIVE SHADOW entry variant는 최대 10개입니다.")
    enabled_filters = [
        row
        for row in rows
        if row.role in {StrategyRole.FILTER, StrategyRole.ROUTER} and row.default_research_enabled
    ]
    if len(enabled_filters) > 5:
        raise ValueError("기본 ON filter·router는 최대 5개입니다.")
    enabled_market_neutral = [
        row
        for row in rows
        if row.role is StrategyRole.MARKET_NEUTRAL_MULTI_LEG and row.default_research_enabled
    ]
    if len(enabled_market_neutral) > 3:
        raise ValueError("기본 ON market-neutral variant는 최대 3개입니다.")
    for row in rows:
        if row.user_visible_by_default and not row.is_current_variant:
            raise ValueError(f"사용자 기본 노출 variant는 current여야 합니다: {row.strategy_id}")
        if not row.is_current_variant and row.final_ranking_eligible:
            raise ValueError(
                f"non-current variant는 최종 순위 대상일 수 없습니다: {row.strategy_id}"
            )
        if (
            row.role
            in {
                StrategyRole.FILTER,
                StrategyRole.ROUTER,
                StrategyRole.MARKET_NEUTRAL_MULTI_LEG,
                StrategyRole.LEGACY,
            }
            and row.final_ranking_eligible
        ):
            raise ValueError(
                f"entry가 아닌 전략은 최종 entry 순위에 들어갈 수 없습니다: {row.strategy_id}"
            )
        if row.role is StrategyRole.LEGACY and row.user_visible_by_default:
            raise ValueError(f"legacy 전략은 기본 목록에서 숨겨야 합니다: {row.strategy_id}")
        if row.superseded_by_strategy_id is not None and (
            row.is_current_variant
            or row.user_visible_by_default
            or row.default_research_enabled
            or row.final_ranking_eligible
        ):
            raise ValueError(
                f"superseded 전략은 current·기본 ON·순위 대상일 수 없습니다: {row.strategy_id}"
            )
        for superseded_id in row.supersedes_strategy_ids:
            superseded = by_id.get(superseded_id)
            if superseded is None or superseded.superseded_by_strategy_id != row.strategy_id:
                raise ValueError(
                    f"strategy supersession 연결이 양방향이 아닙니다: {row.strategy_id}"
                )
        if row.superseded_by_strategy_id is not None:
            successor = by_id.get(row.superseded_by_strategy_id)
            virtual_predecessors = VIRTUAL_VARIANT_SUPERSESSION_CONTRACTS.get(
                row.superseded_by_strategy_id,
                (),
            )
            if successor is not None and row.strategy_id in successor.supersedes_strategy_ids:
                continue
            if successor is None and row.strategy_id in virtual_predecessors:
                continue
            raise ValueError(
                f"strategy supersession 연결이 양방향이 아닙니다: {row.strategy_id}"
            )
    for virtual_successor_id, predecessor_ids in VIRTUAL_VARIANT_SUPERSESSION_CONTRACTS.items():
        if virtual_successor_id in by_id:
            raise ValueError(
                f"virtual successor가 registry variant와 충돌합니다: "
                f"{virtual_successor_id}"
            )
        if len(set(predecessor_ids)) != len(predecessor_ids):
            raise ValueError(
                f"virtual successor에 중복 predecessor가 있습니다: "
                f"{virtual_successor_id}"
            )
        for predecessor_id in predecessor_ids:
            predecessor = by_id.get(predecessor_id)
            if (
                predecessor is None
                or predecessor.superseded_by_strategy_id != virtual_successor_id
                or predecessor.role is not StrategyRole.LEGACY
            ):
                raise ValueError(
                    f"virtual strategy supersession 연결이 양방향이 아닙니다: "
                    f"{virtual_successor_id}"
                )


def validate_family_contract(registry: StrategyRegistry) -> dict[str, object]:
    if {entry.family_id for entry in FAMILY_CATALOG} != set(StrategyFamilyId):
        raise ValueError("8개 strategy family catalog가 완전하지 않습니다.")
    if len(FAMILY_CATALOG) != len(StrategyFamilyId):
        raise ValueError("strategy family catalog에 중복 family가 있습니다.")
    if set(registry.strategy_ids) != set(STRATEGY_VARIANT_CONTRACTS):
        raise ValueError("Registry 전략과 family migration manifest가 일치하지 않습니다.")
    contracts = tuple(
        strategy_variant_contract(strategy_id) for strategy_id in registry.strategy_ids
    )
    validate_variant_contracts(contracts)
    for descriptor in (registry.descriptor(strategy_id) for strategy_id in registry.strategy_ids):
        expected = strategy_variant_contract(descriptor.strategy_id)
        for field_name in StrategyVariantContract.__dataclass_fields__:
            if getattr(descriptor, field_name) != getattr(expected, field_name):
                raise ValueError(
                    f"Registry descriptor family 계약이 다릅니다: {descriptor.strategy_id}"
                )
    current_by_family = {
        family_id.value: next(
            (
                row.strategy_id
                for row in contracts
                if row.family_id is family_id and row.is_current_variant
            ),
            None,
        )
        for family_id in StrategyFamilyId
    }
    for family_id, current_id in current_by_family.items():
        catalog_current = FAMILY_CATALOG_BY_ID[StrategyFamilyId(family_id)].current_variant_id
        if current_id != catalog_current:
            raise ValueError(f"family catalog current variant가 descriptor와 다릅니다: {family_id}")
    return {
        "family_count": len(FAMILY_CATALOG),
        "strategy_count": len(contracts),
        "current_by_family": current_by_family,
    }


def strategy_family_catalog(
    registry: StrategyRegistry,
    performance_by_key: Mapping[str, object] | None = None,
    governance_by_id: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    validate_family_contract(registry)
    performance = performance_by_key or {}
    governance = governance_by_id or {}
    rows: list[dict[str, object]] = []
    for family in sorted(FAMILY_CATALOG, key=lambda item: item.display_order):
        variants = []
        for strategy_id in registry.strategy_ids:
            descriptor = registry.descriptor(strategy_id)
            if descriptor.family_id is not family.family_id:
                continue
            variants.append(
                {
                    "strategy_id": strategy_id,
                    "family_id": descriptor.family_id.value,
                    "role": descriptor.role.value,
                    "variant_id": descriptor.variant_id,
                    "variant_label_ko": descriptor.variant_label_ko,
                    "is_current_variant": descriptor.is_current_variant,
                    "supersedes_strategy_ids": list(descriptor.supersedes_strategy_ids),
                    "superseded_by_strategy_id": descriptor.superseded_by_strategy_id,
                    "user_visible_by_default": descriptor.user_visible_by_default,
                    "default_research_enabled": descriptor.default_research_enabled,
                    "final_ranking_eligible": descriptor.final_ranking_eligible,
                    "setting": registry.setting_row(strategy_id),
                    "performance": performance.get(strategy_id),
                    "governance": governance.get(strategy_id),
                }
            )
        variants.sort(
            key=lambda row: (
                not bool(row["is_current_variant"]),
                str(row["variant_id"]),
            )
        )
        rows.append(
            {
                "family_id": family.family_id.value,
                "label_ko": family.label_ko,
                "category_ko": family.category_ko,
                "description_ko": family.description_ko,
                "display_order": family.display_order,
                "current_variant_id": family.current_variant_id,
                "variant_count": len(variants),
                "variants": variants,
            }
        )
    return rows


def family_detail(
    registry: StrategyRegistry,
    family_id: StrategyFamilyId | str,
    performance_by_key: Mapping[str, object] | None = None,
    governance_by_id: Mapping[str, object] | None = None,
) -> dict[str, object]:
    resolved = StrategyFamilyId(str(family_id))
    return next(
        row
        for row in strategy_family_catalog(registry, performance_by_key, governance_by_id)
        if row["family_id"] == resolved.value
    )
