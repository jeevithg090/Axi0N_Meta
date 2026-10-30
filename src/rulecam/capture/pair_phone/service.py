"""Pair-a-Phone service: pairing, chunk ingest and coach state, independent of HTTP (P1-09).

The router in `server.py` is a thin HTTP/WebSocket layer over this class.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import qrcode
from pydantic import BaseModel, ConfigDict

from rulecam.bus import Bus, Topic
from rulecam.capture.chunker import ChunkInfo, probe_chunk
from rulecam.capture.errors import CaptureError
from rulecam.capture.pair_phone.coach import CoachBars, FrameSegmenter, clean_hint, measure_bars
from rulecam.capture.pair_phone.errors import ChunkTooLargeError, TranscodeError
from rulecam.capture.pair_phone.media import (
    PROBE_TIMEOUT_S,
    TRANSCODE_TIMEOUT_S,
    UploadKind,
    last_frame_args,
    probe_upload,
    run_tool,
    transcode_args,
)
from rulecam.capture.pair_phone.tokens import (
    IssuedToken,
    PairedCamera,
    PairingRegistry,
    RateLimiter,
)
from rulecam.config import Settings
from rulecam.contracts import Chunk, Clock, Provenance
from rulecam.log import get_logger

_log = get_logger(__name__)

PHONE_FPS = 10.0
CHUNK_SECONDS = 3.0
MAX_CHUNK_SECONDS = 6.0
COACH_INTERVAL_S = 2.0
_PHONE_CHUNK = re.compile(r"^phone-(\d+)_\d{6}\.mp4$")


class PairToken(BaseModel):
    """What the owner UI gets from `POST /pair/token`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str
    url: str
    qr_png_base64: str
    expires_at: datetime


def next_phone_number(file_names: Iterable[str]) -> int:
    """Return the first phone number not used by existing chunk files, so restarts never reuse ids.

    Args:
        file_names: Names of files in the chunk directory.
    """
    used = [int(m.group(1)) for name in file_names if (m := _PHONE_CHUNK.match(name))]
    return max(used, default=0) + 1


def page_url(public_base: str | None, request_base: str, path: str) -> str:
    """Join the public tunnel URL (or, without one, the request's own base URL) and a path."""
    return (public_base or request_base).rstrip("/") + path


def qr_png_base64(text: str) -> str:
    """Render `text` as a QR code and return the PNG, base64-encoded."""
    buffer = io.BytesIO()
    qrcode.make(text, border=2).save(buffer)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def chunk_start(received: datetime, duration: timedelta, previous_end: datetime | None) -> datetime:
    """Place a chunk on the camera timeline: it ended when it arrived, never before the last one.

    Phone clocks are untrusted, so the server's receive time anchors the chunk.
    """
    start = received - duration
    return start if previous_end is None or start >= previous_end else previous_end


@dataclass
class _Phone:
    camera: PairedCamera
    next_index: int = 0
    last_end: datetime | None = None
    latest: Chunk | None = None
    bars_chunk_id: str | None = None
    bars: dict[str, float] = field(default_factory=dict)
    hint: str | None = None
    hint_version: int = 0
    ingest_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    coach_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class PairPhone:
    """Pairs phones as cameras, turns their uploads into `Chunk`s and coaches their placement."""

    def __init__(
        self,
        *,
        bus: Bus,
        clock: Clock,
        segmenter: FrameSegmenter,
        settings: Settings,
        concepts: Callable[[], Sequence[str]] = tuple,
        out_dir: Path | None = None,
        fps: float = PHONE_FPS,
        max_chunk_seconds: float = MAX_CHUNK_SECONDS,
        coach_interval_s: float = COACH_INTERVAL_S,
    ) -> None:
        """Create the service.

        Args:
            bus: Bus to publish `chunk.ready` on.
            clock: Time source for token expiry, rate limits and chunk timestamps.
            segmenter: Single-frame segmenter for the placement coach (hosted SAM, P1-02).
            settings: Supplies `public_tunnel_url` and the data directory.
            concepts: Returns the concepts the coach checks (the current plan's concepts).
            out_dir: Where chunks are written; defaults to `<data dir>/chunks`.
            fps: Frame rate of the transcoded chunks.
            max_chunk_seconds: Longest accepted upload.
            coach_interval_s: Seconds between coach updates.
        """
        self._bus = bus
        self._clock = clock
        self._segmenter = segmenter
        self._concepts = concepts
        self._public_url = settings.public_tunnel_url
        self._out_dir = out_dir or settings.rulecam_data_dir / "chunks"
        self._incoming = self._out_dir / ".incoming"
        self._frames = self._out_dir / ".coach"
        for directory in (self._out_dir, self._incoming, self._frames):
            directory.mkdir(parents=True, exist_ok=True)
        self._fps = fps
        self._max_seconds = max_chunk_seconds
        self.coach_interval_s = coach_interval_s
        first = next_phone_number(path.name for path in self._out_dir.glob("phone-*.mp4"))
        self._registry = PairingRegistry(clock, first_phone=first)
        self._limiter = RateLimiter(clock)
        self._phones: dict[str, _Phone] = {}

    @property
    def registry(self) -> PairingRegistry:
        """The token registry (issue, claim, authenticate)."""
        return self._registry

    def issue_token(self, page_path: Callable[[str], str], request_base: str) -> PairToken:
        """Issue a single-use token with its pairing URL and QR code.

        Args:
            page_path: Maps a token to the URL path of its phone page.
            request_base: Base URL of the current request, used when no tunnel URL is set.
        """
        issued: IssuedToken = self._registry.issue()
        url = page_url(self._public_url, request_base, page_path(issued.token))
        _log.info("pair_token_issued", expires_at=issued.expires_at.isoformat())
        return PairToken(
            token=issued.token,
            url=url,
            qr_png_base64=qr_png_base64(url),
            expires_at=issued.expires_at,
        )

    def claim(self, token: str, label: str) -> tuple[PairedCamera, str]:
        """Claim `token` for a phone; see `PairingRegistry.claim`."""
        camera, session = self._registry.claim(token, label)
        self._phones[camera.camera_id] = _Phone(camera=camera)
        _log.info("phone_paired", camera_id=camera.camera_id)
        return camera, session

    def retry_after(self, token: str) -> float:
        """Count a request against `token`'s rate limit; return seconds to wait (0 if allowed)."""
        return self._limiter.retry_after(token)

    def concepts(self) -> tuple[str, ...]:
        """The concepts the coach currently checks."""
        return tuple(self._concepts())

    async def ingest(self, camera_id: str, data: bytes, kind: UploadKind) -> Chunk:
        """Validate one upload, transcode it to H.264 faststart, and publish it as a `Chunk`.

        Uploads from one phone are processed one at a time, so its chunks publish in order.

        Args:
            camera_id: A paired phone.
            data: The raw upload (already size-checked).
            kind: The declared upload kind.

        Returns:
            The published chunk.

        Raises:
            UnsupportedMediaError: Not an allowed video.
            ChunkTooLargeError: Longer than `max_chunk_seconds`.
            TranscodeError: ffmpeg failed on it.
        """
        phone = self._phones[camera_id]
        raw = self._incoming / f"{uuid.uuid4().hex}.{kind}"
        async with phone.ingest_lock:
            await asyncio.to_thread(raw.write_bytes, data)
            try:
                return await self._transcode_and_publish(phone, raw, kind)
            finally:
                raw.unlink(missing_ok=True)

    async def coach_bars(self, camera_id: str) -> CoachBars | None:
        """Return signal bars for the phone's latest chunk, or None before its first chunk.

        Each chunk is segmented once; later calls reuse the result until a new chunk arrives.
        """
        phone = self._phones[camera_id]
        async with phone.coach_lock:
            latest = phone.latest
            if latest is None:
                return None
            if phone.bars_chunk_id != latest.chunk_id:
                frame = self._frames / f"{camera_id}.jpg"
                await run_tool(last_frame_args(Path(latest.path), frame), timeout_s=PROBE_TIMEOUT_S)
                phone.bars = await measure_bars(self._segmenter, frame, self.concepts())
                phone.bars_chunk_id = latest.chunk_id
            return CoachBars(camera_id=camera_id, chunk_id=latest.chunk_id, bars=phone.bars)

    def push_hint(self, camera_id: str, text: str) -> None:
        """Send a one-line placement hint to a phone's coach (used by the planner, P2)."""
        phone = self._phones[camera_id]
        phone.hint = clean_hint(text)
        phone.hint_version += 1

    def hint(self, camera_id: str) -> tuple[int, str | None]:
        """Return the phone's current hint and its version (bumped on every push)."""
        phone = self._phones[camera_id]
        return phone.hint_version, phone.hint

    async def _transcode_and_publish(self, phone: _Phone, raw: Path, kind: UploadKind) -> Chunk:
        info = await probe_upload(raw, kind)
        if info.duration_s is not None and info.duration_s > self._max_seconds:
            msg = f"chunk lasts {info.duration_s:.1f} s; the limit is {self._max_seconds:g} s"
            raise ChunkTooLargeError(msg)
        chunk_id = f"{phone.camera.camera_id}_{phone.next_index:06d}"
        phone.next_index += 1
        path = self._out_dir / f"{chunk_id}.mp4"
        args = transcode_args(
            raw,
            path,
            kind=kind,
            fps=self._fps,
            chunk_seconds=CHUNK_SECONDS,
            max_seconds=self._max_seconds,
        )
        try:
            await run_tool(args, timeout_s=TRANSCODE_TIMEOUT_S)
            probed = await asyncio.to_thread(probe_chunk, path)
        except CaptureError as exc:
            path.unlink(missing_ok=True)
            msg = f"could not transcode the upload into {chunk_id}"
            raise TranscodeError(msg) from exc
        return self._publish(phone, chunk_id, path, probed)

    def _publish(self, phone: _Phone, chunk_id: str, path: Path, probed: ChunkInfo) -> Chunk:
        duration = timedelta(seconds=probed.n_frames / probed.fps)
        start = chunk_start(self._clock.now(), duration, phone.last_end)
        chunk = Chunk(
            chunk_id=chunk_id,
            camera_id=phone.camera.camera_id,
            path=str(path),
            start_ts=start,
            fps=probed.fps,
            n_frames=probed.n_frames,
            width=probed.width,
            height=probed.height,
            provenance=Provenance.RECORDED,
        )
        phone.last_end = start + duration
        phone.latest = chunk
        self._bus.publish(Topic.CHUNK_READY, chunk)
        _log.info("phone_chunk_ready", camera_id=chunk.camera_id, chunk_id=chunk_id)
        return chunk
