"""Tracking metrics on labelled box tracks: HOTA, IDF1, ID switches (PLAN-9.5 §2 Perception).

Ground truth is MOTChallenge text (`frame,id,x,y,w,h,conf,class,visibility`, frames 1-based),
as CVAT and Label Studio export it. Predictions are the runtime's track samples mapped to clip
seconds. Only frames the runtime actually tracked are scored (the distinct frames of its
samples): chunks the runtime skipped (idle stride, motion gate) are coverage gaps, scored by the
alert metrics, not here.

IDF1, ID switches and MOTA come from py-motmetrics (MIT; the `tracking` extra and the dev
group). HOTA follows TrackEval's reference algorithm (Luiten et al. 2021; TrackEval is MIT):
per frame a Hungarian match on IoU weighted by the global alignment score, at every localisation
threshold alpha in 0.05..0.95, then HOTA = mean over alpha of sqrt(DetA * AssA).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import linear_sum_assignment  # type: ignore[import-untyped]

from rulecam.eval.errors import EvalError
from rulecam.eval.record import PredictedBox

ALPHAS = np.arange(0.05, 0.99, 0.05)
MATCH_IOU = 0.5  # motmetrics distance threshold (IoU >= 0.5 can match)
_EPS = np.finfo(float).eps
_MOT_MIN_FIELDS = 6
_MOT_CLASS_FIELD = 7

Box = tuple[float, float, float, float]  # x1, y1, x2, y2


@dataclass
class FrameBoxes:
    """The boxes of one frame for one concept: ids and xyxy boxes, aligned."""

    ids: list[str] = field(default_factory=list)
    boxes: list[Box] = field(default_factory=list)


@dataclass(frozen=True)
class TrackingScores:
    """Tracking quality for one concept on one clip."""

    concept: str
    frames: int
    gt_ids: int
    hota: float
    idf1: float
    id_switches: int
    mota: float
    minutes: float

    @property
    def id_switches_per_min(self) -> float | None:
        """ID switches per minute of the real time the scored frames span."""
        return self.id_switches / self.minutes if self.minutes > 0 else None


def parse_mot(lines: Iterable[str], classes: Mapping[int, str]) -> dict[str, dict[int, FrameBoxes]]:
    """Parse MOT ground truth into `concept -> frame -> boxes` (unknown classes are dropped).

    A row without a class column belongs to the only class, when there is exactly one.

    Raises:
        EvalError: On a malformed row.
    """
    out: dict[str, dict[int, FrameBoxes]] = defaultdict(dict)
    only = next(iter(classes.values())) if len(classes) == 1 else None
    for n, row in enumerate(csv.reader(lines), start=1):
        if not row or not "".join(row).strip():
            continue
        if len(row) < _MOT_MIN_FIELDS:
            msg = f"MOT line {n} has fewer than {_MOT_MIN_FIELDS} fields"
            raise EvalError(msg)
        try:
            frame, track = int(float(row[0])), str(int(float(row[1])))
            x, y, w, h = (float(v) for v in row[2:6])
            cls = int(float(row[_MOT_CLASS_FIELD])) if len(row) > _MOT_CLASS_FIELD else None
        except (ValueError, IndexError) as exc:
            msg = f"MOT line {n} is malformed: {row!r}"
            raise EvalError(msg) from exc
        concept = classes.get(cls) if cls is not None else only
        if concept is None:
            continue
        boxes = out[concept].setdefault(frame, FrameBoxes())
        boxes.ids.append(track)
        boxes.boxes.append((x, y, x + w, y + h))
    return dict(out)


def load_mot(path: Path, classes: Mapping[int, str]) -> dict[str, dict[int, FrameBoxes]]:
    """Read a MOT ground-truth file; see `parse_mot`."""
    return parse_mot(path.read_text(encoding="utf-8").splitlines(), classes)


def predicted_frames(
    predicted: Sequence[PredictedBox], *, fps: float, scale: tuple[float, float] = (1.0, 1.0)
) -> tuple[dict[str, dict[int, FrameBoxes]], set[int]]:
    """Map track samples to ground-truth frames (`round(clip_s * fps) + 1`), scaling boxes.

    Returns:
        `concept -> frame -> boxes`, and every frame that had any sample (the scored frames).
    """
    out: dict[str, dict[int, FrameBoxes]] = defaultdict(dict)
    frames: set[int] = set()
    sx, sy = scale
    for box in predicted:
        frame = round(box.clip_s * fps) + 1
        frames.add(frame)
        x1, y1, x2, y2 = box.box_xyxy
        boxes = out[box.concept].setdefault(frame, FrameBoxes())
        boxes.ids.append(box.track)
        boxes.boxes.append((x1 * sx, y1 * sy, x2 * sx, y2 * sy))
    return dict(out), frames


def iou_matrix(gt: Sequence[Box], pred: Sequence[Box]) -> NDArray[np.float64]:
    """Pairwise IoU of xyxy boxes, shape `(len(gt), len(pred))`."""
    if not gt or not pred:
        return np.zeros((len(gt), len(pred)))
    a = np.asarray(gt, dtype=float)[:, None, :]
    b = np.asarray(pred, dtype=float)[None, :, :]
    iw = np.clip(np.minimum(a[..., 2], b[..., 2]) - np.maximum(a[..., 0], b[..., 0]), 0, None)
    ih = np.clip(np.minimum(a[..., 3], b[..., 3]) - np.maximum(a[..., 1], b[..., 1]), 0, None)
    inter = iw * ih
    area_a = (a[..., 2] - a[..., 0]) * (a[..., 3] - a[..., 1])
    area_b = (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])
    union = area_a + area_b - inter
    return np.where(union > 0, inter / np.maximum(union, _EPS), 0.0)


def _index(ids: Iterable[str]) -> dict[str, int]:
    return {name: i for i, name in enumerate(sorted(set(ids)))}


def hota(
    gt: Mapping[int, FrameBoxes], pred: Mapping[int, FrameBoxes], frames: Iterable[int]
) -> float:
    """HOTA over `frames`, averaged over the localisation thresholds `ALPHAS` (TrackEval)."""
    frames = sorted(frames)
    empty = FrameBoxes()
    gt_index = _index(i for f in frames for i in gt.get(f, empty).ids)
    pr_index = _index(i for f in frames for i in pred.get(f, empty).ids)
    if not gt_index or not pr_index:
        return 0.0
    per_frame = []
    potential = np.zeros((len(gt_index), len(pr_index)))
    gt_count = np.zeros((len(gt_index), 1))
    pr_count = np.zeros((1, len(pr_index)))
    for f in frames:
        g, p = gt.get(f, empty), pred.get(f, empty)
        gi = np.array([gt_index[i] for i in g.ids], dtype=int)
        pi = np.array([pr_index[i] for i in p.ids], dtype=int)
        sim = iou_matrix(g.boxes, p.boxes)
        per_frame.append((gi, pi, sim))
        gt_count[gi, 0] += 1
        pr_count[0, pi] += 1
        if gi.size and pi.size:
            denom = sim.sum(0)[None, :] + sim.sum(1)[:, None] - sim
            ratio = np.where(denom > _EPS, sim / np.maximum(denom, _EPS), 0.0)
            potential[gi[:, None], pi[None, :]] += ratio
    alignment = potential / np.maximum(gt_count + pr_count - potential, _EPS)
    tp, fn, fp = np.zeros(len(ALPHAS)), np.zeros(len(ALPHAS)), np.zeros(len(ALPHAS))
    matches = np.zeros((len(ALPHAS), len(gt_index), len(pr_index)))
    for gi, pi, sim in per_frame:
        if not gi.size or not pi.size:
            fn += gi.size
            fp += pi.size
            continue
        rows, cols = linear_sum_assignment(-(alignment[gi[:, None], pi[None, :]] * sim))
        for a, alpha in enumerate(ALPHAS):
            ok = sim[rows, cols] >= alpha - _EPS
            n = int(ok.sum())
            tp[a] += n
            fn[a] += gi.size - n
            fp[a] += pi.size - n
            matches[a, gi[rows[ok]], pi[cols[ok]]] += 1
    scores = []
    for a in range(len(ALPHAS)):
        ass = matches[a] / np.maximum(gt_count + pr_count - matches[a], 1)
        ass_a = float((matches[a] * ass).sum() / max(1.0, tp[a]))
        det_a = float(tp[a] / max(1.0, tp[a] + fn[a] + fp[a]))
        scores.append(np.sqrt(det_a * ass_a))
    return float(np.mean(scores))


def clear_and_id(
    gt: Mapping[int, FrameBoxes], pred: Mapping[int, FrameBoxes], frames: Iterable[int]
) -> tuple[float, int, float]:
    """Return (IDF1, ID switches, MOTA) from py-motmetrics over `frames`.

    Raises:
        EvalError: If py-motmetrics is not installed (`uv sync --extra tracking`).
    """
    try:
        import motmetrics as mm  # noqa: PLC0415 - optional dependency
    except ImportError as exc:  # pragma: no cover - the dev group installs it
        msg = "tracking metrics need py-motmetrics: uv sync --extra tracking"
        raise EvalError(msg) from exc
    acc = mm.MOTAccumulator(auto_id=True)
    empty = FrameBoxes()
    frames = sorted(frames)
    gt_index = _index(i for f in frames for i in gt.get(f, empty).ids)  # motmetrics wants ints
    pr_index = _index(i for f in frames for i in pred.get(f, empty).ids)
    for f in frames:
        g, p = gt.get(f, empty), pred.get(f, empty)
        dist = 1.0 - iou_matrix(g.boxes, p.boxes)
        dist[dist > 1.0 - MATCH_IOU] = np.nan
        acc.update([gt_index[i] for i in g.ids], [pr_index[i] for i in p.ids], dist)
    summary = mm.metrics.create().compute(
        acc, metrics=["idf1", "num_switches", "mota"], name="clip"
    )
    row = summary.loc["clip"]
    idf1 = float(row["idf1"])
    return (0.0 if np.isnan(idf1) else idf1), int(row["num_switches"]), float(row["mota"])


def scored_minutes(frames: Iterable[int], *, fps: float) -> float:
    """Real time the scored frames span, in minutes, whatever rate the runtime tracked at.

    Counting frames (`len(frames) / fps`) understates time when the runtime samples keyframes
    (2-4 per second against 30 fps ground truth), which overstates ID switches per minute
    10x. The span runs from the first to the last scored frame plus one typical sampling step
    (the median gap), so the last sample's interval counts too; one frame counts `1 / fps`.

    Args:
        frames: The scored ground-truth frame numbers.
        fps: The ground truth's frame rate.

    Returns:
        Minutes (0 when nothing was scored).
    """
    ordered = sorted(set(frames))
    if not ordered:
        return 0.0
    gaps = np.diff(ordered)
    step = float(np.median(gaps)) if gaps.size else 1.0
    return (ordered[-1] - ordered[0] + step) / fps / 60.0


def score_tracking(
    gt: Mapping[str, Mapping[int, FrameBoxes]],
    predicted: Sequence[PredictedBox],
    *,
    fps: float,
    scale: tuple[float, float] = (1.0, 1.0),
) -> list[TrackingScores]:
    """Score every labelled concept on the frames the runtime tracked."""
    pred, frames = predicted_frames(predicted, fps=fps, scale=scale)
    minutes = scored_minutes(frames, fps=fps)
    out = []
    for concept in sorted(gt):
        g, p = gt[concept], pred.get(concept, {})
        idf1, switches, mota = clear_and_id(g, p, frames)
        out.append(
            TrackingScores(
                concept=concept,
                frames=len(frames),
                gt_ids=len({i for f in frames for i in g.get(f, FrameBoxes()).ids}),
                hota=hota(g, p, frames),
                idf1=idf1,
                id_switches=switches,
                mota=mota,
                minutes=minutes,
            )
        )
    return out
