"""Clean background plate: the scene with every person removed (task P4-05, V2 Pillar 7b).

A pixel of the plate is the median, over recent frames, of that pixel while no person covered it.
Pixels that were never seen empty are filled from the plate around them, with no generative
inpainting, so no invented detail and no person pixel ever reaches the plate. Frames are
`uint8` BGR images, height x width x 3.
"""

from __future__ import annotations

import warnings
from collections import deque
from collections.abc import Sequence

import cv2
import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]
BoolMask = NDArray[np.bool_]

NEUTRAL_GREY = 128
DEFAULT_WINDOW = 15
DEFAULT_DILATE_PX = 5
FILL_BLUR_SIGMA = 25.0
MIN_WEIGHT = 1e-6


class BackgroundPlate:
    """A running per-pixel median of person-free pixels.

    Args:
        shape: `(height, width)` of the frames.
        window: Number of recent frames the median is taken over.
        dilate_px: Person masks are grown by this many pixels before use, so a ragged mask edge
            never leaves a halo of the person in the plate.
    """

    def __init__(
        self,
        shape: tuple[int, int],
        *,
        window: int = DEFAULT_WINDOW,
        dilate_px: int = DEFAULT_DILATE_PX,
    ) -> None:
        """Create an empty plate."""
        if window < 1 or dilate_px < 0:
            raise ValueError("window must be at least 1 and dilate_px must not be negative")
        self._shape = shape
        self._dilate_px = dilate_px
        self._recent: deque[tuple[Frame, BoolMask]] = deque(maxlen=window)
        self._pinned: tuple[Frame, BoolMask] | None = None

    def _check(self, frame: Frame) -> None:
        if frame.shape != (*self._shape, 3):
            raise ValueError(f"expected a {self._shape[0]}x{self._shape[1]}x3 frame")

    def seed_empty(self, frame: Frame) -> None:
        """Pin a frame known to contain nobody. It stays in the median however long it runs.

        Raises:
            ValueError: If the frame is the wrong size.
        """
        self._check(frame)
        self._pinned = (frame.copy(), np.ones(self._shape, dtype=np.bool_))

    def update(self, frame: Frame, person_masks: Sequence[BoolMask]) -> None:
        """Add a frame, marking the pixels under any person mask as not usable.

        Args:
            frame: The BGR frame.
            person_masks: One boolean mask per person, each the size of the frame.

        Raises:
            ValueError: If the frame or a mask is the wrong size.
        """
        self._check(frame)
        covered = np.zeros(self._shape, dtype=np.bool_)
        for mask in person_masks:
            if mask.shape != self._shape:
                raise ValueError("person masks must match the frame size")
            covered |= mask
        if self._dilate_px and covered.any():
            size = 2 * self._dilate_px + 1
            kernel = np.ones((size, size), dtype=np.uint8)
            covered = cv2.dilate(covered.astype(np.uint8), kernel).astype(np.bool_)
        self._recent.append((frame.copy(), ~covered))

    def seen_empty(self) -> BoolMask:
        """Pixels that have been seen with nobody on them in at least one frame."""
        valid = np.zeros(self._shape, dtype=np.bool_)
        for _, usable in self._entries():
            valid |= usable
        return valid

    def _entries(self) -> list[tuple[Frame, BoolMask]]:
        pinned = [self._pinned] if self._pinned is not None else []
        return [*pinned, *self._recent]

    def render(self) -> Frame:
        """Return the plate.

        Pixels never seen empty come from a wide blur of the known plate pixels around them, or
        neutral grey if nothing is known at all.
        """
        entries = self._entries()
        if not entries:
            return np.full((*self._shape, 3), NEUTRAL_GREY, dtype=np.uint8)
        stack = np.stack([frame.astype(np.float32) for frame, _ in entries])
        usable = np.stack([mask for _, mask in entries])
        stack[~usable] = np.nan
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN slices are expected
            median = np.nanmedian(stack, axis=0)
        known = ~np.isnan(median[..., 0])
        filled = np.nan_to_num(median, nan=0.0)
        if not known.all():
            weight = known.astype(np.float32)
            blurred = cv2.GaussianBlur(filled * weight[..., None], (0, 0), FILL_BLUR_SIGMA)
            norm = cv2.GaussianBlur(weight, (0, 0), FILL_BLUR_SIGMA)[..., None]
            spread = np.where(
                norm > MIN_WEIGHT, blurred / np.maximum(norm, MIN_WEIGHT), NEUTRAL_GREY
            )
            filled = np.where(known[..., None], filled, spread)
        return np.clip(filled, 0, 255).astype(np.uint8)
