"""The 'Ask' clarifying questions step (task P2-05, MP §3.2).

Proposes at most 5 clarifying questions via Spark 1.3 only where required plan fields
are missing (e.g. exit door identity, notification channels, schedule, ready-to-eat prep counter).
Supports interactive frame taps ({camera_id, x, y}) mapping directly to physical zones or objects.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field
from shapely.geometry import Point, Polygon

from rulecam.clients.meta_hosted.spark import SPARK_1_3
from rulecam.contracts import StructuredLLM
from rulecam.contracts.core import Detection, Frozen, Zone
from rulecam.log import get_logger
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE, Concern

_log = get_logger(__name__)

MAX_CLARIFYING_QUESTIONS = 5
MIN_POLYGON_VERTICES = 3

ASK_SYSTEM_PROMPT = (
    "You are Rulecam's site planning interviewer. Identify essential missing operational details "
    "needed to establish strict automated safety and compliance rules. "
    "Propose at most 5 focused clarifying questions. Only ask about fields not already known or "
    "clear from inventory/concerns (e.g. which door is the emergency exit, notification role "
    "channels, operating schedule, or which prep table is ready-to-eat). "
    "If identifying a physical station or area on screen is needed, set requires_frame_tap=True.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)


class Question(Frozen):
    """A clarifying question for the user or site manager."""

    question_id: str
    text: str
    target_field: str
    requires_frame_tap: bool = False
    camera_id: str | None = None
    options: tuple[str, ...] = ()


class FrameTapAnswer(Frozen):
    """User answer provided by tapping a pixel on a camera frame."""

    camera_id: str
    x: float
    y: float


class ProposedQuestions(BaseModel):
    """Schema for Spark structured output."""

    questions: list[Question] = Field(default_factory=list, max_length=MAX_CLARIFYING_QUESTIONS)


def map_tap_to_zone(tap: FrameTapAnswer, zones: Sequence[Zone]) -> Zone | None:
    """Map a tapped frame coordinate to the containing Zone polygon.

    Args:
        tap: Coordinate {camera_id, x, y} of the user tap.
        zones: List of candidate Zones on the site.

    Returns:
        The matching Zone if the tap falls within its polygon on the same camera, or None.
    """
    point = Point(tap.x, tap.y)
    for zone in zones:
        if zone.camera_id != tap.camera_id:
            continue
        if zone.polygon_px and len(zone.polygon_px) >= MIN_POLYGON_VERTICES:
            # shapely Polygon expects [(x, y), ...]
            poly = Polygon(zone.polygon_px)
            if poly.contains(point) or poly.touches(point):
                return zone
    return None


def map_tap_to_detection(tap: FrameTapAnswer, detections: Sequence[Detection]) -> Detection | None:
    """Map a tapped frame coordinate to the best matching object Detection box.

    Args:
        tap: Coordinate {camera_id, x, y} of the user tap.
        detections: Candidate object detections on the camera.

    Returns:
        The matching Detection if the tap falls within its box, or None.
    """
    matching: list[Detection] = []
    for d in detections:
        if d.camera_id != tap.camera_id:
            continue
        x1, y1, x2, y2 = d.mask.box_xyxy
        if x1 <= tap.x <= x2 and y1 <= tap.y <= y2:
            matching.append(d)

    if not matching:
        return None
    # Pick the most specific (smallest area box) if multiple overlap
    return min(
        matching,
        key=lambda d: (
            (d.mask.box_xyxy[2] - d.mask.box_xyxy[0]) * (d.mask.box_xyxy[3] - d.mask.box_xyxy[1])
        ),
    )


def propose_clarifying_questions(
    concerns: Sequence[Concern | str],
    known_fields: Mapping[str, object],
    *,
    spark: StructuredLLM,
    inventory_concepts: Sequence[str] = (),
    max_questions: int = MAX_CLARIFYING_QUESTIONS,
) -> tuple[Question, ...]:
    """Generate up to `max_questions` focused clarifying questions for missing fields.

    Args:
        concerns: Safety/compliance concerns identified from intake.
        known_fields: Fields already established (e.g. {"roles": ..., "schedule": ...}).
        spark: StructuredLLM client.
        inventory_concepts: Physical items already identified during camera survey.
        max_questions: Upper bound on questions asked (default: 5).

    Returns:
        Tuple of up to `max_questions` Question instances.
    """
    concern_texts = [c.text if isinstance(c, Concern) else str(c) for c in concerns]
    prompt_lines = [
        "Identified safety concerns:",
        *(f"- {t}" for t in concern_texts),
        "",
        "Already established site configuration fields:",
        *(f"- {k}: {v}" for k, v in known_fields.items()),
        "",
        "Observed physical inventory concepts in view:",
        *(f"- {c}" for c in inventory_concepts),
        "",
        f"Generate at most {max_questions} clarifying questions for missing fields.",
    ]
    user_prompt = "\n".join(prompt_lines)

    res = spark.structured(
        model=SPARK_1_3,
        messages=[
            {"role": "system", "content": ASK_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        schema=ProposedQuestions,
    )

    # Strictly cap at max_questions
    capped = res.questions[:max_questions]
    return tuple(capped)
