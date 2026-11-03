"""HTTP front for `Sam31Engine` on our own GPU pod (task P1-03, self-hosted on RunPod).

The API pod reaches it through `sam_runtime.HttpSamWorker`. Routes:

- `GET /health`: open, `{"status": "ok", "model_version": ..., "busy": bool, "waiting": int,
  "memory": {...}}`. `memory` is `Sam31Engine.memory_stats()`: CUDA allocated, reserved, peak,
  free and total bytes now and after the last session, the session count, the memory guard and
  its trips, and the effective model settings. Watch `allocated_bytes` across calls: it should
  return to the same level after every session;
- `GET /ping`: open, the health probe of a RunPod serverless load balancer: 204 while the model
  loads (a small placeholder server holds the port meanwhile), 200 once it serves;
- `POST /segment`: needs `Authorization: Bearer <SAM_API_KEY>`, or `X-Rulecam-Key:
  <SAM_API_KEY>` behind RunPod's load balancer, whose edge takes `Authorization` for the RunPod
  key (`KEY_HEADER` wins when both are sent). The body is JSON
  `{"request": <SamRequest JSON>, "video_b64": "<base64 MP4 bytes>"}`, the same two arguments
  `ModalWorker` passes to the Modal method; the answer is the `SamResponse` JSON. Video mode is
  for offline uses (probes, Rule CI scenes, evidence re-checks);
- `POST /segment_image`: the runtime's keyframes (image mode), same key. The body is
  `{"request": <SamImageRequest JSON>, "images_b64": ["<base64 JPEG>", ...]}` and the answer the
  `SamImageResponse` JSON. `Sam31ImageEngine` runs the detector of the already loaded model, so
  it needs no extra weights and keeps no session state. 404 when the server runs without it
  (`SAM_IMAGE=0`).

- `POST /segment_crops`: crop-zoom SAM 3.1 (add-on 3.3, `crop_worker.CropEngine`), same key.
  The body is `{"request": {"concepts": [...], "crops": [[x0, y0, x1, y1], ...],
  "include_confidence": bool}, "image_b64": "<base64 JPEG>"}`: one frame and the head or hand
  boxes to enlarge; the answer lists each crop's detections with boxes in frame pixels. 404 when
  the server runs without image mode.

- `POST /geometry`: MoGe-2 metric point map and intrinsics of one frame, for ground
  calibration (add-on 3.5, `geometry_worker.MogeEngine`), same key. The body is
  `{"request": {"grid": 96}, "image_b64": "<base64 JPEG>"}`. 404 when the `moge` package is not
  installed; `/health` says so under `geometry`.

- `POST /embed`: appearance embeddings of object crops, same key. The body is
  `{"images_b64": ["<base64 JPEG>", ...], "model": "appearance" | "person"}` (at most
  `MAX_EMBED_IMAGES`; `model` defaults to `appearance`), the answer
  `{"model": ..., "embeddings": [[...], ...]}`. `appearance` is DINOv2 (`appearance_worker`, any
  object); `person` is OSNet-AIN person re-ID (`reid_worker`), 404 when the pod has no re-ID
  model. Models load on their first call and share the GPU with SAM 3.1.


Errors are `{"error": {"message": ...}}` with 400 (malformed body), 401 (missing or wrong key),
404, 411 (no `Content-Length`), 413 (body over the limit), 500 (the engine failed) or 503 (the
queue is full; `Retry-After: 1`). One GPU serves one request at a time: other requests wait, up
to `max_waiting` of them, and the rest are refused with 503 at once instead of piling up lag.

Like `sam_worker`, this module runs inside the GPU venv (numpy < 2, `sam3`, torch), so it imports
only the standard library and `sam_worker`, and logs with `logging`, not structlog. The model is
loaded once, before the real server takes the port. Run it with the key in the environment,
never in argv:

    SAM_API_KEY=... python -m rulecam.perception.sam_server --host 0.0.0.0 --port 8090
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from rulecam.perception.crop_worker import check_crops_request
from rulecam.perception.geometry_worker import MogeEngine, check_geometry_request
from rulecam.perception.sam_worker import MODEL_VERSION

DEFAULT_PORT = 8090
DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024  # a 3 s chunk is well under 1 MB; base64 adds a third
DEFAULT_MAX_WAITING = 2
DEFAULT_IMAGE_MAX_WAITING = 8  # cameras x pipeline depth; one keyframe batch takes about a second
API_KEY_ENV = "SAM_API_KEY"
MAX_BODY_ENV = "SAM_MAX_BODY_BYTES"
MAX_WAITING_ENV = "SAM_MAX_WAITING"
IMAGE_MAX_WAITING_ENV = "SAM_IMAGE_MAX_WAITING"
IMAGE_ENV = "SAM_IMAGE"
HEALTH_PATH = "/health"
PING_PATH = "/ping"  # RunPod serverless load balancer: 204 while loading, 200 when ready
KEY_HEADER = "X-Rulecam-Key"  # the key behind RunPod's edge, which keeps `Authorization`
SEGMENT_PATH = "/segment"
SEGMENT_IMAGE_PATH = "/segment_image"
EMBED_PATH = "/embed"
SEGMENT_CROPS_PATH = "/segment_crops"
GEOMETRY_PATH = "/geometry"
MAX_EMBED_IMAGES = 64
APPEARANCE_MODEL = "appearance"  # DINOv2, any object
PERSON_MODEL = "person"  # OSNet-AIN person re-ID

_log = logging.getLogger("rulecam.sam_server")


class Engine(Protocol):
    """What the server needs from `Sam31Engine` (a fake in tests)."""

    def segment(self, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Return a `SamResponse` dict for a `SamRequest` dict and the chunk's MP4 bytes."""
        ...

    def memory_stats(self) -> dict[str, Any]:
        """Return GPU memory counters for `/health` (see `Sam31Engine.memory_stats`)."""
        ...


class ImageEngine(Protocol):
    """What the server needs from `Sam31ImageEngine` (a fake in tests)."""

    def segment_images(self, images: list[bytes], request: dict[str, Any]) -> dict[str, Any]:
        """Return a `SamImageResponse` dict for a `SamImageRequest` dict and its images."""
        ...

    def memory_stats(self) -> dict[str, Any]:
        """Return the image engine's counters for `/health`."""
        ...


class CropEngine(Protocol):
    """What the server needs from `crop_worker.CropEngine` (a fake in tests)."""

    def segment_crops(self, image: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Return the crop-zoom answer for one frame and its crop boxes."""
        ...

    def memory_stats(self) -> dict[str, Any]:
        """Return the crop engine's counters for `/health`."""
        ...


class GeometryEngine(Protocol):
    """What the server needs from `geometry_worker.MogeEngine` (a fake in tests)."""

    def estimate(self, image: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Return the sampled point map and intrinsics of one frame."""
        ...

    def memory_stats(self) -> dict[str, Any]:
        """Return the engine's state for `/health`."""
        ...


class BadRequestError(ValueError):
    """The request body is not a valid `/segment` payload."""


class GpuSlot:
    """Lets one call use the GPU at a time and at most `max_waiting` calls wait for it."""

    def __init__(self, max_waiting: int, gpu: threading.Lock | None = None) -> None:
        """Create a free slot.

        Args:
            max_waiting: Calls allowed to wait while one runs (0 refuses any overlap).
            gpu: The GPU lock, shared by every slot (queue) of one GPU; a new one by default.
        """
        self._max_waiting = max_waiting
        self._gpu = gpu or threading.Lock()
        self._state = threading.Lock()
        self._admitted = 0

    @property
    def busy(self) -> bool:
        """Whether a call is running."""
        return self._gpu.locked()

    @property
    def waiting(self) -> int:
        """How many admitted calls are waiting for the GPU."""
        with self._state:
            return max(0, self._admitted - 1)

    def try_admit(self) -> bool:
        """Admit a call if the queue has room; call `release` after the call when admitted."""
        with self._state:
            if self._admitted > self._max_waiting:
                return False
            self._admitted += 1
            return True

    def run(self, engine: Engine, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Run one admitted call on the GPU, waiting for the previous one to finish."""
        return self.run_call(lambda: engine.segment(video, request))

    def run_call(self, work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Run one admitted piece of GPU work, waiting for the previous one to finish."""
        with self._gpu:
            return work()

    def release(self) -> None:
        """Leave the queue."""
        with self._state:
            self._admitted -= 1


def parse_segment_body(body: bytes) -> tuple[bytes, dict[str, Any]]:
    """Decode a `/segment` body into the chunk bytes and the request dict.

    Args:
        body: The raw JSON body.

    Returns:
        `(video, request)`.

    Raises:
        BadRequestError: If the body is not JSON, or lacks a `request` object or `video_b64`.
    """
    payload = _json_object(body)
    request, video_b64 = payload.get("request"), payload.get("video_b64")
    if not isinstance(request, dict) or not isinstance(video_b64, str):
        msg = "body needs a `request` object and a `video_b64` string"
        raise BadRequestError(msg)
    return _b64(video_b64, "video_b64"), request


def parse_segment_image_body(body: bytes) -> tuple[list[bytes], dict[str, Any]]:
    """Decode a `/segment_image` body into the images and the request dict.

    Args:
        body: The raw JSON body.

    Returns:
        `(images, request)`.

    Raises:
        BadRequestError: If the body is not JSON, or lacks a `request` object or a non-empty
            `images_b64` list of base64 strings.
    """
    payload = _json_object(body)
    request, encoded = payload.get("request"), payload.get("images_b64")
    if not isinstance(request, dict) or not isinstance(encoded, list) or not encoded:
        msg = "body needs a `request` object and a non-empty `images_b64` list"
        raise BadRequestError(msg)
    if not all(isinstance(item, str) for item in encoded):
        msg = "`images_b64` must hold base64 strings"
        raise BadRequestError(msg)
    return [_b64(item, "images_b64") for item in encoded], request


def parse_image_body(body: bytes) -> tuple[bytes, dict[str, Any]]:
    """Decode a one-image body (`/segment_crops`) into the image and the request dict.

    Raises:
        BadRequestError: If the body is not JSON, or lacks a `request` object or `image_b64`.
    """
    payload = _json_object(body)
    request, image_b64 = payload.get("request"), payload.get("image_b64")
    if not isinstance(request, dict) or not isinstance(image_b64, str) or not image_b64:
        msg = "body needs a `request` object and an `image_b64` string"
        raise BadRequestError(msg)
    return _b64(image_b64, "image_b64"), request


class Embedder(Protocol):
    """What `/embed` needs (`appearance_worker.DinoEmbedder`)."""

    def embed(self, images: list[bytes]) -> dict[str, Any]:
        """Embed encoded crops."""
        ...


def parse_embed_body(body: bytes) -> tuple[list[bytes], str]:
    """Decode a `/embed` body into the crops and the model to embed them with.

    Raises:
        BadRequestError: Not JSON, no `images_b64` list, too many images, bad base64, or a
            `model` that is not a string.
    """
    data = _json_object(body)
    images = data.get("images_b64")
    if not isinstance(images, list) or not images or len(images) > MAX_EMBED_IMAGES:
        msg = f"images_b64 must be a list of 1 to {MAX_EMBED_IMAGES} base64 images"
        raise BadRequestError(msg)
    model = data.get("model", APPEARANCE_MODEL)
    if not isinstance(model, str):
        raise BadRequestError("model must be a string")
    try:
        return [base64.b64decode(str(item), validate=True) for item in images], model
    except (binascii.Error, ValueError) as exc:
        raise BadRequestError("images_b64 holds invalid base64") from exc


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "body is not JSON"
        raise BadRequestError(msg) from exc
    if not isinstance(payload, dict):
        msg = "body must be a JSON object"
        raise BadRequestError(msg)
    return payload


def _b64(value: str, name: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        msg = f"`{name}` is not valid base64"
        raise BadRequestError(msg) from exc


def bearer_matches(header: str | None, api_key: str) -> bool:
    """Check an `Authorization` header against the key in constant time.

    Args:
        header: The header value, if any.
        api_key: The expected key.

    Returns:
        True only for `Bearer <api_key>`.
    """
    if header is None or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[len("Bearer ") :].encode(), api_key.encode())


class SamHTTPServer(ThreadingHTTPServer):
    """A threading HTTP server that holds the engine, the key and the GPU slot."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        engine: Engine,
        *,
        api_key: str,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        max_waiting: int = DEFAULT_MAX_WAITING,
        image_engine: ImageEngine | None = None,
        image_max_waiting: int = DEFAULT_IMAGE_MAX_WAITING,
        embedder: Embedder | None = None,
        person_embedder: Embedder | None = None,
        crop_engine: CropEngine | None = None,
        geometry_engine: GeometryEngine | None = None,
    ) -> None:
        """Bind the server.

        Args:
            address: `(host, port)`; port 0 picks a free one.
            engine: The loaded engine.
            api_key: Bearer key every route except `/health` requires; must not be empty.
            max_body_bytes: Largest accepted body.
            max_waiting: Video calls allowed to wait for the GPU.
            image_engine: Serves `/segment_image` (runtime keyframes); `None` answers 404 there.
            image_max_waiting: Image calls allowed to wait for the GPU.
            embedder: Serves `/embed` (appearance re-identification); `None` answers 404 there.
            person_embedder: Serves `/embed` (person re-identification); `None` answers 404 there.
            crop_engine: Serves `/segment_crops`; `None` answers 404 there.
            geometry_engine: Serves `/geometry`; `None` answers 404 there.

        Raises:
            ValueError: If `api_key` is empty.
        """
        if not api_key:
            msg = f"{API_KEY_ENV} must be set: the GPU is never served without a key"
            raise ValueError(msg)
        super().__init__(address, SamRequestHandler)
        self.engine = engine
        self.api_key = api_key
        self.max_body_bytes = max_body_bytes
        gpu = threading.Lock()
        self.slot = GpuSlot(max_waiting, gpu)
        self.image_engine = image_engine
        self.image_slot = GpuSlot(image_max_waiting, gpu)
        self.embedders = {
            name: model
            for name, model in ((APPEARANCE_MODEL, embedder), (PERSON_MODEL, person_embedder))
            if model is not None
        }
        self.embed_slot = GpuSlot(image_max_waiting, gpu)
        self.crop_engine = crop_engine
        self.crop_slot = GpuSlot(image_max_waiting, gpu)
        self.geometry_engine = geometry_engine
        self.geometry_slot = GpuSlot(image_max_waiting, gpu)

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: ARG002
        """Log a failed connection instead of printing a traceback to stderr.

        A client that keeps sending a refused (oversized) body sees its connection reset; that is
        expected and logged at debug level.
        """
        _log.debug("sam_connection_error", exc_info=True)


class SamRequestHandler(BaseHTTPRequestHandler):
    """Serves `/health`, `/segment` and `/segment_image`."""

    server: SamHTTPServer
    server_version = "rulecam-sam"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Serve `/health` and `/ping`, or 404/401 for anything else."""
        if self.path == PING_PATH:
            self._send(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == HEALTH_PATH:
            slot = self.server.slot
            self._send(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "model_version": MODEL_VERSION,
                    "busy": slot.busy,
                    "waiting": slot.waiting,
                    "memory": self.server.engine.memory_stats(),
                    "image": self._image_health(),
                    "crops": self._crops_health(),
                    "geometry": self._geometry_health(),
                },
            )
            return
        if self._authorized():
            self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: PLR0911 - one early return per route
        """Serve `/segment`, `/segment_image`, `/segment_crops`, `/geometry` and `/embed`."""
        if not self._authorized():
            return
        if self.path == SEGMENT_IMAGE_PATH and self.server.image_engine is not None:
            self._segment_images(self.server.image_engine)
            return
        if self.path == EMBED_PATH and self.server.embedders:
            self._embed()
            return
        if self.path == SEGMENT_CROPS_PATH and self.server.crop_engine is not None:
            self._segment_crops(self.server.crop_engine)
            return
        if self.path == GEOMETRY_PATH and self.server.geometry_engine is not None:
            self._geometry(self.server.geometry_engine)
            return
        if self.path != SEGMENT_PATH:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        body = self._read_body()
        if body is None:
            return
        try:
            video, request = parse_segment_body(body)
        except BadRequestError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        engine = self.server.engine
        self._run(self.server.slot, lambda: engine.segment(video, request), "sam_segment")

    def _segment_images(self, engine: ImageEngine) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            images, request = parse_segment_image_body(body)
        except BadRequestError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._run(
            self.server.image_slot, lambda: engine.segment_images(images, request), "sam_images"
        )

    def _segment_crops(self, engine: CropEngine) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            image, request = parse_image_body(body)
            check_crops_request(request)
        except ValueError as exc:  # BadRequestError, or a malformed crop request
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._run(self.server.crop_slot, lambda: engine.segment_crops(image, request), "sam_crops")

    def _geometry(self, engine: GeometryEngine) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            image, request = parse_image_body(body)
            check_geometry_request(request)
        except ValueError as exc:  # BadRequestError, or a bad grid
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._run(self.server.geometry_slot, lambda: engine.estimate(image, request), "geometry")

    def _embed(self) -> None:

        body = self._read_body()
        if body is None:
            return
        try:
            images, model = parse_embed_body(body)
        except BadRequestError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        embedder = self.server.embedders.get(model)
        if embedder is None:
            self._error(HTTPStatus.NOT_FOUND, f"no {model!r} embedding model on this server")
            return
        self._run(self.server.embed_slot, lambda: embedder.embed(images), f"embed_{model}")

    def _run(self, slot: GpuSlot, work: Callable[[], dict[str, Any]], name: str) -> None:
        if not slot.try_admit():
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "GPU queue is full", retry_after=True)
            return
        started = time.perf_counter()
        try:
            response = slot.run_call(work)
        except Exception as exc:
            _log.exception("%s_failed", name)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"engine failed: {type(exc).__name__}")
            return
        finally:
            slot.release()
        _log.info(
            "%s objects=%d ms=%d",
            name,
            len(response.get("objects", response.get("embeddings", ()))),
            round((time.perf_counter() - started) * 1000),
        )
        self._send(HTTPStatus.OK, response)

    def _image_health(self) -> dict[str, Any]:
        engine = self.server.image_engine
        if engine is None:
            return {"loaded": False}
        return {"loaded": True, "waiting": self.server.image_slot.waiting, **engine.memory_stats()}

    def _geometry_health(self) -> dict[str, Any]:
        engine = self.server.geometry_engine
        if engine is None:
            return {"loaded": False}
        return {"loaded": True, **engine.memory_stats()}

    def _crops_health(self) -> dict[str, Any]:
        engine = self.server.crop_engine
        if engine is None:
            return {"loaded": False}
        return {"loaded": True, "waiting": self.server.crop_slot.waiting, **engine.memory_stats()}

    def log_message(self, format: str, *args: Any) -> None:
        """Route the stdlib access log to `logging` (never prints headers)."""
        _log.debug("%s - %s", self.address_string(), format % args)

    def _authorized(self) -> bool:
        key = self.headers.get(KEY_HEADER)
        if key is not None:
            matched = hmac.compare_digest(key.encode(), self.server.api_key.encode())
        else:
            matched = bearer_matches(self.headers.get("Authorization"), self.server.api_key)
        if matched:
            return True
        self._drain()
        self._error(HTTPStatus.UNAUTHORIZED, "missing or wrong API key")
        return False

    def _read_body(self) -> bytes | None:
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            self._error(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
            return None
        if int(length) > self.server.max_body_bytes:
            self.close_connection = True
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
            return None
        return self.rfile.read(int(length))

    def _drain(self) -> None:
        length = self.headers.get("Content-Length")
        if length is not None and length.isdigit() and int(length) <= self.server.max_body_bytes:
            self.rfile.read(int(length))
        else:
            self.close_connection = True

    def _error(self, status: HTTPStatus, message: str, *, retry_after: bool = False) -> None:
        self._send(status, {"error": {"message": message}}, retry_after=retry_after)

    def _send(self, status: HTTPStatus, payload: object, *, retry_after: bool = False) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if retry_after:
            self.send_header("Retry-After", "1")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)


class LoadingHandler(BaseHTTPRequestHandler):
    """Holds the port while SAM 3.1 loads: `/ping` 204 ("initializing"), anything else 503.

    A RunPod load balancer counts any other answer, or none, as an unhealthy worker.
    """

    server_version = "rulecam-sam"
    sys_version = ""

    def do_GET(self) -> None:
        """Answer the health probe; refuse everything else until the model is loaded."""
        loading = self.path == PING_PATH
        self.send_response(HTTPStatus.NO_CONTENT if loading else HTTPStatus.SERVICE_UNAVAILABLE)
        if not loading:
            self.send_header("Retry-After", "5")
            self.send_header("Content-Length", "0")
        self.close_connection = True
        self.send_header("Connection", "close")
        self.end_headers()

    def do_POST(self) -> None:
        """Refuse work until the model is loaded (the body is never read)."""
        self.path = ""
        self.do_GET()

    def log_message(self, format: str, *args: Any) -> None:
        """Route the stdlib access log to `logging`."""
        _log.debug("%s - %s", self.address_string(), format % args)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - loads SAM 3.1 on a GPU
    """Load SAM 3.1 once, then serve until killed.

    Args:
        argv: Command-line arguments (defaults to `sys.argv[1:]`).
    """
    from rulecam.perception.appearance_worker import DinoEmbedder  # noqa: PLC0415
    from rulecam.perception.crop_worker import CropEngine as SamCropEngine  # noqa: PLC0415
    from rulecam.perception.reid_worker import load_reid  # noqa: PLC0415
    from rulecam.perception.sam_worker import Sam31Engine, Sam31ImageEngine  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Serve self-hosted SAM 3.1 over HTTP.")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - the pod's proxy needs it
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api_key = os.environ.get(API_KEY_ENV, "")
    if not api_key:
        msg = f"{API_KEY_ENV} must be set: the GPU is never served without a key"
        raise SystemExit(msg)
    loading = ThreadingHTTPServer((args.host, args.port), LoadingHandler)
    threading.Thread(target=loading.serve_forever, daemon=True).start()
    _log.info("sam_loading model=%s", MODEL_VERSION)
    engine = Sam31Engine.load()
    image_on = os.environ.get(IMAGE_ENV, "1").strip().lower() not in {"0", "false", "no"}
    reid = load_reid(os.environ)
    image_engine = Sam31ImageEngine.from_video_engine(engine, reid=reid) if image_on else None
    crop_engine = SamCropEngine.from_video_engine(engine) if image_on else None
    geometry_engine = MogeEngine() if MogeEngine.available() else None
    embedder = DinoEmbedder()
    loading.shutdown()
    loading.server_close()
    server = SamHTTPServer(
        (args.host, args.port),
        engine,
        api_key=api_key,
        max_body_bytes=int(os.environ.get(MAX_BODY_ENV, DEFAULT_MAX_BODY_BYTES)),
        max_waiting=int(os.environ.get(MAX_WAITING_ENV, DEFAULT_MAX_WAITING)),
        image_engine=image_engine,
        image_max_waiting=int(os.environ.get(IMAGE_MAX_WAITING_ENV, DEFAULT_IMAGE_MAX_WAITING)),
        embedder=embedder,
        person_embedder=reid,
        crop_engine=crop_engine,
        geometry_engine=geometry_engine,
    )
    _log.info("sam_serving port=%d reid=%s", args.port, reid.model_name if reid else None)
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
