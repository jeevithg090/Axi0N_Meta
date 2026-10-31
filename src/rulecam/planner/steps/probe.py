"""The 'Probe' step: how reliably does SAM see each needed concept? (task P2-04, MP §3.2).

For each concept the concerns need, SAM 3.1 runs on sampled frames of every camera. A frame is a
hit when SAM returns at least one detection. Each (concept, camera) gets a `Probe` with the hit
rate, its Wilson 95% lower bound and the median box height in pixels.

Frame count: the task book says 12 frames, but with 12 frames even 12/12 hits gives a Wilson
lower bound of 12 / (12 + 1.96²) = 0.758, below the 0.8 "observable" bar, so nothing could ever be
observable. The default is 24 frames: 24/24 gives 0.862 (the MP §7.2 example's 0.86).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Final

from rulecam.contracts.plan import Probe
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter, clean_concepts, sample_frames
from rulecam.stats.intervals import wilson_interval

_log = get_logger(__name__)

DEFAULT_PROBE_FRAMES: Final = 24


def probe_concept_on_camera(
    concept: str,
    camera_id: str,
    frames: Sequence[Path],
    *,
    segmenter: ImageSegmenter,
    seed: int,
    n_samples: int = DEFAULT_PROBE_FRAMES,
) -> Probe:
    """Measure how often SAM finds `concept` in one camera's frames.

    Args:
        concept: The concept noun phrase.
        camera_id: The camera.
        frames: The camera's frames, oldest first.
        segmenter: SAM 3.1 on single images.
        seed: Seed for frame sampling.
        n_samples: Frames to sample.

    Returns:
        The probe. With no frames it has `n_frames=0` and a zero rate and bound.
    """
    sampled = sample_frames(frames, n_samples, seed=seed)
    hits = 0
    heights: list[float] = []
    for frame in sampled:
        detections = segmenter.segment_image(frame, concept, camera_id=camera_id)
        hits += bool(detections)
        heights.extend(h for d in detections if (h := d.mask.box_xyxy[3] - d.mask.box_xyxy[1]) > 0)
    n_frames = len(sampled)
    lower, _ = wilson_interval(hits, n_frames)
    probe = Probe(
        concept=concept,
        camera_id=camera_id,
        n_frames=n_frames,
        detect_rate=round(hits / n_frames, 4) if n_frames else 0.0,
        detect_lb=round(lower, 4),
        median_px_height=round(median(heights), 2) if heights else 0.0,
    )
    _log.info("probe", concept=concept, camera_id=camera_id, hits=hits, n=n_frames)
    return probe


def probe_concepts(
    concepts: Sequence[str],
    camera_frames: Mapping[str, Sequence[Path]],
    *,
    segmenter: ImageSegmenter,
    seed: int,
    n_samples: int = DEFAULT_PROBE_FRAMES,
) -> tuple[Probe, ...]:
    """Probe every concept on every camera.

    Args:
        concepts: Concept noun phrases (normalised and de-duplicated here).
        camera_frames: Camera id -> that camera's frames, oldest first.
        segmenter: SAM 3.1 on single images.
        seed: Seed for frame sampling.
        n_samples: Frames to sample per camera.

    Returns:
        One probe per (concept, camera), concepts in first-seen order and cameras in id order.
    """
    return tuple(
        probe_concept_on_camera(
            concept, cam_id, frames, segmenter=segmenter, seed=seed, n_samples=n_samples
        )
        for concept in clean_concepts(concepts)
        for cam_id, frames in sorted(camera_frames.items())
    )
