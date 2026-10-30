"""COCO run-length encoding for `Mask.rle`.

`Mask.rle` holds a compact JSON object `{"size": [height, width], "counts": "<string>"}`: the
COCO compressed RLE that `pycocotools.mask.encode` produces (column-major runs starting with a run
of zeros, LEB128-style character packing). Any COCO tool can decode it. An empty string
(`NO_MASK`) means the segmenter returned a box but its mask could not be decoded; consumers then
fall back to the box.
"""

from __future__ import annotations

import json

import numpy as np
from numpy.typing import NDArray

NO_MASK = ""

_CHAR_OFFSET = 48
_LOW_BITS = 0x1F
_SIGN_BIT = 0x10
_MORE_BIT = 0x20
_BITS_PER_CHAR = 5
_DELTA_START = 2  # counts after index 2 are stored as deltas from the count two places back


def _run_lengths(flat: NDArray[np.bool_]) -> list[int]:
    """Return alternating run lengths of `flat`, starting with the run of zeros (maybe 0)."""
    if flat.size == 0:
        return []
    change_at = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    bounds = np.concatenate(([0], change_at, [flat.size]))
    runs = [int(n) for n in np.diff(bounds)]
    return [0, *runs] if bool(flat[0]) else runs


def counts_to_string(counts: list[int]) -> str:
    """Pack run lengths into COCO's compressed RLE string (`rleToString` in `maskApi.c`).

    Args:
        counts: Alternating run lengths, starting with zeros.

    Returns:
        The compressed counts string.
    """
    out: list[str] = []
    for i, count in enumerate(counts):
        value = count - counts[i - 2] if i > _DELTA_START else count
        more = True
        while more:
            char = value & _LOW_BITS
            value >>= _BITS_PER_CHAR
            more = value != -1 if char & _SIGN_BIT else value != 0
            if more:
                char |= _MORE_BIT
            out.append(chr(char + _CHAR_OFFSET))
    return "".join(out)


def string_to_counts(packed: str) -> list[int]:
    """Unpack COCO's compressed RLE string (`rleFrString` in `maskApi.c`).

    Args:
        packed: The compressed counts string.

    Returns:
        Alternating run lengths, starting with zeros.
    """
    counts: list[int] = []
    pos = 0
    while pos < len(packed):
        value = 0
        shift = 0
        more = True
        while more:
            char = ord(packed[pos]) - _CHAR_OFFSET
            value |= (char & _LOW_BITS) << shift
            more = bool(char & _MORE_BIT)
            pos += 1
            shift += _BITS_PER_CHAR
            if not more and char & _SIGN_BIT:
                value |= -1 << shift
        if len(counts) > _DELTA_START:
            value += counts[-2]
        counts.append(value)
    return counts


def encode_rle(mask: NDArray[np.generic]) -> str:
    """Encode a 2-D binary mask as the `Mask.rle` JSON string.

    Args:
        mask: Array of shape (height, width); non-zero means foreground.

    Returns:
        Compact JSON `{"size": [height, width], "counts": "..."}`.

    Raises:
        ValueError: If `mask` is not two-dimensional.
    """
    if mask.ndim != 2:  # noqa: PLR2004 - a mask is a 2-D raster
        msg = f"mask must be 2-D, got shape {mask.shape}"
        raise ValueError(msg)
    height, width = mask.shape
    flat = np.asarray(mask, dtype=bool).ravel(order="F")
    payload = {"size": [int(height), int(width)], "counts": counts_to_string(_run_lengths(flat))}
    return json.dumps(payload, separators=(",", ":"))


def decode_rle(rle: str) -> NDArray[np.uint8]:
    """Decode a `Mask.rle` JSON string into a 0/1 raster.

    Args:
        rle: The JSON produced by `encode_rle` (or any COCO compressed RLE as JSON).

    Returns:
        A `uint8` array of shape (height, width) holding 0 and 1.

    Raises:
        ValueError: If `rle` is `NO_MASK`, malformed, or its runs do not cover the raster.
    """
    if rle == NO_MASK:
        msg = "mask was not decoded (empty rle); use the box instead"
        raise ValueError(msg)
    try:
        data = json.loads(rle)
        height, width = (int(v) for v in data["size"])
        counts = string_to_counts(str(data["counts"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        msg = "malformed COCO RLE"
        raise ValueError(msg) from exc
    if any(c < 0 for c in counts) or sum(counts) != height * width:
        msg = f"RLE runs do not cover a {height}x{width} raster"
        raise ValueError(msg)
    values = (np.arange(len(counts)) % 2).astype(np.uint8)
    flat = np.repeat(values, counts)
    return flat.reshape((height, width), order="F")
