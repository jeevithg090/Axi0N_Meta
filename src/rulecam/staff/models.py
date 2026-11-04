"""The staff companion's wire models: feed items, actions and response-time stats.

Items address roles (`dock_lead`, `steward`), never people. Nothing here has a field for a name,
a face, a track or a person: stats are per role and per shift only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from rulecam.contracts import Frozen

ROLE_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"
MAX_ROLE = 64
ITEM_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"

Role = Annotated[str, Field(min_length=1, max_length=MAX_ROLE, pattern=ROLE_PATTERN)]

Source = Literal["rule", "crowd", "shelf", "review", "drill"]
"""Where an item came from: a verified rule alert, a crowd warning, a shelf task, a human-queue
review (the verifier could not confirm it) or a response drill."""

ItemStatus = Literal["open", "acknowledged", "on_my_way", "done", "false_alarm"]
StaffActionKind = Literal["acknowledge", "on_my_way", "done", "false_alarm", "escalate"]
Lang = Literal["en", "es", "hi"]
LANGS: tuple[Lang, ...] = ("en", "es", "hi")


class VerdictView(Frozen):
    """The verifier's judgement shown on a card (Glimmer for rules and crowd checks).

    Attributes:
        model: The verifier model (e.g. `muse-glimmer-30b`).
        verdict: Its word (`violation`, `confirmed`, `unsure`, `rejected` …).
        confidence: Its score in [0, 1], when it gave one.
        reason: Its one-line reason (model text: data, shown escaped).
        late: The check arrived after the alert went out (crowd warnings).
    """

    model: str
    verdict: str
    confidence: float | None = None
    reason: str = ""
    late: bool = False


class TakenAction(Frozen):
    """One staff action on an item, as chained."""

    action: StaffActionKind
    role: str
    ts: datetime
    note: str = ""
    chain_hash: str | None = None


Stage = Literal[
    "raised", "verified", "assigned", "acknowledged", "on_my_way", "escalated", "record", "done",
    "false_alarm",
]  # fmt: skip
"""A step of an item's loop: seen on camera, verified, sent to a role, the role's taps, an
escalation, or any other chained record of the event (e.g. an arrival a camera confirmed)."""


class TimelineStep(Frozen):
    """One timed step from alert to action.

    Attributes:
        stage: What happened.
        ts: When.
        label: Plain words (model and record text is data, shown escaped).
        role: The role it concerns, when one does.
        after_s: Seconds since the item was raised.
    """

    stage: Stage
    ts: datetime
    label: str
    role: str | None = None
    after_s: float = 0.0


class StaffItem(Frozen):
    """One card in the staff feed: an alert or a task, with its evidence and state.

    Attributes:
        item_id: The event id (rule and crowd alerts) or the task id (shelf tasks).
        source: Where it came from.
        title: Short headline.
        body: What happened and where (plain words from the rule or the model).
        roles: Every role it has been sent to so far, in order.
        severity: The rule's severity or the crowd level.
        camera_id: The camera, when there is one.
        camera_name: The camera's friendly name, when configured.
        rule_text: The plain-language rule, when it is a rule alert.
        measurement: The measured value with its unit, when there is one.
        ts: When the first alert for it went out.
        status: Where it stands.
        verdict: The verifier's judgement, when it gave one.
        thumbnails: How many evidence stills `GET /staff/media/{item_id}/{n}` serves.
        escalation_step: Notifications so far out of the chain's roles (`2/3`), when escalating.
        next_role: Who is told next if nobody acknowledges.
        escalates_at: When that happens (the phone counts down to it).
        actions: Staff actions taken, oldest first.
        do_now: What the model says staff should do (crowd warnings, shelf tasks).
        announcements: Public-address lines the crowd warden spoke, by language.
        sample: Seeded sample data, never a live result.
        dry_run: The site runs actions in dry run (recorded, not executed).
        timeline: The loop from camera to action, oldest first.
        ack_s: Seconds from the first role being told to the first acknowledgement.
        fix_s: Seconds from raised to *Done*.
    """

    item_id: str
    source: Source
    title: str
    body: str
    roles: tuple[str, ...]
    severity: str = ""
    camera_id: str | None = None
    camera_name: str | None = None
    rule_text: str | None = None
    measurement: str | None = None
    ts: datetime
    status: ItemStatus = "open"
    verdict: VerdictView | None = None
    thumbnails: int = 0
    escalation_step: str | None = None
    next_role: str | None = None
    escalates_at: datetime | None = None
    actions: tuple[TakenAction, ...] = ()
    do_now: tuple[str, ...] = ()
    announcements: tuple[dict[str, str], ...] = ()
    sample: bool = False
    dry_run: bool = False
    timeline: tuple[TimelineStep, ...] = ()
    ack_s: float | None = None
    fix_s: float | None = None


class StaffActionRequest(Frozen):
    """A tap on a card (`POST /staff/items/{item_id}/actions`)."""

    action: StaffActionKind
    role: Role
    note: str = Field(default="", max_length=500)
    to_role: Role | None = Field(
        default=None, description="Escalate: the role to send it to when no chain is open."
    )


class ActionResult(Frozen):
    """What a tap did."""

    item: StaffItem
    did: str


class SeenRequest(Frozen):
    """The phone saw an item for the first time (alert → phone latency)."""

    role: Role
    client_ms: float | None = Field(default=None, ge=0, le=3_600_000)


class Bucket(Frozen):
    """Response times of one role or one shift.

    Attributes:
        key: The role or the shift.
        items: Items that reached it.
        acknowledged: How many were acknowledged (any first tap).
        done: How many were marked done.
        false_alarms: How many were marked false alarm.
        escalated: How many it escalated or let escalate.
        ack_p50_s: Median seconds from alert to first tap.
        ack_p90_s: 90th percentile of the same.
        done_p50_s: Median seconds from alert to done.
        phone_p50_ms: Median milliseconds from the alert to the phone showing it.
        phone_p95_ms: 95th percentile of the same.
    """

    key: str
    items: int
    acknowledged: int
    done: int
    false_alarms: int
    escalated: int
    ack_p50_s: float | None = None
    ack_p90_s: float | None = None
    done_p50_s: float | None = None
    phone_p50_ms: float | None = None
    phone_p95_ms: float | None = None


class StaffStats(Frozen):
    """`GET /staff/stats`: response times per role and per shift, never per person."""

    since: datetime
    timezone: str
    shifts: dict[str, str]
    by_role: list[Bucket]
    by_shift: list[Bucket]


class RoleInfo(Frozen):
    """A role the site uses, and where it was seen."""

    role: str
    sources: tuple[str, ...]


class SpeechLine(Frozen):
    """An item's announcement in one language (`GET /staff/items/{id}/speech`).

    Attributes:
        lang: The language.
        text: What is said (written by Muse Spark from the item; model text is data).
        audio: Where the WAV is (`/staff/audio/{name}`), when a speech engine could say it.
        engine: Which engine spoke it (`facebook/mms-tts-hin` is Meta's; `system-tts` is not).
        model: The model that wrote the text.
    """

    lang: str
    text: str
    audio: str | None = None
    engine: str | None = None
    model: str | None = None


class AskReply(Frozen):
    """`POST /staff/ask`: what was heard, the answer, and the answer spoken."""

    heard: str
    answer: str
    lang: str
    audio: str | None = None
    engine: str | None = None
    seconds: float
    models: list[str]
