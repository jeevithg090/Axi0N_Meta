"""Parsing and request-building edge cases of the SAM 3.1 wire mapping."""

from __future__ import annotations

import numpy as np
import pytest

from rulecam.clients.errors import ModelResponseError
from rulecam.clients.meta_hosted.sam_wire import (
    ObjectPrompt,
    WireMask,
    WireRecord,
    frames_processed,
    mask_rle,
    output_text,
    parse_output,
)
from rulecam.perception.masks import NO_MASK, decode_rle

LINE = (
    "<4f>2<|box;x1=1;y1=2;x2=3;y2=5;w=8;h=6|><|mask;x=0;y=0;data=4,3,!a,b|>c|>,"
    "7<|box;x1=0;y1=0;x2=1;y2=1;w=8;h=6;c=0.5|><|mask;x=0;y=0;data=2,2,~zz|>"
)


def test_payload_may_contain_commas_markers_and_bars() -> None:
    records = parse_output(LINE)

    assert [(r.frame_idx, r.object_id) for r in records] == [(4, 2), (4, 7)]
    assert records[0].mask.payload == "a,b|>c"
    assert records[0].mask.encoding == "one_bit"
    assert records[1].mask.encoding == "lossless"
    assert records[1].confidence == 0.5
    assert records[0].confidence is None


def test_blank_lines_are_ignored_and_empty_text_is_no_match() -> None:
    assert parse_output("") == []
    assert len(parse_output(f"\n{LINE}\n\n")) == 2


@pytest.mark.parametrize(
    "line",
    [
        "0<|box;x1=1|>",  # no frame marker
        "<0f>garbage",
        "<0f>1<|box;x1=1;y1=2;x2=3;y2=4;w=8;h=6|><|mask;x=0;y=0;data=bad|>",
        "<0f>1<|box;x1=1;y1=2;x2=3;w=8;h=6|><|mask;x=0;y=0;data=1,1,~p|>",  # no y2
    ],
)
def test_malformed_lines_raise_response_error(line: str) -> None:
    with pytest.raises(ModelResponseError, match="line 1"):
        parse_output(line)


def test_output_text_shapes() -> None:
    assert output_text({"output_text": "<0f>"}) == "<0f>"
    assert output_text({"output": []}) == ""
    assert output_text({"output": [{"content": [{"type": "refusal", "text": "x"}]}]}) == ""
    with pytest.raises(ModelResponseError):
        output_text(["not", "an", "object"])


def test_frames_processed_is_optional() -> None:
    assert frames_processed({"usage": {"video_frames_processed": 12}}) == 12
    assert frames_processed({"usage": {}}) is None
    assert frames_processed(None) is None


def _record(rows: int, cols: int) -> WireRecord:
    return WireRecord(
        frame_idx=0,
        object_id=1,
        box_xyxy=(2.0, 1.0, 4.0, 2.0),
        frame_width=8,
        frame_height=6,
        mask=WireMask(height=rows, width=cols, encoding="lossless", payload="p"),
    )


def test_full_frame_rasters_are_accepted_as_is() -> None:
    raster = np.zeros((6, 8), dtype=np.uint8)
    raster[5, 7] = 1

    rle = mask_rle(_record(6, 8), lambda mask: raster)

    assert (decode_rle(rle) == raster).all()


def test_rasters_of_unexpected_size_are_not_placed() -> None:
    assert mask_rle(_record(3, 3), lambda mask: np.ones((3, 3), np.uint8)) == NO_MASK


def test_box_sized_raster_lands_on_the_box() -> None:
    rle = mask_rle(_record(2, 3), lambda mask: np.ones((2, 3), np.uint8))

    frame = decode_rle(rle)
    assert frame[1:3, 2:5].all()
    assert frame.sum() == 6


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "at least one box"),
        ({"text": "  "}, "empty"),
        ({"boxes": dict.fromkeys(range(17), (0.0, 0.0, 1.0, 1.0))}, "at most 16"),
    ],
)
def test_invalid_prompts(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ObjectPrompt.model_validate(kwargs)
