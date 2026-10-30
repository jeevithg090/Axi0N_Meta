from __future__ import annotations

from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rulecam.perception.intervals import (
    Interval,
    clip,
    complement,
    intersect,
    merge,
    overlaps,
    subtract,
    total_seconds,
)
from tests.fakes import EPOCH


def span(a: float, b: float) -> Interval:
    return Interval(start=EPOCH + timedelta(seconds=a), end=EPOCH + timedelta(seconds=b))


def secs(intervals: list[Interval]) -> list[tuple[float, float]]:
    return [((i.start - EPOCH).total_seconds(), (i.end - EPOCH).total_seconds()) for i in intervals]


def test_interval_rejects_reversed_bounds() -> None:
    with pytest.raises(ValueError, match="ends before"):
        span(2, 1)


def test_merge_joins_overlaps_and_small_gaps() -> None:
    items = [span(5, 6), span(0, 2), span(1, 3), span(3.5, 4)]
    assert secs(merge(items)) == [(0, 3), (3.5, 4), (5, 6)]
    assert secs(merge(items, max_gap_s=0.5)) == [(0, 4), (5, 6)]


def test_total_counts_overlap_once() -> None:
    assert total_seconds([span(0, 2), span(1, 3)]) == 3


def test_overlaps_spans_and_instants() -> None:
    assert overlaps(span(0, 2), span(1, 3))
    assert not overlaps(span(0, 1), span(1, 2))
    assert overlaps(span(1, 1), span(0, 2))
    assert overlaps(span(0, 2), span(0, 0))
    assert not overlaps(span(2, 2), span(0, 2))


def test_clip_subtract_intersect_complement() -> None:
    window = span(1, 9)
    assert secs(clip([span(0, 2), span(8, 12)], window)) == [(1, 2), (8, 9)]
    assert secs(subtract([span(0, 10)], [span(2, 3), span(5, 7)])) == [(0, 2), (3, 5), (7, 10)]
    assert secs(subtract([span(0, 4)], [span(-1, 5)])) == []
    assert secs(intersect([span(0, 4), span(6, 9)], [span(3, 7)])) == [(3, 4), (6, 7)]
    assert secs(complement([span(2, 3)], window)) == [(1, 2), (3, 9)]


spans = st.lists(
    st.tuples(st.floats(0, 100), st.floats(0, 20)).map(lambda p: span(p[0], p[0] + p[1])),
    max_size=8,
)


@given(spans, spans)
def test_subtract_and_intersect_partition_the_original(
    a: list[Interval], b: list[Interval]
) -> None:
    kept, shared = subtract(a, b), intersect(a, b)
    assert total_seconds(kept) + total_seconds(shared) == pytest.approx(total_seconds(a), abs=1e-6)
    assert not any(overlaps(k, s) for k in kept for s in shared)
