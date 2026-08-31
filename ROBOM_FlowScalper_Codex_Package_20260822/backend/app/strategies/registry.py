"""전략 메타데이터와 Strategy League 설정을 중앙 관리한다."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from functools import wraps
from threading import RLock
from typing import Concatenate

from backend.app.domain.models import Side
from backend.app.regime import Regime
from backend.app.strategies.aggressor_flow import AggressorFlowStrategy
from backend.app.strategies.book_slope_asymmetry import BookSlopeAsymmetryStrategy
from backend.app.strategies.compression_breakout import CompressionBreakoutStrategy
from backend.app.strategies.depth_adjusted_ofi import DepthAdjustedOfiStrategy
from backend.app.strategies.family import (
    StrategyFamilyId,
    StrategyRole,
    strategy_variant_contract,
    validate_family_contract,
)
from backend.app.strategies.hourly_momentum_breakout import HourlyMomentumBreakoutStrategy
from backend.app.strategies.intraday_trend import (
    IntradayTrendStrategy,
    IntradayTrendVariant,
)
from backend.app.strategies.liquidity_sweep import LiquiditySweepStrategy
from backend.app.strategies.multilevel_microprice import MultilevelMicropriceStrategy
from backend.app.strategies.ofi_pullback import OfiPullbackStrategy
from backend.app.strategies.ofi_return_confluence import OfiReturnConfluenceStrategy
from backend.app.strategies.queue_microprice import QueueMicropriceStrategy
from backend.app.strategies.vwap_exhaustion import VwapExhaustionStrategy


def _setting_locked[**P, R](
    method: Callable[Concatenate[StrategyRegistry, P], R],
) -> Callable[Concatenate[StrategyRegistry, P], R]:
    """설정 CAS 검사부터 revision 이력 기록까지 한 임계구역으로 묶는다."""

    @wraps(method)
    def wrapped(
        self: StrategyRegistry,
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        with self._setting_lock:
            return method(self, *args, **kwargs)

    return wrapped


class StrategyMode(StrEnum):
    ACTIVE = "ACTIVE"
    SHADOW = "SHADOW"
    OFF = "OFF"


class StrategyLifecycle(StrEnum):
    RESEARCH = "RESEARCH"
    SHADOW = "SHADOW"
    CHALLENGER = "CHALLENGER"
    ACTIVE = "ACTIVE"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


class StrategyChangeSource(StrEnum):
    USER_UI = "USER_UI"
    AUTO_GOVERNOR = "AUTO_GOVERNOR"
    RECOVERY = "RECOVERY"
    MIGRATION = "MIGRATION"


class StrategyRevisionConflict(RuntimeError):
    def __init__(self, current_setting: dict[str, object]) -> None:
        super().__init__("전략 설정 revision이 최신 상태와 다릅니다.")
        self.current_setting = current_setting


class StrategyManualLockConflict(RuntimeError):
    """사용자가 고정한 전략 설정을 자동 governor가 덮어쓰지 못하게 한다."""


class StrategyStability(StrEnum):
    STABLE = "STABLE"
    EXPERIMENTAL = "EXPERIMENTAL"


class ExitStyle(StrEnum):
    REVERSION_70_30 = "REVERSION_70_30"
    TREND_40_60 = "TREND_40_60"


StrategyEvaluator = (
    LiquiditySweepStrategy
    | CompressionBreakoutStrategy
    | VwapExhaustionStrategy
    | OfiPullbackStrategy
    | QueueMicropriceStrategy
    | AggressorFlowStrategy
    | MultilevelMicropriceStrategy
    | DepthAdjustedOfiStrategy
    | OfiReturnConfluenceStrategy
    | BookSlopeAsymmetryStrategy
    | HourlyMomentumBreakoutStrategy
    | IntradayTrendStrategy
)


@dataclass(frozen=True, slots=True)
class StrategyResearchContract:
    """전략별 가설·반증·데이터·위험·연구 출처를 실행 API와 함께 고정한다."""

    strategy_version: str
    required_market_data: tuple[str, ...]
    minimum_warmup_ko: str
    entry_hypothesis_ko: str
    falsification_conditions_ko: tuple[str, ...]
    edge_decay_policy_ko: str
    risk_budget_rule_ko: str
    target_universe_ko: str
    data_leakage_guards_ko: tuple[str, ...]
    research_source_ids: tuple[str, ...]


POLICY_RETIRED_STRATEGY_IDS = frozenset(
    {
        "LSA_REVERSAL_V1",
        "OFI_CONTINUATION_PULLBACK_V1",
        "QUEUE_MICROPRICE_MOMENTUM_V1",
        "DEPTH_ADJUSTED_OFI_IMPULSE_V1",
        "HOURLY_MOMENTUM_BREAKOUT_V1",
    }
)
POLICY_RETIREMENT_REASONS = {
    strategy_id: "COST_ADJUSTED_RESEARCH_RETIREMENT_WAVE39"
    for strategy_id in POLICY_RETIRED_STRATEGY_IDS
}
POLICY_RETIREMENT_REASONS["HOURLY_MOMENTUM_BREAKOUT_V1"] = (
    "FIXED_HISTORICAL_REPLICATION_FAILED_WAVE46"
)
POLICY_SHADOW_DEFAULT_IDS = frozenset({"CBR_CONTINUATION_V1"})
POLICY_SHADOW_DEFAULT_REASON = "NO_ACTIVE_STRATEGY_WITHOUT_COST_ADJUSTED_PROOF_WAVE46"
UNPROVEN_ACTIVE_RECOVERY_REASON = "V6_UNPROVEN_ACTIVE_RECOVERY_DOWNGRADED"


@dataclass(frozen=True, slots=True)
class StrategyDescriptor:
    strategy_id: str
    display_name_ko: str
    short_name: str
    summary_ko: str
    stability: StrategyStability
    supported_regimes: tuple[Regime, ...]
    evaluator: StrategyEvaluator
    exit_style: ExitStyle
    research_contract: StrategyResearchContract
    horizon_class: str = "MICRO_SCALP"
    expected_holding_seconds: tuple[int, int] = (10, 180)
    signal_half_life_seconds: int = 30
    required_timeframes: tuple[str, ...] = ("250ms", "1s", "3s", "10s", "30s", "120s")
    exit_model: str = "STRUCTURE_TP1_TP2_SL_NO_TIME_EXIT"
    take_profit_1_r: Decimal = Decimal("1.5")
    take_profit_2_r: Decimal = Decimal("3.0")
    entry_rules_ko: tuple[str, ...] = ()
    exit_rules_ko: tuple[str, ...] = ()
    max_hold_seconds: int | None = None
    edge_decay_enabled: bool = False
    cost_model_version: str = "TOP_OF_BOOK_BASE13_STRESS25_V1"
    paper_only: bool = True
    family_id: StrategyFamilyId = field(init=False)
    role: StrategyRole = field(init=False)
    variant_id: str = field(init=False)
    variant_label_ko: str = field(init=False)
    is_current_variant: bool = field(init=False)
    supersedes_strategy_ids: tuple[str, ...] = field(init=False)
    superseded_by_strategy_id: str | None = field(init=False)
    user_visible_by_default: bool = field(init=False)
    default_research_enabled: bool = field(init=False)
    final_ranking_eligible: bool = field(init=False)

    def __post_init__(self) -> None:
        contract = strategy_variant_contract(self.strategy_id)
        for field_name in (
            "family_id",
            "role",
            "variant_id",
            "variant_label_ko",
            "is_current_variant",
            "supersedes_strategy_ids",
            "superseded_by_strategy_id",
            "user_visible_by_default",
            "default_research_enabled",
            "final_ranking_eligible",
        ):
            object.__setattr__(self, field_name, getattr(contract, field_name))


_MICRO_REQUIRED_MARKET_DATA = (
    "sequence-valid 공개 top-10 bid·ask 호가",
    "공개 aggregate trade 가격·수량·aggressor 방향",
    "종목별 250ms~120초 과거 피처와 시장 레짐",
)
_MICRO_MINIMUM_WARMUP_KO = "건전한 종목별 공개시장 10초 이상과 현재 이전 prefix 통계"
_RISK_BUDGET_RULE_KO = "공동 PAPER 0.10%·독립 PAPER 0.50% 계좌자산 위험예산"
_EDGE_DECAY_POLICY_KO = (
    "일반 근거약화·시간청산 없이 TP1·TP2·구조 손절·데이터/시스템 안전종료만 적용"
)
_MICRO_TARGET_UNIVERSE_KO = "동적 정밀분석 종목 중 지원 레짐·유동성·비용 gate 통과 종목"
_MICRO_DATA_LEAKAGE_GUARDS_KO = (
    "현재 event timestamp 이전의 동일 종목 이력만 사용",
    "현재 snapshot은 모든 전략·방향 평가가 끝난 뒤 과거창에 추가",
    "stale·sequence invalid·미래 timestamp 입력은 fail-closed",
)
_INTRADAY_REQUIRED_MARKET_DATA = (
    "완성 공개 15분·30분·1시간 OHLCV와 aggressor 거래량",
    "EMA20·EMA80, 24시간 모멘텀, ADX, 상대거래량, 구조 돌파·되돌림",
    "신호 직후 sequence-valid 공개 bid·ask·OFI·aggressor 체결 흐름",
)
_INTRADAY_MINIMUM_WARMUP_KO = "완성 신호주기 봉 100개 이상과 완성 1시간 봉 50개 이상"
_INTRADAY_EDGE_POLICY_KO = (
    "일반 미세구조 근거약화 청산 없이 TP1·TP2·구조 손절·데이터/시스템 안전종료·"
    "이익보호 방향의 손절 단축만 적용하며 시간만으로 종료하지 않음"
)
_INTRADAY_TARGET_UNIVERSE_KO = (
    "동적 정밀분석 종목 중 완성봉·현재 공개호가·유동성·비용 gate를 모두 통과한 종목"
)
_INTRADAY_DATA_LEAKAGE_GUARDS_KO = (
    "현재 진행 중 봉을 제외하고 신호시각까지 완성된 봉만 사용",
    "돌파 기준은 신호 봉보다 앞선 봉으로만 계산",
    "완성봉 신호 뒤 5초 이내 현재 이전 공개호가 흐름만 사용",
    "같은 공개시장 입력에서 LONG·SHORT와 BASE·STRESS를 독립 평가",
)

_RESEARCH_CONTRACTS = {
    "LSA_REVERSAL_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("sweep 구조·refill·micro-VWAP 범위 재진입",),
        minimum_warmup_ko=_MICRO_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "유동성 쓸기 뒤 공격 흐름의 가격 진전이 멈추고 반대호가 refill·OFI 반전·"
            "microprice 회복·범위 재진입이 지속되면 단기 평균복귀 가능성이 높아진다."
        ),
        falsification_conditions_ko=(
            "쓸기 방향 가격 진전과 공격 흐름이 계속됨",
            "refill·OFI 반전·범위 재진입이 지속되지 않음",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-OFI-2010",
            "SRC-QI-2015",
            "SRC-MICROPRICE-2017",
            "SRC-BINANCE-DEPTH",
            "SRC-BINANCE-AGGTRADE",
        ),
    ),
    "CBR_CONTINUATION_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("30초 변동성·압축·구조 돌파·10초 pullback 가격 경로",),
        minimum_warmup_ko=_MICRO_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "건전한 추세 레짐의 압축 돌파 뒤 얕은 눌림에서 반대 흐름의 가격영향이 약하고 "
            "refill·OFI·microprice·가격이 재가속하면 단기 추세가 이어질 수 있다."
        ),
        falsification_conditions_ko=(
            "압축이 stale 데이터나 비정상 spread에서 발생함",
            "눌림이 깊거나 원래 흐름·microprice 재가속이 끊김",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-OFI-2010",
            "SRC-BINANCE-DEPTH",
            "SRC-BINANCE-AGGTRADE",
        ),
    ),
    "VWAP_EXHAUSTION_REVERSION_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("micro-VWAP 이탈·공격 흐름 robust 통계·구조 재진입",),
        minimum_warmup_ko=_MICRO_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "RANGE에서 micro-VWAP 과도이탈 대비 공격 흐름의 가격 진전이 둔화되고 "
            "반대호가 refill·OFI·microprice·구조가 복귀하면 평균복귀 가능성이 높아진다."
        ),
        falsification_conditions_ko=(
            "RANGE가 아니거나 가격 진전이 계속 강함",
            "반대호가 refill·OFI·microprice·구조 복귀가 확인되지 않음",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-OFI-2010",
            "SRC-MICROPRICE-2017",
            "SRC-BINANCE-DEPTH",
            "SRC-BINANCE-AGGTRADE",
        ),
    ),
    "OFI_CONTINUATION_PULLBACK_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("250ms·3초 OFI 정렬·15초 pullback 경로·가격반응 효율",),
        minimum_warmup_ko=_MICRO_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "추세 레짐에서 다중 OFI와 공격체결이 정렬된 뒤 약한 반대 pullback을 거쳐 "
            "원 흐름·microprice·가격이 재가속하면 추세가 이어질 수 있다."
        ),
        falsification_conditions_ko=(
            "추세 레짐 또는 다중 OFI 정렬이 무너짐",
            "반대 pullback의 가격영향이 강하거나 원 흐름 재가속이 없음",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-OFI-2010",
            "SRC-MLOFI-2019",
            "SRC-MICROPRICE-2017",
            "SRC-BINANCE-DEPTH",
            "SRC-BINANCE-AGGTRADE",
        ),
    ),
    "QUEUE_MICROPRICE_MOMENTUM_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("top-1·5·10 queue imbalance·microprice 변위·500ms 지속",),
        minimum_warmup_ko=_MICRO_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "여러 호가 단계의 queue·OFI·공격체결·microprice가 같은 방향으로 지속되고 "
            "가격이 반응하면 매우 짧은 방향 이동 가능성이 높아진다."
        ),
        falsification_conditions_ko=(
            "queue·OFI·체결·microprice 방향이 불일치하거나 500ms 전에 소멸",
            "가격반응이 없거나 spread·stale·sequence 조건 실패",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-QI-2015",
            "SRC-MLOFI-2019",
            "SRC-MICROPRICE-2017",
            "SRC-BINANCE-DEPTH",
            "SRC-BINANCE-AGGTRADE",
        ),
    ),
    "AGGRESSOR_FLOW_CONTINUATION_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("3초·10초 signed notional robust z·가격반응·500ms 지속",),
        minimum_warmup_ko=_MICRO_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "추세 레짐에서 비정상적으로 강한 공격체결이 OFI·microprice와 정렬되고 "
            "실제 가격을 효율적으로 밀면 단기 흐름이 이어질 수 있다."
        ),
        falsification_conditions_ko=(
            "공격체결 robust z·OFI·microprice 방향이 불일치하거나 지속 실패",
            "큰 체결에도 실제 가격반응이 둔화됨",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-OFI-2010",
            "SRC-BINANCE-AGGTRADE",
            "SRC-BINANCE-DEPTH",
        ),
    ),
    "MULTILEVEL_MICROPRICE_MOMENTUM_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("top-10 cross-weighted 공정가·최우선 microprice·750ms 지속",),
        minimum_warmup_ko=_MICRO_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "top-10 공정가와 최우선 microprice·OFI·체결·가격반응이 같은 방향으로 "
            "지속되면 단일 호가보다 강한 단기 방향 정보가 될 수 있다."
        ),
        falsification_conditions_ko=(
            "다중호가 공정가와 최우선 microprice 방향이 불일치",
            "OFI·체결·가격반응 정렬이 750ms 전에 소멸",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-MLOFI-2019",
            "SRC-MICROPRICE-2017",
            "SRC-QI-2015",
            "SRC-BINANCE-DEPTH",
        ),
    ),
    "DEPTH_ADJUSTED_OFI_IMPULSE_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("top-10 평균깊이 보정 3초 OFI robust z·500ms 지속",),
        minimum_warmup_ko=_MICRO_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "동일 종목 과거깊이에 비해 이례적인 깊이보정 OFI가 체결·microprice·"
            "가격반응과 정렬되면 단기 충격이 이어질 수 있다."
        ),
        falsification_conditions_ko=(
            "깊이보정 OFI robust z 또는 방향 정렬 실패",
            "체결·microprice·가격반응 정렬이 500ms 전에 소멸",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-OFI-2010",
            "SRC-MLOFI-2019",
            "SRC-BINANCE-DEPTH",
        ),
    ),
    "OFI_RETURN_CONFLUENCE_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("깊이보정 OFI robust z·현재 이전 3초 가격수익률·1,000ms 지속",),
        minimum_warmup_ko=_MICRO_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "깊이보정 OFI와 현재 이전 3초 수익률·microprice·가격반응이 같은 방향으로 "
            "지속되면 주문흐름과 가격 경로의 단기 동행이 이어질 수 있다."
        ),
        falsification_conditions_ko=(
            "OFI와 prefix 3초 수익률 방향이 불일치하거나 anchor가 없음",
            "microprice·가격반응 정렬이 1,000ms 전에 소멸",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO
        + ("3초 수익률 anchor는 목표시각 이전 1.5초 범위의 가장 가까운 prefix만 사용",),
        research_source_ids=(
            "SRC-OFI-2010",
            "SRC-MLOFI-2019",
            "SRC-BINANCE-DEPTH",
        ),
    ),
    "BOOK_SLOPE_ASYMMETRY_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=_MICRO_REQUIRED_MARKET_DATA
        + ("top-10 가격거리별 명목깊이 기울기·동일 종목 과거 32표본·1,000ms 지속",),
        minimum_warmup_ko="건전한 공개시장 10초 이상·동일 종목 prefix 호가기울기 32표본 이상",
        entry_hypothesis_ko=(
            "진행 방향 반대호가가 과거보다 얇고 지지호가가 두꺼우며 OFI·체결·"
            "microprice·가격반응이 정렬되면 단기 유동성 비대칭이 이어질 수 있다."
        ),
        falsification_conditions_ko=(
            "prefix 호가기울기 32표본 미만 또는 양쪽 기울기 비대칭 실패",
            "OFI·체결·microprice·가격반응 정렬이 1,000ms 전에 소멸",
            "실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_MICRO_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_MICRO_DATA_LEAKAGE_GUARDS_KO
        + ("현재 호가기울기는 percentile 계산 뒤 과거창에 추가",),
        research_source_ids=(
            "SRC-MLOFI-2019",
            "SRC-QI-2015",
            "SRC-BINANCE-DEPTH",
        ),
    ),
    "HOURLY_MOMENTUM_BREAKOUT_V1": StrategyResearchContract(
        strategy_version="V1",
        required_market_data=(
            "완성 공개 1시간봉 OHLCV 200개 이상",
            "EMA20·50·80·200, 24시간 모멘텀, Donchian, ADX, 상대거래량",
            "신호 후 5초 이내 sequence-valid 공개 bid·ask 실행호가",
        ),
        minimum_warmup_ko="완성 1시간봉 200개 이상",
        entry_hypothesis_ko=(
            "완성 시간봉의 장단기 EMA·24시간 모멘텀·Donchian 돌파·ADX·상대거래량이 "
            "같은 방향이면 비용을 넘는 수시간 추세가 이어질 수 있다."
        ),
        falsification_conditions_ko=(
            "완성봉 200개 미만 또는 EMA·모멘텀·돌파·ADX·상대거래량 중 하나라도 실패",
            "진행 중 봉이나 신호 후 5초를 넘긴 실행호가만 존재",
            "BASE·STRESS 비용후 OOS·강건성 gate 실패",
        ),
        edge_decay_policy_ko=_EDGE_DECAY_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko="동적 정밀분석 종목 중 완성 1시간봉 200개 이상과 추세 레짐을 갖춘 종목",
        data_leakage_guards_ko=(
            "현재 진행 중 1시간봉 제외",
            "신호 시각까지 완성된 봉과 현재 이전 공개호가만 사용",
            "과거 연구결과를 본 뒤 runtime 조건을 자동 변경하지 않음",
        ),
        research_source_ids=(
            "SRC-CRYPTO-MOMENTUM-2018",
            "SRC-BINANCE-KLINE",
            "SRC-BINANCE-DEPTH",
        ),
    ),
    "TREND_PULLBACK_RECLAIM_15M_V2": StrategyResearchContract(
        strategy_version="V2",
        required_market_data=_INTRADAY_REQUIRED_MARKET_DATA,
        minimum_warmup_ko=_INTRADAY_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "완성 1시간 추세와 같은 방향의 15분 EMA20 눌림 뒤 이전 고저를 재돌파하고 "
            "현재 공개 호가·체결 흐름도 같은 방향이면 추세 재개 가능성이 높아질 수 있다."
        ),
        falsification_conditions_ko=(
            "15분·1시간 추세 불일치 또는 24시간 모멘텀·ADX·상대거래량 gate 실패",
            "눌림 뒤 EMA20·이전 고저 재돌파와 현재 공개 흐름 확인 실패",
            "구조 손절 거리·실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_INTRADAY_EDGE_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_INTRADAY_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_INTRADAY_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-TSMOM-2012",
            "SRC-CRYPTO-TREND-2020",
            "SRC-BINANCE-KLINE",
            "SRC-BINANCE-DEPTH",
            "SRC-BINANCE-AGGTRADE",
        ),
    ),
    "BREAKOUT_RETEST_15M_V2": StrategyResearchContract(
        strategy_version="V2",
        required_market_data=_INTRADAY_REQUIRED_MARKET_DATA,
        minimum_warmup_ko=_INTRADAY_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "15분 32봉 구조 돌파를 즉시 추격하지 않고 다음 완성봉이 돌파선을 되짚은 뒤 "
            "지지·저항으로 확인하며 공개 흐름이 재정렬될 때만 추세에 합류한다."
        ),
        falsification_conditions_ko=(
            "돌파봉 상대거래량·24시간 모멘텀·ADX 또는 상위 추세 정렬 실패",
            "돌파선 재확인 봉이 구조 안으로 복귀하거나 현재 공개 흐름 확인 실패",
            "구조 손절 거리·실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_INTRADAY_EDGE_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_INTRADAY_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_INTRADAY_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-TSMOM-2012",
            "SRC-CRYPTO-TREND-2020",
            "SRC-BINANCE-KLINE",
            "SRC-BINANCE-DEPTH",
            "SRC-BINANCE-AGGTRADE",
        ),
    ),
    "BREAKOUT_RETEST_30M_V2": StrategyResearchContract(
        strategy_version="V2",
        required_market_data=_INTRADAY_REQUIRED_MARKET_DATA,
        minimum_warmup_ko=_INTRADAY_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "30분 24봉 구조 돌파와 다음 봉의 보수적 재확인, 1시간 추세와 현재 공개 "
            "호가·체결 흐름이 모두 정렬될 때 더 긴 추세 구간에 합류한다."
        ),
        falsification_conditions_ko=(
            "돌파봉 상대거래량·24시간 모멘텀·ADX 또는 상위 추세 정렬 실패",
            "돌파선 재확인 봉이 구조 안으로 복귀하거나 현재 공개 흐름 확인 실패",
            "구조 손절 거리·실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_INTRADAY_EDGE_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_INTRADAY_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_INTRADAY_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-TSMOM-2012",
            "SRC-CRYPTO-TREND-2020",
            "SRC-BINANCE-KLINE",
            "SRC-BINANCE-DEPTH",
            "SRC-BINANCE-AGGTRADE",
        ),
    ),
    "MULTISPEED_TREND_RECLAIM_30M_V2": StrategyResearchContract(
        strategy_version="V2",
        required_market_data=_INTRADAY_REQUIRED_MARKET_DATA,
        minimum_warmup_ko=_INTRADAY_MINIMUM_WARMUP_KO,
        entry_hypothesis_ko=(
            "완성 30분·1시간 추세와 24시간 모멘텀이 같은 방향일 때 30분 EMA20 조정 뒤 "
            "이전 고저 회복과 현재 공개 흐름을 함께 확인하면 다중속도 추세가 재개될 수 있다."
        ),
        falsification_conditions_ko=(
            "30분·1시간 방향 또는 24시간 모멘텀·ADX·상대거래량 gate 실패",
            "EMA20 조정 뒤 이전 고저 회복과 현재 공개 흐름 확인 실패",
            "구조 손절 거리·실행가능 bid·ask 비용후 순 R:R gate 실패",
        ),
        edge_decay_policy_ko=_INTRADAY_EDGE_POLICY_KO,
        risk_budget_rule_ko=_RISK_BUDGET_RULE_KO,
        target_universe_ko=_INTRADAY_TARGET_UNIVERSE_KO,
        data_leakage_guards_ko=_INTRADAY_DATA_LEAKAGE_GUARDS_KO,
        research_source_ids=(
            "SRC-TSMOM-2012",
            "SRC-CRYPTO-TREND-2020",
            "SRC-BINANCE-KLINE",
            "SRC-BINANCE-DEPTH",
            "SRC-BINANCE-AGGTRADE",
        ),
    ),
}


@dataclass(slots=True)
class StrategySetting:
    mode: StrategyMode = StrategyMode.ACTIVE
    lifecycle: StrategyLifecycle = StrategyLifecycle.RESEARCH
    long_enabled: bool = True
    short_enabled: bool = True
    revision: int = 0
    manual_lock: bool = False
    changed_by: StrategyChangeSource = StrategyChangeSource.MIGRATION
    change_reason: str = "SAFE_DEFAULT"
    updated_ts_ms: int = 0

    def direction_enabled(self, side: Side) -> bool:
        return self.long_enabled if side is Side.LONG else self.short_enabled


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    strategy_id: str
    lifecycle: StrategyLifecycle
    expected_revision: int
    reason: str


class StrategyRegistry:
    """전략 메타데이터와 증거 기반 lifecycle 변경 이력을 중앙 관리한다."""

    def __init__(self) -> None:
        self._setting_lock = RLock()
        descriptors = (
            StrategyDescriptor(
                strategy_id="LSA_REVERSAL_V1",
                display_name_ko="급락·급등 쓸기 반전",
                short_name="LSA 반전",
                summary_ko=(
                    "반전 가설을 연구했으나 비용후 train·holdout 실패로 기본 중지됐습니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=LiquiditySweepStrategy(),
                exit_style=ExitStyle.REVERSION_70_30,
                research_contract=_RESEARCH_CONTRACTS["LSA_REVERSAL_V1"],
            ),
            StrategyDescriptor(
                strategy_id="CBR_CONTINUATION_V1",
                display_name_ko="압축 돌파 재가속",
                short_name="CBR 돌파",
                summary_ko="압축 뒤 돌파를 추격하지 않고 눌림과 재가속을 확인합니다.",
                stability=StrategyStability.STABLE,
                supported_regimes=(Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=CompressionBreakoutStrategy(),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["CBR_CONTINUATION_V1"],
            ),
            StrategyDescriptor(
                strategy_id="VWAP_EXHAUSTION_REVERSION_V1",
                display_name_ko="VWAP 과도이탈 평균복귀",
                short_name="VWAP 소진",
                summary_ko="범위장에서 micro-VWAP 이탈과 공격 흐름 소진을 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE,),
                evaluator=VwapExhaustionStrategy(),
                exit_style=ExitStyle.REVERSION_70_30,
                research_contract=_RESEARCH_CONTRACTS["VWAP_EXHAUSTION_REVERSION_V1"],
            ),
            StrategyDescriptor(
                strategy_id="OFI_CONTINUATION_PULLBACK_V1",
                display_name_ko="OFI 추세 눌림 지속",
                short_name="OFI 눌림",
                summary_ko=(
                    "다중 OFI 눌림 가설을 연구했으나 저장 train과 후기 자연표본이 "
                    "모두 비용후 실패해 기본 중지됐습니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=OfiPullbackStrategy(),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["OFI_CONTINUATION_PULLBACK_V1"],
            ),
            StrategyDescriptor(
                strategy_id="QUEUE_MICROPRICE_MOMENTUM_V1",
                display_name_ko="호가 쏠림 순간추세",
                short_name="호가 쏠림",
                summary_ko=(
                    "호가 불균형·OFI·체결 흐름을 연구했으나 비용후 검증 실패로 기본 중지됐습니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=QueueMicropriceStrategy(),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["QUEUE_MICROPRICE_MOMENTUM_V1"],
            ),
            StrategyDescriptor(
                strategy_id="AGGRESSOR_FLOW_CONTINUATION_V1",
                display_name_ko="강한 체결 흐름 지속",
                short_name="체결흐름",
                summary_ko="강한 공격 체결이 실제 가격 반응과 함께 지속되는지 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=AggressorFlowStrategy(),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["AGGRESSOR_FLOW_CONTINUATION_V1"],
            ),
            StrategyDescriptor(
                strategy_id="MULTILEVEL_MICROPRICE_MOMENTUM_V1",
                display_name_ko="다중호가 공정가 추세",
                short_name="다중호가",
                summary_ko="10단계 호가 공정가·OFI·체결 흐름의 같은 방향 지속을 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=MultilevelMicropriceStrategy(),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["MULTILEVEL_MICROPRICE_MOMENTUM_V1"],
            ),
            StrategyDescriptor(
                strategy_id="DEPTH_ADJUSTED_OFI_IMPULSE_V1",
                display_name_ko="깊이보정 OFI 충격",
                short_name="깊이 OFI",
                summary_ko=(
                    "호가 깊이보정 OFI를 연구했으나 보수적 비용후 검증 실패로 기본 중지됐습니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=DepthAdjustedOfiStrategy(),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["DEPTH_ADJUSTED_OFI_IMPULSE_V1"],
            ),
            StrategyDescriptor(
                strategy_id="OFI_RETURN_CONFLUENCE_V1",
                display_name_ko="OFI·단기수익률 동행",
                short_name="OFI·가격동행",
                summary_ko="깊이보정 주문흐름과 최근 가격 방향이 함께 이어지는지 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=OfiReturnConfluenceStrategy(),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["OFI_RETURN_CONFLUENCE_V1"],
            ),
            StrategyDescriptor(
                strategy_id="BOOK_SLOPE_ASYMMETRY_V1",
                display_name_ko="호가 기울기 비대칭",
                short_name="호가 기울기",
                summary_ko=(
                    "10단계 호가의 한쪽이 얇고 반대쪽 지지가 두꺼운 상태의 지속을 확인합니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=BookSlopeAsymmetryStrategy(),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["BOOK_SLOPE_ASYMMETRY_V1"],
            ),
            StrategyDescriptor(
                strategy_id="HOURLY_MOMENTUM_BREAKOUT_V1",
                display_name_ko="1시간 모멘텀 돌파",
                short_name="시간봉 추세",
                summary_ko=(
                    "시간봉 가설은 독립 과거구간 166건에서 비용후 재현 실패해 기본 중지됐습니다."
                ),
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=HourlyMomentumBreakoutStrategy(),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["HOURLY_MOMENTUM_BREAKOUT_V1"],
                horizon_class="INTRADAY_SWING",
                expected_holding_seconds=(3_600, 129_600),
                signal_half_life_seconds=5,
                required_timeframes=("1h", "4h EMA", "24h momentum"),
                exit_model="ATR_TP1_TP2_SL_NO_TIME_EXIT",
                take_profit_1_r=Decimal("2.2"),
                take_profit_2_r=Decimal("4.5"),
                entry_rules_ko=(
                    "완성된 1시간 봉만 사용",
                    "EMA20·50와 EMA80·200의 상승·하락 방향 일치",
                    "24시간 변화율이 방향별 2% 이상",
                    "직전 20시간 고가·저가 돌파",
                    "ADX 20 이상·상대 거래량 1.1배 이상",
                    "신호 후 5초 이내 실제 bid·ask 비용 검사 통과",
                ),
                exit_rules_ko=(
                    "초기 손절은 1.8 ATR, 최소 진입가의 0.3%",
                    "TP1에서 2.2R·40% 분할 익절",
                    "TP2에서 4.5R은 60% 잔량 익절",
                    "손절은 넓히지 않고 TP1 후 비용 보전 방향으로만 조임",
                    "시간만으로 종료하지 않고 TP·구조 손절로 결판",
                ),
                max_hold_seconds=None,
                edge_decay_enabled=False,
            ),
            StrategyDescriptor(
                strategy_id="TREND_PULLBACK_RECLAIM_15M_V2",
                display_name_ko="15분 추세 눌림 재상승",
                short_name="15분 눌림",
                summary_ko="상위 추세 안에서 EMA20 조정 뒤 이전 고저 회복을 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=IntradayTrendStrategy(
                    strategy_id="TREND_PULLBACK_RECLAIM_15M_V2",
                    variant=IntradayTrendVariant.PULLBACK_RECLAIM_15M,
                    interval_seconds=900,
                    take_profit_2_r=2.8,
                ),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["TREND_PULLBACK_RECLAIM_15M_V2"],
                horizon_class="INTRADAY_SWING",
                expected_holding_seconds=(1_800, 28_800),
                signal_half_life_seconds=5,
                required_timeframes=("15m", "1h", "24h momentum", "public book flow"),
                exit_model="STRUCTURE_TP1_TP2_SL_NO_TIME_EXIT",
                take_profit_1_r=Decimal("1.4"),
                take_profit_2_r=Decimal("2.8"),
                entry_rules_ko=(
                    "완성 15분봉 100개와 완성 1시간봉 50개 이상",
                    "EMA20·EMA80·1시간 추세와 24시간 모멘텀 1% 이상 정렬",
                    "ADX 18 이상·상대거래량 0.8배 이상",
                    "EMA20 눌림 뒤 이전 고저 회복",
                    "신호 후 5초 이내 실제 bid·ask·OFI·체결 흐름 확인",
                ),
                exit_rules_ko=(
                    "최근 두 완성봉 구조 밖에 초기 손절 고정",
                    "TP1 1.4R에서 40%·TP2 2.8R에서 60% 익절",
                    "일반 근거약화 조기청산 없음·손절은 넓히지 않음",
                    "시간만으로 종료하지 않고 TP·구조 손절로 결판",
                ),
                max_hold_seconds=None,
                edge_decay_enabled=False,
            ),
            StrategyDescriptor(
                strategy_id="BREAKOUT_RETEST_15M_V2",
                display_name_ko="15분 돌파 후 재확인",
                short_name="15분 돌파",
                summary_ko="돌파를 추격하지 않고 다음 완성봉의 지지·저항 재확인을 기다립니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=IntradayTrendStrategy(
                    strategy_id="BREAKOUT_RETEST_15M_V2",
                    variant=IntradayTrendVariant.BREAKOUT_RETEST_15M,
                    interval_seconds=900,
                    take_profit_2_r=3.2,
                ),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["BREAKOUT_RETEST_15M_V2"],
                horizon_class="INTRADAY_SWING",
                expected_holding_seconds=(3_600, 43_200),
                signal_half_life_seconds=5,
                required_timeframes=("15m", "1h", "24h momentum", "public book flow"),
                exit_model="STRUCTURE_TP1_TP2_SL_NO_TIME_EXIT",
                take_profit_1_r=Decimal("1.6"),
                take_profit_2_r=Decimal("3.2"),
                entry_rules_ko=(
                    "완성 15분봉의 직전 32봉 고저 돌파",
                    "돌파봉 상대거래량 1.1배·ADX 20·24시간 모멘텀 1.5% 이상",
                    "다음 완성봉이 돌파선을 다시 확인하고 구조 밖에서 마감",
                    "완성 1시간 추세와 실제 bid·ask·OFI·체결 흐름 정렬",
                ),
                exit_rules_ko=(
                    "재확인 구조 밖에 초기 손절 고정",
                    "TP1 1.6R에서 40%·TP2 3.2R에서 60% 익절",
                    "일반 근거약화 조기청산 없음·손절은 넓히지 않음",
                    "시간만으로 종료하지 않고 TP·구조 손절로 결판",
                ),
                max_hold_seconds=None,
                edge_decay_enabled=False,
            ),
            StrategyDescriptor(
                strategy_id="BREAKOUT_RETEST_30M_V2",
                display_name_ko="30분 돌파 후 재확인",
                short_name="30분 돌파",
                summary_ko="더 긴 30분 구조 돌파와 다음 봉 재확인을 함께 봅니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=IntradayTrendStrategy(
                    strategy_id="BREAKOUT_RETEST_30M_V2",
                    variant=IntradayTrendVariant.BREAKOUT_RETEST_30M,
                    interval_seconds=1_800,
                    take_profit_2_r=3.2,
                ),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["BREAKOUT_RETEST_30M_V2"],
                horizon_class="INTRADAY_SWING",
                expected_holding_seconds=(7_200, 64_800),
                signal_half_life_seconds=5,
                required_timeframes=("30m", "1h", "24h momentum", "public book flow"),
                exit_model="STRUCTURE_TP1_TP2_SL_NO_TIME_EXIT",
                take_profit_1_r=Decimal("1.6"),
                take_profit_2_r=Decimal("3.2"),
                entry_rules_ko=(
                    "완성 30분봉의 직전 24봉 고저 돌파",
                    "돌파봉 상대거래량 1.0배·ADX 20·24시간 모멘텀 1.5% 이상",
                    "다음 완성봉이 돌파선을 다시 확인하고 구조 밖에서 마감",
                    "완성 1시간 추세와 실제 bid·ask·OFI·체결 흐름 정렬",
                ),
                exit_rules_ko=(
                    "재확인 구조 밖에 초기 손절 고정",
                    "TP1 1.6R에서 40%·TP2 3.2R에서 60% 익절",
                    "일반 근거약화 조기청산 없음·손절은 넓히지 않음",
                    "시간만으로 종료하지 않고 TP·구조 손절로 결판",
                ),
                max_hold_seconds=None,
                edge_decay_enabled=False,
            ),
            StrategyDescriptor(
                strategy_id="MULTISPEED_TREND_RECLAIM_30M_V2",
                display_name_ko="30분·1시간 추세 재합류",
                short_name="다중추세",
                summary_ko="30분 조정 뒤 1시간 방향으로 다시 합류하는 구간을 확인합니다.",
                stability=StrategyStability.EXPERIMENTAL,
                supported_regimes=(Regime.RANGE, Regime.TREND_UP, Regime.TREND_DOWN),
                evaluator=IntradayTrendStrategy(
                    strategy_id="MULTISPEED_TREND_RECLAIM_30M_V2",
                    variant=IntradayTrendVariant.MULTISPEED_RECLAIM_30M,
                    interval_seconds=1_800,
                    take_profit_2_r=3.0,
                ),
                exit_style=ExitStyle.TREND_40_60,
                research_contract=_RESEARCH_CONTRACTS["MULTISPEED_TREND_RECLAIM_30M_V2"],
                horizon_class="INTRADAY_SWING",
                expected_holding_seconds=(3_600, 57_600),
                signal_half_life_seconds=5,
                required_timeframes=("30m", "1h", "24h momentum", "public book flow"),
                exit_model="STRUCTURE_TP1_TP2_SL_NO_TIME_EXIT",
                take_profit_1_r=Decimal("1.5"),
                take_profit_2_r=Decimal("3.0"),
                entry_rules_ko=(
                    "완성 30분 EMA20·EMA80와 완성 1시간 추세 정렬",
                    "24시간 모멘텀 1.2%·ADX 18·상대거래량 0.9배 이상",
                    "EMA20 조정 뒤 이전 고저 회복",
                    "신호 후 5초 이내 실제 bid·ask·OFI·체결 흐름 확인",
                ),
                exit_rules_ko=(
                    "최근 두 완성봉 구조 밖에 초기 손절 고정",
                    "TP1 1.5R에서 40%·TP2 3.0R에서 60% 익절",
                    "일반 근거약화 조기청산 없음·손절은 넓히지 않음",
                    "시간만으로 종료하지 않고 TP·구조 손절로 결판",
                ),
                max_hold_seconds=None,
                edge_decay_enabled=False,
            ),
        )
        self._descriptors = {item.strategy_id: item for item in descriptors}
        active_ids: set[str] = set()
        self._settings = {
            item.strategy_id: StrategySetting(
                mode=(
                    StrategyMode.ACTIVE
                    if item.strategy_id in active_ids
                    else StrategyMode.OFF
                    if item.strategy_id in POLICY_RETIRED_STRATEGY_IDS
                    or not item.default_research_enabled
                    else StrategyMode.SHADOW
                ),
                lifecycle=(
                    StrategyLifecycle.ACTIVE
                    if item.strategy_id in active_ids
                    else StrategyLifecycle.RETIRED
                    if item.strategy_id in POLICY_RETIRED_STRATEGY_IDS
                    else StrategyLifecycle.RESEARCH
                    if not item.default_research_enabled
                    else StrategyLifecycle.SHADOW
                ),
                change_reason=(
                    POLICY_RETIREMENT_REASONS[item.strategy_id]
                    if item.strategy_id in POLICY_RETIRED_STRATEGY_IDS
                    else "V6_LEGACY_COMPONENT_HISTORY_ONLY"
                    if not item.default_research_enabled
                    else POLICY_SHADOW_DEFAULT_REASON
                    if item.strategy_id in POLICY_SHADOW_DEFAULT_IDS
                    else "SAFE_DEFAULT"
                ),
            )
            for item in descriptors
        }
        self._revision_history = {
            strategy_id: {0: self._setting_row(strategy_id)} for strategy_id in self._settings
        }
        validate_family_contract(self)

    @property
    def strategy_ids(self) -> tuple[str, ...]:
        return tuple(self._descriptors)

    def descriptor(self, strategy_id: str) -> StrategyDescriptor:
        try:
            return self._descriptors[strategy_id]
        except KeyError as error:
            raise ValueError(f"알 수 없는 전략: {strategy_id}") from error

    def setting(self, strategy_id: str) -> StrategySetting:
        self.descriptor(strategy_id)
        return self._settings[strategy_id]

    def is_policy_retired(self, strategy_id: str) -> bool:
        self.descriptor(strategy_id)
        return strategy_id in POLICY_RETIRED_STRATEGY_IDS

    @_setting_locked
    def enforce_policy_retirements(
        self,
        *,
        updated_ts_ms: int,
    ) -> tuple[dict[str, object], ...]:
        """구버전 Run 설정이 비용후 퇴역 결정을 되살리지 못하게 migration한다."""

        changed: list[dict[str, object]] = []
        for strategy_id in sorted(POLICY_RETIRED_STRATEGY_IDS):
            setting = self.setting(strategy_id)
            desired_reason = POLICY_RETIREMENT_REASONS[strategy_id]
            if (
                setting.mode is StrategyMode.OFF
                and setting.lifecycle is StrategyLifecycle.RETIRED
                and setting.change_reason == desired_reason
            ):
                continue
            setting.mode = StrategyMode.OFF
            setting.lifecycle = StrategyLifecycle.RETIRED
            setting.revision += 1
            setting.manual_lock = False
            setting.changed_by = StrategyChangeSource.MIGRATION
            setting.change_reason = desired_reason
            setting.updated_ts_ms = max(updated_ts_ms, setting.updated_ts_ms + 1)
            row = self._setting_row(strategy_id)
            self._revision_history[strategy_id][setting.revision] = row
            changed.append(row)
        return tuple(changed)

    @_setting_locked
    def enforce_unproven_active_defaults(
        self,
        *,
        updated_ts_ms: int,
        validated_governor_active_revisions: Mapping[str, frozenset[int]] | None = None,
    ) -> tuple[dict[str, object], ...]:
        """현재 증거로 재검증된 Governor ACTIVE만 복구하고 나머지는 이관한다."""

        changed: list[dict[str, object]] = []
        validated_revisions = validated_governor_active_revisions or {}
        for strategy_id in sorted(self.strategy_ids):
            setting = self.setting(strategy_id)
            descriptor = self.descriptor(strategy_id)
            has_active_state = (
                setting.mode is StrategyMode.ACTIVE
                or setting.lifecycle is StrategyLifecycle.ACTIVE
            )
            valid_governor_active = (
                setting.mode is StrategyMode.ACTIVE
                and setting.lifecycle is StrategyLifecycle.ACTIVE
                and descriptor.role is StrategyRole.ENTRY
                and descriptor.is_current_variant
                and descriptor.default_research_enabled
                and not self.is_policy_retired(strategy_id)
                and self._has_active_governor_lineage(
                    strategy_id,
                    validated_revisions=validated_revisions.get(strategy_id, frozenset()),
                )
            )
            if not has_active_state or valid_governor_active:
                continue
            if self.is_policy_retired(strategy_id):
                setting.mode = StrategyMode.OFF
                setting.lifecycle = StrategyLifecycle.RETIRED
                change_reason = POLICY_RETIREMENT_REASONS[strategy_id]
            elif (
                descriptor.role is not StrategyRole.ENTRY
                or not descriptor.default_research_enabled
            ):
                setting.mode = StrategyMode.OFF
                setting.lifecycle = StrategyLifecycle.RESEARCH
                change_reason = "V6_LEGACY_COMPONENT_HISTORY_ONLY"
            else:
                setting.mode = StrategyMode.SHADOW
                setting.lifecycle = StrategyLifecycle.SHADOW
                change_reason = (
                    POLICY_SHADOW_DEFAULT_REASON
                    if strategy_id in POLICY_SHADOW_DEFAULT_IDS
                    else UNPROVEN_ACTIVE_RECOVERY_REASON
                )
            setting.revision += 1
            setting.manual_lock = False
            setting.changed_by = StrategyChangeSource.MIGRATION
            setting.change_reason = change_reason
            setting.updated_ts_ms = max(updated_ts_ms, setting.updated_ts_ms + 1)
            row = self._setting_row(strategy_id)
            self._revision_history[strategy_id][setting.revision] = row
            changed.append(row)
        return tuple(changed)

    def _has_active_governor_lineage(
        self,
        strategy_id: str,
        *,
        validated_revisions: frozenset[int],
    ) -> bool:
        """현재 ACTIVE 구간에 재검증된 Governor 승격 revision이 있는지 확인한다."""

        history = self._revision_history[strategy_id]
        for revision in sorted(history, reverse=True):
            row = history[revision]
            if (
                row.get("mode") != StrategyMode.ACTIVE.value
                or row.get("lifecycle") != StrategyLifecycle.ACTIVE.value
            ):
                return False
            changed_by = row.get("changed_by")
            if changed_by == StrategyChangeSource.AUTO_GOVERNOR.value:
                return revision in validated_revisions
            if changed_by != StrategyChangeSource.USER_UI.value:
                return False
        return False

    @_setting_locked
    def enforce_v6_family_runtime_policy(
        self,
        *,
        updated_ts_ms: int,
    ) -> tuple[dict[str, object], ...]:
        """Legacy 구성요소는 기록을 보존하되 새 독립 entry를 만들지 못하게 한다."""

        changed: list[dict[str, object]] = []
        for strategy_id in self.strategy_ids:
            descriptor = self.descriptor(strategy_id)
            setting = self.setting(strategy_id)
            if (
                descriptor.role is StrategyRole.ENTRY
                and not descriptor.is_current_variant
                and setting.mode is StrategyMode.ACTIVE
            ):
                setting.mode = StrategyMode.SHADOW
                setting.lifecycle = StrategyLifecycle.CHALLENGER
                setting.revision += 1
                setting.manual_lock = False
                setting.changed_by = StrategyChangeSource.MIGRATION
                setting.change_reason = "V6_NON_CURRENT_VARIANT_SHADOW_ONLY"
                setting.updated_ts_ms = max(updated_ts_ms, setting.updated_ts_ms + 1)
                row = self._setting_row(strategy_id)
                self._revision_history[strategy_id][setting.revision] = row
                changed.append(row)
                continue
            if descriptor.default_research_enabled or self.is_policy_retired(strategy_id):
                continue
            if (
                setting.mode is StrategyMode.OFF
                and setting.lifecycle is StrategyLifecycle.RESEARCH
                and setting.change_reason == "V6_LEGACY_COMPONENT_HISTORY_ONLY"
            ):
                continue
            setting.mode = StrategyMode.OFF
            setting.lifecycle = StrategyLifecycle.RESEARCH
            setting.revision += 1
            setting.manual_lock = False
            setting.changed_by = StrategyChangeSource.MIGRATION
            setting.change_reason = "V6_LEGACY_COMPONENT_HISTORY_ONLY"
            setting.updated_ts_ms = max(updated_ts_ms, setting.updated_ts_ms + 1)
            row = self._setting_row(strategy_id)
            self._revision_history[strategy_id][setting.revision] = row
            changed.append(row)
        return tuple(changed)

    @_setting_locked
    def configure(
        self,
        strategy_id: str,
        *,
        mode: StrategyMode,
        long_enabled: bool,
        short_enabled: bool,
        expected_revision: int | None = None,
        manual_lock: bool | None = None,
        lifecycle: StrategyLifecycle | None = None,
        source: StrategyChangeSource = StrategyChangeSource.USER_UI,
        reason: str = "USER_CONFIGURATION",
        updated_ts_ms: int = 0,
    ) -> StrategySetting:
        setting = self.setting(strategy_id)
        descriptor = self.descriptor(strategy_id)
        if expected_revision is not None and expected_revision != setting.revision:
            raise StrategyRevisionConflict(self._setting_row(strategy_id))
        if source is StrategyChangeSource.AUTO_GOVERNOR and setting.manual_lock:
            raise StrategyManualLockConflict(f"사용자가 고정한 전략 설정입니다: {strategy_id}")
        resolved_lifecycle = lifecycle or self.lifecycle_for_mode(mode)
        if mode is not self.mode_for_lifecycle(resolved_lifecycle):
            raise ValueError("전략 lifecycle과 실행 mode가 일치하지 않습니다.")
        if (
            mode is StrategyMode.ACTIVE
            and setting.mode is not StrategyMode.ACTIVE
            and source is StrategyChangeSource.USER_UI
        ):
            raise ValueError(
                "Shared Capital ACTIVE는 formal OOS gate를 통과한 Governor만 적용합니다."
            )
        if mode is StrategyMode.ACTIVE and not descriptor.is_current_variant:
            raise ValueError("V6 non-current variant는 독립 SHADOW로만 평가할 수 있습니다.")
        if (
            not descriptor.default_research_enabled
            and mode is not StrategyMode.OFF
            and source not in {StrategyChangeSource.MIGRATION, StrategyChangeSource.RECOVERY}
        ):
            raise ValueError("V6 legacy 구성요소는 독립 entry 모드로 켤 수 없습니다.")
        setting.mode = mode
        setting.lifecycle = resolved_lifecycle
        setting.long_enabled = long_enabled
        setting.short_enabled = short_enabled
        setting.revision += 1
        setting.manual_lock = (
            source is StrategyChangeSource.USER_UI if manual_lock is None else manual_lock
        )
        setting.changed_by = source
        setting.change_reason = reason
        setting.updated_ts_ms = updated_ts_ms
        self._revision_history[strategy_id][setting.revision] = self._setting_row(strategy_id)
        return setting

    @_setting_locked
    def apply_lifecycle_transitions(
        self,
        transitions: tuple[LifecycleTransition, ...],
        *,
        source: StrategyChangeSource,
        updated_ts_ms: int,
    ) -> tuple[dict[str, object], ...]:
        """여러 전략의 lifecycle 교체를 먼저 전체 검증한 뒤 한 번에 반영한다."""

        if not transitions:
            raise ValueError("반영할 lifecycle 전환이 없습니다.")
        strategy_ids = [transition.strategy_id for transition in transitions]
        if len(strategy_ids) != len(set(strategy_ids)):
            raise ValueError("하나의 전략을 한 전환에서 여러 번 바꿀 수 없습니다.")

        for transition in transitions:
            setting = self.setting(transition.strategy_id)
            descriptor = self.descriptor(transition.strategy_id)
            if transition.expected_revision != setting.revision:
                raise StrategyRevisionConflict(self._setting_row(transition.strategy_id))
            if source is StrategyChangeSource.AUTO_GOVERNOR and setting.manual_lock:
                raise StrategyManualLockConflict(
                    f"사용자가 고정한 전략 설정입니다: {transition.strategy_id}"
                )
            if (
                transition.lifecycle is StrategyLifecycle.ACTIVE
                and setting.mode is not StrategyMode.ACTIVE
                and source is StrategyChangeSource.USER_UI
            ):
                raise ValueError(
                    "Shared Capital ACTIVE는 formal OOS gate를 통과한 Governor만 적용합니다."
                )
            if (
                transition.lifecycle is StrategyLifecycle.ACTIVE
                and not descriptor.is_current_variant
            ):
                raise ValueError("V6 non-current variant는 ACTIVE lifecycle로 바꿀 수 없습니다.")
            if (
                not descriptor.default_research_enabled
                and self.mode_for_lifecycle(transition.lifecycle) is not StrategyMode.OFF
                and source not in {StrategyChangeSource.MIGRATION, StrategyChangeSource.RECOVERY}
            ):
                raise ValueError("V6 legacy 구성요소는 독립 entry lifecycle로 바꿀 수 없습니다.")

        changed_rows: list[dict[str, object]] = []
        for transition in transitions:
            setting = self.setting(transition.strategy_id)
            setting.lifecycle = transition.lifecycle
            setting.mode = self.mode_for_lifecycle(transition.lifecycle)
            setting.revision += 1
            setting.manual_lock = source is StrategyChangeSource.USER_UI
            setting.changed_by = source
            setting.change_reason = transition.reason
            setting.updated_ts_ms = updated_ts_ms
            row = self._setting_row(transition.strategy_id)
            self._revision_history[transition.strategy_id][setting.revision] = row
            changed_rows.append(row)
        return tuple(changed_rows)

    @_setting_locked
    def restore_setting(
        self,
        strategy_id: str,
        *,
        mode: StrategyMode,
        long_enabled: bool,
        short_enabled: bool,
        revision: int,
        manual_lock: bool,
        changed_by: StrategyChangeSource,
        change_reason: str,
        updated_ts_ms: int,
        lifecycle: StrategyLifecycle | None = None,
    ) -> StrategySetting:
        setting = self.setting(strategy_id)
        if revision < setting.revision:
            return setting
        setting.mode = mode
        setting.lifecycle = lifecycle or self.lifecycle_for_mode(mode)
        setting.long_enabled = long_enabled
        setting.short_enabled = short_enabled
        setting.revision = revision
        setting.manual_lock = manual_lock
        setting.changed_by = changed_by
        setting.change_reason = change_reason
        setting.updated_ts_ms = updated_ts_ms
        self._revision_history[strategy_id][setting.revision] = self._setting_row(strategy_id)
        return setting

    @_setting_locked
    def rollback(
        self,
        strategy_id: str,
        *,
        target_revision: int,
        expected_revision: int,
        source: StrategyChangeSource,
        reason: str,
        updated_ts_ms: int,
    ) -> StrategySetting:
        """과거 설정을 새 revision으로 복원해 감사 이력을 삭제하지 않는다."""

        setting = self.setting(strategy_id)
        if expected_revision != setting.revision:
            raise StrategyRevisionConflict(self._setting_row(strategy_id))
        if source is StrategyChangeSource.AUTO_GOVERNOR and setting.manual_lock:
            raise StrategyManualLockConflict(f"사용자가 고정한 전략 설정입니다: {strategy_id}")
        target = self._revision_history[strategy_id].get(target_revision)
        if target is None:
            raise ValueError(f"복원할 전략 revision을 찾을 수 없습니다: {target_revision}")
        target_mode = StrategyMode(str(target["mode"]))
        descriptor = self.descriptor(strategy_id)
        if not descriptor.default_research_enabled and target_mode is not StrategyMode.OFF:
            raise ValueError("V6 legacy 구성요소의 과거 entry 설정은 복원할 수 없습니다.")
        if (
            target_mode is StrategyMode.ACTIVE
            and setting.mode is not StrategyMode.ACTIVE
            and source is StrategyChangeSource.USER_UI
        ):
            raise ValueError(
                "Shared Capital ACTIVE는 formal OOS gate를 통과한 Governor만 복원합니다."
            )
        if (
            target_mode is StrategyMode.ACTIVE
            and not descriptor.is_current_variant
        ):
            raise ValueError("V6 non-current variant의 과거 ACTIVE 설정은 복원할 수 없습니다.")
        setting.mode = target_mode
        setting.lifecycle = StrategyLifecycle(str(target["lifecycle"]))
        setting.long_enabled = bool(target["long_enabled"])
        setting.short_enabled = bool(target["short_enabled"])
        setting.revision += 1
        setting.manual_lock = source is StrategyChangeSource.USER_UI
        setting.changed_by = source
        setting.change_reason = reason
        setting.updated_ts_ms = updated_ts_ms
        self._revision_history[strategy_id][setting.revision] = self._setting_row(strategy_id)
        return setting

    @_setting_locked
    def evaluation_enabled(self, strategy_id: str, side: Side) -> bool:
        setting = self.setting(strategy_id)
        return setting.mode is not StrategyMode.OFF and setting.direction_enabled(side)

    @_setting_locked
    def main_enabled(self, strategy_id: str, side: Side) -> bool:
        setting = self.setting(strategy_id)
        descriptor = self.descriptor(strategy_id)
        return (
            descriptor.role is StrategyRole.ENTRY
            and descriptor.is_current_variant
            and setting.mode is StrategyMode.ACTIVE
            and setting.direction_enabled(side)
        )

    @_setting_locked
    def shadow_enabled(self, strategy_id: str, side: Side) -> bool:
        setting = self.setting(strategy_id)
        return setting.mode in {
            StrategyMode.ACTIVE,
            StrategyMode.SHADOW,
        } and setting.direction_enabled(side)

    @_setting_locked
    def rows(self) -> list[dict[str, object]]:
        return [
            self._setting_row(descriptor.strategy_id)
            | {
                "strategy_id": descriptor.strategy_id,
                "display_name_ko": descriptor.display_name_ko,
                "short_name": descriptor.short_name,
                "summary_ko": descriptor.summary_ko,
                "stability": descriptor.stability.value,
                "supported_regimes": [regime.value for regime in descriptor.supported_regimes],
                "exit_style": descriptor.exit_style.value,
                "horizon_class": descriptor.horizon_class,
                "expected_holding_seconds": list(descriptor.expected_holding_seconds),
                "signal_half_life_seconds": descriptor.signal_half_life_seconds,
                "required_timeframes": list(descriptor.required_timeframes),
                "exit_model": descriptor.exit_model,
                "take_profit_1_r": str(descriptor.take_profit_1_r),
                "take_profit_2_r": str(descriptor.take_profit_2_r),
                "entry_rules_ko": list(descriptor.entry_rules_ko),
                "exit_rules_ko": list(descriptor.exit_rules_ko),
                "max_hold_seconds": descriptor.max_hold_seconds,
                "edge_decay_enabled": descriptor.edge_decay_enabled,
                "cost_model_version": descriptor.cost_model_version,
                "paper_only": descriptor.paper_only,
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
                "strategy_version": descriptor.research_contract.strategy_version,
                "required_market_data": list(descriptor.research_contract.required_market_data),
                "minimum_warmup_ko": descriptor.research_contract.minimum_warmup_ko,
                "entry_hypothesis_ko": (descriptor.research_contract.entry_hypothesis_ko),
                "falsification_conditions_ko": list(
                    descriptor.research_contract.falsification_conditions_ko
                ),
                "edge_decay_policy_ko": (descriptor.research_contract.edge_decay_policy_ko),
                "risk_budget_rule_ko": descriptor.research_contract.risk_budget_rule_ko,
                "target_universe_ko": descriptor.research_contract.target_universe_ko,
                "data_leakage_guards_ko": list(descriptor.research_contract.data_leakage_guards_ko),
                "research_source_ids": list(descriptor.research_contract.research_source_ids),
            }
            for descriptor in self._descriptors.values()
        ]

    @_setting_locked
    def setting_row(self, strategy_id: str) -> dict[str, object]:
        """현재 설정과 revision을 공개 계약으로 복사한다."""

        return dict(self._setting_row(strategy_id))

    @_setting_locked
    def revision_history(self, strategy_id: str) -> tuple[dict[str, object], ...]:
        """복구된 과거를 포함한 전략 설정 변경 이력을 revision 순으로 복사한다."""

        self.setting(strategy_id)
        return tuple(dict(row) for _, row in sorted(self._revision_history[strategy_id].items()))

    @staticmethod
    def mode_for_lifecycle(lifecycle: StrategyLifecycle) -> StrategyMode:
        if lifecycle is StrategyLifecycle.ACTIVE:
            return StrategyMode.ACTIVE
        if lifecycle in {StrategyLifecycle.SHADOW, StrategyLifecycle.CHALLENGER}:
            return StrategyMode.SHADOW
        return StrategyMode.OFF

    @staticmethod
    def lifecycle_for_mode(mode: StrategyMode) -> StrategyLifecycle:
        if mode is StrategyMode.ACTIVE:
            return StrategyLifecycle.ACTIVE
        if mode is StrategyMode.SHADOW:
            return StrategyLifecycle.SHADOW
        return StrategyLifecycle.RETIRED

    def _setting_row(self, strategy_id: str) -> dict[str, object]:
        setting = self.setting(strategy_id)
        return {
            "strategy_id": strategy_id,
            "mode": setting.mode.value,
            "lifecycle": setting.lifecycle.value,
            "long_enabled": setting.long_enabled,
            "short_enabled": setting.short_enabled,
            "settings_revision": setting.revision,
            "manual_lock": setting.manual_lock,
            "changed_by": setting.changed_by.value,
            "change_reason": setting.change_reason,
            "settings_updated_ts_ms": setting.updated_ts_ms,
            "policy_reactivation_locked": self.is_policy_retired(strategy_id),
        }
