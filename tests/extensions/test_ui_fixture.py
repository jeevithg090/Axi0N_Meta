"""The UI's sample extension record is exactly what the real proof produces today."""

from __future__ import annotations

import os
from pathlib import Path

from tests.extensions.sample_record import UI_FIXTURE, sample_record_json


def test_ui_sample_matches_the_proof(tmp_path: Path) -> None:
    built = sample_record_json(tmp_path)
    if os.environ.get("RULECAM_WRITE_FIXTURES") == "1":
        UI_FIXTURE.write_text(built, encoding="utf-8")
    assert UI_FIXTURE.read_text(encoding="utf-8") == built, (
        "regenerate with RULECAM_WRITE_FIXTURES=1 uv run pytest tests/extensions/test_ui_fixture.py"
    )
