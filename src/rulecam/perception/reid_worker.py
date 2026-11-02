"""Person re-ID vectors on the GPU pod: OSNet-AIN through BoxMOT (add-ons 3.1 and 3.2).

Runs inside the GPU venv next to SAM 3.1 (`sam_server`). The model is OSNet-AIN x1.0 trained on
MSMT17 (`osnet_ain_x1_0_msmt17`, 512-d vectors, about 1 ms per crop), built by BoxMOT's ReID
factory from a weights file on the pod's volume. BoxMOT and torch are imported lazily, so this
module loads anywhere; `scripts/sam_pod/setup.sh` installs BoxMOT without its numpy >= 2 pin
(SAM 3.1 needs numpy < 2, and the re-ID path runs on 1.26) and downloads the weights to
`weights_path` (`SAM_REID_WEIGHTS`, else `<HF_HOME's parent>/reid/osnet_ain_x1_0_msmt17.pt`).

Two uses:

- `/segment_image` embeds the boxes of the request's `embed_concepts` (people) in the images it
  already decoded (`embed_boxes`), so the API pod's BoT-SORT gets appearance with its boxes;
- `/embed` with `"model": "person"` embeds Ask's person crops (`embed`).

Vectors are L2-normalised, so a dot product is a cosine similarity. BoxMOT crops, resizes to
256 x 128 and normalises each box the way the weights were trained.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import numpy as np

REID_MODEL: Final = "osnet_ain_x1_0_msmt17"
REID_ENV: Final = "SAM_REID"
REID_WEIGHTS_ENV: Final = "SAM_REID_WEIGHTS"
EMBEDDING_DECIMALS: Final = 4

_log = logging.getLogger("rulecam.reid_worker")


def weights_path(env: Mapping[str, str]) -> Path:
    """Where the OSNet-AIN weights are read from.

    Args:
        env: Usually `os.environ`.

    Returns:
        `SAM_REID_WEIGHTS`, else `reid/osnet_ain_x1_0_msmt17.pt` next to `HF_HOME` (the volume's
        `hf-cache`), else under `~/.cache`.
    """
    explicit = env.get(REID_WEIGHTS_ENV)
    if explicit:
        return Path(explicit)
    hf_home = Path(env.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    return hf_home.parent / "reid" / f"{REID_MODEL}.pt"


def wire_vector(vector: np.ndarray) -> list[float]:
    """One vector as a rounded JSON list."""
    return [float(v) for v in np.round(np.asarray(vector, dtype=np.float64), EMBEDDING_DECIMALS)]


def load_reid(env: Mapping[str, str]) -> OsnetEmbedder | None:
    """The person re-ID model the server serves, or `None` (logged) when it cannot be served.

    Args:
        env: Usually `os.environ`; `SAM_REID=0` turns re-ID off.

    Returns:
        The embedder (loaded on first use), or `None` when re-ID is off, BoxMOT is not installed
        or the weights file is missing. `/segment_image` then returns no vectors, and the API pod's
        BoT-SORT falls back to boxes alone.
    """
    if env.get(REID_ENV, "1").strip().lower() in {"0", "false", "no"}:
        return None
    weights = weights_path(env)
    if importlib.util.find_spec("boxmot") is None:
        _log.warning("reid_unavailable reason=boxmot_not_installed")
        return None
    if not weights.is_file():
        _log.warning("reid_unavailable reason=no_weights path=%s", weights)
        return None
    return OsnetEmbedder(weights)


class OsnetEmbedder:
    """OSNet-AIN person re-ID vectors on the GPU, built on first use."""

    def __init__(self, weights: Path, *, device: str | None = None) -> None:
        """Create the embedder; nothing is loaded until the first call.

        Args:
            weights: The `osnet_ain_x1_0_msmt17.pt` file (its name selects the architecture).
            device: Torch device; CUDA when available, else CPU.
        """
        self._weights = weights
        self._device = device
        self._encoder: Any = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        """The model reported with every vector."""
        return REID_MODEL

    def _load(self) -> Any:  # pragma: no cover - needs torch, boxmot and the weights
        import torch  # type: ignore[import-not-found]  # noqa: PLC0415 - GPU venv only
        from boxmot.reid.factory import create_reid_encoder  # noqa: PLC0415
        from boxmot.reid.specs import ReIDConfig  # noqa: PLC0415

        device = self._device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        config = ReIDConfig(self._weights, device=device, allow_download=False)
        _log.info("reid_loaded model=%s device=%s", REID_MODEL, device)
        return create_reid_encoder(config)

    def _ready(self) -> Any:  # pragma: no cover - needs torch, boxmot and the weights
        with self._lock:
            if self._encoder is None:
                self._encoder = self._load()
            return self._encoder

    def embed_boxes(
        self, images: Sequence[np.ndarray], boxes: Sequence[np.ndarray]
    ) -> list[np.ndarray]:  # pragma: no cover - needs torch, boxmot and the weights
        """Embed the boxes of every image in one batch.

        Args:
            images: RGB `uint8` pixels `(H, W, 3)`, one per image.
            boxes: Pixel `xyxy` boxes `(N_i, 4)` per image (empty for none).

        Returns:
            Unit vectors `(N_i, 512)` per image.
        """
        import torch  # noqa: PLC0415 - GPU venv only
        from boxmot.structures import Boxes, Detections, Frame  # noqa: PLC0415

        frames, detections = [], []
        for index, (pixels, found) in enumerate(zip(images, boxes, strict=True)):
            sample = f"image-{index}"
            chw = torch.from_numpy(np.ascontiguousarray(pixels.transpose(2, 0, 1)))
            rows = np.asarray(found, dtype=np.float32).reshape(-1, 4)
            frames.append(Frame(image=chw, sample_id=sample))
            detections.append(
                Detections(
                    geometry=Boxes(torch.from_numpy(rows)),
                    scores=torch.ones(len(rows), dtype=torch.float32),
                    class_ids=torch.zeros(len(rows), dtype=torch.int64),
                    sample_id=sample,
                )
            )
        encoded = self._ready().encode(tuple(frames), tuple(detections))
        return [np.asarray(vectors.detach().cpu().numpy(), dtype=np.float32) for vectors in encoded]

    def embed(self, images: list[bytes]) -> dict[str, Any]:  # pragma: no cover - GPU
        """Embed encoded person crops (`/embed` with `"model": "person"`).

        Args:
            images: JPEG or PNG crops, one person each.

        Returns:
            `{"model": ..., "embeddings": [[float, ...], ...]}`, one unit vector per crop (a crop
            that cannot be decoded gets an empty list).
        """
        import cv2  # noqa: PLC0415 - opencv-python-headless is in both images

        decoded = [cv2.imdecode(np.frombuffer(d, dtype=np.uint8), cv2.IMREAD_COLOR) for d in images]
        kept = [np.ascontiguousarray(p[:, :, ::-1]) for p in decoded if p is not None]
        whole = [np.array([[0.0, 0.0, p.shape[1], p.shape[0]]], dtype=np.float32) for p in kept]
        vectors = iter(self.embed_boxes(kept, whole) if kept else [])
        return {
            "model": REID_MODEL,
            "embeddings": [wire_vector(next(vectors)[0]) if p is not None else [] for p in decoded],
        }
