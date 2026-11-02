"""Latency and cold-start load test of the serverless GPU endpoints (add-on 1.6).

Every latency figure in the reports predates the move of SAM 3.1 and Glimmer to RunPod
serverless load balancers. This replays what the runtime sends them, in bursts, cold and warm:

- one **alert job** is what a verified alert costs the GPUs: a chunk's keyframes to SAM's
  `POST /segment_image` (the `sam` stage), then an evidence strip to Glimmer's
  `POST /chat/completions` (the `glimmer` stage); `total` is the two in a row;
- a **burst** starts N jobs at once (1, 5, 20); **cold** means both endpoints had scaled to zero
  before it, **warm** that it follows another burst straight away;
- per scenario: p50 / p95 per stage, failures, and **dollars per verified alert**: the worker
  time billed during the scenario (sampled from RunPod's endpoint health when an API key is
  given, else estimated as the union of request time per endpoint plus one idle timeout) at
  $1.34/h per running RTX 4090 worker, divided by the jobs that finished.

The HTTP calls go through an injected `httpx.AsyncClient`, so tests use `httpx.MockTransport`.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

import httpx

USD_PER_WORKER_HOUR = 1.34  # RunPod serverless RTX 4090 (ADA_24), per running worker
IDLE_TIMEOUT_S = 300.0  # both endpoints' idle timeout
STAGES = ("sam", "glimmer", "total")
LIVE_WORKER_STATES = ("initializing", "idle", "running")
GLIMMER_PROMPT = (
    "You verify safety-rule alerts from site cameras. Rule: Keep people at least 2 metres away "
    "from a moving forklift. Look at the frames and answer as JSON with keys verdict "
    "(violation, no_violation or unsure), p_violation (0 to 1) and reason (one sentence)."
)


@dataclass(frozen=True)
class Endpoint:
    """One serverless endpoint: base URL and the headers its edge and worker check."""

    name: str
    base_url: str
    headers: Mapping[str, str]
    endpoint_id: str | None = None  # for RunPod's worker counts

    @staticmethod
    def auth(
        api_key: str, edge_key: str | None, key_header: str = "X-Rulecam-Key"
    ) -> dict[str, str]:
        """The runtime's headers: the edge key as bearer and the worker key apart, or one bearer."""
        if edge_key:
            return {"Authorization": f"Bearer {edge_key}", key_header: api_key}
        return {"Authorization": f"Bearer {api_key}"}


@dataclass(frozen=True)
class Payload:
    """One alert job's inputs: a chunk's keyframes (JPEG) and a strip's frames (JPEG)."""

    keyframes: tuple[bytes, ...]
    strip: tuple[bytes, ...]
    width: int
    height: int
    concepts: tuple[str, ...] = ("person", "forklift")


def sam_body(payload: Payload) -> dict[str, object]:
    """The `/segment_image` body the runtime sends for one chunk (boxes only, no masks)."""
    return {
        "request": {
            "concepts": list(payload.concepts),
            "n_images": len(payload.keyframes),
            "width": payload.width,
            "height": payload.height,
            "include_confidence": True,
            "masks": False,
        },
        "images_b64": [base64.b64encode(image).decode("ascii") for image in payload.keyframes],
    }


def glimmer_body(payload: Payload, model: str, *, max_tokens: int = 512) -> dict[str, object]:
    """A Tier 1-sized chat request: the prompt and the strip's frames as data URLs."""
    parts: list[dict[str, object]] = [{"type": "text", "text": GLIMMER_PROMPT}]
    parts.extend(
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(f).decode("ascii")},
        }
        for f in payload.strip
    )
    return {
        "model": model,
        "messages": [{"role": "user", "content": parts}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }


@dataclass
class JobResult:
    """One alert job: per-stage seconds, or the error that stopped it."""

    started_s: float
    sam_s: float | None = None
    glimmer_s: float | None = None
    error: str | None = None
    intervals: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def total_s(self) -> float | None:
        """Both stages, when both finished."""
        if self.sam_s is None or self.glimmer_s is None:
            return None
        return self.sam_s + self.glimmer_s


Clock = Callable[[], float]


async def _timed_post(
    client: httpx.AsyncClient,
    endpoint: Endpoint,
    path: str,
    body: Mapping[str, object],
    clock: Clock,
) -> tuple[float, float]:
    start = clock()
    reply = await client.post(
        endpoint.base_url.rstrip("/") + path, json=body, headers=dict(endpoint.headers)
    )
    reply.raise_for_status()
    return start, clock()


async def run_job(
    client: httpx.AsyncClient,
    *,
    sam: Endpoint,
    glimmer: Endpoint,
    payload: Payload,
    model: str,
    clock: Clock = time.monotonic,
) -> JobResult:
    """Run one alert job: SAM on the chunk, then Glimmer on the strip."""
    result = JobResult(started_s=clock())
    try:
        start, end = await _timed_post(client, sam, "/segment_image", sam_body(payload), clock)
        result.sam_s, result.intervals["sam"] = end - start, (start, end)
        body = glimmer_body(payload, model)
        start, end = await _timed_post(client, glimmer, "/chat/completions", body, clock)
        result.glimmer_s, result.intervals["glimmer"] = end - start, (start, end)
    except httpx.HTTPError as exc:
        result.error = f"{type(exc).__name__}: {str(exc)[:200]}"
    return result


async def run_burst(n: int, job: Callable[[int], Awaitable[JobResult]]) -> list[JobResult]:
    """Start `n` jobs at once and wait for all of them."""
    return list(await asyncio.gather(*(job(i) for i in range(n))))


def percentile(values: Sequence[float], q: float) -> float | None:
    """The `q`-th percentile (0-100) by linear interpolation; `None` for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q / 100.0
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def union_seconds(intervals: Sequence[tuple[float, float]]) -> float:
    """Total time covered by possibly overlapping intervals."""
    total, end = 0.0, -math.inf
    for start, stop in sorted(intervals):
        if stop <= end:
            continue
        total += stop - max(start, end)
        end = stop
    return total


def integrate_workers(samples: Sequence[tuple[float, int]]) -> float:
    """Worker-seconds from `(time, live workers)` samples (step function, last sample ends it)."""
    ordered = sorted(samples)
    return sum((t1 - t0) * n for (t0, n), (t1, _) in itertools.pairwise(ordered))


def live_workers(health: Mapping[str, object]) -> int:
    """Billed workers in a RunPod endpoint health reply (`workers`: initializing/idle/running)."""
    workers = health.get("workers")
    if not isinstance(workers, Mapping):
        return 0
    return sum(int(workers.get(state, 0) or 0) for state in LIVE_WORKER_STATES)


@dataclass(frozen=True)
class StageStats:
    """Latency of one stage in one scenario."""

    stage: str
    n: int
    p50_s: float | None
    p95_s: float | None
    max_s: float | None


@dataclass(frozen=True)
class ScenarioReport:
    """One burst size in one state (cold or warm)."""

    burst: int
    state: str
    jobs: int
    ok: int
    failed: int
    errors: tuple[str, ...]
    wall_s: float
    stages: tuple[StageStats, ...]
    worker_s: dict[str, float]
    worker_s_source: str
    usd: float
    usd_per_alert: float | None


def stage_stats(results: Sequence[JobResult]) -> tuple[StageStats, ...]:
    """p50 / p95 / max per stage over the jobs that reached it."""
    out = []
    for stage in STAGES:
        values = [v for r in results if (v := getattr(r, f"{stage}_s")) is not None]
        out.append(
            StageStats(
                stage=stage,
                n=len(values),
                p50_s=percentile(values, 50),
                p95_s=percentile(values, 95),
                max_s=max(values) if values else None,
            )
        )
    return tuple(out)


def estimated_worker_s(results: Sequence[JobResult], idle_timeout_s: float) -> dict[str, float]:
    """Lower-bound worker time per endpoint: the union of its request time plus one idle tail."""
    out = {}
    for name in ("sam", "glimmer"):
        spans = [r.intervals[name] for r in results if name in r.intervals]
        out[name] = union_seconds(spans) + (idle_timeout_s if spans else 0.0)
    return out


def scenario_report(
    *,
    burst: int,
    state: str,
    results: Sequence[JobResult],
    wall_s: float,
    worker_s: Mapping[str, float] | None = None,
    usd_per_worker_hour: float = USD_PER_WORKER_HOUR,
    idle_timeout_s: float = IDLE_TIMEOUT_S,
) -> ScenarioReport:
    """Summarise one scenario; `worker_s` is measured worker time, else it is estimated."""
    ok = [r for r in results if r.error is None]
    billed = dict(worker_s) if worker_s is not None else estimated_worker_s(results, idle_timeout_s)
    usd = sum(billed.values()) / 3600.0 * usd_per_worker_hour
    return ScenarioReport(
        burst=burst,
        state=state,
        jobs=len(results),
        ok=len(ok),
        failed=len(results) - len(ok),
        errors=tuple(sorted({r.error for r in results if r.error})),
        wall_s=wall_s,
        stages=stage_stats(results),
        worker_s=billed,
        worker_s_source="runpod health samples" if worker_s is not None else "estimate",
        usd=usd,
        usd_per_alert=usd / len(ok) if ok else None,
    )


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def render_markdown(reports: Sequence[ScenarioReport]) -> str:
    """A table: one row per scenario, p50 / p95 per stage, cost per verified alert."""
    lines = [
        "| Burst | State | OK / jobs | SAM p50 / p95 s | Glimmer p50 / p95 s | Total p50 / p95 s "
        "| Worker-s (source) | $ per alert |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        by = {s.stage: s for s in r.stages}
        cells = [f"{_fmt(by[s].p50_s)} / {_fmt(by[s].p95_s)}" for s in STAGES]
        per_alert = "n/a" if r.usd_per_alert is None else f"${r.usd_per_alert:.4f}"
        lines.append(
            f"| {r.burst} | {r.state} | {r.ok} / {r.jobs} | {' | '.join(cells)} | "
            f"{sum(r.worker_s.values()):.0f} ({r.worker_s_source}) | {per_alert} |"
        )
    return "\n".join(lines) + "\n"


def to_json(reports: Sequence[ScenarioReport], meta: Mapping[str, object]) -> str:
    """The run as JSON: metadata plus every scenario."""
    return json.dumps({**meta, "scenarios": [asdict(r) for r in reports]}, indent=2) + "\n"
