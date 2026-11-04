"""Demo preflight of the three use cases against the live API: green / yellow / red with latencies.

    uv run python scripts/ops/preflight_usecases.py [--api URL] [--skip-models]

Checks, in order (docs/demo-day.md):

- **api**: `GET /health` (revision, GPU worker states) and `GET /runtime/metrics` (Tier 2
  configured, Prompt Guard firewall on, Tier 1 circuit closed).
- **supermarket / Ask**: one question through `POST /ask/stream` (Muse Spark 1.3 with SQL, SAM 3.1
  and Glimmer tools); prints the models used and any Spark Open reviews. `/retail/overview` too
  when that route is deployed.
- **warehouse / verify**: `/warehouse/near-misses` when deployed, then the runtime's verification
  chain on a real warehouse strip: Muse Glimmer Tier 1 and Muse Spark Open Tier 2, through the
  same client code the API runs (needs GLIMMER_* / SPARK_OPEN_* in the environment, as on the API
  pod; skipped with `--skip-models`).
- **crowd / assess**: `GET /crowd/status`, then `POST /crowd/scan` (SAM 3.1 density, each episode
  checked) over the 10 minutes before the newest chunk of the freshest camera.

Owner routes need the API's own machine (run it on the API pod against http://localhost:8000) or
`RULECAM_OWNER_PASSPHRASE` in the environment (it logs in like `rulecam eval ask`). Exit status:
0 when nothing is red, 1 otherwise. A yellow line is a route not deployed or nothing to check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model_proofs  # sibling script: the shared strip, rule and candidate

GREEN, YELLOW, RED, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[0m"
DEFAULT_API = os.environ.get("RULECAM_API_URL", "https://rulecam.vercel.app/rulecam-api")
ASK_QUESTION = "How many people did the cameras see in the last hour, and when was it busiest?"
HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_UNAVAILABLE = 503
TIMEOUT_S = 300.0


@dataclass(frozen=True)
class Result:
    """One check's outcome."""

    name: str
    status: str  # "green", "yellow" or "red"
    seconds: float
    detail: str


class Api:
    """The live API, logged in as the owner when a passphrase is given."""

    def __init__(self, base: str, passphrase: str | None) -> None:
        """Connect (and log in when `passphrase` is set)."""
        parts = urlsplit(base)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.http = httpx.Client(base_url=base, timeout=httpx.Timeout(TIMEOUT_S, connect=15.0))
        self.headers: dict[str, str] = {}
        if passphrase:
            reply = self.http.post(
                "/auth/login", json={"passphrase": passphrase}, headers={"Origin": self.origin}
            )
            reply.raise_for_status()
            token = str(reply.json().get("csrf_token") or "")
            self.headers = {"Origin": self.origin, "X-CSRF-Token": token}

    def get(self, path: str) -> httpx.Response:
        """GET with the owner headers."""
        return self.http.get(path, headers=self.headers)

    def post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        """POST JSON with the owner headers."""
        return self.http.post(path, json=body, headers=self.headers)


def timed(name: str, check: Callable[[], tuple[str, str]]) -> Result:
    """Run one check; any exception is red."""
    started = time.perf_counter()
    try:
        status, detail = check()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = " (owner route: set RULECAM_OWNER_PASSPHRASE or run on the API pod)"
        status = "red"
        detail = f"HTTP {code} {exc.request.url.path}" + (hint if code in {401, 403} else "")
    except Exception as exc:  # a preflight reports, it does not crash
        status, detail = "red", f"{type(exc).__name__}: {str(exc)[:160]}"
    return Result(name, status, time.perf_counter() - started, detail)


def check_health(api: Api) -> tuple[str, str]:
    """`/health`: ok, revision and the GPU states."""
    body = api.get("/health").json()
    gpus = body.get("gpus") or {}
    states = ", ".join(f"{k}={v.get('state')}" for k, v in gpus.items())
    status = "green" if body.get("ok") and all(v.get("up") for v in gpus.values()) else "red"
    return status, f"revision {str(body.get('revision'))[:12]}, {states}"


def check_runtime(api: Api) -> tuple[str, str]:
    """`/runtime/metrics`: Tier 2, firewall and the Tier 1 circuit."""
    reply = api.get("/runtime/metrics")
    if reply.status_code != HTTP_OK:
        return "yellow", f"/runtime/metrics HTTP {reply.status_code}"
    body = reply.json()
    response = body.get("response") or {}
    verifier = (body.get("verifier") or {}).get("state", "?")
    tier2, firewall = response.get("tier2", "?"), response.get("firewall", "?")
    status = "green" if tier2 == "configured" and firewall == "on" else "yellow"
    if verifier == "open":
        status = "red"
    return (
        status,
        f"tier2 (Spark Open) {tier2}, firewall (Prompt Guard 2) {firewall}, tier1 {verifier}",
    )


def answer_of(reply: httpx.Response) -> dict[str, Any] | None:
    """The `answer` event of an Ask SSE stream."""
    event = ""
    for raw in reply.iter_lines():
        if raw.startswith("event:"):
            event = raw[6:].strip()
        elif raw.startswith("data:") and event in {"answer", "error"}:
            data = json.loads(raw[5:])
            if event == "error":
                raise RuntimeError(str(data.get("detail") or data)[:200])
            answer = data.get("answer")
            return answer if isinstance(answer, dict) else None
    return None


def check_ask(api: Api) -> tuple[str, str]:
    """One Ask question, streamed."""
    body = {"question": ASK_QUESTION, "numbers": []}
    headers = {"Accept": "text/event-stream", **api.headers}
    with api.http.stream("POST", "/ask/stream", json=body, headers=headers) as reply:
        if reply.status_code in {HTTP_NOT_FOUND, HTTP_UNAVAILABLE}:
            return "yellow", f"Ask not available (HTTP {reply.status_code})"
        reply.raise_for_status()
        answer = answer_of(reply)
    if answer is None:
        return "red", "the stream ended without an answer"
    reviews = answer.get("reviews") or []
    review = ", ".join(f"{r.get('model')}:{r.get('verdict')}" for r in reviews) or "no review"
    models = ", ".join(answer.get("models") or [])
    return "green", f"{len(answer.get('steps') or [])} steps; models {models}; {review}"


def check_route(api: Api, path: str) -> tuple[str, str]:
    """A read route of a use case that may not be deployed yet."""
    reply = api.get(path)
    if reply.status_code == HTTP_NOT_FOUND:
        return "yellow", f"{path} not deployed"
    status = "green" if reply.status_code == HTTP_OK else "red"
    return status, f"{path} HTTP {reply.status_code}, {len(reply.content)} bytes"


def check_verify_chain(tier2: bool) -> tuple[str, str]:
    """Glimmer Tier 1 and Spark Open Tier 2 on a real warehouse strip, as the runtime runs them."""
    from rulecam.clients.open_weights.glimmer import GlimmerClient  # noqa: PLC0415
    from rulecam.clients.open_weights.spark_open import SparkOpenClient  # noqa: PLC0415
    from rulecam.clock import SystemClock  # noqa: PLC0415
    from rulecam.config import Settings  # noqa: PLC0415
    from rulecam.replay.cache import ReplayCache  # noqa: PLC0415
    from rulecam.verify.tier1 import GlimmerVerifier  # noqa: PLC0415
    from rulecam.verify.tier2 import SparkOpenVerifier  # noqa: PLC0415

    settings, clock = Settings(), SystemClock()
    cache = ReplayCache(Path("/tmp/rulecam-preflight-replay"), "live", clock=clock)  # noqa: S108
    rule = model_proofs.hard_hat_rule()
    candidate = model_proofs.candidate_for(model_proofs.DEFAULT_STRIP)
    glimmer = GlimmerVerifier(GlimmerClient.from_settings(settings, cache=cache, clock=clock))
    t1 = glimmer.verify(candidate, rule)
    if t1.reason.startswith("verifier_error"):
        return "red", f"Glimmer: {t1.reason[:150]}"
    detail = f"Glimmer {t1.verdict} ({t1.latency_ms} ms)"
    if not tier2 or not settings.spark_open_configured:
        return "yellow", detail + "; Spark Open Tier 2 not configured"
    spark = SparkOpenVerifier(SparkOpenClient.from_settings(settings, cache=cache, clock=clock))
    t2 = spark.verify(candidate, rule, t1)
    if t2.reason.startswith("Spark Open could not"):
        return "red", detail + f"; {t2.reason[:120]}"
    return "green", detail + f"; Spark Open {t2.verdict} ({t2.latency_ms} ms)"


def freshest_camera(api: Api) -> tuple[str, datetime] | None:
    """The camera with the newest chunk and that chunk's time."""
    rows = api.get("/cameras").json()
    seen = [(r["camera_id"], r["last_chunk_at"]) for r in rows if r.get("last_chunk_at")]
    if not seen:
        return None
    camera, at = max(seen, key=lambda r: r[1])
    return camera, datetime.fromisoformat(at.replace("Z", "+00:00"))


def check_crowd(api: Api) -> tuple[str, str]:
    """`/crowd/status`, then a 10-minute `/crowd/scan` with episode checks."""
    status_reply = api.get("/crowd/status")
    if status_reply.status_code in {HTTP_NOT_FOUND, HTTP_UNAVAILABLE}:
        return "yellow", f"crowd watch not available (HTTP {status_reply.status_code})"
    status_reply.raise_for_status()
    found = freshest_camera(api)
    if found is None:
        return "yellow", "no camera has video to scan"
    camera, last = found
    body = {
        "camera_id": camera,
        "start": (last - timedelta(minutes=10)).isoformat(),
        "end": last.isoformat(),
        "at_least": "crowded",
        "verify": True,
    }
    reply = api.post("/crowd/scan", body)
    if reply.status_code != HTTP_OK:
        return "red", f"/crowd/scan HTTP {reply.status_code}: {reply.text[:150]}"
    report = reply.json()
    episodes = report.get("episodes") or []
    return "green", (
        f"{camera}: {report.get('keyframes')} keyframes, {len(episodes)} episodes "
        f"(levels {json.dumps(report.get('levels'))[:80]})"
    )


def main() -> None:
    """Run the checks and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--skip-models", action="store_true", help="no direct Glimmer/Spark calls")
    parser.add_argument("--models-only", action="store_true", help="only the verify chain")
    parser.add_argument("--no-tier2", action="store_true", help="Glimmer only in the verify chain")
    args = parser.parse_args()
    api = Api(args.api.rstrip("/"), os.environ.get("RULECAM_OWNER_PASSPHRASE"))
    checks: list[tuple[str, Callable[[], tuple[str, str]]]] = [
        ("api / health", lambda: check_health(api)),
        ("api / models", lambda: check_runtime(api)),
        ("supermarket / ask", lambda: check_ask(api)),
        ("supermarket / retail", lambda: check_route(api, "/retail/overview")),
        ("warehouse / near misses", lambda: check_route(api, "/warehouse/near-misses")),
        ("crowd / assess", lambda: check_crowd(api)),
    ]
    chain = ("warehouse / verify chain", lambda: check_verify_chain(not args.no_tier2))
    if args.models_only:
        checks = [chain]
    elif not args.skip_models:
        checks.insert(5, chain)
    colour = {"green": GREEN, "yellow": YELLOW, "red": RED}
    red = False
    for name, check in checks:
        result = timed(name, check)
        red |= result.status == "red"
        sys.stdout.write(
            f"{colour[result.status]}{result.status.upper():6s}{RESET} {name:26s} "
            f"{result.seconds:7.1f} s  {result.detail}\n"
        )
        sys.stdout.flush()
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
