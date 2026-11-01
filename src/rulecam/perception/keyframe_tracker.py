"""Image-mode runtime tracking: SAM 3.1 keyframes joined into tracks by ByteTrack (P1-03, WS2b).

`KeyframeTracker` is the image-mode `ChunkTracker` of the runtime loop, a drop-in for
`perception.tracking.Tracker` (video mode):

1. `segment(chunk, concepts)` (stateless, several chunks of a camera at once): picks the chunk's
   keyframes (`keyframes.keyframe_indices`, the camera's rate), decodes them once, and asks the
   `KeyframeSegmenter` (self-hosted, hosted while it fails) for every dynamic concept on them.
   Static concepts (sink, door, rack) come from the `StaticCache`, filled from one keyframe per
   plan and camera, and are spread over every keyframe.
2. `stitch(chunk, segmented)` (in chunk order): feeds each concept's keyframe detections, frame
   by frame with their capture times, to that camera's and concept's ByteTrack, which keeps
   running across chunks, so a track id survives chunk borders without any extra SAM call.

**Tracker.** [Roboflow `trackers`](https://github.com/roboflow/trackers) (Apache-2.0) ByteTrack:
a Kalman filter per track predicts where it is at the next keyframe, then high-confidence
detections are matched by IoU (Hungarian), low-confidence ones only to the tracks left over.
Its timestamp mode scales the prediction by the real gap, so keyframes 0.25 to 0.5 s apart, and a
skipped chunk, are handled. Between keyframes 0.5 s apart a walking person (1.4 m/s, about 0.5 m
wide) moves more than its own width, so plain IoU would never match its new box before the filter
knows its speed; the association uses `trackers`' buffered IoU (`BIoU`, Yang et al. 2022, each box
grown by `BUFFER_RATIO` of its size on every side), which keeps that overlap at about 0.18. A
track lost for `LOST_TRACK_S` is dropped; a later detection starts a new one. `trackers` only
names a track at its second detection; `_ByteTrack` names it at its first (a SAM detection above
the threshold is already a confident observation), so no keyframe of an object is lost.

Tracks keep the video path's contracts: ids `{camera_id}:{concept}:{8 hex}` (opaque, never an
analytics key, rule R13), one `Track` per id per chunk with this chunk's points only, foot points
from the box (or mask), `foot_m` with a calibration. The rules engine interpolates between
keyframes (at most `rules.timeline.HOLD_S` apart), so `moving`, speeds and operator exclusion
work on keyframe tracks as they did on every-frame tracks.

**Cost.** `take_chunk` / `totals` (the loop's `SamUsage`) count SAM requests and the bill in
hosted SAM *video-frame* units, which the loop's budget is in: an image costs $2.50 per 1,000,
12.5 video frames at $0.20 per 1,000. A batch served by our GPU pod bills nothing. `estimate` is
what the loop reserves against the budget before a chunk's bill is known.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, override
from uuid import uuid4

import numpy as np
import supervision as sv
from trackers import BIoU, ByteTrackTracker
from trackers.core.bytetrack.tracklet import ByteTrackTracklet

from rulecam.bus import Bus, Topic
from rulecam.clients.meta_hosted.sam_cost import USD_PER_IMAGE, USD_PER_VIDEO_FRAME
from rulecam.contracts import Chunk, Detection, Track, TrackStore
from rulecam.log import get_logger
from rulecam.perception.keyframes import (
    FrameDecoder,
    Keyframe,
    KeyframeBatch,
    KeyframeRates,
    KeyframeSegmenter,
    decode_keyframes,
    frame_ts,
    keyframe_indices,
)
from rulecam.perception.router import MAX_OPEN_CHUNKS, StageRecorder
from rulecam.perception.static_cache import StaticCache
from rulecam.perception.tracking import PxToM, SegmentedChunk, TracksUpdated, track_point

_log = get_logger(__name__)

IMAGE_FRAME_EQUIVALENT = USD_PER_IMAGE / USD_PER_VIDEO_FRAME  # 12.5 video frames per image
LOST_TRACK_S = 2.0  # a track unseen this long is dropped
MIN_IOU = 0.1  # ByteTrack's association threshold (after the Kalman prediction)
BUFFER_RATIO = 0.5  # boxes grow by half their size each side before IoU (buffered IoU)
HIGH_CONFIDENCE = 0.5  # first-stage detections; below it they only extend existing tracks
TRACK_ACTIVATION = 0.5  # the confidence a detection needs to start a track
TRACKERS_REFERENCE_FPS = 30.0  # `trackers` counts `lost_track_buffer` in 30 fps frames


class _ByteTrack(ByteTrackTracker):
    """`trackers` ByteTrack that gives a new track its id on its first detection."""

    @override
    def _spawn_new_tracks(
        self,
        detection_boxes: np.ndarray,
        confidences: np.ndarray,
        unmatched_high_local: list[int],
        high_indices: np.ndarray,
        out_det_indices: list[int],
        out_tracker_ids: list[int],
    ) -> None:
        for local in unmatched_high_local:
            index = int(high_indices[local])
            out_det_indices.append(index)
            if float(confidences[index]) < self.track_activation_threshold:
                out_tracker_ids.append(-1)
                continue
            tracklet = ByteTrackTracklet(
                initial_bbox=detection_boxes[index],
                state_estimator_class=self.state_estimator_class,
            )
            tracklet.tracker_id = self._allocate_tracker_id()
            self.tracks.append(tracklet)
            out_tracker_ids.append(tracklet.tracker_id)


def new_bytetrack(lost_track_s: float = LOST_TRACK_S) -> ByteTrackTracker:
    """A ByteTrack for one camera and concept, configured for SAM keyframes.

    Args:
        lost_track_s: Seconds a track may go undetected before it is dropped.

    Returns:
        The tracker; feed it with `update(detections, timestamp=seconds)`.
    """
    return _ByteTrack(
        lost_track_buffer=max(0, round(lost_track_s * TRACKERS_REFERENCE_FPS)),
        frame_rate=TRACKERS_REFERENCE_FPS,
        track_activation_threshold=TRACK_ACTIVATION,
        minimum_consecutive_frames=1,
        minimum_iou_threshold=MIN_IOU,
        high_conf_det_threshold=HIGH_CONFIDENCE,
        iou=BIoU(buffer_ratio=BUFFER_RATIO),
    )


def _new_suffix() -> str:
    return uuid4().hex[:8]


@dataclass
class _Stream:
    """One camera's and concept's ByteTrack, and the track ids it has handed out."""

    tracker: ByteTrackTracker
    ids: dict[int, str] = field(default_factory=dict)
    last_s: float | None = None


def _frames(detections: Iterable[Detection]) -> dict[int, list[Detection]]:
    by_frame: dict[int, list[Detection]] = {}
    for det in detections:
        by_frame.setdefault(det.frame_idx, []).append(det)
    return by_frame


def _as_sv(detections: Sequence[Detection]) -> sv.Detections:
    if not detections:
        return sv.Detections.empty()
    return sv.Detections(
        xyxy=np.array([d.mask.box_xyxy for d in detections], dtype=float).reshape(-1, 4),
        confidence=np.array(
            [1.0 if d.mask.confidence is None else d.mask.confidence for d in detections]
        ),
        data={"row": np.arange(len(detections))},
    )


class KeyframeIdentities:
    """Stable track ids across keyframes and chunks: one ByteTrack per camera and concept."""

    def __init__(
        self,
        *,
        lost_track_s: float = LOST_TRACK_S,
        new_suffix: Callable[[], str] = _new_suffix,
        new_tracker: Callable[[float], ByteTrackTracker] = new_bytetrack,
    ) -> None:
        """Create empty state.

        Args:
            lost_track_s: Seconds a track may go undetected before it is dropped.
            new_suffix: Generates the random part of new track ids (injected in tests).
            new_tracker: Builds a tracker (injected in tests).
        """
        self._lost_track_s = lost_track_s
        self._new_suffix = new_suffix
        self._new_tracker = new_tracker
        self._streams: dict[tuple[str, str], _Stream] = {}

    def assign(
        self,
        camera_id: str,
        concept: str,
        keyframes: Sequence[tuple[int, datetime]],
        detections: Iterable[Detection],
    ) -> dict[str, list[Detection]]:
        """Track one concept through one chunk's keyframes, in time order.

        Call for a camera's chunks in order. A keyframe without detections still advances the
        tracker (its tracks age). A keyframe not after the previous one restarts the stream.

        Args:
            camera_id: The camera.
            concept: The concept.
            keyframes: `(frame_idx, ts)` of every keyframe of the chunk, in time order.
            detections: The concept's detections on those keyframes.

        Returns:
            Track id -> its detections in this chunk, by frame.
        """
        stream = self._stream(camera_id, concept)
        by_frame = _frames(detections)
        assigned: dict[str, list[Detection]] = {}
        for frame_idx, ts in keyframes:
            seconds = ts.timestamp()
            if stream.last_s is not None and seconds <= stream.last_s:
                _log.warning("keyframe_tracker_restart", camera_id=camera_id, concept=concept)
                stream = self._restart(camera_id, concept)
            stream.last_s = seconds
            frame = by_frame.get(frame_idx, [])
            tracked = stream.tracker.update(_as_sv(frame), timestamp=seconds)
            rows = tracked.data.get("row", np.array([], dtype=int))
            ids = tracked.tracker_id if tracked.tracker_id is not None else np.array([], dtype=int)
            for row, tracker_id in zip(rows, ids, strict=True):
                if int(tracker_id) < 0:
                    continue  # an unmatched low-confidence detection: not a track
                track_id = self._track_id(stream, camera_id, concept, int(tracker_id))
                assigned.setdefault(track_id, []).append(frame[int(row)])
        self._forget_dropped(stream)
        return assigned

    def _stream(self, camera_id: str, concept: str) -> _Stream:
        stream = self._streams.get((camera_id, concept))
        return stream if stream is not None else self._restart(camera_id, concept)

    def _restart(self, camera_id: str, concept: str) -> _Stream:
        stream = _Stream(tracker=self._new_tracker(self._lost_track_s))
        self._streams[(camera_id, concept)] = stream
        return stream

    def _track_id(self, stream: _Stream, camera_id: str, concept: str, tracker_id: int) -> str:
        track_id = stream.ids.get(tracker_id)
        if track_id is None:
            track_id = stream.ids[tracker_id] = f"{camera_id}:{concept}:{self._new_suffix()}"
        return track_id

    @staticmethod
    def _forget_dropped(stream: _Stream) -> None:
        alive = {int(t.tracker_id) for t in stream.tracker.tracks}
        for tracker_id in [i for i in stream.ids if i not in alive]:
            del stream.ids[tracker_id]


class _FirstKeyframe:
    """The static cache's `Segmenter` in image mode: one keyframe (frame 0) per cache miss."""

    def __init__(self, owner: KeyframeTracker) -> None:
        self._owner = owner

    def segment(
        self,
        chunk: Chunk,
        concept: str,
        *,
        box_prompts: dict[int, tuple[float, float, float, float]] | None = None,
        negative_points: list[tuple[int, float, float]] | None = None,  # noqa: ARG002 - protocol
        direction: Literal["forward", "backward"] = "forward",  # noqa: ARG002 - protocol
        include_confidence: bool = True,  # noqa: ARG002 - protocol
    ) -> list[Detection]:
        if box_prompts:
            return []
        frames = self._owner.decode(chunk, (0,))
        return list(self._owner.call(chunk, frames, (concept,)).detections[concept])


class KeyframeTracker:
    """Image-mode `ChunkTracker`: SAM 3.1 keyframes, static cache and ByteTrack identities."""

    def __init__(
        self,
        keyframes: KeyframeSegmenter,
        *,
        rates: KeyframeRates | None = None,
        store: TrackStore | None = None,
        bus: Bus | None = None,
        px_to_m: PxToM | None = None,
        decoder: FrameDecoder = decode_keyframes,
        identities: KeyframeIdentities | None = None,
        timings: StageRecorder | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the tracker.

        Args:
            keyframes: Segments a chunk's keyframes (`router.keyframe_chain`).
            rates: Keyframes per second per camera (default 2).
            store: Where `stitch` appends tracks; `None` keeps them in memory only.
            bus: Bus for `tracks.updated`; `None` publishes nothing.
            px_to_m: Image-pixel to ground-metre mapping of the camera's calibration.
            decoder: Decodes keyframes (injected in tests).
            identities: The ByteTrack state (injected in tests).
            timings: Receives `decode` and `sam` (per concept) durations.
            monotonic: Seconds on a monotonic clock.
        """
        self._keyframes = keyframes
        self._rates = rates or KeyframeRates()
        self._store = store
        self._bus = bus
        self._px_to_m = px_to_m
        self._decoder = decoder
        self._identities = identities or KeyframeIdentities()
        self._timings = timings
        self._monotonic = monotonic
        self.cache = StaticCache(_FirstKeyframe(self))
        self._static: frozenset[str] = frozenset()
        self._generation: str | None = None
        self._lock = threading.Lock()
        self._totals: dict[str, tuple[int, float]] = {}
        self._chunks: dict[tuple[str, str], tuple[int, float]] = {}

    # --- the loop's protocols ------------------------------------------------------------------

    def set_static(self, concepts: Iterable[str], *, generation: str | None = None) -> None:
        """Set the static concepts of the plan in force (served from the cache).

        Args:
            concepts: Static SAM prompts.
            generation: Identifies the plan (and its calibration); a new one refills the cache.
        """
        self._static = frozenset(concepts)
        self._generation = generation

    def indices(self, chunk: Chunk) -> tuple[int, ...]:
        """The chunk's keyframes at its camera's rate."""
        return keyframe_indices(chunk.n_frames, chunk.fps, self._rates.fps_for(chunk.camera_id))

    def segment(self, chunk: Chunk, concepts: Iterable[str]) -> SegmentedChunk:
        """Segment the chunk's keyframes; stateless, so several chunks may run at once.

        Args:
            chunk: The chunk.
            concepts: SAM prompts (duplicates are segmented once).

        Returns:
            Each concept's detections on the keyframes.
        """
        unique = tuple(dict.fromkeys(concepts))
        dynamic = tuple(c for c in unique if c not in self._static)
        detections: dict[str, tuple[Detection, ...]] = {}
        if dynamic:
            frames = self.decode(chunk, self.indices(chunk))
            detections.update(self.call(chunk, frames, dynamic).detections)
        for concept in unique:
            if concept in self._static:
                cached = self.cache.get(chunk, concept, calibration=self._generation)
                detections[concept] = self._spread(cached, chunk)
        return SegmentedChunk(chunk_id=chunk.chunk_id, concepts=unique, detections=detections)

    def stitch(self, chunk: Chunk, segmented: SegmentedChunk) -> list[Track]:
        """Track each concept through the chunk's keyframes, store the tracks, publish.

        Call for a camera's chunks in order.

        Args:
            chunk: The chunk `segmented` came from.
            segmented: Its keyframe detections, from `segment`.

        Returns:
            This chunk's tracks, in concept order, each sorted by first timestamp then id.
        """
        keyframes = [(i, frame_ts(chunk, i)) for i in self.indices(chunk)]
        tracks: list[Track] = []
        for concept in segmented.concepts:
            assigned = self._identities.assign(
                chunk.camera_id, concept, keyframes, segmented.detections.get(concept, ())
            )
            found = [self._track(tid, chunk, concept, dets) for tid, dets in assigned.items()]
            found.sort(key=lambda t: (t.points[0].ts, t.track_id))
            tracks.extend(found)
            _log.info(
                "tracks_updated",
                camera_id=chunk.camera_id,
                chunk_id=chunk.chunk_id,
                concept=concept,
                n_tracks=len(found),
                keyframes=len(keyframes),
            )
        if self._store is not None and tracks:
            self._store.append(tracks)
        if self._bus is not None:
            self._bus.publish(
                Topic.TRACKS_UPDATED,
                TracksUpdated(
                    camera_id=chunk.camera_id,
                    t0=chunk.start_ts,
                    t1=frame_ts(chunk, chunk.n_frames),
                ),
            )
        return tracks

    def take_chunk(self, camera_id: str, chunk_id: str) -> tuple[int, int]:
        """Return `(requests, billed_frames)` made for one chunk and forget them.

        `billed_frames` is the hosted bill in video-frame units (12.5 per hosted image).
        """
        with self._lock:
            requests, billed = self._chunks.pop((camera_id, chunk_id), (0, 0.0))
        return requests, math.ceil(billed - 1e-9)

    def totals(self, camera_id: str) -> tuple[int, int]:
        """Return `(requests, billed_frames)` made so far for `camera_id`."""
        with self._lock:
            requests, billed = self._totals.get(camera_id, (0, 0.0))
        return requests, math.ceil(billed - 1e-9)

    def estimate(self, chunk: Chunk, concepts: int) -> int:
        """The bill to reserve for segmenting `concepts` dynamic concepts on `chunk`.

        Zero while our GPU pod serves keyframes; every image at the hosted price otherwise.
        """
        if not self._keyframes.billed:
            return 0
        return math.ceil(concepts * len(self.indices(chunk)) * IMAGE_FRAME_EQUIVALENT - 1e-9)

    # --- SAM calls -----------------------------------------------------------------------------

    def decode(self, chunk: Chunk, indices: Sequence[int]) -> list[Keyframe]:
        """Decode the given keyframes of `chunk` (timed as `decode`)."""
        started = self._monotonic()
        frames = self._decoder(chunk, indices)
        if self._timings is not None:
            seconds = self._monotonic() - started
            self._timings.record("decode", chunk.camera_id, chunk.chunk_id, seconds)
        return frames

    def call(
        self, chunk: Chunk, frames: Sequence[Keyframe], concepts: Sequence[str]
    ) -> KeyframeBatch:
        """Segment `concepts` on `frames`, timing (`sam`) and metering the call."""
        started = self._monotonic()
        batch = self._keyframes.segment_keyframes(chunk, frames, concepts)
        if self._timings is not None:
            seconds = self._monotonic() - started
            for concept in concepts:
                self._timings.record_sam(chunk.camera_id, chunk.chunk_id, concept, seconds)
        self._meter(chunk, batch)
        return batch

    def _meter(self, chunk: Chunk, batch: KeyframeBatch) -> None:
        billed = batch.billed_images * IMAGE_FRAME_EQUIVALENT
        key = (chunk.camera_id, chunk.chunk_id)
        with self._lock:
            self._totals[chunk.camera_id] = _add(self._totals.get(chunk.camera_id), batch, billed)
            self._chunks[key] = _add(self._chunks.pop(key, None), batch, billed)
            while len(self._chunks) > MAX_OPEN_CHUNKS:
                del self._chunks[next(iter(self._chunks))]

    # --- tracks --------------------------------------------------------------------------------

    def _spread(self, cached: Sequence[Detection], chunk: Chunk) -> tuple[Detection, ...]:
        """Copy a static concept's cached objects onto every keyframe of the chunk."""
        reps = sorted(cached, key=lambda d: d.object_id)
        return tuple(
            d.model_copy(update={"frame_idx": i, "ts": frame_ts(chunk, i)})
            for i in self.indices(chunk)
            for d in reps
        )

    def _track(self, track_id: str, chunk: Chunk, concept: str, dets: Sequence[Detection]) -> Track:
        ordered = sorted(dets, key=lambda d: d.frame_idx)
        return Track(
            track_id=track_id,
            camera_id=chunk.camera_id,
            concept=concept,
            points=tuple(track_point(d, self._px_to_m) for d in ordered),
        )


def _add(
    counts: tuple[int, float] | None, batch: KeyframeBatch, billed: float
) -> tuple[int, float]:
    requests, frames = counts or (0, 0.0)
    return requests + batch.requests, frames + billed
