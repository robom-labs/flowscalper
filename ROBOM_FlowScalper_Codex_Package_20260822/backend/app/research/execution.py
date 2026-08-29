# 100후보 신호를 기존 CandidatePlanner와 PAPER 체결 경로의 불변 계획으로 변환한다.

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import StrEnum

from backend.app.candidates import CandidatePlan, CandidatePlanner, TakeProfitTarget
from backend.app.domain.market import Instrument
from backend.app.domain.models import Side
from backend.app.execution.models import BookSnapshot
from backend.app.features import FeatureSnapshot
from backend.app.regime import Regime
from backend.app.research.alpha_evaluators import AlphaFeatureSnapshot, AlphaSignal
from backend.app.research.candidate_registry import (
    HORIZON_MAXIMUM_HOLD_MS,
    ResearchTrialSpec,
    trailing_policy_for_exit,
)
from backend.app.risk import STRATEGY_LEAGUE_RISK_LIMITS, RiskManager, RiskState
from backend.app.strategies.base import CandidateDecision, CandidateStatus, PlanInputs, costed_plan
from backend.app.strategies.registry import ExitStyle

BASE_EXPECTED_TOTAL_COST_BPS = Decimal("13")
STRESS_EXPECTED_TOTAL_COST_BPS = Decimal("25")
E06_TP1_R = Decimal("0.8")
E06_TP1_FRACTION = Decimal("0.7")
E06_TP2_R = Decimal("3")
E06_RUNNER_FRACTION = Decimal("0.3")
E06_WEIGHTED_GROSS_REWARD_R = (
    E06_TP1_R * E06_TP1_FRACTION + E06_TP2_R * E06_RUNNER_FRACTION
)
E06_MINIMUM_WEIGHTED_NET_REWARD_R = Decimal("1.2")
MAXIMUM_DECISION_DATA_AGE_MS = 1_000
DEFAULT_INITIAL_STOP_ATR = Decimal("1")
F05_INITIAL_STOP_ATR = Decimal("2")


class InstrumentMetadataEvidence(StrEnum):
    POINT_IN_TIME_PUBLIC = "POINT_IN_TIME_PUBLIC"
    CURRENT_PUBLIC_CONSERVATIVE = "CURRENT_PUBLIC_CONSERVATIVE"


@dataclass(frozen=True, slots=True)
class ResearchInstrumentMetadata:
    instrument: Instrument
    minimum_notional: Decimal
    snapshot_ts_ms: int
    source_checksum: str
    evidence: InstrumentMetadataEvidence

    def __post_init__(self) -> None:
        if (
            self.minimum_notional <= 0
            or self.snapshot_ts_ms < 0
            or len(self.source_checksum) != 64
            or any(character not in "0123456789abcdef" for character in self.source_checksum)
            or self.instrument.tick_size <= 0
            or self.instrument.quantity_step <= 0
            or self.instrument.minimum_quantity <= 0
        ):
            raise ValueError("연구 instrument 공개필터 증거가 불완전합니다.")

    @property
    def promotion_eligible(self) -> bool:
        return self.evidence is InstrumentMetadataEvidence.POINT_IN_TIME_PUBLIC


@dataclass(frozen=True, slots=True)
class ResearchPlanBuildResult:
    plan: CandidatePlan | None
    rejection_codes: tuple[str, ...]
    evidence_codes: tuple[str, ...]
    instrument_metadata_promotion_eligible: bool


class ResearchCandidatePlanBuilder:
    """사전등록 risk·exit를 런타임과 같은 CandidatePlanner 계약으로 고정한다."""

    def __init__(self, planner: CandidatePlanner | None = None) -> None:
        self.planner = planner or CandidatePlanner(
            risk_manager=RiskManager(STRATEGY_LEAGUE_RISK_LIMITS)
        )

    def build(
        self,
        *,
        trial: ResearchTrialSpec,
        signal: AlphaSignal,
        alpha_snapshot: AlphaFeatureSnapshot,
        market_snapshot: FeatureSnapshot,
        book: BookSnapshot,
        metadata: ResearchInstrumentMetadata,
        regime: Regime,
        run_id: str,
        signal_event_id: str,
        risk_state: RiskState,
    ) -> ResearchPlanBuildResult:
        if not trial.screening_eligible or trial.alpha.family_id != signal.family_id:
            return self._rejected(metadata, "TRIAL_SIGNAL_FAMILY_MISMATCH_OR_BLOCKED")
        if signal.symbol != alpha_snapshot.symbol or signal.symbol != metadata.instrument.symbol:
            return self._rejected(metadata, "TRIAL_SIGNAL_INSTRUMENT_MISMATCH")
        if signal.signal_ts_ms != alpha_snapshot.decision_ts_ms:
            return self._rejected(metadata, "SIGNAL_DECISION_TIME_MISMATCH")
        if (
            book.ts_ms > signal.signal_ts_ms
            or signal.signal_ts_ms - book.ts_ms > MAXIMUM_DECISION_DATA_AGE_MS
        ):
            return self._rejected(metadata, "DECISION_BOOK_NOT_CAUSAL_OR_FRESH")
        if (
            market_snapshot.symbol != signal.symbol
            or market_snapshot.ts_ms > signal.signal_ts_ms
            or signal.signal_ts_ms - market_snapshot.ts_ms > MAXIMUM_DECISION_DATA_AGE_MS
            or not market_snapshot.data_healthy
        ):
            return self._rejected(metadata, "MARKET_FEATURE_NOT_FRESH_AND_HEALTHY")
        if (
            metadata.instrument.onboard_ts_ms is not None
            and metadata.instrument.onboard_ts_ms > signal.signal_ts_ms
        ):
            return self._rejected(metadata, "INSTRUMENT_NOT_ONBOARDED_AT_SIGNAL")
        if metadata.instrument.status != "TRADING":
            return self._rejected(metadata, "INSTRUMENT_NOT_TRADING_IN_METADATA")
        try:
            book.validate()
        except ValueError:
            return self._rejected(metadata, "BOOK_NOT_EXECUTABLE")
        if book.symbol != signal.symbol or book.venue is not metadata.instrument.venue:
            return self._rejected(metadata, "BOOK_INSTRUMENT_MISMATCH")

        side = signal.side
        entry = book.asks[0][0] if side is Side.LONG else book.bids[0][0]
        spread = book.asks[0][0] - book.bids[0][0]
        atr = Decimal(str(alpha_snapshot.atr))
        atr_multiple = (
            F05_INITIAL_STOP_ATR if trial.alpha.family_id == "F05" else DEFAULT_INITIAL_STOP_ATR
        )
        stop_distance = max(
            atr * atr_multiple,
            spread * Decimal("1.5"),
            metadata.instrument.tick_size * Decimal(2),
        )
        stop = self._price(
            entry - stop_distance if side is Side.LONG else entry + stop_distance,
            metadata.instrument.tick_size,
            favorable=False,
            side=side,
        )
        targets = self._targets(
            trial,
            side=side,
            entry=entry,
            stop=stop,
            tick_size=metadata.instrument.tick_size,
        )
        if trial.exit.exit_id == "E06":
            e06_rejections = self._e06_cost_rejections(entry=entry, stop=stop)
            if e06_rejections:
                return self._rejected(metadata, *e06_rejections)
        final_target = targets[-1].price
        costed, rejections = costed_plan(
            side,
            PlanInputs(
                entry=entry,
                structural_stop=stop,
                target=final_target,
                expected_total_cost_bps=BASE_EXPECTED_TOTAL_COST_BPS,
            ),
        )
        if costed is None:
            return ResearchPlanBuildResult(
                None,
                rejections or ("RESEARCH_COST_PLAN_REJECTED",),
                (),
                metadata.promotion_eligible,
            )
        decision = CandidateDecision(
            strategy_id=trial.trial_id,
            side=side,
            status=CandidateStatus.QUALIFIED,
            reason_codes=(*signal.reason_codes, "PREREGISTERED_100_TRIAL"),
            rejection_codes=(),
            planned_entry=entry,
            initial_stop=stop,
            take_profit=final_target,
            expected_cost_bps=BASE_EXPECTED_TOTAL_COST_BPS,
            net_reward_risk=costed.net_reward_risk,
        )
        trailing = trailing_policy_for_exit(trial.exit.exit_id)
        requires_atr = trial.exit.exit_id in {"E02", "E04", "E05", "E06"}
        requires_structure = trial.exit.exit_id == "E04"
        structure_stop = (
            Decimal(
                str(
                    alpha_snapshot.completed_structure_long_stop
                    if side is Side.LONG
                    else alpha_snapshot.completed_structure_short_stop
                )
            )
            if requires_structure
            else None
        )
        result = self.planner.build(
            signal_event_id=signal_event_id,
            run_id=run_id,
            venue=book.venue,
            decision=decision,
            snapshot=market_snapshot,
            regime=regime,
            book=book,
            instrument=metadata.instrument,
            signal_time_ms=signal.signal_ts_ms,
            risk_state=risk_state,
            main_eligible=False,
            shadow_eligible=True,
            exit_style=ExitStyle.TREND_40_60,
            trend_take_profit_1_r=Decimal("1.5"),
            trend_take_profit_2_r=Decimal("3"),
            maximum_holding_ms=HORIZON_MAXIMUM_HOLD_MS[trial.alpha.horizon],
            strategy_version="1",
            trailing_policy=trailing,
            trailing_atr=atr if requires_atr else None,
            trailing_structure_stop=structure_stop,
            trailing_reference_ts_ms=(
                alpha_snapshot.completed_candle_close_ts_ms
                if requires_atr or requires_structure
                else None
            ),
            trailing_reference_interval_seconds=(
                alpha_snapshot.interval_seconds if requires_atr or requires_structure else None
            ),
            take_profit_targets_override=targets,
            candidate_id_override=self._candidate_id(
                run_id=run_id,
                trial_id=trial.trial_id,
                signal_event_id=signal_event_id,
                signal_ts_ms=signal.signal_ts_ms,
                symbol=signal.symbol,
                side=side,
            ),
        )
        if result.plan is None:
            return ResearchPlanBuildResult(
                None,
                result.rejection_codes,
                (),
                metadata.promotion_eligible,
            )
        if result.plan.position_size * result.plan.worst_allowed_entry < metadata.minimum_notional:
            return self._rejected(metadata, "MINIMUM_NOTIONAL_NOT_MET")
        if metadata.promotion_eligible:
            return ResearchPlanBuildResult(result.plan, (), (), True)
        return ResearchPlanBuildResult(
            result.plan,
            (),
            ("INSTRUMENT_METADATA_CURRENT_NOT_POINT_IN_TIME",),
            False,
        )

    @staticmethod
    def _candidate_id(
        *,
        run_id: str,
        trial_id: str,
        signal_event_id: str,
        signal_ts_ms: int,
        symbol: str,
        side: Side,
    ) -> str:
        material = "\x1f".join(
            (run_id, trial_id, signal_event_id, str(signal_ts_ms), symbol, side.value)
        )
        return f"research-{hashlib.sha256(material.encode()).hexdigest()[:24]}"

    @staticmethod
    def _targets(
        trial: ResearchTrialSpec,
        *,
        side: Side,
        entry: Decimal,
        stop: Decimal,
        tick_size: Decimal,
    ) -> tuple[TakeProfitTarget, ...]:
        risk = abs(entry - stop)
        direction = Decimal(1) if side is Side.LONG else Decimal(-1)
        definitions: tuple[tuple[str, Decimal, Decimal], ...]
        if trial.exit.exit_id == "E01":
            definitions = (("TP1", Decimal("1.5"), Decimal(1)),)
        elif trial.exit.exit_id == "E02":
            definitions = (
                ("TP1", Decimal("1.5"), Decimal("0.4")),
                ("TP2", Decimal("3"), Decimal("0.6")),
            )
        elif trial.exit.exit_id == "E06":
            definitions = (
                ("TP1", E06_TP1_R, E06_TP1_FRACTION),
                ("TP2", E06_TP2_R, E06_RUNNER_FRACTION),
            )
        else:
            definitions = (("TP1", Decimal("3"), Decimal(1)),)
        return tuple(
            TakeProfitTarget(
                label,
                ResearchCandidatePlanBuilder._price(
                    entry + direction * risk * multiple,
                    tick_size,
                    favorable=True,
                    side=side,
                ),
                fraction,
            )
            for label, multiple, fraction in definitions
        )

    @staticmethod
    def _e06_cost_rejections(*, entry: Decimal, stop: Decimal) -> tuple[str, ...]:
        risk = abs(entry - stop)
        if risk <= 0:
            return ("E06_INITIAL_RISK_NOT_POSITIVE",)
        rejections: list[str] = []
        for profile, cost_bps in (
            ("BASE", BASE_EXPECTED_TOTAL_COST_BPS),
            ("STRESS", STRESS_EXPECTED_TOTAL_COST_BPS),
        ):
            roundtrip_cost_r = entry * cost_bps / Decimal(10_000) / risk
            if E06_TP1_R - roundtrip_cost_r <= 0:
                rejections.append(f"E06_{profile}_TP1_NOT_NET_POSITIVE")
            if E06_WEIGHTED_GROSS_REWARD_R - roundtrip_cost_r < (
                E06_MINIMUM_WEIGHTED_NET_REWARD_R
            ):
                rejections.append(f"E06_{profile}_WEIGHTED_NET_REWARD_BELOW_1_2R")
        return tuple(rejections)

    @staticmethod
    def _price(
        value: Decimal,
        tick_size: Decimal,
        *,
        favorable: bool,
        side: Side,
    ) -> Decimal:
        if value <= 0 or tick_size <= 0:
            raise ValueError("연구 가격과 tick size는 양수여야 합니다.")
        round_up = (side is Side.LONG) == favorable
        rounding = ROUND_CEILING if round_up else ROUND_FLOOR
        ticks = (value / tick_size).to_integral_value(rounding=rounding)
        return ticks * tick_size

    @staticmethod
    def _rejected(
        metadata: ResearchInstrumentMetadata,
        *codes: str,
    ) -> ResearchPlanBuildResult:
        return ResearchPlanBuildResult(None, tuple(codes), (), metadata.promotion_eligible)
