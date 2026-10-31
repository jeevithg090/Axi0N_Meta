"""Tests for Rule Cards and Declined Cards (task P2-10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rulecam.cards.models import DeclinedCardData, RuleCardData
from rulecam.cards.render import (
    render_card_png,
    render_declined_card_html,
    render_rule_card_html,
)


@pytest.fixture
def sample_rule_card() -> RuleCardData:
    return RuleCardData(
        rule_id="ped-forklift-2m",
        plain_language="Maintain 2m clearance between pedestrians and forklifts in dock staging",
        language="en",
        cited_standard="OSHA 1910.178(m)(2) · Powered industrial trucks",
        feasibility_verdict="green",
        boundary_traces_passed=3,
        boundary_traces_total=3,
        mutants_killed=5,
        mutants_total=5,
        lookalikes_tested=1,
        lookalikes_fooled=0,
        calibrated_false_alarm_rate=0.002,
        verified_by="Glimmer + Spark Open",
        site_id="warehouse-bay-3",
        camera_id="cam3",
    )


@pytest.fixture
def sample_declined_card() -> DeclinedCardData:
    return DeclinedCardData(
        category="Per-Person Productivity Tracking",
        reason=(
            "We decline individual worker speed ranking. "
            "Site Advisor monitors stations and flow bottlenecks."
        ),
        station_alternative="Measure dock turnaround time or staging dwell instead.",
        site_id="warehouse-bay-3",
    )


def test_rule_card_html_snapshot(sample_rule_card: RuleCardData) -> None:
    """Rule card renders complete HTML with all required metadata."""
    html = render_rule_card_html(sample_rule_card)

    assert "1080px" in html
    assert "1350px" in html
    assert "ped-forklift-2m" in html
    assert "Maintain 2m clearance" in html
    assert "OSHA 1910.178(m)(2)" in html
    assert "Feasibility: GREEN" in html
    assert "3/3" in html
    assert "5/5" in html
    assert "0/1" in html
    assert "Glimmer + Spark Open" in html
    assert "FAR: 0.002/shift" in html


def test_declined_card_html_snapshot(sample_declined_card: DeclinedCardData) -> None:
    """Declined card renders category, reason, alternative, and privacy guarantee."""
    html = render_declined_card_html(sample_declined_card)

    assert "Request Declined" in html
    assert "Per-Person Productivity Tracking" in html
    assert "Site Advisor monitors stations and flow bottlenecks" in html
    assert "Measure dock turnaround time" in html
    assert "architecturally prohibited" in html


def test_declined_card_guardrail_never_contains_raw_request_or_names() -> None:
    """Declined cards must strictly exclude raw request text and worker names."""
    forbidden_request = "Who is slowest today: check Maria and John?"
    forbidden_names = ("Maria", "John", "Carlos")

    # Compliant card does not leak any forbidden token
    card = DeclinedCardData(
        category="Per-Person Productivity Tracking",
        reason="We decline per-person tracking. Operations analysis evaluates stations only.",
        station_alternative="Analyze station throughput or turnaround intervals.",
    )
    html = render_declined_card_html(
        card,
        forbidden_tokens=(forbidden_request, *forbidden_names),
    )

    assert "Maria" not in html
    assert "John" not in html
    assert "slowest today" not in html

    # If forbidden token is present in the card data, render_declined_card_html raises ValueError
    leaky_card = DeclinedCardData(
        category="Per-Person Tracking",
        reason="We cannot track John's speed.",
        station_alternative="Track station instead.",
    )
    with pytest.raises(ValueError, match="Guardrail breach"):
        render_declined_card_html(leaky_card, forbidden_tokens=("John",))


@pytest.mark.ui
@pytest.mark.asyncio
async def test_png_rendering_graceful_fallback(
    sample_rule_card: RuleCardData,
    tmp_path: Path,
) -> None:
    """PNG renderer either creates image or returns None if Playwright is absent."""
    html = render_rule_card_html(sample_rule_card)
    output_png = tmp_path / "card.png"

    result = await render_card_png(html, output_png)
    # If Playwright is not in environment, returns None gracefully
    # If Playwright is installed, returns path
    assert result is None or result.exists()
