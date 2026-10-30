from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from rulecam.perception import rle


def _box_4x6() -> np.ndarray:
    mask = np.zeros((4, 6), dtype=bool)
    mask[1:3, 2:5] = True
    return mask


def _corners_5x5() -> np.ndarray:
    mask = np.zeros((5, 5), dtype=bool)
    mask[0, 0] = mask[4, 4] = True
    mask[2, 1:4] = True
    return mask


# Reference strings produced by pycocotools `mask.encode` (see the PR for the cross-check run).
@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        (_box_4x6(), "9220003"),
        (_corners_5x5(), "0160N00020"),
        (np.ones((3, 3), dtype=bool), "09"),
    ],
    ids=["box", "corners-with-negative-deltas", "full"],
)
def test_encode_matches_pycocotools(mask: np.ndarray, expected: str) -> None:
    assert rle.encode(mask) == expected
    assert np.array_equal(rle.decode(expected, *mask.shape), mask)


def test_run_lengths_start_with_zeros() -> None:
    assert rle.run_lengths(np.ones((2, 2), dtype=bool)) == [0, 4]
    assert rle.run_lengths(np.zeros((2, 2), dtype=bool)) == [4]
    assert rle.run_lengths(np.zeros((0, 3), dtype=bool)) == []


def test_large_counts_use_multi_character_groups() -> None:
    mask = np.zeros((720, 1280), dtype=bool)
    mask[100:600, 200:1100] = True
    encoded = rle.encode(mask)
    assert encoded.startswith("Tg\\4d?l6")
    assert np.array_equal(rle.decode(encoded, 720, 1280), mask)


@given(arrays(np.bool_, st.tuples(st.integers(1, 24), st.integers(1, 24))))
def test_round_trip(mask: np.ndarray) -> None:
    assert np.array_equal(rle.decode(rle.encode(mask), *mask.shape), mask)


@pytest.mark.parametrize("bad", ["9 2", "\x7f"])
def test_decode_rejects_characters_outside_the_alphabet(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid RLE character"):
        rle.decode(bad, 4, 6)


def test_decode_rejects_truncated_count() -> None:
    with pytest.raises(ValueError, match="ends in the middle"):
        rle.decode("T", 4, 6)


def test_decode_rejects_wrong_size() -> None:
    with pytest.raises(ValueError, match="do not cover"):
        rle.decode("9220003", 5, 6)
