from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from rulecam.contracts import Keypoint, TrackPoint

NAMES = (
    "nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
    "l_wrist", "r_wrist", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle",
)  # fmt: skip

STANDING = {
    "nose": (100, 80), "l_eye": (98, 78), "r_eye": (102, 78), "l_ear": (95, 80), "r_ear": (105, 80),
    "l_shoulder": (90, 100), "r_shoulder": (110, 100), "l_elbow": (85, 130), "r_elbow": (115, 130),
    "l_wrist": (85, 160), "r_wrist": (115, 160), "l_hip": (92, 160), "r_hip": (108, 160),
    "l_knee": (92, 210), "r_knee": (108, 210), "l_ankle": (92, 260), "r_ankle": (108, 260),
}  # fmt: skip
STANDING_BOX = (70.0, 80.0, 130.0, 265.0)

LYING = {
    "nose": (30, 256), "l_eye": (32, 254), "r_eye": (32, 258),
    "l_ear": (36, 254), "r_ear": (36, 258),
    "l_shoulder": (60, 250), "r_shoulder": (60, 262), "l_elbow": (80, 250), "r_elbow": (80, 262),
    "l_wrist": (100, 250), "r_wrist": (100, 262), "l_hip": (120, 250), "r_hip": (120, 262),
    "l_knee": (170, 250), "r_knee": (170, 262), "l_ankle": (220, 250), "r_ankle": (220, 262),
}  # fmt: skip
LYING_BOX = (20.0, 235.0, 240.0, 270.0)

BENDING = {
    **STANDING,
    "l_shoulder": (170, 190), "r_shoulder": (178, 190),
    "l_hip": (110, 160), "r_hip": (118, 160),
    "l_knee": (110, 210), "r_knee": (118, 210),
    "l_ankle": (110, 260), "r_ankle": (118, 260),
    "l_wrist": (200, 190), "r_wrist": (208, 190),
}  # fmt: skip
BENDING_BOX = (90.0, 170.0, 210.0, 265.0)

LIFTING = {
    **STANDING,
    "l_shoulder": (150, 185), "r_shoulder": (158, 185),
    "l_hip": (110, 160), "r_hip": (118, 160),
    "l_knee": (110, 210), "r_knee": (118, 210),
    "l_wrist": (150, 230), "r_wrist": (158, 230),
}  # fmt: skip

Pose = Mapping[str, tuple[float, float]]


def keypoints(pose: Pose, score: float = 0.9, **scores: float) -> tuple[Keypoint, ...]:
    """Build COCO-17 keypoints from a named pose, with optional per-name score overrides."""
    return tuple(
        Keypoint(x=pose[name][0], y=pose[name][1], score=scores.get(name, score)) for name in NAMES
    )


def track_point(
    frame_idx: int = 0, box: tuple[float, float, float, float] = STANDING_BOX
) -> TrackPoint:
    """A track point at `frame_idx`, one frame per 100 ms, with no keypoints."""
    return TrackPoint(
        ts=datetime(2026, 10, 31, 12, 0, 0, frame_idx * 100_000, tzinfo=UTC),
        frame_idx=frame_idx,
        box_xyxy=box,
        foot_px=((box[0] + box[2]) / 2, box[3]),
    )
