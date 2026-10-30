"""Predicate outputs: a boolean and a measured value per (time step, subject).

A `Signal` is either per-subject (one `Series` per track of the subject concept, covering that
track's index range) or scene-level (one dense `Series` under the key `SCENE`).

Combining signals with different subjects (for example `all(distance(person, bucket) < 2,
moving(bucket))`) reduces every child whose subject concept differs from the first per-subject
child to a scene-level "for some object" signal, then broadcasts it to each subject.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from rulecam.rules.geometry import BoolArray, FloatArray

SCENE = "scene"

Reducer = Callable[[FloatArray, FloatArray], FloatArray]


@dataclass(frozen=True, eq=False)
class Series:
    """Truth and value over the timeline index range `[start, start + len(truth))`.

    Attributes:
        start: First timeline index covered.
        truth: Whether the predicate holds at each sample.
        value: The measured value at each sample (NaN when unknown).
        known: Whether the subject was observed at each sample.
    """

    start: int
    truth: BoolArray
    value: FloatArray
    known: BoolArray

    @property
    def stop(self) -> int:
        """One past the last timeline index covered."""
        return self.start + len(self.truth)

    def padded(self, start: int, stop: int) -> Series:
        """Return this series re-indexed to `[start, stop)`, false and unknown outside."""
        size = stop - start
        truth = np.zeros(size, dtype=np.bool_)
        known = np.zeros(size, dtype=np.bool_)
        value = np.full(size, np.nan)
        lo, hi = max(start, self.start), min(stop, self.stop)
        if lo < hi:
            truth[lo - start : hi - start] = self.truth[lo - self.start : hi - self.start]
            known[lo - start : hi - start] = self.known[lo - self.start : hi - self.start]
            value[lo - start : hi - start] = self.value[lo - self.start : hi - self.start]
        return Series(start=start, truth=truth, value=value, known=known)


@dataclass(frozen=True, eq=False)
class Signal:
    """A predicate's output over all subjects.

    Attributes:
        subject: The subject concept, or None for a scene-level signal.
        unit: Unit of `Series.value` ("m", "s", "count", …), or None if it is only a 0/1 flag.
        series: One series per subject key (track id), or `{SCENE: series}`.
    """

    subject: str | None
    unit: str | None
    series: Mapping[str, Series]


def make_series(start: int, truth: BoolArray, value: FloatArray, known: BoolArray) -> Series:
    """Build a series, forcing truth to false wherever the subject is unknown."""
    return Series(start=start, truth=truth & known, value=value, known=known)


def scene_series(signal: Signal, n: int) -> Series:
    """Reduce a signal to one dense scene-level series ("for some subject").

    Truth and known are OR-ed over subjects. The value is the minimum over subjects for
    distances and the maximum otherwise.

    Args:
        signal: Any signal.
        n: Length of the timeline.

    Returns:
        A series covering `[0, n)`.
    """
    if signal.subject is None:
        return signal.series[SCENE].padded(0, n)
    reducer: Reducer = np.fmin if signal.unit == "m" else np.fmax
    truth = np.zeros(n, dtype=np.bool_)
    known = np.zeros(n, dtype=np.bool_)
    value = np.full(n, np.nan)
    for key in sorted(signal.series):
        series = signal.series[key]
        window = slice(series.start, series.stop)
        truth[window] |= series.truth
        known[window] |= series.known
        value[window] = reducer(value[window], series.value)
    return Series(start=0, truth=truth, value=value, known=known)


def align(signals: Sequence[Signal], n: int) -> tuple[str | None, list[dict[str, Series]]]:
    """Bring child signals onto a common set of subjects for a logical combination.

    Args:
        signals: Child signals.
        n: Length of the timeline.

    Returns:
        The subject concept of the result (None for scene-level), and for each child a mapping
        from every result subject key to a series over that subject's index range.
    """
    subject = next((s.subject for s in signals if s.subject is not None), None)
    if subject is None:
        return None, [{SCENE: scene_series(s, n)} for s in signals]
    keys = sorted({k for s in signals if s.subject == subject for k in s.series})
    ranges: dict[str, tuple[int, int]] = {}
    for key in keys:
        own = [s.series[key] for s in signals if s.subject == subject and key in s.series]
        ranges[key] = (min(x.start for x in own), max(x.stop for x in own))
    aligned: list[dict[str, Series]] = []
    for sig in signals:
        dense = None if sig.subject == subject else scene_series(sig, n)
        per_key = {}
        for key, (lo, hi) in ranges.items():
            source = dense if dense is not None else sig.series.get(key)
            per_key[key] = _blank(lo, hi) if source is None else source.padded(lo, hi)
        aligned.append(per_key)
    return subject, aligned


def _blank(start: int, stop: int) -> Series:
    size = stop - start
    return Series(
        start=start,
        truth=np.zeros(size, dtype=np.bool_),
        value=np.full(size, np.nan),
        known=np.zeros(size, dtype=np.bool_),
    )


def first_unit(signals: Sequence[Signal]) -> tuple[int | None, str | None]:
    """Index and unit of the first child that carries a measured unit."""
    for index, signal in enumerate(signals):
        if signal.unit is not None:
            return index, signal.unit
    return None, None


def combine(signals: Sequence[Signal], n: int, *, conjunction: bool) -> Signal:
    """Logical AND (`conjunction=True`) or OR of child signals.

    The value is taken from the first child that has a unit, so `all(distance < 2, moving)`
    reports the distance.

    Args:
        signals: At least one child signal.
        n: Length of the timeline.
        conjunction: True for `all`, False for `any`.

    Returns:
        The combined signal.
    """
    subject, aligned = align(signals, n)
    unit_index, unit = first_unit(signals)
    out: dict[str, Series] = {}
    for key in aligned[0]:
        parts = [child[key] for child in aligned]
        truths = np.stack([p.truth for p in parts])
        knowns = np.stack([p.known for p in parts])
        truth = truths.all(axis=0) if conjunction else truths.any(axis=0)
        known = knowns.all(axis=0) if conjunction else knowns.any(axis=0)
        value = parts[unit_index].value if unit_index is not None else truth.astype(np.float64)
        out[key] = make_series(parts[0].start, truth, value, known)
    return Signal(subject=subject, unit=unit, series=out)


def negate(signal: Signal) -> Signal:
    """Logical NOT: true where the subject is observed and the child is false."""
    out = {
        key: Series(start=s.start, truth=s.known & ~s.truth, value=s.value, known=s.known)
        for key, s in signal.series.items()
    }
    return Signal(subject=signal.subject, unit=signal.unit, series=out)
