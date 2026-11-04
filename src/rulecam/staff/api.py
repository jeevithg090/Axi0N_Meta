"""`/staff`: the staff companion's routes (the phone app at `ui/src/app/staff`).

- `GET /staff/config`: languages, what is on (Ask, shelf scans, speech engines), dry run.
- `GET /staff/roles`: the roles the site uses (plans, alerts, tasks).
- `GET /staff/feed?role=`: the role's items, newest first (`?role=` omitted: everything).
- `GET /staff/stream?role=`: the same as server-sent events: `snapshot` once, then `item` for
  every item that changes (each with `sent_at`), and a `ping` every 15 s of quiet (the phone
  reopens a stream that has gone silent for 40 s). It wakes
  on the bus (`alert.new`, `verdict.new`, `action.done`) and on staff taps, and re-checks every
  2 s for chained records that arrive without a bus message.
- `GET /staff/items/{item_id}`: one item with its timed loop.
- `POST /staff/items/{item_id}/actions`: Acknowledge, On my way, Done, False alarm, Escalate.
- `POST /staff/items/{item_id}/done`: *Done* with a photo (multipart `role`, `note`, `photo`).
- `POST /staff/items/{item_id}/seen`: the phone showed it (alert → phone latency).
- `GET /staff/items/{item_id}/speech?lang=`: the spoken line and its WAV.
- `GET /staff/media/{item_id}/{n}`: evidence still `n` (anonymised strips, heatmaps, photos).
- `GET /staff/audio/{name}`: a spoken WAV.
- `GET /staff/stats?hours=`: response times per role and per shift.
- `POST /staff/ask`: push-to-talk question (multipart `audio` WAV or `question`, `lang`, `role`).
- `POST /staff/shelf`: a shelf photo becomes restock tasks (multipart `photo`, `role`).

Every route sits behind the app's owner check (`dependencies`), like `/alerts`: roles are
self-asserted, so a public deployment needs staff sign-in in front of these routes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import Annotated, Any, Final

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.types import ASGIApp

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ModelClientError
from rulecam.contracts import Clock, Frozen
from rulecam.log import get_logger
from rulecam.shelf.gaps import ShelfError, ShelfScanner
from rulecam.staff.asking import NothingHeardError, VoiceAsk
from rulecam.staff.feed import StaffError, StaffFeed, UnknownItemError
from rulecam.staff.models import (
    ITEM_ID_PATTERN,
    MAX_ROLE,
    ROLE_PATTERN,
    ActionResult,
    AskReply,
    Lang,
    RoleInfo,
    SeenRequest,
    SpeechLine,
    StaffActionRequest,
    StaffItem,
    StaffStats,
)
from rulecam.staff.push import (
    PushError,
    PushMessage,
    PushRelay,
    Subscriber,
    SubscribeRequest,
    check_endpoint,
)
from rulecam.staff.speech import Speaker
from rulecam.staff.store import ShelfTask

_log = get_logger(__name__)

HEARTBEAT_S: Final = 15.0
HTTP_OK: Final = 200
PROOF_TIMEOUT_S: Final = 180.0  # Spark and Glimmer look at the photo
# The warehouse's spoken near-miss reports; the phone offers them where the API serves them.
NEAR_MISS_ROUTE: Final = "/warehouse/reports/voice"
RECHECK_S: Final = 2.0
MAX_UPLOAD_BYTES: Final = 15 * 1024 * 1024
MAX_AUDIO_BYTES: Final = 8 * 1024 * 1024
PHOTO_SUFFIXES: Final = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
WAKE_TOPICS: Final = frozenset({Topic.ALERT_NEW, Topic.VERDICT_NEW, Topic.ACTION_DONE})
# `no-transform` keeps proxies (the UI's Next.js rewrite) from gzipping, and so holding, events.
_STREAM_HEADERS: Final = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
_MEDIA_TYPES: Final = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

ItemId = Annotated[str, Field(pattern=ITEM_ID_PATTERN)]
RoleQuery = Annotated[str | None, Query(max_length=MAX_ROLE, pattern=ROLE_PATTERN)]
RoleForm = Annotated[str, Form(min_length=1, max_length=MAX_ROLE, pattern=ROLE_PATTERN)]


class StaffConfig(Frozen):
    """`GET /staff/config`: what the phone can offer.

    Attributes:
        langs: Languages the app speaks.
        dry_run: The site's actions run in dry run (alerts are rehearsals).
        ask: Push-to-talk questions are on (`listen`: spoken; otherwise typed only).
        listen: Voice Transcribe is configured.
        shelf: Shelf photo scans are on.
        speech: Speech engines, in order (`mms-tts` is Meta's; `system-tts` is not).
        push: Web Push is on (`GET /staff/push/key`): alerts reach phones with the app closed.
        near_miss_voice: The API serves the warehouse's spoken near-miss reports
            (`POST /warehouse/reports/voice`), checked on each request.
    """

    langs: tuple[str, ...]
    dry_run: bool
    ask: bool
    listen: bool
    shelf: bool
    speech: tuple[str, ...]
    near_miss_voice: bool = False
    push: bool = False


class RetailProof(BaseModel):
    """What the supermarket's proof check said about a *Done* photo.

    Attributes:
        task_id: The retail task.
        http_status: The check's HTTP status (0: it could not be reached).
        status: The task's status after the check (`done` when the models confirmed the fix).
        closed_by: `photo` when the photo closed it.
        minutes_to_fix: From the camera seeing it to the fix, when closed.
        detail: The check's error, when it failed.
    """

    task_id: str
    http_status: int
    status: str | None = None
    closed_by: str | None = None
    minutes_to_fix: float | None = None
    detail: str = ""

    def words(self) -> str:
        """One line for the person who tapped *Done*."""
        if self.status == "done":
            fixed = f" in {self.minutes_to_fix:.1f} min" if self.minutes_to_fix else ""
            return f"the models confirmed the fix from the photo{fixed}"
        if self.http_status == HTTP_OK:
            return "the models did not confirm the fix from the photo; the task stays open"
        return f"the photo could not be checked ({self.detail or self.http_status})"


async def retail_proof(app: ASGIApp, task_id: str, photo: Path) -> RetailProof:
    """Send a *Done* photo to the supermarket's proof check, inside this process.

    The check (`POST /retail/tasks/{task_id}/proof`, multipart `image`) is owner-only, so it is
    called on this app directly as a loopback request: no network hop, no public URL. Muse Spark
    looks at the photo against the task and Glimmer checks it; the task closes, and
    `gap_refilled` or `hazard_cleared` is chained, only when they agree.

    Returns:
        What the check said, or why it could not run (it is not mounted, it failed).
    """
    media = "image/png" if photo.suffix == ".png" else "image/jpeg"
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 0))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            reply = await client.post(
                f"/retail/tasks/{task_id}/proof",
                files={"image": (photo.name, photo.read_bytes(), media)},
                timeout=PROOF_TIMEOUT_S,
            )
    except (httpx.HTTPError, OSError) as exc:
        return RetailProof(task_id=task_id, http_status=0, detail=str(exc)[:200])
    if reply.status_code != HTTP_OK:
        return RetailProof(task_id=task_id, http_status=reply.status_code, detail=reply.text[:200])
    body = (
        reply.json() if reply.headers.get("content-type", "").startswith("application/json") else {}
    )
    minutes = body.get("minutes_to_fix")
    return RetailProof(
        task_id=task_id,
        http_status=reply.status_code,
        status=str(body.get("status") or "") or None,
        closed_by=str(body.get("closed_by") or "") or None,
        minutes_to_fix=float(minutes) if isinstance(minutes, int | float) else None,
    )


class PushKey(BaseModel):
    """`GET /staff/push/key`: the VAPID public key and how many phones each role subscribed."""

    public_key: str
    roles: dict[str, int]


class Unsubscribe(BaseModel):
    """`POST /staff/push/unsubscribe`."""

    endpoint: str = Field(min_length=10, max_length=2000)


class PushTest(BaseModel):
    """`POST /staff/push/test`: send a test notification to a role's phones."""

    role: str = Field(min_length=1, max_length=MAX_ROLE, pattern=ROLE_PATTERN)


class SeenResult(BaseModel):
    """`POST /staff/items/{id}/seen`."""

    first: bool
    alert_to_phone_ms: float | None


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def stream_feed(
    feed: StaffFeed,
    bus: Bus,
    clock: Clock,
    role: str | None,
    *,
    heartbeat_s: float = HEARTBEAT_S,
    recheck_s: float = RECHECK_S,
) -> AsyncGenerator[str, None]:
    """Server-sent events of the role's feed: a snapshot, then every changed item.

    Args:
        feed: The feed.
        bus: Woken by alerts, verdicts and actions.
        clock: For `sent_at`.
        role: The role (`None`: every item).
        heartbeat_s: Seconds of silence before a `ping` event.
        recheck_s: Seconds between re-checks without a wake-up.

    Yields:
        SSE text.
    """
    wake = feed.signal.listen()

    async def pump() -> None:
        async with contextlib.aclosing(bus.subscribe(WAKE_TOPICS)) as messages:
            async for _ in messages:
                wake.set()

    pumping = asyncio.create_task(pump())
    try:
        items = await asyncio.to_thread(feed.items, role)
        sent = {item.item_id: item.model_dump_json() for item in items}
        yield _sse(
            "snapshot",
            {
                "items": [item.model_dump(mode="json") for item in items],
                "sent_at": clock.now().isoformat(),
            },
        )
        quiet = 0.0
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), recheck_s)
            wake.clear()
            changed = [
                item
                for item in await asyncio.to_thread(feed.items, role)
                if sent.get(item.item_id) != item.model_dump_json()
            ]
            for item in changed:
                sent[item.item_id] = item.model_dump_json()
                yield _sse(
                    "item",
                    {"item": item.model_dump(mode="json"), "sent_at": clock.now().isoformat()},
                )
            quiet = 0.0 if changed else quiet + recheck_s
            if quiet >= heartbeat_s:
                quiet = 0.0
                yield _sse("ping", {"sent_at": clock.now().isoformat()})
    finally:
        pumping.cancel()
        feed.signal.forget(wake)


async def _read_upload(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"uploads up to {limit // (1024 * 1024)} MB")
    return data


def _save_photo(data: bytes, content_type: str | None, folder: Path, stem: str) -> Path:
    suffix = PHOTO_SUFFIXES.get(content_type or "")
    if suffix is None:
        raise HTTPException(422, "send a JPEG, PNG or WebP photo")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}-{uuid.uuid4().hex[:12]}{suffix}"
    path.write_bytes(data)
    return path


def _item_or_404(feed: StaffFeed, item_id: str) -> StaffItem:
    item = feed.item(item_id)
    if item is None:
        raise HTTPException(404, "no such item")
    return item


def _feed_routes(
    router: APIRouter, feed: StaffFeed, bus: Bus, clock: Clock, config: StaffConfig
) -> None:
    """Config, roles, the feed, its event stream and the stats."""

    @router.get("/config")
    def get_config(request: Request) -> StaffConfig:
        paths = {getattr(route, "path", "") for route in request.app.routes}
        return config.model_copy(update={"near_miss_voice": NEAR_MISS_ROUTE in paths})

    @router.get("/roles")
    def roles() -> list[RoleInfo]:
        return feed.roles()

    @router.get("/feed")
    def list_feed(role: RoleQuery = None) -> list[StaffItem]:
        return feed.items(role)

    @router.get(
        "/stream",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    def stream(role: RoleQuery = None) -> StreamingResponse:
        return StreamingResponse(
            stream_feed(feed, bus, clock, role),
            media_type="text/event-stream",
            headers=_STREAM_HEADERS,
        )

    @router.get("/stats")
    def stats(hours: Annotated[float, Query(gt=0, le=24 * 31)] = 24.0) -> StaffStats:
        return feed.stats(hours)


def _item_routes(router: APIRouter, feed: StaffFeed, photos_dir: Path) -> None:
    """One item, taps, *Done* with a photo, sightings and evidence stills."""

    @router.get("/items/{item_id}")
    def get_item(item_id: ItemId) -> StaffItem:
        return _item_or_404(feed, item_id)

    @router.post("/items/{item_id}/actions")
    def act(item_id: ItemId, body: StaffActionRequest) -> ActionResult:
        try:
            return feed.act(item_id, body)
        except UnknownItemError as exc:
            raise HTTPException(404, str(exc)) from exc
        except StaffError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/items/{item_id}/done")
    async def done(
        item_id: ItemId,
        http: Request,
        role: RoleForm,
        note: Annotated[str, Form(max_length=500)] = "",
        photo: Annotated[UploadFile | None, File()] = None,
    ) -> ActionResult:
        item = _item_or_404(feed, item_id)
        saved = None
        if photo is not None and photo.filename:
            data = await _read_upload(photo, MAX_UPLOAD_BYTES)
            saved = _save_photo(data, photo.content_type, photos_dir, f"done-{item_id}")
        proof: RetailProof | None = None
        if saved is not None and item.retail_task_id:
            proof = await retail_proof(http.app, item.retail_task_id, saved)
        request = StaffActionRequest(action="done", role=role, note=note)
        extra = {"retail_proof": proof.model_dump(mode="json")} if proof is not None else None
        try:
            result = await asyncio.to_thread(feed.act, item_id, request, photo=saved, extra=extra)
        except UnknownItemError as exc:
            raise HTTPException(404, str(exc)) from exc
        if proof is None:
            return result
        return result.model_copy(update={"did": f"{result.did}; {proof.words()}"})


def _evidence_routes(router: APIRouter, feed: StaffFeed) -> None:
    """Sightings (alert → phone latency) and evidence stills."""

    @router.post("/items/{item_id}/seen")
    def seen(item_id: ItemId, body: SeenRequest) -> SeenResult:
        try:
            latency = feed.seen(item_id, body.role, body.client_ms)
        except UnknownItemError as exc:
            raise HTTPException(404, str(exc)) from exc
        if latency is not None:
            _log.info(
                "staff_alert_seen",
                item_id=item_id,
                role=body.role,
                server_ms=latency,
                client_ms=body.client_ms,
            )
        return SeenResult(first=latency is not None, alert_to_phone_ms=latency)

    @router.get("/media/{item_id}/{index}", response_class=FileResponse)
    def media(item_id: ItemId, index: int) -> FileResponse:
        path = feed.media(item_id, index)
        if path is None:
            raise HTTPException(404, "no such still")
        media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private"})


def _voice_routes(
    router: APIRouter, feed: StaffFeed, speaker: Speaker, asker: VoiceAsk | None
) -> None:
    """Spoken lines, their audio and push-to-talk Ask."""

    @router.get("/items/{item_id}/speech")
    def speech(item_id: ItemId, lang: Lang = "en", role: RoleQuery = None) -> SpeechLine:
        item = _item_or_404(feed, item_id)
        return speaker.for_item(item, lang, role)

    @router.get("/audio/{name}", response_class=FileResponse)
    def audio(name: str) -> FileResponse:
        path = speaker.audio_dir / name
        if Path(name).name != name or path.suffix != ".wav" or not path.is_file():
            raise HTTPException(404, "no such audio")
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "private"})

    @router.post("/ask")
    async def ask(
        role: RoleForm,
        lang: Annotated[Lang, Form()] = "en",
        question: Annotated[str, Form(max_length=2000)] = "",
        audio: Annotated[UploadFile | None, File()] = None,
    ) -> AskReply:
        if asker is None:
            raise HTTPException(503, "Ask needs RULECAM_PROTOTYPE_MODE=true and META_API_KEY")
        wav = await _read_upload(audio, MAX_AUDIO_BYTES) if audio is not None else None
        try:
            return await asyncio.to_thread(
                asker.ask, role=role, lang=lang, audio=wav, question=question
            )
        except NothingHeardError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ValueError as exc:  # not a 16/24 kHz mono WAV
            raise HTTPException(422, str(exc)) from exc
        except ModelClientError as exc:
            raise HTTPException(502, f"model call failed: {exc}") from exc


def _task_routes(
    router: APIRouter, feed: StaffFeed, clock: Clock, shelf: ShelfScanner | None, photos_dir: Path
) -> None:
    """Shelf photos into restock tasks."""

    @router.post("/shelf")
    async def shelf_scan(role: RoleForm, photo: Annotated[UploadFile, File()]) -> list[StaffItem]:
        if shelf is None:
            raise HTTPException(503, "shelf scans need Muse Spark and SAM 3.1 (META_API_KEY)")
        data = await _read_upload(photo, MAX_UPLOAD_BYTES)
        saved = _save_photo(data, photo.content_type, photos_dir, "shelf")
        try:
            report = await asyncio.to_thread(shelf.scan_image, saved)
        except ShelfError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ModelClientError as exc:
            raise HTTPException(502, f"model call failed: {exc}") from exc
        made = []
        stamp = uuid.uuid4().hex[:8]
        for gap in report.gaps:
            task = ShelfTask(
                task_id=f"shelf-{stamp}-{gap.number}",
                role=role,
                ts=clock.now(),
                section=gap.section,
                neighbours=gap.neighbours,
                facings=gap.facings,
                product_cover=gap.product_cover,
                image=report.marked_image,
                camera_id=report.camera_id,
                model=report.model,
            )
            made.append(await asyncio.to_thread(feed.add_task, task))
        return made


def _push_routes(router: APIRouter, push: PushRelay | None, clock: Clock) -> None:
    """Web Push: the VAPID key, subscribing a phone for its role, and a test send."""

    def relay() -> PushRelay:
        if push is None:
            raise HTTPException(503, "Web Push is off")
        return push

    @router.get("/push/key")
    def push_key() -> PushKey:
        found = relay()
        return PushKey(public_key=found.public_key, roles=found.store.roles())

    @router.post("/push/subscribe")
    def subscribe(body: SubscribeRequest) -> PushKey:
        found = relay()
        try:
            check_endpoint(body.subscription.endpoint)
        except PushError as exc:
            raise HTTPException(422, str(exc)) from exc
        found.store.put(
            Subscriber(
                role=body.role, lang=body.lang, subscription=body.subscription, since=clock.now()
            )
        )
        return PushKey(public_key=found.public_key, roles=found.store.roles())

    @router.post("/push/unsubscribe")
    def unsubscribe(body: Unsubscribe) -> dict[str, bool]:
        return {"removed": relay().store.drop(body.endpoint)}

    @router.post("/push/test")
    def push_test(body: PushTest) -> dict[str, list[int]]:
        found = relay()
        message = PushMessage(
            item_id="push-test",
            title="Rulecam Staff: test alert",
            body=f"Web Push works for {body.role}.",
            role=body.role,
            url="/staff",
            ts=clock.now(),
        )
        return {"statuses": [found.send(sub, message) for sub in found.store.for_role(body.role)]}


def staff_router(
    feed: StaffFeed,
    *,
    bus: Bus,
    clock: Clock,
    speaker: Speaker,
    config: StaffConfig,
    photos_dir: Path,
    asker: VoiceAsk | None = None,
    shelf: ShelfScanner | None = None,
    push: PushRelay | None = None,
    dependencies: Sequence[Any] = (),
) -> APIRouter:
    """The `/staff` routes.

    Args:
        feed: The staff feed.
        bus: Wakes the event streams.
        clock: Time source.
        speaker: Writes and says spoken lines.
        config: What `GET /staff/config` reports.
        photos_dir: Where *Done* photos and shelf photos are stored.
        asker: Push-to-talk Ask (`None`: `POST /staff/ask` answers 503).
        shelf: The shelf-gap scanner (`None`: `POST /staff/shelf` answers 503).
        push: Web Push (`None`: the `/staff/push` routes answer 503).
        dependencies: Authentication applied to every route.

    Returns:
        The router.
    """
    router = APIRouter(prefix="/staff", tags=["staff"], dependencies=list(dependencies))
    _feed_routes(router, feed, bus, clock, config)
    _item_routes(router, feed, photos_dir)
    _evidence_routes(router, feed)
    _voice_routes(router, feed, speaker, asker)
    _task_routes(router, feed, clock, shelf, photos_dir)
    _push_routes(router, push, clock)
    return router
