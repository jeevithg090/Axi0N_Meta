"""Self-repair loop orchestration (task P2-S1, MP §3.4, UC2).

When the verifier rejects, or workers dispute, at least 3 alerts of the same rule
for the same reason:
1. cluster the reasons with Spark;
2. propose a rule or concept fix (e.g. add the negative concept "backpack strap");
3. run Rule CI;
4. backtest past events (P1-07): "would have removed 3 past false alerts";
5. show before/after, and wait for human approval.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from rulecam.bus import Bus, Topic
from rulecam.contracts import TrackStore
from rulecam.contracts.protocols import StructuredLLM
from rulecam.contracts.rule_ir import RuleIR
from rulecam.log import get_logger
from rulecam.repair.cluster import cluster_dispute_reasons
from rulecam.repair.fix import propose_rule_repair
from rulecam.repair.models import DisputeRecord, SelfRepairProposal
from rulecam.rule_ci.runner import run_rule_ci
from rulecam.rules.backtest import backtest

_log = get_logger(__name__)


def run_self_repair_loop(
    rule: RuleIR,
    records: Sequence[DisputeRecord],
    store: TrackStore,
    *,
    camera_id: str,
    t0: datetime,
    t1: datetime,
    spark: StructuredLLM | None = None,
    bus: Bus | None = None,
    min_cluster_size: int = 3,
) -> SelfRepairProposal | None:
    """Run the 5-step self-repair loop when at least 3 disputes share a reason.

    Args:
        rule: Active RuleIR experiencing disputes.
        records: Collection of worker disputes or verifier rejections.
        store: TrackStore containing historical tracks for backtesting.
        camera_id: Camera to backtest against.
        t0: Start of the historical backtest window.
        t1: End of the historical backtest window.
        spark: StructuredLLM client for clustering.
        bus: Optional pub/sub bus to broadcast progress.
        min_cluster_size: Minimum number of shared disputes to trigger repair (default: 3).

    Returns:
        SelfRepairProposal in PENDING_APPROVAL status if a cluster >= min_cluster_size is found,
        or None if insufficient clustered disputes exist.
    """
    # Step 1: Cluster dispute reasons
    clusters = cluster_dispute_reasons(rule, records, spark=spark)
    actionable = [c for c in clusters if c.count >= min_cluster_size]
    if not actionable:
        _log.info(
            "self_repair.no_actionable_cluster",
            rule_id=rule.id,
            total_records=len(records),
            min_required=min_cluster_size,
        )
        return None

    top_cluster = actionable[0]
    _log.info(
        "self_repair.cluster_identified",
        rule_id=rule.id,
        cluster_label=top_cluster.label,
        count=top_cluster.count,
    )

    # Step 2: Propose rule fix
    repaired_rule = propose_rule_repair(rule, top_cluster)

    # Step 3: Run Rule CI on repaired rule
    ci_report = run_rule_ci(repaired_rule, bus=bus)

    # Step 4: Backtest past events
    bt_report = backtest(repaired_rule, store, camera_id, t0, t1, baseline=rule)
    removed_count = len(bt_report.diff.removed) if bt_report.diff is not None else 0
    headline = f"would have removed {removed_count} past false alerts"

    # Step 5: Package proposal awaiting human approval
    proposal = SelfRepairProposal(
        proposal_id=f"repair-{uuid.uuid4().hex[:8]}",
        rule_id=rule.id,
        original_rule=rule,
        repaired_rule=repaired_rule,
        cluster=top_cluster,
        ci_green=ci_report.ci_green,
        mutants_killed=ci_report.mutants_killed,
        mutants_total=ci_report.mutants_total,
        backtest_headline=headline,
        removed_false_alerts_count=removed_count,
        status="PENDING_APPROVAL",
    )

    if bus is not None:
        bus.publish(
            Topic.PLANNER_STEP,
            {
                "rule_id": rule.id,
                "step": "self_repair",
                "status": "pending_approval",
                "detail": headline,
            },
        )

    return proposal


def approve_self_repair(
    proposal: SelfRepairProposal,
    *,
    bus: Bus | None = None,
) -> RuleIR:
    """Approve a self-repair proposal and activate the repaired rule version.

    Args:
        proposal: The pending SelfRepairProposal.
        bus: Optional bus to notify plan/rule update.

    Returns:
        The activated, approved RuleIR.
    """
    if proposal.status != "PENDING_APPROVAL":
        msg = f"Cannot approve repair proposal with status '{proposal.status}'."
        raise ValueError(msg)

    approved_rule = proposal.repaired_rule
    _log.info(
        "self_repair.approved",
        rule_id=proposal.rule_id,
        new_version=approved_rule.version,
    )

    if bus is not None:
        bus.publish(
            Topic.PLAN_UPDATED,
            {
                "rule_id": approved_rule.id,
                "version": approved_rule.version,
                "status": "approved",
                "repair_proposal_id": proposal.proposal_id,
            },
        )

    return approved_rule
