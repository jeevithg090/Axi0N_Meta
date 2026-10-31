"""Rule fix proposal generator for self-repair (task P2-S1, MP §3.4).

Applies concrete concept prompt tweaks, negative concepts, or predicate adjustments
to generate candidate repaired RuleIR objects from reason clusters.
"""

from __future__ import annotations

from rulecam.contracts.rule_ir import ConceptSpec, RuleIR
from rulecam.repair.models import ReasonCluster


def propose_rule_repair(rule: RuleIR, cluster: ReasonCluster) -> RuleIR:
    """Generate a candidate repaired RuleIR incorporating the cluster's suggested fix.

    Args:
        rule: Original RuleIR before repair.
        cluster: The ReasonCluster with >= 3 occurrences triggering the fix.

    Returns:
        Repaired RuleIR with incremented version.
    """
    new_concepts = dict(rule.concepts)

    if cluster.fix_kind in ("negative_concept", "concept_prompt"):
        target = cluster.target_concept
        if target in new_concepts:
            orig_spec = new_concepts[target]
            if cluster.fix_kind == "negative_concept":
                new_prompt = f"{orig_spec.prompt}, excluding {cluster.suggested_fix}"
            else:
                new_prompt = cluster.suggested_fix
            new_concepts[target] = ConceptSpec(prompt=new_prompt, static=orig_spec.static)
        elif new_concepts:
            # Fallback: update first available concept
            first_key = next(iter(new_concepts.keys()))
            orig_spec = new_concepts[first_key]
            new_prompt = f"{orig_spec.prompt}, excluding {cluster.suggested_fix}"
            new_concepts[first_key] = ConceptSpec(prompt=new_prompt, static=orig_spec.static)

    # Return upgraded rule with bumped version
    return rule.model_copy(
        update={
            "version": rule.version + 1,
            "concepts": new_concepts,
        }
    )
