"""The default `MaskDecoder`: Meta's `meta-sam-parser` behind our wire model.

`meta-sam-parser` (Meta Platforms, SAM License) is the only public decoder of SAM 3.1's base85 mask
payloads (`~` lossless, `!` one_bit). This adapter is the only module that imports it.
"""

from __future__ import annotations

import numpy as np
from meta_sam_parser import InvalidSegmentationMaskError, SegmentationMask, decode_mask_to_raster
from numpy.typing import NDArray

from rulecam.clients.errors import ModelResponseError
from rulecam.clients.meta_hosted.sam_wire import WireMask

_MARKERS = {"lossless": "~", "one_bit": "!"}


def decode_wire_mask(mask: WireMask) -> NDArray[np.uint8]:
    """Decode a mask token into a row-major 0/1 raster of shape (height, width).

    Args:
        mask: The parsed mask token; its payload excludes the encoding marker.

    Returns:
        The raster, covering the detection's box (see `sam_wire.mask_rle`).

    Raises:
        ModelResponseError: If the payload is malformed or does not fill the raster.
    """
    wire = SegmentationMask(
        encoding=mask.encoding,
        payload=_MARKERS[mask.encoding] + mask.payload,
        width=mask.width,
        height=mask.height,
    )
    try:
        raw = decode_mask_to_raster(wire)
    except InvalidSegmentationMaskError as exc:
        msg = f"SAM mask payload could not be decoded ({mask.height}x{mask.width} {mask.encoding})"
        raise ModelResponseError(msg) from exc
    return np.frombuffer(raw, dtype=np.uint8).reshape((mask.height, mask.width)).copy()
