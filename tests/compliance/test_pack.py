"""Proof packs: Merkle proofs, every recipe, disclosure, rules, summaries and the UI fixture."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rulecam.compliance.build import BuildInputs, build_pack, period_for
from rulecam.compliance.engine import (
    Located,
    MetricSpec,
    Period,
    Shift,
    evaluate,
    get_path,
    shift_of,
)
from rulecam.compliance.models import PackRequest, ProofPack
from rulecam.compliance.narrate import Draft, format_value, render, summarise
from rulecam.compliance.proofs import day_root, fold, inclusion_proofs, pack_hash
from rulecam.compliance.rules import ClauseFinder
from rulecam.evidence.merkle import merkle_root
from rulecam.planner.pipeline import PlanStore
from rulecam.planner.standards import Citation, CitationCheck
from tests.compliance.conftest import write_synthetic
from tests.fakes import FakeLLM

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "ui"
    / "src"
    / "app"
    / "inspector"
    / "_lib"
    / "fixture.json"
)
FIXED = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)


def _inputs(root: Path, tmp_path: Path, **extra: object) -> BuildInputs:
    ids = iter(f"pp-{i:012x}" for i in range(1, 100))
    return BuildInputs(
        chain_root=root,
        site="demo",
        plans=PlanStore(tmp_path / "plans"),
        roots_dir=tmp_path / "roots",
        clock=lambda: FIXED,
        new_id=lambda: next(ids),
        **extra,  # type: ignore[arg-type]
    )


def _day(start: str, end: str) -> PackRequest:
    return PackRequest(
        start=datetime.fromisoformat(start), end=datetime.fromisoformat(end), models=False
    )


@pytest.mark.parametrize("n", range(1, 10))
def test_inclusion_proofs_fold_to_the_day_root(n: int) -> None:
    leaves = [f"{i:064x}" for i in range(n)]
    root = merkle_root(leaves)
    assert day_root(leaves) == root
    for leaf, proof in zip(leaves, inclusion_proofs(leaves), strict=True):
        assert fold(leaf, proof) == root
    assert fold("f" * 64, inclusion_proofs(leaves)[0]) != root


def test_golden_chain_pack_counts_and_proofs(golden_chain: Path, tmp_path: Path) -> None:
    pack = build_pack(
        _day("2026-11-01T00:00:00+00:00", "2026-11-02T00:00:00+00:00"),
        _inputs(golden_chain, tmp_path),
    )
    metrics = {m.id: m for m in pack.metrics}
    assert metrics["alerts_raised"].value == 6
    violations = sum(
        1
        for r in pack.records
        if r.record["kind"] == "verdict" and get_path(r.record, "payload.verdict") == "violation"
    )
    assert metrics["glimmer_verified"].value == violations
    assert metrics["actions_dry_run"].value == metrics["actions"].value == 6
    assert metrics["time_to_action_median"].value is not None
    assert pack.days[0].chain_ok
    assert pack.days[0].n == 35
    assert len(pack.records) == 35  # the whole day is inside the window
    for item in pack.records:
        assert fold(str(item.record["hash"]), item.proof) == pack.days[0].root
    assert any("dry-run" in n for n in pack.notes)
    assert any("BLIND time is unknown" in n for n in pack.notes)
    assert {r.rule_id for r in pack.rules} == {"keep-people-2m-from-moving-forklift"}
    assert pack.rules[0].gate == "pass"
    assert pack.rules[0].distance_rule


def test_partial_window_discloses_its_edges(golden_chain: Path, tmp_path: Path) -> None:
    pack = build_pack(
        _day("2026-11-01T01:40:00+00:00", "2026-11-01T01:56:00+00:00"),
        _inputs(golden_chain, tmp_path),
    )
    day = pack.days[0]
    keys = {r.key for r in pack.records}
    assert day.window_first is not None
    assert day.window_last is not None
    assert all(f"2026-11-01#{s}" in keys for s in range(day.window_first, day.window_last + 1))
    assert f"2026-11-01#{day.window_first - 1}" in keys
    assert f"2026-11-01#{day.window_last + 1}" in keys
    assert "2026-11-01#30" not in keys  # outside, and not linked to an event in the window


def test_synthetic_recipes(synthetic_chain: Path, tmp_path: Path) -> None:
    pack = build_pack(
        _day("2026-11-02T00:00:00+00:00", "2026-11-03T00:00:00+00:00"),
        _inputs(synthetic_chain, tmp_path),
    )
    m = {x.id: x.value for x in pack.metrics}
    assert m["alerts_raised"] == 2
    assert m["glimmer_verified"] == 1
    assert m["glimmer_rejected"] == 1
    assert m["time_to_verdict_median"] == 5.0  # lower median of 20 s and 5 s
    assert m["time_to_ack_median"] == 60.0
    assert m["time_to_clear_median"] == 300.0
    assert m["disputes_filed"] == 1
    assert m["disputes_upheld"] == 1
    assert m["settings_applied"] == 2
    assert m["settings_without_both_keys"] == 1  # p-2 was applied with no keys at all
    assert m["camera:cam-a:blind_s"] == 1800.0
    assert m["shift:early:alerts_raised"] == 1
    assert m["shift:late:alerts_raised"] == 1
    assert m["rule:spill:glimmer_verified"] == 1
    gates = {r.rule_id: (r.gate, r.gate_reasons) for r in pack.rules}
    assert gates["dock"] == ("amber", ("ci_green failed",))
    assert not any("No camera coverage" in n for n in pack.notes)
    assert len(pack.incidents) == 1
    assert pack.incidents[0].event_id == "ev-1"


def test_shift_wraps_and_uses_the_site_zone() -> None:
    shifts = (Shift(name="day", start_hour=6), Shift(name="night", start_hour=18))
    assert shift_of("2026-11-02T03:00:00Z", "UTC", shifts) == "night"
    assert shift_of("2026-11-02T07:00:00Z", "UTC", shifts) == "day"
    assert shift_of("2026-11-02T03:00:00Z", "Asia/Kolkata", shifts) == "day"  # 08:30 IST


def test_period_rejects_reversed_windows() -> None:
    with pytest.raises(ValueError, match="end after"):
        period_for(_day("2026-11-02T00:00:00+00:00", "2026-11-01T00:00:00+00:00"), "UTC")


def test_count_respects_prefix_and_previous_window() -> None:
    period = Period(
        start=datetime(2026, 11, 2, tzinfo=UTC),
        end=datetime(2026, 11, 3, tzinfo=UTC),
        previous_start=datetime(2026, 11, 1, tzinfo=UTC),
        previous_end=datetime(2026, 11, 2, tzinfo=UTC),
    )
    records = [
        Located(
            "2026-11-01#1",
            "2026-11-01",
            {"kind": "action", "ts": "2026-11-01T05:00:00Z", "payload": {"detail": "dry-run: x"}},
        ),
        Located(
            "2026-11-02#1",
            "2026-11-02",
            {"kind": "action", "ts": "2026-11-02T05:00:00Z", "payload": {"detail": "sent"}},
        ),
    ]
    spec = MetricSpec(op="count", where={"kind": "action"}, prefix={"payload.detail": "dry-run"})
    assert evaluate(spec, records, period).value == 0
    assert evaluate(spec, records, period, "previous").refs == ("2026-11-01#1",)


def test_summaries_carry_only_pack_numbers(golden_chain: Path, tmp_path: Path) -> None:
    pack = build_pack(
        _day("2026-11-01T00:00:00+00:00", "2026-11-02T00:00:00+00:00"),
        _inputs(golden_chain, tmp_path),
    )
    by_id = {m.id: m for m in pack.metrics}
    assert render("{alerts_raised} alerts, {alerts_raised.previous} before.", by_id) == (
        "6 alerts, 0 before."
    )
    assert render("There were 6 alerts.", by_id) is None
    assert render("{nope} alerts.", by_id) is None
    fake = FakeLLM(
        [
            Draft(
                sentences=[
                    "The rules raised {alerts_raised} alerts.",
                    "Glimmer confirmed 3 of them.",
                    "Median response was {time_to_action_median}.",
                ]
            )
        ]
    )
    summary = summarise(fake, "worker_rep", "pl", {"metrics": []}, pack.metrics)
    assert summary.text == (
        "The rules raised 6 alerts.",
        f"Median response was {format_value(by_id['time_to_action_median'].value, 'seconds')}.",
    )
    assert summary.dropped == 1
    assert summary.lang == "pl"
    assert '"write_in_language": "pl"' in str(fake.calls[0]["messages"])


class _Grounder:
    def __init__(self, citation: Citation | None) -> None:
        self.citation = citation
        self.calls = 0

    def structured(self, **kwargs: object) -> object:
        self.calls += 1
        schema = kwargs["schema"]
        return schema.model_validate({"citation": self.citation})  # type: ignore[attr-defined]


def test_clause_finder_checks_and_caches(golden_chain: Path, tmp_path: Path) -> None:
    citation = Citation(
        ref="29 CFR 1910.178(n)(4)",
        url="https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.178",
        quoted_text="The driver shall be required to slow down and sound the horn.",
    )
    spark = _Grounder(citation)
    finder = ClauseFinder(
        spark,  # type: ignore[arg-type]
        lambda c: CitationCheck(citation=c, matched=True, detail="quote found on the cited page"),
        tmp_path / "clauses.json",
    )
    request = PackRequest(
        start=datetime(2026, 11, 1, tzinfo=UTC), end=datetime(2026, 11, 2, tzinfo=UTC)
    )
    pack = build_pack(request, _inputs(golden_chain, tmp_path, clauses=finder))
    clause = pack.rules[0].clause
    assert clause is not None
    assert clause.checked
    assert clause.source == "muse-spark-1.3"
    build_pack(request, _inputs(golden_chain, tmp_path, clauses=finder))
    assert spark.calls == 1  # the second pack reads the cache


def _fixture_pack(pack: ProofPack) -> dict[str, object]:
    body = pack.model_dump(mode="json")
    return {"pack": body, "pack_hash": pack_hash(body), "expires_at": "2026-11-05T00:00:00Z"}


def test_ui_fixture_is_current(golden_chain: Path, tmp_path: Path) -> None:
    """The browser's tests check this file; rebuild it with UPDATE_PROOF_FIXTURE=1."""
    golden = build_pack(
        _day("2026-11-01T01:40:00+00:00", "2026-11-01T02:31:00+00:00"),
        _inputs(golden_chain, tmp_path),
    )
    synthetic_root = write_synthetic(tmp_path / "synthetic")
    synthetic = build_pack(
        _day("2026-11-02T00:00:00+00:00", "2026-11-03T00:00:00+00:00"),
        _inputs(synthetic_root, tmp_path),
    )
    fake = FakeLLM([Draft(sentences=["Rules raised {alerts_raised} alerts in this period."])])
    synthetic = synthetic.model_copy(
        update={
            "summaries": (summarise(fake, "inspector", "en", {"metrics": []}, synthetic.metrics),)
        }
    )
    data = {"golden": _fixture_pack(golden), "synthetic": _fixture_pack(synthetic)}
    text = json.dumps(data, indent=1, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_PROOF_FIXTURE") == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(text)
