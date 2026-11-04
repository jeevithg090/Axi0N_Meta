"""Planner step timings from the saved run, and the story of a plan still being planned."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.contracts.plan import RequestItem
from rulecam.planner.pipeline import PipelineInput, PlanStore
from rulecam.warehouse.api import PlanSource, WarehouseService, warehouse_router
from rulecam.warehouse.planning import planning_timeline
from rulecam.warehouse.talks import Store

T0 = 1_793_750_000.0
PLAN = "plan-abc123def456"


def _touch(path: Path, at: float, text: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (at, at))


def _run(tmp_path: Path, done: dict[str, float]) -> PlanStore:
    store = PlanStore(tmp_path / "plans")
    store.save_input(
        PipelineInput(
            plan_id=PLAN,
            site_name="demo",
            request=(RequestItem(kind="text", text="Mantener 4 m", lang="es"),),
        )
    )
    os.utime(store.input_path(PLAN), (T0, T0))
    for step, at in done.items():
        survey = '{"reused": {"phone-1": {"plan_id": "plan-old", "age_s": 95.0, "bits": 3}}}'
        _touch(store.step_path(PLAN, step), at, survey if step == "survey" else "{}")  # type: ignore[arg-type]
    return store


def test_timeline_reads_each_step_from_its_file_time(tmp_path: Path) -> None:
    store = _run(tmp_path, {"understand": T0 + 0.4, "survey": T0 + 1.5, "probe": T0 + 20.0})
    now = datetime.fromtimestamp(T0 + 26.0, tz=UTC)
    timeline = planning_timeline(store, PLAN, now)
    assert timeline is not None
    steps = {s.step: s for s in timeline.steps}
    assert steps["understand"].seconds == 0.4
    assert steps["survey"].seconds == 1.1
    assert "reused phone-1's survey from plan-old (95 s old" in steps["survey"].detail
    assert steps["probe"].seconds == 18.5
    assert (steps["gauge"].status, steps["gauge"].seconds) == ("running", 6.0)
    assert steps["prove"].status == "pending"
    assert timeline.total_s is None
    assert planning_timeline(store, "plan-000000000000", now) is None


def test_a_finished_run_has_a_total(tmp_path: Path) -> None:
    from rulecam.planner.pipeline import PIPELINE_STEPS  # noqa: PLC0415

    store = _run(tmp_path, {step: T0 + 5.0 * (i + 1) for i, step in enumerate(PIPELINE_STEPS)})
    timeline = planning_timeline(store, PLAN, datetime.fromtimestamp(T0 + 99, tz=UTC))
    assert timeline is not None
    assert timeline.total_s == 50.0
    assert {s.status for s in timeline.steps} == {"done"}


def test_story_of_a_plan_still_planning(tmp_path: Path) -> None:
    store = _run(tmp_path, {"understand": T0 + 0.4})
    service = WarehouseService(
        chain_dir=tmp_path / "chain",
        regressions_dir=tmp_path / "reg",
        stills_root=None,
        plans=PlanSource(list, frozenset),
        store=Store(tmp_path / "w"),
        plan_store=store,
    )
    app = FastAPI()
    app.include_router(warehouse_router(service))
    client = TestClient(app)
    story = client.get("/warehouse/story", params={"plan_id": PLAN}).json()
    assert story["request"] == "Mantener 4 m"
    assert story["steps"][1]["status"] == "waiting"
    assert story["steps"][1]["detail"].startswith("Planning: survey running")
    assert story["planning"]["steps"][0]["status"] == "done"
    assert (
        client.get("/warehouse/story", params={"plan_id": "plan-ffffffffffff"}).status_code == 404
    )
