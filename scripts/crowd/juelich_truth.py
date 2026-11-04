"""Ground truth for a Jülich bottleneck clip: true people per crowd zone over clip time.

The Jülich "Crowds in front of bottlenecks" experiment (Forschungszentrum Jülich, 2018, doi
10.34735/ped.2018.1) publishes every participant's head position at 25 frames per second. This
turns one run's trajectories into the truth file `PUT /crowd/truth/{camera_id}` takes: for each
zone of a watch config (image polygons), the number of tracked heads inside it every `step`
seconds, and the zone's true area.

Image pixels are mapped to the trajectories' metres by a homography fitted to SAM's cap
detections against the trajectories (`score030.json` from `score_juelich.py`, median residual
2.3 cm); pass its path with `--fit`.

Usage:
    uv run python scripts/crowd/juelich_truth.py --traj 030_c_56_h0.txt --fit score030.json
        --watch samples/crowd/juelich-030/watch.json --out samples/crowd/juelich-030/truth.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

FPS = 25
CLIP_S = 64.8


def heads(path: Path) -> dict[int, list[tuple[float, float]]]:
    """Frame -> head positions in metres (`id frame x y z` lines; `#` comments skipped)."""
    out: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        _, frame, x, y, _z = line.split()
        out[int(frame)].append((float(x), float(y)))
    return out


def to_metres(h: np.ndarray, polygon: list[list[float]]) -> np.ndarray:
    """An image polygon in the trajectories' metres."""
    points = np.asarray(polygon, dtype=np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(points, h).reshape(-1, 2)


def inside(points: list[tuple[float, float]], polygon: np.ndarray) -> int:
    """How many points lie inside (or on) the polygon."""
    contour = polygon.astype(np.float32).reshape(-1, 1, 2)
    return sum(cv2.pointPolygonTest(contour, (x, y), False) >= 0 for x, y in points)


def main() -> None:
    """Write the truth file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--traj", type=Path, required=True)
    parser.add_argument("--fit", type=Path, required=True)
    parser.add_argument("--watch", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--step", type=float, default=0.2)
    args = parser.parse_args()
    by_frame = heads(args.traj)
    h = np.asarray(json.loads(args.fit.read_text())["fit"]["H"], dtype=np.float64)
    watch = json.loads(args.watch.read_text())
    times = [round(k * args.step, 2) for k in range(int(CLIP_S / args.step) + 1)]
    zones = {}
    for zone in watch["zones"]:
        polygon = to_metres(h, zone["polygon_px"])
        zones[zone["name"]] = {
            "area_m2": round(float(cv2.contourArea(polygon.astype(np.float32))), 3),
            "t": times,
            "people": [inside(by_frame.get(round(t * FPS), []), polygon) for t in times],
        }
    truth = {
        "source": (
            f"Jülich bottleneck experiment, run {args.traj.stem}: tracked head positions "
            "(doi 10.34735/ped.2018.1), zones mapped with a homography fitted to SAM cap "
            "detections (median residual 2.3 cm)"
        ),
        "licence": "Forschungszentrum Jülich pedestrian dynamics data archive; cite the DOI",
        "zones": zones,
        "view": {"t": times, "people": [len(by_frame.get(round(t * FPS), [])) for t in times]},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(truth, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
