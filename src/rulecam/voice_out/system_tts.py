"""Non-Meta fallback TTS through the operating system (macOS `say` or `espeak-ng`).

It is reported on the relay HUD with `meta=False`, so the demo never passes it off as Meta's.
The engine runs as an argument list (never a shell) and reads the phrase from a temporary file,
so phrase text can never be parsed as a command-line option.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal

from rulecam.bus import Bus
from rulecam.voice_out.errors import SynthesisError
from rulecam.voice_out.languages import primary_subtag
from rulecam.voice_out.relay import DEFAULT_SEGMENT, Timer, elapsed_ms, publish_model_used
from rulecam.voice_out.wav import TARGET_SAMPLE_RATE, to_16k_wav

Backend = Literal["say", "espeak-ng"]
CommandRunner = Callable[[Sequence[str]], None]
Which = Callable[[str], str | None]

BACKENDS: Final[tuple[Backend, ...]] = ("say", "espeak-ng")
COMMAND_TIMEOUT_S: Final = 30.0

SAY_VOICES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "de": "Anna",
        "en": "Samantha",
        "es": "Paulina",
        "fr": "Thomas",
        "hi": "Lekha",
        "pt": "Luciana",
        "vi": "Linh",
    }
)
"""Default macOS voice per language; `say` uses the system voice for languages not listed."""


def say_command(executable: str, text_file: Path, out_file: Path, voice: str | None) -> list[str]:
    """Build the macOS `say` argument list that writes a 16 kHz 16-bit WAV.

    Args:
        executable: Absolute path of `say`.
        text_file: UTF-8 file holding the phrase.
        out_file: WAV file to write.
        voice: Voice name, or None for the system voice.

    Returns:
        The argument list.
    """
    voice_args = ["-v", voice] if voice else []
    return [
        executable,
        *voice_args,
        "-o",
        str(out_file),
        "--file-format=WAVE",
        f"--data-format=LEI16@{TARGET_SAMPLE_RATE}",
        "-f",
        str(text_file),
    ]


def espeak_command(executable: str, text_file: Path, out_file: Path, lang: str) -> list[str]:
    """Build the `espeak-ng` argument list that writes a WAV.

    Args:
        executable: Absolute path of `espeak-ng`.
        text_file: UTF-8 file holding the phrase.
        out_file: WAV file to write.
        lang: Language tag; espeak-ng voices are named by ISO-639-1 code.

    Returns:
        The argument list.
    """
    return [executable, "-v", primary_subtag(lang), "-w", str(out_file), "-f", str(text_file)]


def run_command(argv: Sequence[str]) -> None:
    """Run a TTS command without a shell.

    Args:
        argv: Argument list whose first item is an absolute executable path.

    Raises:
        SynthesisError: If the command cannot start, fails or times out.
    """
    try:
        subprocess.run(  # noqa: S603 - resolved executable, fixed flags, text passed via file
            list(argv), check=True, capture_output=True, timeout=COMMAND_TIMEOUT_S
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        msg = f"system TTS command {Path(argv[0]).name!r} failed"
        raise SynthesisError(msg) from exc


class SystemTts:
    """`TTS` through macOS `say` or `espeak-ng`, labelled non-Meta on the relay HUD."""

    def __init__(
        self,
        bus: Bus,
        *,
        backend: Backend | None = None,
        voices: Mapping[str, str] = SAY_VOICES,
        runner: CommandRunner = run_command,
        which: Which = shutil.which,
        segment: str = DEFAULT_SEGMENT,
        timer: Timer = time.perf_counter,
    ) -> None:
        """Create the engine.

        Args:
            bus: Receives a `relay.model_used` payload (with `meta=False`) per synthesis.
            backend: Force one engine; by default `say`, then `espeak-ng`, whichever exists.
            voices: `say` voice per ISO-639-1 code.
            runner: Runs an argument list (injected in tests).
            which: Resolves an executable name to a path (injected in tests).
            segment: Relay HUD segment to report.
            timer: Monotonic clock for latency.
        """
        self._bus = bus
        self._backends: tuple[Backend, ...] = (backend,) if backend else BACKENDS
        self._voices = voices
        self._runner = runner
        self._which = which
        self._segment = segment
        self._timer = timer

    def synth(self, text: str, lang: str) -> bytes:
        """Speak `text` in `lang`.

        Args:
            text: The phrase, already in the target language.
            lang: Language tag.

        Returns:
            A 16 kHz mono 16-bit WAV.

        Raises:
            SynthesisError: If no engine is installed or the engine fails.
        """
        backend, executable = self._resolve()
        started = self._timer()
        with tempfile.TemporaryDirectory(prefix="rulecam-tts-") as tmp:
            text_file = Path(tmp) / "phrase.txt"
            out_file = Path(tmp) / "speech.wav"
            text_file.write_text(text, encoding="utf-8")
            self._runner(self._command(backend, executable, text_file, out_file, lang))
            if not out_file.is_file():
                msg = f"{backend} wrote no audio"
                raise SynthesisError(msg)
            audio = to_16k_wav(out_file.read_bytes())
        publish_model_used(
            self._bus,
            model=f"system-tts/{backend}",
            segment=self._segment,
            ms=elapsed_ms(started, self._timer),
            meta=False,
        )
        return audio

    def _resolve(self) -> tuple[Backend, str]:
        """Return the first available backend and its executable path."""
        for backend in self._backends:
            executable = self._which(backend)
            if executable:
                return backend, executable
        msg = f"no system TTS found; install one of: {', '.join(self._backends)}"
        raise SynthesisError(msg)

    def _command(
        self, backend: Backend, executable: str, text_file: Path, out_file: Path, lang: str
    ) -> list[str]:
        """Build the argument list for `backend`."""
        if backend == "say":
            voice = self._voices.get(primary_subtag(lang))
            return say_command(executable, text_file, out_file, voice)
        return espeak_command(executable, text_file, out_file, lang)
