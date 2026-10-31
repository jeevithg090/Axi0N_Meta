"""The Site Planner runner behind `POST /plans/requests` (task P2-06a; `api.plans.PlannerRunner`).

`PlannerService.submit` gives the request a fresh plan id and runs the fixed pipeline in one
background worker thread (one plan at a time), so the API answers at once and the UI follows
`planner.step`. Every run gets the owner's site config (`planner.site_config`): site name,
privacy mode, retention, roles, languages, and the cameras' click calibrations and drawn zones
(saved in the run's `input.json`). A finished plan is handed to `on_plan` (the API's plan
registry). A failed run is logged; the failing step has already been published as `failed`.

`ChunkFrames` supplies the survey's and probes' camera frames: up to 3 evenly spaced frames (at
least 1 s apart) of each camera chunk from the last `window_s` seconds (60 s by default,
`PLANNER_SURVEY_WINDOW_S`), extracted with one ffmpeg call per chunk under content-addressed
names (never a camera-supplied path). A frame's name is `<chunk digest>-f<k>.jpg`, so the
probe's sampler can spread its frames across chunks (`survey.frame_group`).
"""

from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

from rulecam.contracts import Chunk, Clock, RequestItem, SitePlan
from rulecam.log import get_logger
from rulecam.planner.pipeline import PipelineInput, PlannerPipeline
from rulecam.planner.site_config import SiteConfig

_log = get_logger(__name__)

SURVEY_WINDOW_S: Final = 60.0
FRAME_TIMEOUT_S: Final = 10.0
MAX_FRAMES_PER_CHUNK: Final = 3
MIN_FRAME_SPACING_S: Final = 1.0

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
        frames: FramesSource,
        on_plan: Callable[[SitePlan], object],
        site: SiteConfig | None = None,
        new_id: Callable[[], str] = new_plan_id,
    ) -> None:
        """Create the runner.

        Args:
            pipeline: The fixed-step pipeline.
            frames: Camera id -> recent frames, read when a run starts.
            on_plan: Receives each finished plan.
            site: The owner's site config (validated); the defaults when `None`.
            new_id: Makes plan ids.
        """
        self._pipeline = pipeline
        self._frames = frames
        self._on_plan = on_plan
        self._site = site or SiteConfig()
        self._zones = self._site.zones()
        self._calibrations = self._site.calibrations()
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
        site = self._site
        inputs = PipelineInput(
            plan_id=plan_id,
            site_name=site.site_name,
            request=items,
            camera_frames=frames,
            zones=self._zones,
            roles=site.roles,
            privacy_mode=site.privacy_mode,
            retention_days=site.retention_days,
            calibrations=self._calibrations,
            languages=site.languages,
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


Extract = Callable[[Path, Sequence[int], Path], None]
"""Writes the given frame indices of a chunk into a directory as `1.jpg`, `2.jpg`, ... in order."""


def frame_indices(
    chunk: Chunk,
    *,
    max_frames: int = MAX_FRAMES_PER_CHUNK,
    min_spacing_s: float = MIN_FRAME_SPACING_S,
) -> tuple[int, ...]:
    """Pick evenly spaced frame indices of a chunk, at least `min_spacing_s` apart.

    The chunk's duration is split into `k` equal parts (`k` = as many as fit at the spacing,
    at most `max_frames`, at least 1) and the frame at the middle of each part is taken, so a
    3 s chunk gives frames at 0.5 s, 1.5 s and 2.5 s.

    Args:
        chunk: The chunk (its `fps` and `n_frames` give the duration).
        max_frames: Most frames to take.
        min_spacing_s: Least time between two taken frames.

    Returns:
        Distinct frame indices, ascending.
    """
    duration_s = chunk.n_frames / chunk.fps
    k = max(1, min(max_frames, math.floor(duration_s / min_spacing_s)))
    picks = (min(chunk.n_frames - 1, int((i + 0.5) * duration_s / k * chunk.fps)) for i in range(k))
    return tuple(sorted(set(picks)))


def frames_args(chunk: Path, indices: Sequence[int], out_dir: Path) -> list[str]:
    """Build the one ffmpeg command that writes the chosen frames of a chunk as JPEGs.

    Args:
        chunk: The video chunk.
        indices: Frame indices to keep (0-based, ascending).
        out_dir: Directory for `1.jpg`, `2.jpg`, ... (one per index, in order).

    Returns:
        The argument list, program first (no shell).
    """
    select = "+".join(f"eq(n\\,{i})" for i in indices)
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(chunk),
        "-vf",
        f"select='{select}'",
        "-fps_mode",
        "passthrough",
        "-frames:v",
        str(len(indices)),
        "-q:v",
        "3",
        "-y",
        str(out_dir / "%d.jpg"),
    ]


def ffmpeg_frames(chunk: Path, indices: Sequence[int], out_dir: Path) -> None:
    """Write the chosen frames of `chunk` into `out_dir` with one ffmpeg run.

    Raises:
        subprocess.CalledProcessError: If ffmpeg fails.
        subprocess.TimeoutExpired: If it runs longer than 10 s.
    """
    subprocess.run(  # noqa: S603 - fixed argument list, no shell
        frames_args(chunk, indices, out_dir),
        check=True,
        capture_output=True,
        timeout=FRAME_TIMEOUT_S,
    )


class ChunkFrames:
    """Recent frames per camera, a few per chunk (a `FramesSource`)."""

    def __init__(
        self,
        chunks: ChunkLookup,
        *,
        clock: Clock,
        out_dir: Path,
        window_s: float = SURVEY_WINDOW_S,
        extract: Extract = ffmpeg_frames,
        max_frames_per_chunk: int = MAX_FRAMES_PER_CHUNK,
        min_spacing_s: float = MIN_FRAME_SPACING_S,
    ) -> None:
        """Create the source.

        Args:
            chunks: Recent chunks per camera.
            clock: Time source for the window.
            out_dir: Where extracted frames are stored.
            window_s: How far back to look.
            extract: Writes chosen frames of a chunk into a directory (one call per chunk).
            max_frames_per_chunk: Most frames taken from one chunk.
            min_spacing_s: Least time between two frames of one chunk.
        """
        self._chunks = chunks
        self._clock = clock
        self._out_dir = out_dir
        self._window = timedelta(seconds=window_s)
        self._extract = extract
        self._max_frames = max_frames_per_chunk
        self._min_spacing_s = min_spacing_s

    def __call__(self) -> dict[str, tuple[Path, ...]]:
        """Return camera id -> frames of the last window, oldest first (cameras with none omitted).

        A chunk whose frames cannot be extracted is skipped with a warning.
        """
        now = self._clock.now()
        self._out_dir.mkdir(parents=True, exist_ok=True)
        frames: dict[str, tuple[Path, ...]] = {}
        for camera_id in sorted(self._chunks.last_seen()):
            found = [
                frame
                for chunk in self._chunks.chunks(camera_id, now - self._window, now)
                for frame in self._frames(chunk)
            ]
            if found:
                frames[camera_id] = tuple(found)
        return frames

    def _frames(self, chunk: Chunk) -> list[Path]:
        indices = frame_indices(
            chunk, max_frames=self._max_frames, min_spacing_s=self._min_spacing_s
        )
        key = f"{chunk.camera_id}|{chunk.path}|{','.join(map(str, indices))}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        names = [self._out_dir / f"{digest}-f{k}.jpg" for k in range(len(indices))]
        if all(name.is_file() for name in names):
            return names
        partial = self._out_dir / f"{digest}.part"
        shutil.rmtree(partial, ignore_errors=True)
        written = [partial / f"{k + 1}.jpg" for k in range(len(indices))]
        try:
            partial.mkdir()
            self._extract(Path(chunk.path), indices, partial)
            complete = all(path.is_file() for path in written)
            if complete:
                for path, name in zip(written, names, strict=True):
                    path.replace(name)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("planner_frame_failed", chunk_id=chunk.chunk_id, error=repr(exc))
            return []
        finally:
            shutil.rmtree(partial, ignore_errors=True)
        if not complete:
            _log.warning("planner_frame_failed", chunk_id=chunk.chunk_id, error="missing frames")
            return []
        return names
