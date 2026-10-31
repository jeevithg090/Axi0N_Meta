"""The 'Probe' detectability probing step (task P2-04, MP §3.2).

Runs SAM 3.1 on 12 sampled frames per camera for each required concept.
Computes detect_rate, Wilson 95% lower bound (detect_lb), and median pixel height.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np

from rulecam.contracts.core import Detection
from rulecam.contracts.plan import Probe
from rulecam.log import get_logger
from rulecam.planner.steps.survey import ImageSegmenter
from rulecam.stats.intervals import wilson_interval

_log = get_logger(__name__)

DEFAULT_PROBE_FRAMES = 12


def sample_probe_frames(
    frames: Sequence[Path],
    n_samples: int = DEFAULT_PROBE_FRAMES,
    *,
    seed: int = 42,
) -> list[Path]:
    """Sample `n_samples` frames for detectability probing deterministically.

    Args:
        frames: Sequence of frame paths available for the camera.
        n_samples: Number of frames to sample (default: 12).
        seed: Random seed for deterministic selection.

    Returns:
        Sorted list of sampled frame paths.
    """
    if len(frames) <= n_samples:
        return list(frames)
    rng = random.Random(seed)  # noqa: S311 - deterministic sampling seed
    indices = sorted(rng.sample(range(len(frames)), n_samples))
    return [frames[i] for i in indices]


def probe_concept_on_camera(
    concept: str,
    camera_id: str,
    frames: Sequence[Path],
    *,
    segmenter: ImageSegmenter,
    n_samples: int = DEFAULT_PROBE_FRAMES,
    seed: int = 42,
) -> Probe:
    """Run SAM detectability probe for one concept on one camera.

    Args:
        concept: Concept noun phrase being probed (e.g. 'sink', 'gloves').
        camera_id: Identifier of the camera.
        frames: Frame paths available for this camera.
        segmenter: SAM 3.1 image segmenter.
        n_samples: Number of frames to sample (default: 12).
        seed: Sampling seed for determinism.

    Returns:
        Probe containing detect_rate, Wilson 95% lower bound, and median pixel height.
    """
    sampled = sample_probe_frames(frames, n_samples=n_samples, seed=seed)
    if not sampled:
        return Probe(
            concept=concept,
            camera_id=camera_id,
            n_frames=0,
            detect_rate=0.0,
            detect_lb=0.0,
            median_px_height=0.0,
        )

    successes = 0
    all_heights: list[float] = []

    for frame in sampled:
        dets: list[Detection] = segmenter.segment_image(frame, concept, camera_id=camera_id)
        if dets:
            successes += 1
            for d in dets:
                h = max(0.0, float(d.mask.box_xyxy[3] - d.mask.box_xyxy[1]))
                if h > 0:
                    all_heights.append(h)

    n_total = len(sampled)
    detect_rate = successes / n_total
    detect_lb, _ = wilson_interval(successes, n_total, confidence=0.95)
    median_px = float(np.median(all_heights)) if all_heights else 0.0

    return Probe(
        concept=concept,
        camera_id=camera_id,
        n_frames=n_total,
        detect_rate=round(detect_rate, 4),
        detect_lb=round(detect_lb, 4),
        median_px_height=round(median_px, 2),
    )


def probe_concepts(
    concepts: Sequence[str],
    camera_frames: Mapping[str, Sequence[Path]],
    *,
    segmenter: ImageSegmenter,
    n_samples: int = DEFAULT_PROBE_FRAMES,
    seed: int = 42,
) -> tuple[Probe, ...]:
    """Run detectability probes for all requested concepts across all cameras.

    Args:
        concepts: List of concept noun phrases.
        camera_frames: Mapping of camera_id -> sequence of frame paths.
        segmenter: SAM 3.1 segmenter.
        n_samples: Number of frames per camera (default: 12).
        seed: Random seed for sampling.

    Returns:
        Tuple of Probes for each (concept, camera_id) pair.
    """
    probes: list[Probe] = []
    unique_concepts = list(dict.fromkeys(c.strip().lower() for c in concepts if c.strip()))

    for concept in unique_concepts:
        for cam_id, frames in sorted(camera_frames.items()):
            p = probe_concept_on_camera(
                concept=concept,
                camera_id=cam_id,
                frames=frames,
                segmenter=segmenter,
                n_samples=n_samples,
                seed=seed,
            )
            probes.append(p)

    return tuple(probes)
