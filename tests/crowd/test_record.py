"""The incident record: what the system did not know, and timeline lines linked to the chain."""

from __future__ import annotations

from datetime import UTC, datetime

from rulecam.crowd.record import TimelineEvent, link, unknowns


def test_unknowns_name_every_gap_in_plain_words() -> None:
    alerts: list[dict[str, object]] = [
        {
            "alert_id": "a1",
            "ts": "2026-11-04T00:45:56+00:00",
            "level": "critical",
            "sent": True,
            "check": None,
            "check_note": "Glimmer had no warm worker",
            "route_checks": [{"to": "Hall", "measured": False, "note": "no camera sees it"}],
            "audio": [{"lang": "hi", "meaning": "different", "meaning_note": "gate became crowd"}],
        }
    ]
    readings: list[dict[str, object]] = [
        {"ts": "2026-11-04T00:45:56+00:00", "calibration": "owner", "calibration_err_m": 0.0},
        {"ts": "2026-11-04T00:46:40+00:00", "calibration": "owner", "calibration_err_m": 0.0},
    ]
    acks: dict[str, dict[str, object]] = {
        "a1": {
            "status": "open",
            "notified": [{"role": "stewards"}, {"role": "safety_officer"}],
            "arrival": {"zone": "gate", "checks": 3, "concept": "high-visibility vest"},
        }
    }
    found = unknowns(alerts=alerts, readings=readings, acks=acks, scored=False)
    text = "\n".join(found)
    assert "without Muse Glimmer's look at the frames (Glimmer had no warm worker)" in text
    assert "sent people to Hall, which no camera measured" in text
    assert "told: stewards, safety_officer" in text
    assert "The hi announcement of the critical alert did not survive" in text
    assert "No steward was seen on camera in gate" in text
    assert "No readings for 44 s, from 00:45:56 to 00:46:40." in text
    assert "owner (±0.00 m) floor calibration" in text
    assert found[-1].startswith("No ground truth")


def test_link_ties_lines_to_chain_records_and_adds_arrivals() -> None:
    at = datetime(2026, 11, 4, 0, 46, tzinfo=UTC)
    lines = [
        TimelineEvent(at=at, kind="alert", text="Sent", alert_id="a1"),
        TimelineEvent(at=at, kind="notified", text="told", alert_id="a1", role="stewards"),
        TimelineEvent(at=at, kind="assessment", text="critical"),
    ]
    records: list[dict[str, object]] = [
        {"seq": 4, "kind": "candidate", "hash": "h4", "event_id": "a1"},
        {
            "seq": 3,
            "kind": "action",
            "hash": "h3",
            "event_id": "a1",
            "action": "notify",
            "role": "stewards",
        },
        {
            "seq": 9,
            "kind": "action",
            "hash": "h9",
            "event_id": "a1",
            "action": "arrival",
            "role": "stewards",
            "detail": "1 x vest",
            "ts": "2026-11-04T00:47:00+00:00",
        },
    ]
    out = link(lines, records)
    assert [(e.kind, e.seq) for e in out] == [
        ("alert", 4),
        ("notified", 3),
        ("assessment", None),
        ("arrival", 9),
    ]
