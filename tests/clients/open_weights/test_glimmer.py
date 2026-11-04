"""Tests for the Glimmer client (P3-03)."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ModelHTTPError, ModelResponseError
from rulecam.clients.open_weights import glimmer as glimmer_module
from rulecam.clients.open_weights.evidence_log import EvidenceLog
from rulecam.clients.open_weights.glimmer import (
    GlimmerClient,
    GlimmerConfig,
    image_data_url,
    user_message,
)
from rulecam.clients.relay import ModelUsed
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache
from rulecam.replay.errors import ReplayMiss
from tests.fakes import FakeClock
from tests.verify.helpers import PNG_1PX, chat_reply


class Answer(BaseModel):
    verdict: str
    p: float


class QueueTransport:
    def __init__(self, *replies: Mapping[str, object]) -> None:
        self.replies = deque(replies)
        self.requests: list[Mapping[str, object]] = []

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.requests.append(request)
        return self.replies.popleft()


class Ticker:
    def __init__(self, step: float = 0.25) -> None:
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def make_client(transport: QueueTransport, tmp_path: Path, bus: Bus | None = None) -> GlimmerClient:
    return GlimmerClient(
        transport,
        clock=FakeClock(),
        config=GlimmerConfig(quant="k-quant-17G", hardware="test-box"),
        bus=bus,
        evidence=EvidenceLog(tmp_path / "evidence" / "glimmer" / "inference-log.jsonl"),
        monotonic=Ticker(),
    )


def ask(client: GlimmerClient) -> Answer:
    return client.structured(
        model=client.model, messages=[{"role": "user", "content": "hi"}], schema=Answer
    )


def test_request_is_schema_constrained(tmp_path: Path) -> None:
    transport = QueueTransport(chat_reply({"verdict": "ok", "p": 0.2}))
    assert ask(make_client(transport, tmp_path)) == Answer(verdict="ok", p=0.2)
    request = transport.requests[0]
    assert request["path"] == "/chat/completions"
    body = request["json"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0
    assert body["reasoning_effort"] == "low"
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "Answer"
    assert fmt["json_schema"]["schema"]["required"] == ["verdict", "p"]
    assert fmt["json_schema"]["strict"] is True


def test_strict_schema_can_be_turned_off_for_the_meta_api(tmp_path: Path) -> None:
    transport = QueueTransport(chat_reply({"verdict": "ok", "p": 0.2}))
    client = make_client(transport, tmp_path)
    client._config = GlimmerConfig(strict_schema=False)
    ask(client)
    body = transport.requests[0]["json"]
    assert isinstance(body, dict)
    assert body["response_format"]["json_schema"]["strict"] is False


def test_malformed_json_retries_once_then_raises(tmp_path: Path) -> None:
    good = QueueTransport(chat_reply("not json"), chat_reply({"verdict": "ok", "p": 1.0}))
    assert ask(make_client(good, tmp_path)).p == 1.0
    assert len(good.requests) == 2
    first, second = (r["json"] for r in good.requests)
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    assert first != second, "the retry must be a different request (and replay-cache key)"
    hint = second["messages"][-1]
    assert hint["role"] == "user"
    assert "did not match the required JSON schema" in hint["content"]
    assert second["messages"][:-1] == first["messages"]
    bad = QueueTransport(chat_reply("nope"), chat_reply({"verdict": 3}))
    with pytest.raises(ModelResponseError, match="twice"):
        ask(make_client(bad, tmp_path))
    assert len(bad.requests) == 2


def test_reply_without_content_raises(tmp_path: Path) -> None:
    transport = QueueTransport({"status": 200, "json": {"choices": []}})
    with pytest.raises(ModelResponseError, match="choices"):
        ask(make_client(transport, tmp_path))
    numeric = QueueTransport({"status": 200, "json": {"choices": [{"message": {"content": 5}}]}})
    with pytest.raises(ModelResponseError, match="not text"):
        ask(make_client(numeric, tmp_path))


def test_http_error_is_raised_and_logged(tmp_path: Path) -> None:
    transport = QueueTransport({"status": 400, "json": {"error": "bad"}})
    client = make_client(transport, tmp_path)
    bus = Bus()
    seen: list[object] = []
    bus.publish = lambda topic, payload: seen.append(payload)  # type: ignore[method-assign]
    client._bus = bus
    with pytest.raises(ModelHTTPError):
        ask(client)
    line = json.loads(
        (tmp_path / "evidence/glimmer/inference-log.jsonl").read_text().splitlines()[0]
    )
    assert line["ok"] is False
    assert seen == [ModelUsed(model="muse-glimmer-30b", segment="verify", latency_ms=250)]


def test_relay_reports_http_attempts(tmp_path: Path) -> None:
    bus = Bus()
    seen: list[object] = []
    bus.publish = lambda topic, payload: seen.append(payload)  # type: ignore[method-assign]
    transport = QueueTransport({"status": 503, "json": None}, chat_reply({"verdict": "ok", "p": 0}))
    client = make_client(transport, tmp_path, bus=bus)
    client._sender = replace(client._sender, sleep=lambda _s: None)
    ask(client)
    assert [e.attempts for e in seen if isinstance(e, ModelUsed)] == [2]
    assert len(seen) == 1


def test_latency_relay_and_evidence_line(tmp_path: Path) -> None:
    bus = Bus()
    seen: list[object] = []
    bus.publish = lambda topic, payload: seen.append(payload)  # type: ignore[method-assign]
    transport = QueueTransport(chat_reply({"verdict": "ok", "p": 0.5}))
    ask(make_client(transport, tmp_path, bus=bus))
    assert seen == [ModelUsed(model="muse-glimmer-30b", segment="verify", latency_ms=250)]
    lines = (tmp_path / "evidence/glimmer/inference-log.jsonl").read_text().splitlines()
    record = json.loads(lines[0])
    assert record["latency_ms"] == 250
    assert record["quant"] == "k-quant-17G"
    assert record["hardware"] == "test-box"
    assert len(record["request_hash"]) == 64
    assert record["ok"] is True
    assert "hi" not in lines[0]


def test_images_become_data_urls(tmp_path: Path) -> None:
    frame = tmp_path / "f.png"
    frame.write_bytes(PNG_1PX)
    message = user_message("look", [frame])
    content = message["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "look"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,iVBOR")
    with pytest.raises(ValueError, match="unsupported"):
        image_data_url(tmp_path / "f.gif")


def test_from_settings_routes_through_replay_cache(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, evidence_dir=tmp_path / "ev", glimmer_quant="q")
    cache = ReplayCache(tmp_path / "replay", "replay", clock=FakeClock())
    client = GlimmerClient.from_settings(settings, cache=cache, clock=FakeClock())
    with pytest.raises(ReplayMiss):
        ask(client)
    assert not (tmp_path / "ev" / "glimmer" / "inference-log.jsonl").exists()


def test_replayed_calls_are_not_evidence(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, evidence_dir=tmp_path / "ev")
    cache = ReplayCache(tmp_path / "replay", "record", clock=FakeClock())
    real = QueueTransport(chat_reply({"verdict": "ok", "p": 0.5}))
    client = GlimmerClient.from_settings(settings, cache=cache, clock=FakeClock())
    client._sender = replace(
        client._sender, transport=cache.wrap_transport("glimmer", client.model, real)
    )
    log = tmp_path / "ev" / "glimmer" / "inference-log.jsonl"
    ask(client)
    assert len(log.read_text().splitlines()) == 1
    cache.set_mode("replay")
    assert ask(client).p == 0.5
    assert len(log.read_text().splitlines()) == 1, "a replayed call is not an inference"


def test_bus_topic_is_relay(tmp_path: Path) -> None:
    bus = Bus()
    topics: list[Topic] = []
    bus.publish = lambda topic, payload: topics.append(topic)  # type: ignore[method-assign]
    ask(make_client(QueueTransport(chat_reply({"verdict": "x", "p": 0})), tmp_path, bus))
    assert topics == [Topic.RELAY_MODEL_USED]


@pytest.mark.parametrize(("key", "expected"), [(None, "local"), ("s3cret", "s3cret")])
def test_from_settings_sends_the_configured_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str | None, expected: str
) -> None:
    seen: list[str] = []

    def transport(base_url: str, api_key: str, **_: object) -> QueueTransport:
        seen.append(api_key)
        return QueueTransport()

    monkeypatch.setattr(glimmer_module, "HttpxTransport", transport)
    settings = Settings(_env_file=None, evidence_dir=tmp_path / "ev", glimmer_api_key=key)
    cache = ReplayCache(tmp_path / "replay", "replay", clock=FakeClock())
    GlimmerClient.from_settings(settings, cache=cache, clock=FakeClock())
    assert seen == [expected]
