"""`GET /metrics`: Prometheus text exposition of the API's health and cost (task WS5).

Read-only and public-safe: every value is an aggregate the API already serves as JSON
(`/runtime/metrics`, `/metrics/perception`), plus verdict counts and whether Glimmer and SAM are
up: a pod's open `/health`, or for a RunPod serverless endpoint its worker state from RunPod's
REST API (`runtime.serverless`), which never wakes a worker. No traces, event ids, URLs, keys or
per-person values; the only label beyond fixed enums is the camera id (counters are per camera,
never per person or track, R13). Values are read when Prometheus scrapes, from a registry private
to the app, so nothing is kept twice and tests never touch the process-global registry.

Alert thresholds (docs/deploy.md "Monitoring"): `rulecam_runtime_lag_seconds > 10` and
`rulecam_dependency_up == 0` for a minute or more.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from rulecam.bus import Bus, Topic
from rulecam.contracts.events import VerifierVerdict
from rulecam.perception.metrics import (
    HOSTED_USD_PER_FRAME,
    CameraPerceptionMetrics,
    PerceptionMetricsReport,
)
from rulecam.runtime.cost import CostReport
from rulecam.runtime.loop import LoopMetrics
from rulecam.runtime.serverless import (
    BODY_KEY,
    NO_KEY,
    GpuHealth,
    GpuState,
    endpoint_id,
    running_gpu_usd_per_hour,
)

PREFIX = "rulecam"
PROBE_TTL_S = 10.0
PROBE_TIMEOUT_S = 2.0
MINUTES_PER_HOUR = 60
HTTP_OK = 200

Fetch = Callable[[str], Mapping[str, object] | None]
"""GET a health URL: its JSON object (empty when not JSON) when it answers 200, else `None`."""


def http_health(url: str) -> Mapping[str, object] | None:
    """GET `url` with a short timeout; never raises.

    Args:
        url: A health endpoint (Glimmer's and the SAM pod's are open, no key).

    Returns:
        The JSON object on HTTP 200 (`{}` when the body is not a JSON object), else `None`.
    """
    try:
        response = httpx.get(url, timeout=PROBE_TIMEOUT_S)
    except httpx.HTTPError:
        return None
    if response.status_code != HTTP_OK:
        return None
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@dataclass
class HealthProbe:
    """Whether one dependency answers its health check, cached for `ttl_s`.

    Attributes:
        service: Label value, e.g. `glimmer` or `sam`.
        url: The health URL, or a serverless endpoint's id; never exported.
        fetch: How to read it (`http_health`; `RunPodServerless.fetch` for an endpoint id).
        serverless: `url` is a RunPod serverless endpoint id, read through RunPod's REST API.
        ttl_s: How long a result is reused, so scrapes never hammer a pod or the REST API.
        clock: Monotonic seconds.
    """

    service: str
    url: str
    fetch: Fetch = http_health
    serverless: bool = False
    ttl_s: float = PROBE_TTL_S
    clock: Callable[[], float] = time.monotonic
    _checked_at: float | None = field(default=None, init=False, repr=False)
    _body: Mapping[str, object] | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def check(self) -> Mapping[str, object] | None:
        """Return the cached health body (`None`: down), probing again once it is stale."""
        with self._lock:
            now = self.clock()
            if self._checked_at is None or now - self._checked_at >= self.ttl_s:
                self._body = self.fetch(self.url)
                self._checked_at = now
            return self._body

    def health(self) -> GpuHealth:
        """The dependency's state (`GET /health` → `gpus`), from the cached check."""
        return GpuHealth.from_probe(self.check())

    def up(self) -> bool:
        """Whether requests would be served (the verifier's recovery probe)."""
        return self.health().up


def health_url(base_url: str, *, strip_suffix: str = "") -> str:
    """Return `<base>/health`, dropping a trailing `strip_suffix` (e.g. Glimmer's `/v1`).

    Args:
        base_url: The service's base URL.
        strip_suffix: A path suffix the base URL carries for its API.

    Returns:
        The health URL.
    """
    base = base_url.rstrip("/")
    if strip_suffix and base.endswith(strip_suffix):
        base = base[: -len(strip_suffix)]
    return f"{base}/health"


def no_runpod_key(_: str) -> Mapping[str, object]:
    """The serverless body when no RunPod reader is configured: state `unknown`."""
    health = GpuHealth(state="unknown", up=False, serverless=True, error=NO_KEY)
    return {BODY_KEY: health.model_dump(mode="json")}


def dependency_probe(
    service: str, base_url: str, *, strip_suffix: str = "", runpod: Fetch | None = None
) -> HealthProbe:
    """The probe for one dependency: its open `/health`, or RunPod's state for an endpoint.

    Args:
        service: Label value.
        base_url: The service's base URL.
        strip_suffix: A path suffix the base URL carries for its API (Glimmer's `/v1`).
        runpod: Reads a serverless endpoint's state (`RunPodServerless.fetch`); without it a
            serverless base URL reports `unknown`. The GPU URL itself is never polled.

    Returns:
        The probe.
    """
    endpoint = endpoint_id(base_url)
    if endpoint is None:
        return HealthProbe(service, health_url(base_url, strip_suffix=strip_suffix))
    return HealthProbe(service, endpoint, fetch=runpod or no_runpod_key, serverless=True)


def dependency_probes(
    glimmer_base_url: str | None,
    sam_base_url: str | None,
    *,
    glimmer_runpod: Fetch | None = None,
    sam_runpod: Fetch | None = None,
) -> list[HealthProbe]:
    """Probes for the configured dependencies: llama-server (Glimmer) and SAM.

    Args:
        glimmer_base_url: `GLIMMER_BASE_URL` (ends in `/v1`); `None` skips it.
        sam_base_url: `SAM_BASE_URL`; `None` when runtime SAM is hosted.
        glimmer_runpod: Reads Glimmer's serverless state (with its RunPod key).
        sam_runpod: Reads SAM's serverless state (with its RunPod key).

    Returns:
        One probe per configured service.
    """
    probes = []
    if glimmer_base_url:
        probes.append(
            dependency_probe("glimmer", glimmer_base_url, strip_suffix="/v1", runpod=glimmer_runpod)
        )
    if sam_base_url:
        probes.append(dependency_probe("sam", sam_base_url, runpod=sam_runpod))
    return probes


def serverless_gpu_rate(
    probes: Sequence[HealthProbe], usd_per_worker_hour: float
) -> Callable[[], float | None] | None:
    """What the GPUs bill right now, for the cost meter, when every GPU is serverless.

    Args:
        probes: The dependency probes.
        usd_per_worker_hour: One running worker's price (`RULECAM_GPU_WORKER_USD_PER_HOUR`).

    Returns:
        A callable giving the running workers' $/h (`None` while a state is unknown); `None`
        when there is no GPU or one is a pod, so the meter charges `RULECAM_GPU_USD_PER_HOUR`.
    """
    if not probes or not all(probe.serverless for probe in probes):
        return None
    return lambda: running_gpu_usd_per_hour([p.health() for p in probes], usd_per_worker_hour)


class VerdictCounter:
    """Counts verifier verdicts by tier and verdict (never by event, camera or person)."""

    def __init__(self) -> None:
        """Start from zero."""
        self._counts: Counter[tuple[str, str]] = Counter()
        self._lock = threading.Lock()

    def record(self, verdict: VerifierVerdict) -> None:
        """Count one verdict."""
        with self._lock:
            self._counts[(verdict.tier, verdict.verdict)] += 1

    def counts(self) -> dict[tuple[str, str], int]:
        """Return a copy of the counts keyed by `(tier, verdict)`."""
        with self._lock:
            return dict(self._counts)

    async def consume(self, bus: Bus) -> None:
        """Count every `verdict.new` published on `bus` until cancelled."""
        async with contextlib.aclosing(bus.subscribe(Topic.VERDICT_NEW)) as messages:
            async for message in messages:
                verdict = getattr(message.payload, "verdict", None)
                if isinstance(verdict, VerifierVerdict):
                    self.record(verdict)


@dataclass(frozen=True)
class MetricsSources:
    """Where `/metrics` reads its values at scrape time.

    Attributes:
        runtime: The runtime loop's metrics, or `None` when the loop is not running.
        perception: The perception cost report.
        verdicts: Verdict counts.
        probes: Dependency health probes.
        cost: The dollar budget's report, when the runtime runs one (WS3).
    """

    runtime: Callable[[], LoopMetrics | None]
    perception: Callable[[], PerceptionMetricsReport]
    verdicts: VerdictCounter
    probes: Sequence[HealthProbe] = ()
    cost: Callable[[], CostReport | None] = lambda: None


def _gauge(name: str, doc: str, value: float) -> GaugeMetricFamily:
    return GaugeMetricFamily(f"{PREFIX}_{name}", doc, value=float(value))


def _counter(name: str, doc: str, value: float) -> CounterMetricFamily:
    return CounterMetricFamily(f"{PREFIX}_{name}", doc, value=float(value))


def runtime_metrics(metrics: LoopMetrics) -> Iterator[Metric]:
    """Yield the runtime loop's lag, pipeline, chunk, SAM-bill and budget metrics."""
    yield _gauge("runtime_lag_seconds", "Lag of the most-behind camera.", metrics.lag_s)
    yield _gauge(
        "runtime_keyframe_mode", "1 while any camera is in keyframe mode.", metrics.keyframe_mode
    )
    yield _gauge("runtime_in_flight_chunks", "Chunks being segmented.", metrics.in_flight)
    yield _gauge("runtime_pipeline_depth", "Chunks per camera in flight.", metrics.pipeline_depth)
    latency = GaugeMetricFamily(
        f"{PREFIX}_runtime_segment_latency_seconds",
        "Segmentation latency over the last 200 chunks.",
        labels=["quantile"],
    )
    latency.add_metric(["0.5"], metrics.segment_latency_p50_s)
    latency.add_metric(["0.95"], metrics.segment_latency_p95_s)
    yield latency
    chunks = CounterMetricFamily(
        f"{PREFIX}_runtime_chunks", "Chunks by what the loop did with them.", labels=["outcome"]
    )
    for outcome, value in (
        ("processed", metrics.chunks_processed),
        ("skipped_backpressure", metrics.chunks_skipped),
        ("failed", metrics.chunks_failed),
        ("skipped_no_rule", metrics.chunks_skipped_no_rule),
        ("skipped_idle", metrics.chunks_skipped_idle),
        ("skipped_still", metrics.chunks_skipped_still),
        ("skipped_budget", metrics.chunks_skipped_budget),
    ):
        chunks.add_metric([outcome], value)
    yield chunks
    yield _counter("runtime_candidates", "Candidate events opened.", metrics.candidates)
    yield _counter(
        "runtime_verifications_failed",
        "Candidates Tier 1 gave no verdict for (sent to the human queue).",
        metrics.verifications_failed,
    )
    yield _gauge(
        "runtime_verifier_down", "1 while the Tier 1 circuit is not closed.", metrics.verifier_down
    )
    yield _counter("runtime_windows_blind", "Rule windows cut by gaps.", metrics.windows_blind)
    yield _counter("runtime_sam_calls", "Runtime SAM requests.", metrics.sam_calls)
    yield _counter("runtime_billed_frames", "Hosted-billed SAM frames.", metrics.billed_frames)
    yield _counter("runtime_budget_exceeded", "Budget trips.", metrics.budget_exceeded)
    yield _gauge(
        "runtime_budget_degraded_cameras",
        "Cameras degraded by the SAM budget.",
        len(metrics.budget_degraded),
    )
    per_min = GaugeMetricFamily(
        f"{PREFIX}_runtime_billed_frames_per_minute",
        "Each camera's billed SAM frames over the last minute.",
        labels=["camera"],
    )
    for camera, frames in sorted(metrics.billed_frames_per_min.items()):
        per_min.add_metric([camera], frames)
    yield per_min
    yield _gauge(
        "runtime_hosted_sam_usd_per_hour",
        "Hosted SAM spend rate at the last minute's billed frames.",
        sum(metrics.billed_frames_per_min.values()) * MINUTES_PER_HOUR * HOSTED_USD_PER_FRAME,
    )


def perception_metrics(report: PerceptionMetricsReport) -> Iterator[Metric]:
    """Yield SAM calls by backend, fallbacks, GPU-seconds and the cost per camera-hour."""
    total: CameraPerceptionMetrics = report.total
    calls = CounterMetricFamily(
        f"{PREFIX}_perception_sam_calls", "SAM calls by backend.", labels=["backend"]
    )
    calls.add_metric(["hosted"], total.sam_calls_hosted)
    calls.add_metric(["self_hosted"], total.sam_calls_self_hosted)
    yield calls
    yield _counter(
        "perception_sam_fallbacks", "Self-hosted SAM outages sent to hosted.", total.sam_fallbacks
    )
    yield _counter("perception_gpu_seconds", "Self-hosted GPU-seconds.", total.gpu_seconds)
    cost = GaugeMetricFamily(
        f"{PREFIX}_perception_usd_per_camera_hour",
        "SAM cost per camera-hour (actual, and priced as hosted video).",
        labels=["basis"],
    )
    for basis, value in (
        ("actual", total.actual_usd_per_camera_hour),
        ("hosted_equivalent", total.hosted_equivalent_usd_per_camera_hour),
    ):
        if value is not None:
            cost.add_metric([basis], value)
    yield cost


def cost_metrics(report: CostReport) -> Iterator[Metric]:
    """Yield the dollar budget, its alarm, spend rates and the cost per camera-hour (WS3)."""
    yield _gauge(
        "cost_budget_usd_per_camera_hour",
        "Dollar budget per camera.",
        report.budget_usd_per_camera_hour,
    )
    yield _gauge("cost_budget_alarm", "1 while a camera is over its dollar budget.", report.alarm)
    yield _counter("cost_usd", "Model and GPU spend so far.", report.usd)
    if report.usd_per_camera_hour is not None:
        yield _gauge(
            "cost_usd_per_camera_hour",
            "Spend per camera-hour, all cameras.",
            report.usd_per_camera_hour,
        )
    rate = GaugeMetricFamily(
        f"{PREFIX}_cost_usd_per_hour", "Each camera's current spend rate.", labels=["camera"]
    )
    for camera in report.cameras:
        rate.add_metric([camera.camera_id], camera.usd_per_hour)
    yield rate
    if report.idle_stop is not None:
        yield _gauge(
            "cost_gpu_idle_stopped",
            "1 after the idle stop stopped the GPU pods.",
            report.idle_stop.stopped,
        )


def verdict_metrics(counts: Mapping[tuple[str, str], int]) -> Iterator[Metric]:
    """Yield verifier verdict counts by tier and verdict."""
    family = CounterMetricFamily(
        f"{PREFIX}_verdicts", "Verifier verdicts.", labels=["tier", "verdict"]
    )
    for (tier, verdict), value in sorted(counts.items()):
        family.add_metric([tier, verdict], value)
    yield family


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


GPU_STATES: tuple[GpuState, ...] = ("warm", "cold-ready", "starting", "unhealthy", "unknown")
WORKER_STATUSES = ("running", "idle", "initializing", "throttled", "unhealthy")


def health_metrics(healths: Sequence[tuple[str, GpuHealth]]) -> Iterator[Metric]:
    """Yield each service's `dependency_up` and state, and serverless worker counts and limits."""
    up = GaugeMetricFamily(
        f"{PREFIX}_dependency_up",
        "1 when the service would serve: a pod answers its health check, or a serverless "
        "endpoint is warm, cold-ready or starting.",
        labels=["service"],
    )
    state = GaugeMetricFamily(
        f"{PREFIX}_dependency_state",
        "1 for the service's current state.",
        labels=["service", "state"],
    )
    workers = GaugeMetricFamily(
        f"{PREFIX}_dependency_workers",
        "Serverless workers by status (only running ones bill).",
        labels=["service", "status"],
    )
    limits = GaugeMetricFamily(
        f"{PREFIX}_dependency_workers_limit",
        "Serverless workers.min and workers.max.",
        labels=["service", "limit"],
    )
    for service, health in healths:
        up.add_metric([service], float(health.up))
        for name in GPU_STATES:
            state.add_metric([service, name], float(health.state == name))
        if health.workers is not None:
            for status in WORKER_STATUSES:
                workers.add_metric([service, status], getattr(health.workers, status))
        for limit, value in (("min", health.workers_min), ("max", health.workers_max)):
            if value is not None:
                limits.add_metric([service, limit], value)
    yield from (up, state, workers, limits)


def sam_memory_metrics(body: Mapping[str, object] | None) -> Iterator[Metric]:
    """Yield the SAM pod's GPU memory and memory-guard trips from its `/health` body."""
    memory = GaugeMetricFamily(
        f"{PREFIX}_sam_gpu_memory_bytes", "Self-hosted SAM GPU memory.", labels=["kind"]
    )
    stats = body.get("memory") if body is not None else None
    trips: float | None = None
    if isinstance(stats, Mapping):
        for kind in ("allocated", "reserved", "peak_allocated", "free", "total"):
            value = _number(stats.get(f"{kind}_bytes"))
            if value is not None:
                memory.add_metric([kind], value)
        trips = _number(stats.get("guard_trips"))
    yield memory
    if trips is not None:
        yield _counter("sam_memory_guard_trips", "SAM memory guard trips.", trips)


def dependency_metrics(probes: Sequence[HealthProbe]) -> Iterator[Metric]:
    """Yield each probe's `dependency_up`, state and workers, and the SAM pod's GPU memory."""
    checked = [(probe.service, probe.check()) for probe in probes]
    yield from health_metrics([(service, GpuHealth.from_probe(body)) for service, body in checked])
    yield from sam_memory_metrics(next((body for s, body in checked if s == "sam"), None))


class RulecamCollector(Collector):
    """Collects every Rulecam metric from `MetricsSources` at scrape time."""

    def __init__(self, sources: MetricsSources) -> None:
        """Read from `sources`."""
        self._sources = sources

    def collect(self) -> Iterator[Metric]:
        """Yield all metric families."""
        loop = self._sources.runtime()
        yield _gauge("runtime_running", "1 when the runtime loop runs.", loop is not None)
        if loop is not None:
            yield from runtime_metrics(loop)
        yield from perception_metrics(self._sources.perception())
        cost = self._sources.cost()
        if cost is not None:
            yield from cost_metrics(cost)
        yield from verdict_metrics(self._sources.verdicts.counts())
        yield from dependency_metrics(self._sources.probes)


def render(sources: MetricsSources) -> bytes:
    """Render the metrics in the Prometheus text format."""
    registry = CollectorRegistry(auto_describe=False)
    registry.register(RulecamCollector(sources))
    return generate_latest(registry)


def metrics_router(sources: MetricsSources) -> APIRouter:
    """Build the router serving `GET /metrics` (not in the OpenAPI schema: it is not JSON)."""
    router = APIRouter()

    @router.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        # Health probes may wait up to PROBE_TIMEOUT_S each; keep them off the event loop.
        body = await asyncio.to_thread(render, sources)
        return Response(content=body, media_type=CONTENT_TYPE_LATEST)

    return router
