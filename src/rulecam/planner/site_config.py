"""The owner's site configuration for the Site Planner (task P2-06, `RULECAM_SITE_CONFIG`).

A YAML file the owner writes once per site: the site name, privacy mode, evidence retention,
alert roles, crew languages, how many cameras share the site's GPU pod (`cameras_per_gpu`, for
the cost per camera-hour; the planner ignores it) and, per camera, an optional manual ground
calibration (four or more clicked floor pixels with their positions in metres) and optional
named polygon zones the owner drew. `docs/site.example.yaml` is a commented example;
`docs/deploy.md` says where it goes.

Precedence: what the owner writes here always wins over what the planner infers from the scene.
A camera calibrated here is never re-calibrated from a pallet, and an owner zone replaces a
scene zone of the same name.

`load_site_config(None)` and a missing file give `SiteConfig()` (today's defaults: site `demo`,
Standard privacy, no roles, zones or calibrations), logged as `site_config_missing`. A file that
does not validate raises `SiteConfigError` with every problem; nothing is half-loaded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from rulecam.contracts.core import Calibration, PointXY, PrivacyMode, Zone
from rulecam.log import get_logger
from rulecam.perception.calibration import MIN_POINTS, calibrate_clicks, polygon_zone
from rulecam.perception.errors import PerceptionError
from rulecam.planner.errors import PlannerError

_log = get_logger(__name__)

DEFAULT_SITE_NAME: Final = "demo"
MAX_CAMERAS_PER_GPU: Final = 64
NAME_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


class SiteConfigError(PlannerError):
    """The site configuration file cannot be read or does not validate."""


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClickCalibration(_Strict):
    """Four or more floor points: where they are in the image and on the ground.

    Attributes:
        image_px: Clicked pixels (x right, y down), no three on a line.
        ground_m: The same points' ground positions in metres, in the same order (e.g. the
            corners of a 1 m floor square: `[[0, 0], [1, 0], [1, 1], [0, 1]]`).
    """

    image_px: tuple[PointXY, ...] = Field(min_length=MIN_POINTS)
    ground_m: tuple[PointXY, ...] = Field(min_length=MIN_POINTS)

    @model_validator(mode="after")
    def _same_length(self) -> ClickCalibration:
        if len(self.image_px) != len(self.ground_m):
            msg = f"{len(self.image_px)} image points but {len(self.ground_m)} ground points"
            raise ValueError(msg)
        return self


class CameraConfig(_Strict):
    """What the owner set up for one camera.

    Attributes:
        calibration: The manual ground calibration, if any.
        zones: Zone name -> polygon vertices in image pixels.
    """

    calibration: ClickCalibration | None = None
    zones: dict[str, tuple[PointXY, ...]] = Field(default_factory=dict)


class SiteConfig(_Strict):
    """The owner's site configuration (every field optional; the defaults are today's).

    Attributes:
        site_name: The site every plan is for.
        privacy_mode: `standard`, `anonymous` or `strict`.
        retention_days: Evidence retention.
        roles: Role -> alert channel, e.g. `dock_lead: web_push`.
        languages: Crew languages (ISO codes), listed before any the planner infers.
        cameras_per_gpu: Cameras that share the site's one GPU pod (Glimmer + SAM image mode,
            docs/deploy.md "One GPU per site"); the eval scorecard divides the pod's price by
            it for the GPU share of the cost per camera-hour. `None`: not stated (1).
        cameras: Camera id -> its calibration and zones.
    """

    site_name: str = Field(default=DEFAULT_SITE_NAME, min_length=1, max_length=120)
    privacy_mode: PrivacyMode = PrivacyMode.STANDARD
    retention_days: int = Field(default=30, ge=1, le=3650)
    roles: dict[str, str] = Field(default_factory=dict)
    languages: tuple[str, ...] = ()
    cameras_per_gpu: int | None = Field(default=None, ge=1, le=MAX_CAMERAS_PER_GPU)
    cameras: dict[str, CameraConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _geometry_builds(self) -> SiteConfig:
        """Check names, and that every calibration fits and every zone is a simple polygon."""
        names = [*self.cameras, *(z for cam in self.cameras.values() for z in cam.zones)]
        bad = [n for n in names if re.fullmatch(NAME_PATTERN, n) is None]
        if bad:
            msg = f"camera and zone names must be 1-64 letters, digits, '_', '-' or '.': {bad}"
            raise ValueError(msg)
        zone_names = [z for cam in self.cameras.values() for z in cam.zones]
        repeated = sorted({z for z in zone_names if zone_names.count(z) > 1})
        if repeated:
            msg = f"zone names must be unique across cameras: {repeated}"
            raise ValueError(msg)
        try:
            self.calibrations()
            self.zones()
        except PerceptionError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def calibrations(self) -> dict[str, Calibration]:
        """Camera id -> the owner's click calibration (method `clicks4`).

        Raises:
            CalibrationError: If a calibration cannot be fitted (checked when loading).
        """
        return {
            camera_id: calibrate_clicks(
                camera_id, cam.calibration.image_px, cam.calibration.ground_m
            )
            for camera_id, cam in sorted(self.cameras.items())
            if cam.calibration is not None
        }

    def zones(self) -> tuple[Zone, ...]:
        """The owner's polygon zones, cameras and zones in name order.

        Raises:
            ZoneError: If a polygon is not simple (checked when loading).
        """
        return tuple(
            polygon_zone(name, camera_id, points)
            for camera_id, cam in sorted(self.cameras.items())
            for name, points in sorted(cam.zones.items())
        )


def parse_site_config(text: str, *, source: str = "<site config>") -> SiteConfig:
    """Parse and validate site configuration YAML.

    Args:
        text: The YAML.
        source: Where it came from, for error messages.

    Returns:
        The configuration (an empty document gives the defaults).

    Raises:
        SiteConfigError: If the YAML is malformed or does not validate.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"{source} is not valid YAML: {exc}"
        raise SiteConfigError(msg) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        msg = f"{source} must be a YAML mapping, got {type(data).__name__}"
        raise SiteConfigError(msg)
    try:
        return SiteConfig.model_validate(data)
    except ValidationError as exc:
        msg = f"{source} is invalid: {exc}"
        raise SiteConfigError(msg) from exc


def load_site_config(path: Path | None) -> SiteConfig:
    """Load the site configuration, or the defaults when there is none.

    Args:
        path: The YAML file (`RULECAM_SITE_CONFIG`), or `None`.

    Returns:
        The configuration; `SiteConfig()` (logged) when `path` is `None` or not a file.

    Raises:
        SiteConfigError: If the file cannot be read or does not validate.
    """
    if path is None or not path.is_file():
        _log.warning(
            "site_config_missing",
            path=str(path) if path else None,
            using="defaults: site 'demo', standard privacy, no roles, zones or calibrations",
        )
        return SiteConfig()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read site config {path}: {exc}"
        raise SiteConfigError(msg) from exc
    config = parse_site_config(text, source=str(path))
    _log.info(
        "site_config_loaded",
        path=str(path),
        site=config.site_name,
        calibrated=sorted(config.calibrations()),
        zones=[z.zone_id for z in config.zones()],
    )
    return config
