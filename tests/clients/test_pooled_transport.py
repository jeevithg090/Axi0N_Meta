"""`PooledTransport`: Tier 1 calls spread over several Glimmer servers, with failover."""

from __future__ import annotations

import threading
from collections.abc import Mapping

import pytest

from rulecam.clients.open_weights.glimmer import glimmer_extra_urls
from rulecam.clients.transport import ModelTransportError, PooledTransport
from rulecam.config import Settings

REQUEST: Mapping[str, object] = {"method": "POST", "path": "/chat/completions", "json": {}}


class Server:
    """A fake server answering with fixed statuses in turn (or failing to connect)."""

    def __init__(self, name: str, *statuses: int | None) -> None:
        self.name = name
        self.statuses = list(statuses) or [200]
        self.calls = 0

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        if status is None:
            raise ModelTransportError("POST /chat/completions failed: ConnectError")
        return {"status": status, "headers": {}, "json": {"server": self.name}}


def test_requests_rotate_over_idle_servers() -> None:
    a, b = Server("a"), Server("b")
    pool = PooledTransport([a, b])
    served = [pool(REQUEST)["json"] for _ in range(4)]
    assert served == [{"server": "a"}, {"server": "b"}, {"server": "a"}, {"server": "b"}]


@pytest.mark.parametrize("failure", [None, 404, 503, 429])
def test_a_failing_server_is_passed_over(failure: int | None) -> None:
    broken, healthy = Server("broken", failure), Server("healthy")
    pool = PooledTransport([broken, healthy])
    assert [pool(REQUEST)["json"] for _ in range(2)] == [{"server": "healthy"}] * 2
    assert broken.calls == 1  # tried once first, in turn, then the rotation starts elsewhere


def test_when_every_server_fails_the_last_answer_or_error_is_returned() -> None:
    assert PooledTransport([Server("a", 503), Server("b", 404)])(REQUEST)["status"] == 404
    with pytest.raises(ModelTransportError):
        PooledTransport([Server("a", None), Server("b", None)])(REQUEST)


def test_the_least_busy_server_is_picked() -> None:
    started, release = threading.Event(), threading.Event()

    class Slow(Server):
        def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
            started.set()
            release.wait(5)
            return super().__call__(request)

    slow, fast = Slow("slow"), Server("fast")
    pool = PooledTransport([slow, fast])
    worker = threading.Thread(target=pool, args=(REQUEST,))
    worker.start()
    started.wait(5)
    assert [pool(REQUEST)["json"] for _ in range(3)] == [{"server": "fast"}] * 3
    release.set()
    worker.join(5)


def test_an_empty_pool_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one member"):
        PooledTransport([])


def test_extra_glimmer_urls_are_read_in_order() -> None:
    settings = Settings(_env_file=None, glimmer_extra_base_urls=" https://b/v1, ,https://c/v1 ")
    assert glimmer_extra_urls(settings) == ["https://b/v1", "https://c/v1"]
