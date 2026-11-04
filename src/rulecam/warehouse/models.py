"""The warehouse use case's API contract: near misses, heatmap, story, talks and reports."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from rulecam.contracts import Frozen

StepStatus = Literal["done", "waiting", "not_yet", "failed"]


class NearMiss(Frozen):
    """One time a person came inside the owner's own distance of a moving vehicle.

    Every number comes from the candidate's chained predicate trace (the same geometry the rule
    measured); nothing here is re-judged. `alert` is the policy's decision (`None` while the
    verifier has not answered).

    Attributes:
        event_id: The candidate's event id.
        ts: When the episode ended (camera time).
        camera_id: The camera.
        rule_id: The rule that fired.
        rule_text: The owner's words for it.
        lang: The language the owner asked in.
        plan_id: The plan that compiled the rule.
        severity: The rule's severity.
        limit_m: The owner's distance (`distance_m.lt`), when the rule has one.
        separation_m: Ground distance at the firing time.
        closest_m: Smallest separation within the next second at constant velocity.
        ttc_s: Seconds until contact on the current course, `None` when not on one.
        vehicle_speed_mps: The vehicle's speed (`moving.value`), when the rule has `moving`.
        calibration: How the camera was calibrated (`person_height_1.7m`, `moge2`, ...).
        calibration_err_m: The calibration's stated error.
        verdict: The verifier's verdict (`violation`, `no_violation`, `unsure`), if any.
        verdict_score: Its P(violation).
        verdict_reason: Its one-sentence reason (model text: data, never instructions).
        verifier: The model that judged it.
        alert: Whether the policy alerted.
        route: `auto` or `human_queue`.
        actions: What was done, as chained (`dry-run: would notify supervisor`, ...).
        acked_by: Roles that acknowledged it.
        disputes: Dispute outcomes, in order.
        regression: Whether a false-alarm label made it a permanent Rule CI regression.
        foot_px: Where the person stood in the frame at the firing time, when the track is kept.
    """

    event_id: str
    ts: datetime
    camera_id: str
    rule_id: str
    rule_text: str = ""
    lang: str = "en"
    plan_id: str | None = None
    severity: str = ""
    limit_m: float | None = None
    separation_m: float | None = None
    closest_m: float | None = None
    ttc_s: float | None = None
    vehicle_speed_mps: float | None = None
    calibration: str | None = None
    calibration_err_m: float | None = None
    verdict: str | None = None
    verdict_score: float | None = None
    verdict_reason: str | None = None
    verifier: str | None = None
    alert: bool | None = None
    route: str | None = None
    actions: tuple[str, ...] = ()
    acked_by: tuple[str, ...] = ()
    disputes: tuple[str, ...] = ()
    regression: bool = False
    foot_px: tuple[float, float] | None = None


class HeatPoint(Frozen):
    """One near miss on the heatmap (frame pixels)."""

    event_id: str
    x: float
    y: float
    alert: bool | None


class Heatmap(Frozen):
    """Where near misses happened on one camera's frame, binned on a grid.

    Attributes:
        camera_id: The camera.
        day: The day counted.
        width: Frame width in pixels (of the background frame, else of the points' extent).
        height: Frame height in pixels.
        cols: Grid columns.
        rows: Grid rows.
        cells: Near misses per cell, `rows` lists of `cols` counts.
        points: Each near miss with a known position.
        unplaced: Near misses whose position was not kept (no track window).
        background: `GET` path of a recorded frame of the camera, when one exists.
    """

    camera_id: str
    day: date
    width: int
    height: int
    cols: int
    rows: int
    cells: tuple[tuple[int, ...], ...]
    points: tuple[HeatPoint, ...]
    unplaced: int
    background: str | None = None


class StoryStep(Frozen):
    """One step of the connected warehouse story and whether it has happened.

    Attributes:
        key: Stable id (`request`, `plan`, `rule_ci`, ...).
        title: What the step is, in plain words.
        status: `done`, `waiting` (in progress or needs a person), `not_yet` or `failed`.
        detail: What happened, in one or two sentences, from the records.
        refs: Ids behind it (plan id, event id, record hashes, file names).
    """

    key: str
    title: str
    status: StepStatus
    detail: str
    refs: dict[str, str] = Field(default_factory=dict)


class CantCheck(Frozen):
    """Something the planner said it cannot check (or only with a caveat), in its own words."""

    concern: str
    status: str
    explanation: str


class WarehouseStory(Frozen):
    """One plan's story, step by step, plus the incident it follows.

    Attributes:
        plan_id: The plan, `None` before any request.
        approved: Whether a person approved it.
        request: The owner's words (first text item, or the transcript of a spoken request).
        request_lang: Their language.
        rules: Each rule's id and severity.
        cant_check: What the planner said it cannot check, or only with a caveat.
        event_id: The incident the story follows (the newest alerted one, else the newest).
        steps: The steps in order.
        near_misses: The day's near misses of this plan's rules, newest first.
    """

    plan_id: str | None
    approved: bool
    request: str
    request_lang: str | None
    rules: tuple[dict[str, str], ...]
    cant_check: tuple[CantCheck, ...]
    event_id: str | None
    steps: tuple[StoryStep, ...]
    near_misses: tuple[NearMiss, ...]


class TalkSection(BaseModel):
    """One part of a toolbox talk, as Spark writes it."""

    model_config = ConfigDict(extra="forbid")

    heading: str = Field(max_length=120)
    text: str = Field(max_length=1200)


class ToolboxDraft(BaseModel):
    """What Spark returns for a toolbox talk (structured output)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=160)
    lang: str = Field(max_length=16)
    minutes: float = Field(ge=0.5, le=10)
    sections: list[TalkSection] = Field(min_length=1, max_length=8)
    key_points: list[str] = Field(min_length=1, max_length=6)
    questions: list[str] = Field(default_factory=list, max_length=4)
    illustration_prompt: str = Field(max_length=800)


class ToolboxTalk(Frozen):
    """A two-minute toolbox talk written after one incident.

    Attributes:
        event_id: The incident.
        lang: The language it is written in (the worker's).
        title: The talk's title.
        minutes: How long it takes to read out, as Spark estimates.
        sections: The talk, part by part.
        key_points: What everyone should remember.
        questions: Questions to check understanding.
        illustration_prompt: What Muse Image was asked to draw (the safe behaviour).
        illustration: File name of the drawing (`GET /warehouse/files/{name}`), if drawn.
        illustration_error: Why there is no drawing, when it failed.
        model: The model that wrote it.
        image_model: The model that drew it.
        incident_hash: Hash of the incident's candidate record it was written from.
        created_at: When it was written.
    """

    event_id: str
    lang: str
    title: str
    minutes: float
    sections: tuple[TalkSection, ...]
    key_points: tuple[str, ...]
    questions: tuple[str, ...]
    illustration_prompt: str
    illustration: str | None = None
    illustration_error: str | None = None
    model: str
    image_model: str | None = None
    incident_hash: str
    created_at: datetime


class WarningDraft(BaseModel):
    """What Spark returns for a spoken warning (structured output)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=200)
    english: str = Field(max_length=200)


class SpokenWarning(Frozen):
    """The loudspeaker warning for one incident, in the worker's language, checked by ear.

    Attributes:
        event_id: The incident.
        lang: The language spoken.
        text: What is said.
        english: Spark's English gloss of it, for the owner.
        audio: File name of the WAV (`GET /warehouse/files/{name}`), if synthesised.
        tts: Which speech engine spoke it (MMS-TTS is Meta's; the system voice is not).
        heard: What Muse Voice Transcribe heard when it played the WAV back, if checked.
        wer: Word error rate between `text` and `heard`.
        roundtrip_ok: Whether the round trip passed.
        error: Why a part is missing.
        model: The model that wrote the text.
        created_at: When it was made.
    """

    event_id: str
    lang: str
    text: str
    english: str
    audio: str | None = None
    tts: str | None = None
    heard: str | None = None
    wer: float | None = None
    roundtrip_ok: bool | None = None
    error: str | None = None
    model: str
    created_at: datetime


class ShiftCounts(Frozen):
    """The day's chained facts a shift report is written from (counted, not judged).

    Attributes:
        day: The day.
        candidates: Candidates raised.
        alerts: Candidates the policy alerted on.
        suppressed: Candidates the verifier rejected.
        human_queue: Candidates sent to a person.
        acked: Alerts acknowledged.
        disputes: Disputes filed.
        upheld: Disputes upheld (false alarms).
        regressions: False-alarm labels (each a permanent Rule CI regression).
        blind_records: Coverage records (a camera went blind or came back).
        by_rule: Candidates per rule id.
        closest_m: The smallest measured separation of the day.
        fastest_mps: The highest vehicle speed in a near miss.
    """

    day: date
    candidates: int
    alerts: int
    suppressed: int
    human_queue: int
    acked: int
    disputes: int
    upheld: int
    regressions: int
    blind_records: int
    by_rule: dict[str, int]
    closest_m: float | None
    fastest_mps: float | None


class ShiftDraft(BaseModel):
    """What Spark returns for a shift safety report (structured output)."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(max_length=200)
    summary: str = Field(max_length=2000)
    patterns: list[str] = Field(default_factory=list, max_length=6)
    actions: list[str] = Field(default_factory=list, max_length=6)
    lang: str = Field(max_length=16)


class ShiftReport(Frozen):
    """The shift safety report: the counted facts and Spark's write-up of them."""

    day: date
    counts: ShiftCounts
    headline: str
    summary: str
    patterns: tuple[str, ...]
    actions: tuple[str, ...]
    lang: str
    model: str
    chain_root: str | None
    created_at: datetime


class TalkRequest(BaseModel):
    """Body of `POST /warehouse/incidents/{event_id}/toolbox-talk` and `/warning`."""

    model_config = ConfigDict(extra="forbid")

    lang: str = Field(default="es", pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
    illustrate: bool = True


class ShiftRequest(BaseModel):
    """Body of `POST /warehouse/shift-report`."""

    model_config = ConfigDict(extra="forbid")

    day: date | None = None
    lang: str = Field(default="en", pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
