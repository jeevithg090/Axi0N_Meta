"""`POST /plans/{id}/approve` activates the plan in the runtime (P3-08)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rulecam.api.app import (
    ACTIVE_PLAN_FILE,
    AppServices,
    create_app,
    run_calibrations,
    services_from_settings,
)
from rulecam.bus import Topic
from rulecam.config import Settings
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.planner.pipeline import PipelineInput, PlanStore
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.activate import PlanActivator
from rulecam.runtime.loop import RuntimeLoop
from tests.api.test_app import OWNER, TUNNEL
from tests.fakes import FakeClock, make_chunk
from tests.rules.helpers import CAL
from tests.runtime.activation_helpers import checks_for, sink_rule, zoned_plan
from tests.runtime.fakes import RecordingBus, make_loop


def build(
    tmp_path: Path, *, with_runtime: bool = True
) -> tuple[AppServices, RuntimeLoop, EventChain]:
    clock = FakeClock()
    loop, _, chain, _ = make_loop(tmp_path, clock)
    bus = RecordingBus()
    runtime = loop if with_runtime else None
    services = AppServices(
        bus=bus,
        cache=ReplayCache(tmp_path / "replay", "replay", clock=clock, bus=bus),
        chain_root=tmp_path / "chain",
        site="site",
        runtime=runtime,
        activator=PlanActivator(
            runtime=runtime, chain=chain, state_path=tmp_path / "state" / "active_plan.json"
        ),
    )
    return services, loop, chain


def owner(services: AppServices) -> TestClient:
    return TestClient(create_app(services), **OWNER)  # type: ignore[arg-type]


def test_approve_activates_records_and_publishes(tmp_path: Path) -> None:
    services, loop, chain = build(tmp_path)
    plan = zoned_plan(
        [sink_rule("in-sink"), sink_rule("red")],
        [*checks_for("in-sink"), *checks_for("red", failing=["ci_green"])],
    )
    with owner(services) as client:
        assert client.post("/plans", json=plan.model_dump(mode="json")).status_code == 201
        reply = client.post("/plans/main-st/approve")
        assert reply.status_code == 200
        body = reply.json()
        assert client.get("/plans").json()[0]["approved"] is True
    assert body["approved"] is True
    assert body["activated"] == ["in-sink"]
    assert [g["status"] for g in body["gates"]] == ["pass", "fail"]
    assert loop.plan is not None
    assert body["plan_version_hash"] == loop.plan.plan_version_hash
    assert [r.id for r in loop.plan.rules] == ["in-sink"]
    records = read_chain(chain.path_for(FakeClock().now().date()))
    assert records[-1].payload["event"] == "plan_approved"
    assert records[-1].payload["rules"] == ["in-sink"]
    assert isinstance(services.bus, RecordingBus)
    assert (Topic.PLAN_UPDATED, plan) in services.bus.published
    assert len(loop.handle_chunk(make_chunk(index=0))) == 1


def test_approve_refuses_a_plan_with_nothing_activatable(tmp_path: Path) -> None:
    services, loop, chain = build(tmp_path)
    plan = zoned_plan([sink_rule()], checks_for("in-sink", failing=["nonvacuous"]))
    with owner(services) as client:
        client.post("/plans", json=plan.model_dump(mode="json"))
        reply = client.post("/plans/main-st/approve")
        assert reply.status_code == 409
        assert "in-sink: nonvacuous failed" in reply.json()["detail"]
        assert client.get("/plans").json()[0]["approved"] is False
    assert loop.plan is None
    assert not chain.path_for(FakeClock().now().date()).exists()
    assert isinstance(services.bus, RecordingBus)
    assert Topic.PLAN_UPDATED not in services.bus.topics()


def test_approve_without_runtime_is_503(tmp_path: Path) -> None:
    services, _, _ = build(tmp_path, with_runtime=False)
    plan = zoned_plan([sink_rule()], checks_for("in-sink"))
    with owner(services) as client:
        client.post("/plans", json=plan.model_dump(mode="json"))
        reply = client.post("/plans/main-st/approve")
        assert reply.status_code == 503
        assert "RULECAM_RUNTIME=true" in reply.json()["detail"]
        assert client.get("/plans").json()[0]["approved"] is False
    services.activator = None
    with owner(services) as client:
        assert client.post("/plans/main-st/approve").status_code == 503


def test_approve_stays_owner_only(tmp_path: Path) -> None:
    services, loop, _ = build(tmp_path)
    services.plans.put(zoned_plan([sink_rule()], checks_for("in-sink")))
    with TestClient(create_app(services), **TUNNEL) as remote:  # type: ignore[arg-type]
        assert remote.post("/plans/main-st/approve").status_code == 403
    assert loop.plan is None


def test_a_restart_keeps_enforcing_the_approved_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None, rulecam_data_dir=tmp_path, rulecam_runtime=True)
    services = services_from_settings(settings, "s")
    assert services.runtime is not None
    assert services.runtime.plan is None
    plan = zoned_plan([sink_rule()], checks_for("in-sink"))
    with owner(services) as client:
        client.post("/plans", json=plan.model_dump(mode="json"))
        assert client.post("/plans/main-st/approve").status_code == 200
    assert (tmp_path / ACTIVE_PLAN_FILE).is_file()
    restarted = services_from_settings(settings, "s")
    assert restarted.runtime is not None
    assert restarted.runtime.plan == services.runtime.plan
    without_runtime = Settings(_env_file=None, rulecam_data_dir=tmp_path)
    assert services_from_settings(without_runtime, "s").runtime is None


def test_run_calibrations_come_from_the_saved_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store = PlanStore(tmp_path / "plans")
    lookup = run_calibrations(store)
    plan = zoned_plan([sink_rule()], checks_for("in-sink"))
    assert lookup(plan) == {}
    store.save_input(
        PipelineInput(
            plan_id="main-st", site_name="Main St", request=plan.request, calibrations={"cam1": CAL}
        )
    )
    assert lookup(plan) == {"cam1": CAL}
