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
    code_revision,
    create_app,
    find_plan,
    openapi_json,
    services_from_settings,
    viewer_origins,
)
from rulecam.api.plans import PlanRegistry
from rulecam.api.prometheus import HealthProbe
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
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.planner.pipeline import PlanStore
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
            "revision": None,
            "gpus": {},
        }
        assert client.get("/mode").json() == {"mode": "replay", "badge": "REPLAY"}
        assert client.put("/mode", json={"mode": "live"}).json() == {
            "mode": "live",
            "badge": "LIVE",
        }
        assert services.cache.mode == "live"
        assert client.put("/mode", json={"mode": "bogus"}).status_code == 422


def test_health_reports_the_shipped_revision(services: AppServices, tmp_path: Path) -> None:
    shipped = tmp_path / "REVISION"
    assert code_revision(None, shipped) is None  # a dev checkout: nothing shipped
    shipped.write_text("")
    assert code_revision(None, shipped) is None
    shipped.write_text("0123abcd4567\n")
    assert code_revision(None, shipped) == "0123abcd4567"
    assert code_revision(" fromenv ", shipped) == "fromenv"  # RULECAM_REVISION wins
    services.revision = code_revision(None, shipped)
    with TestClient(create_app(services), **TUNNEL) as client:  # type: ignore[arg-type]
        assert client.get("/health").json()["revision"] == "0123abcd4567"


def test_health_serves_each_gpu_state(services: AppServices) -> None:
    services.health_probes = (
        HealthProbe("glimmer", "g", fetch=lambda _: None),
        HealthProbe("sam", "s", fetch=lambda _: {"status": "ok"}),
    )
    with TestClient(create_app(services), **TUNNEL) as client:  # type: ignore[arg-type]
        gpus = client.get("/health").json()["gpus"]
    assert gpus["glimmer"]["state"] == "unhealthy"
    assert gpus["glimmer"]["up"] is False
    assert gpus["sam"] == {
        "state": "warm",
        "up": True,
        "serverless": False,
        "workers_min": None,
        "workers_max": None,
        "workers": None,
        "error": None,
    }


def test_evidence_routes(services: AppServices) -> None:
    clock = FakeClock()
    chain = EventChain(services.chain_root, "site", clock=clock)
    chain.append("plan", {"p": 1})
    chain.append("ack", {"role": "lead"})
    with owner(create_app(services)) as client:
        text = client.get("/evidence/2026-10-30/chain").text
        assert len(text.splitlines()) == 2
    with TestClient(create_app(services), **TUNNEL) as client:  # type: ignore[arg-type]
        # The public sees days, hashes, roots and the verdict, never a record or payload.
        assert client.get("/evidence/2026-10-30/chain").status_code == 403
        assert client.get("/evidence/days").json() == ["2026-10-30"]
        hashes = client.get("/evidence/2026-10-30/hashes").json()
        assert hashes == {
            "n": 2,
            "hashes": [r.hash for r in read_chain(chain.path_for(EPOCH.date()))],
        }
        assert "lead" not in json.dumps(hashes)
        assert client.get("/evidence/2026-10-30/root").json()["n"] == 2
        assert client.get("/evidence/2026-10-30/verify").json()["ok"] is True
        assert client.get("/evidence/2026-10-29/verify").status_code == 404
        assert client.get("/evidence/2026-10-29/hashes").status_code == 404
        assert client.get("/evidence/not-a-day/verify").status_code == 422
        chain.path_for(EPOCH.date()).write_text("{not json}\n", encoding="utf-8")
        assert client.get("/evidence/2026-10-30/hashes").status_code == 409


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
        "/worker/dispute",
        "/worker/{plan_id}/{camera}",
        "/worker/{plan_id}/{camera}/poster",
        "/alerts",
        "/ack",
        "/interlock/{device}/resume",
    ):
        assert path in paths, path
    assert services.tickers  # the escalation timers
    assert services.response is not None
    assert services.response.actions_dry_run  # dry run until the owner turns it off


def test_find_plan_uses_the_registry_then_saved_plans(tmp_path: Path) -> None:
    registry = PlanRegistry()
    registry.put(site_plan("in-registry"))
    store = PlanStore(tmp_path)
    store.save_plan(site_plan("saved"))
    lookup = find_plan(registry, store)
    assert lookup("in-registry") == site_plan("in-registry")
    assert lookup("saved") == site_plan("saved")
    assert lookup("missing") is None
    assert lookup("../etc") is None


def test_services_from_settings_with_runtime(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, rulecam_data_dir=tmp_path, rulecam_runtime=True)
    services = services_from_settings(settings, "s")
    assert services.runtime is not None
    services.runtime.set_plan(sink_plan())
    assert services.pair is not None
    with owner(create_app(services)) as client:
        assert client.get("/health").json()["runtime"] is True
        response = client.get("/runtime/metrics").json()["response"]
        assert response["tier2"] == "configured"  # replay mode replays recorded reviews
        assert response["firewall"] == "degraded"
        assert "HF_TOKEN" in response["firewall_reason"]
        assert response["actions_dry_run"] is True
        cost = client.get("/runtime/metrics").json()["cost"]
        assert cost["budget_usd_per_camera_hour"] == 12.0
        assert cost["alarm"] is False
        assert cost["idle_stop"]["enabled"] is False  # off by default
        assert "rulecam_cost_budget_alarm 0.0" in client.get("/metrics").text
        metrics = client.get("/runtime/metrics").json()
        assert metrics["verifier_down"] is False
        assert metrics["verifier"]["state"] == "closed"  # Tier 1 behind its circuit breaker
        assert "rulecam_runtime_verifier_down 0.0" in client.get("/metrics").text
    # Escalation timers, Tier 1 health probe, the cost watch, the staff app's Web Push relay.
    assert len(services.tickers) == 4


def test_alert_routes_are_owner_only(tmp_path: Path) -> None:
    services = services_from_settings(Settings(_env_file=None, rulecam_data_dir=tmp_path), "s")
    app = create_app(services)
    with TestClient(app, **TUNNEL) as remote:  # type: ignore[arg-type]
        assert remote.get("/alerts").status_code == 403
        assert remote.post("/ack", json={"event_id": "e", "role": "lead"}).status_code == 403
        assert remote.post("/interlock/plug/resume", json={"role": "lead"}).status_code == 403
    with owner(app) as client:
        assert client.get("/alerts").json() == []
        assert client.post("/interlock/plug/resume", json={"role": "lead"}).status_code == 404


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
        assert client.post("/plans/nope/approve").status_code == 404
        # No activator here: approving is refused and nothing is published (see test_approve).
        assert client.post("/plans/main-st/approve").status_code == 503
        assert client.get("/plans").json()[0]["approved"] is False
    assert received == []
    with TestClient(create_app(services), **TUNNEL) as remote:  # type: ignore[arg-type]
        assert remote.post("/plans", json=body).status_code == 403
        assert remote.post("/plans/main-st/approve").status_code == 403
        assert remote.get("/plans").status_code == 403  # plans hold request text (W-01)


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


PUBLIC_UI = "https://rulecam.vercel.app"


def test_public_ui_can_watch_ws_but_not_change_state(services: AppServices) -> None:
    services.viewer_origins = viewer_origins(PUBLIC_UI + "/worker")
    services.public_demo = True
    app = create_app(services)
    with TestClient(app, **TUNNEL) as client:  # type: ignore[arg-type]
        client.headers["Origin"] = PUBLIC_UI
        with client.websocket_connect("wss://rulecam-demo.trycloudflare.com/ws") as ws:
            threading.Timer(0.2, lambda: services.cache.set_mode("record")).start()
            assert ws.receive_json()["topic"] == Topic.MODE_CHANGED
        assert client.put("/mode", json={"mode": "live"}).status_code == 403
        client.headers["Origin"] = "https://evil.example"
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as ws:
            ws.receive_json()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (None, frozenset()),
        ("", frozenset()),
        ("https://rulecam.vercel.app/", frozenset({"https://rulecam.vercel.app"})),
        ("http://localhost:3000/x?y=1", frozenset({"http://localhost:3000"})),
    ],
)
def test_viewer_origins_from_public_ui_url(url: str | None, expected: frozenset[str]) -> None:
    assert viewer_origins(url) == expected


def test_viewer_origins_take_extra_ui_hosts() -> None:
    extra = " https://ui-3000.proxy.runpod.net/x , http://localhost:3000"
    assert viewer_origins(PUBLIC_UI, extra) == viewer_origins(PUBLIC_UI) | {
        "https://ui-3000.proxy.runpod.net",
        "http://localhost:3000",
    }
    assert viewer_origins(None, "https://ui-3000.proxy.runpod.net") == {
        "https://ui-3000.proxy.runpod.net"
    }
    with pytest.raises(ValueError, match="http"):
        viewer_origins(None, "ftp://x")


def test_viewer_origins_rejects_non_http_urls() -> None:
    with pytest.raises(ValueError, match="PUBLIC_UI_URL"):
        viewer_origins("javascript:alert(1)")


def test_public_ui_origin_alone_cannot_watch_ws(services: AppServices) -> None:
    """Without PUBLIC_DEMO an Origin header (easy to forge outside a browser) grants nothing."""
    services.viewer_origins = viewer_origins(PUBLIC_UI)
    with TestClient(create_app(services), **TUNNEL) as client:  # type: ignore[arg-type]
        client.headers["Origin"] = PUBLIC_UI
        with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws") as ws:
            ws.receive_json()


def test_plan_reads_are_owner_only_unless_public_demo(services: AppServices) -> None:
    EventChain(services.chain_root, "site", clock=FakeClock()).append("plan", {"p": 1})
    services.plans.put(site_plan("p1"))
    plans = ("/plans", "/plans/p1")
    chain = "/evidence/2026-10-30/chain"

    with TestClient(create_app(services), **TUNNEL) as client:  # type: ignore[arg-type]
        assert [client.get(path).status_code for path in plans] == [403, 403]
    with owner(create_app(services)) as client:
        assert [client.get(path).status_code for path in plans] == [200, 200]

    services.public_demo = True
    with TestClient(create_app(services), **TUNNEL) as client:  # type: ignore[arg-type]
        assert [client.get(path).status_code for path in plans] == [200, 200]
        assert client.get(chain).status_code == 403  # the chain stays owner-only


def test_correction_is_owner_only_through_the_tunnel(tmp_path: Path) -> None:
    services = services_from_settings(
        Settings(_env_file=None, rulecam_data_dir=tmp_path, public_demo=True), "s"
    )
    body = {"event_id": "ev-1", "frame_idx": 0, "x": 1.0, "y": 1.0}
    with TestClient(create_app(services), **TUNNEL) as client:  # type: ignore[arg-type]
        assert client.post("/correction", json=body).status_code == 403
    with owner(create_app(services)) as client:
        assert client.post("/correction", json=body).status_code == 404


def test_public_demo_setting_reaches_the_app(tmp_path: Path) -> None:
    off = services_from_settings(Settings(_env_file=None, rulecam_data_dir=tmp_path), "s")
    on = services_from_settings(
        Settings(_env_file=None, rulecam_data_dir=tmp_path, public_demo=True), "s"
    )
    assert (off.public_demo, on.public_demo) == (False, True)


def test_pollers_run_back_to_back_and_survive_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from rulecam.api import app as app_module  # noqa: PLC0415

    monkeypatch.setattr(app_module, "TICK_S", 0.0)
    calls: list[str] = []

    def flaky() -> None:
        calls.append("poll")
        if len(calls) == 1:
            raise RuntimeError("poll failed")

    async def run() -> None:
        task = asyncio.create_task(app_module._poll_forever(flaky))
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(run(), timeout=5))
    assert len(calls) >= 3


def test_lifespan_starts_pollers(services: AppServices) -> None:
    polled = threading.Event()

    def poller() -> None:
        polled.set()
        polled.wait(0.01)

    services.pollers = (poller,)
    with owner(create_app(services)):
        assert polled.wait(timeout=5)


def test_telegram_is_wired_when_a_bot_token_is_set(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rulecam_data_dir=tmp_path,
        telegram_bot_token="123:abc",  # noqa: S106 - test fixture
        telegram_bot_username="site_bot",
    )
    services = services_from_settings(settings, "s")
    assert len(services.pollers) == 1  # the long-polling bot (not started in this test)
    assert services.response is not None
    assert services.response.telegram is True
    app = create_app(services)
    paths = json.loads(openapi_json(app))["paths"]
    assert {"/telegram/link", "/telegram/links"} <= set(paths)
    with TestClient(app, **TUNNEL) as remote:  # type: ignore[arg-type]
        assert remote.get("/telegram/link", params={"role": "aide"}).status_code == 403
    assert services_from_settings(Settings(_env_file=None, rulecam_data_dir=tmp_path)).pollers == ()


def test_webhooks_are_wired_when_configured(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        rulecam_data_dir=tmp_path,
        rulecam_webhooks="slack=https://hooks.slack.com/services/x,teams=https://flow.example/t",
        public_ui_url="https://ui.example",
    )
    services = services_from_settings(settings, "s")
    assert len(services.pollers) == 1  # the webhook queue
    assert services.response is not None
    assert services.response.webhooks == ("slack", "teams")
    assert "hooks.slack.com" not in services.response.model_dump_json()  # URLs are secrets
