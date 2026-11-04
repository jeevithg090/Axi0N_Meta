"""Where Spark Open calls go: own RunPod server, hosted provider, or Baseten."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from pydantic import BaseModel

from rulecam.clients.open_weights.llama_edge import KEY_HEADER
from rulecam.clients.open_weights.reasoning import ReasoningStore
from rulecam.clients.open_weights.spark_open import (
    OWN_PROVIDER,
    PROVIDER,
    SparkOpenClient,
    SparkOpenConfig,
    spark_open_fallback,
    spark_open_target,
    with_model,
)
from rulecam.clients.transport import FailoverTransport, HttpxTransport
from rulecam.config import Settings
from tests.fakes import FakeClock

RUNPOD = "https://endpoint.api.runpod.ai/v1"


class Verdict(BaseModel):
    ok: bool


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_own_server_puts_runpod_key_in_authorization_and_model_key_in_header() -> None:
    target = spark_open_target(
        settings(spark_open_base_url=RUNPOD, spark_open_api_key="model", spark_open_edge_key="rp")
    )
    assert (target.base_url, target.bearer, target.auth_scheme) == (RUNPOD, "rp", "Bearer")
    assert target.headers == {KEY_HEADER: "model"}
    assert (target.provider, target.model) == (OWN_PROVIDER, "muse-spark-open")
    assert target.hardware.startswith("runpod")


def test_hosted_provider_uses_the_key_as_bearer() -> None:
    target = spark_open_target(
        settings(
            spark_open_base_url="https://hosted.test/v1",
            spark_open_api_key="hosted",
            spark_open_model="muse-spark-open",
            spark_open_hardware="hosted",
        )
    )
    assert (target.bearer, target.headers, target.hardware) == ("hosted", {}, "hosted")


def test_baseten_is_the_default_and_the_own_server_wins_over_it() -> None:
    base = spark_open_target(settings(baseten_api_key="b"))
    assert (base.auth_scheme, base.bearer, base.provider) == ("Api-Key", "b", PROVIDER)
    assert base.model == "meta-models/muse-spark-open-1026"
    both = spark_open_target(settings(baseten_api_key="b", spark_open_base_url=RUNPOD))
    assert both.provider == OWN_PROVIDER


def test_configured_needs_a_url_or_a_baseten_key() -> None:
    assert not settings().spark_open_configured
    assert settings(baseten_api_key="b").spark_open_configured
    assert settings(spark_open_base_url=RUNPOD).spark_open_configured


def test_own_server_request_carries_both_keys_and_alias(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        message = {"content": '{"ok": true}', "reasoning_content": "fine"}
        return httpx.Response(200, json={"id": "r1", "choices": [{"message": message}]})

    target = spark_open_target(
        settings(spark_open_base_url=RUNPOD, spark_open_api_key="model", spark_open_edge_key="rp")
    )
    http = HttpxTransport(
        target.base_url,
        target.bearer,
        auth_scheme=target.auth_scheme,
        headers=target.headers,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client = SparkOpenClient(
        http,
        clock=FakeClock(),
        reasoning=ReasoningStore(tmp_path),
        config=SparkOpenConfig(model=target.model, hardware=target.hardware),
    )
    reply = client.reason([{"role": "user", "content": "ok?"}], Verdict)
    assert reply.answer.ok
    assert reply.reasoning == "fine"
    request = seen[0]
    assert str(request.url) == f"{RUNPOD}/chat/completions"
    assert request.headers["authorization"] == "Bearer rp"
    assert request.headers[KEY_HEADER] == "model"
    assert json.loads(request.content)["model"] == "muse-spark-open"


def test_hosted_fallback_answers_when_our_server_is_down(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def ours(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": {"message": "no worker"}})

    def hosted(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        message = {"content": '{"ok": true}', "reasoning_content": "hosted"}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    primary = HttpxTransport(RUNPOD, "rp", client=httpx.Client(transport=httpx.MockTransport(ours)))
    fallback = HttpxTransport(
        "https://hosted.test/v1", "hk", client=httpx.Client(transport=httpx.MockTransport(hosted))
    )
    transport = FailoverTransport(primary, fallback, rewrite=with_model("hosted-spark"))
    client = SparkOpenClient(transport, clock=FakeClock(), reasoning=ReasoningStore(tmp_path))
    reply = client.reason([{"role": "user", "content": "ok?"}], Verdict)
    assert reply.answer.ok
    assert reply.reasoning == "hosted"
    assert json.loads(seen[0].content)["model"] == "hosted-spark"
    assert seen[0].headers["authorization"] == "Bearer hk"


def test_fallback_is_only_wired_when_configured() -> None:
    primary = HttpxTransport(RUNPOD, "rp")
    assert spark_open_fallback(settings(), primary) is primary
    configured = settings(spark_open_fallback_base_url="https://hosted.test/v1")
    assert isinstance(spark_open_fallback(configured, primary), FailoverTransport)
