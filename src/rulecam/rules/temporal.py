"""Temporal operators over sampled signals (P1-06 step 5).

Durations are counted in samples: a run of `n` true samples lasts `n / hz` seconds, and a
threshold of `s` seconds needs `ceil(s * hz)` samples.
"""

from __future__ import annotations

import numpy as np

from rulecam.contracts.rule_ir import Dwell, Inside
from rulecam.rules.geometry import BoolArray
from rulecam.rules.predicates.spatial import inside
from rulecam.rules.scene import Scene
from rulecam.rules.signals import Series, Signal, make_series

Run = tuple[int, int]  # first and last index (inclusive)


def runs(truth: BoolArray) -> list[Run]:
    """Maximal runs of true samples as `(first, last)` index pairs."""
    edges = np.diff(np.concatenate([[0], truth.astype(np.int8), [0]]))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1
    return [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]


def merge_runs(items: list[Run], max_gap: int) -> list[Run]:
    """Merge consecutive runs separated by at most `max_gap` false samples."""
    merged: list[Run] = []
    for first, last in items:
        if merged and first - merged[-1][1] - 1 <= max_gap:
            merged[-1] = (merged[-1][0], last)
        else:
            merged.append((first, last))
    return merged


def dwell(node: Dwell, scene: Scene) -> Signal:
    """Accumulated time inside a zone, bridging gaps of at most `max_gap_s` (handwash fix).

    Runs of "inside" separated by gaps `<= max_gap_s` form one visit; the visit's total is the
    sum of its inside time (gaps excluded). The predicate becomes true at the sample where the
    running total reaches `total_s_gte` and stays true until the visit's last inside sample.
    The value is the running total of the current visit in seconds.
    """
    timeline = scene.timeline
    need = timeline.samples_for(node.total_s_gte)
    max_gap = timeline.samples_within(node.max_gap_s)
    inside_signal = inside(Inside(object=node.object, zone=node.zone), scene)
    out = {
        key: dwell_series(series, need, max_gap, timeline.hz)
        for key, series in inside_signal.series.items()
    }
    return Signal(subject=inside_signal.subject, unit="s", series=out)


def dwell_series(inside_series: Series, need: int, max_gap: int, hz: float) -> Series:
    """Apply the dwell operator to one subject's "inside" series."""
    truth_in = inside_series.truth
    total = np.zeros(len(truth_in))
    truth = np.zeros(len(truth_in), dtype=np.bool_)
    for first, last in merge_runs(runs(truth_in), max_gap):
        running = np.cumsum(truth_in[first : last + 1])
        total[first : last + 1] = running
        truth[first : last + 1] = running >= need
    value = np.where(inside_series.known, total / hz, np.nan)
    return make_series(inside_series.start, truth, value, inside_series.known)
