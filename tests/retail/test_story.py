"""The connected supermarket story: Ask answer → advice card → pre-registered check and watch →
staff alert with the photo → restock task closed by photo → the owner's brief; and the routes."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import Executor, Future
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.act.notify import LanNotifier
from rulecam.advisor.api import AdviceStore
from rulecam.ask import AskAgent, AskAnswer
from rulecam.ask.runlog import GENESIS, row_hash
from rulecam.ask.vision import FrameGrabber
from rulecam.bus import Bus
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.retail.advice import (
    AdviceWriter,
    evidence_numbers,
    grounded,
    queue_gain,
    receipt,
    spread,
)
from rulecam.retail.api import retail_router
from rulecam.retail.brief import BriefWriter, day_stats
from rulecam.retail.checks import QueueChecker, RetailError, ShelfChecker, Site
from rulecam.retail.models import Finding, StaffTask, WatchRequest
from rulecam.retail.proof import ProofChecker
from rulecam.retail.service import CardBook, RetailService, image_resolver
from rulecam.retail.staff import StaffDesk
from rulecam.retail.store import FindingLog, TaskBoard, WatchBook
from rulecam.shelf import ShelfScanner
from rulecam.voice_out.wav import encode_wav
from tests.fakes import FakeClock, FakeLLM
from tests.retail.conftest import AT, FakeFrames, FakeNotifier, FakeSam, Files, image
from tests.retail.test_checks import judgement

RECEIPT = "a" * 64
SITE = Site("A supermarket.", {"till": "checkout lanes", "aisle": "aisle 3"})


def ask_row() -> dict[str, Any]:
    return {
        "sha256": RECEIPT,
        "ts": AT.isoformat(),
        "conversation_id": "c1",
        "question": "When is the checkout queue longest, and do we need another till?",
        "answer": "Busiest at 09:00 with 6 waiting; one till open; about 0.8 customers a minute.",
        "numbers": [6.0],
        "steps": [
            {"action": {"action": "sql"}, "result": {"rows": [[6, 1, 0.8, 45.0]]}},
        ],
    }


def advice_draft(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Open a second till at the morning peak",
        "saw": [
            {"template": "The queue reached {peak_waiting} people with {open_tills} till open."},
            {"template": "Made up: {invented}."},
        ],
        "why": ["One till for the morning rush."],
        "change_op": "add_capacity",
        "change": "Open the second till from 08:30 to 10:00.",
        "change_params": {"till": "2"},
        "who_role": "shift lead",
        "revert": "Close the second till again.",
        "scope_kind": "station",
        "scope_ids": ["checkout"],
        "numbers": [
            {
                "id": "peak_waiting",
                "label": "peak",
                "value": 6,
                "unit": "people",
                "definition": "d",
            },
            {"id": "open_tills", "label": "tills", "value": 1, "unit": "tills", "definition": "d"},
            {
                "id": "arrivals",
                "label": "arrivals",
                "value": 0.8,
                "unit": "/min",
                "definition": "d",
            },
            {"id": "service", "label": "service", "value": 45, "unit": "s", "definition": "d"},
            {"id": "invented", "label": "x", "value": 4242, "unit": "", "definition": "d"},
        ],
        "arrival_rate_id": "arrivals",
        "service_time_id": "service",
        "open_tills_id": "open_tills",
        "primary_metric": "customers waiting at the tills",
        "direction": "lower_is_better",
        "block_minutes": 30,
        "n_pairs": 6,
        "guard_metrics": ["customers needing help"],
        "watch_kind": "queue",
        "watch_camera_id": "till",
        "watch_every_s": 60,
        "watch_role": "tills",
        "watch_focus": "the queue at the left till",
    }
    return base | overrides


class SyncPool(Executor):
    """Runs submitted work at once (no threads in tests)."""

    def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class Rig:
    """A service on fakes, with every part reachable."""

    def __init__(self, tmp_path: Path, spark: FakeLLM, glimmer: FakeLLM | None = None) -> None:
        self.clock = FakeClock(AT)
        self.frame = image(tmp_path / "frame.jpg")
        self.frames = FakeFrames(self.frame, ("till", "aisle"))
        self.notifier = FakeNotifier()
        self.chain = EventChain(tmp_path / "chain", "demo", clock=self.clock)
        self.advice = AdviceStore()
        runs = tmp_path / "ask" / "runs.jsonl"
        runs.parent.mkdir(parents=True)
        runs.write_text(json.dumps(ask_row()) + "\n", encoding="utf-8")
        root = tmp_path / "retail"
        self.findings = FindingLog(root / "findings.jsonl")
        self.tasks = TaskBoard(root / "tasks.json")
        resolve = image_resolver([tmp_path / "marks", tmp_path, tmp_path / "shelf"])
        self.lan = LanNotifier(bus=Bus(), clock=self.clock)
        sam = FakeSam({"person": [(10, 10, 50, 150)]})
        queue = QueueChecker(
            spark=spark,
            files=Files(),
            sam=sam,
            frames=self.frames,
            site=SITE,
            clock=self.clock,
            marks_dir=tmp_path / "marks",
            history=lambda cam, kind: [],
        )
        scanner = ShelfScanner(
            spark=spark,
            files=Files(),
            sam=sam,
            out_dir=tmp_path / "shelf",
            frames=cast(FrameGrabber, self.frames),
        )
        shelf = ShelfChecker(scanner, SITE, self.clock)
        self.service = RetailService(
            checkers={"queue": queue, "shelf": shelf},
            image_checkers={"shelf": shelf},
            frames=self.frames,
            findings=self.findings,
            watches=WatchBook(root / "watches.json"),
            tasks=self.tasks,
            desk=StaffDesk(
                notifier=self.notifier,
                alerts=self.lan.get,
                tasks=self.tasks,
                chain=self.chain,
                clock=self.clock,
                image_path=resolve,
            ),
            cards=CardBook(root / "cards.json"),
            writer=AdviceWriter(
                spark=spark,
                runs=runs,
                clock=self.clock,
                camera_names=SITE.camera_names,
                site_profile=SITE.profile,
            ),
            briefs=BriefWriter(spark, self.clock),
            advice=self.advice,
            chain=self.chain,
            clock=self.clock,
            ask_runs=runs,
            camera_names=SITE.camera_names,
            site_profile=SITE.profile,
            proof=ProofChecker(
                spark=spark, files=Files(), glimmer=glimmer, glimmer_model="muse-glimmer-30b"
            ),
            image_path=resolve,
            pool=SyncPool(),
        )

    def records(self) -> list[Any]:
        return read_chain(self.chain.path_for(AT.date()))


def gap_draft() -> dict[str, Any]:
    gap = {"box": [0, 0, 500, 500], "section": "bay 1", "neighbours": "TEA", "facings": 2}
    return {"gaps": [gap]}


def test_queue_check_alerts_staff_with_the_marked_photo(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM([judgement()]))
    finding = rig.service.run_check("queue", "till")
    assert finding.alert_id == "al-1"
    sent = rig.notifier.sent[0]
    assert sent["role"] == "tills"
    assert sent["title"] == "Checkout: checkout lanes"
    assert sent["body"] == "Open the right till now."
    assert str(sent["clip"]).endswith(".jpg")
    assert sent["buttons"] == ("On my way", "False alarm")
    assert rig.findings.get(finding.id) == finding
    record = rig.records()[-1]
    assert record.kind == "advice"
    assert record.payload["type"] == "retail_finding"
    assert record.payload["finding_id"] == finding.id


def test_till_announcement_goes_to_staff(tmp_path: Path) -> None:
    said = "Till 2 is opening. / La caja 2 está abriendo."
    rig = Rig(tmp_path, FakeLLM([judgement(announcement=said)]))
    rig.service.run_check("queue", "till")
    assert rig.notifier.sent[0]["body"] == f"Open the right till now.\nAnnounce: {said}"


def test_quiet_check_sends_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM([judgement(alert_staff=False)]))
    finding = rig.service.run_check("queue", "till")
    assert finding.alert_id is None
    assert rig.notifier.sent == []


def test_unknown_check_and_camera(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM())
    with pytest.raises(RetailError, match="no hazard check"):
        rig.service.run_check("hazard", "till")
    with pytest.raises(RetailError, match="no recorded video"):
        rig.service.run_check("queue", "car-park")


def test_ask_answer_to_card_to_watch(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM([advice_draft(), judgement()]))
    card = rig.service.draft_advice(RECEIPT, "till")
    numbers = {n.id: n.grounded for n in card.numbers}
    assert numbers == {
        "peak_waiting": True,
        "open_tills": True,
        "arrivals": True,
        "service": True,
        "invented": False,
    }
    assert card.hidden == ["invented"]
    assert card.saw == ["The queue reached 6 customers with 1 till open."]
    assert "s less per customer" in card.gain
    assert card.plan.n_pairs == 6
    assert card.plan.preregistration_hash
    assert rig.advice.get(card.recommendation.id).recommendation.numbers_reproduced == 4

    accepted = rig.service.accept(card.recommendation.id)
    assert accepted.recommendation.status == "measuring"
    assert accepted.preregistered_seq is not None
    assert accepted.watch_id is not None
    prereg = [r for r in rig.records() if r.kind == "measurement_plan"]
    assert prereg[0].payload["recommendation_id"] == card.recommendation.id
    with pytest.raises(RetailError, match="already"):
        rig.service.accept(card.recommendation.id)

    assert rig.service.tick() == 1
    rig.service.close()
    readings = rig.findings.recent(watch_id=accepted.watch_id)
    assert len(readings) == 1
    assert rig.notifier.sent[0]["role"] == "tills"
    progress = rig.service.card_progress(accepted)
    assert progress["watch_readings"] == 1
    assert progress["mean_reading"] == 4.0


def test_card_without_grounded_observation(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM([advice_draft(saw=[{"template": "Made up: {invented}."}])]))
    with pytest.raises(RetailError, match="no observation"):
        rig.service.draft_advice(RECEIPT)


def test_unknown_receipt(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM())
    with pytest.raises(RetailError, match="no Ask answer"):
        rig.service.draft_advice("b" * 64)


def test_watch_is_not_rerun_before_it_is_due(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM([judgement(), judgement()]))
    watch = rig.service.add_watch(WatchRequest(kind="queue", camera_id="till", every_s=60))
    assert rig.service.tick() == 1
    assert rig.service.tick() == 0
    rig.clock.advance(61)
    assert rig.service.tick() == 1
    rig.service.close()
    assert len(rig.findings.recent(watch_id=watch.id)) == 2
    stopped = rig.service.stop_watch(watch.id)
    assert stopped is not None
    assert stopped.active is False


def test_shelf_gap_becomes_a_task_closed_by_a_photo(tmp_path: Path) -> None:
    spark = FakeLLM(
        [
            gap_draft(),
            {
                "gaps": [{"number": 1, "filled": False, "note": "still bare"}],
                "all_filled": False,
                "summary": "not yet",
            },
            {
                "gaps": [{"number": 1, "filled": True, "note": "tea boxes"}],
                "all_filled": True,
                "summary": "filled",
            },
        ]
    )
    agree = {"gaps": [], "all_filled": True, "summary": "agree"}
    rig = Rig(tmp_path, spark, glimmer=FakeLLM([agree, agree]))
    finding = rig.service.run_image("shelf", rig.frame)
    task = rig.tasks.get(str(finding.task_id))
    assert task is not None
    assert task.role == "stock"
    assert task.items == ["gap 1: bay 1 next to TEA (~2 facings)"]
    assert task.seen_at is not None
    assert rig.notifier.sent[0]["title"] == "Restock: upload"
    photo = image(tmp_path / "after.jpg", value=90)
    still_open = rig.service.prove(task.id, photo)
    assert still_open.status == "open"
    rig.clock.advance(12 * 60)
    done = rig.service.prove(task.id, photo)
    assert done.status == "done"
    assert done.closed_by == "photo"
    assert done.minutes_to_fix == 12.0
    assert done.proof is not None
    assert rig.service.tasks_for("stock", open_only=True) == []
    fixed = [r for r in rig.records() if r.payload.get("type") == "gap_refilled"]
    assert fixed[0].seq == done.fixed_seq
    assert fixed[0].payload["minutes_to_fix"] == 12.0
    detected = [r for r in rig.records() if r.payload.get("type") == "retail_finding"]
    assert fixed[0].payload["detected_seq"] == detected[0].seq == finding.chain_seq
    assert detected[0].payload["finding_id"] == finding.id
    assert rig.tasks.events.read_text().count(task.id) == 4  # opened, alerted, tried, fixed


def test_brief_counts_and_writes(tmp_path: Path) -> None:
    spark = FakeLLM(
        [
            judgement(),
            {"headline": "Tills were short at nine.", "text": "One alert...", "decisions": ["x"]},
        ]
    )
    rig = Rig(tmp_path, spark)
    rig.service.run_check("queue", "till")
    brief = rig.service.brief("es")
    assert brief.lang == "es"
    assert brief.stats.checks == {"queue": 1}
    assert brief.stats.alerts_sent == 1
    assert brief.stats.till_calls == 1
    assert brief.stats.asks == 1
    assert rig.service.last_brief == brief
    prompt = spark.calls[-1]["messages"][1]["content"]  # type: ignore[index]
    assert "Language: es" in prompt


def test_day_stats_counts_answered_alerts() -> None:
    f = Finding(
        id="f-1",
        kind="hazard",
        camera_id="aisle",
        at=AT,
        checked_at=AT,
        act=True,
        headline="h",
        alert_id="al-1",
        metric=2.0,
    )
    done = StaffTask(
        id="t-1",
        finding_id="f-1",
        camera_id="aisle",
        where="aisle",
        items=[],
        marked=None,
        created_at=AT,
        status="done",
        minutes_to_fix=7.0,
    )
    stats = day_stats([f], [done], 0, {"f-1": "acknowledged"})
    assert stats.alerts_answered == 1
    assert stats.hazards_confirmed == 2
    assert stats.median_minutes_to_fix == 7.0


def test_grounding_and_gain_helpers() -> None:
    seen = evidence_numbers(ask_row())
    assert grounded(6, seen)
    assert grounded(0.8, seen)
    assert not grounded(4242, seen)
    gain, text = queue_gain(0.8, 45, 1)
    assert 0 < gain.interval[0] <= gain.interval[1]
    assert "M/M/c" in text
    unstable, text = queue_gain(1.5, 60, 1)
    assert unstable.assumptions.get("stable_with_current_tills") == "fail"
    assert "keeps growing" in text
    hopeless, text = queue_gain(3.0, 95, 1)
    assert hopeless.assumptions == {"stable_with_one_more_till": "fail"}
    assert spread([], 3.0)[0] == 3.0


def test_routes(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM([judgement()]))
    app = FastAPI()
    app.include_router(
        retail_router(rig.service, uploads_dir=tmp_path / "up", files=image_resolver([tmp_path]))
    )
    client = TestClient(app)
    finding = client.post("/retail/checks/queue", json={"camera_id": "till"})
    assert finding.status_code == 200
    overview = client.get("/retail/overview").json()
    assert overview["findings"][0]["alert_status"] is None  # FakeNotifier ids are not LAN alerts
    assert [c["camera_id"] for c in overview["cameras"]] == ["till", "aisle"]
    assert client.get("/retail/files/frame.jpg").status_code == 200
    assert client.get("/retail/files/../secret.jpg").status_code == 404
    assert client.post("/retail/checks/queue", json={"camera_id": "car-park"}).status_code == 422
    watch = client.post("/retail/watches", json={"kind": "queue", "camera_id": "till"}).json()
    assert client.delete(f"/retail/watches/{watch['id']}").json()["active"] is False
    assert client.get("/retail/asks").json()[0]["receipt"] == RECEIPT
    assert client.get("/retail/brief").json() is None
    assert client.post("/retail/ask/voice", files={"audio": ("a.wav", b"x")}).status_code == 503

    down = FastAPI()
    down.include_router(retail_router(None, uploads_dir=tmp_path, files=image_resolver([])))
    assert TestClient(down).get("/retail/overview").status_code == 503


def test_clean_recheck_closes_the_task(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM([gap_draft(), {"gaps": []}]))
    finding = rig.service.run_check("shelf", "aisle")
    rig.clock.advance(300)
    # The same camera checked again, now with no gaps: the task closes, seen fixed.
    rig.service.run_check("shelf", "aisle", AT + timedelta(minutes=5))
    task = rig.tasks.get(str(finding.task_id))
    assert task is not None
    assert task.status == "done"
    assert task.closed_by == "camera"
    assert task.minutes_to_fix == 5.0


def test_staff_close_by_hand(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeLLM([gap_draft()]))
    finding = rig.service.run_image("shelf", rig.frame)
    rig.clock.advance(60)
    app = FastAPI()
    app.include_router(
        retail_router(rig.service, uploads_dir=tmp_path / "up", files=image_resolver([tmp_path]))
    )
    client = TestClient(app)
    done = client.post(f"/retail/tasks/{finding.task_id}/status", json={"status": "done"}).json()
    assert done["closed_by"] == "staff"
    assert done["minutes_to_fix"] == 1.0
    feed = client.get("/retail/tasks", params={"role": "stock", "open_only": True}).json()
    assert feed == []


def test_receipt_rehashes_in_any_language(tmp_path: Path) -> None:
    row: dict[str, object] = {
        "question": "¿Cuántos clientes esperan?",
        "answer": "Seis.",
        "ts": AT.isoformat(),
        "models": ["muse-spark-1.3"],
        "seconds": 1.5,
        "prev_sha256": GENESIS,
    }
    row["sha256"] = row_hash(GENESIS, row)
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    got = receipt(runs, str(row["sha256"]))
    digest = hashlib.sha256((got.prev_sha256 + got.hashed_text).encode("utf-8")).hexdigest()
    assert digest == got.receipt
    assert got.rows_verified == 1
    assert got.question == "¿Cuántos clientes esperan?"


class FakeAsk:
    """The Ask agent: answers every question the same way."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def ask(self, question: str, *, conversation_id: str | None = None) -> AskAnswer:
        self.asked.append(question)
        return AskAnswer(
            question=question,
            answer="Seis clientes, dos cajas.",
            steps=[],
            seconds=1.0,
            models=["muse-spark-1.3"],
            conversation_id="c1",
        )


def test_story_routes(tmp_path: Path) -> None:
    spark = FakeLLM([advice_draft(), gap_draft(), {"headline": "h", "text": "t", "decisions": []}])
    rig = Rig(tmp_path, spark)
    fake_ask = FakeAsk()
    heard: list[list[str] | None] = []

    def transcribe(wav: bytes, langs: list[str] | None) -> list[dict[str, str]]:
        heard.append(langs)
        return [{"speaker": "a", "text": "¿Cuántos clientes?", "lang": "es"}]

    app = FastAPI()
    app.include_router(
        retail_router(
            rig.service,
            uploads_dir=tmp_path / "up",
            files=image_resolver([tmp_path]),
            ask=cast(AskAgent, fake_ask),
            transcribe=transcribe,
        )
    )
    client = TestClient(app)
    card = client.post("/retail/advice", json={"receipt": RECEIPT}).json()
    accepted = client.post(f"/retail/advice/{card['recommendation']['id']}/accept").json()
    assert accepted["preregistered_seq"] is not None
    assert client.post("/retail/advice/nope/accept").status_code == 422
    photo = (tmp_path / "frame.jpg").read_bytes()
    shelf = client.post(
        "/retail/checks/shelf/image", files={"image": ("s.jpg", photo, "image/jpeg")}
    ).json()
    assert shelf["task_id"]
    assert (
        client.post(
            "/retail/checks/shelf/image", files={"image": ("s.gif", b"x", "image/gif")}
        ).status_code
        == 422
    )
    brief = client.post("/retail/brief", json={"lang": "es"}).json()
    assert brief["text"] == "t"
    assert client.get("/retail/brief").json()["headline"] == "h"
    wav = encode_wav(np.zeros(1600, dtype=np.float32), 16000)
    voice = client.post(
        "/retail/ask/voice", files={"audio": ("q.wav", wav, "audio/wav")}, data={"lang": "es"}
    ).json()
    assert voice["transcript"] == "¿Cuántos clientes?"
    assert voice["lang"] == "es"
    assert voice["answer"]["answer"] == "Seis clientes, dos cajas."
    assert heard == [["es"]]
    bad = client.post("/retail/ask/voice", files={"audio": ("q.wav", b"nope", "audio/wav")})
    assert bad.status_code == 422
    watches = client.get("/retail/watches").json()
    assert len(watches) == 1
    assert client.get("/retail/findings", params={"kind": "shelf"}).json()[0]["kind"] == "shelf"
