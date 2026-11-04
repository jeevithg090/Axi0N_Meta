"""A hazard's incident report: seen, staff alerted, answered, cleared in N minutes.

Slip-and-fall and blocked-exit cases turn on how long a hazard was there and what the store did.
This builds the same printable incident pack the runtime's alerts get (`evidence.incident`:
the record hashes, the QR code to the browser verify page, PDF by headless Chromium or HTML)
for a retail task, from its own evidence-chain records:

- the detection (`retail_finding`: what the models saw, who was alerted),
- staff acknowledgements (`ack` records of the alert's event),
- the fix (`hazard_cleared` or `gap_refilled`: when, how it was checked, minutes to clear).

Stills are the marked "before" frame and the staff's proof photo with faces blurred the way the
runtime's standard-mode strips are (`runtime.strip_draw.blur_faces`: the face detector when one
is configured, plus the head region of every person SAM 3.1 finds). Raw frames are never put in
a report.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Final

import cv2
import numpy as np

from rulecam.contracts import EventRecord, PrivacyMode
from rulecam.evidence.chain import ChainReport
from rulecam.evidence.incident import IncidentReport, TimelineEntry
from rulecam.evidence.packs import Thumbnail
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter
from rulecam.retail.models import StaffTask
from rulecam.runtime.strip_draw import FaceDetector, Item, blur_faces

_log = get_logger(__name__)

CLAUSES: Final = {
    "hazard": ("Keep aisles and walkways free of slip and trip hazards",),
    "shelf": ("Keep shelves stocked and faced",),
}
FIX_LABELS: Final = frozenset({"hazard_cleared", "gap_refilled", "retail_fixed"})
RULE_TEXT: Final = {
    "hazard": "Floor hazards and blocked exits are found, staff are told, and they are made safe.",
    "shelf": "Empty facings are found, staff are told, and they are refilled.",
}


def _payload_type(record: EventRecord) -> object:
    return record.payload.get("type")


def belongs(record: EventRecord, task: StaffTask) -> bool:
    """Whether a chain record is the task's detection, an ack of its alert, or its fix."""
    kind, label = record.kind, _payload_type(record)
    if kind == "ack":
        return record.payload.get("event_id") == task.finding_id
    if kind != "advice":
        return False
    if label == "retail_finding":
        return record.payload.get("finding_id") == task.finding_id
    return label in FIX_LABELS and record.payload.get("task_id") == task.id


def task_records(records: Sequence[EventRecord], task: StaffTask) -> list[EventRecord]:
    """The task's own chain records, in chain order: detection, acks, fix."""
    return [r for r in records if belongs(r, task)]


def summary(record: EventRecord) -> str:
    """One line for a timeline entry."""
    p = record.payload
    if record.kind == "ack":
        return f"Staff answered ({p.get('role', '?')})"
    if _payload_type(record) == "retail_finding":
        raw = p.get("models")
        models = ", ".join(str(m) for m in raw) if isinstance(raw, list) else ""
        return f"Seen: {p.get('headline', '')}. Alert to '{p.get('role', '?')}' ({models})"
    return f"Cleared after {p.get('minutes_to_fix', '?')} min, checked by {p.get('closed_by', '?')}"


def measurement(task: StaffTask, records: Sequence[EventRecord]) -> str:
    """'Seen 10:02 · alerted 10:02 · answered 10:04 · cleared 10:09 (7.0 min)'."""
    seen = task.seen_at or task.created_at
    parts = [f"Seen {seen:%H:%M:%S}"]
    alerted = next((r for r in records if _payload_type(r) == "retail_finding"), None)
    if alerted is not None:
        parts.append(f"staff alerted {alerted.ts:%H:%M:%S}")
    answered = next((r for r in records if r.kind == "ack"), None)
    if answered is not None:
        parts.append(f"answered {answered.ts:%H:%M:%S}")
    if task.done_at is not None:
        parts.append(f"cleared {task.done_at:%H:%M:%S} ({task.minutes_to_fix} min)")
    else:
        parts.append("not cleared yet")
    return " · ".join(parts)


def masked_still(
    image: Path, sam: ImageSegmenter, faces: FaceDetector, out_dir: Path
) -> Path | None:
    """The image with faces and the head of every person SAM 3.1 finds blurred."""
    pixels = cv2.imread(str(image))
    if pixels is None:
        return None
    people = sam.segment_image(image, "person", camera_id="report")
    items = [Item(concept="person", box=d.mask.box_xyxy, person=True) for d in people]
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{image.stem}-masked.jpg"
    cv2.imwrite(str(target), blur_faces(np.asarray(pixels, dtype=np.uint8), items, faces))
    return target


def task_incident(
    task: StaffTask,
    records: Sequence[EventRecord],
    *,
    site: str,
    day: date,
    verify_url: str,
    chain: ChainReport,
    stills: Sequence[Path],
    now: datetime,
) -> tuple[IncidentReport, tuple[Thumbnail, ...]]:
    """The incident report of one staff task, from its chain records.

    Args:
        task: The task.
        records: The chain records of the days it spans.
        site: The site name.
        day: The day it was seen.
        verify_url: Where the QR code points (the browser verify page for that day).
        chain: That day's chain check.
        stills: Face-blurred stills (before, and the proof photo when there is one).
        now: The time the report is made (the end of an open task).

    Returns:
        The report and its thumbnails.
    """
    mine = task_records(records, task)
    thumbs = tuple(Thumbnail(path=str(p), kind="masked") for p in stills)
    report = IncidentReport(
        event_id=task.id,
        site=site,
        day=day,
        rule_id=f"retail-{task.kind}",
        rule_text=RULE_TEXT[task.kind],
        clauses=CLAUSES[task.kind],
        severity="high" if task.kind == "hazard" else "low",
        camera_id=task.camera_id,
        ts_start=task.seen_at or task.created_at,
        ts_end=task.done_at or now,
        measurement=measurement(task, mine),
        privacy_mode=PrivacyMode.STANDARD,
        timeline=tuple(
            TimelineEntry(seq=r.seq, ts=r.ts, kind=r.kind, summary=summary(r), hash=r.hash)
            for r in mine
        ),
        stills=len(thumbs),
        verify_url=verify_url,
        chain_records=chain.n,
        chain_ok=chain.ok,
    )
    return report, thumbs
