"""The add-on proof steps a live request runs after the default ones (Muse Code add-ons 6 and 7).

`proof.DEFAULT_STEPS` try examples. These two steps check every generated check further, with the
owner's real reference lines and frame:

- `common_sense` (`metamorphic.check_relations`): shift the whole scene, add an unrelated object,
  reorder the objects, film the same calibrated scene at twice the resolution and, when the
  request says above or below, move the objects that way. A correct check keeps its answer (or,
  for the move, keeps an alert on). Any violation, or any error on a generated scene, fails.
- `math_proof` (`smt.prove_check`, Z3): no target object never alerts, the check never raises,
  translation never changes the answer and, for above/below requests, raising (or lowering) the
  object never turns an alert off, proved for every sample with up to `max_objects` objects. A
  counterexample that a concrete sandbox run reproduces fails the step; when nothing is
  disproved but something could not be decided (unsupported Python, solver timeout) the step is
  `skipped` with the reason, which never counts as a pass.

`proof_steps(...)` is the list the service uses: the defaults, then these two.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from rulecam.extensions import metamorphic, scenegen, smt
from rulecam.extensions.generate import CheckSpec
from rulecam.extensions.proof import DEFAULT_STEPS, ProofContext, ProofStep, StepResult
from rulecam.extensions.sample import Line
from rulecam.extensions.sandbox import SampleOutcome
from rulecam.extensions.smt_sample import MonotoneSpec, ProofSpec

DEFAULT_FRAME: Final = (1280, 720)
SCENE_PX_PER_M: Final = 100.0
RANDOM_SCENES: Final = 150
MATH_MAX_OBJECTS: Final = 2
MATH_TIMEOUT_MS: Final = 4000
_DETAIL_CHARS: Final = 400


def _frame(lines: Mapping[str, Line], frame: tuple[int, int] | None) -> tuple[int, int]:
    """The frame the scenes are drawn in: the camera's, else one that holds every line."""
    if frame is not None:
        return frame
    xs = [p[0] for line in lines.values() for p in line]
    ys = [p[1] for line in lines.values() for p in line]
    return (
        max(DEFAULT_FRAME[0], int(max(xs, default=0)) + 1),
        max(DEFAULT_FRAME[1], int(max(ys, default=0)) + 1),
    )


Run = Callable[[str, Sequence[Mapping[str, object]]], list[SampleOutcome]]


def _runner(ctx: ProofContext) -> Run:
    """The context's sandbox runner, typed for the add-on modules."""

    def run(code: str, samples: Sequence[Mapping[str, object]], /) -> list[SampleOutcome]:
        return ctx.runner(code, [dict(sample) for sample in samples])

    return run


@dataclass(frozen=True)
class CommonSenseStep:
    """Metamorphic relations on generated scenes drawn with the owner's lines."""

    lines: Mapping[str, Line] = field(default_factory=dict)
    frame: tuple[int, int] | None = None
    n_random: int = RANDOM_SCENES
    name: str = "common_sense"

    def run(self, ctx: ProofContext) -> StepResult:
        """See `ProofStep.run`."""
        spec = ctx.spec
        width, height = _frame(self.lines, self.frame)
        unrelated = "traffic cone" if spec.concept != "traffic cone" else "bicycle"
        scene_spec = scenegen.CheckSpec(
            concepts=(spec.concept,),
            lines={name: self.lines[name] for name in spec.lines if name in self.lines},
            frame_w=width,
            frame_h=height,
            px_per_m=SCENE_PX_PER_M,
            unrelated_concepts=(unrelated,),
        )
        rules = (
            [metamorphic.MonotoneRule(concept=spec.concept, direction="up")]
            if spec.relation == "above"
            else [metamorphic.MonotoneRule(concept=spec.concept, direction="down")]
            if spec.relation == "below"
            else []
        )
        samples = scenegen.generate(scene_spec, n_random=self.n_random)
        report = metamorphic.check_relations(
            ctx.code, samples, metamorphic.default_relations(scene_spec, rules), _runner(ctx)
        )
        broken = [r for r in report.relations if not r.passed]
        held = sum(1 for r in report.relations if r.passed)
        detail = (
            f"{held} of {len(report.relations)} relations held on {report.samples} scenes"
            + (f"; {report.errors} scenes raised an error" if report.errors else "")
            + (
                "; broken: " + ", ".join(f"{r.name} ({r.violations} scenes)" for r in broken)
                if broken
                else ""
            )
        )
        return StepResult(
            name=self.name,
            status="passed" if report.passed else "failed",
            detail=detail[:_DETAIL_CHARS],
            metrics={
                "scenes": float(report.samples),
                "relations": float(len(report.relations)),
                "held": float(held),
                "errors": float(report.errors),
            },
        )


def _monotone(spec: CheckSpec) -> MonotoneSpec | None:
    """Raising (above) or lowering (below) one object never turns an alert off."""
    if spec.relation is None:
        return None
    return MonotoneSpec(
        fields=("y1", "y2", "mask_top_y"),
        direction="decrease" if spec.relation == "above" else "increase",
        label=f"{'raising' if spec.relation == 'above' else 'lowering'} the {spec.concept}",
    )


@dataclass(frozen=True)
class MathProofStep:
    """Z3 proves the check's properties for every sample with up to `max_objects` objects."""

    max_objects: int = MATH_MAX_OBJECTS
    timeout_ms: int = MATH_TIMEOUT_MS
    name: str = "math_proof"

    def run(self, ctx: ProofContext) -> StepResult:
        """See `ProofStep.run`."""
        spec = ctx.spec
        report = smt.prove_check(
            ctx.code,
            ProofSpec(
                target_concept=spec.concept,
                lines=spec.lines,
                max_objects=self.max_objects,
                monotone=_monotone(spec),
                timeout_ms=self.timeout_ms,
            ),
            runner=_runner(ctx),
        )
        counted = dict.fromkeys(smt.ProofStatus, 0)
        for prop in report.properties:
            counted[prop.status] += 1
        refuted = [
            p
            for p in report.properties
            if p.status is smt.ProofStatus.COUNTEREXAMPLE and p.confirmed is not False
        ]
        parts = [f"{p.name}: {p.status}" for p in report.properties]
        detail = f"Z3 {report.z3_version}, up to {report.max_objects} objects: " + "; ".join(parts)
        if refuted:
            detail += f". Broken: {refuted[0].detail}"
        proved = report.all_proved and bool(report.properties)
        status = "failed" if refuted else "passed" if proved else "skipped"
        return StepResult(
            name=self.name,
            status=status,
            detail=detail[:_DETAIL_CHARS],
            metrics={
                "proved": float(counted[smt.ProofStatus.PROVED]),
                "counterexamples": float(counted[smt.ProofStatus.COUNTEREXAMPLE]),
                "undecided": float(
                    counted[smt.ProofStatus.UNSUPPORTED] + counted[smt.ProofStatus.TIMEOUT]
                ),
            },
        )


def proof_steps(
    lines: Mapping[str, Line], frame: tuple[int, int] | None = None
) -> tuple[ProofStep, ...]:
    """The default steps, then the common-sense and math-proof steps for one request.

    Args:
        lines: The owner's reference lines, in pixels.
        frame: The camera's frame size, when known.

    Returns:
        The steps in report order.
    """
    return (*DEFAULT_STEPS, CommonSenseStep(lines=dict(lines), frame=frame), MathProofStep())
