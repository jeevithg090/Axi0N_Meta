"""The spoken warning as a WhatsApp voice note: sent, skipped or dry-run, chained every time."""

from __future__ import annotations

import io
import shutil
import wave
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rulecam.evidence.chain import EventChain, read_chain
from rulecam.warehouse.models import SpokenPart, SpokenWarning
from rulecam.warehouse.talks import Store
from rulecam.warehouse.voice_note import VoiceNoteError, VoiceNoteSender, to_ogg_opus
from tests.fakes import FakeClock

NOW = datetime(2026, 11, 4, 2, 30, tzinfo=UTC)


def _wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\x00\x10" * 16_000)
    return out.getvalue()


class Phone:
    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.sent: list[tuple[str, bytes, str]] = []

    def voice_note(self, role: str, audio: bytes, *, filename: str) -> str | None:
        self.sent.append((role, audio, filename))
        return self.answer


def _warning(store: Store) -> SpokenWarning:
    name = store.put_file("warning-ev-1-es_en.wav", _wav())
    return SpokenWarning(
        event_id="ev-1",
        lang="es",
        text="¡Montacargas acercándose!",
        english="Forklift coming!",
        audio=name,
        model="muse-spark-1.3",
        created_at=NOW,
        parts=(SpokenPart(lang="es", text="¡…!"), SpokenPart(lang="en", text="…!")),
    )


@pytest.fixture
def chain(tmp_path: Path) -> EventChain:
    return EventChain(tmp_path / "chain", "demo", clock=FakeClock(NOW))


def test_sent_skipped_and_dry_run_are_all_chained(tmp_path: Path, chain: EventChain) -> None:
    store = Store(tmp_path / "w")
    warning = _warning(store)
    phone = Phone("wamid.9")
    sender = VoiceNoteSender(
        phone,
        chain=chain,
        store=store,
        clock=FakeClock(NOW),
        dry_run=False,
        convert=lambda w: b"OggS",
    )
    sent = sender.send(warning, "supervisor")
    assert sent.sent is True
    assert sent.message_id == "wamid.9"
    assert sent.langs == ("es", "en")
    assert phone.sent == [("supervisor", b"OggS", "warning-ev-1-es_en.ogg")]
    skipped = VoiceNoteSender(
        Phone(None),
        chain=chain,
        store=store,
        clock=FakeClock(NOW),
        dry_run=False,
        convert=lambda w: b"OggS",
    ).send(warning, "nobody")
    assert skipped.sent is False
    dry = VoiceNoteSender(None, chain=chain, store=store, clock=FakeClock(NOW), dry_run=True)
    recorded = dry.send(warning, "supervisor")
    assert recorded.detail.startswith("dry-run: would send the spoken warning (es+en)")
    records = read_chain(chain.path_for(NOW.date()))
    assert [(r.kind, r.payload["ok"]) for r in records] == [
        ("action", True),
        ("action", False),
        ("action", True),
    ]
    assert recorded.record_hash == records[-1].hash


def test_no_audio_or_no_whatsapp_is_refused(tmp_path: Path, chain: EventChain) -> None:
    store = Store(tmp_path / "w")
    warning = _warning(store)
    live_without_phone = VoiceNoteSender(
        None, chain=chain, store=store, clock=FakeClock(NOW), dry_run=False
    )
    with pytest.raises(VoiceNoteError, match="not configured"):
        live_without_phone.send(warning, "supervisor")
    with pytest.raises(VoiceNoteError, match="no audio"):
        live_without_phone.send(warning.model_copy(update={"audio": None}), "supervisor")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_to_ogg_opus_makes_a_small_voice_note() -> None:
    note = to_ogg_opus(_wav())
    assert note.startswith(b"OggS")
    assert len(note) < 512 * 1024
    with pytest.raises(VoiceNoteError):
        to_ogg_opus(b"not a wav")
