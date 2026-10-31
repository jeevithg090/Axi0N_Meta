"""What Rule CI's still-image tests share: the renderer interface and single-frame candidates.

Detectability scenes, lookalikes and injection signs (task P2-09) are Muse Image stills of the
real camera frame. Lookalikes and injection signs are judged by the real verifiers, which take a
`CandidateEvent`; `still_candidate` wraps one image as a single-frame strip.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol

from rulecam.clients.meta_hosted.image import DEFAULT_SIZE, ImageResult
from rulecam.contracts import CandidateEvent, RuleIR
from rulecam.rules.hashing import rule_ir_hash

SPARK_MODEL: Final = "muse-spark-1.3"
_UNSAFE: Final = re.compile(r"[^A-Za-z0-9_.-]")


class ImageRenderer(Protocol):
    """Muse Image (`MuseImageClient`, through the replay cache)."""

    def generate(
        self,
        prompt: str,
        *,
        reference_images: Sequence[Path] = (),
        size: str = DEFAULT_SIZE,
        previous_response_id: str | None = None,
    ) -> ImageResult:
        """Render one image, with the real camera frame as a reference."""
        ...


def still_candidate(
    rule: RuleIR, image: Path, *, camera_id: str, name: str, ts: datetime
) -> CandidateEvent:
    """A candidate whose strip is one rendered still, for the verifiers.

    Args:
        rule: The rule under test (its severity picks the policy branch).
        image: The still.
        camera_id: The camera whose frame the still was rendered from.
        name: The test's name; it makes the event id unique within a Rule CI run.
        ts: When the test ran.

    Returns:
        The candidate. `predicate_trace` says it is a Rule CI still, not a real firing.
    """
    return CandidateEvent(
        event_id=f"rule-ci-{_UNSAFE.sub('_', rule.id)}-{_UNSAFE.sub('_', name)}",
        rule_id=rule.id,
        camera_id=camera_id,
        ts_start=ts,
        ts_end=ts,
        severity=rule.severity,
        predicate_trace={"rule_ci": name},
        strip_paths=(str(image),),
        track_window_hash=hashlib.sha256(image.read_bytes()).hexdigest(),
        plan_version_hash="",
        rule_ir_hash=rule_ir_hash(rule),
    )
