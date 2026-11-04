"""The `/staff` routes, the event stream, speech and push-to-talk Ask."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.ask.trace import AskAnswer
from rulecam.shelf.gaps import ShelfGap, ShelfReport
from rulecam.staff.api import StaffConfig, staff_router, stream_feed
from rulecam.staff.asking import NothingHeardError, VoiceAsk, framed
from rulecam.staff.chainview import ChainView
from rulecam.staff.speech import Engine, Speaker
from rulecam.voice_out.errors import SynthesisError
from tests.fakes import FakeLLM, FakeTranscriber, FakeTTS
from tests.staff.conftest import JPEG, Rig


class BrokenTTS:
    def synth(self, text: str, lang: str) -> bytes:
        raise SynthesisError("no voice")


class FakeAgent:
    def __init__(self) -> None:
        self.questions: list[str] = []

    def ask(self, question: str) -> AskAnswer:
        self.questions.append(question)
        return AskAnswer(
            question=question,
            answer="Aisle 4 is clear now.",
            steps=[],
            seconds=1.0,
            models=["muse-spark-1.3"],
            receipt="abc123def456",
        )


class FakeShelf:
    def __init__(self, marked: Path) -> None:
        self.marked = marked

    def scan_image(self, image: Path) -> ShelfReport:
        return ShelfReport(
            camera_id="upload",
            at=None,
            image=str(image),
            marked_image=str(self.marked),
            width=100,
            height=100,
            products_seen=12,
            gaps=[
                ShelfGap(
                    number=1,
                    box_xyxy=(0, 0, 10, 10),
                    section="bay 2, shelf 1",
                    neighbours="rice",
                    facings=3,
                    product_cover=0.1,
                )
            ],
            model="muse-spark-1.3",
        )


def speaker(rig: Rig, llm: FakeLLM | None = None) -> Speaker:
    return Speaker(
        spark=llm,
        engines=[
            (Engine(name="mms-tts", meta=True), BrokenTTS()),
            (Engine(name="system-tts", meta=False), FakeTTS()),
        ],
        audio_dir=rig.tmp / "staff" / "audio",
    )


def client(rig: Rig, *, llm: FakeLLM | None = None, ask: bool = True) -> TestClient:
    voice = speaker(rig, llm)
    asker = (
        VoiceAsk(
            transcriber=FakeTranscriber(
                [{"text": "is aisle 4 clear?", "speaker": "s", "lang": "en"}]
            ),
            agent=FakeAgent(),
            speaker=voice,
        )
        if ask
        else None
    )
    app = FastAPI()
    app.include_router(
        staff_router(
            rig.feed,
            bus=rig.bus,
            clock=rig.clock,
            speaker=voice,
            config=StaffConfig(
                langs=("en", "es", "hi"),
                dry_run=False,
                ask=ask,
                listen=ask,
                shelf=True,
                speech=("mms-tts", "system-tts"),
            ),
            photos_dir=rig.tmp / "staff" / "photos",
            asker=asker,
            shelf=FakeShelf(rig.still("marked.jpg")) if ask else None,  # type: ignore[arg-type]
        )
    )
    return TestClient(app)


def test_feed_item_actions_and_stats(rig: Rig) -> None:
    rig.rule_alert()
    http = client(rig)
    assert http.get("/staff/config").json()["langs"] == ["en", "es", "hi"]
    assert [r["role"] for r in http.get("/staff/roles").json()] == ["aisle_lead", "floor_lead"]
    (item,) = http.get("/staff/feed", params={"role": "aisle_lead"}).json()
    assert item["verdict"]["confidence"] == 0.91
    assert http.get("/staff/feed", params={"role": "bad role!"}).status_code == 422
    assert http.get("/staff/items/ev-1").json()["item_id"] == "ev-1"
    assert http.get("/staff/items/missing").status_code == 404
    still = http.get("/staff/media/ev-1/0")
    assert (still.status_code, still.headers["content-type"]) == (200, "image/jpeg")
    assert http.get("/staff/media/ev-1/7").status_code == 404
    seen = http.post("/staff/items/ev-1/seen", json={"role": "aisle_lead", "client_ms": 120})
    assert seen.json() == {"first": True, "alert_to_phone_ms": 0.0}
    assert http.post("/staff/items/x/seen", json={"role": "aisle_lead"}).status_code == 404
    acked = http.post(
        "/staff/items/ev-1/actions", json={"action": "acknowledge", "role": "aisle_lead"}
    )
    assert acked.json()["item"]["status"] == "acknowledged"
    refused = http.post(
        "/staff/items/ev-1/actions", json={"action": "escalate", "role": "aisle_lead"}
    )
    assert refused.status_code == 409
    missing = http.post("/staff/items/x/actions", json={"action": "done", "role": "aisle_lead"})
    assert missing.status_code == 404
    done = http.post(
        "/staff/items/ev-1/done",
        data={"role": "aisle_lead", "note": "moved"},
        files={"photo": ("p.jpg", JPEG, "image/jpeg")},
    )
    assert done.json()["item"]["status"] == "done"
    assert done.json()["item"]["thumbnails"] == 2
    wrong = http.post(
        "/staff/items/ev-1/done",
        data={"role": "aisle_lead"},
        files={"photo": ("p.gif", b"GIF8", "image/gif")},
    )
    assert wrong.status_code == 422
    stats = http.get("/staff/stats", params={"hours": 2}).json()
    assert stats["by_role"][0]["key"] == "aisle_lead"


def test_speech_writes_the_line_with_spark_and_falls_back_to_the_next_engine(rig: Rig) -> None:
    rig.rule_alert()
    llm = FakeLLM([{"text": "Pasillo 4: retire el palé ahora."}])
    http = client(rig, llm=llm)
    line = http.get("/staff/items/ev-1/speech", params={"lang": "es", "role": "aisle_lead"}).json()
    assert line["text"] == "Pasillo 4: retire el palé ahora."
    assert line["engine"] == "system-tts"
    assert line["model"] == "muse-spark-1.3"
    assert "Spanish (es)" in str(llm.calls[0]["messages"])
    again = http.get("/staff/items/ev-1/speech", params={"lang": "es", "role": "aisle_lead"})
    assert again.json() == line  # cached: Spark was called once
    audio = http.get(f"/staff/audio/{line['audio']}")
    assert (audio.status_code, audio.headers["content-type"]) == (200, "audio/wav")
    assert http.get("/staff/audio/..%2Fstate.jsonl").status_code == 404
    assert http.get("/staff/items/ev-1/speech", params={"lang": "fr"}).status_code == 422


def test_speech_without_spark_reads_the_card(rig: Rig) -> None:
    rig.rule_alert()
    line = speaker(rig).for_item(rig.feed.items()[0], "en")
    assert line.text.startswith("Alert: Keep aisle 4 clear")
    assert line.model is None


def test_push_to_talk_ask(rig: Rig) -> None:
    http = client(rig)
    reply = http.post(
        "/staff/ask",
        data={"role": "aisle_lead", "lang": "hi"},
        files={"audio": ("q.wav", b"RIFF", "audio/wav")},
    ).json()
    assert reply["heard"] == "is aisle 4 clear?"
    assert reply["answer"] == "Aisle 4 is clear now."
    assert reply["engine"] == "system-tts"
    assert reply["models"][0] == "muse-voice-transcribe"
    assert "Answer in Hindi" in framed("q", "steward", "hi")
    off = client(rig, ask=False)
    assert off.post("/staff/ask", data={"role": "aisle_lead"}).status_code == 503
    assert (
        off.post(
            "/staff/shelf", data={"role": "stock"}, files={"photo": ("s.jpg", JPEG, "image/jpeg")}
        ).status_code
        == 503
    )


def test_typed_ask_and_nothing_heard(rig: Rig) -> None:
    asker = VoiceAsk(transcriber=None, agent=FakeAgent(), speaker=speaker(rig))
    assert not asker.can_listen
    assert asker.ask(role="steward", lang="en", question="gate 3?").heard == "gate 3?"
    with pytest.raises(NothingHeardError):
        asker.ask(role="steward", lang="en", question="  ")


def test_shelf_photo_becomes_tasks(rig: Rig) -> None:
    http = client(rig)
    made = http.post(
        "/staff/shelf", data={"role": "stock"}, files={"photo": ("s.jpg", JPEG, "image/jpeg")}
    ).json()
    assert [m["title"] for m in made] == ["Restock: bay 2, shelf 1"]
    assert made[0]["roles"] == ["stock"]
    assert made[0]["thumbnails"] == 1


async def test_the_stream_sends_a_snapshot_then_changed_items(rig: Rig) -> None:
    rig.rule_alert()
    events = stream_feed(
        rig.feed, rig.bus, rig.clock, "aisle_lead", recheck_s=0.05, heartbeat_s=0.1
    )
    first = await anext(events)
    assert first.startswith("event: snapshot")
    snapshot = json.loads(first.split("data: ", 1)[1])
    assert [i["item_id"] for i in snapshot["items"]] == ["ev-1"]
    await asyncio.to_thread(
        rig.feed.act,
        "ev-1",
        __import__("rulecam.staff.models", fromlist=["x"]).StaffActionRequest(
            action="on_my_way", role="aisle_lead"
        ),
    )
    changed = await asyncio.wait_for(anext(events), 5)
    assert changed.startswith("event: item")
    assert json.loads(changed.split("data: ", 1)[1])["item"]["status"] == "on_my_way"
    beat = await asyncio.wait_for(anext(events), 5)
    assert beat.startswith("event: ping")
    with contextlib.suppress(StopAsyncIteration):
        await events.aclose()


def test_the_chain_view_reads_only_whole_new_lines(tmp_path: Path) -> None:
    folder = tmp_path / "site"
    folder.mkdir()
    day = folder / "2026-10-30.jsonl"
    record = {
        "kind": "verdict",
        "ts": "2026-10-30T12:00:00Z",
        "payload": {"event_id": "e", "verdict": "x"},
    }
    day.write_text(json.dumps(record) + "\n" + '{"kind": "verdict", "payl')
    view = ChainView(folder, today=lambda: __import__("datetime").date(2026, 10, 30))
    view.refresh()
    entry = view.get("e")
    assert entry is not None
    assert len(entry.verdicts) == 1
    with day.open("a") as out:
        out.write('oad": {"event_id": "e", "verdict": "y"}}\nnot json\n')
    view.refresh()
    assert [v["verdict"] for v in entry.verdicts] == ["x", "y"]
    assert view.event_ids() == []
