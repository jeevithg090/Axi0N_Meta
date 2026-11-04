"""A supermarket's retail tasks and alerts as a store row on the Chain Mode scorecard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rulecam.chain.retail import (
    HAZARD,
    RESTOCK,
    TILL,
    RetailLimits,
    cameras_by_rule,
    observations,
    store_fit,
    store_observations,
    task_outcome,
    till_outcome,
    window_start,
)
from rulecam.chain.scorecard import chain_scorecard
from rulecam.retail.models import Finding, StaffTask

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
LIMITS = RetailLimits(hazard_minutes=15, restock_minutes=60)


def task(
    kind: str, seen_min_ago: float, fixed_after: float | None, status: str = "open"
) -> StaffTask:
    seen = NOW - timedelta(minutes=seen_min_ago)
    done = seen + timedelta(minutes=fixed_after) if fixed_after is not None else None
    return StaffTask(
        id=f"t-{kind}-{seen_min_ago}",
        kind=kind,
        finding_id="f",
        camera_id="aisle",
        where="aisle 3",
        items=[],
        marked=None,
        seen_at=seen,
        created_at=seen,
        status="done" if done else status,
        done_at=done,
    )


def alert(minute: int) -> Finding:
    at = NOW - timedelta(minutes=minute)
    return Finding(
        id=f"f-{minute}",
        kind="queue",
        camera_id="till",
        at=at,
        checked_at=at,
        act=True,
        headline="",
        alert_id=f"al-{minute}",
    )


def test_task_outcomes() -> None:
    fixed_in_time = task_outcome(task("hazard", 60, 7), LIMITS, NOW)
    late = task_outcome(task("hazard", 60, 30), LIMITS, NOW)
    open_late = task_outcome(task("hazard", 60, None), LIMITS, NOW)
    open_not_due = task_outcome(task("hazard", 5, None), LIMITS, NOW)
    restock = task_outcome(task("shelf", 120, 45), LIMITS, NOW)
    false_alarm = task_outcome(task("hazard", 60, None, "false_alarm"), LIMITS, NOW)
    assert fixed_in_time is not None
    assert fixed_in_time.compliant
    assert late is not None
    assert not late.compliant
    assert open_late is not None
    assert not open_late.compliant
    assert open_not_due is None
    assert false_alarm is None
    assert restock is not None
    assert restock.rule_id == RESTOCK
    assert restock.compliant
    assert fixed_in_time.rule_id == HAZARD
    answered = till_outcome(alert(10), "acknowledged")
    ignored = till_outcome(alert(10), "open")
    assert answered is not None
    assert answered.compliant
    assert answered.rule_id == TILL
    assert ignored is not None
    assert not ignored.compliant
    assert till_outcome(alert(10).model_copy(update={"alert_id": None}), None) is None


def test_store_row_on_the_scorecard() -> None:
    tasks = [task("hazard", 200 - 10 * i, 5 if i < 5 else 40) for i in range(7)]
    tasks.append(task("shelf", 100, 20))
    findings = [alert(30), alert(40)]
    observed = store_observations(
        tasks,
        findings,
        answered={"f-30": "acknowledged", "f-40": None},
        recorded={
            "aisle": (NOW - timedelta(hours=5), NOW),
            "till": (NOW - timedelta(hours=5), NOW),
        },
        limits=LIMITS,
        now=NOW,
        since=NOW - timedelta(hours=24),
    )
    fit = store_fit("demo", "A supermarket", cameras_by_rule(observed), LIMITS)
    begin = window_start(observed, NOW - timedelta(hours=24))
    assert begin == NOW - timedelta(hours=5)
    card = chain_scorecard([fit], observations("demo", observed, begin, NOW))
    scores = {s.rule_id: s for s in card.scores}
    assert (scores[HAZARD].compliant, scores[HAZARD].violations) == (5, 2)
    assert scores[HAZARD].status == "ok"
    assert scores[HAZARD].rate is not None
    assert abs(scores[HAZARD].rate - 5 / 7) < 1e-9
    assert scores[RESTOCK].status == "too_few"  # one restock: below the small-crew floor
    assert scores[TILL].status == "too_few"
    assert card.provenance == "recorded"
    assert fit.ready_count == 3
    assert len(fit.pack_digest) == 64


def test_unrecorded_cameras_are_blind() -> None:
    observed = store_observations(
        [task("hazard", 60, 5)],
        [],
        answered={},
        recorded={},
        limits=LIMITS,
        now=NOW,
        since=NOW - timedelta(hours=24),
    )
    fit = store_fit("demo", "A supermarket", {HAZARD: ["aisle"]}, LIMITS)
    card = chain_scorecard([fit], observations("demo", observed, NOW - timedelta(hours=2), NOW))
    hazard = next(s for s in card.scores if s.rule_id == HAZARD)
    assert hazard.compliant == 0
    assert hazard.blind == 1


def test_staff_photos_count_as_watched() -> None:
    photo_task = task("hazard", 60, 5).model_copy(update={"camera_id": "upload"})
    observed = store_observations(
        [photo_task],
        [],
        answered={},
        recorded={},
        limits=LIMITS,
        now=NOW,
        since=NOW - timedelta(hours=2),
    )
    assert observed.coverage["upload"][0].watched is True
    assert cameras_by_rule(observed) == {HAZARD: ["upload"]}
