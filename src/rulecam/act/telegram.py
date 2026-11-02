"""Telegram alerts with inline buttons (add-on 5.1), over the Telegram Bot API.

WhatsApp needs every phone to message first and only reaches test recipients; Telegram works on
anyone's phone as soon as they open a link, and needs no public webhook.

- **Onboarding without phone numbers.** The owner asks for a link per role
  (`GET /telegram/link?role=aide`): `https://t.me/<bot>?start=<role-token>`. The token is the
  role plus an HMAC of it keyed from the bot token, so only the owner can mint one. Opening it
  sends `/start <role-token>`; the bot then remembers *chat id → role* in
  `<data>/telegram/links.json`. No phone number or name is ever stored, and logs and chain records
  carry roles only. `/stop` unlinks the chat.
- **Sending.** `TelegramNotifier` wraps the outbound notifier (LAN, or WhatsApp over LAN): the
  LAN app always gets the alert first (it holds the alert id), then every chat linked to the role
  gets the face-blurred evidence still as a photo (`sendPhoto`; text only when there is no
  still) with *On my way* / *False alarm* / *Escalate* inline buttons. A Telegram failure is
  logged and never loses the alert.
- **Taps.** `TelegramBot.poll` long-polls `getUpdates` (no public webhook). A button's callback
  data is `<alert id>|<code>`; the tap runs the same `handle_button` as the LAN app (ack,
  false-alarm `label` for rule health, escalate), as the role the chat is linked to. *False
  alarm* also reaches the API's Rule CI regression sink (`rule_ci.hard_negatives`). Taps from
  chats that are not linked are refused.
- **Delivery.** `getUpdates` with `offset` confirms every update it has seen, so Telegram never
  hands the same tap over twice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Final

from fastapi import APIRouter, HTTPException, Query, params, status

from rulecam.act.api import (
    MAX_ROLE,
    ROLE_PATTERN,
    Button,
    FalseAlarmSink,
    RespondRequest,
    handle_button,
)
from rulecam.act.escalation import EscalationManager
from rulecam.act.notify import LanNotifier
from rulecam.capture.pair_phone.service import qr_png_base64
from rulecam.clients.errors import ModelClientError
from rulecam.clients.transport import HttpReply, Transport, parse_reply
from rulecam.contracts import Frozen, Notifier
from rulecam.errors import RulecamError
from rulecam.evidence.chain import EventChain
from rulecam.log import get_logger

_log = get_logger(__name__)

API_BASE: Final = "https://api.telegram.org"
SEND_TIMEOUT_S: Final = 10.0
POLL_TIMEOUT_S: Final = 5  # long-poll wait; short, so shutdown never waits long
ERROR_BACKOFF_S: Final = 5.0
MAX_CAPTION: Final = 1024
MAX_TEXT: Final = 4096
MAX_START_PARAM: Final = 64  # Telegram's limit on the deep-link `start` parameter
SIG_CHARS: Final = 16
TOKEN_SEPARATOR: Final = "_"  # noqa: S105 - a separator, not a secret
CALLBACK_SEPARATOR: Final = "|"
PHOTO_TYPES: Final = frozenset({"image/png", "image/jpeg"})
CODES: Final[dict[str, Button]] = {"ack": "On my way", "fa": "False alarm", "esc": "Escalate"}
CODE_OF: Final[dict[str, str]] = {button: code for code, button in CODES.items()}
ROLE_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
ANSWERS: Final[dict[str, str]] = {
    "acknowledged": "Acknowledged. Thank you.",
    "false_alarm": "Recorded as a false alarm.",
    "escalated": "Escalated to the next role.",
    "nothing_open": "Nothing left to escalate.",
}
WELCOME: Final = (
    "Linked to the role {role}. Rulecam alerts for {role} will arrive here. Send /stop to unlink."
)
NOT_LINKED: Final = "Ask the site owner for a Rulecam link: this chat is not linked to a role."


class TelegramError(RulecamError):
    """The Bot API rejected a call, or a link token or role is invalid."""


def link_key(bot_token: str) -> bytes:
    """The HMAC key for role tokens, derived from the bot token (revoking it revokes links)."""
    return hashlib.sha256(b"rulecam-telegram-link:" + bot_token.encode("utf-8")).digest()


def _signature(key: bytes, role: str) -> str:
    return hmac.new(key, role.encode("utf-8"), hashlib.sha256).hexdigest()[:SIG_CHARS]


def role_token(key: bytes, role: str) -> str:
    """The deep-link `start` parameter for `role`: `<16 hex chars of HMAC>_<role>`.

    Raises:
        TelegramError: If the role is not an identifier or the token would exceed 64 characters.
    """
    token = f"{_signature(key, role)}{TOKEN_SEPARATOR}{role}"
    if not ROLE_RE.match(role) or len(token) > MAX_START_PARAM:
        msg = f"role must be an identifier of at most {MAX_START_PARAM - SIG_CHARS - 1} characters"
        raise TelegramError(msg)
    return token


def role_from_token(key: bytes, token: str) -> str | None:
    """The role a `start` parameter was minted for, or `None` if it is not genuine."""
    signature, sep, role = token.partition(TOKEN_SEPARATOR)
    if not sep or not ROLE_RE.match(role):
        return None
    return role if hmac.compare_digest(signature, _signature(key, role)) else None


def keyboard(alert_id: str, buttons: tuple[str, ...]) -> dict[str, object]:
    """An inline keyboard: one row per button, callback data `<alert id>|<code>`."""
    rows = [
        [{"text": button, "callback_data": f"{alert_id}{CALLBACK_SEPARATOR}{CODE_OF[button]}"}]
        for button in buttons
        if button in CODE_OF
    ]
    return {"inline_keyboard": rows}


class ChatLinks:
    """Chat id → role, persisted as JSON. Thread-safe. Holds no phone numbers or names."""

    def __init__(self, path: Path) -> None:
        """Load the links (a missing or unreadable file starts empty, logged).

        Args:
            path: The JSON file (`<data>/telegram/links.json`).
        """
        self._path = path
        self._lock = threading.Lock()
        self._links: dict[str, str] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _log.warning("telegram_links_unreadable")
                raw = {}
            if isinstance(raw, dict):
                self._links = {
                    str(k): v for k, v in raw.items() if isinstance(v, str) and ROLE_RE.match(v)
                }

    def link(self, chat_id: str, role: str) -> None:
        """Link a chat to a role (replacing any earlier role) and save."""
        with self._lock:
            self._links[chat_id] = role
            self._save()

    def unlink(self, chat_id: str) -> str | None:
        """Unlink a chat and save; returns the role it had."""
        with self._lock:
            role = self._links.pop(chat_id, None)
            self._save()
            return role

    def role_for(self, chat_id: str) -> str | None:
        """The role a chat is linked to."""
        with self._lock:
            return self._links.get(chat_id)

    def chats_for(self, role: str) -> list[str]:
        """Every chat linked to `role`."""
        with self._lock:
            return [chat for chat, linked in self._links.items() if linked == role]

    def counts(self) -> dict[str, int]:
        """How many chats each role has (no chat ids)."""
        with self._lock:
            out: dict[str, int] = {}
            for role in self._links.values():
                out[role] = out.get(role, 0) + 1
            return out

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._links, sort_keys=True), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as exc:
            _log.warning("telegram_links_not_saved", error=type(exc).__name__)


class BotApi:
    """Calls Bot API methods through a transport rooted at `https://api.telegram.org/bot<token>`."""

    def __init__(self, transport: Transport) -> None:
        """Create the client.

        Args:
            transport: HTTP to the bot's API root (`HttpxTransport`, a fake in tests).
        """
        self._transport = transport

    def call(self, method: str, request: Mapping[str, object] | None = None) -> object:
        """Call `method` with a JSON body (or a prepared form/multipart request).

        Returns:
            The API's `result`.

        Raises:
            TelegramError: If Telegram did not answer `ok`.
        """
        full: dict[str, object] = {"method": "POST", "path": f"/{method}"}
        full.update(request or {})
        reply: HttpReply = parse_reply(self._transport(full))
        body = reply.body if isinstance(reply.body, dict) else {}
        if not reply.ok or body.get("ok") is not True:
            description = str(body.get("description", "")) or reply.error_message()
            msg = f"Telegram {method} answered {reply.status}: {description}"
            raise TelegramError(msg)
        return body.get("result")


class TelegramNotifier:
    """`Notifier`: the wrapped notifier always, plus every Telegram chat linked to the role."""

    def __init__(
        self, *, inner: Notifier, api: BotApi, links: ChatLinks, bot_username: str | None = None
    ) -> None:
        """Create the notifier.

        Args:
            inner: The notifier that stores the LAN alert (and returns its id).
            api: The Bot API.
            links: Chat → role links.
            bot_username: The bot's `@username` (without @); asked from `getMe` when absent.
        """
        self._inner = inner
        self._api = api
        self._links = links
        self._username = bot_username.removeprefix("@") if bot_username else None

    @property
    def links(self) -> ChatLinks:
        """The chat links."""
        return self._links

    def username(self) -> str:
        """The bot's username (from `getMe` once, when not configured)."""
        if self._username is None:
            me = self._api.call("getMe")
            name = me.get("username") if isinstance(me, dict) else None
            if not isinstance(name, str) or not name:
                msg = "getMe returned no username"
                raise TelegramError(msg)
            self._username = name
        return self._username

    def link_url(self, key: bytes, role: str) -> str:
        """The `t.me` deep link that links a chat to `role`."""
        return f"https://t.me/{self.username()}?start={role_token(key, role)}"

    def send(
        self,
        role: str,
        title: str,
        body: str,
        *,
        clip_path: str | None = None,
        buttons: tuple[str, ...] = (),
    ) -> str:
        """Notify `role`: through the inner notifier, then on every linked Telegram chat.

        Returns:
            The inner notifier's alert id.
        """
        alert_id = self._inner.send(role, title, body, clip_path=clip_path, buttons=buttons)
        chats = self._links.chats_for(role)
        if not chats:
            return alert_id
        markup = keyboard(alert_id, buttons) if buttons else None
        text = f"{title}\n{body}"
        photo = _photo(clip_path)
        file_id: str | None = None
        for chat in chats:
            try:
                file_id = self._send_one(chat, text, markup, photo, file_id)
                _log.info("telegram_sent", role=role, alert_id=alert_id)
            except (TelegramError, ModelClientError, OSError) as exc:
                _log.warning("telegram_failed", role=role, error=type(exc).__name__)
        return alert_id

    def _send_one(
        self,
        chat: str,
        text: str,
        markup: dict[str, object] | None,
        photo: Path | None,
        file_id: str | None,
    ) -> str | None:
        """Send one chat the photo (reusing an uploaded `file_id`) or the text."""
        extra: dict[str, object] = {"reply_markup": markup} if markup else {}
        if photo is None:
            self._api.call(
                "sendMessage", {"json": {"chat_id": chat, "text": text[:MAX_TEXT], **extra}}
            )
            return None
        caption = text[:MAX_CAPTION]
        if file_id is not None:
            body = {"chat_id": chat, "photo": file_id, "caption": caption, **extra}
            self._api.call("sendPhoto", {"json": body})
            return file_id
        form: dict[str, object] = {"chat_id": chat, "caption": caption}
        if markup:
            form["reply_markup"] = json.dumps(markup)
        content_type = mimetypes.guess_type(photo.name)[0] or "image/png"
        result = self._api.call(
            "sendPhoto",
            {
                "form": form,
                "parts": {
                    "photo": {
                        "filename": photo.name,
                        "content_type": content_type,
                        "content": photo.read_bytes(),
                    }
                },
            },
        )
        return _largest_file_id(result)


def _photo(clip_path: str | None) -> Path | None:
    if not clip_path:
        return None
    path = Path(clip_path)
    kind = mimetypes.guess_type(path.name)[0]
    return path if kind in PHOTO_TYPES and path.is_file() else None


def _largest_file_id(result: object) -> str | None:
    photos = result.get("photo") if isinstance(result, dict) else None
    if not isinstance(photos, list) or not photos or not isinstance(photos[-1], dict):
        return None
    file_id = photos[-1].get("file_id")
    return str(file_id) if file_id else None


class TelegramBot:
    """Long-polls `getUpdates` and turns `/start`, `/stop` and button taps into actions."""

    def __init__(
        self,
        *,
        api: BotApi,
        links: ChatLinks,
        key: bytes,
        lan: LanNotifier,
        escalation: EscalationManager,
        chain: EventChain,
        on_false_alarm: FalseAlarmSink | None = None,
        poll_timeout_s: int = POLL_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the bot.

        Args:
            api: The Bot API, on a transport whose timeout exceeds `poll_timeout_s`.
            links: Chat → role links.
            key: The role-token HMAC key (`link_key`).
            lan: The LAN alert store (alert id → event).
            escalation: Escalation chains.
            chain: Evidence chain (false-alarm labels).
            on_false_alarm: Told about every *False alarm* tap (Rule CI regressions).
            poll_timeout_s: Long-poll wait in seconds.
            sleep: Waits after a failed poll (injected in tests).
        """
        self._api = api
        self._links = links
        self._key = key
        self._lan = lan
        self._escalation = escalation
        self._chain = chain
        self._on_false_alarm = on_false_alarm
        self._timeout = poll_timeout_s
        self._sleep = sleep
        self._offset: int | None = None

    def poll(self) -> int:
        """Fetch and handle one batch of updates (blocks up to the long-poll timeout).

        A failed fetch is logged and followed by a short wait, so a loop calling this back to
        back never spins.

        Returns:
            How many updates were handled.
        """
        body: dict[str, object] = {
            "timeout": self._timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if self._offset is not None:
            body["offset"] = self._offset
        try:
            updates = self._api.call("getUpdates", {"json": body})
        except (TelegramError, ModelClientError) as exc:
            _log.warning("telegram_poll_failed", error=type(exc).__name__)
            self._sleep(ERROR_BACKOFF_S)
            return 0
        handled = 0
        for update in updates if isinstance(updates, list) else []:
            if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
                continue
            self._offset = int(update["update_id"]) + 1
            try:
                handled += self.handle(update)
            except (TelegramError, ModelClientError) as exc:
                _log.warning("telegram_update_failed", error=type(exc).__name__)
        return handled

    def handle(self, update: Mapping[str, object]) -> int:
        """Handle one update: a `/start` or `/stop` message, or a button tap."""
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            return self._tap(callback)
        message = update.get("message")
        if isinstance(message, dict):
            return self._command(message)
        return 0

    def _command(self, message: Mapping[str, object]) -> int:
        chat = _chat_id(message)
        text = message.get("text")
        if chat is None or not isinstance(text, str):
            return 0
        command, _, argument = text.strip().partition(" ")
        command = command.split("@", maxsplit=1)[0]
        if command == "/start":
            role = role_from_token(self._key, argument.strip())
            if role is None:
                self._reply(chat, NOT_LINKED)
                return 1
            self._links.link(chat, role)
            _log.info("telegram_linked", role=role)
            self._reply(chat, WELCOME.format(role=role))
            return 1
        if command == "/stop":
            role = self._links.unlink(chat)
            _log.info("telegram_unlinked", role=role)
            self._reply(chat, "Unlinked. No more Rulecam alerts here.")
            return 1
        return 0

    def _tap(self, callback: Mapping[str, object]) -> int:
        callback_id = str(callback.get("id", ""))
        message = callback.get("message")
        chat = _chat_id(message) if isinstance(message, dict) else None
        role = self._links.role_for(chat) if chat is not None else None
        if role is None:
            self._answer(callback_id, NOT_LINKED)
            return 0
        alert_id, _, code = str(callback.get("data", "")).partition(CALLBACK_SEPARATOR)
        button = CODES.get(code)
        alert = self._lan.get(alert_id)
        if button is None or alert is None or alert.event_id is None:
            self._answer(callback_id, "This alert is no longer open in Rulecam.")
            return 0
        action = handle_button(
            RespondRequest.model_validate({"button": button, "role": role}),
            alert.event_id,
            notifier=self._lan,
            escalation=self._escalation,
            chain=self._chain,
            rule_id=alert.rule_id,
            on_false_alarm=self._on_false_alarm,
        )
        _log.info("telegram_tap", role=role, event_id=alert.event_id, action=action)
        self._answer(callback_id, ANSWERS[action])
        if action in {"acknowledged", "false_alarm"} and isinstance(message, dict):
            self._clear_buttons(chat, message.get("message_id"))
        return 1

    def _reply(self, chat: str, text: str) -> None:
        self._api.call("sendMessage", {"json": {"chat_id": chat, "text": text}})

    def _answer(self, callback_id: str, text: str) -> None:
        if callback_id:
            self._api.call(
                "answerCallbackQuery", {"json": {"callback_query_id": callback_id, "text": text}}
            )

    def _clear_buttons(self, chat: str | None, message_id: object) -> None:
        if chat is None or not isinstance(message_id, int):
            return
        try:
            self._api.call(
                "editMessageReplyMarkup",
                {
                    "json": {
                        "chat_id": chat,
                        "message_id": message_id,
                        "reply_markup": {"inline_keyboard": []},
                    }
                },
            )
        except TelegramError as exc:  # cosmetic: the tap is already recorded
            _log.info("telegram_buttons_kept", error=type(exc).__name__)


def _chat_id(message: Mapping[str, object]) -> str | None:
    chat = message.get("chat")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    return str(chat_id) if isinstance(chat_id, int) else None


class TelegramLink(Frozen):
    """Body of `GET /telegram/link`: the deep link and its QR code for one role."""

    role: str
    url: str
    qr_png_base64: str


class TelegramLinks(Frozen):
    """Body of `GET /telegram/links`: how many chats each role has (never chat ids)."""

    chats_per_role: dict[str, int]


def telegram_router(
    notifier: TelegramNotifier, key: bytes, *, dependencies: Sequence[params.Depends]
) -> APIRouter:
    """Owner routes that mint `t.me` links (`GET /telegram/link?role=…`) and count linked chats.

    Args:
        notifier: The Telegram notifier (bot username, links).
        key: The role-token HMAC key.
        dependencies: Owner authentication.

    Returns:
        The router.
    """
    router = APIRouter(prefix="/telegram", tags=["telegram"], dependencies=list(dependencies))

    @router.get("/link")
    def link(
        role: Annotated[str, Query(min_length=1, max_length=MAX_ROLE, pattern=ROLE_PATTERN)],
    ) -> TelegramLink:
        try:
            url = notifier.link_url(key, role)
        except TelegramError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except ModelClientError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Telegram did not answer") from exc
        return TelegramLink(role=role, url=url, qr_png_base64=qr_png_base64(url))

    @router.get("/links")
    def links() -> TelegramLinks:
        return TelegramLinks(chats_per_role=notifier.links.counts())

    return router
