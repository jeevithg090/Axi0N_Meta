"""The 'Ask' step: clarifying questions, answered in words or by tapping a frame (task P2-05).

Spark 1.3 proposes questions only for plan fields that are still missing (which door is the
exit, role channels, schedule, which counter is ready-to-eat prep). The step enforces that in
code too: questions about known fields and repeated fields are dropped, and at most 5 are kept.

A question can ask for a frame tap. The answer `{camera_id, x, y}` maps to the smallest zone
polygon or object box on that camera containing the point. Taps never map to a person (no
identity, guardrail R13): person detections are ignored.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import BaseModel, Field
from shapely.geometry import Point, Polygon

from rulecam.contracts import StructuredLLM
from rulecam.contracts.core import Detection, Frozen, Zone
from rulecam.log import get_logger
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE, Concern

_log = get_logger(__name__)

ASK_MODEL: Final = "muse-spark-1.3"
MAX_CLARIFYING_QUESTIONS: Final = 5
MIN_POLYGON_VERTICES: Final = 3
PERSON_CONCEPT: Final = "person"

ASK_SYSTEM_PROMPT = (
    "You are Rulecam's site planning interviewer. You get the site's concerns, the plan fields "
    "already known, and the concepts the cameras can see, as JSON. Ask at most "
    f"{MAX_CLARIFYING_QUESTIONS} short questions, and only about plan fields that are missing "
    "and needed to write the rules (e.g. which door is the emergency exit, which channel each "
    "role is alerted on, the operating schedule, which counter is ready-to-eat prep). Never ask "
    "about a field that is already known. When the answer is a place or object on camera, set "
    "requires_frame_tap and camera_id so the user can tap it. Never ask anyone to identify, "
    "name or tap a person.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)


class Question(Frozen):
    """A clarifying question for the site manager."""

    question_id: str
    text: str
    target_field: str
    requires_frame_tap: bool = False
    camera_id: str | None = None
    options: tuple[str, ...] = ()


class FrameTapAnswer(Frozen):
    """An answer given by tapping a point (in image pixels) on a camera frame."""

    camera_id: str
    x: float
    y: float


class ProposedQuestions(BaseModel):
    """Spark's proposed questions (capped to 5 in code, not by validation)."""

    questions: list[Question] = Field(default_factory=list)


def propose_clarifying_questions(
    concerns: Sequence[Concern | str],
    known_fields: Mapping[str, object],
    *,
    spark: StructuredLLM,
    inventory_concepts: Sequence[str] = (),
    max_questions: int = MAX_CLARIFYING_QUESTIONS,
) -> tuple[Question, ...]:
    """Ask Spark which missing plan fields need a question.

    Args:
        concerns: The concerns from intake.
        known_fields: Plan fields already known, field name -> value.
        spark: The structured-output client.
        inventory_concepts: Concepts the survey found.
        max_questions: Cap on questions, at most 5.

    Returns:
        At most `min(max_questions, 5)` questions, one per missing field, in Spark's order.
    """
    data = {
        "concerns": [c if isinstance(c, str) else c.text for c in concerns],
        "known_fields": {k: str(v) for k, v in known_fields.items()},
        "concepts_in_view": list(inventory_concepts),
    }
    result = spark.structured(
        model=ASK_MODEL,
        messages=[
            {"role": "system", "content": ASK_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
        schema=ProposedQuestions,
    )
    limit = min(max_questions, MAX_CLARIFYING_QUESTIONS)
    asked: set[str] = set()
    kept: list[Question] = []
    for question in result.questions:
        field = question.target_field.strip()
        if field in known_fields or field in asked:
            continue
        asked.add(field)
        kept.append(question)
    dropped = len(result.questions) - len(kept[:limit])
    if dropped:
        _log.info("ask_questions_dropped", proposed=len(result.questions), dropped=dropped)
    return tuple(kept[:limit])


def map_tap_to_zone(tap: FrameTapAnswer, zones: Sequence[Zone]) -> Zone | None:
    """Return the smallest polygon zone on the tapped camera that contains the point.

    Args:
        tap: The tap.
        zones: Candidate zones; buffer zones and polygons with fewer than 3 points are skipped.

    Returns:
        The zone (a point on its edge counts), or `None`.
    """
    point = Point(tap.x, tap.y)
    hits: list[tuple[float, Zone]] = []
    for zone in zones:
        polygon_px = zone.polygon_px or ()
        if zone.camera_id != tap.camera_id or len(polygon_px) < MIN_POLYGON_VERTICES:
            continue
        polygon = Polygon(polygon_px)
        if polygon.covers(point):
            hits.append((polygon.area, zone))
    return min(hits, key=lambda hit: hit[0])[1] if hits else None


def map_tap_to_detection(tap: FrameTapAnswer, detections: Sequence[Detection]) -> Detection | None:
    """Return the object on the tapped camera whose box contains the point (smallest box wins).

    Person detections are never matched: a tap can name a door or a counter, not a person.

    Args:
        tap: The tap.
        detections: Detections on the tapped frame.

    Returns:
        The detection, or `None`.
    """

    def area(d: Detection) -> float:
        x1, y1, x2, y2 = d.mask.box_xyxy
        return (x2 - x1) * (y2 - y1)

    hits = [
        d
        for d in detections
        if d.camera_id == tap.camera_id
        and d.concept != PERSON_CONCEPT
        and d.mask.box_xyxy[0] <= tap.x <= d.mask.box_xyxy[2]
        and d.mask.box_xyxy[1] <= tap.y <= d.mask.box_xyxy[3]
    ]
    return min(hits, key=area) if hits else None
