"""Face masking for standard-mode clips that leave a module (task P4-05)."""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]
BoolMask = NDArray[np.bool_]

DEFAULT_DILATE_PX = 4
MIN_KERNEL = 31


def _odd(value: int) -> int:
    return value if value % 2 else value + 1


def blur_faces(
    frame: Frame, face_masks: Sequence[BoolMask], *, dilate_px: int = DEFAULT_DILATE_PX
) -> Frame:
    """Blur every face region, heavily enough that features are not recognisable.

    The blur strength scales with the face's size, and each mask is grown by `dilate_px` so a
    ragged mask edge does not leave a ring of face.

    Args:
        frame: A BGR frame.
        face_masks: One boolean mask per face, each the size of the frame.
        dilate_px: Pixels to grow each mask by.

    Returns:
        A copy of the frame with the faces blurred.

    Raises:
        ValueError: If a mask is not the size of the frame.
    """
    result = frame.copy()
    size = 2 * dilate_px + 1
    kernel = np.ones((size, size), dtype=np.uint8)
    for mask in face_masks:
        if mask.shape != frame.shape[:2]:
            raise ValueError("face masks must match the frame size")
        if not mask.any():
            continue
        grown = cv2.dilate(mask.astype(np.uint8), kernel).astype(np.bool_)
        rows, cols = np.nonzero(grown)
        extent = int(max(rows.max() - rows.min(), cols.max() - cols.min()) + 1)
        side = _odd(max(MIN_KERNEL, extent // 2))
        blurred = cv2.GaussianBlur(frame, (side, side), 0)
        result[grown] = blurred[grown]
    return result
