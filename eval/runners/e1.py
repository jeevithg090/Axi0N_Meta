"""Evaluation runner E1: detection accuracy with ablations and baselines (P4-06, MP §8 E1).

E1 asks a narrow question: on the consented clips of the E1 manifest, how often does a rule fire
when it should, and how often when it should not. It reports per rule and per use case, with
**Wilson 95% intervals**, plus false alerts per hour on the negative clips.

What the numbers do and do not mean (MP §8):

* The interval is a **binomial** interval over labelled episodes. It accounts for how few clips
  we have, and for nothing else: not for the clips being ours, not for staging, not for one site.
* Every arm runs the clip set **five times**. The spread over those runs is **stability**, not
  accuracy. Five runs of the same clip tell you whether the pipeline is deterministic; they add
  no independent evidence about the world, so repeats are never summed into the counts.
* An **outsider** clip is the only honest generalisation evidence, so the report counts outsider
  clips separately and never pools them with our own staged clips.

Arms:

* `predicates` — the rule engine's deterministic predicates alone;
* `glimmer` — predicates plus the Tier 1 verifier;
* `spark_open` — predicates plus Tier 1 plus the Tier 2 verifier;
* `yolov8_ppe` — the YOLOv8 PPE baseline. AGPL-3.0, from the `eval` extra only: no runtime code
  may import it, and this runner only ever receives its *outputs* through `ArmOutcome`;
* `frame_vlm` — the frame-sampling baseline, where the model is asked the rule every 5 s on
  single frames ("Llama CCTV Operator" style).

This module holds the manifest-to-report arithmetic and is pure: it takes outcomes and returns
tables. Playing the clips is the job of an `ArmRunner`, so the aggregation is tested without a
network or a GPU. No real `ArmRunner` exists yet, which is why `simulated` defaults to True: a
caller with a runner backed by the real pipeline must pass `simulated=False` on purpose.
"""

from __future__ import annotations

import functools
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from rulecam.eval.manifests import E1Manifest, ManifestEntry, StagedBy, UseCase
from rulecam.log import get_logger
from rulecam.stats.intervals import wilson_interval

_log = get_logger(__name__)

REQUIRED_RUNS: Final = 5
FRAME_SAMPLE_S: Final = 5.0
CONFIDENCE: Final = 0.95
SECONDS_PER_HOUR: Final = 3600.0

Arm = Literal["predicates", "glimmer", "spark_open", "yolov8_ppe", "frame_vlm"]
ABLATION_ARMS: Final[tuple[Arm, ...]] = ("predicates", "glimmer", "spark_open")
BASELINE_ARMS: Final[tuple[Arm, ...]] = ("yolov8_ppe", "frame_vlm")
ALL_ARMS: Final[tuple[Arm, ...]] = ABLATION_ARMS + BASELINE_ARMS


class ArmOutcome(BaseModel):
    """What one arm did on one clip in one run.

    Counts are over labelled episodes, matched by temporal IoU upstream
    (`rulecam.eval.metrics.match_events`), so this module never re-matches anything.

    Attributes:
        arm: Which arm produced this.
        run_idx: Which of the repeats this is, from 0.
        clip: The eval-set clip name.
        rule_id: The rule measured.
        tp: Labelled episodes the arm alerted on.
        fp: Alerts that matched no labelled episode.
        fn: Labelled episodes with no alert.
        alerts: Total alerts raised, used for the false-alert rate on negatives.
        clip_seconds: Clip length, for the per-hour rate.
        error: Why the clip could not be scored, if it could not.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    arm: Arm
    run_idx: int = Field(ge=0)
    clip: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    tp: int = Field(default=0, ge=0)
    fp: int = Field(default=0, ge=0)
    fn: int = Field(default=0, ge=0)
    alerts: int = Field(default=0, ge=0)
    clip_seconds: float = Field(default=0.0, ge=0)
    error: str | None = None

    @property
    def scored(self) -> bool:
        """Whether this outcome carries usable counts."""
        return self.error is None


class ArmRunner(Protocol):
    """Anything that can play the E1 clip set once for one arm.

    No implementation backed by the real pipeline exists yet; the tests pass a deterministic
    fake. One that does must also be run with `simulated=False`.
    """

    def run(self, arm: Arm, entries: Sequence[ManifestEntry], run_idx: int) -> Sequence[ArmOutcome]:
        """Play every entry once for `arm` and return one outcome per entry."""
        ...


class Rate(BaseModel):
    """A proportion with its Wilson interval and the counts behind it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float | None
    lo: float
    hi: float
    successes: int = Field(ge=0)
    total: int = Field(ge=0)

    @property
    def known(self) -> bool:
        """Whether there were any trials at all."""
        return self.total > 0


def rate(successes: int, total: int, *, confidence: float = CONFIDENCE) -> Rate:
    """Build a `Rate` from counts, with no value and a full interval when there are no trials."""
    lo, hi = wilson_interval(successes, total, confidence)
    return Rate(
        value=None if total == 0 else successes / total,
        lo=lo,
        hi=hi,
        successes=successes,
        total=total,
    )


class ArmScore(BaseModel):
    """One arm's accuracy on one slice of the manifest (a rule, or a use case).

    `runs` is how many repeats produced these counts; the counts themselves come from the
    **first** scored run of each clip, never summed over repeats. `stable` says whether every
    repeat produced the same counts, which is what repeats are evidence for.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    arm: Arm
    slice_kind: Literal["rule", "use_case", "staged_by", "all"]
    slice_id: str
    clips: int = Field(ge=0)
    runs: int = Field(ge=0)
    tp: int = Field(ge=0)
    fp: int = Field(ge=0)
    fn: int = Field(ge=0)
    precision: Rate
    recall: Rate
    false_alerts: int = Field(ge=0)
    negative_seconds: float = Field(ge=0)
    false_alerts_per_hour: float | None = None
    stable: bool = True
    errors: tuple[str, ...] = ()


class E1Summary(BaseModel):
    """Everything E1 measured, ready for `eval/report.py`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest: str
    runs_per_arm: int = Field(ge=0)
    arms: tuple[Arm, ...]
    by_rule: tuple[ArmScore, ...]
    by_use_case: tuple[ArmScore, ...]
    by_staged_by: tuple[ArmScore, ...]
    overall: tuple[ArmScore, ...]
    simulated: bool = False
    warnings: tuple[str, ...] = ()

    def score(self, arm: Arm, slice_kind: str, slice_id: str) -> ArmScore | None:
        """Return one score, or `None` if that slice was not measured."""
        rows = (*self.by_rule, *self.by_use_case, *self.by_staged_by, *self.overall)
        for row in rows:
            if row.arm == arm and row.slice_kind == slice_kind and row.slice_id == slice_id:
                return row
        return None

    def headline(self, arm: Arm = "spark_open") -> dict[str, float | None]:
        """The three numbers for the video (MP §12): recall, precision, false alerts per hour."""
        row = self.score(arm, "all", "all")
        if row is None:
            return {"recall": None, "precision": None, "false_alerts_per_hour": None}
        return {
            "recall": row.recall.value,
            "precision": row.precision.value,
            "false_alerts_per_hour": row.false_alerts_per_hour,
        }


def _counts(rows: Sequence[ArmOutcome]) -> tuple[int, int, int]:
    return (sum(r.tp for r in rows), sum(r.fp for r in rows), sum(r.fn for r in rows))


def _first_runs(outcomes: Sequence[ArmOutcome]) -> list[ArmOutcome]:
    """One outcome per (clip, rule): the earliest scored run, so repeats are never summed."""
    best: dict[tuple[str, str], ArmOutcome] = {}
    for row in sorted(outcomes, key=lambda r: r.run_idx):
        key = (row.clip, row.rule_id)
        if row.scored and key not in best:
            best[key] = row
    return [best[k] for k in sorted(best)]


def _is_stable(outcomes: Sequence[ArmOutcome]) -> bool:
    """Whether every repeat of every clip produced the same counts."""
    seen: dict[tuple[str, str], set[tuple[int, int, int]]] = defaultdict(set)
    for row in outcomes:
        if row.scored:
            seen[(row.clip, row.rule_id)].add((row.tp, row.fp, row.fn))
    return all(len(v) <= 1 for v in seen.values())


def _slice_score(
    arm: Arm,
    slice_kind: Literal["rule", "use_case", "staged_by", "all"],
    slice_id: str,
    outcomes: Sequence[ArmOutcome],
    *,
    labels: Mapping[tuple[str, str], str],
    runs: int,
) -> ArmScore:
    scored = _first_runs(outcomes)
    tp, fp, fn = _counts(scored)
    negatives = [r for r in scored if labels[(r.clip, r.rule_id)] == "negative"]
    false_alerts = sum(r.alerts for r in negatives)
    # Durations come from the same scored run as the counts: an errored run may carry 0 s.
    negative_seconds = sum(r.clip_seconds for r in negatives)
    per_hour = (
        false_alerts / (negative_seconds / SECONDS_PER_HOUR) if negative_seconds > 0 else None
    )
    return ArmScore(
        arm=arm,
        slice_kind=slice_kind,
        slice_id=slice_id,
        clips=len(scored),
        runs=runs,
        tp=tp,
        fp=fp,
        fn=fn,
        precision=rate(tp, tp + fp),
        recall=rate(tp, tp + fn),
        false_alerts=false_alerts,
        negative_seconds=negative_seconds,
        false_alerts_per_hour=per_hour,
        stable=_is_stable(outcomes),
        errors=tuple(sorted({r.error for r in outcomes if r.error})),
    )


def aggregate_e1(
    manifest: E1Manifest,
    outcomes: Sequence[ArmOutcome],
    *,
    runs_per_arm: int = REQUIRED_RUNS,
    simulated: bool = True,
) -> E1Summary:
    """Turn raw per-clip outcomes into the E1 tables.

    Only outcomes for a `(clip, rule)` pair in the manifest are counted: the manifest is the
    consent gate, so an outcome for any other pair is dropped and a warning says how many.

    Args:
        manifest: The consented manifest the run used.
        outcomes: Every outcome of every arm and repeat.
        runs_per_arm: How many repeats each arm was asked for.
        simulated: True unless every arm's outcomes came from the real pipeline. A simulated
            summary can never be quoted as an E1 result, so a real run must pass False.

    Returns:
        The summary, with per-rule, per-use-case, per-staged-by and overall rows per arm.
    """
    labels = {(e.clip_name, e.rule_id): e.label for e in manifest.entries}
    known = [o for o in outcomes if (o.clip, o.rule_id) in labels]
    dropped = len(outcomes) - len(known)
    use_case: dict[tuple[str, str], UseCase] = {
        (e.clip_name, e.rule_id): e.use_case for e in manifest.entries
    }
    staged: dict[tuple[str, str], StagedBy] = {
        (e.clip_name, e.rule_id): e.staged_by for e in manifest.entries
    }

    arms = tuple(a for a in ALL_ARMS if any(o.arm == a for o in known))
    by_rule: list[ArmScore] = []
    by_use_case: list[ArmScore] = []
    by_staged: list[ArmScore] = []
    overall: list[ArmScore] = []
    warnings: list[str] = []
    if dropped:
        warnings.append(
            f"{dropped} outcome(s) for clip/rule pairs outside the manifest were not counted"
        )

    for arm in arms:
        mine = [o for o in known if o.arm == arm]
        got = len({o.run_idx for o in mine})
        if got < runs_per_arm:
            warnings.append(
                f"{arm}: {got} of {runs_per_arm} runs present, so stability is not established"
            )
        score = functools.partial(_slice_score, labels=labels, runs=got)
        for rule_id in sorted({o.rule_id for o in mine}):
            rows = [o for o in mine if o.rule_id == rule_id]
            by_rule.append(score(arm, "rule", rule_id, rows))
        for uc in sorted({use_case[(o.clip, o.rule_id)] for o in mine}):
            rows = [o for o in mine if use_case[(o.clip, o.rule_id)] == uc]
            by_use_case.append(score(arm, "use_case", uc, rows))
        for who in sorted({staged[(o.clip, o.rule_id)] for o in mine}):
            rows = [o for o in mine if staged[(o.clip, o.rule_id)] == who]
            by_staged.append(score(arm, "staged_by", who, rows))
        overall.append(score(arm, "all", "all", mine))

    unstable = sorted({f"{r.arm}/{r.slice_id}" for r in overall if not r.stable})
    if unstable:
        warnings.append(f"not deterministic over repeats: {', '.join(unstable)}")
    if not any(staged[(o.clip, o.rule_id)] == "outsider" for o in known):
        warnings.append("every clip was staged by the team, so E1 shows no generalisation")

    return E1Summary(
        manifest=manifest.name,
        runs_per_arm=runs_per_arm,
        arms=arms,
        by_rule=tuple(by_rule),
        by_use_case=tuple(by_use_case),
        by_staged_by=tuple(by_staged),
        overall=tuple(overall),
        simulated=simulated,
        warnings=tuple(warnings),
    )


def run_e1_evaluation(
    manifest: E1Manifest,
    runner: ArmRunner,
    *,
    arms: Sequence[Arm] = ALL_ARMS,
    runs_per_arm: int = REQUIRED_RUNS,
    simulated: bool = True,
) -> E1Summary:
    """Play the manifest `runs_per_arm` times for each arm and aggregate the result.

    The clip order is the manifest's own order and the run index counts up, so the same runner
    gives the same summary every time.

    Args:
        manifest: A consented manifest (`rulecam.eval.manifests.load_manifest`).
        runner: Plays one arm's clip set once.
        arms: Which arms to run, in order.
        runs_per_arm: Repeats per arm; five is the reporting standard.
        simulated: Passed through to the summary. True unless `runner` plays the clips through
            the real pipeline; a real run must pass False explicitly.

    Returns:
        The E1 summary.
    """
    outcomes: list[ArmOutcome] = []
    for arm in arms:
        for run_idx in range(runs_per_arm):
            produced = runner.run(arm, manifest.entries, run_idx)
            outcomes.extend(produced)
            _log.info(
                "e1.arm.run", arm=arm, run_idx=run_idx, clips=len(produced), manifest=manifest.name
            )
    return aggregate_e1(manifest, outcomes, runs_per_arm=runs_per_arm, simulated=simulated)
