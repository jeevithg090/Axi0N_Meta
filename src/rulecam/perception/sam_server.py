"""HTTP front for `Sam31Engine` on our own GPU pod (task P1-03, self-hosted on RunPod).

The API pod reaches it through `sam_runtime.HttpSamWorker`. Routes:

- `GET /health`: open, `{"status": "ok", "model_version": ..., "busy": bool, "waiting": int}`;
- `POST /segment`: needs `Authorization: Bearer <SAM_API_KEY>`. The body is JSON
  `{"request": <SamRequest JSON>, "video_b64": "<base64 MP4 bytes>"}`, the same two arguments
  `ModalWorker` passes to the Modal method; the answer is the `SamResponse` JSON.

Errors are `{"error": {"message": ...}}` with 400 (malformed body), 401 (missing or wrong key),
404, 411 (no `Content-Length`), 413 (body over the limit), 500 (the engine failed) or 503 (the
queue is full; `Retry-After: 1`). One GPU serves one request at a time: other requests wait, up
to `max_waiting` of them, and the rest are refused with 503 at once instead of piling up lag.

Like `sam_worker`, this module runs inside the GPU venv (numpy < 2, `sam3`, torch), so it imports
only the standard library and `sam_worker`, and logs with `logging`, not structlog. The model is
loaded once, before the port opens. Run it with the key in the environment, never in argv:

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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from rulecam.perception.sam_worker import MODEL_VERSION

DEFAULT_PORT = 8090
DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024  # a 3 s chunk is well under 1 MB; base64 adds a third
DEFAULT_MAX_WAITING = 2
API_KEY_ENV = "SAM_API_KEY"
MAX_BODY_ENV = "SAM_MAX_BODY_BYTES"
MAX_WAITING_ENV = "SAM_MAX_WAITING"
HEALTH_PATH = "/health"
SEGMENT_PATH = "/segment"

_log = logging.getLogger("rulecam.sam_server")


class Engine(Protocol):
    """What the server needs from `Sam31Engine` (a fake in tests)."""

    def segment(self, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
        """Return a `SamResponse` dict for a `SamRequest` dict and the chunk's MP4 bytes."""
        ...


class BadRequestError(ValueError):
    """The request body is not a valid `/segment` payload."""


class GpuSlot:
    """Lets one call use the GPU at a time and at most `max_waiting` calls wait for it."""

    def __init__(self, max_waiting: int) -> None:
        """Create a free slot.

        Args:
            max_waiting: Calls allowed to wait while one runs (0 refuses any overlap).
        """
        self._max_waiting = max_waiting
        self._gpu = threading.Lock()
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
        with self._gpu:
            return engine.segment(video, request)

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
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = "body is not JSON"
        raise BadRequestError(msg) from exc
    if not isinstance(payload, dict):
        msg = "body must be a JSON object"
        raise BadRequestError(msg)
    request, video_b64 = payload.get("request"), payload.get("video_b64")
    if not isinstance(request, dict) or not isinstance(video_b64, str):
        msg = "body needs a `request` object and a `video_b64` string"
        raise BadRequestError(msg)
    try:
        video = base64.b64decode(video_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        msg = "`video_b64` is not valid base64"
        raise BadRequestError(msg) from exc
    return video, request


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
    ) -> None:
        """Bind the server.

        Args:
            address: `(host, port)`; port 0 picks a free one.
            engine: The loaded engine.
            api_key: Bearer key every route except `/health` requires; must not be empty.
            max_body_bytes: Largest accepted body.
            max_waiting: Calls allowed to wait for the GPU.

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
        self.slot = GpuSlot(max_waiting)

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: ARG002
        """Log a failed connection instead of printing a traceback to stderr.

        A client that keeps sending a refused (oversized) body sees its connection reset; that is
        expected and logged at debug level.
        """
        _log.debug("sam_connection_error", exc_info=True)


class SamRequestHandler(BaseHTTPRequestHandler):
    """Serves `/health` and `/segment`."""

    server: SamHTTPServer
    server_version = "rulecam-sam"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Serve `/health`, or 404/401 for anything else."""
        if self.path == HEALTH_PATH:
            slot = self.server.slot
            self._send(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "model_version": MODEL_VERSION,
                    "busy": slot.busy,
                    "waiting": slot.waiting,
                },
            )
            return
        if self._authorized():
            self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        """Serve `/segment`."""
        if not self._authorized():
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
        slot = self.server.slot
        if not slot.try_admit():
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "GPU queue is full", retry_after=True)
            return
        started = time.perf_counter()
        try:
            response = slot.run(self.server.engine, video, request)
        except Exception as exc:
            _log.exception("sam_segment_failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"engine failed: {type(exc).__name__}")
            return
        finally:
            slot.release()
        _log.info(
            "sam_segment objects=%d ms=%d",
            len(response.get("objects", ())),
            round((time.perf_counter() - started) * 1000),
        )
        self._send(HTTPStatus.OK, response)

    def log_message(self, format: str, *args: Any) -> None:
        """Route the stdlib access log to `logging` (never prints headers)."""
        _log.debug("%s - %s", self.address_string(), format % args)

    def _authorized(self) -> bool:
        if bearer_matches(self.headers.get("Authorization"), self.server.api_key):
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


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - loads SAM 3.1 on a GPU
    """Load SAM 3.1 once, then serve until killed.

    Args:
        argv: Command-line arguments (defaults to `sys.argv[1:]`).
    """
    from rulecam.perception.sam_worker import Sam31Engine  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Serve self-hosted SAM 3.1 over HTTP.")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - the pod's proxy needs it
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api_key = os.environ.get(API_KEY_ENV, "")
    if not api_key:
        msg = f"{API_KEY_ENV} must be set: the GPU is never served without a key"
        raise SystemExit(msg)
    _log.info("sam_loading model=%s", MODEL_VERSION)
    engine = Sam31Engine.load()
    server = SamHTTPServer(
        (args.host, args.port),
        engine,
        api_key=api_key,
        max_body_bytes=int(os.environ.get(MAX_BODY_ENV, DEFAULT_MAX_BODY_BYTES)),
        max_waiting=int(os.environ.get(MAX_WAITING_ENV, DEFAULT_MAX_WAITING)),
    )
    _log.info("sam_serving port=%d", args.port)
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
