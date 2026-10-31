"""Skeleton renders on a clean plate, with PPE glyphs (task P4-05, V2 Pillar 7b).

Only keypoints and PPE states are drawn; the renderer never reads a person's pixels, so clothing
colour, skin and faces cannot reach the output. Strict mode additionally rebuilds each body with
canonical bone lengths, keeping the pose but not the proportions.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from rulecam.contracts import Keypoint, PrivacyMode
from rulecam.pose.features import MIN_SCORE

Frame = NDArray[np.uint8]
Point = tuple[float, float]
Colour = tuple[int, int, int]

COCO_EDGES = (
    (0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16),
)  # fmt: skip
NOSE, L_SHOULDER, R_SHOULDER = 0, 5, 6
L_ELBOW, R_ELBOW, L_WRIST, R_WRIST = 7, 8, 9, 10
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE = 11, 12, 13, 14, 15, 16

PRESENT: Colour = (0, 200, 0)  # BGR green
MISSING: Colour = (0, 0, 220)  # BGR red
UNKNOWN: Colour = (160, 160, 160)

# Canonical bone lengths as a multiple of the torso length (neck to pelvis).
HEAD_LENGTH = 0.45
SHOULDER_HALF_WIDTH = 0.4
HIP_HALF_WIDTH = 0.3
UPPER_ARM, FOREARM = 0.6, 0.5
THIGH, SHIN = 0.8, 0.8
DEFAULT_SCALE_PX = 40.0
BONE_THICKNESS = 3
JOINT_RADIUS = 4
GLYPH_OUTLINE = 2


@dataclass(frozen=True)
class PersonRender:
    """One person to draw: keypoints and PPE states, nothing else.

    `track_id` is opaque and only seeds the colour. `ppe` maps an item (`helmet`, `vest`,
    `gloves`, `harness`) to True (present), False (missing) or None (unknown).
    """

    track_id: str
    keypoints: Sequence[Keypoint]
    ppe: Mapping[str, bool | None]


def track_colour(track_id: str) -> Colour:
    """A stable BGR colour from a hash of the track id."""
    hue = int(hashlib.sha256(track_id.encode()).hexdigest()[:4], 16) % 180
    bgr = cv2.cvtColor(np.array([[[hue, 200, 235]]], dtype=np.uint8), cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def ppe_from_trace(
    trace: Mapping[str, float | str | bool], paths: Mapping[str, str]
) -> dict[str, bool | None]:
    """Read PPE states from a predicate trace.

    Args:
        trace: A candidate's `predicate_trace`.
        paths: For each item, the trace path of its `without` predicate (true means missing).

    Returns:
        For each item: True if present, False if missing, None if the trace has no answer or
        marks it unknown.
    """
    states: dict[str, bool | None] = {}
    for item, path in paths.items():
        value = trace.get(path)
        if trace.get(f"{path}.unknown") or not isinstance(value, bool):
            states[item] = None
        else:
            states[item] = not value
    return states


def _joints(keypoints: Sequence[Keypoint]) -> dict[int, Point]:
    return {i: (k.x, k.y) for i, k in enumerate(keypoints) if k.score >= MIN_SCORE}


def _mid(a: Point | None, b: Point | None) -> Point | None:
    if a is not None and b is not None:
        return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    return a or b


def _unit(start: Point, end: Point) -> Point | None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    return None if length == 0 else (dx / length, dy / length)


def _step(origin: Point, towards: Point, length: float, along_from: Point) -> Point | None:
    """The point `length` from `origin`, in the direction `along_from` to `towards`."""
    direction = _unit(along_from, towards)
    if direction is None:
        return None
    return origin[0] + direction[0] * length, origin[1] + direction[1] * length


def canonical_pose(joints: Mapping[int, Point]) -> dict[int, Point]:
    """Rebuild a pose with canonical bone lengths, keeping each bone's direction.

    Lengths are a fixed multiple of the torso length, so height, build and limb proportions can't
    identify anyone. The face points other than the nose are dropped.

    Args:
        joints: Known joints by COCO index.

    Returns:
        The rebuilt joints, or an empty dict if the torso (shoulders and hips) is not known.
    """
    neck = _mid(joints.get(L_SHOULDER), joints.get(R_SHOULDER))
    pelvis = _mid(joints.get(L_HIP), joints.get(R_HIP))
    if neck is None or pelvis is None:
        return {}
    torso = math.hypot(neck[0] - pelvis[0], neck[1] - pelvis[1])
    if torso == 0:
        return {}
    rebuilt: dict[int, Point] = {}

    def place(index: int, parent: tuple[Point, Point], length: float) -> Point | None:
        """Put `index` one canonical bone from the rebuilt parent, along the original bone."""
        original_parent, rebuilt_parent = parent
        target = joints.get(index)
        point = (
            None
            if target is None
            else _step(rebuilt_parent, target, length * torso, original_parent)
        )
        if point is not None:
            rebuilt[index] = point
        return point

    def chain(root: tuple[Point, Point], bones: Sequence[tuple[int, float]]) -> None:
        parent = root
        for index, length in bones:
            point = place(index, parent, length)
            if point is None or index not in joints:
                return
            parent = (joints[index], point)

    root_neck, root_pelvis = (neck, neck), (pelvis, pelvis)
    place(NOSE, root_neck, HEAD_LENGTH)
    chain(root_neck, [(L_SHOULDER, SHOULDER_HALF_WIDTH), (L_ELBOW, UPPER_ARM), (L_WRIST, FOREARM)])
    chain(root_neck, [(R_SHOULDER, SHOULDER_HALF_WIDTH), (R_ELBOW, UPPER_ARM), (R_WRIST, FOREARM)])
    chain(root_pelvis, [(L_HIP, HIP_HALF_WIDTH), (L_KNEE, THIGH), (L_ANKLE, SHIN)])
    chain(root_pelvis, [(R_HIP, HIP_HALF_WIDTH), (R_KNEE, THIGH), (R_ANKLE, SHIN)])
    return rebuilt


def _scale(joints: Mapping[int, Point]) -> float:
    neck = _mid(joints.get(L_SHOULDER), joints.get(R_SHOULDER))
    pelvis = _mid(joints.get(L_HIP), joints.get(R_HIP))
    if neck is not None and pelvis is not None:
        return max(math.hypot(neck[0] - pelvis[0], neck[1] - pelvis[1]), 1.0)
    return DEFAULT_SCALE_PX


def _glyph_colour(state: bool | None) -> Colour:
    return UNKNOWN if state is None else PRESENT if state else MISSING


def _ipoint(point: Point) -> tuple[int, int]:
    return round(point[0]), round(point[1])


def _draw_helmet(image: Frame, joints: Mapping[int, Point], state: bool | None) -> None:
    head = joints.get(NOSE)
    if head is None:
        return
    radius = max(4, round(0.3 * _scale(joints)))
    centre = _ipoint((head[0], head[1] - radius * 0.6))
    colour = _glyph_colour(state)
    thickness = GLYPH_OUTLINE if state is None else -1
    cv2.ellipse(image, centre, (radius, radius), 0, 180, 360, colour, thickness)


def _draw_vest(image: Frame, joints: Mapping[int, Point], state: bool | None) -> None:
    corners = [joints.get(i) for i in (L_SHOULDER, R_SHOULDER, R_HIP, L_HIP)]
    if any(c is None for c in corners):
        return
    polygon = np.array([_ipoint(c) for c in corners if c is not None], dtype=np.int32)
    colour = _glyph_colour(state)
    if state is None:
        cv2.polylines(image, [polygon], True, colour, GLYPH_OUTLINE)
    else:
        cv2.fillPoly(image, [polygon], colour)


def _draw_gloves(image: Frame, joints: Mapping[int, Point], state: bool | None) -> None:
    radius = max(3, round(0.12 * _scale(joints)))
    thickness = GLYPH_OUTLINE if state is None else -1
    for wrist in (L_WRIST, R_WRIST):
        if wrist in joints:
            cv2.circle(image, _ipoint(joints[wrist]), radius, _glyph_colour(state), thickness)


def _draw_harness(image: Frame, joints: Mapping[int, Point], state: bool | None) -> None:
    for a, b in ((L_SHOULDER, R_HIP), (R_SHOULDER, L_HIP)):
        if a in joints and b in joints:
            cv2.line(image, _ipoint(joints[a]), _ipoint(joints[b]), _glyph_colour(state), 3)


GLYPHS = {
    "helmet": _draw_helmet,
    "vest": _draw_vest,
    "gloves": _draw_gloves,
    "harness": _draw_harness,
}


def render_skeletons(plate: Frame, people: Sequence[PersonRender], mode: PrivacyMode) -> Frame:
    """Draw every person as a skeleton on a copy of the plate.

    Args:
        plate: The clean background plate (BGR).
        people: The people to draw.
        mode: `anonymous` draws the poses as they are; `strict` rebuilds them with canonical
            bone lengths. `standard` is refused: it is not an anonymous render.

    Returns:
        The rendered image. A person whose torso is unknown is not drawn in strict mode.

    Raises:
        ValueError: If `mode` is `standard`.
    """
    if mode is PrivacyMode.STANDARD:
        raise ValueError("skeleton renders are for anonymous and strict modes only")
    image = plate.copy()
    for person in people:
        joints = _joints(person.keypoints)
        if mode is PrivacyMode.STRICT:
            joints = canonical_pose(joints)
        colour = track_colour(person.track_id)
        for a, b in COCO_EDGES:
            if a in joints and b in joints:
                cv2.line(image, _ipoint(joints[a]), _ipoint(joints[b]), colour, BONE_THICKNESS)
        for point in joints.values():
            cv2.circle(image, _ipoint(point), JOINT_RADIUS, colour, -1)
        for item, state in person.ppe.items():
            if item in GLYPHS:
                GLYPHS[item](image, joints, state)
    return image
