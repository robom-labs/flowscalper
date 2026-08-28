"""차트·로컬 봉·공개시장 요청이 공유하는 단일 시간구간 계약을 정의한다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TimeframeSpec:
    seconds: int
    label: str
    label_ko: str
    public_chart: bool
    binance_key: str | None
    upbit_unit: int | None

    def as_public_dict(self) -> dict[str, object]:
        return {
            "interval_seconds": self.seconds,
            "label": self.label,
            "label_ko": self.label_ko,
        }


class TimeframeRegistry:
    """지원 시간구간과 거래소 매핑을 한 곳에서 검증한다."""

    def __init__(self, specs: tuple[TimeframeSpec, ...]) -> None:
        if len({spec.seconds for spec in specs}) != len(specs):
            raise ValueError("시간구간 초 값은 중복될 수 없습니다.")
        self._specs = specs
        self._by_seconds = {spec.seconds: spec for spec in specs}

    @property
    def builder_intervals(self) -> tuple[int, ...]:
        return tuple(spec.seconds for spec in self._specs)

    @property
    def public_chart_intervals(self) -> tuple[int, ...]:
        return tuple(spec.seconds for spec in self._specs if spec.public_chart)

    def public_rows(self) -> list[dict[str, object]]:
        return [spec.as_public_dict() for spec in self._specs if spec.public_chart]

    def label(self, seconds: int) -> str:
        spec = self._by_seconds.get(seconds)
        return spec.label if spec is not None else f"{seconds}s"

    def validate_builder(self, seconds: int) -> None:
        if seconds not in self._by_seconds:
            raise ValueError("지원하지 않는 캔들 시간구간입니다.")

    def validate_public_chart(self, seconds: int) -> None:
        spec = self._by_seconds.get(seconds)
        if spec is None or not spec.public_chart:
            raise ValueError("지원하지 않는 차트 시간구간입니다.")

    def exchange_interval(self, venue: str, seconds: int) -> str | int:
        self.validate_public_chart(seconds)
        spec = self._by_seconds[seconds]
        if venue == "BINANCE_USDM" and spec.binance_key is not None:
            return spec.binance_key
        if venue == "UPBIT_KRW" and spec.upbit_unit is not None:
            return spec.upbit_unit
        raise ValueError("해당 공개시장에서 지원하지 않는 차트 시간구간입니다.")


TIMEFRAME_REGISTRY = TimeframeRegistry(
    (
        TimeframeSpec(1, "1s", "1초", False, None, None),
        TimeframeSpec(5, "5s", "5초", False, None, None),
        TimeframeSpec(15, "15s", "15초", False, None, None),
        TimeframeSpec(30, "30s", "30초", False, None, None),
        TimeframeSpec(60, "1m", "1분", True, "1m", 1),
        TimeframeSpec(180, "3m", "3분", True, "3m", 3),
        TimeframeSpec(300, "5m", "5분", True, "5m", 5),
        TimeframeSpec(600, "10m", "10분", False, None, 10),
        TimeframeSpec(900, "15m", "15분", True, "15m", 15),
        TimeframeSpec(1_800, "30m", "30분", True, "30m", 30),
        TimeframeSpec(3_600, "1h", "1시간", True, "1h", 60),
        TimeframeSpec(14_400, "4h", "4시간", True, "4h", 240),
        TimeframeSpec(21_600, "6h", "6시간", False, None, None),
    )
)
