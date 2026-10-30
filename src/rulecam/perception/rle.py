"""COCO run-length encoding of binary masks, as stored in `Mask.rle`.

The format is the compressed COCO RLE string used by pycocotools (`maskApi.c`): run lengths over
the mask in column-major order, starting with a run of zeros, written as 6-bit ASCII groups
(characters 48 to 111) with every count after the second stored as a delta to the count two places
before it. The image size is not part of the string; it comes from the chunk's width and height.

The self-hosted SAM 3.1 worker uses `encode` so its masks match the hosted API's; anything that
needs pixels (predicates on masks, the static cache) uses `decode`.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

_CHAR_OFFSET = 48
_GROUP_BITS = 5
_GROUP_MASK = 0x1F
_SIGN_BIT = 0x10
_MORE_BIT = 0x20
_DELTA_FROM_INDEX = 3  # pycocotools deltas counts from the fourth one on (`i > 2`)


def run_lengths(mask: NDArray[np.bool_]) -> list[int]:
    """Return the COCO run lengths of a 2-D mask (column-major, zeros first).

    Args:
        mask: A `(height, width)` boolean mask.

    Returns:
        Alternating run lengths of 0s and 1s; the first run is 0 when the mask starts with a 1.
    """
    flat = np.asarray(mask, dtype=bool).ravel(order="F")
    if flat.size == 0:
        return []
    changes = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    boundaries = np.concatenate(([0], changes, [flat.size]))
    runs = [int(n) for n in np.diff(boundaries)]
    return [0, *runs] if flat[0] else runs


def counts_to_string(counts: list[int]) -> str:
    """Compress run lengths into a COCO RLE string (pycocotools `rleToString`).

    Args:
        counts: Run lengths as returned by `run_lengths`.

    Returns:
        The compressed ASCII string.
    """
    chars: list[str] = []
    for i, count in enumerate(counts):
        value = count - counts[i - 2] if i >= _DELTA_FROM_INDEX else count
        more = True
        while more:
            group = value & _GROUP_MASK
            value >>= _GROUP_BITS
            more = value != -1 if group & _SIGN_BIT else value != 0
            if more:
                group |= _MORE_BIT
            chars.append(chr(group + _CHAR_OFFSET))
    return "".join(chars)


def string_to_counts(rle: str) -> list[int]:
    """Expand a COCO RLE string into run lengths (pycocotools `rleFrString`).

    Args:
        rle: The compressed ASCII string.

    Returns:
        The run lengths.

    Raises:
        ValueError: If the string contains a character outside the RLE alphabet or ends mid-count.
    """
    counts: list[int] = []
    position = 0
    while position < len(rle):
        value, shift, more = 0, 0, True
        while more:
            if position >= len(rle):
                msg = "RLE string ends in the middle of a count"
                raise ValueError(msg)
            group = ord(rle[position]) - _CHAR_OFFSET
            if not 0 <= group <= _GROUP_MASK | _MORE_BIT:
                msg = f"invalid RLE character {rle[position]!r}"
                raise ValueError(msg)
            value |= (group & _GROUP_MASK) << (_GROUP_BITS * shift)
            more = bool(group & _MORE_BIT)
            position += 1
            shift += 1
            if not more and group & _SIGN_BIT:
                value |= -1 << (_GROUP_BITS * shift)
        if len(counts) >= _DELTA_FROM_INDEX:
            value += counts[-2]
        counts.append(value)
    return counts


def encode(mask: NDArray[np.bool_]) -> str:
    """Encode a 2-D boolean mask as a compressed COCO RLE string.

    Args:
        mask: A `(height, width)` boolean mask.

    Returns:
        The compressed RLE string.
    """
    return counts_to_string(run_lengths(mask))


def decode(rle: str, height: int, width: int) -> NDArray[np.bool_]:
    """Decode a compressed COCO RLE string into a boolean mask.

    Args:
        rle: The compressed RLE string.
        height: Mask height in pixels.
        width: Mask width in pixels.

    Returns:
        A `(height, width)` boolean mask.

    Raises:
        ValueError: If the string is malformed or its runs do not cover `height * width` pixels.
    """
    counts = string_to_counts(rle)
    if any(count < 0 for count in counts) or sum(counts) != height * width:
        msg = f"RLE runs do not cover a {height}x{width} mask"
        raise ValueError(msg)
    values = np.zeros(len(counts), dtype=bool)
    values[1::2] = True
    return np.repeat(values, counts).reshape((height, width), order="F")
