"""The Tier 1 verdict record says where its evidence strip is, relative to `<data>/strips/`."""

from __future__ import annotations

from rulecam.runtime.loop import strip_reference


def test_strip_reference_is_relative_to_the_strips_folder() -> None:
    paths = [
        "/workspace/data/strips/phone-5/ev-1/1.jpg",
        "/workspace/data/strips/phone-5/ev-1/2.jpg",
    ]
    assert strip_reference(paths) == {
        "strip": {"dir": "phone-5/ev-1", "frames": ["1.jpg", "2.jpg"]}
    }
    assert strip_reference([]) == {}
