"""Health of the RunPod serverless GPUs, read from RunPod's REST API, never from the GPU itself.

SAM and Glimmer can run as RunPod serverless load balancers (`https://<id>.api.runpod.ai`,
docs/deploy.md "Serverless SAM" and "Serverless Glimmer"). Their edge answers 401 to a request
without the RunPod key, so probing `<base>/health` reported both GPUs down forever; with the key,
the same probe every 10 s would start a worker each time and keep both GPUs billing. For such a
base URL the API therefore reads the endpoint's state from RunPod's REST API, which never wakes a
worker: `GET /v2/serverless/{id}` (`workers.min`, `workers.max`) and
`GET /v2/serverless/{id}/workers` (its `summary`: workers by status). Pod URLs keep their open
`/health` probe (`api.prometheus`).

The state (`GpuHealth.state`):

- `warm`: a worker is running (billed: loading the model, serving, or in its idle timeout);
- `cold-ready`: scaled to zero but healthy: no worker runs, the next request starts one (a worker
  is idle with its image cached, or throttled while its host's GPU is busy elsewhere);
- `starting`: nothing is ready yet, RunPod is preparing a worker (`initializing`);
- `unhealthy`: `workers.max` is 0 (RunPod sets it after 7 days without a request) or every
  worker listed is unhealthy;
- `unknown`: the REST API gave no answer (no key, a refused key, a timeout).

The first three count as up (`rulecam_dependency_up 1`, the verifier's recovery probe passes):
a request would be served, after a cold start if need be. `GET /health` serves the state, so the
UI can say "GPU waking" instead of "down".

The RunPod key is the management key in `RULECAM_RUNPOD_API_KEY_FILE` when set, else the
service's edge key (`SAM_EDGE_KEY`, `GLIMMER_EDGE_KEY`), which is a RunPod API key. It is sent
only to the REST API, never logged and never put in an error; errors name no endpoint id either.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field, SecretStr

from rulecam.config import Settings
from rulecam.contracts import Frozen
from rulecam.runtime.idle_stop import API_BASE, RunPodError, runpod_call, runpod_client

SERVERLESS_HOST_SUFFIX = ".api.runpod.ai"
BODY_KEY = "runpod_serverless"
TIMEOUT_S = 3.0

GpuState = Literal["warm", "cold-ready", "starting", "unhealthy", "unknown"]
UP_STATES: frozenset[GpuState] = frozenset({"warm", "cold-ready", "starting"})
NO_KEY = "no RunPod key (RULECAM_RUNPOD_API_KEY_FILE, or the service's *_EDGE_KEY)"

RunPodKey = Callable[[], str | None]
"""Returns the RunPod API key to read endpoint state with, or `None` when there is none."""


class WorkerCounts(Frozen):
    """An endpoint's workers by status (RunPod's `summary`).

    Attributes:
        running: Started and billed (cold start, serving, idle timeout).
        idle: Scaled down, image and models cached on the host; not billed.
        initializing: Scaled down while RunPod pulls the image on a host; not billed.
        throttled: Scaled down, the host's GPUs busy with other work; not billed.
        unhealthy: Crashed or failed to start; RunPod retries it.
        total: All of them.
    """

    running: int = Field(default=0, ge=0)
    idle: int = Field(default=0, ge=0)
    initializing: int = Field(default=0, ge=0)
    throttled: int = Field(default=0, ge=0)
    unhealthy: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)


class GpuHealth(Frozen):
    """One GPU dependency's state (`GET /health` → `gpus`).

    Attributes:
        state: See the module docstring; a pod is `warm` when its `/health` answers, else
            `unhealthy`.
        up: Requests would be served (`warm`, `cold-ready`, `starting`).
        serverless: Read from RunPod's REST API (a serverless endpoint), not a pod's `/health`.
        workers_min: The endpoint's `workers.min` (1 keeps a worker warm and billed).
        workers_max: Its `workers.max` (0: it cannot start a worker).
        workers: Its workers by status.
        error: Why the state is `unknown` or `unhealthy`.
    """

    state: GpuState
    up: bool
    serverless: bool = False
    workers_min: int | None = None
    workers_max: int | None = None
    workers: WorkerCounts | None = None
    error: str | None = None

    @classmethod
    def from_probe(cls, body: Mapping[str, object] | None) -> GpuHealth:
        """Read a probe's cached body: a serverless state, else a pod's `/health` (or `None`).

        Args:
            body: What the probe fetched; `None` when a pod did not answer.

        Returns:
            The health; a pod that answered is `warm`.
        """
        if body is None:
            return cls(state="unhealthy", up=False, error="health check failed")
        if BODY_KEY in body:
            return cls.model_validate(body[BODY_KEY])
        return cls(state="warm", up=True)


def endpoint_id(base_url: str | None) -> str | None:
    """The RunPod serverless endpoint id of a base URL, or `None` for anything else.

    Args:
        base_url: E.g. `https://imz4qu1vzwp4z6.api.runpod.ai` or `https://<id>.api.runpod.ai/v1`.

    Returns:
        `imz4qu1vzwp4z6` for the first; `None` for a pod or local URL.
    """
    if not base_url:
        return None
    host = (urlsplit(base_url).hostname or "").lower()
    if not host.endswith(SERVERLESS_HOST_SUFFIX):
        return None
    label = host.removesuffix(SERVERLESS_HOST_SUFFIX)
    return label if label and "." not in label else None


def classify(counts: WorkerCounts, workers_max: int) -> tuple[GpuState, str | None]:
    """Name the state of an endpoint from its worker counts and `workers.max`.

    Args:
        counts: Workers by status.
        workers_max: The endpoint's worker limit.

    Returns:
        The state and, when it is `unhealthy`, why.
    """
    if workers_max == 0:
        return "unhealthy", "workers.max is 0: no worker can start"
    if counts.running:
        return "warm", None
    if counts.idle or counts.throttled:
        return "cold-ready", None
    if counts.initializing:
        return "starting", None
    if counts.unhealthy:
        return "unhealthy", "every worker is unhealthy"
    return "cold-ready", None


def _counts(summary: object) -> WorkerCounts:
    if not isinstance(summary, Mapping):
        return WorkerCounts()
    values = {k: summary.get(k) for k in WorkerCounts.model_fields}
    return WorkerCounts(**{k: v for k, v in values.items() if isinstance(v, int) and v >= 0})


def _workers_setting(endpoint: object, name: str) -> int | None:
    workers = endpoint.get("workers") if isinstance(endpoint, Mapping) else None
    value = workers.get(name) if isinstance(workers, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _unknown(error: str) -> GpuHealth:
    return GpuHealth(state="unknown", up=False, serverless=True, error=error)


def runpod_key(key_file: Path | None, edge_key: SecretStr | None) -> RunPodKey:
    """The RunPod key for reading endpoint state: the key file's when readable, else `edge_key`.

    Args:
        key_file: `RULECAM_RUNPOD_API_KEY_FILE` (a management key, also used by the idle stop).
        edge_key: The service's edge key (`SAM_EDGE_KEY`, `GLIMMER_EDGE_KEY`), a RunPod API key.

    Returns:
        A reader called on every refresh, so a rotated key file is picked up.
    """

    def read() -> str | None:
        if key_file is not None:
            try:
                key = key_file.read_text(encoding="utf-8").strip()
            except OSError:
                key = ""
            if key:
                return key
        return edge_key.get_secret_value() if edge_key is not None else None

    return read


class RunPodServerless:
    """Reads a serverless endpoint's state from RunPod's REST API (never wakes a worker)."""

    def __init__(
        self,
        key: RunPodKey,
        *,
        base_url: str = API_BASE,
        timeout_s: float = TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Create the reader.

        Args:
            key: Returns the RunPod API key, read on every call.
            base_url: The REST API.
            timeout_s: Per-request timeout.
            transport: httpx transport (a `MockTransport` in tests).
        """
        self._key = key
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._transport = transport

    @classmethod
    def for_service(
        cls,
        settings: Settings,
        edge_key: SecretStr | None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> RunPodServerless:
        """The reader for one service: `RULECAM_RUNPOD_API_KEY_FILE`, else its `edge_key`."""
        return cls(runpod_key(settings.rulecam_runpod_api_key_file, edge_key), transport=transport)

    def read(self, endpoint: str) -> GpuHealth:
        """Read one endpoint's limits and workers; never raises.

        Args:
            endpoint: The endpoint id.

        Returns:
            Its health; `unknown` (with the error) when the REST API gave no answer.
        """
        key = self._key()
        if not key:
            return _unknown(NO_KEY)
        try:
            with runpod_client(
                key, base_url=self._base_url, timeout_s=self._timeout_s, transport=self._transport
            ) as client:
                config: Any = runpod_call(client, "GET", f"/v2/serverless/{endpoint}")
                listing: Any = runpod_call(client, "GET", f"/v2/serverless/{endpoint}/workers")
        except (RunPodError, ValueError) as exc:
            return _unknown(str(exc).replace(endpoint, "{id}") or type(exc).__name__)
        counts = _counts(listing.get("summary") if isinstance(listing, Mapping) else None)
        workers_max = _workers_setting(config, "max")
        if workers_max is None:
            return _unknown("the endpoint has no workers.max")
        state, why = classify(counts, workers_max)
        return GpuHealth(
            state=state,
            up=state in UP_STATES,
            serverless=True,
            workers_min=_workers_setting(config, "min"),
            workers_max=workers_max,
            workers=counts,
            error=why,
        )

    def fetch(self, endpoint: str) -> Mapping[str, object]:
        """`read`, as the body a `HealthProbe` caches (`GpuHealth.from_probe` reads it back)."""
        return {BODY_KEY: self.read(endpoint).model_dump(mode="json")}


def running_gpu_usd_per_hour(
    healths: Sequence[GpuHealth], usd_per_worker_hour: float
) -> float | None:
    """What the GPUs bill right now, when every one is serverless with a known state.

    Args:
        healths: Each GPU dependency's health.
        usd_per_worker_hour: The price of one running serverless worker.

    Returns:
        Running workers times the price; `None` when there is no GPU dependency, one is a pod
        (billed by the hour whatever it does), or a state is unknown (the caller falls back to
        `RULECAM_GPU_USD_PER_HOUR`).
    """
    if not healths or any(
        not h.serverless or h.state == "unknown" or h.workers is None for h in healths
    ):
        return None
    return sum(h.workers.running for h in healths if h.workers is not None) * usd_per_worker_hour
