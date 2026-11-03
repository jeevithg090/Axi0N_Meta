"""Crop-zoom at probe time (add-on 3.3): worn concepts are looked for on enlarged person crops."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

from rulecam.contracts.core import BoxXYXY, Detection, Mask
from rulecam.perception.crop_zoom import CropHit
from rulecam.planner.models import Concern
from rulecam.planner.steps.probe import (
    ConceptNeeds,
    ProbeZoom,
    concept_needs,
    probe_concept_on_camera,
    probe_concepts,
)
from tests.fakes import EPOCH, FakeLLM

PERSON: BoxXYXY = (100.0, 40.0, 160.0, 240.0)


def jpeg_frames(tmp_path: Path, n: int) -> list[Path]:
    out = []
    for i in range(n):
        path = tmp_path / f"cam1_{i:03d}.jpg"
        cv2.imwrite(str(path), np.full((300, 400, 3), 80, np.uint8))
        out.append(path)
    return out


class Sam:
    """Fake SAM on images: people everywhere, the hair net never in the full frame."""

    def __init__(self, people: int = 1) -> None:
        self.people = people
        self.calls: list[tuple[str, str]] = []

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        self.calls.append((frame.name, concept))
        if concept != "person":
            return []
        return [
            Detection(
                camera_id=camera_id,
                frame_idx=0,
                ts=EPOCH,
                concept=concept,
                object_id=i,
                mask=Mask(rle="", box_xyxy=PERSON, confidence=0.9),
            )
            for i in range(self.people)
        ]


class Crops:
    """Fake crop-zoom: a 12 px tall hit on the first crop, every other frame."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[list[BoxXYXY], list[str]]] = []
        self.fail = fail

    def segment_crops(
        self, image: bytes, crops: Sequence[BoxXYXY], concepts: Sequence[str], *, camera_id: str
    ) -> list[CropHit]:
        self.calls.append((list(crops), list(concepts)))
        if self.fail:
            raise RuntimeError("pod down")
        if len(self.calls) % 2 == 0:
            return []
        return [CropHit(crop_idx=0, concept=concepts[0], box_xyxy=(120.0, 30.0, 140.0, 42.0))]


def test_concept_needs_keeps_worn_concepts_spark_named_among_the_needed_ones() -> None:
    spark = FakeLLM(
        [
            ConceptNeeds.model_validate(
                {
                    "needs": [{"concern": "Wear hair nets", "concepts": ["Hair Net", "person"]}],
                    "worn": [
                        {"concept": "hair net", "region": "head"},
                        {"concept": "apron", "region": "body"},  # not needed: dropped
                    ],
                }
            )
        ]
    )
    plan = concept_needs(
        [Concern(text="Wear hair nets", source_ref="text")], ["person"], spark=spark
    )
    assert plan.needs == {"Wear hair nets": ("hair net", "person")}
    assert plan.worn == {"hair net": "head"}
    system = str(spark.calls[0]["messages"][0]["content"])  # type: ignore[index]
    assert "`worn`" in system
    assert "enlarged crops" in system


def test_a_worn_concept_missed_in_the_full_frame_is_found_on_head_crops(tmp_path: Path) -> None:
    sam, crops = Sam(people=2), Crops()
    zoom = ProbeZoom(sam, crops)
    probe = probe_concept_on_camera(
        "hair net",
        "cam1",
        jpeg_frames(tmp_path, 4),
        segmenter=sam,
        seed=1,
        zoom=zoom,
        region="head",
    )
    assert probe.n_frames == 4
    assert probe.detect_rate == 0.5  # crop hits on every other frame
    assert probe.median_px_height == 12.0  # the crop hit's height in frame pixels
    boxes, concepts = crops.calls[0]
    assert concepts == ["hair net"]
    assert len(boxes) == 2  # one head crop per person
    assert boxes[0][1] < PERSON[1] < boxes[0][3]
    assert [c for _, c in sam.calls].count("person") == 4  # people found once per frame


def test_zoom_is_only_for_worn_concepts_and_a_failure_is_a_miss(tmp_path: Path) -> None:
    sam, crops = Sam(), Crops(fail=True)
    frames = {"cam1": jpeg_frames(tmp_path, 3)}
    probes = probe_concepts(
        ["hair net", "sink"],
        frames,
        segmenter=sam,
        seed=1,
        zoom=ProbeZoom(sam, crops),
        worn={"hair net": "head"},
    )
    assert [p.detect_rate for p in probes] == [0.0, 0.0]
    assert len(crops.calls) == 3  # only the worn concept, once per frame
    assert all(concepts == ["hair net"] for _, concepts in crops.calls)


def test_no_people_or_an_unreadable_frame_means_no_crop_call(tmp_path: Path) -> None:
    crops = Crops()
    (frame,) = jpeg_frames(tmp_path, 1)
    assert ProbeZoom(Sam(people=0), crops).find(frame, "glove", "hands", camera_id="cam1") == []
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff")
    assert ProbeZoom(Sam(), crops).find(broken, "glove", "hands", camera_id="cam1") == []
    assert crops.calls == []
