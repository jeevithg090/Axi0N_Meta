"""`GET /metrics` (WS5): Prometheus text, read at scrape time, public-safe."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from rulecam.api import prometheus
from rulecam.api.app import AppServices, create_app
from rulecam.api.prometheus import (
    HealthProbe,
    MetricsSources,
    VerdictCounter,
    dependency_probes,
    health_url,
    http_health,
    render,
    serverless_gpu_rate,
)
from rulecam.bus import Bus, Topic
from rulecam.contracts.events import VerifierVerdict
from rulecam.perception.metrics import Backend, PerceptionMetrics
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.cost import CameraSpend, CostReport
from rulecam.runtime.idle_stop import IdleStopStatus
from rulecam.runtime.loop import LoopMetrics, VerdictNew
from rulecam.runtime.serverless import BODY_KEY, GpuHealth, GpuState, WorkerCounts
from tests.fakes import FakeClock

TUNNEL = {"base_url": "https://rulecam-api.proxy.runpod.net", "client": ("100.64.0.9", 4000)}


def verdict(tier: str = "glimmer", label: str = "unsure") -> VerifierVerdict:
    return VerifierVerdict.model_validate(
        {
            "tier": tier,
            "verdict": label,
            "score": 0.5,
            "reason": "secret reason text",
            "latency_ms": 10,
            "model_version": "m",
        }
    )


def samples(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    out = {}
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            out[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
    return out


def value(text: str, name: str, **labels: str) -> float:
    return samples(text)[(name, tuple(sorted(labels.items())))]


def loop_metrics() -> LoopMetrics:
    return LoopMetrics(
        chunks_processed=40,
        chunks_skipped=2,
        lag_s=12.5,
        keyframe_mode=True,
        candidates=3,
        chunks_failed=1,
        chunks_skipped_idle=5,
        sam_calls=80,
        billed_frames=4000,
        billed_frames_per_min={"cam1": 1000, "cam2": 500},
        budget_exceeded=1,
        budget_degraded=("cam1",),
        in_flight=3,
        pipeline_depth=4,
        segment_latency_p50_s=6.0,
        segment_latency_p95_s=14.0,
    )


def perception() -> PerceptionMetrics:
    metrics = PerceptionMetrics(gpu_usd_per_hour=0.59)
    metrics.record_chunk("cam1", 3600.0)
    metrics.record_sam_call("cam1", backend=Backend.HOSTED, frames=30)
    metrics.record_sam_call("cam1", backend=Backend.SELF_HOSTED, frames=30, gpu_seconds=2.0)
    metrics.record_fallback("cam1")
    return metrics


def test_render_exposes_runtime_cost_verdicts_and_health() -> None:
    verdicts = VerdictCounter()
    verdicts.record(verdict())
    verdicts.record(verdict())
    verdicts.record(verdict("spark_open", "violation"))
    sam_health = {"status": "ok", "memory": {"allocated_bytes": 4096, "guard_trips": 2}}
    probes = [
        HealthProbe("glimmer", "http://g/health", fetch=lambda _: None),
        HealthProbe("sam", "http://s/health", fetch=lambda _: sam_health),
    ]
    text = render(
        MetricsSources(
            runtime=loop_metrics,
            perception=perception().report,
            verdicts=verdicts,
            probes=probes,
        )
    ).decode()
    assert value(text, "rulecam_runtime_running") == 1
    assert value(text, "rulecam_runtime_lag_seconds") == 12.5
    assert value(text, "rulecam_runtime_keyframe_mode") == 1
    assert value(text, "rulecam_runtime_in_flight_chunks") == 3
    assert value(text, "rulecam_runtime_segment_latency_seconds", quantile="0.95") == 14.0
    assert value(text, "rulecam_runtime_chunks_total", outcome="processed") == 40
    assert value(text, "rulecam_runtime_chunks_total", outcome="skipped_backpressure") == 2
    assert value(text, "rulecam_runtime_chunks_total", outcome="failed") == 1
    assert value(text, "rulecam_runtime_billed_frames_total") == 4000
    assert value(text, "rulecam_runtime_billed_frames_per_minute", camera="cam2") == 500
    assert value(text, "rulecam_runtime_budget_degraded_cameras") == 1
    # 1,500 billed frames a minute at $0.20 per 1,000 frames is $18 an hour.
    assert value(text, "rulecam_runtime_hosted_sam_usd_per_hour") == pytest.approx(18.0)
    assert value(text, "rulecam_perception_sam_calls_total", backend="hosted") == 1
    assert value(text, "rulecam_perception_sam_calls_total", backend="self_hosted") == 1
    assert value(text, "rulecam_perception_sam_fallbacks_total") == 1
    assert value(text, "rulecam_perception_gpu_seconds_total") == 2.0
    actual = value(text, "rulecam_perception_usd_per_camera_hour", basis="actual")
    assert actual == pytest.approx(0.01 + 2.0 * 0.59 / 3600)
    assert value(text, "rulecam_verdicts_total", tier="glimmer", verdict="unsure") == 2
    assert value(text, "rulecam_verdicts_total", tier="spark_open", verdict="violation") == 1
    assert value(text, "rulecam_dependency_up", service="glimmer") == 0
    assert value(text, "rulecam_dependency_up", service="sam") == 1
    assert value(text, "rulecam_sam_gpu_memory_bytes", kind="allocated") == 4096
    assert value(text, "rulecam_sam_memory_guard_trips_total") == 2
    # Public-safe: no verdict reasons, URLs or hosts leak into the exposition.
    for leaked in ("secret reason text", "http://", "/health"):
        assert leaked not in text


def test_without_runtime_only_running_zero_and_no_cost_rates() -> None:
    text = render(
        MetricsSources(
            runtime=lambda: None, perception=PerceptionMetrics().report, verdicts=VerdictCounter()
        )
    ).decode()
    assert value(text, "rulecam_runtime_running") == 0
    assert not any(name.startswith("rulecam_runtime_lag") for name, _ in samples(text))
    assert not any(name == "rulecam_perception_usd_per_camera_hour" for name, _ in samples(text))
    assert not any(name == "rulecam_dependency_up" for name, _ in samples(text))


def test_sam_memory_ignores_non_numbers_and_glimmer_memory() -> None:
    probes = [
        HealthProbe("glimmer", "g", fetch=lambda _: {"memory": {"allocated_bytes": 1}}),
        HealthProbe("sam", "s", fetch=lambda _: {"memory": {"allocated_bytes": True, "x": 1}}),
    ]
    text = "".join(m.name + "\n" for m in prometheus.dependency_metrics(probes) for _ in m.samples)
    assert "sam_gpu_memory" not in text
    assert "guard_trips" not in text


def test_probe_is_cached_for_its_ttl() -> None:
    now = [0.0]
    calls: list[str] = []

    def fetch(url: str) -> Mapping[str, object] | None:
        calls.append(url)
        return {}

    probe = HealthProbe("sam", "http://s/health", fetch=fetch, ttl_s=10.0, clock=lambda: now[0])
    assert probe.check() == {}
    now[0] = 9.9
    probe.check()
    assert len(calls) == 1
    now[0] = 10.0
    probe.check()
    assert len(calls) == 2


def test_health_urls() -> None:
    assert health_url("https://g-8080.proxy.runpod.net/v1/", strip_suffix="/v1") == (
        "https://g-8080.proxy.runpod.net/health"
    )
    assert health_url("https://s-8090.proxy.runpod.net") == "https://s-8090.proxy.runpod.net/health"
    probes = dependency_probes("http://127.0.0.1:8080/v1", None)
    assert [(p.service, p.url) for p in probes] == [("glimmer", "http://127.0.0.1:8080/health")]
    assert [p.service for p in dependency_probes(None, "https://s")] == ["sam"]


def serverless_body(state: GpuState, **workers: int) -> dict[str, object]:
    counts = {"running": 0, "idle": 0, "initializing": 0, "throttled": 0, "unhealthy": 0, **workers}
    health = GpuHealth(
        state=state,
        up=state in {"warm", "cold-ready", "starting"},
        serverless=True,
        workers_min=0,
        workers_max=3,
        workers=WorkerCounts(**counts, total=sum(counts.values())),
    )
    return {BODY_KEY: health.model_dump(mode="json")}


def test_serverless_urls_read_runpod_state_and_never_the_gpu_url() -> None:
    reads: list[str] = []

    def runpod(endpoint: str) -> Mapping[str, object]:
        reads.append(endpoint)
        return serverless_body("cold-ready", idle=2)

    probes = dependency_probes(
        "https://z000o8uch2wj4i.api.runpod.ai/v1",
        "https://imz4qu1vzwp4z6.api.runpod.ai",
        glimmer_runpod=runpod,
    )
    assert [(p.service, p.url, p.serverless) for p in probes] == [
        ("glimmer", "z000o8uch2wj4i", True),
        ("sam", "imz4qu1vzwp4z6", True),
    ]
    glimmer, sam = probes
    assert glimmer.up()  # scaled to zero but healthy: up, so no alert and the verifier recovers
    assert reads == ["z000o8uch2wj4i"]
    assert (sam.health().state, sam.up()) == ("unknown", False)  # no RunPod reader: no key
    text = render(
        MetricsSources(
            runtime=lambda: None,
            perception=PerceptionMetrics().report,
            verdicts=VerdictCounter(),
            probes=probes,
        )
    ).decode()
    assert value(text, "rulecam_dependency_up", service="glimmer") == 1
    assert value(text, "rulecam_dependency_up", service="sam") == 0
    assert value(text, "rulecam_dependency_state", service="glimmer", state="cold-ready") == 1
    assert value(text, "rulecam_dependency_state", service="glimmer", state="warm") == 0
    assert value(text, "rulecam_dependency_state", service="sam", state="unknown") == 1
    assert value(text, "rulecam_dependency_workers", service="glimmer", status="idle") == 2
    assert value(text, "rulecam_dependency_workers_limit", service="glimmer", limit="max") == 3
    assert "runpod" not in text
    assert "z000o8uch2wj4i" not in text


def test_pods_report_warm_or_unhealthy_states() -> None:
    probes = [
        HealthProbe("glimmer", "g", fetch=lambda _: None),
        HealthProbe("sam", "s", fetch=lambda _: {"status": "ok"}),
    ]
    text = render(
        MetricsSources(
            runtime=lambda: None,
            perception=PerceptionMetrics().report,
            verdicts=VerdictCounter(),
            probes=probes,
        )
    ).decode()
    assert value(text, "rulecam_dependency_state", service="glimmer", state="unhealthy") == 1
    assert value(text, "rulecam_dependency_state", service="sam", state="warm") == 1
    assert value(text, "rulecam_dependency_state", service="sam", state="cold-ready") == 0
    assert not any(name == "rulecam_dependency_workers" for name, _ in samples(text))


def test_serverless_gpu_rate_only_when_every_gpu_is_serverless() -> None:
    warm = HealthProbe("glimmer", "g", fetch=lambda _: serverless_body("warm", running=1))
    cold = HealthProbe("sam", "s", fetch=lambda _: serverless_body("cold-ready", idle=1))
    cold.serverless = warm.serverless = True
    rate = serverless_gpu_rate([warm, cold], 1.34)
    assert rate is not None
    assert rate() == pytest.approx(1.34)
    pod = HealthProbe("sam", "http://s/health", fetch=lambda _: {"status": "ok"})
    assert serverless_gpu_rate([warm, pod], 1.34) is None
    assert serverless_gpu_rate([], 1.34) is None


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(200, json={"status": "ok"}), {"status": "ok"}),
        (httpx.Response(200, text="OK"), {}),
        (httpx.Response(200, json=[1]), {}),
        (httpx.Response(503, json={"status": "loading"}), None),
    ],
)
def test_http_health(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, expected: object
) -> None:
    monkeypatch.setattr(httpx, "get", lambda url, timeout: response)
    assert http_health("http://x/health") == expected


def test_http_health_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", refuse)
    assert http_health("http://x/health") is None


async def test_verdict_counter_consumes_the_bus() -> None:
    bus = Bus()
    counter = VerdictCounter()
    task = asyncio.create_task(counter.consume(bus))
    await asyncio.sleep(0)
    bus.publish(Topic.VERDICT_NEW, VerdictNew(event_id="e1", verdict=verdict()))
    bus.publish(Topic.VERDICT_NEW, "not a verdict")
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert counter.counts() == {("glimmer", "unsure"): 1}


def test_metrics_route_is_public_and_counts_live_verdicts(tmp_path: Path) -> None:
    bus = Bus()
    services = AppServices(
        bus=bus,
        cache=ReplayCache(tmp_path / "replay", "replay", clock=FakeClock(), bus=bus),
        chain_root=tmp_path / "chain",
        site="site",
        health_probes=(HealthProbe("sam", "http://s/health", fetch=lambda _: {"status": "ok"}),),
    )
    services.verdicts.record(verdict("glimmer", "violation"))
    app = create_app(services)
    with TestClient(app, **TUNNEL) as client:  # type: ignore[arg-type]
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert value(response.text, "rulecam_verdicts_total", tier="glimmer", verdict="violation") == 1
    assert value(response.text, "rulecam_dependency_up", service="sam") == 1
    assert value(response.text, "rulecam_runtime_running") == 0
    assert "/metrics" not in app.openapi()["paths"]


def test_monitoring_config_only_uses_exported_metrics() -> None:
    root = Path(__file__).resolve().parents[2] / "deploy" / "monitoring"
    sam_health = {"memory": {"allocated_bytes": 1, "guard_trips": 0}}
    verdicts = VerdictCounter()
    verdicts.record(verdict())
    text = render(
        MetricsSources(
            runtime=loop_metrics,
            perception=perception().report,
            verdicts=verdicts,
            probes=[HealthProbe("sam", "s", fetch=lambda _: sam_health)],
            cost=lambda: CostReport(
                budget_usd_per_camera_hour=12.0, gpu_usd_per_hour=0, alarm=False
            ),
        )
    ).decode()
    exported = {name for name, _ in samples(text)}
    used: set[str] = set()
    for path in (root / "alerts.yml", root / "grafana" / "dashboards" / "rulecam.json"):
        used |= set(re.findall(r"rulecam_[a-z0-9_]+", path.read_text(encoding="utf-8")))
    assert used
    assert used <= exported, used - exported
    alerts = yaml.safe_load((root / "alerts.yml").read_text(encoding="utf-8"))
    exprs = {rule["alert"]: rule["expr"] for rule in alerts["groups"][0]["rules"]}
    assert exprs["RulecamLagHigh"] == "rulecam_runtime_lag_seconds > 10"
    assert exprs["RulecamDependencyDown"] == "rulecam_dependency_up == 0"


def test_cost_metrics_expose_the_dollar_budget_and_alarm() -> None:
    spend = CameraSpend(
        camera_id="cam1",
        usd_per_hour=14.0,
        hosted_usd_per_hour=14.0,
        gpu_usd_per_hour=0.0,
        usd=0.5,
        camera_hours=0.1,
        usd_per_camera_hour=5.0,
        over_budget=True,
        degraded=True,
    )
    report = CostReport(
        budget_usd_per_camera_hour=12.0,
        gpu_usd_per_hour=0.0,
        alarm=True,
        over_budget=("cam1",),
        usd=0.5,
        camera_hours=0.1,
        usd_per_camera_hour=5.0,
        cameras=(spend,),
        idle_stop=IdleStopStatus(enabled=True, after_s=600, stopped=True),
    )
    sources = MetricsSources(
        runtime=lambda: None,
        perception=PerceptionMetrics().report,
        verdicts=VerdictCounter(),
        cost=lambda: report,
    )
    text = render(sources).decode()
    assert value(text, "rulecam_cost_budget_alarm") == 1.0
    assert value(text, "rulecam_cost_budget_usd_per_camera_hour") == 12.0
    assert value(text, "rulecam_cost_usd_per_camera_hour") == 5.0
    assert value(text, "rulecam_cost_usd_per_hour", camera="cam1") == 14.0
    assert value(text, "rulecam_cost_usd_total") == 0.5
    assert value(text, "rulecam_cost_gpu_idle_stopped") == 1.0
