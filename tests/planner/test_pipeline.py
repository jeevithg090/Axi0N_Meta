"""Unit tests for the 10-step Site Planner fixed pipeline and persistence (task P2-06a)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import pytest
import yaml
from pydantic import BaseModel

from rulecam.bus import Bus
from rulecam.contracts.core import Zone
from rulecam.contracts.plan import (
    AcceptanceResult,
    FeasibilityVerdict,
    InventoryItem,
    Probe,
    RequestItem,
    SitePlan,
)
from rulecam.contracts.rule_ir import RuleIR
from rulecam.planner.feasibility import (
    CameraFixProposal,
)
from rulecam.planner.models import Concern, ExtractedDocument, UnderstandResult
from rulecam.planner.pipeline import (
    PIPELINE_STEPS,
    PlannerPipeline,
    StepProgress,
    get_step_file_path,
    load_plan_yaml,
    save_plan_yaml,
    save_step_output,
)
from rulecam.planner.steps.ask import ProposedQuestions, Question
from rulecam.planner.steps.survey import ImageSegmenter, ProposedConcepts

M = TypeVar("M", bound=BaseModel)
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _make_dummy_rule(rule_id: str = "wash-before-line") -> RuleIR:
    raw = yaml.safe_load((FIXTURES_DIR / "wash-before-line.rule.yaml").read_text(encoding="utf-8"))
    raw["id"] = rule_id
    return RuleIR.model_validate(raw)


class PipelineFakeSpark:
    """Fake StructuredLLM answering pipeline queries deterministically."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def upload_file(self, path: Path | str, purpose: str = "user_data") -> str:
        return f"file_{Path(path).stem}"

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        self.calls.append({"model": model, "schema": schema})

        if schema is ExtractedDocument:
            return schema(
                setting_guess="restaurant_kitchen",
                concerns=[
                    Concern(
                        text="Cooks must wash hands before food prep",
                        source_ref="kitchen_sop.pdf",
                    )
                ],
                languages=["en"],
            )

        if schema is ProposedConcepts:
            return schema(concepts=["sink", "cutting board", "cook"])

        if schema is CameraFixProposal:
            return schema(fix="Move camera 1 about 0.5 m closer")

        if schema is ProposedQuestions:
            return schema(
                questions=[
                    Question(
                        question_id="q1",
                        text="Which sink is designated for handwashing?",
                        target_field="sink_zone",
                        options=("North sink", "Dishwashing sink"),
                    )
                ]
            )

        if schema is RuleIR:
            return cast(M, _make_dummy_rule("R-TEST-001"))

        raise NotImplementedError(f"Unhandled schema: {schema}")


class PipelineFakeSegmenter(ImageSegmenter):
    """Fake segmenter returning detections for survey and probe steps."""

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "cam0",
        include_confidence: bool = True,
    ) -> list[Any]:
        class FakeMask:
            box_xyxy = (10.0, 10.0, 60.0, 90.0)
            confidence = 0.95

        class FakeDetection:
            mask = FakeMask()
            confidence = 0.95

        return [FakeDetection()]


def test_pipeline_end_to_end(tmp_path: Path) -> None:
    """Test full execution of all 10 steps, persistence, and YAML generation."""
    plans_dir = tmp_path / "plans"
    spark = PipelineFakeSpark()
    segmenter = PipelineFakeSegmenter()
    bus = Bus()
    progress_events: list[StepProgress] = []

    pipeline = PlannerPipeline(
        spark=spark,
        segmenter=segmenter,
        bus=bus,
        plans_dir=plans_dir,
        progress_callback=progress_events.append,
    )

    doc_file = tmp_path / "kitchen_sop.pdf"
    doc_file.write_bytes(b"%PDF-1.4 dummy")

    frame1 = tmp_path / "cam0_f1.jpg"
    frame1.write_bytes(b"\xff\xd8\xff dummy")

    req = RequestItem(kind="pdf", file=str(doc_file))
    zone = Zone(
        zone_id="prep_table",
        camera_id="cam0",
        kind="polygon",
        polygon_px=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
    )

    plan = pipeline.run(
        plan_id="plan_test_01",
        site_name="Chez Test",
        request_items=[req],
        camera_frames={"cam0": [frame1] * 12},
        zones=[zone],
    )

    assert plan.plan_id == "plan_test_01"
    assert plan.site_name == "Chez Test"
    assert len(plan.rules) == 1
    assert plan.rules[0].id == "R-TEST-001"

    # Verify all 10 step files were created on disk
    for step_name in PIPELINE_STEPS:
        step_file = get_step_file_path(plans_dir, "plan_test_01", step_name)
        assert step_file.exists(), f"Missing output file for step: {step_name}"
        data = json.loads(step_file.read_text(encoding="utf-8"))
        assert data is not None

    # Verify YAML file was generated
    yaml_file = plans_dir / "plan_test_01.plan.yaml"
    assert yaml_file.exists()

    # Verify progress events
    started_steps = [e.step_name for e in progress_events if e.status == "started"]
    completed_steps = [e.step_name for e in progress_events if e.status == "completed"]
    assert started_steps == list(PIPELINE_STEPS)
    assert completed_steps == list(PIPELINE_STEPS)


def test_save_and_load_plan_yaml_roundtrip(tmp_path: Path) -> None:
    """Test lossless serialization and validation of SitePlan to/from YAML."""
    rule = _make_dummy_rule("R-001")
    plan = SitePlan(
        plan_id="plan_roundtrip",
        version=1,
        site_name="Roundtrip Bistro",
        setting_inferred="kitchen",
        languages=("en",),
        request=(RequestItem(kind="pdf", file="sop.pdf"),),
        inventory=(InventoryItem(concept="sink", camera_id="cam0", count=1, mean_confidence=0.9),),
        feasibility=(
            FeasibilityVerdict(
                concern="Wash hands",
                verdict="observable",
                says="Clear view",
            ),
        ),
        rules=(rule,),
        privacy_mode="standard",
        retention_days=30,
        protected_two_key=(),
        roles={"supervisor": "web_push"},
        acceptance=AcceptanceResult(checks=(), rounds=0),
        standards=(),
    )

    yaml_path = save_plan_yaml(plan, output_path=tmp_path / "custom_plan.yaml")
    loaded = load_plan_yaml(yaml_path)

    assert loaded.plan_id == plan.plan_id
    assert loaded.site_name == plan.site_name
    assert len(loaded.rules) == 1
    assert loaded.rules[0].id == "R-001"
    assert loaded.model_dump(mode="json") == plan.model_dump(mode="json")


def test_pipeline_resume_from_step(tmp_path: Path) -> None:
    """Test resuming pipeline execution from step 6 (compile), skipping steps 1-5."""
    plans_dir = tmp_path / "plans"
    plan_id = "plan_resume_test"

    # Pre-populate steps 1 to 5 intermediate files
    understand_data = UnderstandResult(
        setting_guess="restaurant",
        concerns=(Concern(text="Wash hands", source_ref="doc.pdf"),),
        languages=("en",),
    )
    save_step_output(plans_dir, plan_id, "understand", understand_data)

    inventory_data = [InventoryItem(concept="sink", camera_id="cam0", count=1, mean_confidence=0.9)]
    save_step_output(plans_dir, plan_id, "survey", inventory_data)

    probe_data = [
        Probe(
            concept="Wash hands",
            camera_id="cam0",
            n_frames=12,
            detect_rate=0.833,
            detect_lb=0.552,
            median_px_height=50.0,
        )
    ]
    save_step_output(plans_dir, plan_id, "probe", probe_data)

    gauge_data = [
        FeasibilityVerdict(
            concern="Wash hands",
            verdict="observable",
            says="Good view",
        )
    ]
    save_step_output(plans_dir, plan_id, "gauge", gauge_data)

    ask_data = [Question(question_id="q1", text="Confirm sink location?", target_field="sink")]
    save_step_output(plans_dir, plan_id, "ask", ask_data)

    # Now create pipeline and resume from 'compile'
    spark = PipelineFakeSpark()
    progress_events: list[StepProgress] = []

    pipeline = PlannerPipeline(
        spark=spark,
        plans_dir=plans_dir,
        progress_callback=progress_events.append,
    )

    req = RequestItem(kind="pdf", file="doc.pdf")
    plan = pipeline.run(
        plan_id=plan_id,
        site_name="Resume Cafe",
        request_items=[req],
        resume_from_step="compile",
    )

    assert plan.plan_id == plan_id
    assert len(plan.rules) == 1

    # Verify steps 1-5 were skipped
    skipped = [e.step_name for e in progress_events if e.status == "skipped"]
    assert skipped == ["understand", "survey", "probe", "gauge", "ask"]

    # Verify steps 6-10 started & completed
    completed = [e.step_name for e in progress_events if e.status == "completed"]
    assert completed == ["compile", "lint", "accept", "audit", "prove"]


def test_pipeline_resume_missing_file_raises(tmp_path: Path) -> None:
    """Test that resuming when a prior step file is missing raises FileNotFoundError."""
    plans_dir = tmp_path / "plans"
    pipeline = PlannerPipeline(
        spark=PipelineFakeSpark(),
        plans_dir=plans_dir,
    )

    req = RequestItem(kind="pdf", file="doc.pdf")
    with pytest.raises(FileNotFoundError, match="missing persisted understand step output"):
        pipeline.run(
            plan_id="plan_incomplete",
            site_name="Incomplete Cafe",
            request_items=[req],
            resume_from_step="compile",
        )


def test_pipeline_invalid_resume_step_raises(tmp_path: Path) -> None:
    """Test that providing an invalid step name to resume_from_step raises ValueError."""
    pipeline = PlannerPipeline(
        spark=PipelineFakeSpark(),
        plans_dir=tmp_path / "plans",
    )
    req = RequestItem(kind="pdf", file="doc.pdf")
    with pytest.raises(ValueError, match="Unknown step 'nonexistent_step'"):
        pipeline.run(
            plan_id="plan_invalid",
            site_name="Invalid Cafe",
            request_items=[req],
            resume_from_step="nonexistent_step",
        )
