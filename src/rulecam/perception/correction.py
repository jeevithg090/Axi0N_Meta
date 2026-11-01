"""Correction loop: a negative point, a backward re-track and re-evaluation (P1-10, V2 Pillar 2).

When a person marks an alert as mis-segmented ("that is a backpack strap, not a harness"), they
click the false region on the event clip. The clicked object's concept is re-prompted on the
event's chunks with a **negative point**, propagating **backward** from the clicked frame to the
episode start. The re-segmented detections replace the old ones and the rule is re-evaluated on
the corrected tracks. If the episode no longer fires, the candidate is cancelled. The owner's
click confirms a false detection, so with `hard_negatives` it also becomes a permanent Rule CI
lookalike regression (WS1f).

Frames are addressed on the event clip: the chunks overlapping `[ts_start, ts_end]`, in order.
Click the last frame of the episode to correct all of it; detections after the clicked frame are
kept as they are.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal, Protocol

import numpy as np
from pydantic import Field

from rulecam.contracts import (
    CandidateEvent,
    Chunk,
    Detection,
    EventRecord,
    Frozen,
    RuleIR,
    Segmenter,
    Track,
    TrackPoint,
    TrackStore,
)
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.rules import RuleEvaluationError
from rulecam.rules.backtest import DecisionContext, read_candidate_payload, same_episode
from rulecam.rules.geometry import box_iou, project
from rulecam.rules.pose import PoseFeatures

_log = get_logger(__name__)

NEAREST_POINT_S = 0.5
MATCH_IOU = 0.3


class CorrectionError(RulecamError):
    """A correction request cannot be applied to its event."""


class UnknownEventError(CorrectionError):
    """No candidate record exists for the requested event id."""


class CorrectionRequest(Frozen):
    """`POST /correction` body: a click on the event clip marking a false region."""

    event_id: str
    frame_idx: int = Field(ge=0)
    x: float
    y: float


class LabelPayload(Frozen):
    """Payload of the `label` chain record a correction writes."""

    event_id: str
    rule_id: str
    camera_id: str
    label: Literal["false_positive"] = "false_positive"
    reason: Literal["segmentation"] = "segmentation"
    concept: str
    frame_idx: int
    x: float
    y: float


class CorrectionResult(Frozen):
    """What a correction did.

    Attributes:
        status: `cancelled` if the corrected tracks no longer fire this episode.
        concept: The re-prompted concept (the clicked object's).
        chunk_ids: Chunks re-segmented, newest first.
        candidate: The cancelled candidate (trace `status="cancelled"`), or the re-derived one.
        label: The label record payload that was logged.
    """

    event_id: str
    status: Literal["cancelled", "still_firing"]
    concept: str
    chunk_ids: tuple[str, ...]
    candidate: CandidateEvent
    label: LabelPayload


class EventSource(Protocol):
    """Looks up the chain record of a candidate (P3-07's evidence chain)."""

    def find(self, event_id: str) -> EventRecord | None:
        """Return the `candidate` record for `event_id`, or None."""
        ...


class ChunkSource(Protocol):
    """Lists stored chunks of a camera (the capture index)."""

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        """Return the chunks overlapping `[t0, t1]`."""
        ...


class LabelSink(Protocol):
    """Appends a record to the evidence chain (P3-07's `append(kind, payload)`)."""

    def __call__(self, kind: Literal["label"], payload: dict[str, object]) -> object:
        """Append one record."""
        ...


class FalsePositiveSink(Protocol):
    """Remembers a confirmed false detection (`rule_ci.hard_negatives.HardNegatives`)."""

    def from_correction(
        self, candidate: CandidateEvent, rule: RuleIR, concept: str, *, x: float, y: float
    ) -> object:
        """Record an owner correction."""
        ...


class CorrectionService:
    """Applies corrections; inject the segmenter, stores and the chain writer."""

    def __init__(
        self,
        *,
        segmenter: Segmenter,
        store: TrackStore,
        events: EventSource,
        chunks: ChunkSource,
        record_label: LabelSink,
        pose: PoseFeatures | None = None,
        hard_negatives: FalsePositiveSink | None = None,
    ) -> None:
        """Create the service.

        Args:
            segmenter: SAM 3.1 (hosted or self-hosted) behind the `Segmenter` Protocol.
            store: Track store holding the event's track window.
            events: Lookup of candidate chain records.
            chunks: Lookup of the event's video chunks.
            record_label: Writes the `label` record to the evidence chain.
            pose: Pose features, for rules with pose predicates.
            hard_negatives: Turns each correction into a permanent Rule CI regression (WS1f).
        """
        self._segmenter = segmenter
        self._store = store
        self._events = events
        self._chunks = chunks
        self._record_label = record_label
        self._pose = pose
        self._hard_negatives = hard_negatives

    def correct(self, request: CorrectionRequest) -> CorrectionResult:
        """Re-track the clicked object backward with a negative point and re-evaluate the rule.

        Raises:
            UnknownEventError: If the event has no candidate record.
            CorrectionError: If the click does not land on a frame or an object of the rule.
        """
        candidate, context = self._load(request.event_id)
        chunks = sorted(
            self._chunks.chunks(candidate.camera_id, candidate.ts_start, candidate.ts_end),
            key=lambda c: c.start_ts,
        )
        clicked, local = locate_frame(chunks, request.frame_idx)
        click_ts = frame_ts(chunks[clicked], local)
        tracks = self._store.window(candidate.camera_id, context.t0, context.t1)
        false_track = clicked_track(tracks, context, click_ts, request.x, request.y)
        per_chunk = self._resegment(chunks[: clicked + 1], local, false_track, request)
        corrected = replace_detections(
            tracks,
            false_track.concept,
            per_chunk,
            t0=chunks[0].start_ts,
            t1=click_ts,
            context=context,
        )
        after = context.evaluate(corrected, pose=self._pose)
        label = LabelPayload(
            event_id=candidate.event_id,
            rule_id=candidate.rule_id,
            camera_id=candidate.camera_id,
            concept=false_track.concept,
            frame_idx=request.frame_idx,
            x=request.x,
            y=request.y,
        )
        self._record_label("label", label.model_dump(mode="json"))
        if self._hard_negatives is not None:
            self._hard_negatives.from_correction(
                candidate, context.rule, false_track.concept, x=request.x, y=request.y
            )
        result = _result(candidate, after, label, [chunk_id for chunk_id, _ in per_chunk])
        _log.info(
            "correction_applied",
            event_id=candidate.event_id,
            camera_id=candidate.camera_id,
            status=result.status,
            concept=result.concept,
        )
        return result

    def _load(self, event_id: str) -> tuple[CandidateEvent, DecisionContext]:
        record = self._events.find(event_id)
        if record is None:
            msg = f"no candidate record for event {event_id!r}"
            raise UnknownEventError(msg)
        try:
            return read_candidate_payload(record)
        except RuleEvaluationError as exc:
            msg = f"event {event_id!r} has no usable candidate record"
            raise CorrectionError(msg) from exc

    def _resegment(
        self,
        chunks: Sequence[Chunk],
        clicked_local: int,
        false_track: Track,
        request: CorrectionRequest,
    ) -> list[tuple[str, list[Detection]]]:
        """Segment each chunk newest-first with a negative point on the false object.

        The clicked chunk gets the click itself; each earlier chunk gets a point on its last
        frame at the false object's position there (from its old track).
        """
        out = []
        for index in range(len(chunks) - 1, -1, -1):
            chunk = chunks[index]
            clicked = index == len(chunks) - 1
            local = clicked_local if clicked else chunk.n_frames - 1
            click = (request.x, request.y)
            x, y = click if clicked else _centre_near(false_track, frame_ts(chunk, local), click)
            found = self._segmenter.segment(
                chunk, false_track.concept, negative_points=[(local, x, y)], direction="backward"
            )
            out.append((chunk.chunk_id, [d for d in found if d.frame_idx <= local]))
        return out


def locate_frame(chunks: Sequence[Chunk], frame_idx: int) -> tuple[int, int]:
    """Map a frame index on the event clip to `(chunk position, frame index in that chunk)`.

    Raises:
        CorrectionError: If the clip has no such frame.
    """
    offset = 0
    for position, chunk in enumerate(chunks):
        if frame_idx < offset + chunk.n_frames:
            return position, frame_idx - offset
        offset += chunk.n_frames
    msg = f"frame {frame_idx} is outside the event clip ({offset} frames)"
    raise CorrectionError(msg)


def frame_ts(chunk: Chunk, local: int) -> datetime:
    """Timestamp of a frame inside a chunk."""
    return chunk.start_ts + timedelta(seconds=local / chunk.fps)


def _nearest(track: Track, ts: datetime) -> TrackPoint | None:
    best = min(track.points, key=lambda p: abs((p.ts - ts).total_seconds()), default=None)
    if best is None or abs((best.ts - ts).total_seconds()) > NEAREST_POINT_S:
        return None
    return best


def _centre_near(track: Track, ts: datetime, fallback: tuple[float, float]) -> tuple[float, float]:
    point = _nearest(track, ts)
    if point is None:
        return fallback
    x1, y1, x2, y2 = point.box_xyxy
    return (x1 + x2) / 2, (y1 + y2) / 2


def clicked_track(
    tracks: Sequence[Track], context: DecisionContext, ts: datetime, x: float, y: float
) -> Track:
    """The rule-concept track whose box contains the click; the smallest box wins.

    Raises:
        CorrectionError: If no object of the rule's concepts is under the click.
    """
    names = {n for key, spec in context.rule.concepts.items() for n in (key, spec.prompt)}
    hits: list[tuple[float, str, Track]] = []
    for track in tracks:
        point = _nearest(track, ts) if track.concept in names else None
        if point is None:
            continue
        x1, y1, x2, y2 = point.box_xyxy
        if x1 <= x <= x2 and y1 <= y <= y2:
            hits.append(((x2 - x1) * (y2 - y1), track.track_id, track))
    if not hits:
        msg = f"no object of rule {context.rule.id!r} at ({x}, {y})"
        raise CorrectionError(msg)
    return min(hits, key=lambda h: (h[0], h[1]))[2]


def replace_detections(
    tracks: Sequence[Track],
    concept: str,
    per_chunk: Sequence[tuple[str, Sequence[Detection]]],
    *,
    t0: datetime,
    t1: datetime,
    context: DecisionContext,
) -> list[Track]:
    """Swap a concept's points inside `[t0, t1]` for re-segmented detections.

    Each re-segmented object keeps the id of the old track it overlaps (IoU >= 0.3 at the first
    common frame time), so subjects stay attached to their candidates; others get a new id.

    Args:
        tracks: The event's track window.
        concept: The corrected concept (track concept name).
        per_chunk: `(chunk_id, detections)` for every re-segmented chunk.
        t0: Start of the re-segmented span.
        t1: End of the re-segmented span (the clicked frame).
        context: Decision context (its calibration projects new foot points).

    Returns:
        The corrected track window.
    """
    old = [t for t in tracks if t.concept == concept]
    points: dict[str, list[TrackPoint]] = {
        t.track_id: [p for p in t.points if not t0 <= p.ts <= t1] for t in old
    }
    cameras = {t.track_id: t.camera_id for t in old}
    for chunk_id, detections in per_chunk:
        for object_id, dets in _by_object(detections).items():
            new = [to_point(d, context) for d in dets]
            track_id = (
                _match(old, new) or f"{dets[0].camera_id}:{concept}:fix-{chunk_id}-{object_id}"
            )
            points.setdefault(track_id, []).extend(new)
            cameras.setdefault(track_id, dets[0].camera_id)
    rebuilt = [
        Track(
            track_id=track_id,
            camera_id=cameras[track_id],
            concept=concept,
            points=tuple(sorted(pts, key=lambda p: p.ts)),
        )
        for track_id, pts in sorted(points.items())
        if pts
    ]
    return [t for t in tracks if t.concept != concept] + rebuilt


def _by_object(detections: Sequence[Detection]) -> dict[int, list[Detection]]:
    out: dict[int, list[Detection]] = {}
    for det in sorted(detections, key=lambda d: (d.ts, d.object_id)):
        out.setdefault(det.object_id, []).append(det)
    return out


def _match(old: Sequence[Track], points: Sequence[TrackPoint]) -> str | None:
    best: tuple[float, str] | None = None
    for track in old:
        for point in points:
            previous = next((p for p in track.points if p.ts == point.ts), None)
            if previous is None:
                continue
            iou = float(box_iou(np.array([point.box_xyxy]), np.array([previous.box_xyxy]))[0])
            if iou >= MATCH_IOU and (best is None or iou > best[0]):
                best = (iou, track.track_id)
            break
    return None if best is None else best[1]


def to_point(det: Detection, context: DecisionContext) -> TrackPoint:
    """A track point from a detection: box bottom-centre foot point, projected if calibrated."""
    x1, _, x2, y2 = det.mask.box_xyxy
    foot = ((x1 + x2) / 2, y2)
    foot_m = None
    if context.calibration is not None and context.calibration.camera_id == det.camera_id:
        gx, gy = project(context.calibration, np.array([foot]))[0]
        foot_m = (float(gx), float(gy))
    return TrackPoint(
        ts=det.ts,
        frame_idx=det.frame_idx,
        box_xyxy=det.mask.box_xyxy,
        foot_px=foot,
        foot_m=foot_m,
        confidence=det.mask.confidence,
    )


def _result(
    candidate: CandidateEvent,
    after: Sequence[CandidateEvent],
    label: LabelPayload,
    chunk_ids: list[str],
) -> CorrectionResult:
    still = next((e for e in after if same_episode(e, candidate)), None)
    if still is None:
        trace = {**candidate.predicate_trace, "status": "cancelled", "cancelled_by": "correction"}
        updated = candidate.model_copy(update={"predicate_trace": trace})
        status: Literal["cancelled", "still_firing"] = "cancelled"
    else:
        updated, status = still, "still_firing"
    return CorrectionResult(
        event_id=candidate.event_id,
        status=status,
        concept=label.concept,
        chunk_ids=tuple(chunk_ids),
        candidate=updated,
        label=label,
    )
