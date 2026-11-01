"""Render E1 results as the Markdown tables of `eval/RESULTS.md` (task P4-06, MP §8 and §12).

Every number here is printed with its Wilson interval and the counts behind it, so a reader can
see how thin the evidence is without opening the JSON. A cell with no trials prints as `n/a`
rather than `0`, because "we never tested it" and "it never worked" are different claims.

`render_e1_section` returns Markdown; `write_results_section` splices it into `eval/RESULTS.md`
between the E1 markers with the splice every runner shares (`eval.runners.common`), so the rest
of that file (other runners, hand-written notes) is untouched.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

from eval.runners.common import ResultError, begin, end, splice_section
from eval.runners.e1 import ArmScore, E1Summary, Rate

RUNNER_ID: Final = "e1"
BEGIN: Final = begin(RUNNER_ID)
END: Final = end(RUNNER_ID)
PERCENT: Final = 100.0


def fmt_rate(value: Rate) -> str:
    """Format a rate as `62.5% [38.6-81.5] (10/16)`, or `n/a (0/0)` with no trials."""
    if not value.known or value.value is None:
        return f"n/a ({value.successes}/{value.total})"
    return (
        f"{value.value * PERCENT:.1f}% "
        f"[{value.lo * PERCENT:.1f}-{value.hi * PERCENT:.1f}] "
        f"({value.successes}/{value.total})"
    )


def fmt_per_hour(value: float | None) -> str:
    """Format a per-hour rate, or `n/a` when there were no negative clips to measure it on."""
    return "n/a" if value is None else f"{value:.2f}"


def _row(score: ArmScore, *, label: str) -> str:
    stability = "yes" if score.stable else "**no**"
    return (
        f"| {label} | {score.arm} | {score.clips} | {score.runs} | "
        f"{fmt_rate(score.precision)} | {fmt_rate(score.recall)} | "
        f"{fmt_per_hour(score.false_alerts_per_hour)} | {stability} |"
    )


def _table(title: str, scores: Sequence[ArmScore]) -> list[str]:
    if not scores:
        return []
    lines = [
        f"**{title}**",
        "",
        "| Slice | Arm | Clips | Runs | Precision (Wilson 95%) | Recall (Wilson 95%) "
        "| False alerts/h | Stable |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines.extend(_row(s, label=s.slice_id) for s in scores)
    lines.append("")
    return lines


def _headline(summary: E1Summary) -> list[str]:
    numbers = summary.headline()
    recall, precision = numbers["recall"], numbers["precision"]
    per_hour = numbers["false_alerts_per_hour"]
    full = summary.score("spark_open", "all", "all")
    if full is None:
        return ["The three numbers for the video are not available: the full arm did not run.", ""]
    return [
        "**The three numbers (MP §12), full pipeline:**",
        "",
        f"1. Recall: {fmt_rate(full.recall)}" if recall is not None else "1. Recall: n/a",
        f"2. Precision: {fmt_rate(full.precision)}"
        if precision is not None
        else "2. Precision: n/a",
        f"3. False alerts per hour on negative clips: {fmt_per_hour(per_hour)}",
        "",
    ]


def render_e1_section(summary: E1Summary) -> str:
    """Render the whole E1 section, markers included.

    Args:
        summary: The aggregated E1 result.

    Returns:
        Markdown, ending in a newline.
    """
    lines: list[str] = [BEGIN, "", "## E1 — Detection accuracy", ""]
    if summary.simulated:
        lines += [
            "> **Simulated. These numbers are not an E1 result.** At least one arm's outcomes "
            "did not come from the real pipeline; the table exists to exercise the harness.",
            "",
        ]
    lines += [
        f"Manifest `{summary.manifest}`, {summary.runs_per_arm} runs per arm. Intervals are "
        "Wilson 95% over labelled episodes: they account for how few clips there are and for "
        "nothing else. Repeats measure **stability**, not accuracy, so counts come from the "
        "first scored run of each clip and are never summed over repeats.",
        "",
    ]
    lines += _headline(summary)
    lines += _table("Overall, per arm", summary.overall)
    lines += _table("Per rule", summary.by_rule)
    lines += _table("Per use case", summary.by_use_case)
    lines += _table(
        "By who staged the clip (outsider clips are the only generalisation evidence)",
        summary.by_staged_by,
    )
    if summary.warnings:
        lines += ["**Caveats**", ""]
        lines += [f"- {w}" for w in summary.warnings]
        lines += [""]
    errors = sorted({e for s in summary.overall for e in s.errors})
    if errors:
        lines += ["**Clips that could not be scored**", ""]
        lines += [f"- {e}" for e in errors]
        lines += [""]
    lines += [END, ""]
    return "\n".join(lines)


def write_results_section(summary: E1Summary, path: Path) -> Path:
    """Write the E1 section into `eval/RESULTS.md` between its markers.

    Args:
        summary: The aggregated E1 result.
        path: The results file. It must already hold the two markers.

    Returns:
        The path written.

    Raises:
        ResultError: If the file is missing or has no markers.
    """
    if not path.is_file():
        msg = f"no results file at {path}"
        raise ResultError(msg)
    current = path.read_text(encoding="utf-8")
    updated = splice_section(current, RUNNER_ID, render_e1_section(summary))
    path.write_text(updated, encoding="utf-8")
    return path


def headline_lines(summary: E1Summary) -> Iterable[str]:
    """Yield the three video numbers as one line each, for a terminal or a slide."""
    numbers = summary.headline()
    for key in ("recall", "precision", "false_alerts_per_hour"):
        value = numbers[key]
        if value is None:
            yield f"{key}: n/a"
        elif key == "false_alerts_per_hour":
            yield f"{key}: {value:.2f}"
        else:
            yield f"{key}: {value * PERCENT:.1f}%"
