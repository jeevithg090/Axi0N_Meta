"""WhatsApp escalation with reply buttons (task P3-S1), over the WhatsApp Cloud API.

- **Sending.** `WhatsAppNotifier` always stores the alert in the LAN app first (the fallback and the
  source of the alert id), then, for a role whose phone opened the **24-hour window** by messaging
  "start", sends an interactive reply-button message: the skeleton clip as the header (uploaded as
  WhatsApp media, never exposed at a public URL), the alert text, and *On my way* / *False alarm* /
  *Escalate*. Outside the window the Cloud API only allows templates, so the LAN app carries it.
- **Webhook.** `GET /whatsapp/webhook` answers Meta's subscription handshake with the verify
  token. `POST /whatsapp/webhook` checks `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with
  the app secret), then turns each button tap into the same ack / false-alarm label / escalation as
  the LAN buttons (`handle_button`). Taps from numbers that are not a configured role are ignored.
  Meta delivers webhooks at least once, so each message id is handled once (a bounded seen-set),
  and messages older than `REPLAY_WINDOW` are dropped. The work runs in the threadpool, off the
  event loop, because a tap may send the next escalation over the network.
- **Timeouts.** Graph calls use a short timeout (`GRAPH_TIMEOUT_S`), and the escalation manager
  sends them outside its lock, so a slow Graph API never stalls acks or the timers.
- **Privacy.** Phone numbers live only in `.env` (`WHATSAPP_RECIPIENTS`) and are never logged:
  logs and chain records carry roles only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import threading
from collections import OrderedDict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from rulecam.act.api import RespondRequest, handle_button
from rulecam.act.escalation import EscalationManager
from rulecam.act.notify import LanNotifier, alert_event
from rulecam.clients.errors import ModelClientError
from rulecam.clients.transport import HttpReply, Transport, parse_reply
from rulecam.contracts import Clock
from rulecam.errors import RulecamError
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger

_log = get_logger(__name__)

WINDOW = timedelta(hours=24)
BUTTONS = ("On my way", "False alarm", "Escalate")
MAX_BODY_CHARS = 1024
MAX_TITLE_CHARS = 20
ID_SEPARATOR = "|"
HEADER_TYPES = {"image/png": "image", "image/jpeg": "image", "video/mp4": "video"}
GRAPH_TIMEOUT_S = 10.0
REPLAY_WINDOW = timedelta(minutes=10)
SEEN_CAPACITY = 4096


class WhatsAppError(RulecamError):
    """The Cloud API rejected a call, or the configuration is invalid."""


def parse_recipients(spec: str) -> dict[str, str]:
    """Parse `role=number,role=number` into `{role: digits}` (a leading + is dropped).

    Raises:
        WhatsAppError: For a malformed pair or a number that is not 8 to 15 digits.
    """
    out: dict[str, str] = {}
    for pair in filter(None, (p.strip() for p in spec.split(","))):
        role, sep, number = pair.partition("=")
        digits = number.strip().removeprefix("+")
        if not sep or not role.strip() or not digits.isdigit() or not 8 <= len(digits) <= 15:  # noqa: PLR2004 - E.164
            msg = "WHATSAPP_RECIPIENTS must look like role=15551234567,role2=15557654321"
            raise WhatsAppError(msg)
        out[role.strip()] = digits
    return out


class SeenIds:
    """A bounded, thread-safe set of recently seen ids (oldest forgotten first)."""

    def __init__(self, capacity: int = SEEN_CAPACITY) -> None:
        """Create the set.

        Args:
            capacity: How many ids to remember.
        """
        self._capacity = capacity
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, key: str) -> bool:
        """Remember `key`; returns whether it was new."""
        with self._lock:
            if key in self._ids:
                return False
            self._ids[key] = None
            if len(self._ids) > self._capacity:
                self._ids.popitem(last=False)
            return True


def sent_at(message: Mapping[str, object]) -> datetime | None:
    """When a webhook message was sent (its unix `timestamp`), or `None` if unreadable."""
    try:
        return datetime.fromtimestamp(int(str(message.get("timestamp", ""))), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def button_message(
    to: str, body: str, *, event_id: str, media_id: str | None, media_kind: str
) -> dict[str, object]:
    """An interactive reply-button message (Cloud API `type: interactive`, `button`).

    Button ids carry the event: `<event_id>|<button>`, so a tap maps back to the alert.
    """
    interactive: dict[str, object] = {
        "type": "button",
        "body": {"text": body[:MAX_BODY_CHARS]},
        "action": {
            "buttons": [
                {
                    "type": "reply",
                    "reply": {"id": f"{event_id}{ID_SEPARATOR}{b}", "title": b[:MAX_TITLE_CHARS]},
                }
                for b in BUTTONS
            ]
        },
    }
    if media_id is not None:
        interactive["header"] = {"type": media_kind, media_kind: {"id": media_id}}
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }


class WhatsAppNotifier:
    """`Notifier`: the LAN app always, plus WhatsApp for roles inside their 24-hour window."""

    def __init__(
        self,
        *,
        lan: LanNotifier,
        transport: Transport,
        phone_number_id: str,
        recipients: Mapping[str, str],
        clock: Clock,
    ) -> None:
        """Create the notifier.

        Args:
            lan: The LAN notifier (fallback, and the alert id).
            transport: Bearer-authenticated HTTP to the Graph API base (`HttpxTransport`).
            phone_number_id: The business phone number id.
            recipients: Role → phone number (digits).
            clock: Time source for the 24-hour window.
        """
        self._lan = lan
        self._transport = transport
        self._phone_id = phone_number_id
        self._recipients = dict(recipients)
        self._roles_by_number = {number: role for role, number in self._recipients.items()}
        self._clock = clock
        self._last_inbound: dict[str, datetime] = {}
        self._seen = SeenIds()

    def role_for(self, number: str) -> str | None:
        """The role a phone number belongs to, or `None`."""
        return self._roles_by_number.get(number.removeprefix("+"))

    def opened(self, role: str) -> None:
        """Record an inbound message from `role` (it opens the 24-hour window)."""
        self._last_inbound[role] = self._clock.now()

    def accept(self, message: Mapping[str, object]) -> bool:
        """Whether an inbound message is new and recent; records its id as seen.

        Duplicate deliveries (same message id) and messages sent more than `REPLAY_WINDOW` ago,
        or with no readable id or timestamp, are refused.
        """
        when = sent_at(message)
        message_id = str(message.get("id", ""))
        if when is None or not message_id or self._clock.now() - when > REPLAY_WINDOW:
            _log.info("whatsapp_stale_message")
            return False
        if not self._seen.add(message_id):
            _log.info("whatsapp_duplicate_message")
            return False
        return True

    def window_open(self, role: str) -> bool:
        """Whether WhatsApp may send `role` a free-form message right now."""
        last = self._last_inbound.get(role)
        return last is not None and self._clock.now() - last < WINDOW

    def _call(self, request: Mapping[str, object]) -> HttpReply:
        reply = parse_reply(self._transport(request))
        if not reply.ok:
            msg = f"WhatsApp API answered {reply.status}: {reply.error_message()}"
            raise WhatsAppError(msg)
        return reply

    def upload(self, clip: Path) -> tuple[str, str] | None:
        """Upload a clip or still as WhatsApp media; returns (media id, header kind) or `None`."""
        content_type = mimetypes.guess_type(clip.name)[0] or ""
        kind = HEADER_TYPES.get(content_type)
        if kind is None or not clip.is_file():
            return None
        reply = self._call(
            {
                "method": "POST",
                "path": f"/{self._phone_id}/media",
                "form": {"messaging_product": "whatsapp", "type": content_type},
                "file": {
                    "filename": clip.name,
                    "content_type": content_type,
                    "content": clip.read_bytes(),
                },
            }
        )
        media_id = reply.body.get("id") if isinstance(reply.body, dict) else None
        return (str(media_id), kind) if media_id else None

    def send(
        self,
        role: str,
        title: str,
        body: str,
        *,
        clip_path: str | None = None,
        buttons: tuple[str, ...] = (),
    ) -> str:
        """Notify `role`: always in the LAN app, and on WhatsApp when its window is open.

        A WhatsApp failure is logged and never loses the alert: the LAN copy is already stored.

        Returns:
            The LAN alert id.
        """
        alert_id = self._lan.send(role, title, body, clip_path=clip_path, buttons=buttons)
        number = self._recipients.get(role)
        event_id = alert_event.get()
        if number is None or event_id is None or not self.window_open(role):
            _log.info("whatsapp_skipped", role=role, configured=number is not None)
            return alert_id
        try:
            media = self.upload(Path(clip_path)) if clip_path else None
            message = button_message(
                number,
                f"{title}\n{body}",
                event_id=event_id,
                media_id=media[0] if media else None,
                media_kind=media[1] if media else "image",
            )
            self._call({"method": "POST", "path": f"/{self._phone_id}/messages", "json": message})
            _log.info("whatsapp_sent", role=role, event_id=event_id)
        except (WhatsAppError, ModelClientError, OSError) as exc:
            _log.warning("whatsapp_failed", role=role, error=type(exc).__name__)
        return alert_id


def valid_signature(raw: bytes, header: str | None, app_secret: str) -> bool:
    """Whether `X-Hub-Signature-256` is `sha256=<HMAC-SHA256(app_secret, raw)>` (constant time)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _dicts(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _messages(payload: Mapping[str, object]) -> list[dict[str, object]]:
    """Every message in a Cloud API webhook payload (entry -> changes -> value -> messages)."""
    out: list[dict[str, object]] = []
    for entry in _dicts(payload.get("entry")):
        for change in _dicts(entry.get("changes")):
            value = change.get("value")
            if isinstance(value, dict):
                out.extend(_dicts(value.get("messages")))
    return out


def whatsapp_router(
    *,
    notifier: WhatsAppNotifier,
    lan: LanNotifier,
    escalation: EscalationManager,
    chain: EventChain,
    app_secret: str,
    verify_token: str,
) -> APIRouter:
    """The Cloud API webhook (`/whatsapp/webhook`), reachable through the public tunnel.

    It is deliberately not owner-only: Meta calls it. Every POST must carry a valid signature.
    """
    router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

    @router.get("/webhook", response_class=PlainTextResponse)
    def verify(
        mode: Annotated[str, Query(alias="hub.mode")] = "",
        token: Annotated[str, Query(alias="hub.verify_token")] = "",
        challenge: Annotated[str, Query(alias="hub.challenge", max_length=128)] = "",
    ) -> str:
        if mode != "subscribe" or not hmac.compare_digest(token, verify_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "verification failed")
        return challenge

    def handle(payload: Mapping[str, object]) -> int:
        handled = 0
        for message in _messages(payload):
            role = notifier.role_for(str(message.get("from", "")))
            if role is None or not notifier.accept(message):
                continue
            notifier.opened(role)
            handled += _handle_tap(message, role, lan=lan, escalation=escalation, chain=chain)
        return handled

    @router.post("/webhook")
    async def receive(
        request: Request,
        signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
    ) -> dict[str, int]:
        raw = await request.body()
        if not valid_signature(raw, signature, app_secret):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "bad signature")
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "not JSON") from exc
        # A tap may send the next escalation over the network: keep it off the event loop.
        handled = await run_in_threadpool(handle, payload if isinstance(payload, dict) else {})
        return {"handled": handled}

    return router


def _handle_tap(
    message: Mapping[str, object],
    role: str,
    *,
    lan: LanNotifier,
    escalation: EscalationManager,
    chain: EventChain,
) -> int:
    interactive = message.get("interactive")
    reply = interactive.get("button_reply") if isinstance(interactive, dict) else None
    if not isinstance(reply, dict):
        return 0  # a "start" text only opens the window
    event_id, _, button = str(reply.get("id", "")).partition(ID_SEPARATOR)
    if button not in BUTTONS or not event_id:
        return 0
    alert = next((a for a in lan.list() if a.event_id == event_id), None)
    handle_button(
        RespondRequest.model_validate({"button": button, "role": role}),
        event_id,
        notifier=lan,
        escalation=escalation,
        chain=chain,
        rule_id=alert.rule_id if alert else None,
    )
    _log.info("whatsapp_tap", role=role, event_id=event_id, button=button)
    return 1
