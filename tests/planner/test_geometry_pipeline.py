"""End to end with fakes: a warehouse camera, a pallet on the floor, a forklift rule (P2-06).

The survey sees the pallet and calibrates the camera from it; the compiled forklift rule is
grounded on that calibration, passes every acceptance check and Rule CI, and approving the plan
gives the runtime the rule and the calibration. Without the pallet the same rule
stays amber with a question asking for a known distance.
"""

from __future__ import annotations

from pathlib import Path

from rulecam.bus import Bus
from rulecam.contracts.plan import RequestItem, SitePlan
from rulecam.contracts.rule_ir import (
    ActionSpec,
    All,
    ConceptSpec,
    DistanceM,
    Moving,
    RuleIR,
    RuleSource,
)
from rulecam.planner.guardrails import _ModelClassification
from rulecam.planner.pipeline import (
    AcceptResult,
    PipelineInput,
    PlannerPipeline,
    PlanStore,
    ProbeResult,
    SurveyResult,
)
from rulecam.planner.steps.ask import ProposedQuestions
from rulecam.planner.steps.compile import rule_to_draft
from rulecam.planner.steps.probe import ConceptNeeds
from rulecam.planner.steps.survey import ProposedConcepts
from rulecam.rule_ci import prove_plan_hook
from rulecam.runtime.activate import active_plan_from
from tests.planner.geometry_fakes import SceneSam, write_frames
from tests.planner.test_geometry import FORKLIFT
from tests.planner.test_pipeline import FakeAttacher, SchemaLLM

WASH = "wash hands at the sink"


def forklift_proximity_rule() -> RuleIR:
    """UC4: a person within 2 m of a moving forklift, with a calibration name the model made up."""
    return RuleIR(
        id="forklift-proximity",
        source=RuleSource(kind="plan", plan_id="dock-plan"),
        concepts={
            "person": ConceptSpec(prompt="person"),
            "forklift": ConceptSpec(prompt="forklift"),
        },
        calibration="cam-everything@made-up",
        predicate=All(
            of=(
                DistanceM(a="person", b="forklift", lt=2.0),
                Moving(object="forklift", speed_mps_gt=0.2),
            )
        ),
        severity="high",
        actions=(ActionSpec(kind="interlock_cut"), ActionSpec(kind="notify", role="dock_lead")),
    )


def _spark() -> SchemaLLM:
    needs = ConceptNeeds.model_validate(
        {"needs": [{"concern": FORKLIFT, "concepts": ["person", "forklift"]}]}
    )
    return SchemaLLM(
        {
            "ProposedConcepts": ProposedConcepts(concepts=["forklift", "person", "wooden pallet"]),
            "ConceptNeeds": needs,
            "_ModelClassification": _ModelClassification(verdict="allowed"),
            "ProposedQuestions": ProposedQuestions(questions=[]),
            "RuleDraft": rule_to_draft(forklift_proximity_rule()),
        }
    )


def _run(tmp_path: Path, *, pallet: bool) -> tuple[SitePlan, PlanStore]:
    store = PlanStore(tmp_path / "plans")
    pipeline = PlannerPipeline(
        spark=_spark(),
        files=FakeAttacher(),
        segmenter=SceneSam(pallet=pallet),
        store=store,
        prove=prove_plan_hook(Bus()),
    )
    inputs = PipelineInput(
        plan_id="dock-plan",
        site_name="Dock 3",
        request=(RequestItem(kind="text", text=FORKLIFT),),
        camera_frames={"dock": write_frames(tmp_path, "dock")},
    )
    return pipeline.run(inputs), store


def test_a_pallet_on_the_floor_makes_the_forklift_rule_activatable(tmp_path: Path) -> None:
    plan, store = _run(tmp_path, pallet=True)

    survey = store.load_step("dock-plan", "survey", SurveyResult)
    assert survey is not None
    assert survey.calibrations["dock"].reference == "pallet_48x40in"
    (rule,) = plan.rules
    assert rule.calibration == "ground-plane@dock"
    assert plan.acceptance.all_passed, [c for c in plan.acceptance.checks if not c.passed]
    assert {c.check for c in plan.acceptance.checks} >= {"calibration_err<=tol", "ci_green"}

    calibrations = store.calibrations("dock-plan")
    activation = active_plan_from(plan, calibrations=calibrations)
    assert [g.status for g in activation.gates] == ["pass"]
    active = activation.active_plan
    assert active.calibration == survey.calibrations["dock"]
    assert [r.id for r in active.rules] == ["forklift-proximity"]


def test_without_a_reference_the_rule_is_amber_and_asks_for_a_known_distance(
    tmp_path: Path,
) -> None:
    plan, store = _run(tmp_path, pallet=False)

    assert store.calibrations("dock-plan") == {}
    (rule,) = plan.rules
    assert rule.calibration is None
    failed = [c.check for c in plan.acceptance.checks if not c.passed]
    assert failed == ["calibration_err<=tol"]  # Rule CI uses its own synthetic calibration
    assert [v.verdict for v in plan.feasibility] == ["caveat"]
    accepted = store.load_step("dock-plan", "accept", AcceptResult)
    assert accepted is not None
    assert [(q.target_field, q.camera_id) for q in accepted.questions] == [("calibration", "dock")]
    activation = active_plan_from(plan, calibrations=store.calibrations("dock-plan"))
    assert [g.status for g in activation.gates] == ["amber"]
    assert activation.active_plan.calibration is None


def test_owner_calibration_wins_and_a_needed_sink_becomes_a_zone(tmp_path: Path) -> None:
    plan, store = _run(tmp_path, pallet=True)
    owner = store.calibrations("dock-plan")["dock"].model_copy(
        update={"method": "clicks4", "reference": None}
    )
    saved = store.load_input("dock-plan")
    store.save_input(saved.model_copy(update={"calibrations": {"dock": owner}}))
    assert store.calibrations("dock-plan")["dock"].method == "clicks4"
    assert store.calibrations("missing-plan") == {}
    assert plan.rules

    spark = _spark()
    spark._answers["ConceptNeeds"] = ConceptNeeds.model_validate(
        {"needs": [{"concern": WASH, "concepts": ["sink", "person"]}]}
    )
    pipeline = PlannerPipeline(
        spark=spark, files=FakeAttacher(), segmenter=SceneSam(), store=PlanStore(tmp_path / "p2")
    )
    inputs = PipelineInput(
        plan_id="kitchen",
        site_name="Kitchen",
        request=(RequestItem(kind="text", text=WASH),),
        camera_frames={"dock": write_frames(tmp_path, "dock")},
    )
    pipeline.run(inputs)
    probe = PlanStore(tmp_path / "p2").load_step("kitchen", "probe", ProbeResult)
    assert probe is not None
    assert [z.zone_id for z in probe.zones] == ["sink"]
