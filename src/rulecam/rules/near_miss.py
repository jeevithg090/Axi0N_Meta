"""Near-miss metrics: closest approach and time-to-collision (P1-07, V2 Pillar 2).

Both objects are extrapolated at constant velocity from their ground foot points. Velocities are
least-squares slopes over the last `VELOCITY_SAMPLES` timeline samples. The engine adds these
metrics to the trace of every `distance_m` node at the firing time, so they are part of the
re-derivable decision (`rederive`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

import numpy as np

from rulecam.contracts import Calibration, Frozen, Track
from rulecam.contracts.rule_ir import DistanceM, Predicate
from rulecam.rules.geometry import FloatArray
from rulecam.rules.ir import parse_ref, walk
from rulecam.rules.scene import Scene
from rulecam.rules.timeline import SampledTrack, Timeline, prepare_tracks, resample
from rulecam.rules.trace import trace_value

HORIZON_S = 1.0
CONTACT_M = 0.5
VELOCITY_SAMPLES = 5
_MIN_SAMPLES = 2


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
    track: SampledTrack, index: int, hz: float
) -> tuple[FloatArray, tuple[float, float]] | None:
    local = index - track.start
    if not 0 <= local < len(track.present) or not track.present[local]:
        return None
    position = track.foot_m[local]
    vel = velocity(track, local, hz)
    if vel is None or np.isnan(position).any():
        return None
    return position, vel


def near_miss(
    subject: SampledTrack,
    others: Sequence[SampledTrack],
    index: int,
    hz: float,
    *,
    horizon_s: float = HORIZON_S,
    contact_m: float = CONTACT_M,
) -> NearMiss | None:
    """Worst near-miss metrics between a subject and any other track at a timeline index.

    Each field is the worst over all measurable pairs (smallest distance, earliest collision).

    Returns:
        The metrics, or None if the subject or every other object is unmeasurable there.
    """
    own = _state(subject, index, hz)
    if own is None:
        return None
    pairs = []
    for other in others:
        theirs = None if other.track_id == subject.track_id else _state(other, index, hz)
        if theirs is None:
            continue
        rel_pos = (float(theirs[0][0] - own[0][0]), float(theirs[0][1] - own[0][1]))
        rel_vel = (theirs[1][0] - own[1][0], theirs[1][1] - own[1][1])
        when, closest = closest_approach(rel_pos, rel_vel, horizon_s)
        ttc = time_to_collision(rel_pos, rel_vel, contact_m)
        separation = math.sqrt(rel_pos[0] * rel_pos[0] + rel_pos[1] * rel_pos[1])
        pairs.append((separation, closest, when, ttc))
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
        others = scene.tracks_of(parse_ref(node.b)[0])
        metrics = None if subject is None else near_miss(subject, others, index, scene.timeline.hz)
        if metrics is None:
            continue
        out[f"{path}.separation_m"] = trace_value(metrics.separation_m)
        out[f"{path}.closest_m"] = trace_value(metrics.closest_m)
        out[f"{path}.closest_in_s"] = trace_value(metrics.closest_in_s)
        out[f"{path}.ttc_s"] = "none" if metrics.ttc_s is None else trace_value(metrics.ttc_s)
    return out


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
