"""Front for llama-server (Muse Glimmer) on a RunPod serverless load balancer.

RunPod's load balancer polls `GET /ping` (204 while the worker loads, 200 when ready, anything
else unhealthy) and its edge takes `Authorization` for the RunPod API key. llama-server has no
`/ping` and reads its key only from `Authorization`, so this small proxy sits in front of it on
the worker:

- `GET /ping`: open. 200 once llama-server's `/health` answers 200, 204 while it loads (or has
  not opened its port yet), 503 when it stops answering after it was ready, so RunPod replaces
  the worker;
- every other route needs the Glimmer key, as `X-Rulecam-Key: <key>` (behind the edge, which
  keeps `Authorization`) or `Authorization: Bearer <key>`, compared in constant time
  (`KEY_HEADER` wins when both are sent). The request (method, path, body, `Content-Type` and
  `Accept`) then goes to llama-server, which listens on localhost only and runs without
  `--api-key`, and its answer comes back unchanged. Errors of the proxy itself are
  `{"error": {"message": ...}}` with 401, 411, 413 or 502 (llama-server unreachable).

Standard library only: it runs with the standalone Python on the worker's network volume, next
to the stock llama.cpp image (`scripts/glimmer_pod/serverless.sh`). Run it with the key in the
environment, never in argv:

    GLIMMER_API_KEY=... python -m rulecam.clients.open_weights.llama_edge \
        --port 8080 --upstream http://127.0.0.1:8081
"""

from __future__ import annotations

import argparse
import hmac
import http.client
import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

PING_PATH = "/ping"
HEALTH_PATH = "/health"
KEY_HEADER = "X-Rulecam-Key"  # the key behind RunPod's edge, which keeps `Authorization`
API_KEY_ENV = "GLIMMER_API_KEY"
FORWARDED_HEADERS = ("Content-Type", "Accept")
UPSTREAM_TIMEOUT_S = 330.0  # RunPod's load balancer gives up on a request after 5.5 minutes
HEALTH_TIMEOUT_S = 5.0
MAX_BODY_BYTES = 32 * 1024 * 1024  # RunPod's limit is 30 MB each way

_log = logging.getLogger("rulecam.llama_edge")


class EdgeProxy(ThreadingHTTPServer):
    """A threading HTTP server that forwards authorized requests to llama-server."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        upstream: str,
        api_key: str,
        timeout_s: float = UPSTREAM_TIMEOUT_S,
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        """Bind the proxy.

        Args:
            address: `(host, port)`; port 0 picks a free one.
            upstream: llama-server's root, e.g. `http://127.0.0.1:8081`.
            api_key: The Glimmer key every route except `/ping` requires; must not be empty.
            timeout_s: Timeout of one forwarded request.
            max_body_bytes: Largest accepted body.

        Raises:
            ValueError: If `api_key` is empty.
        """
        if not api_key:
            msg = f"{API_KEY_ENV} must be set: the GPU is never served without a key"
            raise ValueError(msg)
        super().__init__(address, EdgeHandler)
        parts = urlsplit(upstream)
        self.upstream_host = parts.hostname or "127.0.0.1"
        self.upstream_port = parts.port or 80
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_body_bytes = max_body_bytes
        self.was_ready = False

    def forward(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
        *,
        timeout_s: float | None = None,
    ) -> tuple[int, str | None, bytes]:
        """Send one request to llama-server.

        Args:
            method: HTTP method.
            path: Path and query.
            body: Request body, if any.
            headers: Headers to send.
            timeout_s: Timeout; defaults to the proxy's.

        Returns:
            `(status, content type, body)` of llama-server's answer.

        Raises:
            OSError: If llama-server did not answer.
        """
        conn = http.client.HTTPConnection(
            self.upstream_host, self.upstream_port, timeout=timeout_s or self.timeout_s
        )
        try:
            conn.request(method, path, body=body, headers=headers)
            reply = conn.getresponse()
            return reply.status, reply.getheader("Content-Type"), reply.read()
        finally:
            conn.close()

    def readiness(self) -> HTTPStatus:
        """Map llama-server's `/health` to the load balancer's `/ping` answer."""
        try:
            status, _, _ = self.forward("GET", HEALTH_PATH, None, {}, timeout_s=HEALTH_TIMEOUT_S)
        except OSError:
            status = 0
        if status == HTTPStatus.OK:
            self.was_ready = True
            return HTTPStatus.OK
        return HTTPStatus.SERVICE_UNAVAILABLE if self.was_ready else HTTPStatus.NO_CONTENT


def key_matches(headers: Any, api_key: str) -> bool:
    """Check `X-Rulecam-Key`, or else a `Bearer` `Authorization`, against the key.

    Args:
        headers: The request's headers (`email.message.Message`-like).
        api_key: The expected key.

    Returns:
        True when the key matches, compared in constant time.
    """
    key = headers.get(KEY_HEADER)
    if key is None:
        auth = headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            return False
        key = auth[len("Bearer ") :]
    return hmac.compare_digest(key.encode(), api_key.encode())


class EdgeHandler(BaseHTTPRequestHandler):
    """Serves `/ping` and forwards everything else."""

    server: EdgeProxy
    server_version = "rulecam-llama-edge"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Answer `/ping`, or forward."""
        if self.path == PING_PATH:
            status = self.server.readiness()
            if status == HTTPStatus.NO_CONTENT:
                self.send_response(status)
                self.end_headers()
                return
            self._send(status, "application/json", json.dumps({"status": status.phrase}).encode())
            return
        self._forward(None)

    def do_POST(self) -> None:
        """Forward a request with a body."""
        if not self._authorized():
            return
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit():
            self._error(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
            return
        if int(length) > self.server.max_body_bytes:
            self.close_connection = True
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
            return
        self._forward(self.rfile.read(int(length)), authorized=True)

    def log_message(self, format: str, *args: Any) -> None:
        """Route the stdlib access log to `logging` (never prints headers)."""
        _log.debug("%s - %s", self.address_string(), format % args)

    def _forward(self, body: bytes | None, *, authorized: bool = False) -> None:
        if not authorized and not self._authorized():
            return
        headers = {name: value for name in FORWARDED_HEADERS if (value := self.headers.get(name))}
        try:
            status, content_type, data = self.server.forward(self.command, self.path, body, headers)
        except OSError as exc:
            _log.warning("llama_edge_upstream_failed error=%s", type(exc).__name__)
            self._error(HTTPStatus.BAD_GATEWAY, "llama-server is not answering")
            return
        self._send(status, content_type, data)

    def _authorized(self) -> bool:
        if key_matches(self.headers, self.server.api_key):
            return True
        length = self.headers.get("Content-Length")
        if length is not None and length.isdigit() and int(length) <= self.server.max_body_bytes:
            self.rfile.read(int(length))
        else:
            self.close_connection = True
        self._error(HTTPStatus.UNAUTHORIZED, "missing or wrong API key")
        return False

    def _error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"error": {"message": message}}).encode()
        self._send(status, "application/json", body)

    def _send(self, status: int, content_type: str | None, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - runs on the GPU worker
    """Serve until killed.

    Args:
        argv: Command-line arguments (defaults to `sys.argv[1:]`).
    """
    parser = argparse.ArgumentParser(description="Front llama-server on a RunPod load balancer.")
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - the load balancer needs it
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--upstream", default="http://127.0.0.1:8081")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api_key = os.environ.get(API_KEY_ENV, "")
    if not api_key:
        msg = f"{API_KEY_ENV} must be set: the GPU is never served without a key"
        raise SystemExit(msg)
    server = EdgeProxy((args.host, args.port), upstream=args.upstream, api_key=api_key)
    _log.info("llama_edge_serving port=%d upstream=%s", args.port, args.upstream)
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
