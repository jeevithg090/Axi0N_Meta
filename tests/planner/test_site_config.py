"""The owner's site config: loading, validation, defaults and precedence (P2-06)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.api.plans import PlanRegistry
from rulecam.bus import Bus
from rulecam.config import Settings
from rulecam.contracts.core import PrivacyMode
from rulecam.contracts.plan import RequestItem
from rulecam.perception.calibration import ground_distance_m
from rulecam.planner.pipeline import PipelineInput
from rulecam.planner.runner import PlannerService
from rulecam.planner.site_config import (
    SiteConfig,
    SiteConfigError,
    load_site_config,
    parse_site_config,
)
from rulecam.replay.cache import ReplayCache
from rulecam.runtime.factory import build_perception
from tests.fakes import FakeClock
from tests.planner.test_pipeline import HANDWASH, _pipeline, _spark

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "site.example.yaml"
CONFIG = """
site_name: Dock 3
privacy_mode: anonymous
retention_days: 14
languages: [es, en]
roles: {dock_lead: web_push}
cameras:
  dock:
    calibration:
      image_px: [[100, 100], [300, 100], [300, 300], [100, 300]]
      ground_m: [[0, 0], [2, 0], [2, 2], [0, 2]]
    zones:
      sink: [[10, 10], [60, 10], [60, 60], [10, 60]]
"""


def test_the_example_file_is_valid() -> None:
    config = load_site_config(EXAMPLE)
    assert config.site_name == "Dock 3 warehouse"
    assert list(config.calibrations()) == ["cam1"]
    assert [z.zone_id for z in config.zones()] == ["loading_dock", "prep_counter"]


def test_a_config_gives_calibrations_and_zones() -> None:
    config = parse_site_config(CONFIG)
    assert config.privacy_mode is PrivacyMode.ANONYMOUS
    cal = config.calibrations()["dock"]
    assert cal.method == "clicks4"
    assert ground_distance_m(cal, (100.0, 100.0), (300.0, 100.0)) == pytest.approx(2.0)
    (zone,) = config.zones()
    assert (zone.zone_id, zone.camera_id, zone.kind) == ("sink", "dock", "polygon")


def test_missing_config_means_the_defaults(tmp_path: Path) -> None:
    assert load_site_config(None) == SiteConfig()
    assert load_site_config(tmp_path / "absent.yaml") == SiteConfig()
    assert parse_site_config("") == SiteConfig()
    defaults = SiteConfig()
    assert (defaults.site_name, defaults.privacy_mode, defaults.roles) == (
        "demo",
        PrivacyMode.STANDARD,
        {},
    )


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("site_name: [", "not valid YAML"),
        ("- a\n- b", "must be a YAML mapping"),
        ("privacy_mode: open", "privacy_mode"),
        ("surprise: 1", "surprise"),
        (
            "cameras: {c: {calibration: {image_px: [[0,0],[1,0],[2,0],[3,0]], "
            "ground_m: [[0,0],[1,0],[2,0],[3,0]]}}}",
            "collinear",
        ),
        (
            "cameras: {c: {calibration: {image_px: [[0,0],[1,0],[1,1]], "
            "ground_m: [[0,0],[1,0],[1,1]]}}}",
            "at least 4",
        ),
        (
            "cameras: {c: {calibration: {image_px: [[0,0],[9,0],[9,9],[0,9],[4,4]], "
            "ground_m: [[0,0],[1,0],[1,1],[0,1]]}}}",
            "5 image points but 4 ground points",
        ),
        ("cameras: {c: {zones: {z: [[0,0],[9,9],[9,0],[0,9]]}}}", "self-intersecting"),
        (
            "cameras: {a: {zones: {z: [[0,0],[9,0],[9,9]]}}, b: {zones: {z: [[0,0],[9,0],[9,9]]}}}",
            "unique",
        ),
        ("cameras: {'../x': {}}", "names must be"),
    ],
)
def test_invalid_configs_are_rejected(text: str, match: str) -> None:
    with pytest.raises(SiteConfigError, match=match):
        parse_site_config(text)


def test_an_unreadable_config_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(CONFIG, encoding="utf-8")

    def unreadable(self: Path, encoding: str | None = None) -> str:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(SiteConfigError, match="cannot read"):
        load_site_config(path)


def test_the_runner_passes_the_site_config_into_every_run(tmp_path: Path) -> None:
    config = parse_site_config(CONFIG)
    service = PlannerService(
        _pipeline(tmp_path, _spark()),
        site=config,
        frames=dict,
        on_plan=lambda plan: None,
        new_id=lambda: "plan-cfg",
    )
    service.submit([RequestItem(kind="text", text=HANDWASH)])
    service.shutdown()
    saved = PipelineInput.model_validate_json(
        (tmp_path / "plans" / "plan-cfg" / "input.json").read_text(encoding="utf-8")
    )
    assert saved.site_name == "Dock 3"
    assert saved.privacy_mode is PrivacyMode.ANONYMOUS
    assert (saved.retention_days, saved.languages, saved.roles) == (
        14,
        ("es", "en"),
        {"dock_lead": "web_push"},
    )
    assert saved.calibrations == config.calibrations()
    assert saved.zones == config.zones()


def _build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: Path | None
) -> PlannerService | None:
    from rulecam.api.app import build_planner  # noqa: PLC0415 - importing the app builds services

    monkeypatch.chdir(tmp_path)
    settings = Settings(
        rulecam_mode="replay", rulecam_data_dir=tmp_path, rulecam_site_config=config, _env_file=None
    )
    clock, bus = FakeClock(), Bus()
    cache = ReplayCache.from_settings(settings, clock=clock, bus=bus)
    return build_planner(
        settings,
        cache=cache,
        clock=clock,
        bus=bus,
        perception=build_perception(settings, cache=cache, clock=clock, bus=bus),
        plans=PlanRegistry(),
        site="api-site",
    )


def test_build_planner_reads_the_site_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    planner = _build(tmp_path, monkeypatch, path)
    assert planner is not None
    assert planner._site.site_name == "Dock 3"
    missing = _build(tmp_path, monkeypatch, tmp_path / "absent.yaml")
    assert missing is not None
    assert missing._site == SiteConfig(site_name="api-site")
    path.write_text("privacy_mode: open", encoding="utf-8")
    assert _build(tmp_path, monkeypatch, path) is None


def test_cameras_per_gpu_is_optional_and_bounded() -> None:
    assert load_site_config(EXAMPLE).cameras_per_gpu == 6
    assert parse_site_config(CONFIG).cameras_per_gpu is None
    assert parse_site_config("cameras_per_gpu: 4").cameras_per_gpu == 4
    for bad in ("cameras_per_gpu: 0", "cameras_per_gpu: 65", "cameras_per_gpu: many"):
        with pytest.raises(SiteConfigError, match="cameras_per_gpu"):
            parse_site_config(bad)
