from __future__ import annotations

import numpy as np
import pytest

from rulecam.privacy import BackgroundPlate
from tests.privacy.helpers import BACKGROUND, HEIGHT, WIDTH, block_mask, scene

RED = (0, 0, 255)


def frame_with_person(mask: np.ndarray) -> np.ndarray:
    frame = scene()
    frame[mask] = RED
    return frame


def test_an_empty_plate_is_neutral_grey() -> None:
    plate = BackgroundPlate((HEIGHT, WIDTH)).render()
    assert plate.shape == (HEIGHT, WIDTH, 3)
    assert (plate == 128).all()


def test_the_plate_is_the_background_once_the_person_has_moved() -> None:
    plate = BackgroundPlate((HEIGHT, WIDTH), dilate_px=0)
    left, right = block_mask(20, 60, 100, 200), block_mask(140, 180, 100, 200)
    plate.update(frame_with_person(left), [left])
    plate.update(frame_with_person(right), [right])
    rendered = plate.render()
    assert (rendered == np.array(BACKGROUND, dtype=np.uint8)).all()
    assert plate.seen_empty().all()


def test_pixels_never_seen_empty_are_filled_from_the_plate_around_them() -> None:
    plate = BackgroundPlate((HEIGHT, WIDTH), dilate_px=0)
    still = block_mask(80, 120, 100, 200)
    for _ in range(3):
        plate.update(frame_with_person(still), [still])
    rendered = plate.render()
    assert not plate.seen_empty()[still].any()
    assert not (rendered == np.array(RED, dtype=np.uint8)).all(axis=-1).any()
    assert np.abs(rendered.astype(int) - np.array(BACKGROUND)).max() <= 1  # spread from neighbours


def test_a_plate_with_no_known_pixel_is_grey_under_the_person_not_the_person() -> None:
    plate = BackgroundPlate((HEIGHT, WIDTH), dilate_px=0)
    everywhere = np.ones((HEIGHT, WIDTH), dtype=np.bool_)
    plate.update(frame_with_person(everywhere), [everywhere])
    assert (plate.render() == 128).all()


def test_a_seeded_empty_frame_covers_a_person_who_never_moves() -> None:
    plate = BackgroundPlate((HEIGHT, WIDTH), window=2, dilate_px=0)
    plate.seed_empty(scene())
    still = block_mask(80, 120, 100, 200)
    for _ in range(5):
        plate.update(frame_with_person(still), [still])
    assert plate.seen_empty().all()
    assert (plate.render() == np.array(BACKGROUND, dtype=np.uint8)).all()


def test_only_the_last_frames_count() -> None:
    plate = BackgroundPlate((HEIGHT, WIDTH), window=2, dilate_px=0)
    for value in (10, 100, 120):
        plate.update(scene((value, value, value)), [])
    assert plate.render()[0, 0, 0] == 110  # median of 100 and 120; the 10 has aged out


def test_dilation_removes_the_halo_of_a_ragged_mask() -> None:
    body = block_mask(80, 120, 100, 200)
    ragged = block_mask(82, 118, 102, 198)  # the mask misses a two-pixel rim of the person
    rim = (150, 80)  # a person pixel the ragged mask does not cover
    for dilate, rim_is_red in ((0, True), (5, False)):
        plate = BackgroundPlate((HEIGHT, WIDTH), dilate_px=dilate)
        for _ in range(3):
            plate.update(frame_with_person(body), [ragged])
        assert (tuple(plate.render()[rim]) == RED) is rim_is_red


def test_wrong_sizes_are_refused() -> None:
    plate = BackgroundPlate((HEIGHT, WIDTH))
    with pytest.raises(ValueError, match="frame"):
        plate.update(np.zeros((10, 10, 3), dtype=np.uint8), [])
    with pytest.raises(ValueError, match="frame"):
        plate.seed_empty(np.zeros((10, 10, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="masks"):
        plate.update(scene(), [np.zeros((5, 5), dtype=np.bool_)])
    with pytest.raises(ValueError, match="window"):
        BackgroundPlate((HEIGHT, WIDTH), window=0)
