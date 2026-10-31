"""Site geometry for the planner: calibrations and zones from the scene and the owner (P2-06).

Nothing here is invented. Every zone and every calibration a compiled rule uses comes from one
of two places:

- the owner's site config (`planner.site_config`): click calibrations and drawn polygons;
- the scene: SAM detections the survey (step 2) and the probe (step 3) already made. A
  `RecordingSegmenter` keeps those detections, so no extra SAM call is spent on geometry.

Calibration from the scene (`scene_calibrations`): a detected reference object of known size
lying on the floor (`pallet_48x40in` for a concept like "wooden pallet", `floor_square_1m` for
a "1 m floor square") gives a ground homography through `calibrate_reference` on its decoded
mask. Per camera the candidate with the lowest `err_m` wins. A detection with only a box (no
decodable mask) is never used: a box is not a footprint, so it would invent a scale. When the
survey also saw a door on that camera, a candidate whose scale disagrees with a 2.03 m door
(`door_scale_ok`) is dropped. The owner's calibration always wins over the scene's.

Reference probes (`metric_cameras`, `probe_references`): the survey only sees a pallet if Spark
names it with a phrase `reference_kind` accepts, and Spark's wording varies ("wooden pallet" on
one run, "stacked pallet with bags" on the next). So when a concern needs metres (its text names
a distance or a speed, `needs_metres`), the probe step explicitly prompts SAM with every
`REFERENCE_PROMPTS` phrase on each camera that saw one of that concern's needed concepts and is
not calibrated yet (by the owner's site config or by the survey). It reuses the survey's frames:
`REFERENCE_PROBE_FRAMES` (3) of the camera's 8 survey frames, so at most 3 x 2 = 6 SAM calls
per camera. Without a metric concern no reference probe runs. The detections go through
`scene_calibrations` like the survey's; nothing found means no calibration (never a guess).

Zones from the scene (`scene_zones`): a needed concept that is a place (sink, prep counter,
door, dock, ...) becomes a polygon zone on each camera that sees it: the most confident
detection's box, widened by `PLACE_SIDE_MARGIN` of its width on each side and extended
`PLACE_FLOOR_MARGIN` of its height downwards (the floor in front of a sink or counter, where a
person's foot point is while they use it), clipped to the frame. The owner's zone of the same
name wins.

`SiteGeometry` is what the compiler is given: the candidate zones and the calibrations. Its
`ground` replaces every polygon a model wrote with the candidate of that name and rejects a
polygon zone that is not a candidate (the compile repair round sees the error), strips any
pixels from buffer zones (a buffer is measured in metres at run time, around its concept), and
sets the rule's `calibration` name only when every camera the metric rule runs on (its camera
scope, `planner.scope`) is calibrated.
A metric rule on an uncalibrated camera is still compiled; acceptance then fails its
`calibration_err<=tol` check, keeps it amber and asks the owner to mark a known distance.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Final

import cv2
import numpy as np
from pydantic import Field

from rulecam.contracts.core import BoxXYXY, Calibration, Detection, Frozen, PointXY, Zone
from rulecam.contracts.plan import FeasibilityVerdict
from rulecam.contracts.rule_ir import RuleIR, ZoneSpec
from rulecam.log import get_logger
from rulecam.perception.calibration import (
    DOOR_SCALE_TOLERANCE,
    ReferenceKind,
    calibrate_reference,
    door_scale_ratio,
)
from rulecam.perception.errors import CalibrationError
from rulecam.perception.masks import NO_MASK, decode_rle
from rulecam.planner.acceptance import is_metric
from rulecam.planner.scope import scoped_cameras
from rulecam.planner.steps.survey import DEFAULT_SURVEY_FRAMES, ImageSegmenter, sample_frames

_log = get_logger(__name__)

FrameSize = tuple[int, int]

PLACE_SIDE_MARGIN: Final = 0.15
PLACE_FLOOR_MARGIN: Final = 0.6
MIN_REFERENCE_AREA_PX: Final = 900
_PALLET: Final = re.compile(r"^(?:(?:wooden|wood|plastic|empty|standard|euro)\s+)*pallets?$")
_FLOOR_SQUARE: Final = re.compile(
    r"^(?:(?:1\s?m|one[\s-]met(?:re|er)|calibration)\s+)(?:floor\s+)?(?:square|mat|tile)s?$"
)
_DOOR: Final = re.compile(r"\b(?:doors?|doorways?)\b")
PLACE_WORDS: Final = re.compile(
    r"\b(?:sinks?|doors?|doorways?|exits?|racks?|racking|shel(?:f|ves|ving)|counters?|"
    r"stations?|tables?|benches?|docks?|bays?|aisles?|stoves?|ovens?|fryers?|grills?|"
    r"fridges?|freezers?|coolers?|walk-?ins?|dishwashers?|conveyors?|lines?|areas?)\b"
)
_NOT_PLACE: Final = re.compile(r"\b(?:person|people|worker|hands?|gloves?|forklifts?)\b")
_SLUG: Final = re.compile(r"[^a-z0-9]+")
REFERENCE_PROMPTS: Final[dict[str, ReferenceKind]] = {
    "wooden pallet": "pallet_48x40in",
    "1 m floor square": "floor_square_1m",
}
REFERENCE_PROBE_FRAMES: Final = 3
_METRIC: Final = re.compile(
    r"\d\s*(?:m|cm|ft|km/?h|mph|m/s)\b"
    r"|\b(?:met(?:re|er)s?|metros?|feet|foot|yards?|distance|distancia|speed(?:ing)?|"
    r"velocidad|too\s+fast|buffer)\b"
)


# --- recording the steps' SAM calls -------------------------------------------------------------


@dataclass(frozen=True)
class Sighting:
    """One SAM call made by the survey or the probe: what it found in one frame."""

    camera_id: str
    frame: Path
    concept: str
    detections: tuple[Detection, ...]


class RecordingSegmenter:
    """An `ImageSegmenter` that passes calls through and keeps what they found (thread-safe)."""

    def __init__(self, inner: ImageSegmenter) -> None:
        """Wrap `inner`."""
        self._inner = inner
        self._lock = threading.Lock()
        self._sightings: list[Sighting] = []

    def segment_image(
        self,
        frame: Path,
        concept: str,
        *,
        camera_id: str = "probe",
        include_confidence: bool = True,
    ) -> list[Detection]:
        """Segment through the wrapped segmenter and record the result."""
        detections = self._inner.segment_image(
            frame, concept, camera_id=camera_id, include_confidence=include_confidence
        )
        with self._lock:
            self._sightings.append(Sighting(camera_id, frame, concept, tuple(detections)))
        return detections

    @property
    def sightings(self) -> tuple[Sighting, ...]:
        """Every recorded call, in call order."""
        with self._lock:
            return tuple(self._sightings)


# --- concepts -----------------------------------------------------------------------------------


def _normalise(concept: str) -> str:
    return " ".join(concept.lower().split())


def reference_kind(concept: str) -> ReferenceKind | None:
    """The calibration reference a concept phrase names, if any.

    `wooden pallet` and `pallet` are `pallet_48x40in` (not `pallet jack`, `pallet rack` or
    `pallet of boxes`: a loaded pallet's mask is not its footprint); `1 m floor square` and
    `calibration mat` are `floor_square_1m`.
    """
    phrase = _normalise(concept)
    if _PALLET.fullmatch(phrase):
        return "pallet_48x40in"
    if _FLOOR_SQUARE.fullmatch(phrase):
        return "floor_square_1m"
    return None


def is_place(concept: str) -> bool:
    """Whether a concept is a fixed place a person can be at (sink, prep counter, door, ...)."""
    phrase = _normalise(concept)
    return bool(PLACE_WORDS.search(phrase)) and not _NOT_PLACE.search(phrase)


def zone_slug(concept: str) -> str:
    """A zone name for a concept: `prep counter` -> `prep_counter`."""
    return _SLUG.sub("_", _normalise(concept)).strip("_")[:48] or "zone"


# --- calibration from the scene -----------------------------------------------------------------


def _mask(detection: Detection) -> np.ndarray | None:
    if detection.mask.rle == NO_MASK:
        return None
    try:
        return decode_rle(detection.mask.rle)
    except ValueError:
        return None


def _door_boxes(sightings: Iterable[Sighting], camera_id: str) -> list[BoxXYXY]:
    return [
        d.mask.box_xyxy
        for s in sightings
        if s.camera_id == camera_id and _DOOR.search(_normalise(s.concept))
        for d in s.detections
    ]


def _agrees_with_doors(calibration: Calibration, doors: Sequence[BoxXYXY]) -> bool:
    """True when no door was seen, or the doors' median scale ratio is within tolerance."""
    ratios: list[float] = []
    for box in doors:
        try:
            ratios.append(door_scale_ratio(calibration, box))
        except CalibrationError:
            continue
    return not ratios or abs(median(ratios) - 1.0) <= DOOR_SCALE_TOLERANCE


def scene_calibrations(sightings: Sequence[Sighting]) -> dict[str, Calibration]:
    """Ground calibrations from reference objects SAM found, the best (lowest `err_m`) per camera.

    Args:
        sightings: The survey's (and probe's) SAM calls.

    Returns:
        Camera id -> calibration (method `reference_object`); cameras without a usable
        reference are absent.
    """
    best: dict[str, Calibration] = {}
    for sighting in sightings:
        kind = reference_kind(sighting.concept)
        if kind is None:
            continue
        doors = _door_boxes(sightings, sighting.camera_id)
        for detection in sighting.detections:
            mask = _mask(detection)
            if mask is None or int(mask.sum()) < MIN_REFERENCE_AREA_PX:
                continue
            try:
                calibration = calibrate_reference(sighting.camera_id, mask, kind)
            except CalibrationError as exc:
                _log.info(
                    "scene_calibration_rejected", camera_id=sighting.camera_id, error=str(exc)
                )
                continue
            if not _agrees_with_doors(calibration, doors):
                _log.warning("scene_calibration_door_mismatch", camera_id=sighting.camera_id)
                continue
            current = best.get(sighting.camera_id)
            if current is None or calibration.err_m < current.err_m:
                best[sighting.camera_id] = calibration
    for camera_id, calibration in sorted(best.items()):
        _log.info(
            "scene_calibration",
            camera_id=camera_id,
            reference=calibration.reference,
            err_m=round(calibration.err_m, 4),
        )
    return dict(sorted(best.items()))


# --- reference probes --------------------------------------------------------------------------


class ReferenceProbe(Frozen):
    """One reference prompt tried on one camera for scene calibration.

    Attributes:
        camera_id: The camera.
        prompt: The SAM prompt (a `REFERENCE_PROMPTS` key).
        n_frames: Frames it ran on.
        n_detections: Detections SAM returned over those frames.
    """

    camera_id: str
    prompt: str
    n_frames: int
    n_detections: int


def needs_metres(concern: str) -> bool:
    """Whether a concern's text asks for a ground distance or a speed (`2 m`, `metres`, `speed`)."""
    return bool(_METRIC.search(_normalise(concern)))


def metric_cameras(
    needs: Mapping[str, Sequence[str]],
    seen: Mapping[str, Iterable[str]],
    *,
    calibrated: Iterable[str] = (),
) -> tuple[str, ...]:
    """The cameras a metric concern runs on that still lack a ground calibration.

    Args:
        needs: Concern text -> the concepts it needs.
        seen: Camera id -> the concepts SAM found there at least once.
        calibrated: Cameras already calibrated (owner's site config, survey); skipped.

    Returns:
        Camera ids, sorted: those that saw a needed concept of a concern that needs metres.
    """
    done = set(calibrated)
    wanted = {
        _normalise(c) for text, concepts in needs.items() if needs_metres(text) for c in concepts
    }
    return tuple(
        sorted(
            camera_id
            for camera_id, concepts in seen.items()
            if camera_id not in done and wanted & {_normalise(c) for c in concepts}
        )
    )


def probe_references(
    cameras: Iterable[str],
    camera_frames: Mapping[str, Sequence[Path]],
    *,
    segmenter: ImageSegmenter,
    seed: int,
    n_frames: int = REFERENCE_PROBE_FRAMES,
) -> tuple[ReferenceProbe, ...]:
    """Prompt SAM with every reference phrase on a few of each camera's survey frames.

    Pass a `RecordingSegmenter` and give its sightings to `scene_calibrations`.

    Args:
        cameras: The cameras to probe.
        camera_frames: Camera id -> its frames, oldest first.
        segmenter: SAM 3.1 on single images.
        seed: The run's seed (the same as the survey's, so its frames are reused).
        n_frames: Frames per camera per prompt, out of the survey's sample.

    Returns:
        One record per (camera, prompt), cameras in the given order.
    """
    tried: list[ReferenceProbe] = []
    for camera_id in cameras:
        survey_frames = sample_frames(
            camera_frames.get(camera_id, ()), DEFAULT_SURVEY_FRAMES, seed=seed
        )
        frames = sample_frames(survey_frames, n_frames, seed=seed)
        for prompt in REFERENCE_PROMPTS:
            found = sum(
                len(segmenter.segment_image(frame, prompt, camera_id=camera_id)) for frame in frames
            )
            tried.append(
                ReferenceProbe(
                    camera_id=camera_id, prompt=prompt, n_frames=len(frames), n_detections=found
                )
            )
            _log.info("reference_probe", camera_id=camera_id, prompt=prompt, found=found)
    return tuple(tried)


# --- zones from the scene -----------------------------------------------------------------------


def frame_size(frame: Path) -> FrameSize | None:
    """(width, height) of an image file, or `None` when it cannot be read."""
    image = cv2.imread(str(frame), cv2.IMREAD_GRAYSCALE) if frame.is_file() else None
    if image is None:
        return None
    height, width = image.shape[:2]
    return width, height


def place_polygon(box: BoxXYXY, width: int, height: int) -> tuple[PointXY, ...] | None:
    """The zone polygon for a place: its box widened and extended down to the floor, clipped.

    Args:
        box: The place's detection box (x1, y1, x2, y2) in pixels.
        width: Frame width.
        height: Frame height.

    Returns:
        Four vertices clockwise from the top-left, or `None` if nothing is left in the frame.
    """
    x1, y1, x2, y2 = box
    side = PLACE_SIDE_MARGIN * (x2 - x1)
    floor = PLACE_FLOOR_MARGIN * (y2 - y1)
    left, right = max(0.0, x1 - side), min(float(width), x2 + side)
    top, bottom = max(0.0, y1), min(float(height), y2 + floor)
    if right <= left or bottom <= top:
        return None
    return ((left, top), (right, top), (right, bottom), (left, bottom))


def _detection_size(detection: Detection, frame: Path) -> FrameSize | None:
    mask = _mask(detection)
    if mask is not None:
        return int(mask.shape[1]), int(mask.shape[0])
    return frame_size(frame)


def _rank(detection: Detection) -> tuple[float, float]:
    x1, y1, x2, y2 = detection.mask.box_xyxy
    return (detection.mask.confidence or 0.0, (x2 - x1) * (y2 - y1))


def scene_zones(sightings: Sequence[Sighting], concepts: Iterable[str]) -> tuple[Zone, ...]:
    """Polygon zones for the place concepts among `concepts`, one per camera that sees each.

    The zone is named after the concept (`prep_counter`), with `_<camera>` appended when more
    than one camera sees it.

    Args:
        sightings: The survey's and probe's SAM calls.
        concepts: The concepts the concerns need.

    Returns:
        The zones, in concept then camera order.
    """
    places = [c for c in dict.fromkeys(_normalise(c) for c in concepts) if is_place(c)]
    zones: list[Zone] = []
    for concept in places:
        found: dict[str, tuple[Detection, Path]] = {}
        for s in sightings:
            if _normalise(s.concept) != concept:
                continue
            for detection in s.detections:
                held = found.get(s.camera_id)
                if held is None or _rank(detection) > _rank(held[0]):
                    found[s.camera_id] = (detection, s.frame)
        polygons: dict[str, tuple[PointXY, ...]] = {}
        for camera_id, (detection, frame) in sorted(found.items()):
            size = _detection_size(detection, frame)
            polygon = place_polygon(detection.mask.box_xyxy, *size) if size else None
            if polygon is not None:
                polygons[camera_id] = polygon
        for camera_id, polygon in polygons.items():
            name = zone_slug(concept) + (f"_{zone_slug(camera_id)}" if len(polygons) > 1 else "")
            zones.append(
                Zone(zone_id=name, camera_id=camera_id, kind="polygon", polygon_px=polygon)
            )
    return tuple(zones)


# --- what the compiler is given -----------------------------------------------------------------


def zone_spec(zone: Zone) -> ZoneSpec:
    """A contract `Zone` as a rule's `ZoneSpec`."""
    return ZoneSpec(
        kind=zone.kind,
        camera_id=zone.camera_id,
        polygon_px=zone.polygon_px,
        buffer_of=zone.buffer_of_concept,
        buffer_m=zone.buffer_m,
    )


class SiteGeometry(Frozen):
    """The cameras, candidate zones and calibrations a rule may use.

    Attributes:
        cameras: The site's camera ids.
        zones: Candidate zone name -> its spec.
        zone_origins: Candidate zone name -> where it came from, for the prompt.
        calibrations: Camera id -> ground calibration (owner's over the scene's).
        feasibility: The gauge's verdicts, which scope each rule to its cameras.
    """

    cameras: tuple[str, ...]
    zones: dict[str, ZoneSpec] = Field(default_factory=dict)
    zone_origins: dict[str, str] = Field(default_factory=dict)
    calibrations: dict[str, Calibration] = Field(default_factory=dict)
    feasibility: tuple[FeasibilityVerdict, ...] = ()

    @classmethod
    def build(
        cls,
        cameras: Iterable[str],
        *,
        owner_zones: Sequence[Zone] = (),
        scene_zones: Sequence[Zone] = (),
        owner_calibrations: Mapping[str, Calibration] | None = None,
        scene_calibrations: Mapping[str, Calibration] | None = None,
        feasibility: Sequence[FeasibilityVerdict] = (),
    ) -> SiteGeometry:
        """Merge the owner's and the scene's geometry; the owner's wins on a clash.

        Args:
            cameras: The site's camera ids.
            owner_zones: Zones from the site config.
            scene_zones: Zones built from detections.
            owner_calibrations: Calibrations from the site config.
            scene_calibrations: Calibrations from reference objects.
            feasibility: The gauge's verdicts (they scope each rule's calibration name).

        Returns:
            The geometry.
        """
        zones: dict[str, ZoneSpec] = {}
        origins: dict[str, str] = {}
        for zone in scene_zones:
            zones[zone.zone_id] = zone_spec(zone)
            origins[zone.zone_id] = "seen by the camera"
        for zone in owner_zones:
            zones[zone.zone_id] = zone_spec(zone)
            origins[zone.zone_id] = "drawn by the owner"
        calibrations = {**(scene_calibrations or {}), **(owner_calibrations or {})}
        return cls(
            cameras=tuple(sorted(set(cameras))),
            zones=dict(sorted(zones.items())),
            zone_origins=dict(sorted(origins.items())),
            calibrations=dict(sorted(calibrations.items())),
            feasibility=tuple(feasibility),
        )

    def prompt_lines(self) -> list[str]:
        """What the compiler is told about zones and calibration (data, in plain words)."""
        if self.zones:
            listed = ", ".join(
                f"{name} ({spec.kind} on {spec.camera_id}, {self.zone_origins.get(name, 'given')})"
                for name, spec in self.zones.items()
            )
            zones = (
                f"Candidate zones: {listed}. Use these names exactly; Rulecam fills in their "
                "polygons, so never write polygon_px and never declare another polygon zone."
            )
        else:
            zones = "Candidate zones: none. Do not declare polygon zones."
        cams = ", ".join(self._camera_line(c) for c in self.cameras) or "none"
        return [
            zones,
            "For a distance around a thing (e.g. '2 m around the forklift') declare a buffer "
            "zone {kind: buffer, camera_id, buffer_of: <declared concept name>, buffer_m: "
            "<metres from the request>} or use distance_m. Metres need a calibrated camera.",
            f"Cameras: {cams}.",
        ]

    def _camera_line(self, camera_id: str) -> str:
        cal = self.calibrations.get(camera_id)
        if cal is None:
            return f"{camera_id} (no ground calibration: metres cannot be measured there yet)"
        how = f"{cal.method} {cal.reference}" if cal.reference else cal.method
        return f"{camera_id} (ground calibration: {how}, err {cal.err_m:.2f} m)"

    def ground(self, rule: RuleIR) -> RuleIR:
        """Make the rule's zones and calibration name the real ones.

        Args:
            rule: A compiled rule.

        Returns:
            The rule with candidate polygons, pixel-free buffers and an honest `calibration`.

        Raises:
            ValueError: A polygon zone that is not a candidate, or a buffer zone on an unknown
                camera or without a positive `buffer_m`.
        """
        problems: list[str] = []
        zones: dict[str, ZoneSpec] = {}
        for name, spec in rule.zones.items():
            candidate = self.zones.get(name)
            if candidate is not None and candidate.kind == spec.kind:
                zones[name] = candidate
            elif spec.kind == "polygon":
                known = ", ".join(n for n, s in self.zones.items() if s.kind == "polygon")
                problems.append(
                    f"zone {name!r} is a polygon Rulecam was not given; use one of the "
                    f"candidate zones ({known or 'there are none'}) or a buffer zone"
                )
            elif spec.camera_id not in self.cameras:
                problems.append(
                    f"buffer zone {name!r} is on camera {spec.camera_id!r}; the cameras are "
                    f"{list(self.cameras)}"
                )
            elif spec.buffer_m is None or spec.buffer_m <= 0:
                problems.append(f"buffer zone {name!r} needs buffer_m > 0 from the request")
            else:
                zones[name] = spec.model_copy(update={"polygon_px": None})
        if problems:
            raise ValueError("; ".join(problems))
        grounded = RuleIR.model_validate({**rule.model_dump(), "zones": zones, "calibration": None})
        return grounded.model_copy(update={"calibration": self.calibration_name(grounded)})

    def calibration_name(self, rule: RuleIR) -> str | None:
        """`ground-plane@<camera>[,...]` for a metric rule all of whose cameras are calibrated.

        The cameras are the rule's scope (`planner.scope`), else every site camera.
        """
        if not is_metric(rule):
            return None
        cameras = scoped_cameras(rule, self.cameras, self.feasibility)
        if not cameras or any(c not in self.calibrations for c in cameras):
            return None
        return ",".join(f"ground-plane@{c}" for c in cameras)
