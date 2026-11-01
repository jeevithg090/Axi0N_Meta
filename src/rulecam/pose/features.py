"""Pure pose features over COCO-17 keypoints (task P4-04).

Image coordinates: x to the right, y downwards. A keypoint scoring under `MIN_SCORE` is unknown.
Every feature returns `True` or `False` when the evidence decides it, and `None` when the needed
keypoints are unknown. The rule engine reads `None` as "false, with the unknown flag set".
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from rulecam.contracts import Keypoint, TrackPoint
from rulecam.contracts.core import BoxXYXY
from rulecam.rules.pose import PoseStateName

MIN_SCORE = 0.3
SIDES = 2
DOWN_ANGLE_DEG = 60.0
HIP_HEIGHT_RATIO = 0.8
BENT_HIP_ANGLE_DEG = 120.0
BENT_TORSO_ANGLE_DEG = 45.0
LADDER_MIN_LIMBS = 3

L_SHOULDER, R_SHOULDER = 5, 6
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16

Point = tuple[float, float]
LadderRegion = Callable[[TrackPoint], BoxXYXY | None]


def known(keypoints: Sequence[Keypoint], index: int) -> Point | None:
    """Return a keypoint's position, or None if it is missing or scores under `MIN_SCORE`."""
    if index >= len(keypoints) or keypoints[index].score < MIN_SCORE:
        return None
    keypoint = keypoints[index]
    return keypoint.x, keypoint.y


def _centre(keypoints: Sequence[Keypoint], left: int, right: int) -> Point | None:
    """Midpoint of a left/right pair, or the one known side if the other is unknown."""
    a, b = known(keypoints, left), known(keypoints, right)
    if a is not None and b is not None:
        return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    return a or b


def _angle_deg(a: Point, vertex: Point, b: Point) -> float | None:
    """Angle at `vertex` between the rays to `a` and `b`, or None for a zero-length ray."""
    ax, ay = a[0] - vertex[0], a[1] - vertex[1]
    bx, by = b[0] - vertex[0], b[1] - vertex[1]
    if (ax == 0 and ay == 0) or (bx == 0 and by == 0):
        return None
    return math.degrees(math.atan2(abs(ax * by - ay * bx), ax * bx + ay * by))


def _torso(keypoints: Sequence[Keypoint]) -> tuple[Point, Point, float] | None:
    """Shoulder centre, hip centre and the torso's angle from vertical in degrees (0 = upright).

    Returns None if the shoulders or hips are unknown, or sit on the same point.
    """
    shoulders = _centre(keypoints, L_SHOULDER, R_SHOULDER)
    hips = _centre(keypoints, L_HIP, R_HIP)
    if shoulders is None or hips is None or shoulders == hips:
        return None
    dx, dy = shoulders[0] - hips[0], shoulders[1] - hips[1]
    return shoulders, hips, math.degrees(math.atan2(abs(dx), abs(dy)))


def is_down(keypoints: Sequence[Keypoint], box_xyxy: BoxXYXY) -> bool | None:
    """Whether the person is lying down.

    True when the torso is more than `DOWN_ANGLE_DEG` from vertical and the hips sit close to
    the ground: the box bottom is the ground, and "close" is under `HIP_HEIGHT_RATIO` torso
    lengths. A person bending over fails the second test because their legs stay upright.

    Args:
        keypoints: COCO-17 keypoints.
        box_xyxy: The person's box in the same image.

    Returns:
        The decision, or None if the shoulders or hips are unknown.
    """
    torso = _torso(keypoints)
    if torso is None:
        return None
    shoulders, hips, angle = torso
    if angle <= DOWN_ANGLE_DEG:
        return False
    torso_length = math.hypot(shoulders[0] - hips[0], shoulders[1] - hips[1])
    return box_xyxy[3] - hips[1] < HIP_HEIGHT_RATIO * torso_length


def hands_above_shoulder(keypoints: Sequence[Keypoint]) -> bool | None:
    """Whether either wrist is above its own shoulder.

    Returns:
        True if either side shows it. False only if both sides are known and neither does. None if
        no side can be judged, or one side is known-false and the other unknown, because the
        unknown side could still be raised.
    """
    results = []
    for wrist, shoulder in ((L_WRIST, L_SHOULDER), (R_WRIST, R_SHOULDER)):
        w, s = known(keypoints, wrist), known(keypoints, shoulder)
        if w is not None and s is not None:
            results.append(w[1] < s[1])
    if any(results):
        return True
    return False if len(results) == SIDES else None


def ladder_three_point_lost(keypoints: Sequence[Keypoint], ladder_box: BoxXYXY) -> bool | None:
    """Whether fewer than three limbs (two wrists, two ankles) touch the ladder region.

    Args:
        keypoints: COCO-17 keypoints.
        ladder_box: The ladder's region in the same image.

    Returns:
        True if fewer than three limbs are inside even if every unknown limb were inside, False if
        three are inside already, otherwise None.
    """
    inside = unknown = 0
    for limb in (L_WRIST, R_WRIST, L_ANKLE, R_ANKLE):
        position = known(keypoints, limb)
        if position is None:
            unknown += 1
        elif (
            ladder_box[0] <= position[0] <= ladder_box[2]
            and ladder_box[1] <= position[1] <= ladder_box[3]
        ):
            inside += 1
    if inside >= LADDER_MIN_LIMBS:
        return False
    return True if inside + unknown < LADDER_MIN_LIMBS else None


def bent_back_lift(
    keypoints: Sequence[Keypoint],
    max_hip_angle_deg: float = BENT_HIP_ANGLE_DEG,
    min_torso_angle_deg: float = BENT_TORSO_ANGLE_DEG,
) -> bool | None:
    """Whether the back is bent forward to lift: hips flexed, torso inclined, hands low.

    All three must hold: the shoulder-hip-knee angle is under `max_hip_angle_deg`, the torso is
    more than `min_torso_angle_deg` from vertical, and the hands are below the knees. The torso
    test is what separates a stooped lift from a correct squat, where the hips and knees bend
    but the back stays upright. The hip angle is averaged over the sides where shoulder, hip and
    knee are all known; hands below knees compares mean wrist height with mean knee height.

    Returns:
        The decision, or None if no hip angle, no torso, no wrist or no knee is known.
    """
    angles = []
    for shoulder, hip, knee in ((L_SHOULDER, L_HIP, L_KNEE), (R_SHOULDER, R_HIP, R_KNEE)):
        s, h, k = known(keypoints, shoulder), known(keypoints, hip), known(keypoints, knee)
        if s is not None and h is not None and k is not None:
            angle = _angle_deg(s, h, k)
            if angle is not None:
                angles.append(angle)
    torso = _torso(keypoints)
    wrists = [p for p in (known(keypoints, L_WRIST), known(keypoints, R_WRIST)) if p]
    knees = [p for p in (known(keypoints, L_KNEE), known(keypoints, R_KNEE)) if p]
    if not angles or torso is None or not wrists or not knees:
        return None
    hands_below_knees = sum(p[1] for p in wrists) / len(wrists) > sum(p[1] for p in knees) / len(
        knees
    )
    return (
        sum(angles) / len(angles) < max_hip_angle_deg
        and torso[2] > min_torso_angle_deg
        and hands_below_knees
    )


class PoseStates:
    """Adapter that makes these features a `rulecam.rules.pose.PoseFeatures`.

    Args:
        ladder_region: Returns the ladder's box for a track point. Without it, the ladder state is
            always unknown.
    """

    def __init__(self, ladder_region: LadderRegion | None = None) -> None:
        """Store the optional ladder region lookup."""
        self._ladder_region = ladder_region

    def __call__(self, state: PoseStateName, point: TrackPoint) -> bool | None:
        """Return whether `point` shows `state`, or None if its keypoints are unreliable."""
        keypoints = point.keypoints
        if keypoints is None:
            return None
        match state:
            case "down":
                return is_down(keypoints, point.box_xyxy)
            case "hands_above_shoulder":
                return hands_above_shoulder(keypoints)
            case "bent_back_lift":
                return bent_back_lift(keypoints)
            case "ladder_three_point_lost":
                box = self._ladder_region(point) if self._ladder_region else None
                return None if box is None else ladder_three_point_lost(keypoints, box)
