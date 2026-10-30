"""COCO RLE encoding of `Mask.rle`."""

from __future__ import annotations

import json

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from rulecam.perception.masks import (
    NO_MASK,
    counts_to_string,
    decode_rle,
    encode_rle,
    string_to_counts,
)


def test_matches_pycocotools_strings() -> None:
    # pycocotools.mask.encode gives b"4" for a 2x2 empty mask and b"04" for a full one.
    assert json.loads(encode_rle(np.zeros((2, 2), np.uint8))) == {"size": [2, 2], "counts": "4"}
    assert json.loads(encode_rle(np.ones((2, 2), np.uint8)))["counts"] == "04"


@given(st.lists(st.integers(min_value=0, max_value=10_000), max_size=40))
def test_counts_round_trip(counts: list[int]) -> None:
    assert string_to_counts(counts_to_string(counts)) == counts


@given(
    arrays(np.uint8, st.tuples(st.integers(1, 12), st.integers(1, 12)), elements=st.integers(0, 1))
)
def test_masks_round_trip(mask: np.ndarray) -> None:
    assert (decode_rle(encode_rle(mask)) == mask).all()


def test_column_major_order() -> None:
    mask = np.array([[1, 0], [0, 0]], np.uint8)

    assert string_to_counts(json.loads(encode_rle(mask))["counts"]) == [0, 1, 3]


@pytest.mark.parametrize(
    ("rle", "message"),
    [
        (NO_MASK, "not decoded"),
        ("{not json", "malformed"),
        ('{"size": [2, 2]}', "malformed"),
        ('{"size": [2, 2], "counts": "5"}', "do not cover"),
    ],
)
def test_bad_rle_is_rejected(rle: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decode_rle(rle)


def test_encode_needs_a_2d_raster() -> None:
    with pytest.raises(ValueError, match="2-D"):
        encode_rle(np.zeros((2, 2, 2), np.uint8))
