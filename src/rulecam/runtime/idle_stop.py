"""Stop the GPU pods when no camera has had an active plan for a while (WS3, optional).

`RULECAM_IDLE_STOP_GPU_AFTER_S` (default 0: off) arms an idle timer: when no camera has a rule of
the active plan and a recent chunk (`cost.active_cameras`) for that many seconds, the GPU pods
named in `RULECAM_IDLE_STOP_GPU_PODS` (pod names or ids; default `rulecam-glimmer,rulecam-sam`)
are stopped through the RunPod REST API (`POST /v2/pods/{id}/action {"action": "stop"}`), once.
Activity re-arms the timer. **Glimmer is never stopped under a running plan:** a camera with a rule
of the active plan that delivered a chunk in the last minute is activity, and so is any candidate
still waiting for Tier 1 (`busy`, the runtime's `verify_pending`), so a verification in flight
never loses its GPU. Cameras of an active plan that stop sending chunks for `after_s` do count as
idle (that is the point of the idle stop). If they come back while the pods are stopped, the
runtime does not go blind or silent: Tier 1's circuit breaker opens (`verify.guard`), every
candidate goes to the human queue as "verifier unavailable", and `/runtime/metrics` and the UI
header show `verifier_down` until Glimmer answers again. Pods are never started from here: bring
them back with `scripts/deploy/runpod_up.sh`, and expect the runtime to fall back to hosted SAM
(and the planner to skip its Glimmer audit) while they are stopped.

The API key is read from `RULECAM_RUNPOD_API_KEY_FILE` at each call. It is sent only as a Bearer
header to `api.runpod.io` and is never logged or put in an error. Requests carry a non-Python
User-Agent: Cloudflare in front of the API blocks Python's default ones.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import Field

from rulecam.config import Settings
from rulecam.contracts import Frozen
from rulecam.errors import RulecamError
from rulecam.log import get_logger

_log = get_logger(__name__)

API_BASE = "https://api.runpod.io"
USER_AGENT = "rulecam-api/1.0 (+https://github.com/jeevithg090/Axi0N_Meta)"
LIVE = frozenset({"RUNNING", "STARTING", "PROVISIONING"})
DEFAULT_PODS = ("rulecam-glimmer", "rulecam-sam")
RETRY_S = 300.0
TIMEOUT_S = 20.0
PAGE_LIMIT = 1000


class RunPodError(RulecamError):
    """A RunPod REST call failed (the message never contains the key)."""


def runpod_client(
    key: str,
    *,
    base_url: str = API_BASE,
    timeout_s: float = TIMEOUT_S,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """An httpx client for the RunPod REST API: Bearer key and a non-Python User-Agent.

    Args:
        key: The RunPod API key (sent only to `base_url`).
        base_url: The REST API.
        timeout_s: Per-request timeout.
        transport: httpx transport (a `MockTransport` in tests).

    Returns:
        The client; close it after use.
    """
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=timeout_s,
        transport=transport,
    )


def runpod_call(
    client: httpx.Client, method: str, path: str, body: Mapping[str, Any] | None = None
) -> Any:
    """Send one REST call and return its JSON (`None` for an empty body).

    Raises:
        RunPodError: No answer or an HTTP error; the message names the method, the path without
            its query and the status, never the key.
    """
    try:
        response = client.request(method, path, json=body)
    except httpx.HTTPError as exc:
        msg = f"{method} {path.split('?', maxsplit=1)[0]}: {type(exc).__name__}"
        raise RunPodError(msg) from None
    if response.status_code >= 400:  # noqa: PLR2004 - HTTP error range
        msg = f"{method} {path.split('?', maxsplit=1)[0]}: HTTP {response.status_code}"
        raise RunPodError(msg)
    return response.json() if response.content else None


class PodStopper(Protocol):
    """Stops pods by name or id and returns the ids it stopped."""

    def stop(self, pods: Sequence[str]) -> list[str]:
        """Stop the live pods among `pods`."""
        ...


class RunPodPods:
    """The two RunPod REST calls the idle stop needs: list pods, stop one."""

    def __init__(
        self,
        key_file: Path,
        *,
        base_url: str = API_BASE,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Create the client.

        Args:
            key_file: File holding the RunPod API key (mode 600, on the volume).
            base_url: The REST API.
            transport: httpx transport (a `MockTransport` in tests).
        """
        self._key_file = key_file
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def _key(self) -> str:
        try:
            key = self._key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            msg = f"cannot read the RunPod key file {self._key_file} ({type(exc).__name__})"
            raise RunPodError(msg) from None
        if not key:
            msg = f"the RunPod key file {self._key_file} is empty"
            raise RunPodError(msg)
        return key

    def _client(self) -> httpx.Client:
        return runpod_client(self._key(), base_url=self._base_url, transport=self._transport)

    def _pods(self, client: httpx.Client) -> list[dict[str, Any]]:
        pods: list[dict[str, Any]] = []
        cursor = ""
        while True:
            query = f"?limit={PAGE_LIMIT}" + (f"&cursor={cursor}" if cursor else "")
            page = runpod_call(client, "GET", "/v2/pods" + query) or {}
            pods.extend(page.get("pods") or [])
            pagination = page.get("pagination") or {}
            cursor = str(pagination.get("nextCursor") or "")
            if not pagination.get("hasNextPage") or not cursor:
                return pods

    def stop(self, pods: Sequence[str]) -> list[str]:
        """Stop every live pod whose name or id is in `pods`.

        Returns:
            The ids stopped (a pod already stopped is left alone).

        Raises:
            RunPodError: The key is missing or a call failed.
        """
        wanted = set(pods)
        with self._client() as client:
            targets = [
                str(p["id"])
                for p in self._pods(client)
                if (p.get("name") in wanted or p.get("id") in wanted) and p.get("status") in LIVE
            ]
            for pod_id in targets:
                runpod_call(client, "POST", f"/v2/pods/{pod_id}/action", {"action": "stop"})
        return targets


class IdleStopStatus(Frozen):
    """`/runtime/metrics` → `cost.idle_stop`.

    Attributes:
        enabled: The idle stop is armed (a delay and a key file are set).
        after_s: Idle seconds before the pods are stopped.
        pods: Pod names or ids it stops.
        idle_s: How long no camera has had an active plan (0 while one has).
        stopped: The pods were stopped in this idle period.
        stopped_at: When.
        last_error: The last failed attempt, if any.
    """

    enabled: bool
    after_s: float = Field(default=0.0, ge=0)
    pods: tuple[str, ...] = ()
    idle_s: float = 0.0
    stopped: bool = False
    stopped_at: datetime | None = None
    last_error: str | None = None


class IdleGpuStop:
    """The idle timer: stops the GPU pods once after `after_s` with no active camera."""

    def __init__(
        self,
        stopper: PodStopper | None,
        *,
        after_s: float,
        pods: Sequence[str] = DEFAULT_PODS,
        retry_s: float = RETRY_S,
    ) -> None:
        """Create the timer.

        Args:
            stopper: Stops pods (`RunPodPods`); `None` disables the idle stop.
            after_s: Idle seconds before stopping; 0 disables it.
            pods: Pod names or ids to stop.
            retry_s: Wait after a failed attempt before trying again.
        """
        self._stopper = stopper
        self._after_s = after_s
        self._pods = tuple(pods)
        self._retry_s = retry_s
        self._idle_since: datetime | None = None
        self._stopped_at: datetime | None = None
        self._failed_at: datetime | None = None
        self._error: str | None = None
        self._idle_s = 0.0

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.BaseTransport | None = None
    ) -> IdleGpuStop:
        """Build from `RULECAM_IDLE_STOP_GPU_*` and `RULECAM_RUNPOD_API_KEY_FILE`."""
        after = settings.rulecam_idle_stop_gpu_after_s
        key_file = settings.rulecam_runpod_api_key_file
        pods = tuple(p.strip() for p in settings.rulecam_idle_stop_gpu_pods.split(",") if p.strip())
        stopper = None
        if after > 0 and key_file is None:
            _log.error("idle_stop_disabled", reason="RULECAM_RUNPOD_API_KEY_FILE is not set")
        elif after > 0 and key_file is not None:
            stopper = RunPodPods(key_file, transport=transport)
            _log.info("idle_stop_armed", after_s=after, pods=list(pods))
        return cls(stopper, after_s=after, pods=pods)

    @property
    def enabled(self) -> bool:
        """Whether the idle stop is armed."""
        return self._stopper is not None and self._after_s > 0 and bool(self._pods)

    def status(self) -> IdleStopStatus:
        """What the timer is doing."""
        return IdleStopStatus(
            enabled=self.enabled,
            after_s=self._after_s,
            pods=self._pods,
            idle_s=round(self._idle_s, 1),
            stopped=self._stopped_at is not None,
            stopped_at=self._stopped_at,
            last_error=self._error,
        )

    def tick(self, now: datetime, active: Collection[str], *, busy: bool = False) -> list[str]:
        """Advance the timer; stop the pods when it runs out.

        Args:
            now: The current time.
            active: Cameras with an active plan and a recent chunk.
            busy: Work still needs the pods (candidates waiting for Tier 1): counts as activity.

        Returns:
            Pod ids stopped by this tick.
        """
        if active or busy:
            if self._stopped_at is not None:
                _log.warning("idle_stop_activity_resumed", pods=list(self._pods))
            self._idle_since = self._stopped_at = self._failed_at = None
            self._idle_s = 0.0
            return []
        if self._idle_since is None:
            self._idle_since = now
        self._idle_s = (now - self._idle_since).total_seconds()
        if not self.enabled or self._stopped_at is not None or self._idle_s < self._after_s:
            return []
        if self._failed_at is not None and (now - self._failed_at).total_seconds() < self._retry_s:
            return []
        return self._stop(now)

    def _stop(self, now: datetime) -> list[str]:
        if self._stopper is None:
            return []
        try:
            stopped = self._stopper.stop(self._pods)
        except RunPodError as exc:
            self._failed_at, self._error = now, str(exc)
            _log.warning("idle_stop_failed", error=str(exc), retry_s=self._retry_s)
            return []
        self._stopped_at, self._error = now, None
        _log.warning("idle_stop_gpu_pods", idle_s=round(self._idle_s), stopped=stopped)
        return stopped
