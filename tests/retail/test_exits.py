"""The typed exit rule: Spark judges "blocked", SAM confirms, code measures how long."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from rulecam.advisor.api import AdviceStore
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.retail.advice import AdviceWriter
from rulecam.retail.brief import BriefWriter
from rulecam.retail.checks import Site
from rulecam.retail.exits import ExitChecker, ExitRule, blocked_since
from rulecam.retail.models import Finding, WatchRequest
from rulecam.retail.service import CardBook, RetailService, image_resolver
from rulecam.retail.staff import StaffDesk
from rulecam.retail.store import FindingLog, TaskBoard, WatchBook
from tests.fakes import FakeClock, FakeLLM
from tests.retail.conftest import AT, FakeFrames, FakeNotifier, FakeSam, Files, image
from tests.retail.test_story import SyncPool

SITE = Site("A supermarket.", {"door": "back fire exit"})


def blocked(yes: bool = True) -> dict[str, Any]:
    return {
        "exit_visible": True,
        "blocked": yes,
        "what": "a pallet of water" if yes else "",
        "box": [400, 500, 700, 900] if yes else None,
        "sam_phrase": "pallet" if yes else "",
        "staff_message": "Move the pallet away from the fire exit." if yes else "",
    }


def service(tmp_path: Path, spark: FakeLLM, clock: FakeClock) -> tuple[RetailService, Any]:
    frame = image(tmp_path / "door.jpg")
    findings = FindingLog(tmp_path / "findings.jsonl")
    tasks = TaskBoard(tmp_path / "tasks.json")
    chain = EventChain(tmp_path / "chain", "demo", clock=clock)
    notifier = FakeNotifier()
    checker = ExitChecker(
        spark=spark,
        files=Files(),
        sam=FakeSam({"pallet": [(170, 110, 270, 170)]}),
        frames=FakeFrames(frame, ("door",)),
        site=SITE,
        clock=clock,
        marks_dir=tmp_path / "marks",
        history=lambda cam, kind: [
            f for f in findings.recent(limit=50, kind=kind) if f.camera_id == cam
        ],
        rule=ExitRule(max_blocked_s=300),
    )
    svc = RetailService(
        checkers={"exit": checker},
        image_checkers={},
        frames=FakeFrames(frame, ("door",)),
        findings=findings,
        watches=WatchBook(tmp_path / "watches.json"),
        tasks=tasks,
        desk=StaffDesk(
            notifier=notifier,
            alerts=lambda _: None,
            tasks=tasks,
            chain=chain,
            clock=clock,
            image_path=image_resolver([tmp_path / "marks", tmp_path]),
        ),
        cards=CardBook(tmp_path / "cards.json"),
        writer=AdviceWriter(
            spark=FakeLLM(),
            runs=tmp_path / "runs.jsonl",
            clock=clock,
            camera_names=SITE.camera_names,
            site_profile=SITE.profile,
        ),
        briefs=BriefWriter(FakeLLM(), clock),
        advice=AdviceStore(),
        chain=chain,
        clock=clock,
        ask_runs=tmp_path / "runs.jsonl",
        camera_names=SITE.camera_names,
        site_profile=SITE.profile,
        pool=SyncPool(),
    )
    return svc, (notifier, chain)


def test_exit_alerts_only_after_the_limit_and_once(tmp_path: Path) -> None:
    clock = FakeClock(AT)
    spark = FakeLLM([blocked(), blocked(), blocked(), blocked(), blocked(yes=False)])
    svc, (notifier, chain) = service(tmp_path, spark, clock)
    minutes = [0, 2, 5, 6, 8]
    found: list[Finding] = []
    for m in minutes:
        found.append(svc.run_check("exit", "door", AT + timedelta(minutes=m)))
        clock.advance(60 if m < 8 else 0)
    assert [f.metric for f in found[:4]] == [0.0, 120.0, 300.0, 360.0]
    assert [f.act for f in found] == [False, False, True, True, False]
    assert len(notifier.sent) == 1  # the 6-minute reading joins the open task
    assert notifier.sent[0]["title"] == "Fire exit blocked: back fire exit"
    assert found[3].task_id == found[2].task_id
    assert found[2].headline.startswith("Fire exit blocked for 5.0 min by a pallet")
    task = svc.tasks.get(str(found[2].task_id))
    assert task is not None
    assert task.kind == "hazard"
    assert task.status == "done"
    assert task.closed_by == "camera"
    labels = [r.payload.get("type") for r in read_chain(chain.path_for(AT.date()))]
    assert labels == ["retail_finding", "hazard_cleared"]


def test_unconfirmed_obstruction_is_not_blocked(tmp_path: Path) -> None:
    clock = FakeClock(AT)
    phantom = {**blocked(), "sam_phrase": "ghost"}
    svc, _ = service(tmp_path, FakeLLM([phantom]), clock)
    finding = svc.run_check("exit", "door", AT)
    assert finding.detail["blocked"] is False
    assert finding.headline == "Fire exit clear"


def test_blocked_since_counts_back_over_the_unbroken_run() -> None:
    def reading(minute: int, yes: bool) -> Finding:
        return Finding(
            id=f"f-{minute}",
            kind="exit",
            camera_id="door",
            at=AT + timedelta(minutes=minute),
            checked_at=AT,
            act=False,
            headline="",
            detail={"blocked": yes},
        )

    newest_first = [reading(4, True), reading(3, True), reading(2, False), reading(1, True)]
    assert blocked_since(newest_first, AT + timedelta(minutes=5)) == AT + timedelta(minutes=3)
    assert blocked_since([], AT) == AT


def test_watch_carries_its_own_limit(tmp_path: Path) -> None:
    clock = FakeClock(AT)
    svc, _ = service(tmp_path, FakeLLM([blocked()]), clock)
    watch = svc.add_watch(WatchRequest(kind="exit", camera_id="door", max_blocked_s=0))
    finding = svc.run_watch(watch.id)
    assert finding.act is True
    assert finding.detail["limit_s"] == 0
