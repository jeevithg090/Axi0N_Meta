"""Per-camera evaluation context: sampled tracks, zones, concept names and object positions.

**Association rule (P1-06 step 4).** A `hands` (or gloves, …) detection belongs to a person when
its box centre lies inside the person's box, or within `HAND_REACH_M` of one of the person's
wrist keypoints. A detection inside two person boxes belongs to both. Pixel distances are
converted to metres with the person's box height as a `PERSON_HEIGHT_M` reference, so the rule
needs no calibration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from rulecam.contracts import Calibration, RuleIR, Zone
from rulecam.rules.errors import RuleEvaluationError
from rulecam.rules.geometry import (
    BoolArray,
    FloatArray,
    box_centres,
    norm,
    point_segment_distance,
    points_in_polygon,
    project,
)
from rulecam.rules.ir import HANDS_CONCEPT
from rulecam.rules.operators import (
    OPERATOR_WARMUP_S,
    is_person_like,
    is_vehicle_like,
    operator_mask,
)
from rulecam.rules.pose import PoseFeatures
from rulecam.rules.timeline import SampledTrack, Timeline

if TYPE_CHECKING:
    from rulecam.rules.extension import ExtensionHook

LEFT_WRIST, RIGHT_WRIST = 9, 10  # COCO-17 indices
MIN_KEYPOINT_SCORE = 0.3
PERSON_HEIGHT_M = 1.7
HAND_REACH_M = 0.15
_MIN_POLYGON_VERTICES = 3


@dataclass(frozen=True, eq=False)
class Points:
    """Positions of one reference on a subject, aligned to the subject's index range.

    Attributes:
        px: Image positions, shape (L, 2); NaN where unavailable.
        m: Ground positions in metres, shape (L, 2); NaN where unavailable.
    """

    px: FloatArray
    m: FloatArray


@dataclass(frozen=True)
class ZoneShape:
    """A resolved zone: an image polygon, or a metric buffer around a concept."""

    in_view: bool
    polygon_px: FloatArray | None = None
    buffer_of: str | None = None
    buffer_m: float = 0.0


@dataclass(frozen=True, eq=False)
class Scene:
    """Everything the predicates need for one camera."""

    rule: RuleIR
    camera_id: str
    timeline: Timeline
    calibration: Calibration | None
    zones: Mapping[str, Zone]
    tracks: tuple[SampledTrack, ...]
    pose: PoseFeatures | None = None
    operator_exclusion: bool = True
    operator_warmup_s: float = OPERATOR_WARMUP_S
    extensions: ExtensionHook | None = None
    _by_concept: dict[str, tuple[SampledTrack, ...]] = field(default_factory=dict)
    _ignored: dict[tuple[str, str], BoolArray | None] = field(default_factory=dict)

    def names(self, concept: str) -> frozenset[str]:
        """Track concept names that stand for a rule concept: its key and its SAM prompt."""
        spec = self.rule.concepts.get(concept)
        return frozenset({concept} if spec is None else {concept, spec.prompt})

    def tracks_of(self, concept: str) -> tuple[SampledTrack, ...]:
        """Sampled tracks of a rule concept, in canonical order."""
        if concept not in self._by_concept:
            names = self.names(concept)
            self._by_concept[concept] = tuple(t for t in self.tracks if t.concept in names)
        return self._by_concept[concept]

    def _person_like(self, track: SampledTrack) -> bool:
        return is_person_like(track.concept, *self._rule_names(track.concept))

    def _vehicle_like(self, track: SampledTrack) -> bool:
        return is_vehicle_like(track.concept, *self._rule_names(track.concept))

    def _rule_names(self, track_concept: str) -> tuple[str, ...]:
        """Rule concept keys whose key or prompt is this track concept."""
        return tuple(
            key
            for key, spec in sorted(self.rule.concepts.items())
            if track_concept in {key, spec.prompt}
        )

    def ignored(self, subject: SampledTrack, other: SampledTrack) -> BoolArray | None:
        """Samples of `subject`'s range where `other` must be ignored (operator exclusion).

        That is where one of the two is a person riding the other, a vehicle, as its operator
        (`rules.operators`). None when exclusion is off or the pair is not person-vehicle.
        """
        key = (subject.track_id, other.track_id)
        if key not in self._ignored:
            self._ignored[key] = self._compute_ignored(subject, other)
        return self._ignored[key]

    def _compute_ignored(self, subject: SampledTrack, other: SampledTrack) -> BoolArray | None:
        if not self.operator_exclusion:
            return None
        hz, horizon = self.timeline.hz, self.timeline.n
        if self._person_like(subject) and self._vehicle_like(other):
            mask = operator_mask(subject, other, hz, horizon, warmup_s=self.operator_warmup_s)
            return mask if mask.any() else None
        if self._vehicle_like(subject) and self._person_like(other):
            mask = operator_mask(other, subject, hz, horizon, warmup_s=self.operator_warmup_s)
            if not mask.any():
                return None
            out = np.zeros(len(subject.present), dtype=np.bool_)
            span = overlap(subject, other.start, other.stop)
            if span is not None:
                out[span[0]] = mask[span[1]]
            return out
        return None

    def zone(self, name: str) -> ZoneShape:
        """Resolve a zone name against the given zones, then the rule's own zone specs.

        Raises:
            RuleEvaluationError: If the zone is unknown or incomplete.
        """
        zone = resolve_zone(name, self.zones, self.rule)
        if zone.camera_id != self.camera_id:
            return ZoneShape(in_view=False)
        if zone.polygon_px is not None:
            if len(zone.polygon_px) < _MIN_POLYGON_VERTICES:
                msg = f"zone {name!r} has fewer than {_MIN_POLYGON_VERTICES} vertices"
                raise RuleEvaluationError(msg)
            return ZoneShape(in_view=True, polygon_px=np.asarray(zone.polygon_px, dtype=float))
        if zone.kind == "buffer" and zone.buffer_of_concept and zone.buffer_m is not None:
            return ZoneShape(in_view=True, buffer_of=zone.buffer_of_concept, buffer_m=zone.buffer_m)
        msg = f"zone {name!r} has neither a polygon nor a buffer definition"
        raise RuleEvaluationError(msg)


def resolve_zone(name: str, zones: Mapping[str, Zone], rule: RuleIR) -> Zone:
    """Find a zone by mapping key, then by `zone_id`, then in `rule.zones`.

    Raises:
        RuleEvaluationError: If no zone of that name exists.
    """
    if name in zones:
        return zones[name]
    for key in sorted(zones):
        if zones[key].zone_id == name:
            return zones[key]
    spec = rule.zones.get(name)
    if spec is None:
        msg = f"rule {rule.id!r} refers to unknown zone {name!r}"
        raise RuleEvaluationError(msg)
    return Zone(
        zone_id=name,
        camera_id=spec.camera_id,
        kind=spec.kind,
        polygon_px=spec.polygon_px,
        buffer_of_concept=spec.buffer_of,
        buffer_m=spec.buffer_m,
    )


def overlap(a: SampledTrack, b_start: int, b_stop: int) -> tuple[slice, slice] | None:
    """Slices into `a`'s arrays and into the other range's arrays where both are covered."""
    lo, hi = max(a.start, b_start), min(a.stop, b_stop)
    if lo >= hi:
        return None
    return slice(lo - a.start, hi - a.start), slice(lo - b_start, hi - b_start)


def ref_points(scene: Scene, subject: SampledTrack, part: str | None) -> list[Points]:
    """Positions a reference such as `person`, `person.mask` or `person.hands` stands for."""
    if part == HANDS_CONCEPT:
        return hand_points(scene, subject)
    px = box_centres(subject.box) if part == "mask" else subject.foot_px
    return [Points(px=px, m=subject.foot_m)]


def wrists_px(subject: SampledTrack) -> FloatArray:
    """Reliable wrist keypoints per sample, shape (L, 2, 2); NaN where missing."""
    points = subject.track.points
    per_point = np.full((len(points), 2, 2), np.nan)
    for index, point in enumerate(points):
        if point.keypoints is None or len(point.keypoints) <= RIGHT_WRIST:
            continue
        for slot, kp_index in enumerate((LEFT_WRIST, RIGHT_WRIST)):
            keypoint = point.keypoints[kp_index]
            if keypoint.score >= MIN_KEYPOINT_SCORE:
                per_point[index, slot] = (keypoint.x, keypoint.y)
    out = per_point[np.maximum(subject.src, 0)]
    out[subject.src < 0] = np.nan
    return out


def reach_px(subject: SampledTrack) -> FloatArray:
    """`HAND_REACH_M` in pixels at each sample, from the subject's box height."""
    height = subject.box[:, 3] - subject.box[:, 1]
    return height * (HAND_REACH_M / PERSON_HEIGHT_M)


def near_wrist(points_px: FloatArray, wrists: FloatArray, reach: FloatArray) -> BoolArray:
    """Whether each point lies within `reach` pixels of either wrist (row-aligned arrays)."""
    hit = np.zeros(len(points_px), dtype=np.bool_)
    for slot in range(2):
        hit |= norm(points_px - wrists[:, slot]) <= reach
    return hit


def in_box(points_px: FloatArray, boxes: FloatArray) -> BoolArray:
    """Whether each point lies inside the row-aligned xyxy box."""
    x, y = points_px[:, 0], points_px[:, 1]
    return (boxes[:, 0] <= x) & (x <= boxes[:, 2]) & (boxes[:, 1] <= y) & (y <= boxes[:, 3])


def hand_points(scene: Scene, subject: SampledTrack) -> list[Points]:
    """Positions of the hands associated with a subject, plus its own wrist keypoints."""
    size = len(subject.present)
    wrists = wrists_px(subject)
    reach = reach_px(subject)
    out = [_project_points(scene, wrists[:, slot]) for slot in range(2)]
    for hand in scene.tracks_of(HANDS_CONCEPT):
        if hand.track_id == subject.track_id:
            continue
        span = overlap(subject, hand.start, hand.stop)
        if span is None:
            continue
        own, other = span
        centres = box_centres(hand.box[other])
        attached = in_box(centres, subject.box[own]) | near_wrist(centres, wrists[own], reach[own])
        px = np.full((size, 2), np.nan)
        px[own] = np.where(attached[:, None], centres, np.nan)
        out.append(_project_points(scene, px))
    return out


def _project_points(scene: Scene, px: FloatArray) -> Points:
    if scene.calibration is None:
        return Points(px=px, m=np.full_like(px, np.nan))
    return Points(px=px, m=project(scene.calibration, px))


def footprint_distance(
    scene: Scene, ground: FloatArray, start: int, other: SampledTrack, *, footprint: bool
) -> FloatArray:
    """Ground distance from row-aligned points (range starting at `start`) to another track.

    With a calibration and `footprint=True`, the other object's ground footprint is the
    projected bottom edge of its box; otherwise its foot point.

    Returns:
        Distances in metres over the points' range; NaN where the other track is absent.
    """
    out = np.full(len(ground), np.nan)
    lo, hi = max(start, other.start), min(start + len(ground), other.stop)
    if lo >= hi:
        return out
    own, theirs = slice(lo - start, hi - start), slice(lo - other.start, hi - other.start)
    if footprint and scene.calibration is not None:
        box = other.box[theirs]
        seg_a = project(scene.calibration, np.stack([box[:, 0], box[:, 3]], axis=1))
        seg_b = project(scene.calibration, np.stack([box[:, 2], box[:, 3]], axis=1))
        dist = point_segment_distance(ground[own], seg_a, seg_b)
    else:
        dist = norm(ground[own] - other.foot_m[theirs])
    out[own] = np.where(other.present[theirs], dist, np.nan)
    return out


def nearest_distance(
    scene: Scene,
    ground: FloatArray,
    start: int,
    others: tuple[SampledTrack, ...],
    *,
    footprint: bool,
    subject: SampledTrack | None = None,
) -> FloatArray:
    """Minimum ground distance from points to any of `others`.

    With `subject` (whose index range the points are aligned to), an other object is treated as
    absent at the samples where `Scene.ignored` says so: the subject rides it, or it rides the
    subject (operator exclusion).

    Returns:
        Metres per sample: +inf where no other object is present, NaN where the points have no
        ground position or only unmeasurable objects are present.
    """
    best = np.full(len(ground), np.inf)
    unmeasured = np.zeros(len(ground), dtype=np.bool_)
    for other in others:
        dist = footprint_distance(scene, ground, start, other, footprint=footprint)
        mask = None if subject is None else scene.ignored(subject, other)
        if mask is not None:
            dist = np.where(mask, np.inf, dist)
        best = np.fmin(best, dist)
        lo, hi = max(start, other.start), min(start + len(ground), other.stop)
        if lo < hi:
            present = np.zeros(len(ground), dtype=np.bool_)
            present[lo - start : hi - start] = other.present[lo - other.start : hi - other.start]
            unmeasured |= present & np.isnan(dist)
    best[np.isinf(best) & unmeasured] = np.nan
    best[np.isnan(ground[:, 0])] = np.nan
    return best


def zone_contains(
    scene: Scene, zone: ZoneShape, points: Points, start: int, subject: SampledTrack | None = None
) -> BoolArray:
    """Whether each position lies in a zone (image polygon, or metric buffer of a concept).

    A buffer around a vehicle ignores the vehicle at samples where `subject` is its operator.
    """
    if zone.polygon_px is not None:
        return points_in_polygon(points.px, zone.polygon_px)
    if not zone.in_view or zone.buffer_of is None:
        return np.zeros(len(points.px), dtype=np.bool_)
    others = scene.tracks_of(zone.buffer_of)
    dist = nearest_distance(scene, points.m, start, others, footprint=True, subject=subject)
    with np.errstate(invalid="ignore"):
        return dist <= zone.buffer_m
