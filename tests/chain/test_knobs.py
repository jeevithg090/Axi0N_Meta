"""Knobs: reading and setting a template's thresholds inside its predicate."""

from __future__ import annotations

import pytest

from rulecam.chain.knobs import KnobError, knob_defaults, knob_value, with_knobs
from rulecam.chain.models import Knob
from rulecam.contracts.rule_ir import RuleIR
from tests.chain.helpers import handwash_only, pack_from

WASH = Knob(name="wash", path=("requirement", "total_s_gte"), low=20, high=40, stricter="higher")


def _rule() -> RuleIR:
    return pack_from(handwash_only()).templates[0].rule


def test_knob_value_reads_the_number_at_the_path() -> None:
    assert knob_value(_rule(), WASH) == 20.0


def test_with_knobs_sets_only_the_named_knobs() -> None:
    window = Knob(name="window", path=("require_within_s",), low=60, high=300, stricter="lower")
    rule = with_knobs(_rule(), [WASH, window], {"wash": 32.0})
    assert knob_defaults(rule, [WASH, window]) == {"wash": 32.0, "window": 300.0}
    assert knob_value(_rule(), WASH) == 20.0  # the original is untouched


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (("requirement", "missing"), "does not exist"),
        (("requirement", 0), "does not exist"),
        (("requirement", "object"), "not a number"),
    ],
)
def test_bad_paths_raise(path: tuple[str | int, ...], message: str) -> None:
    knob = Knob(name="bad", path=path, low=0, high=1, stricter="lower")
    with pytest.raises(KnobError, match=message):
        knob_value(_rule(), knob)


def test_a_value_the_rule_rejects_raises() -> None:
    window = Knob(name="window", path=("require_within_s",), low=-5, high=300, stricter="lower")
    data = handwash_only()
    data["templates"][0]["rule"]["predicate"] = {
        "op": "persist",
        "seconds": 30,
        "of": {"op": "inside", "object": "person", "zone": "line_entry"},
    }
    seconds = Knob(name="seconds", path=("seconds",), low=-5, high=30, stricter="lower")
    rule = pack_from(data).templates[0].rule
    with pytest.raises(KnobError, match="do not fit"):
        with_knobs(rule, [seconds], {"seconds": -1.0})
    with pytest.raises(KnobError, match="does not exist"):
        with_knobs(rule, [window], {"window": 10.0})
