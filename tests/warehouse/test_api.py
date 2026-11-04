"""`/warehouse` routes and the Spark / Muse Image / Voice writers, with fakes (no network)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from rulecam.clients.transport import ModelTransportError
from rulecam.contracts import EventRecord, SitePlan
from rulecam.voice_out.errors import SynthesisError
from rulecam.warehouse.api import PlanSource, WarehouseService, warehouse_router
from rulecam.warehouse.models import (
    ShiftDraft,
    TalkSection,
    ToolboxDraft,
    WarningDraft,
)
from rulecam.warehouse.talks import (
    ShiftReporter,
    Store,
    ToolboxTalkWriter,
    WarningMaker,
    incident_from_records,
    shift_counts,
)
from tests.fakes import FakeClock
from tests.warehouse.conftest import DAY, T02, T02_EVENTS, T02_PLAN, golden_plan

M = TypeVar("M", bound=BaseModel)
NOW = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
EVENT = T02_EVENTS[1]


@dataclass
class FakeSpark:
    """Answers each schema with a fixed draft and records every call."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    fail: bool = False

    def structured(
        self,
        *,
        model: str = "muse-spark-1.3",
        messages: list[dict[str, object]],
        schema: type[M],
        camera_id: str | None = None,
    ) -> M:
        self.calls.append({"model": model, "messages": messages, "schema": schema})
        if self.fail:
            raise ModelTransportError("down")
        drafts: dict[type[BaseModel], BaseModel] = {
            WarningDraft: WarningDraft(text="¡Aléjese de la carretilla!", english="Step away."),
            ToolboxDraft: ToolboxDraft(
                title="Cuatro metros",
                lang="es",
                minutes=2,
                sections=[TalkSection(heading="Qué pasó", text="Una persona pasó a 3,99 m.")],
                key_points=["Mantener 4 m"],
                questions=["¿A qué distancia?"],
                illustration_prompt="a driver and a walker waving across a wide yellow gap",
            ),
            ShiftDraft: ShiftDraft(
                headline="Two near misses", summary="Both confirmed.", lang="en"
            ),
        }
        return schema.model_validate(drafts[schema].model_dump())


class FakeAttach:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def attach(self, path: Path) -> dict[str, object]:
        self.paths.append(path)
        return {"type": "input_image", "file_id": path.name}


@dataclass
class FakeImage:
    out: Path
    fail: bool = False

    def generate(self, prompt: str) -> Any:
        if self.fail:
            raise ModelTransportError("image down")
        self.out.write_bytes(b"\x89PNG fake")

        @dataclass
        class Result:
            path: Path

        return Result(self.out)


class FakeTts:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def synth(self, text: str, lang: str) -> bytes:
        if self.fail:
            raise SynthesisError("no voice for that language")
        return b"RIFF....WAVE"


class FakeTranscriber:
    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        return [{"text": "Aléjese de la carretilla"}]


@pytest.fixture
def service(tmp_path: Path, chain_dir: Path, strips_root: Path) -> WarehouseService:
    plan = golden_plan(T02)
    spark = FakeSpark()
    store = Store(tmp_path / "warehouse")
    clock = FakeClock(NOW)
    regressions = tmp_path / "regressions"
    regressions.mkdir()
    return WarehouseService(
        chain_dir=chain_dir,
        regressions_dir=regressions,
        stills_root=strips_root,
        plans=PlanSource(lambda: [(plan, True)], lambda: frozenset(r.id for r in plan.rules)),
        store=store,
        warnings=WarningMaker(
            spark,
            store=store,
            clock=clock,
            tts=[("Meta MMS-TTS", FakeTts(fail=True)), ("system voice", FakeTts())],
            transcriber=FakeTranscriber(),
        ),
        talks=ToolboxTalkWriter(
            spark,
            store=store,
            clock=clock,
            attach=FakeAttach(),
            image=FakeImage(tmp_path / "drawn.png"),
        ),
        shifts=ShiftReporter(spark, store=store, clock=clock),
    )


@pytest.fixture
def client(service: WarehouseService) -> TestClient:
    app = FastAPI()
    app.include_router(warehouse_router(service))
    return TestClient(app)


def test_story_and_near_misses_routes(client: TestClient) -> None:
    story = client.get("/warehouse/story").json()
    assert story["plan_id"] == T02_PLAN
    assert story["event_id"] == EVENT
    steps = {s["key"]: s for s in story["steps"]}
    assert steps["approve"]["status"] == "done"
    assert steps["evidence"]["status"] == "done"
    rows = client.get("/warehouse/near-misses", params={"day": DAY.isoformat()}).json()
    assert [r["event_id"] for r in rows] == [EVENT, T02_EVENTS[0]]
    assert client.get("/warehouse/story", params={"plan_id": "plan-nope"}).status_code == 404


def test_heatmap_without_positions_counts_unplaced(client: TestClient) -> None:
    heat = client.get("/warehouse/heatmap").json()
    assert heat["camera_id"] == "phone-97"
    assert heat["unplaced"] == 2
    assert heat["background"] is None
    assert client.get("/warehouse/heatmap/phone-97/frame").status_code == 404


def test_warning_is_written_spoken_by_the_next_engine_and_checked(client: TestClient) -> None:
    assert client.get(f"/warehouse/incidents/{EVENT}/warning").status_code == 404
    made = client.post(f"/warehouse/incidents/{EVENT}/warning", json={"lang": "es"}).json()
    assert made["text"] == "¡Aléjese de la carretilla!"
    assert made["tts"] == "system voice"
    assert made["heard"] == "Aléjese de la carretilla"
    assert made["roundtrip_ok"] is True
    audio = client.get(f"/warehouse/files/{made['audio']}")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert client.get(f"/warehouse/incidents/{EVENT}/warning?lang=es").json() == made
    story = client.get("/warehouse/story").json()
    warning = next(s for s in story["steps"] if s["key"] == "warning")
    assert "Aléjese" in warning["detail"]


def test_toolbox_talk_reads_records_and_frames_and_is_drawn(
    client: TestClient, service: WarehouseService
) -> None:
    talk = client.post(f"/warehouse/incidents/{EVENT}/toolbox-talk", json={"lang": "es"}).json()
    assert talk["title"] == "Cuatro metros"
    assert talk["illustration"] == f"talk-{EVENT}-es.png"
    assert talk["image_model"] == "muse-image"
    assert client.get(f"/warehouse/files/{talk['illustration']}").status_code == 200
    assert client.get(f"/warehouse/incidents/{EVENT}/toolbox-talk?lang=es").status_code == 200
    writer = service.talks
    assert writer is not None
    spark = writer._spark
    assert isinstance(spark, FakeSpark)
    sent = spark.calls[-1]["messages"][1]["content"]
    assert sum(1 for part in sent if part.get("type") == "input_image") == 3
    assert "3.991023" in sent[0]["text"]


def test_shift_report_counts_the_day_and_keeps_the_chain_root(client: TestClient) -> None:
    report = client.post("/warehouse/shift-report", json={"day": DAY.isoformat()}).json()
    assert report["headline"] == "Two near misses"
    assert report["counts"]["alerts"] == 2
    assert report["counts"]["by_rule"]["keep-people-4m-from-moving-forklift"] == 2
    assert len(report["chain_root"]) == 64
    got = client.get("/warehouse/shift-report", params={"day": DAY.isoformat()})
    assert got.status_code == 200


def test_model_failures_and_missing_writers(tmp_path: Path, service: WarehouseService) -> None:
    assert service.warnings is not None
    assert service.talks is not None
    failing = FakeSpark(fail=True)
    service.warnings._spark = failing
    app = FastAPI()
    app.include_router(warehouse_router(service))
    client = TestClient(app)
    assert client.post(f"/warehouse/incidents/{EVENT}/warning", json={}).status_code == 502
    assert client.post("/warehouse/incidents/ev-missing/toolbox-talk", json={}).status_code == 404
    service.shifts = None
    assert client.post("/warehouse/shift-report", json={}).status_code == 503
    assert client.get("/warehouse/files/..%2Fsecret").status_code == 404


def test_drawing_failure_keeps_the_talk(tmp_path: Path, t02_records: list[EventRecord]) -> None:
    store = Store(tmp_path / "w")
    writer = ToolboxTalkWriter(
        FakeSpark(), store=store, clock=FakeClock(NOW), image=FakeImage(tmp_path / "x", True)
    )
    incident = incident_from_records(t02_records, EVENT, None)
    assert incident is not None
    assert incident.frames == []
    talk = writer.write(incident, "es")
    assert talk.illustration is None
    assert talk.illustration_error == "ModelTransportError"
    no_image = ToolboxTalkWriter(FakeSpark(), store=store, clock=FakeClock(NOW))
    assert no_image.write(incident, "es").illustration_error == "Muse Image is not configured"
    saved = json.loads((tmp_path / "w" / "talks" / f"{EVENT}.es.json").read_text())
    assert saved["event_id"] == EVENT


def test_shift_counts_tally_decisions_disputes_and_labels(
    t02_records: list[EventRecord],
) -> None:
    counts = shift_counts(t02_records, DAY, [])
    assert counts.candidates >= 2
    assert counts.alerts == 2
    assert counts.disputes == 0
    assert counts.closest_m is None


def test_plan_source_picks_the_newest_approved_distance_plan() -> None:
    plan = golden_plan(T02)
    other = SitePlan.model_validate({**plan.model_dump(mode="json"), "plan_id": "plan-new"})
    source = PlanSource(lambda: [(plan, True), (other, False)], frozenset)
    picked = source.pick(None)
    assert picked is not None
    assert picked[0].plan_id == T02_PLAN
    assert source.pick("plan-new") == (other, False)
    assert PlanSource(list, frozenset).pick(None) is None
