"""How every number in a proof pack is computed from chain records (pure functions).

Each number carries a `MetricSpec`: a small, declarative recipe (count records that match, or
time from a candidate to the first later record of the same event, or seconds a camera spent in a
coverage state). The pack builder computes the value with `evaluate`, and the recipient's browser
runs the same recipe over the records the pack discloses (`ui/src/app/inspector/_lib/engine.ts`),
so a number that does not follow from the records is caught on the recipient's machine.

Semantics both implementations share (keep them identical):

- **Order.** Records are ordered by (day, seq): the chain's own order.
- **Event.** A record belongs to an event through `payload.candidate.event_id` (candidates) or
  `payload.event_id` (everything else), as `evidence.incident.event_of` reads it.
- **Anchor time.** A record is placed in time by its event's candidate `ts` when the candidate is
  among the records, else by its own `ts`. A verdict that lands a minute after the period ends is
  still counted with the alert it judged.
- **Windows** are half-open `[start, end)` instants. Times are compared as integer microseconds.
- **Shift** is the shift whose start hour (site time zone) is the latest at or before the anchor's
  local hour; hours before the first start belong to the last shift (a night shift wraps).
- **Durations** use the lower median and the nearest-rank 90th percentile, in integer
  microseconds, then divided by 1 000 000, so both languages produce the same float.

Nothing here groups by a track, a person or a worker: filters are rule, camera and shift only.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from rulecam.contracts import Frozen

Scalar = str | int | float | bool | None
Window = Literal["current", "previous"]
Op = Literal["count", "median_s", "p90_s", "max_s", "state_s", "two_key_unapproved"]

DURATION_OPS: Final = frozenset({"median_s", "p90_s", "max_s"})
TWO_KEY_ROLES: Final = ("owner", "crew_representative")


class Shift(Frozen):
    """A named shift starting at `start_hour` (0-23) in the site's time zone."""

    name: str = Field(min_length=1, max_length=40)
    start_hour: int = Field(ge=0, le=23)


class Period(Frozen):
    """The two windows a pack compares, as UTC instants `[start, end)`."""

    start: datetime
    end: datetime
    previous_start: datetime
    previous_end: datetime
    timezone: str = "UTC"
    shifts: tuple[Shift, ...] = ()


class MetricSpec(Frozen):
    """The recipe of one number.

    Attributes:
        op: What to compute (see the module docstring).
        where: Dotted path -> required value (`kind`, `payload.verdict`, ...), all must hold.
        prefix: Dotted path -> required string prefix.
        to: For durations, the end record: the first later record of the same event matching any
            of these `where` maps.
        rule: Only records whose event's candidate has this `rule_id`.
        camera: Only records whose event's candidate (or coverage payload) has this camera.
        shift: Only records anchored in this shift.
        state: For `state_s`, the coverage state to add up (`ok` or `blind`).
    """

    op: Op
    where: dict[str, Scalar] = Field(default_factory=dict)
    prefix: dict[str, str] = Field(default_factory=dict)
    to: tuple[dict[str, Scalar], ...] = ()
    rule: str | None = None
    camera: str | None = None
    shift: str | None = None
    state: str | None = None


@dataclass(frozen=True)
class Located:
    """One record with its place in the chain: `key` is `<day>#<seq>`."""

    key: str
    day: str
    record: Mapping[str, object]


@dataclass(frozen=True)
class Result:
    """A computed value and the record keys it was computed from (sorted)."""

    value: float | int | None
    refs: tuple[str, ...]


def record_key(day: str, seq: int) -> str:
    """The pack's id for a record: `<YYYY-MM-DD>#<seq>`."""
    return f"{day}#{seq}"


def micros(ts: str) -> int:
    """Integer microseconds since the epoch of a UTC ISO timestamp ending in `Z`."""
    moment = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)
    delta = moment - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def get_path(record: Mapping[str, object], path: str) -> object:
    """The value at a dotted path (`payload.decision.route`), or `None` when absent."""
    node: object = record
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def event_of(record: Mapping[str, object]) -> str | None:
    """The event id of a record (a candidate's own, or the payload's `event_id`)."""
    found = get_path(record, "payload.candidate.event_id")
    if found is None:
        found = get_path(record, "payload.event_id")
    return found if isinstance(found, str) else None


def _same(value: object, wanted: Scalar) -> bool:
    if isinstance(wanted, bool) or isinstance(value, bool):
        return value is wanted
    return value == wanted


def matches(
    record: Mapping[str, object], where: Mapping[str, Scalar], prefix: Mapping[str, str]
) -> bool:
    """Whether every `where` equality and every `prefix` holds for the record."""
    if not all(_same(get_path(record, k), v) for k, v in where.items()):
        return False
    for path, start in prefix.items():
        value = get_path(record, path)
        if not isinstance(value, str) or not value.startswith(start):
            return False
    return True


def shift_of(ts: str, timezone: str, shifts: Sequence[Shift]) -> str | None:
    """The shift an instant falls in (see the module docstring), or `None` without shifts."""
    if not shifts:
        return None
    moment = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    hour = moment.astimezone(ZoneInfo(timezone)).hour
    ordered = sorted(shifts, key=lambda s: s.start_hour)
    chosen = ordered[-1]
    for shift in ordered:
        if shift.start_hour <= hour:
            chosen = shift
    return chosen.name


class _Index:
    """Candidates by event id over the records, for anchors and rule/camera filters."""

    def __init__(self, records: Sequence[Located]) -> None:
        self.records = records
        self.candidates: dict[str, Located] = {}
        for item in records:
            if item.record.get("kind") == "candidate":
                event = event_of(item.record)
                if event is not None:
                    self.candidates.setdefault(event, item)

    def candidate(self, item: Located) -> Located | None:
        event = event_of(item.record)
        return self.candidates.get(event) if event is not None else None

    def anchor(self, item: Located) -> str:
        found = self.candidate(item)
        return str((found or item).record.get("ts", ""))


def _window(period: Period, window: Window) -> tuple[int, int]:
    if window == "current":
        return micros_of(period.start), micros_of(period.end)
    return micros_of(period.previous_start), micros_of(period.previous_end)


def micros_of(moment: datetime) -> int:
    """Integer microseconds since the epoch of an aware datetime."""
    delta = moment.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _scoped(
    item: Located, spec: MetricSpec, index: _Index, period: Period, bounds: tuple[int, int]
) -> bool:
    anchor = index.anchor(item)
    if not anchor or not bounds[0] <= micros(anchor) < bounds[1]:
        return False
    candidate = index.candidate(item)
    source = candidate.record if candidate is not None else None
    if spec.rule is not None and get_path(source or {}, "payload.candidate.rule_id") != spec.rule:
        return False
    if spec.camera is not None:
        camera = get_path(source or item.record, "payload.candidate.camera_id")
        if camera is None:
            camera = get_path(item.record, "payload.camera_id")
        if camera != spec.camera:
            return False
    return spec.shift is None or shift_of(anchor, period.timezone, period.shifts) == spec.shift


def _count(spec: MetricSpec, index: _Index, period: Period, window: Window) -> Result:
    bounds = _window(period, window)
    hits = [
        item.key
        for item in index.records
        if matches(item.record, spec.where, spec.prefix)
        and _scoped(item, spec, index, period, bounds)
    ]
    return Result(len(hits), tuple(sorted(hits)))


def _durations(spec: MetricSpec, index: _Index, period: Period, window: Window) -> Result:
    bounds = _window(period, window)
    where = spec.where or {"kind": "candidate"}
    values: list[int] = []
    refs: list[str] = []
    for position, item in enumerate(index.records):
        if not matches(item.record, where, spec.prefix):
            continue
        if not _scoped(item, spec, index, period, bounds):
            continue
        event = event_of(item.record)
        end = next(
            (
                later
                for later in index.records[position + 1 :]
                if event_of(later.record) == event
                and any(matches(later.record, option, {}) for option in spec.to)
            ),
            None,
        )
        if end is None or event is None:
            continue
        values.append(micros(str(end.record["ts"])) - micros(str(item.record["ts"])))
        refs.extend((item.key, end.key))
    return Result(_summarise(spec.op, values), tuple(sorted(set(refs))))


def _summarise(op: str, values: Sequence[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if op == "median_s":
        chosen = ordered[(len(ordered) - 1) // 2]
    elif op == "p90_s":
        chosen = ordered[max(math.ceil(0.9 * len(ordered)) - 1, 0)]
    else:
        chosen = ordered[-1]
    return chosen / 1_000_000


def _state_seconds(spec: MetricSpec, index: _Index, period: Period, window: Window) -> Result:
    """Seconds of `[start, end)` a camera's chained coverage state was `spec.state`.

    Each coverage record starts a segment in its state that lasts until the camera's next one;
    the last segment runs to the window's end. Time before the camera's first record is unknown
    and counts in no state. `payload.ts` (when present) is when the change happened.
    """
    start, end = _window(period, window)
    changes = sorted(
        (
            (micros(str(get_path(item.record, "payload.ts") or item.record["ts"])), item)
            for item in index.records
            if item.record.get("kind") == "coverage"
            and get_path(item.record, "payload.camera_id") == spec.camera
        ),
        key=lambda pair: pair[0],
    )
    total = 0
    refs: list[str] = []
    for position, (at, item) in enumerate(changes):
        until = changes[position + 1][0] if position + 1 < len(changes) else end
        lo, hi = max(at, start), min(until, end)
        if hi <= lo:
            continue
        refs.append(item.key)
        if get_path(item.record, "payload.state") == spec.state:
            total += hi - lo
    return Result(total / 1_000_000, tuple(sorted(refs)))


def _two_key(index: _Index, period: Period, window: Window) -> Result:
    """Protected settings applied in the window without both keys approving that proposal first."""
    bounds = _window(period, window)
    approvals: dict[str, dict[str, str]] = {}
    bad: list[str] = []
    for item in index.records:
        record = item.record
        if record.get("kind") != "rule_change":
            continue
        proposal = get_path(record, "payload.proposal_id")
        action = get_path(record, "payload.action")
        if not isinstance(proposal, str):
            continue
        if action == "setting_approval":
            role = get_path(record, "payload.role")
            if isinstance(role, str):
                approvals.setdefault(proposal, {})[role] = item.key
        elif action == "setting_applied" and bounds[0] <= micros(str(record["ts"])) < bounds[1]:
            held = approvals.get(proposal, {})
            if not all(role in held for role in TWO_KEY_ROLES):
                bad.append(item.key)
    return Result(len(bad), tuple(sorted(bad)))


def evaluate(
    spec: MetricSpec, records: Sequence[Located], period: Period, window: Window = "current"
) -> Result:
    """Compute one number from records in chain order.

    Args:
        spec: The recipe.
        records: Records ordered by (day, seq).
        period: The pack's windows, time zone and shifts.
        window: Which window to compute.

    Returns:
        The value (`None` for a duration with no pairs) and the keys of the records used.
    """
    index = _Index(records)
    if spec.op == "count":
        return _count(spec, index, period, window)
    if spec.op in DURATION_OPS:
        return _durations(spec, index, period, window)
    if spec.op == "state_s":
        return _state_seconds(spec, index, period, window)
    return _two_key(index, period, window)
