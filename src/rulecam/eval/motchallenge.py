"""MOTChallenge folders for TrackEval, from our ground truth and track samples (add-on 1.5).

`rulecam.eval.tracking` re-implements HOTA and uses py-motmetrics for IDF1 and MOTA. To check
that against the reference, `scripts/eval/trackeval_crosscheck.py` and `scripts/eval/mot17.py`
write the same data in the layout TrackEval's `MotChallenge2DBox` reads (with
`SKIP_SPLIT_FOL`):

    <root>/gt/<seq>/gt/gt.txt            frame,id,x,y,w,h,1,1,1
    <root>/gt/<seq>/seqinfo.ini          name, frameRate, seqLength, imWidth, imHeight
    <root>/trackers/<tracker>/data/<seq>.txt   frame,id,x,y,w,h,conf,-1,-1,-1

Our scorer only scores the frames the runtime tracked (its keyframes), so the ground truth
written here is restricted to those frames too; otherwise TrackEval would count every
untracked frame's people as misses and the two would not be comparing the same thing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from rulecam.eval.record import PredictedBox

TRACKER_NAME = "rulecam"


@dataclass(frozen=True)
class SeqInfo:
    """A sequence's `seqinfo.ini` fields."""

    name: str
    fps: float
    length: int
    width: int
    height: int

    def ini(self) -> str:
        """The `seqinfo.ini` text."""
        return (
            "[Sequence]\n"
            f"name={self.name}\nimDir=img1\nframeRate={self.fps:g}\nseqLength={self.length}\n"
            f"imWidth={self.width}\nimHeight={self.height}\nimExt=.jpg\n"
        )


def tracker_rows(
    boxes: Sequence[PredictedBox],
    *,
    fps: float,
    scale: tuple[float, float] = (1.0, 1.0),
    concept: str | None = None,
) -> tuple[list[str], set[int]]:
    """MOTChallenge tracker rows for track samples, one per frame and id.

    Samples land on frame `round(clip_s * fps) + 1` (as `tracking.predicted_frames` maps them);
    a second sample of the same id on the same frame is dropped, because TrackEval refuses an id
    twice in one frame. String ids become integers in order of first appearance.

    Args:
        boxes: The samples.
        fps: The ground truth's frame rate.
        scale: Factor from sample pixels to ground-truth pixels.
        concept: Keep only this concept (`None` keeps all).

    Returns:
        The frame-sorted rows, and every frame that had a sample (of any concept).
    """
    ids: dict[str, int] = {}
    seen: set[tuple[int, int]] = set()
    frames: set[int] = set()
    rows: list[tuple[int, int, str]] = []
    sx, sy = scale
    for box in sorted(boxes, key=lambda b: b.clip_s):
        frame = round(box.clip_s * fps) + 1
        frames.add(frame)
        if concept is not None and box.concept != concept:
            continue
        number = ids.setdefault(box.track, len(ids) + 1)
        if (frame, number) in seen:
            continue
        seen.add((frame, number))
        x1, y1, x2, y2 = box.box_xyxy
        rows.append(
            (
                frame,
                number,
                f"{frame},{number},{x1 * sx:.2f},{y1 * sy:.2f},{(x2 - x1) * sx:.2f},"
                f"{(y2 - y1) * sy:.2f},1,-1,-1,-1",
            )
        )
    return [text for _, _, text in sorted(rows)], frames


def restrict_gt(rows: Iterable[str], frames: set[int]) -> list[str]:
    """Keep the ground-truth rows on `frames` (the frames the runtime tracked)."""
    out = []
    for row in rows:
        head = row.split(",", 1)[0].strip()
        if head and int(float(head)) in frames:
            out.append(row)
    return out


def write_layout(
    root: Path,
    seq: SeqInfo,
    gt_rows: Sequence[str],
    tracker: Sequence[str],
    *,
    tracker_name: str = TRACKER_NAME,
) -> Path:
    """Write one sequence's ground truth and tracker output for TrackEval.

    Returns:
        The tracker data file written.
    """
    seq_dir = root / "gt" / seq.name
    (seq_dir / "gt").mkdir(parents=True, exist_ok=True)
    (seq_dir / "gt" / "gt.txt").write_text("".join(r + "\n" for r in gt_rows), encoding="utf-8")
    (seq_dir / "seqinfo.ini").write_text(seq.ini(), encoding="utf-8")
    data_dir = root / "trackers" / tracker_name / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    out = data_dir / f"{seq.name}.txt"
    out.write_text("".join(r + "\n" for r in tracker), encoding="utf-8")
    return out
