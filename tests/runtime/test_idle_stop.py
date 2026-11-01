"""Stopping the GPU pods after a long idle period through the RunPod REST API (WS3), fake HTTP."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from rulecam.config import Settings
from rulecam.runtime.idle_stop import USER_AGENT, IdleGpuStop, RunPodError, RunPodPods
from tests.fakes import EPOCH

KEY = "rp-test-key-value"
PODS = [
    {"id": "g1", "name": "rulecam-glimmer", "status": "RUNNING"},
    {"id": "s1", "name": "rulecam-sam", "status": "EXITED"},
    {"id": "a1", "name": "rulecam-api", "status": "RUNNING"},
    {"id": "x9", "name": "other", "status": "STARTING"},
]


class FakeRunPod:
    """Records requests; serves the pod list over two pages and accepts stop actions."""

    def __init__(self, *, fail: int | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.fail = fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            return httpx.Response(self.fail, json={"error": "nope"})
        if request.method == "GET":
            second = "cursor=c2" in str(request.url)
            page = PODS[2:] if second else PODS[:2]
            pagination = {"hasNextPage": not second, "nextCursor": "" if second else "c2"}
            return httpx.Response(200, json={"pods": page, "pagination": pagination})
        return httpx.Response(200, json={"id": request.url.path.split("/")[3]})

    def stops(self) -> list[tuple[str, object]]:
        return [(r.url.path, json.loads(r.content)) for r in self.requests if r.method == "POST"]


def key_file(tmp_path: Path, value: str = KEY) -> Path:
    path = tmp_path / ".runpod_api_key"
    path.write_text(value + "\n")
    return path


def test_stops_only_live_pods_it_was_asked_to(tmp_path: Path) -> None:
    fake = FakeRunPod()
    pods = RunPodPods(key_file(tmp_path), transport=httpx.MockTransport(fake))
    stopped = pods.stop(["rulecam-glimmer", "rulecam-sam", "x9"])
    assert stopped == ["g1", "x9"]  # the SAM pod is already stopped; the API pod is not listed
    assert fake.stops() == [
        ("/v2/pods/g1/action", {"action": "stop"}),
        ("/v2/pods/x9/action", {"action": "stop"}),
    ]
    first = fake.requests[0]
    assert first.headers["authorization"] == f"Bearer {KEY}"
    assert first.headers["user-agent"] == USER_AGENT  # Cloudflare blocks Python's default
    assert not first.headers["user-agent"].lower().startswith("python")
    assert first.url.host == "api.runpod.io"


@pytest.mark.parametrize("status", [401, 500])
def test_http_errors_never_carry_the_key(tmp_path: Path, status: int) -> None:
    pods = RunPodPods(key_file(tmp_path), transport=httpx.MockTransport(FakeRunPod(fail=status)))
    with pytest.raises(RunPodError) as error:
        pods.stop(["rulecam-sam"])
    assert f"HTTP {status}" in str(error.value)
    assert KEY not in str(error.value)


def test_transport_errors_and_bad_key_files(tmp_path: Path) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(RunPodError, match="ConnectError"):
        RunPodPods(key_file(tmp_path), transport=httpx.MockTransport(down)).stop(["x"])
    with pytest.raises(RunPodError, match="cannot read"):
        RunPodPods(tmp_path / "missing").stop(["x"])
    with pytest.raises(RunPodError, match="empty"):
        RunPodPods(key_file(tmp_path, "  ")).stop(["x"])


class Stopper:
    def __init__(self, errors: int = 0) -> None:
        self.calls = 0
        self.errors = errors

    def stop(self, pods: object) -> list[str]:
        self.calls += 1
        if self.calls <= self.errors:
            msg = "POST /v2/pods/g1/action: HTTP 500"
            raise RunPodError(msg)
        return ["g1"]


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def test_stops_once_after_the_idle_delay_and_rearms_on_activity() -> None:
    stopper = Stopper()
    idle = IdleGpuStop(stopper, after_s=600)
    assert idle.tick(at(0), set()) == []
    assert idle.tick(at(599), set()) == []
    assert idle.status().idle_s == 599
    assert idle.tick(at(600), set()) == ["g1"]
    assert idle.tick(at(900), set()) == []  # once per idle period
    assert stopper.calls == 1
    status = idle.status()
    assert status.stopped
    assert status.stopped_at == at(600)
    assert idle.tick(at(901), {"cam1"}) == []  # activity re-arms (pods are never started here)
    assert not idle.status().stopped
    assert idle.tick(at(1000), set()) == []
    assert idle.tick(at(1600), set()) == ["g1"]
    assert stopper.calls == 2


def test_activity_before_the_delay_resets_the_timer() -> None:
    stopper = Stopper()
    idle = IdleGpuStop(stopper, after_s=60)
    idle.tick(at(0), set())
    idle.tick(at(50), {"cam1"})
    idle.tick(at(51), set())
    assert idle.tick(at(100), set()) == []
    assert idle.tick(at(111), set()) == ["g1"]


def test_a_failed_stop_is_retried_later() -> None:
    stopper = Stopper(errors=1)
    idle = IdleGpuStop(stopper, after_s=10, retry_s=300)
    idle.tick(at(0), set())
    assert idle.tick(at(10), set()) == []
    assert idle.status().last_error == "POST /v2/pods/g1/action: HTTP 500"
    assert idle.tick(at(100), set()) == []  # waiting out the retry delay
    assert idle.tick(at(310), set()) == ["g1"]
    assert idle.status().last_error is None


def test_disabled_does_nothing() -> None:
    assert not IdleGpuStop(None, after_s=10).enabled
    assert not IdleGpuStop(Stopper(), after_s=0).enabled
    off = IdleGpuStop(Stopper(), after_s=0)
    off.tick(at(0), set())
    assert off.tick(at(10_000), set()) == []
    assert not off.status().enabled


def test_from_settings(tmp_path: Path) -> None:
    def build(**extra: object) -> IdleGpuStop:
        return IdleGpuStop.from_settings(Settings(_env_file=None, **extra))  # type: ignore[arg-type]

    assert not build().enabled  # default off
    assert not build(rulecam_idle_stop_gpu_after_s=600).enabled  # no key file
    armed = build(
        rulecam_idle_stop_gpu_after_s=600,
        rulecam_runpod_api_key_file=key_file(tmp_path),
        rulecam_idle_stop_gpu_pods="rulecam-sam, rulecam-glimmer,",
    )
    assert armed.enabled
    assert armed.status().pods == ("rulecam-sam", "rulecam-glimmer")
