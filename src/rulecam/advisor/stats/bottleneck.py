"""Bottleneck lenses: utilisation, waiting time and Roser's active-period method (P4-08b).

Spans are `(start, end)` pairs in seconds. Stations are named by station id, never by a person.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Mapping, Sequence

Span = tuple[float, float]


def merge_spans(spans: Iterable[Span]) -> list[Span]:
    """Merge overlapping or touching spans into disjoint ones, in time order."""
    merged: list[Span] = []
    for start, end in sorted(s for s in spans if s[1] > s[0]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def utilisation(busy: Iterable[Span], window: Span) -> float:
    """Share of `window` during which at least one busy span was active.

    Raises:
        ValueError: If the window is empty.
    """
    length = window[1] - window[0]
    if length <= 0:
        raise ValueError("window must have positive length")
    covered = sum(
        max(0.0, min(end, window[1]) - max(start, window[0])) for start, end in merge_spans(busy)
    )
    return covered / length


def mean_wait(waits: Sequence[float]) -> float | None:
    """Mean waiting time, or None if nothing waited (the waiting-time lens)."""
    return sum(waits) / len(waits) if waits else None


def active_period_bottleneck(
    active: Mapping[str, Sequence[Span]], window: Span
) -> dict[str, float]:
    """Share of the window each station is the bottleneck, by Roser's active-period method.

    At each moment the bottleneck is the station whose current uninterrupted active period began
    earliest.

    Args:
        active: Each station's active spans.
        window: The span to analyse.

    Returns:
        Each station's share of `window` for which it was the bottleneck. Moments when no station
        is active belong to nobody, so the shares can sum to less than 1. Ties go to the lower
        station id.

    Raises:
        ValueError: If the window is empty.
    """
    length = window[1] - window[0]
    if length <= 0:
        raise ValueError("window must have positive length")
    periods = {station: merge_spans(spans) for station, spans in active.items()}
    edges = {window[0], window[1]}
    for spans in periods.values():
        for start, end in spans:
            edges.update(t for t in (start, end) if window[0] < t < window[1])
    shares = dict.fromkeys(active, 0.0)
    ordered = sorted(edges)
    for left, right in itertools.pairwise(ordered):
        middle = (left + right) / 2.0
        current: list[tuple[float, str]] = []
        for station, spans in periods.items():
            for start, end in spans:
                if start <= middle < end:
                    current.append((start, station))
        if current:
            shares[min(current)[1]] += (right - left) / length
    return shares
