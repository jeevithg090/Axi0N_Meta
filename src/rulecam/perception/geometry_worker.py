"""MoGe-2 metric geometry on the GPU pod, for ground calibration (add-on 3.5, `POST /geometry`).

Microsoft's MoGe-2 (`Ruicheng/moge-2-vitl-normal`, MIT, a DINOv2 ViT-L backbone) turns one image
into a metric point map in camera coordinates (OpenCV axes: x right, y down, z forward, metres)
and the camera's intrinsics, normalised by the image size. The API pod fits the floor plane to
the points under SAM's `floor` mask and turns plane plus intrinsics into a ground homography
(`calibration.calibrate_point_map`). That replaces the 1.7 m-person, 65 degree field of view and
15 degree tilt guesses of `calibrate_people`, which stays as the fallback and as a cross-check.

It runs once per camera (planner survey), in about half a second on an RTX 4090, beside SAM 3.1
(1.3 GB of weights, about 2-3 GB of VRAM while it runs). The model loads on the first call from
`MOGE_MODEL` (a Hugging Face repo id or a local `model.pt`; the default repo is pre-fetched into
the volume's Hugging Face cache by `scripts/sam_pod/install_moge.sh`), and `/health` says whether
the `moge` package is installed (`available`), so the API can default to MoGe only where it is.

The answer stays small: the point map is sampled on a grid (`grid` cells on the long side, at
most `MAX_GRID`), each cell the point at its centre pixel or `None` where MoGe's validity mask
is off (sky, far background). Like `sam_worker`, this module runs in the GPU venv: numpy and the
standard library at import time, torch, OpenCV and `moge` lazily.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from typing import Any, Final

import numpy as np

MOGE_MODEL_ENV: Final = "MOGE_MODEL"
DEFAULT_MOGE_MODEL: Final = "Ruicheng/moge-2-vitl-normal"
DEFAULT_GRID: Final = 96
MAX_GRID: Final = 256
MIN_GRID: Final = 8


def grid_shape(width: int, height: int, cells: int) -> tuple[int, int]:
    """`(rows, cols)` of a grid with `cells` along the image's long side, keeping its aspect."""
    if width >= height:
        return max(1, round(cells * height / width)), cells
    return cells, max(1, round(cells * width / height))


def sample_grid(
    points: np.ndarray, mask: np.ndarray, cells: int
) -> tuple[tuple[int, int], list[list[float] | None]]:
    """Sample a point map at the centre pixel of each grid cell.

    Args:
        points: `(H, W, 3)` metric points.
        mask: `(H, W)` validity (MoGe's mask); invalid or non-finite points become `None`.
        cells: Grid cells along the long side.

    Returns:
        `(rows, cols)` and the row-major cells, each `[x, y, z]` in metres (mm precision) or
        `None`.
    """
    height, width = points.shape[:2]
    rows, cols = grid_shape(width, height, cells)
    ys = ((np.arange(rows) + 0.5) * height / rows).astype(int).clip(0, height - 1)
    xs = ((np.arange(cols) + 0.5) * width / cols).astype(int).clip(0, width - 1)
    sampled = points[np.ix_(ys, xs)]
    valid = mask[np.ix_(ys, xs)].astype(bool) & np.isfinite(sampled).all(axis=-1)
    cells_out: list[list[float] | None] = [
        [round(float(v), 3) for v in sampled[r, c]] if valid[r, c] else None
        for r in range(rows)
        for c in range(cols)
    ]
    return (rows, cols), cells_out


def check_geometry_request(request: dict[str, Any]) -> int:
    """The request's grid size (`DEFAULT_GRID` when absent).

    Raises:
        ValueError: A grid that is not an integer from `MIN_GRID` to `MAX_GRID`.
    """
    grid = request.get("grid", DEFAULT_GRID)
    if not isinstance(grid, int) or isinstance(grid, bool) or not MIN_GRID <= grid <= MAX_GRID:
        msg = f"`grid` must be an integer from {MIN_GRID} to {MAX_GRID}"
        raise ValueError(msg)
    return grid


class MogeEngine:
    """MoGe-2 on the GPU, loaded on the first `estimate`; serves `POST /geometry`."""

    def __init__(self, model: str | None = None) -> None:
        """Create the engine; nothing is loaded until the first call.

        Args:
            model: Hugging Face repo id or local checkpoint (default `MOGE_MODEL`, else
                `DEFAULT_MOGE_MODEL`).
        """
        self._model_name = model or os.environ.get(MOGE_MODEL_ENV) or DEFAULT_MOGE_MODEL
        self._model: Any = None
        self._lock = threading.Lock()
        self._calls = 0

    @staticmethod
    def available() -> bool:
        """Whether the `moge` package is installed (the weights load on first use)."""
        return importlib.util.find_spec("moge") is not None

    def memory_stats(self) -> dict[str, Any]:
        """For `/health`: the model, whether it is installed and loaded, and the call count."""
        return {
            "model": self._model_name,
            "available": self.available(),
            "model_loaded": self._model is not None,
            "calls": self._calls,
        }

    def _load(self) -> Any:  # pragma: no cover - needs torch, moge and the weights
        import torch  # type: ignore[import-not-found]  # noqa: PLC0415 - GPU venv only
        from moge.model.v2 import MoGeModel  # type: ignore[import-not-found]  # noqa: PLC0415

        device = "cuda" if torch.cuda.is_available() else "cpu"
        return MoGeModel.from_pretrained(self._model_name).to(device).eval()

    def estimate(self, image: bytes, request: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
        """Run MoGe-2 on one frame and return the sampled point map and intrinsics.

        Args:
            image: JPEG or PNG bytes.
            request: `{"grid": int}` (optional).

        Returns:
            `{"model", "width", "height", "intrinsics" (3 x 3, normalised), "grid" [rows, cols],
            "points" (row-major cells), "gpu_seconds"}`.

        Raises:
            ValueError: A bad grid or an image that cannot be decoded.
        """
        import cv2  # noqa: PLC0415
        import torch  # noqa: PLC0415

        cells = check_geometry_request(request)
        started = time.perf_counter()
        bgr = cv2.imdecode(np.frombuffer(image, dtype=np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            msg = "image bytes could not be decoded"
            raise ValueError(msg)
        with self._lock:
            if self._model is None:
                self._model = self._load()
            model = self._model
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        device = next(model.parameters()).device
        with torch.inference_mode():
            tensor = torch.tensor(rgb / 255.0, dtype=torch.float32, device=device).permute(2, 0, 1)
            output = model.infer(tensor)
        points = output["points"].float().cpu().numpy()
        mask = output["mask"].cpu().numpy() if "mask" in output else np.isfinite(points).all(-1)
        intrinsics = output["intrinsics"].float().cpu().numpy()
        shape, cells_out = sample_grid(points, mask, cells)
        self._calls += 1
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return {
            "model": self._model_name,
            "width": int(bgr.shape[1]),
            "height": int(bgr.shape[0]),
            "intrinsics": [[float(v) for v in row] for row in intrinsics],
            "grid": list(shape),
            "points": cells_out,
            "gpu_seconds": time.perf_counter() - started,
        }
