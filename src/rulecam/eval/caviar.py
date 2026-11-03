"""CAVIAR ground truth (CVML XML) as MOTChallenge box tracks (add-on 1.5).

CAVIAR (EC project IST 2001 37540, University of Edinburgh) labels every person in every frame
of its INRIA lobby and Lisbon shopping-centre clips: a box (`xc, yc, w, h`, the box centre and
size in pixels), an object id, and four activity labels (`movement`, `role`, `context`,
`situation`). Groups of people get their own boxes and labels with a `members` list. Frames are
numbered from 0 at 25 fps.

This module reads that XML and writes MOTChallenge rows (`frame,id,x,y,w,h,conf,class,vis`,
frames from 1), the format `rulecam.eval.tracking` scores. Several clips can be chained into one
stream (as the 2 Nov corridor test fed seven corridor clips back to back): frames are offset by
the length of the clips before them and ids are renumbered, so each clip's people stay apart.

The XML is a public dataset's labels, still read with `defusedxml` because it is downloaded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml import ElementTree

from rulecam.eval.errors import EvalError

CAVIAR_FPS = 25.0
LABEL_FIELDS = ("movement", "role", "context", "situation")
PERSON_CLASS = 1


@dataclass(frozen=True)
class CaviarBox:
    """One labelled object (a person) or group in one frame."""

    frame: int
    object_id: str
    xc: float
    yc: float
    w: float
    h: float
    labels: Mapping[str, str] = field(default_factory=dict)
    members: tuple[str, ...] = ()

    @property
    def xywh(self) -> tuple[float, float, float, float]:
        """Top-left corner and size, as MOTChallenge stores a box."""
        return (self.xc - self.w / 2.0, self.yc - self.h / 2.0, self.w, self.h)


@dataclass(frozen=True)
class CaviarSequence:
    """One CAVIAR clip's labels."""

    name: str
    frames: int
    objects: tuple[CaviarBox, ...]
    groups: tuple[CaviarBox, ...] = ()

    @property
    def duration_s(self) -> float:
        """The clip's length at CAVIAR's 25 fps."""
        return self.frames / CAVIAR_FPS

    @property
    def object_ids(self) -> tuple[str, ...]:
        """Every person id, in order of first appearance."""
        return tuple(dict.fromkeys(box.object_id for box in self.objects))


@dataclass(frozen=True)
class Segment:
    """Where one clip sits in a chained stream."""

    name: str
    first_frame: int  # 1-based MOT frame of the clip's frame 0
    frames: int
    fps: float = CAVIAR_FPS

    @property
    def start_s(self) -> float:
        """Stream second of the clip's first frame."""
        return (self.first_frame - 1) / self.fps

    @property
    def end_s(self) -> float:
        """Stream second just after the clip's last frame."""
        return self.start_s + self.frames / self.fps


def _number(element: Element, name: str) -> float:
    raw = element.get(name)
    if raw is None:
        msg = f"<{element.tag}> has no {name!r} attribute"
        raise EvalError(msg)
    try:
        return float(raw)
    except ValueError as exc:
        msg = f"<{element.tag}> {name}={raw!r} is not a number"
        raise EvalError(msg) from exc


def _labels(element: Element) -> dict[str, str]:
    """The first hypothesis's activity labels (CAVIAR ground truth has one, evaluation 1.0)."""
    hypothesis = element.find("hypothesislist/hypothesis")
    if hypothesis is None:
        return {}
    out = {}
    for name in LABEL_FIELDS:
        node = hypothesis.find(name)
        if node is not None and node.text is not None and node.text.strip():
            out[name] = node.text.strip()
    return out


def _box(element: Element, frame: int, *, group: bool) -> CaviarBox:
    box = element.find("box")
    if box is None:
        msg = f"frame {frame}: <{element.tag} id={element.get('id')!r}> has no <box>"
        raise EvalError(msg)
    members: tuple[str, ...] = ()
    if group:
        text = element.findtext("members") or ""
        members = tuple(m.strip() for m in text.split(",") if m.strip())
    return CaviarBox(
        frame=frame,
        object_id=element.get("id", ""),
        xc=_number(box, "xc"),
        yc=_number(box, "yc"),
        w=_number(box, "w"),
        h=_number(box, "h"),
        labels=_labels(element),
        members=members,
    )


def parse_caviar(text: str | bytes, *, name: str | None = None) -> CaviarSequence:
    """Parse one CAVIAR CVML ground-truth file.

    Args:
        text: The XML.
        name: The clip's name (default: the `<dataset name>` attribute).

    Returns:
        Every object and group box, in frame order.

    Raises:
        EvalError: If the XML is malformed or a frame or box lacks its numbers.
    """
    try:
        root: Element = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        msg = f"CAVIAR XML is malformed: {exc}"
        raise EvalError(msg) from exc
    objects: list[CaviarBox] = []
    groups: list[CaviarBox] = []
    last = -1
    for frame_node in root.iter("frame"):
        frame = int(_number(frame_node, "number"))
        last = max(last, frame)
        objects.extend(
            _box(node, frame, group=False) for node in frame_node.iterfind("objectlist/object")
        )
        groups.extend(
            _box(node, frame, group=True) for node in frame_node.iterfind("grouplist/group")
        )
    if last < 0:
        msg = "CAVIAR XML has no <frame>"
        raise EvalError(msg)
    return CaviarSequence(
        name=name or root.get("name") or "",
        frames=last + 1,
        objects=tuple(objects),
        groups=tuple(groups),
    )


def load_caviar(path: Path) -> CaviarSequence:
    """Read a CAVIAR ground-truth file; the clip is named after the file (`X.xml` -> `X`).

    Raises:
        EvalError: If the file is unreadable or malformed.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        msg = f"cannot read {path}: {exc}"
        raise EvalError(msg) from exc
    return parse_caviar(data, name=path.stem)


def mot_rows(
    sequence: CaviarSequence, *, frame_offset: int = 0, id_offset: int = 0
) -> tuple[list[str], int]:
    """MOTChallenge ground-truth rows for one clip's people (groups are not tracked objects).

    Args:
        sequence: The clip.
        frame_offset: Frames of the stream before this clip.
        id_offset: Ids already used by earlier clips.

    Returns:
        The rows (`frame,id,x,y,w,h,1,1,1`, frame-sorted) and how many ids this clip used.
    """
    ids = {name: id_offset + n for n, name in enumerate(sequence.object_ids, start=1)}
    rows = []
    for box in sorted(sequence.objects, key=lambda b: (b.frame, ids[b.object_id])):
        x, y, w, h = box.xywh
        frame = box.frame + 1 + frame_offset
        rows.append(
            f"{frame},{ids[box.object_id]},{x:.2f},{y:.2f},{w:.2f},{h:.2f},1,{PERSON_CLASS},1"
        )
    return rows, len(ids)


def chain_mot(sequences: Sequence[CaviarSequence]) -> tuple[list[str], list[Segment]]:
    """Chain clips into one stream's MOT ground truth, in the order given.

    Returns:
        The rows, and where each clip sits in the stream.
    """
    rows: list[str] = []
    segments: list[Segment] = []
    frame_offset = id_offset = 0
    for sequence in sequences:
        clip_rows, used = mot_rows(sequence, frame_offset=frame_offset, id_offset=id_offset)
        rows.extend(clip_rows)
        segments.append(Segment(sequence.name, frame_offset + 1, sequence.frames))
        frame_offset += sequence.frames
        id_offset += used
    return rows, segments


def label_inventory(sequences: Iterable[CaviarSequence]) -> dict[str, dict[str, int]]:
    """Count every activity label value per field (object and group boxes), to pick events from.

    Returns:
        `field -> value -> boxes carrying it`, values sorted by count.
    """
    counts: dict[str, dict[str, int]] = {name: {} for name in LABEL_FIELDS}
    for sequence in sequences:
        for box in (*sequence.objects, *sequence.groups):
            for name, value in box.labels.items():
                counts[name][value] = counts[name].get(value, 0) + 1
    return {
        name: dict(sorted(values.items(), key=lambda kv: (-kv[1], kv[0])))
        for name, values in counts.items()
    }
