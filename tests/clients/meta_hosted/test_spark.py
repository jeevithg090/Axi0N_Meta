"""Unit tests for Muse Spark hosted client (`SparkClient`) (task P2-01)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, Field, SecretStr

from rulecam.bus import Bus, Topic
from rulecam.clients.errors import ClientConfigError, StructuredOutputError
from rulecam.clients.meta_hosted.spark import SparkClient
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


def make_ok_reply(content: str, cached_tokens: int = 15) -> dict[str, Any]:
    return {
        "status": 200,
        "headers": {},
        "json": {
            "id": "resp_001",
            "status": "completed",
            "output_text": content,
            "usage": {"cached_tokens": cached_tokens},
        },
    }


def test_structured_output_validates() -> None:
    valid_json = json.dumps({"title": "Sanitation Report", "score": 95})
    transport = ScriptedTransport(make_ok_reply(valid_json))
    bus = RecordingBus()
    client = SparkClient(transport, clock=FakeClock(), bus=bus)

    result = client.structured(
        messages=[{"role": "user", "content": "Analyze report"}],
        schema=SampleOutput,
    )

    assert result.title == "Sanitation Report"
    assert result.score == 95
    assert client.cached_tokens == 15
    assert client.total_cached_tokens == 15

    # Check relay telemetry
    assert len(bus.published) == 1
    topic, event = bus.published[0]
    assert topic == Topic.RELAY_MODEL_USED
    assert isinstance(event, ModelUsed)
    assert event.model == "muse-spark-1.3"
    assert event.segment == "planner"


def test_invalid_json_then_valid_retry_succeeds() -> None:
    bad_reply = {
        "status": 200,
        "headers": {},
        "json": {
            "id": "resp_bad",
            "output_text": "not valid json {{{",
            "usage": {"cached_tokens": 5},
        },
    }
    good_reply = make_ok_reply(json.dumps({"title": "Cleaned", "score": 80}))
    transport = ScriptedTransport(bad_reply, good_reply)
    bus = RecordingBus()
    client = SparkClient(transport, clock=FakeClock(), bus=bus)

    result = client.structured(
        messages=[{"role": "user", "content": "Evaluate"}],
        schema=SampleOutput,
    )

    assert result.title == "Cleaned"
    assert result.score == 80
    assert len(transport.requests) == 2
    retry_req = transport.requests[1]
    req_body = cast(dict[str, Any], retry_req["json"])
    input_msgs = req_body["input"]
    assert len(input_msgs) == 3
    assert input_msgs[1]["role"] == "assistant"
    assert input_msgs[2]["role"] == "user"
    assert "schema" in input_msgs[2]["content"]


def test_two_invalid_responses_raise_structured_output_error() -> None:
    bad_reply_1 = {
        "status": 200,
        "headers": {},
        "json": {"output_text": "invalid json 1"},
    }
    bad_reply_2 = {
        "status": 200,
        "headers": {},
        "json": {"output_text": '{"wrong_fields": 123}'},
    }
    transport = ScriptedTransport(bad_reply_1, bad_reply_2)
    client = SparkClient(transport, clock=FakeClock())

    with pytest.raises(StructuredOutputError, match="failed validation twice"):
        client.structured(
            messages=[{"role": "user", "content": "Evaluate"}],
            schema=SampleOutput,
        )


def test_replay_miss_raises_replay_miss(tmp_path: Path) -> None:
    clock = FakeClock()
    cache = ReplayCache(tmp_path, "replay", clock=clock)
    raw_transport = ScriptedTransport(make_ok_reply('{"title": "test", "score": 1}'))
    cached_transport = cache.wrap_transport("meta", "muse-spark-1.3", raw_transport)
    client = SparkClient(cached_transport, clock=clock)

    with pytest.raises(ReplayMiss):
        client.structured(
            messages=[{"role": "user", "content": "Unrecorded query"}],
            schema=SampleOutput,
        )


def test_background_create_and_poll() -> None:
    pending_reply = {
        "status": 200,
        "headers": {},
        "json": {"id": "job_456", "status": "in_progress"},
    }
    completed_reply = {
        "status": 200,
        "headers": {},
        "json": {
            "id": "job_456",
            "status": "completed",
            "output_text": json.dumps({"title": "Background Job", "score": 100}),
        },
    }
    transport = ScriptedTransport(pending_reply, completed_reply)
    sleeps: list[float] = []
    client = SparkClient(
        transport,
        clock=FakeClock(),
        sleep=sleeps.append,
        poll_interval_s=0.1,
    )

    result = client.structured(
        messages=[{"role": "user", "content": "Long task"}],
        schema=SampleOutput,
        background=True,
    )

    assert result.title == "Background Job"
    assert result.score == 100
    assert len(sleeps) == 1
    assert sleeps[0] == 0.1
    assert transport.requests[1]["path"] == "/responses/job_456"


def test_tools_and_web_search_options() -> None:
    transport = ScriptedTransport(make_ok_reply(json.dumps({"title": "Search", "score": 50})))
    client = SparkClient(transport, clock=FakeClock())

    client.structured(
        messages=[{"role": "user", "content": "Search query"}],
        schema=SampleOutput,
        enable_web_search=True,
        tools=[{"type": "function", "name": "custom_fn"}],
        max_tool_calls=3,
    )

    req_json = cast(dict[str, Any], transport.requests[0]["json"])
    assert req_json["max_tool_calls"] == 3
    tools = cast(list[dict[str, Any]], req_json["tools"])
    assert any(t.get("type") == "web_search" for t in tools)
    assert any(t.get("name") == "custom_fn" for t in tools)


def test_from_settings(tmp_path: Path) -> None:
    settings_live = Settings(
        rulecam_mode="live",
        meta_api_base_url="https://api.meta.ai/v1",
        meta_api_key=SecretStr("secret-key"),
    )
    client_live = SparkClient.from_settings(settings_live, clock=FakeClock())
    assert isinstance(client_live, SparkClient)

    # Missing creds in live mode raises ClientConfigError
    settings_missing = Settings(
        rulecam_mode="live",
        meta_api_base_url=None,
        meta_api_key=None,
    )
    with pytest.raises(ClientConfigError):
        SparkClient.from_settings(settings_missing, clock=FakeClock())
