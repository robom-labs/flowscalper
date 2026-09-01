# V8·V9 연구 필터·위험·근거·히스테리시스 공통 계약을 정의한다.
"""실행 전 연구 gate를 불변 값과 순수 상태전이로 평가한다."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum

from backend.app.domain.models import Side

_DAY_MS = 24 * 60 * 60 * 1_000


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _validate_identifier(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name}는 공백 없는 식별자여야 합니다.")


def _validate_reason_codes(reason_codes: tuple[str, ...]) -> None:
    if any(not code or code != code.strip() for code in reason_codes):
        raise ValueError("사유 코드는 공백 없는 식별자여야 합니다.")
    if len(reason_codes) != len(set(reason_codes)):
        raise ValueError("사유 코드를 중복할 수 없습니다.")


def _validate_timestamp(field_name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name}는 0 이상의 정수여야 합니다.")


def _validate_unit_decimal(field_name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name}는 유한한 Decimal이어야 합니다.")
    if not Decimal(0) <= value <= Decimal(1):
        raise ValueError(f"{field_name}는 0 이상 1 이하여야 합니다.")


class FilterDecision(StrEnum):
    """연구 필터가 기존 후보에 내릴 수 있는 네 가지 판정이다."""

    PASS = "PASS"
    SKIP = "SKIP"
    WAIT = "WAIT"
    QUALITY_DOWNGRADE = "QUALITY_DOWNGRADE"


@dataclass(frozen=True, slots=True)
class FilterResult:
    """하나의 필터가 낸 시점 고정 판정이다."""

    filter_id: str
    decision: FilterDecision
    reason_codes: tuple[str, ...]
    observed_ts_ms: int
    valid_until_ts_ms: int
    quality_multiplier: Decimal = Decimal(1)

    def __post_init__(self) -> None:
        _validate_identifier("filter_id", self.filter_id)
        if not isinstance(self.decision, FilterDecision):
            raise ValueError("decision은 FilterDecision이어야 합니다.")
        _validate_reason_codes(self.reason_codes)
        _validate_timestamp("observed_ts_ms", self.observed_ts_ms)
        _validate_timestamp("valid_until_ts_ms", self.valid_until_ts_ms)
        if self.valid_until_ts_ms < self.observed_ts_ms:
            raise ValueError("필터 유효시각은 관측시각보다 빠를 수 없습니다.")
        _validate_unit_decimal("quality_multiplier", self.quality_multiplier)
        if self.decision is FilterDecision.QUALITY_DOWNGRADE:
            if not Decimal(0) < self.quality_multiplier < Decimal(1):
                raise ValueError(
                    "QUALITY_DOWNGRADE는 0보다 크고 1보다 작은 품질 배수가 필요합니다."
                )
        elif self.quality_multiplier != Decimal(1):
            raise ValueError("품질 배수는 QUALITY_DOWNGRADE 판정만 조정할 수 있습니다.")
        if self.decision is not FilterDecision.PASS and not self.reason_codes:
            raise ValueError("통과 외의 필터 판정에는 사유 코드가 필요합니다.")


@dataclass(frozen=True, slots=True)
class FilterAssessment:
    """동일 후보에 대한 필터 판정을 보수적으로 합성한 결과다."""

    decision: FilterDecision
    results: tuple[FilterResult, ...]
    quality_multiplier: Decimal
    reason_codes: tuple[str, ...]

    @property
    def execution_allowed(self) -> bool:
        return self.decision in {
            FilterDecision.PASS,
            FilterDecision.QUALITY_DOWNGRADE,
        }

    @property
    def retryable(self) -> bool:
        return self.decision is FilterDecision.WAIT


_FILTER_PRECEDENCE = {
    FilterDecision.PASS: 0,
    FilterDecision.QUALITY_DOWNGRADE: 1,
    FilterDecision.WAIT: 2,
    FilterDecision.SKIP: 3,
}


def combine_filter_results(results: Iterable[FilterResult]) -> FilterAssessment:
    """SKIP > WAIT > QUALITY_DOWNGRADE > PASS 순서로 판정을 합성한다."""

    rows = tuple(results)
    filter_ids = [row.filter_id for row in rows]
    if len(filter_ids) != len(set(filter_ids)):
        raise ValueError("동일 필터 판정을 여러 번 합성할 수 없습니다.")
    decision = max(
        (row.decision for row in rows),
        key=_FILTER_PRECEDENCE.__getitem__,
        default=FilterDecision.PASS,
    )
    quality_multiplier = min(
        (
            row.quality_multiplier
            for row in rows
            if row.decision is FilterDecision.QUALITY_DOWNGRADE
        ),
        default=Decimal(1),
    )
    reason_codes = tuple(
        dict.fromkeys(code for row in rows for code in row.reason_codes)
    )
    return FilterAssessment(
        decision=decision,
        results=rows,
        quality_multiplier=quality_multiplier,
        reason_codes=reason_codes,
    )


@dataclass(frozen=True, slots=True)
class RiskOverlayComponent:
    """기본 위험을 줄이기만 하는 하나의 위험 계수다."""

    overlay_id: str
    multiplier: Decimal
    reason_codes: tuple[str, ...] = ()
    observed_ts_ms: int = 0

    def __post_init__(self) -> None:
        _validate_identifier("overlay_id", self.overlay_id)
        _validate_unit_decimal("risk multiplier", self.multiplier)
        _validate_reason_codes(self.reason_codes)
        _validate_timestamp("observed_ts_ms", self.observed_ts_ms)
        if self.multiplier < Decimal(1) and not self.reason_codes:
            raise ValueError("위험을 줄이는 overlay에는 사유 코드가 필요합니다.")


@dataclass(frozen=True, slots=True)
class RiskOverlay:
    """독립 위험 계수 중 가장 보수적인 값을 선택한다."""

    components: tuple[RiskOverlayComponent, ...] = ()

    def __post_init__(self) -> None:
        overlay_ids = [component.overlay_id for component in self.components]
        if len(overlay_ids) != len(set(overlay_ids)):
            raise ValueError("동일 위험 overlay를 여러 번 적용할 수 없습니다.")

    @property
    def multiplier(self) -> Decimal:
        return min(
            (component.multiplier for component in self.components),
            default=Decimal(1),
        )

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                code for component in self.components for code in component.reason_codes
            )
        )


@dataclass(frozen=True, slots=True)
class HypothesisKey:
    """다중검정의 한 독립 연구가설을 정규화한 식별자다."""

    strategy_family: str
    candidate_id: str
    strategy_version: str
    parameter_id: str
    exit_id: str
    execution_policy: str
    filter_combination: tuple[str, ...]
    dataset_id: str
    parameter_hash: str
    cost_profile: str
    dataset_hash: str
    feature_version: str
    label_version: str
    engine_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "strategy_family",
            "candidate_id",
            "strategy_version",
            "parameter_id",
            "exit_id",
            "execution_policy",
            "dataset_id",
            "cost_profile",
            "feature_version",
            "label_version",
            "engine_version",
        ):
            _validate_identifier(field_name, getattr(self, field_name))
        for field_name in (
            "parameter_hash",
            "dataset_hash",
        ):
            if not _is_sha256(getattr(self, field_name)):
                raise ValueError(f"{field_name}는 소문자 SHA-256이어야 합니다.")
        filters = tuple(self.filter_combination)
        for filter_id in filters:
            _validate_identifier("filter_combination", filter_id)
        if len(filters) != len(set(filters)):
            raise ValueError("가설의 필터 조합에 중복이 있습니다.")
        object.__setattr__(self, "filter_combination", tuple(sorted(filters)))

    def canonical_payload(self) -> dict[str, object]:
        return {
            "strategy_family": self.strategy_family,
            "candidate_id": self.candidate_id,
            "strategy_version": self.strategy_version,
            "parameter_id": self.parameter_id,
            "exit_id": self.exit_id,
            "execution_policy": self.execution_policy,
            "filter_combination": list(self.filter_combination),
            "dataset_id": self.dataset_id,
            "parameter_hash": self.parameter_hash,
            "cost_profile": self.cost_profile,
            "dataset_hash": self.dataset_hash,
            "feature_version": self.feature_version,
            "label_version": self.label_version,
            "engine_version": self.engine_version,
        }

    def fingerprint(self) -> str:
        return _fingerprint(self.canonical_payload())

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> HypothesisKey:
        """영속 payload를 canonical key로 복원하며 누락·타입 drift를 거부한다."""

        expected = {
            "strategy_family",
            "candidate_id",
            "strategy_version",
            "parameter_id",
            "exit_id",
            "execution_policy",
            "filter_combination",
            "dataset_id",
            "parameter_hash",
            "cost_profile",
            "dataset_hash",
            "feature_version",
            "label_version",
            "engine_version",
        }
        if set(payload) != expected:
            raise ValueError("영속 가설 key 필드가 canonical 계약과 다릅니다.")
        filters = payload["filter_combination"]
        if not isinstance(filters, list) or any(not isinstance(value, str) for value in filters):
            raise ValueError("영속 가설 filter 조합은 문자열 배열이어야 합니다.")
        string_fields = expected - {"filter_combination"}
        if any(not isinstance(payload[field], str) for field in string_fields):
            raise ValueError("영속 가설 key 식별자는 문자열이어야 합니다.")
        return cls(
            strategy_family=str(payload["strategy_family"]),
            candidate_id=str(payload["candidate_id"]),
            strategy_version=str(payload["strategy_version"]),
            parameter_id=str(payload["parameter_id"]),
            exit_id=str(payload["exit_id"]),
            execution_policy=str(payload["execution_policy"]),
            filter_combination=tuple(filters),
            dataset_id=str(payload["dataset_id"]),
            parameter_hash=str(payload["parameter_hash"]),
            cost_profile=str(payload["cost_profile"]),
            dataset_hash=str(payload["dataset_hash"]),
            feature_version=str(payload["feature_version"]),
            label_version=str(payload["label_version"]),
            engine_version=str(payload["engine_version"]),
        )


@dataclass(frozen=True, slots=True)
class HypothesisRegistration:
    hypothesis_id: str
    key: HypothesisKey

    def __post_init__(self) -> None:
        _validate_identifier("hypothesis_id", self.hypothesis_id)
        if not isinstance(self.key, HypothesisKey):
            raise ValueError("key는 HypothesisKey여야 합니다.")

    @property
    def key_fingerprint(self) -> str:
        return self.key.fingerprint()

    def canonical_payload(self) -> dict[str, object]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "key": self.key.canonical_payload(),
            "key_fingerprint": self.key_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class HypothesisRegistry:
    """가설 ID drift와 같은 가설의 별칭 등록을 막는 불변 registry다."""

    registrations: tuple[HypothesisRegistration, ...] = ()

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.registrations, key=lambda row: row.hypothesis_id))
        ids: dict[str, str] = {}
        fingerprints: dict[str, str] = {}
        for registration in ordered:
            fingerprint = registration.key_fingerprint
            previous_fingerprint = ids.get(registration.hypothesis_id)
            if previous_fingerprint is not None and previous_fingerprint != fingerprint:
                raise ValueError("동일 가설 ID의 canonical fingerprint가 바뀌었습니다.")
            previous_id = fingerprints.get(fingerprint)
            if previous_id is not None and previous_id != registration.hypothesis_id:
                raise ValueError("동일 canonical 가설을 다른 ID로 등록할 수 없습니다.")
            if previous_fingerprint is not None:
                raise ValueError("가설 registry에 중복 등록이 있습니다.")
            ids[registration.hypothesis_id] = fingerprint
            fingerprints[fingerprint] = registration.hypothesis_id
        object.__setattr__(self, "registrations", ordered)

    def register(self, hypothesis_id: str, key: HypothesisKey) -> HypothesisRegistry:
        """동일 ID·키는 멱등적으로 유지하고 충돌은 거부한다."""

        registration = HypothesisRegistration(hypothesis_id, key)
        fingerprint = registration.key_fingerprint
        for existing in self.registrations:
            if existing.hypothesis_id == hypothesis_id:
                if existing.key_fingerprint != fingerprint:
                    raise ValueError("동일 가설 ID의 canonical fingerprint가 바뀌었습니다.")
                return self
            if existing.key_fingerprint == fingerprint:
                raise ValueError("동일 canonical 가설을 다른 ID로 등록할 수 없습니다.")
        return HypothesisRegistry((*self.registrations, registration))

    def fingerprint(self) -> str:
        return _fingerprint(self.canonical_payload())

    def canonical_payload(self) -> list[dict[str, object]]:
        return [registration.canonical_payload() for registration in self.registrations]

    @classmethod
    def from_payload(cls, payload: object) -> HypothesisRegistry:
        """영속 registry를 복원하면서 fingerprint 위조와 alias 충돌을 거부한다."""

        if not isinstance(payload, list):
            raise ValueError("영속 가설 registry는 배열이어야 합니다.")
        registrations: list[HypothesisRegistration] = []
        for row in payload:
            if not isinstance(row, dict) or set(row) != {
                "hypothesis_id",
                "key",
                "key_fingerprint",
            }:
                raise ValueError("영속 가설 registry 행이 canonical 계약과 다릅니다.")
            if not isinstance(row["hypothesis_id"], str) or not isinstance(
                row["key_fingerprint"], str
            ):
                raise ValueError("영속 가설 registry 식별자는 문자열이어야 합니다.")
            if not isinstance(row["key"], dict):
                raise ValueError("영속 가설 registry key는 객체여야 합니다.")
            key = HypothesisKey.from_payload(row["key"])
            if key.fingerprint() != row["key_fingerprint"]:
                raise ValueError("영속 가설 key fingerprint가 canonical 내용과 다릅니다.")
            registrations.append(HypothesisRegistration(row["hypothesis_id"], key))
        return cls(tuple(registrations))

    def registration(self, hypothesis_id: str) -> HypothesisRegistration:
        _validate_identifier("hypothesis_id", hypothesis_id)
        for row in self.registrations:
            if row.hypothesis_id == hypothesis_id:
                return row
        raise ValueError("가설 ID가 registry에 등록되지 않았습니다.")


def _is_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in "0123456789abcdef" for character in value)
    )


@dataclass(frozen=True, slots=True)
class EvidenceEpoch:
    """시장구조·비용·구현 계약이 고정된 근거 평가 구간이다."""

    epoch_id: str
    opened_ts_ms: int
    closed_ts_ms: int | None
    strategy_version: str
    feature_version: str
    label_version: str
    engine_version: str
    cost_model_version: str
    cost_profile: str
    parameter_hash: str
    dataset_hash: str
    fee_model_version: str
    matching_model_version: str
    symbol_contract_version: str
    data_adapter_version: str
    hypothesis_registry_hash: str
    hypothesis_key_fingerprint: str

    def __post_init__(self) -> None:
        for field_name in (
            "epoch_id",
            "strategy_version",
            "feature_version",
            "label_version",
            "engine_version",
            "cost_model_version",
            "cost_profile",
            "fee_model_version",
            "matching_model_version",
            "symbol_contract_version",
            "data_adapter_version",
        ):
            _validate_identifier(field_name, getattr(self, field_name))
        _validate_timestamp("opened_ts_ms", self.opened_ts_ms)
        if self.closed_ts_ms is not None:
            _validate_timestamp("closed_ts_ms", self.closed_ts_ms)
            if self.closed_ts_ms < self.opened_ts_ms:
                raise ValueError("근거 epoch 종료시각은 시작시각보다 빠를 수 없습니다.")
        if not _is_sha256(self.hypothesis_registry_hash):
            raise ValueError("hypothesis_registry_hash는 소문자 SHA-256이어야 합니다.")
        for field_name in (
            "parameter_hash",
            "dataset_hash",
            "hypothesis_key_fingerprint",
        ):
            if not _is_sha256(getattr(self, field_name)):
                raise ValueError(f"{field_name}는 소문자 SHA-256이어야 합니다.")

    def fingerprint(self) -> str:
        return _fingerprint(self.identity_payload())

    def identity_payload(self) -> dict[str, object]:
        """종료시각 변경과 무관한 불변 epoch identity를 반환한다."""

        payload = asdict(self)
        payload.pop("closed_ts_ms")
        return payload

    def canonical_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["epoch_fingerprint"] = self.fingerprint()
        return payload

    def validate_binding(
        self,
        registry: HypothesisRegistry,
        hypothesis_id: str,
    ) -> HypothesisRegistration:
        """epoch와 registry key의 모든 evidence 분리축이 일치하는지 검증한다."""

        if registry.fingerprint() != self.hypothesis_registry_hash:
            raise ValueError("epoch의 hypothesis registry hash가 현재 registry와 다릅니다.")
        registration = registry.registration(hypothesis_id)
        key = registration.key
        expected = {
            "hypothesis_key_fingerprint": registration.key_fingerprint,
            "strategy_version": key.strategy_version,
            "feature_version": key.feature_version,
            "label_version": key.label_version,
            "engine_version": key.engine_version,
            "cost_profile": key.cost_profile,
            "parameter_hash": key.parameter_hash,
            "dataset_hash": key.dataset_hash,
        }
        mismatches = [name for name, value in expected.items() if getattr(self, name) != value]
        if mismatches:
            raise ValueError(
                "epoch와 hypothesis key의 evidence 계약이 섞였습니다: "
                + ", ".join(sorted(mismatches))
            )
        return registration

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> EvidenceEpoch:
        """영속 epoch payload를 복원하며 fingerprint 충돌을 거부한다."""

        expected = {field.name for field in cls.__dataclass_fields__.values()}
        if set(payload) != expected | {"epoch_fingerprint"}:
            raise ValueError("영속 evidence epoch 필드가 canonical 계약과 다릅니다.")
        fingerprint = payload["epoch_fingerprint"]
        if not isinstance(fingerprint, str):
            raise ValueError("영속 evidence epoch fingerprint는 문자열이어야 합니다.")
        values = {name: payload[name] for name in expected}
        try:
            epoch = cls(**values)  # type: ignore[arg-type]
        except TypeError as error:
            raise ValueError("영속 evidence epoch 필드 타입이 올바르지 않습니다.") from error
        if epoch.fingerprint() != fingerprint:
            raise ValueError("영속 evidence epoch fingerprint가 canonical 내용과 다릅니다.")
        return epoch


class EvidenceHorizon(StrEnum):
    MICRO = "MICRO"
    FAST = "FAST"
    SWING = "SWING"
    MARKET_NEUTRAL = "MARKET_NEUTRAL"


class EvidenceFreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    EPOCH_MISMATCH = "EPOCH_MISMATCH"


@dataclass(frozen=True, slots=True)
class EvidenceSample:
    """BASE·STRESS를 opportunity_id로 중복 제거할 순방향 근거 표본이다."""

    opportunity_id: str
    observed_ts_ms: int
    evidence_epoch_id: str
    strategy_version: str
    profile: str = "BASE"

    def __post_init__(self) -> None:
        for field_name in (
            "opportunity_id",
            "evidence_epoch_id",
            "strategy_version",
            "profile",
        ):
            _validate_identifier(field_name, getattr(self, field_name))
        _validate_timestamp("observed_ts_ms", self.observed_ts_ms)


@dataclass(frozen=True, slots=True)
class EvidenceFreshnessAssessment:
    status: EvidenceFreshnessStatus
    horizon: EvidenceHorizon
    window_days: int
    minimum_unique_samples: int
    observed_unique_samples: int
    cutoff_ts_ms: int
    evidence_epoch_id: str
    strategy_version: str
    reason_codes: tuple[str, ...]

    @property
    def promotion_allowed(self) -> bool:
        return self.status is EvidenceFreshnessStatus.FRESH


_FRESHNESS_RULES: Mapping[EvidenceHorizon, tuple[int, int]] = {
    EvidenceHorizon.MICRO: (60, 200),
    EvidenceHorizon.FAST: (90, 50),
    EvidenceHorizon.SWING: (180, 30),
    EvidenceHorizon.MARKET_NEUTRAL: (180, 20),
}
_HORIZON_ALIASES = {
    "MICRO": EvidenceHorizon.MICRO,
    "MICRO_SCALP": EvidenceHorizon.MICRO,
    "FAST": EvidenceHorizon.FAST,
    "SWING": EvidenceHorizon.SWING,
    "INTRADAY_SWING": EvidenceHorizon.SWING,
    "MARKET_NEUTRAL": EvidenceHorizon.MARKET_NEUTRAL,
}


def _resolve_horizon(value: EvidenceHorizon | str) -> EvidenceHorizon:
    if isinstance(value, EvidenceHorizon):
        return value
    if not isinstance(value, str):
        raise ValueError("근거 horizon은 문자열 또는 EvidenceHorizon이어야 합니다.")
    try:
        return _HORIZON_ALIASES[value.strip().upper()]
    except KeyError as error:
        raise ValueError(f"지원하지 않는 근거 horizon입니다: {value}") from error


def assess_evidence_freshness(
    samples: Iterable[EvidenceSample],
    *,
    horizon: EvidenceHorizon | str,
    as_of_ts_ms: int,
    epoch: EvidenceEpoch,
) -> EvidenceFreshnessAssessment:
    """현재 version·epoch으로 한정된 고유 opportunity 근거를 판정한다."""

    _validate_timestamp("as_of_ts_ms", as_of_ts_ms)
    if as_of_ts_ms < epoch.opened_ts_ms:
        raise ValueError("근거 평가시각은 epoch 시작시각보다 빠를 수 없습니다.")
    resolved_horizon = _resolve_horizon(horizon)
    window_days, minimum_samples = _FRESHNESS_RULES[resolved_horizon]
    cutoff_ts_ms = max(0, as_of_ts_ms - window_days * _DAY_MS)
    rows = tuple(samples)
    if any(row.observed_ts_ms > as_of_ts_ms for row in rows):
        raise ValueError("근거 신선도 판정에 미래 표본을 사용할 수 없습니다.")
    matching_rows = tuple(
        row
        for row in rows
        if row.evidence_epoch_id == epoch.epoch_id
        and row.strategy_version == epoch.strategy_version
        and cutoff_ts_ms <= row.observed_ts_ms <= as_of_ts_ms
    )
    observed_unique_samples = len({row.opportunity_id for row in matching_rows})
    mismatch_reasons: list[str] = []
    if epoch.closed_ts_ms is not None and as_of_ts_ms > epoch.closed_ts_ms:
        mismatch_reasons.append("EVIDENCE_EPOCH_CLOSED")
    if any(row.evidence_epoch_id != epoch.epoch_id for row in rows):
        mismatch_reasons.append("EVIDENCE_EPOCH_ID_MISMATCH")
    if any(row.strategy_version != epoch.strategy_version for row in rows):
        mismatch_reasons.append("EVIDENCE_STRATEGY_VERSION_MISMATCH")
    if mismatch_reasons:
        status = EvidenceFreshnessStatus.EPOCH_MISMATCH
        reason_codes = tuple(mismatch_reasons)
    elif observed_unique_samples < minimum_samples:
        status = EvidenceFreshnessStatus.STALE_EVIDENCE
        reason_codes = (
            "STALE_EVIDENCE",
            f"UNIQUE_SAMPLES_LT_{minimum_samples}",
        )
    else:
        status = EvidenceFreshnessStatus.FRESH
        reason_codes = ()
    return EvidenceFreshnessAssessment(
        status=status,
        horizon=resolved_horizon,
        window_days=window_days,
        minimum_unique_samples=minimum_samples,
        observed_unique_samples=observed_unique_samples,
        cutoff_ts_ms=cutoff_ts_ms,
        evidence_epoch_id=epoch.epoch_id,
        strategy_version=epoch.strategy_version,
        reason_codes=reason_codes,
    )


class HysteresisMode(StrEnum):
    FAST = "FAST"
    SWING = "SWING"


class HysteresisDecision(StrEnum):
    HELD = "HELD"
    CONFIRMING = "CONFIRMING"
    ARMED = "ARMED"
    DISARMED = "DISARMED"
    SIDE_FLIP_BLOCKED = "SIDE_FLIP_BLOCKED"
    SIDE_FLIPPED = "SIDE_FLIPPED"


@dataclass(frozen=True, slots=True)
class HysteresisConfig:
    arm_threshold: Decimal = Decimal("0.65")
    disarm_threshold: Decimal = Decimal("0.50")
    confirmation_mode: HysteresisMode = HysteresisMode.FAST
    fast_required_trigger_bars: int = 2
    fast_event_flow_confirmation_ms: int = 500
    side_flip_cooldown_trigger_bars: int = 2

    def __post_init__(self) -> None:
        _validate_unit_decimal("arm_threshold", self.arm_threshold)
        _validate_unit_decimal("disarm_threshold", self.disarm_threshold)
        if self.disarm_threshold >= self.arm_threshold:
            raise ValueError("disarm threshold는 arm threshold보다 작아야 합니다.")
        if not isinstance(self.confirmation_mode, HysteresisMode):
            raise ValueError("confirmation_mode는 HysteresisMode여야 합니다.")
        for field_name in (
            "fast_required_trigger_bars",
            "fast_event_flow_confirmation_ms",
            "side_flip_cooldown_trigger_bars",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field_name}는 양의 정수여야 합니다.")


@dataclass(frozen=True, slots=True)
class HysteresisState:
    armed: bool = False
    side: Side | None = None
    pending_side: Side | None = None
    trigger_confirmations: int = 0
    setup_confirmed: bool = False
    side_flip_cooldown_bars: int = 0
    last_trigger_bar_index: int | None = None

    def __post_init__(self) -> None:
        if self.armed != (self.side is not None):
            raise ValueError("armed 상태와 방향 존재 여부가 일치해야 합니다.")
        if self.pending_side is None and (
            self.trigger_confirmations or self.setup_confirmed or self.side_flip_cooldown_bars
        ):
            raise ValueError("대기 방향 없이 확인 상태를 저장할 수 없습니다.")
        if self.trigger_confirmations < 0 or self.side_flip_cooldown_bars < 0:
            raise ValueError("히스테리시스 확인 카운터는 음수일 수 없습니다.")
        if self.last_trigger_bar_index is not None:
            _validate_timestamp("last_trigger_bar_index", self.last_trigger_bar_index)


@dataclass(frozen=True, slots=True)
class HysteresisTransition:
    decision: HysteresisDecision
    state: HysteresisState
    reason_codes: tuple[str, ...]


def _confirmation_passed(
    config: HysteresisConfig,
    *,
    trigger_confirmations: int,
    setup_confirmed: bool,
    event_flow_confirmation_ms: int,
) -> bool:
    if config.confirmation_mode is HysteresisMode.FAST:
        return (
            trigger_confirmations >= config.fast_required_trigger_bars
            or event_flow_confirmation_ms >= config.fast_event_flow_confirmation_ms
        )
    return setup_confirmed and trigger_confirmations >= 1


def _pending_progress(
    state: HysteresisState,
    *,
    proposed_side: Side,
    new_trigger: bool,
    completed_setup_bar: bool,
    side_flip: bool,
) -> tuple[int, bool, int]:
    same_pending_side = state.pending_side is proposed_side
    trigger_confirmations = state.trigger_confirmations if same_pending_side else 0
    setup_confirmed = state.setup_confirmed if same_pending_side else False
    side_flip_cooldown_bars = state.side_flip_cooldown_bars if same_pending_side else 0
    if new_trigger:
        trigger_confirmations += 1
        if side_flip:
            side_flip_cooldown_bars += 1
    setup_confirmed = setup_confirmed or completed_setup_bar
    return trigger_confirmations, setup_confirmed, side_flip_cooldown_bars


def advance_hysteresis(
    state: HysteresisState,
    *,
    config: HysteresisConfig,
    score: Decimal,
    proposed_side: Side | None,
    completed_trigger_bar: bool = False,
    trigger_bar_index: int | None = None,
    completed_setup_bar: bool = False,
    event_flow_confirmation_ms: int = 0,
    structure_invalidated: bool = False,
    opposite_structure_confirmed: bool = False,
) -> HysteresisTransition:
    """완료된 prefix 입력만으로 arm·disarm·side flip 상태를 전이한다."""

    _validate_unit_decimal("score", score)
    if proposed_side is not None and not isinstance(proposed_side, Side):
        raise ValueError("proposed_side는 Side 또는 None이어야 합니다.")
    if not isinstance(event_flow_confirmation_ms, int) or isinstance(
        event_flow_confirmation_ms, bool
    ) or event_flow_confirmation_ms < 0:
        raise ValueError("event flow 확인시간은 0 이상의 정수여야 합니다.")
    if completed_trigger_bar:
        if trigger_bar_index is None:
            raise ValueError("완료 trigger bar에는 index가 필요합니다.")
        _validate_timestamp("trigger_bar_index", trigger_bar_index)
    elif trigger_bar_index is not None:
        raise ValueError("완료되지 않은 trigger bar의 index를 처리할 수 없습니다.")
    new_trigger = bool(
        completed_trigger_bar
        and trigger_bar_index is not None
        and (
            state.last_trigger_bar_index is None
            or trigger_bar_index > state.last_trigger_bar_index
        )
    )
    last_trigger_bar_index = (
        trigger_bar_index if new_trigger else state.last_trigger_bar_index
    )
    duplicate_reason = (
        ("DUPLICATE_OR_OUT_OF_ORDER_TRIGGER_BAR",)
        if completed_trigger_bar and not new_trigger
        else ()
    )

    if score <= config.disarm_threshold:
        next_state = HysteresisState(last_trigger_bar_index=last_trigger_bar_index)
        return HysteresisTransition(
            decision=(
                HysteresisDecision.DISARMED if state.armed else HysteresisDecision.HELD
            ),
            state=next_state,
            reason_codes=("DISARM_THRESHOLD_REACHED", *duplicate_reason),
        )

    if score < config.arm_threshold:
        next_state = HysteresisState(
            armed=state.armed,
            side=state.side,
            pending_side=state.pending_side,
            trigger_confirmations=state.trigger_confirmations,
            setup_confirmed=state.setup_confirmed,
            side_flip_cooldown_bars=state.side_flip_cooldown_bars,
            last_trigger_bar_index=last_trigger_bar_index,
        )
        return HysteresisTransition(
            decision=HysteresisDecision.HELD,
            state=next_state,
            reason_codes=("NO_TRADE_ZONE_STATE_HELD", *duplicate_reason),
        )

    if proposed_side is None:
        next_state = HysteresisState(
            armed=state.armed,
            side=state.side,
            last_trigger_bar_index=last_trigger_bar_index,
        )
        return HysteresisTransition(
            decision=HysteresisDecision.HELD,
            state=next_state,
            reason_codes=("ARM_SIDE_MISSING", *duplicate_reason),
        )

    side_flip = state.armed and proposed_side is not state.side
    if state.armed and not side_flip:
        next_state = HysteresisState(
            armed=True,
            side=state.side,
            last_trigger_bar_index=last_trigger_bar_index,
        )
        return HysteresisTransition(
            decision=HysteresisDecision.HELD,
            state=next_state,
            reason_codes=("ARMED_STATE_HELD", *duplicate_reason),
        )

    if side_flip and (not structure_invalidated or not opposite_structure_confirmed):
        missing = (
            "SIDE_FLIP_REQUIRES_SETUP_INVALIDATION"
            if not structure_invalidated
            else "SIDE_FLIP_REQUIRES_OPPOSITE_STRUCTURE"
        )
        next_state = HysteresisState(
            armed=True,
            side=state.side,
            last_trigger_bar_index=last_trigger_bar_index,
        )
        return HysteresisTransition(
            decision=HysteresisDecision.SIDE_FLIP_BLOCKED,
            state=next_state,
            reason_codes=(missing, *duplicate_reason),
        )

    trigger_confirmations, setup_confirmed, side_flip_cooldown_bars = _pending_progress(
        state,
        proposed_side=proposed_side,
        new_trigger=new_trigger,
        completed_setup_bar=completed_setup_bar,
        side_flip=side_flip,
    )
    confirmation_passed = _confirmation_passed(
        config,
        trigger_confirmations=trigger_confirmations,
        setup_confirmed=setup_confirmed,
        event_flow_confirmation_ms=event_flow_confirmation_ms,
    )
    cooldown_passed = (
        not side_flip
        or side_flip_cooldown_bars >= config.side_flip_cooldown_trigger_bars
    )
    if confirmation_passed and cooldown_passed:
        next_state = HysteresisState(
            armed=True,
            side=proposed_side,
            last_trigger_bar_index=last_trigger_bar_index,
        )
        return HysteresisTransition(
            decision=(
                HysteresisDecision.SIDE_FLIPPED
                if side_flip
                else HysteresisDecision.ARMED
            ),
            state=next_state,
            reason_codes=(
                "SIDE_FLIP_CONFIRMED" if side_flip else "ARM_CONFIRMATION_PASSED",
                *duplicate_reason,
            ),
        )

    pending_state = HysteresisState(
        armed=state.armed,
        side=state.side,
        pending_side=proposed_side,
        trigger_confirmations=trigger_confirmations,
        setup_confirmed=setup_confirmed,
        side_flip_cooldown_bars=side_flip_cooldown_bars,
        last_trigger_bar_index=last_trigger_bar_index,
    )
    reasons = ["ARM_CONFIRMATION_PENDING"]
    if side_flip and not cooldown_passed:
        reasons.append("SIDE_FLIP_COOLDOWN_ACTIVE")
    reasons.extend(duplicate_reason)
    return HysteresisTransition(
        decision=HysteresisDecision.CONFIRMING,
        state=pending_state,
        reason_codes=tuple(reasons),
    )
