"""Tier 0 verification: crop-zoom SAM 3.1 on the rule's PPE regions, as context for Glimmer.

Add-on 3.3. A rule with a `without` predicate ("a person without a hair net", "hands without
gloves") is about something small: a hair net is a few dozen pixels in the full frame, and Glimmer
sees the strip scaled down to 640 or 960 px. Before Glimmer is asked, `CropZoomCheck` takes a
few keyframes of the episode at full resolution, cuts out the subject's head, hands or body (the
predicate's region, `perception.crop_zoom.WITHOUT_REGIONS`; from the subject's pose when known),
and asks SAM 3.1 for each item the rule checks on those enlarged crops. What it found, per item
and region (crops checked, crops with a hit, best confidence), becomes a few plain lines in
Glimmer's prompt (`GlimmerVerifier(context=...)`). They are evidence for the model to weigh,
never a verdict: a detector can miss an item or mistake a cap for a hard hat, and Glimmer still
judges the frames.

Nothing else changes: rules without a `without` predicate add no lines and cost nothing, and any
failure (no frames, no track, a GPU error) is logged and gives no lines, so Tier 1 runs exactly as
it would without Tier 0. The check is built only outside replay mode (`runtime.factory`): its
lines change Glimmer's prompt, and so the replay cache's key.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final, Protocol

import numpy as np
from numpy.typing import NDArray

from rulecam.contracts import CandidateEvent, RuleIR, Track, TrackPoint, TrackStore
from rulecam.contracts.core import BoxXYXY, Keypoint
from rulecam.contracts.rule_ir import Without
from rulecam.log import get_logger
from rulecam.perception.crop_zoom import (
    WITHOUT_REGIONS,
    CropRegion,
    CropSegmenter,
    encode_jpeg,
    person_crops,
)
from rulecam.rules.ir import parse_ref, walk

_log = get_logger(__name__)

TIER0_FRAMES: Final = 3
TRACK_TOLERANCE_S: Final = 0.6
MAX_SUBJECTS: Final = 2  # people checked when the trace names no subject track

Frame = NDArray[np.uint8]


class EpisodeFrames(Protocol):
    """Full-resolution frames of a candidate's episode (`runtime.strips.EpisodeFrames`)."""

    def frames(self, candidate: CandidateEvent, n: int) -> list[tuple[datetime, Frame]]:
        """Up to `n` BGR frames spread over the episode, with their times, oldest first."""
        ...


class PoseEstimator(Protocol):
    """COCO-17 keypoints for one person (`pose.keypoints.RtmposeOnnx`)."""

    def estimate(self, frame: Frame, box_xyxy: BoxXYXY) -> tuple[Keypoint, ...]:
        """Return 17 keypoints in image pixels for the person inside `box_xyxy`."""
        ...


@dataclass(frozen=True)
class ZoomItem:
    """One thing a rule checks a person for: the person concept, the item, its SAM prompt."""

    subject: str
    item: str
    prompt: str
    region: CropRegion


@dataclass
class _Tally:
    crops: int = 0
    hits: int = 0
    best: float = 0.0
    frames: set[int] = field(default_factory=set)
    hit_frames: set[int] = field(default_factory=set)


def zoom_items(rule: RuleIR) -> tuple[ZoomItem, ...]:
    """The items a rule's `without` predicates check people for, each once.

    Args:
        rule: The rule.

    Returns:
        One item per distinct (subject, item, region), in predicate order.
    """
    items: dict[tuple[str, str, str], ZoomItem] = {}
    for _, node in walk(rule.predicate):
        if not isinstance(node, Without):
            continue
        subject, item = parse_ref(node.subject)[0], parse_ref(node.item)[0]
        spec = rule.concepts.get(item)
        prompt = spec.prompt if spec is not None else item.replace("_", " ")
        region = WITHOUT_REGIONS[node.region]
        items.setdefault((subject, item, region), ZoomItem(subject, item, prompt, region))
    return tuple(items.values())


def nearest_point(track: Track, ts: datetime) -> TrackPoint | None:
    """The track's point closest to `ts`, if within `TRACK_TOLERANCE_S`."""
    best = min(track.points, key=lambda p: abs((p.ts - ts).total_seconds()), default=None)
    if best is None or abs((best.ts - ts).total_seconds()) > TRACK_TOLERANCE_S:
        return None
    return best


def subject_tracks(
    candidate: CandidateEvent, tracks: Sequence[Track], concepts: set[str]
) -> list[Track]:
    """The people to check: the trace's subject, else the largest people of the rule's concepts."""
    subject_id = str(candidate.predicate_trace.get("subject", ""))
    named = [t for t in tracks if t.track_id == subject_id]
    if named:
        return named
    people = [t for t in tracks if t.concept in concepts and t.points]
    people.sort(key=lambda t: max(p.box_xyxy[3] - p.box_xyxy[1] for p in t.points), reverse=True)
    return people[:MAX_SUBJECTS]


def finding_lines(items: Sequence[ZoomItem], tallies: dict[ZoomItem, _Tally], n: int) -> list[str]:
    """Glimmer's context lines for the tallies (empty when no crop was checked)."""
    checked = [(item, tallies[item]) for item in items if tallies[item].crops]
    if not checked:
        return []
    regions = ", ".join(dict.fromkeys(i.region for i, _ in checked))
    lines = [
        "Close-up check (Tier 0, a detector's hints, not a verdict): Rulecam cut out and enlarged "
        f"the subject's {regions} at {n} keyframes of "
        "this clip at full camera resolution and asked SAM 3.1 for each item the rule checks. "
        "Weigh this with what the frames show; a detector can miss an item or mistake a "
        "similar one."
    ]
    for item, tally in checked:
        where = f"{tally.crops} {item.region} crop{'s' if tally.crops != 1 else ''}"
        if tally.hits:
            lines.append(
                f"- '{item.prompt}' ({item.region}): found in {tally.hits} of {where}, at "
                f"{len(tally.hit_frames)} of {len(tally.frames)} keyframes, best confidence "
                f"{tally.best:.2f}."
            )
        else:
            lines.append(f"- '{item.prompt}' ({item.region}): not found in any of {where}.")
    return lines


class CropZoomCheck:
    """Tier 0: crop-zoom findings for a candidate, as extra lines for Glimmer's prompt."""

    def __init__(
        self,
        *,
        frames: EpisodeFrames,
        store: TrackStore,
        crops: CropSegmenter,
        pose: PoseEstimator | None = None,
        n_frames: int = TIER0_FRAMES,
    ) -> None:
        """Create the check.

        Args:
            frames: Full-resolution keyframes of an episode.
            store: The track store (the subject's boxes and keypoints).
            crops: SAM 3.1 on crops (`perception.crop_zoom`).
            pose: Pose for a subject without keypoints, so hand crops follow the wrists.
            n_frames: Keyframes checked per candidate.
        """
        self._frames = frames
        self._store = store
        self._crops = crops
        self._pose = pose
        self._n = n_frames

    def __call__(self, candidate: CandidateEvent, rule: RuleIR) -> tuple[str, ...]:
        """The findings as prompt lines; `()` for a rule without `without`, or on any failure."""
        items = zoom_items(rule)
        if not items:
            return ()
        try:
            lines = self._check(candidate, items)
        except Exception as exc:  # Tier 0 is optional context: Glimmer runs without it
            _log.warning(
                "tier0_failed",
                event_id=candidate.event_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return ()
        _log.info("tier0_findings", event_id=candidate.event_id, lines=len(lines))
        return tuple(lines)

    def _check(self, candidate: CandidateEvent, items: Sequence[ZoomItem]) -> list[str]:
        tolerance = timedelta(seconds=TRACK_TOLERANCE_S)
        start = min(candidate.ts_start, candidate.ts_end - timedelta(seconds=2.0))
        tracks = self._store.window(
            candidate.camera_id, start - tolerance, candidate.ts_end + tolerance
        )
        people = subject_tracks(candidate, tracks, {i.subject for i in items})
        frames = self._frames.frames(candidate, self._n) if people else []
        tallies = {item: _Tally() for item in items}
        for k, (ts, frame) in enumerate(frames):
            self._check_frame(
                k,
                ts,
                frame,
                people=people,
                items=items,
                tallies=tallies,
                camera=candidate.camera_id,
            )
        return finding_lines(items, tallies, len(frames))

    def _check_frame(
        self,
        k: int,
        ts: datetime,
        frame: Frame,
        *,
        people: Sequence[Track],
        items: Sequence[ZoomItem],
        tallies: dict[ZoomItem, _Tally],
        camera: str,
    ) -> None:
        height, width = frame.shape[:2]
        image = encode_jpeg(frame)
        for region in dict.fromkeys(i.region for i in items):
            wanted = [i for i in items if i.region == region]
            boxes: list[BoxXYXY] = []
            for track in people:
                point = nearest_point(track, ts)
                if point is None:
                    continue
                keypoints = point.keypoints
                if keypoints is None and self._pose is not None and region == "hands":
                    keypoints = self._pose.estimate(frame, point.box_xyxy)
                crops = person_crops(
                    point.box_xyxy,
                    regions=(region,),
                    width=width,
                    height=height,
                    keypoints=keypoints,
                )
                boxes.extend(c.box_xyxy for c in crops)
            if not boxes:
                continue
            prompts = list(dict.fromkeys(i.prompt for i in wanted))
            hits = self._crops.segment_crops(image, boxes, prompts, camera_id=camera)
            for item in wanted:
                tally = tallies[item]
                tally.crops += len(boxes)
                tally.frames.add(k)
                found = [h for h in hits if h.concept == item.prompt]
                tally.hits += len({h.crop_idx for h in found})
                if found:
                    tally.hit_frames.add(k)
                    tally.best = max(tally.best, *(h.confidence or 0.0 for h in found))


__all__ = [
    "TIER0_FRAMES",
    "CropZoomCheck",
    "EpisodeFrames",
    "ZoomItem",
    "finding_lines",
    "subject_tracks",
    "zoom_items",
]
