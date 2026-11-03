"""The engine runs `StateCheck(state="ext:…")` only through a hook; otherwise it is unknown."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from rulecam.contracts import RuleIR, Track
from rulecam.contracts.rule_ir import StateCheck
from rulecam.extensions.sample import px_per_m, samples_from_scene
from rulecam.extensions.schema import CheckSample
from rulecam.rules.backtest import required_concepts
from rulecam.rules.engine import evaluate
from rulecam.rules.extension import (
    ExtensionValues,
    extension_ref,
    is_extension_state,
    parse_extension_ref,
)
from rulecam.rules.scene import Scene
from tests.fakes import make_track
from tests.rules.helpers import CAL, at, make_rule

REF = "ext:pallet_above_line@0123456789ab"
LINE = ((0.0, 300.0), (640.0, 300.0))


def _rule() -> RuleIR:
    return make_rule(
        {"op": "state", "object": "pallet", "state": REF}, concepts={"pallet": "wooden pallet"}
    )


@dataclass
class RecordingHook:
    """Says True everywhere and keeps the samples it built."""

    answer: bool = True
    seen: list[list[CheckSample]] = field(default_factory=list)

    def evaluate(self, node: StateCheck, scene: Scene) -> ExtensionValues | None:
        samples = samples_from_scene(scene, node.object, {"yellow": LINE}, (640, 480))
        self.seen.append(samples)
        n = scene.timeline.n
        return ExtensionValues(truth=np.full(n, self.answer), known=np.ones(n, dtype=np.bool_))


def _pallet() -> Track:
    return make_track("cam1:pallet:1", concept="wooden pallet", feet_m=[(2.0, 3.0)] * 11)


def test_refs_round_trip() -> None:
    assert is_extension_state(REF)
    assert parse_extension_ref(REF) == ("pallet_above_line", "0123456789ab")
    assert extension_ref("pallet_above_line", "0123456789ab" + "f" * 52) == REF
    assert parse_extension_ref("ext:Bad Name@xyz") is None


def test_without_a_hook_the_state_is_unknown_and_never_fires() -> None:
    events = evaluate(_rule(), [_pallet()], calibration=CAL, zones={}, t0=at(0), t1=at(1))
    assert events == []


def test_with_a_hook_the_engine_fires_and_traces() -> None:
    hook = RecordingHook()
    [event] = evaluate(
        _rule(), [_pallet()], calibration=CAL, zones={}, t0=at(0), t1=at(1), extensions=hook
    )
    assert event.predicate_trace["state"] is True
    [samples] = hook.seen
    assert len(samples) == 11
    first = samples[0]
    assert first.frame_w == 640
    assert first.lines == {"yellow": LINE}
    [pallet] = first.objects
    assert pallet.concept == "wooden pallet"
    assert pallet.box_xyxy_px == (180.0, 220.0, 220.0, 300.0)
    assert pallet.mask_top_y_px == 220.0
    assert pallet.mask_area_px == 40.0 * 80.0
    assert pallet.ground_xy_m == (2.0, 3.0)
    assert pallet.score == 0.9
    assert first.px_per_m == 100.0
    assert "track" not in str(first.as_json())


def test_a_false_hook_does_not_fire() -> None:
    events = evaluate(
        _rule(),
        [_pallet()],
        calibration=CAL,
        zones={},
        t0=at(0),
        t1=at(1),
        extensions=RecordingHook(answer=False),
    )
    assert events == []


def test_backtest_needs_the_object_itself() -> None:
    assert required_concepts(_rule(), {}) == {"pallet": frozenset({"pallet", "wooden pallet"})}


def test_scale_needs_a_calibration_and_a_point() -> None:
    assert px_per_m(None, (1.0, 1.0)) is None
    assert px_per_m(CAL, None) is None
