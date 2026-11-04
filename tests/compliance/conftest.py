"""Chains for the proof-portal tests.

`golden_chain` copies the recorded 1 Nov chain of the `warehouse_driver` golden run (a real live
run). `synthetic_chain` is **simulated**: records written through the real `EventChain` with a
fake clock, to exercise coverage, acknowledgements, disputes, two-key settings and hazard clears,
which no recorded chain holds yet.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rulecam.evidence.chain import EventChain
from tests.fakes import FakeClock

GOLDEN = Path(__file__).resolve().parents[2] / "scenarios" / "warehouse_driver" / "chain.jsonl"
SYNTH_DAY = datetime(2026, 11, 2, tzinfo=UTC)


@pytest.fixture
def golden_chain(tmp_path: Path) -> Path:
    """`<tmp>/chain/demo/2026-11-01.jsonl`, the golden run's chain."""
    folder = tmp_path / "chain" / "demo"
    folder.mkdir(parents=True)
    shutil.copy(GOLDEN, folder / "2026-11-01.jsonl")
    return tmp_path / "chain"


def _candidate(event: str, rule: str, camera: str) -> dict[str, object]:
    return {
        "candidate": {
            "event_id": event,
            "rule_id": rule,
            "camera_id": camera,
            "severity": "high",
            "strip_paths": [],
        },
        "context": {"rule": {"id": rule, "source": {"text": f"Rule {rule}", "standard": []}}},
    }


def write_synthetic(root: Path) -> Path:
    """A simulated day exercising every recipe; returns the chain root."""
    clock = FakeClock(SYNTH_DAY.replace(hour=7))
    chain = EventChain(root, "demo", clock=clock)

    def at(seconds: float) -> None:
        clock.advance(seconds)

    chain.append(
        "plan",
        {
            "event": "plan_approved",
            "plan_id": "plan-000000000001",
            "rules": ["spill", "dock"],
            "gates": [
                {"rule_id": "spill", "status": "pass", "reasons": []},
                {"rule_id": "dock", "status": "amber", "reasons": ["ci_green failed"]},
            ],
        },
    )
    chain.append("coverage", {"camera_id": "cam-a", "state": "ok", "reason": None})
    at(60)
    chain.append("candidate", _candidate("ev-1", "spill", "cam-a"))
    at(20)
    chain.append(
        "verdict", {"event_id": "ev-1", "tier": "glimmer", "verdict": "violation", "score": 0.9}
    )
    at(10)
    chain.append("action", {"event_id": "ev-1", "kind": "notify", "detail": "sent to staff"})
    at(30)
    chain.append("ack", {"event_id": "ev-1", "role": "staff"})
    at(240)
    chain.append("label", {"event_id": "ev-1", "label": "hazard_cleared"})
    at(600)
    chain.append("coverage", {"camera_id": "cam-a", "state": "blind", "reason": "dark"})
    at(1800)
    chain.append("coverage", {"camera_id": "cam-a", "state": "ok", "reason": None})
    clock.advance(8 * 3600)  # late shift
    chain.append("candidate", _candidate("ev-2", "dock", "cam-b"))
    at(5)
    chain.append(
        "verdict", {"event_id": "ev-2", "tier": "glimmer", "verdict": "no_violation", "score": 0.1}
    )
    at(5)
    chain.append(
        "dispute",
        {
            "dispute_id": "d1",
            "event_id": "ev-2",
            "dispute": {"channel": "tap", "reason_text": "not me", "outcome": "pending"},
        },
    )
    at(5)
    chain.append(
        "dispute",
        {
            "dispute_id": "d1",
            "event_id": "ev-2",
            "dispute": {"channel": "tap", "reason_text": "not me", "outcome": "upheld"},
        },
    )
    for action, extra in (
        ("setting_proposal", {"setting_name": "retention_days"}),
        ("setting_approval", {"role": "owner"}),
        ("setting_approval", {"role": "crew_representative"}),
        ("setting_applied", {"setting_name": "retention_days"}),
    ):
        at(60)
        chain.append("rule_change", {"action": action, "proposal_id": "p-1", **extra})
    at(60)
    chain.append("rule_change", {"action": "setting_applied", "proposal_id": "p-2"})
    return root


@pytest.fixture
def synthetic_chain(tmp_path: Path) -> Path:
    """`<tmp>/synthetic/demo/2026-11-02.jsonl` (simulated)."""
    return write_synthetic(tmp_path / "synthetic")
