"""Tests for Telegram alerts (add-on 5.1): links, sends, long-poll taps and the owner routes."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rulecam.act import EscalationManager, LanNotifier
from rulecam.act.notify import for_event
from rulecam.act.telegram import (
    CODES,
    NOT_LINKED,
    BotApi,
    ChatLinks,
    TelegramBot,
    TelegramError,
    TelegramNotifier,
    keyboard,
    link_key,
    role_from_token,
    role_token,
    telegram_router,
)
from rulecam.api.app import false_alarm_regressions
from rulecam.bus import Bus
from rulecam.clients.transport import HttpxTransport, ModelTransportError
from rulecam.config import Settings
from rulecam.evidence.chain import EventChain, read_chain
from rulecam.rule_ci.hard_negatives import HardNegatives
from rulecam.rule_ci.lookalikes import load_regressions
from rulecam.runtime.loop import ActivePlan
from rulecam.runtime.response import build_telegram
from tests.fakes import FakeClock
from tests.rules.helpers import make_rule

KEY = link_key("123:bot-token")
CHAT = 4242
OTHER_CHAT = 777

Reply = Mapping[str, object]


class FakeBotApi:
    """Records Bot API calls and answers like Telegram (scripted per method when given)."""

    def __init__(
        self, scripted: Mapping[str, Callable[[Mapping[str, object]], Reply]] | None = None
    ):
        self.requests: list[Mapping[str, object]] = []
        self.scripted = dict(scripted or {})

    def __call__(self, request: Mapping[str, object]) -> Reply:
        self.requests.append(request)
        method = str(request["path"]).lstrip("/")
        if method in self.scripted:
            return self.scripted[method](request)
        if method == "getMe":
            return ok({"id": 1, "is_bot": True, "username": "rulecam_site_bot"})
        if method == "sendPhoto":
            return ok({"message_id": 9, "photo": [{"file_id": "small"}, {"file_id": "big"}]})
        return ok({"message_id": 9})

    def methods(self) -> list[str]:
        return [str(r["path"]).lstrip("/") for r in self.requests]


def ok(result: object) -> Reply:
    return {"status": 200, "json": {"ok": True, "result": result}}


class Rig:
    def __init__(self, tmp_path: Path, api: FakeBotApi | None = None) -> None:
        self.clock = FakeClock()
        self.bus = Bus()
        self.chain = EventChain(tmp_path / "chain", "unit", clock=self.clock)
        self.lan = LanNotifier(bus=self.bus, clock=self.clock)
        self.api = api or FakeBotApi()
        self.links = ChatLinks(tmp_path / "telegram" / "links.json")
        self.telegram = TelegramNotifier(inner=self.lan, api=BotApi(self.api), links=self.links)
        self.escalation = EscalationManager(
            notifier=self.telegram, clock=self.clock, chain=self.chain, bus=self.bus
        )
        self.false_alarms: list[tuple[str, str | None]] = []
        self.sleeps: list[float] = []
        self.bot = TelegramBot(
            api=BotApi(self.api),
            links=self.links,
            key=KEY,
            lan=self.lan,
            escalation=self.escalation,
            chain=self.chain,
            on_false_alarm=lambda e, r: self.false_alarms.append((e, r)),
            sleep=self.sleeps.append,
        )

    def records(self) -> list[str]:
        path = self.chain.path_for(self.clock.now().date())
        return [r.kind for r in read_chain(path)] if path.is_file() else []


def message(text: str, chat: int = CHAT) -> dict[str, object]:
    return {"message": {"message_id": 1, "chat": {"id": chat, "type": "private"}, "text": text}}


def tap(data: str, chat: int = CHAT) -> dict[str, object]:
    return {
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "message": {"message_id": 55, "chat": {"id": chat, "type": "private"}},
        }
    }


def test_role_tokens_round_trip_and_refuse_forgeries() -> None:
    token = role_token(KEY, "charge_nurse")
    assert len(token) <= 64
    assert set(token) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    assert role_from_token(KEY, token) == "charge_nurse"
    forged = "0" * 16 + "_charge_nurse"
    assert role_from_token(KEY, forged) is None
    assert role_from_token(link_key("other"), token) is None
    assert role_from_token(KEY, "nonsense") is None
    with pytest.raises(TelegramError):
        role_token(KEY, "x" * 60)
    with pytest.raises(TelegramError):
        role_token(KEY, "bad role")


def test_keyboard_carries_alert_id_and_short_codes() -> None:
    markup = keyboard("al-3", ("On my way", "False alarm", "Escalate", "Unknown"))
    rows = markup["inline_keyboard"]
    assert isinstance(rows, list)
    assert [row[0]["callback_data"] for row in rows] == ["al-3|ack", "al-3|fa", "al-3|esc"]
    assert all(len(row[0]["callback_data"].encode()) <= 64 for row in rows)


def test_chat_links_persist_without_phone_numbers(tmp_path: Path) -> None:
    path = tmp_path / "links.json"
    links = ChatLinks(path)
    links.link("1", "aide")
    links.link("2", "aide")
    links.link("3", "on_call")
    assert ChatLinks(path).chats_for("aide") == ["1", "2"]
    assert links.counts() == {"aide": 2, "on_call": 1}
    assert links.unlink("3") == "on_call"
    assert links.unlink("3") is None
    assert json.loads(path.read_text()) == {"1": "aide", "2": "aide"}
    path.write_text("{not json")
    assert ChatLinks(path).counts() == {}
    path.write_text(json.dumps({"1": "bad role!", "2": 5, "3": "ok_role"}))
    assert ChatLinks(path).counts() == {"ok_role": 1}


def test_an_unlinked_role_only_gets_the_lan_alert(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with for_event("ev-1"):
        alert_id = rig.telegram.send("aide", "Alert", "body", buttons=tuple(CODES.values()))
    assert rig.lan.get(alert_id) is not None
    assert rig.api.requests == []


def test_a_linked_role_gets_the_blurred_still_with_buttons(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.links.link(str(CHAT), "aide")
    rig.links.link(str(OTHER_CHAT), "aide")
    still = tmp_path / "strip_0.png"
    still.write_bytes(b"\x89PNG fake")
    with for_event("ev-1", "wash"):
        alert_id = rig.telegram.send(
            "aide",
            "Alert: person down",
            "corridor east",
            clip_path=str(still),
            buttons=("On my way", "False alarm", "Escalate"),
        )
    first, second = rig.api.requests
    assert first["path"] == "/sendPhoto"
    assert first["form"]["chat_id"] == str(CHAT)  # type: ignore[index]
    markup = json.loads(str(first["form"]["reply_markup"]))  # type: ignore[index]
    assert markup["inline_keyboard"][0][0]["callback_data"] == f"{alert_id}|ack"
    assert first["parts"]["photo"]["content"] == b"\x89PNG fake"  # type: ignore[index]
    assert first["parts"]["photo"]["content_type"] == "image/png"  # type: ignore[index]
    # The second chat reuses the uploaded photo instead of uploading it again.
    assert second["json"]["photo"] == "big"  # type: ignore[index]
    assert second["json"]["chat_id"] == str(OTHER_CHAT)  # type: ignore[index]


def test_text_only_without_a_still_and_failures_never_lose_the_alert(tmp_path: Path) -> None:
    def refuse(_: Mapping[str, object]) -> Reply:
        return {"status": 403, "json": {"ok": False, "description": "bot was blocked"}}

    rig = Rig(tmp_path)
    rig.links.link(str(CHAT), "aide")
    rig.telegram.send("aide", "Morning brief", "all quiet", clip_path=str(tmp_path / "x.mp4"))
    (sent,) = rig.api.requests
    assert sent["path"] == "/sendMessage"
    assert "reply_markup" not in sent["json"]  # type: ignore[operator]
    blocked = Rig(tmp_path / "b", FakeBotApi({"sendMessage": refuse}))
    blocked.links.link(str(CHAT), "aide")
    alert_id = blocked.telegram.send("aide", "Alert", "body")
    assert blocked.lan.get(alert_id) is not None


def test_link_url_asks_get_me_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    url = rig.telegram.link_url(KEY, "aide")
    assert url == f"https://t.me/rulecam_site_bot?start={role_token(KEY, 'aide')}"
    rig.telegram.link_url(KEY, "on_call")
    assert rig.api.methods() == ["getMe"]
    named = TelegramNotifier(
        inner=rig.lan, api=BotApi(rig.api), links=rig.links, bot_username="@named_bot"
    )
    assert named.username() == "named_bot"
    nameless = TelegramNotifier(
        inner=rig.lan, api=BotApi(FakeBotApi({"getMe": lambda _: ok({})})), links=rig.links
    )
    with pytest.raises(TelegramError):
        nameless.username()


def test_start_links_the_chat_and_stop_unlinks_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert rig.bot.handle(message(f"/start {role_token(KEY, 'aide')}")) == 1
    assert rig.links.role_for(str(CHAT)) == "aide"
    welcome = rig.api.requests[-1]["json"]
    assert "aide" in str(welcome["text"])  # type: ignore[index]
    assert rig.bot.handle(message("/start forged_aide", chat=OTHER_CHAT)) == 1
    assert rig.api.requests[-1]["json"]["text"] == NOT_LINKED  # type: ignore[index]
    assert rig.links.role_for(str(OTHER_CHAT)) is None
    assert rig.bot.handle(message("/stop@rulecam_site_bot")) == 1
    assert rig.links.role_for(str(CHAT)) is None
    assert rig.bot.handle(message("hello")) == 0
    assert rig.bot.handle({"message": {"chat": {"id": "x"}, "text": "/stop"}}) == 0
    assert rig.bot.handle({"edited_message": {}}) == 0


def test_on_my_way_acks_the_escalation_and_clears_the_buttons(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.links.link(str(CHAT), "aide")
    rig.escalation.start("ev-7", ["aide", "charge_nurse"], after_s=60, title="Down", body="b")
    alert = rig.lan.list()[0]
    assert rig.bot.handle(tap(f"{alert.alert_id}|ack")) == 1
    assert not rig.escalation.is_open("ev-7")
    assert rig.lan.list()[0].status == "acknowledged"
    assert rig.records() == ["action", "ack"]
    answer, clear = rig.api.requests[-2:]
    assert answer["path"] == "/answerCallbackQuery"
    assert clear["json"] == {
        "chat_id": str(CHAT),
        "message_id": 55,
        "reply_markup": {"inline_keyboard": []},
    }


def test_false_alarm_labels_and_reaches_the_regression_sink(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.links.link(str(CHAT), "aide")
    with for_event("ev-8", "wash"):
        alert_id = rig.lan.send("aide", "t", "b", buttons=("False alarm",))
    rig.bot.handle(tap(f"{alert_id}|fa"))
    records = read_chain(rig.chain.path_for(rig.clock.now().date()))
    assert [r.kind for r in records] == ["ack", "label"]
    assert records[-1].payload == {
        "event_id": "ev-8",
        "rule_id": "wash",
        "label": "false_alarm",
        "role": "aide",
    }
    assert str(CHAT) not in json.dumps([r.payload for r in records])
    assert rig.false_alarms == [("ev-8", "wash")]


def test_taps_from_strangers_or_for_unknown_alerts_do_nothing(tmp_path: Path) -> None:
    def no_edit(_: Mapping[str, object]) -> Reply:
        return {"status": 400, "json": {"ok": False, "description": "message is not modified"}}

    rig = Rig(tmp_path, FakeBotApi({"editMessageReplyMarkup": no_edit}))
    with for_event("ev-9"):
        alert_id = rig.lan.send("aide", "t", "b")
    assert rig.bot.handle(tap(f"{alert_id}|ack", chat=OTHER_CHAT)) == 0
    rig.links.link(str(CHAT), "aide")
    assert rig.bot.handle(tap("al-999|ack")) == 0
    assert rig.bot.handle(tap(f"{alert_id}|delete")) == 0
    assert rig.records() == []
    assert rig.bot.handle(tap(f"{alert_id}|ack")) == 1  # a failed button clear is cosmetic
    assert rig.records() == ["ack"]


def test_poll_advances_the_offset_and_backs_off_on_failure(tmp_path: Path) -> None:
    batches: list[Reply] = [
        ok(
            [
                {"update_id": 10, **message(f"/start {role_token(KEY, 'aide')}")},
                {"no_update_id": True},
                {"update_id": 11, **message("hi")},
            ]
        ),
        ok([]),
        {"status": 502, "json": None},
    ]

    def updates(_: Mapping[str, object]) -> Reply:
        return batches.pop(0)

    def broken(_: Mapping[str, object]) -> Reply:
        raise ModelTransportError("reset")

    rig = Rig(tmp_path, FakeBotApi({"getUpdates": updates}))
    assert rig.bot.poll() == 1
    assert rig.bot.poll() == 0
    polls = [r for r in rig.api.requests if r["path"] == "/getUpdates"]
    assert "offset" not in polls[0]["json"]  # type: ignore[operator]
    assert polls[1]["json"]["offset"] == 12  # type: ignore[index]
    assert polls[1]["json"]["allowed_updates"] == ["message", "callback_query"]  # type: ignore[index]
    assert rig.bot.poll() == 0
    assert rig.sleeps == [5.0]
    rig.api.scripted["getUpdates"] = broken
    assert rig.bot.poll() == 0
    assert len(rig.sleeps) == 2


def test_one_bad_update_does_not_drop_the_batch(tmp_path: Path) -> None:
    def updates(_: Mapping[str, object]) -> Reply:
        return ok([{"update_id": 1, **message("/stop")}, {"update_id": 2, **message("/stop")}])

    calls = {"n": 0}

    def flaky(_: Mapping[str, object]) -> Reply:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": 500, "json": {"ok": False}}
        return ok({})

    rig = Rig(tmp_path, FakeBotApi({"getUpdates": updates, "sendMessage": flaky}))
    assert rig.bot.poll() == 1


def test_owner_link_routes(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.links.link(str(CHAT), "aide")
    app = FastAPI()
    app.include_router(telegram_router(rig.telegram, KEY, dependencies=[]))
    with TestClient(app) as client:
        body = client.get("/telegram/link", params={"role": "aide"}).json()
        assert body["url"].startswith("https://t.me/rulecam_site_bot?start=")
        assert body["qr_png_base64"]
        assert client.get("/telegram/link", params={"role": "x" * 60}).status_code == 422
        assert client.get("/telegram/link", params={"role": "no spaces"}).status_code == 422
        assert client.get("/telegram/links").json() == {"chats_per_role": {"aide": 1}}

    def down(_: Mapping[str, object]) -> Reply:
        raise ModelTransportError("down")

    offline = TelegramNotifier(
        inner=rig.lan, api=BotApi(FakeBotApi({"getMe": down})), links=rig.links
    )
    app = FastAPI()
    app.include_router(telegram_router(offline, KEY, dependencies=[]))
    with TestClient(app) as client:
        assert client.get("/telegram/link", params={"role": "aide"}).status_code == 502


def test_build_telegram_from_settings(tmp_path: Path) -> None:
    lan = LanNotifier(bus=Bus(), clock=FakeClock())
    assert build_telegram(Settings(_env_file=None), inner=lan) is None
    settings = Settings(
        _env_file=None,
        telegram_bot_token="123:abc",  # noqa: S106 - test fixture
        telegram_bot_username="site_bot",
        rulecam_data_dir=tmp_path,
    )
    notifier = build_telegram(settings, inner=lan)
    assert notifier is not None
    assert notifier.username() == "site_bot"


def test_httpx_transport_without_a_key_sends_no_authorization() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": []})

    transport = HttpxTransport(
        "https://api.telegram.org/bot123:abc",
        "",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert BotApi(transport).call("getUpdates", {"json": {"timeout": 0}}) == []
    assert "authorization" not in seen[0].headers
    assert seen[0].url.path == "/bot123:abc/getUpdates"


def test_false_alarm_taps_become_rule_ci_regressions(tmp_path: Path) -> None:
    rule = make_rule(
        {"op": "inside", "object": "person", "zone": "sink"},
        concepts={"sink": "sink", "person": "person"},
        rule_id="wash",
    )
    plan: list[ActivePlan | None] = [None]
    sink = false_alarm_regressions(lambda: plan[0], HardNegatives(tmp_path))
    sink("ev-1", "wash")  # no plan yet: skipped
    assert load_regressions("wash", tmp_path) == []
    plan[0] = ActivePlan(rules=(rule,))
    sink("ev-1", "other-rule")
    sink("ev-1", "wash")
    sink("ev-1", "wash")  # the same event is remembered once
    (item,) = load_regressions("wash", tmp_path)
    assert item.lookalike == "false alarm ev-1"
    assert "mistook for a sink" in item.prompt
    empty = rule.model_copy(update={"concepts": {}})
    assert HardNegatives(tmp_path).from_false_alarm(empty, "ev-2") is None
