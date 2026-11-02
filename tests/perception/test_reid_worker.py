"""The SAM pod's person re-ID loader (add-ons 3.1 and 3.2); the model itself needs a GPU."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from rulecam.perception.reid_worker import (
    REID_MODEL,
    OsnetEmbedder,
    load_reid,
    weights_path,
    wire_vector,
)


def test_weights_live_next_to_the_hf_cache_unless_named() -> None:
    assert weights_path({"SAM_REID_WEIGHTS": "/w/osnet.pt"}) == Path("/w/osnet.pt")
    on_volume = weights_path({"HF_HOME": "/runpod-volume/hf-cache"})
    assert on_volume == Path(f"/runpod-volume/reid/{REID_MODEL}.pt")
    assert weights_path({}).name == f"{REID_MODEL}.pt"


def test_wire_vectors_are_rounded_lists() -> None:
    assert wire_vector(np.array([0.123456, -1.0], dtype=np.float32)) == [0.1235, -1.0]


def test_reid_is_off_on_request(tmp_path: Path) -> None:
    assert load_reid({"SAM_REID": "0", "SAM_REID_WEIGHTS": str(tmp_path / "w.pt")}) is None


def test_reid_needs_boxmot_and_the_weights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    weights = tmp_path / f"{REID_MODEL}.pt"
    env = {"SAM_REID_WEIGHTS": str(weights)}
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    assert load_reid(env) is None
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    assert load_reid(env) is None  # no weights file yet
    weights.write_bytes(b"weights")
    loaded = load_reid(env)
    assert isinstance(loaded, OsnetEmbedder)
    assert loaded.model_name == REID_MODEL
