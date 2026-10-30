from __future__ import annotations

import numpy as np
import pytest

from rulecam.voice_out.errors import SynthesisError
from rulecam.voice_out.wav import TARGET_SAMPLE_RATE, decode_wav, encode_wav, resample, to_16k_wav
from tests.voice_out.helpers import pcm_wav, tone, wav_info


def test_encode_writes_16k_mono_16bit() -> None:
    audio = encode_wav(tone(0.5, 16_000), 16_000)
    assert wav_info(audio) == (1, 2, TARGET_SAMPLE_RATE, 8_000)


def test_encode_flattens_a_batch_axis() -> None:
    audio = encode_wav(tone(0.1, 16_000)[np.newaxis, :], 16_000)
    assert wav_info(audio)[3] == 1_600


@pytest.mark.parametrize("source_rate", [22_050, 24_000, 44_100, 8_000])
def test_encode_resamples_to_16k(source_rate: int) -> None:
    audio = encode_wav(tone(1.0, source_rate), source_rate)
    channels, width, rate, n_frames = wav_info(audio)
    assert (channels, width, rate) == (1, 2, TARGET_SAMPLE_RATE)
    assert abs(n_frames - TARGET_SAMPLE_RATE) <= 1


def test_resample_is_identity_at_target_rate() -> None:
    samples = tone(0.1, 16_000)
    assert resample(samples, 16_000) is samples


def test_encode_clips_out_of_range_samples() -> None:
    samples, _ = decode_wav(encode_wav(np.array([2.0, -2.0, 0.5], dtype=np.float32), 16_000))
    assert samples[0] == pytest.approx(1.0, abs=1e-3)
    assert samples[1] == pytest.approx(-1.0, abs=1e-3)
    assert samples[2] == pytest.approx(0.5, abs=1e-3)


def test_decode_round_trips_samples() -> None:
    original = tone(0.05, 16_000)
    decoded, rate = decode_wav(encode_wav(original, 16_000))
    assert rate == 16_000
    np.testing.assert_allclose(decoded, original, atol=1e-3)


def test_decode_mixes_stereo_to_mono() -> None:
    decoded, rate = decode_wav(pcm_wav(tone(0.1, 22_050), 22_050, channels=2))
    assert rate == 22_050
    assert decoded.ndim == 1
    assert decoded.shape[0] == 2_205


def test_to_16k_wav_converts_engine_output() -> None:
    audio = to_16k_wav(pcm_wav(tone(0.5, 22_050), 22_050, channels=2))
    assert wav_info(audio)[:3] == (1, 2, TARGET_SAMPLE_RATE)


def test_decode_rejects_non_wav_bytes() -> None:
    with pytest.raises(SynthesisError, match="not a readable WAV"):
        decode_wav(b"definitely not audio")


def test_decode_rejects_8bit_audio() -> None:
    with pytest.raises(SynthesisError, match="16-bit"):
        decode_wav(pcm_wav(tone(0.01, 8_000), 8_000, width=1))
