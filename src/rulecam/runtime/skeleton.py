"""Skeleton geometry and PPE marks for anonymous/strict strips (task P4-05).

`runtime.strip_draw.draw_person` uses this module for two things:

- **Strict-mode body normalisation** (`canonical_pose`): the pose keeps the direction of every
  bone, but each bone gets a fixed length (a multiple of the torso length), so height, build and
  limb proportions do not reach the strip. The head is a circle of canonical size above the neck.
- **PPE marks** (`draw_ppe`): for each `without` predicate of the rule, the person's skeleton
  carries a mark where the item belongs (a cap over the head for `top_25pct`, a vest over the
  torso for `full`, rings on the wrists for `hands`). A filled mark means an item was detected in
  that region at this keyframe; an outlined, crossed mark means none was. Every mark is one
  neutral colour: the state is in the shape, never in a per-person colour.

Face keypoints (COCO 0-4) are never drawn or rebuilt here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from rulecam.contracts.core import PointXY

Frame = NDArray[np.uint8]
Region = Literal["full", "top_25pct", "hands"]

L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST = 5, 6, 7, 8, 9, 10
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE = 11, 12, 13, 14, 15, 16
BODY_KEYPOINTS = range(5, 17)  # COCO-17 without the face (nose, eyes, ears)

# Canonical bone lengths as a multiple of the torso length (neck to pelvis).
HEAD_LENGTH = 0.45
HEAD_RADIUS = 0.2
SHOULDER_HALF_WIDTH = 0.4
HIP_HALF_WIDTH = 0.3
UPPER_ARM, FOREARM = 0.6, 0.5
THIGH, SHIN = 0.8, 0.8

GLYPH_COLOUR = (255, 255, 255)
GLYPH_EDGE_COLOUR = (0, 0, 0)
GLYPH_LEGEND = "PPE mark: filled = item seen, crossed = not seen"


@dataclass(frozen=True)
class PpeMark:
    """Whether a rule's PPE item was detected in one region of a person at one keyframe.

    Attributes:
        region: The `without` predicate's region (`top_25pct`, `full` or `hands`).
        seen: True if an item's box centre lay in that region.
    """

    region: Region
    seen: bool


@dataclass(frozen=True)
class Anchors:
    """Where PPE marks go on a drawn figure, in strip pixels.

    Attributes:
        head: The head circle `(centre, radius)`, if the figure has one.
        torso: The torso polygon (shoulders and hips), empty if unknown.
        wrists: The wrists that are known.
    """

    head: tuple[PointXY, float] | None
    torso: tuple[PointXY, ...]
    wrists: tuple[PointXY, ...]


def body_joints(
    keypoints: Sequence[tuple[float, float, float]], min_score: float
) -> dict[int, PointXY]:
    """The known body joints (COCO 5-16) of a keypoint list; face points are left out."""
    return {
        k: (x, y)
        for k, (x, y, score) in enumerate(keypoints)
        if k in BODY_KEYPOINTS and score >= min_score
    }


def _mid(a: PointXY | None, b: PointXY | None) -> PointXY | None:
    if a is not None and b is not None:
        return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    return a or b


def _torso(joints: Mapping[int, PointXY]) -> tuple[PointXY, PointXY, float] | None:
    """`(neck, pelvis, length)` from the shoulders and hips, or None if unknown or degenerate."""
    neck = _mid(joints.get(L_SHOULDER), joints.get(R_SHOULDER))
    pelvis = _mid(joints.get(L_HIP), joints.get(R_HIP))
    if neck is None or pelvis is None:
        return None
    length = math.hypot(neck[0] - pelvis[0], neck[1] - pelvis[1])
    return (neck, pelvis, length) if length > 0 else None


def _step(origin: PointXY, start: PointXY, end: PointXY, length: float) -> PointXY | None:
    """The point `length` from `origin` in the direction `start` to `end`."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    norm = math.hypot(dx, dy)
    if norm == 0:
        return None
    return origin[0] + dx / norm * length, origin[1] + dy / norm * length


CHAINS: tuple[tuple[bool, tuple[tuple[int, float], ...]], ...] = (
    (True, ((L_SHOULDER, SHOULDER_HALF_WIDTH), (L_ELBOW, UPPER_ARM), (L_WRIST, FOREARM))),
    (True, ((R_SHOULDER, SHOULDER_HALF_WIDTH), (R_ELBOW, UPPER_ARM), (R_WRIST, FOREARM))),
    (False, ((L_HIP, HIP_HALF_WIDTH), (L_KNEE, THIGH), (L_ANKLE, SHIN))),
    (False, ((R_HIP, HIP_HALF_WIDTH), (R_KNEE, THIGH), (R_ANKLE, SHIN))),
)
"""Limb chains: `(starts at the neck, ((joint, canonical length), ...))`; else at the pelvis."""


def canonical_pose(joints: Mapping[int, PointXY]) -> dict[int, PointXY]:
    """Rebuild a pose with canonical bone lengths, keeping each bone's direction.

    Each limb is rebuilt outwards from the neck or the pelvis; a chain stops at its first unknown
    joint. Face points are never rebuilt.

    Args:
        joints: Known body joints by COCO index (`body_joints`).

    Returns:
        The rebuilt joints, or an empty dict if the torso (shoulders and hips) is not known.
    """
    torso = _torso(joints)
    if torso is None:
        return {}
    neck, pelvis, length = torso
    rebuilt: dict[int, PointXY] = {}
    for from_neck, chain in CHAINS:
        original = rebuilt_parent = neck if from_neck else pelvis
        for index, factor in chain:
            target = joints.get(index)
            if target is None:
                break
            point = _step(rebuilt_parent, original, target, factor * length)
            if point is None:
                break
            rebuilt[index] = point
            original, rebuilt_parent = target, point
    return rebuilt


def canonical_head(joints: Mapping[int, PointXY]) -> tuple[PointXY, float] | None:
    """The strict-mode head: a circle of canonical size above the neck, along the torso."""
    torso = _torso(joints)
    if torso is None:
        return None
    (nx, ny), (px, py), length = torso
    # HEAD_LENGTH torso lengths beyond the neck, away from the pelvis.
    centre = (nx + (nx - px) * HEAD_LENGTH, ny + (ny - py) * HEAD_LENGTH)
    return centre, HEAD_RADIUS * length


def joint_anchors(joints: Mapping[int, PointXY], head: tuple[PointXY, float] | None) -> Anchors:
    """PPE anchors of a figure drawn from joints (real or canonical)."""
    corners = [joints.get(k) for k in (L_SHOULDER, R_SHOULDER, R_HIP, L_HIP)]
    torso = tuple(c for c in corners if c is not None)
    return Anchors(
        head=head,
        torso=torso if len(torso) == len(corners) else (),
        wrists=tuple(joints[k] for k in (L_WRIST, R_WRIST) if k in joints),
    )


def draw_ppe(frame: Frame, anchors: Anchors, marks: Sequence[PpeMark], size: int) -> None:
    """Draw the PPE marks of one person on the frame, in place.

    Args:
        frame: The strip frame.
        anchors: Where the figure's head, torso and wrists are.
        marks: One mark per region (a region whose anchor is unknown is skipped).
        size: The figure's line thickness; marks scale with it.
    """
    for mark in marks:
        if mark.region == "top_25pct" and anchors.head is not None:
            _cap(frame, anchors.head, mark.seen, size)
        elif mark.region == "full" and anchors.torso:
            _vest(frame, anchors.torso, mark.seen, size)
        elif mark.region == "hands":
            for wrist in anchors.wrists:
                _ring(frame, wrist, mark.seen, size)


def _pt(point: PointXY) -> tuple[int, int]:
    return (round(point[0]), round(point[1]))


def _fill_or_cross(frame: Frame, polygon: NDArray[np.int32], seen: bool, size: int) -> None:
    """Fill the polygon (seen) or outline and cross it (not seen), edged for contrast."""
    if seen:
        cv2.fillPoly(frame, [polygon], GLYPH_COLOUR)
    else:
        x0, y0 = polygon.min(axis=0)
        x1, y1 = polygon.max(axis=0)
        for a, b in (((x0, y0), (x1, y1)), ((x0, y1), (x1, y0))):
            cv2.line(frame, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), GLYPH_COLOUR, size)
        cv2.polylines(frame, [polygon], True, GLYPH_COLOUR, size)
    cv2.polylines(frame, [polygon], True, GLYPH_EDGE_COLOUR, 1)


def _cap(frame: Frame, head: tuple[PointXY, float], seen: bool, size: int) -> None:
    (cx, cy), radius = head
    r = max(3, round(radius * 1.3))
    polygon = cv2.ellipse2Poly(_pt((cx, cy - radius * 0.4)), (r, r), 0, 180, 360, 15)
    _fill_or_cross(frame, np.asarray(polygon, dtype=np.int32), seen, size)


def _vest(frame: Frame, torso: Sequence[PointXY], seen: bool, size: int) -> None:
    _fill_or_cross(frame, np.asarray([_pt(p) for p in torso], dtype=np.int32), seen, size)


def _ring(frame: Frame, wrist: PointXY, seen: bool, size: int) -> None:
    r = max(3, 2 * size)
    polygon = cv2.ellipse2Poly(_pt(wrist), (r, r), 0, 0, 360, 30)
    _fill_or_cross(frame, np.asarray(polygon, dtype=np.int32), seen, size)
