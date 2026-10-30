"""Meta Voice Out speech synthesis: MMS-TTS first, a labelled non-Meta engine as fallback.

`torch` and `transformers` (the `ml` extra) are imported only when an MMS model is first
loaded, so this module imports and type-checks without them.
"""

from __future__ import annotations

import importlib
import importlib.util
import time
from collections.abc import Callable
from types import ModuleType
from typing import Any, Final

import numpy as np

from rulecam.bus import Bus
from rulecam.contracts import TTS
from rulecam.log import get_logger
from rulecam.voice_out.errors import SynthesisError, UnsupportedLanguageError, VoiceOutError
from rulecam.voice_out.languages import mms_code, mms_model_id
from rulecam.voice_out.relay import DEFAULT_SEGMENT, Timer, elapsed_ms, publish_model_used
from rulecam.voice_out.wav import Samples, encode_wav

_log = get_logger(__name__)

Synthesizer = Callable[[str], tuple[Samples, int]]
"""Turns text into `(mono float samples, sample_rate)` for one language."""

SynthesizerLoader = Callable[[str], Synthesizer]
"""Builds the `Synthesizer` for one MMS language code, e.g. `"spa"`."""

MMS_MODEL_ID_TEMPLATE: Final = "facebook/mms-tts-{code}"


def _import_ml(module: str) -> ModuleType:
    """Import an `ml`-extra module, explaining how to install it when missing."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        msg = f"MMS-TTS needs `{module}`; install the ml extra: uv sync --extra ml"
        raise SynthesisError(msg) from exc


def needs_romanisation(text: str, *, is_uroman: bool) -> bool:
    """Return whether a uroman tokenizer would silently drop characters of `text`.

    MMS checkpoints flagged `is_uroman` (e.g. Korean, Amharic) only read Latin script and need the
    optional `uroman` package for anything else.

    Args:
        text: The phrase to speak.
        is_uroman: The tokenizer's `is_uroman` flag.

    Returns:
        True if the text cannot be spoken correctly in this environment.
    """
    if not is_uroman or text.isascii():
        return False
    return importlib.util.find_spec("uroman") is None


class _VitsSynthesizer:
    """A loaded MMS VITS model and its tokenizer."""

    def __init__(self, torch: ModuleType, model: Any, tokenizer: Any, seed: int) -> None:
        self._torch = torch
        self._model = model
        self._tokenizer = tokenizer
        self._seed = seed

    def __call__(self, text: str) -> tuple[Samples, int]:
        """Synthesise `text`; the seed makes VITS's sampled durations reproducible."""
        if needs_romanisation(text, is_uroman=bool(self._tokenizer.is_uroman)):
            msg = "this MMS voice needs the `uroman` romaniser, which is not installed"
            raise UnsupportedLanguageError(msg)
        inputs = self._tokenizer(text, return_tensors="pt")
        with self._torch.random.fork_rng(devices=[]), self._torch.no_grad():
            self._torch.manual_seed(self._seed)
            waveform = self._model(**inputs).waveform
        samples = np.asarray(waveform.squeeze().cpu().numpy(), dtype=np.float32)
        return samples, int(self._model.config.sampling_rate)


class VitsLoader:
    """Loads `transformers.VitsModel` MMS-TTS checkpoints from the Hugging Face Hub."""

    def __init__(self, *, model_id_template: str = MMS_MODEL_ID_TEMPLATE, seed: int = 0) -> None:
        """Create a loader.

        Args:
            model_id_template: Hub id or local path with a `{code}` placeholder.
            seed: Torch seed applied to every synthesis, for reproducible audio.
        """
        self._template = model_id_template
        self._seed = seed

    def __call__(self, code: str) -> Synthesizer:
        """Load the checkpoint for one MMS language code.

        Args:
            code: MMS (ISO-639-3) code, e.g. `"spa"`.

        Returns:
            A synthesizer for that language.

        Raises:
            SynthesisError: If the ml extra is missing or the checkpoint cannot be loaded.
        """
        torch = _import_ml("torch")
        transformers = _import_ml("transformers")
        model_id = self._template.format(code=code)
        try:
            model = transformers.VitsModel.from_pretrained(model_id)
            tokenizer = transformers.AutoTokenizer.from_pretrained(model_id)
        except OSError as exc:
            msg = f"could not load MMS-TTS checkpoint {model_id!r}"
            raise SynthesisError(msg) from exc
        model.eval()
        return _VitsSynthesizer(torch, model, tokenizer, self._seed)


class MmsTts:
    """`TTS` backed by Meta MMS-TTS (`facebook/mms-tts-<iso3>`, CC-BY-NC 4.0), on CPU.

    Each language's model is loaded on first use and kept for the life of the object.
    """

    def __init__(
        self,
        bus: Bus,
        *,
        loader: SynthesizerLoader | None = None,
        segment: str = DEFAULT_SEGMENT,
        timer: Timer = time.perf_counter,
    ) -> None:
        """Create the engine; no model is loaded yet.

        Args:
            bus: Receives a `relay.model_used` payload per synthesis.
            loader: Builds a synthesizer per MMS code; defaults to `VitsLoader()`.
            segment: Relay HUD segment to report.
            timer: Monotonic clock for latency.
        """
        self._bus = bus
        self._loader = loader if loader is not None else VitsLoader()
        self._segment = segment
        self._timer = timer
        self._synthesizers: dict[str, Synthesizer] = {}

    def synth(self, text: str, lang: str) -> bytes:
        """Speak `text` in `lang`.

        Args:
            text: The phrase, already in the target language.
            lang: ISO-639-1 tag (a region suffix is ignored) or MMS code.

        Returns:
            A 16 kHz mono 16-bit WAV.

        Raises:
            UnsupportedLanguageError: If MMS-TTS cannot speak the language.
            SynthesisError: If the model cannot be loaded.
        """
        code = mms_code(lang)
        synthesizer = self._synthesizer(code)
        started = self._timer()
        samples, sample_rate = synthesizer(text)
        audio = encode_wav(samples, sample_rate)
        publish_model_used(
            self._bus,
            model=mms_model_id(code),
            segment=self._segment,
            ms=elapsed_ms(started, self._timer),
            meta=True,
        )
        return audio

    def _synthesizer(self, code: str) -> Synthesizer:
        """Return the cached synthesizer for `code`, loading it on first use."""
        if code not in self._synthesizers:
            _log.info("mms_tts_load", code=code)
            self._synthesizers[code] = self._loader(code)
        return self._synthesizers[code]


class FallbackTts:
    """Tries a primary engine and, on any `VoiceOutError`, speaks with the fallback instead."""

    def __init__(self, primary: TTS, fallback: TTS) -> None:
        """Chain two engines.

        Args:
            primary: Usually `MmsTts`.
            fallback: Usually `SystemTts`, which reports itself as non-Meta.
        """
        self._primary = primary
        self._fallback = fallback

    def synth(self, text: str, lang: str) -> bytes:
        """Speak `text` in `lang` with the first engine that can.

        Args:
            text: The phrase, already in the target language.
            lang: Language tag.

        Returns:
            WAV bytes from whichever engine succeeded.

        Raises:
            VoiceOutError: If the fallback fails too.
        """
        try:
            return self._primary.synth(text, lang)
        except VoiceOutError as exc:
            _log.warning("tts_fallback", lang=lang, error=str(exc))
            return self._fallback.synth(text, lang)
