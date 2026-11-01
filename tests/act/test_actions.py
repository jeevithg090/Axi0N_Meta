"""Tests for actions, escalation, the interlock and LAN alerts (P3-06)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from rulecam.act import ActionExecutor, EscalationManager, Interlock, LanNotifier, PhraseBook
from rulecam.act.api import alerts_router
from rulecam.act.devices import CommandSpeaker, StrobeLight
from rulecam.act.errors import DeviceError, ResumeRefusedError
from rulecam.bus import Topic
from rulecam.contracts import CandidateEvent, RuleIR, Severity, VerifierVerdict
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.verify.decision import Decision
from rulecam.verify.policy import VerificationPolicy
from rulecam.voice_out.prerender import PrerenderedPhrase
from tests.fakes import FakeClock, FakePlug, make_chunk, make_verdict
from tests.rules.helpers import make_rule
from tests.runtime.fakes import FakeStrips, FakeTracker, RecordingBus, sink_plan
from tests.verify.helpers import make_candidate

ALERT = Decision(alert=True, route="auto", act_first=False, reason="t")
HUMAN = Decision(alert=False, route="human_queue", act_first=False, reason="t")
SUPPRESSED = Decision(alert=False, route="auto", act_first=False, reason="t")


class FakeSpeaker:
    def __init__(self) -> None:
        self.played: list[Path] = []

    def play(self, wav: Path) -> None:
        self.played.append(wav)


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.clock = FakeClock()
        self.bus = RecordingBus()
        self.chain = EventChain(tmp_path / "chain", "site", clock=self.clock)
        self.notifier = LanNotifier(bus=self.bus, clock=self.clock)
        self.escalation = EscalationManager(
            notifier=self.notifier, clock=self.clock, chain=self.chain, bus=self.bus
        )
        self.plug = FakePlug()
        self.interlock = Interlock(
            {"forklift": self.plug}, clock=self.clock, chain=self.chain, bus=self.bus
        )
        self.speaker = FakeSpeaker()
        self.light_plug = FakePlug()
        self.phrases = PhraseBook(
            wavs={
                ("r", "es"): Path("tts/es.wav"),
                ("r", "en"): Path("tts/en.wav"),
            },
            crew_languages=("es", "en"),
        )
        self.executor = ActionExecutor(
            clock=self.clock,
            notifier=self.notifier,
            escalation=self.escalation,
            interlock=self.interlock,
            speaker=self.speaker,
            light=StrobeLight(self.light_plug, flashes=1, sleep=lambda s: None),
            phrases=self.phrases,
            bus=self.bus,
        )

    def records(self) -> list[str]:
        path = self.chain.path_for(self.clock.now().date())
        return [r.kind for r in read_chain(path)] if path.exists() else []


def rule(actions: list[dict[str, object]], severity: str = "medium") -> RuleIR:
    base = make_rule(
        {"op": "inside", "object": "person", "zone": "z"}, rule_id="r", severity=severity
    )
    return RuleIR.model_validate({**base.model_dump(mode="json"), "actions": actions})


def test_alert_runs_every_action_interlock_first(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    r = rule(
        [
            {"kind": "speak", "lang": "es"},
            {"kind": "notify", "role": "dock_lead"},
            {"kind": "visual_signal"},
            {"kind": "interlock_cut"},
            {"kind": "log"},
        ]
    )
    records = rig.executor.run(make_candidate(), r, ALERT)
    assert [x.kind for x in records] == ["interlock_cut", "notify", "visual_signal", "speak", "log"]
    assert all(x.ok for x in records)
    assert rig.plug.events == ["off"]
    assert rig.light_plug.events == ["on", "off", "on"]
    assert rig.speaker.played == [Path("tts/es.wav")]
    alert = rig.notifier.list("dock_lead")[0]
    assert (alert.event_id, alert.rule_id) == ("ev-1", "r")
    assert alert.buttons == ("On my way", "False alarm", "Escalate")


def test_speak_picks_the_language(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.executor.run(make_candidate(), rule([{"kind": "speak", "lang": "en"}]), ALERT)
    assert rig.speaker.played == [Path("tts/en.wav")]
    rig.speaker.played.clear()
    rig.executor.run(make_candidate(), rule([{"kind": "coach_station", "lang": "crew"}]), ALERT)
    assert rig.speaker.played == [Path("tts/es.wav"), Path("tts/en.wav")]
    station = [p for t, p in rig.bus.published if t == Topic.STATION_COACH]
    assert [(p.rule_id, p.lang) for p in station] == [("r", "crew")]  # type: ignore[attr-defined]
    missing = rig.executor.run(make_candidate(), rule([{"kind": "speak", "lang": "fr"}]), ALERT)
    assert not missing[0].ok
    assert "no pre-rendered warning" in missing[0].detail


def test_phrase_book_from_prerendered() -> None:
    rendered = [
        PrerenderedPhrase(
            source="Wash your hands",
            lang=lang,
            text="t",
            cache_key="k",
            path=f"tts/{lang}.wav",
            reused=False,
        )
        for lang in ("es", "en")
    ]
    book = PhraseBook.from_prerendered({"wash": "Wash your hands "}, rendered, ["es", "en"])
    assert book.wavs_for("wash", "es") == [Path("tts/es.wav")]
    assert book.wavs_for("wash", None) == [Path("tts/es.wav"), Path("tts/en.wav")]


def test_suppressed_runs_nothing_and_human_queue_only_notifies(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    r = rule([{"kind": "interlock_cut"}, {"kind": "speak"}, {"kind": "notify", "role": "lead"}])
    assert rig.executor.run(make_candidate(), r, SUPPRESSED) == []
    records = rig.executor.run(make_candidate(), r, HUMAN)
    assert [x.kind for x in records] == ["notify"]
    assert records[0].gate == "human"
    assert rig.plug.events == []
    assert rig.notifier.list()[0].title.startswith("Needs review")


def test_missing_devices_record_failure_without_stopping(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    bare = ActionExecutor(clock=rig.clock, notifier=rig.notifier, escalation=rig.escalation)
    r = rule(
        [
            {"kind": "interlock_cut"},
            {"kind": "visual_signal"},
            {"kind": "speak"},
            {"kind": "notify", "role": "lead"},
        ]
    )
    records = bare.run(make_candidate(), r, ALERT)
    assert [(x.kind, x.ok) for x in records] == [
        ("interlock_cut", False),
        ("notify", True),
        ("visual_signal", False),
        ("speak", False),
    ]
    (coach,) = bare.run(make_candidate(), rule([{"kind": "coach_station"}]), ALERT)
    assert not coach.ok
    assert "station display" in coach.detail


class TimedPlug:
    def __init__(self) -> None:
        self.off_at: float | None = None

    def off(self) -> None:
        self.off_at = time.perf_counter()

    def on(self) -> None:
        raise AssertionError("never resumed automatically")


class SlowVerifier:
    def verify(self, candidate: CandidateEvent, rule: RuleIR) -> VerifierVerdict:
        time.sleep(0.5)  # a slow model must not delay the power cut
        return make_verdict(verdict="no_violation", score=0.1)


def test_high_candidate_cuts_power_within_200ms(tmp_path: Path) -> None:
    from rulecam.runtime.loop import RuntimeLoop, chunk_end  # noqa: PLC0415
    from tests.fakes import InMemoryTrackStore  # noqa: PLC0415

    rig = Rig(tmp_path)
    plug = TimedPlug()
    interlock = Interlock({"forklift": plug}, clock=rig.clock, chain=rig.chain, bus=rig.bus)
    executor = ActionExecutor(
        clock=rig.clock, notifier=rig.notifier, escalation=rig.escalation, interlock=interlock
    )
    plan = sink_plan("high")
    high_rule = RuleIR.model_validate(
        {
            **plan.rules[0].model_dump(mode="json"),
            "actions": [{"kind": "interlock_cut"}, {"kind": "notify", "role": "dock_lead"}],
        }
    )
    plan = plan.model_copy(update={"rules": (high_rule,)})
    store = InMemoryTrackStore()
    verifier = SlowVerifier()
    loop = RuntimeLoop(
        bus=rig.bus,
        clock=rig.clock,
        tracker=FakeTracker(store),
        store=store,
        verifier=verifier,
        policy=VerificationPolicy(tier1=verifier),
        actions=executor,
        strips=FakeStrips(),
        chain=rig.chain,
    )
    loop.set_plan(plan)
    rig.clock.advance((chunk_end(make_chunk()) - rig.clock.now()).total_seconds())
    started = time.perf_counter()
    candidates = loop.handle_chunk(make_chunk())
    assert candidates
    assert candidates[0].severity == Severity.HIGH
    assert plug.off_at is not None
    assert plug.off_at - started < 0.2
    assert rig.records()[:4] == ["plan", "candidate", "action", "action"]
    # The slow verifier vetoed: the decision is chained and a person is asked to review it.
    records = read_chain(rig.chain.path_for(rig.clock.now().date()))
    decision = next(r.payload["decision"] for r in records if "decision" in r.payload)
    assert decision["route"] == "human_queue"  # type: ignore[index]
    assert decision["alert"] is True  # type: ignore[index]
    titles = [a.title for a in rig.notifier.list("dock_lead")]
    assert any(t.startswith("Needs review") for t in titles)
    assert any(t.startswith("Alert") for t in titles)


def test_resume_needs_a_role_and_is_logged(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.interlock.cut()
    with pytest.raises(ResumeRefusedError):
        rig.interlock.resume("forklift", "  ")
    record = rig.interlock.resume("forklift", "dock_lead")
    assert (record.kind, record.gate, record.approved_by_role) == (
        "interlock_resume",
        "human",
        "dock_lead",
    )
    assert rig.plug.events == ["off", "on"]
    assert rig.records() == ["action"]


def test_escalation_timeline_with_fake_clock(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.escalation.start(
        "ev-9",
        ["aide", "charge_nurse", "on_call"],
        after_s=60,
        title="Person down",
        body="East corridor",
    )
    assert [a.role for a in rig.notifier.list()] == ["aide"]
    rig.clock.advance(59)
    assert rig.escalation.tick() == []
    rig.clock.advance(1)
    (step,) = rig.escalation.tick()
    assert (step.kind, step.role) == ("escalate", "charge_nurse")
    rig.clock.advance(60)
    assert [r.role for r in rig.escalation.tick()] == ["on_call"]
    rig.clock.advance(600)
    assert rig.escalation.tick() == []  # no more roles: no more timers
    assert rig.escalation.escalate_now("ev-9") is None
    assert rig.escalation.is_open("ev-9")  # still waiting for the last role's ack
    ack = rig.escalation.ack("ev-9", "on_call")
    assert ack is not None
    assert not rig.escalation.is_open("ev-9")
    assert rig.records() == ["action", "action", "action", "ack"]
    assert Topic.ALERT_NEW in rig.bus.topics()


def test_ack_cancels_escalation(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.escalation.start("ev-1", ["aide", "charge_nurse"], after_s=60, title="t", body="b")
    ack = rig.escalation.ack("ev-1", "aide")
    assert ack is not None
    assert ack.role == "aide"
    rig.clock.advance(120)
    assert rig.escalation.tick() == []
    assert rig.escalation.ack("ev-1", "aide") is None
    assert rig.records() == ["action", "ack"]
    with pytest.raises(ValueError, match="at least one role"):
        rig.escalation.start("ev-2", [], after_s=1, title="t", body="b")


def test_escalate_action_starts_a_chain(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    r = rule([{"kind": "escalate", "role": "aide, charge_nurse", "after_s": 30}])
    (record,) = rig.executor.run(make_candidate(), r, ALERT)
    assert record.detail == "escalation aide → charge_nurse"
    rig.clock.advance(30)
    assert [s.role for s in rig.escalation.tick()] == ["charge_nurse"]


def client(rig: Rig) -> TestClient:
    app = FastAPI()
    app.include_router(
        alerts_router(
            notifier=rig.notifier,
            escalation=rig.escalation,
            chain=rig.chain,
            interlock=rig.interlock,
            dependencies=(),
        )
    )
    return TestClient(app)


def test_false_alarm_button_creates_a_label(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.escalation.start("ev-1", ["aide", "nurse"], after_s=60, title="t", body="b")
    alert_id = rig.notifier.list()[0].alert_id
    with client(rig) as c:
        result = c.post(
            f"/alerts/{alert_id}/respond", json={"button": "False alarm", "role": "aide"}
        )
        assert result.json()["action"] == "false_alarm"
        assert c.get("/alerts", params={"role": "aide"}).json()[0]["status"] == "false_alarm"
    records = read_chain(rig.chain.path_for(rig.clock.now().date()))
    assert [r.kind for r in records] == ["action", "ack", "label"]
    assert records[-1].payload == {
        "event_id": "ev-1",
        "rule_id": None,
        "label": "false_alarm",
        "role": "aide",
    }


def test_on_my_way_escalate_and_ack_routes(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.escalation.start("ev-1", ["aide", "nurse", "on_call"], after_s=60, title="t", body="b")
    first = rig.notifier.list()[0].alert_id
    with client(rig) as c:
        escalated = c.post(f"/alerts/{first}/respond", json={"button": "Escalate", "role": "aide"})
        assert escalated.json()["action"] == "escalated"
        mine = c.post(f"/alerts/{first}/respond", json={"button": "On my way", "role": "nurse"})
        assert mine.json()["action"] == "acknowledged"
        again = c.post(f"/alerts/{first}/respond", json={"button": "Escalate", "role": "aide"})
        assert again.json()["action"] == "nothing_open"
        assert (
            c.post("/alerts/nope/respond", json={"button": "Escalate", "role": "a"}).status_code
            == 404
        )
        assert c.post("/ack", json={"event_id": "ev-1", "role": "nurse"}).status_code == 404
        rig.escalation.start("ev-2", ["aide"], after_s=60, title="t", body="b")
        rig.escalation.start("ev-3", ["aide", "nurse"], after_s=60, title="t", body="b")
        assert c.post("/ack", json={"event_id": "ev-3", "role": "aide"}).json()["role"] == "aide"


def test_resume_route(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with client(rig) as c:
        assert c.post("/interlock/forklift/resume", json={}).status_code == 403
        assert c.post("/interlock/forklift/resume", json={"role": ""}).status_code == 403
        ok = c.post("/interlock/forklift/resume", json={"role": "dock_lead"})
        assert ok.json()["kind"] == "interlock_resume"
        assert c.post("/interlock/crane/resume", json={"role": "x"}).status_code == 404
    assert rig.plug.events == ["on"]


def test_command_speaker(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def record(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    speaker = CommandSpeaker("sh", run=record)
    speaker.play(tmp_path / "a.wav")
    assert calls[0][1:] == [str(tmp_path / "a.wav")]
    failing = CommandSpeaker("sh", run=lambda argv: 1)
    with pytest.raises(DeviceError, match="exited with 1"):
        failing.play(tmp_path / "a.wav")
    with pytest.raises(DeviceError, match="not found"):
        CommandSpeaker("no-such-player-xyz")


class FailingPlug:
    def __init__(self) -> None:
        self.calls = 0

    def off(self) -> None:
        self.calls += 1
        raise DeviceError("plug offline")

    def on(self) -> None:
        raise DeviceError("plug offline")


def test_cut_tries_every_plug_when_one_fails(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    broken, good = FailingPlug(), FakePlug()
    interlock = Interlock({"a": broken, "b": good}, clock=rig.clock, chain=rig.chain, bus=rig.bus)
    with pytest.raises(DeviceError, match="power cut failed for a"):
        interlock.cut()
    assert good.events == ["off"]
    executor = ActionExecutor(
        clock=rig.clock, notifier=rig.notifier, escalation=rig.escalation, interlock=interlock
    )
    (cut,) = executor.run(make_candidate(), rule([{"kind": "interlock_cut"}]), ALERT)
    assert not cut.ok
    assert good.events == ["off", "off"]


class ConnectablePlug(FakePlug):
    def __init__(self, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail

    def connect(self) -> None:
        if self.fail:
            raise DeviceError("unreachable")
        self.events.append("connect")


def test_interlock_connect_warms_plugs(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    ok, bad, plain = ConnectablePlug(), ConnectablePlug(fail=True), FakePlug()
    interlock = Interlock(
        {"ok": ok, "bad": bad, "plain": plain}, clock=rig.clock, chain=rig.chain, bus=rig.bus
    )
    assert interlock.connect() == ["bad"]
    assert ok.events == ["connect"]
    assert plain.events == []


class FakeKasaError(Exception):
    """Stands in for kasa.KasaException (not an OSError)."""


class FakeKasaDevice:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    async def update(self) -> None:
        self.calls.append("update")

    async def turn_off(self) -> None:
        if self.fail:
            raise FakeKasaError("auth")
        self.calls.append("off")

    async def turn_on(self) -> None:
        self.calls.append("on")


def test_kasa_plug_connects_once_and_wraps_kasa_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys  # noqa: PLC0415
    import types  # noqa: PLC0415

    from rulecam.act.devices import KasaPlug  # noqa: PLC0415

    device = FakeKasaDevice()
    discovered: list[str] = []

    async def discover_single(host: str, **_: object) -> FakeKasaDevice:
        discovered.append(host)
        return device

    fake = types.SimpleNamespace(Discover=types.SimpleNamespace(discover_single=discover_single))
    monkeypatch.setitem(sys.modules, "kasa", fake)
    plug = KasaPlug("10.0.0.9")
    plug.connect()
    plug.off()
    plug.off()
    assert discovered == ["10.0.0.9"]
    assert device.calls == ["update", "off", "off"]  # one round trip per command once connected
    device.fail = True
    with pytest.raises(DeviceError, match="FakeKasaError"):
        plug.off()


def test_on_my_way_without_a_chain_still_records_the_ack(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.executor.run(make_candidate(), rule([{"kind": "notify", "role": "lead"}]), ALERT)
    alert_id = rig.notifier.list()[0].alert_id
    with client(rig) as c:
        tap = c.post(f"/alerts/{alert_id}/respond", json={"button": "False alarm", "role": "lead"})
        assert tap.json()["action"] == "false_alarm"
    records = read_chain(rig.chain.path_for(rig.clock.now().date()))
    assert [r.kind for r in records] == ["ack", "label"]
    assert records[-1].payload["rule_id"] == "r"


def test_http_inputs_are_validated(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.escalation.start("ev-1", ["aide"], after_s=60, title="t", body="b")
    alert_id = rig.notifier.list()[0].alert_id
    app = FastAPI()
    app.include_router(
        alerts_router(
            notifier=rig.notifier,
            escalation=rig.escalation,
            chain=rig.chain,
            interlock=rig.interlock,
            roles={"aide", "dock_lead"},
            dependencies=(),
        )
    )
    with TestClient(app) as c:
        bad = {"button": "On my way", "role": "x" * 65}
        assert c.post(f"/alerts/{alert_id}/respond", json=bad).status_code == 422
        odd = {"button": "On my way", "role": "aide; rm -rf"}
        assert c.post(f"/alerts/{alert_id}/respond", json=odd).status_code == 422
        stranger = {"button": "On my way", "role": "visitor"}
        assert c.post(f"/alerts/{alert_id}/respond", json=stranger).status_code == 403
        assert c.post("/ack", json={"event_id": "ev 1", "role": "aide"}).status_code == 422
        assert c.get("/alerts", params={"role": "a/b"}).status_code == 422
        assert c.post("/interlock/forklift/resume", json={"role": "visitor"}).status_code == 403
        assert c.post("/interlock/forklift/resume", json={}).status_code == 403
        assert c.post("/interlock/forklift/resume", json={"role": "dock_lead"}).status_code == 200


def test_resume_device_failure_is_502(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    interlock = Interlock(
        {"forklift": FailingPlug()}, clock=rig.clock, chain=rig.chain, bus=rig.bus
    )
    app = FastAPI()
    app.include_router(
        alerts_router(
            notifier=rig.notifier,
            escalation=rig.escalation,
            chain=rig.chain,
            interlock=interlock,
            dependencies=(),
        )
    )
    with TestClient(app) as c:
        assert c.post("/interlock/forklift/resume", json={"role": "lead"}).status_code == 502
    assert rig.records() == []


def test_phrase_book_matches_unicode_normal_forms() -> None:
    import unicodedata  # noqa: PLC0415

    nfc = "¡Cuidado! Póngase el casco"
    rendered = [
        PrerenderedPhrase(
            source=nfc, lang="es", text=nfc, cache_key="k", path="tts/es.wav", reused=True
        )
    ]
    book = PhraseBook.from_prerendered({"hat": unicodedata.normalize("NFD", nfc)}, rendered, ["es"])
    assert book.wavs_for("hat", "es") == [Path("tts/es.wav")]


def test_alerts_router_applies_the_auth_it_is_given(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def refuse() -> None:
        raise HTTPException(status_code=401, detail="sign in")

    app = FastAPI()
    app.include_router(
        alerts_router(
            notifier=rig.notifier,
            escalation=rig.escalation,
            chain=rig.chain,
            interlock=rig.interlock,
            dependencies=[Depends(refuse)],
        )
    )
    with TestClient(app) as c:
        assert c.get("/alerts").status_code == 401
        assert c.post("/ack", json={"event_id": "ev-1", "role": "aide"}).status_code == 401
        assert c.post("/interlock/forklift/resume", json={"role": "lead"}).status_code == 401
    assert rig.records() == []


def test_dry_run_records_what_would_run_and_touches_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    executor = ActionExecutor(
        clock=rig.clock,
        notifier=rig.notifier,
        escalation=rig.escalation,
        interlock=rig.interlock,
        speaker=rig.speaker,
        light=StrobeLight(rig.light_plug, flashes=1, sleep=lambda s: None),
        phrases=rig.phrases,
        bus=rig.bus,
        dry_run=True,
    )
    assert executor.dry_run
    r = rule(
        [
            {"kind": "speak", "lang": "es"},
            {"kind": "visual_signal"},
            {"kind": "coach_station"},
            {"kind": "notify", "role": "dock_lead"},
            {"kind": "escalate", "role": "aide,charge_nurse"},
            {"kind": "interlock_cut"},
            {"kind": "log"},
        ]
    )
    records = executor.run(make_candidate(), r, ALERT)
    assert [x.detail for x in records] == [
        "dry-run: would cut power",
        "dry-run: would notify dock_lead",
        "dry-run: would escalate aide → charge_nurse",
        "dry-run: would strobe the light",
        "dry-run: would play the warning (es)",
        "dry-run: would show the station display and play the warning (crew)",
        "dry-run: logged",
    ]
    assert all(x.ok and x.gate == "auto" for x in records)
    assert rig.plug.events == []
    assert rig.light_plug.events == []
    assert rig.speaker.played == []
    assert rig.notifier.list() == []
    assert not rig.escalation.is_open(make_candidate().event_id)
    human = executor.run(make_candidate(), r, HUMAN)
    assert [x.detail for x in human] == [
        "dry-run: would notify dock_lead (needs review)",
        "dry-run: would escalate aide → charge_nurse (needs review)",
    ]
    assert executor.run(make_candidate(), r, SUPPRESSED) == []
