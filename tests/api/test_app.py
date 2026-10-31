"""API contract tests (P3-08)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from rulecam.api.app import (
    AppServices,
    _cameras,
    _log_task_end,
    create_app,
    openapi_json,
    services_from_settings,
)
from rulecam.api.plans import PlanRegistry
from rulecam.api.ws import frame
from rulecam.bus import Bus, Message, Topic
from rulecam.capture.pair_phone import PairedCamera
from rulecam.config import Settings
from rulecam.contracts import (
    AcceptanceCheck,
    AcceptanceResult,
    PrivacyMode,
    RequestItem,
    SitePlan,
)
from rulecam.evidence.chain import EventChain
from rulecam.replay.cache import ModeChanged, ReplayCache
from rulecam.runtime.factory import ChunkIndex
from tests.fakes import EPOCH, FakeClock, make_chunk
from tests.runtime.fakes import make_loop, sink_plan

OWNER = {"base_url": "http://localhost", "client": ("127.0.0.1", 50000)}
TUNNEL = {"base_url": "https://rulecam-demo.trycloudflare.com", "client": ("127.0.0.1", 50001)}


def owner(app: FastAPI) -> TestClient:
    """A client on the owner's machine (loopback host and client)."""
    return TestClient(app, **OWNER)  # type: ignore[arg-type]


@pytest.fixture
def services(tmp_path: Path) -> AppServices:
    bus = Bus()
    cache = ReplayCache(tmp_path / "replay", "replay", clock=FakeClock(), bus=bus)
    return AppServices(
        bus=bus,
        cache=cache,
        chain_root=tmp_path / "chain",
        site="site",
        verify_dir=tmp_path / "verify",
    )


def test_health_and_mode(services: AppServices) -> None:
    with owner(create_app(services)) as client:
        assert client.get("/health").json() == {
            "ok": True,
            "site": "site",
            "mode": "replay",
            "runtime": False,
        }
        assert client.get("/mode").json() == {"mode": "replay", "badge": "REPLAY"}
        assert client.put("/mode", json={"mode": "live"}).json() == {
            "mode": "live",
            "badge": "LIVE",
        }
        assert services.cache.mode == "live"
        assert client.put("/mode", json={"mode": "bogus"}).status_code == 422


def test_evidence_routes(services: AppServices) -> None:
    clock = FakeClock()
    chain = EventChain(services.chain_root, "site", clock=clock)
    chain.append("plan", {"p": 1})
    chain.append("ack", {"role": "lead"})
    with TestClient(create_app(services)) as client:
        assert client.get("/evidence/days").json() == ["2026-10-30"]
        text = client.get("/evidence/2026-10-30/chain").text
        assert len(text.splitlines()) == 2
        assert client.get("/evidence/2026-10-30/verify").json()["ok"] is True
        assert client.get("/evidence/2026-10-29/verify").status_code == 404
        assert client.get("/evidence/not-a-day/verify").status_code == 422


def test_evidence_days_empty(services: AppServices) -> None:
    with TestClient(create_app(services)) as client:
        assert client.get("/evidence/days").json() == []


def test_runtime_routes_without_runtime(services: AppServices) -> None:
    with TestClient(create_app(services)) as client:
        assert client.get("/runtime/metrics").status_code == 503


def test_runtime_routes_with_runtime(services: AppServices, tmp_path: Path) -> None:
    loop, _, _, _ = make_loop(tmp_path, FakeClock())
    services.runtime = loop
    with owner(create_app(services)) as client:
        body = sink_plan().model_dump(mode="json")
        assert client.put("/runtime/plan", json=body).json() == {"rules": 1}
        assert loop.plan is not None
        assert client.get("/runtime/metrics").json()["chunks_processed"] == 0
        assert client.get("/health").json()["runtime"] is True


def test_ws_mirrors_the_bus(services: AppServices) -> None:
    with owner(create_app(services)) as client, client.websocket_connect("ws://localhost/ws") as ws:
        published = threading.Event()

        def publish_from_worker() -> None:
            services.cache.set_mode("record")  # publishes mode.changed from a worker thread
            published.set()

        # Give the server a moment to subscribe, then publish off the event loop.
        threading.Timer(0.2, publish_from_worker).start()
        message = ws.receive_json()
        assert published.wait(2)
        assert message == {"topic": "mode.changed", "payload": {"mode": "record", "badge": "LIVE"}}


def test_frame_encodes_models_and_plain_data() -> None:
    model = frame(Message(Topic.MODE_CHANGED, ModeChanged(mode="live", badge="LIVE")))
    assert model["payload"] == {"mode": "live", "badge": "LIVE"}
    chunk = frame(Message(Topic.CHUNK_READY, {"chunk": make_chunk()}))
    assert chunk["topic"] == "chunk.ready"
    assert isinstance(chunk["payload"], dict)


def test_extra_routers_and_openapi(services: AppServices) -> None:
    from fastapi import APIRouter  # noqa: PLC0415

    extra = APIRouter()

    @extra.get("/extra")
    def extra_route() -> dict[str, bool]:
        return {"ok": True}

    services.routers = (extra,)
    app = create_app(services)
    with TestClient(app) as client:
        assert client.get("/extra").json() == {"ok": True}
    schema = openapi_json(app)
    assert '"/mode"' in schema
    assert '"/evidence/{day}/verify"' in schema


def test_services_from_settings(tmp_path: Path) -> None:
    services = services_from_settings(Settings(_env_file=None, rulecam_data_dir=tmp_path), "s")
    assert services.chain_root == tmp_path / "evidence" / "chain"
    assert services.cache.mode == "replay"
    assert services.runtime is None
    assert services.pair is not None
    assert services.correction is not None
    paths = json.loads(openapi_json(create_app(services)))["paths"]
    for path in (
        "/pair/token",
        "/correction",
        "/plans",
        "/plans/{plan_id}/approve",
        "/cameras",
        "/metrics/perception",
        "/mode",
    ):
        assert path in paths, path


def test_services_from_settings_with_runtime(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, rulecam_data_dir=tmp_path, rulecam_runtime=True)
    services = services_from_settings(settings, "s")
    assert services.runtime is not None
    services.runtime.set_plan(sink_plan())
    assert services.pair is not None
    with owner(create_app(services)) as client:
        assert client.get("/health").json()["runtime"] is True


async def test_bus_publish_from_worker_thread_is_delivered() -> None:
    bus = Bus()
    bus.bind_loop(asyncio.get_running_loop())
    stream = bus.subscribe(Topic.CHUNK_READY)
    pending = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0)
    await asyncio.to_thread(bus.publish, Topic.CHUNK_READY, "x")
    message = await asyncio.wait_for(pending, timeout=2)
    await stream.aclose()
    assert message.payload == "x"


def test_tickers_survive_a_failing_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    from rulecam.api import app as app_module  # noqa: PLC0415

    monkeypatch.setattr(app_module, "TICK_S", 0.0)
    calls: list[str] = []

    def boom() -> None:
        calls.append("boom")
        raise RuntimeError("tick failed")

    def ok() -> None:
        calls.append("ok")

    async def run() -> None:
        task = asyncio.create_task(app_module._tick_forever([boom, ok]))
        while calls.count("ok") < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(run(), timeout=5))
    assert calls.count("boom") >= 3


def test_state_changes_and_ws_are_owner_only(services: AppServices, tmp_path: Path) -> None:
    services.runtime = make_loop(tmp_path, FakeClock())[0]
    app = create_app(services)
    for where in (TUNNEL, {"base_url": "http://localhost", "client": ("203.0.113.7", 1)}):
        with TestClient(app, **where) as client:  # type: ignore[arg-type]
            assert client.get("/mode").status_code == 200
            assert client.put("/mode", json={"mode": "live"}).status_code == 403
            body = sink_plan().model_dump(mode="json")
            assert client.put("/runtime/plan", json=body).status_code == 403
            ws_url = str(where["base_url"]).replace("http", "ws", 1) + "/ws"
            with pytest.raises(WebSocketDisconnect), client.websocket_connect(ws_url) as ws:
                ws.receive_json()
    assert services.cache.mode == "replay"
    assert services.runtime.plan is None


def test_another_sites_page_cannot_use_the_owners_browser(services: AppServices) -> None:
    app = create_app(services)
    with owner(app) as client:
        client.headers["Origin"] = "https://evil.example"
        assert client.put("/mode", json={"mode": "live"}).status_code == 403
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as ws:
            ws.receive_json()
        client.headers["Origin"] = "http://localhost:3000"
        assert client.put("/mode", json={"mode": "live"}).status_code == 200
    assert services.cache.mode == "live"


def site_plan(plan_id: str = "main-st") -> SitePlan:
    return SitePlan(
        plan_id=plan_id,
        site_name="Main St kitchen",
        setting_inferred="commercial_kitchen",
        languages=("en",),
        request=(RequestItem(kind="text", text="pass the next inspection"),),
        inventory=(),
        feasibility=(),
        rules=(),
        privacy_mode=PrivacyMode.ANONYMOUS,
        acceptance=AcceptanceResult(
            checks=(AcceptanceCheck(item="r", check="ci_green", passed=True),), rounds=1
        ),
    )


def test_plans_create_list_get_approve(services: AppServices) -> None:
    received: list[object] = []
    services.bus.publish = lambda topic, payload: received.append((topic, payload))  # type: ignore[method-assign]
    body = site_plan().model_dump(mode="json")
    with owner(create_app(services)) as client:
        assert client.post("/plans", json=body).status_code == 201
        assert client.get("/plans").json() == [
            {"plan_id": "main-st", "version": 1, "site_name": "Main St kitchen", "approved": False}
        ]
        assert client.get("/plans/main-st").json()["plan_id"] == "main-st"
        assert client.get("/plans/nope").status_code == 404
        assert client.get("/plans/bad%20id").status_code == 422
        assert client.post("/plans/main-st/approve").json()["approved"] is True
        assert client.post("/plans/nope/approve").status_code == 404
    assert received == [(Topic.PLAN_UPDATED, site_plan())]
    with TestClient(create_app(services), **TUNNEL) as remote:  # type: ignore[arg-type]
        assert remote.post("/plans", json=body).status_code == 403
        assert remote.post("/plans/main-st/approve").status_code == 403
        assert remote.get("/plans").status_code == 200


def test_plan_registry_is_bounded() -> None:
    registry = PlanRegistry(max_plans=1)
    registry.put(site_plan("a"))
    registry.put(site_plan("a"))  # replacing is fine
    with pytest.raises(HTTPException) as err:
        registry.put(site_plan("b"))
    assert err.value.status_code == 409


def test_cameras_metrics_and_verify(services: AppServices) -> None:
    services.chunks = ChunkIndex(services.perception_metrics)
    assert services.verify_dir is not None
    services.verify_dir.mkdir(parents=True)
    (services.verify_dir / "index.html").write_text("<p>verify</p>", encoding="utf-8")
    with owner(create_app(services)) as client:
        assert client.get("/cameras").json() == []
        services.bus.publish(Topic.CHUNK_READY, make_chunk(camera_id="cam1"))
        for _ in range(100):
            if client.get("/cameras").json():
                break
        cams = client.get("/cameras").json()
        assert [(c["camera_id"], c["source"]) for c in cams] == [("cam1", "capture")]
        report = client.get("/metrics/perception").json()
        assert [c["camera_id"] for c in report["cameras"]] == ["cam1"]
        assert client.get("/verify/").text == "<p>verify</p>"


def test_verify_dir_is_created(services: AppServices) -> None:
    create_app(services)
    assert services.verify_dir is not None
    assert services.verify_dir.is_dir()


def test_cameras_lists_paired_phones(services: AppServices) -> None:
    pair = MagicMock()
    pair.registry.cameras.return_value = [
        PairedCamera(camera_id="phone-1", label="sink", paired_at=EPOCH)
    ]
    services.pair = pair
    services.chunks = ChunkIndex()
    services.chunks.record(make_chunk(camera_id="phone-1"))
    services.chunks.record(make_chunk(camera_id="cam2"))
    rows = [(c.camera_id, c.label, c.source) for c in _cameras(services)]
    assert rows == [("phone-1", "sink", "paired_phone"), ("cam2", "cam2", "capture")]


def test_evidence_days_skips_stray_files(services: AppServices) -> None:
    folder = services.chain_root / services.site
    folder.mkdir(parents=True)
    (folder / "2026-10-30.jsonl").write_text("", encoding="utf-8")
    (folder / "notes.jsonl").write_text("", encoding="utf-8")
    with owner(create_app(services)) as client:
        assert client.get("/evidence/days").json() == ["2026-10-30"]


async def test_dead_background_task_is_logged() -> None:
    async def boom() -> None:
        raise RuntimeError("x")

    task = asyncio.create_task(boom())
    with contextlib.suppress(RuntimeError):
        await task
    _log_task_end(task)  # logs, does not raise
    cancelled = asyncio.create_task(asyncio.sleep(10))
    cancelled.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cancelled
    _log_task_end(cancelled)


def test_verify_page_and_day_root(services: AppServices, tmp_path: Path) -> None:
    from rulecam.api.app import VERIFY_PAGE_DIR  # noqa: PLC0415
    from rulecam.evidence.merkle import write_day_roots  # noqa: PLC0415

    EventChain(services.chain_root, "site", clock=FakeClock()).append("plan", {})
    services.roots_dir = tmp_path / "roots"
    services.verify_dir = VERIFY_PAGE_DIR
    with TestClient(create_app(services)) as client:
        page = client.get("/verify/")
        assert page.status_code == 200
        assert "Rulecam evidence check" in page.text
        assert client.get("/verify/chain.js").status_code == 200
        before = client.get("/evidence/2026-10-30/root").json()
        assert before["n"] == 1
        assert before["committed_root"] is None
        assert before["committed_n"] is None
        write_day_roots(services.chain_root, services.roots_dir, FakeClock().now().date())
        after = client.get("/evidence/2026-10-30/root").json()
        assert after["committed_root"] == after["root"]
        assert after["committed_n"] == 1
        EventChain(services.chain_root, "site", clock=FakeClock()).append("plan", {"n": 2})
        grown = client.get("/evidence/2026-10-30/root").json()
        assert (grown["n"], grown["committed_n"]) == (2, 1)
        assert grown["committed_root"] == after["root"] != grown["root"]
