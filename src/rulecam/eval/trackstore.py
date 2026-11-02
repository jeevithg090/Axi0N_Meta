"""Score a camera's recorded tracks against MOT ground truth (`rulecam eval track`, add-on 1.5).

`rulecam eval run` scores tracks for clips it plays itself. This scores tracks that already sit in
a track store, such as the 2 Nov corridor test (`phone-68`: seven CAVIAR clips fed once as one
stream, then backfilled), against ground truth for that stream:

- a track point's stream second is its time since `start` (when the stream's first frame was
  fed), so it lands on MOT frame `round(s * fps) + 1`, as in `rulecam.eval.tracking`;
- **raw** scores use the tracker's own ids (SAM 3.1 keyframes + ByteTrack);
- **re-ID** scores relabel each track with the identity the Ask identity job linked it to
  (`track_identity.jsonl`, DINOv2 appearance), so fragments of one person count as one id. Tracks
  the job has not linked keep their own id, and how many there were is reported.

Ids are only compared with each other, never used to say anything about a person (rule R13).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from rulecam.contracts import Track
from rulecam.eval.errors import EvalError
from rulecam.eval.record import PredictedBox
from rulecam.eval.tracking import FrameBoxes, TrackingScores, score_tracking

RAW = "raw"
REID = "reid"


class TrackWindow(Protocol):
    """A track store (`ParquetTrackStore.window`)."""

    def window(self, camera_id: str, t0: datetime, t1: datetime) -> list[Track]:
        """Tracks of a camera with their points in `[t0, t1]`."""
        ...


@dataclass(frozen=True)
class IdSchemeScores:
    """One id scheme's scores for one concept."""

    ids: str  # RAW or REID
    concept: str
    predicted_ids: int
    frames: int
    gt_ids: int
    hota: float
    idf1: float
    mota: float
    id_switches: int
    id_switches_per_min: float | None


@dataclass(frozen=True)
class StoreTrackingReport:
    """Raw and re-ID tracking scores of one camera's stored tracks."""

    camera_id: str
    start: str
    end: str
    fps: float
    scale: tuple[float, float]
    points: int
    tracks: int
    identities: int
    unlinked_tracks: int
    scores: tuple[IdSchemeScores, ...]

    def to_json(self) -> str:
        """The report as indented JSON."""
        return json.dumps(asdict(self), indent=2) + "\n"


def stored_boxes(
    store: TrackWindow, camera_id: str, start: datetime, end: datetime
) -> list[PredictedBox]:
    """Every stored track point of a camera in `[start, end]`, at seconds since `start`."""
    return [
        PredictedBox(
            clip_s=(point.ts - start).total_seconds(),
            track=track.track_id,
            concept=track.concept,
            box_xyxy=point.box_xyxy,
        )
        for track in store.window(camera_id, start, end)
        for point in track.points
    ]


def load_identities(path: Path) -> dict[str, str]:
    """Read the Ask identity job's `track_identity.jsonl` as `track_id -> identity_id`.

    Raises:
        EvalError: If the file is unreadable or a row lacks either id.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        msg = f"cannot read {path}: {exc}"
        raise EvalError(msg) from exc
    out = {}
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            out[str(row["track_id"])] = str(row["identity_id"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            msg = f"{path}:{n} is not a track_id/identity_id row"
            raise EvalError(msg) from exc
    return out


def relabel(
    boxes: Sequence[PredictedBox], identities: Mapping[str, str]
) -> tuple[list[PredictedBox], int]:
    """Replace each box's track id with its identity id.

    Returns:
        The relabelled boxes, and how many distinct tracks had no identity (kept as they were).
    """
    unlinked: set[str] = set()
    out = []
    for box in boxes:
        identity = identities.get(box.track)
        if identity is None:
            unlinked.add(box.track)
            out.append(box.model_copy(update={"track": f"track:{box.track}"}))
        else:
            out.append(box.model_copy(update={"track": f"identity:{identity}"}))
    return out, len(unlinked)


def _rows(
    ids: str, scores: Sequence[TrackingScores], boxes: Sequence[PredictedBox]
) -> list[IdSchemeScores]:
    return [
        IdSchemeScores(
            ids=ids,
            concept=s.concept,
            predicted_ids=len({b.track for b in boxes if b.concept == s.concept}),
            frames=s.frames,
            gt_ids=s.gt_ids,
            hota=s.hota,
            idf1=s.idf1,
            mota=s.mota,
            id_switches=s.id_switches,
            id_switches_per_min=s.id_switches_per_min,
        )
        for s in scores
    ]


def score_store(
    store: TrackWindow,
    gt: Mapping[str, Mapping[int, FrameBoxes]],
    *,
    camera_id: str,
    start: datetime,
    end: datetime,
    fps: float,
    scale: tuple[float, float] = (1.0, 1.0),
    identities: Mapping[str, str] | None = None,
) -> StoreTrackingReport:
    """Score a camera's stored tracks, raw and (with `identities`) re-identified.

    Args:
        store: The track store.
        gt: MOT ground truth, `concept -> frame -> boxes`.
        camera_id: The camera the stream was fed as.
        start: When the stream's first frame was fed (camera time of MOT frame 1).
        end: The end of the stream on camera time.
        fps: The ground truth's frame rate.
        scale: Factor from stored box pixels to ground-truth pixels (0.5 for a 2x upscale).
        identities: `track_id -> identity_id` (`load_identities`); `None` scores raw ids only.

    Returns:
        The report.

    Raises:
        EvalError: If the store has no track point for the camera in the window.
    """
    boxes = stored_boxes(store, camera_id, start, end)
    if not boxes:
        msg = f"no track points for {camera_id} between {start.isoformat()} and {end.isoformat()}"
        raise EvalError(msg)
    rows = _rows(RAW, score_tracking(gt, boxes, fps=fps, scale=scale), boxes)
    unlinked = 0
    if identities is not None:
        linked, unlinked = relabel(boxes, identities)
        rows += _rows(REID, score_tracking(gt, linked, fps=fps, scale=scale), linked)
    tracks = {b.track for b in boxes}
    return StoreTrackingReport(
        camera_id=camera_id,
        start=start.isoformat(),
        end=end.isoformat(),
        fps=fps,
        scale=scale,
        points=len(boxes),
        tracks=len(tracks),
        identities=len({identities[t] for t in tracks if t in identities}) if identities else 0,
        unlinked_tracks=unlinked,
        scores=tuple(rows),
    )


def render_markdown(report: StoreTrackingReport) -> str:
    """A short markdown table of the report."""
    lines = [
        f"Tracking on `{report.camera_id}` from {report.start} to {report.end} "
        f"({report.points} points, {report.tracks} tracks, {report.identities} identities, "
        f"{report.unlinked_tracks} tracks without an identity).",
        "",
        "| Ids | Concept | Predicted ids | GT ids | Frames | HOTA | IDF1 | MOTA | ID switches |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {s.ids} | {s.concept} | {s.predicted_ids} | {s.gt_ids} | {s.frames} | "
        f"{s.hota:.3f} | {s.idf1:.3f} | {s.mota:.3f} | {s.id_switches} |"
        for s in report.scores
    )
    return "\n".join(lines) + "\n"
