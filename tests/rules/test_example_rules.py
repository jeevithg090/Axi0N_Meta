"""Every MP and V2 example rule evaluates on synthetic fixtures (P1-06 acceptance, G2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

from rulecam.contracts import CandidateEvent, RuleIR, Track, TrackPoint, Zone
from rulecam.rules import PoseStateName, evaluate
from tests.rules.helpers import CAL, at, box_track, walk_track

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def load(name: str) -> RuleIR:
    data = yaml.safe_load((FIXTURES / f"{name}.rule.yaml").read_text(encoding="utf-8"))
    return RuleIR.model_validate(data)


# --- handwash: sequence(past) of line entry after a dwell at the sink -------------------------

HANDWASH = load("wash-before-line")
WAIT, SINK, LINE = (8.5, 1.5), (6.5, 1.5), (2.0, 1.5)  # line_entry is x 0-4 m, y 0-3 m
SINK_POLYGON = Zone(
    zone_id="sink_zone",
    camera_id="cam1",
    kind="polygon",
    polygon_px=((600.0, 0.0), (700.0, 0.0), (700.0, 100.0), (600.0, 100.0)),
)
Phase = tuple[tuple[float, float], float]


def worker(phases: list[Phase], track_id: str = "1") -> list[Track]:
    """A person and their hands (0.9 m above the foot point, inside the person box)."""
    feet = [place for place, seconds in phases for _ in range(round(seconds * 10))]
    hands = [(x, y - 0.9) for x, y in feet]
    return [
        walk_track(f"cam1:person:{track_id}", feet, size_px=(100.0, 200.0)),
        walk_track(f"cam1:hands:{track_id}", hands, concept="hands", size_px=(20.0, 20.0)),
    ]


def sink() -> Track:
    # Foot at y = 0.2 m and 40 px wide: the hands at the sink are 0.4 m from its footprint.
    return walk_track("cam1:sink:1", [(6.5, 0.2)] * 4000, concept="sink")


def wash_check(
    phases: list[Phase], *, buffer_zone: bool = False, seconds: float = 400.0
) -> list[CandidateEvent]:
    tracks = worker([(WAIT, 2.0), *phases])
    zones = {} if buffer_zone else {"sink_zone": SINK_POLYGON}
    if buffer_zone:
        tracks.append(sink())
    return evaluate(HANDWASH, tracks, calibration=CAL, zones=zones, t0=at(0), t1=at(seconds))


@pytest.mark.parametrize("buffer_zone", [False, True], ids=["polygon-zone", "buffer-of-sink"])
@pytest.mark.parametrize(
    ("name", "phases", "violation"),
    [
        ("G2: 22 s wash passes", [(SINK, 22), (WAIT, 10), (LINE, 5)], False),
        ("G2: 8 s wash fails", [(SINK, 8), (WAIT, 10), (LINE, 5)], True),
        ("dwell 19.5 s -> violation", [(SINK, 19.5), (WAIT, 5), (LINE, 5)], True),
        ("dwell 20.5 s -> pass", [(SINK, 20.5), (WAIT, 5), (LINE, 5)], False),
        ("12 + 2 s gap + 12 -> pass", [(SINK, 12), (WAIT, 2), (SINK, 12), (LINE, 5)], False),
        ("12 + 4 s gap + 12 -> violation", [(SINK, 12), (WAIT, 4), (SINK, 12), (LINE, 5)], True),
        ("wash then enter within 300 s -> no event", [(SINK, 22), (WAIT, 250), (LINE, 5)], False),
        ("wash more than 300 s before -> event", [(SINK, 22), (WAIT, 310), (LINE, 5)], True),
        ("enter with no wash -> event", [(WAIT, 5), (LINE, 5)], True),
    ],
)
def test_handwash_rule(name: str, phases: list[Phase], violation: bool, buffer_zone: bool) -> None:
    events = wash_check(phases, buffer_zone=buffer_zone)
    assert (len(events) == 1) is violation, name


def test_wash_after_entering_still_violates() -> None:
    events = wash_check([(LINE, 5), (SINK, 22), (WAIT, 5), (LINE, 5)])
    assert len(events) == 1
    assert events[0].ts_start == at(2.0)  # the first entry, before any wash
    assert events[0].ts_start == events[0].ts_end


def test_handwash_violation_reports_the_longest_wash() -> None:
    (event,) = wash_check([(SINK, 8), (WAIT, 10), (LINE, 5)])
    assert event.measurement is not None
    assert (event.measurement.value, event.measurement.unit) == (8.0, "s")
    assert event.predicate_trace["sequence"] is True
    assert event.predicate_trace["sequence.trigger.inside"] is True
    assert event.predicate_trace["sequence.value"] == 8.0
    assert event.predicate_trace["subject"] == "cam1:person:1"
    assert event.severity == "medium"


def test_handwash_is_per_person() -> None:
    washer = worker([(WAIT, 2), (SINK, 22), (LINE, 5)], "1")
    skipper = worker([(WAIT, 24), (LINE, 5)], "2")
    events = evaluate(
        HANDWASH,
        washer + skipper,
        calibration=CAL,
        zones={"sink_zone": SINK_POLYGON},
        t0=at(0),
        t1=at(60),
    )
    assert [e.predicate_trace["subject"] for e in events] == ["cam1:person:2"]


PLACES = st.sampled_from([WAIT, SINK, LINE])


@settings(max_examples=30, deadline=None)
@given(
    data=st.data(),
    plans=st.lists(
        st.lists(st.tuples(PLACES, st.integers(1, 25)), min_size=1, max_size=5),
        min_size=1,
        max_size=3,
    ),
)
def test_handwash_output_ignores_track_order(
    data: st.DataObject, plans: list[list[tuple[tuple[float, float], int]]]
) -> None:
    tracks = [t for i, plan in enumerate(plans) for t in worker([(WAIT, 1.0), *plan], str(i))]
    shuffled = data.draw(st.permutations(tracks))

    def run(items: list[Track]) -> str:
        events = evaluate(
            HANDWASH,
            items,
            calibration=CAL,
            zones={"sink_zone": SINK_POLYGON},
            t0=at(0),
            t1=at(130),
        )
        return "\n".join(e.model_dump_json() for e in events)

    assert run(shuffled) == run(tracks)


# --- excavator swing radius: persist(all(distance, moving)) -----------------------------------

EXCAVATOR = load("excavator-swing-radius")


def excavator(person_m: tuple[float, float], bucket_speed: float, seconds: float) -> list[Track]:
    steps = round(seconds * 10)
    bucket = [(bucket_speed * i / 10, 0.0) for i in range(steps)]
    return [
        walk_track("cam1:bucket:1", bucket, concept="excavator bucket", with_foot_m=False),
        walk_track("cam1:person:1", [person_m] * steps, with_foot_m=False),
    ]


@pytest.mark.parametrize(
    ("person_m", "speed", "fires"),
    [((0.0, 1.5), 1.0, True), ((0.0, 1.5), 0.0, False), ((0.0, 3.0), 1.0, False)],
)
def test_excavator_rule(person_m: tuple[float, float], speed: float, fires: bool) -> None:
    events = evaluate(
        EXCAVATOR,
        excavator(person_m, speed, 3.0),
        calibration=CAL,
        zones={},
        t0=at(0),
        t1=at(3),
    )
    assert bool(events) is fires
    if fires:
        assert events[0].measurement is not None
        assert events[0].measurement.unit == "m"  # the distance, not the persisted seconds
        assert events[0].predicate_trace["persist.value"] == 1.0
        assert events[0].severity == "high"


# --- fire exit blocked: persist(inside(buffer of door), 60 s) ---------------------------------

FIRE_EXIT = load("fire-exit-blocked")


@pytest.mark.parametrize(("blocked_s", "fires"), [(59.0, False), (61.0, True)])
def test_fire_exit_rule(blocked_s: float, fires: bool) -> None:
    door = walk_track("cam1:door:1", [(3.0, 0.0)] * 900, concept="fire exit door")
    box = walk_track(
        "cam1:box:1",
        [(3.0, 1.0)] * round(blocked_s * 10) + [(9.0, 9.0)] * 50,
        concept="box or cart or pallet",
    )
    events = evaluate(FIRE_EXIT, [door, box], calibration=CAL, zones={}, t0=at(0), t1=at(90))
    assert bool(events) is fires
    if fires:
        assert events[0].measurement is not None
        assert (events[0].measurement.value, events[0].measurement.unit) == (60.0, "s")


# --- person down: persist(pose down, 10 s) with injected pose features ------------------------

PERSON_DOWN = load("person-down")
STANDING, LYING = (0.0, 0.0, 40.0, 160.0), (0.0, 120.0, 160.0, 160.0)


def box_pose(state: PoseStateName, point: TrackPoint) -> bool | None:
    """Fake pose features: a person is down when their box is wider than tall."""
    x1, y1, x2, y2 = point.box_xyxy
    return state == "down" and (x2 - x1) > (y2 - y1)


def fallen(seconds_down: float) -> Track:
    boxes = [STANDING] * 20 + [LYING] * round(seconds_down * 10) + [STANDING] * 20
    return box_track("cam1:person:1", boxes, concept="person")


@pytest.mark.parametrize(("down_s", "fires"), [(9.0, False), (11.0, True)])
def test_person_down_rule(down_s: float, fires: bool) -> None:
    events = evaluate(
        PERSON_DOWN,
        [fallen(down_s)],
        calibration=None,
        zones={},
        t0=at(0),
        t1=at(30),
        pose=box_pose,
    )
    assert bool(events) is fires
    if fires:
        assert events[0].ts_start == at(2.0 + 9.9)
        assert "pose_unavailable" not in events[0].predicate_trace


POSE_OR_PRESENT = RuleIR.model_validate(
    {
        **PERSON_DOWN.model_dump(),
        "predicate": {
            "op": "any",
            "of": [{"op": "pose", "state": "down"}, {"op": "count", "object": "person"}],
        },
    }
)


def test_without_pose_features_pose_is_false_and_flagged() -> None:
    assert (
        evaluate(PERSON_DOWN, [fallen(20.0)], calibration=None, zones={}, t0=at(0), t1=at(30)) == []
    )
    (event,) = evaluate(
        POSE_OR_PRESENT, [fallen(5.0)], calibration=None, zones={}, t0=at(0), t1=at(10)
    )
    assert event.predicate_trace["pose_unavailable"] is True
    assert event.predicate_trace["any.0.pose"] is False


def test_unreliable_keypoints_are_unknown_not_true() -> None:
    def unsure(state: PoseStateName, point: TrackPoint) -> bool | None:
        return None

    (event,) = evaluate(
        POSE_OR_PRESENT, [fallen(5.0)], calibration=None, zones={}, t0=at(0), t1=at(10), pose=unsure
    )
    assert event.predicate_trace["any.0.pose"] is False
    assert event.predicate_trace["any.0.pose.unknown"] is True
    assert "pose_unavailable" not in event.predicate_trace


# --- forklift proximity (UC4) -----------------------------------------------------------------

FORKLIFT = load("forklift-proximity")


@pytest.mark.parametrize(
    ("person_m", "speed", "fires"),
    [((2.0, 1.0), 1.0, True), ((2.0, 4.0), 1.0, False), ((0.0, 1.0), 0.0, False)],
)
def test_forklift_proximity_rule(person_m: tuple[float, float], speed: float, fires: bool) -> None:
    forklift = walk_track(
        "cam1:forklift:1", [(speed * i / 10, 0.0) for i in range(40)], concept="forklift"
    )
    person = walk_track("cam1:person:1", [person_m] * 40)
    events = evaluate(FORKLIFT, [forklift, person], calibration=CAL, zones={}, t0=at(0), t1=at(4))
    assert bool(events) is fires
