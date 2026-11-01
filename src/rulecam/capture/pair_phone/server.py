"""FastAPI router for Pair-a-Phone; the API app (P3-08) mounts it (task P1-09).

    app.include_router(build_router(PairPhone(bus=bus, clock=clock, segmenter=sam,
                                              settings=settings, concepts=plan_concepts)))

Every route is HTTPS-only, except on localhost:

| Route | Who | What |
|---|---|---|
| `POST /pair/token` | owner UI (loopback only) | single-use token, its URL and QR PNG |
| `GET /pair/{token}` | phone | the camera page |
| `GET /pair/assets/{name}` | phone | the page's script and stylesheet |
| `POST /pair/{token}/claim` | phone | claim the token: camera id and session key |
| `POST /pair/{token}/chunk` | phone | one WebM/MP4 clip ≤ 5 MB (header `X-Pair-Session`) |
| `WS /pair/{token}/coach` | phone | placement-coach bars every ~2 s |

`require_owner` only lets this machine create tokens. The API app swaps in owner login
(`rulecam.api.auth`) with `app.dependency_overrides[require_owner]`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request, WebSocket, status
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import UploadFile
from starlette.exceptions import WebSocketException
from starlette.requests import HTTPConnection
from starlette.types import Message, Receive
from starlette.websockets import WebSocketDisconnect

from rulecam.capture.pair_phone.coach import CoachHello, CoachHint, CoachStatus
from rulecam.capture.pair_phone.errors import (
    ChunkTooLargeError,
    PairingError,
    SessionInvalidError,
    TokenExpiredError,
    TokenUnknownError,
    TokenUsedError,
    TranscodeError,
    UnsupportedMediaError,
)
from rulecam.capture.pair_phone.media import UploadKind, media_kind
from rulecam.capture.pair_phone.service import PairPhone, PairToken
from rulecam.capture.pair_phone.tokens import PairedCamera
from rulecam.errors import RulecamError
from rulecam.log import get_logger

_log = get_logger(__name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 16 * 1024
SESSION_HEADER = "X-Pair-Session"
TOO_LARGE = f"uploads are limited to {MAX_UPLOAD_BYTES} bytes"
COACH_LOGIN_TIMEOUT_S = 10.0
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
SECURE_SCHEMES = frozenset({"https", "wss"})
STATIC_DIR = Path(__file__).parent / "static"
ASSET_TYPES = {"phone.js": "text/javascript", "phone.css": "text/css"}
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "media-src 'self' blob: mediastream:; connect-src 'self' wss:; object-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
}
_STATUS_OF: dict[type[PairingError], int] = {
    TokenUnknownError: status.HTTP_404_NOT_FOUND,
    TokenExpiredError: status.HTTP_410_GONE,
    TokenUsedError: status.HTTP_409_CONFLICT,
    SessionInvalidError: status.HTTP_401_UNAUTHORIZED,
    UnsupportedMediaError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    ChunkTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    TranscodeError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}

TokenPath = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_-]{16,64}$")]


class ClaimRequest(BaseModel):
    """The phone's claim: a human name for the camera (optional)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(default="", max_length=40, pattern=r"^[^\x00-\x1f\x7f]*$")


class ClaimResponse(BaseModel):
    """The paired camera and the session key the phone sends from now on."""

    camera_id: str
    label: str
    session: str


class ChunkAccepted(BaseModel):
    """A transcoded upload, now published on `chunk.ready`."""

    chunk_id: str
    camera_id: str
    n_frames: int
    width: int
    height: int


def is_loopback(host: str | None) -> bool:
    """Return True for localhost, 127.0.0.1 and ::1."""
    return host in LOOPBACK_HOSTS


def require_secure_transport(conn: HTTPConnection) -> None:
    """Reject plain HTTP/WS unless the request is addressed to localhost.

    Behind the HTTPS tunnel, uvicorn's proxy-header support reports the original `https`.

    Raises:
        HTTPException: 403 for HTTP requests.
        WebSocketException: Policy-violation close for WebSockets.
    """
    if conn.url.scheme in SECURE_SCHEMES or is_loopback(conn.url.hostname):
        return
    reason = "Pair-a-Phone needs HTTPS: open the tunnel URL"
    if conn.scope["type"] == "websocket":
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=reason)
    raise HTTPException(status.HTTP_403_FORBIDDEN, reason)


def _from_loopback_page(conn: HTTPConnection) -> bool:
    """False when a browser sent this from a page on another host (cross-site request forgery).

    A page on any website can make the owner's browser POST to `http://localhost:8000`; the
    browser then sends that page's `Origin`. Requests without one (curl, the UI's server-side
    proxy) are not browser cross-site requests.
    """
    origin = conn.headers.get("origin")
    return origin is None or is_loopback(urlsplit(origin).hostname)


def is_loopback_owner(conn: HTTPConnection) -> bool:
    """Whether the request comes from this machine: loopback host and client, loopback `Origin`."""
    client = conn.client.host if conn.client else None
    return is_loopback(conn.url.hostname) and is_loopback(client) and _from_loopback_page(conn)


def require_owner(conn: HTTPConnection) -> None:
    """Only let this machine (not the public tunnel, nor another site's page) make owner calls.

    The API app replaces this with `rulecam.api.auth.OwnerAuth.require_owner`, which also
    accepts a signed-in owner session.

    Raises:
        HTTPException: 403 unless the target host and the client are loopback and any browser
            `Origin` is a loopback page.
    """
    if is_loopback_owner(conn):
        return
    reason = "pairing codes are created on the owner's machine"
    raise HTTPException(status.HTTP_403_FORBIDDEN, reason)


def http_error(exc: PairingError) -> HTTPException:
    """Map a Pair-a-Phone error to its HTTP status, keeping the message for the phone."""
    return HTTPException(_STATUS_OF.get(type(exc), status.HTTP_400_BAD_REQUEST), str(exc))


def check_declared_length(content_length: str | None, limit: int) -> None:
    """Reject a body whose declared length is over `limit` before reading any of it.

    Raises:
        HTTPException: 400 for a malformed header, 413 when too large.
    """
    if content_length is None:
        return
    if not content_length.isdigit():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed Content-Length")
    if int(content_length) > limit:
        raise http_error(ChunkTooLargeError(TOO_LARGE))


def limited_receive(receive: Receive, limit: int) -> Callable[[], Awaitable[Message]]:
    """Wrap an ASGI `receive` so a body longer than `limit` fails, even when chunk-encoded.

    The returned callable raises `ChunkTooLargeError` once more than `limit` bytes arrived.
    """
    received = 0

    async def wrapped() -> Message:
        nonlocal received
        message = await receive()
        received += len(message.get("body", b""))
        if received > limit:
            raise ChunkTooLargeError(TOO_LARGE)
        return message

    return wrapped


async def read_upload(request: Request) -> tuple[UploadKind, bytes]:
    """Read the single multipart field `file`, enforcing the size and content-type limits.

    Returns:
        The declared upload kind and its bytes.

    Raises:
        ChunkTooLargeError: Over `MAX_UPLOAD_BYTES`.
        UnsupportedMediaError: Not `video/webm` or `video/mp4`.
        HTTPException: 422 when the `file` field is missing.
    """
    body_limit = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES
    limited = Request(request.scope, receive=limited_receive(request.receive, body_limit))
    async with limited.form(max_files=1, max_fields=0) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "expected one multipart file named 'file'"
            )
        kind = media_kind(upload.content_type)
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ChunkTooLargeError(TOO_LARGE)
    return kind, data


async def _until_disconnect(websocket: WebSocket) -> None:
    """Consume (and ignore) client messages until the phone disconnects."""
    while (await websocket.receive())["type"] != "websocket.disconnect":
        continue


class _Routes:
    """Route handlers bound to one `PairPhone` service.

    All handlers are `async`, so they run on the event loop one at a time between awaits;
    that keeps token claims atomic without locks.
    """

    def __init__(self, service: PairPhone) -> None:
        self._service = service
        self._page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self._assets = {name: (STATIC_DIR / name).read_bytes() for name in ASSET_TYPES}

    async def create_token(self, request: Request) -> PairToken:
        """Issue a single-use pairing token with its URL and QR code (owner UI)."""
        return self._service.issue_token(
            lambda token: request.url_for("pair_page", token=token).path, str(request.base_url)
        )

    async def page(self, token: TokenPath) -> HTMLResponse:
        """Serve the phone page while the token is unexpired or already paired."""
        try:
            self._service.registry.check_open(token)
        except PairingError as exc:
            raise http_error(exc) from exc
        return HTMLResponse(self._page, headers=PAGE_HEADERS)

    async def asset(self, name: str) -> Response:
        """Serve one of the page's static files (fixed allowlist; no user paths)."""
        if name not in ASSET_TYPES:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such asset")
        return Response(
            self._assets[name], media_type=ASSET_TYPES[name], headers={"Cache-Control": "no-cache"}
        )

    async def claim(self, token: TokenPath, body: ClaimRequest) -> ClaimResponse:
        """Pair the phone with this token (once) and return its camera id and session key."""
        self._limit(token)
        try:
            camera, session = self._service.claim(token, body.label)
        except PairingError as exc:
            raise http_error(exc) from exc
        return ClaimResponse(camera_id=camera.camera_id, label=camera.label, session=session)

    async def upload_chunk(
        self,
        token: TokenPath,
        request: Request,
        session: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    ) -> ChunkAccepted:
        """Accept one clip from the paired phone, transcode it and publish a `Chunk`."""
        camera = self._authenticate(token, session)
        self._limit(token)
        check_declared_length(
            request.headers.get("content-length"), MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES
        )
        try:
            kind, data = await read_upload(request)
            chunk = await self._service.ingest(camera.camera_id, data, kind)
        except PairingError as exc:
            _log.warning("phone_upload_rejected", camera_id=camera.camera_id, reason=str(exc))
            raise http_error(exc) from exc
        return ChunkAccepted(
            chunk_id=chunk.chunk_id,
            camera_id=chunk.camera_id,
            n_frames=chunk.n_frames,
            width=chunk.width,
            height=chunk.height,
        )

    async def coach(self, websocket: WebSocket, token: TokenPath) -> None:
        """Stream placement-coach bars to the paired phone every `coach_interval_s`.

        The phone's first message must be `{"session": "<key>"}`.
        """
        await websocket.accept()
        camera = await self._coach_login(websocket, token)
        with contextlib.suppress(WebSocketDisconnect):
            await self._coach_loop(websocket, camera.camera_id)

    async def _coach_login(self, websocket: WebSocket, token: str) -> PairedCamera:
        try:
            login: Any = await asyncio.wait_for(
                websocket.receive_json(), timeout=COACH_LOGIN_TIMEOUT_S
            )
            session = login.get("session") if isinstance(login, dict) else None
            camera = self._service.registry.authenticate(token, str(session))
        except (TimeoutError, ValueError, PairingError) as exc:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION, reason="session required"
            ) from exc
        return camera

    async def _coach_loop(self, websocket: WebSocket, camera_id: str) -> None:
        service = self._service
        hello = CoachHello(camera_id=camera_id, concepts=service.concepts())
        await websocket.send_json(hello.model_dump())
        receiver = asyncio.create_task(_until_disconnect(websocket))
        hint_seen = 0
        try:
            while not receiver.done():
                await websocket.send_json(await self._coach_message(camera_id))
                version, text = service.hint(camera_id)
                if version != hint_seen and text:
                    await websocket.send_json(CoachHint(text=text).model_dump())
                hint_seen = version
                await asyncio.wait({receiver}, timeout=service.coach_interval_s)
        finally:
            receiver.cancel()

    async def _coach_message(self, camera_id: str) -> dict[str, Any]:
        try:
            bars = await self._service.coach_bars(camera_id)
        except RulecamError as exc:
            _log.warning("coach_unavailable", camera_id=camera_id, reason=str(exc))
            return CoachStatus(text="Coach unavailable, retrying").model_dump()
        if bars is None:
            return CoachStatus(text="Waiting for the first video chunk").model_dump()
        return bars.model_dump()

    def _authenticate(self, token: str, session: str | None) -> PairedCamera:
        try:
            return self._service.registry.authenticate(token, session or "")
        except PairingError as exc:
            raise http_error(exc) from exc

    def _limit(self, token: str) -> None:
        wait_s = self._service.retry_after(token)
        if wait_s > 0:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "one request per second per pairing code",
                headers={"Retry-After": str(max(1, round(wait_s)))},
            )


def build_router(service: PairPhone) -> APIRouter:
    """Build the `/pair` router for one Pair-a-Phone service.

    Args:
        service: The service holding tokens, phones and coach state.

    Returns:
        A router to include in the API app.
    """
    routes = _Routes(service)
    router = APIRouter(
        prefix="/pair", tags=["pair-phone"], dependencies=[Depends(require_secure_transport)]
    )
    router.add_api_route(
        "/token",
        routes.create_token,
        methods=["POST"],
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_owner)],
    )
    router.add_api_route("/assets/{name}", routes.asset, methods=["GET"])
    router.add_api_route(
        "/{token}", routes.page, methods=["GET"], response_class=HTMLResponse, name="pair_page"
    )
    router.add_api_route(
        "/{token}/claim", routes.claim, methods=["POST"], status_code=status.HTTP_201_CREATED
    )
    router.add_api_route(
        "/{token}/chunk", routes.upload_chunk, methods=["POST"], status_code=status.HTTP_201_CREATED
    )
    router.add_api_websocket_route("/{token}/coach", routes.coach)
    return router
