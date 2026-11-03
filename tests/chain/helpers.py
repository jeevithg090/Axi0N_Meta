"""Shared builders for the Chain Mode tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from rulecam.chain import RulePack, StoreSetup

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "chains" / "kitchen-basics"

CALIBRATION = {
    "image_px": [[412, 610], [868, 604], [930, 812], [355, 820]],
    "ground_m": [[0, 0], [3, 0], [3, 2], [0, 2]],
}
LINE = [[1100, 380], [1700, 380], [1700, 760], [1100, 760]]
ROLES = {
    "shift_lead": "web_push",
    "facilities": "web_push",
    "store_manager": "web_push",
    "kitchen_lead": "web_push",
}


def demo_pack_data() -> dict[str, Any]:
    """The demo pack as a mutable dict."""
    data: dict[str, Any] = yaml.safe_load((DEMO / "pack.yaml").read_text("utf-8"))
    return data


def pack_from(data: dict[str, Any]) -> RulePack:
    return RulePack.model_validate(copy.deepcopy(data))


def handwash_only(**changes: Any) -> dict[str, Any]:
    """The demo pack with only its hand-wash template, plus top-level changes."""
    data = demo_pack_data()
    data["templates"] = data["templates"][:1]
    data.update(changes)
    return data


def store(store_id: str = "store-x", **changes: Any) -> StoreSetup:
    """A one-camera store whose zone names match the hand-wash slots (everything binds)."""
    data: dict[str, Any] = {
        "store_id": store_id,
        "name": f"Store {store_id}",
        "frames": {"kitchen-cam": [1920, 1080]},
        "site": {
            "roles": dict(ROLES),
            "cameras": {
                "kitchen-cam": {"calibration": CALIBRATION, "zones": {"line_entry": LINE}},
            },
        },
    }
    data.update(changes)
    return StoreSetup.model_validate(data)
