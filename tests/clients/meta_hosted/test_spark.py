"""Unit tests for the hosted Muse Spark client (task P2-01). No network: scripted transports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field, SecretStr

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ClientConfigError, ModelResponseError, StructuredOutputError
from rulecam.clients.meta_hosted.spark import SparkClient, answer_schema, build_request
from rulecam.clients.relay import ModelUsed
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache
from rulecam.replay.errors import ReplayMiss
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport
from tests.fakes import FakeClock


class SampleOutput(BaseModel):
    title: str
    score: int = Field(ge=0)


class RecordingBus(Bus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[Topic, object]] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.published.append((topic, payload))
        super().publish(topic, payload)


def responses_reply(
    text: str, *, status: str = "completed", cached: int = 0, rid: str = "resp_1"
) -> dict[str, Any]:
    """A Responses API reply in the verified shape: text in `output[].content[]` parts."""
    return {
        "status": 200,
        "headers": {"x-ratelimit-remaining-requests": "2999"},
        "json": {
            "id": rid,
            "object": "response",
            "status": status,
            "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                },
            ],
            "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": cached}},
        },
    }


def pending_reply(rid: str, status: str = "queued") -> dict[str, Any]:
    return {"status": 200, "headers": {}, "json": {"id": rid, "status": status, "output": []}}


def _messages() -> list[dict[str, object]]:
    return [
        {"role": "system", "content": "stable prefix"},
        {"role": "user", "content": "Analyse report"},
    ]


def test_structured_output_validates_and_publishes_model_used() -> None:
    transport = ScriptedTransport(responses_reply('{"title": "Report", "score": 95}', cached=64))
    bus = RecordingBus()
    client = SparkClient(transport, clock=FakeClock(), bus=bus)

    result = client.structured(messages=_messages(), schema=SampleOutput)

    assert result == SampleOutput(title="Report", score=95)
    assert client.cached_tokens == 64
    assert client.total_cached_tokens == 64
    [(topic, event)] = bus.published
    assert topic == Topic.RELAY_MODEL_USED
    assert isinstance(event, ModelUsed)
    assert (event.model, event.segment, event.attempts) == ("muse-spark-1.3", "planner", 1)


def test_request_body_follows_the_responses_schema() -> None:
    transport = ScriptedTransport(responses_reply('{"title": "a", "score": 1}'))
    SparkClient(transport, clock=FakeClock()).structured(
        messages=_messages(), schema=SampleOutput, reasoning_effort="medium"
    )

    [request] = transport.requests
    assert (request["method"], request["path"]) == ("POST", "/responses")
    body = request["json"]
    assert isinstance(body, dict)
    assert body["model"] == "muse-spark-1.3"
    assert body["stream"] is False
    assert body["input"] == _messages()
    assert body["reasoning"] == {"effort": "medium"}
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == "SampleOutput"
    assert fmt["schema"] == answer_schema(SampleOutput)
    assert "tool_choice" not in body
    assert "response_format" not in body
    assert "reasoning_effort" not in body


def test_invalid_json_then_valid_retry_succeeds() -> None:
    transport = ScriptedTransport(
        responses_reply("not json {{{"), responses_reply('{"title": "Fixed", "score": 80}')
    )
    client = SparkClient(transport, clock=FakeClock())

    result = client.structured(messages=_messages(), schema=SampleOutput)

    assert result.title == "Fixed"
    retry_input = transport.bodies()[1]["input"]
    assert len(retry_input) == 4
    assert retry_input[2] == {"role": "assistant", "content": "not json {{{"}
    assert retry_input[3]["role"] == "user"
    assert "failed schema validation" in retry_input[3]["content"]


def test_two_invalid_responses_raise_structured_output_error() -> None:
    transport = ScriptedTransport(
        responses_reply("invalid"), responses_reply('{"title": "x", "score": -1}')
    )
    client = SparkClient(transport, clock=FakeClock())

    with pytest.raises(StructuredOutputError, match="failed validation twice"):
        client.structured(messages=_messages(), schema=SampleOutput)
    assert len(transport.requests) == 2


def test_replay_miss_raises_replay_miss(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = ReplayCache(tmp_path, "replay", clock=clock)
    inner = ScriptedTransport(responses_reply('{"title": "t", "score": 1}'))
    client = SparkClient(cache.wrap_transport("meta", "muse-spark-1.3", inner), clock=clock)

    with pytest.raises(ReplayMiss):
        client.structured(messages=_messages(), schema=SampleOutput)
    assert inner.requests == []


def test_record_then_replay_serves_without_network(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = ReplayCache(tmp_path, "record", clock=clock)
    inner = ScriptedTransport(responses_reply('{"title": "rec", "score": 3}'))
    transport = cache.wrap_transport("meta", "muse-spark-1.3", inner)
    SparkClient(transport, clock=clock).structured(messages=_messages(), schema=SampleOutput)

    cache.set_mode("replay")
    replayed = SparkClient(transport, clock=clock).structured(
        messages=_messages(), schema=SampleOutput
    )

    assert replayed.title == "rec"
    assert len(inner.requests) == 1


def test_background_create_then_poll_until_completed() -> None:
    transport = ScriptedTransport(
        pending_reply("resp_bg"),
        pending_reply("resp_bg", "in_progress"),
        responses_reply('{"title": "Background", "score": 100}', rid="resp_bg"),
    )
    sleeps: list[float] = []
    client = SparkClient(transport, clock=FakeClock(), sleep=sleeps.append, poll_interval_s=0.1)

    result = client.structured(messages=_messages(), schema=SampleOutput, background=True)

    assert result.title == "Background"
    assert sleeps == [0.1, 0.1]
    create, *polls = transport.requests
    body = create["json"]
    assert isinstance(body, dict)
    assert body["background"] is True
    assert "stream" not in body  # background cannot be combined with stream
    assert [(p["method"], p["path"]) for p in polls] == [("GET", "/responses/resp_bg")] * 2


def test_background_failure_raises() -> None:
    failed = {"status": 200, "headers": {}, "json": {"id": "r", "status": "failed", "error": "x"}}
    transport = ScriptedTransport(pending_reply("r"), failed)
    client = SparkClient(transport, clock=FakeClock(), sleep=lambda _s: None)

    with pytest.raises(ModelResponseError, match="failed"):
        client.structured(messages=_messages(), schema=SampleOutput, background=True)


def test_background_poll_times_out() -> None:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    transport = ScriptedTransport(*(pending_reply("r") for _ in range(5)))
    client = SparkClient(
        transport,
        clock=FakeClock(),
        sleep=sleep,
        monotonic=lambda: now[0],
        poll_interval_s=1.0,
        poll_timeout_s=2.5,
    )

    with pytest.raises(ModelResponseError, match="still pending"):
        client.structured(messages=_messages(), schema=SampleOutput, background=True)


def test_tools_web_search_and_max_tool_calls() -> None:
    transport = ScriptedTransport(responses_reply('{"title": "S", "score": 5}'))
    SparkClient(transport, clock=FakeClock()).structured(
        messages=_messages(),
        schema=SampleOutput,
        enable_web_search=True,
        tools=[{"type": "function", "name": "lookup", "parameters": {"type": "object"}}],
        max_tool_calls=3,
    )

    body = transport.bodies()[0]
    assert body["max_tool_calls"] == 3
    assert body["tools"] == [
        {"type": "function", "name": "lookup", "parameters": {"type": "object"}},
        {"type": "web_search"},
    ]


def test_build_request_rejects_tool_choice_and_bad_cap() -> None:
    with pytest.raises(ValueError, match="tool_choice"):
        build_request(
            model="m",
            messages=[],
            schema=SampleOutput,
            reasoning_effort="low",
            background=False,
            tools=[{"type": "function", "name": "f", "tool_choice": "required"}],
        )
    with pytest.raises(ValueError, match="max_tool_calls"):
        build_request(
            model="m",
            messages=[],
            schema=SampleOutput,
            reasoning_effort="low",
            background=False,
            max_tool_calls=0,
        )


def test_attach_uploads_once_and_builds_typed_parts(tmp_path: Path) -> None:
    pdf = tmp_path / "food_code.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    video = tmp_path / "walk.mp4"
    video.write_bytes(b"fake mp4")
    transport = ScriptedTransport()
    client = SparkClient(transport, clock=FakeClock())

    assert client.attach(pdf) == {"type": "input_file", "file_id": "file-rec-0001"}
    assert client.attach(video) == {"type": "input_video", "file_id": "file-rec-0001"}
    client.attach(pdf)  # same content: no second upload

    uploads = transport.uploads
    assert len(uploads) == 2
    assert uploads[0]["form"] == {"purpose": "user_data"}
    assert uploads[0]["file"]["content_type"] == "application/pdf"
    assert uploads[1]["file"]["content_type"] == "video/mp4"


def test_attach_rejects_unsupported_type(tmp_path: Path) -> None:
    path = tmp_path / "notes.docx"
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="does not accept"):
        SparkClient(ScriptedTransport(), clock=FakeClock()).attach(path)


def test_from_settings_wraps_the_replay_cache(tmp_path: Path) -> None:
    settings = Settings(rulecam_mode="replay", rulecam_replay_dir=tmp_path)
    client = SparkClient.from_settings(settings, clock=FakeClock())
    with pytest.raises(ReplayMiss):
        client.structured(messages=_messages(), schema=SampleOutput)

    live = Settings(
        rulecam_mode="live",
        meta_api_base_url="https://api.meta.ai/v1",
        meta_api_key=SecretStr("secret-key"),
        rulecam_replay_dir=tmp_path,
    )
    assert isinstance(SparkClient.from_settings(live, clock=FakeClock()), SparkClient)
    with pytest.raises(ClientConfigError):
        SparkClient.from_settings(
            Settings(rulecam_mode="live", meta_api_base_url=None, meta_api_key=None),
            clock=FakeClock(),
        )


def test_message_items_only_contribute_output_text() -> None:
    reply = responses_reply('{"title": "a",')
    body = reply["json"]
    body["output"].append(
        {"type": "message", "content": [{"type": "output_text", "text": ' "score": 2}'}]}
    )
    body["output"].insert(0, {"type": "web_search_call", "id": "ws_1"})
    result = SparkClient(ScriptedTransport(reply), clock=FakeClock()).structured(
        messages=_messages(), schema=SampleOutput
    )
    assert result.score == 2
    assert json.loads('{"title": "a", "score": 2}') == result.model_dump()


class _Need(BaseModel):
    concern: str
    concepts: list[str] = Field(default_factory=list)
    note: str | None = None


class _Needs(BaseModel):
    needs: list[_Need] = Field(default_factory=list)


def test_answer_schema_requires_every_property_including_nested() -> None:
    schema = answer_schema(_Needs)
    assert schema["required"] == ["needs"]
    defs = schema["$defs"]
    assert isinstance(defs, dict)
    assert defs["_Need"]["required"] == ["concern", "concepts", "note"]
    # Defaults stay valid: a reply that fills them with empty values is accepted.
    reply = _Needs.model_validate({"needs": [{"concern": "c", "concepts": [], "note": None}]})
    assert reply.needs[0].concepts == []
