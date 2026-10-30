"""SAM 3.1 cost accounting for the cost panel (https://dev.meta.ai/docs/pricing-rate-limits).

Standard tier: video $0.20 per 1,000 frames with at least 50 frames billed per request; images
$2.50 per 1,000 images.
"""

from __future__ import annotations

import threading

from pydantic import Field

from rulecam.contracts import Frozen

MIN_BILLED_FRAMES = 50
USD_PER_VIDEO_FRAME = 0.20 / 1000
USD_PER_IMAGE = 2.50 / 1000


def billed_frames(frames: int) -> int:
    """Return the frames billed for one video request: `max(50, frames)`."""
    return max(MIN_BILLED_FRAMES, frames)


class CameraCost(Frozen):
    """Accumulated SAM 3.1 spend for one camera.

    Attributes:
        camera_id: The camera.
        video_requests: Video requests made.
        billed_frames: Frames billed across those requests.
        images: Single-image requests made.
    """

    camera_id: str
    video_requests: int = Field(default=0, ge=0)
    billed_frames: int = Field(default=0, ge=0)
    images: int = Field(default=0, ge=0)

    @property
    def usd(self) -> float:
        """Total spend in US dollars."""
        return self.billed_frames * USD_PER_VIDEO_FRAME + self.images * USD_PER_IMAGE


class CostLedger:
    """Thread-safe per-camera counters (requests run in worker threads)."""

    def __init__(self) -> None:
        """Create an empty ledger."""
        self._by_camera: dict[str, CameraCost] = {}
        self._lock = threading.Lock()

    def record_video(self, camera_id: str, frames: int) -> int:
        """Record one video request.

        Args:
            camera_id: The camera the chunk came from.
            frames: `usage.video_frames_processed` (or the chunk's frame count).

        Returns:
            The frames billed for this request.
        """
        billed = billed_frames(frames)
        with self._lock:
            current = self._by_camera.get(camera_id, CameraCost(camera_id=camera_id))
            self._by_camera[camera_id] = current.model_copy(
                update={
                    "video_requests": current.video_requests + 1,
                    "billed_frames": current.billed_frames + billed,
                }
            )
        return billed

    def record_image(self, camera_id: str) -> None:
        """Record one single-image request for `camera_id`."""
        with self._lock:
            current = self._by_camera.get(camera_id, CameraCost(camera_id=camera_id))
            self._by_camera[camera_id] = current.model_copy(update={"images": current.images + 1})

    def snapshot(self) -> dict[str, CameraCost]:
        """Return a copy of every camera's counters."""
        with self._lock:
            return dict(self._by_camera)

    def total_usd(self) -> float:
        """Return the spend across all cameras in US dollars."""
        return sum(cost.usd for cost in self.snapshot().values())
