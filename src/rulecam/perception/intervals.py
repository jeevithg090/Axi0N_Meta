"""Time-interval algebra used for watched-time accounting and station intervals.

Intervals are closed-open spans `[start, end)` of aware datetimes. Every function is pure and
returns a new, sorted list of non-overlapping intervals unless its docstring says otherwise.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from pydantic import model_validator

from rulecam.contracts import Frozen


class Interval(Frozen):
    """A time span `[start, end)`; `end` may equal `start` for an instant."""

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> Interval:
        if self.end < self.start:
            msg = f"interval ends before it starts: {self.start} > {self.end}"
            raise ValueError(msg)
        return self

    @property
    def seconds(self) -> float:
        """Length of the interval in seconds."""
        return (self.end - self.start).total_seconds()


def merge(intervals: Iterable[Interval], max_gap_s: float = 0.0) -> list[Interval]:
    """Union of intervals, also joining neighbours separated by at most `max_gap_s`.

    Args:
        intervals: Intervals in any order; they may overlap.
        max_gap_s: Gaps up to this many seconds are bridged.

    Returns:
        Sorted, disjoint intervals.
    """
    gap = timedelta(seconds=max_gap_s)
    out: list[Interval] = []
    for item in sorted(intervals, key=lambda i: (i.start, i.end)):
        if out and item.start <= out[-1].end + gap:
            if item.end > out[-1].end:
                out[-1] = Interval(start=out[-1].start, end=item.end)
        else:
            out.append(item)
    return out


def total_seconds(intervals: Iterable[Interval]) -> float:
    """Total length in seconds of the union of `intervals` (overlaps are counted once)."""
    return sum(i.seconds for i in merge(intervals))


def overlaps(a: Interval, b: Interval) -> bool:
    """Return True when `a` and `b` share time.

    Two spans overlap when they share a positive length of time. An instant overlaps a span
    that contains it (`start <= t < end`).

    Args:
        a: First interval.
        b: Second interval.

    Returns:
        Whether the intervals overlap.
    """
    if a.seconds == 0:
        return b.start <= a.start < b.end
    if b.seconds == 0:
        return a.start <= b.start < a.end
    return max(a.start, b.start) < min(a.end, b.end)


def clip(intervals: Iterable[Interval], window: Interval) -> list[Interval]:
    """Restrict intervals to `window`, dropping the parts outside it.

    Args:
        intervals: Intervals in any order.
        window: The span to keep.

    Returns:
        Sorted, disjoint intervals inside `window` with positive length.
    """
    out = []
    for item in merge(intervals):
        start, end = max(item.start, window.start), min(item.end, window.end)
        if start < end:
            out.append(Interval(start=start, end=end))
    return out


def subtract(intervals: Iterable[Interval], removed: Sequence[Interval]) -> list[Interval]:
    """Remove every part of `intervals` that lies inside `removed`.

    Use it to drop BLIND time from compliant time before counting it.

    Args:
        intervals: The time to keep, in any order.
        removed: The time to cut out, in any order.

    Returns:
        Sorted, disjoint intervals with positive length.
    """
    cuts = merge(removed)
    out: list[Interval] = []
    for item in merge(intervals):
        start = item.start
        for cut in cuts:
            if cut.end <= start or cut.start >= item.end:
                continue
            if cut.start > start:
                out.append(Interval(start=start, end=cut.start))
            start = max(start, cut.end)
        if start < item.end:
            out.append(Interval(start=start, end=item.end))
    return out


def intersect(a: Iterable[Interval], b: Iterable[Interval]) -> list[Interval]:
    """Return the time covered by both `a` and `b`.

    Args:
        a: First set of intervals.
        b: Second set of intervals.

    Returns:
        Sorted, disjoint intervals with positive length.
    """
    out = []
    for window in merge(b):
        out.extend(clip(a, window))
    return merge(out)


def complement(intervals: Iterable[Interval], window: Interval) -> list[Interval]:
    """Return the parts of `window` not covered by `intervals`.

    Args:
        intervals: Covered time, in any order.
        window: The span to complement within.

    Returns:
        Sorted, disjoint intervals with positive length.
    """
    return subtract([window], list(intervals))
