"""Runner E8: person-down on a public dataset we did not make (task P4-07, MP §8 E8).

Every other evaluation uses clips we staged, which means every other evaluation is partly a test
of our own staging. E8 is the one that is not: the **UR Fall Detection** dataset was recorded by
someone else, for their own purposes, before this system existed. That makes it the only
generalisation evidence in the set, and the only place a precision figure here is not circular.

What this runner does and does not do:

* It does **not** download anything. The dataset has its own terms, and accepting them is a
  person's decision, not a script's. `dataset_manifest` writes the manifest to fill in, and
  `aggregate_e8` refuses to report figures unless the manifest records that the terms were
  accepted and names the sequences used.
* It reports person-down precision and recall with **Wilson 95% intervals**, over sequences,
  not frames. A fall is one event; scoring frames would multiply one event into hundreds of
  correlated trials and make the interval look far tighter than the evidence is.

The dataset is older, lower-resolution, and filmed in rooms rather than a warehouse. A good
score on it is evidence the detector is not purely tuned to our own clips. It is not evidence
about a warehouse, and a bad score may be the camera angle rather than the rule.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import yaml
from eval.runners.common import Row, RunnerResult, not_measured
from pydantic import BaseModel, ConfigDict, Field

from rulecam.stats.intervals import wilson_interval

TITLE: Final = "Person-down on a public dataset (UR Fall Detection)"
DATASET_URL: Final = "http://fenix.ur.edu.pl/~mkepski/ds/uf.html"
CONFIDENCE: Final = 0.95
PERCENT: Final = 100.0


class SequenceResult(BaseModel):
    """What the rule made of one dataset sequence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sequence: str = Field(min_length=1)
    fall: bool  # the dataset's own label
    alerted: bool  # whether the person-down rule fired
    note: str = ""


class DatasetManifest(BaseModel):
    """What was used from the dataset, and that its terms were accepted.

    Attributes:
        name: Dataset name.
        url: Where it came from.
        licence: Its terms, as stated by its authors.
        terms_accepted: Whether a person read and accepted those terms. Figures are refused
            without this.
        accepted_by: Who accepted them, for the record.
        sequences: The sequence ids used, so the result can be reproduced.
        notes: Anything a reader of the figures should know.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = "UR Fall Detection"
    url: str = DATASET_URL
    licence: str = ""
    terms_accepted: bool = False
    accepted_by: str = ""
    sequences: tuple[str, ...] = ()
    notes: str = ""


def dataset_manifest_yaml() -> str:
    """Return the manifest template to commit at `data/urfall/manifest.yaml`."""
    return yaml.safe_dump(
        {
            "name": "UR Fall Detection",
            "url": DATASET_URL,
            "licence": "FILL IN: the terms as the dataset's authors state them",
            "terms_accepted": False,
            "accepted_by": "FILL IN: who read and accepted the terms",
            "sequences": [],
            "notes": (
                "Download by hand under the dataset's own terms. E8 refuses to report any "
                "figure until terms_accepted is true and sequences are listed."
            ),
        },
        sort_keys=False,
    )


def aggregate_e8(manifest: DatasetManifest, results: Sequence[SequenceResult]) -> RunnerResult:
    """Score person-down over dataset sequences, with Wilson intervals.

    Args:
        manifest: The dataset manifest, which must record accepted terms.
        results: One result per sequence.

    Returns:
        The result. Refused as "not measured" if the terms are not accepted or there are no
        sequences.
    """
    if not manifest.terms_accepted:
        return not_measured(
            "e8",
            TITLE,
            f"the dataset's terms are not recorded as accepted. Read them at {manifest.url}, "
            "download it by hand, then set `terms_accepted` and `accepted_by` in the manifest.",
        )
    if not results:
        return not_measured("e8", TITLE, "the terms are accepted but no sequence was scored yet.")

    tp = sum(r.fall and r.alerted for r in results)
    fn = sum(r.fall and not r.alerted for r in results)
    fp = sum(not r.fall and r.alerted for r in results)
    tn = sum(not r.fall and not r.alerted for r in results)

    rows = [
        Row(
            name="Recall (falls detected)",
            value=_rate(tp, tp + fn),
            detail=f"{tp + fn} fall sequences",
        ),
        Row(
            name="Precision (alerts that were falls)",
            value=_rate(tp, tp + fp),
            detail=f"{tp + fp} alerts raised",
        ),
        Row(
            name="False alerts on non-fall sequences",
            value=_rate(fp, fp + tn),
            detail=f"{fp + tn} non-fall sequences (activities of daily living)",
        ),
    ]

    caveats: list[str] = [
        f"Dataset: {manifest.name}, {manifest.url}. Licence as recorded: "
        f"{manifest.licence or 'NOT RECORDED'}. Terms accepted by: "
        f"{manifest.accepted_by or 'NOT RECORDED'}.",
        "Scored per sequence, not per frame. A fall is one event, so frame-level scoring would "
        "turn one event into hundreds of correlated trials and make the interval look tighter "
        "than the evidence is.",
        "This dataset is the only generalisation evidence in the set: we did not record it and "
        "nobody staged it for this system. It is also older, lower-resolution and filmed in "
        "rooms, so a poor score may be the camera angle rather than the rule.",
    ]
    if not manifest.licence:
        caveats.append(
            "**The licence is not recorded.** Fill it in before any figure here is quoted."
        )
    listed = set(manifest.sequences)
    scored = {r.sequence for r in results}
    if listed and listed != scored:
        missing = sorted(listed - scored)
        extra = sorted(scored - listed)
        caveats.append(
            "The manifest and the scored sequences differ"
            + (f"; not scored: {', '.join(missing)}" if missing else "")
            + (f"; scored but not in the manifest: {', '.join(extra)}" if extra else "")
            + ". The result cannot be reproduced from the manifest until they match."
        )
    if tp + fn == 0:
        caveats.append("No fall sequence was scored, so recall says nothing.")

    return RunnerResult(id="e8", title=TITLE, rows=tuple(rows), caveats=tuple(caveats))


def _rate(successes: int, total: int) -> str:
    """Format a rate with its Wilson 95% interval, or `n/a` with no trials."""
    if total == 0:
        return f"n/a ({successes}/{total})"
    lo, hi = wilson_interval(successes, total, CONFIDENCE)
    return (
        f"{successes / total * PERCENT:.1f}% "
        f"[{lo * PERCENT:.1f}-{hi * PERCENT:.1f}] ({successes}/{total})"
    )
