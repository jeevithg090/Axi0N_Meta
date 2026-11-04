"""What the glasses assistant sends and gets back (`POST /wearables/look`, safety walks).

The model-facing schemas (`GlimmerLook`, `SparkLook`, `Composed`, `WalkSummary`) stay small and
flat, with short fields, so the models answer fast: they go to Muse Glimmer as strict JSON schemas
and to Muse Spark as structured outputs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from rulecam.contracts import Frozen

Mode = Literal["aisle", "warehouse", "crowd", "general"]
Severity = Literal["none", "info", "caution", "danger"]
HazardSeverity = Literal["info", "caution", "danger"]


class GlimmerLook(Frozen):
    """Muse Glimmer's first look at the wearer's view."""

    scene: str = Field(description="One short sentence: what is in front of the wearer.")
    answer: str = Field(description="For the ear: one sentence, at most 15 words.")
    hazards: list[str] = Field(description="Hazards visible now, a few words each, with where.")
    alert: bool = Field(description="True if the wearer must be warned right now.")


class Hazard(Frozen):
    """One hazard in the wearer's view."""

    what: str = Field(description="What it is, e.g. 'forklift reversing'.")
    where: str = Field(description="Where, relative to the wearer: 'ahead left', 'right, 3 m'.")
    severity: HazardSeverity


class SparkLook(Frozen):
    """Muse Spark's own look, in parallel with Glimmer's: a short answer for the ear."""

    spoken: str = Field(description="For the ear: one sentence, at most 15 words.")
    alert: bool = Field(description="True if the wearer must be warned right now.")
    severity: Severity
    hazards: list[Hazard] = Field(default_factory=list)
    count: list[str] = Field(
        default_factory=list,
        description="Only if the answer needs an exact number: up to 3 noun phrases to count.",
    )
    confidence: float = Field(ge=0.0, le=1.0)


class Composed(Frozen):
    """Muse Spark's checked answer when SAM 3.1 counted or the shelf check found gaps."""

    spoken: str = Field(description="For the ear: one sentence, at most 20 words.")
    detail: str = Field(description="At most two sentences for the screen: the evidence used.")
    alert: bool = Field(description="True only if a hazard the wearer must act on is confirmed.")
    severity: Severity
    hazards: list[Hazard] = Field(default_factory=list)
    rejected: list[str] = Field(
        default_factory=list,
        description="SAM 3.1 labels that do not fit the frame, e.g. '1 hard hat: a warning light'.",
    )
    confidence: float = Field(ge=0.0, le=1.0)


class Line(Frozen):
    """A sentence spoken in the wearer's ear."""

    text: str
    audio: str | None = None
    by: str = Field(description="The model whose words these are.")
    at_ms: int = Field(description="Milliseconds after the frame arrived when it was ready.")


class Count(Frozen):
    """SAM 3.1's detections of one concept: boxes `[x1, y1, x2, y2, confidence]` in pixels."""

    concept: str
    count: int
    boxes: list[list[float]] = Field(default_factory=list)


class Gap(Frozen):
    """A shelf gap Muse Spark found and SAM 3.1 confirmed empty (from `rulecam.shelf`)."""

    number: int
    section: str
    neighbours: str
    facings: int


class Stage(Frozen):
    """One step of an answer, timed."""

    name: str
    model: str
    ms: int
    ok: bool
    note: str = ""


class PovAnswer(Frozen):
    """The answer to one glasses frame (with or without a question)."""

    answer_id: str
    at: datetime
    mode: Mode
    kind: Literal["question", "patrol"]
    question: str | None = None
    heard: str | None = Field(default=None, description="Voice Transcribe's text of the audio.")
    first: Line | None = Field(
        default=None, description="The first sentence, from whichever model answered first."
    )
    spoken: str = Field(description="The checked line after it; empty when nothing to add.")
    detail: str = ""
    alert: bool = False
    verified: bool = Field(
        default=False, description="The alert is backed by at least two independent models."
    )
    verified_by: list[str] = Field(default_factory=list)
    severity: Severity = "none"
    confidence: float = 0.0
    hazards: list[Hazard] = Field(default_factory=list)
    counts: list[Count] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    look: GlimmerLook | None = None
    spark_look: SparkLook | None = None
    rejected: list[str] = Field(default_factory=list, description="SAM labels Spark rejected.")
    privacy_edits: int = Field(default=0, description="Appearance phrases cut from model text.")
    looked_by: str = ""
    frame: str
    marked: str | None = None
    audio: str | None = None
    voice: str | None = None
    stages: list[Stage] = Field(default_factory=list)
    total_ms: int = 0
    walk_id: str | None = None
    source: str = Field(
        default="phone-camera",
        description="Where the frame came from: phone-camera, meta-glasses or mock-device.",
    )


class WalkHazard(Frozen):
    """A hazard of a safety walk, with the answers (frames) that show it."""

    what: str
    where: str
    severity: HazardSeverity
    answer_ids: list[str] = Field(description="Ids of the frames that show it.")
    action: str = Field(description="What someone should do about it.")


class WalkSummary(Frozen):
    """Muse Spark's report of a safety walk."""

    summary: str = Field(description="Two to four sentences for the site manager.")
    spoken: str = Field(description="One sentence to say in the wearer's ear.")
    hazards: list[WalkHazard] = Field(default_factory=list)
    restock: list[str] = Field(default_factory=list, description="Shelf gaps to refill, if any.")


class Walk(Frozen):
    """A safety walk: the frames the glasses sent between start and finish."""

    walk_id: str
    mode: Mode
    note: str = ""
    started_at: datetime
    finished_at: datetime | None = None
    frames: int = 0


class WalkReport(Frozen):
    """A finished walk: the summary and where it sits in the evidence chain."""

    walk: Walk
    summary: WalkSummary
    answers: list[PovAnswer]
    chain_hashes: list[str] = Field(default_factory=list)
    model: str
    ms: int
