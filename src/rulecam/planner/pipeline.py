"""The Site Planner's fixed ten-step pipeline and its persistence (task P2-06a, MP §3.2, §7.2).

The steps always run in this order: understand, survey, probe, gauge, ask, compile, lint, accept,
audit, prove. Each step has a typed output that is written atomically to
`plans/<plan_id>/steps/<NN>_<step>.json`; the run's inputs go to `plans/<plan_id>/input.json`.
`resume` reloads the inputs and the outputs of the steps before the resume point (by default
the first step without an output), so a crashed run continues where it stopped. Running a step
first deletes the outputs of that step and every later one, so no stale output survives.

Every step publishes `planner.step` (`PlannerStepEvent`: running, then done or failed). The
finished `SitePlan` is saved to `plans/<plan_id>.plan.yaml` with its version one above the plan
already saved there, then published on `plan.updated`.

Steps 1-6 call the P2-02..P2-05 modules; a concern whose rule fails to compile is kept with its
error rather than failing the run. Step 7 lints the draft plan (`planner.lint`, P2-06b) against
the site's cameras and records the findings with the plan. Step 8 runs the acceptance checks
and the bounded repair loop (`planner.acceptance`, P2-06c): it fills the plan's acceptance
trace, re-compiles failing rules at most twice, and turns what still fails into amber verdicts
and questions. Steps 9-10 are plan -> plan hooks for audit (P2-07) and Rule CI (P2-08/09); a
hook that is not wired yet passes the plan through unchanged and says so in its step detail.

Geometry (`planner.geometry`): steps 2 and 3 record their SAM detections. The survey turns
reference objects (a pallet, a 1 m floor square) into ground calibrations
(`SurveyResult.calibrations`), and the probe turns needed places (sink, prep counter, door)
into polygon zones (`ProbeResult.zones`). When a concern needs metres, the probe also prompts SAM
with the reference phrases (pallet, 1 m floor square) on the cameras that concern runs on and
that are still uncalibrated, so calibration does not depend on how Spark worded the survey
(`ProbeResult.reference_probes`, `.calibrations`). Compile, lint and accept use the merge of
those with the owner's site config (`PipelineInput.zones` and `.calibrations`), the owner's winning.
`PlanStore.calibrations` gives the merged calibrations of a saved run, which plan approval
(`runtime.activate`) hands to the runtime.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from functools import partial
from pathlib import Path
from typing import Final, Literal, TypeVar

import yaml
from pydantic import BaseModel, Field, ValidationError

from rulecam.bus import Bus, Topic
from rulecam.contracts import StructuredLLM, Transcriber
from rulecam.contracts.core import Calibration, Frozen, PrivacyMode, Zone
from rulecam.contracts.plan import (
    AcceptanceResult,
    FeasibilityVerdict,
    InventoryItem,
    Probe,
    RequestItem,
    SitePlan,
)
from rulecam.contracts.rule_ir import RuleIR
from rulecam.log import get_logger
from rulecam.perception.crop_zoom import CropRegion, CropSegmenter
from rulecam.perception.point_map import GeometryEstimator
from rulecam.planner.acceptance import CitationResult, PlannedRule, RepairLoop, SiteFacts
from rulecam.planner.errors import GuardrailDeclinedError, PipelineError, RuleCompilationError
from rulecam.planner.feasibility import gauge_plan_feasibility
from rulecam.planner.geometry import (
    RecordingSegmenter,
    ReferenceProbe,
    SiteGeometry,
    frame_size,
    metric_cameras,
    model_calibrations,
    probe_references,
    scene_calibrations,
    scene_zones,
    standing_people,
    trusted_calibrations,
)
from rulecam.planner.hedge import HedgedSegmenter
from rulecam.planner.lint import CameraViews, LintFinding, lint_plan
from rulecam.planner.models import Concern, UnderstandResult
from rulecam.planner.standards import (
    GroundingLLM,
    StandardsLibrary,
    check_citation,
    ground_concern_standard,
)
from rulecam.planner.steps.ask import Question, propose_clarifying_questions
from rulecam.planner.steps.audit import audit_summary
from rulecam.planner.steps.compile import compile_rule
from rulecam.planner.steps.probe import ProbeZoom, concept_needs, probe_concepts
from rulecam.planner.steps.survey import (
    DEFAULT_SURVEY_FRAMES,
    ImageSegmenter,
    sample_frames,
    survey_cameras,
)
from rulecam.planner.steps.understand import FileAttacher, understand
from rulecam.planner.survey_cache import Reused, SurveyCache, scene_hash
from rulecam.prototype import prototype_mode
from rulecam.replay.cache import atomic_write_text

_log = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)

StepName = Literal[
    "understand", "survey", "probe", "gauge", "ask", "compile", "lint", "accept", "audit", "prove"
]
PIPELINE_STEPS: Final[tuple[StepName, ...]] = (
    "understand",
    "survey",
    "probe",
    "gauge",
    "ask",
    "compile",
    "lint",
    "accept",
    "audit",
    "prove",
)
PLAN_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MAX_DETAIL_CHARS: Final = 300
COMPILED_VERDICTS: Final = frozenset({"observable", "caveat"})
HOOK_STEPS: Final[tuple[StepName, ...]] = ("audit", "prove")
GEOMETRY_WAIT_S: Final = 5.0
HOOK_TASKS: Final[dict[StepName, str]] = {
    "audit": "P2-07",
    "prove": "P2-08",
}

PlanHook = Callable[[SitePlan], SitePlan]
StepStatus = Literal["running", "done", "failed"]


class PipelineInput(Frozen):
    """Everything a run needs, saved so that `resume` does not need it again.

    Attributes:
        plan_id: The plan's id; also its directory name, so it must match `PLAN_ID_PATTERN`.
        site_name: Human-readable site name.
        request: The owner's request items (files are server-side paths, never client paths).
        camera_frames: Camera id -> recent frames, oldest first.
        zones: The owner's zones (site config); the rules may also use zones from the scene.
        roles: Role -> alert channel.
        privacy_mode: The site's privacy mode.
        retention_days: Evidence retention.
        calibrations: Camera id -> the owner's ground calibration (site config); it wins over a
            calibration from the scene.
        languages: Crew languages from the site config, listed before inferred ones.
        seed: Seed for frame sampling.
    """

    plan_id: str
    site_name: str
    request: tuple[RequestItem, ...]
    camera_frames: dict[str, tuple[Path, ...]] = Field(default_factory=dict)
    zones: tuple[Zone, ...] = ()
    roles: dict[str, str] = Field(default_factory=dict)
    privacy_mode: PrivacyMode = PrivacyMode.STANDARD
    retention_days: int = 30
    calibrations: dict[str, Calibration] = Field(default_factory=dict)
    languages: tuple[str, ...] = ()
    seed: int = 0


class SurveyResult(Frozen):
    """Step 2: what each camera sees, and the calibrations its reference objects give.

    `reused` names, per camera, the earlier plan whose survey of the same scene was reused and
    how old it was (`planner.survey_cache`); empty when the survey ran.
    """

    inventory: tuple[InventoryItem, ...]
    calibrations: dict[str, Calibration] = Field(default_factory=dict)
    reused: dict[str, Reused] = Field(default_factory=dict)


class ProbeResult(Frozen):
    """Step 3: the concepts each concern needs, how reliably SAM sees them, and place zones.

    `reference_probes` are the reference prompts tried for the metric concerns' cameras and
    `calibrations` what they gave (cameras the owner or the survey calibrated are not probed).
    `distrusted` are cameras whose survey calibration the people seen by the probe contradict;
    the plan never uses those calibrations. `worn` are the needed concepts Spark said a person
    wears or holds, with the body region crop-zoom looked at (add-on 3.3).
    """

    needs: dict[str, tuple[str, ...]]
    probes: tuple[Probe, ...]
    worn: dict[str, CropRegion] = Field(default_factory=dict)
    zones: tuple[Zone, ...] = ()
    reference_probes: tuple[ReferenceProbe, ...] = ()
    calibrations: dict[str, Calibration] = Field(default_factory=dict)
    distrusted: tuple[str, ...] = ()


class GaugeResult(Frozen):
    """Step 4: one feasibility verdict per concern."""

    feasibility: tuple[FeasibilityVerdict, ...]


class AskResult(Frozen):
    """Step 5: clarifying questions for plan fields that are still missing."""

    questions: tuple[Question, ...]


class CompileResult(Frozen):
    """Step 6: a rule (or the compile error) per concern a camera can check, and the verdicts.

    `planned` has one entry per `observable` or `caveat` concern the guardrails allowed.
    `feasibility` is the gauge's, except that a concern the compiler's guardrails declined is
    `declined`.
    """

    planned: tuple[PlannedRule, ...]
    feasibility: tuple[FeasibilityVerdict, ...]

    @property
    def rules(self) -> tuple[RuleIR, ...]:
        """The rules that compiled, in concern order."""
        return tuple(p.rule for p in self.planned if p.rule is not None)


class LintResult(Frozen):
    """Step 7: the draft plan and its lint findings (empty when the plan is clean)."""

    plan: SitePlan
    findings: tuple[LintFinding, ...]


class AcceptResult(Frozen):
    """Step 8: the plan with its acceptance trace, and questions about rules taken out of it."""

    plan: SitePlan
    questions: tuple[Question, ...]


class PlannerStepEvent(Frozen):
    """The `planner.step` payload the UI's step progress reads."""

    plan_id: str
    step: StepName
    status: StepStatus
    detail: str = ""


def check_plan_id(plan_id: str) -> str:
    """Return `plan_id` if it is safe as a file name, else raise `PipelineError`."""
    if PLAN_ID_PATTERN.fullmatch(plan_id) is None:
        msg = f"invalid plan id {plan_id!r}: use 1-64 letters, digits, '_', '-' or '.'"
        raise PipelineError(msg)
    return plan_id


def plan_to_yaml(plan: SitePlan) -> str:
    """Dump a plan as YAML (JSON-mode values, field order kept)."""
    return yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False, allow_unicode=True)


def plan_from_yaml(text: str) -> SitePlan:
    """Parse and validate a plan dumped by `plan_to_yaml`."""
    return SitePlan.model_validate(yaml.safe_load(text))


class PlanStore:
    """Where plans, run inputs and step outputs live on disk (atomic writes)."""

    def __init__(self, root: Path) -> None:
        """Use `root` (normally `plans/`) as the plans directory."""
        self.root = root

    def plan_path(self, plan_id: str) -> Path:
        """`<root>/<plan_id>.plan.yaml`."""
        return self.root / f"{check_plan_id(plan_id)}.plan.yaml"

    def input_path(self, plan_id: str) -> Path:
        """`<root>/<plan_id>/input.json`."""
        return self.root / check_plan_id(plan_id) / "input.json"

    def step_path(self, plan_id: str, step: StepName) -> Path:
        """`<root>/<plan_id>/steps/<NN>_<step>.json`, NN being the 1-based step number."""
        number = PIPELINE_STEPS.index(step) + 1
        return self.root / check_plan_id(plan_id) / "steps" / f"{number:02d}_{step}.json"

    def save_input(self, inputs: PipelineInput) -> None:
        """Write a run's inputs."""
        atomic_write_text(self.input_path(inputs.plan_id), inputs.model_dump_json(indent=2))

    def load_input(self, plan_id: str) -> PipelineInput:
        """Read a run's inputs.

        Raises:
            PipelineError: If the plan was never started here, or the file is invalid.
        """
        inputs = self._read(self.input_path(plan_id), PipelineInput, "run input")
        if inputs is None:
            msg = f"no saved run input for plan {plan_id!r}"
            raise PipelineError(msg)
        return inputs

    def calibrations(self, plan_id: str) -> dict[str, Calibration]:
        """The ground calibrations a saved run used: the scene's, overridden by the owner's.

        Args:
            plan_id: The plan.

        Returns:
            Camera id -> calibration; empty when the plan has no saved run.

        Raises:
            PipelineError: If the saved input or survey output is invalid.
        """
        if not self.input_path(plan_id).is_file():
            return {}
        owner = self.load_input(plan_id).calibrations
        survey = self.load_step(plan_id, "survey", SurveyResult)
        probe = self.load_step(plan_id, "probe", ProbeResult)
        distrusted = set(probe.distrusted) if probe is not None else set()
        scene = {
            **{
                c: cal
                for c, cal in (survey.calibrations if survey is not None else {}).items()
                if c not in distrusted
            },
            **(probe.calibrations if probe is not None else {}),
        }
        return {**scene, **owner}

    def save_step(self, plan_id: str, step: StepName, output: BaseModel) -> None:
        """Write one step's output."""
        atomic_write_text(self.step_path(plan_id, step), output.model_dump_json(indent=2))

    def load_step(self, plan_id: str, step: StepName, kind: type[M]) -> M | None:
        """Read one step's output, or `None` if the step has not finished.

        Raises:
            PipelineError: If the saved output does not validate as `kind`.
        """
        return self._read(self.step_path(plan_id, step), kind, f"{step} output")

    def clear_steps_from(self, plan_id: str, step: StepName) -> None:
        """Delete the outputs of `step` and every later step."""
        for later in PIPELINE_STEPS[PIPELINE_STEPS.index(step) :]:
            self.step_path(plan_id, later).unlink(missing_ok=True)

    def first_missing_step(self, plan_id: str) -> StepName | None:
        """The first step without a saved output (`None` if all ten have one)."""
        return next((s for s in PIPELINE_STEPS if not self.step_path(plan_id, s).is_file()), None)

    def load_plan(self, plan_id: str) -> SitePlan | None:
        """Read the saved plan, or `None` if there is none.

        Raises:
            PipelineError: If the YAML does not validate as a `SitePlan`.
        """
        path = self.plan_path(plan_id)
        if not path.is_file():
            return None
        try:
            return plan_from_yaml(path.read_text(encoding="utf-8"))
        except (ValidationError, yaml.YAMLError) as exc:
            msg = f"saved plan {path} is invalid: {exc}"
            raise PipelineError(msg) from exc

    def next_version(self, plan_id: str) -> int:
        """One above the saved plan's version (1 when there is none)."""
        saved = self.load_plan(plan_id)
        return saved.version + 1 if saved is not None else 1

    def save_plan(self, plan: SitePlan) -> Path:
        """Write the plan's YAML, refusing to overwrite a version as new or newer.

        Raises:
            PipelineError: If the saved plan's version is not lower (another run saved first).
        """
        saved = self.load_plan(plan.plan_id)
        if saved is not None and saved.version >= plan.version:
            msg = (
                f"plan {plan.plan_id!r} v{saved.version} is already saved; "
                f"not overwriting it with v{plan.version}"
            )
            raise PipelineError(msg)
        path = self.plan_path(plan.plan_id)
        atomic_write_text(path, plan_to_yaml(plan))
        return path

    @staticmethod
    def _read(path: Path, kind: type[M], what: str) -> M | None:
        if not path.is_file():
            return None
        try:
            return kind.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            msg = f"saved {what} {path} is invalid: {exc}"
            raise PipelineError(msg) from exc


def _plan_request(items: Sequence[RequestItem]) -> tuple[RequestItem, ...]:
    """The request as the plan records it: file names only, never server paths."""
    return tuple(
        item.model_copy(update={"file": Path(item.file).name}) if item.file else item
        for item in items
    )


def _verdict_counts(gauge: GaugeResult) -> str:
    verdicts = [f.verdict for f in gauge.feasibility]
    return ", ".join(f"{verdicts.count(v)} {v}" for v in dict.fromkeys(verdicts))


def camera_views(camera_frames: dict[str, tuple[Path, ...]]) -> CameraViews:
    """The site's cameras with the frame size of each camera's latest readable frame.

    Args:
        camera_frames: Camera id -> frames, oldest first.

    Returns:
        Camera id -> (width, height), or `None` for a camera none of whose frames can be read.
    """
    return {
        camera_id: next(
            (size for f in reversed(frames) if (size := frame_size(f)) is not None), None
        )
        for camera_id, frames in camera_frames.items()
    }


def _compiles(verdict: FeasibilityVerdict) -> bool:
    """Whether a concern is compiled: feasible ones; in prototype mode all but declined ones."""
    if prototype_mode():
        return verdict.verdict != "declined"
    return verdict.verdict in COMPILED_VERDICTS


def survey_summary(survey: SurveyResult) -> str:
    """Step 2's detail, e.g. "12 things seen; cam1 calibrated from pallet_48x40in (0.03 m)"."""
    calibrated = [
        f"{cam} calibrated from {cal.reference or cal.method} ({cal.err_m:.2f} m)"
        for cam, cal in survey.calibrations.items()
    ]
    reused = [
        f"{cam} reused from {r.plan_id} ({r.age_s:.0f} s old, same scene)"
        for cam, r in survey.reused.items()
    ]
    return "; ".join([f"{len(survey.inventory)} things seen", *calibrated, *reused])


def probe_summary(probe: ProbeResult) -> str:
    """Step 3's detail, e.g. "6 probes; zones from the scene: sink; references on dock: ...".

    The reference part lists each prompt tried with its detection count, then the calibration
    it gave (or that none was made).
    """
    parts = [f"{len(probe.probes)} probes"]
    parts.extend(
        f"survey calibration on {c} dropped: the people in view disagree with its scale"
        for c in probe.distrusted
    )
    if probe.zones:
        parts.append("zones from the scene: " + ", ".join(z.zone_id for z in probe.zones))
    cameras = dict.fromkeys(r.camera_id for r in probe.reference_probes)
    for camera_id in cameras:
        tried = ", ".join(
            f"{r.prompt} ({r.n_detections} found in {r.n_frames} frames)"
            for r in probe.reference_probes
            if r.camera_id == camera_id
        )
        cal = probe.calibrations.get(camera_id)
        outcome = (
            f"calibrated from {cal.reference or cal.method} ({cal.err_m:.2f} m)"
            if cal is not None
            else "no calibration"
        )
        parts.append(f"references on {camera_id}: {tried}; {outcome}")
    return "; ".join(parts)


def _lint_summary(result: LintResult) -> str:
    if not result.findings:
        return "no lint errors"
    codes = [f.code for f in result.findings]
    return ", ".join(f"{codes.count(c)} {c}" for c in dict.fromkeys(codes))


def prove_summary(plan: SitePlan) -> str:
    """Describe step 10's outcome, e.g. "Rule CI green for 2/3 rules", from the trace.

    Args:
        plan: The plan after the prove step.

    Returns:
        The `planner.step` detail.
    """
    green = [c for c in plan.acceptance.checks if c.check == "ci_green"]
    return f"Rule CI green for {sum(c.passed for c in green)}/{len(green)} rules"


def _accept_summary(result: AcceptResult) -> str:
    acceptance = result.plan.acceptance
    passed = sum(c.passed for c in acceptance.checks)
    return (
        f"{passed}/{len(acceptance.checks)} checks passed after {acceptance.rounds} repair "
        f"round(s), {len(result.questions)} question(s); ci_green and nonvacuous wait for "
        "Rule CI (P2-08)"
    )


def _detail(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_DETAIL_CHARS]


class PlannerPipeline:
    """Runs the ten fixed steps for one plan at a time (runs are serialised by a lock)."""

    def __init__(
        self,
        *,
        spark: StructuredLLM,
        files: FileAttacher,
        segmenter: ImageSegmenter,
        store: PlanStore,
        voice: Transcriber | None = None,
        bus: Bus | None = None,
        grounding: GroundingLLM | None = None,
        standards: StandardsLibrary | None = None,
        audit: PlanHook | None = None,
        prove: PlanHook | None = None,
        zoom: CropSegmenter | None = None,
        geometry: GeometryEstimator | None = None,
        survey_cache: SurveyCache | None = None,
        geometry_wait_s: float = GEOMETRY_WAIT_S,
    ) -> None:
        """Create the pipeline.

        Args:
            spark: Muse Spark (`SparkClient` through the replay cache).
            files: Uploads frames and documents (`SparkClient` does).
            segmenter: SAM 3.1 on single images (`HostedSam31` through the replay cache).
            store: Where the plan, inputs and step outputs are saved.
            voice: Voice Transcribe, needed for voice items.
            bus: Receives `planner.step` and `plan.updated`.
            grounding: Spark with web search, which proposes a rule's citation.
            standards: Cited pages, for `citation_text_match`; without it (or `grounding`) a
                rule citing a standard fails that check.
            audit: Step 9 hook (P2-07, `planner.steps.audit.audit_hook`).
            prove: Step 10 hook (P2-08/09, `rule_ci.prove_plan_hook`).
            zoom: Crop-zoom SAM 3.1 (add-on 3.3): the probe looks for worn concepts on
                enlarged crops of each person where the full frame missed them.
            geometry: MoGe-2 on the GPU pod (add-on 3.5): the survey calibrates each camera
                from a metric point map and SAM's floor when it is available.
            survey_cache: Reuses a fresh survey of the same scene (`planner.survey_cache`).
            geometry_wait_s: How long the survey waits, after its SAM calls, for the geometry
                model's health check that started with it; a slower check skips the model.
        """
        self._spark = spark
        self._files = files
        self._segmenter = HedgedSegmenter(segmenter)  # a hung SAM call gets a twin
        self._voice = voice
        self._bus = bus
        self._grounding = grounding
        self._standards = standards
        self._zoom = zoom
        self._geometry = geometry
        self._survey_cache = survey_cache
        self._geometry_wait_s = geometry_wait_s
        self.store = store
        self._hooks: dict[StepName, PlanHook | None] = dict(
            zip(HOOK_STEPS, (audit, prove), strict=True)
        )
        self._lock = threading.Lock()

    def run(self, inputs: PipelineInput) -> SitePlan:
        """Run every step from the start.

        Args:
            inputs: The run's inputs; saved before step 1.

        Returns:
            The saved and published plan.

        Raises:
            PipelineError: On an invalid plan id or when a newer plan version is already saved.
            PlannerError: Or any client error a step raises; the step is reported `failed`.
        """
        check_plan_id(inputs.plan_id)
        with self._lock:
            self.store.save_input(inputs)
            return self._execute(inputs, start=0)

    def resume(self, plan_id: str, from_step: StepName | None = None) -> SitePlan:
        """Continue a saved run; earlier steps are loaded from their saved outputs.

        Args:
            plan_id: The plan to continue.
            from_step: First step to run again; by default the first without a saved output
                (all ten saved: the last step re-runs and the plan is saved again).

        Returns:
            The saved and published plan.

        Raises:
            PipelineError: If the run input or an earlier step's output is missing or invalid.
        """
        with self._lock:
            inputs = self.store.load_input(plan_id)
            step = from_step or self.store.first_missing_step(plan_id) or PIPELINE_STEPS[-1]
            if step not in PIPELINE_STEPS:
                msg = f"unknown step {step!r}; the steps are {', '.join(PIPELINE_STEPS)}"
                raise PipelineError(msg)
            return self._execute(inputs, start=PIPELINE_STEPS.index(step))

    def _execute(self, inputs: PipelineInput, *, start: int) -> SitePlan:
        plan_id = inputs.plan_id
        self.store.clear_steps_from(plan_id, PIPELINE_STEPS[start])

        def step(
            name: StepName, kind: type[M], compute: Callable[[], M], summary: Callable[[M], str]
        ) -> M:
            return self._step(plan_id, name, kind, start=start, compute=compute, summary=summary)

        understood = step(
            "understand",
            UnderstandResult,
            lambda: self._understand(inputs),
            lambda out: f"{len(out.concerns)} concerns",
        )
        survey = step(
            "survey",
            SurveyResult,
            lambda: self._survey(inputs),
            survey_summary,
        )
        probe = step(
            "probe",
            ProbeResult,
            lambda: self._probe(inputs, understood, survey),
            probe_summary,
        )
        gauge = step("gauge", GaugeResult, lambda: self._gauge(understood, probe), _verdict_counts)
        # Compile reads what ask reads, not ask's answer: its model call starts with ask's, and
        # the steps are still reported (and saved) in order, ask first.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="compile-ahead") as ahead:
            compiling = (
                ahead.submit(self._compile, inputs, understood, survey, probe, gauge)
                if PIPELINE_STEPS.index("compile") >= start
                else None
            )
            step(
                "ask",
                AskResult,
                lambda: self._ask(inputs, understood, survey, probe),
                lambda out: f"{len(out.questions)} questions",
            )
            compiled = step(
                "compile",
                CompileResult,
                compiling.result if compiling is not None else self._unreachable_compile,
                lambda out: f"{len(out.rules)} rules",
            )
        linted = step(
            "lint",
            LintResult,
            lambda: self._lint(self._draft(inputs, understood, survey, compiled), inputs),
            _lint_summary,
        )
        accepted = step(
            "accept",
            AcceptResult,
            lambda: self._accept(inputs, linted.plan, survey, probe, compiled),
            _accept_summary,
        )
        plan = accepted.plan
        for name in HOOK_STEPS:
            plan = step(
                name,
                SitePlan,
                partial(self._run_hook, name, plan),
                partial(self._hook_summary, name),
            )
        self.store.save_plan(plan)
        if self._bus is not None:
            self._bus.publish(Topic.PLAN_UPDATED, plan)
        _log.info("plan_saved", plan_id=plan_id, version=plan.version, rules=len(plan.rules))
        return plan

    @staticmethod
    def _unreachable_compile() -> CompileResult:
        msg = "compile runs ahead whenever it is run; a saved compile is loaded instead"
        raise PipelineError(msg)

    def _hook_summary(self, name: StepName, plan: SitePlan) -> str:
        if self._hooks[name] is None:
            return f"not wired yet ({HOOK_TASKS[name]}); plan passed through"
        return audit_summary(plan) if name == "audit" else prove_summary(plan)

    def _run_hook(self, name: StepName, plan: SitePlan) -> SitePlan:
        hook = self._hooks[name]
        if hook is None:
            return plan
        out = SitePlan.model_validate(hook(plan).model_dump())  # `model_copy` skips validation
        if (out.plan_id, out.version) != (plan.plan_id, plan.version):
            msg = f"the {name} hook changed the plan id or version"
            raise PipelineError(msg)
        return out

    def _step(
        self,
        plan_id: str,
        name: StepName,
        kind: type[M],
        *,
        start: int,
        compute: Callable[[], M],
        summary: Callable[[M], str],
    ) -> M:
        if PIPELINE_STEPS.index(name) < start:
            saved = self.store.load_step(plan_id, name, kind)
            if saved is None:
                msg = (
                    f"cannot resume plan {plan_id!r} at {PIPELINE_STEPS[start]!r}: "
                    f"step {name!r} has no saved output"
                )
                raise PipelineError(msg)
            self._publish(plan_id, name, "done", "loaded from the saved output")
            return saved
        self._publish(plan_id, name, "running")
        try:
            output = compute()
        except Exception as exc:
            self._publish(plan_id, name, "failed", _detail(exc))
            _log.warning("planner_step_failed", plan_id=plan_id, step=name, error=_detail(exc))
            raise
        self.store.save_step(plan_id, name, output)
        self._publish(plan_id, name, "done", summary(output))
        return output

    def _publish(self, plan_id: str, step: StepName, status: StepStatus, detail: str = "") -> None:
        _log.info("planner_step", plan_id=plan_id, step=step, status=status)
        if self._bus is not None:
            event = PlannerStepEvent(plan_id=plan_id, step=step, status=status, detail=detail)
            self._bus.publish(Topic.PLANNER_STEP, event)

    # --- steps 1-6 ------------------------------------------------------------------------------

    def _understand(self, inputs: PipelineInput) -> UnderstandResult:
        return understand(inputs.request, spark=self._spark, files=self._files, voice=self._voice)

    def _survey(self, inputs: PipelineInput) -> SurveyResult:
        scenes = (
            {
                camera_id: scene_hash(
                    sample_frames(frames, DEFAULT_SURVEY_FRAMES, seed=inputs.seed)
                )
                for camera_id, frames in inputs.camera_frames.items()
            }
            if self._survey_cache is not None
            else {}
        )
        if self._survey_cache is not None and (hit := self._survey_cache.lookup(scenes)):
            inventory, calibrations, reused = hit
            _log.info("survey_reused", plan_id=inputs.plan_id, cameras=sorted(reused))
            return SurveyResult(inventory=inventory, calibrations=calibrations, reused=reused)
        ready = self._check_geometry()  # in the background, while SAM surveys
        recorder = RecordingSegmenter(self._segmenter)
        inventory = survey_cameras(
            inputs.camera_frames,
            spark=self._spark,
            files=self._files,
            segmenter=recorder,
            seed=inputs.seed,
        )
        model = self._model_calibrations(inputs, recorder, ready)
        calibrations = scene_calibrations(recorder.sightings, model=model)
        if self._survey_cache is not None:
            self._survey_cache.keep(inputs.plan_id, scenes, inventory, calibrations)
        return SurveyResult(inventory=inventory, calibrations=calibrations)

    def _check_geometry(self) -> Future[bool] | None:
        """Start the geometry model's health check (a slow GPU can take a minute to answer)."""
        if self._geometry is None:
            return None
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="geometry-health")
        future = pool.submit(self._geometry.available)
        pool.shutdown(wait=False)
        return future

    def _geometry_available(self, ready: Future[bool] | None) -> bool:
        if ready is None:
            return False
        try:
            return ready.result(timeout=self._geometry_wait_s)
        except FutureTimeout:
            _log.info("geometry_check_slow", waited_s=self._geometry_wait_s)
            return False

    def _model_calibrations(
        self, inputs: PipelineInput, recorder: RecordingSegmenter, ready: Future[bool] | None
    ) -> dict[str, Calibration]:
        if self._geometry is None or not self._geometry_available(ready):
            _log.info("model_calibration_skipped", geometry=self._geometry is not None)
            return {}
        return model_calibrations(
            inputs.camera_frames,
            geometry=self._geometry,
            segmenter=recorder,
            seed=inputs.seed,
            people=standing_people(recorder.sightings),
        )

    def _probe(
        self, inputs: PipelineInput, understood: UnderstandResult, survey: SurveyResult
    ) -> ProbeResult:
        planned = concept_needs(
            understood.concerns, [i.concept for i in survey.inventory], spark=self._spark
        )
        needs = planned.needs
        concepts = [c for needed in needs.values() for c in needed]
        recorder = RecordingSegmenter(self._segmenter)
        probes = probe_concepts(
            concepts,
            inputs.camera_frames,
            segmenter=recorder,
            seed=inputs.seed,
            zoom=ProbeZoom(recorder, self._zoom) if self._zoom is not None else None,
            worn=planned.worn,
        )
        zones = scene_zones(recorder.sightings, concepts)
        trusted = trusted_calibrations(survey.calibrations, recorder.sightings)
        seen: dict[str, list[str]] = {}
        for p in probes:
            if p.detect_rate > 0:
                seen.setdefault(p.camera_id, []).append(p.concept)
        cameras = metric_cameras(needs, seen, calibrated=[*inputs.calibrations, *trusted])
        tried = probe_references(
            cameras, inputs.camera_frames, segmenter=recorder, seed=inputs.seed
        )
        calibrations = {
            camera_id: cal
            for camera_id, cal in scene_calibrations(recorder.sightings).items()
            if camera_id in cameras
        }
        return ProbeResult(
            needs=needs,
            probes=probes,
            worn=planned.worn,
            zones=zones,
            reference_probes=tried,
            calibrations=calibrations,
            distrusted=tuple(sorted(set(survey.calibrations) - set(trusted))),
        )

    def geometry(
        self,
        inputs: PipelineInput,
        survey: SurveyResult,
        probe: ProbeResult,
        feasibility: Sequence[FeasibilityVerdict] = (),
    ) -> SiteGeometry:
        """The zones and calibrations rules may use: the scene's, overridden by the owner's.

        `feasibility` (the gauge's verdicts) scopes each rule to its cameras
        (`planner.scope`), so a rule's calibration name covers only the cameras it runs on.
        """
        return SiteGeometry.build(
            inputs.camera_frames,
            owner_zones=inputs.zones,
            scene_zones=probe.zones,
            owner_calibrations=inputs.calibrations,
            scene_calibrations={
                **{c: cal for c, cal in survey.calibrations.items() if c not in probe.distrusted},
                **probe.calibrations,
            },
            feasibility=feasibility,
        )

    def _gauge(self, understood: UnderstandResult, probe: ProbeResult) -> GaugeResult:
        requirements = {c.text: probe.needs.get(c.text, ()) for c in understood.concerns}
        feasibility = gauge_plan_feasibility(requirements, probe.probes, spark=self._spark)
        return GaugeResult(feasibility=feasibility)

    def _ask(
        self,
        inputs: PipelineInput,
        understood: UnderstandResult,
        survey: SurveyResult,
        probe: ProbeResult,
    ) -> AskResult:
        if not understood.concerns:
            return AskResult(questions=())
        known: dict[str, object] = {
            "site_name": inputs.site_name,
            "privacy_mode": inputs.privacy_mode.value,
            "retention_days": inputs.retention_days,
        }
        if inputs.roles:
            known["roles"] = inputs.roles
        geometry = self.geometry(inputs, survey, probe)
        if geometry.zones:
            known["zones"] = list(geometry.zones)
        if geometry.calibrations:
            known["calibrated_cameras"] = list(geometry.calibrations)
        questions = propose_clarifying_questions(
            understood.concerns,
            known,
            spark=self._spark,
            inventory_concepts=list(dict.fromkeys(i.concept for i in survey.inventory)),
        )
        return AskResult(questions=questions)

    def _compile(
        self,
        inputs: PipelineInput,
        understood: UnderstandResult,
        survey: SurveyResult,
        probe: ProbeResult,
        gauge: GaugeResult,
    ) -> CompileResult:
        verdicts = {v.concern: v for v in gauge.feasibility}
        planned: list[PlannedRule] = []
        for concern in understood.concerns:
            verdict = verdicts.get(concern.text)
            if verdict is None or not _compiles(verdict):
                continue
            try:
                rule = self._compile_concern(
                    inputs, survey, probe, concern, (), feasibility=gauge.feasibility
                )
            except GuardrailDeclinedError as declined:
                verdicts[concern.text] = declined.declined.to_feasibility_verdict(concern.text)
                continue
            except RuleCompilationError as exc:  # step 8 repairs it or asks about it
                _log.warning("compile_failed", plan_id=inputs.plan_id, error=str(exc))
                planned.append(PlannedRule(concern=concern, error=str(exc)))
                continue
            planned.append(PlannedRule(concern=concern, rule=rule))
        return CompileResult(
            planned=tuple(planned),
            feasibility=tuple(verdicts[v.concern] for v in gauge.feasibility),
        )

    def _compile_concern(
        self,
        inputs: PipelineInput,
        survey: SurveyResult,
        probe: ProbeResult,
        concern: Concern,
        feedback: Sequence[str],
        *,
        feasibility: Sequence[FeasibilityVerdict],
    ) -> RuleIR:
        return compile_rule(
            concern,
            spark=self._spark,
            inventory=survey.inventory,
            site_name=inputs.site_name,
            plan_id=inputs.plan_id,
            feedback=feedback,
            geometry=self.geometry(inputs, survey, probe, feasibility),
        )

    # --- step 8 ---------------------------------------------------------------------------------

    def _accept(
        self,
        inputs: PipelineInput,
        draft: SitePlan,
        survey: SurveyResult,
        probe: ProbeResult,
        compiled: CompileResult,
    ) -> AcceptResult:
        facts = SiteFacts(
            views=dict(camera_views(inputs.camera_frames)),
            probes=probe.probes,
            calibrations=self.geometry(inputs, survey, probe).calibrations,
            privacy_mode=inputs.privacy_mode,
        )
        loop = RepairLoop(
            facts,
            recompile=partial(
                self._compile_concern, inputs, survey, probe, feasibility=compiled.feasibility
            ),
            probe=partial(self._probe_more, inputs),
            verify_citation=self._verify_citation,
        )
        outcome = loop.run(compiled.planned, draft.feasibility)
        plan = SitePlan.model_validate(
            {
                **draft.model_dump(),
                "rules": outcome.rules,
                "feasibility": outcome.feasibility,
                "acceptance": outcome.acceptance,
            }
        )
        return AcceptResult(plan=plan, questions=outcome.questions)

    def _probe_more(self, inputs: PipelineInput, concepts: Sequence[str]) -> tuple[Probe, ...]:
        return probe_concepts(
            concepts, inputs.camera_frames, segmenter=self._segmenter, seed=inputs.seed
        )

    def _verify_citation(self, concern: Concern) -> CitationResult:
        if self._grounding is None or self._standards is None:
            return CitationResult(
                matched=False, detail="standards grounding is not configured; citation unverified"
            )
        citation = ground_concern_standard(concern, spark=self._grounding)
        if citation is None:
            cited = concern.standard_ref or concern.text
            return CitationResult(matched=False, detail=f"no citable source found for {cited!r}")
        check = check_citation(citation, self._standards)
        return CitationResult(matched=check.matched, detail=f"{citation.ref}: {check.detail}")

    def _lint(self, draft: SitePlan, inputs: PipelineInput) -> LintResult:
        findings = lint_plan(draft, views=camera_views(inputs.camera_frames))
        for finding in findings:
            _log.warning("plan_lint_error", plan_id=draft.plan_id, **finding.model_dump())
        return LintResult(plan=draft, findings=findings)

    def _draft(
        self,
        inputs: PipelineInput,
        understood: UnderstandResult,
        survey: SurveyResult,
        compiled: CompileResult,
    ) -> SitePlan:
        """The plan steps 7-10 work on: everything but acceptance, audit and back-translation."""
        return SitePlan(
            plan_id=inputs.plan_id,
            version=self.store.next_version(inputs.plan_id),
            site_name=inputs.site_name,
            setting_inferred=understood.setting_guess,
            languages=tuple(dict.fromkeys((*inputs.languages, *understood.languages))),
            request=_plan_request(inputs.request),
            inventory=survey.inventory,
            feasibility=compiled.feasibility,
            rules=compiled.rules,
            privacy_mode=inputs.privacy_mode,
            retention_days=inputs.retention_days,
            roles=inputs.roles,
            acceptance=AcceptanceResult(checks=(), rounds=0),
            standards=tuple(
                dict.fromkeys(c.standard_ref for c in understood.concerns if c.standard_ref)
            ),
        )
