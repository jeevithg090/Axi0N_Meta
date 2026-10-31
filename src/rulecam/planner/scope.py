"""A rule's camera scope: the cameras it can be checked on (P2-06c, P3-08).

One definition, used by the planner (compile grounding names the calibration of these cameras,
acceptance measures its per-camera checks on them) and by the runtime (activation runs the rule
only on them), so what is approved is what runs.

The scope of a rule is the union of:

- the cameras of the `observable`/`caveat` feasibility verdicts that belong to the rule
  (`rule_verdicts`: matched by the rule's source text against each verdict's concern; when none
  matches, by concepts: every probed concept is one of the rule's, key or prompt, and every
  dynamic concept prompt of the rule was probed);
- the cameras of its zones;
- the cameras its `calibration` names (`ground-plane@<camera>[,...]`).

A rule with none of these has an empty scope; callers then treat it as running on every camera.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from rulecam.contracts.plan import FeasibilityVerdict
from rulecam.contracts.rule_ir import RuleIR

SCOPING_VERDICTS: Final = frozenset({"observable", "caveat"})
"""Feasibility verdicts whose evidence cameras scope a rule."""


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def rule_calibration_cameras(rule: RuleIR) -> set[str]:
    """The cameras a rule uses: its zones' cameras and those its `calibration` names."""
    named = (part.rsplit("@", 1)[-1] for part in (rule.calibration or "").split(",") if "@" in part)
    return {spec.camera_id for spec in rule.zones.values()} | set(named)


def rule_verdicts(
    feasibility: Iterable[FeasibilityVerdict], rule: RuleIR
) -> list[FeasibilityVerdict]:
    """The `observable`/`caveat` feasibility verdicts with evidence that belong to `rule`.

    Args:
        feasibility: The plan's verdicts.
        rule: The rule.

    Returns:
        The verdicts matched by source text, else those matched by concepts.
    """
    usable = [v for v in feasibility if v.verdict in SCOPING_VERDICTS and v.evidence]
    text = _norm(rule.source.text or "")
    by_text = [v for v in usable if text and _norm(v.concern) == text]
    if by_text:
        return by_text
    names = {_norm(n) for key, spec in rule.concepts.items() for n in (key, spec.prompt)}
    dynamic = {_norm(spec.prompt) for spec in rule.concepts.values() if not spec.static}
    matched = []
    for verdict in usable:
        probed = {_norm(p.concept) for p in verdict.evidence}
        if probed <= names and dynamic <= probed:
            matched.append(verdict)
    return matched


def rule_scope(feasibility: Iterable[FeasibilityVerdict], rule: RuleIR) -> tuple[str, ...]:
    """The cameras `rule` is checked and run on: feasibility evidence, zones and calibration.

    Args:
        feasibility: The plan's verdicts.
        rule: The rule.

    Returns:
        Sorted camera ids; empty when there is no camera evidence for the rule.
    """
    cameras = rule_calibration_cameras(rule)
    for verdict in rule_verdicts(feasibility, rule):
        cameras |= {probe.camera_id for probe in verdict.evidence}
    return tuple(sorted(cameras))


def scoped_cameras(
    rule: RuleIR, cameras: Iterable[str], feasibility: Sequence[FeasibilityVerdict] = ()
) -> list[str]:
    """The cameras a per-camera check of `rule` measures: its scope, else every site camera.

    Args:
        rule: The rule.
        cameras: The site's camera ids (used when the scope is empty).
        feasibility: The plan's verdicts.

    Returns:
        Sorted camera ids.
    """
    return list(rule_scope(feasibility, rule) or sorted(cameras))
