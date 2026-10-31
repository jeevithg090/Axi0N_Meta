"""Feasibility gauge and verdict engine (task P2-04, MP §3.2, §5).

Evaluates whether user safety concerns can be reliably verified with available cameras:
- All required concepts have `detect_lb >= 0.8`: `observable`.
- Any required concept has `0.5 <= detect_lb < 0.8`: `caveat` (with cheapest camera fix).
- Any required concept has `detect_lb < 0.5` or is absent: `not_observable` (with an alternative).
- Declines from guardrail checks (P2-03) yield `declined`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field

from rulecam.clients.meta_hosted.spark import SPARK_1_3
from rulecam.contracts import StructuredLLM
from rulecam.contracts.plan import FeasibilityVerdict, Probe
from rulecam.log import get_logger
from rulecam.planner.guardrails import Declined, check_request
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE

_log = get_logger(__name__)

OBSERVABLE_THRESHOLD = 0.80
CAVEAT_THRESHOLD = 0.50
MIN_REASONABLE_PX_HEIGHT = 30.0

CAVEAT_SYSTEM_PROMPT = (
    "You are Rulecam's camera placement engineer. Given a camera detectability probe with "
    "borderline confidence or small pixel height, propose the cheapest, most practical physical "
    "camera fix (e.g. 'Move camera 2 about 0.5 m closer', 'Adjust angle downward by 15 deg'). "
    "Return a concise single-sentence recommendation.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)


class CameraFixProposal(BaseModel):
    """Camera repositioning or optical adjustment recommendation."""

    fix: str = Field(description="Actionable physical camera adjustment or zoom fix")


def _get_cheapest_fix(
    probe: Probe,
    *,
    spark: StructuredLLM | None = None,
) -> str:
    """Generate the cheapest camera adjustment fix for a caveat probe."""
    if spark is not None:
        user_prompt = (
            f"Concept '{probe.concept}' on camera '{probe.camera_id}' has detect_lb="
            f"{probe.detect_lb} and median pixel height={probe.median_px_height}px. "
            "Propose the cheapest camera fix."
        )
        try:
            res = spark.structured(
                model=SPARK_1_3,
                messages=[
                    {"role": "system", "content": CAVEAT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                schema=CameraFixProposal,
            )
            if res.fix.strip():
                return res.fix.strip()
        except Exception as err:
            _log.warning("spark_camera_fix_failed", error=str(err))

    # Deterministic fallback based on pixel height
    if probe.median_px_height < MIN_REASONABLE_PX_HEIGHT:
        return (
            f"Move {probe.camera_id} about 0.5 m closer or increase optical zoom to "
            f"increase {probe.concept} height from {probe.median_px_height}px."
        )
    return (
        f"Adjust {probe.camera_id} angle and lighting to improve {probe.concept} "
        f"detection reliability (currently {probe.detect_lb:.2f} lower bound)."
    )


def _default_alternative(missing_concept: str, concern: str) -> str:
    """Provide a practical alternative when camera observation is not feasible."""
    lowered = missing_concept.lower()
    if "thermometer" in lowered or "temperature" in lowered:
        return "Point a phone at the dial thermometer, or add an automated IoT temperature sensor."
    if "gauge" in lowered or "meter" in lowered:
        return "Install a digital datalogger or perform manual shift-change clipboard logs."
    return (
        f"Use a dedicated mobile device check, or verify {concern} through manual supervisor logs."
    )


def _evaluate_thresholds(
    concern: str,
    required_concepts: Sequence[str],
    best_probes: list[Probe],
    spark: StructuredLLM | None,
) -> FeasibilityVerdict:
    limiting_probe = min(best_probes, key=lambda p: p.detect_lb)
    lowest_lb = limiting_probe.detect_lb

    if lowest_lb >= OBSERVABLE_THRESHOLD:
        concepts_str = ", ".join(required_concepts)
        return FeasibilityVerdict(
            concern=concern,
            verdict="observable",
            says=f"I can reliably observe {concepts_str} to verify {concern}.",
            evidence=tuple(best_probes),
            alternative=None,
        )

    if lowest_lb >= CAVEAT_THRESHOLD:
        fix = _get_cheapest_fix(limiting_probe, spark=spark)
        return FeasibilityVerdict(
            concern=concern,
            verdict="caveat",
            says=(
                f"I can detect {limiting_probe.concept} with caveats (lower bound: "
                f"{limiting_probe.detect_lb:.2f}). {fix}"
            ),
            evidence=tuple(best_probes),
            alternative=fix,
        )

    alt = _default_alternative(limiting_probe.concept, concern)
    return FeasibilityVerdict(
        concern=concern,
        verdict="not_observable",
        says=(
            f"I can't reliably see {limiting_probe.concept} (lower bound: {lowest_lb:.2f} < 0.50), "
            f"so I can't check {concern}."
        ),
        evidence=tuple(best_probes),
        alternative=alt,
    )


def evaluate_concern_feasibility(
    concern: str,
    required_concepts: Sequence[str],
    camera_probes: Sequence[Probe],
    *,
    spark: StructuredLLM | None = None,
    declined: Declined | None = None,
) -> FeasibilityVerdict:
    """Evaluate camera observation feasibility for one safety concern.

    Args:
        concern: The text of the user's safety concern or rule.
        required_concepts: Physical concepts required to verify this concern.
        camera_probes: Detectability probes gathered across cameras for these concepts.
        spark: StructuredLLM client for camera fix proposals.
        declined: If already flagged by guardrails, provide the Declined object.

    Returns:
        FeasibilityVerdict with status ('observable', 'caveat', 'not_observable', 'declined').
    """
    if declined is not None:
        return declined.to_feasibility_verdict(concern)

    guardrail_res = check_request(concern, spark=spark)
    if isinstance(guardrail_res, Declined):
        return guardrail_res.to_feasibility_verdict(concern)

    if not required_concepts:
        return FeasibilityVerdict(
            concern=concern,
            verdict="not_observable",
            says=f"No observable physical objects were identified for: {concern}.",
            evidence=(),
            alternative="Clarify the required physical equipment or process.",
        )

    best_probes: list[Probe] = []
    for concept in required_concepts:
        matching = [p for p in camera_probes if p.concept.lower() == concept.lower()]
        if not matching:
            alt = _default_alternative(concept, concern)
            return FeasibilityVerdict(
                concern=concern,
                verdict="not_observable",
                says=f"I can't see a {concept}, so I can't check {concern}.",
                evidence=(),
                alternative=alt,
            )
        best = max(matching, key=lambda p: p.detect_lb)
        best_probes.append(best)

    return _evaluate_thresholds(concern, required_concepts, best_probes, spark)


def gauge_plan_feasibility(
    concern_requirements: Mapping[str, Sequence[str]],
    all_probes: Sequence[Probe],
    *,
    spark: StructuredLLM | None = None,
) -> tuple[FeasibilityVerdict, ...]:
    """Gauge feasibility across a collection of concerns and their required concepts.

    Args:
        concern_requirements: Mapping of concern text -> sequence of required concepts.
        all_probes: All probes performed across cameras.
        spark: StructuredLLM client.

    Returns:
        Tuple of FeasibilityVerdict entries for each concern.
    """
    verdicts: list[FeasibilityVerdict] = []
    for concern, req_concepts in concern_requirements.items():
        v = evaluate_concern_feasibility(
            concern=concern,
            required_concepts=req_concepts,
            camera_probes=all_probes,
            spark=spark,
        )
        verdicts.append(v)
    return tuple(verdicts)
