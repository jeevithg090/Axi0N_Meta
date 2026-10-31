"""`GET /metrics`: Prometheus text exposition of the API's health and cost (task WS5).

Read-only and public-safe: every value is an aggregate the API already serves as JSON
(`/runtime/metrics`, `/metrics/perception`), plus verdict counts and whether Glimmer and the SAM
pod answer their health checks. No traces, event ids, URLs, keys or per-person values; the only
label beyond fixed enums is the camera id (counters are per camera, never per person or track,
R13). Values are read when Prometheus scrapes, from a registry private to the app, so nothing is
kept twice and tests never touch the process-global registry.

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
from rulecam.runtime.loop import LoopMetrics

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
        url: The health URL; never exported.
        fetch: How to GET it (injected in tests).
        ttl_s: How long a result is reused, so scrapes never hammer a pod.
        clock: Monotonic seconds.
    """

    service: str
    url: str
    fetch: Fetch = http_health
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


def dependency_probes(glimmer_base_url: str | None, sam_base_url: str | None) -> list[HealthProbe]:
    """Probes for the configured dependencies: llama-server (Glimmer) and the SAM pod.

    Args:
        glimmer_base_url: `GLIMMER_BASE_URL` (ends in `/v1`); `None` skips it.
        sam_base_url: `SAM_BASE_URL`; `None` when runtime SAM is hosted.

    Returns:
        One probe per configured service.
    """
    probes = []
    if glimmer_base_url:
        probes.append(HealthProbe("glimmer", health_url(glimmer_base_url, strip_suffix="/v1")))
    if sam_base_url:
        probes.append(HealthProbe("sam", health_url(sam_base_url)))
    return probes


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
    """

    runtime: Callable[[], LoopMetrics | None]
    perception: Callable[[], PerceptionMetricsReport]
    verdicts: VerdictCounter
    probes: Sequence[HealthProbe] = ()


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


def dependency_metrics(probes: Sequence[HealthProbe]) -> Iterator[Metric]:
    """Yield `dependency_up` per probe, plus the SAM pod's GPU memory from its `/health`."""
    up = GaugeMetricFamily(
        f"{PREFIX}_dependency_up",
        "1 when the service answers its health check.",
        labels=["service"],
    )
    memory = GaugeMetricFamily(
        f"{PREFIX}_sam_gpu_memory_bytes", "Self-hosted SAM GPU memory.", labels=["kind"]
    )
    trips: float | None = None
    for probe in probes:
        body = probe.check()
        up.add_metric([probe.service], 0.0 if body is None else 1.0)
        stats = body.get("memory") if body is not None and probe.service == "sam" else None
        if not isinstance(stats, Mapping):
            continue
        for kind in ("allocated", "reserved", "peak_allocated", "free", "total"):
            value = _number(stats.get(f"{kind}_bytes"))
            if value is not None:
                memory.add_metric([kind], value)
        trips = _number(stats.get("guard_trips"))
    yield up
    yield memory
    if trips is not None:
        yield _counter("sam_memory_guard_trips", "SAM memory guard trips.", trips)


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
