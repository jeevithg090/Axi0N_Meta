"""The staff feed over the real notifier, escalation chains and evidence chain."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from rulecam.staff.feed import StaffError, UnknownItemError, percentile, record_words, shift_of
from rulecam.staff.models import StaffActionRequest
from rulecam.staff.store import ShelfTask, StaffStore
from tests.fakes import EPOCH
from tests.staff.conftest import Rig


def tap(action: str, role: str = "aisle_lead", **extra: object) -> StaffActionRequest:
    return StaffActionRequest.model_validate({"action": action, "role": role, **extra})


def test_a_verified_rule_alert_reaches_its_role_with_evidence_and_a_countdown(rig: Rig) -> None:
    rig.rule_alert(roles=("aisle_lead", "store_manager"))
    (item,) = rig.feed.items("aisle_lead")
    assert item.source == "rule"
    assert item.roles == ("aisle_lead",)
    assert item.camera_name == "Aisle 4"
    assert item.rule_text == "Keep aisle 4 clear of pallets."
    assert item.measurement == "0.42 m"
    assert item.verdict is not None
    assert (item.verdict.model, item.verdict.confidence) == ("muse-glimmer-30b", 0.91)
    assert item.thumbnails == 1
    assert item.escalation_step == "1/2"
    assert item.next_role == "store_manager"
    assert item.escalates_at == rig.clock.now() + timedelta(seconds=60)
    assert [s.stage for s in item.timeline] == ["raised", "verified", "assigned"]
    assert rig.feed.items("store_manager") == []
    assert rig.feed.media("ev-1", 0) is not None
    assert rig.feed.media("ev-1", 1) is None


def test_acknowledging_stops_the_escalation_and_is_chained(rig: Rig) -> None:
    rig.rule_alert(roles=("aisle_lead", "store_manager"))
    rig.clock.advance(12)
    result = rig.feed.act("ev-1", tap("acknowledge"))
    assert result.item.status == "acknowledged"
    assert result.item.ack_s == 12.0
    assert result.item.next_role is None
    assert not rig.escalation.is_open("ev-1")
    rig.clock.advance(120)
    rig.escalation.tick()
    assert rig.feed.items("store_manager") == []  # nobody else was woken
    kinds = [r.kind for r in rig.records()]
    assert kinds[-2:] == ["ack", "action"]
    last = rig.records()[-1].payload
    assert last["source"] == "staff_app"
    assert last["approved_by_role"] == "aisle_lead"
    assert result.item.actions[0].chain_hash == rig.records()[-1].hash


def test_on_my_way_then_done_with_a_photo_times_the_fix(rig: Rig) -> None:
    rig.rule_alert()
    rig.clock.advance(5)
    rig.feed.act("ev-1", tap("on_my_way"))
    rig.clock.advance(55)
    photo = rig.tmp / "staff" / "photos" / "done.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"\xff\xd8proof\xff\xd9")
    done = rig.feed.act("ev-1", tap("done", note="pallet moved"), photo=photo).item
    assert done.status == "done"
    assert done.fix_s == 60.0
    assert done.thumbnails == 2
    stages = [s.stage for s in done.timeline]
    assert stages[-2:] == ["on_my_way", "done"]
    assert "pallet moved" in done.timeline[-1].label
    payload = rig.records()[-1].payload
    assert len(str(payload["photo_sha256"])) == 64
    assert [r.kind for r in rig.records()].count("ack") == 1


def test_false_alarm_goes_to_rule_health_and_rule_ci(rig: Rig) -> None:
    rig.rule_alert()
    item = rig.feed.act("ev-1", tap("false_alarm")).item
    assert item.status == "false_alarm"
    assert rig.false_alarms == [("ev-1", "keep-aisle-clear")]
    labels = [r.payload for r in rig.records() if r.kind == "label"]
    assert labels == [
        {
            "event_id": "ev-1",
            "rule_id": "keep-aisle-clear",
            "label": "false_alarm",
            "role": "aisle_lead",
        }
    ]


def test_escalate_moves_the_chain_on_or_asks_who_to_send_it_to(rig: Rig) -> None:
    rig.rule_alert(roles=("aisle_lead", "store_manager"))
    result = rig.feed.act("ev-1", tap("escalate"))
    assert result.did == "escalated to store_manager"
    assert rig.feed.items("store_manager")[0].item_id == "ev-1"
    with pytest.raises(StaffError):
        rig.feed.act("ev-1", tap("escalate", role="store_manager"))
    sent = rig.feed.act("ev-1", tap("escalate", role="store_manager", to_role="area_manager"))
    assert sent.did == "sent to area_manager"
    assert sent.item.roles == ("aisle_lead", "store_manager", "area_manager")


def test_unknown_items_are_refused(rig: Rig) -> None:
    with pytest.raises(UnknownItemError):
        rig.feed.act("nope", tap("acknowledge"))
    with pytest.raises(UnknownItemError):
        rig.feed.seen("nope", "aisle_lead", None)
    assert rig.feed.item("nope") is None
    assert rig.feed.media("nope", 0) is None


def test_crowd_warnings_show_what_to_do_and_what_was_announced(rig: Rig) -> None:
    rig.crowd_alert()
    (item,) = rig.feed.items("steward")
    assert item.source == "crowd"
    assert item.do_now == ("Hold entry at gate 3",)
    assert item.announcements == ({"lang": "hi", "text": "kripya"},)
    assert item.next_role is None


def test_dry_run_alerts_show_who_would_have_been_told(tmp_path: Path) -> None:
    rig = Rig(tmp_path, dry_run=True)
    rig.candidate("ev-9")
    rig.chain.append(
        "action",
        {
            "event_id": "ev-9",
            "kind": "escalate",
            "detail": "dry-run: would escalate aisle_lead → store_manager",
            "ts": rig.clock.now().isoformat(),
            "ok": True,
        },
    )
    (item,) = rig.feed.items()
    assert item.dry_run
    assert item.roles == ("aisle_lead", "store_manager")
    assert item.title == "Alert: Keep aisle 4 clear of pallets."
    rig.feed.act("ev-9", tap("acknowledge"))
    assert rig.feed.items("store_manager")[0].status == "acknowledged"


def test_shelf_tasks_are_chained_and_can_be_escalated(rig: Rig) -> None:
    task = ShelfTask(
        task_id="shelf-ab-1",
        role="stock",
        ts=rig.clock.now(),
        section="left bay, shelf 3",
        neighbours="oat milk",
        facings=4,
        product_cover=0.05,
    )
    item = rig.feed.add_task(task)
    assert item.source == "shelf"
    assert item.verdict is not None
    assert item.verdict.confidence == 0.95
    assert rig.records()[-1].payload["event_id"] == "shelf-ab-1"
    sent = rig.feed.act("shelf-ab-1", tap("escalate", role="stock", to_role="floor_lead"))
    assert sent.item.roles == ("stock", "floor_lead")
    assert len(rig.feed.items()) == 1


def test_roles_come_from_plans_alerts_and_tasks(rig: Rig) -> None:
    rig.rule_alert()
    rig.crowd_alert()
    roles = {r.role: r.sources for r in rig.feed.roles()}
    assert roles == {"aisle_lead": ("rule",), "floor_lead": ("plan",), "steward": ("crowd",)}


def test_stats_are_per_role_and_shift_with_phone_latency(rig: Rig) -> None:
    rig.rule_alert(roles=("aisle_lead", "store_manager"))
    rig.clock.advance(0.4)
    assert rig.feed.seen("ev-1", "aisle_lead", 380.0) == 400.0
    assert rig.feed.seen("ev-1", "aisle_lead", 380.0) is None
    rig.clock.advance(60)
    rig.escalation.tick()  # nobody answered: the store manager is told
    rig.clock.advance(10)
    rig.feed.act("ev-1", tap("acknowledge", role="store_manager"))
    stats = rig.feed.stats()
    by_role = {b.key: b for b in stats.by_role}
    assert by_role["aisle_lead"].acknowledged == 0
    assert by_role["aisle_lead"].escalated == 1
    assert by_role["aisle_lead"].phone_p50_ms == 400.0
    assert by_role["store_manager"].ack_p50_s == 10.0
    shift = {b.key: b for b in stats.by_shift}["early"]  # 12:00 UTC
    assert (shift.items, shift.ack_p50_s) == (1, 70.4)


def test_the_store_survives_a_restart(rig: Rig) -> None:
    rig.rule_alert()
    rig.feed.act("ev-1", tap("acknowledge"))
    rig.feed.seen("ev-1", "aisle_lead", None)
    again = StaffStore(rig.feed.store.path)
    assert [a.action for a in again.actions("ev-1")] == ["acknowledge"]
    assert len(again.sightings()) == 1


def test_helpers() -> None:
    assert percentile([], 50) is None
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert shift_of(EPOCH.replace(hour=23), __import__("zoneinfo").ZoneInfo("UTC")) == "night"
    assert shift_of(EPOCH.replace(hour=15), __import__("zoneinfo").ZoneInfo("UTC")) == "late"
    assert record_words({"type": "retail_fixed", "minutes_to_fix": 7.5, "closed_by": "proof"}) == (
        "retail fixed: proof; fixed in 7.5 min"
    )
    assert record_words({"_kind": "dispute"}) == "dispute"


def test_a_restart_rebuilds_cards_from_the_chain_with_the_whole_strip(rig: Rig) -> None:
    folder = rig.strips / "cam-1" / "ev-7"
    folder.mkdir(parents=True)
    for k in (2, 1, 10):
        (folder / f"{k}.jpg").write_bytes(b"\xff\xd8x\xff\xd9")
    rig.candidate("ev-7")
    rig.verdict("ev-7")
    rig.escalation.start(
        "ev-7",
        ("aisle_lead", "store_manager"),
        after_s=60,
        title="t",
        body="b",
        clip_path=str(folder / "1.jpg"),
    )
    live = rig.feed.item("ev-7")
    assert live is not None
    assert live.thumbnails == 3
    assert rig.feed.media("ev-7", 2) == (folder / "10.jpg").resolve()
    rig.notifier._alerts.clear()  # the notifier forgets every alert on an API restart
    (item,) = rig.feed.items("aisle_lead")
    assert item.item_id == "ev-7"
    assert item.title == "Alert: Keep aisle 4 clear of pallets."
    assert item.thumbnails == 3
    assert not item.dry_run
