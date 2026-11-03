"""The shared check format: field names other modules and add-ons code against."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rulecam.extensions.schema import CheckObject, CheckResult, CheckSample


def test_sample_json_has_exactly_the_shared_keys() -> None:
    sample = CheckSample(
        timestamp="2026-11-03T12:00:00Z",
        frame_w=640,
        frame_h=480,
        objects=(
            CheckObject(
                concept="pallet",
                box_xyxy_px=(10, 20, 30, 40),
                mask_top_y_px=20,
                mask_area_px=400,
                ground_xy_m=(1.0, 2.0),
                score=0.9,
            ),
        ),
        zones={"rack": ((0, 0), (10, 0), (10, 10))},
        lines={"yellow": ((0, 300), (640, 300))},
        px_per_m=120.0,
    )
    data = sample.as_json()
    assert set(data) == {
        "timestamp",
        "frame_w",
        "frame_h",
        "objects",
        "zones",
        "lines",
        "px_per_m",
    }
    objects = data["objects"]
    assert isinstance(objects, list)
    assert set(objects[0]) == {
        "concept",
        "box_xyxy_px",
        "mask_top_y_px",
        "mask_area_px",
        "ground_xy_m",
        "score",
    }
    assert data["lines"] == {"yellow": [[0.0, 300.0], [640.0, 300.0]]}


def test_result_is_strict() -> None:
    assert CheckResult.model_validate({"value": True, "measure": 3}).measure == 3.0
    for bad in ({"value": 1}, {"value": True, "measure": float("inf")}, {"value": True, "x": 1}):
        with pytest.raises(ValidationError):
            CheckResult.model_validate(bad)
