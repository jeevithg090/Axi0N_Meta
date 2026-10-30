"""A synthetic two-dock warehouse window used by the process-log and guardrail tests.

Ground metres map to pixels as px = 100 * m (like `tests.fakes.make_track`). Timeline, 0-300 s:

* dock_1 (cam1): truck A 20-140 s. Forklift 1 in the dock 50-80 s and 90-110 s, so waiting 40 s,
  handling 50 s, post-handling dwell 30 s. A pallet is staged at 60 s.
* dock_3 (cam3): truck B 30-90 s with forklift 2 at 60-85 s (wait 30, handling 25, post 5);
  truck C 200-300 s with forklift 2 at 250-270 s (still there at the window end: incomplete).
* aisle_1 (cam1): a pallet blocks it 150-180 s; idle forklift 1 parks in it 200-220 s.
* People stand in dock_1, dock_3 and the aisle the whole time; they must change nothing.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import timedelta

import numpy as np

from rulecam.contracts import Track, TrackPoint, Zone
from tests.fakes import EPOCH

Keyframe = tuple[float, float, float]  # (seconds, x_m, y_m)

_ids = itertools.count(1)

DOCK_1 = Zone(
    zone_id="dock_1",
    camera_id="cam1",
    kind="polygon",
    polygon_px=((0, 0), (400, 0), (400, 300), (0, 300)),
)
AISLE_1 = Zone(
    zone_id="aisle_1",
    camera_id="cam1",
    kind="polygon",
    polygon_px=((0, 500), (1000, 500), (1000, 700), (0, 700)),
)
DOCK_3 = Zone(
    zone_id="dock_3",
    camera_id="cam3",
    kind="polygon",
    polygon_px=((600, 0), (1000, 0), (1000, 300), (600, 300)),
)
DOOR = Zone(
    zone_id="door_1",
    camera_id="cam1",
    kind="polygon",
    polygon_px=((0, 0), (10, 0), (10, 10)),
)
ZONES = (DOCK_1, AISLE_1, DOCK_3, DOOR)
WINDOW_S = 300.0


def along(
    concept: str,
    camera_id: str,
    keyframes: Sequence[Keyframe],
    *,
    hz: float = 10.0,
    calibrated: bool = True,
) -> Track:
    """A track sampled at `hz`, linearly interpolated between keyframes."""
    times = [k[0] for k in keyframes]
    xs, ys = [k[1] for k in keyframes], [k[2] for k in keyframes]
    points = []
    for i in range(round(times[0] * hz), round(times[-1] * hz) + 1):
        t = i / hz
        x, y = float(np.interp(t, times, xs)), float(np.interp(t, times, ys))
        px = (100.0 * x, 100.0 * y)
        points.append(
            TrackPoint(
                ts=EPOCH + timedelta(seconds=t),
                frame_idx=i,
                box_xyxy=(px[0] - 20.0, px[1] - 80.0, px[0] + 20.0, px[1]),
                foot_px=px,
                foot_m=(x, y) if calibrated else None,
                confidence=0.9,
            )
        )
    track_id = f"{camera_id}:{concept}:{next(_ids):08x}"
    return Track(track_id=track_id, camera_id=camera_id, concept=concept, points=tuple(points))


def stay(t0: float, t1: float, x: float, y: float) -> list[Keyframe]:
    return [(t0, x, y), (t1, x, y)]


def object_tracks() -> list[Track]:
    """Trucks, forklifts and pallets of the scenario (no people)."""
    outside_1, outside_3 = (2.0, 4.5), (8.0, 4.5)
    forklift_1 = [
        *stay(0, 49.9, *outside_1),
        (50.0, 1.0, 2.0),
        (79.9, 3.0, 2.0),
        *stay(80.0, 89.9, *outside_1),
        *stay(90.0, 109.9, 2.0, 2.0),
        *stay(110.0, 199.9, *outside_1),
        *stay(200.0, 219.9, 3.0, 6.0),
        *stay(220.0, 300.0, *outside_1),
    ]
    forklift_2 = [
        *stay(0, 59.9, *outside_3),
        *stay(60.0, 84.9, 8.0, 2.0),
        *stay(85.0, 249.9, *outside_3),
        *stay(250.0, 269.9, 8.0, 2.0),
        *stay(270.0, 300.0, *outside_3),
    ]
    return [
        along("truck", "cam1", stay(20, 140, 2.0, 1.5)),
        along("forklift", "cam1", forklift_1),
        along("pallet", "cam1", [*stay(60, 99.9, 3.0, 2.0), *stay(100, 105, *outside_1)]),
        along("pallet", "cam1", [*stay(150, 179.9, 5.0, 6.0), *stay(180, 185, 5.0, 8.0)]),
        along("truck", "cam3", stay(30, 90, 8.0, 1.5)),
        along("truck", "cam3", stay(200, 300, 8.0, 1.5)),
        along("forklift", "cam3", forklift_2),
    ]


def person_tracks() -> list[Track]:
    """People inside every station for the whole window."""
    return [
        along("person", "cam1", stay(0, 300, 1.0, 1.0)),
        along("person", "cam1", stay(100, 200, 8.0, 6.0)),
        along("person", "cam3", stay(0, 300, 7.0, 1.0)),
        along("worker", "cam3", stay(0, 300, 9.0, 2.0)),
    ]
