"""Drawing and privacy for evidence strips (task P4-05): overlays, distance ruler, plate, faces.

Pure functions on BGR `uint8` frames, in strip pixels. `runtime.strips` decides what is drawn
(the rule's tracks at each keyframe, the zones, the distance ruler) and which privacy mode
applies; this module only draws.

Privacy modes (MP §7.5):

- **standard**: the real frame, with every face blurred (the face detector's boxes plus the head
  region of every tracked person) and the overlays drawn on top.
- **anonymous**: no person pixel of the frame is kept. The background is a clean plate (the
  per-pixel median of the strip's frames over pixels no person box covered; pixels never seen
  empty are neutral grey, no generative inpainting), the non-person objects of the rule (a
  forklift) are pasted from the frame *minus every person box*, faces the detector still finds
  are blurred, and each person is drawn as a skeleton (COCO-17 keypoints when pose is on) or a
  stick figure fitted to the person's box.
- **strict**: as anonymous, but greyscale (no clothing or skin colour), every person is the same
  canonical stick figure in one neutral colour (no per-track colour, no real bone lengths).

**Annotation band.** Every word Rulecam draws goes in a band *appended below* the frame (the
scene's pixels are never covered by text): a header that says the band is Rulecam's, the caption
and a colour legend for the boxes, the distance ruler, the one-metre tick and the zones. On the
frame itself only shapes are drawn (boxes, lines, polygons), so a verifier that copies visible
text into `scene_text` finds none of ours in the scene; `annotation_texts` lists exactly what
the band says, so the verifier can also remove any copy of it (`verify.annotations`).

Never identity: faces are only located to be blurred; nothing is recognised or compared.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from rulecam.contracts.core import BoxXYXY, PointXY

Frame = NDArray[np.uint8]
BoolMask = NDArray[np.bool_]

GREY = 128
PERSON_GROW = 0.12  # each person box grows by this fraction of its size on every side
HEAD_FRACTION = 0.24  # head region: the top of a person box, when the head keypoints are unknown
FACE_GROW = 0.25
PIXELATE_CELLS = 6
KEYPOINT_MIN_SCORE = 0.3
FACE_KEYPOINTS = range(5)  # COCO-17: nose, eyes, ears (never drawn, only blurred)
BONES = (
    (5, 6),
    (5, 7),
    (7, 9),
    (6, 8),
    (8, 10),
    (5, 11),
    (6, 12),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
)
PALETTE = (
    (0, 200, 255),
    (255, 160, 0),
    (80, 220, 80),
    (255, 80, 200),
    (0, 120, 255),
    (200, 200, 0),
)
SUBJECT_COLOUR = (0, 0, 255)
RULER_COLOUR = (0, 255, 255)
ZONE_COLOUR = (255, 255, 0)
STRICT_COLOUR = (230, 230, 230)
FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_SCALE = 0.45
BAND_HEADER = "RULECAM ANNOTATION (not part of the scene)"
BAND_COLOUR = (0, 0, 0)
BAND_EDGE_COLOUR = RULER_COLOUR
BAND_EDGE = 2
BAND_LINE_H = 18
BAND_PAD = 4
SWATCH = 10
LEGEND_GAP = 14
TICK_LABEL = "white tick: 1 m on the ground"
CAPTION_SEPARATOR = "  "


@dataclass(frozen=True)
class Item:
    """One tracked object at one keyframe, in strip pixels.

    Attributes:
        concept: The track's concept (the label drawn; never a track id).
        box: Its box.
        person: Whether it is a person (its pixels are private in anonymous/strict mode).
        subject: Whether it is the candidate's subject (drawn highlighted).
        colour_key: Seed of its colour in anonymous mode (an opaque track id, never drawn).
        keypoints: COCO-17 `(x, y, score)` when pose is on.
    """

    concept: str
    box: BoxXYXY
    person: bool
    subject: bool = False
    colour_key: str = ""
    keypoints: tuple[tuple[float, float, float], ...] | None = None


@dataclass(frozen=True)
class Ruler:
    """A distance annotation between the subject and the nearest other object (calibrated only).

    Attributes:
        a: Subject foot point.
        b: The other object's foot point.
        metres: Ground distance between them.
        tick: A one-metre segment on the ground at the subject's foot, in pixels, if projectable.
    """

    a: PointXY
    b: PointXY
    metres: float
    tick: tuple[PointXY, PointXY] | None = None


@dataclass(frozen=True)
class Overlay:
    """Everything drawn on one keyframe besides the pixels."""

    items: tuple[Item, ...]
    zones: tuple[tuple[str, tuple[PointXY, ...]], ...] = ()
    ruler: Ruler | None = None
    caption: str = ""


class FaceDetector(Protocol):
    """Finds face boxes in a BGR frame, to blur them (never to recognise anyone)."""

    def detect(self, frame: Frame) -> list[BoxXYXY]:
        """Return face boxes in the frame's pixels."""
        ...


class NoFaceDetector:
    """No face model configured: only the head regions of tracked people are blurred."""

    def detect(self, frame: Frame) -> list[BoxXYXY]:  # noqa: ARG002 - protocol signature
        """Find nothing."""
        return []


class YuNetFaces:
    """OpenCV's YuNet face detector (`cv2.FaceDetectorYN`, ONNX model from opencv_zoo)."""

    def __init__(self, model: Path, *, score_threshold: float = 0.6) -> None:
        """Load the model.

        Args:
            model: Path to the YuNet ONNX file (e.g. `face_detection_yunet_2023mar.onnx`).
            score_threshold: Minimum face score. Low on purpose: a missed face is a leak, an
                extra blur costs nothing.

        Raises:
            cv2.error: If the model cannot be loaded.
        """
        self._net = cv2.FaceDetectorYN.create(str(model), "", (320, 320), score_threshold)
        self._lock = threading.Lock()

    def detect(self, frame: Frame) -> list[BoxXYXY]:
        """Return face boxes in the frame's pixels."""
        height, width = frame.shape[:2]
        with self._lock:
            self._net.setInputSize((width, height))
            _, faces = self._net.detect(frame)
        if faces is None:
            return []
        return [
            (float(x), float(y), float(x + w), float(y + h))
            for x, y, w, h in np.asarray(faces)[:, :4].tolist()
        ]


def grow(box: BoxXYXY, fraction: float) -> BoxXYXY:
    """Grow a box by `fraction` of its width and height on every side."""
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * fraction, (y1 - y0) * fraction
    return (x0 - dx, y0 - dy, x1 + dx, y1 + dy)


def box_mask(shape: tuple[int, ...], boxes: Sequence[BoxXYXY]) -> BoolMask:
    """A boolean mask of the frame's size that is true inside any of the boxes (clipped)."""
    height, width = shape[:2]
    mask = np.zeros((height, width), dtype=np.bool_)
    for x0, y0, x1, y1 in boxes:
        c0, r0 = max(0, int(np.floor(x0))), max(0, int(np.floor(y0)))
        c1, r1 = min(width, int(np.ceil(x1))), min(height, int(np.ceil(y1)))
        if c1 > c0 and r1 > r0:
            mask[r0:r1, c0:c1] = True
    return mask


def person_mask(shape: tuple[int, ...], items: Sequence[Item]) -> BoolMask:
    """Pixels covered by any (grown) person box."""
    return box_mask(shape, [grow(i.box, PERSON_GROW) for i in items if i.person])


def clean_plate(frames: Sequence[Frame], masks: Sequence[BoolMask]) -> Frame:
    """Per-pixel median of the frames over the pixels their masks leave uncovered.

    Args:
        frames: Same-size BGR frames.
        masks: One mask per frame; true where a person may be (never used for the plate).

    Returns:
        The plate. A pixel covered in every frame is neutral grey: it was never seen empty, so
        nothing about it is guessed.

    Raises:
        ValueError: If there are no frames, or the sizes differ.
    """
    if not frames or len(frames) != len(masks):
        msg = "clean_plate needs one mask per frame and at least one frame"
        raise ValueError(msg)
    stack = np.stack(frames).astype(np.float32)  # (n, h, w, 3)
    hidden = np.stack(masks)  # (n, h, w)
    if hidden.shape != stack.shape[:3]:
        msg = "masks must match the frames' size"
        raise ValueError(msg)
    stack[hidden] = np.inf
    ordered = np.sort(stack, axis=0)
    seen = (~hidden).sum(axis=0)  # (h, w)
    middle = np.maximum(seen - 1, 0) // 2
    index = np.broadcast_to(middle[None, :, :, None], (1, *stack.shape[1:]))
    plate = np.take_along_axis(ordered, index, axis=0)[0]
    plate[seen == 0] = GREY
    return np.asarray(np.clip(plate, 0, 255), dtype=np.uint8)


def head_box(item: Item) -> BoxXYXY:
    """The region of a person's head: from the face keypoints when known, else the box's top."""
    x0, y0, x1, y1 = item.box
    points = [
        (x, y)
        for k, (x, y, score) in enumerate(item.keypoints or ())
        if k in FACE_KEYPOINTS and score >= KEYPOINT_MIN_SCORE
    ]
    if len(points) >= 2:  # noqa: PLR2004 - two points give a head extent
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        side = max(max(xs) - min(xs), max(ys) - min(ys), (x1 - x0) * 0.3)
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        return (cx - side, cy - side, cx + side, cy + side)
    height = y1 - y0
    if x1 - x0 > height * 1.3:  # lying down: the head is at one end, which is not known
        return item.box
    return (x0, y0, x1, y0 + height * HEAD_FRACTION)


def blur_boxes(frame: Frame, boxes: Sequence[BoxXYXY]) -> Frame:
    """Pixelate then blur each box on a copy of the frame (features are not recoverable)."""
    out = frame.copy()
    height, width = frame.shape[:2]
    for box in boxes:
        x0, y0, x1, y1 = (round(v) for v in grow(box, FACE_GROW))
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
        if x1 - x0 < 2 or y1 - y0 < 2:  # noqa: PLR2004 - nothing to blur
            continue
        region = out[y0:y1, x0:x1]
        cells = cv2.resize(region, (PIXELATE_CELLS, PIXELATE_CELLS), interpolation=cv2.INTER_AREA)
        block = cv2.resize(cells, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        side = max(3, (min(x1 - x0, y1 - y0) // 3) | 1)
        out[y0:y1, x0:x1] = cv2.GaussianBlur(block, (side, side), 0)
    return out


def blur_faces(frame: Frame, items: Sequence[Item], faces: FaceDetector) -> Frame:
    """Blur the detector's faces and the head region of every tracked person."""
    boxes = [*faces.detect(frame), *(head_box(i) for i in items if i.person)]
    return blur_boxes(frame, boxes)


def standard_frame(raw: Frame, overlay: Overlay, faces: FaceDetector) -> Frame:
    """A standard-mode keyframe: the real frame, faces blurred, overlays drawn, band below."""
    out = blur_faces(raw, overlay.items, faces)
    return draw_overlay(out, overlay, strict=False)


def anonymous_frame(
    raw: Frame, plate: Frame, overlay: Overlay, faces: FaceDetector, *, strict: bool
) -> Frame:
    """An anonymous/strict keyframe: the plate, the rule's objects, people as skeletons.

    Args:
        raw: The real frame (only its non-person object regions are kept).
        plate: The clean plate of the strip (`clean_plate`).
        overlay: What to draw.
        faces: Face detector, run on the composite as a second line of defence (a person SAM
            missed inside an object's box).
        strict: Greyscale and canonical figures only.

    Returns:
        The composite with the annotation band below it; no pixel inside a (grown) person box
        comes from `raw`.
    """
    people = person_mask(raw.shape, overlay.items)
    objects = box_mask(raw.shape, [i.box for i in overlay.items if not i.person])
    keep = objects & ~people
    out = plate.copy()
    out[keep] = raw[keep]
    out = blur_boxes(out, faces.detect(out))
    if strict:
        grey = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
        out = np.asarray(cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    for item in overlay.items:
        if item.person:
            draw_person(out, item, strict=strict)
    return draw_overlay(out, overlay, strict=strict)


def track_colour(key: str) -> tuple[int, int, int]:
    """A stable colour for an opaque key (never shown as text)."""
    digest = hashlib.sha256(key.encode()).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def draw_person(frame: Frame, item: Item, *, strict: bool) -> None:
    """Draw a person as a skeleton (keypoints) or a stick figure fitted to the box, in place."""
    colour = STRICT_COLOUR if strict else track_colour(item.colour_key or item.concept)
    bones = None if strict else _keypoint_bones(item)
    if bones is None:
        bones = stick_figure(item.box)
    thickness = max(2, int((item.box[3] - item.box[1]) / 40))
    for a, b in bones:
        cv2.line(frame, _pt(a), _pt(b), colour, thickness, cv2.LINE_AA)
    head = _head_circle(item, bones)
    if head is not None:
        centre, radius = head
        cv2.circle(frame, _pt(centre), max(2, int(radius)), colour, thickness, cv2.LINE_AA)


def _keypoint_bones(item: Item) -> list[tuple[PointXY, PointXY]] | None:
    points = item.keypoints
    if points is None or len(points) < 17:  # noqa: PLR2004 - COCO-17
        return None
    bones = [
        ((points[a][0], points[a][1]), (points[b][0], points[b][1]))
        for a, b in BONES
        if points[a][2] >= KEYPOINT_MIN_SCORE and points[b][2] >= KEYPOINT_MIN_SCORE
    ]
    return bones or None


def stick_figure(box: BoxXYXY) -> list[tuple[PointXY, PointXY]]:
    """Canonical stick-figure bones fitted to a box (upright, or lying along a wide box)."""
    x0, y0, x1, y1 = box
    upright = (y1 - y0) >= (x1 - x0) / 1.3
    # Canonical body in unit coordinates: u along the body (0 = head, 1 = feet), v across it.
    unit = (
        ((0.5, 0.20), (0.5, 0.55)),  # spine
        ((0.25, 0.25), (0.75, 0.25)),  # shoulders
        ((0.25, 0.25), (0.15, 0.50)),  # left arm
        ((0.75, 0.25), (0.85, 0.50)),  # right arm
        ((0.5, 0.55), (0.30, 1.0)),  # left leg
        ((0.5, 0.55), (0.70, 1.0)),  # right leg
    )

    def place(v: float, u: float) -> PointXY:
        if upright:
            return (x0 + v * (x1 - x0), y0 + u * (y1 - y0))
        return (x0 + u * (x1 - x0), y0 + v * (y1 - y0))

    return [(place(*a), place(*b)) for a, b in unit]


def _head_circle(
    item: Item, bones: Sequence[tuple[PointXY, PointXY]]
) -> tuple[PointXY, float] | None:
    x0, y0, x1, y1 = item.box
    if bones and item.keypoints is not None and len(item.keypoints) >= 17:  # noqa: PLR2004
        hx0, hy0, hx1, hy1 = head_box(item)
        return ((hx0 + hx1) / 2, (hy0 + hy1) / 2), (hx1 - hx0) / 4
    if (y1 - y0) >= (x1 - x0) / 1.3:
        return ((x0 + x1) / 2, y0 + 0.1 * (y1 - y0)), 0.08 * (y1 - y0)
    return (x0 + 0.1 * (x1 - x0), (y0 + y1) / 2), 0.08 * (x1 - x0)


def draw_overlay(frame: Frame, overlay: Overlay, *, strict: bool) -> Frame:
    """Draw the overlay: shapes on the frame, every word in the band below it.

    Zones, boxes and the distance ruler are drawn on `frame` in place, without any text; the
    returned frame is `frame` with the annotation band (caption and legend) appended below.
    """
    for _, polygon in overlay.zones:
        pts = np.asarray([_pt(p) for p in polygon], dtype=np.int32)
        cv2.polylines(frame, [pts], isClosed=True, color=ZONE_COLOUR, thickness=1)
    for item in overlay.items:
        colour = SUBJECT_COLOUR if item.subject else _concept_colour(item.concept, strict=strict)
        x0, y0, x1, y1 = item.box
        cv2.rectangle(frame, _pt((x0, y0)), _pt((x1, y1)), colour, 3 if item.subject else 1)
    if overlay.ruler is not None:
        _draw_ruler(frame, overlay.ruler)
    return with_band(frame, overlay, strict=strict)


def legend(overlay: Overlay, *, strict: bool) -> list[tuple[str, tuple[int, int, int]]]:
    """The band's legend: `(text, swatch colour)` per subject, other concept, ruler, tick, zone."""
    entries: list[tuple[str, tuple[int, int, int]]] = []
    subjects = sorted({i.concept for i in overlay.items if i.subject})
    entries += [(f"subject: {concept}", SUBJECT_COLOUR) for concept in subjects]
    others = sorted({i.concept for i in overlay.items if not i.subject})
    entries += [(concept, _concept_colour(concept, strict=strict)) for concept in others]
    if overlay.ruler is not None:
        entries.append((f"distance {overlay.ruler.metres:.1f} m", RULER_COLOUR))
        if overlay.ruler.tick is not None:
            entries.append((TICK_LABEL, (255, 255, 255)))
    entries += [(f"zone {name}", ZONE_COLOUR) for name, _ in overlay.zones]
    return entries


def caption_parts(caption: str) -> tuple[str, ...]:
    """The caption's fields (frame, offset, rule, measurement), as drawn and as copied apart."""
    return tuple(part.strip() for part in caption.split(CAPTION_SEPARATOR) if part.strip())


def annotation_texts(overlay: Overlay) -> tuple[str, ...]:
    """Every string the annotation band of this overlay shows (the same in every privacy mode)."""
    texts = [BAND_HEADER]
    if overlay.caption:
        texts += [overlay.caption, *caption_parts(overlay.caption)]
    texts += [text for text, _ in legend(overlay, strict=False)]
    if overlay.ruler is not None:
        texts.append(f"{overlay.ruler.metres:.1f} m")  # the value alone, as a model may copy it
    return tuple(dict.fromkeys(texts))


def _text_width(text: str) -> int:
    (width, _), _ = cv2.getTextSize(text, FONT, FONT_SCALE, 1)
    return int(width)


def _legend_rows(
    entries: list[tuple[str, tuple[int, int, int]]], width: int
) -> list[list[tuple[str, tuple[int, int, int]]]]:
    """Legend entries wrapped into rows that fit `width` (an over-long entry gets its own row)."""
    rows: list[list[tuple[str, tuple[int, int, int]]]] = []
    used = width
    for entry in entries:
        need = SWATCH + 4 + _text_width(entry[0]) + LEGEND_GAP
        if used + need > width and (not rows or rows[-1]):
            rows.append([])
            used = BAND_PAD
        rows[-1].append(entry)
        used += need
    return rows


def band_height(overlay: Overlay, width: int, *, strict: bool) -> int:
    """Height in pixels of the annotation band for a frame `width` pixels wide."""
    lines = 1 + bool(overlay.caption) + len(_legend_rows(legend(overlay, strict=strict), width))
    return BAND_EDGE + lines * BAND_LINE_H + BAND_PAD


def with_band(frame: Frame, overlay: Overlay, *, strict: bool) -> Frame:
    """The frame with Rulecam's annotation band appended below it (the scene is not covered).

    The band is black under a `BAND_EDGE` px line in the ruler colour. It shows `BAND_HEADER`,
    the caption, and the legend: a colour swatch and a label per subject, concept, ruler, tick and
    zone. Its strings are exactly `annotation_texts(overlay)`.
    """
    height, width = frame.shape[:2]
    out = np.zeros((height + band_height(overlay, width, strict=strict), width, 3), np.uint8)
    out[:height] = frame
    out[height:] = BAND_COLOUR
    out[height : height + BAND_EDGE] = BAND_EDGE_COLOUR
    baseline = height + BAND_EDGE + BAND_LINE_H - 5
    _text(out, BAND_HEADER, (BAND_PAD, baseline), BAND_EDGE_COLOUR)
    if overlay.caption:
        baseline += BAND_LINE_H
        _text(out, overlay.caption, (BAND_PAD, baseline), (255, 255, 255))
    for row in _legend_rows(legend(overlay, strict=strict), width):
        baseline += BAND_LINE_H
        x = BAND_PAD
        for text, colour in row:
            top = baseline - SWATCH
            cv2.rectangle(out, (x, top), (x + SWATCH, top + SWATCH), colour, thickness=-1)
            x += SWATCH + 4
            _text(out, text, (x, baseline), (255, 255, 255))
            x += _text_width(text) + LEGEND_GAP
    return out


def _draw_ruler(frame: Frame, ruler: Ruler) -> None:
    a, b = _pt(ruler.a), _pt(ruler.b)
    cv2.line(frame, a, b, RULER_COLOUR, 2, cv2.LINE_AA)
    for end in (a, b):
        cv2.circle(frame, end, 4, RULER_COLOUR, -1)
    if ruler.tick is not None:
        t0, t1 = _pt(ruler.tick[0]), _pt(ruler.tick[1])
        cv2.line(frame, t0, t1, (255, 255, 255), 3, cv2.LINE_AA)


def _concept_colour(concept: str, *, strict: bool) -> tuple[int, int, int]:
    return STRICT_COLOUR if strict else track_colour(f"concept:{concept}")


def _text(frame: Frame, text: str, origin: tuple[int, int], colour: tuple[int, int, int]) -> None:
    cv2.putText(frame, text, origin, FONT, FONT_SCALE, colour, 1, cv2.LINE_AA)


def _pt(point: PointXY) -> tuple[int, int]:
    return (round(point[0]), round(point[1]))
