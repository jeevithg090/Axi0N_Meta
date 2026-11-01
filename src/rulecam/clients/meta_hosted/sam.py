"""Hosted SAM 3.1 client (`HostedSam31`), the hosted implementation of `Segmenter` (task P1-02).

One concept per request; concepts run in parallel under a semaphore and a token bucket (2,500 RPM
by default, under the 3,000 RPM team limit). Box prompts are split into requests of at most 16
objects. 429 and 5xx answers are retried with jittered exponential backoff (at most 4 tries).
Every request is costed (`max(50, frames)` frames at $0.20 per 1,000; images $2.50 per 1,000) and
publishes `relay.model_used` with its latency.

The HTTP layer is an injected `Transport` (a mapping in, a mapping out). `from_settings` wraps it
in the replay cache (P3-02) in every mode, so REPLAY never touches the network, and tests can
feed recorded-style JSON fixtures without any network. All knowledge of the wire format lives in
`sam_wire`.

Mask payloads are decoded by `meta-sam-parser` in pure Python, about 1.5 us of CPU per mask pixel
while holding the GIL: a 30-frame reply of a large object costs seconds, and replies decoded in
parallel threads only queue for the GIL. The runtime only needs foot points, so it segments with
`runtime_view()` (boxes only, the foot point is the box's bottom centre); probes and calibration
keep the masks. `set_stage_sink` reports each chunk upload and SAM request duration to the
runtime's stage timings.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import random
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.clients.meta_hosted.sam_cost import CostLedger
from rulecam.clients.meta_hosted.sam_masks import decode_wire_mask
from rulecam.clients.meta_hosted.sam_wire import (
    FILE_PURPOSE,
    FILES_PATH,
    IMAGE_CONTENT_TYPES,
    MAX_PROMPTED_OBJECTS,
    MAX_VIDEO_FRAMES,
    RESPONSES_PATH,
    SAM_MODEL,
    VIDEO_CONTENT_TYPES,
    Direction,
    MaskDecoder,
    NegativePoint,
    ObjectPrompt,
    WireRecord,
    frames_processed,
    image_request,
    mask_rle,
    output_text,
    parse_output,
    video_request,
)
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.relay import ModelUsed, publish_model_used
from rulecam.clients.transport import (
    RECORDED_LATENCY_HEADER,
    HttpxTransport,
    RetryPolicy,
    Sender,
    Transport,
)
from rulecam.config import Settings
from rulecam.contracts import Chunk, Clock, Detection, Mask
from rulecam.contracts.core import BoxXYXY
from rulecam.log import get_logger
from rulecam.perception.masks import NO_MASK
from rulecam.replay.cache import ReplayCache

_log = get_logger(__name__)

DEFAULT_RPM = 2500.0
DEFAULT_MAX_CONCURRENCY = 8
RELAY_SEGMENT = "perception"
REPLAY_PROVIDER = "meta"
PROBE_CAMERA_ID = "probe"
_FILE_ID_CACHE_SIZE = 256

StageSink = Callable[[str, str, str, float], None]
"""`(stage, camera_id, chunk_id, seconds)`: told the duration of an `upload` or `sam_request`."""


@dataclass(frozen=True)
class _Reply:
    body: object
    attempts: int
    latency_ms: int


def build_prompts(
    chunk: Chunk,
    concept: str,
    box_prompts: Mapping[int, BoxXYXY] | None,
    negative_points: Iterable[NegativePoint] | None,
    direction: Direction,
) -> list[ObjectPrompt]:
    """Turn `Segmenter.segment` arguments into one prompt per request.

    Without box prompts, `concept` is the text prompt. With box prompts, `concept` only labels
    the detections, and the boxes are split into batches of at most 16 objects. Boxes go on frame
    0 for forward tracking and on the last frame for backward tracking (the API needs a backward
    prompt after frame 0).

    Args:
        chunk: The chunk being segmented.
        concept: The concept noun phrase.
        box_prompts: Object id -> box on the prompt frame, or `None`.
        negative_points: (frame_idx, x, y) points that are not the object, or `None`.
        direction: Tracking direction for box prompts.

    Returns:
        The prompts, one per request.

    Raises:
        ValueError: Before any network call, if text and point prompts would be combined, or
            the chunk cannot be sent.
    """
    _check_chunk(chunk, direction)
    negatives = tuple(negative_points or ())
    if not box_prompts:
        if negatives:
            msg = (
                "negative points need box prompts: SAM 3.1 cannot combine a concept (text) "
                "prompt with point prompts"
            )
            raise ValueError(msg)
        return [ObjectPrompt(text=concept)]
    box_frame = 0 if direction == "forward" else chunk.n_frames - 1
    ids = sorted(box_prompts)
    return [
        ObjectPrompt(
            boxes={i: box_prompts[i] for i in ids[start : start + MAX_PROMPTED_OBJECTS]},
            box_frame=box_frame,
            negative_points=negatives,
        )
        for start in range(0, len(ids), MAX_PROMPTED_OBJECTS)
    ]


def _check_chunk(chunk: Chunk, direction: Direction) -> None:
    suffix = Path(chunk.path).suffix.lower()
    if suffix not in VIDEO_CONTENT_TYPES:
        msg = f"SAM 3.1 video takes MP4 or MOV, got {suffix or 'no extension'}"
        raise ValueError(msg)
    if chunk.n_frames > MAX_VIDEO_FRAMES:
        msg = f"SAM 3.1 takes at most {MAX_VIDEO_FRAMES} frames, chunk has {chunk.n_frames}"
        raise ValueError(msg)
    if direction == "backward" and chunk.n_frames < 2:  # noqa: PLR2004 - needs a frame after 0
        msg = "backward propagation needs a prompt on a frame after frame 0"
        raise ValueError(msg)


class HostedSam31:
    """SAM 3.1 on Meta's hosted Model API; satisfies the `Segmenter` Protocol."""

    def __init__(
        self,
        transport: Transport,
        *,
        clock: Clock,
        bus: Bus | None = None,
        rpm: float = DEFAULT_RPM,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        retry: RetryPolicy | None = None,
        mask_decoder: MaskDecoder | None = decode_wire_mask,
        ledger: CostLedger | None = None,
        model: str = SAM_MODEL,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        seed: int | None = None,
    ) -> None:
        """Create the client.

        Args:
            transport: Sends one request mapping and returns the reply mapping.
            clock: Time source for single-image probes.
            bus: Bus for `relay.model_used`; `None` publishes nothing.
            rpm: Request budget per minute for this client.
            max_concurrency: Concepts segmented at once by `segment_concepts`.
            retry: Backoff policy (default: 4 tries, 0.5 s doubling, jittered).
            mask_decoder: Decodes mask payloads (default: `meta-sam-parser` via
                `decode_wire_mask`); `None` skips decoding, so masks are `NO_MASK` and only boxes
                are returned.
            ledger: Cost counters to add to (a new ledger by default).
            model: The model id.
            sleep: Blocking sleep (injected in tests).
            monotonic: Monotonic time source for latency and the bucket (injected in tests).
            seed: Seed for the retry jitter.
        """
        self._clock = clock
        self._bus = bus
        self._model = model
        self._monotonic = monotonic
        self._max_concurrency = max_concurrency
        self._mask_decoder = mask_decoder
        self._ledger = ledger or CostLedger()
        limiter = TokenBucket(rpm, monotonic=monotonic, sleep=sleep)
        jitter = random.Random(seed)  # noqa: S311 - retry jitter, not cryptography
        self._sender = Sender(transport, limiter, retry or RetryPolicy(), sleep, jitter)
        self._file_ids: OrderedDict[str, str] = OrderedDict()
        self._upload_lock = threading.Lock()  # guards the two dicts only, never the network
        self._digest_locks: dict[str, threading.Lock] = {}
        self._stage_sink: StageSink | None = None
        if mask_decoder is None:
            _log.warning("sam_mask_decoder_missing", hint="masks fall back to boxes")

    def runtime_view(self, *, masks: bool = False) -> HostedSam31:
        """A client for the runtime path, sharing this one's limits and caches.

        It shares the rate limiter, retries, cost ledger and uploaded-file cache, so the two
        never upload a chunk twice or exceed the team's request budget together, but has its
        own stage sink. Without `masks` it returns boxes only (`NO_MASK`): decoding a mask is
        CPU-bound pure Python under the GIL (module docstring).

        Args:
            masks: Whether to decode masks (only if this client decodes them).

        Returns:
            The view.
        """
        view = copy.copy(self)
        if not masks:
            view._mask_decoder = None
        view._stage_sink = None
        return view

    def set_stage_sink(self, sink: StageSink | None) -> None:
        """Report each chunk upload (`upload`) and SAM request (`sam_request`) to `sink`.

        Args:
            sink: Called as `sink(stage, camera_id, chunk_id, seconds)`; `None` stops it.
        """
        self._stage_sink = sink

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Clock,
        bus: Bus | None = None,
        cache: ReplayCache | None = None,
    ) -> HostedSam31:
        """Build a client whose transport always goes through the replay cache.

        In `replay` mode no API key is needed: hits come from the cache and a miss raises
        `ReplayMiss`. In `live` and `record` mode the cache wraps a real `HttpxTransport`.

        Args:
            settings: Application settings.
            clock: Time source.
            bus: Bus for `relay.model_used` (and `mode.changed` of a cache built here).
            cache: The replay cache to use; by default one is built from `settings`.

        Returns:
            The client.

        Raises:
            ClientConfigError: In `live` or `record` mode when `META_API_BASE_URL` or
                `META_API_KEY` is missing.
        """
        if cache is None:
            cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
        inner = _live_transport(settings, required=cache.mode != "replay")
        transport = cache.wrap_transport(REPLAY_PROVIDER, SAM_MODEL, inner)
        return cls(transport, clock=clock, bus=bus)

    @property
    def costs(self) -> CostLedger:
        """Per-camera cost counters for the cost panel."""
        return self._ledger

    def segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: dict[int, tuple[float, float, float, float]] | None = None,
        negative_points: list[tuple[int, float, float]] | None = None,
        direction: Direction = "forward",
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Segment one concept in one chunk.

        Call from the event-loop thread (or with no bus): it publishes on the bus directly.
        Worker threads should use `segment_concepts`.

        Args:
            chunk: An MP4/MOV faststart chunk.
            concept: The concept noun phrase (the label only, when box prompts are given).
            box_prompts: Object id -> box on the prompt frame; more than 16 are split.
            negative_points: (frame_idx, x, y) points that are not the object; need box prompts.
            direction: Tracking direction for box prompts.
            include_confidence: Whether detections carry confidences.

        Returns:
            Detections sorted by frame index, then object id; `[]` when nothing matched.

        Raises:
            ValueError: Before any network call, for invalid prompt combinations or chunks.
            ModelHTTPError: When the API keeps failing or rejects the request.
            ModelResponseError: When the reply cannot be parsed.
        """
        detections, events = self._segment(
            chunk,
            concept,
            box_prompts=box_prompts,
            negative_points=negative_points,
            direction=direction,
            include_confidence=include_confidence,
        )
        for event in events:
            publish_model_used(self._bus, event)
        return detections

    async def segment_concepts(
        self, chunk: Chunk, concepts: Iterable[str], *, include_confidence: bool = True
    ) -> dict[str, list[Detection]]:
        """Segment several concepts in one chunk in parallel, one request per concept.

        Args:
            chunk: The chunk.
            concepts: Concept noun phrases; duplicates are segmented once.
            include_confidence: Whether detections carry confidences.

        Returns:
            Concept -> its sorted detections.
        """
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def one(concept: str) -> tuple[str, list[Detection], list[ModelUsed]]:
            async with semaphore:
                detections, events = await asyncio.to_thread(
                    self._segment,
                    chunk,
                    concept,
                    box_prompts=None,
                    negative_points=None,
                    direction="forward",
                    include_confidence=include_confidence,
                )
            return concept, detections, events

        results = await asyncio.gather(*(one(c) for c in dict.fromkeys(concepts)))
        out: dict[str, list[Detection]] = {}
        for concept, detections, events in results:
            for event in events:
                publish_model_used(self._bus, event)
            out[concept] = detections
        return out

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = PROBE_CAMERA_ID,
        ts: datetime | None = None,
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Segment one concept in a single image (planner probes, Rule CI checks).

        Args:
            frame: A JPEG or PNG file.
            concept: The concept noun phrase.
            camera_id: The camera the frame came from (for detections and cost counters).
            ts: The frame's timestamp; defaults to the injected clock's now.
            include_confidence: Whether detections carry confidences.

        Returns:
            Detections on frame 0, sorted by object id; `[]` when nothing matched.

        Raises:
            ValueError: If the file type is not supported or the concept is empty.
            ModelHTTPError: When the API keeps failing or rejects the request.
            ModelResponseError: When the reply cannot be parsed.
        """
        content_type = IMAGE_CONTENT_TYPES.get(frame.suffix.lower())
        if content_type is None:
            msg = f"segment_image takes JPEG or PNG, got {frame.suffix or 'no extension'}"
            raise ValueError(msg)
        return self.segment_image_bytes(
            frame.read_bytes(),
            concept,
            content_type=content_type,
            camera_id=camera_id,
            ts=ts,
            include_confidence=include_confidence,
        )

    def segment_image_bytes(
        self,
        image: bytes,
        concept: str,
        *,
        content_type: str = "image/jpeg",
        camera_id: str = PROBE_CAMERA_ID,
        ts: datetime | None = None,
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Segment one concept in an encoded image held in memory (runtime keyframes).

        Billed as one image ($2.50 per 1,000), whatever its size. Like `segment`, it publishes
        `relay.model_used` on the bus directly (the bus hands it to its loop from other threads).

        Args:
            image: JPEG or PNG bytes.
            concept: The concept noun phrase.
            content_type: `image/jpeg` or `image/png`.
            camera_id: The camera the frame came from (for detections and cost counters).
            ts: The frame's timestamp; defaults to the injected clock's now.
            include_confidence: Whether detections carry confidences.

        Returns:
            Detections on frame 0, sorted by object id; `[]` when nothing matched.

        Raises:
            ValueError: If the content type is not JPEG or PNG.
            ModelHTTPError: When the API keeps failing or rejects the request.
            ModelResponseError: When the reply cannot be parsed.
        """
        if content_type not in IMAGE_CONTENT_TYPES.values():
            msg = f"segment_image takes JPEG or PNG, got {content_type}"
            raise ValueError(msg)
        encoded = base64.b64encode(image).decode("ascii")
        body = image_request(
            concept,
            image_data_url=f"data:{content_type};base64,{encoded}",
            include_confidence=include_confidence,
            model=self._model,
        )
        reply = self._post(body)
        self._ledger.record_image(camera_id)
        stamp = ts or self._clock.now()
        records = parse_output(output_text(reply.body))
        detections = [
            self._detection(r, camera_id, concept, stamp, include_confidence) for r in records
        ]
        detections.sort(key=lambda d: d.object_id)
        publish_model_used(self._bus, self._model_used(reply, camera_id))
        _log.info("sam_image", camera_id=camera_id, concept=concept, n=len(detections))
        return detections

    # --- internals ------------------------------------------------------------------------------

    def _segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: Mapping[int, BoxXYXY] | None,
        negative_points: Iterable[NegativePoint] | None,
        direction: Direction,
        include_confidence: bool,
    ) -> tuple[list[Detection], list[ModelUsed]]:
        prompts = build_prompts(chunk, concept, box_prompts, negative_points, direction)
        file_id = self._timed_upload(chunk)
        detections: list[Detection] = []
        events: list[ModelUsed] = []
        for prompt in prompts:
            started = self._monotonic()
            body = video_request(
                prompt,
                file_id=file_id,
                width=chunk.width,
                height=chunk.height,
                direction=direction,
                include_confidence=include_confidence,
                model=self._model,
            )
            reply = self._post(body)
            frames = frames_processed(reply.body)
            billed = self._ledger.record_video(chunk.camera_id, frames or chunk.n_frames)
            replied = self._monotonic()
            records = parse_output(output_text(reply.body))
            detections.extend(
                self._detection(
                    r, chunk.camera_id, concept, _frame_ts(chunk, r), include_confidence
                )
                for r in records
            )
            events.append(self._model_used(reply, chunk.camera_id))
            done = self._monotonic()
            self._report("sam_request", chunk, done - started)
            _log.info(
                "sam_video",
                camera_id=chunk.camera_id,
                chunk_id=chunk.chunk_id,
                concept=concept,
                n_prompted=len(prompt.boxes),
                n_records=len(records),
                billed_frames=billed,
                latency_ms=reply.latency_ms,
                decode_ms=round((done - replied) * 1000),
                masks=self._mask_decoder is not None,
            )
        detections.sort(key=lambda d: (d.frame_idx, d.object_id))
        return detections, events

    def _report(self, stage: str, chunk: Chunk, seconds: float) -> None:
        if self._stage_sink is not None:
            self._stage_sink(stage, chunk.camera_id, chunk.chunk_id, seconds)

    def _timed_upload(self, chunk: Chunk) -> str:
        """Upload the chunk (or reuse its file id) and report how long it took."""
        started = self._monotonic()
        file_id, uploaded = self._upload(chunk)
        seconds = self._monotonic() - started
        if uploaded:
            self._report("upload", chunk, seconds)
            _log.info(
                "sam_upload",
                camera_id=chunk.camera_id,
                chunk_id=chunk.chunk_id,
                latency_ms=round(seconds * 1000),
            )
        return file_id

    def _upload(self, chunk: Chunk) -> tuple[str, bool]:
        """Return the chunk's file id, and whether this call (rather than another) uploaded it."""
        path = Path(chunk.path)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with self._upload_lock:
            cached = self._file_ids.get(digest)
            if cached is not None:
                self._file_ids.move_to_end(digest)
                return cached, False
            digest_lock = self._digest_locks.setdefault(digest, threading.Lock())
        # Different chunks upload in parallel (the runtime keeps several in flight); concurrent
        # calls for the same chunk (one per concept) wait for the first upload and reuse its id.
        with digest_lock:
            with self._upload_lock:
                cached = self._file_ids.get(digest)
            if cached is not None:
                return cached, False
            request: dict[str, object] = {
                "method": "POST",
                "path": FILES_PATH,
                "form": {"purpose": FILE_PURPOSE},
                "file": {
                    "filename": path.name,
                    "content_type": VIDEO_CONTENT_TYPES[path.suffix.lower()],
                    "content": content,
                },
            }
            body = self._sender.send(request).reply.body
            file_id = body.get("id") if isinstance(body, Mapping) else None
            if not isinstance(file_id, str) or not file_id:
                msg = "file upload reply has no id"
                raise ModelResponseError(msg)
            with self._upload_lock:
                self._file_ids[digest] = file_id
                if len(self._file_ids) > _FILE_ID_CACHE_SIZE:
                    self._file_ids.popitem(last=False)
                self._digest_locks.pop(digest, None)
            return file_id, True

    def _post(self, body: dict[str, object]) -> _Reply:
        start = self._monotonic()
        result = self._sender.send({"method": "POST", "path": RESPONSES_PATH, "json": body})
        latency_ms = _recorded_latency_ms(result.reply.header(RECORDED_LATENCY_HEADER))
        if latency_ms is None:
            latency_ms = max(0, round((self._monotonic() - start) * 1000))
        return _Reply(body=result.reply.body, attempts=result.attempts, latency_ms=latency_ms)

    def _model_used(self, reply: _Reply, camera_id: str) -> ModelUsed:
        return ModelUsed(
            model=self._model,
            segment=RELAY_SEGMENT,
            latency_ms=reply.latency_ms,
            camera_id=camera_id,
            attempts=reply.attempts,
        )

    def _detection(
        self,
        record: WireRecord,
        camera_id: str,
        concept: str,
        ts: datetime,
        include_confidence: bool,
    ) -> Detection:
        rle = mask_rle(record, self._mask_decoder)
        if rle == NO_MASK and self._mask_decoder is not None:
            _log.warning("sam_mask_unplaced", camera_id=camera_id, frame_idx=record.frame_idx)
        confidence = record.confidence if record.confidence is not None else record.mask.confidence
        return Detection(
            camera_id=camera_id,
            frame_idx=record.frame_idx,
            ts=ts,
            concept=concept,
            object_id=record.object_id,
            mask=Mask(
                rle=rle,
                box_xyxy=record.box_xyxy,
                confidence=confidence if include_confidence else None,
            ),
        )


def _live_transport(settings: Settings, *, required: bool) -> Transport:
    """Return the real HTTP transport, or a no-network stand-in when allowed and unconfigured."""
    if settings.meta_api_base_url and settings.meta_api_key is not None:
        return HttpxTransport(settings.meta_api_base_url, settings.meta_api_key.get_secret_value())
    if required:
        msg = "META_API_BASE_URL and META_API_KEY are required for hosted SAM 3.1"
        raise ClientConfigError(msg)
    return _no_network


def _no_network(request: Mapping[str, object]) -> Mapping[str, object]:
    """Stand-in transport for replay without credentials; reached only after a mode switch."""
    msg = f"no Meta API credentials configured to send {request.get('path')} live"
    raise ClientConfigError(msg)


def _recorded_latency_ms(value: str | None) -> int | None:
    """Parse the replay cache's recorded-latency header; `None` when absent or invalid."""
    if value is None:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None


def _frame_ts(chunk: Chunk, record: WireRecord) -> datetime:
    return chunk.start_ts + timedelta(seconds=record.frame_idx / chunk.fps)
