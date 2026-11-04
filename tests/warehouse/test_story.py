"""The connected story of the recorded T02 plan, step by step."""

from __future__ import annotations

from datetime import UTC, datetime

from rulecam.contracts import EventRecord
from rulecam.evidence.chain import ChainReport
from rulecam.warehouse.models import SpokenWarning, StoryStep, TalkSection, ToolboxTalk
from rulecam.warehouse.near_misses import near_misses
from rulecam.warehouse.story import build_story, cant_check, request_text, rule_ci_detail
from tests.warehouse.conftest import T02, T02_PLAN, golden_plan

NOW = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)


def _steps(steps: tuple[StoryStep, ...]) -> dict[str, StoryStep]:
    return {s.key: s for s in steps}


def test_story_follows_the_alerted_incident(t02_records: list[EventRecord]) -> None:
    plan = golden_plan(T02)
    near = near_misses(t02_records, plan_id=T02_PLAN)
    talk = ToolboxTalk(
        event_id=near[0].event_id,
        lang="es",
        title="Cuatro metros",
        minutes=2,
        sections=(TalkSection(heading="Qué pasó", text="..."),),
        key_points=("4 m",),
        questions=(),
        illustration_prompt="safe gap",
        model="muse-spark-1.3",
        incident_hash="h",
        created_at=NOW,
    )
    warning = SpokenWarning(
        event_id=near[0].event_id,
        lang="es",
        text="Aléjese de la carretilla.",
        english="Step away from the forklift.",
        model="muse-spark-1.3",
        created_at=NOW,
    )
    story = build_story(
        plan,
        approved=True,
        active=True,
        near=near,
        chain=ChainReport(ok=True, n=len(t02_records)),
        talk=talk,
        warning=warning,
        regressions=0,
    )
    steps = _steps(story.steps)
    assert story.plan_id == T02_PLAN
    assert story.event_id == near[0].event_id
    assert story.request.startswith("Keep people at least 4 metres")
    assert [s.key for s in story.steps][:5] == [
        "request",
        "plan",
        "rule_ci",
        "approve",
        "violation",
    ]
    assert steps["rule_ci"].status == "done"
    assert "boundary 16/16, mutations 6/6" in steps["rule_ci"].detail
    assert steps["approve"].status == "done"
    assert "3.99 m" in steps["violation"].detail
    assert "limit 4 m" in steps["violation"].detail
    assert steps["verdict"].detail.startswith("violation (0.99)")
    assert steps["warning"].status == "done"
    assert "Aléjese" in steps["warning"].detail
    assert steps["ack"].status == "waiting"
    assert steps["evidence"].status == "done"
    assert steps["toolbox_talk"].status == "done"
    assert steps["dispute"].status == "not_yet"
    assert steps["regression"].status == "not_yet"
    assert story.cant_check[0].status == "caveat"


def test_story_before_any_request_and_before_approval(t02_records: list[EventRecord]) -> None:
    empty = build_story(None, approved=False, active=False, near=[], chain=None)
    assert [s.key for s in empty.steps] == ["request"]
    assert empty.steps[0].status == "not_yet"
    plan = golden_plan(T02)
    waiting = build_story(plan, approved=False, active=False, near=[], chain=None)
    steps = _steps(waiting.steps)
    assert steps["approve"].status == "waiting"
    assert steps["violation"].status == "not_yet"
    broken = build_story(
        plan,
        approved=True,
        active=False,
        near=near_misses(t02_records),
        chain=ChainReport(ok=False, first_bad_seq=3, n=5, reason="hash mismatch"),
        regressions=2,
    )
    steps = _steps(broken.steps)
    assert steps["approve"].status == "failed"
    assert steps["evidence"].status == "failed"
    assert steps["regression"].status == "done"


def test_plan_helpers_read_the_planner_s_own_words() -> None:
    plan = golden_plan(T02)
    text, lang = request_text(plan)
    assert text.startswith("Keep people")
    assert lang == "en"
    limits = cant_check(plan)
    assert limits
    assert limits[0].explanation
    passed, detail = rule_ci_detail(plan)
    assert passed is True
    assert "mutations 6/6" in detail
