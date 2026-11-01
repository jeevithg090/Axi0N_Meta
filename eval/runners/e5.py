"""Runner E5-lite: what does Anonymous Mode cost in accuracy? (task P4-07, MP §8 E5).

Anonymous Mode replaces people with skeletons (P4-05), so the question a site owner will ask is
blunt: does turning privacy on make the system worse at noticing someone is hurt? E5-lite answers
it the only honest way — the **same clips** scored twice, once on raw frames and once on the
skeleton render, for the rules that depend on a body: person down and PPE.

Both arms are scored per clip, so the comparison is paired. The figure that matters is the
**difference** and its range across clips, not two separate accuracies: two numbers measured on
the same clips are far more comparable than two numbers measured on different ones.

A rule that only one arm could score at all (a PPE glyph the renderer does not draw, say) is
reported as unpaired and left out of the difference, because a missing arm is not a wrong answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from eval.runners.common import PERCENT, Row, RunnerResult, not_measured, percent, plural
from pydantic import BaseModel, ConfigDict, Field

TITLE: Final = "What does Anonymous Mode cost in accuracy?"
BODY_RULES: Final = ("person_down", "helmet_required", "vest_required")


class ArmScore(BaseModel):
    """One arm's counts on one clip and rule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    clip: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    arm: str = Field(pattern=r"^(raw|skeleton)$")
    tp: int = Field(default=0, ge=0)
    fp: int = Field(default=0, ge=0)
    fn: int = Field(default=0, ge=0)
    scored: bool = True
    note: str = ""

    @property
    def correct(self) -> int:
        """Labelled episodes this arm got right."""
        return self.tp

    @property
    def episodes(self) -> int:
        """Labelled episodes on this clip and rule."""
        return self.tp + self.fn


def aggregate_e5(scores: Sequence[ArmScore]) -> RunnerResult:
    """Compare the raw and skeleton arms on the clips both could score.

    Args:
        scores: Both arms' per-clip counts. Pair up by `(clip, rule_id)`.

    Returns:
        The result. With nothing paired, a "not measured" result.
    """
    by_key: dict[tuple[str, str], dict[str, ArmScore]] = {}
    for score in scores:
        by_key.setdefault((score.clip, score.rule_id), {})[score.arm] = score

    paired = {
        key: arms
        for key, arms in by_key.items()
        if "raw" in arms and "skeleton" in arms and arms["raw"].scored and arms["skeleton"].scored
    }
    if not paired:
        return not_measured(
            "e5-lite",
            TITLE,
            "no clip was scored on both arms. E5-lite needs the same clips run raw and in "
            "Anonymous Mode, which needs the UC2 recordings.",
        )

    rows: list[Row] = []
    caveats: list[str] = []
    for arm in ("raw", "skeleton"):
        tp = sum(a[arm].tp for a in paired.values())
        fn = sum(a[arm].fn for a in paired.values())
        fp = sum(a[arm].fp for a in paired.values())
        rows.append(
            Row(
                name=f"Recall, {arm}",
                value=percent(tp, tp + fn),
                detail=plural(len(paired), "paired clip/rule pair"),
            )
        )
        rows.append(
            Row(
                name=f"Precision, {arm}",
                value=percent(tp, tp + fp),
                detail=plural(len(paired), "paired clip/rule pair"),
            )
        )

    raw_tp = sum(a["raw"].tp for a in paired.values())
    skel_tp = sum(a["skeleton"].tp for a in paired.values())
    episodes = sum(a["raw"].episodes for a in paired.values())
    if episodes:
        delta = (skel_tp - raw_tp) / episodes * PERCENT
        pairs = plural(len(paired), "clip/rule pair")
        rows.append(
            Row(
                name="Recall difference (skeleton minus raw)",
                value=f"{delta:+.1f} points",
                detail=f"paired on {pairs}, {plural(episodes, 'episode')}",
            )
        )
    worse = sorted(f"{c}/{r}" for (c, r), a in paired.items() if a["skeleton"].tp < a["raw"].tp)
    if worse:
        caveats.append(f"Anonymous Mode missed what raw caught on: {', '.join(worse)}.")
    better = sorted(f"{c}/{r}" for (c, r), a in paired.items() if a["skeleton"].tp > a["raw"].tp)
    if better:
        caveats.append(
            f"Anonymous Mode caught what raw missed on: {', '.join(better)}. That is not a win "
            "for privacy; on this few clips it is noise unless the same thing repeats."
        )

    unpaired = sorted(f"{c}/{r}" for (c, r), arms in by_key.items() if (c, r) not in paired)
    if unpaired:
        caveats.append(
            f"Not paired, so left out of the difference: {', '.join(unpaired)}. One arm could "
            "not score these at all, which is a gap, not a wrong answer."
        )
    non_body = sorted({r for _, r in paired if r not in BODY_RULES})
    if non_body:
        caveats.append(
            f"These rules do not depend on a body, so the skeleton should make no difference to "
            f"them and they are weak evidence either way: {', '.join(non_body)}."
        )
    caveats.append(
        "Both arms were scored against the same labels on the same clips, so the difference is "
        "paired. It still comes from our own clips at one site."
    )
    return RunnerResult(id="e5-lite", title=TITLE, rows=tuple(rows), caveats=tuple(caveats))
