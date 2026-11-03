"""One-click incident report (add-on 5.2): one alert's chain records as a printable pack.

`packs.py` renders period-wide evidence packs (clause → rule → compliance rate). An incident
report is the same idea for **one event**: the clause and rule that fired, the measurement, every
verdict, action, acknowledgement, label and dispute chained for that event in order (each with its
record hash), the anonymised stills the privacy mode allows, and the QR code to the verify page.

It reuses the pack machinery: stills are inlined as data URIs (`packs.image_data_uri`) and
filtered by `packs.allowed_thumbnails`, so a report never names a file on the server; the QR is
`packs.qr_data_uri`; the PDF is printed by `packs.render_pdf` (Playwright and Chromium). When
Chromium is not installed, `report_document` falls back to the printable HTML.

Model text in verdicts (`reason`) is data: Jinja2 auto-escapes it, and nothing in it is followed.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from rulecam.contracts import EventRecord, Frozen, PrivacyMode
from rulecam.evidence.chain import ChainReport, read_chain
from rulecam.evidence.packs import (
    Thumbnail,
    allowed_thumbnails,
    image_data_uri,
    qr_data_uri,
    render_pdf,
    render_template,
)
from rulecam.log import get_logger

_log = get_logger(__name__)

MAX_STILLS = 6
MAX_REASON_CHARS = 400
EVENT_KINDS = frozenset({"candidate", "verdict", "action", "ack", "label", "dispute"})
DEFAULT_CLAUSE = "Site rule"

PdfPrinter = Callable[[str, Path], Path]


class TimelineEntry(Frozen):
    """One chained record of the incident, in chain order."""

    seq: int
    ts: datetime
    kind: str
    summary: str
    hash: str


class IncidentSummary(Frozen):
    """One row of `GET /incidents`: an event that fired on a day."""

    event_id: str
    rule_id: str
    camera_id: str
    severity: str
    ts: datetime
    verdict: str | None = None


class IncidentReport(Frozen):
    """Everything an incident report shows (`GET /incidents/{event_id}`).

    `stills` counts the anonymised stills the privacy mode allows; their paths are never served.
    """

    event_id: str
    site: str
    day: date
    rule_id: str
    rule_text: str
    clauses: tuple[str, ...]
    severity: str
    camera_id: str
    ts_start: datetime
    ts_end: datetime
    measurement: str | None
    privacy_mode: PrivacyMode
    timeline: tuple[TimelineEntry, ...]
    stills: int
    verify_url: str
    chain_records: int
    chain_ok: bool


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def event_of(record: EventRecord) -> str | None:
    """The event id a chain record belongs to (a candidate's, or the payload's `event_id`)."""
    candidate = _dict(record.payload.get("candidate"))
    event_id = candidate.get("event_id", record.payload.get("event_id"))
    return event_id if isinstance(event_id, str) else None


def candidates(records: Iterable[EventRecord]) -> list[IncidentSummary]:
    """Every candidate on a day, newest first, with its first verdict."""
    verdicts: dict[str, str] = {}
    found: list[IncidentSummary] = []
    for record in records:
        event_id = event_of(record)
        if event_id is None:
            continue
        if record.kind == "verdict":
            verdicts.setdefault(event_id, str(record.payload.get("verdict", "")))
        elif record.kind == "candidate":
            candidate = _dict(record.payload.get("candidate"))
            found.append(
                IncidentSummary(
                    event_id=event_id,
                    rule_id=str(candidate.get("rule_id", "")),
                    camera_id=str(candidate.get("camera_id", "")),
                    severity=str(candidate.get("severity", "")),
                    ts=record.ts,
                )
            )
    rows = [row.model_copy(update={"verdict": verdicts.get(row.event_id)}) for row in found]
    return sorted(rows, key=lambda row: row.ts, reverse=True)


def find_incident(chain_dir: Path, event_id: str) -> tuple[date, list[EventRecord]] | None:
    """Find the day whose chain holds `event_id`'s candidate; return the day and its records.

    Args:
        chain_dir: The site's chain folder (`<root>/<site>`), one `<YYYY-MM-DD>.jsonl` per day.
        event_id: The event to look for.

    Returns:
        The day and every record of that day's chain, or `None` when no day has the event.
    """
    for path in sorted(chain_dir.glob("*.jsonl"), reverse=True) if chain_dir.is_dir() else []:
        try:
            day = date.fromisoformat(path.stem)
            records = read_chain(path)
        except ValueError:
            _log.warning("incident_chain_unreadable", name=path.name)
            continue
        if any(r.kind == "candidate" and event_of(r) == event_id for r in records):
            return day, records
    return None


def _measurement(candidate: Mapping[str, object]) -> str | None:
    measurement = _dict(candidate.get("measurement"))
    value = measurement.get("value")
    if not isinstance(value, int | float):
        return None
    return f"{value:.2f} {measurement.get('unit', '')}".strip()


def _summary(record: EventRecord) -> str:
    p = record.payload
    if record.kind == "candidate":
        candidate = _dict(p.get("candidate"))
        measured = _measurement(candidate)
        where = f" on {candidate.get('camera_id', '?')}"
        return f"Rule {candidate.get('rule_id', '?')} fired{where}" + (
            f", measured {measured}" if measured else ""
        )
    if record.kind == "verdict":
        reason = str(p.get("reason", ""))[:MAX_REASON_CHARS]
        score = p.get("score")
        scored = f" (score {score:.2f})" if isinstance(score, int | float) else ""
        return f"{p.get('tier', 'verifier')}: {p.get('verdict', '?')}{scored}. {reason}".strip()
    if record.kind == "action":
        role = f" to {p['role']}" if p.get("role") else ""
        failed = "" if p.get("ok", True) else " (failed)"
        return f"{p.get('kind', 'action')}{role}: {p.get('detail', '')}{failed}".strip()
    if record.kind == "ack":
        return f"Acknowledged by {p.get('role', '?')}"
    if record.kind == "label":
        return f"Labelled {p.get('label', '?')} by {p.get('role') or p.get('source') or '?'}"
    return f"Dispute {p.get('outcome', p.get('status', ''))}".strip()


def _stills(paths: Sequence[str], mode: PrivacyMode, root: Path | None) -> tuple[Thumbnail, ...]:
    """The incident's strip as thumbnails, only from under `root`, filtered by privacy mode.

    The runtime renders skeletons in anonymous and strict modes and face-blurred frames in
    standard mode, so the kind follows the mode.
    """
    if root is None:
        return ()
    kind: Literal["skeleton", "masked"] = "masked" if mode is PrivacyMode.STANDARD else "skeleton"
    base = root.resolve()
    kept: list[Thumbnail] = []
    for raw in paths[:MAX_STILLS]:
        path = Path(raw).resolve()
        if path.is_relative_to(base) and path.is_file():
            kept.append(Thumbnail(path=str(path), kind=kind))
    return allowed_thumbnails(kept, mode)


def build_incident(
    records: Sequence[EventRecord],
    event_id: str,
    *,
    site: str,
    day: date,
    privacy_mode: PrivacyMode,
    verify_url: str,
    chain: ChainReport,
    stills_root: Path | None = None,
) -> tuple[IncidentReport, tuple[Thumbnail, ...]]:
    """Build the report of one event from its day's chain.

    Args:
        records: The day's chain records.
        event_id: The event (its candidate must be among `records`).
        site: The site name.
        day: The chain's day.
        privacy_mode: Which stills may be shown.
        verify_url: Where the QR code points.
        chain: The day's verification result.
        stills_root: Only stills under this folder are inlined (the runtime's strips).

    Returns:
        The report and the thumbnails it may show.

    Raises:
        KeyError: If the event has no candidate in `records`.
    """
    mine = [r for r in records if r.kind in EVENT_KINDS and event_of(r) == event_id]
    first = next((r for r in mine if r.kind == "candidate"), None)
    if first is None:
        raise KeyError(event_id)
    candidate = _dict(first.payload.get("candidate"))
    rule = _dict(_dict(first.payload.get("context")).get("rule"))
    source = _dict(rule.get("source"))
    standards = source.get("standard")
    clauses = tuple(str(s) for s in standards) if isinstance(standards, list) else ()
    strips = candidate.get("strip_paths")
    thumbnails = _stills(
        [str(s) for s in strips] if isinstance(strips, list) else [], privacy_mode, stills_root
    )
    report = IncidentReport(
        event_id=event_id,
        site=site,
        day=day,
        rule_id=str(candidate.get("rule_id", "")),
        rule_text=str(source.get("text", "")),
        clauses=clauses or (DEFAULT_CLAUSE,),
        severity=str(candidate.get("severity", "")),
        camera_id=str(candidate.get("camera_id", "")),
        ts_start=datetime.fromisoformat(str(candidate.get("ts_start", first.ts.isoformat()))),
        ts_end=datetime.fromisoformat(str(candidate.get("ts_end", first.ts.isoformat()))),
        measurement=_measurement(candidate),
        privacy_mode=privacy_mode,
        timeline=tuple(
            TimelineEntry(seq=r.seq, ts=r.ts, kind=r.kind, summary=_summary(r), hash=r.hash)
            for r in mine
        ),
        stills=len(thumbnails),
        verify_url=verify_url,
        chain_records=chain.n,
        chain_ok=chain.ok,
    )
    return report, thumbnails


def render_incident_html(report: IncidentReport, thumbnails: Sequence[Thumbnail]) -> str:
    """Render the report as one self-contained, printable HTML page (auto-escaped)."""
    return render_template(
        "incident.html.j2",
        report=report,
        stills=[image_data_uri(Path(t.path)) for t in thumbnails],
        qr=qr_data_uri(report.verify_url),
    )


def report_document(html: str, printer: PdfPrinter = render_pdf) -> tuple[bytes, str]:
    """The report as a PDF, or the printable HTML when Chromium cannot print it.

    Args:
        html: The rendered report.
        printer: Prints HTML to a PDF file (`packs.render_pdf`; a fake in tests).

    Returns:
        The document's bytes and its media type (`application/pdf` or `text/html`).
    """
    with tempfile.TemporaryDirectory(prefix="rulecam-incident-") as tmp:
        out = Path(tmp) / "incident.pdf"
        try:
            return printer(html, out).read_bytes(), "application/pdf"
        except Exception as exc:  # no playwright, no browser, or the browser failed to launch
            _log.warning("incident_pdf_unavailable", error=type(exc).__name__)
    return html.encode("utf-8"), "text/html"
