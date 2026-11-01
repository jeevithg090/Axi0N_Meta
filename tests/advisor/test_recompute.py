"""Tests for Glimmer's independent recompute of advice numbers (P3-10)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from rulecam.advisor.recompute import (
    Recomputer,
    UnsafeSqlError,
    check_sql,
    matches,
    open_read_only,
    with_recompute,
)
from tests.advisor.card_helpers import (
    TURN_SQL,
    WAIT_SQL,
    FakeSqlWriter,
    make_card,
    make_db,
    numbers,
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return make_db(tmp_path / "advisor.duckdb")


def test_matching_numbers_are_reproduced(db: Path) -> None:
    writer = FakeSqlWriter(
        {"dock_wait_for_forklift_min": WAIT_SQL, "dock_turnaround_min": TURN_SQL}
    )
    report = Recomputer(writer, db).check_card(make_card(), numbers())
    assert (report.reproduced, report.total, report.flagged) == (2, 2, False)
    assert report.checks[0].recomputed == pytest.approx(14.0)
    assert report.checks[0].sql == WAIT_SQL
    card = with_recompute(make_card(), report)
    assert (card.numbers_total, card.numbers_reproduced) == (2, 2)


def test_glimmer_only_sees_the_definition_not_the_value(db: Path) -> None:
    writer = FakeSqlWriter(
        {"dock_wait_for_forklift_min": WAIT_SQL, "dock_turnaround_min": TURN_SQL}
    )
    Recomputer(writer, db).check_card(make_card(), numbers())
    prompt = writer.prompts[0]
    assert "Mean minutes a truck waits" in prompt
    assert "14.0" not in prompt
    assert "SELECT" not in prompt


def test_a_mismatch_hides_the_number_and_flags_the_card(db: Path) -> None:
    wrong = "SELECT avg(turnaround_s) / 60 FROM cases WHERE station = 'dock_3'"
    writer = FakeSqlWriter({"dock_wait_for_forklift_min": wrong, "dock_turnaround_min": TURN_SQL})
    report = Recomputer(writer, db).check_card(make_card(), numbers())
    assert report.flagged
    assert report.hidden == frozenset({"mr_wait"})
    assert report.checks[0].error == "value differs from the catalogue"
    assert with_recompute(make_card(), report).numbers_reproduced == 1


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM cases", "SELECT"),
        ("SELECT count(*) FROM tracks", "not allowed"),
        ("SELECT 1; DROP TABLE cases", "SELECT"),
        ("SELECT 42", "no table"),
        ("SELECT * FROM read_csv('/etc/passwd')", "no table"),
    ],
)
def test_unsafe_sql_is_rejected_and_counts_as_not_reproduced(
    db: Path, sql: str, reason: str
) -> None:
    with open_read_only(db) as con, pytest.raises(UnsafeSqlError, match=reason):
        check_sql(con, sql)
    writer = FakeSqlWriter({"dock_wait_for_forklift_min": sql, "dock_turnaround_min": TURN_SQL})
    report = Recomputer(writer, db).check_card(make_card(), numbers())
    assert not report.checks[0].ok


def test_a_write_on_the_read_only_connection_errors(db: Path) -> None:
    with open_read_only(db) as con:
        with pytest.raises(duckdb.Error):
            con.execute("INSERT INTO cases (case_id) VALUES ('x')")
        with pytest.raises(duckdb.Error):
            con.execute("SELECT * FROM read_csv('/etc/hostname')").fetchall()


def test_non_scalar_results_and_writer_failures_are_not_reproduced(db: Path) -> None:
    multi = "SELECT wait_s FROM cases"
    text = "SELECT station FROM cases WHERE case_id = 'c1'"
    for sql in (multi, text):
        writer = FakeSqlWriter({"dock_wait_for_forklift_min": sql, "dock_turnaround_min": TURN_SQL})
        assert not Recomputer(writer, db).check_card(make_card(), numbers()).checks[0].ok
    silent = FakeSqlWriter({})
    report = Recomputer(silent, db).check_card(make_card(), numbers())
    assert report.reproduced == 0
    assert "ValueError" in report.checks[0].error


def test_a_slot_without_a_catalogue_result_is_not_reproduced(db: Path) -> None:
    writer = FakeSqlWriter({"dock_wait_for_forklift_min": WAIT_SQL})
    report = Recomputer(writer, db).check_card(make_card(), numbers()[:1])
    assert [c.ok for c in report.checks] == [True, False]
    assert report.checks[1].error == "no catalogue result for this slot"


def test_tolerance() -> None:
    assert matches(14.0, 14.1)
    assert not matches(14.0, 14.5)
    assert matches(0.0, 1e-9)
