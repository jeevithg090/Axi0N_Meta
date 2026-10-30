"""Request and response mapping for the hosted SAM 3.1 API, isolated in one module.

Everything here follows the official guides (read Oct 30 2026):
<https://dev.meta.ai/docs/sam/segmenting> and <https://dev.meta.ai/docs/sam/reading-segmentation>.

- Segmentation is `POST {base}/responses` with model `sam-3.1`; one `input_text` part plus one
  media part (`input_video` with a `file_id` from `POST {base}/files`, purpose `user_data`, or
  `input_image` with a data URL).
- `metadata` values are strings: `include_confidence` and `propagation_direction`.
- The prompt text is either ONE concept noun phrase, or box/point prompts (`<Nf>` frame markers,
  then `id<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|>`; a negative point is `-<|point;x=..;y=..;w=..;
  h=..|>`). The two must never be combined. At most 16 prompted objects.
- Output is one line per frame: `<Nf>id<|box;...|><|mask;x=0;y=0;[c=..;]data=H,W,ENC...|>,...`.
  Frames may arrive out of order; zero matches is an empty completion, not an error.

Assumptions that still need checking against the live API are marked `ASSUMPTION` (tracked in the
GitHub issue linked from the P1-02 PR).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, Protocol

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, model_validator

from rulecam.clients.errors import ModelResponseError
from rulecam.contracts import Frozen
from rulecam.contracts.core import BoxXYXY
from rulecam.perception.masks import NO_MASK, encode_rle

SAM_MODEL = "sam-3.1"
RESPONSES_PATH = "/responses"
FILES_PATH = "/files"
FILE_PURPOSE = "user_data"
MAX_PROMPTED_OBJECTS = 16
MAX_VIDEO_FRAMES = 15_000
VIDEO_CONTENT_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime"}
IMAGE_CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

Direction = Literal["forward", "backward"]
NegativePoint = tuple[int, float, float]  # (frame_idx, x, y) in source pixels

_LINE = re.compile(r"<(\d+)f>(.*)")
_RECORD = re.compile(r"(\d+)<\|box;([^|]*)\|><\|mask;(.*?)\|>(?=,\d+<\|box;|$)", re.DOTALL)
_MASK_DATA = re.compile(r"(\d+),(\d+),([~!])(.*)", re.DOTALL)
_ENCODINGS: dict[str, Literal["lossless", "one_bit"]] = {"~": "lossless", "!": "one_bit"}


class ObjectPrompt(Frozen):
    """What one request asks SAM for: a concept phrase, or box prompts (never both).

    Attributes:
        text: A single concept noun phrase, e.g. "hard hat".
        boxes: Object id -> box (x1, y1, x2, y2) in source pixels, all on `box_frame`.
        box_frame: The frame the boxes are drawn on.
        negative_points: Points that are NOT the object, as (frame_idx, x, y).
    """

    text: str | None = None
    boxes: dict[int, BoxXYXY] = Field(default_factory=dict)
    box_frame: int = Field(default=0, ge=0)
    negative_points: tuple[NegativePoint, ...] = ()

    @model_validator(mode="after")
    def _one_kind_of_prompt(self) -> ObjectPrompt:
        if self.text is not None and (self.boxes or self.negative_points):
            msg = "SAM 3.1 cannot combine a concept (text) prompt with box or point prompts"
            raise ValueError(msg)
        if self.text is None and not self.boxes:
            msg = "a prompt needs a concept phrase or at least one box"
            raise ValueError(msg)
        if self.text is not None and not self.text.strip():
            msg = "the concept phrase is empty"
            raise ValueError(msg)
        if len(self.boxes) > MAX_PROMPTED_OBJECTS:
            msg = f"at most {MAX_PROMPTED_OBJECTS} prompted objects per request"
            raise ValueError(msg)
        return self


class WireMask(Frozen):
    """A mask token as sent by the API, before decoding.

    Attributes:
        height: Raster rows (`H`).
        width: Raster columns (`W`).
        encoding: `lossless` (`~`) or `one_bit` (`!`).
        payload: The base85 payload after the encoding marker.
        confidence: The mask's `c` value, when confidence was requested.
    """

    height: int = Field(ge=0)
    width: int = Field(ge=0)
    encoding: Literal["lossless", "one_bit"]
    payload: str
    confidence: float | None = None


class WireRecord(Frozen):
    """One object in one frame, parsed from an output line.

    Attributes:
        frame_idx: Zero-based source frame.
        object_id: The response's object id (the prompt's id for box prompts).
        box_xyxy: Inclusive corners in source pixels.
        frame_width: Source frame width (`w` on the box token).
        frame_height: Source frame height (`h` on the box token).
        confidence: The box's `c` value, when confidence was requested.
        mask: The undecoded mask token.
    """

    frame_idx: int = Field(ge=0)
    object_id: int = Field(ge=0)
    box_xyxy: BoxXYXY
    frame_width: int = Field(gt=0)
    frame_height: int = Field(gt=0)
    confidence: float | None = None
    mask: WireMask


class MaskDecoder(Protocol):
    """Turns a mask token's payload into a row-major 0/1 raster of shape (height, width).

    The payload format is not documented publicly; Meta's `meta-sam-parser` package decodes it
    (`decode_mask_to_raster`). Adapt that package behind this Protocol once it is a dependency.
    """

    def __call__(self, mask: WireMask) -> NDArray[np.uint8]:
        """Return the decoded raster."""
        ...


# --- requests -----------------------------------------------------------------------------------


def _px(value: float, limit: int) -> int:
    return min(max(round(value), 0), limit - 1)


def _box_token(box: BoxXYXY, width: int, height: int) -> str:
    x1, y1, x2, y2 = box
    return (
        f"<|box;x1={_px(x1, width)};y1={_px(y1, height)};x2={_px(x2, width)};"
        f"y2={_px(y2, height)};w={width};h={height}|>"
    )


def _negative_token(x: float, y: float, width: int, height: int) -> str:
    return f"-<|point;x={_px(x, width)};y={_px(y, height)};w={width};h={height}|>"


def prompt_text(prompt: ObjectPrompt, width: int, height: int) -> str:
    """Render a prompt as the request's `input_text`.

    ASSUMPTION: a negative point applies to every prompted object ("this pixel is not the
    concept"). On the box frame it follows each box; on another frame it gets its own `<Nf>`
    marker with one `id-<|point;...|>` record per object.

    Args:
        prompt: The prompt to render.
        width: Source width in pixels (the API's `w`).
        height: Source height in pixels (the API's `h`).

    Returns:
        The prompt text.
    """
    if prompt.text is not None:
        return prompt.text.strip()
    by_frame: dict[int, list[tuple[float, float]]] = {}
    for frame_idx, x, y in prompt.negative_points:
        by_frame.setdefault(frame_idx, []).append((x, y))
    ids = sorted(prompt.boxes)
    parts: list[str] = []
    for frame_idx in sorted({prompt.box_frame, *by_frame}):
        negatives = "".join(
            _negative_token(x, y, width, height) for x, y in by_frame.get(frame_idx, [])
        )
        if frame_idx == prompt.box_frame:
            records = [f"{i}{_box_token(prompt.boxes[i], width, height)}{negatives}" for i in ids]
        else:
            records = [f"{i}{negatives}" for i in ids]
        parts.append(f"<{frame_idx}f>" + ",".join(records))
    return "".join(parts)


def _metadata(include_confidence: bool, direction: Direction | None) -> dict[str, str]:
    metadata = {"include_confidence": "true" if include_confidence else "false"}
    if direction is not None:
        metadata["propagation_direction"] = direction
    return metadata


def video_request(
    prompt: ObjectPrompt,
    *,
    file_id: str,
    width: int,
    height: int,
    direction: Direction,
    include_confidence: bool,
    model: str = SAM_MODEL,
) -> dict[str, object]:
    """Build the JSON body for one video segmentation request.

    Non-streaming (`stream: false`) so the reply is one JSON document the replay cache can store;
    3-second chunks are small. `propagation_direction` is only sent with box prompts, because the
    guide documents it for box and point prompts only.

    Args:
        prompt: The concept or box prompt.
        file_id: The uploaded chunk's file id.
        width: Source width in pixels.
        height: Source height in pixels.
        direction: Tracking direction for box prompts.
        include_confidence: Whether to ask for `c` values.
        model: The model id.

    Returns:
        The request body.
    """
    content = [
        {"type": "input_text", "text": prompt_text(prompt, width, height)},
        {"type": "input_video", "file_id": file_id},
    ]
    return {
        "model": model,
        "input": [{"type": "message", "role": "user", "content": content}],
        "stream": False,
        "metadata": _metadata(include_confidence, direction if prompt.boxes else None),
    }


def image_request(
    concept: str, *, image_data_url: str, include_confidence: bool, model: str = SAM_MODEL
) -> dict[str, object]:
    """Build the JSON body for one single-image concept request.

    Args:
        concept: One concept noun phrase.
        image_data_url: `data:<mime>;base64,<bytes>`.
        include_confidence: Whether to ask for `c` values.
        model: The model id.

    Returns:
        The request body.
    """
    prompt = ObjectPrompt(text=concept)
    content = [
        {"type": "input_text", "text": prompt_text(prompt, 1, 1)},
        {"type": "input_image", "image_url": image_data_url},
    ]
    return {
        "model": model,
        "input": [{"type": "message", "role": "user", "content": content}],
        "stream": False,
        "metadata": _metadata(include_confidence, None),
    }


# --- responses ----------------------------------------------------------------------------------


def output_text(body: object) -> str:
    """Return the concatenated output text of a non-streaming response.

    ASSUMPTION: the non-streaming body follows the Responses API shape, with the text in
    `output[].content[]` parts of type `output_text` (a top-level `output_text` string is also
    accepted). An empty `output` is a zero-match completion.

    Args:
        body: The decoded JSON body.

    Returns:
        The output text, possibly empty.

    Raises:
        ModelResponseError: If the body is not a JSON object.
    """
    if not isinstance(body, Mapping):
        msg = "SAM response body is not a JSON object"
        raise ModelResponseError(msg)
    direct = body.get("output_text")
    if isinstance(direct, str):
        return direct
    texts: list[str] = []
    for item in body.get("output") or []:
        content = item.get("content") if isinstance(item, Mapping) else None
        for part in content or []:
            if isinstance(part, Mapping) and part.get("type") == "output_text":
                texts.append(str(part.get("text", "")))
    return "\n".join(t for t in texts if t)


def frames_processed(body: object) -> int | None:
    """Return `usage.video_frames_processed`, or `None` if the response does not carry it."""
    usage = body.get("usage") if isinstance(body, Mapping) else None
    value = usage.get("video_frames_processed") if isinstance(usage, Mapping) else None
    return int(value) if isinstance(value, int | float) else None


def _fields(text: str) -> dict[str, str]:
    pairs = (item.split("=", 1) for item in text.split(";") if "=" in item)
    return {key.strip(): value.strip() for key, value in pairs}


def _optional_float(value: str | None) -> float | None:
    return float(value) if value is not None else None


def _parse_mask(text: str) -> WireMask:
    head, sep, data = text.partition("data=")
    found = _MASK_DATA.fullmatch(data) if sep else None
    if found is None:
        msg = "mask token has no valid data field"
        raise ValueError(msg)
    rows, cols, marker, payload = found.groups()
    return WireMask(
        height=int(rows),
        width=int(cols),
        encoding=_ENCODINGS[marker],
        payload=payload,
        confidence=_optional_float(_fields(head).get("c")),
    )


def _parse_record(frame_idx: int, match: re.Match[str]) -> WireRecord:
    object_id, box_text, mask_text = match.groups()
    box = _fields(box_text)
    return WireRecord(
        frame_idx=frame_idx,
        object_id=int(object_id),
        box_xyxy=(float(box["x1"]), float(box["y1"]), float(box["x2"]), float(box["y2"])),
        frame_width=int(box["w"]),
        frame_height=int(box["h"]),
        confidence=_optional_float(box.get("c")),
        mask=_parse_mask(mask_text),
    )


def _parse_line(line: str) -> list[WireRecord]:
    header = _LINE.fullmatch(line)
    if header is None:
        msg = "output line does not start with a frame marker"
        raise ValueError(msg)
    frame_idx, rest = int(header.group(1)), header.group(2)
    records: list[WireRecord] = []
    pos = 0
    while pos < len(rest):
        found = _RECORD.match(rest, pos)
        if found is None:
            msg = f"unparseable record at column {pos}"
            raise ValueError(msg)
        records.append(_parse_record(frame_idx, found))
        pos = found.end() + 1  # skip the comma between records
    return records


def parse_output(text: str) -> list[WireRecord]:
    """Parse SAM output text into records sorted by (frame, object id).

    Args:
        text: The output text; empty means zero matches.

    Returns:
        Every record, sorted, because frames can arrive out of order with box prompts.

    Raises:
        ModelResponseError: If a line does not follow the documented grammar.
    """
    records: list[WireRecord] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.extend(_parse_line(line.strip()))
        except (ValueError, KeyError) as exc:
            msg = f"SAM output line {line_no} is malformed: {exc}"
            raise ModelResponseError(msg) from exc
    return sorted(records, key=lambda r: (r.frame_idx, r.object_id))


def mask_rle(record: WireRecord, decoder: MaskDecoder | None) -> str:
    """Decode a record's mask and encode it as full-frame COCO RLE (`Mask.rle`).

    ASSUMPTION: the raster covers the box, inclusive (`H = y2 - y1 + 1`, `W = x2 - x1 + 1`), as in
    the guide's examples (box 211..270 x 228..254 with `data=27,60`). A raster the size of the
    whole frame is also accepted. Any other size is not placed and gives `NO_MASK`.

    Args:
        record: The parsed record.
        decoder: The payload decoder, or `None` when none is configured.

    Returns:
        The `Mask.rle` string, or `NO_MASK` if the mask cannot be decoded or placed.
    """
    if decoder is None:
        return NO_MASK
    raster = np.asarray(decoder(record.mask), dtype=np.uint8)
    frame = np.zeros((record.frame_height, record.frame_width), dtype=np.uint8)
    if raster.shape == frame.shape:
        return encode_rle(raster)
    x1, y1 = round(record.box_xyxy[0]), round(record.box_xyxy[1])
    rows, cols = raster.shape
    expected = (round(record.box_xyxy[3]) - y1 + 1, round(record.box_xyxy[2]) - x1 + 1)
    if (rows, cols) != expected:
        return NO_MASK
    frame[y1 : y1 + rows, x1 : x1 + cols] = raster[: frame.shape[0] - y1, : frame.shape[1] - x1]
    return encode_rle(frame)
