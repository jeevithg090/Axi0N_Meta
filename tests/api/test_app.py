"""API contract tests (P3-08)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rulecam.api.app import AppServices, create_app, openapi_json, services_from_settings
from rulecam.api.ws import frame
from rulecam.bus import Bus, Message, Topic
from rulecam.config import Settings
from rulecam.evidence.chain import EventChain
from rulecam.replay.cache import ModeChanged, ReplayCache
from tests.fakes import FakeClock, make_chunk
from tests.runtime.fakes import make_loop, sink_plan


@pytest.fixture
def services(tmp_path: Path) -> AppServices:
    bus = Bus()
    cache = ReplayCache(tmp_path / "replay", "replay", clock=FakeClock(), bus=bus)
    return AppServices(bus=bus, cache=cache, chain_root=tmp_path / "chain", site="site")


def test_health_and_mode(services: AppServices) -> None:
    with TestClient(create_app(services)) as client:
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
    with TestClient(create_app(services)) as client:
        body = sink_plan().model_dump(mode="json")
        assert client.put("/runtime/plan", json=body).json() == {"rules": 1}
        assert loop.plan is not None
        assert client.get("/runtime/metrics").json()["chunks_processed"] == 0
        assert client.get("/health").json()["runtime"] is True


def test_ws_mirrors_the_bus(services: AppServices) -> None:
    with TestClient(create_app(services)) as client, client.websocket_connect("/ws") as ws:
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
