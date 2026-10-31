from __future__ import annotations

import numpy as np
import pytest

from rulecam.privacy import blur_faces
from tests.privacy.helpers import HEIGHT, WIDTH, block_mask


def noisy() -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.integers(0, 256, size=(HEIGHT, WIDTH, 3), dtype=np.uint8)


def test_the_face_region_loses_its_detail_and_the_rest_is_untouched() -> None:
    frame = noisy()
    face = block_mask(80, 120, 60, 100)
    result = blur_faces(frame, [face], dilate_px=0)
    assert result[face].std() < 0.3 * frame[face].std()
    assert (result[~face] == frame[~face]).all()


def test_the_mask_is_grown_so_a_ragged_edge_leaves_no_ring_of_face() -> None:
    frame = noisy()
    face = block_mask(80, 120, 60, 100)
    result = blur_faces(frame, [face], dilate_px=4)
    ring = block_mask(76, 124, 56, 104) & ~face
    assert (result[ring] != frame[ring]).any()


def test_input_is_not_modified_and_no_faces_is_a_copy() -> None:
    frame = noisy()
    before = frame.copy()
    blur_faces(frame, [block_mask(80, 120, 60, 100)])
    assert (frame == before).all()
    copy = blur_faces(frame, [])
    assert (copy == frame).all()
    assert copy is not frame


def test_an_empty_mask_changes_nothing_and_a_wrong_size_is_refused() -> None:
    frame = noisy()
    assert (blur_faces(frame, [np.zeros((HEIGHT, WIDTH), dtype=np.bool_)]) == frame).all()
    with pytest.raises(ValueError, match="frame size"):
        blur_faces(frame, [np.ones((5, 5), dtype=np.bool_)])
