"""Fixtures for the e2e checks: the full local API (as deployed, minus GPUs) and the live stack.

The local app is built by `services_from_settings`, exactly as `uvicorn rulecam.api.app:app`
builds it, with every route mounted, owner login on and canary values in the secret settings.
Nothing in it calls the network: no model is reached without a request that the owner makes.

Live checks are marked `live` (deselected by default) and read `RULECAM_E2E_API` (the API) and
`RULECAM_E2E_UI` (the web UI); both default to the hackathon deployment.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.api.app import create_app, services_from_settings
from rulecam.api.passphrase import hash_passphrase
from rulecam.config import Settings
from rulecam.evidence.chain import EventChain
from tests.fakes import FakeClock

UI_ORIGIN: Final = "https://rulecam.vercel.app"
PUBLIC_HOST: Final = "https://rulecam-api-8000.proxy.runpod.net"
VISITOR: Final = ("203.0.113.7", 40000)  # a browser on the internet, behind the RunPod proxy
PASSPHRASE: Final = "qa e2e owner passphrase for tests"  # noqa: S105 - a test passphrase
SESSION_SECRET: Final = "qa-e2e-session-secret-" + "s" * 32
DEFAULT_LIVE_API: Final = "https://7nv0ifjo2tmk00-8000.proxy.runpod.net"
DEFAULT_LIVE_UI: Final = "https://rulecam.vercel.app"
_OWNER_PORTS = itertools.count(41000)  # one client address per login: the limit is per client

# Each secret setting gets a unique value; no response may ever contain one of them.
CANARIES: Final[dict[str, str]] = {
    field: f"qa-canary-{field.replace('_', '-')}-7f3e9b2c"
    for field in (
        "meta_api_key",
        "baseten_api_key",
        "glimmer_api_key",
        "glimmer_edge_key",
        "sam_api_key",
        "sam_edge_key",
        "whatsapp_app_secret",
        "whatsapp_verify_token",
        "rulecam_webhook_secret",
        "ui_proxy_secret",
        "rulecam_owner_passphrase",
    )
}
WEBHOOK_URL_CANARY: Final = "https://hooks.example.invalid/qa-canary-webhook-url-7f3e9b2c"


@pytest.fixture(scope="session")
def local_app(tmp_path_factory: pytest.TempPathFactory) -> FastAPI:
    """The full API, configured like the deployment (owner login, public UI origin, chain)."""
    options: dict[str, Any] = {
        "rulecam_mode": "replay",
        "rulecam_data_dir": tmp_path_factory.mktemp("e2e-data"),
        "public_ui_url": UI_ORIGIN,
        "owner_passphrase_hash": hash_passphrase(PASSPHRASE, n=2**4, r=8, p=1),
        "session_secret": SESSION_SECRET,
        "rulecam_webhooks": f"generic={WEBHOOK_URL_CANARY}",
        **CANARIES,
    }
    services = services_from_settings(Settings(_env_file=None, **options))  # never read .env
    chain = EventChain(services.chain_root, services.site, clock=FakeClock())
    chain.append("plan", {"note": "seeded by the e2e suite"})
    chain.append("ack", {"role": "lead"})
    return create_app(services)


@pytest.fixture
def visitor(local_app: FastAPI) -> Iterator[TestClient]:
    """A browser on the internet, reaching the API through its public host (not the owner)."""
    with TestClient(
        local_app, base_url=PUBLIC_HOST, client=VISITOR, raise_server_exceptions=False
    ) as client:
        yield client


@pytest.fixture
def owner(local_app: FastAPI) -> Iterator[TestClient]:
    """The owner's browser on the public UI: logged in, with the CSRF token as a header."""
    address = (f"198.51.100.{next(_OWNER_PORTS) % 250 + 1}", 41000)
    with TestClient(
        local_app, base_url=PUBLIC_HOST, client=address, raise_server_exceptions=False
    ) as client:
        reply = client.post(
            "/auth/login", json={"passphrase": PASSPHRASE}, headers={"Origin": UI_ORIGIN}
        )
        assert reply.status_code == 200, reply.text
        csrf = client.get("/auth/me").json()["csrf_token"]
        client.headers.update({"Origin": UI_ORIGIN, "X-CSRF-Token": csrf})
        yield client


def canary_values() -> list[str]:
    """Every secret value the local app was configured with."""
    return [*CANARIES.values(), WEBHOOK_URL_CANARY, SESSION_SECRET, PASSPHRASE]


@pytest.fixture(scope="session")
def live_api() -> str:
    """The deployed API's base URL."""
    return os.environ.get("RULECAM_E2E_API", DEFAULT_LIVE_API).rstrip("/")


@pytest.fixture(scope="session")
def live_ui() -> str:
    """The deployed web UI's base URL."""
    return os.environ.get("RULECAM_E2E_UI", DEFAULT_LIVE_UI).rstrip("/")


def dotenv_secrets(path: Path = Path(".env")) -> dict[str, str]:
    """Secret-looking values from a local `.env` (names ending KEY/TOKEN/SECRET/HASH), if any.

    Used only to check that none of them reached a public bundle or response; the values are
    never printed.
    """
    if not path.is_file():
        return {}
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip().strip("'\"")
        suffixes = ("KEY", "TOKEN", "SECRET", "HASH", "PASSPHRASE", "WEBHOOKS")
        if sep and not name.startswith("#") and name.endswith(suffixes) and len(value) >= 12:
            found[name] = value
    return found
