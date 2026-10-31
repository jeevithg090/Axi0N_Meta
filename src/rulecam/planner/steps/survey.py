"""The 'Survey' camera survey step (task P2-04, MP §3.2).

Samples 8 frames per camera across the last 60 seconds (seeded).
Spark 1.3 proposes up to 30 noun phrases visible in the scene.
SAM 3.1 `segment_image` runs each concept in parallel, producing `InventoryItem`s.
"""

from __future__ import annotations

import base64
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

import numpy as np
from pydantic import BaseModel, Field

from rulecam.clients.meta_hosted.spark import SPARK_1_3
from rulecam.contracts import StructuredLLM
from rulecam.contracts.core import Detection
from rulecam.contracts.plan import InventoryItem
from rulecam.log import get_logger
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE

_log = get_logger(__name__)

DEFAULT_SURVEY_FRAMES = 8
MAX_SURVEY_CONCEPTS = 30

SURVEY_SYSTEM_PROMPT = (
    "You are Rulecam's camera survey analyst. Analyze the attached camera survey frames "
    "and propose up to 30 salient physical object, person, equipment, or structural noun phrases "
    "present in the scene (e.g. 'sink', 'handwash station', 'prep counter', 'person', "
    "'refrigerator', 'gloves', 'door', 'floor'). Return only distinct, concise noun phrases.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)


class ImageSegmenter(Protocol):
    """Protocol for segmenting a single image with SAM 3.1."""

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "cam0",
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Segment one concept in a single image."""
        ...


class ProposedConcepts(BaseModel):
    """List of up to 30 physical concepts identified in survey frames."""

    concepts: list[str] = Field(default_factory=list, max_length=MAX_SURVEY_CONCEPTS)


def sample_survey_frames(
    frames: Sequence[Path],
    n_samples: int = DEFAULT_SURVEY_FRAMES,
    *,
    seed: int = 42,
) -> list[Path]:
    """Sample `n_samples` frames deterministically using a seed.

    Args:
        frames: Sequence of frame paths available for one camera.
        n_samples: Number of frames to sample (default: 8).
        seed: Random seed for deterministic selection.

    Returns:
        Sorted list of sampled frame paths.
    """
    if len(frames) <= n_samples:
        return list(frames)
    rng = random.Random(seed)  # noqa: S311 - deterministic sampling seed
    indices = sorted(rng.sample(range(len(frames)), n_samples))
    return [frames[i] for i in indices]


def propose_survey_concepts(
    sample_frames: Sequence[Path],
    *,
    spark: StructuredLLM,
) -> list[str]:
    """Ask Spark 1.3 to propose up to 30 salient noun phrases from survey frames.

    Args:
        sample_frames: Representative frames sampled across cameras.
        spark: StructuredLLM client.

    Returns:
        List of unique proposed concept noun phrases.
    """
    user_prompt = "Propose up to 30 physical concepts, items, stations, or objects visible:\n"
    content_parts: list[dict[str, object]] = [{"type": "input_text", "text": user_prompt}]

    for frame in sample_frames[:4]:  # Attach up to 4 representative images
        if frame.exists():
            mime = "image/jpeg" if frame.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            b64 = base64.b64encode(frame.read_bytes()).decode("ascii")
            content_parts.append({"type": "input_image", "image_url": f"data:{mime};base64,{b64}"})

    res = spark.structured(
        model=SPARK_1_3,
        messages=[
            {"role": "system", "content": SURVEY_SYSTEM_PROMPT},
            {"role": "user", "content": content_parts},
        ],
        schema=ProposedConcepts,
    )
    # Deduplicate and normalize
    cleaned = [c.strip().lower() for c in res.concepts if c.strip()]
    return list(dict.fromkeys(cleaned))[:MAX_SURVEY_CONCEPTS]


def survey_cameras(
    camera_frames: Mapping[str, Sequence[Path]],
    *,
    spark: StructuredLLM,
    segmenter: ImageSegmenter,
    seed: int = 42,
    custom_concepts: Sequence[str] | None = None,
) -> tuple[InventoryItem, ...]:
    """Run camera survey to build an inventory of detected concepts across cameras.

    Args:
        camera_frames: Mapping of camera_id -> sequence of frame paths.
        spark: StructuredLLM for concept discovery.
        segmenter: SAM 3.1 segmenter for detecting concepts in frames.
        seed: Seed for frame sampling.
        custom_concepts: Optional list of concepts to force inclusion in the survey.

    Returns:
        Tuple of InventoryItems found in the cameras.
    """
    # Sample 8 frames per camera
    sampled_per_cam: dict[str, list[Path]] = {}
    all_sample_frames: list[Path] = []
    for cam_id, frames in sorted(camera_frames.items()):
        sampled = sample_survey_frames(frames, n_samples=DEFAULT_SURVEY_FRAMES, seed=seed)
        sampled_per_cam[cam_id] = sampled
        all_sample_frames.extend(sampled)

    # Propose concepts from survey frames
    concepts = list(custom_concepts) if custom_concepts else []
    if not concepts and all_sample_frames:
        concepts = propose_survey_concepts(all_sample_frames, spark=spark)

    inventory: list[InventoryItem] = []
    for cam_id, frames in sorted(sampled_per_cam.items()):
        for concept in concepts:
            all_dets: list[Detection] = []
            for frame in frames:
                dets = segmenter.segment_image(frame, concept, camera_id=cam_id)
                all_dets.extend(dets)

            if all_dets:
                confidences = [d.mask.confidence for d in all_dets if d.mask.confidence is not None]
                mean_conf = float(np.mean(confidences)) if confidences else 1.0
                inventory.append(
                    InventoryItem(
                        concept=concept,
                        camera_id=cam_id,
                        count=len(all_dets),
                        mean_confidence=round(mean_conf, 4),
                    )
                )

    return tuple(inventory)
