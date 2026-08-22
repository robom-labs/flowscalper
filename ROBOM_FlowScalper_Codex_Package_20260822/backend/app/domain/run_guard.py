"""하나의 Run에 다른 거래소 이벤트가 섞이는 것을 차단한다."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.domain.models import MarketEvent, Venue


class VenueMixingError(RuntimeError):
    """Run의 고정 거래소와 다른 이벤트가 들어올 때 발생한다."""


@dataclass(frozen=True, slots=True)
class RunVenueGuard:
    run_id: str
    venue: Venue

    def validate(self, event: MarketEvent) -> None:
        if event.run_id != self.run_id or event.venue is not self.venue:
            raise VenueMixingError("Run ID 또는 거래소가 고정된 Run 계약과 다릅니다.")
