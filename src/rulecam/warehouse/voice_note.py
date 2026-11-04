"""Send an incident's spoken warning to a role's phone as a WhatsApp voice note.

The warning Spark wrote and MMS-TTS spoke (`talks.WarningMaker`) is turned into Ogg/Opus and sent
through the WhatsApp Cloud API as a voice message (`act.whatsapp.WhatsAppNotifier.voice_note`),
to the number configured for the role, inside its 24-hour window. Every attempt is chained as an
`action` record on the incident, whether it was sent, skipped (no number, window closed) or only
recorded (`RULECAM_ACTIONS_DRY_RUN`, "dry-run: would send …"). Phone numbers are never logged or
chained: only roles.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from typing import Literal, Protocol

from rulecam.contracts import ActionRecord, Clock, EventRecord, Frozen
from rulecam.errors import RulecamError
from rulecam.log import get_logger
from rulecam.warehouse.models import SpokenWarning
from rulecam.warehouse.talks import Store

_log = get_logger(__name__)

CONVERT_TIMEOUT_S = 60.0
MAX_VOICE_BYTES = 512 * 1024


class VoiceNoteError(RulecamError):
    """The warning could not be made into a voice note."""


class VoiceChannel(Protocol):
    """Sends a voice note to a role (`WhatsAppNotifier.voice_note`)."""

    def voice_note(self, role: str, audio: bytes, *, filename: str) -> str | None:
        """Send it; the message id, or `None` when nothing was sent."""
        ...


class ActionChain(Protocol):
    """Appends one record to the evidence chain (`EventChain.append`)."""

    def append(self, kind: Literal["action"], payload: Mapping[str, object]) -> EventRecord:
        """Append and return the record."""
        ...


class VoiceNote(Frozen):
    """What happened to one voice note (`POST /warehouse/incidents/{event_id}/voice-note`).

    Attributes:
        event_id: The incident.
        role: Who it was for.
        langs: The languages it speaks, in order.
        sent: Whether WhatsApp accepted it.
        message_id: WhatsApp's message id, when sent.
        detail: What happened, as chained.
        record_hash: Hash of the chained `action` record.
    """

    event_id: str
    role: str
    langs: tuple[str, ...]
    sent: bool
    message_id: str | None = None
    detail: str
    record_hash: str


def to_ogg_opus(wav: bytes) -> bytes:
    """The WAV as Ogg/Opus, the format WhatsApp plays as a voice message.

    Raises:
        VoiceNoteError: When ffmpeg cannot convert it or the note is over WhatsApp's 512 KB.
    """
    args = ["ffmpeg", "-v", "error", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "24k"]
    args += ["-f", "ogg", "pipe:1"]
    try:
        done = subprocess.run(  # noqa: S603 - fixed argument list, audio on stdin
            args, input=wav, capture_output=True, timeout=CONVERT_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"could not convert the warning: {type(exc).__name__}"
        raise VoiceNoteError(msg) from exc
    if done.returncode != 0 or not done.stdout:
        msg = "could not convert the warning to Ogg/Opus"
        raise VoiceNoteError(msg)
    if len(done.stdout) > MAX_VOICE_BYTES:
        msg = "the voice note is over WhatsApp's 512 KB"
        raise VoiceNoteError(msg)
    return done.stdout


class VoiceNoteSender:
    """Sends (or, in dry-run, records) a warning as a voice note, and chains what happened."""

    def __init__(
        self,
        channel: VoiceChannel | None,
        *,
        chain: ActionChain,
        store: Store,
        clock: Clock,
        dry_run: bool,
        convert: Callable[[bytes], bytes] = to_ogg_opus,
    ) -> None:
        """Create the sender.

        Args:
            channel: WhatsApp (`None` when it is not configured).
            chain: The evidence chain.
            store: Where the warning's WAV is.
            clock: Time source.
            dry_run: Record "would send" instead of sending (`RULECAM_ACTIONS_DRY_RUN`).
            convert: WAV to Ogg/Opus.
        """
        self._channel = channel
        self._chain = chain
        self._store = store
        self._clock = clock
        self._dry_run = dry_run
        self._convert = convert

    def send(self, warning: SpokenWarning, role: str) -> VoiceNote:
        """Send the warning's WAV to `role` as a voice note; chained either way.

        Raises:
            VoiceNoteError: When the warning has no audio, WhatsApp is not configured (outside
                dry-run) or the conversion fails.
        """
        wav = self._store.file(warning.audio) if warning.audio else None
        if wav is None:
            msg = "this warning has no audio to send"
            raise VoiceNoteError(msg)
        langs = tuple(p.lang for p in warning.parts) or (warning.lang,)
        what = f"the spoken warning ({'+'.join(langs)}) as a WhatsApp voice note to {role}"
        message_id: str | None = None
        if self._dry_run:
            detail, ok = f"dry-run: would send {what}", True
        elif self._channel is None:
            msg = "WhatsApp is not configured (WHATSAPP_TOKEN, WHATSAPP_RECIPIENTS)"
            raise VoiceNoteError(msg)
        else:
            note = self._convert(wav.read_bytes())
            filename = f"{wav.stem}.ogg"
            message_id = self._channel.voice_note(role, note, filename=filename)
            ok = message_id is not None
            detail = (
                f"sent {what}"
                if ok
                else f"not sent: no number for {role}, or its 24-hour window is closed"
            )
        record = ActionRecord(
            kind="notify", role=role, gate="human", ts=self._clock.now(), ok=ok, detail=detail
        )
        chained = self._chain.append(
            "action", {"event_id": warning.event_id, **record.model_dump(mode="json")}
        )
        _log.info("voice_note", event_id=warning.event_id, role=role, sent=bool(message_id))
        return VoiceNote(
            event_id=warning.event_id,
            role=role,
            langs=langs,
            sent=bool(message_id),
            message_id=message_id or None,
            detail=detail,
            record_hash=chained.hash,
        )
