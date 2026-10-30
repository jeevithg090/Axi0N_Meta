"""Vectorised plane geometry used by the predicates.

Only basic IEEE arithmetic is used (no transcendental functions), so results are bit-identical
across platforms, which the determinism guarantee of the engine relies on.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from rulecam.contracts import Calibration

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]


def project(calibration: Calibration, points_px: FloatArray) -> FloatArray:
    """Map image pixels to ground-plane metres with the calibration homography.

    Args:
        calibration: Camera calibration (3x3 homography, image px -> ground metres).
        points_px: Array of shape (n, 2). NaN rows stay NaN.

    Returns:
        Array of shape (n, 2) in metres.
    """
    h = np.asarray(calibration.homography, dtype=np.float64)
    x, y = points_px[:, 0], points_px[:, 1]
    w = h[2, 0] * x + h[2, 1] * y + h[2, 2]
    gx = (h[0, 0] * x + h[0, 1] * y + h[0, 2]) / w
    gy = (h[1, 0] * x + h[1, 1] * y + h[1, 2]) / w
    return np.stack([gx, gy], axis=1)


def norm(vectors: FloatArray) -> FloatArray:
    """Return the Euclidean length of each row of an (n, 2) array."""
    return np.sqrt(vectors[:, 0] * vectors[:, 0] + vectors[:, 1] * vectors[:, 1])


def points_in_polygon(points: FloatArray, polygon: FloatArray) -> BoolArray:
    """Even-odd ray-casting test for many points against one polygon.

    Args:
        points: Array of shape (n, 2). NaN rows are outside.
        polygon: Array of shape (m, 2) with m >= 3 vertices, open or closed.

    Returns:
        Boolean array of shape (n,).
    """
    x, y = points[:, 0], points[:, 1]
    inside = np.zeros(len(points), dtype=np.bool_)
    xj, yj = polygon[-1]
    for xi, yi in polygon:
        crosses = (yi > y) != (yj > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
        inside ^= crosses & (x < x_cross)
        xj, yj = xi, yi
    return inside


def box_iou(a: FloatArray, b: FloatArray) -> FloatArray:
    """Row-wise intersection over union of two (n, 4) arrays of xyxy boxes."""
    ix = np.clip(np.minimum(a[:, 2], b[:, 2]) - np.maximum(a[:, 0], b[:, 0]), 0.0, None)
    iy = np.clip(np.minimum(a[:, 3], b[:, 3]) - np.maximum(a[:, 1], b[:, 1]), 0.0, None)
    inter = ix * iy
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a + area_b - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(union > 0, inter / union, 0.0)


def box_centres(boxes: FloatArray) -> FloatArray:
    """Return the centre of each xyxy box as an (n, 2) array."""
    return np.stack([(boxes[:, 0] + boxes[:, 2]) / 2.0, (boxes[:, 1] + boxes[:, 3]) / 2.0], axis=1)


def point_segment_distance(points: FloatArray, seg_a: FloatArray, seg_b: FloatArray) -> FloatArray:
    """Row-wise distance from points to segments, all arrays of shape (n, 2)."""
    d = seg_b - seg_a
    length_sq = d[:, 0] * d[:, 0] + d[:, 1] * d[:, 1]
    rel = points - seg_a
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(length_sq > 0, (rel[:, 0] * d[:, 0] + rel[:, 1] * d[:, 1]) / length_sq, 0.0)
    t = np.clip(t, 0.0, 1.0)
    closest = seg_a + d * t[:, None]
    return norm(points - closest)
