"""`/wearables`: smart glasses as a Rulecam camera (owner-only, like the other site tools).

- `GET /wearables/status`: which models and voice answer, the modes, and Glimmer's state (asking
  pings Glimmer in the background, at most once a minute, so a cold worker starts early).
- `POST /wearables/look`: one point-of-view frame (multipart `image`), with an optional typed
  `question` or a push-to-talk `audio` WAV, the `mode` (aisle, warehouse, crowd, general), the
  frame's `source` and an open `walk_id`. Returns a `PovAnswer` ready to speak.
- `POST /wearables/walks`, `GET /wearables/walks/{id}`, `POST /wearables/walks/{id}/finish`: a
  safety walk; finishing it writes the report and chains its hazards as evidence.
- `GET /wearables/frames/{name}`, `GET /wearables/audio/{name}`: the frames, marked frames and
  spoken WAVs an answer names.
- `GET /wearables/jobs/{job_id}`: a look or walk report sent with `wait=false`. A phone behind a
  proxy (the UI's rewrite, a tunnel) cannot hold one request open for a slow model, so the page
  starts the work and polls for it.

Without a Muse Spark key `/wearables/look` and walk reports answer 503.
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.clients.meta_hosted.voice import VoiceTranscribeClient
from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.config import Settings
from rulecam.contracts import Clock, Frozen
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.factory import Perception
from rulecam.shelf.gaps import ShelfScanner
from rulecam.voice_out import FallbackTts, MmsTts, SystemTts
from rulecam.voice_out.system_tts import BACKENDS
from rulecam.wearables.assist import (
    SAM_MODEL,
    SPARK_MODEL,
    VOICE_MODEL,
    Ask,
    GlimmerState,
    PovAssistant,
    Voice,
    WearablesError,
)
from rulecam.wearables.schemas import Mode, PovAnswer, Walk, WalkReport
from rulecam.wearables.walks import WalkError, WalkReporter, WalkStore

_log = get_logger(__name__)

HTTP_NOT_FOUND: Final = 404
HTTP_CONFLICT: Final = 409
HTTP_TOO_LARGE: Final = 413
HTTP_UNPROCESSABLE: Final = 422
HTTP_UNAVAILABLE: Final = 503
MAX_IMAGE_BYTES: Final = 8 * 1024 * 1024
MAX_AUDIO_BYTES: Final = 10 * 1024 * 1024
MODES: Final[tuple[Mode, ...]] = ("aisle", "warehouse", "crowd", "general")
SOURCES: Final = ("phone-camera", "meta-glasses", "mock-device", "upload")
MAX_JOBS: Final = 200
UNAVAILABLE: Final = "the glasses assistant needs Muse Spark (META_API_KEY)"
MMS_LABEL: Final = "Meta MMS-TTS (system voice if it fails)"
SYSTEM_LABEL: Final = "system voice (not a Meta model)"


class WalkStart(BaseModel):
    """Start a safety walk."""

    mode: Mode = "general"
    note: str = Field(default="", max_length=500)


class WalkView(Frozen):
    """A walk with its answers so far and, once finished, its report."""

    walk: Walk
    answers: list[PovAnswer]
    report: WalkReport | None = None


class Job(Frozen):
    """Work started with `wait=false`: poll `GET /wearables/jobs/{job_id}` until it is done."""

    job_id: str
    status: Literal["running", "done", "failed"]
    answer: PovAnswer | None = None
    report: WalkReport | None = None
    error: str | None = None


class Jobs:
    """The latest background looks and walk reports, by id (oldest dropped past `MAX_JOBS`)."""

    def __init__(self) -> None:
        """Create an empty job list."""
        self._tasks: OrderedDict[str, asyncio.Task[PovAnswer | WalkReport]] = OrderedDict()

    def start(self, work: Callable[[], Awaitable[PovAnswer | WalkReport]]) -> Job:
        """Run `work` in the background and return its job."""
        job_id = uuid.uuid4().hex[:16]

        async def run() -> PovAnswer | WalkReport:
            return await work()

        self._tasks[job_id] = asyncio.create_task(run())
        while len(self._tasks) > MAX_JOBS:
            self._tasks.popitem(last=False)
        return Job(job_id=job_id, status="running")

    def get(self, job_id: str) -> Job:
        """The job's state.

        Raises:
            HTTPException: 404 for an unknown job.
        """
        task = self._tasks.get(job_id)
        if task is None:
            raise HTTPException(HTTP_NOT_FOUND, "no such job")
        if not task.done():
            return Job(job_id=job_id, status="running")
        error = task.exception()
        if error is not None:
            detail = error.detail if isinstance(error, HTTPException) else repr(error)
            return Job(job_id=job_id, status="failed", error=str(detail)[:500])
        result = task.result()
        if isinstance(result, PovAnswer):
            return Job(job_id=job_id, status="done", answer=result)
        return Job(job_id=job_id, status="done", report=result)


class WearablesStatus(Frozen):
    """What answers the glasses."""

    ready: bool
    look: str | None = Field(description="The first-look model; Spark looks when it is down.")
    glimmer: GlimmerState = Field(description="Glimmer as last seen; asking pings it.")
    compose: str
    count: str
    transcribe: str | None
    voice: str | None = Field(description="Server-side speech; None leaves it to the browser.")
    modes: list[str]
    glimmer_timeout_s: float


def wearables_voice(bus: Bus) -> Voice | None:
    """The repo's speech: Meta MMS-TTS with the `ml` extra, else the labelled system voice."""
    system = SystemTts(bus) if any(shutil.which(b) for b in BACKENDS) else None
    if importlib.util.find_spec("transformers") is not None:
        mms = MmsTts(bus)
        return Voice(FallbackTts(mms, system) if system is not None else mms, MMS_LABEL)
    return Voice(system, SYSTEM_LABEL) if system is not None else None


async def _read(upload: UploadFile, limit: int, what: str) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(HTTP_TOO_LARGE, f"{what} up to {limit // (1024 * 1024)} MB")
    return data


def _file(folder: Path, name: str, suffixes: set[str], media_type: str | None) -> FileResponse:
    path = folder / name
    if Path(name).name != name or path.suffix not in suffixes or not path.is_file():
        raise HTTPException(HTTP_NOT_FOUND, "no such file")
    return FileResponse(path, media_type=media_type)


def wearables_router(  # noqa: C901 - one closure per route
    assistant: PovAssistant | None,
    walks: WalkStore,
    reporter: WalkReporter | None,
    *,
    glimmer_timeout_s: float,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    """The `/wearables` routes; 503 without the models."""
    router = APIRouter(prefix="/wearables", tags=["wearables"], dependencies=list(dependencies))
    jobs = Jobs()

    def _assistant() -> PovAssistant:
        if assistant is None:
            raise HTTPException(HTTP_UNAVAILABLE, UNAVAILABLE)
        return assistant

    def _walk(walk_id: str) -> Walk:
        try:
            return walks.get(walk_id)
        except WalkError as exc:
            raise HTTPException(HTTP_NOT_FOUND, str(exc)) from exc

    @router.get("/status")
    async def status() -> WearablesStatus:
        voice = assistant.voice if assistant is not None else None
        return WearablesStatus(
            ready=assistant is not None,
            look=assistant.glimmer_model if assistant is not None else None,
            glimmer=assistant.warm() if assistant is not None else "none",
            compose=SPARK_MODEL,
            count=SAM_MODEL,
            transcribe=VOICE_MODEL if assistant is not None and assistant.can_hear else None,
            voice=voice.label if voice is not None else None,
            modes=list(MODES),
            glimmer_timeout_s=glimmer_timeout_s,
        )

    @router.post("/look")
    async def look(
        *,
        image: Annotated[UploadFile, File()],
        audio: Annotated[UploadFile | None, File()] = None,
        question: Annotated[str, Form(max_length=1000)] = "",
        mode: Annotated[Mode, Form()] = "general",
        source: Annotated[str, Form(max_length=40)] = "phone-camera",
        walk_id: Annotated[str, Form(max_length=80)] = "",
        lang: Annotated[str, Form(pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")] = "en",
        speak: Annotated[bool, Form()] = True,
        wait: Annotated[bool, Form()] = True,
    ) -> PovAnswer | Job:
        svc = _assistant()
        if walk_id and _walk(walk_id).finished_at is not None:
            raise HTTPException(HTTP_CONFLICT, f"walk {walk_id} is finished")
        history = walks.answers(walk_id) if walk_id else []
        ask = Ask(
            image=await _read(image, MAX_IMAGE_BYTES, "frames"),
            content_type=image.content_type or "",
            mode=mode,
            question=question.strip() or None,
            audio=await _read(audio, MAX_AUDIO_BYTES, "audio") if audio is not None else None,
            lang=lang,
            speak=speak,
            source=source if source in SOURCES else "phone-camera",
            walk_id=walk_id or None,
        )

        async def work() -> PovAnswer:
            try:
                answer = await svc.answer(ask, history)
            except WearablesError as exc:
                raise HTTPException(HTTP_UNPROCESSABLE, str(exc)) from exc
            if walk_id:
                walks.add(walk_id, answer)
            return answer

        return await work() if wait else jobs.start(work)

    @router.post("/walks")
    def start_walk(body: WalkStart) -> Walk:
        return walks.start(body.mode, body.note)

    @router.get("/walks/{walk_id}")
    def get_walk(walk_id: str) -> WalkView:
        walk = _walk(walk_id)
        return WalkView(walk=walk, answers=walks.answers(walk_id), report=walks.report(walk_id))

    @router.post("/walks/{walk_id}/finish")
    async def finish_walk(walk_id: str, wait: bool = True) -> WalkReport | Job:
        _walk(walk_id)
        done = walks.report(walk_id)
        if done is not None:
            return done
        if reporter is None:
            raise HTTPException(HTTP_UNAVAILABLE, UNAVAILABLE)
        walk = walks.finish(walk_id)
        writer = reporter

        async def work() -> WalkReport:
            report = await asyncio.to_thread(writer.report, walk, walks.answers(walk_id))
            walks.save_report(report)
            return report

        return await work() if wait else jobs.start(work)

    @router.get("/jobs/{job_id}")
    def job(job_id: str) -> Job:
        return jobs.get(job_id)

    @router.get("/frames/{name}")
    def frame(name: str) -> FileResponse:
        folder = _assistant().frames_dir
        media = "image/png" if name.endswith(".png") else "image/jpeg"
        return _file(folder, name, {".jpg", ".png"}, media)

    @router.get("/audio/{name}")
    def audio_file(name: str) -> FileResponse:
        return _file(_assistant().audio_dir, name, {".wav"}, "audio/wav")

    return router


def build_wearables(
    settings: Settings,
    *,
    cache: ReplayCache,
    clock: Clock,
    bus: Bus,
    perception: Perception,
    chain: EventChain | None,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    """The `/wearables` router: Glimmer looks, SAM 3.1 counts, Muse Spark answers and reports.

    Args:
        settings: Application settings.
        cache: The replay cache every model call goes through.
        clock: Time source.
        bus: Bus for the relay HUD.
        perception: Hosted SAM 3.1 (`perception.hosted`).
        chain: The evidence chain walk reports are appended to.
        dependencies: Route dependencies (owner-only).

    Returns:
        The router; its look and report routes answer 503 without a Spark key.
    """
    root = settings.rulecam_data_dir / "wearables"
    walks = WalkStore(root / "walks", clock=clock)
    timeout = settings.rulecam_wearables_glimmer_timeout_s
    try:
        spark = SparkClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError as exc:
        _log.warning("wearables_without_spark", error=str(exc))
        return wearables_router(
            None, walks, None, glimmer_timeout_s=timeout, dependencies=dependencies
        )
    try:
        transcriber: VoiceTranscribeClient | None = VoiceTranscribeClient.from_settings(
            settings, clock=clock, bus=bus, cache=cache
        )
    except ClientConfigError as exc:
        _log.warning("wearables_without_transcribe", error=str(exc))
        transcriber = None
    glimmer = GlimmerClient.from_settings(settings, cache=cache, clock=clock, bus=bus)
    frames = root / "frames"
    assistant = PovAssistant(
        spark=spark,
        files=spark,
        sam=perception.hosted,
        out_dir=root,
        clock=clock,
        glimmer=glimmer,
        glimmer_model=glimmer.model,
        shelf=ShelfScanner(spark=spark, files=spark, sam=perception.hosted, out_dir=frames),
        transcriber=transcriber,
        voice=wearables_voice(bus),
        glimmer_timeout_s=timeout,
        site_profile=settings.rulecam_site_profile,
    )
    reporter = WalkReporter(
        spark=spark,
        files=spark,
        chain=chain,
        frame_path=lambda name: frames / Path(name).name,
        site_profile=settings.rulecam_site_profile,
    )
    return wearables_router(
        assistant, walks, reporter, glimmer_timeout_s=timeout, dependencies=dependencies
    )
