"""Data models for self-repair loop (task P2-S1, MP §3.4, UC2).

When the verifier rejects, or workers dispute, at least 3 alerts of the same rule
for the same reason:
1. cluster the reasons with Spark;
2. propose a rule or concept fix (e.g. add the negative concept "backpack strap");
3. run Rule CI;
4. backtest past events: "would have removed 3 past false alerts";
5. show before/after, and wait for human approval.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from rulecam.contracts.core import Frozen
from rulecam.contracts.rule_ir import RuleIR


class DisputeRecord(Frozen):
    """A worker tap dispute or verifier rejection record."""

    rule_id: str
    event_code: str
    reason_choice: str
    reason_text: str
    source: Literal["worker_dispute", "verifier_rejection"] = "worker_dispute"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReasonCluster(Frozen):
    """A semantic cluster of dispute or rejection reasons."""

    cluster_id: str
    label: str
    count: int
    target_concept: str
    fix_kind: Literal["negative_concept", "concept_prompt", "threshold_tweak"]
    suggested_fix: str
    sample_reasons: tuple[str, ...] = ()


class ReasonClusterItem(BaseModel):
    """Spark structured output item for clustering reasons."""

    model_config = ConfigDict(extra="forbid")

    label: str
    target_concept: str
    fix_kind: Literal["negative_concept", "concept_prompt", "threshold_tweak"]
    suggested_fix: str
    reasons: list[str]


class ClusterResponse(BaseModel):
    """Spark structured response schema for clustered dispute reasons."""

    model_config = ConfigDict(extra="forbid")

    clusters: list[ReasonClusterItem]


class SelfRepairProposal(Frozen):
    """An end-to-end self-repair proposal awaiting human supervisor approval."""

    proposal_id: str
    rule_id: str
    original_rule: RuleIR
    repaired_rule: RuleIR
    cluster: ReasonCluster
    ci_green: bool
    mutants_killed: int
    mutants_total: int
    backtest_headline: str
    removed_false_alerts_count: int
    status: Literal["PENDING_APPROVAL", "APPROVED", "REJECTED"] = "PENDING_APPROVAL"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
