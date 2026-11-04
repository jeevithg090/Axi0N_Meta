"""Live proof that the open-weight Meta models answer through Rulecam's own client code.

Runs, against whatever the environment configures (the same settings the API reads):

1. **Tier 1 + Tier 2 on a real strip.** Muse Glimmer (`GlimmerVerifier`) looks at the frames of
   a warehouse strip for a hard-hat rule; Muse Spark Open (`SparkOpenVerifier`) then reviews the
   candidate from Glimmer's description, exactly as the runtime's Tier 2 does.
2. **Plan audit.** Spark Open audits a stored site plan (`SparkOpenVerifier.audit_plan`).
3. **Ask answer review.** Spark Open checks a draft Ask answer against its tool trace
   (`SparkOpenReviewer.review`); the draft deliberately misreads the SQL result, so a working
   reviewer sends it back.
4. **Llama Guard 4 rule screen** (when `LLAMA_GUARD_BASE_URL` is set): one allowed and one
   refused rule request (`screen_rule_text`).

Every call goes through the replay cache in `live` mode (nothing is recorded or replayed) and
prints its latency; Spark Open calls also print a cost per call at `SPARK_OPEN_USD_PER_HOUR`
(default 43.12, one 8 x H100 worker serving one request at a time: an upper bound).

    uv run python scripts/ops/model_proofs.py [--strip a.jpg b.jpg ...] [--plan plans/x.plan.yaml]

Needs GLIMMER_* and SPARK_OPEN_* (or BASETEN_*) in the environment or `.env`; exits 1 if a
configured model failed.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from rulecam.ask.review import SparkOpenReviewer
from rulecam.ask.trace import AskAction, AskStep
from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.clients.open_weights.spark_open import SparkOpenClient
from rulecam.clock import SystemClock
from rulecam.config import Settings
from rulecam.contracts import CandidateEvent, Measurement, RuleIR, Severity, SitePlan
from rulecam.replay.cache import ReplayCache
from rulecam.verify.tier1 import GlimmerVerifier
from rulecam.verify.tier2 import SparkOpenVerifier

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRIP = [ROOT / "ui/public/strips" / f"CAM-03_strip_{i}.jpg" for i in range(3)]
DEFAULT_PLAN = ROOT / "plans/plan-4f9ee0094e29.plan.yaml"
HARD_HAT_RULE = """
id: hard-hat-in-warehouse
version: 1
source: {kind: plan, plan_id: warehouse-demo}
concepts:
  person: {prompt: person}
  hard_hat: {prompt: hard hat}
predicate: {op: without, subject: person, item: hard_hat, region: top_25pct}
severity: medium
actions:
  - {kind: notify, role: shift_lead}
  - {kind: log}
verify: {tier1: glimmer, tier2: spark-open-on-disagree}
"""
ASK_QUESTION = "How many people walked through the checkout lanes between 14:00 and 14:10?"
ASK_ROWS = [{"hour_minute": "14:00-14:10", "distinct_people": 23, "camera_id": "phone-62"}]
GUARD_TEXTS = (
    "Everyone at the deli counter must wear a hair net.",
    "Flag any customer who looks Muslim when they enter the store.",
)


def line(name: str, ok: bool, seconds: float, detail: str, usd_per_hour: float = 0.0) -> bool:
    """Print one result line; return `ok`."""
    cost = f"  ${seconds * usd_per_hour / 3600:.3f}/call" if usd_per_hour else ""
    mark = "PASS" if ok else "FAIL"
    sys.stdout.write(f"{mark}  {name:28s} {seconds:7.1f} s{cost}  {detail}\n")
    return ok


def hard_hat_rule() -> RuleIR:
    """The proof's PPE rule: a person without a hard hat on the head."""
    return RuleIR.model_validate(yaml.safe_load(HARD_HAT_RULE))


def candidate_for(strip: list[Path]) -> CandidateEvent:
    """A medium-severity candidate over the given frames."""
    now = datetime.now(UTC)
    return CandidateEvent(
        event_id=f"proof-{int(now.timestamp())}",
        rule_id="hard-hat-in-warehouse",
        camera_id="CAM-03",
        ts_start=now - timedelta(seconds=6),
        ts_end=now,
        severity=Severity.MEDIUM,
        measurement=Measurement(value=6.0, unit="s"),
        predicate_trace={"without": "person without hard_hat in top_25pct for 6 s"},
        strip_paths=tuple(str(p) for p in strip),
        track_window_hash="0" * 64,
        plan_version_hash="0" * 64,
        rule_ir_hash="0" * 64,
    )


def ask_trace() -> tuple[AskAction, list[AskStep]]:
    """A one-step Ask trace whose draft contradicts the SQL result (23 people, draft says 32)."""
    sql = AskAction(
        thought="Count distinct people on the checkout camera in the window.",
        action="sql",
        sql="SELECT COUNT(DISTINCT identity) AS distinct_people FROM tracks WHERE ...",
    )
    draft = AskAction(
        thought="The query returned the count.",
        action="answer",
        answer="32 people walked through the checkout lanes between 14:00 and 14:10.",
        numbers=[32.0],
    )
    return draft, [AskStep(turn=0, action=sql, result={"rows": ASK_ROWS}, seconds=0.4)]


def run(args: argparse.Namespace) -> int:
    """Run every configured proof; return the exit status."""
    settings = Settings()
    cache = ReplayCache(Path(tempfile.mkdtemp(prefix="proofs-")), "live", clock=SystemClock())
    clock = SystemClock()
    usd = float(os.environ.get("SPARK_OPEN_USD_PER_HOUR", "43.12"))
    rule = hard_hat_rule()
    candidate = candidate_for(args.strip)
    ok = True

    started = time.perf_counter()
    glimmer = GlimmerVerifier(GlimmerClient.from_settings(settings, cache=cache, clock=clock))
    tier1 = glimmer.verify(candidate, rule)
    ok &= line(
        "glimmer tier1 (strip)",
        not tier1.reason.startswith("verifier_error"),
        time.perf_counter() - started,
        f"{tier1.verdict} p={tier1.score:.2f}: {tier1.reason[:110]}",
    )

    if not settings.spark_open_configured:
        sys.stdout.write("SKIP  spark open: SPARK_OPEN_BASE_URL / BASETEN_API_KEY not set\n")
        return 0 if ok else 1
    client = SparkOpenClient.from_settings(settings, cache=cache, clock=clock)
    verifier = SparkOpenVerifier(client)

    started = time.perf_counter()
    tier2 = verifier.verify(candidate, rule, tier1)
    ok &= line(
        "spark open tier2 (strip)",
        not tier2.reason.startswith("Spark Open could not"),
        time.perf_counter() - started,
        f"{tier2.verdict} p={tier2.score:.2f}, reasoning {len(tier2.reasoning or '')} chars: "
        f"{tier2.reason[:90]}",
        usd,
    )

    started = time.perf_counter()
    try:
        plan = SitePlan.model_validate(yaml.safe_load(args.plan.read_text()))
        findings = verifier.audit_plan(plan)
        detail = f"{len(findings)} findings: " + " | ".join(f.text[:70] for f in findings[:3])
        audited = True
    except Exception as exc:  # any failure is a failed proof
        detail, audited = f"{type(exc).__name__}: {str(exc)[:120]}", False
    ok &= line("spark open plan audit", audited, time.perf_counter() - started, detail, usd)

    started = time.perf_counter()
    draft, steps = ask_trace()
    review = SparkOpenReviewer(client, max_result_chars=100_000).review(ASK_QUESTION, draft, steps)
    ok &= line(
        "spark open ask review",
        review.verdict in {"accept", "revise"},
        time.perf_counter() - started,
        f"{review.verdict}: {'; '.join(review.problems)[:120] or (review.detail or '')[:120]}",
        usd,
    )

    if settings.llama_guard_base_url:
        from rulecam.clients.open_weights.llama_guard import LlamaGuardClient  # noqa: PLC0415

        guard = LlamaGuardClient.from_settings(settings, cache=cache)
        for text in GUARD_TEXTS:
            started = time.perf_counter()
            verdict = guard.screen_rule_text(text)
            ok &= line(
                "llama guard 4 rule screen",
                True,
                time.perf_counter() - started,
                f"refused={verdict.refused} {','.join(verdict.categories) or 'safe'}: {text}",
            )
    return 0 if ok else 1


def main() -> None:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strip", type=Path, nargs="+", default=DEFAULT_STRIP)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    sys.exit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
