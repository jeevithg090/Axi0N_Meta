"""Every route of the full local API: owner routes refuse visitors, nothing crashes or leaks.

The app is the one `uvicorn rulecam.api.app:app` serves, built from settings (see conftest.py),
so a router mounted later by any vertical (crowd, shelf, ask, extensions, ...) is covered the day
it lands: a new route is owner-only unless it is added to `surface.PUBLIC` with a reason.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from rulecam.api.auth import SESSION_COOKIE
from tests.e2e.conftest import UI_ORIGIN, canary_values
from tests.e2e.surface import (
    FRAMEWORK,
    PUBLIC,
    PUBLIC_PENDING,
    RouteInfo,
    app_routes,
    fill,
    leaks,
)

EVIL_ORIGIN = "https://evil.example"


def _http_routes(app: FastAPI) -> list[RouteInfo]:
    return [r for r in app_routes(app) if r.method != "WS" and r.path not in FRAMEWORK]


def _owner_routes(app: FastAPI) -> list[RouteInfo]:
    public = PUBLIC.keys() | PUBLIC_PENDING.keys()
    return [r for r in _http_routes(app) if (r.method, r.path) not in public]


def _crashed(status: int, text: str) -> bool:
    """A 5xx is a crash, unless it is a deliberate 501/503 (a service this deployment lacks)."""
    return status >= 500 and not (status in {501, 503} and text.startswith('{"detail":'))


STREAMING = 0  # the status `_call` reports for a stream that was still open after the timeout


def _call(client: TestClient, route: RouteInfo, **kwargs: Any) -> tuple[int, str]:
    """One request; an event stream gets `STREAM_TIMEOUT_S` (TestClient reads the whole body)."""
    body: dict[str, Any] = {"json": {}} if route.method in {"POST", "PUT", "PATCH"} else {}
    if not route.streams:
        reply = client.request(route.method, fill(route.path), **{**body, **kwargs})
        return reply.status_code, reply.text
    done: list[tuple[int, str]] = []

    def request() -> None:
        reply = client.request(route.method, fill(route.path), **{**body, **kwargs})
        done.append((reply.status_code, reply.text))

    worker = threading.Thread(target=request, daemon=True)
    worker.start()
    worker.join(STREAM_TIMEOUT_S)
    return done[0] if done else (STREAMING, "")


STREAM_TIMEOUT_S = 5.0


def test_the_allowlist_names_only_routes_that_exist(local_app: FastAPI) -> None:
    served = {(r.method, r.path) for r in _http_routes(local_app)}
    assert set(PUBLIC) - served == set(), "stale entries in surface.PUBLIC"


def test_every_owner_route_refuses_a_visitor(local_app: FastAPI, visitor: TestClient) -> None:
    open_routes = []
    for route in _owner_routes(local_app):
        status, text = _call(visitor, route)
        if status != 403:
            open_routes.append(f"{route.method} {route.path} -> {status} {text[:80]}")
    assert open_routes == [], "owner routes answering a visitor (add auth, or to PUBLIC)"


def test_every_owner_route_refuses_a_forged_session(
    local_app: FastAPI, visitor: TestClient
) -> None:
    forged = "eyJzaWQiOiJ4IiwiY3NyZiI6IngiLCJleHAiOjk5OTk5OTk5OTl9." + "A" * 43
    visitor.cookies.set(SESSION_COOKIE, forged)
    try:
        refused = [_call(visitor, r)[0] for r in _owner_routes(local_app)]
    finally:
        visitor.cookies.clear()
    assert set(refused) == {403}


def test_a_web_page_cannot_drive_the_owners_loopback(local_app: FastAPI) -> None:
    """Any site the owner visits can make their browser call localhost: Origin must be checked."""
    with TestClient(
        local_app,
        base_url="http://localhost:8000",
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    ) as loopback:
        writes = [r for r in _owner_routes(local_app) if r.method != "GET"]
        statuses = {
            f"{r.method} {r.path}": _call(loopback, r, headers={"Origin": EVIL_ORIGIN})[0]
            for r in writes
        }
    assert {k: v for k, v in statuses.items() if v != 403} == {}


def test_owner_writes_need_the_ui_origin_and_csrf(local_app: FastAPI, owner: TestClient) -> None:
    writes = [r for r in _owner_routes(local_app) if r.method != "GET"]
    assert writes, "no owner write routes found"
    no_csrf = {"X-CSRF-Token": "", "Origin": UI_ORIGIN}
    bad_origin = {"Origin": EVIL_ORIGIN}
    accepted = []
    for route in writes:
        for headers in (no_csrf, bad_origin):
            status, text = _call(owner, route, headers=headers)
            if status != 403:
                accepted.append(f"{route.method} {route.path} {headers} -> {status} {text[:60]}")
    assert accepted == []


def test_the_owner_session_reaches_owner_reads(local_app: FastAPI, owner: TestClient) -> None:
    """The positive control: a real session is not refused (so the 403s above mean something)."""
    reads = [r for r in _owner_routes(local_app) if r.method == "GET" and not r.streams]
    refused = [r.path for r in reads if _call(owner, r)[0] == 403]
    assert refused == []


def test_no_route_crashes_or_leaks_for_a_visitor(local_app: FastAPI, visitor: TestClient) -> None:
    problems = []
    for route in _http_routes(local_app):
        status, text = _call(visitor, route)
        found = leaks(text, canary_values())
        if _crashed(status, text):
            problems.append(f"{route.method} {route.path} -> {status}")
        if found:
            problems.append(f"{route.method} {route.path} leaks {found}")
    assert problems == []


def test_no_owner_read_leaks_a_secret(local_app: FastAPI, owner: TestClient) -> None:
    """Even the owner never gets a key, token, webhook URL or passphrase back."""
    problems = []
    for route in _http_routes(local_app):
        if route.method != "GET" or route.streams:  # the owner's own endless event streams
            continue
        status, text = _call(owner, route)
        if _crashed(status, text):
            problems.append(f"GET {route.path} -> {status} {text[:80]}")
        if found := leaks(text, canary_values()):
            problems.append(f"GET {route.path} leaks {found}")
    assert problems == []


def test_the_openapi_and_docs_carry_no_secret(visitor: TestClient) -> None:
    for path in sorted(FRAMEWORK - {"/docs/oauth2-redirect"}):
        reply = visitor.get(path)
        assert reply.status_code == 200
        assert leaks(reply.text, canary_values()) == [], path


BAD_SEGMENTS = [
    "..%2f..%2fpyproject.toml",
    "%2e%2e%2f%2e%2e%2f.env",
    "x" * 2048,
    "%00",
    "%F0%9F%98%80",
    "<script>alert(1)</script>",
    "'%20OR%201=1--",
]


@pytest.mark.parametrize("segment", BAD_SEGMENTS)
def test_public_routes_reject_bad_path_values_cleanly(
    local_app: FastAPI, visitor: TestClient, segment: str
) -> None:
    problems = []
    for method, path in sorted(PUBLIC):
        if "{" not in path:
            continue
        reply = visitor.request(method, fill(path, segment), json={} if method == "POST" else None)
        if _crashed(reply.status_code, reply.text) or leaks(reply.text, canary_values()):
            problems.append(f"{method} {path} -> {reply.status_code}")
        if "<script>alert(1)</script>" in reply.text and "html" in reply.headers.get(
            "content-type", ""
        ):
            problems.append(f"{method} {path} reflects markup unescaped")
        assert "[project]" not in reply.text
        assert "META_API_KEY" not in reply.text
    assert problems == []


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"not json", "application/json"),
        (b"[]", "application/json"),
        (b'{"a":' * 500 + b"1" + b"}" * 500, "application/json"),
        (b"\xff\xfe\x00garbage", "application/octet-stream"),
        (b"x" * 2_000_000, "application/json"),
    ],
    ids=["not-json", "array", "deeply-nested", "binary", "2MB"],
)
def test_public_writes_reject_bad_bodies_cleanly(
    visitor: TestClient, body: bytes, content_type: str
) -> None:
    problems = []
    for method, path in sorted(PUBLIC):
        if method != "POST":
            continue
        reply = visitor.post(fill(path), content=body, headers={"Content-Type": content_type})
        if _crashed(reply.status_code, reply.text) or leaks(reply.text, canary_values()):
            problems.append(f"POST {path} -> {reply.status_code} {reply.text[:80]}")
    assert problems == []


def test_static_verify_mount_does_not_traverse(visitor: TestClient) -> None:
    for path in (
        "/verify/../pyproject.toml",
        "/verify/%2e%2e/%2e%2e/pyproject.toml",
        "/verify/..%2f..%2f.env",
        "/verify/%2e%2e%2f%2e%2e%2fsrc/rulecam/config.py",
    ):
        reply = visitor.get(path)
        assert reply.status_code in {400, 404}, (path, reply.status_code)
        assert "[project]" not in reply.text
        assert "BaseSettings" not in reply.text


def test_unknown_routes_and_methods_answer_plainly(visitor: TestClient) -> None:
    assert visitor.get("/no-such-route").json() == {"detail": "Not Found"}
    reply = visitor.delete("/health")
    assert reply.status_code == 405
    assert leaks(reply.text) == []


def test_no_cors_grant_to_a_foreign_origin(local_app: FastAPI, visitor: TestClient) -> None:
    """The browser reaches the API through the UI's same-origin rewrite; the API grants no CORS."""
    granted = []
    for route in _http_routes(local_app):
        url = fill(route.path)
        preflight = visitor.options(
            url,
            headers={"Origin": EVIL_ORIGIN, "Access-Control-Request-Method": route.method},
        )
        simple = (
            visitor.get(url, headers={"Origin": EVIL_ORIGIN})
            if route.method == "GET" and not route.streams
            else None
        )
        for reply in (preflight, simple):
            if reply is None:
                continue
            allow = reply.headers.get("access-control-allow-origin")
            if allow in {"*", EVIL_ORIGIN}:
                granted.append(f"{route.method} {route.path}: {allow}")
    assert granted == []


def test_bus_websocket_refuses_visitors(visitor: TestClient) -> None:
    for headers in ({}, {"Origin": EVIL_ORIGIN}, {"Origin": UI_ORIGIN}):
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            visitor.websocket_connect("/ws", headers=headers) as socket,
        ):
            socket.receive_json()
        assert closed.value.code == 1008


def test_public_evidence_routes_never_return_payloads(visitor: TestClient) -> None:
    day = visitor.get("/evidence/days").json()[-1]
    for part in ("hashes", "root", "verify"):
        reply = visitor.get(f"/evidence/{day}/{part}")
        assert reply.status_code == 200, part
        assert "lead" not in reply.text
        assert "seeded by the e2e suite" not in reply.text
    assert visitor.get(f"/evidence/{day}/verify").json()["ok"] is True
    assert visitor.get(f"/evidence/{day}/chain").status_code == 403


def test_login_is_rate_limited_and_never_echoes_the_passphrase(visitor: TestClient) -> None:
    statuses = []
    for _ in range(7):
        reply = visitor.post(
            "/auth/login",
            json={"passphrase": "wrong guess 0123456789"},
            headers={"Origin": UI_ORIGIN},
        )
        statuses.append(reply.status_code)
        assert "wrong guess" not in reply.text
    assert statuses[0] == 401
    assert 429 in statuses
