"""Coverage watchdog: a camera that cannot see turns its rules BLIND, never "compliant" (P1-08).

Each chunk is measured (`measure_chunk`) and classified (`classify`) into a raw verdict: OK, or a
BLIND reason. A per-camera hysteresis machine (`advance`) confirms a change only after
`enter_chunks` / `recover_chunks` consecutive chunks agree. `CoverageWatchdog` ties this together,
publishes `Topic.COVERAGE_CHANGED` when the confirmed state changes, and keeps watched-time
accounting for `watched_fraction` and `blind_intervals`.

Reasons (checked in this order; the first that holds wins):

1. `dark`: mean luma below 20 / 255.
2. `blocked`: one uniform region covers more than 60% of the frame (a tray over the lens).
3. `frozen`: every frame hash is identical, or no consecutive pair differs by more than a
   near-zero mean grey level across the whole chunk.
4. `moved`: anchor centroids shifted by more than 5% of the frame width (median over anchors), or
   an ORB homography to the calibration frame moves the frame corners by more than that.
5. `blocked`: an anchor static mask is missing, or its IoU against the calibration-time mask is
   below 0.3.

Anchor masks (static concepts such as sink, door, racks) are inputs: the caller passes the
calibration-time masks in `CoverageReference` and the current masks to `observe`. After a camera
moves it stays BLIND(moved) until it is recalibrated and `set_reference` is called again.

`coverage.changed` payload (a plain JSON-ready dict, built from `CoverageChange`)::

    {"camera_id": "cam1", "state": "blind", "reason": "moved", "moved": true,
     "ts": "2026-11-01T14:03:09Z"}

`state` is `"ok"` or `"blind"`; `reason` is `"frozen" | "dark" | "blocked" | "moved"` or `None`
when OK; `moved` is True exactly when the camera has just been confirmed BLIND(moved), which is the
signal the static cache uses to invalidate; `ts` is the end of the chunk that confirmed the change.

Watched-time accounting is conservative: a chunk counts as watched only when its own raw verdict is
OK *and* the confirmed state after it is OK. Unconfirmed bad chunks, chunks while recovery is still
pending, and time with no chunk at all are not watched.
"""

from __future__ import annotations

import hashlib
import itertools
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
from pydantic import Field, model_validator

from rulecam.bus import Bus, Topic
from rulecam.contracts import CandidateEvent, Chunk, Frozen
from rulecam.log import get_logger
from rulecam.perception.intervals import Interval, clip, complement, merge, overlaps, total_seconds

_log = get_logger(__name__)

Frame = npt.NDArray[np.uint8]
BoolMask = npt.NDArray[np.bool_]

_ORDER_TOLERANCE = timedelta(milliseconds=10)
_LOCAL_WINDOW = (5, 5)
_RANSAC_REPROJ_PX = 5.0
_BGR_CHANNELS = 3


class CoverageReason(StrEnum):
    """Why a camera cannot be trusted to see."""

    FROZEN = "frozen"
    DARK = "dark"
    BLOCKED = "blocked"
    MOVED = "moved"


class CoverageState(StrEnum):
    """Confirmed coverage state of one camera."""

    OK = "ok"
    BLIND = "blind"


class CoverageThresholds(Frozen):
    """Tunable thresholds; the defaults follow the task book (P1-08)."""

    dark_luma: float = 20.0
    frozen_max_diff: float = Field(default=0.1, ge=0)
    uniform_fraction: float = Field(default=0.6, gt=0, le=1)
    uniform_local_std: float = Field(default=4.0, gt=0)
    uniform_tolerance: float = Field(default=12.0, gt=0)
    anchor_min_iou: float = Field(default=0.3, ge=0, le=1)
    moved_shift_frac: float = Field(default=0.05, gt=0)
    orb_min_inliers: int = Field(default=15, ge=4)
    analysis_width: int = Field(default=160, gt=0)
    orb_width: int = Field(default=640, gt=0)
    enter_chunks: int = Field(default=2, ge=1)
    recover_chunks: int = Field(default=2, ge=1)


class ChunkSignals(Frozen):
    """The measured values behind one chunk's verdict (logged for tuning and evidence)."""

    n_frames: int
    mean_luma: float
    identical: bool
    max_diff: float | None = None  # None with fewer than two frames
    uniform_fraction: float
    anchors_missing: tuple[str, ...] = ()
    anchors_low_iou: tuple[str, ...] = ()
    anchor_shift_frac: float | None = None  # median over present anchors
    orb_shift_frac: float | None = None


@dataclass(frozen=True)
class CoverageReference:
    """Calibration-time view of one camera.

    Attributes:
        frame: Grayscale calibration frame, resized to `CoverageThresholds.orb_width`.
        anchors: Calibration-time boolean masks of static concepts, keyed by concept.
    """

    frame: Frame
    anchors: Mapping[str, BoolMask] = field(default_factory=dict)


class Hysteresis(Frozen):
    """Confirmed status plus the streak of chunks that disagree with it.

    `status` is None when the camera is OK. `candidate` and `streak` describe the pending change.
    """

    status: CoverageReason | None = None
    candidate: CoverageReason | None = None
    streak: int = 0

    @property
    def state(self) -> CoverageState:
        """The confirmed state."""
        return CoverageState.OK if self.status is None else CoverageState.BLIND


class CoverageChange(Frozen):
    """A confirmed change of a camera's coverage state (the `coverage.changed` payload)."""

    camera_id: str
    state: CoverageState
    reason: CoverageReason | None
    moved: bool
    ts: datetime

    def payload(self) -> dict[str, object]:
        """Return the JSON-ready dict published on the bus."""
        return self.model_dump(mode="json")


class CoverageSegment(Frozen):
    """Watched-time accounting for one observed chunk."""

    start: datetime
    end: datetime
    watched: bool
    reason: CoverageReason | None = None  # the chunk's raw reason, or the confirmed one if BLIND


class ChunkVerdict(Frozen):
    """Result of observing one chunk."""

    chunk_id: str
    camera_id: str
    signals: ChunkSignals
    raw: CoverageReason | None
    state: CoverageState
    reason: CoverageReason | None
    changed: bool


# --- frame measurements (pure) ----------------------------------------------------------------


def to_gray(frame: Frame) -> Frame:
    """Convert a BGR or grayscale uint8 frame to grayscale (Rec. 601 luma).

    Args:
        frame: An `H x W` or `H x W x 3` uint8 array.

    Returns:
        An `H x W` uint8 array.

    Raises:
        ValueError: If the frame is not uint8 grayscale or BGR.
    """
    if frame.dtype != np.uint8:
        msg = f"frames must be uint8, got {frame.dtype}"
        raise ValueError(msg)
    if frame.ndim == 2:  # noqa: PLR2004 - grayscale has two axes
        return frame
    if frame.ndim == 3 and frame.shape[2] == _BGR_CHANNELS:  # noqa: PLR2004 - H x W x C
        return np.asarray(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
    msg = f"frames must be grayscale or BGR, got shape {frame.shape}"
    raise ValueError(msg)


def resize_to_width(gray: Frame, width: int) -> Frame:
    """Downscale a grayscale frame to `width` pixels wide, keeping the aspect ratio.

    Frames already at most `width` wide are returned unchanged.
    """
    height, current = gray.shape
    if current <= width:
        return gray
    target = (width, max(1, round(height * width / current)))
    return np.asarray(cv2.resize(gray, target, interpolation=cv2.INTER_AREA), dtype=np.uint8)


def uniform_fraction(gray: Frame, thresholds: CoverageThresholds) -> float:
    """Share of the frame covered by its largest uniform, connected region.

    A pixel is flat when its 5 x 5 local standard deviation is below `uniform_local_std`. The
    region is the largest 8-connected set of flat pixels within `uniform_tolerance` grey levels of
    the median flat intensity, so a smooth gradient does not count as one region.

    Args:
        gray: A grayscale frame, ideally already downscaled to `analysis_width`.
        thresholds: Coverage thresholds.

    Returns:
        A fraction in `[0, 1]`.
    """
    image = gray.astype(np.float32)
    mean = cv2.blur(image, _LOCAL_WINDOW)
    local_std = np.sqrt(np.maximum(cv2.blur(image * image, _LOCAL_WINDOW) - mean * mean, 0.0))
    flat = local_std < thresholds.uniform_local_std
    if not flat.any():
        return 0.0
    centre = float(np.median(image[flat]))
    region = (flat & (np.abs(image - centre) <= thresholds.uniform_tolerance)).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(region, connectivity=8)
    if count <= 1:
        return 0.0
    return float(stats[1:, cv2.CC_STAT_AREA].max()) / region.size


def orb_shift(reference: Frame, current: Frame, min_inliers: int) -> float | None:
    """How far the view moved since calibration, from an ORB homography.

    Args:
        reference: Grayscale calibration frame.
        current: Grayscale current frame at the same scale.
        min_inliers: Minimum RANSAC inliers for the homography to count.

    Returns:
        Mean displacement of the four frame corners as a fraction of the frame width, or None
        when there are too few features or matches to tell.
    """
    orb = cv2.ORB.create(nfeatures=500)
    ref_points, ref_desc = orb.detectAndCompute(reference, None)
    cur_points, cur_desc = orb.detectAndCompute(current, None)
    if ref_desc is None or cur_desc is None:
        return None
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(ref_desc, cur_desc)
    if len(matches) < min_inliers:
        return None
    src = np.array([ref_points[m.queryIdx].pt for m in matches], dtype=np.float32).reshape(-1, 1, 2)
    dst = np.array([cur_points[m.trainIdx].pt for m in matches], dtype=np.float32).reshape(-1, 1, 2)
    homography, inliers = cv2.findHomography(src, dst, cv2.RANSAC, _RANSAC_REPROJ_PX)
    if homography is None or int(inliers.sum()) < min_inliers:
        return None
    height, width = reference.shape
    corners = np.array(
        [[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32
    ).reshape(-1, 1, 2)
    moved = cv2.perspectiveTransform(corners, homography)
    return float(np.linalg.norm((moved - corners).reshape(-1, 2), axis=1).mean()) / int(width)


def _max_consecutive_diff(grays: Sequence[Frame]) -> float | None:
    if len(grays) < 2:  # noqa: PLR2004 - a difference needs two frames
        return None
    return max(float(cv2.absdiff(a, b).mean()) for a, b in itertools.pairwise(grays))


def _all_identical(frames: Sequence[Frame]) -> bool:
    if len(frames) < 2:  # noqa: PLR2004 - identity needs two frames
        return False
    digests = {hashlib.blake2b(f.tobytes(), digest_size=16).digest() for f in frames}
    return len(digests) == 1


def _match_shape(mask: BoolMask, shape: tuple[int, ...]) -> BoolMask:
    if mask.shape == shape:
        return mask
    resized = cv2.resize(
        mask.astype(np.uint8), (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST
    )
    matched: BoolMask = resized.astype(bool)
    return matched


def _centroid_shift(reference: BoolMask, current: BoolMask) -> float:
    ref_y, ref_x = np.nonzero(reference)
    cur_y, cur_x = np.nonzero(current)
    shift = np.hypot(cur_x.mean() - ref_x.mean(), cur_y.mean() - ref_y.mean())
    return float(shift) / int(reference.shape[1])


def _iou(a: BoolMask, b: BoolMask) -> float:
    union = np.logical_or(a, b).sum()
    return 0.0 if union == 0 else float(np.logical_and(a, b).sum()) / float(union)


def anchor_signals(
    reference: Mapping[str, BoolMask],
    current: Mapping[str, BoolMask | None],
    min_iou: float,
) -> tuple[tuple[str, ...], tuple[str, ...], float | None]:
    """Compare current anchor masks with their calibration-time masks.

    Args:
        reference: Calibration-time masks by concept.
        current: Current masks by concept; a missing key, None or an empty mask means the anchor
            was not found.
        min_iou: Below this IoU an anchor counts as occluded.

    Returns:
        `(missing, low_iou, median_shift)`: concepts not found, concepts found with low IoU, and
        the median centroid shift (fraction of frame width) over found anchors, or None.
    """
    missing: list[str] = []
    low_iou: list[str] = []
    shifts: list[float] = []
    for concept in sorted(reference):
        ref_mask = reference[concept]
        mask = current.get(concept)
        if mask is None or not mask.any() or not ref_mask.any():
            missing.append(concept)
            continue
        mask = _match_shape(mask, ref_mask.shape)
        shifts.append(_centroid_shift(ref_mask, mask))
        if _iou(ref_mask, mask) < min_iou:
            low_iou.append(concept)
    shift = statistics.median(shifts) if shifts else None
    return tuple(missing), tuple(low_iou), shift


def make_reference(
    frame: Frame,
    anchors: Mapping[str, BoolMask] | None = None,
    thresholds: CoverageThresholds | None = None,
) -> CoverageReference:
    """Build a camera's calibration-time reference from one frame and its anchor masks.

    Args:
        frame: A BGR or grayscale uint8 calibration frame.
        anchors: Boolean masks of static concepts at calibration time.
        thresholds: Coverage thresholds (for the ORB working width).

    Returns:
        The reference to pass to `CoverageWatchdog.set_reference`.
    """
    limits = thresholds or CoverageThresholds()
    return CoverageReference(
        frame=resize_to_width(to_gray(frame), limits.orb_width), anchors=dict(anchors or {})
    )


def measure_chunk(
    frames: Sequence[Frame],
    *,
    reference: CoverageReference | None = None,
    anchors: Mapping[str, BoolMask | None] | None = None,
    thresholds: CoverageThresholds | None = None,
) -> ChunkSignals:
    """Measure the coverage signals of one chunk.

    Args:
        frames: Frames sampled from the chunk, in time order (BGR or grayscale uint8).
        reference: The camera's calibration-time reference, if any.
        anchors: Current anchor masks by concept. None skips the anchor checks.
        thresholds: Coverage thresholds.

    Returns:
        The measured signals.

    Raises:
        ValueError: If `frames` is empty or a frame has the wrong type or shape.
    """
    if not frames:
        msg = "a chunk needs at least one frame"
        raise ValueError(msg)
    limits = thresholds or CoverageThresholds()
    grays = [to_gray(f) for f in frames]
    small = [resize_to_width(g, limits.analysis_width) for g in grays]
    missing: tuple[str, ...] = ()
    low_iou: tuple[str, ...] = ()
    anchor_shift = orb = None
    if reference is not None:
        orb = orb_shift(
            reference.frame, resize_to_width(grays[-1], limits.orb_width), limits.orb_min_inliers
        )
        if anchors is not None and reference.anchors:
            missing, low_iou, anchor_shift = anchor_signals(
                reference.anchors, anchors, limits.anchor_min_iou
            )
    return ChunkSignals(
        n_frames=len(frames),
        mean_luma=float(np.mean([g.mean() for g in grays])),
        identical=_all_identical(frames),
        max_diff=_max_consecutive_diff(grays),
        uniform_fraction=float(np.median([uniform_fraction(s, limits) for s in small])),
        anchors_missing=missing,
        anchors_low_iou=low_iou,
        anchor_shift_frac=anchor_shift,
        orb_shift_frac=orb,
    )


def _above(value: float | None, threshold: float) -> bool:
    return value is not None and value > threshold


def classify(
    signals: ChunkSignals, thresholds: CoverageThresholds | None = None
) -> CoverageReason | None:
    """Turn one chunk's signals into a raw verdict (None means the chunk looks fine).

    Args:
        signals: Measured signals of the chunk.
        thresholds: Coverage thresholds.

    Returns:
        The first BLIND reason that holds, in the order documented in the module docstring.
    """
    limits = thresholds or CoverageThresholds()
    frozen = signals.identical or (
        signals.max_diff is not None and signals.max_diff < limits.frozen_max_diff
    )
    moved = _above(signals.anchor_shift_frac, limits.moved_shift_frac) or _above(
        signals.orb_shift_frac, limits.moved_shift_frac
    )
    checks = (
        (signals.mean_luma < limits.dark_luma, CoverageReason.DARK),
        (signals.uniform_fraction > limits.uniform_fraction, CoverageReason.BLOCKED),
        (frozen, CoverageReason.FROZEN),
        (moved, CoverageReason.MOVED),
        (bool(signals.anchors_missing or signals.anchors_low_iou), CoverageReason.BLOCKED),
    )
    return next((reason for hit, reason in checks if hit), None)


def advance(
    machine: Hysteresis,
    raw: CoverageReason | None,
    *,
    enter_chunks: int = 2,
    recover_chunks: int = 2,
) -> Hysteresis:
    """Advance the hysteresis machine by one chunk.

    Entering BLIND needs `enter_chunks` consecutive bad chunks (any reasons; the latest wins).
    Recovering needs `recover_chunks` consecutive good chunks. Changing the reason while BLIND
    needs `enter_chunks` consecutive chunks with the same new reason.

    Args:
        machine: The current machine.
        raw: The chunk's raw verdict (None means OK).
        enter_chunks: Chunks needed to enter BLIND or change reason.
        recover_chunks: Chunks needed to recover.

    Returns:
        The machine after this chunk.
    """
    if raw == machine.status:
        return Hysteresis(status=machine.status)
    entering = machine.status is None
    same_direction = machine.streak > 0 and (entering or machine.candidate == raw)
    streak = machine.streak + 1 if same_direction else 1
    needed = recover_chunks if raw is None else enter_chunks
    if streak >= needed:
        return Hysteresis(status=raw)
    return Hysteresis(status=machine.status, candidate=raw, streak=streak)


def read_frames(path: Path, n_frames: int, max_frames: int = 8) -> list[Frame]:
    """Sample up to `max_frames` evenly spaced frames from a video file.

    Args:
        path: The chunk's video file.
        n_frames: Frames in the chunk (sets the sampling step).
        max_frames: Maximum number of frames to return.

    Returns:
        BGR frames in time order.

    Raises:
        ValueError: If the file cannot be opened or yields no frames.
    """
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        msg = f"cannot open video {path}"
        raise ValueError(msg)
    step = max(1, n_frames // max_frames)
    frames: list[Frame] = []
    index = 0
    try:
        while len(frames) < max_frames and capture.grab():
            if index % step == 0:
                ok, frame = capture.retrieve()
                if ok:
                    frames.append(np.asarray(frame, dtype=np.uint8))
            index += 1
    finally:
        capture.release()
    if not frames:
        msg = f"no frames decoded from {path}"
        raise ValueError(msg)
    return frames


# --- the watchdog -----------------------------------------------------------------------------


class CoverageWatchdog:
    """Per-camera coverage state machines with watched-time accounting.

    Chunks of one camera must be observed in time order.
    """

    def __init__(
        self, bus: Bus | None = None, thresholds: CoverageThresholds | None = None
    ) -> None:
        """Create a watchdog.

        Args:
            bus: Where `coverage.changed` is published; None disables publishing.
            thresholds: Coverage thresholds.
        """
        self._bus = bus
        self._limits = thresholds or CoverageThresholds()
        self._machines: dict[str, Hysteresis] = {}
        self._references: dict[str, CoverageReference] = {}
        self._segments: dict[str, list[CoverageSegment]] = {}

    def set_reference(self, camera_id: str, reference: CoverageReference) -> None:
        """Set (or replace, after recalibration) a camera's calibration-time reference."""
        self._references[camera_id] = reference

    def status(self, camera_id: str) -> Hysteresis:
        """Return the camera's hysteresis machine (OK for a camera never observed)."""
        return self._machines.get(camera_id, Hysteresis())

    def segments(self, camera_id: str) -> tuple[CoverageSegment, ...]:
        """Return the camera's watched-time accounting, one segment per observed chunk."""
        return tuple(self._segments.get(camera_id, ()))

    def observe_chunk(
        self,
        chunk: Chunk,
        anchors: Mapping[str, BoolMask | None] | None = None,
        max_frames: int = 8,
    ) -> ChunkVerdict:
        """Read frames from the chunk's file and observe them (see `observe`)."""
        frames = read_frames(Path(chunk.path), chunk.n_frames, max_frames)
        return self.observe(chunk, frames, anchors)

    def observe(
        self,
        chunk: Chunk,
        frames: Sequence[Frame],
        anchors: Mapping[str, BoolMask | None] | None = None,
    ) -> ChunkVerdict:
        """Assess one chunk, advance the camera's state and record watched time.

        Args:
            chunk: The chunk the frames came from.
            frames: Frames sampled from the chunk, in time order.
            anchors: Current anchor masks by concept; None skips the anchor checks.

        Returns:
            The chunk's verdict, including whether the confirmed state changed.

        Raises:
            ValueError: If the chunk starts before the previous chunk of this camera ended, or
                the frames are invalid.
        """
        camera_id = chunk.camera_id
        end = chunk.start_ts + timedelta(seconds=chunk.n_frames / chunk.fps)
        history = self._segments.setdefault(camera_id, [])
        if history and chunk.start_ts < history[-1].end - _ORDER_TOLERANCE:
            msg = f"chunk {chunk.chunk_id} starts before the previous chunk of {camera_id} ended"
            raise ValueError(msg)
        signals = measure_chunk(
            frames,
            reference=self._references.get(camera_id),
            anchors=anchors,
            thresholds=self._limits,
        )
        raw = classify(signals, self._limits)
        before = self.status(camera_id)
        after = advance(
            before,
            raw,
            enter_chunks=self._limits.enter_chunks,
            recover_chunks=self._limits.recover_chunks,
        )
        self._machines[camera_id] = after
        watched = raw is None and after.status is None
        history.append(
            CoverageSegment(
                start=chunk.start_ts, end=end, watched=watched, reason=raw or after.status
            )
        )
        changed = after.status != before.status
        if changed:
            self._publish(
                CoverageChange(
                    camera_id=camera_id,
                    state=after.state,
                    reason=after.status,
                    moved=after.status is CoverageReason.MOVED,
                    ts=end,
                )
            )
        return ChunkVerdict(
            chunk_id=chunk.chunk_id,
            camera_id=camera_id,
            signals=signals,
            raw=raw,
            state=after.state,
            reason=after.status,
            changed=changed,
        )

    def watched_fraction(self, camera_id: str, t0: datetime, t1: datetime) -> float:
        """Fraction of `[t0, t1)` during which the camera was watched.

        Time with no observed chunk counts as not watched.

        Args:
            camera_id: The camera.
            t0: Window start.
            t1: Window end; must be after `t0`.

        Returns:
            A fraction in `[0, 1]`.

        Raises:
            ValueError: If `t1` is not after `t0`.
        """
        window = _window(t0, t1)
        return total_seconds(clip(self._watched(camera_id), window)) / window.seconds

    def blind_intervals(self, camera_id: str, t0: datetime, t1: datetime) -> list[Interval]:
        """Return the parts of `[t0, t1)` during which the camera was not watched.

        Raises:
            ValueError: If `t1` is not after `t0`.
        """
        return complement(self._watched(camera_id), _window(t0, t1))

    def _watched(self, camera_id: str) -> list[Interval]:
        return merge(
            (
                Interval(start=s.start, end=s.end)
                for s in self._segments.get(camera_id, ())
                if s.watched
            ),
            max_gap_s=_ORDER_TOLERANCE.total_seconds(),
        )

    def _publish(self, change: CoverageChange) -> None:
        _log.info(
            "coverage_changed",
            camera_id=change.camera_id,
            state=str(change.state),
            reason=None if change.reason is None else str(change.reason),
        )
        if self._bus is not None:
            self._bus.publish(Topic.COVERAGE_CHANGED, change.payload())


def _window(t0: datetime, t1: datetime) -> Interval:
    if t1 <= t0:
        msg = f"window end {t1} must be after start {t0}"
        raise ValueError(msg)
    return Interval(start=t0, end=t1)


# --- compliance accounting --------------------------------------------------------------------


class OutcomeStatus(StrEnum):
    """How one rule outcome counts once coverage is taken into account."""

    COMPLIANT = "compliant"
    VIOLATION = "violation"
    BLIND = "blind"


class RuleOutcome(Frozen):
    """One evaluated opportunity of a rule over a time span, as the engine wrapper reports it."""

    rule_id: str
    camera_id: str
    start: datetime
    end: datetime
    compliant: bool

    @model_validator(mode="after")
    def _ordered(self) -> RuleOutcome:
        if self.end < self.start:
            msg = "outcome ends before it starts"
            raise ValueError(msg)
        return self


class ComplianceTally(Frozen):
    """Compliance counts with BLIND outcomes kept out of both numerator and denominator."""

    compliant: int = 0
    violations: int = 0
    blind: int = 0

    @property
    def rate(self) -> float | None:
        """Compliant share of the watched outcomes, or None when nothing was watched."""
        watched = self.compliant + self.violations
        return None if watched == 0 else self.compliant / watched


def outcome_from_candidate(candidate: CandidateEvent) -> RuleOutcome:
    """Turn an engine candidate (a violation) into a `RuleOutcome`."""
    return RuleOutcome(
        rule_id=candidate.rule_id,
        camera_id=candidate.camera_id,
        start=candidate.ts_start,
        end=candidate.ts_end,
        compliant=False,
    )


def classify_outcome(outcome: RuleOutcome, blind: Sequence[Interval]) -> OutcomeStatus:
    """Classify an outcome: any overlap with blind time makes it BLIND, compliant or not.

    A rule on a BLIND camera is reported BLIND: a compliant result is not evidence of compliance,
    and a violation may come from the broken view (a frozen frame, a missed hand wash).

    Args:
        outcome: The rule outcome.
        blind: The camera's blind intervals.

    Returns:
        The outcome's status.
    """
    span = Interval(start=outcome.start, end=outcome.end)
    if any(overlaps(span, b) for b in blind):
        return OutcomeStatus.BLIND
    return OutcomeStatus.COMPLIANT if outcome.compliant else OutcomeStatus.VIOLATION


def tally_compliance(
    outcomes: Sequence[RuleOutcome], blind_by_camera: Mapping[str, Sequence[Interval]]
) -> ComplianceTally:
    """Count outcomes, excluding any that overlap their camera's blind time.

    Args:
        outcomes: Rule outcomes, possibly from several cameras.
        blind_by_camera: Blind intervals per camera (e.g. from `CoverageWatchdog.blind_intervals`);
            a camera missing from the mapping has no blind time.

    Returns:
        The tally.
    """
    counts = dict.fromkeys(OutcomeStatus, 0)
    for outcome in outcomes:
        counts[classify_outcome(outcome, blind_by_camera.get(outcome.camera_id, ()))] += 1
    return ComplianceTally(
        compliant=counts[OutcomeStatus.COMPLIANT],
        violations=counts[OutcomeStatus.VIOLATION],
        blind=counts[OutcomeStatus.BLIND],
    )
