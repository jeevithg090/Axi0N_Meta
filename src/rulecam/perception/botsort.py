"""BoT-SORT with person re-ID between SAM keyframes (add-on 3.1): BoxMOT's `BotSort`.

ByteTrack matches boxes only, so two people who cross or stand close swap ids or fragment (416
tracks for 80 visits on CAVIAR). BoT-SORT (Aharon et al. 2022) adds an appearance cue: each
track keeps a smoothed re-ID vector, and a detection near a track (geometry gate) matches it
when their vectors are close, whichever cost is lower. The vectors are OSNet-AIN x1.0 (MSMT17),
computed by the SAM pod inside `/segment_image` on the boxes it just found (`reid_worker`), so
the API pod needs no model weights, only BoxMOT and CPU torch (the `reid` extra).

`KeyframeBotSort` configures BoxMOT 26's `BotSort` like the keyframe ByteTrack it replaces for
people (`keyframe_tracker`): capture-time Kalman prediction (`variable_dt`, keyframes 0.25 to
0.5 s apart and skipped chunks), buffered IoU (`BUFFER_RATIO`) with the same gate, the same two
confidence stages, a track named at its first detection, dropped after `lost_track_s` unseen,
and no camera-motion compensation (fixed cameras; no frame needed). BoxMOT's tuned
`appearance_thresh` is kept.

A detection without a vector (a keyframe served by hosted SAM, or a SAM pod without the re-ID
model) gets a random unit vector instead: it is far from every track's vector, so that match
falls back to geometry alone, as ByteTrack would.

`by_concept` routes person concepts to BoT-SORT and everything else to ByteTrack. Without BoxMOT
installed (`boxmot_available`), the runtime factory keeps ByteTrack for people too.
"""

from __future__ import annotations

import functools
import importlib.util
from collections.abc import Callable, Sequence
from typing import Any, Final, Protocol

import numpy as np

from rulecam.contracts import Detection
from rulecam.perception.keyframe_tracker import (
    BUFFER_RATIO,
    HIGH_CONFIDENCE,
    LOW_CONFIDENCE,
    MIN_IOU,
    TRACK_ACTIVATION,
    StreamTracker,
    TrackerFactory,
    bytetrack_stream,
    confidence_of,
)
from rulecam.perception.keyframes import Embedding

REID_DIM: Final = 512  # OSNet-AIN x1.0
PLACEHOLDER_SEED: Final = 0


def boxmot_available() -> bool:
    """Whether BoxMOT (the `reid` extra) is installed."""
    return importlib.util.find_spec("boxmot") is not None


def buffered_iou(a: np.ndarray, b: np.ndarray, ratio: float = BUFFER_RATIO) -> np.ndarray:
    """IoU of `xyxy` boxes after growing each by `ratio` of its size on every side (pure).

    Args:
        a: Boxes `(M, 4)`.
        b: Boxes `(N, 4)`.
        ratio: Growth per side, as a fraction of the box's width or height.

    Returns:
        The `(M, N)` buffered IoU (Yang et al. 2022), as `trackers.BIoU` computes it.
    """
    grown_a, grown_b = (
        _grow(np.asarray(a, float).reshape(-1, 4), ratio),
        _grow(np.asarray(b, float).reshape(-1, 4), ratio),
    )
    top_left = np.maximum(grown_a[:, None, :2], grown_b[None, :, :2])
    bottom_right = np.minimum(grown_a[:, None, 2:], grown_b[None, :, 2:])
    inter = np.prod(np.clip(bottom_right - top_left, 0.0, None), axis=2)
    area_a, area_b = _area(grown_a), _area(grown_b)
    union = area_a[:, None] + area_b[None, :] - inter
    iou: np.ndarray = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
    return iou


def _grow(boxes: np.ndarray, ratio: float) -> np.ndarray:
    size = boxes[:, 2:] - boxes[:, :2]
    return np.concatenate([boxes[:, :2] - ratio * size, boxes[:, 2:] + ratio * size], axis=1)


def _area(boxes: np.ndarray) -> np.ndarray:
    return np.prod(np.clip(boxes[:, 2:] - boxes[:, :2], 0.0, None), axis=1)


class BoxTrackerEngine(Protocol):
    """The tracker `BotSortStream` drives (`BoxmotBotSort`, or a fake in tests)."""

    def update(
        self, boxes: np.ndarray, scores: np.ndarray, embeddings: np.ndarray, timestamp_s: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Advance one keyframe; return the visible tracks' ids and their detection rows."""
        ...

    def alive(self) -> set[int]:
        """Ids of tracks still active or lost."""
        ...


class BotSortStream:
    """`StreamTracker` that feeds boxes, scores and re-ID vectors to BoT-SORT."""

    def __init__(
        self, engine: BoxTrackerEngine, *, width: int = REID_DIM, seed: int = PLACEHOLDER_SEED
    ) -> None:
        """Wrap the tracker.

        Args:
            engine: BoT-SORT (`BoxmotBotSort`).
            width: The re-ID vectors' length; vectors of another length count as missing.
            seed: Seeds the random placeholder vectors (reproducible runs).
        """
        self._engine = engine
        self._width = width
        self._rng = np.random.default_rng(seed)

    def step(
        self,
        detections: Sequence[Detection],
        embeddings: Sequence[Embedding | None],
        timestamp_s: float,
    ) -> list[tuple[int, int]]:
        """Match one keyframe's detections to tracks by place and appearance."""
        boxes = np.array([d.mask.box_xyxy for d in detections], dtype=np.float32).reshape(-1, 4)
        scores = np.clip([confidence_of(d) for d in detections], 0.0, 1.0).astype(np.float32)
        ids, rows = self._engine.update(boxes, scores, self.vectors(embeddings), timestamp_s)
        return [(int(r), int(i)) for i, r in zip(ids, rows, strict=True)]

    def alive(self) -> set[int]:
        """Ids of BoT-SORT's active and lost tracks."""
        return self._engine.alive()

    def vectors(self, embeddings: Sequence[Embedding | None]) -> np.ndarray:
        """The `(N, width)` vectors to send: the real ones, random unit vectors for the rest."""
        out = self._rng.standard_normal((len(embeddings), self._width)).astype(np.float32)
        for row, vector in enumerate(embeddings):
            if vector is not None and len(vector) == self._width:
                out[row] = vector
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return np.asarray(out / np.where(norms > 0, norms, 1.0), dtype=np.float32)


def new_botsort_stream(
    lost_track_s: float, *, low_confidence: float = LOW_CONFIDENCE
) -> StreamTracker:  # pragma: no cover - needs the `reid` extra; tests/perception/test_botsort.py
    """A BoT-SORT `StreamTracker` for one camera's people.

    Args:
        lost_track_s: Seconds a track may go undetected before it is dropped.
        low_confidence: The second stage's floor.

    Returns:
        The stream.
    """
    return BotSortStream(BoxmotBotSort(lost_track_s, low_confidence=low_confidence))


def by_concept(
    is_person: Callable[[str], bool],
    person: Callable[[float], StreamTracker] | None,
    other: TrackerFactory = bytetrack_stream,
) -> TrackerFactory:
    """A `TrackerFactory` that puts person concepts on `person` and the rest on `other`.

    Args:
        is_person: Whether a concept denotes a person (`rules.operators.is_person_like`).
        person: Builds a person tracker from `lost_track_s`; `None` keeps `other` for people.
        other: Everything else (ByteTrack).

    Returns:
        The factory for `KeyframeIdentities`.
    """

    def build(concept: str, lost_track_s: float) -> StreamTracker:
        if person is not None and is_person(concept):
            return person(lost_track_s)
        return other(concept, lost_track_s)

    return build


class BoxmotBotSort:  # pragma: no cover - needs the `reid` extra; tests/perception/test_botsort.py
    """`BoxTrackerEngine` over BoxMOT's `BotSort`, configured for keyframes."""

    def __init__(self, lost_track_s: float, *, low_confidence: float = LOW_CONFIDENCE) -> None:
        """Build the tracker.

        Args:
            lost_track_s: Seconds a track may go undetected before it is dropped.
            low_confidence: The second stage's floor.
        """
        self._tracker = keyframe_botsort_class()(
            lost_track_s=lost_track_s, low_confidence=low_confidence
        )

    def update(
        self, boxes: np.ndarray, scores: np.ndarray, embeddings: np.ndarray, timestamp_s: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Advance one keyframe with BoxMOT's canonical detections (vectors supplied)."""
        import torch  # type: ignore[import-not-found]  # noqa: PLC0415 - the `reid` extra
        from boxmot.structures import Boxes, Detections  # noqa: PLC0415

        detections = Detections(
            geometry=Boxes(torch.from_numpy(np.ascontiguousarray(boxes, dtype=np.float32))),
            scores=torch.from_numpy(np.ascontiguousarray(scores, dtype=np.float32)),
            class_ids=torch.zeros(len(scores), dtype=torch.int64),
            embeddings=torch.from_numpy(np.ascontiguousarray(embeddings, dtype=np.float32)),
        )
        tracks = self._tracker.update(detections, timestamp_s=timestamp_s)
        return tracks.track_ids.numpy(), tracks.detection_indices.numpy()

    def alive(self) -> set[int]:
        """Ids of BotSort's active and lost tracks."""
        tracker = self._tracker
        return {int(t.id) for t in (*tracker.active_tracks, *tracker.lost_stracks)}


@functools.cache
def keyframe_botsort_class() -> Any:  # pragma: no cover - needs the `reid` extra
    """BoxMOT's `BotSort`, subclassed for keyframes (built on first use: BoxMOT is optional)."""
    from boxmot import BotSort, BotSortConfig, KalmanConfig  # noqa: PLC0415

    gate = 1.0 - MIN_IOU

    class KeyframeBotSort(BotSort):  # type: ignore[misc, unused-ignore]  # Any without the extra
        """BoT-SORT with ByteTrack's keyframe geometry, first-detection ids and lost time."""

        def __init__(self, *, lost_track_s: float, low_confidence: float) -> None:
            super().__init__(
                BotSortConfig(
                    track_high_thresh=HIGH_CONFIDENCE,
                    track_low_thresh=low_confidence,
                    new_track_thresh=TRACK_ACTIVATION,
                    det_thresh=low_confidence,
                    match_thresh=gate,
                    second_match_thresh=gate,
                    unconfirmed_match_thresh=gate,
                    proximity_thresh=gate,
                    fuse_first_associate=False,
                    fuse_unconfirmed_associate=False,
                    use_cmc=False,
                ),
                kalman=KalmanConfig(variable_dt=True),
            )
            self._lost_track_s = lost_track_s
            self._seen_at: dict[int, float] = {}
            self._now = 0.0

        def update(self, detections: Any, frame: Any = None, **kwargs: Any) -> Any:
            self._now = float(kwargs["timestamp_s"])
            self._seen_at[self.frame_count + 1] = self._now  # the update's frame number
            return super().update(detections, frame, **kwargs)

        def association_similarity(self, boxes_a: Any, boxes_b: Any) -> np.ndarray:
            first, second = self._association_boxes(boxes_a), self._association_boxes(boxes_b)
            return buffered_iou(first, second)

        def _initialize_new_tracks(
            self, u_detections: Any, activated_stracks: list[Any], detections: Any
        ) -> None:
            before = len(activated_stracks)
            super()._initialize_new_tracks(u_detections, activated_stracks, detections)
            for track in activated_stracks[before:]:
                track.is_activated = True  # a confident SAM detection names a track at once

        def _update_track_states(self, removed_stracks: list[Any]) -> None:
            for track in self.lost_stracks:
                if self._now - self._seen_at.get(track.end_frame, self._now) > self._lost_track_s:
                    track.mark_removed()
                    removed_stracks.append(track)
            alive = [t.end_frame for t in (*self.active_tracks, *self.lost_stracks)]
            oldest = min(alive, default=self.frame_count)
            self._seen_at = {k: v for k, v in self._seen_at.items() if k >= oldest}

    return KeyframeBotSort
