"""The shared runner result shape: JSON, tables and the marker splice (task P4-07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eval.runners.common import (
    ResultError,
    Row,
    RunnerResult,
    begin,
    end,
    not_measured,
    percent,
    plural,
    publish,
    render,
    splice,
    write_json,
    write_section,
)


def result(**over: object) -> RunnerResult:
    base: dict[str, object] = {
        "id": "e4",
        "title": "Does Rule CI catch bugs?",
        "rows": (Row(name="Mutation catch rate", value="90.0% (9/10)", detail="3 rules"),),
    }
    return RunnerResult.model_validate(base | over)


def results_file(tmp_path: Path, *ids: str) -> Path:
    path = tmp_path / "RESULTS.md"
    body = "# Results\n\nKeep me.\n\n"
    for runner_id in ids:
        body += f"{begin(runner_id)}\n{end(runner_id)}\n\n"
    path.write_text(body + "Keep me too.\n", encoding="utf-8")
    return path


def test_percent_formats_a_count_and_says_n_a_with_no_trials() -> None:
    assert percent(9, 10) == "90.0% (9/10)"
    assert percent(0, 0) == "n/a (0/0)"
    assert percent(0, 4) == "0.0% (0/4)"


def test_plural_agrees_with_its_count() -> None:
    assert plural(1, "rule") == "1 rule"
    assert plural(3, "rule") == "3 rules"
    assert plural(0, "rule") == "0 rules"
    assert plural(1, "paired clip/rule pair") == "1 paired clip/rule pair"


def test_a_runner_with_nothing_to_measure_still_produces_a_result() -> None:
    """ "Not measured" is a result: RESULTS.md must say so, not stay quiet."""
    got = not_measured("e8", "Public dataset", "the terms were not accepted")
    assert not got.measured
    assert got.rows == ()
    assert got.caveats == ("Not measured: the terms were not accepted",)
    assert "No figure was produced." in render(got)


def test_a_table_holds_every_row_and_its_caveats() -> None:
    text = render(result(caveats=("Only our own clips.",)))
    assert text.startswith(begin("e4"))
    assert text.rstrip().endswith(end("e4"))
    assert "## E4 — Does Rule CI catch bugs?" in text
    assert "| Mutation catch rate | 90.0% (9/10) | 3 rules |" in text
    assert "- Only our own clips." in text


def test_a_simulated_result_is_marked_in_bold() -> None:
    text = render(result(simulated=True, simulated_because="the runner did not call the Planner."))
    assert "**Simulated. This is not an E4 result.**" in text
    assert "the runner did not call the Planner." in text


def test_an_id_that_is_not_a_runner_id_is_refused() -> None:
    with pytest.raises(ValueError, match="pattern"):
        result(id="not-a-runner")


def test_writing_json_is_sorted_and_reloadable(tmp_path: Path) -> None:
    path = write_json(result(), results_dir=tmp_path)
    assert path.name == "e4.json"
    loaded = json.loads(path.read_text())
    assert loaded["id"] == "e4"
    assert loaded["rows"][0]["name"] == "Mutation catch rate"
    assert list(loaded) == sorted(loaded)  # sorted keys, so a diff is readable


def test_splice_replaces_only_the_runner_s_own_section(tmp_path: Path) -> None:
    path = results_file(tmp_path, "e4", "e7")
    text = splice(path.read_text(), result())
    assert "Keep me." in text
    assert "Keep me too." in text
    assert begin("e7") in text  # another runner's section is untouched
    assert "Mutation catch rate" in text


def test_splice_refuses_a_file_without_the_markers(tmp_path: Path) -> None:
    with pytest.raises(ResultError, match="needs the markers"):
        splice("# Results\n", result())
    out_of_order = f"{end('e4')}\n{begin('e4')}\n"
    with pytest.raises(ResultError):
        splice(out_of_order, result())


def test_writing_a_section_keeps_the_rest_of_the_file(tmp_path: Path) -> None:
    path = results_file(tmp_path, "e4")
    write_section(result(), path)
    out = path.read_text(encoding="utf-8")
    assert "Keep me." in out
    assert "Keep me too." in out
    assert "## E4 —" in out


def test_writing_a_section_twice_is_idempotent(tmp_path: Path) -> None:
    path = results_file(tmp_path, "e4")
    write_section(result(), path)
    once = path.read_text()
    write_section(result(), path)
    assert path.read_text() == once


def test_writing_to_a_missing_results_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ResultError, match="no results file"):
        write_section(result(), tmp_path / "absent.md")


def test_publish_writes_every_runner(tmp_path: Path) -> None:
    path = results_file(tmp_path, "e4", "e7")
    written = publish(
        [result(), result(id="e7", title="Cost")],
        results_path=path,
        results_dir=tmp_path / "results",
    )
    assert [p.name for p in written] == ["e4.json", "e7.json"]
    out = path.read_text(encoding="utf-8")
    assert "## E4 —" in out
    assert "## E7 —" in out


def test_publish_writes_nothing_if_any_runner_s_markers_are_missing(tmp_path: Path) -> None:
    """A half-updated RESULTS.md is worse than one that was not updated."""
    path = results_file(tmp_path, "e4")  # no e7 markers
    before = path.read_text()
    out_dir = tmp_path / "results"
    with pytest.raises(ResultError, match="E7"):
        publish([result(), result(id="e7", title="Cost")], results_path=path, results_dir=out_dir)
    assert path.read_text() == before
    assert not out_dir.exists()


def test_publish_refuses_a_missing_results_file(tmp_path: Path) -> None:
    with pytest.raises(ResultError, match="no results file"):
        publish([result()], results_path=tmp_path / "absent.md", results_dir=tmp_path)
