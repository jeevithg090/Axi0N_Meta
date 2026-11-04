"""Crowd memory: each camera's crowd measured every few seconds, for the warden and for Ask.

Every `every_s` seconds, for each camera with new video, `CrowdMemory` measures the newest
recorded frame with `CrowdScanner` (motion from the frame `gap_s` before it) and appends one row
per zone to a JSON-lines file, which `POST /ask` loads as the `crowd_readings` table. The latest
readings per camera also stay in memory for the warden (`crowd.warden`) and `GET /crowd/status`.

A density needs ground metres, so each camera's calibration comes from, in order (`Calibrations`):
floor points the operator marked with the watch (`owner`), the active plan's calibration (the
owner's or the planner's), MoGe-2's point map with SAM's floor (add-on 3.5), or the crowd itself:
the people SAM found are the ruler (`calibrate_people`; only for cameras that see people from the
side). The reading says which one it used and its error, so a model reading it can weigh it.

Only live cameras are measured: a camera whose newest video is older than `max_age_s` is skipped.

`tick` never blocks: one scan at a time runs on its own worker thread, as the caption memory does.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

import cv2

from rulecam.ask.vision import Frame, FrameGrabber
from rulecam.contracts import Calibration, Clock
from rulecam.crowd.scan import CrowdReading, CrowdScanner
from rulecam.crowd.signals import Box, foot_points, inside
from rulecam.crowd.zones import CrowdZone
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.perception.calibration import calibrate_people
from rulecam.perception.errors import PerceptionError

_log = get_logger(__name__)

DEFAULT_GAP_S: Final = 0.5  # seconds between the two frames motion is measured from
KEEP_READINGS: Final = 720  # per camera in memory: an hour at one reading every 5 s
NEWEST_OFFSET_S: Final = 1.0  # the newest chunk's start + this is a recorded instant
MODEL_RETRY_S: Final = 300.0  # a failed MoGe-2 calibration is tried again after this
DEFAULT_MAX_AGE_S: Final = 120.0  # a camera with no newer video than this is not measured

PlanCalibrations = Callable[[], dict[str, Calibration]]
"""Camera id -> the active plan's calibration."""
ModelCalibration = Callable[[str, Path], Calibration | None]
"""MoGe-2 + SAM floor on one frame (`planner.geometry.model_calibration`), or `None`."""


class Calibrations:
    """Each camera's ground calibration and where it came from (plan, MoGe-2, the crowd)."""

    def __init__(
        self,
        *,
        plan: PlanCalibrations = dict,
        model: ModelCalibration | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the lookup.

        Args:
            plan: The active plan's calibrations per camera.
            model: MoGe-2 calibration from a frame, if the GPU pod has it.
            clock: Current time, for retrying a failed model calibration.
        """
        self._plan = plan
        self._model = model
        self._clock = clock
        self._found: dict[str, tuple[Calibration, str]] = {}
        self._owner: dict[str, Calibration] = {}
        self._tried: dict[str, datetime] = {}
        self._running: dict[str, Future[Calibration | None]] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crowd-calibration")
        self._lock = threading.Lock()

    def set_owner(self, calibration: Calibration) -> None:
        """Use floor points the operator marked for this camera (wins over every other source)."""
        with self._lock:
            self._owner[calibration.camera_id] = calibration

    def get(
        self, camera_id: str, frame: Frame, boxes: Sequence[Box]
    ) -> tuple[Calibration, str] | None:
        """The camera's calibration, trying the plan, then MoGe-2, then the crowd's people.

        Args:
            camera_id: The camera.
            frame: A recent frame (for MoGe-2 and the frame size).
            boxes: People SAM found on that frame (the ruler of last resort).

        Returns:
            `(calibration, source)` with source `owner`, `plan`, `moge2` or `people`, or
            `None`.
        """
        with self._lock:
            owner = self._owner.get(camera_id)
        if owner is not None:
            return owner, "owner"
        planned = self._plan().get(camera_id)
        if planned is not None:
            return planned, "plan"
        with self._lock:
            known = self._found.get(camera_id)
        if known is not None and known[1] == "moge2":
            return known
        model = self._from_model(camera_id, frame)
        if model is not None:
            return model
        if known is not None:
            return known
        return self._from_people(camera_id, frame, boxes)

    def _from_model(self, camera_id: str, frame: Frame) -> tuple[Calibration, str] | None:
        """MoGe-2's calibration once it is ready (`None` until then).

        It runs on its own thread, never blocking a reading (a cold GPU worker can take minutes),
        and is retried `MODEL_RETRY_S` after a failure.
        """
        if self._model is None:
            return None
        now = self._clock() if self._clock is not None else frame.at
        with self._lock:
            running = self._running.get(camera_id)
            if running is not None and not running.done():
                return None
            if running is None:
                tried = self._tried.get(camera_id)
                if tried is not None and (now - tried).total_seconds() < MODEL_RETRY_S:
                    return None
                self._tried[camera_id] = now
                self._running[camera_id] = self._pool.submit(self._model, camera_id, frame.path)
                return None
            del self._running[camera_id]
        found = running.result()
        if found is None:
            return None
        with self._lock:
            self._found[camera_id] = (found, "moge2")
        return found, "moge2"

    def _from_people(
        self, camera_id: str, frame: Frame, boxes: Sequence[Box]
    ) -> tuple[Calibration, str] | None:
        image = cv2.imread(str(frame.path))
        if image is None:
            return None
        height, width = image.shape[:2]
        whole = [b[:4] for b in boxes if b[1] > 1 and b[3] < height - 1]  # not cut by the frame
        try:
            found = calibrate_people(camera_id, whole, width, height)
        except PerceptionError as exc:
            _log.info("crowd_people_calibration_failed", camera_id=camera_id, error=str(exc))
            return None
        with self._lock:
            self._found[camera_id] = (found, "people")
        return found, "people"


class CrowdMemory:
    """Measures each camera's crowd every `every_s` seconds into a JSON-lines file."""

    def __init__(
        self,
        *,
        frames: FrameGrabber,
        scanner: CrowdScanner,
        calibrations: Calibrations,
        path: Path,
        every_s: float,
        gap_s: float = DEFAULT_GAP_S,
        cameras: Callable[[], Collection[str]] | None = None,
        zones: Callable[[str], Sequence[CrowdZone]] | None = None,
        counting: Callable[[str], tuple[str | None, int | None]] | None = None,
        clock: Clock | None = None,
        max_age_s: float = DEFAULT_MAX_AGE_S,
    ) -> None:
        """Create the memory.

        Args:
            frames: Decodes recorded frames.
            scanner: Measures one frame pair.
            calibrations: Each camera's ground calibration.
            path: The JSON-lines file readings are appended to.
            every_s: Seconds between readings of one camera.
            gap_s: Seconds between the two frames motion is measured from.
            cameras: The cameras to measure (default: every camera with video).
            zones: Each camera's named zones (default: none, only the whole view).
            counting: Each camera's concept and tiles per side (`None`s: the scanner's).
            clock: Current time; with it, cameras without video newer than `max_age_s` are
                skipped.
            max_age_s: Seconds after which a camera's newest video is too old to measure.
        """
        self._frames = frames
        self._scanner = scanner
        self._calibrations = calibrations
        self._path = path
        self._every = timedelta(seconds=every_s)
        self._gap = timedelta(seconds=gap_s)
        self._cameras = cameras
        self._zones = zones
        self._counting = counting
        self._clock = clock
        self._max_age = timedelta(seconds=max_age_s)
        self._done: dict[str, datetime] = {}
        self._readings: dict[str, deque[CrowdReading]] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crowd-memory")
        self._running: Future[None] | None = None
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """The JSON-lines file of readings (one row per zone)."""
        return self._path

    @property
    def heatmaps(self) -> Path:
        """Where the readings' heatmaps are written."""
        return self._scanner.out_dir

    @property
    def calibrations(self) -> Calibrations:
        """Each camera's ground calibration."""
        return self._calibrations

    def tick(self) -> None:
        """Start the next due reading, if none is running (returns at once)."""
        if self._running is not None and not self._running.done():
            return
        due = self._due()
        if due is not None:
            self._running = self._pool.submit(self._record, *due)

    def latest(self, camera_id: str) -> CrowdReading | None:
        """The camera's newest reading."""
        with self._lock:
            kept = self._readings.get(camera_id)
            return kept[-1] if kept else None

    def readings(self, camera_id: str, since: datetime | None = None) -> list[CrowdReading]:
        """The camera's readings kept in memory, oldest first (only those after `since`)."""
        with self._lock:
            kept = list(self._readings.get(camera_id, ()))
        return [r for r in kept if since is None or r.at > since]

    def cameras(self) -> list[str]:
        """Cameras with at least one reading."""
        with self._lock:
            return sorted(k for k, v in self._readings.items() if v)

    def measure(
        self,
        camera_id: str,
        at: datetime,
        zones: Sequence[CrowdZone] = (),
        *,
        concept: str | None = None,
        grid: int | None = None,
    ) -> CrowdReading:
        """Measure one recorded instant (not stored): Ask's `crowd_scan` tool.

        Raises:
            ValueError: Nothing is recorded at that instant.
            OSError: The frame cannot be read.
            RulecamError: SAM fails.
        """
        found = self._frames.frames(camera_id, [at - self._gap, at])
        after = next((f for f in found if f.at == at), None)
        if after is None:
            msg = f"no recorded frame of {camera_id} at {at.isoformat()}"
            raise ValueError(msg)
        before = next((f for f in found if f.at < at), None)
        concept = concept or self._scanner.concept
        grid = grid or self._scanner.grid
        boxes = self._scanner.detect(after, concept, grid)
        calibration = self._calibrations.get(camera_id, after, boxes)
        return self._scanner.scan(
            after, before, calibration, zones, concept=concept, grid=grid, boxes=boxes
        )

    def count(
        self,
        camera_id: str,
        at: datetime,
        concept: str,
        *,
        polygon: Sequence[tuple[float, float]] | None = None,
        grid: int | None = None,
    ) -> int:
        """How many `concept` SAM 3.1 finds at one recorded instant (inside `polygon`, if given).

        Raises:
            ValueError: Nothing is recorded at that instant.
            OSError: The frame cannot be read.
            RulecamError: SAM fails.
        """
        found = self._frames.frames(camera_id, [at])
        if not found:
            msg = f"no recorded frame of {camera_id} at {at.isoformat()}"
            raise ValueError(msg)
        boxes = self._scanner.detect(found[0], concept, grid or self._scanner.grid)
        if polygon is None:
            return len(boxes)
        return int(inside(polygon, foot_points(boxes)).sum()) if boxes else 0

    def _due(self) -> tuple[str, datetime] | None:
        """The live camera whose reading is most overdue (never read first)."""
        wanted = set(self._cameras()) if self._cameras is not None else None
        now = self._clock.now() if self._clock is not None else None
        due: list[tuple[datetime | None, str, datetime]] = []
        for camera_id, (_, last) in sorted(self._frames.cameras().items()):
            if wanted is not None and camera_id not in wanted:
                continue
            if now is not None and now - last > self._max_age:
                continue
            at = last + timedelta(seconds=NEWEST_OFFSET_S)
            done = self._done.get(camera_id)
            if done is None or at - done >= self._every:
                due.append((done, camera_id, at))
        if not due:
            return None
        _, camera_id, at = min(due, key=lambda d: (d[0] is not None, d[0] or d[2], d[1]))
        return camera_id, at

    def _record(self, camera_id: str, at: datetime) -> None:
        self._done[camera_id] = at
        zones = self._zones(camera_id) if self._zones is not None else ()
        concept, grid = self._counting(camera_id) if self._counting is not None else (None, None)
        try:
            reading = self.measure(camera_id, at, zones, concept=concept, grid=grid)
        except (ValueError, OSError, RulecamError) as exc:
            _log.warning("crowd_reading_failed", camera_id=camera_id, error=str(exc)[:200])
            return
        with self._lock:
            kept = self._readings.setdefault(camera_id, deque(maxlen=KEEP_READINGS))
            kept.append(reading)
            with self._path.open("a", encoding="utf-8") as out:
                for row in rows(reading):
                    out.write(json.dumps(row) + "\n")
        view = reading.zones[0]
        _log.info(
            "crowd_reading",
            camera_id=camera_id,
            at=at.isoformat(),
            people=reading.people,
            density=view.density_ppm2,
            peak=reading.peak_density_ppm2,
            speed=view.speed_mps,
            seconds=reading.seconds,
        )


def rows(reading: CrowdReading) -> list[dict[str, object]]:
    """One `crowd_readings` row per zone of a reading."""
    peak_row, peak_col = reading.peak_cell if reading.peak_cell is not None else (None, None)
    return [
        {
            "camera_id": reading.camera_id,
            "ts": reading.at.isoformat(),
            **zone.model_dump(),
            "peak_density_ppm2": reading.peak_density_ppm2,
            "peak_row": peak_row,
            "peak_col": peak_col,
            "calibration": reading.calibration,
            "calibration_err_m": reading.calibration_err_m,
            "concept": reading.concept,
            "heatmap": reading.heatmap,
        }
        for zone in reading.zones
    ]
