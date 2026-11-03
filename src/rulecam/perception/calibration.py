"""Ground-plane calibration and zones (task P1-05; V2 Pillar 1, "Metric calibration").

A `Calibration` holds a homography H that maps image pixels to metres on the ground plane, so a
rule's "two metres" is a real distance between foot points.

Public API:
    calibrate_clicks: H from four or more clicked pixels with known ground positions.
    calibrate_reference: H from the mask of a reference object of known size lying on the floor.
    px_to_m, m_to_px, ground_distance_m: apply H or its inverse.
    door_scale_ratio, door_scale_ok: a door-height sanity check on the scale of H.
    polygon_zone: a zone from polygon clicks in the UI.
    buffer_zone: a metric buffer around a concept's mask, returned as a pixel polygon.
    zone_in_view: how much of a zone the frame shows (the planner's `zone_in_view` check).

Conventions:
    * Pixels are OpenCV (x, y) coordinates: x to the right, y down.
    * Ground coordinates are metres in a frame fixed by the calibration (the clicked world points,
      or one corner of the reference object). Only distances and areas are meaningful.
    * Masks are binary H x W arrays where non-zero is inside. Decode SAM's COCO RLE first.
    * `err_m` is the RMS ground reprojection error of the fit, in metres: over the clicked
      points (RANSAC inliers when there are more than four) for `calibrate_clicks`, and over the
      footprint outline against the known rectangle for `calibrate_reference`. Four clicks fit
      exactly, so their `err_m` is about 0 and says nothing about extrapolation; Monte Carlo
      error bars are task P1-S1.

Reference corners: the four corners come from a four-vertex approximation of the footprint's
convex hull. Seen from straight above this equals the minimum-area rectangle; under perspective a
floor rectangle images as a general quadrilateral, which the minimum-area rectangle would
distort, so the minimum-area rectangle is only the fallback when the hull does not reduce to
four vertices. The longer pair of opposite image sides is taken as the object's long side, so
lay a pallet with its long side across the view (within about 45 degrees of the image
horizontal): pointed at the camera, perspective can shorten it below the short side and the scale
comes out about 20% off. The 1 m floor square has no such ambiguity and is the safer reference.

Door height (2.03 m) is a sanity check on scale only, never a homography source. A door stands
upright, so its height is not on the ground plane and cannot constrain a ground homography, and
door sizes vary too much to fix one. `door_scale_ratio` compares a door's pixel height with the
height the calibration's local ground scale predicts; a ratio far from 1 flags a bad
calibration (or a bad door box) for the operator to redo. It never changes H.

Tests: `uv run pytest tests/perception/test_calibration.py`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

import cv2
import numpy as np
import shapely
from numpy.typing import NDArray
from pydantic import Field
from shapely.geometry import MultiPoint, Polygon, box

from rulecam.contracts import Calibration, Frozen, Zone
from rulecam.contracts.core import BoxXYXY, PointXY
from rulecam.log import get_logger
from rulecam.perception.errors import CalibrationError, PerceptionError, ZoneError

FloatArray = NDArray[np.float64]
ReferenceKind = Literal["pallet_48x40in", "floor_square_1m"]
Footprint = Literal["full", "base"]
ViewState = Literal["inside", "partial", "outside"]

REFERENCE_SIZES_M: dict[str, tuple[float, float]] = {
    "pallet_48x40in": (1.219, 1.016),
    "floor_square_1m": (1.0, 1.0),
}
DOOR_HEIGHT_M = 2.03
DOOR_SCALE_TOLERANCE = 0.25
PERSON_HEIGHT_M = 1.7  # adult standing height used as a ruler (people vary about +/- 6 %)
PERSON_REFERENCE = "person_height_1.7m"
PERSON_SCALE_TOLERANCE = 0.45  # people slouch, sit and are foreshortened: a loose band
PERSON_ASSUMED_HFOV_DEG = 65.0  # phone and CCTV lenses: depth only, lateral scale needs none
MIN_PERSON_BOXES = 3
MIN_PERSON_ROW_SPREAD = 0.08  # of the frame height between the nearest and farthest foot rows
_PERSON_TRIM_RATIO = 0.25  # a box more than 25 % off the first fit is dropped before the refit
_MAX_PITCH_DEG = 60.0  # a horizon fitted further above the frame than this tilt allows is noise
_PRIOR_PITCH_DEG = 15.0  # people at one depth give no horizon: assume a typical downward tilt
ONE_DEPTH_ERR_M = 0.5  # floor on err_m when the horizon is assumed, not fitted
BASE_BAND_FRACTION = 0.05  # lowest share of an upright mask's height that touches the floor
RANSAC_THRESHOLD_M = 0.10  # a click more than 10 cm off the fit is an outlier
MIN_POINTS = 4

_MIN_RELATIVE_AREA = 1e-3  # triangle area / spread^2 at or below this counts as collinear
_MAX_CONDITION = 1e12
_HORIZON_EPS = 1e-9
_QUAD_EPSILONS = (0.01, 0.02, 0.03, 0.05, 0.08)  # approxPolyDP tolerance / hull perimeter

_LOG = get_logger(__name__)


class ZoneView(Frozen):
    """How much of a zone a camera frame shows."""

    state: ViewState
    visible_fraction: float = Field(ge=0.0, le=1.0)


def calibrate_clicks(
    camera_id: str, img_pts: Sequence[PointXY], world_pts_m: Sequence[PointXY]
) -> Calibration:
    """Fit a ground-plane homography to clicked pixels with known ground positions.

    Uses `cv2.findHomography`: a direct fit for exactly four points, RANSAC (threshold
    `RANSAC_THRESHOLD_M`) when there are more, so one mis-click is dropped rather than bending
    the fit.

    Args:
        camera_id: Camera the clicks were made on.
        img_pts: Clicked pixels, at least four, no three collinear.
        world_pts_m: Ground positions of the same points in metres, in the same order.

    Returns:
        A `Calibration` with method `clicks4` and `err_m` the RMS ground reprojection error of
        the inliers.

    Raises:
        CalibrationError: Fewer than four pairs, mismatched lengths, non-finite values, (nearly)
            collinear points, or a degenerate fit.
    """
    img = _as_points(img_pts, "image points", CalibrationError)
    world = _as_points(world_pts_m, "world points", CalibrationError)
    if len(img) != len(world):
        raise CalibrationError(f"got {len(img)} image points but {len(world)} world points")
    if len(img) < MIN_POINTS:
        raise CalibrationError(f"need at least {MIN_POINTS} point pairs, got {len(img)}")
    _require_general_position(img, "image points")
    _require_general_position(world, "world points")
    homography, inliers = _fit_homography(img, world)
    if not inliers.all():
        _LOG.warning(
            "calibration_outliers_dropped",
            camera_id=camera_id,
            dropped=int((~inliers).sum()),
            kept=int(inliers.sum()),
        )
    err_m = _rms(_project(homography, img[inliers]) - world[inliers])
    return _calibration(camera_id, homography, "clicks4", None, err_m)


def calibrate_reference(
    camera_id: str, mask: NDArray[np.generic], kind: ReferenceKind
) -> Calibration:
    """Fit a ground-plane homography to the mask of a reference object of known size.

    The four footprint corners (see the module docstring) are matched to the object's known
    rectangle, with ground origin at one corner.

    Args:
        camera_id: Camera the mask comes from.
        mask: Binary H x W mask of the object lying flat on the floor.
        kind: `pallet_48x40in` (1.219 x 1.016 m) or `floor_square_1m` (1 x 1 m).

    Returns:
        A `Calibration` with method `reference_object`, `reference` set to `kind`, and `err_m`
        the RMS ground distance of the mask outline from the known rectangle.

    Raises:
        CalibrationError: Unknown kind, empty or malformed mask, or degenerate corners.
    """
    size = REFERENCE_SIZES_M.get(kind)
    if size is None:
        raise CalibrationError(
            f"unknown reference kind {kind!r}; expected one of {sorted(REFERENCE_SIZES_M)}"
        )
    outline = _outline_px(_binary_mask(mask, CalibrationError))
    corners = _order_cyclic(_quad_corners(outline))
    world = _rectangle_for(corners, size)
    _require_general_position(corners, "reference corners")
    homography, _ = _fit_homography(corners, world)
    rectangle = Polygon(world).exterior
    distances = shapely.distance(rectangle, shapely.points(_project(homography, outline)))
    err_m = math.sqrt(float(np.mean(np.square(distances))))
    return _calibration(camera_id, homography, "reference_object", kind, err_m)


def px_to_m(cal: Calibration, pts: Sequence[PointXY]) -> list[PointXY]:
    """Map image pixels to ground-plane metres.

    Args:
        cal: The camera's calibration.
        pts: Pixels on the ground plane.

    Returns:
        Ground positions in metres, in input order.

    Raises:
        CalibrationError: Malformed or singular homography, or a pixel on the horizon.
    """
    return _as_tuples(_project(_matrix(cal), _as_points(pts, "pixels", CalibrationError)))


def m_to_px(cal: Calibration, pts: Sequence[PointXY]) -> list[PointXY]:
    """Map ground-plane metres to image pixels.

    Args:
        cal: The camera's calibration.
        pts: Ground positions in metres.

    Returns:
        Pixels, in input order.

    Raises:
        CalibrationError: Malformed or singular homography, or a point on the camera plane.
    """
    inverse = np.linalg.inv(_matrix(cal))
    return _as_tuples(_project(inverse, _as_points(pts, "ground points", CalibrationError)))


def ground_distance_m(cal: Calibration, a_px: PointXY, b_px: PointXY) -> float:
    """Ground distance in metres between two pixels on the floor.

    Args:
        cal: The camera's calibration.
        a_px: First pixel, e.g. one end of a tape measure.
        b_px: Second pixel.

    Returns:
        The distance in metres.

    Raises:
        CalibrationError: As for `px_to_m`.
    """
    a_m, b_m = px_to_m(cal, [a_px, b_px])
    return math.dist(a_m, b_m)


def door_scale_ratio(cal: Calibration, door_box_xyxy: BoxXYXY) -> float:
    """Ratio of a door's pixel height to the height the calibration predicts for a 2.03 m door.

    The calibration's local scale is measured along the box's bottom row, which lies on the
    floor at the door's depth; an upright object at that depth spans about the same pixels per
    metre. The approximation holds for cameras pitched up to about 40 degrees. This is a sanity
    check only and never feeds the homography (see the module docstring).

    Args:
        cal: The calibration under test.
        door_box_xyxy: Box of a standard door, its bottom edge on the floor.

    Returns:
        Observed over predicted height; about 1 when the scale is right.

    Raises:
        CalibrationError: Box without positive width and height, or as for `px_to_m`.
    """
    x1, y1, x2, y2 = door_box_xyxy
    if not (x2 > x1 and y2 > y1):
        raise CalibrationError(f"door box {door_box_xyxy} needs positive width and height")
    width_m = ground_distance_m(cal, (x1, y2), (x2, y2))
    expected_height_px = DOOR_HEIGHT_M * (x2 - x1) / width_m
    return (y2 - y1) / expected_height_px


def door_scale_ok(
    cal: Calibration, door_box_xyxy: BoxXYXY, *, tolerance: float = DOOR_SCALE_TOLERANCE
) -> bool:
    """Whether a door's apparent height agrees with the calibration's scale.

    Args:
        cal: The calibration under test.
        door_box_xyxy: Box of a standard door, its bottom edge on the floor.
        tolerance: Allowed relative deviation of `door_scale_ratio` from 1.

    Returns:
        True when the ratio is within tolerance.

    Raises:
        CalibrationError: As for `door_scale_ratio`.
    """
    return abs(door_scale_ratio(cal, door_box_xyxy) - 1.0) <= tolerance


def person_scale_ratio(cal: Calibration, person_box_xyxy: BoxXYXY) -> float:
    """Ratio of a standing person's pixel height to the height the calibration predicts.

    The same check as `door_scale_ratio`, with a 1.7 m adult as the upright object: about 1 when
    the calibration's scale is right. People are in view on almost every camera a person rule
    runs on, so this catches a reference fit that is off by a large factor (a loaded pallet or a
    stack read as one pallet) even where no door is visible.

    Args:
        cal: The calibration under test.
        person_box_xyxy: Box of a standing person, feet on the floor.

    Returns:
        Observed over predicted height.

    Raises:
        CalibrationError: Box without positive width and height, or as for `px_to_m`.
    """
    x1, y1, x2, y2 = person_box_xyxy
    if not (x2 > x1 and y2 > y1):
        raise CalibrationError(f"person box {person_box_xyxy} needs positive width and height")
    width_m = ground_distance_m(cal, (x1, y2), (x2, y2))
    if width_m <= 0:
        raise CalibrationError("person box has no ground width under this calibration")
    expected_height_px = PERSON_HEIGHT_M * (x2 - x1) / width_m
    return (y2 - y1) / expected_height_px


def people_agree(
    cal: Calibration,
    person_boxes: Sequence[BoxXYXY],
    *,
    tolerance: float = PERSON_SCALE_TOLERANCE,
) -> bool:
    """True when fewer than `MIN_PERSON_BOXES` people were seen, or most of them fit the scale.

    Each standing person is measured on its own; a person the calibration cannot measure (feet
    beyond its horizon, no ground width) counts against it. At least half the people must be
    within `tolerance` of a 1.7 m adult, so a degenerate fit cannot pass on a lucky median.

    Args:
        cal: The calibration under test.
        person_boxes: Boxes of standing people on that camera.
        tolerance: Allowed relative deviation of each `person_scale_ratio` from 1.

    Returns:
        Whether the people in view agree with the calibration's scale.
    """
    if len(person_boxes) < MIN_PERSON_BOXES:
        return True
    agreeing = 0
    for person_box in person_boxes:
        try:
            ratio = person_scale_ratio(cal, person_box)
        except CalibrationError:
            continue
        if abs(ratio - 1.0) <= tolerance:
            agreeing += 1
    return 2 * agreeing >= len(person_boxes)


def calibrate_people(
    camera_id: str,
    person_boxes: Sequence[BoxXYXY],
    frame_width: int,
    frame_height: int,
    *,
    hfov_deg: float = PERSON_ASSUMED_HFOV_DEG,
) -> Calibration:
    """Fit a ground homography from standing people of known average height.

    Under a level (no roll) pinhole camera, an upright object of height h whose feet are at
    image row y spans h * (y - y_h) / C pixels, where y_h is the horizon row and C the camera
    height. A line fitted through (foot row, pixel height) over the people in view gives y_h and
    C; then a ground point (x, y) is X = C (x - cx) / (y - y_h) across the view and
    Z = f C / (y - y_h) away from the camera, with f from an assumed lens field of view. Lateral
    distances (people beside a forklift) do not depend on f; depth does, so `err_m` is wider than
    a reference object's and acceptance keeps the rule amber until the owner marks a distance.
    People all at about one depth give no horizon: it is then assumed from a typical 15 degree
    tilt and `err_m` is at least `ONE_DEPTH_ERR_M`.

    Args:
        camera_id: Camera the boxes come from.
        person_boxes: Boxes of standing people, whole (not cut by the frame edge).
        frame_width: Frame width in pixels (principal point and focal length).
        frame_height: Frame height in pixels (the spread check).
        hfov_deg: Assumed horizontal field of view of the lens.

    Returns:
        A `Calibration` with method `reference_object` and reference `PERSON_REFERENCE`.

    Raises:
        CalibrationError: Too few people of a consistent height.
    """
    boxes = np.asarray([b for b in person_boxes if b[2] > b[0] and b[3] > b[1]], dtype=np.float64)
    if len(boxes):
        boxes = np.unique(np.round(boxes), axis=0)  # one box seen in many frames is one sighting
    if len(boxes) < MIN_PERSON_BOXES:
        raise CalibrationError(f"need {MIN_PERSON_BOXES} standing people, got {len(boxes)}")
    feet = boxes[:, 3]
    heights = boxes[:, 3] - boxes[:, 1]
    focal = frame_width / (2.0 * math.tan(math.radians(hfov_deg) / 2.0))
    highest = frame_height / 2.0 - focal * math.tan(math.radians(_MAX_PITCH_DEG))
    one_depth = float(feet.max() - feet.min()) < MIN_PERSON_ROW_SPREAD * frame_height
    if one_depth:
        # No spread of foot rows, so no horizon to fit. The people's own scale is still right at
        # their depth (what lateral distances beside them need); the horizon is a typical tilt.
        keep = np.abs(heights - np.median(heights)) <= _PERSON_TRIM_RATIO * np.median(heights)
        feet, heights = feet[keep], heights[keep]
        horizon = frame_height / 2.0 - focal * math.tan(math.radians(_PRIOR_PITCH_DEG))
    else:
        slope, intercept = np.polyfit(feet, heights, 1)
        keep = np.abs(heights - (slope * feet + intercept)) <= _PERSON_TRIM_RATIO * heights
        if int(keep.sum()) >= MIN_PERSON_BOXES:
            slope, intercept = np.polyfit(feet[keep], heights[keep], 1)
            feet, heights = feet[keep], heights[keep]
        # People at nearly one depth leave the slope ill-conditioned; their own scale is still
        # right, so the horizon is clamped into the range a real tilt allows and C refitted.
        horizon = -intercept / slope if slope > 0 else highest
    if len(feet) < MIN_PERSON_BOXES:
        raise CalibrationError("too few people of a consistent height to use as a ruler")
    lowest = float(feet.min()) - MIN_PERSON_ROW_SPREAD * frame_height
    horizon = min(max(horizon, highest), lowest)
    camera_height = float(np.median(PERSON_HEIGHT_M * (feet - horizon) / heights))
    slope, intercept = PERSON_HEIGHT_M / camera_height, -PERSON_HEIGHT_M * horizon / camera_height
    cx = frame_width / 2.0
    homography = np.array(
        [
            [camera_height, 0.0, -camera_height * cx],
            [0.0, 0.0, focal * camera_height],
            [0.0, 1.0, -horizon],
        ]
    )
    relative = np.abs(heights - (slope * feet + intercept)) / heights
    spread = math.sqrt(float(np.mean(np.square(relative))) + 0.06**2)  # fit + people's heights
    err_m = 2.0 * spread  # 1-sigma at the 2 m distances person rules are written with
    if one_depth:
        err_m = max(err_m, ONE_DEPTH_ERR_M)
    _LOG.info(
        "people_calibration",
        camera_id=camera_id,
        people=len(feet),
        horizon_px=round(horizon, 1),
        camera_height_m=round(camera_height, 2),
        err_m=round(err_m, 3),
    )
    return _calibration(camera_id, homography, "reference_object", PERSON_REFERENCE, err_m)


def polygon_zone(zone_id: str, camera_id: str, points_px: Sequence[PointXY]) -> Zone:
    """Build a polygon zone from clicks in the UI.

    Args:
        zone_id: Zone name used by rules, e.g. `line_entry`.
        camera_id: Camera the polygon was drawn on.
        points_px: Polygon vertices in pixels, in drawing order.

    Returns:
        A `Zone` of kind `polygon`.

    Raises:
        ZoneError: Fewer than three vertices, non-finite values, zero area or self-intersection.
    """
    vertices = _as_points(points_px, "zone vertices", ZoneError)
    zone = Zone(
        zone_id=zone_id, camera_id=camera_id, kind="polygon", polygon_px=_as_tuples(vertices)
    )
    _zone_polygon(zone)
    return zone


def buffer_zone(
    zone_id: str,
    cal: Calibration,
    mask: NDArray[np.generic],
    concept: str,
    buffer_m: float,
    *,
    footprint: Footprint = "full",
) -> Zone:
    """Build a metric buffer zone around a concept's mask ("the area around the excavator").

    The mask's ground footprint is projected into metres, buffered by `buffer_m` with shapely,
    and projected back to pixels for display and for `inside` checks.

    Args:
        zone_id: Zone name used by rules, e.g. `egress`.
        cal: Calibration of the camera the mask comes from.
        mask: Binary H x W mask of the concept.
        concept: The concept the zone is built around, e.g. `fire exit door`.
        buffer_m: Buffer distance in metres, greater than 0.
        footprint: `full` uses the whole mask, for flat objects (pallets, mats, spills);
            `base` uses only its lowest `BASE_BAND_FRACTION`, for upright objects (doors,
            racks, machines) whose upper pixels are not on the floor.

    Returns:
        A `Zone` of kind `buffer` with `polygon_px` set.

    Raises:
        ZoneError: Non-positive buffer, empty mask, or a buffer reaching behind the camera.
        CalibrationError: Malformed calibration, or a footprint reaching the horizon.
    """
    if not buffer_m > 0:
        raise ZoneError(f"buffer_m must be > 0, got {buffer_m}")
    homography = _matrix(cal)
    band = _footprint_mask(_binary_mask(mask, ZoneError), footprint)
    ground = _ground_hull(homography, _outline_px(band))
    ring = np.asarray(ground.buffer(buffer_m).exterior.coords, dtype=np.float64)[:-1]
    centroid = np.array([[ground.centroid.x, ground.centroid.y]])
    polygon_px, w = _homogeneous(np.linalg.inv(homography), np.vstack([centroid, ring]))
    if np.any(np.sign(w[1:]) != np.sign(w[0])):
        raise ZoneError(f"a {buffer_m} m buffer around {concept!r} reaches behind the camera")
    return Zone(
        zone_id=zone_id,
        camera_id=cal.camera_id,
        kind="buffer",
        polygon_px=_as_tuples(polygon_px[1:]),
        buffer_of_concept=concept,
        buffer_m=buffer_m,
    )


def zone_in_view(zone: Zone, width: int, height: int) -> ZoneView:
    """Report how much of a zone's pixel polygon lies inside a `width` x `height` frame.

    Args:
        zone: A polygon zone, or a buffer zone built by `buffer_zone`.
        width: Frame width in pixels.
        height: Frame height in pixels.

    Returns:
        `inside` when the frame covers the whole zone, `outside` when they share no area,
        otherwise `partial`, with the visible share of the zone's area.

    Raises:
        ZoneError: Non-positive frame size, or a zone without a valid pixel polygon.
    """
    if width <= 0 or height <= 0:
        raise ZoneError(f"frame size must be positive, got {width}x{height}")
    polygon = _zone_polygon(zone)
    frame = box(0, 0, width, height)
    if frame.covers(polygon):
        return ZoneView(state="inside", visible_fraction=1.0)
    fraction = min(1.0, polygon.intersection(frame).area / polygon.area)
    return ZoneView(state="partial" if fraction > 0 else "outside", visible_fraction=fraction)


def _fit_homography(img: FloatArray, world: FloatArray) -> tuple[FloatArray, NDArray[np.bool_]]:
    """Fit image -> ground H (direct for four points, RANSAC above) and return it with inliers."""
    method = cv2.RANSAC if len(img) > MIN_POINTS else 0
    homography, status = cv2.findHomography(img, world, method, RANSAC_THRESHOLD_M)
    if homography is None or homography.shape != (3, 3):
        raise CalibrationError("OpenCV could not fit a homography to these points")
    inliers = status.ravel().astype(bool)
    if inliers.sum() < MIN_POINTS:
        raise CalibrationError(f"only {int(inliers.sum())} points agree with the fit")
    _require_general_position(img[inliers], "inlier image points")
    matrix = np.asarray(homography, dtype=np.float64)
    _require_well_conditioned(matrix)
    return matrix, inliers


def _require_general_position(points: FloatArray, what: str) -> None:
    """Raise unless `points` contain four points with no three (nearly) collinear.

    Greedy: take the farthest pair a, b; the point c farthest from line ab; then the point d
    that maximises the smallest of the triangles abd, acd and bcd.
    """
    gaps = points[:, None, :] - points[None, :, :]
    squared = np.einsum("ijk,ijk->ij", gaps, gaps)
    first, second = np.unravel_index(int(np.argmax(squared)), squared.shape)
    limit = _MIN_RELATIVE_AREA * float(squared[first, second])
    a, b = points[first], points[second]
    third = int(np.argmax(_triangle_areas(a, b, points)))
    c = points[third]
    smallest = np.minimum.reduce(
        [_triangle_areas(*pair, points) for pair in ((a, b), (a, c), (b, c))]
    )
    if _triangle_areas(a, b, c[None, :])[0] <= limit or float(smallest.max()) <= limit:
        raise CalibrationError(
            f"{what} are (nearly) collinear: a homography needs four points, no three on a line"
        )


def _triangle_areas(a: FloatArray, b: FloatArray, points: FloatArray) -> FloatArray:
    """Areas of the triangles (a, b, p) for every row p of `points`."""
    ab = b - a
    ap = points - a
    areas: FloatArray = 0.5 * np.abs(ab[0] * ap[:, 1] - ab[1] * ap[:, 0])
    return areas


def _require_well_conditioned(matrix: FloatArray) -> None:
    """Raise unless `matrix` is a finite, invertible 3 x 3 homography."""
    if not np.isfinite(matrix).all() or np.linalg.cond(matrix) > _MAX_CONDITION:
        raise CalibrationError("homography is singular or not finite")


def _matrix(cal: Calibration) -> FloatArray:
    """The calibration's homography as a validated 3 x 3 array."""
    matrix = np.asarray(cal.homography, dtype=np.float64)
    if matrix.shape != (3, 3):
        raise CalibrationError(f"homography must be 3x3, got shape {matrix.shape}")
    _require_well_conditioned(matrix)
    return matrix


def _homogeneous(matrix: FloatArray, pts: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Apply a homography to (N, 2) points; return the mapped points and their w terms.

    Raises:
        CalibrationError: A point maps to infinity (w cancels to zero).
    """
    lifted = np.column_stack([pts, np.ones(len(pts))])
    mapped = lifted @ matrix.T
    w = mapped[:, 2]
    scale = np.abs(lifted) @ np.abs(matrix[2])
    if np.any(np.abs(w) <= _HORIZON_EPS * scale):
        raise CalibrationError("a point maps to infinity: it lies on the horizon line")
    return mapped[:, :2] / w[:, None], w


def _project(matrix: FloatArray, pts: FloatArray) -> FloatArray:
    """Apply a homography to (N, 2) points."""
    return _homogeneous(matrix, pts)[0]


def _ground_hull(homography: FloatArray, outline_px: FloatArray) -> shapely.Geometry:
    """Convex hull, in ground metres, of a mask outline that must lie below the horizon."""
    ground, w = _homogeneous(homography, outline_px)
    lowest = int(np.argmax(outline_px[:, 1]))
    if np.any(np.sign(w) != np.sign(w[lowest])):
        raise CalibrationError(
            "the mask reaches the horizon, so it is not on the floor; use footprint='base'"
        )
    return MultiPoint(ground).convex_hull


def _as_points(pts: Sequence[PointXY], what: str, error: type[PerceptionError]) -> FloatArray:
    """Validate a sequence of (x, y) pairs as a finite (N, 2) float array."""
    array = np.asarray(pts, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 2 or len(array) == 0:  # noqa: PLR2004 - (x, y)
        raise error(f"{what} must be a non-empty sequence of (x, y) pairs")
    if not np.isfinite(array).all():
        raise error(f"{what} must be finite")
    return array


def _as_tuples(pts: FloatArray) -> list[PointXY]:
    """Convert an (N, 2) array to a list of (x, y) tuples."""
    return [(float(x), float(y)) for x, y in pts]


def _rms(residuals: FloatArray) -> float:
    """Root-mean-square length of (N, 2) residual vectors."""
    return math.sqrt(float(np.mean(np.sum(np.square(residuals), axis=1))))


def _calibration(
    camera_id: str,
    homography: FloatArray,
    method: Literal["clicks4", "reference_object"],
    reference: str | None,
    err_m: float,
) -> Calibration:
    """Package a fitted homography as a `Calibration` contract."""
    rows = tuple((float(r[0]), float(r[1]), float(r[2])) for r in homography)
    return Calibration(
        camera_id=camera_id, homography=rows, method=method, reference=reference, err_m=err_m
    )


def _binary_mask(mask: NDArray[np.generic], error: type[PerceptionError]) -> NDArray[np.uint8]:
    """Validate a 2-D mask and return it as a 0/1 uint8 image."""
    array = np.asarray(mask)
    if array.ndim != 2:  # noqa: PLR2004 - an image is two-dimensional
        raise error(f"mask must be a 2-D array, got {array.ndim} dimensions")
    binary: NDArray[np.uint8] = (array != 0).astype(np.uint8)
    if not binary.any():
        raise error("mask is empty")
    return binary


def _footprint_mask(mask: NDArray[np.uint8], footprint: Footprint) -> NDArray[np.uint8]:
    """The part of `mask` that touches the floor: all of it, or its lowest band of rows."""
    if footprint == "full":
        return mask
    rows = np.flatnonzero(mask.any(axis=1))
    top, bottom = int(rows[0]), int(rows[-1])
    band = max(1, math.ceil(BASE_BAND_FRACTION * (bottom - top + 1)))
    base = np.zeros_like(mask)
    base[bottom - band + 1 : bottom + 1] = mask[bottom - band + 1 : bottom + 1]
    return base


def _outline_px(mask: NDArray[np.uint8]) -> FloatArray:
    """All outer-contour pixels of every blob in a non-empty binary mask, as (N, 2) x, y."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return np.vstack([c.reshape(-1, 2) for c in contours]).astype(np.float64)


def _quad_corners(outline: FloatArray) -> FloatArray:
    """Four corners of a footprint: a 4-vertex hull approximation, else its min-area rectangle."""
    hull = cv2.convexHull(outline.astype(np.float32))
    perimeter = cv2.arcLength(hull, closed=True)
    for fraction in _QUAD_EPSILONS:
        approx = cv2.approxPolyDP(hull, fraction * perimeter, closed=True).reshape(-1, 2)
        if len(approx) == MIN_POINTS:
            return approx.astype(np.float64)
    return np.asarray(cv2.boxPoints(cv2.minAreaRect(hull)), dtype=np.float64)


def _order_cyclic(corners: FloatArray) -> FloatArray:
    """Order four corners by angle around their centroid, starting nearest the top-left."""
    centred = corners - corners.mean(axis=0)
    ordered = corners[np.argsort(np.arctan2(centred[:, 1], centred[:, 0]))]
    start = int(np.argmin(ordered.sum(axis=1)))
    return np.roll(ordered, -start, axis=0)


def _rectangle_for(corners: FloatArray, size_m: tuple[float, float]) -> FloatArray:
    """Ground corners of the reference rectangle matching cyclically ordered image corners."""
    sides = np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)
    long_m, short_m = max(size_m), min(size_m)
    first_long = sides[0] + sides[2] >= sides[1] + sides[3]
    along, across = (long_m, short_m) if first_long else (short_m, long_m)
    return np.array([[0.0, 0.0], [along, 0.0], [along, across], [0.0, across]])


def _zone_polygon(zone: Zone) -> Polygon:
    """The zone's pixel polygon, validated as a simple polygon with positive area."""
    if zone.polygon_px is None or len(zone.polygon_px) < 3:  # noqa: PLR2004 - a triangle
        raise ZoneError(
            f"zone {zone.zone_id!r} has no pixel polygon of 3+ vertices; "
            "build buffer zones with buffer_zone()"
        )
    polygon = Polygon(zone.polygon_px)
    if not polygon.is_valid or polygon.area <= 0:
        raise ZoneError(f"zone {zone.zone_id!r} polygon is self-intersecting or has no area")
    return polygon
