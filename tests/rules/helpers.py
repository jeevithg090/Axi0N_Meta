"""Builders for rules-engine tests: rules, calibrated synthetic tracks and zones."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from rulecam.contracts import Calibration, Keypoint, RuleIR, Track, TrackPoint, Zone
from tests.fakes import EPOCH

HZ = 10.0
PX_PER_M = 100.0
# 100 px = 1 m on the ground plane, so pixel and metre geometry are easy to reason about.
CAL = Calibration(
    camera_id="cam1",
    homography=((1 / PX_PER_M, 0.0, 0.0), (0.0, 1 / PX_PER_M, 0.0), (0.0, 0.0, 1.0)),
    method="clicks4",
    err_m=0.01,
)
INSIDE_M = (0.5, 0.5)
Segment = tuple[tuple[float, float] | None, float]
OUTSIDE_M = (5.0, 5.0)
SINK_ZONE = Zone(
    zone_id="sink_zone",
    camera_id="cam1",
    kind="polygon",
    polygon_px=((0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)),
)


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def make_rule(
    predicate: dict[str, object],
    *,
    concepts: dict[str, str] | None = None,
    zones: dict[str, object] | None = None,
    rule_id: str = "test-rule",
    severity: str = "medium",
) -> RuleIR:
    names = concepts or {"person": "person"}
    return RuleIR.model_validate(
        {
            "id": rule_id,
            "source": {"kind": "text", "text": "test"},
            "concepts": {key: {"prompt": prompt} for key, prompt in names.items()},
            "zones": zones or {},
            "predicate": predicate,
            "severity": severity,
            "actions": [{"kind": "log"}],
        }
    )


def walk_track(
    track_id: str,
    positions_m: Sequence[tuple[float, float] | None],
    *,
    concept: str = "person",
    camera_id: str = "cam1",
    start_s: float = 0.0,
    hz: float = HZ,
    size_px: tuple[float, float] = (40.0, 80.0),
    with_foot_m: bool = True,
    keypoints: tuple[Keypoint, ...] | None = None,
) -> Track:
    """A track whose foot point visits `positions_m` at `hz`; `None` entries are dropouts."""
    width, height = size_px
    points = []
    for index, pos in enumerate(positions_m):
        if pos is None:
            continue
        fx, fy = pos[0] * PX_PER_M, pos[1] * PX_PER_M
        points.append(
            TrackPoint(
                ts=at(start_s + index / hz),
                frame_idx=index,
                box_xyxy=(fx - width / 2, fy - height, fx + width / 2, fy),
                foot_px=(fx, fy),
                foot_m=pos if with_foot_m else None,
                confidence=0.9,
                keypoints=keypoints,
            )
        )
    return Track(track_id=track_id, camera_id=camera_id, concept=concept, points=tuple(points))


def box_track(
    track_id: str,
    boxes: Iterable[tuple[float, float, float, float]],
    *,
    concept: str,
    camera_id: str = "cam1",
    start_s: float = 0.0,
    keypoints: tuple[Keypoint, ...] | None = None,
) -> Track:
    """A track with explicit pixel boxes; the foot point is the box bottom-centre."""
    points = []
    for index, box in enumerate(boxes):
        foot = ((box[0] + box[2]) / 2, box[3])
        points.append(
            TrackPoint(
                ts=at(start_s + index / HZ),
                frame_idx=index,
                box_xyxy=box,
                foot_px=foot,
                foot_m=(foot[0] / PX_PER_M, foot[1] / PX_PER_M),
                keypoints=keypoints,
            )
        )
    return Track(track_id=track_id, camera_id=camera_id, concept=concept, points=tuple(points))


def segments(
    *parts: tuple[tuple[float, float] | None, float], hz: float = HZ
) -> list[tuple[float, float] | None]:
    """Concatenate `(position, seconds)` segments into per-sample positions."""
    out: list[tuple[float, float] | None] = []
    for position, seconds in parts:
        out.extend([position] * round(seconds * hz))
    return out


def wrists(left: tuple[float, float], right: tuple[float, float]) -> tuple[Keypoint, ...]:
    """COCO-17 keypoints with only the two wrists reliable."""
    kps = [Keypoint(x=0.0, y=0.0, score=0.0) for _ in range(17)]
    kps[9] = Keypoint(x=left[0], y=left[1], score=0.9)
    kps[10] = Keypoint(x=right[0], y=right[1], score=0.9)
    return tuple(kps)
