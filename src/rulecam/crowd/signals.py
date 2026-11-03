"""Crowd measurements from one frame pair: counts, density per square metre, motion, pressure.

Pure functions; nothing here decides what is dangerous. They turn SAM 3.1's boxes, the camera's
ground calibration and the optical flow between two frames into numbers the models read:

- **Counting in dense crowds:** SAM sees small, packed people better on enlarged tiles, so the
  frame is cut into overlapping tiles (`tile_boxes`) and the per-tile boxes are merged back
  (`merge_boxes`).
- **Density:** each pixel's ground area comes from the homography's Jacobian,
  `|det H| / w^3` with `w` the homography's third row at that pixel (`area_map`), so any image
  region's area in square metres is a sum, with no assumption about the view's shape. Pixels at
  or above the horizon, or farther than `MAX_RANGE_M`, count as no area.
- **Motion:** Farneback optical flow (OpenCV) moves each person's foot point by the median flow
  inside their box; both points go through the homography, so velocities are in m/s without
  tracking anyone (trackers break in dense crowds; flow does not need identities).
- **Crowd pressure:** after Helbing, Johansson and Al-Abideen (2007, the 2006 Jamarat video),
  `pressure = density x Var(V)`, the local density times the variance of the velocities. It
  rises before turbulence; the reading carries it and the models judge it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import cv2
import numpy as np
from cv2.typing import MatLike
from numpy.typing import NDArray

from rulecam.contracts import Calibration

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
Box = tuple[float, float, float, float, float]
"""A detection: `(x1, y1, x2, y2, confidence)` in frame pixels."""
TileRect = tuple[int, int, int, int]
"""A tile: `(x1, y1, x2, y2)` in frame pixels, end exclusive."""

AREA_STEP_PX: Final = 4  # area map resolution: one value per 4 x 4 pixel block
MAX_RANGE_M: Final = 60.0  # ground farther than this is too coarse to measure
MIN_CELL_AREA_M2: Final = 2.0  # a smaller visible area makes a density from one person
MIN_SPEED_MPS: Final = 0.15  # below this a velocity's direction is noise (counterflow ignores it)
FLOW_MAX_WIDTH_PX: Final = 640  # optical flow runs on a copy at most this wide
_HORIZON_EPS: Final = 1e-9


def tile_boxes(width: int, height: int, grid: int, overlap: float = 0.15) -> list[TileRect]:
    """Overlapping tiles covering the frame, `grid` x `grid` of them.

    Args:
        width: Frame width in pixels.
        height: Frame height in pixels.
        grid: Tiles per side; 1 is the whole frame.
        overlap: Share of a tile's size that overlaps its neighbour, so a person cut by a tile
            edge is whole in the next tile.

    Returns:
        The tiles, row by row.

    Raises:
        ValueError: A grid below 1 or an overlap outside `[0, 0.5)`.
    """
    if grid < 1 or not 0.0 <= overlap < 0.5:  # noqa: PLR2004 - more overlap repeats whole tiles
        msg = f"grid must be >= 1 and overlap in [0, 0.5), got {grid}, {overlap}"
        raise ValueError(msg)
    tile_w = width / (grid - (grid - 1) * overlap)
    tile_h = height / (grid - (grid - 1) * overlap)
    tiles = []
    for row in range(grid):
        for col in range(grid):
            x1 = round(col * tile_w * (1 - overlap))
            y1 = round(row * tile_h * (1 - overlap))
            tiles.append((x1, y1, min(width, round(x1 + tile_w)), min(height, round(y1 + tile_h))))
    return tiles


def merge_boxes(boxes: Sequence[Box], iou: float = 0.5, inside: float = 0.8) -> list[Box]:
    """Merge detections of several tiles into one set (greedy, most confident first).

    A box is dropped when it overlaps a kept box by more than `iou`, or when more than `inside`
    of the smaller box lies in the other: a person cut by a tile edge is a part box inside the
    whole one seen by the next tile.

    Args:
        boxes: Detections in frame pixels.
        iou: Intersection over union above which two boxes are one object.
        inside: Share of the smaller box inside the other above which they are one object.

    Returns:
        The kept boxes, most confident first.
    """
    kept: list[Box] = []
    for box in sorted(boxes, key=lambda b: b[4], reverse=True):
        if all(_overlap(box, other) <= (iou, inside) for other in kept):
            kept.append(box)
    return kept


def _overlap(a: Box, b: Box) -> tuple[float, float]:
    """Intersection over union and over the smaller box; both 0 when they do not touch."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0, 0.0
    common = width * height
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - common
    return (common / union if union > 0 else 0.0), common / max(min(area_a, area_b), 1e-9)


def foot_points(boxes: Sequence[Box]) -> FloatArray:
    """The bottom centre of each box, `(N, 2)` pixels: where a person stands."""
    if not boxes:
        return np.zeros((0, 2))
    array = np.asarray(boxes, dtype=np.float64)
    return np.column_stack([(array[:, 0] + array[:, 2]) / 2.0, array[:, 3]])


def homography(cal: Calibration) -> FloatArray:
    """The calibration's 3 x 3 image-pixel -> ground-metre homography."""
    return np.asarray(cal.homography, dtype=np.float64).reshape(3, 3)


def to_ground(h: FloatArray, points_px: FloatArray) -> tuple[FloatArray, BoolArray]:
    """Map pixels to ground metres; `valid` is false at or above the horizon and out of range.

    Args:
        h: The homography.
        points_px: `(N, 2)` pixels.

    Returns:
        `(N, 2)` metres (NaN where invalid) and the valid mask.
    """
    if not len(points_px):
        return np.zeros((0, 2)), np.zeros(0, dtype=bool)
    lifted = np.column_stack([points_px, np.ones(len(points_px))])
    mapped = lifted @ h.T
    w = mapped[:, 2]
    valid = w * _sign(h) > _HORIZON_EPS
    ground = np.full((len(points_px), 2), np.nan)
    ground[valid] = mapped[valid, :2] / w[valid, None]
    valid &= np.hypot(ground[:, 0], ground[:, 1]) <= MAX_RANGE_M
    ground[~valid] = np.nan
    return ground, valid


def _sign(h: FloatArray) -> float:
    """Which sign of `w` is in front of the camera: the sign at the bottom centre of the view.

    The bottom row of a ground camera's view is always on the floor, so the sign of `w` there
    picks the side of the horizon that is ground (homographies are defined up to scale).
    """
    w = float(h[2, 1] * 1e4 + h[2, 2])  # a far-down pixel; only the sign matters
    return 1.0 if w >= 0 else -1.0


def area_map(h: FloatArray, width: int, height: int, step: int = AREA_STEP_PX) -> FloatArray:
    """Ground area in square metres of each `step` x `step` pixel block of the frame.

    For a homography `p -> (H p)_{1,2} / w`, the Jacobian determinant at pixel `p` is
    `det H / w^3`, so a block's area is that value times `step^2`. Blocks at or above the
    horizon, or farther than `MAX_RANGE_M`, have area 0.

    Args:
        h: The homography.
        width: Frame width in pixels.
        height: Frame height in pixels.
        step: Block size in pixels.

    Returns:
        `(ceil(height / step), ceil(width / step))` areas.
    """
    xs = np.arange(0, width, step, dtype=np.float64) + step / 2.0
    ys = np.arange(0, height, step, dtype=np.float64) + step / 2.0
    grid_x, grid_y = np.meshgrid(xs, ys)
    centres = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    _, valid = to_ground(h, centres)
    lifted = np.column_stack([centres, np.ones(len(centres))])
    w = (lifted @ h.T)[:, 2]
    areas = np.zeros(len(centres))
    det = abs(float(np.linalg.det(h)))
    areas[valid] = det / np.abs(w[valid]) ** 3 * step * step
    return areas.reshape(grid_y.shape)


def polygon_blocks(
    polygon_px: Sequence[tuple[float, float]], width: int, height: int, step: int = AREA_STEP_PX
) -> BoolArray:
    """Which `step`-pixel blocks of the frame a polygon covers (the area map's grid)."""
    rows, cols = -(-height // step), -(-width // step)
    mask = np.zeros((rows, cols), dtype=np.uint8)
    points = np.round(np.asarray(polygon_px, dtype=np.float64) / step).astype(np.int32)
    if len(points) >= 3:  # noqa: PLR2004 - a polygon needs three corners
        cv2.fillPoly(mask, [points], 1)
    return mask.astype(bool)


def inside(polygon_px: Sequence[tuple[float, float]], points_px: FloatArray) -> BoolArray:
    """Which points lie inside (or on) the polygon."""
    contour = np.asarray(polygon_px, dtype=np.float32).reshape(-1, 1, 2)
    return np.array(
        [
            cv2.pointPolygonTest(contour, (float(x), float(y)), measureDist=False) >= 0
            for x, y in points_px
        ],
        dtype=bool,
    )


def optical_flow(before: MatLike, after: MatLike) -> FloatArray:
    """Dense Farneback flow from `before` to `after`, `(H, W, 2)` pixels of the full frame.

    Runs on copies at most `FLOW_MAX_WIDTH_PX` wide and scales the result back up.

    Args:
        before: The earlier frame (BGR or grey).
        after: The later frame, same size.

    Returns:
        Per-pixel displacement `(dx, dy)` in full-frame pixels.
    """
    height, width = after.shape[:2]
    scale = min(1.0, FLOW_MAX_WIDTH_PX / width)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    grey_before = cv2.resize(_grey(before), size, interpolation=cv2.INTER_AREA)
    grey_after = cv2.resize(_grey(after), size, interpolation=cv2.INTER_AREA)
    start = np.zeros((size[1], size[0], 2), dtype=np.float32)
    flow = cv2.calcOpticalFlowFarneback(grey_before, grey_after, start, 0.5, 4, 21, 3, 5, 1.1, 0)
    full = cv2.resize(flow, (width, height), interpolation=cv2.INTER_LINEAR)
    out: FloatArray = np.asarray(full, dtype=np.float64) / scale
    return out


def _grey(image: MatLike) -> MatLike:
    if len(image.shape) == 3:  # noqa: PLR2004 - colour images have a channel axis
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def box_flow(flow: FloatArray, boxes: Sequence[Box]) -> FloatArray:
    """The median flow inside each box, `(N, 2)` pixels (zero for an empty box)."""
    out = np.zeros((len(boxes), 2))
    height, width = flow.shape[:2]
    for k, (x1, y1, x2, y2, _) in enumerate(boxes):
        left, top = max(0, int(x1)), max(0, int(y1))
        right, bottom = min(width, int(np.ceil(x2))), min(height, int(np.ceil(y2)))
        if right > left and bottom > top:
            patch = flow[top:bottom, left:right].reshape(-1, 2)
            out[k] = np.median(patch, axis=0)
    return out


def ground_velocities(
    h: FloatArray, feet_px: FloatArray, flow_px: FloatArray, dt_s: float
) -> FloatArray:
    """Each person's ground velocity in m/s: the foot point moved by its box's flow.

    Args:
        h: The homography.
        feet_px: `(N, 2)` foot points in the later frame.
        flow_px: `(N, 2)` displacement from the earlier frame to the later one.
        dt_s: Seconds between the frames.

    Returns:
        `(N, 2)` velocities, NaN where either point is off the measurable ground.
    """
    now, valid_now = to_ground(h, feet_px)
    before, valid_before = to_ground(h, feet_px - flow_px)
    velocities = (now - before) / dt_s
    velocities[~(valid_now & valid_before)] = np.nan
    return velocities


@dataclass(frozen=True)
class Motion:
    """How a group of people moves.

    Attributes:
        moving: People with a measured velocity.
        speed: Mean speed (m/s, or px/s uncalibrated).
        speed_sd: Standard deviation of the speeds.
        velocity_var: Variance of the velocity vectors, `mean |v - mean v|^2`.
        coherence: `|mean v| / mean |v|`: 1 when everyone walks the same way, near 0 when they
            mill about or push against each other.
        counterflow: Share of the people moving faster than `MIN_SPEED_MPS` whose direction is
            against the group's mean direction.
    """

    moving: int
    speed: float | None
    speed_sd: float | None
    velocity_var: float | None
    coherence: float | None
    counterflow: float | None


def motion(velocities: FloatArray) -> Motion:
    """Summarise velocities (rows with NaN are skipped)."""
    v = velocities[np.isfinite(velocities).all(axis=1)] if len(velocities) else velocities
    if not len(v):
        return Motion(0, None, None, None, None, None)
    speeds = np.hypot(v[:, 0], v[:, 1])
    mean = v.mean(axis=0)
    mean_speed = float(speeds.mean())
    variance = float(np.mean(np.sum((v - mean) ** 2, axis=1)))
    coherence = float(np.hypot(*mean) / mean_speed) if mean_speed > 0 else None
    fast = speeds > MIN_SPEED_MPS
    counter = None
    if fast.any() and np.hypot(*mean) > 0:
        counter = float(np.mean((v[fast] @ mean) < 0))
    return Motion(
        moving=len(v),
        speed=mean_speed,
        speed_sd=float(speeds.std()),
        velocity_var=variance,
        coherence=coherence,
        counterflow=counter,
    )


def pressure(density: float | None, velocity_var: float | None) -> float | None:
    """Crowd pressure, `density x Var(V)` (Helbing et al. 2007), in 1/s^2; `None` if unknown."""
    if density is None or velocity_var is None:
        return None
    return density * velocity_var


def cell_rects(width: int, height: int, cols: int, rows: int) -> list[TileRect]:
    """The density grid's cells, row by row, as pixel rectangles."""
    xs = np.linspace(0, width, cols + 1).round().astype(int)
    ys = np.linspace(0, height, rows + 1).round().astype(int)
    return [
        (int(xs[c]), int(ys[r]), int(xs[c + 1]), int(ys[r + 1]))
        for r in range(rows)
        for c in range(cols)
    ]


def region_area(
    h: FloatArray,
    areas: FloatArray,
    polygon_px: Sequence[tuple[float, float]],
    width: int,
    height: int,
) -> float:
    """Ground area of an image polygon in square metres.

    Exact when every corner is on measurable ground (a homography maps the polygon to a polygon,
    whose area is the shoelace sum); otherwise the area map's blocks inside the polygon.
    """
    ground, valid = to_ground(h, np.asarray(polygon_px, dtype=np.float64))
    if len(ground) >= 3 and valid.all():  # noqa: PLR2004 - a polygon needs three corners
        x, y = ground[:, 0], ground[:, 1]
        return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)
    return float(areas[polygon_blocks(polygon_px, width, height)].sum())


def rect_polygon(rect: TileRect) -> list[tuple[float, float]]:
    """A pixel rectangle's corners, clockwise from the top left."""
    x1, y1, x2, y2 = (float(v) for v in rect)
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def in_rect(points_px: FloatArray, rect: TileRect) -> BoolArray:
    """Which points lie in the rectangle (left/top edges in, right/bottom out)."""
    if not len(points_px):
        return np.zeros(0, dtype=bool)
    x1, y1, x2, y2 = rect
    return (
        (points_px[:, 0] >= x1)
        & (points_px[:, 0] < x2)
        & (points_px[:, 1] >= y1)
        & (points_px[:, 1] < y2)
    )


def density(people: int, area_m2: float | None) -> float | None:
    """People per square metre; `None` without a big enough measured area."""
    if area_m2 is None or area_m2 < MIN_CELL_AREA_M2:
        return None
    return people / area_m2


@dataclass(frozen=True)
class Trend:
    """A least-squares line through a series.

    Attributes:
        points: How many samples it fits.
        latest: The newest value.
        per_min: Change per minute.
        r2: How well the line fits (1 is a straight line).
        in_2_min: The line's value two minutes after the newest sample.
        in_5_min: The line's value five minutes after the newest sample.
    """

    points: int
    latest: float
    per_min: float
    r2: float
    in_2_min: float
    in_5_min: float


def trend(seconds: Sequence[float], values: Sequence[float]) -> Trend | None:
    """Fit a line to `(seconds, value)` samples; `None` with fewer than three."""
    if len(values) < 3:  # noqa: PLR2004 - a line through two points says nothing about fit
        return None
    t = np.asarray(seconds, dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    if float(t.max() - t.min()) <= 0:
        return None
    slope, intercept = np.polyfit(t, y, 1)
    fitted = slope * t + intercept
    total = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum((y - fitted) ** 2)) / total if total > 0 else 1.0
    last = float(t.max())
    return Trend(
        points=len(y),
        latest=float(y[np.argmax(t)]),
        per_min=float(slope * 60.0),
        r2=r2,
        in_2_min=float(slope * (last + 120.0) + intercept),
        in_5_min=float(slope * (last + 300.0) + intercept),
    )
