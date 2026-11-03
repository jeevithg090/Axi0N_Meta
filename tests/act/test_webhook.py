"""Tests for signed outbound webhooks (add-on 5.4): presets, signing, retries and idempotency."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest

from rulecam.act import EscalationManager, LanNotifier
from rulecam.act.notify import for_event
from rulecam.act.webhook import (
    IDEMPOTENCY_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    AlertEvent,
    HttpxPoster,
    WebhookDispatcher,
    WebhookError,
    WebhookNotifier,
    WebhookTarget,
    event_hash,
    parse_targets,
    payload_for,
    sign,
    verify_signature,
)
from rulecam.bus import Bus
from rulecam.config import Settings
from rulecam.evidence.chain import EventChain
from rulecam.runtime.response import build_webhooks
from tests.fakes import EPOCH, FakeClock

SECRET = "whsec-test"  # noqa: S105 - test fixture
GENERIC = WebhookTarget(preset="generic", url="https://n8n.example/webhook/rulecam")
SLACK = WebhookTarget(preset="slack", url="https://hooks.slack.com/services/T/B/x")
TEAMS = WebhookTarget(preset="teams", url="https://prod.workflows.example/teams")


class FakePoster:
    """Answers with scripted statuses (or raises) and records every request."""

    def __init__(self, *outcomes: int | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: Mapping[str, str]) -> int:
        self.calls.append((url, body, dict(headers)))
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def event(**extra: object) -> AlertEvent:
    base: dict[str, object] = {
        "site": "demo",
        "event_id": "ev-1",
        "rule_id": "keep-2m",
        "role": "aide",
        "title": "Person near a moving forklift",
        "body": "Dock 2 <camera> & aisle",
        "ts": EPOCH,
        "link": "https://ui.example/incidents/ev-1",
    }
    return AlertEvent.model_validate(base | extra)


def test_parse_targets() -> None:
    assert parse_targets(" slack=https://a/x , generic=http://b/y") == (
        WebhookTarget(preset="slack", url="https://a/x"),
        WebhookTarget(preset="generic", url="http://b/y"),
    )
    assert parse_targets("") == ()
    for bad in ("slack", "discord=https://x", "teams=ftp://x", "=https://x"):
        with pytest.raises(WebhookError):
            parse_targets(bad)


def test_preset_payloads() -> None:
    generic = payload_for("generic", event())
    assert generic["id"] == event_hash(event())
    assert generic["type"] == "rulecam.alert"
    assert generic["link"] == "https://ui.example/incidents/ev-1"
    slack = payload_for("slack", event())
    assert str(slack["text"]).startswith("Person near")
    blocks = slack["blocks"]
    assert isinstance(blocks, list)
    assert blocks[1]["text"]["text"] == "Dock 2 &lt;camera&gt; &amp; aisle"
    assert "<https://ui.example/incidents/ev-1|Incident report>" in blocks[2]["elements"][0]["text"]
    teams = payload_for("teams", event())
    attachment = teams["attachments"][0]  # type: ignore[index]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard"
    assert {"title": "Rule", "value": "keep-2m"} in card["body"][2]["facts"]
    assert card["actions"][0]["url"] == "https://ui.example/incidents/ev-1"
    bare = payload_for("teams", event(link=None, event_id=None, rule_id=None))
    content = bare["attachments"][0]["content"]  # type: ignore[index]
    assert "actions" not in content
    assert [f["title"] for f in content["body"][2]["facts"]] == ["Site", "Role"]
    assert "Incident report" not in json.dumps(payload_for("slack", event(link=None)))


def test_event_hash_is_stable_and_content_addressed() -> None:
    assert event_hash(event()) == event_hash(event())
    assert event_hash(event()) != event_hash(event(role="on_call"))
    assert len(event_hash(event())) == 64


def test_signature_round_trip_and_freshness() -> None:
    body = b'{"a":1}'
    ts = int(EPOCH.timestamp())
    signature = sign(SECRET, ts, body)
    assert signature.startswith("v1=")
    ok = {"timestamp": str(ts), "signature": signature, "body": body}
    assert verify_signature(SECRET, now=EPOCH, **ok)  # type: ignore[arg-type]
    assert not verify_signature("other", now=EPOCH, **ok)  # type: ignore[arg-type]
    tampered = ok | {"body": b'{"a":2}'}
    assert not verify_signature(SECRET, now=EPOCH, **tampered)  # type: ignore[arg-type]
    later = FakeClock()
    later.advance(301)
    assert not verify_signature(SECRET, now=later.now(), **ok)  # type: ignore[arg-type]
    assert not verify_signature(SECRET, timestamp=None, signature=signature, body=body, now=EPOCH)
    assert not verify_signature(SECRET, timestamp="x", signature=signature, body=body, now=EPOCH)


def test_delivery_is_signed_and_carries_the_idempotency_key() -> None:
    clock = FakeClock()
    poster = FakePoster()
    dispatcher = WebhookDispatcher([GENERIC, SLACK], post=poster, clock=clock, secret=SECRET)
    key = dispatcher.enqueue(event())
    assert dispatcher.pending() == 2
    assert dispatcher.deliver_due() == 2
    assert dispatcher.pending() == 0
    (url, body, headers), (slack_url, _, slack_headers) = poster.calls
    assert url == GENERIC.url
    assert slack_url == SLACK.url
    assert headers[IDEMPOTENCY_HEADER] == key == json.loads(body)["id"]
    assert slack_headers[IDEMPOTENCY_HEADER] == key
    assert verify_signature(
        SECRET,
        timestamp=headers[TIMESTAMP_HEADER],
        signature=headers[SIGNATURE_HEADER],
        body=body,
        now=clock.now(),
    )
    dispatcher.enqueue(event())  # the same event again: already queued once per target
    assert dispatcher.pending() == 0
    assert dispatcher.presets == ("generic", "slack")


def test_retries_back_off_and_resign_with_the_same_key() -> None:
    clock = FakeClock()
    poster = FakePoster(503, httpx.ConnectError("refused"), 429, 200)
    dispatcher = WebhookDispatcher([GENERIC], post=poster, clock=clock, secret=SECRET)
    dispatcher.enqueue(event())
    assert dispatcher.deliver_due() == 0  # 503: retry in 2 s
    assert dispatcher.deliver_due() == 0  # not due yet
    assert len(poster.calls) == 1
    clock.advance(2)
    assert dispatcher.deliver_due() == 0  # network error: retry in 4 s
    clock.advance(3)
    assert dispatcher.deliver_due() == 0
    clock.advance(1)
    assert dispatcher.deliver_due() == 0  # 429: retry in 8 s
    clock.advance(8)
    assert dispatcher.deliver_due() == 1
    keys = {h[IDEMPOTENCY_HEADER] for _, _, h in poster.calls}
    stamps = [h[TIMESTAMP_HEADER] for _, _, h in poster.calls]
    assert len(keys) == 1
    assert stamps == sorted(stamps)
    assert stamps[0] != stamps[-1]  # re-signed at each attempt
    assert len({body for _, body, _ in poster.calls}) == 1


def test_final_failures_are_dropped() -> None:
    clock = FakeClock()
    poster = FakePoster(400, 500, 500)
    dispatcher = WebhookDispatcher(
        [SLACK], post=poster, clock=clock, max_attempts=2, base_delay_s=1.0
    )
    dispatcher.enqueue(event())
    dispatcher.deliver_due()  # 400 is final
    assert dispatcher.pending() == 0
    dispatcher.enqueue(event(role="on_call"))
    dispatcher.deliver_due()
    clock.advance(1)
    dispatcher.deliver_due()  # second 500: out of attempts
    assert dispatcher.pending() == 0
    assert len(poster.calls) == 3
    assert SIGNATURE_HEADER not in poster.calls[0][2]  # no secret: unsigned


def test_queue_is_bounded_and_generic_needs_a_secret() -> None:
    dispatcher = WebhookDispatcher([SLACK], post=FakePoster(), clock=FakeClock(), capacity=2)
    for role in ("a", "b", "c"):
        dispatcher.enqueue(event(role=role))
    assert dispatcher.pending() == 2
    with pytest.raises(WebhookError):
        WebhookDispatcher([GENERIC], post=FakePoster(), clock=FakeClock())


def test_poll_waits_then_delivers() -> None:
    dispatcher = WebhookDispatcher([TEAMS], post=FakePoster(), clock=FakeClock())
    dispatcher.enqueue(event())
    assert dispatcher.poll() == 1  # woken by the enqueue: no wait


def test_notifier_sends_first_then_queues_with_the_incident_link(tmp_path: Path) -> None:
    clock = FakeClock()
    bus = Bus()
    lan = LanNotifier(bus=bus, clock=clock)
    poster = FakePoster()
    dispatcher = WebhookDispatcher([GENERIC], post=poster, clock=clock, secret=SECRET)
    notifier = WebhookNotifier(
        inner=lan, dispatcher=dispatcher, clock=clock, site="demo", link_base="https://ui.example/"
    )
    escalation = EscalationManager(
        notifier=notifier,
        clock=clock,
        chain=EventChain(tmp_path / "chain", "demo", clock=clock),
        bus=bus,
    )
    escalation.start("ev-5", ["aide"], after_s=60, title="Person down", body="corridor")
    with for_event("ev-6", "wash"):
        alert_id = notifier.send("aide", "Wash", "skipped", clip_path="/data/strips/x.png")
    notifier.send("aide", "Morning brief", "quiet")  # no event: no link
    assert lan.get(alert_id) is not None
    assert dispatcher.deliver_due() == 3
    bodies = [json.loads(body) for _, body, _ in poster.calls]
    assert bodies[0]["link"] == "https://ui.example/incidents/ev-5"
    assert bodies[1]["rule_id"] == "wash"
    assert bodies[2]["link"] is None
    assert "/data/strips" not in json.dumps(bodies)  # clips never leave the site


def test_httpx_poster_posts_the_raw_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    poster = HttpxPoster(httpx.Client(transport=httpx.MockTransport(handler)))
    assert poster("https://x.example/hook", b'{"a":1}', {"X-Test": "1"}) == 204
    assert seen[0].content == b'{"a":1}'
    assert seen[0].headers["x-test"] == "1"


def test_build_webhooks_from_settings() -> None:
    clock = FakeClock()
    assert build_webhooks(Settings(_env_file=None), clock=clock) is None
    assert build_webhooks(Settings(_env_file=None, rulecam_webhooks=" "), clock=clock) is None
    slack = build_webhooks(
        Settings(_env_file=None, rulecam_webhooks="slack=https://hooks.slack.com/x"), clock=clock
    )
    assert slack is not None
    assert slack.presets == ("slack",)
    with pytest.raises(WebhookError):
        build_webhooks(Settings(_env_file=None, rulecam_webhooks="generic=https://x"), clock=clock)
    signed = build_webhooks(
        Settings(
            _env_file=None,
            rulecam_webhooks="generic=https://x",
            rulecam_webhook_secret=SECRET,
        ),
        clock=clock,
    )
    assert signed is not None
