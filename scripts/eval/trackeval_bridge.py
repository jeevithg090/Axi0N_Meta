"""Run TrackEval (the MOTChallenge reference scorer) on folders `rulecam.eval.motchallenge` wrote.

TrackEval (MIT, github.com/JonathonLuiten/TrackEval) is not on PyPI and is not a project
dependency. Scripts that need it run with it added for that one call:

    uv run --with git+https://github.com/JonathonLuiten/TrackEval python scripts/eval/<script>.py

Its code still uses the `np.float` / `np.int` / `np.bool` aliases NumPy 1.24 removed, so they are
restored (as the builtins they always were) before it is imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from rulecam.eval.motchallenge import TRACKER_NAME

INSTALL_HINT = (
    "TrackEval is not installed; run with: "
    "uv run --with git+https://github.com/JonathonLuiten/TrackEval python <script>"
)


@dataclass(frozen=True)
class ReferenceScores:
    """TrackEval's scores for one sequence."""

    hota: float
    idf1: float
    mota: float
    id_switches: int


def _import_trackeval() -> Any:
    for name, alias in (("float", float), ("int", int), ("bool", bool)):
        if not hasattr(np, name):
            setattr(np, name, alias)
    try:
        import trackeval  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(INSTALL_HINT) from exc
    return trackeval


def evaluate(
    root: Path, lengths: dict[str, int], *, tracker: str = TRACKER_NAME
) -> dict[str, ReferenceScores]:
    """Score every sequence under `root` with TrackEval's HOTA, CLEAR and Identity metrics.

    Args:
        root: The folder `motchallenge.write_layout` wrote into.
        lengths: Sequence name -> number of frames.
        tracker: The tracker folder name.

    Returns:
        Sequence name -> scores.
    """
    trackeval = _import_trackeval()
    eval_config = trackeval.Evaluator.get_default_eval_config()
    eval_config.update(
        {
            "USE_PARALLEL": False,
            "PRINT_RESULTS": False,
            "PRINT_CONFIG": False,
            "TIME_PROGRESS": False,
            "OUTPUT_SUMMARY": False,
            "OUTPUT_DETAILED": False,
            "PLOT_CURVES": False,
            "DISPLAY_LESS_PROGRESS": True,
        }
    )
    dataset_config = trackeval.datasets.MotChallenge2DBox.get_default_dataset_config()
    dataset_config.update(
        {
            "GT_FOLDER": str(root / "gt"),
            "TRACKERS_FOLDER": str(root / "trackers"),
            "OUTPUT_FOLDER": str(root / "out"),
            "TRACKERS_TO_EVAL": [tracker],
            "CLASSES_TO_EVAL": ["pedestrian"],
            "BENCHMARK": "MOT17",
            "SPLIT_TO_EVAL": "train",
            "SKIP_SPLIT_FOL": True,
            "SEQ_INFO": dict(lengths),
            "PRINT_CONFIG": False,
        }
    )
    quiet = {"PRINT_CONFIG": False}
    metrics = [
        trackeval.metrics.HOTA(quiet),
        trackeval.metrics.CLEAR(quiet),
        trackeval.metrics.Identity(quiet),
    ]
    evaluator = trackeval.Evaluator(eval_config)
    results, _ = evaluator.evaluate([trackeval.datasets.MotChallenge2DBox(dataset_config)], metrics)
    per_seq = results["MotChallenge2DBox"][tracker]
    out = {}
    for seq in lengths:
        found = per_seq[seq]["pedestrian"]
        out[seq] = ReferenceScores(
            hota=float(np.mean(found["HOTA"]["HOTA"])),
            idf1=float(found["Identity"]["IDF1"]),
            mota=float(found["CLEAR"]["MOTA"]),
            id_switches=int(found["CLEAR"]["IDSW"]),
        )
    return out
