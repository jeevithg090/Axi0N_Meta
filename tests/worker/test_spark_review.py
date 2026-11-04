"""Muse Spark 1.3 reviews disputes only when no Tier 2 decided (fakes, no network)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from rulecam.clients.transport import ModelTransportError
from rulecam.contracts import CandidateEvent, Dispute, RuleIR, VerifierVerdict
from rulecam.verify.policy import NO_TIER2_REASONING
from rulecam.verify.tier2 import DisputeAnswer
from rulecam.worker.spark_review import SparkDisputeReviewer, strip_frames
from tests.fakes import EPOCH
from tests.verify.helpers import load_rule
from tests.worker.helpers import make_candidate

M = TypeVar("M", bound=BaseModel)


@dataclass
class Policy:
    outcome: str = "pending"
    reasoning: str = NO_TIER2_REASONING

    def review_dispute(
        self,
        candidate: CandidateEvent,
        rule: RuleIR,
        dispute: Dispute,
        tier1: VerifierVerdict | None = None,
    ) -> Dispute:
        return dispute.model_copy(
            update={"outcome": self.outcome, "reviewer_reasoning": self.reasoning}
        )


@dataclass
class Spark:
    answer: DisputeAnswer | None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def structured(
        self,
        *,
        model: str = "muse-spark-1.3",
        messages: list[dict[str, object]],
        schema: type[M],
        camera_id: str | None = None,
    ) -> M:
        self.calls.append({"messages": messages})
        if self.answer is None:
            raise ModelTransportError("down")
        return schema.model_validate(self.answer.model_dump())


class Attach:
    def attach(self, path: Path) -> dict[str, object]:
        return {"type": "input_image", "file_id": path.name}


DISPUTE = Dispute(channel="tap", reason_text="It was the driver on the forklift, not me.")


def _strips(root: Path, candidate: CandidateEvent, n: int = 8) -> None:
    folder = root / candidate.camera_id / candidate.event_id
    folder.mkdir(parents=True)
    for i in range(1, n + 1):
        (folder / f"{i}.jpg").write_bytes(b"jpg")


def test_spark_upholds_a_dispute_the_policy_left_pending(tmp_path: Path) -> None:
    candidate, rule = make_candidate("ev-1", EPOCH), load_rule()
    _strips(tmp_path, candidate)
    spark = Spark(DisputeAnswer(outcome="upheld", reason="The frames show only the driver."))
    reviewer = SparkDisputeReviewer(Policy(), spark, attach=Attach(), stills_root=tmp_path)
    reviewed = reviewer.review_dispute(candidate, rule, DISPUTE)
    assert reviewed.outcome == "upheld"
    assert reviewed.reviewer_reasoning is not None
    assert reviewed.reviewer_reasoning.startswith("muse-spark-1.3 (no Tier 2 configured)")
    parts = spark.calls[0]["messages"][1]["content"]
    assert sum(1 for p in parts if p.get("type") == "input_image") == 4
    assert "not me" in parts[0]["text"]


def test_a_tier2_decision_or_a_spark_failure_is_kept(tmp_path: Path) -> None:
    candidate, rule = make_candidate("ev-1", EPOCH), load_rule()
    decided = SparkDisputeReviewer(
        Policy(outcome="rejected", reasoning="Spark Open: stands"), Spark(None)
    )
    assert decided.review_dispute(candidate, rule, DISPUTE).outcome == "rejected"
    failing = SparkDisputeReviewer(Policy(), Spark(None))
    kept = failing.review_dispute(candidate, rule, DISPUTE)
    assert kept.outcome == "pending"
    assert kept.reviewer_reasoning == NO_TIER2_REASONING


def test_strip_frames_prefers_listed_paths(tmp_path: Path) -> None:
    listed = tmp_path / "a.jpg"
    listed.write_bytes(b"x")
    candidate = make_candidate("ev-2", EPOCH).model_copy(update={"strip_paths": (str(listed),)})
    assert strip_frames(candidate, tmp_path) == [listed]
    assert strip_frames(make_candidate("ev-3", EPOCH), None) == []
