"""Add-on 1.1: README and RESULTS numbers come from result files, with run, date and SHA."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rulecam.cli import app
from rulecam.eval.errors import EvalError
from rulecam.eval.results_doc import (
    BEGIN,
    END,
    Fact,
    git_sha,
    in_result,
    load_facts,
    numbers,
    render_section,
    resolve,
    section_of,
    splice,
    table_rows,
    unbacked_numbers,
)

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"
RESULTS = REPO / "eval" / "RESULTS.md"
FACTS = REPO / "eval" / "results.yaml"
cli = CliRunner()


@pytest.mark.parametrize("doc", [README, RESULTS], ids=["README", "RESULTS"])
def test_every_shown_number_is_in_its_result_file(doc: Path) -> None:
    section = section_of(doc.read_text(encoding="utf-8"))
    assert section, f"{doc.name} has no generated results section"
    assert table_rows(section), "the results table is empty"
    assert unbacked_numbers(section, REPO) == []


@pytest.mark.parametrize("doc", [README, RESULTS], ids=["README", "RESULTS"])
def test_the_docs_show_the_current_facts(doc: Path) -> None:
    facts = load_facts(FACTS, REPO, sha=lambda path: "sha")
    shown = [
        (r["Measured"], r["Value"], r["Run"], r["Date"])
        for r in table_rows(section_of(doc.read_text(encoding="utf-8")))
    ]
    assert shown == [(f.label, f.value, f.run, f.date) for f in facts], (
        "run `uv run rulecam eval results`"
    )
    assert all(r["Git SHA"].strip("`") for r in table_rows(section_of(doc.read_text("utf-8"))))


def test_a_number_not_in_the_file_is_caught(tmp_path: Path) -> None:
    (tmp_path / "reports" / "eval").mkdir(parents=True)
    (tmp_path / "reports" / "eval" / "r.json").write_text(
        json.dumps({"a": 0.534, "b": [12, {"c": 2.900231}], "flag": True}), encoding="utf-8"
    )
    fact = Fact(
        "Thing", "53.4% and 2.90 m and 12", "run", "2026-11-01", "abc", "reports/eval/r.json"
    )
    assert unbacked_numbers(render_section([fact]), tmp_path) == []
    bad = Fact("Thing", "7.5 s", "run", "2026-11-01", "abc", "reports/eval/r.json")
    assert unbacked_numbers(render_section([bad]), tmp_path) == [
        "Thing: 7.5 is not in reports/eval/r.json"
    ]
    prose = render_section([fact], intro="We measured 99 things.")
    assert unbacked_numbers(prose, tmp_path) == [
        "number '99' outside the table: 'We measured 99 things.'"
    ]
    uncited = render_section([Fact("X", "1", "r", "d", "s", "elsewhere.json")])
    assert unbacked_numbers(uncited, tmp_path) == ["X: no result file cited"]


def test_resolve_paths() -> None:
    data = {"tests": [{"id": "T02", "events": [{"score": 0.99}]}], "n": 3}
    assert resolve(data, "tests[id=T02].events[0].score") == 0.99
    assert resolve(data, "n") == 3
    assert resolve([1, 2], "[1]") == 2
    with pytest.raises(EvalError, match="does not exist"):
        resolve(data, "tests[id=T09].events")
    with pytest.raises(EvalError, match="does not exist"):
        resolve(data, "n.x")
    with pytest.raises(EvalError, match="bad path"):
        resolve(data, "[")


def test_number_matching() -> None:
    assert numbers("416 tracks, 39 identities vs 80 visits") == ["416", "39", "80"]
    assert numbers("-1.13 and 5,301") == ["-1.13", "5301"]
    assert in_result("22.7", [0.227])
    assert in_result("2.90", [2.900231])
    assert not in_result("2.91", [2.900231])


def test_load_facts(tmp_path: Path) -> None:
    (tmp_path / "r.json").write_text(
        json.dumps({"git_sha": "feed123", "when": "2026-11-02T09:00Z", "x": 0.5, "s": "no"}),
        encoding="utf-8",
    )
    spec = tmp_path / "facts.yaml"
    spec.write_text(
        "sources: {r: {file: r.json, run: R, date: when}}\n"
        "facts:\n"
        "  - {source: r, label: Share, path: x, scale: 100, format: '{:.0f}%'}\n"
        "  - {source: r, run: A, label: Pair, path: [x, x], format: '{} / {}'}\n",
        encoding="utf-8",
    )
    first, second = load_facts(spec, tmp_path)
    assert (first.value, first.sha, first.date, first.run) == ("50%", "feed123", "2026-11-02", "R")
    assert (second.value, second.run) == ("0.5 / 0.5", "R: A")
    spec.write_text("sources: {}\nfacts: [{source: r, label: L, path: x}]\n", encoding="utf-8")
    with pytest.raises(EvalError, match="unknown source"):
        load_facts(spec, tmp_path)
    spec.write_text(
        "sources: {r: {file: r.json}}\nfacts: [{source: r, label: L, path: s}]\n", encoding="utf-8"
    )
    with pytest.raises(EvalError, match="not a number"):
        load_facts(spec, tmp_path)
    spec.write_text(
        "sources: {r: {file: gone.json}}\nfacts: [{source: r, label: L, path: s}]\n",
        encoding="utf-8",
    )
    with pytest.raises(EvalError, match="cannot read result file"):
        load_facts(spec, tmp_path)
    with pytest.raises(EvalError, match="cannot read"):
        load_facts(tmp_path / "missing.yaml", tmp_path)


def test_git_sha_outside_a_repo_is_empty(tmp_path: Path) -> None:
    assert git_sha(tmp_path / "x.json", tmp_path) == ""


def test_splice() -> None:
    text = f"top\n{BEGIN}\nold\n{END}\nbottom\n"
    section = render_section([Fact("L", "1", "r", "d", "s", "reports/eval/x.json")])
    out = splice(text, section, prefix="../")
    assert out.startswith("top\n")
    assert out.endswith("\nbottom\n")
    assert "](../reports/eval/x.json)" in out
    assert "old" not in out
    with pytest.raises(EvalError, match="markers"):
        splice("no markers", section)
    assert section_of("nothing") == ""


def test_cli_results_writes_and_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO)
    readme, results = tmp_path / "README.md", tmp_path / "RESULTS.md"
    for path in (readme, results):
        path.write_text(f"# x\n{BEGIN}\n{END}\n", encoding="utf-8")
    args = ["eval", "results", "--readme", str(readme), "--results", str(results)]
    stale = cli.invoke(app, [*args, "--check"])
    assert stale.exit_code == 1
    written = cli.invoke(app, args)
    assert written.exit_code == 0, written.output
    assert "](../reports/eval/" in results.read_text(encoding="utf-8")
    assert cli.invoke(app, [*args, "--check"]).exit_code == 0
    bad = cli.invoke(app, [*args, "--facts", str(tmp_path / "none.yaml")])
    assert bad.exit_code == 2
