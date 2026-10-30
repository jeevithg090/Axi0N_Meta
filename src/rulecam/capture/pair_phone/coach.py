"""Placement coach: per-concept SAM confidence on the phone's latest frame (task P1-09).

The single-frame segmenter is injected. The hosted SAM 3.1 client (P1-02) provides
`segment_image(frame, concept)`, so it satisfies `FrameSegmenter` as it is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from rulecam.contracts import Detection

HINT_MAX_CHARS = 140


class FrameSegmenter(Protocol):
    """Segments one concept in one still frame (one concept per request, as SAM 3.1 requires)."""

    def segment_image(self, frame: Path, concept: str) -> list[Detection]:
        """Return the detections of `concept` in `frame`; empty when nothing matches."""
        ...


class _Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CoachHello(_Message):
    """First message after the phone authenticates: which camera it is and what is checked."""

    type: Literal["hello"] = "hello"
    camera_id: str
    concepts: tuple[str, ...]


class CoachBars(_Message):
    """Signal bars: the best SAM confidence per concept on the latest frame (0 = not seen)."""

    type: Literal["bars"] = "bars"
    camera_id: str
    chunk_id: str
    bars: dict[str, float]


class CoachHint(_Message):
    """A one-line placement hint pushed by the planner (P2)."""

    type: Literal["hint"] = "hint"
    text: str = Field(max_length=HINT_MAX_CHARS)


class CoachStatus(_Message):
    """A short status line, e.g. while waiting for the first chunk."""

    type: Literal["status"] = "status"
    text: str


def concept_confidence(detections: Sequence[Detection]) -> float:
    """Return the best confidence among detections of one concept.

    SAM returns an empty completion when nothing matches, which gives 0.0. A mask without a
    score still means the concept is visible, so it counts as 1.0.
    """
    return max(
        (1.0 if d.mask.confidence is None else d.mask.confidence for d in detections),
        default=0.0,
    )


async def measure_bars(
    segmenter: FrameSegmenter, frame: Path, concepts: Sequence[str]
) -> dict[str, float]:
    """Segment every concept in one frame, concurrently, and return its signal bar.

    Args:
        segmenter: The single-frame segmenter (hosted SAM in production).
        frame: A still image of the camera's latest view.
        concepts: Concepts to check, one request each.

    Returns:
        `{concept: confidence}` rounded to 3 decimals, in the order given.
    """
    results = await asyncio.gather(
        *(asyncio.to_thread(segmenter.segment_image, frame, concept) for concept in concepts)
    )
    return {
        concept: round(concept_confidence(found), 3)
        for concept, found in zip(concepts, results, strict=True)
    }


def clean_hint(text: str) -> str:
    """Collapse a hint to one line of at most `HINT_MAX_CHARS` characters."""
    return " ".join(text.split())[:HINT_MAX_CHARS]
