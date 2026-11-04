"""The incident record: every timeline line tied to its evidence-chain record, and the gaps.

`link` gives each line of a report's timeline the chain record it rests on (its sequence number
and hash), so a reader can check any line against the hash-chained log, and adds the steward
arrivals SAM saw, which only the chain holds. `unknowns` lists, in plain words, what the system
did not know when it acted: alerts sent without Glimmer's check, destinations nobody measured,
alerts nobody acknowledged, stewards not seen, gaps between readings, the calibration the
densities rest on, and whether anything was scored against ground truth.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from pydantic import Field

from rulecam.contracts import Frozen

MAX_GAP_S: Final = 30.0  # a longer pause between readings is reported as a gap


class TimelineEvent(Frozen):
    """One moment of the event, with the chain record behind it when there is one."""

    at: datetime
    kind: str = Field(
        description="assessment, alert, held, announcement, notified, tap, arrival, peak."
    )
    level: str | None = None
    text: str
    ref: str | None = Field(default=None, description="A heatmap or WAV file name.")
    alert_id: str | None = None
    role: str | None = None
    seq: int | None = Field(default=None, description="The evidence-chain record's number.")
    hash: str | None = Field(default=None, description="The evidence-chain record's hash.")


def link(
    timeline: Sequence[TimelineEvent], records: Sequence[dict[str, object]]
) -> list[TimelineEvent]:
    """Each line with its chain record, plus the arrivals only the chain holds.

    Args:
        timeline: The report's lines.
        records: The alerts' chain records (`seq`, `kind`, `hash`, `ts`, `event_id`, `action`,
            `role`, `detail`).

    Returns:
        The lines, linked where a record matches.
    """
    unused = list(records)
    out = []
    for event in timeline:
        found = _match(event, unused)
        if found is not None:
            unused.remove(found)
        out.append(
            event.model_copy(update={"seq": found["seq"], "hash": found["hash"]})
            if found is not None
            else event
        )
    for record in records:
        if record.get("action") == "arrival" and isinstance(record.get("ts"), str):
            out.append(
                TimelineEvent(
                    at=datetime.fromisoformat(str(record["ts"])),
                    kind="arrival",
                    text=str(record.get("detail", "steward seen on camera")),
                    alert_id=str(record.get("event_id")),
                    role=str(record.get("role")) if record.get("role") else None,
                    seq=_int(record.get("seq")),
                    hash=str(record.get("hash")),
                )
            )
    return out


def _int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _match(event: TimelineEvent, records: Sequence[dict[str, object]]) -> dict[str, object] | None:
    if event.alert_id is None:
        return None
    mine = [r for r in records if r.get("event_id") == event.alert_id]
    if event.kind in {"alert", "held"}:
        wanted = [r for r in mine if r.get("kind") == "candidate"]
    elif event.kind == "notified":
        wanted = [
            r
            for r in mine
            if r.get("kind") == "action"
            and r.get("action") in {"notify", "escalate"}
            and r.get("role") == event.role
        ]
    elif event.kind == "announcement":
        wanted = [
            r
            for r in mine
            if r.get("action") == "speak"
            and event.text.split("] ", 1)[-1].split(" · heard back")[0] in str(r.get("detail"))
        ]
    elif event.kind == "tap":
        wanted = [
            r for r in mine if r.get("kind") in {"ack", "label"} and r.get("role") == event.role
        ]
    else:
        wanted = []
    return wanted[0] if wanted else None


def unknowns(
    *,
    alerts: Sequence[dict[str, object]],
    readings: Sequence[dict[str, object]],
    acks: dict[str, dict[str, object]],
    scored: bool,
) -> list[str]:
    """What the system did not know when it acted, in plain words.

    Args:
        alerts: The window's alert rows.
        readings: The window's reading rows (one per zone).
        acks: Alert id -> its ack state (`status`, `notified`, `arrival`).
        scored: Whether ground truth scored the readings.

    Returns:
        One sentence per gap.
    """
    out: list[str] = []
    for alert in alerts:
        out += _alert_unknowns(alert, acks.get(str(alert.get("alert_id")), {}))
    out += _gaps(readings)
    sources = sorted(
        {
            f"{r.get('calibration')}"
            + (
                f" (±{_float(r['calibration_err_m']):.2f} m)"
                if r.get("calibration_err_m") is not None
                else ""
            )
            for r in readings
        }
    )
    if sources:
        out.append(
            f"Densities rest on the {', '.join(sources)} floor calibration; a zone that "
            "reaches past the calibrated floor is extrapolated."
        )
    out.append("SAM 3.1 counts what the camera sees: people hidden behind others are missed.")
    if not scored:
        out.append("No ground truth was registered for this camera: the counts are not scored.")
    return out


def _alert_unknowns(alert: dict[str, object], state: dict[str, object]) -> list[str]:
    """What one alert went out without: a frame check, a measured route, a tap, a steward seen."""
    out = []
    when = _clock(alert.get("ts"))
    level = alert.get("level")
    if alert.get("sent") and not isinstance(alert.get("check"), dict):
        note = alert.get("check_note") or "no check"
        out.append(
            f"The {level} alert from the {when} reading went out without Muse "
            f"Glimmer's look at the frames ({note})."
        )
    checks = alert.get("route_checks")
    for check in checks if isinstance(checks, list) else []:
        if isinstance(check, dict) and not check.get("measured") and not check.get("replaced"):
            out.append(
                f"The {level} alert sent people to {check.get('to')}, which no camera "
                f"measured ({check.get('note')})."
            )
    audio = alert.get("audio")
    for spoken in audio if isinstance(audio, list) else []:
        if isinstance(spoken, dict) and spoken.get("meaning") == "different":
            out.append(
                f"The {spoken.get('lang')} announcement of the {level} alert did not survive "
                f"being heard back ({spoken.get('meaning_note')}); the page does not play it "
                "by itself."
            )
    roles = state.get("notified")
    told = ", ".join(
        str(n.get("role"))
        for n in (roles if isinstance(roles, list) else [])
        if isinstance(n, dict)
    )
    if alert.get("sent") and state.get("status") == "open":
        out.append(
            f"Nobody acknowledged the {level} alert from the {when} reading "
            f"(told: {told or 'nobody'})."
        )
    arrival = state.get("arrival")
    if isinstance(arrival, dict) and not arrival.get("seen_at"):
        out.append(
            f"No steward was seen on camera in {arrival.get('zone')} after the "
            f"{level} alert was acknowledged ({arrival.get('checks')} looks for "
            f"'{arrival.get('concept')}')."
        )
    return out


def _float(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _gaps(readings: Sequence[dict[str, object]]) -> list[str]:
    times = sorted({str(r.get("ts")) for r in readings})
    out = []
    for before, after in itertools.pairwise(times):
        gap = (datetime.fromisoformat(after) - datetime.fromisoformat(before)).total_seconds()
        if gap > MAX_GAP_S:
            out.append(f"No readings for {gap:.0f} s, from {_clock(before)} to {_clock(after)}.")
    return out


def _clock(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M:%S")
    except ValueError:
        return str(value)


__all__ = ["MAX_GAP_S", "TimelineEvent", "link", "unknowns"]
