"""Tests for ground-plane calibration and zones (task P1-05).

Synthetic scenes come from a pinhole camera looking down at the floor, so every test has an
exact ground truth under real perspective.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray
from shapely.geometry import Point, Polygon

from rulecam.contracts import Calibration, Zone
from rulecam.contracts.core import BoxXYXY, PointXY
from rulecam.perception.calibration import (
    DOOR_HEIGHT_M,
    REFERENCE_SIZES_M,
    ReferenceKind,
    ZoneView,
    buffer_zone,
    calibrate_clicks,
    calibrate_reference,
    door_scale_ok,
    door_scale_ratio,
    ground_distance_m,
    m_to_px,
    polygon_zone,
    px_to_m,
    zone_in_view,
)
from rulecam.perception.errors import CalibrationError, ZoneError

WIDTH, HEIGHT = 1280, 720


def camera_matrix(
    *, height_m: float = 3.0, pitch_deg: float = 35.0, focal_px: float = 800.0
) -> NDArray[np.float64]:
    """3x4 projection of a camera at (0, 0, height_m) looking along +Y, pitched down."""
    theta = math.radians(pitch_deg)
    sin, cos = math.sin(theta), math.cos(theta)
    intrinsics = np.array([[focal_px, 0, WIDTH / 2], [0, focal_px, HEIGHT / 2], [0, 0, 1]])
    rotation = np.array([[1, 0, 0], [0, -sin, -cos], [0, cos, -sin]])
    translation = -rotation @ np.array([0.0, 0.0, height_m])
    projection: NDArray[np.float64] = intrinsics @ np.column_stack([rotation, translation])
    return projection


def ground_to_image(projection: NDArray[np.float64]) -> NDArray[np.float64]:
    """Homography from ground metres (Z = 0) to pixels."""
    return projection[:, [0, 1, 3]]


def project(matrix: NDArray[np.float64], pts: NDArray[np.float64]) -> NDArray[np.float64]:
    """Apply a 3x3 homography or 3x4 projection to rows of `pts` (2-D or 3-D)."""
    lifted = np.column_stack([pts, np.ones(len(pts))])
    mapped = lifted @ matrix.T
    return mapped[:, :2] / mapped[:, 2:]


def true_calibration(projection: NDArray[np.float64], camera_id: str = "cam1") -> Calibration:
    """The exact image -> ground calibration of a synthetic camera."""
    inverse = np.linalg.inv(ground_to_image(projection))
    rows = tuple(tuple(float(v) for v in row) for row in inverse / inverse[2, 2])
    return Calibration(camera_id=camera_id, homography=rows, method="clicks4", err_m=0.0)


def rectangle_m(
    centre: tuple[float, float], size: tuple[float, float], yaw_deg: float = 0.0
) -> NDArray[np.float64]:
    """Corners of a ground rectangle centred at `centre`, rotated by `yaw_deg`."""
    half_w, half_h = size[0] / 2, size[1] / 2
    local = np.array([[-half_w, -half_h], [half_w, -half_h], [half_w, half_h], [-half_w, half_h]])
    yaw = math.radians(yaw_deg)
    rot = np.array([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
    corners: NDArray[np.float64] = local @ rot.T + np.asarray(centre)
    return corners


def render(pixels: NDArray[np.float64]) -> NDArray[np.uint8]:
    """Rasterise a convex pixel polygon into a binary mask with sub-pixel vertices."""
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    shift = 4
    cv2.fillPoly(mask, [np.round(pixels * 2**shift).astype(np.int32)], 1, shift=shift)
    return mask


def render_ground(
    projection: NDArray[np.float64], corners_m: NDArray[np.float64]
) -> NDArray[np.uint8]:
    return render(project(ground_to_image(projection), corners_m))


def as_tuples(pts: NDArray[np.float64]) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in pts]


def ground_area(cal: Calibration, zone: Zone) -> float:
    assert zone.polygon_px is not None
    return float(Polygon(px_to_m(cal, zone.polygon_px)).area)


# --- calibrate_clicks ----------------------------------------------------------------------


TAPE_MARKS_M = np.array(
    [[-2.0, 3.0], [2.0, 3.0], [2.5, 8.0], [-2.5, 8.0], [0.0, 5.0], [1.0, 6.5], [-1.5, 4.0]]
)


def test_known_homography_round_trips_within_1e6() -> None:
    projection = camera_matrix()
    clicks = project(ground_to_image(projection), TAPE_MARKS_M[:4])
    cal = calibrate_clicks("cam1", as_tuples(clicks), as_tuples(TAPE_MARKS_M[:4]))

    assert cal.method == "clicks4"
    assert cal.err_m < 1e-6
    np.testing.assert_allclose(px_to_m(cal, as_tuples(clicks)), TAPE_MARKS_M[:4], atol=1e-6)
    grid = np.array([[x, y] for x in range(0, WIDTH, 160) for y in range(0, HEIGHT, 90)], float)
    np.testing.assert_allclose(m_to_px(cal, px_to_m(cal, as_tuples(grid))), grid, atol=1e-6)


@settings(max_examples=50, deadline=None)
@given(x=st.floats(min_value=0, max_value=WIDTH), y=st.floats(min_value=0, max_value=HEIGHT))
def test_pixel_round_trip_property(x: float, y: float) -> None:
    cal = true_calibration(camera_matrix())
    (back,) = m_to_px(cal, px_to_m(cal, [(x, y)]))
    assert back == pytest.approx((x, y), abs=1e-6)


def test_distance_between_known_ground_points_within_1cm() -> None:
    projection = camera_matrix()
    to_image = ground_to_image(projection)
    clicks = project(to_image, TAPE_MARKS_M)
    cal = calibrate_clicks("cam1", as_tuples(clicks), as_tuples(TAPE_MARKS_M))

    a_px, b_px = project(to_image, np.array([[-1.0, 5.0], [1.0, 5.0]]))
    assert ground_distance_m(cal, tuple(a_px), tuple(b_px)) == pytest.approx(2.0, abs=0.01)


def test_ransac_drops_a_misclick() -> None:
    projection = camera_matrix()
    to_image = ground_to_image(projection)
    clicks = project(to_image, TAPE_MARKS_M)
    clicks[4] += (120.0, -60.0)  # one wild click
    cal = calibrate_clicks("cam1", as_tuples(clicks), as_tuples(TAPE_MARKS_M))

    assert cal.err_m < 1e-6
    a_px, b_px = project(to_image, np.array([[0.0, 4.0], [0.0, 6.0]]))
    assert ground_distance_m(cal, tuple(a_px), tuple(b_px)) == pytest.approx(2.0, abs=0.01)


def test_err_m_is_rms_ground_reprojection_error_for_noisy_clicks() -> None:
    projection = camera_matrix()
    clicks = project(ground_to_image(projection), TAPE_MARKS_M)
    noisy = clicks + np.random.default_rng(7).normal(0.0, 1.0, clicks.shape)
    cal = calibrate_clicks("cam1", as_tuples(noisy), as_tuples(TAPE_MARKS_M))

    residuals = np.asarray(px_to_m(cal, as_tuples(noisy))) - TAPE_MARKS_M
    rms = math.sqrt(float(np.mean(np.sum(residuals**2, axis=1))))
    assert 0 < cal.err_m < 0.05
    assert cal.err_m == pytest.approx(rms, rel=1e-6)


@pytest.mark.parametrize(
    ("img", "world"),
    [
        pytest.param(
            [(0, 0), (100, 100), (200, 200), (300, 300)],
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            id="collinear-image",
        ),
        pytest.param(
            [(0, 0), (100, 0), (100, 100), (0, 100)],
            [(0, 0), (1, 0), (2, 0), (3, 0)],
            id="collinear-world",
        ),
        pytest.param(
            [(0, 0), (100, 0), (200, 0), (100, 100)],
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            id="three-of-four-collinear",
        ),
        pytest.param(
            [(0, 0), (100, 0), (200, 0), (300, 0), (150, 100)],
            [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)],
            id="four-of-five-collinear",
        ),
        pytest.param(
            [(0, 0), (100, 0.05), (200, -0.05), (300, 0.0)],
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            id="nearly-collinear",
        ),
    ],
)
def test_collinear_points_raise(img: list[PointXY], world: list[PointXY]) -> None:
    with pytest.raises(CalibrationError, match="collinear"):
        calibrate_clicks("cam1", img, world)


@pytest.mark.parametrize(
    ("img", "world", "message"),
    [
        ([(0, 0), (1, 0), (0, 1)], [(0, 0), (1, 0), (0, 1)], "at least 4"),
        ([(0, 0), (1, 0), (1, 1), (0, 1)], [(0, 0), (1, 0), (1, 1)], "3 world points"),
        ([(0, 0), (1, 0), (1, 1), (0, math.nan)], [(0, 0), (1, 0), (1, 1), (0, 1)], "finite"),
        ([], [], "non-empty"),
    ],
)
def test_bad_click_input_raises(img: list[PointXY], world: list[PointXY], message: str) -> None:
    with pytest.raises(CalibrationError, match=message):
        calibrate_clicks("cam1", img, world)


# --- calibrate_reference -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "pitch_deg", "centre", "yaw_deg"),
    [
        ("pallet_48x40in", 90.0, (0.3, 0.2), 20.0),  # camera looking straight down
        ("pallet_48x40in", 40.0, (0.5, 5.0), 15.0),  # oblique: footprint is a quadrilateral
        ("pallet_48x40in", 40.0, (-0.8, 4.0), -35.0),
        ("floor_square_1m", 50.0, (0.0, 4.0), 30.0),
        ("floor_square_1m", 40.0, (0.6, 4.5), 100.0),  # a square has no long side to confuse
    ],
)
def test_reference_object_gives_expected_scale_within_2pct(
    kind: ReferenceKind, pitch_deg: float, centre: tuple[float, float], yaw_deg: float
) -> None:
    projection = camera_matrix(height_m=3.0 if pitch_deg < 90 else 4.0, pitch_deg=pitch_deg)
    to_image = ground_to_image(projection)
    mask = render_ground(projection, rectangle_m(centre, REFERENCE_SIZES_M[kind], yaw_deg))
    cal = calibrate_reference("cam2", mask, kind)

    assert cal.method == "reference_object"
    assert cal.reference == kind
    assert cal.err_m < 0.02
    cx, cy = centre
    for a_m, b_m in [((cx - 1, cy), (cx + 1, cy)), ((cx, cy - 1), (cx, cy + 1))]:
        a_px, b_px = project(to_image, np.array([a_m, b_m]))
        assert ground_distance_m(cal, tuple(a_px), tuple(b_px)) == pytest.approx(2.0, rel=0.02)


def test_reference_err_m_grows_when_the_mask_is_not_the_rectangle() -> None:
    projection = camera_matrix(pitch_deg=40.0)
    clean = render_ground(projection, rectangle_m((0.0, 5.0), (1.219, 1.016)))
    blob = render_ground(projection, rectangle_m((0.9, 5.0), (0.4, 0.4)))
    noisy = calibrate_reference("cam2", clean | blob, "pallet_48x40in")
    assert noisy.err_m > 5 * calibrate_reference("cam2", clean, "pallet_48x40in").err_m


def test_reference_falls_back_to_min_area_rectangle_for_round_masks() -> None:
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    cv2.circle(mask, (640, 360), 100, 1, -1)
    cal = calibrate_reference("cam2", mask, "floor_square_1m")
    assert cal.err_m > 0.03  # a disc is a poor 1 m square, and err_m says so


@pytest.mark.parametrize(
    ("mask", "kind", "message"),
    [
        (np.zeros((10, 10), np.uint8), "pallet_48x40in", "empty"),
        (np.ones((4, 4, 3), np.uint8), "pallet_48x40in", "2-D"),
        (np.ones((10, 10), np.uint8), "cone_28in", "unknown reference kind"),
    ],
)
def test_bad_reference_input_raises(mask: NDArray[np.uint8], kind: str, message: str) -> None:
    with pytest.raises(CalibrationError, match=message):
        calibrate_reference("cam2", mask, kind)  # type: ignore[arg-type]


def test_line_mask_raises_collinear() -> None:
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[300, 100:900] = 1
    with pytest.raises(CalibrationError, match="collinear"):
        calibrate_reference("cam2", mask, "floor_square_1m")


# --- px_to_m / m_to_px errors --------------------------------------------------------------


def test_malformed_homography_raises() -> None:
    cal = Calibration(
        camera_id="cam1", homography=((1, 0, 0), (0, 1, 0)), method="clicks4", err_m=0
    )
    with pytest.raises(CalibrationError, match="3x3"):
        px_to_m(cal, [(0.0, 0.0)])


def test_singular_homography_raises() -> None:
    cal = Calibration(
        camera_id="cam1", homography=((1, 0, 0), (2, 0, 0), (0, 0, 1)), method="clicks4", err_m=0
    )
    with pytest.raises(CalibrationError, match="singular"):
        m_to_px(cal, [(0.0, 0.0)])


def test_pixel_on_the_horizon_raises() -> None:
    projection = camera_matrix(height_m=1.5, pitch_deg=5.0)
    cal = true_calibration(projection)
    h31, h32, h33 = cal.homography[2]
    horizon_y = -(h31 * 640 + h33) / h32
    with pytest.raises(CalibrationError, match="horizon"):
        px_to_m(cal, [(640.0, horizon_y)])


# --- door sanity check ---------------------------------------------------------------------


def door_box(projection: NDArray[np.float64], distance_m: float) -> BoxXYXY:
    corners = np.array(
        [[x, distance_m, z] for x in (-0.45, 0.45) for z in (0.0, DOOR_HEIGHT_M)], float
    )
    pixels = project(projection, corners)
    (x1, y1), (x2, y2) = pixels.min(axis=0), pixels.max(axis=0)
    return (float(x1), float(y1), float(x2), float(y2))


def test_door_height_agrees_with_a_good_calibration() -> None:
    projection = camera_matrix(height_m=2.5, pitch_deg=15.0)
    box_px = door_box(projection, 6.0)
    cal = true_calibration(projection)
    assert door_scale_ratio(cal, box_px) == pytest.approx(1.0, abs=0.1)
    assert door_scale_ok(cal, box_px)


def test_door_height_flags_a_calibration_off_by_2x() -> None:
    projection = camera_matrix(height_m=2.5, pitch_deg=15.0)
    good = np.asarray(true_calibration(projection).homography)
    doubled = np.diag([2.0, 2.0, 1.0]) @ good
    cal = Calibration(
        camera_id="cam1",
        homography=tuple(tuple(float(v) for v in row) for row in doubled),
        method="clicks4",
        err_m=0,
    )
    box_px = door_box(projection, 6.0)
    assert door_scale_ratio(cal, box_px) == pytest.approx(2.0, rel=0.1)
    assert not door_scale_ok(cal, box_px)


def test_door_box_without_area_raises() -> None:
    cal = true_calibration(camera_matrix())
    with pytest.raises(CalibrationError, match="positive"):
        door_scale_ratio(cal, (100.0, 100.0, 100.0, 300.0))


# --- zones ---------------------------------------------------------------------------------


def test_buffer_zone_area_matches_rounded_rectangle_within_3pct() -> None:
    projection = camera_matrix()
    cal = true_calibration(projection)
    w, h, b = 2.0, 1.0, 0.5
    mask = render_ground(projection, rectangle_m((0.5, 5.0), (w, h), 25.0))
    zone = buffer_zone("keep_out", cal, mask, "pallet", b)

    expected = (w + 2 * b) * (h + 2 * b) - (4 - math.pi) * b**2
    assert ground_area(cal, zone) == pytest.approx(expected, rel=0.03)
    assert zone.kind == "buffer"
    assert zone.camera_id == "cam1"
    assert zone.buffer_of_concept == "pallet"
    assert zone.buffer_m == b


def test_base_footprint_buffers_the_bottom_of_an_upright_door() -> None:
    projection = camera_matrix()
    cal = true_calibration(projection)
    top = DOOR_HEIGHT_M
    door = np.array([[-0.45, 6.0, 0.0], [0.45, 6.0, 0.0], [0.45, 6.0, top], [-0.45, 6.0, top]])
    mask = render(project(projection, door))
    zone = buffer_zone("egress", cal, mask, "fire exit door", 1.0, footprint="base")

    assert zone.polygon_px is not None
    ground = Polygon(px_to_m(cal, zone.polygon_px))
    assert ground.contains(Point(0.0, 6.0))
    assert ground.contains(Point(0.0, 5.1))  # 0.9 m in front of the door
    assert not ground.contains(Point(0.0, 4.5))  # 1.5 m in front
    assert 0.9 * 2 + math.pi < ground.area < 6.0


def test_full_footprint_above_the_horizon_raises() -> None:
    projection = camera_matrix(height_m=1.5, pitch_deg=5.0)
    cal = true_calibration(projection)
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    mask[200:700, 600:700] = 1  # horizon is near y = 290
    with pytest.raises(CalibrationError, match="horizon"):
        buffer_zone("z", cal, mask, "rack", 1.0)
    assert buffer_zone("z", cal, mask, "rack", 1.0, footprint="base").kind == "buffer"


def test_buffer_behind_the_camera_raises() -> None:
    projection = camera_matrix()
    cal = true_calibration(projection)
    mask = render_ground(projection, rectangle_m((0.0, 2.2), (0.6, 0.3)))
    with pytest.raises(ZoneError, match="behind the camera"):
        buffer_zone("z", cal, mask, "cone", 6.0)


@pytest.mark.parametrize(
    ("mask", "buffer_m", "message"),
    [
        (np.ones((HEIGHT, WIDTH), np.uint8), 0.0, "> 0"),
        (np.ones((HEIGHT, WIDTH), np.uint8), math.nan, "> 0"),
        (np.zeros((HEIGHT, WIDTH), np.uint8), 1.0, "empty"),
    ],
)
def test_bad_buffer_input_raises(mask: NDArray[np.uint8], buffer_m: float, message: str) -> None:
    cal = true_calibration(camera_matrix())
    with pytest.raises(ZoneError, match=message):
        buffer_zone("z", cal, mask, "pallet", buffer_m)


def test_polygon_zone_from_clicks() -> None:
    zone = polygon_zone("line_entry", "cam1", [(100, 100), (300, 100), (300, 250), (100, 250)])
    assert zone.kind == "polygon"
    assert zone.polygon_px == ((100.0, 100.0), (300.0, 100.0), (300.0, 250.0), (100.0, 250.0))


@pytest.mark.parametrize(
    ("points", "message"),
    [
        ([(0, 0), (10, 10)], "3\\+ vertices"),
        ([(0, 0), (10, 10), (20, 20)], "no area"),
        ([(0, 0), (10, 10), (10, 0), (0, 10)], "self-intersecting"),
        ([(0, 0), (10, 0), (math.inf, 10)], "finite"),
    ],
)
def test_bad_polygon_zone_raises(points: list[PointXY], message: str) -> None:
    with pytest.raises(ZoneError, match=message):
        polygon_zone("z", "cam1", points)


@pytest.mark.parametrize(
    ("points", "state", "fraction"),
    [
        ([(100, 100), (200, 100), (200, 200), (100, 200)], "inside", 1.0),
        ([(0, 0), (WIDTH, 0), (WIDTH, HEIGHT), (0, HEIGHT)], "inside", 1.0),
        ([(-50, 100), (50, 100), (50, 200), (-50, 200)], "partial", 0.5),
        ([(1200, 650), (1400, 650), (1400, 850), (1200, 850)], "partial", 0.4 * 0.35),
        ([(2000, 100), (2100, 100), (2100, 200), (2000, 200)], "outside", 0.0),
        ([(WIDTH, 100), (WIDTH + 50, 100), (WIDTH + 50, 200), (WIDTH, 200)], "outside", 0.0),
    ],
)
def test_zone_in_view(points: list[PointXY], state: str, fraction: float) -> None:
    view = zone_in_view(polygon_zone("z", "cam1", points), WIDTH, HEIGHT)
    assert isinstance(view, ZoneView)
    assert view.state == state
    assert view.visible_fraction == pytest.approx(fraction)


def test_zone_in_view_for_a_buffer_zone() -> None:
    projection = camera_matrix()
    cal = true_calibration(projection)
    mask = render_ground(projection, rectangle_m((0.0, 5.0), (1.0, 1.0)))
    assert zone_in_view(buffer_zone("z", cal, mask, "pallet", 0.5), WIDTH, HEIGHT).state == (
        "inside"
    )


def test_zone_in_view_rejects_bad_input() -> None:
    unbuilt = Zone(zone_id="egress", camera_id="cam1", kind="buffer", buffer_of_concept="door")
    with pytest.raises(ZoneError, match="no pixel polygon"):
        zone_in_view(unbuilt, WIDTH, HEIGHT)
    square = polygon_zone("z", "cam1", [(0, 0), (10, 0), (10, 10), (0, 10)])
    with pytest.raises(ZoneError, match="positive"):
        zone_in_view(square, 0, HEIGHT)
