"""The llama-server front for RunPod's load balancer (`llama_edge`) and the client's edge key."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from rulecam.clients.open_weights.glimmer import glimmer_headers, glimmer_transport
from rulecam.clients.open_weights.llama_edge import EdgeProxy, key_matches
from rulecam.config import Settings

KEY = "glimmer-key"
EDGE = "runpod-edge-key"


class FakeLlama(BaseHTTPRequestHandler):
    """llama-server stand-in: `/health` from `server.health`, other routes echo the request."""

    server: Upstream
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(self.server.health, {"status": "ok"})
            return
        self._send(200, {"path": self.path, "auth": self.headers.get("Authorization")})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append(dict(self.headers))
        status = 400 if body.get("bad") else 200
        self._send(status, {"path": self.path, "echo": body})

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, payload: object) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Upstream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FakeLlama)
        self.health = 503
        self.seen: list[dict[str, str]] = []


def _serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _stop(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


class Stack:
    def __init__(self) -> None:
        self.upstream = Upstream()
        self._up = _serve(self.upstream)
        upstream_url = f"http://127.0.0.1:{self.upstream.server_address[1]}"
        self.proxy = EdgeProxy(("127.0.0.1", 0), upstream=upstream_url, api_key=KEY)
        self._proxy = _serve(self.proxy)
        self.url = f"http://127.0.0.1:{self.proxy.server_address[1]}"
        self.upstream_up = True

    def stop_upstream(self) -> None:
        if self.upstream_up:
            _stop(self.upstream, self._up)
            self.upstream_up = False

    def close(self) -> None:
        _stop(self.proxy, self._proxy)
        self.stop_upstream()


@pytest.fixture
def stack() -> Iterator[Stack]:
    running = Stack()
    yield running
    running.close()


def test_ping_follows_llama_server_health(stack: Stack) -> None:
    assert httpx.get(f"{stack.url}/ping").status_code == 204  # loading
    stack.upstream.health = 200
    assert httpx.get(f"{stack.url}/ping").status_code == 200
    stack.upstream.health = 503
    assert httpx.get(f"{stack.url}/ping").status_code == 503  # was ready: now unhealthy


def test_ping_is_initializing_until_llama_server_opens_its_port() -> None:
    proxy = EdgeProxy(("127.0.0.1", 0), upstream="http://127.0.0.1:9", api_key=KEY)
    thread = _serve(proxy)
    try:
        assert httpx.get(f"http://127.0.0.1:{proxy.server_address[1]}/ping").status_code == 204
    finally:
        _stop(proxy, thread)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": f"Bearer {EDGE}"},
        {"Authorization": f"Bearer {KEY}", "X-Rulecam-Key": "x"},  # the key header wins
    ],
)
def test_routes_need_the_key(stack: Stack, headers: dict[str, str]) -> None:
    reply = httpx.post(f"{stack.url}/v1/chat/completions", json={"a": 1}, headers=headers)
    assert reply.status_code == 401
    assert reply.json()["error"]["message"] == "missing or wrong API key"
    assert httpx.get(f"{stack.url}/v1/models", headers=headers).status_code == 401
    assert stack.upstream.seen == []


def test_forwards_the_request_and_relays_the_answer(stack: Stack) -> None:
    edge = {"Authorization": f"Bearer {EDGE}", "X-Rulecam-Key": KEY}
    reply = httpx.post(f"{stack.url}/v1/chat/completions", json={"q": "hi"}, headers=edge)
    assert reply.status_code == 200
    assert reply.json() == {"path": "/v1/chat/completions", "echo": {"q": "hi"}}
    sent = {k.lower(): v for k, v in stack.upstream.seen[0].items()}
    assert "authorization" not in sent  # llama-server never sees either key
    assert "x-rulecam-key" not in sent
    assert sent["content-type"] == "application/json"
    bad = httpx.post(f"{stack.url}/v1/chat/completions", json={"bad": True}, headers=edge)
    assert bad.status_code == 400  # llama-server's own errors pass through
    direct = httpx.get(f"{stack.url}/v1/models", headers={"Authorization": f"Bearer {KEY}"})
    assert direct.json() == {"path": "/v1/models", "auth": None}


def test_llama_server_down_is_502(stack: Stack) -> None:
    stack.stop_upstream()
    reply = httpx.post(f"{stack.url}/v1/chat/completions", json={}, headers={"X-Rulecam-Key": KEY})
    assert reply.status_code == 502


def test_body_limits(stack: Stack) -> None:
    stack.proxy.max_body_bytes = 8
    headers = {"X-Rulecam-Key": KEY}
    big = httpx.post(f"{stack.url}/v1/x", content=b"0123456789", headers=headers)
    assert big.status_code == 413
    request = httpx.Request("POST", f"{stack.url}/v1/x", headers=headers)
    request.headers.pop("Content-Length", None)
    with httpx.Client() as client:
        assert client.send(request).status_code == 411


def test_key_matching() -> None:
    assert key_matches({"X-Rulecam-Key": KEY}, KEY)
    assert key_matches({"Authorization": f"Bearer {KEY}"}, KEY)
    assert not key_matches({"Authorization": f"Basic {KEY}"}, KEY)
    assert not key_matches({"X-Rulecam-Key": KEY + "x", "Authorization": f"Bearer {KEY}"}, KEY)
    with pytest.raises(ValueError, match="GLIMMER_API_KEY"):
        EdgeProxy(("127.0.0.1", 0), upstream="http://127.0.0.1:9", api_key="")


def test_proxy_imports_only_the_standard_library() -> None:
    code = (
        "import sys, rulecam.clients.open_weights.llama_edge;"
        "bad = {'pydantic', 'httpx', 'structlog', 'numpy'} & set(sys.modules);"
        "sys.exit(len(bad))"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0  # noqa: S603 - fixed code


def test_client_headers_direct_and_behind_the_edge() -> None:
    assert glimmer_headers(Settings(_env_file=None)) == {}
    direct = Settings(_env_file=None, glimmer_api_key=SecretStr(KEY))
    assert glimmer_headers(direct) == {"Authorization": f"Bearer {KEY}"}
    edge = Settings(
        _env_file=None, glimmer_api_key=SecretStr(KEY), glimmer_edge_key=SecretStr(EDGE)
    )
    assert glimmer_headers(edge) == {"Authorization": f"Bearer {EDGE}", "X-Rulecam-Key": KEY}


def test_client_transport_through_the_proxy(stack: Stack) -> None:
    edge = Settings(
        _env_file=None, glimmer_api_key=SecretStr(KEY), glimmer_edge_key=SecretStr(EDGE)
    )
    reply = glimmer_transport(stack.url, edge)({"path": "/v1/chat/completions", "json": {"q": 1}})
    assert reply["status"] == 200
    assert reply["json"] == {"path": "/v1/chat/completions", "echo": {"q": 1}}
