"""Boundary-pair Muse Image edits using previous_response_id (P2 stretch task, MP §3.2, §12).

Tests detectability and spatial boundaries with paired, minimally-perturbed synthetic scenes.
The first image establishes the scene; the second image edits it via `previous_response_id`
(e.g., 'same scene, half a step closer' or 'same worker, remove cut-resistant gloves').
Because the second turn chains directly onto the first, background clutter, camera angle,
lighting, and actor appearance remain identical, isolating the exact decision boundary.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from rulecam.contracts.core import Calibration, Frozen
from rulecam.contracts.rule_ir import RuleIR
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter
from rulecam.rule_ci.stills import ImageRenderer

_log = get_logger(__name__)

EPSILON: Final = 1e-9


class BoundaryPairCase(BaseModel):
    """Specification of one boundary-pair edit test.

    Attributes:
        name: Unique test identifier, e.g. `forklift_pedestrian_distance_boundary`.
        concept: The primary concept under test.
        secondary_concept: Optional second concept for distance/overlap tests.
        base_prompt: Scene prompt for the unperturbed initial still.
        edit_prompt: Minimal delta prompt chained via `previous_response_id`.
        expected_base_firing: Whether the base still should trigger a rule firing / breach.
        expected_edit_firing: Whether the edited still should trigger a rule firing / breach.
        distance_threshold_m: Optional ground-plane distance limit in metres.
    """

    name: str
    concept: str
    secondary_concept: str | None = None
    base_prompt: str
    edit_prompt: str
    expected_base_firing: bool
    expected_edit_firing: bool
    distance_threshold_m: float | None = None


class BoundaryPairResult(Frozen):
    """Outcome of one boundary-pair evaluation.

    Attributes:
        name: Test name.
        base_image: Path to initial generated still.
        edit_image: Path to follow-up edited still.
        base_response_id: Model response id for initial turn.
        edit_response_id: Model response id for chained turn.
        base_fired: Whether base still triggered the breach condition.
        edit_fired: Whether edit still triggered the breach condition.
        passed: True if both base and edit matched their expected firing states.
        detail: Human-readable diagnostic.
    """

    name: str
    base_image: str
    edit_image: str
    base_response_id: str
    edit_response_id: str
    base_fired: bool
    edit_fired: bool
    passed: bool
    detail: str


class BoundaryPairReport(Frozen):
    """Aggregated report across multiple boundary-pair tests."""

    results: tuple[BoundaryPairResult, ...]

    @property
    def passed(self) -> int:
        """Count of successful boundary pairs."""
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        """Total tests run."""
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        """Fraction of successful tests (0.0 if empty)."""
        return self.passed / self.total if self.total > 0 else 0.0

    def summary(self) -> str:
        """Short summary string, e.g. 'boundary pairs: 4/4 verified'."""
        return f"boundary pairs: {self.passed}/{self.total} verified"


def project_pixel_to_ground(
    pixel_x: float, pixel_y: float, calibration: Calibration
) -> tuple[float, float]:
    """Project an image pixel coordinate to ground metric coordinates via homography.

    Args:
        pixel_x: Horizontal pixel coordinate.
        pixel_y: Vertical pixel coordinate.
        calibration: Calibration carrying 3x3 homography matrix.

    Returns:
        (X, Y) coordinate in ground metres.
    """
    h = calibration.homography
    u = h[0][0] * pixel_x + h[0][1] * pixel_y + h[0][2]
    v = h[1][0] * pixel_x + h[1][1] * pixel_y + h[1][2]
    w = h[2][0] * pixel_x + h[2][1] * pixel_y + h[2][2]
    if abs(w) < EPSILON:
        return (0.0, 0.0)
    return (u / w, v / w)


def evaluate_firing(
    image: Path,
    case: BoundaryPairCase,
    rule: RuleIR,
    segmenter: ImageSegmenter,
    *,
    camera_id: str,
    calibration: Calibration | None = None,
) -> bool:
    """Evaluate whether an image triggers the rule's breach condition."""
    prompt1 = rule.concepts.get(case.concept, None)
    p1_text = prompt1.prompt if prompt1 else case.concept
    detections1 = segmenter.segment_image(image, p1_text, camera_id=camera_id)
    if not detections1:
        return False

    # Presence-only breach condition
    if not case.secondary_concept or case.distance_threshold_m is None:
        return True

    # Two-concept distance condition
    prompt2 = rule.concepts.get(case.secondary_concept, None)
    p2_text = prompt2.prompt if prompt2 else case.secondary_concept
    detections2 = segmenter.segment_image(image, p2_text, camera_id=camera_id)
    if not detections2:
        return False

    if calibration is None:
        # Without calibration, overlap / presence of both triggers breach
        return True

    # Compute ground metric distance between bounding box bottom-centers (footings)
    box1 = detections1[0].mask.box_xyxy
    box2 = detections2[0].mask.box_xyxy
    foot1 = ((box1[0] + box1[2]) / 2.0, box1[3])
    foot2 = ((box2[0] + box2[2]) / 2.0, box2[3])

    g1 = project_pixel_to_ground(foot1[0], foot1[1], calibration)
    g2 = project_pixel_to_ground(foot2[0], foot2[1], calibration)
    dist_m = math.hypot(g1[0] - g2[0], g1[1] - g2[1])

    return dist_m <= case.distance_threshold_m


def render_boundary_pair(
    case: BoundaryPairCase,
    rule: RuleIR,
    frame: Path,
    *,
    renderer: ImageRenderer,
    segmenter: ImageSegmenter,
    camera_id: str,
    calibration: Calibration | None = None,
) -> BoundaryPairResult:
    """Execute a boundary pair edit test using `previous_response_id`.

    Args:
        case: Test case definition.
        rule: Rule under test.
        frame: Real camera reference frame.
        renderer: Muse Image client.
        segmenter: SAM 3.1 segmenter.
        camera_id: Target camera id.
        calibration: Optional ground homography calibration.

    Returns:
        BoundaryPairResult with verification outcome.
    """
    base_res = renderer.generate(case.base_prompt, reference_images=(frame,))
    base_fired = evaluate_firing(
        base_res.path,
        case,
        rule,
        segmenter,
        camera_id=camera_id,
        calibration=calibration,
    )

    edit_res = renderer.generate(
        case.edit_prompt,
        previous_response_id=base_res.response_id,
    )
    edit_fired = evaluate_firing(
        edit_res.path,
        case,
        rule,
        segmenter,
        camera_id=camera_id,
        calibration=calibration,
    )

    passed = (base_fired == case.expected_base_firing) and (edit_fired == case.expected_edit_firing)
    detail = (
        f"Base: fired={base_fired} (expected {case.expected_base_firing}); "
        f"Edit: fired={edit_fired} (expected {case.expected_edit_firing})"
    )

    _log.info(
        "boundary_pair_evaluated",
        name=case.name,
        passed=passed,
        base_fired=base_fired,
        edit_fired=edit_fired,
    )

    return BoundaryPairResult(
        name=case.name,
        base_image=str(base_res.path),
        edit_image=str(edit_res.path),
        base_response_id=base_res.response_id,
        edit_response_id=edit_res.response_id,
        base_fired=base_fired,
        edit_fired=edit_fired,
        passed=passed,
        detail=detail,
    )


def run_boundary_pairs_suite(
    rule: RuleIR,
    frame: Path,
    cases: Sequence[BoundaryPairCase],
    *,
    renderer: ImageRenderer,
    segmenter: ImageSegmenter,
    camera_id: str,
    calibration: Calibration | None = None,
) -> BoundaryPairReport:
    """Run a suite of boundary-pair cases and compile a report."""
    results = tuple(
        render_boundary_pair(
            case,
            rule,
            frame,
            renderer=renderer,
            segmenter=segmenter,
            camera_id=camera_id,
            calibration=calibration,
        )
        for case in cases
    )
    return BoundaryPairReport(results=results)
