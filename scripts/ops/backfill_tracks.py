"""Track recorded video after the fact, with the runtime's own image-mode tracker.

The runtime only segments cameras an active plan's rules cover. A camera recorded without one
(or footage an owner wants to ask about later) has video but no tracks, so `POST /ask` has no
object memory for it. This runs the same pipeline the live loop runs (SAM 3.1 keyframes on the
GPU pod, hosted as fallback, joined into tracks by ByteTrack) over a camera's recorded chunks,
in order, and appends the tracks to the track store; the Ask identity job then picks them up.

Run on the API pod, from the repository, with the API's environment:

    uv run --no-sync python scripts/ops/backfill_tracks.py phone-68 person [concept ...]
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime

from rulecam.bus import Bus
from rulecam.clock import SystemClock
from rulecam.config import get_settings
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.factory import _image_tracker, build_perception
from rulecam.runtime.timings import StageTimings


def main(argv: list[str]) -> int:
    """Backfill one camera's tracks for the given concepts; returns a process exit code."""
    if len(argv) < 2:  # noqa: PLR2004 - camera and at least one concept
        sys.stderr.write("usage: backfill_tracks.py CAMERA_ID CONCEPT [CONCEPT ...]\n")
        return 2
    camera_id, concepts = argv[0], argv[1:]
    settings = get_settings()
    clock, bus = SystemClock(), Bus()
    cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
    perception = build_perception(settings, cache=cache, clock=clock, bus=bus)
    runtime = _image_tracker(settings, perception, clock=clock, bus=bus, timings=StageTimings())
    chunks = perception.chunks.chunks(
        camera_id, datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC)
    )
    started, points = time.perf_counter(), 0
    for chunk in chunks:
        tracks = runtime.tracker.stitch(chunk, runtime.tracker.segment(chunk, concepts))
        points += sum(len(t.points) for t in tracks)
    sys.stdout.write(
        f"{camera_id}: {len(chunks)} chunks, {points} tracked points for {concepts} "
        f"in {time.perf_counter() - started:.0f} s\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
