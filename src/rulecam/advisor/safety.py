"""Safety veto check for Site Advisor recommendations (task P2-12, SA §4 step 11).

Fails closed: Any change adding vehicles or people to a zone with an active
proximity or safety rule is vetoed unless a concrete mitigation is listed.
Vetoed changes are recorded in `considered_not_recommended`.
"""

from __future__ import annotations

from collections.abc import Sequence

from rulecam.contracts.advisor import Change, SafetyCheck
from rulecam.contracts.rule_ir import DistanceM, RuleIR
from rulecam.log import get_logger

_log = get_logger(__name__)

VEHICLE_KEYWORDS = ("forklift", "truck", "vehicle", "cart", "equipment")
PERSON_KEYWORDS = ("person", "worker", "operator", "crew", "aide", "runner")


def is_proximity_rule(rule: RuleIR) -> bool:
    """Check whether a safety rule enforces proximity / anti-collision constraints."""
    rule_id_lower = rule.id.lower()
    if any(k in rule_id_lower for k in ("proximity", "dist", "ped-forklift", "collision")):
        return True

    # Check predicate
    pred = rule.predicate
    if isinstance(pred, DistanceM) or getattr(pred, "op", "") == "distance_m":
        return True

    for sub in getattr(pred, "rules", ()):
        if isinstance(sub, DistanceM) or getattr(sub, "op", "") == "distance_m":
            return True

    return False


def rule_covers_zone(rule: RuleIR, zone: str) -> bool:
    """Check whether a rule applies to a specific zone or site-wide."""
    if not rule.zones:  # site-wide rule applies everywhere
        return True
    return any(z.lower() == zone.lower() or zone.lower() in z.lower() for z in rule.zones)


def check_safety_veto(
    changes: Sequence[Change],
    active_rules: Sequence[RuleIR],
) -> SafetyCheck:
    """Evaluate candidate changes against active safety rules.

    Args:
        changes: List of proposed operations from the closed vocabulary.
        active_rules: Active safety rules currently deployed at the site.

    Returns:
        SafetyCheck with verdict ('pass', 'pass_with_mitigation', 'veto')
        and considered_not_recommended records for any rejected changes.
    """
    rules_checked = tuple(r.id for r in active_rules)
    considered_not_recommended: list[tuple[str, str]] = []

    has_mitigation = False
    has_veto = False

    for change in changes:
        params_str = " ".join(f"{k}={v}" for k, v in change.params.items()).lower()
        zone = change.params.get("zone", change.params.get("location", ""))
        mitigation = change.params.get("mitigation")

        adds_vehicle = (
            change.op in ("add_capacity", "move_location")
            and any(v in params_str for v in VEHICLE_KEYWORDS)
        ) or any(v in params_str for v in ("add a forklift", "extra forklift", "additional truck"))

        adds_person = change.op in ("add_capacity", "move_location") and any(
            p in params_str for p in PERSON_KEYWORDS
        )

        if adds_vehicle or adds_person:
            # Check against active proximity rules
            for rule in active_rules:
                if is_proximity_rule(rule) and (not zone or rule_covers_zone(rule, zone)):
                    change_desc = f"{change.op}({change.params})"
                    if mitigation:
                        _log.info(
                            "safety_change_mitigated",
                            change=change_desc,
                            rule=rule.id,
                            mitigation=mitigation,
                        )
                        has_mitigation = True
                    else:
                        reason = (
                            f"Active safety rule '{rule.id}' restricts "
                            f"vehicle/pedestrian proximity in zone '{zone or 'site'}'. "
                            "Adding capacity without mitigation increases collision risk."
                        )
                        _log.warning(
                            "safety_change_vetoed",
                            change=change_desc,
                            rule=rule.id,
                            reason=reason,
                        )
                        considered_not_recommended.append((change_desc, reason))
                        has_veto = True

    if has_veto:
        verdict = "veto"
    elif has_mitigation:
        verdict = "pass_with_mitigation"
    else:
        verdict = "pass"

    return SafetyCheck(
        rules_checked=rules_checked,
        verdict=verdict,
        considered_not_recommended=tuple(considered_not_recommended),
    )
