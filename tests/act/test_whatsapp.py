"""Tests for WhatsApp escalation (P3-S1): payload shapes, the 24-hour window and the webhook."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import json
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.act import EscalationManager, LanNotifier
from rulecam.act.notify import for_event
from rulecam.act.whatsapp import (
    GRAPH_TIMEOUT_S,
    SeenIds,
    WhatsAppError,
    WhatsAppNotifier,
    button_message,
    parse_recipients,
    sent_at,
    valid_signature,
    whatsapp_router,
)
from rulecam.bus import Bus
from rulecam.config import Settings
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.runtime.response import build_whatsapp
from tests.fakes import EPOCH, FakeClock

SECRET = "app-secret"  # noqa: S105 - test fixture, not a real secret
TOKEN = "verify-me"  # noqa: S105 - test fixture
AIDE = "15551234567"
NURSE = "15557654321"
_IDS = itertools.count()


class FakeGraph:
    """Records Graph API calls and answers like the Cloud API."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[Mapping[str, object]] = []
        self.on_event_loop: list[bool] = []

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        self.requests.append(request)
        try:
            asyncio.get_running_loop()
            self.on_event_loop.append(True)
        except RuntimeError:
            self.on_event_loop.append(False)
        if self.status != 200:
            return {"status": self.status, "json": {"error": {"message": "nope"}}}
        if str(request["path"]).endswith("/media"):
            return {"status": 200, "json": {"id": "media-123"}}
        return {
            "status": 200,
            "json": {"messaging_product": "whatsapp", "messages": [{"id": "wamid.1"}]},
        }


class Rig:
    def __init__(self, tmp_path: Path, graph: FakeGraph | None = None) -> None:
        self.clock = FakeClock()
        self.bus = Bus()
        self.chain = EventChain(tmp_path / "chain", "unit", clock=self.clock)
        self.lan = LanNotifier(bus=self.bus, clock=self.clock)
        self.graph = graph or FakeGraph()
        self.wa = WhatsAppNotifier(
            lan=self.lan,
            transport=self.graph,
            phone_number_id="PNID",
            recipients={"aide": AIDE, "charge_nurse": NURSE},
            clock=self.clock,
        )
        self.escalation = EscalationManager(
            notifier=self.wa, clock=self.clock, chain=self.chain, bus=self.bus
        )

    def app(self) -> TestClient:
        app = FastAPI()
        app.include_router(
            whatsapp_router(
                notifier=self.wa,
                lan=self.lan,
                escalation=self.escalation,
                chain=self.chain,
                app_secret=SECRET,
                verify_token=TOKEN,
            )
        )
        return TestClient(app)


def signed(payload: object) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"}


def inbound(
    number: str,
    *,
    text: str | None = None,
    button_id: str | None = None,
    msg_id: str | None = None,
    ts: datetime = EPOCH,
) -> dict[str, object]:
    message: dict[str, object] = {
        "from": number,
        "id": msg_id or f"wamid.in.{next(_IDS)}",
        "timestamp": str(int(ts.timestamp())),
    }
    if button_id is not None:
        message |= {
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": button_id, "title": "x"},
            },
        }
    else:
        message |= {"type": "text", "text": {"body": text or "start"}}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": "1", "changes": [{"field": "messages", "value": {"messages": [message]}}]}
        ],
    }


def test_recipients_parse_and_reject_bad_input() -> None:
    assert parse_recipients("aide=+15551234567, charge_nurse=15557654321") == {
        "aide": AIDE,
        "charge_nurse": NURSE,
    }
    for bad in ("aide", "aide=12", "=15551234567", "aide=1555abc4567"):
        with pytest.raises(WhatsAppError):
            parse_recipients(bad)


def test_button_message_shape() -> None:
    message = button_message(
        AIDE, "Alert: person down", event_id="ev-1", media_id="m1", media_kind="image"
    )
    assert message["messaging_product"] == "whatsapp"
    assert message["type"] == "interactive"
    interactive = message["interactive"]
    assert isinstance(interactive, dict)
    assert interactive["type"] == "button"
    assert interactive["header"] == {"type": "image", "image": {"id": "m1"}}
    buttons = interactive["action"]["buttons"]
    assert [b["reply"]["id"] for b in buttons] == [
        "ev-1|On my way",
        "ev-1|False alarm",
        "ev-1|Escalate",
    ]
    assert all(len(b["reply"]["title"]) <= 20 for b in buttons)
    bare = button_message(AIDE, "x", event_id="e", media_id=None, media_kind="image")
    assert isinstance(bare["interactive"], dict)
    assert "header" not in bare["interactive"]


def test_outside_the_24h_window_only_the_lan_app_gets_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with for_event("ev-1"):
        alert_id = rig.wa.send("aide", "Alert", "body")
    assert alert_id.startswith("al-")
    assert rig.graph.requests == []
    assert rig.lan.list()[0].alert_id == alert_id


def test_inside_the_window_it_sends_buttons_with_the_clip(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.wa.opened("aide")
    clip = tmp_path / "strip.png"
    clip.write_bytes(b"\x89PNG fake")
    with for_event("ev-1"):
        rig.wa.send("aide", "Alert: person down", "corridor east", clip_path=str(clip))
    upload, message = rig.graph.requests
    assert upload["path"] == "/PNID/media"
    assert upload["form"] == {"messaging_product": "whatsapp", "type": "image/png"}
    body = message["json"]
    assert isinstance(body, dict)
    assert body["to"] == AIDE
    assert body["interactive"]["header"] == {"type": "image", "image": {"id": "media-123"}}
    rig.clock.advance(24 * 3600 + 1)
    assert not rig.wa.window_open("aide")


def test_voice_note_goes_only_inside_the_window_as_a_voice_message(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert rig.wa.voice_note("aide", b"OggS", filename="w.ogg") is None  # window closed
    assert rig.wa.voice_note("stranger", b"OggS", filename="w.ogg") is None
    assert rig.graph.requests == []
    rig.wa.opened("aide")
    assert rig.wa.voice_note("aide", b"OggS", filename="w.ogg") == "wamid.1"
    upload, message = rig.graph.requests
    assert upload["form"] == {"messaging_product": "whatsapp", "type": "audio/ogg"}
    body = message["json"]
    assert isinstance(body, dict)
    assert body["type"] == "audio"
    assert body["audio"] == {"id": "media-123", "voice": True}
    failing = Rig(tmp_path / "f", FakeGraph(status=500))
    failing.wa.opened("aide")
    with pytest.raises(WhatsAppError):
        failing.wa.voice_note("aide", b"OggS", filename="w.ogg")


def test_a_whatsapp_failure_never_loses_the_alert(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeGraph(status=500))
    rig.wa.opened("aide")
    with for_event("ev-1"):
        alert_id = rig.wa.send("aide", "Alert", "body")
    assert rig.lan.get(alert_id) is not None
    assert len(rig.graph.requests) == 1


def test_unknown_role_and_alerts_without_an_event_skip_whatsapp(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.wa.opened("aide")
    rig.wa.send("aide", "no event", "b")
    with for_event("ev-2"):
        rig.wa.send("cook", "unknown role", "b")
    assert rig.graph.requests == []


def test_webhook_handshake(tmp_path: Path) -> None:
    with Rig(tmp_path).app() as client:
        ok = client.get(
            "/whatsapp/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": TOKEN, "hub.challenge": "42"},
        )
        assert (ok.status_code, ok.text) == (200, "42")
        bad = client.get(
            "/whatsapp/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"},
        )
        assert bad.status_code == 403


def test_webhook_rejects_bad_signatures(tmp_path: Path) -> None:
    raw, headers = signed(inbound(AIDE))
    with Rig(tmp_path).app() as client:
        assert (
            client.post(
                "/whatsapp/webhook",
                content=raw,
                headers={**headers, "X-Hub-Signature-256": "sha256=00"},
            ).status_code
            == 403
        )
        assert client.post("/whatsapp/webhook", content=raw).status_code == 403
        bad_raw = b"not json"
        sig = hmac.new(SECRET.encode(), bad_raw, hashlib.sha256).hexdigest()
        assert (
            client.post(
                "/whatsapp/webhook",
                content=bad_raw,
                headers={"X-Hub-Signature-256": f"sha256={sig}"},
            ).status_code
            == 400
        )
    assert not valid_signature(raw, None, SECRET)


def test_start_opens_the_window_and_a_tap_acks_the_escalation(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.app() as client:
        raw, headers = signed(inbound(AIDE, text="start"))
        assert client.post("/whatsapp/webhook", content=raw, headers=headers).json() == {
            "handled": 0
        }
        assert rig.wa.window_open("aide")
        rig.escalation.start(
            "ev-7", ["aide", "charge_nurse"], after_s=60, title="Person down", body="b"
        )
        raw, headers = signed(inbound(AIDE, button_id="ev-7|On my way"))
        assert client.post("/whatsapp/webhook", content=raw, headers=headers).json() == {
            "handled": 1
        }
    assert not rig.escalation.is_open("ev-7")
    kinds = [r.kind for r in read_chain(rig.chain.path_for(rig.clock.now().date()))]
    assert kinds == ["action", "ack"]
    assert rig.lan.list()[0].status == "acknowledged"


def test_false_alarm_tap_labels_and_strangers_are_ignored(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.escalation.start("ev-8", ["aide"], after_s=60, title="t", body="b")
    with rig.app() as client:
        raw, headers = signed(inbound("19990000000", button_id="ev-8|False alarm"))
        assert client.post("/whatsapp/webhook", content=raw, headers=headers).json() == {
            "handled": 0
        }
        raw, headers = signed(inbound(AIDE, button_id="ev-8|Delete everything"))
        assert client.post("/whatsapp/webhook", content=raw, headers=headers).json() == {
            "handled": 0
        }
        raw, headers = signed(inbound(AIDE, button_id="ev-8|False alarm"))
        assert client.post("/whatsapp/webhook", content=raw, headers=headers).json() == {
            "handled": 1
        }
    records = read_chain(rig.chain.path_for(rig.clock.now().date()))
    assert [r.kind for r in records] == ["action", "ack", "label"]
    assert records[-1].payload["label"] == "false_alarm"
    assert records[-1].payload["role"] == "aide"
    assert AIDE not in json.dumps([r.payload for r in records])  # numbers never reach the chain


def test_build_whatsapp_from_settings(tmp_path: Path) -> None:
    lan = LanNotifier(bus=Bus(), clock=FakeClock())
    assert build_whatsapp(Settings(_env_file=None), lan=lan, clock=FakeClock()) is None
    settings = Settings(
        _env_file=None,
        whatsapp_token="t",  # noqa: S106 - test fixture
        whatsapp_phone_number_id="PNID",
        whatsapp_recipients=f"aide={AIDE}",
    )
    notifier = build_whatsapp(settings, lan=lan, clock=FakeClock())
    assert notifier is not None
    assert notifier.role_for(f"+{AIDE}") == "aide"


def test_a_redelivered_escalate_tap_escalates_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.wa.opened("charge_nurse")
    rig.escalation.start(
        "ev-9", ["aide", "charge_nurse", "on_call"], after_s=60, title="t", body="b"
    )
    raw, headers = signed(inbound(AIDE, button_id="ev-9|Escalate"))
    with rig.app() as client:
        first = client.post("/whatsapp/webhook", content=raw, headers=headers)
        again = client.post("/whatsapp/webhook", content=raw, headers=headers)
    assert (first.json(), again.json()) == ({"handled": 1}, {"handled": 0})
    assert again.status_code == 200
    records = read_chain(rig.chain.path_for(rig.clock.now().date()))
    assert [r.payload["role"] for r in records] == ["aide", "charge_nurse"]
    assert rig.graph.on_event_loop == [False]  # the Graph send ran off the event loop


def test_a_redelivered_false_alarm_labels_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.escalation.start("ev-10", ["aide"], after_s=60, title="t", body="b")
    raw, headers = signed(inbound(AIDE, button_id="ev-10|False alarm", msg_id="wamid.dup"))
    with rig.app() as client:
        for _ in range(3):
            assert client.post("/whatsapp/webhook", content=raw, headers=headers).status_code == 200
    kinds = [r.kind for r in read_chain(rig.chain.path_for(rig.clock.now().date()))]
    assert kinds == ["action", "ack", "label"]


def test_stale_or_undated_messages_are_dropped(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.escalation.start("ev-11", ["aide"], after_s=60, title="t", body="b")
    rig.clock.advance(11 * 60)
    undated = inbound(AIDE, button_id="ev-11|False alarm")
    messages = undated["entry"][0]["changes"][0]["value"]["messages"]  # type: ignore[index]
    del messages[0]["timestamp"]
    with rig.app() as client:
        for payload in (inbound(AIDE, button_id="ev-11|False alarm"), undated):
            raw, headers = signed(payload)
            assert client.post("/whatsapp/webhook", content=raw, headers=headers).json() == {
                "handled": 0
            }
    assert rig.escalation.is_open("ev-11")
    assert not rig.wa.window_open("aide")  # a replayed "start" does not open the window either
    assert sent_at({"timestamp": "x"}) is None


def test_seen_ids_are_bounded() -> None:
    seen = SeenIds(capacity=2)
    assert seen.add("a")
    assert not seen.add("a")
    assert seen.add("b")
    assert seen.add("c")  # forgets "a"
    assert seen.add("a")


def test_build_whatsapp_uses_a_short_graph_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def recording_transport(*args: object, **kwargs: object) -> FakeGraph:
        calls.append(kwargs)
        return FakeGraph()

    monkeypatch.setattr("rulecam.runtime.response.HttpxTransport", recording_transport)
    settings = Settings(
        _env_file=None,
        whatsapp_token="t",  # noqa: S106 - test fixture
        whatsapp_phone_number_id="PNID",
        whatsapp_recipients=f"aide={AIDE}",
    )
    assert build_whatsapp(
        settings, lan=LanNotifier(bus=Bus(), clock=FakeClock()), clock=FakeClock()
    )
    assert calls == [{"timeout_s": GRAPH_TIMEOUT_S}]
    assert GRAPH_TIMEOUT_S <= 10


class LockProbe:
    """A notifier that checks, from another thread, that the escalation lock is free."""

    def __init__(self) -> None:
        self.escalation: EscalationManager | None = None
        self.lock_free: list[bool] = []

    def send(
        self,
        role: str,
        title: str,
        body: str,
        *,
        clip_path: str | None = None,
        buttons: tuple[str, ...] = (),
    ) -> str:
        assert self.escalation is not None
        probe = threading.Thread(target=self.escalation.is_open, args=("ev-x",))
        probe.start()
        probe.join(timeout=2)
        self.lock_free.append(not probe.is_alive())
        return role


def test_escalation_sends_without_holding_its_lock(tmp_path: Path) -> None:
    clock = FakeClock()
    probe = LockProbe()
    escalation = EscalationManager(
        notifier=probe,
        clock=clock,
        chain=EventChain(tmp_path / "chain", "unit", clock=clock),
        bus=Bus(),
    )
    probe.escalation = escalation
    escalation.start("ev-x", ["aide", "charge_nurse", "on_call"], after_s=60, title="t", body="b")
    escalation.escalate_now("ev-x")
    clock.advance(61)
    assert len(escalation.tick()) == 1
    assert probe.lock_free == [True, True, True]
