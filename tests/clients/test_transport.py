"""Transport, retry policy and token bucket (no network: httpx.MockTransport)."""

from __future__ import annotations

import random
from collections.abc import Mapping

import httpx
import pytest

from rulecam.clients.errors import ModelHTTPError, ModelResponseError
from rulecam.clients.ratelimit import TokenBucket
from rulecam.clients.transport import (
    HttpReply,
    HttpxTransport,
    ModelTransportError,
    RetryPolicy,
    Sender,
    parse_reply,
)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_httpx_transport_sends_json_with_bearer_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"ok": True}, headers={"X-RateLimit-Remaining-Requests": "9"}
        )

    transport = HttpxTransport(
        "https://api.example.test/v1/",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    reply = transport({"method": "POST", "path": "/responses", "json": {"model": "sam-3.1"}})

    assert reply["status"] == 200
    assert reply["json"] == {"ok": True}
    headers = reply["headers"]
    assert isinstance(headers, dict)
    assert headers["x-ratelimit-remaining-requests"] == "9"  # names are lower-cased
    assert str(seen[0].url) == "https://api.example.test/v1/responses"
    assert seen[0].headers["authorization"] == "Bearer secret"


def test_httpx_transport_custom_auth_scheme() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    transport = HttpxTransport(
        "https://api.example.test/v1",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        auth_scheme="Api-Key",
    )
    transport({"method": "POST", "path": "/chat/completions", "json": {}})
    assert seen[0].headers["authorization"] == "Api-Key secret"


def test_httpx_transport_uploads_multipart_files() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.read())
        return httpx.Response(200, text="not json")

    transport = HttpxTransport(
        "https://api.example.test/v1",
        "k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    reply = transport(
        {
            "method": "POST",
            "path": "/files",
            "form": {"purpose": "user_data"},
            "file": {"filename": "c.mp4", "content_type": "video/mp4", "content": b"MP4DATA"},
        }
    )

    assert reply["json"] is None
    assert b'name="purpose"' in seen[0]
    assert b"user_data" in seen[0]
    assert b'filename="c.mp4"' in seen[0]
    assert b"MP4DATA" in seen[0]


def test_httpx_transport_rejects_non_bytes_content() -> None:
    transport = HttpxTransport(
        "https://api.example.test/v1",
        "k",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )

    with pytest.raises(TypeError):
        transport(
            {"path": "/files", "file": {"filename": "a", "content_type": "b", "content": "str"}}
        )


def test_httpx_transport_wraps_connection_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    transport = HttpxTransport(
        "https://api.example.test/v1",
        "k",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ModelTransportError, match="ConnectError"):
        transport({"method": "POST", "path": "/responses", "json": {}})


def _sender(transport: object, clock: FakeTime, attempts: int = 4) -> Sender:
    bucket = TokenBucket(6000, monotonic=clock.monotonic, sleep=clock.sleep)
    return Sender(
        transport,  # type: ignore[arg-type]
        bucket,
        RetryPolicy(max_attempts=attempts),
        clock.sleep,
        random.Random(1),  # noqa: S311 - jitter
    )


def test_transport_errors_are_retried_then_raised() -> None:
    calls: list[int] = []

    def flaky(request: Mapping[str, object]) -> Mapping[str, object]:
        calls.append(1)
        if len(calls) < 3:
            raise ModelTransportError("reset")
        return {"status": 200, "headers": {}, "json": {}}

    assert _sender(flaky, FakeTime()).send({"path": "/x"}).attempts == 3

    def dead(request: Mapping[str, object]) -> Mapping[str, object]:
        raise ModelTransportError("reset")

    with pytest.raises(ModelTransportError):
        _sender(dead, FakeTime(), attempts=2).send({"path": "/x"})


def test_zero_remaining_requests_drains_the_bucket() -> None:
    clock = FakeTime()
    replies = iter([{"status": 200, "headers": {"x-ratelimit-remaining-requests": "0"}}] * 2)
    sender = _sender(lambda request: next(replies), clock)

    sender.send({"path": "/x"})
    sender.send({"path": "/x"})

    assert clock.sleeps == [pytest.approx(0.01)]  # waited for one token at 100 req/s


def test_zero_attempt_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        _sender(lambda request: {"status": 200}, FakeTime(), attempts=0).send({"path": "/x"})


def test_backoff_doubles_with_jitter_and_honours_retry_after() -> None:
    policy = RetryPolicy(base_delay_s=0.5, max_delay_s=8.0)
    rng = random.Random(0)  # noqa: S311 - jitter

    delays = [policy.delay_s(n, rng, None) for n in (1, 2, 3, 6)]

    assert 0.25 <= delays[0] <= 0.5
    assert 0.5 <= delays[1] <= 1.0
    assert 1.0 <= delays[2] <= 2.0
    assert 4.0 <= delays[3] <= 8.0
    assert policy.delay_s(1, rng, 3.0) == 3.0


def test_http_date_retry_after_falls_back_to_backoff() -> None:
    clock = FakeTime()
    replies = iter(
        [
            {"status": 429, "headers": {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}},
            {"status": 200, "headers": {}},
        ]
    )

    _sender(lambda request: next(replies), clock).send({"path": "/x"})

    assert 0.25 <= clock.sleeps[0] <= 0.5


def test_reply_validation_and_error_messages() -> None:
    with pytest.raises(ModelResponseError):
        parse_reply({"headers": {}})
    assert HttpReply(status=400, json={"error": "plain"}).error_message() == "plain"
    assert HttpReply(status=400, json={"error": {"message": "nested"}}).error_message() == "nested"
    assert HttpReply(status=400, json=None).error_message() == ""
    assert HttpReply(status=404).header("missing") is None


def test_non_retryable_status_raises_immediately() -> None:
    sender = _sender(
        lambda request: {"status": 404, "json": {"error": {"message": "model_not_found"}}},
        FakeTime(),
    )

    with pytest.raises(ModelHTTPError, match="model_not_found") as raised:
        sender.send({"path": "/responses"})
    assert raised.value.attempts == 1


def test_token_bucket_limits_bursts_and_refills() -> None:
    clock = FakeTime()
    bucket = TokenBucket(120, capacity=2, monotonic=clock.monotonic, sleep=clock.sleep)

    assert bucket.rate_per_minute == pytest.approx(120)
    assert bucket.try_acquire() == 0.0
    assert bucket.try_acquire() == 0.0
    assert bucket.try_acquire() == pytest.approx(0.5)
    bucket.acquire()
    assert clock.sleeps == [pytest.approx(0.5)]


@pytest.mark.parametrize(("rate", "capacity"), [(0, None), (60, 0)])
def test_token_bucket_rejects_non_positive_settings(rate: float, capacity: float | None) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        TokenBucket(rate, capacity=capacity)
