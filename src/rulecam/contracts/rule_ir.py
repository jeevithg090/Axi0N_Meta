"""Typed Rule IR (task book §4.2, V2 §6, MP §7.3).

Predicates are a discriminated union on `op`; evaluation lives in `rulecam.rules`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from rulecam.contracts.core import Frozen, Severity

ObjRef = str  # "<concept>" or "<concept>.foot_point" / "<concept>.mask" / "<concept>.hands"


class DistanceM(Frozen):
    """Ground distance in metres between two object references."""

    op: Literal["distance_m"] = "distance_m"
    a: ObjRef
    b: ObjRef
    lt: float | None = None
    gt: float | None = None


class Inside(Frozen):
    """The object's foot point lies inside a zone."""

    op: Literal["inside"] = "inside"
    object: str
    zone: str


class Overlap(Frozen):
    """Two objects overlap with at least `min_iou`."""

    op: Literal["overlap"] = "overlap"
    a: str
    b: str
    min_iou: float = 0.0


class Without(Frozen):
    """Negation by composition: subject has no `item` mask intersecting `region` (V2 Pillar 2)."""

    op: Literal["without"] = "without"
    subject: str
    item: str
    region: Literal["full", "top_25pct", "hands"] = "full"


class Moving(Frozen):
    """The object's ground speed exceeds a threshold."""

    op: Literal["moving"] = "moving"
    object: str
    speed_mps_gt: float


class Count(Frozen):
    """The number of objects (optionally in a zone) is above or below a bound."""

    op: Literal["count"] = "count"
    object: str
    zone: str | None = None
    gt: int | None = None
    lt: int | None = None


class PoseState(Frozen):
    """A pose-derived state of a person (P4 pose features)."""

    op: Literal["pose"] = "pose"
    object: str = "person"
    state: Literal["down", "hands_above_shoulder", "ladder_three_point_lost", "bent_back_lift"]


class StateCheck(Frozen):
    """A visual state of an object, e.g. a door being open."""

    op: Literal["state"] = "state"
    object: str
    state: str  # e.g. "open", "closed", "present"


class All(Frozen):
    """Logical AND of sub-predicates."""

    op: Literal["all"] = "all"
    of: tuple[Predicate, ...]


class Any_(Frozen):  # noqa: N801 - trailing underscore avoids shadowing the builtin `any`
    """Logical OR of sub-predicates."""

    op: Literal["any"] = "any"
    of: tuple[Predicate, ...]


class Not_(Frozen):  # noqa: N801 - trailing underscore mirrors `Any_` for the keyword `not`
    """Logical NOT of a sub-predicate."""

    op: Literal["not"] = "not"
    of: Predicate


class Persist(Frozen):
    """The sub-predicate holds continuously for at least `seconds`."""

    op: Literal["persist"] = "persist"
    of: Predicate
    seconds: float = Field(gt=0)


class Dwell(Frozen):
    """Total time `object` is inside `zone`, allowing gaps up to `max_gap_s` (MP §1, handwash)."""

    op: Literal["dwell"] = "dwell"
    object: str
    zone: str
    total_s_gte: float
    max_gap_s: float = 0.0


class Absence(Frozen):
    """No `object` inside `zone` for at least `seconds`."""

    op: Literal["absence"] = "absence"
    object: str
    zone: str
    seconds: float


class Rate(Frozen):
    """Rising edges of the sub-predicate exceed a per-minute rate."""

    op: Literal["rate"] = "rate"
    of: Predicate
    per_minute_gt: float


class Sequence(Frozen):
    """Ordered requirement between two predicates for the same subject.

    With `semantics="past"`: when `trigger` becomes true, `requirement` must have been satisfied
    within the previous `require_within_s` seconds for the same track; otherwise it is a
    violation. With `semantics="future"`, the requirement must follow within the window.
    """

    op: Literal["sequence"] = "sequence"
    semantics: Literal["past", "future"]
    trigger: Predicate
    require_within_s: float
    requirement: Predicate


Predicate = Annotated[
    DistanceM
    | Inside
    | Overlap
    | Without
    | Moving
    | Count
    | PoseState
    | StateCheck
    | All
    | Any_
    | Not_
    | Persist
    | Dwell
    | Absence
    | Rate
    | Sequence,
    Field(discriminator="op"),
]


class ConceptSpec(Frozen):
    """A SAM concept used by a rule."""

    prompt: str
    static: bool = False  # segment once and cache (sink, door, racks)


class ZoneSpec(Frozen):
    """A zone as declared inside a rule."""

    kind: Literal["polygon", "buffer"]
    camera_id: str
    polygon_px: tuple[tuple[float, float], ...] | None = None
    buffer_of: str | None = None
    buffer_m: float | None = None


class ActionSpec(Frozen):
    """An action the response agent takes when the rule fires."""

    kind: Literal[
        "speak", "visual_signal", "coach_station", "notify", "escalate", "interlock_cut", "log"
    ]
    role: str | None = None
    lang: str | None = None  # "auto" / "crew" / ISO code
    after_s: float | None = None  # for escalate


class VerifySpec(Frozen):
    """How candidates of this rule are verified."""

    tier1: Literal["glimmer"] = "glimmer"
    tier2: Literal["spark-open-on-disagree", "spark-open-on-dispute", "none"] = (
        "spark-open-on-disagree"
    )
    min_confidence: float = 0.7
    calibrated_alpha: float | None = None


class RuleSource(Frozen):
    """Where a rule came from (voice, text, sign, PDF, surprise event or a plan)."""

    kind: Literal["voice", "text", "sign_photo", "pdf", "surprise_event", "plan"]
    text: str | None = None
    lang: str | None = None
    plan_id: str | None = None
    standard: tuple[str, ...] = ()


class RuleIR(Frozen):
    """A compiled, inspectable rule."""

    id: str
    version: int = 1
    source: RuleSource
    concepts: dict[str, ConceptSpec]
    zones: dict[str, ZoneSpec] = Field(default_factory=dict)
    calibration: str | None = None
    predicate: Predicate
    severity: Severity
    actions: tuple[ActionSpec, ...]
    verify: VerifySpec = VerifySpec()
    accept: tuple[str, ...] = ()
    backtestable: Literal["yes", "partial", "no"] = "yes"
    schedule: str | None = None  # e.g. "21:00-07:00"


for _model in (All, Any_, Not_, Persist, Rate, Sequence):
    _model.model_rebuild()
