"""Tests for safety walks: the store, the Spark report and the evidence records."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from rulecam.evidence.chain import EventChain, read_chain, verify_chain
from rulecam.wearables.schemas import Hazard, PovAnswer
from rulecam.wearables.walks import WalkError, WalkReporter, WalkStore, finding_line, notable
from tests.fakes import EPOCH, FakeClock, FakeLLM


def answer(answer_id: str, *, verified: bool = False, hazard: bool = True) -> PovAnswer:
    return PovAnswer(
        answer_id=answer_id,
        at=EPOCH,
        mode="warehouse",
        kind="patrol",
        spoken="Forklift ahead." if verified else "",
        detail="Glimmer and Spark see a forklift ahead.",
        alert=verified,
        verified=verified,
        verified_by=["muse-spark-1.3", "muse-glimmer-30b"] if verified else [],
        severity="danger" if verified else "none",
        confidence=0.8,
        hazards=[Hazard(what="forklift", where="ahead", severity="danger")] if hazard else [],
        frame=f"{answer_id}.jpg",
    )


class Files:
    def __init__(self) -> None:
        self.attached: list[str] = []

    def attach(self, path: Path) -> dict[str, object]:
        self.attached.append(path.name)
        return {"type": "input_image", "file_id": path.name}


def test_store_lifecycle(tmp_path: Path) -> None:
    clock = FakeClock()
    store = WalkStore(tmp_path, clock=clock)
    walk = store.start("warehouse", "north aisle")
    store.add(walk.walk_id, answer("a1"))
    store.add(walk.walk_id, answer("a2"))
    assert store.get(walk.walk_id).frames == 2
    assert [a.answer_id for a in store.answers(walk.walk_id)] == ["a1", "a2"]
    clock.advance(60)
    done = store.finish(walk.walk_id)
    assert done.finished_at == EPOCH + timedelta(seconds=60)
    assert store.finish(walk.walk_id) == done
    with pytest.raises(WalkError):
        store.add(walk.walk_id, answer("a3"))
    with pytest.raises(WalkError):
        store.get("walk-missing-000000")
    with pytest.raises(WalkError):
        store.get("../etc/passwd")


def test_findings_and_notable_frames() -> None:
    quiet = answer("q", hazard=False)
    seen = answer("s")
    sure = answer("v", verified=True)
    assert [a.answer_id for a in notable([quiet, seen, sure])] == ["v", "s"]
    line = finding_line(sure)
    assert "alert verified by muse-spark-1.3, muse-glimmer-30b" in line
    assert "forklift (ahead, danger)" in line


def test_report_chains_each_hazard(tmp_path: Path) -> None:
    clock = FakeClock()
    store = WalkStore(tmp_path / "walks", clock=clock)
    walk = store.start("warehouse")
    for a in (answer("a1", verified=True), answer("a2"), answer("a3", hazard=False)):
        store.add(walk.walk_id, a)
    walk = store.finish(walk.walk_id)
    spark = FakeLLM(
        [
            {
                "summary": "One forklift crossed the pedestrian aisle twice.",
                "spoken": "One hazard found: a forklift in the walkway.",
                "hazards": [
                    {
                        "what": "forklift in the pedestrian aisle",
                        "where": "north aisle",
                        "severity": "danger",
                        "answer_ids": ["a1", "a2", "nope"],
                        "action": "Mark the walkway and brief drivers.",
                    }
                ],
                "restock": [],
            }
        ]
    )
    files = Files()
    chain = EventChain(tmp_path / "chain", "demo", clock=clock)
    reporter = WalkReporter(
        spark=spark, files=files, chain=chain, frame_path=lambda n: tmp_path / "frames" / n
    )

    report = reporter.report(walk, store.answers(walk.walk_id))

    assert report.summary.hazards[0].answer_ids == ["a1", "a2", "nope"]
    assert files.attached == ["a1.jpg", "a2.jpg"]
    records = read_chain(chain.path_for(EPOCH.date()))
    assert [r.kind for r in records] == ["candidate", "verdict", "action"]
    candidate = records[0].payload["candidate"]
    assert isinstance(candidate, dict)
    assert candidate["event_id"] == f"{walk.walk_id}-h1"
    assert candidate["rule_id"] == "safety-walk"
    assert candidate["strip_paths"] == [
        str(tmp_path / "frames" / "a1.jpg"),
        str(tmp_path / "frames" / "a2.jpg"),
    ]
    assert records[1].payload["verdict"] == "confirmed"
    assert records[1].payload["tier"] == "muse-spark-1.3 + muse-glimmer-30b"
    assert records[2].payload["kind"] == "report"
    assert report.chain_hashes == [records[0].hash, records[2].hash]
    assert verify_chain(chain.path_for(EPOCH.date())).ok


def test_empty_walk_needs_no_model(tmp_path: Path) -> None:
    store = WalkStore(tmp_path, clock=FakeClock())
    walk = store.finish(store.start("aisle").walk_id)
    reporter = WalkReporter(spark=FakeLLM([]), files=Files(), chain=None, frame_path=Path)
    report = reporter.report(walk, [])
    assert report.summary.hazards == []
    assert report.chain_hashes == []
