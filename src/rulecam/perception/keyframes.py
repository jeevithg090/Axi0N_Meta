"""Runtime keyframes: SAM 3.1 *image* mode on a few frames of each chunk (P1-03, PLAN-9.5 WS2b).

The rules need positions a few times a second, plus identities and speeds, not every frame. So
the image-mode runtime picks keyframes from each 3 s chunk (`keyframe_indices`, 2 to 4 per second,
per camera: `KeyframeRates`), decodes only those (`decode_keyframes`, in memory, JPEG), and asks
SAM 3.1 image detection for every concept on each. `perception.keyframe_tracker` then joins the
detections into tracks with an open-source multi-object tracker.

Two `KeyframeSegmenter`s serve a chunk's keyframes:

- `SelfHostedKeyframes`: one `POST /segment_image` to our GPU pod for all keyframes and concepts
  of a chunk; the server encodes each image once and runs the text-conditioned detector per
  concept on it. Image mode fits easily on an L4 (no video session state). No per-call bill.
- `HostedKeyframes`: hosted SAM 3.1 takes one image and one concept per request, so a chunk is
  `keyframes x concepts` requests, sent concurrently on a bounded pool (shared across cameras,
  `RULECAM_SAM_MAX_CONCURRENCY`). Each is billed as one image ($2.50 per 1,000).

The router's `KeyframeFallback` chains them: self-hosted first, hosted while it is failing.

At the minimum rate of 2 per second, keyframes are never further apart than the timeline's hold
(`rules.timeline.HOLD_S`, 0.5 s), so the rules engine interpolates a track between two keyframes
instead of seeing it vanish. Detections keep the chunk's frame index and timestamp of their
keyframe; their `object_id` only numbers the objects of one image (identity is the tracker's job).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import cv2
import numpy as np
from pydantic import Field

from rulecam.bus import Bus, Topic
from rulecam.contracts import Chunk, Detection, Frozen, Mask
from rulecam.log import get_logger
from rulecam.perception.errors import PerceptionError
from rulecam.perception.metrics import Backend, PerceptionMetrics
from rulecam.perception.sam_runtime import (
    MAX_IMAGES_PER_CALL,
    MODEL_NAME,
    RELAY_SEGMENT,
    SamImageRequest,
    SamImageResponse,
    SamImageWorker,
    parse_image_response,
)

_log = get_logger(__name__)

MIN_KEYFRAME_FPS = 2.0  # 1 / rules.timeline.HOLD_S: keyframes further apart leave track gaps
MAX_KEYFRAME_FPS = 10.0
DEFAULT_KEYFRAME_FPS = 2.0
JPEG_QUALITY = 90
JPEG = "image/jpeg"
DEFAULT_HOSTED_CONCURRENCY = 8

StageSink = Callable[[str, str, str, float], None]
"""`(stage, camera_id, chunk_id, seconds)`, e.g. `runtime.timings.StageTimings.record`."""


class KeyframeRates(Frozen):
    """Keyframes per second per camera (`RULECAM_KEYFRAME_FPS` and its per-camera overrides)."""

    default_fps: float = Field(
        default=DEFAULT_KEYFRAME_FPS, ge=MIN_KEYFRAME_FPS, le=MAX_KEYFRAME_FPS
    )
    per_camera: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def parse(cls, default_fps: float, spec: str) -> KeyframeRates:
        """Build the rates from a default and a `cam1=4,cam2=3` override list.

        Args:
            default_fps: Keyframes per second for cameras without an override.
            spec: Comma-separated `camera_id=fps` pairs (empty: no overrides).

        Returns:
            The rates.

        Raises:
            ValueError: If a pair is malformed or a rate is outside 2 to 10 per second.
        """
        overrides: dict[str, float] = {}
        for item in (part.strip() for part in spec.split(",")):
            if not item:
                continue
            camera_id, sep, value = item.partition("=")
            if not sep or not camera_id.strip():
                msg = f"keyframe rate override {item!r} is not camera_id=fps"
                raise ValueError(msg)
            overrides[camera_id.strip()] = _checked_fps(float(value))
        return cls(default_fps=_checked_fps(default_fps), per_camera=overrides)

    def fps_for(self, camera_id: str) -> float:
        """Keyframes per second for `camera_id`."""
        return self.per_camera.get(camera_id, self.default_fps)


def _checked_fps(fps: float) -> float:
    if not MIN_KEYFRAME_FPS <= fps <= MAX_KEYFRAME_FPS:
        msg = f"keyframe rate {fps:g}/s is outside {MIN_KEYFRAME_FPS:g} to {MAX_KEYFRAME_FPS:g}"
        raise ValueError(msg)
    return fps


def keyframe_indices(n_frames: int, fps: float, keyframe_fps: float) -> tuple[int, ...]:
    """The frames of a chunk to segment: every `floor(fps / keyframe_fps)`-th, from frame 0.

    The step is a whole number of frames rounded down, so keyframes are never further apart
    than `1 / keyframe_fps` seconds, also across the border to the next chunk (a 10 fps chunk at
    3 per second is segmented on frames 0, 3, 6, ...: 0.3 s apart, never 0.4).

    Args:
        n_frames: Frames in the chunk.
        fps: The chunk's frame rate.
        keyframe_fps: Keyframes per second; at or above `fps` every frame is a keyframe.

    Returns:
        Sorted frame indices (empty for an empty chunk).
    """
    step = max(1, math.floor(fps / keyframe_fps + 1e-9))
    return tuple(range(0, max(0, n_frames), step))


def frame_ts(chunk: Chunk, frame_idx: int) -> datetime:
    """Timestamp of frame `frame_idx` of `chunk`."""
    return chunk.start_ts + timedelta(seconds=frame_idx / chunk.fps)


@dataclass(frozen=True)
class Keyframe:
    """One decoded keyframe: its place in the chunk and its JPEG bytes (never written to disk)."""

    frame_idx: int
    ts: datetime
    jpeg: bytes


FrameDecoder = Callable[[Chunk, Sequence[int]], list[Keyframe]]
"""Decodes the given frames of a chunk (`decode_keyframes`, or a fake in tests)."""


def decode_keyframes(
    chunk: Chunk, indices: Sequence[int], *, quality: int = JPEG_QUALITY
) -> list[Keyframe]:
    """Decode the chosen frames of a chunk into JPEGs, in memory, with one sequential read.

    Frames between keyframes are only grabbed, not decoded into pixels.

    Args:
        chunk: The chunk (its `path` is an MP4/MOV file).
        indices: Sorted frame indices to keep.
        quality: JPEG quality.

    Returns:
        One keyframe per index the file has (a shorter file gives fewer).

    Raises:
        PerceptionError: If the file cannot be opened or none of the frames can be read.
    """
    wanted = set(indices)
    last = max(wanted, default=-1)
    capture = cv2.VideoCapture(chunk.path)
    if not capture.isOpened():
        msg = f"cannot open chunk {chunk.chunk_id} for keyframes"
        raise PerceptionError(msg)
    frames: list[Keyframe] = []
    try:
        for index in range(last + 1):
            if not capture.grab():
                break
            if index in wanted:
                frames.append(_encoded(chunk, index, capture, quality))
    finally:
        capture.release()
    if wanted and not frames:
        msg = f"chunk {chunk.chunk_id} has no readable keyframes"
        raise PerceptionError(msg)
    return frames


def _encoded(chunk: Chunk, index: int, capture: cv2.VideoCapture, quality: int) -> Keyframe:
    ok, pixels = capture.retrieve()
    encoded, data = cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok or not encoded:
        msg = f"cannot decode frame {index} of chunk {chunk.chunk_id}"
        raise PerceptionError(msg)
    return Keyframe(frame_idx=index, ts=frame_ts(chunk, index), jpeg=np.asarray(data).tobytes())


@dataclass(frozen=True)
class KeyframeBatch:
    """What one `segment_keyframes` call found and what it cost.

    Attributes:
        detections: Concept -> its detections on the keyframes, by frame then object.
        requests: SAM requests made (hosted: one per keyframe and concept; self-hosted: per call).
        billed_images: Images hosted SAM billed for them (0 when self-hosted served the batch).
        backend: Which deployment served it.
    """

    detections: Mapping[str, tuple[Detection, ...]]
    requests: int
    billed_images: int
    backend: Backend


class KeyframeSegmenter(Protocol):
    """Segments every concept on a chunk's keyframes (SAM 3.1 image mode)."""

    @property
    def billed(self) -> bool:
        """Whether its next calls are billed per image (hosted SAM is serving them)."""
        ...

    def segment_keyframes(
        self, chunk: Chunk, frames: Sequence[Keyframe], concepts: Sequence[str]
    ) -> KeyframeBatch:
        """Return each concept's detections on `frames` of `chunk`."""
        ...


class ImageSam(Protocol):
    """Hosted SAM 3.1 image requests (`HostedSam31.segment_image_bytes`)."""

    def segment_image_bytes(
        self,
        image: bytes,
        concept: str,
        *,
        content_type: str,
        camera_id: str,
        ts: datetime | None,
    ) -> list[Detection]:
        """Segment one concept in one encoded image."""
        ...


def on_keyframe(detections: Sequence[Detection], frame: Keyframe) -> list[Detection]:
    """Place one image's detections on their keyframe of the chunk (frame index and timestamp)."""
    return [d.model_copy(update={"frame_idx": frame.frame_idx, "ts": frame.ts}) for d in detections]


def _by_concept(
    concepts: Sequence[str], found: Mapping[str, list[Detection]]
) -> dict[str, tuple[Detection, ...]]:
    return {
        concept: tuple(sorted(found.get(concept, []), key=lambda d: (d.frame_idx, d.object_id)))
        for concept in concepts
    }


class HostedKeyframes:
    """`KeyframeSegmenter` on hosted SAM 3.1: one image request per keyframe and concept."""

    def __init__(
        self,
        hosted: ImageSam,
        *,
        max_concurrency: int = DEFAULT_HOSTED_CONCURRENCY,
        metrics: PerceptionMetrics | None = None,
        timings: StageSink | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wrap the hosted client.

        Args:
            hosted: Hosted SAM 3.1 (the runtime's box-only view).
            max_concurrency: Image requests open at once, across every camera and chunk.
            metrics: Receives one `record_image_call` per request.
            timings: Receives `sam_queue` (waiting for a request slot) and `sam_request`.
            monotonic: Seconds on a monotonic clock (injected in tests).
        """
        self._hosted = hosted
        self._pool = ThreadPoolExecutor(max(1, max_concurrency), thread_name_prefix="rulecam-img")
        self._metrics = metrics
        self._timings = timings
        self._monotonic = monotonic

    @property
    def billed(self) -> bool:
        """Hosted requests are always billed."""
        return True

    def segment_keyframes(
        self, chunk: Chunk, frames: Sequence[Keyframe], concepts: Sequence[str]
    ) -> KeyframeBatch:
        """Send every (keyframe, concept) request concurrently and gather the detections.

        Raises:
            ModelHTTPError: When a request keeps failing (the others are cancelled).
            ModelResponseError: When a reply cannot be parsed.
            ValueError: For an empty concept.
        """
        asked = self._monotonic()
        jobs = [(frame, concept) for concept in concepts for frame in frames]
        futures: list[Future[list[Detection]]] = [
            self._pool.submit(self._one, chunk, frame, concept, asked) for frame, concept in jobs
        ]
        found: dict[str, list[Detection]] = {concept: [] for concept in concepts}
        try:
            for (_, concept), future in zip(jobs, futures, strict=True):
                found[concept].extend(future.result())
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        return KeyframeBatch(
            detections=_by_concept(concepts, found),
            requests=len(jobs),
            billed_images=len(jobs),
            backend=Backend.HOSTED,
        )

    def _one(self, chunk: Chunk, frame: Keyframe, concept: str, asked: float) -> list[Detection]:
        started = self._monotonic()
        self._report("sam_queue", chunk, started - asked)
        detections = self._hosted.segment_image_bytes(
            frame.jpeg, concept, content_type=JPEG, camera_id=chunk.camera_id, ts=frame.ts
        )
        self._report("sam_request", chunk, self._monotonic() - started)
        if self._metrics is not None:
            self._metrics.record_image_call(chunk.camera_id, backend=Backend.HOSTED, images=1)
        return on_keyframe(detections, frame)

    def _report(self, stage: str, chunk: Chunk, seconds: float) -> None:
        if self._timings is not None:
            self._timings(stage, chunk.camera_id, chunk.chunk_id, seconds)


class SelfHostedKeyframes:
    """`KeyframeSegmenter` on our GPU pod's `POST /segment_image` (one call per chunk)."""

    def __init__(
        self,
        worker: SamImageWorker,
        *,
        bus: Bus | None = None,
        metrics: PerceptionMetrics | None = None,
        timings: StageSink | None = None,
        masks: bool = False,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Wrap the server's worker.

        Args:
            worker: `HttpSamWorker` (or a fake in tests).
            bus: Receives `relay.model_used` after every call.
            metrics: Receives one `record_image_call` per call, with its GPU-seconds.
            timings: Receives `sam_request` per call.
            masks: Ask for masks too (the runtime's foot point needs boxes only).
            timer: Monotonic seconds, for latency.
        """
        self._worker = worker
        self._bus = bus
        self._metrics = metrics
        self._timings = timings
        self._masks = masks
        self._timer = timer

    @property
    def billed(self) -> bool:
        """Self-hosted calls have no per-image bill."""
        return False

    def segment_keyframes(
        self, chunk: Chunk, frames: Sequence[Keyframe], concepts: Sequence[str]
    ) -> KeyframeBatch:
        """Send the keyframes (at most 32 per call) with every concept and parse the answer.

        Raises:
            SamRuntimeError: If the server is unreachable, refuses the call or answers badly.
            ValueError: For an empty or duplicated concept (before any call).
        """
        found: dict[str, list[Detection]] = {concept: [] for concept in concepts}
        calls = 0
        for start in range(0, len(frames), MAX_IMAGES_PER_CALL):
            batch = list(frames[start : start + MAX_IMAGES_PER_CALL])
            for concept, detections in self._call(chunk, batch, concepts).items():
                found[concept].extend(detections)
            calls += 1
        return KeyframeBatch(
            detections=_by_concept(concepts, found),
            requests=calls,
            billed_images=0,
            backend=Backend.SELF_HOSTED,
        )

    def _call(
        self, chunk: Chunk, frames: list[Keyframe], concepts: Sequence[str]
    ) -> dict[str, list[Detection]]:
        request = SamImageRequest(
            concepts=tuple(concepts),
            n_images=len(frames),
            width=chunk.width,
            height=chunk.height,
            masks=self._masks,
        )
        started = self._timer()
        raw = self._worker.segment_images([f.jpeg for f in frames], request.model_dump(mode="json"))
        seconds = self._timer() - started
        response = parse_image_response(raw, request)
        self._record(chunk, len(frames) * len(concepts), response, seconds)
        found: dict[str, list[Detection]] = {}
        for obj in response.objects:
            frame = frames[obj.image_idx]
            found.setdefault(obj.concept, []).append(
                Detection(
                    camera_id=chunk.camera_id,
                    frame_idx=frame.frame_idx,
                    ts=frame.ts,
                    concept=obj.concept,
                    object_id=obj.object_id,
                    mask=Mask(rle=obj.rle, box_xyxy=obj.box_xyxy, confidence=obj.confidence),
                )
            )
        return found

    def _record(
        self, chunk: Chunk, images: int, response: SamImageResponse, seconds: float
    ) -> None:
        if self._timings is not None:
            self._timings("sam_request", chunk.camera_id, chunk.chunk_id, seconds)
        if self._metrics is not None:
            self._metrics.record_image_call(
                chunk.camera_id,
                backend=Backend.SELF_HOSTED,
                images=images,
                gpu_seconds=response.gpu_seconds,
            )
        if self._bus is not None:
            self._bus.publish(
                Topic.RELAY_MODEL_USED,
                {
                    "model": MODEL_NAME,
                    "segment": RELAY_SEGMENT,
                    "ms": round(seconds * 1000),
                    "camera_id": chunk.camera_id,
                },
            )
        _log.debug(
            "sam_self_hosted_images",
            camera_id=chunk.camera_id,
            chunk_id=chunk.chunk_id,
            images=response.images_processed,
            objects=len(response.objects),
            latency_ms=round(seconds * 1000),
            gpu_seconds=response.gpu_seconds,
        )
