"""Connected cameras: add, run, watch, survey and remove them (behind `/connectors`).

`ConnectorManager.add` stores a camera (credentials apart), starts its `StreamWorker` and, as
soon as its first chunk is ready, runs the `CameraSurveyor` on a snapshot of it in the
background. The camera's name is the owner's, else Muse Spark's from the survey. Snapshots and
surveys also work for cameras the connectors did not add (a paired phone, `rulecam capture`):
they take that camera's newest chunk from the chunk index.

Workers start on the API's first tick (`tick`, once a second inside the app's lifespan), so
saved cameras reconnect after a restart without anything running at import time.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

import cv2
from pydantic import BaseModel, Field, SecretStr

from rulecam.bus import Bus
from rulecam.connectors.errors import SourceError, UnknownCameraError
from rulecam.connectors.sources import (
    ResolvedInput,
    SourceKind,
    classify,
    input_args,
    local_file,
    redact,
    resolve_public,
    split_credentials,
    with_credentials,
)
from rulecam.connectors.store import CameraRecord, CameraStore, Credentials
from rulecam.connectors.stream import StreamConfig, StreamHealth, StreamWorker
from rulecam.connectors.survey import CameraSurvey, CameraSurveyor
from rulecam.contracts import Chunk, Clock, Provenance
from rulecam.log import get_logger

_log = get_logger(__name__)

SURVEY_WORKERS: Final = 2
FIRST_CHUNK_WAIT_S: Final = 90.0
WAIT_POLL_S: Final = 0.5
PROVENANCE: Final = {
    SourceKind.PUBLIC: Provenance.PUBLIC,
    SourceKind.FILE: Provenance.UPLOAD,
}


class LatestChunks(Protocol):
    """The chunk index (`runtime.factory.ChunkIndex`): every camera's recent chunks."""

    def last_seen(self) -> dict[str, datetime]:
        """Each camera's latest chunk start."""
        ...

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        """The camera's chunks overlapping `[t0, t1]`."""
        ...


class AddCamera(BaseModel):
    """Body of `POST /connectors/cameras`."""

    url: str = Field(min_length=1, max_length=2000, description="Camera URL or clip path.")
    name: str = Field(default="", max_length=60, pattern=r"^[^\x00-\x1f\x7f]*$")
    username: str | None = Field(default=None, max_length=128)
    password: SecretStr | None = Field(default=None, max_length=256)
    loop: bool = Field(default=True, description="Loop a clip forever (a virtual camera).")
    survey: bool = Field(default=True, description="Run the first-look survey once it is live.")


class CameraView(BaseModel):
    """One connected camera as served: no URL secrets, its health and its survey."""

    camera_id: str
    name: str
    named_by: str
    kind: SourceKind
    display_url: str
    has_credentials: bool
    loop: bool
    provenance: Provenance
    created_at: datetime
    blocked: str | None = None
    health: StreamHealth
    survey: CameraSurvey | None = None


@dataclass(frozen=True)
class ConnectorSettings:
    """Where the connectors keep their files and how cameras are chunked.

    Attributes:
        root: `<data>/connectors`.
        fps: Chunk frame rate.
        chunk_seconds: Chunk length.
        max_width: Wider frames are scaled down.
        keep_chunks: Chunk files kept per camera.
        clip_roots: Folders a clip path may point into (uploads always may).
    """

    root: Path
    fps: float = 10.0
    chunk_seconds: float = 3.0
    max_width: int = 1280
    keep_chunks: int = 1200
    clip_roots: tuple[Path, ...] = field(default_factory=tuple)


def last_frame(chunk_path: Path, out: Path) -> Path | None:
    """Decode a chunk's last frame to a JPEG (cached by name)."""
    if out.is_file():
        return out
    capture = cv2.VideoCapture(str(chunk_path))
    try:
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if count > 1:
            capture.set(cv2.CAP_PROP_POS_FRAMES, count - 1)
        ok, frame = capture.read()
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return out


class ConnectorManager:
    """Owns every connector camera's worker, snapshots and surveys."""

    def __init__(
        self,
        settings: ConnectorSettings,
        *,
        bus: Bus,
        clock: Clock,
        surveyor: CameraSurveyor | None = None,
        chunks: LatestChunks | None = None,
        resolve_public_url: Callable[[str], ResolvedInput] = resolve_public,
    ) -> None:
        """Create the manager; saved cameras start on the first `tick()`.

        Args:
            settings: Folders and chunking.
            bus: Where chunks are published.
            clock: Timestamps.
            surveyor: The first-look survey; `None` (no Spark key) marks surveys unavailable.
            chunks: The chunk index, for cameras the connectors did not add.
            resolve_public_url: yt-dlp resolution of public stream pages (tests replace it).
        """
        self._settings = settings
        self._bus = bus
        self._clock = clock
        self._surveyor = surveyor
        self._chunks = chunks
        self._resolve_public = resolve_public_url
        self._store = CameraStore(settings.root)
        self._workers: dict[str, StreamWorker] = {}
        self._surveys: dict[str, CameraSurvey] = {}
        self._lock = threading.Lock()
        self._restored = False
        self._pool = ThreadPoolExecutor(max_workers=SURVEY_WORKERS, thread_name_prefix="survey")
        self._load_surveys()

    @property
    def root(self) -> Path:
        """The connectors folder."""
        return self._settings.root

    # ---- cameras ------------------------------------------------------------------------

    def add(self, request: AddCamera) -> CameraView:
        """Store a camera, start it and (by default) survey it once its first chunk is ready.

        Raises:
            SourceError: For an unusable URL or clip path.
        """
        url, user, password = split_credentials(request.url)
        kind = classify(url)
        if kind is SourceKind.FILE:
            url = str(local_file(url, self._clip_roots()))
        username = request.username or user
        secret = request.password.get_secret_value() if request.password else password
        credentials = (
            Credentials(username=username, password=SecretStr(secret) if secret else None)
            if username
            else None
        )
        return self._create(
            kind,
            url,
            name=request.name,
            credentials=credentials,
            loop=request.loop,
            survey=request.survey,
        )

    def add_clip(self, path: Path, name: str = "", *, survey: bool = True) -> CameraView:
        """A stored upload, looped forever as a virtual camera."""
        return self._create(SourceKind.FILE, str(path), name=name, survey=survey)

    def cameras(self) -> list[CameraView]:
        """Every connector camera, oldest first."""
        return [self._view(record) for record in self._store.records()]

    def camera(self, camera_id: str) -> CameraView:
        """One connector camera.

        Raises:
            UnknownCameraError: When there is none with that id.
        """
        return self._view(self._record(camera_id))

    def remove(self, camera_id: str) -> None:
        """Stop a camera and forget it (its chunks stay on disk for evidence).

        Raises:
            UnknownCameraError: When there is none with that id.
        """
        self._record(camera_id)
        with self._lock:
            worker = self._workers.pop(camera_id, None)
        if worker is not None:
            worker.stop()
        self._store.remove(camera_id)
        _log.info("camera_removed", camera_id=camera_id)

    def restart(self, camera_id: str) -> CameraView:
        """Reconnect a camera now (also clears a reconnect wait).

        Raises:
            UnknownCameraError: When there is none with that id.
        """
        record = self._record(camera_id)
        with self._lock:
            worker = self._workers.pop(camera_id, None)
        if worker is not None:
            worker.stop()
        if record.blocked is None:
            self._start(record)
        return self._view(record)

    def tick(self) -> None:
        """Start saved cameras on the first call; restart any worker whose thread died."""
        if not self._restored:
            self._restored = True
            for saved in self._store.records():
                if saved.blocked is None:
                    self._start(saved)
            return
        with self._lock:
            dead = [cid for cid, w in self._workers.items() if not w.running]
        for camera_id in dead:
            record = self._store.get(camera_id)
            worker = self._workers.get(camera_id)
            ended = worker is not None and worker.health().state == "ended"
            if record is not None and record.blocked is None and not ended:
                self._start(record)

    def close(self) -> None:
        """Stop every worker."""
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ---- snapshots and surveys ----------------------------------------------------------

    def latest_chunk(self, camera_id: str) -> Chunk | None:
        """The newest chunk of any camera: a connector's worker, else the chunk index."""
        with self._lock:
            worker = self._workers.get(camera_id)
        chunk = worker.latest_chunk() if worker is not None else None
        if chunk is None and self._chunks is not None:
            last = self._chunks.last_seen().get(camera_id)
            if last is not None:
                found = self._chunks.chunks(camera_id, last, last + timedelta(seconds=1))
                chunk = max(found, key=lambda c: c.start_ts) if found else None
        return chunk if chunk is not None and Path(chunk.path).is_file() else None

    def snapshot(self, camera_id: str) -> Path | None:
        """A JPEG of any camera's newest frame, or `None` before its first chunk."""
        chunk = self.latest_chunk(camera_id)
        if chunk is None:
            return None
        return last_frame(Path(chunk.path), self.root / "snapshots" / f"{chunk.chunk_id}.jpg")

    def survey(self, camera_id: str) -> CameraSurvey | None:
        """Any camera's latest survey (running or finished)."""
        with self._lock:
            return self._surveys.get(camera_id)

    def start_survey(self, camera_id: str) -> CameraSurvey:
        """Survey any camera in the background, once it has a chunk.

        Returns:
            The survey's state now (`waiting`, or `unavailable` without Spark).
        """
        if self._surveyor is None:
            survey = CameraSurvey(
                camera_id=camera_id,
                status="unavailable",
                error="the first look needs Muse Spark and SAM 3.1 (META_API_KEY)",
            )
            self._set_survey(survey)
            return survey
        current = self.survey(camera_id)
        if current is not None and current.status in ("waiting", "describing", "detecting"):
            return current
        survey = CameraSurvey(camera_id=camera_id, status="waiting", started_at=self._clock.now())
        self._set_survey(survey)
        self._pool.submit(self._run_survey, camera_id)
        return survey

    def survey_image(self, name: str) -> Path | None:
        """A snapshot or marked image a survey names."""
        for folder in ("snapshots", "surveys"):
            path = self.root / folder / name
            if Path(name).name == name and path.suffix == ".jpg" and path.is_file():
                return path
        return None

    # ---- internals ----------------------------------------------------------------------

    def _clip_roots(self) -> list[Path]:
        return [self.root / "uploads", *self._settings.clip_roots]

    def _record(self, camera_id: str) -> CameraRecord:
        record = self._store.get(camera_id)
        if record is None:
            msg = f"no connected camera {camera_id!r}"
            raise UnknownCameraError(msg)
        return record

    def _create(
        self,
        kind: SourceKind,
        url: str,
        *,
        name: str,
        credentials: Credentials | None = None,
        loop: bool = True,
        survey: bool = True,
    ) -> CameraView:
        camera_id = self._store.new_id()
        default_name = Path(url).stem if kind is SourceKind.FILE else redact(url)
        record = CameraRecord(
            camera_id=camera_id,
            name=name.strip() or default_name[:60],
            named_by="owner" if name.strip() else "source",
            kind=kind,
            url=url,
            loop=loop,
            provenance=PROVENANCE.get(kind, Provenance.RECORDED),
            created_at=self._clock.now(),
        )
        self._store.put(record, credentials)
        _log.info("camera_added", camera_id=camera_id, kind=kind.value, url=redact(url))
        self._start(record)
        if survey:
            self.start_survey(camera_id)
        return self._view(record)

    def _resolver(self, record: CameraRecord) -> Callable[[], ResolvedInput]:
        credentials = self._store.credentials(record.camera_id)
        password = (
            credentials.password.get_secret_value()
            if credentials is not None and credentials.password
            else None
        )
        username = credentials.username if credentials is not None else None

        def resolve() -> ResolvedInput:
            if record.kind is SourceKind.PUBLIC:
                return self._resolve_public(record.url)
            if record.kind is SourceKind.FILE:
                if not Path(record.url).is_file():
                    msg = f"the clip {Path(record.url).name} is gone"
                    raise SourceError(msg)
                return ResolvedInput(args=input_args(SourceKind.FILE, record.url, loop=record.loop))
            full = with_credentials(record.url, username, password)
            return ResolvedInput(args=input_args(record.kind, full))

        return resolve

    def _start(self, record: CameraRecord) -> None:
        credentials = self._store.credentials(record.camera_id)
        config = StreamConfig(
            camera_id=record.camera_id,
            out_dir=self.root / "chunks" / record.camera_id,
            provenance=record.provenance,
            fps=self._settings.fps,
            chunk_seconds=self._settings.chunk_seconds,
            max_width=self._settings.max_width,
            keep_chunks=self._settings.keep_chunks,
            ends=record.kind is SourceKind.FILE and not record.loop,
        )
        worker = StreamWorker(
            config,
            self._resolver(record),
            bus=self._bus,
            clock=self._clock,
            secrets=credentials.secrets() if credentials is not None else (),
        )
        with self._lock:
            old = self._workers.get(record.camera_id)
            self._workers[record.camera_id] = worker
        if old is not None:
            old.stop()
        worker.start()

    def _view(self, record: CameraRecord) -> CameraView:
        with self._lock:
            worker = self._workers.get(record.camera_id)
            survey = self._surveys.get(record.camera_id)
        health = worker.health() if worker is not None else StreamHealth(state="stopped")
        display = Path(record.url).name if record.kind is SourceKind.FILE else redact(record.url)
        return CameraView(
            camera_id=record.camera_id,
            name=record.name,
            named_by=record.named_by,
            kind=record.kind,
            display_url=display,
            has_credentials=self._store.credentials(record.camera_id) is not None,
            loop=record.loop,
            provenance=record.provenance,
            created_at=record.created_at,
            blocked=record.blocked,
            health=health,
            survey=survey,
        )

    def _set_survey(self, survey: CameraSurvey) -> None:
        with self._lock:
            self._surveys[survey.camera_id] = survey
        if survey.status in ("done", "failed", "blocked", "unavailable"):
            folder = self.root / "surveys"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{survey.camera_id}.json").write_text(
                survey.model_dump_json(indent=2), "utf-8"
            )

    def _load_surveys(self) -> None:
        folder = self.root / "surveys"
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                survey = CameraSurvey.model_validate_json(path.read_text("utf-8"))
            except ValueError:
                continue
            self._surveys[survey.camera_id] = survey

    def _wait_for_snapshot(self, camera_id: str) -> tuple[Chunk, Path] | None:
        deadline = time.monotonic() + FIRST_CHUNK_WAIT_S
        while time.monotonic() < deadline:
            chunk = self.latest_chunk(camera_id)
            if chunk is not None:
                shot = self.snapshot(camera_id)
                if shot is not None:
                    return chunk, shot
            time.sleep(WAIT_POLL_S)
        return None

    def _run_survey(self, camera_id: str) -> None:
        surveyor = self._surveyor
        if surveyor is None:
            return
        waited = time.monotonic()
        ready = self._wait_for_snapshot(camera_id)
        if ready is None:
            self._set_survey(
                CameraSurvey(
                    camera_id=camera_id,
                    status="failed",
                    error=f"no video from the camera within {FIRST_CHUNK_WAIT_S:.0f} s",
                    finished_at=self._clock.now(),
                )
            )
            return
        chunk, shot = ready
        wait_s = round(time.monotonic() - waited, 2)

        def progress(survey: CameraSurvey) -> None:
            self._set_survey(
                survey.model_copy(update={"timings": {"first_frame_s": wait_s, **survey.timings}})
            )

        try:
            survey = surveyor.run(camera_id, shot, chunk_id=chunk.chunk_id, on_step=progress)
        except Exception as exc:  # a pool thread must never leave a survey stuck mid-way
            _log.exception("camera_survey_crashed", camera_id=camera_id)
            failed = CameraSurvey(
                camera_id=camera_id,
                status="failed",
                error=f"the first look stopped: {type(exc).__name__}",
                finished_at=self._clock.now(),
            )
            self._set_survey(failed)
            return
        self._after_survey(survey)

    def _after_survey(self, survey: CameraSurvey) -> None:
        record = self._store.get(survey.camera_id)
        if record is None or survey.look is None:
            return
        if survey.status == "blocked":
            self._store.put(record.model_copy(update={"blocked": survey.error}))
            with self._lock:
                worker = self._workers.pop(record.camera_id, None)
            if worker is not None:
                worker.stop()
            _log.warning("camera_blocked_private_space", camera_id=record.camera_id)
            return
        if record.named_by != "owner" and survey.look.name.strip():
            self._store.put(
                record.model_copy(update={"name": survey.look.name.strip(), "named_by": "spark"})
            )


def clip_roots(data_dir: Path, extra: Iterable[Path] = ()) -> tuple[Path, ...]:
    """Folders a clip path may point into: the data folder, the working folder, any extras."""
    return (data_dir, Path.cwd(), *extra)
