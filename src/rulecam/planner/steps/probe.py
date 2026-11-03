"""The 'Probe' step: how reliably does SAM see each needed concept? (task P2-04, MP §3.2).

For each concept the concerns need, SAM 3.1 runs on sampled frames of every camera. A frame is a
hit when SAM returns at least one detection. Each (concept, camera) gets a `Probe` with the hit
rate, its Wilson 95% lower bound and the median box height in pixels.

Frame count: the task book says 12 frames, but with 12 frames even 12/12 hits gives a Wilson
lower bound of 12 / (12 + 1.96²) = 0.758, below the 0.8 "observable" bar, so nothing could ever be
observable. The default is 24 frames: 24/24 gives 0.862 (the MP §7.2 example's 0.86). A bound
of 0.8 needs at least 16 frames, all hits, so the camera must supply them: `ChunkFrames` takes
up to 3 frames per 3 s chunk (at least 1 s apart), so a 60 s window gives up to ~60 frames.

Sampling and its caveat: the 24 frames are spread across as many chunks as possible (seeded,
at most `ceil(24 / n_chunks)` per chunk while other chunks still have frames, see
`survey.sample_frames`). Frames of one chunk, and of neighbouring chunks, are seconds apart and
correlated (a static scene gives near-identical frames), while the Wilson interval assumes
independent trials. The effective sample size is smaller than `n_frames`, so `detect_lb` is
optimistic for a scene that barely changes over the window; a longer window
(`PLANNER_SURVEY_WINDOW_S`) gives more independent frames.

Which concepts a concern needs (`concepts_for_concerns`) is Spark 1.3's judgement, given the
concerns and the surveyed concepts as JSON; the answer is cleaned and capped in code.

Crop-zoom (add-on 3.3): Spark also says which needed concepts a person wears or holds and on
which body region (`concept_needs(...).worn`: head, hands or body). A hat, hair net or glove is a
few dozen pixels in the full frame, so on a frame where SAM misses such a concept, `ProbeZoom`
finds the people in it (SAM, `person`), crops and enlarges that region of each
(`perception.crop_zoom`) and asks SAM 3.1 again on the crops. A crop hit counts as a hit, with
its box height in frame pixels. Without a crop segmenter, or for a concept nobody wears, the
probe is the full-frame probe it always was.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Final

import cv2
from pydantic import BaseModel, Field

from rulecam.contracts import StructuredLLM
from rulecam.contracts.core import BoxXYXY
from rulecam.contracts.plan import Probe
from rulecam.log import get_logger
from rulecam.perception.crop_zoom import CropHit, CropRegion, CropSegmenter, person_crops
from rulecam.planner.models import DATA_NOT_INSTRUCTIONS_CLAUSE, Concern
from rulecam.planner.steps.survey import ImageSegmenter, clean_concepts, sample_frames
from rulecam.stats.intervals import wilson_interval

_log = get_logger(__name__)

DEFAULT_PROBE_FRAMES: Final = 24
NEEDS_MODEL: Final = "muse-spark-1.3"
MAX_CONCEPTS_PER_CONCERN: Final = 4
ZOOM_PERSON_CONCEPT: Final = "person"
MAX_ZOOM_PEOPLE: Final = 6  # the largest people in a frame whose crops are checked

NEEDS_SYSTEM_PROMPT = (
    "You are Rulecam's detectability planner. You get the site's concerns and the concepts the "
    "cameras already see, as JSON. For each concern, list the physical things (at most "
    f"{MAX_CONCEPTS_PER_CONCERN}) a camera must see to check it, as short noun phrases a "
    "segmentation model can be prompted with (e.g. 'sink', 'gloves', 'thermometer'). Prefer the "
    "surveyed concepts' wording; name a thing even if no camera sees it. Copy each concern's "
    "text exactly. Never name a person's face or identity. Also list in `worn` each of those "
    "concepts that a person wears or holds (PPE, hats, hair nets, gloves, a tool in the hand), "
    "with the body region to look at: `head`, `hands` or `body`. Such things are small in a "
    "full camera frame, so Rulecam also looks for them on enlarged crops of that region of "
    "each person.\n"
    f"{DATA_NOT_INSTRUCTIONS_CLAUSE}"
)


class ConcernConcepts(BaseModel):
    """The concepts one concern needs."""

    concern: str
    concepts: list[str] = Field(default_factory=list)


class WornConcept(BaseModel):
    """A needed concept a person wears or holds, and the body region it is on."""

    concept: str
    region: CropRegion


class ConceptNeeds(BaseModel):
    """Spark's answer: the concepts each concern needs, and which of them people wear."""

    needs: list[ConcernConcepts] = Field(default_factory=list)
    worn: list[WornConcept] = Field(default_factory=list)


class ConceptPlan(BaseModel):
    """The cleaned answer: concepts per concern, and the region of each worn concept."""

    needs: dict[str, tuple[str, ...]]
    worn: dict[str, CropRegion] = Field(default_factory=dict)


def concepts_for_concerns(
    concerns: Sequence[Concern],
    inventory_concepts: Sequence[str],
    *,
    spark: StructuredLLM,
) -> dict[str, tuple[str, ...]]:
    """Ask Spark which concepts each concern needs a camera to see (`concept_needs(...).needs`).

    Args:
        concerns: The concerns from intake.
        inventory_concepts: Concepts the survey found.
        spark: The structured-output client.

    Returns:
        Concern text -> its cleaned concepts (at most 4), for every concern in input order. A
        concern Spark skipped, or named with different text, maps to `()`.
    """
    return concept_needs(concerns, inventory_concepts, spark=spark).needs


def concept_needs(
    concerns: Sequence[Concern],
    inventory_concepts: Sequence[str],
    *,
    spark: StructuredLLM,
) -> ConceptPlan:
    """Ask Spark which concepts each concern needs, and which of them people wear or hold.

    Args:
        concerns: The concerns from intake.
        inventory_concepts: Concepts the survey found.
        spark: The structured-output client.

    Returns:
        The concepts per concern (as `concepts_for_concerns`), and needed concept -> the body
        region to zoom into, for the needed concepts Spark said a person wears or holds.
    """
    texts = list(dict.fromkeys(c.text for c in concerns))
    if not texts:
        return ConceptPlan(needs={})
    data = {"concerns": texts, "concepts_in_view": list(dict.fromkeys(inventory_concepts))}
    result = spark.structured(
        model=NEEDS_MODEL,
        messages=[
            {"role": "system", "content": NEEDS_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
        schema=ConceptNeeds,
    )
    answered: dict[str, list[str]] = {}
    for need in result.needs:
        answered.setdefault(need.concern.strip(), []).extend(need.concepts)
    needs = {
        text: tuple(clean_concepts(answered.get(text, ()))[:MAX_CONCEPTS_PER_CONCERN])
        for text in texts
    }
    missing = [text for text, concepts in needs.items() if not concepts]
    if missing:
        _log.info("probe_needs_missing", n_missing=len(missing))
    needed = {c for concepts in needs.values() for c in concepts}
    worn: dict[str, CropRegion] = {}
    for item in result.worn:
        cleaned = clean_concepts([item.concept])
        if cleaned and cleaned[0] in needed:
            worn.setdefault(cleaned[0], item.region)
    return ConceptPlan(needs=needs, worn=worn)


class ProbeZoom:
    """Looks for a worn concept on enlarged crops of the people in a frame (crop-zoom).

    The people come from SAM (`person`) through the probe's own segmenter, so a recording
    segmenter keeps them (calibration reads standing people from those sightings), once per
    frame. Crop failures are logged and count as a miss: zoom never fails a probe.
    """

    def __init__(
        self,
        segmenter: ImageSegmenter,
        crops: CropSegmenter,
        *,
        max_people: int = MAX_ZOOM_PEOPLE,
    ) -> None:
        """Wrap the probe's segmenter and a crop segmenter.

        Args:
            segmenter: SAM 3.1 on single images (finds the people).
            crops: SAM 3.1 on crops of a frame (`perception.crop_zoom`).
            max_people: Most people per frame whose crops are checked (the largest first).
        """
        self._segmenter = segmenter
        self._crops = crops
        self._max_people = max_people
        self._people: dict[Path, tuple[BoxXYXY, ...]] = {}

    def find(
        self, frame: Path, concept: str, region: CropRegion, *, camera_id: str
    ) -> list[CropHit]:
        """The concept's detections on the region's crops of every person in `frame`.

        Args:
            frame: A JPEG or PNG frame.
            concept: The worn concept.
            region: The body region to crop.
            camera_id: The frame's camera.

        Returns:
            Hits in frame pixels; `[]` with nobody in the frame or when the crop call fails.
        """
        try:
            return self._find(frame, concept, region, camera_id)
        except Exception as exc:  # a zoom failure is a miss, never a failed probe
            _log.warning("probe_zoom_failed", camera_id=camera_id, concept=concept, error=str(exc))
            return []

    def _find(self, frame: Path, concept: str, region: CropRegion, camera_id: str) -> list[CropHit]:
        people = self._people_in(frame, camera_id)
        image = cv2.imread(str(frame), cv2.IMREAD_COLOR) if people else None
        if image is None:
            return []
        height, width = image.shape[:2]
        boxes = [
            crop.box_xyxy
            for idx, box in enumerate(people)
            for crop in person_crops(
                box, regions=(region,), width=width, height=height, person_idx=idx
            )
        ]
        if not boxes:
            return []
        return self._crops.segment_crops(frame.read_bytes(), boxes, [concept], camera_id=camera_id)

    def _people_in(self, frame: Path, camera_id: str) -> tuple[BoxXYXY, ...]:
        if frame not in self._people:
            found = self._segmenter.segment_image(frame, ZOOM_PERSON_CONCEPT, camera_id=camera_id)
            boxes = sorted(
                (d.mask.box_xyxy for d in found),
                key=lambda b: (b[2] - b[0]) * (b[3] - b[1]),
                reverse=True,
            )
            self._people[frame] = tuple(boxes[: self._max_people])
        return self._people[frame]


def probe_concept_on_camera(
    concept: str,
    camera_id: str,
    frames: Sequence[Path],
    *,
    segmenter: ImageSegmenter,
    seed: int,
    n_samples: int = DEFAULT_PROBE_FRAMES,
    zoom: ProbeZoom | None = None,
    region: CropRegion | None = None,
) -> Probe:
    """Measure how often SAM finds `concept` in one camera's frames.

    Args:
        concept: The concept noun phrase.
        camera_id: The camera.
        frames: The camera's frames, oldest first.
        segmenter: SAM 3.1 on single images.
        seed: Seed for frame sampling.
        n_samples: Frames to sample.
        zoom: Crop-zoom, tried on a frame where the full frame found nothing.
        region: The body region a worn concept is on; `None` means no zoom for it.

    Returns:
        The probe. With no frames it has `n_frames=0` and a zero rate and bound.
    """
    sampled = sample_frames(frames, n_samples, seed=seed)
    hits = zoom_hits = 0
    heights: list[float] = []
    for frame in sampled:
        detections = segmenter.segment_image(frame, concept, camera_id=camera_id)
        boxes = [d.mask.box_xyxy for d in detections]
        if not boxes and zoom is not None and region is not None:
            boxes = [h.box_xyxy for h in zoom.find(frame, concept, region, camera_id=camera_id)]
            zoom_hits += bool(boxes)
        hits += bool(boxes)
        heights.extend(h for box in boxes if (h := box[3] - box[1]) > 0)
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
    _log.info(
        "probe", concept=concept, camera_id=camera_id, hits=hits, zoom_hits=zoom_hits, n=n_frames
    )
    return probe


def probe_concepts(
    concepts: Sequence[str],
    camera_frames: Mapping[str, Sequence[Path]],
    *,
    segmenter: ImageSegmenter,
    seed: int,
    n_samples: int = DEFAULT_PROBE_FRAMES,
    zoom: ProbeZoom | None = None,
    worn: Mapping[str, CropRegion] | None = None,
) -> tuple[Probe, ...]:
    """Probe every concept on every camera.

    Args:
        concepts: Concept noun phrases (normalised and de-duplicated here).
        camera_frames: Camera id -> that camera's frames, oldest first.
        segmenter: SAM 3.1 on single images.
        seed: Seed for frame sampling.
        n_samples: Frames to sample per camera.
        zoom: Crop-zoom for worn concepts (see the module docstring).
        worn: Worn concept -> the body region to zoom into (`concept_needs(...).worn`).

    Returns:
        One probe per (concept, camera), concepts in first-seen order and cameras in id order.
    """
    regions = worn or {}
    return tuple(
        probe_concept_on_camera(
            concept,
            cam_id,
            frames,
            segmenter=segmenter,
            seed=seed,
            n_samples=n_samples,
            zoom=zoom,
            region=regions.get(concept),
        )
        for concept in clean_concepts(concepts)
        for cam_id, frames in sorted(camera_frames.items())
    )
