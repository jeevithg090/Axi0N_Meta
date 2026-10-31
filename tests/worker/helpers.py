"""Shared builders for the worker page tests (task P2-11)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.contracts import CandidateEvent, Dispute, RuleIR, Severity, VerifierVerdict
from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import AcceptanceResult, SitePlan
from rulecam.evidence.chain import EventChain
from rulecam.rules.backtest import DecisionContext, candidate_payload
from rulecam.worker import DailyLimiter, DisputeService, worker_router
from rulecam.worker.log import event_code
from tests.fakes import EPOCH, FakeClock
from tests.verify.helpers import load_rule

BASE_URL = "https://ui.rulecam.example"
SITE = "main-st"
TRACK_SUBJECT = "track-7"
STRIP = ("data/chunks/cam1/0001.mp4", "data/strips/ev/f0.png")


def make_plan(rule: RuleIR | None = None, *, site_name: str = "Main St <Kitchen>") -> SitePlan:
    rule = rule or load_rule()
    return SitePlan(
        plan_id="main-st-kitchen",
        site_name=site_name,
        setting_inferred="commercial_kitchen",
        languages=("es", "en"),
        request=(),
        inventory=(),
        feasibility=(),
        rules=(rule,),
        privacy_mode=PrivacyMode.ANONYMOUS,
        retention_days=14,
        acceptance=AcceptanceResult(checks=(), rounds=0),
        back_translation=(
            f"[es]\n- {rule.id}: Lávese las manos 20 s <b>antes</b> de la línea.\n\n"
            f"[en]\n- {rule.id}: Wash hands for 20 s before entering the line."
        ),
    )


def make_candidate(event_id: str, ts: datetime, rule: RuleIR | None = None) -> CandidateEvent:
    rule = rule or load_rule()
    return CandidateEvent(
        event_id=event_id,
        rule_id=rule.id,
        camera_id="cam1",
        ts_start=ts,
        ts_end=ts + timedelta(seconds=8),
        severity=Severity.MEDIUM,
        predicate_trace={"subject": TRACK_SUBJECT, "dwell_s": 8.0},
        strip_paths=STRIP,
        track_window_hash="t" * 64,
        plan_version_hash="p" * 64,
        rule_ir_hash="r" * 64,
    )


def chain_candidate(
    chain: EventChain, candidate: CandidateEvent, rule: RuleIR | None = None
) -> None:
    rule = rule or load_rule()
    context = DecisionContext(rule=rule, t0=candidate.ts_start, t1=candidate.ts_end)
    chain.append("candidate", candidate_payload(candidate, context))
    verdict = VerifierVerdict(
        tier="glimmer",
        verdict="violation",
        score=0.9,
        reason="hands left the sink early",
        latency_ms=10,
        model_version="glimmer",
    )
    chain.append("verdict", {"event_id": candidate.event_id, **verdict.model_dump(mode="json")})


@dataclass
class FakeReviewer:
    """Tier 2 stand-in: answers `outcome`, or raises `error`."""

    outcome: str = "upheld"
    error: Exception | None = None
    calls: list[tuple[CandidateEvent, Dispute, VerifierVerdict | None]] = field(
        default_factory=list
    )

    def review_dispute(
        self,
        candidate: CandidateEvent,
        rule: RuleIR,
        dispute: Dispute,
        tier1: VerifierVerdict | None = None,
    ) -> Dispute:
        self.calls.append((candidate, dispute, tier1))
        if self.error is not None:
            raise self.error
        return dispute.model_copy(
            update={
                "outcome": self.outcome,
                "reviewer_reasoning": "a backpack strap, not a harness",
            }
        )


@dataclass
class World:
    """A site with one plan, its chain and the worker API."""

    tmp: Path
    clock: FakeClock = field(default_factory=lambda: FakeClock(EPOCH))
    reviewer: FakeReviewer = field(default_factory=FakeReviewer)
    plan: SitePlan = field(default_factory=make_plan)
    base_url: str | None = BASE_URL
    limiter: Callable[[FakeClock], DailyLimiter] = DailyLimiter

    def __post_init__(self) -> None:
        self.chain = EventChain(self.tmp / "chain", SITE, clock=self.clock)
        plans = {self.plan.plan_id: self.plan}
        app = FastAPI()
        self.disputes = DisputeService(
            self.chain, self.reviewer, clock=self.clock, limiter=self.limiter(self.clock)
        )
        app.include_router(
            worker_router(
                plans=plans.get,
                chain=self.chain,
                disputes=self.disputes,
                clock=self.clock,
                public_base_url=self.base_url,
            )
        )
        self.client = TestClient(app)

    def chain_text(self) -> str:
        return "".join(
            p.read_text("utf-8") for p in sorted((self.tmp / "chain" / SITE).glob("*.jsonl"))
        )


PAGE = "/worker/main-st-kitchen/cam1"


def world_with_event(tmp_path: Path, **kwargs: object) -> tuple[World, str]:
    world = World(tmp_path, **kwargs)  # type: ignore[arg-type]
    chain_candidate(world.chain, make_candidate("ev-strap-1", EPOCH - timedelta(minutes=7)))
    return world, event_code("ev-strap-1")


def dispute(code: str, text: str = "era la correa de mi mochila") -> dict[str, str]:
    return {"event_code": code, "reason_choice": "lookalike_object", "reason_text": text}
