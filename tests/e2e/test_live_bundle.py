"""The deployed web UI ships no secret (marked `live`; `uv run pytest -m live tests/e2e`).

Fetches the public pages of `RULECAM_E2E_UI`, every `/_next/static` script they load, and checks
each for credential patterns and for the values of the secret settings in a local `.env` (when
one is present; the values are compared, never printed). Also checks that every page answers 200
and that no source map is published.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator

import httpx
import pytest

from tests.e2e.conftest import dotenv_secrets
from tests.e2e.surface import leaks

pytestmark = pytest.mark.live

PAGES = (
    "/",
    "/demo?demo=1",
    "/planner",
    "/plans",
    "/monitor",
    "/alerts",
    "/ask",
    "/incidents",
    "/ci",
    "/cards",
    "/advice",
    "/surprise",
    "/system",
    "/site",
    "/streams",
    "/audit",
    "/extensions",
    "/login",
    "/worker",
)
SERVER_ONLY_NAMES = ("RULECAM_PROXY_SECRET", "UI_PROXY_SECRET", "META_API_KEY", "SESSION_SECRET")
SCRIPT = re.compile(r"""(/_next/static/[^"'\s>]+?\.js)""")


@pytest.fixture(scope="module")
def ui(live_ui: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=live_ui, timeout=30, follow_redirects=True) as client:
        yield client


@pytest.fixture(scope="module")
def pages(ui: httpx.Client) -> dict[str, httpx.Response]:
    found = {}
    for page in PAGES:
        time.sleep(0.2)
        found[page] = ui.get(page)
    return found


@pytest.fixture(scope="module")
def scripts(ui: httpx.Client, pages: dict[str, httpx.Response]) -> dict[str, str]:
    urls = sorted({m for reply in pages.values() for m in SCRIPT.findall(reply.text)})
    assert urls, "no Next.js scripts found on the pages"
    bodies = {}
    for url in urls:
        time.sleep(0.1)
        reply = ui.get(url)
        assert reply.status_code == 200, url
        bodies[url] = reply.text
    return bodies


def test_every_page_answers(pages: dict[str, httpx.Response]) -> None:
    broken = {page: r.status_code for page, r in pages.items() if r.status_code != 200}
    assert broken == {}


def test_no_page_or_script_carries_a_secret(
    pages: dict[str, httpx.Response], scripts: dict[str, str]
) -> None:
    secrets = list(dotenv_secrets().values())
    texts = {**{p: r.text for p, r in pages.items()}, **scripts}
    found = {name: hits for name, text in texts.items() if (hits := leaks(text, secrets))}
    assert found == {}


def test_server_only_settings_stay_on_the_server(scripts: dict[str, str]) -> None:
    named = {
        url: [name for name in SERVER_ONLY_NAMES if name in text] for url, text in scripts.items()
    }
    assert {url: names for url, names in named.items() if names} == {}


def test_no_source_maps_are_published(ui: httpx.Client, scripts: dict[str, str]) -> None:
    for url in list(scripts)[:5]:
        time.sleep(0.1)
        assert ui.get(url + ".map").status_code in {403, 404}, url
