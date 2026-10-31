"""Per-subject spatial predicates: distance, inside, overlap, without and moving."""

from __future__ import annotations

import numpy as np

from rulecam.contracts.rule_ir import DistanceM, Inside, Moving, Overlap, Without
from rulecam.rules.errors import RuleEvaluationError
from rulecam.rules.geometry import BoolArray, FloatArray, box_centres, box_iou, norm
from rulecam.rules.ir import parse_ref
from rulecam.rules.scene import (
    Scene,
    in_box,
    nearest_distance,
    overlap,
    reach_px,
    ref_points,
    wrists_px,
    zone_contains,
)
from rulecam.rules.signals import Signal, make_series
from rulecam.rules.timeline import SampledTrack

SPEED_MEDIAN_SAMPLES = 5
TOP_FRACTION = 0.25
LOWER_HALF = 0.5


def distance_m(node: DistanceM, scene: Scene) -> Signal:
    """Minimum ground distance from each `a` subject's foot point to any `b` object.

    The `b` side uses the object's ground footprint (projected bottom edge of its box) when a
    calibration is available, unless the reference is `<concept>.foot_point`.

    Raises:
        RuleEvaluationError: If neither `lt` nor `gt` is set.
    """
    if node.lt is None and node.gt is None:
        msg = "distance_m needs `lt` or `gt`"
        raise RuleEvaluationError(msg)
    a_concept, a_part = parse_ref(node.a)
    b_concept, b_part = parse_ref(node.b)
    others = scene.tracks_of(b_concept)
    series = {}
    for subject in scene.tracks_of(a_concept):
        candidates = tuple(o for o in others if o.track_id != subject.track_id)
        nears = [
            nearest_distance(
                scene,
                points.m,
                subject.start,
                candidates,
                footprint=b_part != "foot_point",
                subject=subject,
            )
            for points in ref_points(scene, subject, a_part)
        ]
        dist = np.fmin.reduce(np.stack(nears), axis=0)
        with np.errstate(invalid="ignore"):
            truth = _bounds(dist, node.lt, node.gt)
        series[subject.track_id] = make_series(subject.start, truth, dist, subject.present)
    return Signal(subject=a_concept, unit="m", series=series)


def _bounds(values: FloatArray, lt: float | None, gt: float | None) -> BoolArray:
    truth = ~np.isnan(values)
    if lt is not None:
        truth &= values < lt
    if gt is not None:
        truth &= values > gt
    return truth


def inside(node: Inside, scene: Scene) -> Signal:
    """Whether the subject's reference point (foot point by default) lies in the zone."""
    concept, part = parse_ref(node.object)
    zone = scene.zone(node.zone)
    series = {}
    for subject in scene.tracks_of(concept):
        hit = np.zeros(len(subject.present), dtype=np.bool_)
        for points in ref_points(scene, subject, part):
            hit |= zone_contains(scene, zone, points, subject.start, subject)
        series[subject.track_id] = make_series(
            subject.start, hit, hit.astype(np.float64), subject.present
        )
    return Signal(subject=concept, unit=None, series=series)


def overlap_iou(node: Overlap, scene: Scene) -> Signal:
    """Largest box IoU between each `a` subject and any `b` object."""
    a_concept, _ = parse_ref(node.a)
    b_concept, _ = parse_ref(node.b)
    others = scene.tracks_of(b_concept)
    series = {}
    for subject in scene.tracks_of(a_concept):
        best = np.zeros(len(subject.present))
        for other in others:
            span = overlap(subject, other.start, other.stop)
            if other.track_id == subject.track_id or span is None:
                continue
            own, theirs = span
            iou = np.where(other.present[theirs], box_iou(subject.box[own], other.box[theirs]), 0)
            best[own] = np.fmax(best[own], iou)
        truth = (best > 0) & (best >= node.min_iou)
        series[subject.track_id] = make_series(subject.start, truth, best, subject.present)
    return Signal(subject=a_concept, unit="iou", series=series)


def without(node: Without, scene: Scene) -> Signal:
    """True when the subject has no `item` whose box centre lies in `region` of the subject.

    Regions: `full` is the subject box; `top_25pct` its top quarter; `hands` is within
    `HAND_REACH_M` of a wrist keypoint, or the lower half of the box when there is no pose.
    The value is the number of items found in the region.
    """
    subject_concept, _ = parse_ref(node.subject)
    item_concept, _ = parse_ref(node.item)
    items = scene.tracks_of(item_concept)
    series = {}
    for subject in scene.tracks_of(subject_concept):
        count = np.zeros(len(subject.present))
        for item in items:
            span = overlap(subject, item.start, item.stop)
            if item.track_id == subject.track_id or span is None:
                continue
            own, theirs = span
            centres = box_centres(item.box[theirs])
            hit = _in_region(node.region, subject, own, centres) & item.present[theirs]
            count[own] += hit
        series[subject.track_id] = make_series(subject.start, count == 0, count, subject.present)
    return Signal(subject=subject_concept, unit="count", series=series)


def _in_region(region: str, subject: SampledTrack, own: slice, centres: FloatArray) -> BoolArray:
    box = subject.box[own]
    if region == "full":
        return in_box(centres, box)
    height = box[:, 3] - box[:, 1]
    if region == "top_25pct":
        top = box.copy()
        top[:, 3] = box[:, 1] + TOP_FRACTION * height
        return in_box(centres, top)
    wrists = wrists_px(subject)[own]
    has_pose = ~np.isnan(wrists[:, :, 0]).all(axis=1)
    near = np.zeros(len(centres), dtype=np.bool_)
    for slot in range(2):
        near |= norm(centres - wrists[:, slot]) <= reach_px(subject)[own]
    lower = box.copy()
    lower[:, 1] = box[:, 1] + LOWER_HALF * height
    return np.where(has_pose, near, in_box(centres, lower))


def moving(node: Moving, scene: Scene) -> Signal:
    """Smoothed ground speed (centred 5-sample median) above `speed_mps_gt`."""
    concept, _ = parse_ref(node.object)
    hz = scene.timeline.hz
    series = {}
    for subject in scene.tracks_of(concept):
        speed = np.full(len(subject.present), np.nan)
        if len(speed) > 1:
            speed[1:] = norm(np.diff(subject.foot_m, axis=0)) * hz
        smooth = rolling_median(speed, SPEED_MEDIAN_SAMPLES)
        with np.errstate(invalid="ignore"):
            truth = smooth > node.speed_mps_gt
        series[subject.track_id] = make_series(subject.start, truth, smooth, subject.present)
    return Signal(subject=concept, unit="m/s", series=series)


def rolling_median(values: FloatArray, window: int) -> FloatArray:
    """Centred rolling median ignoring NaN; NaN where the window has no finite value."""
    half = window // 2
    padded = np.concatenate([np.full(half, np.nan), values, np.full(half, np.nan)])
    windows = np.lib.stride_tricks.sliding_window_view(padded, window)
    ordered = np.sort(windows, axis=1)
    valid = (~np.isnan(windows)).sum(axis=1)
    lo = np.maximum((valid - 1) // 2, 0)
    hi = np.maximum(valid // 2, 0)
    rows = np.arange(len(values))
    median: FloatArray = (ordered[rows, lo] + ordered[rows, hi]) / 2.0
    median[valid == 0] = np.nan
    return median
