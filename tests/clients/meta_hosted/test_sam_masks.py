"""The `meta-sam-parser` mask decoder on REAL recorded SAM 3.1 masks (Oct 30 2026, issue #5)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from rulecam.clients.errors import ModelResponseError
from rulecam.clients.meta_hosted.sam import HostedSam31
from rulecam.clients.meta_hosted.sam_masks import decode_wire_mask
from rulecam.clients.meta_hosted.sam_wire import (
    WireMask,
    WireRecord,
    mask_rle,
    output_text,
    parse_output,
)
from rulecam.contracts import Chunk
from rulecam.perception.masks import decode_rle, encode_rle
from tests.clients.meta_hosted.transport_fakes import SAM_FIXTURES, ScriptedTransport
from tests.fakes import EPOCH, FakeClock

LIVE = SAM_FIXTURES / "live"


def records(name: str) -> list[WireRecord]:
    reply = json.loads((LIVE / f"{name}.json").read_text("utf-8"))
    return parse_output(output_text(reply["json"]))


def assert_mask_matches_box(record: WireRecord) -> None:
    raster = decode_wire_mask(record.mask)
    assert raster.shape == (record.mask.height, record.mask.width)
    assert set(np.unique(raster)) <= {0, 1}
    assert raster.any()
    assert (decode_rle(encode_rle(raster)) == raster).all()  # RLE round trip

    frame = decode_rle(mask_rle(record, decode_wire_mask))
    assert frame.shape == (record.frame_height, record.frame_width)
    rows, cols = np.nonzero(frame)
    x1, y1, x2, y2 = (round(v) for v in record.box_xyxy)
    assert rows.min() >= y1
    assert rows.max() <= y2
    assert cols.min() >= x1
    assert cols.max() <= x2
    assert frame.sum() == raster.sum()  # nothing lost when placed on the frame


def test_rectangle_mask_decodes_inside_its_box() -> None:
    first = records("video_rectangle")[0]
    assert first.box_xyxy == (122.0, 262.0, 197.0, 277.0)
    assert (first.mask.height, first.mask.width, first.mask.encoding) == (16, 76, "lossless")

    raster = decode_wire_mask(first.mask)

    assert raster.sum() == 1207  # a filled rectangle with slightly rounded corners
    assert raster[3:14, 2:74].all()
    assert_mask_matches_box(first)


@pytest.mark.parametrize(
    "name", ["video_rectangle", "video_number", "video_backward_negative_point"]
)
def test_every_recorded_mask_decodes_and_stays_in_its_box(name: str) -> None:
    decoded = records(name)

    assert len(decoded) == {"video_rectangle": 46, "video_number": 37}.get(name, 20)
    for record in decoded:
        assert_mask_matches_box(record)


def test_client_decodes_masks_by_default(tmp_path: Path) -> None:
    path = tmp_path / "scene.mp4"
    path.write_bytes(b"\x00\x00\x00 ftypisom")
    chunk = Chunk(
        chunk_id="scene",
        camera_id="live",
        path=str(path),
        start_ts=EPOCH,
        fps=10.0,
        n_frames=30,
        width=640,
        height=360,
    )
    reply = json.loads((LIVE / "video_rectangle.json").read_text("utf-8"))

    detections = HostedSam31(ScriptedTransport(reply), clock=FakeClock()).segment(
        chunk, "rectangle"
    )

    first = decode_rle(detections[0].mask.rle)
    assert first.shape == (360, 640)
    assert first.sum() == 1207
    assert all(d.mask.rle for d in detections)


@pytest.mark.parametrize("encoding", ["lossless", "one_bit"])
def test_malformed_payloads_raise_response_errors(encoding: str) -> None:
    bad = WireMask(height=4, width=4, encoding=encoding, payload="not base85 at all \x00")

    with pytest.raises(ModelResponseError, match="could not be decoded"):
        decode_wire_mask(bad)
