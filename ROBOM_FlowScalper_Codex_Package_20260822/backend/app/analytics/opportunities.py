# BASE·STRESS와 partial-exit 원장행을 고유 시장기회 단위로 묶는다.
"""V6 고유기회 키, 비용 profile 집계, Wilson 하한 helper를 제공한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

WILSON_Z_95 = Decimal("1.959963984540054")


@dataclass(frozen=True, order=True, slots=True)
class OpportunityKey:
    run_id: str
    strategy_id: str
    strategy_version: str
    opportunity_id: str
    symbol: str
    side: str

    def as_tuple(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.run_id,
            self.strategy_id,
            self.strategy_version,
            self.opportunity_id,
            self.symbol,
            self.side,
        )


@dataclass(frozen=True, slots=True)
class OpportunityAccountGroup:
    account_scope: str
    account_id: str
    rows: tuple[Mapping[str, object], ...]
    profiles: tuple[str, ...]
    profile_row_counts: tuple[tuple[str, int], ...]
    raw_result_row_count: int
    base_result_row_count: int
    stress_result_row_count: int
    partial_exit_row_count: int


@dataclass(frozen=True, slots=True)
class OpportunityGroup:
    key: OpportunityKey
    rows: tuple[Mapping[str, object], ...]
    accounts: tuple[OpportunityAccountGroup, ...]
    profiles: tuple[str, ...]
    profile_row_counts: tuple[tuple[str, int], ...]
    raw_result_row_count: int
    base_result_row_count: int
    stress_result_row_count: int
    partial_exit_row_count: int


@dataclass(frozen=True, slots=True)
class UnresolvedOpportunityRow:
    row: Mapping[str, object]
    status: str
    reason_code: str
    reason_ko: str


@dataclass(frozen=True, slots=True)
class OpportunityGrouping:
    groups: tuple[OpportunityGroup, ...]
    unresolved_rows: tuple[UnresolvedOpportunityRow, ...]
    unique_opportunity_count: int
    raw_result_row_count: int
    base_result_row_count: int
    stress_result_row_count: int
    unresolved_result_row_count: int


class OpportunityKeyError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def opportunity_key(row: Mapping[str, object]) -> OpportunityKey:
    opportunity_id = _verified_opportunity_identifier(row)
    return OpportunityKey(
        run_id=_required_identifier(row.get("run_id"), "run_id"),
        strategy_id=_required_identifier(
            row.get("strategy_id") or row.get("strategy"), "strategy_id"
        ),
        strategy_version=_required_identifier(row.get("strategy_version"), "strategy_version"),
        opportunity_id=opportunity_id,
        symbol=_required_identifier(row.get("symbol"), "symbol"),
        side=_required_identifier(row.get("side"), "side"),
    )


def group_trade_opportunities(
    rows: Sequence[Mapping[str, object]],
    *,
    strategy_version: str | None = None,
) -> OpportunityGrouping:
    grouped: dict[OpportunityKey, list[Mapping[str, object]]] = {}
    unresolved_rows: list[UnresolvedOpportunityRow] = []
    for row in rows:
        raw_strategy_version = _optional_identifier(row.get("strategy_version"))
        if strategy_version is not None and raw_strategy_version != strategy_version:
            continue
        try:
            key = opportunity_key(row)
            profile = _cost_profile(row)
            _account_identity(row, profile=profile)
        except OpportunityKeyError as error:
            unresolved_rows.append(
                UnresolvedOpportunityRow(
                    row=row,
                    status="NOT_PROVEN",
                    reason_code=error.reason_code,
                    reason_ko=str(error),
                )
            )
            continue
        grouped.setdefault(key, []).append(row)
    groups: list[OpportunityGroup] = []
    for key, result_rows in sorted(grouped.items()):
        profile_counts: dict[str, int] = {}
        account_rows: dict[tuple[str, str], list[Mapping[str, object]]] = {}
        for row in result_rows:
            profile = _cost_profile(row)
            profile_counts[profile] = profile_counts.get(profile, 0) + 1
            account_rows.setdefault(_account_identity(row, profile=profile), []).append(row)
        accounts: list[OpportunityAccountGroup] = []
        for (account_scope, account_id), scoped_rows in sorted(
            account_rows.items(),
            key=lambda item: (
                0 if item[0][0] == "MAIN" else 1,
                item[0][0],
                item[0][1],
            ),
        ):
            scoped_profile_counts: dict[str, int] = {}
            for row in scoped_rows:
                profile = _cost_profile(row)
                scoped_profile_counts[profile] = scoped_profile_counts.get(profile, 0) + 1
            scoped_ordered_counts = tuple(sorted(scoped_profile_counts.items()))
            accounts.append(
                OpportunityAccountGroup(
                    account_scope=account_scope,
                    account_id=account_id,
                    rows=tuple(scoped_rows),
                    profiles=tuple(profile for profile, _ in scoped_ordered_counts),
                    profile_row_counts=scoped_ordered_counts,
                    raw_result_row_count=len(scoped_rows),
                    base_result_row_count=scoped_profile_counts.get("BASE", 0),
                    stress_result_row_count=scoped_profile_counts.get("STRESS", 0),
                    partial_exit_row_count=sum(
                        max(0, count - 1) for count in scoped_profile_counts.values()
                    ),
                )
            )
        ordered_counts = tuple(sorted(profile_counts.items()))
        groups.append(
            OpportunityGroup(
                key=key,
                rows=tuple(result_rows),
                accounts=tuple(accounts),
                profiles=tuple(profile for profile, _ in ordered_counts),
                profile_row_counts=ordered_counts,
                raw_result_row_count=len(result_rows),
                base_result_row_count=profile_counts.get("BASE", 0),
                stress_result_row_count=profile_counts.get("STRESS", 0),
                partial_exit_row_count=sum(
                    account.partial_exit_row_count for account in accounts
                ),
            )
        )
    return OpportunityGrouping(
        groups=tuple(groups),
        unresolved_rows=tuple(unresolved_rows),
        unique_opportunity_count=len(groups),
        raw_result_row_count=sum(group.raw_result_row_count for group in groups),
        base_result_row_count=sum(group.base_result_row_count for group in groups),
        stress_result_row_count=sum(group.stress_result_row_count for group in groups),
        unresolved_result_row_count=len(unresolved_rows),
    )


def unique_opportunity_count(
    rows: Sequence[Mapping[str, object]],
    *,
    strategy_version: str | None = None,
) -> int:
    grouped = group_trade_opportunities(rows, strategy_version=strategy_version)
    return grouped.unique_opportunity_count


def wilson_lower_bound(wins: int, total: int) -> Decimal | None:
    if wins < 0 or total < 0 or wins > total:
        raise ValueError("Wilson 입력은 0 <= wins <= total이어야 합니다.")
    if total == 0:
        return None
    sample = Decimal(total)
    proportion = Decimal(wins) / sample
    z_squared = WILSON_Z_95 * WILSON_Z_95
    denominator = Decimal(1) + z_squared / sample
    center = proportion + z_squared / (Decimal(2) * sample)
    variance = proportion * (Decimal(1) - proportion) / sample + z_squared / (
        Decimal(4) * sample * sample
    )
    lower = (center - WILSON_Z_95 * variance.sqrt()) / denominator
    return max(Decimal(0), lower)


def _required_identifier(value: object | None, field_name: str) -> str:
    normalized = _optional_identifier(value)
    if normalized is None:
        raise OpportunityKeyError(
            "INVALID_EXACT_OPPORTUNITY_KEY",
            f"고유기회 키에 유효한 {field_name}가 필요합니다.",
        )
    return normalized


def _optional_identifier(value: object | None) -> str | None:
    if (
        value is None
        or not (normalized := str(value).strip())
        or normalized.upper() == "UNKNOWN"
    ):
        return None
    return normalized


def _verified_opportunity_identifier(row: Mapping[str, object]) -> str:
    candidate_id = _optional_identifier(row.get("candidate_id"))
    signal_event_id = _optional_identifier(row.get("signal_event_id"))
    opportunity_id = _optional_identifier(row.get("opportunity_id"))
    if opportunity_id is None:
        opportunity_id = candidate_id or signal_event_id
    if opportunity_id is None:
        raise OpportunityKeyError(
            "MISSING_VERIFIABLE_OPPORTUNITY_LINKAGE",
            "legacy 거래행에 검증 가능한 opportunity·candidate·signal 연결값이 없습니다.",
        )
    if candidate_id is None and signal_event_id is None and _is_synthetic_legacy_linkage(
        row, opportunity_id
    ):
        raise OpportunityKeyError(
            "SYNTHETIC_LEGACY_LINKAGE_NOT_PROVEN",
            "legacy 속성 조합으로 만든 연결값은 하나의 시장기회로 간주할 수 없습니다.",
        )
    return opportunity_id


def _is_synthetic_legacy_linkage(
    row: Mapping[str, object],
    opportunity_id: str,
) -> bool:
    required = (
        _optional_identifier(row.get("run_id")),
        _optional_identifier(row.get("strategy_id") or row.get("strategy")),
        _optional_identifier(row.get("symbol")),
        _optional_identifier(row.get("side")),
        _optional_identifier(row.get("entry_ts_ms")),
    )
    return all(value is not None for value in required) and opportunity_id == "|".join(
        value for value in required if value is not None
    )


def _account_identity(
    row: Mapping[str, object],
    *,
    profile: str,
) -> tuple[str, str]:
    account_scope = (_optional_identifier(row.get("account_scope")) or "MAIN").upper()
    if account_scope not in {"MAIN", "LEAGUE"}:
        raise OpportunityKeyError(
            "INVALID_ACCOUNT_SCOPE",
            f"거래 계좌 범위가 MAIN·LEAGUE 계약을 벗어났습니다: {account_scope}",
        )
    strategy_id = _required_identifier(
        row.get("strategy_id") or row.get("strategy"), "strategy_id"
    )
    default_account_id = "SHARED_PAPER" if account_scope == "MAIN" else f"{strategy_id}:{profile}"
    account_id = _optional_identifier(row.get("account_id")) or default_account_id
    if account_id != default_account_id:
        raise OpportunityKeyError(
            "INVALID_ACCOUNT_IDENTITY",
            (
                "거래 계좌 식별자가 strategy·profile 계약과 다릅니다: "
                f"{account_scope}/{account_id} != {default_account_id}"
            ),
        )
    return account_scope, account_id


def _cost_profile(row: Mapping[str, object]) -> str:
    profile = _required_identifier(row.get("profile", "BASE"), "profile").upper()
    if profile not in {"BASE", "STRESS"}:
        raise OpportunityKeyError(
            "INVALID_COST_PROFILE",
            f"거래 비용 profile이 BASE·STRESS 계약을 벗어났습니다: {profile}",
        )
    return profile
