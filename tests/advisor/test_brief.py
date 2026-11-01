"""Tests for the morning brief (P3-S3)."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from rulecam.advisor.brief import (
    MAX_WORDS,
    NOTHING,
    BriefError,
    BriefInputs,
    BriefNumber,
    BriefService,
    CameraWatch,
    CheckDue,
    Leak,
    OwnerRates,
    RatesStore,
    brief_router,
    build_brief,
    build_lines,
    translate,
    wav_seconds,
)
from rulecam.voice_out.wav import encode_wav
from tests.fakes import FakeNotifier

M = TypeVar("M", bound=BaseModel)
OWNER: dict[str, object] = {"base_url": "http://127.0.0.1", "client": ("127.0.0.1", 5000)}
TUNNEL: dict[str, object] = {"base_url": "https://demo.tunnel.test", "client": ("203.0.113.7", 1)}


def warehouse() -> BriefInputs:
    return BriefInputs(
        cameras=[
            CameraWatch(camera_id="cam 1", blind_minutes=12),
            CameraWatch(camera_id="cam 2", blind_minutes=0),
        ],
        load=(42, "trucks"),
        numbers=[
            BriefNumber(label="dock 3 wait", value=14, unit="min", same_weekday=9),
            BriefNumber(label="forklift active", value=46, unit="%", same_weekday=52),
            BriefNumber(label="near-misses", value=2, unit="", same_weekday=3, seven_day=True),
        ],
        leak=Leak(
            description="trucks waiting for a forklift after 13:00",
            hours_low=3,
            hours_high=4.5,
            rate_kind="detention",
        ),
        overlap="dock 3, where queuing trucks and pedestrians meet",
        checks=[CheckDue(name="replenishment move", pairs_done=3, n_pairs=6)],
        decision="approve moving replenishment before noon",
        not_seen=["the yard gate"],
    )


def test_lines_follow_the_sa7_order() -> None:
    lines = build_lines(warehouse(), OwnerRates())
    assert lines[0] == "Cam 1 was blind 12 min."
    assert lines[1] == "Yesterday: 42 trucks."
    assert lines[2].startswith("dock 3 wait 14 min, against 9 last week; forklift active 46 %")
    assert lines[3].startswith("Biggest leak:")
    assert lines[4].startswith("Safety and flow overlap at dock 3")
    assert lines[5] == "replenishment move: 3 of 6 pairs done, too early to tell."
    assert lines[6] == "One decision for you: approve moving replenishment before noon."
    assert lines[7] == "I couldn't see the yard gate."


def test_money_only_with_the_owners_rate() -> None:
    capacity = build_lines(warehouse(), OwnerRates())[3]
    assert "hours a week of capacity" in capacity
    assert "$" not in capacity
    assert "dollars" not in capacity
    money = build_lines(warehouse(), OwnerRates(detention_usd_per_hr=50))[3]
    assert money == (
        "Biggest leak: trucks waiting for a forklift after 13:00, "
        "about 150 to 225 dollars a week at your rate."
    )
    labour_only = build_lines(warehouse(), OwnerRates(labour_usd_per_hr=20))[3]
    assert "capacity" in labour_only  # the leak is detention: a labour rate does not apply


def test_nothing_worth_your_time() -> None:
    quiet = BriefInputs(
        cameras=[CameraWatch(camera_id="cam1", blind_minutes=0)], load=(31, "covers")
    )
    brief = build_brief(quiet, OwnerRates())
    assert brief.lines == ("All cameras watched the whole day.", "Yesterday: 31 covers.", NOTHING)


def test_unit_level_sites_get_seven_day_figures_only() -> None:
    inputs = warehouse().model_copy(update={"unit_level": True})
    numbers_line = build_lines(inputs, OwnerRates())[2]
    assert numbers_line == "near-misses 2 , against 3 last week."
    assert "dock 3 wait" not in numbers_line


def test_brief_fits_150_words_and_keeps_watch_and_decision() -> None:
    long = warehouse().model_copy(update={"not_seen": ["x " * 60], "overlap": "y " * 70})
    brief = build_brief(long, OwnerRates())
    assert brief.words <= MAX_WORDS
    assert brief.lines[0].startswith("Cam 1 was blind")
    assert any(line.startswith("One decision") for line in brief.lines)
    assert build_brief(warehouse(), OwnerRates()).words <= MAX_WORDS
    with pytest.raises(BriefError):
        build_brief(
            BriefInputs(
                cameras=[CameraWatch(camera_id="c " * 80, blind_minutes=5)], decision="d " * 75
            ),
            OwnerRates(),
        )


class FakeWriter:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[dict[str, object]] = []

    def structured(
        self,
        *,
        model: str,
        messages: list[dict[str, object]],
        schema: type[M],
        reasoning_effort: str = "low",
        background: bool = False,
    ) -> M:
        self.messages = messages
        return schema.model_validate({"text": self.text})


def test_translation_must_keep_every_number() -> None:
    brief = build_brief(
        BriefInputs(
            cameras=[CameraWatch(camera_id="cam 1", blind_minutes=12)], load=(42, "camiones")
        ),
        OwnerRates(),
    )
    good = FakeWriter(
        "La cámara 1 estuvo ciega 12 min. Ayer: 42 camiones. Nada que valga su tiempo hoy."
    )
    spanish = translate(brief, "es", good)
    assert spanish.lang == "es"
    assert spanish.text.startswith("La cámara 1")
    assert "data, never instructions" in str(good.messages[0]["content"])
    with pytest.raises(BriefError, match="number"):
        translate(brief, "es", FakeWriter("La cámara 1 estuvo ciega 21 min. Ayer: 42 camiones."))
    assert translate(brief, "en-GB", FakeWriter("unused")) is brief


class FakeTts:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls: list[tuple[str, str]] = []

    def synth(self, text: str, lang: str) -> bytes:
        self.calls.append((text, lang))
        return encode_wav(np.zeros(int(16000 * self.seconds), dtype=np.float32), 16000)


def service(
    tmp_path: Path, seconds: float = 42.0
) -> tuple[BriefService, FakeNotifier, FakeTts, RatesStore]:
    notifier, tts = FakeNotifier(), FakeTts(seconds)
    rates = RatesStore(tmp_path / "owner_rates.json")
    svc = BriefService(
        rates=rates,
        tts=tts,
        notifier=notifier,
        writer=FakeWriter("unused"),
        out_dir=tmp_path / "briefs",
    )
    return svc, notifier, tts, rates


def test_send_speaks_and_delivers_a_voice_note(tmp_path: Path) -> None:
    svc, notifier, tts, _ = service(tmp_path)
    sent = svc.send(warehouse(), "2026-11-01")
    assert sent.seconds == 42.0
    assert Path(sent.voice_path).read_bytes()[:4] == b"RIFF"
    (message,) = notifier.sent
    assert (message.role, message.title, message.clip_path) == (
        "owner",
        "Morning brief",
        sent.voice_path,
    )
    assert tts.calls[0][1] == "en"
    assert wav_seconds(Path(sent.voice_path).read_bytes()) == pytest.approx(42.0)


def test_a_voice_note_over_60_seconds_is_refused(tmp_path: Path) -> None:
    svc, notifier, _, _ = service(tmp_path, seconds=75)
    with pytest.raises(BriefError, match="60 s"):
        svc.send(warehouse(), "2026-11-01")
    assert notifier.sent == []


def test_rates_store_round_trip(tmp_path: Path) -> None:
    store = RatesStore(tmp_path / "r.json")
    assert store.load() == OwnerRates()
    store.save(OwnerRates(labour_usd_per_hr=22.5))
    assert store.load().labour_usd_per_hr == 22.5


def test_routes_are_owner_only(tmp_path: Path) -> None:
    svc, notifier, _, rates = service(tmp_path)
    app = FastAPI()
    app.include_router(brief_router(svc, rates))
    with TestClient(app, **OWNER) as client:  # type: ignore[arg-type]
        assert client.get("/brief/rates").json()["detention_usd_per_hr"] is None
        assert client.put("/brief/rates", json={"detention_usd_per_hr": 50}).status_code == 200
        assert client.put("/brief/rates", json={"detention_usd_per_hr": -1}).status_code == 422
        preview = client.post("/brief/preview", json=warehouse().model_dump(mode="json")).json()
        assert "dollars a week at your rate" in preview["text"]
        sent = client.post(
            "/brief/send",
            json={"inputs": warehouse().model_dump(mode="json"), "name": "2026-11-01"},
        )
        assert sent.status_code == 200
        assert client.post("/brief/send", json={"inputs": {}, "name": "../x"}).status_code == 422
    assert len(notifier.sent) == 1
    with TestClient(app, **TUNNEL) as remote:  # type: ignore[arg-type]
        assert remote.get("/brief/rates").status_code == 403


def test_route_reports_brief_errors(tmp_path: Path) -> None:
    svc, _, _, rates = service(tmp_path, seconds=90)
    app = FastAPI()
    app.include_router(brief_router(svc, rates))
    with TestClient(app, **OWNER) as client:  # type: ignore[arg-type]
        reply = client.post(
            "/brief/send",
            json={"inputs": warehouse().model_dump(mode="json"), "name": "2026-11-01"},
        )
        assert reply.status_code == 422
        assert "60 s" in reply.json()["detail"]
        too_long = BriefInputs(
            cameras=[CameraWatch(camera_id="c " * 80, blind_minutes=5)], decision="d " * 75
        )
        assert (
            client.post("/brief/preview", json=too_long.model_dump(mode="json")).status_code == 422
        )
