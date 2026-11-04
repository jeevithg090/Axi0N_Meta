"""The runtime's strip renderer (task P4-05): Glimmer sees the episode, in the site's privacy mode.

For every candidate the loop hands over, `EpisodeStrips.render`:

1. picks up to 6 keyframes evenly spread over the episode, `ts_start` to `ts_end` (at least the
   last `MIN_SPAN_S` seconds, so an instantaneous candidate still shows its lead-up), from the
   camera's chunks on disk (the current chunk plus the `ChunkIndex`); two times that land on the
   same frame count once, and a time no chunk covers is skipped;
2. decodes them with **one ffmpeg call per chunk** straight into memory (`rawvideo` on a pipe,
   scaled to at most 640 px wide): no raw frame is ever written to disk, in any mode;
3. reads the rule's tracks (`relevant_concepts`) from the track store, and at each keyframe
   takes each track's nearest point (within `TRACK_TOLERANCE_S`); the candidate's subject is
   highlighted;
   With a pose model (`pose`, RTMPose on the CPU), each person without keypoints gets its pose
   estimated on the decoded keyframe, so the skeleton, the head blur and the `hands` PPE region
   follow the real body rather than a figure fitted to the box;
4. draws boxes, the rule's polygon zones and, where the plan has a calibration for the camera, a
   **distance ruler** from the subject's foot to the nearest point of the nearest other object's
   ground footprint (the geometry the rule measured) with a
   one-metre tick on the ground; every word (a caption with the frame, the offset from the
   episode start, the rule id and the measured value, and a colour legend with the concepts and
   the ground distance in metres) goes in Rulecam's **annotation band** below the frame, never
   on the scene (`strip_draw.with_band`);
5. applies the privacy mode (`runtime.strip_draw`): standard blurs faces on the real frame;
   anonymous and strict keep no person pixel (plate + objects minus people + skeletons), and
   each person's skeleton carries a PPE mark per `without` predicate of the rule (`ppe_marks`:
   was an item's box centre in that region of the person at this keyframe);
6. writes the JPEGs under `<data dir>/strips/<camera>/<event>/`, with `annotations.json` listing
   every string the bands show (`verify.annotations`: the verifiers remove copies of them from
   `scene_text` before the firewall), atomically (a half-written strip is never returned), and
   returns the candidate with `strip_paths` set.

**Failures never break the loop.** A chunk that cannot be decoded is skipped (`strip_chunk_failed`
in the log); any other failure gives the candidate an empty strip and `strip_render_failed`, so
Glimmer still runs on the trace alone (and answers `unsure` when it cannot see), exactly as with
no renderer.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from rulecam.contracts import (
    Calibration,
    CandidateEvent,
    Chunk,
    Keypoint,
    PrivacyMode,
    RuleIR,
    Track,
    TrackPoint,
    TrackStore,
    Zone,
)
from rulecam.contracts.core import PointXY
from rulecam.contracts.rule_ir import Without
from rulecam.log import get_logger
from rulecam.planner.runner import passthrough_args
from rulecam.pose.keypoints import PoseEstimator
from rulecam.rules.backtest import DecisionContext
from rulecam.rules.engine import relevant_concepts
from rulecam.rules.errors import RuleEvaluationError
from rulecam.rules.geometry import project
from rulecam.rules.ir import parse_ref, referenced_zones, walk
from rulecam.rules.predicates.spatial import LOWER_HALF, TOP_FRACTION
from rulecam.rules.scene import (
    HAND_REACH_M,
    LEFT_WRIST,
    MIN_KEYPOINT_SCORE,
    PERSON_HEIGHT_M,
    RIGHT_WRIST,
    resolve_zone,
)
from rulecam.runtime.sampling import chunk_end
from rulecam.runtime.skeleton import PpeMark, Region
from rulecam.runtime.strip_draw import (
    FaceDetector,
    Frame,
    Item,
    NoFaceDetector,
    Overlay,
    Ruler,
    YuNetFaces,
    annotation_texts,
    anonymous_frame,
    clean_plate,
    draw_overlay,
    person_mask,
    standard_frame,
)
from rulecam.verify.annotations import write_annotations

_log = get_logger(__name__)

STRIP_FRAMES: Final = (
    6  # default; `RULECAM_STRIP_FRAMES` (Glimmer reads at most `MAX_STRIP_FRAMES`)
)
MIN_SPAN_S: Final = 2.0
MAX_WIDTH: Final = 640
TRACK_TOLERANCE_S: Final = 0.6
DECODE_TIMEOUT_S: Final = 10.0
JPEG_QUALITY: Final = 85
PERSON_WORDS: Final = frozenset(
    {
        "person", "people", "worker", "workers", "human", "pedestrian", "man", "woman",
        "child", "staff", "driver", "operator", "cook", "chef", "employee", "visitor",
        "customer", "patient", "nurse", "hand", "hands", "face", "head", "arm", "body",
    }
)  # fmt: skip
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class ChunkSource(Protocol):
    """The part of `runtime.factory.ChunkIndex` the renderer needs."""

    def chunks(self, camera_id: str, t0: datetime, t1: datetime) -> list[Chunk]:
        """The camera's chunks overlapping `[t0, t1]`, oldest first."""
        ...


Decode = Callable[[Chunk, Sequence[int], tuple[int, int]], list[Frame]]
"""Decodes the given frame indices of a chunk at `(width, height)`, in index order."""


@dataclass(frozen=True)
class Keyframe:
    """One frame of the strip: which chunk, which frame, and its time."""

    chunk: Chunk
    frame_idx: int
    ts: datetime


def strip_span(
    candidate: CandidateEvent, min_span_s: float = MIN_SPAN_S
) -> tuple[datetime, datetime]:
    """The stretch of time a strip covers: the episode, widened backwards to `min_span_s`."""
    end = candidate.ts_end
    return min(candidate.ts_start, end - timedelta(seconds=min_span_s)), end


def pick_keyframes(
    start: datetime, end: datetime, chunks: Sequence[Chunk], n: int = STRIP_FRAMES
) -> list[Keyframe]:
    """Pick up to `n` keyframes evenly spread over `[start, end]` (both ends included).

    Args:
        start: First moment.
        end: Last moment.
        chunks: The camera's chunks (any order, duplicates allowed).
        n: Most keyframes.

    Returns:
        Distinct keyframes in time order; a moment no chunk covers is skipped.

    Raises:
        ValueError: If `n` is below 1.
    """
    if n < 1:
        msg = "a strip needs at least one frame"
        raise ValueError(msg)
    ordered = sorted({c.chunk_id: c for c in chunks}.values(), key=lambda c: c.start_ts)
    times = [end] if n == 1 else [start + (end - start) * i / (n - 1) for i in range(n)]
    picked: list[Keyframe] = []
    seen: set[tuple[str, int]] = set()
    for moment in times:
        covering = [c for c in ordered if c.start_ts <= moment <= chunk_end(c)]
        if not covering:
            continue
        chunk = covering[-1]
        offset_s = (moment - chunk.start_ts).total_seconds()
        index = min(chunk.n_frames - 1, max(0, int(offset_s * chunk.fps + 1e-6)))
        if (chunk.chunk_id, index) in seen:
            continue
        seen.add((chunk.chunk_id, index))
        ts = chunk.start_ts + timedelta(seconds=index / chunk.fps)
        picked.append(Keyframe(chunk=chunk, frame_idx=index, ts=ts))
    return picked


def strip_size(chunk: Chunk, max_width: int = MAX_WIDTH) -> tuple[int, int]:
    """The strip's `(width, height)`: the chunk's, scaled down to `max_width`, both even."""
    scale = min(1.0, max_width / chunk.width)
    return (max(2, int(chunk.width * scale) // 2 * 2), max(2, int(chunk.height * scale) // 2 * 2))


def decode_args(path: Path, indices: Sequence[int], size: tuple[int, int]) -> list[str]:
    """The one ffmpeg command that writes the chosen frames of a chunk as raw BGR to stdout.

    Works on ffmpeg 4.4 (the RunPod image) and later (`passthrough_args`).
    """
    select = "+".join(f"eq(n\\,{i})" for i in indices)
    width, height = size
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vf",
        f"select='{select}',scale={width}:{height}",
        *passthrough_args(),
        "-frames:v",
        str(len(indices)),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]


def ffmpeg_decode(chunk: Chunk, indices: Sequence[int], size: tuple[int, int]) -> list[Frame]:
    """Decode the chosen frames of a chunk in memory with one ffmpeg run.

    Raises:
        OSError: If the chunk file is missing.
        subprocess.CalledProcessError: If ffmpeg fails.
        subprocess.TimeoutExpired: If it runs longer than `DECODE_TIMEOUT_S`.
        ValueError: If ffmpeg returned a different number of frames than asked for.
    """
    path = Path(chunk.path)
    if not path.is_file():
        msg = f"chunk file {chunk.chunk_id} is missing"
        raise FileNotFoundError(msg)
    data = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        decode_args(path, indices, size),
        check=True,
        capture_output=True,
        timeout=DECODE_TIMEOUT_S,
    ).stdout
    width, height = size
    frame_bytes = width * height * 3
    if len(data) != frame_bytes * len(indices):
        got = len(data) / frame_bytes
        msg = f"ffmpeg gave {got:.1f} frames of {chunk.chunk_id}, not {len(indices)}"
        raise ValueError(msg)
    pixels = np.frombuffer(data, dtype=np.uint8).reshape(len(indices), height, width, 3)
    return [pixels[k].copy() for k in range(len(indices))]


class EpisodeFrames:
    """Full-resolution keyframes of a candidate's episode, for Tier 0 (`verify.tier0`).

    The same keyframe choice as the strip (`strip_span`, `pick_keyframes`), from the camera's
    chunks on disk, decoded in memory at the chunk's own size (one ffmpeg call per chunk). A
    chunk that cannot be decoded is skipped.
    """

    def __init__(self, chunks: ChunkSource, decode: Decode = ffmpeg_decode) -> None:
        """Read chunks from `chunks` and decode them with `decode`."""
        self._chunks = chunks
        self._decode = decode

    def frames(self, candidate: CandidateEvent, n: int) -> list[tuple[datetime, Frame]]:
        """Up to `n` BGR frames spread over the episode, with their times, oldest first."""
        start, end = strip_span(candidate)
        known = [
            c
            for c in self._chunks.chunks(candidate.camera_id, start, end)
            if c.camera_id == candidate.camera_id
        ]
        by_chunk: dict[str, list[Keyframe]] = {}
        for key in pick_keyframes(start, end, known, n) if known else []:
            by_chunk.setdefault(key.chunk.chunk_id, []).append(key)
        out: list[tuple[datetime, Frame]] = []
        for keys in by_chunk.values():
            chunk = keys[0].chunk
            try:
                frames = self._decode(
                    chunk, [k.frame_idx for k in keys], (chunk.width, chunk.height)
                )
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                _log.warning("tier0_chunk_failed", chunk_id=chunk.chunk_id, error=str(exc))
                continue
            out.extend((k.ts, f) for k, f in zip(keys, frames, strict=True))
        return sorted(out, key=lambda pair: pair[0])


def is_person(concept: str, rule: RuleIR) -> bool:
    """Whether a concept names people or body parts (its key or its SAM prompt)."""
    spec = rule.concepts.get(concept)
    words = set(re.findall(r"[a-z]+", concept.lower()))
    if spec is not None:
        words |= set(re.findall(r"[a-z]+", spec.prompt.lower()))
    return bool(words & PERSON_WORDS)


def nearest_point(
    track: Track, ts: datetime, tolerance_s: float = TRACK_TOLERANCE_S
) -> TrackPoint | None:
    """The track's point closest to `ts`, if within `tolerance_s`."""
    if not track.points:
        return None
    best = min(track.points, key=lambda p: abs((p.ts - ts).total_seconds()))
    return best if abs((best.ts - ts).total_seconds()) <= tolerance_s else None


def ground(point: TrackPoint, calibration: Calibration) -> PointXY:
    """A point's ground position in metres (its own, else projected from its foot pixel)."""
    if point.foot_m is not None:
        return point.foot_m
    gx, gy = project(calibration, np.asarray([point.foot_px], dtype=np.float64))[0]
    return (float(gx), float(gy))


def unproject(calibration: Calibration, point_m: PointXY) -> PointXY | None:
    """Map a ground point back to image pixels, or `None` if it is not in front of the camera."""
    try:
        inverse = np.linalg.inv(np.asarray(calibration.homography, dtype=np.float64))
    except np.linalg.LinAlgError:
        return None
    x, y, w = inverse @ np.asarray([point_m[0], point_m[1], 1.0])
    if not np.isfinite(w) or abs(w) < 1e-12:  # noqa: PLR2004 - a point at infinity
        return None
    return (float(x / w), float(y / w))


def footprint_point(
    here: npt.NDArray[np.float64], other: TrackPoint, calibration: Calibration
) -> npt.NDArray[np.float64]:
    """The point of the other object's ground footprint nearest to `here` (metres).

    The footprint is the projected bottom edge of its box: the geometry `distance_m` measures
    with a calibration (`rules.scene.footprint_distance`), so the ruler shows the rule's number,
    not the gap to the middle of a wide vehicle. Falls back to its foot point.
    """
    x1, _, x2, y2 = other.box_xyxy
    ends = project(calibration, np.asarray([[x1, y2], [x2, y2]], dtype=np.float64))
    if np.isnan(ends).any():
        return np.asarray(ground(other, calibration), dtype=np.float64)
    start, edge = ends[0], ends[1] - ends[0]
    length_sq = float(edge @ edge)
    t = 0.0 if length_sq == 0 else min(max(float((here - start) @ edge) / length_sq, 0.0), 1.0)
    return np.asarray(start + t * edge, dtype=np.float64)


def distance_ruler(
    subject: TrackPoint, others: Sequence[TrackPoint], calibration: Calibration, scale: float
) -> Ruler | None:
    """The ruler from the subject's foot to the nearest other object's footprint (strip px).

    Args:
        subject: The subject's point at this keyframe (chunk pixels).
        others: The other objects' points at this keyframe.
        calibration: The camera's ground homography (chunk pixels -> metres).
        scale: Strip pixels per chunk pixel.

    Returns:
        The ruler, or `None` when there is no other object.
    """
    if not others:
        return None
    here = np.asarray(ground(subject, calibration), dtype=np.float64)
    spots = [(other, footprint_point(here, other, calibration)) for other in others]
    other, there = min(spots, key=lambda s: float(np.hypot(*(s[1] - here))))
    metres = float(np.hypot(*(there - here)))
    direction = (there - here) / metres if metres > 0 else np.asarray([1.0, 0.0])
    a = unproject(calibration, (float(here[0]), float(here[1])))
    b = unproject(calibration, (float(here[0] + direction[0]), float(here[1] + direction[1])))
    tick = None if a is None or b is None else (_scaled(a, scale), _scaled(b, scale))
    end = unproject(calibration, (float(there[0]), float(there[1]))) or other.foot_px
    return Ruler(
        a=_scaled(subject.foot_px, scale),
        b=_scaled(end, scale),
        metres=metres,
        tick=tick,
    )


def _scaled(point: PointXY, scale: float) -> PointXY:
    return (point[0] * scale, point[1] * scale)


def safe_name(text: str) -> str:
    """A file-name-safe form of an id (no separators, no traversal)."""
    cleaned = _SAFE.sub("_", text).strip(".") or "_"
    return cleaned[:120]


def face_detector(model: Path | None) -> FaceDetector:
    """The strips' face detector: YuNet when its model file is configured, else none.

    Without a model, standard-mode strips still blur the head region of every tracked person;
    a face SAM did not track stays visible, so the log says so once at start-up.
    """
    if model is None or not model.is_file():
        _log.warning("face_detector_missing", model=str(model) if model else None)
        return NoFaceDetector()
    try:
        return YuNetFaces(model)
    except cv2.error as exc:
        _log.warning("face_detector_missing", model=str(model), error=str(exc))
        return NoFaceDetector()


class EpisodeStrips:
    """Renders a candidate's strip from the chunks on disk (the loop's `StripRenderer`)."""

    def __init__(
        self,
        *,
        chunks: ChunkSource,
        store: TrackStore,
        out_dir: Path,
        faces: FaceDetector,
        decode: Decode = ffmpeg_decode,
        frames: int = STRIP_FRAMES,
        max_width: int = MAX_WIDTH,
        raw: bool = False,
        pose: PoseEstimator | None = None,
    ) -> None:
        """Create the renderer.

        Args:
            chunks: Recent chunks per camera.
            store: The track store (the rule's boxes and keypoints).
            out_dir: Where strips are written (`<out_dir>/<camera>/<event>/<k>.jpg`).
            faces: Face detector, for blurring only.
            decode: Decodes frames of a chunk in memory (one call per chunk).
            frames: Most keyframes per strip.
            max_width: Strip width in pixels (frames are scaled down to it, never up).
            raw: Prototype mode: the real frames with the overlays, no face blurring.
            pose: Estimates the keypoints of people the track store has none for.
        """
        self._chunks = chunks
        self._store = store
        self._out_dir = out_dir
        self._faces = faces
        self._decode = decode
        self._frames = frames
        self._max_width = max_width
        self._raw = raw
        self._pose = pose

    def render(
        self,
        candidate: CandidateEvent,
        chunk: Chunk,
        mode: PrivacyMode,
        context: DecisionContext,
    ) -> CandidateEvent:
        """Return the candidate with its strip; an empty strip if it could not be rendered.

        Args:
            candidate: The new candidate.
            chunk: The chunk whose evaluation produced it (the episode ends in it).
            mode: The plan's privacy mode.
            context: The rule, calibration and zones it was evaluated with.

        Returns:
            The candidate with `strip_paths` set.
        """
        try:
            paths = self._render(candidate, chunk, mode, context)
        except Exception as exc:  # a strip is optional evidence: it must never stop verification
            _log.warning(
                "strip_render_failed",
                event_id=candidate.event_id,
                camera_id=candidate.camera_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            paths = ()
        return candidate.model_copy(update={"strip_paths": paths})

    def _render(
        self, candidate: CandidateEvent, chunk: Chunk, mode: PrivacyMode, context: DecisionContext
    ) -> tuple[str, ...]:
        start, end = strip_span(candidate)
        known = [*self._chunks.chunks(candidate.camera_id, start, end), chunk]
        keyframes = pick_keyframes(
            start, end, [c for c in known if c.camera_id == chunk.camera_id], self._frames
        )
        size = strip_size(chunk, self._max_width)
        decoded = self._decode_all(keyframes, size)
        if not decoded:
            _log.warning(
                "strip_no_frames", event_id=candidate.event_id, camera_id=candidate.camera_id
            )
            return ()
        tolerance = timedelta(seconds=TRACK_TOLERANCE_S)
        tracks = self._store.window(candidate.camera_id, start - tolerance, end + tolerance)
        scale = size[0] / chunk.width
        overlays = [
            keyframe_overlay(
                candidate,
                context,
                tracks,
                key,
                scale=scale,
                position=(k, len(decoded)),
                ppe=mode is not PrivacyMode.STANDARD,
                pose=PosePass(self._pose, frame, scale) if self._pose is not None else None,
            )
            for k, (key, frame) in enumerate(decoded, start=1)
        ]
        frames = [frame for _, frame in decoded]
        if self._raw:
            images = [
                draw_overlay(f, o, strict=False, pose=True)
                for f, o in zip(frames, overlays, strict=True)
            ]
        else:
            images = render_frames(frames, overlays, mode, self._faces)
        drawn = [text for overlay in overlays for text in annotation_texts(overlay)]
        paths = self._write(candidate, images, drawn)
        _log.info(
            "strip_rendered",
            event_id=candidate.event_id,
            camera_id=candidate.camera_id,
            frames=len(paths),
            mode="raw" if self._raw else str(mode),
        )
        return paths

    def _decode_all(
        self, keyframes: Sequence[Keyframe], size: tuple[int, int]
    ) -> list[tuple[Keyframe, Frame]]:
        by_chunk: dict[str, list[Keyframe]] = {}
        for key in keyframes:
            by_chunk.setdefault(key.chunk.chunk_id, []).append(key)
        decoded: list[tuple[Keyframe, Frame]] = []
        for keys in by_chunk.values():
            chunk = keys[0].chunk
            try:
                frames = self._decode(chunk, [k.frame_idx for k in keys], size)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                _log.warning(
                    "strip_chunk_failed",
                    chunk_id=chunk.chunk_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            decoded.extend(zip(keys, frames, strict=True))
        return sorted(decoded, key=lambda pair: pair[0].ts)

    def _write(
        self, candidate: CandidateEvent, images: Sequence[Frame], drawn: Sequence[str]
    ) -> tuple[str, ...]:
        folder = self._out_dir / safe_name(candidate.camera_id) / safe_name(candidate.event_id)
        partial = folder.with_name(folder.name + ".part")
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        try:
            for k, image in enumerate(images, start=1):
                path = partial / f"{k}.jpg"
                if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
                    msg = f"could not write strip frame {k}"
                    raise OSError(msg)
            write_annotations(partial, drawn)
            shutil.rmtree(folder, ignore_errors=True)
            partial.rename(folder)
        finally:
            shutil.rmtree(partial, ignore_errors=True)
        return tuple(str(folder / f"{k}.jpg") for k in range(1, len(images) + 1))


def keyframe_overlay(
    candidate: CandidateEvent,
    context: DecisionContext,
    tracks: Sequence[Track],
    key: Keyframe,
    *,
    scale: float,
    position: tuple[int, int],
    ppe: bool = False,
    pose: Callable[[TrackPoint], TrackPoint] | None = None,
) -> Overlay:
    """What is drawn on one keyframe: the rule's tracks, its zones, the ruler and the caption.

    Args:
        candidate: The candidate (its subject is highlighted).
        context: The rule, zones and calibration it was evaluated with.
        tracks: The camera's tracks around the episode.
        key: The keyframe.
        scale: Strip pixels per chunk pixel.
        position: `(k, n)`: this is frame `k` of `n`.
        ppe: Give each person its PPE marks (anonymous and strict mode, where the person's
            pixels, and so the item itself, are not shown).
        pose: Adds keypoints to a person's point (`PosePass`), before it is drawn or its PPE
            regions are checked.

    Returns:
        The overlay, in strip pixels.
    """
    rule = context.rule
    concepts = relevant_concepts(rule, context.zones)
    subject_id = str(candidate.predicate_trace.get("subject", ""))
    present: list[tuple[Track, TrackPoint]] = []
    for track in tracks:
        point = nearest_point(track, key.ts)
        if point is None or (concepts and track.concept not in concepts):
            continue
        if pose is not None and is_person(track.concept, rule):
            point = pose(point)
        present.append((track, point))
    seen: list[tuple[Item, TrackPoint]] = []
    for track, point in present:
        item = _item(track, point, rule, scale, subject=track.track_id == subject_id)
        if ppe and item.person:
            item = replace(item, ppe=ppe_marks(rule, track, point, present))
        seen.append((item, point))
    return Overlay(
        items=tuple(item for item, _ in seen),
        zones=rule_polygons(rule, context.zones, candidate.camera_id, scale),
        ruler=_ruler(seen, context.calibration, candidate.camera_id, scale),
        caption=caption(candidate, key, *position),
    )


class PosePass:
    """Adds estimated keypoints to people's track points at one decoded keyframe.

    Args:
        estimator: The pose model.
        frame: The keyframe as decoded for the strip (strip pixels, BGR).
        scale: Strip pixels per chunk pixel.
    """

    def __init__(self, estimator: PoseEstimator, frame: Frame, scale: float) -> None:
        """Hold the model, the frame and its scale."""
        self._estimator = estimator
        self._frame = frame
        self._scale = scale

    def __call__(self, point: TrackPoint) -> TrackPoint:
        """The point with keypoints in chunk pixels; unchanged if it has some or pose fails."""
        if point.keypoints is not None:
            return point
        scale = self._scale
        x0, y0, x1, y1 = point.box_xyxy
        try:
            found = self._estimator.estimate(
                self._frame, (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
            )
        except Exception as exc:  # pose is optional evidence: never lose the strip over it
            _log.warning("strip_pose_failed", error=f"{type(exc).__name__}: {exc}")
            return point
        keypoints = tuple(Keypoint(x=kp.x / scale, y=kp.y / scale, score=kp.score) for kp in found)
        return point.model_copy(update={"keypoints": keypoints})


def render_frames(
    raws: Sequence[Frame], overlays: Sequence[Overlay], mode: PrivacyMode, faces: FaceDetector
) -> list[Frame]:
    """Render keyframes in a privacy mode (the only place raw pixels turn into strip pixels).

    Args:
        raws: The decoded frames, same size, in time order.
        overlays: One overlay per frame.
        mode: `standard` keeps the real frame (faces blurred); `anonymous`/`strict` keep no
            person pixel (`strip_draw.anonymous_frame`).
        faces: Face detector.

    Returns:
        One rendered frame per input frame.
    """
    if mode is PrivacyMode.STANDARD:
        return [standard_frame(raw, o, faces) for raw, o in zip(raws, overlays, strict=True)]
    plate = clean_plate(
        raws, [person_mask(raw.shape, o.items) for raw, o in zip(raws, overlays, strict=True)]
    )
    strict = mode is PrivacyMode.STRICT
    return [
        anonymous_frame(raw, plate, o, faces, strict=strict)
        for raw, o in zip(raws, overlays, strict=True)
    ]


def _item(track: Track, point: TrackPoint, rule: RuleIR, scale: float, *, subject: bool) -> Item:
    x0, y0, x1, y1 = point.box_xyxy
    keypoints = (
        tuple((kp.x * scale, kp.y * scale, kp.score) for kp in point.keypoints)
        if point.keypoints is not None
        else None
    )
    return Item(
        concept=track.concept,
        box=(x0 * scale, y0 * scale, x1 * scale, y1 * scale),
        person=is_person(track.concept, rule),
        subject=subject,
        colour_key=track.track_id,
        keypoints=keypoints,
    )


def ppe_marks(
    rule: RuleIR,
    person: Track,
    point: TrackPoint,
    present: Sequence[tuple[Track, TrackPoint]],
) -> tuple[PpeMark, ...]:
    """The person's PPE marks at one keyframe: one per region the rule's `without` checks.

    A region is `seen` when any other track of the predicate's item concept has its box centre
    in that region of the person's box at this keyframe, with the same regions as the
    `without` predicate: `full` is the box, `top_25pct` its top quarter, `hands` within
    `HAND_REACH_M` of a wrist keypoint (the lower half of the box without pose). Two predicates
    on the same region give one mark, seen only if both items were.

    Args:
        rule: The rule (its `without` predicates).
        person: The person's track.
        point: The person's point at this keyframe (chunk pixels).
        present: Every rule track seen at this keyframe, with its point.

    Returns:
        The marks, in region order; empty when no `without` predicate is about this concept.
    """
    marks: dict[Region, bool] = {}
    for _, node in walk(rule.predicate):
        if not isinstance(node, Without) or parse_ref(node.subject)[0] != person.concept:
            continue
        item_concept = parse_ref(node.item)[0]
        found = any(
            track.concept == item_concept
            and track.track_id != person.track_id
            and _in_region(node.region, point, _centre(other))
            for track, other in present
        )
        marks[node.region] = marks.get(node.region, True) and found
    return tuple(PpeMark(region=region, seen=seen) for region, seen in sorted(marks.items()))


def _centre(point: TrackPoint) -> PointXY:
    x0, y0, x1, y1 = point.box_xyxy
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def _in_region(region: str, person: TrackPoint, at: PointXY) -> bool:
    """Whether `at` lies in a `without` region of the person's box (chunk pixels)."""
    x0, y0, x1, y1 = person.box_xyxy
    height = y1 - y0
    if region == "top_25pct":
        y1 = y0 + TOP_FRACTION * height
    elif region == "hands":
        keypoints = person.keypoints or ()
        wrists = [
            (kp.x, kp.y)
            for k, kp in enumerate(keypoints)
            if k in (LEFT_WRIST, RIGHT_WRIST) and kp.score >= MIN_KEYPOINT_SCORE
        ]
        if wrists:
            reach = height * HAND_REACH_M / PERSON_HEIGHT_M
            return any(np.hypot(at[0] - wx, at[1] - wy) <= reach for wx, wy in wrists)
        y0 += LOWER_HALF * height
    return x0 <= at[0] <= x1 and y0 <= at[1] <= y1


def _ruler(
    seen: Sequence[tuple[Item, TrackPoint]],
    calibration: Calibration | None,
    camera_id: str,
    scale: float,
) -> Ruler | None:
    """The distance ruler at one keyframe, when the camera is calibrated and the subject seen."""
    if calibration is None or calibration.camera_id != camera_id:
        return None
    subject = next(((item, point) for item, point in seen if item.subject), None)
    if subject is None:
        return None
    others = [p for item, p in seen if not item.subject and item.concept != subject[0].concept]
    return distance_ruler(subject[1], others, calibration, scale)


def rule_polygons(
    rule: RuleIR, zones: Mapping[str, Zone], camera_id: str, scale: float
) -> tuple[tuple[str, tuple[PointXY, ...]], ...]:
    """The rule's polygon zones on this camera, in strip pixels."""
    names = sorted({name for _, node in walk(rule.predicate) for name in referenced_zones(node)})
    found = []
    for name in names:
        try:
            zone = resolve_zone(name, zones, rule)
        except RuleEvaluationError:
            continue
        if zone.camera_id == camera_id and zone.kind == "polygon" and zone.polygon_px:
            found.append((name, tuple(_scaled(p, scale) for p in zone.polygon_px)))
    return tuple(found)


def caption(candidate: CandidateEvent, key: Keyframe, k: int, n: int) -> str:
    """The text bar of a keyframe: position, offset from the episode start, rule, measurement."""
    offset = (key.ts - candidate.ts_start).total_seconds()
    text = f"{k}/{n}  t{offset:+.1f}s  {candidate.rule_id}"
    measured = candidate.measurement
    if measured is not None:
        text += f"  measured {measured.value:.2f} {measured.unit}"
    return text
