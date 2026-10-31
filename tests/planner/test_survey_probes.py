"""Unit tests for camera survey, detectability probes, and feasibility gauge (task P2-04)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel

from rulecam.contracts.core import Detection, Mask
from rulecam.contracts.plan import Probe
from rulecam.planner.feasibility import (
    evaluate_concern_feasibility,
    gauge_plan_feasibility,
)
from rulecam.planner.steps.probe import (
    probe_concept_on_camera,
    probe_concepts,
    sample_probe_frames,
)
from rulecam.planner.steps.survey import (
    ImageSegmenter,
    ProposedConcepts,
    sample_survey_frames,
    survey_cameras,
)

M = TypeVar("M", bound=BaseModel)


class FakeSparkLLM:
    """Fake StructuredLLM returning predetermined responses."""

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


class ScriptedImageSegmenter(ImageSegmenter):
    """Scripted SAM 3.1 segmenter returning configured detections per concept."""

    def __init__(self, mapping: dict[str, list[Detection]] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[dict[str, object]] = []

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "cam0",
        include_confidence: bool = True,
    ) -> list[Detection]:
        self.calls.append({"frame": frame, "concept": concept, "camera_id": camera_id})
        return self.mapping.get(concept, [])


def make_detection(
    concept: str,
    camera_id: str = "cam0",
    confidence: float = 0.95,
    height: float = 120.0,
) -> Detection:
    return Detection(
        camera_id=camera_id,
        frame_idx=0,
        ts=datetime.now(UTC),
        concept=concept,
        object_id=1,
        mask=Mask(
            rle="dummy_rle",
            box_xyxy=(10.0, 10.0, 50.0, 10.0 + height),
            confidence=confidence,
        ),
    )


def test_frame_sampling_is_deterministic_for_a_given_seed(tmp_path: Path) -> None:
    """Verify sample_survey_frames and probe frames produce identical output with same seed."""
    frames = [tmp_path / f"frame_{i:03d}.jpg" for i in range(30)]
    for f in frames:
        f.touch()

    s1 = sample_survey_frames(frames, n_samples=8, seed=123)
    s2 = sample_survey_frames(frames, n_samples=8, seed=123)
    s3 = sample_survey_frames(frames, n_samples=8, seed=999)

    assert s1 == s2
    assert len(s1) == 8
    assert s1 != s3

    p1 = sample_probe_frames(frames, n_samples=12, seed=42)
    p2 = sample_probe_frames(frames, n_samples=12, seed=42)
    assert p1 == p2
    assert len(p1) == 12


def test_survey_cameras_builds_inventory(tmp_path: Path) -> None:
    """Survey samples 8 frames per camera, queries Spark for concepts, and segments with SAM."""
    cam1_frames = [tmp_path / f"c1_{i}.jpg" for i in range(10)]
    cam2_frames = [tmp_path / f"c2_{i}.jpg" for i in range(10)]
    for f in cam1_frames + cam2_frames:
        f.touch()

    spark_reply = ProposedConcepts(concepts=["sink", "gloves", "thermometer"])
    spark = FakeSparkLLM(spark_reply)

    segmenter = ScriptedImageSegmenter(
        {
            "sink": [make_detection("sink", camera_id="cam1", confidence=0.92)],
            "gloves": [make_detection("gloves", camera_id="cam1", confidence=0.85)],
        }
    )

    camera_frames = {"cam1": cam1_frames, "cam2": cam2_frames}
    inventory = survey_cameras(camera_frames, spark=spark, segmenter=segmenter, seed=42)

    assert len(inventory) >= 2
    concepts_found = {item.concept for item in inventory}
    assert "sink" in concepts_found
    assert "gloves" in concepts_found
    assert "thermometer" not in concepts_found  # count 0 excluded


def test_probe_concept_computes_detect_rate_wilson_bound_and_pixel_height(
    tmp_path: Path,
) -> None:
    """Probe concept runs on 12 frames, computes detect_rate, Wilson lb, and median pixel height."""
    frames = [tmp_path / f"f_{i:02d}.jpg" for i in range(12)]
    for f in frames:
        f.touch()

    detection = make_detection("sink", height=150.0)
    mapping = {"sink": [detection]}
    segmenter = ScriptedImageSegmenter(mapping)

    probe = probe_concept_on_camera(
        concept="sink",
        camera_id="cam1",
        frames=frames,
        segmenter=segmenter,
        n_samples=12,
        seed=42,
    )

    assert probe.concept == "sink"
    assert probe.camera_id == "cam1"
    assert probe.n_frames == 12
    assert probe.detect_rate == 1.0
    assert probe.detect_lb > 0.70
    assert probe.median_px_height == 150.0


def test_probe_concepts_multiple(tmp_path: Path) -> None:
    """probe_concepts probes multiple concepts across cameras."""
    frames = [tmp_path / f"f_{i:02d}.jpg" for i in range(12)]
    for f in frames:
        f.touch()

    segmenter = ScriptedImageSegmenter({"sink": [make_detection("sink")]})
    probes = probe_concepts(["sink", "gloves"], {"cam1": frames}, segmenter=segmenter)
    assert len(probes) == 2
    p_map = {p.concept: p for p in probes}
    assert p_map["sink"].detect_rate == 1.0
    assert p_map["gloves"].detect_rate == 0.0


def test_feasibility_threshold_boundaries() -> None:
    """Verify threshold boundaries: 0.79 -> caveat, 0.80 -> observable."""
    concern = "Handwash compliance"
    probe_79 = Probe(
        concept="sink",
        camera_id="cam1",
        n_frames=12,
        detect_rate=0.85,
        detect_lb=0.79,
        median_px_height=45.0,
    )
    v79 = evaluate_concern_feasibility(concern, ["sink"], [probe_79])
    assert v79.verdict == "caveat"
    assert v79.alternative is not None
    assert "0.79" in v79.says or "caveat" in v79.says

    probe_80 = Probe(
        concept="sink",
        camera_id="cam1",
        n_frames=12,
        detect_rate=0.92,
        detect_lb=0.80,
        median_px_height=80.0,
    )
    v80 = evaluate_concern_feasibility(concern, ["sink"], [probe_80])
    assert v80.verdict == "observable"
    assert v80.alternative is None


def test_feasibility_not_observable_includes_alternative() -> None:
    """Detect_lb below 0.5 or missing object yields not_observable with alternative."""
    concern = "Cold holding temperature monitoring"
    v_absent = evaluate_concern_feasibility(concern, ["thermometer"], [])
    assert v_absent.verdict == "not_observable"
    assert v_absent.alternative is not None
    assert "thermometer" in v_absent.alternative or "sensor" in v_absent.alternative
    assert "I can't see a thermometer" in v_absent.says

    probe_low = Probe(
        concept="thermometer",
        camera_id="cam1",
        n_frames=12,
        detect_rate=0.33,
        detect_lb=0.22,
        median_px_height=15.0,
    )
    v_low = evaluate_concern_feasibility(concern, ["thermometer"], [probe_low])
    assert v_low.verdict == "not_observable"
    assert v_low.alternative is not None


def test_acceptance_criteria_uc1_kitchen_feasibility_verdicts() -> None:
    """Acceptance criteria: UC1 kitchen gives handwash observable, gloves caveat,

    cold holding not observable, and sick-cook request declined.
    """
    sink_probe = Probe(
        concept="sink",
        camera_id="cam1",
        n_frames=12,
        detect_rate=1.0,
        detect_lb=0.85,
        median_px_height=160.0,
    )
    gloves_probe = Probe(
        concept="gloves",
        camera_id="cam1",
        n_frames=12,
        detect_rate=0.75,
        detect_lb=0.62,
        median_px_height=22.0,
    )
    thermometer_probe = Probe(
        concept="thermometer",
        camera_id="cam1",
        n_frames=12,
        detect_rate=0.08,
        detect_lb=0.01,
        median_px_height=8.0,
    )

    all_probes = [sink_probe, gloves_probe, thermometer_probe]

    concern_requirements = {
        "Handwashing compliance before entering line": ["sink"],
        "Gloves required when handling ready-to-eat food": ["gloves"],
        "Cold-holding temperature under 41F": ["thermometer"],
        "Detect if cook looks sick or has a fever": ["person"],
    }

    verdicts = gauge_plan_feasibility(concern_requirements, all_probes)
    v_map = {v.concern: v for v in verdicts}

    # 1. Handwash -> observable
    assert v_map["Handwashing compliance before entering line"].verdict == "observable"
    assert v_map["Handwashing compliance before entering line"].alternative is None

    # 2. Gloves -> caveat with a camera fix
    v_gloves = v_map["Gloves required when handling ready-to-eat food"]
    assert v_gloves.verdict == "caveat"
    assert v_gloves.alternative is not None
    assert "closer" in v_gloves.alternative.lower() or "zoom" in v_gloves.alternative.lower()

    # 3. Cold holding -> not_observable with an alternative
    v_cold = v_map["Cold-holding temperature under 41F"]
    assert v_cold.verdict == "not_observable"
    assert v_cold.alternative is not None
    assert "sensor" in v_cold.alternative.lower() or "phone" in v_cold.alternative.lower()

    # 4. Sick cook -> declined under ethical guardrails
    v_sick = v_map["Detect if cook looks sick or has a fever"]
    assert v_sick.verdict == "declined"
    assert v_sick.reason is not None
