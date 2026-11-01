"""Tests for response drills (P3-S2)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.act import EscalationManager, LanNotifier
from rulecam.act.drills import DrillRunner, DrillStart, drill_report, drills_router, f689_row
from rulecam.bus import Bus
from rulecam.evidence.chain import EventChain, read_chain
from tests.fakes import FakeClock


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.clock = FakeClock()
        self.bus = Bus()
        self.chain = EventChain(tmp_path / "chain", "unit-east", clock=self.clock)
        self.notifier = LanNotifier(bus=self.bus, clock=self.clock)
        self.escalation = EscalationManager(
            notifier=self.notifier, clock=self.clock, chain=self.chain, bus=self.bus
        )
        self.runner = DrillRunner(self.escalation, self.chain, self.clock)

    def records(self) -> list:  # type: ignore[type-arg]
        return read_chain(self.chain.path_for(self.clock.now().date()))


def test_a_drill_runs_through_the_real_chain_labelled_as_a_drill(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    started = rig.runner.start(DrillStart(roles=["aide", "charge_nurse"], after_s=60))
    assert started.event_id.startswith("drill-")
    (alert,) = rig.notifier.list()
    assert alert.title.startswith("DRILL:")
    assert alert.event_id == started.event_id
    assert alert.role == "aide"
    kinds = [r.kind for r in rig.records()]
    assert kinds == ["label", "action"]
    assert rig.records()[0].payload["drill"] is True


def test_report_times_acks_by_role(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    first = rig.runner.start(DrillStart(roles=["aide"], after_s=60))
    rig.clock.advance(20)
    rig.escalation.ack(first.event_id, "aide")
    second = rig.runner.start(DrillStart(roles=["aide", "charge_nurse"], after_s=60))
    rig.clock.advance(60)
    rig.escalation.tick()  # nobody answered: escalates to the charge nurse
    rig.clock.advance(30)
    rig.escalation.ack(second.event_id, "charge_nurse")
    rig.runner.start(DrillStart(roles=["aide"], after_s=60))  # never acknowledged
    report = rig.runner.report(target_s=60)
    assert (report.drills, report.acked, report.missed, report.within_target) == (3, 2, 1, 1)
    assert [(t.role, t.acked, t.median_s) for t in report.by_role] == [
        ("aide", 1, 20.0),
        ("charge_nurse", 1, 90.0),
    ]


def test_only_drills_are_counted_and_only_the_first_ack(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.escalation.start("ev-real", ["aide"], after_s=60, title="Real alert", body="b")
    rig.escalation.ack("ev-real", "aide")
    drill = rig.runner.start(DrillStart(roles=["aide"]))
    rig.clock.advance(10)
    rig.escalation.record_ack(drill.event_id, "aide")
    rig.clock.advance(50)
    rig.escalation.record_ack(drill.event_id, "aide")
    report = drill_report(rig.records())
    assert report.drills == 1
    assert report.by_role[0].median_s == 10.0


def test_report_never_names_a_person(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    drill = rig.runner.start(DrillStart(roles=["aide"]))
    rig.escalation.ack(drill.event_id, "aide")
    report = rig.runner.report()
    assert set(report.model_dump()) == {
        "drills",
        "acked",
        "missed",
        "within_target",
        "target_s",
        "by_role",
    }
    assert set(report.by_role[0].model_dump()) == {"role", "acked", "median_s", "p90_s", "max_s"}


def test_f689_row(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    drill = rig.runner.start(DrillStart(roles=["aide"]))
    rig.clock.advance(15)
    rig.escalation.ack(drill.event_id, "aide")
    rig.runner.start(DrillStart(roles=["aide"]))
    row = f689_row(rig.runner.report(target_s=60))
    assert (row.checks, row.passes) == (2, 1)
    assert row.clause.startswith("F689")
    assert row.corrective_actions == (
        "aide: median 15 s, p90 15 s (1 acked)",
        "1 drill(s) never acknowledged",
    )


def test_routes_start_and_report(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    app = FastAPI()
    app.include_router(drills_router(rig.runner))
    with TestClient(app) as client:
        assert client.get("/drills/report").json()["drills"] == 0
        started = client.post("/drills", json={"roles": ["aide"], "scenario": "Exit door open"})
        assert started.status_code == 201
        assert started.json()["seq"] == 1
        assert client.post("/drills", json={"roles": ["Mrs K"]}).status_code == 422
        assert client.post("/drills", json={"roles": []}).status_code == 422
        assert client.get("/drills/report", params={"target_s": 1}).status_code == 422
        assert client.get("/drills/report").json()["drills"] == 1
