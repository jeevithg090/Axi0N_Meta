"""Glimmer reasoning-budget sweep (add-on 2.2): replay recorded strips at several budgets.

Each Tier 1 candidate in a day's evidence chain is asked again, exactly as the runtime asked it
(`verify.tier1.tier1_messages`: the same instructions, question and strip frames), once per
reasoning budget (`reasoning_budget_tokens`; -1 sends none, so the server's own setting applies).
Per budget the report gives how often Glimmer was decisive, how often it agreed with the
recorded verdict and, given labels, with the truth; latency and token counts (median and p95);
how many replies were cut at the cap and retried, and how many failed.

Strips are read from `<strips>/<camera_id>/<event_id>/N.jpg` (the API's `<data>/strips`) or
`<strips>/<event_id>/N.jpg` (a golden fixture's `strips/`). Candidates without a strip are
skipped and counted.

Labels (optional) are a JSON object `{event_id: "violation" | "no_violation"}`, e.g. from the
labelled verifier set of add-on 1.4.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from rulecam.clients.errors import ModelClientError
from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.contracts import CandidateEvent, Frozen, RuleIR
from rulecam.contracts.events import EventRecord
from rulecam.evidence.chain import read_chain
from rulecam.log import get_logger
from rulecam.rules.backtest import read_candidate_payload
from rulecam.verify.tier1 import Tier1Answer, tier1_messages

_log = get_logger(__name__)

DEFAULT_BUDGETS: Final = (0, 128, 256, 512, 1024, -1)
Label = Literal["violation", "no_violation"]


class Case(Frozen):
    """One recorded candidate to ask again.

    Attributes:
        candidate: The candidate with its strip attached.
        rule: The rule that fired.
        recorded: The runtime's Glimmer verdict, when the chain has one.
    """

    candidate: CandidateEvent
    rule: RuleIR
    recorded: str | None = None


class Trial(Frozen):
    """One candidate asked at one budget."""

    event_id: str
    budget: int
    verdict: str | None = None
    p_violation: float | None = None
    recorded: str | None = None
    label: str | None = None
    latency_ms: int | None = None
    calls: int = 0
    max_tokens: int | None = None
    finish_reason: str = ""
    usage: dict[str, float] = Field(default_factory=dict)
    reasoning_ref: str | None = None
    error: str | None = None


class BudgetSummary(Frozen):
    """Every trial at one budget, summed up."""

    budget: int
    trials: int
    errors: int
    decisive: int
    agree_recorded: int
    compared_recorded: int
    agree_label: int
    labelled: int
    retried_cut: int
    latency_ms_p50: float | None
    latency_ms_p95: float | None
    completion_tokens_p50: float | None
    reasoning_tokens_p50: float | None


class SweepReport(Frozen):
    """The sweep: settings, a summary per budget and every trial."""

    generated_at: datetime
    model: str
    max_tokens: int
    max_tokens_ceiling: int
    budgets: list[int]
    cases: int
    skipped_no_strip: int
    summaries: list[BudgetSummary]
    trials: list[Trial]


def strip_frames(strips: Path, camera_id: str, event_id: str) -> list[Path]:
    """The candidate's strip frames in order, from either strip layout (empty when none)."""
    for folder in (strips / camera_id / event_id, strips / event_id):
        frames = [p for p in folder.glob("*.jpg") if p.stem.isdigit()] if folder.is_dir() else []
        if frames:
            return sorted(frames, key=lambda p: int(p.stem))
    return []


def load_cases(records: Sequence[EventRecord], strips: Path) -> tuple[list[Case], int]:
    """The chain's candidates that have a strip on disk, and how many had none."""
    recorded = {
        str(r.payload.get("event_id")): str(r.payload.get("verdict"))
        for r in records
        if r.kind == "verdict" and r.payload.get("tier") == "glimmer"
    }
    cases, skipped = [], 0
    for record in records:
        if record.kind != "candidate":
            continue
        candidate, context = read_candidate_payload(record)
        frames = strip_frames(strips, candidate.camera_id, candidate.event_id)
        if not frames:
            skipped += 1
            continue
        shown = candidate.model_copy(update={"strip_paths": tuple(str(p) for p in frames)})
        cases.append(
            Case(candidate=shown, rule=context.rule, recorded=recorded.get(candidate.event_id))
        )
    return cases, skipped


def read_cases(chains: Sequence[Path], strips: Path) -> tuple[list[Case], int]:
    """`load_cases` over several chain files."""
    records = [r for path in chains for r in read_chain(path)]
    return load_cases(records, strips)


def read_labels(path: Path | None) -> dict[str, Label]:
    """Labels by event id (`{}` without a file).

    Raises:
        ValueError: If a label is not `violation` or `no_violation`.
        OSError: If the file cannot be read.
    """
    if path is None:
        return {}
    raw = json.loads(path.read_text("utf-8"))
    labels: dict[str, Label] = {}
    for event_id, label in dict(raw).items():
        if label not in ("violation", "no_violation"):
            msg = f"label of {event_id} must be violation or no_violation, not {label!r}"
            raise ValueError(msg)
        labels[str(event_id)] = label
    return labels


def ask_once(client: GlimmerClient, case: Case, budget: int, label: str | None) -> Trial:
    """Ask one candidate at one budget (a failure is recorded, not raised)."""
    base = Trial(
        event_id=case.candidate.event_id, budget=budget, recorded=case.recorded, label=label
    )
    try:
        reply = client.structured_reply(
            tier1_messages(case.candidate, case.rule),
            Tier1Answer,
            camera_id=case.candidate.camera_id,
        )
    except (ModelClientError, OSError, ValueError) as exc:
        _log.warning("glimmer_sweep_failed", event_id=base.event_id, budget=budget, error=repr(exc))
        return base.model_copy(update={"error": f"{type(exc).__name__}: {exc}"})
    return base.model_copy(
        update={
            "verdict": reply.answer.verdict,
            "p_violation": reply.answer.p_violation,
            "latency_ms": reply.latency_ms,
            "calls": reply.calls,
            "max_tokens": reply.max_tokens,
            "finish_reason": reply.finish_reason,
            "usage": reply.usage,
            "reasoning_ref": reply.reasoning_ref,
        }
    )


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))])


def _median(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def summarise(budget: int, trials: Sequence[Trial]) -> BudgetSummary:
    """Sum up the trials of one budget."""
    answered = [t for t in trials if t.error is None]
    latencies = [float(t.latency_ms) for t in answered if t.latency_ms is not None]
    compared = [t for t in answered if t.recorded is not None]
    labelled = [t for t in answered if t.label is not None]
    return BudgetSummary(
        budget=budget,
        trials=len(trials),
        errors=len(trials) - len(answered),
        decisive=sum(t.verdict != "unsure" for t in answered),
        agree_recorded=sum(t.verdict == t.recorded for t in compared),
        compared_recorded=len(compared),
        agree_label=sum(t.verdict == t.label for t in labelled),
        labelled=len(labelled),
        retried_cut=sum(t.calls > 1 for t in answered),
        latency_ms_p50=_median(latencies),
        latency_ms_p95=_quantile(latencies, 0.95),
        completion_tokens_p50=_median(
            [t.usage["completion_tokens"] for t in answered if "completion_tokens" in t.usage]
        ),
        reasoning_tokens_p50=_median(
            [t.usage["reasoning_tokens"] for t in answered if "reasoning_tokens" in t.usage]
        ),
    )


def sweep(
    cases: Sequence[Case],
    budgets: Sequence[int],
    client_for: Callable[[int], GlimmerClient],
    *,
    labels: Mapping[str, str] | None = None,
    repeats: int = 1,
) -> tuple[list[BudgetSummary], list[Trial]]:
    """Ask every case at every budget `repeats` times, budgets interleaved per case.

    Interleaving keeps a warming or cooling server from favouring one budget.
    """
    labels = labels or {}
    clients = {budget: client_for(budget) for budget in budgets}
    trials = [
        ask_once(clients[budget], case, budget, labels.get(case.candidate.event_id))
        for case in cases
        for _ in range(repeats)
        for budget in budgets
    ]
    summaries = [summarise(b, [t for t in trials if t.budget == b]) for b in budgets]
    return summaries, trials


def render_table(report: SweepReport) -> str:
    """A Markdown table of the summaries."""

    def cell(value: float | None) -> str:
        return "-" if value is None else f"{value:g}"

    lines = [
        f"Glimmer budget sweep, {report.model}, max_tokens {report.max_tokens} "
        f"(ceiling {report.max_tokens_ceiling}), {report.cases} strips "
        f"({report.skipped_no_strip} without a strip skipped)",
        "",
        "| budget | trials | errors | decisive | = recorded | = label | cut+retried "
        "| p50 ms | p95 ms | completion tok p50 | reasoning tok p50 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report.summaries:
        lines.append(
            f"| {'server' if s.budget < 0 else s.budget} | {s.trials} | {s.errors} | "
            f"{s.decisive} | {s.agree_recorded}/{s.compared_recorded} | "
            f"{s.agree_label}/{s.labelled} | {s.retried_cut} | {cell(s.latency_ms_p50)} | "
            f"{cell(s.latency_ms_p95)} | {cell(s.completion_tokens_p50)} | "
            f"{cell(s.reasoning_tokens_p50)} |"
        )
    return "\n".join(lines) + "\n"
