"""Rulecam's own strip annotations are not scene text (live finding, P4-05 / P3-03).

The strip renderer (`runtime.strip_draw`) puts every word it draws (caption, legend, distance)
in an annotation band below each frame, and writes the exact strings beside the strip as
`ANNOTATION_FILE`. Glimmer is told the band is Rulecam's, but a model may still copy it into
`scene_text`, which feeds the Prompt Guard firewall (P3-05): our own "1/6 t+0.0s rule-id measured
0.01 m" could then read as a tamper attempt. `strip_annotations` therefore removes, before the
firewall, any copy of a string we drew. It is deterministic (we know what we drew) and never
hides anything else: text that is not one of ours, or merely contains one of our single-word
labels, is kept for the firewall to scan.

Matching ignores case and runs of whitespace (a model collapses the caption's double spaces).
A string of two or more words is removed wherever it appears; a one-word label (a concept such
as `forklift`) only when it is a whole line, so a real sign that says "FORKLIFTS ONLY" or
"forklift" in a sentence is never touched. Lines left without a letter or digit are dropped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

from rulecam.log import get_logger

_log = get_logger(__name__)

ANNOTATION_FILE: Final = "annotations.json"
"""The strip folder's list of strings the renderer drew: `{"drawn": [...]}`."""
_SEPARATORS: Final = " \t,;|/:-*\"'`\u2013\u2014\u00b7\u2022"
_ALNUM: Final = re.compile(r"[0-9A-Za-z]")


def write_annotations(folder: Path, drawn: Iterable[str]) -> Path:
    """Write the strings drawn on a strip next to its frames (sorted, without duplicates)."""
    path = folder / ANNOTATION_FILE
    texts = sorted({t for t in drawn if t.strip()})
    path.write_text(json.dumps({"drawn": texts}, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def read_annotations(strip_paths: Sequence[str | Path]) -> tuple[str, ...]:
    """The strings drawn on a strip, from `ANNOTATION_FILE` in its first frame's folder.

    Returns:
        The drawn strings; empty when there is no strip, no file, or the file is unreadable
        (then nothing is removed: the firewall sees everything, the safe side).
    """
    if not strip_paths:
        return ()
    path = Path(strip_paths[0]).parent / ANNOTATION_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ()
    except (OSError, ValueError) as exc:
        _log.warning("strip_annotations_unreadable", path=str(path), error=type(exc).__name__)
        return ()
    drawn = data.get("drawn") if isinstance(data, dict) else None
    if not isinstance(drawn, list):
        return ()
    return tuple(t for t in drawn if isinstance(t, str) and t.strip())


def _pattern(text: str) -> re.Pattern[str]:
    words = text.split()
    return re.compile(r"\s+".join(re.escape(w) for w in words), re.IGNORECASE)


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def strip_annotations(scene_text: str, drawn: Sequence[str]) -> str:
    """Remove every copy of Rulecam's own annotation strings from a verifier's `scene_text`.

    Args:
        scene_text: The text the verifier says it saw.
        drawn: The exact strings the renderer drew (`read_annotations`).

    Returns:
        `scene_text` without them, one line per remaining text; unchanged when `drawn` is empty.
    """
    if not drawn or not scene_text:
        return scene_text
    phrases = sorted({t for t in drawn if len(t.split()) > 1}, key=len, reverse=True)
    words = {_norm(t) for t in drawn if len(t.split()) == 1}
    kept = []
    for line in scene_text.splitlines():
        rest = line
        for phrase in phrases:
            rest = _pattern(phrase).sub(" ", rest)
        if rest != line:  # something of ours was cut out: tidy what is left around it
            rest = " ".join(rest.split()).strip(_SEPARATORS)
        if _ALNUM.search(rest) and _norm(rest.strip(_SEPARATORS)) not in words:
            kept.append(rest)
    return "\n".join(kept)
