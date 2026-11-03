"""Glimmer's reasoning budget (add-on 2.2): sent, cut replies get a higher cap, reasoning kept."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from rulecam.clients.errors import ModelResponseError, ModelTruncatedError
from rulecam.clients.open_weights.evidence_log import EvidenceLog
from rulecam.clients.open_weights.glimmer import (
    GlimmerClient,
    GlimmerConfig,
    parse_completion,
    usage_of,
)
from rulecam.clients.open_weights.reasoning import ReasoningStore
from rulecam.config import Settings
from rulecam.replay.cache import ReplayCache
from tests.clients.open_weights.test_glimmer import Answer, QueueTransport, Ticker
from tests.fakes import FakeClock

GOOD = json.dumps({"verdict": "ok", "p": 0.5})


def reply(
    content: str | None,
    *,
    finish: str = "stop",
    reasoning: str | None = None,
    usage: dict[str, object] | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    body: dict[str, object] = {"choices": [{"message": message, "finish_reason": finish}]}
    if usage is not None:
        body["usage"] = usage
    return {"status": 200, "headers": {}, "json": body}


def client(
    transport: QueueTransport, tmp_path: Path, config: GlimmerConfig | None = None
) -> GlimmerClient:
    return GlimmerClient(
        transport,
        clock=FakeClock(),
        config=config or GlimmerConfig(),
        evidence=EvidenceLog(tmp_path / "log.jsonl"),
        monotonic=Ticker(),
        reasoning=ReasoningStore(tmp_path / "reasoning"),
    )


def bodies(transport: QueueTransport) -> list[dict[str, object]]:
    return [r["json"] for r in transport.requests]  # type: ignore[misc]


def ask(glimmer: GlimmerClient) -> Answer:
    return glimmer.structured(
        model=glimmer.model, messages=[{"role": "user", "content": "q"}], schema=Answer
    )


def test_the_budget_is_sent_only_when_configured(tmp_path: Path) -> None:
    off = QueueTransport(reply(GOOD))
    ask(client(off, tmp_path))
    assert "reasoning_budget_tokens" not in bodies(off)[0]
    on = QueueTransport(reply(GOOD))
    ask(client(on, tmp_path, GlimmerConfig(reasoning_budget_tokens=256)))
    (body,) = bodies(on)
    assert body["reasoning_budget_tokens"] == 256
    assert "JSON" in str(body["reasoning_budget_message"])
    zero = QueueTransport(reply(GOOD))
    ask(client(zero, tmp_path, GlimmerConfig(reasoning_budget_tokens=0)))
    assert bodies(zero)[0]["reasoning_budget_tokens"] == 0


def test_a_cut_reply_is_retried_with_a_higher_cap_not_a_hint(tmp_path: Path) -> None:
    transport = QueueTransport(
        reply(None, finish="length", reasoning="thinking a lot"),
        reply('{"verdict": "o', finish="length"),
        reply(GOOD, reasoning="enough"),
    )
    config = GlimmerConfig(max_tokens=1000, max_tokens_ceiling=3000)
    got = client(transport, tmp_path, config).structured_reply(
        [{"role": "user", "content": "q"}], Answer
    )
    assert got.answer == Answer(verdict="ok", p=0.5)
    assert [b["max_tokens"] for b in bodies(transport)] == [1000, 2000, 3000]
    assert all(b["messages"] == bodies(transport)[0]["messages"] for b in bodies(transport))
    assert (got.max_tokens, got.calls, got.finish_reason) == (3000, 3, "stop")
    assert got.reasoning_ref is not None
    assert (tmp_path / "reasoning" / f"{got.reasoning_ref}.txt").read_text() == "enough"


def test_a_reply_cut_at_the_ceiling_is_reported(tmp_path: Path) -> None:
    usage: dict[str, object] = {"prompt_tokens": 900, "completion_tokens": 2048}
    transport = QueueTransport(
        reply(None, finish="length", usage=usage), reply("{", finish="length", usage=usage)
    )
    config = GlimmerConfig(max_tokens=1024, max_tokens_ceiling=2048)
    with pytest.raises(ModelTruncatedError, match="max_tokens=2048") as caught:
        ask(client(transport, tmp_path, config))
    assert caught.value.max_tokens == 2048
    assert isinstance(caught.value, ModelResponseError), "callers' fallbacks still apply"
    assert len(transport.requests) == 2


def test_a_malformed_reply_that_was_not_cut_still_gets_the_hint(tmp_path: Path) -> None:
    transport = QueueTransport(reply("nope"), reply(GOOD))
    ask(client(transport, tmp_path))
    first, second = bodies(transport)
    assert first["max_tokens"] == second["max_tokens"]
    assert "did not match" in str(second["messages"][-1])  # type: ignore[index]


def test_a_valid_reply_marked_cut_is_kept(tmp_path: Path) -> None:
    transport = QueueTransport(reply(GOOD, finish="length"))
    assert ask(client(transport, tmp_path)).p == 0.5
    assert len(transport.requests) == 1


def test_usage_reasoning_and_finish_reason_reach_the_evidence_log(tmp_path: Path) -> None:
    usage = {
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
        "completion_tokens_details": {"reasoning_tokens": 250},
    }
    transport = QueueTransport(reply(GOOD, reasoning="why", usage=usage))
    ask(client(transport, tmp_path))
    line = json.loads((tmp_path / "log.jsonl").read_text())
    assert line["finish_reason"] == "stop"
    assert line["usage"] == {
        "prompt_tokens": 1200.0,
        "completion_tokens": 300.0,
        "total_tokens": 1500.0,
        "reasoning_tokens": 250.0,
    }
    assert (tmp_path / "reasoning" / f"{line['reasoning_ref']}.txt").read_text() == "why"
    assert "q" not in line.get("usage", {})


def test_without_reasoning_nothing_is_stored(tmp_path: Path) -> None:
    transport = QueueTransport(reply(GOOD))
    got = client(transport, tmp_path).structured_reply([{"role": "user", "content": "q"}], Answer)
    assert got.reasoning_ref is None
    assert not (tmp_path / "reasoning").exists()


def test_usage_of_reads_counts_and_timings() -> None:
    body = {
        "usage": {"prompt_tokens": 10, "completion_tokens": True},
        "timings": {"predicted_ms": 812.5, "predicted_per_second": 40, "cache_n": 3},
    }
    assert usage_of(body) == {
        "prompt_tokens": 10.0,
        "predicted_ms": 812.5,
        "predicted_per_second": 40.0,
    }
    assert usage_of({}) == {}


def test_null_content_is_an_error_unless_cut() -> None:
    with pytest.raises(ModelResponseError, match="not text"):
        parse_completion({"choices": [{"message": {"content": None}, "finish_reason": "stop"}]})
    cut = parse_completion({"choices": [{"message": {"content": None}, "finish_reason": "length"}]})
    assert (cut.content, cut.finish_reason) == ("", "length")


def test_from_settings_carries_the_caps_and_budget(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        evidence_dir=tmp_path / "ev",
        rulecam_data_dir=tmp_path / "data",
        glimmer_max_tokens=512,
        glimmer_max_tokens_ceiling=1024,
        glimmer_reasoning_budget_tokens=128,
    )
    cache = ReplayCache(tmp_path / "replay", "replay", clock=FakeClock())
    glimmer = GlimmerClient.from_settings(settings, cache=cache, clock=FakeClock())
    config: GlimmerConfig = glimmer._config
    assert replace(config, model="m") == replace(
        GlimmerConfig(max_tokens=512, max_tokens_ceiling=1024, reasoning_budget_tokens=128),
        model="m",
    )
