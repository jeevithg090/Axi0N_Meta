"""The fixed ten-step pipeline, its persistence and resume (task P2-06a). No network."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeVar

import cv2
import numpy as np
import pytest
import yaml
from pydantic import BaseModel

from rulecam.bus import Bus, Message, Topic
from rulecam.contracts import Detection, Mask
from rulecam.contracts.core import Calibration, PrivacyMode, Zone
from rulecam.contracts.plan import (
    AcceptanceCheck,
    AcceptanceResult,
    AuditFinding,
    FeasibilityVerdict,
    InventoryItem,
    Probe,
    RequestItem,
    SitePlan,
)
from rulecam.contracts.rule_ir import RuleIR
from rulecam.planner.errors import PipelineError
from rulecam.planner.guardrails import GuardrailCategory, _ModelClassification
from rulecam.planner.models import Concern
from rulecam.planner.pipeline import (
    PIPELINE_STEPS,
    AcceptResult,
    AskResult,
    CompileResult,
    LintResult,
    PipelineInput,
    PlannerPipeline,
    PlannerStepEvent,
    PlanStore,
    camera_views,
    plan_from_yaml,
    plan_to_yaml,
)
from rulecam.planner.standards import (
    Citation,
    GroundedCitation,
    StandardsLibrary,
    html_to_text,
)
from rulecam.planner.steps.ask import ProposedQuestions, Question
from rulecam.planner.steps.audit import audit_hook, is_audit_skipped
from rulecam.planner.steps.back_translate import BackTranslationAnswer, RuleSummary
from rulecam.planner.steps.compile import rule_to_draft
from rulecam.planner.steps.probe import (
    MAX_CONCEPTS_PER_CONCERN,
    ConceptNeeds,
    concepts_for_concerns,
)
from rulecam.planner.steps.survey import ProposedConcepts
from rulecam.replay.cache import ReplayCache
from rulecam.rule_ci import prove_plan_hook
from tests.fakes import EPOCH, FakeClock, FakeLLM

M = TypeVar("M", bound=BaseModel)
RULES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
HANDWASH = "wash hands before the line"
COLD = "keep the fridge at 41F"
SICK = "keep sick cooks off the line"
CALIBRATION = Calibration(
    camera_id="cam1",
    homography=((0.01, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 1.0)),
    method="clicks4",
    err_m=0.04,
)


def _rule(rule_id: str = "wash-before-line") -> RuleIR:
    raw = yaml.safe_load((RULES_DIR / "wash-before-line.rule.yaml").read_text(encoding="utf-8"))
    return RuleIR.model_validate({**raw, "id": rule_id})


class SchemaLLM:
    """A `StructuredLLM` fake answering by schema; a callable answer sees the user message."""

    def __init__(self, answers: dict[str, BaseModel | Callable[[str], BaseModel]]) -> None:
        self._answers = answers
        self.calls: list[str] = []

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: Literal["low", "medium", "high"] = "low",
        background: bool = False,
    ) -> M:
        self.calls.append(schema.__name__)
        answer = self._answers.get(schema.__name__)
        if answer is None:
            msg = f"unexpected {schema.__name__} call"
            raise AssertionError(msg)
        user = str(messages[-1]["content"])
        data = answer(user) if callable(answer) else answer
        return schema.model_validate(data.model_dump())


class FakeAttacher:
    """Stands in for `SparkClient.attach`."""

    def attach(self, path: Path) -> dict[str, object]:
        return {"type": "input_image", "file_id": f"file-{path.stem}"}


class SeesSinkAndHands:
    """Fake SAM: finds sinks, hands and people in every frame, nothing else."""

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        if concept not in {"sink", "hands", "person"}:
            return []
        box = (0.0, 0.0, 50.0, 120.0)
        mask = Mask(rle="", box_xyxy=box, confidence=0.9)
        return [
            Detection(
                camera_id=camera_id, frame_idx=0, ts=EPOCH, concept=concept, object_id=1, mask=mask
            )
        ]


def _needs() -> ConceptNeeds:
    return ConceptNeeds.model_validate(
        {
            "needs": [
                {"concern": HANDWASH, "concepts": ["Sink", "hands"]},
                {"concern": COLD, "concepts": ["thermometer"]},
                {"concern": SICK, "concepts": ["person"]},
            ]
        }
    )


def _spark(*, compile_declines: bool = False) -> SchemaLLM:
    def classify(user: str) -> BaseModel:
        if SICK in user:  # the deterministic pass may not catch this phrasing; the model does
            return _ModelClassification(
                verdict="declined",
                category=GuardrailCategory.CLINICAL_OR_EMOTION,
                reason="illness is clinical inference",
                alternative="a shift-start reminder of the written illness policy",
            )
        return _ModelClassification(verdict="allowed")

    handwash_checks: list[str] = []

    def classify_compile(user: str) -> BaseModel:
        if HANDWASH in user:
            handwash_checks.append(user)
        if compile_declines and len(handwash_checks) > 1:  # the gauge allowed it; compile not
            return _ModelClassification(
                verdict="declined",
                category=GuardrailCategory.PER_PERSON_PRODUCTIVITY,
                reason="changed its mind",
                alternative="station-level checks",
            )
        return classify(user)

    return SchemaLLM(
        {
            "ProposedConcepts": ProposedConcepts(concepts=["sink", "hands", "prep counter"]),
            "ConceptNeeds": _needs(),
            "_ModelClassification": classify_compile,
            "ProposedQuestions": ProposedQuestions(
                questions=[
                    Question(question_id="q1", text="Who gets alerts?", target_field="roles"),
                    Question(question_id="q2", text="Opening hours?", target_field="schedule"),
                ]
            ),
            "RuleDraft": rule_to_draft(_rule()),
        }
    )


QUOTE = "Food employees shall clean their hands and exposed portions of their arms."
PAGE_URL = "https://www.fda.gov/food-code/2-301-12"


class FakeGrounding:
    """Spark with web search: cites the Food Code page for every concern."""

    def __init__(self) -> None:
        self.calls = 0

    def structured(self, *, schema: type[M], **_: object) -> M:
        self.calls += 1
        citation = Citation(ref="Food Code §2-301.12", url=PAGE_URL, quoted_text=QUOTE)
        return schema.model_validate(GroundedCitation(citation=citation).model_dump())


def _standards(
    tmp_path: Path, page: str = f"<p>2-301.12 Cleaning Procedure. {QUOTE}</p>"
) -> StandardsLibrary:
    cache = ReplayCache(tmp_path / "standards_cache", "record", clock=FakeClock())
    return StandardsLibrary(cache, get=lambda url: html_to_text(page))


def _write_frames(tmp_path: Path) -> tuple[Path, ...]:
    frames = tuple(tmp_path / f"cam1_{i:02d}.jpg" for i in range(30))
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    for frame in frames:
        if not frame.is_file():
            cv2.imwrite(str(frame), image)
    return frames


def _inputs(tmp_path: Path, plan_id: str = "main-st") -> PipelineInput:
    frames = _write_frames(tmp_path)
    zone = Zone(
        zone_id="line_entry",
        camera_id="cam1",
        kind="polygon",
        polygon_px=((0.0, 0.0), (400.0, 0.0), (400.0, 300.0), (0.0, 300.0)),
    )
    return PipelineInput(
        plan_id=plan_id,
        site_name="Main St kitchen",
        request=(
            RequestItem(kind="text", text=HANDWASH, lang="en"),
            RequestItem(kind="text", text=COLD),
            RequestItem(kind="text", text=SICK),
        ),
        camera_frames={"cam1": frames},
        zones=(zone,),
        roles={"shift_lead": "web_push"},
        privacy_mode=PrivacyMode.ANONYMOUS,
        calibrations={"cam1": CALIBRATION},
        seed=7,
    )


class Recorder:
    """Collects what the pipeline publishes on the bus."""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def publish(self, topic: Topic, payload: object) -> None:
        self.messages.append(Message(topic=topic, payload=payload))

    def steps(self) -> list[tuple[str, str]]:
        return [
            (m.payload.step, m.payload.status)
            for m in self.messages
            if isinstance(m.payload, PlannerStepEvent)
        ]

    def plans(self) -> list[SitePlan]:
        return [m.payload for m in self.messages if isinstance(m.payload, SitePlan)]


def _pipeline(
    tmp_path: Path, spark: SchemaLLM, bus: Recorder | None = None, **options: object
) -> PlannerPipeline:
    options = {"grounding": FakeGrounding(), "standards": _standards(tmp_path), **options}
    return PlannerPipeline(
        spark=spark,
        files=FakeAttacher(),
        segmenter=SeesSinkAndHands(),
        store=PlanStore(tmp_path / "plans"),
        bus=bus,  # type: ignore[arg-type]
        **options,  # type: ignore[arg-type]
    )


def test_end_to_end_with_fakes_produces_a_valid_saved_and_published_plan(tmp_path: Path) -> None:
    bus = Recorder()
    pipeline = _pipeline(tmp_path, _spark(), bus)

    plan = pipeline.run(_inputs(tmp_path))

    assert [r.id for r in plan.rules] == ["wash-before-line"]
    verdicts = {f.concern: f.verdict for f in plan.feasibility}
    assert verdicts == {HANDWASH: "observable", COLD: "not_observable", SICK: "declined"}
    assert plan.version == 1
    assert plan.privacy_mode is PrivacyMode.ANONYMOUS
    assert plan.roles == {"shift_lead": "web_push"}
    assert plan.languages == ("en",)
    acceptance = plan.acceptance
    assert acceptance.rounds == 0
    assert acceptance.all_passed
    assert [c.check for c in acceptance.checks] == [
        "concern_covered",
        "zone_in_view",
        "detect_lb>=0.8",
        "calibration_err<=tol",
        "privacy_consistent",
        "citation_text_match",
        "no_private_spaces",
    ]
    assert acceptance.checks[0].item == HANDWASH
    assert {c.item for c in acceptance.checks[1:]} == {"wash-before-line"}
    assert {i.concept for i in plan.inventory} == {"sink", "hands"}
    store = pipeline.store
    for step in PIPELINE_STEPS:
        assert store.step_path("main-st", step).is_file(), step
    assert store.step_path("main-st", "survey").name == "02_survey.json"
    assert store.load_plan("main-st") == plan
    asked = store.load_step("main-st", "ask", AskResult)
    assert asked is not None
    assert [q.target_field for q in asked.questions] == ["schedule"]  # roles were known
    assert bus.steps() == [(s, status) for s in PIPELINE_STEPS for status in ("running", "done")]
    assert bus.plans() == [plan]
    assert bus.messages[-1].topic is Topic.PLAN_UPDATED


def test_step_events_reach_the_bus_as_the_ui_frame_shape(tmp_path: Path) -> None:
    async def scenario() -> dict[str, object]:
        bus = Bus()
        received: list[Message] = []

        async def listen() -> None:
            async for message in bus.subscribe(Topic.PLANNER_STEP):
                received.append(message)
                return

        task = asyncio.create_task(listen())
        await asyncio.sleep(0)
        _pipeline(tmp_path, _spark(), bus).run(_inputs(tmp_path))  # type: ignore[arg-type]
        await task
        payload = received[0].payload
        assert isinstance(payload, PlannerStepEvent)
        return payload.model_dump(mode="json")

    frame = asyncio.run(scenario())
    assert frame == {"plan_id": "main-st", "step": "understand", "status": "running", "detail": ""}


def test_not_observable_concerns_are_not_compiled(tmp_path: Path) -> None:
    spark = _spark()
    _pipeline(tmp_path, spark).run(_inputs(tmp_path))
    assert spark.calls.count("RuleDraft") == 1


def test_a_compile_time_decline_becomes_a_declined_verdict(tmp_path: Path) -> None:
    plan = _pipeline(tmp_path, _spark(compile_declines=True)).run(_inputs(tmp_path))
    assert plan.rules == ()
    handwash = next(f for f in plan.feasibility if f.concern == HANDWASH)
    assert handwash.verdict == "declined"
    assert handwash.alternative == "station-level checks"


def test_resume_from_step_6_skips_steps_1_to_5(tmp_path: Path) -> None:
    _pipeline(tmp_path, _spark()).run(_inputs(tmp_path))
    bus = Recorder()
    spark = _spark()
    plan = _pipeline(tmp_path, spark, bus).resume("main-st", "compile")

    assert set(spark.calls) == {"_ModelClassification", "RuleDraft"}  # compile only
    assert bus.steps()[:5] == [(s, "done") for s in PIPELINE_STEPS[:5]]
    assert bus.steps()[5:] == [(s, st) for s in PIPELINE_STEPS[5:] for st in ("running", "done")]
    assert plan.version == 2  # a re-run is a new version, never an overwrite
    assert [r.id for r in plan.rules] == ["wash-before-line"]


def test_resume_without_a_step_continues_after_a_crash(tmp_path: Path) -> None:
    bus = Recorder()
    broken = _spark()
    broken._answers.pop("RuleDraft")
    with pytest.raises(AssertionError, match="unexpected RuleDraft"):
        _pipeline(tmp_path, broken, bus).run(_inputs(tmp_path))
    assert bus.steps()[-1] == ("compile", "failed")
    detail = bus.messages[-1].payload
    assert isinstance(detail, PlannerStepEvent)
    assert "unexpected RuleDraft" in detail.detail
    store = PlanStore(tmp_path / "plans")
    assert store.first_missing_step("main-st") == "compile"
    assert store.load_plan("main-st") is None

    spark = _spark()
    plan = _pipeline(tmp_path, spark).resume("main-st")
    assert "ConceptNeeds" not in spark.calls
    assert plan.version == 1


def test_running_a_step_deletes_stale_later_outputs(tmp_path: Path) -> None:
    _pipeline(tmp_path, _spark()).run(_inputs(tmp_path))
    store = PlanStore(tmp_path / "plans")
    broken = _spark()
    broken._answers.pop("ProposedQuestions")
    with pytest.raises(AssertionError):
        _pipeline(tmp_path, broken).resume("main-st", "gauge")
    assert store.load_step("main-st", "compile", CompileResult) is None
    assert store.first_missing_step("main-st") == "ask"


def test_resume_needs_the_earlier_outputs(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, _spark())
    with pytest.raises(PipelineError, match="no saved run input"):
        pipeline.resume("never-run")
    pipeline.store.save_input(_inputs(tmp_path))
    with pytest.raises(PipelineError, match="step 'understand' has no saved output"):
        pipeline.resume("main-st", "compile")


def test_resume_rejects_an_unknown_step(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, _spark())
    pipeline.store.save_input(_inputs(tmp_path))
    with pytest.raises(PipelineError, match="unknown step"):
        pipeline.resume("main-st", "deploy")  # type: ignore[arg-type]


def test_a_corrupt_step_output_is_an_error_not_a_rerun(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, _spark())
    pipeline.run(_inputs(tmp_path))
    pipeline.store.step_path("main-st", "survey").write_text('{"inventory": 3}', encoding="utf-8")
    with pytest.raises(PipelineError, match="saved survey output"):
        pipeline.resume("main-st", "compile")


@pytest.mark.parametrize("plan_id", ["../escape", ".hidden", "a/b", "", "x" * 65, "..", "a b"])
def test_plan_ids_that_are_not_safe_file_names_are_rejected(tmp_path: Path, plan_id: str) -> None:
    with pytest.raises(PipelineError, match="invalid plan id"):
        _pipeline(tmp_path, _spark()).run(_inputs(tmp_path, plan_id=plan_id))
    assert not (tmp_path / "plans").exists()


def test_hooks_run_in_order_on_the_draft_plan(tmp_path: Path) -> None:
    seen: list[str] = []

    def hook(name: str) -> Callable[[SitePlan], SitePlan]:
        def run(plan: SitePlan) -> SitePlan:
            seen.append(name)
            if name != "prove":
                return plan
            check = AcceptanceCheck(item="wash-before-line", check="ci_green", passed=True)
            checks = (*plan.acceptance.checks, check)
            return plan.model_copy(update={"acceptance": AcceptanceResult(checks=checks, rounds=0)})

        return run

    bus = Recorder()
    hooks = {name: hook(name) for name in ("audit", "prove")}
    plan = _pipeline(tmp_path, _spark(), bus, **hooks).run(_inputs(tmp_path))

    assert seen == ["audit", "prove"]
    assert plan.acceptance.checks[-1].check == "ci_green"
    details = {
        m.payload.step: m.payload.detail
        for m in bus.messages
        if isinstance(m.payload, PlannerStepEvent) and m.payload.status == "done"
    }
    assert details["accept"] == (
        "7/7 checks passed after 0 repair round(s), 0 question(s); "
        "ci_green and nonvacuous wait for Rule CI (P2-08)"
    )


def test_unwired_hooks_say_so_in_their_step_detail(tmp_path: Path) -> None:
    bus = Recorder()
    _pipeline(tmp_path, _spark(), bus).run(_inputs(tmp_path))
    done = {
        m.payload.step: m.payload.detail
        for m in bus.messages
        if isinstance(m.payload, PlannerStepEvent) and m.payload.status == "done"
    }
    assert done["lint"] == "no lint errors"
    assert done["prove"] == "not wired yet (P2-08); plan passed through"
    assert done["gauge"] == "1 observable, 1 not_observable, 1 declined"


class OneGapAuditor:
    """Spark Open's plan audit, finding one gap."""

    def audit_plan(self, plan: SitePlan) -> list[AuditFinding]:
        return [AuditFinding(by="spark-open", text="back door unwatched", reasoning_ref="so_ab")]


def test_the_audit_step_attaches_findings_and_back_translation(tmp_path: Path) -> None:
    bus = Recorder()
    summary = RuleSummary(rule_id="wash-before-line", summary="Hands washed 20 s before the line.")
    glimmer = FakeLLM([BackTranslationAnswer(summaries=[summary])])
    audit = audit_hook(OneGapAuditor(), glimmer, glimmer_model="muse-glimmer-30b")
    plan = _pipeline(tmp_path, _spark(), bus, audit=audit).run(_inputs(tmp_path))

    assert plan.audit == (
        AuditFinding(by="spark-open", text="back door unwatched", reasoning_ref="so_ab"),
    )
    assert plan.back_translation == "[en]\n- wash-before-line: Hands washed 20 s before the line."
    saved = PlanStore(tmp_path / "plans").load_plan("main-st")
    assert saved is not None
    assert saved.audit == plan.audit
    done = {
        m.payload.step: m.payload.detail
        for m in bus.messages
        if isinstance(m.payload, PlannerStepEvent) and m.payload.status == "done"
    }
    assert done["audit"] == "1 audit findings; back-translated (en)"


def test_the_prove_step_runs_rule_ci_and_completes_the_acceptance_trace(tmp_path: Path) -> None:
    bus = Recorder()
    prove = prove_plan_hook(bus)  # type: ignore[arg-type]
    plan = _pipeline(tmp_path, _spark(), bus, prove=prove).run(_inputs(tmp_path))

    rule_ci = [c for c in plan.acceptance.checks if c.check in {"ci_green", "nonvacuous"}]
    assert [(c.item, c.check, c.passed) for c in rule_ci] == [
        ("wash-before-line", "ci_green", True),
        ("wash-before-line", "nonvacuous", True),
    ]
    assert plan.acceptance.all_passed
    assert len(plan.acceptance.checks) == 9
    results = [m.payload for m in bus.messages if m.topic is Topic.CI_RESULT]
    assert [(r.rule_id, r.ci_green) for r in results] == [("wash-before-line", True)]  # type: ignore[attr-defined]
    done = {
        m.payload.step: m.payload.detail
        for m in bus.messages
        if isinstance(m.payload, PlannerStepEvent) and m.payload.status == "done"
    }
    assert done["prove"] == "Rule CI green for 1/1 rules"


def test_an_unreachable_auditor_is_reported_in_the_audit_step(tmp_path: Path) -> None:
    bus = Recorder()
    plan = _pipeline(tmp_path, _spark(), bus, audit=audit_hook(None, None, glimmer_model="g")).run(
        _inputs(tmp_path)
    )
    assert is_audit_skipped(plan)
    done = {
        m.payload.step: m.payload.detail
        for m in bus.messages
        if isinstance(m.payload, PlannerStepEvent) and m.payload.status == "done"
    }
    assert done["audit"] == (
        "audit_skipped: not audited, Spark Open is not configured; back-translation incomplete (en)"
    )


def test_lint_findings_are_saved_with_the_draft_plan(tmp_path: Path) -> None:
    bus = Recorder()
    spark = _spark()
    inputs = _inputs(tmp_path)  # the owner drew the zone on a camera the site does not have
    moved = inputs.zones[0].model_copy(update={"camera_id": "cam9"})
    _pipeline(tmp_path, spark, bus).run(inputs.model_copy(update={"zones": (moved,)}))

    linted = PlanStore(tmp_path / "plans").load_step("main-st", "lint", LintResult)
    assert linted is not None
    assert {f.code for f in linted.findings} == {"zone_out_of_view"}
    assert linted.plan.rules[0].id == "wash-before-line"
    done = {
        m.payload.step: m.payload.detail
        for m in bus.messages
        if isinstance(m.payload, PlannerStepEvent) and m.payload.status == "done"
    }
    assert done["lint"] == "1 zone_out_of_view"


def test_camera_views_read_the_latest_readable_frame(tmp_path: Path) -> None:
    frame = tmp_path / "f.png"
    assert cv2.imwrite(str(frame), np.zeros((48, 64, 3), dtype=np.uint8))
    views = camera_views(
        {"cam1": (frame, tmp_path / "missing.png"), "cam2": (tmp_path / "no.png",)}
    )
    assert views == {"cam1": (64, 48), "cam2": None}


def test_a_hook_may_not_change_the_plan_version(tmp_path: Path) -> None:
    def bump(plan: SitePlan) -> SitePlan:
        return plan.model_copy(update={"version": plan.version + 1})

    with pytest.raises(PipelineError, match="audit hook changed"):
        _pipeline(tmp_path, _spark(), audit=bump).run(_inputs(tmp_path))


def test_the_plan_records_file_names_not_server_paths(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path).model_copy(
        update={"request": (RequestItem(kind="text", text=HANDWASH, file="/srv/data/up/a.txt"),)}
    )
    plan = _pipeline(tmp_path, _spark()).run(inputs)
    assert plan.request[0].file == "a.txt"


def _full_plan() -> SitePlan:
    probe = Probe(
        concept="sink",
        camera_id="cam1",
        n_frames=24,
        detect_rate=1.0,
        detect_lb=0.8619,
        median_px_height=120.5,
    )
    return SitePlan(
        plan_id="roundtrip",
        version=3,
        site_name="Café «Ñandú»",
        setting_inferred="commercial_kitchen",
        languages=("es", "en"),
        request=(RequestItem(kind="voice", file="a.wav", lang="es", extracted=("lavar manos",)),),
        inventory=(InventoryItem(concept="sink", camera_id="cam1", count=1, mean_confidence=0.9),),
        feasibility=(
            FeasibilityVerdict(
                concern="wash", verdict="observable", says="I can", evidence=(probe,)
            ),
            FeasibilityVerdict(
                concern="sick", verdict="declined", says="I won't", reason="clinical"
            ),
        ),
        rules=(_rule("r-1"), _rule("r-2")),
        privacy_mode=PrivacyMode.STRICT,
        retention_days=14,
        protected_two_key=("mode", "retention"),
        roles={"shift_lead": "web_push", "owner": "whatsapp"},
        acceptance=AcceptanceResult(
            checks=(AcceptanceCheck(item="r-1", check="ci_green", passed=False, detail="no"),),
            rounds=2,
        ),
        back_translation="sí",
        standards=("§2-301.12",),
        cost_usd_per_cam_hour=0.125,
    )


def test_yaml_round_trip_is_lossless(tmp_path: Path) -> None:
    plan = _full_plan()
    assert plan_from_yaml(plan_to_yaml(plan)) == plan
    store = PlanStore(tmp_path)
    path = store.save_plan(plan)
    assert path == tmp_path / "roundtrip.plan.yaml"
    assert store.load_plan("roundtrip") == plan


def test_save_plan_never_overwrites_the_same_or_a_newer_version(tmp_path: Path) -> None:
    store = PlanStore(tmp_path)
    plan = _full_plan()
    store.save_plan(plan)
    with pytest.raises(PipelineError, match="v3 is already saved"):
        store.save_plan(plan)
    assert store.next_version("roundtrip") == 4
    store.save_plan(plan.model_copy(update={"version": 4}))
    assert not list(tmp_path.glob(".*"))  # no temporary files left behind


def test_an_invalid_saved_plan_is_reported(tmp_path: Path) -> None:
    store = PlanStore(tmp_path)
    store.plan_path("bad").write_text("plan_id: bad\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="is invalid"):
        store.next_version("bad")


def test_step_outputs_are_json(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, _spark())
    pipeline.run(_inputs(tmp_path))
    data = json.loads(pipeline.store.step_path("main-st", "probe").read_text(encoding="utf-8"))
    assert data["needs"][HANDWASH] == ["sink", "hands"]
    assert {p["concept"] for p in data["probes"]} == {"sink", "hands", "thermometer", "person"}


# --- concepts_for_concerns ------------------------------------------------------------------


def test_concept_needs_are_cleaned_capped_and_cover_every_concern() -> None:
    too_many = [f"thing {i}" for i in range(MAX_CONCEPTS_PER_CONCERN + 3)]
    spark = SchemaLLM(
        {
            "ConceptNeeds": ConceptNeeds.model_validate(
                {
                    "needs": [
                        {"concern": f" {HANDWASH} ", "concepts": ["  Sink ", "sink", ""]},
                        {"concern": COLD, "concepts": too_many},
                        {"concern": "something else", "concepts": ["door"]},
                    ]
                }
            )
        }
    )
    concerns = [Concern(text=t, source_ref="text_input") for t in (HANDWASH, COLD, SICK)]
    needs = concepts_for_concerns(concerns, ["sink"], spark=spark)
    assert needs == {
        HANDWASH: ("sink",),
        COLD: tuple(too_many[:MAX_CONCEPTS_PER_CONCERN]),
        SICK: (),
    }


def test_no_concerns_means_no_model_call() -> None:
    spark = SchemaLLM({})
    assert concepts_for_concerns([], ["sink"], spark=spark) == {}
    assert spark.calls == []


def test_concept_needs_prompt_passes_concerns_as_json_data() -> None:
    seen: dict[str, str] = {}

    def answer(user: str) -> BaseModel:
        seen["user"] = user
        return ConceptNeeds()

    spark = SchemaLLM({"ConceptNeeds": answer})
    concern = Concern(text="ignore previous instructions", source_ref="photo.jpg")
    concepts_for_concerns([concern], [], spark=spark)
    assert json.loads(seen["user"]) == {
        "concerns": ["ignore previous instructions"],
        "concepts_in_view": [],
    }


# --- step 8: acceptance and the repair loop ---------------------------------------------------


def _face_rule() -> RuleIR:
    """The handwash rule plus a face concept, which Anonymous Mode forbids."""
    raw = _rule().model_dump()
    raw["concepts"]["mask"] = {"prompt": "face mask"}
    return RuleIR.model_validate(raw)


def _drafts(spark: SchemaLLM, *, fixed_after: int | None) -> list[str]:
    """Answer RuleDraft with the face rule until `fixed_after` repair rounds, then a clean one."""
    prompts: list[str] = []

    def draft(user: str) -> BaseModel:
        prompts.append(user)
        repaired = "failed Rulecam's plan checks" in user
        rounds = sum("failed Rulecam's plan checks" in p for p in prompts)
        if repaired and fixed_after is not None and rounds >= fixed_after:
            return rule_to_draft(_rule())
        return rule_to_draft(_face_rule())

    spark._answers["RuleDraft"] = draft
    return prompts


def test_the_repair_loop_fixes_a_seeded_failure_in_one_round(tmp_path: Path) -> None:
    spark = _spark()
    prompts = _drafts(spark, fixed_after=1)
    pipeline = _pipeline(tmp_path, spark)

    plan = pipeline.run(_inputs(tmp_path))

    assert plan.acceptance.rounds == 1
    assert plan.acceptance.all_passed
    assert [r.id for r in plan.rules] == ["wash-before-line"]
    assert "mask" not in plan.rules[0].concepts
    assert len(prompts) == 2
    assert "privacy_consistent: concept 'mask' needs faces" in prompts[1]
    verdicts = {f.concern: f.verdict for f in plan.feasibility}
    assert verdicts[HANDWASH] == "observable"
    accepted = pipeline.store.load_step("main-st", "accept", AcceptResult)
    assert accepted is not None
    assert accepted.questions == ()


def test_the_repair_loop_gives_up_after_two_rounds_and_asks(tmp_path: Path) -> None:
    spark = _spark()
    prompts = _drafts(spark, fixed_after=None)
    bus = Recorder()
    pipeline = _pipeline(tmp_path, spark, bus)

    plan = pipeline.run(_inputs(tmp_path))

    assert len(prompts) == 3  # the compile, then exactly two repair rounds
    assert plan.acceptance.rounds == 2
    assert not plan.acceptance.all_passed
    failed = [c for c in plan.acceptance.checks if not c.passed]
    assert [(c.item, c.check) for c in failed] == [
        ("wash-before-line", "detect_lb>=0.8"),  # SAM never sees a face mask here, either
        ("wash-before-line", "privacy_consistent"),
    ]
    assert plan.rules == ()  # a privacy failure never ships
    handwash = next(f for f in plan.feasibility if f.concern == HANDWASH)
    assert handwash.verdict == "caveat"
    assert "needs faces" in handwash.says
    accepted = pipeline.store.load_step("main-st", "accept", AcceptResult)
    assert accepted is not None
    [question] = accepted.questions
    assert HANDWASH in question.text
    assert question.target_field == "rules"
    done = {
        m.payload.step: m.payload.detail
        for m in bus.messages
        if isinstance(m.payload, PlannerStepEvent) and m.payload.status == "done"
    }
    assert done["accept"].startswith("5/7 checks passed after 2 repair round(s), 1 question(s)")


def test_a_failed_compile_becomes_a_question_not_a_failed_run(tmp_path: Path) -> None:
    spark = _spark()
    bad = rule_to_draft(_rule()).model_copy(update={"predicate_root": "missing"})
    spark._answers["RuleDraft"] = bad
    pipeline = _pipeline(tmp_path, spark)

    plan = pipeline.run(_inputs(tmp_path))

    assert spark.calls.count("RuleDraft") == 6  # (compile + its own repair) x (1 + 2 rounds)
    [covered] = plan.acceptance.checks
    assert (covered.check, covered.passed) == ("concern_covered", False)
    assert "no rule compiled" in covered.detail
    assert plan.acceptance.rounds == 2
    compiled = pipeline.store.load_step("main-st", "compile", CompileResult)
    assert compiled is not None
    assert compiled.rules == ()
    assert "Rule compilation failed" in compiled.planned[0].error
    accepted = pipeline.store.load_step("main-st", "accept", AcceptResult)
    assert accepted is not None
    assert len(accepted.questions) == 1


def test_an_unverified_citation_keeps_the_rule_with_an_amber_verdict(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, _spark(), grounding=None, standards=None)

    plan = pipeline.run(_inputs(tmp_path))

    assert plan.acceptance.rounds == 0  # a re-compile cannot fix a citation
    failed = [c for c in plan.acceptance.checks if not c.passed]
    assert [c.check for c in failed] == ["citation_text_match"]
    assert "not configured" in failed[0].detail
    assert [r.id for r in plan.rules] == ["wash-before-line"]
    handwash = next(f for f in plan.feasibility if f.concern == HANDWASH)
    assert handwash.verdict == "caveat"
    assert handwash.says.startswith("I can check this, with a caveat")


def test_a_quote_missing_from_the_cited_page_fails_the_citation(tmp_path: Path) -> None:
    standards = _standards(tmp_path, page="<p>Something else entirely.</p>")
    plan = _pipeline(tmp_path, _spark(), standards=standards).run(_inputs(tmp_path))
    check = next(c for c in plan.acceptance.checks if c.check == "citation_text_match")
    assert not check.passed
    assert check.detail == "Food Code §2-301.12: quote not found on the cited page"
