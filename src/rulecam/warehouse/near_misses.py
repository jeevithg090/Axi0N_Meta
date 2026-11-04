"""Near-miss log and heatmap from the evidence chain (pure functions).

A near miss is a candidate whose rule measures a distance: a person came inside the owner's
own `distance_m` limit (of a moving vehicle, when the rule says `moving`). Its numbers are read
from the candidate's predicate trace, which the rules engine wrote with the same geometry it
fired on (`rules.near_miss`): separation, closest approach in the next second, time to
collision, and the `moving` node's speed. What happened next (verdict, decision, actions,
acks, disputes, false-alarm labels) is joined from the same day's chain by event id.

No threshold is added here: the only distance is the owner's, and the verdict is the
verifier's. The heatmap bins where the person stood at the firing time in the frame; it counts
events, never people.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from rulecam.contracts import EventRecord, Track
from rulecam.evidence.incident import event_of
from rulecam.warehouse.models import Heatmap, HeatPoint, NearMiss

DISTANCE_NODE = ".distance_m"
MOVING_NODE = ".moving"
DEFAULT_COLS = 16
DEFAULT_ROWS = 9
TRACK_PAD = timedelta(seconds=1)
TRAINING_DONE = "refresher_training_done"

TrackWindow = Callable[[str, datetime, datetime], Sequence[Track]]


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def trace_number(trace: Mapping[str, object], node: str, field_name: str) -> float | None:
    """The first numeric `<path><node>.<field>` in a predicate trace.

    Args:
        trace: The candidate's `predicate_trace` (flat `path -> value`).
        node: The node suffix, e.g. `.distance_m` (a root node has no leading path).
        field_name: The field, e.g. `separation_m`.

    Returns:
        The value, or `None` when the trace has no such numeric entry.
    """
    wanted = f"{node}.{field_name}"
    root = wanted.lstrip(".")
    for key in sorted(trace):
        if key.endswith(wanted) or key == root:
            found = _number(trace[key])
            if found is not None:
                return found
    return None


def _walk(predicate: Mapping[str, object]) -> Iterable[Mapping[str, object]]:
    yield predicate
    for key in ("of", "a", "b", "inner", "first", "then", "predicate"):
        child = predicate.get(key)
        if isinstance(child, dict):
            yield from _walk(child)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, dict):
                    yield from _walk(item)


def distance_limit(rule: Mapping[str, object]) -> float | None:
    """The owner's distance: the first `distance_m` node's `lt` in a rule (as chained)."""
    for node in _walk(_dict(rule.get("predicate"))):
        if node.get("op") == "distance_m":
            return _number(node.get("lt"))
    return None


def measures_distance(rule: Mapping[str, object]) -> bool:
    """Whether a rule (as chained in a candidate's context) has a `distance_m` node."""
    return any(node.get("op") == "distance_m" for node in _walk(_dict(rule.get("predicate"))))


@dataclass
class _Joined:
    """What the chain says happened after one candidate."""

    verdict: Mapping[str, object] | None = None
    decision: Mapping[str, object] | None = None
    actions: list[str] = field(default_factory=list)
    acked_by: list[str] = field(default_factory=list)
    disputes: dict[str, str] = field(default_factory=dict)  # dispute id -> latest outcome
    regression: bool = False
    training_done: bool = False


def _join(records: Sequence[EventRecord]) -> dict[str, _Joined]:
    joined: dict[str, _Joined] = defaultdict(_Joined)
    for record in records:
        event_id = event_of(record)
        if event_id is None or record.kind == "candidate":
            continue
        entry = joined[event_id]
        payload = record.payload
        if record.kind == "verdict":
            decision = payload.get("decision")
            if isinstance(decision, dict):
                entry.decision = decision
            else:  # the last verdict is the one the decision used (Tier 2 after Tier 1)
                entry.verdict = payload
        elif record.kind == "action":
            entry.actions.append(str(payload.get("detail", payload.get("kind", ""))))
        elif record.kind == "ack":
            entry.acked_by.append(str(payload.get("role", "")))
        elif record.kind == "dispute":
            outcome = _dict(payload.get("dispute")).get("outcome", "pending")
            entry.disputes[str(payload.get("dispute_id", record.seq))] = str(outcome)
        elif record.kind == "label" and payload.get("label") == "false_alarm":
            entry.regression = True
        elif record.kind == "label" and payload.get("label") == TRAINING_DONE:
            entry.training_done = True
    return joined


def _calibration(context: Mapping[str, object]) -> tuple[str | None, float | None]:
    calibration = _dict(context.get("calibration"))
    reference = calibration.get("reference") or calibration.get("method")
    return (str(reference) if reference else None), _number(calibration.get("err_m"))


def _source(rule: Mapping[str, object]) -> tuple[str, str]:
    source = _dict(rule.get("source"))
    return str(source.get("text", "")), str(source.get("lang", "en") or "en")


def foot_at(tracks: Sequence[Track], subject: str, at: datetime) -> tuple[float, float] | None:
    """The subject track's foot point (frame pixels) nearest to `at`, if the track is kept.

    Args:
        tracks: Tracks of the camera around `at`.
        subject: The trace's `subject` (`<camera>:<concept>:<id>` or the bare track id).
        at: The firing time.

    Returns:
        `(x, y)` in pixels, or `None` when no track matches.
    """
    for track in tracks:
        if track.track_id != subject and not subject.endswith(f":{track.track_id}"):
            continue
        if not track.points:
            return None
        nearest = min(track.points, key=lambda p: abs((p.ts - at).total_seconds()))
        return float(nearest.foot_px[0]), float(nearest.foot_px[1])
    return None


def near_misses(
    records: Sequence[EventRecord],
    *,
    rule_ids: Iterable[str] | None = None,
    plan_id: str | None = None,
    camera_id: str | None = None,
    tracks: TrackWindow | None = None,
) -> list[NearMiss]:
    """Every near miss in one day's chain, newest first.

    Args:
        records: The day's chain records in order.
        rule_ids: Keep only these rules (all distance rules by default).
        plan_id: Keep only rules compiled by this plan (the rule's `source.plan_id`).
        camera_id: Keep only this camera.
        tracks: Reads a camera's tracks in a time window (the track store), to place each
            near miss in the frame; `None` leaves `foot_px` empty.

    Returns:
        The near misses, newest first.
    """
    wanted = set(rule_ids) if rule_ids is not None else None
    joined = _join(records)
    found: list[NearMiss] = []
    for record in records:
        if record.kind != "candidate":
            continue
        candidate = _dict(record.payload.get("candidate"))
        context = _dict(record.payload.get("context"))
        rule = _dict(context.get("rule"))
        rule_id = str(candidate.get("rule_id", ""))
        cam = str(candidate.get("camera_id", ""))
        if (wanted is not None and rule_id not in wanted) or (camera_id and cam != camera_id):
            continue
        if plan_id is not None and _dict(rule.get("source")).get("plan_id") != plan_id:
            continue
        trace = _dict(candidate.get("predicate_trace"))
        if not measures_distance(rule) and trace_number(trace, DISTANCE_NODE, "value") is None:
            continue
        found.append(
            _near_miss(record, candidate, context=context, rule=rule, joined=joined, tracks=tracks)
        )
    return sorted(found, key=lambda row: row.ts, reverse=True)


def _near_miss(
    record: EventRecord,
    candidate: Mapping[str, object],
    *,
    context: Mapping[str, object],
    rule: Mapping[str, object],
    joined: Mapping[str, _Joined],
    tracks: TrackWindow | None,
) -> NearMiss:
    event_id = str(candidate.get("event_id", ""))
    trace = _dict(candidate.get("predicate_trace"))
    ts = _ts(candidate.get("ts_end")) or record.ts
    after = joined.get(event_id, _Joined())
    verdict = after.verdict or {}
    decision = after.decision or {}
    text, lang = _source(rule)
    plan = _dict(rule.get("source")).get("plan_id")
    reference, err = _calibration(context)
    ttc = trace_number(trace, DISTANCE_NODE, "ttc_s")
    alert = decision.get("alert")
    foot = None
    subject = trace.get("subject")
    cam = str(candidate.get("camera_id", ""))
    if tracks is not None and isinstance(subject, str):
        foot = foot_at(tracks(cam, ts - TRACK_PAD, ts + TRACK_PAD), subject, ts)
    return NearMiss(
        event_id=event_id,
        ts=ts,
        camera_id=cam,
        rule_id=str(candidate.get("rule_id", "")),
        rule_text=text,
        lang=lang,
        plan_id=plan if isinstance(plan, str) else None,
        severity=str(candidate.get("severity", "")),
        limit_m=distance_limit(rule),
        separation_m=trace_number(trace, DISTANCE_NODE, "separation_m")
        or trace_number(trace, DISTANCE_NODE, "value"),
        closest_m=trace_number(trace, DISTANCE_NODE, "closest_m"),
        ttc_s=ttc,
        vehicle_speed_mps=trace_number(trace, MOVING_NODE, "value"),
        calibration=reference,
        calibration_err_m=err,
        verdict=_str(verdict.get("verdict")),
        verdict_score=_number(verdict.get("score")),
        verdict_reason=_str(verdict.get("reason")),
        verifier=_str(verdict.get("model_version")),
        alert=alert if isinstance(alert, bool) else None,
        route=_str(decision.get("route")),
        actions=tuple(after.actions),
        acked_by=tuple(after.acked_by),
        disputes=tuple(after.disputes.values()),
        regression=after.regression,
        training_done=after.training_done,
        foot_px=foot,
    )


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def heatmap(
    rows_in: Sequence[NearMiss],
    *,
    camera_id: str,
    day: date,
    width: int | None = None,
    height: int | None = None,
    cols: int = DEFAULT_COLS,
    rows: int = DEFAULT_ROWS,
    background: str | None = None,
) -> Heatmap:
    """Bin one camera's near misses by where the person stood in the frame.

    Args:
        rows_in: Near misses (any cameras; only `camera_id`'s are counted).
        camera_id: The camera.
        day: The day counted.
        width: Frame width; by default the points' extent.
        height: Frame height; by default the points' extent.
        cols: Grid columns.
        rows: Grid rows.
        background: Path of a frame to draw under it.

    Returns:
        The heatmap; near misses without a position are counted in `unplaced`.
    """
    mine = [r for r in rows_in if r.camera_id == camera_id]
    points = [
        HeatPoint(event_id=r.event_id, x=r.foot_px[0], y=r.foot_px[1], alert=r.alert)
        for r in mine
        if r.foot_px is not None
    ]
    w = width or max([int(p.x) + 1 for p in points], default=1)
    h = height or max([int(p.y) + 1 for p in points], default=1)
    cells = [[0] * cols for _ in range(rows)]
    for point in points:
        col = min(cols - 1, max(0, int(point.x / w * cols)))
        row = min(rows - 1, max(0, int(point.y / h * rows)))
        cells[row][col] += 1
    return Heatmap(
        camera_id=camera_id,
        day=day,
        width=w,
        height=h,
        cols=cols,
        rows=rows,
        cells=tuple(tuple(r) for r in cells),
        points=tuple(points),
        unplaced=len(mine) - len(points),
        background=background,
    )
