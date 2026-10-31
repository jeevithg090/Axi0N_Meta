"""Unit tests for clarifying questions, frame tap mapping, and standards grounding (task P2-05)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from rulecam.contracts.core import Detection, Mask, Zone
from rulecam.planner.models import Concern
from rulecam.planner.standards import (
    Citation,
    GroundedCitationResponse,
    StandardsCache,
    citation_text_match,
    fetch_standard_text,
    ground_concern_standard,
)
from rulecam.planner.steps.ask import (
    FrameTapAnswer,
    ProposedQuestions,
    Question,
    map_tap_to_detection,
    map_tap_to_zone,
    propose_clarifying_questions,
)
from rulecam.replay.errors import ReplayMiss

M = TypeVar("M", bound=BaseModel)


class FakeSparkLLM:
    """Fake StructuredLLM returning preset models."""

    def __init__(self, response: BaseModel) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        self.calls.append({"model": model, "messages": messages, "schema": schema})
        assert isinstance(self.response, schema)
        return self.response


def test_never_more_than_five_questions() -> None:
    """Verify propose_clarifying_questions strictly caps returned questions at 5."""
    five_questions = [
        Question(
            question_id=f"q_{i}",
            text=f"Question text {i}",
            target_field=f"field_{i}",
        )
        for i in range(5)
    ]
    spark = FakeSparkLLM(ProposedQuestions(questions=five_questions))

    questions = propose_clarifying_questions(
        concerns=["Handwashing compliance"],
        known_fields={},
        spark=spark,
        max_questions=5,
    )
    assert len(questions) == 5

    capped = propose_clarifying_questions(
        concerns=["Handwashing compliance"],
        known_fields={},
        spark=spark,
        max_questions=3,
    )
    assert len(capped) == 3
    assert [q.question_id for q in capped] == ["q_0", "q_1", "q_2"]

    with pytest.raises(ValidationError):
        ProposedQuestions(
            questions=[
                Question(question_id=f"q_{i}", text=f"Q{i}", target_field="f") for i in range(6)
            ]
        )


def test_frame_tap_maps_to_containing_zone() -> None:
    """Verify map_tap_to_zone accurately maps tapped coordinates to the enclosing Zone."""
    sink_zone = Zone(
        zone_id="zone_sink",
        camera_id="cam1",
        kind="polygon",
        polygon_px=((100.0, 100.0), (300.0, 100.0), (300.0, 400.0), (100.0, 400.0)),
    )
    door_zone = Zone(
        zone_id="zone_exit_door",
        camera_id="cam1",
        kind="polygon",
        polygon_px=((500.0, 100.0), (700.0, 100.0), (700.0, 500.0), (500.0, 500.0)),
    )
    cam2_zone = Zone(
        zone_id="zone_cam2_prep",
        camera_id="cam2",
        kind="polygon",
        polygon_px=((100.0, 100.0), (400.0, 100.0), (400.0, 400.0), (100.0, 400.0)),
    )

    zones = [sink_zone, door_zone, cam2_zone]

    # Tap inside sink zone on cam1
    tap_sink = FrameTapAnswer(camera_id="cam1", x=200.0, y=250.0)
    matched = map_tap_to_zone(tap_sink, zones)
    assert matched is not None
    assert matched.zone_id == "zone_sink"

    # Tap inside door zone on cam1
    tap_door = FrameTapAnswer(camera_id="cam1", x=550.0, y=200.0)
    matched_door = map_tap_to_zone(tap_door, zones)
    assert matched_door is not None
    assert matched_door.zone_id == "zone_exit_door"

    # Tap on cam2 in empty area
    tap_empty = FrameTapAnswer(camera_id="cam2", x=50.0, y=50.0)
    assert map_tap_to_zone(tap_empty, zones) is None

    # Tap on cam1 coordinates matching cam2 zone does NOT match cam2 zone
    tap_cross_cam = FrameTapAnswer(camera_id="cam1", x=200.0, y=200.0)
    # this matches sink_zone, not cam2_zone
    assert map_tap_to_zone(tap_cross_cam, zones) == sink_zone


def test_frame_tap_maps_to_detection_box() -> None:
    """Verify map_tap_to_detection locates object bounding box containing the point."""
    det_sink = Detection(
        camera_id="cam1",
        frame_idx=0,
        ts=datetime.now(UTC),
        concept="sink",
        object_id=1,
        mask=Mask(
            rle="rle",
            box_xyxy=(50.0, 50.0, 250.0, 250.0),
            confidence=0.95,
        ),
    )
    tap = FrameTapAnswer(camera_id="cam1", x=120.0, y=180.0)
    matched = map_tap_to_detection(tap, [det_sink])
    assert matched is not None
    assert matched.concept == "sink"

    tap_outside = FrameTapAnswer(camera_id="cam1", x=10.0, y=10.0)
    assert map_tap_to_detection(tap_outside, [det_sink]) is None


def test_citation_text_match_exact_and_whitespace_normalized() -> None:
    """Verify citation_text_match succeeds on exact and normalized quotes, and fails when absent."""
    raw_page = """
    Food Code 2026 - Chapter 2 Management and Personnel
    Section 2-301.12 Cleaning Procedure.
    Food employees shall clean their hands and exposed portions of their arms
    immediately before engaging in food preparation including working with exposed food,
    clean equipment and utensils, and unwrapped single-service and single-use articles.
    """

    # 1. Exact match
    c_exact = Citation(
        ref="§2-301.12",
        url="https://www.fda.gov/food-code-2026#2-301.12",
        quoted_text="Food employees shall clean their hands and exposed portions of their arms",
    )
    assert citation_text_match(c_exact, raw_page) is True

    # 2. Whitespace-normalized match (wrapped across newlines and spaces in page)
    c_wrapped = Citation(
        ref="§2-301.12",
        url="https://www.fda.gov/food-code-2026#2-301.12",
        quoted_text=(
            "clean their hands and exposed portions of their arms immediately before engaging"
        ),
    )
    assert citation_text_match(c_wrapped, raw_page) is True

    # 3. Fabricated or absent citation text returns False
    c_false = Citation(
        ref="§2-301.12",
        url="https://www.fda.gov/food-code-2026#2-301.12",
        quoted_text="Employees must sing Happy Birthday twice while washing hands",
    )
    assert citation_text_match(c_false, raw_page) is False


def test_standards_cache_hit_and_replay_mode(tmp_path: Path) -> None:
    """Verify standards cache disk storage and replay mode behavior."""
    cache = StandardsCache(cache_dir=tmp_path / "standards_cache")
    url = "https://www.fda.gov/food-code-2026"
    content = "FDA Food Code 2026 Official Text..."

    # In replay mode, missing url raises ReplayMiss
    with pytest.raises(ReplayMiss):
        fetch_standard_text(url, cache=cache, mode="replay")

    # Store content in cache
    cache.put(url, content)

    # Replay mode now retrieves cached content
    cached_text = fetch_standard_text(url, cache=cache, mode="replay")
    assert cached_text == content


def test_acceptance_criteria_uc1_kitchen_plan_cites_food_code_with_matched_text(
    tmp_path: Path,
) -> None:
    """Acceptance criteria: UC1 kitchen plan cites Food Code with matched text."""
    cache = StandardsCache(cache_dir=tmp_path / "standards_cache")
    fc_url = "https://www.fda.gov/food/food-code-2026"

    food_code_doc = (
        "Food Code 2026 Recommendations of the FDA.\n\n"
        "§ 2-103.11 Person in Charge - Duties.\n"
        "(G) Employees are effectively cleaning their hands, by routinely monitoring "
        "the employees' handwashing;\n\n"
        "§ 2-301.12 Cleaning Procedure.\n"
        "(A) Except as specified in ¶ (D) of this section, food employees shall clean their hands "
        "and exposed portions of their arms immediately before engaging in food preparation.\n"
    )
    cache.put(fc_url, food_code_doc)

    c_handwash = Citation(
        ref="Food Code §2-301.12",
        url=fc_url,
        quoted_text=(
            "food employees shall clean their hands and exposed portions of their arms "
            "immediately before engaging in food preparation"
        ),
    )
    c_monitor = Citation(
        ref="Food Code §2-103.11(G)",
        url=fc_url,
        quoted_text=(
            "Employees are effectively cleaning their hands, by routinely monitoring "
            "the employees' "
            "handwashing"
        ),
    )

    doc_text = fetch_standard_text(fc_url, cache=cache, mode="replay")
    assert citation_text_match(c_handwash, doc_text) is True
    assert citation_text_match(c_monitor, doc_text) is True


def test_ground_concern_standard() -> None:
    """Verify ground_concern_standard queries Spark and returns structured Citation."""
    citation = Citation(
        ref="Food Code §2-301.12",
        url="https://www.fda.gov/food-code",
        quoted_text="Food employees shall clean their hands",
    )
    spark = FakeSparkLLM(GroundedCitationResponse(citation=citation))

    res = ground_concern_standard(Concern(text="Handwash required", source_ref="text"), spark=spark)
    assert res is not None
    assert res.ref == "Food Code §2-301.12"
    assert res.quoted_text == "Food employees shall clean their hands"
