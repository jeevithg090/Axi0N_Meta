"""Temporal operators over sampled signals (P1-06 step 5).

Durations are counted in samples: a run of `n` true samples lasts `n / hz` seconds, and a
threshold of `s` seconds needs `ceil(s * hz)` samples.
"""

from __future__ import annotations

import numpy as np

from rulecam.contracts.rule_ir import Absence, Dwell, Inside, Persist, Rate, Sequence
from rulecam.rules.geometry import BoolArray, FloatArray
from rulecam.rules.predicates.presence import count_in_zone
from rulecam.rules.predicates.spatial import inside
from rulecam.rules.scene import Scene
from rulecam.rules.signals import SCENE, Series, Signal, make_series, scene_series
from rulecam.rules.timeline import Timeline

Run = tuple[int, int]  # first and last index (inclusive)

RATE_WINDOW_S = 60.0


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


def run_lengths(truth: BoolArray) -> FloatArray:
    """Number of consecutive true samples ending at each index (0 where false)."""
    index = np.arange(len(truth))
    last_false = np.maximum.accumulate(np.where(truth, -1, index))
    return (index - last_false).astype(np.float64)


def rising_edges(truth: BoolArray, *, first_counts: bool) -> BoolArray:
    """Samples where `truth` turns true; the first sample counts only if `first_counts`."""
    edges = truth.copy()
    edges[1:] &= ~truth[:-1]
    if len(edges) and not first_counts:
        edges[0] = False
    return edges


def persist(node: Persist, child: Signal, timeline: Timeline) -> Signal:
    """True once the child has held continuously for `seconds`; the value is the run so far."""
    need = timeline.samples_for(node.seconds)
    out = {}
    for key, series in child.series.items():
        run = run_lengths(series.truth)
        value = np.where(series.known, run / timeline.hz, np.nan)
        out[key] = make_series(series.start, run >= need, value, series.known)
    return Signal(subject=child.subject, unit="s", series=out)


def absence(node: Absence, scene: Scene) -> Signal:
    """Scene-level: no `object` inside `zone` for at least `seconds`.

    The window start counts as the start of an empty stretch. The value is the time the zone
    has been empty so far.
    """
    timeline = scene.timeline
    empty = count_in_zone(scene, node.object, node.zone) == 0
    run = run_lengths(empty)
    known = np.ones(timeline.n, dtype=np.bool_)
    series = make_series(0, run >= timeline.samples_for(node.seconds), run / timeline.hz, known)
    return Signal(subject=None, unit="s", series={SCENE: series})


def rate(node: Rate, child: Signal, timeline: Timeline) -> Signal:
    """Scene-level: rising edges of the child over the trailing 60 s exceed `per_minute_gt`.

    Edges are summed over all subjects (never reported per person). A subject that is already
    true at the window start is not counted as an edge.
    """
    edges = np.zeros(timeline.n)
    for series in child.series.values():
        edges[series.start : series.stop] += rising_edges(
            series.truth, first_counts=series.start > 0
        )
    window = max(1, timeline.samples_for(RATE_WINDOW_S))
    cumulative = np.concatenate([[0.0], np.cumsum(edges)])
    index = np.arange(timeline.n)
    per_minute = (cumulative[index + 1] - cumulative[np.maximum(index + 1 - window, 0)]) * (
        RATE_WINDOW_S / (window / timeline.hz)
    )
    known = np.ones(timeline.n, dtype=np.bool_)
    series = make_series(0, per_minute > node.per_minute_gt, per_minute, known)
    return Signal(subject=None, unit="1/min", series={SCENE: series})


def sequence(node: Sequence, trigger: Signal, requirement: Signal, timeline: Timeline) -> Signal:
    """Ordered requirement for the same subject (the handwash "wash before entering the line").

    At each rising edge of `trigger` for a subject, `semantics="past"` checks that
    `requirement` held at some sample in the previous `require_within_s` seconds (inclusive of
    the edge) and fires at the edge if it did not. `semantics="future"` checks the following
    window and fires at its deadline; edges whose deadline lies beyond the window are undecided
    and never fire. A requirement about another concept is read as "for some object".
    The value at a firing sample is the requirement's best value in the window (for a dwell
    requirement: the longest wash), in the requirement's unit.
    """
    same_subject = trigger.subject is not None and requirement.subject == trigger.subject
    dense = None if same_subject else scene_series(requirement, timeline.n)
    within = timeline.samples_within(node.require_within_s)
    out = {}
    for key, trig in trigger.series.items():
        source = dense if dense is not None else requirement.series.get(key)
        req = _window_series(source, trig, within, timeline.n)
        out[key] = _sequence_series(trig, req, within, timeline.n, past=node.semantics == "past")
    return Signal(subject=trigger.subject, unit=requirement.unit, series=out)


def _window_series(source: Series | None, trig: Series, within: int, n: int) -> Series:
    lo, hi = max(0, trig.start - within), min(n, trig.stop + within)
    if source is None:
        size = hi - lo
        return Series(
            start=lo,
            truth=np.zeros(size, dtype=np.bool_),
            value=np.full(size, np.nan),
            known=np.zeros(size, dtype=np.bool_),
        )
    return source.padded(lo, hi)


def _sequence_series(trig: Series, req: Series, within: int, n: int, *, past: bool) -> Series:
    stop = trig.stop if past else req.stop
    truth = np.zeros(stop - trig.start, dtype=np.bool_)
    value = np.full(stop - trig.start, np.nan)
    satisfied = np.concatenate([[0], np.cumsum(req.truth)])
    for edge in np.flatnonzero(rising_edges(trig.truth, first_counts=trig.start > 0)):
        at = trig.start + int(edge)
        lo, hi = (at - within, at) if past else (at, at + within)
        if hi >= n:
            continue
        lo = max(lo, req.start)
        if satisfied[hi - req.start + 1] - satisfied[lo - req.start] > 0:
            continue
        fire = at if past else hi
        truth[fire - trig.start] = True
        value[fire - trig.start] = np.fmax.reduce(req.value[lo - req.start : hi - req.start + 1])
    known = trig.padded(trig.start, stop).known if past else np.ones(len(truth), dtype=np.bool_)
    return make_series(trig.start, truth, value, known)
