"""Build the UI's sample extension record with the real proof and fake model replies.

The sandbox, boundary cases and mutants are the real code; Muse Code and the reviewer are the test
fakes (`tests.fakes.extensions`). `test_ui_fixture.py` checks the committed UI fixture equals this.
"""

from __future__ import annotations

import json
from pathlib import Path

from rulecam.clients.meta_hosted.muse_code import MuseCodeClient
from rulecam.extensions.generate import CheckGenerator
from rulecam.extensions.registry import ExtensionRegistry
from rulecam.extensions.review import ExtensionReviewer
from rulecam.extensions.service import ExtensionRequest, ExtensionService
from rulecam.replay.cache import ReplayCache
from tests.fakes import FakeClock
from tests.fakes.extensions import FakeMuseCode, FakeSparkOpen

UI_FIXTURE = Path(__file__).resolve().parents[2] / "ui" / "src" / "fixtures" / "extension-sample.json"
REQUEST = ExtensionRequest(
    request="Alert if a pallet is stacked above the yellow line on the rack",
    concept="pallet",
    camera_id="CAM-04",
    lines={"yellow": ((0.0, 200.0), (640.0, 200.0))},
    frame_w=640,
    frame_h=480,
)


def sample_record_json(workdir: Path) -> str:
    """The sample record as the UI fixture's JSON text."""
    clock = FakeClock()
    muse = MuseCodeClient(
        cache=ReplayCache(workdir / "replay", "live", clock=clock),
        runner=FakeMuseCode(),
        which=lambda name: f"/usr/local/bin/{name}",
    )
    reviewer = FakeSparkOpen(
        answer={
            "verdict": "approve",
            "reasons": [
                "measures the pallet's top against the line at the pallet's own x",
                "returns no alert when the yellow line or the pallets are missing",
            ],
            "risks": ["the line must be redrawn if the camera moves"],
        }
    )
    service = ExtensionService(
        CheckGenerator(muse),
        ExtensionReviewer(reviewer),
        ExtensionRegistry(workdir / "extensions", clock=clock),
        clock=clock,
    )
    record = service.propose(REQUEST)
    return json.dumps(json.loads(record.model_dump_json()), indent=2, sort_keys=True) + "\n"
