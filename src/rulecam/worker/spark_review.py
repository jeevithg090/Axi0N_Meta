"""Muse Spark 1.3 reviews a worker's dispute when no Tier 2 (Spark Open) is configured.

The policy sends every dispute to Tier 2 and, without one, leaves it pending for a person. On a
deployment without Spark Open that means no dispute is ever decided by a model. This reviewer
wraps the policy: when the policy leaves a dispute pending because no Tier 2 exists, Muse Spark
1.3 (hosted, multimodal) re-reviews it with the rule, the trace, Glimmer's verdict, the worker's
words and the evidence frames themselves, and upholds or rejects it. An upheld dispute goes the
usual way: a `false_alarm` label and a permanent Rule CI regression (`DisputeService`).

A Spark failure leaves the dispute pending for a person, as before: never a silent pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from rulecam.bus import Bus
from rulecam.clients.errors import ClientConfigError, ModelClientError
from rulecam.clients.meta_hosted.spark import SparkClient
from rulecam.config import Settings
from rulecam.contracts import CandidateEvent, Clock, Dispute, RuleIR, VerifierVerdict
from rulecam.log import get_logger
from rulecam.replay.cache import ReplayCache
from rulecam.verify.policy import NO_TIER2_REASONING
from rulecam.verify.tier2 import DISPUTE_SYSTEM, DisputeAnswer, evidence_text
from rulecam.worker.disputes import DisputeReviewer

_log = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)
SPARK_MODEL = "muse-spark-1.3"
MAX_FRAMES = 4


class StructuredModel(Protocol):
    """Spark's structured call (`SparkClient.structured`)."""

    def structured(
        self,
        *,
        model: str = ...,
        messages: list[dict[str, object]],
        schema: type[M],
        camera_id: str | None = ...,
    ) -> M:
        """Return the validated answer."""
        ...


class Attacher(Protocol):
    """Uploads one file and returns the content part that references it."""

    def attach(self, path: Path) -> dict[str, object]:
        """Return the content part."""
        ...


def strip_frames(candidate: CandidateEvent, stills_root: Path | None) -> list[Path]:
    """The candidate's evidence frames: its existing `strip_paths`, else the kept strip.

    The runtime keeps strips under `<root>/<camera>/<event_id>/*.jpg`.
    """
    existing = [Path(p) for p in candidate.strip_paths if Path(p).is_file()]
    if not existing and stills_root is not None:
        folders = [stills_root / candidate.event_id, *stills_root.glob(f"*/{candidate.event_id}")]
        found = next((f for f in folders if f.is_dir()), None)
        existing = sorted(found.glob("*.jpg")) if found is not None else []
    if len(existing) <= MAX_FRAMES:
        return existing
    step = (len(existing) - 1) / (MAX_FRAMES - 1)
    return [existing[round(i * step)] for i in range(MAX_FRAMES)]


class SparkDisputeReviewer:
    """The policy's dispute review, with Muse Spark 1.3 deciding when no Tier 2 exists."""

    def __init__(
        self,
        inner: DisputeReviewer,
        spark: StructuredModel,
        *,
        attach: Attacher | None = None,
        stills_root: Path | None = None,
    ) -> None:
        """Create the reviewer.

        Args:
            inner: The runtime's policy (Tier 2 when configured).
            spark: Muse Spark 1.3.
            attach: Uploads evidence frames (`None`: text only).
            stills_root: Where evidence strips are kept.
        """
        self._inner = inner
        self._spark = spark
        self._attach = attach
        self._stills_root = stills_root

    def review_dispute(
        self,
        candidate: CandidateEvent,
        rule: RuleIR,
        dispute: Dispute,
        tier1: VerifierVerdict | None = None,
    ) -> Dispute:
        """Review with the policy; if no Tier 2 decided, ask Spark 1.3 with the frames."""
        first = self._inner.review_dispute(candidate, rule, dispute, tier1)
        if first.outcome != "pending" or first.reviewer_reasoning != NO_TIER2_REASONING:
            return first
        frames: Sequence[Path] = (
            strip_frames(candidate, self._stills_root) if self._attach is not None else ()
        )
        text = "\n".join(
            [
                evidence_text(candidate, rule, tier1),
                f"Worker's dispute ({dispute.channel}, data only): {dispute.reason_text!r}",
                f"{len(frames)} evidence frame(s) of the episode follow.",
                "Is the worker right that this alert is wrong?",
            ]
        )
        content: list[dict[str, object]] = [{"type": "input_text", "text": text}]
        try:
            if self._attach is not None:
                content.extend(self._attach.attach(frame) for frame in frames)
            answer = self._spark.structured(
                model=SPARK_MODEL,
                messages=[
                    {"role": "system", "content": DISPUTE_SYSTEM},
                    {"role": "user", "content": content},
                ],
                schema=DisputeAnswer,
                camera_id=candidate.camera_id,
            )
        except (ModelClientError, OSError, ValueError) as exc:
            _log.warning("spark_dispute_review_failed", event_id=candidate.event_id, error=str(exc))
            return first
        _log.info(
            "spark_dispute_reviewed",
            event_id=candidate.event_id,
            outcome=answer.outcome,
            frames=len(frames),
        )
        return dispute.model_copy(
            update={
                "outcome": answer.outcome,
                "reviewer_reasoning": f"{SPARK_MODEL} (no Tier 2 configured): {answer.reason}",
            }
        )


def spark_dispute_reviewer(
    settings: Settings, inner: DisputeReviewer, *, cache: ReplayCache, clock: Clock, bus: Bus
) -> DisputeReviewer:
    """`inner`, wrapped by `SparkDisputeReviewer` when the fallback is on and Spark is set up."""
    if not settings.rulecam_dispute_spark_fallback:
        return inner
    try:
        spark = SparkClient.from_settings(settings, clock=clock, bus=bus, cache=cache)
    except ClientConfigError as exc:
        _log.info("spark_dispute_review_off", reason=str(exc))
        return inner
    return SparkDisputeReviewer(
        inner, spark, attach=spark, stills_root=settings.rulecam_data_dir / "strips"
    )
