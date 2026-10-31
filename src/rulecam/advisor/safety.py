"""Safety veto for Site Advisor changes (task P2-12, SA §4 step 11). It fails closed.

Minimum viable version of the task book: a change that may add vehicles or people to a zone
covered by an active proximity rule is vetoed unless the owner has recorded a mitigation for
that rule. The decision never trusts model output:

- `add_capacity` and `move_location` count as adding vehicles or people unless their `resource`
  is on a short code-side list of non-agent resources; any other op counts when its params say
  it adds a vehicle or person ("extra forklift", "assign another runner");
- the zones checked are the recommendation's scope plus every zone the params name, and a
  change that names no zone is checked against every rule;
- a `mitigation` written into the params by the model is ignored; only `mitigations` supplied by
  the caller (entered by the owner, keyed by rule id) count.

Vetoed changes are recorded in `SafetyCheck.considered_not_recommended` and never recommended.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel

from rulecam.contracts.advisor import Change, SafetyCheck
from rulecam.contracts.rule_ir import DistanceM, RuleIR
from rulecam.log import get_logger

_log = get_logger(__name__)

EXPOSURE_OPS: Final = frozenset({"add_capacity", "move_location"})
NON_AGENT_RESOURCES: Final = frozenset(
    {
        "dock_door",
        "staging_space",
        "staging_lane",
        "rack",
        "shelving",
        "scanner",
        "signage",
        "pass_bell",
        "heat_lamp",
        "ticket_rail",
        "order_printer",
    }
)
ADDS_AGENTS: Final = re.compile(
    r"\b(add|adds|adding|extra|additional|more|another|second|third|hire|deploy|assign)\b"
    r".*\b(forklifts?|trucks?|vehicles?|carts?|jacks?|agvs?|loaders?|tugs?|persons?|people|"
    r"workers?|operators?|crews?|aides?|runners?|staff|pickers?)\b",
    re.IGNORECASE,
)
PROXIMITY_ID_HINTS: Final = ("proximity", "ped-", "collision", "clearance")
ZONE_PARAMS: Final = ("zone", "location", "station", "to", "from")

ChangeVerdict = Literal["pass", "pass_with_mitigation", "veto"]


@dataclass(frozen=True)
class ChangeSafety:
    """The safety outcome for one change."""

    change: Change
    verdict: ChangeVerdict
    reasons: tuple[str, ...]


def _sub_predicates(node: object) -> Iterator[BaseModel]:
    """Yield every predicate model in a Rule IR predicate tree, depth first."""
    if isinstance(node, BaseModel):
        yield node
        for name in type(node).model_fields:
            yield from _sub_predicates(getattr(node, name))
    elif isinstance(node, tuple):
        for item in node:
            yield from _sub_predicates(item)


def is_proximity_rule(rule: RuleIR) -> bool:
    """Return whether a rule constrains distance between objects anywhere in its predicate."""
    if any(hint in rule.id.lower() for hint in PROXIMITY_ID_HINTS):
        return True
    return any(isinstance(node, DistanceM) for node in _sub_predicates(rule.predicate))


def adds_agents(change: Change) -> bool:
    """Return whether a change may add vehicles or people somewhere (fails closed)."""
    if change.op in EXPOSURE_OPS:
        return change.params.get("resource", "").lower() not in NON_AGENT_RESOURCES
    params = " ".join(f"{key} {value}" for key, value in change.params.items())
    return ADDS_AGENTS.search(params.replace("_", " ")) is not None


def _zones_of(change: Change, scope_ids: Sequence[str]) -> set[str]:
    named = {change.params[key] for key in ZONE_PARAMS if change.params.get(key)}
    return {zone.lower() for zone in (*scope_ids, *named)}


def rule_covers(rule: RuleIR, zones: set[str]) -> bool:
    """Return whether a rule applies in any of `zones` (site-wide rules and no zones: always)."""
    if not rule.zones or not zones:
        return True
    rule_zones = {zone.lower() for zone in rule.zones}
    return any(z == r or z.startswith(r) or r.startswith(z) for z in zones for r in rule_zones)


def assess_change(
    change: Change,
    active_rules: Sequence[RuleIR],
    *,
    scope_ids: Sequence[str],
    mitigations: Mapping[str, str],
) -> ChangeSafety:
    """Decide whether one change may be recommended under the active safety rules.

    Args:
        change: A proposed change.
        active_rules: The site's active rules.
        scope_ids: The recommendation's scope (station ids).
        mitigations: Owner-entered mitigations keyed by rule id; model text never counts.

    Returns:
        `veto` if any covering proximity rule has no mitigation, `pass_with_mitigation` if every
        covering rule has one, else `pass`, with a reason per affected rule.
    """
    if not adds_agents(change):
        return ChangeSafety(change=change, verdict="pass", reasons=())
    zones = _zones_of(change, scope_ids)
    affected = [r for r in active_rules if is_proximity_rule(r) and rule_covers(r, zones)]
    vetoes = tuple(
        f"may add vehicles or people where active safety rule {rule.id!r} applies, "
        "and no mitigation is recorded for it"
        for rule in affected
        if rule.id not in mitigations
    )
    if vetoes:
        _log.warning("advisor_change_vetoed", op=change.op, rules=[r.id for r in affected])
        return ChangeSafety(change=change, verdict="veto", reasons=vetoes)
    if affected:
        mitigated = tuple(f"{rule.id}: {mitigations[rule.id]}" for rule in affected)
        return ChangeSafety(change=change, verdict="pass_with_mitigation", reasons=mitigated)
    return ChangeSafety(change=change, verdict="pass", reasons=())


def describe(change: Change) -> str:
    """Return a stable one-line description of a change."""
    params = ", ".join(f"{key}={value}" for key, value in sorted(change.params.items()))
    return f"{change.op}({params})"


def safety_check(
    chosen: ChangeSafety | None,
    considered: Sequence[tuple[str, str]],
    active_rules: Sequence[RuleIR],
) -> SafetyCheck:
    """Summarise the safety outcome of a recommendation.

    Args:
        chosen: The change being recommended, or None if no change survived.
        considered: `(change, reason)` for every change considered and not recommended.
        active_rules: The rules that were checked.

    Returns:
        The contract `SafetyCheck`; its verdict is the chosen change's, or `veto` without one.
    """
    return SafetyCheck(
        rules_checked=tuple(rule.id for rule in active_rules),
        verdict="veto" if chosen is None else chosen.verdict,
        considered_not_recommended=tuple(considered),
    )
