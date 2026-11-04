"""What the supermarket checks produce and keep: findings, watches, restock tasks, briefs.

Every judgement in these records was made by a model (Muse Spark 1.3 reading frames, SAM 3.1
finding objects, Muse Glimmer giving a second look); the code only stores what they said, which
model said it and the evidence (frames, marked images) they saw.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from rulecam.contracts import Frozen

CheckKind = Literal["queue", "hazard", "shelf"]
TaskStatus = Literal["open", "on_my_way", "done", "false_alarm"]
BoxGrid = list[float]
"""`[x1, y1, x2, y2]` on a 0-1000 grid over an image's width and height."""


class Evidence(Frozen):
    """The pictures a judgement was made from.

    Attributes:
        frames: Frame files the models saw, oldest first (served by `GET /retail/files/{name}`).
        marked: The frame with numbered boxes on it (what staff get).
        clip: A joined video clip the model watched, when one was used.
    """

    frames: list[str] = Field(default_factory=list)
    marked: str | None = None
    clip: str | None = None


class ModelCall(Frozen):
    """One model call behind a finding: which model, for what, how long, and what went wrong."""

    model: str
    role: str
    seconds: float
    error: str | None = None


class LaneState(Frozen):
    """One till or checkout lane as Spark sees it."""

    lane: str = Field(max_length=120, description="Which lane or till, e.g. 'left till'.")
    open: bool = Field(description="A cashier or working self-checkout is serving here.")
    customers_waiting: int = Field(ge=0, le=200, description="People queueing, not yet served.")
    being_served: bool = Field(description="Someone is being served right now.")


class QueueJudgement(Frozen):
    """Muse Spark's reading of a checkout camera (structured output)."""

    lanes: list[LaneState] = Field(default_factory=list, max_length=24)
    customers_waiting: int = Field(ge=0, le=500, description="All people queueing in view.")
    est_wait_s: float | None = Field(
        default=None,
        ge=0,
        le=7200,
        description="Rough wait for the last person in the longest queue, in seconds.",
    )
    queue_moving: bool | None = Field(
        default=None, description="Whether the queue moved across the frames; null if unsure."
    )
    customer_needs_help: bool = Field(
        description="Someone is waiting at a counter or till with no staff serving them."
    )
    open_another_till: bool = Field(description="The store should open another till now.")
    alert_staff: bool = Field(description="Send staff an alert now (not already handled).")
    reason: str = Field(max_length=800, description="Why, from what is visible and the data.")
    staff_message: str = Field(
        max_length=300, description="What staff should do, in one short sentence, or ''."
    )
    queue_marks: list[int] = Field(
        default_factory=list,
        max_length=100,
        description="Numbers of the yellow boxes that are people queueing.",
    )
    confidence: float = Field(ge=0, le=1)


class HazardItem(Frozen):
    """One floor hazard Spark sees."""

    box: BoxGrid = Field(min_length=4, max_length=4, description="On a 0-1000 grid.")
    kind: Literal[
        "spill", "broken_glass", "fallen_stock", "obstruction", "wet_floor", "trip", "other"
    ]
    sam_phrase: str = Field(
        max_length=80,
        description="A short noun phrase SAM 3.1 can find it with, e.g. 'puddle of milk'.",
    )
    what: str = Field(max_length=300)
    severity: Literal["low", "medium", "high"]


class HazardDraft(Frozen):
    """Muse Spark's hazard reading of one frame (structured output)."""

    hazards: list[HazardItem] = Field(default_factory=list, max_length=12)
    alert_staff: bool = Field(description="Send staff to make it safe now.")
    summary: str = Field(max_length=600)
    staff_message: str = Field(max_length=300)
    confidence: float = Field(ge=0, le=1)


class SecondLook(Frozen):
    """Muse Glimmer's independent check of one marked hazard (structured output)."""

    hazard: bool = Field(description="Box N really is a slip or trip hazard on the floor.")
    why: str = Field(max_length=400)


class HazardCheck(Frozen):
    """One hazard after SAM 3.1 and Glimmer had their say."""

    number: int
    item: HazardItem
    box_xyxy: tuple[float, float, float, float]
    sam_found: int = Field(description="SAM 3.1 detections of the phrase that overlap the box.")
    glimmer: SecondLook | None = None
    confirmed: bool


class Finding(Frozen):
    """One check's result, kept in `findings.jsonl` and shown on the supermarket page.

    Attributes:
        id: `f-…`, unique.
        kind: Which check.
        camera_id: The camera (`upload` for a phone photo).
        camera_name: The owner's name for it.
        at: The recorded instant checked (UTC).
        checked_at: When the check ran.
        watch_id: The standing watch that ran it, if any.
        act: Whether staff should act (the models' decision).
        headline: One line for the owner.
        staff_message: What staff were told.
        detail: The models' structured output (queue judgement, hazards, gaps).
        evidence: Frames and the marked image.
        calls: Every model call, with its time.
        seconds: Wall-clock seconds for the whole check.
        alert_id: The alert sent to staff (`/alerts`), when one was.
        task_id: The restock task created, for a shelf finding with gaps.
        metric: The number this check measures for a pre-registered switchback (queue: people
            waiting), when there is one.
    """

    id: str
    kind: CheckKind
    camera_id: str
    camera_name: str = ""
    at: datetime | None
    checked_at: datetime
    watch_id: str | None = None
    act: bool
    headline: str
    staff_message: str = ""
    detail: dict[str, object] = Field(default_factory=dict)
    evidence: Evidence = Field(default_factory=Evidence)
    calls: list[ModelCall] = Field(default_factory=list)
    seconds: float = 0.0
    alert_id: str | None = None
    task_id: str | None = None
    metric: float | None = None


class Watch(Frozen):
    """A standing check the owner turned on: run `kind` on `camera_id` every `every_s` seconds.

    Attributes:
        id: `w-…`.
        kind: Which check.
        camera_id: The camera watched.
        every_s: Seconds between runs.
        role: Who is alerted (a role, never a person).
        focus: What to look out for, in the owner's words (goes to the model).
        advice_id: The advice card it came from, when one did.
        created_at: When it was turned on.
        active: Whether it runs.
        last_run: When it last ran.
    """

    id: str
    kind: CheckKind
    camera_id: str
    every_s: float = Field(ge=15, le=86400)
    role: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    focus: str = Field(default="", max_length=500)
    advice_id: str | None = None
    created_at: datetime
    active: bool = True
    last_run: datetime | None = None


class WatchRequest(Frozen):
    """`POST /retail/watches`."""

    kind: CheckKind
    camera_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    every_s: float = Field(default=60, ge=15, le=86400)
    role: str = Field(default="floor", pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    focus: str = Field(default="", max_length=500)


class StaffTask(Frozen):
    """A job for staff from a finding: restock a shelf gap, or make a floor hazard safe.

    Attributes:
        id: `t-…`.
        kind: `shelf` (restock) or `hazard` (clean up, clear the aisle).
        finding_id: The finding that opened it.
        camera_id: Where.
        where: The owner's name for the place.
        items: What to do, one line per gap or hazard (from the models' reading).
        marked: The marked "before" image.
        seen_at: When the camera saw it (the recorded instant), else when it was reported.
        created_at: When the task was opened and staff were told.
        status: `open`, `on_my_way`, `done` or `false_alarm`.
        done_at: When it was closed.
        closed_by: `photo` (staff proof checked by the models), `camera` (a later check of the
            same camera saw it fixed) or `staff` (closed by hand, unchecked).
        minutes_to_fix: From `seen_at` to `done_at`.
        alert_id: The staff alert.
        role: Who it is for.
        proof_image: The staff's photo, when one was sent.
        proof: The models' verdict on the photo or the re-check.
        fixed_seq: The evidence-chain record of the fix.
    """

    id: str
    kind: Literal["shelf", "hazard"] = "shelf"
    finding_id: str
    camera_id: str
    where: str
    items: list[str]
    marked: str | None
    seen_at: datetime | None = None
    created_at: datetime
    status: TaskStatus = "open"
    done_at: datetime | None = None
    closed_by: Literal["photo", "camera", "staff"] | None = None
    minutes_to_fix: float | None = None
    alert_id: str | None = None
    role: str = "stock"
    proof_image: str | None = None
    proof: dict[str, object] | None = None
    fixed_seq: int | None = None


class BriefStats(Frozen):
    """The day's numbers the brief is written from (all counted from stored findings)."""

    checks: dict[str, int] = Field(default_factory=dict)
    alerts_sent: int = 0
    alerts_answered: int = 0
    queue_peak_waiting: int | None = None
    queue_peak_at: datetime | None = None
    till_calls: int = 0
    help_calls: int = 0
    hazards_confirmed: int = 0
    shelf_gaps: int = 0
    tasks_open: int = 0
    tasks_done: int = 0
    median_minutes_to_fix: float | None = None
    asks: int = 0


class Brief(Frozen):
    """The owner's brief: Muse Spark writes it from the day's stored findings and answers."""

    day: str
    written_at: datetime
    lang: str
    text: str
    headline: str
    stats: BriefStats
    decisions: list[str] = Field(default_factory=list)
    model: str
    seconds: float
