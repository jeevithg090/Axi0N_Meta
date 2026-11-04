"""The supermarket checks: the models judge, the code keeps their evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rulecam.retail.checks import (
    HazardChecker,
    QueueChecker,
    RetailError,
    ShelfChecker,
    Site,
    overlap_share,
    queue_headline,
)
from rulecam.retail.models import Finding, QueueJudgement
from rulecam.shelf import ShelfScanner
from tests.fakes import FakeClock, FakeLLM
from tests.retail.conftest import AT, FakeFrames, FakeSam, Files

SITE = Site("A supermarket.", {"till": "checkout lanes"})


def judgement(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "lanes": [
            {"lane": "left till", "open": True, "customers_waiting": 4, "being_served": True},
            {"lane": "right till", "open": False, "customers_waiting": 0, "being_served": False},
        ],
        "customers_waiting": 4,
        "est_wait_s": 180,
        "queue_moving": False,
        "customer_needs_help": False,
        "open_another_till": True,
        "alert_staff": True,
        "reason": "Four people wait at the only open till and the queue did not move.",
        "staff_message": "Open the right till now.",
        "queue_marks": [1, 2],
        "confidence": 0.8,
    }
    return base | overrides


def queue_checker(
    tmp_path: Path, frame: Path, spark: FakeLLM, earlier: list[Finding] | None = None
) -> QueueChecker:
    return QueueChecker(
        spark=spark,
        files=Files(),
        sam=FakeSam({"person": [(10, 10, 50, 150), (60, 10, 100, 150)]}),
        frames=FakeFrames(frame),
        site=SITE,
        clock=FakeClock(AT),
        marks_dir=tmp_path / "marks",
        history=lambda cam, kind: earlier or [],
    )


def test_queue_check_keeps_the_models_judgement(tmp_path: Path, frame: Path) -> None:
    spark = FakeLLM([judgement()])
    finding = queue_checker(tmp_path, frame, spark).check("till", AT)
    assert finding.kind == "queue"
    assert finding.act is True
    assert finding.metric == 4.0
    assert finding.camera_name == "checkout lanes"
    assert finding.detail["people_per_frame"] == [2, 2, 2]
    assert "open another till" in finding.headline
    assert finding.evidence.marked is not None
    assert len(finding.evidence.frames) == 3
    assert {c.model for c in finding.calls} == {"sam-3.1", "muse-spark-1.3"}
    content = spark.calls[0]["messages"][1]["content"]  # type: ignore[index]
    assert "SAM 3.1 people per frame" in content[0]["text"]
    assert len(content) == 5  # the text, three frames and the marked frame


def test_queue_check_no_alert_without_a_reason(tmp_path: Path, frame: Path) -> None:
    spark = FakeLLM([judgement(open_another_till=False, customer_needs_help=False)])
    assert queue_checker(tmp_path, frame, spark).check("till", AT).act is False


def test_queue_check_shows_earlier_readings(tmp_path: Path, frame: Path) -> None:
    first = queue_checker(tmp_path, frame, FakeLLM([judgement()])).check("till", AT)
    spark = FakeLLM([judgement(alert_staff=False)])
    queue_checker(tmp_path, frame, spark, [first]).check("till", AT)
    text = spark.calls[0]["messages"][1]["content"][0]["text"]  # type: ignore[index]
    assert "Earlier readings" in text
    assert "4 waiting" in text


def test_queue_check_needs_video(tmp_path: Path, frame: Path) -> None:
    with pytest.raises(RetailError, match="no recorded frames"):
        queue_checker(tmp_path, frame, FakeLLM()).check("aisle", AT)


def test_queue_headline() -> None:
    j = QueueJudgement.model_validate(judgement(customer_needs_help=True))
    assert queue_headline(j) == (
        "4 waiting, 1 of 2 tills open; about 180 s wait; open another till; a customer needs help"
    )


def hazards(*items: dict[str, Any], alert: bool = True) -> dict[str, Any]:
    return {
        "hazards": list(items),
        "alert_staff": alert,
        "summary": "A puddle in the aisle.",
        "staff_message": "Bring the mop and a wet-floor sign.",
        "confidence": 0.7,
    }


SPILL = {
    "box": [100, 500, 300, 900],
    "kind": "spill",
    "sam_phrase": "puddle of milk",
    "what": "milk on the floor",
    "severity": "high",
}


def hazard_checker(
    tmp_path: Path, frame: Path, spark: FakeLLM, sam: FakeSam, glimmer: FakeLLM | None
) -> HazardChecker:
    return HazardChecker(
        spark=spark,
        files=Files(),
        sam=sam,
        glimmer=glimmer,
        glimmer_model="muse-glimmer-30b",
        frames=FakeFrames(frame),
        site=SITE,
        clock=FakeClock(AT),
        marks_dir=tmp_path / "marks",
    )


def test_glimmer_disagreeing_overrules_sam(tmp_path: Path, frame: Path) -> None:
    sam = FakeSam({"puddle of milk": [(45, 110, 115, 170)]})  # inside the 40-120 x 100-180 box
    glimmer = FakeLLM([{"hazard": False, "why": "looks like a reflection"}])
    finding = hazard_checker(tmp_path, frame, FakeLLM([hazards(SPILL)]), sam, glimmer).check(
        "till", AT
    )
    assert finding.act is False
    check = finding.detail["hazards"][0]  # type: ignore[index]
    assert check["sam_found"] == 1
    assert check["glimmer"]["hazard"] is False


def test_sam_stands_in_when_glimmer_is_off(tmp_path: Path, frame: Path) -> None:
    sam = FakeSam({"puddle of milk": [(45, 110, 115, 170)]})
    finding = hazard_checker(tmp_path, frame, FakeLLM([hazards(SPILL)]), sam, None).check(
        "till", AT
    )
    assert finding.act is True
    assert finding.metric == 1.0
    assert finding.staff_message == "Bring the mop and a wet-floor sign."


def test_hazard_confirmed_by_glimmer_alone(tmp_path: Path, frame: Path) -> None:
    glimmer = FakeLLM([{"hazard": True, "why": "white liquid pooled on the tiles"}])
    finding = hazard_checker(
        tmp_path, frame, FakeLLM([hazards(SPILL)]), FakeSam({}), glimmer
    ).check("till", AT)
    assert finding.act is True
    assert "1 confirmed hazard(s): spill" in finding.headline


def test_unconfirmed_hazard_does_not_send_staff(tmp_path: Path, frame: Path) -> None:
    far = FakeSam({"puddle of milk": [(300, 0, 390, 40)]})
    finding = hazard_checker(tmp_path, frame, FakeLLM([hazards(SPILL)]), far, None).check(
        "till", AT
    )
    assert finding.act is False
    assert finding.staff_message == ""
    assert "not confirmed" in finding.headline


def test_no_hazard(tmp_path: Path, frame: Path) -> None:
    finding = hazard_checker(
        tmp_path, frame, FakeLLM([hazards(alert=False)]), FakeSam({}), None
    ).check_image(frame)
    assert finding.act is False
    assert finding.headline == "No floor hazard seen"
    assert finding.camera_id == "upload"


def test_overlap_share() -> None:
    assert overlap_share((0, 0, 10, 10), (5, 5, 20, 20)) == pytest.approx(0.25)
    assert overlap_share((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert overlap_share((0, 0, 10, 10), (2, 2, 4, 4)) == 1.0


def test_shelf_finding_with_gaps(tmp_path: Path, frame: Path) -> None:
    spark = FakeLLM(
        [
            {
                "gaps": [
                    {
                        "box": [0, 0, 500, 500],
                        "section": "bay 1, shelf 2",
                        "neighbours": "CORN FLAKES",
                        "facings": 3,
                    }
                ]
            }
        ]
    )
    scanner = ShelfScanner(spark=spark, files=Files(), sam=FakeSam({}), out_dir=tmp_path / "shelf")
    finding = ShelfChecker(scanner, SITE, FakeClock(AT)).check_image(frame)
    assert finding.act is True
    assert finding.metric == 1.0
    assert "CORN FLAKES" in finding.staff_message
    assert finding.evidence.marked is not None


def test_shelf_check_on_a_camera_without_video(tmp_path: Path) -> None:
    scanner = ShelfScanner(spark=FakeLLM(), files=Files(), sam=FakeSam({}), out_dir=tmp_path)
    with pytest.raises(RetailError):
        ShelfChecker(scanner, SITE, FakeClock(AT)).check("till", AT)
