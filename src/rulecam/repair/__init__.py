"""Self-repair loop (task P2-S1, MP §3.4).

Cluster rejections / disputes -> propose fix -> Rule CI -> backtest -> approval.
"""

from __future__ import annotations

from rulecam.repair.cluster import cluster_dispute_reasons
from rulecam.repair.fix import propose_rule_repair
from rulecam.repair.loop import approve_self_repair, run_self_repair_loop
from rulecam.repair.models import (
    ClusterResponse,
    DisputeRecord,
    ReasonCluster,
    SelfRepairProposal,
)

__all__ = [
    "ClusterResponse",
    "DisputeRecord",
    "ReasonCluster",
    "SelfRepairProposal",
    "approve_self_repair",
    "cluster_dispute_reasons",
    "propose_rule_repair",
    "run_self_repair_loop",
]
