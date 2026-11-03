"""Golden step kinds for recorded-run fixtures (`scenarios/<name>.yaml`, kind `demo.*`).

Every step takes `fixture: <folder>` (relative to the working directory, as `rulecam golden`
runs from the repository root) and re-derives one part of the live run from the fixture's
files. None of them reaches the network or a model: the one model call, Tier 1, is served from
the fixture's recorded replies in replay mode whatever the runner's mode is, because the live
run already recorded it (`rulecam golden capture`). `golden record` therefore writes the
expected outputs from the recording, and `golden verify` proves the code still reproduces them.

- `demo.plan`: the approved plan: its rules, their hashes against the run's, canonical form,
  acceptance trace and the planner's steps;
- `demo.rule_ci`: Rule CI on every rule (boundary traces, mutations), against the acceptance
  trace;
- `demo.evidence`: chain integrity from the first record, the Merkle root, and every candidate of
  the run re-derived from its recorded track window;
- `demo.verify`: Tier 1 (Glimmer) on each candidate from its recorded reply, then the policy's
  decision;
- `demo.score`: the eval harness's score of the run (candidates, alerts, false alerts).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from rulecam.contracts import Calibration, CandidateEvent, RuleIR, SitePlan, VerifierVerdict
from rulecam.contracts.events import EventRecord
from rulecam.demo.errors import FixtureError
from rulecam.demo.fixture import GoldenFixture
from rulecam.demo.tier1 import replay_cache, replay_tier1, with_strip
from rulecam.eval.schema import load_eval_set
from rulecam.eval.score import score_runs
from rulecam.evidence.chain import verify_chain
from rulecam.evidence.merkle import merkle_root
from rulecam.planner.canonical import canonical_rule_hash, canonicalize_rule
from rulecam.replay.cache import ReplayCache
from rulecam.replay.golden import register_step
from rulecam.rule_ci.runner import run_rule_ci
from rulecam.rules.backtest import read_candidate_payload, rederive
from rulecam.rules.hashing import rule_ir_hash
from rulecam.verify.policy import VerificationPolicy

PLANNER_STEP_TOPIC = "planner.step"
_SCORED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def fixture_of(params: Mapping[str, object]) -> GoldenFixture:
    """The fixture a step's `fixture` parameter names.

    Raises:
        FixtureError: If the parameter is missing or not a folder.
    """
    value = params.get("fixture")
    root = Path(str(value)) if isinstance(value, str) and value else None
    if root is None or not root.is_dir():
        msg = f"golden step needs `fixture: <folder>`; got {value!r}"
        raise FixtureError(msg)
    return GoldenFixture(root)


def planner_steps(fixture: GoldenFixture) -> list[dict[str, object]]:
    """The planner's steps as the run saw them on `/ws`: each step's last status and detail."""
    last: dict[str, dict[str, object]] = {}
    for frame in fixture.run().bus:
        if frame.topic == PLANNER_STEP_TOPIC and isinstance(frame.payload.get("step"), str):
            step = str(frame.payload["step"])
            last.pop(step, None)  # keep first-seen order of the final statuses
            last[step] = {
                "step": step,
                "status": frame.payload.get("status"),
                "detail": frame.payload.get("detail", ""),
            }
    return list(last.values())


def _rule_summary(rule: RuleIR, request: str | None) -> dict[str, object]:
    return {
        "id": rule.id,
        "severity": str(rule.severity),
        "predicate": rule.predicate.model_dump(mode="json"),
        "rule_ir_hash": rule_ir_hash(rule),
        "canonical_rule_hash": canonical_rule_hash(rule),
        "canonical": canonicalize_rule(rule, request_text=request) == rule,
    }


@register_step("demo.plan")
def plan_step(params: Mapping[str, object], cache: ReplayCache) -> object:  # noqa: ARG001
    """Summarise the approved plan and check its rules are the ones the runtime ran."""
    fixture = fixture_of(params)
    plan, run = fixture.plan(), fixture.run()
    request = plan.request[0].text if plan.request else None
    hashes = tuple(rule_ir_hash(rule) for rule in plan.rules)
    return {
        "plan_id": plan.plan_id,
        "version": plan.version,
        "request": request,
        "rules": [_rule_summary(rule, request) for rule in plan.rules],
        "rule_hashes_match_run": hashes == run.rule_hashes,
        "feasibility": [
            {"concern": v.concern, "verdict": str(v.verdict)} for v in plan.feasibility
        ],
        "acceptance": [
            {"item": c.item, "check": c.check, "passed": c.passed, "detail": c.detail}
            for c in plan.acceptance.checks
        ],
        "planner_steps": planner_steps(fixture),
    }


def _calibrations(fixture: GoldenFixture) -> dict[str, Calibration]:
    """Ground calibrations by camera, from the run's chained candidate contexts."""
    found: dict[str, Calibration] = {}
    for record in fixture.run_records():
        if record.kind == "candidate":
            _, context = read_candidate_payload(record)
            if context.calibration is not None:
                found[context.calibration.camera_id] = context.calibration
    return found


def _acceptance_detail(plan: SitePlan, rule_id: str, check: str) -> str | None:
    for c in plan.acceptance.checks:
        if c.item == rule_id and c.check == check:
            return c.detail
    return None


@register_step("demo.rule_ci")
def rule_ci_step(params: Mapping[str, object], cache: ReplayCache) -> object:  # noqa: ARG001
    """Re-run Rule CI on every rule and compare with what the planner recorded."""
    fixture = fixture_of(params)
    plan = fixture.plan()
    calibrations = _calibrations(fixture)
    out = []
    for rule in plan.rules:
        report = run_rule_ci(rule, calibrations=calibrations)
        detail = report.summary() + ("; " + "; ".join(report.failures) if report.failures else "")
        out.append(
            {
                "rule_id": rule.id,
                "summary": report.summary(),
                "ci_green": report.ci_green,
                "nonvacuous": report.nonvacuous,
                "failures": list(report.failures),
                "matches_plan": _acceptance_detail(plan, rule.id, "ci_green") == detail,
            }
        )
    return out


def _candidate_ids(records: list[EventRecord]) -> list[str]:
    return [read_candidate_payload(r)[0].event_id for r in records if r.kind == "candidate"]


@register_step("demo.evidence")
def evidence_step(params: Mapping[str, object], cache: ReplayCache) -> object:  # noqa: ARG001
    """Verify the day's chain and re-derive every candidate of the run from its track window."""
    fixture = fixture_of(params)
    report = verify_chain(fixture.chain_path)
    records = fixture.chain()
    run_records = fixture.run_records()
    store = fixture.windows()
    candidates = [r for r in run_records if r.kind == "candidate"]
    reproduced = [r.seq for r in candidates if rederive(r, store)]
    return {
        "ok": report.ok,
        "n": report.n,
        "reason": report.reason,
        "root": merkle_root([r.hash for r in records]),
        "site": records[0].site if records else None,
        "run_seqs": [r.seq for r in run_records],
        "run_kinds": [r.kind for r in run_records],
        "candidates": len(candidates),
        "reproduced": len(reproduced),
        "event_ids": _candidate_ids(run_records),
    }


def _chained(records: list[EventRecord], event_id: str) -> tuple[dict[str, object] | None, ...]:
    """The chained Tier 1 verdict and decision of one candidate (each `None` when absent)."""
    tier1 = decision = None
    for r in records:
        if r.kind != "verdict" or r.payload.get("event_id") != event_id:
            continue
        if "decision" in r.payload:
            decision = r.payload["decision"]
        elif r.payload.get("tier") == "glimmer" and tier1 is None:
            tier1 = dict(r.payload)
    return (
        tier1 if isinstance(tier1, dict) else None,
        decision if isinstance(decision, dict) else None,
    )


class _NoTier1:
    """The policy's own Tier 1 is only used by a firewall re-run, which the run did not have."""

    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        msg = f"no firewall re-run in a golden replay ({candidate.event_id}, {rule.id})"
        raise FixtureError(msg)


def _verdict_view(verdict: VerifierVerdict) -> dict[str, object]:
    data = verdict.model_dump(mode="json")
    data.pop("latency_ms", None)
    return data


def _matches(view: Mapping[str, object], chained: Mapping[str, object] | None) -> bool:
    if chained is None:
        return False
    return all(chained.get(k) == v for k, v in view.items())


@register_step("demo.verify")
def verify_step(params: Mapping[str, object], cache: ReplayCache) -> object:  # noqa: ARG001
    """Replay Tier 1 on every candidate of the run, then decide as the runtime's policy did.

    Raises:
        FixtureError: If the runtime had Tier 2 or the firewall on (not replayable here yet).
    """
    fixture = fixture_of(params)
    response = fixture.response()
    if response.tier2 != "absent" or response.firewall != "degraded":
        msg = "golden replay of Tier 2 or the scene-text firewall is not supported yet"
        raise FixtureError(msg)
    policy = VerificationPolicy(tier1=_NoTier1(), screen_required=True)
    replies = replay_cache(fixture.replay_dir)
    records = fixture.run_records()
    out = []
    for record in records:
        if record.kind != "candidate":
            continue
        candidate, context = read_candidate_payload(record)
        chained_tier1, chained_decision = _chained(records, candidate.event_id)
        model = str((chained_tier1 or {}).get("model_version", "muse-glimmer-30b"))
        strips = fixture.strips(candidate.event_id)
        shown = with_strip(candidate, strips)
        tier1 = replay_tier1(shown, context.rule, model=model, cache=replies)
        decision = policy.decide(shown, context.rule, tier1)
        view = _verdict_view(tier1)
        decided = decision.model_dump(mode="json")
        out.append(
            {
                "event_id": candidate.event_id,
                "rule_id": candidate.rule_id,
                "severity": str(candidate.severity),
                "measurement": candidate.measurement.model_dump(mode="json")
                if candidate.measurement
                else None,
                "strip_frames": len(strips),
                "act_first": policy.act_first(candidate, context.rule),
                "tier1": view,
                "tier1_matches_chain": _matches(view, chained_tier1),
                "decision": decided,
                "decision_matches_chain": chained_decision is None or chained_decision == decided,
            }
        )
    return out


@register_step("demo.score")
def score_step(params: Mapping[str, object], cache: ReplayCache) -> object:  # noqa: ARG001
    """Score the run with the eval harness against its clip's labels (`eval_set`: `eval/clips`)."""
    return score_fixture(params)


def score_fixture(params: Mapping[str, object]) -> dict[str, object]:
    """The `demo.score` output: the fixture's run scored by `rulecam eval score`'s code."""
    fixture = fixture_of(params)
    evalset = load_eval_set(Path(str(params.get("eval_set", "eval/clips"))))
    (run,) = score_runs([fixture.run()], evalset, generated_at=_SCORED_AT).runs
    return {
        "clip": run.clip,
        "rule": run.rule,
        "status": run.status,
        "outcome": run.outcome,
        "candidates": run.candidates,
        "alerts": run.alerts,
        "events": run.events,
        "tp": run.tp,
        "fp": run.fp,
        "fn": run.fn,
        "tier1": list(run.tier1),
        "decisive": run.decisive,
        "false_alerts_on_negative": run.alerts if run.status == "negative" else 0,
    }
