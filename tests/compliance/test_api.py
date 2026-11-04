"""Proof portal routes: owner-only building and sharing, public signed links."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor, Future
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from rulecam.compliance.api import ProofService, proof_router
from rulecam.compliance.build import BuildInputs
from rulecam.compliance.share import PackStore, ShareError, ShareSigner, load_secret
from rulecam.planner.pipeline import PlanStore

DAY = {"start": "2026-11-01T00:00:00Z", "end": "2026-11-02T00:00:00Z", "models": False}


class _Now(Executor):
    """Runs a submitted build at once, so a test sees `ready` on the next read."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


def _owner(request: Request) -> None:
    if request.headers.get("x-owner") != "1":
        raise HTTPException(403, "owner only")


def _no_chromium(html: str, out: Path) -> Path:
    raise RuntimeError("no browser")


@pytest.fixture
def client(golden_chain: Path, tmp_path: Path) -> TestClient:
    service = ProofService(
        lambda: BuildInputs(
            chain_root=golden_chain,
            site="demo",
            plans=PlanStore(tmp_path / "plans"),
            roots_dir=tmp_path / "roots",
        ),
        PackStore(tmp_path / "compliance"),
        ShareSigner(b"s" * 32),
        executor=_Now(),
        printer=_no_chromium,
    )
    app = FastAPI()
    app.include_router(proof_router(service, dependencies=[Depends(_owner)]))
    return TestClient(app)


OWNER = {"x-owner": "1"}


def test_owner_builds_shares_and_revokes(client: TestClient) -> None:
    assert client.post("/proof/packs", json=DAY).status_code == 403
    started = client.post("/proof/packs", json=DAY, headers=OWNER)
    assert started.status_code == 202
    assert started.json()["status"] == "building"
    pack_id = started.json()["pack_id"]
    entry = client.get(f"/proof/packs/{pack_id}", headers=OWNER).json()
    assert entry["status"] == "ready"
    assert len(entry["pack_hash"]) == 64
    assert [p["pack_id"] for p in client.get("/proof/packs", headers=OWNER).json()] == [pack_id]
    link = client.post(
        f"/proof/packs/{pack_id}/share",
        json={"hours": 2, "ui_origin": "http://localhost:3000"},
        headers=OWNER,
    ).json()
    assert link["url"].startswith("http://localhost:3000/inspector/view?t=")
    assert link["qr"].startswith("data:image/png;base64,")
    shared = client.get(f"/proof/shared/{link['token']}")
    assert shared.status_code == 200
    assert shared.json()["pack_hash"] == entry["pack_hash"]
    doc = client.get(f"/proof/shared/{link['token']}/document?format=pdf")
    assert doc.headers["x-rulecam-report-format"] == "html"  # Chromium missing: printable HTML
    assert b"Check it yourself" in doc.content
    assert b"data:image/png;base64," in doc.content
    forged = link["token"][:-4] + ("AAAA" if not link["token"].endswith("AAAA") else "BBBB")
    assert client.get(f"/proof/shared/{forged}").status_code == 403
    assert client.post(f"/proof/packs/{pack_id}/revoke", headers=OWNER).json() == {"revoked": True}
    assert client.get(f"/proof/shared/{link['token']}").status_code == 410
    assert client.post(f"/proof/packs/{pack_id}/share", json={}, headers=OWNER).status_code == 409


def test_bad_period_and_unknown_pack(client: TestClient) -> None:
    reversed_day = {**DAY, "start": DAY["end"], "end": DAY["start"]}
    assert client.post("/proof/packs", json=reversed_day, headers=OWNER).status_code == 422
    assert client.get("/proof/packs/pp-000000000000", headers=OWNER).status_code == 404
    assert client.get("/proof/packs/pp-000000000000/document", headers=OWNER).status_code == 404


def test_tokens_expire_and_bind_the_pack(tmp_path: Path) -> None:
    signer = ShareSigner(load_secret(None, tmp_path / "share.key"))
    assert load_secret(None, tmp_path / "share.key") == (tmp_path / "share.key").read_bytes()
    now = datetime(2026, 11, 4, tzinfo=UTC)
    token = signer.mint("pp-0123456789ab", "a" * 64, now + timedelta(hours=1))
    claims = signer.check(token, now)
    assert (claims.p, claims.h) == ("pp-0123456789ab", "a" * 64)
    with pytest.raises(ShareError, match="expired"):
        signer.check(token, now + timedelta(hours=2))
    with pytest.raises(ShareError, match="not valid"):
        ShareSigner(b"other").check(token, now)
    with pytest.raises(ShareError, match="not valid"):
        signer.check("garbage", now)
