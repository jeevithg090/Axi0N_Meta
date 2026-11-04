"""After the event: one camera's crowd report, with its evidence and, when known, the truth.

`build_report` reads what the crowd watch wrote (readings, Spark's assessments, alerts, taps; the
JSON-lines files Ask also loads) for one camera and window and returns an `EventReport`:

- **per zone:** the series, the peak (with its heatmap), the mean, and the time spent at or above
  each published reference density;
- **a timeline:** every change of Spark's level, each alert (sent or held, with Glimmer's check
  and why it has none), the announcements, who was notified and who tapped;
- **latency:** reading to Spark's assessment to the alert, per alert;
- **evidence:** each alert's chain records and whether the chain files verify;
- **ground truth**, when a truth file is registered for the camera (`PUT /crowd/truth/{camera}`):
  people and density per zone against the true counts at the same clip time, scored as the count
  ratio, the mean count error, the median density ratio and the peaks.

`narrate` adds Muse Spark's after-action summary written from the report. Nothing here judges.
"""

from __future__ import annotations

import itertools
import json
import statistics
from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Final

from pydantic import Field

from rulecam.contracts import Frozen, StructuredLLM
from rulecam.crowd.forecast import REFERENCE_PPM2
from rulecam.crowd.record import TimelineEvent, link, unknowns
from rulecam.evidence.chain import EventChain, read_chain, verify_chain
from rulecam.log import get_logger

_log = get_logger(__name__)

SPARK_MODEL: Final = "muse-spark-1.3"
MAX_GAP_S: Final = 30.0  # a longer gap between readings is not counted as time at a density


class ZoneSeries(Frozen):
    """One zone's readings as columns."""

    t: list[str] = Field(default_factory=list)
    clip_s: list[float | None] = Field(default_factory=list)
    people: list[int] = Field(default_factory=list)
    density_ppm2: list[float | None] = Field(default_factory=list)
    speed_mps: list[float | None] = Field(default_factory=list)
    pressure: list[float | None] = Field(default_factory=list)


class ZoneSummary(Frozen):
    """One zone over the window."""

    zone: str
    readings: int
    peak_density_ppm2: float | None = None
    peak_at: datetime | None = None
    peak_people: int | None = None
    peak_heatmap: str | None = None
    mean_density_ppm2: float | None = None
    above_s: dict[str, float] = Field(
        default_factory=dict, description="Reference density -> seconds at or above it."
    )
    max_pressure: float | None = None
    max_counterflow_share: float | None = None


class AlertLatency(Frozen):
    """How long one alert took from the reading behind it."""

    alert_id: str
    level: str
    sent: bool
    reading_at: datetime
    assess_s: float | None = None
    alert_s: float | None = None
    checked: str = Field(description="confirmed, rejected, or why there was no check.")


class TruthZone(Frozen):
    """One zone against its ground truth."""

    zone: str
    readings: int
    count_ratio: float | None = None
    count_mae: float | None = None
    density_ratio_median: float | None = None
    sys_peak_ppm2: float | None = None
    true_peak_ppm2: float | None = None
    true_peak_clip_s: float | None = None
    clip_s: list[float] = Field(default_factory=list)
    sys_people: list[int] = Field(default_factory=list)
    true_people: list[int] = Field(default_factory=list)
    sys_density: list[float | None] = Field(default_factory=list)
    true_density: list[float | None] = Field(default_factory=list)
    truth_curve_s: list[float] = Field(default_factory=list)
    truth_curve_density: list[float | None] = Field(default_factory=list)


class Evidence(Frozen):
    """The alerts' records on the evidence chain."""

    chain_ok: bool | None = None
    chain_files: list[str] = Field(default_factory=list)
    problem: str | None = None
    records: list[dict[str, object]] = Field(default_factory=list)


class CitedSentence(Frozen):
    """One sentence of the after-action summary and the chain records it rests on."""

    text: str
    records: list[int] = Field(
        default_factory=list, description="`seq` of the evidence records it rests on; [] if none."
    )


class AfterAction(Frozen):
    """Muse Spark's after-action summary of the report."""

    headline: str = Field(description="One sentence: what happened and how the system did.")
    narrative: str = Field(description="Four to six sentences with times and numbers.")
    cited: list[CitedSentence] = Field(
        default_factory=list,
        description="The narrative again, one sentence each, citing the evidence records.",
    )
    worked: list[str] = Field(default_factory=list, description="What worked, short.")
    change: list[str] = Field(
        default_factory=list, description="What to change before the next event, short."
    )


class EventReport(Frozen):
    """`GET /crowd/report`."""

    camera_id: str
    name: str
    since: datetime | None = None
    until: datetime | None = None
    clip_start: datetime | None = None
    instruction: str | None = None
    readings: int = 0
    reference_ppm2: list[float] = Field(default_factory=lambda: list(REFERENCE_PPM2))
    series: dict[str, ZoneSeries] = Field(default_factory=dict)
    zones: list[ZoneSummary] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    unknowns: list[str] = Field(
        default_factory=list, description="What the system did not know when it acted."
    )
    latency: list[AlertLatency] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)
    truth: list[TruthZone] | None = None
    truth_source: str | None = None
    after_action: AfterAction | None = None


class Truth(Frozen):
    """True people per zone over a clip's time (from published trajectories or a hand count)."""

    source: str = Field(max_length=500)
    licence: str = Field(default="", max_length=200)
    clip_start_utc: datetime | None = Field(
        default=None, description="When clip second 0 was recorded (default: the first chunk)."
    )
    zones: dict[str, TruthSeries]
    view: TruthSeries | None = None


class TruthSeries(Frozen):
    """True people in one zone at clip seconds (and the zone's true area)."""

    area_m2: float | None = None
    t: list[float]
    people: list[int]


Truth.model_rebuild()


def _rows(
    path: Path, camera_id: str, since: datetime | None, until: datetime | None
) -> Iterator[dict[str, object]]:
    """The JSON-lines rows of one camera inside the window (bad lines skipped)."""
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("camera_id") != camera_id:
                continue
            at = _at(row.get("ts"))
            if at is None or (since is not None and at < since) or (until and at > until):
                continue
            yield row


def _at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _int(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0


def build_report(
    *,
    camera_id: str,
    name: str,
    readings_path: Path,
    assessments_path: Path,
    alerts_path: Path,
    acks_path: Path,
    chain: EventChain | None,
    since: datetime | None = None,
    until: datetime | None = None,
    clip_start: datetime | None = None,
    instruction: str | None = None,
    truth: Truth | None = None,
    acks: dict[str, dict[str, object]] | None = None,
) -> EventReport:
    """One camera's report over a window.

    Args:
        camera_id: The camera.
        name: The owner's name for it.
        readings_path: `crowd_readings` JSON lines.
        assessments_path: `crowd_assessments` JSON lines.
        alerts_path: `crowd_alerts` JSON lines.
        acks_path: Taps made on the live page.
        chain: The evidence chain (`None`: no evidence section).
        since: Window start (default: everything).
        until: Window end.
        clip_start: When clip second 0 was recorded (for `clip_s` and the truth).
        instruction: The operator's instruction.
        truth: The camera's ground truth, if registered.
        acks: Alert id -> its ack state as JSON (`status`, `notified`, `taps`, `arrival`).

    Returns:
        The report.
    """
    if truth is not None and truth.clip_start_utc is not None:
        clip_start = truth.clip_start_utc
    by_zone: dict[str, list[dict[str, object]]] = defaultdict(list)
    instants: set[str] = set()
    for row in _rows(readings_path, camera_id, since, until):
        by_zone[str(row.get("zone"))].append(row)
        instants.add(str(row["ts"]))
    series = {zone: _series(rows, clip_start) for zone, rows in by_zone.items()}
    zones = [_summary(zone, rows) for zone, rows in by_zone.items()]
    assessments = list(_rows(assessments_path, camera_id, since, until))
    alerts = list(_rows(alerts_path, camera_id, since, until))
    alert_ids = {str(a.get("alert_id")) for a in alerts}
    taps = [t for t in _all_rows(acks_path) if str(t.get("alert_id")) in alert_ids]
    states = {k: v for k, v in (acks or {}).items() if k in alert_ids}
    notified = [
        {"alert_id": alert_id, **note}
        for alert_id, state in states.items()
        for note in _notes(state)
    ]
    timeline = _timeline(assessments, alerts, taps, notified)
    timeline += [
        TimelineEvent(
            at=z.peak_at,
            kind="peak",
            text=f"{z.zone}: peak {z.peak_density_ppm2:g} people/m² ({z.peak_people} people)",
            ref=z.peak_heatmap,
        )
        for z in zones
        if z.peak_at is not None and z.peak_density_ppm2 is not None
    ]
    evidence = _evidence(chain, alerts)
    scored = _truth(truth, by_zone, clip_start) if truth is not None and clip_start else None
    return EventReport(
        camera_id=camera_id,
        name=name,
        since=since,
        until=until,
        clip_start=clip_start,
        instruction=instruction,
        readings=len(instants),
        series=series,
        zones=zones,
        timeline=sorted(
            link(sorted(timeline, key=lambda e: e.at), evidence.records), key=lambda e: e.at
        ),
        unknowns=unknowns(
            alerts=alerts,
            readings=[r for rows in by_zone.values() for r in rows],
            acks=states,
            scored=scored is not None,
        ),
        latency=[_latency(a, assessments) for a in alerts],
        evidence=evidence,
        truth=scored,
        truth_source=truth.source if truth is not None else None,
    )


def _notes(state: dict[str, object]) -> list[dict[str, object]]:
    found = state.get("notified")
    return [n for n in found if isinstance(n, dict)] if isinstance(found, list) else []


def _all_rows(path: Path) -> Iterator[dict[str, object]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _clip_s(at: datetime | None, clip_start: datetime | None) -> float | None:
    if at is None or clip_start is None:
        return None
    return round((at - clip_start).total_seconds(), 2)


def _series(rows: Sequence[dict[str, object]], clip_start: datetime | None) -> ZoneSeries:
    return ZoneSeries(
        t=[str(r["ts"]) for r in rows],
        clip_s=[_clip_s(_at(r["ts"]), clip_start) for r in rows],
        people=[_int(r.get("people")) for r in rows],
        density_ppm2=[_float(r.get("density_ppm2")) for r in rows],
        speed_mps=[_float(r.get("speed_mps")) for r in rows],
        pressure=[_float(r.get("pressure")) for r in rows],
    )


def _summary(zone: str, rows: Sequence[dict[str, object]]) -> ZoneSummary:
    dense = [(r, d) for r in rows if (d := _float(r.get("density_ppm2"))) is not None]
    peak = max(dense, key=lambda x: x[1]) if dense else None
    above: dict[str, float] = {f"{level:g}": 0.0 for level in REFERENCE_PPM2}
    for (row, value), (after, _) in itertools.pairwise(dense):
        start, end = _at(row["ts"]), _at(after["ts"])
        if start is None or end is None:
            continue
        gap = (end - start).total_seconds()
        if gap > MAX_GAP_S:
            continue
        for level in REFERENCE_PPM2:
            if value >= level:
                above[f"{level:g}"] += gap
    pressures = [p for r in rows if (p := _float(r.get("pressure"))) is not None]
    counter = [c for r in rows if (c := _float(r.get("counterflow_share"))) is not None]
    return ZoneSummary(
        zone=zone,
        readings=len(rows),
        peak_density_ppm2=peak[1] if peak else None,
        peak_at=_at(peak[0]["ts"]) if peak else None,
        peak_people=_int(peak[0].get("people")) if peak else None,
        peak_heatmap=str(peak[0].get("heatmap")) if peak and peak[0].get("heatmap") else None,
        mean_density_ppm2=round(statistics.fmean(d for _, d in dense), 3) if dense else None,
        above_s={k: round(v, 1) for k, v in above.items()},
        max_pressure=max(pressures) if pressures else None,
        max_counterflow_share=max(counter) if counter else None,
    )


def _timeline(
    assessments: Sequence[dict[str, object]],
    alerts: Sequence[dict[str, object]],
    taps: Sequence[dict[str, object]],
    notified: Sequence[dict[str, object]],
) -> list[TimelineEvent]:
    out: list[TimelineEvent] = []
    level = None
    for row in assessments:
        at = _at(row.get("assessed_at")) or _at(row.get("ts"))
        if at is None or row.get("level") == level:
            continue
        level = str(row.get("level"))
        out.append(
            TimelineEvent(at=at, kind="assessment", level=level, text=str(row.get("why", "")))
        )
    for row in alerts:
        out += _alert_events(row)
    for tap in taps:
        at = _at(tap.get("at")) or _at(tap.get("ts"))
        if at is not None:
            text = f"{tap.get('role')}: {tap.get('button')}"
            out.append(
                TimelineEvent(
                    at=at,
                    kind="tap",
                    text=text,
                    alert_id=str(tap.get("alert_id")),
                    role=str(tap.get("role")),
                )
            )
    for note in notified:
        when = note.get("at")
        when = datetime.fromisoformat(when) if isinstance(when, str) else when
        if isinstance(when, datetime):
            text = f"notified {note.get('role')}"
            out.append(
                TimelineEvent(
                    at=when,
                    kind="notified",
                    text=text,
                    alert_id=str(note.get("alert_id")),
                    role=str(note.get("role")),
                )
            )
    return out


def _verdict(row: dict[str, object]) -> str:
    check = row.get("check")
    if isinstance(check, dict):
        seen = "confirmed" if check.get("agrees") else "did not see it"
        return f"Glimmer {seen}: {check.get('seen')}"
    return str(row.get("check_note") or "no check")


def _heard(spoken: dict[str, object]) -> str:
    """How an announcement was heard back, for its timeline line ('' when not checked)."""
    meaning = spoken.get("meaning")
    if meaning is None:
        return ""
    return f" · heard back: {meaning} meaning ({spoken.get('meaning_note')})"


def _alert_events(row: dict[str, object]) -> list[TimelineEvent]:
    at = _at(row.get("sent_at")) or _at(row.get("ts"))
    if at is None:
        return []
    sent = bool(row.get("sent"))
    out = [
        TimelineEvent(
            at=at,
            kind="alert" if sent else "held",
            level=str(row.get("level")),
            text=f"{'Sent' if sent else 'Held'} ({_verdict(row)})",
            ref=str(row.get("heatmap")) if row.get("heatmap") else None,
            alert_id=str(row.get("alert_id")),
        )
    ]
    audio = row.get("audio")
    for spoken in audio if isinstance(audio, list) else []:
        if isinstance(spoken, dict):
            out.append(
                TimelineEvent(
                    at=at,
                    kind="announcement",
                    text=f"[{spoken.get('lang')}] {spoken.get('text')}" + _heard(spoken),
                    ref=str(spoken.get("audio")) if spoken.get("audio") else None,
                    alert_id=str(row.get("alert_id")),
                )
            )
    return out


def _latency(alert: dict[str, object], assessments: Sequence[dict[str, object]]) -> AlertLatency:
    reading_at = _at(alert.get("ts"))
    assert reading_at is not None  # noqa: S101 - `_rows` keeps only rows with a time
    assessed = _at(alert.get("assessed_at"))
    if assessed is None:
        match = next(
            (a for a in assessments if a.get("ts") == alert.get("ts") and a.get("alert")), None
        )
        assessed = _at(match.get("assessed_at")) if match else None
    sent_at = _at(alert.get("sent_at"))
    check = alert.get("check")
    if isinstance(check, dict):
        checked = "confirmed" if check.get("agrees") else "rejected"
    else:
        checked = str(alert.get("check_note") or "no check")
    return AlertLatency(
        alert_id=str(alert.get("alert_id")),
        level=str(alert.get("level")),
        sent=bool(alert.get("sent")),
        reading_at=reading_at,
        assess_s=round((assessed - reading_at).total_seconds(), 1) if assessed else None,
        alert_s=round((sent_at - reading_at).total_seconds(), 1) if sent_at else None,
        checked=checked,
    )


def _evidence(chain: EventChain | None, alerts: Sequence[dict[str, object]]) -> Evidence:
    if chain is None or not alerts:
        return Evidence()
    ids = {str(a.get("alert_id")) for a in alerts}
    days: set[date] = set()
    for alert in alerts:
        for key in ("ts", "sent_at"):
            at = _at(alert.get(key))
            if at is not None:
                days.add(at.date())
    records: list[dict[str, object]] = []
    files: list[str] = []
    ok: bool | None = None
    problem = None
    for day in sorted(days):
        path = chain.path_for(day)
        if not path.is_file():
            continue
        files.append(path.name)
        checked = verify_chain(path)
        ok = checked.ok if ok is None else ok and checked.ok
        if not checked.ok:
            problem = f"{path.name}: record {checked.first_bad_seq}: {checked.reason}"
        records += [
            {
                "seq": r.seq,
                "kind": r.kind,
                "ts": r.ts.isoformat(),
                "hash": r.hash,
                **_brief(r.payload),
            }
            for r in read_chain(path)
            if _event_of(r.payload) in ids
        ]
    return Evidence(chain_ok=ok, chain_files=files, problem=problem, records=records)


def _event_of(payload: dict[str, object]) -> str | None:
    candidate = payload.get("candidate")
    if isinstance(candidate, dict):
        return str(candidate.get("event_id"))
    found = payload.get("event_id")
    return str(found) if found is not None else None


def _brief(payload: dict[str, object]) -> dict[str, object]:
    """The parts of a chain record a person reads: event, verdict, action detail, role."""
    keep = ("event_id", "verdict", "reason", "role", "detail", "label", "tier")
    out = {k: payload[k] for k in keep if k in payload}
    if "kind" in payload:
        out["action"] = payload["kind"]
    candidate = payload.get("candidate")
    if isinstance(candidate, dict):
        out["event_id"] = candidate.get("event_id")
        out["severity"] = candidate.get("severity")
    return out


def _truth(
    truth: Truth, by_zone: dict[str, list[dict[str, object]]], clip_start: datetime
) -> list[TruthZone]:
    out = []
    for zone, known in truth.zones.items():
        rows = by_zone.get(zone, [])
        pairs = []
        for row in rows:
            at = _at(row.get("ts"))
            clip = _clip_s(at, clip_start)
            if clip is None or clip < known.t[0] or clip > known.t[-1]:
                continue
            true = known.people[_nearest(known.t, clip)]
            density = _float(row.get("density_ppm2"))
            true_density = true / known.area_m2 if known.area_m2 else None
            pairs.append((clip, _int(row.get("people")), true, density, true_density))
        out.append(_score(zone, pairs, known))
    return out


def _nearest(times: Sequence[float], at: float) -> int:
    return min(range(len(times)), key=lambda k: abs(times[k] - at))


def _score(
    zone: str,
    pairs: Sequence[tuple[float, int, int, float | None, float | None]],
    known: TruthSeries,
) -> TruthZone:
    counted = [p for p in pairs if p[2] > 0]
    ratios = [p[3] / p[4] for p in counted if p[3] is not None and p[4]]
    curve = [p / known.area_m2 if known.area_m2 else None for p in known.people]
    real = [(t, d) for t, d in zip(known.t, curve, strict=True) if d is not None]
    peak = max(real, key=lambda x: x[1]) if real else None
    sys_dense = [p[3] for p in pairs if p[3] is not None]
    return TruthZone(
        zone=zone,
        readings=len(pairs),
        count_ratio=round(sum(p[1] for p in counted) / sum(p[2] for p in counted), 3)
        if counted
        else None,
        count_mae=round(statistics.fmean(abs(p[1] - p[2]) for p in counted), 2)
        if counted
        else None,
        density_ratio_median=round(statistics.median(ratios), 2) if ratios else None,
        sys_peak_ppm2=max(sys_dense) if sys_dense else None,
        true_peak_ppm2=round(peak[1], 2) if peak else None,
        true_peak_clip_s=peak[0] if peak else None,
        clip_s=[p[0] for p in pairs],
        sys_people=[p[1] for p in pairs],
        true_people=[p[2] for p in pairs],
        sys_density=[p[3] for p in pairs],
        true_density=[round(p[4], 3) if p[4] is not None else None for p in pairs],
        truth_curve_s=list(known.t),
        truth_curve_density=[round(d, 3) if d is not None else None for d in curve],
    )


def after_action(spark: StructuredLLM, report: EventReport) -> AfterAction:
    """Muse Spark's after-action summary, written from the report (series left out).

    Raises:
        RulecamError: Spark fails.
    """
    brief = report.model_dump(mode="json", exclude={"series": True, "truth": {"__all__": _CURVES}})
    system = (
        "You write the after-action summary of a crowd-safety event for the venue's safety "
        "lead, from the crowd watch's own report: what happened and when, how dense it got and "
        "for how long, how fast the system warned and whether stewards acknowledged, what was "
        "announced, and, when ground truth is given, how close the measurements were. Use the "
        "report's times and numbers only; say plainly what is missing (`unknowns`). In `cited`, "
        "repeat the narrative one sentence at a time, each with the `seq` numbers of the "
        "evidence records (`evidence.records`, and `seq` on timeline lines) it rests on, [] when "
        "none does. Reply with one JSON object."
    )
    return spark.structured(
        model=SPARK_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(brief, default=str)},
        ],
        schema=AfterAction,
    )


_CURVES: Final = {
    "clip_s",
    "sys_people",
    "true_people",
    "sys_density",
    "true_density",
    "truth_curve_s",
    "truth_curve_density",
}


__all__ = [
    "AfterAction",
    "EventReport",
    "Truth",
    "TruthSeries",
    "after_action",
    "build_report",
]
