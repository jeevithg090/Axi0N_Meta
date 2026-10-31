"""Clip fetching (sha256-pinned cache), the HTTP client over a mock transport, the /ws listener."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
from tests.fakes import EPOCH
from tests.fakes.eval_api import SimClock

from rulecam.eval.client import (
    HttpApiClient,
    NoBus,
    Pairing,
    PlanRefusedError,
    WsBusListener,
    ws_url,
)
from rulecam.eval.errors import ClipFetchError, EvalRunError
from rulecam.eval.fetch import cached_name, http_download, resolve_clip, sha256_file
from rulecam.eval.live import sample_live
from rulecam.eval.schema import ClipEntry

VIDEO = b"\x00\x00\x00\x18ftypmp42 fake video"
SHA = hashlib.sha256(VIDEO).hexdigest()


def clip(source: dict[str, str] | None) -> ClipEntry:
    return ClipEntry.model_validate(
        {
            "name": "yard",
            "camera_id": "yard",
            "source": source,
            "duration_s": None if source is None else 5,
            "placeholder": source is None,
            "rules": [{"id": "r", "request": "x", "status": "negative"}],
        }
    )


def test_local_clips_resolve_and_are_checked(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(VIDEO)
    assert resolve_clip(clip({"path": "clip.mp4"}), tmp_path) == (tmp_path / "clip.mp4").resolve()
    pinned = clip({"path": "clip.mp4", "sha256": "0" * 64})
    with pytest.raises(ClipFetchError, match="does not match"):
        resolve_clip(pinned, tmp_path)
    with pytest.raises(ClipFetchError, match="does not exist"):
        resolve_clip(clip({"path": "missing.mp4"}), tmp_path)
    with pytest.raises(ClipFetchError, match="placeholder"):
        resolve_clip(clip(None), tmp_path)


def test_url_clips_download_once_into_the_cache(tmp_path: Path) -> None:
    calls: list[str] = []

    def download(url: str, dest: Path) -> None:
        calls.append(url)
        dest.write_bytes(VIDEO)

    entry = clip({"url": "https://example.test/v/45848-720.mp4", "sha256": SHA})
    cache = tmp_path / "cache"
    first = resolve_clip(entry, tmp_path, cache_dir=cache, download=download)
    again = resolve_clip(entry, tmp_path, cache_dir=cache, download=download)
    assert first == again == cache / f"{SHA}.mp4"
    assert calls == ["https://example.test/v/45848-720.mp4"]
    assert sha256_file(first) == SHA
    assert list(cache.iterdir()) == [first]  # no temporary file left behind


def write_other(url: str, dest: Path) -> None:
    dest.write_bytes(b"other")


def test_a_changed_upstream_file_fails_and_leaves_no_cache(tmp_path: Path) -> None:
    entry = clip({"url": "https://example.test/a.mp4", "sha256": SHA})
    with pytest.raises(ClipFetchError, match="does not match"):
        resolve_clip(
            entry,
            tmp_path,
            cache_dir=tmp_path / "c",
            download=write_other,
        )
    assert list((tmp_path / "c").iterdir()) == []


def test_cached_name_keeps_only_a_plain_extension() -> None:
    assert cached_name("https://x/a/b.MP4?x=1", SHA) == f"{SHA}.mp4"
    assert cached_name("https://x/a/b", SHA) == SHA
    assert cached_name("https://x/a/b.mp4;evil", SHA) == SHA


def test_http_download_streams_and_maps_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ok.mp4":
            return httpx.Response(200, content=VIDEO)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def stream(method: str, url: str, **kwargs: object) -> object:
        client = httpx.Client(transport=transport)
        return client.stream(method, url)

    monkeypatch.setattr(httpx, "stream", stream)
    http_download("https://example.test/ok.mp4", tmp_path / "ok")
    assert (tmp_path / "ok").read_bytes() == VIDEO
    with pytest.raises(ClipFetchError, match="could not download"):
        http_download("https://example.test/missing.mp4", tmp_path / "missing")
    monkeypatch.setattr("rulecam.eval.fetch.MAX_CLIP_BYTES", 4)
    with pytest.raises(ClipFetchError, match="larger"):
        http_download("https://example.test/ok.mp4", tmp_path / "big")


def api_handler(request: httpx.Request) -> httpx.Response:  # noqa: C901, PLR0911 - one branch per route
    path, method = request.url.path, request.method
    if (method, path) == ("POST", "/pair/token"):
        return httpx.Response(201, json={"token": "tok" * 6})
    if path.endswith("/claim"):
        assert json.loads(request.content) == {"label": "yard"}
        return httpx.Response(201, json={"camera_id": "phone-3", "label": "yard", "session": "s3"})
    if path.endswith("/chunk"):
        assert request.headers["X-Pair-Session"] == "s3"
        assert b"video/mp4" in request.content
        return httpx.Response(
            201,
            json={
                "chunk_id": "phone-3_000000",
                "camera_id": "phone-3",
                "n_frames": 30,
                "width": 640,
                "height": 360,
            },
        )
    if path == "/plans/requests":
        assert json.loads(request.content)["items"][0]["text"] == "keep clear"
        return httpx.Response(202, json={"plan_id": "plan-9"})
    if path == "/plans/plan-9":
        return httpx.Response(200, json={"plan_id": "plan-9", "rules": []})
    if path == "/plans/plan-0":
        return httpx.Response(404, json={"detail": "no plan"})
    if path == "/plans/plan-9/approve":
        return httpx.Response(200, json={"activated": ["r"], "plan_version_hash": "h"})
    if path == "/plans/plan-0/approve":
        return httpx.Response(409, json={"detail": "no rule passes"})
    if path == "/runtime/metrics":
        return httpx.Response(200, json={"lag_s": 1.5})
    if path == "/evidence/2026-10-30/chain":
        return httpx.Response(200, text='{"seq": 1}\n\n{"seq": 2}\n')
    if path == "/evidence/2026-10-29/chain":
        return httpx.Response(404)
    return httpx.Response(503, text="runtime loop not running")


def test_http_api_client_round_trip(tmp_path: Path) -> None:
    http = httpx.Client(
        base_url="http://localhost:8000", transport=httpx.MockTransport(api_handler)
    )
    api = HttpApiClient(http=http)
    pairing = api.pair("yard")
    assert pairing == Pairing(token="tok" * 6, camera_id="phone-3", session="s3")
    chunk = tmp_path / "c0000.mp4"
    chunk.write_bytes(VIDEO)
    assert api.upload(pairing, chunk).width == 640
    assert api.request_plan("keep clear", "en") == "plan-9"
    assert api.plan("plan-9") == {"plan_id": "plan-9", "rules": []}
    assert api.plan("plan-0") is None
    assert api.approve("plan-9")["plan_version_hash"] == "h"
    with pytest.raises(PlanRefusedError, match="no rule passes"):
        api.approve("plan-0")
    assert api.runtime_metrics() == {"lag_s": 1.5}
    assert api.chain(date(2026, 10, 30)) == [{"seq": 1}, {"seq": 2}]
    assert api.chain(date(2026, 10, 29)) == []
    with pytest.raises(EvalRunError, match="HTTP 503"):
        api.chain(date(2026, 10, 28))
    api.close()


def test_http_api_client_rejects_non_objects() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[1, 2]))
    api = HttpApiClient(http=httpx.Client(base_url="http://localhost", transport=transport))
    with pytest.raises(EvalRunError, match="JSON object"):
        api.runtime_metrics()


def test_ws_url() -> None:
    assert ws_url("http://localhost:8000") == "ws://localhost:8000/ws"
    assert ws_url("https://pod.example/") == "wss://pod.example/ws"


def test_ws_listener_keeps_only_watched_topics() -> None:
    listener = WsBusListener("ws://localhost:1/ws", now=lambda: EPOCH)
    listener._keep(json.dumps({"topic": "alert.new", "payload": {"event_id": "e1"}}))
    listener._keep(json.dumps({"topic": "tracks.updated", "payload": {}}))
    listener._keep(json.dumps({"topic": "verdict.new", "payload": [1]}))
    listener._keep("not json")
    frames = listener.stop()
    assert [(f.topic, f.payload) for f in frames] == [
        ("alert.new", {"event_id": "e1"}),
        ("verdict.new", {}),
    ]
    assert NoBus().stop() == []


def test_ws_listener_survives_an_unreachable_api() -> None:
    listener = WsBusListener("ws://127.0.0.1:9/ws")
    listener.start()
    assert listener.stop() == []


def test_sample_live() -> None:
    sim = SimClock()
    readings: Iterator[dict[str, object]] = iter(
        [{"lag_s": 2.0}, {"lag_s": 9, "keyframe_mode": True}, {"lag_s": None}]
    )

    class Metrics:
        def runtime_metrics(self) -> dict[str, object]:
            return next(readings)

    live = sample_live(Metrics(), samples=3, interval_s=30, source="pod", clock=sim.clock())  # type: ignore[arg-type]
    assert live.lags_s == (2.0, 9.0)
    assert live.keyframe_mode
    assert live.source == "pod"
    assert sim.t == 60
