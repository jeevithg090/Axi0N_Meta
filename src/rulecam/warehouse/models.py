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
        training_done: Whether the refresher training it made due was marked done (chained).
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
    training_done: bool = False
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
    also_text: str | None = Field(default=None, max_length=200)


class SpokenPart(Frozen):
    """One language of a spoken warning, spoken and checked on its own.

    Attributes:
        lang: Its language.
        text: What is said in it.
        tts: The engine that spoke it.
        heard: What Muse Voice Transcribe heard.
        wer: Word error rate of `heard` against `text`.
        roundtrip_ok: Whether the round trip passed.
    """

    lang: str
    text: str
    tts: str | None = None
    heard: str | None = None
    wer: float | None = None
    roundtrip_ok: bool | None = None


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
        parts: Every language spoken, in order (the crew's first, then `also`), each checked; the
            WAV plays them one after the other. The fields above describe the first.
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
    parts: tuple[SpokenPart, ...] = ()


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
    also: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class ShiftRequest(BaseModel):
    """Body of `POST /warehouse/shift-report`."""

    model_config = ConfigDict(extra="forbid")

    day: date | None = None
    lang: str = Field(default="en", pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class ClauseDraft(BaseModel):
    """What Spark returns when it looks up the near-miss training clause (structured output)."""

    model_config = ConfigDict(extra="forbid")

    clause: str = Field(max_length=80)
    title: str = Field(max_length=200)
    text: str = Field(max_length=1500)
    source_url: str = Field(max_length=500)


class GroundedClause(Frozen):
    """The regulation a forklift near miss triggers, as Spark found it on the web.

    Attributes:
        clause: The citation, e.g. `29 CFR 1910.178(l)(4)(ii)(B)`.
        title: Its heading.
        text: Its words as Spark quotes them.
        source_url: The page Spark cites.
        source_retrieved: Whether that page is one the search actually returned (else the
            citation is unverified and the page says so).
        source_title: The returned page's title, when retrieved.
        model: The model that looked it up.
        created_at: When.
    """

    clause: str
    title: str
    text: str
    source_url: str
    source_retrieved: bool
    source_title: str = ""
    model: str
    created_at: datetime


class DutyDraft(BaseModel):
    """What Spark returns when it reads one incident for the training duty (structured output)."""

    model_config = ConfigDict(extra="forbid")

    applies: bool
    why: str = Field(max_length=600)
    vehicle_unit: str | None = Field(default=None, max_length=40)
    unit_evidence: str = Field(default="", max_length=300)
    training_focus: list[str] = Field(default_factory=list, max_length=5)
    due: str = Field(max_length=200)


DutyStatus = Literal["due", "done", "not_required"]


class RefresherDuty(Frozen):
    """The refresher training a near miss makes due, and whether someone has done it.

    The forklift is named by what is painted on it (unit or fleet number), never by a person.

    Attributes:
        event_id: The near miss.
        rule_id: Its rule.
        camera_id: Its camera.
        applies: Whether Spark judged the clause to apply to this incident.
        why: Spark's reason.
        clause: The clause, grounded on a web page.
        vehicle_unit: The vehicle's painted unit number, when readable in the frames.
        unit_evidence: Where in the frames Spark read it.
        training_focus: What the refresher should cover.
        due: When it is due, in Spark's words.
        status: `due`, `done` or `not_required`.
        done_by: The role that marked it done.
        done_at: When.
        done_hash: Hash of the chained `label` record that marked it done.
        model: The model that read the incident.
        created_at: When the duty was raised.
    """

    event_id: str
    rule_id: str
    camera_id: str
    applies: bool
    why: str
    clause: GroundedClause
    vehicle_unit: str | None
    unit_evidence: str
    training_focus: tuple[str, ...]
    due: str
    status: DutyStatus
    done_by: str | None = None
    done_at: datetime | None = None
    done_hash: str | None = None
    model: str
    created_at: datetime


class ReportDraft(BaseModel):
    """What Spark returns for a spoken near-miss report (structured output)."""

    model_config = ConfigDict(extra="forbid")

    lang: str = Field(max_length=16)
    summary_en: str = Field(max_length=600)
    what_happened: str = Field(max_length=600)
    where: str = Field(default="", max_length=200)
    minutes_ago: float | None = Field(default=None, ge=0, le=24 * 60)
    vehicle_unit: str | None = Field(default=None, max_length=40)
    matches_event_id: str | None = Field(default=None, max_length=128)
    match_reason: str = Field(default="", max_length=600)
    proposed_rule: str = Field(max_length=300)
    proposed_rule_en: str = Field(max_length=300)


class VoiceReport(Frozen):
    """A near miss a worker reported by voice, checked against what the cameras measured.

    The audio is never kept; the transcript is. Nobody is identified: the report has no name,
    address or device, and the vehicle is named by its painted unit number if the worker said it.

    Attributes:
        report_id: The report's id.
        lang: The language the worker spoke.
        transcript: What Muse Voice Transcribe heard.
        summary_en: Spark's English summary for the owner.
        what_happened: Spark's summary in the worker's language.
        where: Where it happened, in the worker's words.
        minutes_ago: When, as the worker said it.
        vehicle_unit: The forklift's unit number, if said.
        candidates: Event ids of the camera near misses Spark was shown (in the time window).
        matches_event_id: The camera near miss Spark matched it to, if any.
        match_reason: Why (or why none matched).
        measured_m: The matched near miss's measured separation.
        proposed_rule: The rule Spark proposes, in the worker's language.
        proposed_rule_en: The same in English.
        plan_id: The plan the proposal started (planner + Rule CI), once proposed.
        chained_hash: Hash of the `label` record chained on the matched near miss.
        model: The model that read it.
        created_at: When.
    """

    report_id: str
    lang: str
    transcript: str
    summary_en: str
    what_happened: str
    where: str
    minutes_ago: float | None
    vehicle_unit: str | None
    candidates: tuple[str, ...]
    matches_event_id: str | None
    match_reason: str
    measured_m: float | None
    proposed_rule: str
    proposed_rule_en: str
    plan_id: str | None = None
    chained_hash: str | None = None
    model: str
    created_at: datetime


class DutyDone(BaseModel):
    """Body of `POST /warehouse/incidents/{event_id}/duty/done`."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(default="owner", min_length=1, max_length=64, pattern=r"^[A-Za-z][\w-]*$")


class VoiceNoteRequest(BaseModel):
    """Body of `POST /warehouse/incidents/{event_id}/voice-note`: which warning, to whom."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][\w-]*$")
    lang: str = Field(default="es", pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
    also: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
