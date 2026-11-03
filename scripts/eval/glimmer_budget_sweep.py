"""Replay recorded Tier 1 strips through Glimmer at several reasoning budgets (add-on 2.2).

Usage (on the API pod, or anywhere with GLIMMER_BASE_URL and its keys in .env):
    uv run python scripts/eval/glimmer_budget_sweep.py \
        --chain /workspace/data/evidence/chain/demo/2026-11-01.jsonl \
        --strips /workspace/data/strips --budgets 0,128,256,512,1024,-1 --repeats 1 \
        --labels eval/glimmer_labels.json --out reports/eval/glimmer-budget-sweep.json

    # a golden fixture instead of the live data:
    uv run python scripts/eval/glimmer_budget_sweep.py --chain scenarios/<name>/chain.jsonl \
        --strips scenarios/<name>/strips

Each candidate is sent exactly as the runtime sent it, once per budget (-1: no budget sent, the
server's own setting applies), straight to the Glimmer endpoint (live: nothing is served from the
replay cache). It writes the JSON report (settings, a summary per budget, every trial) and prints
a Markdown table. Logic and tests: `rulecam.eval.glimmer_budget`.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from rulecam.clients.open_weights.glimmer import GlimmerClient
from rulecam.config import get_settings
from rulecam.eval.glimmer_budget import (
    DEFAULT_BUDGETS,
    SweepReport,
    read_cases,
    read_labels,
    render_table,
    sweep,
)
from rulecam.replay.cache import ReplayCache


def _budgets(text: str) -> list[int]:
    return [int(part) for part in text.split(",") if part.strip()]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Read the command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--chain", type=Path, action="append", required=True, help="chain JSONL")
    parser.add_argument("--strips", type=Path, required=True, help="strip root")
    parser.add_argument(
        "--budgets", type=_budgets, default=list(DEFAULT_BUDGETS), help="comma-separated"
    )
    parser.add_argument("--repeats", type=int, default=1, help="asks per candidate and budget")
    parser.add_argument("--limit", type=int, default=0, help="first N candidates only (0: all)")
    parser.add_argument("--labels", type=Path, default=None, help="{event_id: verdict} JSON")
    parser.add_argument("--max-tokens", type=int, default=None, help="default GLIMMER_MAX_TOKENS")
    parser.add_argument("--out", type=Path, default=None, help="JSON report path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the sweep and write the report."""
    args = parse_args(argv)
    settings = get_settings()
    if args.max_tokens:
        settings = settings.model_copy(update={"glimmer_max_tokens": args.max_tokens})
    cache = ReplayCache(settings.replay_dir, "live", clock=_Utc())

    def client_for(budget: int) -> GlimmerClient:
        tuned = settings.model_copy(update={"glimmer_reasoning_budget_tokens": budget})
        return GlimmerClient.from_settings(tuned, cache=cache, clock=_Utc())

    cases, skipped = read_cases(args.chain, args.strips)
    if args.limit:
        cases = cases[: args.limit]
    summaries, trials = sweep(
        cases, args.budgets, client_for, labels=read_labels(args.labels), repeats=args.repeats
    )
    now = datetime.now(UTC)
    report = SweepReport(
        generated_at=now,
        model=settings.glimmer_model,
        max_tokens=settings.glimmer_max_tokens,
        max_tokens_ceiling=settings.glimmer_max_tokens_ceiling,
        budgets=args.budgets,
        cases=len(cases),
        skipped_no_strip=skipped,
        summaries=summaries,
        trials=trials,
    )
    out = args.out or Path("reports/eval") / f"{now:%Y-%m-%dT%H%M%SZ}-glimmer-budget-sweep.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(render_table(report))
    print(f"wrote {out}")


class _Utc:
    def now(self) -> datetime:
        return datetime.now(UTC)


if __name__ == "__main__":
    main()
