from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from rulecam.contracts import TTS
from rulecam.voice_out.errors import SynthesisError
from rulecam.voice_out.relay import ModelUsed
from rulecam.voice_out.system_tts import (
    Backend,
    SystemTts,
    espeak_command,
    run_command,
    say_command,
)
from tests.voice_out.helpers import CASCO_ES, RecordingBus, StepTimer, pcm_wav, tone, wav_info

SAY = "/usr/bin/say"
ESPEAK = "/usr/bin/espeak-ng"


class FakeRunner:
    """Pretends to be the TTS engine: records argv and writes a 22.05 kHz WAV to the output."""

    def __init__(self, *, write: bool = True) -> None:
        self.write = write
        self.calls: list[list[str]] = []
        self.texts: list[str] = []

    def __call__(self, argv: Sequence[str]) -> None:
        args = list(argv)
        self.calls.append(args)
        self.texts.append(Path(args[args.index("-f") + 1]).read_text(encoding="utf-8"))
        out_flag = "-o" if "-o" in args else "-w"
        if self.write:
            Path(args[args.index(out_flag) + 1]).write_bytes(pcm_wav(tone(0.5, 22_050), 22_050))


def make_tts(
    bus: RecordingBus,
    runner: FakeRunner,
    available: dict[str, str],
    *,
    backend: Backend | None = None,
    segment: str = "voice_out",
    timer: StepTimer | None = None,
) -> SystemTts:
    return SystemTts(
        bus,
        backend=backend,
        runner=runner,
        which=available.get,
        segment=segment,
        timer=timer or StepTimer(),
    )


def test_satisfies_the_tts_protocol(bus: RecordingBus) -> None:
    tts: TTS = make_tts(bus, FakeRunner(), {"say": SAY})
    assert tts.synth("hello", "en")


def test_fallback_label_is_non_meta(bus: RecordingBus) -> None:
    tts = make_tts(bus, FakeRunner(), {"say": SAY}, segment="uc1", timer=StepTimer(step=0.5))
    tts.synth(CASCO_ES, "es")
    assert bus.models_used() == [
        ModelUsed(model="system-tts/say", segment="uc1", ms=500, meta=False)
    ]
    assert all(not used.meta for used in bus.models_used())


def test_output_is_converted_to_16k(bus: RecordingBus) -> None:
    audio = make_tts(bus, FakeRunner(), {"say": SAY}).synth(CASCO_ES, "es")
    channels, width, rate, n_frames = wav_info(audio)
    assert (channels, width, rate) == (1, 2, 16_000)
    assert abs(n_frames - 8_000) <= 1


def test_say_gets_the_language_voice_and_reads_text_from_a_file(bus: RecordingBus) -> None:
    runner = FakeRunner()
    make_tts(bus, runner, {"say": SAY}).synth(CASCO_ES, "es-MX")

    argv = runner.calls[0]
    assert argv[0] == SAY
    assert argv[argv.index("-v") + 1] == "Paulina"
    assert CASCO_ES not in argv
    assert runner.texts == [CASCO_ES]


def test_text_that_looks_like_an_option_is_never_an_argument(bus: RecordingBus) -> None:
    runner = FakeRunner()
    make_tts(bus, runner, {"say": SAY}).synth("--help -o /etc/passwd", "en")
    assert all("passwd" not in arg for arg in runner.calls[0])


def test_say_uses_system_voice_for_unmapped_language(bus: RecordingBus) -> None:
    runner = FakeRunner()
    make_tts(bus, runner, {"say": SAY}).synth("Vaa kofia ngumu", "sw")
    assert "-v" not in runner.calls[0]


def test_espeak_is_used_when_say_is_missing(bus: RecordingBus) -> None:
    runner = FakeRunner()
    make_tts(bus, runner, {"espeak-ng": ESPEAK}).synth(CASCO_ES, "es-MX")

    argv = runner.calls[0]
    assert argv[0] == ESPEAK
    assert argv[argv.index("-v") + 1] == "es"
    assert bus.models_used()[0].model == "system-tts/espeak-ng"
    assert bus.models_used()[0].meta is False


def test_forced_backend_is_respected(bus: RecordingBus) -> None:
    runner = FakeRunner()
    make_tts(bus, runner, {"say": SAY, "espeak-ng": ESPEAK}, backend="espeak-ng").synth("hi", "en")
    assert runner.calls[0][0] == ESPEAK


def test_no_engine_installed_raises(bus: RecordingBus) -> None:
    with pytest.raises(SynthesisError, match="no system TTS"):
        make_tts(bus, FakeRunner(), {}).synth("hi", "en")
    assert bus.published == []


def test_engine_that_writes_nothing_raises(bus: RecordingBus) -> None:
    with pytest.raises(SynthesisError, match="wrote no audio"):
        make_tts(bus, FakeRunner(write=False), {"say": SAY}).synth("hi", "en")


def test_say_command_layout(tmp_path: Path) -> None:
    text, out = tmp_path / "t.txt", tmp_path / "o.wav"
    assert say_command(SAY, text, out, "Paulina") == [
        SAY,
        "-v",
        "Paulina",
        "-o",
        str(out),
        "--file-format=WAVE",
        "--data-format=LEI16@16000",
        "-f",
        str(text),
    ]
    assert "-v" not in say_command(SAY, text, out, None)


def test_espeak_command_layout(tmp_path: Path) -> None:
    text, out = tmp_path / "t.txt", tmp_path / "o.wav"
    assert espeak_command(ESPEAK, text, out, "pt-BR") == [
        ESPEAK,
        "-v",
        "pt",
        "-w",
        str(out),
        "-f",
        str(text),
    ]


def test_run_command_reports_a_failing_engine() -> None:
    with pytest.raises(SynthesisError, match="failed"):
        run_command([sys.executable, "-c", "raise SystemExit(3)"])


def test_run_command_reports_a_missing_engine(tmp_path: Path) -> None:
    with pytest.raises(SynthesisError, match="failed"):
        run_command([str(tmp_path / "no-such-engine")])


def test_run_command_succeeds_quietly() -> None:
    run_command([sys.executable, "-c", "print('spoken')"])
