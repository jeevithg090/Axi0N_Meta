"""The real `transformers.VitsModel` path, on a tiny randomly initialised model built offline.

Runs in the `ml-cpu` CI job (which installs the ml extra) and is skipped where torch is absent.
Nothing is downloaded: the model and tokenizer are saved to a temporary directory first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rulecam.voice_out.errors import SynthesisError, UnsupportedLanguageError
from rulecam.voice_out.tts import MmsTts, VitsLoader
from tests.voice_out.helpers import CASCO_ES, RecordingBus, wav_info

pytest.importorskip("torch")
transformers = pytest.importorskip("transformers")

VOCAB = list(" abcdefghijklmnopqrstuvwxyzáéíóúñ'")


def save_tiny_vits(directory: Path, *, is_uroman: bool = False) -> None:
    """Save a ~40 KB VITS checkpoint and tokenizer in the MMS layout."""
    vocab_file = directory.parent / f"{directory.name}-vocab.json"
    vocab_file.write_text(json.dumps({char: i for i, char in enumerate(VOCAB)}), encoding="utf-8")
    tokenizer = transformers.VitsTokenizer(
        str(vocab_file), add_blank=True, normalize=True, phonemize=False, is_uroman=is_uroman
    )
    config = transformers.VitsConfig(
        vocab_size=len(VOCAB),
        hidden_size=8,
        num_hidden_layers=1,
        num_attention_heads=2,
        ffn_dim=16,
        flow_size=8,
        spectrogram_bins=17,
        upsample_initial_channel=8,
        upsample_rates=[4, 4],
        upsample_kernel_sizes=[8, 8],
        resblock_kernel_sizes=[3],
        resblock_dilation_sizes=[[1]],
        prior_encoder_num_flows=1,
        prior_encoder_num_wavenet_layers=1,
        posterior_encoder_num_wavenet_layers=1,
        duration_predictor_num_flows=1,
        duration_predictor_filter_channels=8,
        depth_separable_num_layers=1,
        sampling_rate=16_000,
    )
    transformers.VitsModel(config).save_pretrained(directory)
    tokenizer.save_pretrained(directory)


@pytest.fixture
def model_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    save_tiny_vits(tmp_path / "mms-tts-spa")
    save_tiny_vits(tmp_path / "mms-tts-kor", is_uroman=True)
    return tmp_path


def make_tts(bus: RecordingBus, model_root: Path, seed: int = 0) -> MmsTts:
    loader = VitsLoader(model_id_template=str(model_root / "mms-tts-{code}"), seed=seed)
    return MmsTts(bus, loader=loader)


def test_vits_synthesis_produces_a_16k_wav(bus: RecordingBus, model_root: Path) -> None:
    audio = make_tts(bus, model_root).synth(CASCO_ES, "es")

    channels, width, rate, n_frames = wav_info(audio)
    assert (channels, width, rate) == (1, 2, 16_000)
    assert n_frames > 0
    assert bus.models_used()[0].model == "facebook/mms-tts-spa"
    assert bus.models_used()[0].meta is True


def test_vits_synthesis_is_reproducible(bus: RecordingBus, model_root: Path) -> None:
    tts = make_tts(bus, model_root)
    assert tts.synth(CASCO_ES, "es") == tts.synth(CASCO_ES, "es")


def test_uroman_voice_rejects_non_latin_text(bus: RecordingBus, model_root: Path) -> None:
    with pytest.raises(UnsupportedLanguageError, match="uroman"):
        make_tts(bus, model_root).synth("안전모를 쓰세요", "ko")


def test_missing_checkpoint_raises_synthesis_error(bus: RecordingBus, model_root: Path) -> None:
    with pytest.raises(SynthesisError, match="mms-tts-fra"):
        make_tts(bus, model_root).synth("Mettez votre casque", "fr")
