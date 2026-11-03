"""Common-sense (metamorphic) tests for a model-written check (Muse Code extensions, add-on 6).

Hand-written tests only check the scenes someone thought of. A metamorphic relation instead
changes a scene in a way that must not change a correct check's answer, or may change it only
one way, and compares the two answers on many generated scenes (`scenegen.generate`):

- **translate**: shift the whole scene, its lines and zones by a few pixels: same answer;
- **unrelated object**: add an object of a concept the check is not about: same answer;
- **reorder**: reverse the order of the objects: same answer;
- **scale**: multiply every pixel coordinate and `px_per_m` together (calibrated scenes only):
  same answer, so a calibrated check must measure in metres, not in fixed pixel margins;
- **monotone**, only when the spec declares it (`MonotoneRule`): e.g. moving every pallet up
  never turns an alert off.

Each transform is a pure function that returns a new sample, or None when the relation does not
apply (the shifted scene would leave the frame, the scene is uncalibrated, there is no object of
the concept); those samples are counted as skipped. Answers compare as:

- "same": both values equal; if either run errors, both must error (errors on the original
  samples are also counted on their own, and any error fails the report);
- "keeps_true": when the original answer is True, the transformed answer must be True.

`check_relations` runs every batch through a `CheckRunner`, which is
`extensions.sandbox.run_check` in production: the check never runs in this process. The first
violation of each relation is shrunk to a small counterexample (`scenegen.shrink`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from rulecam.extensions.scenegen import (
    CheckSpec,
    FailsBatch,
    SampleJSON,
    copy_sample,
    inside_frame,
    make_object,
    shift_points,
    shifted_object,
    shrink,
)

Expect = Literal["same", "keeps_true"]
Label = Literal["error", "unknown", "alert", "no alert"]
Direction = Literal["up", "down", "left", "right"]
Transform = Callable[[SampleJSON], SampleJSON | None]

BATCH_SIZE: Final = 1000
TRANSLATE_PX: Final = (23.0, 11.0)
SCALE: Final = 2.0
UNRELATED_BOX: Final = (4.0, 4.0, 36.0, 36.0)
_UNIT: Final[dict[Direction, tuple[float, float]]] = {
    "up": (0.0, -1.0),
    "down": (0.0, 1.0),
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
}


class ResultLike(Protocol):
    """The part of a check's result this module reads (`extensions.schema.CheckResult`)."""

    @property
    def value(self) -> bool | None:
        """The check's answer."""
        ...

    @property
    def why(self) -> str:
        """The check's short reason."""
        ...


class OutcomeLike(Protocol):
    """One sample's outcome (`extensions.sandbox.CheckOutcome`)."""

    @property
    def result(self) -> ResultLike | None:
        """The valid result, or None."""
        ...

    @property
    def error(self) -> str | None:
        """Why there is no result."""
        ...


class CheckRunner(Protocol):
    """Runs a check's source on a batch of samples (`extensions.sandbox.run_check`)."""

    def __call__(
        self, code: str, samples: Sequence[Mapping[str, object]], /
    ) -> Sequence[OutcomeLike]:
        """Return one outcome per sample, in order."""
        ...


class Answer(BaseModel):
    """A check's answer on one sample, or the error it raised.

    Attributes:
        value: The check's value (None when it could not tell).
        why: The check's short reason.
        error: Why there is no answer (the check raised or returned something invalid).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: bool | None = None
    why: str = ""
    error: str | None = None


def _answer(outcome: OutcomeLike) -> Answer:
    result = outcome.result
    if result is None:
        return Answer(error=outcome.error or "no result")
    return Answer(value=result.value, why=result.why)


def run_answers(
    runner: CheckRunner,
    code: str,
    samples: Sequence[SampleJSON],
    *,
    batch_size: int = BATCH_SIZE,
) -> list[Answer]:
    """Run a check on samples in batches and return one answer per sample.

    Args:
        runner: The sandbox runner.
        code: The check's source.
        samples: The samples.
        batch_size: Samples per runner call (each call is one child process).

    Returns:
        The answers, in order.

    Raises:
        ValueError: If the runner returns a different number of outcomes than samples.
    """
    answers: list[Answer] = []
    for start in range(0, len(samples), batch_size):
        batch = samples[start : start + batch_size]
        outcomes = runner(code, batch)
        if len(outcomes) != len(batch):
            msg = f"runner returned {len(outcomes)} outcomes for {len(batch)} samples"
            raise ValueError(msg)
        answers.extend(_answer(outcome) for outcome in outcomes)
    return answers


def agrees(before: Answer, after: Answer, expect: Expect) -> bool:
    """Whether two answers respect a relation's expectation.

    Args:
        before: The answer on the original sample.
        after: The answer on the transformed sample.
        expect: "same" or "keeps_true".

    Returns:
        True when the relation holds.
    """
    if expect == "keeps_true":
        if before.error is not None or before.value is not True:
            return True
        return after.error is None and after.value is True
    if before.error is not None or after.error is not None:
        return before.error is not None and after.error is not None
    return before.value == after.value


def answer_label(answer: Answer) -> Label:
    """A one-word reading of an answer, used to keep a failure's kind while shrinking.

    Args:
        answer: The answer.

    Returns:
        "error", "unknown" (value None), "alert" or "no alert".
    """
    if answer.error is not None:
        return "error"
    if answer.value is None:
        return "unknown"
    return "alert" if answer.value else "no alert"


# --- transforms (pure) ----------------------------------------------------------------------


def translated(sample: SampleJSON, dx: float, dy: float) -> SampleJSON | None:
    """The whole scene, its zones and lines moved by `(dx, dy)` pixels.

    Args:
        sample: The sample.
        dx: Pixels to the right.
        dy: Pixels down.

    Returns:
        The moved sample, or None when it would leave the frame.
    """
    out = copy_sample(sample)
    ppm = sample["px_per_m"]
    out["objects"] = [shifted_object(obj, dx, dy, ppm) for obj in sample["objects"]]
    out["zones"] = {name: shift_points(poly, dx, dy) for name, poly in sample["zones"].items()}
    out["lines"] = {name: shift_points(line, dx, dy) for name, line in sample["lines"].items()}
    return out if inside_frame(out) else None


def with_unrelated(sample: SampleJSON, concept: str) -> SampleJSON | None:
    """The scene with one small object of `concept` added in the top-left corner.

    Args:
        sample: The sample.
        concept: A concept the check is not about.

    Returns:
        The new sample, or None when the corner box does not fit the frame.
    """
    out = copy_sample(sample)
    out["objects"].append(make_object(concept, UNRELATED_BOX, px_per_m=sample["px_per_m"]))
    return out if inside_frame(out) else None


def reversed_objects(sample: SampleJSON) -> SampleJSON | None:
    """The scene with its objects in reverse order.

    Args:
        sample: The sample.

    Returns:
        The new sample, or None when it has fewer than two objects.
    """
    if len(sample["objects"]) < 2:  # noqa: PLR2004 - order needs two objects
        return None
    out = copy_sample(sample)
    out["objects"].reverse()
    return out


def scaled(sample: SampleJSON, k: float) -> SampleJSON | None:
    """Every pixel coordinate, the frame and `px_per_m` multiplied by `k` (areas by k squared).

    Ground points stay where they are: the same scene, filmed at a higher resolution.

    Args:
        sample: The sample.
        k: The factor.

    Returns:
        The scaled sample, or None when the sample is not calibrated.
    """
    ppm = sample["px_per_m"]
    if ppm is None:
        return None
    out = copy_sample(sample)
    out["px_per_m"] = ppm * k
    width, height = sample["frame_w"], sample["frame_h"]
    out["frame_w"] = None if width is None else round(width * k)
    out["frame_h"] = None if height is None else round(height * k)
    for obj in out["objects"]:
        obj["box_xyxy_px"] = [v * k for v in obj["box_xyxy_px"]]
        obj["mask_top_y_px"] *= k
        obj["mask_area_px"] *= k * k
    out["zones"] = {name: [[x * k, y * k] for x, y in poly] for name, poly in out["zones"].items()}
    out["lines"] = {name: [[x * k, y * k] for x, y in line] for name, line in out["lines"].items()}
    return out


def moved_concept(sample: SampleJSON, concept: str, dx: float, dy: float) -> SampleJSON | None:
    """The scene with every object of `concept` moved by `(dx, dy)` pixels.

    Args:
        sample: The sample.
        concept: Whose objects move.
        dx: Pixels to the right.
        dy: Pixels down.

    Returns:
        The new sample, or None when there is no such object or one would leave the frame.
    """
    if not any(obj["concept"] == concept for obj in sample["objects"]):
        return None
    out = copy_sample(sample)
    ppm = sample["px_per_m"]
    out["objects"] = [
        shifted_object(obj, dx, dy, ppm) if obj["concept"] == concept else obj
        for obj in out["objects"]
    ]
    return out if inside_frame(out) else None


# --- relations ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Relation:
    """A metamorphic relation: a transform and what it may do to the answer.

    Attributes:
        name: Short id, e.g. "translate" or "monotone:pallet:up".
        description: One plain sentence for the report.
        expect: "same" or "keeps_true".
        transform: Pure; returns None where the relation does not apply.
    """

    name: str
    description: str
    expect: Expect
    transform: Transform


class MonotoneRule(BaseModel):
    """A direction the spec declares can only make an alert more likely.

    Attributes:
        concept: Whose objects move.
        direction: Which way they move.
        step_px: How far, in pixels.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    concept: str
    direction: Direction
    step_px: float = Field(default=10.0, gt=0)


def translation(dx: float = TRANSLATE_PX[0], dy: float = TRANSLATE_PX[1]) -> Relation:
    """Shift the whole scene; tries the four sign combinations until one fits the frame.

    Args:
        dx: Pixels to the right.
        dy: Pixels down.

    Returns:
        The relation.
    """

    def transform(sample: SampleJSON) -> SampleJSON | None:
        """The first shift of the four sign combinations that keeps the scene in the frame."""
        for sx, sy in ((dx, dy), (-dx, -dy), (dx, -dy), (-dx, dy)):
            moved = translated(sample, sx, sy)
            if moved is not None:
                return moved
        return None

    return Relation(
        name="translate",
        description=(
            f"Shift the whole scene, its lines and zones by {dx:g}, {dy:g} px: "
            "the answer must not change."
        ),
        expect="same",
        transform=transform,
    )


def unrelated_object(concept: str) -> Relation:
    """Add an object of a concept the check is not about.

    Args:
        concept: The unrelated concept.

    Returns:
        The relation.
    """
    return Relation(
        name="unrelated_object",
        description=f"Add a {concept} the check is not about: the answer must not change.",
        expect="same",
        transform=lambda sample: with_unrelated(sample, concept),
    )


def reordering() -> Relation:
    """Reverse the order of the objects.

    Returns:
        The relation.
    """
    return Relation(
        name="reorder",
        description="Reverse the order of the objects: the answer must not change.",
        expect="same",
        transform=reversed_objects,
    )


def scaling(k: float = SCALE) -> Relation:
    """Film the same calibrated scene at `k` times the resolution.

    Args:
        k: The factor.

    Returns:
        The relation.
    """
    return Relation(
        name="scale",
        description=(
            f"Multiply every pixel coordinate and the pixels per metre by {k:g}: the answer "
            "must not change (measure in metres, not fixed pixel margins)."
        ),
        expect="same",
        transform=lambda sample: scaled(sample, k),
    )


def monotone(rule: MonotoneRule) -> Relation:
    """Move every object of a concept one way; an alert must not turn off.

    Args:
        rule: The declared direction.

    Returns:
        The relation.
    """
    ux, uy = _UNIT[rule.direction]
    dx, dy = ux * rule.step_px, uy * rule.step_px
    return Relation(
        name=f"monotone:{rule.concept}:{rule.direction}",
        description=(
            f"Move every {rule.concept} {rule.step_px:g} px {rule.direction}: "
            "an alert must not turn off."
        ),
        expect="keeps_true",
        transform=lambda sample: moved_concept(sample, rule.concept, dx, dy),
    )


def default_relations(
    spec: CheckSpec, monotone_rules: Sequence[MonotoneRule] = ()
) -> list[Relation]:
    """The relations every check must respect, plus the spec's declared monotone directions.

    Args:
        spec: The check spec (its first unrelated concept is the one added).
        monotone_rules: Directions the owner's request makes monotone, if any.

    Returns:
        The relations, in report order.
    """
    relations = [translation()]
    if spec.unrelated_concepts:
        relations.append(unrelated_object(spec.unrelated_concepts[0]))
    relations.extend([reordering(), scaling()])
    relations.extend(monotone(rule) for rule in monotone_rules)
    return relations


# --- running --------------------------------------------------------------------------------


class Counterexample(BaseModel):
    """A sample on which a relation fails, shrunk when possible.

    Attributes:
        sample: The original sample.
        transformed: The relation's transform of it.
        before: The check's answer on `sample`.
        after: The check's answer on `transformed`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sample: SampleJSON
    transformed: SampleJSON
    before: Answer
    after: Answer


class RelationResult(BaseModel):
    """One relation's outcome over the samples.

    Attributes:
        name: The relation's id.
        description: Its plain sentence.
        expect: "same" or "keeps_true".
        checked: Samples the relation applied to.
        skipped: Samples it did not apply to.
        violations: Checked samples on which it failed.
        passed: True when there were no violations.
        counterexample: The first violation, shrunk.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    expect: Expect
    checked: int
    skipped: int
    violations: int
    passed: bool
    counterexample: Counterexample | None = None


class MetamorphicReport(BaseModel):
    """Every relation's outcome for one check.

    Attributes:
        samples: How many samples were generated.
        errors: Original samples on which the check raised or answered invalidly.
        relations: One result per relation.
        passed: True when every relation passed and the check never errored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    samples: int
    errors: int
    relations: tuple[RelationResult, ...]
    passed: bool


def violations_of(
    code: str,
    relation: Relation,
    runner: CheckRunner,
    *,
    labels: tuple[Label, Label] | None = None,
) -> FailsBatch:
    """A batch judge for `scenegen.shrink`: True where the relation still fails.

    Args:
        code: The check's source.
        relation: The relation.
        runner: The sandbox runner.
        labels: When given, the (before, after) answer labels a candidate must keep, so the
            shrunk counterexample shows the same kind of failure (not, say, a crash instead).

    Returns:
        A function judging a batch of candidates with one runner pass.
    """

    def fails(candidates: list[SampleJSON]) -> list[bool]:
        """Run each candidate and its transform together; True where the relation breaks."""
        transformed = [relation.transform(c) for c in candidates]
        live = [(c, t) for c, t in zip(candidates, transformed, strict=True) if t is not None]
        answers = run_answers(runner, code, [c for c, _ in live] + [t for _, t in live])
        pairs = iter(zip(answers[: len(live)], answers[len(live) :], strict=True))
        verdicts: list[bool] = []
        for t in transformed:
            if t is None:
                verdicts.append(False)
                continue
            before, after = next(pairs)
            kept = labels is None or (answer_label(before), answer_label(after)) == labels
            verdicts.append(kept and not agrees(before, after, relation.expect))
        return verdicts

    return fails


def _shrunk(
    code: str, example: Counterexample, relation: Relation, runner: CheckRunner
) -> Counterexample:
    labels = (answer_label(example.before), answer_label(example.after))
    small = shrink(example.sample, violations_of(code, relation, runner, labels=labels))
    transformed = relation.transform(small)
    if transformed is None:
        return example
    before, after = run_answers(runner, code, [small, transformed])
    return Counterexample(sample=small, transformed=transformed, before=before, after=after)


def _check_one(
    code: str,
    samples: Sequence[SampleJSON],
    before: Sequence[Answer],
    relation: Relation,
    runner: CheckRunner,
    *,
    shrink_failures: bool,
) -> RelationResult:
    pairs = [(i, t) for i, s in enumerate(samples) if (t := relation.transform(s)) is not None]
    after = run_answers(runner, code, [t for _, t in pairs])
    bad = [
        (i, t, answer)
        for (i, t), answer in zip(pairs, after, strict=True)
        if not agrees(before[i], answer, relation.expect)
    ]
    example = None
    if bad:
        index, transformed, answer = bad[0]
        example = Counterexample(
            sample=samples[index], transformed=transformed, before=before[index], after=answer
        )
        if shrink_failures:
            example = _shrunk(code, example, relation, runner)
    return RelationResult(
        name=relation.name,
        description=relation.description,
        expect=relation.expect,
        checked=len(pairs),
        skipped=len(samples) - len(pairs),
        violations=len(bad),
        passed=not bad,
        counterexample=example,
    )


def check_relations(
    code: str,
    samples: Sequence[SampleJSON],
    relations: Sequence[Relation],
    runner: CheckRunner,
    *,
    shrink_failures: bool = True,
) -> MetamorphicReport:
    """Test a check against metamorphic relations on generated samples.

    Args:
        code: The check's source (validated and run by `runner`, never here).
        samples: Generated samples (`scenegen.generate`).
        relations: The relations (`default_relations`).
        runner: The sandbox runner (`extensions.sandbox.run_check`).
        shrink_failures: Shrink each relation's first counterexample.

    Returns:
        The report; `passed` only when every relation holds and the check never errors.
    """
    before = run_answers(runner, code, samples)
    results = tuple(
        _check_one(code, samples, before, relation, runner, shrink_failures=shrink_failures)
        for relation in relations
    )
    errors = sum(1 for answer in before if answer.error is not None)
    return MetamorphicReport(
        samples=len(samples),
        errors=errors,
        relations=results,
        passed=errors == 0 and all(result.passed for result in results),
    )
