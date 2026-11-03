"""Build `CheckSample`s from the rules engine's per-camera `Scene` (one per timeline sample).

A sample holds the objects of one rule concept present at one instant (named by the rule's
concept key), the camera's polygon zones,
the extension's reference lines and a local pixels-per-metre scale. Objects are ordered by their
box, never by track id, and carry no track id: a check sees an instant, not a person over time
(AGENTS.md rule 7).
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from rulecam.contracts import Calibration
from rulecam.evidence.chain import utc_iso
from rulecam.extensions.schema import CheckObject, CheckSample, PointXY
from rulecam.rules.geometry import project
from rulecam.rules.scene import Scene, resolve_zone
from rulecam.rules.timeline import SampledTrack

Line = tuple[PointXY, PointXY]


def samples_from_scene(
    scene: Scene,
    concept: str,
    lines: Mapping[str, Line],
    frame_wh: tuple[int, int] | None = None,
) -> list[CheckSample]:
    """One `CheckSample` per timeline sample of `scene`.

    Args:
        scene: The engine's scene for one camera.
        concept: The rule concept whose objects the check sees.
        lines: The extension's reference lines on this camera, in pixels.
        frame_wh: The camera's frame size, when known.

    Returns:
        Samples in timeline order.
    """
    zones = camera_zones(scene)
    tracks = scene.tracks_of(concept)
    scale = px_per_m(scene.calibration, reference_point(lines, tracks))
    width, height = frame_wh if frame_wh is not None else (None, None)
    return [
        CheckSample(
            timestamp=utc_iso(scene.timeline.ts_at(index)),
            frame_w=width,
            frame_h=height,
            objects=objects_at(concept, tracks, index),
            zones=zones,
            lines=dict(lines),
            px_per_m=scale,
        )
        for index in range(scene.timeline.n)
    ]


def objects_at(
    concept: str, tracks: tuple[SampledTrack, ...], index: int
) -> tuple[CheckObject, ...]:
    """Objects of `tracks` present at timeline sample `index`, named `concept`, ordered by box."""
    found = [obj for track in tracks if (obj := object_at(concept, track, index)) is not None]
    return tuple(sorted(found, key=lambda o: o.box_xyxy_px))


def object_at(concept: str, track: SampledTrack, index: int) -> CheckObject | None:
    """The track's object at sample `index` under the rule's concept name, or None if absent.

    The rule's concept key ("pallet") is used, not the track's SAM prompt ("wooden pallet"), so
    a check written for the owner's words sees the same name at run time.
    """
    if not track.start <= index < track.stop:
        return None
    local = index - track.start
    if not track.present[local]:
        return None
    x1, y1, x2, y2 = (float(v) for v in track.box[local])
    foot = track.foot_m[local]
    point = track.point_at(local)
    score = point.confidence if point is not None else None
    return CheckObject(
        concept=concept,
        box_xyxy_px=(x1, y1, x2, y2),
        mask_top_y_px=min(y1, y2),
        mask_area_px=max(0.0, (x2 - x1) * (y2 - y1)),
        ground_xy_m=(float(foot[0]), float(foot[1])) if bool(np.isfinite(foot).all()) else None,
        score=score,
    )


def camera_zones(scene: Scene) -> dict[str, tuple[PointXY, ...]]:
    """Polygon zones on the scene's camera, given ones first, then the rule's own specs."""
    names = sorted(set(scene.zones) | set(scene.rule.zones))
    out: dict[str, tuple[PointXY, ...]] = {}
    for name in names:
        zone = resolve_zone(name, scene.zones, scene.rule)
        if zone.camera_id == scene.camera_id and zone.polygon_px is not None:
            out[name] = tuple((float(x), float(y)) for x, y in zone.polygon_px)
    return out


def reference_point(lines: Mapping[str, Line], tracks: tuple[SampledTrack, ...]) -> PointXY | None:
    """Where to measure the image scale: the first line's middle, else the first foot point."""
    for name in sorted(lines):
        (x1, y1), (x2, y2) = lines[name]
        return ((x1 + x2) / 2, (y1 + y2) / 2)
    for track in tracks:
        finite = track.foot_px[np.isfinite(track.foot_px).all(axis=1)]
        if len(finite):
            return (float(finite[0][0]), float(finite[0][1]))
    return None


def px_per_m(calibration: Calibration | None, point: PointXY | None) -> float | None:
    """Image pixels per ground metre around `point` (one vertical pixel step), when calibrated."""
    if calibration is None or point is None:
        return None
    x, y = point
    ground = project(calibration, np.array([[x, y], [x, y + 1.0]]))
    step_m = float(np.hypot(*(ground[1] - ground[0])))
    if not math.isfinite(step_m) or step_m <= 0:
        return None
    return round(1.0 / step_m, 3)
