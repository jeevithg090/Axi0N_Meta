"""`/retail`: the supermarket use case's routes, all owner-only (docs/usecases/supermarket.md).

- `GET /retail/overview`: cameras, watches, recent findings (with alert status), restock tasks,
  advice cards with their check's progress, the owner's last questions and the last brief.
- `POST /retail/checks/{kind}` (`queue`, `hazard`, `shelf`): run a check on a camera now.
- `POST /retail/checks/{kind}/image`: run `hazard` or `shelf` on a staff photo.
- `GET /retail/findings`, `GET /retail/files/{name}`: stored findings and their pictures.
- `GET|POST /retail/watches`, `POST /retail/watches/{id}/run`, `DELETE /retail/watches/{id}`.
- `GET /retail/tasks`, `POST /retail/tasks/{id}/status`: restock tasks.
- `GET /retail/asks`: the owner's latest Ask answers with their receipts.
- `POST /retail/advice`: an advice card from an Ask answer; `POST /retail/advice/{id}/accept`
  pre-registers its check and turns on its watch.
- `POST /retail/brief`, `GET /retail/brief`: the owner's brief.
- `POST /retail/ask/voice`: a spoken question (WAV) → Voice Transcribe → Ask.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from rulecam.ask import AskAgent, AskAnswer
from rulecam.capture.pair_phone.service import qr_png_base64
from rulecam.clients.errors import ModelClientError
from rulecam.contracts import Frozen
from rulecam.retail.advice import Receipt, RetailCard
from rulecam.retail.checks import RetailError
from rulecam.retail.models import (
    Brief,
    CheckKind,
    Finding,
    StaffTask,
    TaskStatus,
    Watch,
    WatchRequest,
)
from rulecam.retail.service import RetailService

HTTP_NOT_FOUND = 404
HTTP_TOO_LARGE = 413
HTTP_UNPROCESSABLE = 422
HTTP_BAD_GATEWAY = 502
HTTP_UNAVAILABLE = 503
MAX_UPLOAD_BYTES: Final = 15 * 1024 * 1024
MAX_AUDIO_BYTES: Final = 20 * 1024 * 1024
IMAGE_SUFFIXES: Final = {"image/jpeg": ".jpg", "image/png": ".png"}
UNAVAILABLE: Final = "the supermarket tools need Muse Spark and SAM 3.1 (META_API_KEY)"
CAMERA = r"^[A-Za-z0-9_.-]{1,64}$"


class CheckRequest(BaseModel):
    """`POST /retail/checks/{kind}`."""

    camera_id: str = Field(pattern=CAMERA)
    at: datetime | None = Field(default=None, description="Recorded instant; default: newest.")
    role: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,31}$")


class AdviceRequest(BaseModel):
    """`POST /retail/advice`."""

    receipt: str = Field(pattern=r"^[0-9a-f]{64}$", description="The Ask answer's receipt.")
    camera_id: str | None = Field(default=None, pattern=CAMERA)


class TaskUpdate(BaseModel):
    """`POST /retail/tasks/{id}/status`."""

    status: TaskStatus


class BriefRequest(BaseModel):
    """`POST /retail/brief`."""

    lang: str = Field(default="en", min_length=2, max_length=40)


class VoiceAnswer(Frozen):
    """`POST /retail/ask/voice`: what was heard and the Ask answer."""

    transcript: str
    lang: str
    answer: AskAnswer
    transcribe_seconds: float


Transcribe = Callable[[bytes, list[str] | None], list[dict[str, str]]]


async def _run[T](call: Callable[[], T]) -> T:
    """Run blocking work off the event loop; map errors to HTTP answers."""
    try:
        return await asyncio.to_thread(call)
    except RetailError as exc:
        raise HTTPException(HTTP_UNPROCESSABLE, str(exc)) from exc
    except ModelClientError as exc:
        raise HTTPException(HTTP_BAD_GATEWAY, f"model call failed: {exc}") from exc


async def _read(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(HTTP_TOO_LARGE, f"files up to {limit // (1024 * 1024)} MB")
    return data


def _view_routes(
    router: APIRouter, svc: Callable[[], RetailService], files: Callable[[str], Path | None]
) -> None:
    """Overview, cameras, findings and their pictures."""

    @router.get("/overview")
    def overview() -> dict[str, object]:
        """Everything the supermarket page shows."""
        return svc().overview()

    @router.get("/cameras")
    def cameras() -> list[dict[str, object]]:
        """Cameras with recorded video."""
        return svc().cameras()

    @router.get("/findings")
    def findings(
        kind: CheckKind | None = None, limit: Annotated[int, Query(ge=1, le=500)] = 50
    ) -> list[Finding]:
        """Stored findings, newest first."""
        return svc().findings.recent(limit=limit, kind=kind)

    @router.get("/files/{name}")
    def file(name: str) -> FileResponse:
        """A frame, marked image or photo a finding names."""
        path = files(name)
        if path is None:
            raise HTTPException(HTTP_NOT_FOUND, "no such file")
        media = "image/png" if path.suffix == ".png" else "image/jpeg"
        return FileResponse(path, media_type=media, headers={"Cache-Control": "private"})


def _check_routes(router: APIRouter, svc: Callable[[], RetailService], uploads_dir: Path) -> None:
    """Run a check on a camera or a photo."""

    @router.post("/checks/{kind}")
    async def check(kind: CheckKind, body: CheckRequest) -> Finding:
        """Run a check on a camera's recorded video now (alerting staff if the models say so)."""
        s = svc()
        return await _run(lambda: s.run_check(kind, body.camera_id, body.at, role=body.role))

    @router.post("/checks/{kind}/image")
    async def check_image(
        kind: Literal["hazard", "shelf"],
        image: Annotated[UploadFile, File()],
        role: Annotated[str | None, Form(pattern=r"^[a-z][a-z0-9_-]{0,31}$")] = None,
    ) -> Finding:
        """Run a hazard or shelf check on a staff photo (JPEG or PNG)."""
        s = svc()
        suffix = IMAGE_SUFFIXES.get(image.content_type or "")
        if suffix is None:
            raise HTTPException(HTTP_UNPROCESSABLE, "send a JPEG or PNG")
        data = await _read(image, MAX_UPLOAD_BYTES)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        path = uploads_dir / f"photo-{uuid.uuid4().hex[:16]}{suffix}"
        path.write_bytes(data)
        return await _run(lambda: s.run_image(kind, path, role))


def _watch_routes(router: APIRouter, svc: Callable[[], RetailService]) -> None:
    """Standing watches."""

    @router.get("/watches")
    def watches() -> list[Watch]:
        """Every standing watch."""
        return svc().watches.all()

    @router.post("/watches")
    def add_watch(body: WatchRequest) -> Watch:
        """Turn on a standing watch."""
        return svc().add_watch(body)

    @router.post("/watches/{watch_id}/run")
    async def run_watch(watch_id: str) -> Finding:
        """Run a watch's check now."""
        s = svc()
        return await _run(lambda: s.run_watch(watch_id))

    @router.delete("/watches/{watch_id}")
    def stop_watch(watch_id: str) -> Watch:
        """Turn a watch off."""
        watch = svc().stop_watch(watch_id)
        if watch is None:
            raise HTTPException(HTTP_NOT_FOUND, "no such watch")
        return watch


def _task_routes(router: APIRouter, svc: Callable[[], RetailService], uploads_dir: Path) -> None:
    """Restock tasks and their proof photos."""

    @router.get("/tasks")
    def tasks(
        role: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_-]{0,31}$")] = None,
        open_only: bool = False,
    ) -> list[StaffTask]:
        """Restock tasks, newest first (the staff app's feed: `?role=stock&open_only=true`)."""
        return svc().tasks_for(role, open_only=open_only)

    @router.post("/tasks/{task_id}/proof")
    async def task_proof(task_id: str, image: Annotated[UploadFile, File()]) -> StaffTask:
        """Close a restock task with a photo; Spark and Glimmer check every gap is filled."""
        s = svc()
        suffix = IMAGE_SUFFIXES.get(image.content_type or "")
        if suffix is None:
            raise HTTPException(HTTP_UNPROCESSABLE, "send a JPEG or PNG")
        data = await _read(image, MAX_UPLOAD_BYTES)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        path = uploads_dir / f"proof-{uuid.uuid4().hex[:16]}{suffix}"
        path.write_bytes(data)
        return await _run(lambda: s.prove(task_id, path))

    @router.post("/tasks/{task_id}/status")
    def task_status(task_id: str, body: TaskUpdate) -> StaffTask:
        """Move a restock task on (staff tapped *On my way* or *Done*)."""
        s = svc()
        task = s.tasks.set_status(task_id, body.status, s.now())
        if task is None:
            raise HTTPException(HTTP_NOT_FOUND, "no such task")
        return task


def _advice_routes(router: APIRouter, svc: Callable[[], RetailService]) -> None:
    """Ask answers, advice cards and the brief."""

    @router.get("/asks")
    def asks(limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[dict[str, object]]:
        """The owner's latest Ask answers with their receipts."""
        return svc().asks(limit)

    @router.get("/receipts/{sha}")
    async def ask_receipt(
        sha: Annotated[str, PathParam(pattern=r"^[0-9a-f]{64}$")],
        link: Annotated[str | None, Query(max_length=400, pattern=r"^https?://\S+$")] = None,
    ) -> Receipt:
        """An Ask answer's receipt, the run log's chain check and a QR code of `link`.

        `link` is the page that checks the receipt in the browser; without it there is no QR.
        """
        s = svc()
        found = await _run(lambda: s.receipt(sha))
        if link is None:
            return found
        return found.model_copy(update={"qr_png_base64": qr_png_base64(link)})

    @router.post("/advice")
    async def advice(body: AdviceRequest) -> RetailCard:
        """Draft an advice card from an Ask answer."""
        s = svc()
        return await _run(lambda: s.draft_advice(body.receipt, body.camera_id))

    @router.post("/advice/{card_id}/accept")
    async def accept(card_id: str) -> RetailCard:
        """Pre-register the card's check on the evidence chain and turn on its watch."""
        s = svc()
        return await _run(lambda: s.accept(card_id))

    @router.post("/brief")
    async def brief(body: BriefRequest) -> Brief:
        """Write the owner's brief from the last 24 hours."""
        s = svc()
        return await _run(lambda: s.brief(body.lang))

    @router.get("/brief")
    def last_brief() -> Brief | None:
        """The last brief written (null before the first)."""
        return svc().last_brief


def _voice_routes(router: APIRouter, ask: AskAgent | None, transcribe: Transcribe | None) -> None:
    """Spoken questions."""

    @router.post("/ask/voice")
    async def ask_voice(
        audio: Annotated[UploadFile, File()],
        lang: Annotated[str | None, Form(max_length=40)] = None,
        conversation_id: Annotated[str | None, Form(pattern=r"^[A-Za-z0-9_-]{1,64}$")] = None,
    ) -> VoiceAnswer:
        """A spoken question: Muse Voice Transcribe, then the Ask agent answers in that language."""
        if ask is None or transcribe is None:
            raise HTTPException(HTTP_UNAVAILABLE, "spoken questions need Ask and Voice Transcribe")
        data = await _read(audio, MAX_AUDIO_BYTES)
        heard, seconds = await _run(lambda: _transcribe(transcribe, data, lang))
        text = " ".join(seg.get("text", "") for seg in heard).strip()
        if not text:
            raise HTTPException(HTTP_UNPROCESSABLE, "no speech heard")
        spoken = next((seg.get("lang", "") for seg in heard if seg.get("lang")), lang or "")
        answer = await _run(lambda: ask.ask(text, conversation_id=conversation_id))
        return VoiceAnswer(transcript=text, lang=spoken, answer=answer, transcribe_seconds=seconds)


def retail_router(
    service: RetailService | None,
    *,
    uploads_dir: Path,
    files: Callable[[str], Path | None],
    ask: AskAgent | None = None,
    transcribe: Transcribe | None = None,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    """The `/retail` routes; 503 without the models.

    Args:
        service: The supermarket service, or `None` without Spark.
        uploads_dir: Where staff photos are stored.
        files: Resolves an evidence file name to its path.
        ask: The Ask agent (for spoken questions).
        transcribe: Muse Voice Transcribe (`VoiceTranscribeClient.transcribe`).
        dependencies: Route dependencies (the owner check).

    Returns:
        The router.
    """
    router = APIRouter(prefix="/retail", tags=["retail"], dependencies=list(dependencies))

    def svc() -> RetailService:
        if service is None:
            raise HTTPException(HTTP_UNAVAILABLE, UNAVAILABLE)
        return service

    _view_routes(router, svc, files)
    _check_routes(router, svc, uploads_dir)
    _watch_routes(router, svc)
    _task_routes(router, svc, uploads_dir)
    _advice_routes(router, svc)
    _voice_routes(router, ask, transcribe)
    return router


def _transcribe(
    transcribe: Transcribe, data: bytes, lang: str | None
) -> tuple[list[dict[str, str]], float]:
    import time  # noqa: PLC0415 - only this route times anything

    from rulecam.voice_out.wav import to_16k_wav  # noqa: PLC0415 - numpy only when used

    start = time.monotonic()
    try:
        wav = to_16k_wav(data)
    except Exception as exc:
        raise RetailError(f"send a 16-bit PCM WAV ({type(exc).__name__})") from exc
    segments = transcribe(wav, [lang] if lang else None)
    return segments, round(time.monotonic() - start, 2)
