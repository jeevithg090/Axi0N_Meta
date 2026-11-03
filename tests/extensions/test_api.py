"""`/extensions`: propose, list, approve and retire over HTTP (fakes, no network)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.bus import Bus
from rulecam.clients.meta_hosted.muse_code import MuseCodeClient
from rulecam.config import Settings
from rulecam.evidence.chain import EventChain
from rulecam.extensions.api import build_extensions, extensions_router
from rulecam.extensions.generate import CheckGenerator
from rulecam.extensions.registry import ExtensionRegistry
from rulecam.extensions.review import ExtensionReviewer
from rulecam.extensions.service import ExtensionService
from rulecam.replay.cache import ReplayCache
from tests.fakes import FakeClock
from tests.fakes.extensions import FakeMuseCode, FakeSparkOpen

BODY = {
    "request": "Alert if a pallet is stacked above the yellow line on the rack",
    "concept": "pallet",
    "camera_id": "cam1",
    "lines": {"yellow": [[0, 200], [640, 200]]},
}


def _client(tmp_path: Path, *, with_service: bool = True) -> TestClient:
    clock = FakeClock()
    registry = ExtensionRegistry(
        tmp_path / "ext", clock=clock, chain=EventChain(tmp_path / "chain", "s", clock=clock)
    )
    muse = MuseCodeClient(
        cache=ReplayCache(tmp_path / "replay", "live", clock=clock),
        runner=FakeMuseCode(),
        which=lambda n: f"/bin/{n}",
    )
    service = (
        ExtensionService(
            CheckGenerator(muse), ExtensionReviewer(FakeSparkOpen()), registry, clock=clock
        )
        if with_service
        else None
    )
    app = FastAPI()
    app.include_router(extensions_router(service, registry))
    return TestClient(app)


def test_propose_list_approve_retire(tmp_path: Path) -> None:
    client = _client(tmp_path)
    proposed = client.post("/extensions", json=BODY)
    assert proposed.status_code == 200, proposed.text
    record = proposed.json()
    assert record["status"] == "proposed"
    assert record["generator"] == "muse-code"
    assert [s["name"] for s in record["proof"]["steps"]][:4] == [
        "validator",
        "muse_code_tests",
        "boundary",
        "mutation",
    ]
    name = record["name"]
    assert [r["name"] for r in client.get("/extensions").json()] == [name]
    assert len(client.get(f"/extensions/{name}").json()) == 1

    approved = client.post(f"/extensions/{name}/approve")
    assert approved.json()["status"] == "approved"
    assert client.post(f"/extensions/{name}/approve").status_code == 409
    retired = client.post(f"/extensions/{name}/retire", json={"sha256": record["sha256"][:12]})
    assert retired.json()["status"] == "retired"


def test_errors_map_to_status_codes(tmp_path: Path) -> None:
    client = _client(tmp_path, with_service=False)
    assert client.post("/extensions", json=BODY).status_code == 503
    assert client.get("/extensions/nothing_here").status_code == 404
    assert client.post("/extensions/nothing_here/approve").status_code == 404
    assert client.post("/extensions", json={**BODY, "name": "Bad Name"}).status_code == 422


def test_the_same_code_twice_is_a_conflict(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/extensions", json=BODY).status_code == 200
    assert client.post("/extensions", json=BODY).status_code == 409


def test_build_without_keys_still_serves_the_registry(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rulecam_data_dir=tmp_path,
        rulecam_muse_code_bin="no-such-muse",
    )
    clock = FakeClock()
    built = build_extensions(
        settings,
        cache=ReplayCache(tmp_path / "replay", "live", clock=clock),
        clock=clock,
        bus=Bus(),
        chain=None,
    )
    assert built.registry.all_latest() == []
    app = FastAPI()
    app.include_router(built.router())
    client = TestClient(app)
    assert client.get("/extensions").json() == []
