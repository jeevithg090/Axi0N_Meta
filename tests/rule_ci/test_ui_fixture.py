"""The web UI's Rule CI demo payloads are real `ci.result` payloads (task P3-09c).

`ui/src/fixtures/ci-results.json` feeds the Rule CI panel in `?demo=1`. Each entry must validate
as a `CIResult`, carry the verdict the backend computes, and match `model_dump(mode="json")`
exactly, so the UI's types and fixtures cannot drift from what the bus publishes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rulecam.rule_ci.models import CIResult

FIXTURE = Path(__file__).resolve().parents[2] / "ui" / "src" / "fixtures" / "ci-results.json"
PAYLOADS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("payload", PAYLOADS, ids=[p["rule_id"] for p in PAYLOADS])
def test_the_ui_fixture_is_a_serialised_ci_result(payload: dict[str, Any]) -> None:
    """Validate, recompute the verdict from the report, and round-trip byte for byte."""
    result = CIResult.model_validate(payload)
    assert result == CIResult.of(result.report)
    assert result.model_dump(mode="json") == payload


def test_the_fixture_has_a_green_and_a_red_rule() -> None:
    """The panel demo shows both verdicts, the red one from a fooled lookalike."""
    results = {p["rule_id"]: CIResult.model_validate(p) for p in PAYLOADS}
    assert results["wash-before-line"].ci_green
    harness = results["harness-at-height"]
    assert not harness.ci_green
    red_team = harness.report.red_team
    assert red_team is not None
    assert red_team.lookalikes is not None
    assert [f.lookalike for f in red_team.lookalikes.fooled] == ["backpack strap"]
