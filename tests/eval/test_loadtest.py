"""Add-on 1.6: serverless burst load test (no network: httpx.MockTransport)."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import cv2
import httpx
import numpy as np
import pytest

from rulecam.eval.loadtest import (
    Endpoint,
    JobResult,
    Payload,
    billed_workers,
    glimmer_body,
    integrate_workers,
    percentile,
    render_markdown,
    run_burst,
    run_job,
    sam_body,
    scenario_report,
    to_json,
    union_seconds,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval" / "serverless_load.py"
PAYLOAD = Payload(keyframes=(b"k1", b"k2"), strip=(b"s1",), width=640, height=360)
SAM = Endpoint("sam", "https://sam.api.runpod.ai", Endpoint.auth("samkey", "edge"))
GLIMMER = Endpoint("glimmer", "https://gl.api.runpod.ai/v1", Endpoint.auth("glkey", None))


class Ticker:
    """A clock that advances one second per reading."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def handler(fail_glimmer: bool = False) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/segment_image":
            assert request.headers["Authorization"] == "Bearer edge"
            assert request.headers["X-Rulecam-Key"] == "samkey"
            body = json.loads(request.content)
            assert body["request"]["n_images"] == 2
            return httpx.Response(200, json={"detections": []})
        if request.url.path == "/v1/chat/completions":
            assert request.headers["Authorization"] == "Bearer glkey"
            if fail_glimmer:
                return httpx.Response(503, text="busy")
            return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
        if request.url.path.endswith("/workers"):
            parked = {"status": "IDLE"}
            return httpx.Response(
                200, json={"workers": [parked, {"status": "RUNNING", "uptimeSeconds": 12}]}
            )
        return httpx.Response(404)

    return httpx.MockTransport(respond)


def test_bodies() -> None:
    body = sam_body(PAYLOAD)
    assert body["images_b64"] == [
        base64.b64encode(b"k1").decode(),
        base64.b64encode(b"k2").decode(),
    ]
    chat = glimmer_body(PAYLOAD, "muse-glimmer-30b")
    content = chat["messages"][0]["content"]  # type: ignore[index]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert Endpoint.auth("k", None) == {"Authorization": "Bearer k"}


def test_job_and_burst() -> None:
    async def go() -> list[JobResult]:
        async with httpx.AsyncClient(transport=handler()) as client:
            clock = Ticker()

            async def job(i: int) -> JobResult:
                return await run_job(
                    client, sam=SAM, glimmer=GLIMMER, payload=PAYLOAD, model="m", clock=clock
                )

            return await run_burst(3, job)

    results = asyncio.run(go())
    assert len(results) == 3
    assert all(r.error is None for r in results)
    assert all(r.total_s is not None for r in results)


def test_failed_job_records_its_error() -> None:
    async def go() -> JobResult:
        async with httpx.AsyncClient(transport=handler(fail_glimmer=True)) as client:
            return await run_job(client, sam=SAM, glimmer=GLIMMER, payload=PAYLOAD, model="m")

    result = asyncio.run(go())
    assert result.sam_s is not None
    assert result.glimmer_s is None
    assert result.total_s is None
    assert result.error is not None
    assert "HTTPStatusError" in result.error


def test_maths() -> None:
    assert percentile([], 50) is None
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == pytest.approx(3.85)
    assert union_seconds([(0, 2), (1, 3), (5, 6), (5.5, 5.8)]) == 4.0
    assert integrate_workers([(0, 1), (10, 2), (15, 0)]) == 20.0
    # 2 Nov, SAM endpoint: parked IDLE/INITIALIZING workers have no uptime and are not billed;
    # one IDLE worker in its idle timeout has a container up and is.
    listing = {
        "summary": {"idle": 3, "initializing": 1, "running": 1, "throttled": 1},
        "workers": [
            {"status": "IDLE"},
            {"status": "IDLE"},
            {"status": "IDLE", "uptimeSeconds": 504},
            {"status": "INITIALIZING"},
            {"status": "RUNNING", "uptimeSeconds": 12},
            {"status": "THROTTLED"},
        ],
    }
    assert billed_workers(listing) == 2
    assert billed_workers({"workers": [{"status": "IDLE"}, {"status": "INITIALIZING"}]}) == 0
    assert billed_workers(listing["workers"]) == 2
    assert billed_workers({}) == 0
    assert billed_workers({"workers": "x"}) == 0


def test_scenario_cost_measured_and_estimated() -> None:
    ok = JobResult(started_s=0, sam_s=1.0, glimmer_s=10.0)
    ok.intervals = {"sam": (0.0, 1.0), "glimmer": (1.0, 11.0)}
    failed = JobResult(started_s=0, sam_s=2.0, error="ReadTimeout: x")
    failed.intervals = {"sam": (0.0, 2.0)}
    measured = scenario_report(
        burst=2,
        state="cold",
        results=[ok, failed],
        wall_s=11.0,
        worker_s={"sam": 1800, "glimmer": 1800},
    )
    assert measured.ok == 1
    assert measured.failed == 1
    assert measured.errors == ("ReadTimeout: x",)
    assert measured.usd == pytest.approx(1.34)
    assert measured.usd_per_alert == pytest.approx(1.34)
    assert measured.worker_s_source == "runpod worker samples"
    estimated = scenario_report(
        burst=2, state="warm", results=[ok, failed], wall_s=11.0, idle_timeout_s=0
    )
    assert estimated.worker_s == {"sam": 2.0, "glimmer": 10.0}
    assert estimated.worker_s_source == "estimate"
    by = {s.stage: s for s in estimated.stages}
    assert by["sam"].n == 2
    assert by["total"].n == 1
    nothing = scenario_report(burst=1, state="cold", results=[failed], wall_s=1.0)
    assert nothing.usd_per_alert is None
    table = render_markdown([measured, nothing])
    assert "| 2 | cold | 1 / 2 |" in table
    assert "n/a" in table
    assert json.loads(to_json([measured], {"run": "x"}))["scenarios"][0]["burst"] == 2


# --- the script, end to end against a mock transport ---------------------------------------


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("serverless_load", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_runs_bursts_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = load_script()
    folder = tmp_path / "strips" / "phone-1" / "ev-1"
    folder.mkdir(parents=True)
    for k in (1, 2):
        assert cv2.imwrite(str(folder / f"{k}.jpg"), np.zeros((36, 64, 3), np.uint8))
    for name, value in {
        "SAM_BASE_URL": "https://sam.api.runpod.ai",
        "SAM_API_KEY": "samkey",
        "SAM_EDGE_KEY": "edge",
        "GLIMMER_BASE_URL": "https://gl.api.runpod.ai/v1",
        "GLIMMER_API_KEY": "glkey",
        "RUNPOD_API_KEY": "rp",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GLIMMER_EDGE_KEY", raising=False)
    real = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=handler(), **kwargs)

    monkeypatch.setattr(script.httpx, "AsyncClient", client)
    monkeypatch.setattr(script, "SAMPLE_EVERY_S", 0.0)
    out = tmp_path / "load"
    args = ["--strips", str(tmp_path / "strips"), "--bursts", "1", "2", "--states", "warm"]
    assert script.main([*args, "--out", str(out)]) == 0
    report = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert [s["burst"] for s in report["scenarios"]] == [1, 2]
    assert report["sam_endpoint"] == "sam"
    assert report["scenarios"][0]["worker_s_source"] == "runpod worker samples"
    assert out.with_suffix(".md").is_file()
    assert script.endpoint_id("http://localhost:8090", None) is None
    assert script.endpoint_id("x", "abc") == "abc"
    monkeypatch.delenv("SAM_BASE_URL")
    with pytest.raises(SystemExit, match="set SAM_BASE_URL"):
        script.main(args)
    with pytest.raises(SystemExit, match="no strips"):
        script.load_payloads(tmp_path / "empty", None, 5)


def test_script_waits_for_cold(monkeypatch: pytest.MonkeyPatch) -> None:
    script = load_script()
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(script.asyncio, "sleep", sleep)

    async def go() -> None:
        async with httpx.AsyncClient(transport=handler()) as client:
            await script.wait_cold(client, [SAM], None, 330.0)
            assert await script.workers(client, SAM, None) is None

    asyncio.run(go())
    assert slept == [330.0]
