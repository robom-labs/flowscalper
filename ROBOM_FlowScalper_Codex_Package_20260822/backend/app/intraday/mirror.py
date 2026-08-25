# 원본 신호와 방향만 뒤집은 기계적 미러를 동일 정보집합으로 묶는다.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from backend.app.domain.models import Side


class ResearchVariantKind(StrEnum):
    ORIGINAL = "ORIGINAL"
    MECHANICAL_MIRROR = "MECHANICAL_MIRROR"
    HYPOTHESIS_REVERSE = "HYPOTHESIS_REVERSE"


@dataclass(frozen=True, slots=True)
class SignalVariant:
    candidate_id: str
    variant: ResearchVariantKind
    symbol: str
    side: Side
    signal_ts_ms: int
    interval_seconds: int
    information_set_id: str


def pair_original_and_mechanical_mirror(
    original: SignalVariant,
) -> tuple[SignalVariant, SignalVariant]:
    if original.variant is not ResearchVariantKind.ORIGINAL:
        raise ValueError("ORIGINAL 신호만 기계적 미러와 묶을 수 있습니다.")
    opposite = Side.SHORT if original.side is Side.LONG else Side.LONG
    return (
        original,
        SignalVariant(
            candidate_id=original.candidate_id,
            variant=ResearchVariantKind.MECHANICAL_MIRROR,
            symbol=original.symbol,
            side=opposite,
            signal_ts_ms=original.signal_ts_ms,
            interval_seconds=original.interval_seconds,
            information_set_id=original.information_set_id,
        ),
    )
