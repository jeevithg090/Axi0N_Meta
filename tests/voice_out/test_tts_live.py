"""Real MMS-TTS synthesis. Downloads `facebook/mms-tts-spa` (public, about 145 MB) on first run.

Run by hand before demo recording: `make live-test` or `uv run pytest -m live tests/voice_out`.
"""

from __future__ import annotations

import numpy as np
import pytest

from rulecam.voice_out.tts import MmsTts
from rulecam.voice_out.wav import decode_wav
from tests.voice_out.helpers import CASCO_ES, RecordingBus, wav_info

pytestmark = pytest.mark.live


def test_real_mms_speaks_spanish_hard_hat_warning(bus: RecordingBus) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("transformers")

    audio = MmsTts(bus).synth(CASCO_ES, "es")

    channels, width, rate, n_frames = wav_info(audio)
    assert (channels, width, rate) == (1, 2, 16_000)
    assert 0.8 <= n_frames / rate <= 6.0, "four words should take about one to three seconds"
    samples, _ = decode_wav(audio)
    assert float(np.sqrt(np.mean(samples**2))) > 0.01, "audio is near-silent"
    assert bus.models_used()[0].model == "facebook/mms-tts-spa"
    assert bus.models_used()[0].meta is True
