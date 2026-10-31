"""Unit and integration tests for self-repair loop (task P2-S1, MP §3.4, UC2)."""

from __future__ import annotations

import pytest
from tests.fakes import FakeLLM, InMemoryTrackStore
from tests.rules.helpers import at, make_rule, walk_track

from rulecam.bus import Bus
from rulecam.contracts.rule_ir import RuleIR
from rulecam.repair import (
    ClusterResponse,
    DisputeRecord,
    ReasonCluster,
    approve_self_repair,
    cluster_dispute_reasons,
    propose_rule_repair,
    run_self_repair_loop,
)
from rulecam.repair.models import ReasonClusterItem, SelfRepairProposal


@pytest.fixture
def harness_rule() -> RuleIR:
    """Safety harness rule with harness concept subject to backpack strap confounders."""
    return make_rule(
        {
            "op": "all",
            "of": [
                {"op": "distance_m", "a": "person", "b": "forklift", "lt": 2.0},
                {"op": "moving", "object": "forklift", "speed_mps_gt": 0.2},
            ],
        },
        concepts={
            "harness": "safety harness strap",
            "person": "dock worker",
            "forklift": "forklift",
        },
        rule_id="strap-harness-check",
        severity="high",
    )


def test_no_repair_triggered_when_fewer_than_three_disputes(
    harness_rule: RuleIR,
) -> None:
    """Self-repair loop requires >= 3 disputes/rejections of the same reason to trigger."""
    store = InMemoryTrackStore()
    two_disputes = [
        DisputeRecord(
            rule_id="strap-harness-check",
            event_code="ev-01",
            reason_choice="backpack_strap_not_harness",
            reason_text="Es la correa de mi mochila",
        ),
        DisputeRecord(
            rule_id="strap-harness-check",
            event_code="ev-02",
            reason_choice="backpack_strap_not_harness",
            reason_text="Backpack strap, not harness",
        ),
    ]

    proposal = run_self_repair_loop(
        harness_rule,
        two_disputes,
        store,
        camera_id="cam1",
        t0=at(0),
        t1=at(100),
    )
    assert proposal is None


def test_self_repair_clusters_and_proposes_fix_on_three_disputes(
    harness_rule: RuleIR,
) -> None:
    """When >= 3 disputes occur for the same reason, self-repair triggers."""
    # Build track store with synthetic tracks
    store = InMemoryTrackStore()
    t_forklift = walk_track(
        "cam1:forklift:1", [(i * 0.1, 0.0) for i in range(100)], concept="forklift"
    )
    t_person = walk_track("cam1:person:1", [(i * 0.1, 1.0) for i in range(100)], concept="person")
    store.append([t_forklift, t_person])

    three_disputes = [
        DisputeRecord(
            rule_id="strap-harness-check",
            event_code="ev-01",
            reason_choice="backpack_strap_not_harness",
            reason_text="Es la correa de mi mochila, no el arnés",
        ),
        DisputeRecord(
            rule_id="strap-harness-check",
            event_code="ev-02",
            reason_choice="backpack_strap_not_harness",
            reason_text="Just my backpack strap hanging down",
        ),
        DisputeRecord(
            rule_id="strap-harness-check",
            event_code="ev-03",
            reason_choice="backpack_strap_not_harness",
            reason_text="Correa de mochila mistaken for strap",
        ),
    ]

    bus = Bus()
    proposal = run_self_repair_loop(
        harness_rule,
        three_disputes,
        store,
        camera_id="cam1",
        t0=at(0),
        t1=at(10),
        bus=bus,
    )

    assert proposal is not None
    assert proposal.rule_id == "strap-harness-check"
    assert proposal.status == "PENDING_APPROVAL"
    assert proposal.cluster.count == 3
    assert "backpack strap" in proposal.cluster.suggested_fix.lower()

    # Repaired rule has updated version and prompt exclusion
    assert proposal.repaired_rule.version == 2
    harness_prompt = proposal.repaired_rule.concepts["harness"].prompt
    assert "excluding backpack strap" in harness_prompt

    # Rule CI evaluated
    assert proposal.ci_green is True
    assert proposal.mutants_killed > 0
    assert "would have removed" in proposal.backtest_headline


def test_approve_self_repair_promotes_rule_version(
    harness_rule: RuleIR,
) -> None:
    """Approving a repair proposal updates status and publishes to bus."""
    cluster = ReasonCluster(
        cluster_id="c1",
        label="Backpack strap",
        count=3,
        target_concept="harness",
        fix_kind="negative_concept",
        suggested_fix="backpack strap",
    )
    repaired = propose_rule_repair(harness_rule, cluster)
    proposal = SelfRepairProposal(
        proposal_id="prop-1",
        rule_id=harness_rule.id,
        original_rule=harness_rule,
        repaired_rule=repaired,
        cluster=cluster,
        ci_green=True,
        mutants_killed=10,
        mutants_total=10,
        backtest_headline="would have removed 3 past false alerts",
        removed_false_alerts_count=3,
        status="PENDING_APPROVAL",
    )

    bus = Bus()
    approved = approve_self_repair(proposal, bus=bus)

    assert approved.version == 2
    assert approved.id == harness_rule.id

    # Trying to approve an already approved proposal fails
    approved_proposal = proposal.model_copy(update={"status": "APPROVED"})
    with pytest.raises(ValueError, match="Cannot approve"):
        approve_self_repair(approved_proposal)


def test_spark_structured_clustering(
    harness_rule: RuleIR,
) -> None:
    """Spark structured LLM clustering extracts semantic clusters."""
    fake_spark = FakeLLM()
    fake_spark.queue(
        ClusterResponse(
            clusters=[
                ReasonClusterItem(
                    label="Backpack strap confounder",
                    target_concept="harness",
                    fix_kind="negative_concept",
                    suggested_fix="backpack strap",
                    reasons=[
                        "Es la correa de mi mochila",
                        "Backpack strap hanging down",
                        "Correa de mochila",
                    ],
                )
            ]
        )
    )

    records = [
        DisputeRecord(
            rule_id=harness_rule.id,
            event_code=f"ev-{i}",
            reason_choice="backpack",
            reason_text=f"reason {i}",
        )
        for i in range(3)
    ]

    clusters = cluster_dispute_reasons(harness_rule, records, spark=fake_spark)
    assert len(clusters) == 1
    assert clusters[0].count == 3
    assert clusters[0].suggested_fix == "backpack strap"


def test_clustering_edge_cases_and_fix_prompt(harness_rule: RuleIR) -> None:
    """Covers deterministic shadow/glare clustering, empty records, and concept prompt tweak."""
    # Empty records
    assert cluster_dispute_reasons(harness_rule, []) == []

    # Shadow/glare records
    shadow_records = [
        DisputeRecord(
            rule_id=harness_rule.id,
            event_code="ev-sh-1",
            reason_choice="glare_or_shadow",
            reason_text="Reflejo del sol en el suelo",
        ),
        DisputeRecord(
            rule_id=harness_rule.id,
            event_code="ev-sh-2",
            reason_choice="glare_or_shadow",
            reason_text="Ground shadow from forklift",
        ),
    ]
    clusters = cluster_dispute_reasons(harness_rule, shadow_records)
    assert len(clusters) == 1
    assert clusters[0].fix_kind == "concept_prompt"
    assert "shadow" in clusters[0].label.lower()

    # Concept prompt tweak fix
    repaired = propose_rule_repair(harness_rule, clusters[0])
    assert repaired.version == harness_rule.version + 1
    assert "excluding ground shadows" in repaired.concepts["harness"].prompt

    # Fallback to first concept if target_concept not found
    orphan_cluster = ReasonCluster(
        cluster_id="c-unknown",
        label="Unknown target",
        count=3,
        target_concept="non_existent_concept",
        fix_kind="concept_prompt",
        suggested_fix="refined target prompt",
    )
    fallback_repaired = propose_rule_repair(harness_rule, orphan_cluster)
    first_key = next(iter(harness_rule.concepts.keys()))
    assert "refined target prompt" in fallback_repaired.concepts[first_key].prompt
