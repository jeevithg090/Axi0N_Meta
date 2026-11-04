"""Tests for the appearance filter, on sentences the models really wrote on Nov 4."""

from __future__ import annotations

import pytest

from rulecam.wearables.privacy import scrub, scrub_model
from rulecam.wearables.schemas import Hazard


@pytest.mark.parametrize(
    ("said", "kept"),
    [
        (
            "The person ahead is in a brown and beige jacket and red cap with no "
            "high-visibility clothing visible.",
            "The person ahead has no high-visibility clothing visible.",
        ),
        (
            "Operator ahead is wearing a white shirt and red headwear with no "
            "high-visibility vest visible.",
            "Operator ahead has no high-visibility vest visible.",
        ),
        (
            "the cashier in yellow ahead center scanning an item, the customer in teal at the "
            "left register, the yellow-shirted employee at the right register",
            "the cashier ahead center scanning an item, the customer at the left register, the "
            "employee at the right register",
        ),
        (
            "The operator standing at it, wearing a work jacket and cap without "
            "high-visibility clothing.",
            "The operator standing at it without high-visibility clothing.",
        ),
        (
            "A man with grey hair is waiting. Two people are queueing.",
            "Two people are queueing.",
        ),
    ],
)
def test_appearance_is_cut(said: str, kept: str) -> None:
    text, edits = scrub(said)
    assert text == kept
    assert edits >= 1


@pytest.mark.parametrize(
    "safe",
    [
        "No, stop. A forklift is right ahead blocking the aisle.",
        "The driver is wearing a hard hat but no high-visibility vest.",
        "Operator without high-visibility clothing at the forklift, ahead.",
        "Bread on the left and displays on the right look fully stocked.",
        "Tall racking on both sides with pallets in red wrap.",
        "",
    ],
)
def test_safety_and_scene_text_is_kept(safe: str) -> None:
    assert scrub(safe) == (safe, 0)


def test_models_are_scrubbed_all_the_way_down() -> None:
    hazard = Hazard(what="person in a red cap in the lane", where="ahead", severity="caution")
    cleaned, edits = scrub_model(hazard)
    assert cleaned.what == "person in the lane"
    assert cleaned.where == "ahead"
    assert edits == 1
