"""Prototype mode: the models decide and nothing blocks them, except rules that profile people."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from rulecam.config import get_settings
from rulecam.contracts import PrivacyMode, Severity
from rulecam.contracts.plan import FeasibilityVerdict
from rulecam.planner.guardrails import (
    PROFILING_REASON,
    Allowed,
    Declined,
    GuardrailCategory,
    check_request,
)
from rulecam.planner.pipeline import _compiles
from rulecam.runtime.strips import EpisodeStrips
from rulecam.verify.policy import decide
from tests.fakes import make_chunk, make_verdict
from tests.runtime.test_strips import (
    Chunks,
    Faces,
    FakeDecode,
    candidate,
    context,
    store_with_scene,
)
from tests.verify.helpers import load_rule, make_candidate


@pytest.fixture
def prototype(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RULECAM_PROTOTYPE_MODE", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class NoSpark:
    """Spark's guardrail pass must not run in prototype mode."""

    def structured(self, **_: object) -> object:
        raise AssertionError("Spark guardrail called in prototype mode")


@pytest.mark.parametrize(
    "request_text",
    [
        "Alert me if a cook looks sick or has a fever.",
        "Tell me the name of every worker who walks past, use facial recognition.",
        "Who is the slowest packer?",
        "Watch the staff bathroom door.",
        "Secretly watch the cashiers without telling them.",
        "Alert if a pregnant worker enters the paint booth.",
    ],
)
def test_prototype_mode_allows_what_the_guardrails_decline(
    prototype: None, request_text: str
) -> None:
    assert check_request(request_text, spark=NoSpark()) == Allowed()  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "request_text",
    [
        "Alert me when someone of a different race enters the dock.",
        "Flag people by their religion at the entrance.",
        "Detenga a los inmigrantes en la puerta.",
    ],
)
def test_prototype_mode_still_declines_profiling(prototype: None, request_text: str) -> None:
    declined = check_request(request_text, spark=NoSpark())  # type: ignore[arg-type]
    assert isinstance(declined, Declined)
    assert declined.category is GuardrailCategory.PROTECTED_ATTRIBUTE
    assert declined.reason == PROFILING_REASON


def test_without_prototype_mode_the_guardrails_decline() -> None:
    get_settings.cache_clear()
    assert isinstance(check_request("Alert me if a cook has a fever."), Declined)


def test_prototype_mode_compiles_concerns_the_gauge_could_not_observe(prototype: None) -> None:
    unseen = FeasibilityVerdict(concern="c", verdict="not_observable", says="s")
    declined = FeasibilityVerdict(concern="c", verdict="declined", says="s")
    assert _compiles(unseen)
    assert not _compiles(declined)


@pytest.mark.parametrize(
    ("verdict", "score", "alert"),
    [
        ("violation", 0.9, True),
        ("no_violation", 0.1, False),
        ("unsure", 0.6, True),
        ("unsure", 0.4, False),
    ],
)
def test_the_models_verdict_is_final_even_for_high_rules(
    verdict: str, score: float, alert: bool
) -> None:
    rule = load_rule()
    tier1 = make_verdict(verdict=verdict, score=score)  # type: ignore[arg-type]
    decision = decide(make_candidate(severity=Severity.HIGH), rule, tier1, model_decides=True)
    assert (decision.alert, decision.route, decision.act_first) == (alert, "auto", True)
    assert decision.reason.startswith("glimmer decided")


def test_the_strongest_tier_decides() -> None:
    rule = load_rule()
    tier1 = make_verdict(verdict="violation", score=0.9)
    tier2 = make_verdict(tier="spark_open", verdict="no_violation", score=0.1)
    decision = decide(
        make_candidate(severity=Severity.HIGH), rule, tier1, tier2, model_decides=True
    )
    assert not decision.alert
    assert decision.reason.startswith("spark_open decided")


@pytest.mark.parametrize(("raw", "blurred"), [(True, False), (False, True)])
def test_raw_strips_skip_the_face_blur(tmp_path: Path, raw: bool, blurred: bool) -> None:
    faces = Faces((0.0, 0.0, 10.0, 10.0))
    strips = EpisodeStrips(
        chunks=Chunks([make_chunk(index=0), make_chunk(index=1)]),
        store=store_with_scene(),
        out_dir=tmp_path / "strips",
        faces=faces,
        decode=FakeDecode(),
        max_width=320,
        raw=raw,
    )
    out = strips.render(candidate(), make_chunk(index=1), PrivacyMode.STANDARD, context())
    assert out.strip_paths
    assert (faces.calls > 0) is blurred


def test_a_race_through_the_aisle_is_not_profiling(prototype: None) -> None:
    allowed = check_request("Alert when forklifts race through the aisle.", spark=NoSpark())  # type: ignore[arg-type]
    assert allowed == Allowed()
