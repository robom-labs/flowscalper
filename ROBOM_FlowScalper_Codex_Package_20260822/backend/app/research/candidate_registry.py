"""20개 alpha와 5개 exit를 결합한 PAPER 전용 100후보 사전등록 계약이다."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import product
from typing import Any

from backend.app.execution.trailing import (
    TrailingActivationRule,
    TrailingModel,
    TrailingPolicy,
)
from backend.app.research.alpha_evaluators import (
    ALPHA_EVALUATION_INTERVAL_SECONDS,
    ALPHA_EVALUATORS,
    ALPHA_PARAMETER_CONTRACTS,
)

HORIZON_MAXIMUM_HOLD_MS = {
    "MICRO_SCALP": 180_000,
    "FAST_INTRADAY": 3_600_000,
    "INTRADAY_SWING": 21_600_000,
}


class EvidenceGrade(StrEnum):
    EXACT_PUBLIC_RULE = "EXACT_PUBLIC_RULE"
    PUBLIC_AMBIGUOUS = "PUBLIC_AMBIGUOUS"
    RESEARCH_HYPOTHESIS = "RESEARCH_HYPOTHESIS"


class TrialLifecycle(StrEnum):
    RESEARCH = "RESEARCH"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class AlphaFamilySpec:
    family_id: str
    name: str
    horizon: str
    evidence_grade: EvidenceGrade
    entry_rule: str
    parameters: tuple[tuple[str, str], ...]
    source_ids: tuple[str, ...]
    existing_strategy_links: tuple[str, ...] = ()
    blocker_codes: tuple[str, ...] = ()

    @property
    def execution_allowed(self) -> bool:
        return not self.blocker_codes and self.family_id in ALPHA_EVALUATORS

    @property
    def evaluator_id(self) -> str | None:
        return (
            f"ALPHA_EVALUATOR_{self.family_id}_V1" if self.family_id in ALPHA_EVALUATORS else None
        )


@dataclass(frozen=True, slots=True)
class ExitModuleSpec:
    exit_id: str
    name: str
    activation_rule: str
    exit_rule: str
    parameters: tuple[tuple[str, str], ...]
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResearchTrialSpec:
    trial_id: str
    trial_number: int
    alpha: AlphaFamilySpec
    exit: ExitModuleSpec
    lifecycle: TrialLifecycle
    screening_eligible: bool
    paper_only: bool = True
    runtime_active: bool = False
    live_shadow_enabled: bool = False


def _parameters(**values: object) -> tuple[tuple[str, str], ...]:
    return tuple((key, str(value)) for key, value in values.items())


ALPHA_FAMILIES: tuple[AlphaFamilySpec, ...] = (
    AlphaFamilySpec(
        "F01",
        "SIHO exact current public strategy",
        "UNCONFIRMED",
        EvidenceGrade.PUBLIC_AMBIGUOUS,
        "공개 영상에서 exact entry가 복원된 뒤에만 정의한다.",
        (),
        ("SIHO_PUBLIC_VIDEO_EVIDENCE",),
        blocker_codes=(
            "BLOCKED_MISSING_ENTRY_RULE",
            "BLOCKED_MISSING_EXIT_RULE",
            "BLOCKED_MISSING_TIMEFRAME",
            "BLOCKED_MISSING_TRAILING_PARAMETER",
            "BLOCKED_MISSING_POSITION_SIZING",
        ),
    ),
    AlphaFamilySpec(
        "F02",
        "SIHO conservative interpretation",
        "UNCONFIRMED",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "F01의 공개 모호성을 보수적으로 해석하되 exact source가 완성된 뒤 사전등록한다.",
        (),
        ("SIHO_PUBLIC_VIDEO_EVIDENCE",),
        blocker_codes=("BLOCKED_MISSING_PUBLIC_RULES_FOR_INTERPRETATION",),
    ),
    AlphaFamilySpec(
        "F03",
        "Multi-timeframe EMA trend pullback",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "완성 1h·4h EMA 추세와 같은 방향의 15m 눌림 뒤 5m 종가 재가속에 진입한다.",
        _parameters(
            higher_timeframes="1h,4h",
            setup_timeframe="15m",
            trigger_timeframe="5m",
            ema_fast=20,
            ema_slow=50,
            adx_minimum=20,
            relative_volume_minimum=1.1,
            pullback_band_atr=0.5,
            closed_candle_only=True,
        ),
        ("FREQTRADE_LOOKAHEAD_RECURSIVE_GUIDANCE",),
        ("HOURLY_MOMENTUM_BREAKOUT_V1",),
    ),
    AlphaFamilySpec(
        "F04",
        "Donchian 20 breakout",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "직전 20개 완성봉 channel을 종가 돌파하고 추격거리가 제한될 때 진입한다.",
        _parameters(
            timeframe="5m",
            lookback=20,
            close_confirmation=True,
            maximum_chase_atr=0.5,
            relative_volume_minimum=1.2,
        ),
        ("DONCHIAN_PUBLIC_RULE",),
        ("CBR_CONTINUATION_V1", "HOURLY_MOMENTUM_BREAKOUT_V1"),
    ),
    AlphaFamilySpec(
        "F05",
        "Turtle Donchian 55 breakout",
        "INTRADAY_SWING",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "직전 55개 완성봉 channel 돌파를 ADX와 ATR 위험으로 확인한다.",
        _parameters(
            timeframe="1h",
            lookback=55,
            close_confirmation=True,
            adx_minimum=25,
            initial_stop_atr=2.0,
        ),
        ("TURTLE_DONCHIAN_PUBLIC_RULE",),
        ("HOURLY_MOMENTUM_BREAKOUT_V1",),
    ),
    AlphaFamilySpec(
        "F06",
        "Breakout retest continuation",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "완성봉 돌파 뒤 0.35ATR 이내 retest와 구조 회복·방향 OFI 재가속을 확인한다.",
        _parameters(
            timeframe="5m",
            breakout_lookback=20,
            retest_tolerance_atr=0.35,
            relative_volume_minimum=1.2,
            ofi_alignment_required=True,
            maximum_retest_bars=6,
        ),
        ("PUBLIC_BREAKOUT_RETEST_HYPOTHESIS",),
        ("CBR_CONTINUATION_V1", "OFI_CONTINUATION_PULLBACK_V1"),
    ),
    AlphaFamilySpec(
        "F07",
        "Supertrend ADX continuation",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "Supertrend 방향을 ADX·EMA slope·비용 gate가 모두 확인할 때만 진입한다.",
        _parameters(
            timeframe="15m",
            atr_period=10,
            supertrend_multiplier=3.0,
            adx_minimum=25,
            ema_slope_period=50,
            close_confirmation=True,
        ),
        ("PUBLIC_SUPERTREND_HYPOTHESIS",),
    ),
    AlphaFamilySpec(
        "F08",
        "VWAP trend pullback",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "1h EMA 추세에서 5m session VWAP 눌림 뒤 종가와 flow가 같은 방향으로 회복될 때 진입한다.",
        _parameters(
            trend_timeframe="1h",
            trigger_timeframe="5m",
            ema_fast=20,
            ema_slow=50,
            vwap_pullback_atr=0.35,
            ofi_alignment_required=True,
        ),
        ("PUBLIC_VWAP_HYPOTHESIS",),
        ("OFI_CONTINUATION_PULLBACK_V1",),
    ),
    AlphaFamilySpec(
        "F09",
        "Anchored VWAP continuation",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        (
            "미래를 보지 않는 UTC session open 또는 확정 breakout event에 "
            "anchor한 VWAP 회복에 진입한다."
        ),
        _parameters(
            timeframe="5m",
            anchor="UTC_00_SESSION_OPEN_OR_CONFIRMED_DONCHIAN20_BREAKOUT",
            confirmation_bars=2,
            relative_volume_minimum=1.2,
            deterministic_anchor=True,
        ),
        ("PUBLIC_ANCHORED_VWAP_HYPOTHESIS",),
    ),
    AlphaFamilySpec(
        "F10",
        "Bollinger bandwidth squeeze breakout",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "자체 과거 bandwidth 하위 20% 압축 뒤 종가 돌파·RVOL·추세 정렬에 진입한다.",
        _parameters(
            timeframe="5m",
            bollinger_period=20,
            standard_deviations=2.0,
            bandwidth_percentile=20,
            percentile_lookback=240,
            relative_volume_minimum=1.3,
        ),
        ("PUBLIC_BOLLINGER_HYPOTHESIS",),
        ("CBR_CONTINUATION_V1",),
    ),
    AlphaFamilySpec(
        "F11",
        "Keltner TTM style compression breakout",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "Bollinger가 Keltner 안에서 3개 완성봉 유지된 뒤 종가·거래량 돌파에 진입한다.",
        _parameters(
            timeframe="5m",
            bollinger_period=20,
            bollinger_std=2.0,
            keltner_period=20,
            keltner_atr=1.5,
            minimum_compression_bars=3,
            relative_volume_minimum=1.2,
        ),
        ("PUBLIC_TTM_SQUEEZE_HYPOTHESIS",),
        ("CBR_CONTINUATION_V1",),
    ),
    AlphaFamilySpec(
        "F12",
        "ATR realized volatility expansion",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "단기 실현변동성이 장기창의 1.5배로 전환되고 방향 종가가 확정될 때 진입한다.",
        _parameters(
            timeframe="5m",
            fast_realized_vol_bars=12,
            slow_realized_vol_bars=72,
            expansion_ratio=1.5,
            close_location_minimum=0.75,
            maximum_impulse_atr=1.5,
        ),
        ("PUBLIC_VOLATILITY_EXPANSION_HYPOTHESIS",),
    ),
    AlphaFamilySpec(
        "F13",
        "Relative volume breakout",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "RVOL·trade count·방향 taker flow가 함께 증가하고 spread가 정상범위일 때 돌파에 진입한다.",
        _parameters(
            timeframe="5m",
            relative_volume_minimum=1.5,
            trade_count_z_minimum=1.0,
            long_taker_ratio_minimum=0.6,
            short_taker_ratio_maximum=0.4,
            spread_percentile_maximum=75,
        ),
        ("BINANCE_PUBLIC_KLINE_AND_TRADE_FIELDS",),
        ("AGGRESSOR_FLOW_CONTINUATION_V1",),
    ),
    AlphaFamilySpec(
        "F14",
        "Session opening range breakout",
        "FAST_INTRADAY",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "사전 고정한 UTC session의 첫 15분 범위를 5m 종가로 돌파할 때 진입한다.",
        _parameters(
            sessions_utc="00:00,08:00,13:30",
            opening_range_minutes=15,
            trigger_timeframe="5m",
            maximum_chase_atr=0.5,
            relative_volume_minimum=1.2,
        ),
        ("PUBLIC_OPENING_RANGE_HYPOTHESIS",),
    ),
    AlphaFamilySpec(
        "F15",
        "6h-bar 24h time series momentum",
        "INTRADAY_SWING",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "완성 6h봉 4개의 24h 수익률 방향이 EMA 추세와 같고 변동성 조정 기준을 넘을 때 진입한다.",
        _parameters(
            timeframe="6h",
            momentum_lookback_bars=4,
            absolute_return_minimum=0.02,
            ema_fast=20,
            ema_slow=50,
            momentum_volatility_ratio_minimum=1.0,
            closed_candle_only=True,
        ),
        ("MOSKOWITZ_OOI_PEDERSEN_2012_TSMOM",),
        ("HOURLY_MOMENTUM_BREAKOUT_V1",),
    ),
    AlphaFamilySpec(
        "F16",
        "Cross sectional momentum",
        "INTRADAY_SWING",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "당시 거래가능 universe의 24h 상대강도 상·하위 20%를 유동성 floor와 함께 평가한다.",
        _parameters(
            rebalance_timeframe="6h",
            momentum_lookback_hours=24,
            long_quantile=0.8,
            short_quantile=0.2,
            minimum_universe_size=20,
            point_in_time_universe=True,
        ),
        ("PUBLIC_CROSS_SECTIONAL_MOMENTUM_HYPOTHESIS",),
    ),
    AlphaFamilySpec(
        "F17",
        "Queue imbalance microprice",
        "MICRO_SCALP",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        (
            "top5 queue imbalance와 executable microprice 변위가 500ms 지속되고 "
            "비용을 넘을 때 진입한다."
        ),
        _parameters(
            depth_levels=5,
            long_imbalance_minimum=0.65,
            short_imbalance_maximum=0.35,
            microprice_spread_fraction=0.15,
            persistence_ms=500,
            sequence_valid_required=True,
        ),
        ("GOULD_BONART_2015_QUEUE_IMBALANCE",),
        ("QUEUE_MICROPRICE_MOMENTUM_V1",),
    ),
    AlphaFamilySpec(
        "F18",
        "Multi level order flow imbalance continuation",
        "MICRO_SCALP",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "top10 MLOFI를 현재 양방향 깊이로 정규화하고 robust z와 가격반응이 지속될 때 진입한다.",
        _parameters(
            depth_levels=10,
            robust_z_minimum=2.0,
            normalization="MEAN_BID_ASK_NOTIONAL_DEPTH",
            persistence_ms=500,
            price_response_required=True,
        ),
        ("XU_GOULD_HOWISON_2019_MLOFI",),
        ("DEPTH_ADJUSTED_OFI_IMPULSE_V1", "MULTILEVEL_MICROPRICE_MOMENTUM_V1"),
    ),
    AlphaFamilySpec(
        "F19",
        "Aggressor flow liquidity vacuum continuation",
        "MICRO_SCALP",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "signed notional·trade intensity와 반대호가 depth 감소가 지속될 때만 진입한다.",
        _parameters(
            signed_notional_z_minimum=2.0,
            trade_intensity_z_minimum=1.5,
            opposing_depth_depletion_minimum=0.2,
            spread_percentile_maximum=75,
            persistence_ms=500,
        ),
        ("PUBLIC_AGGRESSOR_FLOW_HYPOTHESIS",),
        ("AGGRESSOR_FLOW_CONTINUATION_V1",),
    ),
    AlphaFamilySpec(
        "F20",
        "Absorption VWAP exhaustion reversal",
        "MICRO_SCALP",
        EvidenceGrade.RESEARCH_HYPOTHESIS,
        "RANGE에서 VWAP 과도이탈·가격진전 둔화·refill·OFI와 microprice 반전을 확인한다.",
        _parameters(
            regime="RANGE",
            vwap_deviation_z_minimum=2.0,
            price_progress_efficiency_maximum=0.25,
            refill_minimum=0.2,
            ofi_reversal_required=True,
            microprice_reentry_required=True,
        ),
        ("PUBLIC_ABSORPTION_VWAP_HYPOTHESIS",),
        ("LSA_REVERSAL_V1", "VWAP_EXHAUSTION_REVERSION_V1"),
    ),
)


EXIT_MODULES: tuple[ExitModuleSpec, ...] = (
    ExitModuleSpec(
        "E01",
        "Fixed TP SL",
        "ENTRY_FILLED",
        "initial stop 1R와 fixed target 1.5R 중 먼저 executable bid·ask에 닿는 쪽을 체결한다.",
        _parameters(initial_stop_r=1.0, take_profit_r=1.5, partial_fraction=0.0),
        ("FLOWSCALPER_FIXED_TP_SL_BASELINE",),
    ),
    ExitModuleSpec(
        "E02",
        "Partial TP fee breakeven ATR runner",
        "TP1_EXECUTED",
        "1.5R에서 40%를 부분익절하고 나머지 60%를 fee breakeven과 ATR trail로 보호한다.",
        _parameters(
            tp1_r=1.5,
            tp1_fraction=0.4,
            runner_fraction=0.6,
            breakeven="ROUNDTRIP_FEE_PLUS_BUFFER",
            breakeven_buffer_r=0.1,
            atr_period=14,
            atr_multiplier=2.5,
            runner_evaluation_reference_r=3.0,
        ),
        ("FREQTRADE_TRAILING_OFFSET_GUIDANCE", "FLOWSCALPER_PARTIAL_TP"),
    ),
    ExitModuleSpec(
        "E03",
        "Profit activation percentage trailing",
        "EXECUTABLE_PRICE_REACHES_1R",
        "1R activation 뒤 favorable executable bid·ask의 0.8% 되돌림에 runner를 종료한다.",
        _parameters(
            activation_r=1.0,
            retracement_rate=0.008,
            partial_fraction=0.0,
            planning_reference_target_r=3.0,
        ),
        ("BYBIT_OFFICIAL_TRAILING_FORMULA",),
    ),
    ExitModuleSpec(
        "E04",
        "Chandelier structure trailing",
        "EXECUTABLE_PRICE_REACHES_1R",
        "1R activation 뒤 ATR Chandelier와 완료된 구조 stop 중 더 보수적인 단조 stop을 사용한다.",
        _parameters(
            activation_r=1.0,
            atr_period=14,
            atr_multiplier=2.5,
            structure_lookback_completed_bars=3,
            future_pivot_allowed=False,
            planning_reference_target_r=3.0,
        ),
        ("PUBLIC_CHANDELIER_HYPOTHESIS", "FREQTRADE_TRAILING_GUIDANCE"),
    ),
    ExitModuleSpec(
        "E05",
        "Edge decay volatility adaptive trailing",
        "EXECUTABLE_PRICE_REACHES_0.75R",
        "0.75R activation 뒤 ATR 2배 trail을 사용하고 사전정의 edge decay에서 1.2배로 좁힌다.",
        _parameters(
            activation_r=0.75,
            normal_atr_multiplier=2.0,
            adverse_atr_multiplier=1.2,
            adverse_signal_count=2,
            adverse_persistence_ms=3000,
            data_degraded_action="NO_FAVORABLE_UPDATE_OR_SAFE_EXIT",
            planning_reference_target_r=3.0,
        ),
        ("FLOWSCALPER_EDGE_DECAY_POLICY",),
    ),
)


def trailing_policy_for_exit(exit_id: str) -> TrailingPolicy | None:
    """사전등록 exit를 같은 PAPER trailing 실행계약으로 변환한다."""

    policies = {
        "E02": TrailingPolicy(
            policy_id="E02_PARTIAL_TP_ATR_RUNNER_V1",
            model=TrailingModel.ATR_CHANDELIER,
            activation_rule=TrailingActivationRule.TP1_TRIGGERED,
            activation_r=Decimal("1.5"),
            partial_tp_required=True,
            atr_multiplier=Decimal("2.5"),
        ),
        "E03": TrailingPolicy(
            policy_id="E03_PERCENT_TRAIL_V1",
            model=TrailingModel.FIXED_RATE,
            activation_rule=TrailingActivationRule.R_MULTIPLE,
            activation_r=Decimal("1"),
            partial_tp_required=False,
            retracement_rate=Decimal("0.008"),
        ),
        "E04": TrailingPolicy(
            policy_id="E04_CHANDELIER_STRUCTURE_V1",
            model=TrailingModel.CHANDELIER_STRUCTURE,
            activation_rule=TrailingActivationRule.R_MULTIPLE,
            activation_r=Decimal("1"),
            partial_tp_required=False,
            atr_multiplier=Decimal("2.5"),
        ),
        "E05": TrailingPolicy(
            policy_id="E05_EDGE_ADAPTIVE_TRAIL_V1",
            model=TrailingModel.EDGE_ADAPTIVE,
            activation_rule=TrailingActivationRule.R_MULTIPLE,
            activation_r=Decimal("0.75"),
            partial_tp_required=False,
            atr_multiplier=Decimal("2"),
            adverse_atr_multiplier=Decimal("1.2"),
        ),
    }
    if exit_id == "E01":
        return None
    try:
        return policies[exit_id]
    except KeyError as error:
        raise ValueError(f"알 수 없는 사전등록 exit ID입니다: {exit_id}") from error


def preregistered_trials() -> tuple[ResearchTrialSpec, ...]:
    trials: list[ResearchTrialSpec] = []
    for number, (alpha, exit_module) in enumerate(
        product(ALPHA_FAMILIES, EXIT_MODULES),
        start=1,
    ):
        allowed = alpha.execution_allowed
        trials.append(
            ResearchTrialSpec(
                trial_id=f"ALPHA_{alpha.family_id}_EXIT_{exit_module.exit_id}_V1",
                trial_number=number,
                alpha=alpha,
                exit=exit_module,
                lifecycle=TrialLifecycle.RESEARCH if allowed else TrialLifecycle.BLOCKED,
                screening_eligible=allowed,
            )
        )
    _validate_registry(trials)
    return tuple(trials)


def _validate_registry(trials: list[ResearchTrialSpec]) -> None:
    if len(ALPHA_FAMILIES) != 20 or len(EXIT_MODULES) != 5 or len(trials) != 100:
        raise ValueError("연구 레지스트리는 정확히 20 alpha × 5 exit = 100이어야 합니다.")
    family_ids = [spec.family_id for spec in ALPHA_FAMILIES]
    exit_ids = [spec.exit_id for spec in EXIT_MODULES]
    trial_ids = [spec.trial_id for spec in trials]
    if len(set(family_ids)) != 20 or len(set(exit_ids)) != 5 or len(set(trial_ids)) != 100:
        raise ValueError("연구 family·exit·trial ID는 모두 고유해야 합니다.")
    unsafe = any(
        spec.runtime_active or spec.live_shadow_enabled or not spec.paper_only for spec in trials
    )
    if unsafe:
        raise ValueError(
            "사전등록 후보는 PAPER 연구 전용이며 runtime에서 자동 활성화할 수 없습니다."
        )
    expected_evaluators = {f"F{number:02d}" for number in range(3, 21)}
    if set(ALPHA_EVALUATORS) != expected_evaluators:
        raise ValueError("F03~F20 evaluator는 누락·추가 없이 모두 구현돼야 합니다.")
    for family in ALPHA_FAMILIES:
        if family.family_id not in expected_evaluators:
            continue
        if dict(family.parameters) != ALPHA_PARAMETER_CONTRACTS[family.family_id]:
            raise ValueError(f"{family.family_id} 사전등록 parameter와 evaluator 계약이 다릅니다.")


def trial_manifest(
    *,
    code_version: str,
    generated_ts_utc: str,
    source_checksums: Mapping[str, str],
) -> dict[str, Any]:
    if not source_checksums or any(
        not path
        or len(checksum) != 64
        or any(character not in "0123456789abcdef" for character in checksum)
        for path, checksum in source_checksums.items()
    ):
        raise ValueError("100후보 manifest source checksum 계약이 잘못됐습니다.")
    trials = preregistered_trials()
    rows: list[dict[str, Any]] = []
    for trial in trials:
        row = asdict(trial)
        row["alpha"]["parameters"] = dict(trial.alpha.parameters)
        row["alpha"]["evaluator_id"] = trial.alpha.evaluator_id
        row["alpha"]["implementation_status"] = (
            "EXECUTABLE" if trial.alpha.evaluator_id is not None else "BLOCKED"
        )
        row["alpha"]["evaluation_interval_seconds"] = ALPHA_EVALUATION_INTERVAL_SECONDS.get(
            trial.alpha.family_id
        )
        row["exit"]["parameters"] = dict(trial.exit.parameters)
        trailing_policy = trailing_policy_for_exit(trial.exit.exit_id)
        row["paper_execution_binding"] = {
            "fixed_tp_sl": trailing_policy is None,
            "trailing_policy": (
                {
                    "policy_id": trailing_policy.policy_id,
                    "model": trailing_policy.model.value,
                    "activation_rule": trailing_policy.activation_rule.value,
                    "activation_r": str(trailing_policy.activation_r),
                    "partial_tp_required": trailing_policy.partial_tp_required,
                    "fixed_distance": str(trailing_policy.fixed_distance)
                    if trailing_policy.fixed_distance is not None
                    else None,
                    "retracement_rate": str(trailing_policy.retracement_rate)
                    if trailing_policy.retracement_rate is not None
                    else None,
                    "atr_multiplier": str(trailing_policy.atr_multiplier)
                    if trailing_policy.atr_multiplier is not None
                    else None,
                    "adverse_atr_multiplier": str(trailing_policy.adverse_atr_multiplier)
                    if trailing_policy.adverse_atr_multiplier is not None
                    else None,
                    "adverse_signal_count": trailing_policy.adverse_signal_count,
                    "adverse_persistence_ms": trailing_policy.adverse_persistence_ms,
                }
                if trailing_policy is not None
                else None
            ),
            "uses_paper_portfolio_engine": True,
            "real_orders_enabled": False,
        }
        rows.append(row)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "PREREGISTERED_NOT_EXECUTED",
        "generated_ts_utc": generated_ts_utc,
        "code_version": code_version,
        "source_checksums": dict(sorted(source_checksums.items())),
        "trial_count": len(rows),
        "alpha_family_count": len(ALPHA_FAMILIES),
        "exit_module_count": len(EXIT_MODULES),
        "screening_eligible_count": sum(trial.screening_eligible for trial in trials),
        "blocked_count": sum(not trial.screening_eligible for trial in trials),
        "runtime_active_count": 0,
        "live_shadow_count": 0,
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "runtime_ai_enabled": False,
        "funnel": {
            "offline_screening": 100,
            "event_replay_maximum": 25,
            "full_paper_replay_maximum": 10,
            "live_shadow_minimum": 3,
            "live_shadow_maximum": 6,
        },
        "cost_contract": {
            "model": "CONSERVATIVE_ASSUMED_V1",
            "base_entry_fee_bps": 6,
            "base_exit_fee_bps": 6,
            "base_additional_safety_bps": 1,
            "stress_entry_fee_bps": 12,
            "stress_exit_fee_bps": 12,
            "stress_additional_safety_bps": 1,
            "depth_slippage": "EXECUTABLE_BOOK_WALK",
            "decision_to_arrival_latency_ms": {"BASE": 250, "STRESS": 500},
            "stop_processing_latency_ms": {"BASE": 250, "STRESS": 500},
            "partial_fill": True,
        },
        "risk_contract": {
            "starting_equity_per_trial_profile_usdt": "1000",
            "profiles_per_trial": ["BASE", "STRESS"],
            "risk_per_trade_fraction": "0.005",
            "maximum_positions_per_trial_profile": 3,
            "initial_stop_model": "MAX_ATR_SPREAD_TICK_FLOOR_V1",
            "default_initial_stop_atr": "1.0",
            "F05_initial_stop_atr": "2.0",
            "spread_floor_multiplier": "1.5",
            "tick_floor_multiplier": "2",
            "averaging_down": False,
            "martingale": False,
            "pyramiding": False,
            "real_leverage_setting": False,
        },
        "data_split_contract": {
            "dataset_manifest": "evidence/STRATEGY_100_DATASET_MANIFEST.json",
            "required_historical_status": "FROZEN_HISTORICAL_FORWARD_PENDING",
            "random_shuffle": False,
            "required_splits": ["TRAIN", "VALIDATION", "FINAL_OOS", "FORWARD_LIVE_PUBLIC"],
            "stage1_selection_splits": ["TRAIN", "VALIDATION"],
            "stage1_final_oos_access": "FORBIDDEN",
            "final_oos_open_after_full_paper_finalists_frozen": True,
            "final_oos_may_be_opened_once": True,
            "final_oos_may_not_be_used_for_retuning": True,
            "walk_forward": ["ANCHORED", "ROLLING"],
            "purge_and_embargo": True,
            "purge_embargo_ms_by_horizon": dict(HORIZON_MAXIMUM_HOLD_MS),
            "maximum_holding_ms_by_horizon": dict(HORIZON_MAXIMUM_HOLD_MS),
            "horizon_without_four_usable_validation_folds_must_fail": True,
            "holdouts": ["SYMBOL", "VENUE", "REGIME", "VOLATILITY", "COST"],
            "historical_screening_blocked_until_dataset_frozen": True,
            "forward_live_public_must_remain_prospective": True,
        },
        "promotion_gates": {
            "base_expectancy_positive": True,
            "stress_expectancy_positive": True,
            "profit_factor_greater_than": 1,
            "bootstrap_expectancy_95pct_lower_bound_positive": True,
            "deflated_sharpe_ratio_minimum": 0.95,
            "probability_backtest_overfitting_maximum": 0.20,
            "single_symbol_pnl_contribution_maximum": 0.25,
            "single_trade_pnl_contribution_maximum": 0.10,
            "replay_checksum_required": True,
            "recovery_required": True,
            "live_paper_minimum_sample_required": True,
        },
        "trials": rows,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return manifest
