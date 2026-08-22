"""현재 시점 이전 표본만 사용하는 강건한 임계값 유틸리티를 제공한다."""

from __future__ import annotations

import statistics


def robust_z(history: list[float], current: float) -> float:
    if not history:
        return 0.0
    median = statistics.median(history)
    deviations = [abs(value - median) for value in history]
    mad = statistics.median(deviations)
    if mad == 0:
        return 0.0 if current == median else (1.0 if current > median else -1.0)
    return 0.67448975 * (current - median) / mad


def rolling_percentile(history: list[float], current: float) -> float:
    if not history:
        return 0.5
    below = sum(value < current for value in history)
    equal = sum(value == current for value in history)
    return (below + equal * 0.5) / len(history)
