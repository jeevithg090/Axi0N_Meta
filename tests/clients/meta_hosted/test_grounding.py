"""Tests for search grounding: raw results, citations and `SparkClient.grounded`. No network."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from rulecam.clients.errors import StructuredOutputError
from rulecam.clients.meta_hosted.grounding import (
    INCLUDE_RESULTS,
    cited_urls,
    grounded,
    normal_url,
    search_sources,
)
from rulecam.clients.meta_hosted.spark import SparkClient, build_request
from tests.clients.meta_hosted.transport_fakes import ScriptedTransport
from tests.fakes import FakeClock

MET = "https://weather.metoffice.gov.uk/forecast/gcn4fph7r"


class Answer(BaseModel):
    source: str


def body(text: str, hits: list[tuple[str, str]], cites: list[str]) -> dict[str, Any]:
    """A completed reply in the shape seen live (3 Nov 2026): search call, then the message."""
    return {
        "id": "resp_1",
        "status": "completed",
        "output": [
            {
                "type": "web_search_call",
                "status": "completed",
                "results": [
                    {"type": "text_result", "url": u, "title": t, "snippet": f"{t} says"}
                    for u, t in hits
                ],
            },
            {"type": "web_search_call", "status": "completed"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [
                            {"type": "url_citation", "url": c, "start_index": 0, "end_index": 1}
                            for c in cites
                        ],
                    }
                ],
            },
        ],
    }


def test_normal_url_ignores_spelling_differences() -> None:
    assert normal_url("HTTPS://WWW.Example.com/a/?utm_source=x&b=1#top") == normal_url(
        "https://example.com/a?b=1"
    )
    assert normal_url("https://example.com/a") != normal_url("https://example.com/b")
    assert normal_url("example.com/x").startswith("https:")


def test_sources_and_citations_are_read_from_the_reply() -> None:
    reply = body("{}", [(MET, "Glastonbury weather"), ("", "no url")], [MET])
    sources, searches = search_sources(reply)
    assert searches == 2
    assert [s.url for s in sources] == [MET]
    assert cited_urls(reply) == [MET]
    found = grounded(Answer(source=MET), [reply, body("{}", [(MET + "/", "dup")], [])])
    assert len(found.sources) == 1
    assert found.searches == 4
    assert found.retrieved("https://www.weather.metoffice.gov.uk/forecast/gcn4fph7r/") is not None
    assert found.retrieved("https://weather.metoffice.gov.uk/") is None


def test_build_request_adds_tool_options_and_include() -> None:
    request = build_request(
        model="m",
        messages=[],
        schema=Answer,
        reasoning_effort="low",
        background=False,
        web_search=True,
        web_search_options={"search_context_size": "medium"},
        include=[INCLUDE_RESULTS],
    )
    assert request["tools"] == [{"type": "web_search", "search_context_size": "medium"}]
    assert request["include"] == [INCLUDE_RESULTS]


def test_grounded_returns_the_answer_with_its_sources() -> None:
    transport = ScriptedTransport(
        {"status": 200, "json": body(f'{{"source": "{MET}"}}', [(MET, "weather")], [MET])}
    )
    client = SparkClient(transport, clock=FakeClock())
    found = client.grounded(
        messages=[{"role": "user", "content": "q"}],
        schema=Answer,
        user_location={"country": "GB", "city": "Glastonbury"},
        search_context_size="low",
    )
    assert found.answer.source == MET
    assert found.retrieved(MET) is not None
    sent = transport.bodies()[0]
    assert sent["include"] == [INCLUDE_RESULTS]
    assert sent["tools"] == [
        {
            "type": "web_search",
            "user_location": {"type": "approximate", "country": "GB", "city": "Glastonbury"},
            "search_context_size": "low",
        }
    ]


def test_grounded_retries_once_and_keeps_both_replies_sources() -> None:
    other = "https://example.org/page"
    transport = ScriptedTransport(
        {"status": 200, "json": body("not json", [(MET, "weather")], [])},
        {"status": 200, "json": body(f'{{"source": "{other}"}}', [(other, "page")], [other])},
    )
    found = SparkClient(transport, clock=FakeClock(), sleep=lambda _s: None).grounded(
        messages=[{"role": "user", "content": "q"}], schema=Answer
    )
    assert found.retrieved(MET) is not None
    assert found.retrieved(other) is not None
    failing = ScriptedTransport(
        {"status": 200, "json": body("nope", [], [])}, {"status": 200, "json": body("x", [], [])}
    )
    with pytest.raises(StructuredOutputError):
        SparkClient(failing, clock=FakeClock()).grounded(
            messages=[{"role": "user", "content": "q"}], schema=Answer
        )
