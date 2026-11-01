"""Tests for pre-registration and sealed causes (P3-10)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rulecam.advisor.ledger import (
    AdviceLedger,
    PreregistrationError,
    plan_hash,
    preregistered_first,
)
from rulecam.advisor.seal import SealBox, SealError, seal_hash, seal_ids, seal_status
from rulecam.cli import app
from rulecam.config import Settings
from rulecam.contracts import EventRecord, Verdict
from rulecam.evidence.chain import EventChain, read_chain, verify_chain
from tests.advisor.card_helpers import make_card, make_plan
from tests.fakes import FakeClock


def chain(tmp_path: Path) -> EventChain:
    return EventChain(tmp_path / "chain", "site", clock=FakeClock())


def records(c: EventChain) -> list[EventRecord]:
    return read_chain(c.path_for(FakeClock().now().date()))


def test_accept_preregisters_before_measuring(tmp_path: Path) -> None:
    c = chain(tmp_path)
    ledger = AdviceLedger(c)
    card, plan, record = ledger.accept(make_card(), make_plan())
    assert card.status == "measuring"
    assert card.measurement_plan_id == "mp-0007"
    assert plan.preregistration_hash == plan_hash(plan) != ""
    assert record.kind == "measurement_plan"
    block = ledger.record_block(plan, {"pair": 1, "on": True, "wait_min": 6.0})
    verdict = ledger.record_verdict(
        Verdict(
            plan_id="mp-0007", label="worked", effect=-9, ci=(-14, -4), p_value=0.031, pairs_done=6
        )
    )
    assert record.seq < block.seq < verdict.seq
    assert preregistered_first(records(c), "mp-0007")
    assert verify_chain(c.path_for(FakeClock().now().date())).ok


def test_preregistration_is_refused_for_the_wrong_card_or_status(tmp_path: Path) -> None:
    ledger = AdviceLedger(chain(tmp_path))
    with pytest.raises(PreregistrationError, match="is for"):
        ledger.accept(make_card(), make_plan("rec-other"))
    with pytest.raises(PreregistrationError, match="already measuring"):
        ledger.accept(make_card("measuring"), make_plan())


def test_a_block_before_preregistration_fails_the_order_check(tmp_path: Path) -> None:
    c = chain(tmp_path)
    ledger = AdviceLedger(c)
    ledger.record_block(make_plan(), {"pair": 1})
    ledger.accept(make_card(), make_plan())
    assert not preregistered_first(records(c), "mp-0007")
    assert not preregistered_first(records(c), "mp-unknown")


def test_a_changed_plan_no_longer_matches_its_hash(tmp_path: Path) -> None:
    c = chain(tmp_path)
    _, plan, record = AdviceLedger(c).accept(make_card(), make_plan())
    tampered = plan.model_copy(update={"n_pairs": 3})
    assert plan_hash(tampered) != plan.preregistration_hash
    forged = record.model_copy(
        update={"payload": {**record.payload, "plan": tampered.model_dump(mode="json")}}
    )
    assert not preregistered_first([forged], "mp-0007")


def test_seal_then_reveal_verifies(tmp_path: Path) -> None:
    c = chain(tmp_path)
    box = SealBox(c, tmp_path / "seals")
    cause = "13:00 replenishment takes both forklifts"
    seal_id, sealed = box.seal(cause)
    chain_text = c.path_for(FakeClock().now().date()).read_text()
    assert cause not in chain_text
    assert seal_status(records(c), seal_id).model_dump() == {
        "seal_id": seal_id,
        "sealed": True,
        "revealed": False,
        "matches": None,
        "cause": None,
    }
    secret = tmp_path / "seals" / f"{seal_id}.json"
    assert secret.stat().st_mode & 0o077 == 0
    box.reveal(seal_id)
    status = seal_status(records(c), seal_id)
    assert (status.revealed, status.matches, status.cause) == (True, True, cause)
    assert seal_ids(records(c)) == [seal_id]
    assert sealed.payload["hash"] == seal_hash(cause, json.loads(secret.read_text())["salt"])


def test_a_wrong_salt_or_cause_does_not_verify(tmp_path: Path) -> None:
    c = chain(tmp_path)
    seal_id, _ = SealBox(c, tmp_path / "seals").seal("cause A")
    c.append(
        "sealed_cause", {"seal_id": seal_id, "revealed": True, "cause": "cause A", "salt": "00"}
    )
    assert seal_status(records(c), seal_id).matches is False
    assert seal_hash("cause A", "s1") != seal_hash("cause B", "s1")


def test_seal_errors(tmp_path: Path) -> None:
    box = SealBox(chain(tmp_path), tmp_path / "seals")
    with pytest.raises(SealError):
        box.seal("   ")
    with pytest.raises(SealError, match="invalid seal id"):
        box.reveal("../../etc")
    with pytest.raises(SealError, match="no local secret"):
        box.reveal("seal_abc123")
    assert seal_status([], "seal_x").sealed is False


def test_cli_seal_reveal_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "rulecam.cli.get_settings", lambda: Settings(_env_file=None, rulecam_data_dir=tmp_path)
    )
    runner = CliRunner()
    sealed = runner.invoke(app, ["advisor", "seal", "both forklifts at 13:00", "--site", "s"])
    assert sealed.exit_code == 0, sealed.output
    match = re.search(r"sealed (seal_[0-9a-f]+)", sealed.output)
    assert match is not None, sealed.output
    seal_id = match.group(1)
    revealed = runner.invoke(app, ["advisor", "reveal", seal_id, "--site", "s"])
    assert revealed.exit_code == 0, revealed.output
    day = next((tmp_path / "evidence" / "chain" / "s").glob("*.jsonl")).stem
    status = runner.invoke(app, ["advisor", "seal-status", day, "--site", "s"])
    assert '"matches": true' in status.output
