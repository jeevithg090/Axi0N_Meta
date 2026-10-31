"""The Site Planner runner behind `POST /plans/requests` (task P2-06a; `api.plans.PlannerRunner`).

`PlannerService.submit` gives the request a fresh plan id and runs the fixed pipeline in one
background worker thread (one plan at a time), so the API answers at once and the UI follows
`planner.step`. A finished plan is handed to `on_plan` (the API's plan registry). A failed run is
logged; the failing step has already been published as `failed`.

`ChunkFrames` supplies the survey's camera frames: the last frame of each camera's chunks from
the last 60 s, extracted with ffmpeg under content-addressed names (never a camera-supplied
path).
"""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

from rulecam.capture.pair_phone.media import last_frame_args
from rulecam.contracts import Chunk, Clock, RequestItem, SitePlan
from rulecam.contracts.core import PrivacyMode, Zone
from rulecam.log import get_logger
from rulecam.planner.pipeline import PipelineInput, PlannerPipeline

_log = get_logger(__name__)

SURVEY_WINDOW_S: Final = 60.0
FRAME_TIMEOUT_S: Final = 10.0

FramesSource = Callable[[], Mapping[str, Sequence[Path]]]


def new_plan_id() -> str:
    """A fresh plan id, e.g. `plan-1f2e3d4c5b6a`."""
    return f"plan-{uuid.uuid4().hex[:12]}"


class PlannerService:
    """Runs submitted requests through the pipeline, one at a time, off the request thread."""

    def __init__(
        self,
        pipeline: PlannerPipeline,
        *,
        site_name: str,
        frames: FramesSource,
        on_plan: Callable[[SitePlan], object],
        zones: Sequence[Zone] = (),
        roles: Mapping[str, str] | None = None,
        privacy_mode: PrivacyMode = PrivacyMode.STANDARD,
        new_id: Callable[[], str] = new_plan_id,
    ) -> None:
        """Create the runner.

        Args:
            pipeline: The fixed-step pipeline.
            site_name: The site every plan is for.
            frames: Camera id -> recent frames, read when a run starts.
            on_plan: Receives each finished plan.
            zones: Zones declared for the site.
            roles: Role -> alert channel.
            privacy_mode: The site's privacy mode.
            new_id: Makes plan ids.
        """
        self._pipeline = pipeline
        self._site_name = site_name
        self._frames = frames
        self._on_plan = on_plan
        self._zones = tuple(zones)
        self._roles = dict(roles or {})
        self._privacy_mode = privacy_mode
        self._new_id = new_id
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="planner")

    def submit(self, items: Sequence[RequestItem]) -> str:
        """Queue a planning run and return its plan id.

        Args:
            items: The request; file items already point at stored uploads.

        Returns:
            The new plan's id.
        """
        plan_id = self._new_id()
        future = self._executor.submit(self._run, plan_id, tuple(items))
        future.add_done_callback(lambda done: _log_failure(plan_id, done))
        _log.info("planner_submitted", plan_id=plan_id, items=len(items))
        return plan_id

    def shutdown(self) -> None:
        """Wait for the queued runs, then stop the worker."""
        self._executor.shutdown(wait=True)

    def _run(self, plan_id: str, items: tuple[RequestItem, ...]) -> SitePlan:
        frames = {cam: tuple(paths) for cam, paths in self._frames().items()}
        inputs = PipelineInput(
            plan_id=plan_id,
            site_name=self._site_name,
            request=items,
            camera_frames=frames,
            zones=self._zones,
            roles=self._roles,
            privacy_mode=self._privacy_mode,
        )
        plan = self._pipeline.run(inputs)
        self._on_plan(plan)
        return plan


def _log_failure(plan_id: str, done: Future[SitePlan]) -> None:
    error = done.exception()
    if error is not None:
        _log.error("planner_run_failed", plan_id=plan_id, error=f"{type(error).__name__}: {error}")


class ChunkLookup(Protocol):
    """The part of `runtime.factory.ChunkIndex` the frame source needs."""

    def last_seen(self) -> dict[str, datetime]:
        """Each camera's latest chunk start."""
        ...

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        """The camera's chunks overlapping `[t0, t1]`, oldest first."""
        ...


Extract = Callable[[Path, Path], None]


def ffmpeg_last_frame(chunk: Path, frame: Path) -> None:
    """Write the last frame of `chunk` to `frame` (JPEG) with ffmpeg.

    Raises:
        subprocess.CalledProcessError: If ffmpeg fails.
        subprocess.TimeoutExpired: If it runs longer than 10 s.
    """
    subprocess.run(  # noqa: S603 - fixed argument list, no shell
        last_frame_args(chunk, frame), check=True, capture_output=True, timeout=FRAME_TIMEOUT_S
    )


class ChunkFrames:
    """Recent frames per camera, one per chunk (a `FramesSource`)."""

    def __init__(
        self,
        chunks: ChunkLookup,
        *,
        clock: Clock,
        out_dir: Path,
        window_s: float = SURVEY_WINDOW_S,
        extract: Extract = ffmpeg_last_frame,
    ) -> None:
        """Create the source.

        Args:
            chunks: Recent chunks per camera.
            clock: Time source for the window.
            out_dir: Where extracted frames are stored.
            window_s: How far back to look.
            extract: Writes a chunk's last frame to a path.
        """
        self._chunks = chunks
        self._clock = clock
        self._out_dir = out_dir
        self._window = timedelta(seconds=window_s)
        self._extract = extract

    def __call__(self) -> dict[str, tuple[Path, ...]]:
        """Return camera id -> frames of the last window, oldest first (cameras with none omitted).

        A chunk whose frame cannot be extracted is skipped with a warning.
        """
        now = self._clock.now()
        self._out_dir.mkdir(parents=True, exist_ok=True)
        frames: dict[str, tuple[Path, ...]] = {}
        for camera_id in sorted(self._chunks.last_seen()):
            found = [
                frame
                for chunk in self._chunks.chunks(camera_id, now - self._window, now)
                if (frame := self._frame(chunk)) is not None
            ]
            if found:
                frames[camera_id] = tuple(found)
        return frames

    def _frame(self, chunk: Chunk) -> Path | None:
        digest = hashlib.sha256(f"{chunk.camera_id}|{chunk.path}".encode()).hexdigest()[:32]
        frame = self._out_dir / f"{digest}.jpg"
        if frame.is_file():
            return frame
        partial = frame.with_suffix(".part.jpg")
        try:
            self._extract(Path(chunk.path), partial)
            partial.replace(frame)
        except (OSError, subprocess.SubprocessError) as exc:
            partial.unlink(missing_ok=True)
            _log.warning("planner_frame_failed", chunk_id=chunk.chunk_id, error=repr(exc))
            return None
        return frame
