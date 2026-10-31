"""The API calls the eval runner makes, behind a protocol so tests use a fake.

`HttpApiClient` talks to a running Rulecam API exactly like the test feeder used on the pod:
owner calls (`POST /pair/token`, `POST /plans/requests`, `POST /plans/{id}/approve`) only work
from the API's own machine (loopback), so the runner runs there, against `http://localhost:8000`.
`WsBusListener` mirrors `/ws` (owner-only for the same reason) to timestamp alerts as the UI
would see them.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import ConfigDict

from rulecam.contracts import Frozen
from rulecam.eval.errors import EvalRunError
from rulecam.eval.record import BusFrame
from rulecam.log import get_logger

_log = get_logger(__name__)

DEFAULT_API = "http://localhost:8000"
HTTP_TIMEOUT_S = 60.0
SESSION_HEADER = "X-Pair-Session"
BUS_TOPICS = frozenset({"alert.new", "verdict.new", "candidate.new", "planner.step"})
_NOT_FOUND = 404
_CONFLICT = 409


class Pairing(Frozen):
    """A phone pairing the runner holds: token, server camera id and session key."""

    token: str
    camera_id: str
    session: str


class ChunkAck(Frozen):
    """What `POST /pair/{token}/chunk` answered (the fields the runner uses)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    chunk_id: str
    n_frames: int
    width: int
    height: int


class PlanRefusedError(EvalRunError):
    """`POST /plans/{id}/approve` answered 409: no rule passes the activation gate."""


class ApiClient(Protocol):
    """Everything the runner needs from the API."""

    def pair(self, label: str) -> Pairing:
        """Create a pairing token (owner) and claim it as a phone."""
        ...

    def upload(self, pairing: Pairing, chunk: Path) -> ChunkAck:
        """Upload one MP4 chunk as the paired phone."""
        ...

    def request_plan(self, text: str, lang: str) -> str:
        """Submit a text request to the Site Planner; return the plan id."""
        ...

    def plan(self, plan_id: str) -> dict[str, object] | None:
        """Return the finished plan, or `None` while the planner is still running."""
        ...

    def approve(self, plan_id: str) -> dict[str, object]:
        """Approve a plan; raise `PlanRefusedError` when nothing can be activated."""
        ...

    def runtime_metrics(self) -> dict[str, object]:
        """Return `/runtime/metrics`."""
        ...

    def chain(self, day: date) -> list[dict[str, object]]:
        """Return a UTC day's evidence-chain records (empty when there are none)."""
        ...


class HttpApiClient:
    """`ApiClient` over HTTP (httpx)."""

    def __init__(self, base_url: str = DEFAULT_API, *, http: httpx.Client | None = None) -> None:
        """Connect to the API at `base_url` (or use an injected httpx client)."""
        self._http = http or httpx.Client(base_url=base_url, timeout=HTTP_TIMEOUT_S)

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self._http.close()

    def _json(self, reply: httpx.Response, what: str) -> dict[str, object]:
        if reply.is_error:
            msg = f"{what}: HTTP {reply.status_code} {reply.text[:300]}"
            raise EvalRunError(msg)
        data = reply.json()
        if not isinstance(data, dict):
            msg = f"{what}: expected a JSON object"
            raise EvalRunError(msg)
        return data

    def pair(self, label: str) -> Pairing:
        """See `ApiClient.pair`."""
        token = str(self._json(self._http.post("/pair/token"), "pair token")["token"])
        claim = self._json(
            self._http.post(f"/pair/{token}/claim", json={"label": label}), "pair claim"
        )
        return Pairing(
            token=token, camera_id=str(claim["camera_id"]), session=str(claim["session"])
        )

    def upload(self, pairing: Pairing, chunk: Path) -> ChunkAck:
        """See `ApiClient.upload`."""
        reply = self._http.post(
            f"/pair/{pairing.token}/chunk",
            files={"file": (chunk.name, chunk.read_bytes(), "video/mp4")},
            headers={SESSION_HEADER: pairing.session},
        )
        return ChunkAck.model_validate(self._json(reply, f"upload {chunk.name}"))

    def request_plan(self, text: str, lang: str) -> str:
        """See `ApiClient.request_plan`."""
        body = {"items": [{"kind": "text", "lang": lang, "text": text}]}
        return str(
            self._json(self._http.post("/plans/requests", json=body), "plan request")["plan_id"]
        )

    def plan(self, plan_id: str) -> dict[str, object] | None:
        """See `ApiClient.plan`."""
        reply = self._http.get(f"/plans/{plan_id}")
        if reply.status_code == _NOT_FOUND:
            return None
        return self._json(reply, f"plan {plan_id}")

    def approve(self, plan_id: str) -> dict[str, object]:
        """See `ApiClient.approve`."""
        reply = self._http.post(f"/plans/{plan_id}/approve")
        if reply.status_code == _CONFLICT:
            raise PlanRefusedError(str(reply.json().get("detail", reply.text))[:500])
        return self._json(reply, f"approve {plan_id}")

    def runtime_metrics(self) -> dict[str, object]:
        """See `ApiClient.runtime_metrics`."""
        return self._json(self._http.get("/runtime/metrics"), "runtime metrics")

    def chain(self, day: date) -> list[dict[str, object]]:
        """See `ApiClient.chain`."""
        reply = self._http.get(f"/evidence/{day.isoformat()}/chain")
        if reply.status_code == _NOT_FOUND:
            return []
        if reply.is_error:
            msg = f"chain {day}: HTTP {reply.status_code}"
            raise EvalRunError(msg)
        return [json.loads(line) for line in reply.text.splitlines() if line.strip()]


class BusListener(Protocol):
    """Collects bus frames while a run plays."""

    def start(self) -> None:
        """Start listening."""
        ...

    def stop(self) -> list[BusFrame]:
        """Stop and return every frame received."""
        ...


class NoBus:
    """A listener that hears nothing (chain-only collection)."""

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> list[BusFrame]:
        """Return no frames."""
        return []


def ws_url(base_url: str) -> str:
    """Turn the API's http(s) base URL into its `/ws` URL."""
    scheme, _, rest = base_url.partition("://")
    return f"{'wss' if scheme == 'https' else 'ws'}://{rest.rstrip('/')}/ws"


class WsBusListener:
    """Mirrors `/ws` on a background thread, keeping frames of `BUS_TOPICS`."""

    def __init__(self, url: str, *, now: Callable[[], datetime] | None = None) -> None:
        """Listen to the `/ws` endpoint at `url` (see `ws_url`)."""
        self._url = url
        self._now = now or (lambda: datetime.now(UTC))
        self._frames: list[BusFrame] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Connect and start receiving (a failed connection is logged, not raised)."""
        self._thread = threading.Thread(target=self._run, name="eval-ws", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        from websockets.exceptions import WebSocketException  # noqa: PLC0415
        from websockets.sync.client import connect  # noqa: PLC0415

        try:
            with connect(self._url, open_timeout=10) as socket:
                while not self._stop.is_set():
                    try:
                        raw = socket.recv(timeout=0.5)
                    except TimeoutError:
                        continue
                    self._keep(raw)
        except (OSError, WebSocketException) as exc:
            _log.warning("eval_ws_unavailable", url=self._url, error=str(exc))

    def _keep(self, raw: str | bytes) -> None:
        try:
            frame = json.loads(raw)
        except ValueError:
            return
        if isinstance(frame, dict) and frame.get("topic") in BUS_TOPICS:
            payload = frame.get("payload")
            self._frames.append(
                BusFrame(
                    at=self._now(),
                    topic=str(frame["topic"]),
                    payload=payload if isinstance(payload, dict) else {},
                )
            )

    def stop(self) -> list[BusFrame]:
        """Stop the thread and return the frames."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return list(self._frames)
