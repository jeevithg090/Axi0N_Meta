"""Read-only checks of the deployed API (marked `live`; run with `uv run pytest -m live tests/e2e`).

Every request is a GET or an OPTIONS preflight without credentials, paced at about 3 per second:
the live pod is never written to and never loaded. `RULECAM_E2E_API` / `RULECAM_E2E_UI` pick the
deployment (defaults: the hackathon stack).
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx
import pytest

from tests.e2e.conftest import dotenv_secrets
from tests.e2e.surface import PUBLIC, fill, leaks, spec_operations

pytestmark = pytest.mark.live

PACE_S = 0.3
EVIL_ORIGIN = "https://evil.example"


@pytest.fixture(scope="module")
def api(live_api: str) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=live_api, timeout=30, headers={"User-Agent": "rulecam-qa-e2e"}
    ) as client:
        yield client


@pytest.fixture(scope="module")
def spec(api: httpx.Client) -> dict[str, object]:
    reply = api.get("/openapi.json")
    assert reply.status_code == 200
    return reply.json()  # type: ignore[no-any-return]


def _get(api: httpx.Client, path: str, **kwargs: object) -> httpx.Response:
    time.sleep(PACE_S)
    return api.get(path, **kwargs)  # type: ignore[arg-type]


def _peek(api: httpx.Client, path: str, limit: int = 65_536, max_s: float = 5.0) -> tuple[int, str]:
    """Status and the first `limit` bytes, so an event stream cannot hang the check."""
    time.sleep(PACE_S)
    data = b""
    started = time.monotonic()
    with api.stream("GET", path) as reply:
        for chunk in reply.iter_bytes():
            data += chunk
            if len(data) >= limit or time.monotonic() - started > max_s:
                break
        return reply.status_code, data[:limit].decode("utf-8", errors="replace")


def test_health_is_ok_and_names_its_revision(api: httpx.Client) -> None:
    health = _get(api, "/health").json()
    assert health["ok"] is True
    assert health["revision"], "the deployed API should say which commit it runs"
    assert health["mode"] in {"live", "replay", "record"}


def test_every_owner_read_refuses_a_visitor(api: httpx.Client, spec: dict[str, object]) -> None:
    open_routes = []
    for method, path in spec_operations(spec):
        if method != "GET" or (method, path) in PUBLIC:
            continue
        status, _ = _peek(api, fill(path))
        if status != 403:
            open_routes.append(f"GET {path} -> {status}")
    assert open_routes == []


def test_no_public_read_crashes_or_leaks(api: httpx.Client, spec: dict[str, object]) -> None:
    secrets = list(dotenv_secrets().values())
    problems = []
    for method, path in spec_operations(spec):
        if method != "GET":
            continue
        status, text = _peek(api, fill(path))
        if status >= 500 and status != 503:
            problems.append(f"GET {path} -> {status}")
        if found := leaks(text, secrets):
            problems.append(f"GET {path} leaks {found}")
    for path in ("/metrics", "/openapi.json", "/no-such-route"):
        if found := leaks(_peek(api, path, limit=1_000_000)[1], secrets):
            problems.append(f"GET {path} leaks {found}")
    assert problems == []


def test_no_cors_grant_to_a_foreign_origin(api: httpx.Client) -> None:
    for path in ("/mode", "/health", "/evidence/days"):
        reply = _get(api, path, headers={"Origin": EVIL_ORIGIN})
        assert reply.headers.get("access-control-allow-origin") not in {"*", EVIL_ORIGIN}
    time.sleep(PACE_S)
    preflight = api.options(
        "/mode", headers={"Origin": EVIL_ORIGIN, "Access-Control-Request-Method": "PUT"}
    )
    assert preflight.headers.get("access-control-allow-origin") not in {"*", EVIL_ORIGIN}


def test_every_evidence_day_verifies(api: httpx.Client) -> None:
    days = _get(api, "/evidence/days").json()
    assert days, "no evidence days on the live stack"
    for day in days[-3:]:
        report = _get(api, f"/evidence/{day}/verify").json()
        hashes = _get(api, f"/evidence/{day}/hashes").json()
        root = _get(api, f"/evidence/{day}/root").json()
        assert report["ok"] is True, (day, report)
        assert report["n"] == hashes["n"] == root["n"] == len(hashes["hashes"]), day
        assert _get(api, f"/evidence/{day}/chain").status_code == 403


def test_ui_rewrite_reaches_the_same_api(api: httpx.Client, live_ui: str) -> None:
    """The badge the UI shows comes from the same API, through the same-origin rewrite."""
    direct = _get(api, "/mode").json()
    time.sleep(PACE_S)
    via_ui = httpx.get(f"{live_ui}/rulecam-api/mode", timeout=30).json()
    assert via_ui == direct


def test_owner_login_is_available_for_the_presenter(api: httpx.Client) -> None:
    """docs/DEMO.md has the presenter log in on /login; that needs the hash and secret set."""
    me = _get(api, "/auth/me").json()
    assert me["owner"] is False
    assert me["login_enabled"] is True, "OWNER_PASSPHRASE_HASH / SESSION_SECRET unset on the pod"
