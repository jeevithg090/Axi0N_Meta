"""Push-to-talk "Ask" from a staff phone: Voice Transcribe, then the Ask agent, then speech.

A person holds the button and asks ("is aisle 4 still blocked?"). The recording is a WAV the
phone made; Muse Voice Transcribe turns it into text (push-to-talk mode, biased to the chosen
language). Only that one recording is transcribed, on the person's press: never ambient audio.
The question goes to the same Ask agent owners use (`rulecam.ask`: Muse Spark plans, SAM 3.1
counts, Glimmer looks), told who is asking and to answer briefly in their language, and the answer
is spoken back with the staff speaker.
"""

from __future__ import annotations

import time
from typing import Protocol

from rulecam.ask.trace import AskAnswer
from rulecam.errors import RulecamError
from rulecam.staff.models import AskReply
from rulecam.staff.speech import LANG_NAMES, Speaker


class Asker(Protocol):
    """The Ask agent (`AskAgent.ask`)."""

    def ask(self, question: str) -> AskAnswer:
        """Answer one question."""
        ...


class Transcribe(Protocol):
    """Voice Transcribe (`VoiceTranscribeClient.transcribe`)."""

    def transcribe(
        self,
        audio: bytes,
        *,
        lang_bias: list[str] | None = None,
        keywords: list[str] | None = None,
        diarize: bool = False,
    ) -> list[dict[str, str]]:
        """Segments of the recording."""
        ...


class NothingHeardError(RulecamError):
    """The recording had no speech in it."""


def framed(question: str, role: str, lang: str) -> str:
    """The question as the agent gets it: who asks, and how to answer."""
    language = LANG_NAMES.get(lang, lang)
    return (
        f"{question}\n\n(Asked by voice from a staff phone on the floor by the {role} role. "
        f"Answer in {language}, in at most three short sentences that can be read aloud.)"
    )


class VoiceAsk:
    """Transcribes a question, asks the agent and speaks the answer."""

    def __init__(self, *, transcriber: Transcribe | None, agent: Asker, speaker: Speaker) -> None:
        """Create the asker.

        Args:
            transcriber: Muse Voice Transcribe (`None`: typed questions only).
            agent: The Ask agent.
            speaker: Says the answer.
        """
        self._transcriber = transcriber
        self._agent = agent
        self._speaker = speaker

    @property
    def can_listen(self) -> bool:
        """Whether spoken questions are possible."""
        return self._transcriber is not None

    def ask(
        self, *, role: str, lang: str, audio: bytes | None = None, question: str = ""
    ) -> AskReply:
        """Answer a spoken (or typed) question.

        Args:
            role: The asking role.
            lang: The answer's language.
            audio: A 16 kHz mono WAV of the question.
            question: A typed question, used when there is no audio.

        Returns:
            What was heard, the answer, and the answer spoken.

        Raises:
            NothingHeardError: When neither the recording nor the text has a question.
        """
        started = time.perf_counter()
        heard = question.strip()
        models: list[str] = []
        if audio is not None and self._transcriber is not None:
            segments = self._transcriber.transcribe(audio, lang_bias=[lang])
            heard = " ".join(s.get("text", "").strip() for s in segments).strip()
            models.append("muse-voice-transcribe")
        if not heard:
            msg = "no question heard in the recording"
            raise NothingHeardError(msg)
        answer = self._agent.ask(framed(heard, role, lang))
        spoken = self._speaker.say(
            answer.answer, lang, name=f"ask-{answer.receipt[:12] or int(time.time())}"
        )
        return AskReply(
            heard=heard,
            answer=answer.answer,
            lang=lang,
            audio=spoken.audio,
            engine=spoken.engine,
            seconds=round(time.perf_counter() - started, 2),
            models=[*models, *answer.models, *([spoken.engine] if spoken.engine else [])],
        )
