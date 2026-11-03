"""Self-hosted SAM 3.1 segmenter: `ModalSam31` (task P1-03).

`ModalSam31` implements the `Segmenter` Protocol with the hosted client's request and response
semantics (P1-02), so the router can swap one for the other:

- one concept per call;
- without box prompts the concept is the text prompt; with box prompts (identity hand-off) the
  concept only labels the detections. Text prompts and box or point prompts are mutually
  exclusive, so `negative_points` need box prompts. A `ValueError` is raised before any call;
- at most 16 prompted objects per call; larger sets are split into several calls;
- zero matches returns `[]`, not an error; detections are sorted by frame index.

The GPU side lives in `sam_worker` (the Modal app `rulecam-sam`, with one warm container). Calls
cross the Modal boundary as JSON dicts, the `model_dump(mode="json")` of `SamRequest` and
`SamResponse`. A worker can therefore be the deployed Modal class (`ModalWorker`, the default),
our own GPU pod behind `sam_server` (`HttpSamWorker`, selected by `SAM_BASE_URL`),
`sam_worker.Sam31Engine` running in-process on a site GPU, or a fake in tests. Each request
carries the chunk's SHA-256 so the replay cache (P3-02) can key it.

Every call publishes `relay.model_used` (`{model, segment, ms, camera_id}`) and records its
frames and GPU-seconds in `PerceptionMetrics`.
"""

from __future__ import annotations

import base64
import hashlib
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, Self

from pydantic import Field, SecretStr, ValidationError, model_validator

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ModelClientError
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.transport import HttpxTransport, RetryPolicy, Sender, Transport
from rulecam.config import Settings, get_settings
from rulecam.contracts.core import BoxXYXY, Chunk, Detection, Frozen, Mask
from rulecam.log import get_logger
from rulecam.perception.errors import SamResponseError, SamRuntimeUnavailableError
from rulecam.perception.metrics import Backend, PerceptionMetrics
from rulecam.perception.sam_worker import MODAL_APP_NAME, MODAL_CLASS_NAME

MODEL_NAME = "sam3.1-self-hosted"
RELAY_SEGMENT = "perception"
MAX_PROMPTED_OBJECTS = 16
HTTP_SEGMENT_PATH = "/segment"  # sam_server.SEGMENT_PATH (that module stays free of pydantic)
HTTP_SEGMENT_IMAGE_PATH = "/segment_image"  # sam_server.SEGMENT_IMAGE_PATH
SAM_KEY_HEADER = "X-Rulecam-Key"  # sam_server.KEY_HEADER
MAX_IMAGES_PER_CALL = 32  # keyframes of one chunk: 3 s at up to 10 fps
MAX_IMAGE_CONCEPTS = 16
HTTP_TIMEOUT_S = 60.0
HTTP_RETRY = RetryPolicy(max_attempts=2, base_delay_s=0.25, max_delay_s=1.0)
HTTP_RATE_PER_MINUTE = 1200.0  # far above what one GPU serves; the server's queue pushes back

_log = get_logger(__name__)


class BoxPrompt(Frozen):
    """A box prompt for one object on the start frame (pixels, XYXY)."""

    object_id: int
    box_xyxy: BoxXYXY


class NegativePoint(Frozen):
    """A negative click: the object under it is not the concept (pixels)."""

    frame_idx: int = Field(ge=0)
    x: float
    y: float


class SamRequest(Frozen):
    """One self-hosted SAM 3.1 call: one concept, one chunk, at most 16 prompted objects."""

    text: str | None = Field(default=None, min_length=1)
    box_prompts: tuple[BoxPrompt, ...] = ()
    negative_points: tuple[NegativePoint, ...] = ()
    direction: Literal["forward", "backward"] = "forward"
    include_confidence: bool = True
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    n_frames: int = Field(gt=0)
    video_sha256: str

    @model_validator(mode="after")
    def _check_prompts(self) -> Self:
        if (self.text is None) == (not self.box_prompts):
            msg = "a SAM request takes a text prompt or box prompts, never both or neither"
            raise ValueError(msg)
        if self.text is not None and self.negative_points:
            msg = "point prompts (negative_points) cannot be combined with a text prompt"
            raise ValueError(msg)
        if len(self.box_prompts) > MAX_PROMPTED_OBJECTS:
            msg = f"at most {MAX_PROMPTED_OBJECTS} prompted objects per call"
            raise ValueError(msg)
        if any(point.frame_idx >= self.n_frames for point in self.negative_points):
            msg = "a negative point is outside the chunk"
            raise ValueError(msg)
        return self


class SamObject(Frozen):
    """One object in one frame, as returned by a worker."""

    frame_idx: int = Field(ge=0)
    object_id: int
    rle: str
    box_xyxy: BoxXYXY
    confidence: float | None = None


class SamResponse(Frozen):
    """A worker's answer to one `SamRequest`."""

    objects: tuple[SamObject, ...] = ()
    frames_processed: int = Field(ge=0)
    gpu_seconds: float = Field(ge=0)
    model_version: str


class SamWorker(Protocol):
    """Runs SAM 3.1 on one chunk; requests and responses are JSON-shaped dicts."""

    def segment(self, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Return a `SamResponse` dict for a `SamRequest` dict and the chunk's MP4 bytes."""
        ...


class SamImageRequest(Frozen):
    """One self-hosted SAM 3.1 image call: a chunk's keyframes, every concept on every image.

    The server runs the image encoder once per image and the text-conditioned detector once per
    concept on it, so one call for all of a chunk's concepts costs far less GPU than one per
    concept. `masks` false returns boxes only (the runtime needs foot points, not masks).
    """

    concepts: tuple[str, ...] = Field(min_length=1, max_length=MAX_IMAGE_CONCEPTS)
    n_images: int = Field(gt=0, le=MAX_IMAGES_PER_CALL)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    include_confidence: bool = True
    masks: bool = False

    @model_validator(mode="after")
    def _check_concepts(self) -> Self:
        if any(not concept.strip() for concept in self.concepts):
            msg = "an image request's concepts must be non-empty noun phrases"
            raise ValueError(msg)
        if len(set(self.concepts)) != len(self.concepts):
            msg = "an image request's concepts must be distinct"
            raise ValueError(msg)
        return self


class SamImageObject(Frozen):
    """One object of one concept in one image, as returned by the server."""

    image_idx: int = Field(ge=0)
    concept: str
    object_id: int
    rle: str
    box_xyxy: BoxXYXY
    confidence: float | None = None


class SamImageResponse(Frozen):
    """The server's answer to one `SamImageRequest`."""

    objects: tuple[SamImageObject, ...] = ()
    images_processed: int = Field(ge=0)
    gpu_seconds: float = Field(ge=0)
    model_version: str


class SamImageWorker(Protocol):
    """Runs SAM 3.1 image detection on a few keyframes (JPEG bytes); JSON-shaped dicts."""

    def segment_images(self, images: list[bytes], request: dict[str, Any]) -> dict[str, Any]:
        """Return a `SamImageResponse` dict for a `SamImageRequest` dict and the images."""
        ...


def parse_image_response(raw: object, request: SamImageRequest) -> SamImageResponse:
    """Validate the server's answer to an image request.

    Args:
        raw: What the server returned.
        request: The request it answers.

    Returns:
        The validated response.

    Raises:
        SamResponseError: If the answer is malformed, names an image that was not sent or a
            concept that was not asked for.
    """
    try:
        response = SamImageResponse.model_validate(raw)
    except ValidationError as exc:
        msg = "malformed SAM image response"
        raise SamResponseError(msg) from exc
    concepts = set(request.concepts)
    for obj in response.objects:
        if obj.image_idx >= request.n_images or obj.concept not in concepts:
            msg = f"SAM image response names image {obj.image_idx} / concept {obj.concept!r}"
            raise SamResponseError(msg)
    return response


def prompt_batches(
    box_prompts: Mapping[int, BoxXYXY] | None,
) -> list[tuple[BoxPrompt, ...]]:
    """Split box prompts into batches of at most 16 objects, by ascending object id.

    Args:
        box_prompts: Object id to box (pixels, XYXY), or `None`.

    Returns:
        The batches; empty when there are no box prompts.
    """
    if not box_prompts:
        return []
    prompts = [BoxPrompt(object_id=i, box_xyxy=box_prompts[i]) for i in sorted(box_prompts)]
    return [
        tuple(prompts[start : start + MAX_PROMPTED_OBJECTS])
        for start in range(0, len(prompts), MAX_PROMPTED_OBJECTS)
    ]


def build_requests(
    chunk: Chunk,
    concept: str,
    *,
    video_sha256: str,
    box_prompts: Mapping[int, BoxXYXY] | None = None,
    negative_points: list[tuple[int, float, float]] | None = None,
    direction: Literal["forward", "backward"] = "forward",
    include_confidence: bool = True,
) -> list[SamRequest]:
    """Build the worker requests for one `Segmenter.segment` call.

    Args:
        chunk: The chunk to segment.
        concept: The concept; it is the text prompt unless box prompts are given.
        video_sha256: SHA-256 of the chunk file.
        box_prompts: Object id to box on the start frame (identity hand-off).
        negative_points: `(frame_idx, x, y)` clicks on wrong objects; need box prompts.
        direction: Propagation direction.
        include_confidence: Whether to return per-object confidences.

    Returns:
        One request in text mode, or one per batch of 16 box prompts.

    Raises:
        ValueError: If the prompts are invalid (checked before any worker call).
    """
    negatives = tuple(NegativePoint(frame_idx=f, x=x, y=y) for f, x, y in negative_points or ())
    batches = prompt_batches(box_prompts) or [()]
    return [
        SamRequest(
            text=None if batch else concept,
            box_prompts=batch,
            negative_points=negatives,
            direction=direction,
            include_confidence=include_confidence,
            width=chunk.width,
            height=chunk.height,
            n_frames=chunk.n_frames,
            video_sha256=video_sha256,
        )
        for batch in batches
    ]


def parse_response(raw: object, chunk: Chunk) -> SamResponse:
    """Validate a worker's answer against the wire format and the chunk.

    Args:
        raw: What the worker returned.
        chunk: The chunk that was segmented.

    Returns:
        The validated response.

    Raises:
        SamResponseError: If the answer is malformed or refers to frames outside the chunk.
    """
    try:
        response = SamResponse.model_validate(raw)
    except ValidationError as exc:
        msg = f"malformed SAM worker response for chunk {chunk.chunk_id}"
        raise SamResponseError(msg) from exc
    if any(obj.frame_idx >= chunk.n_frames for obj in response.objects):
        msg = f"SAM worker returned a frame outside chunk {chunk.chunk_id}"
        raise SamResponseError(msg)
    return response


def to_detections(chunk: Chunk, concept: str, response: SamResponse) -> list[Detection]:
    """Convert a worker response into contract `Detection`s.

    Args:
        chunk: The chunk that was segmented.
        concept: The concept label for every detection.
        response: The validated worker response.

    Returns:
        Detections in response order.
    """
    return [
        Detection(
            camera_id=chunk.camera_id,
            frame_idx=obj.frame_idx,
            ts=chunk.start_ts + timedelta(seconds=obj.frame_idx / chunk.fps),
            concept=concept,
            object_id=obj.object_id,
            mask=Mask(rle=obj.rle, box_xyxy=obj.box_xyxy, confidence=obj.confidence),
        )
        for obj in response.objects
    ]


def read_video_bytes(path: str) -> bytes:
    """Read a chunk file.

    Args:
        path: The chunk's path.

    Returns:
        Its bytes.
    """
    return Path(path).read_bytes()


class ModalWorker:
    """`SamWorker` that calls the deployed Modal class `rulecam-sam` / `Sam31Worker`.

    Needs the `ml` extra. Modal tokens come from `Settings` when set there, otherwise from
    Modal's own configuration. Any Modal failure becomes `SamRuntimeUnavailableError`.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        app_name: str = MODAL_APP_NAME,
        class_name: str = MODAL_CLASS_NAME,
    ) -> None:
        """Prepare a lazy handle; nothing is contacted until the first call.

        Args:
            settings: Source of the Modal tokens (defaults to `get_settings()`).
            app_name: Deployed app name.
            class_name: Deployed class name.
        """
        self._settings = settings
        self._app_name = app_name
        self._class_name = class_name
        self._handle: Any = None

    def segment(self, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Run one request on the warm Modal GPU container.

        Args:
            video: The chunk's MP4 bytes.
            request: A `SamRequest` dict.

        Returns:
            The worker's response dict.

        Raises:
            SamRuntimeUnavailableError: If Modal is missing, unreachable or the call fails.
        """
        try:
            import modal  # noqa: PLC0415 - the `ml` extra is optional
        except ImportError as exc:
            msg = "self-hosted SAM needs the `ml` extra (modal is not installed)"
            raise SamRuntimeUnavailableError(msg) from exc
        target = f"{self._app_name}/{self._class_name}"
        try:
            result: dict[str, Any] = self._remote(modal).segment.remote(video, request)
        except (modal.exception.Error, OSError) as exc:
            msg = f"Modal call to {target} failed: {exc}"
            raise SamRuntimeUnavailableError(msg) from exc
        return result

    def _remote(self, modal: Any) -> Any:
        if self._handle is None:
            cls = modal.Cls.from_name(self._app_name, self._class_name, client=self._client(modal))
            self._handle = cls()
        return self._handle

    def _client(self, modal: Any) -> Any:
        settings = self._settings or get_settings()
        token_id, token_secret = settings.modal_token_id, settings.modal_token_secret
        if token_id is None or token_secret is None:
            return None
        return modal.Client.from_credentials(
            token_id.get_secret_value(), token_secret.get_secret_value()
        )


def sam_transport(
    base_url: str, api_key: SecretStr, edge_key: SecretStr | None = None
) -> HttpxTransport:
    """`HttpxTransport` to `sam_server`, on a GPU pod or behind a RunPod load balancer.

    Args:
        base_url: `SAM_BASE_URL`.
        api_key: `SAM_API_KEY`, the server's own key.
        edge_key: `SAM_EDGE_KEY`, the RunPod API key a serverless load balancer checks at its
            edge. When set, it is the `Authorization` bearer and the server's key travels in
            `X-Rulecam-Key`.

    Returns:
        The transport.
    """
    if edge_key is None:
        return HttpxTransport(base_url, api_key.get_secret_value(), timeout_s=HTTP_TIMEOUT_S)
    return HttpxTransport(
        base_url,
        edge_key.get_secret_value(),
        timeout_s=HTTP_TIMEOUT_S,
        headers={SAM_KEY_HEADER: api_key.get_secret_value()},
    )


class HttpSamWorker:
    """`SamWorker` that calls `sam_server` on our own GPU pod (`SAM_BASE_URL`, `SAM_API_KEY`).

    Sends `{"request": ..., "video_b64": ...}` to `POST /segment`, and a chunk's keyframes as
    `{"request": ..., "images_b64": [...]}` to `POST /segment_image`, with a Bearer key. A busy GPU
    (503) or a dropped connection is retried once after a short backoff; every other failure
    becomes a `SamRuntimeError`, so the router falls back to hosted SAM at once rather than add
    lag: `SamRuntimeUnavailableError` when the server is unreachable or refuses the call (auth,
    queue full, engine failure), `SamResponseError` when the answer is not a JSON object.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        retry: RetryPolicy = HTTP_RETRY,
        limiter: TokenBucket | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Wrap a transport whose base URL is the SAM server.

        Args:
            transport: Usually `HttpxTransport(SAM_BASE_URL, SAM_API_KEY)`; a fake in tests.
            retry: Backoff for 429, 5xx and transport errors.
            limiter: Client-side rate limit (defaults to `HTTP_RATE_PER_MINUTE`).
            sleep: Blocking sleep between retries (injected in tests).
        """
        self._sender = Sender(
            transport=transport,
            limiter=limiter or TokenBucket(HTTP_RATE_PER_MINUTE),
            policy=retry,
            sleep=sleep,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Build the worker from `sam_base_url` and `sam_api_key`.

        Args:
            settings: Application settings.

        Returns:
            The worker.

        Raises:
            SamRuntimeUnavailableError: If either setting is missing.
        """
        if not settings.sam_base_url or settings.sam_api_key is None:
            msg = "SAM_BASE_URL and SAM_API_KEY must both be set for the self-hosted SAM server"
            raise SamRuntimeUnavailableError(msg)
        edge_key = settings.sam_edge_key
        return cls(sam_transport(settings.sam_base_url, settings.sam_api_key, edge_key))

    def segment(self, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Run one request on the GPU pod.

        Args:
            video: The chunk's MP4 bytes.
            request: A `SamRequest` dict.

        Returns:
            The server's response dict (validated by `ModalSam31`).

        Raises:
            SamRuntimeUnavailableError: If the server is unreachable or answers with an error.
            SamResponseError: If the answer is not a JSON object.
        """
        payload = {"request": request, "video_b64": base64.b64encode(video).decode("ascii")}
        return self._post(HTTP_SEGMENT_PATH, payload)

    def segment_images(self, images: list[bytes], request: dict[str, Any]) -> dict[str, Any]:
        """Run one keyframe batch on the GPU pod (`POST /segment_image`).

        Args:
            images: JPEG bytes of the keyframes, in request order.
            request: A `SamImageRequest` dict.

        Returns:
            The server's response dict (validated by the caller, `parse_image_response`).

        Raises:
            SamRuntimeUnavailableError: If the server is unreachable or answers with an error.
            SamResponseError: If the answer is not a JSON object.
        """
        payload = {
            "request": request,
            "images_b64": [base64.b64encode(image).decode("ascii") for image in images],
        }
        return self._post(HTTP_SEGMENT_IMAGE_PATH, payload)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._sender.send({"method": "POST", "path": path, "json": payload})
        except ModelClientError as exc:
            msg = f"self-hosted SAM server call failed: {exc}"
            raise SamRuntimeUnavailableError(msg) from exc
        body = result.reply.body
        if not isinstance(body, dict):
            msg = "self-hosted SAM server answered without a JSON object"
            raise SamResponseError(msg)
        return body


class ModalSam31:
    """`Segmenter` backed by self-hosted SAM 3.1 (Modal GPU by default)."""

    def __init__(
        self,
        worker: SamWorker | None = None,
        *,
        bus: Bus | None = None,
        metrics: PerceptionMetrics | None = None,
        read_video: Callable[[str], bytes] = read_video_bytes,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        """Create the segmenter.

        Args:
            worker: Runs the model (defaults to the deployed Modal class).
            bus: Receives `relay.model_used` after every call.
            metrics: Receives one `record_sam_call` per worker call.
            read_video: Reads a chunk file.
            timer: Monotonic seconds, for latency.
        """
        self._worker = worker if worker is not None else ModalWorker()
        self._bus = bus
        self._metrics = metrics
        self._read_video = read_video
        self._timer = timer

    def segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: dict[int, tuple[float, float, float, float]] | None = None,
        negative_points: list[tuple[int, float, float]] | None = None,
        direction: Literal["forward", "backward"] = "forward",
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Segment one concept in one chunk.

        Args:
            chunk: The chunk.
            concept: The concept (the text prompt unless box prompts are given).
            box_prompts: Object id to box on the start frame (identity hand-off).
            negative_points: `(frame_idx, x, y)` clicks on wrong objects; need box prompts.
            direction: Propagation direction.
            include_confidence: Whether to return per-object confidences.

        Returns:
            Detections sorted by frame index, then object id; `[]` for zero matches.

        Raises:
            ValueError: If the prompts are invalid (before any worker call).
            SamRuntimeError: If the worker fails or answers with a malformed response.
        """
        video = self._read_video(chunk.path)
        requests = build_requests(
            chunk,
            concept,
            video_sha256=hashlib.sha256(video).hexdigest(),
            box_prompts=box_prompts,
            negative_points=negative_points,
            direction=direction,
            include_confidence=include_confidence,
        )
        detections: list[Detection] = []
        for request in requests:
            response = self._call(chunk, concept, video, request)
            detections.extend(to_detections(chunk, concept, response))
        detections.sort(key=lambda d: (d.frame_idx, d.object_id))
        return detections

    def _call(self, chunk: Chunk, concept: str, video: bytes, request: SamRequest) -> SamResponse:
        started = self._timer()
        raw = self._worker.segment(video, request.model_dump(mode="json"))
        latency_ms = round((self._timer() - started) * 1000)
        response = parse_response(raw, chunk)
        if self._bus is not None:
            self._bus.publish(
                Topic.RELAY_MODEL_USED,
                {
                    "model": MODEL_NAME,
                    "segment": RELAY_SEGMENT,
                    "ms": latency_ms,
                    "camera_id": chunk.camera_id,
                },
            )
        if self._metrics is not None:
            self._metrics.record_sam_call(
                chunk.camera_id,
                backend=Backend.SELF_HOSTED,
                frames=response.frames_processed or chunk.n_frames,
                gpu_seconds=response.gpu_seconds,
            )
        _log.debug(
            "sam_self_hosted_call",
            camera_id=chunk.camera_id,
            chunk_id=chunk.chunk_id,
            concept=concept,
            objects=len(response.objects),
            latency_ms=latency_ms,
            gpu_seconds=response.gpu_seconds,
        )
        return response
