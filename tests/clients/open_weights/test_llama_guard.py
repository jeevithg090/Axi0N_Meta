"""Llama Guard 4 plan-time rule screen: parsing, refusal on S7/S10 only, wire format."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from rulecam.clients.errors import ClientConfigError, ModelResponseError
from rulecam.clients.open_weights.llama_edge import KEY_HEADER
from rulecam.clients.open_weights.llama_guard import (
    IDENTITY_CATEGORIES,
    LlamaGuardClient,
    parse_guard_output,
    screen_rule_text,
)
from rulecam.clients.transport import HttpxTransport
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache, ReplayMiss
from tests.fakes import FakeClock

URL = "https://guard.api.runpod.ai/v1"


def client_for(answer: str, seen: list[httpx.Request] | None = None) -> LlamaGuardClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})

    mock = httpx.Client(transport=httpx.MockTransport(handler))
    http = HttpxTransport(URL, "rp", headers={KEY_HEADER: "model"}, client=mock)
    return LlamaGuardClient(http)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("safe", (True, ())),
        ("\n\nsafe\n", (True, ())),
        ("unsafe\nS10", (False, ("S10",))),
        ("unsafe\nS7,S10", (False, ("S7", "S10"))),
        ("unsafe\nS1, S1, S14", (False, ("S1", "S14"))),
        ("unsafe", (False, ())),
    ],
)
def test_parse_guard_output(text: str, expected: tuple[bool, tuple[str, ...]]) -> None:
    assert parse_guard_output(text) == expected


def test_parse_rejects_other_text() -> None:
    with pytest.raises(ModelResponseError, match="neither safe nor unsafe"):
        parse_guard_output("I cannot help with that")


@pytest.mark.parametrize("codes", ["S7", "S10", "S1,S10"])
def test_identity_categories_refuse(codes: str) -> None:
    request = "alert me when anyone of that religion comes in"
    verdict = screen_rule_text(request, client_for(f"unsafe\n{codes}"))
    assert verdict.refused
    assert not verdict.safe
    assert "never who they are" in verdict.reason
    assert any(c in IDENTITY_CATEGORIES for c in verdict.categories)


@pytest.mark.parametrize("answer", ["safe", "unsafe\nS1", "unsafe\nS9"])
def test_other_answers_do_not_refuse(answer: str) -> None:
    verdict = client_for(answer).screen_rule_text("forklift within 2 m of a person")
    assert not verdict.refused
    assert verdict.reason == ""


def test_empty_text_is_not_sent() -> None:
    seen: list[httpx.Request] = []
    verdict = screen_rule_text("   ", client_for("unsafe\nS10", seen))
    assert verdict.safe
    assert not verdict.refused
    assert not seen


def test_wire_format_sends_both_keys_and_one_user_turn() -> None:
    seen: list[httpx.Request] = []
    client_for("safe", seen).classify("wear a hard hat in zone A")
    request = seen[0]
    assert str(request.url) == f"{URL}/chat/completions"
    assert request.headers["authorization"] == "Bearer rp"
    assert request.headers[KEY_HEADER] == "model"
    body = json.loads(request.content)
    assert body["model"] == "llama-guard-4-12b"
    assert body["messages"] == [{"role": "user", "content": "wear a hard hat in zone A"}]
    assert body["temperature"] == 0.0


def test_missing_content_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    http = HttpxTransport(URL, "k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ModelResponseError, match="no choices"):
        LlamaGuardClient(http).classify("x")


def make_cache(tmp_path: Path) -> ReplayCache:
    return ReplayCache(tmp_path / "replay", "replay", clock=FakeClock())


def test_from_settings_needs_a_url(tmp_path: Path) -> None:
    with pytest.raises(ClientConfigError, match="LLAMA_GUARD_BASE_URL"):
        LlamaGuardClient.from_settings(Settings(_env_file=None), cache=make_cache(tmp_path))


def test_from_settings_builds_a_replayable_client(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        llama_guard_base_url=URL,
        llama_guard_api_key="model",  # type: ignore[arg-type]
        llama_guard_edge_key="rp",  # type: ignore[arg-type]
    )
    client = LlamaGuardClient.from_settings(settings, cache=make_cache(tmp_path))
    assert client.model == "llama-guard-4-12b"
    with pytest.raises(ReplayMiss, match="llama_guard/llama-guard-4-12b"):
        client.classify("x")
