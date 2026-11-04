"""Glimmer's automatic hosted fallback: when it triggers, and what the hosted server receives."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from rulecam.clients.open_weights.glimmer import (
    SCHEMA_PROMPT,
    GlimmerClient,
    glimmer_fallback,
    hosted_rewrite,
)
from rulecam.clients.transport import FailoverTransport, HttpxTransport, ModelTransportError
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache
from tests.fakes import FakeClock

OK = {"status": 200, "headers": {}, "json": {"ok": True}}
REQUEST: Mapping[str, object] = {
    "method": "POST",
    "path": "/chat/completions",
    "json": {
        "model": "muse-glimmer-30b",
        "messages": [{"role": "user", "content": "is there a hard hat?"}],
        "reasoning_budget_tokens": 512,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "A", "schema": {"type": "object"}},
        },
    },
}


class Recorder:
    def __init__(self, reply: Mapping[str, object] | Exception) -> None:
        self.reply = reply
        self.requests: list[Mapping[str, object]] = []

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.requests.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.parametrize(
    "primary_reply",
    [
        ModelTransportError("timeout"),
        {"status": 503, "headers": {}, "json": None},
        {"status": 429, "headers": {}, "json": None},
        {"status": 404, "headers": {}, "json": None},
    ],
)
def test_fails_over_when_our_server_gives_no_usable_answer(
    primary_reply: Mapping[str, object] | Exception,
) -> None:
    primary, hosted = Recorder(primary_reply), Recorder(OK)
    transport = FailoverTransport(primary, hosted, rewrite=hosted_rewrite("hosted-glimmer"))
    assert transport(REQUEST) == OK
    assert transport.failovers == 1
    assert hosted.requests[0]["json"]["model"] == "hosted-glimmer"  # type: ignore[index]


@pytest.mark.parametrize("status", [200, 400, 401])
def test_keeps_our_answer_and_client_errors(status: int) -> None:
    reply = {"status": status, "headers": {}, "json": {}}
    primary, hosted = Recorder(reply), Recorder(OK)
    transport = FailoverTransport(primary, hosted)
    assert transport(REQUEST) == reply
    assert not hosted.requests
    assert transport.failovers == 0


def test_rewrite_states_the_schema_once_and_drops_llama_fields() -> None:
    rewritten = hosted_rewrite("m")(REQUEST)["json"]
    assert isinstance(rewritten, dict)
    assert "reasoning_budget_tokens" not in rewritten
    assert rewritten["messages"][0]["role"] == "system"
    assert rewritten["messages"][0]["content"].startswith(SCHEMA_PROMPT[:30])
    assert rewritten["messages"][1] == {"role": "user", "content": "is there a hard hat?"}
    again = hosted_rewrite("m")({**REQUEST, "json": rewritten})["json"]
    assert isinstance(again, dict)
    assert len(again["messages"]) == len(rewritten["messages"])
    assert REQUEST["json"]["model"] == "muse-glimmer-30b"  # type: ignore[index]


def test_rewrite_leaves_requests_without_a_body() -> None:
    request = {"method": "GET", "path": "/models"}
    assert hosted_rewrite("m")(request) is request


def test_no_fallback_configured_keeps_the_primary() -> None:
    primary = Recorder(OK)
    assert glimmer_fallback(Settings(_env_file=None), primary) is primary


class Answer(BaseModel):
    ok: bool


def test_client_answers_through_the_hosted_server_when_ours_is_down(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def hosted(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        message = {"role": "assistant", "content": '{"ok": true}'}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    def ours(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no capacity")

    settings = Settings(
        _env_file=None,
        rulecam_data_dir=tmp_path,
        evidence_dir=tmp_path / "evidence",
        glimmer_fallback_base_url="https://hosted.test/v1",
        glimmer_fallback_api_key="hosted-key",
        glimmer_fallback_model="muse-glimmer-30b:free",
    )
    hosted_http = HttpxTransport(
        "https://hosted.test/v1",
        "hosted-key",
        client=httpx.Client(transport=httpx.MockTransport(hosted)),
    )
    ours_http = HttpxTransport(
        "https://ours.test/v1", "k", client=httpx.Client(transport=httpx.MockTransport(ours))
    )
    transport = FailoverTransport(
        ours_http, hosted_http, rewrite=hosted_rewrite(settings.glimmer_fallback_model)
    )
    cache = ReplayCache(tmp_path / "replay", "live", clock=FakeClock())
    client = GlimmerClient(
        cache.wrap_transport("glimmer", "muse-glimmer-30b", transport), clock=FakeClock()
    )
    reply = client.structured(
        model="muse-glimmer-30b", messages=[{"role": "user", "content": "ok?"}], schema=Answer
    )
    assert reply.ok
    body = json.loads(seen[0].content)
    assert body["model"] == "muse-glimmer-30b:free"
    assert seen[0].headers["authorization"] == "Bearer hosted-key"


def test_standby_then_hosted_are_chained_in_order() -> None:
    settings = Settings(
        _env_file=None,
        glimmer_standby_base_url="https://standby.test/v1",
        glimmer_fallback_base_url="https://hosted.test/v1",
    )
    chained = glimmer_fallback(settings, Recorder(OK))
    assert isinstance(chained, FailoverTransport)
    assert isinstance(chained._primary, FailoverTransport)
    only_standby = Settings(_env_file=None, glimmer_standby_base_url="https://standby.test/v1")
    assert isinstance(glimmer_fallback(only_standby, Recorder(OK)), FailoverTransport)
