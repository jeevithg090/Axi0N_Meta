"""The common timeline and track resampling (P1-06 step 2).

Every track is resampled onto one grid `t0 + k / hz`. Between two consecutive points that are at
most `HOLD_S` apart, the box and foot point are linearly interpolated. After a point with no
successor within `HOLD_S`, its value is held for at most `HOLD_S`; beyond that the track is
absent. A sample counts as `1 / hz` seconds, so a run of `n` samples lasts `n / hz` seconds.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import numpy.typing as npt

from rulecam.contracts import Calibration, Track, TrackPoint
from rulecam.rules.errors import RuleEvaluationError
from rulecam.rules.geometry import BoolArray, FloatArray, project

IntArray = npt.NDArray[np.int64]

HOLD_S = 0.5
_EPS = 1e-9
_ONE_US = timedelta(microseconds=1)


@dataclass(frozen=True)
class Timeline:
    """A sampling grid `t0 + k / hz` for `k = 0 … n - 1` covering `[t0, t1]`."""

    t0: datetime
    hz: float
    n: int

    @classmethod
    def build(cls, t0: datetime, t1: datetime, hz: float) -> Timeline:
        """Create the grid for a window.

        Args:
            t0: Window start (inclusive).
            t1: Window end (inclusive).
            hz: Samples per second.

        Returns:
            The timeline.

        Raises:
            RuleEvaluationError: If `hz` is not positive or `t1` is before `t0`.
        """
        if not hz > 0:
            msg = f"hz must be positive, got {hz}"
            raise RuleEvaluationError(msg)
        if t1 < t0:
            msg = f"window end {t1.isoformat()} is before its start {t0.isoformat()}"
            raise RuleEvaluationError(msg)
        span_s = (t1 - t0) / _ONE_US / 1e6
        return cls(t0=t0, hz=hz, n=math.floor(span_s * hz + _EPS) + 1)

    def offset_s(self, ts: datetime) -> float:
        """Seconds from `t0` to `ts` (exact to the microsecond)."""
        return (ts - self.t0) / _ONE_US / 1e6

    def ts_at(self, index: int) -> datetime:
        """Timestamp of grid sample `index`."""
        return self.t0 + timedelta(seconds=index / self.hz)

    def samples_for(self, seconds: float) -> int:
        """Smallest number of samples whose duration is at least `seconds`."""
        return max(0, math.ceil(seconds * self.hz - _EPS))

    def samples_within(self, seconds: float) -> int:
        """Largest number of samples whose duration is at most `seconds`."""
        return max(0, math.floor(seconds * self.hz + _EPS))


@dataclass(frozen=True, eq=False)
class SampledTrack:
    """One track resampled onto the timeline over the index range `[start, stop)`.

    Attributes:
        track: The source track (clipped to the window, points sorted by time).
        start: First timeline index covered.
        present: Whether the track is observed at each covered sample.
        box: Interpolated xyxy box per sample, shape (L, 4); NaN when absent.
        foot_px: Interpolated foot point in pixels, shape (L, 2); NaN when absent.
        foot_m: Interpolated ground position in metres, shape (L, 2); NaN when unknown.
        src: Index into `track.points` of the latest point at or before each sample, or -1.
    """

    track: Track
    start: int
    present: BoolArray
    box: FloatArray
    foot_px: FloatArray
    foot_m: FloatArray
    src: IntArray

    @property
    def stop(self) -> int:
        """One past the last timeline index covered."""
        return self.start + len(self.present)

    @property
    def track_id(self) -> str:
        """The opaque track id."""
        return self.track.track_id

    @property
    def concept(self) -> str:
        """The tracked concept."""
        return self.track.concept

    def point_at(self, local_index: int) -> TrackPoint | None:
        """The latest source point at or before a covered sample, if the track is present."""
        if not self.present[local_index]:
            return None
        return self.track.points[int(self.src[local_index])]


def prepare_tracks(
    tracks: Iterable[Track], t0: datetime, t1: datetime, concepts: frozenset[str]
) -> list[Track]:
    """Clip, merge and canonically order the tracks an evaluation uses.

    Points outside `[t0, t1]` are dropped, tracks of other concepts are ignored, tracks sharing
    `(camera_id, concept, track_id)` are merged, and points are sorted, so the result does not
    depend on the order of the input list.

    Args:
        tracks: Input tracks, in any order.
        t0: Window start.
        t1: Window end.
        concepts: Track concepts the rule refers to.

    Returns:
        Tracks sorted by `(camera_id, concept, track_id)`, each with at least one point.
    """
    merged: dict[tuple[str, str, str], list[TrackPoint]] = {}
    for track in tracks:
        if track.concept not in concepts:
            continue
        key = (track.camera_id, track.concept, track.track_id)
        merged.setdefault(key, []).extend(p for p in track.points if t0 <= p.ts <= t1)
    out = []
    for (camera_id, concept, track_id), points in sorted(merged.items()):
        if points:
            ordered = _canonical_order(points)
            out.append(
                Track(track_id=track_id, camera_id=camera_id, concept=concept, points=ordered)
            )
    return out


def _canonical_order(points: list[TrackPoint]) -> tuple[TrackPoint, ...]:
    """Sort points by time; collapse points sharing a timestamp to one canonical point."""
    by_ts: dict[datetime, list[TrackPoint]] = {}
    for point in points:
        by_ts.setdefault(point.ts, []).append(point)
    ordered = []
    for ts in sorted(by_ts):
        same = by_ts[ts]
        ordered.append(same[0] if len(same) == 1 else min(same, key=_canonical_json))
    return tuple(ordered)


def _canonical_json(point: TrackPoint) -> str:
    return point.model_dump_json()


def resample(track: Track, timeline: Timeline, calibration: Calibration | None) -> SampledTrack:
    """Resample one prepared track onto the timeline.

    Args:
        track: A track from `prepare_tracks` (clipped, sorted, non-empty).
        timeline: The common grid.
        calibration: Used to compute ground positions for points without `foot_m`.

    Returns:
        The sampled track.
    """
    times = np.array([timeline.offset_s(p.ts) for p in track.points], dtype=np.float64)
    boxes = np.array([p.box_xyxy for p in track.points], dtype=np.float64)
    feet = np.array([p.foot_px for p in track.points], dtype=np.float64)
    ground = _ground_positions(track.points, feet, calibration)

    start = min(timeline.n - 1, max(0, math.ceil(times[0] * timeline.hz - _EPS)))
    stop = min(timeline.n, math.floor((times[-1] + HOLD_S) * timeline.hz + _EPS) + 1)
    grid = np.arange(start, stop, dtype=np.float64) / timeline.hz
    src = np.searchsorted(times, grid + _EPS, side="right").astype(np.int64) - 1
    nxt = np.minimum(src + 1, len(times) - 1)
    safe_src = np.maximum(src, 0)
    gap = times[nxt] - times[safe_src]
    interpolate = (src >= 0) & (nxt > src) & (gap <= HOLD_S + _EPS)
    held = (src >= 0) & (grid - times[safe_src] <= HOLD_S + _EPS)
    present = interpolate | held
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(interpolate, (grid - times[safe_src]) / gap, 0.0)
    frac = np.clip(frac, 0.0, 1.0)

    def lerp(values: FloatArray) -> FloatArray:
        out = values[safe_src] + (values[nxt] - values[safe_src]) * frac[:, None]
        out[~present] = np.nan
        return out

    return SampledTrack(
        track=track,
        start=start,
        present=present,
        box=lerp(boxes),
        foot_px=lerp(feet),
        foot_m=lerp(ground),
        src=np.where(present, src, -1),
    )


def _ground_positions(
    points: tuple[TrackPoint, ...], feet_px: FloatArray, calibration: Calibration | None
) -> FloatArray:
    """Per-point ground position: `foot_m` if set, else the projected foot point, else NaN."""
    ground = np.array(
        [p.foot_m if p.foot_m is not None else (math.nan, math.nan) for p in points],
        dtype=np.float64,
    )
    missing = np.isnan(ground[:, 0])
    if calibration is not None and missing.any():
        ground[missing] = project(calibration, feet_px[missing])
    return ground
