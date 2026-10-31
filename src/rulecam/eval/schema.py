"""The eval-set format: one `eval/clips/<name>/clip.yaml` per clip (WS0, PLAN-9.5 §3).

```yaml
name: warehouse_45848            # must equal the folder name
camera_id: warehouse             # logical camera; sent as the paired phone's label
source:                          # a local file (relative to the clip folder) or a URL + sha256
  url: https://assets.mixkit.co/videos/45848/45848-720.mp4
  sha256: e5e3c892...
duration_s: 21.76
rules:
  - id: forklift_2m              # the scoring key: the same id across clips is the same rule
    request: Keep people at least 2 metres away from a moving forklift.
    status: negative             # labelled | negative | unlabelled
    events: []                   # labelled: [{start_s, end_s, kind: positive}]
tracks:                          # optional MOT ground truth for tracking metrics
  file: gt.txt
  fps: 25
  classes: {1: person, 2: forklift}
```

`labelled` rules list every positive event (an interval of clip seconds where the rule is
violated); everything outside the events is negative. `negative` rules have no event at all, so
any alert on them is a false alert. `unlabelled` rules are not scored (they still count for
Glimmer decisiveness). A `placeholder` clip is one the owner has not recorded yet: it documents
what to film and is skipped by the runner.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, ValidationError, model_validator

from rulecam.contracts import Frozen
from rulecam.eval.errors import EvalSetError

CLIP_FILE = "clip.yaml"
SLUG = r"^[a-z0-9][a-z0-9_-]{0,63}$"
SHA256 = r"^[0-9a-f]{64}$"
END_SLACK_S = 0.5  # labels may end this far past the clip's stated duration (rounding)

RuleStatus = Literal["labelled", "negative", "unlabelled"]
Expectation = Literal["activatable", "refusal"]


class ClipSource(Frozen):
    """Where the video comes from: a local path or a URL pinned by its sha256."""

    path: str | None = None
    url: str | None = Field(default=None, pattern=r"^https://")
    sha256: str | None = Field(default=None, pattern=SHA256)

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.path is None) == (self.url is None):
            msg = "source needs exactly one of 'path' or 'url'"
            raise ValueError(msg)
        if self.url is not None and self.sha256 is None:
            msg = "a URL source must pin its sha256"
            raise ValueError(msg)
        return self


class LabelledEvent(Frozen):
    """One positive event: the clip seconds during which the rule is violated."""

    start_s: float = Field(ge=0)
    end_s: float
    kind: Literal["positive"] = "positive"
    note: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end_s <= self.start_s:
            msg = f"event end_s {self.end_s} must be after start_s {self.start_s}"
            raise ValueError(msg)
        return self


class RuleLabels(Frozen):
    """One owner request on one clip, and its ground truth."""

    id: str = Field(pattern=SLUG)
    request: str = Field(min_length=1, max_length=2000)
    lang: str = "en"
    status: RuleStatus
    expect: Expectation = "activatable"
    events: tuple[LabelledEvent, ...] = ()

    @model_validator(mode="after")
    def _events_match_status(self) -> Self:
        if self.status != "labelled" and self.events:
            msg = f"rule {self.id!r} is {self.status}: it must not list events"
            raise ValueError(msg)
        if self.status == "labelled" and not self.events:
            msg = f"rule {self.id!r} is labelled but has no events (use status: negative)"
            raise ValueError(msg)
        ordered = sorted(self.events, key=lambda e: e.start_s)
        for before, after in itertools.pairwise(ordered):
            if after.start_s < before.end_s:
                msg = f"rule {self.id!r} has overlapping events at {after.start_s} s"
                raise ValueError(msg)
        return self


class TrackLabels(Frozen):
    """Box tracks in MOTChallenge text format (`frame,id,x,y,w,h,conf,class,visibility`)."""

    file: str
    fps: float = Field(gt=0)
    classes: dict[int, str] = Field(min_length=1)
    width: int | None = Field(default=None, gt=0)  # frame size the boxes were drawn on
    height: int | None = Field(default=None, gt=0)


class ClipEntry(Frozen):
    """One clip of the eval set."""

    name: str = Field(pattern=SLUG)
    description: str = ""
    camera_id: str = Field(min_length=1, max_length=40, pattern=r"^[^\x00-\x1f\x7f]*$")
    source: ClipSource | None = None
    duration_s: float | None = Field(default=None, gt=0)
    license: str = ""
    placeholder: bool = False
    rules: tuple[RuleLabels, ...] = Field(min_length=1)
    tracks: TrackLabels | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.placeholder and (self.source is None or self.duration_s is None):
            msg = f"clip {self.name!r} needs a source and duration_s (or placeholder: true)"
            raise ValueError(msg)
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            msg = f"clip {self.name!r} lists a rule id twice"
            raise ValueError(msg)
        if self.duration_s is not None:
            limit = self.duration_s + END_SLACK_S
            for rule in self.rules:
                late = [e for e in rule.events if e.end_s > limit]
                if late:
                    msg = f"rule {rule.id!r} has an event ending after the clip ({late[0].end_s} s)"
                    raise ValueError(msg)
        return self

    def rule(self, rule_id: str) -> RuleLabels:
        """Return the labels of one rule.

        Raises:
            EvalSetError: If the clip has no such rule.
        """
        for rule in self.rules:
            if rule.id == rule_id:
                return rule
        msg = f"clip {self.name!r} has no rule {rule_id!r}"
        raise EvalSetError(msg)


class EvalSet(Frozen):
    """Every clip under one eval-set folder, with the folder it was read from."""

    root: Path
    clips: tuple[ClipEntry, ...]

    def clip(self, name: str) -> ClipEntry:
        """Return one clip by name.

        Raises:
            EvalSetError: If there is no such clip.
        """
        for clip in self.clips:
            if clip.name == name:
                return clip
        msg = f"no clip {name!r} in {self.root}"
        raise EvalSetError(msg)

    def clip_dir(self, name: str) -> Path:
        """Return the folder holding a clip's `clip.yaml` (and its local files)."""
        return self.root / name

    def runnable(self) -> tuple[ClipEntry, ...]:
        """Return the clips that are not placeholders."""
        return tuple(clip for clip in self.clips if not clip.placeholder)


def load_clip(path: Path) -> ClipEntry:
    """Read and validate one `clip.yaml`.

    Raises:
        EvalSetError: If the file is unreadable, not YAML, invalid, or named after another folder.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        entry = ClipEntry.model_validate(data)
    except (OSError, yaml.YAMLError) as exc:
        msg = f"{path}: {exc}"
        raise EvalSetError(msg) from exc
    except ValidationError as exc:
        msg = f"{path}: {exc}"
        raise EvalSetError(msg) from exc
    if entry.name != path.parent.name:
        msg = f"{path}: name {entry.name!r} must equal its folder {path.parent.name!r}"
        raise EvalSetError(msg)
    if entry.tracks is not None and not (path.parent / entry.tracks.file).is_file():
        msg = f"{path}: tracks file {entry.tracks.file!r} is missing"
        raise EvalSetError(msg)
    return entry


def load_eval_set(root: Path) -> EvalSet:
    """Read every `<root>/<name>/clip.yaml`, sorted by name.

    Raises:
        EvalSetError: If the folder has no clips or any entry is invalid.
    """
    paths = sorted(root.glob(f"*/{CLIP_FILE}"))
    if not paths:
        msg = f"no {CLIP_FILE} under {root}"
        raise EvalSetError(msg)
    return EvalSet(root=root, clips=tuple(load_clip(path) for path in paths))
