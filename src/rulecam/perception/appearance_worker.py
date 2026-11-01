"""DINOv2 appearance embeddings on the GPU pod, for re-identifying track fragments (`/embed`).

Runs inside the GPU venv next to SAM 3.1 (`sam_server`): torch, timm and OpenCV are imported
lazily, so the module loads anywhere. The model is Meta's DINOv2 ViT-S/14 (`timm` name
`vit_small_patch14_dinov2.lvd142m`, Apache-2.0), loaded once on first use from the local
Hugging Face cache (`scripts/sam_pod/setup.sh` fetches it). Each image (a crop of one tracked
object) is resized to `CROP_SIZE` keeping nothing else, normalised, and embedded; the answer is
the L2-normalised CLS embedding per image, so a dot product is a cosine similarity.
"""

from __future__ import annotations

import threading
from typing import Any, Final

MODEL_NAME: Final = "vit_small_patch14_dinov2.lvd142m"
CROP_SIZE: Final = (224, 112)  # height, width: a standing person's proportions, 14 px patches
_MEAN: Final = (0.485, 0.456, 0.406)
_STD: Final = (0.229, 0.224, 0.225)


class DinoEmbedder:
    """DINOv2 crop embeddings on the GPU, loaded on first call."""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        """Create the embedder; nothing is loaded until the first `embed`."""
        self._model_name = model_name
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:  # pragma: no cover - needs torch and the weights
        import timm  # type: ignore[import-not-found]  # noqa: PLC0415 - GPU venv only
        import torch  # type: ignore[import-not-found]  # noqa: PLC0415

        model = timm.create_model(
            self._model_name, pretrained=True, num_classes=0, dynamic_img_size=True
        )
        device = "cuda" if torch.cuda.is_available() else "cpu"
        return model.eval().to(device)

    def embed(self, images: list[bytes]) -> dict[str, Any]:  # pragma: no cover - GPU
        """Embed JPEG/PNG crops.

        Args:
            images: Encoded crops, one object each.

        Returns:
            `{"model": ..., "embeddings": [[float, ...], ...]}`, one unit vector per image
            (an image that cannot be decoded gets an empty list).
        """
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415
        import torch  # noqa: PLC0415 - the stub-less import above already reported it

        with self._lock:
            if self._model is None:
                self._model = self._load()
            model = self._model
        device = next(model.parameters()).device
        batch, ok = [], []
        for data in images:
            image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            ok.append(image is not None)
            if image is None:
                continue
            rgb = cv2.cvtColor(cv2.resize(image, CROP_SIZE[::-1]), cv2.COLOR_BGR2RGB)
            tensor = (rgb.astype(np.float32) / 255.0 - _MEAN) / _STD
            batch.append(tensor.transpose(2, 0, 1))
        vectors: list[list[float]] = []
        if batch:
            with torch.inference_mode():
                pixels = torch.from_numpy(np.stack(batch).astype(np.float32)).to(device)
                features = torch.nn.functional.normalize(model(pixels), dim=-1)
            vectors = features.float().cpu().numpy().round(5).tolist()
        found = iter(vectors)
        return {"model": self._model_name, "embeddings": [next(found) if k else [] for k in ok]}
