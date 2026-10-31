"""Near-miss metrics: closest approach and time-to-collision (P1-07, V2 Pillar 2).

Both objects are extrapolated at constant velocity from their ground foot points. Velocities are
least-squares slopes over the last `VELOCITY_SAMPLES` timeline samples. The engine adds these
metrics to the trace of every `distance_m` node at the firing time, so they are part of the
re-derivable decision (`rederive`).

The metrics measure the same geometry as the predicate they annotate (WS1d): when `distance_m`
measures to the other object's ground footprint (the projected bottom edge of its box, with a
calibration and without `.foot_point`), so do `separation_m`, `closest_m` and `ttc_s`, the
footprint moving rigidly with the object's foot point. So `separation_m` equals the node's
`value` at the firing time, and a candidate of `distance_m(lt=d)` always shows
`separation_m < d`. Vehicles the subject operates (`rules.operators`) are left out, as in the
predicate.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

import numpy as np

from rulecam.contracts import Calibration, Frozen, Track
from rulecam.contracts.rule_ir import DistanceM, Predicate
from rulecam.rules.geometry import FloatArray, project
from rulecam.rules.ir import parse_ref, walk
from rulecam.rules.scene import Scene
from rulecam.rules.timeline import SampledTrack, Timeline, prepare_tracks, resample
from rulecam.rules.trace import trace_value

HORIZON_S = 1.0
CONTACT_M = 0.5
VELOCITY_SAMPLES = 5
_MIN_SAMPLES = 2
_SEARCH_STEPS = 100
_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0
_TIE_M = 1e-9

XY = tuple[float, float]
Kinematics = tuple[float, float, float, float | None]
"""`(separation_m, closest_m, closest_in_s, ttc_s)` of one pair."""


class NearMiss(Frozen):
    """Worst-case kinematics between a subject and the objects around it at one instant.

    Attributes:
        separation_m: Current ground distance between foot points.
        closest_m: Smallest separation within the horizon under constant velocity.
        closest_in_s: When that smallest separation occurs (0 when moving apart).
        ttc_s: Time until the separation drops to the contact distance, or None when the
            objects are not on a collision course. Not limited to the horizon.
    """

    separation_m: float
    closest_m: float
    closest_in_s: float
    ttc_s: float | None


def closest_approach(
    rel_pos: tuple[float, float], rel_vel: tuple[float, float], horizon_s: float = HORIZON_S
) -> tuple[float, float]:
    """Time and distance of closest approach within `[0, horizon_s]`.

    Args:
        rel_pos: Position of the other object relative to the subject (m).
        rel_vel: Velocity of the other object relative to the subject (m/s).
        horizon_s: Extrapolation horizon.

    Returns:
        `(t, distance)` of the closest approach.
    """
    px, py = rel_pos
    vx, vy = rel_vel
    speed_sq = vx * vx + vy * vy
    t = 0.0 if speed_sq == 0 else min(max(-(px * vx + py * vy) / speed_sq, 0.0), horizon_s)
    dx, dy = px + vx * t, py + vy * t
    return t, math.sqrt(dx * dx + dy * dy)


def time_to_collision(
    rel_pos: tuple[float, float], rel_vel: tuple[float, float], contact_m: float = CONTACT_M
) -> float | None:
    """Time until the separation first equals `contact_m` under constant velocity.

    Returns:
        0.0 when already within `contact_m`, None when the objects never get that close.
    """
    px, py = rel_pos
    vx, vy = rel_vel
    a = vx * vx + vy * vy
    b = 2.0 * (px * vx + py * vy)
    c = px * px + py * py - contact_m * contact_m
    if c <= 0:
        return 0.0
    disc = b * b - 4.0 * a * c
    if a == 0 or b >= 0 or disc < 0:
        return None
    return (-b - math.sqrt(disc)) / (2.0 * a)


def velocity(track: SampledTrack, local_index: int, hz: float) -> tuple[float, float] | None:
    """Least-squares ground velocity over the samples ending at `local_index`."""
    lo = max(0, local_index - VELOCITY_SAMPLES + 1)
    pos = track.foot_m[lo : local_index + 1]
    ok = ~np.isnan(pos[:, 0])
    if ok.sum() < _MIN_SAMPLES:
        return None
    times = np.arange(lo, local_index + 1, dtype=np.float64)[ok] / hz
    return _slope(times, pos[ok, 0]), _slope(times, pos[ok, 1])


def _slope(times: FloatArray, values: FloatArray) -> float:
    dt = times - times.mean()
    return float((dt * (values - values.mean())).sum() / (dt * dt).sum())


def _state(
    track: SampledTrack, index: int, hz: float, *, still_if_unknown: bool = False
) -> tuple[FloatArray, tuple[float, float]] | None:
    """Ground position and velocity at a timeline index.

    With `still_if_unknown`, a present track whose velocity cannot be measured yet (fewer than
    two samples) counts as standing still instead of being unmeasurable.
    """
    local = index - track.start
    if not 0 <= local < len(track.present) or not track.present[local]:
        return None
    position = track.foot_m[local]
    vel = velocity(track, local, hz)
    if vel is None and still_if_unknown:
        vel = (0.0, 0.0)
    if vel is None or np.isnan(position).any():
        return None
    return position, vel


def _segment_distance(point: XY, seg_a: XY, seg_b: XY) -> float:
    """Distance from a point to a segment (scalar)."""
    dx, dy = seg_b[0] - seg_a[0], seg_b[1] - seg_a[1]
    rx, ry = point[0] - seg_a[0], point[1] - seg_a[1]
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq == 0 else min(max((rx * dx + ry * dy) / length_sq, 0.0), 1.0)
    ex, ey = rx - dx * t, ry - dy * t
    return math.sqrt(ex * ex + ey * ey)


def _argmin(gap: Callable[[float], float], lo: float, hi: float) -> float:
    """Earliest minimiser of a convex function on `[lo, hi]` (golden section, then bisection)."""
    a, b = lo, hi
    for _ in range(_SEARCH_STEPS):
        left, right = b - _GOLDEN * (b - a), a + _GOLDEN * (b - a)
        if gap(left) <= gap(right):
            b = right
        else:
            a = left
    best = (a + b) / 2
    floor = min(gap(lo), gap(best))
    return _earliest(gap, lo, best, floor + _TIE_M)


def _earliest(gap: Callable[[float], float], lo: float, hi: float, level: float) -> float:
    """Earliest `t` in `[lo, hi]` with `gap(t) <= level`, for `gap` decreasing on that range."""
    if gap(lo) <= level:
        return lo
    for _ in range(_SEARCH_STEPS):
        mid = (lo + hi) / 2
        if gap(mid) <= level:
            hi = mid
        else:
            lo = mid
    return hi


def footprint_kinematics(
    rel_a: XY, rel_b: XY, rel_vel: XY, horizon_s: float = HORIZON_S, contact_m: float = CONTACT_M
) -> Kinematics:
    """Near-miss metrics from a subject point to another object's footprint segment.

    The footprint (endpoints `rel_a`, `rel_b`, relative to the subject) moves rigidly at
    `rel_vel`. The separation at time t is a convex function of t, so its minimum within the
    horizon and its first crossing of `contact_m` are found by golden-section search and
    bisection (fixed step counts, so the result is deterministic).

    Returns:
        `(separation_m, closest_m, closest_in_s, ttc_s)`; `ttc_s` is None when the footprint
        never comes within `contact_m`.
    """

    def gap(t: float) -> float:
        return _segment_distance((-rel_vel[0] * t, -rel_vel[1] * t), rel_a, rel_b)

    separation = gap(0.0)
    speed = math.sqrt(rel_vel[0] * rel_vel[0] + rel_vel[1] * rel_vel[1])
    when = 0.0 if speed == 0 else _argmin(gap, 0.0, horizon_s)
    if separation <= contact_m:
        return separation, gap(when), when, 0.0
    ttc = None
    if speed > 0:
        reach = max(math.hypot(*rel_a), math.hypot(*rel_b)) + contact_m
        lowest = _argmin(gap, 0.0, reach / speed)
        if gap(lowest) <= contact_m:
            ttc = _earliest(gap, 0.0, lowest, contact_m)
    return separation, gap(when), when, ttc


def point_kinematics(
    rel_pos: XY, rel_vel: XY, horizon_s: float = HORIZON_S, contact_m: float = CONTACT_M
) -> Kinematics:
    """Near-miss metrics between two points (closed form)."""
    when, closest = closest_approach(rel_pos, rel_vel, horizon_s)
    ttc = time_to_collision(rel_pos, rel_vel, contact_m)
    return math.hypot(*rel_pos), closest, when, ttc


def _footprint_ends(
    other: SampledTrack, index: int, calibration: Calibration
) -> tuple[FloatArray, FloatArray] | None:
    box = other.box[index - other.start]
    ends = project(calibration, np.array([[box[0], box[3]], [box[2], box[3]]], dtype=np.float64))
    if np.isnan(ends).any():
        return None
    return ends[0], ends[1]


def _pair(
    own: tuple[FloatArray, tuple[float, float]],
    other: SampledTrack,
    index: int,
    hz: float,
    *,
    calibration: Calibration | None,
    limits: tuple[float, float],
) -> Kinematics | None:
    theirs = _state(other, index, hz, still_if_unknown=True)
    if theirs is None:
        return None
    rel_vel = (theirs[1][0] - own[1][0], theirs[1][1] - own[1][1])
    ends = None if calibration is None else _footprint_ends(other, index, calibration)
    if ends is None:
        rel_pos = (float(theirs[0][0] - own[0][0]), float(theirs[0][1] - own[0][1]))
        return point_kinematics(rel_pos, rel_vel, *limits)
    rel_a = (float(ends[0][0] - own[0][0]), float(ends[0][1] - own[0][1]))
    rel_b = (float(ends[1][0] - own[0][0]), float(ends[1][1] - own[0][1]))
    return footprint_kinematics(rel_a, rel_b, rel_vel, *limits)


def near_miss(
    subject: SampledTrack,
    others: Sequence[SampledTrack],
    index: int,
    hz: float,
    *,
    horizon_s: float = HORIZON_S,
    contact_m: float = CONTACT_M,
    calibration: Calibration | None = None,
    ignore: Callable[[SampledTrack], bool] | None = None,
) -> NearMiss | None:
    """Worst near-miss metrics between a subject and any other track at a timeline index.

    Each field is the worst over all measurable pairs (smallest distance, earliest collision).

    Args:
        subject: The subject track.
        others: Candidate other tracks (the subject itself is skipped).
        index: Timeline index.
        hz: Timeline rate.
        horizon_s: Closest-approach horizon.
        contact_m: Contact distance for the time to collision.
        calibration: When given, distances are to the other objects' ground footprints (the
            projected bottom edge of their boxes), as `distance_m` measures them.
        ignore: Other tracks to leave out at this index (the vehicle the subject operates).

    An other object whose velocity cannot be measured yet is extrapolated as standing still, so
    every object the predicate measures is in `separation_m`.

    Returns:
        The metrics, or None if the subject or every other object is unmeasurable there.
    """
    own = _state(subject, index, hz)
    if own is None:
        return None
    pairs = []
    for other in others:
        if other.track_id == subject.track_id or (ignore is not None and ignore(other)):
            continue
        metrics = _pair(
            own, other, index, hz, calibration=calibration, limits=(horizon_s, contact_m)
        )
        if metrics is not None:
            pairs.append(metrics)
    if not pairs:
        return None
    ttcs = [p[3] for p in pairs if p[3] is not None]
    worst = min(pairs, key=lambda p: (p[1], p[2]))
    return NearMiss(
        separation_m=min(p[0] for p in pairs),
        closest_m=worst[1],
        closest_in_s=worst[2],
        ttc_s=min(ttcs) if ttcs else None,
    )


def near_miss_trace(
    predicate: Predicate, scene: Scene, key: str, index: int
) -> dict[str, float | str | bool]:
    """Near-miss metrics for every `distance_m` node whose subject is `key`, at `index`.

    Keys are `<path>.separation_m`, `<path>.closest_m`, `<path>.closest_in_s` and
    `<path>.ttc_s` (`"none"` when not on a collision course).
    """
    out: dict[str, float | str | bool] = {}
    for path, node in walk(predicate):
        if not isinstance(node, DistanceM):
            continue
        subjects = scene.tracks_of(parse_ref(node.a)[0])
        subject = next((t for t in subjects if t.track_id == key), None)
        if subject is None:
            continue
        footprint = parse_ref(node.b)[1] != "foot_point"
        metrics = near_miss(
            subject,
            scene.tracks_of(parse_ref(node.b)[0]),
            index,
            scene.timeline.hz,
            calibration=scene.calibration if footprint else None,
            ignore=_ignored_at(scene, subject, index),
        )
        if metrics is None:
            continue
        out[f"{path}.separation_m"] = trace_value(metrics.separation_m)
        out[f"{path}.closest_m"] = trace_value(metrics.closest_m)
        out[f"{path}.closest_in_s"] = trace_value(metrics.closest_in_s)
        out[f"{path}.ttc_s"] = "none" if metrics.ttc_s is None else trace_value(metrics.ttc_s)
    return out


def _ignored_at(scene: Scene, subject: SampledTrack, index: int) -> Callable[[SampledTrack], bool]:
    def ignore(other: SampledTrack) -> bool:
        mask = scene.ignored(subject, other)
        return mask is not None and bool(mask[index - subject.start])

    return ignore


def near_miss_at(
    tracks: Sequence[Track],
    *,
    camera_id: str,
    subject_concept: str,
    other_concept: str,
    at: datetime,
    calibration: Calibration | None,
    hz: float = 10.0,
) -> dict[str, NearMiss]:
    """Near-miss metrics for every `subject_concept` track at time `at` (e.g. person vs forklift).

    Args:
        tracks: Tracks of any cameras and concepts; only the named ones are used.
        camera_id: Camera to evaluate.
        subject_concept: Concept of the subjects (track concept name).
        other_concept: Concept of the other objects.
        at: The instant to evaluate; velocities use the samples just before it.
        calibration: Used for points without `foot_m`.
        hz: Sampling rate for velocity estimation.

    Returns:
        Metrics by subject track id, for subjects that are measurable at `at`.
    """
    t0 = at - timedelta(seconds=VELOCITY_SAMPLES / hz)
    timeline = Timeline.build(t0, at, hz)
    concepts = frozenset({subject_concept, other_concept})
    own = [t for t in prepare_tracks(tracks, t0, at, concepts) if t.camera_id == camera_id]
    sampled = [resample(t, timeline, calibration) for t in own]
    others = [s for s in sampled if s.concept == other_concept]
    out = {}
    for subject in (s for s in sampled if s.concept == subject_concept):
        metrics = near_miss(subject, others, timeline.n - 1, hz)
        if metrics is not None:
            out[subject.track_id] = metrics
    return out
