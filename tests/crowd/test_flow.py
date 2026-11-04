"""The connected crowd flow: projection, venue actions, steward acks, the report, the setup."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from rulecam.act.escalation import EscalationManager
from rulecam.act.notify import LanNotifier
from rulecam.ask.vision import Frame, FrameGrabber
from rulecam.bus import Bus
from rulecam.crowd.forecast import forecast
from rulecam.crowd.memory import CrowdMemory
from rulecam.crowd.report import Truth, after_action, build_report
from rulecam.crowd.routes import check_routes, recheck_message
from rulecam.crowd.scan import CrowdReading, CrowdScanner, ZoneReading
from rulecam.crowd.setup import propose, role_id
from rulecam.crowd.venue import Place, Venue
from rulecam.crowd.warden import CrowdWarden
from rulecam.crowd.zones import CrowdZone
from rulecam.evidence.chain import EventChain, read_chain
from tests.crowd.conftest import Sam, top_down, wait_for
from tests.crowd.test_warden import INSTRUCTION, Gate, Speech, crowd, latest, warning
from tests.fakes import EPOCH, FakeClock, FakeLLM

__all__ = ["crowd"]  # the fixture, shared with the warden tests

VENUE = Venue(
    description="SAMPLE layout: one hall entrance with a narrow gate and a side door.",
    places=[
        Place(name="Gate A", kind="gate", leads_to=["Hall"], camera_id="gate", zone="view"),
        Place(name="Side door B", kind="exit", leads_to=["Hall"], note="2 m wide, closed"),
        Place(name="Hall", kind="hall"),
    ],
    sample=True,
)


def reading(seconds: float, density: float, people: int, zone: str = "gate") -> CrowdReading:
    return CrowdReading(
        camera_id="gate",
        at=EPOCH + timedelta(seconds=seconds),
        concept="head",
        grid=1,
        people=people,
        calibration="owner",
        zones=[
            ZoneReading(zone="view", people=people),
            ZoneReading(zone=zone, people=people, area_m2=4.0, density_ppm2=density),
        ],
    )


def test_the_projection_measures_trend_inflow_and_time_to_each_reference_density() -> None:
    history = [reading(10.0 * k, 2.0 + 0.5 * k, 8 + 2 * k) for k in range(7)]  # +3/m² a minute
    (_, gate) = forecast(history)
    assert gate.zone == "gate"
    assert gate.density_ppm2 == 5.0
    assert gate.per_min == pytest.approx(3.0)
    assert gate.inflow_per_min == pytest.approx(12.0)
    assert gate.eta_s["6"] == pytest.approx(20.0)
    assert gate.eta_s["4"] is None  # already past it
    assert gate.above_s["4"] == 20.0
    assert gate.above_s["5"] == 0.0
    thinning = forecast([reading(10.0 * k, 5.0 - 0.5 * k, 20 - k) for k in range(5)])[1]
    assert thinning.eta_s["6"] is None
    assert forecast([]) == []


def escalating(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber, spark: FakeLLM
) -> tuple[CrowdWarden, LanNotifier, FakeClock, EventChain, EscalationManager]:
    clock = FakeClock(EPOCH)
    bus = Bus()
    chain = EventChain(tmp_path / "chain", "stadium", clock=clock)
    store = LanNotifier(bus=bus, clock=clock)
    escalation = EscalationManager(notifier=store, clock=clock, chain=chain, bus=bus)
    made = CrowdWarden(
        memory=crowd,
        frames=grabber,
        spark=spark,
        glimmer=FakeLLM([{"agrees": True, "seen": "Packed.", "confidence": 0.8}]),
        glimmer_model="muse-glimmer",
        chain=chain,
        notifier=None,
        tts=Speech(),
        clock=clock,
        out_dir=tmp_path / "crowd",
        camera_names={"gate": "Gate A"},
        escalation=escalation,
        alert_store=store,
    )
    return made, store, clock, chain, escalation


def with_actions() -> dict[str, object]:
    return {
        **warning(),
        "actions": [
            {"do": "close", "target": "Gate A", "role": "stewards", "why": "4.2/m² rising"},
            {"do": "open", "target": "Side door B", "role": "gate staff", "why": "hall has room"},
        ],
    }


def test_spark_plans_on_the_venue_and_an_unanswered_alert_escalates_until_a_steward_taps(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    spark = FakeLLM([with_actions()])
    chosen, store, clock, chain, escalation = escalating(tmp_path, crowd, grabber, spark)
    chosen.set_venue(VENUE)
    watch = chosen.watch(
        "gate",
        INSTRUCTION,
        ["hi", "en"],
        role="stewards",
        escalate_to=["safety_officer"],
        ack_within_s=30.0,
    )
    chosen.assess(watch, latest(crowd))
    prompt = json.loads(str(spark.calls[0]["messages"][1]["content"]))  # type: ignore[index]
    places = {p["name"]: p for p in prompt["venue"]["places"]}
    assert places["Gate A"]["now"]["people"] == 8
    assert "now" not in places["Side door B"]
    assert prompt["projection_per_zone"][0]["zone"] == "view"
    (alert,) = chosen.alerts()
    assert alert.roles == ["stewards", "safety_officer"]
    assert alert.notified == "escalation to stewards then safety_officer"
    assert alert.sent_at == clock.now()
    (first,) = store.list()
    assert first.role == "stewards"
    assert "close Gate A (stewards)" in first.body
    state = chosen.ack_state(alert.alert_id)
    assert (state.status, state.escalating) == ("open", True)
    clock.advance(31.0)
    escalation.tick()
    assert [n.role for n in chosen.ack_state(alert.alert_id).notified] == [
        "stewards",
        "safety_officer",
    ]
    done = chosen.ack(alert.alert_id, "safety_officer")
    assert done.status == "acknowledged"
    assert not done.escalating
    assert done.taps[0]["role"] == "safety_officer"
    kinds = [r.kind for r in read_chain(chain.path_for(EPOCH.date()))]
    assert kinds.count("ack") == 1
    assert "escalate" in {r.payload.get("kind") for r in read_chain(chain.path_for(EPOCH.date()))}
    with pytest.raises(KeyError):
        chosen.ack("crowd-nope", "stewards")
    restarted, *_ = escalating(tmp_path, crowd, grabber, FakeLLM([]))
    assert restarted.venue == VENUE


def test_the_report_has_the_timeline_latency_evidence_and_the_ground_truth_score(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    spark = FakeLLM([with_actions()])
    chosen, _, clock, *_ = escalating(tmp_path, crowd, grabber, spark)
    watch = chosen.watch("gate", INSTRUCTION, ["hi", "en"], role="stewards")
    now = latest(crowd)
    clock.advance((now.at - clock.now()).total_seconds() + 12.0)
    chosen.assess(watch, now)
    (alert,) = chosen.alerts()
    chosen.ack(alert.alert_id, "stewards")
    start = chosen.first_recorded("gate")
    assert start is not None
    clip = (now.at - start).total_seconds()
    truth = Truth(
        source="SAMPLE hand count",
        zones={"view": {"area_m2": 4.0, "t": [0.0, clip, clip + 5], "people": [0, 10, 3]}},
    )
    chosen.set_truth("gate", truth)
    assert chosen.truth("gate") == truth
    made = build_report(
        camera_id="gate",
        name="Gate A",
        readings_path=crowd.path,
        assessments_path=chosen.assessments_path,
        alerts_path=chosen.alerts_path,
        acks_path=chosen.acks_path,
        chain=chosen.chain,
        clip_start=start,
        truth=chosen.truth("gate"),
        acks={alert.alert_id: chosen.ack_state(alert.alert_id).model_dump(mode="json")},
    )
    assert made.readings == 1
    assert made.zones[0].peak_people == 8
    kinds = [e.kind for e in made.timeline]
    assert {"assessment", "alert", "announcement", "tap", "peak"} <= set(kinds)
    (late,) = made.latency
    assert late.checked == "confirmed"
    assert (late.assess_s, late.alert_s) == (12.0, 12.0)
    assert made.evidence.chain_ok is True
    assert {r["kind"] for r in made.evidence.records} >= {"candidate", "verdict", "ack"}
    assert made.truth is not None
    (scored,) = made.truth
    assert scored.count_ratio == 0.8
    assert scored.count_mae == 2.0
    assert scored.true_peak_ppm2 == 2.5
    linked = {e.kind: e for e in made.timeline if e.seq is not None}
    assert {"alert", "notified", "announcement", "tap"} <= set(linked)
    assert linked["alert"].hash == alert.chain_hash
    assert any("SAM 3.1 counts what the camera sees" in u for u in made.unknowns)
    assert any("owner" in u or "plan" in u for u in made.unknowns)
    summary: dict[str, object] = {
        "headline": "Warned in time.",
        "narrative": "…",
        "cited": [{"text": "Critical at the first reading.", "records": [2]}],
    }
    narrator = FakeLLM([summary])
    told = after_action(narrator, made)
    assert told.headline == "Warned in time."
    assert told.cited[0].records == [2]
    brief = json.loads(str(narrator.calls[0]["messages"][1]["content"]))  # type: ignore[index]
    assert "series" not in brief
    assert "truth_curve_s" not in brief["truth"][0]


class Files:
    def __init__(self) -> None:
        self.attached: list[Path] = []

    def attach(self, path: Path) -> dict[str, object]:
        self.attached.append(path)
        return {"type": "input_image", "file_id": "file-1"}


def test_spark_proposes_the_watch_from_the_instruction_and_the_newest_frame(
    grabber: FrameGrabber,
) -> None:
    draft = {
        "summary": "गेट के सामने की भीड़ पर नज़र रखेंगे।",
        "view": "from above",
        "concept": "head",
        "grid": 2,
        "languages": ["hi", "en"],
        "role": "Gate Stewards",
        "escalate_to": ["safety officer", "9"],
        "zones": [
            {"name": "gate approach", "polygon": [[0.5, 0.2], [1.2, 0.2], [1.0, 0.8]], "why": "x"},
            {"name": "too small", "polygon": [[0.1, 0.1], [0.2, 0.2]], "why": "x"},
        ],
        "places": [{"name": "Gate", "kind": "gate"}],
        "calibration": "Mark four floor points.",
    }
    spark, files = FakeLLM([draft]), Files()
    made = propose(spark, files, grabber, "gate", "गेट पर नज़र रखें", name="Gate A", people=12)
    assert (made.width, made.height) == (400, 300)
    (zone,) = made.zones
    assert zone.polygon_px == [(200.0, 60.0), (400.0, 60.0), (400.0, 240.0)]
    assert made.role == "gate_stewards"
    assert made.escalate_to == ["safety_officer"]
    assert files.attached[0].name == made.frame
    intro = spark.calls[0]["messages"][1]["content"][0]["text"]  # type: ignore[index]
    assert "12 people" in intro
    with pytest.raises(ValueError, match="no recorded video"):
        propose(spark, files, grabber, "nope", "x", name="x")
    with pytest.raises(ValueError, match="images"):
        propose(spark, None, grabber, "gate", "x", name="x")
    assert role_id("  ") == ""


def test_the_fill_clock_counts_people_crossing_the_zone_edge(
    tmp_path: Path, frame_at: Callable[[int, datetime], Frame]
) -> None:
    scanner = CrowdScanner(
        segmenter=Sam([(20.0, 100.0, 40.0, 150.0)]), out_dir=tmp_path / "heat", grid=1
    )
    after = frame_at(15, EPOCH + timedelta(seconds=1.5))
    before = frame_at(10, EPOCH + timedelta(seconds=1.0))  # the scene drifts 20 px/s right
    gate = CrowdZone(name="gate", polygon_px=[(50, 0), (400, 0), (400, 300), (50, 300)])
    reading = scanner.scan(after, before, (top_down(0.05), "owner"), [gate])
    zone = reading.zone("gate")
    assert zone is not None
    assert (zone.in_per_min, zone.out_per_min) == (30.0, 0.0)
    (cell,) = [c for c in reading.cells if c.people]
    flow = cell.flow_px
    assert flow is not None
    assert flow[0] == pytest.approx(20.0, abs=3.0)
    history = [reading.model_copy(update={"at": EPOCH + timedelta(seconds=s)}) for s in (0, 5)]
    (_, projected) = forecast(history)
    assert projected.in_per_min == 30.0
    assert projected.fill_eta_s["2"] is not None


def test_the_route_guard_measures_each_destination() -> None:
    venue = Venue(places=[Place(name="Gate C", camera_id="c", zone="view"), Place(name="Car")])
    filling = [reading(10.0 * k, 3.0 + 0.2 * k, 12 + k) for k in range(4)]
    checks = check_routes(
        [("redirect to Gate C", "gate c"), ("open Car", "Car"), ("x", "Moon")],
        venue,
        lambda camera_id: filling if camera_id == "c" else [],
        source_density=4.0,
    )
    assert [c.measured for c in checks] == [False, False, False]  # "view" has no density here
    assert [c.note for c in checks][1:] == ["no camera sees it", "not in the venue layout"]
    checks = check_routes(
        [("redirect to Gate C", "Gate C")],
        Venue(places=[Place(name="Gate C", camera_id="c", zone="gate")]),
        lambda camera_id: filling,
        source_density=4.0,
    )
    assert checks[0].filling
    assert checks[0].note == "getting denser"
    assert "3.6 people/m² now" in recheck_message(checks)


def test_spark_chooses_again_when_a_destination_is_filling_and_hears_who_answered(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    redirect = {
        **warning(),
        "actions": [
            {"do": "redirect", "target": "Gate A", "role": "stewards", "why": "x", "to": "Gate A"}
        ],
    }
    hold = {
        **warning(),
        "actions": [{"do": "hold", "target": "Outer barrier", "role": "stewards", "why": "y"}],
    }
    spark = FakeLLM([redirect, hold, {**warning(), "level": "critical"}])
    chosen, *_ = escalating(tmp_path, crowd, grabber, spark)
    chosen.set_venue(VENUE)
    watch = chosen.watch("gate", INSTRUCTION, ["en"], role="stewards")
    assessment = chosen.assess(watch, latest(crowd))
    assert assessment.actions[0].do == "hold"
    again = spark.calls[1]["messages"]
    assert "Route check" in str(again[-1]["content"])  # type: ignore[index]
    (alert,) = chosen.alerts()
    (checked,) = alert.route_checks
    assert (checked.to, checked.filling, checked.replaced) == ("Gate A", True, True)
    chosen.assess(watch, latest(crowd))
    prompt = json.loads(str(spark.calls[2]["messages"][1]["content"]))  # type: ignore[index]
    (shown,) = prompt["open_alerts"]
    assert (shown["status"], shown["still_escalating"]) == ("open", True)


def test_a_cold_glimmer_is_not_waited_for_and_its_verdict_still_lands(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    glimmer = Gate(FakeLLM([{"agrees": True, "seen": "Packed.", "confidence": 0.9}]))
    clock = FakeClock(EPOCH)
    chain = EventChain(tmp_path / "chain", "stadium", clock=clock)
    chosen = CrowdWarden(
        memory=crowd,
        frames=grabber,
        spark=FakeLLM([warning()]),
        glimmer=glimmer,
        glimmer_model="muse-glimmer",
        chain=chain,
        notifier=None,
        tts=None,
        clock=clock,
        out_dir=tmp_path / "crowd",
        check_timeout_s=30.0,
        verifier_warm=lambda: False,
    )
    chosen.assess(chosen.watch("gate", INSTRUCTION, ["en"]), latest(crowd))
    (alert,) = chosen.alerts()
    assert alert.sent
    assert alert.check_note is not None
    assert alert.check_note.startswith("Glimmer had no warm worker")
    assert alert.notified_at is not None
    glimmer.open.set()
    path = chain.path_for(EPOCH.date())
    wait_for(lambda: read_chain(path)[-1].kind == "verdict")


def test_after_an_ack_sam_looks_for_the_steward_in_the_zone(
    tmp_path: Path, crowd: CrowdMemory, grabber: FrameGrabber
) -> None:
    chosen, _, _, chain, _ = escalating(tmp_path, crowd, grabber, FakeLLM([warning()]))
    watch = chosen.watch("gate", INSTRUCTION, ["en"], role="stewards")
    chosen.assess(watch, latest(crowd))
    (alert,) = chosen.alerts()
    chosen.tick()
    assert chosen.ack_state(alert.alert_id).arrival is None  # nobody acknowledged yet
    chosen.ack(alert.alert_id, "stewards")
    chosen.tick()

    def looked() -> bool:
        arrival = chosen.ack_state(alert.alert_id).arrival
        return arrival is not None and arrival.checks > 0

    wait_for(looked)
    arrival = chosen.ack_state(alert.alert_id).arrival
    assert arrival is not None
    assert (arrival.concept, arrival.zone, arrival.people) == ("high-visibility vest", "view", 8)
    assert arrival.seen_at == latest(crowd).at
    details = [r.payload.get("detail") for r in read_chain(chain.path_for(EPOCH.date()))]
    assert "8 x 'high-visibility vest' seen in view by SAM 3.1" in details


class TwoCameras:
    """Crowd memory stand-in with one fixed reading per camera."""

    heatmaps = Path("heat")

    def latest(self, camera_id: str) -> CrowdReading:
        return reading(0.0, 4.0, 16).model_copy(update={"camera_id": camera_id})

    def readings(self, camera_id: str, since: object = None) -> list[CrowdReading]:
        return [self.latest(camera_id)]


def test_the_camera_waiting_longest_is_assessed_first(
    tmp_path: Path, grabber: FrameGrabber
) -> None:
    spark = FakeLLM([warning(alert=False), warning(alert=False)])
    chosen = CrowdWarden(
        memory=cast("CrowdMemory", TwoCameras()),
        frames=grabber,
        spark=spark,
        glimmer=FakeLLM([]),
        glimmer_model="muse-glimmer",
        chain=None,
        notifier=None,
        tts=None,
        clock=FakeClock(EPOCH),
        out_dir=tmp_path / "crowd",
    )
    chosen.watch("b", INSTRUCTION)
    chosen.watch("a", INSTRUCTION)
    for _ in range(2):
        chosen.tick()
        running = chosen._running  # the assessment the tick started
        assert running is not None
        wait_for(running.done)
    messages = [c["messages"] for c in spark.calls]
    cameras = [json.loads(str(m[1]["content"]))["camera"]["id"] for m in messages]  # type: ignore[index]
    assert sorted(cameras) == ["a", "b"]
