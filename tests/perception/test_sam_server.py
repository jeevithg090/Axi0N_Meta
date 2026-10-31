"""The HTTP SAM server on our GPU pod and its client `HttpSamWorker` (P1-03; no GPU, loopback)."""

from __future__ import annotations

import base64
import http.client
import json
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from rulecam.clients.transport import HttpxTransport, ModelTransportError
from rulecam.config import Settings
from rulecam.contracts import Segmenter
from rulecam.perception.errors import SamResponseError, SamRuntimeUnavailableError
from rulecam.perception.metrics import PerceptionMetrics
from rulecam.perception.router import Purpose, SegmenterRouter
from rulecam.perception.sam_runtime import HttpSamWorker, ModalSam31, SamWorker
from rulecam.perception.sam_server import (
    GpuSlot,
    SamHTTPServer,
    bearer_matches,
    parse_segment_body,
)
from rulecam.perception.sam_worker import MODEL_VERSION
from tests.fakes import FakeClock, FakeSegmenter, make_chunk

KEY = "test-sam-key"
VIDEO = b"\x00\x01fake mp4 bytes\xff"
BOX_RLE = '{"size":[4,6],"counts":"9220003"}'


def _response(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "objects": [
            {
                "frame_idx": 0,
                "object_id": 1,
                "rle": BOX_RLE,
                "box_xyxy": [2.0, 1.0, 5.0, 3.0],
                "confidence": 0.9 if request.get("include_confidence") else None,
            }
        ],
        "frames_processed": 30,
        "gpu_seconds": 0.4,
        "model_version": "fake",
    }


class FakeEngine:
    """Records calls; answers with `respond`, which may block or raise."""

    def __init__(self, respond: Callable[[dict[str, Any]], dict[str, Any]] = _response) -> None:
        self.respond = respond
        self.calls: list[tuple[bytes, dict[str, Any]]] = []

    def segment(self, video: bytes, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((video, request))
        return self.respond(request)


class Running:
    """A `SamHTTPServer` on a loopback port, served from a background thread."""

    def __init__(self, engine: FakeEngine, **kwargs: Any) -> None:
        self.engine = engine
        self.server = SamHTTPServer(("127.0.0.1", 0), engine, api_key=KEY, **kwargs)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def running() -> Iterator[Running]:
    server = Running(FakeEngine())
    yield server
    server.close()


def _body(request: dict[str, Any] | None = None, video: bytes = VIDEO) -> dict[str, Any]:
    return {
        "request": request or {"text": "person", "include_confidence": True},
        "video_b64": base64.b64encode(video).decode(),
    }


def _auth(key: str = KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# --- server -----------------------------------------------------------------------------------


def test_health_is_open(running: Running) -> None:
    reply = httpx.get(f"{running.url}/health")
    assert reply.status_code == 200
    assert reply.json() == {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "busy": False,
        "waiting": 0,
    }


@pytest.mark.parametrize("headers", [{}, _auth("wrong"), {"Authorization": KEY}])
def test_segment_requires_the_key(running: Running, headers: dict[str, str]) -> None:
    reply = httpx.post(f"{running.url}/segment", json=_body(), headers=headers)
    assert reply.status_code == 401
    assert reply.json()["error"]["message"] == "missing or wrong API key"
    assert running.engine.calls == []


def test_segment_round_trip(running: Running) -> None:
    request = {"text": "forklift", "include_confidence": True, "width": 6}
    reply = httpx.post(f"{running.url}/segment", json=_body(request), headers=_auth())
    assert reply.status_code == 200
    assert reply.json() == _response(request)
    assert running.engine.calls == [(VIDEO, request)]


def test_unknown_paths(running: Running) -> None:
    assert httpx.get(f"{running.url}/metrics").status_code == 401
    assert httpx.get(f"{running.url}/metrics", headers=_auth()).status_code == 404
    reply = httpx.post(f"{running.url}/other", json=_body(), headers=_auth())
    assert reply.status_code == 404


def test_body_size_limit() -> None:
    server = Running(FakeEngine(), max_body_bytes=64)
    try:
        reply = httpx.post(f"{server.url}/segment", json=_body(), headers=_auth())
        assert reply.status_code == 413
        assert server.engine.calls == []
        unauthorized = httpx.post(f"{server.url}/segment", json=_body())
        assert unauthorized.status_code == 401  # the key is checked first
    finally:
        server.close()


def test_content_length_is_required(running: Running) -> None:
    host, port = running.server.server_address[:2]
    conn = http.client.HTTPConnection(str(host), int(port), timeout=5)
    try:
        conn.putrequest("POST", "/segment")
        conn.putheader("Authorization", f"Bearer {KEY}")
        conn.endheaders()
        assert conn.getresponse().status == 411
    finally:
        conn.close()


@pytest.mark.parametrize(
    "content",
    [b"not json", b"[1, 2]", b'{"request": {}}', b'{"request": {}, "video_b64": "%%%"}'],
)
def test_malformed_bodies_are_400(running: Running, content: bytes) -> None:
    headers = {**_auth(), "Content-Type": "application/json"}
    reply = httpx.post(f"{running.url}/segment", content=content, headers=headers)
    assert reply.status_code == 400
    assert running.engine.calls == []


def test_engine_failure_is_500_and_the_server_lives_on() -> None:
    def explode(request: dict[str, Any]) -> dict[str, Any]:
        if request.get("text") == "boom":
            raise RuntimeError("CUDA out of memory")
        return _response(request)

    server = Running(FakeEngine(explode))
    try:
        failed = httpx.post(f"{server.url}/segment", json=_body({"text": "boom"}), headers=_auth())
        assert failed.status_code == 500
        assert failed.json()["error"]["message"] == "engine failed: RuntimeError"
        assert httpx.post(f"{server.url}/segment", json=_body(), headers=_auth()).is_success
        assert not server.server.slot.busy
    finally:
        server.close()


def test_full_queue_is_refused_with_503() -> None:
    started, release = threading.Event(), threading.Event()

    def block(request: dict[str, Any]) -> dict[str, Any]:
        started.set()
        release.wait(timeout=5)
        return _response(request)

    server = Running(FakeEngine(block), max_waiting=0)
    first: list[int] = []
    worker = threading.Thread(
        target=lambda: first.append(
            httpx.post(f"{server.url}/segment", json=_body(), headers=_auth()).status_code
        )
    )
    try:
        worker.start()
        assert started.wait(timeout=5)
        assert httpx.get(f"{server.url}/health").json()["busy"] is True
        busy = httpx.post(f"{server.url}/segment", json=_body(), headers=_auth())
        assert busy.status_code == 503
        assert busy.headers["retry-after"] == "1"
    finally:
        release.set()
        worker.join(timeout=5)
        server.close()
    assert first == [200]


def test_gpu_slot_admits_one_running_plus_waiting() -> None:
    slot = GpuSlot(max_waiting=1)
    assert slot.try_admit()
    assert slot.try_admit()
    assert slot.waiting == 1
    assert not slot.try_admit()
    slot.release()
    slot.release()
    assert slot.waiting == 0
    assert slot.run(FakeEngine(), VIDEO, {"text": "x"})["frames_processed"] == 30


def test_server_refuses_to_start_without_a_key() -> None:
    with pytest.raises(ValueError, match="SAM_API_KEY"):
        SamHTTPServer(("127.0.0.1", 0), FakeEngine(), api_key="")


def test_bearer_matching() -> None:
    assert bearer_matches(f"Bearer {KEY}", KEY)
    assert not bearer_matches(None, KEY)
    assert not bearer_matches(f"Basic {KEY}", KEY)
    assert not bearer_matches(f"Bearer {KEY}x", KEY)


def test_parse_segment_body() -> None:
    video, request = parse_segment_body(json.dumps(_body({"text": "a"})).encode())
    assert (video, request) == (VIDEO, {"text": "a"})


def test_server_module_imports_no_structlog_or_httpx() -> None:
    code = (
        "import sys, rulecam.perception.sam_server;"
        "bad = {'structlog', 'httpx', 'fastapi', 'torch', 'sam3'} & set(sys.modules);"
        "sys.exit(len(bad))"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0  # noqa: S603 - fixed code


# --- HttpSamWorker ----------------------------------------------------------------------------


def _chunk_with_file(tmp_path: Any) -> Any:
    path = tmp_path / "chunk.mp4"
    path.write_bytes(VIDEO)
    return make_chunk().model_copy(update={"path": str(path)})


def test_http_worker_round_trip_through_the_server(running: Running, tmp_path: Any) -> None:
    worker: SamWorker = HttpSamWorker(HttpxTransport(running.url, KEY))
    metrics = PerceptionMetrics()
    segmenter: Segmenter = ModalSam31(worker, metrics=metrics)
    detections = segmenter.segment(_chunk_with_file(tmp_path), "person")
    assert [(d.frame_idx, d.object_id, d.concept) for d in detections] == [(0, 1, "person")]
    video, request = running.engine.calls[0]
    assert video == VIDEO
    assert request["text"] == "person"
    assert metrics.report().total.sam_calls_self_hosted == 1


def test_http_worker_wrong_key_is_unavailable(running: Running) -> None:
    worker = HttpSamWorker(HttpxTransport(running.url, "wrong"))
    with pytest.raises(SamRuntimeUnavailableError, match="HTTP 401"):
        worker.segment(VIDEO, {"text": "person"})


class ScriptedTransport:
    """Returns (or raises) the scripted replies in order and records requests."""

    def __init__(self, *replies: Mapping[str, object] | Exception) -> None:
        self._replies = list(replies)
        self.requests: list[Mapping[str, object]] = []

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.requests.append(request)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_http_worker_retries_a_busy_gpu_once() -> None:
    ok = {"status": 200, "headers": {}, "json": {"objects": []}}
    busy = {"status": 503, "headers": {"retry-after": "1"}, "json": None}
    transport = ScriptedTransport(busy, ok)
    sleeps: list[float] = []
    worker = HttpSamWorker(transport, sleep=sleeps.append)
    assert worker.segment(VIDEO, {"text": "person"}) == {"objects": []}
    assert sleeps == [1.0]
    sent = transport.requests[0]
    assert sent["path"] == "/segment"
    payload = sent["json"]
    assert isinstance(payload, dict)
    assert base64.b64decode(payload["video_b64"]) == VIDEO


@pytest.mark.parametrize(
    "replies",
    [
        (ModelTransportError("connect failed"), ModelTransportError("connect failed")),
        ({"status": 503, "json": None}, {"status": 503, "json": None}),
        ({"status": 500, "json": {"error": {"message": "engine failed"}}},) * 2,
        ({"status": 400, "json": {"error": {"message": "bad"}}},),
    ],
)
def test_http_worker_failures_are_unavailable(replies: tuple[Any, ...]) -> None:
    worker = HttpSamWorker(ScriptedTransport(*replies), sleep=lambda s: None)
    with pytest.raises(SamRuntimeUnavailableError):
        worker.segment(VIDEO, {"text": "person"})


def test_http_worker_non_object_body_is_a_response_error() -> None:
    worker = HttpSamWorker(ScriptedTransport({"status": 200, "json": ["nope"]}))
    with pytest.raises(SamResponseError):
        worker.segment(VIDEO, {"text": "person"})


def test_http_worker_from_settings_needs_url_and_key() -> None:
    with pytest.raises(SamRuntimeUnavailableError, match="SAM_BASE_URL"):
        HttpSamWorker.from_settings(Settings(_env_file=None, sam_base_url="http://x"))
    built = HttpSamWorker.from_settings(
        Settings(_env_file=None, sam_base_url="http://127.0.0.1:1", sam_api_key=SecretStr(KEY))
    )
    assert isinstance(built, HttpSamWorker)


def test_router_falls_back_to_hosted_when_the_http_worker_fails(tmp_path: Any) -> None:
    transport = ScriptedTransport(ModelTransportError("refused"), ModelTransportError("refused"))
    metrics = PerceptionMetrics()
    hosted, alerts = FakeSegmenter(), []
    router = SegmenterRouter(
        hosted,
        ModalSam31(HttpSamWorker(transport, sleep=lambda s: None), metrics=metrics),
        clock=FakeClock(),
        metrics=metrics,
        on_fallback=lambda camera_id, error: alerts.append(camera_id),
    )
    chunk = _chunk_with_file(tmp_path)
    router.for_purpose(Purpose.RUNTIME).segment(chunk, "person")
    router.for_purpose(Purpose.RUNTIME).segment(chunk, "person")  # inside the retry window
    assert len(hosted.calls) == 2
    assert len(transport.requests) == 2  # one failed call, retried once; then hosted directly
    assert alerts == ["cam1"]
    report = metrics.report().total
    assert (report.sam_calls_hosted, report.sam_calls_self_hosted) == (2, 0)


def test_pod_setup_script_uses_the_pinned_commit_and_torch() -> None:
    from pathlib import Path  # noqa: PLC0415

    from rulecam.perception import sam_worker  # noqa: PLC0415

    root = Path(__file__).resolve().parents[2]
    source = (root / "src/rulecam/perception/sam_worker.py").read_text()
    script = (root / "scripts/sam_pod/setup.sh").read_text()
    assert f'SAM3_COMMIT = "{sam_worker.SAM3_COMMIT}"' in source.splitlines()
    assert "pinned SAM3_COMMIT" in script
    assert f'"{sam_worker.TORCH_PACKAGES[0]}"' in script
    assert sam_worker.TORCH_INDEX_URL in script
    assert "SAM_PORT:-8090" in script
