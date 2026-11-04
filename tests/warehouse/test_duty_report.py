"""Near miss -> refresher training duty (chained when done) and spoken near-miss reports."""

from __future__ import annotations

import io
import shutil
import wave
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from rulecam.clients.meta_hosted.grounding import Grounded, SearchSource, normal_url
from rulecam.clients.meta_hosted.voice import check_wav
from rulecam.contracts import EventRecord, RequestItem
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.warehouse.duty import DutyWriter, RelinkDraft
from rulecam.warehouse.models import ClauseDraft, DutyDraft, NearMiss, ReportDraft
from rulecam.warehouse.near_misses import near_misses
from rulecam.warehouse.report import NearMissReporter, ReportError, to_wav16k
from rulecam.warehouse.story import build_story
from rulecam.warehouse.talks import Store, incident_from_records
from tests.fakes import FakeClock
from tests.warehouse.conftest import T02, T02_EVENTS, T02_PLAN, golden_plan

M = TypeVar("M", bound=BaseModel)
NOW = datetime(2026, 11, 2, 23, 50, tzinfo=UTC)
EVENT = T02_EVENTS[1]
OSHA = "https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.178"


@dataclass
class FakeSpark:
    answers: dict[type[BaseModel], BaseModel]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def structured(
        self,
        *,
        model: str = "muse-spark-1.3",
        messages: list[dict[str, object]],
        schema: type[M],
        camera_id: str | None = None,
    ) -> M:
        self.calls.append({"messages": messages, "schema": schema})
        return schema.model_validate(self.answers[schema].model_dump())

    def grounded(self, *, messages: list[dict[str, object]], schema: type[M]) -> Grounded[M]:
        answer = schema.model_validate(self.answers[schema].model_dump())
        return Grounded(
            answer=answer,
            sources=(SearchSource(url=OSHA, title="1910.178 - Powered industrial trucks"),),
            _index=frozenset({normal_url(OSHA)}),
        )


CLAUSE = ClauseDraft(
    clause="29 CFR 1910.178(l)(4)(ii)(B)",
    title="Refresher training",
    text="The operator has been involved in an accident or near-miss incident;",
    source_url=OSHA,
)


@pytest.fixture
def chain(tmp_path: Path) -> EventChain:
    return EventChain(tmp_path / "chain", "demo", clock=FakeClock(NOW))


def test_duty_reads_the_unit_number_and_is_chained_when_done(
    tmp_path: Path, t02_records: list[EventRecord], chain: EventChain
) -> None:
    spark = FakeSpark(
        {
            ClauseDraft: CLAUSE,
            DutyDraft: DutyDraft(
                applies=True,
                why="A person on foot came within 4 m of the moving forklift.",
                vehicle_unit="HELI 07",
                unit_evidence="number painted on the counterweight, frame 2",
                training_focus=["pedestrian right of way", "eye contact"],
                due="before the operator drives again",
            ),
        }
    )
    writer = DutyWriter(
        spark, search=spark, store=Store(tmp_path / "w"), clock=FakeClock(NOW), chain=chain
    )
    incident = incident_from_records(t02_records, EVENT, None)
    assert incident is not None
    duty = writer.raise_duty(incident)
    assert duty.status == "due"
    assert duty.vehicle_unit == "HELI 07"
    assert duty.clause.source_retrieved is True
    assert duty.clause.source_title.startswith("1910.178")
    assert writer.every() == [duty]
    assert writer.get("ev-unknown") is None
    assert writer.done("ev-unknown", "owner") is None
    closed = writer.done(EVENT, "owner")
    assert closed is not None
    assert closed.status == "done"
    assert writer.done(EVENT, "owner") == closed  # idempotent: one chained record
    records = read_chain(chain.path_for(NOW.date()))
    assert [r.payload["label"] for r in records] == ["refresher_training_done"]
    assert closed.done_hash == records[0].hash
    marked = near_misses([*t02_records, *records], plan_id=T02_PLAN)
    assert next(n for n in marked if n.event_id == EVENT).training_done is True
    story = build_story(
        golden_plan(T02), approved=True, active=True, near=marked, chain=None, duty=closed
    )
    step = next(s for s in story.steps if s.key == "duty")
    assert step.status == "done"
    assert "HELI 07" in step.detail


def test_clause_without_search_is_marked_unverified(
    tmp_path: Path, t02_records: list[EventRecord], chain: EventChain
) -> None:
    spark = FakeSpark(
        {DutyDraft: DutyDraft(applies=False, why="The person is the seated driver.", due="-")}
    )
    writer = DutyWriter(
        spark, search=None, store=Store(tmp_path / "w"), clock=FakeClock(NOW), chain=chain
    )
    incident = incident_from_records(t02_records, EVENT, None)
    assert incident is not None
    duty = writer.raise_duty(incident)
    assert duty.status == "not_required"
    assert duty.clause.source_retrieved is False


class FakeTranscriber:
    def __init__(self, text: str) -> None:
        self.text = text
        self.bias: list[str] | None = None

    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        self.bias = lang_bias
        return [{"text": self.text}] if self.text else []


def _near(t02_records: list[EventRecord]) -> Sequence[NearMiss]:
    return near_misses(t02_records, plan_id=T02_PLAN)


def test_spoken_report_is_matched_to_the_measured_near_miss_and_chained(
    tmp_path: Path, t02_records: list[EventRecord], chain: EventChain
) -> None:
    spark = FakeSpark(
        {
            ReportDraft: ReportDraft(
                lang="es",
                summary_en="A forklift passed very close to a worker in the main aisle.",
                what_happened="Una carretilla pasó muy cerca de un trabajador.",
                where="pasillo principal",
                minutes_ago=5,
                matches_event_id=EVENT,
                match_reason="Same aisle and time; the camera measured 3.99 m.",
                proposed_rule="Mantener a las personas a 4 m de las carretillas en el pasillo.",
                proposed_rule_en="Keep people 4 m from forklifts in the main aisle.",
            )
        }
    )
    windows: list[tuple[datetime, datetime]] = []

    def window(t0: datetime, t1: datetime) -> Sequence[NearMiss]:
        windows.append((t0, t1))
        return _near(t02_records)

    transcriber = FakeTranscriber("Una carretilla pasó muy cerca de mí en el pasillo principal")
    reporter = NearMissReporter(
        spark,
        transcriber,
        store=Store(tmp_path / "w"),
        clock=FakeClock(NOW),
        chain=chain,
        near=window,
        convert=lambda audio: audio,
    )
    report = reporter.report(b"RIFF", lang="es")
    assert transcriber.bias == ["es"]
    assert windows == [(NOW - timedelta(minutes=30), NOW)]
    assert report.matches_event_id == EVENT
    assert report.measured_m == pytest.approx(3.991023)
    assert set(report.candidates) == set(T02_EVENTS)
    labels = read_chain(chain.path_for(NOW.date()))
    assert labels[0].payload["label"] == "worker_report"
    assert "transcript" not in labels[0].payload
    assert report.chained_hash == labels[0].hash
    assert reporter.every() == [report]
    proposed = reporter.proposed(report.report_id, "plan-new")
    assert proposed is not None
    assert proposed.plan_id == "plan-new"
    assert reporter.proposed("nr-000000000000", "plan-x") is None
    item = RequestItem(kind="text", text=report.proposed_rule, lang="es")
    assert item.text is not None


def test_report_without_speech_or_with_unknown_match(
    tmp_path: Path, t02_records: list[EventRecord], chain: EventChain
) -> None:
    draft = ReportDraft(
        lang="en",
        summary_en="s",
        what_happened="w",
        matches_event_id="ev-made-up",
        proposed_rule="r",
        proposed_rule_en="r",
    )
    reporter = NearMissReporter(
        FakeSpark({ReportDraft: draft}),
        FakeTranscriber("a forklift nearly hit me"),
        store=Store(tmp_path / "w"),
        clock=FakeClock(NOW),
        chain=chain,
        near=lambda t0, t1: _near(t02_records),
        convert=lambda audio: audio,
    )
    report = reporter.report(b"x")
    assert report.matches_event_id is None  # an id the cameras never measured is not kept
    assert report.chained_hash is None
    silent = NearMissReporter(
        FakeSpark({ReportDraft: draft}),
        FakeTranscriber(""),
        store=Store(tmp_path / "w"),
        clock=FakeClock(NOW),
        chain=chain,
        near=lambda t0, t1: [],
        convert=lambda audio: audio,
    )
    with pytest.raises(ReportError):
        silent.report(b"x")


def test_to_wav16k_rejects_what_ffmpeg_cannot_decode() -> None:
    with pytest.raises(ReportError):
        to_wav16k(b"not audio at all")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_to_wav16k_gives_voice_transcribe_a_wav_with_its_length() -> None:
    source = io.BytesIO()
    with wave.open(source, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44_100)
        wav.writeframes(b"\x00\x01" * 2 * 44_100)
    converted = to_wav16k(source.getvalue())
    check_wav(converted)
    with wave.open(io.BytesIO(converted), "rb") as wav:
        assert (wav.getnchannels(), wav.getframerate()) == (1, 16_000)
        assert wav.getnframes() == pytest.approx(16_000, abs=200)


def test_clause_cited_off_search_is_relinked_by_spark(tmp_path: Path, chain: EventChain) -> None:
    cited_elsewhere = CLAUSE.model_copy(update={"source_url": "https://example.org/not-returned"})
    spark = FakeSpark({ClauseDraft: cited_elsewhere, RelinkDraft: RelinkDraft(index=0, why="ok")})
    writer = DutyWriter(
        spark, search=spark, store=Store(tmp_path / "w"), clock=FakeClock(NOW), chain=chain
    )
    clause = writer.clause()
    assert clause.source_retrieved is True
    assert clause.source_url == OSHA
    assert writer.clause() == clause  # saved: looked up once


def test_public_voice_report_says_only_what_was_heard_and_has_a_quota(
    tmp_path: Path, t02_records: list[EventRecord], chain: EventChain
) -> None:
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from rulecam.warehouse.api import PlanSource, WarehouseService, warehouse_public_router  # noqa: PLC0415
    from rulecam.worker.disputes import DailyLimiter  # noqa: PLC0415

    draft = ReportDraft(
        lang="es",
        summary_en="close pass",
        what_happened="Pasó muy cerca",
        matches_event_id=EVENT,
        proposed_rule="r",
        proposed_rule_en="r",
    )
    reporter = NearMissReporter(
        FakeSpark({ReportDraft: draft}),
        FakeTranscriber("pasó muy cerca"),
        store=Store(tmp_path / "w"),
        clock=FakeClock(NOW),
        chain=chain,
        near=lambda t0, t1: _near(t02_records),
        convert=lambda audio: audio,
    )
    service = WarehouseService(
        chain_dir=tmp_path / "c",
        regressions_dir=tmp_path / "r",
        stills_root=None,
        plans=PlanSource(list, frozenset),
        store=Store(tmp_path / "w"),
        reports=reporter,
    )
    app = FastAPI()
    limiter = DailyLimiter(FakeClock(NOW), per_client=1, per_day=10)
    app.include_router(
        warehouse_public_router(service, client_address=lambda r: "1.2.3.4", limiter=limiter)
    )
    client = TestClient(app)
    reply = client.post("/warehouse/public/reports/voice?lang=es", content=b"RIFF")
    assert reply.json() == {
        "heard": "pasó muy cerca",
        "what_happened": "Pasó muy cerca",
        "matched": True,
        "measured_m": pytest.approx(3.991023),
    }
    assert "event" not in reply.text
    assert client.post("/warehouse/public/reports/voice", content=b"RIFF").status_code == 429
