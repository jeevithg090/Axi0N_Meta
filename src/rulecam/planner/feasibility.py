"""The feasibility gauge: can this site's cameras check each concern? (task P2-04, MP §3.2).

Verdict rules, applied per concern in this order:

1. **declined**: the guardrails (P2-03, `check_request`) decline the concern.
2. **not_observable**: no required concept was named, or a required concept has no probe on the
   chosen camera, or its Wilson lower bound is below 0.5. The verdict carries an alternative.
3. **caveat**: the weakest required concept has `0.5 <= detect_lb < 0.8`. The verdict carries
   the cheapest camera fix, proposed by Spark from the probe (pixel height), with a
   deterministic fallback when Spark is not given or fails.
4. **observable**: every required concept has `detect_lb >= 0.8`.

A rule is evaluated on one camera, so all required concepts must be seen by the same camera.
The chosen camera is the one that covers the most required concepts, then has the highest
weakest lower bound, then the smallest id. `says` is always a fixed first-person template; model
text only ever goes into `alternative`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Final

from pydantic import BaseModel, Field

from rulecam.clients.errors import ModelClientError
from rulecam.contracts import StructuredLLM
from rulecam.contracts.plan import FeasibilityVerdict, Probe
from rulecam.log import get_logger
from rulecam.planner.guardrails import Declined, check_request
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE
from rulecam.planner.steps.survey import clean_concepts

_log = get_logger(__name__)

FIX_MODEL: Final = "muse-spark-1.3"
OBSERVABLE_LB: Final = 0.80
CAVEAT_LB: Final = 0.50
SMALL_PX_HEIGHT: Final = 30.0
MAX_FIX_CHARS: Final = 200

CAMERA_FIX_SYSTEM_PROMPT = (
    "You are Rulecam's camera placement engineer. You get one detectability probe as JSON: a "
    "concept, its camera, the Wilson lower bound of SAM's detection rate and the median object "
    "height in pixels. Propose the single cheapest physical fix (move the camera closer, zoom, "
    "change the angle or the lighting) in one short sentence, e.g. 'Move cam2 about 0.5 m "
    "closer to the sink.'\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)


class CameraFix(BaseModel):
    """Spark's proposed camera fix."""

    fix: str = Field(description="One short sentence: the cheapest physical camera adjustment")


def _fallback_fix(probe: Probe) -> str:
    if probe.median_px_height < SMALL_PX_HEIGHT:
        return (
            f"Move {probe.camera_id} about 0.5 m closer or zoom in: the {probe.concept} is only "
            f"{probe.median_px_height:.0f} px tall."
        )
    return f"Adjust the angle or lighting of {probe.camera_id} so the {probe.concept} is clearer."


def cheapest_fix(probe: Probe, *, spark: StructuredLLM | None = None) -> str:
    """Propose the cheapest camera fix for a borderline probe.

    Args:
        probe: The weakest probe of a caveat verdict.
        spark: Proposes the fix; without it (or if the call fails) a template is used.

    Returns:
        One sentence.
    """
    if spark is None:
        return _fallback_fix(probe)
    try:
        result = spark.structured(
            model=FIX_MODEL,
            messages=[
                {"role": "system", "content": CAMERA_FIX_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(probe.model_dump(), sort_keys=True)},
            ],
            schema=CameraFix,
        )
    except ModelClientError as err:
        _log.warning("camera_fix_fallback", camera_id=probe.camera_id, error=str(err))
        return _fallback_fix(probe)
    fix = " ".join(result.fix.split())
    return fix[:MAX_FIX_CHARS] if fix else _fallback_fix(probe)


def _alternative(concept: str, concern: str) -> str:
    return f"Point a phone at the {concept}, or add a sensor or a manual log, to check {concern}."


def _choose_camera(
    concepts: Sequence[str], probes: Sequence[Probe]
) -> tuple[str, dict[str, Probe]] | None:
    """Pick the camera that best sees all `concepts`; `None` if no probe names any of them."""
    by_camera: dict[str, dict[str, Probe]] = defaultdict(dict)
    for probe in probes:
        concept = " ".join(probe.concept.split()).lower()
        if concept in concepts:
            best = by_camera[probe.camera_id].get(concept)
            if best is None or probe.detect_lb > best.detect_lb:
                by_camera[probe.camera_id][concept] = probe
    if not by_camera:
        return None

    def rank(cam_id: str) -> tuple[int, float, str]:
        seen = by_camera[cam_id]
        return (-len(seen), -min(p.detect_lb for p in seen.values()), cam_id)

    cam_id = min(by_camera, key=rank)
    return cam_id, by_camera[cam_id]


def _not_observable(
    concern: str, concept: str, says: str, evidence: Sequence[Probe]
) -> FeasibilityVerdict:
    return FeasibilityVerdict(
        concern=concern,
        verdict="not_observable",
        says=says,
        evidence=tuple(evidence),
        alternative=_alternative(concept, concern),
    )


def _grade(
    concern: str,
    concepts: Sequence[str],
    cam_id: str,
    seen: Mapping[str, Probe],
    spark: StructuredLLM | None,
) -> FeasibilityVerdict:
    evidence = tuple(seen[c] for c in concepts if c in seen)
    missing = [c for c in concepts if c not in seen]
    if missing:
        says = f"I can't see a {missing[0]}, so I can't check {concern}."
        return _not_observable(concern, missing[0], says, evidence)
    weakest = min(evidence, key=lambda p: p.detect_lb)
    if weakest.detect_lb < CAVEAT_LB:
        says = (
            f"I can't see the {weakest.concept} reliably (lower bound {weakest.detect_lb:.2f}), "
            f"so I can't check {concern}."
        )
        return _not_observable(concern, weakest.concept, says, evidence)
    if weakest.detect_lb < OBSERVABLE_LB:
        return FeasibilityVerdict(
            concern=concern,
            verdict="caveat",
            says=(
                f"I can see the {weakest.concept} on {cam_id}, but not reliably enough yet "
                f"(lower bound {weakest.detect_lb:.2f}), so I can check {concern} with a caveat."
            ),
            evidence=evidence,
            alternative=cheapest_fix(weakest, spark=spark),
        )
    return FeasibilityVerdict(
        concern=concern,
        verdict="observable",
        says=f"I can see the {', '.join(concepts)} on {cam_id}, so I can check {concern}.",
        evidence=evidence,
    )


def evaluate_concern_feasibility(
    concern: str,
    required_concepts: Sequence[str],
    probes: Sequence[Probe],
    *,
    spark: StructuredLLM | None = None,
    declined: Declined | None = None,
) -> FeasibilityVerdict:
    """Grade one concern against the probes.

    Args:
        concern: The concern text.
        required_concepts: Concepts a rule for this concern must see.
        probes: Probes for those concepts across cameras.
        spark: Runs the model guardrail pass and proposes camera fixes.
        declined: A decline P2-03 already made for this concern, if any.

    Returns:
        The verdict.

    Raises:
        ModelClientError: If the guardrail model pass fails (guardrails fail closed).
    """
    if declined is None:
        guardrail = check_request(concern, spark=spark)
        declined = guardrail if isinstance(guardrail, Declined) else None
    if declined is not None:
        return declined.to_feasibility_verdict(concern)
    concepts = clean_concepts(required_concepts)
    if not concepts:
        return FeasibilityVerdict(
            concern=concern,
            verdict="not_observable",
            says=f"I don't know what to look for to check {concern}.",
            alternative="Name the objects or stations a camera would have to see.",
        )
    chosen = _choose_camera(concepts, probes)
    if chosen is None:
        says = f"I can't see a {concepts[0]}, so I can't check {concern}."
        return _not_observable(concern, concepts[0], says, ())
    return _grade(concern, concepts, *chosen, spark)


def gauge_plan_feasibility(
    concern_requirements: Mapping[str, Sequence[str]],
    probes: Sequence[Probe],
    *,
    spark: StructuredLLM | None = None,
) -> tuple[FeasibilityVerdict, ...]:
    """Grade every concern.

    Args:
        concern_requirements: Concern text -> the concepts it needs.
        probes: All probes.
        spark: Runs the model guardrail pass and proposes camera fixes.

    Returns:
        One verdict per concern, in mapping order.
    """
    return tuple(
        evaluate_concern_feasibility(concern, concepts, probes, spark=spark)
        for concern, concepts in concern_requirements.items()
    )
