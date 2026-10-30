"""Errors raised by Meta Voice Out."""

from __future__ import annotations

from rulecam.errors import RulecamError


class VoiceOutError(RulecamError):
    """Base class for Voice Out errors; `FallbackTts` falls back on any of them."""


class UnsupportedLanguageError(VoiceOutError):
    """The engine has no voice for the requested language."""


class SynthesisError(VoiceOutError):
    """Speech could not be synthesised (missing engine, failed process, unreadable audio)."""


class TranslationError(VoiceOutError):
    """The translator returned an unusable phrase."""
