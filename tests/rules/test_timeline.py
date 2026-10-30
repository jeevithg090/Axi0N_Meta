"""Resampling onto the common timeline (P1-06 step 2) and the geometry helpers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from rulecam.contracts import Calibration, Track
from rulecam.rules.geometry import box_iou, point_segment_distance, points_in_polygon, project
from rulecam.rules.timeline import HOLD_S, SampledTrack, Timeline, prepare_tracks, resample
from tests.rules.helpers import CAL, at, walk_track

TL = Timeline.build(at(0), at(10), 10.0)
CONCEPTS = frozenset({"person"})


def sampled(track: Track, calibration: Calibration | None = None) -> SampledTrack:
    (prepared,) = prepare_tracks([track], at(0), at(10), CONCEPTS)
    return resample(prepared, TL, calibration)


def test_timeline_grid() -> None:
    assert TL.n == 101
    assert TL.ts_at(15) == at(1.5)
    assert TL.samples_for(20.0) == 200
    assert TL.samples_for(19.95) == 200
    assert TL.samples_within(3.0) == 30
    assert Timeline.build(at(0), at(0), 10.0).n == 1


def test_points_are_interpolated_between_close_samples() -> None:
    track = walk_track("cam1:person:1", [(0.0, 0.0), (1.0, 2.0)], hz=5.0)  # 0.2 s apart
    s = sampled(track)
    assert s.start == 0
    assert s.foot_m[1].tolist() == pytest.approx([0.5, 1.0])  # t = 0.1 s, halfway
    assert s.present[:2].all()


def test_last_value_is_held_for_half_a_second_then_absent() -> None:
    track = walk_track("cam1:person:1", [(1.0, 1.0)])
    s = sampled(track)
    held = round(HOLD_S * 10)
    assert s.present.tolist() == [True] * (held + 1)
    assert s.stop == held + 1


def test_long_gaps_are_held_then_absent() -> None:
    positions = [(0.0, 0.0)] + [None] * 19 + [(2.0, 0.0)]  # 2 s dropout
    s = sampled(walk_track("cam1:person:1", positions))
    assert s.present[:6].all()
    assert not s.present[6:20].any()
    assert s.present[20]
    assert np.isnan(s.foot_m[10]).all()
    assert s.point_at(10) is None
    point = s.point_at(20)
    assert point is not None
    assert point.foot_m == (2.0, 0.0)


def test_missing_foot_m_is_projected_with_calibration() -> None:
    track = walk_track("cam1:person:1", [(1.5, 2.5)] * 3, with_foot_m=False)
    assert np.isnan(sampled(track).foot_m).all()
    assert sampled(track, CAL).foot_m[0].tolist() == pytest.approx([1.5, 2.5])


def test_prepare_merges_duplicates_and_sorts() -> None:
    a = walk_track("cam1:person:1", [(0.0, 0.0)] * 3)
    b = walk_track("cam1:person:1", [(1.0, 0.0)] * 3, start_s=0.3)
    c = walk_track("cam1:person:0", [(0.0, 0.0)])
    dog = walk_track("cam1:dog:1", [(0.0, 0.0)], concept="dog")
    late = walk_track("cam1:person:9", [(0.0, 0.0)], start_s=20.0)
    tracks = prepare_tracks([b, dog, a, c, late, a], at(0), at(10), CONCEPTS)
    assert [t.track_id for t in tracks] == ["cam1:person:0", "cam1:person:1"]
    assert len(tracks[1].points) == 6
    assert [p.ts for p in tracks[1].points] == sorted(p.ts for p in tracks[1].points)


def test_conflicting_points_at_one_timestamp_collapse_canonically() -> None:
    a = walk_track("cam1:person:1", [(0.0, 0.0)])
    b = walk_track("cam1:person:1", [(9.0, 0.0)])
    first = prepare_tracks([a, b], at(0), at(10), CONCEPTS)
    second = prepare_tracks([b, a], at(0), at(10), CONCEPTS)
    assert first == second
    assert len(first[0].points) == 1


def test_geometry_helpers() -> None:
    square = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
    pts = np.array([[5, 5], [15, 5], [math.nan, math.nan]], dtype=float)
    assert points_in_polygon(pts, square).tolist() == [True, False, False]
    boxes = np.array([[0, 0, 10, 10], [0, 0, 0, 0]], dtype=float)
    assert box_iou(boxes, boxes).tolist() == [1.0, 0.0]
    dist = point_segment_distance(
        np.array([[0.0, 1.0], [3.0, 0.0]]), np.array([[-1.0, 0.0]] * 2), np.array([[1.0, 0.0]] * 2)
    )
    assert dist.tolist() == [1.0, 2.0]
    assert project(CAL, np.array([[100.0, 250.0]])).tolist() == [[1.0, 2.5]]
