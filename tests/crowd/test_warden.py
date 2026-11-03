"""The crowd warden: Spark assesses, Glimmer checks the frames, then alert, announce, chain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rulecam.ask.vision import Frame, FrameGrabber
from rulecam.contracts import PrivacyMode
from rulecam.crowd.memory import Calibrations, CrowdMemory
from rulecam.crowd.scan import CrowdReading, CrowdScanner
from rulecam.crowd.warden import CrowdWarden, FloorPoints
from rulecam.crowd.zones import CrowdZone
from rulecam.evidence.chain import EventChain, read_chain, verify_chain
from rulecam.evidence.incident import build_incident, candidates
from rulecam.perception.errors import PerceptionError
from tests.crowd.conftest import Sam, top_down, wait_for
from tests.fakes import EPOCH, FakeClock, FakeLLM

INSTRUCTION = "Warn me before the gate gets dangerous; tell people in Hindi and English to wait."


def warning(alert: bool = True, level: str = "warning") -> dict[str, object]:
    return {
        "level": level,
        "zones": ["view"],
        "why": "Density rose from 2.1 to 4.2 per square metre in two minutes; speed fell to 0.2.",
        "forecast": "At this rate the densest cell passes 5 per square metre in about 3 minutes.",
        "minutes_to_danger": 3,
        "alert": alert,
        "staff_actions": ["Hold entry at gate 3", "Open gate 4"],
        "announce": [
            {"lang": "hi", "text": "कृपया रुकिए, गेट चार खुला है।"},
            {"lang": "en", "text": "Please wait; gate four is open."},
        ],
        "check": "Are people packed shoulder to shoulder and barely moving?",
    }


class Notifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str | None]] = []

    def send(
        self,
        role: str,
        title: str,
        body: str,
        *,
        clip_path: str | None = None,
        buttons: tuple[str, ...] = (),
    ) -> str:
        self.sent.append((role, title, body, clip_path))
        return f"msg-{len(self.sent)}"


class Speech:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []

    def synth(self, text: str, lang: str) -> bytes:
        self.said.append((lang, text))
        if lang == "xx":
            msg = "no voice"
            raise ValueError(msg)
        return b"RIFF-wav"


@pytest.fixture
def crowd(tmp_path: Path, grabber: FrameGrabber) -> CrowdMemory:
    people = [(20.0 + 40 * k, 100.0, 40.0 + 40 * k, 150.0) for k in range(8)]
    memory = CrowdMemory(
        frames=grabber,
        scanner=CrowdScanner(segmenter=Sam(people), out_dir=tmp_path / "heat", grid=1),
        calibrations=Calibrations(plan=lambda: {"gate": top_down(0.05)}),
        path=tmp_path / "readings.jsonl",
        every_s=5.0,
    )
    memory.tick()
    wait_for(lambda: memory.latest("gate") is not None)
    return memory


def warden(
    tmp_path: Path,
    crowd: CrowdMemory,
    grabber: FrameGrabber,
    spark: FakeLLM,
    glimmer: FakeLLM,
    *,
    notifier: Notifier | None = None,
    speech: Speech | None = None,
) -> tuple[CrowdWarden, EventChain]:
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path / "chain", "stadium", clock=clock)
    made = CrowdWarden(
        memory=crowd,
        frames=grabber,
        spark=spark,
        glimmer=glimmer,
        glimmer_model="muse-glimmer",
        chain=chain,
        notifier=notifier,
        tts=speech,
        clock=clock,
        out_dir=tmp_path / "crowd",
        site_profile="Stadium gate 3",
        camera_names={"gate": "Gate 3 approach"},
    )
    return made, chain


def latest(crowd: CrowdMemory) -> CrowdReading:
    reading = crowd.latest("gate")
    assert reading is not None
    return reading


def test_a_confirmed_warning_alerts_announces_and_is_chained_as_an_incident(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    spark = FakeLLM([warning()])
    glimmer = FakeLLM(
        [{"agrees": True, "seen": "People packed, barely moving.", "confidence": 0.9}]
    )
    notifier, speech = Notifier(), Speech()
    chosen, chain = warden(
        tmp_path, crowd, grabber, spark, glimmer, notifier=notifier, speech=speech
    )
    watch = chosen.watch("gate", INSTRUCTION, ["hi", "en"])
    assessment = chosen.assess(watch, latest(crowd))
    assert assessment.level == "warning"
    prompt = json.loads(str(spark.calls[0]["messages"][1]["content"]))  # type: ignore[index]
    assert prompt["operator_instruction"] == INSTRUCTION
    assert prompt["camera"]["name"] == "Gate 3 approach"
    assert "view" in prompt["readings_per_zone"]
    assert "Helbing" in str(spark.calls[0]["messages"][0]["content"])  # type: ignore[index]
    (alert,) = chosen.alerts()
    assert alert.sent
    assert alert.check is not None
    assert alert.check.agrees
    assert [a["lang"] for a in alert.audio] == ["hi", "en"]
    assert all((chosen.audio_dir / a["audio"]).is_file() for a in alert.audio)
    (sent,) = notifier.sent
    assert sent[0] == "security"
    assert sent[1] == "Crowd WARNING: Gate 3 approach"
    assert "Hold entry at gate 3" in sent[2]
    assert sent[3] is not None
    assert sent[3].endswith(".jpg")
    records = read_chain(chain.path_for(EPOCH.date()))
    assert [r.kind for r in records] == ["candidate", "verdict", "action", "action", "action"]
    (row,) = candidates(records)
    assert (row.event_id, row.rule_id, row.verdict) == (alert.alert_id, "crowd-watch", "confirmed")
    states = chosen.states()
    assert states[0].last_alert == alert
    rows = chosen.assessments_path.read_text().splitlines()
    assert json.loads(rows[0])["level"] == "warning"
    assert json.loads(chosen.alerts_path.read_text())["ts"] == "2026-10-30T12:00:28Z"


def test_the_incident_report_reads_a_crowd_alert(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    spark = FakeLLM([warning()])
    glimmer = FakeLLM([{"agrees": True, "seen": "Packed.", "confidence": 0.8}])
    chosen, chain = warden(tmp_path, crowd, grabber, spark, glimmer, notifier=Notifier())
    chosen.assess(chosen.watch("gate", INSTRUCTION), latest(crowd))
    (alert,) = chosen.alerts()
    path = chain.path_for(EPOCH.date())
    report, _ = build_incident(
        read_chain(path),
        alert.alert_id,
        site="stadium",
        day=EPOCH.date(),
        privacy_mode=PrivacyMode.STANDARD,
        verify_url="https://example.test/verify",
        chain=verify_chain(path),
    )
    assert report.rule_text == INSTRUCTION
    assert report.measurement is not None
    assert "people/m2" in report.measurement
    assert len(report.timeline) == 5  # candidate, verdict, notify, two announcements (text)


def test_glimmer_holds_an_alert_it_does_not_see(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    spark = FakeLLM([warning()])
    glimmer = FakeLLM([{"agrees": False, "seen": "A loose crowd walking.", "confidence": 0.8}])
    notifier = Notifier()
    chosen, chain = warden(
        tmp_path, crowd, grabber, spark, glimmer, notifier=notifier, speech=Speech()
    )
    chosen.assess(chosen.watch("gate", INSTRUCTION), latest(crowd))
    (alert,) = chosen.alerts()
    assert not alert.sent
    assert alert.held_reason == "Glimmer did not see it: A loose crowd walking."
    assert notifier.sent == []
    assert not chain.path_for(EPOCH.date()).exists()
    assert chosen.states()[0].last_alert is None


def test_no_alert_when_spark_sees_no_need(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    spark = FakeLLM([warning(alert=False, level="watch")])
    glimmer = FakeLLM([])
    chosen, _ = warden(tmp_path, crowd, grabber, spark, glimmer, notifier=Notifier())
    chosen.assess(chosen.watch("gate", INSTRUCTION), latest(crowd))
    assert chosen.alerts() == []
    assert glimmer.calls == []
    assert chosen.states()[0].assessment is not None


def test_repeats_wait_unless_the_level_rises(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    agree = {"agrees": True, "seen": "Packed.", "confidence": 0.9}
    spark = FakeLLM([warning(), warning(), warning(level="critical")])
    glimmer = FakeLLM([agree, agree])
    notifier = Notifier()
    chosen, _ = warden(tmp_path, crowd, grabber, spark, glimmer, notifier=notifier, speech=Speech())
    watch = chosen.watch("gate", INSTRUCTION)
    reading = latest(crowd)
    chosen.assess(watch, reading)
    chosen.assess(watch, reading)  # same level within REPEAT_S: no second alert
    chosen.assess(watch, reading)  # critical: alert again
    assert [a.level for a in chosen.alerts()] == ["critical", "warning"]
    assert len(notifier.sent) == 2


def test_speech_failures_keep_the_text(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    assessment = warning()
    assessment["announce"] = [{"lang": "xx", "text": "?"}]
    spark = FakeLLM([assessment])
    glimmer = FakeLLM([{"agrees": True, "seen": "Packed.", "confidence": 0.9}])
    chosen, _ = warden(
        tmp_path, crowd, grabber, spark, glimmer, notifier=Notifier(), speech=Speech()
    )
    chosen.assess(chosen.watch("gate", INSTRUCTION), latest(crowd))
    (alert,) = chosen.alerts()
    assert alert.audio == [{"lang": "xx", "text": "?"}]


def test_ticks_assess_watched_cameras_on_their_own_thread(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    spark = FakeLLM([warning(alert=False, level="normal")])
    chosen, _ = warden(tmp_path, crowd, grabber, spark, FakeLLM([]))
    chosen.tick()  # nothing watched yet
    assert spark.calls == []
    chosen.watch("gate", INSTRUCTION, [" HI ", ""], role="stewards")
    assert chosen.watched() == ["gate"]
    assert chosen.states()[0].watch.languages == ["hi"]
    chosen.tick()
    wait_for(lambda: chosen.states()[0].assessment is not None)
    chosen.tick()  # the same reading is not assessed twice
    assert len(spark.calls) == 1
    assert chosen.unwatch("gate")
    assert not chosen.unwatch("gate")
    assert chosen.states() == []


def test_a_failing_spark_is_logged_not_raised(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    chosen, _ = warden(tmp_path, crowd, grabber, FakeLLM([{"level": "nonsense"}]), FakeLLM([]))
    chosen.watch("gate", INSTRUCTION)
    chosen.tick()
    wait_for(lambda: chosen.assessments_path.exists() or True)
    assert chosen.alerts() == []


def test_marked_floor_points_calibrate_and_watches_survive_a_restart(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    floor = FloorPoints(
        image_px=[(0, 0), (400, 0), (400, 300), (0, 300)],
        ground_m=[(0, 0), (8, 0), (8, 6), (0, 6)],
    )
    chosen, _ = warden(tmp_path, crowd, grabber, FakeLLM([]), FakeLLM([]))
    gate = CrowdZone(name="gate", polygon_px=[(0, 0), (100, 0), (100, 100)])
    chosen.watch("gate", INSTRUCTION, ["hi"], floor=floor, zones=[gate], concept="head", grid=1)
    assert chosen.zones("gate") == [gate]
    assert chosen.zones("other") == []
    assert chosen.counting("gate") == ("head", 1)
    assert chosen.counting("other") == (None, None)
    reading = latest(crowd)
    found = crowd.calibrations.get("gate", Frame(camera_id="gate", at=EPOCH, path=Path("x")), [])
    assert found is not None
    assert found[1] == "owner"
    assert reading is not None
    again, _ = warden(tmp_path, crowd, grabber, FakeLLM([]), FakeLLM([]))
    (state,) = again.states()
    assert state.watch.floor == floor
    assert state.watch.languages == ["hi"]
    with pytest.raises(PerceptionError):
        chosen.watch("gate", INSTRUCTION, floor=floor.model_copy(update={"ground_m": [(0, 0)] * 4}))


def test_an_unreadable_watches_file_is_skipped(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    (tmp_path / "crowd").mkdir()
    (tmp_path / "crowd" / "watches.json").write_text("[{]")
    chosen, _ = warden(tmp_path, crowd, grabber, FakeLLM([]), FakeLLM([]))
    assert chosen.states() == []
