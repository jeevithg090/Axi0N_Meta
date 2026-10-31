"""Motion gate: skip dynamic SAM concepts on still chunks (task P1-03, MP §3.4).

Motion is measured on grayscale frames downscaled to 160 px wide, for every pair of consecutive
frames (the previous chunk's last frame is prepended, so motion across a chunk boundary counts):

- `mean_abs_diff`: the mean absolute grey-level difference of the pair;
- `changed_fraction`: the fraction of pixels whose grey level changed by more than 15.

A chunk's statistics are the maximum over its pairs, so one moving pair is enough to keep the
chunk. When both statistics are under their thresholds the chunk is still, and its *dynamic*
concepts (`ConceptSpec.static` is false) are skipped, except that:

- a concept used by any active rule with `severity=high` is never gated;
- every concept still runs on a keyframe, at least once every `keyframe_s` (default 30 s).

Static concepts are not the gate's business: the static cache serves them.

`motion_stats` and `decide` are pure; `MotionGate` keeps the per-camera keyframe clock and the
previous frame, and `read_gray_frames` is the only I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from rulecam.contracts.core import Chunk, Frozen, Severity
from rulecam.contracts.rule_ir import RuleIR
from rulecam.log import get_logger
from rulecam.perception.errors import PerceptionError
from rulecam.perception.metrics import PerceptionMetrics

GRAY_WIDTH = 160
PIXEL_DELTA = 15
_RGB_CHANNELS = 3

GrayFrame = NDArray[np.uint8]

_log = get_logger(__name__)


class MotionStats(Frozen):
    """Peak motion inside one chunk (maximum over consecutive frame pairs)."""

    mean_abs_diff: float = Field(ge=0)
    changed_fraction: float = Field(ge=0, le=1)


class GateConfig(Frozen):
    """Thresholds of the motion gate (per site).

    The defaults are deliberately low, so the gate only skips chunks that are clearly still.
    """

    max_mean_abs_diff: float = Field(default=1.0, ge=0)
    max_changed_fraction: float = Field(default=0.001, ge=0, le=1)
    keyframe_s: float = Field(default=30.0, gt=0)


class GateDecision(Frozen):
    """Which dynamic concepts to segment for one chunk."""

    run: tuple[str, ...]
    skipped: tuple[str, ...]
    still: bool
    keyframe: bool
    stats: MotionStats | None


def downscale_gray(frame: NDArray[np.uint8], width: int = GRAY_WIDTH) -> GrayFrame:
    """Convert a BGR or grayscale frame to grayscale `width` pixels wide (aspect kept).

    Args:
        frame: An `(h, w, 3)` BGR or `(h, w)` grayscale frame.
        width: Output width.

    Returns:
        The downscaled grayscale frame.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == _RGB_CHANNELS else frame
    height = max(1, round(gray.shape[0] * width / gray.shape[1]))
    resized = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
    return np.asarray(resized, dtype=np.uint8)


def motion_stats(frames: Sequence[GrayFrame], *, pixel_delta: int = PIXEL_DELTA) -> MotionStats:
    """Measure peak motion across consecutive frames.

    Args:
        frames: At least two downscaled grayscale frames of the same shape.
        pixel_delta: A pixel counts as changed when its grey level moves by more than this.

    Returns:
        The maximum mean absolute difference and changed fraction over all consecutive pairs.

    Raises:
        ValueError: With fewer than two frames.
    """
    if len(frames) < 2:  # noqa: PLR2004 - motion needs a pair of frames
        msg = "motion needs at least two frames"
        raise ValueError(msg)
    peak_mean, peak_fraction = 0.0, 0.0
    for previous, current in pairwise(frames):
        diff = np.abs(current.astype(np.int16) - previous.astype(np.int16))
        peak_mean = max(peak_mean, float(diff.mean()))
        peak_fraction = max(peak_fraction, float((diff > pixel_delta).mean()))
    return MotionStats(mean_abs_diff=peak_mean, changed_fraction=peak_fraction)


def is_still(stats: MotionStats | None, config: GateConfig) -> bool:
    """Return whether a chunk is still (unknown motion is never still).

    Args:
        stats: The chunk's motion, or `None` if it could not be measured.
        config: Gate thresholds.

    Returns:
        True when both statistics are under their thresholds.
    """
    return (
        stats is not None
        and stats.mean_abs_diff < config.max_mean_abs_diff
        and stats.changed_fraction < config.max_changed_fraction
    )


def dynamic_concepts(rules: Iterable[RuleIR]) -> frozenset[str]:
    """Return the concepts that some active rule needs segmented every chunk.

    Args:
        rules: Active rules.

    Returns:
        Concept names whose `ConceptSpec.static` is false in at least one rule.
    """
    return frozenset(
        name for rule in rules for name, spec in rule.concepts.items() if not spec.static
    )


def protected_concepts(rules: Iterable[RuleIR]) -> frozenset[str]:
    """Return the concepts of high-severity rules, which are never gated.

    Args:
        rules: Active rules.

    Returns:
        Concept names used by any rule with `severity=high`.
    """
    return frozenset(
        name for rule in rules if rule.severity is Severity.HIGH for name in rule.concepts
    )


def decide(
    rules: Sequence[RuleIR],
    stats: MotionStats | None,
    *,
    chunk_start: datetime,
    last_full_run: datetime | None,
    config: GateConfig,
) -> GateDecision:
    """Decide which dynamic concepts to segment for one chunk.

    Args:
        rules: Active rules on the chunk's camera.
        stats: The chunk's motion, or `None` if unknown (then nothing is gated).
        chunk_start: The chunk's start time.
        last_full_run: When every dynamic concept last ran on this camera, if ever.
        config: Gate thresholds and keyframe cadence.

    Returns:
        The concepts to run and to skip.
    """
    dynamic = dynamic_concepts(rules)
    still = is_still(stats, config)
    keyframe = (
        last_full_run is None or (chunk_start - last_full_run).total_seconds() >= config.keyframe_s
    )
    if not still or keyframe:
        run, skipped = dynamic, frozenset[str]()
    else:
        run = dynamic & protected_concepts(rules)
        skipped = dynamic - run
    return GateDecision(
        run=tuple(sorted(run)),
        skipped=tuple(sorted(skipped)),
        still=still,
        keyframe=keyframe,
        stats=stats,
    )


def read_gray_frames(path: str | Path, width: int = GRAY_WIDTH) -> list[GrayFrame]:
    """Decode a video file into downscaled grayscale frames.

    Args:
        path: Video file.
        width: Output width.

    Returns:
        Every frame, in order.

    Raises:
        PerceptionError: If the file cannot be opened.
    """
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        msg = f"cannot open video {path}"
        raise PerceptionError(msg)
    frames: list[GrayFrame] = []
    try:
        ok, frame = capture.read()
        while ok:
            frames.append(downscale_gray(np.asarray(frame, dtype=np.uint8), width))
            ok, frame = capture.read()
    finally:
        capture.release()
    return frames


class MotionGate:
    """Per-camera motion gate: remembers the last frame and the last full run per camera."""

    def __init__(
        self,
        config: GateConfig | None = None,
        *,
        metrics: PerceptionMetrics | None = None,
        read_frames: Callable[[str], list[GrayFrame]] = read_gray_frames,
    ) -> None:
        """Create a gate.

        Args:
            config: Thresholds and keyframe cadence (defaults to `GateConfig()`).
            metrics: Counters for camera time and gated concepts.
            read_frames: Decodes a chunk into downscaled grayscale frames.
        """
        self._config = config or GateConfig()
        self._metrics = metrics
        self._read_frames = read_frames
        self._last_frame: dict[str, GrayFrame] = {}
        self._last_full_run: dict[str, datetime] = {}

    @property
    def config(self) -> GateConfig:
        """The gate's thresholds."""
        return self._config

    def measure(
        self, chunk: Chunk, frames: Sequence[GrayFrame] | None = None
    ) -> MotionStats | None:
        """Measure a chunk's motion and remember its last frame (for the next chunk's pair).

        The runtime measures every chunk of a camera, also those it then does not segment, so
        motion across a skipped chunk still counts.

        Args:
            chunk: The chunk. Chunks of one camera must arrive in order.
            frames: The chunk's downscaled grayscale frames, if already decoded.

        Returns:
            The motion, or `None` when it cannot be measured (never treated as still).
        """
        camera = chunk.camera_id
        current = list(frames) if frames is not None else self._read_frames(chunk.path)
        previous = self._last_frame.get(camera)
        window = [previous, *current] if previous is not None else current
        if current:
            self._last_frame[camera] = current[-1]
        return motion_stats(window) if _same_shape(window) else None

    def decide(
        self,
        chunk: Chunk,
        rules: Sequence[RuleIR],
        frames: Sequence[GrayFrame] | None = None,
    ) -> GateDecision:
        """Decide which dynamic concepts to segment for `chunk` and update the camera's state.

        Args:
            chunk: The chunk to gate. Chunks of one camera must arrive in order.
            rules: Active rules on the chunk's camera.
            frames: The chunk's downscaled grayscale frames, if already decoded.

        Returns:
            The decision.
        """
        return self.gate(chunk, rules, self.measure(chunk, frames))

    def gate(
        self, chunk: Chunk, rules: Sequence[RuleIR], stats: MotionStats | None
    ) -> GateDecision:
        """Decide which dynamic concepts to segment for `chunk`, from motion already measured.

        Args:
            chunk: The chunk to gate.
            rules: Active rules on the chunk's camera.
            stats: The chunk's motion from `measure` (`None`: unknown, nothing is gated).

        Returns:
            The decision; the camera's keyframe clock restarts when nothing was skipped.
        """
        camera = chunk.camera_id
        decision = decide(
            rules,
            stats,
            chunk_start=chunk.start_ts,
            last_full_run=self._last_full_run.get(camera),
            config=self._config,
        )
        if not decision.skipped:
            self._last_full_run[camera] = chunk.start_ts
        if self._metrics is not None:
            self._metrics.record_chunk(camera, chunk.n_frames / chunk.fps)
            self._metrics.record_gate(
                camera,
                considered=len(decision.run) + len(decision.skipped),
                gated=len(decision.skipped),
            )
        _log.debug(
            "motion_gate",
            camera_id=camera,
            chunk_id=chunk.chunk_id,
            still=decision.still,
            keyframe=decision.keyframe,
            skipped=list(decision.skipped),
        )
        return decision


def _same_shape(frames: Sequence[GrayFrame]) -> bool:
    return len(frames) >= 2 and len({frame.shape for frame in frames}) == 1  # noqa: PLR2004
