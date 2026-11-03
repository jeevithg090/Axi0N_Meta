"""Rendering the E1 section of `eval/RESULTS.md` (task P4-06)."""

from __future__ import annotations

from pathlib import Path

import pytest
from eval.report import (
    BEGIN,
    END,
    fmt_per_hour,
    fmt_rate,
    headline_lines,
    render_e1_section,
    write_results_section,
)
from eval.runners.common import ResultError, splice_section
from eval.runners.e1 import ArmOutcome, E1Summary, aggregate_e1, rate, run_e1_evaluation
from tests.eval.test_e1_runner import SIMPLE, FixedRunner


def summary_of(*, simulated: bool = False) -> E1Summary:
    runner = FixedRunner(
        {"pos_a": (1, 0, 0, 1), "pos_b": (1, 0, 1, 1), "neg_a": (0, 1, 0, 1)}, seconds=1800.0
    )
    return run_e1_evaluation(SIMPLE, runner, arms=["predicates", "spark_open"], simulated=simulated)


def test_a_rate_prints_its_interval_and_counts() -> None:
    assert fmt_rate(rate(1, 2)) == "50.0% [9.5-90.5] (1/2)"


def test_an_untested_rate_prints_not_available_not_zero() -> None:
    """ "We never tested it" and "it never worked" must not look the same."""
    assert fmt_rate(rate(0, 0)) == "n/a (0/0)"
    assert fmt_rate(rate(0, 3)).startswith("0.0%")
    assert fmt_per_hour(None) == "n/a"
    assert fmt_per_hour(1.5) == "1.50"


def test_the_section_holds_every_table_and_the_three_numbers() -> None:
    text = render_e1_section(summary_of())
    assert text.startswith(BEGIN)
    assert text.rstrip().endswith(END)
    assert "## E1 — Detection accuracy" in text
    assert "The three numbers (MP §12)" in text
    for title in ("Overall, per arm", "Per rule", "Per use case", "By who staged the clip"):
        assert title in text
    assert "Wilson 95%" in text
    assert "predicates" in text
    assert "spark_open" in text


def test_the_section_says_repeats_measure_stability_not_accuracy() -> None:
    text = render_e1_section(summary_of())
    assert "stability" in text
    assert "never summed over repeats" in text


def test_a_simulated_summary_is_marked_in_the_markdown() -> None:
    text = render_e1_section(summary_of(simulated=True))
    assert "Simulated. These numbers are not an E1 result." in text


def test_caveats_are_printed() -> None:
    text = render_e1_section(summary_of())
    assert "**Caveats**" in text
    assert "no generalisation" in text


def test_unscored_clips_are_listed() -> None:
    outcomes = [
        ArmOutcome(arm="spark_open", run_idx=0, clip="pos_a", rule_id="forklift_2m", tp=1),
        ArmOutcome(
            arm="spark_open",
            run_idx=0,
            clip="pos_b",
            rule_id="forklift_2m",
            error="pos_b: no plan was approved",
        ),
    ]
    text = render_e1_section(aggregate_e1(SIMPLE, outcomes, runs_per_arm=1))
    assert "Clips that could not be scored" in text
    assert "pos_b: no plan was approved" in text


def test_splice_replaces_only_the_marked_section() -> None:
    text = f"# Results\n\nBefore.\n\n{BEGIN}\nold\n{END}\n\nAfter.\n"
    out = splice_section(text, "e1", f"{BEGIN}\nnew\n{END}\n")
    assert "Before." in out
    assert "After." in out
    assert "old" not in out
    assert "new" in out


def test_splice_refuses_a_file_without_markers() -> None:
    with pytest.raises(ResultError, match="needs the markers"):
        splice_section("# Results\n\nnothing here\n", "e1", f"{BEGIN}\nnew\n{END}\n")
    with pytest.raises(ResultError):
        splice_section(f"{END}\n{BEGIN}\n", "e1", f"{BEGIN}\nnew\n{END}\n")


def test_writing_the_section_keeps_the_rest_of_the_file(tmp_path: Path) -> None:
    path = tmp_path / "RESULTS.md"
    path.write_text(f"# Results\n\nKeep me.\n\n{BEGIN}\nold\n{END}\n\nKeep me too.\n")
    written = write_results_section(summary_of(), path)
    out = written.read_text(encoding="utf-8")
    assert "Keep me." in out
    assert "Keep me too." in out
    assert "old" not in out
    assert "E1 — Detection accuracy" in out


def test_writing_twice_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "RESULTS.md"
    path.write_text(f"# Results\n\n{BEGIN}\n{END}\n\ntail\n")
    summary = summary_of()
    write_results_section(summary, path)
    once = path.read_text()
    write_results_section(summary, path)
    assert path.read_text() == once


def test_writing_to_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ResultError, match="no results file"):
        write_results_section(summary_of(), tmp_path / "absent.md")


def test_headline_lines_are_three_plain_lines() -> None:
    lines = list(headline_lines(summary_of()))
    assert len(lines) == 3
    assert lines[0].startswith("recall: ")
    assert lines[2].startswith("false_alerts_per_hour: ")


def test_the_committed_results_file_holds_the_e1_markers() -> None:
    """The E1 section shares RESULTS.md with E4-E11, so all their markers must coexist."""
    results = Path(__file__).resolve().parents[2] / "eval" / "RESULTS.md"
    text = results.read_text(encoding="utf-8")
    out = splice_section(text, "e1", render_e1_section(summary_of()))
    assert out.count(BEGIN) == 1
    assert "<!-- BEGIN E4 " in out
    assert "<!-- END E11 -->" in out
