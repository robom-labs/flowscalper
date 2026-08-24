"""현재 시점 이전 표본만 사용하는 강건한 임계값 유틸리티를 제공한다."""

from __future__ import annotations

import statistics
from bisect import bisect_left, bisect_right
from collections.abc import Sequence


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


def robust_z_from_sorted(history: Sequence[float], current: float) -> float:
    """정렬된 과거값을 재정렬하지 않고 기존 robust z와 같은 값을 계산한다."""

    if not history:
        return 0.0
    median = _median_sorted(history)
    deviations = sorted(abs(value - median) for value in history)
    mad = _median_sorted(deviations)
    if mad == 0:
        return 0.0 if current == median else (1.0 if current > median else -1.0)
    return 0.67448975 * (current - median) / mad


def rolling_percentile_from_sorted(history: Sequence[float], current: float) -> float:
    """정렬된 과거값에서 기존 percentile과 같은 below/equal 순위를 계산한다."""

    if not history:
        return 0.5
    below = bisect_left(history, current)
    equal = bisect_right(history, current) - below
    return (below + equal * 0.5) / len(history)


def _median_sorted(values: Sequence[float]) -> float:
    midpoint = len(values) // 2
    if len(values) % 2:
        return float(values[midpoint])
    return (values[midpoint - 1] + values[midpoint]) / 2
