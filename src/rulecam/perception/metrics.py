"""Perception cost and load counters per camera (task P1-03; feeds E7 and the cost panel).

Counters are kept per camera, never per person or per track (R13). P3's API serves
`PerceptionMetrics.report()` at `GET /metrics/perception`.

Who records what:
- `MotionGate.decide` records the camera time of every chunk and how many concepts it gated;
- `ModalSam31` records each self-hosted call with its GPU-seconds;
- the segmenter router records each runtime call served by hosted SAM 3.1.

Hosted-equivalent cost prices every call as hosted SAM 3.1 video: `max(50, frames)` billed frames
at $0.20 per 1,000 frames. Actual cost prices self-hosted calls by GPU-seconds at
`gpu_usd_per_hour`, and hosted calls at the hosted price. GPU-seconds count only time spent
serving calls; Modal also bills the idle time of the warm container.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum

from rulecam.contracts.core import Frozen

HOSTED_USD_PER_FRAME = 0.20 / 1000
HOSTED_MIN_BILLED_FRAMES = 50
DEFAULT_GPU_USD_PER_HOUR = 0.80  # Modal L4 on-demand list price; set from the real bill for E7
SECONDS_PER_HOUR = 3600.0
ALL_CAMERAS = "all"


class Backend(StrEnum):
    """Which SAM 3.1 deployment served a call."""

    HOSTED = "hosted"
    SELF_HOSTED = "self_hosted"


def hosted_billed_frames(frames: int) -> int:
    """Return the frames hosted SAM 3.1 bills for one video request (minimum 50).

    Args:
        frames: Frames in the request.

    Returns:
        Billed frames.
    """
    return max(HOSTED_MIN_BILLED_FRAMES, frames)


def hosted_usd(frames: int) -> float:
    """Return the hosted SAM 3.1 price of one video request.

    Args:
        frames: Frames in the request.

    Returns:
        US dollars.
    """
    return hosted_billed_frames(frames) * HOSTED_USD_PER_FRAME


class CameraPerceptionMetrics(Frozen):
    """Perception load and cost for one camera (or `"all"` cameras).

    Rates are `None` until some camera time has been recorded, and `gated_fraction` is `None`
    until the gate has considered a concept.
    """

    camera_id: str
    camera_hours: float
    sam_calls: int
    sam_calls_hosted: int
    sam_calls_self_hosted: int
    sam_calls_per_camera_hour: float | None
    gated_fraction: float | None
    hosted_equivalent_usd_per_camera_hour: float | None
    actual_usd_per_camera_hour: float | None
    gpu_seconds: float


class PerceptionMetricsReport(Frozen):
    """What `GET /metrics/perception` returns."""

    cameras: tuple[CameraPerceptionMetrics, ...]
    total: CameraPerceptionMetrics


@dataclass
class CameraCounters:
    """Raw counters for one camera."""

    seconds: float = 0.0
    calls_hosted: int = 0
    calls_self_hosted: int = 0
    gpu_seconds: float = 0.0
    hosted_equivalent_usd: float = 0.0
    actual_usd: float = 0.0
    concepts_considered: int = 0
    concepts_gated: int = 0

    def add(self, other: CameraCounters) -> None:
        """Add another camera's counters into this one."""
        self.seconds += other.seconds
        self.calls_hosted += other.calls_hosted
        self.calls_self_hosted += other.calls_self_hosted
        self.gpu_seconds += other.gpu_seconds
        self.hosted_equivalent_usd += other.hosted_equivalent_usd
        self.actual_usd += other.actual_usd
        self.concepts_considered += other.concepts_considered
        self.concepts_gated += other.concepts_gated


def summarize(camera_id: str, counters: CameraCounters) -> CameraPerceptionMetrics:
    """Turn raw counters into per-camera-hour figures.

    Args:
        camera_id: The camera, or `"all"`.
        counters: Its raw counters.

    Returns:
        The summary.
    """
    hours = counters.seconds / SECONDS_PER_HOUR
    calls = counters.calls_hosted + counters.calls_self_hosted

    def per_hour(value: float) -> float | None:
        return value / hours if hours > 0 else None

    gated = (
        counters.concepts_gated / counters.concepts_considered
        if counters.concepts_considered
        else None
    )
    return CameraPerceptionMetrics(
        camera_id=camera_id,
        camera_hours=hours,
        sam_calls=calls,
        sam_calls_hosted=counters.calls_hosted,
        sam_calls_self_hosted=counters.calls_self_hosted,
        sam_calls_per_camera_hour=per_hour(calls),
        gated_fraction=gated,
        hosted_equivalent_usd_per_camera_hour=per_hour(counters.hosted_equivalent_usd),
        actual_usd_per_camera_hour=per_hour(counters.actual_usd),
        gpu_seconds=counters.gpu_seconds,
    )


class PerceptionMetrics:
    """Thread-safe perception counters, one set per camera."""

    def __init__(self, *, gpu_usd_per_hour: float = DEFAULT_GPU_USD_PER_HOUR) -> None:
        """Create empty counters.

        Args:
            gpu_usd_per_hour: Price of one GPU-hour, used for the actual self-hosted cost.
        """
        self._gpu_usd_per_second = gpu_usd_per_hour / SECONDS_PER_HOUR
        self._lock = threading.Lock()
        self._cameras: dict[str, CameraCounters] = {}

    def record_chunk(self, camera_id: str, seconds: float) -> None:
        """Record camera time covered by one processed chunk.

        Args:
            camera_id: The camera.
            seconds: Chunk duration (`n_frames / fps`).
        """
        with self._lock:
            self._counters(camera_id).seconds += seconds

    def record_gate(self, camera_id: str, *, considered: int, gated: int) -> None:
        """Record one motion-gate decision.

        Args:
            camera_id: The camera.
            considered: Dynamic concepts the gate looked at for the chunk.
            gated: How many of them it skipped.
        """
        with self._lock:
            counters = self._counters(camera_id)
            counters.concepts_considered += considered
            counters.concepts_gated += gated

    def record_sam_call(
        self, camera_id: str, *, backend: Backend, frames: int, gpu_seconds: float = 0.0
    ) -> None:
        """Record one SAM 3.1 video request.

        Args:
            camera_id: The camera.
            backend: Which deployment served it.
            frames: Frames in the request.
            gpu_seconds: GPU time used (self-hosted only).
        """
        hosted_price = hosted_usd(frames)
        with self._lock:
            counters = self._counters(camera_id)
            counters.hosted_equivalent_usd += hosted_price
            if backend is Backend.HOSTED:
                counters.calls_hosted += 1
                counters.actual_usd += hosted_price
            else:
                counters.calls_self_hosted += 1
                counters.gpu_seconds += gpu_seconds
                counters.actual_usd += gpu_seconds * self._gpu_usd_per_second

    def report(self) -> PerceptionMetricsReport:
        """Return per-camera and total figures (the body of `GET /metrics/perception`).

        Returns:
            The report, cameras sorted by id.
        """
        with self._lock:
            snapshot = {camera: CameraCounters(**vars(c)) for camera, c in self._cameras.items()}
        total = CameraCounters()
        for counters in snapshot.values():
            total.add(counters)
        return PerceptionMetricsReport(
            cameras=tuple(summarize(camera, snapshot[camera]) for camera in sorted(snapshot)),
            total=summarize(ALL_CAMERAS, total),
        )

    def _counters(self, camera_id: str) -> CameraCounters:
        return self._cameras.setdefault(camera_id, CameraCounters())
