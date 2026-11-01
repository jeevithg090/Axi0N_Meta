"""Hour-of-day baseline (task P4-08b)."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class HourBaseline:
    """Typical value and spread for one hour of the day."""

    hour: int
    median: float
    mad: float
    n: int


def hour_of_day_baseline(
    timestamps: Sequence[datetime], values: Sequence[float]
) -> dict[int, HourBaseline]:
    """Median and median absolute deviation of `values` for each hour of the day.

    Raises:
        ValueError: If the two sequences differ in length.
    """
    if len(timestamps) != len(values):
        raise ValueError("timestamps and values must have the same length")
    by_hour: dict[int, list[float]] = defaultdict(list)
    for ts, value in zip(timestamps, values, strict=True):
        by_hour[ts.hour].append(value)
    result = {}
    for hour, group in by_hour.items():
        median = statistics.median(group)
        mad = statistics.median(abs(v - median) for v in group)
        result[hour] = HourBaseline(hour, median, mad, len(group))
    return result


def robust_z(value: float, baseline: HourBaseline) -> float | None:
    """How many robust standard deviations `value` is above the hour's median.

    Returns:
        The score, or None when the baseline has no spread to compare against.
    """
    if baseline.mad == 0:
        return None
    return (value - baseline.median) / (MAD_TO_SIGMA * baseline.mad)
