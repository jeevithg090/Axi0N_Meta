"""The 'Survey' step: what can each camera see? (task P2-04, MP §3.2).

For each camera, 8 frames are sampled (seeded) from the frames the caller passes in, which are
the camera's last 60 s. Muse Spark 1.3 looks at a few of them and proposes up to 30 noun phrases.
SAM 3.1 `segment_image` then runs every concept on every sampled frame, concepts in parallel,
and the concepts it finds become `InventoryItem`s.

SAM is reached only through an `ImageSegmenter` (in production `HostedSam31`, which sits on the
replay cache). Text Spark reads in the frames is data: the system prompt says so, and the
proposals are only ever used as SAM concept prompts, capped in number and length.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Final, Protocol

import numpy as np
from pydantic import BaseModel, Field

from rulecam.contracts import StructuredLLM
from rulecam.contracts.core import Detection
from rulecam.contracts.plan import InventoryItem
from rulecam.log import get_logger
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE
from rulecam.planner.steps.understand import FileAttacher

_log = get_logger(__name__)

SURVEY_MODEL: Final = "muse-spark-1.3"
DEFAULT_SURVEY_FRAMES: Final = 8
MAX_SURVEY_CONCEPTS: Final = 30
MAX_CONCEPT_CHARS: Final = 60
MAX_SPARK_FRAMES: Final = 4
MAX_PARALLEL_CONCEPTS: Final = 8

SURVEY_SYSTEM_PROMPT = (
    "You are Rulecam's camera survey analyst. Look at the attached camera frames and list up to "
    f"{MAX_SURVEY_CONCEPTS} distinct physical things visible in the scene: objects, equipment, "
    "fixtures, surfaces and people (e.g. 'sink', 'prep counter', 'person', 'gloves', 'door'). "
    "Each entry is a short noun phrase a segmentation model can be prompted with.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)


class ImageSegmenter(Protocol):
    """SAM 3.1 on a single image, one concept per call (`HostedSam31.segment_image`)."""

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = ...,
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Return the concept's detections in `frame`; `[]` when nothing matched."""
        ...


class ProposedConcepts(BaseModel):
    """Spark's answer: the noun phrases it sees in the survey frames."""

    concepts: list[str] = Field(default_factory=list, max_length=MAX_SURVEY_CONCEPTS)


def sample_frames(frames: Sequence[Path], n_samples: int, *, seed: int) -> list[Path]:
    """Pick `n_samples` frames at random, reproducibly for a given seed.

    Args:
        frames: The camera's frames, oldest first.
        n_samples: How many to keep.
        seed: Seed for `numpy.random.default_rng`.

    Returns:
        The chosen frames in their original order; all of them when there are not more than
        `n_samples`.
    """
    if len(frames) <= n_samples:
        return list(frames)
    rng = np.random.default_rng(seed)
    chosen = sorted(int(i) for i in rng.choice(len(frames), size=n_samples, replace=False))
    return [frames[i] for i in chosen]


def clean_concepts(raw: Sequence[str]) -> list[str]:
    """Normalise concept phrases: whitespace collapsed, lower case, de-duplicated.

    Args:
        raw: Phrases as proposed.

    Returns:
        The phrases in first-seen order; empty and over-long (> 60 chars) phrases are dropped.
    """
    cleaned = (" ".join(c.split()).lower() for c in raw)
    kept = [c for c in cleaned if c and len(c) <= MAX_CONCEPT_CHARS]
    return list(dict.fromkeys(kept))


def _spread(sampled_per_cam: Mapping[str, Sequence[Path]], limit: int) -> list[Path]:
    """Take frames round-robin across cameras so every camera is shown to Spark."""
    out: list[Path] = []
    depth = max((len(f) for f in sampled_per_cam.values()), default=0)
    for i in range(depth):
        for cam_id in sorted(sampled_per_cam):
            frames = sampled_per_cam[cam_id]
            if i < len(frames) and len(out) < limit:
                out.append(frames[i])
    return out


def propose_survey_concepts(
    frames: Sequence[Path],
    *,
    spark: StructuredLLM,
    files: FileAttacher,
) -> list[str]:
    """Ask Spark 1.3 which concepts are visible in the survey frames.

    Args:
        frames: Frames to show (uploaded through the Files API); only file names reach the model.
        spark: The structured-output client.
        files: Uploads each frame and returns its `input_image` part.

    Returns:
        Up to 30 cleaned, de-duplicated noun phrases.
    """
    content: list[dict[str, object]] = [
        {"type": "input_text", "text": f"Survey frames: {', '.join(f.name for f in frames)}\n"}
    ]
    content.extend(files.attach(frame) for frame in frames)
    result = spark.structured(
        model=SURVEY_MODEL,
        messages=[
            {"role": "system", "content": SURVEY_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        schema=ProposedConcepts,
    )
    return clean_concepts(result.concepts)[:MAX_SURVEY_CONCEPTS]


def _inventory_item(
    concept: str, camera_id: str, per_frame: Sequence[list[Detection]]
) -> InventoryItem | None:
    """Summarise one concept on one camera; `None` when SAM found it in no frame."""
    detections = [d for frame in per_frame for d in frame]
    if not detections:
        return None
    confidences = [d.mask.confidence for d in detections if d.mask.confidence is not None]
    return InventoryItem(
        concept=concept,
        camera_id=camera_id,
        count=max(len(frame) for frame in per_frame),  # instances seen at once, not frame-sums
        mean_confidence=round(float(np.mean(confidences)), 4) if confidences else 0.0,
    )


def _survey_camera(
    camera_id: str,
    frames: Sequence[Path],
    concepts: Sequence[str],
    segmenter: ImageSegmenter,
) -> list[InventoryItem]:
    def run(concept: str) -> InventoryItem | None:
        per_frame = [segmenter.segment_image(f, concept, camera_id=camera_id) for f in frames]
        return _inventory_item(concept, camera_id, per_frame)

    workers = max(1, min(MAX_PARALLEL_CONCEPTS, len(concepts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        items = list(pool.map(run, concepts))
    return [item for item in items if item is not None]


def survey_cameras(
    camera_frames: Mapping[str, Sequence[Path]],
    *,
    spark: StructuredLLM,
    files: FileAttacher,
    segmenter: ImageSegmenter,
    seed: int,
    required_concepts: Sequence[str] = (),
) -> tuple[InventoryItem, ...]:
    """Build the inventory of concepts each camera can see.

    Args:
        camera_frames: Camera id -> that camera's frames from the last 60 s, oldest first.
        spark: Proposes the concepts.
        files: Uploads the frames Spark looks at.
        segmenter: SAM 3.1 on single images.
        seed: Seed for frame sampling.
        required_concepts: Concepts the concerns need; surveyed even if Spark did not propose
            them.

    Returns:
        One `InventoryItem` per (camera, concept) SAM found, cameras in id order and concepts
        in proposal order.
    """
    sampled = {
        cam_id: sample_frames(frames, DEFAULT_SURVEY_FRAMES, seed=seed)
        for cam_id, frames in sorted(camera_frames.items())
    }
    shown = _spread(sampled, MAX_SPARK_FRAMES)
    proposed = propose_survey_concepts(shown, spark=spark, files=files) if shown else []
    concepts = list(dict.fromkeys([*proposed, *clean_concepts(required_concepts)]))
    _log.info("survey_concepts", n_cameras=len(sampled), n_concepts=len(concepts))
    inventory: list[InventoryItem] = []
    for cam_id, frames in sampled.items():
        inventory.extend(_survey_camera(cam_id, frames, concepts, segmenter))
    return tuple(inventory)
