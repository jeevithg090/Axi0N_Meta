"""What the glasses assistant sends and gets back (`POST /wearables/look`, safety walks).

The model-facing schemas (`GlimmerLook`, `Composed`, `WalkSummary`) stay small and flat: they go
to Muse Glimmer as strict JSON schemas and to Muse Spark as structured outputs.
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

    scene: str = Field(description="One sentence: what is in front of the wearer.")
    answer: str = Field(description="A direct answer to the wearer's question; empty if none.")
    hazards: list[str] = Field(description="Safety hazards visible now, each with where it is.")
    alert: bool = Field(description="True if the wearer should be warned right now.")
    count: list[str] = Field(
        description="Up to 3 short noun phrases SAM 3.1 should count to check the answer."
    )


class Hazard(Frozen):
    """One hazard in the wearer's view."""

    what: str = Field(description="What it is, e.g. 'forklift reversing'.")
    where: str = Field(description="Where, relative to the wearer: 'ahead left', 'right, 3 m'.")
    severity: HazardSeverity


class Composed(Frozen):
    """Muse Spark's answer for the wearer's ear, after checking the other models' evidence."""

    spoken: str = Field(description="What to say in the wearer's ear: 1-2 short sentences.")
    detail: str = Field(description="A fuller answer for the screen, with the evidence used.")
    alert: bool = Field(description="True only if a hazard the wearer must act on is confirmed.")
    severity: Severity
    hazards: list[Hazard] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


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
    spoken: str = Field(description="What the wearer hears; empty when there is nothing to say.")
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
