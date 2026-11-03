"""RunPod serverless GPU state from RunPod's REST API, never from the GPU itself (fake HTTP)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from rulecam.config import Settings
from rulecam.runtime.idle_stop import USER_AGENT
from rulecam.runtime.serverless import (
    BODY_KEY,
    NO_KEY,
    GpuHealth,
    RunPodServerless,
    WorkerCounts,
    classify,
    endpoint_id,
    running_gpu_usd_per_hour,
    runpod_key,
)

KEY = "rp-edge-key-value"
ENDPOINT = "imz4qu1vzwp4z6"


class FakeRest:
    """Serves `GET /v2/serverless/{id}` and its `/workers`; records every request."""

    def __init__(
        self,
        summary: dict[str, int] | None = None,
        *,
        workers: dict[str, object] | None = None,
        fail: int | None = None,
    ) -> None:
        self.summary = summary or {}
        self.workers = {"min": 0, "max": 2, "idleTimeout": 300} if workers is None else workers
        self.fail = fail
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            return httpx.Response(self.fail, json={"error": "nope"})
        if request.url.path.endswith("/workers"):
            counts = {"running": 0, "idle": 0, "initializing": 0, "throttled": 0, "unhealthy": 0}
            counts.update(self.summary)
            counts["total"] = sum(counts.values())
            return httpx.Response(200, json={"workers": [], "summary": counts})
        return httpx.Response(200, json={"id": ENDPOINT, "workers": self.workers})


def reader(fake: FakeRest, key: str | None = KEY) -> RunPodServerless:
    return RunPodServerless(lambda: key, transport=httpx.MockTransport(fake))


def test_endpoint_ids_come_only_from_serverless_hosts() -> None:
    assert endpoint_id(f"https://{ENDPOINT}.api.runpod.ai") == ENDPOINT
    assert endpoint_id("https://z000o8uch2wj4i.api.runpod.ai/v1/") == "z000o8uch2wj4i"
    assert endpoint_id("https://abc-8090.proxy.runpod.net") is None
    assert endpoint_id("http://127.0.0.1:8080/v1") is None
    assert endpoint_id("https://api.runpod.ai") is None
    assert endpoint_id("https://a.b.api.runpod.ai") is None
    assert endpoint_id(None) is None


@pytest.mark.parametrize(
    ("counts", "workers_max", "state"),
    [
        (WorkerCounts(running=1, idle=2), 2, "warm"),
        (WorkerCounts(idle=3), 2, "cold-ready"),
        (WorkerCounts(throttled=2), 2, "cold-ready"),
        (WorkerCounts(), 2, "cold-ready"),
        (WorkerCounts(initializing=1), 2, "starting"),
        (WorkerCounts(unhealthy=2), 2, "unhealthy"),
        (WorkerCounts(running=1), 0, "unhealthy"),
    ],
)
def test_classify(counts: WorkerCounts, workers_max: int, state: str) -> None:
    got, why = classify(counts, workers_max)
    assert got == state
    assert (why is not None) == (state == "unhealthy")


def test_reads_state_from_the_rest_api_with_the_key_and_never_the_gpu() -> None:
    fake = FakeRest({"idle": 3, "throttled": 1}, workers={"min": 1, "max": 3})
    health = reader(fake).read(ENDPOINT)
    assert health == GpuHealth(
        state="cold-ready",
        up=True,
        serverless=True,
        workers_min=1,
        workers_max=3,
        workers=WorkerCounts(idle=3, throttled=1, total=4),
    )
    paths = [r.url.path for r in fake.requests]
    assert paths == [f"/v2/serverless/{ENDPOINT}", f"/v2/serverless/{ENDPOINT}/workers"]
    for request in fake.requests:
        assert request.method == "GET"
        assert request.url.host == "api.runpod.io"  # never <id>.api.runpod.ai: that wakes a worker
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["user-agent"] == USER_AGENT


def test_running_worker_is_warm_and_max_zero_is_unhealthy() -> None:
    assert reader(FakeRest({"running": 1})).read(ENDPOINT).state == "warm"
    stopped = reader(FakeRest(workers={"min": 0, "max": 0})).read(ENDPOINT)
    assert (stopped.state, stopped.up) == ("unhealthy", False)
    assert stopped.error is not None
    assert "workers.max is 0" in stopped.error


@pytest.mark.parametrize("status", [401, 500])
def test_rest_errors_are_unknown_and_leak_neither_key_nor_endpoint(status: int) -> None:
    health = reader(FakeRest(fail=status)).read(ENDPOINT)
    assert (health.state, health.up, health.serverless) == ("unknown", False, True)
    assert health.error is not None
    assert f"HTTP {status}" in health.error
    assert KEY not in health.error
    assert ENDPOINT not in health.error


def test_no_key_bad_json_and_missing_limits_are_unknown() -> None:
    fake = FakeRest()
    assert reader(fake, key=None).read(ENDPOINT).error == NO_KEY
    assert not fake.requests

    def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>")

    broken = RunPodServerless(lambda: KEY, transport=httpx.MockTransport(garbage))
    assert broken.read(ENDPOINT).state == "unknown"
    no_max = reader(FakeRest(workers={"min": 0})).read(ENDPOINT)
    assert no_max.state == "unknown"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    slow = RunPodServerless(lambda: KEY, transport=httpx.MockTransport(down))
    assert "ConnectTimeout" in (slow.read(ENDPOINT).error or "")


def test_odd_summary_values_are_ignored() -> None:
    fake = FakeRest()

    def odd(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/workers"):
            return httpx.Response(200, json={"summary": {"running": -1, "idle": "2", "total": 1}})
        return fake(request)

    health = RunPodServerless(lambda: KEY, transport=httpx.MockTransport(odd)).read(ENDPOINT)
    assert health.workers == WorkerCounts(total=1)


def test_fetch_body_round_trips_and_pods_read_as_warm_or_unhealthy() -> None:
    body = reader(FakeRest({"initializing": 1})).fetch(ENDPOINT)
    assert set(body) == {BODY_KEY}
    health = GpuHealth.from_probe(body)
    assert (health.state, health.up, health.serverless) == ("starting", True, True)
    assert GpuHealth.from_probe({"status": "ok"}) == GpuHealth(state="warm", up=True)
    down = GpuHealth.from_probe(None)
    assert (down.state, down.up, down.serverless) == ("unhealthy", False, False)


def test_key_file_wins_over_the_edge_key(tmp_path: Path) -> None:
    edge = SecretStr("edge")
    key_file = tmp_path / ".runpod_api_key"
    assert runpod_key(None, edge)() == "edge"
    assert runpod_key(key_file, edge)() == "edge"  # missing file: the edge key
    key_file.write_text("\n")
    assert runpod_key(key_file, None)() is None
    key_file.write_text("management\n")
    assert runpod_key(key_file, edge)() == "management"
    settings = Settings(_env_file=None, rulecam_runpod_api_key_file=key_file)
    fake = FakeRest()
    RunPodServerless.for_service(settings, edge, transport=httpx.MockTransport(fake)).read("e")
    assert fake.requests[0].headers["authorization"] == "Bearer management"


def test_running_gpu_rate_needs_every_gpu_serverless_and_known() -> None:
    warm = GpuHealth(state="warm", up=True, serverless=True, workers=WorkerCounts(running=2))
    cold = GpuHealth(state="cold-ready", up=True, serverless=True, workers=WorkerCounts(idle=1))
    unknown = GpuHealth(state="unknown", up=False, serverless=True, error="x")
    pod = GpuHealth(state="warm", up=True)
    assert running_gpu_usd_per_hour([warm, cold], 1.34) == pytest.approx(2.68)
    assert running_gpu_usd_per_hour([cold, cold], 1.34) == 0.0
    assert running_gpu_usd_per_hour([warm, unknown], 1.34) is None
    assert running_gpu_usd_per_hour([warm, pod], 1.34) is None
    assert running_gpu_usd_per_hour([], 1.34) is None
