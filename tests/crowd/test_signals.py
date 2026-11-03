"""Crowd signals: tiles, merged boxes, ground areas, motion, pressure and trends."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from rulecam.contracts import Calibration
from rulecam.crowd import signals


def top_down(scale_m_per_px: float = 0.01) -> Calibration:
    """A camera looking straight down: every pixel is `scale` metres on the ground."""
    return Calibration(
        camera_id="gate",
        homography=((scale_m_per_px, 0.0, 0.0), (0.0, scale_m_per_px, 0.0), (0.0, 0.0, 1.0)),
        method="clicks4",
        err_m=0.1,
    )


def test_tiles_cover_the_frame_and_overlap() -> None:
    tiles = signals.tile_boxes(1000, 600, 2, overlap=0.2)
    assert len(tiles) == 4
    assert tiles[0][:2] == (0, 0)
    assert max(t[2] for t in tiles) == 1000
    assert max(t[3] for t in tiles) == 600
    assert tiles[1][0] < tiles[0][2]  # neighbours overlap
    assert signals.tile_boxes(640, 480, 1) == [(0, 0, 640, 480)]


@pytest.mark.parametrize(("grid", "overlap"), [(0, 0.1), (2, 0.5), (2, -0.1)])
def test_bad_tilings_are_refused(grid: int, overlap: float) -> None:
    with pytest.raises(ValueError, match="grid"):
        signals.tile_boxes(100, 100, grid, overlap)


def test_merging_drops_duplicates_and_parts_cut_by_a_tile_edge() -> None:
    whole = (100.0, 100.0, 140.0, 200.0, 0.9)
    same = (101.0, 101.0, 141.0, 199.0, 0.8)
    part = (100.0, 150.0, 140.0, 200.0, 0.7)  # the lower half, from the next tile
    other = (300.0, 100.0, 340.0, 200.0, 0.6)
    assert signals.merge_boxes([part, same, whole, other]) == [whole, other]


def test_feet_are_the_bottom_centres() -> None:
    feet = signals.foot_points([(10.0, 20.0, 30.0, 60.0, 1.0)])
    assert feet.tolist() == [[20.0, 60.0]]
    assert signals.foot_points([]).shape == (0, 2)


def test_the_area_map_sums_to_the_ground_area() -> None:
    areas = signals.area_map(signals.homography(top_down()), 400, 300)
    assert areas.sum() == pytest.approx(4.0 * 3.0)
    h = signals.homography(top_down())
    left = signals.rect_polygon((0, 0, 200, 300))
    assert signals.region_area(h, areas, left, 400, 300) == pytest.approx(6.0)
    blocks = signals.polygon_blocks([(0, 0), (200, 0), (200, 300), (0, 300)], 400, 300)
    assert areas[blocks].sum() == pytest.approx(6.0, rel=0.05)


def test_ground_above_the_horizon_has_no_area() -> None:
    # w = y - 100: rows above 100 are sky.
    h = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 50.0], [0.0, 1.0, -100.0]])
    ground, valid = signals.to_ground(h, np.array([[10.0, 50.0], [10.0, 250.0]]))
    assert valid.tolist() == [False, True]
    assert math.isnan(ground[0, 0])
    areas = signals.area_map(h, 200, 300)
    assert areas[: 100 // signals.AREA_STEP_PX].sum() == 0.0
    assert areas[-1].sum() > 0.0
    across = signals.rect_polygon((0, 50, 200, 300))  # crosses the horizon: blocks are summed
    below = signals.rect_polygon((0, 100, 200, 300))
    assert signals.region_area(h, areas, across, 200, 300) == pytest.approx(
        signals.region_area(h, areas, below, 200, 300), rel=0.05
    )


def test_motion_of_a_crowd_walking_one_way() -> None:
    moved = signals.motion(np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0], [np.nan, np.nan]]))
    assert moved.moving == 3
    assert moved.speed == pytest.approx(1.0)
    assert moved.velocity_var == pytest.approx(0.0)
    assert moved.coherence == pytest.approx(1.0)
    assert moved.counterflow == 0.0


def test_motion_of_people_pushing_against_each_other() -> None:
    moved = signals.motion(np.array([[1.0, 0.0], [-0.8, 0.0], [1.2, 0.0], [-1.0, 0.0]]))
    assert moved.coherence is not None
    assert moved.coherence < 0.2
    assert moved.counterflow == pytest.approx(0.5)
    assert moved.velocity_var == pytest.approx(0.99, rel=0.05)
    assert signals.pressure(5.0, 0.2) == pytest.approx(1.0)


def test_nothing_measured_gives_nones() -> None:
    moved = signals.motion(np.zeros((0, 2)))
    assert (moved.moving, moved.speed, moved.coherence) == (0, None, None)
    assert signals.pressure(None, 0.1) is None
    assert signals.density(3, None) is None
    assert signals.density(3, 1.0) is None  # too small an area to call a density
    assert signals.density(10, 4.0) == 2.5


def test_flow_finds_a_shift_and_the_ground_velocity() -> None:
    rng = np.random.default_rng(0)
    texture = cv2.GaussianBlur(rng.integers(0, 255, (240, 320), dtype=np.uint8), (5, 5), 0)
    before = np.ascontiguousarray(texture[:, 10:250])
    after = np.ascontiguousarray(texture[:, 4:244])  # the scene moved 6 px to the right
    flow = signals.optical_flow(before, after)
    box = (80.0, 80.0, 160.0, 160.0, 1.0)
    moved = signals.box_flow(flow, [box])
    assert moved[0, 0] == pytest.approx(6.0, abs=1.0)
    assert abs(moved[0, 1]) < 1.0
    velocity = signals.ground_velocities(
        signals.homography(top_down()), signals.foot_points([box]), moved, 0.5
    )
    assert velocity[0, 0] == pytest.approx(0.12, abs=0.02)  # 6 px x 1 cm in 0.5 s


def test_cells_and_points_in_them() -> None:
    rects = signals.cell_rects(400, 300, 4, 3)
    assert len(rects) == 12
    assert rects[0] == (0, 0, 100, 100)
    points = np.array([[50.0, 50.0], [150.0, 50.0], [99.9, 99.9]])
    assert signals.in_rect(points, rects[0]).tolist() == [True, False, True]
    assert signals.inside([(0, 0), (100, 0), (100, 100), (0, 100)], points).tolist() == [
        True,
        False,
        True,
    ]


def test_a_rising_series_is_fitted_and_projected() -> None:
    fit = signals.trend([0, 30, 60, 90], [1.0, 1.5, 2.0, 2.5])
    assert fit is not None
    assert fit.per_min == pytest.approx(1.0)
    assert fit.r2 == pytest.approx(1.0)
    assert fit.in_2_min == pytest.approx(4.5)
    assert fit.in_5_min == pytest.approx(7.5)
    assert signals.trend([0, 1], [1.0, 2.0]) is None
    assert signals.trend([5, 5, 5], [1.0, 2.0, 3.0]) is None


def test_the_homography_is_signed_so_the_floor_is_in_front() -> None:
    flipped = Calibration(
        camera_id="gate",
        homography=((-0.01, 0.0, 0.0), (0.0, -0.01, 0.0), (0.0, 0.0, -1.0)),
        method="clicks4",
        err_m=0.1,
    )
    h = signals.homography(flipped, (200.0, 299.0))
    ground, valid = signals.to_ground(h, np.array([[100.0, 100.0]]))
    assert valid.tolist() == [True]
    assert ground[0].tolist() == pytest.approx([1.0, 1.0])
