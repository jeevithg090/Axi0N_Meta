"""Mutation operators for Rule CI fuzzing (task P2-08, MP §3.4, V2 Pillar 3).

Applies mutation operators to a RuleIR:
- threshold x 0.1 and x 10
- flip lt/gt
- drop not
- swap all/any
- remove persist
- switch semantics from past to future (or future to past)
- widen a zone by 50%

A mutant is considered 'killed' if any boundary or variant trace changes its outcome
(i.e. violation is detected when pass was expected, or vice-versa).
"""

from __future__ import annotations

import copy
from typing import Any

from rulecam.contracts.rule_ir import RuleIR, ZoneSpec
from rulecam.rule_ci.models import MutationCase


def _mutate_thresholds(pred_dict: dict[str, Any], factor: float) -> bool:
    """Recursively scale numeric thresholds by `factor`. Returns True if any threshold changed."""
    changed = False
    for key in ("total_s_gte", "lt", "gt", "seconds", "speed_mps_gt", "require_within_s"):
        if key in pred_dict and isinstance(pred_dict[key], (int, float)):
            pred_dict[key] = round(float(pred_dict[key]) * factor, 3)
            changed = True

    if "trigger" in pred_dict and isinstance(pred_dict["trigger"], dict):
        changed |= _mutate_thresholds(pred_dict["trigger"], factor)
    if "requirement" in pred_dict and isinstance(pred_dict["requirement"], dict):
        changed |= _mutate_thresholds(pred_dict["requirement"], factor)
    if "of" in pred_dict:
        of_val = pred_dict["of"]
        if isinstance(of_val, dict):
            changed |= _mutate_thresholds(of_val, factor)
        elif isinstance(of_val, (list, tuple)):
            for item in of_val:
                if isinstance(item, dict):
                    changed |= _mutate_thresholds(item, factor)

    return changed


def _flip_lt_gt(pred_dict: dict[str, Any]) -> bool:
    """Flip lt to gt or gt to lt in distance or speed predicates."""
    changed = False
    if "lt" in pred_dict and pred_dict.get("op") == "distance_m":
        pred_dict["gt"] = pred_dict.pop("lt")
        changed = True
    elif "gt" in pred_dict and pred_dict.get("op") == "distance_m":
        pred_dict["lt"] = pred_dict.pop("gt")
        changed = True

    for sub in ("trigger", "requirement"):
        if sub in pred_dict and isinstance(pred_dict[sub], dict):
            changed |= _flip_lt_gt(pred_dict[sub])
    if "of" in pred_dict:
        of_val = pred_dict["of"]
        if isinstance(of_val, dict):
            changed |= _flip_lt_gt(of_val)
        elif isinstance(of_val, (list, tuple)):
            for item in of_val:
                if isinstance(item, dict):
                    changed |= _flip_lt_gt(item)
    return changed


def _drop_not_or_invert(pred_dict: dict[str, Any]) -> dict[str, Any]:
    """Drop not if present, or invert predicate truth value."""
    if pred_dict.get("op") == "not" and "of" in pred_dict and isinstance(pred_dict["of"], dict):
        return pred_dict["of"]
    # Wrap in not if not already present
    return {"op": "not", "of": copy.deepcopy(pred_dict)}


def _swap_all_any(pred_dict: dict[str, Any]) -> bool:
    """Swap all and any operators."""
    changed = False
    if pred_dict.get("op") == "all":
        pred_dict["op"] = "any"
        changed = True
    elif pred_dict.get("op") == "any":
        pred_dict["op"] = "all"
        changed = True

    if "of" in pred_dict and isinstance(pred_dict["of"], (list, tuple)):
        for item in pred_dict["of"]:
            if isinstance(item, dict):
                changed |= _swap_all_any(item)
    return changed


def _remove_persist(pred_dict: dict[str, Any]) -> dict[str, Any]:
    """Remove persist wrapper if present."""
    if pred_dict.get("op") == "persist" and "of" in pred_dict and isinstance(pred_dict["of"], dict):
        return pred_dict["of"]
    return pred_dict


def _switch_semantics(pred_dict: dict[str, Any]) -> bool:
    """Switch sequence semantics from past to future or future to past."""
    changed = False
    if pred_dict.get("op") == "sequence":
        sem = pred_dict.get("semantics", "past")
        pred_dict["semantics"] = "future" if sem == "past" else "past"
        changed = True
    return changed


def _widen_zones(zones: dict[str, ZoneSpec]) -> dict[str, ZoneSpec]:
    """Widen all zone boundaries by 50%."""
    widened: dict[str, ZoneSpec] = {}
    for zid, z in zones.items():
        if z.kind == "buffer" and z.buffer_m is not None:
            widened[zid] = z.model_copy(update={"buffer_m": round(z.buffer_m * 1.5, 3)})
        elif z.polygon_px:
            scaled_poly = tuple((round(x * 1.5, 1), round(y * 1.5, 1)) for x, y in z.polygon_px)
            widened[zid] = z.model_copy(update={"polygon_px": scaled_poly})
        else:
            widened[zid] = z
    return widened


def generate_mutations(rule: RuleIR) -> list[MutationCase]:
    """Generate the standard suite of mutants for a RuleIR."""
    mutants: list[MutationCase] = []
    rule_dict = rule.model_dump(mode="json")

    # 1. Threshold x 0.1
    dict_scale_down = copy.deepcopy(rule_dict)
    if _mutate_thresholds(dict_scale_down["predicate"], 0.1):
        mutants.append(
            MutationCase(
                name="threshold_scale_0.1",
                operator="scale_threshold_down",
                mutated_rule=RuleIR.model_validate(dict_scale_down),
                description="Scale numeric thresholds by 0.1x (e.g. 20s -> 2s, 2m -> 0.2m)",
            )
        )

    # 2. Threshold x 10
    dict_scale_up = copy.deepcopy(rule_dict)
    if _mutate_thresholds(dict_scale_up["predicate"], 10.0):
        mutants.append(
            MutationCase(
                name="threshold_scale_10.0",
                operator="scale_threshold_up",
                mutated_rule=RuleIR.model_validate(dict_scale_up),
                description="Scale numeric thresholds by 10x (e.g. 20s -> 200s, 2m -> 20m)",
            )
        )

    # 3. Flip lt/gt
    dict_flip = copy.deepcopy(rule_dict)
    if _flip_lt_gt(dict_flip["predicate"]):
        mutants.append(
            MutationCase(
                name="flip_lt_gt",
                operator="flip_comparison",
                mutated_rule=RuleIR.model_validate(dict_flip),
                description="Flip less-than (<) to greater-than (>) in spatial comparison",
            )
        )

    # 4. Drop not / Invert predicate
    dict_not = copy.deepcopy(rule_dict)
    dict_not["predicate"] = _drop_not_or_invert(dict_not["predicate"])
    mutants.append(
        MutationCase(
            name="drop_not_or_invert",
            operator="invert_truth_value",
            mutated_rule=RuleIR.model_validate(dict_not),
            description="Invert or drop negation on predicate tree",
        )
    )

    # 5. Swap all/any
    dict_swap = copy.deepcopy(rule_dict)
    if _swap_all_any(dict_swap["predicate"]):
        mutants.append(
            MutationCase(
                name="swap_all_any",
                operator="swap_logical_aggregation",
                mutated_rule=RuleIR.model_validate(dict_swap),
                description="Swap logical aggregations: all <-> any",
            )
        )

    # 6. Switch semantics (past -> future)
    dict_sem = copy.deepcopy(rule_dict)
    if _switch_semantics(dict_sem["predicate"]):
        mutants.append(
            MutationCase(
                name="switch_semantics",
                operator="switch_temporal_direction",
                mutated_rule=RuleIR.model_validate(dict_sem),
                description="Switch temporal sequence semantics (past <-> future)",
            )
        )

    # 7. Widen zone by 50%
    if rule.zones:
        dict_zones = copy.deepcopy(rule_dict)
        widened = _widen_zones(rule.zones)
        dict_zones["zones"] = {k: v.model_dump(mode="json") for k, v in widened.items()}
        mutants.append(
            MutationCase(
                name="widen_zone_50",
                operator="widen_zone_boundary",
                mutated_rule=RuleIR.model_validate(dict_zones),
                description="Widen zone boundary by 50%",
            )
        )

    # 8. Remove persist if present
    dict_persist = copy.deepcopy(rule_dict)
    new_pred = _remove_persist(dict_persist["predicate"])
    if new_pred != dict_persist["predicate"]:
        dict_persist["predicate"] = new_pred
        mutants.append(
            MutationCase(
                name="remove_persist",
                operator="remove_temporal_persist",
                mutated_rule=RuleIR.model_validate(dict_persist),
                description="Remove persist duration filter from predicate",
            )
        )

    return mutants
