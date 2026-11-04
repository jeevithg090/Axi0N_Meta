"""The API's attack surface: every route, who may call it, and what no response may contain.

`PUBLIC` is the allowlist of operations a visitor may call without an owner session. Every other
HTTP route must answer 403 to a visitor. A new route that is public on purpose is added here with
the reason; a route that is public by accident fails `test_api_surface.py`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Final

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute, APIWebSocketRoute

Operation = tuple[str, str]  # (METHOD, path template)

PUBLIC: Final[dict[Operation, str]] = {
    ("GET", "/health"): "liveness, mode, revision and GPU state for the status panel",
    ("GET", "/mode"): "the LIVE/REPLAY badge every screen shows",
    ("GET", "/metrics"): "Prometheus gauges and counters (no payloads, no URLs)",
    ("GET", "/metrics/perception"): "SAM call and cost figures per camera",
    ("GET", "/runtime/metrics"): "runtime lag, verifier circuit and cost (no URLs or keys)",
    ("GET", "/cameras"): "camera ids, labels and last chunk time for the camera tiles",
    ("POST", "/auth/login"): "owner passphrase login (rate-limited)",
    ("POST", "/auth/logout"): "drops the caller's own session",
    ("GET", "/auth/me"): "whether the caller is the owner, and the CSRF token if so",
    ("GET", "/evidence/days"): "the days that have an evidence chain",
    ("GET", "/evidence/{day}/hashes"): "record hashes only, for the browser verify page",
    ("GET", "/evidence/{day}/root"): "the day's Merkle root",
    ("GET", "/evidence/{day}/verify"): "the chain report (ok, n, first bad record)",
    ("GET", "/advice"): "Site Advisor cards (stations, never people)",
    ("GET", "/advice/seals"): "sealed pre-registered checks of accepted advice",
    ("GET", "/advice/{advice_id}"): "one advice card",
    ("GET", "/pair/assets/{name}"): "the pairing page's static files (fixed allowlist)",
    ("GET", "/pair/{token}"): "the phone's pairing page; the token is the credential",
    ("POST", "/pair/{token}/claim"): "a phone claims its one-time token",
    ("POST", "/pair/{token}/chunk"): "a paired phone uploads video (needs the session header)",
    ("POST", "/worker/dispute"): "anonymous worker dispute (rate-limited, small body)",
    ("POST", "/worker/dispute/voice"): "anonymous spoken dispute",
    ("GET", "/worker/{plan_id}/{camera}"): "the worker's rules page (QR poster)",
    ("GET", "/worker/{plan_id}/{camera}/poster"): "the printable A4 poster",
    ("GET", "/proof/shared/{token}"): "a shared proof pack; the signed, expiring token is the key",
    ("GET", "/proof/shared/{token}/document"): "the shared proof pack as PDF/HTML",
    ("POST", "/warehouse/public/reports/voice"): (
        "anonymous spoken near-miss report from the worker page (rate-limited, audio not kept)"
    ),
}

# Public on purpose in branches not merged yet (QA checked them on the branch); once merged they
# move to PUBLIC, until then they are not reported as stale.
PUBLIC_PENDING: Final[dict[Operation, str]] = {}

# FastAPI's own documentation routes: public, and they list nothing the OpenAPI does not.
FRAMEWORK: Final = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})

# Values for path parameters that reach the handler (they match each route's pattern).
PATH_VALUES: Final[dict[str, str]] = {
    "day": "2026-11-04",
    "token": "qa-token-0123456789abcdef",
}

# What must never be in a response body: stack traces, local paths and credentials.
LEAK_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "python traceback": re.compile(r"Traceback \(most recent call last\)"),
    "python frame": re.compile(r'File "[^"]+\.py", line \d+'),
    "site-packages path": re.compile(r"site-packages/"),
    "home directory path": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    "RunPod key": re.compile(r"\brpa_[A-Za-z0-9]{30,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Meta/WhatsApp token": re.compile(r"\bEAA[A-Za-z0-9]{60,}"),
    "Telegram bot token": re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "passphrase hash": re.compile(r"\bscrypt:[A-Za-z0-9$:+/=_-]{20,}"),
    "bearer credential": re.compile(r"\bBearer [A-Za-z0-9._~+/-]{20,}"),
}


@dataclass(frozen=True)
class RouteInfo:
    """One HTTP operation or WebSocket the app serves."""

    method: str  # "GET", "POST", ... or "WS"
    path: str
    in_schema: bool
    streams: bool = False  # a server-sent event stream that may never end


def _streams(route: APIRoute) -> bool:
    """Whether `route` answers with a (possibly endless) stream, e.g. server-sent events."""
    if isinstance(route.response_class, type) and issubclass(
        route.response_class, StreamingResponse
    ):
        return True
    declared = str(route.responses) + str(getattr(route.endpoint, "__annotations__", {}))
    return "event-stream" in declared or "StreamingResponse" in declared


def _walk(routes: Iterable[object], prefix: str = "") -> Iterator[RouteInfo]:
    for route in routes:
        if isinstance(route, APIRoute):
            streams = _streams(route)
            for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
                yield RouteInfo(method, prefix + route.path, route.include_in_schema, streams)
        elif isinstance(route, APIWebSocketRoute):
            yield RouteInfo("WS", prefix + route.path, False)
        else:  # FastAPI >= 0.160 wraps included routers; older versions flatten them
            inner = getattr(route, "original_router", None)
            if inner is not None:
                context = getattr(route, "include_context", None)
                yield from _walk(inner.routes, prefix + (getattr(context, "prefix", "") or ""))


def app_routes(app: FastAPI) -> list[RouteInfo]:
    """Every operation and WebSocket `app` serves, including routes hidden from the OpenAPI."""
    return sorted(set(_walk(app.router.routes)), key=lambda r: (r.path, r.method))


def spec_operations(spec: dict[str, object]) -> list[Operation]:
    """Every (METHOD, path) in an OpenAPI document."""
    paths = spec.get("paths", {})
    assert isinstance(paths, dict)
    return sorted(
        (method.upper(), path)
        for path, ops in paths.items()
        for method in ops
        if method in {"get", "post", "put", "patch", "delete"}
    )


def fill(path: str, value: str = "x") -> str:
    """`path` with each `{param}` replaced by a value that matches its pattern."""
    return re.sub(r"\{(\w+)\}", lambda m: PATH_VALUES.get(m.group(1), value), path)


def leaks(text: str, canaries: Iterable[str] = ()) -> list[str]:
    """The names of the leak patterns (and canary secrets) found in `text`."""
    found = [name for name, pattern in LEAK_PATTERNS.items() if pattern.search(text)]
    found += [f"canary secret #{i}" for i, canary in enumerate(canaries) if canary in text]
    return found
