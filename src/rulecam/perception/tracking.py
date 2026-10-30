"""Cross-chunk tracking with identity hand-off (task P1-04).

Within a chunk SAM's `object_id` is stable. Across chunks, identity is handed off in two steps:

1. **Box re-prompt.** The next chunk's first frame is prompted with the previous chunk's final
   boxes (at most 16 per call). An object SAM returns for prompt `k` continues the track whose box
   was prompt `k`.
2. **IoU fallback.** Objects from the chunk's concept pass that did not come from a re-prompt
   are matched to the remaining previous tracks with the Hungarian algorithm on box IoU
   (IoU >= 0.3). Unmatched objects start new tracks.

Concept-pass objects that duplicate a re-prompted object (IoU >= 0.3 on a shared frame) are
dropped, so one person never becomes two tracks.

Track IDs are opaque (`{camera_id}:{concept}:{8 hex}`) and never an analytics key (rule R13).
The foot point is the bottom-centre of the mask's lowest 5% of pixels, falling back to the box's
bottom-centre; `foot_m` is filled only when a pixel-to-metre mapping is given (P1-05).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

import numpy as np
from scipy.optimize import linear_sum_assignment  # type: ignore[import-untyped]

from rulecam.bus import Bus, Topic
from rulecam.contracts import Chunk, Detection, Frozen, Segmenter, Track, TrackPoint, TrackStore
from rulecam.contracts.core import BoxXYXY, PointXY
from rulecam.log import get_logger
from rulecam.perception.masks import foot_point

_log = get_logger(__name__)

IOU_THRESHOLD = 0.3
MAX_PROMPTS_PER_CALL = 16

PxToM = Callable[[PointXY], PointXY]


class TracksUpdated(Frozen):
    """Payload of `tracks.updated`: new track points exist for `camera_id` in `[t0, t1]`."""

    camera_id: str
    t0: datetime
    t1: datetime


def box_iou(a: BoxXYXY, b: BoxXYXY) -> float:
    """Return the intersection-over-union of two (x1, y1, x2, y2) boxes, in [0, 1]."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def match_by_iou(
    previous: Sequence[BoxXYXY], current: Sequence[BoxXYXY], threshold: float = IOU_THRESHOLD
) -> list[tuple[int, int]]:
    """Match boxes one-to-one with the Hungarian algorithm, maximising total IoU.

    Args:
        previous: Boxes of the tracks to continue.
        current: Boxes of the candidate objects.
        threshold: Minimum IoU for a pair to count as a match.

    Returns:
        `(previous_index, current_index)` pairs with IoU >= `threshold`, sorted.
    """
    if not previous or not current:
        return []
    iou = np.array([[box_iou(p, c) for c in current] for p in previous])
    rows, cols = linear_sum_assignment(iou, maximize=True)
    return sorted(
        (int(r), int(c)) for r, c in zip(rows, cols, strict=True) if iou[r, c] >= threshold
    )


def group_by_object(detections: Iterable[Detection]) -> dict[int, list[Detection]]:
    """Group detections by `object_id`, each group sorted by frame (first one per frame kept)."""
    grouped: dict[int, dict[int, Detection]] = {}
    for det in detections:
        grouped.setdefault(det.object_id, {}).setdefault(det.frame_idx, det)
    return {oid: [frames[f] for f in sorted(frames)] for oid, frames in sorted(grouped.items())}


def prompt_batches(
    boxes: Sequence[BoxXYXY], size: int = MAX_PROMPTS_PER_CALL
) -> list[dict[int, BoxXYXY]]:
    """Split boxes into box-prompt dicts of at most `size`, with prompt ids 1..n per batch.

    Args:
        boxes: Boxes in track order.
        size: Largest batch.

    Returns:
        One `{prompt_id: box}` dict per call; batch `b`'s prompt `k` is `boxes[b * size + k - 1]`.
    """
    return [
        {k + 1: box for k, box in enumerate(boxes[start : start + size])}
        for start in range(0, len(boxes), size)
    ]


def _box_at(detections: Sequence[Detection], frame_idx: int) -> BoxXYXY | None:
    return next((d.mask.box_xyxy for d in detections if d.frame_idx == frame_idx), None)


def _overlap_iou(a: Sequence[Detection], b: Sequence[Detection]) -> float:
    """IoU of two objects on their earliest shared frame (0 when they share none)."""
    shared = sorted({d.frame_idx for d in a} & {d.frame_idx for d in b})
    if not shared:
        return 0.0
    box_a, box_b = _box_at(a, shared[0]), _box_at(b, shared[0])
    return box_iou(box_a, box_b) if box_a and box_b else 0.0


@dataclass(frozen=True)
class _Live:
    """A track that was visible at the end of the previous chunk."""

    track_id: str
    box: BoxXYXY


@dataclass(frozen=True)
class _ConceptState:
    chunk_end: datetime
    live: tuple[_Live, ...]


def _new_suffix() -> str:
    return uuid4().hex[:8]


class Tracker:
    """Turns per-chunk SAM detections into tracks with identities that survive chunk borders."""

    def __init__(
        self,
        segmenter: Segmenter,
        *,
        store: TrackStore | None = None,
        bus: Bus | None = None,
        px_to_m: PxToM | None = None,
        reprompt: bool = True,
        iou_threshold: float = IOU_THRESHOLD,
        max_gap_s: float = 1.0,
        handoff_window_s: float = 0.5,
        new_suffix: Callable[[], str] = _new_suffix,
    ) -> None:
        """Create a tracker.

        Args:
            segmenter: Any `Segmenter` (hosted, self-hosted or a fake).
            store: Where `process_chunk` appends tracks; `None` keeps them in memory only.
            bus: Bus for `tracks.updated`; `None` publishes nothing.
            px_to_m: Image-pixel to ground-metre mapping from the camera's calibration (P1-05),
                e.g. `functools.partial(px_to_m_one, calibration)`; `None` leaves `foot_m` empty.
            reprompt: Whether the segmenter supports box re-prompts; if not, only IoU is used.
            iou_threshold: Minimum IoU for the fallback and for dropping duplicates.
            max_gap_s: Largest gap between chunks that still hands identities off.
            handoff_window_s: A track is handed off only if seen in the chunk's last
                `handoff_window_s` seconds.
            new_suffix: Generates the random part of new track IDs (injected in tests).
        """
        self._segmenter = segmenter
        self._store = store
        self._bus = bus
        self._px_to_m = px_to_m
        self._reprompt = reprompt
        self._iou_threshold = iou_threshold
        self._max_gap = timedelta(seconds=max_gap_s)
        self._handoff_window_s = handoff_window_s
        self._new_suffix = new_suffix
        self._state: dict[tuple[str, str], _ConceptState] = {}

    def process_chunk(self, chunk: Chunk, concepts: Iterable[str]) -> list[Track]:
        """Track every concept in a chunk, store the tracks and publish `tracks.updated`.

        Args:
            chunk: The next chunk from one camera.
            concepts: Concepts the active rules need; each is one segmenter call (plus re-prompts).

        Returns:
            This chunk's tracks (points from this chunk only), across all concepts.
        """
        tracks = [t for concept in dict.fromkeys(concepts) for t in self.update(chunk, concept)]
        if self._store is not None and tracks:
            self._store.append(tracks)
        if self._bus is not None:
            self._bus.publish(
                Topic.TRACKS_UPDATED,
                TracksUpdated(camera_id=chunk.camera_id, t0=chunk.start_ts, t1=_chunk_end(chunk)),
            )
        return tracks

    def update(self, chunk: Chunk, concept: str) -> list[Track]:
        """Track one concept in one chunk, continuing identities from the previous chunk.

        Args:
            chunk: The chunk.
            concept: The concept noun phrase.

        Returns:
            Tracks with this chunk's points, sorted by first timestamp then track ID.
        """
        key = (chunk.camera_id, concept)
        live = self._live_for(key, chunk)
        objects = group_by_object(self._segmenter.segment(chunk, concept))
        continued = self._reprompted(chunk, concept, live) if self._reprompt else {}
        fresh = self._drop_duplicates(objects, continued)
        assigned = self._iou_fallback(live, continued, fresh, chunk)
        tracks = [self._track(tid, chunk, concept, dets) for tid, dets in continued.items()]
        tracks += [self._track(tid, chunk, concept, dets) for tid, dets in assigned]
        tracks.sort(key=lambda t: (t.points[0].ts, t.track_id))
        self._state[key] = _ConceptState(
            chunk_end=_chunk_end(chunk), live=self._still_live(chunk, tracks)
        )
        _log.info(
            "tracks_updated",
            camera_id=chunk.camera_id,
            chunk_id=chunk.chunk_id,
            concept=concept,
            n_tracks=len(tracks),
            n_reprompted=len(continued),
        )
        return tracks

    # --- hand-off steps ---------------------------------------------------------------------------

    def _live_for(self, key: tuple[str, str], chunk: Chunk) -> tuple[_Live, ...]:
        state = self._state.get(key)
        if state is None:
            return ()
        gap = chunk.start_ts - state.chunk_end
        return state.live if -self._max_gap <= gap <= self._max_gap else ()

    def _reprompted(
        self, chunk: Chunk, concept: str, live: Sequence[_Live]
    ) -> dict[str, list[Detection]]:
        continued: dict[str, list[Detection]] = {}
        for start, batch in zip(
            range(0, len(live), MAX_PROMPTS_PER_CALL),
            prompt_batches([t.box for t in live]),
            strict=True,
        ):
            found = group_by_object(self._segmenter.segment(chunk, concept, box_prompts=batch))
            for prompt_id in batch:
                if prompt_id in found:
                    continued[live[start + prompt_id - 1].track_id] = found[prompt_id]
        return continued

    def _drop_duplicates(
        self, objects: Mapping[int, list[Detection]], continued: Mapping[str, list[Detection]]
    ) -> list[list[Detection]]:
        candidates = list(objects.values())
        taken = set()
        for dets in continued.values():
            scores = [
                (_overlap_iou(dets, c), i) for i, c in enumerate(candidates) if i not in taken
            ]
            best = max(scores, default=(0.0, -1))
            if best[0] >= self._iou_threshold:
                taken.add(best[1])
        return [c for i, c in enumerate(candidates) if i not in taken]

    def _iou_fallback(
        self,
        live: Sequence[_Live],
        continued: Mapping[str, list[Detection]],
        fresh: Sequence[list[Detection]],
        chunk: Chunk,
    ) -> list[tuple[str, list[Detection]]]:
        waiting = [t for t in live if t.track_id not in continued]
        early_frames = round(self._handoff_window_s * chunk.fps)
        eligible = [i for i, dets in enumerate(fresh) if dets[0].frame_idx <= early_frames]
        pairs = match_by_iou(
            [t.box for t in waiting],
            [fresh[i][0].mask.box_xyxy for i in eligible],
            self._iou_threshold,
        )
        matched = {eligible[c]: waiting[r].track_id for r, c in pairs}
        return [
            (matched.get(i) or self._new_id(chunk.camera_id, dets[0].concept), dets)
            for i, dets in enumerate(fresh)
        ]

    def _still_live(self, chunk: Chunk, tracks: Sequence[Track]) -> tuple[_Live, ...]:
        last_frame = chunk.n_frames - 1 - round(self._handoff_window_s * chunk.fps)
        return tuple(
            _Live(track_id=t.track_id, box=t.points[-1].box_xyxy)
            for t in tracks
            if t.points[-1].frame_idx >= last_frame
        )

    # --- building tracks --------------------------------------------------------------------------

    def _new_id(self, camera_id: str, concept: str) -> str:
        return f"{camera_id}:{concept}:{self._new_suffix()}"

    def _track(self, track_id: str, chunk: Chunk, concept: str, dets: Sequence[Detection]) -> Track:
        return Track(
            track_id=track_id,
            camera_id=chunk.camera_id,
            concept=concept,
            points=tuple(self._point(d) for d in dets),
        )

    def _point(self, det: Detection) -> TrackPoint:
        foot = foot_point(det.mask.rle, det.mask.box_xyxy)
        return TrackPoint(
            ts=det.ts,
            frame_idx=det.frame_idx,
            box_xyxy=det.mask.box_xyxy,
            foot_px=foot,
            foot_m=self._px_to_m(foot) if self._px_to_m is not None else None,
            confidence=det.mask.confidence,
        )


def _chunk_end(chunk: Chunk) -> datetime:
    return chunk.start_ts + timedelta(seconds=chunk.n_frames / chunk.fps)
