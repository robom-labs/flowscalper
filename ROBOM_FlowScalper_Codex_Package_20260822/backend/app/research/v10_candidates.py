# V10 중단타·스윙 전략과 외부 필터를 실행 전 연구 후보로 사전등록한다.

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum


class V10CandidateRole(StrEnum):
    ENTRY = "ENTRY"
    ENTRY_RESEARCH = "ENTRY_RESEARCH"
    FILTER = "FILTER"
    MARKET_NEUTRAL_MULTI_LEG = "MARKET_NEUTRAL_MULTI_LEG"


class V10Readiness(StrEnum):
    RESEARCH_SPEC = "RESEARCH_SPEC"
    BLOCKED_SOURCE_PIPELINE = "BLOCKED_SOURCE_PIPELINE"
    BLOCKED_POINT_IN_TIME_UNIVERSE = "BLOCKED_POINT_IN_TIME_UNIVERSE"
    BLOCKED_ENGINE = "BLOCKED_ENGINE"


_DIRECTION_ROLES = frozenset(
    {V10CandidateRole.ENTRY, V10CandidateRole.ENTRY_RESEARCH}
)


@dataclass(frozen=True, slots=True)
class V10CandidateSpec:
    candidate_id: str
    label_ko: str
    role: V10CandidateRole
    family_id: str
    horizon: str
    expected_hold_ko: str
    entry_contract: tuple[str, ...]
    exit_contract: tuple[str, ...]
    research_gates: tuple[str, ...]
    prerequisite_capability_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    readiness: V10Readiness
    data_availability_status: str
    research_enabled: bool = False
    user_visible_by_default: bool = False
    trial_ledger_included: bool = True
    final_ranking_eligible: bool = False
    entry_enabled: bool = False
    active_enabled: bool = False
    runtime_entry_registered: bool = False
    can_create_direction_now: bool = False
    can_increase_risk: bool = False
    can_widen_stop: bool = False
    completed_bars_only: bool = True
    max_hold_safety_review_only: bool = True
    averaging_down: bool = False
    martingale: bool = False
    pyramiding: bool = False
    real_orders_enabled: bool = False
    private_api_enabled: bool = False
    api_key_enabled: bool = False
    wallet_enabled: bool = False
    runtime_ai_order_decision_enabled: bool = False
    paper_only: bool = True
    profitability_status: str = "NOT_PROVEN"
    funding_readiness: str = "NOT_READY"

    def __post_init__(self) -> None:
        if not self.candidate_id or self.candidate_id != self.candidate_id.strip():
            raise ValueError("V10 후보 ID가 필요합니다.")
        if not self.label_ko.strip() or not self.family_id.strip():
            raise ValueError("V10 후보의 표시명과 family가 필요합니다.")
        required_sequences = (
            self.entry_contract,
            self.exit_contract,
            self.research_gates,
            self.prerequisite_capability_ids,
            self.source_ids,
        )
        if any(not sequence for sequence in required_sequences):
            raise ValueError("V10 후보의 조건·gate·출처를 생략할 수 없습니다.")
        if any(len(set(sequence)) != len(sequence) for sequence in required_sequences):
            raise ValueError("V10 후보의 조건·gate·출처를 중복할 수 없습니다.")
        if any(not source_id.startswith("SRC-") for source_id in self.source_ids):
            raise ValueError("V10 후보의 Source ID는 SRC- 형식이어야 합니다.")
        if self.role is V10CandidateRole.FILTER and self.can_create_direction_now:
            raise ValueError("V10 필터는 단독 방향을 만들 수 없습니다.")
        if (
            self.research_enabled
            or self.user_visible_by_default
            or self.final_ranking_eligible
            or self.entry_enabled
            or self.active_enabled
            or self.runtime_entry_registered
            or self.can_create_direction_now
        ):
            raise ValueError("V10 검증 전 후보는 기본 목록·진입·ACTIVE·순위에 올라갈 수 없습니다.")
        if (
            self.can_increase_risk
            or self.can_widen_stop
            or not self.completed_bars_only
            or not self.max_hold_safety_review_only
            or self.averaging_down
            or self.martingale
            or self.pyramiding
        ):
            raise ValueError("V10 연구 후보는 위험을 늘리거나 손절을 넓힐 수 없습니다.")
        if any(
            (
                self.real_orders_enabled,
                self.private_api_enabled,
                self.api_key_enabled,
                self.wallet_enabled,
                self.runtime_ai_order_decision_enabled,
            )
        ):
            raise ValueError("V10 후보는 실제 주문·private 인증·runtime AI를 사용할 수 없습니다.")
        if not self.trial_ledger_included:
            raise ValueError("V10 후보는 실패를 포함한 trial ledger에 남아야 합니다.")
        if (
            not self.paper_only
            or self.profitability_status != "NOT_PROVEN"
            or self.funding_readiness != "NOT_READY"
        ):
            raise ValueError("V10 후보는 공개시장 PAPER·NOT_PROVEN이어야 합니다.")

    @property
    def counts_as_direction_strategy(self) -> bool:
        return self.role in _DIRECTION_ROLES

    @property
    def counts_as_filter(self) -> bool:
        return self.role is V10CandidateRole.FILTER

    @property
    def counts_as_market_neutral_strategy(self) -> bool:
        return self.role is V10CandidateRole.MARKET_NEUTRAL_MULTI_LEG


_SWING_OOS_GATES = (
    "FINAL_OOS_UNIQUE_OPPORTUNITIES_GE_150",
    "CALENDAR_DAYS_GE_365",
    "SYMBOLS_GE_8",
    "BASE_EXPECTANCY_GT_0",
    "STRESS_EXPECTANCY_GT_0",
    "PROFIT_FACTOR_GE_1.20",
    "PAYOFF_GE_1.70",
    "OOS_EXPECTANCY_LOWER_BOUND_GT_0",
    "DSR_GE_0.95",
    "PBO_LE_0.20",
    "INDEPENDENT_PERIODS_GE_2",
    "ONE_SYMBOL_CONTRIBUTION_LT_0.25",
    "ONE_TRADE_CONTRIBUTION_LT_0.10",
)

_WEEKLY_FILTER_GATES = (
    "WEEKLY_OBSERVATIONS_GE_104",
    "FILTER_RETENTION_GE_0.50",
    "DELTA_EXPECTANCY_LOWER_BOUND_GT_0",
    "LAG_AWARE_BACKTEST_REQUIRED",
)

_MARKET_NEUTRAL_GATES = (
    "CYCLES_GE_50",
    "CALENDAR_DAYS_GE_365",
    "BASE_NET_GT_0",
    "STRESS_NET_GT_0",
    "ATOMIC_FILL_TEST_REQUIRED",
    "FUNDING_BASIS_LEGGING_INCLUDED",
)


def v10_candidate_specs() -> tuple[V10CandidateSpec, ...]:
    """실행은 차단하고 반증 가능한 조건만 고정한 V10 후보를 반환한다."""

    rows = (
        V10CandidateSpec(
            candidate_id="SWING_MULTI_HORIZON_TREND_4H1D_V1",
            label_ko="일봉 추세·4시간 눌림 스윙",
            role=V10CandidateRole.ENTRY,
            family_id="TREND_PULLBACK",
            horizon="INTRADAY_SWING",
            expected_hold_ko="6시간~5일",
            entry_contract=(
                "POINT_IN_TIME_USDT_PERPETUAL_UNIVERSE_MAX_30",
                "LISTING_DAYS_GE_180",
                "MEDIAN_30D_QUOTE_VOLUME_GE_50000000_USDT",
                "MEDIAN_30D_SPREAD_LE_8BP",
                "CANDLE_MISSING_RATE_LE_0.001",
                "1D_CLOSE_GT_EMA50_GT_EMA200",
                "EMA50_5D_SLOPE_OVER_ATR20_GE_0.15",
                "ADX14_1D_GE_18",
                "VOL_NORMALIZED_TREND_SCORE_12H_24H_72H_GE_2",
                "4H_PULLBACK_TO_EMA20_WITHIN_MINUS_0.60_TO_PLUS_0.25_ATR",
                "1H_COMPLETED_CLOSE_GT_PREVIOUS_HIGH_PLUS_0.05_ATR",
                "1H_BODY_RATIO_GE_0.55",
                "1H_CLV_GE_0.70",
                "1H_RVOL20_GE_1.20",
                "1H_TAKER_IMBALANCE_GE_0.08",
                "BASE_COST_COVERAGE_GE_3.00",
                "STRESS_COST_COVERAGE_GE_1.75",
            ),
            exit_contract=(
                "INITIAL_STOP_BELOW_4H_PULLBACK_OR_EMA50_STRUCTURE",
                "RISK_DISTANCE_1.0_TO_3.0_ATR4H",
                "TP1_1.50R_CLOSE_25_PERCENT",
                "RUNNER_75_PERCENT_ARM_AT_2.00R",
                "TRAIL_NEVER_WIDENS_3.5ATR4H_OR_DONCHIAN_LOW10",
                "MAX_HOLD_7D_SAFETY_ONLY_NOT_TIME_EXIT",
            ),
            research_gates=_SWING_OOS_GATES,
            prerequisite_capability_ids=(
                "V10.POINT_IN_TIME_SWING_UNIVERSE",
                "V10.COMPLETED_1H_4H_1D_FEATURES",
                "V7.BASE_STRESS_EXECUTION_TOURNAMENT",
            ),
            source_ids=(
                "SRC-TSMOM-2012",
                "SRC-CRYPTO-TREND-2020",
                "SRC-DYNAMIC-CRYPTO-TSMOM-2021",
                "SRC-CRYPTO-MOMENTUM-REVERSAL-2021",
            ),
            readiness=V10Readiness.RESEARCH_SPEC,
            data_availability_status="POINT_IN_TIME_UNIVERSE_AND_1D_HISTORY_NOT_CONNECTED",
        ),
        V10CandidateSpec(
            candidate_id="DAILY_DONCHIAN_RETEST_1D4H_V1",
            label_ko="일봉 55일 돌파·4시간 재확인",
            role=V10CandidateRole.ENTRY,
            family_id="BREAKOUT_RUNNER",
            horizon="SWING",
            expected_hold_ko="1일~14일",
            entry_contract=(
                "1D_CLOSE_GT_PREVIOUS_DONCHIAN_HIGH55_PLUS_0.10_ATR20",
                "1D_BODY_RATIO_GE_0.50",
                "1D_CLV_GE_0.75",
                "1D_RVOL20_GE_1.25",
                "1D_ADX14_GE_20",
                "BREAKOUT_CHASE_DISTANCE_LE_1.00_ATR1D",
                "RETEST_WITHIN_NEXT_6_COMPLETED_4H_BARS",
                "4H_RETEST_RANGE_MINUS_0.40_TO_PLUS_0.30_ATR",
                "4H_CLOSE_GT_DONCHIAN_LEVEL_AND_CLV_GE_0.55",
                "1H_CLOSE_GT_RETEST_HIGH_PLUS_0.05_ATR",
                "1H_RVOL_GE_1.15",
                "1H_TAKER_IMBALANCE_GE_0.08",
                "BASE_COST_COVERAGE_GE_3.00",
                "STRESS_COST_COVERAGE_GE_1.75",
            ),
            exit_contract=(
                "INITIAL_STOP_RETEST_LOW_MINUS_0.25_ATR4H",
                "TP1_2.00R_CLOSE_20_PERCENT",
                "RUNNER_80_PERCENT_ARM_AT_2.00R",
                "TRAIL_NEVER_WIDENS_4.0ATR4H_OR_DONCHIAN_LOW20",
                "MAX_HOLD_14D_SAFETY_ONLY_NOT_TIME_EXIT",
            ),
            research_gates=_SWING_OOS_GATES,
            prerequisite_capability_ids=(
                "V10.COMPLETED_1H_4H_1D_FEATURES",
                "V10.DONCHIAN_RETEST_STATE_MACHINE",
                "V7.BASE_STRESS_EXECUTION_TOURNAMENT",
            ),
            source_ids=(
                "SRC-TSMOM-2012",
                "SRC-CRYPTO-TREND-2020",
                "SRC-DYNAMIC-CRYPTO-TSMOM-2021",
            ),
            readiness=V10Readiness.RESEARCH_SPEC,
            data_availability_status="DAILY_DONCHIAN_RETEST_STATE_NOT_CONNECTED",
        ),
        V10CandidateSpec(
            candidate_id="CFTC_CME_BITCOIN_CROWDING_FILTER_V1",
            label_ko="CFTC CME 비트코인 쏠림 필터",
            role=V10CandidateRole.FILTER,
            family_id="MARKET_REGIME_FILTERS",
            horizon="WEEKLY_FILTER",
            expected_hold_ko="진입 필터만",
            entry_contract=(
                "CFTC_TFF_FUTURES_ONLY_BITCOIN_133741_PRIMARY",
                "MICRO_BITCOIN_133742_SENSITIVITY_ONLY",
                "REPORT_DATE_IS_POSITION_DATE_NOT_AVAILABILITY",
                "SCHEDULED_RELEASE_AT_AMERICA_NEW_YORK_15_30_WITH_HOLIDAY_SCHEDULE",
                "FIRST_OBSERVED_AT_OR_INGESTED_AT_REQUIRED",
                "FIRST_OBSERVED_AT_IS_NOT_OFFICIAL_ACTUAL_RELEASE_TIMESTAMP",
                "DATA_AGE_GT_10D_IS_UNAVAILABLE",
                "ROBUST_Z_PRIOR_156_RELEASED_WEEKS_MIN_104",
                "COT_ALONE_CANNOT_CREATE_DIRECTION",
                "CROWDED_LONG_REQUIRES_LEV_NET_Z_GE_1.50_AND_FUNDING_Z_GE_1.50_AND_BASIS_Z_GE_1.50",
            ),
            exit_contract=(
                "FILTER_ONLY_NO_POSITION_EXIT_AUTHORITY",
                "UNAVAILABLE_FAILS_CLOSED_FOR_FILTER_DEPENDENT_ENTRY",
            ),
            research_gates=_WEEKLY_FILTER_GATES,
            prerequisite_capability_ids=(
                "V10.CFTC_RELEASE_TIMESTAMP_DATASET",
                "V10.WEEKLY_POINT_IN_TIME_POSITIONING",
                "V7.RISK_REDUCTION_ONLY",
            ),
            source_ids=(
                "SRC-CFTC-TFF-FUTURES-ONLY",
                "SRC-CFTC-COT-RELEASE-SCHEDULE",
                "SRC-CFTC-COT-HISTORICAL-VIEWABLE",
            ),
            readiness=V10Readiness.BLOCKED_SOURCE_PIPELINE,
            data_availability_status="CFTC_POINT_IN_TIME_RELEASE_DATA_NOT_CONNECTED",
        ),
        V10CandidateSpec(
            candidate_id="CRYPTO_FUTURES_CURVE_REGIME_FILTER_V1",
            label_ko="크립토 선물곡선 레짐 필터",
            role=V10CandidateRole.FILTER,
            family_id="POSITIONING_LIQUIDATION",
            horizon="DAILY_CURVE_FILTER",
            expected_hold_ko="진입 필터만",
            entry_contract=(
                "PUBLIC_SPOT_PERPETUAL_NEAR_FAR_QUARTER_INPUTS_ONLY",
                "ANNUALIZED_BASIS_USING_ACTUAL_DAYS_TO_EXPIRY",
                "CURVE_SLOPE_FAR_MINUS_NEAR",
                "BASIS_MOMENTUM_7D",
                "ROLL_SAFE_CONTRACT_MAPPING_REQUIRED",
                "ONE_CONTRACT_IS_FILTER_DATA_UNAVAILABLE",
                "CROWDED_LONG_CURVE_REQUIRES_2_OF_BASIS_FUNDING_OI_Z_GE_2",
                "CURVE_FILTER_ALONE_CANNOT_CREATE_DIRECTION",
            ),
            exit_contract=(
                "FILTER_ONLY_NO_POSITION_EXIT_AUTHORITY",
                "UNAVAILABLE_FAILS_CLOSED_FOR_FILTER_DEPENDENT_ENTRY",
            ),
            research_gates=_WEEKLY_FILTER_GATES,
            prerequisite_capability_ids=(
                "V10.POINT_IN_TIME_FUTURES_CURVE",
                "V10.ROLL_SAFE_QUARTERLY_CONTRACT_MAP",
                "V7.RISK_REDUCTION_ONLY",
            ),
            source_ids=(
                "SRC-CRYPTO-FUTURES-RISK-FACTORS-2023",
            ),
            readiness=V10Readiness.BLOCKED_SOURCE_PIPELINE,
            data_availability_status="NEAR_FAR_QUARTERLY_CURVE_NOT_CONNECTED",
        ),
        V10CandidateSpec(
            candidate_id="RESIDUAL_14D_RELATIVE_STRENGTH_V1",
            label_ko="14일 잔차 상대강도",
            role=V10CandidateRole.ENTRY_RESEARCH,
            family_id="TREND_PULLBACK",
            horizon="CROSS_SECTIONAL_SWING",
            expected_hold_ko="최대 7일 안전 재검토",
            entry_contract=(
                "POINT_IN_TIME_TOP50_LIQUID_PERPETUAL_UNIVERSE",
                "SURVIVORSHIP_SAFE_HISTORY_GE_180D",
                "PRIOR_ONLY_180D_BTC_ETH_MARKET_REGRESSION",
                "RESIDUAL_14D_PERCENTILE_GE_80",
                "RESIDUAL_28D_PERCENTILE_GE_60",
                "1D_CLOSE_GT_EMA50_GT_EMA200",
                "4H_PULLBACK_OR_BREAKOUT_TRIGGER_REQUIRED",
                "MAX_3_SYMBOLS_ONE_PER_CORRELATION_CLUSTER",
            ),
            exit_contract=(
                "RANK_LT_50_PERCENTILE_OR_4H_CLOSE_LT_EMA20_OR_ATR_RUNNER",
                "CURRENT_POSITION_NOT_FORCED_OUT_BY_INTRADAY_RERANK",
                "MAX_HOLD_7D_SAFETY_REVIEW",
            ),
            research_gates=_SWING_OOS_GATES,
            prerequisite_capability_ids=(
                "V10.SURVIVORSHIP_SAFE_POINT_IN_TIME_UNIVERSE",
                "V10.MULTI_SYMBOL_PORTFOLIO_ENGINE",
                "V8.CLUSTER_EXPOSURE",
            ),
            source_ids=(
                "SRC-CRYPTO-MOMENTUM-2018",
                "SRC-CRYPTO-MOMENTUM-REVERSAL-2021",
            ),
            readiness=V10Readiness.BLOCKED_POINT_IN_TIME_UNIVERSE,
            data_availability_status="MULTI_SYMBOL_POINT_IN_TIME_ENGINE_NOT_PROVEN",
        ),
        V10CandidateSpec(
            candidate_id="BASIS_MOMENTUM_CROSS_SECTIONAL_RESEARCH_V1",
            label_ko="Basis Momentum 시장중립 연구",
            role=V10CandidateRole.MARKET_NEUTRAL_MULTI_LEG,
            family_id="MARKET_NEUTRAL",
            horizon="CROSS_SECTIONAL_MARKET_NEUTRAL",
            expected_hold_ko="연구단계 미확정",
            entry_contract=(
                "PERPETUAL_AND_NEAR_QUARTER_BASIS_FEATURES",
                "BASIS_MOMENTUM_7D_CURVE_SLOPE_FUNDING_OI_FEATURES",
                "TRAIN_ONLY_STANDARDIZE_AND_RIDGE",
                "FEATURE_SIGN_STABLE_IN_GE_2_OF_3_WALK_FORWARD_FOLDS",
                "TOP_QUANTILE_LONG_BOTTOM_QUANTILE_SHORT_DOLLAR_NEUTRAL",
                "MAX_3_PAIRS",
                "ATOMIC_MULTI_LEG_FILL_REQUIRED",
            ),
            exit_contract=(
                "PREDECLARED_SPREAD_OR_RANK_EXIT_REQUIRED",
                "FUNDING_FEE_SLIPPAGE_LEGGING_INCLUDED",
            ),
            research_gates=_MARKET_NEUTRAL_GATES,
            prerequisite_capability_ids=(
                "V10.POINT_IN_TIME_FUTURES_CURVE",
                "V10.ATOMIC_MULTI_LEG_PAPER_ENGINE",
                "V10.FUNDING_BASIS_LEGGING_ACCOUNTING",
            ),
            source_ids=("SRC-CRYPTO-FUTURES-RISK-FACTORS-2023",),
            readiness=V10Readiness.BLOCKED_ENGINE,
            data_availability_status="ATOMIC_MULTI_LEG_AND_FUNDING_ENGINE_NOT_PROVEN",
        ),
    )
    _validate_candidate_set(rows)
    return rows


_EXPECTED_CANDIDATE_IDS = {
    "SWING_MULTI_HORIZON_TREND_4H1D_V1",
    "DAILY_DONCHIAN_RETEST_1D4H_V1",
    "CFTC_CME_BITCOIN_CROWDING_FILTER_V1",
    "CRYPTO_FUTURES_CURVE_REGIME_FILTER_V1",
    "RESIDUAL_14D_RELATIVE_STRENGTH_V1",
    "BASIS_MOMENTUM_CROSS_SECTIONAL_RESEARCH_V1",
}


def _validate_candidate_set(rows: tuple[V10CandidateSpec, ...]) -> None:
    candidate_ids = [row.candidate_id for row in rows]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("V10 후보 ID가 중복됐습니다.")
    if len(rows) != 6 or set(candidate_ids) != _EXPECTED_CANDIDATE_IDS:
        raise ValueError("V10 사전등록 후보는 지정된 정확한 6개여야 합니다.")
    if sum(row.counts_as_direction_strategy for row in rows) != 3:
        raise ValueError("V10 방향 연구 후보는 정확히 3개여야 합니다.")
    if sum(row.counts_as_filter for row in rows) != 2:
        raise ValueError("V10 필터 후보는 정확히 2개여야 합니다.")
    if sum(row.counts_as_market_neutral_strategy for row in rows) != 1:
        raise ValueError("V10 시장중립 연구 후보는 정확히 1개여야 합니다.")
    if any("WEEKEND_GAP" in candidate_id for candidate_id in candidate_ids):
        raise ValueError("CME weekend gap은 V10 현재 후보에 포함할 수 없습니다.")


def v10_candidate_manifest(*, source_commit: str) -> dict[str, object]:
    """기본 OFF·순위 제외·실행 차단을 분리한 V10 manifest를 만든다."""

    if not source_commit.strip():
        raise ValueError("source commit이 필요합니다.")
    rows = v10_candidate_specs()
    payload_rows = [
        asdict(row)
        | {
            "role": row.role.value,
            "readiness": row.readiness.value,
            "counts_as_direction_strategy": row.counts_as_direction_strategy,
            "counts_as_filter": row.counts_as_filter,
            "counts_as_market_neutral_strategy": row.counts_as_market_neutral_strategy,
        }
        for row in rows
    ]
    manifest: dict[str, object] = {
        "schema": "flowscalper.v10_candidate_registry.v1",
        "status": "RESEARCH_OFF_ENTRY_BLOCKED",
        "source_commit": source_commit,
        "candidate_count": len(rows),
        "research_enabled_count": sum(row.research_enabled for row in rows),
        "default_visible_count": sum(row.user_visible_by_default for row in rows),
        "trial_ledger_included_count": sum(row.trial_ledger_included for row in rows),
        "final_ranking_eligible_count": sum(row.final_ranking_eligible for row in rows),
        "direction_strategy_count": sum(
            row.counts_as_direction_strategy for row in rows
        ),
        "filter_count": sum(row.counts_as_filter for row in rows),
        "market_neutral_strategy_count": sum(
            row.counts_as_market_neutral_strategy for row in rows
        ),
        "runtime_entry_registered_count": sum(
            row.runtime_entry_registered for row in rows
        ),
        "active_count": sum(row.active_enabled for row in rows),
        "entry_enabled_count": sum(row.entry_enabled for row in rows),
        "candidates": payload_rows,
        "rejected_hypotheses": [
            {
                "hypothesis_id": "CME_WEEKEND_GAP_FILL",
                "status": "REJECTED",
                "reason": "OBSOLETE_REGIME",
                "readiness": "OBSOLETE_REGIME",
                "cutover_local_ts": "2026-05-29T16:00:00",
                "cutover_timezone": "America/Chicago",
                "pre_post_cutover_mixing_allowed": False,
                "post_cutover_runtime_entry_registered": False,
                "weekly_maintenance_min_hours": 2,
                "weekend_trade_date_is_next_business_day": True,
                "source_ids": [
                    "SRC-CME-CRYPTO-24X7-LAUNCH-2026",
                    "SRC-CME-GLOBEX-CRYPTO-24X7-20260525",
                    "SRC-CME-CRYPTO-24X7-REGIME-2026",
                ],
            }
        ],
        "paper_only": True,
        "real_orders_enabled": False,
        "real_order_endpoints_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "api_key_enabled": False,
        "secret_enabled": False,
        "login_enabled": False,
        "real_account_access_enabled": False,
        "wallet_enabled": False,
        "transfer_enabled": False,
        "tradingview_webhook_orders_enabled": False,
        "runtime_ai_order_decision_enabled": False,
        "completed_bars_only": True,
        "max_hold_safety_review_only": True,
        "initial_stop_never_widens": True,
        "averaging_down_enabled": False,
        "martingale_enabled": False,
        "pyramiding_enabled": False,
        "automatic_risk_increase_enabled": False,
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
    "V10CandidateRole",
    "V10CandidateSpec",
    "V10Readiness",
    "v10_candidate_manifest",
    "v10_candidate_specs",
]
