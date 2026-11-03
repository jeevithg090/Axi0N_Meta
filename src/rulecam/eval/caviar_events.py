"""CAVIAR activity labels as eval-set events and `clip.yaml` entries (add-on 1.3).

CAVIAR's labellers marked what each person (and group) is doing in every frame. Those labels
become the positive events of plain-language rules, so Rulecam's alerts can be scored against
someone else's ground truth:

- `fall`: a label containing "fall", "fell" or "down" (Rest_FallOnFloor, Fight_OneManDown);
- `fight`: a label containing "fight" (Fight_RunAway1/2, Fight_OneManDown, Fight_Chase);
- `left_bag`: a label naming a left or abandoned object (the five LeftBag / LeftBox clips);
- `shop_enter`: situation "shop enter", the step through the door (the corridor clips).

An event is the span of frames during which some person or group carries a matching label,
with gaps under `merge_gap_s` closed and overlapping spans of different people merged, because
the eval set's events on one rule must not overlap. A spec that matches nothing in a clip that
should have the event is an error, with the clip's label inventory, so the label vocabulary is
checked against the data instead of being assumed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from rulecam.eval.caviar import CAVIAR_FPS, LABEL_FIELDS, CaviarSequence, label_inventory
from rulecam.eval.errors import EvalError
from rulecam.eval.schema import ClipEntry

MERGE_GAP_S = 1.0
MIN_EVENT_S = 1.0  # the scorer widens alert episodes to 1 s; labels get the same floor
CAVIAR_LICENSE = (
    "CAVIAR project (EC IST 2001 37540), University of Edinburgh: publicly available for "
    "research; cite the CAVIAR project"
)


@dataclass(frozen=True)
class EventType:
    """One kind of labelled event, the rule it is scored on, and the owner's request for it."""

    rule_id: str
    request: str
    needles: tuple[str, ...]  # lower-case substrings of a label value
    fields: tuple[str, ...] = LABEL_FIELDS

    def matches(self, labels: Mapping[str, str]) -> bool:
        """Whether any of the watched label fields contains a needle."""
        for name in self.fields:
            value = labels.get(name, "").lower()
            if value and any(needle in value for needle in self.needles):
                return True
        return False


EVENT_TYPES: dict[str, EventType] = {
    "fall": EventType(
        rule_id="caviar_fall",
        request="Tell me if anyone falls down or ends up lying on the floor.",
        needles=("fall", "fell", "down"),
    ),
    "fight": EventType(
        rule_id="caviar_fight",
        request="Alert me if people start fighting or someone is being attacked.",
        needles=("fight",),
    ),
    "left_bag": EventType(
        rule_id="caviar_left_bag",
        request="Warn me when a bag or box is left behind with nobody looking after it.",
        needles=("left object", "leaving object", "leave object", "abandon", "left bag"),
    ),
    "shop_enter": EventType(
        rule_id="caviar_shop_enter",
        request="Let me know each time someone walks into one of the shops.",
        needles=("shop enter",),
        fields=("situation",),
    ),
}


def _spans(frames: Iterable[int], gap_frames: int) -> list[tuple[int, int]]:
    """Group frame numbers into `[first, last]` spans, bridging gaps up to `gap_frames`."""
    spans: list[tuple[int, int]] = []
    for frame in sorted(set(frames)):
        if spans and frame - spans[-1][1] <= gap_frames:
            spans[-1] = (spans[-1][0], frame)
        else:
            spans.append((frame, frame))
    return spans


def event_spans(
    sequence: CaviarSequence,
    event: EventType,
    *,
    fps: float = CAVIAR_FPS,
    merge_gap_s: float = MERGE_GAP_S,
    min_s: float = MIN_EVENT_S,
) -> list[tuple[float, float]]:
    """The clip seconds during which someone carries one of the event's labels.

    Args:
        sequence: The clip's labels.
        event: Which labels mark the event.
        fps: The clip's frame rate.
        merge_gap_s: Gaps up to this long, per person and between people, are closed.
        min_s: Shorter events are widened to this length (a step through a door lasts 0.5 s).

    Returns:
        Non-overlapping `(start_s, end_s)` spans, in order.
    """
    gap = round(merge_gap_s * fps)
    per_box: dict[str, list[int]] = {}
    for kind, boxes in (("object", sequence.objects), ("group", sequence.groups)):
        for box in boxes:
            if event.matches(box.labels):
                per_box.setdefault(f"{kind}:{box.object_id}", []).append(box.frame)
    spans = [s for frames in per_box.values() for s in _spans(frames, gap)]
    merged: list[tuple[int, int]] = []
    for first, last in sorted(spans):
        if merged and first - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    limit = sequence.frames / fps
    out: list[tuple[float, float]] = []
    for first, last in merged:
        start, end = first / fps, (last + 1) / fps
        if end - start < min_s:
            end = min(limit, start + min_s)
        if out and start < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return [(round(start, 2), round(end, 2)) for start, end in out]


@dataclass(frozen=True)
class RulePlan:
    """One rule on one clip: which event type, and whether the clip is a negative control."""

    event: str
    negative: bool = False


def placeholder_entry(
    *, name: str, camera_id: str, rules: Sequence[RulePlan], description: str, url: str
) -> dict[str, object]:
    """A placeholder `clip.yaml` for a clip whose labels and video are not downloaded yet.

    It lists the rules and requests (unlabelled) so `eval validate` shows what is planned;
    the runner skips placeholders until the real entry replaces it.
    """
    data: dict[str, object] = {
        "name": name,
        "description": f"{description} Video: {url}",
        "camera_id": camera_id,
        "license": CAVIAR_LICENSE,
        "placeholder": True,
        "rules": [
            {
                "id": EVENT_TYPES[plan.event].rule_id,
                "request": EVENT_TYPES[plan.event].request,
                "status": "unlabelled",
            }
            for plan in rules
        ],
    }
    ClipEntry.model_validate(data)
    return data


def clip_entry(
    sequence: CaviarSequence,
    *,
    name: str,
    camera_id: str,
    url: str,
    sha256: str,
    rules: Sequence[RulePlan],
    description: str = "",
    tracks_file: str | None = None,
    frame_size: tuple[int, int] = (384, 288),
) -> dict[str, object]:
    """Build one validated `clip.yaml` mapping for a CAVIAR clip.

    Args:
        sequence: The clip's labels.
        name: The eval-set clip name (the folder name).
        camera_id: The logical camera (one per CAVIAR view).
        url: Where the video is downloaded from.
        sha256: The video's sha256.
        rules: The rules to score on the clip.
        description: One line about the clip.
        tracks_file: A MOT `gt.txt` next to `clip.yaml` for tracking metrics, if written.
        frame_size: The size CAVIAR's boxes were drawn on.

    Returns:
        The mapping (`ClipEntry`-valid), ready to dump as YAML.

    Raises:
        EvalError: If a positive rule's labels match nothing, or a negative control has events.
    """
    entries: list[dict[str, object]] = []
    for plan in rules:
        event = EVENT_TYPES[plan.event]
        spans = event_spans(sequence, event)
        if plan.negative and spans:
            msg = f"{sequence.name}: negative control for {plan.event} has {len(spans)} events"
            raise EvalError(msg)
        if not plan.negative and not spans:
            inventory = label_inventory([sequence])
            msg = (
                f"{sequence.name}: no label matches {plan.event} {event.needles}; "
                f"labels: {inventory}"
            )
            raise EvalError(msg)
        rule: dict[str, object] = {
            "id": event.rule_id,
            "request": event.request,
            "status": "negative" if plan.negative else "labelled",
        }
        if spans:
            rule["events"] = [
                {"start_s": s, "end_s": e, "kind": "positive", "note": f"CAVIAR {plan.event}"}
                for s, e in spans
            ]
        entries.append(rule)
    data: dict[str, object] = {
        "name": name,
        "description": description or f"CAVIAR {sequence.name}",
        "camera_id": camera_id,
        "source": {"url": url, "sha256": sha256},
        "duration_s": round(sequence.duration_s, 2),
        "license": CAVIAR_LICENSE,
        "rules": entries,
    }
    if tracks_file is not None:
        width, height = frame_size
        data["tracks"] = {
            "file": tracks_file,
            "fps": CAVIAR_FPS,
            "width": width,
            "height": height,
            "classes": {1: "person"},
        }
    try:
        ClipEntry.model_validate(data)
    except ValueError as exc:
        msg = f"{name}: the generated clip.yaml is invalid: {exc}"
        raise EvalError(msg) from exc
    return data
